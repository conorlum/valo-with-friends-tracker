"""Nested CV over leverage rows. Pure: synthetic observations and leverage,
no DB."""

import numpy as np
import pytest

from app.services.impact_eval import PRIMARY_T1, PRIMARY_T2, build_target, stable_folds
from app.services.kill_order_curves import FAMILY_A
from app.services.kill_order_leverage import COMPONENTS, PARAMS, PARAM_INDEX, shipped_graph
from app.services.kill_order_refit import align_target, run_nested_cv


def synthetic_observations(matches=40, seed=17):
    """RoundObservations with enough structure for a target to be
    non-degenerate: 24 rounds each, alternating winners with a per-match
    bias so the match outcome is predictable but not deterministic."""
    from app.services.impact_eval import RoundObservation

    rng = np.random.default_rng(seed)
    out = []
    round_id = 0
    for match_index in range(matches):
        bias = rng.uniform(0.3, 0.7)
        team_a_wins = 0
        rounds = []
        for number in range(1, 25):
            round_id += 1
            won = bool(rng.uniform() < bias)
            team_a_wins += won
            rounds.append((round_id, number, won))
        match_won = team_a_wins > 12
        for rid, number, won in rounds:
            out.append(RoundObservation(
                match_id=1000 + match_index, round_id=rid, round_number=number,
                damage=rng.normal() * 20, econ_impact=0.0, time_impact=0.0,
                swing_impact=0.0, kill_diff=0.0, acs_diff=0.0, impact_diff=0.0,
                score_diff_before=0, attacking_is_team_a=number <= 12,
                loadout_diff=0.0, full_buy_count_diff=0,
                round_won_by_team_a=won, match_won_by_team_a=match_won,
                is_terminal=number == 24,
            ))
    return out


def leverage_for(observations, seed=23):
    """One TeamLeverageRow per observation, with signal on three states."""
    from app.services.kill_order_leverage import TeamLeverageRow

    rng = np.random.default_rng(seed)
    rows = []
    for obs in observations:
        kill = np.zeros((len(PARAMS), len(COMPONENTS)))
        death = np.zeros_like(kill)
        pull = 1.0 if obs.round_won_by_team_a else -1.0
        for name in ("5v5", "4v4", "3v3"):
            kill[PARAM_INDEX[name]] = pull * abs(rng.normal(size=len(COMPONENTS)))
            death[PARAM_INDEX[name]] = pull * abs(rng.normal(size=len(COMPONENTS))) * 0.6
        rows.append(TeamLeverageRow(
            match_id=obs.match_id, round_id=obs.round_id, round_number=obs.round_number,
            damage_diff=obs.damage, kill=kill, death=death, death_untraded=death,
        ))
    return rows


def test_alignment_reproduces_the_parent_targets_y_and_weights():
    """The anti-drift gate. Stage C builds its own design matrix but must
    predict exactly the quantity the parent project's target defines."""
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    for config in (PRIMARY_T1, PRIMARY_T2):
        aligned = align_target(leverage, observations, config)
        reference = build_target(observations, config, ["damage"])
        assert len(aligned.y) == len(reference.y)
        assert np.allclose(aligned.y, reference.y)
        assert np.allclose(aligned.weights, reference.w)
        assert np.array_equal(aligned.match_ids, reference.match_ids)


def test_t1_sums_leverage_over_the_first_half():
    observations = synthetic_observations(matches=3)
    leverage = leverage_for(observations)
    aligned = align_target(leverage, observations, PRIMARY_T1)
    assert aligned.leverage.shape == (3, len(PARAMS))
    first_match = [r for r in leverage if r.match_id == observations[0].match_id
                   and r.round_number <= 12]
    expected = sum((r.kill + r.death).sum(axis=1) / len(COMPONENTS) for r in first_match)
    assert np.allclose(aligned.leverage[0], expected)


def test_t2_keeps_one_row_per_source_round():
    observations = synthetic_observations(matches=5)
    leverage = leverage_for(observations)
    aligned = align_target(leverage, observations, PRIMARY_T2)
    assert len(set(aligned.round_ids)) == len(aligned.round_ids)


def test_nested_cv_never_scores_a_match_its_model_trained_on():
    observations = synthetic_observations(matches=60)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["current_graph", "free"], l2_grid=[1.0], n_folds=5)
    for result in results.values():
        for fold, fitted in result.per_fold.items():
            assert set(fitted.train_match_ids).isdisjoint(fitted.test_match_ids)


def test_the_swing_table_and_exposure_come_from_training_matches_only():
    """A held-out match with a wildly different state distribution must not
    move the fold's swing table."""
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["swing_plugin"], l2_grid=[1.0], n_folds=5)
    tables = [f.swing_table for f in results["swing_plugin"].per_fold.values()]
    assert len({id(t) for t in tables}) == len(tables), "one table per fold, not one shared"
    assert not np.allclose(tables[0].visits, tables[1].visits)


def test_l2_is_selected_inside_the_training_fold():
    observations = synthetic_observations(matches=80)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["free"], l2_grid=[0.01, 1.0, 100.0], n_folds=5)
    chosen = [f.l2 for f in results["free"].per_fold.values()]
    assert len(chosen) == 5
    assert all(value in (0.01, 1.0, 100.0) for value in chosen)


def test_calibration_is_fitted_inside_each_outer_fold():
    """A fitted candidate's pooled scores come from five different models,
    so calibrating over the pooled scores would put a score in the
    calibration training set whose own model saw that match."""
    observations = synthetic_observations(matches=60)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["free"], l2_grid=[1.0], n_folds=5)
    result = results["free"]
    assert len(result.oof_probabilities) == len(result.oof_scores)
    assert np.all((result.oof_probabilities > 0) & (result.oof_probabilities < 1))
    assert len({f.calibration.tobytes() for f in result.per_fold.values()}) > 1


def test_every_candidate_is_scored_on_identical_rows():
    """Different candidates differ only in coefficients; if their row sets
    diverged, the paired comparisons downstream would be meaningless."""
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["current_graph", "swing_plugin", "free"],
                            l2_grid=[1.0], n_folds=5)
    reference = results["current_graph"].oof_row_ids
    for result in results.values():
        assert np.array_equal(result.oof_row_ids, reference)


from app.services.kill_order_refit import LADDER_RUNGS, control_ladder


def test_the_ladder_has_five_rungs_in_the_declared_order():
    assert LADDER_RUNGS == (
        "round_result", "plus_context", "plus_damage",
        "plus_terminal_state", "plus_leverage",
    )


def test_rung_four_adds_exactly_two_columns():
    """Pinned before the fact: a richer terminal encoding could reconstruct
    the round and make the headline null for the wrong reason."""
    observations = synthetic_observations(matches=30)
    leverage = leverage_for(observations)
    report = control_ladder(leverage, observations, PRIMARY_T2, n_folds=5)
    assert report["plus_terminal_state"]["n_features"] - report["plus_damage"]["n_features"] == 2
    assert report["plus_terminal_state"]["added_columns"] == [
        "terminal_alive_diff", "total_kills",
    ]


def test_each_rung_is_a_superset_of_the_previous():
    observations = synthetic_observations(matches=30)
    leverage = leverage_for(observations)
    report = control_ladder(leverage, observations, PRIMARY_T2, n_folds=5)
    previous: set[str] = set()
    for rung in LADDER_RUNGS:
        columns = set(report[rung]["columns"])
        assert previous <= columns, f"{rung} dropped a column the previous rung had"
        previous = columns


def test_the_headline_is_rung_four_to_five_with_a_paired_interval():
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    report = control_ladder(leverage, observations, PRIMARY_T2, n_folds=5, draws=50)
    headline = report["headline"]
    assert headline["from"] == "plus_terminal_state"
    assert headline["to"] == "plus_leverage"
    low, high = headline["delta_ci"]
    assert low <= headline["delta"] <= high
    assert "negative delta" in headline["reading"]


def test_the_three_to_four_step_is_reported_too():
    observations = synthetic_observations(matches=30)
    leverage = leverage_for(observations)
    report = control_ladder(leverage, observations, PRIMARY_T2, n_folds=5, draws=50)
    assert "delta" in report["plus_terminal_state"]
    assert report["plus_terminal_state"]["delta_from"] == "plus_damage"
