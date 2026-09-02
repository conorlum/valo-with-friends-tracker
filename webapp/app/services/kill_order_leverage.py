"""Per-kill decomposition of the kill-order graph's contribution to Impact.

Impact is exactly linear in the 26 kill-order parameters before rounding
(verified in the spec: doubling every edge weight doubles econ/time/swing to
a median ratio of exactly 2.000, and leaves damage at 1.000). So a round's
Impact differential can be written

    ImpactDiff(r) = damage_diff(r) + SUM_k b_k * x_r[k]

and every candidate graph is scored on the SAME design matrix, differing
only in b. This module produces x_r.

The state walk here mirrors app/scoring/impact.py:503-580 exactly, INCLUDING
its resurrection rule, which differs from app/services/state_replay.py's
(that engine excludes ambiguous-lifecycle rounds; impact.py keeps them and
declines to decrement). The divergence is deliberate: this stage refits the
parameters of the shipped scorer, so extracting through a different engine
would refit a graph for a metric that is not the one that ships. The
reconstruction gate holds the two in sync.
"""

from dataclasses import dataclass, replace

import numpy as np

from app.models.match import Team
from app.scoring.impact import (
    _KILL_ORDER_GRAPH,
    _categorize_econ,
    _check_for_resurrection,
    _econ_swing_risk_factor,
    _time_factor,
    _traded_factor,
)

# own-major so PARAMS[:5] is the own=1 row. The 26th is the fallback
# _kill_order_bonus returns for transitions the graph does not contain --
# 543 of 178,242 kill events, in 497 of 23,955 rounds, measured.
PARAMS: list[str] = [f"{own}v{opp}" for own in range(1, 6) for opp in range(1, 6)] + ["fallback"]
PARAM_INDEX: dict[str, int] = {name: i for i, name in enumerate(PARAMS)}
COMPONENTS: tuple[str, str, str] = ("econ", "time", "swing")
FALLBACK_INDEX = PARAM_INDEX["fallback"]
FALLBACK_WEIGHT = 100.0

# Self-kill death-side econ factors, from impact.py:547-555. Keyed by the
# victim's econ tier code.
_SELF_KILL_DEATH_ECON = {4: 0.9, 5: 0.85, 6: 0.75}
_SELF_KILL_DEATH_ECON_DEFAULT = 0.15


def shipped_graph() -> np.ndarray:
    """The live _KILL_ORDER_GRAPH as a 26-vector in PARAMS order.

    The DiGraph's 50 edges are exactly 25 killer-perspective parameters
    duplicated by side. That symmetry is a structural invariant of the
    metric -- without it Impact would depend on which team you happen to be
    -- so it is ASSERTED here rather than assumed.
    """
    graph = np.full(len(PARAMS), np.nan)
    graph[FALLBACK_INDEX] = FALLBACK_WEIGHT
    for source, target, data in _KILL_ORDER_GRAPH.edges(data=True):
        before_a, before_b = (int(v) for v in source.split("v"))
        after_a, _ = (int(v) for v in target.split("v"))
        # A decrement of the first index is a TEAM_1 kill, whose killer has
        # `before_b` alive against `before_a`; otherwise it is TEAM_2's.
        own, opp = (before_b, before_a) if after_a == before_a - 1 else (before_a, before_b)
        index = PARAM_INDEX[f"{own}v{opp}"]
        weight = float(data["weight"])
        if not np.isnan(graph[index]) and graph[index] != weight:
            raise ValueError(
                f"kill-order graph is not side-symmetric at {own}v{opp}: "
                f"{graph[index]} != {weight}"
            )
        graph[index] = weight
    if np.isnan(graph).any():
        missing = [PARAMS[i] for i in np.flatnonzero(np.isnan(graph))]
        raise ValueError(f"kill-order graph has no weight for {missing}")
    return graph


@dataclass(frozen=True)
class KillTerm:
    """One kill's contribution, with the graph weight factored OUT.

    `kill`, `death` and `death_untraded` are the three component factors
    (econ, time, swing). `death` is what impact.py actually scores -- the
    traded factor already applied. `death_untraded` is the same before that
    discount.

    Both are stored rather than one being derived, for two reasons. The
    player-level read reports the trade discount as a subtraction
    (`death_untraded - death`), and _traded_factor can legitimately return
    0.0 for a kill traded back instantly, which would make division by
    `traded` undefined.
    """

    round_number: int
    round_id: int
    param_index: int
    tracked: bool
    sign: float
    killer_match_player_id: int
    victim_match_player_id: int
    kill: tuple[float, float, float]
    death: tuple[float, float, float]
    death_untraded: tuple[float, float, float]
    traded: float
    # Alive counts AFTER this kill, straight from the walk. Reconstructing
    # them later by counting victims is wrong: impact.py deliberately does
    # not decrement on events _check_for_resurrection flags, so a
    # re-referenced player would be subtracted twice and the terminal state
    # could go negative.
    alive_team1_after: int = 5
    alive_team2_after: int = 5


def kill_terms_for_match(
    rounds_by_number,
    round_outcomes,
    round_player_stats,
    match_players,
    round_kills,
) -> dict[int, list[KillTerm]]:
    """Decompose every kill of a match. Inputs are the same structures
    build_impact_rows_for_match builds internally (impact.py:404-437).

    EX-ANTE ONLY: the swing factor comes from _econ_swing_risk_factor and
    the realized term is never consulted, because it reads round N+1's
    loadouts and any forward-looking model trained on it would leak.
    """
    out: dict[int, list[KillTerm]] = {}

    for round_number, kills in round_kills.items():
        round_row = rounds_by_number[round_number]
        stats = round_player_stats[round_number]

        swing_by_team = {
            Team.TEAM_1: _econ_swing_risk_factor(
                round_outcomes, round_player_stats, match_players,
                round_number, Team.TEAM_1, round_row,
            ),
            Team.TEAM_2: _econ_swing_risk_factor(
                round_outcomes, round_player_stats, match_players,
                round_number, Team.TEAM_2, round_row,
            ),
        }

        # Mirrors impact.py's confusing but load-bearing naming: team1_index
        # tracks TEAM_2's alive count and vice versa, because each decrements
        # when the OTHER team lands a kill.
        team1_index = 5
        team2_index = 5
        terms: list[KillTerm] = []

        for position, event in enumerate(kills):
            killer_id = event["killer_match_player_id"]
            victim_id = event["death_match_player_id"]
            self_kill = killer_id == victim_id
            killer_team = match_players[killer_id].team

            before = f"{team1_index}v{team2_index}"
            after_a, after_b = team1_index, team2_index
            if (killer_team == Team.TEAM_1) != self_kill:
                after_a -= 1
            else:
                after_b -= 1
            tracked = _KILL_ORDER_GRAPH.has_edge(before, f"{after_a}v{after_b}")

            if killer_team == Team.TEAM_1:
                own, opp = team2_index, team1_index
            else:
                own, opp = team1_index, team2_index
            param_index = PARAM_INDEX[f"{own}v{opp}"] if tracked else FALLBACK_INDEX

            # Both halves carry the killer's sign: the victim's death_impact
            # is subtracted from the OTHER team, so it raises the same
            # differential the kill does. A self-kill reverses it.
            sign = 1.0 if killer_team == Team.TEAM_1 else -1.0
            if self_kill:
                sign = -sign

            killer_tier = _categorize_econ(stats[killer_id]["loadout"])
            victim_tier = _categorize_econ(stats[victim_id]["loadout"])
            swing = swing_by_team[Team.TEAM_2 if killer_team == Team.TEAM_1 else Team.TEAM_1]
            traded = _traded_factor(kills, event, self_kill)

            if self_kill:
                kill_half = (0.0, 0.0, 0.0)
                death_econ = _SELF_KILL_DEATH_ECON.get(victim_tier, _SELF_KILL_DEATH_ECON_DEFAULT)
            else:
                kill_half = (
                    killer_tier / victim_tier,
                    _time_factor(round_row, event["event_time_seconds"]),
                    swing,
                )
                death_econ = killer_tier / victim_tier

            death_untraded = (
                death_econ,
                _time_factor(round_row, event["event_time_seconds"], for_death=True),
                swing,
            )
            death_half = tuple(traded * value for value in death_untraded)

            terms.append(
                KillTerm(
                    round_number=round_number,
                    round_id=round_row.id,
                    param_index=param_index,
                    tracked=tracked,
                    sign=sign,
                    killer_match_player_id=killer_id,
                    victim_match_player_id=victim_id,
                    kill=kill_half,
                    death=death_half,
                    death_untraded=death_untraded,
                    traded=traded,
                )
            )

            if not _check_for_resurrection(position, kills):
                if (killer_team == Team.TEAM_1) != self_kill:
                    team1_index -= 1
                else:
                    team2_index -= 1
            # team1_index tracks TEAM_2's alive count and vice versa.
            terms[-1] = replace(
                terms[-1], alive_team1_after=team2_index, alive_team2_after=team1_index
            )

        out[round_number] = terms

    return out
