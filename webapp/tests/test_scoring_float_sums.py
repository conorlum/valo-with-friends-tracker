"""Scores must not depend on which Python adds the floats up.

Python 3.12 changed the built-in sum() to use compensated summation for floats.
The rc3 chain caught what that does to this scorer: the same corpus, read
through an identical source fingerprint, scored differently on 3.11 and 3.13 in
18 of 659,500 player-rounds. One trade kill avenged three teammates with timed
shares [0.3, 0.36, 0.42]; sum() gave 1.0799999999999998 on 3.11 and 1.08 on
3.13, the traded player's credit landed either side of 59.5, and rounding turned
that into 60 on one interpreter and 59 on the other.

math.fsum is exactly rounded on every interpreter, so the scorer uses it wherever
it sums floats. These tests pin both the case that was observed and the budget
arithmetic where the same exposure exists. They are only meaningful as a proof
when run under an interpreter whose sum() is NOT compensated (3.11): under 3.12+
the old code already happened to agree with fsum.
"""

import math
import random

from app.models.match import Team
from app.scoring import econ_buy_disruption as bd
from app.scoring.impact import _trade_credits_for_round

# One enemy (11) kills three of team 1's players, then team 1's player 4 kills
# him. The gaps to that trade kill fall in the 0.30, 0.36 and 0.42 steps of the
# trade credit schedule -- the exact shares observed in match 89, round 1776.
TEAM_OF = {1: Team.TEAM_1, 2: Team.TEAM_1, 3: Team.TEAM_1, 4: Team.TEAM_1, 11: Team.TEAM_2}
TRADE_BONUS_X_TIME = 170


def _kill(killer, victim, seconds, bonus=0.0):
    return {"killer_match_player_id": killer, "death_match_player_id": victim,
            "event_time_seconds": seconds, "kill_order_bonus_x_time": bonus}


ROUND = [
    _kill(11, 1, 10.0),                         # 5.5 s before the trade: share 0.30
    _kill(11, 2, 11.0),                         # 4.5 s: share 0.36
    _kill(11, 3, 12.0),                         # 3.5 s: share 0.42
    _kill(4, 11, 15.5, bonus=TRADE_BONUS_X_TIME),
]


def test_the_observed_boundary_case_scores_the_same_on_every_interpreter():
    credits = _trade_credits_for_round(ROUND, TEAM_OF)
    exact_scale = 0.42 / math.fsum([0.3, 0.36, 0.42])
    assert credits[2] == 0.36 * exact_scale * TRADE_BONUS_X_TIME
    # B = 2.5 puts this credit on the rounding boundary; exact summation lands it
    # at 59 everywhere, where 3.11's plain sum() used to land it at 60.
    assert round(2.5 * credits[2]) == 59


def test_the_shares_still_add_up_to_the_fastest_ones():
    credits = _trade_credits_for_round(ROUND, TEAM_OF)
    total_share = math.fsum(credits[v] for v in (1, 2, 3)) / TRADE_BONUS_X_TIME
    assert math.isclose(total_share, 0.42, rel_tol=1e-12)


def _floats(rng, count, low, high):
    return [round(rng.uniform(low, high), 3) for _ in range(count)]


def test_team_budget_sums_its_floats_exactly():
    """Seeded rather than hand-picked: under a non-compensated sum() a fair share
    of these draws disagree with fsum, so the test fails on the old code there."""
    rng = random.Random(20260916)
    for _ in range(500):
        targets = _floats(rng, bd.ROSTER_SIZE, 0.0, 9000.0)
        paid = _floats(rng, bd.ROSTER_SIZE, 0.0, 9000.0)
        bank = _floats(rng, bd.ROSTER_SIZE, 0.0, 9000.0)
        losses = _floats(rng, bd.ROSTER_SIZE, 0.0, 9000.0)
        budget = bd.team_budget(targets, paid, bank, losses)
        assert budget.target == math.fsum(targets)
        assert budget.wealth == math.fsum(paid) + math.fsum(bank)
        assert budget.lost == math.fsum(losses)
        assert budget.funding == math.fsum(min(p, t) for p, t in zip(paid, targets)) + math.fsum(bank)
        assert budget.observed_gap == math.fsum(max(0.0, t - p) for t, p in zip(targets, paid))
