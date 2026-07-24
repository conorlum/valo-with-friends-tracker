from dataclasses import dataclass


@dataclass
class PlayerShoutout:
    """A single flattering, individual callout for one player -- distinct
    from Fun Stats, which only ever crowns one session- or match-wide winner
    per category. Every player passed in gets exactly one of these.
    """

    player_id: int
    display_name: str
    headline: str
    detail: str


def _plural(value: int) -> str:
    return "" if value == 1 else "s"


# (raw-counts field name, headline, detail template) -- ordered flashiest/most
# specific first, so a player's most distinctive achievement wins out over a
# more generic one when they'd otherwise qualify for both. "Round MVP" sits
# near the bottom since it's the closest in spirit to the Impact Leaderboard
# these sit directly below.
SHOUTOUT_CATEGORIES: list[tuple[str, str, str]] = [
    ("entry_kill_counts", "Entry Fragger", "{v} first blood{s}"),
    ("clutch_counts", "Clutch Gene", "{v} clutch round{s} won"),
    ("post_plant_kill_counts", "Post-Plant Menace", "{v} kill{s} defending the plant"),
    ("multi_kill_counts", "Multi-Kill Machine", "{v} round{s} with 3+ kills"),
    ("max_streak", "On A Heater", "a {v}-kill streak without dying"),
    ("round_changer_kill_counts", "Round Changer", "{v} kill{s} while outnumbered"),
    ("xvx_kill_counts", "Even-Fight Specialist", "{v} kill{s} in even-numbered fights"),
    ("kills_on_top_frag", "Shut Down Their Star", "{v} kill{s} on the enemy's top fragger"),
    ("late_kill_counts", "Closer", "{v} kill{s} after the 1-minute mark"),
    ("op_kill_counts", "Worth The Credits", "{v} kill{s} with the Operator"),
    ("eco_kill_counts", "Does More With Less", "{v} kill{s} on an eco/force buy"),
    ("traded_teammate_totals", "Avenger", "traded for a teammate {v} time{s}"),
    ("traded_by_teammate_totals", "Never Alone", "avenged by a teammate {v} time{s}"),
    ("mvp_counts", "Round MVP", "highest-Impact player in {v} round{s}"),
]

# How many rank-depths (1st place, 2nd place, ...) of each category are even
# considered as shoutout candidates -- bounded so a category's long tail can't
# hand someone a shoutout for a barely-there number. A player who's rank 5+ in
# something is better served by a different category, or a fallback.
_MAX_DEPTH = 5


def assign_shoutouts(
    roster: list[tuple[int, str]],
    raw_dicts: dict[str, dict[int, int]],
    best_single_round_impact: dict[int, float],
    anchor: tuple[int, str, str] | None = None,
) -> list[PlayerShoutout]:
    """Gives every player in `roster` exactly one flattering, individual
    callout, in roster order.

    This is the assignment problem: each player gets at most one category,
    each category at most one player, and a (player, category) pairing is
    only a candidate if the player ranks in that category's top _MAX_DEPTH
    with a nonzero value (a player with 0 Operator kills should never end up
    with an Operator-kills shoutout). Solved by picking the assignment that
    first covers as many players as possible, then -- among assignments
    covering that many -- has the smallest total of each player's rank
    within their assigned category (rank 0 = that category's outright
    leader), secondarily minimizing the worst individual rank anyone gets
    stuck with (so the cost of a thin category isn't dumped entirely on one
    player when an equally-cheap alternative spreads it out), via a bitmask
    DP over the roster. Anyone the matching can't cover falls back to their
    single best-Impact round, then (for at most one player) to `anchor` -- a
    caller-supplied (player_id, headline, detail) fallback, typically "led in
    average Impact." A player who still has nothing after all of that means
    the category catalog missed something real -- that's surfaced loudly
    rather than papered over, so it gets fixed instead of going unnoticed.

    `roster`: (player_id, display_name) pairs, in the order shoutouts should
    be returned.
    `raw_dicts`: player_id -> count, keyed by the category names in
    SHOUTOUT_CATEGORIES. Missing keys are treated as empty.
    `best_single_round_impact`: player_id -> their best single-round Impact,
    used for the "Highlight Reel" fallback.
    `anchor`: (player_id, headline, detail) used as a last-resort fallback
    for one player (typically whoever leads in average Impact), or None.
    """
    players_by_id = dict(roster)
    player_ids = [player_id for player_id, _display_name in roster]
    player_index = {player_id: i for i, player_id in enumerate(player_ids)}

    # Per category, candidates ranked best-first (value > 0 only), capped at
    # _MAX_DEPTH, as (player_index, rank, value).
    category_candidates: list[list[tuple[int, int, int]]] = []
    for raw_key, _headline, _template in SHOUTOUT_CATEGORIES:
        ranked = sorted(
            (
                (player_index[pid], v)
                for pid, v in raw_dicts.get(raw_key, {}).items()
                if v > 0 and pid in player_index
            ),
            key=lambda kv: (-kv[1], players_by_id.get(player_ids[kv[0]], "?")),
        )[:_MAX_DEPTH]
        category_candidates.append([(pi, rank, v) for rank, (pi, v) in enumerate(ranked)])

    # dp[mask] = (total rank-cost, worst individual rank) for the
    # lexicographically-best way to have matched exactly the players in
    # `mask`, considering categories processed so far. The worst-individual-
    # rank tiebreak matters because minimizing the sum alone can't tell apart
    # "spread the low ranks evenly" from "dump them all on one player" --
    # among equal-sum solutions, prefer the one that isn't unnecessarily
    # unkind to any single player.
    dp: dict[int, tuple[int, int]] = {0: (0, 0)}
    # choice[c][mask] = the player_index (or None) that category c was given
    # to reach `mask` immediately after processing category c, for backtracking.
    choice: list[dict[int, int | None]] = []

    for candidates in category_candidates:
        new_dp: dict[int, tuple[int, int]] = {}
        choice_c: dict[int, int | None] = {}
        for mask, (cost, worst) in dp.items():
            if mask not in new_dp or (cost, worst) < new_dp[mask]:
                new_dp[mask] = (cost, worst)
                choice_c[mask] = None
            for player_index_, rank, _value in candidates:
                bit = 1 << player_index_
                if mask & bit:
                    continue
                new_mask = mask | bit
                new_state = (cost + rank, max(worst, rank))
                if new_mask not in new_dp or new_state < new_dp[new_mask]:
                    new_dp[new_mask] = new_state
                    choice_c[new_mask] = player_index_
        dp = new_dp
        choice.append(choice_c)

    best_mask = max(dp, key=lambda mask: (bin(mask).count("1"), tuple(-v for v in dp[mask]))) if dp else 0

    category_of_player: dict[int, int] = {}
    mask = best_mask
    for category_i in range(len(category_candidates) - 1, -1, -1):
        player_index_ = choice[category_i].get(mask)
        if player_index_ is not None:
            category_of_player[player_index_] = category_i
            mask &= ~(1 << player_index_)

    assigned: dict[int, PlayerShoutout] = {}
    for player_index_, category_i in category_of_player.items():
        player_id = player_ids[player_index_]
        _raw_key, headline, template = SHOUTOUT_CATEGORIES[category_i]
        rank_by_player = {pi: (rank, v) for pi, rank, v in category_candidates[category_i]}
        _rank, value = rank_by_player[player_index_]
        assigned[player_id] = PlayerShoutout(
            player_id=player_id,
            display_name=players_by_id.get(player_id, "?"),
            headline=headline,
            detail=template.format(v=value, s=_plural(value)),
        )

    # Fallback 1: best single round, strongest first.
    for player_id, impact in sorted(best_single_round_impact.items(), key=lambda kv: -kv[1]):
        if player_id in assigned:
            continue
        assigned[player_id] = PlayerShoutout(
            player_id=player_id,
            display_name=players_by_id.get(player_id, "?"),
            headline="Highlight Reel",
            detail=f"{round(impact)} Impact in a single round",
        )

    # Fallback 2: caller-supplied anchor, claims at most one remaining player.
    if anchor is not None:
        anchor_player_id, anchor_headline, anchor_detail = anchor
        if anchor_player_id not in assigned:
            assigned[anchor_player_id] = PlayerShoutout(
                player_id=anchor_player_id,
                display_name=players_by_id.get(anchor_player_id, "?"),
                headline=anchor_headline,
                detail=anchor_detail,
            )

    shoutouts: list[PlayerShoutout] = []
    for player_id, display_name in roster:
        if player_id in assigned:
            shoutouts.append(assigned[player_id])
        else:
            shoutouts.append(
                PlayerShoutout(
                    player_id=player_id,
                    display_name=display_name,
                    headline="UH OH",
                    detail="Conor fucked up, fix this",
                )
            )
    return shoutouts
