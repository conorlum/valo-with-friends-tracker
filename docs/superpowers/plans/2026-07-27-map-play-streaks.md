# Map Play Streaks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For every map in the current competitive pool, show a player's current and all-time-record longest stretch (in matches) of not playing that map, on the player detail page.

**Architecture:** A new pure-function algorithm module (`app/services/map_streaks.py`) computes per-act population map pools, finds per-map contiguous "windows" (runs of acts where that map stayed in the pool), and scans a player's own matches within each window for the longest gap. A thin DB-querying wrapper feeds it real data; the router passes the result into the existing player detail template.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Jinja2 (existing stack, no new dependencies). Tests via pytest, following the existing `webapp/tests/test_sessions.py` convention (pure functions tested with plain data, no DB fixtures).

## Global Constraints

- Reuse `MIN_POOL_MATCHES` (3) and `SEASON_ACTS_PATH` from `app/services/map_prediction.py` — do not redefine or change these constants, and do not modify `map_prediction.py`.
- A map's streak may only accumulate within acts where that specific map is in the population-wide pool (`>= MIN_POOL_MATCHES` matches in that act's date window). A streak never bridges an act (or run of acts) where the map was absent from the pool.
- One query for population-wide match data, one query for the target player's own matches — no per-act queries (avoid N+1; see `docs`-adjacent history of N+1 fixes on this codebase, e.g. commits `0eaa078`, `84711d6`, `2bce0bd`).
- No new UI chart — plain HTML table, matching the existing `players/detail.html` table styling (see the "Maps" card, `app/templates/players/detail.html:213-241`).

---

### Task 1: `app/services/map_streaks.py` — act/window/gap algorithm + tests

**Files:**
- Create: `webapp/app/services/map_streaks.py`
- Create: `webapp/tests/test_map_streaks.py`

**Interfaces:**
- Consumes: `MIN_POOL_MATCHES: int`, `SEASON_ACTS_PATH: pathlib.Path` from `app.services.map_prediction`.
- Produces (for Task 2):
  - `@dataclass MapStreakWindow(act_labels: list[str], gap: int, ongoing: bool)`
  - `@dataclass MapStreak(map_name: str, current: MapStreakWindow, record: MapStreakWindow, record_is_current: bool)`
  - `compute_map_streaks(db: sqlalchemy.orm.Session, player_id: int) -> list[MapStreak]`

- [ ] **Step 1: Write failing tests for `_map_windows`**

Create `webapp/tests/test_map_streaks.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_map_streaks.py -v` (from `webapp/`)
Expected: FAIL — `ImportError: cannot import name 'Act' from 'app.services.map_streaks'` (module doesn't exist yet).

- [ ] **Step 3: Implement `Act` and `_map_windows`**

Create `webapp/app/services/map_streaks.py`:

```python
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Match, MatchPlayer
from app.services.map_prediction import MIN_POOL_MATCHES, SEASON_ACTS_PATH


@dataclass
class Act:
    label: str
    start: datetime
    end: datetime | None  # None only for the current (latest) act


@dataclass
class MapStreakWindow:
    act_labels: list[str]
    gap: int
    ongoing: bool


@dataclass
class MapStreak:
    map_name: str
    current: MapStreakWindow
    record: MapStreakWindow
    record_is_current: bool


def _map_windows(map_name: str, acts: list[Act], act_pools: list[set[str]]) -> list[tuple[int, int]]:
    """Contiguous runs of act-indices where `map_name` is in that act's pool.
    Only adjacent acts merge -- a gap where the map drops out starts a new,
    disjoint window."""
    windows: list[tuple[int, int]] = []
    start: int | None = None
    for i, pool in enumerate(act_pools):
        present = map_name in pool
        if present and start is None:
            start = i
        elif not present and start is not None:
            windows.append((start, i - 1))
            start = None
    if start is not None:
        windows.append((start, len(acts) - 1))
    return windows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_map_streaks.py -v`
Expected: 3 tests PASS (the 3 `_map_windows` tests; other imports will still fail at collection since `_act_pools`, `_gap_in_window`, `_build_map_streaks` don't exist yet -- add stub definitions raising `NotImplementedError` for those three names so collection succeeds).

Add to the bottom of `map_streaks.py`. Each stub is replaced by its real implementation later: `_act_pools` in Step 8, `_gap_in_window` in Step 13, `_build_map_streaks` in Step 18.

```python
def _act_pools(*args, **kwargs):
    raise NotImplementedError


def _gap_in_window(*args, **kwargs):
    raise NotImplementedError


def _build_map_streaks(*args, **kwargs):
    raise NotImplementedError
```

- [ ] **Step 5: Commit**

```bash
git add app/services/map_streaks.py tests/test_map_streaks.py
git commit -m "Add _map_windows: per-map contiguous pool-presence windows"
```

- [ ] **Step 6: Write failing tests for `_act_pools`**

Append to `webapp/tests/test_map_streaks.py`:

```python
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
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_map_streaks.py -v`
Expected: the 2 new tests FAIL with `NotImplementedError`.

- [ ] **Step 8: Implement `_act_pools`, replacing its stub**

Replace the `_act_pools` stub in `map_streaks.py`:

```python
def _act_pools(acts: list[Act], population_matches: list[tuple[str, datetime]]) -> list[set[str]]:
    """Population-wide map pool per act -- same MIN_POOL_MATCHES rule as
    map_prediction.get_current_map_pool, applied to every act instead of
    just the latest one."""
    counts_per_act: list[dict[str, int]] = [dict() for _ in acts]
    for map_name, played_at in population_matches:
        if map_name is None or played_at is None:
            continue
        for i, act in enumerate(acts):
            if played_at >= act.start and (act.end is None or played_at < act.end):
                counts_per_act[i][map_name] = counts_per_act[i].get(map_name, 0) + 1
                break
    return [{m for m, c in counts.items() if c >= MIN_POOL_MATCHES} for counts in counts_per_act]
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_map_streaks.py -v`
Expected: all `_map_windows` and `_act_pools` tests PASS.

- [ ] **Step 10: Commit**

```bash
git add app/services/map_streaks.py tests/test_map_streaks.py
git commit -m "Add _act_pools: per-act population map pools"
```

- [ ] **Step 11: Write failing tests for `_gap_in_window`**

Append to `webapp/tests/test_map_streaks.py`:

```python
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
```

- [ ] **Step 12: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_map_streaks.py -v`
Expected: the 5 new tests FAIL with `NotImplementedError` / `TypeError` (stub takes `*args, **kwargs` but callers unpack a 2-tuple from `None`).

- [ ] **Step 13: Implement `_gap_in_window`, replacing its stub**

Replace the `_gap_in_window` stub in `map_streaks.py`:

```python
def _gap_in_window(
    window_start: datetime,
    window_end: datetime | None,
    map_name: str,
    player_matches: list[tuple[str, datetime]],
) -> tuple[int, bool] | None:
    """Longest run of consecutive matches (within [window_start, window_end))
    that aren't `map_name`. Returns None if the player has no matches in
    range at all -- nothing to measure. `ongoing` is True only when the
    window is still open (`window_end is None`) and the longest run found is
    the trailing one (i.e. it's still accruing, not already closed off by a
    later play of the map)."""
    in_window = [
        (m, t) for m, t in player_matches if t >= window_start and (window_end is None or t < window_end)
    ]
    if not in_window:
        return None

    best = 0
    running = 0
    for m, _ in in_window:
        if m == map_name:
            running = 0
        else:
            running += 1
            best = max(best, running)

    ongoing = window_end is None and running == best and best > 0
    return best, ongoing
```

- [ ] **Step 14: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_map_streaks.py -v`
Expected: all `_gap_in_window` tests PASS.

- [ ] **Step 15: Commit**

```bash
git add app/services/map_streaks.py tests/test_map_streaks.py
git commit -m "Add _gap_in_window: longest no-play run within a date window"
```

- [ ] **Step 16: Write failing tests for `_build_map_streaks`**

Append to `webapp/tests/test_map_streaks.py`:

```python
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
```

- [ ] **Step 17: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_map_streaks.py -v`
Expected: the 5 new tests FAIL with `NotImplementedError`.

- [ ] **Step 18: Implement `_build_map_streaks`, replacing its stub**

Replace the `_build_map_streaks` stub in `map_streaks.py`:

```python
def _build_map_streaks(
    acts: list[Act],
    act_pools: list[set[str]],
    player_matches: list[tuple[str, datetime]],
) -> list[MapStreak]:
    if not acts:
        return []

    current_pool = act_pools[-1]
    current_act_index = len(acts) - 1
    streaks: list[MapStreak] = []

    for map_name in sorted(current_pool):
        window_results: list[tuple[int, MapStreakWindow]] = []
        for w_start, w_end in _map_windows(map_name, acts, act_pools):
            outcome = _gap_in_window(acts[w_start].start, acts[w_end].end, map_name, player_matches)
            if outcome is None:
                continue
            gap, ongoing = outcome
            act_labels = [acts[i].label for i in range(w_start, w_end + 1)]
            window_results.append((w_end, MapStreakWindow(act_labels=act_labels, gap=gap, ongoing=ongoing)))

        current_result = next((w for idx, w in window_results if idx == current_act_index), None)
        if current_result is None:
            continue  # no matches yet in the current act -- omit (page-level empty state covers this)

        record_idx, record_result = max(window_results, key=lambda pair: pair[1].gap)
        record_is_current = record_idx == current_act_index and record_result.gap == current_result.gap

        streaks.append(
            MapStreak(
                map_name=map_name,
                current=current_result,
                record=record_result,
                record_is_current=record_is_current,
            )
        )

    return streaks
```

- [ ] **Step 19: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_map_streaks.py -v`
Expected: all tests PASS (15 total).

- [ ] **Step 20: Commit**

```bash
git add app/services/map_streaks.py tests/test_map_streaks.py
git commit -m "Add _build_map_streaks: compose windows+gaps into per-map streak results"
```

- [ ] **Step 21: Add `_load_acts` and the DB-facing `compute_map_streaks` wrapper (no unit test -- DB-querying, verified manually in Task 2)**

Append to `map_streaks.py`:

```python
def _load_acts() -> list[Act]:
    if not SEASON_ACTS_PATH.exists():
        return []
    acts_raw = sorted(
        json.loads(SEASON_ACTS_PATH.read_text(encoding="utf-8")), key=lambda a: a["start_time"]
    )
    acts: list[Act] = []
    for i, a in enumerate(acts_raw):
        start = datetime.fromisoformat(a["start_time"])
        end = datetime.fromisoformat(acts_raw[i + 1]["start_time"]) if i + 1 < len(acts_raw) else None
        acts.append(Act(label=a["label"], start=start, end=end))
    return acts


def compute_map_streaks(db: Session, player_id: int) -> list[MapStreak]:
    """For every map in the current competitive pool, the player's current
    and all-time-record longest run of consecutive matches without playing
    it -- see app/services/map_streaks.py module docstring / design doc at
    docs/superpowers/specs/2026-07-27-map-play-streaks-design.md for the
    per-map windowing rationale (map pool rotates 2-3 maps per act, so
    continuity is checked per map, not per whole pool)."""
    acts = _load_acts()
    if not acts:
        return []

    population_matches = list(
        db.query(Match.map_name, Match.played_at).filter(Match.played_at.isnot(None)).all()
    )
    act_pools = _act_pools(acts, population_matches)

    player_matches = list(
        db.query(Match.map_name, Match.played_at)
        .join(MatchPlayer, MatchPlayer.match_id == Match.id)
        .filter(MatchPlayer.player_id == player_id, Match.played_at.isnot(None))
        .order_by(Match.played_at)
        .all()
    )

    return _build_map_streaks(acts, act_pools, player_matches)
```

- [ ] **Step 22: Run the full test file once more to confirm nothing broke**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_map_streaks.py -v`
Expected: all 15 tests still PASS.

- [ ] **Step 23: Commit**

```bash
git add app/services/map_streaks.py
git commit -m "Add compute_map_streaks: DB-querying wrapper around the pure algorithm"
```

---

### Task 2: Wire into the player detail page

**Files:**
- Modify: `webapp/app/routers/players.py:18-52` (the `player_detail` handler)
- Modify: `webapp/app/templates/players/detail.html:213-241` (insert new card after the existing "Maps" card, before the "Matches" card)

**Interfaces:**
- Consumes: `compute_map_streaks(db: Session, player_id: int) -> list[MapStreak]` from Task 1 (`app.services.map_streaks`), where `MapStreak` has `.map_name: str`, `.current: MapStreakWindow`, `.record: MapStreakWindow`, `.record_is_current: bool`, and `MapStreakWindow` has `.act_labels: list[str]`, `.gap: int`, `.ongoing: bool`.
- Produces: template context key `map_streaks: list[MapStreak]`, available in `players/detail.html`.

- [ ] **Step 1: Add the import and function call to the router**

In `webapp/app/routers/players.py`, add to the imports:

```python
from app.services.map_streaks import compute_map_streaks
```

In `player_detail`, after the existing `round_win_graph, kill_order_graph = build_state_diagrams(db, player)` line, add:

```python
    map_streaks = compute_map_streaks(db, player.id)
```

And add `"map_streaks": map_streaks,` to the template context dict passed to `TemplateResponse`.

The full modified handler:

```python
@router.get("/{display_name}")
def player_detail(request: Request, display_name: str, db: Session = Depends(get_db)):
    player = get_player_or_404(db, display_name)
    profile = get_player_profile(db, player)
    chart_data = {
        "labels": [match_label(m.match) for m in profile.matches],
        "kill_impact": [m.average_kill_impact for m in profile.matches],
        "death_impact": [m.average_death_impact for m in profile.matches],
    }
    highlights_chart_data = {
        "labels": ["Econ", "Clutch / High-Impact", "Post-Plant"],
        "kill": [profile.avg_econ_kill, profile.avg_clutch_kill, profile.avg_post_plant_kill],
        "death": [profile.avg_econ_death, profile.avg_clutch_death, profile.avg_post_plant_death],
    }
    map_chart_data = {
        "labels": [s.key for s in profile.map_stats],
        "kill_impact": [s.average_kill_impact for s in profile.map_stats],
        "death_impact": [s.average_death_impact for s in profile.map_stats],
    }
    round_win_graph, kill_order_graph = build_state_diagrams(db, player)
    top_kill_differentials, top_death_differentials = top_kill_order_differentials(kill_order_graph)
    map_streaks = compute_map_streaks(db, player.id)
    return templates.TemplateResponse(
        request,
        "players/detail.html",
        {
            "profile": profile,
            "chart_data": chart_data,
            "highlights_chart_data": highlights_chart_data,
            "map_chart_data": map_chart_data,
            "round_win_graph": round_win_graph,
            "kill_order_graph": kill_order_graph,
            "top_kill_differentials": top_kill_differentials,
            "top_death_differentials": top_death_differentials,
            "map_streaks": map_streaks,
        },
    )
```

- [ ] **Step 2: Add the template card**

In `webapp/app/templates/players/detail.html`, insert this new card immediately after the closing `</div>` of the "Maps" card (after line 241, before the "Matches" card starting at line 243):

```html
<div class="card">
  <h2>Map streaks</h2>
  <p class="page-meta">
    For each map in the current pool, the longest run of matches played without landing on
    that map -- only counted while the map was actually in the pool (a per-map streak never
    bridges an act where the map was dropped).
  </p>
  {% if map_streaks %}
  <table>
    <thead>
      <tr><th>Map</th><th>Current gap</th><th>Record gap</th></tr>
    </thead>
    <tbody>
      {% for s in map_streaks %}
      <tr>
        <td>{{ s.map_name }}</td>
        <td>
          {{ s.current.gap }} match{{ "es" if s.current.gap != 1 else "" }}
          {% if s.current.ongoing %}<span class="badge badge-score">ongoing</span>{% endif %}
          <div class="stat-sub">{{ s.current.act_labels | join(" &ndash; ") | safe }}</div>
        </td>
        <td>
          {% if s.record_is_current %}
          &ndash;
          {% else %}
          {{ s.record.gap }} match{{ "es" if s.record.gap != 1 else "" }}
          {% if s.record.ongoing %}<span class="badge badge-score">ongoing</span>{% endif %}
          <div class="stat-sub">{{ s.record.act_labels | join(" &ndash; ") | safe }}</div>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="page-meta">No current-act match data yet for {{ profile.player.display_name }}.</p>
  {% endif %}
</div>
```

- [ ] **Step 3: Manual verification**

Start the app locally per `CLAUDE.md`'s "Running the webapp locally" section (Postgres already up, migrations applied):

```
cd webapp
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open `/players/<a display name with match history>` in a browser. Confirm:
- The new "Map streaks" card renders below "Maps" and above "Matches".
- Every map shown is one of the current pool's maps (cross-check against the map names shown on `/map-prediction`'s probability table for the same moment in time).
- A map you know you haven't played in a while shows a plausible non-zero "current gap" with an "ongoing" badge.
- The "Record gap" column shows `–` for rows where the record is the same as the current window, and a real number + act range otherwise.

- [ ] **Step 4: Commit**

```bash
git add app/routers/players.py app/templates/players/detail.html
git commit -m "Show per-map play streaks on the player detail page"
```

---

## Self-Review Notes

- **Spec coverage:** per-act pool computation (Task 1 `_act_pools`), per-map window merging across act boundaries (Task 1 `_map_windows`), gap scanning with `ongoing` flag (Task 1 `_gap_in_window`), current vs. all-time-record composition with `record_is_current` (Task 1 `_build_map_streaks`), DB wiring (Task 1 `compute_map_streaks`), router + template (Task 2) — all covered.
- **No placeholders:** every step has real code; the `NotImplementedError` stubs in Task 1 are intentional scaffolding removed by the very next implementation step, not left-behind TODOs.
- **Type consistency:** `MapStreakWindow`/`MapStreak` field names are identical across Task 1's definition and Task 2's template/router usage (`.map_name`, `.current`, `.record`, `.record_is_current`, `.act_labels`, `.gap`, `.ongoing`).
- **Scope:** single subsystem (one new service module + one existing page), no decomposition needed.
