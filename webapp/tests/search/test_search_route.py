"""Replay search admin route (W6): GET /admin/replays/search is hidden without the token and in demo mode, takes the
raw query string as ordered key=value pairs (a repeated key stays repeated), maps a FilterError to 400 naming the
key, and is registered not_page in site_health.PAGE_ROUTES."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.config import settings
from app.db import get_db
from app.routers import replay_admin
from app.services import replay_search as lib
from app.services import site_health

from tests.search.test_search_library import factory  # noqa: F401  (the toy DB fixture)

AUTH = {"Authorization": "Bearer t"}


class AsgiClient:
    """A minimal in-process GET client (the test venv has no httpx, so no TestClient), as test_deploy_admin does."""

    def __init__(self, app):
        self.app = app

    def get(self, url, headers=None):
        path, _, query = url.partition("?")
        raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
                 "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
                 "root_path": "", "headers": raw, "client": ("testclient", 1), "server": ("testserver", 80)}
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        asyncio.run(asyncio.wait_for(self.app(scope, receive, send), timeout=5))
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        payload = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return SimpleNamespace(status_code=status, json=lambda: json.loads(payload))


@pytest.fixture
def client(factory, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "replay_admin_token", "t")
    monkeypatch.setattr(settings, "demo_mode", False)

    def database():
        with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(replay_admin.router)
    app.dependency_overrides[get_db] = database
    return AsgiClient(app)


def test_no_token_or_wrong_token_is_404(client):
    assert client.get("/admin/replays/search?kind=kill").status_code == 404
    assert client.get("/admin/replays/search?kind=kill", headers={"Authorization": "Bearer x"}).status_code == 404


def test_demo_mode_is_404(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    assert client.get("/admin/replays/search?kind=kill", headers=AUTH).status_code == 404


def test_search_returns_hits(client, factory):  # noqa: F811
    response = client.get("/admin/replays/search?kind=death&first=1&side=attack", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    with factory() as db:
        want = lib.search(db, ["kind=death", "first=1", "side=attack"])
    assert body["total"] == want["total"] > 0
    assert body["hits"] == want["hits"]
    assert all(h["kind"] == "death" for h in body["hits"])


def test_bad_key_is_400_naming_it(client):
    response = client.get("/admin/replays/search?bogus=1", headers=AUTH)
    assert response.status_code == 400
    assert "bogus" in response.json()["error"]


def test_our_filter_without_perspective_is_400(client):
    response = client.get("/admin/replays/search?kind=kill&first=1&won=1", headers=AUTH)
    assert response.status_code == 400
    assert response.json()["error"]


def test_repeated_tag_params_are_both_applied(client, factory):  # noqa: F811
    one = client.get("/admin/replays/search?tag=won", headers=AUTH).json()
    both = client.get("/admin/replays/search?tag=won&tag=other", headers=AUTH).json()
    flipped = client.get("/admin/replays/search?tag=other&tag=won", headers=AUTH).json()
    with factory() as db:
        single = lib.search(db, ["tag=won"])
    assert one["total"] == single["total"] > 0
    # tags are ANDed: the second tag must narrow the result, so neither was dropped
    assert both["total"] == 0 and flipped["total"] == 0


def test_page_param_is_passed_through(client):
    body = client.get("/admin/replays/search?kind=kill&page=2", headers=AUTH).json()
    assert body["page"] == 2 and body["hits"] == []


def test_route_is_registered_not_page():
    assert site_health.PAGE_ROUTES["/admin/replays/search"] == "not_page"
