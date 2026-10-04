# Timing Gaps, Plan 1: Engine, Chokes, Detector and Storage

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect timing gaps (predicted gaps and back-shots) for every replay round and store them, built on
the control engine's unknown, with the new locating rules added to that unknown.

**Architecture:** The engine's `Unknown` gains route history (a parent per arrival, an append-only log per
team, a choke sequence per entry) and five new locating events (kill, plant, footsteps, gunfire, gun damage),
bumping `CONTROL_REVISION` to 5. `compute_round` takes an optional observer that receives one plain
`TickRecord` per tick. A new package `app/gaps/` consumes those records: `detect.py` finds predicted gaps
online, `backshots.py` finds back-shots after the last tick, `cache.py` stores the records locally so the
detector can be re-run alone. Rows go to two new tables written by `scripts/compute_control.py`.

**Tech Stack:** Python 3.13, numpy, scipy, SQLAlchemy 2.0, Alembic, pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-timing-gaps-design.md` (read it first; this plan argues from it).
Also read `docs/map-control-unknown-plan.md` (the engine's unknown, which this plan extends).

**Scope.** This is plan 1 of 3. It covers spec sections 1-7 and the preview. Plan 2 (written after this
plan's preview) covers section 8, the viewer layer, and the tagger's choke mode from section 2. Plan 3 covers
section 9, the pattern page. Until plan 2, chokes are corrected by editing the asset JSON by hand (the merge
rules in Task 2 already keep hand edits).

## Global Constraints

- Work in the worktree `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\.claude\worktrees\timing-gaps`
  on branch `worktree-timing-gaps`. Never edit the main checkout.
- Python: `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv\Scripts\python.exe` (the
  worktree has no venv). Run every command from the worktree's `webapp\` folder. Below, `PY` means that
  interpreter.
- `CONTROL_REVISION` goes from 4 to 5 in Task 5, once. Later engine constant changes in this plan re-pin
  `PINNED[5]` in place (it is unreleased until the full recompute).
- Spec constants, verbatim: `MIN_UNSEEN_S` 5 s; `CLOSE_AFTER_S` 5 s; `KILL_AREA_M`, `PLANT_AREA_M`,
  `SHOT_AREA_M` 5 m; `DAMAGE_AREA_M` 10 m; `FOOTSTEP_AREA_M` 10 m; `FLICKER_S` 1 s; `RESULT_WINDOW_S` 3 s;
  `AUDIBLE_WINDOW_S` 0.5 s; `AUDIBLE_MPS` 4.5 m/s; `SHOT_LOOKBACK_S` 0.5 s; "behind" = more than 120 degrees
  off facing.
- `victim_side` uses the engine's spelling: `attack` / `defense`.
- No DB writes outside tests until Task 11's final step, which the owner runs. No full recompute before the
  owner approves the preview.
- The repo is public: commit no credentials, no real players' names, no preview output. Previews go under
  `%TEMP%\valo-replay\`.
- Commit messages: write them to a scratch file with the Write tool, then `git commit -F <file>` (never a
  heredoc). End every message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Existing tests:** a pre-existing test that fails because of this change is **not** rewritten unless this
  plan names it as replaced. Stop, and report the failing assertion and why to the owner before touching it.
  Named replacements in this plan: `PINNED` in `tests/replays/test_control_format.py` (Task 5) and the
  fixture `tests/fixtures/control/reference_flat.json` (Task 5).

## Review Focus

1. **The route bookkeeping must not move the unknown.** Task 4 changes only bookkeeping; the flat reference
   rounds must stay byte-identical through Task 4 and change only in Task 5. Test in Task 4:
   `test_route_history_leaves_the_unknown_unchanged` plus the existing `test_control_reference.py`.
2. **A shot that is also the back-shot's own sound** must not cancel the back-shot (spec section 6). Test in
   Task 8: `test_the_shooters_own_gunfire_does_not_cancel_the_backshot`.
3. **A revived enemy** must not restart from their team's barrier ground (today's engine does, because a new
   region always starts from `_start`). Test in Task 5: `test_a_revived_enemy_restarts_where_they_stand`.
4. **Ticks after `t_decided`** (playback runs on to `t_end`) must create no gaps and must close open ones at
   `t_decided`. Test in Task 7: `test_nothing_is_detected_after_the_round_is_decided`.
5. **Two rounds of one replay** must never share a tick-cache file. Test in Task 6:
   `test_cache_keys_differ_by_round`.

## Revisions from the plan review (applied 2026-10-02, AFK run W0)

The independent review (`2026-10-02-timing-gaps-engine-and-detector-review.md`, findings R1-R20 below; not the
run register's R ids) was applied as follows. Every finding was accepted. **Where a revision and a task's code
snippet disagree, the revision wins**; the snippets are a starting point, and each task's tests must include the
regression tests the revision names. Each task's own "Review fixes" line says which revisions apply to it.

**Run overrides (AFK run, 2026-10-02).** Work in `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\.worktrees\afk-2026-10-02-timing-gaps`
on branch `afk/2026-10-02-timing-gaps`, not `.claude\worktrees\timing-gaps`. Owner stop points become: Task 1
Step 4 a provisional hearing table (`"PROVISIONAL": "D<n>"` key in `hearing.json`) built on and flagged; Task 2
Step 6's 10-80 range and Task 4 Step 7's 20% budget are flagged for the owner and the build continues; Task 11
Steps 3 and 5 are left for the owner and never run.

- **R1 (blocker), Task 10.** `CONTROL_TABLES` in `tests/replays/test_control_store.py` is extended with
  `ReplayRoundGapRun.__table__` and `ReplayGap.__table__` (a named replacement: every existing assertion stays
  unchanged), and `tests/replays/test_control_store.py` joins Task 10's Step 6 command.
- **R2 (blocker), Tasks 1 and 9.** Task 1 Step 1 runs `gun_names.py` from the **main checkout's** `webapp\`
  folder (its `.env` points at local Postgres on 5433; never read or print `.env`), with the script's
  `sys.path` line pointing at that folder. Task 9 Step 7 rehearses the migration only against the disposable
  database `valomaths_gaps_test` on localhost:5433, setting `DATABASE_URL=postgresql+psycopg2://valorant:valorant@localhost:5433/valomaths_gaps_test`
  inline for each of the three alembic commands (the public local credentials of `webapp/docker-compose.yml`).
  It does not start Docker. Never run alembic against any other database.
- **R3, Task 2.** A wider span counts only when every along-axis cell for steps `1..WIDEN_CELLS` in that
  direction is walkable (no jumping a wall corner). Keep the one-door assertion; add
  `test_a_wall_corner_is_not_a_choke` (the door hall's wall corners give no diagonal choke).
- **R4, Task 2.** `merge` keeps an auto choke whose cells detection finds again with its id and name; new ids
  come from an asset-level high-water mark `next_id` saved in the JSON (`{"version": 1, "map", "next_id",
  "chokes"}`), so ids are never reused, even after every choke was dropped. `merge(existing, detected,
  next_id) -> (chokes, next_id)`; `load` returns `(chokes, next_id)` or None; `save(name, chokes, next_id)`.
  Tests: identical detection twice keeps ids; drop-all then re-add gets a fresh id.
- **R5, Tasks 6 and 10.** The tick cache is keyed by an **engine key**: a 16-hex hash of the control
  fingerprint, the map's choke asset hash and the hearing hash (R6), not by the control fingerprint alone, so
  a choke or hearing edit misses the cache and goes through the engine. `cache_path(replay_id, round_number,
  engine_key, directory)`. `GAPS_REVISION` is deliberately not in the key (re-running the detector alone is
  the cache's point). Test: a choke edit followed by a gaps-only run does not read the old file.
- **R6, Task 10.** `replay_gaps.hearing_hash()` (16 hex of `app/control/hearing.json`'s bytes, read as a file:
  no `app.control` import) goes into `gap_fingerprint` and the engine key. Test: a hearing-only edit changes
  the gap fingerprint with no revision change.
- **R7, Task 7.** Pre-event judging, in the detector: at each tick, an enemy with a locating event in
  `rec.events` is judged (exposure, qualifying, opening, `stood`) against the **previous tick's** entry array
  for that enemy (the detector keeps a copy) and against the locating history **before** this tick's events;
  this tick's events are appended to the history only after the victims are processed. Back-shots already use
  `inclusive=False`. Tests: a same-tick damage event that opens a gap; a kill at the moment of `stood` still
  counts as `killed`.
- **R8, Task 5.** `_locating` returns **every** event in the interval (all kinds, time order), the first tick
  included (interval `(-inf, t]` when there is no previous tick). The region collapse uses the latest event
  (ties: smaller area); `located` and `events` record all of them. A sighting at the same tick is recorded
  after them. Tests: damage plus gunfire between two ticks both recorded; an event at time 0 recorded.
- **R9, Task 7.** An open gap's `t_last_exposed` is updated only from enemies already on its candidate list;
  new candidates join only by qualifying. Test: a non-candidate with the same sequence during the close timer
  does not keep the gap open.
- **R10, Task 7.** At each tick, open gaps are first expired against their previous `t_last_exposed` (close at
  `t_last_exposed + CLOSE_AFTER_S` if `t` is at or past it, integrating qualified time only to that close),
  and only then is the tick's exposure consumed. Tests: a sparse timeline (exposed 0, empty 1, exposed 6 gives
  two gaps, the first closing at 5) and a return exactly at expiry.
- **R11, Task 7.** `open_timing` is a candidate cause only when no node of the route was observed (released)
  since the enemy was last located. `victim_turned` and `victim_moved` are judged independently of each other:
  `victim_moved` when the victim's node changed since the last tick and a route node is exposed now that was
  not exposed from the old position (exposure is new, not only rotated); `victim_turned` when the route's
  nodes were exposed last tick and the victim's facing changed. Tie order as in the spec. Tests: a
  release-delayed arrival gives `route_released`; moving into a new line of sight gives `victim_moved`.
- **R12, Tasks 6 and 7.** `Tick` keeps `view[s]` (`active | passive` before Memory, presence included) beside
  `live`; `PlayerView` gains `view`. Attribution is by `view` first, then `utility`, overlap kept (a node in
  both is credited to view). `checked_at` and "victim sees an enemy" use `view`. `_reason` says `died` only
  when the observer is no longer alive (`rnd.alive`); a live observer without a position is `other`; `smoked`
  only when a smoke that started since the last tick blocks the released node from the observer (cast with
  and without it). Tests: overlap credited to view; missing-position observer not `died`; an unrelated new
  smoke is not `smoked`.
- **R13, Tasks 6 and 7.** "A clear line" is a full-circle `geometry.cast` from the victim's real position at
  their eye height (`PlayerView.eye_z`, the engine's `Tick._eye` value, None on a flat map) with the tick's
  smokes and `own=p.node`, not `seen_from` over static rows. Computed only for victims who have any enemy
  unknown this tick. Tests: a raised player and a within-cell corner position change what is exposed. The
  cost is measured in Task 11's preview.
- **R14, Task 5.** A revived enemy with no sample at revival keeps a pending new-life origin; the restart
  (region at their position, `located`, a `revived` event) happens at the first sample of the new life. Test:
  missing samples across a revival, then the 5 s wait counted from the first sample.
- **R15, Tasks 4 and 5.** Every source (barrier ground, own position push, sighting cell, area centre) gets a
  log entry even when the source node is observed (outside `room`); descendants whose spread parent is a
  source node use that source's entry. Test: a watched centre with reachable unobserved neighbours roots
  their routes at the centre's entry; clearing and re-entry keep the old trace intact.
- **R16, Task 8.** Peak speed is the maximum over every unbroken path piece from the last locating (or round
  start) to `t0`; the candidate distance is from the shooter's real position to `geo.centres[shooter_node]`
  (the spot), separate from `distance_m` (spot to victim). Tests: an early sprint is the peak; the two
  distances differ.
- **R17, Task 10.** `plan_gaps(db, planned_control, every, retry_failed=False)` takes only rounds with a
  matching **ok** control row (status ok, current fingerprint); it loads gap-run status, skips a current
  failed gap run unless `retry_failed`. Tests: fresh ok control; current failed control (not gaps-only);
  current failed gap run (skipped, then retried with `retry_failed`).
- **R18, Task 10.** The cache writer runs behind a guard observer: an exception in the observer or in
  `close()` is caught, stops further cache writes, deletes any partial file, and becomes the gap run's
  failure; control's result is kept. Tests: an observer exception and a `close()` exception both leave
  control ok and the gap run failed.
- **R19, Task 10.** `run` binds `rows = result.get("gaps", {}).get("rows", [])`; it counts gap-run failures
  and gap-store outcomes other than `stored` separately, prints them, and the command returns nonzero when
  any happened. Test: a failed gap run makes `main` return nonzero.
- **R20, Tasks 5, 6 and 10.** Missing-data cases are counted once per case and slot per round (a set of
  `(case, slot)` pairs folded into the counts), not per check: `speed across a track break or missing
  sample`, `locating event without a position`, plus the plan's shot and plant cases. The tick cache stores
  the compute-time `missing_inputs` (`Writer.close(missing)`), and both gap paths report those counts in the
  run's notes. Test: live and cache notes are equal.

---

### Task 1: Hearing figures (research, then owner check)

No code. The figures feed Task 5's constants.

Review fixes: R2 (run `gun_names.py` from the main checkout's `webapp\`).

**Files:**
- Create: `webapp/app/control/hearing.json`

**Interfaces:**
- Produces: `hearing.json` with exactly this shape (values are what this task finds):

```json
{
  "footstep_range_m": 0.0,
  "default_gun_m": 0.0,
  "guns": {"<gun name exactly as the blob's shot rows spell it>": 0.0},
  "sources": {"footstep_range_m": "<URL>", "guns": "<URL(s)>", "gun_names": "<how the names were listed>"}
}
```

- [ ] **Step 1: List the gun names the blobs use.** Shot rows carry `gun` = the equippable's name
  (`app/replays/extras.py` line ~1138). Start local Postgres (`docker compose -p valomaths-private up -d`
  from `webapp\`), then write `%TEMP%\valo-replay\gun_names.py`:

```python
"""Every distinct `gun` in the shot rows of the local DB's replay blobs, with counts."""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(r"C:\Users\User\Documents\GitHub\valo-with-friends-tracker\.claude\worktrees\timing-gaps\webapp")))
from app.db import SessionLocal
from app.models.replay import ReplayRound
from app.replays import format as fmt

counts = Counter()
s = SessionLocal()
for (data,) in s.query(ReplayRound.data).yield_per(50):
    for e in fmt.decode_blob(data).get("util") or []:
        if e.get("k") == "shot":
            counts[e.get("gun")] += 1
for name, n in counts.most_common():
    print(f"{n:8d}  {name!r}")
```

  Run: `PY %TEMP%\valo-replay\gun_names.py`. Expected: a list of names (and a count for `None`).

- [ ] **Step 2: Source the ranges.** Use the Valorant wiki (valorant.fandom.com, the owner's named source):
  the spike page for the explosion radius (spec: footstep range equals it), and each weapon page for how far
  its fire is heard, silenced guns included. Record each URL. `default_gun_m` is the largest gun range.
  A name from Step 1 that no page covers gets `default_gun_m`, and is listed in `sources.gun_names`.
- [ ] **Step 3: Write `webapp/app/control/hearing.json`** in the shape above.
- [ ] **Step 4: STOP for the owner.** Show the owner the table (name, metres, source) and ask them to check
  it against the buy menu. Apply their corrections. Do not start Task 5 before they approve. (Tasks 2-4 do
  not depend on this and may proceed.)
- [ ] **Step 5: Commit**

```
git add app/control/hearing.json
git commit -F <msg file>   # "Hearing ranges for the timing-gaps locating rules"
```

---

### Task 2: Chokes

Review fixes: R3, R4.

**Files:**
- Create: `webapp/app/replays/choke_assets.py` (standard library only: the web app and the stdlib-only
  `app/replays` package may import it; `tests/replays/test_control_isolation.py` forbids the web app loading
  `app.control`)
- Create: `webapp/app/control/chokes.py` (numpy: detection and the per-node map)
- Create: `webapp/scripts/build_chokes.py`
- Create: `webapp/app/static/data/control/<Map>.chokes.json` (one per map, generated in Step 7)
- Test: `webapp/tests/replays/test_gaps_chokes.py`

**Interfaces:**
- Consumes: `Geometry` (`walk` GRID x GRID bool, `cell_m`, `specials`, `heights`, `node_cell`, `n`,
  `cell_of_px`, `px_of_uv`, `name`); `app.replays.format.STATIC_DIR`.
- Produces, in `app/replays/choke_assets.py`:
  - `ASSET_DIR` (= `format.STATIC_DIR / "data" / "control"`, the same folder as `geometry.ASSET_DIR`)
  - `@dataclass Choke(id: int, name: str, cells: list[int], source: str = "auto", deleted: bool = False)`
  - `merge(existing: list[Choke], detected: list[list[int]]) -> list[Choke]`
  - `load(name: str, asset_dir: Path = ASSET_DIR) -> list[Choke] | None`
  - `save(name: str, chokes: list[Choke], asset_dir: Path = ASSET_DIR) -> Path`
  - `asset_hash(name: str, asset_dir: Path = ASSET_DIR) -> str | None` (16 hex, None without an asset)
- Produces, in `app/control/chokes.py`:
  - `MAX_CHOKE_M: float = 8.0`
  - `detect(geo) -> list[list[int]]`: each detected choke's cells, sorted, in a stable order.
  - `node_chokes(geo, chokes: list[Choke] | None) -> np.ndarray` (int32, length `geo.n`, -1 off any choke)

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_gaps_chokes.py`:

```python
"""Chokes (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 2): narrow passages found in a
map's walkable grid, kept per map, with hand edits surviving a re-run."""

import numpy as np

from app.control import chokes as ck
from app.replays import choke_assets as ca
from tests.replays.control_toys import door_hall, midwall_hall, open_hall, two_rooms, uv


def test_an_open_hall_has_no_chokes():
    assert ck.detect(open_hall()) == []


def test_a_one_cell_door_is_one_choke_across_it():
    geo = door_hall()
    found = ck.detect(geo)
    assert len(found) == 1
    door = geo.cell_of_px(208, 292)          # the door: the gap under the wall's south end
    assert door in found[0]


def test_the_passage_under_a_wall_is_a_choke():
    geo = midwall_hall()
    found = ck.detect(geo)
    under = geo.cell_of_px(256, 272)
    assert any(under in cells for cells in found)


def test_a_special_link_is_a_choke_of_its_two_ends():
    geo = two_rooms([{"kind": "drop", "a": list(uv(170, 170)), "b": list(uv(310, 170)), "one_way": True}])
    found = ck.detect(geo)
    a, b = geo.cell_of_px(170, 170), geo.cell_of_px(310, 170)
    assert sorted([a, b]) in found


def test_merge_keeps_hand_edits_and_tombstones():
    hand = ca.Choke(1, "A Main", [10, 11], source="hand")
    gone = ca.Choke(2, "2", [50, 51], source="auto", deleted=True)
    old_auto = ca.Choke(3, "3", [90, 91], source="auto")
    merged = ca.merge([hand, gone, old_auto], [[10, 11], [50, 51], [200, 201]])
    by_id = {c.id: c for c in merged}
    assert by_id[1].name == "A Main" and by_id[1].source == "hand", "a hand choke is kept as it is"
    assert by_id[2].deleted, "a deleted choke stays deleted"
    assert 3 not in by_id, "an auto choke detection no longer finds is dropped"
    new = [c for c in merged if c.cells == [200, 201]]
    assert len(new) == 1 and new[0].id == 4 and new[0].name == "4", "new ids continue after the largest"
    assert not any(c.cells == [10, 11] and c.source == "auto" for c in merged), "no duplicate of a hand choke"
    assert not any(c.cells == [50, 51] and not c.deleted for c in merged), "a tombstone suppresses re-detection"


def test_save_load_and_hash(tmp_path):
    chokes = [ca.Choke(1, "1", [5, 6])]
    ca.save("Toy", chokes, tmp_path)
    assert ca.load("Toy", tmp_path) == chokes
    h = ca.asset_hash("Toy", tmp_path)
    assert len(h) == 16
    ca.save("Toy", [ca.Choke(1, "Door", [5, 6])], tmp_path)
    assert ca.asset_hash("Toy", tmp_path) != h, "renaming a choke changes the hash"
    assert ca.load("None", tmp_path) is None and ca.asset_hash("None", tmp_path) is None


def test_the_asset_folder_is_the_control_masks_folder():
    from app.control import geometry
    assert ca.ASSET_DIR.resolve() == geometry.ASSET_DIR.resolve()


def test_node_chokes_marks_cells_and_skips_deleted():
    geo = open_hall()
    out = ck.node_chokes(geo, [ca.Choke(1, "1", [5, 6]), ca.Choke(2, "2", [7], deleted=True)])
    assert out.dtype == np.int32 and len(out) == geo.n
    assert out[5] == 1 and out[6] == 1 and out[7] == -1 and out[8] == -1
    assert (ck.node_chokes(geo, None) == -1).all()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PY -m pytest tests/replays/test_gaps_chokes.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.control.chokes'`).

- [ ] **Step 3: Implement `webapp/app/replays/choke_assets.py`**

```python
"""Chokes as a per-map asset (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 2):
`<Map>.chokes.json` beside the control masks. Standard library only, so the web app can read chokes and
hash them without the engine. Detection is app/control/chokes.py.

A hand choke (`source: "hand"`) and a deleted one (a tombstone) survive re-detection; a detected choke that
overlaps either is dropped; an auto choke detection no longer finds is dropped; new ids continue after the
largest ever used."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app.replays import format as fmt

ASSET_DIR = fmt.STATIC_DIR / "data" / "control"


@dataclass
class Choke:
    id: int
    name: str
    cells: list[int]
    source: str = "auto"     # "auto" | "hand"
    deleted: bool = False


def merge(existing: list[Choke], detected: list[list[int]]) -> list[Choke]:
    kept = [c for c in existing if c.source == "hand" or c.deleted]
    taken = {tuple(c.cells) for c in kept}
    covered = set().union(*(c.cells for c in kept)) if kept else set()
    next_id = max([c.id for c in existing] + [0]) + 1
    out = list(kept)
    for cells in detected:
        if tuple(cells) in taken or covered & set(cells):
            continue
        out.append(Choke(next_id, str(next_id), list(cells)))
        next_id += 1
    return sorted(out, key=lambda c: c.id)


def _path(name: str, asset_dir: Path) -> Path:
    return Path(asset_dir) / f"{name}.chokes.json"


def save(name: str, chokes: list[Choke], asset_dir: Path = ASSET_DIR) -> Path:
    path = _path(name, asset_dir)
    body = {"version": 1, "map": name, "chokes": [asdict(c) for c in sorted(chokes, key=lambda c: c.id)]}
    path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    return path


def load(name: str, asset_dir: Path = ASSET_DIR) -> list[Choke] | None:
    path = _path(name, asset_dir)
    if not path.exists():
        return None
    return [Choke(**c) for c in json.loads(path.read_text(encoding="utf-8"))["chokes"]]


def asset_hash(name: str, asset_dir: Path = ASSET_DIR) -> str | None:
    path = _path(name, asset_dir)
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
```

Implement `webapp/app/control/chokes.py`:

```python
"""Choke detection (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 2); the asset itself is
app/replays/choke_assets.py.

A cell is a pinch when, across some direction, the walkable span through it is at most MAX_CHOKE_M and the
span WIDEN_CELLS further along the passage, on both sides, is wider by at least WIDEN_CELLS. Touching pinch
cells are one choke, whose cells are the spans across them (the line across the passage). Each of the map's
special links (teleporters, ropes, drops) is a choke of its two end cells. Chokes never affect control: the
engine only labels routes with them."""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from app.control.geometry import GRID
from app.replays.choke_assets import Choke

MAX_CHOKE_M = 8.0
WIDEN_CELLS = 2
# (along, across): unit steps; across is perpendicular to along
_AXES = [((0, 1), (1, 0)), ((1, 0), (0, 1)), ((1, 1), (1, -1)), ((1, -1), (1, 1))]


def _span(walk: np.ndarray, y: int, x: int, dy: int, dx: int) -> list[int]:
    """The walkable cells on the line through (y, x) in direction +-(dy, dx), up to the first non-walkable."""
    out = [y * GRID + x]
    for sign in (1, -1):
        cy, cx = y + sign * dy, x + sign * dx
        while 0 <= cy < GRID and 0 <= cx < GRID and walk[cy, cx]:
            out.append(cy * GRID + cx)
            cy, cx = cy + sign * dy, cx + sign * dx
    return sorted(out)


def detect(geo) -> list[list[int]]:
    walk = geo.walk
    limit = max(1, int(math.floor(MAX_CHOKE_M / geo.cell_m)))
    pinch = np.zeros((GRID, GRID), bool)
    across_of: dict[int, set[int]] = {}
    ys, xs = np.nonzero(walk)
    for y, x in zip(ys.tolist(), xs.tolist()):
        for (ay, ax), (cy, cx) in _AXES:
            here = _span(walk, y, x, cy, cx)
            if len(here) > limit:
                continue
            wider = 0
            for sign in (1, -1):
                ny, nx = y + sign * WIDEN_CELLS * ay, x + sign * WIDEN_CELLS * ax
                if 0 <= ny < GRID and 0 <= nx < GRID and walk[ny, nx] and \
                        len(_span(walk, ny, nx, cy, cx)) >= len(here) + WIDEN_CELLS:
                    wider += 1
            if wider == 2:
                pinch[y, x] = True
                across_of.setdefault(y * GRID + x, set()).update(here)
    lab, n = ndimage.label(pinch, np.ones((3, 3), bool))
    found = []
    for i in range(1, n + 1):
        cells = set()
        for c in np.flatnonzero(lab.ravel() == i).tolist():
            cells |= across_of[c]
        found.append(sorted(cells))
    for sp in geo.specials:
        try:
            a = geo.cell_of_px(*geo.px_of_uv(*sp["a"]))
            b = geo.cell_of_px(*geo.px_of_uv(*sp["b"]))
        except (KeyError, TypeError):
            continue
        found.append(sorted({a, b}))
    return sorted(found)


def node_chokes(geo, chokes: list[Choke] | None) -> np.ndarray:
    """Per node, the id of the choke its cell lies on (-1 for none). A choke covers every floor of its cells."""
    by_cell = np.full(GRID * GRID, -1, np.int32)
    for c in chokes or []:
        if not c.deleted:
            by_cell[c.cells] = c.id
    if geo.heights is None:
        return by_cell[: geo.n].copy()
    return by_cell[geo.node_cell].astype(np.int32)
```

- [ ] **Step 4: Run the tests**

Run: `PY -m pytest tests/replays/test_gaps_chokes.py -q`
Expected: PASS. If `test_a_one_cell_door_is_one_choke_across_it` finds more than one choke, print `found`
and the spans: the door hall's wall end may also make the hall's south strip a pinch. Fix by tightening the
pinch test (not the test), and record the reason in the module docstring.

- [ ] **Step 5: Write `webapp/scripts/build_chokes.py`**

```python
"""Detects each map's chokes and merges them into its `<Map>.chokes.json`, keeping hand edits
(app/control/chokes.py; docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 2).

    .\\.venv\\Scripts\\python.exe scripts\\build_chokes.py            # every map with control masks
    .\\.venv\\Scripts\\python.exe scripts\\build_chokes.py Ascent     # one map
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def main(argv: list[str] | None = None) -> int:
    from app.control import chokes, geometry
    from app.replays import choke_assets

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("maps", nargs="*")
    args = parser.parse_args(argv)
    names = args.maps or sorted(p.name.split(".")[0] for p in geometry.ASSET_DIR.glob("*.walk.png"))
    for name in names:
        geo = geometry.load_geometry(name)
        merged = choke_assets.merge(choke_assets.load(name) or [], chokes.detect(geo))
        path = choke_assets.save(name, merged)
        live = [c for c in merged if not c.deleted]
        print(f"{name}: {len(live)} chokes ({sum(c.source == 'hand' for c in live)} by hand) -> {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run it on one map**

Run: `PY scripts/build_chokes.py Ascent`
Expected: `Ascent: N chokes (0 by hand) -> Ascent.chokes.json`, with N between 10 and 80. Outside that range,
stop and show the owner the count before tuning `MAX_CHOKE_M` or `WIDEN_CELLS`.

- [ ] **Step 7: Run it on every map**

Run: `PY scripts/build_chokes.py`
Expected: one line per map.

- [ ] **Step 8: Commit**

```
git add app/replays/choke_assets.py app/control/chokes.py scripts/build_chokes.py app/static/data/control/*.chokes.json tests/replays/test_gaps_chokes.py
git commit -F <msg file>   # "Chokes: detected per map, hand edits kept"
```

---

### Task 3: The spread's parents

**Files:**
- Modify: `webapp/app/control/topology.py` (`FlatTopology.spread` lines 97-120, `NodeTopology.spread`
  lines 216-231)
- Test: `webapp/tests/replays/test_gaps_spread.py`

**Interfaces:**
- Produces: `spread(reached, room, free, t, straight, links, parents=False)` on both topologies. With
  `parents=False` it returns the arrival array exactly as today. With `parents=True` it returns
  `(arrivals, parent)`, where `parent` is int64 of length n: the node an arrival came from, or -1 for a node
  whose value did not come from a neighbour or link in this call.

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_gaps_spread.py`:

```python
"""The unknown's spread with parents (timing-gaps spec, section 4, "Route history"): the same arrivals as
without, and each reached node's parent is a neighbour (or link end) that arrived earlier."""

import math

import numpy as np
import pytest

from app.control import topology
from app.control.geometry import GRID
from tests.replays.control_toys import door_hall, toy_heights


def _setup(geo):
    topo = topology.of(geo)
    n = geo.n
    room = geo.walk_n.copy() if hasattr(geo, "walk_n") else geo.walk.ravel().copy()
    reached = np.full(n, np.inf)
    start = int(np.flatnonzero(room)[0])
    reached[start] = 0.0
    free = np.zeros(n)
    return topo, room, reached, free, start


@pytest.mark.parametrize("make", [door_hall, lambda: toy_heights("Spread", [(96, 96, 416, 296)],
                                                                  upper=[((200, 150, 260, 200), 3.0)])])
def test_parents_do_not_change_arrivals(make):
    geo = make()
    topo, room, reached, free, _ = _setup(geo)
    plain = topo.spread(reached, room, free, 20.0, 0.35, [])
    with_parents, parent = topo.spread(reached, room, free, 20.0, 0.35, [], parents=True)
    assert np.array_equal(plain, with_parents)
    assert parent.dtype == np.int64 and len(parent) == geo.n


def test_every_reached_node_points_to_an_earlier_neighbour():
    geo = door_hall()
    topo, room, reached, free, start = _setup(geo)
    arr, parent = topo.spread(reached, room, free, 20.0, 0.35, [], parents=True)
    for node in np.flatnonzero(np.isfinite(arr)).tolist():
        if node == start:
            assert parent[node] == -1
            continue
        p = int(parent[node])
        assert p >= 0 and arr[p] < arr[node]
        py, px = divmod(p, GRID)
        ny, nx = divmod(node, GRID)
        assert max(abs(py - ny), abs(px - nx)) == 1, "a parent is one of the 8 neighbours"
        step = math.sqrt(2) if py != ny and px != nx else 1.0
        assert arr[node] == pytest.approx(max(arr[p], free[node]) + 0.35 * step)


def test_a_link_is_a_parent():
    geo = door_hall()
    topo, room, reached, free, start = _setup(geo)
    far = int(np.flatnonzero(room)[-1])
    arr, parent = topo.spread(reached, room, free, 0.5, 0.35, [(start, far, True)], parents=True)
    assert parent[far] == start and arr[far] == pytest.approx(0.35)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PY -m pytest tests/replays/test_gaps_spread.py -q`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'parents'`).

- [ ] **Step 3: Implement parents in `FlatTopology.spread`**

Replace the method body (lines 97-120) with:

```python
    def spread(self, reached: np.ndarray, room: np.ndarray, free: np.ndarray, t: float, straight: float,
               links: list, parents: bool = False):
        """The unknown's arrival times relaxed through `room` up to time `t` (engine.Unknown._spread):
        each node's earliest arrival from a neighbour, after the node was last freed (`free`); a straight
        step costs `straight` seconds, a diagonal sqrt(2) of it, a link (a, b, one way) one straight step.
        Arrivals later than `t` aren't there yet. With `parents`, also each arrival's source node (-1 for a
        node whose value came from no neighbour or link here); ties go to the first in SPREAD_ORDER."""
        g = reached.reshape(GRID, GRID).copy()
        f = free.reshape(GRID, GRID)
        r = room.reshape(GRID, GRID)
        idx = np.arange(GRID * GRID).reshape(GRID, GRID)
        par = np.full((GRID, GRID), -1, np.int64) if parents else None
        while True:
            best = g.copy()
            for dy, dx in SPREAD_ORDER:
                src = np.full((GRID, GRID), np.inf)
                src[max(dy, 0):GRID + min(dy, 0), max(dx, 0):GRID + min(dx, 0)] = \
                    g[max(-dy, 0):GRID + min(-dy, 0), max(-dx, 0):GRID + min(-dx, 0)]
                cand = np.maximum(src, f) + straight * (math.sqrt(2) if dy and dx else 1.0)
                if parents:
                    who = np.full((GRID, GRID), -1, np.int64)
                    who[max(dy, 0):GRID + min(dy, 0), max(dx, 0):GRID + min(dx, 0)] = \
                        idx[max(-dy, 0):GRID + min(-dy, 0), max(-dx, 0):GRID + min(-dx, 0)]
                    better = cand < best
                    par[better] = who[better]
                np.minimum(best, cand, out=best)
            for a, b, one_way in links:
                via = max(g.flat[a], f.flat[b]) + straight
                if parents and via < best.flat[b]:
                    par.flat[b] = a
                best.flat[b] = min(best.flat[b], via)
                if not one_way:
                    via = max(g.flat[b], f.flat[a]) + straight
                    if parents and via < best.flat[a]:
                        par.flat[a] = b
                    best.flat[a] = min(best.flat[a], via)
            best[~r | (best > t)] = np.inf
            if np.array_equal(best, g):
                return (best.ravel(), par.ravel()) if parents else best.ravel()
            g = best
```

- [ ] **Step 4: Implement parents in `NodeTopology.spread`**

Replace the method (lines 216-231) with:

```python
    def spread(self, reached: np.ndarray, room: np.ndarray, free: np.ndarray, t: float, straight: float,
               links: list, parents: bool = False):
        g = reached.copy()
        cost = self.in_cost * straight
        freed = free[:, None]
        rows = np.arange(self.n)
        par = np.full(self.n, -1, np.int64) if parents else None
        while True:
            padded = np.append(g, np.inf)
            cand = np.maximum(padded[self.in_from], freed) + cost
            j = cand.argmin(1)
            m = cand[rows, j]
            best = np.minimum(g, m)
            if parents:
                better = m < g
                par[better] = self.in_from[rows[better], j[better]]
            for a, b, one_way in links:
                via = max(g[a], free[b]) + straight
                if parents and via < best[b]:
                    par[b] = a
                best[b] = min(best[b], via)
                if not one_way:
                    via = max(g[b], free[a]) + straight
                    if parents and via < best[a]:
                        par[a] = b
                    best[a] = min(best[a], via)
            best[~room | (best > t)] = np.inf
            if np.array_equal(best, g):
                return (best, par) if parents else best
            g = best
```

`np.minimum(g, m)` equals the old `np.minimum(g, cand.min(1))`, so arrivals are unchanged.

- [ ] **Step 5: Run the new tests and the engine's**

Run: `PY -m pytest tests/replays/test_gaps_spread.py tests/replays/test_control_unknown.py tests/replays/test_control_reference.py tests/replays/test_control_floors.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```
git add app/control/topology.py tests/replays/test_gaps_spread.py
git commit -F <msg file>   # "Unknown spread: optionally return each arrival's parent"
```

---

### Task 4: Route history in the engine's unknown (output unchanged)

Review fixes: R15. Step 7's baseline timing is taken **before** any code change in this task.

**Files:**
- Create: `webapp/app/control/routes.py`
- Modify: `webapp/app/control/engine.py` (`Unknown.__init__` line ~1548, `Unknown.apply` lines 1624-1674,
  `Unknown._drop_pieces` lines 1676-1696, `Unknown._spread` lines 1707-1719, `TickRunner.__init__` line ~1889,
  `compute_round` line ~1945)
- Test: `webapp/tests/replays/test_gaps_routes.py`

**Interfaces:**
- Consumes: `topology.spread(..., parents=True)` (Task 3); `choke_assets.load`, `chokes.node_chokes` (Task 2).
- Produces:
  - `class RouteLog` in `app/control/routes.py`:
    - `add(node: int, t: float, parent: int, choke: int) -> int` (returns the entry id)
    - arrays (numpy views of the first `len(log)` entries): `node`, `t`, `parent`, `seq` (int32 seq ids)
    - `seqs: list[tuple[int, ...]]` (seq id -> choke ids; id 0 is `()`)
    - `trace(entry: int) -> list[int]`: entry ids from the route's source to `entry`
    - `__len__`
  - `Unknown(geo, chokes: np.ndarray | None = None)`; new attributes `log: dict[str, RouteLog]` (per team) and
    `entry: dict[str, dict[int, np.ndarray]]` (team -> enemy slot -> int64 entry id per node, -1 outside).
  - `TickRunner(geo, chokes=None)`; `compute_round` loads the map's chokes with `choke_assets.load(geo.name)`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_gaps_routes.py`:

```python
"""Route history in the engine's unknown (timing-gaps spec, section 4): every unknown node has an entry
whose route leads back through earlier entries to a source, labelled with the chokes it crosses, and none
of it changes which nodes are unknown."""

import numpy as np

from app.control import engine as ce
from app.control.routes import RouteLog
from tests.replays.control_toys import door_hall, open_hall
from tests.replays.test_control_unknown import _Tk, _at


def test_routelog_traces_and_interns_choke_sequences():
    log = RouteLog()
    a = log.add(5, 0.0, -1, -1)
    b = log.add(6, 0.3, a, 7)
    c = log.add(7, 0.6, b, 7)        # still on choke 7: not repeated
    d = log.add(8, 0.9, c, 9)
    assert log.trace(d) == [a, b, c, d]
    assert log.seqs[log.seq[d]] == (7, 9)
    assert log.seqs[log.seq[a]] == () and log.seq[a] == 0
    assert len(log) == 4 and log.node[d] == 8 and log.t[d] == 0.9


def _walk_east(geo, unk, times):
    for t in times:
        unk.apply(_Tk(t, _at(0, "A", geo, 120, 200), _at(5, "B", geo, 400, 200)))


def test_every_unknown_node_has_a_route_to_a_source():
    geo = door_hall()
    unk = ce.Unknown(geo)
    _walk_east(geo, unk, [0.0, 1.0, 2.0, 3.0])
    ent = unk.entry["A"][5]
    log = unk.log["A"]
    unknown_nodes = np.flatnonzero(np.isfinite(unk.reached["A"][5]))
    assert set(np.flatnonzero(ent >= 0).tolist()) == set(unknown_nodes.tolist())
    for node in unknown_nodes.tolist():
        route = log.trace(int(ent[node]))
        assert log.node[route[-1]] == node and log.parent[route[0]] == -1
        times = [log.t[e] for e in route]
        assert times == sorted(times)


def test_route_history_leaves_the_unknown_unchanged():
    geo = door_hall()
    chokes = np.full(geo.n, -1, np.int32)
    chokes[geo.cell_of_px(208, 292)] = 1
    plain, labelled = ce.Unknown(geo), ce.Unknown(geo, chokes=chokes)
    _walk_east(geo, plain, [0.0, 1.0, 2.0, 5.0, 9.0])
    _walk_east(geo, labelled, [0.0, 1.0, 2.0, 5.0, 9.0])
    assert np.array_equal(plain.cells["A"], labelled.cells["A"])
    assert np.array_equal(plain.reached["A"][5], labelled.reached["A"][5])


def test_routes_through_the_door_carry_its_choke():
    geo = door_hall()
    door = geo.cell_of_px(208, 292)
    chokes = np.full(geo.n, -1, np.int32)
    chokes[door] = 1
    unk = ce.Unknown(geo, chokes=chokes)
    _walk_east(geo, unk, [0.0, 4.0, 8.0, 12.0, 16.0])
    west = geo.cell_of_px(150, 200)      # behind the wall: only reachable through the door
    ent = unk.entry["A"][5]
    assert ent[west] >= 0
    assert unk.log["A"].seqs[unk.log["A"].seq[ent[west]]] == (1,)


def test_a_cleared_node_loses_its_entry_and_routes_survive():
    geo = open_hall()
    unk = ce.Unknown(geo)
    _walk_east(geo, unk, [0.0, 2.0])
    log = unk.log["A"]
    ent = unk.entry["A"][5].copy()
    target = int(np.flatnonzero(ent >= 0)[-1])
    route_before = log.trace(int(ent[target]))
    watched = np.zeros(geo.n, bool)
    watched[target] = True
    unk.apply(_Tk(2.5, _at(0, "A", geo, 120, 200, cells=watched), _at(5, "B", geo, 400, 200)))
    assert unk.entry["A"][5][target] == -1
    assert log.trace(route_before[-1]) == route_before, "an earlier route is never rewritten"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PY -m pytest tests/replays/test_gaps_routes.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.control.routes'`).

- [ ] **Step 3: Implement `webapp/app/control/routes.py`**

```python
"""The unknown's route history (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 4): an
append-only log of arrivals per team. An entry is (node, arrival time, parent entry, choke sequence id); a
parent is always an earlier entry, so a route never loops and is never rewritten. Choke sequences are
interned: id 0 is the empty sequence."""

from __future__ import annotations

import numpy as np


class RouteLog:
    def __init__(self, capacity: int = 4096):
        self._node = np.zeros(capacity, np.int64)
        self._t = np.zeros(capacity, np.float64)
        self._parent = np.zeros(capacity, np.int64)
        self._seq = np.zeros(capacity, np.int32)
        self._n = 0
        self.seqs: list[tuple[int, ...]] = [()]
        self._seq_id: dict[tuple[int, ...], int] = {(): 0}

    def __len__(self) -> int:
        return self._n

    def _grow(self) -> None:
        for name in ("_node", "_t", "_parent", "_seq"):
            old = getattr(self, name)
            new = np.zeros(len(old) * 2, old.dtype)
            new[: len(old)] = old
            setattr(self, name, new)

    def add(self, node: int, t: float, parent: int, choke: int) -> int:
        if self._n == len(self._node):
            self._grow()
        base = self.seqs[self._seq[parent]] if parent >= 0 else ()
        seq = base + (choke,) if choke >= 0 and (not base or base[-1] != choke) else base
        sid = self._seq_id.get(seq)
        if sid is None:
            sid = self._seq_id[seq] = len(self.seqs)
            self.seqs.append(seq)
        i = self._n
        self._node[i], self._t[i], self._parent[i], self._seq[i] = node, t, parent, sid
        self._n += 1
        return i

    @property
    def node(self) -> np.ndarray:
        return self._node[: self._n]

    @property
    def t(self) -> np.ndarray:
        return self._t[: self._n]

    @property
    def parent(self) -> np.ndarray:
        return self._parent[: self._n]

    @property
    def seq(self) -> np.ndarray:
        return self._seq[: self._n]

    def trace(self, entry: int) -> list[int]:
        out = []
        while entry >= 0:
            out.append(int(entry))
            entry = int(self._parent[entry])
        return out[::-1]
```

- [ ] **Step 4: Give `Unknown` the log**

In `engine.py`, add `from app.control import chokes`, `from app.replays import choke_assets` and
`from app.control.routes import RouteLog`
beside the other `app.control` imports.

Change `Unknown.__init__`'s signature to `def __init__(self, geo: Geometry, chokes: np.ndarray | None = None):`
and add at its end:

```python
        # route history (timing gaps, section 4): bookkeeping only; it never changes which nodes are unknown
        self.chokes = chokes if chokes is not None else np.full(geo.n, -1, np.int32)
        self.log = {"A": RouteLog(), "B": RouteLog()}
        self.entry: dict[str, dict[int, np.ndarray]] = {"A": {}, "B": {}}   # side -> enemy -> entry per node
```

Change `_spread` to return parents:

```python
    def _spread(self, reached: np.ndarray, room: np.ndarray, free: np.ndarray, t: float,
                seen: tuple[int, float] | None = None) -> tuple[np.ndarray, np.ndarray]:
        """... (docstring unchanged) Also each arrival's parent node (topology.spread, `parents`)."""
        g = reached.copy()
        if seen is not None:
            g[seen[0]] = min(g[seen[0]], seen[1])
        if not np.isfinite(g).any():
            return g, np.full(len(g), -1, np.int64)
        return self.topo.spread(g, room, free, t, self.geo.cell_m / UNKNOWN_MPS, self.links, parents=True)
```

Add a method after `_spread`:

```python
    def _record(self, side: str, slot: int, before: np.ndarray, after: np.ndarray, parent: np.ndarray,
                origin: dict[int, int] | None = None) -> None:
        """Log every node whose arrival is new or changed, in arrival order, so each parent's entry exists
        first. `origin` gives a source node's parent node (an area collapse's centre); other sources have
        none."""
        log = self.log[side]
        ent = self.entry[side].get(slot)
        ent = np.full(len(after), -1, np.int64) if ent is None else ent.copy()
        fin = np.isfinite(after)
        ent[~fin] = -1
        changed = np.flatnonzero(fin & ((~np.isfinite(before)) | (after != before)))
        order = changed[np.lexsort((changed, after[changed]))]
        origin = origin or {}
        for node in order.tolist():
            p = int(parent[node])
            if p < 0:
                p = origin.get(node, -1)
            pe = int(ent[p]) if p >= 0 and fin[p] else -1
            ent[node] = log.add(node, float(after[node]), pe, int(self.chokes[node]))
        self.entry[side][slot] = ent
```

In `apply`, inside the per-enemy loop, keep a copy of the arrivals before this tick and record after the
spread. Replace the loop body's start and end so it reads:

```python
            for slot in sorted(enemies):
                reached = self.reached[side].get(slot)
                before = np.full(n, np.inf) if reached is None else reached.copy()
                if reached is None:
                    reached = np.full(n, np.inf)
                    if self._start[side] is not None:
                        reached[self._start[side]] = t        # the barrier drop's ground: there now
                # ... the existing lines from `h = tick.holders.get(slot)` to `reached[~room] = np.inf` ...
                reached, parent = self._spread(reached, room, free, t, self.seen[side].get(slot))
                self._record(side, slot, before, reached, parent)
                self.reached[side][slot] = reached
                cells |= np.isfinite(reached)
```

In the dead-enemy loop at the top of `apply`, also drop the entry array:

```python
            for gone in set(self.reached[side]) - enemies:   # dead: nowhere
                del self.reached[side][gone]
                self.seen[side].pop(gone, None)
                self.entry[side].pop(gone, None)
```

In `_drop_pieces`, where each enemy's dropped nodes are set to `inf`, also clear their entries:

```python
        for slot, reached in self.reached[side].items():
            reached[drop] = np.inf
            if slot in self.entry[side]:
                self.entry[side][slot][drop] = -1
```

- [ ] **Step 5: Pass chokes in from `compute_round`**

`TickRunner.__init__(self, geo: Geometry, chokes: np.ndarray | None = None)` constructs
`self.unknown = Unknown(geo, chokes)`. In `compute_round`, replace `runner = TickRunner(geo)` with:

```python
    runner = TickRunner(geo, chokes.node_chokes(geo, choke_assets.load(geo.name)))
```

- [ ] **Step 6: Run the tests, including the byte-identity reference**

Run: `PY -m pytest tests/replays/test_gaps_routes.py tests/replays/test_control_unknown.py tests/replays/test_control_reference.py tests/replays/test_control_format.py -q`
Expected: PASS. `test_control_reference.py` passing proves the unknown is unchanged. If it fails, the
bookkeeping changed an arrival: fix the code, never the fixture.

- [ ] **Step 7: Measure the cost**

Before Step 3 of this task, run `PY -m pytest tests/replays/test_control_reference.py -q --durations=8` and
note the per-round times. Run it again now. Expected: under 20% slower. If slower, report both sets of
numbers to the owner before Task 5.

- [ ] **Step 8: Commit**

```
git add app/control/routes.py app/control/engine.py tests/replays/test_gaps_routes.py
git commit -F <msg file>   # "Unknown: route history and choke sequences, output unchanged"
```

---

### Task 5: Locating events (CONTROL_REVISION 5)

Review fixes: R8, R14, R15, R20.

**Files:**
- Modify: `webapp/app/control/engine.py` (constants block lines ~80-128; `RoundInputs.__init__` lines
  271-281; `_read_util` lines 370-371 and 400-403; `_damage` lines 439-447; `Unknown.apply`; module
  docstring's Unknown bullet lines 28-33)
- Modify: `webapp/app/replays/control_format.py` (`CONTROL_REVISION = 5`)
- Modify (named replacement): `webapp/tests/replays/test_control_format.py` (`PINNED`)
- Modify (named replacement): `webapp/tests/fixtures/control/reference_flat.json`
- Test: `webapp/tests/replays/test_gaps_locating.py`

**Interfaces:**
- Consumes: `hearing.json` (Task 1); `Unknown._record` (Task 4).
- Produces:
  - engine constants `KILL_AREA_M = PLANT_AREA_M = SHOT_AREA_M = 5.0`, `DAMAGE_AREA_M = 10.0`,
    `FOOTSTEP_AREA_M = 10.0`, `AUDIBLE_MPS = 4.5`, `AUDIBLE_WINDOW_S = 0.5`, `HEARING` (the parsed JSON),
    `FOOTSTEP_RANGE_M`, `GUN_HEARING_M`, `GUN_HEARING_DEFAULT_M`.
  - `RoundInputs` attributes: `kills: list[tuple[float, int, int]]` (t, killer, victim),
    `planter: tuple[float, int] | None`, `shots: list[tuple[float, int, str | None]]` (t, by, gun),
    `gun_runs: list[tuple[float, float, int, int, bool]]` (t, t1, by, target, wall), and
    `speed(s, t) -> float | None` (m/s over `AUDIBLE_WINDOW_S`, within one track segment).
  - `Unknown.located: dict[str, dict[int, float]]` (side -> enemy -> last locating time) and
    `Unknown.events: dict[str, list[tuple[int, float, str]]]` (side -> this tick's events as
    (enemy, time, kind); kind in `seen`, `kill`, `plant`, `footsteps`, `gunfire`, `damage`, `revived`).

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_gaps_locating.py`:

```python
"""The unknown's locating events (timing-gaps spec, section 4): a kill, the plant, audible movement,
gunfire and gun damage collapse an enemy's unknown to an area around them; a revived enemy restarts where
they stand. Toy rounds through the real engine."""

import numpy as np
import pytest

from app.control import engine as ce
from tests.replays.control_toys import blob, open_hall, toy_ability, uv


def _runner(geo, data):
    rnd = ce.RoundInputs(data, geo)
    runner = ce.TickRunner(geo)
    return rnd, runner


def _step(rnd, runner, t):
    return runner.step(ce.Tick(rnd, t))


def _unknown_of(runner, side, slot):
    return np.isfinite(runner.unknown.reached[side][slot])


def _hidden_pair(**extra):
    """A faces west at x 120; B stands east at x 400 behind A's back: B is never seen by A."""
    return {0: ("A", [(0.0, 120, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])}


def test_a_kill_collapses_the_killers_unknown_to_an_area():
    geo = open_hall()
    data = blob({**_hidden_pair(), 1: ("A", [(0.0, 200, 120, 180)])}, t_end=12.0, deaths={1: 8.0})
    data["kills"] = [{"i": 0, "t": 8.0, "killer": 5, "victim": 1, "u": uv(200, 120)[0], "v": uv(200, 120)[1]}]
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 8.0, 0.5):
        _step(rnd, runner, float(t))
    wide = _unknown_of(runner, "A", 5).sum()
    _step(rnd, runner, 8.0)
    after = _unknown_of(runner, "A", 5)
    assert after.sum() < wide
    r = ce.KILL_AREA_M / geo.m_per_px
    x, y = 400, 200
    far = np.hypot(geo.centres[:, 0] - x, geo.centres[:, 1] - y) > r + 8
    assert not (after & far).any(), "only within KILL_AREA_M of the killer (plus spread since)"
    assert runner.unknown.located["A"][5] == pytest.approx(8.0)
    assert ("kill" in {k for _, _, k in runner.unknown.events["A"]})


def test_gun_damage_locates_the_shooter_heard_or_not():
    geo = open_hall()
    data = blob(_hidden_pair(), t_end=10.0,
                util=[{"k": "damage", "t": 6.0, "t1": 6.1, "by": 5, "target": 0, "src": "gun", "wall": True, "n": 1}])
    rnd, runner = _runner(geo, data)
    assert rnd.gun_runs == [(6.0, 6.1, 5, 0, True)]
    for t in sorted(set(np.arange(0.0, 7.0, 0.5).tolist()) | {6.0}):
        _step(rnd, runner, float(t))
    assert runner.unknown.located["A"][5] == pytest.approx(6.0)


def test_ability_damage_locates_nobody():
    geo = open_hall()
    data = blob(_hidden_pair(), t_end=10.0,
                util=[{"k": "damage", "t": 6.0, "t1": 6.1, "by": 5, "target": 0, "src": "ability", "wall": False, "n": 1}])
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 7.0, 0.5):
        _step(rnd, runner, float(t))
    assert 5 not in runner.unknown.located["A"]


def test_gunfire_out_of_hearing_range_locates_nobody(monkeypatch):
    geo = open_hall()
    monkeypatch.setattr(ce, "GUN_HEARING_M", {"Quiet": 1.0})
    data = blob(_hidden_pair(), t_end=10.0, util=[{"k": "shot", "t": 6.0, "by": 5, "u": 0, "v": 0, "gun": "Quiet"}])
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 7.0, 0.5):
        _step(rnd, runner, float(t))
    assert 5 not in runner.unknown.located["A"]
    monkeypatch.setattr(ce, "GUN_HEARING_M", {"Quiet": 100.0})
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 7.0, 0.5):
        _step(rnd, runner, float(t))
    assert runner.unknown.located["A"][5] == pytest.approx(6.0)


def test_running_near_a_player_is_heard_and_walking_is_not(monkeypatch):
    geo = open_hall()
    monkeypatch.setattr(ce, "FOOTSTEP_RANGE_M", 100.0)
    walk_px = 3.0 / geo.m_per_px      # 3 m/s: under AUDIBLE_MPS
    run_px = 6.0 / geo.m_per_px       # 6 m/s: over it
    for px_per_s, heard in ((walk_px, False), (run_px, True)):
        data = blob({0: ("A", [(0.0, 120, 200, 180)]),
                     5: ("B", [(0.0, 400, 120, 180), (4.0, 400, 120, 180), (5.0, 400, 120 + px_per_s, 180)])},
                    t_end=8.0)
        rnd, runner = _runner(geo, data)
        for t in np.arange(0.0, 5.5, 0.5):
            _step(rnd, runner, float(t))
        assert (5 in runner.unknown.located["A"]) is heard


def test_the_plant_locates_the_planter():
    geo = open_hall()
    data = blob(_hidden_pair(), t_end=10.0, util=[toy_ability("", "Spike", 400, 200, 5, t=5.0, t1=None, kind="Bomb")])
    rnd, runner = _runner(geo, data)
    assert rnd.planter == (5.0, 5)
    for t in np.arange(0.0, 6.0, 0.5):
        _step(rnd, runner, float(t))
    assert runner.unknown.located["A"][5] == pytest.approx(5.0)


def test_a_revived_enemy_restarts_where_they_stand():
    import copy
    from tests.replays.control_toys import barrier_hall
    geo = barrier_hall()
    data = blob({0: ("A", [(0.0, 120, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180), (6.0, 300, 120, 180)])},
                t_end=10.0)
    data["alive"]["5"] = [[0.0, 3.0, "kill"], [6.0, None, None]]
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 6.5, 0.5):
        _step(rnd, runner, float(t))
    back = _unknown_of(runner, "A", 5)
    start = runner.unknown._start["A"]
    assert back.sum() <= 2, "not the barrier ground again: just where they stand"
    assert not (back & start & (np.hypot(geo.centres[:, 0] - 300, geo.centres[:, 1] - 120) > 16)).any()
    assert runner.unknown.located["A"][5] == pytest.approx(6.0)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PY -m pytest tests/replays/test_gaps_locating.py -q`
Expected: FAIL (`AttributeError: ... 'gun_runs'` and similar).

- [ ] **Step 3: Constants**

After `DROP_PIECE_CELLS` in `engine.py`'s constants block add:

```python
# Locating events (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 4): what a team hears
# or learns from the feed collapses that enemy's unknown to an area round them. Ranges: app/control/hearing.json.
KILL_AREA_M = 5.0
PLANT_AREA_M = 5.0
SHOT_AREA_M = 5.0
DAMAGE_AREA_M = 10.0
FOOTSTEP_AREA_M = 10.0
AUDIBLE_MPS = 4.5            # above the fastest shift-walk (knife, 4.05 m/s), below the slowest run (rifle, 5.40)
AUDIBLE_WINDOW_S = 0.5
HEARING = json.loads((Path(__file__).with_name("hearing.json")).read_text(encoding="utf-8"))
FOOTSTEP_RANGE_M = float(HEARING["footstep_range_m"])
GUN_HEARING_M = {str(k): float(v) for k, v in HEARING["guns"].items()}
GUN_HEARING_DEFAULT_M = float(HEARING["default_gun_m"])
```

and add `import json` and `from pathlib import Path` to the imports.

- [ ] **Step 4: Inputs on `RoundInputs`**

Beside `self.plant: float | None = None` (line ~279) add:

```python
        self.planter: tuple[float, int] | None = None     # (time, slot) of the plant, when its owner is known
        self.shots: list[tuple[float, int, str | None]] = []
        self.gun_runs: list[tuple[float, float, int, int, bool]] = []   # (t, t1, by, target, wall), enemies only
        self.kills = [(float(k["t"]), int(k["killer"]), int(k["victim"])) for k in blob.get("kills") or []
                      if k.get("killer") is not None and k.get("victim") is not None]
```

Directly above `self.tracks = {}` (line 259) add `self._segment: dict[int, np.ndarray] = {}   # slot -> each
sample's segment index`, and in the tracks loop (lines 262-269) record each sample's segment:

```python
            self._segment[int(s)] = np.concatenate([np.full(len(g["u"]), i) for i, g in enumerate(segs)]) if segs \
                else np.zeros(0, int)
```

In `_read_util`, the Bomb branch becomes:

```python
                if e.get("kind") == "Bomb" and self.plant is None:
                    self.plant = snap(e["t"])
                    if e.get("by") is not None:
                        self.planter = (float(e["t"]), int(e["by"]))
                    else:
                        self.missing["plant without a planter (locates nobody)"] += 1
```

and the shot branch:

```python
            elif k == "shot":
                self.shot_times.append(e["t"])
                if e.get("by") is not None:
                    self.shots.append((float(e["t"]), int(e["by"]), e.get("gun")))
                    if e.get("gun") is None:
                        self.missing["shot without a gun (loudest range used)"] += 1
```

At the end of `_damage` (after the existing checks; it already returned for same-team or unknown slots):

```python
        if e.get("src") == "gun":
            self.gun_runs.append((float(e["t"]), float(t1), int(by), int(target), bool(e.get("wall"))))
            self.events.append(e["t"])       # every gun damage run gets a tick (timing gaps)
```

Add a method after `movement`:

```python
    def speed(self, s: int, t: float) -> float | None:
        """Metres per second over the last AUDIBLE_WINDOW_S, within one track segment; None without two
        samples in the same segment."""
        a, b = self._sample(s, t - AUDIBLE_WINDOW_S), self._sample(s, t)
        if a is None or b is None or self._segment[s][a] != self._segment[s][b]:
            return None
        tr = self.tracks[s]
        dt = tr[0][b] - tr[0][a]
        if dt <= 0:
            return None
        dist_px = math.hypot((tr[1][b] - tr[1][a]) * PX / 10000, (tr[2][b] - tr[2][a]) * PX / 10000)
        return dist_px * self.geo.m_per_px / dt
```

- [ ] **Step 5: Events in `Unknown.apply`**

In `Unknown.__init__` add:

```python
        self.located: dict[str, dict[int, float]] = {"A": {}, "B": {}}    # side -> enemy -> last locating time
        self.events: dict[str, list[tuple[int, float, str]]] = {"A": [], "B": []}   # this tick's
        self._prev_t: float | None = None
```

Add methods:

```python
    def _heard_by(self, rnd, side: str, te: float, x: float, y: float, range_m: float) -> bool:
        """Whether a live player of `side` is within range_m of (x, y) px at te (walls ignored)."""
        r = range_m / self.geo.m_per_px
        for s, team in rnd.team.items():
            if team != side or not rnd.alive(s, te):
                continue
            p = rnd.pos(s, te)
            if p is not None and (p[0] - x) ** 2 + (p[1] - y) ** 2 <= r * r:
                return True
        return False

    def _locating(self, rnd, side: str, slot: int, t0: float, t: float) -> tuple[float, str, float] | None:
        """The latest event in (t0, t] that locates enemy `slot` for `side`: (time, kind, area radius m).
        On a time tie the smaller area wins."""
        found = []
        for te, killer, victim in rnd.kills:
            if killer == slot and rnd.team.get(victim) == side and t0 < te <= t:
                found.append((te, "kill", KILL_AREA_M))
        if rnd.planter is not None and rnd.planter[1] == slot and t0 < rnd.planter[0] <= t:
            found.append((rnd.planter[0], "plant", PLANT_AREA_M))
        for te, _, by, target, _ in rnd.gun_runs:
            if by == slot and rnd.team.get(target) == side and t0 < te <= t:
                found.append((te, "damage", DAMAGE_AREA_M))
        for te, by, gun in rnd.shots:
            if by == slot and t0 < te <= t:
                p = rnd.pos(slot, te)
                if p is not None and self._heard_by(rnd, side, te, p[0], p[1],
                                                    GUN_HEARING_M.get(gun, GUN_HEARING_DEFAULT_M)):
                    found.append((te, "gunfire", SHOT_AREA_M))
        v = rnd.speed(slot, t)
        if v is not None and v > AUDIBLE_MPS:
            p = rnd.pos(slot, t)
            if p is not None and self._heard_by(rnd, side, t, p[0], p[1], FOOTSTEP_RANGE_M):
                found.append((t, "footsteps", FOOTSTEP_AREA_M))
        if not found:
            return None
        return max(found, key=lambda f: (f[0], -f[2]))

    def _area(self, node: int, x: float, y: float, radius_m: float, room: np.ndarray) -> np.ndarray:
        """Nodes within radius_m of (x, y) px, reached by walking from `node` through `room` (plus `node`)."""
        seed = np.zeros(self.geo.n, bool)
        seed[node] = True
        within = room | seed
        steps = max(1, int(math.ceil(radius_m / self.geo.cell_m)))
        grown = self.topo.dilate(seed, eight=True, iterations=steps, within=within)
        r = radius_m / self.geo.m_per_px
        near = (self.geo.centres[:, 0] - x) ** 2 + (self.geo.centres[:, 1] - y) ** 2 <= r * r
        return (grown & within & near) | seed
```

In `apply`, at the start of the per-side loop add `self.events[side] = []`, and set `rnd = getattr(tick, "rnd",
None)` once before the loop. The per-enemy body becomes (new lines marked `# new`; the rest is Task 4's):

```python
            for slot in sorted(enemies):
                reached = self.reached[side].get(slot)
                before = np.full(n, np.inf) if reached is None else reached.copy()
                origin: dict[int, int] = {}                                        # new
                h = tick.holders.get(slot)
                if reached is None:
                    reached = np.full(n, np.inf)
                    if self._prev_t is None:
                        if self._start[side] is not None:
                            reached[self._start[side]] = t    # the barrier drop's ground: there now
                    else:                                                          # new: a second life
                        self.located[side][slot] = t
                        self.events[side].append((slot, t, "revived"))
                if rnd is not None and self._prev_t is not None:                   # new
                    hit = self._locating(rnd, side, slot, self._prev_t, t)
                    p = rnd.pos(slot, hit[0]) if hit is not None else None
                    if hit is not None and p is not None:
                        te, kind, radius = hit
                        centre = rnd.node(slot, te, p[0], p[1])
                        area = self._area(centre, p[0], p[1], radius, room)
                        reached = np.full(n, np.inf)
                        reached[area] = te
                        origin = {int(c): centre for c in np.flatnonzero(area) if c != centre}
                        self.seen[side].pop(slot, None)
                        self.located[side][slot] = te
                        self.events[side].append((slot, te, kind))
                # in an active view or a watcher's; or seen at their own height in an active cone (Tick._direct)
                in_cone = any(active and target == slot and viewer in tick.holders and tick.holders[viewer].team == side
                              for (viewer, target), active in getattr(tick, "direct", {}).items())
                if h is not None and (spots[h.cell] or in_cone):
                    reached = np.full(n, np.inf)    # spotted: there, and nowhere else
                    self.seen[side][slot] = (h.cell, t)
                    self.located[side][slot] = t                                   # new
                    self.events[side].append((slot, t, "seen"))                    # new
                    origin = {}
                if h is not None:
                    reached[h.cell] = min(reached[h.cell], t)   # an enemy pushes it out from where they stand
                reached[~room] = np.inf
                reached, parent = self._spread(reached, room, free, t, self.seen[side].get(slot))
                self._record(side, slot, before, reached, parent, origin)
                self.reached[side][slot] = reached
                cells |= np.isfinite(reached)
```

The area's nodes outside `room` are cleared by `reached[~room] = np.inf` like any other source, and an
area-collapsed centre is logged before the area (equal times; `_record` breaks ties by node, so make the
centre first): in `_record`, change the sort to put origin targets after their centre:

```python
        centres = set(origin.values()) if origin else set()
        order = changed[np.lexsort((changed, [0 if c in centres else 1 for c in changed.tolist()], after[changed]))]
```

At the very end of `apply` (after the side loop) add `self._prev_t = t`.

- [ ] **Step 6: Run the new tests**

Run: `PY -m pytest tests/replays/test_gaps_locating.py tests/replays/test_gaps_routes.py tests/replays/test_control_unknown.py -q`
Expected: PASS.

- [ ] **Step 7: Bump the revision and re-pin (named replacements)**

In `app/replays/control_format.py` set `CONTROL_REVISION = 5`. Run
`PY -m pytest tests/replays/test_control_format.py::test_the_engine_constants_are_pinned_to_the_control_revision -q`;
it fails and prints the digest. Add `5: "<digest>"` to `PINNED` and extend the comment above it:

```python
# 5: timing gaps (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 4): the unknown's locating
# events (KILL_AREA_M ... GUN_HEARING_M, from app/control/hearing.json). Unreleased, so re-pinned in place.
```

Then re-record the flat reference: `PY scripts/control_reference.py --toys` (expect DIFFERS for the rounds whose
enemies are located by the new rules: at least `midwall`, which has a gun wall-bang, a shot and a death), then
`PY scripts/control_reference.py --toys --write`. In `tests/replays/test_control_reference.py`'s module
docstring add one line: "Re-recorded 2026-10-02 for CONTROL_REVISION 5 (the timing-gaps locating events)."

- [ ] **Step 8: Run the whole control suite**

Run: `PY -m pytest tests/replays -q`
Expected: PASS. Any other failing existing test: stop and report it to the owner (Global Constraints).

- [ ] **Step 9: Update the engine docstring**

In the module docstring's **Unknown** bullet (lines 28-33), append: "An enemy is also located, as an area
round them, by a kill, the plant, audible movement, gunfire within hearing and gun damage (the timing-gaps
spec, section 4); a revived enemy restarts where they stand."

- [ ] **Step 10: Commit**

```
git add app/control/engine.py app/replays/control_format.py tests/replays/test_gaps_locating.py tests/replays/test_control_format.py tests/replays/test_control_reference.py tests/fixtures/control/reference_flat.json
git commit -F <msg file>   # "Unknown: kills, plant, footsteps, gunfire and gun damage locate an enemy (revision 5)"
```

---

### Task 6: Observer hook and tick cache

Review fixes: R5 (cache key), R12 (`view`), R13 (`eye_z`), R20 (`Writer.close(missing)`).

**Files:**
- Create: `webapp/app/control/observe.py`
- Create: `webapp/app/gaps/__init__.py` (empty docstring module)
- Create: `webapp/app/gaps/cache.py`
- Modify: `webapp/app/control/engine.py` (`compute_round` signature line 1913 and loop line ~1952)
- Test: `webapp/tests/replays/test_gaps_observe.py`

**Interfaces:**
- Produces:
  - `observe.PlayerView(slot: int, team: str, node: int, x: float, y: float, yaw: float, live: np.ndarray,
    utility: np.ndarray)` (`live` is `Tick.live[slot]`: view, utility and own node; `utility` is `Holder.watch`)
  - `observe.TickRecord(t: float, players: dict[int, PlayerView], unknown: dict[str, dict[int, np.ndarray]],
    events: dict[str, list[tuple[int, float, str]]], smokes: list)`
  - `observe.record(tick, unknown) -> TickRecord` (arrays are the live engine arrays: consume at once)
  - `compute_round(..., observer=None)`; the observer is called as `observer(record, runner.unknown)` once
    per tick after `runner.step`.
  - `cache.cache_path(replay_id: int, round_number: int, fingerprint: str, directory: Path | None = None) -> Path`
  - `class cache.Writer(path)`: `__call__(record, unknown)` (an observer), `close()`
  - `cache.read(path) -> tuple[list[TickRecord], dict[str, RouteLog]]`: records with full entry arrays rebuilt
    one at a time as a generator: `cache.replay(path)` yields `(record, logs)`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_gaps_observe.py`:

```python
"""The observer hook and the tick cache (timing-gaps spec, section 3)."""

import numpy as np

from app.control import engine as ce
from app.gaps import cache
from tests.replays.control_toys import blob, door_hall


def _round():
    return blob({0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180), (6.0, 300, 270, 180)])}, t_end=6.0)


def test_control_is_byte_identical_with_an_observer():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import control_reference as ref
    geo, data = door_hall(), _round()
    plain = ref.digest_round(ce.compute_round(data, geo), data)
    watched = ref.digest_round(ce.compute_round(data, geo, observer=lambda rec, unk: None), data)
    assert plain == watched


def test_the_observer_sees_every_tick_with_players_and_unknown():
    geo, data = door_hall(), _round()
    seen = []
    rc = ce.compute_round(data, geo, observer=lambda rec, unk: seen.append(
        (rec.t, sorted(rec.players), {s: {e: int((a >= 0).sum()) for e, a in d.items()} for s, d in rec.unknown.items()})))
    assert [t for t, _, _ in seen] == rc.ticks.tolist()
    assert seen[0][1] == [0, 5]
    assert seen[-1][2]["A"][5] > 0


def test_cache_keys_differ_by_round(tmp_path):
    a = cache.cache_path(7, 1, "abcd", tmp_path)
    b = cache.cache_path(7, 2, "abcd", tmp_path)
    c = cache.cache_path(8, 1, "abcd", tmp_path)
    assert len({a, b, c}) == 3


def test_the_cache_replays_what_the_observer_saw(tmp_path):
    geo, data = door_hall(), _round()
    live = []
    path = cache.cache_path(1, 1, "f", tmp_path)
    writer = cache.Writer(path)

    def both(rec, unk):
        live.append((rec.t, {s: {e: a.copy() for e, a in d.items()} for s, d in rec.unknown.items()}))
        writer(rec, unk)

    ce.compute_round(data, geo, observer=both)
    writer.close()
    replayed = list(cache.replay(path))
    assert [rec.t for rec, _ in replayed] == [t for t, _ in live]
    for (rec, logs), (_, want) in zip(replayed, live):
        for side, enemies in want.items():
            for e, arr in enemies.items():
                assert np.array_equal(rec.unknown[side][e], arr)
    assert len(replayed[-1][1]["A"]) > 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PY -m pytest tests/replays/test_gaps_observe.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.gaps'`).

- [ ] **Step 3: Implement `webapp/app/control/observe.py`**

```python
"""What the control engine shows an observer each tick (docs/superpowers/specs/2026-10-02-timing-gaps-design.md,
section 3): plain values, taken after the tick's unknown is applied. The arrays are the engine's own: an
observer must use or copy them before returning."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PlayerView:
    slot: int
    team: str
    node: int
    x: float
    y: float
    yaw: float
    live: np.ndarray        # Tick.live: view (active, passive, presence), utility and own node; no memory
    utility: np.ndarray     # Holder.watch


@dataclass
class TickRecord:
    t: float
    players: dict           # slot -> PlayerView (live players with a position)
    unknown: dict           # side -> enemy slot -> entry id per node (-1 outside); Unknown.entry
    events: dict            # side -> [(enemy, time, kind)]; Unknown.events
    smokes: list


def record(tick, unknown) -> TickRecord:
    players = {}
    for s, h in tick.holders.items():
        p = tick.rnd.pos(s, tick.t)
        players[s] = PlayerView(s, h.team, int(h.cell), float(h.x), float(h.y), float(p[2]) if p else 0.0,
                                tick.live[s], h.watch)
    return TickRecord(float(tick.t), players, unknown.entry, {k: list(v) for k, v in unknown.events.items()},
                      list(tick.smokes))
```

- [ ] **Step 4: Call it from `compute_round`**

Signature: add `observer=None` after `knowledge: bool = True`. Docstring: add "`observer`, when given, is
called as observer(record, unknown) after each tick's unknown (app/control/observe.py)." Right after
`tick = runner.step(Tick(rnd, t, timings), timings)` add:

```python
        if observer is not None:
            observer(observe.record(tick, runner.unknown), runner.unknown)
```

and import `from app.control import observe` at the top.

- [ ] **Step 5: Implement `webapp/app/gaps/cache.py`**

```python
"""The tick cache (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 3): one round's tick
records, kept locally so the gap detector can be re-run without the engine. Unknown entry arrays are stored
as changes from the previous tick; the route logs once, at the end. Local only: never in the database or
the repo. Pickle is safe here because only this module writes these files."""

from __future__ import annotations

import gzip
import pickle
from pathlib import Path

import numpy as np

from app.control.observe import PlayerView, TickRecord

FORMAT = 1


def cache_dir() -> Path:
    from app.control import geometry

    return Path(geometry.cache_dir()) / "gaps"


def cache_path(replay_id: int, round_number: int, fingerprint: str, directory: Path | None = None) -> Path:
    return Path(directory or cache_dir()) / f"{replay_id}-r{round_number}-{fingerprint}.ticks.pkl.gz"


class Writer:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.ticks = []
        self.prev: dict = {}
        self.logs = None

    def __call__(self, rec: TickRecord, unknown) -> None:
        diff = {}
        for side, enemies in rec.unknown.items():
            for e, arr in enemies.items():
                old = self.prev.get((side, e))
                if old is None or len(old) != len(arr):
                    idx = np.flatnonzero(arr >= 0)
                    diff[(side, e)] = ("full", idx.astype(np.int32), arr[idx].astype(np.int64))
                else:
                    idx = np.flatnonzero(arr != old)
                    diff[(side, e)] = ("delta", idx.astype(np.int32), arr[idx].astype(np.int64))
                self.prev[(side, e)] = arr.copy()
        gone = [k for k in self.prev if k[1] not in rec.unknown.get(k[0], {})]
        for k in gone:
            del self.prev[k]
        players = {s: (p.team, p.node, p.x, p.y, p.yaw, np.flatnonzero(p.live).astype(np.int32),
                       np.flatnonzero(p.utility).astype(np.int32)) for s, p in rec.players.items()}
        n = unknown.geo.n
        self.ticks.append((rec.t, players, diff, gone, rec.events, rec.smokes, n))
        self.logs = unknown.log

    def close(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with gzip.open(tmp, "wb") as f:
            pickle.dump({"format": FORMAT, "ticks": self.ticks, "logs": self.logs}, f, protocol=5)
        tmp.replace(self.path)


def replay(path: Path):
    """Yields (TickRecord, logs) in tick order, rebuilding each tick's full entry arrays."""
    with gzip.open(path, "rb") as f:
        body = pickle.load(f)
    if body.get("format") != FORMAT:
        raise ValueError(f"tick cache format {body.get('format')}, expected {FORMAT}")
    logs, state = body["logs"], {}
    for t, players, diff, gone, events, smokes, n in body["ticks"]:
        for k in gone:
            state.pop(k, None)
        for k, (how, idx, val) in diff.items():
            if how == "full" or k not in state:
                arr = np.full(n, -1, np.int64)
            else:
                arr = state[k].copy()
            arr[idx] = val
            state[k] = arr
        unknown: dict = {"A": {}, "B": {}}
        for (side, e), arr in state.items():
            unknown[side][e] = arr
        views = {}
        for s, (team, node, x, y, yaw, live, util) in players.items():
            lv, ut = np.zeros(n, bool), np.zeros(n, bool)
            lv[live], ut[util] = True, True
            views[s] = PlayerView(s, team, node, x, y, yaw, lv, ut)
        yield TickRecord(t, views, unknown, events, smokes), logs
```

`Writer` keeps the final `RouteLog`s (they only ever grow), so every entry id any tick referenced is there.

- [ ] **Step 6: Run the tests**

Run: `PY -m pytest tests/replays/test_gaps_observe.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```
git add app/control/observe.py app/control/engine.py app/gaps/__init__.py app/gaps/cache.py tests/replays/test_gaps_observe.py
git commit -F <msg file>   # "compute_round observer and the local tick cache"
```

---

### Task 7: Predicted gaps

Review fixes: R7, R9, R10, R11, R12, R13.

**Files:**
- Create: `webapp/app/gaps/detect.py`
- Test: `webapp/tests/replays/test_gaps_detect.py`

**Interfaces:**
- Consumes: `TickRecord`, `PlayerView` (Task 6); `RouteLog` (Task 4); `RoundInputs` (engine, incl. Task 5's
  `kills`, `gun_runs`, `planter`); `engine.seen_from`.
- Produces:
  - constants `MIN_UNSEEN_S = 5.0`, `CLOSE_AFTER_S = 5.0`, `FLICKER_S = 1.0`, `RESULT_WINDOW_S = 3.0`,
    `SHOT_LOOKBACK_S = 0.5`, `BEHIND_DEG = 120.0`, `ROUTE_THIN_S = 0.5`, `GAPS_REVISION = 1`
  - `@dataclass Gap` (fields listed in Step 3)
  - `class GapDetector(geo, rnd)`: `step(rec: TickRecord, logs: dict[str, RouteLog])`, `finish() -> list[Gap]`
    (predicted gaps; Task 8 adds back-shots inside `finish`), `notes: Counter`
  - `off_facing(yaw: float, x0, y0, x1, y1) -> float` (degrees, 0..180)

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_gaps_detect.py`. These drive the detector through the real engine on toy
rounds (`run`), so the unknown, chokes and timing are the engine's own.

```python
"""Predicted gaps (timing-gaps spec, section 5) on toy rounds through the real engine."""

import numpy as np
import pytest

from app.control import engine as ce
from app.gaps import detect as gd
from tests.replays.control_toys import blob, door_hall, open_hall


def run(geo, data, link=None, chokes=None):
    rnd = ce.RoundInputs(data, geo, link)
    det = gd.GapDetector(geo, rnd)
    ce.compute_round(data, geo, link, observer=lambda rec, unk: det.step(rec, unk.log), knowledge=False)
    return det.finish()


def predicted(gaps):
    return [g for g in gaps if g.kind == "predicted"]


def test_off_facing():
    assert gd.off_facing(0, 0, 0, 10, 0) == pytest.approx(0)
    assert gd.off_facing(0, 0, 0, -10, 0) == pytest.approx(180)
    assert gd.off_facing(90, 0, 0, 10, 0) == pytest.approx(90)


def test_an_enemy_behind_a_player_opens_one_gap_with_open_timing():
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=8.0)
    gaps = predicted(run(geo, data))
    mine = [g for g in gaps if g.victim == 0]
    assert len(mine) == 1, "one route behind one victim is one gap, however many cells"
    g = mine[0]
    assert g.cause == "open_timing" and g.candidates.keys() == {5}
    assert g.angle_deg > gd.BEHIND_DEG and g.choke_seq == ()


def test_an_enemy_in_front_opens_nothing():
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=8.0)
    assert [g for g in predicted(run(geo, data)) if g.victim == 0] == []


def test_the_5s_wait_after_a_sighting():
    geo = open_hall()
    # A faces east (sees B) until 3 s, then turns west: B was located at 3 s, so no gap before 8 s.
    data = blob({0: ("A", [(0.0, 150, 200, 0), (3.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])},
                t_end=12.0)
    mine = [g for g in predicted(run(geo, data)) if g.victim == 0]
    assert mine and mine[0].t_open >= 3.0 + gd.MIN_UNSEEN_S - 0.5


def test_turning_to_face_the_enemy_and_away_again_keeps_one_gap():
    geo = open_hall()
    # A faces west (B behind) 0-3 s, faces east and sees B 3-4 s, faces west again from 4 s. B's own cell is
    # always unknown and in A's line, so the gap is never unexposed: one gap, closed only by the round's end.
    data = blob({0: ("A", [(0.0, 150, 200, 180), (3.0, 150, 200, 0), (4.0, 150, 200, 180)]),
                 5: ("B", [(0.0, 400, 200, 180)])}, t_end=14.0)
    [g] = [g for g in predicted(run(geo, data)) if g.victim == 0]
    assert g.t_open == pytest.approx(0.0) and g.t_close == pytest.approx(14.0)


# Hand-made records: the close and glance rules need exposure to come and go on cue, which the engine's
# unknown (pushed out from the enemy's own cell every tick) never does in an open hall.

def _manual(geo, frames, t_decided=60.0):
    """frames: [(t, nodes of enemy 5's unknown)]; victim 0 (team A) stands at (150, 200) facing west."""
    from app.control.observe import PlayerView, TickRecord
    from app.control.routes import RouteLog

    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])},
                t_end=t_decided + 1.0, t_decided=t_decided)
    rnd = ce.RoundInputs(data, geo)
    logs = {"A": RouteLog(), "B": RouteLog()}
    det = gd.GapDetector(geo, rnd)
    z = np.zeros(geo.n, bool)
    for t, nodes in frames:
        ent = np.full(geo.n, -1, np.int64)
        for node in nodes:
            ent[node] = logs["A"].add(node, t, -1, -1)
        p = PlayerView(0, "A", geo.cell_of_px(150, 200), 150.0, 200.0, 180.0, z.copy(), z.copy())
        det.step(TickRecord(t, {0: p}, {"A": {5: ent}, "B": {}}, {"A": [], "B": []}, []), logs)
    return [g for g in det.finish() if g.kind == "predicted"]


def test_closes_5s_after_last_exposure_and_a_return_is_a_new_gap():
    geo = open_hall()
    behind = [geo.cell_of_px(300, 200)]
    frames = [(t, behind) for t in (0.0, 0.5, 1.0, 1.5, 2.0)] + [(t, []) for t in (2.5, 4.0, 6.0, 7.0)] + \
             [(8.0, behind), (8.5, behind)]
    first, second = _manual(geo, frames)
    assert first.t_last_exposed == pytest.approx(2.0) and first.t_close == pytest.approx(7.0)
    assert second.t_open == pytest.approx(8.0)


def test_a_glance_back_within_5s_is_the_same_gap():
    geo = open_hall()
    behind = [geo.cell_of_px(300, 200)]
    frames = [(t, behind) for t in (0.0, 0.5, 1.0)] + [(t, []) for t in (1.5, 2.0, 2.5)] + \
             [(t, behind) for t in (3.0, 3.5, 4.0)]
    [g] = _manual(geo, frames)
    assert g.t_open == pytest.approx(0.0) and g.t_close == pytest.approx(60.0)
    assert g.qualified_s == pytest.approx(1.5 + 1.0 + (60.0 - 4.0)), "time qualifying, clipped at the close"


def test_nothing_is_detected_after_the_round_is_decided():
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=10.0, t_decided=3.0)
    gaps = predicted(run(geo, data))
    assert all(g.t_open <= 3.0 for g in gaps)
    assert all(g.t_close is not None and g.t_close <= 3.0 for g in gaps)


def test_the_victims_death_closes_their_gap():
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=10.0, deaths={0: 4.0})
    [g] = [g for g in predicted(run(geo, data)) if g.victim == 0]
    assert g.t_close == pytest.approx(4.0)


def test_flicker_flag():
    geo = open_hall()
    # A faces west (B behind) for 0.5 s, then faces east (sees B) for the rest.
    data = blob({0: ("A", [(0.0, 150, 200, 180), (0.5, 150, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])},
                t_end=8.0)
    [g] = [g for g in predicted(run(geo, data)) if g.victim == 0]
    assert g.qualified_s < gd.FLICKER_S and g.flicker


def test_stood_is_recorded_when_the_enemy_stands_in_the_gap():
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=6.0)
    [g] = [g for g in predicted(run(geo, data)) if g.victim == 0]
    assert g.stood_by == 5 and g.stood_at is not None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PY -m pytest tests/replays/test_gaps_detect.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.gaps.detect'`).

- [ ] **Step 3: Implement `webapp/app/gaps/detect.py`**

```python
"""Predicted gaps (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 5): unknown space behind
a player, read online from the control engine's tick records (app/control/observe.py). One gap per victim
life and choke sequence; it opens when a node of that sequence first qualifies (exposed to the victim,
within their rear 120 degrees, the enemy unlocated for MIN_UNSEEN_S or never located), stays latched, and
closes CLOSE_AFTER_S after its last exposure (facing ignored), on the victim's death, or at t_decided.
Back-shots: app/gaps/backshots.py, run from `finish`."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from app.control.engine import seen_from

MIN_UNSEEN_S = 5.0
CLOSE_AFTER_S = 5.0
FLICKER_S = 1.0
RESULT_WINDOW_S = 3.0
SHOT_LOOKBACK_S = 0.5
BEHIND_DEG = 120.0
ROUTE_THIN_S = 0.5
GAPS_REVISION = 1
CAUSE_ORDER = ("route_released", "victim_turned", "victim_moved", "open_timing")


def off_facing(yaw: float, x0: float, y0: float, x1: float, y1: float) -> float:
    bearing = math.degrees(math.atan2(y1 - y0, x1 - x0))
    return abs((bearing - yaw + 180) % 360 - 180)


@dataclass
class Gap:
    kind: str                         # "predicted" | "backshot"
    victim: int
    team: str                         # the victim's side group, "A" / "B"
    seq: int                          # choke sequence id in the victim team's log; -1 for none known
    choke_seq: tuple | None
    t_open: float
    spot: int
    victim_node: int
    distance_m: float
    angle_deg: float
    route: list                       # [[[t, x px, y px], ...], ...]: unbroken pieces
    life: int = 0
    cause: str | None = None
    cause_detail: dict | None = None
    candidates: dict = field(default_factory=dict)     # enemy slot -> distance m when they joined, or None
    t_last_exposed: float | None = None
    t_close: float | None = None
    qualified_s: float = 0.0
    checked_at: list = field(default_factory=list)
    stood_at: float | None = None
    stood_by: int | None = None
    shot_at: float | None = None
    shot_by: int | None = None
    killed_at: float | None = None
    killed_by: int | None = None
    victim_won_at: float | None = None
    context: dict = field(default_factory=dict)
    linked: "Gap | None" = None
    stood_times: dict = field(default_factory=lambda: defaultdict(list))   # enemy -> times stood in it
    _qual_prev: bool = False
    _checked: bool = False

    @property
    def flicker(self) -> bool:
        return self.kind == "predicted" and self.qualified_s < FLICKER_S


class GapDetector:
    def __init__(self, geo, rnd):
        self.geo, self.rnd = geo, rnd
        self.notes: Counter = Counter()
        self.gaps: list[Gap] = []
        self.open: dict[tuple, Gap] = {}
        self.located: dict[str, dict[int, list]] = {"A": defaultdict(list), "B": defaultdict(list)}
        self.release_t = {s: np.full(geo.n, -np.inf) for s in ("A", "B")}
        self.release_why: dict[str, dict[int, dict]] = {"A": {}, "B": {}}
        self.observed_prev: dict[str, np.ndarray | None] = {"A": None, "B": None}
        self.owner_prev: dict[str, tuple | None] = {"A": None, "B": None}
        self.player_prev: dict[int, tuple] = {}
        self.exposed_prev: dict[int, set] = {}
        self.smokes_prev: list = []
        self.prev_t: float | None = None
        self.logs = None
        self.done = False

    # ---------------------------------------------------------------- helpers

    def _metres(self, x0, y0, x1, y1) -> float:
        return math.hypot(x1 - x0, y1 - y0) * self.geo.m_per_px

    def _life(self, slot: int, t: float) -> int:
        for i, (a, b) in enumerate(self.rnd.lives.get(slot, [])):
            if a <= t < b:
                return i
        return -1

    def last_located(self, side: str, enemy: int, before: float, inclusive: bool = True,
                     ignore_gunfire_from: float | None = None) -> float | None:
        times = [te for te, kind in self.located[side][enemy]
                 if (te <= before if inclusive else te < before)
                 and not (ignore_gunfire_from is not None and kind == "gunfire" and te >= ignore_gunfire_from)]
        return max(times) if times else None

    def wait_over(self, side: str, enemy: int, t: float, **kw) -> bool:
        last = self.last_located(side, enemy, t, **kw)
        return last is None or t - last >= MIN_UNSEEN_S

    def _route(self, log, entry: int) -> list:
        entries = log.trace(entry)
        pts, last = [], None
        for e in entries:
            te = float(log.t[e])
            if last is None or te - last >= ROUTE_THIN_S or e == entries[-1]:
                x, y = self.geo.centres[int(log.node[e])]
                pts.append([round(te, 3), round(float(x), 1), round(float(y), 1)])
                last = te
        return [pts]

    # ---------------------------------------------------------------- per tick

    def step(self, rec, logs) -> None:
        if self.done:
            return
        self.logs = logs
        t = rec.t
        if t > self.rnd.t_decided:
            self._close_all(self.rnd.t_decided)
            self.done = True
            return
        for side, evs in rec.events.items():
            for enemy, te, kind in evs:
                self.located[side][enemy].append((float(te), kind))
        for side in ("A", "B"):
            self._observe(side, rec)
        for key, g in list(self.open.items()):
            if not self.rnd.alive(g.victim, t):
                self._close(key, self.rnd.life_end(g.victim, g.t_open), t)
        for p in rec.players.values():
            self._victim(p, rec, logs)
        for key, g in list(self.open.items()):
            if g.t_last_exposed is not None and t - g.t_last_exposed >= CLOSE_AFTER_S:
                self._close(key, g.t_last_exposed + CLOSE_AFTER_S, t)
        self.smokes_prev = list(rec.smokes)
        self.prev_t = t

    def _observe(self, side: str, rec) -> None:
        """Who observes each node for `side`, and when each node was last released, with why."""
        n = self.geo.n
        observed = np.zeros(n, bool)
        owner = np.full(n, -1, np.int64)
        by_util = np.zeros(n, bool)
        for s in sorted((s for s, p in rec.players.items() if p.team == side), reverse=True):
            p = rec.players[s]
            view = p.live & ~p.utility
            owner[p.utility] = s
            by_util[p.utility] = True
            owner[view] = s
            by_util[view] = False
            observed |= p.live
        prev = self.observed_prev[side]
        if prev is not None:
            released = np.flatnonzero(prev & ~observed)
            if len(released):
                p_owner, p_util = self.owner_prev[side]
                self.release_t[side][released] = rec.t
                reasons: dict = {}
                for node in released.tolist():
                    key = (int(p_owner[node]), bool(p_util[node]))
                    if key not in reasons:
                        reasons[key] = self._reason(key[0], key[1], rec)
                    self.release_why[side][node] = {"t": rec.t, "player": key[0],
                                                    "by": "utility" if key[1] else "view", "reason": reasons[key]}
        self.observed_prev[side] = observed
        self.owner_prev[side] = (owner, by_util)

    def _reason(self, slot: int, util: bool, rec) -> str:
        rnd, t = self.rnd, rec.t
        if slot < 0 or slot not in rec.players:
            return "died"
        if util:
            for w in rnd.watchers:
                if w.by == slot and w.kind in ("camera", "drone") and w.in_use and self.prev_t is not None:
                    was = any(a <= self.prev_t < b for a, b in w.in_use)
                    now = any(a <= t < b for a, b in w.in_use)
                    if was and not now:
                        return "utility_left"
            return "utility_expired"
        if any(a <= t < b for a, b in rnd.flashed.get(slot, [])) or \
                any(a <= t < b for a, b in rnd.nearsight.get(slot, [])):
            return "blinded"
        if len(rec.smokes) > len(self.smokes_prev):
            return "smoked"
        before = self.player_prev.get(slot)
        p = rec.players[slot]
        if before is not None and before[0] != p.node:
            return "moved"
        if before is not None and abs((p.yaw - before[1] + 180) % 360 - 180) > 1.0:
            return "turned"
        return "other"

    def _victim(self, p, rec, logs) -> None:
        t, side = rec.t, p.team
        enemies = rec.unknown.get(side) or {}
        life = self._life(p.slot, t)
        exposed_seqs: set = set()
        quals: dict[int, list] = defaultdict(list)     # seq -> [(enemy, nodes)]
        if enemies:
            log = logs[side]
            seen = seen_from(self.geo, np.array([p.node]), rec.smokes)
            cx, cy = self.geo.centres[:, 0], self.geo.centres[:, 1]
            for enemy, ent in enemies.items():
                nodes = np.flatnonzero(seen & (ent >= 0))
                if not len(nodes):
                    continue
                seqs = log.seq[ent[nodes]]
                exposed_seqs.update(np.unique(seqs).tolist())
                if not self.wait_over(side, enemy, t):
                    continue
                bearing = np.degrees(np.arctan2(cy[nodes] - p.y, cx[nodes] - p.x))
                behind = np.abs((bearing - p.yaw + 180) % 360 - 180) > BEHIND_DEG
                for sid in np.unique(seqs[behind]).tolist():
                    quals[sid].append((enemy, nodes[behind & (seqs == sid)]))
        for sid in exposed_seqs:
            g = self.open.get((p.slot, life, sid))
            if g is not None:
                g.t_last_exposed = t
        qualifying_now = set()
        for sid, found in quals.items():
            key = (p.slot, life, sid)
            g = self.open.get(key)
            if g is None:
                g = self._open_gap(p, rec, logs, sid, found, life)
                self.open[key] = g
                self.gaps.append(g)
            qualifying_now.add(key)
            all_nodes = np.concatenate([nodes for _, nodes in found])
            for enemy, _ in found:
                if enemy not in g.candidates:
                    e = rec.players.get(enemy)
                    sx, sy = self.geo.centres[g.spot]
                    g.candidates[enemy] = None if e is None else round(self._metres(e.x, e.y, sx, sy), 2)
                    if e is None:
                        self.notes["candidate without a position"] += 1
            for enemy in g.candidates:
                e = rec.players.get(enemy)
                if e is not None and e.node in set(all_nodes.tolist()):
                    g.stood_times[enemy].append(t)
                    if g.stood_at is None:
                        g.stood_at, g.stood_by = t, enemy
        for key, g in self.open.items():
            if key[0] != p.slot:
                continue
            if g._qual_prev and self.prev_t is not None:
                g.qualified_s += t - self.prev_t
            g._qual_prev = key in qualifying_now
            covered = bool((p.live & ~p.utility)[g.spot])
            if covered and not g._checked:
                g.checked_at.append(t)
            g._checked = covered
        self.exposed_prev[p.slot] = exposed_seqs - {k[2] for k in qualifying_now}
        self.player_prev[p.slot] = (p.node, p.yaw)

    def _open_gap(self, p, rec, logs, sid: int, found: list, life: int) -> Gap:
        side, t = p.team, rec.t
        log = logs[side]
        best = None
        for enemy, nodes in found:
            ent = rec.unknown[side][enemy]
            for node in nodes.tolist():
                key = (float(log.t[ent[node]]), node)
                if best is None or key < best[0]:
                    best = (key, enemy, int(ent[node]))
        (arrival, spot), enemy, entry = best
        sx, sy = self.geo.centres[spot]
        g = Gap("predicted", p.slot, side, sid, log.seqs[sid], t, spot, p.node, round(self._metres(p.x, p.y, sx, sy), 2),
                round(off_facing(p.yaw, p.x, p.y, sx, sy), 1), self._route(log, entry), life=life, t_last_exposed=t)
        g.cause, g.cause_detail = self._cause(p, side, sid, enemy, log, entry, arrival, t)
        alive = Counter(self.rnd.team[s] for s in self.rnd.team if self.rnd.alive(s, t))
        g.context = {"t_round": round(t - self.rnd.t_start, 3), "alive": {"A": alive["A"], "B": alive["B"]},
                     "spike": "planted" if self.rnd.plant is not None and self.rnd.plant <= t else "not planted",
                     "victim_sees_enemy": any(e.team != side and (p.live & ~p.utility)[e.node]
                                              for e in rec.players.values())}
        return g

    def _cause(self, p, side, sid, enemy, log, entry, arrival, t) -> tuple[str, dict]:
        options = []
        since = self.last_located(side, enemy, t)
        route_nodes = log.node[log.trace(entry)]
        rel = self.release_t[side][route_nodes]
        if since is not None:
            rel = np.where(rel >= since, rel, -np.inf)
        if np.isfinite(rel).any():
            i = int(np.argmax(rel))
            node = int(route_nodes[i])
            why = self.release_why[side].get(node, {"t": float(rel[i]), "player": -1, "by": "view", "reason": "other"})
            options.append((float(rel[i]), "route_released", {"cell": node, **why}))
        if sid in self.exposed_prev.get(p.slot, set()):
            before = self.player_prev.get(p.slot)
            moved = before is not None and before[0] != p.node
            options.append((t, "victim_moved" if moved else "victim_turned", {}))
        ready = arrival if since is None else max(arrival, since + MIN_UNSEEN_S)
        options.append((min(ready, t), "open_timing", {}))
        best = max(options, key=lambda o: (o[0], -CAUSE_ORDER.index(o[1])))
        return best[1], best[2]

    def _close(self, key, t_close: float, now: float) -> None:
        g = self.open.pop(key)
        t_close = min(t_close, self.rnd.t_decided)
        if g._qual_prev and self.prev_t is not None:
            g.qualified_s += max(0.0, min(t_close, now) - self.prev_t)
            g._qual_prev = False
        g.t_close = t_close

    def _close_all(self, t: float) -> None:
        for key in list(self.open):
            self._close(key, t, t)

    # ---------------------------------------------------------------- end

    def finish(self) -> list[Gap]:
        if not self.done:
            self._close_all(self.rnd.t_decided)    # the last tick came before t_decided: still open until then
            self.done = True
        from app.gaps import backshots

        backshots.add(self)
        return sorted(self.gaps, key=lambda g: (g.t_open, g.victim, g.kind))
```

Create `webapp/app/gaps/backshots.py` now as a stub that Task 8 fills, so `finish` imports cleanly:

```python
"""Back-shots (timing-gaps spec, section 6). Filled in by Task 8."""


def add(detector) -> None:
    return None
```

(This stub is replaced in full by Task 8, Step 3.)

- [ ] **Step 4: Run the tests**

Run: `PY -m pytest tests/replays/test_gaps_detect.py -q`
Expected: PASS. A toy test that fails because the engine's real unknown differs from the test's assumption
(for example the presence bubble covering a cell): print the gap list and the unknown at that tick, and fix
the toy's positions, not the rule. Report any rule doubt to the owner.

- [ ] **Step 5: Commit**

```
git add app/gaps/detect.py app/gaps/backshots.py tests/replays/test_gaps_detect.py
git commit -F <msg file>   # "Timing gaps: predicted gaps from the engine's unknown"
```

---

### Task 8: Back-shots, linking and levels of use

Review fixes: R16; R7's kill-at-use test lives here if Task 7 cannot express it.

**Files:**
- Modify (replace the stub): `webapp/app/gaps/backshots.py`
- Test: `webapp/tests/replays/test_gaps_backshots.py`

**Interfaces:**
- Consumes: `GapDetector` internals from Task 7: `gaps`, `rnd`, `geo`, `logs`, `last_located`, `wait_over`,
  `_metres`, `notes`, constants.
- Produces: `add(detector) -> None` appends back-shot `Gap`s (kind `"backshot"`) and fills `shot_*`,
  `killed_*`, `victim_won_at` on predicted gaps; `choke_path(geo, nodes, node_choke) -> tuple`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_gaps_backshots.py`:

```python
"""Back-shots (timing-gaps spec, section 6) on toy rounds through the real engine."""

import pytest

from app.gaps import detect as gd
from tests.replays.control_toys import blob, open_hall
from tests.replays.test_gaps_detect import run


def _dmg(t, by=5, target=0, wall=False):
    return {"k": "damage", "t": t, "t1": t + 0.1, "by": by, "target": target, "src": "gun", "wall": wall, "n": 1}


def _behind_round(util, deaths=None, kills=None, t_end=12.0):
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=t_end,
                util=util, deaths=deaths)
    data["kills"] = kills or []
    return data


def backshots(gaps):
    return [g for g in gaps if g.kind == "backshot"]


def test_damage_from_behind_by_an_unlocated_enemy_is_a_backshot():
    [b] = backshots(run(open_hall(), _behind_round([_dmg(8.0)])))
    assert b.victim == 0 and b.candidates.keys() == {5} and b.t_open == pytest.approx(8.0)
    assert b.angle_deg > gd.BEHIND_DEG and b.route and b.choke_seq == ()


def test_the_shooters_own_gunfire_does_not_cancel_the_backshot():
    shot = {"k": "shot", "t": 7.8, "by": 5, "u": 3900, "v": 1950, "gun": None}
    assert len(backshots(run(open_hall(), _behind_round([shot, _dmg(8.0)])))) == 1


def test_follow_up_damage_within_5s_is_the_same_backshot():
    assert len(backshots(run(open_hall(), _behind_round([_dmg(8.0), _dmg(9.0)])))) == 1


def test_damage_from_in_front_is_not_a_backshot():
    data = blob({0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=12.0, util=[_dmg(8.0)])
    assert backshots(run(open_hall(), data)) == []


def test_a_wallbang_counts_and_is_tagged():
    [b] = backshots(run(open_hall(), _behind_round([_dmg(8.0, wall=True)])))
    assert b.context["wall"] is True


def test_a_backshot_links_to_the_open_gap_and_marks_it_shot_and_killed():
    kills = [{"i": 0, "t": 8.5, "killer": 5, "victim": 0, "u": 0, "v": 0}]
    gaps = run(open_hall(), _behind_round([_dmg(8.0)], deaths={0: 8.5}, kills=kills))
    [b] = backshots(gaps)
    [g] = [g for g in gaps if g.kind == "predicted" and g.victim == 0]
    assert b.linked is g
    assert g.shot_by == 5 and g.shot_at == pytest.approx(8.0)
    assert g.killed_by == 5 and g.killed_at == pytest.approx(8.5)
    assert b.killed_by == 5
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PY -m pytest tests/replays/test_gaps_backshots.py -q`
Expected: FAIL (no back-shots: the stub adds none).

- [ ] **Step 3: Implement `webapp/app/gaps/backshots.py`**

```python
"""Back-shots (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 6), after the round's last
tick: a gun damage run whose attacker is within the victim's rear 120 degrees at its start, by an enemy the
victim's team had not located for MIN_UNSEEN_S (or ever), ignoring the shooter's own gunfire in the
SHOT_LOOKBACK_S before it. Its path is the shooter's real track from that last locating, kept in unbroken
pieces; the choke sequence is known only for a path without a break. Linked to an open predicted gap whose
candidates include the shooter. Also fills the predicted gaps' shot / killed / victim-won levels."""

from __future__ import annotations

import numpy as np

from app.control import chokes
from app.replays import choke_assets
from app.gaps.detect import (BEHIND_DEG, RESULT_WINDOW_S, ROUTE_THIN_S, SHOT_LOOKBACK_S, Gap, off_facing)


def choke_path(nodes: list[int], node_choke: np.ndarray) -> tuple:
    out: list[int] = []
    for node in nodes:
        c = int(node_choke[node])
        if c >= 0 and (not out or out[-1] != c):
            out.append(c)
    return tuple(out)


def _segments(rnd, slot: int) -> list[tuple[float, float]]:
    out = []
    for g in (rnd.blob.get("tracks") or {}).get(str(slot), []):
        out.append((float(g["t0"]), float(g["t0"]) + (len(g["u"]) - 1) / rnd.hz))
    return out


def _real_path(det, slot: int, ta: float, tb: float) -> tuple[list, list[list[int]]]:
    """(pieces of [t, x, y] thinned to ROUTE_THIN_S, pieces of nodes at every sample) from ta to tb."""
    rnd = det.rnd
    pieces, node_pieces = [], []
    for s0, s1 in _segments(rnd, slot):
        lo, hi = max(s0, ta), min(s1, tb)
        if lo > hi:
            continue
        pts, nodes, last = [], [], None
        for t in np.arange(lo, hi + 1e-9, 1.0 / rnd.hz).tolist() + [hi]:
            p = rnd.pos(slot, t)
            if p is None:
                continue
            nodes.append(rnd.node(slot, t, p[0], p[1]))
            if last is None or t - last >= ROUTE_THIN_S or t == hi:
                pts.append([round(t, 3), round(p[0], 1), round(p[1], 1)])
                last = t
        if pts:
            pieces.append(pts)
            node_pieces.append(nodes)
    return pieces, node_pieces


def add(det) -> None:
    rnd, geo = det.rnd, det.geo
    node_choke = chokes.node_chokes(geo, choke_assets.load(geo.name))
    predicted = [g for g in det.gaps if g.kind == "predicted"]
    last_by_pair: dict[tuple[int, int], float] = {}
    shots: list[Gap] = []
    for t0, _, by, target, wall in sorted(rnd.gun_runs):
        if t0 > rnd.t_decided:
            continue
        side = rnd.team[target]
        pv, pe = rnd.pos(target, t0), rnd.pos(by, t0)
        if pv is None or pe is None:
            det.notes["back-shot check without positions"] += 1
            continue
        angle = off_facing(pv[2], pv[0], pv[1], pe[0], pe[1])
        if angle <= BEHIND_DEG:
            continue
        prev = last_by_pair.get((by, target))
        if prev is not None and t0 - prev < 5.0:
            last_by_pair[(by, target)] = t0
            continue
        if not det.wait_over(side, by, t0, inclusive=False, ignore_gunfire_from=t0 - SHOT_LOOKBACK_S):
            continue
        last_by_pair[(by, target)] = t0
        since = det.last_located(side, by, t0, inclusive=False, ignore_gunfire_from=t0 - SHOT_LOOKBACK_S)
        pieces, node_pieces = _real_path(det, by, rnd.t_start if since is None else since, t0)
        seq = choke_path(node_pieces[0], node_choke) if len(node_pieces) == 1 else None
        if len(node_pieces) > 1:
            det.notes["back-shot path with a break (choke sequence unknown)"] += 1
        shooter_node = rnd.node(by, t0, pe[0], pe[1])
        b = Gap("backshot", target, side, -1, seq, t0, shooter_node, rnd.node(target, t0, pv[0], pv[1]),
                round(det._metres(pv[0], pv[1], pe[0], pe[1]), 2), round(angle, 1), pieces,
                candidates={by: round(det._metres(pv[0], pv[1], pe[0], pe[1]), 2)})
        peak = max((v for v in (rnd.speed(by, t) for t in np.arange(t0 - 5.0, t0 + 1e-9, 0.25)) if v is not None),
                   default=None)
        b.context = {"t_round": round(t0 - rnd.t_start, 3), "wall": bool(wall),
                     "peak_speed_mps": None if peak is None else round(peak, 2)}
        for kt, killer, victim in rnd.kills:
            if killer == by and victim == target and t0 <= kt <= t0 + RESULT_WINDOW_S:
                b.killed_at, b.killed_by = kt, by
                break
        open_now = [g for g in predicted if g.victim == target and by in g.candidates
                    and g.t_open <= t0 <= (g.t_close if g.t_close is not None else float("inf"))]
        if open_now:
            same = [g for g in open_now if g.choke_seq == seq]
            sx, sy = pe[0], pe[1]
            b.linked = (same or sorted(open_now, key=lambda g: det._metres(sx, sy, *geo.centres[g.spot])))[0]
        shots.append(b)
    for g in predicted:
        for b in shots:
            if b.victim != g.victim:
                continue
            shooter = next(iter(b.candidates))
            if shooter not in g.candidates:
                continue
            in_window = any(s <= b.t_open <= s + RESULT_WINDOW_S for s in g.stood_times.get(shooter, []))
            open_then = g.t_open <= b.t_open <= (g.t_close if g.t_close is not None else float("inf"))
            if (b.linked is g or open_then or in_window) and g.shot_at is None:
                g.shot_at, g.shot_by = b.t_open, shooter
        marks = sorted([(s, e) for e, ts in g.stood_times.items() for s in ts] +
                       ([(g.shot_at, g.shot_by)] if g.shot_at is not None else []))
        for kt, killer, victim in rnd.kills:
            if victim == g.victim and killer in g.candidates and g.killed_at is None and \
                    any(e == killer and s <= kt <= s + RESULT_WINDOW_S for s, e in marks):
                g.killed_at, g.killed_by = kt, killer
            if killer == g.victim and victim in g.candidates and g.victim_won_at is None and \
                    any(e == victim and s <= kt <= s + RESULT_WINDOW_S for s, e in marks):
                g.victim_won_at = kt
    det.gaps.extend(shots)
```

The "follow-up within 5 s" rule (`last_by_pair`) keeps one row per pair while damage continues even when the
gunfire was out of hearing; normally the damage's own locating (Task 5) already fails the wait.

- [ ] **Step 4: Run the tests**

Run: `PY -m pytest tests/replays/test_gaps_backshots.py tests/replays/test_gaps_detect.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add app/gaps/backshots.py tests/replays/test_gaps_backshots.py
git commit -F <msg file>   # "Timing gaps: back-shots, linking and levels of use"
```

---

### Task 9: Storage

Review fixes: R2 (migration rehearsal on `valomaths_gaps_test` only).

**Files:**
- Modify: `webapp/app/models/replay.py` (add two models after `ReplayRoundControl`)
- Create: `webapp/alembic/versions/0016_replay_gaps.py`
- Create: `webapp/app/services/replay_gaps_store.py`
- Create: `webapp/app/gaps/rows.py`
- Test: `webapp/tests/replays/test_gaps_store.py`

**Interfaces:**
- Consumes: `Gap` (Task 7).
- Produces:
  - models `ReplayRoundGapRun`, `ReplayGap` (columns per spec section 7)
  - `rows.to_rows(gaps: list[Gap], rnd, geo) -> list[dict]` (JSON-ready, `seq` assigned in order; `linked_seq`
    resolved)
  - `replay_gaps_store.store_gaps(session_factory, replay_id: int, round_number: int, run: dict, rows: list[dict]) -> str`
    where `run = {"status", "fingerprint", "gaps_revision", "chokes_hash", "notes", "error"}`; returns `"stored"`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_gaps_store.py`. It reuses the replay fixtures of
`tests/replays/test_replay_store.py` (`TABLES`, `add_match`, `condensed`), the way
`tests/replays/test_control_store.py` does, on SQLite:

```python
"""Timing gaps' storage (timing-gaps spec, section 7): a round's run row and gap rows are replaced together.
SQLite, no engine."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

from test_replay_store import TABLES, add_match, condensed  # noqa: E402,F401  (fixtures)

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models.replay import ReplayGap, ReplayRoundGapRun  # noqa: E402
from app.replays import store  # noqa: E402
from app.services.replay_gaps_store import store_gaps  # noqa: E402


@pytest.fixture
def session_factory(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=TABLES + [ReplayRoundGapRun.__table__, ReplayGap.__table__])
    return sessionmaker(bind=engine)


@pytest.fixture
def stored_round(session_factory, condensed):
    db = session_factory()
    add_match(db)
    replay_id = store.store_replay(db, condensed, source="local").replay_id
    db.close()
    return replay_id, 1


RUN = {"status": "ok", "fingerprint": "f" * 16, "gaps_revision": 1, "chokes_hash": None, "notes": {}, "error": None}
ROW = {"seq": 0, "kind": "predicted", "map": "Toy", "victim_slot": 0, "victim_side": None, "t_open": 1.0,
       "t_last_exposed": 2.0, "t_close": 7.0, "spot_cell": 5, "victim_cell": 6, "distance_m": 3.0,
       "angle_deg": 170.0, "qualified_s": 2.0, "flicker": False, "cause": "open_timing", "cause_detail": {},
       "choke_seq": [], "route": [[[1.0, 1.0, 1.0]]], "candidate_slots": [5], "candidate_distances": {"5": 3.0},
       "checked_at": None, "stood_at": None, "stood_by": None, "shot_at": None, "shot_by": None,
       "killed_at": None, "killed_by": None, "victim_won_at": None, "context": {}, "linked_seq": None}


def test_store_replaces_a_rounds_gaps_in_one_go(session_factory, stored_round):
    replay_id, n = stored_round
    assert store_gaps(session_factory, replay_id, n, RUN, [ROW, {**ROW, "seq": 1}]) == "stored"
    assert store_gaps(session_factory, replay_id, n, RUN, [ROW]) == "stored"
    s = session_factory()
    assert s.query(ReplayGap).filter_by(replay_id=replay_id, round_number=n).count() == 1
    assert s.get(ReplayRoundGapRun, (replay_id, n)).gap_count == 1


def test_a_failed_run_stores_no_rows(session_factory, stored_round):
    replay_id, n = stored_round
    store_gaps(session_factory, replay_id, n, RUN, [ROW])
    assert store_gaps(session_factory, replay_id, n, {**RUN, "status": "failed", "error": "boom"}, []) == "stored"
    s = session_factory()
    assert s.get(ReplayRoundGapRun, (replay_id, n)).status == "failed"
    assert s.query(ReplayGap).count() == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PY -m pytest tests/replays/test_gaps_store.py -q`
Expected: FAIL (`ImportError: cannot import name 'ReplayGap'`).

- [ ] **Step 3: Models**

In `app/models/replay.py`, after `ReplayRoundControl`:

```python
class ReplayRoundGapRun(Base):
    """Timing gaps for one round (migration 0016; docs/superpowers/specs/2026-10-02-timing-gaps-design.md,
    section 7): computed or failed, with what it was computed from. Written by scripts/compute_control.py
    through app/services/replay_gaps_store.py."""

    __tablename__ = "replay_round_gap_runs"
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'failed')", name="ck_replay_round_gap_runs_status"),
        ForeignKeyConstraint(["replay_id", "round_number"], ["replay_rounds.replay_id", "replay_rounds.round_number"],
                             ondelete="CASCADE", name="fk_replay_round_gap_runs_round"),
    )

    replay_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_number: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    gaps_revision: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    chokes_hash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    gap_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ReplayGap(Base):
    """One predicted gap or back-shot (section 7 of the timing-gaps spec). Slots, never player ids: the
    pattern page joins slots to players through replay_players at query time."""

    __tablename__ = "replay_gaps"
    __table_args__ = (
        CheckConstraint("kind IN ('predicted', 'backshot')", name="ck_replay_gaps_kind"),
        ForeignKeyConstraint(["replay_id", "round_number"], ["replay_rounds.replay_id", "replay_rounds.round_number"],
                             ondelete="CASCADE", name="fk_replay_gaps_round"),
    )

    replay_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_number: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    seq: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    map: Mapped[str] = mapped_column(String(64), nullable=False)
    victim_slot: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    victim_side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    t_open: Mapped[float] = mapped_column(REAL, nullable=False)
    t_last_exposed: Mapped[float | None] = mapped_column(REAL, nullable=True)
    t_close: Mapped[float | None] = mapped_column(REAL, nullable=True)
    spot_cell: Mapped[int] = mapped_column(Integer, nullable=False)
    victim_cell: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_m: Mapped[float] = mapped_column(REAL, nullable=False)
    angle_deg: Mapped[float] = mapped_column(REAL, nullable=False)
    qualified_s: Mapped[float | None] = mapped_column(REAL, nullable=True)
    flicker: Mapped[bool] = mapped_column(nullable=False, default=False)
    cause: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cause_detail: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    choke_seq: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    route: Mapped[list] = mapped_column(JSONType, nullable=False)
    candidate_slots: Mapped[list] = mapped_column(JSONType, nullable=False)
    candidate_distances: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    checked_at: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    stood_at: Mapped[float | None] = mapped_column(REAL, nullable=True)
    stood_by: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    shot_at: Mapped[float | None] = mapped_column(REAL, nullable=True)
    shot_by: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    killed_at: Mapped[float | None] = mapped_column(REAL, nullable=True)
    killed_by: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    victim_won_at: Mapped[float | None] = mapped_column(REAL, nullable=True)
    context: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    linked_seq: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
```

`candidate_slots` is JSON (an array of slots) rather than a Postgres array, so the SQLite test database works;
the pattern page (plan 3) filters it in Python.

- [ ] **Step 4: Migration `webapp/alembic/versions/0016_replay_gaps.py`**

Check `ls alembic/versions` first: if a `0016_*` already exists on main after the rebase, use the next number
and set `down_revision` to the current head.

```python
"""replay gaps

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-02

Timing gaps (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 7): one run row per replay round
and one row per predicted gap or back-shot, written only by scripts/compute_control.py. Both reference
replay_rounds with ON DELETE CASCADE. Additive: the ValoMaths demo gets empty tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

J = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "replay_round_gap_runs",
        sa.Column("replay_id", sa.Integer(), primary_key=True),
        sa.Column("round_number", sa.SmallInteger(), primary_key=True),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("fingerprint", sa.String(length=16), nullable=False),
        sa.Column("gaps_revision", sa.SmallInteger(), nullable=False),
        sa.Column("chokes_hash", sa.String(length=16), nullable=True),
        sa.Column("gap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", J, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["replay_id", "round_number"], ["replay_rounds.replay_id", "replay_rounds.round_number"],
                                ondelete="CASCADE", name="fk_replay_round_gap_runs_round"),
        sa.CheckConstraint("status IN ('ok', 'failed')", name="ck_replay_round_gap_runs_status"),
    )
    op.create_table(
        "replay_gaps",
        sa.Column("replay_id", sa.Integer(), primary_key=True),
        sa.Column("round_number", sa.SmallInteger(), primary_key=True),
        sa.Column("seq", sa.SmallInteger(), primary_key=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("map", sa.String(length=64), nullable=False),
        sa.Column("victim_slot", sa.SmallInteger(), nullable=False),
        sa.Column("victim_side", sa.String(length=8), nullable=True),
        sa.Column("t_open", sa.REAL(), nullable=False),
        sa.Column("t_last_exposed", sa.REAL(), nullable=True),
        sa.Column("t_close", sa.REAL(), nullable=True),
        sa.Column("spot_cell", sa.Integer(), nullable=False),
        sa.Column("victim_cell", sa.Integer(), nullable=False),
        sa.Column("distance_m", sa.REAL(), nullable=False),
        sa.Column("angle_deg", sa.REAL(), nullable=False),
        sa.Column("qualified_s", sa.REAL(), nullable=True),
        sa.Column("flicker", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cause", sa.String(length=16), nullable=True),
        sa.Column("cause_detail", J, nullable=True),
        sa.Column("choke_seq", J, nullable=True),
        sa.Column("route", J, nullable=False),
        sa.Column("candidate_slots", J, nullable=False),
        sa.Column("candidate_distances", J, nullable=True),
        sa.Column("checked_at", J, nullable=True),
        sa.Column("stood_at", sa.REAL(), nullable=True),
        sa.Column("stood_by", sa.SmallInteger(), nullable=True),
        sa.Column("shot_at", sa.REAL(), nullable=True),
        sa.Column("shot_by", sa.SmallInteger(), nullable=True),
        sa.Column("killed_at", sa.REAL(), nullable=True),
        sa.Column("killed_by", sa.SmallInteger(), nullable=True),
        sa.Column("victim_won_at", sa.REAL(), nullable=True),
        sa.Column("context", J, nullable=True),
        sa.Column("linked_seq", sa.SmallInteger(), nullable=True),
        sa.ForeignKeyConstraint(["replay_id", "round_number"], ["replay_rounds.replay_id", "replay_rounds.round_number"],
                                ondelete="CASCADE", name="fk_replay_gaps_round"),
        sa.CheckConstraint("kind IN ('predicted', 'backshot')", name="ck_replay_gaps_kind"),
    )
    op.create_index("ix_replay_gaps_map_kind", "replay_gaps", ["map", "kind"])


def downgrade() -> None:
    op.drop_index("ix_replay_gaps_map_kind", table_name="replay_gaps")
    op.drop_table("replay_gaps")
    op.drop_table("replay_round_gap_runs")
```

Add `ix_replay_gaps_map_kind` to the `ReplayGap` model's `__table_args__` as `Index("ix_replay_gaps_map_kind",
"map", "kind")` (import `Index` from sqlalchemy).

- [ ] **Step 5: `webapp/app/gaps/rows.py`**

```python
"""Gaps as storage rows (timing-gaps spec, section 7)."""

from __future__ import annotations


def to_rows(gaps, rnd, geo) -> list[dict]:
    seq_of = {id(g): i for i, g in enumerate(gaps)}
    out = []
    for i, g in enumerate(gaps):
        side = rnd.group_side.get(g.team)
        out.append({
            "seq": i, "kind": g.kind, "map": geo.name, "victim_slot": g.victim, "victim_side": side,
            "t_open": g.t_open, "t_last_exposed": g.t_last_exposed, "t_close": g.t_close,
            "spot_cell": int(geo.node_cell[g.spot]) if geo.heights is not None else int(g.spot),
            "victim_cell": int(geo.node_cell[g.victim_node]) if geo.heights is not None else int(g.victim_node),
            "distance_m": g.distance_m, "angle_deg": g.angle_deg,
            "qualified_s": round(g.qualified_s, 3) if g.kind == "predicted" else None, "flicker": g.flicker,
            "cause": g.cause, "cause_detail": g.cause_detail,
            "choke_seq": None if g.choke_seq is None else list(g.choke_seq), "route": g.route,
            "candidate_slots": sorted(g.candidates), "candidate_distances": {str(k): v for k, v in g.candidates.items()},
            "checked_at": g.checked_at or None, "stood_at": g.stood_at, "stood_by": g.stood_by,
            "shot_at": g.shot_at, "shot_by": g.shot_by, "killed_at": g.killed_at, "killed_by": g.killed_by,
            "victim_won_at": g.victim_won_at, "context": g.context,
            "linked_seq": seq_of.get(id(g.linked)) if g.linked is not None else None,
        })
    return out
```

- [ ] **Step 6: `webapp/app/services/replay_gaps_store.py`**

```python
"""Stores one round's timing gaps (migration 0016; docs/superpowers/specs/2026-10-02-timing-gaps-design.md,
section 7): the run row and all gap rows of a round are replaced in one transaction. Used by
scripts/compute_control.py. The engine is never imported here."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.models.replay import ReplayGap, ReplayRoundGapRun

STORED = "stored"


def store_gaps(session_factory, replay_id: int, round_number: int, run: dict, rows: list[dict]) -> str:
    session = session_factory()
    try:
        session.query(ReplayGap).filter(ReplayGap.replay_id == replay_id,
                                        ReplayGap.round_number == round_number).delete()
        ok = run["status"] == "ok"
        session.merge(ReplayRoundGapRun(
            replay_id=replay_id, round_number=round_number, status=run["status"], fingerprint=run["fingerprint"],
            gaps_revision=run["gaps_revision"], chokes_hash=run.get("chokes_hash"),
            gap_count=len(rows) if ok else 0, notes=run.get("notes") or None, error=run.get("error"),
            computed_at=datetime.now(timezone.utc)))
        if ok:
            session.add_all(ReplayGap(replay_id=replay_id, round_number=round_number, **row) for row in rows)
        session.commit()
        return STORED
    except IntegrityError:
        session.rollback()
        return "skipped: the replay changed while computing (re-ingested?)"
    finally:
        session.rollback()
        session.close()
```

- [ ] **Step 7: Run the tests and the migration**

Run: `PY -m pytest tests/replays/test_gaps_store.py -q` (expect PASS), then against local Postgres:
`docker compose -p valomaths-private up -d` and `PY -m alembic upgrade head` then `PY -m alembic downgrade -1`
then `PY -m alembic upgrade head`. Expected: no errors.

- [ ] **Step 8: Commit**

```
git add app/models/replay.py alembic/versions/0016_replay_gaps.py app/gaps/rows.py app/services/replay_gaps_store.py tests/replays/test_gaps_store.py
git commit -F <msg file>   # "Timing gaps: storage (migration 0016)"
```

---

### Task 10: Computing gaps with control

Review fixes: R1, R5, R6, R17, R18, R19, R20. The interface is `plan_gaps(db, planned_control, every,
retry_failed=False)`.

**Files:**
- Create: `webapp/app/services/replay_gaps.py`
- Modify: `webapp/app/control/task.py` (`compute_task`, lines 94-123)
- Modify: `webapp/scripts/compute_control.py` (`run`, `main`, docstring)
- Test: `webapp/tests/replays/test_gaps_task.py`

**Interfaces:**
- Consumes: everything above; `replay_control.plan`, `PlannedRound`.
- Produces:
  - `replay_gaps.gap_fingerprint(control_fingerprint: str, map_name: str) -> str` (16 hex over the control
    fingerprint, `GAPS_REVISION` and `choke_assets.asset_hash(map_name)`)
  - `replay_gaps.plan_gaps(db, planned_control: list[PlannedRound]) -> list[PlannedRound]`: rounds whose
    control is fresh (not in `planned_control`) and whose gap run is missing or stale; `reason` = `"gaps"`.
  - `compute_task(task)`: with `task["gaps"]` (a dict `{"replay_id", "fingerprint"}`), also returns
    `result["gaps"] = {"run": {...}, "rows": [...]}`; with `task["gaps_only"]`, it uses the tick cache when
    present, else the engine, and returns no control `data`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_gaps_task.py`:

```python
"""Gaps in the control task (timing-gaps spec, section 7, "Writer")."""

import gzip
import json

from app.control.task import compute_task
from app.replays import format as fmt
from tests.replays.control_toys import blob


def _task(tmp_path, monkeypatch, **extra):
    from app.gaps import cache
    monkeypatch.setattr(cache, "cache_dir", lambda: tmp_path)
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=6.0)
    raw = gzip.compress(json.dumps(data).encode("utf-8"))
    return {"key": 0, "map": "Ascent", "blob": raw, "link": {"sides": {}, "db_deaths": []},
            "gaps": {"replay_id": 1, "round": 1, "fingerprint": "c" * 16}, **extra}


def test_a_detector_failure_leaves_control_ok(tmp_path, monkeypatch):
    from app.gaps import detect
    monkeypatch.setattr(detect.GapDetector, "finish", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    result = compute_task(_task(tmp_path, monkeypatch))
    assert result["status"] == "ok" and result["data"]
    assert result["gaps"]["run"]["status"] == "failed" and "boom" in result["gaps"]["run"]["error"]


def test_gaps_only_uses_the_cache_written_by_a_full_run(tmp_path, monkeypatch):
    first = compute_task(_task(tmp_path, monkeypatch))
    assert first["gaps"]["run"]["status"] == "ok"
    assert list(tmp_path.glob("*.ticks.pkl.gz"))
    import app.control.engine as ce
    monkeypatch.setattr(ce, "compute_round", lambda *a, **k: (_ for _ in ()).throw(AssertionError("engine ran")))
    again = compute_task(_task(tmp_path, monkeypatch, gaps_only=True))
    assert again["gaps"]["rows"] == first["gaps"]["rows"] and "data" not in again
```

Toy blobs use map `"Ascent"` here so `_load` finds real geometry; the round's toy positions sit on Ascent's
floor only if (150, 200) and (400, 200) are walkable there. Before writing the test, check with
`PY -c "from app.control import geometry as g; geo=g.load_geometry('Ascent'); print(geo.walk[25, 18], geo.walk[25, 50])"`
and pick two walkable points 30+ m apart if not.

- [ ] **Step 2: Run them to verify they fail**

Run: `PY -m pytest tests/replays/test_gaps_task.py -q`
Expected: FAIL (`KeyError: 'gaps'`).

- [ ] **Step 3: `webapp/app/services/replay_gaps.py`**

```python
"""Which rounds need timing gaps, and their fingerprint (docs/superpowers/specs/2026-10-02-timing-gaps-design.md,
section 7). A round's gaps are computed with its control; a round whose control is fresh but whose gap run is
missing or stale (new gap rules, an edited choke asset) is computed from the tick cache, or the engine without
rewriting control. Standard library and the DB only."""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import load_only

from app.models.replay import ReplayRoundGapRun
from app.replays import choke_assets

GAPS_REVISION = 1     # keep equal to app.gaps.detect.GAPS_REVISION (tests/replays/test_gaps_task.py pins it)


def gap_fingerprint(control_fingerprint: str, map_name: str) -> str:
    body = f"{control_fingerprint}|{GAPS_REVISION}|{choke_assets.asset_hash(map_name)}"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def plan_gaps(db, planned_control: list, every: list) -> list:
    """Of `every` (replay_control.plan(force=True) of the same scope: each round with its current control
    fingerprint and link), the rounds not in `planned_control` whose gap run is missing or stale."""
    from dataclasses import replace

    busy = {(p.replay_id, p.round_number) for p in planned_control}
    runs = {(r.replay_id, r.round_number): r for r in db.query(ReplayRoundGapRun).options(
        load_only(ReplayRoundGapRun.replay_id, ReplayRoundGapRun.round_number, ReplayRoundGapRun.fingerprint))}
    out = []
    for p in every:
        if not p.computable or (p.replay_id, p.round_number) in busy:
            continue
        run = runs.get((p.replay_id, p.round_number))
        if run is None or run.fingerprint != gap_fingerprint(p.fingerprint, p.map_name):
            out.append(replace(p, reason="gaps"))
    return out
```

`replay_gaps.py` reads chokes through `app/replays/choke_assets.py` (standard library), so plan 3's pattern
page can import it. Pin that in `tests/replays/test_control_isolation.py`: in
`test_the_web_apps_control_views_import_nothing_heavy`, add these two paths to the tuple it loops over:

```python
                 WEBAPP / "app" / "services" / "replay_gaps.py",
                 WEBAPP / "app" / "services" / "replay_gaps_store.py",
```

Add to `tests/replays/test_gaps_task.py`:

```python
def test_the_gaps_revision_is_one_number():
    from app.gaps import detect
    from app.services import replay_gaps
    assert detect.GAPS_REVISION == replay_gaps.GAPS_REVISION
```

- [ ] **Step 4: `compute_task` with gaps**

In `task.py`, replace the body's `try:` block (lines 106-113) with:

```python
    try:
        geo = _load(task["map"], task.get("heights"))
        blob = fmt.decode_blob(task["blob"])
        link = engine.ControlLink(sides={int(s): side for s, side in task["link"]["sides"].items()},
                                  db_deaths=tuple((int(s), float(t)) for s, t in task["link"]["db_deaths"]))
        gaps_job = task.get("gaps")
        if task.get("gaps_only"):
            result = {"status": "ok", "geometry": geometry_used(geo), "gaps": _gaps(geo, blob, link, gaps_job,
                                                                                   allow_cache=True)}
        else:
            observer = None
            if gaps_job:
                from app.gaps import cache
                observer = cache.Writer(cache.cache_path(gaps_job["replay_id"], gaps_job["round"],
                                                         gaps_job["fingerprint"]))
            rc = engine.compute_round(blob, geo, link, observer=observer)
            result = {"status": "ok", "data": encode_data(rc, blob), "summary": encode_summary(rc, blob),
                      "missing": dict(rc.missing_inputs), "geometry": geometry_used(geo)}
            if gaps_job:
                observer.close()
                result["gaps"] = _gaps(geo, blob, link, gaps_job, allow_cache=True)
```

and add above `compute_task`:

```python
def _gaps(geo, blob, link, job: dict, allow_cache: bool) -> dict:
    """One round's timing gaps, from the tick cache when it is there, else through the engine. Its own
    failure boundary: an error here is the gap run's, never control's."""
    from app.replays import choke_assets
    from app.services.replay_gaps import GAPS_REVISION, gap_fingerprint

    run = {"fingerprint": gap_fingerprint(job["fingerprint"], geo.name), "gaps_revision": GAPS_REVISION,
           "chokes_hash": choke_assets.asset_hash(geo.name), "notes": {}, "error": None}
    try:
        from app.control import engine
        from app.gaps import cache, detect, rows

        rnd = engine.RoundInputs(blob, geo, link)
        det = detect.GapDetector(geo, rnd)
        path = cache.cache_path(job["replay_id"], job["round"], job["fingerprint"])
        if allow_cache and path.exists():
            for rec, logs in cache.replay(path):
                det.step(rec, logs)
        else:
            writer = cache.Writer(path)

            def both(rec, unk):
                writer(rec, unk)
                det.step(rec, unk.log)

            engine.compute_round(blob, geo, link, observer=both, knowledge=False)
            writer.close()
        gaps = det.finish()
        run.update({"status": "ok", "notes": dict(det.notes) | dict(rnd.missing)})
        return {"run": run, "rows": rows.to_rows(gaps, rnd, geo)}
    except Exception as error:  # noqa: BLE001 - the gap run's failure, stored with its error
        run.update({"status": "failed", "error": f"{type(error).__name__}: {error}\n{traceback.format_exc()[-1500:]}"})
        return {"run": run, "rows": []}
```

The full run writes the cache through `observer` and `_gaps` then reads it back (`allow_cache=True`), so a
full run and a gaps-only run produce identical rows by construction.

- [ ] **Step 5: `compute_control.py`**

In `main`, after `planned = replay_control.plan(...)` (and the `--map` filter), add:

```python
        from app.services import replay_gaps
        every = replay_control.plan(session, match_uuid=args.match,
                                    rounds=set(args.rounds) if args.rounds else None, force=True)
        if args.map_name:
            every = [p for p in every if p.map_name == args.map_name]
        planned = planned + replay_gaps.plan_gaps(session, planned, every)
```

In `run`, build the task with gaps, and store them:

```python
                task = {"key": key, "map": p.map_name, "blob": blob, "link": p.link,
                        "gaps": {"replay_id": p.replay_id, "round": p.round_number, "fingerprint": p.fingerprint},
                        "gaps_only": p.reason == "gaps"}
```

and where a result is stored:

```python
                outcome = store_result(session_factory, p, result) if p.reason != "gaps" else "stored"
                if result.get("gaps"):
                    from app.services.replay_gaps_store import store_gaps
                    store_gaps(session_factory, p.replay_id, p.round_number, result["gaps"]["run"],
                               result["gaps"]["rows"])
```

In the per-round print, for `p.reason == "gaps"` print `gaps {len(rows)}` instead of data sizes (guard
`sizes.append` with `if "data" in result`). Add to the docstring's "Which rounds" bullet: "Also every round
whose control is fresh but whose timing gaps are missing or stale (reason `gaps`): those run from the local
tick cache (`webapp/.control_cache/gaps/`) when it is there."

- [ ] **Step 6: Run the tests**

Run: `PY -m pytest tests/replays/test_gaps_task.py tests/replays/test_control_task.py tests/replays/test_control_isolation.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```
git add app/services/replay_gaps.py app/control/task.py scripts/compute_control.py tests/replays/test_gaps_task.py tests/replays/test_control_isolation.py
git commit -F <msg file>   # "Timing gaps computed with control, and from the tick cache alone"
```

---

### Task 11: Preview on real rounds, then the full recompute

**Files:**
- Create: `webapp/scripts/preview_gaps.py`

- [ ] **Step 1: Write `webapp/scripts/preview_gaps.py`**

```python
"""Timing gaps for a preview folder of real rounds (written by scripts/preview_control_live.py: `<n>.json.gz`
and `context.json`), printed as a table. Nothing is written but under %TEMP%.

    .\\.venv\\Scripts\\python.exe scripts\\preview_control_live.py <uuid> 1 2
    .\\.venv\\Scripts\\python.exe scripts\\preview_gaps.py %TEMP%\\valo-replay\\<uuid>-rev5
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))
sys.path.insert(0, str(WEBAPP_ROOT / "scripts"))


def main(argv: list[str] | None = None) -> int:
    import preview_control_live as preview

    from app.control import engine, geometry
    from app.gaps import cache, detect
    from app.replays import format as fmt

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args(argv)
    ctx = json.loads((args.folder / "context.json").read_text(encoding="utf-8"))
    geo = geometry.visibility(geometry.load_geometry(ctx["match"]["map"]))
    for path in sorted(args.folder.glob("*.json.gz"), key=lambda p: int(p.name.split(".")[0])):
        n = int(path.name.split(".")[0])
        blob = fmt.decode_blob(path.read_bytes())
        link = preview.link_for(ctx, n)
        control_link = engine.ControlLink(sides={int(s): side for s, side in link["sides"].items()},
                                          db_deaths=tuple((int(s), float(t)) for s, t in link["db_deaths"]))
        rnd = engine.RoundInputs(blob, geo, control_link)
        det = detect.GapDetector(geo, rnd)
        writer = cache.Writer(args.folder / f"{n}.ticks.pkl.gz")
        started = time.time()

        def both(rec, unk):
            writer(rec, unk)
            det.step(rec, unk.log)

        engine.compute_round(blob, geo, control_link, observer=both, knowledge=False)
        writer.close()
        gaps = det.finish()
        size = (args.folder / f"{n}.ticks.pkl.gz").stat().st_size
        print(f"round {n}: {len(gaps)} rows in {time.time() - started:.0f}s; tick cache {size / 1e6:.1f} MB; "
              f"notes {dict(det.notes)}")
        for g in gaps:
            print(f"  {g.kind:9s} t {g.t_open:6.1f}-{(g.t_close or 0):6.1f} victim {g.victim} by {sorted(g.candidates)} "
                  f"seq {g.choke_seq} cause {g.cause} stood {g.stood_by} shot {g.shot_by} killed {g.killed_by} "
                  f"{'FLICKER' if g.flicker else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Preview two rounds**

Pick the Ascent replay the unknown plan used (`6f12db3e-b2db-4bca-96e4-a837c85ba5a6`, rounds 1-2):
`PY scripts/preview_control_live.py 6f12db3e-b2db-4bca-96e4-a837c85ba5a6 1 2` then
`PY scripts/preview_gaps.py %TEMP%\valo-replay\6f12db3e-b2db-4bca-96e4-a837c85ba5a6-rev5`.
Record: rows per round, compute seconds per round (compare with the ~45 s control figure), tick-cache MB per
round, and the notes.

- [ ] **Step 3: STOP for the owner.** Show the owner the two rounds' gap lists and the three measurements.
  Ask: do the gaps match what they see in the viewer at those times; is the tick cache size acceptable to keep
  for every round (if not, agree a retention rule, e.g. keep only the last N replays, and implement it in
  `cache.py` with a test before continuing). Do not continue until they approve.

- [ ] **Step 4: Commit the preview script**

```
git add scripts/preview_gaps.py
git commit -F <msg file>   # "Timing gaps: preview script for real rounds"
```

- [ ] **Step 5: Hand over the full recompute.** The owner runs it (a normal terminal, not Claude Code's `!`):
  first `PY scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/compute_control.py --dry-run`
  to see the count, then the same without `--read-only`/`--dry-run`. Every round is stale (revision 5), so
  control and gaps are recomputed together. Do not run it yourself.

---

## Self-review notes (for the executor)

- Spec section 2's tagger choke mode, section 8 (viewer) and section 9 (pattern page) are plans 2 and 3.
- The spec's "candidate's real distance" for a candidate without a position is stored as null and counted
  (`candidate without a position`).
- `RESULT_WINDOW_S` windows are anchored on each stood time and on the shot (Task 8).
