"""Pages that crash are recorded as page_error (W8): a fresh app, never main.app's routes."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.services import site_health
from app.services.site_health_web import TRACEBACK_LINES, page_error_handler


def make_app():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.add_exception_handler(Exception, page_error_handler)

    @app.get("/boom/{n}")
    def boom(n: int):
        raise ValueError(f"synthetic {n}")

    @app.get("/missing")
    def missing():
        raise HTTPException(404)

    @app.get("/forbidden")
    def forbidden():
        raise HTTPException(403)

    return app


class TestClient:
    """Minimal ASGI caller (httpx is not installed): swallows the re-raise after the handler."""
    __test__ = False

    def __init__(self, app, raise_server_exceptions=False):
        self.app, self.cookie = app, None

    def get(self, target):
        path, _, query = target.partition("?")
        headers = [(b"host", b"testserver")] + ([(b"cookie", self.cookie.encode())] if self.cookie else [])
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
                 "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
                 "root_path": "", "client": ("127.0.0.1", 1), "server": ("testserver", 80), "headers": headers}
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        try:
            asyncio.run(self.app(scope, receive, send))
        except Exception:
            pass
        start = next(m for m in messages if m["type"] == "http.response.start")
        for key, value in start["headers"]:
            if key == b"set-cookie":
                self.cookie = value.decode().split(";")[0]
        body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return SimpleNamespace(status_code=start["status"], text=body.decode())


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    return TestClient(make_app())


def test_a_crash_is_enqueued_and_the_500_is_unchanged(client):
    response = client.get("/boom/7?x=1")
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    [entry] = list(site_health._queue)
    assert "page_error" in repr(entry) and "GET /boom/{n}" in repr(entry)
    assert "ValueError: synthetic 7" in repr(entry)
    assert "'/boom/7'" in repr(entry) or '"/boom/7"' in repr(entry)
    assert "x=1" not in repr(entry)


def test_the_record_fields(client, monkeypatch):
    seen = []
    monkeypatch.setattr(site_health, "enqueue", lambda *a, **k: seen.append((a, k)))
    client.get("/boom/7?x=1")
    [(args, _)] = seen
    kind, key, error, context = args
    assert (kind, key, error) == ("page_error", "GET /boom/{n}", "ValueError: synthetic 7")
    assert context["type"] == "ValueError"
    assert context["path"] == "/boom/7"
    assert context["logged_in"] is False
    assert isinstance(context["traceback"], str)
    assert len(context["traceback"].splitlines()) <= TRACEBACK_LINES
    assert "ValueError" in context["traceback"]


def test_http_exceptions_are_not_recorded(client):
    assert client.get("/missing").status_code == 404
    assert client.get("/forbidden").status_code == 403
    assert not site_health._queue


def test_unrouted_key_and_no_session(monkeypatch):
    seen = []
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(site_health, "enqueue", lambda *a, **k: seen.append(a))
    scope = {"type": "http", "method": "POST", "path": "/nowhere", "query_string": b"a=1", "headers": []}
    response = page_error_handler(Request(scope), RuntimeError("x"))
    assert response.status_code == 500 and response.body == b"Internal Server Error"
    [(kind, key, error, context)] = seen
    assert key == "POST (unrouted)" and context["logged_in"] is False and context["path"] == "/nowhere"


def test_logged_in_flag(monkeypatch):
    app = make_app()

    @app.get("/login")
    def login(request: Request):
        request.session["player_id"] = 1
        return "ok"

    seen = []
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(site_health, "enqueue", lambda *a, **k: seen.append(a))
    c = TestClient(app)
    c.get("/login")
    c.get("/boom/1")
    assert seen[0][3]["logged_in"] is True


def test_the_database_is_never_touched(client, monkeypatch):
    import app.db

    def fail(*a, **k):
        raise AssertionError("database touched")
    monkeypatch.setattr(app.db, "SessionLocal", fail)
    if hasattr(site_health, "SessionLocal"):
        monkeypatch.setattr(site_health, "SessionLocal", fail)
    assert client.get("/boom/1").status_code == 500


def test_enqueue_failure_never_breaks_the_500(client, monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("queue broke")
    monkeypatch.setattr(site_health, "enqueue", fail)
    response = client.get("/boom/1")
    assert response.status_code == 500 and response.text == "Internal Server Error"


def test_demo_mode_enqueues_nothing(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    c = TestClient(make_app())
    assert c.get("/boom/1").status_code == 500
    assert not site_health._queue


def test_main_app_has_the_handler():
    from app.main import app
    assert app.exception_handlers[Exception] is page_error_handler
