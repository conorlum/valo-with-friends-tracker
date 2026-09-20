"""One bad match must not cost a whole player -- and a service that is
refusing everything must not be ground through one failure at a time.

The 2026-09-19 roster run lost the remainder of Beef Shortrib#Galbi's ingest
to a single ERR_CONNECTION_CLOSED. Fetching a match page writes nothing, so
that attempt could simply have been repeated. These pin the three layers:
retry the fetch, skip the match that still won't load, give up on a streak.
"""

import pytest

from app.adapters import trackergg_browserstate_source as src
from app.adapters.trackergg_browserstate_source import (
    MATCH_FETCH_ATTEMPTS,
    MAX_CONSECUTIVE_MATCH_FAILURES,
    _ingest_discovered,
)


class FakeDB:
    """Enough Session for the dedup query and rollback accounting."""

    def __init__(self, existing=()):
        self.existing = set(existing)
        self.rollbacks = 0
        self.commits = 0

    def query(self, _model):
        db = self

        class _Q:
            def filter_by(self, external_id):
                self.ext = external_id
                return self

            def one_or_none(self_inner):
                return object() if self_inner.ext in db.existing else None

        return _Q()

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        self.commits += 1


class FakeMatch:
    def __init__(self, match_id, external_id):
        self.id = match_id
        self.external_id = external_id
        self.map_name = "Haven"
        self.played_at = None


@pytest.fixture
def wiring(monkeypatch):
    """Stubs everything around the loop so only its control flow is tested."""
    state = {"fetch_calls": [], "loaded": [], "slept": [], "scored": []}

    monkeypatch.setattr(src, "verify_ingest_preflight", lambda db: None)
    monkeypatch.setattr(src, "find_cached_player_ids_for_match", lambda db, mid: set())
    monkeypatch.setattr(src, "invalidate_player_cache", lambda db, ids: None)
    monkeypatch.setattr(src, "invalidate_site_stats_cache", lambda db: None)
    monkeypatch.setattr(src, "compute_impact_for_match",
                        lambda db, mid: state["scored"].append(mid))
    monkeypatch.setattr(src.time, "sleep", lambda s: state["slept"].append(s))
    monkeypatch.setattr(src.random, "uniform", lambda a, b: 0.0)

    counter = {"n": 0}

    def fake_load(db, payload):
        counter["n"] += 1
        state["loaded"].append(payload)
        return FakeMatch(counter["n"], payload)

    monkeypatch.setattr(src, "load_match", fake_load)
    return state


def make_fetch(state, failures):
    """`failures` maps match_id -> how many times its fetch should fail."""
    remaining = dict(failures)

    def fetch(page, match_id):
        state["fetch_calls"].append(match_id)
        if remaining.get(match_id, 0) > 0:
            remaining[match_id] -= 1
            raise RuntimeError(f"net::ERR_CONNECTION_CLOSED for {match_id}")
        return match_id

    return fetch


def test_transient_fetch_failure_is_retried_and_succeeds(monkeypatch, wiring):
    monkeypatch.setattr(src, "fetch_match_json", make_fetch(wiring, {"b": 1}))

    progress = _ingest_discovered(FakeDB(), None, ["a", "b", "c"])

    assert progress.ingested == 3, "a retried match still counts as ingested"
    assert progress.failed_ids == []
    assert progress.error is None
    assert wiring["fetch_calls"] == ["a", "b", "b", "c"], "b fetched twice"
    assert 10 in wiring["slept"], "first retry waits the first backoff"


def test_retry_gives_up_after_the_attempt_limit_and_skips_the_match(monkeypatch, wiring):
    # "b" fails more times than we will ever attempt.
    monkeypatch.setattr(src, "fetch_match_json", make_fetch(wiring, {"b": 99}))

    progress = _ingest_discovered(FakeDB(), None, ["a", "b", "c"])

    assert progress.ingested == 2, "a and c still got in"
    assert progress.failed_ids == ["b"]
    assert progress.error is None, "one skip is not a reason to abandon the player"
    assert wiring["fetch_calls"].count("b") == MATCH_FETCH_ATTEMPTS
    assert "c" in wiring["fetch_calls"], "the loop continued past the bad match"


def test_a_streak_of_failures_gives_up_rather_than_grinding(monkeypatch, wiring):
    ids = [f"m{i}" for i in range(20)]
    monkeypatch.setattr(src, "fetch_match_json",
                        make_fetch(wiring, {i: 99 for i in ids}))

    progress = _ingest_discovered(FakeDB(), None, ids)

    assert progress.error is not None, "a total outage must stop the run"
    assert len(progress.failed_ids) == MAX_CONSECUTIVE_MATCH_FAILURES
    attempted_ids = set(wiring["fetch_calls"])
    assert len(attempted_ids) == MAX_CONSECUTIVE_MATCH_FAILURES, (
        "must not keep trying the whole list once it is clearly not us"
    )


def test_a_success_resets_the_failure_streak(monkeypatch, wiring):
    """Scattered failures across a long list are not an outage, so the run
    should survive more of them than the streak limit."""
    ids = [f"m{i}" for i in range(12)]
    # Fail every other match -- 6 failures total, never 5 in a row.
    failures = {f"m{i}": 99 for i in range(0, 12, 2)}
    monkeypatch.setattr(src, "fetch_match_json", make_fetch(wiring, failures))

    progress = _ingest_discovered(FakeDB(), None, ids)

    assert progress.error is None, "alternating failures are not a streak"
    assert len(progress.failed_ids) == 6
    assert progress.ingested == 6


def test_load_failure_rolls_back_and_is_not_retried(monkeypatch, wiring):
    """A malformed payload fails the same way every time, and leaving the
    session in a poisoned transaction would cascade into every later match."""
    monkeypatch.setattr(src, "fetch_match_json", make_fetch(wiring, {}))

    def bad_load(db, payload):
        if payload == "b":
            raise ValueError("malformed match payload")
        return FakeMatch(1, payload)

    monkeypatch.setattr(src, "load_match", bad_load)
    db = FakeDB()

    progress = _ingest_discovered(db, None, ["a", "b", "c"])

    assert progress.failed_ids == ["b"]
    assert db.rollbacks == 1, "must roll back the poisoned transaction"
    assert wiring["fetch_calls"].count("b") == 1, "load failures are not retried"
    assert progress.ingested == 2


def test_already_present_matches_are_not_fetched_at_all(monkeypatch, wiring):
    monkeypatch.setattr(src, "fetch_match_json", make_fetch(wiring, {}))

    progress = _ingest_discovered(FakeDB(existing={"b"}), None, ["a", "b", "c"])

    assert progress.already_present == 1
    assert progress.attempted == 2
    assert "b" not in wiring["fetch_calls"]


def test_first_error_is_kept_so_legacy_callers_still_raise(monkeypatch, wiring):
    """_dedup_and_ingest raises on ANY failure, including ones the loop
    skipped past, so snowball_1hour.py and the crawl keep their behaviour."""
    monkeypatch.setattr(src, "fetch_match_json", make_fetch(wiring, {"b": 99}))

    progress = _ingest_discovered(FakeDB(), None, ["a", "b", "c"])
    assert progress.first_error is not None

    monkeypatch.setattr(src, "_ingest_discovered", lambda *a, **k: progress)
    with pytest.raises(RuntimeError):
        src._dedup_and_ingest(FakeDB(), None, ["a", "b", "c"])


def test_clean_run_raises_nothing_through_the_legacy_wrapper(monkeypatch, wiring):
    monkeypatch.setattr(src, "fetch_match_json", make_fetch(wiring, {}))

    dirty = src._dedup_and_ingest(FakeDB(), None, ["a", "b"])

    assert dirty == set()


def test_failures_are_recorded_in_the_ledger(monkeypatch, wiring, tmp_path):
    """A ledger should say what the run could NOT get, not only what it did --
    a skipped match is otherwise indistinguishable from one never discovered."""
    import json

    monkeypatch.setattr(src, "fetch_match_json", make_fetch(wiring, {"b": 99}))
    ledger = src.IngestLedger(tmp_path / "run.jsonl", run_label="test")

    _ingest_discovered(FakeDB(), None, ["a", "b", "c"], ledger, "Someone#NA1")

    rows = [json.loads(l) for l in
            (tmp_path / "run.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    failed = [r for r in rows if r["event"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["external_id"] == "b"
    assert "ERR_CONNECTION_CLOSED" in failed[0]["reason"]
    assert len([r for r in rows if r["event"] == "ingested"]) == 2
