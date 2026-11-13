"""Compressed values storage and metadata-only cache reads, using synthetic builds."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import zlib

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Query, Session

from app.db import Base
from app.models.replay import UtilModel
from app.services import util_store, util_values


V = {"rows": [{"a": 1.5, "b": None, "c": "x"}], "fit": {"w": [0.1]}}
V2 = {"rows": [{"a": 2.5}], "fit": {"w": [0.2]}}
V3 = {"rows": [{"a": 3.5}], "fit": {"w": [0.3]}}


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@contextmanager
def statements(db):
    captured = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_round_trip_compact_json():
    blob = util_store.encode_values(V)
    assert isinstance(blob, bytes)
    raw = zlib.decompress(blob)
    assert raw == json.dumps(V, separators=(",", ":"), allow_nan=False).encode()
    assert b", " not in raw and b": " not in raw
    assert util_store.decode_values(blob) == V


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_values_rejected(value):
    with pytest.raises(ValueError):
        util_store.encode_values({"x": value})


def test_realistic_build_shape_round_trip():
    values = {
        "ability_prior": {}, "estimator": {}, "fingerprints": {}, "fit": {"w": [0.1]},
        "gate": {}, "matches": [], "overlap": {}, "prior_sums": {}, "rows": V["rows"],
        "same_type_overlap": {}, "spread": {}, "spread_by_mode": {},
    }
    assert util_store.decode_values(util_store.encode_values(values)) == values


def test_store_then_read(db):
    util_store.store_values(db, "k1", V)
    db.commit()
    assert util_store.read_values(db) == ("k1", V)
    row = db.get(UtilModel, "values")
    assert row.data is None
    assert row.blob is not None


def test_store_leaves_transaction_to_caller(db):
    util_store.store_values(db, "k1", V)
    db.flush()
    db.rollback()
    assert util_store.read_values(db) is None


def test_store_refreshes_timestamp_with_same_key(db):
    before = datetime(2000, 1, 1)
    db.add(UtilModel(name="values", version=util_values.VERSION, key="k1", data=V,
                     computed_at=before))
    db.commit()
    assert util_store.read_values(db) == ("k1", V)
    util_store.store_values(db, "k1", V2)
    db.commit()
    assert db.get(UtilModel, "values").computed_at > before
    assert util_store.read_values(db) == ("k1", V2)


def test_legacy_row(db):
    db.add(UtilModel(name="values", version=util_values.VERSION, key="old", data=V))
    db.commit()
    assert util_store.read_values(db) == ("old", V)


def test_blob_preferred_over_legacy_data(db):
    db.add(UtilModel(name="values", version=util_values.VERSION, key="both", data=V,
                     blob=util_store.encode_values(V2)))
    db.commit()
    assert util_store.read_values(db) == ("both", V2)


def test_wrong_version(db):
    util_store.store_values(db, "wrong", V, version=util_values.VERSION + 1)
    db.commit()
    assert util_store.read_values(db) is None


def test_no_row(db):
    assert util_store.read_values(db) is None


@pytest.mark.parametrize("legacy", [False, True])
def test_empty_build(db, legacy):
    if legacy:
        db.add(UtilModel(name="values", version=util_values.VERSION, key="empty", data={}))
    else:
        util_store.store_values(db, "empty", {})
    db.commit()
    assert util_store.read_values(db) is None


def test_cache_hit_selects_only_metadata(db):
    util_store.store_values(db, "k1", V)
    db.commit()
    first = util_store.read_values(db)
    with statements(db) as sql:
        second = util_store.read_values(db)
    assert len(sql) == 1
    projection = sql[0].split("FROM", 1)[0]
    assert "util_model.blob" not in projection
    assert "util_model.data" not in projection
    assert second[1] is first[1]


def test_cache_miss_on_change(db):
    util_store.store_values(db, "k1", V)
    db.commit()
    assert util_store.read_values(db) == ("k1", V)
    util_store.store_values(db, "k2", V2)
    db.commit()
    assert util_store.read_values(db) == ("k2", V2)
    db.query(UtilModel).filter_by(name="values").update({"version": 0})
    assert util_store.read_values(db) is None


def test_same_key_external_change(db):
    util_store.store_values(db, "k1", V)
    db.commit()
    assert util_store.read_values(db) == ("k1", V)
    later = db.get(UtilModel, "values").computed_at + timedelta(seconds=1)
    db.execute(text("UPDATE util_model SET blob = :b, computed_at = :later WHERE name = 'values'"),
               {"b": util_store.encode_values(V3), "later": later.isoformat(sep=" ")})
    db.commit()
    assert util_store.read_values(db) == ("k1", V3)


@pytest.mark.parametrize("replacement", ["blob", "wrong_version", "deleted", "empty"])
def test_replacement_between_metadata_and_payload(db, monkeypatch, replacement):
    db.add(UtilModel(name="values", version=util_values.VERSION, key="old", data=V))
    db.commit()
    original = Query.one_or_none
    replaced = False
    later = datetime(2030, 1, 1, tzinfo=timezone.utc)

    def replace_after_metadata(query):
        nonlocal replaced
        row = original(query)
        if not replaced and len(query.column_descriptions) == 3:
            replaced = True
            db.execute(UtilModel.__table__.delete())
            if replacement != "deleted":
                db.execute(UtilModel.__table__.insert().values(
                    name="values", version=0 if replacement == "wrong_version" else util_values.VERSION,
                    key="new", computed_at=later, data=None,
                    blob=util_store.encode_values({} if replacement == "empty" else V3)))
        return row

    monkeypatch.setattr(Query, "one_or_none", replace_after_metadata)
    first = util_store.read_values(db)
    assert replaced
    if replacement != "blob":
        assert first is None
        return
    assert first == ("new", V3)
    with statements(db) as sql:
        second = util_store.read_values(db)
    assert len(sql) == 1
    assert second[1] is first[1]


def test_clear_cache_forces_payload_read(db):
    util_store.store_values(db, "k1", V)
    db.commit()
    first = util_store.read_values(db)
    util_store.clear_cache()
    with statements(db) as sql:
        second = util_store.read_values(db)
    assert len(sql) == 2
    assert second == first
    assert second[1] is not first[1]


def test_cache_identity_includes_database_url(db):
    timestamp = datetime(2030, 1, 1)
    db.add(UtilModel(name="values", version=util_values.VERSION, key="k1", data=V,
                     computed_at=timestamp))
    db.commit()
    assert util_store.read_values(db) == ("k1", V)
    engine = create_engine("sqlite+pysqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as other:
            other.add(UtilModel(name="values", version=util_values.VERSION, key="k1", data=V2,
                                computed_at=timestamp))
            other.commit()
            assert util_store.read_values(other) == ("k1", V2)
    finally:
        engine.dispose()
