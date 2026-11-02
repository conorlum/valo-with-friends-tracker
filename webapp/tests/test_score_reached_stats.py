"""T05: reached scores are team samples; overtime is a match sample."""
import pytest
from app.services.score_reached_stats import compute_score_reached_stats, build_score_reached_stats
from tests.support.stats import match


def test_regulation_counts_both_teams_only_through_their_reached_score():
    data = compute_score_reached_stats([match("A"*24, score=(13, 11))], {1, 2})
    assert data["all"]["buckets"] == {**{str(n): {"total": 2, "win": 1} for n in range(12)},
                                       "12": {"total": 1, "win": 1}}
    assert data["group"]["buckets"] == {str(n): {"total": 1, "win": 1} for n in range(13)}
    assert data["all"]["ot"] == {"total": 1, "count": 0}


@pytest.mark.parametrize("rounds,ot", [(24, 0), (25, 1), (26, 1), (40, 1)])
@pytest.mark.parametrize("group,total", [({1}, 1), ({1, 2}, 1), ({1, 3, 4}, 1), ({99}, 0)])
def test_overtime_is_one_sample_per_match(rounds, ot, group, total):
    data = compute_score_reached_stats([match("A"*rounds, score=(15, 13))], group)
    assert data["all"]["ot"] == {"total": 1, "count": ot}
    assert data["group"]["ot"] == {"total": total, "count": ot*total}
    assert set(data["all"]["buckets"]) == {str(n) for n in range(13)}


def test_tie_and_unknown_final_scores_are_excluded_and_empty_display_is_safe():
    data = compute_score_reached_stats([match("A"*26, score=(13, 13)), match(score=(None, None))], {1})
    assert data["all"] == {"buckets": {}, "ot": {"total": 0, "count": 0}}
    view = build_score_reached_stats(data["all"])
    assert view.rows == [] and view.ot_pct is None and view.ot_total == 0


def test_builder_percentage_and_order():
    view = build_score_reached_stats({"buckets": {"12": {"total": 2, "win": 1}, "0": {"total": 4, "win": 2}},
                                     "ot": {"total": 4, "count": 1}})
    assert [(r.score, r.total, r.win_pct) for r in view.rows] == [(0, 4, .5), (12, 2, .5)]
    assert view.ot_pct == .25
