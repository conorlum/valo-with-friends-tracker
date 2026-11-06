"""Stored replay errors: SQLite and synthetic identities only, no worker or network."""

import importlib
import inspect
import io
import json
import re
import sys
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
from sqlalchemy import event

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_control_store import db, factory, linked, put_row  # noqa: E402,F401
from test_gaps_task import _gap_run  # noqa: E402
from test_replay_store import condensed  # noqa: E402,F401

from app.config import settings
from app.models import Player
from app.models.replay import (ControlHeight, Replay, ReplayPlayer, ReplayRound, ReplayRoundControl, ReplayRoundGapRun,
                               ReplayRoundMoments, ReplayRoundUtil, ReplayUpload)
from app.models.site_events import SiteEvent
from app.services import control_heights as ch, replay_progress as progress
from app.services.replay_progress import uploads, cf, replay_control, replay_gaps, STEPS, _LABELS
from app.services.replay_util import Freshness

STAMP = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
KEYS = ["page_error", "box_conflict", "control_failed", "gaps_failed", "moments_failed", "util_failed",
        "heights_rejected", "heights_out_of_tries", "warm_vanished", "given_up", "held", "upload_failed", "upload_held",
        "store_failed", "ack_missing", "stuck", "unlinked", "needs_a_person", "feature_failed"]
FIELDS = {"map", "match", "round", "t", "agent", "figures", "link", "error", "revision", "reproduce"}
RECORD = {"match": "0a9ab548-718b-41ee-94ce-db25fd430bf8", "round": 1, "slot": 5,
          "t0": 1.648, "t1": 5.528, "z": 2.0, "px": [774, 414],
          "candidates": [[15, [2.4], 0.58], [16, []]]}


@pytest.fixture
def errors(db, monkeypatch):
    ReplayUpload.__table__.create(db.get_bind(), checkfirst=True)
    module = importlib.import_module("app.services.replay_errors")
    monkeypatch.setattr(module, "now", lambda: STAMP)
    monkeypatch.setattr(module, "_cached", None)
    monkeypatch.setattr(module, "_verification_results", {}, raising=False)
    monkeypatch.setattr(module, "_verification_started", False, raising=False)
    monkeypatch.setattr(module, "_verification_running", False, raising=False)
    monkeypatch.setattr(module, "_verification_pending", False, raising=False)
    monkeypatch.setattr(module, "_verification_progress", (0, 0), raising=False)
    monkeypatch.setattr(module, "_digests", {}, raising=False)
    monkeypatch.setattr(module, "_real_start_verification", getattr(module, "_start_verification", None), raising=False)
    # Ordinary inventory tests never start a real verifier/child.
    monkeypatch.setattr(module, "_start_verification", lambda: None, raising=False)
    ch._BOX_CONFLICTS.clear()
    yield module
    ch._BOX_CONFLICTS.clear()


def kind(errors, db, key):
    body = errors.compute(db)
    assert [k["key"] for k in body["kinds"]] == KEYS
    assert datetime.fromisoformat(body["checked_at"]).tzinfo is not None
    return next(k for k in body["kinds"] if k["key"] == key)


def one(errors, db, key):
    found = kind(errors, db, key)
    assert found["count"] == 1
    [row] = found["rows"]
    assert set(row) == FIELDS
    assert row["reproduce"] and isinstance(row["figures"], dict)
    assert row["link"].startswith("https://valowithfriendstracker.onrender.com/replays/")
    return row


@pytest.fixture
def event_kind(errors, monkeypatch):
    # test_event stands in for page_error, so these tests see one synthetic event kind.
    monkeypatch.setattr(errors, "KINDS", tuple(k for k in errors.KINDS if k[0] != "page_error")
                        + (("test_event", "Test events"),))
    monkeypatch.setattr(errors, "GROUPS", {**{k: v for k, v in errors.GROUPS.items() if k != "page_error"},
                                           "test_event": "Pages"})
    monkeypatch.setattr(errors, "EVENT_KINDS", frozenset({"test_event"}))
    return errors


@pytest.fixture
def stored_events(event_kind, db):
    db.add_all([
        SiteEvent(kind="test_event", key="GET /players/{player_id}",
                  error="Traceback (most recent call last):\n  ...\nValueError: synthetic",
                  context={"path": "/players/12", "status": 500}, count=3,
                  first_seen=STAMP - timedelta(hours=5), last_seen=STAMP - timedelta(minutes=2)),
        SiteEvent(kind="test_event", key="older", error="synthetic older", context=None, count=1,
                  first_seen=STAMP - timedelta(days=1), last_seen=STAMP - timedelta(hours=1)),
        SiteEvent(kind="unknown", key="ignored", error="unknown synthetic", context={}, count=1,
                  first_seen=STAMP, last_seen=STAMP),
    ])
    db.commit()
    return event_kind


def test_kind_groups_are_complete(errors):
    assert errors.GROUP_ORDER == ("Pages", "Background work", "Data", "Replays")
    assert set(dict(errors.KINDS)) <= errors.GROUPS.keys()
    assert set(errors.GROUPS.values()) <= set(errors.GROUP_ORDER)
    assert errors.EVENT_KINDS <= set(dict(errors.KINDS))


def test_page_error_events_are_listed_under_pages(errors, db):
    db.add(SiteEvent(kind="page_error", key="GET /players/{display_name}", error="ValueError: synthetic",
                     context={"type": "ValueError", "traceback": "Traceback ...\nValueError: synthetic",
                              "path": "/players/x", "logged_in": False}, count=2,
                     first_seen=STAMP - timedelta(hours=2), last_seen=STAMP - timedelta(minutes=1)))
    db.commit()
    body = errors.compute(db)
    found = next(k for k in body["kinds"] if k["key"] == "page_error")
    assert found["label"] == "Pages that crashed" and found["count"] == 1
    assert errors.GROUPS["page_error"] == "Pages" and "page_error" in errors.EVENT_KINDS
    assert (found["group"], found["shape"]) == ("Pages", "event")
    [row] = found["rows"]
    assert (row["key"], row["count"], row["error"]) == ("GET /players/{display_name}", 2, "ValueError: synthetic")
    assert row["context"]["type"] == "ValueError"


def test_event_rows_are_grouped_sorted_and_read_once(stored_events, db):
    errors = stored_events
    queries = []
    def record(conn, cursor, statement, parameters, context, many):
        if "site_events" in statement.lower():
            queries.append(statement.lower())
    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        body = errors.compute(db)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)
    entry = body["kinds"][0]
    assert (entry["key"], entry["group"], entry["shape"], entry["count"]) == (
        "test_event", "Pages", "event", 2)
    assert [row["key"] for row in entry["rows"]] == ["GET /players/{player_id}", "older"]
    newest, older = entry["rows"]
    assert set(newest) == {"kind", "key", "count", "first_seen", "last_seen", "error", "context",
                           "link", "reproduce"}
    assert newest["count"] == 3
    assert newest["first_seen"] == (STAMP - timedelta(hours=5)).isoformat()
    assert newest["last_seen"] == (STAMP - timedelta(minutes=2)).isoformat()
    assert newest["context"] == {"path": "/players/12", "status": 500}
    assert newest["link"] == f"{errors.SITE}/replays/upload"
    assert newest["reproduce"] == errors._inventory_command()
    assert older["context"] == {}
    assert [entry["key"] for entry in body["kinds"][1:]] == KEYS[1:]
    assert all(entry["shape"] == "replay" and entry["group"] == "Replays" for entry in body["kinds"][1:])
    assert "unknown" not in json.dumps(body) and "ignored" not in json.dumps(body)
    assert len(queries) == 1 and queries[0].startswith("select ")
    selected = queries[0].replace('"', "")      # SQLite quotes the reserved word key
    assert all(f"site_events.{column}" in selected for column in (
        "kind", "key", "count", "first_seen", "last_seen", "error", "context"))


def test_event_row_copies_nested_context_and_accepts_overrides(errors):
    context = {"nested": {"items": ["synthetic"]}}
    row = errors._event_row(kind="test_event", key="synthetic", count=1, first_seen=STAMP,
                            last_seen=STAMP, error="synthetic", context=context,
                            link="/synthetic", reproduce="synthetic command")
    context["nested"]["items"].append("changed")
    assert row["context"] == {"nested": {"items": ["synthetic"]}}
    assert row["link"] == "/synthetic" and row["reproduce"] == "synthetic command"


def test_event_prompt_and_remaining_query_share_budget(stored_events, db):
    errors = stored_events
    db.add_all([SiteEvent(kind="test_event", key=f"rest-{i:03}", count=1, error="synthetic",
                          context={}, first_seen=STAMP, last_seen=STAMP - timedelta(days=2))
                for i in reversed(range(199))])
    db.commit()
    body = errors.compute(db, limit=None)
    text = errors.prompt(body)
    assert text.startswith("Investigate site health problems that need a human.\n")
    for line in ("kind: test_event", "key: GET /players/{player_id}", "count: 3",
                 f"first_seen: {(STAMP - timedelta(hours=5)).isoformat()}",
                 f"last_seen: {(STAMP - timedelta(minutes=2)).isoformat()}",
                 'context: {"path": "/players/12", "status": 500}',
                 "error: Traceback (most recent call last):\n  ...\nValueError: synthetic",
                 f"link: {errors.SITE}/replays/upload", f"reproduce: {errors._inventory_command()}"):
        assert line in text
    assert text.count("\nkind: test_event") == 200
    assert "1 more. List the rest read-only:" in text
    assert ("SELECT kind, key, count, first_seen, last_seen, error, context FROM site_events "
            "WHERE kind = 'test_event' AND key IN ('rest-198') ORDER BY last_seen DESC;") in text
    assert "\nmap:" not in text and "\nrevision:" not in text
    assert errors.prompt(body, "test_event") == text


def test_event_rest_query_quotes_values(event_kind):
    assert event_kind._rest_query("test_event", [{"key": "synthetic's key"}]) == (
        "SELECT kind, key, count, first_seen, last_seen, error, context FROM site_events "
        "WHERE kind = 'test_event' AND key IN ('synthetic''s key') ORDER BY last_seen DESC;")


def test_demo_event_kind_never_reads_db_even_with_warm_cache(stored_events, db, monkeypatch):
    errors = stored_events
    errors.cached(db, now=100)
    monkeypatch.setattr(settings, "demo_mode", True)
    class NoDB:
        def execute(self, *args):
            pytest.fail("demo execute")
        def query(self, *args):
            pytest.fail("demo query")
    for body in (errors.compute(NoDB()), errors.cached(NoDB(), now=101)):
        assert body["kinds"][0]["key"] == "test_event"
        assert len(body["kinds"]) == len(errors.KINDS)
        assert all(entry["count"] == 0 and entry["rows"] == [] for entry in body["kinds"])


def test_missing_event_table_keeps_replay_evidence(event_kind, db, monkeypatch, caplog):
    upload(db, status="failed", error="synthetic replay failure")
    SiteEvent.__table__.drop(db.get_bind())
    rollbacks = []
    original = db.rollback
    def rollback():
        rollbacks.append(True)
        original()
    monkeypatch.setattr(db, "rollback", rollback)
    body = event_kind.compute(db)
    assert body["kinds"][0]["count"] == 0
    entry = next(entry for entry in body["kinds"] if entry["key"] == "upload_failed")
    assert entry["count"] == 1 and entry["rows"][0]["error"] == "synthetic replay failure"
    assert rollbacks == [True]
    assert any(record.levelname == "WARNING" and "site_events" in record.message for record in caplog.records)


def upload(db, i=1, **kwargs):
    row = ReplayUpload(id=str(UUID(int=i)), status="failed", created_at=STAMP - timedelta(hours=2),
                       error="Synthetic upload error", size_bytes=123, worker_job_id="synthetic-job")
    for key, value in kwargs.items():
        setattr(row, key, value)
    db.add(row)
    db.commit()
    return row


def asset(records):
    out = io.BytesIO()
    np.savez(out, meta=np.frombuffer(json.dumps({"box_conflicts": records}).encode(), np.uint8))
    return out.getvalue()


def height(db, map_name="Ascent", **kwargs):
    row = ControlHeight(map_name=map_name, digest="synthetic", asset=asset([]),
                        report={"ready": False, "not_ready": ["synthetic reason"],
                                "supported_cells": 0, "walkable_cells": 1,
                                "kill_lines": {"passes": True}, "must_block": {"passes": True}},
                        match_uuids=[], rules={"revision": 9}, inputs={}, inputs_sha="synthetic",
                        status="rejected", built_at=STAMP)
    for key, value in kwargs.items():
        setattr(row, key, value)
    db.add(row)
    db.commit()
    return row


def test_real_shaped_box_conflict_and_shared_copy_cache(errors, db, linked, monkeypatch):
    linked.match_uuid = RECORD["match"]
    db.commit()
    height(db, linked.map_name, asset=asset([RECORD]), status="active")
    wanted_agent = db.get(ReplayPlayer, (linked.id, 5)).agent
    row = one(errors, db, "box_conflict")
    assert row["match"] == RECORD["match"] and row["agent"] == wanted_agent
    assert row["round"] == 1 and row["t"] == 1.648
    assert row["figures"] == {"z": 2.0, "px": [774, 414], "slot": 5, "t1": 5.528,
                              "candidates": [{"piece": 15, "tops": [2.4], "dist_m": 0.58},
                                             {"piece": 16, "tops": [], "dist_m": None}]}
    assert row["link"].endswith(f'{RECORD["match"]}?round=1&t=1.648')
    assert row["revision"] == {"heights_revision": 9, "built_at": STAMP.isoformat()}
    assert row["reproduce"].endswith(
        f'control_heights.py export --map {linked.map_name} --out "$TEMP/{linked.map_name}_heights.npz"')
    assert [(map_name, record) for map_name, _, _, record in ch.all_box_conflicts(db)] == [(linked.map_name, RECORD)]
    monkeypatch.setattr(ch, "asset_bytes", lambda *args: pytest.fail("cached asset reread"))
    all_rows = ch.all_box_conflicts(db)
    all_rows[0][1]["revision"] = 99
    all_rows[0][3]["z"] = 99
    assert ch.all_box_conflicts(db)[0][1]["revision"] == 9
    assert ch.all_box_conflicts(db)[0][3] == RECORD


@pytest.mark.parametrize("key,model", [("control_failed", ReplayRoundControl), ("gaps_failed", ReplayRoundGapRun)])
def test_current_and_stale_round_failures(errors, factory, db, linked, key, model):
    if key == "control_failed":
        put_row(db, linked, 1, status="failed")
        put_row(db, linked, 2, status="failed", fingerprint="old")
    else:
        put_row(db, linked, 1)
        put_row(db, linked, 2)
        _gap_run(factory, linked, 1, status="failed")
        _gap_run(factory, linked, 2, status="failed", fingerprint="old")
    db.expire_all()
    stored = db.get(model, (linked.id, 1))
    row = one(errors, db, key)
    assert row["round"] == 1 and row["error"] == stored.error
    revision = "control_revision" if key == "control_failed" else "gaps_revision"
    assert row["revision"][revision] == getattr(stored, revision)
    assert row["revision"]["computed_at"]
    assert kind(errors, db, key)["note"] == "1 failed rows from older rules: the worker will retry them"


@pytest.mark.parametrize("key", ["control_failed", "gaps_failed"])
def test_failures_behind_an_earlier_step_are_counted_not_listed(errors, factory, db, linked, key):
    # Probe W9a: 21 live failed control rows sat behind a re-parse, neither listed nor counted.
    put_row(db, linked, 1, status="failed" if key == "control_failed" else "ok")
    if key == "gaps_failed":
        _gap_run(factory, linked, 1, status="failed")
    linked.recipe, linked.source = "000000000000.c9.f1.000000000", "upload"
    db.commit()
    found = kind(errors, db, key)
    assert found["count"] == 0
    assert found["note"] == "1 failed rows from older rules: the worker will retry them"


@pytest.mark.parametrize("key,model", [("moments_failed", ReplayRoundMoments), ("util_failed", ReplayRoundUtil)])
def test_moments_and_util_use_util_freshness(errors, db, linked, key, model):
    from app.services import replay_gaps
    fresh = Freshness(db)
    for n, fingerprint, revision in [(1, fresh.expected(linked, 1), replay_gaps.GAPS_REVISION),
                                     (2, "old", replay_gaps.GAPS_REVISION),
                                     (3, fresh.expected(linked, 3), replay_gaps.GAPS_REVISION - 1)]:
        db.add(model(replay_id=linked.id, round_number=n, status="failed", fingerprint=fingerprint,
                     gaps_revision=revision, error="Synthetic computation error", computed_at=STAMP))
    db.commit()
    row = one(errors, db, key)
    assert row["round"] == 1 and row["error"] == "Synthetic computation error"
    assert row["revision"] == {"gaps_revision": replay_gaps.GAPS_REVISION, "computed_at": STAMP.isoformat()}
    assert kind(errors, db, key)["note"] == "2 failed rows from older rules: the worker will retry them"


def test_only_newest_height_rejection_counts(errors, db):
    height(db)
    row = one(errors, db, "heights_rejected")
    assert row["match"] is None and row["round"] is None and row["t"] is None
    assert row["figures"] == {"reasons": ["synthetic reason"], "digest": "synthetic", "inputs_sha": "synthetic"}
    height(db, status="superseded")  # same time: id breaks the tie
    assert kind(errors, db, "heights_rejected")["count"] == 0


@pytest.mark.parametrize("key,kwargs", [
    ("upload_failed", {}), ("upload_held", {"status": "held"}),
    ("store_failed", {"status": "stored", "store_outcome": "failed", "archive_ack": "ok"}),
    ("ack_missing", {"status": "stored"}), ("stuck", {"status": "queued"}),
    ("stuck", {"status": "parsing"})])
def test_upload_kinds(errors, db, linked, key, kwargs):
    stored = upload(db, replay_id=linked.id, **kwargs)
    row = one(errors, db, key)
    assert row["match"] == str(linked.match_uuid)
    assert row["map"] == linked.map_name
    assert row["error"] == stored.error
    assert row["figures"]["upload_id"] == stored.id
    assert row["figures"]["size_bytes"] == 123 and row["figures"]["worker_job_id"] == "synthetic-job"


def test_later_success_supersedes_only_failures_with_sha(errors, db):
    upload(db, 1, source_sha256="a" * 64)
    no_sha = upload(db, 2)
    upload(db, 3, status="stored", source_sha256="a" * 64, created_at=STAMP, archive_ack="ok")
    assert one(errors, db, "upload_failed")["figures"]["upload_id"] == no_sha.id
    upload(db, 4, source_sha256="a" * 64, created_at=STAMP + timedelta(seconds=1))
    assert kind(errors, db, "upload_failed")["count"] == 2


def test_upload_time_boundaries_and_finished_time(errors, db):
    cutoff = STAMP - timedelta(hours=1)
    upload(db, 1, status="queued", created_at=cutoff.replace(tzinfo=None))
    upload(db, 2, status="stored", finished_at=cutoff)
    upload(db, 3, status="stored", finished_at=STAMP)
    upload(db, 4, status="stored", archive_ack="ok")
    assert kind(errors, db, "stuck")["count"] == 0
    assert kind(errors, db, "ack_missing")["count"] == 0
    upload(db, 5, status="stored", finished_at=cutoff - timedelta(seconds=1))
    assert one(errors, db, "ack_missing")["figures"]["upload_id"] == str(UUID(int=5))


@pytest.mark.parametrize("status", ["unlinked", "refused"])
def test_unlinked(errors, db, linked, status):
    linked.link_status, linked.link_report = status, {"reason": "synthetic mismatch", "count": 2}
    db.commit()
    row = one(errors, db, "unlinked")
    assert row["figures"] == linked.link_report and row["round"] is None


def test_needs_a_person_groups_rounds_per_replay_step(errors, db, linked, monkeypatch):
    states = {(linked.id, n): step for n, step in enumerate(
        ["reparse_local", "reparse_local", "no_map", "control_failed", "gaps_failed", "reparse"], 1)}
    monkeypatch.setattr(progress, "round_states", lambda db: {"states": states, "replays": {linked.id: linked}})
    found = kind(errors, db, "needs_a_person")
    assert found["count"] == 2
    assert sorted(r["figures"]["rounds"] for r in found["rows"]) == [1, 2]
    assert {r["figures"]["step"] for r in found["rows"]} == {dict((k, label) for k, label, _ in progress.STEPS)[s]
                                                                        for s in ("reparse_local", "no_map")}
    assert all(r["round"] is None for r in found["rows"])


def test_all_rows_kept_sorted_then_first_fifty_shown(errors, db, monkeypatch):
    records = [dict(RECORD, match=str(UUID(int=i % 3 + 1)), round=i % 7 + 1, t0=i / 10)
               for i in reversed(range(53))]
    monkeypatch.setattr(ch, "all_box_conflicts", lambda db: [
        ("B" if i % 2 else "A", {}, STAMP, r) for i, r in enumerate(records)])
    all_rows = errors.collect(db)["box_conflict"]
    expected = sorted(all_rows, key=lambda r: (r["map"], r["match"], r["round"], r["t"]))
    assert all_rows == expected and len(all_rows) == 53
    found = kind(errors, db, "box_conflict")
    assert found["count"] == 53 and found["rows"] == expected[:50]
    full = errors.compute(db, limit=None)
    assert len(next(k for k in full["kinds"] if k["key"] == "box_conflict")["rows"]) == 53
    assert len(next(k for k in errors.cached(db)["kinds"] if k["key"] == "box_conflict")["rows"]) == 53


def test_demo_never_reads_db_even_with_warm_cache(errors, db, monkeypatch):
    errors.cached(db, now=100)
    monkeypatch.setattr(settings, "demo_mode", True)
    class NoDB:
        def query(self, *args):
            pytest.fail("demo query")
        def execute(self, *args):
            pytest.fail("demo execute")
    for body in (errors.compute(NoDB()), errors.cached(NoDB(), now=101)):
        assert [k["key"] for k in body["kinds"]] == KEYS
        assert all(k["count"] == 0 and k["rows"] == [] for k in body["kinds"])


def test_no_identity_or_private_upload_columns(errors, db, linked):
    db.query(Player).update({Player.display_name: "synthetic-private-name"})
    subject = "00000000-0000-0000-0000-000000000099"
    db.get(ReplayPlayer, (linked.id, 5)).subject = subject
    db.commit()
    upload(db)
    queries = []
    def record(conn, cursor, statement, parameters, context, many):
        queries.append(statement.lower())
    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        body = json.dumps(errors.compute(db))
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)
    assert "synthetic-private-name" not in body and subject not in body
    source = inspect.getsource(errors)
    for forbidden in ("display_name", "session_key", "client_ip"):
        assert forbidden not in source
        assert all(forbidden not in q for q in queries)


def test_cached_shares_result_for_ttl(errors, db, monkeypatch):
    calls = []
    monkeypatch.setattr(errors, "compute", lambda db, **kw: calls.append(kw) or {"n": len(calls)})
    first = errors.cached(db, now=100)
    assert errors.cached(db, now=100 + errors.TTL_S - 1) is first
    assert errors.cached(db, now=100 + errors.TTL_S) == {"n": 2}
    assert calls[0]["limit"] is None


def test_all_box_conflicts_best_effort(errors, db, monkeypatch):
    height(db, status="active", asset=b"not an asset")
    assert ch.all_box_conflicts(db) == []
    def broken(db):
        raise RuntimeError("synthetic database error")
    monkeypatch.setattr(ch, "active_rows", broken)
    assert ch.all_box_conflicts(db) == []

def test_live_kinds_match_current_work_and_fingerprint(errors, db, linked, monkeypatch):
    from app.services.replay_control_remote import Live, MAX_TRIES
    states = {(linked.id, 1): "control_missing", (linked.id, 2): "gaps_stale",
              (linked.id, 3): "done", (linked.id, 4): "control_stale"}
    monkeypatch.setattr(progress, "round_states", lambda db: {
        "states": states, "replays": {linked.id: linked},
        "fingerprints": {key: "current" for key in states}})
    live = Live(at=10000, given_up=((linked.id, 1, "control", "current"),
                (linked.id, 2, "gaps", "current"), (linked.id, 3, "control", "current"),
                (linked.id, 4, "control", "old"), (linked.id, 4, "gaps", "current")),
                held=(linked.id, 99999), heights_out_of_tries=("Ascent",),
                in_flight=((linked.id, 2, 100, "gaps"), (linked.id, 1, 6400, "control")))
    found = {k["key"]: k for k in errors.compute(db, live)["kinds"]}
    assert found["given_up"]["count"] == 2
    assert found["given_up"]["note"] == "3 older give-ups no longer apply"
    assert found["given_up"]["rows"][0]["figures"] == {"kind": "control", "tries": MAX_TRIES}
    assert found["given_up"]["rows"][0]["revision"] == {"fingerprint": "current"}
    assert found["held"]["rows"][0]["figures"] == {"rounds": 4}
    assert found["held"]["count"] == 1
    assert found["heights_out_of_tries"]["rows"][0]["figures"] == {"map": "Ascent"}
    assert found["heights_out_of_tries"]["rows"][0]["link"].endswith("/upload")
    assert found["stuck"]["count"] == 1
    assert found["stuck"]["rows"][0]["figures"] == {"kind": "gaps", "minutes": 165}
    for key in ("held", "heights_out_of_tries", "warm_vanished", "given_up"):
        absent = kind(errors, db, key)
        assert absent["count"] == 0
        assert absent["note"] == "not known in this process (no dispatcher here)"


def test_a_warm_cache_file_deleted_before_use_is_listed_and_in_the_prompt(errors, db):
    # D3: the fallback bounds it; this row is the alarm that it happened.
    from app.services.replay_control_remote import Live
    live = Live(100, (), (), (), (), warm_vanished=(("Ascent:abc123def456:|stand", 2),))
    result = errors.compute(db, live)
    found = next(k for k in result["kinds"] if k["key"] == "warm_vanished")
    assert found["count"] == 1
    row = found["rows"][0]
    assert row["map"] == "Ascent"
    assert row["figures"] == {"identity": "Ascent:abc123def456:|stand", "times": 2}
    assert "left the replay worker's disk 2 time(s)" in row["error"]
    assert "--read-only" in row["reproduce"] and "--expect-database valowithfriendsdb" in row["reproduce"]
    text = errors.prompt(result, "warm_vanished")
    assert "Warm cache files deleted before use (1)" in text and "Ascent:abc123def456:|stand" in text


def test_cached_uses_dispatcher_snapshot(errors, db, monkeypatch):
    from app.services import replay_control_remote as remote
    live = remote.Live(100, (), (), ("Ascent",), ())
    monkeypatch.setattr(remote, "LIVE", live)
    body = errors.cached(db)
    assert next(k for k in body["kinds"] if k["key"] == "heights_out_of_tries")["count"] == 1


def test_every_kind_has_a_read_only_reproduction_command(errors, factory, db, linked, monkeypatch):
    linked.link_status = "unlinked"
    db.commit()
    height(db, linked.map_name, status="active", asset=asset([dict(RECORD, match=str(linked.match_uuid))]))
    height(db, linked.map_name, digest="rejected")
    put_row(db, linked, 1, status="failed")
    put_row(db, linked, 2)
    _gap_run(factory, linked, 2, status="failed")
    db.expire_all()
    fresh = Freshness(db)
    for model in (ReplayRoundMoments, ReplayRoundUtil):
        db.merge(model(replay_id=linked.id, round_number=3, status="failed",
                       fingerprint=fresh.expected(linked, 3), gaps_revision=replay_gaps.GAPS_REVISION,
                       computed_at=STAMP, error="Synthetic evidence error"))
    db.commit()
    for i, kwargs in enumerate(({}, {"status": "held"}, {"store_outcome": "failed"},
                               {"status": "stored"}, {"status": "queued"}), 1):
        upload(db, i, replay_id=linked.id, **kwargs)

    original = progress.round_states

    def with_manual_step(db):
        collected = original(db)
        collected["states"][linked.id, 4] = "incomplete"
        return collected

    monkeypatch.setattr(progress, "round_states", with_manual_step)
    collected = errors.collect(db)
    assert set(collected) == set(KEYS)
    assert all(rows for key, rows in collected.items()
               if key not in {"heights_out_of_tries", "warm_vanished", "given_up", "held", "feature_failed", "page_error"}), \
        "stored kinds"
    for key, rows in collected.items():
        for row in rows:
            command = row["reproduce"]
            assert "--read-only" in command, (key, command)
            assert "--expect-database valowithfriendsdb" in command, (key, command)
            assert "--force" not in command, (key, command)
            if key in ("control_failed", "gaps_failed", "moments_failed", "util_failed"):
                assert command.endswith("--retry-failed --dry-run"), command
            elif key in ("box_conflict", "heights_rejected"):
                assert command.endswith(f'--out "$TEMP/{row["map"]}_heights.npz"'), command


# Frozen copy of compute before extracting round_states: equivalence across mixed states.
def _before_split(db) -> dict:
    """Every stored round under one state: "done" or the key of its first stuck step."""
    recipe = uploads.site_recipe()
    replays = {r.id: r for r in db.query(Replay)}
    rounds = [(rid, n) for rid, n in db.query(ReplayRound.replay_id, ReplayRound.round_number)]

    every = {(p.replay_id, p.round_number): p for p in replay_control.plan(db, force=True)}
    control_todo = replay_control.plan(db, retry_failed=True)
    control = {(p.replay_id, p.round_number): p.reason for p in control_todo}
    gaps_todo = replay_gaps.plan_gaps(db, [p for p in control_todo if p.computable], list(every.values()),
                                      retry_failed=True)
    gaps = {(p.replay_id, p.round_number): p for p in gaps_todo}
    runs = {}
    if gaps:
        runs = {(r.replay_id, r.round_number): r for r in db.query(
            ReplayRoundGapRun.replay_id, ReplayRoundGapRun.round_number, ReplayRoundGapRun.status,
            ReplayRoundGapRun.fingerprint)}
    wanted: dict[tuple, str] = {}

    def gaps_state(key) -> str:
        p, run = gaps[key], runs.get(key)
        if run is None:
            return "gaps_missing"
        if (p.fingerprint, p.map_name) not in wanted:
            wanted[p.fingerprint, p.map_name] = replay_gaps.gap_fingerprint(p.fingerprint, p.map_name)
        if run.fingerprint == wanted[p.fingerprint, p.map_name]:
            return "gaps_failed" if run.status == "failed" else "gaps_stale"
        return "gaps_stale"

    def state(key) -> str:
        replay = replays[key[0]]
        if replay.recipe != recipe:
            return "reparse_local" if replay.source == "local" else "reparse"
        planned = every.get(key)
        if planned is None:
            return "incomplete"
        if not planned.computable:
            return planned.reason if planned.reason in _LABELS else "incomplete"
        reason = control.get(key)
        if reason is not None:
            return {"missing": "control_missing", "stale": "control_stale"}.get(reason, "control_failed")
        if key in gaps:
            return gaps_state(key)
        return "done"

    states = {key: state(key) for key in rounds}
    order = {key: i for i, (key, _, _) in enumerate(STEPS)}
    matches: dict[str, dict] = {}
    for (replay_id, _), found in states.items():
        row = matches.setdefault(str(replays[replay_id].match_uuid).lower(), {"done": 0, "total": 0, "first": None})
        row["total"] += 1
        if found == "done":
            row["done"] += 1
        elif row["first"] is None or order[found] < order[row["first"]]:
            row["first"] = found
    counts = Counter(states.values())
    total = len(rounds)
    done = counts.pop("done", 0)
    steps = [{"key": key, "label": label, "automatic": automatic, "rounds": counts[key]}
             for key, label, automatic in STEPS if counts.get(key)]
    return {
        "total_rounds": total,
        "total_replays": len(replays),
        "done": done,
        "needs_work": total - done,
        "worker_will_do": sum(s["rounds"] for s in steps if s["automatic"]),
        "needs_a_person": sum(s["rounds"] for s in steps if not s["automatic"]),
        "steps": steps,
        "matches": matches,
        "judged_against": {"recipe": recipe, "control_revision": cf.CONTROL_REVISION,
                           "gaps_revision": replay_gaps.GAPS_REVISION},
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def test_progress_split_preserves_mixed_fixture_output(errors, factory, db, linked):
    for n in range(1, linked.round_count + 1):
        put_row(db, linked, n)
    put_row(db, linked, 2, fingerprint="old")
    put_row(db, linked, 3, status="failed")
    _gap_run(factory, linked, 1)
    _gap_run(factory, linked, 4, status="failed")
    _gap_run(factory, linked, 5, fingerprint="old")
    db.expire_all()
    expected, actual = _before_split(db), progress.compute(db)
    expected.pop("checked_at")
    actual.pop("checked_at")
    assert expected == actual
    states = progress.round_states(db)["states"]
    assert [states[linked.id, n] for n in range(1, 6)] == [
        "done", "control_stale", "control_failed", "gaps_failed", "gaps_stale"]


def feature_rows(db, linked):
    for n, digest in [(1, "digest-a"), (2, "digest-a"), (3, "digest-b")]:
        put_row(db, linked, n)
        row = db.get(ReplayRoundControl, (linked.id, n))
        row.summary = cf.pack_summary({"provenance": {"inputs": {"geometry": {"features": digest}}}})
    put_row(db, linked, 4)  # no provenance
    db.commit()


def test_feature_verification_groups_current_rounds_and_keeps_results(errors, db, factory, linked, monkeypatch):
    from app.services.control_feature_verification import VerificationResult
    feature_rows(db, linked)
    calls = []
    def verify(session, row):
        calls.append((row.replay_id, row.round_number))
        return VerificationResult("verified", "failed" if row.round_number == 1 else "verified", "current",
                                  ("stored reason", "second reason"))
    assert kind(errors, db, "feature_failed")["note"] == "not checked yet"
    errors._verify_features(factory, verify)
    found = kind(errors, db, "feature_failed")
    assert calls == [(linked.id, 1), (linked.id, 3)]
    assert found["count"] == 1
    row = found["rows"][0]
    assert row["round"] == 1 and row["match"] == str(linked.match_uuid)
    assert row["figures"] == {"digest": "digest-a", "integrity": "verified", "recompilation": "failed",
                              "freshness": "current", "rounds": 2}
    assert row["error"] == "stored reason; second reason"
    assert "verify_stored_round" in row["reproduce"] and "--read-only python -c" in row["reproduce"]
    assert found["note"] == "2 verified, 1 unavailable or unsupported"
    monkeypatch.setattr(errors, "_start_verification", errors._real_start_verification)
    errors.cached(db, now=100)
    errors.cached(db, now=200)
    errors._verify_features(factory, verify)
    assert len(calls) == 2
    put_row(db, linked, 1, fingerprint="old")
    assert kind(errors, db, "feature_failed")["rows"][0]["round"] == 2
    assert kind(errors, db, "feature_failed")["rows"][0]["figures"]["rounds"] == 1


def test_verification_reports_progress_during_serial_run(errors, db, factory, linked, monkeypatch):
    from app.services.control_feature_verification import VerificationResult
    feature_rows(db, linked)
    representatives, missing = errors._feature_representatives(db)
    monkeypatch.setattr(errors, "_feature_representatives", lambda session, collected=None: (representatives, missing))
    # The in-memory SQLite connection is per thread: the worker thread can't read the fixture's rows back.
    monkeypatch.setattr(errors, "_load_round", lambda session, row: row)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def verify(session, row):
        calls.append(row.round_number)
        if len(calls) == 2:
            entered.set()
            assert release.wait(10)
        return VerificationResult("verified", "verified", "current")
    worker = threading.Thread(target=errors._verify_features, args=(factory, verify))
    worker.start()
    try:
        assert entered.wait(10)
        assert kind(errors, db, "feature_failed")["note"] == "checking 1 of 2"
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and calls == [1, 3]


def test_cached_starts_one_daemon_verifier_and_none_in_demo(errors, db, monkeypatch):
    # Restore the actual starter, suppressed by the ordinary inventory fixture.
    monkeypatch.setattr(errors, "_start_verification", errors._real_start_verification)
    workers = []
    entered, release = threading.Event(), threading.Event()
    def body():
        workers.append(threading.current_thread())
        entered.set()
        assert release.wait(10)
    monkeypatch.setattr(errors, "_verify_features", body)
    monkeypatch.setattr(settings, "demo_mode", True)
    errors.cached(None, now=100)
    errors.cached(None, now=101)
    assert workers == []
    monkeypatch.setattr(settings, "demo_mode", False)
    try:
        errors.cached(db, now=100)
        assert entered.wait(10)
        errors.cached(db, now=101)
        assert len(workers) == 1 and workers[0].daemon
    finally:
        release.set()
        for worker in workers:
            worker.join(10)
    assert all(not worker.is_alive() for worker in workers)


def test_artifacts_arriving_after_the_first_pass_get_a_new_pass(errors, db, factory, linked, monkeypatch):
    from app.services.control_feature_verification import VerificationResult

    def verify(session, row):
        return VerificationResult("verified", "verified", "current")
    errors._verify_features(factory, verify)    # nothing to check yet: the rounds are still recomputing
    assert errors._verification_started and not errors._verification_running
    real_pass = errors._verify_features
    spawned = []
    monkeypatch.setattr(errors, "_verify_features", lambda: spawned.append(threading.current_thread()))

    def start_and_wait():
        errors._real_start_verification()
        for worker in spawned:
            worker.join(10)
    start_and_wait()
    assert spawned == [], "nothing current is unchecked, so no new pass"
    feature_rows(db, linked)
    kind(errors, db, "feature_failed")    # the card's next refresh sees them
    start_and_wait()
    assert len(spawned) == 1, "the new artifacts get a pass"
    monkeypatch.setattr(errors, "_verification_running", False)
    real_pass(factory, verify)
    assert kind(errors, db, "feature_failed")["note"] == "2 verified, 1 unavailable or unsupported"
    start_and_wait()
    assert len(spawned) == 1, "all checked again: no third pass"


def test_prompt_real_conflict_preserves_all_evidence_and_excludes_identity(errors, db, linked):
    linked.match_uuid = RECORD["match"]
    db.query(Player).update({Player.display_name: "synthetic-private-name"})
    db.commit()
    height(db, linked.map_name, asset=asset([RECORD]), status="active")
    body = errors.compute(db, limit=None)
    text = errors.prompt(body, "box_conflict")
    for value in ("conorlum/valo-with-friends-tracker", "webapp/", "read-only", "likely cause", "propose a fix",
                  "owner approves", "--expect-database valowithfriendsdb --read-only", "z: 2.0", '"piece": 15',
                  '"tops": [2.4]', '"dist_m": 0.58', '"dist_m": null', "heights_revision", STAMP.isoformat()):
        assert value in text
    row = next(k for k in body["kinds"] if k["key"] == "box_conflict")["rows"][0]
    for key in ("map", "match", "agent", "link", "reproduce"):
        assert str(row[key]) in text
    assert "synthetic-private-name" not in text
    with pytest.raises(KeyError):
        errors.prompt(body, "unknown")
    assert "nothing to fix" in errors.prompt(body, "control_failed")


def prompt_fixture(errors, counts):
    return {"kinds": [{"key": key, "label": dict(errors.KINDS)[key], "count": count, "note": "synthetic note",
                       "rows": [errors._row(map_name="Ascent", match=str(UUID(int=i + 1)), n=i + 1,
                                            error="stored error\nwith newline", revision={"revision": 9})
                                for i in range(count)]} for key, count in counts], "checked_at": STAMP.isoformat()}


def test_prompt_limit_and_page_keep_full_counts(errors):
    body = prompt_fixture(errors, [("control_failed", 201)])
    text = errors.prompt(body, "control_failed")
    assert text.count("\nmatch:") == 200
    assert "1 more" in text and "SELECT" in text and "replay_round_control" in text
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE)\b", text, re.I)
    assert "stored error\nwith newline" in text
    shown = errors.page(body)
    assert shown["kinds"][0]["count"] == 201 and len(shown["kinds"][0]["rows"]) == 50
    assert len(body["kinds"][0]["rows"]) == 201


def test_all_prompt_shares_200_in_kind_order(errors):
    body = prompt_fixture(errors, [("control_failed", 150), ("gaps_failed", 100)])
    text = errors.prompt(body)
    assert text.count("\nmatch:") == 200 and "50 more" in text
    assert text.index("Map control failed") < text.index("Timing gaps failed")
    assert "replay_round_gap_runs" in text


def test_feature_representatives_never_load_the_control_blob(errors, db, linked):
    from sqlalchemy import event

    feature_rows(db, linked)
    statements = []
    listen = lambda conn, cursor, sql, *rest: statements.append(sql)  # noqa: E731
    event.listen(db.get_bind(), "before_cursor_execute", listen)
    try:
        groups, missing = errors._feature_representatives(db)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", listen)
    assert set(groups) == {"digest-a", "digest-b"} and missing == 1
    chosen = [s for s in statements if "replay_round_control" in s and "summary" in s]
    assert chosen and not any("replay_round_control.data" in s for s in chosen)


def test_a_concurrent_reader_replacing_the_digest_cache_never_breaks_this_one(errors, db, linked):
    # Branch review 2, finding 4: the request reader and the background verifier share _digests. One replacing it
    # with its own snapshot mid-loop must not leave the other indexing a key it no longer holds.
    feature_rows(db, linked)
    collected = errors.replay_progress.round_states(db)

    class Replays(dict):
        def __getitem__(self, key):
            errors._digests = {}          # another reader's snapshot, without this one's keys
            return super().__getitem__(key)

    collected["replays"] = Replays(collected["replays"])
    groups, missing = errors._feature_representatives(db, collected)
    assert set(groups) == {"digest-a", "digest-b"} and missing == 1


def test_feature_summaries_are_read_once_per_fingerprint(errors, db, linked):
    from sqlalchemy import event

    feature_rows(db, linked)
    statements = []
    listen = lambda conn, cursor, sql, *rest: statements.append(sql)  # noqa: E731
    event.listen(db.get_bind(), "before_cursor_execute", listen)
    try:
        first = errors._feature_representatives(db)
        reads = sum("replay_round_control.summary" in s for s in statements)
        second = errors._feature_representatives(db)
        again = sum("replay_round_control.summary" in s for s in statements) - reads
        put_row(db, linked, 2, fingerprint="new")      # a recompute: its summary is read afresh
        row = db.get(ReplayRoundControl, (linked.id, 2))
        row.summary = cf.pack_summary({"provenance": {"inputs": {"geometry": {"features": "digest-c"}}}})
        db.commit()
        collected = errors.replay_progress.round_states(db)
        collected["states"][linked.id, 2] = "done"     # as if the new fingerprint were the planner's
        before = sum("replay_round_control.summary" in s for s in statements)
        third = errors._feature_representatives(db, collected)
        reread = sum("replay_round_control.summary" in s for s in statements) - before
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", listen)
    assert reads == 1 and again == 0
    assert {d: c for d, (_, _, c) in first[0].items()} == {d: c for d, (_, _, c) in second[0].items()}
    assert reread == 1
    assert {d: c for d, (_, _, c) in third[0].items()} == {"digest-a": 1, "digest-b": 1, "digest-c": 1}
    assert (linked.id, 2, "new") in errors._digests and len(errors._digests) == 4
