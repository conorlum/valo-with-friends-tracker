"""One-load, one-replay orchestration producing every cached player-page
product (state diagram aggregates, fight-EV views, and -- since docs/
player_page_render_speed.txt Step 2 -- the profile summary and econ
aggregates too) from a single DB hydration pass.

Step 8 collapsed the two independent replay passes (the state-diagram
diamonds' manual walk, and state_replay's fight-EV replay) into one:
_replay_all_matches calls app.services.state_replay.replay_match ONCE per
match, and both build_match_fight_ev_block_from_replay and
accumulate_state_stats_from_replay consume that SAME (entries, duels) pair.
Profile/econ (Step 2) are a third and fourth pass over the shared
load_player_match_data rows, not yet folded into the replay -- see the doc's
Step 8 section for why (they're built from RoundPlayerStat/ImpactScore, not
from kill-event replay, so there's no shared work to collapse there).
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import MatchPlayer, Player
from app.services.economy_graphs import compute_econ_aggregates, econ_samples_from_data
from app.services.fight_ev import (
    PAGE_BOOTSTRAP_DRAWS,
    FightEvViews,
    build_fight_ev_views_from_blocks,
    build_match_fight_ev_block_from_replay,
)
from app.services.friends import list_friend_ids
from app.services.player_data import (
    RECENT_MATCH_LIMIT,
    load_impact_scores_for_match_players,
    load_player_match_data,
    match_input_from_data,
)
from app.services.player_graphs import accumulate_state_stats_from_replay
from app.services.player_profile_types import PlayerProfile, build_player_profile_from_match_data
from app.services.state_replay import (
    DuelOccurrence,
    MatchInput,
    ReplayDiagnostics,
    StateEntryOccurrence,
    replay_match,
)


@dataclass
class PlayerViews:
    win_stats: dict[str, dict[str, int]]
    kill_order_weights: dict[tuple[str, str], int]
    fight_ev: FightEvViews
    profile: PlayerProfile
    econ_aggregates: dict


def _replay_all_matches(
    match_players: list[MatchPlayer],
) -> tuple[
    list[tuple[MatchPlayer, MatchInput, list[StateEntryOccurrence], list[DuelOccurrence]]], ReplayDiagnostics
]:
    """ONE match_input_from_data + replay_match() call per match_player --
    the shared-replay half of Step 8. Returns (match_player, match_input,
    entries, duels) tuples in the SAME order as `match_players`, so callers
    can zip results back against per-scope slices exactly like the old
    two-separate-passes code did. match_input is carried along (not rebuilt
    a second time) since build_match_fight_ev_block_from_replay needs it too."""
    diagnostics = ReplayDiagnostics()
    replays: list[tuple[MatchPlayer, MatchInput, list[StateEntryOccurrence], list[DuelOccurrence]]] = []
    for mp in match_players:
        match_input = match_input_from_data(mp)
        entries, duels, _ = replay_match(match_input, diagnostics)
        replays.append((mp, match_input, entries, duels))
    return replays, diagnostics


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
    """One load, one replay, all four products (see module docstring)."""
    match_players = load_player_match_data(db, player, match_limit)
    roster_player_ids = list_friend_ids(db, player.id)
    replays, _diag = _replay_all_matches(match_players)

    win_stats: dict[str, dict[str, int]] = {}
    kill_order_weights: dict[tuple[str, str], int] = {}
    blocks = []
    for mp, match_input, entries, duels in replays:
        accumulate_state_stats_from_replay(entries, duels, mp.team, mp.id, win_stats, kill_order_weights)
        blocks.append(build_match_fight_ev_block_from_replay(mp, match_input, entries, duels, roster_player_ids))

    match_player_ids = [mp.id for mp in match_players]
    scores_by_match_player = load_impact_scores_for_match_players(db, match_player_ids)
    profile, econ_aggregates = _build_profile_and_econ(player, match_players, scores_by_match_player)

    return PlayerViews(
        win_stats, kill_order_weights,
        build_fight_ev_views_from_blocks(blocks, player.id, draws),
        profile, econ_aggregates,
    )


def compute_player_views_by_scope(
    db: Session, player: Player, draws: int = PAGE_BOOTSTRAP_DRAWS,
) -> dict[str, PlayerViews]:
    """Both scopes off ONE career load and ONE replay pass. `recent` is the
    first RECENT_MATCH_LIMIT rows of the career load -- which is only
    correct because load_player_match_data always orders most-recent-first.
    ORM hydration and round replay happen once; only the bootstrap (which
    genuinely differs per match subset) and the profile/econ builders
    (which need a per-scope slice of scores, see Step 2b) run per scope."""
    match_players = load_player_match_data(db, player, match_limit=None)
    roster_player_ids = list_friend_ids(db, player.id)
    replays, _diag = _replay_all_matches(match_players)
    assert len(replays) == len(match_players)   # slicing below depends on it

    per_match_state: list[tuple[dict[str, dict[str, int]], dict[tuple[str, str], int]]] = []
    blocks = []
    for mp, match_input, entries, duels in replays:
        ws: dict[str, dict[str, int]] = {}
        ko: dict[tuple[str, str], int] = {}
        accumulate_state_stats_from_replay(entries, duels, mp.team, mp.id, ws, ko)
        per_match_state.append((ws, ko))
        blocks.append(build_match_fight_ev_block_from_replay(mp, match_input, entries, duels, roster_player_ids))

    match_player_ids = [mp.id for mp in match_players]
    scores_by_match_player = load_impact_scores_for_match_players(db, match_player_ids)

    views: dict[str, PlayerViews] = {}
    for scope, limit in (("recent", RECENT_MATCH_LIMIT), ("career", None)):
        n = len(match_players) if limit is None else limit
        scope_match_players = match_players[:n]
        win_stats, ko_weights = _merge_state_aggregates(per_match_state[:n])

        scope_ids = {mp.id for mp in scope_match_players}
        scope_scores = {mid: s for mid, s in scores_by_match_player.items() if mid in scope_ids}
        profile, econ_aggregates = _build_profile_and_econ(player, scope_match_players, scope_scores)

        views[scope] = PlayerViews(
            win_stats, ko_weights,
            build_fight_ev_views_from_blocks(blocks[:n], player.id, draws),
            profile, econ_aggregates,
        )
    return views
