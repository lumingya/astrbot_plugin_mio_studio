"""Tiny JSON persistence for the tasks created through the plugin.

Each record keeps the stable plugin-side number (``num``), the Mio production
task id, who asked for it and how it should be delivered once finished.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

MAX_RECORDS = 400


class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {"seq": 0, "tasks": []}
        self._load()

    # ------------------------------------------------------------------ io
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except FileNotFoundError:
            return
        except Exception:
            return
        if isinstance(raw, dict) and isinstance(raw.get("tasks"), list):
            self._data = {"seq": int(raw.get("seq") or 0), "tasks": [t for t in raw["tasks"] if isinstance(t, dict)]}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tasks-", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------- queries
    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(t) for t in self._data["tasks"]]

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            for t in self._data["tasks"]:
                if t.get("id") == task_id:
                    return dict(t)
        return None

    def by_num(self, num: int) -> dict[str, Any] | None:
        with self._lock:
            for t in self._data["tasks"]:
                if t.get("num") == num:
                    return dict(t)
        return None

    def latest_for(self, umo: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            for t in reversed(self._data["tasks"]):
                if umo is None or t.get("umo") == umo:
                    return dict(t)
        return None

    def sessions(self) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        with self._lock:
            for t in reversed(self._data["tasks"]):
                umo = t.get("umo")
                if umo and umo not in seen:
                    seen[umo] = {"umo": umo, "platform": t.get("platform"), "sender": t.get("sender_name") or t.get("sender_id"), "lastUsed": t.get("created_at")}
        return list(seen.values())

    # ------------------------------------------------------------ mutation
    def add(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._data["seq"] = int(self._data.get("seq") or 0) + 1
            record = {**record, "num": self._data["seq"], "created_at": record.get("created_at") or time.time()}
            self._data["tasks"].append(record)
            if len(self._data["tasks"]) > MAX_RECORDS:
                self._data["tasks"] = self._data["tasks"][-MAX_RECORDS:]
            self._save()
            return dict(record)

    def update(self, task_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            for t in self._data["tasks"]:
                if t.get("id") == task_id:
                    t.update(changes)
                    self._save()
                    return dict(t)
        return None

    def remove(self, task_id: str) -> bool:
        with self._lock:
            before = len(self._data["tasks"])
            self._data["tasks"] = [t for t in self._data["tasks"] if t.get("id") != task_id]
            if len(self._data["tasks"]) != before:
                self._save()
                return True
        return False
