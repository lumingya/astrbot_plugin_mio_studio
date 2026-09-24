"""AstrBot plugin: Mio 绘页 (astrbot_plugin_mio_studio) v2.

Chat commands drive a locally deployed Mio · 绘页 (comfy-comic-studio):
list numbered resources (workflows / storyboards / presets), assemble and
start a production task with ``/mio_use``, follow its progress and download
the finished album in the export format and layout configured in the
standalone control panel.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

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

from .core.mio_client import MioError
from .core.page_api import PageApi
from .core.service import FORMATS, MioStudioService, ServiceError
from .core.textfmt import HELP_TEXT, format_catalog, format_jobs, format_list, format_task

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
        self.service.reload_config()
        self.service.ensure_poller()
        if not self.service.client.configured:
            logger.warning("[mio_studio] Mio API token 未配置或不足 32 位，请在插件配置 / 控制面板中填写。")
            return
        try:
            status = await self.service.status()
            if status.get("ok") and status.get("patched"):
                logger.info(f"[mio_studio] 已连接 Mio {status.get('version') or ''} @ {self.service.client.base_url}")
            else:
                logger.warning(f"[mio_studio] Mio 连接检查：{status.get('error') or '未知问题'}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"[mio_studio] Mio 连接检查失败：{exc}")

    async def terminate(self) -> None:
        await self.service.close()

    # ---------------------------------------------------------------- helpers
    def _allowed(self, event: AstrMessageEvent) -> bool:
        return self.service.session_allowed(event.unified_msg_origin, event.is_admin())

    @staticmethod
    def _tokens(text: str | None) -> list[str]:
        return [t for t in _TOKEN_SPLIT.split(str(text or "").strip()) if t]

    async def _guard(self, event: AstrMessageEvent):
        """Common pre-checks; returns an error message or None."""
        if not self._allowed(event):
            return "⛔ 当前会话没有使用 Mio 绘页的权限"
        if not self.service.client.configured:
            return "⚠️ 尚未配置 Mio API Token（至少 32 位），请在插件配置或控制面板中填写。"
        return None

    @staticmethod
    def _err(exc: Exception) -> str:
        if isinstance(exc, ServiceError):
            return f"❌ {exc.message}"
        if isinstance(exc, MioError):
            if exc.code == "unpatched" or (exc.status == 400 and "Unknown resource kind" in exc.message):
                return "❌ Mio 尚未应用配套补丁（缺少 /api/v1/catalog 等路由），请先按 mio-patch/README 应用补丁并重启 Mio。"
            return f"❌ Mio：{exc.message}"
        return f"❌ 内部错误：{exc}"

    async def _list(self, event: AstrMessageEvent, kinds: tuple[str, ...]):
        problem = await self._guard(event)
        if problem:
            return problem
        try:
            cat = await self.service.catalog(force=True)
        except Exception as exc:
            return self._err(exc)
        defaults = self.service.catalog_defaults()
        if len(kinds) == 1:
            return format_list(kinds[0], cat.get(kinds[0]) or [], defaults=defaults)
        return format_catalog(cat, kinds, defaults)

    # --------------------------------------------------------------- commands
    @filter.command("mio_help", alias={"mio"})
    async def cmd_help(self, event: AstrMessageEvent):
        """Mio 绘页指令帮助"""
        yield event.plain_result(HELP_TEXT)

    @filter.command("mio_ping")
    async def cmd_ping(self, event: AstrMessageEvent):
        """检查与 Mio 的连接"""
        if not self._allowed(event):
            yield event.plain_result("⛔ 当前会话没有使用 Mio 绘页的权限")
            return
        status = await self.service.status()
        if status.get("ok"):
            text = f"✅ Mio {status.get('version') or ''} 在线 @ {status.get('baseUrl')}"
            text += "\n补丁：已应用" if status.get("patched") else f"\n⚠️ {status.get('error')}"
        else:
            text = f"❌ Mio 不可用：{status.get('error')}"
        tasks = status.get("tasks") or {}
        text += f"\n本插件任务：{tasks.get('total', 0)} 个，进行中 {tasks.get('active', 0)} 个"
        yield event.plain_result(text)

    @filter.command("mio_ls")
    async def cmd_ls(self, event: AstrMessageEvent):
        """列出工作流 / 分幕 / 预设（带编号）"""
        yield event.plain_result(await self._list(event, ("workflows", "storyboards", "presets")))

    @filter.command("mio_ls_wf")
    async def cmd_ls_wf(self, event: AstrMessageEvent):
        """列出工作流"""
        yield event.plain_result(await self._list(event, ("workflows",)))

    @filter.command("mio_ls_sb")
    async def cmd_ls_sb(self, event: AstrMessageEvent):
        """列出分幕 / 分镜"""
        yield event.plain_result(await self._list(event, ("storyboards",)))

    @filter.command("mio_ls_ps")
    async def cmd_ls_ps(self, event: AstrMessageEvent):
        """列出预设"""
        yield event.plain_result(await self._list(event, ("presets",)))

    @filter.command("mio_ls_ch")
    async def cmd_ls_ch(self, event: AstrMessageEvent):
        """列出图像通道"""
        yield event.plain_result(await self._list(event, ("channels",)))

    @filter.command("mio_ls_tpl")
    async def cmd_ls_tpl(self, event: AstrMessageEvent):
        """列出导出模板"""
        yield event.plain_result(await self._list(event, ("layouts",)))

    @filter.command("mio_use")
    async def cmd_use(self, event: AstrMessageEvent, args: GreedyStr):
        """/mio_use <工作流#> <分幕#> <预设#[,预设#]> [通道#] 创建并开始生成"""
        problem = await self._guard(event)
        if problem:
            yield event.plain_result(problem)
            return
        tokens = self._tokens(args)
        if not tokens:
            yield event.plain_result("用法：/mio_use <工作流#> <分幕#> <预设#[,预设#]> [通道#]\n或 /mio_use <分幕#>（使用面板默认的工作流 / 预设 / 通道）\n先用 /mio_ls 查看编号。")
            return
        if len(tokens) == 1:
            workflow, storyboard, presets, channel = None, tokens[0], [], None
        elif len(tokens) == 2:
            workflow, storyboard, presets, channel = tokens[0], tokens[1], [], None
        else:
            workflow, storyboard, presets = tokens[0], tokens[1], [p for p in tokens[2].split(",") if p]
            channel = tokens[3] if len(tokens) > 3 else None
        try:
            selection = await self.service.resolve_selection(workflow, storyboard, presets, channel)
            record = await self.service.create_task(
                selection,
                umo=event.unified_msg_origin,
                sender_id=str(event.get_sender_id() or ""),
                sender_name=str(event.get_sender_name() or ""),
                platform=str(event.get_platform_name() or ""),
            )
        except Exception as exc:
            yield event.plain_result(self._err(exc))
            return
        sel = record.get("selection") or {}
        lines = [f"🚀 任务 #{record['num']} 已创建并开始生成", f"《{record.get('title')}》"]
        if sel.get("workflowTitle"):
            lines.append(f"工作流：{sel['workflowTitle']}")
        lines.append(f"分幕：{sel.get('storyboardTitle')}（{selection['storyboard'].get('frameCount', '?')} 幕）")
        lines.append("预设：" + " + ".join(sel.get("presetTitles") or []))
        lines.append(f"通道：{sel.get('channelTitle')}")
        lines.append(f"进度：/mio_status {record['num']}　成品：/mio_get {record['num']}")
        yield event.plain_result("\n".join(lines))

    @filter.command("mio_jobs")
    async def cmd_jobs(self, event: AstrMessageEvent):
        """我的任务列表"""
        problem = await self._guard(event)
        if problem:
            yield event.plain_result(problem)
            return
        try:
            views, others = await self.service.task_views(limit=12, force=True)
        except Exception as exc:
            yield event.plain_result(self._err(exc))
            return
        yield event.plain_result(format_jobs(views, others=others))

    @filter.command("mio_status")
    async def cmd_status(self, event: AstrMessageEvent, num: str = ""):
        """查看任务进度：/mio_status [#]"""
        problem = await self._guard(event)
        if problem:
            yield event.plain_result(problem)
            return
        try:
            record = self.service.find_record(num, umo=event.unified_msg_origin)
            view = await self.service.task_view(record)
        except Exception as exc:
            yield event.plain_result(self._err(exc))
            return
        yield event.plain_result(format_task(view))

    @filter.command("mio_get")
    async def cmd_get(self, event: AstrMessageEvent, args: GreedyStr):
        """下载成品：/mio_get [#] [html|zip|pdf|img] [模板#]"""
        problem = await self._guard(event)
        if problem:
            yield event.plain_result(problem)
            return
        tokens = self._tokens(args)
        num: str | None = None
        fmt: str | None = None
        layout: str | None = None
        for token in tokens:
            low = token.lower()
            if low in FORMATS or low in ("img", "image", "images", "图片"):
                fmt = "img" if low in ("img", "image", "images", "图片") else low
            elif num is None and (token.lstrip("#").isdigit() or token.startswith("assembly-")):
                num = token
            elif layout is None:
                if low.isascii() and low.isalpha() and len(low) <= 6:
                    yield event.plain_result(f"❌ 未知格式「{token}」，支持 html / zip / pdf / img；导出模板请用 /mio_ls_tpl 的编号")
                    return
                layout = token
        try:
            record = self.service.find_record(num, umo=event.unified_msg_origin)
            view = await self.service.task_view(record)
            if view.get("statusKey") == "missing":
                raise ServiceError(f"任务 #{record['num']} 已从 Mio 队列移除")
            if not view.get("albumId"):
                raise ServiceError(f"任务 #{record['num']} 还没有关联画册")
            if view.get("done", 0) == 0:
                raise ServiceError(f"任务 #{record['num']} 还没有生成完成的分幕（{view.get('statusKey')}）")
            umo = event.unified_msg_origin
            if fmt == "img":
                yield event.plain_result(f"⏳ 正在发送任务 #{record['num']} 的 {view.get('done', 0)} 幕图片…")
                count = await self.service.send_images(record, umo, platform=str(event.get_platform_name() or ""))
                if count == 0:
                    yield event.plain_result("没有可发送的图片")
                return
            note = ""
            if view.get("statusKey") != "complete":
                note = f"（任务尚未全部完成，导出当前已生成的 {view.get('done', 0)}/{view.get('total', 0)} 幕）"
            yield event.plain_result(f"⏳ 正在导出任务 #{record['num']}（{fmt or self.service.export_format()}）… {note}")
            await self.service.send_export(record, umo, fmt=fmt, layout=layout)
        except Exception as exc:
            yield event.plain_result(self._err(exc))

    async def _action(self, event: AstrMessageEvent, num: str, action: str):
        problem = await self._guard(event)
        if problem:
            return problem
        try:
            record = self.service.find_record(num, umo=event.unified_msg_origin)
            return await self.service.task_action(record, action)
        except Exception as exc:
            return self._err(exc)

    @filter.command("mio_pause")
    async def cmd_pause(self, event: AstrMessageEvent, num: str = ""):
        """暂停任务：/mio_pause #"""
        yield event.plain_result(await self._action(event, num, "pause"))

    @filter.command("mio_resume")
    async def cmd_resume(self, event: AstrMessageEvent, num: str = ""):
        """继续 / 重试任务：/mio_resume #"""
        yield event.plain_result(await self._action(event, num, "resume"))

    @filter.command("mio_cancel")
    async def cmd_cancel(self, event: AstrMessageEvent, num: str = ""):
        """停止任务：/mio_cancel #"""
        yield event.plain_result(await self._action(event, num, "cancel"))

    @filter.command("mio_rm")
    async def cmd_rm(self, event: AstrMessageEvent, num: str = ""):
        """移除任务记录：/mio_rm #"""
        yield event.plain_result(await self._action(event, num, "remove"))
