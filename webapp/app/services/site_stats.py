"""Aggregate stats computed across many players at once, rather than one
player's own profile -- the /stats page. It shows two populations:

  - "All Players": every match in the DB, computed once site-wide and cached
    as the single site_stats_cache row (get_site_stats / refresh_site_stats;
    see app.services.site_stats_cache).
  - "Friends": the LOGGED-IN viewer plus the players on their own Friends
    page (friendships the viewer owns), computed per viewer and cached one
    row per viewer in viewer_site_stats_cache (get_viewer_site_stats; see
    app.services.viewer_site_stats_cache).

Every stat is a compute_*(matches, group_player_ids) function returning
{"group": ..., "all": ...}: "group" counts a sample only when the relevant
team (or match) included a player from group_player_ids, "all" counts every
one. The site-wide pass keeps "all"; a viewer's pass loads only the matches
their group played in and keeps "group".

pistol_match_stats is the one per-player personal stat: it tallies each
group member's OWN matches, so a match where two friends were teammates
counts twice (once per member), and it's the only stat that honours the
Friends tab's Recent/Career toggle (each member's last RECENT_MATCH_LIMIT
matches, in load_player_match_data's order).

scripts/tracked_players.json is NOT read here: it only decides which matches
the tracker.gg scripts crawl, and nothing displayed on the site is defined by
it.
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Friendship, Match, MatchPlayer, Round
from app.services.eco_followup import compute_pistol_win_followup_eco
from app.services.enemy_at_11_response import compute_enemy_at_11_response_stats
from app.services.force_buy_stats import compute_force_buy_stats
from app.services.friends import list_friend_ids
from app.services.halftime_conversion_stats import compute_halftime_conversion_stats
from app.services.map_side_stats import compute_map_side_stats
from app.services.player_data import RECENT_MATCH_LIMIT
from app.services.player_profile_types import compute_pistol_match_stats
from app.services.round_combo_stats import compute_round_combo_stats
from app.services.round_streak_stats import compute_round_streak_stats
from app.services.score_reached_stats import compute_score_reached_stats
from app.services.site_stats_cache import (
    get_site_stats_cache,
    invalidate_all_viewer_site_stats,
    store_site_stats_cache,
)
from app.services.viewer_site_stats_cache import (
    PISTOL_SCOPES,
    friend_set_hash,
    get_viewer_site_stats_cache,
    store_viewer_site_stats_cache,
)

logger = logging.getLogger(__name__)

# Every team/match-scoped stat, keyed as it's stored in both caches.
GROUP_STAT_COMPUTERS = {
    "pistol_win_followup_eco": compute_pistol_win_followup_eco,
    "pistol_round_combos": compute_round_combo_stats,
    "map_side_stats": compute_map_side_stats,
    "halftime_conversion": compute_halftime_conversion_stats,
    "score_reached": compute_score_reached_stats,
    "round_streaks": compute_round_streak_stats,
    "force_buy_stats": compute_force_buy_stats,
    "enemy_at_11_response": compute_enemy_at_11_response_stats,
}


def _match_load_options():
    """match_players and rounds(+player_stats) -- everything every stat here
    reads. kill_events are NOT loaded: no stat on /stats looks at them."""
    return (
        selectinload(Match.match_players),
        selectinload(Match.rounds).selectinload(Round.player_stats),
    )


def _load_all_matches(db: Session) -> list[Match]:
    return db.query(Match).options(*_match_load_options()).all()


def _load_group_matches(db: Session, player_ids: set[int]) -> list[Match]:
    """Only the matches at least one of player_ids played in -- every sample a
    group variant can count comes from one of these. One query plus the
    selectin batches, however many players are in the group."""
    if not player_ids:
        return []
    group_match_ids = select(MatchPlayer.match_id).where(MatchPlayer.player_id.in_(player_ids))
    return db.query(Match).filter(Match.id.in_(group_match_ids)).options(*_match_load_options()).all()


def _newest_first_key(mp: MatchPlayer):
    """load_player_match_data's ORDER BY (played_at DESC NULLS FIRST, id
    DESC), as a key for sorted(..., reverse=True). A missing played_at sorts
    as the newest match -- that module's pinned convention."""
    played_at = mp.match.played_at
    return (played_at is None, played_at or datetime.min, mp.match.id)


def compute_group_pistol_match_stats(matches: list[Match], player_ids: set[int], scope: str) -> dict[str, int]:
    """Sum of every group member's own pistol_match_stats over already-loaded
    matches. "recent" keeps each member's newest RECENT_MATCH_LIMIT matches,
    the same window their player page's Recent tab uses; "career" keeps all."""
    by_player: dict[int, list[MatchPlayer]] = {}
    for match in matches:
        for mp in match.match_players:
            if mp.player_id in player_ids:
                by_player.setdefault(mp.player_id, []).append(mp)

    selected: list[MatchPlayer] = []
    for mps in by_player.values():
        if scope == "recent":
            mps = sorted(mps, key=_newest_first_key, reverse=True)[:RECENT_MATCH_LIMIT]
        selected.extend(mps)
    # compute_pistol_match_stats' buckets are additive across players, so one
    # call over every member's rows equals the sum of per-member calls.
    return compute_pistol_match_stats(selected)


# --- All Players (site-wide) -------------------------------------------------


def _compute_site_stats(db: Session) -> dict:
    """Every stat's "all" variant, from ONE shared load of every match."""
    matches = _load_all_matches(db)
    match_players = [mp for m in matches for mp in m.match_players]
    return {
        "pistol_match_stats": compute_pistol_match_stats(match_players),
        **{key: compute(matches, set())["all"] for key, compute in GROUP_STAT_COMPUTERS.items()},
    }


def refresh_site_stats(db: Session) -> dict:
    """Unconditional recompute of the All Players cache, written through, and
    retirement of every viewer's Friends-tab row in the same commit. Used by
    get_site_stats on a miss and by the tracker.gg ingest scripts after a run
    (mirrors app.services.player_view_cache.prewarm_player_cache's role for
    the per-player cache)."""
    data = _compute_site_stats(db)
    invalidate_all_viewer_site_stats(db)
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
    data = _compute_site_stats(db)
    try:
        store_site_stats_cache(db, data)
    except Exception:
        db.rollback()
        logger.exception("site_stats_cache: write-through failed, serving live result uncached")
    return data


# --- Friends (per viewer) -----------------------------------------------------


def viewer_group_player_ids(db: Session, viewer_player_id: int) -> set[int]:
    """The viewer plus everyone on their own Friends page."""
    return {viewer_player_id} | list_friend_ids(db, viewer_player_id)


def compute_viewer_site_stats(db: Session, group_player_ids: set[int]) -> dict:
    """Every stat's "group" variant for one viewer's group, from one load of
    just the matches that group played in."""
    matches = _load_group_matches(db, group_player_ids)
    return {
        "pistol_match_stats": {
            scope: compute_group_pistol_match_stats(matches, group_player_ids, scope) for scope in PISTOL_SCOPES
        },
        **{key: compute(matches, group_player_ids)["group"] for key, compute in GROUP_STAT_COMPUTERS.items()},
    }


def get_viewer_site_stats(db: Session, viewer_player_id: int) -> dict:
    """Same hit / live-recompute / best-effort write-through contract as
    get_site_stats, keyed by viewer and checked against their current
    friend set."""
    group = viewer_group_player_ids(db, viewer_player_id)
    group_hash = friend_set_hash(group)
    cached = get_viewer_site_stats_cache(db, viewer_player_id, group_hash)
    if cached is not None:
        return cached
    data = compute_viewer_site_stats(db, group)
    try:
        store_viewer_site_stats_cache(db, viewer_player_id, group_hash, data)
    except Exception:
        db.rollback()
        logger.exception("viewer_site_stats_cache: write-through failed for viewer %s", viewer_player_id)
    return data


def prewarm_viewer_site_stats(db: Session) -> int:
    """Recomputes and stores the Friends tab for every player who owns at
    least one friendship -- the only viewers whose Friends tab is more than
    their own matches. Returns how many rows were written. Best-effort per
    viewer: one failure is logged and skipped."""
    viewer_ids = sorted(pid for (pid,) in db.query(Friendship.owner_player_id).distinct().all())
    written = 0
    for viewer_id in viewer_ids:
        try:
            group = viewer_group_player_ids(db, viewer_id)
            store_viewer_site_stats_cache(db, viewer_id, friend_set_hash(group), compute_viewer_site_stats(db, group))
            written += 1
        except Exception:
            db.rollback()
            logger.exception("viewer_site_stats_cache: pre-warm failed for viewer %s", viewer_id)
    return written
