"""Slow first page loads use route templates and finish at the last body chunk."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.routing import APIRoute

from app.config import settings
from app.services import site_health, site_health_web


class TestClient:
    """Minimal ASGI client, with no database or lifespan startup."""

    __test__ = False

    def __init__(self, app):
        self.app = app

    def request(self, target, method="GET", on_send=None):
        path, _, query = target.partition("?")
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": query.encode(), "root_path": "",
            "client": ("127.0.0.1", 1), "server": ("testserver", 80), "headers": [],
        }
        messages = []
        request_received = False

        async def receive():
            nonlocal request_received
            if request_received:
                await asyncio.Event().wait()
            request_received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)
            if on_send:
                on_send(message)

        asyncio.run(self.app(scope, receive, send))
        return messages


def clock(monkeypatch, *values):
    ticks = iter(values)
    monkeypatch.setattr(site_health_web, "_clock", lambda: next(ticks))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    app = FastAPI()
    app.add_middleware(site_health_web.SlowPageMiddleware)

    @app.get("/matches/{external_id}")
    @app.post("/matches/{external_id}")
    async def page(external_id: str):
        return HTMLResponse("page")

    @app.get("/health")
    async def data():
        return {"status": "ok"}

    @app.get("/replays/upload")
    async def stream():
        async def chunks():
            yield b"first"
            yield b"last"
        return StreamingResponse(chunks(), media_type="text/html")

    return TestClient(app)


def test_every_get_route_is_classified():
    from app.main import app

    paths = {r.path for r in app.routes if isinstance(r, APIRoute) and "GET" in r.methods}
    assert set(site_health.PAGE_ROUTES) == paths
    assert set(site_health.PAGE_ROUTES.values()) == {"page", "not_page"}


def test_first_page_load_classifications():
    excluded = {
        "/players/{display_name}/career", "/players/{display_name}/recent-matches",
        "/stats/friends/career", "/stats/map-control", "/stats/all", "/health",
        "/accounts/search", "/matches/{external_id}/rounds/{round_number}", "/squad/career",
        "/replays/{match_uuid}/coach/prompt", "/replays/uploads/{upload_id}/status",
        "/admin/replays/archive", "/admin/replays/reparse/status", "/admin/replays/uploads/{upload_id}",
    }
    excluded.update(path for path in site_health.PAGE_ROUTES if path.endswith((".json", ".bin", ".txt")))
    assert all(site_health.PAGE_ROUTES[path] == "not_page" for path in excluded)
    assert site_health.PAGE_ROUTES["/replays/upload"] == "page"
    assert site_health.PAGE_ROUTES["/replays/uploads/{upload_id}"] == "page"
    assert site_health.PAGE_ROUTES["/replays/upload/guide/{key}"] == "page"


def test_slow_pages_merge_by_template_without_query(client, monkeypatch):
    clock(monkeypatch, 0, 6, 0, 8)
    client.request("/matches/1?secret=excluded")
    counter = site_health._counters[("slow_page", "/matches/{external_id}")]
    assert counter["n"] == 1
    assert counter["context"] == {
        "slowest_s": 6, "slowest_path": "/matches/1",
        "last": [{"s": 6, "path": "/matches/1"}], "n": 1,
    }
    client.request("/matches/2")
    assert counter["n"] == 2
    assert counter["context"] == {
        "slowest_s": 8, "slowest_path": "/matches/2",
        "last": [{"s": 6, "path": "/matches/1"}, {"s": 8, "path": "/matches/2"}], "n": 2,
    }


def test_keeps_slowest_and_only_last_five(client, monkeypatch):
    clock(monkeypatch, 0, 12, 0, 7, 0, 6, 0, 9, 0, 8, 0, 10.126)
    for number in range(6):
        client.request(f"/matches/{number}")
    context = site_health._counters[("slow_page", "/matches/{external_id}")]["context"]
    assert context["n"] == 6
    assert context["slowest_s"] == 12
    assert context["slowest_path"] == "/matches/0"
    assert [item["path"] for item in context["last"]] == [f"/matches/{n}" for n in range(1, 6)]
    assert context["last"][-1]["s"] == 10.13


@pytest.mark.parametrize("target,method,seconds", [
    ("/health", "GET", 6), ("/matches/1", "GET", 4), ("/matches/1", "GET", 5),
    ("/matches/1", "POST", 6), ("/missing", "GET", 6), ("/static/missing", "GET", 6),
])
def test_requests_that_do_not_count(client, monkeypatch, target, method, seconds):
    clock(monkeypatch, 0, seconds)
    client.request(target, method)
    assert not site_health._counters


def test_stream_uses_clock_after_last_chunk(client, monkeypatch):
    elapsed = 0
    reads = []

    def read_clock():
        reads.append(elapsed)
        return elapsed

    def advance(message):
        nonlocal elapsed
        if message["type"] == "http.response.body":
            elapsed += 3

    monkeypatch.setattr(site_health_web, "_clock", read_clock)
    messages = client.request("/replays/upload", on_send=advance)
    assert b"".join(message.get("body", b"") for message in messages) == b"firstlast"
    assert reads == [0, 9]
    assert site_health._counters[("slow_page", "/replays/upload")]["context"]["slowest_s"] == 9


def test_demo_is_checked_per_request(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    clock(monkeypatch, 0, 6)
    client.request("/matches/1")
    assert not site_health._counters
    monkeypatch.setattr(settings, "demo_mode", False)
    client.request("/matches/1")
    assert site_health._counters[("slow_page", "/matches/{external_id}")]["n"] == 1


@pytest.mark.parametrize("broken", ["_clock", "finish_clock", "count"])
def test_instrumentation_failure_preserves_response(client, monkeypatch, broken):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic instrumentation failure")

    clock(monkeypatch, 0, 6)
    if broken == "finish_clock":
        clock(monkeypatch, 0)
    else:
        module = site_health_web if broken == "_clock" else site_health
        monkeypatch.setattr(module, broken, fail)
    messages = client.request("/matches/1")
    assert messages[0]["status"] == 200
    assert messages[-1]["body"] == b"page"


def test_app_exception_propagates(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    clock(monkeypatch, 0)
    error = RuntimeError("synthetic app failure")

    async def app(scope, receive, send):
        raise error

    with pytest.raises(RuntimeError) as caught:
        TestClient(site_health_web.SlowPageMiddleware(app)).request("/matches/1")
    assert caught.value is error
    assert not site_health._counters


def test_unrouted_maintenance_response_is_skipped(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    clock(monkeypatch, 0, 6)

    async def maintenance(scope, receive, send):
        await HTMLResponse("maintenance", status_code=503)(scope, receive, send)

    messages = TestClient(site_health_web.SlowPageMiddleware(maintenance)).request("/matches/1")
    assert messages[0]["status"] == 503
    assert not site_health._counters


def test_non_http_scope_passes_through(monkeypatch):
    def fail():
        raise AssertionError("clock called")

    monkeypatch.setattr(site_health_web, "_clock", fail)
    scope = {"type": "lifespan"}
    seen = []

    async def app(scope, receive, send):
        seen.append(scope)

    asyncio.run(site_health_web.SlowPageMiddleware(app)(scope, None, None))
    assert seen == [scope]


def test_main_installs_middleware():
    from app.main import app

    assert site_health_web.SlowPageMiddleware in [m.cls for m in app.user_middleware]


def test_slow_pages_are_stored_events_in_pages_group():
    from app.services import replay_errors

    assert ("slow_page", "Slow page loads") in replay_errors.KINDS
    assert replay_errors.GROUPS["slow_page"] == "Pages"
    assert "slow_page" in replay_errors.EVENT_KINDS
