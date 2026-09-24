"""Plain-text renderers for chat replies (lists, task cards, help)."""

from __future__ import annotations

import time
from typing import Any

STATUS_LABEL = {
    "standby": "待命",
    "ready": "排队中",
    "preparing": "准备中",
    "running": "生成中",
    "complete": "已完成",
    "failed": "失败",
    "interrupted": "已中断",
    "cancelled": "已停止",
    "paused": "已暂停",
    "unknown": "未知",
    "missing": "已从 Mio 移除",
}
STATUS_ICON = {
    "standby": "⏸",
    "ready": "⏳",
    "preparing": "⏳",
    "running": "🎨",
    "complete": "✅",
    "failed": "❌",
    "interrupted": "⚠️",
    "cancelled": "⏹",
    "paused": "⏸",
    "unknown": "❔",
    "missing": "🗑",
}
KIND_LABEL = {"workflows": "工作流", "storyboards": "分幕 / 分镜", "presets": "预设", "channels": "图像通道", "layouts": "导出模板"}
KIND_CMD = {"workflows": "/mio_ls_wf", "storyboards": "/mio_ls_sb", "presets": "/mio_ls_ps", "channels": "/mio_ls_ch", "layouts": "/mio_ls_tpl"}

HELP_TEXT = """🎨 Mio 绘页 · 指令
/mio_ls — 列出工作流 / 分幕 / 预设（带编号）
/mio_ls_wf | /mio_ls_sb | /mio_ls_ps — 单独列出
/mio_ls_ch | /mio_ls_tpl — 图像通道 / 导出模板
/mio_use <工作流#> <分幕#> <预设#[,预设#]> [通道#] — 创建并开始生成
/mio_use <分幕#> — 用面板里配置的默认工作流 / 预设 / 通道
/mio_jobs — 我的任务列表（编号 #）
/mio_status [#] — 查看任务进度（默认最近一个）
/mio_get [#] [html|zip|pdf|img] [模板#] — 下载成品 / 发送图片
/mio_pause # | /mio_resume # | /mio_cancel # | /mio_rm # — 任务控制
/mio_ping — 检查与 Mio 的连接
提示：编号以 /mio_ls 当前输出为准；0 表示使用默认值。"""


def clip(text: Any, limit: int = 40) -> str:
    text = str(text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _preset_summary(item: dict[str, Any]) -> str:
    parts = []
    for entry in item.get("entries") or []:
        key, value = entry.get("key"), entry.get("value")
        if key in ("character_display_name",) or not isinstance(value, str) or not value.strip():
            continue
        parts.append(f"{key}={clip(value, 18)}")
        if len(parts) >= 3:
            break
    return "；".join(parts)


def format_list(kind: str, items: list[dict[str, Any]], *, defaults: dict[str, Any] | None = None, detail: bool = True) -> str:
    label = KIND_LABEL.get(kind, kind)
    if not items:
        return f"{label}：Mio 中暂无记录"
    default_ids = set()
    if defaults:
        value = defaults.get(kind)
        if isinstance(value, (list, tuple, set)):
            default_ids = {v for v in value if v}
        elif value:
            default_ids = {value}
    lines = [f"{label}（{len(items)}）"]
    for index, item in enumerate(items, 1):
        mark = " ★默认" if item.get("id") in default_ids else ""
        extra = ""
        if detail:
            if kind == "storyboards":
                extra = f" · {item.get('frameCount', 0)} 幕"
            elif kind == "presets":
                cat = "角色" if item.get("category") == "characters" else "场景"
                extra = f" · {cat}"
                summary = _preset_summary(item)
                if summary:
                    extra += f" · {summary}"
            elif kind == "workflows":
                extra = f" · {item.get('nodeCount', 0)} 节点"
            elif kind == "channels":
                extra = f" · {item.get('provider', '')}" + (f"/{clip(item.get('model'), 20)}" if item.get("model") else "")
            elif kind == "layouts":
                extra = f" · {item.get('layout', '')}"
        lines.append(f"{index}. {clip(item.get('title') or item.get('id'), 30)}{extra}{mark}")
    return "\n".join(lines)


def format_catalog(catalog: dict[str, Any], kinds: tuple[str, ...], defaults: dict[str, Any] | None = None) -> str:
    blocks = [format_list(kind, catalog.get(kind) or [], defaults=defaults) for kind in kinds]
    if kinds == ("workflows", "storyboards", "presets"):
        blocks.append("用法：/mio_use <工作流#> <分幕#> <预设#> — 例如 /mio_use 1 1 1")
    return "\n\n".join(blocks)


def progress_of(task: dict[str, Any] | None) -> tuple[int, int, int]:
    """(complete, failed, total) from a Mio production task."""
    pages = task.get("pages") if isinstance(task, dict) else None
    if not isinstance(pages, list):
        return 0, 0, 0
    done = sum(1 for p in pages if isinstance(p, dict) and p.get("state") == "complete")
    failed = sum(1 for p in pages if isinstance(p, dict) and p.get("state") == "failed")
    return done, failed, len(pages)


def status_key(task: dict[str, Any] | None) -> str:
    if not task:
        return "missing"
    if task.get("paused") and task.get("status") not in ("complete", "failed", "interrupted", "cancelled"):
        return "paused"
    return str(task.get("status") or "unknown")


def format_job_line(view: dict[str, Any]) -> str:
    key = view.get("statusKey", "unknown")
    done, failed, total = view.get("done", 0), view.get("failed", 0), view.get("total", 0)
    prog = f" {done}/{total}" if total else ""
    fail = f"（{failed} 幕失败）" if failed else ""
    return f"#{view['num']} {STATUS_ICON.get(key, '❔')} {clip(view.get('title'), 26)} · {STATUS_LABEL.get(key, key)}{prog}{fail}"


def format_jobs(views: list[dict[str, Any]], *, others: int = 0) -> str:
    if not views:
        text = "还没有通过本插件创建的任务。用 /mio_ls 查看资源，再 /mio_use 创建。"
    else:
        text = "🗂 我的任务\n" + "\n".join(format_job_line(v) for v in views)
        text += "\n\n/mio_status # 查看详情，/mio_get # 下载成品"
    if others:
        text += f"\n（Mio 队列中另有 {others} 个非本插件创建的任务）"
    return text


def format_task(view: dict[str, Any]) -> str:
    key = view.get("statusKey", "unknown")
    lines = [f"{STATUS_ICON.get(key, '❔')} 任务 #{view['num']} 《{view.get('title')}》", f"状态：{STATUS_LABEL.get(key, key)}"]
    total = view.get("total", 0)
    if total:
        lines.append(f"进度：{view.get('done', 0)}/{total} 幕" + (f"，失败 {view.get('failed', 0)} 幕" if view.get("failed") else ""))
    sel = view.get("selection") or {}
    if sel:
        parts = []
        if sel.get("workflowTitle"):
            parts.append(f"工作流 {sel['workflowTitle']}")
        if sel.get("storyboardTitle"):
            parts.append(f"分幕 {sel['storyboardTitle']}")
        if sel.get("presetTitles"):
            parts.append("预设 " + " + ".join(sel["presetTitles"]))
        if sel.get("channelTitle"):
            parts.append(f"通道 {sel['channelTitle']}")
        if parts:
            lines.append("配置：" + "；".join(parts))
    if view.get("error"):
        lines.append(f"错误：{clip(view['error'], 160)}")
    if view.get("created_at"):
        lines.append("创建：" + time.strftime("%m-%d %H:%M", time.localtime(float(view["created_at"]))))
    if key == "complete":
        lines.append(f"下载：/mio_get {view['num']} [html|zip|pdf|img] [模板#]")
    elif key in ("failed", "interrupted", "cancelled", "paused", "standby"):
        lines.append(f"继续 / 重试：/mio_resume {view['num']}" + (f"　已生成部分可 /mio_get {view['num']}" if view.get("done") else ""))
    return "\n".join(lines)
