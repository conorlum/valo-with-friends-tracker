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


from app.services.kill_order_refit import (
    conditioning_report,
    monotonicity_violations,
    per_parameter_report,
    stability_report,
)


def test_the_shipped_graph_has_no_monotonicity_violations():
    """Measured in the spec: zero, across all comparable pairs. The brief's
    suggested counter-example (4v4=170 against 2v2=200) is the diagonal
    rising as the round narrows, not a violation."""
    assert monotonicity_violations(shipped_graph()) == []


def test_a_deliberately_broken_curve_is_flagged():
    graph = shipped_graph().copy()
    graph[PARAM_INDEX["3v3"]] = 10.0        # even state now worth less than 3v1
    violations = monotonicity_violations(graph)
    assert any("3v3" in v for v in violations)


def test_conditioning_reports_rank_and_vif():
    rng = np.random.default_rng(4)
    leverage = rng.normal(size=(2000, len(PARAMS)))
    leverage[:, 1] = leverage[:, 0] + rng.normal(scale=0.01, size=2000)  # near-duplicate
    report = conditioning_report(leverage)
    assert report["condition_number"] > 50
    assert report["effective_rank"] < len(PARAMS)
    assert max(report["vif"]) > 20


def rng_noise(fold, scale=6.0):
    return np.random.default_rng(100 + fold).normal(scale=scale, size=len(PARAMS))


def fake_result(graphs_by_fold):
    from app.services.kill_order_refit import CandidateResult, FoldFit

    result = CandidateResult(name="test")
    for fold, graph in graphs_by_fold.items():
        result.per_fold[fold] = FoldFit(
            fold=fold, l2=1.0, train_match_ids=(), test_match_ids=(),
            swing_table=None, exposure=np.ones(len(PARAMS)),
            calibration=np.zeros(2), graph=graph, d=1.0,
            deployable=True, reasons=(),
        )
    return result


def test_stability_calls_a_candidate_that_barely_moves_unstable():
    """The pathological rule this replaces would have called this STABLE,
    because its fold spread is small in absolute terms."""
    shipped = shipped_graph()
    result = fake_result({f: shipped + rng_noise(f) for f in range(5)})
    report = stability_report(result, shipped, exposure=np.ones(len(PARAMS)), draws=40)
    assert report["ratio"] > 1
    assert report["stable"] is False


def test_stability_calls_a_consistent_large_move_stable():
    shipped = shipped_graph()
    moved = shipped * 1.4
    result = fake_result({f: moved + rng_noise(f, scale=0.5) for f in range(5)})
    report = stability_report(result, shipped, exposure=np.ones(len(PARAMS)), draws=40)
    assert report["ratio"] < 1
    assert report["stable"] is True
    assert report["ratio_ci"][1] < 1


def test_per_parameter_report_carries_exposure_and_never_a_verdict():
    """Per-parameter numbers are diagnostics. If a 'stable' or
    'indeterminate' key appears here, the rejected rule has come back."""
    rng = np.random.default_rng(6)
    leverage = rng.normal(size=(500, len(PARAMS)))
    report = per_parameter_report(leverage, exposure=np.abs(leverage).sum(axis=0))
    assert set(report) == set(PARAMS)
    entry = report["3v3"]
    assert {"exposure", "rounds_touched", "vif"} <= set(entry)
    assert "stable" not in entry and "indeterminate" not in entry


from app.services.kill_order_refit import stage_c0_report


def test_stage_c0_regresses_the_shipped_graph_on_the_swing_curve():
    """hand ~ alpha + beta * dP. The spec measured R^2 = 0.9704 on real
    data; here the synthetic curve is exactly affine, so R^2 must be ~1."""
    dp = np.linspace(0.02, 0.45, len(PARAMS))
    graph = 50.0 + 478.0 * dp
    report = stage_c0_report.regress_on_swing(graph, dp, exposure=np.ones(len(PARAMS)))
    assert report["r_squared"] > 0.999
    assert report["intercept"] == pytest.approx(50.0, rel=1e-6)
    assert report["slope"] == pytest.approx(478.0, rel=1e-6)
    assert max(abs(r) for r in report["residuals"].values()) < 1e-6


def test_stage_c0_reports_sign_flips_and_correlation_between_two_graphs():
    rng = np.random.default_rng(31)
    leverage = rng.normal(size=(800, len(PARAMS)))
    damage = rng.normal(size=800) * 20
    a = shipped_graph()
    b = a * 1.02
    report = stage_c0_report.compare_graphs(leverage, damage, a, b)
    assert report["pearson"] > 0.99
    assert report["sign_flip_rate"] < 0.05
    assert report["sd_difference"] < report["sd_reference"]


from app.services.kill_order_refit import player_level_report


def fake_player_rows(n_players=10, n_rounds=20, seed=41):
    from app.services.kill_order_leverage import PlayerLeverageRow

    rng = np.random.default_rng(seed)
    rows, outcomes = [], {}
    for match in range(6):
        outcomes[match] = bool(rng.uniform() < 0.5)
        for rnd in range(n_rounds):
            for player in range(n_players):
                kill = np.zeros((len(PARAMS), len(COMPONENTS)))
                death = np.zeros_like(kill)
                untraded = np.zeros_like(kill)
                kill[PARAM_INDEX["3v3"]] = abs(rng.normal(size=len(COMPONENTS)))
                untraded[PARAM_INDEX["3v3"]] = abs(rng.normal(size=len(COMPONENTS)))
                death[PARAM_INDEX["3v3"]] = untraded[PARAM_INDEX["3v3"]] * 0.7
                rows.append(PlayerLeverageRow(
                    match_id=match, round_id=match * 100 + rnd, round_number=rnd + 1,
                    match_player_id=match * 10 + player,
                    # Canonical player id, stable ACROSS matches (unlike
                    # match_player_id, a per-match surrogate) -- the same
                    # `player` index plays in all 6 fake matches here, which
                    # is what makes them a candidate for within-player
                    # eligibility at all.
                    player_id=player,
                    team_is_a=player < 5,
                    damage=rng.normal() * 20, kill=kill, death=death,
                    death_untraded=untraded,
                ))
    return rows, outcomes


def test_kill_and_death_impact_are_reported_separately():
    rows, outcomes = fake_player_rows()
    report = player_level_report(rows, outcomes, shipped_graph(), draws=20)
    assert "kill_impact" in report["per_player"]["summary"]
    assert "death_impact" in report["per_player"]["summary"]
    assert report["per_player"]["summary"]["death_impact"]["mean"] > 0


def test_the_trade_discount_is_reported_per_player():
    """The decision this task exists to honour: death cost as scored,
    against death cost with no trade credit."""
    rows, outcomes = fake_player_rows()
    report = player_level_report(rows, outcomes, shipped_graph(), draws=20)
    trades = report["trades"]
    assert trades["death_impact_as_scored"] < trades["death_impact_without_trade_credit"]
    assert trades["discount"] > 0
    assert np.isclose(
        trades["discount"],
        trades["death_impact_without_trade_credit"] - trades["death_impact_as_scored"],
    )


def test_tercile_lift_is_reported_for_each_half_as_well_as_pooled():
    rows, outcomes = fake_player_rows()
    report = player_level_report(rows, outcomes, shipped_graph(), draws=20)
    for key in ("impact", "kill_impact", "death_impact"):
        # Named within_player_tercile_lift, not the generic "tercile_lift":
        # the whole point (spec, repeatedly) is that terciles are computed
        # WITHIN each sufficiently-observed player then pooled, never
        # globally -- a name that dropped "within_player" would invite
        # exactly the wrong (global-tercile) implementation later.
        assert "within_player_tercile_lift" in report["per_player"][key]
        assert "ci" in report["per_player"][key]


def test_a_graph_change_moves_the_player_level_read():
    """If it did not, the player-level block would be decorative and the
    kill/death decision would have nowhere to land."""
    rows, outcomes = fake_player_rows()
    flat = np.full(len(PARAMS), 136.6)
    a = player_level_report(rows, outcomes, shipped_graph(), draws=20)
    b = player_level_report(rows, outcomes, flat, draws=20)
    assert a["per_player"]["summary"]["death_impact"]["mean"] != pytest.approx(
        b["per_player"]["summary"]["death_impact"]["mean"], rel=1e-6
    )


from app.services.kill_order_refit import (
    PRIMARY_COMPARISONS,
    RunIdentity,
    matrix_is_comparable,
    paired_delta,
    verdict_report,
)


def paired_fixture(effect=0.0, n=600, seed=71):
    from app.services.kill_order_refit import CandidateResult

    rng = np.random.default_rng(seed)
    y = (rng.uniform(size=n) < 0.5).astype(float)
    match_ids = np.repeat(np.arange(n // 20), 20)[:n]
    base = np.clip(rng.uniform(0.3, 0.7, size=n), 0.01, 0.99)

    def make(name, shift):
        result = CandidateResult(name=name)
        result.oof_probabilities = np.clip(base + shift * (y - 0.5), 0.01, 0.99)
        result.oof_y, result.oof_weights, result.oof_match_ids = y, np.ones(n), match_ids
        return result

    return make("a", effect), make("b", 0.0)


def verdict_fixture():
    return {
        "primaries": {
            "P1": {"delta": -0.002, "ci": [-0.004, -0.0005]},
            "P2": {"delta": -0.001, "ci": [-0.003, 0.001]},
            "P3": {"delta": -0.0015, "ci": [-0.003, -0.0002]},
            "P4": {"delta": -0.001, "ci": [-0.003, 0.001]},
        },
        "deployable": {"swing_basis": True, "pooled": True},
        "practically_equivalent": False,
        "targets_agree": False,
        "max_component_correlation": 0.81,
        "econ_negative_every_fold": True,
        "beats_kill_diff_t1": True,
        "stability": {"swing_basis": {"stable": True}, "pooled": {"stable": True}},
    }


def test_the_primaries_are_declared_with_their_intervals():
    names = {p["name"]: p for p in PRIMARY_COMPARISONS}
    assert set(names) == {"P1", "P2", "P3", "P4"}
    assert names["P1"]["alpha"] == pytest.approx(0.025)
    assert names["P2"]["alpha"] == pytest.approx(0.025)
    assert names["P3"]["alpha"] == pytest.approx(0.05)
    assert names["P4"]["declares"] is None
    assert all(p["target"] == "T2" for p in PRIMARY_COMPARISONS)


def test_a_co_primary_uses_the_tighter_interval():
    """97.5% must be strictly harder to clear than 95%, or the multiplicity
    adjustment is decorative."""
    a, b = paired_fixture(effect=0.004)
    wide = paired_delta(a, b, alpha=0.05)
    tight = paired_delta(a, b, alpha=0.025)
    assert tight["ci"][0] <= wide["ci"][0]
    assert tight["ci"][1] >= wide["ci"][1]


def test_verdicts_are_reported_separately_and_never_merged():
    report = verdict_report(**verdict_fixture())
    assert set(report["verdicts"]) == {"A1", "A2", "B", "C"}
    for verdict in report["verdicts"].values():
        assert "helped" in verdict and "items" in verdict
    assert "overall" not in report


def test_a_t1_null_does_not_fail_the_t2_verdict():
    """The bug this split exists to fix: the primaries declare on T2, while
    the kill_diff bar is a T1 comparison."""
    fixture = verdict_fixture()
    fixture["beats_kill_diff_t1"] = False
    report = verdict_report(**fixture)
    assert report["verdicts"]["A2"]["helped"] is False
    assert report["verdicts"]["A1"]["helped"] is True


def test_a_non_deployable_candidate_cannot_clear_the_success_bar():
    fixture = verdict_fixture()
    fixture["deployable"] = {"swing_basis": False, "pooled": True}
    report = verdict_report(**fixture)
    assert "not deployable" in " ".join(report["verdicts"]["A1"]["notes"]).lower()


def test_the_analysis_plan_is_labelled_honestly():
    report = verdict_report(**verdict_fixture())
    assert "predeclared analysis plan" in report["note"].lower()
    assert "pre-registration" in report["note"].lower()


def test_the_matrix_refuses_mixed_runs_and_says_which_identity_differed():
    a = RunIdentity(dataset_fingerprint="1151:abc", fold_mapping_hash="deadbeef",
                    calculation_version="1/1")
    assert matrix_is_comparable(a, a) == (True, [])

    for field, value in (("dataset_fingerprint", "1150:abc"),
                         ("fold_mapping_hash", "cafe"),
                         ("calculation_version", "2/1")):
        other = RunIdentity(**{**a.__dict__, field: value})
        ok, reasons = matrix_is_comparable(a, other)
        assert not ok
        assert any(field in r for r in reasons)


def test_the_report_sections_are_ordered_with_stage_c0_first():
    from app.services.kill_order_refit import REPORT_SECTIONS

    # identity is provenance, not a finding; stage_c0 is the first CONTENT
    # section and must precede every fitted number.
    assert REPORT_SECTIONS[0] == "identity"
    assert REPORT_SECTIONS[1] == "stage_c0"
    assert REPORT_SECTIONS[-1] == "verdicts"
    assert REPORT_SECTIONS.index("stage_c0") < REPORT_SECTIONS.index("family_a")
    assert REPORT_SECTIONS.index("control_ladder") < REPORT_SECTIONS.index("verdicts")
    assert REPORT_SECTIONS.index("player_level") < REPORT_SECTIONS.index("verdicts")


from app.services.kill_order_curves import FAMILY_B


def test_family_b_runs_through_the_same_orchestrator():
    observations = synthetic_observations(matches=60)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=list(FAMILY_B), l2_grid=[1.0], n_folds=5,
                            family="B")
    assert set(results) == set(FAMILY_B)
    for result in results.values():
        assert result.oof_scores is not None
        for fitted in result.per_fold.values():
            assert set(fitted.train_match_ids).isdisjoint(fitted.test_match_ids)


def test_family_b_candidates_are_scored_on_the_same_rows_as_family_a():
    """P3 compares a Family B rung against another Family B rung, but the
    matrix places both families side by side -- so their row sets must
    match or every cross-family number is meaningless."""
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    a = run_nested_cv(leverage, observations, PRIMARY_T2, candidates=["free"],
                      l2_grid=[1.0], n_folds=5, family="A")
    b = run_nested_cv(leverage, observations, PRIMARY_T2, candidates=["component_tilt"],
                      l2_grid=[1.0], n_folds=5, family="B")
    assert np.array_equal(a["free"].oof_row_ids, b["component_tilt"].oof_row_ids)


def test_p3_can_actually_be_produced():
    """The regression test for the blocking defect: verdict_report indexes
    primaries['P3'], and before this task nothing produced it."""
    observations = synthetic_observations(matches=60)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["stage_a_exact", "component_tilt"],
                            l2_grid=[1.0], n_folds=5, family="B")
    delta = paired_delta(results["component_tilt"], results["stage_a_exact"], alpha=0.05)
    assert np.isfinite(delta["delta"])
    assert len(delta["ci"]) == 2


def test_family_b_rungs_carry_their_effective_surfaces():
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["component_tilt_symmetric"], l2_grid=[1.0],
                            n_folds=5, family="B")
    fitted = next(iter(results["component_tilt_symmetric"].per_fold.values()))
    assert fitted.surfaces is not None
    assert set(fitted.surfaces) == {
        f"{c}_{s}" for c in COMPONENTS for s in ("kill", "death")
    }
    assert all(v.shape == (len(PARAMS),) for v in fitted.surfaces.values())


def test_an_unknown_family_is_refused():
    observations = synthetic_observations(matches=20)
    with pytest.raises(ValueError, match="family"):
        run_nested_cv(leverage_for(observations), observations, PRIMARY_T2,
                      candidates=["free"], l2_grid=[1.0], family="C")


from app.services.kill_order_refit import run_all_targets, target_agreement


def test_t1_refuses_the_high_dimensional_candidates():
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    results = run_all_targets(leverage, observations, l2_grid=[1.0], n_folds=5)
    assert set(results) == {"T1", "T2", "WPA"}
    assert "pooled" not in results["T1"]
    assert "free" not in results["T1"]
    assert "swing_basis" in results["T1"]
    assert "pooled" in results["T2"]


def test_agreement_is_measured_against_declared_thresholds():
    exposure = np.ones(len(PARAMS))
    base = shipped_graph()
    agree = target_agreement(
        {"T1": base, "T2": base * 1.02, "WPA": base * 0.99}, exposure
    )
    assert agree["agree"] is True
    assert min(agree["spearman"].values()) > 0.90

    disagree = target_agreement(
        {"T1": base, "T2": base[::-1].copy(), "WPA": base * 3.0}, exposure
    )
    assert disagree["agree"] is False
    assert disagree["thresholds"]["spearman_above"] == 0.90
    assert disagree["thresholds"]["rms_share_below"] == 0.15
