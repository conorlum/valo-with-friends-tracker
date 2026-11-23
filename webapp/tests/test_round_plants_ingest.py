"""Tracker plant storage is best effort after the match transaction commits."""
import pytest
from sqlalchemy import event

from app.adapters.trackergg_browserstate_source import load_match
from app.models import Match, Round, RoundPlant, RoundPlayerStat
from app.services import round_plants
from tests.buy_disruption_fixtures import session
from tests.test_round_player_spend_ingest import IDS, _payload


def _planted_payload():
    payload = _payload(match_id="m-plants")
    summary = next(s for s in payload["segments"] if s["type"] == "round-summary")
    summary["metadata"]["plant"] = {
        "roundTime": 40000, "site": "A", "location": {"x": 1000, "y": 2000},
    }
    # A pre-plant defender death and a post-plant attacker death distinguish
    # the last pre-plant state from both the starting and final alive counts.
    for killer, victim, time in ((0, 5, 10000), (6, 1, 50000)):
        payload["segments"].append({
            "type": "player-round-kills",
            "attributes": {"round": 1, "platformUserIdentifier": IDS[killer],
                           "opponentPlatformUserIdentifier": IDS[victim]},
            "metadata": {"roundTime": time, "weaponName": "Classic"},
        })
    return payload, summary


@pytest.fixture
def db():
    db = session(all_tables=True)
    try:
        yield db
    finally:
        db.close()
        db.bind.dispose()


def test_planted_round_stores_position_site_alive_counts_and_outcome(db):
    payload, _ = _planted_payload()
    match = load_match(db, payload)
    plant = db.query(RoundPlant).one()
    assert plant.match_id == match.id and plant.round_number == 1
    assert (plant.u, plant.v, plant.site, plant.source) == (6949, 9086, "A", "tracker")
    assert (plant.attackers_alive, plant.defenders_alive, plant.attackers_won) == (5, 4, True)
    assert plant.plant_time == 40.0
    assert plant.defused is False
    # Reading through another session proves the plant transaction committed.
    with type(db)(bind=db.bind) as reader:
        assert reader.query(RoundPlant).count() == 1


@pytest.mark.parametrize("bad_location", [None, {}, {"x": 1}, {"x": "bad", "y": 2},
                                          {"x": float("nan"), "y": 2}])
def test_bad_or_missing_location_does_not_prevent_match_ingest(db, bad_location):
    payload, summary = _planted_payload()
    if bad_location is None:
        del summary["metadata"]["plant"]["location"]
    else:
        summary["metadata"]["plant"]["location"] = bad_location
    match = load_match(db, payload)
    assert match.external_id == "m-plants"
    assert db.query(RoundPlant).count() == 0
    assert db.query(Round).count() == 2
    assert db.query(RoundPlayerStat).count() == 20


def test_unknown_map_does_not_prevent_match_ingest(db, capsys):
    payload, _ = _planted_payload()
    payload["metadata"]["mapName"] = "Unknown map"
    match = load_match(db, payload)
    assert match.map_name == "Unknown map"
    assert db.query(RoundPlant).count() == 0
    assert db.query(RoundPlayerStat).count() == 20
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("failure", ["exception", "flush"])
def test_storage_failure_rolls_back_only_plants_and_leaves_match_usable(db, monkeypatch, capsys, failure):
    payload, _ = _planted_payload()
    original = round_plants.upsert_plants
    calls, rollbacks = [], []
    event.listen(db, "after_rollback", lambda session: rollbacks.append(True))

    def fail(db, match, plants, source):
        calls.append(True)
        original(db, match, plants, source)
        if failure == "flush":
            db.query(RoundPlant).one().site = None
            db.flush()  # A real integrity failure leaves the session needing rollback.
        raise RuntimeError("plant storage failed\nsecond line")

    monkeypatch.setattr(round_plants, "upsert_plants", fail)
    match = load_match(db, payload)
    assert calls == [True]
    assert rollbacks
    assert match.id is not None and match.external_id == "m-plants" and match.map_name == "Bind"
    assert db.query(Match).one() is match
    assert db.query(Round).count() == 2
    assert db.query(RoundPlayerStat).count() == 20
    assert db.query(RoundPlant).count() == 0
    assert len(capsys.readouterr().out.splitlines()) == 1


@pytest.mark.parametrize("rollback_raises", [False, True])
def test_failed_cleanup_returns_committed_match_without_lazy_reads(db, monkeypatch, capsys, rollback_raises):
    payload, _ = _planted_payload()
    payload["metadata"]["dateStarted"] = "2026-01-02T03:04:05"
    original_upsert = round_plants.upsert_plants
    original_refresh, original_rollback = db.refresh, db.rollback
    refresh_calls = []

    def fail_storage(db, match, plants, source):
        original_upsert(db, match, plants, source)
        raise RuntimeError("plant storage failed")

    def fail_refresh_once(*args, **kwargs):
        refresh_calls.append(True)
        if len(refresh_calls) == 1:
            raise RuntimeError("refresh connection unavailable")
        return original_refresh(*args, **kwargs)

    def fail_rollback():
        original_rollback()
        raise RuntimeError("rollback connection unavailable")

    monkeypatch.setattr(round_plants, "upsert_plants", fail_storage)
    monkeypatch.setattr(db, "refresh", fail_refresh_once)
    if rollback_raises:
        monkeypatch.setattr(db, "rollback", fail_rollback)

    match = load_match(db, payload)
    assert refresh_calls == [True]
    # These are the attributes used by the ingest ledger, scoring/cache steps,
    # and progress output. None may need a connection after refresh failed.
    statements = []

    def record_sql(*args):
        statements.append(True)

    event.listen(db.bind, "before_cursor_execute", record_sql)
    try:
        assert match.id is not None
        assert match.external_id == "m-plants"
        assert match.map_name == "Bind"
        assert match.played_at.isoformat() == "2026-01-02T03:04:05"
    finally:
        event.remove(db.bind, "before_cursor_execute", record_sql)
    assert statements == []
    assert db.query(Match).one() is match
    assert db.query(Round).count() == 2
    assert db.query(RoundPlayerStat).count() == 20
    assert db.query(RoundPlant).count() == 0
    output = capsys.readouterr().out.splitlines()
    assert len(output) == 1 and "plant storage failed" in output[0]


def test_duplicate_load_does_not_write_or_retry_plant_storage(db, monkeypatch):
    payload, _ = _planted_payload()
    match = load_match(db, payload)
    plant = db.query(RoundPlant).one()
    stamp = plant.updated_at

    def unexpected(*args, **kwargs):
        pytest.fail("duplicate ingest must not store plants again")

    monkeypatch.setattr(round_plants, "upsert_plants", unexpected)
    assert load_match(db, payload) is match
    assert db.query(Match).count() == 1
    assert db.query(Round).count() == 2
    assert db.query(RoundPlayerStat).count() == 20
    assert db.query(RoundPlant).one().updated_at == stamp


@pytest.mark.parametrize("round_result", ["Time", "Unrecognized"])
def test_phantom_or_excluded_round_keeps_position_without_outcome(db, round_result):
    payload, summary = _planted_payload()
    summary["stats"]["roundResult"]["value"] = round_result
    load_match(db, payload)
    plant = db.query(RoundPlant).one()
    assert (plant.u, plant.v, plant.site, plant.plant_time) == (6949, 9086, "A", 40.0)
    assert (plant.attackers_alive, plant.defenders_alive, plant.attackers_won) == (None, None, None)
