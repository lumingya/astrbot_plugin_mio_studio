import hashlib
import hmac
import json
import time
from sqlite3 import connect
from unittest.mock import AsyncMock

import aiohttp
import pytest
from multidict import CIMultiDict

from core.webhook import (
    CALLBACK_PATH,
    MAX_BODY_BYTES,
    SUBSCRIBED_EVENTS,
    WebhookError,
    WebhookListener,
    callback_url,
    register_webhook,
    verify_webhook,
    webhook_deliveries,
)
from core.webhook import (
    test_webhook as probe_webhook,
)

KEY = "example-signing-key-for-tests"
STAMP = 1_790_000_000


def _headers(body, *, stamp=STAMP, event="take.created", delivery="first_delivery", key=KEY):
    digest = hmac.new(key.encode(), str(stamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Mio-Timestamp": str(stamp),
        "X-Mio-Event": event,
        "X-Mio-Delivery": delivery,
        "X-Mio-Signature": f"sha256={digest}",
    }


def _body(event="take.created", data=None):
    return json.dumps(
        {
            "id": "first_delivery",
            "event": event,
            "at": "2026-11-01T08:20:00+00:00",
            "data": {"job_id": "job_1", "episode_id": "ep_1", "prompt": "测试"}
            if data is None
            else data,
        },
        ensure_ascii=False,
    ).encode()


def test_verify_raw_signed_payload_exposes_only_reference_fields():
    body = _body()
    event = verify_webhook(body, _headers(body), KEY, now=STAMP)
    assert (event.event, event.job_id, event.episode_id) == ("take.created", "job_1", "ep_1")
    assert event.timestamp == STAMP
    assert "prompt" not in repr(event)
    with pytest.raises(WebhookError, match="signature"):
        verify_webhook(body + b"\n", _headers(body), KEY, now=STAMP)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"stamp": STAMP - 301}, "timestamp"),
        ({"stamp": STAMP + 301}, "timestamp"),
        ({"event": "job.completed"}, "envelope"),
        ({"delivery": "different_delivery"}, "envelope"),
        ({"key": "different-example-key"}, "signature"),
    ],
)
def test_signed_headers_and_timestamp(changes, reason):
    body = _body()
    with pytest.raises(WebhookError, match=reason):
        verify_webhook(body, _headers(body, **changes), KEY, now=STAMP)


def test_rejects_ambiguous_headers_and_json_even_with_valid_mac():
    body = _body()
    headers = CIMultiDict(_headers(body))
    headers.add("x-mio-timestamp", str(STAMP))
    with pytest.raises(WebhookError, match="duplicate verification header"):
        verify_webhook(body, headers, KEY, now=STAMP)
    ambiguous = body.replace(b'"job_id": "job_1"', b'"job_id":"job_2","job_id":"job_1"')
    with pytest.raises(WebhookError, match="JSON"):
        verify_webhook(ambiguous, _headers(ambiguous), KEY, now=STAMP)


async def test_listener_claims_once_per_instance_across_restart(tmp_path):
    database = tmp_path / "replay.sqlite3"
    with connect(database) as db:
        db.execute("CREATE TABLE seen (instance, id, stamp, PRIMARY KEY (instance, id))")
    received = []

    def claim(instance, delivery, stamp):
        with connect(database) as db:
            return (
                db.execute(
                    "INSERT OR IGNORE INTO seen VALUES (?, ?, ?)", (instance, delivery, stamp)
                ).rowcount
                == 1
            )

    async def send(instance, *, stamp, key, body=None, encoding=None):
        content = _body() if body is None else body
        headers = _headers(content, stamp=stamp, key=key)
        if encoding:
            headers["Content-Encoding"] = encoding
        async with client.post(
            f"http://127.0.0.1:{listener.port}" + CALLBACK_PATH.format(instance_id=instance),
            data=content,
            headers=headers,
        ) as response:
            assert not await response.read()
            return response.status

    stamp = int(time.time())
    secret_for = {"primary": KEY, "backup": "another-example-key"}.get

    def record(instance, event):
        received.append((instance, event.job_id))

    async with aiohttp.ClientSession() as client:
        listener = WebhookListener(secret_for, claim, record)
        await listener.start(port=0)
        try:
            assert await send("primary", stamp=stamp, key="wrong") == 401
            assert await send("unknown", stamp=stamp, key=KEY) == 401
            assert await send("primary", stamp=stamp, key=KEY, encoding="gzip") == 415
            assert (
                await send("primary", stamp=stamp, key=KEY, body=b"x" * (MAX_BODY_BYTES + 1)) == 413
            )
            assert await send("primary", stamp=stamp, key=KEY) == 204
        finally:
            await listener.close()
        assert listener.port is None

        listener = WebhookListener(secret_for, claim, record)
        await listener.start(port=0)
        try:
            assert await send("primary", stamp=stamp + 1, key=KEY) == 204
            assert await send("backup", stamp=stamp, key="another-example-key") == 204
        finally:
            await listener.close()
    assert received == [("primary", "job_1"), ("backup", "job_1")]


@pytest.mark.parametrize(
    "url", ["ftp://example.test", "https://user@example.test", "https://example.test/?token=1"]
)
def test_callback_url_rejects_bad_base(url):
    with pytest.raises(ValueError):
        callback_url(url, "primary")


async def test_registration_sends_exact_contract_and_sanitizes_delivery_summaries():
    client = AsyncMock()
    remote_delivery = {
        "id": "delivery_1",
        "event": "ping",
        "at": "2026-11-01T08:20:00+00:00",
        "status": 503,
        "attempts": 1,
        "error": f"Sensitive error details {KEY}",
        "secret": KEY,
    }
    client.call.side_effect = [
        {"id": "hook_1", "secret": KEY, "enabled": True},
        remote_delivery,
        [remote_delivery],
    ]
    registration = await register_webhook(client, "https://example.test/relay/", "primary")
    assert registration.hook_id == "hook_1" and registration.secret == KEY
    assert KEY not in repr(registration)
    client.call.assert_awaited_once_with(
        "create_webhook",
        body={
            "name": "AstrBot (primary)",
            "url": "https://example.test/relay/mio/webhooks/primary",
            "events": list(SUBSCRIBED_EVENTS),
            "enabled": True,
        },
    )
    summary = await probe_webhook(client, registration.hook_id)
    history = await webhook_deliveries(client, registration.hook_id)
    assert history == [summary]
    assert summary["status"] == 503 and summary["attempts"] == 1
    assert KEY not in json.dumps(summary)
