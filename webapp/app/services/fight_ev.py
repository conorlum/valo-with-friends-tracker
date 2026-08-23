"""Fight-EV Diamond: a leverage-weighted comparison of a player's duel
conversion rate against a teammate benchmark, at every 5-by-5 man-advantage
state, split by side and by two teammate pools (tracked roster / everyone).

See docs/fight_ev_diamond.txt for the full design. This module implements
sections 5-8 of that handoff: per-match aggregation, point estimates, and
the cluster bootstrap. Presentation (FightEvNode/FightEvDiagram, the SVG
diamond, the player-page route) is a separate, not-yet-implemented layer on
top of the pure functions here -- see build_fight_ev_views's docstring.
"""

from __future__ import annotations

import enum
import hashlib
import struct
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from sqlalchemy.orm import Session

from app.models import MatchPlayer, Player
from app.models.match import Team
from app.services.friends import list_friend_ids
from app.services.player_data import load_player_match_data, match_input_from_data
from app.services.state_replay import (
    DuelOccurrence,
    MatchInput,
    ReplayDiagnostics,
    RoundInput,
    StateEntryOccurrence,
    attacking_team_for_round,
    replay_match,
)

Side = Literal["attacking", "defending"]
StateKey = tuple[Side, int, int]

TeammatePool = Literal["tracked_roster", "all_teammates"]

# Bumping this constant invalidates every previously-served bootstrap seed --
# use it to force a reshuffle if the replay/aggregation logic changes in a
# way that should not be silently blended with old draws.
# v2: switched bootstrap_cell's resampling from Python's random.Random to
# numpy's Generator (a vectorized draws-array is what made the page-load-time
# fix possible) -- same statistical procedure, different pseudorandom
# sequence, so old seeds must not be blended with new draws.
# v3: _bootstrap_seed switched from Python's hash() (PYTHONHASHSEED-randomized
# per process, since the tuple contains the `side` string -- meaning the web
# server and a recompute script produced different seeds for the same cell)
# to a process-stable SHA-256 digest. Required before caching: a cached blob
# and a live recompute must agree on every seed.
CALCULATION_VERSION = 3

# Also part of the player-view cache's validity contract (app.services.
# player_view_cache._validate_blob) -- a decoded blob must contain exactly
# these four view keys and every cell must have exactly this key set.
FIGHT_EV_VIEW_KEYS = (
    "attacking_tracked_roster", "attacking_all_teammates",
    "defending_tracked_roster", "defending_all_teammates",
)
FIGHT_EV_CELL_KEYS = frozenset({
    "a", "b", "display_state", "m", "p_player", "n_player",
    "p_teammates", "n_teammates", "bootstrap",
})

DEFAULT_BOOTSTRAP_DRAWS = 2000

# Lower draw count used for the live player page (both the synchronous
# "recent" render and the async "career" htmx fragment) -- keeps a page
# load in the single-digit seconds on this local-only site instead of the
# ~40s four-view/2000-draw run scripts/validate_fight_ev.py uses offline.
# CI width shrinks with more draws, so this trades a slightly coarser
# interval for responsiveness; bump it if that tradeoff stops being worth it.
PAGE_BOOTSTRAP_DRAWS = 800

# Provisional interval-validity thresholds. Section 8 of the handoff requires
# these to come from running scripts/validate_fight_ev.py over real data and
# choosing values from the observed contributing-match/defined-draw
# distributions -- that calibration pass has not been run yet, so these are
# deliberately conservative placeholders. Until recalibrated, cells that
# don't clear them report INTERVAL_NOT_ESTIMABLE rather than a fabricated CI.
MIN_DEFINED_DRAW_FRACTION = 0.5
MIN_CONTRIBUTING_MATCHES = 5


@dataclass
class WinCounts:
    wins: int = 0
    entries: int = 0


@dataclass
class DuelCounts:
    kills: int = 0
    deaths: int = 0


@dataclass
class MatchFightEvBlock:
    match_id: int
    wins: dict[StateKey, WinCounts] = field(default_factory=dict)
    player_duels: dict[StateKey, DuelCounts] = field(default_factory=dict)
    roster_duels: dict[StateKey, DuelCounts] = field(default_factory=dict)
    all_teammate_duels: dict[StateKey, DuelCounts] = field(default_factory=dict)


class DisplayState(str, enum.Enum):
    NO_DATA = "NO_DATA"
    NON_POSITIVE_LEVERAGE = "NON_POSITIVE_LEVERAGE"
    INTERVAL_NOT_ESTIMABLE = "INTERVAL_NOT_ESTIMABLE"
    UNRESOLVED = "UNRESOLVED"
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"


@dataclass
class BootstrapResult:
    ci_low: float | None
    ci_high: float | None
    defined_draw_fraction: float
    contributing_matches_player: int
    contributing_matches_teammates: int
    contributing_matches_w_u: int
    contributing_matches_w_d: int


@dataclass
class FightEvCell:
    side: Side
    a: int
    b: int
    p_player: float | None
    n_player: int
    p_teammates: float | None
    n_teammates: int
    w_u: float | None
    w_d: float | None
    leverage: float | None
    m: float | None
    bootstrap: BootstrapResult | None
    display_state: DisplayState


def _team_perspective(
    team1_value: frozenset[int], team2_value: frozenset[int], target_team: Team
) -> frozenset[int]:
    return team1_value if target_team == Team.TEAM_1 else team2_value


def _opponent_perspective(
    team1_value: frozenset[int], team2_value: frozenset[int], target_team: Team
) -> frozenset[int]:
    return team2_value if target_team == Team.TEAM_1 else team1_value


def build_match_fight_ev_block(
    match_id: int,
    entries: list[StateEntryOccurrence],
    duels: list[DuelOccurrence],
    round_side_by_round_id: dict[int, Side | None],
    target_team: Team,
    target_match_player_id: int,
    match_player_team: dict[int, Team],
    match_player_to_player_id: dict[int, int],
    roster_player_ids: set[int],
) -> MatchFightEvBlock:
    block = MatchFightEvBlock(match_id=match_id)

    for entry in entries:
        side = round_side_by_round_id.get(entry.round_id)
        if side is None:
            continue
        own_alive = _team_perspective(entry.team1_alive_ids, entry.team2_alive_ids, target_team)
        opp_alive = _opponent_perspective(entry.team1_alive_ids, entry.team2_alive_ids, target_team)
        key: StateKey = (side, len(own_alive), len(opp_alive))
        counts = block.wins.setdefault(key, WinCounts())
        counts.entries += 1
        if entry.winner == target_team:
            counts.wins += 1

    for duel in duels:
        side = round_side_by_round_id.get(duel.round_id)
        if side is None:
            continue
        killer_team = match_player_team.get(duel.killer_match_player_id)
        victim_team = match_player_team.get(duel.victim_match_player_id)
        if killer_team is None or victim_team is None:
            continue
        if killer_team != target_team and victim_team != target_team:
            continue

        own_before = _team_perspective(duel.team1_alive_before, duel.team2_alive_before, target_team)
        opp_before = _opponent_perspective(duel.team1_alive_before, duel.team2_alive_before, target_team)
        key = (side, len(own_before), len(opp_before))

        is_kill = killer_team == target_team
        actor_match_player_id = duel.killer_match_player_id if is_kill else duel.victim_match_player_id

        def _add(bucket: dict[StateKey, DuelCounts]) -> None:
            counts = bucket.setdefault(key, DuelCounts())
            if is_kill:
                counts.kills += 1
            else:
                counts.deaths += 1

        if actor_match_player_id == target_match_player_id:
            _add(block.player_duels)
            continue

        _add(block.all_teammate_duels)
        actor_player_id = match_player_to_player_id.get(actor_match_player_id)
        if actor_player_id is not None and actor_player_id in roster_player_ids:
            _add(block.roster_duels)

    return block


def _sum_blocks(blocks: list[MatchFightEvBlock]) -> MatchFightEvBlock:
    summed = MatchFightEvBlock(match_id=-1)
    for block in blocks:
        for key, counts in block.wins.items():
            total = summed.wins.setdefault(key, WinCounts())
            total.wins += counts.wins
            total.entries += counts.entries
        for attr in ("player_duels", "roster_duels", "all_teammate_duels"):
            src = getattr(block, attr)
            dst = getattr(summed, attr)
            for key, counts in src.items():
                total = dst.setdefault(key, DuelCounts())
                total.kills += counts.kills
                total.deaths += counts.deaths
    return summed


def win_rate(wins: dict[StateKey, WinCounts], side: Side, a: int, b: int) -> float | None:
    if a < 0 or b < 0:
        return None
    if side == "attacking" and b == 0:
        return 1.0
    if side == "defending" and a == 0:
        return 0.0
    counts = wins.get((side, a, b))
    if counts is None or counts.entries == 0:
        return None
    return counts.wins / counts.entries


def duel_rate(duels: dict[StateKey, DuelCounts], key: StateKey) -> tuple[float | None, int]:
    counts = duels.get(key)
    if counts is None:
        return None, 0
    denom = counts.kills + counts.deaths
    if denom == 0:
        return None, 0
    return counts.kills / denom, denom


def _teammate_bucket_name(pool: TeammatePool) -> str:
    return "roster_duels" if pool == "tracked_roster" else "all_teammate_duels"


def _bootstrap_seed(player_id: int, side: Side, a: int, b: int, pool: TeammatePool) -> int:
    # Process-stable digest, NOT hash(): PYTHONHASHSEED randomizes
    # str hashing per process, so the old version gave the web server
    # and the recompute script different seeds for the same cell.
    # `pool` is still deliberately excluded -- section 8 of the handoff
    # requires the same sampled match indices across both teammate
    # benchmarks for a given draw.
    del pool
    raw = struct.pack(
        ">iiBBB", player_id, CALCULATION_VERSION, a, b,
        0 if side == "attacking" else 1,
    )
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big")


def compute_point_estimate(
    wins: dict[StateKey, WinCounts],
    player_duels: dict[StateKey, DuelCounts],
    teammate_duels: dict[StateKey, DuelCounts],
    side: Side,
    a: int,
    b: int,
) -> FightEvCell:
    key: StateKey = (side, a, b)
    p_y, n_y = duel_rate(player_duels, key)
    p_m, n_m = duel_rate(teammate_duels, key)
    w_u = win_rate(wins, side, a, b - 1)
    w_d = win_rate(wins, side, a - 1, b)

    if p_y is None or p_m is None or w_u is None or w_d is None:
        return FightEvCell(side, a, b, p_y, n_y, p_m, n_m, w_u, w_d, None, None, None, DisplayState.NO_DATA)

    leverage = w_u - w_d
    if leverage <= 0:
        return FightEvCell(
            side, a, b, p_y, n_y, p_m, n_m, w_u, w_d, leverage, None, None, DisplayState.NON_POSITIVE_LEVERAGE
        )

    m = (p_y - p_m) * leverage
    return FightEvCell(side, a, b, p_y, n_y, p_m, n_m, w_u, w_d, leverage, m, None, DisplayState.POSITIVE)


def _rail_rate(side: Side, rail_a: int, rail_b: int, wins: int, entries: int) -> float | None:
    """Same semantics as win_rate, but operating on already-summed win/entries
    totals for one specific rail cell -- used by bootstrap_cell's inner draw
    loop, which resamples raw per-match scalars rather than whole dicts."""
    if rail_a < 0 or rail_b < 0:
        return None
    if side == "attacking" and rail_b == 0:
        return 1.0
    if side == "defending" and rail_a == 0:
        return 0.0
    return (wins / entries) if entries > 0 else None


def _rail_rate_vectorized(
    side: Side, rail_a: int, rail_b: int, wins: np.ndarray, entries: np.ndarray, draws: int
) -> np.ndarray:
    """Vectorized counterpart to _rail_rate, used by bootstrap_cell's
    numpy draw arrays. The degenerate a==0/b==0 rails are a constant for
    every draw (not data-dependent), so those short-circuit without
    touching `wins`/`entries` at all."""
    if rail_a < 0 or rail_b < 0:
        return np.full(draws, np.nan)
    if side == "attacking" and rail_b == 0:
        return np.full(draws, 1.0)
    if side == "defending" and rail_a == 0:
        return np.full(draws, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(entries > 0, wins / entries, np.nan)


def bootstrap_cell(
    blocks: list[MatchFightEvBlock],
    side: Side,
    a: int,
    b: int,
    teammate_pool: TeammatePool,
    seed: int,
    draws: int = DEFAULT_BOOTSTRAP_DRAWS,
) -> BootstrapResult:
    teammate_attr = _teammate_bucket_name(teammate_pool)
    key: StateKey = (side, a, b)
    key_u: StateKey = (side, a, b - 1)
    key_d: StateKey = (side, a - 1, b)

    # Pull out just the scalars this cell needs from every match block once,
    # up front -- resampling these small tuples in the draw loop below is far
    # cheaper than re-summing whole per-match dicts 2,000 times (which is
    # what naively calling _sum_blocks per draw did originally: minutes per
    # cell against the real dataset instead of a fraction of a second).
    per_block = []
    teammate_dict_attr = teammate_attr
    for blk in blocks:
        pc = blk.player_duels.get(key)
        tc = getattr(blk, teammate_dict_attr).get(key)
        wu = blk.wins.get(key_u)
        wd = blk.wins.get(key_d)
        per_block.append(
            (
                pc.kills if pc else 0,
                pc.deaths if pc else 0,
                tc.kills if tc else 0,
                tc.deaths if tc else 0,
                wu.wins if wu else 0,
                wu.entries if wu else 0,
                wd.wins if wd else 0,
                wd.entries if wd else 0,
            )
        )

    contributing_player = sum(1 for row in per_block if row[0] + row[1] > 0)
    contributing_teammates = sum(1 for row in per_block if row[2] + row[3] > 0)

    def _contributing_win(rail_key: StateKey, entries_index: int) -> int:
        rail_side, rail_a, rail_b = rail_key
        if rail_a < 0 or rail_b < 0:
            return 0
        if rail_side == "attacking" and rail_b == 0:
            return len(blocks)  # definitional constant, not data-dependent
        if rail_side == "defending" and rail_a == 0:
            return len(blocks)
        return sum(1 for row in per_block if row[entries_index] > 0)

    contributing_w_u = _contributing_win(key_u, 5)
    contributing_w_d = _contributing_win(key_d, 7)

    n = len(blocks)
    per_block_arr = np.array(per_block, dtype=np.int64).reshape(n, 8)
    defined_fraction = 0.0
    ci_low = ci_high = None

    # Resample all `draws` cluster-bootstrap iterations at once instead of a
    # Python-level double loop (draws x n). That loop -- re-picking one of n
    # match blocks at a time via random.randrange and accumulating 8 scalars
    # per pick -- was the actual cost of a player-page load (see profiling in
    # the PR that added this): with n ~= 30 matches and draws=800, it's ~2.2M
    # randrange calls for a single cell, and there are up to 100 cells (4
    # views x 25 states) on one page. Gathering the same picks as a
    # numpy index array and summing along the match axis does the identical
    # sampling-with-replacement in compiled code instead of the interpreter.
    if n > 0 and draws > 0:
        rng = np.random.default_rng(seed)
        indices = rng.integers(0, n, size=(draws, n))
        # per_block_arr[indices] : (draws, n, 8) -> sum over the n axis -> (draws, 8)
        sums = per_block_arr[indices].sum(axis=1)
        pk, pd, tk, td, wu_w, wu_e, wd_w, wd_e = (sums[:, i] for i in range(8))

        with np.errstate(divide="ignore", invalid="ignore"):
            p_y = np.where(pk + pd > 0, pk / (pk + pd), np.nan)
            p_m = np.where(tk + td > 0, tk / (tk + td), np.nan)
            w_u = _rail_rate_vectorized(side, a, b - 1, wu_w, wu_e, draws)
            w_d = _rail_rate_vectorized(side, a - 1, b, wd_w, wd_e, draws)

            leverage = w_u - w_d
            values = (p_y - p_m) * leverage

        valid = np.isfinite(values) & (leverage > 0)
        defined_values = values[valid]

        defined_fraction = len(defined_values) / draws
        if len(defined_values) > 0:
            ci_low = float(np.percentile(defined_values, 2.5))
            ci_high = float(np.percentile(defined_values, 97.5))

    return BootstrapResult(
        ci_low=ci_low,
        ci_high=ci_high,
        defined_draw_fraction=defined_fraction,
        contributing_matches_player=contributing_player,
        contributing_matches_teammates=contributing_teammates,
        contributing_matches_w_u=contributing_w_u,
        contributing_matches_w_d=contributing_w_d,
    )


def _is_interval_estimable(bootstrap: BootstrapResult) -> bool:
    if bootstrap.defined_draw_fraction < MIN_DEFINED_DRAW_FRACTION:
        return False
    return (
        bootstrap.contributing_matches_player >= MIN_CONTRIBUTING_MATCHES
        and bootstrap.contributing_matches_teammates >= MIN_CONTRIBUTING_MATCHES
    )


def classify_cell(cell: FightEvCell, bootstrap: BootstrapResult) -> FightEvCell:
    """Combines a point estimate with its bootstrap result into the final
    display classification (section 9). Only called for cells that already
    have a defined `m` (i.e. display_state is still POSITIVE, the
    provisional placeholder compute_point_estimate leaves it in)."""
    if cell.display_state != DisplayState.POSITIVE:
        return cell
    if not _is_interval_estimable(bootstrap):
        return FightEvCell(
            cell.side, cell.a, cell.b, cell.p_player, cell.n_player, cell.p_teammates, cell.n_teammates,
            cell.w_u, cell.w_d, cell.leverage, cell.m, bootstrap, DisplayState.INTERVAL_NOT_ESTIMABLE,
        )
    if bootstrap.ci_low is None or bootstrap.ci_high is None:
        display_state = DisplayState.INTERVAL_NOT_ESTIMABLE
    elif bootstrap.ci_low > 0:
        display_state = DisplayState.POSITIVE
    elif bootstrap.ci_high < 0:
        display_state = DisplayState.NEGATIVE
    else:
        display_state = DisplayState.UNRESOLVED
    return FightEvCell(
        cell.side, cell.a, cell.b, cell.p_player, cell.n_player, cell.p_teammates, cell.n_teammates,
        cell.w_u, cell.w_d, cell.leverage, cell.m, bootstrap, display_state,
    )


def compute_fight_ev_view(
    blocks: list[MatchFightEvBlock],
    side: Side,
    teammate_pool: TeammatePool,
    player_id: int,
    draws: int = DEFAULT_BOOTSTRAP_DRAWS,
) -> list[FightEvCell]:
    """All 25 cells (a,b in 1..5) for one (side, teammate_pool) view."""
    aggregate = _sum_blocks(blocks)
    teammate_attr = _teammate_bucket_name(teammate_pool)

    cells = []
    for a in range(1, 6):
        for b in range(1, 6):
            cell = compute_point_estimate(aggregate.wins, aggregate.player_duels, getattr(aggregate, teammate_attr), side, a, b)
            if cell.display_state == DisplayState.POSITIVE:
                seed = _bootstrap_seed(player_id, side, a, b, teammate_pool)
                bootstrap = bootstrap_cell(blocks, side, a, b, teammate_pool, seed, draws)
                cell = classify_cell(cell, bootstrap)
            cells.append(cell)
    return cells


@dataclass
class FightEvViews:
    attacking_tracked_roster: list[FightEvCell]
    attacking_all_teammates: list[FightEvCell]
    defending_tracked_roster: list[FightEvCell]
    defending_all_teammates: list[FightEvCell]


def _serialize_cell(cell: FightEvCell) -> dict:
    """JSON-friendly projection of one cell -- just the fields the
    player-page's client-side renderer needs (app/templates/players/detail.html).
    Keeping node layout/color/text-formatting in JS (ported from the approved
    design mockup) instead of pre-rendering all 4 views x 25 tiles of SVG
    server-side keeps the page's DOM light -- the earlier all-SSR version
    quadrupled the SVG node count for no benefit, since only one view is ever
    visible at a time.
    """
    return {
        "a": cell.a,
        "b": cell.b,
        "display_state": cell.display_state.value,
        "m": cell.m,
        "p_player": cell.p_player,
        "n_player": cell.n_player,
        "p_teammates": cell.p_teammates,
        "n_teammates": cell.n_teammates,
        "bootstrap": None
        if cell.bootstrap is None
        else {
            "ci_low": cell.bootstrap.ci_low,
            "ci_high": cell.bootstrap.ci_high,
            "contributing_matches_player": cell.bootstrap.contributing_matches_player,
            "contributing_matches_teammates": cell.bootstrap.contributing_matches_teammates,
        },
    }


def serialize_fight_ev_views(views: FightEvViews) -> dict:
    return {
        view_key: [_serialize_cell(c) for c in getattr(views, view_key)]
        for view_key in FIGHT_EV_VIEW_KEYS
    }


def _round_side_map(round_inputs: list[RoundInput], target_team: Team) -> dict[int, Side | None]:
    mapping: dict[int, Side | None] = {}
    for round_input in round_inputs:
        attacking_team = attacking_team_for_round(round_input.round_number)
        if attacking_team is None:
            mapping[round_input.round_id] = None
        else:
            mapping[round_input.round_id] = "attacking" if attacking_team == target_team else "defending"
    return mapping


def build_match_fight_ev_block_from_replay(
    match_player: MatchPlayer,
    match_input: MatchInput,
    entries: list[StateEntryOccurrence],
    duels: list[DuelOccurrence],
    roster_player_ids: set[int],
) -> MatchFightEvBlock:
    """Per-match block-building given an ALREADY-COMPUTED replay
    (entries/duels) for match_input -- the fight-EV half of Step 8's shared
    replay pass (docs/player_page_render_speed.txt). A caller that also needs
    to replay this same match for another product (the round-win/kill-order
    diamonds -- see app.services.player_graphs.accumulate_state_stats_from_
    replay) calls replay_match() itself ONCE and passes the result both
    places, instead of this function replaying again."""
    match = match_player.match
    target_team = match_player.team
    match_player_team = {mp.id: mp.team for mp in match.match_players}
    match_player_to_player_id = {mp.id: mp.player_id for mp in match.match_players}
    round_side = _round_side_map(match_input.rounds, target_team)
    return build_match_fight_ev_block(
        match.id, entries, duels, round_side, target_team, match_player.id,
        match_player_team, match_player_to_player_id, roster_player_ids,
    )


def load_match_fight_ev_blocks_from_data(
    db: Session,
    match_players: list[MatchPlayer],
    player: Player,
) -> tuple[list[MatchFightEvBlock], ReplayDiagnostics]:
    """Replays each pre-loaded match_player's match once via
    app.services.state_replay, returning one MatchFightEvBlock per input row
    (same order) plus the merged replay diagnostics (exclusion reasons,
    ambiguous-lifecycle and equal-time-ambiguity counts, etc.) across all of
    them. `db` is still needed for list_friend_ids.

    INVARIANT: returns exactly one block per input match_player, in the same
    order. app.services.player_views.compute_player_views_by_scope slices
    this list by scope.

    Kept as its own single-purpose loop (rather than always going through
    app.services.player_views' shared-replay orchestration) for callers that
    only need fight-EV, e.g. scripts/validate_fight_ev.py via
    load_match_fight_ev_blocks below.
    """
    roster_player_ids = list_friend_ids(db, player.id)
    diagnostics = ReplayDiagnostics()

    blocks: list[MatchFightEvBlock] = []
    for match_player in match_players:
        match_input = match_input_from_data(match_player)
        entries, duels, _ = replay_match(match_input, diagnostics)
        blocks.append(build_match_fight_ev_block_from_replay(match_player, match_input, entries, duels, roster_player_ids))

    return blocks, diagnostics


def build_fight_ev_views_from_blocks(
    blocks: list[MatchFightEvBlock], player_id: int, draws: int = DEFAULT_BOOTSTRAP_DRAWS
) -> FightEvViews:
    return FightEvViews(
        attacking_tracked_roster=compute_fight_ev_view(blocks, "attacking", "tracked_roster", player_id, draws),
        attacking_all_teammates=compute_fight_ev_view(blocks, "attacking", "all_teammates", player_id, draws),
        defending_tracked_roster=compute_fight_ev_view(blocks, "defending", "tracked_roster", player_id, draws),
        defending_all_teammates=compute_fight_ev_view(blocks, "defending", "all_teammates", player_id, draws),
    )


def load_match_fight_ev_blocks(
    db: Session, player: Player, match_limit: int | None = None
) -> tuple[list[MatchFightEvBlock], ReplayDiagnostics]:
    """Loads matches `player` appears in and replays each one once. Shared by
    build_fight_ev_views (the four displayed views) and
    scripts/validate_fight_ev.py (which needs the raw diagnostics and blocks,
    not just the final cells).

    `match_limit` mirrors app.services.player_graphs.build_state_diagrams's
    parameter of the same name -- None loads full history (the "career" view
    on the player page), otherwise the `match_limit` most recent matches (the
    "recent" view, and also what keeps the bootstrap's O(draws * matches) cost
    small enough for a synchronous page load; full-history "career" is loaded
    async via the existing htmx fragment).
    """
    match_players = load_player_match_data(db, player, match_limit)
    return load_match_fight_ev_blocks_from_data(db, match_players, player)


def build_fight_ev_views(
    db: Session, player: Player, match_limit: int | None = None, draws: int = DEFAULT_BOOTSTRAP_DRAWS
) -> FightEvViews:
    """Returns all four displayed views in a single pass (one DB/replay/
    bootstrap cost per request, per section 6's router contract). See
    load_match_fight_ev_blocks for what `match_limit` does.
    """
    blocks, _diagnostics = load_match_fight_ev_blocks(db, player, match_limit)
    return build_fight_ev_views_from_blocks(blocks, player.id, draws)
