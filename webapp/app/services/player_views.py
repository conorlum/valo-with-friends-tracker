"""One-load orchestration producing both cached player-page products (state
diagram aggregates + fight-EV views) from a single DB hydration + replay pass.

Phase 1 still runs two replay passes over the shared rows (one for the manual
state-diagram loop, one for state_replay's fight-EV replay); phase 2 (see
docs/player_page_precompute.txt section 4) collapses them into one.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Player
from app.services.fight_ev import (
    PAGE_BOOTSTRAP_DRAWS,
    FightEvViews,
    build_fight_ev_views_from_blocks,
    load_match_fight_ev_blocks_from_data,
)
from app.services.player_data import RECENT_MATCH_LIMIT, load_player_match_data
from app.services.player_graphs import accumulate_match_state_stats, build_state_aggregates_from_data


@dataclass
class PlayerViews:
    win_stats: dict[str, dict[str, int]]
    kill_order_weights: dict[tuple[str, str], int]
    fight_ev: FightEvViews


def _merge_state_aggregates(
    per_match: list[tuple[dict[str, dict[str, int]], dict[tuple[str, str], int]]],
) -> tuple[dict[str, dict[str, int]], dict[tuple[str, str], int]]:
    win_stats: dict[str, dict[str, int]] = {}
    kill_order_weights: dict[tuple[str, str], int] = {}
    for ws, ko in per_match:
        for state, bucket in ws.items():
            merged = win_stats.setdefault(state, {"win": 0, "total": 0})
            merged["win"] += bucket["win"]
            merged["total"] += bucket["total"]
        for key, weight in ko.items():
            kill_order_weights[key] = kill_order_weights.get(key, 0) + weight
    return win_stats, kill_order_weights


def compute_player_views(
    db: Session, player: Player, match_limit: int | None,
    draws: int = PAGE_BOOTSTRAP_DRAWS,
) -> PlayerViews:
    """One load, both products. Phase 1 still runs two replay passes over
    the shared rows; phase 2 collapses them into one."""
    match_players = load_player_match_data(db, player, match_limit)
    win_stats, ko_weights = build_state_aggregates_from_data(match_players)
    blocks, _diag = load_match_fight_ev_blocks_from_data(db, match_players, player)
    return PlayerViews(
        win_stats, ko_weights,
        build_fight_ev_views_from_blocks(blocks, player.id, draws),
    )


def compute_player_views_by_scope(
    db: Session, player: Player, draws: int = PAGE_BOOTSTRAP_DRAWS,
) -> dict[str, PlayerViews]:
    """Both scopes off ONE career load. `recent` is the first
    RECENT_MATCH_LIMIT rows of the career load -- which is only correct
    because load_player_match_data always orders most-recent-first.
    ORM hydration and round replay happen once; only the bootstrap (which
    genuinely differs per match subset) runs per scope."""
    match_players = load_player_match_data(db, player, match_limit=None)
    per_match: list[tuple[dict[str, dict[str, int]], dict[tuple[str, str], int]]] = []
    for mp in match_players:
        ws: dict[str, dict[str, int]] = {}
        ko: dict[tuple[str, str], int] = {}
        accumulate_match_state_stats(mp, ws, ko)
        per_match.append((ws, ko))
    blocks, _diag = load_match_fight_ev_blocks_from_data(db, match_players, player)
    assert len(blocks) == len(match_players)   # slicing below depends on it

    views: dict[str, PlayerViews] = {}
    for scope, limit in (("recent", RECENT_MATCH_LIMIT), ("career", None)):
        n = len(match_players) if limit is None else limit
        win_stats, ko_weights = _merge_state_aggregates(per_match[:n])
        views[scope] = PlayerViews(
            win_stats, ko_weights,
            build_fight_ev_views_from_blocks(blocks[:n], player.id, draws),
        )
    return views
