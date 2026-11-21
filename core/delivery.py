"""Export Mio works and deliver files through AstrBot platform adapters."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .v2_client import DownloadCache, DownloadedFile, MioApiError, MioV2Client

FORMATS = ("html", "pdf", "zip", "strip", "slices", "motion")
IMAGE_PROFILES = ("auto", "archive", "clean", "publish", "preview")
SEND_MODES = ("auto", "forward", "separate", "batch")
FORWARD_PLATFORMS = {"aiocqhttp", "napcat", "llonebot", "lagrange"}
_FORMAT_SUFFIX = {
    "html": "html",
    "pdf": "pdf",
    "zip": "zip",
    "strip": "png",
    "slices": "zip",
    "motion": "html",
}
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_MAX_BATCH_IMAGES = 8


@dataclass(frozen=True, slots=True)
class Frame:
    number: int
    panel_id: str
    caption: str
    file: DownloadedFile | None


@dataclass(frozen=True, slots=True)
class FrameBundle:
    title: str
    frames: tuple[Frame, ...]


class MioDelivery:
    def __init__(self, context, data_dir: Path, logger) -> None:
        self.context = context
        self.logger = logger
        self.export_cache = DownloadCache(data_dir / "exports", max_files=40)
        self.image_cache = DownloadCache(data_dir / "cache", max_files=240)

    @staticmethod
    async def wait_job(
        client: MioV2Client, job_id: str, *, timeout: float = 180.0, interval: float = 0.75
    ) -> dict[str, Any]:
        try:
            async with asyncio.timeout(timeout):
                while True:
                    job = await client.call("get_job", path_params={"job_id": job_id})
                    if not isinstance(job, dict) or not isinstance(job.get("state"), str):
                        raise MioApiError("Mio 未返回作业状态", code="bad_job")
                    state = job["state"]
                    if state == "completed":
                        return job
                    if state in {"failed", "canceled", "paused", "blocked"}:
                        raise MioApiError(
                            "Mio 未完成此作业，请在绘页检查状态",
                            code=f"job_{state}",
                            status=409,
                        )
                    await asyncio.sleep(interval)
        except TimeoutError:
            raise MioApiError(
                "Mio 仍在后台处理，请稍后查看作业或重试",
                code="job_pending",
                status=504,
                retryable=True,
            ) from None

    async def export(
        self,
        client: MioV2Client,
        episode_id: str,
        *,
        fmt: str,
        template_id: str | None = None,
        options: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> DownloadedFile:
        if fmt not in FORMATS:
            raise MioApiError("请选择 html、pdf、zip、strip、slices 或 motion", code="bad_format")
        opts = options or {}
        filename = f"mio-{fmt}.{_FORMAT_SUFFIX[fmt]}"
        variant = opts.get("variant_id")
        if fmt == "motion":
            body = {"episode_id": episode_id, "voice": bool(opts.get("voice", False))}
            if variant:
                body["variant_id"] = variant
            return await client.download_operation(
                "export_motion",
                cache=self.export_cache,
                filename=filename,
                body=body,
                timeout=max(180, client.timeout),
            )
        body = {"idempotency_key": idempotency_key or f"mio-export-{uuid.uuid4().hex}"}
        if variant:
            body["variant_id"] = variant
        if fmt in {"strip", "slices"}:
            operation = "submit_strip_export"
            body.update(
                episode_id=episode_id,
                fmt="long" if fmt == "strip" else "slices",
                preset=str(opts.get("preset") or "webtoon"),
            )
            quality = opts.get("quality")
            if type(quality) is int and 0 <= quality <= 100:
                body["quality"] = quality
        else:
            operation = "submit_album_export" if fmt == "html" else "submit_portable_export"
            body.update(
                episode_ids=[episode_id],
                title=str(opts.get("title") or "")[:120],
                show_captions=bool(opts.get("show_captions", True)),
                lettered=bool(opts.get("lettered", True)),
            )
            profile = opts.get("image_profile")
            if profile in IMAGE_PROFILES:
                body["image_profile"] = profile
            if fmt == "html":
                body.update(
                    show_prompts=bool(opts.get("show_prompts", False)),
                    signature=str(opts.get("signature") or "")[:120],
                )
                if template_id:
                    body["template_id"] = template_id
                color = opts.get("theme_color")
                if color and isinstance(color, str) and re.fullmatch(r"#[a-fA-F\d]{6}", color):
                    body["theme_color"] = color
            else:
                body["format"] = fmt
        submitted = await client.call(operation, body=body)
        job_id = submitted.get("id") if isinstance(submitted, dict) else None
        if not isinstance(job_id, str) or not job_id:
            raise MioApiError("Mio 未返回导出编号，请检查服务版本", code="bad_export_job")
        if submitted.get("state") != "completed":
            await self.wait_job(client, job_id)
        return await client.download_operation(
            "download_export",
            cache=self.export_cache,
            filename=filename,
            path_params={"job_id": job_id},
            timeout=max(180, client.timeout),
        )

    async def panel_image(
        self,
        client: MioV2Client,
        episode_id: str,
        panel_id: str | None = None,
        *,
        take_id: str | None = None,
        width: int = 1280,
        variant_id: str | None = None,
        filename: str = "mio-panel.png",
    ) -> DownloadedFile:
        if take_id:
            operation = "take_image"
            path_params = {"episode_id": episode_id, "take_id": take_id}
            params = None
        elif panel_id:
            operation = "reading_panel_image"
            path_params = {"episode_id": episode_id, "panel_id": panel_id}
            params = {"width": max(64, min(3200, width)), "variant_id": variant_id}
        else:
            raise MioApiError("请选择要查看的分幕或候选图", code="no_panel")
        file = await client.download_operation(
            operation,
            cache=self.image_cache,
            filename=filename,
            path_params=path_params,
            params=params,
        )
        if file.content_type not in _IMAGE_TYPES:
            raise MioApiError("Mio 没有返回受支持的图片", code="bad_image")
        return file

    async def frames(
        self,
        client: MioV2Client,
        episode_id: str,
        *,
        limit: int = 30,
        variant_id: str | None = None,
        take_fallback: bool = False,
    ) -> FrameBundle:
        episode = await client.call("get_episode", path_params={"episode_id": episode_id})
        if not isinstance(episode, dict) or not isinstance(episode.get("panels"), list):
            raise MioApiError("无法读取 Mio 作品分幕", code="bad_episode")
        panels = sorted(episode["panels"], key=lambda panel: panel["order"])
        takes: dict[str, Any] = {}
        for take in episode.get("takes") or []:
            if take.get("variant_id") != variant_id:
                continue
            if take.get("status") == "adopted":
                takes[take["panel_id"]] = take
            elif take_fallback and take.get("status") == "candidate":
                takes.setdefault(take["panel_id"], take)
        frames: list[Frame] = []
        first_error: MioApiError | None = None
        for number, panel in enumerate(panels, 1):
            if len(frames) >= max(1, min(200, limit)):
                break
            take = takes.get(panel["id"])
            if take is None:
                continue
            caption = (
                "\n".join(str(d["text"]) for d in panel.get("dialogues") or [] if d.get("text"))
                or str(panel.get("description") or "")
            )[:360]
            try:
                file = await self.panel_image(
                    client,
                    episode_id,
                    panel["id"],
                    take_id=take["id"] if take.get("status") == "candidate" else None,
                    variant_id=variant_id,
                    filename=f"mio-panel-{number:03}.png",
                )
            except MioApiError as exc:
                first_error = first_error or exc
                file = None
            frames.append(Frame(number, panel["id"], caption, file))
        if first_error and not any(frame.file for frame in frames):
            raise first_error
        return FrameBundle(str(episode.get("title") or "Mio 绘页")[:120], tuple(frames))

    async def send(self, umo: str, chain: list[Any]) -> bool:
        from astrbot.api.event import MessageChain

        try:
            return bool(await self.context.send_message(umo, MessageChain(chain=chain)))
        except Exception:
            self.logger.warning("Mio chat adapter rejected the delivery")
            return False

    async def send_text(self, umo: str, text: str) -> bool:
        from astrbot.api.message_components import Plain

        return await self.send(umo, [Plain(text)])

    async def send_file(
        self,
        umo: str,
        file: DownloadedFile,
        *,
        note: str = "",
        max_size: int = 50 * 1024**2,
        as_image: bool = False,
    ) -> None:
        from astrbot.api.message_components import File, Image

        if file.size > max_size:
            raise MioApiError(
                f"成品超过当前发送上限（{max_size / 1024**2:g} MB）。"
                "可在控制面板下载，或调整图片档位后重试。",
                code="delivery_file_too_large",
                status=413,
            )
        if not file.path.is_file():
            raise MioApiError("缓存已过期，请重新取件", code="missing_delivery_file")
        component = (
            Image.fromFileSystem(str(file.path))
            if as_image
            else File(name=file.filename, file=str(file.path))
        )
        if not await self.send(umo, [component]):
            raise MioApiError(
                "聊天平台没有接收成品，可从控制面板下载",
                code="delivery_failed",
                status=502,
            )
        if note:
            await self.send_text(umo, note)

    async def send_images(
        self,
        umo: str,
        bundle: FrameBundle,
        *,
        platform: str = "",
        send_mode: str = "auto",
        include_captions: bool = True,
        header: str = "",
        max_size: int = 15 * 1024**2,
        forward_sender_name: str = "Mio 绘页",
        forward_sender_uin: str = "10000",
    ) -> int:
        from astrbot.api.message_components import Image, Node, Nodes, Plain

        if send_mode not in SEND_MODES:
            send_mode = "auto"
        usable = [
            f for f in bundle.frames if f.file and f.file.size <= max_size and f.file.path.is_file()
        ]
        if not usable:
            raise MioApiError(
                "没有可发送的图片。请先采纳候选图，或从控制面板下载超限图片。",
                code="no_images",
                status=409,
            )
        skipped = len(bundle.frames) - len(usable)
        header = header or f"《{bundle.title}》 · {len(bundle.frames)} 幕"

        def pair(frame: Frame) -> list[Any]:
            caption = f"第 {frame.number} 幕"
            if include_captions and frame.caption:
                caption += f"\n{frame.caption}"
            return [Plain(caption), Image.fromFileSystem(str(frame.file.path))]

        forward = send_mode == "forward" or (
            send_mode == "auto" and platform in FORWARD_PLATFORMS and len(usable) > 1
        )
        if forward:
            sender = {"name": forward_sender_name, "uin": forward_sender_uin}
            nodes = [Node(content=[Plain(header)], **sender)]
            nodes += [Node(content=pair(frame), **sender) for frame in usable]
            if await self.send(umo, [Nodes(nodes=nodes)]):
                return await self._image_result(umo, len(usable), skipped)
        sent = 0
        for offset in range(0, len(usable), _MAX_BATCH_IMAGES):
            batch = usable[offset : offset + _MAX_BATCH_IMAGES]
            if send_mode != "separate" and len(batch) > 1:
                chain = [Plain(header)] if offset == 0 else []
                chain.extend(component for frame in batch for component in pair(frame))
                if await self.send(umo, chain):
                    sent += len(batch)
                    continue
            if offset == 0:
                await self.send_text(umo, header)
            for frame in batch:
                if await self.send(umo, pair(frame)):
                    sent += 1
                else:
                    skipped += 1
        return await self._image_result(umo, sent, skipped)

    async def _image_result(self, umo: str, sent: int, skipped: int) -> int:
        if skipped:
            message = (
                f"已发送 {sent} 张，{skipped} 张未能发送（超限或平台拒收），可在控制面板下载。"
            )
            if not sent or not await self.send_text(umo, message):
                raise MioApiError(message, code="delivery_failed", status=502)
        return sent
