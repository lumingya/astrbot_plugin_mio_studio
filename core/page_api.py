"""Web API endpoints backing the standalone control panel page.

Routes are registered under ``/<plugin_name>/...`` and reached from the page
through the AstrBot plugin-page bridge
(``window.AstrBotPluginPage.apiGet/apiPost/download``).
"""

from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable

from astrbot.api.web import error_response, file_response, json_response, request

from .config import ConfigError
from .governance import Actor, GovernanceError
from .service import (
    FORMATS,
    MioStudioService,
    ServiceError,
)
from .textfmt import STATUS_LABEL
from .v2_client import MioApiError


def _wrap(handler: Callable[..., Awaitable[Any]], logger):
    @functools.wraps(handler)
    async def inner(*args, **kwargs):
        try:
            # AstrBot sets the username on plugin extension and dashboard routes.
            # Never grant panel authority based on fields supplied in a request body.
            if not request.username:
                return error_response("请先登录 AstrBot 控制面板。", status_code=401)
            return await handler(*args, **kwargs)
        except (ConfigError, ServiceError, GovernanceError) as exc:
            return error_response(
                exc.message,
                status_code=exc.status if 400 <= exc.status < 600 else 400,
                data={"code": exc.code},
            )
        except MioApiError as exc:
            status = exc.status if 400 <= exc.status < 600 else 502
            return error_response(f"Mio: {exc.message}", status_code=status, data=exc.to_dict())
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("[mio_studio] 面板操作失败：%s", type(exc).__name__)
            return error_response("Mio 操作遇到内部错误，请稍后重试。", status_code=500)

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
            (
                "/tasks/<task_id>/action",
                self.task_action,
                ["POST"],
                "pause / resume / cancel / remove",
            ),
            (
                "/tasks/<task_id>/export",
                self.task_export,
                ["GET"],
                "Download export (html/zip/pdf)",
            ),
            ("/tasks/<task_id>/send", self.task_send, ["POST"], "Send result to a chat session"),
            ("/sessions", self.sessions, ["GET"], "Known chat sessions"),
        ]
        for path, handler, methods, desc in routes:
            context.register_web_api(prefix + path, _wrap(handler, self.logger), methods, desc)

    @staticmethod
    async def _body() -> dict[str, Any]:
        data = await request.json()
        if not isinstance(data, dict):
            raise ServiceError("请求体必须为 JSON 对象。", code="invalid_body")
        return data

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ServiceError("请输入有效的文本值。", code="invalid_input")
        return value.strip()

    def _actor_for_session(self, umo: str) -> Actor:
        if not umo:
            return Actor.panel()
        session = next((item for item in self.service.store.sessions() if item["umo"] == umo), None)
        if session is None:
            raise ServiceError("请选择已知会话。请先在目标会话中使用 Mio。", code="unknown_session")
        return Actor(
            id="panel",
            name="控制面板",
            session=umo,
            platform=str(session.get("platform") or ""),
            is_admin=True,
        )

    # ---------------------------------------------------------------- routes
    async def status(self):
        status = await self.service.status(
            actor=Actor.panel(), instance_id=request.query.get("instance_id") or None
        )
        # The pre-Wave 4 panel uses this old field to display the green state.
        return json_response(status)

    async def config_get(self):
        return json_response(
            {
                "config": self.service.config_view(),
                "options": {
                    "notify_modes": [
                        {"value": "images", "label": "自动发送每一幕图片"},
                        {"value": "summary", "label": "只发完成摘要（/mio get 取件）"},
                        {"value": "strip", "label": "发送整条长漫"},
                        {"value": "export", "label": "自动用默认模板导出并发送文件"},
                        {"value": "qa_then_send", "label": "质检后发送图片"},
                        {"value": "silent", "label": "不通知（仅查询）"},
                    ],
                    "formats": list(FORMATS),
                    "image_profiles": [
                        {"value": "auto", "label": "自动（跟随 Mio）"},
                        {"value": "archive", "label": "原样归档"},
                        {"value": "clean", "label": "无损清洗（去元数据）"},
                        {"value": "publish", "label": "发布压缩（更小）"},
                        {"value": "preview", "label": "快速预览"},
                    ],
                    "send_modes": [
                        {"value": "auto", "label": "自动（QQ 合并转发 / 其他分条）"},
                        {"value": "forward", "label": "合并转发"},
                        {"value": "separate", "label": "逐条发送"},
                        {"value": "batch", "label": "一条消息内多图"},
                    ],
                    "seed_modes": [
                        {"value": "random", "label": "随机"},
                        {"value": "fixed", "label": "固定（需要工作流映射种子节点）"},
                    ],
                },
            }
        )

    async def config_post(self):
        body = await self._body()
        changes = body.get("config") if isinstance(body.get("config"), dict) else body
        return json_response({"config": await self.service.update_config(changes)})

    async def catalog(self):
        force = str(request.query.get("force", "")).lower() in ("1", "true", "yes")
        cat = await self.service.catalog(
            actor=Actor.panel(), force=force, instance_id=request.query.get("instance_id") or None
        )
        return json_response({**cat, "defaults": self.service.catalog_defaults()})

    async def tasks(self):
        limit = request.query.get("limit", "50")
        try:
            limit_int = max(1, min(400, int(limit)))
        except (TypeError, ValueError):
            limit_int = 50
        views, others = await self.service.task_views(
            actor=Actor.panel(),
            limit=limit_int,
            force=True,
            instance_id=request.query.get("instance_id") or None,
        )
        return json_response(
            {
                "items": [self._task_json(v) for v in views],
                "others": others,
                "statusLabels": STATUS_LABEL,
            }
        )

    @staticmethod
    def _task_json(view: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "num",
            "id",
            "title",
            "albumId",
            "umo",
            "sender_name",
            "platform",
            "status",
            "statusKey",
            "done",
            "failed",
            "total",
            "error",
            "created_at",
            "finished_at",
            "notified",
            "queuePosition",
            "instance_id",
            "episode_id",
        )
        output = {k: view.get(k) for k in keys}
        selection = view.get("selection") or {}
        output["selection"] = {
            k: selection.get(k)
            for k in ("workflowTitle", "storyboardTitle", "presetTitles", "channelTitle")
        }
        return output

    async def task_create(self):
        body = await self._body()
        presets = body.get("presets")
        if isinstance(presets, str):
            presets = [p.strip() for p in presets.split(",") if p.strip()]
        elif presets is None:
            presets = []
        elif not isinstance(presets, list) or not all(isinstance(p, str) for p in presets):
            raise ServiceError("预设须为编号列表。", code="invalid_presets")
        actor = self._actor_for_session(self._text(body.get("umo")))
        selection = await self.service.resolve_selection(
            self._text(body.get("workflow")) or None,
            self._text(body.get("storyboard")),
            presets,
            self._text(body.get("channel")) or None,
            actor=actor,
            instance_id=self._text(body.get("instance_id")) or None,
        )
        record = await self.service.create_task(
            selection,
            actor=actor,
            title=self._text(body.get("title")),
            start=body.get("start", True) is not False,
        )
        return json_response({"task": self._task_json(self.service.merge_view(record, None))})

    async def task_detail(self, task_id: str):
        actor = Actor.panel()
        record = self.service.find_record(task_id, actor=actor)
        view = await self.service.task_view(record, actor=actor)
        return json_response({"task": self._task_json(view)})

    async def task_action(self, task_id: str):
        body = await self._body()
        action = self._text(body.get("action"))
        if action not in ("pause", "resume", "cancel", "remove"):
            raise ServiceError("未知操作")
        actor = Actor.panel()
        record = self.service.find_record(task_id, actor=actor)
        message = await self.service.task_action(record, action, actor=actor)
        return json_response({"message": message})

    async def task_export(self, task_id: str):
        actor = Actor.panel()
        record = self.service.find_record(task_id, actor=actor)
        fmt = str(request.query.get("format", "") or "") or None
        layout = str(request.query.get("layout", "") or "") or None
        result = await self.service.export(record, actor=actor, fmt=fmt, layout=layout)
        return file_response(
            result["path"],
            filename=result["filename"],
            content_type=result["contentType"],
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def task_send(self, task_id: str):
        body = await self._body()
        record = self.service.find_record(task_id, actor=Actor.panel())
        umo = self._text(body.get("umo")) or record.get("umo") or ""
        if not umo:
            raise ServiceError("请选择目标会话。")
        actor = self._actor_for_session(umo)
        mode = self._text(body.get("mode")) or "export"
        if mode == "images":
            count = await self.service.send_images(
                record, umo, actor=actor, platform=actor.platform
            )
            return json_response({"message": f"已向目标会话发送 {count} 张图片"})
        if mode != "export":
            raise ServiceError("请选择发送图片或导出文件。", code="invalid_send_mode")
        result = await self.service.send_export(
            record,
            umo,
            actor=actor,
            fmt=self._text(body.get("format")) or None,
            layout=self._text(body.get("layout")) or None,
        )
        return json_response({"message": f"已向目标会话发送 {result['filename']}"})

    async def sessions(self):
        return json_response({"items": self.service.store.sessions()})
