"""Local SQLite state and atomic admission for the Mio plugin."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

ACTIVE_STATES = frozenset(
    {"dispatching", "pending", "ready", "preparing", "running", "queued_remote"}
)
PENDING_STATES = ACTIVE_STATES | {"queued"}
_TABLES = """
CREATE TABLE IF NOT EXISTS tasks (
    num INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    instance_id TEXT NOT NULL,
    actor_key TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    umo TEXT NOT NULL,
    series_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,
    notified INTEGER NOT NULL,
    created_at REAL NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_queue ON tasks(status, num);
CREATE INDEX IF NOT EXISTS tasks_owner ON tasks(actor_key, status);
CREATE INDEX IF NOT EXISTS tasks_session ON tasks(umo, num);
CREATE INDEX IF NOT EXISTS tasks_remote_job ON tasks(instance_id, job_id);
CREATE INDEX IF NOT EXISTS tasks_remote_episode ON tasks(instance_id, episode_id);
CREATE INDEX IF NOT EXISTS tasks_remote_series ON tasks(instance_id, series_id);
CREATE TABLE IF NOT EXISTS sessions (
    umo TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    sender TEXT NOT NULL,
    last_used REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS session_overrides (umo TEXT PRIMARY KEY, config TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quota (
    id INTEGER PRIMARY KEY,
    actor_key TEXT NOT NULL,
    day TEXT NOT NULL,
    created_at REAL NOT NULL,
    cloud INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS quota_actor_day ON quota(actor_key, day);
CREATE INDEX IF NOT EXISTS quota_time ON quota(created_at);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS instances (id TEXT PRIMARY KEY, config TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS primary_credentials (address TEXT PRIMARY KEY, token TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS webhooks (instance_id TEXT PRIMARY KEY, config TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    instance_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    received_at REAL NOT NULL,
    PRIMARY KEY(instance_id, delivery_id)
);
CREATE INDEX IF NOT EXISTS webhook_delivery_time ON webhook_deliveries(received_at);
"""
_TASK_FIELDS = (
    "id",
    "instance_id",
    "actor_key",
    "sender_id",
    "umo",
    "series_id",
    "episode_id",
    "job_id",
    "status",
    "notified",
    "created_at",
)
_TASK_UPDATE = ", ".join(f"{col}=?" for col in (*_TASK_FIELDS, "payload"))
_PRIVATE_KEYS = frozenset(
    {
        "token",
        "apitoken",
        "apikey",
        "keysecret",
        "authorization",
        "authorizationheader",
        "bearer",
        "password",
        "secret",
        "webhooksecret",
        "signingsecret",
        "clientsecret",
    }
)


class MigrationError(RuntimeError):
    """The JSON archive could not be read safely; the source was untouched."""


class AdmissionDenied(RuntimeError):
    def __init__(self, code: str, message: str, *, retry_after: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


def _snapshot(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _snapshot(item)
            for key, item in value.items()
            if not isinstance(key, str)
            or key.lower().replace("_", "").replace("-", "") not in _PRIVATE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_snapshot(row) for row in value]
    return value


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _restored(record: sqlite3.Row | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {**json.loads(record["payload"]), "num": record["num"]}


def _placeholders(items: Iterable[str]) -> str:
    return ", ".join("?" for _ in items)


def _truthy_text(value: Any) -> str:
    return str(value or "")


def _now(now: float | None) -> float:
    return time.time() if now is None else now


class TaskStore:
    def __init__(self, path: Path) -> None:
        path = Path(path)
        legacy = path if path.suffix.lower() == ".json" else path.with_name("tasks.json")
        self.path = path.with_suffix(".sqlite3") if path == legacy else path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_TABLES)
        self._migrate(legacy)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _migrate(self, path: Path) -> None:
        with self._write() as db:
            if db.execute("SELECT 1 FROM meta WHERE key='migrated_json'").fetchone():
                return
            try:
                raw = json.loads(path.read_text("utf-8-sig"))
            except FileNotFoundError:
                return
            except (OSError, UnicodeError, ValueError) as exc:
                raise MigrationError("无法读取旧版 tasks.json。原文件已保留。") from exc
            if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), list):
                raise MigrationError("旧版 tasks.json 的格式不正确。原文件已保留。")
            try:
                sequence = max(int(raw.get("seq") or 0), 0)
                for task in raw["tasks"]:
                    if not isinstance(task, dict) or not _truthy_text(task.get("id")):
                        raise ValueError("Invalid archived task")
                    num = int(task["num"])
                    if num <= 0:
                        raise ValueError("Invalid archived number")
                    self._insert(
                        db,
                        {
                            **task,
                            "status": "archived",
                            "legacy_status": task.get("status"),
                            "notified": True,
                            "series_id": "",
                            "episode_id": "",
                            "job_id": "",
                        },
                        num=num,
                    )
                    sequence = max(sequence, num)
                db.execute(
                    "UPDATE sqlite_sequence SET seq=MAX(seq, ?) WHERE name='tasks'", (sequence,)
                )
                if sequence:
                    db.execute(
                        """INSERT INTO sqlite_sequence(name, seq) SELECT 'tasks', ?
                           WHERE NOT EXISTS(SELECT 1 FROM sqlite_sequence WHERE name='tasks')""",
                        (sequence,),
                    )
            except (KeyError, TypeError, ValueError, OverflowError, sqlite3.IntegrityError) as exc:
                raise MigrationError("旧版任务记录无法完整导入。原文件已保留。") from exc
            db.execute("INSERT INTO meta(key, value) VALUES('migrated_json', '1')")

    @staticmethod
    def _task_values(record: dict[str, Any]) -> tuple[Any, ...]:
        return (
            *(_truthy_text(record.get(key)) for key in _TASK_FIELDS[:-2]),
            int(bool(record.get("notified"))),
            float(record["created_at"]),
            _json(record),
        )

    def _insert(
        self, db: sqlite3.Connection, record: dict[str, Any], *, num: int | None = None
    ) -> dict[str, Any]:
        record = _snapshot(record)
        record.pop("num", None)
        record.setdefault("instance_id", "primary")
        record.setdefault("status", "standby")
        record.setdefault("notified", False)
        if record.get("created_at") is None:
            record["created_at"] = time.time()
        if not isinstance(record.get("id"), str) or not record["id"]:
            raise ValueError("任务必须包含本地 ID。")
        columns = ", ".join(("num", *_TASK_FIELDS, "payload"))
        values = (num, *self._task_values(record))
        cursor = db.execute(
            f"INSERT INTO tasks({columns}) VALUES ({', '.join('?' for _ in values)})",
            values,
        )
        self._seen_session(
            db,
            record.get("umo", ""),
            record.get("platform", ""),
            record.get("sender_name") or record.get("sender_id", ""),
            float(record["created_at"]),
        )
        return {**record, "num": cursor.lastrowid}

    def add(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._write() as db:
            return self._insert(db, record)

    def _fetch_tasks(self, sql: str, values: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            return [
                {**json.loads(row["payload"]), "num": row["num"]} for row in db.execute(sql, values)
            ]

    def all(self) -> list[dict[str, Any]]:
        return self._fetch_tasks("SELECT num, payload FROM tasks ORDER BY num")

    def list_tasks(
        self,
        *,
        umo: str | None = None,
        sender_id: str | None = None,
        instance_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        filters = {
            k: v
            for k, v in {"umo": umo, "sender_id": sender_id, "instance_id": instance_id}.items()
            if v is not None
        }
        where = " WHERE " + " AND ".join(f"{k}=?" for k in filters) if filters else ""
        return self._fetch_tasks(
            f"SELECT num, payload FROM tasks{where} ORDER BY num DESC LIMIT ?",
            (*filters.values(), max(0, limit)),
        )

    def get(self, task_id: str) -> dict[str, Any] | None:
        rows = self._fetch_tasks("SELECT num, payload FROM tasks WHERE id=?", (task_id,))
        return rows[0] if rows else None

    def by_num(self, num: int) -> dict[str, Any] | None:
        rows = self._fetch_tasks("SELECT num, payload FROM tasks WHERE num=?", (num,))
        return rows[0] if rows else None

    def latest_for(
        self, umo: str | None = None, *, sender_id: str | None = None
    ) -> dict[str, Any] | None:
        rows = self.list_tasks(umo=umo, sender_id=sender_id, limit=1)
        return rows[0] if rows else None

    def find_remote(
        self,
        instance_id: str,
        *,
        job_id: str | None = None,
        episode_id: str | None = None,
        series_id: str | None = None,
    ) -> dict[str, Any] | None:
        filters = {
            k: v
            for k, v in {"job_id": job_id, "episode_id": episode_id, "series_id": series_id}.items()
            if v
        }
        if not filters:
            return None
        where = " AND ".join(f"{k}=?" for k in filters)
        rows = self._fetch_tasks(
            f"""SELECT num, payload FROM tasks WHERE instance_id=? AND {where}
                ORDER BY num DESC LIMIT 1""",
            (instance_id, *filters.values()),
        )
        return rows[0] if rows else None

    @staticmethod
    def _write_task(db: sqlite3.Connection, record: dict[str, Any]) -> None:
        db.execute(
            f"UPDATE tasks SET {_TASK_UPDATE} WHERE num=?",
            (*TaskStore._task_values(record), record["num"]),
        )

    def update(
        self, task_id: str, *, expected_status: str | Iterable[str] | None = None, **changes: Any
    ) -> dict[str, Any] | None:
        if set(changes) & {
            "id",
            "num",
            "instance_id",
            "actor_key",
            "sender_id",
            "umo",
            "created_at",
        }:
            raise ValueError("任务身份、归属和创建时间不能修改。")
        expected = {expected_status} if isinstance(expected_status, str) else expected_status
        with self._write() as db:
            task = _restored(
                db.execute("SELECT num, payload FROM tasks WHERE id=?", (task_id,)).fetchone()
            )
            if task is None or (expected is not None and task.get("status") not in expected):
                return None
            task.update(_snapshot(changes))
            self._write_task(db, task)
            return task

    def remove(self, task_id: str) -> bool:
        with self._write() as db:
            return db.execute("DELETE FROM tasks WHERE id=?", (task_id,)).rowcount > 0

    @staticmethod
    def _seen_session(
        db: sqlite3.Connection, umo: str, platform: str, sender: str, now: float
    ) -> None:
        if umo:
            db.execute(
                """INSERT INTO sessions(umo, platform, sender, last_used) VALUES (?, ?, ?, ?)
                   ON CONFLICT(umo) DO UPDATE SET
                   platform=excluded.platform, sender=excluded.sender, last_used=excluded.last_used
                   WHERE excluded.last_used >= sessions.last_used""",
                (umo, platform or "", sender or "", now),
            )

    def seen_session(
        self, umo: str, platform: str = "", sender: str = "", *, now: float | None = None
    ) -> None:
        with self._write() as db:
            self._seen_session(db, umo, platform, sender, _now(now))

    def sessions(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT umo, platform, sender, last_used AS lastUsed FROM sessions ORDER BY last_used DESC"
                )
            ]

    def session_overrides(self, umo: str) -> dict[str, Any]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT config FROM session_overrides WHERE umo=?", (umo,)).fetchone()
        return json.loads(row["config"]) if row else {}

    def set_session_overrides(self, umo: str, changes: dict[str, Any]) -> dict[str, Any]:
        if not umo:
            raise ValueError("需要选择一个会话。")
        with self._write() as db:
            row = db.execute("SELECT config FROM session_overrides WHERE umo=?", (umo,)).fetchone()
            updated = json.loads(row["config"]) if row else {}
            for key, value in _snapshot(changes).items():
                if value is None:
                    updated.pop(key, None)
                else:
                    updated[key] = value
            if updated:
                db.execute(
                    """INSERT INTO session_overrides(umo, config) VALUES (?, ?)
                       ON CONFLICT(umo) DO UPDATE SET config=excluded.config""",
                    (umo, _json(updated)),
                )
            else:
                db.execute("DELETE FROM session_overrides WHERE umo=?", (umo,))
            return updated

    @staticmethod
    def _quota(db: sqlite3.Connection, actor_key: str, now: float) -> dict[str, Any]:
        day = datetime.fromtimestamp(now).date().isoformat()
        row = db.execute(
            """SELECT COUNT(*) AS used, COALESCE(SUM(cloud), 0) AS cloud_used
               FROM quota WHERE actor_key=? AND day=?""",
            (actor_key, day),
        ).fetchone()
        last = db.execute(
            "SELECT MAX(created_at) FROM quota WHERE actor_key=? AND day>=?",
            (actor_key, datetime.fromtimestamp(now - 86400).date().isoformat()),
        ).fetchone()[0]
        return {**dict(row), "last_at": last}

    def quota(self, actor_key: str, now: float | None = None) -> dict[str, Any]:
        with closing(self._connect()) as db:
            return self._quota(db, actor_key, _now(now))

    def admit(
        self,
        record: dict[str, Any],
        *,
        actor_key: str,
        daily_limit: int = 0,
        user_limit: int = 0,
        cooldown: int = 0,
        cloud: bool = False,
        cloud_limit: int = 0,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = _now(now)
        if not actor_key:
            raise ValueError("无法识别任务发起人。")
        with self._write() as db:
            quota = self._quota(db, actor_key, now)
            if daily_limit and quota["used"] >= daily_limit:
                raise AdmissionDenied("daily_quota", "今天的创作额度已用完，明天再来吧。")
            if cloud and cloud_limit and quota["cloud_used"] >= cloud_limit:
                raise AdmissionDenied("cloud_quota", "今天的云端生成额度已用完，请改用本地实例。")
            if cooldown and quota["last_at"] is not None:
                retry = math.ceil(quota["last_at"] + cooldown - now)
                if retry > 0:
                    raise AdmissionDenied(
                        "cooldown", f"请再等 {retry} 秒再提交。", retry_after=retry
                    )
            if user_limit:
                count = db.execute(
                    f"""SELECT count(*) FROM tasks
                        WHERE actor_key=? AND status IN ({_placeholders(PENDING_STATES)})""",
                    (actor_key, *PENDING_STATES),
                ).fetchone()[0]
                if count >= user_limit:
                    raise AdmissionDenied(
                        "user_concurrency", "你的任务仍在排队或生成，请先等它完成。"
                    )
            task = self._insert(
                db, {**record, "actor_key": actor_key, "status": "queued", "created_at": now}
            )
            db.execute(
                "INSERT INTO quota(actor_key, day, created_at, cloud) VALUES (?, ?, ?, ?)",
                (actor_key, datetime.fromtimestamp(now).date().isoformat(), now, int(cloud)),
            )
            db.execute("DELETE FROM quota WHERE created_at < ?", (now - 45 * 86400,))
            return task

    def queue_position(self, task_id: str) -> int | None:
        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT COUNT(*) FROM tasks WHERE status='queued' AND num <=
                   (SELECT num FROM tasks WHERE id=? AND status='queued')""",
                (task_id,),
            ).fetchone()
            return row[0] or None

    def queue_length(self) -> int:
        with closing(self._connect()) as db:
            return db.execute("SELECT count(*) FROM tasks WHERE status='queued'").fetchone()[0]

    def pending(self) -> list[dict[str, Any]]:
        return self._fetch_tasks(
            f"""SELECT num, payload FROM tasks WHERE status IN ({_placeholders(PENDING_STATES)})
                ORDER BY num""",
            tuple(PENDING_STATES),
        )

    @staticmethod
    def _has_capacity(db: sqlite3.Connection, global_limit: int) -> bool:
        if global_limit <= 0:
            return True
        active_count = db.execute(
            f"SELECT count(*) FROM tasks WHERE status IN ({_placeholders(ACTIVE_STATES)})",
            tuple(ACTIVE_STATES),
        ).fetchone()[0]
        return active_count < global_limit

    def claim_next(self, global_limit: int) -> dict[str, Any] | None:
        with self._write() as db:
            if not self._has_capacity(db, global_limit):
                return None
            task = _restored(
                db.execute(
                    "SELECT num, payload FROM tasks WHERE status='queued' ORDER BY num LIMIT 1"
                ).fetchone()
            )
            if task is None:
                return None
            task.update(status="dispatching", started_at=time.time())
            self._write_task(db, task)
            return task

    def claim_control(self, task_id: str, status: str, global_limit: int) -> bool:
        """Reserve capacity for resuming or retrying an existing remote job."""
        if status in PENDING_STATES:
            return False
        with self._write() as db:
            if not self._has_capacity(db, global_limit):
                return False
            task = _restored(
                db.execute(
                    "SELECT num, payload FROM tasks WHERE id=? AND status=?",
                    (task_id, status),
                ).fetchone()
            )
            if task is None:
                return False
            task.update(status="dispatching", started_at=time.time())
            self._write_task(db, task)
            return True

    def claim_notification(self, task_id: str, status: str) -> bool:
        with self._write() as db:
            task = _restored(
                db.execute(
                    "SELECT num, payload FROM tasks WHERE id=? AND status=? AND notified=0",
                    (task_id, status),
                ).fetchone()
            )
            if task is None:
                return False
            task["notified"] = True
            self._write_task(db, task)
            return True

    def audit(
        self,
        action: str,
        *,
        actor_id: str = "",
        instance_id: str = "",
        task_id: str = "",
        outcome: str = "ok",
    ) -> None:
        with self._write() as db:
            cursor = db.execute(
                """INSERT INTO audit(action, actor_id, instance_id, task_id, outcome, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (action, actor_id, instance_id, task_id, outcome, time.time()),
            )
            db.execute("DELETE FROM audit WHERE id <= ?", (cursor.lastrowid - 1000,))

    def audit_log(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            return [
                dict(row)
                for row in db.execute(
                    """SELECT id, action, actor_id, instance_id, task_id, outcome, created_at
                   FROM audit ORDER BY id DESC LIMIT ?""",
                    (max(0, min(limit, 500)),),
                )
            ]

    def list_instances(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            return [
                json.loads(row["config"])
                for row in db.execute("SELECT config FROM instances ORDER BY id")
            ]

    def get_primary_token(self, address: str) -> str | None:
        """Read a local override only for the server it was configured against."""
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT token FROM primary_credentials WHERE address=?", (address,)
            ).fetchone()
        return row["token"] if row else None

    def set_primary_token(self, address: str, token: str) -> None:
        with self._write() as db:
            if not token:
                db.execute("DELETE FROM primary_credentials WHERE address=?", (address,))
                return
            db.execute(
                """INSERT INTO primary_credentials(address, token) VALUES (?, ?)
                   ON CONFLICT(address) DO UPDATE SET token=excluded.token""",
                (address, token),
            )

    def save_instance(self, config: dict[str, Any]) -> None:
        with self._write() as db:
            db.execute(
                """INSERT INTO instances(id, config) VALUES (?, ?)
                   ON CONFLICT(id) DO UPDATE SET config=excluded.config""",
                (config["id"], _json(config)),
            )

    def remove_instance(self, instance_id: str) -> bool:
        with self._write() as db:
            if db.execute(
                f"SELECT 1 FROM tasks WHERE instance_id=? AND status IN ({_placeholders(PENDING_STATES)}) LIMIT 1",
                (instance_id, *PENDING_STATES),
            ).fetchone():
                raise ValueError("此实例有排队或进行中的任务，请先等待完成。")
            deleted = db.execute("DELETE FROM instances WHERE id=?", (instance_id,)).rowcount > 0
            if deleted:
                db.execute("DELETE FROM webhooks WHERE instance_id=?", (instance_id,))
                db.execute("DELETE FROM webhook_deliveries WHERE instance_id=?", (instance_id,))
                db.execute(
                    "DELETE FROM meta WHERE key='default_instance' AND value=?", (instance_id,)
                )
            return deleted

    def default_instance(self) -> str:
        with closing(self._connect()) as db:
            row = db.execute("SELECT value FROM meta WHERE key='default_instance'").fetchone()
        return row["value"] if row else "primary"

    def set_default_instance(self, instance_id: str) -> None:
        with self._write() as db:
            if (
                instance_id != "primary"
                and not db.execute("SELECT 1 FROM instances WHERE id=?", (instance_id,)).fetchone()
            ):
                raise ValueError("默认实例不存在。")
            db.execute(
                """INSERT INTO meta(key, value) VALUES ('default_instance', ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (instance_id,),
            )

    def get_webhook(self, instance_id: str) -> dict[str, Any]:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT config FROM webhooks WHERE instance_id=?", (instance_id,)
            ).fetchone()
        return json.loads(row["config"]) if row else {}

    def set_webhook(self, instance_id: str, config: dict[str, Any]) -> None:
        with self._write() as db:
            if not config:
                db.execute("DELETE FROM webhooks WHERE instance_id=?", (instance_id,))
                return
            db.execute(
                """INSERT INTO webhooks(instance_id, config) VALUES (?, ?)
                   ON CONFLICT(instance_id) DO UPDATE SET config=excluded.config""",
                (instance_id, _json(config)),
            )

    def remember_delivery(
        self, instance_id: str, delivery_id: str, now: float | None = None
    ) -> bool:
        now = _now(now)
        with self._write() as db:
            db.execute("DELETE FROM webhook_deliveries WHERE received_at < ?", (now - 86400,))
            return (
                db.execute(
                    """INSERT OR IGNORE INTO webhook_deliveries(instance_id, delivery_id, received_at)
                   VALUES (?, ?, ?)""",
                    (instance_id, delivery_id, now),
                ).rowcount
                > 0
            )
