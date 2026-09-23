"""plant_window.round_decided: declaration 12's "decided", ported from the
measurement's reference (scripts/postplant_v4_variants.py::round_decided).

Exactly three ways, and only these:
  1. the spike was defused and the kill is at or after the defuse;
  2. a REAL plant (phantoms excluded) and the kill is at or after plant+45,
     whatever the exploded flag says;
  3. no real plant, the outcome is a Time Win, and the kill is after 100s.
"""
import sys
from pathlib import Path

import pytest

from app.models.round import Round
from app.scoring import plant_window
from app.scoring.plant_window import ROUND_SECONDS, SPIKE_SECONDS, round_decided


def _round(**kwargs) -> Round:
    defaults = dict(round_number=5, outcome="Team A Elimination Win", planted=False,
                    plant_time=None, exploded=False, defused=False, defuse_time=None)
    defaults.update(kwargs)
    return Round(**defaults)


def test_constants_match_the_measurement():
    assert ROUND_SECONDS == 100.0
    assert SPIKE_SECONDS == 45.0


# -- rule 1: defuse --------------------------------------------------------------

def test_defuse_boundary_is_inclusive():
    r = _round(planted=True, plant_time=40.0, defused=True, defuse_time=70.0,
               outcome="Team B Defuse Win")
    assert round_decided(r, 69.999) is False
    assert round_decided(r, 70.0) is True
    assert round_decided(r, 71.0) is True


def test_defuse_time_without_defused_flag_does_not_decide():
    r = _round(planted=True, plant_time=40.0, defused=False, defuse_time=70.0)
    assert round_decided(r, 75.0) is False


# -- rule 2: plant + 45 -------------------------------------------------------------

@pytest.mark.parametrize("exploded", [True, False])
def test_plant_plus_45_boundary_with_and_without_exploded_flag(exploded):
    r = _round(planted=True, plant_time=30.0, exploded=exploded,
               outcome="Team A Detonate Win" if exploded else "Team A Elimination Win")
    assert round_decided(r, 74.999) is False
    assert round_decided(r, 75.0) is True
    assert round_decided(r, 90.0) is True


def test_plant_plus_39_is_live():
    r = _round(planted=True, plant_time=30.0, exploded=True, outcome="Team A Detonate Win")
    assert round_decided(r, 69.0) is False


def test_phantom_plant_with_kill_at_101_is_decided_by_rule_3():
    r = _round(planted=True, plant_time=101.5, outcome="Team B Time Win")
    assert plant_window.effective_plant_time(r) is None
    assert round_decided(r, 101.0) is True
    assert round_decided(r, 99.0) is False


def test_elimination_win_with_late_plant_is_not_decided_at_103():
    # 14 corpus rounds carry plant_time > 100 and end in Elimination Wins: a
    # noisy clock, not a decided round. The plant is REAL (not a phantom), so
    # rule 2 governs and plant+45 is far away; rule 3 needs a Time Win.
    r = _round(planted=True, plant_time=101.0, outcome="Team A Elimination Win")
    assert round_decided(r, 103.0) is False


def test_unplanted_elimination_win_at_100_5_is_not_decided():
    r = _round(planted=False, outcome="Team B Elimination Win")
    assert round_decided(r, 100.5) is False


# -- rule 3: time win ---------------------------------------------------------------

def test_unplanted_time_win_boundary_is_exclusive():
    r = _round(planted=False, outcome="Team B Time Win")
    assert round_decided(r, 100.0) is False
    assert round_decided(r, 100.001) is True


def test_surrendered_round_is_never_decided():
    r = _round(planted=False, outcome="Team A Surrendered Win")
    for t in (0.0, 50.0, 100.5, 120.0):
        assert round_decided(r, t) is False


def test_no_outcome_is_never_decided_by_rule_3():
    r = _round(planted=False, outcome=None)
    assert round_decided(r, 120.0) is False


# -- the port agrees with the measurement's reference ------------------------------

def test_agrees_with_the_measurement_reference_on_a_grid():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import postplant_v4_variants as reference

    rounds = [
        _round(planted=True, plant_time=30.0, exploded=True, outcome="Team A Detonate Win"),
        _round(planted=True, plant_time=30.0, outcome="Team A Elimination Win"),
        _round(planted=True, plant_time=40.0, defused=True, defuse_time=70.0,
               outcome="Team B Defuse Win"),
        _round(planted=True, plant_time=101.5, outcome="Team B Time Win"),
        _round(planted=True, plant_time=101.0, outcome="Team A Elimination Win"),
        _round(planted=False, outcome="Team B Time Win"),
        _round(planted=False, outcome="Team A Elimination Win"),
        _round(planted=False, outcome="Team A Surrendered Win"),
    ]
    for r in rounds:
        for tenth in range(0, 1300, 5):
            t = tenth / 10
            assert round_decided(r, t) == reference.round_decided(r, t), (r.outcome, t)
