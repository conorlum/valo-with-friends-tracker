"""One-load orchestration producing every cached player-page product (state
diagram aggregates, fight-EV views, and -- since docs/
player_page_render_speed.txt Step 2 -- the profile summary and econ
aggregates too) from a single DB hydration pass.

Phase 1 still runs two replay passes over the shared rows (one for the manual
state-diagram loop, one for state_replay's fight-EV replay); phase 2 (see
docs/player_page_precompute.txt section 4) collapses them into one. Step 2's
profile/econ builders are a THIRD and FOURTH pass over the same rows, in the
same spirit -- not yet collapsed into the replay either; see Step 8.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import MatchPlayer, Player
from app.services.economy_graphs import compute_econ_aggregates, econ_samples_from_data
from app.services.fight_ev import (
    PAGE_BOOTSTRAP_DRAWS,
    FightEvViews,
    build_fight_ev_views_from_blocks,
    load_match_fight_ev_blocks_from_data,
)
from app.services.player_data import RECENT_MATCH_LIMIT, load_impact_scores_for_match_players, load_player_match_data
from app.services.player_graphs import accumulate_match_state_stats, build_state_aggregates_from_data
from app.services.player_profile_types import PlayerProfile, build_player_profile_from_match_data


@dataclass
class PlayerViews:
    win_stats: dict[str, dict[str, int]]
    kill_order_weights: dict[tuple[str, str], int]
    fight_ev: FightEvViews
    profile: PlayerProfile
    econ_aggregates: dict


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


def _build_profile_and_econ(
    player: Player, match_players_newest_first: list[MatchPlayer], scores_by_match_player: dict,
) -> tuple[PlayerProfile, dict]:
    """match_players_newest_first is load_player_match_data's own order (or
    a newest-first SLICE of it, per scope); build_player_profile_from_match_data
    needs oldest-first (get_player_profile's convention, which the router's
    chart_data/template rely on), so this reverses just for that call. Econ
    aggregation is order-independent."""
    oldest_first = list(reversed(match_players_newest_first))
    profile = build_player_profile_from_match_data(player, oldest_first, scores_by_match_player)
    econ_samples = econ_samples_from_data(match_players_newest_first)
    econ_aggregates = compute_econ_aggregates(econ_samples)
    return profile, econ_aggregates


def compute_player_views(
    db: Session, player: Player, match_limit: int | None,
    draws: int = PAGE_BOOTSTRAP_DRAWS,
) -> PlayerViews:
    """One load, all four products. Phase 1 still runs two replay passes
    over the shared rows for state-diagrams/fight-EV (see module docstring);
    profile/econ (Step 2) are a third and fourth pass over the same rows."""
    match_players = load_player_match_data(db, player, match_limit)
    win_stats, ko_weights = build_state_aggregates_from_data(match_players)
    blocks, _diag = load_match_fight_ev_blocks_from_data(db, match_players, player)

    match_player_ids = [mp.id for mp in match_players]
    scores_by_match_player = load_impact_scores_for_match_players(db, match_player_ids)
    profile, econ_aggregates = _build_profile_and_econ(player, match_players, scores_by_match_player)

    return PlayerViews(
        win_stats, ko_weights,
        build_fight_ev_views_from_blocks(blocks, player.id, draws),
        profile, econ_aggregates,
    )


def compute_player_views_by_scope(
    db: Session, player: Player, draws: int = PAGE_BOOTSTRAP_DRAWS,
) -> dict[str, PlayerViews]:
    """Both scopes off ONE career load. `recent` is the first
    RECENT_MATCH_LIMIT rows of the career load -- which is only correct
    because load_player_match_data always orders most-recent-first.
    ORM hydration and round replay happen once; only the bootstrap (which
    genuinely differs per match subset) and the profile/econ builders
    (which need a per-scope slice of scores, see Step 2b) run per scope."""
    match_players = load_player_match_data(db, player, match_limit=None)
    per_match: list[tuple[dict[str, dict[str, int]], dict[tuple[str, str], int]]] = []
    for mp in match_players:
        ws: dict[str, dict[str, int]] = {}
        ko: dict[tuple[str, str], int] = {}
        accumulate_match_state_stats(mp, ws, ko)
        per_match.append((ws, ko))
    blocks, _diag = load_match_fight_ev_blocks_from_data(db, match_players, player)
    assert len(blocks) == len(match_players)   # slicing below depends on it

    match_player_ids = [mp.id for mp in match_players]
    scores_by_match_player = load_impact_scores_for_match_players(db, match_player_ids)

    views: dict[str, PlayerViews] = {}
    for scope, limit in (("recent", RECENT_MATCH_LIMIT), ("career", None)):
        n = len(match_players) if limit is None else limit
        scope_match_players = match_players[:n]
        win_stats, ko_weights = _merge_state_aggregates(per_match[:n])

        scope_ids = {mp.id for mp in scope_match_players}
        scope_scores = {mid: s for mid, s in scores_by_match_player.items() if mid in scope_ids}
        profile, econ_aggregates = _build_profile_and_econ(player, scope_match_players, scope_scores)

        views[scope] = PlayerViews(
            win_stats, ko_weights,
            build_fight_ev_views_from_blocks(blocks[:n], player.id, draws),
            profile, econ_aggregates,
        )
    return views
