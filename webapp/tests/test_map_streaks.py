from datetime import datetime
from unittest.mock import MagicMock

import app.services.map_streaks as map_streaks_module
from app.services.map_streaks import (
    Act,
    _act_pools,
    _build_map_streaks,
    _gap_in_window,
    _get_acts_and_pools,
    _map_windows,
    compute_map_streaks,
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


def test_gap_in_window_finds_best_and_trailing_runs():
    window_start = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    matches = [
        ("Bind", datetime.fromisoformat("2026-01-02T00:00:00+00:00")),
        ("Ascent", datetime.fromisoformat("2026-01-03T00:00:00+00:00")),
        ("Haven", datetime.fromisoformat("2026-01-04T00:00:00+00:00")),
        ("Sunset", datetime.fromisoformat("2026-01-05T00:00:00+00:00")),
        ("Bind", datetime.fromisoformat("2026-01-06T00:00:00+00:00")),
        ("Ascent", datetime.fromisoformat("2026-01-07T00:00:00+00:00")),
    ]

    best, trailing = _gap_in_window(window_start, None, "Bind", matches)

    assert best == 3  # Ascent, Haven, Sunset between the two Bind matches
    assert trailing == 1  # only Ascent played since the second (most recent) Bind


def test_gap_in_window_trailing_equals_best_when_map_never_replayed():
    window_start = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    matches = [
        ("Bind", datetime.fromisoformat("2026-01-02T00:00:00+00:00")),
        ("Ascent", datetime.fromisoformat("2026-01-03T00:00:00+00:00")),
        ("Haven", datetime.fromisoformat("2026-01-04T00:00:00+00:00")),
    ]

    best, trailing = _gap_in_window(window_start, None, "Bind", matches)

    assert best == 2
    assert trailing == 2


def test_gap_in_window_never_played_map_counts_whole_window():
    window_start = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    matches = [
        ("Ascent", datetime.fromisoformat("2026-01-03T00:00:00+00:00")),
        ("Haven", datetime.fromisoformat("2026-01-04T00:00:00+00:00")),
    ]

    best, trailing = _gap_in_window(window_start, None, "Bind", matches)

    assert best == 2
    assert trailing == 2


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

    best, trailing = _gap_in_window(window_start, window_end, "Bind", matches)

    assert best == 1
    assert trailing == 1


def test_build_map_streaks_merged_window_no_reset_at_act_boundary():
    acts = make_acts([("A1", "2026-01-01T00:00:00+00:00"), ("A2", "2026-03-01T00:00:00+00:00")])
    act_pools = [{"Bind"}, {"Bind"}]
    player_matches = [
        ("Bind", datetime.fromisoformat("2026-01-05T00:00:00+00:00")),
        ("Haven", datetime.fromisoformat("2026-01-10T00:00:00+00:00")),  # A1
        ("Haven", datetime.fromisoformat("2026-03-05T00:00:00+00:00")),  # A2 -- must chain onto the A1 gap
    ]

    streaks = _build_map_streaks(acts, act_pools, player_matches)

    bind = next(s for s in streaks if s.map_name == "Bind")
    assert bind.current.gap == 2  # both Haven matches, spanning the act boundary
    assert bind.current.act_labels == ["A1", "A2"]
    assert bind.current.ongoing is True


def test_build_map_streaks_disjoint_windows_record_does_not_bridge():
    acts = make_acts(
        [
            ("A1", "2026-01-01T00:00:00+00:00"),
            ("A2", "2026-03-01T00:00:00+00:00"),
            ("A3", "2026-05-01T00:00:00+00:00"),
        ]
    )
    act_pools = [{"Bind"}, {"Ascent"}, {"Bind"}]  # Bind drops out for A2, returns in A3
    player_matches = [
        ("Haven", datetime.fromisoformat("2026-01-05T00:00:00+00:00")),
        ("Haven", datetime.fromisoformat("2026-01-10T00:00:00+00:00")),
        ("Haven", datetime.fromisoformat("2026-01-15T00:00:00+00:00")),  # 3-match gap in A1 window
        ("Ascent", datetime.fromisoformat("2026-03-05T00:00:00+00:00")),  # A2 -- Bind not in pool, no window
        ("Haven", datetime.fromisoformat("2026-05-05T00:00:00+00:00")),  # A3 window starts fresh
    ]

    streaks = _build_map_streaks(acts, act_pools, player_matches)

    bind = next(s for s in streaks if s.map_name == "Bind")
    assert bind.record.gap == 3
    assert bind.record.act_labels == ["A1"]
    assert bind.current.gap == 1  # only the A3 window counts as "current"
    assert bind.current.act_labels == ["A3"]
    assert bind.record_is_current is False


def test_build_map_streaks_record_equals_current_when_same_window():
    acts = make_acts([("A1", "2026-01-01T00:00:00+00:00")])
    act_pools = [{"Bind"}]
    player_matches = [("Haven", datetime.fromisoformat("2026-01-05T00:00:00+00:00"))]

    streaks = _build_map_streaks(acts, act_pools, player_matches)

    bind = next(s for s in streaks if s.map_name == "Bind")
    assert bind.record_is_current is True
    assert bind.record.gap == bind.current.gap == 1
    assert bind.current.ongoing is True
    assert bind.record.ongoing is True


def test_build_map_streaks_omits_map_with_no_current_window_matches():
    acts = make_acts([("A1", "2026-01-01T00:00:00+00:00")])
    act_pools = [{"Bind"}]
    player_matches: list[tuple[str, datetime]] = []  # player has no matches at all this act

    streaks = _build_map_streaks(acts, act_pools, player_matches)

    assert streaks == []


def test_build_map_streaks_only_covers_current_pool_maps():
    acts = make_acts([("A1", "2026-01-01T00:00:00+00:00"), ("A2", "2026-03-01T00:00:00+00:00")])
    act_pools = [{"Bind"}, {"Ascent"}]  # Bind was in A1's pool but not A2's (current) pool
    player_matches = [("Ascent", datetime.fromisoformat("2026-03-05T00:00:00+00:00"))]

    streaks = _build_map_streaks(acts, act_pools, player_matches)

    assert [s.map_name for s in streaks] == ["Ascent"]  # Bind not in current pool -- not shown at all


def test_build_map_streaks_current_gap_is_trailing_not_historical_max():
    acts = make_acts([("A1", "2026-01-01T00:00:00+00:00")])
    act_pools = [{"Bind"}]
    player_matches = [
        ("Haven", datetime.fromisoformat("2026-01-02T00:00:00+00:00")),
        ("Ascent", datetime.fromisoformat("2026-01-03T00:00:00+00:00")),
        ("Sunset", datetime.fromisoformat("2026-01-04T00:00:00+00:00")),  # best run of 3 without Bind
        ("Bind", datetime.fromisoformat("2026-01-05T00:00:00+00:00")),   # closes that run
        ("Haven", datetime.fromisoformat("2026-01-06T00:00:00+00:00")),  # live trailing run of 1
    ]

    streaks = _build_map_streaks(acts, act_pools, player_matches)

    bind = next(s for s in streaks if s.map_name == "Bind")
    assert bind.current.gap == 1  # the live streak since the last Bind play, NOT the closed run of 3
    assert bind.record.gap == 3  # the all-time record is still the closed run of 3
    assert bind.record_is_current is False  # record's window IS "current" but its value differs from the live streak
    assert bind.record.ongoing is False  # that record run has already been closed off by a later Bind play


# ---------------------------------------------------------------------------
# Step 4 (docs/player_page_render_speed.txt): _get_acts_and_pools memoizes
# the population-wide act-pools computation at process level -- these tests
# never touch _build_map_streaks/_act_pools' own correctness (covered above),
# just the caching behavior: a hit issues zero queries, a TTL expiry or a
# season_acts.json mtime change forces a recompute.
# ---------------------------------------------------------------------------

def _mock_db_with_population_query(rows) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = rows
    return db


def setup_function(_fn):
    # Each test starts from a clean slate -- the cache is process-global.
    map_streaks_module._act_pools_cache = None


def test_first_call_populates_cache_and_queries_the_population(monkeypatch):
    monkeypatch.setattr(map_streaks_module, "_load_acts", lambda: [Act(label="A1", start=datetime(2026, 1, 1), end=None)])
    monkeypatch.setattr(map_streaks_module, "_season_acts_mtime", lambda: 1.0)
    monkeypatch.setattr(map_streaks_module, "time", MagicMock(monotonic=lambda: 1000.0))
    db = _mock_db_with_population_query([("Bind", datetime(2026, 1, 2)), ("Bind", datetime(2026, 1, 3)), ("Bind", datetime(2026, 1, 4))])

    acts, act_pools = _get_acts_and_pools(db)

    assert len(acts) == 1
    db.query.assert_called_once()
    assert map_streaks_module._act_pools_cache is not None


def test_second_call_within_ttl_issues_no_query(monkeypatch):
    monkeypatch.setattr(map_streaks_module, "_load_acts", lambda: [Act(label="A1", start=datetime(2026, 1, 1), end=None)])
    monkeypatch.setattr(map_streaks_module, "_season_acts_mtime", lambda: 1.0)
    fake_time = MagicMock(monotonic=lambda: 1000.0)
    monkeypatch.setattr(map_streaks_module, "time", fake_time)
    db1 = _mock_db_with_population_query([])
    _get_acts_and_pools(db1)
    assert db1.query.call_count == 1

    db2 = _mock_db_with_population_query([])
    fake_time.monotonic = lambda: 1010.0  # 10s later -- well within the 300s TTL
    acts, act_pools = _get_acts_and_pools(db2)

    db2.query.assert_not_called()


def test_call_after_ttl_expires_recomputes(monkeypatch):
    monkeypatch.setattr(map_streaks_module, "_load_acts", lambda: [Act(label="A1", start=datetime(2026, 1, 1), end=None)])
    monkeypatch.setattr(map_streaks_module, "_season_acts_mtime", lambda: 1.0)
    fake_time = MagicMock(monotonic=lambda: 1000.0)
    monkeypatch.setattr(map_streaks_module, "time", fake_time)
    db1 = _mock_db_with_population_query([])
    _get_acts_and_pools(db1)

    db2 = _mock_db_with_population_query([])
    fake_time.monotonic = lambda: 1000.0 + map_streaks_module._ACT_POOLS_TTL_SECONDS + 1
    _get_acts_and_pools(db2)

    db2.query.assert_called_once()


def test_season_acts_mtime_change_forces_recompute_even_within_ttl(monkeypatch):
    monkeypatch.setattr(map_streaks_module, "_load_acts", lambda: [Act(label="A1", start=datetime(2026, 1, 1), end=None)])
    fake_mtime = MagicMock(return_value=1.0)
    monkeypatch.setattr(map_streaks_module, "_season_acts_mtime", fake_mtime)
    fake_time = MagicMock(monotonic=lambda: 1000.0)
    monkeypatch.setattr(map_streaks_module, "time", fake_time)
    db1 = _mock_db_with_population_query([])
    _get_acts_and_pools(db1)

    fake_mtime.return_value = 2.0  # file edited -- must be picked up immediately, not after the TTL
    db2 = _mock_db_with_population_query([])
    fake_time.monotonic = lambda: 1001.0  # still well within the TTL
    _get_acts_and_pools(db2)

    db2.query.assert_called_once()


def test_no_acts_short_circuits_without_a_population_query(monkeypatch):
    monkeypatch.setattr(map_streaks_module, "_load_acts", lambda: [])
    monkeypatch.setattr(map_streaks_module, "_season_acts_mtime", lambda: 1.0)
    db = MagicMock()

    acts, act_pools = _get_acts_and_pools(db)

    assert acts == []
    assert act_pools == []
    db.query.assert_not_called()


def test_compute_map_streaks_reuses_cached_population_across_two_players(monkeypatch):
    monkeypatch.setattr(map_streaks_module, "_load_acts", lambda: [Act(label="A1", start=datetime(2026, 1, 1), end=None)])
    monkeypatch.setattr(map_streaks_module, "_season_acts_mtime", lambda: 1.0)
    monkeypatch.setattr(map_streaks_module, "time", MagicMock(monotonic=lambda: 1000.0))

    call_log = []

    def fake_query(*args):
        call_log.append(args)
        q = MagicMock()
        # Population query: .filter(...).all(); player query: .join(...).filter(...).order_by(...).all()
        q.filter.return_value.all.return_value = []
        q.join.return_value.filter.return_value.order_by.return_value.all.return_value = []
        return q

    db = MagicMock()
    db.query.side_effect = fake_query

    compute_map_streaks(db, player_id=1)
    compute_map_streaks(db, player_id=2)

    # 1 population query (memoized across both calls) + 2 per-player queries.
    assert db.query.call_count == 3
