"""The head block of scripts/fit_preplant_time_factor.py: the Part 3
kill-side centring gate for the SHIPPED empirical curve. It has to print the
declared population, its scored/fallback split, c, |c-1|, the effective
bounds and the death-side residual against its tolerance -- those are what
the activation decision is read from, so a silently dropped or misreported
line is the failure this test exists to catch.
"""

import pytest

from app.scoring.preplant_centering import solve_empirical_kill_side_centering
from app.scoring.preplant_time_model import PreplantKillObservation
from scripts.fit_preplant_time_factor import _report_empirical_centering

# The shipped strength, which _report_empirical_centering reads from
# impact.py rather than copying. Asserted so a change there is visible here.
STRENGTH = 3.0


def _population():
    observations = [
        PreplantKillObservation(0, 0, 2.5, 0, True, "5v5", True),
        PreplantKillObservation(0, 0, 13.25, 0, False, "4v5", True),
        PreplantKillObservation(0, 0, 27.5, 0, True, "3v4", False),
        PreplantKillObservation(0, 0, 44.0, 0, False, "5v4", True),
    ]
    return observations, [140.0, 210.0, 95.0, 180.0], [1.0, 0.4, 1.0, 0.7]


def test_report_prints_the_shipped_strength_in_its_header(capsys):
    _report_empirical_centering(*_population())
    assert f"strength={STRENGTH}" in capsys.readouterr().out


def test_report_prints_the_declared_population_split(capsys):
    _report_empirical_centering(*_population())
    out = capsys.readouterr().out
    assert "AFFECTED" in out and "SCORED" in out and "FALLBACK" in out
    # 3 of the 4 observations sit inside 0 < dt <= 30.
    assert "AFFECTED          4" in out
    assert "SCORED            3" in out
    assert "FALLBACK          1" in out
    assert "0 < dt <= 30" in out


def test_report_prints_c_and_the_distance_from_one(capsys):
    observations, bonuses, trades = _population()
    expected = solve_empirical_kill_side_centering(
        observations, bonuses, trades, strength=STRENGTH,
    )
    _report_empirical_centering(observations, bonuses, trades)
    out = capsys.readouterr().out
    assert f"c                     = {expected.c:.6f}" in out
    assert f"|c - 1|               = {abs(expected.c - 1):.6f}" in out


def test_report_prints_the_effective_bounds_as_c_times_the_clamp(capsys):
    observations, bonuses, trades = _population()
    expected = solve_empirical_kill_side_centering(
        observations, bonuses, trades, strength=STRENGTH,
    )
    _report_empirical_centering(observations, bonuses, trades)
    low, high = expected.effective_bounds
    assert f"[{low:.6f}, {high:.6f}]" in capsys.readouterr().out


def test_report_prints_the_death_side_residual_against_its_tolerance(capsys):
    observations, bonuses, trades = _population()
    expected = solve_empirical_kill_side_centering(
        observations, bonuses, trades, strength=STRENGTH,
    )
    _report_empirical_centering(observations, bonuses, trades)
    out = capsys.readouterr().out
    assert f"death-side residual   = {expected.death_side_residual:+.4%}" in out
    assert ("WITHIN" if abs(expected.death_side_residual) <= 0.02 else "EXCEEDS") in out
    assert "2% tolerance" in out


def test_report_says_EXCEEDS_when_the_residual_is_outside_the_tolerance(capsys):
    # A traded factor that loads all the death-side weight onto the single
    # most-lifted kill drives the residual past 2%. Verified below against the
    # solver itself before the output is asserted against, so this fixture
    # cannot silently stop producing the relationship it claims to test.
    observations = [
        PreplantKillObservation(0, 0, 20.0, 0, True, "5v5", True),
        PreplantKillObservation(0, 0, 3.0, 0, True, "5v5", True),
    ]
    bonuses = [100.0, 100.0]
    trades = [1.0, 0.05]
    result = solve_empirical_kill_side_centering(
        observations, bonuses, trades, strength=STRENGTH,
    )
    assert abs(result.death_side_residual) > 0.02, "fixture no longer exceeds the tolerance"

    _report_empirical_centering(observations, bonuses, trades)
    out = capsys.readouterr().out
    assert "EXCEEDS" in out
    assert "WITHIN" not in out


def test_report_quantifies_the_dt30_boundary_jump_for_both_sides(capsys):
    # The decided policy keeps the jump, so the report must show its size --
    # this is the number the decision was taken against.
    _report_empirical_centering(*_population())
    out = capsys.readouterr().out
    assert "BOUNDARY" in out
    assert "attacker" in out and "defender" in out
    assert out.count("-> 1.0000") == 2


def test_report_returns_the_same_result_the_solver_gives(capsys):
    observations, bonuses, trades = _population()
    returned = _report_empirical_centering(observations, bonuses, trades)
    capsys.readouterr()
    direct = solve_empirical_kill_side_centering(
        observations, bonuses, trades, strength=STRENGTH,
    )
    assert returned.c == pytest.approx(direct.c)
    assert returned.death_side_residual == pytest.approx(direct.death_side_residual)


def test_report_states_whether_the_clamp_actually_binds_rather_than_asserting_it(capsys):
    # The predeclared 0.2-1.7 clamp is inert at strength 3.0 on the real
    # population (0 of 130,506 events reach either bound). Saying so is only
    # honest if the script COUNTED it -- a hardcoded parenthetical would keep
    # printing "does not bind" on a population where it did.
    _report_empirical_centering(*_population())
    out = capsys.readouterr().out
    assert "clamp binds on 0 of 3" in out


def test_report_shows_a_binding_clamp_when_the_strength_makes_it_bind(capsys, monkeypatch):
    # Verified against the curve before asserting on the output: at this
    # strength the defender curve's far end falls below the 0.2 floor.
    import scripts.fit_preplant_time_factor as script
    from app.scoring.preplant_empirical_factor import empirical_preplant_factor

    strength = 60.0
    # BOTH halves of the fixture are checked: one kill must clamp and the
    # other must not, or "1 of 2" would pass for a counter that counts
    # everything. dt=8.0 attacker was the first attempt and clamps at the
    # CEILING, which is why this is checked and not assumed.
    assert empirical_preplant_factor(29.0, False, strength=strength) == pytest.approx(0.2), (
        "fixture no longer drives the defender curve onto the floor"
    )
    assert 0.2 < empirical_preplant_factor(3.0, True, strength=strength) < 1.7, (
        "fixture's unclamped attacker kill now clamps too"
    )
    monkeypatch.setattr(script, "_PREPLANT_EMPIRICAL_STRENGTH", strength)

    observations = [
        PreplantKillObservation(0, 0, 29.0, 0, False, "5v5", True),
        PreplantKillObservation(0, 0, 3.0, 0, True, "5v5", True),
    ]
    script._report_empirical_centering(observations, [100.0, 100.0], [1.0, 1.0])
    out = capsys.readouterr().out
    assert "clamp binds on 1 of 2" in out


def test_report_prints_the_realised_post_centring_factor_range(capsys):
    observations, bonuses, trades = _population()
    expected = solve_empirical_kill_side_centering(
        observations, bonuses, trades, strength=STRENGTH,
    )
    from app.scoring.preplant_empirical_factor import empirical_preplant_factor
    scored = [
        expected.c * empirical_preplant_factor(o.dt, o.is_attacker, strength=STRENGTH)
        for o in observations if 0 < o.dt <= 30
    ]
    _report_empirical_centering(observations, bonuses, trades)
    out = capsys.readouterr().out
    assert f"realised range        = [{min(scored):.6f}, {max(scored):.6f}]" in out
