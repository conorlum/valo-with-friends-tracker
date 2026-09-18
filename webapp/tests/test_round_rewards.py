"""Actual round rewards (spec 2026-09-12 section 4.1): 3000 after a win,
1900/2400/2900 by the consecutive-loss streak WITHIN the regulation half."""
from app.scoring import round_rewards as rr

A, B = "A", "B"


def winners(*seq, start=1):
    return {start + i: w for i, w in enumerate(seq)}


def test_win_pays_3000():
    assert rr.round_reward(winners(A, A), 3, A) == 3000


def test_loss_streak_tiers():
    w = winners(A, B, B, B, B)
    assert rr.round_reward(w, 3, A) == 1900
    assert rr.round_reward(w, 4, A) == 2400
    assert rr.round_reward(w, 5, A) == 2900
    assert rr.round_reward(w, 6, A) == 2900


def test_streak_does_not_cross_halftime():
    w = {12: B, 13: B, 14: B}
    assert rr.round_reward(w, 14, A) == 1900
    assert rr.round_reward(w, 15, A) == 2400


def test_unknown_outcome_or_pistol_reset_is_none():
    assert rr.round_reward(winners(A, None), 3, A) is None
    assert rr.round_reward(winners(A, B, None), 4, A) is None
    assert rr.round_reward({12: A}, 13, A) is None
    assert rr.round_reward({24: A}, 25, A) is None


def test_constants():
    assert (rr.WIN_REWARD, rr.LOSS_REWARDS, rr.KILL_REWARD, rr.PLANT_BONUS, rr.CREDIT_CAP) == (
        3000, (1900, 2400, 2900), 200, 300, 9000)
