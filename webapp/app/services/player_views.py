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
from app.services.agent_roles import filter_agents
from app.services.circle import circle_teammate_ids
from app.services.player_data import (
    RECENT_MATCH_LIMIT,
    load_impact_scores_for_match_players,
    load_player_match_data,
    match_input_from_data,
)
from app.services.player_graphs import accumulate_state_stats_from_replay
from app.services.player_profile_types import (
    PlayerProfile,
    build_player_profile_from_match_data,
    compute_pistol_match_stats,
)
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
    pistol_match_stats: dict[str, int]


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
) -> tuple[PlayerProfile, dict, dict]:
    """match_players_newest_first is load_player_match_data's own order (or
    a newest-first SLICE of it, per scope); build_player_profile_from_match_data
    needs oldest-first (get_player_profile's convention, which the router's
    chart_data/template rely on), so this reverses just for that call. Econ
    aggregation and pistol-match aggregation are both order-independent."""
    oldest_first = list(reversed(match_players_newest_first))
    profile = build_player_profile_from_match_data(player, oldest_first, scores_by_match_player)
    econ_samples = econ_samples_from_data(match_players_newest_first)
    econ_aggregates = compute_econ_aggregates(econ_samples)
    pistol_match_stats = compute_pistol_match_stats(match_players_newest_first)
    return profile, econ_aggregates, pistol_match_stats


def _roster_player_ids(db: Session, player: Player, player_ids: frozenset[int] | None) -> set[int]:
    """The fight-EV teammate pool: the player's circle minus their own
    accounts. A group's members share one circle, so any member gives the
    same pool (the lowest id, for a stable choice)."""
    if not player_ids:
        return circle_teammate_ids(db, player.id)
    return circle_teammate_ids(db, min(player_ids))


def compute_player_views(
    db: Session, player: Player, match_limit: int | None,
    draws: int = PAGE_BOOTSTRAP_DRAWS,
    player_ids: frozenset[int] | None = None,
    agents: frozenset[str] | None = None,
) -> PlayerViews:
    """One load, one replay, all four products (see module docstring).

    `player_ids` computes an account group's combined view instead: every
    one of those accounts' matches, profiled under `player` (the page being
    viewed). The bootstrap is then seeded by the group's lowest player ID,
    so the combined numbers don't depend on which account's page they were
    computed from (they're cached once per group)."""
    match_players = load_player_match_data(db, player, match_limit, player_ids=player_ids, agents=agents)
    roster_player_ids = _roster_player_ids(db, player, player_ids)
    seed_player_id = min(player_ids) if player_ids else player.id
    replays, _diag = _replay_all_matches(match_players)

    win_stats: dict[str, dict[str, int]] = {}
    kill_order_weights: dict[tuple[str, str], int] = {}
    blocks = []
    for mp, match_input, entries, duels in replays:
        accumulate_state_stats_from_replay(entries, duels, mp.team, mp.id, win_stats, kill_order_weights)
        blocks.append(build_match_fight_ev_block_from_replay(mp, match_input, entries, duels, roster_player_ids))

    match_player_ids = [mp.id for mp in match_players]
    scores_by_match_player = load_impact_scores_for_match_players(db, match_player_ids)
    profile, econ_aggregates, pistol_match_stats = _build_profile_and_econ(
        player, match_players, scores_by_match_player
    )

    return PlayerViews(
        win_stats, kill_order_weights,
        build_fight_ev_views_from_blocks(blocks, seed_player_id, draws),
        profile, econ_aggregates, pistol_match_stats,
    )


def compute_player_views_by_scope(
    db: Session, player: Player, draws: int = PAGE_BOOTSTRAP_DRAWS,
    player_ids: frozenset[int] | None = None,
) -> dict[str, PlayerViews]:
    """Both scopes off ONE career load and ONE replay pass. `recent` is the
    first RECENT_MATCH_LIMIT rows of the career load -- which is only
    correct because load_player_match_data always orders most-recent-first.
    ORM hydration and round replay happen once; only the bootstrap (which
    genuinely differs per match subset) and the profile/econ builders
    (which need a per-scope slice of scores, see Step 2b) run per scope.
    `player_ids` is an account group's combined view, as in compute_player_views."""
    return compute_player_views_by_filter(db, player, ("",), draws, player_ids)


def _views_from_rows(
    player: Player, indices: list[int], match_players: list[MatchPlayer],
    per_match_state: list, blocks: list, scores_by_match_player: dict,
    seed_player_id: int, draws: int,
) -> PlayerViews:
    """One PlayerViews from the rows at `indices` (load order, newest first)
    of the shared load, its per-match state and fight-EV blocks."""
    rows = [match_players[i] for i in indices]
    win_stats, ko_weights = _merge_state_aggregates([per_match_state[i] for i in indices])
    ids = {mp.id for mp in rows}
    scope_scores = {mid: s for mid, s in scores_by_match_player.items() if mid in ids}
    profile, econ_aggregates, pistol_match_stats = _build_profile_and_econ(player, rows, scope_scores)
    return PlayerViews(
        win_stats, ko_weights,
        build_fight_ev_views_from_blocks([blocks[i] for i in indices], seed_player_id, draws),
        profile, econ_aggregates, pistol_match_stats,
    )


def compute_player_views_by_filter(
    db: Session, player: Player, keys, draws: int = PAGE_BOOTSTRAP_DRAWS,
    player_ids: frozenset[int] | None = None,
) -> dict[str, PlayerViews]:
    """Both scopes of every filter key off ONE career load and ONE replay.
    A key is "" (all rows), "role:<role>" or "agent:<Name>"; its rows are
    the loaded rows whose agent the key selects (agent_roles.filter_agents
    over the agents played), in load order. `recent` is the first
    RECENT_MATCH_LIMIT of those, `career` all of them. Output keys are
    cache_scope_key(scope, key). The bootstrap is seeded the same for every
    filter. `player_ids` is an account group's combined view."""
    from app.services.player_view_cache import cache_scope_key   # it imports this module
    match_players = load_player_match_data(db, player, match_limit=None, player_ids=player_ids)
    roster_player_ids = _roster_player_ids(db, player, player_ids)
    seed_player_id = min(player_ids) if player_ids else player.id
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

    scores_by_match_player = load_impact_scores_for_match_players(db, [mp.id for mp in match_players])
    agents_played = {mp.agent for mp in match_players}

    views: dict[str, PlayerViews] = {}
    for key in keys:
        if key:
            wanted = filter_agents(key, agents_played)
            indices = [i for i, mp in enumerate(match_players) if mp.agent in wanted]
        else:
            indices = list(range(len(match_players)))
        for scope, chosen in (("recent", indices[:RECENT_MATCH_LIMIT]), ("career", indices)):
            views[cache_scope_key(scope, key)] = _views_from_rows(
                player, chosen, match_players, per_match_state, blocks,
                scores_by_match_player, seed_player_id, draws,
            )
    return views
