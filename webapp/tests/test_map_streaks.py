from datetime import datetime, timezone

from app.services.map_streaks import (
    Act,
    MapStreakWindow,
    _act_pools,
    _build_map_streaks,
    _gap_in_window,
    _map_windows,
)


def make_acts(labels_and_starts):
    """labels_and_starts: list of (label, iso_start). Builds Acts with each
    act's `end` set to the next act's start, last act's `end` = None."""
    acts = []
    for i, (label, start) in enumerate(labels_and_starts):
        start_dt = datetime.fromisoformat(start)
        end_dt = (
            datetime.fromisoformat(labels_and_starts[i + 1][1])
            if i + 1 < len(labels_and_starts)
            else None
        )
        acts.append(Act(label=label, start=start_dt, end=end_dt))
    return acts


def test_map_windows_merges_adjacent_acts_where_map_stays_in_pool():
    acts = make_acts([("A1", "2026-01-01T00:00:00+00:00"), ("A2", "2026-03-01T00:00:00+00:00"), ("A3", "2026-05-01T00:00:00+00:00")])
    act_pools = [{"Bind", "Haven"}, {"Bind", "Ascent"}, {"Bind", "Sunset"}]

    windows = _map_windows("Bind", acts, act_pools)

    assert windows == [(0, 2)]


def test_map_windows_splits_when_map_drops_out_and_returns():
    acts = make_acts([("A1", "2026-01-01T00:00:00+00:00"), ("A2", "2026-03-01T00:00:00+00:00"), ("A3", "2026-05-01T00:00:00+00:00")])
    act_pools = [{"Bind"}, {"Ascent"}, {"Bind"}]

    windows = _map_windows("Bind", acts, act_pools)

    assert windows == [(0, 0), (2, 2)]


def test_map_windows_map_never_in_pool():
    acts = make_acts([("A1", "2026-01-01T00:00:00+00:00")])
    act_pools = [{"Ascent"}]

    assert _map_windows("Bind", acts, act_pools) == []
