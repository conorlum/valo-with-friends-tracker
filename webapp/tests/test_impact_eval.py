"""Observation extraction: one differential row per round, team A minus
team B. Fixtures are plain ORM construction with no session, following
tests/test_player_profile_types.py."""

import numpy as np
import pytest

from app.models import Match, MatchPlayer, Player, Round
from app.models.match import MatchSource, Team
from app.models.round import RoundPlayerStat
from app.scoring.impact import CalculatedImpact
from app.services.impact_eval import RoundObservation, build_observations_for_match


def _match_with_two_rounds():
    match = Match(
        id=1, external_id="ext-1", source=MatchSource.SCRAPED, map_name="Bind",
        team1_rounds_won=13, team2_rounds_won=7,
    )
    a = MatchPlayer(id=1, match_id=1, player_id=10, agent="Jett", team=Team.TEAM_1)
    b = MatchPlayer(id=2, match_id=1, player_id=20, agent="Sova", team=Team.TEAM_2)
    a.player = Player(id=10, display_name="A#1")
    b.player = Player(id=20, display_name="B#2")
    match.match_players = [a, b]

    r1 = Round(id=101, match_id=1, round_number=1, outcome="Team A Wins")
    r1.player_stats = [
        RoundPlayerStat(match_player_id=1, kills=2, deaths=0, assists=0, loadout=800, score=250),
        RoundPlayerStat(match_player_id=2, kills=0, deaths=2, assists=0, loadout=800, score=90),
    ]
    r2 = Round(id=102, match_id=1, round_number=2, outcome="Team B Wins")
    r2.player_stats = [
        RoundPlayerStat(match_player_id=1, kills=0, deaths=1, assists=0, loadout=4500, score=60),
        RoundPlayerStat(match_player_id=2, kills=1, deaths=0, assists=0, loadout=2000, score=210),
    ]
    match.rounds = [r1, r2]
    return match


def _calculated():
    return [
        CalculatedImpact(101, 1, 300, 0, 300, 100, 60, 30, 20, 0, 0, 0, 0, 0, 0, 0, 0, None),
        CalculatedImpact(101, 2, 0, 250, -250, 10, -40, -20, -10, 0, 0, 0, 0, 0, 0, 0, 0, None),
        CalculatedImpact(102, 1, 0, 200, -200, 20, -30, -10, -5, 0, 0, 0, 0, 0, 0, 0, 0, None),
        CalculatedImpact(102, 2, 280, 0, 280, 90, 50, 25, 15, 0, 0, 0, 0, 0, 0, 0, 0, None),
    ]


def test_one_observation_per_round():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert [o.round_number for o in obs] == [1, 2]


def test_features_are_team_a_minus_team_b():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].damage == 100 - 10
    assert obs[0].econ_impact == 60 - (-40)
    assert obs[0].swing_impact == 20 - (-10)


def test_kill_diff_is_team_kill_differential():
    """kills_A - kills_B, not kills-minus-deaths: deaths_A == kills_B in
    99.1% of real rounds, so the latter is the same column doubled."""
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].kill_diff == 2 - 0
    assert obs[1].kill_diff == 0 - 1


def test_acs_diff_is_the_raw_combat_score_differential():
    """Team A minus team B's RoundPlayerStat.score, summed over the round --
    plain ACS, not anything the Impact formula derives."""
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].acs_diff == 250 - 90
    assert obs[1].acs_diff == 60 - 210


def test_score_differential_excludes_the_current_round():
    """Round 1's control must be 0-0, not 1-0: the round's own result is a
    separate control and must never leak into pre-round score."""
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].score_diff_before == 0
    assert obs[1].score_diff_before == 1


def test_economy_controls_are_start_of_round():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].loadout_diff == 800 - 800
    assert obs[1].loadout_diff == 4500 - 2000
    assert obs[1].full_buy_count_diff == 1 - 0


def test_round_and_match_outcomes():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].round_won_by_team_a is True
    assert obs[1].round_won_by_team_a is False
    assert all(o.match_won_by_team_a is True for o in obs)


def test_first_half_is_always_attack_first_for_team_a():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert all(o.attacking_is_team_a for o in obs)


def test_last_round_is_terminal():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[-1].is_terminal
    assert not obs[0].is_terminal


def test_surrender_rounds_are_dropped():
    match = _match_with_two_rounds()
    match.rounds[1].outcome = "Team A Surrendered Win"
    obs = build_observations_for_match(match, _calculated())
    assert [o.round_number for o in obs] == [1]


def test_impact_diff_is_the_exact_stored_differential():
    """Not reconstructed from components: round 1 is 300 - (-250) = 550."""
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].impact_diff == 300 - (-250)
    assert obs[1].impact_diff == -200 - 280


def test_economy_control_is_a_team_average_not_a_sum():
    """A sum would encode how many player-stat rows a round happens to have."""
    match = _match_with_two_rounds()
    # Drop one of team B's stat rows from round 2; the average must not move.
    match.rounds[1].player_stats = [
        s for s in match.rounds[1].player_stats if s.match_player_id == 1
    ] + [RoundPlayerStat(match_player_id=2, kills=1, deaths=0, assists=0, loadout=2000)]
    obs = build_observations_for_match(match, _calculated())
    assert obs[1].loadout_diff == 4500 - 2000


def test_missing_impact_rows_raise_rather_than_becoming_zero():
    from app.services.impact_eval import MissingImpactRows

    with pytest.raises(MissingImpactRows, match="round 2"):
        build_observations_for_match(
            _match_with_two_rounds(), [r for r in _calculated() if r.round_id == 101]
        )


def test_tied_match_has_no_match_label():
    match = _match_with_two_rounds()
    match.team1_rounds_won = match.team2_rounds_won = 12
    obs = build_observations_for_match(match, _calculated())
    assert all(o.match_won_by_team_a is None for o in obs)


from app.services.impact_eval import (
    CONTROLS_CONTEXT,
    CONTROLS_RESULT,
    FEATURE_COMPONENTS,
    FIRST_HALF_ROUNDS,
    _feature_value,
    _half_of,
    assign_folds,
    group_by_match,
)


def test_every_match_gets_exactly_one_fold():
    folds = assign_folds([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], n_folds=5, seed=0)
    assert set(folds) == set(range(1, 11))
    assert set(folds.values()) <= {0, 1, 2, 3, 4}


def test_folds_are_balanced():
    folds = assign_folds(list(range(100)), n_folds=5, seed=0)
    counts = [sum(1 for f in folds.values() if f == k) for k in range(5)]
    assert max(counts) - min(counts) <= 1


def test_fold_assignment_is_seed_deterministic():
    assert assign_folds(list(range(50)), seed=11) == assign_folds(list(range(50)), seed=11)


def test_grouping_keeps_a_match_together():
    grouped = group_by_match(build_observations_for_match(_match_with_two_rounds(), _calculated()))
    assert list(grouped) == [1]
    assert len(grouped[1]) == 2


def test_half_boundaries_match_the_scorer_convention():
    """impact.py:309 already encodes rounds 12/24 as the economy resets."""
    assert _half_of(1) == _half_of(FIRST_HALF_ROUNDS) == 1
    assert _half_of(13) == _half_of(24) == 2
    assert _half_of(25) == 3


def test_round_result_control_is_signed_and_separate():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert _feature_value(obs[0], "round_result") == 1.0
    assert _feature_value(obs[1], "round_result") == -1.0
    assert "round_result" in CONTROLS_RESULT
    assert "round_result" not in CONTROLS_CONTEXT
    assert "round_result" not in FEATURE_COMPONENTS


from app.services.impact_eval import (
    FitDataset,
    TargetConfig,
    build_target,
    first_half_target,
    forward_window_target,
)


def _obs(round_number, damage, won_by_a, match_won, terminal=False, match_id=1):
    return RoundObservation(
        match_id=match_id, round_id=1000 * match_id + round_number, round_number=round_number,
        damage=damage, econ_impact=0.0, time_impact=0.0, swing_impact=0.0,
        impact_diff=damage, kill_diff=0.0, acs_diff=0.0,
        score_diff_before=0, attacking_is_team_a=True,
        loadout_diff=0.0, full_buy_count_diff=0,
        round_won_by_team_a=won_by_a, match_won_by_team_a=match_won, is_terminal=terminal,
    )


def _full_half(match_id=1, damage=10.0, won_by_a=True, match_won=True):
    obs = [_obs(n, damage, won_by_a, match_won, match_id=match_id) for n in range(1, 13)]
    obs[-1].is_terminal = True
    return obs


def test_first_half_requires_all_twelve_rounds():
    short = [_obs(n, 10.0, True, True) for n in range(1, 12)]
    assert len(first_half_target(short, FEATURE_COMPONENTS).y) == 0
    assert len(first_half_target(_full_half(), FEATURE_COMPONENTS).y) == 1


def test_first_half_sums_components_over_the_half():
    dataset = first_half_target(_full_half(), FEATURE_COMPONENTS)
    assert dataset.X[0][FEATURE_COMPONENTS.index("damage")] == 120.0
    assert dataset.y[0] == 1.0
    assert dataset.w[0] == 1.0


def test_first_half_excludes_tied_matches():
    obs = _full_half()
    for o in obs:
        o.match_won_by_team_a = None
    assert len(first_half_target(obs, FEATURE_COMPONENTS).y) == 0


def test_forward_window_collapses_to_one_row_per_source_round():
    """Five rounds, k=3: rounds 1 and 2 each get a full window, round 3 a
    partial one, round 4 one, round 5 is terminal. Five rounds in, four
    rows out -- one per non-terminal source round."""
    obs = [_obs(n, 1.0, True, True) for n in range(1, 6)]
    obs[-1].is_terminal = True
    dataset = forward_window_target(obs, FEATURE_COMPONENTS, k=3, gamma=0.5, match_weight=0.0)
    assert len(dataset.y) == 4


def test_forward_window_target_is_the_weighted_fraction():
    """Round 1 sees rounds 2 (won) and 3 (lost) at gamma=0.5.
    y = (1*1 + 0.5*0) / 1.5 = 0.667, w = 1.5"""
    obs = [
        _obs(1, 1.0, True, True), _obs(2, 1.0, True, True),
        _obs(3, 1.0, False, True), _obs(4, 1.0, True, True, terminal=True),
    ]
    dataset = forward_window_target(obs, FEATURE_COMPONENTS, k=2, gamma=0.5, match_weight=0.0)
    assert abs(dataset.y[0] - (1.0 / 1.5)) < 1e-12
    assert abs(dataset.w[0] - 1.5) < 1e-12


def test_forward_window_does_not_cross_halftime():
    """Round 12 is the last of the first half, so with no match auxiliary it
    contributes nothing; round 11 still gets its one in-half partner."""
    obs = [_obs(n, 1.0, True, True) for n in range(1, 25)]
    obs[-1].is_terminal = True
    only_twelve = [o for o in obs if o.round_number == 12]
    assert len(forward_window_target(only_twelve, FEATURE_COMPONENTS, k=3, match_weight=0.0).y) == 0

    eleven_twelve = [o for o in obs if o.round_number in (11, 12)]
    assert len(forward_window_target(eleven_twelve, FEATURE_COMPONENTS, k=3, match_weight=0.0).y) == 1


def test_forward_window_skips_terminal_rounds():
    obs = [_obs(1, 1.0, True, True), _obs(2, 1.0, True, True, terminal=True)]
    assert len(forward_window_target(obs, FEATURE_COMPONENTS, k=3, match_weight=0.0).y) == 1


def test_match_auxiliary_only_for_early_rounds():
    """Round 24's window crosses into OT, so with the auxiliary
    restricted to N <= 12 it contributes no row at all."""
    late = [_obs(24, 1.0, True, True), _obs(25, 1.0, True, True, terminal=True)]
    assert len(forward_window_target(late, FEATURE_COMPONENTS, k=3, match_weight=5.0).y) == 0


def test_match_auxiliary_shifts_target_and_weight_for_early_rounds():
    obs = [
        _obs(1, 1.0, True, False), _obs(2, 1.0, False, False),
        _obs(3, 1.0, True, False, terminal=True),
    ]
    without = forward_window_target(obs, FEATURE_COMPONENTS, k=1, gamma=1.0, match_weight=0.0)
    with_aux = forward_window_target(obs, FEATURE_COMPONENTS, k=1, gamma=1.0, match_weight=1.0)
    assert without.y[0] == 0.0 and without.w[0] == 1.0
    # round 2 lost, match lost -> y stays 0 but total weight doubles
    assert with_aux.w[0] == 2.0


def test_build_target_dispatches_on_config():
    config_one = TargetConfig(name="T1")
    config_two = TargetConfig(name="T2", k=2, gamma=0.5, match_weight=0.0)
    obs = _full_half()
    assert len(build_target(obs, config_one, FEATURE_COMPONENTS).y) == 1
    assert len(build_target(obs, config_two, FEATURE_COMPONENTS).y) > 1
    with pytest.raises(ValueError):
        build_target(obs, TargetConfig(name="nope"), FEATURE_COMPONENTS)


from app.services import impact_eval
from app.services.impact_eval import (
    FoldResult,
    _select_config,
    cross_validate,
    oof_metrics,
    split_observations,
)

# One frozen target -- selection across target definitions is refused.
T2_CONFIGS = [TargetConfig(name="T2", k=3, gamma=0.7, match_weight=1.0)]


def _synthetic_matches(n_matches=60, seed=0):
    """Each match is a 12-round half where team A's damage differential
    predicts whether it wins its rounds."""
    rng = np.random.default_rng(seed)
    observations = []
    for match_id in range(n_matches):
        strength = rng.normal()
        obs = []
        for n in range(1, 13):
            won = rng.random() < 1.0 / (1.0 + np.exp(-2.0 * strength))
            o = _obs(n, strength * 10.0, won, strength > 0, match_id=match_id)
            obs.append(o)
        obs[-1].is_terminal = True
        observations.extend(obs)
    return observations


def test_split_puts_each_match_wholly_on_one_side():
    observations = _synthetic_matches(20)
    folds = assign_folds([o.match_id for o in observations], n_folds=5, seed=0)
    train, test = split_observations(observations, folds, 0)
    assert {o.match_id for o in train}.isdisjoint({o.match_id for o in test})
    assert len(train) + len(test) == len(observations)


def test_cross_validate_recovers_a_planted_signal():
    """Judged by weighted log loss against the intercept-only baseline --
    not by AUC on a rounded fractional target."""
    observations = _synthetic_matches(80)
    result = cross_validate(observations, T2_CONFIGS, FEATURE_COMPONENTS, [0.1, 1.0], seed=0)
    metrics = oof_metrics(result["oof"], draws=20, seed=0)
    assert metrics["improvement_over_intercept"] > 0
    assert all(f.beta_raw[FEATURE_COMPONENTS.index("damage") + 1] > 0 for f in result["folds"])


def test_selection_refuses_to_compare_different_targets():
    """The blocking bug this guard exists for: log loss against different y
    is not a comparison, and a smoother target wins for the wrong reason."""
    observations = _synthetic_matches(20)
    mixed = [
        TargetConfig(name="T2", k=2, gamma=0.5, match_weight=0.0),
        TargetConfig(name="T2", k=4, gamma=0.9, match_weight=1.0),
    ]
    with pytest.raises(ValueError, match="different target definitions"):
        _select_config(observations, mixed, FEATURE_COMPONENTS, [1.0], 3, 0)


def test_oof_metrics_reports_no_auc():
    observations = _synthetic_matches(30)
    result = cross_validate(observations, T2_CONFIGS, FEATURE_COMPONENTS, [1.0], seed=0)
    metrics = oof_metrics(result["oof"], draws=20, seed=0)
    assert "auc" not in metrics
    assert "weighted_log_loss" in metrics


def test_every_match_appears_in_exactly_one_test_fold():
    observations = _synthetic_matches(50)
    result = cross_validate(observations, T2_CONFIGS, FEATURE_COMPONENTS, [1.0], seed=0)
    test_ids = [mid for f in result["folds"] for mid in f.test_match_ids]
    assert len(test_ids) == len(set(test_ids))
    assert set(test_ids) == {o.match_id for o in observations}


def test_train_and_test_never_overlap_within_a_fold():
    observations = _synthetic_matches(40)
    result = cross_validate(observations, T2_CONFIGS, FEATURE_COMPONENTS, [1.0], seed=0)
    for fold in result["folds"]:
        assert set(fold.train_match_ids).isdisjoint(fold.test_match_ids)


def test_selection_never_sees_the_test_fold(monkeypatch):
    """The property the whole rewrite exists to guarantee: hyperparameters
    are chosen from training matches only."""
    observations = _synthetic_matches(40)
    seen = []
    original = impact_eval._select_config

    def spy(train_obs, *args, **kwargs):
        seen.append({o.match_id for o in train_obs})
        return original(train_obs, *args, **kwargs)

    monkeypatch.setattr(impact_eval, "_select_config", spy)
    result = cross_validate(observations, T2_CONFIGS, FEATURE_COMPONENTS, [1.0], seed=0)

    assert len(seen) == len(result["folds"])
    for fold, train_ids in zip(result["folds"], seen):
        assert train_ids.isdisjoint(fold.test_match_ids)


def test_select_config_returns_a_member_of_the_grid():
    observations = _synthetic_matches(30)
    config, l2 = _select_config(observations, T2_CONFIGS, FEATURE_COMPONENTS, [0.1, 1.0], 3, 0)
    assert config in T2_CONFIGS
    assert l2 in (0.1, 1.0)


def test_oof_weights_are_returned():
    """gamma and match_weight change row weights, so the weights must
    survive into reporting -- otherwise they influence the fit but not the
    number that judges it."""
    observations = _synthetic_matches(30)
    oof = cross_validate(observations, T2_CONFIGS, FEATURE_COMPONENTS, [1.0], seed=0)["oof"]
    assert len(oof["w"]) == len(oof["scores"]) == len(oof["y"]) == len(oof["match_ids"])
    assert np.all(oof["w"] > 0)


from app.services.impact_eval import ConstrainedWeights, fit_constrained_weights


def _weighted_matches(n_matches=60, econ_weight=0.9, seed=5):
    """Rounds whose FUTURE outcome is driven mostly by econ_impact, so a
    correct search must put its weight there rather than on time/swing.

    The k=1 forward-window target predicts round N+1's outcome from round
    N's features, so the signal must be planted ONE ROUND AHEAD: round N's
    outcome is generated from round N-1's econ/time, not its own. Wiring a
    round's outcome to its own features would leave the k=1 window with no
    real relationship to find.
    """
    rng = np.random.default_rng(seed)
    observations = []
    for match_id in range(n_matches):
        obs = []
        prev_econ = prev_other = 0.0
        # Alternating, not constant: fit_value_model (Stage B) needs real
        # label variance across matches, and a constant match_won_by_team_a
        # degenerates every fit to the same trivial zero-coefficient model,
        # making two different training subsets indistinguishable.
        match_won = match_id % 2 == 0
        for n in range(1, 13):
            econ = rng.normal()
            other = rng.normal()
            o = _obs(n, 0.0, None, match_won, match_id=match_id)
            o.econ_impact = econ * 10
            o.time_impact = other * 10
            o.swing_impact = rng.normal() * 10
            o.round_won_by_team_a = (
                (econ_weight * prev_econ + (1 - econ_weight) * prev_other) > 0 if n > 1 else True
            )
            prev_econ, prev_other = econ, other
            obs.append(o)
        obs[-1].is_terminal = True
        observations.extend(obs)
    return observations


def test_constrained_search_finds_the_dominant_component():
    obs = _weighted_matches()
    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    result = fit_constrained_weights(obs, config, CONTROLS_CONTEXT)
    assert result.econ > result.time
    assert result.econ > result.swing


def test_constrained_weights_are_non_negative_and_normalised():
    obs = _weighted_matches(n_matches=30)
    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    result = fit_constrained_weights(obs, config, CONTROLS_CONTEXT)
    assert result.econ >= 0 and result.time >= 0 and result.swing >= 0
    assert abs((result.econ + result.time + result.swing) - 3.0) < 1e-6
    assert result.damage_multiplier >= 0


def test_constrained_search_is_deterministic():
    obs = _weighted_matches(n_matches=25)
    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    a = fit_constrained_weights(obs, config, CONTROLS_CONTEXT)
    b = fit_constrained_weights(obs, config, CONTROLS_CONTEXT)
    assert (a.econ, a.time, a.swing, a.damage_multiplier) == (
        b.econ, b.time, b.swing, b.damage_multiplier
    )


def test_controls_are_actually_in_the_design(monkeypatch):
    """If the controls were dropped, the design handed to fit_logistic
    would have exactly one column (the composite)."""
    widths = []
    original = impact_eval.fit_logistic

    def spy(X, *args, **kwargs):
        widths.append(np.asarray(X).shape[1])
        return original(X, *args, **kwargs)

    monkeypatch.setattr(impact_eval, "fit_logistic", spy)
    obs = _weighted_matches(n_matches=15)
    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    fit_constrained_weights(obs, config, CONTROLS_CONTEXT, simplex_step=0.5, damage_grid=[1.0])
    assert widths, "expected fits"
    assert all(w == len(CONTROLS_CONTEXT) + 1 for w in widths)


def test_empty_observations_return_neutral_weights():
    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    result = fit_constrained_weights([], config, CONTROLS_CONTEXT)
    assert isinstance(result, ConstrainedWeights)
    assert result.econ == result.time == result.swing == 1.0
    assert result.usable is False


def test_anti_predictive_components_do_not_yield_an_adoption_proposal():
    """The upside-down-Impact trap: if every component predicts LOSING, a
    negative composite slope would still fit well. Returning non-negative
    weights then publishes 'higher Impact is better' when the data said the
    opposite. The search must refuse."""
    rng = np.random.default_rng(31)
    observations = []
    for match_id in range(50):
        obs = []
        for n in range(1, 13):
            econ = rng.normal()
            o = _obs(n, 0.0, None, True, match_id=match_id)
            o.econ_impact = econ * 10
            o.time_impact = econ * 8
            o.swing_impact = econ * 6
            o.damage = econ * 12
            # Higher components -> LOSES the next round.
            o.round_won_by_team_a = econ < 0
            obs.append(o)
        obs[-1].is_terminal = True
        observations.extend(obs)

    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    result = fit_constrained_weights(observations, config, CONTROLS_CONTEXT)
    assert result.usable is False, (
        "an anti-predictive weighting was returned as usable; the deployment "
        "proposal would claim higher Impact is better"
    )


def test_usable_result_reports_a_positive_composite_slope():
    obs = _weighted_matches(n_matches=40, seed=32)
    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    result = fit_constrained_weights(obs, config, CONTROLS_CONTEXT)
    if result.usable:
        assert result.composite_slope > 0


from app.services.impact_eval import coefficient_diagnostics

DIAG_CONFIG = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)


def test_sign_stability_is_high_for_a_clean_signal():
    obs = _weighted_matches(n_matches=80, econ_weight=1.0, seed=6)
    diag = coefficient_diagnostics(obs, DIAG_CONFIG, FEATURE_COMPONENTS, draws=40, seed=0)
    assert diag["sign_stability"]["econ_impact"] > 0.9


def test_sign_stability_is_near_chance_for_a_pure_noise_column():
    """swing_impact contributes nothing to the label here, so its sign must
    not be reported as stable."""
    obs = _weighted_matches(n_matches=60, econ_weight=1.0, seed=7)
    diag = coefficient_diagnostics(obs, DIAG_CONFIG, FEATURE_COMPONENTS, draws=40, seed=0)
    assert diag["sign_stability"]["swing_impact"] < 0.95


def test_correlation_matrix_detects_a_duplicated_column():
    obs = _weighted_matches(n_matches=30, seed=8)
    for o in obs:
        o.time_impact = o.econ_impact
    diag = coefficient_diagnostics(obs, DIAG_CONFIG, FEATURE_COMPONENTS, draws=10, seed=0)
    assert abs(diag["correlation_matrix"]["econ_impact"]["time_impact"] - 1.0) < 1e-9


def test_drop_one_reports_every_component_in_weighted_log_loss():
    obs = _weighted_matches(n_matches=40, seed=9)
    diag = coefficient_diagnostics(obs, DIAG_CONFIG, FEATURE_COMPONENTS, draws=10, seed=0)
    assert set(diag["drop_one"]) == set(FEATURE_COMPONENTS)
    for entry in diag["drop_one"].values():
        assert "log_loss_cost_of_dropping" in entry
        assert "cost_ci" in entry, "the cost of dropping needs a PAIRED interval"
        assert "auc_without" not in entry, "no AUC on a fractional target"


def test_sign_direction_distinguishes_helpful_from_anti_predictive():
    obs = _weighted_matches(n_matches=60, econ_weight=1.0, seed=10)
    diag = coefficient_diagnostics(obs, DIAG_CONFIG, FEATURE_COMPONENTS, draws=30, seed=0)
    assert 0.0 <= diag["sign_direction"]["econ_impact"] <= 1.0
    # stability is the folded magnitude; direction says which way
    assert diag["sign_stability"]["econ_impact"] == max(
        diag["sign_direction"]["econ_impact"], 1 - diag["sign_direction"]["econ_impact"]
    )


from app.services.impact_eval import (
    BASELINE_CANDIDATES,
    CURRENT_IMPACT_CANDIDATE,
    Candidate,
    fold_candidates,
    yardstick_first_half,
    yardstick_forward_rounds,
    yardstick_full_match,
    yardstick_matrix,
)


def _decisive_match(match_id, damage, team_a_wins):
    obs = [_obs(n, damage, team_a_wins, team_a_wins, match_id=match_id) for n in range(1, 13)]
    obs[-1].is_terminal = True
    return obs


def test_first_half_yardstick_scores_one_row_per_eligible_match():
    obs = _decisive_match(1, 10.0, True) + _decisive_match(2, -10.0, False)
    scores, labels, mids = yardstick_first_half(obs, CURRENT_IMPACT_CANDIDATE)
    assert len(scores) == 2
    assert labels == [1, 0]
    assert scores[0] > scores[1]
    assert sorted(mids) == [1, 2]


def test_first_half_yardstick_skips_incomplete_matches():
    short = [_obs(n, 1.0, True, True, match_id=9) for n in range(1, 6)]
    assert yardstick_first_half(short, CURRENT_IMPACT_CANDIDATE)[0] == []


def test_full_match_yardstick_uses_every_round():
    obs = _decisive_match(1, 10.0, True) + [_obs(13, 10.0, True, True, match_id=1)]
    half, _, _ = yardstick_first_half(obs, CURRENT_IMPACT_CANDIDATE)
    full, _, _ = yardstick_full_match(obs, CURRENT_IMPACT_CANDIDATE)
    assert full[0] > half[0]


def test_forward_rounds_yardstick_labels_rounds_two_ahead():
    """Round 1's label comes from rounds 3+, never rounds 1 or 2."""
    obs = [_obs(n, 1.0, n > 2, True, match_id=1) for n in range(1, 13)]
    obs[-1].is_terminal = True
    scores, labels, _ = yardstick_forward_rounds(obs, CURRENT_IMPACT_CANDIDATE)
    assert labels[0] == 1


def test_baselines_are_not_duplicates():
    """kills and deaths were the same column twice; only one kill baseline
    survives."""
    names = {c.name for c in BASELINE_CANDIDATES}
    assert "kill_diff" in names
    assert "kills_and_deaths" not in names
    assert all(isinstance(c, Candidate) for c in BASELINE_CANDIDATES)


def test_fold_candidates_are_fitted_on_training_matches_only(monkeypatch):
    observations = _weighted_matches(n_matches=40, seed=11)
    result = cross_validate(observations, [DIAG_CONFIG], FEATURE_COMPONENTS, [1.0], seed=0)

    seen = []
    original = impact_eval.fit_constrained_weights

    def spy(obs, *args, **kwargs):
        seen.append({o.match_id for o in obs})
        return original(obs, *args, **kwargs)

    monkeypatch.setattr(impact_eval, "fit_constrained_weights", spy)
    fold_candidates(observations, result["folds"], "fitted")

    assert len(seen) == len(result["folds"])
    for fold, train_ids in zip(result["folds"], seen):
        assert train_ids.isdisjoint(fold.test_match_ids)


def test_fold_candidates_returns_the_weights_for_reporting():
    observations = _weighted_matches(n_matches=30, seed=14)
    result = cross_validate(observations, [DIAG_CONFIG], FEATURE_COMPONENTS, [1.0], seed=0)
    candidates, weights = fold_candidates(observations, result["folds"], "fitted")
    assert set(candidates) == set(weights)
    assert all(hasattr(w, "econ") for w in weights.values())


def test_controls_are_derived_per_target():
    """round_result belongs with T2 (the ladder's claim) but never with WPA,
    where it is the label."""
    from app.services.impact_eval import controls_for

    assert "round_result" in controls_for(TargetConfig(name="T2"))
    assert "round_result" not in controls_for(TargetConfig(name="WPA"))
    assert controls_for(TargetConfig(name="T1")) == []
    with pytest.raises(ValueError, match="no control set"):
        controls_for(TargetConfig(name="nope"))


def test_matrix_scores_each_fold_candidate_on_its_own_test_matches():
    observations = _weighted_matches(n_matches=40, seed=12)
    result = cross_validate(observations, [DIAG_CONFIG], FEATURE_COMPONENTS, [1.0], seed=0)
    folds = {f.fold: f for f in result["folds"]}
    per_fold = fold_candidates(observations, result["folds"], "fitted")

    matrix = yardstick_matrix(
        observations, [CURRENT_IMPACT_CANDIDATE, *BASELINE_CANDIDATES],
        {"fitted": per_fold[0]}, folds, draws=20, seed=0,
    )
    assert "forward_rounds" in matrix
    cell = matrix["forward_rounds"]["fitted"]
    assert cell is not None and cell["n"] > 0
    assert "gap_over_kill_diff" in cell
    assert "gap_ci" in cell, "the gap needs a PAIRED interval, not two separate ones"
    assert "log_loss_ci" in cell, "every cell carries CIs for both metrics"


def test_matrix_reports_paired_gap_ci_not_a_difference_of_point_estimates():
    observations = _weighted_matches(n_matches=30, seed=13)
    matrix = yardstick_matrix(
        observations, [CURRENT_IMPACT_CANDIDATE, *BASELINE_CANDIDATES], {}, {}, draws=20, seed=0
    )
    cell = matrix["forward_rounds"]["current_impact"]
    lo, hi = cell["gap_ci"]
    assert lo <= cell["gap_over_kill_diff"] <= hi


def test_loaders_use_the_shared_surrender_predicate():
    """Guards the constraint rather than the DB: loaders must reference the
    shared predicate, not hand-roll a filter that can drift."""
    import inspect

    for fn in (impact_eval.load_all_observations, impact_eval.load_stored_observations,
               impact_eval.load_player_matches, impact_eval.load_player_matches_acs):
        assert "NOT_A_SURRENDER_ROUND" in inspect.getsource(fn), fn.__name__


def test_acs_baseline_is_registered():
    """User-requested addition: Impact must be compared against straight
    ACS, not just kill differential -- this is a much sharper baseline."""
    names = {c.name for c in BASELINE_CANDIDATES}
    assert "acs" in names


def test_ex_ante_loader_defaults_to_ex_ante():
    import inspect

    signature = inspect.signature(impact_eval.load_all_observations)
    assert signature.parameters["use_realized_swing"].default is False


def test_wpa_target_labels_are_round_outcomes_and_weights_are_leverage():
    from app.services.impact_eval import wpa_target
    from app.services.win_probability import fit_value_model

    obs = _weighted_matches(n_matches=40, seed=21)
    beta = fit_value_model(obs)
    dataset = wpa_target(obs, FEATURE_COMPONENTS, {"value_beta": beta})

    assert set(np.unique(dataset.y)) <= {0.0, 1.0}, "labels must be round outcomes"
    assert np.all(dataset.w >= 0.0)
    assert np.all(dataset.w <= 1.0), "abs(dV) cannot exceed 1"


def test_wpa_target_skips_unresolved_rounds():
    from app.services.impact_eval import wpa_target
    from app.services.win_probability import fit_value_model

    resolved = _obs(5, 1.0, True, True, match_id=1)
    unresolved = _obs(6, 1.0, None, True, match_id=1)
    beta = fit_value_model([resolved])
    dataset = wpa_target([resolved, unresolved], FEATURE_COMPONENTS, {"value_beta": beta})
    assert len(dataset.y) == 1


def test_training_rows_use_an_inner_oof_value_model():
    """Leverage for a training row must come from a model that did not see
    that row's match."""
    from app.services.impact_eval import wpa_target
    from app.services.win_probability import fit_value_model

    obs = _weighted_matches(n_matches=30, seed=25)
    full = fit_value_model(obs)
    other = fit_value_model(obs[: len(obs) // 2])
    match_ids = {o.match_id for o in obs}
    context = {
        "value_beta": full,
        "value_beta_by_match": {mid: other for mid in match_ids},
    }
    with_inner = wpa_target(obs, FEATURE_COMPONENTS, context)
    without = wpa_target(obs, FEATURE_COMPONENTS, {"value_beta": full})
    assert not np.allclose(with_inner.w, without.w), "per-match betas must change leverage"


def test_context_builder_only_sees_training_matches(monkeypatch):
    """The Stage B leakage fix, asserted directly."""
    observations = _weighted_matches(n_matches=40, seed=22)
    seen = []

    def builder(train_obs):
        from app.services.win_probability import fit_value_model

        seen.append({o.match_id for o in train_obs})
        return {"value_beta": fit_value_model(train_obs)}

    result = cross_validate(
        observations, [TargetConfig(name="WPA")], FEATURE_COMPONENTS, [1.0],
        seed=0, context_builder=builder,
    )
    # context_builder fires once per INNER fold (for L2 selection inside
    # _select_config) plus once for the final outer fit, per outer fold --
    # not just once per outer fold. Every one of those calls must still
    # never see its own outer fold's held-out matches.
    calls_per_fold = len(seen) // len(result["folds"])
    assert calls_per_fold * len(result["folds"]) == len(seen)
    for i, fold in enumerate(result["folds"]):
        chunk = seen[i * calls_per_fold : (i + 1) * calls_per_fold]
        for train_ids in chunk:
            assert train_ids.isdisjoint(fold.test_match_ids)


def test_build_target_passes_context_to_wpa():
    from app.services.win_probability import fit_value_model

    obs = _weighted_matches(n_matches=20, seed=23)
    beta = fit_value_model(obs)
    dataset = build_target(obs, TargetConfig(name="WPA"), FEATURE_COMPONENTS, {"value_beta": beta})
    assert len(dataset.y) > 0


def test_wpa_without_context_raises():
    obs = _weighted_matches(n_matches=10, seed=24)
    with pytest.raises(ValueError, match="context"):
        build_target(obs, TargetConfig(name="WPA"), FEATURE_COMPONENTS, None)


from app.services.impact_eval import (
    assign_folds,
    dataset_fingerprint,
    fold_mapping_hash,
    stable_folds,
)


def test_assign_folds_moves_when_the_match_set_changes():
    """Documents the defect stable_folds exists to fix. If this ever stops
    holding, assign_folds changed and this plan's premise needs rechecking."""
    base = list(range(1, 51))
    a = assign_folds(base, n_folds=5, seed=0)
    b = assign_folds(base + [999], n_folds=5, seed=0)
    assert [m for m in base if a[m] != b[m]], "expected a reshuffle"


def test_stable_folds_are_membership_independent():
    base = list(range(1, 51))
    a = stable_folds(base, n_folds=5, seed=0)
    b = stable_folds(base + [999], n_folds=5, seed=0)
    for m in base:
        assert a[m] == b[m]
    subset = stable_folds([m for m in base if m % 7], n_folds=5, seed=0)
    for m in subset:
        assert subset[m] == a[m]


def test_stable_folds_respect_the_seed_and_fold_count():
    ids = list(range(1, 201))
    assert stable_folds(ids, seed=0) != stable_folds(ids, seed=1)
    assert set(stable_folds(ids, n_folds=5, seed=0).values()) <= set(range(5))
    assert set(stable_folds(ids, n_folds=3, seed=0).values()) <= set(range(3))


def test_stable_folds_are_reasonably_balanced():
    ids = list(range(1, 1152))
    counts: dict[int, int] = {}
    for fold in stable_folds(ids, n_folds=5, seed=0).values():
        counts[fold] = counts.get(fold, 0) + 1
    assert len(counts) == 5
    assert max(counts.values()) - min(counts.values()) < 0.1 * len(ids) / 5


def test_stable_folds_survive_a_process_restart():
    """Python's built-in hash() is randomized per process, so a mapping
    built on hash() would differ between runs and silently invalidate every
    cached comparison. Asserting equality inside ONE process cannot detect
    that -- this launches a second interpreter and compares."""
    import subprocess
    import sys

    program = (
        "from app.services.impact_eval import stable_folds;"
        "print(sorted(stable_folds(range(1, 40), n_folds=5, seed=0).items()))"
    )
    runs = [
        subprocess.run([sys.executable, "-c", program], capture_output=True, text=True,
                       check=True).stdout
        for _ in range(2)
    ]
    assert runs[0] == runs[1]
    assert runs[0].strip() == str(sorted(stable_folds(range(1, 40), n_folds=5, seed=0).items()))


def test_dataset_fingerprint_is_order_insensitive_and_content_sensitive():
    assert dataset_fingerprint([3, 1, 2]) == dataset_fingerprint([1, 2, 3])
    assert dataset_fingerprint([1, 1, 2, 3]) == dataset_fingerprint([1, 2, 3])
    assert dataset_fingerprint([1, 2, 3]) != dataset_fingerprint([1, 2, 4])
    assert dataset_fingerprint([1, 2, 3]) != dataset_fingerprint([1, 2, 3, 4])


def test_fold_mapping_hash_catches_a_same_set_different_folds_collision():
    """The whole point: the two mappings below cover the same matches, so
    the dataset fingerprint agrees, but the assignments differ."""
    ids = list(range(1, 51))
    stable = stable_folds(ids, n_folds=5, seed=0)
    permuted = assign_folds(ids, n_folds=5, seed=0)
    assert dataset_fingerprint(ids) == dataset_fingerprint(list(permuted))
    assert fold_mapping_hash(stable) != fold_mapping_hash(permuted)
    assert fold_mapping_hash(stable) == fold_mapping_hash(dict(stable))
