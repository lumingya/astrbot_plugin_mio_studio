"""Async client for the Mio · 绘页 (comfy-comic-studio) external API ``/api/v1``.

Authentication is a Bearer token (``MIO_API_TOKEN``, >= 32 chars). Successful
responses are ``{"data": ..., "requestId": ...}``; errors are either
``{"error": {"code", "message"}}`` (core API) or ``{"error": "message"}``
(production queue routes) — both shapes are normalised into :class:`MioError`.

The plugin relies on the routes added by the accompanying Mio patch
(``mio-patch/0001-*.patch``): ``catalog``, ``resources/*``, ``albums/<id>``,
``albums/export`` and ``production/*``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import urllib.parse
from typing import Any

import aiohttp


class MioError(Exception):
    """Transport problem or an API-level error returned by Mio."""

    def __init__(self, message: str, *, code: str = "mio_error", status: int = 0, request_id: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.request_id = request_id

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "status": self.status, "requestId": self.request_id}


def parse_data_url(data_url: str) -> tuple[bytes, str]:
    """Split ``data:<mime>;base64,<payload>`` into ``(bytes, mime)``."""
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        raise MioError("asset payload is not a data URL", code="bad_asset")
    header, _, payload = data_url.partition(",")
    mime = header[5:].split(";")[0] or "application/octet-stream"
    try:
        return base64.b64decode(payload), mime
    except Exception as exc:  # pragma: no cover - defensive
        raise MioError(f"cannot decode asset payload: {exc}", code="bad_asset") from exc


_FILENAME_STAR = re.compile(r"filename\*=UTF-8''([^;]+)", re.IGNORECASE)
_FILENAME = re.compile(r'filename="?([^";]+)"?', re.IGNORECASE)


def filename_from_disposition(value: str | None, fallback: str) -> str:
    if value:
        match = _FILENAME_STAR.search(value)
        if match:
            return urllib.parse.unquote(match.group(1))
        match = _FILENAME.search(value)
        if match:
            return match.group(1)
    return fallback


class MioClient:
    """Small aiohttp based client for the Mio external API."""

    def __init__(self, base_url: str = "http://127.0.0.1:8777", token: str = "", *, timeout: float = 60.0) -> None:
        self.base_url = (base_url or "http://127.0.0.1:8777").rstrip("/")
        self.token = (token or "").strip()
        self.timeout = float(timeout or 60.0)
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ setup
    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def configure(self, base_url: str | None = None, token: str | None = None, timeout: float | None = None) -> None:
        if base_url is not None:
            self.base_url = (base_url or "http://127.0.0.1:8777").rstrip("/")
        if token is not None:
            self.token = (token or "").strip()
        if timeout is not None:
            self.timeout = float(timeout)

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(headers={"Accept": "application/json"})
            return self._session

    async def close(self) -> None:
        async with self._lock:
            if self._session is not None and not self._session.closed:
                await self._session.close()
            self._session = None

    def _url(self, path: str) -> str:
        path = path if path.startswith("/") else f"/{path}"
        if path.startswith("/api/"):
            return f"{self.base_url}{path}"
        return f"{self.base_url}/api/v1{path}"

    # --------------------------------------------------------------- transport
    @staticmethod
    def _error_from(status: int, payload: Any, raw: bytes, reason: str | None) -> MioError:
        err = payload.get("error") if isinstance(payload, dict) else None
        request_id = payload.get("requestId") if isinstance(payload, dict) else None
        if isinstance(err, dict):
            return MioError(str(err.get("message") or f"HTTP {status}"), code=str(err.get("code") or f"http_{status}"), status=status, request_id=request_id)
        if isinstance(err, str) and err:
            code = payload.get("code") if isinstance(payload, dict) and isinstance(payload.get("code"), str) else f"http_{status}"
            return MioError(err, code=code, status=status, request_id=request_id)
        text = raw.decode("utf-8", "replace")[:300]
        return MioError(f"HTTP {status}: {text or reason or ''}".strip(), code=f"http_{status}", status=status)

    async def request_raw(self, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: Any | None = None, timeout: float | None = None) -> tuple[bytes, dict[str, str]]:
        session = await self._get_session()
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        query = {k: str(v) for k, v in (params or {}).items() if v is not None} or None
        req_timeout = aiohttp.ClientTimeout(total=timeout or self.timeout)
        try:
            async with session.request(method.upper(), self._url(path), params=query, json=json_body, headers=headers, timeout=req_timeout) as resp:
                raw = await resp.read()
                if resp.status >= 400:
                    payload: Any = None
                    try:
                        payload = json.loads(raw.decode("utf-8"))
                    except Exception:
                        payload = None
                    raise self._error_from(resp.status, payload, raw, resp.reason)
                return raw, {k: v for k, v in resp.headers.items()}
        except MioError:
            raise
        except asyncio.TimeoutError as exc:
            raise MioError(f"请求 Mio 超时（{path}）", code="timeout") from exc
        except aiohttp.ClientConnectorError as exc:
            raise MioError(f"无法连接 Mio（{self.base_url}）：{exc}", code="unreachable") from exc
        except aiohttp.ClientError as exc:
            raise MioError(f"与 Mio 通信失败：{exc}", code="transport") from exc

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: Any | None = None, timeout: float | None = None) -> Any:
        """Perform a request and return the unwrapped ``data`` payload."""
        raw, _ = await self.request_raw(method, path, params=params, json_body=json_body, timeout=timeout)
        if not raw:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise MioError("Mio 返回了无法解析的响应", code="bad_response") from exc
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params or None)

    async def post(self, path: str, body: Any = None, *, timeout: float | None = None) -> Any:
        return await self.request("POST", path, json_body=body if body is not None else {}, timeout=timeout)

    # -------------------------------------------------------------- discovery
    async def health(self) -> dict[str, Any]:
        return await self.get("/health") or {}

    async def capabilities(self) -> dict[str, Any]:
        return await self.get("/capabilities") or {}

    async def catalog(self) -> dict[str, Any]:
        """Workflows, storyboards, presets, layouts, channels, collections (needs the Mio patch)."""
        data = await self.get("/catalog")
        if not isinstance(data, dict):
            raise MioError("Mio 未返回资源目录，请确认已应用配套补丁", code="unpatched")
        return data

    async def resources(self, kind: str) -> list[dict[str, Any]]:
        data = await self.get(f"/resources/{kind}")
        return list(data.get("items", [])) if isinstance(data, dict) else []

    # ----------------------------------------------------------------- albums
    async def list_albums(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return await self.get("/albums", limit=limit, offset=offset) or {}

    async def album(self, album_id: str) -> dict[str, Any]:
        data = await self.get(f"/albums/{urllib.parse.quote(album_id, safe='')}")
        if not isinstance(data, dict):
            raise MioError("画册不存在", code="not_found", status=404)
        return data

    async def asset_bytes(self, asset_path: str) -> tuple[bytes, str]:
        data = await self.request("GET", "/assets", params={"path": asset_path})
        return parse_data_url((data or {}).get("dataUrl", ""))

    async def export_album(self, album_ids: list[str], fmt: str = "html", *, layout_id: str | None = None, image_profile: str | None = None, options: dict[str, Any] | None = None, timeout: float | None = None) -> tuple[bytes, str, str]:
        """Server-side export; returns ``(bytes, filename, content_type)``."""
        body: dict[str, Any] = {"albumIds": list(album_ids), "format": fmt}
        if layout_id:
            body["layoutId"] = layout_id
        if image_profile:
            body["imageProfile"] = image_profile
        for key, value in (options or {}).items():
            if value not in (None, ""):
                body[key] = value
        raw, headers = await self.request_raw("POST", "/albums/export", json_body=body, timeout=timeout or max(self.timeout, 300))
        ext = {"html": ".html", "zip": ".zip", "pdf": ".pdf"}.get(fmt, "")
        name = filename_from_disposition(headers.get("Content-Disposition"), f"album{ext}")
        return raw, name, headers.get("Content-Type", "application/octet-stream")

    # ------------------------------------------------------------- production
    async def production_tasks(self) -> list[dict[str, Any]]:
        data = await self.get("/production/tasks")
        return list(data.get("tasks", [])) if isinstance(data, dict) else []

    async def production_task(self, task_id: str) -> dict[str, Any]:
        data = await self.get(f"/production/tasks/{urllib.parse.quote(task_id, safe='')}")
        if not isinstance(data, dict):
            raise MioError("任务不存在", code="not_found", status=404)
        return data

    async def production(self, action: str, body: dict[str, Any] | None = None) -> Any:
        return await self.post(f"/production/{action}", body or {})

    async def assemble(self, *, story_id: str, presets: list[dict[str, str]], channel_id: str, workflow_id: str | None, title: str, request_id: str, seed: int | None = None, seed_enabled: bool = False, concurrency: int | None = None, overrides: dict[str, Any] | None = None, project_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "storyId": story_id,
            "presets": presets,
            "channelId": channel_id,
            "title": title,
            "requestId": request_id,
            "seed": int(seed) if isinstance(seed, int) and 0 <= seed < 2**32 else 1,
            "seedEnabled": bool(seed_enabled),
        }
        if workflow_id:
            body["workflowId"] = workflow_id
        if concurrency:
            body["concurrency"] = int(concurrency)
        if overrides:
            body["overrides"] = overrides
        if project_id:
            body["projectId"] = project_id
        return await self.production("assemble", body)

    async def start_task(self, task_id: str, *, concurrency: int | None = None, indices: list[int] | None = None, force_prepare: bool = False) -> Any:
        body: dict[str, Any] = {"id": task_id, "trusted": True, "confirmUncertain": True}
        if concurrency:
            body["concurrency"] = int(concurrency)
        if indices:
            body["indices"] = indices
        if force_prepare:
            body["forcePrepare"] = True
        return await self.production("start", body)
