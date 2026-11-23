"""Upload keys for the replay uploader app.

A key is `vwf1_` plus 43 URL-safe characters. Only its SHA-256 is stored; the plain key
is returned once, at minting, and is never logged or stored.
"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.upload_token import UploadToken

PREFIX = "vwf1_"
UPLOADER_DOWNLOAD_URL = "https://github.com/conorlum/valo-with-friends-tracker/releases/latest/download/ValoUploader.exe"
TOUCH_INTERVAL = timedelta(seconds=60)
_KEY_RE = re.compile(r"^vwf1_[A-Za-z0-9_-]{43}$")


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def looks_like_key(text: str) -> bool:
    return isinstance(text, str) and _KEY_RE.fullmatch(text) is not None


def mint(db: Session, label: str, player_id: int | None = None) -> tuple[UploadToken, str]:
    key = PREFIX + secrets.token_urlsafe(32)
    token = UploadToken(token_hash=hash_key(key), label=label, player_id=player_id)
    db.add(token)
    db.commit()
    return token, key


def verify(db: Session, key: str) -> UploadToken | None:
    if not looks_like_key(key):
        return None
    token = db.execute(select(UploadToken).where(UploadToken.token_hash == hash_key(key))).scalar_one_or_none()
    if token is None or token.revoked_at is not None:
        return None
    return token


def touch(db: Session, token: UploadToken, now: datetime) -> None:
    """Set last_used_at when null or at least 60 s old, in one atomic UPDATE."""
    db.execute(
        update(UploadToken)
        .where(
            UploadToken.id == token.id,
            (UploadToken.last_used_at.is_(None)) | (UploadToken.last_used_at <= now - TOUCH_INTERVAL),
        )
        .values(last_used_at=now),
        execution_options={"synchronize_session": False},
    )
    db.commit()


def revoke(db: Session, token_id: int, now: datetime) -> bool:
    token = db.get(UploadToken, token_id)
    if token is None:
        return False
    if token.revoked_at is None:
        token.revoked_at = now
        db.commit()
    return True


def list_tokens(db: Session) -> list[dict]:
    rows = db.execute(select(UploadToken).order_by(UploadToken.id)).scalars().all()
    return [
        {
            "id": r.id,
            "label": r.label,
            "created_at": r.created_at,
            "last_used_at": r.last_used_at,
            "revoked_at": r.revoked_at,
        }
        for r in rows
    ]


def session_key(token: UploadToken) -> str:
    return f"app:{token.id}"
