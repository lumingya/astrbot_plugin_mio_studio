import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime

import pytest

from core.store import AdmissionDenied, MigrationError, TaskStore


def test_migrates_legacy_history_once_and_preserves_previous_sequence(tmp_path):
    source = tmp_path / "tasks.json"
    original = json.dumps(
        {
            "seq": 19,
            "tasks": [
                {
                    "num": 7,
                    "id": "v1",
                    "albumId": "v1-album",
                    "status": "running",
                    "umo": "qq:GroupMessage:group",
                    "sender_id": "owner",
                    "selection": {"nested": [{"api_key": "fake-secret"}]},
                }
            ],
        }
    )
    source.write_text(original, encoding="utf-8")
    store = TaskStore(source)
    legacy = store.by_num(7)
    assert legacy["id"] == "v1" and legacy["albumId"] == "v1-album"
    assert legacy["status"] == "archived" and legacy["legacy_status"] == "running"
    assert legacy["sender_id"] == "owner"
    assert not legacy["job_id"] and not legacy["episode_id"]
    assert legacy["selection"] == {"nested": [{}]}
    assert store.pending() == []
    assert source.read_text(encoding="utf-8") == original
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    assert store.add({"id": "new"})["num"] == 20
    reopened = TaskStore(source)
    assert [row["num"] for row in reopened.all()] == [7, 20]


def test_invalid_archive_rolls_back_all_rows_and_is_retriable(tmp_path):
    source = tmp_path / "tasks.json"
    rows = [{"id": "one", "num": 1}, {"id": "two", "num": 1}]
    source.write_text(json.dumps({"seq": 2, "tasks": rows}), encoding="utf-8")
    with pytest.raises(MigrationError):
        TaskStore(source)
    assert json.loads(source.read_text(encoding="utf-8"))["tasks"] == rows
    with closing(sqlite3.connect(source.with_suffix(".sqlite3"))) as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0

    rows[1]["num"] = 2
    source.write_text(json.dumps({"seq": 2, "tasks": rows}), encoding="utf-8")
    assert [row["num"] for row in TaskStore(source).all()] == [1, 2]


def test_empty_migration_still_preserves_number_sequence(tmp_path):
    source = tmp_path / "tasks.json"
    source.write_text('{"seq":42,"tasks":[]}', encoding="utf-8")
    assert TaskStore(source).add({"id": "next"})["num"] == 43


def test_queue_claim_is_atomic_across_store_connections_and_keeps_fifo(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    writer, worker = TaskStore(path), TaskStore(path)
    for idx in range(5):
        writer.admit({"id": f"task-{idx}"}, actor_key=f"qq:{idx}")

    assert worker.queue_position("task-4") == 5
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = [pool.submit(store.claim_next, 1) for store in (writer, worker)]
    claimed = [record for future in claims if (record := future.result()) is not None]
    assert [record["id"] for record in claimed] == ["task-0"]
    assert worker.queue_position("task-4") == 4
    assert worker.claim_next(1) is None
    assert writer.update("task-0", expected_status="queued", status="cancelled") is None
    assert writer.update("task-0", expected_status="dispatching", status="cancelled")
    assert worker.claim_next(1)["id"] == "task-1"


def test_concurrent_user_limit_and_admission_charge_share_transaction(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    store = TaskStore(path)
    peers = [TaskStore(path) for _ in range(5)]

    def request(idx):
        try:
            return peers[idx].admit({"id": f"request-{idx}"}, actor_key="qq:owner", user_limit=1)[
                "id"
            ]
        except AdmissionDenied as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(request, range(5)))
    assert results.count("user_concurrency") == 4
    assert store.quota("qq:owner")["used"] == 1


def test_retry_and_new_tasks_compete_for_same_capacity(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    submitter, controller = TaskStore(path), TaskStore(path)
    submitter.admit({"id": "new"}, actor_key="qq:owner")
    submitter.add({"id": "previous", "status": "paused"})
    assert not controller.claim_control("previous", "failed", 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        new = pool.submit(submitter.claim_next, 1)
        retry = pool.submit(controller.claim_control, "previous", "paused", 1)
    assert bool(new.result()) != retry.result()
    assert not controller.claim_control("previous", "dispatching", 0)


def test_cloud_quota_and_cooldown_are_separate_and_roll_over(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    t = datetime(2026, 11, 20, 23, 59, 55).timestamp()
    store.admit({"id": "first"}, actor_key="qq:owner", cloud=True, now=t)
    with pytest.raises(AdmissionDenied) as cooldown:
        store.admit({"id": "early"}, actor_key="qq:owner", cooldown=10, now=t + 6)
    assert cooldown.value.code == "cooldown" and cooldown.value.retry_after == 4
    with pytest.raises(AdmissionDenied, match="云端") as cloud:
        store.admit({"id": "cloud"}, actor_key="qq:owner", cloud=True, cloud_limit=1, now=t)
    assert cloud.value.code == "cloud_quota"
    store.admit({"id": "local"}, actor_key="qq:owner", cloud_limit=1, now=t + 1)
    assert store.quota("qq:owner", t + 1)["used"] == 2
    assert store.quota("qq:owner", t + 1)["cloud_used"] == 1
    store.admit(
        {"id": "tomorrow"},
        actor_key="qq:owner",
        daily_limit=1,
        cloud_limit=1,
        cloud=True,
        now=t + 10,
    )
    assert store.quota("qq:owner", t + 11)["used"] == 1


def test_remote_ids_are_scoped_and_final_notification_has_one_claimant(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    store = TaskStore(path)
    peer = TaskStore(path)
    store.add({"id": "one", "job_id": "same", "status": "succeeded"})
    store.add({"id": "two", "job_id": "same", "instance_id": "backup", "status": "succeeded"})
    assert store.find_remote("primary") is None
    assert store.find_remote("primary", job_id="same")["id"] == "one"
    assert store.find_remote("backup", job_id="same")["id"] == "two"
    assert store.claim_notification("one", "failed") is False
    with ThreadPoolExecutor(max_workers=2) as pool:
        chances = [
            pool.submit(worker.claim_notification, "one", "succeeded") for worker in (store, peer)
        ]
    assert sorted(job.result() for job in chances) == [False, True]
    assert not peer.get("two")["notified"]


def test_preferences_audit_and_webhook_replay_persist_on_reopen(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    store = TaskStore(path)
    umo = "qq:GroupMessage:group"
    store.add({"id": "id", "umo": umo, "platform": "qq", "sender_name": "creator"})
    store.set_session_overrides(
        umo, {"notify_mode": "silent", "layout_id": "draft", "token": "secret"}
    )
    store.set_session_overrides(umo, {"layout_id": None})
    store.save_instance({"id": "remote", "label": "Test"})
    store.set_default_instance("remote")
    store.set_webhook("primary", {"hook_id": "hook", "secret": "fixture"})
    assert store.remember_delivery("primary", "duplicate")
    store.audit("task_created", actor_id="creator", task_id="id")

    reopened = TaskStore(path)
    assert reopened.sessions()[0]["umo"] == umo
    assert reopened.session_overrides(umo) == {"notify_mode": "silent"}
    assert reopened.default_instance() == "remote"
    assert reopened.get_webhook("primary")["hook_id"] == "hook"
    assert not reopened.remember_delivery("primary", "duplicate")
    assert reopened.remember_delivery("remote", "duplicate")
    assert reopened.audit_log()[0]["action"] == "task_created"
    assert reopened.remove_instance("remote")
    assert reopened.default_instance() == "primary"


def test_primary_token_is_bound_to_the_original_address(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    store = TaskStore(path)
    address = "http://localhost:8788"
    assert store.get_primary_token(address) is None
    store.set_primary_token(address, "mio_fixture")
    assert TaskStore(path).get_primary_token(address) == "mio_fixture"
    assert store.get_primary_token("https://other.example.invalid") is None
    store.set_primary_token(address, "")
    assert TaskStore(path).get_primary_token(address) is None
