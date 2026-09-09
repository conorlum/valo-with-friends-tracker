"""Part 4's centring constant, solved on the post-plant population alone.

    c = ( SUM_all(K * ramp) - SUM_fallback(K * 1) ) / SUM_supported(K * s)

Spec: docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md,
Part 4 ("Centring"). SUMS, not means -- the mean form is wrong whenever the
supported and fallback populations differ in size, and the spec carries a
worked counterexample where it returns a NEGATIVE constant.
"""

import pytest

from app.scoring.postplant_centering import (
    DEATH_RESIDUAL_TOLERANCE,
    DegenerateCentering,
    solve_postplant_centering,
)


def test_neutral_factors_give_c_of_exactly_one():
    """If the new factor already equals the ramp everywhere, nothing needs
    rescaling."""
    result = solve_postplant_centering(
        kill_order_bonuses=[100.0, 200.0, 150.0],
        ramp_factors=[1.2, 1.2, 1.2],
        new_factors=[1.2, 1.2, 1.2],
        supported=[True, True, True],
        traded_factors=[1.0, 1.0, 1.0],
        death_ramp_factors=[1.2, 1.2, 1.2],
    )

    assert result.c == pytest.approx(1.0)
    assert result.death_side_residual == pytest.approx(0.0)


def test_the_specs_worked_counterexample_returns_the_sum_form_not_the_mean_form():
    """One supported kill (K=100, s=1) and one fallback kill (K=200), both
    scoring 1.2 under today's ramp. The mean form returns (180-200)/100 =
    -0.2, a negative constant that would make every affected kill worth
    negative Impact. The sum form returns (360-200)/100 = 1.6."""
    result = solve_postplant_centering(
        kill_order_bonuses=[100.0, 200.0],
        ramp_factors=[1.2, 1.2],
        new_factors=[1.0, 1.0],
        supported=[True, False],
        traded_factors=[1.0, 1.0],
        death_ramp_factors=[1.2, 1.2],
    )

    assert result.c == pytest.approx(1.6)
    assert result.c > 0


def test_total_contribution_is_preserved_with_fallbacks_held_at_one():
    """The gate: the mean contribution over the WHOLE post-plant population,
    fallback cells included at exactly 1.0, matches the ramp's."""
    kill_order_bonuses = [120.0, 90.0, 200.0, 60.0]
    ramp_factors = [1.10, 1.35, 1.50, 1.05]
    new_factors = [0.60, 1.80, 1.00, 0.40]
    supported = [True, True, False, True]

    result = solve_postplant_centering(
        kill_order_bonuses=kill_order_bonuses, ramp_factors=ramp_factors,
        new_factors=new_factors, supported=supported,
        traded_factors=[1.0] * 4,
        death_ramp_factors=ramp_factors,
    )

    under_ramp = sum(k * r for k, r in zip(kill_order_bonuses, ramp_factors))
    under_new = sum(
        k * (result.c * s if sup else 1.0)
        for k, s, sup in zip(kill_order_bonuses, new_factors, supported)
    )
    assert under_new == pytest.approx(under_ramp)


def test_fallback_cells_are_not_scaled_by_c():
    """Centring a fallback cell would multiply the neutral 1.0 by c, which
    contradicts the rule that an unsupported cell returns exactly 1.0."""
    result = solve_postplant_centering(
        kill_order_bonuses=[100.0, 100.0],
        ramp_factors=[1.5, 1.5],
        new_factors=[0.5, 1.0],
        supported=[True, False],
        traded_factors=[1.0, 1.0],
        death_ramp_factors=[1.5, 1.5],
    )

    assert result.c != pytest.approx(1.0)  # the supported kill really is rescaled
    assert result.fallback_kills == 1
    assert result.supported_kills == 1


def test_c_is_structurally_positive_because_the_ramp_is_at_least_one():
    """Today's ramp is >= 1.05 everywhere it applies, so
    SUM_fallback(K*1) <= SUM_all(K*ramp) and the numerator cannot go
    negative. Checked on a fallback-heavy population, which is where the
    withdrawn mean form went negative."""
    result = solve_postplant_centering(
        kill_order_bonuses=[50.0] + [300.0] * 10,
        ramp_factors=[1.05] + [1.75] * 10,
        new_factors=[0.9] + [1.0] * 10,
        supported=[True] + [False] * 10,
        traded_factors=[1.0] * 11,
        death_ramp_factors=[1.05] + [0.5] * 10,
    )

    assert result.c > 0


def test_no_supported_cell_is_reported_as_degenerate_not_solved():
    """Every factor pinned at 1.0 leaves nothing for c to scale. That is a
    degenerate configuration, not a solvable one: the estimator must report
    it rather than returning some c."""
    with pytest.raises(DegenerateCentering):
        solve_postplant_centering(
            kill_order_bonuses=[100.0, 200.0],
            ramp_factors=[1.2, 1.4],
            new_factors=[1.0, 1.0],
            supported=[False, False],
            traded_factors=[1.0, 1.0],
            death_ramp_factors=[1.2, 1.4],
        )


def test_death_side_residual_is_reported_against_the_two_percent_tolerance():
    assert DEATH_RESIDUAL_TOLERANCE == 0.02

    result = solve_postplant_centering(
        kill_order_bonuses=[100.0, 100.0, 100.0],
        ramp_factors=[1.2, 1.2, 1.2],
        new_factors=[0.8, 1.2, 1.6],
        supported=[True, True, True],
        traded_factors=[1.0, 0.5, 0.2],
        death_ramp_factors=[1.2, 1.2, 1.2],
    )

    # Reported, never tuned to zero -- the kill side is what is pinned exactly.
    assert isinstance(result.death_side_residual, float)
    assert result.c == pytest.approx(1.2 * 3 / (0.8 + 1.2 + 1.6))


def test_misaligned_inputs_are_rejected():
    with pytest.raises(ValueError):
        solve_postplant_centering(
            kill_order_bonuses=[100.0],
            ramp_factors=[1.2, 1.2],
            new_factors=[1.0],
            supported=[True],
            traded_factors=[1.0],
            death_ramp_factors=[1.2, 1.2],
        )


# --------------------------------------------------------------------------
# Review finding 4 -- the death-side residual needs the DEATH-side baseline
# --------------------------------------------------------------------------


def test_the_death_residual_is_measured_against_the_death_side_ramp():
    """In plant+38..45 the legacy scorer pays 1.75 on the kill side and 0.5 on
    the death side. Two kills in that window, both supported, both scoring a
    new factor of 1.0:

        c            = (100*1.75 + 100*1.75) / (100*1.0 + 100*1.0) = 1.75
        new deaths   = 100*1.0*(1.75*1.0) * 2                      = 350
        legacy deaths= 100*1.0*0.5        * 2                      = 100
        residual     = 350/100 - 1                                 = +2.5

    Measured against the KILL-side ramp instead, the same population returns
    350/350 - 1 = 0.0 -- a residual of exactly zero, reported as if the death
    side were already perfectly preserved, in the one window where the two
    regimes diverge by 3.5x. That is the defect.
    """
    result = solve_postplant_centering(
        kill_order_bonuses=[100.0, 100.0],
        ramp_factors=[1.75, 1.75],
        new_factors=[1.0, 1.0],
        supported=[True, True],
        traded_factors=[1.0, 1.0],
        death_ramp_factors=[0.5, 0.5],
    )

    assert result.c == pytest.approx(1.75)
    assert result.death_side_residual == pytest.approx(2.5)
    # The number the kill-side baseline would have produced.
    assert result.death_side_residual != pytest.approx(0.0)


def test_the_kill_side_constant_ignores_the_death_ramp_entirely():
    """c preserves the KILL side exactly. Handing it a different death-side
    ramp must not move it -- only one side can be pinned by one constant, and
    the spec pins the kill side."""
    kwargs = dict(
        kill_order_bonuses=[100.0, 200.0],
        ramp_factors=[1.75, 1.2],
        new_factors=[0.9, 1.4],
        supported=[True, True],
        traded_factors=[1.0, 1.0],
    )

    a = solve_postplant_centering(**kwargs, death_ramp_factors=[0.5, 1.2])
    b = solve_postplant_centering(**kwargs, death_ramp_factors=[1.75, 1.2])

    assert a.c == pytest.approx(b.c)
    assert a.death_side_residual != pytest.approx(b.death_side_residual)


def test_a_misaligned_death_ramp_is_rejected_like_every_other_list():
    with pytest.raises(ValueError):
        solve_postplant_centering(
            kill_order_bonuses=[100.0, 100.0],
            ramp_factors=[1.2, 1.2],
            new_factors=[1.0, 1.0],
            supported=[True, True],
            traded_factors=[1.0, 1.0],
            death_ramp_factors=[1.2],
        )
