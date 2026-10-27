"""T05: attack/defence boundaries, sample counts, and significance verdicts."""
import pytest
from app.services.map_side_stats import compute_map_side_stats, build_map_side_stats_from_aggregates, _classify
from tests.support.stats import match


@pytest.mark.parametrize("rn,attacks", [(1, True), (12, True), (13, False), (24, False), (25, True), (26, False)])
@pytest.mark.parametrize("winner", ["A", "B"])
def test_round_boundaries_from_both_winner_perspectives(rn, attacks, winner):
    data = compute_map_side_stats([match(winner, start=rn)], {1, 2, 3})
    attack_wins = int(attacks == (winner == "A"))
    assert data["all"] == data["group"] == {"Bind": {"matches": 1, "attack_wins": attack_wins,
                                                                    "defense_wins": 1-attack_wins}}


def test_outsiders_missing_map_and_unknown_outcomes():
    data = compute_map_side_stats([match("A?B", map_name=None)], {99})
    assert data == {"group": {}, "all": {"Unknown": {"matches": 1, "attack_wins": 1, "defense_wins": 1}}}


@pytest.mark.parametrize("a,d,label", [(59, 41, "Neutral"), (61, 39, "Attacker-sided"),
                                      (41, 59, "Neutral"), (39, 61, "Defender-sided")])
def test_verdict_either_side_of_threshold(a, d, label):
    row, = build_map_side_stats_from_aggregates({"Bind": {"matches": 1, "attack_wins": a, "defense_wins": d}}).rows
    assert row.label == label
    assert row.attack_win_pct == a/(a+d)


@pytest.mark.parametrize("z,label", [(2, "Attacker-sided"), (-2, "Defender-sided"), (1.999, "Neutral"),
                                      (-1.999, "Neutral")])
def test_exact_verdict_boundary(z, label):
    assert _classify(z) == label


def test_sort_uses_significance_not_raw_percentage_and_skips_no_rounds():
    rows = build_map_side_stats_from_aggregates({
        "Small": {"matches": 1, "attack_wins": 7, "defense_wins": 3},
        "Large": {"matches": 100, "attack_wins": 600, "defense_wins": 400},
        "Empty": {"matches": 1, "attack_wins": 0, "defense_wins": 0}}).rows
    assert [r.map_name for r in rows] == ["Large", "Small"]
    assert build_map_side_stats_from_aggregates({}).rows == []
