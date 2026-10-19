"""The Form card filtered by role or agent (design P4)."""

from datetime import datetime, timedelta, timezone

from app.models import ImpactScore, Match, MatchPlayer, Round
from app.models.match import MatchSource, Team
from app.routers import players as players_router
from app.services.player_data import played_agent_counts
from app.services.recent_match_rows import build_recent_match_rows, recent_form_entries
from tests.test_account_groups import _players, db  # noqa: F401  (db is a fixture)
from tests.test_account_groups_routes import _request, site  # noqa: F401  (site is a fixture)


def _game(db, player, day, ext, agent, team=Team.TEAM_1, match=None):
    if match is None:
        match = Match(
            external_id=ext, source=MatchSource.SCRAPED, map_name="Ascent",
            played_at=datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(days=day),
        )
        db.add(match)
        db.flush()
    mp = MatchPlayer(match_id=match.id, player_id=player.id, agent=agent, team=team)
    db.add(mp)
    db.flush()
    return mp


def _ext(entries):
    return [e.external_id for e in entries]


def _agents_by_day(db, player):
    # Jett on days 0-2, Sova on days 3-5.
    for day in range(3):
        _game(db, player, day, f"j{day}", "Jett")
    for day in range(3, 6):
        _game(db, player, day, f"s{day}", "Sova")
    db.commit()


def test_entries_filter_by_agent_and_limit_applies_after(db):
    (p,) = _players(db, "a#1")
    _agents_by_day(db, p)
    got = recent_form_entries(db, p.id, limit=2, agents=frozenset({"Sova"}))
    assert _ext(got) == ["s5", "s4"]
    got = recent_form_entries(db, p.id, limit=10, agents=frozenset({"Jett"}))
    assert _ext(got) == ["j2", "j1", "j0"]
    assert len(recent_form_entries(db, p.id, limit=10)) == 6


def test_combined_filter_keeps_agents_and_counts_shared_match_once(db):
    a, b = _players(db, "a#1", "b#2")
    _game(db, a, 1, "x1", "Sova")
    shared = _game(db, a, 2, "shared", "Sova")
    _game(db, b, 2, "shared", "Sova", match=shared.match)
    _game(db, b, 3, "b-jett", "Jett")
    _game(db, b, 4, "b-sova", "Sova")
    db.commit()
    ids = frozenset({a.id, b.id})
    got = recent_form_entries(db, a.id, player_ids=ids, agents=frozenset({"Sova"}))
    assert _ext(got) == ["b-sova", "shared", "x1"]


def test_played_agent_counts(db):
    a, b = _players(db, "a#1", "b#2")
    _game(db, a, 1, "m1", "Jett")
    _game(db, a, 2, "m2", "Jett")
    _game(db, a, 3, "m3", "Sova")
    # (match_players.agent is NOT NULL, so there is no NULL-agent row to skip)
    shared = _game(db, a, 5, "m5", "Sova")
    _game(db, b, 5, "m5", "Sova", match=shared.match)
    _game(db, b, 6, "m6", "Sova")
    db.commit()
    single = {agent: (n, last) for agent, n, last in played_agent_counts(db, a.id)}
    assert set(single) == {"Jett", "Sova"}
    assert single["Jett"][0] == 2 and single["Sova"][0] == 2
    assert single["Jett"][1].day == 3     # Sept 1 + 2 days
    assert single["Sova"][1].day == 6     # the shared match, Sept 1 + 5 days
    both = {agent: n for agent, n, _ in played_agent_counts(db, a.id, frozenset({a.id, b.id}))}
    assert both == {"Jett": 2, "Sova": 3}


def _scored(db, mp, econ_kill):
    round_row = Round(match_id=mp.match_id, round_number=1)
    db.add(round_row)
    db.flush()
    db.add(ImpactScore(
        match_player_id=mp.id, round_id=round_row.id, kill_impact=5, death_impact=0, impact=5,
        econ_kill=econ_kill,
    ))


def _econ_badges(row):
    return [b.label for b in row.badges if b.label.startswith("Econ impact")]


def test_thresholds_come_from_the_filtered_baseline(db):
    (p,) = _players(db, "a#1")
    for day in range(10):
        _scored(db, _game(db, p, day, f"j{day}", "Jett"), 1000)
    star = None
    for day in range(10, 20):
        mp = _game(db, p, day, f"s{day}", "Sova")
        _scored(db, mp, 400 if day == 19 else 0)
    db.commit()
    sova = frozenset({"Sova"})
    entries = recent_form_entries(db, p.id, agents=sova)
    filtered = build_recent_match_rows(db, p.id, entries, agents=sova)
    assert _econ_badges(filtered[0]) == ["Econ impact +400"]       # s19, top 20% of Sova
    unfiltered = build_recent_match_rows(db, p.id, entries)
    assert _econ_badges(unfiltered[0]) == []                        # below the mixed threshold


def _fragment_setup(db):
    (p,) = _players(db, "a#1")
    _game(db, p, 0, "d0", "Jett")        # duelist
    _game(db, p, 1, "t1", "Killjoy")     # sentinel
    _game(db, p, 2, "t2", "Cypher")      # sentinel
    _game(db, p, 3, "s3", "Sova")
    db.commit()
    return p


def _rows(site, p, **kw):
    response = players_router.player_recent_matches_fragment(_request(p), "a#1", db=site, **kw)
    return [r.entry.external_id for r in response.context["recent_rows"]]


def test_fragment_filters(site, monkeypatch):
    p = _fragment_setup(site)
    assert _rows(site, p, filter="role:sentinel") == ["t2", "t1"]
    assert _rows(site, p, filter="agent:Sova") == ["s3"]
    everything = ["s3", "t2", "t1", "d0"]
    assert _rows(site, p) == everything
    assert _rows(site, p, filter="bogus") == everything
    assert _rows(site, p, filter="agent:NeverPlayed") == everything


def test_unfiltered_fragment_does_not_look_up_played_agents(site, monkeypatch):
    p = _fragment_setup(site)
    calls = []
    real = players_router.played_agent_counts
    monkeypatch.setattr(players_router, "played_agent_counts", lambda *a, **k: calls.append(1) or real(*a, **k))
    _rows(site, p)
    assert calls == []
    _rows(site, p, filter="agent:Sova")
    assert calls == [1]
