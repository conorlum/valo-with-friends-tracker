"""T05: complete windows, not consecutive-win counters or match outcomes."""
import pytest
from app.services.round_streak_stats import compute_round_streak_stats, build_round_streak_stats
from tests.support.stats import match


def test_hand_counted_sequence_includes_losses_inside_longer_windows():
    # AABAA: A starts at 1,2,4,5. For k=1: WW,WL,WW -> 2/3;
    # k=2: AB,BA -> 0/2; k=3: ABA,BAA -> 0/2; k=4: ABAA -> 0/1.
    data = compute_round_streak_stats([match("AABAA", score=(4, 4))], {1, 2})
    assert data["group"] == {"1": {"total": 3, "win": 2}, "2": {"total": 2, "win": 0},
                             "3": {"total": 2, "win": 0}, "4": {"total": 1, "win": 0}}
    # B's start at round 3 adds one losing k=1 and one losing k=2 window.
    assert data["all"]["1"] == {"total": 4, "win": 2}
    assert data["all"]["2"] == {"total": 3, "win": 0}


@pytest.mark.parametrize("start", [12, 24, 25, 26])
def test_no_window_crosses_a_side_swap(start):
    assert compute_round_streak_stats([match("AA", start=start)], {1}) == {"group": {}, "all": {}}


@pytest.mark.parametrize("missing", [False, True])
def test_unknown_or_missing_followup_does_not_become_a_loss(missing):
    m = match("A?A")
    if missing: m.rounds.pop(1)
    assert compute_round_streak_stats([m], {1}) == {"group": {}, "all": {}}


@pytest.mark.parametrize("group,total", [({99}, 0), ({3, 4}, 0), ({1}, 1), ({1, 2}, 1), ({1, 3}, 1)])
def test_group_count_is_per_winning_team(group, total):
    assert compute_round_streak_stats([match("AA")], group)["group"].get("1", {"total": 0})["total"] == total


def test_builder_omits_empty_buckets_and_keeps_length_order():
    rows = build_round_streak_stats({"3": {"total": 2, "win": 1}, "1": {"total": 4, "win": 3},
                                    "2": {"total": 0, "win": 0}}).rows
    assert [(r.next_n, r.win_pct) for r in rows] == [(1, .75), (3, .5)]
    assert build_round_streak_stats({}).rows == []
