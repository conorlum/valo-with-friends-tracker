import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.player import Player
from app.models.upload_token import UploadToken
from app.services import replay_upload, upload_tokens

T0 = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'tokens.db'}")
    Base.metadata.create_all(eng, tables=[Player.__table__, UploadToken.__table__])
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with sessionmaker(bind=engine)() as session:
        yield session


def _stored(db, token_id):
    db.expire_all()
    value = db.execute(select(UploadToken.last_used_at).where(UploadToken.id == token_id)).scalar_one()
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def test_mint_returns_key_once_and_stores_only_hash(db):
    token, key = upload_tokens.mint(db, "laptop")
    assert upload_tokens.looks_like_key(key)
    assert key.startswith(upload_tokens.PREFIX)
    row = db.execute(select(UploadToken)).scalar_one()
    assert row.token_hash == hashlib.sha256(key.encode()).hexdigest()
    for column in UploadToken.__table__.columns:
        assert key not in str(getattr(row, column.name))


def test_verify(db):
    token, key = upload_tokens.mint(db, "laptop")
    assert upload_tokens.verify(db, key).id == token.id
    changed = key[:-1] + ("A" if key[-1] != "A" else "B")
    assert upload_tokens.verify(db, changed) is None
    assert upload_tokens.verify(db, "") is None
    assert upload_tokens.verify(db, "abc") is None
    assert upload_tokens.revoke(db, token.id, T0) is True
    assert upload_tokens.verify(db, key) is None


def test_touch_throttles_to_one_write_a_minute(db):
    token, _ = upload_tokens.mint(db, "laptop")
    upload_tokens.touch(db, token, T0)
    assert _stored(db, token.id) == T0
    upload_tokens.touch(db, token, T0 + timedelta(seconds=10))
    assert _stored(db, token.id) == T0
    upload_tokens.touch(db, token, T0 + timedelta(seconds=61))
    assert _stored(db, token.id) == T0 + timedelta(seconds=61)


def test_touch_with_stale_object_from_another_session(engine):
    Session = sessionmaker(bind=engine)
    with Session() as one, Session() as two:
        token, _ = upload_tokens.mint(one, "laptop")
        stale = two.get(UploadToken, token.id)
        mine = one.get(UploadToken, token.id)
        upload_tokens.touch(one, mine, T0)
        upload_tokens.touch(two, stale, T0 + timedelta(seconds=10))
        assert _stored(one, token.id) == T0


def test_list_tokens_never_shows_hash(db):
    first, _ = upload_tokens.mint(db, "one")
    second, _ = upload_tokens.mint(db, "two")
    upload_tokens.revoke(db, second.id, T0)
    rows = upload_tokens.list_tokens(db)
    assert len(rows) == 2
    for row in rows:
        assert "token_hash" not in row
        assert set(row) == {"id", "label", "created_at", "last_used_at", "revoked_at"}
    by_id = {row["id"]: row for row in rows}
    assert by_id[first.id]["revoked_at"] is None
    assert by_id[second.id]["revoked_at"] is not None


def test_revoke_unknown_id(db):
    assert upload_tokens.revoke(db, 999, T0) is False


def test_session_key():
    class Token:
        id = 7

    key = upload_tokens.session_key(Token())
    assert key == "app:7"
    assert len(key) <= 64
    assert not key.startswith(replay_upload.AUTO_PREFIX)
