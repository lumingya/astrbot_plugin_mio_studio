"""Application service shared by chat, panel and the durable task queue."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .config import Settings
from .delivery import FORMATS, MioDelivery
from .governance import Actor, Governance
from .instances import InstanceConfig, InstanceRegistry
from .resources import Resources
from .store import ACTIVE_STATES, AdmissionDenied, TaskStore
from .textfmt import format_task
from .v2_client import DownloadedFile, MioApiError, MioV2Client
from .webhook import (
    WebhookEvent,
    WebhookListener,
    register_webhook,
    test_webhook,
    webhook_deliveries,
)

NOTIFY_MODES = ("summary", "images", "strip", "export", "qa_then_send", "silent")
FINAL_STATES = {"complete", "failed", "cancelled", "interrupted", "missing"}
JOB_STATES = {
    "queued": "queued_remote",
    "running": "running",
    "paused": "paused",
    "blocked": "interrupted",
    "completed": "complete",
    "failed": "failed",
    "canceled": "cancelled",
}
DISPLAY_STATES = {"queued": "ready", "queued_remote": "ready", "dispatching": "preparing"}


class ServiceError(Exception):
    def __init__(self, message: str, *, code: str = "service_error", status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def _integer(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (ValueError, TypeError, OverflowError):
        return default


def _entries(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("items")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


class MioStudioService:
    def __init__(self, context, config, data_dir: Path, logger) -> None:
        self.store = TaskStore(data_dir / "tasks.sqlite3")
        self.settings = Settings(config, self.store)
        self.governance = Governance(config)
        self.instances = InstanceRegistry(self.settings.primary())
        self.resources = Resources()
        self.delivery = MioDelivery(context, data_dir, logger)
        self.logger = logger
        self._wake = asyncio.Event()
        self._closing = False
        self._poller: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()
        self._controls = asyncio.Lock()
        self._hook_hits: set[str] = set()
        self._hook_address: tuple[str, int] | None = None
        self.listener = WebhookListener(
            self._hook_secret, self.store.remember_delivery, self._hook_event
        )

    def cfg(self, section, key, default=None):
        return self.settings.cfg(section, key, default)

    def session_policy(self, umo: str) -> dict[str, Any]:
        return self.store.session_overrides(umo) if umo else {}

    def _policy_value(self, umo, section, key, default=None):
        return self.session_policy(umo).get(key, self.cfg(section, key, default))

    def config_view(self) -> dict[str, Any]:
        return self.settings.view()

    def export_format(self, umo: str = "") -> str:
        fmt = self._policy_value(umo, "export", "format", "html")
        return fmt if fmt in FORMATS else "html"

    async def reload_config(self) -> None:
        configs = [self.settings.primary()]
        configs.extend(InstanceConfig(**item) for item in self.store.list_instances())
        await self.instances.configure(configs, self.store.default_instance())
        host = self.cfg("webhook", "bind_host", "127.0.0.1")
        port = _integer(self.cfg("webhook", "bind_port", 8790), 8790, 1, 65535)
        address = (host, port) if self.cfg("webhook", "enabled", False) else None
        if address == self._hook_address:
            return
        await self.listener.close()
        self._hook_address = None
        if address:
            try:
                await self.listener.start(*address)
                self._hook_address = address
            except (OSError, ValueError):
                self.logger.warning("[mio_studio] Webhook 监听不可用，继续使用轮询。")

    async def update_config(self, changes):
        mio = changes.get("mio") if isinstance(changes, dict) else None
        if isinstance(mio, dict) and "base_url" in mio:
            current = self.instances.resolve("primary")
            try:
                new = replace(current, base_url=mio["base_url"] or current.base_url)
            except ValueError as exc:
                raise ServiceError(str(exc)) from None
            bound = self.store.list_tasks(instance_id="primary", limit=1)
            if new.base_url != current.base_url and (
                bound or self.store.get_webhook("primary").get("hook_id")
            ):
                raise ServiceError(
                    "此连接已关联作品或回调。请用新的实例 ID 添加另一台 Mio。", status=409
                )
        self.settings.update(changes)
        await self.reload_config()
        self.ensure_poller()
        return self.config_view()

    async def change_instance(self, actor: Actor, action: str, body: dict):
        self.governance.require(actor, "admin")
        instance_id = body.get("id") or ""
        if action == "probe" and not body.get("base_url"):
            return await self.status(actor=actor, instance_id=instance_id or None)
        async with self._controls:
            if action == "default":
                self.instances.resolve(instance_id)
                self.store.set_default_instance(instance_id)
            elif action == "remove":
                if instance_id == "primary":
                    raise ServiceError("主实例不能移除，可在连接设置更换地址。")
                if self.store.get_webhook(instance_id).get("hook_id"):
                    raise ServiceError("请先在交付页面解除该实例的 Webhook。", status=409)
                try:
                    if not self.store.remove_instance(instance_id):
                        raise ServiceError("该实例不存在。", status=404)
                except ValueError as exc:
                    raise ServiceError(str(exc), status=409) from None
            elif action in {"save", "probe"}:
                old = next(
                    (item for item in self.instances.public() if item["id"] == instance_id), None
                )
                token = body.get("token") or ""
                if token and (not isinstance(token, str) or not token.startswith("mio_")):
                    raise ServiceError("请粘贴 Mio 开放 API 的 mio_ 令牌。")
                try:
                    instance = InstanceConfig(
                        instance_id,
                        body.get("label") or (old or {}).get("label") or instance_id,
                        body.get("base_url") or (old or {}).get("base_url") or "",
                        token,
                        body.get("timeout", (old or {}).get("timeout", 60)),
                        body.get("cloud", (old or {}).get("cloud", False)),
                    )
                except ValueError as exc:
                    raise ServiceError(str(exc)) from None
                same_address = old and instance.base_url == old["base_url"]
                if (
                    not old
                    and action == "save"
                    and self.store.list_tasks(
                        instance_id=instance_id,
                        limit=1,
                    )
                ):
                    raise ServiceError(
                        "该 ID 曾用于其他实例。请为新实例填写不同的 ID。", status=409
                    )
                if same_address and not token and body.get("clear_token") is not True:
                    token = self.instances.resolve(instance_id).token
                    instance = replace(instance, token=token)
                if action == "probe":
                    client = MioV2Client(
                        instance.base_url, instance.token, timeout=min(15, instance.timeout)
                    )
                    try:
                        meta = await client.probe()
                        if instance.token:
                            identity = await client.call("whoami")
                            return {
                                "ok": True,
                                "api": meta["api"],
                                "version": meta["server"],
                                "scopes": identity.get("scopes", []),
                            }
                        return {
                            "ok": False,
                            "api": meta["api"],
                            "version": meta["server"],
                            "error": "连接成功，还需要填写 Mio 开放 API 令牌。",
                        }
                    finally:
                        await client.close()
                if (
                    old
                    and not same_address
                    and (
                        self.store.list_tasks(instance_id=instance_id, limit=1)
                        or self.store.get_webhook(instance_id).get("hook_id")
                    )
                ):
                    raise ServiceError(
                        "此实例已关联作品或回调。请使用新实例 ID 添加另一台 Mio。",
                        status=409,
                    )
                if instance_id == "primary":
                    settings = {
                        "base_url": instance.base_url,
                        "request_timeout": int(instance.timeout),
                        "cloud": instance.cloud,
                    }
                    if token:
                        settings["api_token"] = token
                    elif body.get("clear_token") is True:
                        settings["api_token"] = None
                    self.settings.update({"mio": settings})
                else:
                    self.store.save_instance(asdict(instance))
            else:
                raise ServiceError("未知的实例操作。")
            await self.reload_config()
            self.store.audit(
                f"instance_{action}", actor_id=actor.scoped_id, instance_id=instance_id
            )
            return {"instances": self.instances.public(), "default_id": self.instances.default_id}

    async def start(self) -> None:
        await self.reload_config()
        # A POST can have succeeded before a shutdown. Never replay it blindly.
        for task in self.store.pending():
            if task["status"] == "dispatching":
                self.store.update(
                    task["id"],
                    expected_status="dispatching",
                    status="interrupted",
                    error="提交结果未知。请到 Mio 核对是否已接单，再决定是否重试。",
                    finished_at=time.time(),
                )
        for task in self.store.list_tasks(limit=400):
            self._schedule_notification(task)
        self.ensure_poller()

    def route(self, actor: Actor, instance_id: str | None = None) -> InstanceConfig:
        self.governance.require(actor, "read")
        return self.instances.resolve(
            instance_id, self.session_policy(actor.session).get("instance_id")
        )

    def client_for_task(self, record):
        instance = self.instances.resolve(record["instance_id"])
        bound = record.get("backend_address")
        if bound and instance.base_url != bound:
            raise ServiceError(
                "这项任务属于原来的 Mio 地址。请恢复原地址后重试。",
                code="instance_binding_changed",
                status=409,
            )
        return self.instances.get(instance.id)

    async def status(self, *, actor: Actor, instance_id: str | None = None) -> dict[str, Any]:
        instance = self.route(actor, instance_id)
        client = self.instances.get(instance.id)
        records = self._visible(actor, 400, instance_id=instance.id)
        view = {
            "configured": bool(instance.token),
            "instance_id": instance.id,
            "baseUrl": instance.base_url if actor.is_admin else instance.label,
            "ok": False,
            "tasks": {
                "total": len(records),
                "active": sum(item["status"] in ACTIVE_STATES | {"queued"} for item in records),
            },
        }
        try:
            meta = await client.probe()
            view.update(api=meta["api"], version=meta["server"])
            if not instance.token:
                view.update(error="请在插件配置里填写 Mio 开放 API 的 mio_ 令牌。")
                return view
            identity = await client.call("whoami")
            view.update(ok=True, scopes=identity.get("scopes", []))
        except MioApiError as exc:
            view.update(error=exc.message, code=exc.code)
        return view

    async def catalog(self, *, actor: Actor, force: bool = False, instance_id: str | None = None):
        instance = self.route(actor, instance_id)
        return await self.resources.catalog(self.instances.get(instance.id), force=force)

    def catalog_defaults(self) -> dict[str, Any]:
        presets = self.cfg("defaults", "preset_ids", "")
        return {
            "workflows": self.cfg("defaults", "workflow_id", ""),
            "storyboards": self.cfg("defaults", "storyboard_id", ""),
            "presets": presets
            if isinstance(presets, list)
            else [p.strip() for p in presets.split(",") if p.strip()],
            "channels": self.cfg("defaults", "channel_id", ""),
            "layouts": self.cfg("export", "layout_id", ""),
        }

    @staticmethod
    def pick(items, token, *, label):
        value = str(token or "").strip().lstrip("#")
        if value.isdecimal() and len(value) <= 9 and 1 <= int(value) <= len(items):
            return items[int(value) - 1]
        for item in items:
            if value and value in (item.get("id"), item.get("title"), item.get("name")):
                return item
        raise ServiceError(
            f"没有找到{label}。请先刷新目录，检查编号或名称。",
            code="resource_not_found",
            status=404,
        )

    async def resolve_selection(
        self,
        workflow,
        storyboard,
        presets,
        channel,
        *,
        actor: Actor,
        instance_id: str | None = None,
        candidates: int = 1,
        slot_overrides=None,
    ):
        instance = self.route(actor, instance_id)
        cat = await self.catalog(actor=actor, instance_id=instance.id)
        defaults = self.catalog_defaults()
        room = self.session_policy(actor.session)

        def choose(kind, token, label, fallback="", first=False):
            actual = token if token not in (None, "", "0") else fallback
            if actual:
                return self.pick(cat.get(kind, []), actual, label=label)
            return cat[kind][0] if first and cat.get(kind) else None

        story = choose("storyboards", storyboard, "分镜", defaults["storyboards"], True)
        if not story:
            raise ServiceError("Mio 还没有分镜。请先创建分镜，再装配任务。")
        preset_tokens = presets or room.get("preset_ids") or defaults["presets"]
        if isinstance(preset_tokens, str):
            preset_tokens = preset_tokens.split(",")
        selected_presets = [
            self.pick(cat.get("presets", []), entry, label="预设")
            for entry in dict.fromkeys(
                p.strip() for p in preset_tokens if p.strip() and p.strip() != "0"
            )
        ][:128]
        source = choose("channels", channel, "图像通道", defaults["channels"])
        is_cloud = bool(source and source.get("cloud"))
        if is_cloud and workflow not in (None, "", "0"):
            raise ServiceError("请选择工作流或云端通道，两者不能同时使用。")
        wf = (
            None
            if is_cloud
            else choose("workflows", workflow, "工作流", defaults["workflows"], True)
        )
        if not is_cloud and not wf:
            raise ServiceError("没有可用工作流。请在 Mio 导入工作流或选择云端图像通道。")
        generation = {"candidates": _integer(candidates, 1, 1, 16)}
        if is_cloud:
            generation["cloud_channel_id"] = source["id"]
        else:
            generation["workflow_id"] = wf["id"]
        return {
            "instance_id": instance.id,
            "storyboard": story,
            "workflow": wf,
            "presets": selected_presets,
            "channel": source,
            "body": {
                "storyboard_id": story["id"],
                "preset_ids": [item["id"] for item in selected_presets],
                "generation": generation,
                **({"slot_overrides": slot_overrides} if slot_overrides else {}),
            },
        }

    async def preview(self, selection, *, actor: Actor):
        self.governance.require(actor, "read")
        instance = self.route(actor, selection["instance_id"])
        return await self.instances.get(instance.id).call(
            "preview_assembly", body=selection["body"]
        )

    def _admit(
        self,
        actor: Actor,
        instance_id: str,
        *,
        kind: str,
        title: str,
        body: dict,
        selection: dict | None = None,
        text: str = "",
        cloud: bool = False,
        delivery: dict | None = None,
        series_id: str = "",
        episode_id: str = "",
    ):
        instance = self.route(actor, instance_id)
        if not instance.token:
            raise ServiceError(
                "请先填写 Mio 开放 API 的 mio_ 令牌。", code="missing_token", status=401
            )
        policy = self.governance.policy(actor, cloud=cloud or instance.cloud, text=text)
        task_id = uuid.uuid4().hex
        payload = {
            "id": task_id,
            "kind": kind,
            "title": title.strip()[:120] or "未命名作品",
            "instance_id": instance.id,
            "backend_address": instance.base_url,
            "umo": actor.session,
            "platform": actor.platform,
            "sender_id": actor.id,
            "sender_name": actor.name,
            "selection": selection or {},
            "request": body,
            "delivery": delivery or {},
            "series_id": series_id,
            "episode_id": episode_id,
            "notified": False,
        }
        try:
            record = self.store.admit(
                payload,
                actor_key=actor.scoped_id,
                daily_limit=policy.daily_limit,
                user_limit=policy.user_active_limit,
                cooldown=policy.cooldown_seconds,
                cloud=policy.cloud,
                cloud_limit=policy.cloud_daily_limit,
            )
        except AdmissionDenied as exc:
            self.store.audit(
                kind, actor_id=actor.scoped_id, instance_id=instance.id, outcome=exc.code
            )
            raise ServiceError(exc.message, code=exc.code, status=429) from None
        self.store.audit(kind, actor_id=actor.scoped_id, instance_id=instance.id, task_id=task_id)
        self.ensure_poller()
        return record

    async def create_task(self, selection, *, actor: Actor, title=None, start=True):
        story = selection["storyboard"]
        body = dict(selection["body"])
        title = title or story["title"]
        body.update(title=title[:120], auto_start=bool(start), adopt_first=bool(start))
        return self._admit(
            actor,
            selection["instance_id"],
            kind="workshop",
            title=title,
            body=body,
            selection={
                "storyboardTitle": story["title"],
                "workflowTitle": (selection["workflow"] or {}).get("title"),
                "presetTitles": [p["title"] for p in selection["presets"]],
                "channelTitle": (selection["channel"] or {}).get("title"),
            },
            cloud=bool((body.get("generation") or {}).get("cloud_channel_id")),
            text=title,
        )

    async def create_draw(
        self,
        idea,
        *,
        actor: Actor,
        instance_id=None,
        series=None,
        style=None,
        candidates=1,
        layout=None,
        mode=None,
    ):
        instance = self.route(actor, instance_id)
        idea = str(idea or "").strip()
        if not 1 <= len(idea) <= 2000:
            raise ServiceError("请写一句不超过 2000 字的创作灵感。")
        self.governance.policy(actor, cloud=instance.cloud, text=idea)
        client = self.instances.get(instance.id)
        series_id = ""
        profile_id = "profile_default"
        if series:
            available = await self.resources.series(client, limit=100)
            chosen = self.pick(available, series, label="系列")
            series_id = chosen["id"]
            existing = self.store.find_remote(instance.id, series_id=series_id)
            if not actor.is_admin:
                if not existing:
                    raise ServiceError("只能向自己创建的系列添加新分集。", status=403)
                self.governance.authorize_task(actor, existing)
            remote = await client.call("get_series", path_params={"series_id": series_id})
            profile_id = remote.get("default_profile_id") or profile_id
        cat = None
        style_label = ""
        if style:
            cat = await self.resources.catalog(client)
            preset = self.pick(cat.get("presets", []), style, label="风格预设")
            style_label = preset["title"]
            entries = [f"{part['key']}：{part['value']}" for part in preset.get("entries", [])[:16]]
            idea = (idea + "\n画风参考：" + style_label + "\n" + "\n".join(entries))[:3500]
        if layout:
            cat = cat if cat is not None else await self.resources.catalog(client)
            layout = self.pick(cat.get("layouts", []), layout, label="导出模板")["id"]
        cloud = await self._uses_cloud_profile(client, profile_id)
        cloud_policy = self.governance.policy(actor, cloud=instance.cloud or cloud, text=idea)
        return self._admit(
            actor,
            instance.id,
            kind="draw",
            title=idea.splitlines()[0][:48],
            series_id=series_id,
            body={
                "idea": idea,
                "profile_id": profile_id,
                "candidates": _integer(candidates, 1, 1, 16),
                "cloud_allowed": cloud_policy.cloud,
            },
            selection={"presetTitles": [style_label]} if style_label else {},
            delivery={
                **({"layout_id": layout} if layout else {}),
                "notify_mode": mode if mode in NOTIFY_MODES else ("export" if layout else "strip"),
            },
            text=idea,
            cloud=cloud,
        )

    @staticmethod
    async def _uses_cloud_profile(client, profile_id):
        profile = await client.call("get_profile", path_params={"profile_id": profile_id})
        if not isinstance(profile, dict) or profile.get("id") != profile_id:
            raise ServiceError("Mio 没有返回有效的出图配置，请在绘页检查默认工作流。", status=502)
        return profile.get("cloud_shape") is True

    async def _verify_draw_profiles(self, client, episode, base_profile, cloud_allowed):
        if cloud_allowed:
            return
        profile_ids = {base_profile}
        profile_ids.update(
            value
            for panel in _entries(episode.get("panels"))
            if (value := (panel.get("overrides") or {}).get("profile_id"))
        )
        for profile in profile_ids:
            if await self._uses_cloud_profile(client, profile):
                raise ServiceError(
                    "出图配置已改为云端。请重新提交，让插件检查云端权限和额度。", status=403
                )

    async def create_trial(self, prompt, workflow=None, *, actor: Actor, instance_id=None):
        instance = self.route(actor, instance_id)
        prompt = str(prompt or "").strip()
        if not 1 <= len(prompt) <= 8000:
            raise ServiceError("试画提示词应有 1 到 8000 字。")
        self.governance.policy(actor, cloud=instance.cloud, text=prompt)
        cat = await self.catalog(actor=actor, instance_id=instance.id)
        workflows = cat.get("workflows", [])
        default = workflow or self.catalog_defaults()["workflows"]
        chosen = (
            self.pick(workflows, default, label="工作流")
            if default
            else next(iter(workflows), None)
        )
        if not chosen:
            raise ServiceError("没有可用于试画的工作流，请先在 Mio 导入工作流。")
        return self._admit(
            actor,
            instance.id,
            kind="trial",
            title="试画 · " + prompt[:40],
            body={"workflow_id": chosen["id"], "prompt": prompt},
            selection={"workflowTitle": chosen["title"]},
            delivery={"notify_mode": "images"},
            text=prompt,
        )

    async def works(self, *, actor: Actor, instance_id=None, query="", limit=30):
        instance = self.route(actor, instance_id)
        rows = await self.resources.works(self.instances.get(instance.id), query=query, limit=limit)
        return [{**item, "instance_id": instance.id} for item in rows]

    async def export_work(self, work, *, actor: Actor, fmt=None, layout=None, options=None):
        self.route(actor, work.get("instance_id"))
        fmt = fmt or self.export_format(actor.session)
        return self._file_view(
            await self._file(work, fmt=fmt, layout=layout, options=options, umo=actor.session),
            fmt,
        )

    async def send_work(self, work, umo, *, actor: Actor, fmt=None, layout=None, options=None):
        self.route(actor, work.get("instance_id"))
        self._authorize_destination(actor, umo)
        fmt = fmt or self.export_format(umo)
        file = await self._file(work, fmt=fmt, layout=layout, options=options, umo=umo)
        size = _integer(self._policy_value(umo, "delivery", "max_file_mb", 50), 50, 1, 200)
        await self.delivery.send_file(umo, file, max_size=size * 1024**2, as_image=fmt == "strip")
        return self._file_view(file, fmt)

    async def _episode(self, record):
        if not record.get("episode_id"):
            raise ServiceError("该任务尚未生成分集。", status=409)
        client = self.client_for_task(record)
        episode = await client.call("get_episode", path_params={"episode_id": record["episode_id"]})
        if not isinstance(episode, dict) or episode.get("id") != record["episode_id"]:
            raise ServiceError("无法确认 Mio 的分集信息。", status=502)
        panels = sorted(_entries(episode.get("panels")), key=lambda panel: panel["order"])
        return client, episode, panels

    @staticmethod
    def _panel(panels, number):
        if type(number) is not int or not 1 <= number <= len(panels):
            raise ServiceError("没有这幕。请查看任务详情中的分幕编号。", status=404)
        return panels[number - 1]

    def _check_episode_idle(self, record):
        if any(
            item["episode_id"] == record["episode_id"]
            and item["instance_id"] == record["instance_id"]
            for item in self.store.pending()
            if item.get("episode_id")
        ):
            raise ServiceError("这个分集正在进行其他操作，请等待完成后再试。", status=409)

    async def redraw(self, record, panel_number, prompt=None, *, actor: Actor):
        self.governance.authorize_task(actor, record)
        text = str(prompt or "").strip()
        if len(text) > 8000:
            raise ServiceError("新的提示词不能超过 8000 字。")
        async with self._controls:
            self._check_episode_idle(record)
            client, episode, panels = await self._episode(record)
            panel = self._panel(panels, panel_number)
            series = await client.call(
                "get_series", path_params={"series_id": episode["series_id"]}
            )
            generation = series.get("generation") or {}
            profile_id = (panel.get("overrides") or {}).get("profile_id")
            if not profile_id and generation:
                cloud = bool((generation.get("profile") or {}).get("cloud_shape"))
            else:
                profile_id = profile_id or series.get("default_profile_id") or "profile_default"
                cloud = await self._uses_cloud_profile(client, profile_id)
            return self._admit(
                actor,
                record["instance_id"],
                kind="redraw",
                title=f"重画 · 第 {panel_number} 幕",
                body={"panel_id": panel["id"], "revision": episode["revision"], "prompt": text},
                series_id=episode["series_id"],
                episode_id=episode["id"],
                text=text,
                cloud=cloud,
            )

    async def pick_take(self, record, panel_number, take_number, *, actor: Actor):
        self.governance.authorize_task(actor, record)
        async with self._controls:
            self._check_episode_idle(record)
            client, episode, panels = await self._episode(record)
            panel = self._panel(panels, panel_number)
            takes = [
                take
                for take in _entries(episode.get("takes"))
                if take.get("panel_id") == panel["id"]
                and take.get("status") in {"candidate", "adopted"}
            ]
            if type(take_number) is not int or not 1 <= take_number <= len(takes):
                raise ServiceError("没有这个候选图。请检查分幕候选编号。", status=404)
            await client.call(
                "adopt_take",
                path_params={"episode_id": episode["id"], "take_id": takes[take_number - 1]["id"]},
            )
        self.store.audit(
            "adopt_take",
            actor_id=actor.scoped_id,
            instance_id=record["instance_id"],
            task_id=record["id"],
        )
        return f"第 {panel_number} 幕已采纳候选图 {take_number}。"

    async def qa(self, record, *, actor: Actor):
        self.governance.authorize_task(actor, record)
        async with self._controls:
            self._check_episode_idle(record)
            _, episode, _ = await self._episode(record)
            if not _entries(episode.get("takes")):
                raise ServiceError("这个分集还没有可质检的图片。", status=409)
            return self._admit(
                actor,
                record["instance_id"],
                kind="qa",
                title=f"质检 · {record['title']}",
                body={},
                episode_id=episode["id"],
                series_id=episode["series_id"],
                delivery={"notify_mode": "summary"},
            )

    def quota(self, actor: Actor):
        self.governance.require(actor, "read")
        usage = self.store.quota(actor.scoped_id)
        limit = self.cfg("governance", "daily_limit", 8)
        cooldown = self.cfg("governance", "cooldown_seconds", 10)
        remaining = max(0, limit - usage["used"]) if limit else None
        cloud_limit = self.cfg("governance", "cloud_daily_limit", 3)
        wait = max(0, int((usage["last_at"] or 0) + cooldown - time.time() + 0.999))
        return {
            **usage,
            "daily_limit": limit,
            "remaining": remaining,
            "cloud_remaining": max(0, cloud_limit - usage["cloud_used"]) if cloud_limit else None,
            "cooldown": wait,
            "cooldown_remaining": wait,
            "queue_length": self.store.queue_length(),
        }

    def set_session_policy(self, actor: Actor, umo, changes):
        self.governance.authorize_session_config(actor, umo)
        return self.settings.set_session_policy(umo, changes)

    def _visible(self, actor, limit, *, instance_id=None):
        role = self.governance.require(actor, "read")
        return self.store.list_tasks(
            umo=None if role == "admin" else actor.session,
            sender_id=None if role == "admin" else actor.id,
            instance_id=instance_id,
            limit=_integer(limit, 50, 1, 400),
        )

    def find_record(self, token, *, actor: Actor):
        role = self.governance.require(actor, "read")
        if actor.session:
            self.store.seen_session(actor.session, actor.platform, actor.name)
        value = str(token or "").strip().lstrip("#")
        if not value:
            record = self.store.latest_for(
                actor.session or None, sender_id=None if role == "admin" else actor.id
            )
        elif value.isdecimal() and len(value) < 16:
            record = self.store.by_num(int(value))
        else:
            record = self.store.get(value)
        if not record:
            raise ServiceError(
                "没找到这个任务。用 /mio jobs 查看编号。", code="task_not_found", status=404
            )
        self.governance.authorize_task(actor, record, capability="read")
        return record

    def merge_view(self, record, live):
        live = live or {}
        status = record.get("status", "unknown")
        fields = (
            "id",
            "num",
            "title",
            "instance_id",
            "series_id",
            "episode_id",
            "job_id",
            "kind",
            "umo",
            "platform",
            "sender_id",
            "sender_name",
            "selection",
            "created_at",
            "started_at",
            "finished_at",
            "notified",
            "error",
        )
        return {
            **{k: record.get(k) for k in fields},
            "status": status,
            "statusKey": DISPLAY_STATES.get(status, status),
            "albumId": record.get("episode_id"),
            "done": live.get("done", record.get("done", 0)),
            "failed": live.get("failed", record.get("failed", 0)),
            "total": live.get("total", record.get("total", 0)),
            "queuePosition": self.store.queue_position(record["id"])
            if status == "queued"
            else None,
        }

    async def task_views(self, *, actor: Actor, limit=50, force=False, instance_id=None):
        return [
            self.merge_view(record, None)
            for record in self._visible(actor, limit, instance_id=instance_id)
        ], 0

    async def task_view(self, record, *, actor: Actor, force=True):
        self.governance.authorize_task(actor, record, capability="read")
        if (
            force
            and record.get("job_id")
            and record["status"] in {"queued_remote", "running", "paused"}
        ):
            await self._refresh(record)
        return self.merge_view(self.store.get(record["id"]) or record, None)

    @staticmethod
    def _job_stats(job):
        items = _entries(job.get("items"))
        return {
            "done": sum(item.get("state") in {"complete", "skipped"} for item in items),
            "failed": sum(item.get("state") in {"failed", "uncertain"} for item in items),
            "total": len(items),
        }

    async def _dispatch(self, record):
        task_id = record["id"]
        try:
            client = self.client_for_task(record)
            data = await self._submit(client, record)
            job = data.get("job") or {}
            job_id = data.get("job_id") or job.get("id")
            status = JOB_STATES.get(job.get("state"), "queued_remote") if job_id else "standby"
            if data.get("start_error"):
                status = "failed"
            updated = self.store.update(
                task_id,
                expected_status="dispatching",
                status=status,
                job_id=job_id or "",
                series_id=(data.get("series") or {}).get("id") or record.get("series_id", ""),
                episode_id=(data.get("episode") or {}).get("id") or record.get("episode_id", ""),
                error="工作已装配，Mio 未能启动出图。请检查工作流和通道后继续。"
                if data.get("start_error")
                else "",
                **self._job_stats(job),
                **({"finished_at": time.time()} if status in FINAL_STATES else {}),
            )
        except (MioApiError, ServiceError) as exc:
            status = "interrupted" if getattr(exc, "retryable", False) else "failed"
            updated = self.store.update(
                task_id,
                expected_status="dispatching",
                status=status,
                error=exc.message,
                finished_at=time.time(),
            )
        except Exception:
            self.logger.warning("[mio_studio] 无法处理 Mio 的提交响应。")
            updated = self.store.update(
                task_id,
                expected_status="dispatching",
                status="interrupted",
                error="提交结果未知，请先到 Mio 查看任务是否已经创建。",
                finished_at=time.time(),
            )
        if updated:
            self._schedule_notification(updated)
        self._wake.set()

    async def _submit(self, client, record):
        body = {**record["request"], "idempotency_key": "astrbot:" + record["id"]}
        if record.get("run_existing"):
            job = await client.call(
                "render_episode",
                path_params={"episode_id": record["episode_id"]},
                body={"adopt_first": True, "idempotency_key": body["idempotency_key"] + ":render"},
            )
            return {"job": job}
        if record["kind"] == "workshop":
            return await client.call("create_workshop_task", body=body)
        if record["kind"] == "trial":
            workflow = body.pop("workflow_id")
            return await client.call(
                "trial_workflow", path_params={"workflow_id": workflow}, body=body
            )
        if record["kind"] == "draw":
            series_id = record["series_id"]
            if not series_id:
                series = await client.call("create_series", body={"title": record["title"]})
                series_id = series["id"]
                self.store.update(record["id"], expected_status="dispatching", series_id=series_id)
            path = {"series_id": series_id}
            episode = await client.call(
                "generate_episode",
                path_params=path,
                body={"sentence": body["idea"], "title": record["title"]},
                timeout=max(120, client.timeout),
            )
            self.store.update(record["id"], expected_status="dispatching", episode_id=episode["id"])
            await self._verify_draw_profiles(
                client, episode, body["profile_id"], body["cloud_allowed"]
            )
            job = await client.call(
                "render_episode",
                path_params={"episode_id": episode["id"]},
                body={
                    "profile_id": body["profile_id"],
                    "candidates": body["candidates"],
                    "adopt_first": True,
                    "qa": body["candidates"] > 1,
                    "idempotency_key": body["idempotency_key"],
                },
            )
            return {"series": {"id": series_id}, "episode": episode, "job": job}
        if record["kind"] == "redraw":
            episode_path = {"episode_id": record["episode_id"]}
            if body.get("prompt"):
                await client.call(
                    "patch_panel",
                    path_params={**episode_path, "panel_id": body["panel_id"]},
                    body={
                        "changes": {"overrides": {"raw_prompt": body["prompt"]}},
                        "revision": body["revision"],
                    },
                )
            job = await client.call(
                "render_episode",
                path_params=episode_path,
                body={
                    "panel_ids": [body["panel_id"]],
                    "candidates": 1,
                    "adopt_replace": True,
                    "idempotency_key": body["idempotency_key"],
                },
            )
            return {"job": job}
        if record["kind"] == "qa":
            job = await client.call(
                "qa_episode",
                path_params={"episode_id": record["episode_id"]},
                body={"auto_adopt": False, "idempotency_key": body["idempotency_key"]},
            )
            return {"job": job}
        raise ServiceError("无法识别当前任务类型。")

    async def _refresh(self, record):
        if not record.get("job_id"):
            return
        try:
            job = await self.client_for_task(record).call(
                "get_job",
                path_params={"job_id": record["job_id"]},
            )
        except (MioApiError, ServiceError) as exc:
            if exc.code == "instance_binding_changed":
                changes = {"status": "interrupted", "error": exc.message}
            elif exc.status == 404:
                changes = {"status": "missing", "error": "Mio 中已找不到该作业。"}
            else:
                return
        else:
            if not isinstance(job, dict) or job.get("id") != record["job_id"]:
                return
            status = JOB_STATES.get(job.get("state"))
            if status is None:
                return
            changes = {"status": status, **self._job_stats(job)}
            if status in {"failed", "interrupted"}:
                changes["error"] = "部分分幕未完成。请在 Mio 检查工作流、模型和执行记录。"
            else:
                changes["error"] = ""
        if changes["status"] in FINAL_STATES:
            changes["finished_at"] = time.time()
        current = self.store.get(record["id"])
        if not current or current.get("job_id") != record["job_id"]:
            return
        changes = {k: v for k, v in changes.items() if current.get(k) != v}
        if changes:
            current = self.store.update(record["id"], expected_status=record["status"], **changes)
        if current:
            self._schedule_notification(current)

    async def task_action(self, record, action, *, actor: Actor):
        self.governance.authorize_task(
            actor,
            record,
            capability="remove" if action == "remove" else "control",
        )
        async with self._controls:
            current = self.store.get(record["id"])
            if not current:
                raise ServiceError("任务已移除。", status=404)
            result = await self._control(current, action, actor)
        self.store.audit(
            action,
            actor_id=actor.scoped_id,
            instance_id=current.get("instance_id", ""),
            task_id=current["id"],
        )
        self.ensure_poller()
        return result

    async def _control(self, record, action, actor):
        status, task_id = record["status"], record["id"]
        number = record["num"]
        if action == "remove":
            if status in ACTIVE_STATES or status in {"queued", "paused"}:
                raise ServiceError("请先停止任务，等它结束后再移除记录。", status=409)
            self.store.remove(task_id)
            return f"已移除任务 #{number} 的本地记录。Mio 图库中的作品仍可查看。"
        if action in {"pause", "cancel"} and status == "queued":
            new_state = "paused" if action == "pause" else "cancelled"
            if self.store.update(task_id, expected_status="queued", status=new_state):
                return f"任务 #{number} {'已暂停' if action == 'pause' else '已停止'}。"
            raise ServiceError("任务刚开始提交，请稍后重试。", status=409)
        if action == "resume" and status == "paused" and not record.get("job_id"):
            if self.store.update(task_id, expected_status="paused", status="queued"):
                return f"任务 #{number} 已重新排队。"
        if action == "cancel" and status == "paused" and not record.get("job_id"):
            if self.store.update(task_id, expected_status="paused", status="cancelled"):
                return f"任务 #{number} 已停止。"
        job_id = record.get("job_id")
        if not job_id:
            if action == "resume" and status in {"standby", "failed"} and record.get("episode_id"):
                self._enqueue_existing_render(record, actor)
                return f"任务 #{number} 已重新排队。"
            raise ServiceError("此任务没有可控制的作业。请在 Mio 核对后重新创建。", status=409)
        client = self.client_for_task(record)
        path = {"job_id": job_id}
        if action == "resume" and status in {"paused", "failed", "cancelled"}:
            source = (record.get("request") or {}).get("generation") or {}
            self.governance.policy(
                actor,
                cloud=self.instances.resolve(record["instance_id"]).cloud
                or bool(source.get("cloud_channel_id")),
            )
            if not self.store.claim_control(task_id, status, self.governance.global_limit):
                raise ServiceError("生成队列已满，请等正在运行的任务结束后再继续。", status=429)
            try:
                op = "resume_job" if status == "paused" else "retry_job"
                job = await client.call(
                    op, path_params=path, body={} if op == "retry_job" else None
                )
                self.store.update(
                    task_id,
                    expected_status="dispatching",
                    status=JOB_STATES.get(job.get("state"), "queued_remote"),
                    notified=False,
                    finished_at=None,
                    **self._job_stats(job),
                )
            except BaseException:
                # Keep tracking the same job; the server may have accepted the request.
                self.store.update(task_id, expected_status="dispatching", status="queued_remote")
                self.ensure_poller()
                raise
            return f"任务 #{number} 已请求继续。"
        if action in {"pause", "cancel"} and status in {"queued_remote", "running", "paused"}:
            job = await client.call(f"{action}_job", path_params=path)
            if self.store.update(
                task_id,
                expected_status=status,
                status=JOB_STATES.get(job.get("state"), status),
                **self._job_stats(job),
            ):
                return f"任务 #{number} 已请求{'暂停' if action == 'pause' else '停止'}。"
        raise ServiceError("当前任务状态不支持这个操作。请刷新任务状态。", status=409)

    def _enqueue_existing_render(self, record, actor):
        source = (record.get("request") or {}).get("generation") or {}
        self.governance.policy(
            actor,
            cloud=self.instances.resolve(record["instance_id"]).cloud
            or bool(source.get("cloud_channel_id")),
        )
        if not self.store.update(
            record["id"],
            expected_status=record["status"],
            status="queued",
            run_existing=True,
            notified=False,
            finished_at=None,
        ):
            raise ServiceError("任务状态已变化，请刷新后再试。", status=409)

    async def _file(self, record, *, fmt=None, layout=None, options=None, umo="") -> DownloadedFile:
        episode = record.get("episode_id")
        if not episode:
            raise ServiceError("任务还没有生成可取件的分集。", status=409)
        client = self.client_for_task(record)
        fmt = fmt or self.export_format(umo)
        template = (
            layout
            or (record.get("delivery") or {}).get("layout_id")
            or self._policy_value(
                umo,
                "export",
                "layout_id",
                "",
            )
        )
        if template and template != "0":
            cat = await self.resources.catalog(client)
            template = self.pick(cat.get("layouts", []), template, label="导出模板")["id"]
        defaults = {
            key: self.cfg("export", key, fallback)
            for key, fallback in {
                "image_profile": "auto",
                "show_captions": True,
                "show_prompts": False,
                "theme_color": "",
                "signature": "",
            }.items()
        }
        return await self.delivery.export(
            client,
            episode,
            fmt=fmt,
            template_id=template or None,
            options={**defaults, **(options or {})},
        )

    @staticmethod
    def _file_view(file, fmt):
        return {
            "path": file.path,
            "filename": file.filename,
            "contentType": file.content_type,
            "size": file.size,
            "format": fmt,
        }

    async def export(self, record, *, actor: Actor, fmt=None, layout=None, options=None):
        self.governance.authorize_task(actor, record, capability="read")
        fmt = fmt or self.export_format(actor.session)
        file = await self._file(record, fmt=fmt, layout=layout, options=options, umo=actor.session)
        return self._file_view(file, fmt)

    def _authorize_destination(self, actor, umo):
        self.governance.require(actor, "read")
        if not umo or (not actor.is_admin and actor.session != umo):
            raise ServiceError("请选择当前会话，或由管理员选择已知会话。", status=403)
        if umo != actor.session and not any(item["umo"] == umo for item in self.store.sessions()):
            raise ServiceError("找不到该会话。请先在目标会话运行一条 Mio 指令。", status=404)

    async def send_export(
        self, record, umo, *, actor: Actor, fmt=None, layout=None, note="", options=None
    ):
        self.governance.authorize_task(actor, record, capability="read")
        self._authorize_destination(actor, umo)
        fmt = fmt or self.export_format(umo)
        file = await self._file(record, fmt=fmt, layout=layout, options=options, umo=umo)
        max_mb = _integer(self.cfg("delivery", "max_file_mb", 50), 50, 1, 200)
        await self.delivery.send_file(
            umo, file, note=note, max_size=max_mb * 1024**2, as_image=fmt == "strip"
        )
        return self._file_view(file, fmt)

    async def send_images(self, record, umo, *, actor: Actor, platform="", header=""):
        self.governance.authorize_task(actor, record, capability="read")
        self._authorize_destination(actor, umo)
        if not record.get("episode_id"):
            raise ServiceError("任务还没有可发送的分幕图片。", status=409)
        limit = _integer(self.cfg("delivery", "max_images", 30), 30, 1, 100)
        bundle = await self.delivery.frames(
            self.client_for_task(record),
            record["episode_id"],
            limit=limit,
            take_fallback=record.get("kind") == "trial",
        )
        return await self.delivery.send_images(
            umo,
            bundle,
            platform=platform or record.get("platform") or "",
            send_mode=self._policy_value(umo, "delivery", "send_mode", "auto"),
            include_captions=bool(self.cfg("delivery", "include_captions", True)),
            header=header,
            forward_sender_name=self.cfg("delivery", "forward_sender_name", "Mio 绘页"),
            forward_sender_uin=self.cfg("delivery", "forward_sender_uin", "10000"),
        )

    async def _notify(self, record):
        task_id, status = record["id"], record["status"]
        if not self.store.claim_notification(task_id, status):
            return
        umo = record.get("umo")
        if not umo:
            return
        view = self.merge_view(record, None)
        mode = (record.get("delivery") or {}).get("notify_mode") or self._policy_value(
            umo,
            "delivery",
            "notify_mode",
            "summary",
        )
        if mode == "silent":
            return
        if status == "complete" and record.get("kind") == "qa":
            try:
                _, episode, panels = await self._episode(record)
                report = self._qa_text(episode, panels)
            except (MioApiError, ServiceError) as exc:
                report = f"质检已完成，但暂时无法读取报告：{exc.message}"
            await self.delivery.send_text(umo, f"质检任务 #{view['num']}\n{report}")
            return
        if status != "complete" or mode == "summary":
            await self.delivery.send_text(umo, format_task(view))
            return
        actor = Actor.panel()
        try:
            if mode == "qa_then_send":
                client = self.client_for_task(record)
                job = await client.call(
                    "qa_episode",
                    path_params={"episode_id": record["episode_id"]},
                    body={"auto_adopt": True, "idempotency_key": "astrbot:notify-qa:" + task_id},
                )
                await self.delivery.wait_job(client, job["id"])
            if mode in {"images", "qa_then_send"}:
                await self.send_images(record, umo, actor=actor)
            elif mode in {"strip", "export"}:
                await self.send_export(
                    record, umo, actor=actor, fmt="strip" if mode == "strip" else None
                )
        except (MioApiError, ServiceError) as exc:
            await self.delivery.send_text(
                umo,
                f"任务 #{record['num']} 已完成，自动交付失败：{exc.message}。可用 /mio get 取件。",
            )

    def _schedule_notification(self, record):
        if record["status"] in FINAL_STATES and not record.get("notified"):
            self._spawn(self._notify(record), wake=False)

    @staticmethod
    def _qa_text(episode, panels):
        results = {}
        for take in _entries(episode.get("takes")):
            qa = take.get("qa")
            if not isinstance(qa, dict) or type(qa.get("score")) not in {float, int}:
                continue
            if take["panel_id"] not in results or qa["score"] > results[take["panel_id"]]["score"]:
                results[take["panel_id"]] = qa
        lines = []
        for number, panel in enumerate(panels[:25], 1):
            qa = results.get(panel["id"])
            if not qa:
                continue
            issues = "；".join(str(issue)[:100] for issue in (qa.get("issues") or [])[:2])
            issues = re.sub(r"\bmio_[A-Za-z0-9_-]{12,}\b", "[已隐藏]", issues)
            lines.append(f"第 {number} 幕：{qa['score']:.0%} · {issues or '未发现明显问题'}")
        return "\n".join(lines) or "Mio 暂未返回可用的评分。请确认已配置视觉质检模型。"

    def _task_done(self, task, wake):
        self._background.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.logger.warning("[mio_studio] 后台操作未完成。")
        if wake:
            self._wake.set()

    def _spawn(self, work, *, wake=True):
        task = asyncio.create_task(work)
        self._background.add(task)
        task.add_done_callback(lambda item: self._task_done(item, wake))

    def ensure_poller(self):
        if self._closing:
            return
        self._wake.set()
        if self._poller is None or self._poller.done():
            self._poller = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self):
        quiet = 0
        while not self._closing:
            self._wake.clear()
            for _ in range(self.governance.global_limit):
                record = self.store.claim_next(self.governance.global_limit)
                if not record:
                    break
                self._spawn(self._dispatch(record))
            pending = self.store.pending()
            if not pending:
                return
            self._hook_hits = set()
            who = [
                item
                for item in pending
                if item["status"] in {"queued_remote", "running", "preparing"}
            ]
            if who:
                await asyncio.gather(*(self._refresh(item) for item in who))
                quiet = 0
            else:
                quiet += 1
            base = _integer(self.cfg("delivery", "poll_interval", 5), 5, 2, 120)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=min(30, base * (1 + quiet)))
            except TimeoutError:
                pass

    def _hook_secret(self, instance_id):
        return self.store.get_webhook(instance_id).get("secret")

    def _hook_event(self, instance_id: str, event: WebhookEvent):
        record = self.store.find_remote(instance_id, job_id=event.job_id) if event.job_id else None
        if not record and event.episode_id:
            record = self.store.find_remote(instance_id, episode_id=event.episode_id)
        if record and record["status"] in {"queued_remote", "running", "preparing"}:
            self._hook_hits.add(record["id"])
            self.ensure_poller()

    async def webhook(self, actor: Actor, action: str, instance_id: str | None = None):
        self.governance.require(actor, "admin")
        inst = self.route(actor, instance_id)
        client = self.instances.get(inst.id)
        current = self.store.get_webhook(inst.id)
        if action == "register":
            if current.get("hook_id"):
                return {"registered": True, "listening": bool(self._hook_address)}
            public = self.cfg("webhook", "public_url", "")
            if not self._hook_address or not public:
                raise ServiceError("请先启用回调监听，并填写 Mio 能访问的回调基址。")
            result = await register_webhook(client, public, inst.id)
            self.store.set_webhook(inst.id, {"hook_id": result.hook_id, "secret": result.secret})
            self.store.audit("webhook_register", actor_id=actor.scoped_id, instance_id=inst.id)
            return {"registered": True, "listening": True}
        hook_id = current.get("hook_id")
        if action == "test" and hook_id:
            return await test_webhook(client, hook_id)
        if action == "remove" and hook_id:
            await client.call("delete_webhook", path_params={"hook_id": hook_id})
            self.store.set_webhook(inst.id, {})
            return {"registered": False}
        return {
            "registered": bool(hook_id),
            "listening": bool(self._hook_address),
            "deliveries": await webhook_deliveries(client, hook_id) if hook_id else [],
        }

    async def close(self):
        self._closing = True
        await self.listener.close()
        work = [*self._background, *([self._poller] if self._poller else [])]
        for task in work:
            task.cancel()
        await asyncio.gather(*work, return_exceptions=True)
        await self.instances.close()
