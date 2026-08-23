"""Read/write/invalidate layer for the pre-computed player-page cache
(player_view_cache table). Best-effort everywhere: a cache miss, a corrupt or
structurally-invalid blob, or a stale version all degrade to the caller
recomputing live (see app.routers.players) -- this module never raises out of
get_cached_views, and a failed pre-warm never aborts a batch.

Since docs/player_page_render_speed.txt Step 2, the cached blob covers the
WHOLE player page (state diagrams, fight-EV, econ charts, and the profile
summary/match table), not just the two products PR #29 shipped. Only
CANONICAL AGGREGATES are ever stored -- every presentation object (colors,
radii, SVG paths, ticks, view boxes, GroupedStat sorting, match_label()
output) is rebuilt at decode from those aggregates, so a purely-visual change
never needs PLAYER_VIEW_CACHE_SCHEMA_VERSION bumped (doc 2a, 4.1).
"""

import logging
from collections import Counter
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import MatchPlayer, Player, PlayerViewCache
from app.scoring.impact import IMPACT_CALCULATION_VERSION
from app.services.economy_graphs import (
    LoadoutWinScatter,
    PistolStats,
    TierMatrix,
    build_loadout_win_scatter_from_aggregates,
    build_pistol_stats_from_aggregates,
    build_tier_matrix_from_aggregates,
)
from app.services.fight_ev import (
    CALCULATION_VERSION,
    FIGHT_EV_CELL_KEYS,
    FIGHT_EV_VIEW_KEYS,
    DisplayState,
    serialize_fight_ev_views,
)
from app.services.player_graphs import (
    STATE_DIAGRAM_CALCULATION_VERSION,
    StateDiagram,
    build_state_diagrams_from_aggregates,
)
from app.services.player_profile_types import (
    CachedMatchRef,
    MatchBreakdown,
    PlayerProfile,
    grouped_stats,
)
from app.services.player_views import PlayerViews, compute_player_views_by_scope

logger = logging.getLogger(__name__)

PLAYER_VIEW_CACHE_SCHEMA_VERSION = 2
# Bump when ANY of these change:
#   - replay semantics (round exclusion rules, event filtering)
#   - win_stats / kill_order_weights accumulation logic
#   - PAGE_BOOTSTRAP_DRAWS, or the draw count used for the cache
#   - MIN_DEFINED_DRAW_FRACTION / MIN_CONTRIBUTING_MATCHES
#   - point-estimate or display classification logic
#   - the shape of the stored blob (keys added/removed/renamed)
#   - the friendship-dependent teammate pool logic
#   - the recent-window ORDER BY contract (see app.services.player_data)
#   - the econ tier/pistol/loadout-bucket aggregation rules
#   - the profile match-summary/scalar field set, or the exclusion rule for
#     which matches are listed (currently: must have ImpactScore rows)
# All products share one row, so a bump recomputes every one of them even
# when only one changed. That is fine: the cost is dominated by
# hydration+replay, which they all share (one load_player_match_data call).
#
# v2: extended to cover the whole page (Step 2) -- econ_aggregates and
# profile added to the blob; IMPACT_CALCULATION_VERSION folded into
# cache_version() (Step 3b), since profile/econ are now derived from
# ImpactScore rows this cache previously never read at all.
# Step 8: STATE_DIAGRAM_CALCULATION_VERSION folded in too -- the round-win/
# kill-order diamonds now come from a shared replay pass with fight-EV
# (app.services.player_graphs.accumulate_state_stats_from_replay) instead of
# their own independent walk, adopting state_replay's stricter round-
# exclusion semantics. See that function's docstring for the validated
# before/after diff this represents.

assert CALCULATION_VERSION < 1000  # keeps the composite below collision-free
assert IMPACT_CALCULATION_VERSION < 1000
assert STATE_DIAGRAM_CALCULATION_VERSION < 1000


def cache_version() -> int:
    """The value persisted in and checked against player_view_cache.version.

    CALCULATION_VERSION, IMPACT_CALCULATION_VERSION and STATE_DIAGRAM_
    CALCULATION_VERSION are folded in MECHANICALLY rather than documented as
    remember-to-also-bump steps: CALCULATION_VERSION feeds _bootstrap_seed
    (every stored confidence interval depends on it), IMPACT_CALCULATION_
    VERSION versions the ImpactScore rows the profile/econ aggregates are
    derived from (Step 3b), and STATE_DIAGRAM_CALCULATION_VERSION versions
    the round-win/kill-order replay semantics (Step 8) -- any of these
    changing without invalidating this cache would leave the site serving
    numbers computed under the PREVIOUS rules indefinitely. Composite rather
    than hashed so the stored value stays readable -- 2_002_003_001 is
    schema 2, state-diagram calculation 2, fight-EV calculation 3, impact
    calculation 1.
    """
    return (
        PLAYER_VIEW_CACHE_SCHEMA_VERSION * 1_000_000_000
        + STATE_DIAGRAM_CALCULATION_VERSION * 1_000_000
        + CALCULATION_VERSION * 1000
        + IMPACT_CALCULATION_VERSION
    )


VALID_DISPLAY_STATES = frozenset(state.value for state in DisplayState)

_BOOTSTRAP_KEYS = frozenset({
    "ci_low", "ci_high", "contributing_matches_player", "contributing_matches_teammates",
})


def _validate_win_stats(win_stats: object) -> bool:
    if not isinstance(win_stats, dict):
        return False
    for state, bucket in win_stats.items():
        if not isinstance(state, str) or "v" not in state:
            return False
        a, _, b = state.partition("v")
        if not (a.isdigit() and b.isdigit()):
            return False
        if not isinstance(bucket, dict) or set(bucket.keys()) != {"win", "total"}:
            return False
        win, total = bucket["win"], bucket["total"]
        if not isinstance(win, int) or not isinstance(total, int) or isinstance(win, bool) or isinstance(total, bool):
            return False
        if win > total:
            return False
    return True


def _validate_kill_order_weights(weights: object) -> bool:
    if not isinstance(weights, list):
        return False
    for triple in weights:
        if not isinstance(triple, list) or len(triple) != 3:
            return False
        src, dst, weight = triple
        if not isinstance(src, str) or not isinstance(dst, str):
            return False
        if not isinstance(weight, int) or isinstance(weight, bool):
            return False
    return True


def _validate_bootstrap(bootstrap: object) -> bool:
    if bootstrap is None:
        return True
    return isinstance(bootstrap, dict) and set(bootstrap.keys()) == _BOOTSTRAP_KEYS


def _validate_cell(cell: object) -> bool:
    if not isinstance(cell, dict) or set(cell.keys()) != FIGHT_EV_CELL_KEYS:
        return False
    if not isinstance(cell["a"], int) or not (1 <= cell["a"] <= 5):
        return False
    if not isinstance(cell["b"], int) or not (1 <= cell["b"] <= 5):
        return False
    if cell["display_state"] not in VALID_DISPLAY_STATES:
        return False
    return _validate_bootstrap(cell["bootstrap"])


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_nonneg_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


_ECON_BUCKET_KEYS = frozenset({"win", "total", "ratio_sum", "ratio_count"})


def _validate_econ_bucket_fields(win: object, total: object, ratio_sum: object, ratio_count: object) -> bool:
    if not (_is_nonneg_int(win) and _is_nonneg_int(total) and _is_nonneg_int(ratio_count)):
        return False
    if not _is_number(ratio_sum):
        return False
    return win <= total and ratio_count <= total


def _validate_econ_aggregates(econ: object) -> bool:
    if not isinstance(econ, dict) or set(econ.keys()) != {"tier_pairs", "pistol", "loadout_buckets"}:
        return False

    tier_pairs = econ["tier_pairs"]
    if not isinstance(tier_pairs, list):
        return False
    for row in tier_pairs:
        if not isinstance(row, list) or len(row) != 6:
            return False
        own_tier, enemy_tier, win, total, ratio_sum, ratio_count = row
        if not isinstance(own_tier, str) or not isinstance(enemy_tier, str):
            return False
        if not _validate_econ_bucket_fields(win, total, ratio_sum, ratio_count):
            return False

    pistol = econ["pistol"]
    if not isinstance(pistol, dict) or set(pistol.keys()) != _ECON_BUCKET_KEYS:
        return False
    if not _validate_econ_bucket_fields(
        pistol["win"], pistol["total"], pistol["ratio_sum"], pistol["ratio_count"]
    ):
        return False

    loadout_buckets = econ["loadout_buckets"]
    if not isinstance(loadout_buckets, list):
        return False
    for row in loadout_buckets:
        if not isinstance(row, list) or len(row) != 3:
            return False
        idx, win, total = row
        if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < 20):
            return False
        if not (_is_nonneg_int(win) and _is_nonneg_int(total)) or win > total:
            return False

    return True


_MATCH_SUMMARY_KEYS = frozenset({
    "match_id", "external_id", "map_name", "played_at", "team1_rounds_won", "team2_rounds_won",
    "agent", "team", "win", "kills", "deaths", "assists",
    "average_impact", "average_kill_impact", "average_death_impact",
})


def _validate_played_at(played_at: object) -> bool:
    if played_at is None:
        return True
    if not isinstance(played_at, str):
        return False
    try:
        parsed = datetime.fromisoformat(played_at)
    except ValueError:
        return False
    # Load-bearing (doc 2d): match_label() calls played.astimezone(DISPLAY_TZ)
    # -- a naive datetime would be silently interpreted as local time and
    # render a wrong timestamp instead of raising, so a blob whose played_at
    # would decode naive must be treated as corrupt, not silently trusted.
    return parsed.tzinfo is not None


def _validate_match_summary(m: object) -> bool:
    if not isinstance(m, dict) or set(m.keys()) != _MATCH_SUMMARY_KEYS:
        return False
    if not isinstance(m["match_id"], int) or isinstance(m["match_id"], bool):
        return False
    if not isinstance(m["external_id"], str):
        return False
    if m["map_name"] is not None and not isinstance(m["map_name"], str):
        return False
    if not _validate_played_at(m["played_at"]):
        return False
    if not _is_nonneg_int(m["team1_rounds_won"]) or not _is_nonneg_int(m["team2_rounds_won"]):
        return False
    if not isinstance(m["agent"], str) or not isinstance(m["team"], str):
        return False
    if m["win"] is not None and not isinstance(m["win"], bool):
        return False
    if not all(_is_nonneg_int(m[k]) for k in ("kills", "deaths", "assists")):
        return False
    if not all(_is_number(m[k]) for k in ("average_impact", "average_kill_impact", "average_death_impact")):
        return False
    return True


def _validate_name_count_pairs(pairs: object) -> bool:
    if not isinstance(pairs, list):
        return False
    for pair in pairs:
        if not isinstance(pair, list) or len(pair) != 2:
            return False
        name, count = pair
        if not isinstance(name, str) or not _is_nonneg_int(count):
            return False
    return True


_PROFILE_SCALAR_KEYS = (
    "overall_average_impact", "overall_average_round_win_impact", "overall_average_death_impact",
    "avg_econ_kill", "avg_econ_death", "avg_clutch_kill", "avg_clutch_death",
    "avg_post_plant_kill", "avg_post_plant_death", "avg_traded_teammate", "avg_traded_by_teammate",
)
_PROFILE_KEYS = frozenset(
    {"matches", "agent_counts", "top_traded_teammate", "top_traded_by_teammate", *_PROFILE_SCALAR_KEYS}
)


def _validate_profile(profile: object) -> bool:
    if not isinstance(profile, dict) or set(profile.keys()) != _PROFILE_KEYS:
        return False
    if not all(_is_number(profile[k]) for k in _PROFILE_SCALAR_KEYS):
        return False
    matches = profile["matches"]
    if not isinstance(matches, list) or not all(_validate_match_summary(m) for m in matches):
        return False
    if not _validate_name_count_pairs(profile["agent_counts"]):
        return False
    if not _validate_name_count_pairs(profile["top_traded_teammate"]):
        return False
    if not _validate_name_count_pairs(profile["top_traded_by_teammate"]):
        return False
    return True


def _validate_blob(blob: object) -> bool:
    """Returns False -> treat as a cache miss and log which check failed. A
    blob that is valid JSON but structurally wrong (a truncated write, a
    payload from a half-finished refactor, a hand-edited row) must not reach
    the template -- that's exactly the failure the live-compute fallback
    exists to prevent."""
    if not isinstance(blob, dict) or set(blob.keys()) != {"state_aggregates", "fight_ev", "econ_aggregates", "profile"}:
        logger.warning("player_view_cache: blob missing top-level keys")
        return False

    state_aggregates = blob["state_aggregates"]
    if not isinstance(state_aggregates, dict) or set(state_aggregates.keys()) != {"win_stats", "kill_order_weights"}:
        logger.warning("player_view_cache: blob missing state_aggregates keys")
        return False
    if not _validate_win_stats(state_aggregates["win_stats"]):
        logger.warning("player_view_cache: invalid win_stats")
        return False
    if not _validate_kill_order_weights(state_aggregates["kill_order_weights"]):
        logger.warning("player_view_cache: invalid kill_order_weights")
        return False

    fight_ev = blob["fight_ev"]
    if not isinstance(fight_ev, dict) or set(fight_ev.keys()) != set(FIGHT_EV_VIEW_KEYS):
        logger.warning("player_view_cache: blob missing fight_ev view keys")
        return False
    for view_key in FIGHT_EV_VIEW_KEYS:
        cells = fight_ev[view_key]
        if not isinstance(cells, list) or len(cells) != 25:
            logger.warning("player_view_cache: fight_ev view %s has wrong cell count", view_key)
            return False
        if not all(_validate_cell(cell) for cell in cells):
            logger.warning("player_view_cache: fight_ev view %s has an invalid cell", view_key)
            return False

    if not _validate_econ_aggregates(blob["econ_aggregates"]):
        logger.warning("player_view_cache: invalid econ_aggregates")
        return False

    if not _validate_profile(blob["profile"]):
        logger.warning("player_view_cache: invalid profile")
        return False

    return True


def _encode_econ_aggregates(econ: dict) -> dict:
    tier_pairs = econ["tier_pairs"]
    pistol = econ["pistol"]
    loadout_buckets = econ["loadout_buckets"]
    return {
        "tier_pairs": [
            [own_tier, enemy_tier, b["win"], b["total"], b["ratio_sum"], b["ratio_count"]]
            for (own_tier, enemy_tier), b in tier_pairs.items()
        ],
        "pistol": {
            "win": pistol["win"], "total": pistol["total"],
            "ratio_sum": pistol["ratio_sum"], "ratio_count": pistol["ratio_count"],
        },
        "loadout_buckets": [[idx, b["win"], b["total"]] for idx, b in loadout_buckets.items()],
    }


def _decode_econ_aggregates(d: dict) -> dict:
    tier_pairs = {
        (own_tier, enemy_tier): {"win": win, "total": total, "ratio_sum": ratio_sum, "ratio_count": ratio_count}
        for own_tier, enemy_tier, win, total, ratio_sum, ratio_count in d["tier_pairs"]
    }
    pistol = dict(d["pistol"])
    loadout_buckets = {idx: {"win": win, "total": total} for idx, win, total in d["loadout_buckets"]}
    return {"tier_pairs": tier_pairs, "pistol": pistol, "loadout_buckets": loadout_buckets}


def _encode_match_summary(m: MatchBreakdown) -> dict:
    match = m.match
    return {
        "match_id": match.id,
        "external_id": match.external_id,
        "map_name": match.map_name,
        "played_at": match.played_at.isoformat() if match.played_at is not None else None,
        "team1_rounds_won": match.team1_rounds_won,
        "team2_rounds_won": match.team2_rounds_won,
        "agent": m.agent,
        "team": m.team,
        "win": m.win,
        "kills": m.kills,
        "deaths": m.deaths,
        "assists": m.assists,
        "average_impact": m.average_impact,
        "average_kill_impact": m.average_kill_impact,
        "average_death_impact": m.average_death_impact,
    }


def _decode_match_ref(d: dict) -> tuple[CachedMatchRef, MatchBreakdown]:
    played_at = datetime.fromisoformat(d["played_at"]) if d["played_at"] is not None else None
    match_ref = CachedMatchRef(
        external_id=d["external_id"], map_name=d["map_name"], played_at=played_at,
        team1_rounds_won=d["team1_rounds_won"], team2_rounds_won=d["team2_rounds_won"],
    )
    breakdown = MatchBreakdown(
        match=match_ref, agent=d["agent"], team=d["team"],
        average_impact=d["average_impact"], average_kill_impact=d["average_kill_impact"],
        average_death_impact=d["average_death_impact"], win=d["win"],
        kills=d["kills"], deaths=d["deaths"], assists=d["assists"],
    )
    return match_ref, breakdown


def _encode_profile(profile: PlayerProfile) -> dict:
    """Only canonical scalars + per-match summary rows -- agent_stats,
    map_stats and profile.player are all rebuilt at decode (doc 2a/2d)."""
    return {
        "matches": [_encode_match_summary(m) for m in profile.matches],
        "agent_counts": [[agent, count] for agent, count in profile.agent_counts.items()],
        "top_traded_teammate": [[name, count] for name, count in profile.top_traded_teammate],
        "top_traded_by_teammate": [[name, count] for name, count in profile.top_traded_by_teammate],
        **{key: getattr(profile, key) for key in _PROFILE_SCALAR_KEYS},
    }


def _decode_profile(d: dict, player: Player) -> PlayerProfile:
    matches = [_decode_match_ref(m)[1] for m in d["matches"]]
    agent_stats = grouped_stats(matches, lambda m: m.agent)
    map_stats = grouped_stats(matches, lambda m: m.match.map_name)
    # Same override as build_profile_from_precomputed: win rate first, then
    # matches played as the tiebreak -- kept in sync by hand since decode
    # doesn't call that function (it has no queries to precompute inputs
    # from). See app.services.player_profile_types for the canonical version.
    map_stats.sort(key=lambda s: (s.win_rate if s.win_rate is not None else -1, s.matches_played), reverse=True)

    return PlayerProfile(
        player=player,
        matches=matches,
        agent_counts=Counter({agent: count for agent, count in d["agent_counts"]}),
        agent_stats=agent_stats,
        map_stats=map_stats,
        top_traded_teammate=[(name, count) for name, count in d["top_traded_teammate"]],
        top_traded_by_teammate=[(name, count) for name, count in d["top_traded_by_teammate"]],
        **{key: d[key] for key in _PROFILE_SCALAR_KEYS},
    )


def _encode(views: PlayerViews) -> dict:
    return {
        "state_aggregates": {
            "win_stats": views.win_stats,
            "kill_order_weights": [[src, dst, weight] for (src, dst), weight in views.kill_order_weights.items()],
        },
        "fight_ev": serialize_fight_ev_views(views.fight_ev),
        "econ_aggregates": _encode_econ_aggregates(views.econ_aggregates),
        "profile": _encode_profile(views.profile),
    }


class CachedPlayerViews:
    def __init__(
        self,
        round_win_graph: StateDiagram,
        kill_order_graph: StateDiagram,
        fight_ev_data: dict,
        profile: PlayerProfile,
        econ_tier_matrix: TierMatrix,
        econ_pistol_stats: PistolStats,
        econ_loadout_scatter: LoadoutWinScatter,
    ):
        self.round_win_graph = round_win_graph
        self.kill_order_graph = kill_order_graph
        self.fight_ev_data = fight_ev_data
        self.profile = profile
        self.econ_tier_matrix = econ_tier_matrix
        self.econ_pistol_stats = econ_pistol_stats
        self.econ_loadout_scatter = econ_loadout_scatter


def _decode(blob: dict, player: Player) -> CachedPlayerViews:
    state_aggregates = blob["state_aggregates"]
    win_stats = state_aggregates["win_stats"]
    kill_order_weights = {(src, dst): weight for src, dst, weight in state_aggregates["kill_order_weights"]}
    round_win_graph, kill_order_graph = build_state_diagrams_from_aggregates(win_stats, kill_order_weights)

    econ_aggregates = _decode_econ_aggregates(blob["econ_aggregates"])
    econ_tier_matrix = build_tier_matrix_from_aggregates(econ_aggregates["tier_pairs"])
    econ_pistol_stats = build_pistol_stats_from_aggregates(econ_aggregates["pistol"])
    econ_loadout_scatter = build_loadout_win_scatter_from_aggregates(econ_aggregates["loadout_buckets"])

    profile = _decode_profile(blob["profile"], player)

    return CachedPlayerViews(
        round_win_graph, kill_order_graph, blob["fight_ev"],
        profile, econ_tier_matrix, econ_pistol_stats, econ_loadout_scatter,
    )


def decode_cache_row(row: PlayerViewCache | None, player: Player) -> CachedPlayerViews | None:
    """Version-check + validate + decode an ALREADY-FETCHED player_view_cache
    row (or None). Factored out of get_cached_views so a caller that already
    has the row -- e.g. from a merged player+cache lookup query (see
    app.services.players.get_player_and_cached_views, Step 1) -- doesn't
    need a second round trip just to validate/decode it. `player` is the
    LIVE route-level Player object, attached to the decoded profile so
    profile.player.display_name keeps working (doc 2d) -- this module never
    queries for it itself. Same miss/corruption contract as get_cached_views:
    never raises, a bad row degrades to None (live recompute), never a 500
    and never reaches the template."""
    if row is None:
        return None
    if row.version != cache_version():
        return None
    try:
        if not _validate_blob(row.data):
            return None
        return _decode(row.data, player)
    except Exception:
        logger.exception("player_view_cache: decode failed for player %s scope %s", row.player_id, row.scope)
        return None


def get_cached_views(db: Session, player: Player, scope: str) -> CachedPlayerViews | None:
    """Cache miss, version mismatch, or any validation/decode failure ->
    None (the caller then computes live). Failures are logged, never
    raised: a corrupt blob must not turn into a 500, and must not reach
    the template either. Rebuilds every presentation object (StateDiagrams,
    TierMatrix, PistolStats, LoadoutWinScatter, GroupedStat lists) from
    canonical aggregates so a purely visual change never needs a cache
    bump."""
    row = db.query(PlayerViewCache).filter_by(player_id=player.id, scope=scope).one_or_none()
    return decode_cache_row(row, player)


def _upsert_cache(db: Session, player_id: int, scope: str, data: dict) -> None:
    """Does NOT commit -- store_player_views commits once for all scopes."""
    version = cache_version()
    stmt = (
        pg_insert(PlayerViewCache.__table__)
        .values(player_id=player_id, scope=scope, data=data,
                version=version, updated_at=func.now())
        .on_conflict_do_update(
            index_elements=[PlayerViewCache.player_id, PlayerViewCache.scope],
            set_={"data": data, "version": version, "updated_at": func.now()},
        )
    )
    db.execute(stmt)


def store_player_views(db: Session, player_id: int, views_by_scope: dict[str, PlayerViews]) -> None:
    """Encode and upsert EVERY given scope, then commit ONCE.
    recompute_player_views passes both scopes so a mid-way failure can
    never leave one scope cached and the other empty; the route's
    write-through passes a single-entry dict."""
    for scope, views in views_by_scope.items():
        _upsert_cache(db, player_id, scope, _encode(views))
    db.commit()


def invalidate_player_cache(db: Session, player_ids: set[int]) -> None:
    """DELETE FROM player_view_cache WHERE player_id IN (...).
    Does NOT commit -- the caller commits, so the delete lands in the same
    transaction as whatever mutation triggered it."""
    if not player_ids:
        return
    db.query(PlayerViewCache).filter(PlayerViewCache.player_id.in_(player_ids)).delete(synchronize_session=False)


def invalidate_all_player_caches(db: Session) -> None:
    """DELETE every player_view_cache row. Does NOT commit. For a
    recompute that touches EVERY match (scripts/recompute_impact.py) --
    simpler and strictly safer than discovering which players are affected,
    since the answer there is "all of them" (Step 3a)."""
    db.query(PlayerViewCache).delete(synchronize_session=False)


def filter_cached_player_ids(db: Session, player_ids: set[int]) -> set[int]:
    """Narrows an arbitrary ID set to those with existing cache rows."""
    if not player_ids:
        return set()
    return {
        pid
        for (pid,) in db.query(PlayerViewCache.player_id)
        .filter(PlayerViewCache.player_id.in_(player_ids))
        .distinct()
        .all()
    }


def find_cached_player_ids_for_match(db: Session, match_id: int) -> set[int]:
    """The players in `match_id` who ALREADY have cache rows. Targets only
    those -- not the tracked_players.json roster (a separate concept from
    the Friendship table fight-EV's list_friend_ids uses), and not every
    opponent who ever appeared in a match."""
    player_ids = {
        pid for (pid,) in db.query(MatchPlayer.player_id).filter_by(match_id=match_id).all()
    }
    return filter_cached_player_ids(db, player_ids)


def recompute_player_views(db: Session, player_id: int) -> None:
    """compute_player_views_by_scope, then one store_player_views call
    with both scopes -- a single commit."""
    player = db.query(Player).filter_by(id=player_id).one()
    views_by_scope = compute_player_views_by_scope(db, player)
    store_player_views(db, player_id, views_by_scope)


def prewarm_player_cache(db: Session, player_ids: set[int]) -> None:
    """Best-effort, per player: one player's failure never aborts the batch."""
    for pid in player_ids:
        try:
            recompute_player_views(db, pid)
        except Exception:
            db.rollback()
            logger.exception("Cache pre-warm failed for player %s; next page visit will recompute", pid)
