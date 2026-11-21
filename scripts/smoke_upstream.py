"""Exercise this plugin against a local Mio source checkout and fake providers."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


async def wait_for_task(service, task_id: str) -> dict:
    async with asyncio.timeout(45):
        while True:
            record = service.store.get(task_id)
            if record["status"] not in {"queued", "dispatching", "queued_remote", "running"}:
                break
            await asyncio.sleep(0.1)
    assert record["status"] == "complete", record.get("error")
    assert record["episode_id"] and record["job_id"] and record["done"] >= 1
    return record


async def run_smoke(url: str, token: str, data_dir: Path) -> None:
    from core.governance import Actor, GovernanceError
    from core.service import MioStudioService
    from core.v2_client import MioApiError, MioV2Client
    from core.webhook import register_webhook, test_webhook, webhook_deliveries

    anonymous = MioV2Client(url, timeout=5, retries=0)
    try:
        assert (await anonymous.probe())["api"].split(".")[0] == "2"
        try:
            await anonymous.call("list_series")
        except MioApiError as error:
            assert error.status == 401
        else:
            raise AssertionError("Mio accepted an anonymous request to the gallery")
    finally:
        await anonymous.close()

    settings = {"mio": {"base_url": url}, "delivery": {"poll_interval": 2, "notify_mode": "silent"}}
    service = MioStudioService(None, settings, data_dir, logging.getLogger("mio.smoke"))
    service.store.set_primary_token(url, token)
    owner = Actor("smoke-writer", session="test:GroupMessage:workshop", platform="test")
    peer = Actor("smoke-reader", session=owner.session, platform="test")
    try:
        await service.start()
        connection = await service.status(actor=owner)
        assert connection["ok"], connection.get("error")
        assert {"read", "write", "render", "admin"} <= set(connection["scopes"])
        catalog = await service.catalog(actor=owner)
        assert all(catalog[key] for key in ("storyboards", "workflows", "presets", "layouts"))
        workflow_id = "builtin_t2i_sdxl"
        client = service.instances.get("primary")
        await service.listener.start(port=0)
        hook = await register_webhook(
            client, f"http://127.0.0.1:{service.listener.port}", "primary"
        )
        service.store.set_webhook("primary", {"hook_id": hook.hook_id, "secret": hook.secret})
        assert (await test_webhook(client, hook.hook_id))["status"] == 204
        board = await service.resources.detail(
            client, "storyboards", catalog["storyboards"][0]["id"]
        )
        assert any(item["id"] == workflow_id for item in catalog["workflows"])

        selection = await service.resolve_selection(
            workflow_id, board["id"], [catalog["presets"][0]["id"]], None, actor=owner
        )
        preview = await service.preview(selection, actor=owner)
        assert preview["panels"] and preview["panels"][0]["positive"]
        task = await service.create_task(selection, actor=owner, title="Isolated workshop smoke")
        try:
            await service.task_action(task, "cancel", actor=peer)
        except GovernanceError as error:
            assert error.code == "not_owner"
        else:
            raise AssertionError("Another chat member was able to cancel the task")
        record = await wait_for_task(service, task["id"])

        gallery = await service.works(actor=owner, query="Isolated workshop smoke")
        assert any(item["episode_id"] == record["episode_id"] for item in gallery)
        file = await service.export(record, actor=owner, fmt="html")
        assert file["contentType"].split(";")[0] == "text/html" and file["size"] > 500
        assert "<html" in file["path"].read_text(encoding="utf-8").lower()
        async with asyncio.timeout(15):
            while not any(
                item["status"] == 204 and item["event"] == "job.completed"
                for item in await webhook_deliveries(client, hook.hook_id)
            ):
                await asyncio.sleep(0.1)
        await client.call("delete_webhook", path_params={"hook_id": hook.hook_id})

        draw = await service.create_draw(
            "A night train pulls into an empty station at the edge of the sea.",
            actor=peer,
            mode="silent",
        )
        drawing = await wait_for_task(service, draw["id"])
        assert drawing["done"] == drawing["total"] >= 2
        trial = await service.create_trial(
            "A single frame with a train", workflow_id, actor=Actor.panel()
        )
        trial_record = await wait_for_task(service, trial["id"])
        frames = await service.delivery.frames(
            client, trial_record["episode_id"], take_fallback=True
        )
        assert len(frames.frames) == 1
        assert frames.frames[0].file.path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        print(
            f"Mio {connection['version']} API {connection['api']}: "
            f"authenticated catalog, preview ({len(preview['panels'])} panels), "
            f"ownership, workshop render ({record['done']}/{record['total']}), "
            f"gallery, HTML export ({file['size']} bytes), signed webhooks, "
            f"one-line comic ({drawing['done']} panels) and single-frame trial passed."
        )
    finally:
        await service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream", type=Path, help="Path to the upstream Mio repository")
    args = parser.parse_args()
    server = args.upstream.resolve() / "server"
    if not (server / "tests" / "api_harness.py").is_file():
        parser.error("the upstream checkout must include server/tests/api_harness.py")
    sys.path[:0] = [str(server), str(Path(__file__).resolve().parents[1])]
    from mio_server.models import Panel, PanelOverrides
    from tests.test_api_v2 import ApiV2Tests, LiveServer

    # Reuse the upstream test harness: isolated databases and fake ComfyUI/LLM.
    fixture = ApiV2Tests()
    try:
        fixture.setUp()
        workshop = fixture.ok(fixture.client.get("/api/workshop"))
        boards = fixture.ctx.store.list_episodes(workshop["id"])
        board = boards[0]
        board.panels = [
            Panel(
                order=0, description="Ocean platform", overrides=PanelOverrides(raw_prompt="sea")
            ),
            Panel(order=1, description="Night train", overrides=PanelOverrides(raw_prompt="train")),
        ]
        fixture.ctx.store.save_episode(board)
        token = fixture.token("read", "write", "render", "admin")
        fixture.ctx.engine.start()
        with (
            LiveServer(fixture.client.app) as url,
            TemporaryDirectory(prefix="mio-plugin-smoke-") as tmp,
        ):
            asyncio.run(run_smoke(url, token, Path(tmp)))
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    main()
