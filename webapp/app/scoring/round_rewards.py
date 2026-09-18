"""Actual Valorant round rewards, read retrospectively from real outcomes.

Spec: docs/superpowers/specs/2026-09-12-econ-bonus-round-denial-design.md,
section 4.1. PURE and import-free with respect to the scorer:
app.scoring.credit_events.round_bonus imports app.scoring.impact, which
imports the econ calculator, so the calculator cannot reuse that helper
without a cycle. Unlike round_bonus, the loss streak here stops at the
half's pistol round (credits and streaks reset at halftime).
"""
from typing import Hashable, Mapping

WIN_REWARD = 3000
LOSS_REWARDS = (1900, 2400, 2900)
KILL_REWARD = 200
PLANT_BONUS = 300
CREDIT_CAP = 9000


def half_start(round_number: int) -> int | None:
    """First round of the regulation half containing round_number; None in overtime."""
    if 1 <= round_number <= 12:
        return 1
    if 13 <= round_number <= 24:
        return 13
    return None


def round_reward(winners: Mapping[int, Hashable | None], round_number: int, team: Hashable) -> int | None:
    """The reward `team` receives going INTO `round_number`, from real outcomes.

    None when round_number starts a half (the pistol reset), is overtime, or
    any outcome the streak needs is unknown -- never an assumed value."""
    start = half_start(round_number)
    if start is None or round_number <= start:
        return None
    previous = round_number - 1
    if winners.get(previous) is None:
        return None
    if winners[previous] == team:
        return WIN_REWARD
    streak = 0
    r = previous
    while r >= start:
        winner = winners.get(r)
        if winner is None:
            return None
        if winner == team:
            break
        streak += 1
        r -= 1
    return LOSS_REWARDS[min(streak, len(LOSS_REWARDS)) - 1]
