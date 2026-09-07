"""Part 1 (docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md):
app.scoring.impact._attacking_team now delegates to the one consolidated
helper, app.scoring.plant_window.attacking_team, which -- unlike the old
None-past-24 stub -- has a real answer for overtime. This must not change any
score: _econ_swing_risk_factor early-returns for round_number > 24 before it
ever consults _attacking_team (impact.py:271-272), so the migration is a
no-op through this path. That invariant is what these tests pin down.
"""

from app.models.match import Team
from app.scoring.impact import _attacking_team, _econ_swing_risk_factor
from app.scoring.plant_window import attacking_team as plant_window_attacking_team


def test_attacking_team_now_delegates_and_agrees_with_plant_window():
    for round_number in (1, 12, 13, 24, 25, 26, 30):
        assert _attacking_team(round_number) == plant_window_attacking_team(round_number)


def test_econ_swing_risk_factor_overtime_early_return_is_unaffected_by_the_migration():
    # round_number > 24 must return 1 regardless of team or round_row --
    # confirms the early return at impact.py:271-272 runs before
    # _attacking_team is ever consulted, so extending _attacking_team to
    # handle OT cannot change a score through this path.
    for team in (Team.TEAM_1, Team.TEAM_2):
        result = _econ_swing_risk_factor(
            round_outcomes={}, round_player_stats={}, match_players={},
            round_number=25, team=team, round_row=None,
        )
        assert result == 1
