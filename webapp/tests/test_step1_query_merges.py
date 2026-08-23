"""Step 1 (docs/player_page_render_speed.txt) merges the player-page router's
Q1+Q2 and Q3+Q4 into single round trips. No DB fixture exists in this repo
(see tests/test_player_view_cache.py's precedent), so these tests fabricate
plain in-memory model instances and a MagicMock Session standing in for the
query chain -- the goal is the SHAPE of each merge (join condition present,
404 preserved, decode delegated, the no-session short-circuit still issues
ZERO queries), not a real round trip.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.models import Friendship, Player, PlayerViewCache
from app.services.fight_ev import build_fight_ev_views_from_blocks
from app.services.friends import get_current_player_and_friendship
from app.services.player_profile_types import PlayerProfile
from app.services.player_view_cache import (
    CachedPlayerViews,
    _encode,
    cache_version,
    decode_cache_row,
)
from app.services.player_views import PlayerViews
from app.services.players import get_player_and_cached_views

_SAMPLE_PLAYER = Player(id=1, display_name="Foo#123")


def _sample_blob() -> dict:
    fight_ev = build_fight_ev_views_from_blocks([], player_id=1, draws=10)
    profile = PlayerProfile(
        player=_SAMPLE_PLAYER, overall_average_impact=0.0, overall_average_round_win_impact=0.0,
        overall_average_death_impact=0.0, matches=[],
    )
    views = PlayerViews(
        win_stats={}, kill_order_weights={}, fight_ev=fight_ev,
        profile=profile, econ_aggregates={"tier_pairs": {}, "pistol": {"win": 0, "total": 0, "ratio_sum": 0.0, "ratio_count": 0}, "loadout_buckets": {}},
    )
    return _encode(views)


# ---------------------------------------------------------------------------
# decode_cache_row: same contract as get_cached_views, given an
# already-fetched row instead of querying for one.
# ---------------------------------------------------------------------------

def test_decode_cache_row_returns_none_for_none():
    assert decode_cache_row(None, _SAMPLE_PLAYER) is None


def test_decode_cache_row_returns_none_for_version_mismatch():
    row = PlayerViewCache(player_id=1, scope="recent", data=_sample_blob(), version=cache_version() + 1)
    assert decode_cache_row(row, _SAMPLE_PLAYER) is None


def test_decode_cache_row_decodes_a_valid_row():
    row = PlayerViewCache(player_id=1, scope="recent", data=_sample_blob(), version=cache_version())
    decoded = decode_cache_row(row, _SAMPLE_PLAYER)
    assert isinstance(decoded, CachedPlayerViews)


def test_decode_cache_row_rejects_a_corrupt_blob_without_raising():
    row = PlayerViewCache(player_id=1, scope="recent", data={"not": "a valid blob"}, version=cache_version())
    assert decode_cache_row(row, _SAMPLE_PLAYER) is None


# ---------------------------------------------------------------------------
# get_player_and_cached_views: Q1+Q2 merge (Step 1a)
# ---------------------------------------------------------------------------

def _mock_db_returning(row) -> MagicMock:
    db = MagicMock()
    db.query.return_value.outerjoin.return_value.filter.return_value.one_or_none.return_value = row
    return db


def test_get_player_and_cached_views_raises_404_when_player_missing():
    db = _mock_db_returning(None)
    with pytest.raises(HTTPException) as exc_info:
        get_player_and_cached_views(db, "Nobody#000", "recent")
    assert exc_info.value.status_code == 404


def test_get_player_and_cached_views_returns_player_with_no_cache_row():
    player = Player(id=1, display_name="Foo#123")
    db = _mock_db_returning((player, None))
    returned_player, cached = get_player_and_cached_views(db, "Foo#123", "recent")
    assert returned_player is player
    assert cached is None


def test_get_player_and_cached_views_decodes_a_present_cache_row():
    player = Player(id=1, display_name="Foo#123")
    cache_row = PlayerViewCache(player_id=1, scope="recent", data=_sample_blob(), version=cache_version())
    db = _mock_db_returning((player, cache_row))
    returned_player, cached = get_player_and_cached_views(db, "Foo#123", "recent")
    assert returned_player is player
    assert isinstance(cached, CachedPlayerViews)


def test_get_player_and_cached_views_treats_stale_version_as_a_miss():
    player = Player(id=1, display_name="Foo#123")
    cache_row = PlayerViewCache(player_id=1, scope="recent", data=_sample_blob(), version=cache_version() + 1)
    db = _mock_db_returning((player, cache_row))
    _, cached = get_player_and_cached_views(db, "Foo#123", "recent")
    assert cached is None


def test_get_player_and_cached_views_joins_on_scope_and_player_id():
    player = Player(id=1, display_name="Foo#123")
    db = _mock_db_returning((player, None))
    get_player_and_cached_views(db, "Foo#123", "career")
    outerjoin_call = db.query.return_value.outerjoin
    assert outerjoin_call.called
    # The join's ON-clause is passed as the second positional arg -- assert
    # it's a real SQLAlchemy boolean clause (an AND of two comparisons),
    # not e.g. an accidentally-dropped filter.
    on_clause = outerjoin_call.call_args[0][1]
    compiled = str(on_clause.compile(compile_kwargs={"literal_binds": True}))
    assert "player_view_cache.player_id" in compiled
    assert "player_view_cache.scope" in compiled
    assert "'career'" in compiled


# ---------------------------------------------------------------------------
# get_current_player_and_friendship: Q3+Q4 merge (Step 1b)
# ---------------------------------------------------------------------------

def test_no_session_short_circuits_with_zero_queries():
    db = MagicMock()
    result = get_current_player_and_friendship(db, None, target_player_id=99)
    assert result == (None, False)
    db.query.assert_not_called()


def test_session_player_not_found_returns_logged_out():
    db = _mock_db_returning(None)
    result = get_current_player_and_friendship(db, session_player_id=7, target_player_id=99)
    assert result == (None, False)


def test_not_a_friend_returns_false():
    current_player = Player(id=7, display_name="Me#123")
    db = _mock_db_returning((current_player, None))
    player, is_friend = get_current_player_and_friendship(db, session_player_id=7, target_player_id=99)
    assert player is current_player
    assert is_friend is False


def test_is_a_friend_returns_true():
    current_player = Player(id=7, display_name="Me#123")
    friendship = Friendship(owner_player_id=7, friend_player_id=99)
    db = _mock_db_returning((current_player, friendship))
    player, is_friend = get_current_player_and_friendship(db, session_player_id=7, target_player_id=99)
    assert player is current_player
    assert is_friend is True


def test_viewing_own_profile_is_never_a_friend_even_if_a_row_matched():
    # Defensive guard, mirroring the original router's explicit
    # `current_player.id != player.id` check -- add_friend() itself refuses
    # to create a self-friendship, but the merged query shouldn't rely on
    # that alone.
    current_player = Player(id=7, display_name="Me#123")
    self_friendship = Friendship(owner_player_id=7, friend_player_id=7)
    db = _mock_db_returning((current_player, self_friendship))
    _, is_friend = get_current_player_and_friendship(db, session_player_id=7, target_player_id=7)
    assert is_friend is False
