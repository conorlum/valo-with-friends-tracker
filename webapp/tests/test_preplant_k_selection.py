"""The declaration's selection rule (2026-09-07-predeclared-values.md,
'The k / FLOOR / CEIL / W grid decision'): deterministic, tie-breaks toward
the smaller k, three branches. dt is seconds_to_plant -- POSITIVE before
the plant, so 'near the plant' is a SMALL dt."""

import pytest

from app.scoring.preplant_k_selection import K_GRID, raw_scalar, select_k
from app.scoring.preplant_time_model import PreplantFit, PreplantKillObservation


def _fit(intercept_atk, slope_atk, intercept_def=0.0, slope_def=0.0, shape_mid_ratio=1.0):
    return PreplantFit(
        intercept_atk=intercept_atk, slope_atk=slope_atk,
        intercept_def=intercept_def, slope_def=slope_def,
        shape_mid_ratio=shape_mid_ratio,
        state_effects={}, include_side_interaction=True, n_observations=0,
    )


def _obs(dt, adv=0, is_attacker=True):
    return PreplantKillObservation(
        match_id=0, round_id=0, dt=dt, adv=adv, is_attacker=is_attacker,
        exact_state="5v5", round_won_by_killer_team=True,
    )


def test_grid_matches_the_declared_eleven_members():
    assert K_GRID == (0.2, 0.27, 0.35, 0.45, 0.6, 0.8, 1.0, 1.3, 1.7, 2.2, 2.8)


def test_crossings_counted_on_the_raw_not_clamped_scalar():
    """The single easiest mistake per the declaration: testing the CLAMPED
    scalar against 0.2/1.7 reports 0.00% at every k. raw_scalar must NOT
    clamp, even with an amplitude large enough to exceed 1.7."""
    fit = _fit(intercept_atk=5.0, slope_atk=5.0)
    obs = _obs(dt=2.0, adv=2, is_attacker=True)  # near plant, adv=2 -> large lift
    raw = raw_scalar(fit, k=2.8, obs=obs)
    assert raw > 1.7  # NOT clamped


def test_branch_3_is_the_smallest_k_when_floor_constraint_never_clears():
    # lift = intercept + slope*adv = -5 + 5*(-3) = -20 at adv=-3 (clamped):
    # a large NEGATIVE lift, floods the floor at every grid member.
    fit = _fit(intercept_atk=-5.0, slope_atk=5.0, intercept_def=-5.0, slope_def=5.0)
    obs = [_obs(dt=2.0, adv=-3, is_attacker=True)] * 100
    result = select_k(fit, obs, total_kills_denominator=100)

    assert result.branch == 3
    assert result.selected_k == K_GRID[0]


def test_ties_break_toward_the_smaller_k():
    # Zero amplitude: every k gives 0% at both clamps -- the floor
    # constraint clears (0% < 1.5%) but the ceiling never reaches 2%, so
    # this exercises branch 2 (the |ceiling - 3%| minimiser) with every k
    # tied at 0%: smallest wins.
    fit = _fit(intercept_atk=0.0, slope_atk=0.0)
    obs = [_obs(dt=2.0, adv=0, is_attacker=True)] * 50
    result = select_k(fit, obs, total_kills_denominator=1000)

    assert result.branch == 2
    assert result.selected_k == K_GRID[0]


def test_branch_1_picks_the_member_closest_to_3_percent_ceiling():
    fit = _fit(intercept_atk=0.9, slope_atk=0.9)
    # 30 near-plant, adv=1, attacker kills (large positive lift -> some k's
    # cross the ceiling); 970 defender, adv=0 kills that never cross either
    # clamp, giving the denominator room for a 2-4% ceiling rate to exist.
    obs = [_obs(dt=2.0, adv=1, is_attacker=True) for _ in range(30)]
    obs += [_obs(dt=2.0, adv=0, is_attacker=False) for _ in range(970)]
    result = select_k(fit, obs, total_kills_denominator=len(obs))

    assert result.branch in (1, 2)  # depends on whether any k lands in [2,4]%
    row = next(r for r in result.table if r["k"] == result.selected_k)
    if result.branch == 1:
        assert 2.0 <= row["ceiling_rate_all"] <= 4.0
    assert row["floor_rate_all"] < 1.5
    # Selected k must be the closest-to-3% among branch-1 qualifiers, or the
    # closest-to-3% among all floor-qualifying members otherwise -- either
    # way, no OTHER floor-qualifying k may be strictly closer to 3% (or, on
    # a tie, smaller) than the one selected.
    floor_ok = [r for r in result.table if r["floor_rate_all"] < 1.5]
    best = min(floor_ok, key=lambda r: (abs(r["ceiling_rate_all"] - 3.0), r["k"]))
    assert result.selected_k == best["k"]


def test_selected_k_is_always_a_member_of_the_grid():
    fit = _fit(intercept_atk=0.5, slope_atk=0.3)
    obs = [_obs(dt=2.0, adv=1, is_attacker=True)] * 100
    result = select_k(fit, obs, total_kills_denominator=1000)
    assert result.selected_k in K_GRID


def test_table_has_one_row_per_grid_member():
    fit = _fit(intercept_atk=0.0, slope_atk=0.0)
    result = select_k(fit, [_obs(2.0)], total_kills_denominator=10)
    assert len(result.table) == len(K_GRID)
    assert [row["k"] for row in result.table] == list(K_GRID)
