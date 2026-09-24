"""Service layer shared by the chat commands and the control-panel web API.

Responsibilities:
* configuration access (+ writes from the panel, persisted through AstrBotConfig)
* numbered resource catalogue from Mio (workflows / storyboards / presets / channels / layouts)
* task creation (Mio production ``assemble`` + ``start``) and bookkeeping
* progress polling and completion notifications (strategy selectable in the panel)
* result retrieval: server-side export (html/zip/pdf) or frame images
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api.event import MessageChain
import astrbot.api.message_components as Comp

from .mio_client import MioClient, MioError
from .store import TaskStore
from .textfmt import STATUS_LABEL, format_task, progress_of, status_key

NOTIFY_MODES = ("images", "summary", "export", "silent")
FORMATS = ("html", "zip", "pdf")
IMAGE_PROFILES = ("auto", "archive", "clean", "publish")
SEND_MODES = ("auto", "forward", "separate", "batch")
FINAL_STATES = {"complete", "failed", "interrupted", "cancelled"}
FORWARD_PLATFORMS = {"aiocqhttp", "napcat", "llonebot", "lagrange"}
CATALOG_TTL = 15.0
TASKS_TTL = 2.0
MAX_EXPORT_FILES = 40


class ServiceError(Exception):
    def __init__(self, message: str, *, code: str = "service_error", status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _safe_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", str(name or "")).strip(" .")
    return name[:120] or "export"


class MioStudioService:
    def __init__(self, context, config, data_dir: Path, logger) -> None:
        self.context = context
        self.config = config
        self.data_dir = data_dir
        self.logger = logger
        self.client = MioClient()
        self.store = TaskStore(data_dir / "tasks.json")
        self._catalog: dict[str, Any] | None = None
        self._catalog_at = 0.0
        self._catalog_lock = asyncio.Lock()
        self._tasks_cache: list[dict[str, Any]] = []
        self._tasks_at = 0.0
        self._poller: asyncio.Task | None = None
        self._closing = False
        self.reload_config()

    # ----------------------------------------------------------------- config
    def cfg(self, section: str, key: str, default: Any = None) -> Any:
        sec = self.config.get(section) if isinstance(self.config, dict) else None
        if isinstance(sec, dict) and key in sec and sec[key] is not None:
            return sec[key]
        return default

    def reload_config(self) -> None:
        self.client.configure(
            base_url=str(self.cfg("mio", "base_url", "http://127.0.0.1:8777") or "http://127.0.0.1:8777"),
            token=str(self.cfg("mio", "api_token", "") or ""),
            timeout=float(_int(self.cfg("mio", "request_timeout", 60), 60, 5, 600)),
        )

    def config_view(self) -> dict[str, Any]:
        token = str(self.cfg("mio", "api_token", "") or "")
        return {
            "mio": {
                "base_url": self.client.base_url,
                "api_token_set": bool(token),
                "api_token_masked": (token[:4] + "…" + token[-4:]) if len(token) >= 12 else ("已设置" if token else ""),
                "request_timeout": _int(self.cfg("mio", "request_timeout", 60), 60, 5, 600),
            },
            "defaults": {
                "channel_id": str(self.cfg("defaults", "channel_id", "") or ""),
                "workflow_id": str(self.cfg("defaults", "workflow_id", "") or ""),
                "preset_ids": self.default_preset_ids(),
                "title_template": str(self.cfg("defaults", "title_template", "{story} · {preset}") or "{story} · {preset}"),
                "seed_mode": str(self.cfg("defaults", "seed_mode", "random") or "random"),
                "seed": _int(self.cfg("defaults", "seed", 1), 1, 0, 2**32 - 1),
                "concurrency": _int(self.cfg("defaults", "concurrency", 0), 0, 0, 8),
            },
            "export": {
                "layout_id": str(self.cfg("export", "layout_id", "") or ""),
                "format": self.export_format(),
                "image_profile": self.image_profile(),
                "show_captions": bool(self.cfg("export", "show_captions", True)),
                "show_prompts": bool(self.cfg("export", "show_prompts", False)),
                "theme_color": str(self.cfg("export", "theme_color", "") or ""),
                "signature": str(self.cfg("export", "signature", "") or ""),
            },
            "delivery": {
                "notify_mode": self.notify_mode(),
                "send_mode": str(self.cfg("delivery", "send_mode", "auto") or "auto"),
                "include_captions": bool(self.cfg("delivery", "include_captions", True)),
                "max_images": _int(self.cfg("delivery", "max_images", 30), 30, 1, 200),
                "poll_interval": _int(self.cfg("delivery", "poll_interval", 5), 5, 2, 120),
                "forward_sender_name": str(self.cfg("delivery", "forward_sender_name", "Mio 绘页") or "Mio 绘页"),
                "forward_sender_uin": str(self.cfg("delivery", "forward_sender_uin", "10000") or "10000"),
            },
            "permissions": {
                "admin_only": bool(self.cfg("permissions", "admin_only", False)),
                "allowed_sessions": list(self.cfg("permissions", "allowed_sessions", []) or []),
            },
        }

    ALLOWED_KEYS = {
        "mio": {"base_url": str, "api_token": str, "request_timeout": int},
        "defaults": {"channel_id": str, "workflow_id": str, "preset_ids": str, "title_template": str, "seed_mode": str, "seed": int, "concurrency": int},
        "export": {"layout_id": str, "format": str, "image_profile": str, "show_captions": bool, "show_prompts": bool, "theme_color": str, "signature": str},
        "delivery": {"notify_mode": str, "send_mode": str, "include_captions": bool, "max_images": int, "poll_interval": int, "forward_sender_name": str, "forward_sender_uin": str},
        "permissions": {"admin_only": bool, "allowed_sessions": list},
    }

    def update_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Apply a partial ``{section: {key: value}}`` update and persist it."""
        if not isinstance(changes, dict):
            raise ServiceError("配置格式不正确")
        touched = 0
        for section, values in changes.items():
            allowed = self.ALLOWED_KEYS.get(section)
            if not allowed or not isinstance(values, dict):
                continue
            target = self.config.setdefault(section, {}) if isinstance(self.config, dict) else None
            if not isinstance(target, dict):
                continue
            for key, value in values.items():
                kind = allowed.get(key)
                if kind is None:
                    continue
                if kind is bool:
                    value = bool(value)
                elif kind is int:
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        raise ServiceError(f"{section}.{key} 必须是整数")
                elif kind is list:
                    if isinstance(value, str):
                        value = [v.strip() for v in re.split(r"[\n,]", value) if v.strip()]
                    value = [str(v) for v in (value or []) if str(v).strip()]
                else:
                    value = str(value if value is not None else "").strip()
                    if key == "preset_ids" and isinstance(values.get(key), list):
                        value = ",".join(str(v) for v in values[key] if str(v).strip())
                if key == "api_token" and value == "":
                    continue  # never clear the token through a masked form
                if key == "notify_mode" and value not in NOTIFY_MODES:
                    raise ServiceError("无效的通知策略")
                if key == "format" and value not in FORMATS:
                    raise ServiceError("无效的导出格式")
                if key == "image_profile" and value not in IMAGE_PROFILES:
                    raise ServiceError("无效的图片处理档位")
                if key == "send_mode" and value not in SEND_MODES:
                    raise ServiceError("无效的发送方式")
                if key == "seed_mode" and value not in ("random", "fixed"):
                    raise ServiceError("无效的种子模式")
                target[key] = value
                touched += 1
        if touched and hasattr(self.config, "save_config"):
            self.config.save_config()
        self.reload_config()
        self.ensure_poller()
        return self.config_view()

    def notify_mode(self) -> str:
        mode = str(self.cfg("delivery", "notify_mode", "summary") or "summary")
        return mode if mode in NOTIFY_MODES else "summary"

    def export_format(self) -> str:
        fmt = str(self.cfg("export", "format", "html") or "html").lower()
        return fmt if fmt in FORMATS else "html"

    def image_profile(self) -> str:
        prof = str(self.cfg("export", "image_profile", "auto") or "auto")
        return prof if prof in IMAGE_PROFILES else "auto"

    def default_preset_ids(self) -> list[str]:
        raw = self.cfg("defaults", "preset_ids", "")
        if isinstance(raw, list):
            return [str(v).strip() for v in raw if str(v).strip()]
        return [v.strip() for v in str(raw or "").split(",") if v.strip()]

    def export_options(self) -> dict[str, Any]:
        return {
            "showCaptions": bool(self.cfg("export", "show_captions", True)),
            "showPrompts": bool(self.cfg("export", "show_prompts", False)),
            "themeColor": str(self.cfg("export", "theme_color", "") or ""),
            "signature": str(self.cfg("export", "signature", "") or ""),
        }

    # ------------------------------------------------------------- permission
    def session_allowed(self, umo: str, is_admin: bool) -> bool:
        if is_admin:
            return True
        if bool(self.cfg("permissions", "admin_only", False)):
            return False
        allowed = [str(v) for v in (self.cfg("permissions", "allowed_sessions", []) or []) if str(v).strip()]
        if not allowed:
            return True
        return any(umo == a or umo.startswith(a) for a in allowed)

    # ------------------------------------------------------------------ status
    async def status(self) -> dict[str, Any]:
        info: dict[str, Any] = {"configured": self.client.configured, "baseUrl": self.client.base_url, "ok": False, "patched": False}
        if not self.client.configured:
            info["error"] = "尚未配置 Mio 服务地址"
            return info
        try:
            health = await self.client.health()
            info.update({"ok": True, "version": health.get("version"), "health": health})
            caps = await self.client.capabilities()
            info["patched"] = bool(caps.get("productionQueue")) and "html" in (caps.get("albumExportFormats") or [])
            info["capabilities"] = {k: caps.get(k) for k in ("version", "catalog", "albumExportFormats", "productionQueue", "operations")}
            if not info["patched"]:
                info["error"] = "Mio 未应用配套补丁（缺少 catalog / albums/export / production 路由）"
        except MioError as exc:
            info["error"] = exc.message
            info["errorCode"] = exc.code
        tasks = self.store.all()
        info["tasks"] = {"total": len(tasks), "active": sum(1 for t in tasks if t.get("status") not in FINAL_STATES and t.get("status") != "missing")}
        info["poller"] = bool(self._poller and not self._poller.done())
        return info

    # ----------------------------------------------------------------- catalog
    async def catalog(self, force: bool = False) -> dict[str, Any]:
        async with self._catalog_lock:
            if not force and self._catalog and time.monotonic() - self._catalog_at < CATALOG_TTL:
                return self._catalog
            data = await self.client.catalog()
            cat = {k: list(data.get(k) or []) for k in ("workflows", "storyboards", "presets", "layouts", "channels", "collections")}
            cat["fetchedAt"] = time.time()
            self._catalog = cat
            self._catalog_at = time.monotonic()
            return cat

    def catalog_defaults(self) -> dict[str, Any]:
        return {
            "workflows": str(self.cfg("defaults", "workflow_id", "") or ""),
            "presets": self.default_preset_ids(),
            "channels": str(self.cfg("defaults", "channel_id", "") or ""),
            "layouts": str(self.cfg("export", "layout_id", "") or ""),
        }

    @staticmethod
    def pick(items: list[dict[str, Any]], token: str | int | None, *, label: str) -> dict[str, Any]:
        """Resolve a 1-based number, an id or a unique title prefix to a catalogue item."""
        if token is None or token == "":
            raise ServiceError(f"缺少{label}编号")
        text = str(token).strip()
        if text.isdigit():
            index = int(text)
            if not 1 <= index <= len(items):
                raise ServiceError(f"{label}编号 {index} 超出范围（1–{len(items)}）")
            return items[index - 1]
        for item in items:
            if item.get("id") == text:
                return item
        matches = [it for it in items if str(it.get("title") or "").startswith(text)]
        if len(matches) == 1:
            return matches[0]
        raise ServiceError(f"找不到{label}「{text}」，请用 /mio_ls 查看编号")

    def _default_item(self, items: list[dict[str, Any]], configured: str, label: str, *, required: bool) -> dict[str, Any] | None:
        if configured:
            for item in items:
                if item.get("id") == configured:
                    return item
            raise ServiceError(f"面板里配置的默认{label}（{configured}）在 Mio 中不存在，请重新选择")
        if items and required:
            return items[0]
        if required:
            raise ServiceError(f"Mio 中没有可用的{label}")
        return None

    async def resolve_selection(self, workflow: str | None, storyboard: str | None, presets: list[str] | None, channel: str | None) -> dict[str, Any]:
        """Turn user tokens (numbers / ids / '0' for default) into concrete Mio resources."""
        cat = await self.catalog(force=True)
        channels = cat["channels"]
        if channel and channel != "0":
            channel_item = self.pick(channels, channel, label="通道")
        else:
            configured = str(self.cfg("defaults", "channel_id", "") or "")
            channel_item = self._default_item(channels, configured, "图像通道", required=True)
            if not configured:
                comfy = [c for c in channels if c.get("provider") == "comfyui"]
                channel_item = comfy[0] if comfy else channel_item
        uses_workflow = channel_item.get("provider") == "comfyui" or channel_item.get("usesWorkflow") is True
        workflow_item = None
        if uses_workflow:
            if workflow and workflow != "0":
                workflow_item = self.pick(cat["workflows"], workflow, label="工作流")
            else:
                workflow_item = self._default_item(cat["workflows"], str(self.cfg("defaults", "workflow_id", "") or ""), "工作流", required=True)
        story_item = self.pick(cat["storyboards"], storyboard, label="分幕")
        preset_items: list[dict[str, Any]] = []
        tokens = [t for t in (presets or []) if t not in ("", "0")]
        if tokens:
            for token in tokens:
                item = self.pick(cat["presets"], token, label="预设")
                if item not in preset_items:
                    preset_items.append(item)
        else:
            for pid in self.default_preset_ids():
                item = next((p for p in cat["presets"] if p.get("id") == pid), None)
                if item is None:
                    raise ServiceError(f"面板里配置的默认预设（{pid}）在 Mio 中不存在，请重新选择")
                preset_items.append(item)
            if not preset_items and cat["presets"]:
                preset_items.append(cat["presets"][0])
        if not preset_items:
            raise ServiceError("Mio 中没有可用的预设（角色 / 场景变量集）")
        if len(preset_items) > 20:
            raise ServiceError("一次最多选择 20 个预设")
        return {"channel": channel_item, "workflow": workflow_item, "storyboard": story_item, "presets": preset_items}

    # ------------------------------------------------------------------ tasks
    def _title_for(self, selection: dict[str, Any]) -> str:
        template = str(self.cfg("defaults", "title_template", "{story} · {preset}") or "{story} · {preset}")
        story = selection["storyboard"].get("title") or "画册"
        preset = " + ".join(str(p.get("title") or p.get("id")) for p in selection["presets"])
        workflow = (selection.get("workflow") or {}).get("title") or ""
        channel = selection["channel"].get("title") or selection["channel"].get("id") or ""
        try:
            title = template.format(story=story, preset=preset, workflow=workflow, channel=channel, date=time.strftime("%m-%d"), time=time.strftime("%H:%M"))
        except (KeyError, IndexError, ValueError):
            title = f"{story} · {preset}"
        title = re.sub(r"\s+", " ", title).strip(" ·")
        return (title or story)[:120]

    async def create_task(self, selection: dict[str, Any], *, umo: str, sender_id: str = "", sender_name: str = "", platform: str = "", title: str | None = None, start: bool = True) -> dict[str, Any]:
        seed_mode = str(self.cfg("defaults", "seed_mode", "random") or "random")
        seed = _int(self.cfg("defaults", "seed", 1), 1, 0, 2**32 - 1)
        concurrency = _int(self.cfg("defaults", "concurrency", 0), 0, 0, 8) or None
        workflow = selection.get("workflow")
        title = (title or "").strip() or self._title_for(selection)
        request_id = f"astrbot-{uuid.uuid4().hex}"
        try:
            task = await self.client.assemble(
                story_id=selection["storyboard"]["id"],
                presets=[{"kind": p.get("category") or "characters", "id": p["id"]} for p in selection["presets"]],
                channel_id=selection["channel"]["id"],
                workflow_id=workflow["id"] if workflow else None,
                title=title,
                request_id=request_id,
                seed=seed,
                seed_enabled=(seed_mode == "fixed" and workflow is not None),
                concurrency=concurrency,
            )
        except MioError as exc:
            raise ServiceError(f"Mio 装配失败：{exc.message}", code=exc.code, status=exc.status or 502)
        if not isinstance(task, dict) or not task.get("id"):
            raise ServiceError("Mio 未返回任务信息", code="bad_response", status=502)
        record = self.store.add(
            {
                "id": task["id"],
                "title": task.get("title") or title,
                "albumId": task.get("albumId"),
                "umo": umo,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "platform": platform,
                "status": task.get("status") or "standby",
                "notified": False,
                "selection": {
                    "workflowId": workflow["id"] if workflow else None,
                    "workflowTitle": workflow.get("title") if workflow else None,
                    "storyboardId": selection["storyboard"]["id"],
                    "storyboardTitle": selection["storyboard"].get("title"),
                    "presetIds": [p["id"] for p in selection["presets"]],
                    "presetTitles": [str(p.get("title") or p.get("id")) for p in selection["presets"]],
                    "channelId": selection["channel"]["id"],
                    "channelTitle": selection["channel"].get("title") or selection["channel"].get("id"),
                },
            }
        )
        if start:
            try:
                await self.client.start_task(task["id"], concurrency=concurrency)
                record = self.store.update(task["id"], status="ready") or record
            except MioError as exc:
                self.store.update(task["id"], status="standby", error=exc.message)
                raise ServiceError(f"任务 #{record['num']} 已创建但启动失败：{exc.message}（可用 /mio_resume {record['num']} 重试）", code=exc.code, status=exc.status or 502)
        self._tasks_at = 0.0
        self.ensure_poller()
        return record

    async def mio_tasks(self, force: bool = False) -> list[dict[str, Any]]:
        if not force and time.monotonic() - self._tasks_at < TASKS_TTL:
            return self._tasks_cache
        self._tasks_cache = await self.client.production_tasks()
        self._tasks_at = time.monotonic()
        return self._tasks_cache

    def merge_view(self, record: dict[str, Any], live: dict[str, Any] | None) -> dict[str, Any]:
        done, failed, total = progress_of(live)
        key = status_key(live) if live is not None else ("missing" if record.get("status") == "missing" else str(record.get("status") or "unknown"))
        view = {**record, "live": bool(live), "statusKey": key, "done": done, "failed": failed, "total": total}
        if live:
            view["albumId"] = live.get("albumId") or record.get("albumId")
            report = live.get("cancelReport") if isinstance(live.get("cancelReport"), dict) else {}
            view["error"] = live.get("error") or report.get("message") or report.get("reason") or ""
            view["queuePosition"] = live.get("queuePosition")
            view["title"] = live.get("title") or record.get("title")
        else:
            view["error"] = record.get("error")
        return view

    async def task_views(self, *, umo: str | None = None, limit: int = 10, force: bool = False) -> tuple[list[dict[str, Any]], int]:
        records = self.store.all()
        if umo:
            records = [r for r in records if r.get("umo") == umo]
        records = records[-limit:] if limit else records
        try:
            live = {t.get("id"): t for t in await self.mio_tasks(force=force)}
        except MioError:
            live = None
        views = []
        for record in records:
            views.append(self.merge_view(record, live.get(record["id"]) if live is not None else None))
        others = 0
        if live is not None:
            mine = {r.get("id") for r in self.store.all()}
            others = sum(1 for tid in live if tid not in mine)
        return views, others

    async def task_view(self, record: dict[str, Any], *, force: bool = True) -> dict[str, Any]:
        """Merge the queue list entry (paused / queuePosition / albumId) with the detail (error / cancelReport)."""
        live: dict[str, Any] | None
        try:
            live = await self.client.production_task(record["id"])
        except MioError as exc:
            if exc.status == 404:
                live = None
                if record.get("status") != "missing":
                    self.store.update(record["id"], status="missing")
                    record = {**record, "status": "missing"}
            else:
                raise ServiceError(f"读取任务失败：{exc.message}", code=exc.code, status=exc.status or 502)
        if live is not None:
            try:
                entry = next((t for t in await self.mio_tasks(force=force) if t.get("id") == record["id"]), None)
            except MioError:
                entry = None
            if entry:
                live = {**entry, **{k: v for k, v in live.items() if v is not None}}
        return self.merge_view(record, live)

    def find_record(self, token: str | int | None, *, umo: str | None = None) -> dict[str, Any]:
        if token in (None, "", "0"):
            record = self.store.latest_for(umo) or self.store.latest_for(None)
            if not record:
                raise ServiceError("还没有任务，先用 /mio_use 创建一个")
            return record
        text = str(token).strip().lstrip("#")
        if text.isdigit():
            record = self.store.by_num(int(text))
            if record:
                return record
            raise ServiceError(f"没有编号为 #{text} 的任务（/mio_jobs 查看）")
        record = self.store.get(text)
        if record:
            return record
        raise ServiceError(f"找不到任务「{text}」")

    async def task_action(self, record: dict[str, Any], action: str) -> str:
        num = record["num"]
        try:
            if action == "pause":
                await self.client.production("pause", {"id": record["id"]})
                return f"⏸ 任务 #{num} 已暂停"
            if action == "resume":
                view = await self.task_view(record)
                if view.get("statusKey") == "paused":
                    await self.client.production("resume", {"id": record["id"]})
                    self.store.update(record["id"], notified=False)
                    self.ensure_poller()
                    return f"▶️ 任务 #{num} 已继续"
                if view.get("statusKey") in ("running", "ready", "preparing"):
                    return f"ℹ️ 任务 #{num} 正在进行中（{view.get('done', 0)}/{view.get('total', 0)} 幕）"
                if view.get("statusKey") == "complete":
                    return f"ℹ️ 任务 #{num} 已全部完成，用 /mio_get {num} 取件"
                await self.client.start_task(record["id"])
                self.store.update(record["id"], notified=False, status="ready")
                self._tasks_at = 0.0
                self.ensure_poller()
                return f"▶️ 任务 #{num} 已重新开始（补跑未完成的分幕）"
            if action == "cancel":
                await self.client.production("cancel", {"id": record["id"]})
                self.store.update(record["id"], notified=True, status="cancelled")
                return f"⏹ 任务 #{num} 已停止（已生成的分幕保留，可 /mio_resume {num} 补跑）"
            if action == "remove":
                try:
                    await self.client.production("remove", {"id": record["id"], "deleteAlbums": False})
                except MioError as exc:
                    if exc.status != 404:
                        raise
                self.store.remove(record["id"])
                return f"🗑 任务 #{num} 已移除（Mio 图库中的画册保留）"
        except MioError as exc:
            raise ServiceError(f"操作失败：{exc.message}", code=exc.code, status=exc.status or 502)
        raise ServiceError("未知操作")

    # ---------------------------------------------------------------- results
    def _export_dir(self) -> Path:
        path = self.data_dir / "exports"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _prune_exports(self) -> None:
        files = sorted(self._export_dir().glob("*"), key=lambda p: p.stat().st_mtime if p.exists() else 0)
        for stale in files[:-MAX_EXPORT_FILES]:
            try:
                stale.unlink()
            except OSError:
                pass

    async def resolve_layout(self, token: str | None) -> dict[str, Any] | None:
        cat = await self.catalog()
        layouts = cat["layouts"]
        if token and token != "0":
            return self.pick(layouts, token, label="导出模板")
        configured = str(self.cfg("export", "layout_id", "") or "")
        if configured:
            for item in layouts:
                if item.get("id") == configured:
                    return item
            self.logger.warning(f"[mio_studio] 默认导出模板 {configured} 不存在，改用 Mio 第一个模板")
        return layouts[0] if layouts else None

    async def export(self, record: dict[str, Any], *, fmt: str | None = None, layout: str | None = None) -> dict[str, Any]:
        album_id = record.get("albumId")
        if not album_id:
            raise ServiceError("该任务还没有关联画册")
        fmt = (fmt or self.export_format()).lower()
        if fmt not in FORMATS:
            raise ServiceError("导出格式只支持 html / zip / pdf")
        layout_item = await self.resolve_layout(layout) if fmt == "html" else None
        try:
            data, filename, content_type = await self.client.export_album(
                [album_id], fmt, layout_id=layout_item.get("id") if layout_item else None, image_profile=self.image_profile() if self.image_profile() != "auto" else None, options=self.export_options() if fmt == "html" else None
            )
        except MioError as exc:
            raise ServiceError(f"导出失败：{exc.message}", code=exc.code, status=exc.status or 502)
        filename = _safe_filename(filename or f"{record.get('title')}.{fmt}")
        if not filename.lower().endswith("." + fmt):
            filename += "." + fmt
        path = self._export_dir() / f"{record['num']:04d}-{filename}"
        path.write_bytes(data)
        self._prune_exports()
        return {"path": path, "filename": filename, "contentType": content_type, "size": len(data), "layout": layout_item, "format": fmt}

    async def frames(self, album_id: str, *, limit: int | None = None) -> dict[str, Any]:
        """Download (and cache) the frame images of an album."""
        try:
            album = await self.client.album(album_id)
        except MioError as exc:
            raise ServiceError(f"读取画册失败：{exc.message}", code=exc.code, status=exc.status or 502)
        cache = self.data_dir / "cache" / re.sub(r"[^A-Za-z0-9_.-]", "_", album_id)
        cache.mkdir(parents=True, exist_ok=True)
        out = []
        steps = sorted((s for s in album.get("steps", []) if isinstance(s, dict)), key=lambda s: s.get("stepIndex", 0))
        for step in steps[: limit or None]:
            image = step.get("image") or ""
            entry = {"index": step.get("stepIndex", len(out)), "name": step.get("name", ""), "caption": step.get("caption", ""), "path": None}
            if image:
                digest = hashlib.sha1(image.encode("utf-8")).hexdigest()[:16]
                existing = next(cache.glob(f"{digest}.*"), None)
                if existing is None:
                    try:
                        data, mime = await self.client.asset_bytes(image)
                    except MioError as exc:
                        self.logger.warning(f"[mio_studio] 下载分幕图片失败 {image}: {exc.message}")
                        data, mime = b"", ""
                    if data:
                        ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(mime, "png")
                        existing = cache / f"{digest}.{ext}"
                        existing.write_bytes(data)
                entry["path"] = existing
            out.append(entry)
        return {"album": album, "frames": out}

    # --------------------------------------------------------------- delivery
    async def send(self, umo: str, chain: MessageChain) -> bool:
        try:
            return bool(await self.context.send_message(umo, chain))
        except Exception as exc:
            self.logger.warning(f"[mio_studio] send_message({umo}) failed: {exc}")
            return False

    async def send_text(self, umo: str, text: str) -> bool:
        return await self.send(umo, MessageChain(chain=[Comp.Plain(text)]))

    async def send_export(self, record: dict[str, Any], umo: str, *, fmt: str | None = None, layout: str | None = None, note: str = "") -> dict[str, Any]:
        result = await self.export(record, fmt=fmt, layout=layout)
        layout_name = (result.get("layout") or {}).get("title") if result.get("layout") else None
        text = note or f"📦 任务 #{record['num']}《{record.get('title')}》导出完成：{result['filename']}（{result['size'] / 1024:.0f} KB"
        if not note:
            text += f"，模板 {layout_name}）" if layout_name else "）"
        await self.send(umo, MessageChain(chain=[Comp.Plain(text)]))
        await self.send(umo, MessageChain(chain=[Comp.File(name=result["filename"], file=str(result["path"]))]))
        return result

    async def send_images(self, record: dict[str, Any], umo: str, *, platform: str = "", header: str = "") -> int:
        album_id = record.get("albumId")
        if not album_id:
            raise ServiceError("该任务还没有关联画册")
        max_images = _int(self.cfg("delivery", "max_images", 30), 30, 1, 200)
        bundle = await self.frames(album_id, limit=max_images)
        frames = [f for f in bundle["frames"] if f.get("path")]
        album = bundle["album"]
        include_captions = bool(self.cfg("delivery", "include_captions", True))
        header = header or f"🖼 任务 #{record['num']}《{album.get('title') or record.get('title')}》 {len(frames)} 幕"
        if not frames:
            await self.send_text(umo, header + "\n（还没有可发送的图片）")
            return 0
        send_mode = str(self.cfg("delivery", "send_mode", "auto") or "auto")
        platform = platform or str(record.get("platform") or "") or (umo.split(":", 1)[0] if ":" in umo else "")
        use_forward = send_mode == "forward" or (send_mode == "auto" and platform in FORWARD_PLATFORMS and len(frames) > 1)

        def caption(frame: dict[str, Any]) -> str:
            text = f"第 {int(frame['index']) + 1} 幕"
            if frame.get("name"):
                text += f" · {frame['name']}"
            if include_captions and frame.get("caption"):
                text += f"\n{frame['caption']}"
            return text

        if use_forward:
            name = str(self.cfg("delivery", "forward_sender_name", "Mio 绘页") or "Mio 绘页")
            uin = str(self.cfg("delivery", "forward_sender_uin", "10000") or "10000")
            nodes = [Comp.Node(uin=uin, name=name, content=[Comp.Plain(header)])]
            for frame in frames:
                nodes.append(Comp.Node(uin=uin, name=name, content=[Comp.Plain(caption(frame)), Comp.Image.fromFileSystem(str(frame["path"]))]))
            if await self.send(umo, MessageChain(chain=[Comp.Nodes(nodes=nodes)])):
                return len(frames)
            self.logger.warning("[mio_studio] forward message failed, falling back to separate messages")
        await self.send_text(umo, header)
        if send_mode == "batch" or (send_mode == "auto" and not use_forward and len(frames) <= 4):
            content: list = []
            for frame in frames:
                content.append(Comp.Plain(caption(frame) + "\n"))
                content.append(Comp.Image.fromFileSystem(str(frame["path"])))
            await self.send(umo, MessageChain(chain=content))
            return len(frames)
        for frame in frames:
            await self.send(umo, MessageChain(chain=[Comp.Plain(caption(frame) + "\n"), Comp.Image.fromFileSystem(str(frame["path"]))]))
            await asyncio.sleep(0.3)
        return len(frames)

    async def notify_finished(self, record: dict[str, Any], view: dict[str, Any]) -> None:
        umo = record.get("umo")
        if not umo:
            return
        key = view.get("statusKey")
        num = record["num"]
        if key != "complete":
            text = format_task(view)
            await self.send_text(umo, text)
            return
        mode = self.notify_mode()
        summary = f"✅ 任务 #{num}《{view.get('title')}》生成完成（{view.get('done', 0)}/{view.get('total', 0)} 幕）"
        if mode == "silent":
            return
        if mode == "summary":
            await self.send_text(umo, summary + f"\n下载：/mio_get {num} [html|zip|pdf|img] [模板#]")
            return
        if mode == "images":
            try:
                await self.send_images(record, umo, header=summary)
            except ServiceError as exc:
                await self.send_text(umo, summary + f"\n（发送图片失败：{exc.message}）")
            return
        if mode == "export":
            try:
                await self.send_export(record, umo)
            except ServiceError as exc:
                await self.send_text(umo, summary + f"\n（自动导出失败：{exc.message}，可用 /mio_get {num} 重试）")

    # ----------------------------------------------------------------- poller
    def ensure_poller(self) -> None:
        if self._closing:
            return
        if self._poller is None or self._poller.done():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._poller = loop.create_task(self._poll_loop())

    def _pending_records(self) -> list[dict[str, Any]]:
        return [r for r in self.store.all() if not r.get("notified") and r.get("status") != "missing"]

    async def _poll_loop(self) -> None:
        idle_rounds = 0
        while not self._closing:
            interval = _int(self.cfg("delivery", "poll_interval", 5), 5, 2, 120)
            try:
                pending = self._pending_records()
                if not pending or not self.client.configured:
                    idle_rounds += 1
                    if idle_rounds > 6:
                        return  # nothing to watch; restarted on the next task
                    await asyncio.sleep(interval)
                    continue
                idle_rounds = 0
                await self._check_once(pending)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.warning(f"[mio_studio] poller error: {exc}")
            await asyncio.sleep(interval)

    async def _check_once(self, pending: list[dict[str, Any]]) -> None:
        try:
            live = {t.get("id"): t for t in await self.mio_tasks(force=True)}
        except MioError as exc:
            self.logger.debug(f"[mio_studio] poll failed: {exc.message}")
            return
        for record in pending:
            task = live.get(record["id"])
            if task is None:
                if time.time() - float(record.get("created_at") or 0) > 30:
                    self.store.update(record["id"], status="missing", notified=True)
                continue
            status = str(task.get("status") or "")
            changes: dict[str, Any] = {}
            if status != record.get("status"):
                changes["status"] = status
            if task.get("albumId") and task.get("albumId") != record.get("albumId"):
                changes["albumId"] = task.get("albumId")
            if status in FINAL_STATES:
                changes["notified"] = True
                changes["finished_at"] = time.time()
            if changes:
                self.store.update(record["id"], **changes)
            if status in FINAL_STATES:
                view = self.merge_view({**record, **changes}, task)
                try:
                    await self.notify_finished({**record, **changes}, view)
                except Exception as exc:  # pragma: no cover - defensive
                    self.logger.warning(f"[mio_studio] notify failed for #{record.get('num')}: {exc}")

    async def close(self) -> None:
        self._closing = True
        if self._poller and not self._poller.done():
            self._poller.cancel()
            try:
                await self._poller
            except (asyncio.CancelledError, Exception):
                pass
        await self.client.close()
