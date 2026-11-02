"""T05: halftime is the complete first twelve rounds, counted once per team."""
import pytest
from app.services.halftime_conversion_stats import compute_halftime_conversion_stats, build_halftime_conversion_stats
from tests.support.stats import match


@pytest.mark.parametrize("group,expected", [({1, 2}, {"9": {"total": 1, "win": 1}}),
    ({3}, {"3": {"total": 1, "win": 0}}), ({99}, {}),
    ({1, 3}, {"9": {"total": 1, "win": 1}, "3": {"total": 1, "win": 0}})])
def test_complementary_perspectives_and_no_roster_multiplication(group, expected):
    m = match("A"*9 + "B"*3 + "B"*4)
    data = compute_halftime_conversion_stats([m], group)
    assert data["all"] == {"9": {"total": 1, "win": 1}, "3": {"total": 1, "win": 0}}
    assert data["group"] == expected


@pytest.mark.parametrize("broken", ["missing", "unknown", "tie"])
def test_incomplete_half_or_undecided_match_is_excluded(broken):
    m = match("A"*9+"B"*3, score=(12, 12) if broken == "tie" else (13, 5))
    if broken == "missing": m.rounds.pop(4)
    if broken == "unknown": m.rounds[4].outcome = None
    assert compute_halftime_conversion_stats([m], {1}) == {"group": {}, "all": {}}


def test_future_rounds_do_not_change_the_bucket_and_builder_orders_by_half_score():
    short, long = match("A"*9+"B"*3), match("A"*9+"B"*3+"B"*20)
    assert compute_halftime_conversion_stats([short], set()) == compute_halftime_conversion_stats([long], set())
    rows = build_halftime_conversion_stats({"3": {"total": 2, "win": 1}, "9": {"total": 1, "win": 1},
                                           "0": {"total": 0, "win": 0}}).rows
    assert [(r.own, r.opp, r.win_pct) for r in rows] == [(9, 3, 1.0), (3, 9, .5)]
    assert build_halftime_conversion_stats({}).rows == []
