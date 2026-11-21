"""AstrBot chat, wizard, and AI handlers for Mio Studio."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from weakref import ReferenceType, ref

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.agent.tool import ToolSet
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core.card_renderer import CardRenderer
from .core.commands import HELP_WAVE3, CommandArgs, CommandError, parse_command
from .core.config import ConfigError
from .core.governance import Actor, GovernanceError
from .core.llm_tools import LLMTools
from .core.page_api import PageApi
from .core.service import MioStudioService, ServiceError
from .core.textfmt import HELP_TEXT, clip, format_catalog, format_jobs, format_list, format_task
from .core.v2_client import MioApiError
from .core.wizard import WizardManager

PLUGIN_NAME = "astrbot_plugin_mio_studio"
_WORK_SHELF_TTL = 300.0
_WORK_SHELF_MAX = 128
_DEFAULT_CATALOG = ("workflows", "storyboards", "presets")
_LLM_FIELDS = {
    "mio_list_resources",
    "mio_draw_comic_from_idea",
    "mio_assemble_workshop_task",
    "mio_trial_image",
    "mio_task_status_and_control",
    "mio_export_and_send",
}
_LLM_CREATOR_FIELDS = {
    "mio_draw_comic_from_idea",
    "mio_assemble_workshop_task",
    "mio_trial_image",
}
_MODE_LABELS = {
    "images": "逐幕发图",
    "strip": "发送长条漫",
    "export": "发送导出文件",
    "summary": "发送完成摘要",
    "qa_then_send": "质检并发图",
    "silent": "静默",
}
_active_wizard: ReferenceType[WizardManager] | None = None


def _actor(event: AstrMessageEvent) -> Actor:
    return Actor(
        id=str(event.get_sender_id() or ""),
        name=str(event.get_sender_name() or ""),
        session=event.unified_msg_origin,
        platform=str(event.get_platform_name() or ""),
        is_admin=event.is_admin(),
    )


def _wizard_text(event: AstrMessageEvent) -> str:
    value = event.message_str.strip()
    # AstrBot can remove the wake prefix before it evaluates plugin filters.
    return "" if value.casefold().split(maxsplit=1)[:1] == ["mio"] else value


class _HelpOnly(filter.CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        # Bare aliases are normally prefix matches, so "mio" must be exact here.
        text = " ".join(event.get_message_str().split())
        return text in {"mio", "mio help"} or text.startswith("mio help ")


class _WizardInput(filter.CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        manager = _active_wizard() if _active_wizard else None
        if manager is None:
            return False
        actor = _actor(event)
        return manager.accepts(actor.scoped_id, actor.session, _wizard_text(event))


class MioStudioPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self.service = MioStudioService(context, self.config, data_dir, logger)
        self.page_api = PageApi(PLUGIN_NAME, self.service, logger)
        self.page_api.register(context)
        self.cards = CardRenderer(data_dir / "cards")
        self.llm = LLMTools(self.service)
        self.wizard = WizardManager()
        self._work_shelves: OrderedDict[tuple[str, str, str], tuple[float, list[dict]]] = (
            OrderedDict()
        )

    async def initialize(self) -> None:
        global _active_wizard
        await self.service.start()
        _active_wizard = ref(self.wizard)

    async def terminate(self) -> None:
        global _active_wizard
        if _active_wizard and _active_wizard() is self.wizard:
            _active_wizard = None
        self.wizard.close()
        self._work_shelves.clear()
        await self.service.close()

    @staticmethod
    def _err(exc: Exception) -> str:
        if isinstance(exc, (CommandError, ConfigError, ServiceError, GovernanceError)):
            return f"❌ {exc.message}"
        if isinstance(exc, MioApiError):
            return f"❌ Mio：{exc.message}"
        logger.error("[mio_studio] 聊天操作失败：%s", type(exc).__name__)
        return "❌ Mio 操作遇到内部错误，请稍后重试。"

    async def _card(self, event, render, fallback: str, *args, **kwargs):
        mode = self.service.cfg("chat", "render_mode", "auto")
        if mode == "text":
            return event.plain_result(fallback)
        theme = self.service.cfg("chat", "card_theme", "paper")
        path = await asyncio.to_thread(render, *args, mode=mode, theme=theme, **kwargs)
        return event.image_result(str(path)) if path else event.plain_result(fallback)

    def _accepted(self, record: dict) -> str:
        view = self.service.merge_view(record, None)
        number = record["num"]
        position = view.get("queuePosition")
        queue = f" · 本地队列第 {position} 位" if position else ""
        return f"已受理任务 #{number}{queue}\n《{record['title']}》\n进度 /mio status {number} · 取件 /mio get {number}"

    async def _assemble(self, actor: Actor, args: CommandArgs):
        return await self.service.resolve_selection(
            args.workflow,
            args.storyboard,
            list(args.presets),
            args.channel,
            actor=actor,
            instance_id=args.instance_id,
        )

    @staticmethod
    def _preview_text(preview: dict) -> str:
        panels = preview.get("panels") or []
        lines = [f"装配预览 · 《{clip(preview.get('title'), 40)}》 · {len(panels)} 幕"]
        for number, panel in enumerate(panels[:4], 1):
            missing = [
                clip(var["key"], 30)
                for var in panel.get("variables", [])
                if var.get("status") in {"missing", "empty", "cycle", "depth_limit"}
                and var.get("key")
            ][:8]
            lines.append(f"{number}. {clip(panel.get('title') or panel.get('positive'), 76)}")
            if missing:
                lines.append("   待确认：" + "、".join(missing))
        if len(panels) > 4:
            lines.append("其余分幕可在控制面板逐幕查看。")
        lines.append("预览未提交出图。确认后可用 /mio use 开画。")
        return "\n".join(lines)

    @filter.command("mio help", alias={"mio"})
    @filter.custom_filter(_HelpOnly)
    async def cmd_help(self, event: AstrMessageEvent, args: GreedyStr):
        """Mio 指令帮助。"""
        try:
            parse_command("help", args)
            fallback = HELP_TEXT + "\n\n" + HELP_WAVE3
            yield await self._card(event, self.cards.help, fallback)
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio ping")
    async def cmd_ping(self, event: AstrMessageEvent, args: GreedyStr):
        """检查 Mio 工作室连接。"""
        try:
            actor = _actor(event)
            selected = parse_command("ping", args)
            status = await self.service.status(actor=actor, instance_id=selected.instance_id)
            if actor.session:
                self.service.store.seen_session(actor.session, actor.platform, actor.name)
            if status.get("ok"):
                text = f"Mio {status.get('version') or ''} 在线（开放 API v2）"
            else:
                text = f"Mio 不可用：{status.get('error')}"
            tasks = status.get("tasks") or {}
            yield event.plain_result(
                f"{text}\n本插件任务：{tasks.get('total', 0)} 个，进行中 {tasks.get('active', 0)} 个"
            )
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio ls")
    async def cmd_ls(self, event: AstrMessageEvent, args: GreedyStr):
        """浏览资源并显示创作用编号。"""
        try:
            selected = parse_command("ls", args)
            kinds = (selected.target,) if selected.target else _DEFAULT_CATALOG
            catalog = await self.service.catalog(
                actor=_actor(event), instance_id=selected.instance_id
            )
            defaults = self.service.catalog_defaults()
            trimmed = {kind: (catalog.get(kind) or [])[:20] for kind in kinds}
            overflow = any(len(catalog.get(kind) or []) > 20 for kind in kinds)
            fallback = (
                format_list(kinds[0], trimmed[kinds[0]], defaults=defaults)
                if len(kinds) == 1
                else format_catalog(trimmed, kinds, defaults)
            )
            if overflow:
                fallback += "\n\n聊天最多列出每类 20 项；完整目录可在控制面板查看。"
            yield await self._card(event, self.cards.catalog, fallback, catalog, kinds)
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio preview")
    async def cmd_preview(self, event: AstrMessageEvent, args: GreedyStr):
        """预览工作坊分镜、预设和缺失变量。"""
        try:
            actor = _actor(event)
            selection = await self._assemble(actor, parse_command("preview", args))
            preview = await self.service.preview(selection, actor=actor)
            yield event.plain_result(self._preview_text(preview))
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio use")
    async def cmd_use(self, event: AstrMessageEvent, args: GreedyStr):
        """用指定工作流、分镜和预设装配并生成作品。"""
        try:
            actor = _actor(event)
            selection = await self._assemble(actor, parse_command("use", args))
            record = await self.service.create_task(selection, actor=actor)
            yield event.plain_result(self._accepted(record))
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio draw", alias={"mio 画"})
    async def cmd_draw(self, event: AstrMessageEvent, args: GreedyStr):
        """用一句灵感创作分镜并提交出图。"""
        try:
            selected = parse_command("draw", args)
            record = await self.service.create_draw(
                selected.idea,
                actor=_actor(event),
                instance_id=selected.instance_id,
                series=selected.series,
                style=selected.style,
                candidates=selected.candidates,
                layout=selected.layout,
            )
            yield event.plain_result(self._accepted(record))
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio trial", alias={"mio 试画"})
    async def cmd_trial(self, event: AstrMessageEvent, args: GreedyStr):
        """快速生成一张图，完成后发送到当前会话。"""
        try:
            selected = parse_command("trial", args)
            record = await self.service.create_trial(
                selected.prompt,
                selected.workflow,
                actor=_actor(event),
                instance_id=selected.instance_id,
            )
            yield event.plain_result(self._accepted(record))
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio jobs")
    async def cmd_jobs(self, event: AstrMessageEvent, args: GreedyStr):
        """列出此会话中可查看的任务。"""
        try:
            parse_command("jobs", args)
            views, others = await self.service.task_views(actor=_actor(event), limit=8)
            yield await self._card(
                event, self.cards.tasks, format_jobs(views, others=others), views
            )
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio status")
    async def cmd_status(self, event: AstrMessageEvent, args: GreedyStr):
        """查看任务进度、状态和交付入口。"""
        try:
            actor = _actor(event)
            selected = parse_command("status", args)
            record = self.service.find_record(selected.task, actor=actor)
            view = await self.service.task_view(record, actor=actor)
            fallback = format_task(view)
            if view.get("error") or view.get("kind") == "qa":
                yield event.plain_result(fallback)
                return
            previews = []
            if (
                view.get("episode_id")
                and view.get("done")
                and self.service.cfg("chat", "render_mode", "auto") != "text"
            ):
                try:
                    client = self.service.instances.get(record["instance_id"])
                    async with asyncio.timeout(3):
                        bundle = await self.service.delivery.frames(
                            client, view["episode_id"], limit=3, take_fallback=True
                        )
                    previews = [item.file.path for item in bundle.frames if item.file]
                except (MioApiError, TimeoutError):
                    pass
            yield await self._card(event, self.cards.tasks, fallback, [view], previews=previews)
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio get")
    async def cmd_get(self, event: AstrMessageEvent, args: GreedyStr):
        """向当前会话发送任务的图片或导出文件。"""
        try:
            actor = _actor(event)
            selected = parse_command("get", args)
            record = self.service.find_record(selected.task, actor=actor)
            view = await self.service.task_view(record, actor=actor)
            if not view.get("episode_id") or not view.get("done"):
                raise ServiceError("这条任务还没有可取件的分幕。可以用 /mio status 查看进度。")
            number = view["num"]
            if selected.format == "img":
                yield event.plain_result(f"正在发送任务 #{number} 的分幕图片…")
                await self.service.send_images(
                    record, actor.session, actor=actor, platform=actor.platform
                )
            else:
                fmt = selected.format or self.service.export_format(actor.session)
                partial = "（含当前已生成的分幕）" if view.get("statusKey") != "complete" else ""
                yield event.plain_result(f"正在导出任务 #{number}（{fmt}）{partial}…")
                await self.service.send_export(
                    record, actor.session, actor=actor, fmt=fmt, layout=selected.layout
                )
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    def _remember_works(self, actor: Actor, instance: str, works: list[dict]) -> None:
        key = (actor.scoped_id, actor.session, instance)
        self._work_shelves[key] = (time.monotonic() + _WORK_SHELF_TTL, works)
        self._work_shelves.move_to_end(key)
        while len(self._work_shelves) > _WORK_SHELF_MAX:
            self._work_shelves.popitem(last=False)

    async def _gallery_cards(self, instance_id: str, works: list[dict]) -> list[dict]:
        client = self.service.instances.get(instance_id)
        cache = self.service.delivery.image_cache

        async def thumb(item: dict) -> dict:
            entry = dict(item)
            asset_id = item.get("cover_asset_id")
            if asset_id:
                try:
                    async with asyncio.timeout(2):
                        file = await client.download_operation(
                            "get_asset",
                            cache=cache,
                            path_params={"asset_id": asset_id},
                            params={"thumb": True},
                        )
                    if file.content_type in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                        entry["cover_path"] = file.path
                except (MioApiError, TimeoutError, ValueError):
                    pass
            return entry

        return await asyncio.gather(*(thumb(item) for item in works[:6])) + works[6:]

    @filter.command("mio works", alias={"mio 作品"})
    async def cmd_works(self, event: AstrMessageEvent, args: GreedyStr):
        """查找作品并获取导出编号。"""
        try:
            actor = _actor(event)
            selected = parse_command("works", args)
            instance = self.service.route(actor, selected.instance_id)
            items = await self.service.works(
                actor=actor,
                instance_id=instance.id,
                query=selected.keyword,
                limit=6,
            )
            works = [{**item, "num": number} for number, item in enumerate(items, 1)]
            self._remember_works(actor, instance.id, works)
            if not works:
                yield event.plain_result("暂未找到作品。换个关键词，或用 /mio draw 开始创作。")
                return
            fallback = "\n".join(
                ["作品画廊"]
                + [f"{item['num']}. {clip(item.get('title'), 40)}" for item in works]
                + ["使用 /mio export <编号> [格式] 取件；编号在本次会话保留 5 分钟。"]
            )
            gallery = (
                await self._gallery_cards(instance.id, works)
                if self.service.cfg("chat", "render_mode", "auto") != "text"
                else works
            )
            yield await self._card(event, self.cards.gallery, fallback, gallery)
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio export")
    async def cmd_export(self, event: AstrMessageEvent, args: GreedyStr):
        """导出一部已在作品画廊中列出的作品。"""
        try:
            actor = _actor(event)
            selected = parse_command("export", args)
            instance = self.service.route(actor, selected.instance_id)
            key = (actor.scoped_id, actor.session, instance.id)
            expires, works = self._work_shelves.get(key, (0, []))
            if expires <= time.monotonic():
                self._work_shelves.pop(key, None)
                raise ServiceError("作品编号已过期。请先运行 /mio works，再使用列表中的编号。")
            work = self.service.pick(works, selected.work, label="作品")
            yield event.plain_result(f"正在导出《{clip(work.get('title'), 40)}》…")
            await self.service.send_work(
                work,
                actor.session,
                actor=actor,
                fmt=selected.format,
                layout=selected.layout,
            )
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    async def _action(self, event: AstrMessageEvent, raw: str, command: str) -> str:
        try:
            actor = _actor(event)
            args = parse_command(command, raw)
            record = self.service.find_record(args.task, actor=actor)
            action = "remove" if command == "rm" else command
            return await self.service.task_action(record, action, actor=actor)
        except Exception as exc:
            return self._err(exc)

    @filter.command("mio pause")
    async def cmd_pause(self, event: AstrMessageEvent, args: GreedyStr):
        """暂停自己的任务。"""
        yield event.plain_result(await self._action(event, args, "pause"))

    @filter.command("mio resume")
    async def cmd_resume(self, event: AstrMessageEvent, args: GreedyStr):
        """继续或重试自己的任务。"""
        yield event.plain_result(await self._action(event, args, "resume"))

    @filter.command("mio cancel")
    async def cmd_cancel(self, event: AstrMessageEvent, args: GreedyStr):
        """停止自己的任务。"""
        yield event.plain_result(await self._action(event, args, "cancel"))

    @filter.command("mio rm")
    async def cmd_rm(self, event: AstrMessageEvent, args: GreedyStr):
        """移除自己的本地任务历史。"""
        yield event.plain_result(await self._action(event, args, "rm"))

    @filter.command("mio redraw")
    async def cmd_redraw(self, event: AstrMessageEvent, args: GreedyStr):
        """重画一个分幕，可提供新提示词。"""
        try:
            actor = _actor(event)
            selected = parse_command("redraw", args)
            record = self.service.find_record(selected.task, actor=actor)
            result = await self.service.redraw(
                record, selected.panel, selected.prompt or None, actor=actor
            )
            yield event.plain_result(
                self._accepted(result)
                if isinstance(result, dict) and result.get("num")
                else f"已提交任务 #{record['num']} 的第 {selected.panel} 幕重画。"
            )
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio pick")
    async def cmd_pick(self, event: AstrMessageEvent, args: GreedyStr):
        """采纳一个分幕的候选图。"""
        try:
            actor = _actor(event)
            selected = parse_command("pick", args)
            record = self.service.find_record(selected.task, actor=actor)
            await self.service.pick_take(record, selected.panel, selected.candidate, actor=actor)
            yield event.plain_result(
                f"任务 #{record['num']} · 第 {selected.panel} 幕已采用候选图 {selected.candidate}。"
            )
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio qa")
    async def cmd_qa(self, event: AstrMessageEvent, args: GreedyStr):
        """运行质检并查看每幕质量反馈。"""
        try:
            actor = _actor(event)
            selected = parse_command("qa", args)
            record = self.service.find_record(selected.task, actor=actor)
            result = await self.service.qa(record, actor=actor)
            yield event.plain_result(self._accepted(result))
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio quota")
    async def cmd_quota(self, event: AstrMessageEvent, args: GreedyStr):
        """查看今天的额度、冷却和待执行任务数。"""
        try:
            parse_command("quota", args)
            quota = self.service.quota(_actor(event))
            left = quota.get("remaining")
            cloud_limit = self.service.cfg("governance", "cloud_daily_limit", 3)
            cloud_left = max(0, cloud_limit - quota.get("cloud_used", 0)) if cloud_limit else None
            cooldown = quota.get("cooldown_remaining", 0)
            queue = quota.get("queue_length", 0)
            lines = [
                f"今日创作剩余：{left if left is not None else '不限'}",
                f"云端剩余：{cloud_left if cloud_left is not None else '不限'}",
                f"冷却：{cooldown} 秒 · 本地待执行：{queue}",
            ]
            yield event.plain_result("\n".join(lines))
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.on_llm_request(priority=-10)
    async def hide_unavailable_mio_tools(self, event: AstrMessageEvent, request: ProviderRequest):
        """Scope the advertised tools to this request without mutating AstrBot's registry."""
        if not request.func_tool:
            return
        actor = _actor(event)
        role = self.service.governance.role(actor)
        if role is None or self.service.cfg("chat", "llm_tools_enabled", False) is not True:
            hidden = _LLM_FIELDS
        elif role == "viewer":
            hidden = _LLM_CREATOR_FIELDS
        else:
            return
        remaining = [tool for tool in request.func_tool.tools if tool.name not in hidden]
        request.func_tool = ToolSet(tools=remaining) if remaining else None

    @filter.llm_tool(name="mio_list_resources")
    async def llm_list_resources(
        self, event: AstrMessageEvent, kind="workflows", query="", instance_id=""
    ):
        """查询聊天会话可调用的 Mio 创作资源；先查目录再引用实际 ID。

        Args:
            kind(string): workflows、storyboards、presets、channels、layouts、kits 或 works。
            query(string): 可选关键词，可留空。
            instance_id(string): 可选 Mio 实例 ID；留空选择会话默认。
        """
        return await self.llm.list_resources(_actor(event), kind, query, instance_id)

    @filter.llm_tool(name="mio_draw_comic_from_idea")
    async def llm_draw(
        self,
        event: AstrMessageEvent,
        idea,
        series="",
        style="",
        candidates=1,
        layout="",
        instance_id="",
    ):
        """把用户明确要求生成的灵感提交为漫画。后台异步出图，不要承诺即时完成。

        Args:
            idea(string): 用户的一句话灵感，1–1000 字。
            series(string): 可选自有系列 ID；留空建立新系列。
            style(string): 可选风格预设 ID。
            candidates(number): 每幕候选数，1 到 16 的整数，默认 1。
            layout(string): 可选导出模板 ID；留空交付条漫。
            instance_id(string): 可选 Mio 实例 ID。
        """
        return await self.llm.draw(
            _actor(event), idea, series, style, candidates, layout, instance_id
        )

    @filter.llm_tool(name="mio_assemble_workshop_task")
    async def llm_assemble(
        self,
        event: AstrMessageEvent,
        storyboard,
        workflow="",
        presets="",
        channel="",
        preview=True,
        instance_id="",
    ):
        """用工作流、分镜和预设装配作品。默认只预览，用户明确要求生成时才关闭预览。

        Args:
            storyboard(string): 已知的分镜 ID。
            workflow(string): 可选工作流 ID，不与云端图像通道同时使用。
            presets(string): 以逗号分隔的预设 ID，留空使用会话设置。
            channel(string): 可选图像通道 ID。
            preview(boolean): 是否只预览，不消耗算力。默认为 true。
            instance_id(string): 可选 Mio 实例 ID。
        """
        return await self.llm.assemble(
            _actor(event), storyboard, workflow, presets, channel, preview, instance_id
        )

    @filter.llm_tool(name="mio_trial_image")
    async def llm_trial(self, event: AstrMessageEvent, prompt, workflow="", instance_id=""):
        """按用户要求以指定工作流试画一张，后台完成后发至当前会话。

        Args:
            prompt(string): 试画提示词，1–2400 字。
            workflow(string): 可选工作流 ID，留空使用默认配置。
            instance_id(string): 可选 Mio 实例 ID。
        """
        return await self.llm.trial(_actor(event), prompt, workflow, instance_id)

    @filter.llm_tool(name="mio_task_status_and_control")
    async def llm_task(self, event: AstrMessageEvent, task="", action="status", panel=0, prompt=""):
        """查询或控制当前用户有权管理的任务。收到明确请求才执行暂停、继续、取消或重画。

        Args:
            task(string): 任务编号；仅查询可留空以查看最近任务。
            action(string): status、pause、resume、cancel 或 redraw；默认 status。
            panel(number): 重画时必填，从 1 开始的分幕编号。
            prompt(string): 重画可选的新提示词。
        """
        return await self.llm.task(_actor(event), task, action, panel, prompt)

    @filter.llm_tool(name="mio_export_and_send")
    async def llm_export(
        self, event: AstrMessageEvent, task="", work="", fmt="", layout="", instance_id=""
    ):
        """把用户请求的单件任务或历史作品按指定格式导出并发送到当前会话。

        Args:
            task(string): 任务编号；和 work 只填一个。
            work(string): 作品目录返回的 ID；和 task 只填一个。
            fmt(string): html、pdf、zip、strip、slices、motion 或 img（任务专用）。留空使用默认格式。
            layout(string): 可选导出模板 ID。
            instance_id(string): 可选作品或任务所属实例 ID。
        """
        return await self.llm.export_and_send(_actor(event), task, work, fmt, layout, instance_id)

    @filter.command("mio mode")
    async def cmd_mode(self, event: AstrMessageEvent, args: GreedyStr):
        """设置当前会话的自动交付方式。"""
        try:
            actor = _actor(event)
            selected = parse_command("mode", args)
            self.service.governance.require(actor, "read")
            if not actor.is_admin and event.get_group_id():
                try:
                    group = await event.get_group()
                except Exception:
                    group = None
                if group and actor.id and actor.id == str(group.group_owner):
                    actor = replace(actor, is_session_admin=True)
            self.service.set_session_policy(actor, actor.session, {"notify_mode": selected.mode})
            yield event.plain_result(f"当前会话交付方式：{_MODE_LABELS[selected.mode]}。")
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.command("mio wizard", alias={"mio 向导"})
    async def cmd_wizard(self, event: AstrMessageEvent, args: GreedyStr):
        """通过三步向导选择分镜、工作流和预设。"""
        try:
            actor = _actor(event)
            self.service.governance.require(actor, "create")
            selected = parse_command("wizard", args)
            instance = self.service.route(actor, selected.instance_id)
            catalog = await self.service.catalog(actor=actor, instance_id=instance.id)
            reply = self.wizard.start(
                actor.scoped_id, actor.session, catalog, instance_id=instance.id
            )
            yield event.plain_result(reply.text)
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    @filter.custom_filter(_WizardInput, priority=-10)
    async def wizard_reply(self, event: AstrMessageEvent):
        """处理正在创作的用户提交的有效向导选项。"""
        actor = _actor(event)
        participation = self.wizard.consume(actor.scoped_id, actor.session, _wizard_text(event))
        if not participation:
            return
        # A stopped AstrBot pipeline won't send a yielded result. Send the
        # handled reply first, then stop it from turning into a separate LLM turn.
        event.should_call_llm(False)
        selection = participation.selection
        message = participation.text
        try:
            if selection:
                resolved = await self.service.resolve_selection(
                    selection.workflow,
                    selection.storyboard,
                    list(selection.presets),
                    None,
                    actor=actor,
                    instance_id=selection.instance_id,
                )
                record = await self.service.create_task(resolved, actor=actor)
                message = self._accepted(record)
        except Exception as exc:
            message = self._err(exc)
        try:
            await event.send(event.plain_result(message))
        finally:
            event.stop_event()
