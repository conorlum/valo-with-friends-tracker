"""Stage 4 (docs/replay-viewer-plan.md, "Stage 4" gate): the alive-count badge, the annotations from
state_replay, post-decision marks, and reingest_replays.py's write mode. sqlite; the round-Impact
check against get_round_detail runs on the real matches in the Stage 2 gate DB (the run's
stage4 check), since impact_scores needs PostgreSQL."""

import copy
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

from replay_synthetic import MATCH_UUID, SyntheticMatch  # noqa: E402
from test_ingest_replay import LINK_KILLS, ROUNDS, WINNERS  # noqa: E402
from test_replay_store import TABLES, add_match  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import KillEvent, MatchPlayer, Round  # noqa: E402
from app.models.match import Team  # noqa: E402
from app.models.replay import Replay  # noqa: E402
from app.replays import condense as cd  # noqa: E402
from app.replays.contract import load_pin  # noqa: E402
from app.replays import store  # noqa: E402
from app.services import replay_view  # noqa: E402
from app.services.state_replay import KillEventInput, ReplayDiagnostics, RoundInput, replay_round  # noqa: E402

SLOT_TEAM = {s: ("team-1" if s < 5 else "team-2") for s in range(10)}


def blob(alive, t_decided=60.0, t_end=67.0):
    full = {str(s): [[0.0, None, "round_end"]] for s in range(10)}
    full.update({str(s): iv for s, iv in alive.items()})
    return {"t_start": 0.0, "t_decided": t_decided, "t_end": t_end, "alive": full}


# ---------------------------------------------------------------- the badge


def test_the_badge_starts_five_v_five_and_follows_kills():
    steps = replay_view.alive_steps(blob({0: [[0.0, 10.0, "kill"]], 5: [[0.0, 20.0, "kill"]]}), SLOT_TEAM, [], 0.0)
    assert steps == [[0.0, 5, 5], [10.0, 4, 5], [20.0, 4, 4]]


def test_a_revive_brings_a_player_back():
    steps = replay_view.alive_steps(blob({7: [[0.0, 12.0, "kill"], [18.0, None, "round_end"]]}), SLOT_TEAM, [], 0.0)
    assert steps == [[0.0, 5, 5], [12.0, 5, 4], [18.0, 5, 5]]


def test_a_db_only_death_counts_once_on_the_replay_clock():
    # The spike kills slot 3 at DB time 61.4 with a +0.4 s offset; the replay shows no death for it.
    steps = replay_view.alive_steps(blob({}), SLOT_TEAM, [{"slot": 3, "t_db": 61.4}], 0.4)
    assert steps == [[0.0, 5, 5], [61.0, 4, 5]]
    # When the replay also saw that death (a replay self-kill), it isn't counted twice.
    seen = blob({3: [[0.0, 61.3, "kill"]]})
    assert replay_view.alive_steps(seen, SLOT_TEAM, [{"slot": 3, "t_db": 61.4}], 0.4) == [[0.0, 5, 5], [61.3, 4, 5]]


def test_an_equal_time_double_kill_is_one_step():
    steps = replay_view.alive_steps(blob({1: [[0.0, 15.0, "kill"]], 2: [[0.0, 15.0, "kill"]]}), SLOT_TEAM, [], 0.0)
    assert steps == [[0.0, 5, 5], [15.0, 3, 5]]


def test_a_post_decision_kill_still_moves_the_badge():
    steps = replay_view.alive_steps(blob({9: [[0.0, 63.0, "kill"]]}, t_decided=60.0), SLOT_TEAM, [], 0.0)
    assert steps[-1] == [63.0, 5, 4] and steps[-1][0] > 60.0


def test_a_plant_or_defuse_with_no_kill_between_changes_nothing():
    assert replay_view.alive_steps(blob({}), SLOT_TEAM, [], 0.0) == [[0.0, 5, 5]]


# ---------------------------------------------------------------- annotations


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=TABLES)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_annotations_equal_state_replay_for_every_included_round(db):
    match, _ = add_match(db)
    offset = 0.4
    notes = replay_view.round_annotations(db, match.id, offset)
    players = db.query(MatchPlayer).filter(MatchPlayer.match_id == match.id).all()
    team1 = frozenset(p.id for p in players if p.team == Team.TEAM_1)
    team2 = frozenset(p.id for p in players if p.team == Team.TEAM_2)
    assert sorted(notes) == list(range(1, ROUNDS + 1))
    for r in db.query(Round).filter(Round.match_id == match.id):
        kills = db.query(KillEvent).filter(KillEvent.round_id == r.id).all()
        expected = replay_round(match.id, RoundInput(
            r.id, r.round_number, r.outcome, r.planted, r.plant_time, r.exploded, r.defused, r.defuse_time,
            tuple(KillEventInput(k.id, k.killer_match_player_id, k.death_match_player_id, k.event_time_seconds)
                  for k in kills)), team1, team2, ReplayDiagnostics())
        got = notes[r.round_number]
        assert got["excluded"] == expected.exclusion_reason
        assert [s["alive"] for s in got["states"]] == [[len(e.team1_alive_ids), len(e.team2_alive_ids)]
                                                       for e in expected.entries]
        assert [s["t"] for s in got["states"][1:]] == [round(e.event_time_seconds - offset, 3)
                                                      for e in expected.entries[1:]]


def test_an_excluded_round_shows_its_reason(db):
    match, _ = add_match(db)
    first = db.query(Round).filter(Round.match_id == match.id, Round.round_number == 1).one()
    kills = db.query(KillEvent).filter(KillEvent.round_id == first.id).order_by(KillEvent.id).all()
    kills[1].event_time_seconds = kills[0].event_time_seconds  # two kills at one moment
    db.commit()
    notes = replay_view.round_annotations(db, match.id, 0.4)
    assert notes[1] == {"states": [], "ending": None, "excluded": "equal_time_ambiguity"}
    # The synthetic script's rounds 2-4 already have a dead player killing again (no revive
    # source): state_replay excludes those as ambiguous; round 5 repeats round 1's clean script.
    assert notes[2]["excluded"] == "ambiguous_lifecycle" and notes[5]["excluded"] is None


def test_a_defuse_ending_sits_at_its_own_db_time(db):
    match, _ = add_match(db)
    r = db.query(Round).filter(Round.match_id == match.id, Round.round_number == 1).one()
    r.outcome, r.planted, r.plant_time, r.defused, r.defuse_time = "Team B Defuse Win", True, 20.0, True, 55.5
    db.commit()
    notes = replay_view.round_annotations(db, match.id, 0.5)
    assert notes[1]["ending"] == {"cause": "defuse", "t": 55.0}


# ---------------------------------------------------------------- reingest write mode


def test_reingest_restores_a_stale_local_replay_from_its_export(db, tmp_path, monkeypatch):
    import reingest_replays

    match = SyntheticMatch(rounds=ROUNDS, kills=copy.deepcopy(LINK_KILLS), winners=dict(WINNERS))
    root = tmp_path / "exports"
    export = match.write(root / MATCH_UUID)
    archive = tmp_path / "archive"
    archive.mkdir()
    match.write_vrf(archive / f"{MATCH_UUID}.vrf")
    parser_dir = tmp_path / "parser"
    (parser_dir / "bin").mkdir(parents=True)
    pin = load_pin()
    (parser_dir / "bin" / "BUILD.json").write_text(json.dumps({"commit": pin.commit, "patch_hash": pin.patch_hash}))
    add_match(db)
    current = cd.condense_export_dir(export, source_sha256=match.source_sha256)
    store.store_replay(db, replace(current, recipe="old-recipe"), source="local")
    monkeypatch.setattr("app.services.replay_impact.refresh_replay_impact", lambda f, i: "skipped in tests")
    factory = lambda: db  # noqa: E731
    db.close = lambda: None
    code = reingest_replays.main(["--archive", str(archive), "--export-root", str(root), "--parser-dir",
                                  str(parser_dir)], session_factory=factory)
    assert code == 0
    row = db.query(Replay).one()
    assert row.recipe == current.recipe and row.link_status == "linked"
    assert reingest_replays.plan(db, current.recipe, archive) == []
