import asyncio
import hashlib
from collections import Counter
from urllib.parse import quote

import pytest
import pytest_asyncio
from aiohttp import web

from core.v2_client import DownloadCache, MioApiError, MioV2Client, normalize_base_url

TEST_TOKEN = "mio_fixture_only"
META = {"api": "2.0", "server": "3.4.5", "scopes": ["read", "render"]}


@pytest_asyncio.fixture
async def make_server():
    runners = []

    async def start(routes):
        app = web.Application()
        app.add_routes(routes)
        runner = web.AppRunner(app)
        runners.append(runner)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        return f"http://127.0.0.1:{port}"

    yield start
    for runner in reversed(runners):
        await runner.cleanup()


@pytest_asyncio.fixture
async def make_client():
    clients = []

    def add(base, **options):
        c = MioV2Client(base, TEST_TOKEN, **options)
        clients.append(c)
        return c

    yield add
    await asyncio.gather(*(client.close() for client in clients))


async def test_named_operations_use_contract_and_bearer(make_server, make_client):
    async def meta_handler(request):
        assert "Authorization" not in request.headers
        return web.json_response(META)

    async def handler(request):
        assert request.headers["Authorization"] == f"Bearer {TEST_TOKEN}"
        assert request.match_info["series_id"] == "测试 #1"
        assert await request.json() == {"title": "新名字"}
        assert request.query["flag"] == "false"
        assert "absent" not in request.query
        return web.json_response({"data": {"revision": 1}, "id": "series-1"})

    base = await make_server(
        [
            web.get("/mio/api/v2/meta", meta_handler),
            web.patch("/mio/api/v2/series/{series_id}", handler),
        ]
    )
    client = make_client(f"{base}/mio/api/v2/")
    assert await client.probe() == META
    result = await client.call(
        "patch_series",
        path_params={"series_id": "测试 #1"},
        params={"flag": False, "absent": None},
        body={"title": "新名字"},
    )
    assert result == {"data": {"revision": 1}, "id": "series-1"}


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (401, "unauthorized", False),
        (403, "forbidden", False),
        (404, "not_found", False),
        (422, "http_422", False),
        (500, "http_500", True),
    ],
)
async def test_http_errors_keep_status_and_safe_kind(
    make_server, make_client, status, kind, retryable
):
    detail = f"Sensitive upstream diagnostic: token={TEST_TOKEN}, prompt=private"

    async def handler(_request):
        body = {"detail": detail} if kind.startswith("http_") else {"detail": detail, "kind": kind}
        return web.json_response(body, status=status)

    base = await make_server([web.get("/api/v2/settings", handler)])
    with pytest.raises(MioApiError) as error:
        await make_client(base, retries=0).get("/settings")
    details = error.value.to_dict()
    assert (details["code"], details["status"], details["retryable"]) == (kind, status, retryable)
    assert TEST_TOKEN not in str(details)
    assert "private" not in str(details)
    assert details["message"]


async def test_retry_only_get(make_server, make_client):
    calls = Counter()

    async def handler(request):
        calls[request.method] += 1
        if request.method == "GET" and calls["GET"] == 2:
            return web.json_response({"ok": True})
        return web.json_response({"detail": "temporarily offline"}, status=503)

    base = await make_server([web.route("*", "/api/v2/jobs", handler)])
    client = make_client(base)
    assert await client.get("/jobs") == {"ok": True}
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(MioApiError, match="服务暂时") as error:
            await client.request(method, "/jobs")
        assert error.value.status == 503
    assert calls == {"GET": 2, "POST": 1, "PUT": 1, "PATCH": 1, "DELETE": 1}


async def test_timeout_has_friendly_error(make_server, make_client):
    release = asyncio.Event()

    async def blocked(_request):
        await release.wait()
        return web.json_response({"ok": True})

    base = await make_server([web.get("/api/v2/jobs/job", blocked)])
    try:
        with pytest.raises(MioApiError, match="超时") as error:
            await make_client(base, retries=0).request("GET", "/jobs/job", timeout=0.05)
    finally:
        release.set()
    assert error.value.code == "timeout"
    assert error.value.retryable


@pytest.mark.parametrize("legacy", [True, False])
async def test_missing_v2_detects_old_service_without_forwarding_token(
    make_server, make_client, legacy
):
    seen = []

    async def old_probe(request):
        seen.append(request.path)
        assert "Authorization" not in request.headers
        if legacy:
            return web.json_response({"data": {"version": "1.4"}, "requestId": "sample"})
        return web.Response(text="<html>not Mio</html>", content_type="text/html")

    base = await make_server([web.get("/api/v1/health", old_probe)])
    with pytest.raises(MioApiError) as error:
        await make_client(base).probe()
    assert seen == ["/api/v1/health"]
    assert error.value.code == ("legacy_v1" if legacy else "v2_unavailable")
    if legacy:
        assert error.value.status == 426
        assert "升级" in error.value.message


async def test_redirect_does_not_send_bearer_or_follow(make_server, make_client):
    hits = []

    async def trap(request):
        hits.append(request.path)
        return web.json_response({"ok": False})

    other = await make_server([web.get("/trap", trap)])

    async def redirect(_request):
        raise web.HTTPFound(f"{other}/trap")

    base = await make_server([web.get("/api/v2/jobs", redirect)])
    with pytest.raises(MioApiError) as error:
        await make_client(base).get("/jobs")
    assert error.value.code == "redirect"
    assert not hits


def test_normalize_base_url_and_reject_invalid_input():
    assert normalize_base_url("https://example.test/proxy/api/v2/") == "https://example.test/proxy"
    assert normalize_base_url("http://[::1]:8788/api/v1") == "http://[::1]:8788"
    for url in (
        "/relative",
        "ftp://example.test",
        "https://secret@example.test",
        "https://example.test/?query",
        "https://example.test:0",
        "https://example.test:/x/../y",
        "https://example.test/%2e%2e/x",
        "https://example.test/%252e%252e/x",
    ):
        with pytest.raises(ValueError, match="Mio 地址"):
            normalize_base_url(url)


async def test_binary_download_streams_and_keeps_cache_capped(make_server, make_client, tmp_path):
    download = b"0123456789abcdef" * 16384
    initial = asyncio.Event()
    resume = asyncio.Event()
    name = quote("今晚画册.zip")

    async def handler(request):
        response = web.StreamResponse(
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": f"attachment; filename*=UTF-8''{name}",
            }
        )
        await response.prepare(request)
        await response.write(download)
        initial.set()
        await resume.wait()
        for _ in range(39):
            await response.write(download)
        return response

    base = await make_server([web.get("/api/v2/export/jobs/{job_id}/file", handler)])
    client = make_client(base)
    size = len(download) * 40
    cache = DownloadCache(tmp_path, max_bytes=size + 16, max_files=3)
    pending = asyncio.create_task(
        client.download_operation("download_export", cache=cache, path_params={"job_id": "one"})
    )
    try:
        async with asyncio.timeout(3):
            await initial.wait()
            while not any(path.stat().st_size > 0 for path in tmp_path.glob("mio-*/.part")):
                await asyncio.sleep(0.01)
        assert not list(tmp_path.glob("mio-*/今晚画册.zip"))
    finally:
        resume.set()
    result = await pending
    assert result.filename == "今晚画册.zip"
    assert result.content_type == "application/zip"
    assert result.size == size == result.path.stat().st_size
    with result.path.open("rb") as saved:
        assert (
            hashlib.file_digest(saved, "sha256").digest() == hashlib.sha256(download * 40).digest()
        )
    next_result = await client.download_operation(
        "download_export", cache=cache, path_params={"job_id": "two"}
    )
    assert next_result.path.is_file()
    assert next_result.path != result.path
    assert not result.path.exists()


async def test_cache_count_and_safe_basename(make_server, make_client, tmp_path):
    async def handler(_request):
        name = quote("../../CON.zip", safe="")
        return web.Response(
            body=b"sample", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"}
        )

    base = await make_server([web.get("/api/v2/export/jobs/job/file", handler)])
    client = make_client(base)
    cache = DownloadCache(tmp_path, max_bytes=1024, max_files=1)
    first = await client.download("GET", "/export/jobs/job/file", cache=cache)
    second = await client.download("GET", "/export/jobs/job/file", cache=cache)
    assert second.path.parent.parent == tmp_path
    assert "/" not in second.filename and "\\" not in second.filename
    assert second.path.is_file()
    assert not first.path.exists()
    assert not list(tmp_path.glob("mio-*/.part"))


@pytest.mark.parametrize("with_length", [True, False])
async def test_overlarge_download_rejects_and_cleans_up(
    make_server, make_client, tmp_path, with_length
):
    async def handler(request):
        if with_length:
            return web.Response(body=b"1234")
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(b"12")
        await response.write(b"34")
        return response

    base = await make_server([web.get("/api/v2/file", handler)])
    with pytest.raises(MioApiError) as error:
        await make_client(base).download("GET", "/file", cache=DownloadCache(tmp_path, max_bytes=3))
    assert error.value.code == "file_too_large"
    assert not list(tmp_path.iterdir())


async def test_cancelled_download_cleans_partial_file(make_server, make_client, tmp_path):
    release = asyncio.Event()

    async def handler(request):
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(b"partial")
        await release.wait()
        return response

    base = await make_server([web.get("/api/v2/file", handler)])
    client = make_client(base)
    task = asyncio.create_task(client.download("GET", "/file", cache=DownloadCache(tmp_path)))
    try:
        async with asyncio.timeout(3):
            while not list(tmp_path.glob("mio-*/.part")):
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not list(tmp_path.iterdir())
    finally:
        release.set()
