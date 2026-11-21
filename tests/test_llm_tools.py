import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.governance import Actor, GovernanceError
from core.llm_tools import LLMTools
from core.service import MioStudioService, ServiceError

ACTOR = Actor("alice", session="test:GroupMessage:room", platform="test")
_CREDENTIAL = "mio_tok_fixture_" + "A" * 25


@pytest.fixture
def service():
    return SimpleNamespace(
        cfg=Mock(return_value=True),
        catalog=AsyncMock(return_value={}),
        create_draw=AsyncMock(return_value={"num": 12, "status": "queued"}),
        resolve_selection=AsyncMock(return_value={"id": "selection"}),
        preview=AsyncMock(return_value={"panels": []}),
        create_task=AsyncMock(return_value={"num": 13, "status": "queued"}),
        create_trial=AsyncMock(return_value={"num": 14, "status": "queued"}),
        find_record=Mock(return_value={"num": 11, "instance_id": "primary"}),
        task_view=AsyncMock(
            return_value={"num": 11, "statusKey": "running", "done": 1, "total": 3}
        ),
        task_action=AsyncMock(return_value="已暂停。"),
        redraw=AsyncMock(return_value={"num": 11, "status": "queued"}),
        works=AsyncMock(return_value=[{"id": "work-a", "episode_id": "work-a"}]),
        pick=Mock(return_value={"id": "work-a", "episode_id": "work-a"}),
        send_export=AsyncMock(
            return_value={"filename": "绘页.pdf", "format": "pdf", "path": "C:/internal/file.pdf"}
        ),
        send_images=AsyncMock(return_value=2),
        send_work=AsyncMock(return_value={"filename": "作品.zip", "format": "zip"}),
    )


async def test_tools_require_opt_in_and_trusted_chat_identity(service):
    adapter = LLMTools(service)
    service.cfg.return_value = False
    assert json.loads(await adapter.draw(ACTOR, "月球邮递员"))["code"] == "disabled"
    service.create_draw.assert_not_awaited()
    service.cfg.assert_called_once_with("chat", "llm_tools_enabled", False)
    service.cfg.return_value = True
    result = json.loads(await adapter.draw(Actor.panel(), "月球邮递员"))
    assert result["code"] == "missing_actor"
    service.create_draw.assert_not_awaited()


async def test_creation_checks_inputs_and_passes_exact_actor(service):
    tools = LLMTools(service)
    response = json.loads(await tools.draw(ACTOR, "月球邮递员", candidates=2, instance_id="local"))
    assert response["ok"] and response["next"] == "/mio status 12"
    service.create_draw.assert_awaited_once_with(
        "月球邮递员",
        actor=ACTOR,
        series=None,
        style=None,
        candidates=2,
        layout=None,
        instance_id="local",
    )
    service.create_draw.reset_mock()
    assert not json.loads(await tools.draw(ACTOR, "月球邮递员", candidates=True))["ok"]
    assert not json.loads(await tools.draw(ACTOR, _CREDENTIAL))["ok"]
    service.create_draw.assert_not_awaited()

    response = json.loads(await tools.trial(ACTOR, "浅色水彩", workflow="wf", instance_id="local"))
    assert response["task"] == 14
    service.create_trial.assert_awaited_once_with(
        "浅色水彩", "wf", actor=ACTOR, instance_id="local"
    )


async def test_quota_and_upstream_errors_remain_safe(service):
    service.create_trial.side_effect = ServiceError(
        f"今天的额度已用完（Bearer {_CREDENTIAL}）。", code="daily_quota", status=429
    )
    response = await LLMTools(service).trial(ACTOR, "水彩")
    assert json.loads(response)["code"] == "daily_quota"
    assert _CREDENTIAL not in response


async def test_preview_is_the_default_and_does_not_publish_prompt_text(service):
    service.preview.return_value = {
        "panels": [
            {
                "positive": "private prompt",
                "variables": [
                    {"key": "主角", "status": "missing"},
                    {"key": "api_token", "status": "missing", "value": _CREDENTIAL},
                    {"key": "背景", "status": "resolved"},
                ],
            }
        ]
    }
    adapter = LLMTools(service)
    response = await adapter.assemble(ACTOR, "story-a", presets="小猫，书店、深墨")
    summary = json.loads(response)
    assert summary["panel_count"] == 1
    assert summary["missing_variables"] == ["主角"]
    assert "private prompt" not in response and _CREDENTIAL not in response
    service.resolve_selection.assert_awaited_once_with(
        None, "story-a", ["小猫", "书店", "深墨"], None, actor=ACTOR, instance_id=None
    )
    service.create_task.assert_not_awaited()

    assert json.loads(await adapter.assemble(ACTOR, "story-a", preview="false"))["ok"] is False
    service.create_task.assert_not_awaited()
    assert json.loads(await adapter.assemble(ACTOR, "story-a", preview=False))["task"] == 13
    service.create_task.assert_awaited_once_with({"id": "selection"}, actor=ACTOR)


async def test_workflow_search_preserves_numbers_and_bounds_safe_output(service):
    service.catalog.return_value = {
        "workflows": [
            {"id": "ignored", "title": "线稿"},
            *(
                {
                    "id": f"paper-{n}",
                    "title": f"暖纸 {n} {_CREDENTIAL} " + "长" * 500,
                    "api_key": "private metadata",
                }
                for n in range(40)
            ),
        ],
    }
    response = await LLMTools(service).list_resources(ACTOR, "workflows", "暖纸")
    parsed = json.loads(response)
    assert (
        len(response) < 3000 and _CREDENTIAL not in response and "private metadata" not in response
    )
    assert parsed["more"] is True
    assert len(parsed["items"]) == 8 and parsed["items"][0]["number"] == 2


async def test_status_and_task_control_delegate_actor_visibility(service):
    tools = LLMTools(service)
    got = json.loads(await tools.task(ACTOR, "11"))
    assert got["progress"] == {"done": 1, "total": 3}
    service.task_view.assert_awaited_once_with(service.find_record.return_value, actor=ACTOR)
    service.find_record.side_effect = GovernanceError("不属于你。", code="not_owner")
    assert json.loads(await tools.task(ACTOR, "11", action="pause"))["code"] == "not_owner"
    service.task_action.assert_not_awaited()
    service.find_record.side_effect = None
    assert json.loads(await tools.task(ACTOR, "11", action="redraw", panel=2, prompt="重画"))["ok"]
    service.redraw.assert_awaited_once_with(
        service.find_record.return_value, 2, "重画", actor=ACTOR
    )


async def test_real_service_enforces_task_ownership_before_any_upstream_io(tmp_path):
    svc = MioStudioService(
        None, {"chat": {"llm_tools_enabled": True}}, tmp_path, logging.getLogger("mio-llm-test")
    )
    try:
        record = svc.store.add(
            {"id": "local-task", "sender_id": "bob", "umo": ACTOR.session, "status": "queued"}
        )
        reply = json.loads(await LLMTools(svc).task(ACTOR, str(record["num"]), action="cancel"))
        assert reply["code"] == "not_owner"
        assert svc.store.get(record["id"])["status"] == "queued"
    finally:
        await svc.close()


async def test_export_can_only_deliver_to_current_session_and_hides_path(service):
    tools = LLMTools(service)
    response = await tools.export_and_send(ACTOR, task="11", fmt="pdf")
    assert json.loads(response)["filename"] == "绘页.pdf"
    assert "internal" not in response and ACTOR.session not in response
    service.send_export.assert_awaited_once_with(
        service.find_record.return_value, ACTOR.session, actor=ACTOR, fmt="pdf", layout=None
    )
    result = json.loads(await tools.export_and_send(ACTOR, work="work-a", instance_id="remote"))
    assert result["filename"] == "作品.zip"
    service.works.assert_awaited_once_with(actor=ACTOR, instance_id="remote", query="", limit=100)
    service.send_work.assert_awaited_once_with(
        service.pick.return_value, ACTOR.session, actor=ACTOR, fmt=None, layout=None
    )
    assert not json.loads(await tools.export_and_send(ACTOR, task="11", work="work-a"))["ok"]
