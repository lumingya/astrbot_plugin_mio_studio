"""Small, actor-aware responses for AstrBot's Mio function calls."""

from __future__ import annotations

import functools
import json
import re
from typing import Any

from .delivery import FORMATS
from .governance import Actor, GovernanceError
from .service import ServiceError
from .v2_client import MioApiError

_KINDS = frozenset({"workflows", "storyboards", "presets", "layouts", "channels", "kits", "works"})
_CREDENTIAL = re.compile(
    r"\bmio_[A-Za-z0-9_-]{10,}\b|\bsk-[A-Za-z0-9_-]{12,}\b"
    r"|(?i:\bbearer\s+)[A-Za-z0-9._~-]{8,}"
)
_SECRET_KEY = re.compile(r"token|secret|password|api.?key|authorization|凭据|令牌|密钥", re.I)
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_LIST_LIMIT = 8
_OUTPUT_LIMIT = 3000


def _visible_text(value: object, limit: int = 96) -> str:
    if not isinstance(value, str):
        return ""
    value = _CREDENTIAL.sub("[已隐藏]", value)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _dump(payload: dict[str, Any]) -> str:
    output = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(output) <= _OUTPUT_LIMIT:
        return output
    return _dump(
        {
            "ok": False,
            "code": "response_too_large",
            "message": "可展示内容过多，请缩小检索范围或到 Mio 控制面板查看。",
        }
    )


def _tool(method):
    @functools.wraps(method)
    async def call(self, actor: Actor, *args, **kwargs) -> str:
        if self.service.cfg("chat", "llm_tools_enabled", False) is not True:
            return _dump(
                {
                    "ok": False,
                    "code": "disabled",
                    "message": "AI 创作工具尚未开启。管理员可在 Mio 插件设置中开启。",
                }
            )
        if not actor.id or not actor.session:
            return _dump(
                {
                    "ok": False,
                    "code": "missing_actor",
                    "message": "无法识别当前聊天身份，请在聊天会话中重新发起请求。",
                }
            )
        try:
            return _dump({"ok": True, **await method(self, actor, *args, **kwargs)})
        except (GovernanceError, MioApiError, ServiceError) as exc:
            return _dump(
                {
                    "ok": False,
                    "code": exc.code
                    if _CODE.fullmatch(exc.code) and not _CREDENTIAL.search(exc.code)
                    else "mio_error",
                    "message": _visible_text(exc.message, 180),
                }
            )
        except Exception:
            # Tools can be called with malformed values by a provider. Never pass
            # exception text, transport bodies or file paths back to the model.
            return _dump(
                {
                    "ok": False,
                    "code": "tool_error",
                    "message": "操作未完成，请核对参数后重试。",
                }
            )

    return call


def _invalid(message: str) -> ServiceError:
    return ServiceError(message, code="invalid_arguments")


def _text(value: object, label: str, *, length: int = 256, required: bool = False) -> str:
    if not isinstance(value, str):
        raise _invalid(f"{label}应为文字。")
    value = value.strip()
    if (required and not value) or len(value) > length or _CREDENTIAL.search(value):
        raise _invalid(f"请填写有效的{label}（最多 {length} 字）。不要输入令牌或密钥。")
    return value


def _integer(value: object, label: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise _invalid(f"{label}必须是 1 到 {maximum} 的整数。")
    return value


def _ref(value: object, label: str) -> str | None:
    return _text(value, label) or None


def _number(record: dict) -> int | None:
    number = record.get("num")
    return number if type(number) is int and 1 <= number < 10**15 else None


def _submitted(record: dict) -> dict:
    number = _number(record)
    return {
        "task": number,
        "state": _visible_text(record.get("status"), 24),
        "message": "已受理，当前仍在后台处理。",
        "next": f"/mio status {number}" if number else "/mio jobs",
    }


def _catalog_items(rows, query: str) -> tuple[list[dict], int]:
    items = []
    total = 0
    remaining = _OUTPUT_LIMIT - 300
    match = query.casefold()
    for index, row in enumerate(rows, 1):
        title = _visible_text(row.get("title") or row.get("name"))
        identifier = _visible_text(row.get("episode_id") or row.get("id"), 256)
        if match and match not in f"{title} {identifier}".casefold():
            continue
        total += 1
        item = {"number": index, "id": identifier, "title": title}
        if series_title := _visible_text(row.get("series_title"), 48):
            item["series"] = series_title
        size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":"))) + 1
        if len(items) < _LIST_LIMIT and size <= remaining:
            remaining -= size
            items.append(item)
    return items, total


def _assembly_summary(result: dict) -> dict:
    missing = set()
    panels = result.get("panels") or []
    for panel in panels:
        for variable in panel.get("variables") or []:
            if variable.get("status") not in {"missing", "empty", "cycle", "depth_limit"}:
                continue
            key = _visible_text(variable.get("key"), 40)
            if key and not _SECRET_KEY.search(key):
                missing.add(key)
    return {
        "panel_count": len(panels),
        "missing_variables": sorted(missing)[:12],
        "message": "装配预览完成，尚未出图。"
        + ("请核对变量后再提交。" if missing else "如确认方案，可提交生成。"),
    }


class LLMTools:
    """Service adapter; only decorator handlers create an Actor from a trusted event."""

    def __init__(self, service) -> None:
        self.service = service

    @_tool
    async def list_resources(
        self, actor: Actor, kind="workflows", query="", instance_id=""
    ) -> dict:
        kind = _text(kind, "资源类型", length=24, required=True)
        if kind not in _KINDS:
            raise _invalid("请指定工作流、分镜、预设、通道、模板、套件或作品。")
        query = _text(query, "搜索关键词", length=96)
        instance = _ref(instance_id, "实例")
        if kind == "works":
            rows = await self.service.works(
                actor=actor, instance_id=instance, query=query, limit=_LIST_LIMIT + 1
            )
            query = ""
        else:
            catalog = await self.service.catalog(actor=actor, instance_id=instance)
            rows = catalog.get(kind) or []
        items, count = _catalog_items(rows, query)
        return {
            "kind": kind,
            "items": items,
            "more": count > len(items),
            "message": "使用显示的 ID 可直接选择资源。" if items else "没有找到匹配的资源。",
        }

    @_tool
    async def draw(
        self, actor: Actor, idea, series="", style="", candidates=1, layout="", instance_id=""
    ) -> dict:
        record = await self.service.create_draw(
            _text(idea, "一句话创意", length=1000, required=True),
            actor=actor,
            series=_ref(series, "系列"),
            style=_ref(style, "风格"),
            candidates=_integer(candidates, "候选数", 16),
            layout=_ref(layout, "模板"),
            instance_id=_ref(instance_id, "实例"),
        )
        return _submitted(record)

    @_tool
    async def assemble(
        self,
        actor: Actor,
        storyboard,
        workflow="",
        presets="",
        channel="",
        preview=True,
        instance_id="",
    ) -> dict:
        if type(preview) is not bool:
            raise _invalid("preview 必须是布尔值。")
        preset_ids = [
            item.strip()
            for item in re.split("[,，、]", _text(presets, "预设列表", length=1600))
            if item.strip()
        ]
        if len(preset_ids) > 16:
            raise _invalid("单次最多选择 16 个预设。")
        selection = await self.service.resolve_selection(
            _ref(workflow, "工作流"),
            _text(storyboard, "分镜", required=True),
            preset_ids,
            _ref(channel, "图像通道"),
            actor=actor,
            instance_id=_ref(instance_id, "实例"),
        )
        if preview:
            return _assembly_summary(await self.service.preview(selection, actor=actor))
        return _submitted(await self.service.create_task(selection, actor=actor))

    @_tool
    async def trial(self, actor: Actor, prompt, workflow="", instance_id="") -> dict:
        record = await self.service.create_trial(
            _text(prompt, "试画提示词", length=2400, required=True),
            _ref(workflow, "工作流"),
            actor=actor,
            instance_id=_ref(instance_id, "实例"),
        )
        return _submitted(record)

    @_tool
    async def task(self, actor: Actor, task, action="status", panel=0, prompt="") -> dict:
        action = _text(action, "操作", length=16, required=True)
        if action not in {"status", "pause", "resume", "cancel", "redraw"}:
            raise _invalid("可查询进度、暂停、继续、取消或重画一幕。")
        number = _text(task, "任务编号", length=64, required=action != "status")
        panel_number = _integer(panel, "分幕编号", 1000) if action == "redraw" else None
        new_prompt = _text(prompt, "新提示词", length=2400) if action == "redraw" else ""
        record = self.service.find_record(number or None, actor=actor)
        if action == "status":
            view = await self.service.task_view(record, actor=actor)
            return {
                "task": _number(view),
                "title": _visible_text(view.get("title")),
                "state": _visible_text(view.get("statusKey"), 24),
                "progress": {
                    key: view[key]
                    for key in ("done", "total", "failed", "queuePosition")
                    if type(view.get(key)) is int
                },
                "message": _visible_text(view.get("error"), 160) or "已查询当前进度。",
            }
        if action == "redraw":
            queued = await self.service.redraw(
                record, panel_number, new_prompt or None, actor=actor
            )
            return _submitted(queued)
        message = await self.service.task_action(record, action, actor=actor)
        return {"task": _number(record), "message": _visible_text(message, 180)}

    @_tool
    async def export_and_send(
        self, actor: Actor, task="", work="", fmt="", layout="", instance_id=""
    ) -> dict:
        task = _text(task, "任务编号", length=64)
        work = _text(work, "作品 ID")
        fmt = _text(fmt, "导出格式", length=12)
        if bool(task) == bool(work):
            raise _invalid("请选择一个任务或一件作品来导出。")
        if fmt and fmt not in {*FORMATS, "img"}:
            raise _invalid("请选择 html、pdf、zip、strip、slices、motion 或 img。")
        if work and fmt == "img":
            raise _invalid("分幕发图适用于任务；图库作品可导出 strip 或 slices。")
        template = _ref(layout, "模板")
        instance = _ref(instance_id, "实例")
        if task:
            record = self.service.find_record(task, actor=actor)
            if instance and record.get("instance_id") != instance:
                raise _invalid("任务不在所选实例上，请核对实例和任务编号。")
            if fmt == "img":
                count = await self.service.send_images(record, actor.session, actor=actor)
                return {"sent": count, "message": f"已发送 {count} 张图到当前会话。"}
            result = await self.service.send_export(
                record, actor.session, actor=actor, fmt=fmt or None, layout=template
            )
        else:
            rows = await self.service.works(actor=actor, instance_id=instance, query="", limit=100)
            selected = self.service.pick(rows, work, label="作品")
            result = await self.service.send_work(
                selected, actor.session, actor=actor, fmt=fmt or None, layout=template
            )
        return {
            "filename": _visible_text(result.get("filename")),
            "format": _visible_text(result.get("format"), 12),
            "message": "已发送到当前会话。",
        }
