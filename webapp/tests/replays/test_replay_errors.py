"""Stored replay errors: SQLite and synthetic identities only, no worker or network."""

import importlib
import inspect
import io
import json
import sys
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
from app.services import control_heights as ch, replay_progress as progress
from app.services.replay_progress import uploads, cf, replay_control, replay_gaps, STEPS, _LABELS
from app.services.replay_util import Freshness

STAMP = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
KEYS = ["box_conflict", "control_failed", "gaps_failed", "moments_failed", "util_failed",
        "heights_rejected", "upload_failed", "upload_held", "store_failed", "ack_missing", "stuck",
        "unlinked", "needs_a_person"]
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
    assert ch.box_conflicts(db, linked.map_name, RECORD["match"]) == [RECORD]
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
    monkeypatch.setattr(errors, "compute", lambda db: calls.append(1) or {"n": len(calls)})
    first = errors.cached(db, now=100)
    assert errors.cached(db, now=100 + errors.TTL_S - 1) is first
    assert errors.cached(db, now=100 + errors.TTL_S) == {"n": 2}


def test_all_box_conflicts_best_effort(errors, db, monkeypatch):
    height(db, status="active", asset=b"not an asset")
    assert ch.all_box_conflicts(db) == []
    def broken(db):
        raise RuntimeError("synthetic database error")
    monkeypatch.setattr(ch, "active_rows", broken)
    assert ch.all_box_conflicts(db) == []

def test_live_argument_is_reserved(errors, db):
    assert errors.compute(db, live=object()) == errors.compute(db)


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
    assert all(collected.values()), "exercise every stored kind"
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
