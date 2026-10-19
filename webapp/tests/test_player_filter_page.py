"""The role/agent filter on the player page: which matches each filter keeps,
the select and the "Showing N matches" line, the fragment URLs, the keyed
cache read and write, and the empty state that computes nothing.

Routes are called directly (see tests/test_account_groups_routes.py). The
PG-only cache upsert can't run on sqlite, so the cache functions are spied.
"""

from types import SimpleNamespace

import pytest

from app.models import ImpactScore, Round
from app.routers import players as players_router
from app.services.account_groups import link_account
from app.services.economy_graphs import (
    build_loadout_win_scatter_from_aggregates,
    build_pistol_match_stats_from_aggregates,
    build_pistol_stats_from_aggregates,
    build_tier_matrix_from_aggregates,
)
from app.services.fight_ev import serialize_fight_ev_views
from app.services.player_graphs import build_state_diagrams_from_aggregates
from tests.test_account_groups import _play, _players, db  # noqa: F401  (db is a fixture)
from tests.test_account_groups_routes import _body, _demo, _request, _Spy, site  # noqa: F401


def _match(db, player, day, ext, agent):
    mp = _play(db, player, day, ext)
    mp.agent = agent
    round_row = Round(match_id=mp.match_id, round_number=1)
    db.add(round_row)
    db.flush()
    db.add(ImpactScore(match_player_id=mp.id, round_id=round_row.id, kill_impact=5, death_impact=0, impact=5))
    return mp


@pytest.fixture
def solo(site):
    """p#1: Jett, Reyna, Sova, Sova (no sentinel)."""
    (p,) = _players(site, "p#1")
    for day, (ext, agent) in enumerate(
        [("e-jett", "Jett"), ("e-reyna", "Reyna"), ("e-sova1", "Sova"), ("e-sova2", "Sova")], start=1
    ):
        _match(site, p, day, ext, agent)
    site.commit()
    return p


class _Counters:
    def __init__(self, monkeypatch):
        self.compute = 0
        self.reads = []
        self.stores = []
        real_compute = players_router.compute_player_views

        def compute(*a, **kw):
            self.compute += 1
            return real_compute(*a, **kw)

        def read(db, player, scope, key=""):
            self.reads.append((scope, key))
            return None

        def store(db, player_id, views_by_key):
            self.stores.append(dict(views_by_key))

        monkeypatch.setattr(players_router, "compute_player_views", compute)
        monkeypatch.setattr(players_router, "get_cached_views", read)
        monkeypatch.setattr(players_router, "store_player_views", store)


def _externals(page):
    return {m.match.external_id for m in page.context["profile"].matches}


def test_role_filter_keeps_only_that_roles_matches(site, solo):
    page = players_router.player_detail(_request(solo), "p#1", filter="role:duelist", db=site)
    assert _externals(page) == {"e-jett", "e-reyna"}
    html = _body(page)
    assert "Showing 2 matches as Duelist" in html
    assert '<option value="role:duelist" selected' in html
    assert "filter=role%3Aduelist" in html
    assert page.context["filter_key"] == "role:duelist"


def test_agent_filter_keeps_only_that_agent(site, solo):
    page = players_router.player_detail(_request(solo), "p#1", filter="agent:Sova", db=site)
    assert _externals(page) == {"e-sova1", "e-sova2"}
    html = _body(page)
    assert "Showing 2 matches as Sova" in html
    assert '<option value="agent:Sova" selected' in html
    assert ">Sova (2)<" in html


def test_unfiltered_page_has_options_and_no_showing_line(site, solo):
    page = players_router.player_detail(_request(solo), "p#1", db=site)
    html = _body(page)
    assert "Showing" not in html
    assert '<option value="" selected' in html
    assert page.context["filter_options"][0] == ("", "All agents")
    assert [v for v, _ in page.context["filter_options"][1:5]] == [
        "role:duelist", "role:initiator", "role:controller", "role:sentinel"]
    assert page.context["filter_options"][5] == ("agent:Sova", "Sova (2)")
    assert page.context["view_query"] == ""
    assert "filter=" not in html


@pytest.mark.parametrize("bad", ["bogus", "agent:NeverPlayed", ""])
def test_bad_key_is_the_unfiltered_page(site, solo, bad):
    plain = players_router.player_detail(_request(solo), "p#1", db=site)
    page = players_router.player_detail(_request(solo), "p#1", filter=bad, db=site)
    assert _externals(page) == _externals(plain) == {"e-jett", "e-reyna", "e-sova1", "e-sova2"}
    assert page.context["filter_key"] == ""
    assert "Showing" not in _body(page)


def test_combined_view_filter_covers_every_account_and_urls_carry_both(site):
    main, alt = _players(site, "main#1", "alt#2")
    _match(site, main, 1, "m-jett", "Jett")
    _match(site, alt, 2, "a-reyna", "Reyna")
    _match(site, alt, 3, "a-sova", "Sova")
    assert link_account(site, main.id, alt.id) is None
    site.commit()
    page = players_router.player_detail(_request(main), "main#1", accounts="all", filter="role:duelist", db=site)
    assert _externals(page) == {"m-jett", "a-reyna"}
    html = _body(page)
    assert "Showing 2 matches as Duelist" in html
    assert page.context["view_query"] == "?accounts=all&filter=role%3Aduelist"
    assert 'name="accounts" value="all"' in html
    assert "/career?accounts=all&amp;filter=role%3Aduelist" in html


def test_empty_role_shows_the_empty_state_and_computes_nothing(site, solo, monkeypatch):
    counters = _Counters(monkeypatch)
    spy = _Spy()
    monkeypatch.setattr(players_router, "templates", spy)
    page = players_router.player_detail(_request(solo), "p#1", filter="role:sentinel", db=site)
    html = _body(page)
    assert "No matches as Sentinel." in html
    assert counters.compute == 0 and counters.reads == [] and counters.stores == []
    assert spy.contexts[-1]["filter_empty"] is True
    assert 'id="recent-sections"' not in html
    assert 'name="filter"' in html   # the select is still there to change it back

    players_router.player_career_fragment(_request(solo), "p#1", filter="role:sentinel", db=site)
    assert counters.compute == 0 and counters.reads == []


def _as_cached(computed):
    round_win, kill_order = build_state_diagrams_from_aggregates(computed.win_stats, computed.kill_order_weights)
    return SimpleNamespace(
        round_win_graph=round_win, kill_order_graph=kill_order,
        fight_ev_data=serialize_fight_ev_views(computed.fight_ev), profile=computed.profile,
        econ_tier_matrix=build_tier_matrix_from_aggregates(computed.econ_aggregates["tier_pairs"]),
        econ_pistol_stats=build_pistol_stats_from_aggregates(computed.econ_aggregates["pistol"]),
        econ_loadout_scatter=build_loadout_win_scatter_from_aggregates(computed.econ_aggregates["loadout_buckets"]),
        pistol_match_stats=build_pistol_match_stats_from_aggregates(computed.pistol_match_stats),
    )


def test_a_filtered_miss_writes_a_keyed_row_and_the_next_request_hits_it(site, solo, monkeypatch):
    counters = _Counters(monkeypatch)
    players_router.player_detail(_request(solo), "p#1", filter="role:duelist", db=site)
    assert counters.reads == [("recent", "role:duelist")]
    assert counters.compute == 1
    assert [list(s) for s in counters.stores] == [["recent|role:duelist"]]

    stored = counters.stores[0]["recent|role:duelist"]
    cached = _as_cached(stored)
    monkeypatch.setattr(players_router, "get_cached_views", lambda db, p, scope, key="": cached)
    page = players_router.player_detail(_request(solo), "p#1", filter="role:duelist", db=site)
    assert counters.compute == 1
    assert len(counters.stores) == 1
    assert _externals(page) == {"e-jett", "e-reyna"}


def test_the_unfiltered_page_still_uses_the_unkeyed_row(site, solo, monkeypatch):
    counters = _Counters(monkeypatch)
    players_router.player_career_fragment(_request(solo), "p#1", db=site)
    assert [list(s) for s in counters.stores] == [["career"]]


def test_map_streaks_are_not_filtered(site, solo, monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(players_router, "templates", spy)
    players_router.player_detail(_request(solo), "p#1", db=site)
    players_router.player_detail(_request(solo), "p#1", filter="role:duelist", db=site)
    players_router.player_detail(_request(solo), "p#1", filter="role:sentinel", db=site)
    plain, filtered, empty = (c["map_streaks"] for c in spy.contexts[-3:])
    assert plain == filtered == empty


def test_career_with_a_filter_is_filtered(site, solo, monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(players_router, "templates", spy)
    players_router.player_career_fragment(_request(solo), "p#1", filter="agent:Sova", db=site)
    assert {m.match.external_id for m in spy.contexts[-1]["profile"].matches} == {"e-sova1", "e-sova2"}
    players_router.player_career_fragment(_request(solo), "p#1", db=site)
    assert len(spy.contexts[-1]["profile"].matches) == 4


def test_demo_shows_the_select_but_no_account_chips(site, solo, monkeypatch):
    _demo(monkeypatch)
    page = players_router.player_detail(_request(solo), "p#1", filter="agent:Sova", db=site)
    html = _body(page)
    assert 'name="filter"' in html
    assert "Showing 2 matches as Sova" in html
    assert "account-toggle" not in html
