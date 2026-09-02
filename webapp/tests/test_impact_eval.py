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
        RoundPlayerStat(match_player_id=1, kills=2, deaths=0, assists=0, loadout=800),
        RoundPlayerStat(match_player_id=2, kills=0, deaths=2, assists=0, loadout=800),
    ]
    r2 = Round(id=102, match_id=1, round_number=2, outcome="Team B Wins")
    r2.player_stats = [
        RoundPlayerStat(match_player_id=1, kills=0, deaths=1, assists=0, loadout=4500),
        RoundPlayerStat(match_player_id=2, kills=1, deaths=0, assists=0, loadout=2000),
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
        impact_diff=damage, kill_diff=0.0,
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
        for n in range(1, 13):
            econ = rng.normal()
            other = rng.normal()
            o = _obs(n, 0.0, None, True, match_id=match_id)
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
