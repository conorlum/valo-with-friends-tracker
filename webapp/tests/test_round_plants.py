"""Plant positions and outcomes use the same coordinates and states as replays."""
import json
import zlib

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from app.models import Match, PlantStatsCache, Round, RoundPlant
from app.models.match import MatchSource
from app.replays import layouts
from app.replays.condense import load_maps
from app.scoring.plant_window import attacking_team
from app.models.match import Team
from app.services import round_plants as plants
from tests.buy_disruption_fixtures import build_match, session


@pytest.fixture
def db():
    db = session(all_tables=True)
    try:
        yield db
    finally:
        db.close()
        db.bind.dispose()


def planted_match(db, *, upto=1, kills=None, outcomes=None, external_id="plant-test"):
    match, _, _ = build_match(db, external_id, upto=upto, kills=kills,
                             outcomes=outcomes, planted={n: True for n in range(1, upto + 1)})
    for row in db.scalars(select(Round).where(Round.match_id == match.id)):
        row.plant_time = 15.0
    db.flush()
    return match


def position(number=1, **overrides):
    return {"round_number": number, "site": "A", "u": 1234, "v": 5678, **overrides}


@pytest.mark.parametrize("map_name", ["Ascent", "Bind", "Haven"])
@pytest.mark.parametrize("point", [(0, 0), (1234.5, -678.9), (-50000, 50000)])
def test_to_uv_matches_replay(map_name, point):
    assert plants.to_uv(map_name, *point) == load_maps()[map_name].to_uv(*point)


def test_summary_plant():
    row = {"metadata": {"plant": {"site": "B", "location": {"x": 123.5, "y": -456}}}}
    assert plants.plant_from_summary(row) == {"site": "B", "x": 123.5, "y": -456}


@pytest.mark.parametrize("row", [
    None, [], "bad", {}, {"metadata": None}, {"metadata": []},
    {"metadata": {"plant": None}}, {"metadata": {"plant": "bad"}},
    {"metadata": {"plant": {"site": "A"}}},
    {"metadata": {"plant": {"site": "A", "location": []}}},
    {"metadata": {"plant": {"site": "A", "location": {"x": "1", "y": 2}}}},
    {"metadata": {"plant": {"site": "D", "location": {"x": 1, "y": 2}}}},
    {"metadata": {"plant": {"site": [], "location": {"x": 1, "y": 2}}}},
    {"metadata": {"plant": {"site": "A", "location": {"x": True, "y": 2}}}},
    {"metadata": {"plant": {"site": "A", "location": {"x": float("nan"), "y": 2}}}},
    {"metadata": {"plant": {"site": "A", "location": {"x": 1, "y": float("inf")}}}},
])
def test_bad_summary_is_ignored(row):
    assert plants.plant_from_summary(row) is None


@pytest.mark.parametrize("victim,expected", [("A1", (4, 5)), ("B1", (5, 4))])
def test_only_kills_strictly_before_plant_count(db, victim, expected):
    killer = "B5" if victim.startswith("A") else "A5"
    match = planted_match(db, kills={1: [(killer, victim, 10), ("A5", "B2", 15),
                                        ("B5", "A2", 20)]})
    result = plants.outcomes_for_match(db, match.id)[1]
    assert (result["attackers_alive"], result["defenders_alive"]) == expected
    assert result["plant_time"] == 15
    assert result["attackers_won"] is True
    assert result["defused"] is False


@pytest.mark.parametrize("outcome,kills", [
    ("Team A Surrendered Win", []),
    ("Team A Elimination Win", [("A1", "B1", 10), ("A2", "B1", 20)]),
    ("Team B Time Win", []),
    ("Unknown", []),
])
def test_excluded_or_phantom_round_keeps_position_without_rates(db, outcome, kills):
    match = planted_match(db, outcomes={1: outcome}, kills={1: kills})
    result = plants.outcomes_for_match(db, match.id)[1]
    assert result == dict(attackers_alive=None, defenders_alive=None, attackers_won=None,
                          plant_time=15, defused=False)
    plants.upsert_plants(db, match, [position()], "tracker")
    row = db.scalars(select(RoundPlant)).one()
    assert (row.u, row.v) == (1234, 5678)
    assert (row.attackers_alive, row.defenders_alive, row.attackers_won) == (None, None, None)


@pytest.mark.parametrize("number", [1, 13, 25, 26])
@pytest.mark.parametrize("winner", [Team.TEAM_1, Team.TEAM_2])
def test_attackers_won_follows_regulation_and_overtime(db, number, winner):
    outcome = "Team A Elimination Win" if winner == Team.TEAM_1 else "Team B Elimination Win"
    match = planted_match(db, upto=number, outcomes={number: outcome})
    assert plants.outcomes_for_match(db, match.id)[number]["attackers_won"] == (
        winner == attacking_team(number))


def test_outcomes_only_reads_requested_match(db):
    match = planted_match(db)
    planted_match(db, external_id="other-test", outcomes={1: "Team B Time Win"})
    result = plants.outcomes_for_match(db, match.id)
    assert list(result) == [1]
    assert (result[1]["attackers_alive"], result[1]["defenders_alive"]) == (5, 5)
    assert result[1]["attackers_won"] is True
    assert plants.outcomes_for_match(db, -1) == {}


def test_site_ignores_closer_lobby():
    # At Abyss A Lobby itself, only the named sites are candidates.
    assert plants.site_for_uv("Abyss", 7754, 2367) == "A"
    assert plants.site_for_uv("missing-map", 0, 0) is None


@pytest.mark.parametrize("bad", [position(2), position(u=None), position(v=None),
                                 position(u=1.5), position(v="2"), position(u=True)])
def test_invalid_position_is_skipped(db, bad):
    match = planted_match(db)
    summary = plants.upsert_plants(db, match, [bad], "tracker")
    assert summary["skipped"] == 1
    assert list(db.scalars(select(RoundPlant))) == []


def test_replay_position_wins_and_tracker_site_survives(db):
    match = planted_match(db)
    assert plants.upsert_plants(db, match, [position(site="B")], "tracker")["inserted"] == 1
    plants.upsert_plants(db, match, [position(site="A", u=3000, v=4000)], "replay")
    row = db.scalars(select(RoundPlant)).one()
    assert (row.source, row.site, row.u, row.v) == ("replay", "B", 3000, 4000)
    plants.upsert_plants(db, match, [position(site="A", u=3001, v=4001)], "replay")
    assert (row.source, row.site, row.u, row.v) == ("replay", "B", 3001, 4001)
    plants.upsert_plants(db, match, [position(u=1, v=2)], "tracker")
    assert (row.source, row.site, row.u, row.v) == ("replay", "B", 3001, 4001)


def test_replay_then_tracker_preserves_all_replay_fields(db):
    match = planted_match(db)
    plants.upsert_plants(db, match, [position()], "replay")
    row = db.scalars(select(RoundPlant)).one()
    updated_at = row.updated_at
    plants.upsert_plants(db, match, [position(site="B", u=1, v=2)], "tracker")
    assert (row.source, row.site, row.u, row.v) == ("replay", "A", 1234, 5678)
    assert row.updated_at == updated_at


def test_tracker_upsert_is_idempotent_and_does_not_commit(db):
    match = planted_match(db)
    plants.upsert_plants(db, match, [position(), position()], "tracker")
    plants.upsert_plants(db, match, [position()], "tracker")
    assert len(list(db.scalars(select(RoundPlant)))) == 1
    db.rollback()
    assert list(db.scalars(select(RoundPlant))) == []


def test_stored_plant_time_has_priority(db):
    match = planted_match(db)
    plants.upsert_plants(db, match, [position(plant_time=999)], "replay")
    assert db.scalars(select(RoundPlant)).one().plant_time == 15


@pytest.mark.parametrize("offset,expected", [(3, 15), (None, 12), (-3, 9)])
def test_replay_time_fallback_is_on_database_clock(db, offset, expected):
    match = planted_match(db)
    db.scalars(select(Round)).one().plant_time = None
    plants.upsert_plants(db, match, [position(t=12, clock_offset=offset)], "replay")
    row = db.scalars(select(RoundPlant)).one()
    assert row.plant_time == expected
    assert (row.attackers_alive, row.defenders_alive, row.attackers_won) == (5, 5, True)


@pytest.mark.parametrize("supplied", [{}, {"plant_time": float("nan")}, {"t": "bad"},
                                    {"t": 12, "clock_offset": float("inf")}])
def test_missing_usable_plant_time_keeps_position_without_rates(db, supplied):
    match = planted_match(db, kills={1: [("A5", "B1", 10), ("B5", "A1", 20)]})
    rnd = db.scalars(select(Round)).one()
    rnd.plant_time = None
    result = plants.outcomes_for_match(db, match.id)[1]
    assert result == dict(attackers_alive=None, defenders_alive=None, attackers_won=None,
                          plant_time=None, defused=False)
    plants.upsert_plants(db, match, [position(**supplied)], "replay")
    row = db.scalars(select(RoundPlant)).one()
    assert (row.u, row.v) == (1234, 5678)
    assert (row.plant_time, row.attackers_alive, row.defenders_alive, row.attackers_won) == (
        None, None, None, None)


@pytest.mark.parametrize("supplied", [{"t": 12, "clock_offset": 3}, {"plant_time": 15}])
def test_replay_fallback_time_cuts_alive_counts_on_database_clock(db, supplied):
    match = planted_match(db, kills={1: [("A5", "B1", 10), ("B5", "A1", 15),
                                        ("B5", "A2", 20)]})
    rnd = db.scalars(select(Round)).one()
    rnd.plant_time = None
    # Exercise a one-pass iterable as well as the plant-time fallback.
    plants.upsert_plants(db, match, iter([position(**supplied)]), "replay")
    row = db.scalars(select(RoundPlant)).one()
    assert (row.plant_time, row.attackers_alive, row.defenders_alive, row.attackers_won) == (
        15, 5, 4, True)
    db.refresh(rnd)
    assert rnd.plant_time is None


def test_supplied_time_is_used_by_outcomes_without_changing_round(db):
    match = planted_match(db, kills={1: [("A5", "B1", 10), ("B5", "A1", 20)]})
    rnd = db.scalars(select(Round)).one()
    rnd.plant_time = None
    result = plants.outcomes_for_match(db, match.id, plant_times={1: 15})[1]
    assert result == dict(attackers_alive=5, defenders_alive=4, attackers_won=True,
                          plant_time=15, defused=False)
    db.refresh(rnd)
    assert rnd.plant_time is None


def test_stored_time_controls_alive_cut_even_with_supplied_time(db):
    match = planted_match(db, kills={1: [("A5", "B1", 10), ("B5", "A1", 20)]})
    result = plants.outcomes_for_match(db, match.id, plant_times={1: 25})[1]
    assert (result["plant_time"], result["attackers_alive"], result["defenders_alive"]) == (15, 5, 4)
    plants.upsert_plants(db, match, [position(t=22, clock_offset=3)], "replay")
    row = db.scalars(select(RoundPlant)).one()
    assert (row.plant_time, row.attackers_alive, row.defenders_alive) == (15, 5, 4)


def test_defuse_outcome(db):
    match = planted_match(db, outcomes={1: "Team B Defuse Win"})
    rnd = db.scalars(select(Round)).one()
    rnd.defused, rnd.defuse_time = True, 30
    result = plants.outcomes_for_match(db, match.id)[1]
    assert result == dict(attackers_alive=5, defenders_alive=5, attackers_won=False,
                          plant_time=15, defused=True)


def test_delete_match_cascades_with_foreign_keys(db):
    db.execute(text("PRAGMA foreign_keys=ON"))
    match = Match(external_id="cascade-test", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    rnd = Round(match_id=match.id, round_number=1)
    db.add(rnd)
    db.flush()
    plants.upsert_plants(db, match, [position()], "tracker")
    db.commit()
    db.delete(match)
    db.commit()
    assert list(db.scalars(select(RoundPlant))) == []
    fks = RoundPlant.__table__.foreign_keys
    assert {fk.target_fullname: fk.ondelete for fk in fks} == {
        "rounds.id": "CASCADE", "matches.id": "CASCADE"}


def test_delete_round_cascades(db):
    db.execute(text("PRAGMA foreign_keys=ON"))
    match = Match(external_id="round-cascade-test", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    db.add(Round(match_id=match.id, round_number=1))
    db.flush()
    plants.upsert_plants(db, match, [position()], "tracker")
    db.execute(delete(Round).where(Round.match_id == match.id))
    db.flush()
    assert list(db.scalars(select(RoundPlant))) == []


def test_source_constraint_and_compressed_cache(db):
    match = planted_match(db)
    plants.upsert_plants(db, match, [position()], "tracker")
    row = db.scalars(select(RoundPlant)).one()
    assert row.updated_at is not None
    row.source = "bad"
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    payload = {"plants": 1}
    db.add(PlantStatsCache(asset_key="Bind", population="all", fingerprint="f" * 64,
                           data=zlib.compress(json.dumps(payload).encode())))
    db.flush()
    cache = db.get(PlantStatsCache, ("Bind", "all"))
    assert json.loads(zlib.decompress(cache.data)) == payload
    assert cache.computed_at is not None


def test_layout_selection_uses_branch_when_map_has_multiple_layouts(monkeypatch):
    registry = {"maps": {"Bind": [
        {"id": "Bind@base", "key": "Bind", "start": None, "drawn_from": None, "regions": None},
        {"id": "Bind@14.01", "key": "Bind_14_1", "start": "14.01", "drawn_from": None,
         "regions": None},
    ]}}
    assert plants.asset_key_for("Bind", None) == "Bind"
    monkeypatch.setattr(layouts, "load_registry", lambda: registry)
    # A position stored as Bind before this registry change must be reselected on read.
    assert plants.asset_key_for("Bind", None) is None
    assert plants.asset_key_for("Bind", "bad") is None
    assert plants.asset_key_for("Bind", "release-13.08") == "Bind"
    assert plants.asset_key_for("Bind", "release-14.01") == "Bind_14_1"
