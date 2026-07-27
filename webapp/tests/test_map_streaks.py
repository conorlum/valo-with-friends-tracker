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


def test_act_pools_counts_population_matches_per_act_window():
    acts = make_acts([("A1", "2026-01-01T00:00:00+00:00"), ("A2", "2026-03-01T00:00:00+00:00")])
    population_matches = [
        ("Bind", datetime.fromisoformat("2026-01-05T00:00:00+00:00")),
        ("Bind", datetime.fromisoformat("2026-01-10T00:00:00+00:00")),
        ("Bind", datetime.fromisoformat("2026-01-15T00:00:00+00:00")),  # 3rd Bind match in A1 -> meets MIN_POOL_MATCHES
        ("Haven", datetime.fromisoformat("2026-01-20T00:00:00+00:00")),  # only 1 Haven match in A1 -> below threshold
        ("Ascent", datetime.fromisoformat("2026-03-05T00:00:00+00:00")),
        ("Ascent", datetime.fromisoformat("2026-03-06T00:00:00+00:00")),
        ("Ascent", datetime.fromisoformat("2026-03-07T00:00:00+00:00")),  # 3rd Ascent match in A2
    ]

    pools = _act_pools(acts, population_matches)

    assert pools == [{"Bind"}, {"Ascent"}]


def test_act_pools_ignores_matches_outside_all_act_windows():
    acts = make_acts([("A1", "2026-01-01T00:00:00+00:00"), ("A2", "2026-03-01T00:00:00+00:00")])
    population_matches = [
        ("Bind", datetime.fromisoformat("2025-12-01T00:00:00+00:00")),  # before A1 starts
    ] * 5

    pools = _act_pools(acts, population_matches)

    assert pools == [set(), set()]


def test_gap_in_window_finds_longest_run_between_plays():
    window_start = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    matches = [
        ("Bind", datetime.fromisoformat("2026-01-02T00:00:00+00:00")),
        ("Ascent", datetime.fromisoformat("2026-01-03T00:00:00+00:00")),
        ("Haven", datetime.fromisoformat("2026-01-04T00:00:00+00:00")),
        ("Sunset", datetime.fromisoformat("2026-01-05T00:00:00+00:00")),
        ("Bind", datetime.fromisoformat("2026-01-06T00:00:00+00:00")),
        ("Ascent", datetime.fromisoformat("2026-01-07T00:00:00+00:00")),
    ]

    gap, ongoing = _gap_in_window(window_start, None, "Bind", matches)

    assert gap == 3  # Ascent, Haven, Sunset between the two Bind matches
    assert ongoing is False  # the biggest gap already closed (Bind played again after it)


def test_gap_in_window_ongoing_when_trailing_run_is_the_longest():
    window_start = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    matches = [
        ("Bind", datetime.fromisoformat("2026-01-02T00:00:00+00:00")),
        ("Ascent", datetime.fromisoformat("2026-01-03T00:00:00+00:00")),
        ("Haven", datetime.fromisoformat("2026-01-04T00:00:00+00:00")),
    ]

    gap, ongoing = _gap_in_window(window_start, None, "Bind", matches)

    assert gap == 2
    assert ongoing is True


def test_gap_in_window_never_played_map_counts_whole_window():
    window_start = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    matches = [
        ("Ascent", datetime.fromisoformat("2026-01-03T00:00:00+00:00")),
        ("Haven", datetime.fromisoformat("2026-01-04T00:00:00+00:00")),
    ]

    gap, ongoing = _gap_in_window(window_start, None, "Bind", matches)

    assert gap == 2
    assert ongoing is True


def test_gap_in_window_no_matches_in_range_returns_none():
    window_start = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    window_end = datetime.fromisoformat("2026-02-01T00:00:00+00:00")
    matches = [("Ascent", datetime.fromisoformat("2026-03-01T00:00:00+00:00"))]

    assert _gap_in_window(window_start, window_end, "Bind", matches) is None


def test_gap_in_window_respects_closed_window_end():
    window_start = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    window_end = datetime.fromisoformat("2026-02-01T00:00:00+00:00")
    matches = [
        ("Ascent", datetime.fromisoformat("2026-01-15T00:00:00+00:00")),
        ("Bind", datetime.fromisoformat("2026-03-01T00:00:00+00:00")),  # after window_end -- excluded
    ]

    gap, ongoing = _gap_in_window(window_start, window_end, "Bind", matches)

    assert gap == 1
    assert ongoing is False  # closed window (end is not None) is never "ongoing"
