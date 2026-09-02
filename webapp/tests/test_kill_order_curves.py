"""The empirical swing table and the curve parameterizations. Pure: the
state visits are constructed directly, no DB."""

import numpy as np
import pytest

from app.services.kill_order_curves import (
    EVEN_STATE,
    MARGIN,
    TOTAL_ALIVE,
    estimate_swing_table,
)
from app.services.kill_order_leverage import PARAMS, PARAM_INDEX, StateVisitRow


def visits(spec):
    """spec: {(own, opp): (wins, losses)} -> a flat list of visit rows."""
    rows = []
    round_id = 0
    for (own, opp), (wins, losses) in spec.items():
        for won in [True] * wins + [False] * losses:
            round_id += 1
            rows.append(StateVisitRow(match_id=round_id % 7, round_id=round_id,
                                      own=own, opp=opp, won=won))
    return rows


def test_state_axes_are_fixed_constants_over_the_25_lattice_states():
    """Standardized over the STATES, unweighted -- so the transform carries
    no data dependence and needs no per-fold recomputation."""
    assert MARGIN.shape == (len(PARAMS),)
    assert TOTAL_ALIVE.shape == (len(PARAMS),)
    lattice = slice(0, 25)
    assert np.isclose(MARGIN[lattice].mean(), 0.0, atol=1e-12)
    assert np.isclose(MARGIN[lattice].std(), 1.0, atol=1e-12)
    assert np.isclose(TOTAL_ALIVE[lattice].mean(), 0.0, atol=1e-12)
    assert MARGIN[PARAM_INDEX["5v1"]] > MARGIN[PARAM_INDEX["1v5"]]
    assert TOTAL_ALIVE[PARAM_INDEX["5v5"]] > TOTAL_ALIVE[PARAM_INDEX["1v1"]]


def test_the_fallback_parameter_sits_at_the_origin_of_both_axes():
    """It has no state, so it receives no tilt. Anything else would invent
    a position for it."""
    assert MARGIN[PARAM_INDEX["fallback"]] == 0.0
    assert TOTAL_ALIVE[PARAM_INDEX["fallback"]] == 0.0
    assert EVEN_STATE[PARAM_INDEX["fallback"]] == 0.0


def test_even_state_indicator_marks_only_the_diagonal():
    for own in range(1, 6):
        assert EVEN_STATE[PARAM_INDEX[f"{own}v{own}"]] == 1.0
    assert EVEN_STATE[PARAM_INDEX["3v2"]] == 0.0


def test_swing_is_the_difference_between_neighbouring_state_win_rates():
    table = estimate_swing_table(visits({
        (3, 3): (50, 50),   # P(win) = 0.5
        (3, 2): (75, 25),   # P(win) = 0.75 -- the state a 3v3 kill reaches
    }))
    assert np.isclose(table.dp[PARAM_INDEX["3v3"]], 0.25)
    assert table.visits[PARAM_INDEX["3v3"]] == 100


def test_a_state_with_no_successor_data_is_marked_rather_than_guessed():
    """Every OTHER lattice state needs its own win-rate data AND its
    successor's, or it too would show up incomplete for the trivial reason
    that nothing populated it -- a fixture supplying only (3, 3) (as a
    first draft of this test did) leaves all 25 states incomplete, not
    just the one missing its successor.

    The gap must also be a TERMINAL successor (own, 0): a lattice state's
    successor is itself a lattice state's own "here" value whenever
    opp - 1 >= 1, so deleting e.g. (3, 2) breaks "3v2" too (it needs (3, 2)
    as ITS OWN here-lookup), not just "3v3"'s after-lookup on the same
    dict entry. (3, 1)'s successor (3, 0) is never anyone else's here-value
    (opp=0 is not part of the 25-state lattice), so it is the one gap that
    isolates to exactly one incomplete state."""
    spec = {(own, opp): (3, 3) for own in range(1, 6) for opp in range(0, 6)}
    del spec[(3, 0)]  # 3v1's successor -- the one gap this test is about
    table = estimate_swing_table(visits(spec))
    assert np.isnan(table.dp[PARAM_INDEX["3v1"]])
    assert table.incomplete == ["3v1"]


def test_opp_zero_is_a_won_round_and_supplies_the_1v1_successor():
    table = estimate_swing_table(visits({
        (1, 1): (40, 60),
        (1, 0): (100, 0),
    }))
    assert np.isclose(table.dp[PARAM_INDEX["1v1"]], 0.6)


def test_the_fallback_parameter_has_no_swing_value():
    table = estimate_swing_table(visits({(3, 3): (1, 1), (3, 2): (1, 1)}))
    assert np.isnan(table.dp[PARAM_INDEX["fallback"]])


def test_estimating_from_a_training_subset_ignores_held_out_matches():
    """The cross-fitting guarantee, as a test rather than a comment. If the
    held-out rows leaked, the 3v3 win rate would move off 0.5."""
    training = visits({(3, 3): (50, 50), (3, 2): (50, 50)})
    held_out = [StateVisitRow(match_id=999, round_id=10_000 + i, own=3, opp=3, won=True)
                for i in range(500)]
    from_training_only = estimate_swing_table(training)
    assert np.isclose(from_training_only.dp[PARAM_INDEX["3v3"]], 0.0)
    polluted = estimate_swing_table(training + held_out)
    assert not np.isclose(polluted.dp[PARAM_INDEX["3v3"]], 0.0)


from app.services.kill_order_curves import (
    ScoredCandidate,
    basis_for,
    check_deployable,
    construction_normalize,
    family_a_leverage,
    normalize_for_display,
    recover_graph,
    score_rounds,
)
from app.services.kill_order_leverage import COMPONENTS, shipped_graph


class FakeTeamRow:
    def __init__(self, kill, death, damage_diff=0.0):
        self.kill = kill
        self.death = death
        self.death_untraded = death
        self.damage_diff = damage_diff


def rows_with(values, damage=0.0):
    """One row whose (kill + death) leverage equals `values` per parameter,
    spread evenly across the three components."""
    kill = np.zeros((len(PARAMS), len(COMPONENTS)))
    for name, value in values.items():
        kill[PARAM_INDEX[name], :] = value / len(COMPONENTS)
    return FakeTeamRow(kill=kill, death=np.zeros_like(kill), damage_diff=damage)


def test_family_a_leverage_collapses_components_with_the_shipped_weights():
    """FACTOR_WEIGHTS are all 1.0 and divide by 3, so Family A's single
    column per parameter is the mean over components of kill + death."""
    row = rows_with({"3v3": 6.0})
    X = family_a_leverage([row])
    assert X.shape == (1, len(PARAMS))
    assert np.isclose(X[0, PARAM_INDEX["3v3"]], 2.0)


def test_bases_have_the_expected_widths_and_nest():
    """G1b/G2 are over the LATTICE ONLY (25 rows): their fallback is pinned
    and enters the fit through the composite damage column instead, and
    fit_family_a multiplies them against the lattice-sliced leverage
    (train_leverage[:, LATTICE] @ basis), which requires exactly 25 rows.
    G4 is unstructured over the full 26 columns, fallback included."""
    table = type("T", (), {"dp": np.linspace(0.01, 0.5, len(PARAMS))})()
    assert basis_for("G1b", table).shape == (25, 2)
    assert basis_for("G2", table).shape == (25, 5)
    assert basis_for("G4", table).shape == (len(PARAMS), len(PARAMS))
    # G2 contains G1b: zeroing its last three coefficients leaves the affine fit.
    g2 = basis_for("G2", table)
    assert np.allclose(g2[:, :2], basis_for("G1b", table))


def test_a_nan_dp_is_pinned_rather_than_propagated_into_the_basis():
    """The fallback has no dP. If it reached the basis as NaN the whole fit
    would return NaN, silently."""
    dp = np.full(len(PARAMS), 0.2)
    dp[PARAM_INDEX["fallback"]] = np.nan
    table = type("T", (), {"dp": dp})()
    for name in ("G1b", "G2"):
        assert np.all(np.isfinite(basis_for(name, table)))


def test_recover_divides_every_coefficient_by_the_damage_coefficient():
    basis = np.eye(len(PARAMS))
    beta = np.zeros(1 + 1 + len(PARAMS))
    beta[1] = 2.0                       # d
    beta[2:] = 8.0                      # q
    graph, d = recover_graph(beta, damage_index=0, basis=basis)
    assert np.isclose(d, 2.0)
    assert np.allclose(graph, 4.0)


def test_recovery_refuses_a_non_positive_damage_coefficient():
    basis = np.eye(len(PARAMS))
    beta = np.zeros(1 + 1 + len(PARAMS))
    beta[1] = -0.5
    beta[2:] = 8.0
    graph, d = recover_graph(beta, damage_index=0, basis=basis)
    assert d == -0.5
    ok, reasons = check_deployable(graph, d, exposure=np.ones(len(PARAMS)))
    assert not ok
    assert any("damage" in r for r in reasons)


def test_deployability_rejects_a_negative_price_with_real_exposure():
    graph = shipped_graph().copy()
    graph[PARAM_INDEX["3v3"]] = -20.0
    exposure = np.ones(len(PARAMS)) * 1000
    ok, reasons = check_deployable(graph, d=1.0, exposure=exposure)
    assert not ok
    assert any("3v3" in r for r in reasons)


def test_deployability_ignores_a_negative_price_with_no_exposure():
    graph = shipped_graph().copy()
    graph[PARAM_INDEX["1v5"]] = -1.0
    exposure = np.ones(len(PARAMS)) * 1000
    exposure[PARAM_INDEX["1v5"]] = 0.0
    ok, _ = check_deployable(graph, d=1.0, exposure=exposure)
    assert ok


def test_scoring_is_damage_plus_the_graph_weighted_leverage():
    row = rows_with({"3v3": 6.0}, damage=12.0)
    X = family_a_leverage([row])
    graph = np.zeros(len(PARAMS))
    graph[PARAM_INDEX["3v3"]] = 10.0
    scores = score_rounds(X, np.array([12.0]), graph)
    assert np.isclose(scores[0], 12.0 + 2.0 * 10.0)


def test_construction_normalization_matches_a_training_reference_mean():
    exposure = np.zeros(len(PARAMS))
    exposure[PARAM_INDEX["3v3"]] = 3.0
    exposure[PARAM_INDEX["5v5"]] = 1.0
    raw = np.zeros(len(PARAMS))
    raw[PARAM_INDEX["3v3"]] = 0.2
    raw[PARAM_INDEX["5v5"]] = 0.6
    reference = shipped_graph()
    scaled = construction_normalize(raw, exposure, reference)
    target = float(np.sum(exposure * reference) / exposure.sum())
    assert np.isclose(float(np.sum(exposure * scaled) / exposure.sum()), target)
    assert np.isclose(scaled[PARAM_INDEX["5v5"]] / scaled[PARAM_INDEX["3v3"]], 3.0)


def test_display_normalization_does_not_change_ordering():
    graph = shipped_graph()
    exposure = np.ones(len(PARAMS))
    shown = normalize_for_display(graph, exposure)
    assert np.isclose(float(np.sum(exposure * shown) / exposure.sum()), 136.6, atol=1e-6)
    assert np.array_equal(np.argsort(graph), np.argsort(shown))
