"""Web API endpoints backing the standalone control panel page.

Routes are registered under ``/<plugin_name>/...`` and reached from the page
through the AstrBot plugin-page bridge
(``window.AstrBotPluginPage.apiGet/apiPost/download``).
"""

from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable

from astrbot.api.web import error_response, file_response, json_response, request

from .mio_client import MioError
from .service import FORMATS, IMAGE_PROFILES, NOTIFY_MODES, SEND_MODES, MioStudioService, ServiceError
from .textfmt import STATUS_LABEL


def _wrap(handler: Callable[..., Awaitable[Any]]):
    @functools.wraps(handler)
    async def inner(*args, **kwargs):
        try:
            return await handler(*args, **kwargs)
        except ServiceError as exc:
            return error_response(exc.message, status_code=exc.status if 400 <= exc.status < 600 else 400, data={"code": exc.code})
        except MioError as exc:
            status = exc.status if 400 <= exc.status < 600 else 502
            return error_response(f"Mio: {exc.message}", status_code=status, data=exc.to_dict())
        except Exception as exc:  # pragma: no cover - defensive
            return error_response(f"内部错误：{exc}", status_code=500)

    return inner


class PageApi:
    def __init__(self, plugin_name: str, service: MioStudioService, logger) -> None:
        self.plugin_name = plugin_name
        self.service = service
        self.logger = logger

    def register(self, context) -> None:
        prefix = f"/{self.plugin_name}"
        routes: list[tuple[str, Callable, list[str], str]] = [
            ("/status", self.status, ["GET"], "Mio connection status"),
            ("/config", self.config_get, ["GET"], "Plugin configuration"),
            ("/config", self.config_post, ["POST"], "Update plugin configuration"),
            ("/catalog", self.catalog, ["GET"], "Numbered Mio resource catalogue"),
            ("/tasks", self.tasks, ["GET"], "Tasks created through the plugin"),
            ("/tasks/create", self.task_create, ["POST"], "Assemble and start a task"),
            ("/tasks/<task_id>", self.task_detail, ["GET"], "Task detail"),
            ("/tasks/<task_id>/action", self.task_action, ["POST"], "pause / resume / cancel / remove"),
            ("/tasks/<task_id>/export", self.task_export, ["GET"], "Download export (html/zip/pdf)"),
            ("/tasks/<task_id>/send", self.task_send, ["POST"], "Send result to a chat session"),
            ("/sessions", self.sessions, ["GET"], "Known chat sessions"),
        ]
        for path, handler, methods, desc in routes:
            context.register_web_api(prefix + path, _wrap(handler), methods, desc)

    @staticmethod
    async def _body() -> dict[str, Any]:
        data = await request.json(default={})
        return data if isinstance(data, dict) else {}

    # ---------------------------------------------------------------- routes
    async def status(self):
        return json_response(await self.service.status())

    async def config_get(self):
        return json_response(
            {
                "config": self.service.config_view(),
                "options": {
                    "notify_modes": [
                        {"value": "images", "label": "自动发送每一幕图片"},
                        {"value": "summary", "label": "只发完成摘要（/mio_get 取件）"},
                        {"value": "export", "label": "自动用默认模板导出并发送文件"},
                        {"value": "silent", "label": "不通知（仅查询）"},
                    ],
                    "formats": list(FORMATS),
                    "image_profiles": [
                        {"value": "auto", "label": "自动（跟随 Mio）"},
                        {"value": "archive", "label": "原样归档"},
                        {"value": "clean", "label": "无损清洗（去元数据）"},
                        {"value": "publish", "label": "发布压缩（更小）"},
                    ],
                    "send_modes": [
                        {"value": "auto", "label": "自动（QQ 合并转发 / 其他分条）"},
                        {"value": "forward", "label": "合并转发"},
                        {"value": "separate", "label": "逐条发送"},
                        {"value": "batch", "label": "一条消息内多图"},
                    ],
                    "seed_modes": [{"value": "random", "label": "随机"}, {"value": "fixed", "label": "固定（需要工作流映射种子节点）"}],
                },
            }
        )

    async def config_post(self):
        body = await self._body()
        changes = body.get("config") if isinstance(body.get("config"), dict) else body
        return json_response({"config": self.service.update_config(changes)})

    async def catalog(self):
        force = str(request.query.get("force", "")).lower() in ("1", "true", "yes")
        cat = await self.service.catalog(force=force)
        return json_response({**cat, "defaults": self.service.catalog_defaults()})

    async def tasks(self):
        limit = request.query.get("limit", "50")
        try:
            limit_int = max(1, min(400, int(limit)))
        except (TypeError, ValueError):
            limit_int = 50
        views, others = await self.service.task_views(limit=limit_int, force=True)
        views.reverse()
        return json_response({"items": [self._task_json(v) for v in views], "others": others, "statusLabels": STATUS_LABEL})

    @staticmethod
    def _task_json(view: dict[str, Any]) -> dict[str, Any]:
        keys = ("num", "id", "title", "albumId", "umo", "sender_id", "sender_name", "platform", "status", "statusKey", "done", "failed", "total", "error", "created_at", "finished_at", "notified", "selection", "live", "queuePosition")
        return {k: view.get(k) for k in keys}

    async def task_create(self):
        body = await self._body()
        presets = body.get("presets")
        if isinstance(presets, str):
            presets = [p for p in presets.split(",") if p]
        selection = await self.service.resolve_selection(
            str(body.get("workflow") or "") or None,
            str(body.get("storyboard") or ""),
            [str(p) for p in (presets or [])],
            str(body.get("channel") or "") or None,
        )
        umo = str(body.get("umo") or "").strip()
        record = await self.service.create_task(selection, umo=umo, sender_name="控制面板", platform=umo.split(":", 1)[0] if ":" in umo else "panel", title=str(body.get("title") or ""), start=body.get("start", True) is not False)
        return json_response({"task": self._task_json(self.service.merge_view(record, None))})

    async def task_detail(self, task_id: str):
        record = self.service.find_record(task_id)
        view = await self.service.task_view(record)
        return json_response({"task": self._task_json(view)})

    async def task_action(self, task_id: str):
        body = await self._body()
        action = str(body.get("action") or "")
        if action not in ("pause", "resume", "cancel", "remove"):
            raise ServiceError("未知操作")
        record = self.service.find_record(task_id)
        message = await self.service.task_action(record, action)
        return json_response({"message": message})

    async def task_export(self, task_id: str):
        record = self.service.find_record(task_id)
        fmt = str(request.query.get("format", "") or "") or None
        layout = str(request.query.get("layout", "") or "") or None
        result = await self.service.export(record, fmt=fmt, layout=layout)
        return file_response(result["path"], filename=result["filename"], content_type=result["contentType"])

    async def task_send(self, task_id: str):
        body = await self._body()
        record = self.service.find_record(task_id)
        umo = str(body.get("umo") or record.get("umo") or "").strip()
        if not umo:
            raise ServiceError("缺少目标会话（umo）")
        mode = str(body.get("mode") or "export")
        if mode == "images":
            count = await self.service.send_images(record, umo)
            return json_response({"message": f"已发送 {count} 张图片到 {umo}"})
        result = await self.service.send_export(record, umo, fmt=str(body.get("format") or "") or None, layout=str(body.get("layout") or "") or None)
        return json_response({"message": f"已发送 {result['filename']} 到 {umo}"})

    async def sessions(self):
        return json_response({"items": self.service.store.sessions()})
