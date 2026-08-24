"""Read/write/invalidate layer for the single-row site_stats_cache table --
the "All Players" tab's cache. Same best-effort contract as
app.services.player_view_cache: a miss, a stale version, or a corrupt blob all
degrade to a live recompute (app.services.site_stats), never a 500.

The row holds a dict of independent canonical aggregates, one key per
whole-database stat shown on that tab (pistol_match_stats,
pistol_win_followup_eco, pistol_round_combos) -- adding another stat later
means adding another key to this dict, not redesigning the cache.
"""

import itertools
import logging

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.site_stats_cache import SITE_STATS_CACHE_ROW_ID, SiteStatsCache
from app.services.eco_followup import ECO_NUM_BUCKETS
from app.services.round_combo_stats import FIRST_HALF_ROUNDS, FULL_ROUNDS

logger = logging.getLogger(__name__)

SITE_STATS_CACHE_SCHEMA_VERSION = 4
# Bump when the shape of the stored blob changes (a stat's aggregate keys
# change, or a stat is renamed/removed), or when compute_pistol_match_stats /
# compute_pistol_win_followup_eco / compute_round_combo_stats / compute_map_side_stats
# (or any other stat this blob covers) changes its round-exclusion/aggregation
# rules. There's no separate calculation-version constant to fold in here the
# way player_view_cache folds in IMPACT_CALCULATION_VERSION etc. -- every stat
# this cache covers reads raw Match/Round/MatchPlayer rows directly, with no
# intermediate versioned calculation of its own.
#
# v2: added pistol_win_followup_eco (team-eco-after-a-pistol-win buckets,
# both "friends" and "all" variants) alongside pistol_match_stats.
# v3: added pistol_round_combos (match-win rate by pistol/round-2/round-13/
# round-14 W-L combination, "first_half" and "full" granularities, both
# "friends" and "all" variants).
# v4: added map_side_stats (per-map attacker vs. defender round-win rate,
# "friends" and "all" variants).

_PISTOL_MATCH_STATS_BUCKET_PREFIXES = ("lost_both", "won_one", "won_both")
_PISTOL_MATCH_STATS_KEYS = frozenset(
    f"{prefix}_{suffix}" for prefix in _PISTOL_MATCH_STATS_BUCKET_PREFIXES for suffix in ("total", "wins")
)


def _is_nonneg_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate_pistol_match_stats(stats: object) -> bool:
    if not isinstance(stats, dict) or set(stats.keys()) != _PISTOL_MATCH_STATS_KEYS:
        return False
    if not all(_is_nonneg_int(stats[k]) for k in _PISTOL_MATCH_STATS_KEYS):
        return False
    return all(
        stats[f"{prefix}_wins"] <= stats[f"{prefix}_total"] for prefix in _PISTOL_MATCH_STATS_BUCKET_PREFIXES
    )


def _validate_eco_bucket_row(row: object) -> bool:
    if not isinstance(row, list) or len(row) != 5:
        return False
    idx, total, win, kills_ratio_sum, wins_ratio_sum = row
    if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < ECO_NUM_BUCKETS):
        return False
    if not _is_nonneg_int(total) or not _is_nonneg_int(win) or win > total:
        return False
    if not _is_number(kills_ratio_sum) or not _is_number(wins_ratio_sum):
        return False
    return kills_ratio_sum >= 0 and wins_ratio_sum >= 0


def _validate_eco_followup_variant(variant: object) -> bool:
    if not isinstance(variant, dict) or set(variant.keys()) != {"buckets"}:
        return False
    buckets = variant["buckets"]
    return isinstance(buckets, list) and all(_validate_eco_bucket_row(row) for row in buckets)


def _validate_pistol_win_followup_eco(data: object) -> bool:
    if not isinstance(data, dict) or set(data.keys()) != {"friends", "all"}:
        return False
    return _validate_eco_followup_variant(data["friends"]) and _validate_eco_followup_variant(data["all"])


_ROUND_COMBO_KEY_SETS = {
    "first_half": {"".join(bits) for bits in itertools.product("WL", repeat=len(FIRST_HALF_ROUNDS))},
    "full": {"".join(bits) for bits in itertools.product("WL", repeat=len(FULL_ROUNDS))},
}


def _validate_round_combo_bucket(bucket: object) -> bool:
    if not isinstance(bucket, dict) or set(bucket.keys()) != {"total", "win"}:
        return False
    return _is_nonneg_int(bucket["total"]) and _is_nonneg_int(bucket["win"]) and bucket["win"] <= bucket["total"]


def _validate_round_combo_granularity(granularity: object, valid_keys: set[str]) -> bool:
    if not isinstance(granularity, dict):
        return False
    if not set(granularity.keys()) <= valid_keys:
        return False
    return all(_validate_round_combo_bucket(bucket) for bucket in granularity.values())


def _validate_round_combo_variant(variant: object) -> bool:
    if not isinstance(variant, dict) or set(variant.keys()) != {"first_half", "full"}:
        return False
    return _validate_round_combo_granularity(
        variant["first_half"], _ROUND_COMBO_KEY_SETS["first_half"]
    ) and _validate_round_combo_granularity(variant["full"], _ROUND_COMBO_KEY_SETS["full"])


def _validate_pistol_round_combos(data: object) -> bool:
    if not isinstance(data, dict) or set(data.keys()) != {"friends", "all"}:
        return False
    return _validate_round_combo_variant(data["friends"]) and _validate_round_combo_variant(data["all"])


_MAP_SIDE_BUCKET_KEYS = {"matches", "attack_wins", "defense_wins"}


def _validate_map_side_bucket(bucket: object) -> bool:
    if not isinstance(bucket, dict) or set(bucket.keys()) != _MAP_SIDE_BUCKET_KEYS:
        return False
    return all(_is_nonneg_int(bucket[k]) for k in _MAP_SIDE_BUCKET_KEYS)


def _validate_map_side_variant(variant: object) -> bool:
    if not isinstance(variant, dict):
        return False
    return all(
        isinstance(map_name, str) and _validate_map_side_bucket(bucket)
        for map_name, bucket in variant.items()
    )


def _validate_map_side_stats(data: object) -> bool:
    if not isinstance(data, dict) or set(data.keys()) != {"friends", "all"}:
        return False
    return _validate_map_side_variant(data["friends"]) and _validate_map_side_variant(data["all"])


def _validate_blob(data: object) -> bool:
    if not isinstance(data, dict) or set(data.keys()) != {
        "pistol_match_stats", "pistol_win_followup_eco", "pistol_round_combos", "map_side_stats",
    }:
        return False
    if not _validate_pistol_match_stats(data["pistol_match_stats"]):
        return False
    if not _validate_pistol_win_followup_eco(data["pistol_win_followup_eco"]):
        return False
    if not _validate_pistol_round_combos(data["pistol_round_combos"]):
        return False
    return _validate_map_side_stats(data["map_side_stats"])


def get_site_stats_cache(db: Session) -> dict | None:
    """None on a miss, a version mismatch, or a corrupt blob -- the caller
    then computes live (and, on success, writes back through store_site_stats_cache)."""
    row = db.query(SiteStatsCache).filter_by(id=SITE_STATS_CACHE_ROW_ID).one_or_none()
    if row is None:
        return None
    if row.version != SITE_STATS_CACHE_SCHEMA_VERSION:
        return None
    if not _validate_blob(row.data):
        logger.warning("site_stats_cache: invalid blob")
        return None
    return row.data


def store_site_stats_cache(db: Session, data: dict) -> None:
    """Upserts the singleton row and commits."""
    stmt = (
        pg_insert(SiteStatsCache.__table__)
        .values(id=SITE_STATS_CACHE_ROW_ID, data=data, version=SITE_STATS_CACHE_SCHEMA_VERSION, updated_at=func.now())
        .on_conflict_do_update(
            index_elements=[SiteStatsCache.id],
            set_={"data": data, "version": SITE_STATS_CACHE_SCHEMA_VERSION, "updated_at": func.now()},
        )
    )
    db.execute(stmt)
    db.commit()


def invalidate_site_stats_cache(db: Session) -> None:
    """DELETE the singleton row. Does NOT commit -- the caller commits, so
    the delete lands in the same transaction as whatever match ingestion
    triggered it (same contract as app.services.player_view_cache.
    invalidate_player_cache)."""
    db.query(SiteStatsCache).filter_by(id=SITE_STATS_CACHE_ROW_ID).delete(synchronize_session=False)
