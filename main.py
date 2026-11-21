"""AstrBot commands for Mio Studio OpenAPI v2."""

from __future__ import annotations

import re
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:  # GreedyStr captures the rest of the message as one argument
    from astrbot.core.star.filter.command import GreedyStr
except Exception:  # pragma: no cover - very old cores
    GreedyStr = str  # type: ignore[misc,assignment]
try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover
    get_astrbot_data_path = None  # type: ignore[assignment]

from .core.config import ConfigError
from .core.governance import Actor, GovernanceError
from .core.page_api import PageApi
from .core.service import FORMATS, MioStudioService, ServiceError
from .core.textfmt import HELP_TEXT, format_catalog, format_jobs, format_list, format_task
from .core.v2_client import MioApiError

PLUGIN_NAME = "astrbot_plugin_mio_studio"
_TOKEN_SPLIT = re.compile(r"[\s，、]+")


class MioStudioPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}
        if get_astrbot_data_path is not None:
            data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        else:  # pragma: no cover
            data_dir = Path("data") / "plugin_data" / PLUGIN_NAME
        data_dir.mkdir(parents=True, exist_ok=True)
        self.service = MioStudioService(context, self.config, data_dir, logger)
        self.page_api = PageApi(PLUGIN_NAME, self.service, logger)
        self.page_api.register(context)

    async def initialize(self) -> None:
        await self.service.start()

    async def terminate(self) -> None:
        await self.service.close()

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _actor(event: AstrMessageEvent) -> Actor:
        return Actor(
            id=str(event.get_sender_id() or ""),
            name=str(event.get_sender_name() or ""),
            session=event.unified_msg_origin,
            platform=str(event.get_platform_name() or ""),
            is_admin=event.is_admin(),
        )

    @staticmethod
    def _tokens(text: str | None) -> list[str]:
        return [t for t in _TOKEN_SPLIT.split(str(text or "").strip()) if t]

    @staticmethod
    def _err(exc: Exception) -> str:
        if isinstance(exc, (ConfigError, ServiceError, GovernanceError)):
            return f"❌ {exc.message}"
        if isinstance(exc, MioApiError):
            return f"❌ Mio：{exc.message}"
        logger.error("[mio_studio] 聊天操作失败：%s", type(exc).__name__)
        return "❌ Mio 操作遇到内部错误，请稍后重试。"

    async def _list(self, event: AstrMessageEvent, kinds: tuple[str, ...]):
        try:
            cat = await self.service.catalog(actor=self._actor(event), force=True)
        except Exception as exc:
            return self._err(exc)
        defaults = self.service.catalog_defaults()
        if len(kinds) == 1:
            return format_list(kinds[0], cat.get(kinds[0]) or [], defaults=defaults)
        return format_catalog(cat, kinds, defaults)

    # --------------------------------------------------------------- commands
    @filter.command("mio help", alias={"mio"})
    async def cmd_help(self, event: AstrMessageEvent):
        """Mio 绘页指令帮助"""
        yield event.plain_result(HELP_TEXT)

    @filter.command("mio ping")
    async def cmd_ping(self, event: AstrMessageEvent):
        """检查与 Mio 的连接"""
        try:
            actor = self._actor(event)
            status = await self.service.status(actor=actor)
            if actor.session:
                self.service.store.seen_session(actor.session, actor.platform, actor.name)
        except Exception as exc:
            yield event.plain_result(self._err(exc))
            return
        if status.get("ok"):
            text = f"✅ Mio {status.get('version') or ''} 在线（开放 API v2）"
        else:
            text = f"❌ Mio 不可用：{status.get('error')}"
        tasks = status.get("tasks") or {}
        text += f"\n本插件任务：{tasks.get('total', 0)} 个，进行中 {tasks.get('active', 0)} 个"
        yield event.plain_result(text)

    @filter.command("mio ls")
    async def cmd_ls(self, event: AstrMessageEvent, target: str = ""):
        """列出工作流 / 分幕 / 预设 / 通道 / 模板（支持 /mio ls [wf|sb|ps|ch|tpl]）"""
        target = (target or "").strip().lower()
        if not target:
            kinds = ("workflows", "storyboards", "presets")
        elif target in ("wf", "workflow", "workflows", "工作流"):
            kinds = ("workflows",)
        elif target in ("sb", "storyboard", "storyboards", "分幕", "分镜"):
            kinds = ("storyboards",)
        elif target in ("ps", "preset", "presets", "预设"):
            kinds = ("presets",)
        elif target in ("ch", "channel", "channels", "通道"):
            kinds = ("channels",)
        elif target in ("tpl", "layout", "layouts", "template", "templates", "模板"):
            kinds = ("layouts",)
        else:
            yield event.plain_result(
                f"❌ 未知类型「{target}」，支持：\n"
                "• /mio ls wf  — 列出工作流\n"
                "• /mio ls sb  — 列出分幕\n"
                "• /mio ls ps  — 列出预设\n"
                "• /mio ls ch  — 列出通道\n"
                "• /mio ls tpl — 列出导出模板\n"
                "或直接使用 /mio ls 列出常用资源。"
            )
            return
        yield event.plain_result(await self._list(event, kinds))

    @filter.command("mio use")
    async def cmd_use(self, event: AstrMessageEvent, args: GreedyStr):
        """/mio use <工作流#> <分幕#> <预设#[,预设#]> [通道#] 创建并开始生成"""
        tokens = self._tokens(args)
        if not 1 <= len(tokens) <= 4:
            yield event.plain_result(
                "用法：/mio use <工作流#> <分幕#> <预设#[,预设#]> [通道#]\n或 /mio use <分幕#>（使用面板默认的工作流 / 预设 / 通道）\n先用 /mio ls 查看编号。"
            )
            return
        if len(tokens) == 1:
            workflow, storyboard, presets, channel = None, tokens[0], [], None
        elif len(tokens) == 2:
            workflow, storyboard, presets, channel = tokens[0], tokens[1], [], None
        else:
            workflow, storyboard, presets = (
                tokens[0],
                tokens[1],
                [p for p in tokens[2].split(",") if p],
            )
            channel = tokens[3] if len(tokens) > 3 else None
        try:
            actor = self._actor(event)
            selection = await self.service.resolve_selection(
                workflow, storyboard, presets, channel, actor=actor
            )
            record = await self.service.create_task(selection, actor=actor)
            view = self.service.merge_view(record, None)
        except Exception as exc:
            yield event.plain_result(self._err(exc))
            return
        sel = record.get("selection") or {}
        position = view.get("queuePosition")
        submitted = f"，当前排第 {position} 位" if position else ""
        lines = [f"🚀 任务 #{record['num']} 已受理{submitted}", f"《{record.get('title')}》"]
        if sel.get("workflowTitle"):
            lines.append(f"工作流：{sel['workflowTitle']}")
        if sel.get("storyboardTitle"):
            lines.append(f"分幕：{sel['storyboardTitle']}")
        if sel.get("presetTitles"):
            lines.append("预设：" + " + ".join(sel["presetTitles"]))
        if sel.get("channelTitle"):
            lines.append(f"通道：{sel['channelTitle']}")
        lines.append(f"进度：/mio status {record['num']}　成品：/mio get {record['num']}")
        yield event.plain_result("\n".join(lines))

    @filter.command("mio jobs")
    async def cmd_jobs(self, event: AstrMessageEvent):
        """我的任务列表"""
        try:
            views, others = await self.service.task_views(
                actor=self._actor(event), limit=12, force=True
            )
        except Exception as exc:
            yield event.plain_result(self._err(exc))
            return
        yield event.plain_result(format_jobs(views, others=others))

    @filter.command("mio status")
    async def cmd_status(self, event: AstrMessageEvent, num: str = ""):
        """查看任务进度：/mio status [#]"""
        try:
            actor = self._actor(event)
            record = self.service.find_record(num, actor=actor)
            view = await self.service.task_view(record, actor=actor)
        except Exception as exc:
            yield event.plain_result(self._err(exc))
            return
        yield event.plain_result(format_task(view))

    @filter.command("mio get")
    async def cmd_get(self, event: AstrMessageEvent, args: GreedyStr):
        """下载成品：/mio get [#] [html|zip|pdf|strip|slices|motion|img] [模板#]"""
        tokens = self._tokens(args)
        formats = " / ".join((*FORMATS, "img"))
        num: str | None = None
        fmt: str | None = None
        layout: str | None = None
        for token in tokens:
            low = token.lower()
            if low in FORMATS or low in ("img", "image", "images", "图片"):
                if fmt is not None:
                    yield event.plain_result("❌ 每次取件只能选择一种导出格式")
                    return
                fmt = "img" if low in ("img", "image", "images", "图片") else low
            elif num is None and (token.lstrip("#").isdigit() or token.startswith("assembly-")):
                num = token
            elif layout is None:
                if low.isascii() and low.isalpha() and len(low) <= 6:
                    yield event.plain_result(
                        f"❌ 未知格式「{token}」，支持 {formats}；导出模板请用 /mio ls tpl 的编号"
                    )
                    return
                layout = token
            else:
                yield event.plain_result("用法：/mio get [任务号] [导出格式] [模板号]")
                return
        try:
            actor = self._actor(event)
            record = self.service.find_record(num, actor=actor)
            view = await self.service.task_view(record, actor=actor)
            if view.get("statusKey") == "missing":
                raise ServiceError(f"任务 #{record['num']} 已从 Mio 队列移除")
            if not (view.get("episode_id") or view.get("albumId")):
                raise ServiceError(f"任务 #{record['num']} 还没有关联画册")
            if view.get("done", 0) == 0:
                raise ServiceError(
                    f"任务 #{record['num']} 还没有生成完成的分幕（{view.get('statusKey')}）"
                )
            if fmt == "img":
                yield event.plain_result(
                    f"⏳ 正在发送任务 #{record['num']} 的 {view.get('done', 0)} 幕图片…"
                )
                count = await self.service.send_images(
                    record, actor.session, actor=actor, platform=actor.platform
                )
                if count == 0:
                    yield event.plain_result("没有可发送的图片")
                return
            note = ""
            if view.get("statusKey") != "complete":
                note = f"（任务尚未全部完成，导出当前已生成的 {view.get('done', 0)}/{view.get('total', 0)} 幕）"
            yield event.plain_result(
                f"⏳ 正在导出任务 #{record['num']}（{fmt or self.service.export_format(actor.session)}）… {note}"
            )
            await self.service.send_export(
                record, actor.session, actor=actor, fmt=fmt, layout=layout
            )
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    async def _action(self, event: AstrMessageEvent, num: str, action: str):
        if not num:
            return "请提供任务编号，例如 /mio pause 1。"
        try:
            actor = self._actor(event)
            record = self.service.find_record(num, actor=actor)
            return await self.service.task_action(record, action, actor=actor)
        except Exception as exc:
            return self._err(exc)

    @filter.command("mio pause")
    async def cmd_pause(self, event: AstrMessageEvent, num: str = ""):
        """暂停任务：/mio pause #"""
        yield event.plain_result(await self._action(event, num, "pause"))

    @filter.command("mio resume")
    async def cmd_resume(self, event: AstrMessageEvent, num: str = ""):
        """继续 / 重试任务：/mio resume #"""
        yield event.plain_result(await self._action(event, num, "resume"))

    @filter.command("mio cancel")
    async def cmd_cancel(self, event: AstrMessageEvent, num: str = ""):
        """停止任务：/mio cancel #"""
        yield event.plain_result(await self._action(event, num, "cancel"))

    @filter.command("mio rm")
    async def cmd_rm(self, event: AstrMessageEvent, num: str = ""):
        """移除任务记录：/mio rm #"""
        yield event.plain_result(await self._action(event, num, "remove"))
