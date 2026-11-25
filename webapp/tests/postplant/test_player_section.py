"""The player page's post-plant fragment, on SQLite with seeded rows (made-up names).

The route is called directly with a hand-built Request, like test_page.py. Post-plant rows are linked
to players through `ReplayPlayer.match_player_id`; the gap run's "current" test is patched to a
per-round fingerprint the test controls.
"""

import re
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.config import settings
from app.coach import areas
from app.db import Base
from app.models import (
    AccountGroup, AccountGroupMember, Friendship, ImpactScore, Match, MatchPlayer, Player, Round, RoundPlant,
)
from app.models.match import MatchSource, Team
from app.models.replay import Replay, ReplayPlayer, UtilModel
from app.models.replay_postplant import PostplantStatsCache, ReplayRoundPostplant
from app.models.round_plant import PlantStatsCache
from app.routers import players as players_router
from app.services import plant_spots, postplant_page, postplant_stats, replay_gaps, util_model
from app.services.auth import get_current_player
from app.templates import templates

@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):   # impact_scores.trade_detail
    return "JSON"


TABLES = [Player, Match, MatchPlayer, Round, RoundPlant, PlantStatsCache, Replay, ReplayPlayer,
          ReplayRoundPostplant, PostplantStatsCache, UtilModel, Friendship, AccountGroup,
          AccountGroupMember, ImpactScore]
SPOTS = {"Ascent": {"A": [{"id": "s1", "name": "Default plant", "u": 3500, "v": 1400, "r": 300}]}}
NAMES = {100: "Heaven", 200: "Rafters", 300: "Window"}
FP = {}
COUNTER = [0]


@pytest.fixture
def db(tmp_path, monkeypatch):
    eng = create_engine("sqlite:///" + str(tmp_path / "pps.sqlite"))
    Base.metadata.create_all(eng, tables=[t.__table__ for t in TABLES])
    FP.clear()
    COUNTER[0] = 0
    with sessionmaker(bind=eng)() as session:
        monkeypatch.setattr(templates, "context_processors",
                            [lambda request: {"current_player": get_current_player(request, session)}])
        monkeypatch.setattr(plant_spots, "load", lambda: SPOTS)
        monkeypatch.setattr(plant_spots, "digest", lambda: "digest-1")
        monkeypatch.setattr(areas, "load_painted", lambda key, *a, **k: None)
        monkeypatch.setattr(areas, "_table", lambda: {"Ascent": [{"name": "Heaven", "u": 3000, "v": 2000,
                                                                  "super": "A"}]})
        monkeypatch.setattr(areas, "area_at", lambda key, u, v, z, *, painted_table:
                            {"name": NAMES[u]} if u in NAMES else None)
        monkeypatch.setattr(postplant_stats.util_values, "swings", lambda data, model:
                            {(round(k["t"], 2), k["killer"], k["victim"]): 0.10 for k in data["kills"]})
        monkeypatch.setattr(replay_gaps.Freshness, "expected", lambda self, replay, n: FP.get((replay.id, n)))
        session.merge(UtilModel(name="win", version=util_model.VERSION, key="model-1", data={"key": "model-1"}))
        session.commit()
        yield session
    eng.dispose()


def _player(db, name):
    player = Player(display_name=name)
    db.add(player)
    db.flush()
    return player


def _rows(slot_samples):
    return [[10, slot, u, 100, 20, 1] for slot, us in slot_samples.items() for u in us]


def seed(db, players, *, agents=None, samples=None, kills=(), side_of=None):
    """One replay, one round, a plant on A. `players` maps slot -> Player; `agents` slot -> agent (Jett
    default); `samples` slot -> [u, ...] (default: every linked slot in Heaven)."""
    COUNTER[0] += 1
    i = COUNTER[0]
    agents = agents or {}
    match = Match(external_id=f"M-{i}", source=MatchSource.SCRAPED, map_name="Ascent",
                  played_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    db.add(match)
    db.flush()
    replay = Replay(match_uuid=f"00000000-0000-0000-0000-{i:012d}", match_id=match.id, map_name="Ascent",
                    round_count=1, format_version=1, recipe="r", game_branch="release-13.08",
                    source_sha256="x", source="upload", link_status="linked", link_inputs={}, clock_offset=2.0,
                    linked_at=datetime(2026, 10, 2, tzinfo=timezone.utc))
    db.add(replay)
    db.flush()
    for slot in range(10):
        mp_id = None
        if slot in players:
            mp = MatchPlayer(match_id=match.id, player_id=players[slot].id, agent=agents.get(slot, "Jett"),
                             team=Team.TEAM_1 if slot < 5 else Team.TEAM_2)
            db.add(mp)
            db.flush()
            mp_id = mp.id
        db.add(ReplayPlayer(replay_id=replay.id, slot=slot, agent="Jett", match_player_id=mp_id))
    FP[(replay.id, 1)] = f"fp-{replay.id}"
    samples = samples if samples is not None else {slot: [100] for slot in players}
    data = {"v": 1, "plant": {"t": 10, "u": 3500, "v": 1400, "by": 0}, "end": 12, "step": 0.5,
            "sides": {str(s): "attack" if s < 5 else "defense" for s in range(10)}, "sides_known": True,
            "samples": _rows(samples), "kills": list(kills)}
    db.add(ReplayRoundPostplant(replay_id=replay.id, round_number=1, status="ok",
                                fingerprint=f"fp-{replay.id}", gaps_revision=replay_gaps.GAPS_REVISION,
                                data=data, computed_at=datetime(2026, 10, 3, tzinfo=timezone.utc)))
    db.commit()


def kill(ku=100, vu=200):
    return {"t": 10.2, "killer": 0, "victim": 5, "k_side": "attack", "v_side": "defense", "before": [5, 5],
            "ku": ku, "kv": 100, "kz": 20, "vu": vu, "vv": 100, "vz": 30, "k_line": 1}


def _fragment(db, name, query=b"", **params):
    request = Request({"type": "http", "method": "GET", "path": f"/players/{name}/postplant",
                       "query_string": query, "headers": [], "scheme": "http", "server": ("testserver", 80),
                       "session": {}})
    return players_router.player_postplant_fragment(request, name, db=db, **params).body.decode()


def _group(db, main, *subs):
    group = AccountGroup(main_player_id=main.id)
    db.add(group)
    db.flush()
    for p in (main, *subs):
        db.add(AccountGroupMember(player_id=p.id, group_id=group.id))
    db.commit()


# --- eligibility (D6) -----------------------------------------------------------


def test_twenty_hold_post_plants_show_a_block_and_the_sentence(db):
    me, other = _player(db, "Zorblax#1"), _player(db, "Quillon#2")
    for _ in range(20):
        seed(db, {0: me, 6: other}, kills=[kill()])
    page = _fragment(db, "Zorblax#1")
    assert "Hold" in page and "Retake" not in page
    assert "Ascent" in page and "n=20" in page
    assert "You hold from Heaven 100% of post-plant time" in page
    assert "+" in page and "pts" in page                          # R10: signed
    assert "Quillon" not in page                                  # R11: nobody else is named


def test_nineteen_on_both_sides_is_an_empty_fragment(db):
    me = _player(db, "Zorblax#1")
    for _ in range(19):
        seed(db, {0: me})
    for _ in range(19):
        seed(db, {5: me})
    assert _fragment(db, "Zorblax#1").strip() == ""


def test_distinct_post_plants_not_area_visits_decide(db):
    me = _player(db, "Zorblax#1")
    for _ in range(10):
        seed(db, {0: me}, samples={0: [100, 200, 300]})
    assert _fragment(db, "Zorblax#1").strip() == ""


def test_a_block_under_eight_is_not_listed_but_counts_to_eligibility(db):
    me = _player(db, "Zorblax#1")
    for _ in range(7):
        seed(db, {0: me})
    for _ in range(7):
        seed(db, {0: me}, samples={0: [100]})
    assert _fragment(db, "Zorblax#1").strip() == ""


def test_unknown_player_is_404(db):
    with pytest.raises(HTTPException) as err:
        _fragment(db, "Nobody#9")
    assert err.value.status_code == 404


def test_only_the_players_replays_are_read(db, monkeypatch):
    me, other = _player(db, "Zorblax#1"), _player(db, "Quillon#2")
    for _ in range(20):
        seed(db, {0: me})
    for _ in range(5):
        seed(db, {0: other})
    mine = {r.id for r in db.query(Replay).join(ReplayPlayer, ReplayPlayer.replay_id == Replay.id)
            .join(MatchPlayer, MatchPlayer.id == ReplayPlayer.match_player_id)
            .filter(MatchPlayer.player_id == me.id)}
    seen = []
    real = postplant_page.read_rounds
    monkeypatch.setattr(postplant_page, "read_rounds",
                        lambda db_, key, replay_ids=None: seen.append(replay_ids) or real(db_, key, replay_ids))
    assert "n=20" in _fragment(db, "Zorblax#1")
    # the player's own read is limited to their replays; the unfiltered read is the Everyone map table
    # (cache path), which is the only other caller
    assert len(mine) == 20 and [ids for ids in seen if ids is not None] == [mine]


# --- filter and accounts ---------------------------------------------------------


def test_agent_filter_keeps_only_that_agent(db):
    me = _player(db, "Zorblax#1")
    for _ in range(20):
        seed(db, {0: me}, agents={0: "Jett"})
    for _ in range(5):
        seed(db, {0: me}, agents={0: "Sage"})
    assert "n=25" in _fragment(db, "Zorblax#1")
    assert "n=20" in _fragment(db, "Zorblax#1", filter="agent:Jett")
    assert _fragment(db, "Zorblax#1", filter="agent:Sage").strip() == ""


def test_accounts_all_includes_a_linked_accounts_rounds(db):
    main, alt = _player(db, "Zorblax#1"), _player(db, "Zorblax Alt#2")
    for _ in range(12):
        seed(db, {0: main})
    for _ in range(12):
        seed(db, {0: alt})
    _group(db, main, alt)
    assert _fragment(db, "Zorblax#1").strip() == ""
    page = _fragment(db, "Zorblax#1", accounts="all")
    assert "n=24" in page


def test_a_shared_match_counts_once_and_the_filter_applies_after(db):
    main, alt = _player(db, "Zorblax#1"), _player(db, "Zorblax Alt#2")
    assert main.id < alt.id
    for _ in range(20):
        seed(db, {0: main, 1: alt}, agents={0: "Jett", 1: "Sage"})
    _group(db, main, alt)
    page = _fragment(db, "Zorblax#1", accounts="all")
    assert re.search(r"<h3[^>]*>Hold <span[^>]*>\(n=20\)", page)     # Everyone's rows may say n=40
    # Sage is "played" only through three matches the lowest id sat out as Sage.
    for _ in range(3):
        seed(db, {0: main}, agents={0: "Sage"})
    assert _fragment(db, "Zorblax#1", accounts="all", filter="agent:Sage").strip() == ""
    assert "n=20" in _fragment(db, "Zorblax#1", accounts="all", filter="agent:Jett")


# --- demo mode -------------------------------------------------------------------


def test_demo_mode_is_404_and_the_page_has_no_div(db, monkeypatch):
    me = _player(db, "Zorblax#1")
    db.commit()
    monkeypatch.setattr(settings, "demo_mode", True)
    with pytest.raises(HTTPException) as err:
        _fragment(db, "Zorblax#1")
    assert err.value.status_code == 404
    template = templates.env.get_template("players/detail.html")
    source = open(template.filename, encoding="utf-8").read()
    at = source.index("/postplant{{ view_query }}")
    assert "{% if not demo_mode %}" in source[max(0, at - 200):at]


def test_the_detail_page_loads_the_fragment_after_load():
    template = templates.env.get_template("players/detail.html")
    source = open(template.filename, encoding="utf-8").read()
    assert '/postplant{{ view_query }}"' in source and 'hx-trigger="load delay:1s"' in source


def test_the_route_is_listed_as_not_a_page():
    from app.services import site_health
    assert site_health.PAGE_ROUTES["/players/{display_name}/postplant"] == "not_page"
