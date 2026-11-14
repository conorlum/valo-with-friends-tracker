"""Compressed values builds, with a single decoded build cached per process."""

from datetime import datetime, timezone
import json
import threading
import zlib

from sqlalchemy.orm import Session

from app.models.replay import UtilModel
from app.services import util_values


_lock = threading.Lock()
_cache_identity: tuple[str, str, int, datetime] | None = None
_cache_value: tuple[str, dict] | None = None


def encode_values(values: dict) -> bytes:
    return zlib.compress(json.dumps(values, separators=(",", ":"), allow_nan=False).encode(), 6)


def decode_values(blob: bytes) -> dict:
    return json.loads(zlib.decompress(blob))


def clear_cache() -> None:
    global _cache_identity, _cache_value
    with _lock:
        _cache_identity = None
        _cache_value = None


def store_values(db: Session, key: str, values: dict, *, version: int | None = None) -> None:
    """Stage a blob row; the caller owns the transaction and rebuild lock."""
    db.merge(UtilModel(
        name="values", version=version or util_values.VERSION, key=key, data=None,
        blob=encode_values(values), computed_at=datetime.now(timezone.utc)))
    clear_cache()


def read_values(db: Session) -> tuple[str, dict] | None:
    global _cache_identity, _cache_value
    with _lock:
        metadata = db.query(UtilModel.key, UtilModel.version, UtilModel.computed_at).filter(
            UtilModel.name == "values").one_or_none()
        if metadata is None or metadata.version != util_values.VERSION:
            return None
        url = str(db.get_bind().url)
        identity = (url, metadata.key, metadata.version, metadata.computed_at)
        if identity == _cache_identity:
            return _cache_value

        # A writer may have replaced the row since the metadata read. Select and validate
        # this payload's own metadata so the build and its cache identity always agree.
        row = db.query(UtilModel.key, UtilModel.version, UtilModel.computed_at,
                       UtilModel.blob, UtilModel.data).filter(UtilModel.name == "values").one_or_none()
        if row is None or row.version != util_values.VERSION:
            return None
        values = decode_values(row.blob) if row.blob is not None else row.data
        if not values:
            return None
        _cache_identity = (url, row.key, row.version, row.computed_at)
        _cache_value = (row.key, values)
        return _cache_value
