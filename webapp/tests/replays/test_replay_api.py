"""Token API requests, with SQLite and an in-process worker (no sockets)."""
import base64
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base, get_db
from app.models.deploy_drain import DeployDrain
from app.models.replay import Replay, ReplayUpload
from app.models.upload_token import UploadToken
from app.routers import replay_api
from app.services import deploy_drain, replay_upload as uploads, upload_activity, upload_tokens
from tests.support.http import Client
from replay_synthetic import SyntheticMatch
from test_replay_store import TABLES, condensed  # noqa: F401 (fixture)

UPLOAD = "/api/replays/upload"
CHECK = "/api/replays/check"
KEY = "/api/replays/key"
UPLOAD_ID = "0f0e0d0c-0b0a-4908-8706-050403020100"
STATUS = f"/api/replays/uploads/{UPLOAD_ID}/status"
ROUTES = [("POST", UPLOAD), ("POST", CHECK), ("GET", KEY), ("GET", STATUS)]


class Worker:
    def __init__(self):
        self.base = f"http://fake/{uuid4()}"
        self.submitted, self.acked = [], []
        self.streams = []
        self.answer = {"status": "parsing"}
        self.error = None

    def submit(self, stream, size):
        assert upload_activity.receiving() == 1
        self.streams.append(stream)
        self.submitted.append((stream.read(), size))
        if self.error:
            raise self.error
        return {"id": "job1"}

    def job(self, job_id):
        assert job_id == "job1"
        return self.answer

    def health(self, timeout=None):
        return {}

    def ack(self, *args, **kwargs):
        self.acked.append((args, kwargs))
        return {"ok": True}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_upload_code", "x")
    monkeypatch.setattr(settings, "replay_worker_url", "http://fake")
    engine = create_engine(f"sqlite:///{tmp_path / 'api.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[*TABLES, ReplayUpload.__table__, DeployDrain.__table__,
                                            UploadToken.__table__])
    factory = sessionmaker(bind=engine)
    with factory() as db:
        token, key = upload_tokens.mint(db, "Synthetic uploader")
        token_id = token.id
        other, other_key = upload_tokens.mint(db, "Other uploader")
        other_id = other.id
    app = FastAPI()
    app.include_router(replay_api.router)

    def sessions():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = sessions
    worker = Worker()
    monkeypatch.setattr(uploads, "client", lambda: worker)
    upload_activity.reset()
    yield SimpleNamespace(client=Client(app), factory=factory, worker=worker, key=key, token_id=token_id,
                          other_key=other_key, other_id=other_id)
    upload_activity.reset()
    engine.dispose()


def send(api, method, path, key=True, **kwargs):
    headers = kwargs.pop("headers", {})
    if key:
        headers["authorization"] = f"Bearer {api.key if key is True else key}"
    response = api.client.request(method, path, headers=headers, **kwargs)
    assert response.headers["cache-control"] == "no-store"
    return response


def last_used(api):
    with api.factory() as db:
        return db.get(UploadToken, api.token_id).last_used_at


def add_upload(api, session_key=None, status="parsing", sha="a" * 64, upload_id=UPLOAD_ID):
    with api.factory() as db:
        db.add(ReplayUpload(id=upload_id, session_key=session_key or f"app:{api.token_id}", status=status,
                            worker_job_id="job1", source_sha256=sha, created_at=datetime.now(timezone.utc)))
        db.commit()


def upload(api, data=None):
    return send(api, "POST", UPLOAD, files={"file": ("synthetic.vrf", data if data is not None else
                                                    SyntheticMatch(shape="swiftplay").vrf_bytes,
                                                    "application/octet-stream")})


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("auth", [None, "junk", "unknown", "revoked", "basic", "empty"])
@pytest.mark.parametrize("body", ["json", "missing", "multipart"])
def test_auth_gates_precede_body_parsing(api, method, path, auth, body):
    key = {None: False, "junk": "junk", "unknown": "vwf1_" + "z" * 43}.get(auth, api.key)
    headers = {}
    if auth == "revoked":
        with api.factory() as db:
            upload_tokens.revoke(db, api.token_id, datetime.now(timezone.utc))
    elif auth in ("basic", "empty"):
        key = False
        headers["authorization"] = "Basic junk" if auth == "basic" else "Bearer "
    kwargs = {"json": {}} if body == "missing" else {
        "data": b"{broken" if body == "json" else b"broken multipart",
        "headers": {"content-type": "application/json" if body == "json" else
                    "multipart/form-data; boundary=synthetic", **headers}}
    if body == "missing":
        kwargs["headers"] = headers
    response = send(api, method, path, key=key, **kwargs)
    assert response.status_code == 401
    assert response.json() == {"error": "unknown or revoked key"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert last_used(api) is None
    assert not api.worker.submitted


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("setting,value", [("demo_mode", True), ("replay_upload_code", None),
                                           ("replay_worker_url", None)])
def test_disabled_gates_precede_auth_and_body(api, monkeypatch, method, path, setting, value):
    monkeypatch.setattr(settings, setting, value)
    for key in (True, False):
        assert send(api, method, path, key=key, data=b"bad", headers={
            "content-type": "multipart/form-data; boundary=broken"}).status_code == 404
    assert last_used(api) is None


@pytest.mark.parametrize("payload", [b"{bad", b"[]", b"{}", b'{"sha256":null}', b'{"sha256":5}'])
def test_bad_check_body_is_400(api, payload):
    response = send(api, "POST", CHECK, data=payload, headers={"content-type": "application/json"})
    assert response.status_code == 400 and "error" in response.json()
    assert last_used(api) is None


@pytest.mark.parametrize("kwargs", [{}, {"data": {"file": "text"}}, {"files": {"other": ("x", b"x", "text/plain")}},
                                    {"data": b"broken", "headers": {"content-type": "multipart/form-data"}}])
def test_bad_upload_body_is_400(api, kwargs):
    response = send(api, "POST", UPLOAD, **kwargs)
    assert response.status_code == 400 and "error" in response.json()
    assert last_used(api) is None


def test_upload_uses_token_session_and_closes_file(api):
    response = upload(api)
    assert response.status_code == 200
    body = response.json()
    assert body == {"upload_id": body["upload_id"],
                    "status_url": f"/api/replays/uploads/{body['upload_id']}/status"}
    with api.factory() as db:
        row = db.get(ReplayUpload, body["upload_id"])
        assert row.session_key == f"app:{api.token_id}" and row.status == "parsing"
    assert len(api.worker.submitted) == 1
    assert all(stream.closed for stream in api.worker.streams)
    assert upload_activity.receiving() == 0 and upload_activity.last_end_age_s() is not None


def test_upload_database_work_stays_off_event_loop(api):
    request_thread = threading.get_ident()
    engine = api.factory.kw["bind"]

    def before_query(*args):
        assert threading.get_ident() != request_thread

    event.listen(engine, "before_cursor_execute", before_query)
    try:
        assert upload(api).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", before_query)


def test_upload_stores_worker_result_and_acknowledges(api, condensed):
    response = upload(api)
    assert response.status_code == 200
    with api.factory() as db:
        sha = db.get(ReplayUpload, response.json()["upload_id"]).source_sha256
    api.worker.answer = {"status": "done", "result": {
        "match_uuid": condensed.match_uuid, "map_name": condensed.map_name, "game_branch": condensed.game_branch,
        "source_sha256": sha, "recipe": condensed.recipe, "hz": condensed.hz, "players": condensed.players,
        "link_inputs": condensed.link_inputs, "report": condensed.report,
        "rounds": {str(n): base64.b64encode(blob).decode() for n, blob in condensed.encoded_rounds().items()}}}
    status = send(api, "GET", response.json()["status_url"])
    assert status.status_code == 200 and status.json()["status"] == "stored"
    with api.factory() as db:
        row = db.get(ReplayUpload, response.json()["upload_id"])
        assert row.status == "stored" and row.replay_id is not None
        assert db.get(Replay, row.replay_id).source_sha256 == sha
    assert len(api.worker.acked) == 1


def test_upload_error_mapping(api):
    assert upload(api, b"not a replay").status_code == 400
    api.worker.error = uploads.WorkerError("unavailable")
    response = upload(api)
    assert response.status_code == 503 and response.headers["retry-after"] == "600"
    assert "error" in response.json() and last_used(api) is None
    assert all(stream.closed for stream in api.worker.streams)


def test_upload_batch_limit(api):
    for _ in range(uploads.LOCAL_BATCH):
        add_upload(api, upload_id=str(uuid4()))
    response = upload(api)
    assert response.status_code == 429 and response.headers["retry-after"] == "600"
    assert not api.worker.submitted and last_used(api) is None


def test_only_admitted_uploads_affect_drain_quiet_timer(api, monkeypatch):
    original_form = replay_api.Request.form
    parsed = []

    def form(request, *args, **kwargs):
        assert upload_activity.receiving() == 1
        parsed.append(True)
        return original_form(request, *args, **kwargs)

    monkeypatch.setattr(replay_api.Request, "form", form)
    monkeypatch.setattr(deploy_drain, "paused", lambda db, now: True)
    # This body cannot be parsed: admission must refuse it unread.
    response = send(api, "POST", UPLOAD, data=b"broken", headers={"content-type": "multipart/form-data"})
    assert response.status_code == 503 and response.json()["deploy"] is True
    assert response.headers["retry-after"] == "600"
    assert send(api, "POST", UPLOAD, key=False).status_code == 401
    monkeypatch.setattr(settings, "demo_mode", True)
    assert send(api, "POST", UPLOAD).status_code == 404
    assert upload_activity.receiving() == 0 and upload_activity.last_end_age_s() is None
    assert not api.worker.submitted and last_used(api) is None
    assert not parsed
    with api.factory() as db:
        assert db.query(ReplayUpload).count() == 0
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(deploy_drain, "paused", lambda db, now: False)
    assert upload(api).status_code == 200
    assert parsed == [True]
    assert upload_activity.receiving() == 0 and upload_activity.last_end_age_s() is not None


@pytest.mark.parametrize("owner", ["own", "other", "browser"])
def test_status_ownership(api, owner):
    session = {"own": f"app:{api.token_id}", "other": f"app:{api.other_id}", "browser": "abc"}[owner]
    add_upload(api, session_key=session)
    response = send(api, "GET", STATUS)
    assert response.status_code == (200 if owner == "own" else 404)
    if owner == "own":
        assert response.json() == {"status": "parsing", "error": None}
    else:
        assert last_used(api) is None


def test_status_bad_or_missing_uuid(api):
    for value in ("bad", str(uuid4())):
        assert send(api, "GET", f"/api/replays/uploads/{value}/status").status_code == 404


def test_status_releases_transaction_before_touch(api, monkeypatch):
    add_upload(api)
    original_touch = upload_tokens.touch
    touched = []

    def touch(db, token, now):
        assert not db.in_transaction()
        touched.append(True)
        original_touch(db, token, now)

    monkeypatch.setattr(upload_tokens, "touch", touch)
    assert send(api, "GET", STATUS).status_code == 200
    assert touched == [True]


@pytest.mark.parametrize("source,known", [("replay", True), ("stored", True), ("failed", False), ("parsing", False)])
def test_check_known_sources(api, source, known):
    sha = "abcdef01" * 8
    if source == "replay":
        with api.factory() as db:
            db.add(Replay(match_uuid=str(uuid4()), map_name="Ascent", round_count=1, format_version=1,
                          recipe="synthetic", game_branch="synthetic", source_sha256=sha, source="upload",
                          link_status="unlinked", link_inputs={}))
            db.commit()
    else:
        add_upload(api, status=source, sha=sha)
    response = send(api, "POST", CHECK, json={"sha256": sha.upper()})
    assert response.status_code == 200 and response.json() == {"known": known}


@pytest.mark.parametrize("sha", ["xyz", "a" * 63, "a" * 65, "g" * 64, "a" * 63 + "\n"])
def test_check_invalid_sha(api, sha):
    assert send(api, "POST", CHECK, json={"sha256": sha}).status_code == 400
    assert last_used(api) is None


def test_key_label(api):
    response = send(api, "GET", KEY)
    assert response.status_code == 200 and response.json() == {"ok": True, "label": "Synthetic uploader"}
    assert last_used(api) is not None


@pytest.mark.parametrize("route", ["upload", "status", "check", "key"])
def test_each_success_touches_at_most_once_per_minute(api, monkeypatch, route):
    now = datetime(2026, 11, 22, tzinfo=timezone.utc)

    class Clock:
        @staticmethod
        def now(tz):
            return now

    monkeypatch.setattr(replay_api, "datetime", Clock)
    add_upload(api, status="stored")

    def call():
        if route == "upload":
            return upload(api)
        method, path = {"status": ("GET", STATUS), "check": ("POST", CHECK), "key": ("GET", KEY)}[route]
        return send(api, method, path, **({"json": {"sha256": "b" * 64}} if route == "check" else {}))

    for seconds, expected in [(0, 0), (10, 0), (61, 61)]:
        now = datetime(2026, 11, 22, tzinfo=timezone.utc) + timedelta(seconds=seconds)
        assert call().status_code == 200
        assert last_used(api) == datetime(2026, 11, 22) + timedelta(seconds=expected)


def test_main_registers_all_api_routes():
    from app.main import app
    expected = {(method, path.replace(UPLOAD_ID, "{upload_id}")) for method, path in ROUTES}
    actual = {(method, route.path) for route in app.routes for method in getattr(route, "methods", ())}
    assert expected <= actual
