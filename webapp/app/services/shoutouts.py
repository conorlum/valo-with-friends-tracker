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

# How many rank-depths (1st place, 2nd place, ...) to try per category before
# moving on -- bounded so the assignment pass can't run away on a huge roster.
_MAX_DEPTH = 4


def assign_shoutouts(
    roster: list[tuple[int, str]],
    raw_dicts: dict[str, dict[int, int]],
    best_single_round_impact: dict[int, float],
    anchor: tuple[int, str, str] | None = None,
) -> list[PlayerShoutout]:
    """Gives every player in `roster` exactly one flattering, individual
    callout, in roster order.

    Pass 1 walks the category catalog rank-by-rank (every category's outright
    leader first, then runners-up, ...) so each player's most distinctive
    achievement gets first claim, and no two players share a category where
    avoidable. Anyone left over falls back to their single best-Impact round,
    then (for at most one player) to `anchor` -- a caller-supplied
    (player_id, headline, detail) fallback, typically "led in average
    Impact." A player who still has nothing after all of that means the
    category catalog missed something real -- that's surfaced loudly rather
    than papered over, so it gets fixed instead of going unnoticed.

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

    ranked_by_category: dict[str, list[tuple[int, int]]] = {
        key: sorted(
            ((pid, v) for pid, v in raw_dicts.get(key, {}).items() if v > 0),
            key=lambda kv: (-kv[1], players_by_id.get(kv[0], "?")),
        )
        for key, _headline, _template in SHOUTOUT_CATEGORIES
    }

    assigned: dict[int, PlayerShoutout] = {}
    used_categories: set[str] = set()

    for depth in range(_MAX_DEPTH):
        for raw_key, headline, template in SHOUTOUT_CATEGORIES:
            if raw_key in used_categories:
                continue
            ranked = ranked_by_category[raw_key]
            if depth >= len(ranked):
                continue
            player_id, value = ranked[depth]
            if player_id in assigned:
                continue
            assigned[player_id] = PlayerShoutout(
                player_id=player_id,
                display_name=players_by_id.get(player_id, "?"),
                headline=headline,
                detail=template.format(v=value, s=_plural(value)),
            )
            used_categories.add(raw_key)

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
