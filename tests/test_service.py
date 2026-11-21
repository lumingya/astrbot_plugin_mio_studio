import asyncio
import logging
from dataclasses import replace

import pytest

from core.governance import Actor, GovernanceError
from core.service import MioStudioService, ServiceError


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.01)


class Mio:
    def __init__(self):
        self.calls = []
        self.release = asyncio.Event()
        self.finished = False

    async def call(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation == "create_workshop_task":
            await self.release.wait()
            return {
                "series": {"id": "series-a"},
                "episode": {"id": "episode-a"},
                "job": {"id": "job-a", "state": "running", "items": [{"state": "running"}]},
            }
        if operation == "get_job":
            return {
                "id": kwargs["path_params"]["job_id"],
                "state": "completed" if self.finished else "running",
                "items": [{"state": "complete" if self.finished else "running"}],
            }
        raise AssertionError(operation)


def selection():
    return {
        "instance_id": "primary",
        "storyboard": {"id": "board", "title": "故事"},
        "workflow": {"id": "workflow", "title": "线稿"},
        "channel": None,
        "presets": [],
        "body": {"storyboard_id": "board", "generation": {"workflow_id": "workflow"}},
    }


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    service = MioStudioService(
        None,
        {
            "governance": {"global_limit": 1, "cooldown_seconds": 0},
        },
        tmp_path,
        logging.getLogger("mio-test"),
    )
    upstream = Mio()
    service.instances._configs["primary"] = replace(
        service.instances.resolve(), token="fixture-credential"
    )
    notices = []

    async def send(umo, message):
        notices.append((umo, message))
        return True

    monkeypatch.setattr(service.instances, "get", lambda instance_id=None: upstream)
    monkeypatch.setattr(service.delivery, "send_text", send)
    try:
        yield service, upstream, notices
    finally:
        await service.close()


async def test_queue_is_atomic_and_other_member_cannot_control(setup):
    service, server, _ = setup
    creator = Actor("alice", session="qq:GroupMessage:group")
    other = Actor("bob", session=creator.session)
    first = await service.create_task(selection(), actor=creator)
    await until(lambda: len(server.calls) == 1)
    queued = await service.create_task(selection(), actor=other)
    assert service.merge_view(queued, None)["queuePosition"] == 1
    with pytest.raises(GovernanceError, match="不属于你"):
        await service.task_action(first, "cancel", actor=other)
    with pytest.raises(ServiceError, match="排队"):
        await service.create_task(selection(), actor=other)
    await asyncio.sleep(0.02)
    assert len([op for op, _ in server.calls if op == "create_workshop_task"]) == 1


async def test_webhook_and_polling_share_notification_claim(setup):
    from core.webhook import WebhookEvent

    service, server, notices = setup
    creator = Actor("alice", session="qq:GroupMessage:group")
    first = await service.create_task(selection(), actor=creator)
    server.release.set()
    await until(lambda: service.store.get(first["id"])["status"] == "running")
    server.finished = True
    hook = WebhookEvent("delivery-a", "job.completed", 0, "job-a", "episode-a")
    service._hook_event("primary", hook)
    record = service.store.get(first["id"])
    await asyncio.gather(
        service.task_view(record, actor=creator), service.task_view(record, actor=creator)
    )
    await until(lambda: len(notices) == 1)
    assert service.store.get(first["id"])["done"] == 1
    assert notices[0][0] == creator.session
    assert service.store.get(first["id"])["notified"] is True


async def test_idle_queue_stops_poller(setup):
    service, server, _ = setup
    await service.start()
    await until(lambda: service._poller.done())
    assert not server.calls
