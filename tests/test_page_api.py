import importlib
import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.panel_queries import assembly_preview, episode_view, export_options, overview_metrics
from core.service import ServiceError
from core.v2_client import DownloadedFile


@pytest.fixture
def web(monkeypatch):
    # Run the handlers through the same registration boundary as AstrBot,
    # without loading a bot or requiring its runtime dependencies in CI.
    mod = ModuleType("astrbot.api.web")
    req = SimpleNamespace(username="dashboard", query={}, json=AsyncMock())
    mod.request = req
    mod.json_response = lambda data: data
    mod.file_response = lambda path, **kwargs: {"path": path, **kwargs}
    mod.error_response = lambda message, **kwargs: {"message": message, **kwargs}
    monkeypatch.setitem(sys.modules, mod.__name__, mod)
    monkeypatch.delitem(sys.modules, "core.page_api", raising=False)
    api = importlib.import_module("core.page_api")
    yield api, req
    sys.modules.pop("core.page_api", None)


def routes(api, service):
    captured = {}
    context = SimpleNamespace(
        register_web_api=lambda path, handler, methods, desc: captured.update(
            {(method, path.removeprefix("/mio")): handler for method in methods}
        )
    )
    api.PageApi("mio", service, Mock()).register(context)
    return captured


async def test_dashboard_requires_server_authenticated_identity(web):
    api, req = web
    req.username = None
    req.json.return_value = {"username": "claimed-admin", "is_admin": True, "idea": "mock"}
    submit = AsyncMock()
    post = routes(api, SimpleNamespace(create_draw=submit))
    assert (await post["POST", "/studio/draw"]())["status_code"] == 401
    submit.assert_not_awaited()


async def test_model_discovery_uses_only_saved_mio_channel(web):
    api, req = web
    req.json.return_value = {
        "id": "channel",
        "instance_id": "studio",
        "base_url": "https://ignored.example.invalid",
        "api_key": "unused-provider-fixture",
    }
    client = SimpleNamespace(
        call=AsyncMock(
            side_effect=[
                {
                    "image_channels": [
                        {
                            "id": "channel",
                            "base_url": "https://saved.example.invalid",
                            "kind": "openai_images",
                            "api_key": "••••••",
                        }
                    ]
                },
                {"models": [{"id": "model-a", "name": "Model A", "credentials": "unused"}]},
            ]
        )
    )
    service = SimpleNamespace(
        route=lambda actor, identifier: SimpleNamespace(id=identifier),
        instances=SimpleNamespace(get=lambda identifier: client),
    )
    result = await routes(api, service)["POST", "/resources/models"]()
    assert result["models"] == [{"id": "model-a", "name": "Model A", "group": ""}]
    assert client.call.await_args.kwargs["body"] == {
        "kind": "openai_images",
        "base_url": "https://saved.example.invalid",
        "api_key": "••••••",
        "channel_id": "channel",
    }


async def test_channel_probe_accepts_saved_default_channel_id(web):
    api, req = web
    req.json.return_value = {"id": "", "instance_id": "studio"}
    client = SimpleNamespace(
        call=AsyncMock(
            side_effect=[
                {
                    "image_channels": [
                        {"id": "", "kind": "openai_images", "base_url": "", "api_key": ""}
                    ]
                },
                {"models": [{"id": "saved-default"}], "notice": ""},
            ]
        )
    )
    service = SimpleNamespace(
        route=lambda actor, identifier: SimpleNamespace(id=identifier),
        instances=SimpleNamespace(get=lambda identifier: client),
    )
    result = await routes(api, service)["POST", "/resources/models"]()
    assert result["models"][0]["id"] == "saved-default"
    assert client.call.await_args.kwargs["body"]["channel_id"] == ""


async def test_task_preview_gets_bound_instance_and_exposes_no_path(web, tmp_path):
    api, req = web
    req.query.update(instance_id="other", panel_id="panel-a")
    path = tmp_path / "preview.png"
    path.write_bytes(b"png-fixture")
    client = object()
    open_client = Mock(return_value=client)
    read = AsyncMock(return_value=DownloadedFile(path, path.name, "image/png", 11))
    service = SimpleNamespace(
        find_record=lambda task_id, actor: {
            "id": task_id,
            "status": "complete",
            "episode_id": "ep",
            "instance_id": "owner",
        },
        delivery=SimpleNamespace(panel_image=read),
        client_for_task=lambda record: open_client(record["instance_id"]),
    )
    result = await routes(api, service)["GET", "/tasks/<task_id>/image"]("task-a")
    assert result == {"src": "data:image/png;base64,cG5nLWZpeHR1cmU="}
    open_client.assert_called_once_with("owner")
    assert read.await_args.args == (client, "ep", "panel-a")


async def test_task_detail_uses_record_instance_and_projects_only_reader_fields(web):
    api, _ = web
    record = {
        "id": "local-a",
        "status": "complete",
        "episode_id": "ep",
        "instance_id": "old-server",
    }
    client = object()
    open_client = Mock(return_value=client)
    service = SimpleNamespace(
        find_record=Mock(return_value=record),
        task_view=AsyncMock(
            return_value={
                "num": 1,
                "id": "local-a",
                "status": "complete",
                "statusKey": "complete",
                "episode_id": "ep",
                "instance_id": "old-server",
                "selection": {},
            }
        ),
        client_for_task=lambda value: open_client(value["instance_id"]),
        instances=SimpleNamespace(
            get=Mock(side_effect=AssertionError("should use original config"))
        ),
    )
    plugin_routes = routes(api, service)
    api_payload = plugin_routes["GET", "/tasks/<task_id>"]

    async def episode(operation, *, path_params):
        assert operation == "get_episode"
        assert path_params == {"episode_id": "ep"}
        return {
            "id": "ep",
            "series_id": "series-private-internal",
            "title": "Shelf",
            "synopsis": "Synopsis",
            "panels": [{"id": "p", "order": 0, "title": "Frame"}],
            "takes": [
                {
                    "id": "t",
                    "panel_id": "p",
                    "status": "adopted",
                    "score": 0.9,
                    "parameter_snapshot": {"prompt": "private-prompt"},
                    "qa": {"score": 0.95, "passed": True, "issues": ["good"]},
                }
            ],
        }

    client_api = SimpleNamespace(call=AsyncMock(side_effect=episode))
    open_client.return_value = client_api
    result = await api_payload("local-a")
    assert result["panels"][0]["takes"][0]["qa"]["score"] == 0.95
    assert "parameter_snapshot" not in str(result)
    open_client.assert_called_once_with("old-server")


async def test_gallery_send_does_not_accept_an_unknown_session(web):
    api, req = web
    req.json.return_value = {"umo": "unknown:GroupMessage:sample", "format": "pdf"}
    send = AsyncMock()
    service = SimpleNamespace(
        store=SimpleNamespace(sessions=lambda: []),
        route=lambda actor, identifier: SimpleNamespace(id="main"),
        send_work=send,
    )
    output = await routes(api, service)["POST", "/gallery/<episode_id>/send"]("ep")
    assert output["data"]["code"] == "unknown_session"
    send.assert_not_awaited()


async def test_diagnose_projects_expected_readable_checks(web):
    api, req = web
    req.json.return_value = {"workflow_id": "wf", "instance_id": "studio"}
    client = SimpleNamespace(
        call=AsyncMock(
            return_value={
                "ok": False,
                "diagnostics": {
                    "missing_models": [
                        {"node": "4", "class_type": "Checkpoint", "input": "model_name"}
                    ],
                    "missing_inputs": [
                        {"node": "4", "class_type": "Checkpoint", "input": "ckpt_name"}
                    ],
                    "invalid_links": [],
                    "invalid_inputs": [],
                    "invalid_values": [],
                    "missing_nodes": {"MissingNode": ["12"]},
                },
            }
        )
    )
    service = SimpleNamespace(
        route=lambda actor, identifier: SimpleNamespace(id=identifier),
        instances=SimpleNamespace(get=lambda identifier: client),
    )
    result = await routes(api, service)["POST", "/resources/diagnose"]()
    diagnosis = result["diagnosis"]
    assert diagnosis["ok"] is False
    assert diagnosis["issues"] == 3
    assert diagnosis["checks"][0]["message"] == ("Checkpoint · 4 · ckpt_name")
    assert diagnosis["missing_nodes"] == {"MissingNode": ["12"]}


async def test_missing_saved_channel_is_rejected_before_model_discovery(web):
    api, req = web
    req.json.return_value = {"id": "unconfigured", "instance_id": "studio"}
    client = SimpleNamespace(call=AsyncMock(return_value={"image_channels": []}))
    service = SimpleNamespace(
        route=lambda actor, identifier: SimpleNamespace(id=identifier),
        instances=SimpleNamespace(get=lambda identifier: client),
    )
    output = await routes(api, service)["POST", "/resources/models"]()
    assert output["data"]["code"] == "channel_not_found"
    assert client.call.await_count == 1


def test_preview_missing_variables_and_panel_take_positions():
    preview = assembly_preview(
        {
            "title": "示例",
            "panels": [
                {
                    "panel_id": "p1",
                    "positive": "{weather}",
                    "variables": [
                        {"key": "weather", "status": "missing"},
                        {"key": "api_key", "status": "resolved", "value": "fixture-credential"},
                    ],
                }
            ],
        }
    )
    assert preview["panels"][0]["missing_variables"] == ["weather"]
    assert preview["panels"][0]["variables"][1]["value"] == ""
    episode = episode_view(
        {
            "id": "ep",
            "panels": [{"id": "p1", "order": 0}],
            "takes": [
                {"id": "discarded", "panel_id": "p1", "status": "rejected"},
                {"id": "a", "panel_id": "p1", "status": "candidate"},
                {"id": "b", "panel_id": "p1", "status": "adopted", "score": 0.9},
            ],
        }
    )
    assert episode["panels"][0]["status"] == "adopted"
    assert [(t["id"], t["number"]) for t in episode["panels"][0]["takes"]] == [("a", 1), ("b", 2)]


def test_overview_counts_art_not_quality_jobs_and_validates_export_options():
    now = datetime(2025, 4, 2, 20)
    t = now.timestamp()
    metrics = overview_metrics(
        [
            {"status": "complete", "done": 4, "started_at": t - 45, "finished_at": t},
            {"status": "complete", "kind": "qa", "done": 5, "finished_at": t},
            {"status": "queued"},
        ],
        {"workflows": [{}], "presets": [{}, {}]},
        now=now,
    )
    assert metrics == {"images": 4, "active": 1, "average": 45, "resources": 3}
    assert export_options({"show_prompts": "false", "options": {"extra": "ignore"}}) == {
        "show_prompts": False,
    }
    with pytest.raises(ServiceError, match="十六进制"):
        export_options({"theme_color": "red"})
