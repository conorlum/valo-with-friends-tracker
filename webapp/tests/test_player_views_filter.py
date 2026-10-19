"""W16: one load and one replay for many filters (compute_player_views_by_filter)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.services import player_views
from app.services.agent_roles import filter_agents
from app.services.player_data import RECENT_MATCH_LIMIT, load_player_match_data
from app.services.player_view_cache import cache_scope_key, encode_player_views
from app.services.player_views import (
    compute_player_views,
    compute_player_views_by_filter,
    compute_player_views_by_scope,
)
from tests.test_account_groups import TABLES   # also registers the JSONB-on-sqlite shim

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_db(path):
    eng = create_engine("sqlite:///" + str(path))
    Base.metadata.create_all(eng, tables=[t.__table__ for t in TABLES])
    return eng


@pytest.fixture
def db(tmp_path):
    eng = _make_db(tmp_path / "filters.sqlite")
    with sessionmaker(bind=eng)() as session:
        yield session
    eng.dispose()


def _game(db, player, enemy, hour, agent, external_id):
    """One match with one round: `player` kills `enemy` once."""
    match = Match(
        external_id=external_id, source=MatchSource.SCRAPED, map_name="Ascent",
        played_at=BASE + timedelta(hours=hour),
    )
    db.add(match)
    db.flush()
    mp = MatchPlayer(match_id=match.id, player_id=player.id, agent=agent, team=Team.TEAM_1)
    foe = MatchPlayer(match_id=match.id, player_id=enemy.id, agent="Reyna", team=Team.TEAM_2)
    db.add_all([mp, foe])
    db.flush()
    rnd = Round(match_id=match.id, round_number=1, outcome="Eliminated")
    db.add(rnd)
    db.flush()
    db.add_all([
        RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, kills=1, deaths=0, assists=0),
        RoundPlayerStat(round_id=rnd.id, match_player_id=foe.id, kills=0, deaths=1, assists=0),
        KillEvent(
            round_id=rnd.id, killer_match_player_id=mp.id, death_match_player_id=foe.id,
            weapon="Vandal", event_time_seconds=10.0 + hour,
        ),
    ])
    db.flush()
    return mp


def _two_players(db):
    me, foe = Player(display_name="me#1"), Player(display_name="foe#2")
    db.add_all([me, foe])
    db.flush()
    return me, foe


def _mixed(db):
    me, foe = _two_players(db)
    agents = ["Jett", "Sova", "Omen", "Jett", "Sova", "Jett"]
    for i, agent in enumerate(agents):
        _game(db, me, foe, i, agent, f"m{i}")
    db.commit()
    return me, foe


def _enc(views):
    return encode_player_views(views)


def _played(db, me, ids=None):
    return {mp.agent for mp in load_player_match_data(db, me, None, player_ids=ids)}


KEYS = ("", "role:duelist", "agent:Sova")


def test_career_matches_the_filtered_single_scope_compute(db):
    me, _ = _mixed(db)
    played = _played(db, me)
    out = compute_player_views_by_filter(db, me, KEYS)
    for key in KEYS:
        agents = filter_agents(key, played) if key else None
        expected = compute_player_views(db, me, None, agents=agents)
        assert _enc(out[cache_scope_key("career", key)]) == _enc(expected), key


def test_career_equals_scope_compute_on_a_second_db_holding_only_those_matches(db, tmp_path):
    me, _ = _mixed(db)
    out = compute_player_views_by_filter(db, me, ("agent:Sova",))
    eng = _make_db(tmp_path / "only_sova.sqlite")
    try:
        with sessionmaker(bind=eng)() as other:
            me2, foe2 = _two_players(other)
            for i, agent in enumerate(["Jett", "Sova", "Omen", "Jett", "Sova", "Jett"]):
                if agent == "Sova":
                    _game(other, me2, foe2, i, agent, f"m{i}")
            other.commit()
            by_scope = compute_player_views_by_scope(other, me2)
            assert _enc(out["career|agent:Sova"]) == _enc(by_scope["career"])
            assert _enc(out["recent|agent:Sova"]) == _enc(by_scope["recent"])
    finally:
        eng.dispose()


def test_filtered_recent_is_the_newest_thirty_of_that_filter(db, monkeypatch):
    me, foe = _two_players(db)
    sova_hours = {5, 12, 20, 28, 36}
    jett = 0
    for hour in range(37):
        agent = "Sova" if hour in sova_hours else "Jett"
        jett += agent == "Jett"
        _game(db, me, foe, hour, agent, f"m{hour}")
    db.commit()
    assert jett == 32
    real = player_views._build_profile_and_econ
    covered = []   # (external ids of the rows, newest first) per call

    def spy(player, rows, scores):
        covered.append([mp.match.external_id for mp in rows])
        return real(player, rows, scores)

    monkeypatch.setattr(player_views, "_build_profile_and_econ", spy)
    out = compute_player_views_by_filter(db, me, ("agent:Jett", "agent:Sova"))
    assert len(out) == 4
    newest_jett = [f"m{h}" for h in range(36, -1, -1) if h not in sova_hours]
    sova = [f"m{h}" for h in sorted(sova_hours, reverse=True)]
    # calls come in key order, recent then career
    assert covered == [newest_jett[:30], newest_jett, sova, sova]
    assert len(newest_jett[:30]) == 30 and newest_jett[0] == "m35"
    assert _enc(out["recent|agent:Jett"]) == _enc(compute_player_views(db, me, RECENT_MATCH_LIMIT, agents=frozenset({"Jett"})))
    assert _enc(out["recent|agent:Sova"]) == _enc(compute_player_views(db, me, None, agents=frozenset({"Sova"})))


def test_eight_keys_cost_one_load_and_one_replay(db, monkeypatch):
    me, _ = _mixed(db)
    loads, replays = [], []
    real_load, real_replay = player_views.load_player_match_data, player_views._replay_all_matches
    monkeypatch.setattr(player_views, "load_player_match_data", lambda *a, **k: loads.append(1) or real_load(*a, **k))
    monkeypatch.setattr(player_views, "_replay_all_matches", lambda *a, **k: replays.append(1) or real_replay(*a, **k))
    keys = ("", "role:duelist", "role:initiator", "role:controller", "role:sentinel",
            "agent:Jett", "agent:Sova", "agent:Omen")
    out = compute_player_views_by_filter(db, me, keys)
    assert len(loads) == 1 and len(replays) == 1
    assert set(out) == {cache_scope_key(s, k) for k in keys for s in ("recent", "career")}


def test_by_scope_is_the_unfiltered_by_filter_output(db):
    me, _ = _mixed(db)
    by_scope = compute_player_views_by_scope(db, me)
    by_filter = compute_player_views_by_filter(db, me, ("",))
    assert set(by_scope) == {"recent", "career"} == set(by_filter)
    for scope in by_scope:
        assert _enc(by_scope[scope]) == _enc(by_filter[scope])


def test_a_key_selecting_nothing_still_gets_empty_views(db):
    me, _ = _mixed(db)
    out = compute_player_views_by_filter(db, me, ("", "role:sentinel"))
    assert "career|role:sentinel" in out and "recent|role:sentinel" in out
    # a player with no matches at all: every key yields entries
    nobody = Player(display_name="nobody#9")
    db.add(nobody)
    db.commit()
    empty = compute_player_views_by_filter(db, nobody, ("",))
    assert set(empty) == {"recent", "career"}


def test_a_group_with_a_filter_takes_rows_from_both_accounts(db):
    main, alt = Player(display_name="main#1"), Player(display_name="alt#2")
    foe = Player(display_name="foe#3")
    db.add_all([main, alt, foe])
    db.flush()
    _game(db, main, foe, 0, "Jett", "a0")
    _game(db, alt, foe, 1, "Sova", "a1")
    _game(db, alt, foe, 2, "Jett", "a2")
    _game(db, main, foe, 3, "Sova", "a3")
    db.commit()
    ids = frozenset({main.id, alt.id})
    rows = load_player_match_data(db, main, None, player_ids=ids, agents=frozenset({"Jett"}))
    assert [mp.match.external_id for mp in rows] == ["a2", "a0"]
    assert {mp.player_id for mp in rows} == {main.id, alt.id}
    out = compute_player_views_by_filter(db, main, ("agent:Jett",), player_ids=ids)
    expected = compute_player_views(db, main, None, player_ids=ids, agents=frozenset({"Jett"}))
    assert _enc(out["career|agent:Jett"]) == _enc(expected)


def test_load_with_agents_filters_rows_newest_first(db):
    me, _ = _mixed(db)
    assert load_player_match_data(db, me, None, agents=frozenset()) == []
    rows = load_player_match_data(db, me, None, agents=frozenset({"Jett"}))
    assert [mp.match.external_id for mp in rows] == ["m5", "m3", "m0"]
    assert all(mp.agent == "Jett" for mp in rows)
    assert len(load_player_match_data(db, me, None)) == 6
