"""Read/write/invalidate layer for the pre-computed player-page cache
(player_view_cache table). Best-effort everywhere: a cache miss, a corrupt or
structurally-invalid blob, or a stale version all degrade to the caller
recomputing live (see app.routers.players) -- this module never raises out of
get_cached_views, and a failed pre-warm never aborts a batch.
"""

import logging

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import MatchPlayer, Player, PlayerViewCache
from app.services.fight_ev import (
    CALCULATION_VERSION,
    FIGHT_EV_CELL_KEYS,
    FIGHT_EV_VIEW_KEYS,
    DisplayState,
    serialize_fight_ev_views,
)
from app.services.player_graphs import StateDiagram, build_state_diagrams_from_aggregates
from app.services.player_views import PlayerViews, compute_player_views_by_scope

logger = logging.getLogger(__name__)

PLAYER_VIEW_CACHE_SCHEMA_VERSION = 1
# Bump when ANY of these change:
#   - replay semantics (round exclusion rules, event filtering)
#   - win_stats / kill_order_weights accumulation logic
#   - PAGE_BOOTSTRAP_DRAWS, or the draw count used for the cache
#   - MIN_DEFINED_DRAW_FRACTION / MIN_CONTRIBUTING_MATCHES
#   - point-estimate or display classification logic
#   - the shape of the stored blob (keys added/removed/renamed)
#   - the friendship-dependent teammate pool logic
#   - the recent-window ORDER BY contract (see app.services.player_data)
# Both products share one row, so a bump recomputes both even when only
# one changed. That is fine: the cost is dominated by hydration+replay,
# which they share.

assert CALCULATION_VERSION < 1000  # keeps the composite below collision-free


def cache_version() -> int:
    """The value persisted in and checked against player_view_cache.version.

    CALCULATION_VERSION is folded in MECHANICALLY rather than documented as a
    remember-to-also-bump step: it feeds _bootstrap_seed, so every stored
    confidence interval depends on it, and a bump that did not invalidate the
    cache would leave the site serving intervals from the previous seed
    sequence indefinitely. Composite rather than hashed so the stored value
    stays readable -- 1003 is schema 1, calculation 3.
    """
    return PLAYER_VIEW_CACHE_SCHEMA_VERSION * 1000 + CALCULATION_VERSION


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


def _validate_blob(blob: object) -> bool:
    """Returns False -> treat as a cache miss and log which check failed. A
    blob that is valid JSON but structurally wrong (a truncated write, a
    payload from a half-finished refactor, a hand-edited row) must not reach
    the template -- that's exactly the failure the live-compute fallback
    exists to prevent."""
    if not isinstance(blob, dict) or set(blob.keys()) != {"state_aggregates", "fight_ev"}:
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

    return True


def _encode(views: PlayerViews) -> dict:
    return {
        "state_aggregates": {
            "win_stats": views.win_stats,
            "kill_order_weights": [[src, dst, weight] for (src, dst), weight in views.kill_order_weights.items()],
        },
        "fight_ev": serialize_fight_ev_views(views.fight_ev),
    }


class CachedPlayerViews:
    def __init__(self, round_win_graph: StateDiagram, kill_order_graph: StateDiagram, fight_ev_data: dict):
        self.round_win_graph = round_win_graph
        self.kill_order_graph = kill_order_graph
        self.fight_ev_data = fight_ev_data


def _decode(blob: dict) -> CachedPlayerViews:
    state_aggregates = blob["state_aggregates"]
    win_stats = state_aggregates["win_stats"]
    kill_order_weights = {(src, dst): weight for src, dst, weight in state_aggregates["kill_order_weights"]}
    round_win_graph, kill_order_graph = build_state_diagrams_from_aggregates(win_stats, kill_order_weights)
    return CachedPlayerViews(round_win_graph, kill_order_graph, blob["fight_ev"])


def get_cached_views(db: Session, player_id: int, scope: str) -> CachedPlayerViews | None:
    """Cache miss, version mismatch, or any validation/decode failure ->
    None (the caller then computes live). Failures are logged, never
    raised: a corrupt blob must not turn into a 500, and must not reach
    the template either. Rebuilds the StateDiagrams via
    build_state_diagrams_from_aggregates so presentation changes never
    need a cache bump."""
    row = db.query(PlayerViewCache).filter_by(player_id=player_id, scope=scope).one_or_none()
    if row is None:
        return None
    if row.version != cache_version():
        return None
    try:
        if not _validate_blob(row.data):
            return None
        return _decode(row.data)
    except Exception:
        logger.exception("player_view_cache: decode failed for player %s scope %s", player_id, scope)
        return None


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
