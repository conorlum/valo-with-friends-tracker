"""W17: the pre-computed filter set and the keyed view caches.

sqlite tests cover the key set and the "unfiltered rows only" counts; the
Postgres ones (VALO_TEST_DATABASE_URL, a *_test database) cover the multi-row
upsert, which sqlite cannot run."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.models import (
    AccountGroupMember,
    AccountGroupViewCache,
    Match,
    MatchPlayer,
    Player,
    PlayerViewCache,
)
from app.models.match import MatchSource, Team
from app.services import account_groups, player_view_cache as pvc
from app.services.account_groups import (
    get_cached_group_views,
    group_fingerprint,
    link_account,
    prewarm_group_caches,
)
from app.services.player_view_cache import (
    cache_version,
    filter_cached_player_ids,
    get_cached_views,
    invalidate_player_cache,
    recompute_player_views,
    store_player_views,
    _encode,
)
from app.services.player_views import precompute_keys
from scripts.verify_player_cache_coverage import coverage
from tests._postgres import postgres_session_or_skip
from tests.test_account_groups import _players, db   # noqa: F401  (db is a fixture)
from tests.test_player_view_cache import _sample_views


def _play_as(db, player, agent, day, external_id):
    match = Match(
        external_id=external_id, source=MatchSource.SCRAPED, map_name="Ascent",
        played_at=datetime(2026, 10, day, tzinfo=timezone.utc),
    )
    db.add(match)
    db.flush()
    mp = MatchPlayer(match_id=match.id, player_id=player.id, agent=agent, team=Team.TEAM_1)
    db.add(mp)
    db.flush()
    return mp


def _keys(db, player):
    return precompute_keys(db.query(MatchPlayer).filter_by(player_id=player.id).all())


# ---------------------------------------------------------------------------
# precompute_keys
# ---------------------------------------------------------------------------

def test_roles_are_listed_in_role_order_and_only_when_played(db):
    (p,) = _players(db, "p#1")
    _play_as(db, p, "Sage", 1, "m1")        # sentinel
    _play_as(db, p, "Jett", 2, "m2")        # duelist
    keys = _keys(db, p)
    assert keys[:3] == ("", "role:duelist", "role:sentinel")
    assert not any(k in keys for k in ("role:initiator", "role:controller"))


def test_top_three_agents_by_count_then_recency_then_name(db):
    (p,) = _players(db, "p#1")
    day = 0
    for agent, n in (("Jett", 3), ("Sage", 2), ("Omen", 1), ("Sova", 1), ("Fade", 1)):
        for _ in range(n):
            day += 1
            _play_as(db, p, agent, day, f"m{day}")
    keys = _keys(db, p)
    agent_keys = [k for k in keys if k.startswith("agent:")]
    # Jett (3), Sage (2); then the three 1-match agents tie, Fade played last.
    assert agent_keys == ["agent:Jett", "agent:Sage", "agent:Fade"]


def test_equal_count_ties_go_to_the_most_recently_played(db):
    (p,) = _players(db, "p#1")
    _play_as(db, p, "Sova", 5, "m1")
    _play_as(db, p, "Omen", 9, "m2")
    _play_as(db, p, "Fade", 7, "m3")
    _play_as(db, p, "Brimstone", 1, "m4")
    agent_keys = [k for k in _keys(db, p) if k.startswith("agent:")]
    assert agent_keys == ["agent:Omen", "agent:Fade", "agent:Sova"]


def test_recency_is_the_latest_match_not_the_first(db):
    (p,) = _players(db, "p#1")
    _play_as(db, p, "Sova", 1, "m1")
    _play_as(db, p, "Omen", 2, "m2")
    _play_as(db, p, "Omen", 3, "m3")
    _play_as(db, p, "Sova", 10, "m4")      # Sova's latest is the newest match of all
    _play_as(db, p, "Fade", 6, "m5")
    _play_as(db, p, "Fade", 7, "m6")
    agent_keys = [k for k in _keys(db, p) if k.startswith("agent:")]
    assert agent_keys == ["agent:Sova", "agent:Fade", "agent:Omen"]


def test_equal_count_and_time_ties_go_to_the_name(db):
    (p,) = _players(db, "p#1")
    _play_as(db, p, "Sova", 5, "m1")
    _play_as(db, p, "Omen", 5, "m2")
    agent_keys = [k for k in _keys(db, p) if k.startswith("agent:")]
    assert agent_keys == ["agent:Omen", "agent:Sova"]


def test_fewer_than_three_agents_gives_fewer_keys_and_no_matches_gives_only_blank(db):
    (p, q) = _players(db, "p#1", "q#2")
    _play_as(db, p, "Jett", 1, "m1")
    assert [k for k in _keys(db, p) if k.startswith("agent:")] == ["agent:Jett"]
    assert _keys(db, q) == ("",)


# ---------------------------------------------------------------------------
# Unfiltered-only counting
# ---------------------------------------------------------------------------

def _add_row(db, player_id, scope):
    db.add(PlayerViewCache(player_id=player_id, scope=scope, data=_encode(_sample_views(player_id)),
                           version=cache_version()))
    db.commit()


def test_a_player_with_only_a_filtered_row_is_not_counted_as_cached(db):
    a, b, c = _players(db, "a#1", "b#2", "c#3")
    _add_row(db, a.id, "career|role:duelist")
    _add_row(db, b.id, "career")
    assert filter_cached_player_ids(db, {a.id, b.id, c.id}) == {b.id}


def test_a_new_match_clears_a_players_filtered_only_rows_without_prewarming_them(db):
    a, b, c = _players(db, "a#1", "b#2", "c#3")
    mp = _play_as(db, a, "Jett", 1, "m1")
    for player in (b, c):
        db.add(MatchPlayer(match_id=mp.match_id, player_id=player.id, agent="Sage", team=Team.TEAM_1))
    db.commit()
    _add_row(db, a.id, "career|role:duelist")
    _add_row(db, b.id, "career")

    dirty = pvc.invalidate_match_players(db, mp.match_id)
    db.commit()
    assert dirty == {b.id}
    with sessionmaker(bind=db.get_bind())() as fresh:
        assert fresh.query(PlayerViewCache).count() == 0


def test_a_group_with_only_a_filtered_row_does_not_count_its_members(db):
    main, alt = _players(db, "main#1", "alt#2")
    link_account(db, main.id, alt.id)
    gid = db.query(AccountGroupMember).filter_by(player_id=main.id).one().group_id
    db.add(AccountGroupViewCache(group_id=gid, scope="career|role:duelist", fingerprint="f",
                                 data=_encode(_sample_views(main.id)), version=cache_version()))
    db.commit()
    assert filter_cached_player_ids(db, {main.id, alt.id}) == set()


def test_store_group_views_with_a_key_lands_in_the_keyed_row(db):
    main, alt = _players(db, "main#1", "alt#2")
    link_account(db, main.id, alt.id)
    gid = db.query(AccountGroupMember).filter_by(player_id=main.id).one().group_id
    account_groups.store_group_views(db, gid, "career", "fp", _sample_views(main.id), key="agent:Jett")
    assert [r.scope for r in db.query(AccountGroupViewCache).all()] == ["career|agent:Jett"]
    assert get_cached_group_views(db, gid, "career", "fp", main, key="agent:Jett") is not None
    assert get_cached_group_views(db, gid, "career", "fp", main) is None


def test_verify_coverage_ignores_filtered_rows(db):
    (p,) = _players(db, "p#1")
    _add_row(db, p.id, "career|agent:Jett")
    result = coverage(db, {p.id})
    assert result["ok"] == 0
    assert len(result["problems"]) == 2      # recent and career missing; the filtered row neither counts nor raises
    _add_row(db, p.id, "recent")
    _add_row(db, p.id, "career")
    assert coverage(db, {p.id})["ok"] == 2


# ---------------------------------------------------------------------------
# Postgres: the keyed caches
# ---------------------------------------------------------------------------

_CLEAN = (
    "DELETE FROM account_group_view_cache", "DELETE FROM player_view_cache",
    "DELETE FROM account_group_members", "DELETE FROM account_groups",
    "DELETE FROM friendships",
)


@pytest.fixture
def pg():
    session = postgres_session_or_skip(empties_tables=True)
    engine = session.get_bind()

    def clean():
        session.rollback()
        for stmt in _CLEAN:
            session.execute(text(stmt))
        session.execute(text("DELETE FROM players WHERE display_name LIKE 'w17-%'"))
        session.commit()

    clean()
    yield session
    clean()
    session.close()
    engine.dispose()


def _fresh(pg):
    """A new session on a new connection: sees only what was committed."""
    return sessionmaker(bind=create_engine(pg.get_bind().url))()


def _pg_players(pg, *names):
    players = [Player(display_name=f"w17-{n}") for n in names]
    pg.add_all(players)
    pg.commit()
    return players


def _views_with(player_id, wins):
    views = _sample_views(player_id)
    views.win_stats["5v5"]["win"] = wins
    return views


def _wins_in(row):
    return row.data["state_aggregates"]["win_stats"]["5v5"]["win"]


def _rows(session, player_id):
    return {r.scope: r for r in session.query(PlayerViewCache).filter_by(player_id=player_id).all()}


def _count_statements(engine, prefix):
    seen = {"insert": 0, "commit": 0}

    def on_exec(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith(prefix):
            seen["insert"] += 1

    def on_commit(conn):
        seen["commit"] += 1

    event.listen(engine, "before_cursor_execute", on_exec)
    event.listen(engine, "commit", on_commit)
    return seen, lambda: (event.remove(engine, "before_cursor_execute", on_exec),
                          event.remove(engine, "commit", on_commit))


def test_recompute_writes_exactly_the_returned_keys_in_one_insert_and_one_commit(pg, monkeypatch):
    (p,) = _pg_players(pg, "a")
    returned = {
        "recent": _views_with(p.id, 1), "career": _views_with(p.id, 2),
        "recent|role:duelist": _views_with(p.id, 3), "career|role:duelist": _views_with(p.id, 4),
        "career|agent:Jett": _views_with(p.id, 5),
    }
    monkeypatch.setattr(pvc, "compute_player_views_by_filter", lambda db, player, keys, *a, **k: returned)
    seen, stop = _count_statements(pg.get_bind(), "INSERT INTO PLAYER_VIEW_CACHE")
    try:
        recompute_player_views(pg, p.id)
    finally:
        stop()
    assert seen == {"insert": 1, "commit": 1}
    rows = _rows(_fresh(pg), p.id)
    assert {s: _wins_in(r) for s, r in rows.items()} == {
        "recent": 1, "career": 2, "recent|role:duelist": 3, "career|role:duelist": 4, "career|agent:Jett": 5,
    }
    assert all(r.version == cache_version() for r in rows.values())


def test_recompute_passes_none_so_the_precomputed_set_is_used(pg, monkeypatch):
    (p,) = _pg_players(pg, "a")
    asked = []

    def fake(db, player, keys, *a, **k):
        asked.append(keys)
        return {"recent": _sample_views(p.id), "career": _sample_views(p.id)}

    monkeypatch.setattr(pvc, "compute_player_views_by_filter", fake)
    recompute_player_views(pg, p.id)
    assert asked == [None]


def test_recompute_deletes_stale_filtered_rows_and_overwrites_in_set_ones(pg, monkeypatch):
    (p, other) = _pg_players(pg, "a", "b")
    for pid in (p.id, other.id):
        store_player_views(pg, pid, {
            "career": _views_with(pid, 0), "career|agent:Old": _views_with(pid, 0),
            "career|role:duelist": _views_with(pid, 0), "recent|agent:Old": _views_with(pid, 0),
        })
    returned = {"recent": _views_with(p.id, 1), "career": _views_with(p.id, 2),
                "career|role:duelist": _views_with(p.id, 3)}
    monkeypatch.setattr(pvc, "compute_player_views_by_filter", lambda db, player, keys, *a, **k: returned)
    recompute_player_views(pg, p.id)
    fresh = _fresh(pg)
    rows = _rows(fresh, p.id)
    assert {s: _wins_in(r) for s, r in rows.items()} == {"recent": 1, "career": 2, "career|role:duelist": 3}
    assert set(_rows(fresh, other.id)) == {"career", "career|agent:Old", "career|role:duelist", "recent|agent:Old"}


def test_store_player_views_is_one_statement_and_one_commit(pg):
    (p,) = _pg_players(pg, "a")
    seen, stop = _count_statements(pg.get_bind(), "INSERT INTO PLAYER_VIEW_CACHE")
    try:
        store_player_views(pg, p.id, {"career": _views_with(p.id, 1), "career|role:duelist": _views_with(p.id, 2)})
    finally:
        stop()
    assert seen == {"insert": 1, "commit": 1}
    assert set(_rows(_fresh(pg), p.id)) == {"career", "career|role:duelist"}


def test_invalidate_deletes_filtered_rows_in_both_tables(pg):
    main, alt = _pg_players(pg, "main", "alt")
    link_account(pg, main.id, alt.id)
    gid = pg.query(AccountGroupMember).filter_by(player_id=main.id).one().group_id
    store_player_views(pg, main.id, {"career": _sample_views(main.id), "career|role:duelist": _sample_views(main.id)})
    pg.add(AccountGroupViewCache(group_id=gid, scope="career|role:duelist", fingerprint="f",
                                 data=_encode(_sample_views(main.id)), version=cache_version()))
    pg.commit()
    invalidate_player_cache(pg, {main.id})
    pg.commit()
    fresh = _fresh(pg)
    assert fresh.query(PlayerViewCache).filter_by(player_id=main.id).count() == 0
    assert fresh.query(AccountGroupViewCache).filter_by(group_id=gid).count() == 0


def test_get_cached_views_reads_the_keyed_row_and_blank_key_reads_career(pg):
    (p,) = _pg_players(pg, "a")
    store_player_views(pg, p.id, {
        "career": _views_with(p.id, 1), "career|role:duelist": _views_with(p.id, 2),
    })
    fresh = _fresh(pg)
    player = fresh.get(Player, p.id)
    plain = get_cached_views(fresh, player, "career")
    keyed = get_cached_views(fresh, player, "career", key="role:duelist")
    assert plain is not None and keyed is not None
    assert plain.round_win_graph is not keyed.round_win_graph
    assert get_cached_views(fresh, player, "recent", key="role:duelist") is None
    assert get_cached_views(fresh, player, "career", key="agent:Jett") is None
    # Different stored data: the wins differ (1 vs 2), seen through the rendered state graph.
    assert plain.round_win_graph != keyed.round_win_graph


def test_player_and_cached_views_join_uses_the_keyed_row(pg):
    from app.services.players import get_player_and_cached_views
    (p,) = _pg_players(pg, "a")
    store_player_views(pg, p.id, {"career|role:duelist": _views_with(p.id, 2)})
    fresh = _fresh(pg)
    assert get_player_and_cached_views(fresh, "w17-a", "career")[1] is None
    assert get_player_and_cached_views(fresh, "w17-a", "career", "role:duelist")[1] is not None
    assert get_player_and_cached_views(fresh, "w17-a", "career", "role:sentinel")[1] is None


def test_cache_hits_are_isolated_from_the_input_and_from_earlier_hits(pg):
    (p,) = _pg_players(pg, "a")
    views = _views_with(p.id, 7)
    store_player_views(pg, p.id, {"career": views})
    views.win_stats["5v5"]["win"] = 999              # caller changes its input after storing
    fresh = _fresh(pg)
    player = fresh.get(Player, p.id)
    first = get_cached_views(fresh, player, "career")
    first.profile.matches.clear()                    # and a hit's result is changed by its caller
    first.fight_ev_data["x"] = 1
    second = get_cached_views(fresh, player, "career")
    assert len(second.profile.matches) == 2 and "x" not in second.fight_ev_data
    assert _wins_in(_rows(_fresh(pg), p.id)["career"]) == 7


def test_group_prewarm_stores_every_key_under_one_fingerprint(pg, monkeypatch):
    main, alt = _pg_players(pg, "main", "alt")
    link_account(pg, main.id, alt.id)
    pg.commit()
    returned = {"recent": _views_with(main.id, 1), "career": _views_with(main.id, 2),
                "career|role:duelist": _views_with(main.id, 3), "career|agent:Jett": _views_with(main.id, 4)}
    calls = []

    def fake(db, anchor, keys, *a, **k):
        calls.append((keys, k.get("player_ids")))
        return returned

    monkeypatch.setattr(account_groups, "compute_player_views_by_filter", fake)
    prewarm_group_caches(pg, {main.id})
    assert calls == [(None, frozenset({main.id, alt.id}))]
    fresh = _fresh(pg)
    gid = fresh.query(AccountGroupMember).filter_by(player_id=main.id).one().group_id
    rows = {r.scope: r for r in fresh.query(AccountGroupViewCache).filter_by(group_id=gid).all()}
    assert set(rows) == set(returned)
    fingerprint = group_fingerprint(fresh, frozenset({main.id, alt.id}))
    assert {r.fingerprint for r in rows.values()} == {fingerprint}
    player = fresh.get(Player, main.id)
    assert get_cached_group_views(fresh, gid, "career", fingerprint, player, key="role:duelist") is not None
    assert get_cached_group_views(fresh, gid, "career", fingerprint, player, key="agent:Jett") is not None
    assert get_cached_group_views(fresh, gid, "career", fingerprint, player) is not None
    assert get_cached_group_views(fresh, gid, "career", fingerprint, player, key="role:sentinel") is None
    assert get_cached_group_views(fresh, gid, "career", "stale", player, key="role:duelist") is None
