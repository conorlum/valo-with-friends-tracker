"""Aggregate stats computed across many players at once (a "whole group" or
"whole database" view), rather than one player's own profile.

The design goal is to stay cheap regardless of scope: compute_pistol_match_stats
(app.services.player_profile_types) takes an arbitrary list[MatchPlayer] and
returns mutually-exclusive win/total buckets, which are simply additive across
players -- there's no per-player identity in the output. That means:

  - "friends" scope reuses each roster player's ALREADY-CACHED
    player_view_cache.pistol_match_stats row (recomputed on every ingest/
    prewarm anyway, see app.services.player_view_cache) and just sums the raw
    ints -- no replay, no per-player DB round trip beyond one IN query.
  - "all players" scope hands compute_pistol_match_stats every MatchPlayer row
    in the DB in one query (rounds eager-loaded, kill_events/player_stats
    NOT -- this stat never looks at them), rather than looping per player.

app.services.eco_followup's pistol-win-followup-eco stat is different: it's
inherently match+team scoped (not a per-player personal stat), and even its
"friends" variant needs a full scan of every match (filtered to ones where a
roster player was on the winning team) -- there's no cheap per-player cache
row to sum the way pistol_match_stats has. So BOTH variants of that stat are
computed together, in the SAME one-query match load used for the "all
players" pistol_match_stats above, and both live in site_stats_cache (a
single row covering every stat on the /stats page's "All Players" tab, plus
whichever variant of eco_followup "Friends" needs) via get_site_stats -- see
app.services.site_stats_cache for the read/write/invalidate contract, and
app.adapters.trackergg_browserstate_source / scripts/ingest_demo_match.py /
scripts/seed_demo_matches.py for where it's invalidated (any new match
changes both cached stats, unconditionally -- unlike player_view_cache's
per-player invalidation, there's no "was this player already cached" gate).
"""

import json
import logging
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models import Match, MatchPlayer, Player, PlayerViewCache, Round
from app.services.eco_followup import compute_pistol_win_followup_eco
from app.services.halftime_conversion_stats import compute_halftime_conversion_stats
from app.services.map_side_stats import compute_map_side_stats
from app.services.player_data import RECENT_MATCH_LIMIT
from app.services.player_profile_types import compute_pistol_match_stats
from app.services.player_view_cache import cache_version
from app.services.round_combo_stats import compute_round_combo_stats
from app.services.round_streak_stats import compute_round_streak_stats
from app.services.score_reached_stats import compute_score_reached_stats
from app.services.site_stats_cache import get_site_stats_cache, store_site_stats_cache

logger = logging.getLogger(__name__)

ROSTER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "tracked_players.json"


def _empty_pistol_match_stats() -> dict[str, int]:
    """Zero-valued aggregate with the canonical key set -- pulled from
    compute_pistol_match_stats itself (rather than a hardcoded key list here)
    so this module never drifts from that function's own bucket shape."""
    return compute_pistol_match_stats([])


def _merge_pistol_match_stats(per_player: list[dict[str, int]]) -> dict[str, int]:
    total = _empty_pistol_match_stats()
    for stats in per_player:
        for key in total:
            total[key] += stats.get(key, 0)
    return total


def resolve_roster_player_ids(db: Session) -> list[int]:
    """tracked_players.json's "Name#Tag" entries -> Player.id. Player.display_name
    stores the FULL "Name#Tag" string (that's what trackergg_browserstate_source's
    _get_or_create_player is given), matching the roster file's own format
    exactly -- so this is a case-insensitive exact match, not a fuzzy one. An
    entry with no matching Player row (not yet ingested) is silently skipped --
    this is a best-effort roster lookup, not a data-integrity check."""
    riot_ids = json.loads(ROSTER_PATH.read_text())
    if not riot_ids:
        return []
    rows = (
        db.query(Player.id)
        .filter(func.lower(Player.display_name).in_([riot_id.lower() for riot_id in riot_ids]))
        .all()
    )
    return [pid for (pid,) in rows]


def _load_match_players_for_pistol_stats(
    db: Session, player_id: int, match_limit: int | None
) -> list[MatchPlayer]:
    """Lightweight stand-in for app.services.player_data.load_player_match_data:
    compute_pistol_match_stats only ever reads mp.team and mp.match.rounds
    (round_number/outcome) plus match.team1_rounds_won/team2_rounds_won, so
    this skips that function's much heavier kill_events/player_stats/teammate
    eager loads entirely."""
    query = (
        db.query(MatchPlayer)
        .filter_by(player_id=player_id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .options(selectinload(MatchPlayer.match).selectinload(Match.rounds))
        .order_by(Match.played_at.desc().nullsfirst(), Match.id.desc())
    )
    if match_limit is not None:
        query = query.limit(match_limit)
    return query.all()


def _raw_pistol_stats_from_cache_row(row: PlayerViewCache | None) -> dict[str, int] | None:
    """None on any miss/version-mismatch/corruption -- the caller then falls
    back to a live (still cheap) computation for that one player, same
    best-effort contract as app.services.player_view_cache."""
    if row is None or row.version != cache_version():
        return None
    data = row.data
    if not isinstance(data, dict):
        return None
    stats = data.get("pistol_match_stats")
    return stats if isinstance(stats, dict) else None


def compute_roster_pistol_match_stats(db: Session, scope: str) -> dict[str, int]:
    """Sum of every tracked-roster player's OWN pistol_match_stats aggregate
    (their personal "pistols won -> did my team win the match" bucket), read
    from player_view_cache where possible. This double-counts a match where
    two roster friends were teammates (each contributes their own row) --
    consistent with each input being a per-player personal stat, not a
    per-match dedup."""
    match_limit = RECENT_MATCH_LIMIT if scope == "recent" else None
    player_ids = resolve_roster_player_ids(db)
    if not player_ids:
        return _empty_pistol_match_stats()

    cache_rows = {
        row.player_id: row
        for row in db.query(PlayerViewCache)
        .filter(PlayerViewCache.player_id.in_(player_ids), PlayerViewCache.scope == scope)
        .all()
    }

    per_player: list[dict[str, int]] = []
    for player_id in player_ids:
        raw = _raw_pistol_stats_from_cache_row(cache_rows.get(player_id))
        if raw is None:
            match_players = _load_match_players_for_pistol_stats(db, player_id, match_limit)
            raw = compute_pistol_match_stats(match_players)
        per_player.append(raw)

    return _merge_pistol_match_stats(per_player)


def _load_all_matches(db: Session) -> list[Match]:
    """Every Match row in the DB, with match_players and rounds(+player_stats)
    eager-loaded -- the one query shared by every stat this module caches
    site-wide. kill_events are NOT loaded: neither compute_pistol_match_stats
    nor compute_pistol_win_followup_eco looks at them."""
    return (
        db.query(Match)
        .options(
            selectinload(Match.match_players),
            selectinload(Match.rounds).selectinload(Round.player_stats),
        )
        .all()
    )


def compute_all_players_pistol_match_stats(db: Session) -> dict[str, int]:
    """Every MatchPlayer row in the DB -- career/all-time only (a per-player
    "recent 30" window doesn't compose into one meaningful global cutoff, so
    this scope skips that toggle entirely)."""
    matches = _load_all_matches(db)
    match_players = [mp for m in matches for mp in m.match_players]
    return compute_pistol_match_stats(match_players)


def _compute_site_stats(db: Session) -> dict:
    """Both cached stats, from ONE shared match load."""
    matches = _load_all_matches(db)
    match_players = [mp for m in matches for mp in m.match_players]
    roster_player_ids = set(resolve_roster_player_ids(db))
    return {
        "pistol_match_stats": compute_pistol_match_stats(match_players),
        "pistol_win_followup_eco": compute_pistol_win_followup_eco(matches, roster_player_ids),
        "pistol_round_combos": compute_round_combo_stats(matches, roster_player_ids),
        "map_side_stats": compute_map_side_stats(matches, roster_player_ids),
        "halftime_conversion": compute_halftime_conversion_stats(matches, roster_player_ids),
        "score_reached": compute_score_reached_stats(matches, roster_player_ids),
        "round_streaks": compute_round_streak_stats(matches, roster_player_ids),
    }


def refresh_site_stats(db: Session) -> dict:
    """Unconditional recompute of every stat cached in site_stats_cache,
    written through. Used both by get_site_stats on a cache miss and by the
    tracker.gg ingest scripts' post-refresh pre-warm (mirrors
    app.services.player_view_cache.prewarm_player_cache's role for the
    per-player cache)."""
    data = _compute_site_stats(db)
    store_site_stats_cache(db, data)
    return data


def get_site_stats(db: Session) -> dict:
    """Cache hit -> the stored blob as-is. Cache miss/stale/corrupt -> live
    recompute, then a best-effort write-through (same non-fatal contract as
    app.services.player_view_cache's route-level write-through: a failure to
    cache never prevents the page from rendering)."""
    cached = get_site_stats_cache(db)
    if cached is not None:
        return cached
    try:
        return refresh_site_stats(db)
    except Exception:
        db.rollback()
        logger.exception("site_stats_cache: write-through failed, serving live result uncached")
        return _compute_site_stats(db)
