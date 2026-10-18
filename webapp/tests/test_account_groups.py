from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AccountGroup,
    AccountGroupMember,
    AccountGroupViewCache,
    Friendship,
    ImpactScore,
    KillEvent,
    Match,
    MatchPlayer,
    Player,
    PlayerViewCache,
    Round,
    RoundPlayerStat,
)
from app.models.match import MatchSource, Team
from app.services import account_groups
from app.services.account_groups import (
    get_cached_group_views,
    group_fingerprint,
    link_account,
    list_group_members,
    prewarm_group_caches,
    set_account_label,
    store_group_views,
    unlink_account,
)
from app.services.player_data import load_player_match_data
from app.services.player_view_cache import filter_cached_player_ids, invalidate_player_cache
from app.services.recent_match_rows import recent_form_entries
from tests.test_player_view_cache import _sample_views

@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):   # impact_scores.trade_detail
    return "JSON"


TABLES = [
    Player, Match, MatchPlayer, Round, RoundPlayerStat, KillEvent, ImpactScore, Friendship,
    PlayerViewCache, AccountGroup, AccountGroupMember, AccountGroupViewCache,
]


@pytest.fixture
def db(tmp_path):
    eng = create_engine("sqlite:///" + str(tmp_path / "groups.sqlite"))
    Base.metadata.create_all(eng, tables=[t.__table__ for t in TABLES])
    with sessionmaker(bind=eng)() as session:
        yield session
    eng.dispose()


def _players(db, *names):
    players = [Player(display_name=n) for n in names]
    db.add_all(players)
    db.flush()
    return players


def _play(db, player, day, external_id):
    match = Match(
        external_id=external_id, source=MatchSource.SCRAPED, map_name="Ascent",
        played_at=datetime(2026, 10, day, tzinfo=timezone.utc),
    )
    db.add(match)
    db.flush()
    mp = MatchPlayer(match_id=match.id, player_id=player.id, agent="Jett", team=Team.TEAM_1)
    db.add(mp)
    db.flush()
    return mp


def _ids(members):
    return [m.player_id for m in members]


def test_linking_starts_a_group_and_both_accounts_see_it(db):
    main, alt, other = _players(db, "main#1", "alt#2", "other#3")
    assert link_account(db, main.id, alt.id) is None
    db.commit()
    assert _ids(list_group_members(db, main.id)) == [main.id, alt.id]
    assert _ids(list_group_members(db, alt.id)) == [main.id, alt.id]
    assert list_group_members(db, other.id) == []


def test_the_first_account_to_link_is_the_main_and_only_it_adds(db):
    main, alt, third = _players(db, "main#1", "alt#2", "third#3")
    link_account(db, main.id, alt.id)
    db.commit()
    members = list_group_members(db, alt.id)
    assert [m.is_main for m in members] == [True, False]

    error = link_account(db, alt.id, third.id)
    assert error is not None and "main#1" in error
    assert link_account(db, main.id, third.id) is None
    db.commit()
    assert _ids(list_group_members(db, main.id)) == [main.id, alt.id, third.id]


def test_a_linked_account_cant_be_claimed_by_anyone_else(db):
    a, b, c, d = _players(db, "a#1", "b#2", "c#3", "d#4")
    link_account(db, a.id, b.id)
    db.commit()
    # Neither a sub nor a main can be pulled into another group, by a free account or another main.
    for owner in (c, d):
        for claimed in (a, b):
            error = link_account(db, owner.id, claimed.id)
            assert error is not None and claimed.display_name in error
    link_account(db, c.id, d.id)
    db.commit()
    assert link_account(db, c.id, b.id) is not None
    assert _ids(list_group_members(db, a.id)) == [a.id, b.id]
    assert _ids(list_group_members(db, c.id)) == [c.id, d.id]
    assert account_groups.linked_display_names(db) == {"a#1", "b#2", "c#3", "d#4"}


def test_linking_yourself_or_an_existing_member_is_a_no_op(db):
    main, alt = _players(db, "main#1", "alt#2")
    assert link_account(db, main.id, main.id) is None
    link_account(db, main.id, alt.id)
    db.commit()
    assert link_account(db, main.id, alt.id) is None
    db.commit()
    assert len(list_group_members(db, main.id)) == 2


@pytest.mark.parametrize("owner_first", [True, False])
def test_a_new_groups_member_rows_are_inserted_in_ascending_player_id(db, owner_first):
    from sqlalchemy import event

    low, high = _players(db, "low#1", "high#2")
    owner, other = (low, high) if owner_first else (high, low)
    inserted = []

    def record(mapper, connection, target):
        inserted.append(target.player_id)

    event.listen(AccountGroupMember, "before_insert", record)
    try:
        assert link_account(db, owner.id, other.id) is None
        db.commit()
    finally:
        event.remove(AccountGroupMember, "before_insert", record)
    assert inserted == [low.id, high.id]


def test_a_sub_can_leave_but_not_remove_another_and_the_main_cant_leave(db):
    main, alt, third, stranger = _players(db, "main#1", "alt#2", "third#3", "stranger#4")
    link_account(db, main.id, alt.id)
    link_account(db, main.id, third.id)
    db.commit()
    assert unlink_account(db, alt.id, third.id) is False
    assert unlink_account(db, stranger.id, alt.id) is False
    assert unlink_account(db, main.id, main.id) is False
    assert unlink_account(db, alt.id, main.id) is False
    assert unlink_account(db, alt.id, alt.id) is True
    db.commit()
    assert _ids(list_group_members(db, main.id)) == [main.id, third.id]
    # Once it has left, it's free to be linked again.
    assert link_account(db, stranger.id, alt.id) is None


def test_the_main_hands_its_role_to_a_sub(db):
    main, alt, third = _players(db, "main#1", "alt#2", "third#3")
    link_account(db, main.id, alt.id)
    link_account(db, main.id, third.id)
    db.commit()
    assert account_groups.make_main_account(db, alt.id, third.id) is False
    assert account_groups.make_main_account(db, main.id, third.id) is True
    db.commit()
    assert _ids(list_group_members(db, main.id)) == [third.id, main.id, alt.id]
    assert link_account(db, main.id, _players(db, "new#4")[0].id) is not None
    assert unlink_account(db, third.id, main.id) is True


def test_removing_the_second_last_account_deletes_the_group(db):
    main, alt = _players(db, "main#1", "alt#2")
    link_account(db, main.id, alt.id)
    db.commit()
    assert unlink_account(db, main.id, alt.id) is True
    db.commit()
    assert list_group_members(db, main.id) == []
    assert db.query(AccountGroup).count() == 0


def test_only_your_own_group_can_be_edited(db):
    a, b, c = _players(db, "a#1", "b#2", "c#3")
    link_account(db, a.id, b.id)
    db.commit()
    assert unlink_account(db, c.id, b.id) is False
    assert set_account_label(db, c.id, b.id, "nope") is False
    assert set_account_label(db, b.id, b.id, "a sub can't") is False
    assert set_account_label(db, a.id, b.id, "  Duelist alt  ") is True
    db.commit()
    assert db.get(AccountGroupMember, b.id).label == "Duelist alt"
    set_account_label(db, a.id, b.id, "   ")
    db.commit()
    assert db.get(AccountGroupMember, b.id).label is None


def test_combined_load_takes_every_accounts_matches_newest_first(db):
    main, alt, other = _players(db, "main#1", "alt#2", "other#3")
    _play(db, main, 1, "m1")
    _play(db, alt, 3, "a3")
    _play(db, main, 2, "m2")
    _play(db, other, 4, "o4")
    db.commit()

    single = load_player_match_data(db, main)
    combined = load_player_match_data(db, main, player_ids=frozenset({main.id, alt.id}))
    assert [mp.match.external_id for mp in single] == ["m2", "m1"]
    assert [mp.match.external_id for mp in combined] == ["a3", "m2", "m1"]
    assert [mp.match.external_id for mp in load_player_match_data(db, main, 2, frozenset({main.id, alt.id}))] == [
        "a3", "m2",
    ]

    entries = recent_form_entries(db, main.id, player_ids=frozenset({main.id, alt.id}))
    assert [e.external_id for e in entries] == ["a3", "m2", "m1"]


def _also_play(db, player, match_id):
    mp = MatchPlayer(match_id=match_id, player_id=player.id, agent="Sage", team=Team.TEAM_2)
    db.add(mp)
    db.flush()
    return mp


def test_combined_load_counts_a_shared_match_once_with_the_lowest_player_id(db):
    main, alt = _players(db, "main#1", "alt#2")
    shared = _play(db, alt, 5, "shared")
    _also_play(db, main, shared.match_id)
    _play(db, main, 1, "m1")
    db.commit()
    ids = frozenset({main.id, alt.id})
    rows = load_player_match_data(db, main, player_ids=ids)
    assert [mp.match.external_id for mp in rows] == ["shared", "m1"]
    assert rows[0].player_id == min(main.id, alt.id)
    # The single-account load is unchanged.
    assert [mp.match.external_id for mp in load_player_match_data(db, alt)] == ["shared"]


def test_a_shared_match_does_not_use_up_a_slot_of_the_limit(db):
    main, alt = _players(db, "main#1", "alt#2")
    for day in range(1, 29):
        _play(db, main if day % 2 else alt, day, f"x{day}")
    shared = _play(db, alt, 29, "s29")
    _also_play(db, main, shared.match_id)
    _play(db, main, 30, "x30")
    _play(db, alt, 31, "x31")
    db.commit()
    rows = load_player_match_data(db, main, 30, frozenset({main.id, alt.id}))
    assert len(rows) == 30
    assert len({mp.match_id for mp in rows}) == 30


def test_recent_form_entries_and_baseline_count_a_shared_match_once(db):
    from app.services.recent_match_rows import build_recent_match_rows
    from app.services.player_data import one_row_per_match

    main, alt = _players(db, "main#1", "alt#2")
    shared = _play(db, alt, 5, "shared")
    _also_play(db, main, shared.match_id)
    db.commit()
    ids = frozenset({main.id, alt.id})
    entries = recent_form_entries(db, main.id, player_ids=ids)
    assert [e.external_id for e in entries] == ["shared"]
    assert len(build_recent_match_rows(db, main.id, entries, ids)) == 1
    kept = db.query(MatchPlayer).filter(MatchPlayer.player_id.in_(ids), one_row_per_match(ids)).all()
    assert [mp.player_id for mp in kept] == [min(main.id, alt.id)]


def _round_with(db, mp, kills, impact=None):
    round_row = db.query(Round).filter_by(match_id=mp.match_id).first()
    if round_row is None:
        round_row = Round(match_id=mp.match_id, round_number=1)
        db.add(round_row)
        db.flush()
    db.add(RoundPlayerStat(round_id=round_row.id, match_player_id=mp.id, kills=kills, deaths=0, assists=0))
    if impact is not None:
        db.add(ImpactScore(match_player_id=mp.id, round_id=round_row.id, kill_impact=impact, death_impact=0, impact=impact))
    db.flush()


def test_recent_rows_show_the_lowest_accounts_row_of_a_shared_match(db):
    from app.services.recent_match_rows import build_recent_match_rows

    main, alt = _players(db, "main#1", "alt#2")
    low, high = (main, alt) if main.id < alt.id else (alt, main)
    shared = _play(db, high, 5, "shared")
    low_mp = _also_play(db, low, shared.match_id)
    _round_with(db, low_mp, kills=7)
    _round_with(db, shared, kills=2)
    db.commit()
    ids = frozenset({main.id, alt.id})
    entries = recent_form_entries(db, main.id, player_ids=ids)
    rows = build_recent_match_rows(db, main.id, entries, ids)
    assert [r.kills for r in rows] == [7]


def test_the_baseline_counts_a_shared_match_once(db, monkeypatch):
    from app.services import recent_match_rows
    from app.services.recent_match_rows import MIN_BASELINE_MATCHES, build_recent_match_rows

    main, alt = _players(db, "main#1", "alt#2")
    distinct = MIN_BASELINE_MATCHES - 1
    for day in range(1, distinct):
        _round_with(db, _play(db, main, day, f"m{day}"), kills=1, impact=1)
    shared = _play(db, alt, distinct, "shared")
    _round_with(db, shared, kills=1, impact=1)
    _round_with(db, _also_play(db, main, shared.match_id), kills=1, impact=1)
    db.commit()
    calls = []
    real = recent_match_rows._quantile
    monkeypatch.setattr(recent_match_rows, "_quantile", lambda v, q: calls.append(len(v)) or real(v, q))
    ids = frozenset({main.id, alt.id})
    entries = recent_form_entries(db, main.id, player_ids=ids)
    assert len(entries) == distinct
    build_recent_match_rows(db, main.id, entries, ids)
    assert calls == []   # 9 distinct matches: below the baseline minimum, so no thresholds


def test_fingerprint_changes_with_members_matches_scores_and_friends(db):
    main, alt, friend = _players(db, "main#1", "alt#2", "friend#3")
    ids = frozenset({main.id, alt.id})
    link_account(db, main.id, alt.id)   # the friends part is the circle, which needs the real link
    db.commit()
    before = group_fingerprint(db, ids)
    assert group_fingerprint(db, ids) == before

    mp = _play(db, alt, 1, "a1")
    db.commit()
    after_match = group_fingerprint(db, ids)
    assert after_match != before

    round_row = Round(match_id=mp.match_id, round_number=1)
    db.add(round_row)
    db.flush()
    db.add(ImpactScore(match_player_id=mp.id, round_id=round_row.id, kill_impact=5, death_impact=0, impact=5))
    db.commit()
    after_score = group_fingerprint(db, ids)
    assert after_score != after_match

    db.add(Friendship(owner_player_id=alt.id, friend_player_id=friend.id))
    db.commit()
    assert group_fingerprint(db, ids) != after_score
    assert group_fingerprint(db, frozenset({main.id})) != group_fingerprint(db, ids)


def test_group_cache_round_trips_and_misses_on_a_new_fingerprint(db):
    main, alt = _players(db, "main#1", "alt#2")
    link_account(db, main.id, alt.id)
    db.commit()
    group_id = list_group_members(db, main.id)[0].group_id

    assert get_cached_group_views(db, group_id, "recent", "fp1", main) is None
    store_group_views(db, group_id, "recent", "fp1", _sample_views(main.id))
    hit = get_cached_group_views(db, group_id, "recent", "fp1", main)
    assert hit is not None and hit.profile.player is main
    assert get_cached_group_views(db, group_id, "recent", "fp2", main) is None
    assert get_cached_group_views(db, group_id, "career", "fp1", main) is None

    store_group_views(db, group_id, "recent", "fp2", _sample_views(main.id))
    assert get_cached_group_views(db, group_id, "recent", "fp2", main) is not None


def test_membership_changes_and_player_invalidation_clear_group_rows(db):
    a, b, c = _players(db, "a#1", "b#2", "c#3")
    link_account(db, a.id, b.id)
    db.commit()
    group_id = list_group_members(db, a.id)[0].group_id

    store_group_views(db, group_id, "recent", "fp", _sample_views(a.id))
    link_account(db, a.id, c.id)
    db.commit()
    assert db.query(AccountGroupViewCache).count() == 0

    store_group_views(db, group_id, "career", "fp", _sample_views(a.id))
    invalidate_player_cache(db, {c.id})
    db.commit()
    assert db.query(AccountGroupViewCache).count() == 0


def test_an_alt_with_only_a_combined_row_still_counts_as_cached(db):
    main, alt, other = _players(db, "main#1", "alt#2", "other#3")
    link_account(db, main.id, alt.id)
    db.commit()
    assert filter_cached_player_ids(db, {main.id, alt.id, other.id}) == set()
    group_id = list_group_members(db, main.id)[0].group_id
    store_group_views(db, group_id, "recent", "fp", _sample_views(main.id))
    assert filter_cached_player_ids(db, {main.id, alt.id, other.id}) == {main.id, alt.id}


def test_prewarm_writes_both_scopes_under_the_current_fingerprint(db, monkeypatch):
    main, alt, other = _players(db, "main#1", "alt#2", "other#3")
    link_account(db, main.id, alt.id)
    db.commit()
    group_id = list_group_members(db, main.id)[0].group_id
    seen = []

    def fake_by_scope(db_, anchor, player_ids=None):
        seen.append((anchor.id, player_ids))
        return {"recent": _sample_views(anchor.id), "career": _sample_views(anchor.id)}

    monkeypatch.setattr(account_groups, "compute_player_views_by_scope", fake_by_scope)
    prewarm_group_caches(db, {alt.id, other.id})
    assert seen == [(main.id, frozenset({main.id, alt.id}))]
    fingerprint = group_fingerprint(db, frozenset({main.id, alt.id}))
    for scope in ("recent", "career"):
        assert get_cached_group_views(db, group_id, scope, fingerprint, alt) is not None


def test_a_bad_blob_is_a_miss_not_an_error(db, monkeypatch):
    main, alt = _players(db, "main#1", "alt#2")
    link_account(db, main.id, alt.id)
    db.commit()
    group_id = list_group_members(db, main.id)[0].group_id
    store_group_views(db, group_id, "recent", "fp", _sample_views(main.id))
    row = db.get(AccountGroupViewCache, (group_id, "recent"))
    row.data = {"not": "a blob"}
    db.commit()
    assert get_cached_group_views(db, group_id, "recent", "fp", main) is None

    monkeypatch.setattr(account_groups, "cache_version", lambda: -1)
    store_group_views(db, group_id, "recent", "fp", _sample_views(main.id))
    monkeypatch.undo()
    assert get_cached_group_views(db, group_id, "recent", "fp", main) is None
