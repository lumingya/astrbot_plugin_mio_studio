from core.store import TaskStore


def test_task_number_survives_restart(tmp_path):
    path = tmp_path / "tasks.json"
    store = TaskStore(path)
    first = store.add({"id": "episode-1", "umo": "test:GroupMessage:one"})
    reopened = TaskStore(path)
    assert reopened.by_num(first["num"])["id"] == "episode-1"
    assert reopened.add({"id": "episode-2"})["num"] > first["num"]
