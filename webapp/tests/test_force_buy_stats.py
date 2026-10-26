"""T05: pistol losers, team-level spend ratio, and independent denominators."""
import pytest
from app.services.force_buy_stats import compute_force_buy_stats, build_force_buy_stats
from tests.support.stats import match, economy


@pytest.mark.parametrize("spent,expected", [(699, 0), (700, 1), (701, 1)])
@pytest.mark.parametrize("start", [1, 13])
def test_threshold_in_each_half(spent, expected, start):
    m = economy(match("AB", start=start), start + 1, {3: (spent, 1000-spent)})
    assert compute_force_buy_stats([m], {3})["group"]["forced"] == {"total": expected, "win": expected}


def test_ratio_is_of_team_sums_not_average_player_ratios():
    m = economy(match("ABAB"), 2, {3: (800, 200), 4: (0, 1), 1: (0, 9000)})
    # Team B spends 800/1001 (>70%), although mean of its two ratios is 40%.
    value = compute_force_buy_stats([m], {3, 4})
    assert value["group"] == value["all"] == {
        "forced": {"total": 1, "win": 1}, "next": {"total": 1, "win": 0},
        "next2": {"total": 1, "win": 1}, "match": {"total": 1, "win": 0}}


@pytest.mark.parametrize("group,total", [({1, 2}, 0), ({99}, 0), ({3}, 1), ({3, 4}, 1), ({1, 3}, 1)])
def test_only_losers_team_counts_once(group, total):
    m = economy(match("AB"), 2, {3: (700, 300)})
    data = compute_force_buy_stats([m], group)
    assert data["group"]["forced"] == {"total": total, "win": total}
    assert data["all"]["forced"] == {"total": 1, "win": 1}


@pytest.mark.parametrize("winners,budgets", [("A", {}), ("?B", {3: (900, 0)}),
                                            ("A?", {3: (900, 0)}), ("AB", {}), ("AB", {3: (0, 0)})])
def test_unknown_or_absent_pistol_followup_or_money_is_not_a_loss(winners, budgets):
    m = match(winners)
    if len(winners) > 1:
        economy(m, 2, budgets)
    assert compute_force_buy_stats([m], {3})["all"]["forced"] == {"total": 0, "win": 0}


def test_short_match_and_tie_keep_independent_denominators():
    m = economy(match("AB", score=(1, 1)), 2, {3: (700, 300)})
    value = compute_force_buy_stats([m], {3})["group"]
    assert value["forced"] == {"total": 1, "win": 1}
    assert all(value[k] == {"total": 0, "win": 0} for k in ("next", "next2", "match"))
    rows = build_force_buy_stats(value)
    assert rows.total_samples == 1
    assert [(r.label, r.total, r.win_pct) for r in rows.rows] == [("Won the forced round", 1, 1.0)]


def test_empty_display():
    view = build_force_buy_stats(compute_force_buy_stats([], set())["all"])
    assert view.rows == [] and view.total_samples == 0
