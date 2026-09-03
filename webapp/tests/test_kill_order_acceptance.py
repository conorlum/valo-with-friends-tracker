"""End-to-end: the full report is produced, complete, and free of
placeholders. This is the test that would have caught the plan shipping
fifteen tasks that could not produce their own product."""

import numpy as np
import pytest

from app.services.kill_order_leverage import StateVisitRow
from app.services.kill_order_refit import REPORT_SECTIONS, build_full_report
from tests.test_kill_order_refit import leverage_for, synthetic_observations


def _synthetic_state_visits(seed=99):
    """Enough StateVisitRow data for every lattice state -- and every
    state's successor -- to have a determinate win rate, so
    estimate_swing_table's dP is fully populated. leverage_for/
    synthetic_observations build no real round-by-round state walk, so
    Stage C0 (which needs a usable swing table, not an empty one) has
    nothing to work from unless this is supplied. A first draft of this
    fixture passed state_visits=[], which _finite_dp correctly refuses to
    turn into a graph -- it does not impute a state's dP, by design (a
    genuine gap should raise, not be quietly invented)."""
    rng = np.random.default_rng(seed)
    rows = []
    round_id = 0
    for own in range(0, 6):
        for opp in range(0, 6):
            if own == 0 and opp == 0:
                continue
            # Win probability rises with (own - opp): a monotone
            # placeholder curve, not a claim about real round dynamics.
            p_win = 1.0 / (1.0 + np.exp(-(own - opp)))
            for _ in range(60):
                round_id += 1
                won = bool(rng.uniform() < p_win)
                rows.append(StateVisitRow(match_id=round_id % 80, round_id=round_id,
                                          own=own, opp=opp, won=won))
    return rows


@pytest.fixture(scope="module")
def report():
    observations = synthetic_observations(matches=80)
    return build_full_report(leverage_for(observations), observations,
                             player_rows=[], state_visits=_synthetic_state_visits(),
                             draws=20, l2_grid=[1.0])


def test_every_declared_section_is_populated(report):
    for section in REPORT_SECTIONS:
        assert report.get(section) is not None, f"{section} was never filled in"


def test_all_four_primary_comparisons_exist(report):
    assert set(report["verdicts"]["primaries"]) == {"P1", "P2", "P3", "P4"}
    for entry in report["verdicts"]["primaries"].values():
        assert np.isfinite(entry["delta"])
        assert len(entry["ci"]) == 2


def test_all_four_verdicts_are_computed(report):
    assert set(report["verdicts"]["verdicts"]) == {"A1", "A2", "B", "C"}
    for verdict in report["verdicts"]["verdicts"].values():
        assert isinstance(verdict["helped"], bool)


def test_no_verdict_input_is_a_placeholder(report):
    """The CLI once passed max_component_correlation=1.0 and
    beats_kill_diff_t1=False as constants. A verdict computed from a
    hardcoded input is worse than no verdict."""
    inputs = report["verdicts"]["inputs"]
    assert inputs["max_component_correlation"] != 1.0
    assert inputs["source"]["max_component_correlation"] == "component_correlations"
    assert inputs["source"]["beats_kill_diff_t1"] == "yardstick_matrix"
    assert inputs["source"]["targets_agree"] == "target_agreement"


def test_the_report_is_json_serializable(report):
    """NumPy arrays, NaNs and dataclasses all break json.dump silently or
    loudly; the CLI writes this file and it must survive the round trip."""
    import json

    from app.services.kill_order_refit import to_jsonable

    text = json.dumps(to_jsonable(report))
    restored = json.loads(text)
    assert set(restored) == set(report)
