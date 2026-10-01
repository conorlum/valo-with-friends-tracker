# Map Control "Unknown" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each team an "unknown" area (where an enemy could be) that spreads at walking speed. It replaces remembered-ground decay, stops backfill, defines Safe, and is drawn as a hatch in the viewer.

**Architecture:** A new `Unknown` class in `app/control/engine.py` runs once per tick, before `Memory`. It seeds from enemy players and the barrier drop, spreads `UNKNOWN_MPS` through walkable cells and specials, and is cleared by the team's live control. `Memory` loses its decay and is eaten by unknown instead. `Tick` gets the round's unknown (`tick.unknown`), which `backfill` subtracts and `compose` uses for Safe (line of sight from unknown). `RoundControl.unknown` is stored as two optional single-slot mask streams (`unknown_a`, `unknown_b`). `replay_control.js` decodes and hatches them, and `replay.js` adds a toggle.

**Tech Stack:** Python 3.13, numpy, scipy.ndimage, pytest; plain ES5 JavaScript run under node for tests; Jinja2 templates.

**Spec:** `docs/map-control-unknown-plan.md` (read it first; this plan argues from it).

## Global Constraints

- Work in the worktree `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\.claude\worktrees\control-fixes` on branch `worktree-control-fixes`. Never edit the main checkout.
- Python: `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv\Scripts\python.exe` (the worktree has no venv). Run every command from the worktree's `webapp\` folder. Below, `PY` means that interpreter.
- `CONTROL_REVISION` stays **3** (unreleased). Constants change, so re-pin `PINNED[3]` in `tests/replays/test_control_format.py` in place; never add a revision 4.
- `UNKNOWN_MPS = 3.5` (shift-walk). `DECAY_MPS` and `BARRIER_GRACE_S` are deleted.
- No DB writes, ever, in this plan. No full-corpus recompute: the real-data check is rounds 1-2 of replay `6f12db3e-b2db-4bca-96e4-a837c85ba5a6`, computed locally from the live site's public responses.
- The repo is public: commit no credentials, and no preview pages (they name real players). Previews go under `%TEMP%\valo-replay\`.
- Commit messages: write them to a scratch file with the Write tool, then `git commit -F <file>` (never a heredoc). End every message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Existing tests:** a pre-existing test that fails because of this change is **not** rewritten unless this plan names it as replaced. Stop, and report the failing assertion and why to the user before touching it.
- The counterfactual (`compose(removed=...)`) reuses the tick's unknown unchanged. Unknown is the round's history, not one tick's players.

## Review Focus

1. **Maps without barrier paint, or with leaking paint.** Unknown must start as just the enemies' own cells, not empty and not the whole map. Test in Task 1: `test_without_barrier_paint_unknown_starts_at_the_enemies_alone`.
2. **An enemy standing in the team's vision** must push out no unknown inside that vision, and must start seeding on the tick they step out. Test in Task 1: `test_an_enemy_the_team_sees_pushes_no_unknown_until_out_of_sight`.
3. **Irregular tick spacing** (the schedule mixes 4 and 16 Hz). Spread speed must not depend on tick spacing. Test in Task 1: `test_spread_does_not_depend_on_tick_spacing`.
4. **Smokes.** Unknown walks through a smoke, but Safe's line of sight from unknown stops at it. Test in Task 3: `test_a_smoke_hides_ground_from_unknown`.
5. **Rows without unknown streams** (every row stored before this change) must still decode, and the viewer must hide the toggle. Tests in Task 4 (`test_a_row_without_unknown_reads_as_before`) and Task 5 (`cursor.unknown` returns `null`).

---

### Task 1: The `Unknown` class

**Files:**
- Modify: `webapp/app/control/engine.py`: constants block near line 75; docstring bullets at lines 19-28; `Tick._links` at line 749; add `special_links`, `barrier_start` and `Unknown` beside `Memory` (line ~1120).
- Test: `webapp/tests/replays/test_control_unknown.py` (new).

**Interfaces:**
- Consumes: `Holder` fields `team`, `cell`, `active`, `passive`, `watch`; `Geometry` fields `walk` (GRID x GRID bool), `barrier` (GRID x GRID bool or None), `specials`, `cell_m`, `cell_of_px`, `px_of_uv`; `_share_by_walk`; `EIGHT`; `GRID`.
- Produces:
  - `UNKNOWN_MPS: float = 3.5`
  - `special_links(geo) -> list[tuple[int, int, bool]]`: (cell a, cell b, one_way).
  - `barrier_start(geo, tick) -> dict[str, tuple[np.ndarray, dict[int, int]]]`: side -> (flat bool area, {slot: start cell}).
  - `class Unknown(geo)` with `cells: dict[str, np.ndarray]` (flat bool, side -> where that side's enemies could be), `begin(areas)`, `apply(tick)`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_control_unknown.py`:

```python
"""Unknown control (docs/map-control-unknown-plan.md) on toy maps: where an enemy of the team could be,
pushed out by the enemy's players and the barrier drop, spreading at UNKNOWN_MPS, cleared by the
team's live control. Positions are minimap pixels; cells are 8 px (1.12 m)."""

import copy

import numpy as np
import pytest

from app.control import engine as ce
from app.control.geometry import GRID
from tests.replays.control_toys import open_hall, two_rooms, uv


class _Tk:
    """Just what Unknown and Memory read from a tick: its time and holders."""

    def __init__(self, t, *holders):
        self.t, self.holders = t, {h.slot: h for h in holders}


def _at(slot, team, geo, x, y, cells=()):
    """A holder standing at (x, y) px whose live view (active) is exactly `cells` (flat indices or mask)."""
    z = np.zeros(GRID * GRID, bool)
    view = z.copy()
    cells = np.asarray(cells)
    view[cells if cells.dtype == bool else cells.astype(int)] = True
    return ce.Holder(slot, team, geo.cell_of_px(x, y), float(x), float(y), view.copy(), z.copy(), z.copy(),
                     view.copy(), view.copy(), False, "hold")


def _halves(geo):
    walk = np.flatnonzero(geo.walk.ravel())
    x = walk % GRID
    mid = geo.cell_of_px(256, 200) % GRID
    return walk[x < mid], walk[x >= mid]


def _barrier_hall():
    """The open hall with a barrier line down x 256 (column 32): A starts west of it, B east."""
    geo = copy.copy(open_hall())    # open_hall() is shared: don't leave a barrier on it
    geo.barrier = np.zeros((GRID, GRID), bool)
    geo.barrier[:, geo.cell_of_px(256, 200) % GRID] = True
    return geo


def _col(geo, x_px, y_px=200):
    return geo.cell_of_px(x_px, y_px)


def test_unknown_spreads_from_an_enemy_at_a_shift_walk():
    geo = open_hall()
    unk = ce.Unknown(geo)
    a, b = (lambda: _at(0, "A", geo, 120, 200)), (lambda: _at(5, "B", geo, 400, 200))
    unk.apply(_Tk(0.0, a(), b()))
    e = _col(geo, 400)
    assert np.flatnonzero(unk.cells["A"]).tolist() == [e], "at once: only the enemy's own cell"
    unk.apply(_Tk(1.0, a(), b()))
    steps = int(ce.UNKNOWN_MPS * 1.0 // geo.cell_m)
    assert steps == 3
    assert unk.cells["A"][e - steps] and not unk.cells["A"][e - steps - 1], "8-connected steps at UNKNOWN_MPS"
    assert unk.cells["B"][_col(geo, 120) + steps], "each team's unknown comes from the other team's players"


def test_live_vision_pushes_unknown_back_and_it_refills_when_they_look_away():
    geo = open_hall()
    west, east = _halves(geo)
    unk = ce.Unknown(geo)
    b = lambda: _at(5, "B", geo, 400, 200)  # noqa: E731
    for t in (0.0, 30.0):                    # 30 s: the whole hall but A's own cell
        unk.apply(_Tk(t, _at(0, "A", geo, 120, 200), b()))
    assert unk.cells["A"][_col(geo, 200)] and not unk.cells["A"][_col(geo, 120)], "a player's own cell is clear"
    unk.apply(_Tk(30.1, _at(0, "A", geo, 120, 200, east), b()))    # A sees the east half (and B in it)
    assert not unk.cells["A"][east].any(), "cleared as far as they see"
    assert unk.cells["A"][_col(geo, 200)], "the west half they don't see stays unknown"
    unk.apply(_Tk(31.1, _at(0, "A", geo, 120, 200), b()))           # they look away
    steps = 3     # 3.5 m/s for 1 s is 3 whole 1.12 m cells; the carry left from earlier ticks is under a cell
    for k in range(steps):
        assert unk.cells["A"][_col(geo, 256 + 8 * k + 4)], f"refilled {k + 1} cell(s) east of the old line"
    assert not unk.cells["A"][_col(geo, 300)], "not yet further"
    assert unk.cells["A"][_col(geo, 400)], "and from B again"


def test_a_dead_enemy_stops_pushing_unknown_but_what_is_out_stays():
    geo = open_hall()
    unk = ce.Unknown(geo)
    a = lambda: _at(0, "A", geo, 120, 200)  # noqa: E731
    unk.apply(_Tk(0.0, a(), _at(5, "B", geo, 400, 200)))
    unk.apply(_Tk(1.0, a(), _at(5, "B", geo, 400, 200)))
    before = unk.cells["A"].copy()
    unk.apply(_Tk(2.0, a()))                 # B is dead: no holder
    after = unk.cells["A"]
    assert after[before].all(), "what was out stays"
    assert after.sum() > before.sum(), "and keeps spreading from itself"


def test_without_barrier_paint_unknown_starts_at_the_enemies_alone():
    geo = open_hall()
    tk = _Tk(0.0, _at(0, "A", geo, 120, 200), _at(5, "B", geo, 400, 200))
    areas = ce.barrier_start(geo, tk)
    assert areas == {}
    unk = ce.Unknown(geo)
    unk.begin(areas)
    unk.apply(tk)
    assert np.flatnonzero(unk.cells["A"]).tolist() == [_col(geo, 400)]
    assert np.flatnonzero(unk.cells["B"]).tolist() == [_col(geo, 120)]


def test_at_the_barrier_drop_each_teams_unknown_is_the_enemys_side():
    geo = _barrier_hall()
    west, east = _halves(geo)
    line = geo.barrier.ravel()
    tk = _Tk(0.0, _at(0, "A", geo, 150, 200), _at(5, "B", geo, 400, 200))
    areas = ce.barrier_start(geo, tk)
    assert set(areas) == {"A", "B"} and areas["A"][1] == {0: _col(geo, 150)}
    unk = ce.Unknown(geo)
    unk.begin(areas)
    unk.apply(tk)
    assert unk.cells["A"][east[~line[east]]].all() and not unk.cells["A"][west].any()
    assert unk.cells["B"][west[west != _col(geo, 150)]].all() and not unk.cells["B"][east].any()


def test_an_enemy_the_team_sees_pushes_no_unknown_until_out_of_sight():
    geo = open_hall()
    west, east = _halves(geo)
    unk = ce.Unknown(geo)
    unk.apply(_Tk(0.0, _at(0, "A", geo, 120, 200, east), _at(5, "B", geo, 400, 200)))
    assert not unk.cells["A"].any(), "B stands in A's view: A knows where B is"
    near = np.flatnonzero(geo.walk.ravel() & (geo.centres[:, 0] < 330))
    unk.apply(_Tk(0.0625, _at(0, "A", geo, 120, 200, near), _at(5, "B", geo, 400, 200)))
    assert unk.cells["A"][_col(geo, 400)], "out of sight: B's cell is unknown on that tick"


def test_unknown_crosses_a_one_way_special_only_one_way():
    geo = two_rooms([{"kind": "drop", "a": list(uv(170, 170)), "b": list(uv(310, 170)), "one_way": True}])
    west_room = lambda u: u[_col(geo, 120, 120)]   # noqa: E731
    east_room = lambda u: u[_col(geo, 360, 120)]   # noqa: E731
    down = ce.Unknown(geo)
    for t in (0.0, 30.0):
        down.apply(_Tk(t, _at(5, "B", geo, 120, 120)))
    assert east_room(down.cells["A"]), "from the west room it drops into the east room"
    up = ce.Unknown(geo)
    for t in (0.0, 30.0):
        up.apply(_Tk(t, _at(5, "B", geo, 360, 120)))
    assert not west_room(up.cells["A"]), "but never climbs back"
    assert not ce.Unknown(two_rooms()).cells["A"].any()


@pytest.mark.parametrize("hz", [16, 4])
def test_spread_does_not_depend_on_tick_spacing(hz):
    geo = open_hall()
    unk = ce.Unknown(geo)
    for k in range(2 * hz + 1):
        unk.apply(_Tk(k / hz, _at(0, "A", geo, 120, 200), _at(5, "B", geo, 400, 200)))
    steps = int(ce.UNKNOWN_MPS * 2.0 // geo.cell_m)
    e = _col(geo, 400)
    assert unk.cells["A"][e - steps] and not unk.cells["A"][e - steps - 1]
```

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_unknown.py -q`
Expected: FAIL, `AttributeError: module 'app.control.engine' has no attribute 'Unknown'` (and `barrier_start`, `UNKNOWN_MPS`).

- [ ] **Step 3: Implement**

In `engine.py`'s constants, under `BARRIER_GRACE_S` (leave `DECAY_MPS` and `BARRIER_GRACE_S` for Task 2), add:

```python
# Unknown (docs/map-control-unknown-plan.md, 2026-10-01): where an enemy of a team could be. It spreads
# at Valorant's shift-walk, the speed an enemy can move without being heard.
UNKNOWN_MPS = 3.5
```

Replace `Tick._links`'s body with a call to a new module-level function, placed just above `class Tick`:

```python
def special_links(geo: Geometry) -> list[tuple[int, int, bool]]:
    """The map's specials (teleporters, ropes, drops) as (cell a, cell b, one way)."""
    out = []
    for sp in geo.specials:
        try:
            a = geo.cell_of_px(*geo.px_of_uv(*sp["a"]))
            b = geo.cell_of_px(*geo.px_of_uv(*sp["b"]))
        except (KeyError, TypeError):
            continue
        out.append((a, b, bool(sp.get("one_way"))))
    return out
```

```python
    def _links(self) -> list[tuple[int, int, bool]]:
        return special_links(self.geo)
```

Above `class Memory`, add `barrier_start`. Its body is `Memory.start`'s, returning areas instead of storing them. Task 2 points `Memory` at it.

```python
def barrier_start(geo: Geometry, tick) -> dict[str, tuple[np.ndarray, dict[int, int]]]:
    """The barriers drop: side -> (its start ground, flat: the 4-connected walkable region around its
    players, cut by the barrier paint; {slot: start cell}). A side whose ground reaches an enemy's start
    (the paint has a gap) is left out and counted; no paint, no start ground."""
    if geo.barrier is None:
        return {}
    open_ = geo.walk & ~geo.barrier
    regions, _ = ndimage.label(open_)
    out = {}
    for side in ("A", "B"):
        hs = [h for h in tick.holders.values() if h.team == side and open_.ravel()[h.cell]]
        ids = {int(regions.ravel()[h.cell]) for h in hs}
        if not hs:
            continue
        starts = {h.slot: h.cell for h in hs}
        # a player pressed against a barrier can stand on a line cell: they start from the
        # neighbouring open cell in their teammates' ground
        for h in tick.holders.values():
            if h.team == side and h.slot not in starts and geo.barrier.ravel()[h.cell]:
                y, x = divmod(h.cell, GRID)
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1),
                               (y - 1, x - 1), (y - 1, x + 1), (y + 1, x - 1), (y + 1, x + 1)):
                    if 0 <= ny < GRID and 0 <= nx < GRID and int(regions[ny, nx]) in ids:
                        starts[h.slot] = ny * GRID + nx
                        break
        area = np.isin(regions, list(ids))
        if any(area.ravel()[e.cell] for e in tick.holders.values() if e.team != side):
            tick.rnd.missing["barrier paint leaks (no start ground)"] += 1
            continue
        out[side] = (area.ravel(), starts)
    return out
```

Then the class, also above `Memory`:

```python
class Unknown:
    """Each team's unknown across a round's ticks (docs/map-control-unknown-plan.md): the cells where an
    enemy of the team could be. `begin` takes the barrier drop (each team's unknown is the enemy's start
    ground). `apply` runs on each tick in time order, after the tick's vision and before Memory.apply:
    the enemy's live players push it out from their own cells, it spreads at UNKNOWN_MPS through
    walkable cells in 8-connected steps and across the map's specials, and the team's live control
    (its players' active and passive vision, their watchers and their own cells) clears it and stops it.
    Smokes don't stop it: you can walk through a smoke."""

    def __init__(self, geo: Geometry):
        self.geo = geo
        self.cells = {"A": np.zeros(GRID * GRID, bool), "B": np.zeros(GRID * GRID, bool)}
        self.carry = {"A": 0.0, "B": 0.0}         # metres of spread not yet a whole cell step
        self.t: float | None = None
        self.links = special_links(geo)

    def begin(self, areas: dict) -> None:
        for side in ("A", "B"):
            other = "B" if side == "A" else "A"
            if other in areas:
                self.cells[side] = areas[other][0].copy()

    def apply(self, tick) -> None:
        walk = self.geo.walk.ravel()
        dt = 0.0 if self.t is None else max(0.0, tick.t - self.t)
        self.t = tick.t
        for side in ("A", "B"):
            live = np.zeros(GRID * GRID, bool)
            unk = self.cells[side].copy()
            for h in tick.holders.values():
                if h.team == side:
                    live |= h.active | h.passive | h.watch
                    live[h.cell] = True
                else:
                    unk[h.cell] = True            # an enemy pushes it out from where they stand
            room = walk & ~live
            self.carry[side] += UNKNOWN_MPS * dt
            steps = int(self.carry[side] // self.geo.cell_m)
            self.carry[side] -= steps * self.geo.cell_m
            self.cells[side] = self._spread(unk & room, room, steps)

    def _spread(self, cells: np.ndarray, room: np.ndarray, steps: int) -> np.ndarray:
        g, r = cells.reshape(GRID, GRID), room.reshape(GRID, GRID)
        for _ in range(steps):
            nxt = ndimage.binary_dilation(g, EIGHT) & r
            for a, b, one_way in self.links:
                if g.flat[a] and r.flat[b]:
                    nxt.flat[b] = True
                if not one_way and g.flat[b] and r.flat[a]:
                    nxt.flat[a] = True
            if np.array_equal(nxt, g):
                break
            g = nxt
        return g.ravel().copy()
```

Note: `binary_dilation` keeps the source cells, so `nxt` contains `g` (`g` is already inside `room`).

In the module docstring, after the **Backfill** bullet, add:

```
- **Unknown (2026-10-01; docs/map-control-unknown-plan.md).** Each team's unknown is where an enemy
  could be: pushed out by the enemy's live players, the enemy's side of the barriers at the drop, and
  its own spread at UNKNOWN_MPS (8-connected, across specials, through smokes). The team's live
  control clears it on contact.
```

- [ ] **Step 4: Run the new tests and the engine tests**

Run: `PY -m pytest tests/replays/test_control_unknown.py tests/replays/test_control_engine.py -q`
Expected: all PASS. `test_control_format.py`'s pin test fails here (a new constant); Task 2 re-pins it.

- [ ] **Step 5: Commit**

Message file:
```
Map control: unknown, where an enemy could be (spreads at a shift-walk)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```
Run: `git add app/control/engine.py tests/replays/test_control_unknown.py` then `git commit -F <message file>`.

---

### Task 2: Remembered ground is eaten by unknown; wire it into `compute_round`

**Files:**
- Modify: `webapp/app/control/engine.py`: delete `DECAY_MPS` and `BARRIER_GRACE_S` and their comments; rewrite `Memory` (line ~1120); docstring **Memory** bullet (lines 19-24); `Tick.__init__` (add `unknown`); `RoundControl` (add `unknown`); `compute_round` (lines 1365-1449).
- Modify: `webapp/tests/replays/test_control_engine.py`: the memory and barrier tests at lines 381-511 (replaced as listed below).
- Modify: `webapp/tests/replays/test_control_format.py:23-24` (re-pin `PINNED[3]`).
- Test: `webapp/tests/replays/test_control_unknown.py` (add).

**Interfaces:**
- Consumes: `barrier_start`, `Unknown` (Task 1).
- Produces:
  - `Memory.begin(areas)`, `Memory.apply(tick, unknown: dict[str, np.ndarray] | None = None)`. `Memory.start`, `.carry`, `.held` and `.t` are gone.
  - `Tick.unknown: dict[str, np.ndarray] | None` (flat bool per side, set by `compute_round`; `None` for a bare `Tick`).
  - `RoundControl.unknown: dict | None`: side group -> ticks x walkable cells, bool.

The following tests encode the rule the user replaced (decay from open ground, the 5 s grace, `Memory.start`). The spec authorises **replacing** them as written below; no other existing test may be changed:
- `test_ground_looked_away_from_stays_passive_and_open_ground_eats_in_at_a_walk`: delete (replaced by `test_remembered_ground_lasts_until_unknown_reaches_it`).
- `test_memory_walled_off_by_live_control_does_not_decay`: delete (no decay any more).
- `test_the_barrier_start_gives_each_team_its_side_as_passive_with_a_grace`: delete (replaced by `test_the_spawn_is_held_until_an_enemy_could_have_walked_there`).
- `test_a_player_pressed_on_the_barrier_line_still_gets_a_share`, `test_a_leaking_barrier_gives_no_start_ground` and `test_no_barrier_paint_means_no_start_memory`: change only the calls, as shown in Step 1. The assertions stay.

- [ ] **Step 1: Write the failing tests and update the three call sites**

Append to `test_control_unknown.py`:

```python
from tests.replays.control_toys import blob  # noqa: E402

A_OWN = (ce.A_PASSIVE, ce.A_SAFE, ce.A_ACTIVE)


def still(side, x, y, yaw):
    return side, [(0.0, x, y, yaw)]


def test_remembered_ground_lasts_until_unknown_reaches_it():
    geo = open_hall()
    west, _ = _halves(geo)
    z = np.zeros(GRID * GRID, bool)
    mem = ce.Memory(geo)
    mem.apply(_Tk(0.0, _at(0, "A", geo, 120, 200, west)), {"A": z, "B": z})
    blind = _at(0, "A", geo, 120, 200)
    mem.apply(_Tk(30.0, blind), {"A": z, "B": z})
    assert blind.passive[west].all(), "no enemy could have got there: still held after 30 s"
    reached = z.copy()
    reached[west[west % GRID >= 28]] = True           # unknown has walked into the west half's last 4 columns
    eaten = _at(0, "A", geo, 120, 200)
    mem.apply(_Tk(31.0, eaten), {"A": reached, "B": z})
    assert not eaten.passive[reached].any(), "unknown eats remembered ground"
    held = np.zeros(GRID * GRID, bool)
    held[west] = True
    assert eaten.passive[held & ~reached].all(), "and nothing else"


def test_the_spawn_is_held_until_an_enemy_could_have_walked_there():
    geo = _barrier_hall()
    # each team faces its own back wall: nobody watches the barrier
    players = {0: still("A", 120, 200, 180), 5: still("B", 400, 200, 0)}
    rc = ce.compute_round(blob(players, t_end=6.0), geo, ticks=[0.0, 1.0, 2.0])
    col = lambda x: int(np.searchsorted(rc.walk_cells, _col(geo, x)))   # noqa: E731
    assert rc.states[0, col(248)] in A_OWN and rc.states[0, col(160)] in A_OWN, "at the drop: A's whole side"
    assert rc.states[1, col(248)] not in A_OWN, "1 s later the cell by the barrier is gone: no grace"
    assert rc.states[2, col(160)] in A_OWN, "deep in A's side: still held at 2 s"
    assert rc.unknown["A"][2, col(240)] and not rc.unknown["A"][2, col(160)]
    assert rc.unknown["A"].shape == rc.states.shape
```

In `test_control_engine.py`, change the three surviving barrier tests' calls:
- `test_a_player_pressed_on_the_barrier_line_still_gets_a_share`: replace `mem.start(_Tk(0.0, ...))` with
  `mem.begin(ce.barrier_start(geo, _Tk(0.0, _at(0, "A", geo, 150, 250), on_line, _at(5, "B", geo, 400, 200))))`.
- `test_a_leaking_barrier_gives_no_start_ground`: replace `mem.start(tk)` with `mem.begin(ce.barrier_start(geo, tk))`.
- `test_no_barrier_paint_means_no_start_memory`: replace its first `mem.apply(...)` with
  `mem.begin(ce.barrier_start(geo, _Tk(0.0, _at(0, "A", geo, 150, 200))))`. Keep the second `apply` and the assertion.

Delete the three tests listed above.

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_unknown.py tests/replays/test_control_engine.py -q`
Expected: FAIL. `Memory.apply()` takes no `unknown`, `Memory` has no `begin`, and `RoundControl` has no `unknown`.

- [ ] **Step 3: Implement**

Delete from the constants:

```python
# Remembered ground (D6, 2026-09-30): what a player saw and looked away from stays theirs as passive
# control, and open ground eats into it at a quiet walk, Valorant's shift-walk (approximate).
DECAY_MPS = 3.5
# When the buy-phase barriers drop, each team remembers its side of them (the barrier paint), spared
# decay for this long (the user's call, 2026-09-30: passive, with a short grace before it erodes).
BARRIER_GRACE_S = 5.0
```

Replace the module docstring's **Memory** bullet with:

```
- **Memory (D6).** Ground a player saw and looked away from stays theirs as passive control until
  the team's unknown reaches it (2026-10-01; it replaced decay from open ground). Memory dies with
  its player. When the buy-phase barriers drop, each team remembers its side of them (the barrier
  paint).
```

Replace the whole `Memory` class with:

```python
class Memory:
    """Remembered ground across a round's ticks (D6). `begin` takes the barrier drop (each team
    remembers its side, shared out to its players by walking distance). `apply` runs on each tick in
    time order, after Unknown.apply and before `compose`: it drops what the team's unknown has
    reached, adds what is left to each holder's passive cells, then remembers what they see now."""

    def __init__(self, geo: Geometry):
        self.geo = geo
        self.cells: dict[int, np.ndarray] = {}    # slot -> flat remembered cells

    def begin(self, areas: dict) -> None:
        for _, (area, starts) in areas.items():
            for slot, share in _share_by_walk(area.reshape(GRID, GRID), starts).items():
                self.cells[slot] = share.ravel()

    def apply(self, tick, unknown: dict | None = None) -> None:
        walk = self.geo.walk.ravel()
        for s in [s for s in self.cells if s not in tick.holders]:
            del self.cells[s]                     # memory dies with its player
        for h in tick.holders.values():
            if h.slot in self.cells:
                self.cells[h.slot] &= ~(h.active | h.passive)    # seen again: live, not memory
                if unknown is not None:
                    self.cells[h.slot] &= ~unknown[h.team]       # an enemy could be there now
        for h in tick.holders.values():
            seen = (h.active | h.passive) & walk
            if h.slot in self.cells:
                h.passive = h.passive | (self.cells[h.slot] & ~h.active)
                self.cells[h.slot] |= seen
            else:
                self.cells[h.slot] = seen
```

In `Tick.__init__`, after `self._safe = {}`, add:

```python
        # side -> flat cells where an enemy of that side could be (Unknown; set by compute_round). None
        # for a tick built on its own: then Safe is the instant flood (Q73), as before unknown.
        self.unknown: dict[str, np.ndarray] | None = None
        self._usafe: dict[str, np.ndarray] = {}   # side -> its Safe cells from unknown (Task 3)
```

At the end of `RoundControl`, add the field:

```python
    unknown: dict | None = None         # side group -> ticks x walkable cells, bool: its unknown
```

In `compute_round`, replace `memory = Memory(geo)` with:

```python
    memory = Memory(geo)
    unknown = Unknown(geo)
    unknown_masks = {side: np.zeros((n_ticks, n_walk), bool) for side in ("A", "B")}
```

Replace the loop's head, from `tick = Tick(rnd, t, timings)` through `timings["memory"] += ...`, with:

```python
        tick = Tick(rnd, t, timings)
        a = time.perf_counter()
        if n == 0:
            areas = barrier_start(geo, tick)
            memory.begin(areas)
            unknown.begin(areas)
        unknown.apply(tick)                   # before memory: live vision only clears it
        timings["unknown"] += time.perf_counter() - a
        a = time.perf_counter()
        memory.apply(tick, unknown.cells)
        timings["memory"] += time.perf_counter() - a
        tick.unknown = {side: cells.copy() for side, cells in unknown.cells.items()}
        for side in ("A", "B"):
            unknown_masks[side][n] = tick.unknown[side][walk_flat]
```

In the `return RoundControl(...)`, add `unknown=unknown_masks,` after `knew_states=knew_states or None,`.

- [ ] **Step 4: Re-pin revision 3 and run the whole control suite**

Run: `PY -m pytest tests/replays/test_control_format.py::test_the_engine_constants_are_pinned_to_the_control_revision -q`
Expected: FAIL with `digest <d>`. In `tests/replays/test_control_format.py`, set `PINNED[3]` to `<d>` and change the comment above `PINNED` to:

```python
# 2: space taken, what each team knew (KNEW_*) and remembered ground (D6: DECAY_MPS). Unreleased,
# so re-pinned in place. 3: barriers, backfill and unknown (UNKNOWN_MPS; DECAY_MPS and BARRIER_GRACE_S
# removed). Unreleased, so re-pinned in place.
```

Run: `PY -m pytest tests/replays -q -k control`
Expected: all PASS. If a pre-existing test fails (likely candidates: `test_a_holders_death_loses_the_space_on_the_next_tick`, `test_a_turn_leaves_the_ground_behind_covered`, and the `test_control_stats.py` taken tests), **stop** and report the assertion and the cause to the user (Global Constraints).

- [ ] **Step 5: Commit**

Message file:
```
Map control: remembered ground ends where unknown reaches it (no decay, no grace)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```
Run: `git add app/control/engine.py tests/replays/test_control_unknown.py tests/replays/test_control_engine.py tests/replays/test_control_format.py` then `git commit -F <message file>`.

---

### Task 3: Backfill stops at unknown; Safe is line of sight from unknown

**Files:**
- Modify: `webapp/app/control/engine.py`: `Tick._pocket` (line ~675); add `Tick.unknown_safe` after `comp_seen` (line ~826); `Tick.compose`'s level loop (lines 936-948); the docstring's **Backfill** and **Safe** bullets.
- Test: `webapp/tests/replays/test_control_unknown.py` (add).

**Interfaces:**
- Consumes: `Tick.unknown`, `Tick._usafe` (Task 2); `Tick.comp_seen(mask GRID x GRID, watched GRID x GRID) -> flat bool`.
- Produces: `Tick.unknown_safe(side: str) -> np.ndarray` (flat bool). `compose` uses it whenever `self.unknown is not None`.

- [ ] **Step 1: Write the failing tests**

Append to `test_control_unknown.py`:

```python
from tests.replays.control_toys import midwall_hall  # noqa: E402


def _band(geo, x0, x1, y0=96, y1=296):
    cx, cy = geo.centres[:, 0], geo.centres[:, 1]
    return geo.walk.ravel() & (cx >= x0) & (cx < x1) & (cy >= y0) & (cy < y1)


def _tick(geo, players, t=1.0, **kw):
    return ce.Tick(ce.RoundInputs(blob(players, **kw), geo), t)


def _lines(geo, views: dict):
    """A tick whose holders see exactly `views`: {slot: (team, x, y, active flat)}."""
    tk = _tick(geo, {s: still(team, x, y, 0) for s, (team, x, y, _) in views.items()})
    z = np.zeros(GRID * GRID, bool)
    tk.holders = {s: ce.Holder(s, team, geo.cell_of_px(x, y), x, y, act.copy(), z.copy(), z.copy(), act.copy(),
                               act.copy(), False, "hold") for s, (team, x, y, act) in views.items()}
    tk._back = {}
    return tk


def test_backfill_never_claims_where_an_enemy_could_be():
    geo = open_hall()
    tk = _lines(geo, {0: ("A", 150, 200, _band(geo, 240, 256)), 5: ("B", 400, 200, _band(geo, 300, 316))})
    assert tk.backfill("A")[0][_col(geo, 110, 120)], "without unknown: behind A's line is A's"
    strip = _band(geo, 96, 140)
    tk.unknown = {"A": strip.copy(), "B": np.zeros(GRID * GRID, bool)}
    tk._back = {}
    back = tk.backfill("A")[0]
    assert not back[strip].any() and back[_col(geo, 200, 250)]


def test_safe_is_what_unknown_cannot_see():
    geo = midwall_hall()       # a wall down x 248-264 from the north wall to y 248: a gap to the south
    tk = _tick(geo, {0: still("A", 120, 120, 180), 5: still("B", 400, 120, 0)})
    tk.unknown = {"A": _band(geo, 300, 400, 96, 160), "B": np.zeros(GRID * GRID, bool)}
    safe = tk.unknown_safe("A")
    assert safe[_col(geo, 150, 120)], "behind the wall from unknown: Safe"
    assert not safe[_col(geo, 350, 120)], "unknown itself is not Safe"
    assert not safe[_col(geo, 330, 250)], "unknown sees it"
    assert tk.unknown_safe("B")[_col(geo, 350, 120)], "a team with no unknown: all Safe"
    # through compose: give B an unknown over the west half, so B isn't Safe there and nothing is contested
    tk.unknown["B"] = _band(geo, 96, 248)
    tk._usafe = {}
    state = tk.compose()["state"]
    assert int(state[_col(geo, 150, 120)]) == ce.A_SAFE


def test_a_smoke_hides_ground_from_unknown():
    geo = open_hall()
    smoke = {"k": "ability", "t": 0.0, "t1": 10.0, "by": 5, "kind": "Zone", "code": "Wraith", "name": "4_Smoke",
             "u": uv(250, 200)[0], "v": uv(250, 200)[1]}
    tk = _tick(geo, {0: still("A", 120, 120, 180), 5: still("B", 400, 120, 0)}, util=[smoke])
    tk.unknown = {"A": _band(geo, 300, 316, 192, 208), "B": np.zeros(GRID * GRID, bool)}
    safe = tk.unknown_safe("A")
    assert safe[_col(geo, 150, 200)], "straight through the smoke: hidden"
    assert not safe[_col(geo, 290, 200)], "this side of the smoke: seen"


def test_the_knowledge_views_use_the_same_unknown():
    geo = open_hall()
    rnd = ce.RoundInputs(blob({0: still("A", 120, 200, 0), 5: still("B", 400, 200, 180)}), geo)
    tk = ce.Tick(rnd, 1.0)
    tk.unknown = {"A": _band(geo, 300, 316), "B": _band(geo, 100, 116)}
    kt = ce.Knowledge(rnd, "A").tick_for(tk, 1.0)
    assert kt.unknown is tk.unknown
```

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_unknown.py -q`
Expected: FAIL. `Tick` has no `unknown_safe`, and the backfill test still claims the strip.

- [ ] **Step 3: Implement**

In `Tick._pocket`, replace the final `return ...` with:

```python
        pocket = ((lab > 0) & ~np.isin(lab, list(reach))).ravel() & ~enemy
        if self.unknown is not None:
            pocket &= ~self.unknown[side]     # never where an enemy could be (docs/map-control-unknown-plan.md)
        return pocket
```

After `comp_seen`, add:

```python
    def unknown_safe(self, side: str) -> np.ndarray:
        """Safe (docs/map-control-unknown-plan.md): walkable cells outside `side`'s unknown that no cell of
        it sees, smoke-aware (`comp_seen`'s boundary method, Q73). Cached for the tick; the counterfactual
        and the knowledge pictures reuse it, since unknown is the round's history, not one tick's players."""
        if side not in self._usafe:
            walk = self.geo.walk
            unk = self.unknown[side].reshape(GRID, GRID)
            if not unk.any():
                self._usafe[side] = walk.ravel().copy()
            else:
                watched = np.zeros(GRID * GRID, bool)
                for h in self.team(side, None):
                    watched |= h.active | h.passive | h.watch
                seen = self.comp_seen(unk, watched.reshape(GRID, GRID))
                self._usafe[side] = (walk & ~unk).ravel() & ~seen
        return self._usafe[side]
```

In `compose`'s level loop, replace

```python
            f_enemy = fills[other]
            safe = walk.ravel() if f_enemy is None else f_enemy.safe(walk).ravel()
```

with

```python
            f_enemy = fills[other]
            if self.unknown is not None:
                safe = self.unknown_safe(side)
            else:
                safe = walk.ravel() if f_enemy is None else f_enemy.safe(walk).ravel()
```

`Knowledge.tick_for` uses `copy.copy(tick)`, so `kt.unknown` and `kt._usafe` are the tick's own objects. Leave that as it is; the test pins it.

In the module docstring, add to the end of the **Backfill** bullet: "Never on a cell in the team's unknown." Replace the **Safe (Q73)** bullet's first sentence with: "**Safe (Q73; 2026-10-01).** A team's Safe ground is what no cell of its unknown sees, smoke-aware, from the unknown's boundary. A tick built without unknown (tests) falls back to the instant flood: each team's free space from its alive players through walkable cells the other team doesn't watch (map specials link cells); the other team's Safe is what no free cell sees." Keep the rest of the bullet.

- [ ] **Step 4: Run the whole control suite**

Run: `PY -m pytest tests/replays -q -k control`
Expected: all PASS. A pre-existing failure means stop and report (Global Constraints).

- [ ] **Step 5: Commit**

Message file:
```
Map control: backfill stops at unknown; Safe is what unknown can't see

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```
Run: `git add app/control/engine.py tests/replays/test_control_unknown.py` then `git commit -F <message file>`.

---

### Task 4: Store unknown as two optional streams

**Files:**
- Modify: `webapp/app/control/encode.py` (`encode_data`, after the `knew` block).
- Modify: `webapp/app/replays/control_format.py` (`decode_masks` gets `slots`; add `decode_unknown`; docstring note).
- Modify: `webapp/tests/replays/test_control_format.py` (`test_what_each_team_knew_round_trips_as_optional_streams`'s last block, see Step 1; add two tests).

**Interfaces:**
- Consumes: `RoundControl.unknown` (Task 2); `encode_masks(masks ticks x slots x cells, checkpoints)`.
- Produces:
  - Streams `unknown_a`, `unknown_b`: `encode_masks`' format with one slot.
  - Header `unknown_checkpoints: [[tick, offset_a, offset_b], ...]`, at the same checkpoint ticks as `checkpoints`.
  - `cf.decode_masks(stream, ticks, cells, checkpoints, slots=SLOTS)`.
  - `cf.decode_unknown(stream, ticks, cells, checkpoints) -> list[list[int]]`.

- [ ] **Step 1: Write the failing tests**

Add to `test_control_format.py` (with `import dataclasses` at the top):

```python
def test_each_teams_unknown_round_trips_as_optional_streams(round_control):
    rc, data = round_control
    header, streams = cf.unpack_data(encode_data(rc, data))
    n_ticks, cells = len(rc.ticks), len(rc.walk_cells)
    checkpoints = [c[0] for c in header["checkpoints"]]
    assert [c[0] for c in header["unknown_checkpoints"]] == checkpoints
    for group in ("A", "B"):
        got = cf.decode_unknown(streams[f"unknown_{group.lower()}"], n_ticks, cells, checkpoints)
        assert np.array_equal(np.array(got, bool), rc.unknown[group]), group
        assert rc.unknown[group].any(), f"the toy round should exercise {group}'s unknown"


def test_a_row_without_unknown_reads_as_before(round_control):
    rc, data = round_control
    header, streams = cf.unpack_data(encode_data(dataclasses.replace(rc, unknown=None), data))
    assert "unknown_a" not in streams and "unknown_checkpoints" not in header
    n_ticks, cells = len(rc.ticks), len(rc.walk_cells)
    assert np.array_equal(np.array(cf.decode_states(streams["states"], n_ticks, cells)), rc.states)
```

In `test_what_each_team_knew_round_trips_as_optional_streams`, the last block computes `plain` with `knowledge=False` and asserts three streams. Change the encode line to `encode_data(dataclasses.replace(plain, unknown=None), data)` so it still checks "a row computed without the optional streams". Change no assertion.

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_format.py -q`
Expected: FAIL with `KeyError: 'unknown_checkpoints'` and `AttributeError: ... no attribute 'decode_unknown'`.

- [ ] **Step 3: Implement**

In `encode_data`, after the `if rc.knew_states:` block and before `return`:

```python
    if rc.unknown:
        # Each side group's unknown (docs/map-control-unknown-plan.md): optional streams, one mask a tick
        # in encode_masks' format with a single slot, so rows without them read as before.
        offsets = {}
        for group in ("A", "B"):
            name = f"unknown_{group.lower()}"
            streams[name], offsets[name] = encode_masks(rc.unknown[group][:, None, :], checkpoints)
        header["unknown_checkpoints"] = [[t, a, b] for t, a, b in
                                         zip(checkpoints, offsets["unknown_a"], offsets["unknown_b"])]
```

In `control_format.py`, give `decode_masks` a `slots: int = SLOTS` parameter and use it in place of `SLOTS` in its body (`cur = [[0] * cells for _ in range(slots)]`). Then add:

```python
def decode_unknown(stream: bytes, ticks: int, cells: int, checkpoints: list[int]) -> list[list[int]]:
    """A side group's unknown per tick (the optional `unknown_a` / `unknown_b` streams; 1 where an enemy of
    that group could be): decode_masks with one slot (reference decoder; the viewer's is replay_control.js)."""
    return [frame[0] for frame in decode_masks(stream, ticks, cells, checkpoints, slots=1)]
```

- [ ] **Step 4: Run the format, store and task tests**

Run: `PY -m pytest tests/replays/test_control_format.py tests/replays/test_control_store.py tests/replays/test_control_task.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

Message file:
```
Map control: store each team's unknown as optional streams

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```
Run: `git add app/control/encode.py app/replays/control_format.py tests/replays/test_control_format.py` then `git commit -F <message file>`.

---

### Task 5: The viewer: decode, hatch, toggle

**Files:**
- Modify: `webapp/app/static/js/replay_control.js` (add `Cursor.prototype.unknown` after `Cursor.prototype.knew`; add `paintUnknown`, `HATCH_PX`, `HATCH_ALPHA` after `paintStates`; export them in `api`).
- Modify: `webapp/app/static/js/replay.js`: the constructor wiring near line 781; `refreshControl`; `showControlViews`; `drawControl`.
- Modify: `webapp/app/templates/replays/_player.html` (the toggle after the knew picker; a legend key).
- Modify: `webapp/app/static/css/style.css` (after line 1904: `.replay-key-ctl-unknown`).
- Test: `webapp/tests/replays/test_control_viewer.py` (add).

**Interfaces:**
- Consumes: the streams and header from Task 4.
- Produces (JS): `Cursor.prototype.unknown(name: "unknown_a"|"unknown_b", i) -> Uint8Array | null`; `paintUnknown(rgba, size, walk, a, b, colors, show: "A"|"B"|"both") -> rgba`; `ReplayViewer.unknownView` (`"both"|"A"|"B"|"off"`).

- [ ] **Step 1: Write the failing tests**

Add to `test_control_viewer.py`:

```python
def test_the_js_decoder_reads_each_teams_unknown(stored):
    _, streams = cf.unpack_data(gzip.compress(base64.b64decode(stored["raw"])))
    checkpoints = [c[0] for c in stored["header"]["checkpoints"]]
    expected = {name: cf.decode_unknown(streams[name], stored["ticks"], stored["header"]["cells"], checkpoints)
                for name in ("unknown_a", "unknown_b")}
    order = list(range(stored["ticks"]))
    shuffled = order[:]
    random.Random(5).shuffle(shuffled)
    body = """
      function run(p) {
        const parsed = C.parse(C.base64Bytes(p.raw)), cur = new C.Cursor(parsed), out = {unknown_a: {}, unknown_b: {}};
        for (const i of p.order) for (const name of ["unknown_a", "unknown_b"]) out[name][i] = Array.from(cur.unknown(name, i));
        out.none = cur.unknown("unknown_c", 0);
        return out;
      }"""
    for seq in (order, shuffled):
        got = run_node(body, {"raw": stored["raw"], "order": seq})
        for name in ("unknown_a", "unknown_b"):
            for i in seq:
                assert got[name][str(i)] == expected[name][i], f"{name} tick {i}"
        assert got["none"] is None


def test_unknown_is_hatched_in_the_enemys_colour_and_crosshatched_where_both():
    body = """
      function run(p) {
        const size = 512, rgba = new Uint8Array(size * size * 4), colors = {a: [200, 10, 10], b: [10, 10, 200]};
        const walk = Int32Array.from([0, 1, 2]);                 // three cells along the top row, 4 px each
        const a = Uint8Array.from([1, 0, 1]), b = Uint8Array.from([0, 1, 1]);
        const px = (x, y) => Array.from(rgba.subarray((y * size + x) * 4, (y * size + x) * 4 + 4));
        const out = {};
        for (const show of ["both", "A", "B"]) {
          C.paintUnknown(rgba, size, walk, a, b, colors, show);
          out[show] = [];
          for (let y = 0; y < 4; y++) for (let x = 0; x < 12; x++) out[show].push(px(x, y));
        }
        C.paintUnknown(rgba, size, walk, null, null, colors, "both");
        out.blank = !Array.from(rgba).some(v => v);
        out.hatch = C.HATCH_PX;
        return out;
      }"""
    got = run_node(body, {})
    n = got["hatch"]

    def cell(show, k):
        return [got[show][y * 12 + x] for y in range(4) for x in range(4 * k, 4 * k + 4)]

    def colours(pixels):
        return {tuple(p[:3]) for p in pixels if p[3]}

    assert colours(cell("both", 0)) == {(10, 10, 200)}, "A's unknown in B's colour"
    assert colours(cell("both", 1)) == {(200, 10, 10)}, "B's unknown in A's colour"
    assert colours(cell("both", 2)) == {(10, 10, 200), (200, 10, 10)}, "both: cross-hatched"
    lit = [p for p in cell("both", 0) if p[3]]
    assert 0 < len(lit) < 16 and len({p[3] for p in lit}) == 1, "a hatch, not a fill, at one opacity"
    assert colours(cell("A", 1)) == set() and colours(cell("B", 0)) == set(), "the toggle shows one team"
    assert got["blank"] and n >= 3
```

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_viewer.py -q -k "unknown"`
Expected: FAIL with `cur.unknown is not a function` / `C.paintUnknown is not a function`.

- [ ] **Step 3: Implement the decoder and painter**

In `replay_control.js`, after `Cursor.prototype.knew`:

```js
  // A side group's unknown at tick i (the optional `unknown_a` / `unknown_b` streams: 1 where an enemy of
  // that group could be; docs/map-control-unknown-plan.md); null when the row has no such stream. One
  // slot in the coverage/control mask format, with its own checkpoint offsets.
  Cursor.prototype.unknown = function (name, i) {
    var stream = this.p[name], cps = this.p.header.unknown_checkpoints;
    if (!stream || !cps) return null;
    var col = name === "unknown_a" ? 1 : 2, cells = this.p.cells, width = (cells + 7) >> 3, best = 0;
    for (var c = 0; c < cps.length && cps[c][0] <= i; c++) best = c;
    this.uk = this.uk || {};
    var m = this.uk[name];
    if (!m || m.tick > i || cps[best][0] > m.tick + 1) {
      m = this.uk[name] = { tick: cps[best][0] - 1, pos: cps[best][col], cur: m ? m.cur : new Uint8Array(cells) };
    }
    while (m.tick < i) {
      var tick = m.tick + 1, pos = m.pos;
      if (this.isCp[tick] !== undefined) {
        for (var k = 0; k < cells; k++) m.cur[k] = stream[pos + (k >> 3)] >> (7 - (k & 7)) & 1;
        pos += width;
      } else {
        var r = readVarint(stream, pos), count = r[0], cell = -1;
        pos = r[1];
        for (var j = 0; j < count; j++) {
          r = readVarint(stream, pos);
          pos = r[1];
          cell += r[0] + 1;
          m.cur[cell] ^= 1;
        }
      }
      m.pos = pos;
      m.tick = tick;
    }
    return m.cur;
  };
```

After `paintStates`:

```js
  // Each side group's unknown as a hatch in the enemy's colour (docs/map-control-unknown-plan.md): A's
  // unknown in B's colour along "/" lines, B's in A's colour along "\" lines, so a cell in both teams'
  // unknown is cross-hatched. `show` is "A", "B" or "both"; a or b may be null (no stream).
  var HATCH_PX = 4, HATCH_ALPHA = 0.55;

  function paintUnknown(rgba, size, walk, a, b, colors, show) {
    var px = size / GRID, alpha = Math.round(HATCH_ALPHA * 255);
    rgba.fill(0);
    for (var k = 0; k < walk.length; k++) {
      var inA = !!(a && a[k]) && show !== "B", inB = !!(b && b[k]) && show !== "A";
      if (!inA && !inB) continue;
      var cell = walk[k], cx = (cell % GRID) * px, cy = Math.floor(cell / GRID) * px;
      for (var y = cy; y < cy + px; y++) {
        for (var x = cx; x < cx + px; x++) {
          var rgb = null;
          if (inA && (x + y) % HATCH_PX === 0) rgb = colors.b;
          else if (inB && ((x - y) % HATCH_PX + HATCH_PX) % HATCH_PX === 0) rgb = colors.a;
          if (!rgb) continue;
          var o = (y * size + x) * 4;
          rgba[o] = rgb[0]; rgba[o + 1] = rgb[1]; rgba[o + 2] = rgb[2]; rgba[o + 3] = alpha;
        }
      }
    }
    return rgba;
  }
```

In `api`, add `paintUnknown: paintUnknown, HATCH_PX: HATCH_PX, HATCH_ALPHA: HATCH_ALPHA`.

- [ ] **Step 4: Run the decoder and painter tests**

Run: `PY -m pytest tests/replays/test_control_viewer.py -q`
Expected: all PASS, 0 skipped (node is installed).

- [ ] **Step 5: Wire the toggle and the layer**

In `_player.html`, after the `</select></label>` that closes the knew picker:

```html
        <label class="replay-control-view" data-replay-control-unknown-wrap hidden>unknown
          <select data-replay-control-unknown aria-label="Whose unknown to hatch: where that team's enemies could be">
            <option value="both" selected>both teams</option>
            <option value="A" data-unknown-group="A">side A's</option>
            <option value="B" data-unknown-group="B">side B's</option>
            <option value="off">off</option>
          </select></label>
```

In the control legend, before the `replay-control-legend-note` span:

```html
        <span class="replay-key replay-key-ctl-unknown">Unknown (an enemy could be here; hatched in the enemy's colour)</span>
```

In `style.css`, after `.replay-key-ctl-contested::before`:

```css
.replay-key-ctl-unknown::before { border-radius: 2px; background: repeating-linear-gradient(45deg, color-mix(in srgb, var(--replay-team-1) 70%, transparent) 0 1px, transparent 1px 4px), repeating-linear-gradient(-45deg, color-mix(in srgb, var(--replay-team-2) 70%, transparent) 0 1px, transparent 1px 4px); border: 1px solid var(--border-strong); }
```

In `replay.js`'s constructor, after the `this.ui.controlView` block (line ~791):

```js
    this.unknownView = "both";
    this.ui.controlUnknown = q("[data-replay-control-unknown]");
    this.ui.controlUnknownWrap = q("[data-replay-control-unknown-wrap]");
    if (this.ui.controlUnknown) {
      this.ui.controlUnknown.addEventListener("change", function () {
        self.unknownView = self.ui.controlUnknown.value;
        self.controlPaint = null;
        self.draw();
      });
    }
```

In `refreshControl`, after the line hiding `controlViewWrap`:

```js
    if (this.ui.controlUnknownWrap && !this.layers.control) this.ui.controlUnknownWrap.hidden = true;
```

At the end of `showControlViews`:

```js
    var uwrap = this.ui.controlUnknownWrap, uselect = this.ui.controlUnknown;
    if (uwrap && uselect) {
      var hasUnknown = !!(value && value.status === "ok" && value.parsed.unknown_a && value.parsed.unknown_b);
      uwrap.hidden = !hasUnknown || !this.layers.control;
      Array.prototype.forEach.call(uselect.querySelectorAll("[data-unknown-group]"), function (option) {
        option.textContent = self.groupName(option.getAttribute("data-unknown-group")) + "'s";
      });
    }
```

In `drawControl`, add `this.unknownView` to the `key` array. Inside `if (this.controlPaint !== key)`, after the `if (this.layers.control) { ... paintStates ... }` block:

```js
      var showUnknown = this.layers.control && this.unknownView !== "off" && parsed.unknown_a && parsed.unknown_b;
      if (showUnknown) {
        var uk = this.controlCanvas("unknown");
        uk.data = uk.data || uk.ctx.createImageData(CONTROL_PX, CONTROL_PX);
        C.paintUnknown(uk.data.data, CONTROL_PX, parsed.walk, value.cursor.unknown("unknown_a", tick),
          value.cursor.unknown("unknown_b", tick), colors, this.unknownView);
        uk.ctx.putImageData(uk.data, 0, 0);
      }
```

In the drawing block, after `if (this.layers.control) ctx.drawImage(this.controlCanvas("states").canvas, ...)`:

```js
    if (this.layers.control && this.unknownView !== "off" && parsed.unknown_a && parsed.unknown_b) {
      ctx.drawImage(this.controlCanvas("unknown").canvas, 0, 0, size, size);
    }
```

- [ ] **Step 6: Run the viewer, views and preview-page tests**

Run: `PY -m pytest tests/replays/test_control_viewer.py tests/replays/test_replay_viewer.py tests/replays/test_control_views.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

Message file:
```
Map control viewer: hatch each team's unknown in the enemy's colour, with a toggle

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```
Run: `git add app/static/js/replay_control.js app/static/js/replay.js app/templates/replays/_player.html app/static/css/style.css tests/replays/test_control_viewer.py` then `git commit -F <message file>`.

---

### Task 6: The small-sample check on real rounds

**Files:**
- Create: `webapp/scripts/preview_control_live.py`
- Modify: `docs/map-control-unknown-plan.md` (append a "What the sample showed" section)

**Interfaces:**
- Consumes: `compute_task` (`app/control/task.py`); `replay_control_views.player_tables`, `_match_totals`, `match_heatmap`, `read_walk`, `RoundSummaries`, `HEATMAP_VIEWS`; `render_replay_standalone.py --blobs <dir> --out <page>`.
- Produces: `%TEMP%\valo-replay\<uuid>-<tag>\preview.html`, and the measured stream sizes printed to the terminal.

- [ ] **Step 1: Write the script**

Create `webapp/scripts/preview_control_live.py`:

```python
"""Computes map control for a few rounds of a live replay and writes the local preview page, from the
friends site's public responses only: no database, no writes anywhere but %TEMP%.

    .\\.venv\\Scripts\\python.exe scripts\\preview_control_live.py <match uuid> <round> [<round> ...] [--tag NAME]

The replay page embeds what the engine's link needs (`replay-data`: clock offset, side_to_team, the
players' side groups, each round's db_deaths), and `/replays/<uuid>/<n>.json` is the stored round blob
as gzip. The rounds are computed with this checkout's engine, so a branch's engine can be looked at
before anything is stored (docs/map-control-unknown-plan.md: check a small sample before recomputing).
The page names real players: keep it local.
"""

from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.control.task import compute_task  # noqa: E402

SITE = "https://valowithfriendstracker.onrender.com"


def fetch(url: str) -> bytes:
    """The response's bytes as sent (a round blob is its stored gzip)."""
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept-Encoding": "gzip"}), timeout=120) as r:
        return r.read()


def page_data(uuid: str) -> dict:
    raw = fetch(f"{SITE}/replays/{uuid}")
    html = (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode("utf-8")
    found = re.search(r'<script id="replay-data" type="application/json">(.*?)</script>', html, re.S)
    if found is None:
        raise SystemExit(f"no replay data on {SITE}/replays/{uuid}")
    return json.loads(found.group(1))


def link_for(ctx: dict, n: int) -> dict:
    """app/services/replay_control.round_link, from the page's data."""
    from app.scoring.plant_window import attacking_team

    match, attacking = ctx["match"], attacking_team(n)
    sides = {}
    for slot, p in sorted(ctx["players"].items(), key=lambda kv: int(kv[0])):
        team = match["side_to_team"].get(p["side"])
        if team is not None and attacking is not None:
            sides[str(slot)] = "attack" if team == attacking.value else "defense"
    offset = match.get("clock_offset") or 0.0
    deaths = sorted([d["slot"], round(float(d["t_db"]) - offset, 3)]
                    for d in ctx["rounds"][str(n)].get("db_deaths", []) if d.get("slot") is not None)
    return {"sides": sides, "db_deaths": deaths}


def main(argv: list[str] | None = None) -> int:
    from app.replays import control_format as cf
    from app.services import replay_control_views as views

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("uuid")
    parser.add_argument("rounds", type=int, nargs="+")
    parser.add_argument("--tag", default=f"rev{cf.CONTROL_REVISION}", help="the output folder's suffix")
    args = parser.parse_args(argv)
    out = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "valo-replay" / f"{args.uuid}-{args.tag}"
    out.mkdir(parents=True, exist_ok=True)
    for f in out.iterdir():
        f.unlink()
    ctx = page_data(args.uuid)
    tasks = []
    for n in args.rounds:
        blob = fetch(f"{SITE}/replays/{args.uuid}/{n}.json")
        (out / f"{n}.json.gz").write_bytes(blob)
        tasks.append({"key": n, "map": ctx["match"]["map"], "blob": blob, "link": link_for(ctx, n)})
    print(f"{ctx['match']['map']}: rounds {args.rounds} at revision {cf.CONTROL_REVISION}", flush=True)
    started, results = time.time(), {}
    with multiprocessing.Pool(len(tasks)) as pool:
        for r in pool.imap_unordered(compute_task, tasks):
            print(f"  r{r['key']}: {r['status']} {r['seconds']:.0f}s {r.get('error', '')[:600]}", flush=True)
            results[r["key"]] = r
    print(f"computed in {time.time() - started:.0f}s", flush=True)
    loaded, walks = views.RoundSummaries(), {}
    for n, r in sorted(results.items()):
        if r["status"] != "ok":
            continue
        (out / f"{n}.control.bin").write_bytes(r["data"])
        header, streams = cf.unpack_data(r["data"])
        sizes = ", ".join(f"{k} {len(v) // 1024} KB" for k, v in streams.items())
        print(f"  r{n}: {len(r['data']) // 1024} KB gzipped; raw streams: {sizes}", flush=True)
        loaded.summaries[n] = cf.unpack_summary(r["summary"])
        walks[n] = views.read_walk(r["data"])

    class _Replay:                     # what player_tables reads from a Replay
        round_count = max(args.rounds)

    tables = views.player_tables(_Replay, loaded)
    tables["rounds"] = {k: v for k, v in tables["rounds"].items() if int(k) in args.rounds}
    (out / "control_players.json").write_text(json.dumps(tables), encoding="utf-8")
    totals = views._match_totals(loaded.summaries, walks, max(loaded.summaries)) if loaded.summaries else None
    for view in views.HEATMAP_VIEWS:
        body = (views.match_heatmap(totals, view, ctx["match"]["side_to_team"]) if totals
                else {"status": "missing", "view": view, "sections": []})
        (out / f"control_heatmap_{view}.json").write_text(json.dumps(body), encoding="utf-8")
    ctx["match"]["rounds"] = args.rounds
    (out / "context.json").write_text(json.dumps(ctx), encoding="utf-8")
    page = out / "preview.html"
    subprocess.check_call([sys.executable, str(WEBAPP_ROOT / "scripts" / "render_replay_standalone.py"),
                           "--blobs", str(out), "--out", str(page)], cwd=WEBAPP_ROOT)
    print(f"PAGE {page}")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
```

- [ ] **Step 2: Run it on rounds 1-2 and open the page**

Run: `PY scripts/preview_control_live.py 6f12db3e-b2db-4bca-96e4-a837c85ba5a6 1 2 --tag unknown`
Expected: `r1: ok`, `r2: ok`, two size lines that include `unknown_a` and `unknown_b`, and a `PAGE` line.
Open it with `PY -c "import os; os.startfile(r'<the PAGE path>')"`.

- [ ] **Step 3: Check the numbers that motivated the change**

With the Write tool, put this one-off check in the session's scratchpad as `start_ground.py` (not in the repo). It reads the files the script wrote:

```python
"""How much of the defenders' (side B's) barrier-drop ground they still hold, every 2 s of round 1."""
import os
import sys

sys.path.insert(0, ".")
from app.replays import control_format as cf  # noqa: E402

path = os.path.join(os.environ["TEMP"], "valo-replay", "6f12db3e-b2db-4bca-96e4-a837c85ba5a6-unknown", "1.control.bin")
header, streams = cf.unpack_data(open(path, "rb").read())
frames = cf.decode_states(streams["states"], len(header["ticks"]), header["cells"])
held = {cf.STATE_NAMES.index(n) for n in ("b_passive", "b_safe", "b_active")}
start = [i for i, v in enumerate(frames[1]) if v in held]
for k, units in enumerate(header["ticks"]):
    if units % (2 * header["hz"]) == 0 and units <= 26 * header["hz"]:
        print(f"{units / header['hz']:5.1f}s defenders keep {sum(frames[k][i] in held for i in start)} of {len(start)}")
```

Run: `PY <scratchpad>/start_ground.py` from `webapp\`.

Expected: the defenders' count falls steadily as unknown walks in. No collapse at 14-18 s followed by a jump back at 20 s (the earlier run's numbers were 1,067 at 18 s, 2,516 at 20 s and 506 at 24 s).

- [ ] **Step 4: Record what the sample showed**

Append to `docs/map-control-unknown-plan.md`:

```markdown
## What the sample showed (Ascent 6f12db3e, rounds 1-2, computed locally, no DB writes)

- Compute: r1 <s> s, r2 <s> s (revision 3 before unknown: 82 s and 103 s).
- Size: r1 <KB> KB gzipped (unknown_a <KB> KB, unknown_b <KB> KB raw); r2 <KB> KB.
- Defenders' start ground kept, round 1: <the printed series>.
```

Fill each `<...>` with the numbers printed in Steps 2-3. Don't round them into a claim.

- [ ] **Step 5: Commit and hand over for the look**

Message file:
```
Map control: preview a live replay's rounds locally; the unknown sample's numbers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```
Run: `git add scripts/preview_control_live.py ../docs/map-control-unknown-plan.md` then `git commit -F <message file>`.

Then stop and ask the user to check, at the start of round 1:
1. The defenders' spawn stays held until unknown could have walked there.
2. The ground to the left inside B main is held after the Yoru's look, until unknown walks back in.
3. The Miks glance at 18-20 s claims only what it saw.
4. The hatches and the toggle read clearly.

**Do not push, merge, or recompute anything until the user says the sample looks right.**
