"""Async transport for Mio Studio's OpenAPI v2."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import quote, unquote, urlsplit

import aiohttp
from yarl import URL

DEFAULT_BASE_URL = "http://127.0.0.1:8788"
_API_PREFIX = "/api/v2"
_OLD_HEALTH = "/api/v1/health"
_JSON_BYTES = 16 * 1024 * 1024
_ERROR_BYTES = 16 * 1024
_CHUNK_BYTES = 256 * 1024
_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
_CODE = re.compile(r"[a-z][a-z0-9_-]{0,63}", re.ASCII)
_BAD_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED_FILENAME = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])$", re.I)
_T = TypeVar("_T")


class MioApiError(Exception):
    """A public-safe service or transport error."""

    def __init__(
        self, message: str, *, code: str = "mio_error", status: int = 0, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            "retryable": self.retryable,
        }


def _validate_path(path: str) -> None:
    if (
        not path.startswith("/")
        or "//" in path
        or not path.isascii()
        or re.search(r"[?#\\\x00-\x20\x7f]", path)
    ):
        raise ValueError("Mio API 路径无效")
    for segment in path.split("/"):
        # Validate decoded segments as well: reverse proxies may normalize the path.
        if re.search(r"%(?![a-fA-F\d]{2})", segment):
            raise ValueError("Mio API 路径无效")
        decoded = unquote(segment, errors="strict")
        if decoded in (".", "..") or re.search(r"[/\\\x00-\x1f\x7f]", decoded):
            raise ValueError("Mio API 路径无效")
        if re.search(r"%(?:25|2e|2f|5c)", decoded, re.I):
            raise ValueError("Mio API 路径无效")


def normalize_base_url(value: str) -> str:
    value = value.strip()
    try:
        if re.search(r"[\\?#\x00-\x20\x7f]", value):
            raise ValueError
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or "@" in parts.netloc
            or "%" in parts.netloc
            or parts.port == 0
        ):
            raise ValueError
        _validate_path(quote(parts.path or "/", safe="%/!$&'()*+,;=:@"))
        url = URL(value)
    except (ValueError, UnicodeError):
        raise ValueError("Mio 地址需为有效的 HTTP 或 HTTPS 地址，且不含账号或查询参数") from None
    path = url.raw_path.rstrip("/")
    for suffix in (_API_PREFIX, "/api/v1"):
        if path.endswith(suffix):
            path = path.removesuffix(suffix)
            break
    return f"{url.origin()}{path}"


@dataclass(frozen=True, slots=True)
class DownloadedFile:
    path: Path
    filename: str
    content_type: str
    size: int


def _safe_filename(value: str | None) -> str:
    cleaned = _BAD_FILENAME.sub("_", (value or "").strip()).strip(" .")
    if len(cleaned.encode("utf-8", "replace")) > 200:
        stem, suffix = os.path.splitext(cleaned)
        cleaned = stem.encode("utf-8", "replace")[:170].decode("utf-8", "ignore") + suffix.encode(
            "utf-8", "replace"
        )[:30].decode("utf-8", "ignore")
    if _RESERVED_FILENAME.fullmatch(cleaned.split(".")[0]):
        cleaned = f"_{cleaned}"
    return cleaned or "download.bin"


class DownloadCache:
    """Commit complete downloads under unique folders and evict oldest entries."""

    def __init__(self, root: Path, *, max_bytes: int = 512 * 1024**2, max_files: int = 160) -> None:
        if max_bytes < 1 or max_files < 1:
            raise ValueError("缓存容量与文件数必须大于零")
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.max_files = max_files

    def prune(self, *, keep: Path | None = None) -> bool:
        """Return whether the requested cache limits could be met."""
        if not self.root.is_dir():
            return True
        entries: list[tuple[int, Path, int]] = []
        for directory in self.root.glob("mio-*"):
            if directory.is_symlink() or not directory.is_dir():
                continue
            for path in directory.iterdir():
                if path.name == ".part" or path.is_symlink() or not path.is_file():
                    continue
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                entries.append((stat.st_mtime_ns, path, stat.st_size))
        count = len(entries)
        size = sum(entry[2] for entry in entries)
        for _, path, bytes_used in sorted(entries):
            if count <= self.max_files and size <= self.max_bytes:
                break
            if path == keep:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Windows can deny unlink while the chat adapter still reads a file.
                continue
            count -= 1
            size -= bytes_used
            try:
                path.parent.rmdir()
            except OSError:
                pass
        return count <= self.max_files and size <= self.max_bytes

    async def write(self, response: aiohttp.ClientResponse, filename: str | None) -> DownloadedFile:
        if response.content_length is not None and response.content_length > self.max_bytes:
            raise self._too_large()
        disposition = response.content_disposition
        name = _safe_filename((disposition.filename if disposition else None) or filename)
        folder: Path | None = None
        partial: Path | None = None
        target: Path | None = None
        complete = False
        size = 0
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix="mio-", dir=self.root))
            partial = folder / ".part"
            target = folder / name
            with partial.open("xb") as output:
                async for chunk in response.content.iter_chunked(_CHUNK_BYTES):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise self._too_large()
                    output.write(chunk)
            if not size:
                raise MioApiError("Mio 没有返回成品文件，请确认导出已完成", code="empty_file")
            os.replace(partial, target)
            if not self.prune(keep=target):
                raise MioApiError("缓存空间不足，请稍后重试", code="cache_full", retryable=True)
            complete = True
            return DownloadedFile(target, name, response.content_type, size)
        except (TimeoutError, aiohttp.ClientError):
            raise
        except OSError:
            raise MioApiError(
                "保存 Mio 文件失败，请检查磁盘空间与目录权限", code="cache_write_failed"
            ) from None
        finally:
            if folder is not None and not complete:
                try:
                    if partial is not None:
                        partial.unlink(missing_ok=True)
                    if target is not None:
                        target.unlink(missing_ok=True)
                    folder.rmdir()
                except OSError:
                    pass

    @staticmethod
    def _too_large() -> MioApiError:
        return MioApiError(
            "成品文件超过插件缓存容量，请减小图片档位或缩小导出范围",
            code="file_too_large",
            status=413,
        )


async def _bounded_body(response: aiohttp.ClientResponse, limit: int) -> bytearray:
    payload = bytearray()
    if response.content_length is not None and response.content_length > limit:
        raise MioApiError("Mio 响应体积过大", code="response_too_large")
    async for chunk in response.content.iter_chunked(min(_CHUNK_BYTES, limit)):
        payload.extend(chunk)
        if len(payload) > limit:
            raise MioApiError("Mio 响应体积过大", code="response_too_large")
    return payload


async def _read_json(response: aiohttp.ClientResponse) -> Any:
    payload = await _bounded_body(response, _JSON_BYTES)
    if not payload:
        return None
    try:
        return json.loads(payload)
    except (ValueError, RecursionError):
        raise MioApiError(
            "Mio 返回的数据无法读取，请检查服务地址和版本", code="bad_response"
        ) from None


_STATUS_MESSAGES = {
    400: "Mio 无法处理这项操作，请检查所选资源和参数",
    401: "Mio 令牌缺失或已失效，请在连接设置中填写有效的 mio_ 令牌",
    403: "当前 Mio 令牌没有这项操作的权限，请在 Mio 检查令牌的授权范围",
    404: "Mio 中找不到此资源或接口，请刷新目录并检查版本",
    409: "Mio 资源状态已经变化，请刷新后再操作",
    413: "请求内容超过 Mio 的容量限制，请缩小文件或导出范围",
    422: "Mio 拒绝了请求参数，请检查所选内容和参数范围",
    429: "Mio 请求过于频繁，请稍等片刻再试",
}


async def _http_error(response: aiohttp.ClientResponse) -> MioApiError:
    status = response.status
    if 300 <= status < 400:
        return MioApiError(
            "Mio 地址返回了重定向，请填写最终的服务地址", code="redirect", status=status
        )
    kind = f"http_{status}"
    try:
        data = json.loads(await _bounded_body(response, _ERROR_BYTES))
    except (MioApiError, ValueError, RecursionError):
        data = None
    if isinstance(data, dict):
        for key in ("code", "kind"):
            candidate = data.get(key)
            if (
                isinstance(candidate, str)
                and _CODE.fullmatch(candidate)
                and not candidate.startswith("mio_")
            ):
                kind = candidate
                break
    # Mio's detail can contain credentials, prompts or private file paths.
    # Keep the machine-readable error kind and render public advice locally.
    message = _STATUS_MESSAGES.get(status)
    if message is None:
        message = (
            "Mio 服务暂时无法完成操作，请检查 Mio 和出图通道的状态"
            if status >= 500
            else "Mio 未能完成操作，请检查服务和当前资源的状态"
        )
    return MioApiError(message, code=kind, status=status, retryable=status in _RETRY_STATUSES)


class MioV2Client:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        token: str = "",
        *,
        timeout: float = 60,
        retries: int = 2,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0 or not 0 <= retries <= 5:
            raise ValueError("Mio 请求超时或重试次数无效")
        self.base_url = normalize_base_url(base_url)
        self._token = token.strip()
        if self._token and re.search(r"[^!-~]", self._token):
            raise ValueError("Mio 令牌格式无效")
        self.timeout = timeout
        self.retries = retries
        self._session: aiohttp.ClientSession | None = None
        self._closed = False

    async def close(self) -> None:
        self._closed = True
        session, self._session = self._session, None
        if session is not None:
            await session.close()

    def _get_session(self) -> aiohttp.ClientSession:
        if self._closed:
            raise MioApiError("Mio 客户端已停止", code="closed")
        if self._session is None:
            self._session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
            # aiohttp >=3.11 retries dropped connections for PUT and DELETE by default.
            # All retries here are controlled by _perform so only GET can be replayed.
            if hasattr(self._session, "_retry_connection"):
                self._session._retry_connection = False
        return self._session

    def _url(self, path: str, *, legacy_probe: bool = False) -> URL:
        _validate_path(path)
        return URL(f"{self.base_url}{'' if legacy_probe else _API_PREFIX}{path}", encoded=True)

    async def _perform(
        self,
        method: str,
        path: str,
        consume: Callable[[aiohttp.ClientResponse], Awaitable[_T]],
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
        public: bool = False,
        legacy_probe: bool = False,
        accept: str = "application/json",
    ) -> _T:
        method = method.upper()
        url = self._url(path, legacy_probe=legacy_probe)
        headers = {"Accept": accept}
        if self._token and not public:
            headers["Authorization"] = f"Bearer {self._token}"
        query = {
            key: str(value).lower() if isinstance(value, bool) else value
            for key, value in (params or {}).items()
            if value is not None
        }
        deadline = self.timeout if timeout is None else timeout
        if not math.isfinite(deadline) or deadline <= 0:
            raise ValueError("Mio 请求超时无效")
        attempts = 1 + (self.retries if method == "GET" and not legacy_probe else 0)
        for attempt in range(attempts):
            try:
                async with self._get_session().request(
                    method,
                    url,
                    params=query or None,
                    json=json_body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=deadline),
                    allow_redirects=False,
                ) as response:
                    if not 200 <= response.status < 300:
                        raise await _http_error(response)
                    return await consume(response)
            except MioApiError as exc:
                failure = exc
            except TimeoutError:
                failure = MioApiError(
                    "等待 Mio 响应超时，请检查服务负载和连接设置",
                    code="timeout",
                    retryable=True,
                )
            except aiohttp.ClientConnectorError:
                failure = MioApiError(
                    "无法连接 Mio，请确认 Mio 已启动、地址和端口正确",
                    code="unreachable",
                    retryable=True,
                )
            except (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError):
                failure = MioApiError(
                    "与 Mio 的连接中断，请稍后重试", code="transport", retryable=True
                )
            except aiohttp.ClientError:
                failure = MioApiError("与 Mio 通信失败，请检查连接设置", code="transport")
            if attempt + 1 == attempts or not failure.retryable:
                raise failure from None
            await asyncio.sleep(min(0.15 * 2**attempt, 1.5))
        raise AssertionError("unreachable")

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
    ) -> Any:
        return await self._perform(
            method,
            path,
            _read_json,
            params=params,
            json_body=json_body,
            timeout=timeout,
            public=path in ("/meta", "/openapi.json"),
        )

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, body: Any = None, *, timeout: float | None = None) -> Any:
        return await self.request("POST", path, json_body=body, timeout=timeout)

    async def probe(self) -> dict[str, Any]:
        """Discover v2 publicly, checking for the old Mio health route on absence."""
        try:
            meta = await self.get("/meta")
            if (
                isinstance(meta, dict)
                and isinstance(meta.get("api"), str)
                and meta["api"].split(".", 1)[0] == "2"
                and isinstance(meta.get("server"), str)
            ):
                return meta
            raise MioApiError("地址未提供 Mio API v2 服务信息", code="bad_response")
        except MioApiError as primary:
            if primary.code != "bad_response" and primary.status != 404:
                raise
            try:
                old = await self._perform(
                    "GET", _OLD_HEALTH, _read_json, public=True, legacy_probe=True
                )
            except MioApiError:
                old = None
            if isinstance(old, dict):
                result = old.get("data", old)
                if isinstance(result, dict) and (
                    isinstance(result.get("version"), str)
                    or (result.get("ok") is True and "requestId" in old)
                ):
                    raise MioApiError(
                        "此地址运行旧版 Mio API v1，请升级 Mio，改用 v2 地址与 mio_ 令牌",
                        code="legacy_v1",
                        status=426,
                    ) from None
            raise MioApiError(
                "此地址未提供 Mio API v2，请核对 Mio 版本和服务地址（默认端口 8788）",
                code="v2_unavailable",
                status=primary.status,
            ) from None

    async def download(
        self,
        method: str,
        path: str,
        *,
        cache: DownloadCache,
        filename: str | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
    ) -> DownloadedFile:
        async def save(response: aiohttp.ClientResponse) -> DownloadedFile:
            return await cache.write(response, filename)

        return await self._perform(
            method,
            path,
            save,
            params=params,
            json_body=json_body,
            timeout=timeout,
            accept="*/*",
        )

    async def call(
        self,
        operation_id: str,
        *,
        path_params: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        timeout: float | None = None,
    ) -> Any:
        from .v2_operations import OPS, operation_path

        method, _ = OPS[operation_id]
        return await self.request(
            method,
            operation_path(operation_id, path_params or {}),
            params=params,
            json_body=body,
            timeout=timeout,
        )

    async def download_operation(
        self,
        operation_id: str,
        *,
        cache: DownloadCache,
        filename: str | None = None,
        path_params: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        timeout: float | None = None,
    ) -> DownloadedFile:
        from .v2_operations import OPS, operation_path

        method, _ = OPS[operation_id]
        return await self.download(
            method,
            operation_path(operation_id, path_params or {}),
            cache=cache,
            filename=filename,
            params=params,
            json_body=body,
            timeout=timeout,
        )
