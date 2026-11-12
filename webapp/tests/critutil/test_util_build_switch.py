"""Remote-build switch and operator entry point, using synthetic SQLite data and ASGI."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings, settings
from app.critutil import build_digest
from app.db import Base, get_db
from app.models.friendship import Friendship
from app.models.replay import Replay, ReplayRoundUtil, UtilModel
from app.routers import replay_admin
from app.services import replay_util, util_build_remote, util_model, util_rebuild, util_values
from scripts import _admin_client
from tests.critutil.test_util_web import (  # noqa: F401 (shared synthetic fixtures)
    condensed, db, linked, page_db, page_linked, put_model, put_util,
)


WEBAPP = Path(__file__).resolve().parents[2]
PATH = "/admin/replays/util/build"


def test_setting_defaults_off_and_reads_environment(monkeypatch):
    monkeypatch.delenv("UTIL_BUILD_REMOTE", raising=False)
    assert Settings(_env_file=None).util_build_remote is False
    monkeypatch.setenv("UTIL_BUILD_REMOTE", "true")
    assert Settings(_env_file=None).util_build_remote is True


def test_render_enables_only_the_web_services_util_build_switch():
    text = (WEBAPP.parent / "render.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    configured = {service["name"]: env["value"] for service in doc["services"]
                  for env in service.get("envVars", []) if env["key"] == "UTIL_BUILD_REMOTE"}
    assert configured == {"valowithfriendstracker": "true"}
    addition = '      - key: UTIL_BUILD_REMOTE\n        value: "true"\n'
    assert text.count(addition) == 1
    # W11 changes only this setting. The rest of the Blueprint is the pre-W11 pin.
    assert hashlib.sha256(text.replace(addition, "").encode()).hexdigest() == (
        "060dfd68c171133f0ae8f41aa2a11bc8cf2f45584183d17e2e2b57635c0eb097")


@pytest.mark.parametrize("remote", [True, False])
@pytest.mark.parametrize("stored", [None, ("old", {"rows": []})])
def test_stale_page_dispatches_locally_only_with_switch_off(monkeypatch, remote, stored):
    monkeypatch.setattr(settings, "util_build_remote", remote)
    monkeypatch.setattr(replay_util, "_values_row", lambda db: stored)
    monkeypatch.setattr(replay_util, "values_current", lambda *a: False)
    calls = []

    def request():
        if remote:
            pytest.fail("local rebuild requested with remote switch on")
        calls.append(1)

    monkeypatch.setattr(util_rebuild, "request", request)
    assert replay_util.page_values(None) == (stored[1] if stored else None, False)
    assert calls == ([] if remote else [1])


@pytest.mark.parametrize("wait", [False, True])
@pytest.mark.parametrize("has_model", [False, True])
def test_remote_tab_shows_updating_without_any_local_build(db, linked, monkeypatch, wait, has_model):
    monkeypatch.setattr(settings, "util_build_remote", True)
    put_util(db, linked, 1)
    if has_model:
        put_model(db, {"cells": {}}, set())

    def forbidden(*a, **kw):
        pytest.fail("remote tab started a local build or refit")

    monkeypatch.setattr(util_rebuild, "request", forbidden)
    monkeypatch.setattr(util_model, "ensure", forbidden)
    monkeypatch.setattr(replay_util, "ensure_values", forbidden)
    assert replay_util.match_payload(db, linked, wait=wait)["updating"] is True


def test_remote_tab_with_current_products_reads_without_updating(db, linked, monkeypatch):
    monkeypatch.setattr(settings, "util_build_remote", True)
    put_util(db, linked, 1)
    put_model(db, {"cells": {}}, {linked.match_id})
    rows = replay_util.current_rows(db, with_data=False)
    key = replay_util.corpus_key(db, rows, util_model.load(db))
    replay_util._store_values(db, key, {"rows": [], "fingerprints": {
        str(linked.id): {str(n): fp for rid, n, fp, _ in rows if rid == linked.id}}})

    def forbidden(*a, **kw):
        pytest.fail("current remote products triggered a build")

    monkeypatch.setattr(util_rebuild, "request", forbidden)
    monkeypatch.setattr(util_model, "ensure", forbidden)
    monkeypatch.setattr(replay_util, "ensure_values", forbidden)
    assert replay_util.page_values(db)[1] is True
    assert replay_util.match_payload(db, linked, wait=True)["updating"] is False


def test_local_tab_still_initializes_missing_estimator(db, linked, monkeypatch):
    monkeypatch.setattr(settings, "util_build_remote", False)
    put_util(db, linked, 1)
    calls = []

    def ensure(session):
        assert session is db
        calls.append("ensure")
        return {"key": "synthetic-win", "excluded": [linked.match_id], "cells": {}}

    monkeypatch.setattr(util_model, "ensure", ensure)
    monkeypatch.setattr(util_rebuild, "request", lambda: calls.append("request"))
    assert replay_util.match_payload(db, linked)["updating"] is True
    assert calls == ["ensure", "request"]


@pytest.fixture
def admin_site(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in
        (Friendship, Replay, ReplayRoundUtil, UtilModel)])
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "util_build_remote", True)
    monkeypatch.setattr(settings, "replay_admin_token", "synthetic-admin-token")
    monkeypatch.setattr(util_build_remote, "STATE", util_build_remote.UtilState())
    app = FastAPI()
    app.include_router(replay_admin.router)

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = database
    yield app, engine
    engine.dispose()


def post(app, token=None):
    headers = [] if token is None else [(b"authorization", f"Bearer {token}".encode())]
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": PATH, "raw_path": PATH.encode(), "query_string": b"",
             "root_path": "", "headers": headers, "client": ("testclient", 1), "server": ("testserver", 80)}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body)


@pytest.mark.parametrize("token", [None, "", "wrong"])
def test_admin_requires_token(admin_site, token):
    app, _ = admin_site
    assert post(app, token) == (404, {"detail": "Not Found"})
    assert util_build_remote.STATE.asked is False


@pytest.mark.parametrize("setting", ["demo_mode", "util_build_remote", "replay_admin_token"])
def test_admin_hidden_when_disabled(admin_site, monkeypatch, setting):
    app, _ = admin_site
    value = {"demo_mode": True, "util_build_remote": False, "replay_admin_token": None}[setting]
    monkeypatch.setattr(settings, setting, value)
    assert post(app, "synthetic-admin-token") == (404, {"detail": "Not Found"})
    assert util_build_remote.STATE.asked is False


@pytest.mark.parametrize("phase", ["idle", "polling"])
@pytest.mark.parametrize("current", [False, True])
def test_admin_asks_and_reports_current_inputs(admin_site, phase, current):
    app, engine = admin_site
    util_build_remote.STATE.phase = phase
    with Session(engine) as session:
        model = {"key": "synthetic-win", "excluded": [], "cells": {}}
        session.add(UtilModel(name="win", key=model["key"], version=util_model.VERSION, data=model))
        session.commit()
        key = replay_util.corpus_key(session, [], model)
        stored_key = key if current else "synthetic-old-key"
        session.add(UtilModel(name="values", key=stored_key, version=util_values.VERSION, data={"rows": []}))
        session.commit()
    status, body = post(app, "synthetic-admin-token")
    assert status == 200
    assert util_build_remote.STATE.asked is True
    assert body == {"stale": not current, "in_flight": phase != "idle", "key": key,
                    "stored_key": stored_key, "digest": build_digest.digest(WEBAPP)}


def test_admin_reports_missing_products(admin_site):
    app, engine = admin_site
    with Session(engine) as session:
        key = replay_util.corpus_key(session, [], None)
    status, body = post(app, "synthetic-admin-token")
    assert status == 200 and body["stale"] is True
    assert body["stored_key"] is None and body["key"] == key
    assert util_build_remote.STATE.asked is True


@pytest.mark.parametrize("version", [util_values.VERSION, util_values.VERSION + 1])
@pytest.mark.parametrize("covered", [True, False])
def test_admin_checks_values_metadata_without_loading_payload(admin_site, monkeypatch, version, covered):
    app, engine = admin_site
    with Session(engine) as session:
        model = {"key": "synthetic-win", "excluded": [1] if covered else [], "cells": {}}
        session.add(UtilModel(name="win", key=model["key"], version=util_model.VERSION, data=model))
        session.commit()
        key = replay_util.corpus_key(session, [], model)
        session.add(UtilModel(name="values", key=key, version=version, data={"rows": []}))
        session.commit()

    monkeypatch.setattr(util_model, "replay_match_ids", lambda db: {1})

    def forbidden(*a, **kw):
        pytest.fail("loaded the stored values build to check freshness")

    def guard(conn, cursor, statement, parameters, context, many):
        if "util_model.data" in statement and "values" in parameters:
            forbidden()

    monkeypatch.setattr(replay_util, "_values_row", forbidden)
    event.listen(engine, "before_cursor_execute", guard)
    try:
        status, body = post(app, "synthetic-admin-token")
    finally:
        event.remove(engine, "before_cursor_execute", guard)
    assert status == 200 and util_build_remote.STATE.asked is True
    assert body["key"] == key
    assert body["stored_key"] == (key if version == util_values.VERSION else None)
    assert body["stale"] is (version != util_values.VERSION or not covered)


def test_admin_client_requests_util_build(monkeypatch):
    calls = []

    def call(*args):
        calls.append(args)
        return {"stale": True}

    monkeypatch.setattr(_admin_client, "call", call)
    assert _admin_client.request_util_build("https://synthetic.invalid") == {"stale": True}
    assert calls == [("https://synthetic.invalid", "POST", PATH)]
