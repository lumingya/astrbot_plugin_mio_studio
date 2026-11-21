"""Read-side, public views of Mio workflows, workshop resources and the bookshelf."""

from __future__ import annotations

import asyncio
import math
import re
import time
from copy import deepcopy
from typing import Any
from weakref import WeakKeyDictionary

from .v2_client import MioApiError

_PAGE_SIZE = 100
_MAX_BOARDS = 1000
_CATALOG = {
    "workflows": "list_workflows",
    "storyboards": "list_storyboards",
    "presets": "list_presets",
    "channels": "list_image_channels",
    "layouts": "list_album_templates",
    "kits": "list_creation_kits",
}
_DETAIL = {
    "workflows": ("get_workflow", "workflow_id"),
    "storyboards": ("get_storyboard", "storyboard_id"),
    "presets": ("get_preset", "preset_id"),
    "layouts": ("get_album_template", "template_id"),
    "kits": ("get_series", "series_id"),
}
_MIO_SECRET = re.compile(r"\bmio_[A-Za-z0-9_-]{12,}\b")
_VAR = re.compile(r"(?<!\{)\{([^{}\s]{1,64})\}(?!\})")
_SENSITIVE_VAR = re.compile(r"secret|password|token|api.?key|authorization|凭据|令牌|密钥", re.I)
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def _string(value: Any, limit=160) -> str:
    return _MIO_SECRET.sub("[已隐藏]", value)[:limit] if isinstance(value, str) else ""


def _count(value: Any) -> int:
    return max(0, value) if type(value) is int else 0


def _weight(value: Any) -> int | float | None:
    if type(value) is int or (type(value) is float and math.isfinite(value)):
        return value
    return None


def _rows(value: Any) -> list[dict]:
    if isinstance(value, dict):
        value = value.get("items")
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _project(row: dict, kind: str) -> dict:
    identifier = _string(row.get("id"), 256)
    title = _string(row.get("title") or row.get("name") or row.get("label") or identifier)
    item = {"id": identifier, "title": title}
    if kind == "workflows":
        describe = row.get("describe") if isinstance(row.get("describe"), dict) else row
        loras = row.get("loras") if isinstance(row.get("loras"), dict) else {}
        item.update(
            name=title,
            nodeCount=_count(describe.get("nodes")),
            checkpoint=_string(describe.get("checkpoint")),
            loras={
                "enabled": loras.get("enabled") is True,
                "groups": _count(loras.get("groups")),
                "supports_independent_toggle": loras.get("supports_independent_toggle") is True,
            },
        )
    elif kind == "presets":
        entries = [
            {
                "key": _string(entry.get("key"), 64),
                "value": _string(entry.get("value"), 800),
                "label": _string(entry.get("label")),
                "group_id": _string(entry.get("group_id")),
            }
            for entry in _rows(row.get("entries"))[:128]
            if entry.get("key") and not _SENSITIVE_VAR.search(str(entry["key"]))
        ]
        item.update(
            entries=entries,
            groups=[
                {"id": _string(group.get("id")), "title": _string(group.get("title"))}
                for group in _rows(row.get("groups"))[:128]
            ],
            category="characters"
            if any(
                e["key"].casefold().startswith(("character", "outfit", "角色", "服装"))
                for e in entries
            )
            else "scenes",
        )
    elif kind == "channels":
        provider = _string(row.get("kind"))
        item.update(
            provider=provider,
            cloud=provider != "comfyui",
            model=_string(row.get("model")),
            default=row.get("default") is True,
        )
    elif kind == "layouts":
        options = row.get("options")
        options = options if isinstance(options, dict) else {}
        source = _string(row.get("source"))
        item.update(
            layout=_string(row.get("layout")) or identifier,
            layout_name=_string(row.get("layout_name")),
            description=_string(row.get("description"), 500),
            source=source,
            builtin=source == "builtin",
            options={
                key: value
                for key in ("accent", "background", "paper", "text")
                if isinstance(value := options.get(key), str) and _COLOR.fullmatch(value)
            },
        )
    elif kind in {"storyboards", "series", "works", "kits"}:
        panels = _rows(row.get("panels"))
        item.update(
            series_id=_string(row.get("series_id"), 256),
            frameCount=_count(row.get("panel_count", len(panels))),
            adopted_count=_count(row.get("adopted_count")),
            episode_count=_count(row.get("episode_count")),
            cover_asset_id=_string(row.get("cover_asset_id"), 256),
            description=_string(row.get("synopsis") or row.get("subtitle"), 600),
            updated_at=_string(row.get("updated_at")),
        )
    return item


def _board_detail(row: dict) -> dict:
    panels = []
    base_prompt = _string(row.get("base_prompt"), 4000)
    for panel in sorted(_rows(row.get("panels")), key=lambda p: _count(p.get("order")))[:200]:
        overrides = panel.get("overrides")
        overrides = overrides if isinstance(overrides, dict) else {}
        prompt = _string(overrides.get("raw_prompt") or overrides.get("append_prompt"), 4000)
        negative = _string(overrides.get("raw_negative") or overrides.get("negative_prompt"), 2000)
        panels.append(
            {
                "id": _string(panel.get("id"), 256),
                "order": _count(panel.get("order")),
                "description": _string(panel.get("description"), 2000),
                "prompt": prompt,
                "negative": negative,
                "variables": sorted(set(_VAR.findall("\n".join((base_prompt, prompt, negative))))),
            }
        )
    return {**_project(row, "storyboards"), "base_prompt": base_prompt, "panels": panels}


class Resources:
    """Cache each client independently; credentials and full object payloads stay upstream."""

    def __init__(self, ttl: float = 20) -> None:
        self.ttl = ttl
        self._cache: WeakKeyDictionary = WeakKeyDictionary()
        self._locks: WeakKeyDictionary = WeakKeyDictionary()

    async def _paged(self, client, operation, *, path_params=None, limit=_MAX_BOARDS, offset=0):
        items = []
        while len(items) < limit:
            size = min(_PAGE_SIZE, limit - len(items))
            payload = await client.call(
                operation, path_params=path_params, params={"offset": offset, "limit": size}
            )
            page = _rows(payload)
            items.extend(page[:size])
            offset += size
            total = payload.get("total") if isinstance(payload, dict) else None
            if len(page) < size or (type(total) is int and offset >= total):
                break
        return items

    async def catalog(self, client, *, force=False) -> dict[str, list[dict]]:
        lock = self._locks.setdefault(client, asyncio.Lock())
        async with lock:
            cached = self._cache.get(client)
            if not force and cached is not None and cached[0] > time.monotonic():
                return deepcopy(cached[1])

            async def load(kind, operation):
                if kind == "storyboards":
                    rows = await self._paged(client, operation)
                else:
                    rows = _rows(await client.call(operation))
                return [
                    _project(row, kind)
                    for row in rows[:_MAX_BOARDS]
                    if row.get("id") or (kind == "channels" and row.get("id") == "")
                ]

            result = dict(
                zip(
                    _CATALOG,
                    await asyncio.gather(*(load(kind, op) for kind, op in _CATALOG.items())),
                    strict=True,
                )
            )
            self._cache[client] = (time.monotonic() + self.ttl, result)
            return deepcopy(result)

    async def detail(self, client, kind, resource_id) -> dict:
        if kind == "channels":
            catalog = await self.catalog(client)
            row = next((row for row in catalog[kind] if row["id"] == resource_id), None)
            if row is not None:
                return row
            raise MioApiError("没有找到这个图像通道。", code="resource_not_found", status=404)
        if kind not in _DETAIL:
            raise MioApiError("不支持该资源类型。", code="resource_kind", status=400)
        operation, parameter = _DETAIL[kind]
        row = await client.call(operation, path_params={parameter: resource_id})
        if not isinstance(row, dict) or row.get("id") != resource_id:
            raise MioApiError("Mio 未返回所请求的资源。", code="bad_response")
        return _board_detail(row) if kind == "storyboards" else _project(row, kind)

    async def loras(self, client, workflow_id) -> dict:
        data = await client.call("workflow_loras", path_params={"workflow_id": workflow_id})
        if not isinstance(data, dict):
            raise MioApiError("Mio 未返回 LoRA 槽位。", code="bad_response")
        groups = []
        for group in _rows(data.get("groups"))[:100]:
            loras = []
            for lora in _rows(group.get("loras"))[:100]:
                loras.append(
                    {
                        "key": _string(lora.get("key"), 512),
                        "name": _string(lora.get("name"), 512),
                        "strength": _weight(lora.get("strength")),
                        "clip": _weight(lora.get("clip")),
                        "enabled": lora.get("enabled") is True,
                    }
                )
            groups.append(
                {
                    "key": _string(group.get("key"), 512),
                    "kind": _string(group.get("kind")),
                    "active": group.get("active") is True,
                    "supports_independent_toggle": group.get("supports_independent_toggle") is True,
                    "reason": _string(group.get("reason")),
                    "loras": loras,
                }
            )
        return {
            "workflow_id": workflow_id,
            "enabled": data.get("enabled") is True,
            "groups": groups,
        }

    async def series(self, client, *, query="", limit=100, offset=0) -> list[dict]:
        match = query.strip().casefold()
        rows = [
            _project(row, "series")
            for row in _rows(await client.call("list_series"))
            if row.get("id")
        ]
        return [
            row
            for row in rows
            if not match or match in f"{row['title']} {row['description']}".casefold()
        ][offset : offset + limit]

    async def episodes(self, client, series_id, *, limit=50, offset=0) -> list[dict]:
        rows = await self._paged(
            client,
            "list_episodes",
            path_params={"series_id": series_id},
            limit=min(200, max(1, limit)),
            offset=max(0, offset),
        )
        return [
            {
                **_project(row, "works"),
                "series_id": series_id,
                "episode_id": _string(row["id"], 256),
            }
            for row in rows
            if row.get("id")
        ]

    async def works(self, client, *, query="", limit=30) -> list[dict]:
        cap = min(100, max(1, limit))
        match = query.strip().casefold()
        shelves = await self.series(client, limit=100)
        result = []
        for shelf in shelves:
            shelf_match = match in f"{shelf['title']} {shelf['description']}".casefold()
            episodes = await self.episodes(
                client, shelf["id"], limit=100 if match and not shelf_match else cap
            )
            result.extend(
                {**episode, "series_title": shelf["title"]}
                for episode in episodes
                if shelf_match or match in episode["title"].casefold()
            )
            if len(result) >= cap:
                break
        return result[:cap]
