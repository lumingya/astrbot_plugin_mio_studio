import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.delivery import Frame, FrameBundle, MioDelivery
from core.v2_client import DownloadedFile, MioApiError


@pytest.fixture
def delivery(tmp_path):
    return MioDelivery(SimpleNamespace(send_message=AsyncMock()), tmp_path, Mock())


@pytest.fixture
def adapter(monkeypatch):
    """Stub AstrBot components at its boundary; no running bot is needed."""

    def element(kind):
        def factory(*args, **kwargs):
            return SimpleNamespace(kind=kind, args=args, **kwargs)

        return factory

    components = ModuleType("astrbot.api.message_components")
    for kind in ("File", "Plain", "Node", "Nodes"):
        setattr(components, kind, element(kind))
    components.Image = SimpleNamespace(fromFileSystem=element("Image"))
    events = ModuleType("astrbot.api.event")
    events.MessageChain = element("MessageChain")
    monkeypatch.setitem(sys.modules, components.__name__, components)
    monkeypatch.setitem(sys.modules, events.__name__, events)
    return components


@pytest.mark.parametrize(
    ("fmt", "operation", "specific", "suffix"),
    [
        ("html", "submit_album_export", {"template_id": "export-folio"}, "html"),
        ("pdf", "submit_portable_export", {"format": "pdf"}, "pdf"),
        ("zip", "submit_portable_export", {"format": "zip"}, "zip"),
        ("strip", "submit_strip_export", {"fmt": "long", "episode_id": "ep"}, "png"),
        ("slices", "submit_strip_export", {"fmt": "slices", "episode_id": "ep"}, "zip"),
    ],
)
async def test_export_contract(delivery, fmt, operation, specific, suffix):
    file = DownloadedFile(Path("cached-file"), f"work.{suffix}", "application/octet-stream", 19)
    client = SimpleNamespace(
        call=AsyncMock(side_effect=[{"id": "ex-1", "state": "queued"}, {"state": "completed"}]),
        download_operation=AsyncMock(return_value=file),
        timeout=7,
    )
    assert (
        await delivery.export(
            client,
            "ep",
            fmt=fmt,
            template_id="export-folio",
            options={
                "image_profile": "clean",
                "theme_color": "#FAFAFA",
                "show_prompts": True,
                "preset": "webtoon",
            },
            idempotency_key="fixture-key",
        )
        == file
    )
    first, second = client.call.await_args_list
    assert first.args == (operation,)
    body = first.kwargs["body"]
    assert body["idempotency_key"] == "fixture-key"
    assert all(body[key] == value for key, value in specific.items())
    if fmt in {"html", "pdf", "zip"}:
        assert body["episode_ids"] == ["ep"] and body["image_profile"] == "clean"
        assert ("theme_color" in body) == ("show_prompts" in body) == (fmt == "html")
    else:
        assert body["preset"] == "webtoon"
        assert "image_profile" not in body
    assert second.args == ("get_job",)
    args, kwargs = client.download_operation.call_args
    assert args == ("download_export",)
    assert kwargs["path_params"] == {"job_id": "ex-1"}
    assert kwargs["filename"].endswith(f".{suffix}")


async def test_motion_export_streams_html_directly(delivery):
    client = SimpleNamespace(
        call=AsyncMock(),
        download_operation=AsyncMock(),
        timeout=7,
    )
    await delivery.export(client, "ep", fmt="motion", options={"variant_id": "alt"})
    assert not client.call.called
    args, kwargs = client.download_operation.call_args
    assert args == ("export_motion",)
    assert kwargs["body"] == {"episode_id": "ep", "voice": False, "variant_id": "alt"}
    assert kwargs["filename"].endswith(".html")


async def test_export_wait_is_bounded_and_checks_failure(delivery):
    client = SimpleNamespace(call=AsyncMock(return_value={"state": "queued"}))
    with pytest.raises(MioApiError) as error:
        await delivery.wait_job(client, "job", timeout=0.01, interval=1)
    assert error.value.code == "job_pending"
    client.call.return_value = {"state": "failed", "error": "private upstream message"}
    with pytest.raises(MioApiError) as error:
        await delivery.wait_job(client, "job")
    assert error.value.code == "job_failed"
    assert "private" not in error.value.message


async def test_frames_render_adopted_and_trial_candidate_per_panel(delivery):
    episode = {
        "title": "库存",
        "panels": [
            {"id": "two", "order": 3, "description": "second"},
            {"id": "one", "order": 1, "dialogues": [{"text": "first"}]},
        ],
        "takes": [
            {"id": "alt", "panel_id": "one", "status": "adopted", "variant_id": "alt"},
            {"id": "take-1", "panel_id": "one", "status": "candidate"},
            {"id": "take-2", "panel_id": "two", "status": "adopted"},
        ],
    }
    file = DownloadedFile(Path("test.png"), "test.png", "image/png", 5)
    client = SimpleNamespace(
        call=AsyncMock(return_value=episode), download_operation=AsyncMock(return_value=file)
    )
    bundle = await delivery.frames(client, "ep", take_fallback=True)
    assert [(frame.number, frame.caption, frame.file) for frame in bundle.frames] == [
        (1, "first", file),
        (2, "second", file),
    ]
    first, second = client.download_operation.call_args_list
    assert first.args == ("take_image",)
    assert first.kwargs["path_params"] == {"episode_id": "ep", "take_id": "take-1"}
    assert second.args == ("reading_panel_image",)
    assert second.kwargs["path_params"] == {"episode_id": "ep", "panel_id": "two"}


def _image(tmp_path, size=5):
    path = tmp_path / f"test-{size}.png"
    path.write_bytes(b"0" * size)
    return DownloadedFile(path, path.name, "image/png", size)


async def test_image_delivery_falls_back_from_forward_to_batch_to_individual(
    delivery, adapter, tmp_path
):
    img = _image(tmp_path)
    bundle = FrameBundle("试画", (Frame(1, "a", "", img), Frame(2, "b", "", img)))
    sender = delivery.context.send_message
    sender.side_effect = [RuntimeError("private transport failure"), False, True, True, True]
    assert await delivery.send_images("private-session", bundle, platform="aiocqhttp") == 2
    assert [[item.kind for item in call.args[1].chain] for call in sender.call_args_list] == [
        ["Nodes"],
        ["Plain", "Plain", "Image", "Plain", "Image"],
        ["Plain"],
        ["Plain", "Image"],
        ["Plain", "Image"],
    ]
    assert delivery.logger.warning.call_count == 1
    assert "private" not in str(delivery.logger.warning.call_args)


async def test_oversize_or_partial_delivery_reports_actual_results(delivery, adapter, tmp_path):
    bundle = FrameBundle(
        "成果",
        (
            Frame(1, "a", "", _image(tmp_path, 5)),
            Frame(2, "b", "", _image(tmp_path, 15)),
            Frame(3, "c", "", None),
        ),
    )
    delivery.context.send_message.return_value = True
    assert await delivery.send_images("session", bundle, max_size=10) == 1
    final = delivery.context.send_message.call_args.args[1].chain[0].args[0]
    assert "已发送 1 张" in final and "2 张未能发送" in final
    with pytest.raises(MioApiError) as error:
        await delivery.send_file("session", bundle.frames[1].file, max_size=10)
    assert error.value.code == "delivery_file_too_large"


async def test_failed_file_delivery_does_not_send_a_success_notice(delivery, adapter, tmp_path):
    delivery.context.send_message.return_value = False
    with pytest.raises(MioApiError) as error:
        await delivery.send_file("session", _image(tmp_path), note="发送完成")
    assert error.value.code == "delivery_failed"
    delivery.context.send_message.assert_awaited_once()
