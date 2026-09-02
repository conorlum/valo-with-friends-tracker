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
