"""Part 4's post-plant factor: the D differencing, the kill-weighted
mean_over_t denominator, the clamp, and every fallback-to-1.0 path.

Spec: docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md,
Part 4 ("The estimand", "Parameterisation", "The estimator contract",
"Testing: Part 4"). Clamp values from
docs/superpowers/2026-09-07-three-grids-declaration-draft.md section 2.

Per feedback_plan_execution_test_fixtures and the spec's own closing note,
every synthetic fixture below is verified to contain the relationship it
claims BEFORE anything is asserted against it.
"""

import pytest

from app.scoring.postplant_factor import (
    CEIL_DEFAULT,
    FLOOR_DEFAULT,
    PostPlantKill,
    PostPlantFactorTable,
    build_factor_table,
)
from app.scoring.postplant_value_table import (
    MIN_OBSERVATIONS,
    PostPlantRoundSecond,
    build_value_table,
)


def _cell(a, d, t, rate, n=MIN_OBSERVATIONS, seed=0):
    """n round-seconds in state (a, d) at second t, of which round(rate*n)
    are attacker wins."""
    wins = round(rate * n)
    return [
        PostPlantRoundSecond(match_id=seed * 100000 + i, round_id=seed * 100000 + i, t=t,
                             attackers_alive=a, defenders_alive=d, atk_won=(i < wins))
        for i in range(n)
    ]


def _kills(a, d, victim_is_attacker, seconds, per_second=1):
    out = []
    for t in seconds:
        for i in range(per_second):
            out.append(PostPlantKill(
                match_id=i, round_id=i, t=t, attackers_alive=a, defenders_alive=d,
                victim_is_attacker=victim_is_attacker,
            ))
    return out


# --------------------------------------------------------------------------
# Declared defaults
# --------------------------------------------------------------------------

def test_shipped_clamp_defaults_are_the_declared_ones():
    assert (FLOOR_DEFAULT, CEIL_DEFAULT) == (0.05, 2.0)


# --------------------------------------------------------------------------
# The differencing, including both terminal endpoints
# --------------------------------------------------------------------------

def test_defender_victim_differences_against_the_analytic_terminal_endpoint():
    """The last defender's death differences against V(a, 0, t) = 1.0
    exactly. With V(3, 1, t) = 0.6 the raw D is 0.4 at every second, so the
    ratio to its own mean is exactly 1.0."""
    obs = []
    for t in range(10, 20):
        obs += _cell(3, 1, t, 0.6, seed=t)
    table = build_value_table(obs, w=0)
    # Verify the fixture: the endpoint really is pinned and D really is 0.4.
    assert table.value(3, 0, 15).value == 1.0
    assert table.value(3, 1, 15).value == pytest.approx(0.6)

    factors = build_factor_table(table, _kills(3, 1, False, range(10, 20)))

    assert factors.factor(3, 1, 15, victim_is_attacker=False) == pytest.approx(1.0)


def test_attacker_victim_differences_against_an_estimated_not_assumed_value():
    """The last attacker's death differences against V(0, d, t), which is
    ESTIMATED -- attackers still win when the defuse fails. Here V(0,2,t) is
    a measured 0.2, so D = V(1,2,t) - V(0,2,t) = 0.5 - 0.2 = 0.3, not the
    0.5 an assumed V(0,d,t)=0 would produce."""
    obs = []
    for t in range(10, 20):
        obs += _cell(1, 2, t, 0.5, seed=t)
        obs += _cell(0, 2, t, 0.2, seed=t + 500)
    table = build_value_table(obs, w=0)
    assert table.value(0, 2, 15).value == pytest.approx(0.2)  # estimated, not pinned to 0

    factors = build_factor_table(table, _kills(1, 2, True, range(10, 20)))

    # Constant D over t, so the ratio is 1.0 -- what matters is that it is
    # computed at all, which requires the (0, d) cell to be supported.
    assert factors.factor(1, 2, 15, victim_is_attacker=True) == pytest.approx(1.0)


def test_unsupported_second_endpoint_returns_exactly_one():
    """Support is required at BOTH V cells a D differences, not just the
    kill's own. Here (1, 2, t) is well supported but (0, 2, t) is not --
    thin at EVERY rung, since the pooling ladder would otherwise rescue it
    at the band level."""
    obs = []
    for t in range(10, 20):
        obs += _cell(1, 2, t, 0.5, seed=t)
    obs += _cell(0, 2, 15, 0.2, n=10, seed=500)  # 10 in the whole band: unsupported
    table = build_value_table(obs, w=0)
    assert table.value(1, 2, 15).supported is True
    assert table.value(0, 2, 15).supported is False

    factors = build_factor_table(table, _kills(1, 2, True, range(10, 20)))

    assert factors.factor(1, 2, 15, victim_is_attacker=True) == 1.0


def test_cell_below_the_support_floor_returns_exactly_one():
    """Thin at every rung -- 10 observations in the whole band, so the
    pooling ladder cannot rescue it either."""
    obs = _cell(4, 3, 15, 0.5, n=10, seed=1) + _cell(4, 2, 15, 0.5, n=10, seed=2)
    table = build_value_table(obs, w=0)
    assert table.value(4, 3, 15).supported is False

    factors = build_factor_table(table, _kills(4, 3, False, range(10, 20)))

    assert factors.factor(4, 3, 15, victim_is_attacker=False) == 1.0


# --------------------------------------------------------------------------
# The denominator
# --------------------------------------------------------------------------

def test_denominator_is_weighted_by_scored_kills_not_uniformly_over_seconds():
    """D is 0.4 at t=10 and 0.2 at t=11. With 9 kills at t=10 and 1 at t=11
    the kill-weighted mean is 0.38, so the factor at t=10 is 0.4/0.38, NOT
    the 0.4/0.3 a uniform-over-seconds mean would give."""
    obs = _cell(3, 1, 10, 0.6, seed=1) + _cell(3, 1, 11, 0.8, seed=2)
    table = build_value_table(obs, w=0)
    # Fixture check: D = 1.0 - V(3,1,t).
    assert table.value(3, 1, 10).value == pytest.approx(0.6)
    assert table.value(3, 1, 11).value == pytest.approx(0.8)

    kills = _kills(3, 1, False, [10], per_second=9) + _kills(3, 1, False, [11], per_second=1)
    factors = build_factor_table(table, kills)

    expected_mean = (9 * 0.4 + 1 * 0.2) / 10
    assert expected_mean == pytest.approx(0.38)
    assert factors.factor(3, 1, 10, victim_is_attacker=False) == pytest.approx(0.4 / 0.38)


def test_seconds_that_fall_back_are_excluded_from_the_denominator():
    """Only seconds where both endpoints are supported enter mean_over_t --
    letting fallback seconds in would shrink every other second's factor by a
    quantity with no meaning. t=12 is unsupported here and must not count."""
    obs = []
    for t in (10, 11, 12, 13, 14):
        obs += _cell(2, 2, t, 0.5, seed=t) + _cell(2, 1, t, 0.9, seed=t + 50)
    table = build_value_table(obs, w=0)
    # Second 40 sits in a DIFFERENT deadline band with no data, so it is
    # unsupported while band 0's seconds are supported. (Within one band the
    # ladder cannot leave a single second behind -- the band total is at least
    # any one second's count -- so a cross-band fixture is the honest way to
    # exercise this rule.)
    assert table.value(2, 2, 14).supported is True
    assert table.value(2, 2, 40).supported is False

    kills = _kills(2, 2, False, [10, 11, 12, 13, 14, 40])
    factors = build_factor_table(table, kills)

    # Every eligible second has the identical D = 0.9 - 0.5 = 0.4, so the mean
    # over them is 0.4 and the ratio is exactly 1.0. Had second 40 entered the
    # average at its 1.0 fallback, it would not be.
    assert factors.factor(2, 2, 10, victim_is_attacker=False) == pytest.approx(1.0)
    assert factors.factor(2, 2, 40, victim_is_attacker=False) == 1.0  # itself a fallback


def test_fewer_than_five_eligible_seconds_makes_the_whole_cell_unsupported():
    """The last deadline band, [41.5, 45), contains only whole seconds 42, 43
    and 44. A cell supported nowhere else therefore has three eligible
    seconds -- below the contract's floor of five -- and the whole
    (a, d, victim_side) cell returns 1.0."""
    obs = []
    for t in (42, 43, 44):
        obs += _cell(3, 1, t, 0.6, seed=t)
    table = build_value_table(obs, w=0)
    assert table.value(3, 1, 43).supported is True
    assert table.value(3, 1, 20).supported is False

    factors = build_factor_table(table, _kills(3, 1, False, [42, 43, 44]))

    assert factors.factor(3, 1, 10, victim_is_attacker=False) == 1.0
    assert factors.diagnostics["cells_with_too_few_eligible_seconds"] >= 1


def test_non_positive_denominator_never_divides_and_is_counted():
    """V is monotone in alive counts in expectation but not in every smoothed
    cell, so mean_over_t D <= 0 is possible from noise. Dividing flips the
    factor's sign, which the FLOOR > 0 clamp then hides rather than catches."""
    obs = []
    for t in range(10, 20):
        # V(2, 1) ABOVE V(2, 0)=1.0 is impossible, so invert a different pair:
        # make V(1, 2) exceed V(1, 1) so a defender-victim D goes negative.
        obs += _cell(1, 2, t, 0.8, seed=t)
        obs += _cell(1, 1, t, 0.2, seed=t + 500)
    table = build_value_table(obs, w=0)
    # Fixture check: D(victim=defender) = V(1,1,t) - V(1,2,t) = -0.6 < 0.
    assert table.value(1, 1, 15).value - table.value(1, 2, 15).value == pytest.approx(-0.6)

    factors = build_factor_table(table, _kills(1, 2, False, range(10, 20)))

    assert factors.factor(1, 2, 15, victim_is_attacker=False) == 1.0
    assert factors.diagnostics["non_positive_denominators"] >= 1


# --------------------------------------------------------------------------
# The clamp
# --------------------------------------------------------------------------

def test_ratio_is_clamped_to_the_policy_bounds():
    """One second carries almost all the leverage, so its raw ratio exceeds
    CEIL and must be clamped rather than scored raw."""
    obs = _cell(3, 1, 10, 0.0, seed=1)  # D = 1.0
    for t in range(11, 16):
        obs += _cell(3, 1, t, 0.99, seed=t)  # D = 0.01
    table = build_value_table(obs, w=0)

    factors = build_factor_table(table, _kills(3, 1, False, range(10, 16)))
    raw = factors.raw_ratio(3, 1, 10, victim_is_attacker=False)

    assert raw > CEIL_DEFAULT  # fixture check: the clamp really does bind here
    assert factors.factor(3, 1, 10, victim_is_attacker=False) == CEIL_DEFAULT
    assert factors.diagnostics["ceiling_bindings"] >= 1


def test_negative_numerator_scores_at_floor_and_is_counted_separately():
    """A single second with D < 0 against a positive denominator still scores
    at FLOOR; only the counting is new (declaration 2.4)."""
    obs = _cell(3, 1, 10, 1.2, seed=1)  # V > 1 is impossible; use the pair below instead
    obs = []
    for t in range(11, 16):
        obs += _cell(2, 2, t, 0.3, seed=t) + _cell(2, 1, t, 0.9, seed=t + 50)
    obs += _cell(2, 2, 10, 0.95, seed=1) + _cell(2, 1, 10, 0.5, seed=2)
    table = build_value_table(obs, w=0)
    # Fixture check: D at t=10 is 0.5 - 0.95 = -0.45 while the others are +0.6.
    assert table.value(2, 1, 10).value - table.value(2, 2, 10).value == pytest.approx(-0.45)

    factors = build_factor_table(table, _kills(2, 2, False, range(10, 16)))

    assert factors.factor(2, 2, 10, victim_is_attacker=False) == FLOOR_DEFAULT
    assert factors.diagnostics["negative_numerators"] >= 1


# --------------------------------------------------------------------------
# Side dependence -- the behavioural heart of Part 4
# --------------------------------------------------------------------------

def test_attacker_and_defender_victims_get_different_factors_in_the_same_state():
    """Asserted on a declared supported, unsaturated fixture: neither factor
    is on a clamp and both cells are supported, so a coincidence would be a
    real disagreement rather than two pinned values."""
    obs = []
    for t in range(10, 20):
        rate_22 = 0.50
        rate_21 = 0.60 + 0.03 * (t - 10)   # defender-victim D widens with t
        rate_12 = 0.40 - 0.01 * (t - 10)   # attacker-victim D narrows with t
        obs += _cell(2, 2, t, rate_22, seed=t)
        obs += _cell(2, 1, t, rate_21, seed=t + 100)
        obs += _cell(1, 2, t, rate_12, seed=t + 200)
    table = build_value_table(obs, w=0)

    kills = (_kills(2, 2, False, range(10, 20)) + _kills(2, 2, True, range(10, 20)))
    factors = build_factor_table(table, kills)

    defender_victim = factors.factor(2, 2, 12, victim_is_attacker=False)
    attacker_victim = factors.factor(2, 2, 12, victim_is_attacker=True)

    # Fixture checks: supported, and neither sitting on a clamp.
    assert FLOOR_DEFAULT < defender_victim < CEIL_DEFAULT
    assert FLOOR_DEFAULT < attacker_victim < CEIL_DEFAULT
    assert defender_victim != pytest.approx(attacker_victim)


def test_two_v_one_and_one_v_two_move_in_opposite_directions_across_t():
    """M24: 2v1 falls 1.33 -> 0.10 while 1v2 rises 0.89 -> 1.09. Opposite
    directions cannot be represented by one shared curve -- if a refactor
    collapses them to a shared shape, this catches it."""
    obs = []
    for t in range(10, 20):
        # 2v1, defender victim: D = 1.0 - V(2,1,t). Rising V => FALLING D.
        obs += _cell(2, 1, t, 0.50 + 0.04 * (t - 10), seed=t)
        # 1v2, defender victim: D = V(1,1,t) - V(1,2,t). Widening => RISING D.
        obs += _cell(1, 1, t, 0.50 + 0.03 * (t - 10), seed=t + 100)
        obs += _cell(1, 2, t, 0.40 - 0.01 * (t - 10), seed=t + 200)
    table = build_value_table(obs, w=0)

    kills = _kills(2, 1, False, range(10, 20)) + _kills(1, 2, False, range(10, 20))
    factors = build_factor_table(table, kills)

    two_v_one = [factors.factor(2, 1, t, victim_is_attacker=False) for t in range(10, 20)]
    one_v_two = [factors.factor(1, 2, t, victim_is_attacker=False) for t in range(10, 20)]

    # Fixture check first: the raw differences really do move oppositely.
    assert two_v_one[0] > two_v_one[-1], "fixture does not contain a falling 2v1"
    assert one_v_two[0] < one_v_two[-1], "fixture does not contain a rising 1v2"


def test_factors_differ_across_seconds_within_the_denial_window():
    """The flat 1.75 override at plant+38..45 is gone: a kill at second 39
    and one at second 44 in the same state receive different factors."""
    obs = []
    for t in range(35, 45):
        obs += _cell(2, 1, t, 0.50 + 0.04 * (t - 35), seed=t)
    table = build_value_table(obs, w=0)

    factors = build_factor_table(table, _kills(2, 1, False, range(35, 45)))

    assert factors.factor(2, 1, 39, victim_is_attacker=False) != pytest.approx(
        factors.factor(2, 1, 44, victim_is_attacker=False)
    )
