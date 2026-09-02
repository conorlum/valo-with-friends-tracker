"""Turns match data into ONE differential observation per round, then fits
and scores candidate Impact weightings against forward-looking targets
under nested cross-validation.

Internal tooling only -- nothing here is imported by app/main.py, any
router, or any template. See
docs/superpowers/specs/2026-09-01-impact-win-correlation-design.md.

Observation unit is one row per round with team-A-minus-team-B features.
The two (round, team) rows of a round have perfectly complementary
outcomes, so treating them as two observations would double every
apparent sample size.
"""

from dataclasses import dataclass

import numpy as np

from app.models.match import Team
from app.scoring.impact import FULL_BUY_THRESHOLD
from app.services.map_side_stats import attacking_team_for_round

SURRENDER_SUFFIX = "Surrendered Win"


class MissingImpactRows(Exception):
    """Raised when a playable round has no impact rows. Never swallowed into
    a zero-valued observation -- absent data is not zero impact."""


@dataclass
class RoundObservation:
    match_id: int
    round_id: int
    round_number: int

    # Component differentials (team A minus team B).
    damage: float
    econ_impact: float
    time_impact: float
    swing_impact: float

    # The single kill baseline: kills_A - kills_B. Deaths are ~redundant
    # (deaths_A == kills_B in 99.1% of rounds in this DB), so carrying them
    # separately would be the same column twice.
    kill_diff: float

    # The EXACT stored/calculated impact differential, carried alongside the
    # components rather than reconstructed from them. impact.py round()s
    # kill_impact, death_impact and each component independently, so
    # rebuilding "current Impact" from the four component columns accumulates
    # a couple of points of error per player-round -- across 10 players and
    # ~21 rounds that is enough to move a close comparison. The
    # current_impact candidate reads this field directly.
    impact_diff: float

    # Controls. Score is BEFORE this round, economy is at the START of this
    # round, side is DURING this round, and the round's own result is kept
    # as its own separate control -- never folded into the others.
    score_diff_before: int
    attacking_is_team_a: bool
    loadout_diff: float
    full_buy_count_diff: int

    # Outcomes.
    round_won_by_team_a: bool | None
    match_won_by_team_a: bool | None
    is_terminal: bool


def _winner_is_team_a(outcome: str | None) -> bool | None:
    if not outcome or outcome.endswith(SURRENDER_SUFFIX):
        return None
    if outcome.startswith("Team A"):
        return True
    if outcome.startswith("Team B"):
        return False
    return None


def _match_won_by_team_a(match) -> bool | None:
    """None for a tie -- excluded from every denominator, matching
    match_win()'s contract in app.services.player_profile_types."""
    if match.team1_rounds_won == match.team2_rounds_won:
        return None
    return match.team1_rounds_won > match.team2_rounds_won


def build_observations_for_match(match, calculated_rows) -> list[RoundObservation]:
    """`calculated_rows` are CalculatedImpact objects from
    build_impact_rows_for_match for this match only. Surrender placeholder
    rounds are dropped -- nobody played them."""
    team_by_mp = {
        mp.id: (mp.team.value if hasattr(mp.team, "value") else mp.team)
        for mp in match.match_players
    }
    team_a = Team.TEAM_1.value

    def team_of(match_player_id: int) -> str:
        # An unknown id silently defaulting to "not team A" would quietly
        # assign a stranger's kills and impact to team B.
        if match_player_id not in team_by_mp:
            raise MissingImpactRows(
                f"match {match.id}: match_player {match_player_id} is not in this match"
            )
        return team_by_mp[match_player_id]

    impact_by_round: dict[int, dict[str, float]] = {}
    impact_rows_by_round: dict[int, set[int]] = {}
    for row in calculated_rows:
        impact_rows_by_round.setdefault(row.round_id, set()).add(row.match_player_id)
        sign = 1.0 if team_of(row.match_player_id) == team_a else -1.0
        bucket = impact_by_round.setdefault(
            row.round_id,
            {"damage": 0.0, "econ_impact": 0.0, "time_impact": 0.0,
             "swing_impact": 0.0, "impact_diff": 0.0},
        )
        bucket["damage"] += sign * row.damage
        bucket["econ_impact"] += sign * row.econ_impact
        bucket["time_impact"] += sign * row.time_impact
        bucket["swing_impact"] += sign * row.swing_impact
        bucket["impact_diff"] += sign * row.impact

    playable = [
        r for r in sorted(match.rounds, key=lambda r: r.round_number)
        if not (r.outcome or "").endswith(SURRENDER_SUFFIX)
    ]
    match_result = _match_won_by_team_a(match)

    observations: list[RoundObservation] = []
    score_a = score_b = 0
    for index, round_row in enumerate(playable):
        kills_a = kills_b = 0
        loadout_a = loadout_b = 0
        players_a = players_b = 0
        full_buy_a = full_buy_b = 0
        for stat in round_row.player_stats:
            if team_of(stat.match_player_id) == team_a:
                kills_a += stat.kills
                loadout_a += stat.loadout
                players_a += 1
                full_buy_a += 1 if stat.loadout >= FULL_BUY_THRESHOLD else 0
            else:
                kills_b += stat.kills
                loadout_b += stat.loadout
                players_b += 1
                full_buy_b += 1 if stat.loadout >= FULL_BUY_THRESHOLD else 0

        # A round with no impact rows would otherwise silently become a
        # "zero impact" observation, which is a data point that says
        # something false. Fail loudly; the loader counts and reports
        # excluded matches.
        if round_row.id not in impact_by_round:
            raise MissingImpactRows(
                f"match {match.id} round {round_row.round_number} has no impact rows"
            )
        # PARTIAL coverage is as corrupting as none: a round scored for 7 of 10
        # players has component totals and full-buy counts that are simply
        # wrong, and would enter the regression looking like a legitimate
        # observation. Every participant with a stat row must also have an
        # impact row, and vice versa.
        stat_ids = {s.match_player_id for s in round_row.player_stats}
        impact_ids = impact_rows_by_round.get(round_row.id, set())
        if stat_ids != impact_ids:
            raise MissingImpactRows(
                f"match {match.id} round {round_row.round_number}: "
                f"{len(stat_ids)} stat rows vs {len(impact_ids)} impact rows"
            )
        impact = impact_by_round[round_row.id]
        won_by_a = _winner_is_team_a(round_row.outcome)

        observations.append(
            RoundObservation(
                match_id=match.id,
                round_id=round_row.id,
                round_number=round_row.round_number,
                damage=impact["damage"],
                econ_impact=impact["econ_impact"],
                time_impact=impact["time_impact"],
                swing_impact=impact["swing_impact"],
                impact_diff=impact["impact_diff"],
                kill_diff=kills_a - kills_b,
                score_diff_before=score_a - score_b,
                attacking_is_team_a=attacking_team_for_round(round_row.round_number) == Team.TEAM_1,
                # TEAM-AVERAGE, not sum: a sum silently encodes how many
                # player-stat rows a round happens to have, so a round
                # missing a player would read as a poorer economy.
                loadout_diff=(loadout_a / players_a if players_a else 0.0)
                - (loadout_b / players_b if players_b else 0.0),
                full_buy_count_diff=full_buy_a - full_buy_b,
                round_won_by_team_a=won_by_a,
                match_won_by_team_a=match_result,
                is_terminal=index == len(playable) - 1,
            )
        )

        if won_by_a is True:
            score_a += 1
        elif won_by_a is False:
            score_b += 1

    return observations


FIRST_HALF_ROUNDS = 12
SECOND_HALF_END = 24

FEATURE_COMPONENTS = ["damage", "econ_impact", "time_impact", "swing_impact"]
BASELINE_DAMAGE = ["damage"]
BASELINE_KILL_DIFF = ["kill_diff"]
# The round's own result is a control in its own right. It is deliberately
# NOT merged into CONTROLS_CONTEXT: the control ladder's whole point is to
# measure what the components add ON TOP of knowing who won the round.
CONTROLS_RESULT = ["round_result"]
CONTROLS_CONTEXT = ["score_diff_before", "attacking_is_team_a", "loadout_diff", "full_buy_count_diff"]

# Which nuisance controls belong with which target. DERIVED from the config
# rather than passed in, because the right answer differs per target and a
# caller passing the wrong set produces a plausible-looking but meaningless
# weighting.
#
#   T2  -> result + context. The whole claim is "the components add something
#          beyond knowing who won the round and what the teams could afford
#          next", which is exactly the control ladder's step 3 -> 4. Fitting
#          the weights without round_result would report weights from a
#          different model than the ladder validates.
#   WPA -> context only. round_result IS the WPA label; controlling for the
#          label would be circular.
#   T1  -> none. Its rows are whole-match aggregates, where a summed
#          per-round result control is just the halftime score, and
#          "does first-half Impact predict the match" is the question as
#          asked. Stated explicitly rather than defaulted.
TARGET_CONTROLS = {
    "T1": [],
    "T2": CONTROLS_RESULT + CONTROLS_CONTEXT,
    "WPA": CONTROLS_CONTEXT,
}


def controls_for(config) -> list[str]:
    if config.name not in TARGET_CONTROLS:
        raise ValueError(f"no control set declared for target {config.name!r}")
    return list(TARGET_CONTROLS[config.name])


def _feature_value(obs: RoundObservation, name: str) -> float:
    if name == "round_result":
        return 0.0 if obs.round_won_by_team_a is None else (1.0 if obs.round_won_by_team_a else -1.0)
    if name == "attacking_is_team_a":
        return 1.0 if obs.attacking_is_team_a else 0.0
    return float(getattr(obs, name))


def _row(obs: RoundObservation, feature_names: list[str]) -> list[float]:
    return [_feature_value(obs, name) for name in feature_names]


def _half_of(round_number: int) -> int:
    """1 = first half, 2 = second half, 3 = overtime. Mirrors the boundary
    impact.py:309 already uses -- the economy resets at halftime, so a
    forward window must never cross it."""
    if round_number <= FIRST_HALF_ROUNDS:
        return 1
    if round_number <= SECOND_HALF_END:
        return 2
    return 3


def assign_folds(match_ids, n_folds: int = 5, seed: int = 0) -> dict[int, int]:
    """match_id -> fold index. Folds are assigned by MATCH, never by row:
    two rounds of the same match are not independent, so splitting them
    across folds would leak."""
    unique = sorted(set(int(m) for m in match_ids))
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(unique))
    return {unique[int(pos)]: int(i % n_folds) for i, pos in enumerate(order)}


def group_by_match(observations) -> dict[int, list]:
    grouped: dict[int, list] = {}
    for obs in observations:
        grouped.setdefault(obs.match_id, []).append(obs)
    return grouped
