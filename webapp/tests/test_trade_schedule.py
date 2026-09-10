"""The trade-cost schedule declared by the project owner on 2026-09-10.

    0-1s   5%      3-4s  35%
    1-2s  10%      4-5s  50%
    2-3s  17%      5-6s  75%
    6s+   not a trade at all

Two properties this pins that the previous `trade_time / 10` did not:
  - a trade is NEVER free; 5% is the floor, because you did still die
  - the window closes at 6s, not 10s, and it closes for the DISPLAYED trade
    count too, not only for the scoring discount
"""
import pytest

from app.models.match import Team
from app.scoring.impact import (
    TRADE_COST_SCHEDULE,
    TRADE_WINDOW_SECONDS,
    _traded_factor,
)

# killer 1 is on TEAM_1; avenger 6 is on TEAM_2, the victim's own side.
TEAM_OF = {1: Team.TEAM_1, 2: Team.TEAM_1, 6: Team.TEAM_2, 7: Team.TEAM_2}


def _kill(killer, victim, t):
    return {"killer_match_player_id": killer, "death_match_player_id": victim,
            "event_time_seconds": float(t)}


def _factor_at(trade_time):
    """Player 1 kills player 6 at t=10; player 7 avenges at t=10+trade_time."""
    death = _kill(1, 6, 10.0)
    kills = [death, _kill(7, 1, 10.0 + trade_time)]
    return _traded_factor(kills, death, False, team_of=TEAM_OF)


@pytest.mark.parametrize("trade_time,expected", [
    (0.0, 0.05), (0.5, 0.05), (0.99, 0.05),
    (1.0, 0.10), (1.5, 0.10), (1.99, 0.10),
    (2.0, 0.17), (2.5, 0.17),
    (3.0, 0.35), (3.5, 0.35),
    (4.0, 0.50), (4.5, 0.50),
    (5.0, 0.75), (5.99, 0.75),
])
def test_each_declared_bucket_charges_its_declared_cost(trade_time, expected):
    assert _factor_at(trade_time) == expected


def test_an_instant_trade_is_not_free():
    """The whole point of the 5% floor: `trade_time / 10` charged an instant
    trade exactly 0.0, and you did still die."""
    assert _factor_at(0.0) == 0.05


@pytest.mark.parametrize("trade_time", [6.0, 6.5, 8.0, 9.9, 12.0])
def test_beyond_six_seconds_is_not_a_trade(trade_time):
    """Charged in full -- the same value an untraded death gets."""
    assert _factor_at(trade_time) == 1


def test_the_schedule_is_monotone_and_bounded():
    previous = 0.0
    for _, cost in TRADE_COST_SCHEDULE:
        assert previous < cost < 1.0
        previous = cost
    assert TRADE_COST_SCHEDULE[0][1] == 0.05
    assert TRADE_COST_SCHEDULE[-1][0] == TRADE_WINDOW_SECONDS


def test_an_untraded_death_is_still_charged_in_full():
    death = _kill(1, 6, 10.0)
    assert _traded_factor([death], death, False, team_of=TEAM_OF) == 1


def test_a_self_kill_is_unaffected_by_the_schedule():
    """self_kill short-circuits before any of this."""
    suicide = _kill(1, 1, 10.0)
    assert _traded_factor([suicide], suicide, True, team_of=TEAM_OF) == 1
