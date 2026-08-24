"""Per-map attacker vs. defender round-win rate -- "is this map attack-sided,
defense-sided, or neutral?"

The attacking team for a given round is NOT stored anywhere (tracker.gg's API
exposes no side field), but it's fully derivable from data we already have,
via a pattern empirically verified against 20 regulation matches and 10
overtime matches (2026-08-23, this DB): team-1 ("Team A"/Red) always attacks
first in round 1 -- this is a tracker.gg/Riot labeling convention (team color
reflects starting side), not a per-match coin flip. Sides swap once at the
round-13 half boundary (standard MR12 rules), and in overtime round 25 resets
to the SAME side as round 1, then alternates every single round after that
(unlike regulation's swap-every-12) -- every planted OT round across all 10
sampled OT matches (43/43) matched this rule with zero exceptions.

See attacking_team_for_round for the resulting pure function of round number.
"""

import math
from dataclasses import dataclass

from app.models import Match
from app.models.match import Team
from app.services.player_graphs import win_color

# How many standard deviations a map's attack-win% must be from 50% before
# it's labeled "sided" rather than "neutral". Chosen as the standard 95%-CI
# cutoff rather than a flat percentage-point gap: this dataset's per-map round
# counts range from ~400 to ~3800 (2026-08-23 snapshot), so a fixed pp
# threshold would either flag small-sample maps on pure noise or miss a real
# few-point bias on a heavily-played map. A z-score scales the bar to each
# map's own sample size instead.
SIDED_Z_THRESHOLD = 2.0


def attacking_team_for_round(round_number: int) -> Team:
    if round_number <= 12:
        return Team.TEAM_1
    if round_number <= 24:
        return Team.TEAM_2
    # Overtime: round 25 resets to the same side as round 1, then the
    # attacker alternates every single round (not every 2, unlike halves).
    offset = round_number - 25
    return Team.TEAM_1 if offset % 2 == 0 else Team.TEAM_2


def _winner_team(outcome: str | None) -> Team | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return Team.TEAM_1
    if outcome.startswith("Team B"):
        return Team.TEAM_2
    return None


def _empty_map_bucket() -> dict[str, int]:
    return {"matches": 0, "attack_wins": 0, "defense_wins": 0}


def compute_map_side_stats(matches: list[Match], roster_player_ids: set[int]) -> dict:
    """Pure aggregation over already-loaded Match rows (match_players + rounds
    must be eager-loaded by the caller). Returns
    {"friends": {map_name: {"matches", "attack_wins", "defense_wins"}}, "all": {...}} --
    "friends" only counts a match if a tracked roster player was in it (either
    team), "all" counts every match. A round with no decisive outcome (should
    not happen for real ingested rows, but defensively skipped) contributes to
    neither the match's win/loss buckets."""
    variants: dict[str, dict[str, dict[str, int]]] = {"friends": {}, "all": {}}

    for match in matches:
        map_name = match.map_name or "Unknown"
        is_friends_match = any(mp.player_id in roster_player_ids for mp in match.match_players)

        targets = [variants["all"]]
        if is_friends_match:
            targets.append(variants["friends"])

        for buckets in targets:
            bucket = buckets.setdefault(map_name, _empty_map_bucket())
            bucket["matches"] += 1
            for r in match.rounds:
                winner = _winner_team(r.outcome)
                if winner is None:
                    continue
                attacker = attacking_team_for_round(r.round_number)
                if winner == attacker:
                    bucket["attack_wins"] += 1
                else:
                    bucket["defense_wins"] += 1

    return variants


@dataclass
class MapSideRow:
    map_name: str
    matches: int
    rounds: int
    attack_win_pct: float
    fill: str
    label: str  # "Attacker-sided" / "Defender-sided" / "Neutral"
    z: float  # signed significance score -- see _z. Drives both `label` and row order.


@dataclass
class MapSideStats:
    rows: list[MapSideRow]


def _z(attack_win_pct: float, rounds: int) -> float:
    if rounds == 0:
        return 0.0
    se = math.sqrt(0.25 / rounds)
    return (attack_win_pct - 0.5) / se


def _classify(z: float) -> str:
    if z >= SIDED_Z_THRESHOLD:
        return "Attacker-sided"
    if z <= -SIDED_Z_THRESHOLD:
        return "Defender-sided"
    return "Neutral"


def build_map_side_stats_from_aggregates(variant: dict) -> MapSideStats:
    rows: list[MapSideRow] = []
    for map_name, bucket in variant.items():
        rounds = bucket["attack_wins"] + bucket["defense_wins"]
        if rounds == 0:
            continue
        attack_win_pct = bucket["attack_wins"] / rounds
        z = _z(attack_win_pct, rounds)
        rows.append(
            MapSideRow(
                map_name=map_name,
                matches=bucket["matches"],
                rounds=rounds,
                attack_win_pct=attack_win_pct,
                fill=win_color(attack_win_pct),
                label=_classify(z),
                z=z,
            )
        )

    # Sort by significance (z), not raw attack_win_pct: two maps can round to
    # the same displayed percentage while one is "Attacker-sided" and the
    # other "Neutral" because it has far fewer rounds behind it (see
    # SIDED_Z_THRESHOLD) -- sorting by raw pct could then rank the Neutral
    # map above the Attacker-sided one, which reads as a contradiction next
    # to the Verdict column. Sorting by z guarantees every Attacker-sided row
    # outranks every Neutral row, which outranks every Defender-sided row.
    rows.sort(key=lambda r: -r.z)
    return MapSideStats(rows=rows)
