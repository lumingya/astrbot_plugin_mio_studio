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
    timeout = 60

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
            if kwargs["path_params"]["job_id"] == "qa-job":
                return {"id": "qa-job", "state": "completed", "items": []}
            return {
                "id": kwargs["path_params"]["job_id"],
                "state": "completed" if self.finished else "running",
                "items": [{"state": "complete" if self.finished else "running"}],
            }
        if operation == "get_profile":
            return {"id": kwargs["path_params"]["profile_id"], "cloud_shape": False}
        if operation == "create_series":
            return {"id": "draw-series"}
        if operation == "generate_episode":
            return {"id": "draw-episode", "panels": [{"id": "draw-panel", "order": 0}]}
        if operation == "render_episode":
            return {"id": "draw-job", "state": "running", "items": []}
        if operation == "get_episode":
            return {
                "id": "episode-qa",
                "series_id": "series-qa",
                "panels": [{"id": "panel-qa", "order": 0}],
                "takes": [
                    {
                        "id": "old-candidate",
                        "panel_id": "panel-qa",
                        "job_id": "old-job",
                        "status": "candidate",
                    },
                    {
                        "id": "new-take",
                        "panel_id": "panel-qa",
                        "job_id": "current-job",
                        "status": "candidate",
                    },
                    {
                        "id": "rejected-take",
                        "panel_id": "panel-qa",
                        "job_id": "current-job",
                        "status": "rejected",
                    },
                ],
            }
        if operation == "qa_episode":
            return {"id": "qa-job"}
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


async def test_default_cloud_channel_requires_and_records_cloud_permission(setup):
    service, _, _ = setup
    actor = Actor("artist", session="qq:GroupMessage:studio")
    service.settings.config["permissions"] = {"cloud_allowed_ids": []}
    cloud_selection = {
        **selection(),
        "workflow": None,
        "channel": {"id": "", "title": "Mio 默认云端", "cloud": True},
        "body": {
            "storyboard_id": "board",
            "preset_ids": [],
            "generation": {"cloud_channel_id": ""},
        },
    }
    with pytest.raises(GovernanceError, match="云端"):
        await service.create_task(cloud_selection, actor=actor)
    service.settings.config["permissions"]["cloud_allowed_ids"] = [actor.scoped_id]
    accepted = await service.create_task(cloud_selection, actor=actor)
    assert accepted["cloud"] is True
    assert service.store.quota(actor.scoped_id)["cloud_used"] == 1


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


async def test_failed_chat_notification_is_retried_and_acknowledged(setup, monkeypatch):
    service, _, notices = setup
    actor = Actor("artist", session="qq:GroupMessage:studio")
    task = service.store.add(
        {
            "id": "notify-retry",
            "status": "complete",
            "kind": "workshop",
            "num": 1,
            "title": "完成消息",
            "sender_id": actor.id,
            "actor_key": actor.scoped_id,
            "umo": actor.session,
            "instance_id": "primary",
            "notified": False,
        }
    )
    results = iter((False, True))

    async def fail_once(umo, message):
        delivered = next(results)
        notices.append((umo, message, delivered))
        return delivered

    monkeypatch.setattr(service.delivery, "send_text", fail_once)
    service._schedule_notification(task)
    await until(lambda: service.store.get(task["id"]).get("notification_retry_at") is not None)
    service.store.update(
        task["id"],
        notification_retry_at=0,
        notification_claimed_at=None,
    )
    service._schedule_notification(service.store.get(task["id"]))
    await until(lambda: service.store.get(task["id"])["notified"])
    assert [entry[2] for entry in notices] == [False, True]


async def test_qa_then_send_only_scores_candidates_from_completed_task(setup, monkeypatch):
    service, server, _ = setup
    actor = Actor("artist", session="qq:GroupMessage:studio")
    record = service.store.add(
        {
            "id": "qa-notify",
            "status": "complete",
            "kind": "draw",
            "title": "仅检查本次候选",
            "sender_id": actor.id,
            "actor_key": actor.scoped_id,
            "umo": actor.session,
            "instance_id": "primary",
            "episode_id": "episode-qa",
            "job_id": "current-job",
            "delivery": {"notify_mode": "qa_then_send"},
            "notified": False,
        }
    )

    async def send_images(*_args, **_kwargs):
        return 1

    monkeypatch.setattr(service, "send_images", send_images)
    await service._notify(record)
    qa_request = next(kwargs for op, kwargs in server.calls if op == "qa_episode")
    assert qa_request["body"]["take_ids"] == ["new-take"]
    assert qa_request["body"]["auto_adopt"] is True
    assert service.store.get(record["id"])["notified"] is True


async def test_idle_queue_stops_poller(setup):
    service, server, _ = setup
    await service.start()
    await until(lambda: service._poller.done())
    assert not server.calls


async def test_draw_writes_series_episode_and_render_job_before_delivery(setup):
    service, server, _ = setup
    creator = Actor("artist", session="qq:GroupMessage:atelier")
    task = await service.create_draw("凌晨的火车停在海面上", actor=creator, candidates=2)
    await until(lambda: service.store.get(task["id"])["status"] == "running")
    record = service.store.get(task["id"])
    assert (record["series_id"], record["episode_id"], record["job_id"]) == (
        "draw-series",
        "draw-episode",
        "draw-job",
    )
    draw = next(body for op, body in server.calls if op == "generate_episode")
    assert draw["body"]["sentence"] == "凌晨的火车停在海面上"
    render = next(body for op, body in server.calls if op == "render_episode")
    assert render["body"]["candidates"] == 2
    assert render["body"]["qa"] is True


async def test_failed_retry_rechecks_policy_and_charges_atomic_credit(setup):
    service, server, _ = setup
    actor = Actor("artist", session="qq:GroupMessage:studio")
    task = await service.create_task(selection(), actor=actor)
    server.release.set()
    await until(lambda: service.store.get(task["id"])["status"] == "running")
    server.calls.clear()
    service.store.update(
        task["id"],
        status="failed",
        job_id="job-retry",
        cloud=True,
        request={**task["request"], "generation": {"cloud_channel_id": "cloud-proof"}},
    )
    service.settings.config["permissions"] = {"cloud_allowed_ids": []}
    with pytest.raises(GovernanceError, match="云端"):
        await service.task_action(service.store.get(task["id"]), "resume", actor=actor)
    assert not server.calls


async def test_failed_retry_spends_quota_inside_claim(setup):
    service, server, _ = setup
    actor = Actor("artist", session="qq:GroupMessage:studio")
    task = await service.create_task(selection(), actor=actor)
    server.release.set()
    await until(lambda: service.store.get(task["id"])["status"] == "running")
    service.store.update(task["id"], status="failed", job_id="job-retry")
    service.settings.config["governance"]["daily_limit"] = 1
    with pytest.raises(ServiceError, match="额度"):
        await service.task_action(service.store.get(task["id"]), "resume", actor=actor)
    assert service.store.get(task["id"])["status"] == "failed"
    assert len(server.calls) == 1
    assert service.store.quota(actor.scoped_id)["used"] == 1


async def test_paused_resume_reserves_the_owner_concurrency_slot(setup):
    service, server, _ = setup
    actor = Actor("artist", session="qq:GroupMessage:studio")
    service._closing = True  # Do not dispatch: exercise both claims deterministically.
    paused = await service.create_task(selection(), actor=actor)
    service.store.update(paused["id"], status="paused", job_id="job-paused")
    await service.create_task(selection(), actor=actor)
    with pytest.raises(ServiceError) as denied:
        await service.task_action(service.store.get(paused["id"]), "resume", actor=actor)
    assert denied.value.code == "user_concurrency"
    assert service.store.get(paused["id"])["status"] == "paused"
    assert not server.calls
