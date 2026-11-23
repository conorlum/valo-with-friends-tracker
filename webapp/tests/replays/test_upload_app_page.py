"""Self-serve uploader keys and the owner's CLI, without a replay worker."""

import json
import re
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.config import settings
from app.db import Base
from app.models.player import Player
from app.models.upload_token import UploadToken
from app.routers import replays as routes
from app.services import upload_tokens
from app.templates import templates

DOWNLOAD_URL = "https://github.com/conorlum/valo-with-friends-tracker/releases/latest/download/ValoUploader.exe"


@pytest.fixture
def factory(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_upload_code", "synthetic-code")
    monkeypatch.setattr(settings, "replay_worker_url", "http://worker.invalid")
    monkeypatch.setattr(templates, "context_processors", [])
    monkeypatch.setattr(routes.uploads, "batch_limit", lambda worker: 5)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Player.__table__, UploadToken.__table__])
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture
def db(factory):
    with factory() as session:
        yield session


def request(session=None):
    return Request({"type": "http", "method": "POST", "path": "/replays/upload/app-key",
                    "query_string": b"", "headers": [], "session": session or {}})


def authorized(**session):
    return request({"replay_upload_ok": True, **session})


def test_app_key_requires_invite_session(db):
    player = Player(display_name="Synthetic Viewer")
    db.add(player)
    db.commit()
    response = routes.upload_app_key(request({"player_id": player.id}), db)
    assert response.status_code == 403
    assert json.loads(response.body)["error"]
    assert db.query(UploadToken).count() == 0


def test_anonymous_key_verifies_and_uses_utc_date(db, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return cls(2026, 11, 22, tzinfo=tz)

    monkeypatch.setattr(routes, "datetime", Clock)
    response = routes.upload_app_key(authorized(), db)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = json.loads(response.body)
    assert set(body) == {"key", "label"}
    assert body["label"] == "unnamed 2026-11-22"
    token = upload_tokens.verify(db, body["key"])
    assert token.label == body["label"] and token.player_id is None


@pytest.mark.parametrize("name", ["Synthetic Viewer", "x" * 128])
def test_player_label_and_length(db, name):
    player = Player(display_name=name)
    db.add(player)
    db.commit()
    body = json.loads(routes.upload_app_key(authorized(player_id=player.id), db).body)
    expected = f"{name} {datetime.now(timezone.utc).date()}"[:80]
    assert body["label"] == expected
    token = upload_tokens.verify(db, body["key"])
    assert token.player_id == player.id and token.label == expected


def test_each_click_mints_a_fresh_key(db):
    first = json.loads(routes.upload_app_key(authorized(), db).body)
    second = json.loads(routes.upload_app_key(authorized(), db).body)
    assert first["key"] != second["key"]
    assert db.query(UploadToken).count() == 2


@pytest.mark.parametrize("setting,value", [("demo_mode", True), ("replay_upload_code", None),
                                           ("replay_worker_url", None)])
def test_disabled_upload_never_mints(db, monkeypatch, setting, value):
    monkeypatch.setattr(settings, setting, value)
    with pytest.raises(HTTPException) as raised:
        routes.upload_app_key(authorized(), db)
    assert raised.value.status_code == 404
    assert db.query(UploadToken).count() == 0


def test_download_and_copy_controls_only_for_invited_session(db):
    page = routes.upload_form(authorized(), None).body.decode()
    assert upload_tokens.UPLOADER_DOWNLOAD_URL == DOWNLOAD_URL
    assert DOWNLOAD_URL in page and "data-app-key-copy" in page
    assert "data-app-key-status" in page and "upload_app_key.js" in page
    assert "14 days" in page and "shown once" in page
    assert page.index("data-app-key-copy") < page.index('action="/replays/upload"')
    locked = routes.upload_form(request(), None).body.decode()
    assert DOWNLOAD_URL not in locked and "data-app-key-copy" not in locked


def test_owner_cli_issues_lists_and_revokes(factory, capsys):
    from scripts import upload_tokens as cli

    assert cli.main(["issue", "synthetic laptop"], factory) == 0
    issued = capsys.readouterr().out
    keys = re.findall(r"vwf1_[A-Za-z0-9_-]{43}", issued)
    assert len(keys) == 1
    key = keys[0]
    with factory() as db:
        token = upload_tokens.verify(db, key)
        token_id, digest = token.id, token.token_hash
        assert token.label == "synthetic laptop"
    assert cli.main(["list"], factory) == 0
    listing = capsys.readouterr().out
    assert "synthetic laptop" in listing and str(token_id) in listing
    assert all(word in listing.lower() for word in ("created", "last used", "revoked"))
    assert key not in listing and digest not in listing
    assert re.search(r"\b[0-9a-fA-F]{64}\b", listing) is None
    assert cli.main(["revoke", str(token_id)], factory) == 0
    assert key not in capsys.readouterr().out
    with factory() as db:
        assert upload_tokens.verify(db, key) is None
    assert cli.main(["list"], factory) == 0
    assert key not in capsys.readouterr().out
