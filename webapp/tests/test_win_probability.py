"""V(state) = P(team A wins the match | state).

Stage B uses this for leverage-weighted ATTRIBUTION, not independent
validation: dV is dominated by the round's own outcome, which the features
nearly determine."""

import numpy as np

from app.services.impact_eval import RoundObservation
from app.services.win_probability import (
    econ_increment_report,
    fit_value_model,
    state_after,
    state_before,
    value_of,
)


def _obs(round_number, score_diff_before, won_by_a, match_won, terminal=False, match_id=1):
    return RoundObservation(
        match_id=match_id, round_id=round_number, round_number=round_number,
        damage=0.0, econ_impact=0.0, time_impact=0.0, swing_impact=0.0,
        impact_diff=0.0, kill_diff=0.0, acs_diff=0.0,
        score_diff_before=score_diff_before, attacking_is_team_a=True,
        loadout_diff=0.0, full_buy_count_diff=0,
        round_won_by_team_a=won_by_a, match_won_by_team_a=match_won, is_terminal=terminal,
    )


def test_state_before_excludes_the_round_result():
    o = _obs(5, 2, True, True)
    assert state_before(o).score_diff == 2
    assert state_before(o).rounds_played == 4


def test_state_after_includes_the_round_result():
    assert state_after(_obs(5, 2, True, True)).score_diff == 3
    assert state_after(_obs(5, 2, False, True)).score_diff == 1


def test_state_after_unresolved_round_leaves_score_unchanged():
    assert state_after(_obs(5, 2, None, True)).score_diff == 2


def test_state_after_uses_the_next_rounds_side_at_halftime():
    """Round 12 -> 13 is a side swap. The after-state belongs to round 13."""
    after_twelve = state_after(_obs(12, 0, True, True))
    assert after_twelve.attacking_is_team_a is False
    assert state_before(_obs(12, 0, True, True)).attacking_is_team_a is True


def test_state_after_terminal_round_is_pinned_to_the_result():
    won = state_after(_obs(21, 5, True, True, terminal=True))
    lost = state_after(_obs(21, -5, False, False, terminal=True))
    assert won.is_terminal and won.terminal_result == 1.0
    assert lost.is_terminal and lost.terminal_result == 0.0


def test_value_of_returns_exactly_one_or_zero_for_terminal_states():
    observations = [_obs(10, i % 5 - 2, True, i % 2 == 0, match_id=i) for i in range(60)]
    model = fit_value_model(observations)
    assert value_of(model, state_after(_obs(21, 5, True, True, terminal=True))) == 1.0
    assert value_of(model, state_after(_obs(21, -5, False, False, terminal=True))) == 0.0


def test_value_model_carries_its_training_scaling():
    """Raw columns differ by orders of magnitude; the model must store the
    statistics it was fitted under and apply them to test states."""
    observations = [_obs(10, i % 5 - 2, True, i % 2 == 0, match_id=i) for i in range(60)]
    model = fit_value_model(observations)
    assert model.centre.shape == model.scale.shape
    assert len(model.beta) == len(model.centre) + 1
    assert np.all(model.scale > 0)


def test_econ_increment_reports_a_paired_interval():
    rng = np.random.default_rng(3)
    observations = []
    for match_id in range(120):
        o = _obs(10, int(rng.integers(-3, 4)), True, rng.random() < 0.5, match_id=match_id)
        o.loadout_diff = rng.normal() * 1000
        o.full_buy_count_diff = int(rng.integers(-2, 3))
        observations.append(o)
    report = econ_increment_report(observations, seed=0)
    lo, hi = report["delta_ci"]
    assert lo <= report["delta"] <= hi


def test_value_model_learns_that_a_lead_is_good():
    observations = []
    for match_id in range(200):
        leading = match_id % 2 == 0
        observations.append(_obs(10, 5 if leading else -5, True, leading, match_id=match_id))
    model = fit_value_model(observations)
    ahead = value_of(model, state_before(_obs(10, 5, True, True)))
    behind = value_of(model, state_before(_obs(10, -5, True, False)))
    assert ahead > behind
    assert 0.0 <= ahead <= 1.0


def test_value_model_uses_a_score_by_progress_interaction():
    """A two-round lead late must be worth more than the same lead early.
    An additive model cannot express this."""
    observations = []
    rng = np.random.default_rng(0)
    for match_id in range(400):
        rounds_played = int(rng.integers(2, 20))
        diff = int(rng.integers(-4, 5))
        # Later leads convert far more reliably.
        p = 0.5 + 0.02 * diff * rounds_played
        observations.append(
            _obs(rounds_played + 1, diff, True, rng.random() < np.clip(p, 0.02, 0.98), match_id=match_id)
        )
    model = fit_value_model(observations)
    early = value_of(model, state_before(_obs(4, 2, True, True)))
    late = value_of(model, state_before(_obs(20, 2, True, True)))
    assert late > early


def test_after_state_refuses_econ_aware_evaluation():
    """Round N+1's pre-buy economy is not extracted, so an econ-aware
    V(after) would silently reuse round N's -- flattering econ. It must
    raise instead."""
    import pytest

    observations = [_obs(10, 1, True, True, match_id=i) for i in range(40)]
    for i, o in enumerate(observations):
        o.match_won_by_team_a = i % 2 == 0
    econ_model = fit_value_model(observations, include_econ=True)
    with pytest.raises(ValueError, match="after-state"):
        value_of(econ_model, state_after(_obs(5, 1, True, True)))


def test_econ_increment_report_measures_the_delta():
    observations = []
    rng = np.random.default_rng(1)
    for match_id in range(120):
        o = _obs(10, int(rng.integers(-3, 4)), True, rng.random() < 0.5, match_id=match_id)
        o.loadout_diff = rng.normal() * 1000
        o.full_buy_count_diff = int(rng.integers(-2, 3))
        observations.append(o)
    report = econ_increment_report(observations, seed=0)
    assert "base_log_loss" in report and "with_econ_log_loss" in report
    assert "delta" in report
