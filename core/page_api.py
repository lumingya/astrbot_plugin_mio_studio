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
from .panel_queries import (
    assembly_preview,
    display,
    episode_view,
    export_options,
    image_source,
    integer,
    overview_metrics,
    rows,
)
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
            ("/overview", self.overview, ["GET"], "Dashboard metrics and instances"),
            ("/status", self.status, ["GET"], "Mio connection status"),
            ("/config", self.config_get, ["GET"], "Plugin configuration"),
            ("/config", self.config_post, ["POST"], "Update plugin configuration"),
            ("/catalog", self.catalog, ["GET"], "Numbered Mio resource catalogue"),
            ("/resources/<kind>/<resource_id>", self.resource, ["GET"], "Resource details"),
            ("/resources/diagnose", self.diagnose, ["POST"], "Diagnose a workflow"),
            ("/resources/models", self.models, ["POST"], "Check a saved model channel"),
            ("/studio/preview", self.studio_preview, ["POST"], "Preview workshop assembly"),
            ("/studio/draw", self.studio_draw, ["POST"], "Create a comic from an idea"),
            ("/studio/trial", self.studio_trial, ["POST"], "Enqueue a trial image"),
            ("/tasks", self.tasks, ["GET"], "Tasks created through the plugin"),
            ("/tasks/create", self.task_create, ["POST"], "Assemble and start a task"),
            ("/tasks/<task_id>", self.task_detail, ["GET"], "Task detail"),
            ("/tasks/<task_id>/image", self.task_image, ["GET"], "Preview an image from a task"),
            (
                "/tasks/<task_id>/redraw",
                self.task_redraw,
                ["POST"],
                "Render a new version of a panel",
            ),
            ("/tasks/<task_id>/pick", self.task_pick, ["POST"], "Adopt a candidate"),
            ("/tasks/<task_id>/qa", self.task_qa, ["POST"], "Enqueue quality assessment"),
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
            ("/gallery", self.gallery, ["GET"], "Browse the Mio gallery"),
            ("/gallery/<episode_id>", self.gallery_detail, ["GET"], "Episode and panels"),
            ("/gallery/<episode_id>/image", self.gallery_image, ["GET"], "Preview a gallery image"),
            (
                "/gallery/<episode_id>/export",
                self.gallery_export,
                ["GET"],
                "Download a gallery work",
            ),
            ("/gallery/<episode_id>/send", self.gallery_send, ["POST"], "Send a gallery work"),
            ("/instances", self.instances, ["GET"], "Configured Mio instances"),
            ("/instances", self.instance_action, ["POST"], "Manage a Mio instance"),
            ("/sessions", self.sessions, ["GET"], "Known chat sessions"),
            ("/sessions/rule", self.session_rule, ["POST"], "Save a chat delivery rule"),
            ("/webhooks", self.webhooks, ["GET"], "Webhook status and delivery history"),
            ("/webhooks", self.webhook_action, ["POST"], "Register, test or remove a webhook"),
            ("/audit", self.audit, ["GET"], "Recent audit and quotas"),
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

    @staticmethod
    def _id(value: Any, *, optional: bool = False) -> str | None:
        if value in (None, "") and optional:
            return None
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 256
            or value in {".", ".."}
            or any(c in "/\\%?#" or ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ServiceError("资源编号格式不正确。", code="invalid_id")
        return value

    def _instance_id(self, payload=None):
        source = request.query if payload is None else payload
        return self._id(source.get("instance_id"), optional=True)

    def _runtime(self, payload=None):
        instance = self.service.route(Actor.panel(), self._instance_id(payload))
        return instance, self.service.instances.get(instance.id)

    def _instance_payload(self):
        return {
            "instances": self.service.instances.public(),
            "default_id": self.service.instances.default_id,
        }

    def _task_response(self, record):
        return json_response({"task": self._task_json(self.service.merge_view(record, None))})

    @staticmethod
    def _download(result):
        return file_response(
            result["path"],
            filename=result["filename"],
            content_type=result["contentType"],
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @staticmethod
    def _query_export():
        return {
            key: value
            for key in (
                "theme_color",
                "signature",
                "show_captions",
                "show_prompts",
                "image_profile",
                "lettered",
                "voice",
            )
            if (value := request.query.get(key)) is not None
        }

    async def overview(self):
        actor = Actor.panel()
        instance = self.service.route(actor, self._instance_id())
        tasks = self.service.store.list_tasks(instance_id=instance.id, limit=400)
        catalog_error = ""
        try:
            catalog = await self.service.catalog(actor=actor, instance_id=instance.id)
        except MioApiError as exc:
            catalog, catalog_error = {}, exc.message
        return json_response(
            {
                **self._instance_payload(),
                "metrics": overview_metrics(tasks, catalog),
                "catalog_error": catalog_error,
            }
        )

    # ---------------------------------------------------------------- routes
    async def status(self):
        status = await self.service.status(
            actor=Actor.panel(), instance_id=request.query.get("instance_id") or None
        )
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

    async def resource(self, kind: str, resource_id: str):
        _, client = self._runtime()
        rid = self._id(resource_id)
        resource = await self.service.resources.detail(client, kind, rid)
        loras = {}
        if kind == "workflows":
            slots = await self.service.resources.loras(client, rid)
            resource = {**resource, "loras": slots}
            loras = {"loras": slots}
        return json_response({"resource": resource, **loras})

    async def diagnose(self):
        body = await self._body()
        _, client = self._runtime(body)
        workflow = self._id(body.get("id") or body.get("workflow_id") or body.get("workflow"))
        result = await client.call("diagnose_workflow", path_params={"workflow_id": workflow})
        if not isinstance(result, dict):
            raise ServiceError("Mio 没有返回诊断。", status=502)
        warnings = result.get("warnings")
        if isinstance(result.get("diagnostics"), dict):
            result = result["diagnostics"]
        by_kind = {}
        for key in (
            "missing_models",
            "missing_inputs",
            "invalid_links",
            "invalid_inputs",
            "invalid_values",
        ):
            by_kind[key] = [
                {
                    field: display(entry.get(field), 200)
                    for field in ("node", "class_type", "input", "reason")
                }
                for entry in rows(result.get(key), 50)
            ]
        nodes = result.get("missing_nodes")
        by_kind["missing_nodes"] = (
            {
                display(key): [display(str(node), 48) for node in ids[:32]]
                for key, ids in list(nodes.items())[:50]
                if isinstance(ids, list)
            }
            if isinstance(nodes, dict)
            else {}
        )
        issues = sum(
            len(value) if isinstance(value, list) else sum(map(len, value.values()))
            for value in by_kind.values()
        )
        return json_response(
            {
                "diagnosis": {
                    "ok": result.get("ok", not issues) is True,
                    "issues": issues,
                    **by_kind,
                    "warnings": [display(w, 200) for w in warnings[:50]]
                    if isinstance(warnings, list)
                    else [],
                    "checks": [
                        {
                            "message": " · ".join(
                                display(item.get(field), 64)
                                for field in ("class_type", "node", "input", "reason")
                                if item.get(field)
                            )
                        }
                        for key in ("missing_inputs", "invalid_inputs", "invalid_links")
                        for item in by_kind.get(key, [])[:30]
                    ],
                }
            }
        )

    async def models(self):
        body = await self._body()
        _, client = self._runtime(body)
        # Only settings already saved in Mio may be used. Never accept a raw
        # destination or credential supplied by an iframe.
        channel_id = self._text(body.get("id") if "id" in body else body.get("channel_id"))
        if len(channel_id) > 256:
            raise ServiceError("图像通道编号过长。", code="invalid_id")
        settings = await client.call("get_settings")
        channel = (
            next(
                (
                    row
                    for row in rows(settings.get("image_channels"), 300)
                    if row.get("id") == channel_id
                ),
                None,
            )
            if isinstance(settings, dict)
            else None
        )
        if channel is None:
            raise ServiceError("请先在 Mio 保存该图像通道。", code="channel_not_found", status=404)
        data = await client.call(
            "fetch_models",
            body={
                "kind": channel["kind"],
                "base_url": channel.get("base_url") or "",
                "api_key": "••••••" if channel.get("api_key") == "••••••" else "",
                "channel_id": channel_id,
            },
        )
        if not isinstance(data, dict):
            raise ServiceError("Mio 没有返回模型目录。", status=502)
        return json_response(
            {
                "models": [
                    {key: display(row.get(key), 200) for key in ("id", "name", "group")}
                    for row in rows(data.get("models"), 300)
                ],
                "source": "bundled" if data.get("source") == "bundled" else "remote",
                "notice": display(data.get("notice"), 300),
            }
        )

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
            "series_id",
            "job_id",
            "kind",
            "started_at",
        )
        output = {k: view.get(k) for k in keys}
        for key in ("title", "sender_name", "error"):
            output[key] = display(output[key], 300)
        if isinstance(view.get("started_at"), (int, float)) and isinstance(
            view.get("finished_at"), (int, float)
        ):
            output["duration"] = max(0, view["finished_at"] - view["started_at"])
        else:
            output["duration"] = None
        selection = view.get("selection") or {}
        output["selection"] = {
            k: selection.get(k)
            for k in ("workflowTitle", "storyboardTitle", "presetTitles", "channelTitle")
        }
        return output

    async def _selection(self, body, actor):
        presets = body.get("presets")
        if isinstance(presets, str):
            presets = [p.strip() for p in presets.split(",") if p.strip()]
        elif presets is None:
            presets = []
        elif not isinstance(presets, list) or not all(isinstance(p, str) for p in presets):
            raise ServiceError("预设须为编号列表。", code="invalid_presets")
        if len(presets) > 128:
            raise ServiceError("预设数量超过上限。", code="invalid_presets")
        slot_overrides = body.get("slot_overrides")
        if slot_overrides is not None and not isinstance(slot_overrides, dict):
            raise ServiceError("LoRA 覆盖格式不正确。", code="invalid_lora")
        return await self.service.resolve_selection(
            self._text(body.get("workflow")) or None,
            self._text(body.get("storyboard")),
            presets,
            self._text(body.get("channel")) or None,
            actor=actor,
            instance_id=self._instance_id(body),
            candidates=integer(body.get("candidates"), default=1, minimum=1, maximum=16),
            slot_overrides=slot_overrides,
        )

    async def studio_preview(self):
        body = await self._body()
        actor = Actor.panel()
        selection = await self._selection(body, actor)
        preview = await self.service.preview(selection, actor=actor)
        return json_response({"preview": assembly_preview(preview)})

    async def studio_draw(self):
        body = await self._body()
        actor = self._actor_for_session(self._text(body.get("umo")))
        record = await self.service.create_draw(
            self._text(body.get("idea") or body.get("sentence")),
            actor=actor,
            instance_id=self._instance_id(body),
            series=self._text(body.get("series")) or None,
            style=self._text(body.get("style")) or None,
            candidates=integer(body.get("candidates"), default=1, minimum=1, maximum=16),
            layout=self._text(body.get("layout")) or None,
            mode=self._text(body.get("mode") or body.get("output")) or None,
        )
        return self._task_response(record)

    async def studio_trial(self):
        body = await self._body()
        actor = self._actor_for_session(self._text(body.get("umo")))
        record = await self.service.create_trial(
            self._text(body.get("prompt")),
            self._text(body.get("workflow")) or None,
            actor=actor,
            instance_id=self._instance_id(body),
        )
        return self._task_response(record)

    async def task_create(self):
        body = await self._body()
        actor = self._actor_for_session(self._text(body.get("umo")))
        selection = await self._selection(body, actor)
        record = await self.service.create_task(
            selection,
            actor=actor,
            title=self._text(body.get("title")),
            start=body.get("start", True) is not False,
        )
        return self._task_response(record)

    async def _episode(self, client, episode_id):
        episode_id = self._id(episode_id)
        data = await client.call("get_episode", path_params={"episode_id": episode_id})
        if not isinstance(data, dict) or data.get("id") != episode_id:
            raise ServiceError("Mio 未返回所请求的分集。", code="bad_episode", status=502)
        return episode_view(data)

    async def task_detail(self, task_id: str):
        actor = Actor.panel()
        record = self.service.find_record(task_id, actor=actor)
        view = await self.service.task_view(record, actor=actor)
        task = self._task_json(view)
        panels = []
        if (episode_id := task["episode_id"]) and task["status"] != "archived":
            try:
                client = self.service.client_for_task(record)
                panels = (await self._episode(client, episode_id))["panels"]
            except MioApiError as exc:
                task["panels_error"] = exc.message
        task["panels"] = panels
        return json_response({"task": task, "panels": panels})

    async def _panel_image(self, client, episode_id):
        panel_id = self._id(request.query.get("panel_id"), optional=True)
        take_id = self._id(request.query.get("take_id"), optional=True)
        width = integer(request.query.get("width"), default=640, minimum=64, maximum=1600)
        if not panel_id and not take_id:
            episode = await self._episode(client, episode_id)
            cover = next((p for p in episode["panels"] if p["status"] == "adopted"), None)
            if cover:
                panel_id = cover["id"]
            else:
                candidate = next((p for p in episode["panels"] if p["takes"]), None)
                take_id = candidate["takes"][0]["id"] if candidate else None
        if not panel_id and not take_id:
            raise ServiceError("分集还没有可预览的图片。", code="no_image", status=404)
        file = await self.service.delivery.panel_image(
            client,
            episode_id,
            panel_id,
            take_id=take_id,
            width=width,
        )
        return json_response({"src": await image_source(file)})

    async def task_image(self, task_id: str):
        record = self.service.find_record(task_id, actor=Actor.panel())
        if not (episode_id := record.get("episode_id")) or record["status"] == "archived":
            raise ServiceError("任务还没有可预览的分集。", code="no_episode", status=404)
        return await self._panel_image(self.service.client_for_task(record), episode_id)

    async def task_redraw(self, task_id: str):
        body = await self._body()
        actor = Actor.panel()
        record = self.service.find_record(task_id, actor=actor)
        panel = integer(body.get("panel_number", body.get("panel")), minimum=1, maximum=200)
        return self._task_response(
            await self.service.redraw(record, panel, self._text(body.get("prompt")), actor=actor)
        )

    async def task_pick(self, task_id: str):
        body = await self._body()
        actor = Actor.panel()
        record = self.service.find_record(task_id, actor=actor)
        panel = integer(body.get("panel_number", body.get("panel")), minimum=1, maximum=200)
        take = integer(body.get("take_number", body.get("candidate")), minimum=1, maximum=50)
        return json_response(
            {"message": await self.service.pick_take(record, panel, take, actor=actor)}
        )

    async def task_qa(self, task_id: str):
        actor = Actor.panel()
        record = self.service.find_record(task_id, actor=actor)
        return self._task_response(await self.service.qa(record, actor=actor))

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
        result = await self.service.export(
            record,
            actor=actor,
            fmt=fmt,
            layout=layout,
            options=export_options(self._query_export()),
        )
        return self._download(result)

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
            options=export_options(body),
        )
        return json_response({"message": f"已向目标会话发送 {result['filename']}"})

    async def gallery(self):
        instance, client = self._runtime()
        search = self._text(request.query.get("query") or request.query.get("q"))[:256]
        limit = integer(request.query.get("limit"), default=48, minimum=1, maximum=100)
        series_id = self._id(request.query.get("series_id"), optional=True)
        series = await self.service.resources.series(client, limit=100)
        if series_id:
            items = await self.service.resources.episodes(client, series_id, limit=limit)
            title = next((s["title"] for s in series if s["id"] == series_id), "")
            items = [
                {**item, "series_title": title, "instance_id": instance.id}
                for item in items
                if search.casefold() in f"{item['title']} {title}".casefold()
            ]
        else:
            items = await self.service.works(
                actor=Actor.panel(),
                instance_id=instance.id,
                query=search,
                limit=limit,
            )
        return json_response({"items": items, "series": series})

    async def gallery_detail(self, episode_id: str):
        instance, client = self._runtime()
        work = await self._episode(client, episode_id)
        work["instance_id"] = instance.id
        return json_response({"work": work, "panels": work["panels"]})

    async def gallery_image(self, episode_id: str):
        _, client = self._runtime()
        return await self._panel_image(client, self._id(episode_id))

    async def gallery_export(self, episode_id: str):
        instance, _ = self._runtime()
        result = await self.service.export_work(
            {"instance_id": instance.id, "episode_id": self._id(episode_id)},
            actor=Actor.panel(),
            fmt=self._text(request.query.get("format")) or None,
            layout=self._text(request.query.get("layout")) or None,
            options=export_options(self._query_export()),
        )
        return self._download(result)

    async def gallery_send(self, episode_id: str):
        body = await self._body()
        instance = self.service.route(Actor.panel(), self._instance_id(body))
        umo = self._text(body.get("umo"))
        if not umo:
            raise ServiceError("请选择目标会话。", code="unknown_session")
        actor = self._actor_for_session(umo)
        result = await self.service.send_work(
            {"instance_id": instance.id, "episode_id": self._id(episode_id)},
            umo,
            actor=actor,
            fmt=self._text(body.get("format")) or None,
            layout=self._text(body.get("layout")) or None,
            options=export_options(body),
        )
        return json_response({"message": f"已向目标会话发送 {display(result['filename'], 120)}"})

    async def instances(self):
        return json_response(self._instance_payload())

    async def instance_action(self):
        payload = await self._body()
        actor = Actor.panel()
        action = self._text(payload.get("action"))
        payload["id"] = self._id(payload.get("id") or payload.get("instance_id"))
        if payload.get("token") is None and action == "save":
            payload["clear_token"] = True
        try:
            result = await self.service.change_instance(actor, action, payload)
        except MioApiError as exc:
            if action != "probe":
                raise
            result = {"ok": False, "error": exc.message, "code": exc.code}
        if action == "probe":
            return json_response({**self._instance_payload(), "status": result})
        return json_response(self._instance_payload())

    async def sessions(self):
        return json_response(
            {
                "items": [
                    {**row, "rule": self.service.session_policy(row["umo"])}
                    for row in self.service.store.sessions()
                ]
            }
        )

    async def session_rule(self):
        body = await self._body()
        umo = self._text(body.get("umo"))
        if not umo:
            raise ServiceError("请选择目标会话。", code="unknown_session")
        actor = self._actor_for_session(umo)
        changes = body.get("changes")
        if not isinstance(changes, dict):
            raise ServiceError("请提交该会话的设置。", code="invalid_config")
        if changes.get("instance_id"):
            self.service.route(actor, self._id(changes["instance_id"]))
        return json_response({"rule": self.service.set_session_policy(actor, umo, changes)})

    async def webhooks(self):
        return json_response(
            await self.service.webhook(Actor.panel(), "status", self._instance_id())
        )

    async def webhook_action(self):
        body = await self._body()
        action = self._text(body.get("action"))
        if action not in {"register", "test", "remove"}:
            raise ServiceError("请选择注册、测试或移除回调。", code="invalid_action")
        return json_response(
            await self.service.webhook(Actor.panel(), action, self._instance_id(body))
        )

    async def audit(self):
        limit = integer(request.query.get("limit"), default=50, minimum=1, maximum=200)
        return json_response(
            {
                "items": self.service.store.audit_log(limit),
                "quota": self.service.quota(Actor.panel()),
            }
        )
