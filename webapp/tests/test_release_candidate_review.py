"""The review script's script-local pre-plant centring patch.

impact.py multiplies by the RAW Part 3 curve today -- c = 0.900537 is solved
and recorded but not wired in. The review script applies it itself so the
comparison shows what would SHIP. That patch embeds the boundary rule, and
getting it wrong silently corrupts every number the review prints.
"""

import pytest

from app.scoring.preplant_empirical_factor import empirical_preplant_factor
from scripts.release_candidate_review import PREPLANT_C, _CentredPreplant

CENTRED = _CentredPreplant(empirical_preplant_factor, PREPLANT_C)


def test_a_scored_kill_is_multiplied_by_c():
    raw = empirical_preplant_factor(12.0, True, strength=3.0)
    assert CENTRED(12.0, True, strength=3.0) == pytest.approx(PREPLANT_C * raw)


def test_a_fallback_kill_beyond_the_cutoff_keeps_exactly_one():
    # The decided policy: c applies only inside the scored region, and dt>30
    # kills pay exactly what they pay today.
    assert CENTRED(45.0, True, strength=3.0) == 1.0
    assert CENTRED(45.0, False, strength=3.0) == 1.0


def test_a_kill_with_no_plant_keeps_exactly_one():
    assert CENTRED(None, True, strength=3.0) == 1.0


def test_forward_mode_stays_neutral_and_is_not_scaled_by_c():
    # use_realized=False is the leakage gate: the curve returns exactly 1.0
    # and nothing may scale it, or an ex-ante replay would silently carry a
    # retrospective constant.
    assert CENTRED(12.0, True, strength=3.0, use_realized=False) == 1.0


def test_the_cutoff_itself_is_scored():
    raw = empirical_preplant_factor(30.0, False, strength=3.0)
    assert CENTRED(30.0, False, strength=3.0) == pytest.approx(PREPLANT_C * raw)


def test_the_patch_is_undone_by_its_own_undo_callable():
    import app.scoring.impact as impact_module
    from scripts.release_candidate_review import apply_preplant_centering

    original = impact_module.empirical_preplant_factor
    undo = apply_preplant_centering()
    assert impact_module.empirical_preplant_factor is not original
    undo()
    assert impact_module.empirical_preplant_factor is original
