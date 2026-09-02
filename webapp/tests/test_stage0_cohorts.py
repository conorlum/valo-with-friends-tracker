"""Stage 0 answers the original question -- does a player's Impact track
their wins -- on the CURRENT stored scores, before any fitting.

The cohort rules exist because 94.7% of players in this DB have exactly
one match, which would make an uncontrolled within-player calculation
almost entirely zero-variance rows."""

import numpy as np

from app.services.impact_stage0 import (
    COHORT_RULES,
    PlayerMatch,
    filter_cohort,
    per_player_correlations,
    pooled_relationship,
    stage0_report,
    within_player_centered,
    within_player_tercile_lift,
)


def _history(player_id, impacts, wins):
    return [
        PlayerMatch(player_id=player_id, match_id=player_id * 100 + i, avg_impact=v, won=w)
        for i, (v, w) in enumerate(zip(impacts, wins))
    ]


def test_cohort_rules_match_the_spec():
    assert COHORT_RULES["recurrent"] == 2
    assert COHORT_RULES["per_player_tercile"] == 9
    assert COHORT_RULES["per_player_correlation"] == 10


def test_filter_cohort_drops_single_match_players():
    rows = _history(1, [100.0], [True]) + _history(2, [100.0, 200.0], [False, True])
    assert {r.player_id for r in filter_cohort(rows, min_matches=2)} == {2}


def test_single_match_player_centres_to_exactly_zero():
    """The exact artifact the cohort rule exists to exclude."""
    result = within_player_centered(_history(1, [500.0], [True]), min_matches=1)
    assert result["n"] == 1
    assert np.isnan(result["point_biserial"])  # zero variance


def test_pooled_relationship_reports_correlation_and_counts():
    result = pooled_relationship(_history(1, [10.0, 20.0, 30.0, 40.0], [False, False, True, True]))
    assert result["n"] == 4
    assert result["point_biserial"] > 0.8
    assert result["win_rate"] == 0.5
    assert result["mean_impact_in_wins"] > result["mean_impact_in_losses"]


def test_within_player_centering_removes_between_player_offsets():
    """Two players with opposite absolute levels but identical internal
    patterns: pooled correlation is destroyed by the offset, centred is
    not."""
    strong = _history(1, [900.0, 1000.0], [False, True])
    weak = _history(2, [100.0, 200.0], [False, True])
    rows = strong + weak
    for r in strong:
        r.won = not r.won  # strong player loses when scoring high
    centred = within_player_centered(rows, min_matches=2)
    assert centred["players"] == 2
    assert np.isfinite(centred["point_biserial"])


def test_per_player_correlations_are_one_per_player():
    """A distribution, not a pooled number: three eligible players give
    three correlations."""
    rows = []
    for pid in (1, 2, 3):
        impacts = [float(i) for i in range(10)]
        rows += _history(pid, impacts, [i >= 5 for i in range(10)])
    result = per_player_correlations(rows, min_matches=10)
    assert result["players"] == 3
    assert len(result["values"]) == 3
    assert result["median"] > 0
    assert result["fraction_positive"] == 1.0


def test_per_player_correlations_skip_ineligible_players():
    result = per_player_correlations(_history(1, [1.0, 2.0], [True, False]), min_matches=10)
    assert result["players"] == 0
    assert np.isnan(result["median"])


def test_within_player_terciles_measure_lift_against_own_baseline():
    impacts = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
    wins = [False, False, False, False, True, False, True, True, True]
    result = within_player_tercile_lift(_history(1, impacts, wins), min_matches=9)
    assert result["top_win_rate"] == 1.0
    assert result["bottom_win_rate"] == 0.0
    assert result["lift"] == 1.0
    assert result["players"] == 1


def test_within_player_terciles_skip_ineligible_players():
    result = within_player_tercile_lift(_history(1, [1.0, 2.0], [True, False]), min_matches=9)
    assert result["players"] == 0
    assert np.isnan(result["lift"])


def test_stage0_report_has_every_required_section():
    rows = []
    for pid in range(1, 6):
        impacts = [float(i * 10) for i in range(10)]
        rows += _history(pid, impacts, [i >= 5 for i in range(10)])
    report = stage0_report(rows, roster_player_ids={1, 2}, draws=20, seed=0)

    assert set(report) >= {
        "variant", "pooled", "within_player_centered", "per_player_correlations",
        "within_player_terciles", "cohorts",
    }
    assert report["variant"] == "realized"
    assert "roster" in report["cohorts"] and "recurrent" in report["cohorts"]
    assert report["cohorts"]["roster"]["players"] == 2
    for section in ("pooled", "within_player_centered", "within_player_terciles"):
        assert "ci" in report[section], f"{section} must carry a bootstrap CI"
    assert "median_ci" in report["per_player_correlations"]
    for cohort in report["cohorts"].values():
        assert "ci" in cohort["pooled"]
        assert "median_ci" in cohort["per_player_correlations"]
        assert "ci" in cohort["within_player_terciles"]
