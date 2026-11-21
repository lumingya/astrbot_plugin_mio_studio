"""Mio v2 signed callbacks. Events are hints; the poller confirms remote state."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from aiohttp import web

from .v2_client import MioApiError, MioV2Client

CALLBACK_PATH = "/mio/webhooks/{instance_id}"
SUBSCRIBED_EVENTS = ("job.completed", "job.failed", "job.canceled", "take.created")
KNOWN_EVENTS = frozenset((*SUBSCRIBED_EVENTS, "take.status", "episode.exported", "ping"))
MAX_BODY_BYTES = 512 * 1024
TIMESTAMP_TOLERANCE = 300
_INSTANCE = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")
_IDENTIFIER = re.compile(r"[a-zA-Z0-9_-]{1,128}")
_STAMP = re.compile(r"[0-9]{1,18}")
_SIGNATURE = re.compile(r"sha256=[a-f0-9]{64}")
_REQUIRED_HEADERS = {
    "x-mio-timestamp",
    "x-mio-signature",
    "x-mio-delivery",
    "x-mio-event",
}


class WebhookError(ValueError):
    """The request could not be authenticated or did not match the protocol."""


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    delivery_id: str
    event: str
    timestamp: int
    job_id: str | None
    episode_id: str | None


def _unique_json(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WebhookError("duplicate JSON key")
        result[key] = value
    return result


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _valid_datetime(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def verify_webhook(
    body: bytes,
    headers: Mapping[str, str],
    secret: str,
    *,
    now: float | None = None,
) -> WebhookEvent:
    """Validate raw bytes and return only non-sensitive references to the affected work."""
    if (
        not isinstance(body, bytes)
        or not body
        or len(body) > MAX_BODY_BYTES
        or not isinstance(secret, str)
        or not secret
    ):
        raise WebhookError("missing signing key or invalid body")
    normalized: dict[str, str] = {}
    for name, value in headers.items():
        key = name.lower()
        if key in _REQUIRED_HEADERS:
            if key in normalized or not isinstance(value, str):
                raise WebhookError("duplicate verification header")
            normalized[key] = value
    if normalized.keys() != _REQUIRED_HEADERS:
        raise WebhookError("missing verification header")

    stamp = normalized["x-mio-timestamp"]
    signature = normalized["x-mio-signature"]
    if not _STAMP.fullmatch(stamp) or not _SIGNATURE.fullmatch(signature):
        raise WebhookError("invalid verification header")
    timestamp = int(stamp)
    now = time.time() if now is None else now
    if not math.isfinite(now) or abs(now - timestamp) > TIMESTAMP_TOLERANCE:
        raise WebhookError("stale timestamp")
    expected = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"), stamp.encode("ascii") + b"." + body, hashlib.sha256
        ).hexdigest()
    )
    if not hmac.compare_digest(expected, signature):
        raise WebhookError("invalid signature")

    try:
        envelope = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_json)
    except (ValueError, RecursionError):
        raise WebhookError("invalid JSON envelope") from None
    if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), dict):
        raise WebhookError("invalid event data")
    delivery_id = normalized["x-mio-delivery"]
    event = normalized["x-mio-event"]
    if (
        not _valid_id(delivery_id)
        or envelope.get("id") != delivery_id
        or event not in KNOWN_EVENTS
        or envelope.get("event") != event
        or not _valid_datetime(envelope.get("at"))
    ):
        raise WebhookError("invalid event envelope")
    data = envelope["data"]
    job_id = data.get("job_id")
    episode_id = data.get("episode_id")
    if (job_id is not None and not _valid_id(job_id)) or (
        episode_id is not None and not _valid_id(episode_id)
    ):
        raise WebhookError("invalid event target")
    if event in SUBSCRIBED_EVENTS and not job_id and not episode_id:
        raise WebhookError("missing event target")
    return WebhookEvent(delivery_id, event, timestamp, job_id, episode_id)


class WebhookListener:
    """Optional listener independent of AstrBot dashboard authentication.

    The injected claim function must durably reserve IDs per instance. A restart,
    or a retry with a fresh signature timestamp, does not make an ID new again.
    """

    def __init__(
        self,
        secret_for: Callable[[str], str | None],
        claim: Callable[[str, str, int], bool],
        on_event: Callable[[str, WebhookEvent], None],
    ) -> None:
        self._secret_for = secret_for
        self._claim = claim
        self._on_event = on_event
        self._runner: web.AppRunner | None = None
        self._lifecycle = asyncio.Lock()

    @property
    def port(self) -> int | None:
        if self._runner is not None and self._runner.addresses:
            return self._runner.addresses[0][1]
        return None

    async def start(self, host: str = "127.0.0.1", port: int = 8790) -> None:
        async with self._lifecycle:
            if self._runner is not None:
                return
            app = web.Application(client_max_size=MAX_BODY_BYTES + 1)
            app.router.add_post(CALLBACK_PATH, self._handle)
            runner = web.AppRunner(app, access_log=None, auto_decompress=False, shutdown_timeout=5)
            await runner.setup()
            try:
                await web.TCPSite(runner, host, port).start()
            except BaseException:
                await runner.cleanup()
                raise
            self._runner = runner

    async def close(self) -> None:
        async with self._lifecycle:
            runner, self._runner = self._runner, None
            if runner is not None:
                await runner.cleanup()

    async def _handle(self, request: web.Request) -> web.Response:
        instance_id = request.match_info["instance_id"]
        if _INSTANCE.fullmatch(instance_id) is None:
            return web.Response(status=401)
        if request.content_type != "application/json" or request.headers.get("Content-Encoding"):
            return web.Response(status=415)
        if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
            return web.Response(status=413)
        try:
            secret = self._secret_for(instance_id)
        except Exception:
            return web.Response(status=503)
        if not secret:
            return web.Response(status=401)
        try:
            body = await asyncio.wait_for(request.read(), timeout=5)
        except TimeoutError:
            return web.Response(status=408)
        except web.HTTPRequestEntityTooLarge:
            return web.Response(status=413)
        try:
            event = verify_webhook(body, request.headers, secret)
        except WebhookError:
            return web.Response(status=401)
        try:
            first = await asyncio.to_thread(
                self._claim, instance_id, event.delivery_id, event.timestamp
            )
            if first and event.event in SUBSCRIBED_EVENTS:
                self._on_event(instance_id, event)
        except Exception:
            return web.Response(status=503)
        # Duplicate retries acknowledge receipt without executing the refresh twice.
        return web.Response(status=204)


@dataclass(frozen=True, slots=True)
class WebhookRegistration:
    hook_id: str
    secret: str = field(repr=False)


def callback_url(public_base_url: str, instance_id: str) -> str:
    """Join an administrator's public receiver base URL to this instance's route."""
    if not isinstance(instance_id, str) or not _INSTANCE.fullmatch(instance_id):
        raise ValueError("Webhook 实例 ID 无效。")
    try:
        if not isinstance(public_base_url, str):
            raise ValueError
        parts = urlsplit(public_base_url)
        if (
            not public_base_url.startswith(("http://", "https://"))
            or not parts.hostname
            or parts.username is not None
            or any(char.isspace() or ord(char) < 32 or char in "\\?#" for char in public_base_url)
            or parts.port == 0
            or len(public_base_url) > 1800
        ):
            raise ValueError
    except ValueError:
        raise ValueError("请输入不带凭据、查询串或片段的 HTTP(S) 回调基址。") from None
    route = CALLBACK_PATH.format(instance_id=instance_id)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + route, "", ""))


async def register_webhook(
    client: MioV2Client, public_base_url: str, instance_id: str
) -> WebhookRegistration:
    response = await client.call(
        "create_webhook",
        body={
            "name": f"AstrBot ({instance_id})",
            "url": callback_url(public_base_url, instance_id),
            "events": list(SUBSCRIBED_EVENTS),
            "enabled": True,
        },
    )
    if isinstance(response, dict) and _valid_id(response.get("id")):
        secret = response.get("secret")
        if isinstance(secret, str) and 16 <= len(secret) <= 256 and _IDENTIFIER.fullmatch(secret):
            return WebhookRegistration(response["id"], secret)
    raise MioApiError("Mio 未返回可保存的 Webhook 签名配置。", code="invalid_webhook_config")


def _delivery_view(data: object) -> dict[str, object]:
    if not isinstance(data, dict):
        return {}
    event = data.get("event")
    status = data.get("status")
    attempts = data.get("attempts")
    return {
        "id": data.get("id") if _valid_id(data.get("id")) else "",
        "event": event if isinstance(event, str) and event in KNOWN_EVENTS else "",
        "at": data.get("at") if _valid_datetime(data.get("at")) else "",
        "status": status if type(status) is int and 100 <= status <= 599 else None,
        "attempts": attempts if type(attempts) is int and 0 <= attempts <= 3 else 0,
        "error": "Mio 未能投递回调。" if data.get("error") else "",
    }


async def test_webhook(client: MioV2Client, hook_id: str) -> dict[str, object]:
    response = await client.call("test_webhook", path_params={"hook_id": hook_id})
    return _delivery_view(response)


async def webhook_deliveries(client: MioV2Client, hook_id: str) -> list[dict[str, object]]:
    response = await client.call("webhook_deliveries", path_params={"hook_id": hook_id})
    return [_delivery_view(item) for item in response[:30]] if isinstance(response, list) else []
