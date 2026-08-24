from app.models import PlayerViewCache
from app.services.player_view_cache import cache_version
from app.services.site_stats import (
    _empty_pistol_match_stats,
    _merge_pistol_match_stats,
    _raw_pistol_stats_from_cache_row,
)


def test_empty_pistol_match_stats_has_zeroed_canonical_keys():
    empty = _empty_pistol_match_stats()
    assert empty == {
        "lost_both_total": 0, "lost_both_wins": 0,
        "won_one_total": 0, "won_one_wins": 0,
        "won_both_total": 0, "won_both_wins": 0,
    }


def test_merge_pistol_match_stats_sums_buckets_across_players():
    alice = {
        "lost_both_total": 2, "lost_both_wins": 1,
        "won_one_total": 5, "won_one_wins": 3,
        "won_both_total": 1, "won_both_wins": 1,
    }
    bob = {
        "lost_both_total": 1, "lost_both_wins": 0,
        "won_one_total": 3, "won_one_wins": 2,
        "won_both_total": 4, "won_both_wins": 3,
    }

    merged = _merge_pistol_match_stats([alice, bob])

    assert merged == {
        "lost_both_total": 3, "lost_both_wins": 1,
        "won_one_total": 8, "won_one_wins": 5,
        "won_both_total": 5, "won_both_wins": 4,
    }


def test_merge_pistol_match_stats_with_no_players_is_all_zero():
    assert _merge_pistol_match_stats([]) == _empty_pistol_match_stats()


def test_raw_pistol_stats_from_cache_row_reads_the_stored_dict():
    row = PlayerViewCache(
        player_id=1, scope="career", version=cache_version(),
        data={"pistol_match_stats": {"lost_both_total": 4, "lost_both_wins": 2}},
    )
    assert _raw_pistol_stats_from_cache_row(row) == {"lost_both_total": 4, "lost_both_wins": 2}


def test_raw_pistol_stats_from_cache_row_is_none_on_missing_row():
    assert _raw_pistol_stats_from_cache_row(None) is None


def test_raw_pistol_stats_from_cache_row_is_none_on_stale_version():
    """A version bump (e.g. PLAYER_VIEW_CACHE_SCHEMA_VERSION) must make this
    look like a miss, not silently sum stats computed under old rules."""
    row = PlayerViewCache(
        player_id=1, scope="career", version=cache_version() - 1,
        data={"pistol_match_stats": {"lost_both_total": 4, "lost_both_wins": 2}},
    )
    assert _raw_pistol_stats_from_cache_row(row) is None


def test_raw_pistol_stats_from_cache_row_is_none_on_corrupt_blob():
    row = PlayerViewCache(player_id=1, scope="career", version=cache_version(), data={"unexpected": True})
    assert _raw_pistol_stats_from_cache_row(row) is None
