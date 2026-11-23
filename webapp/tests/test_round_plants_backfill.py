"""Owner plant maintenance uses SQLite, synthetic payloads and fake Chrome pages."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import gzip
import json
from types import SimpleNamespace
import zlib

import pytest
from sqlalchemy import event, select

from app.adapters.tracker_verification import TrackerVerificationTimeout
from app.db import Base
from app.models import Match, Round, RoundPlant
from app.models.replay import Replay, ReplayRound
from app.services import round_plants as plants
from tests.test_round_plants import db, planted_match


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "unavailable.json"
    monkeypatch.setattr(plants, "UNAVAILABLE_PATH", path, raising=False)
    monkeypatch.setattr(plants, "PAUSE_PATH", tmp_path / "round_plants.pause", raising=False)
    monkeypatch.setattr(plants, "CORRECTED_PATH", tmp_path / "corrected.json", raising=False)
    return path


def matches(db, count):
    rows = [planted_match(db, external_id=f"synthetic-match-{n}") for n in range(count)]
    for n, row in enumerate(rows):
        row.played_at = datetime(2026, 1, 1, tzinfo=timezone.utc) - timedelta(days=n)
    db.commit()
    return [row.external_id for row in rows]


def payload(*numbers):
    return {"metadata": {}, "segments": [
        {"type": "round-summary", "attributes": {"round": n},
         "metadata": {"plant": {"site": "B", "location": {"x": 123.5, "y": -456}}}}
        for n in numbers
    ]}


def run(db, fetch, **kwargs):
    kwargs.setdefault("alert", lambda: None)
    return plants.backfill(db, object(), limit=None, fetch=fetch,
                           sleep=lambda _: None, uniform=lambda a, b: a, **kwargs)


def snapshot(db):
    return {table.name: sorted(repr(tuple(row)) for row in db.execute(select(table)))
            for table in Base.metadata.sorted_tables if table.name != "round_plants"}


def replay(db, *, offset=0, stored_time=None, compression=gzip.compress, upto=1):
    match = planted_match(db, upto=upto)
    db.scalar(select(Round).where(Round.match_id == match.id)).plant_time = stored_time
    row = Replay(match_uuid="00000000-0000-0000-0000-000000000001", match_id=match.id,
                 map_name=match.map_name, round_count=max(2, upto), format_version=1, recipe="synthetic",
                 game_branch="release-11.00", source_sha256="0" * 64, source="local",
                 link_status="linked", link_inputs={}, clock_offset=offset)
    db.add(row)
    db.flush()
    blob = {"v": 1, "plant": None, "util": [
        {"k": "damage", "kind": "Bomb", "u": 1, "v": 2, "t": 3},
        {"k": "ability", "kind": "Bomb", "u": 1234, "v": 5678, "t": 25},
        {"k": "ability", "kind": "Bomb", "u": 9999, "v": 9999, "t": 35},
    ]}
    db.add_all([ReplayRound(replay_id=row.id, round_number=n,
                            data=compression(json.dumps(blob).encode())) for n in range(1, max(2, upto) + 1)])
    db.commit()


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("compression", [gzip.compress, zlib.compress])
@pytest.mark.parametrize("stored_time,expected", [(None, 32), (15, 15)])
def test_sync_replays_clock_missing_round_and_other_tables(db, dry_run, compression,
                                                         stored_time, expected):
    replay(db, offset=7, stored_time=stored_time, compression=compression)
    before = snapshot(db)
    result = plants.sync_replays(db, dry_run=dry_run)
    assert result["skipped"] == 1
    assert result["inserted"] == 1
    row = db.scalar(select(RoundPlant))
    if dry_run:
        assert row is None
    else:
        assert (row.u, row.v, row.source, row.plant_time) == (1234, 5678, "replay", expected)
    assert snapshot(db) == before


def test_sync_skips_corrupt_round_and_continues(db):
    replay(db)
    db.scalar(select(ReplayRound).where(ReplayRound.round_number == 2)).data = b"broken"
    db.commit()
    result = plants.sync_replays(db, dry_run=False)
    assert result["inserted"] == result["skipped"] == 1


def test_sync_continues_after_a_round_missing_from_database(db):
    replay(db)
    first = db.scalar(select(ReplayRound).where(ReplayRound.round_number == 1))
    db.add(ReplayRound(replay_id=first.replay_id, round_number=0, data=first.data))
    db.commit()
    result = plants.sync_replays(db)
    assert result["skipped"] == 2 and result["inserted"] == 1
    assert db.scalar(select(RoundPlant)).round_number == 1


def test_dry_run_preserves_existing_tracker_row_without_sql_writes(db):
    replay(db)
    linked_replay = db.get(Replay, 1)
    plants.upsert_plants(db, db.get(Match, linked_replay.match_id), [
        dict(round_number=1, u=111, v=222, site="B")], source="tracker")
    db.commit()
    stored = db.scalar(select(RoundPlant))
    writes = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(db.bind, "before_cursor_execute", record)
    try:
        result = plants.sync_replays(db, dry_run=True)
    finally:
        event.remove(db.bind, "before_cursor_execute", record)
    assert result["updated"] == 1
    assert (stored.u, stored.v, stored.source, stored.site) == (111, 222, "tracker", "B")
    assert writes == []


def test_pending_matches_filters_before_limit_and_orders_newest_first(db):
    ids = matches(db, 3)
    assert [m.external_id for m in plants.pending_matches(db, limit=1, skip={ids[0]})] == ids[1:2]
    assert plants.pending_matches(db, limit=0, skip=()) == []
    with pytest.raises(ValueError):
        plants.pending_matches(db, limit=-1)


def test_killed_run_resumes_from_database_and_only_changes_plants(db):
    ids = matches(db, 4)
    before = snapshot(db)
    fetched = []

    def fetch(page, external_id):
        fetched.append(external_id)
        if external_id == ids[2]:
            raise KeyboardInterrupt()
        return payload(1)

    with pytest.raises(KeyboardInterrupt):
        run(db, fetch)
    db.rollback()
    assert fetched == ids[:3]
    fetched.clear()
    result = run(db, lambda page, key: (fetched.append(key), payload(1))[1])
    assert fetched == ids[2:]
    assert result["done"] == 2 and result["left"] == 0
    assert snapshot(db) == before


def test_skip_list_limit_and_explicit_retry(db, ledger):
    ids = matches(db, 2)
    fetched = []

    def fetch(page, key):
        fetched.append(key)
        return payload() if key == ids[0] else payload(1)

    def once(**kwargs):
        return plants.backfill(db, None, limit=1, fetch=fetch, sleep=lambda _: None,
                               uniform=lambda a, b: a, **kwargs)

    once()
    assert json.loads(ledger.read_text()) == [ids[0]]
    once()
    assert fetched == ids
    once(retry_unavailable=True)
    assert fetched == ids + ids[:1]


def test_partial_payload_does_not_block_following_matches(db, ledger):
    match = planted_match(db, upto=2)
    db.commit()
    result = run(db, lambda page, key: payload(1))
    assert result["done"] == 1
    assert json.loads(ledger.read_text()) == [match.external_id]
    assert plants.pending_matches(db, limit=None, skip=set())
    assert not plants.pending_matches(db, limit=None, skip={match.external_id})


def test_verification_stops_immediately_and_keeps_commits(db, ledger):
    ids = matches(db, 4)
    fetched = []

    def fetch(page, key):
        fetched.append(key)
        if key == ids[1]:
            raise TrackerVerificationTimeout()
        return payload(1)

    result = run(db, fetch)
    assert result["stopped"] == "verification"
    assert result["done"] == 1 and result["failed"] == 0
    assert fetched == ids[:2]
    assert json.loads(ledger.read_text()) == []
    assert len(db.scalars(select(RoundPlant)).all()) == 1


def test_five_consecutive_failures_stop_and_pace(db):
    ids = matches(db, 7)
    fetched, pauses, ranges = [], [], []

    def fetch(page, key):
        fetched.append(key)
        raise ValueError("synthetic failure")

    result = plants.backfill(db, None, limit=None, fetch=fetch, sleep=pauses.append,
                             uniform=lambda a, b: (ranges.append((a, b)), 8)[1])
    assert result["stopped"] == "failures" and result["failed"] == 5
    assert fetched == ids[:5]
    assert ranges == [(5, 12)] * 4 and pauses == [8] * 4


def test_success_resets_consecutive_failure_count(db):
    ids = matches(db, 10)
    fetched = []

    def fetch(page, key):
        fetched.append(key)
        if key != ids[4]:
            raise ValueError("synthetic failure")
        return payload(1)

    result = run(db, fetch)
    assert fetched == ids and result["done"] == 1 and result["failed"] == 9


def test_cli_verification_exit_resume_and_status(db, monkeypatch, capsys):
    from scripts import round_plants as cli

    matches(db, 2)

    @contextmanager
    def fake_page():
        yield object()

    monkeypatch.setattr(cli, "SessionLocal", lambda: db)
    monkeypatch.setattr(cli, "tracker_page", fake_page)
    monkeypatch.setattr(cli, "resume_python", lambda: r".\.venv313\Scripts\python.exe")

    def fetch(page, key):
        raise TrackerVerificationTimeout()

    monkeypatch.setattr(cli, "fetch_match_json", fetch)
    assert cli.main(["backfill", "--limit", "1", "--retry-unavailable"]) == 2
    output = capsys.readouterr().out
    resume = next(line for line in output.splitlines() if line.startswith("Resume:"))
    assert r".\.venv313\Scripts\python.exe scripts\with_friends_db.py --expect-database valowithfriendsdb" in resume
    assert r"scripts\round_plants.py backfill --limit 1 --retry-unavailable" in resume
    assert "--read-only" not in resume
    assert "done: 0" in output and "left: 2" in output and "estimated" in output
    assert cli.main(["status"]) == 0
    assert "skip-list: 0" in capsys.readouterr().out


def test_cli_sync_dry_run_and_populated_status_need_no_chrome(db, ledger, monkeypatch, capsys):
    from scripts import round_plants as cli

    replay(db)
    monkeypatch.setattr(cli, "SessionLocal", lambda: db)

    def no_chrome():
        pytest.fail("status and sync must not attach to Chrome")

    monkeypatch.setattr(cli, "tracker_page", no_chrome)
    ledger.write_text(json.dumps(["synthetic-unavailable-1", "synthetic-unavailable-2"]))
    assert cli.main(["status"]) == 0
    assert "skip-list: 2" in capsys.readouterr().out
    assert cli.main(["sync-replays", "--dry-run"]) == 0
    assert "dry-run:" in capsys.readouterr().out
    assert db.scalar(select(RoundPlant)) is None


@pytest.mark.parametrize("statuses,unavailable", [
    ([404], True), ([410], True), ([403], False), ([429], False), ([500], False),
    ([404, 429], False), ([429, 404], True),
])
@pytest.mark.parametrize("body", [{}, {"data": None}])
def test_fetch_match_json_classifies_last_missing_response(monkeypatch, statuses, unavailable, body):
    from app.adapters import tracker_verification as verification
    from app.adapters import trackergg_browserstate_source as adapter

    callbacks = {}
    def goto(*args, **kwargs):
        for status in statuses:
            callbacks["response"](SimpleNamespace(
                url=adapter.MATCH_API_MARKER_TMPL.format(match_id="synthetic-match"),
                status=status, json=lambda: body))

    page = SimpleNamespace(on=lambda name, callback: callbacks.update({name: callback}),
                           goto=goto, wait_for_timeout=lambda _: None,
                           remove_listener=lambda name, callback: callbacks.pop(name))
    monkeypatch.setattr(adapter, "wait_for_owner", lambda page: None)
    with pytest.raises(RuntimeError) as error:
        adapter.fetch_match_json(page, "synthetic-match")
    expected = (verification.TrackerMatchUnavailable if unavailable
                else verification.TrackerRateLimited if statuses[-1] == 429 else RuntimeError)
    assert type(error.value) is expected
    assert callbacks == {}


def test_fetch_good_body_after_404_is_used(monkeypatch):
    from app.adapters import trackergg_browserstate_source as adapter

    callbacks = {}
    good = payload(1)
    def goto(*args, **kwargs):
        for status, body in [(404, {}), (200, {"data": good})]:
            callbacks["response"](SimpleNamespace(
                url=adapter.MATCH_API_MARKER_TMPL.format(match_id="synthetic-match"),
                status=status, json=lambda: body))

    page = SimpleNamespace(on=lambda name, callback: callbacks.update({name: callback}),
                           goto=goto, wait_for_timeout=lambda _: None,
                           remove_listener=lambda name, callback: callbacks.pop(name))
    monkeypatch.setattr(adapter, "wait_for_owner", lambda page: None)
    assert adapter.fetch_match_json(page, "synthetic-match") == good


def test_six_unavailable_matches_do_not_block_the_next(db, ledger):
    from app.adapters import tracker_verification as verification

    ids = matches(db, 7)
    before = snapshot(db)
    fetched = []
    def fetch(page, key):
        fetched.append(key)
        if key != ids[-1]:
            raise verification.TrackerMatchUnavailable("status 404")
        return payload(1)

    result = run(db, fetch)
    assert fetched == ids
    assert (result["unavailable"], result["failed"], result["stopped"]) == (6, 0, None)
    assert result["done"] == 1 and result["left"] == 0
    assert set(json.loads(ledger.read_text())) == set(ids[:-1])
    assert run(db, fetch)["done"] == 0
    assert fetched == ids
    assert snapshot(db) == before


@pytest.mark.parametrize("bad", [None, [], "invalid", {}, {"segments": None}, {"segments": {}}])
def test_unusable_payload_is_skip_listed_without_failure(db, ledger, bad):
    ids = matches(db, 2)
    fetched = []
    def fetch(page, key):
        fetched.append(key)
        return bad if key == ids[0] else payload(1)

    result = run(db, fetch)
    assert fetched == ids
    assert (result["unavailable"], result["failed"], result["stopped"]) == (1, 0, None)
    assert json.loads(ledger.read_text()) == ids[:1]
    assert plants._tracker_plants(db.scalar(select(Match)), bad) == []


def test_transient_429_is_a_failure_and_is_not_skip_listed(db, ledger):
    ids = matches(db, 1)
    def fetch(page, key):
        raise RuntimeError("status 429")

    result = run(db, fetch)
    assert (result["failed"], result["unavailable"], result["left"]) == (1, 0, 1)
    assert json.loads(ledger.read_text()) == []
    assert plants.pending_matches(db)[0].external_id == ids[0]


@pytest.mark.parametrize("attributes", [None, [], "invalid", {}, {"round": []}])
def test_malformed_summary_keeps_other_plants(db, attributes):
    planted_match(db, upto=2)
    db.commit()
    value = payload(1, 2)
    value["segments"][1]["attributes"] = attributes
    result = run(db, lambda page, key: value)
    assert result["failed"] == 0
    assert [row.round_number for row in db.scalars(select(RoundPlant))] == [1]


@pytest.mark.parametrize("dry_run", [False, True])
def test_sync_batches_outcomes_once_per_replay(db, monkeypatch, dry_run):
    replay(db, upto=3)
    original = plants.outcomes_for_match
    calls = []
    def counted(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(plants, "outcomes_for_match", counted)
    result = plants.sync_replays(db, dry_run=dry_run)
    assert len(calls) == 1
    assert result["inserted"] == 3 and result["skipped"] == 0
    assert len(db.scalars(select(RoundPlant)).all()) == (0 if dry_run else 3)


@pytest.mark.parametrize("dry_run", [False, True])
def test_sync_batch_failure_rolls_back_and_falls_back_per_plant(db, monkeypatch, dry_run):
    replay(db, upto=3)
    before = snapshot(db)
    original = plants.upsert_plants
    calls = []
    def fail_second(db, match, positions, source, **kwargs):
        calls.append([plant["round_number"] for plant in positions])
        if any(plant["round_number"] == 2 for plant in positions):
            original(db, match, positions[:1], source, **kwargs)
            raise RuntimeError("synthetic storage failure")
        return original(db, match, positions, source, **kwargs)

    monkeypatch.setattr(plants, "upsert_plants", fail_second)
    result = plants.sync_replays(db, dry_run=dry_run)
    assert calls == [[1, 2, 3], [1], [2], [3]]
    assert result["inserted"] == 2 and result["skipped"] == 1
    assert [row.round_number for row in db.scalars(select(RoundPlant).order_by(
        RoundPlant.round_number))] == ([] if dry_run else [1, 3])
    assert snapshot(db) == before


def test_replay_bomb_on_unplanted_db_round_keeps_position_without_rates(db):
    replay(db)
    rnd = db.scalar(select(Round))
    rnd.planted = False
    # Include a kill so the last state would describe the end rather than the plant.
    from app.models import KillEvent, MatchPlayer
    players = db.scalars(select(MatchPlayer).order_by(MatchPlayer.id)).all()
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players[0].id,
                     death_match_player_id=players[-1].id, weapon="Vandal", event_time_seconds=30))
    db.commit()
    plants.sync_replays(db)
    row = db.scalar(select(RoundPlant))
    assert (row.u, row.v, row.plant_time) == (1234, 5678, 25)
    assert (row.attackers_alive, row.defenders_alive, row.attackers_won) == (None, None, None)


# --- R1.1 F2: a 200 with no usable data is permanent -----------------------------


def _respond(monkeypatch, responses):
    """Run fetch_match_json against a page that answers with (status, json callable) pairs."""
    from app.adapters import trackergg_browserstate_source as adapter

    callbacks = {}

    def goto(*args, **kwargs):
        for status, body in responses:
            callbacks["response"](SimpleNamespace(
                url=adapter.MATCH_API_MARKER_TMPL.format(match_id="synthetic-match"),
                status=status, json=body))

    page = SimpleNamespace(on=lambda name, callback: callbacks.update({name: callback}),
                           goto=goto, wait_for_timeout=lambda _: None,
                           remove_listener=lambda name, callback: callbacks.pop(name))
    monkeypatch.setattr(adapter, "wait_for_owner", lambda page: None)
    with pytest.raises(RuntimeError) as error:
        adapter.fetch_match_json(page, "synthetic-match")
    assert callbacks == {}
    return type(error.value)


def _unparseable():
    raise ValueError("HTML challenge page")


@pytest.mark.parametrize("body", [{}, {"data": None}, {"errors": []}, ["unexpected array"], "text"])
@pytest.mark.parametrize("statuses,unavailable", [
    ([200], True), ([429, 200], True), ([200, 404], True), ([200, 429], False), ([200, 403], False),
    ([200, 500], False),
])
def test_status_200_json_without_data_is_unavailable_only_when_last(monkeypatch, body, statuses, unavailable):
    from app.adapters import tracker_verification as verification

    got = _respond(monkeypatch, [(status, lambda: body) for status in statuses])
    assert got is (verification.TrackerMatchUnavailable if unavailable
                   else verification.TrackerRateLimited if statuses[-1] == 429 else RuntimeError)


@pytest.mark.parametrize("responses", [
    [(200, _unparseable)],
    [(200, lambda: {}), (200, _unparseable)],
    [(200, _unparseable), (403, lambda: {})],
])
def test_status_200_that_does_not_parse_stays_retryable(monkeypatch, responses):
    assert _respond(monkeypatch, responses) is RuntimeError


# --- R1.1 F3: no transaction is open while a page loads --------------------------


def test_backfill_ends_its_transaction_before_every_fetch(db):
    ids = matches(db, 4)
    states = []

    def fetch(page, key):
        states.append(db.in_transaction())
        if key == ids[1]:
            raise ValueError("synthetic failure")
        return payload(1)

    result = run(db, fetch)
    assert states == [False] * 4
    assert result["done"] == 3 and result["failed"] == 1


def test_backfill_ends_its_transaction_before_pacing_too(db):
    matches(db, 2)
    events = []
    plants.backfill(db, None, limit=None,
                    fetch=lambda page, key: (events.append(("fetch", db.in_transaction())), payload(1))[1],
                    sleep=lambda s: events.append(("sleep", db.in_transaction())),
                    uniform=lambda a, b: a)
    assert events == [("fetch", False), ("sleep", False), ("fetch", False)]


def _commit_fails_once(db, monkeypatch):
    real, calls = db.commit, []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("synthetic dropped connection")
        return real()

    monkeypatch.setattr(db, "commit", flaky)


def test_backfill_carries_on_when_the_commit_before_a_fetch_fails(db, monkeypatch):
    ids = matches(db, 2)
    _commit_fails_once(db, monkeypatch)
    fetched = []
    result = run(db, lambda page, key: (fetched.append(key), payload(1))[1])
    assert fetched == ids and result["done"] == 2 and result["failed"] == 0


def test_end_transaction_never_raises(monkeypatch):
    class Dead:
        def commit(self):
            raise RuntimeError("commit")

        def rollback(self):
            raise RuntimeError("rollback")

    plants.end_transaction(Dead())


# --- R1.1 F8: status reports coverage per map and source -------------------------


def test_status_reports_coverage_per_map_and_source(db, ledger, monkeypatch, capsys):
    from scripts import round_plants as cli

    def at(number):
        return {"round_number": number, "site": "A", "u": 1234, "v": 5678}

    bind = planted_match(db, upto=2, external_id="synthetic-bind")
    ascent = planted_match(db, upto=1, external_id="synthetic-ascent")
    ascent.map_name = "Ascent"
    db.flush()
    plants.upsert_plants(db, bind, [at(1)], source="tracker")
    plants.upsert_plants(db, ascent, [at(1)], source="replay")
    db.commit()
    monkeypatch.setattr(cli, "SessionLocal", lambda: db)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "stored plants: 2; planted rounds: 3; skip-list: 0" in out
    lines = {line.split(":")[0].strip(): line for line in out.splitlines()
             if line.strip().startswith(("Bind:", "Ascent:"))}
    assert "planted rounds 2" in lines["Bind"] and "replay 0, tracker 1" in lines["Bind"]
    assert "coverage 50.0%" in lines["Bind"]
    assert "planted rounds 1" in lines["Ascent"] and "replay 1, tracker 0" in lines["Ascent"]
    assert "coverage 100.0%" in lines["Ascent"]


# --- Pause and resume -------------------------------------------------------------


def test_stop_before_the_first_match_fetches_nothing(db):
    matches(db, 2)
    fetched = []
    result = run(db, lambda page, key: fetched.append(key), should_stop=lambda: "paused")
    assert fetched == [] and result["stopped"] == "paused" and result["left"] == 2


def test_stop_requested_during_a_match_keeps_it_and_stops_before_the_next(db, ledger):
    ids = matches(db, 3)
    fetched, requested = [], []

    def fetch(page, key):
        fetched.append(key)
        requested.append(True)
        return payload(1)

    result = run(db, fetch, should_stop=lambda: "interrupted" if requested else None)
    assert fetched == ids[:1]
    assert result["done"] == 1 and result["stopped"] == "interrupted" and result["left"] == 2
    assert len(db.scalars(select(RoundPlant)).all()) == 1


def test_stop_after_the_pacing_wait_skips_the_fetch(db):
    ids = matches(db, 3)
    fetched, waited = [], []
    result = plants.backfill(db, None, limit=None, fetch=lambda page, key: (fetched.append(key), payload(1))[1],
                             sleep=waited.append, uniform=lambda a, b: a,
                             should_stop=lambda: "paused" if waited else None)
    assert fetched == ids[:1] and waited == [5] and result["stopped"] == "paused"


@pytest.mark.parametrize("error", [ValueError("driver gone"), plants.TrackerMatchUnavailable("x")])
def test_a_match_broken_by_the_stop_is_neither_failed_nor_skip_listed(db, ledger, error):
    ids = matches(db, 2)
    requested = []

    def fetch(page, key):
        requested.append(True)
        raise error

    result = run(db, fetch, should_stop=lambda: "interrupted" if requested else None)
    assert result["failed"] == result["unavailable"] == result["done"] == 0
    assert result["stopped"] == "interrupted" and result["left"] == 2
    assert not ledger.exists() or json.loads(ledger.read_text()) == []
    assert plants.pending_matches(db)[0].external_id == ids[0]


def test_archive_keeps_each_usable_payload(db, tmp_path):
    ids = matches(db, 2)
    archive = tmp_path / "archive"
    run(db, lambda page, key: payload(1) if key == ids[0] else {"no": "segments"}, archive=archive)
    assert sorted(path.name for path in archive.iterdir()) == [f"{ids[0]}.json.gz"]
    assert json.loads(gzip.decompress((archive / f"{ids[0]}.json.gz").read_bytes())) == payload(1)


def test_pause_file_round_trip():
    assert not plants.pause_requested()
    plants.request_pause()
    assert plants.pause_requested()
    assert plants.clear_pause() is True
    assert plants.clear_pause() is False and not plants.pause_requested()


def test_cli_pause_command_needs_no_database(monkeypatch, capsys):
    from scripts import round_plants as cli

    monkeypatch.setattr(cli, "SessionLocal", lambda: pytest.fail("pause opened the database"))
    assert cli.main(["pause"]) == 0
    assert plants.pause_requested() and "pause requested" in capsys.readouterr().out


def test_cli_clears_a_leftover_pause_and_a_paused_run_exits_zero(db, monkeypatch, capsys, tmp_path):
    from scripts import round_plants as cli

    ids = matches(db, 3)
    plants.request_pause()

    @contextmanager
    def fake_page():
        yield object()

    def fetch(page, key):
        plants.request_pause()  # the owner pauses while the first match loads
        return payload(1)

    monkeypatch.setattr(cli, "SessionLocal", lambda: db)
    monkeypatch.setattr(cli, "tracker_page", fake_page)
    monkeypatch.setattr(cli, "fetch_match_json", fetch)
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    assert cli.main(["backfill", "--archive", str(tmp_path / "a b")]) == 0
    out = capsys.readouterr().out
    assert "removed a leftover pause file" in out and "stopped: paused" in out
    assert f'--archive "{tmp_path / "a b"}"' in next(line for line in out.splitlines() if line.startswith("Resume:"))
    assert "done: 1; left: 2" in out
    assert not plants.pause_requested()
    assert (tmp_path / "a b" / f"{ids[0]}.json.gz").exists()


def test_resume_names_the_interpreter_with_friends_db_uses(monkeypatch, tmp_path):
    from scripts import round_plants as cli
    from scripts import with_friends_db

    main = tmp_path / "repo" / "webapp"
    worktree = tmp_path / "repo" / ".claude" / "worktrees" / "w" / "webapp"
    worktree.mkdir(parents=True)
    (main / ".venv313" / "Scripts").mkdir(parents=True)
    (main / ".venv313" / "Scripts" / "python.exe").write_text("")
    monkeypatch.setattr(cli, "WEBAPP", worktree)
    assert cli.resume_python() == r"..\..\..\..\webapp\.venv313\Scripts\python.exe"
    monkeypatch.setattr(cli, "WEBAPP", main)
    assert cli.resume_python() == r".\.venv313\Scripts\python.exe"
    assert with_friends_db.find_ingest_python  # the same lookup the wrapper runs with


def test_a_429_waits_and_retries_the_same_match(db, monkeypatch, capsys):
    from app.adapters.tracker_verification import TrackerRateLimited

    monkeypatch.setattr(plants, "RATE_LIMIT_ATTENTION_S", 3); monkeypatch.setattr(plants, "RATE_LIMIT_RETRY_S", 3); monkeypatch.setattr(plants, "RATE_LIMIT_COOLDOWNS_S", (4,))
    ids = matches(db, 2)
    fetched, slept = [], []

    def fetch(page, key):
        fetched.append(key)
        if len(fetched) <= 2:
            raise TrackerRateLimited("status 429")
        return payload(1)

    result = plants.backfill(db, None, limit=None, fetch=fetch, sleep=slept.append,
                             uniform=lambda a, b: 5, alert=lambda: None)
    assert fetched == [ids[0], ids[0], ids[0], ids[1]]
    assert result["done"] == 2 and result["failed"] == 0 and result["left"] == 0
    assert slept == [1] * 3 + [1] * 4 + [5]
    out = capsys.readouterr().out; assert "human check" in out and "still 429: waiting" in out


def test_a_429_after_every_wait_is_one_failure_not_skip_listed(db, ledger, monkeypatch):
    from app.adapters.tracker_verification import TrackerRateLimited

    monkeypatch.setattr(plants, "RATE_LIMIT_ATTENTION_S", 2); monkeypatch.setattr(plants, "RATE_LIMIT_RETRY_S", 2); monkeypatch.setattr(plants, "RATE_LIMIT_COOLDOWNS_S", (2,))
    ids = matches(db, 1)
    fetched = []

    def fetch(page, key):
        fetched.append(key)
        raise TrackerRateLimited("status 429")

    result = run(db, fetch)
    assert fetched == ids * 3
    assert (result["failed"], result["unavailable"], result["left"]) == (1, 0, 1)
    assert json.loads(ledger.read_text()) == []


def test_a_stop_during_the_wait_pauses_without_a_failure(db, monkeypatch):
    from app.adapters.tracker_verification import TrackerRateLimited

    monkeypatch.setattr(plants, "RATE_LIMIT_ATTENTION_S", 100); monkeypatch.setattr(plants, "RATE_LIMIT_RETRY_S", 100)
    matches(db, 2)
    fetched, slept = [], []

    def fetch(page, key):
        fetched.append(key)
        raise TrackerRateLimited("status 429")

    result = plants.backfill(db, None, limit=None, fetch=fetch, sleep=slept.append,
                             uniform=lambda a, b: 5, alert=lambda: None,
                             should_stop=lambda: "paused" if len(slept) >= 10 else None)
    assert len(fetched) == 1 and len(slept) == 10
    assert result["stopped"] == "paused" and result["failed"] == 0 and result["left"] == 2


def facing_summary(view=0.0, who="planter"):
    return {"type": "round-summary", "attributes": {"round": 1}, "metadata": {"plant": {
        "site": "B", "location": {"x": 1000, "y": 2000}, "platformUserIdentifier": "planter",
        "playerLocations": [{"platformUserIdentifier": "other", "location": {"x": 0, "y": 0}, "viewRadians": 3.0},
                            {"platformUserIdentifier": who, "location": {"x": 1000, "y": 2000},
                             "viewRadians": view}]}}}


@pytest.mark.parametrize("view,x,y", [(0.0, 1070, 2000), (1.5707963267948966, 1000, 2070)])
def test_spike_is_moved_in_front_of_the_planter(view, x, y):
    plant = plants.plant_from_summary(facing_summary(view))
    assert plant["corrected"] is True
    assert plant["x"] == pytest.approx(x) and plant["y"] == pytest.approx(y)


@pytest.mark.parametrize("summary", [facing_summary(who="someone else"), facing_summary(view=None),
                                     facing_summary(view=float("nan"))])
def test_a_plant_without_the_planters_facing_keeps_tracker_position(summary):
    assert plants.plant_from_summary(summary) == {"site": "B", "x": 1000, "y": 2000}


def test_rows_saved_before_the_fix_are_refetched_once_and_updated(db, ledger):
    ids = matches(db, 2)
    match = db.scalar(select(Match).where(Match.external_id == ids[1]))
    plants.upsert_plants(db, match, [dict(round_number=1, u=1, v=2, site="B")], source="tracker")
    db.commit()
    assert plants.crawl_queue(db) == [ids[1], ids[0]]
    fetched = []
    result = run(db, lambda page, key: (fetched.append(key), {"segments": [facing_summary()]})[1])
    assert fetched == [ids[1], ids[0]]
    assert result["updated"] == 1 and result["inserted"] == 1 and result["no_facing"] == 0 and result["left"] == 0
    row = db.scalar(select(RoundPlant).where(RoundPlant.match_id == match.id))
    assert (row.u, row.v) != (1, 2)
    assert plants.crawl_queue(db) == []


def test_a_stopped_fix_resumes_after_the_last_saved_match(db, ledger):
    ids = matches(db, 3)
    for key in ids:
        match = db.scalar(select(Match).where(Match.external_id == key))
        plants.upsert_plants(db, match, [dict(round_number=1, u=1, v=2, site="B")], source="tracker")
    db.commit()
    requested = []
    run(db, lambda page, key: (requested.append(key), {"segments": [facing_summary()]})[1],
        should_stop=lambda: "paused" if requested else None)
    assert requested == ids[:1] and plants.crawl_queue(db) == ids[1:]


def test_replay_position_is_never_replaced_by_the_refetch(db, ledger):
    match = planted_match(db, external_id="synthetic-replay")
    plants.upsert_plants(db, match, [dict(round_number=1, u=1234, v=5678, site="A")], source="replay")
    db.commit()
    assert plants.crawl_queue(db) == []


def test_each_match_prints_one_running_line(db, ledger, capsys):
    ids = matches(db, 3)

    def fetch(page, key):
        if key == ids[1]:
            raise plants.TrackerMatchUnavailable("gone")
        return payload(1)

    run(db, fetch)
    lines = [line for line in capsys.readouterr().out.splitlines() if "/3]" in line]
    assert len(lines) == 3
    assert "[1/3]" in lines[0] and "+1 new, 0 fixed" in lines[0] and "(this run: 1)" in lines[0]
    assert "[2/3]" in lines[1] and "unavailable, skip-listed" in lines[1]
    assert "[3/3]" in lines[2] and "(this run: 2)" in lines[2]


def test_owner_stop_first_ctrl_c_asks_second_interrupts(capsys):
    import signal
    from scripts import round_plants as cli

    before = signal.getsignal(signal.SIGINT)
    with cli.owner_stop() as should_stop:
        handler = signal.getsignal(signal.SIGINT)
        assert should_stop() is None
        plants.request_pause()
        assert should_stop() == "paused"
        plants.clear_pause()
        assert should_stop() is None
        handler(signal.SIGINT, None)
        assert "PAUSING" in capsys.readouterr().out
        assert should_stop() == "paused"
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGINT, None)
    assert signal.getsignal(signal.SIGINT) is before
