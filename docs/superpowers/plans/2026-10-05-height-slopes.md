# Height slopes: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The height build gives stairs and slopes a height (from players walking them, from the low end of each floor's samples, and by filling along a gradient), ignores a player for 3 s after a movement ability, and tells the engine which drops are slides and which are falls, so the unknown stops at a ledge too high to land from quietly.

**Architecture:** One new module, `app/control/height_motion.py`, turns a stored round's tracks into the second kind of ground evidence (walks) and the times to leave out (blackouts). `app/control/height_build.py` groups stands and walks into floors per cell, fills slopes a ground run crossed, and labels each one-way connection a slide or a fall. The asset (`app/control/heights.py`) gains a per-cell `kind` and a fifth `edges` column; `app/control/topology.py` gives the unknown's spread a second cost table in which high falls are closed. The bar and both checks are untouched.

**Tech Stack:** Python 3.13, numpy, scipy (the control engine's own venv), vanilla JS in the viewer page (Node for its tests), pytest.

**Spec:** `docs/superpowers/specs/2026-10-05-height-slopes-design.md`. Its companion, `2026-10-05-height-auto-rebuild-design.md`, has its own plan (`2026-10-05-height-auto-rebuild.md`) and depends on this one.

## What was measured before this plan was written, and what that is worth

The spec leaves two questions for "the first task". Both were answered on local round blobs
(`%TEMP%\valo-replay\heights-local`: 64 rounds of 3 matches, Sunset, Lotus and Summit), and the code in Tasks 1
to 5 was run in a scratch copy of these modules. **These are prototype measurements on three matches. They are
not validation of the implementation.** The decisions below were answered by the owner on 2026-10-07. Task 2
repeats the measurement on a frozen copy of the live rounds with a committed script; Task 6 judges the rules on
all five maps; the owner looks last.

| Question | Prototype answer |
| --- | --- |
| The stored sample rate (open question 2) | **125 Hz** in all 64 rounds. |
| Vertical acceleration over a 0.3 s window while moving | Two groups: 82% of windows under 1 m/s2 (the ground), and a second group at 15 to 30 m/s2 (free fall). Gravity read off the flights the detector below finds is 19.9 m/s2 at the 90th percentile; `GRAVITY_MPS2` starts at **20**. |
| Whether movement-ability casts are in `util` (open question 1) | **They are not.** 40 rounds with a Jett and 22 with a Waylay record no cast for the updraft or either dash. No Raze in the sample. |
| What a dash and an updraft look like in the track | Along the ground over 0.1 s: everyone else's 99.9th percentile is 8 m/s, Jett's and Waylay's 99th is 17 to 20 m/s. Upward: everyone else's 99.99th is 6 m/s, theirs is 20 m/s. |
| How much of a round is in the air | 7.4% of moving samples on Sunset are inside a flight (1,783 flights in 22 rounds, median 0.38 s). |
| The owner's staircase (Sunset cells x 26-28, y 76-81) | 80.3% of the moving samples inside it are a walk's; their slope is 0.47 at the median and 0.59 at the 99th percentile. |
| One Sunset match built both ways (one-match preview rule) | Supported 46.9% to 48.1%; 125 cells from walks alone, 21 filled along a gradient; of 2,908 cells supported before that still have a height, 98.5% moved by 0.2 m or less (1st percentile -0.3 m); 107 cells lost their height and 103 gained one; kill lines blocked 0 of 121 and 0 of 123. |

An earlier draft of this plan reported 49.6% and 217 walk cells for that match. A review showed its walk rule
accepted whole falls as walks (below, D2); those numbers are withdrawn. With one match, `neighbours disagree`
did not go down (207 to 209): whether the rules clear Sunset's blocker is not known until Task 6.

## Decisions this plan takes that the spec does not (for the owner to confirm at review)

The spec says a change to it "is reported before it is adopted". These are the changes. **The owner answered
them on 2026-10-07:** D1 changed (record the casts and use them; the row below is the changed decision), D2 to
D6 approved as proposed ("sure"), D7 approved ("agreed"). What is still the owner's to look at is the result:
the measurement's out-of-range rows (Task 2 Step 8) and the five maps by eye (Task 6 Step 3), both after the
build.

| # | Decision | Why |
| --- | --- | --- |
| D1 | **The blackout is started by the recorded cast; the condenser is changed to record it (Task 1b).** A round condensed with movement casts (its blob says `"movement_casts": 1`) starts a blackout only from a cast in `AIRBORNE_ABILITIES`. A round condensed before that has no casts to read, so there, and only there, the blackout is started by speed: any 0.1 s faster than `BURST_MPS` (12 m/s) along the ground or `BURST_UP_MPS` (8 m/s) upward. | **Owner, 2026-10-07:** "auto reparsing is already being implemented so doing that isn't a huge deal, let's record and use the updraft and other movement abilities' timings." Spec 1b assumed the casts were already in `util`; they are not. The speed rule is kept for old rounds only because every round stored today is one: without it the rules could not be judged until every match is re-parsed. **That fallback is this plan's addition, not something the owner asked for.** |
| D2 | **Flights are found first, by what only a flight does; the spec's two limits are applied to what is left.** A flight is seeded where z changes faster than `SLOPE_MAX` allows for the ground covered, or where z's rate of change drops by 60% of gravity across two 0.1 s windows in a row; from the seed its arc (`GRAVITY_MPS2`) is followed both ways while the track stays within 0.1 m of it; a flight found only by the second seed must end in a landing. A sample is then a walk's when it is in no flight and some 0.3 s window holding it has no flight sample in it and passes the spec's slope and acceleration limits. | The spec's rule is "two limits over the run". One parabola fitted over a window cannot tell a fall from the ground: a takeoff or a landing bends z the other way and the two cancel. A 1.1 m fall between two ramps passed both limits in every window (review finding 1, reproduced). "Every window must pass" fixes that but cuts 0.3 s off both ends of every staircase, because a slope's start bends z too. |
| D3 | Open question 3: a stand gives each of its cells its **median** (as today); a walk gives each cell the **lowest z it had there**, one value per pass. The floor's height is the `LOW_PCT` percentile of those values, taken with numpy's `method="higher"`. | One value per pass, so a player who waits 30 s on a spot doesn't outweigh thirty who ran through. `method="higher"` is what makes "one bad sample can't sink a cell" true for small cells. |
| D4 | **Bands are built from supported levels, not from gaps between samples.** A level is a group of samples within `FLOOR_TOL_M` that passes the floor rule (`FLOOR_MIN_STANDS` from `FLOOR_MIN_ROUNDS` rounds in `FLOOR_MIN_MATCHES` matches, stands and walks alike). Levels less than `FLOOR_SEP_M` apart are one band. A sample no level holds joins a band only within `FLOOR_TOL_M` of it. | The spec says bands are "grouped as stands are grouped today". Cutting bands at 2 m gaps between samples let one stray sample half way between two floors merge them and refuse the cell (review finding 2, reproduced). A supported level between two floors still merges them: that is the spec's "what stays unresolved, on purpose". |
| D5 | A ground run that crosses a gradient-filled cell is also the evidence for that cell's two connections, in the direction it was seen (once is enough). **The run must be on the two anchor floors**: within one cell's steepest rise plus `FLOOR_TOL_M` of each, going the way the floors go, and between its two ends in the middle. A run that lost cells to a platform is cut there. | Spec 3: "the direction they were seen going decides how the filled cell connects". Without heights a walk along a bridge over two ground cells would authorise a slope between them (review finding 3). |
| D6 | New constants the spec doesn't name: `WALK_MIN_MPS` 0.5, `GRAVITY_MPS2` 20, `AIR_RATE_S` 0.1, `AIR_RATE_SLACK_MPS` 0.6, `AIR_CORE` 0.6, `AIR_FIT_M` 0.1, `AIR_LANDING_MPS` 1.0, `BURST_S` 0.1, `BURST_MPS` 12, `BURST_UP_MPS` 8, `CROSS_REACH` 2. | Gap fillers, as `STAND_APEX_S` and `PLATFORM_R_M` were. Each is a start value; Task 2 measures what the flight ones rest on. |
| D7 | Only the unknown's **spread** is closed at a high fall. The area the engine draws around a located enemy (`Unknown._area`) still grows over one. | Spec 5: "everything else that uses connections is unchanged". |

What D2 leaves open, on purpose: a hop shorter than about a quarter of a second with no fast part (a few
decimetres up and down) is not found as a flight. Its samples are above the ground, so they never lower a floor,
and it starts and ends on the same level.

## Correction from the 2026-10-07 review

Task 2's steep-descent diagnostic now selects low-acceleration moving windows independently of `airborne`.
It reports candidates for inspecting slides, not confirmed ground contact, and includes a regression track
the current air rule rejects. The build's thresholds and classification rules are unchanged.

**Checked 2026-10-07:** 20 focused motion/measurement tests passed in scratch, including the new steep-descent
regression; they form part of the companion plan's 56-test scratch run. These use in-memory overlays of the
planned modules on unchanged worktree code. Frozen real-data measurements and the full post-implementation
suites remain the task gates below.

## Global Constraints

- Work in the worktree `.claude/worktrees/height-viewer` (branch `worktree-height-viewer`), or a branch cut from it. Run tests from its `webapp/` with the main checkout's interpreter: `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv313\Scripts\python.exe -m pytest ... -p no:cacheprovider` (written `PY -m pytest` below).
- Baseline before Task 1: run `PY -m pytest tests/replays -k "not pg" -q` on the unchanged base and write down every failure with its message. A test is exempt from a task's gate only if it failed in that baseline run with the same message; on 2026-10-05 the one such test was `test_replay_worker_control.py::test_control_off_answers_404_and_parsing_is_untouched` (`ConnectionResetError` WinError 10054, a socket race on Windows). If it passes in your baseline, it is not exempt. Nothing is exempt by its exception type alone.
- Start values, verbatim from the spec: `WALK_S` 0.3 s, `SLOPE_MAX` 1.0, `LOW_PCT` 10, `SILENT_DROP_M` 1.0 m, `ABILITY_BLACKOUT_S` 3 s. `WALK_ACC_MAX` 4.0 m/s2 (a prototype measurement, above). Any change to one is reported to the owner with numbers and waits for their answer.
- The bar does not move: `HEIGHT_SUPPORTED_MIN` 0.60, `UNRESOLVED_MAX` 12, `KILL_LINE_BAR` 0.02, and the must-block set. Stands are kept exactly as they are; only stands can make an upper floor.
- `HEIGHT_VERSION` goes from 1 to 2. No height asset is committed (`test_no_real_maps_heights_are_committed`), so nothing is migrated.
- **Start from `main` as it is.** Checked 2026-10-07: `origin/main` (`0caa172`, PR #117) is 8 commits ahead of this worktree's branch and has already taken `CONTROL_REVISION` 6 (the live-claim rule, with its own pin and a re-recorded fingerprint snapshot); the worktree still says 5. Task 1, Step 1 merges `origin/main` in before anything else, and the baseline is recorded after that merge.
- `CONTROL_REVISION` therefore goes from 6 to **7**. If `main` has moved again by the time this is built, take the next free number everywhere this plan says 7, and say so in the task's report. It is unreleased, so `tests/replays/test_control_format.py`'s digest for it is re-pinned in place as each task changes a constant; revisions 1 to 6 keep their pins untouched.
- A flat map's computed control must not change: `tests/replays/test_control_reference.py` passes untouched at every commit. That test compares digests of each round's decompressed bytes with the header's and the summary's `revision` set to 0 (`scripts/control_reference.py`), so the bump to revision 7 is the one difference it allows; the stored bytes themselves differ in that number and nothing else.
- Both sets of rules are judged on **one frozen copy of the rounds** (Task 2), never on two reads of the live database: a build records what it read, and the comparison refuses builds that read different things.
- `app/control/heights.py` imports nothing from `app.control`. The web app imports neither `app.control` nor numpy through anything here (`tests/replays/test_control_isolation.py`).
- Reading the live database is always `scripts\with_friends_db.py --expect-database valowithfriendsdb --read-only ...`. If the session isn't allowed to read production, give the owner the exact command and wait for its output. Never write to it in this plan.
- No shell heredocs. Comments and docstrings match the surrounding code: plain sentences, the reason not the narration, docs referenced by path.

## Review Focus

1. **A slide steeper than `SLOPE_MAX`.** The spec says a ramp that can only be slid down is still a walk down it; a ramp that can't be walked up is probably steeper than 1.0, and the flight rule would then read it as a fall and close it to the unknown. Expected: the Lotus and Summit slides are slides. Task 2 prints steady descent candidates steeper than `SLOPE_MAX` by cell, without filtering through `airborne`, so the owner can inspect them; if they show up there, stop and report before Task 3. Pinned for the in-limit case in Task 3 (`test_a_drop_walked_down_is_a_slide_and_one_fallen_down_is_a_fall`).
2. **A fall whose takeoff and landing hide it.** Off a flat ledge, between two ramps, near `SILENT_DROP_M`, at a run and at a walk. Expected: no sample of the flight is ground, the cells flown over get no height part way down, the ledge is never filled as a slope and never becomes a slide. Pinned in Task 1 (`test_a_fall_off_a_ledge_is_no_walk_whatever_its_height`, `test_a_fall_between_two_ramps_is_no_walk`, `test_a_jump_is_no_walk`) and through the whole build in Task 3 (`test_a_ledge_run_off_is_never_a_slope_or_a_slide`, `test_a_fall_between_two_ramps_gives_no_floor_under_it_and_no_slide`).
3. **Where a slope begins or ends.** Expected: nobody is "in the air" at the top or bottom of a staircase or over a crest, and the walk runs straight through. Pinned in Task 1 (`test_where_a_slope_begins_or_ends_nobody_is_in_the_air`).
4. **A stray sample between two floors, or under one.** Expected: it bridges nothing and sinks nothing; a supported level between two floors still refuses the cell. Pinned in Task 3 (`test_a_stray_sample_between_two_floors_bridges_nothing`, `test_band_rules`).
5. **A run that isn't on the floors it seems to cross.** A bridge over two ground cells, a tunnel under them, a run past a Sage wall. Expected: no fill, no connection. Pinned in Task 3 (`test_a_crossing_counts_only_on_the_floors_it_fills_between`, `test_a_run_past_a_platform_is_cut_there`).

---

## File structure

| File | Change | Task |
| --- | --- | --- |
| `webapp/app/control/heights.py` | the new constants; later `HEIGHT_VERSION` 2, the asset's `kind` and the fifth `edges` column | 1, 3 |
| `webapp/app/control/height_motion.py` (new) | tracks, blackouts, walks | 1 |
| `webapp/app/control/height_build.py` | `stands(skip)`; then bands, gradient fill, slides and falls, the report | 1, 3 |
| `webapp/app/replays/control_format.py`, `webapp/tests/replays/test_control_format.py` | `CONTROL_REVISION` 7 and its digest | 1, 3 |
| `webapp/tests/replays/control_toys.py` | 125 Hz rounds, `stair_run`; later `toy_heights` writes edge kinds | 1, 3 |
| `webapp/tests/replays/test_height_motion.py` (new) | walks and blackouts | 1 |
| `webapp/app/replays/extras.py`, `webapp/app/replays/format.py` | the condenser records movement-ability casts; `CONDENSE_REVISION` up one | 1b |
| `webapp/scripts/measure_height_motion.py` (new) | the measurement | 2 |
| `webapp/scripts/freeze_height_rounds.py` (new), `webapp/scripts/build_control_heights.py` | one frozen copy of the rounds; a preview records what it read | 2 |
| `webapp/scripts/compare_height_builds.py` (new), `webapp/tests/replays/test_compare_height_builds.py` (new) | old rules against new, refused unless both read the same | 2 |
| `webapp/tests/replays/test_control_heights.py` | the build's tests | 3 |
| `webapp/app/control/topology.py`, `webapp/app/control/engine.py` | the unknown's spread is closed at a high fall | 3 (reads five columns), 4 |
| `webapp/tests/replays/test_control_floors.py` | the unknown at a ledge | 4 |
| `webapp/scripts/height_viewer.py`, `height_viewer_core.js`, `height_viewer.template.html`, `webapp/tests/replays/test_height_viewer.py` | kind in the hover, the new-kinds layer | 5 |
| `docs/superpowers/specs/2026-10-05-height-slopes-design.md` | "Measured" and "Results" sections, status | 2, 6 |

Task 3's parts (floors, fill, connections, the asset) are one task because `build` calls all of them and its
report counts all of them: there is no state in between that is worth a reviewer's gate.

The code in Tasks 1 to 5, the three scripts and every test below were run while this plan was written, against a
scratch copy of these modules (the tests of Tasks 1 to 4 and of the scripts all pass there; the whole replay
suite was last run before the review's fixes and then failed only in the two files this plan rewrites, on the
`CONTROL_REVISION` pin and on the baseline failure). That is a prototype run, not the implementation's
validation: each task still runs its own gate. On 2026-10-07 three of the review's reproductions were run again
against the code blocks of this plan as written, on the worktree's unchanged modules: the 1.1 m fall between two
ramps (125 Hz, 0.14 m/px, 5 m/s, z in decimetres) gives no ground sample inside the fall and two ground runs;
six stands at 0 dm and six at 30 dm stay two floors with one stray stand or walk at 15 dm, and a supported level
at 15 dm is still `floors too close`. The gradient and platform cases (finding 3) and the comparison's refusals
(finding 4) were not re-run then. If a step's expected result doesn't appear, suspect a
transcription slip before the design.

---

### Task 1: Walks and blackouts

**Files:**
- Modify: `webapp/app/control/heights.py` (constants, after `PLATFORMS`)
- Create: `webapp/app/control/height_motion.py`
- Modify: `webapp/app/control/height_build.py` (`_tracks`, `stands`)
- Modify: `webapp/app/replays/control_format.py` (`CONTROL_REVISION`), `webapp/tests/replays/test_control_format.py` (`PINNED`)
- Modify: `webapp/tests/replays/control_toys.py` (after `height_rounds`)
- Test: `webapp/tests/replays/test_height_motion.py` (new)

**Interfaces:**
- Consumes: `height_build.Stand` (`round, slot, t0, t1, z, cells, x, y`), `Geometry.m_per_px`, a round blob's `hz`, `tracks`, `util`.
- Produces:
  - `heights.py`: `WALK_S, SLOPE_MAX, WALK_ACC_MAX, WALK_MIN_MPS, GRAVITY_MPS2, AIR_RATE_S, AIR_RATE_SLACK_MPS, AIR_CORE, AIR_FIT_M, AIR_LANDING_MPS, LOW_PCT, ABILITY_BLACKOUT_S, AIRBORNE_ABILITIES, BURST_S, BURST_MPS, BURST_UP_MPS, CROSS_REACH, SILENT_DROP_M`; `KIND_NONE, KIND_STANDS, KIND_WALKS, KIND_FILLED, KIND_GRADIENT = 0..4`; `EDGE_STEP, EDGE_SLIDE, EDGE_FALL = 0, 1, 2`.
  - `height_motion.Walk(round: int, slot: int, t0: float, t1: float, cells: tuple, low: tuple)`.
  - `height_motion.tracks(blob, skip: dict | None = None)` yields `(slot, t, x px, y px, z dm | None)`.
  - `height_motion.blackouts(blob, geo) -> dict[int, list[tuple[float, float]]]`.
  - `height_motion.airborne(x, y, z, hz, m_per_px) -> np.ndarray` (bool per sample: inside a flight); `height_motion.on_ground(x, y, z, hz, m_per_px) -> np.ndarray` (bool per sample: a walk's); `height_motion.cells_of(x, y) -> np.ndarray`, `height_motion._acc_filter(n, hz)`, `height_motion._rate(v, n, hz)`, `height_motion._runs_of(mask)`.
  - `height_motion.walks(blob, geo, round_index=0, found=(), skip=None) -> list[Walk]`.
  - `height_build.stands(blob, geo, round_index=0, skip=None)`; `height_build._tracks` is `height_motion.tracks`.
  - `control_toys`: `FAST_HZ = 125`, `fast_blob(players, *, t_end=10.0, util=(), n=1)`, `fast_rounds(make, matches=2, rounds=3, t_end=10.0)`, `stair_run(x0=160, x1=224, z0=0.0, z1=4.0, t0=1.0, t1=3.0, side="A", y=204)`, and `height_rounds(..., t_end=10.0)`.

- [ ] **Step 1: Bring the branch up to `main`, then record the baseline**

```bash
git fetch origin
git merge origin/main
```

Expected: a clean merge (the branch's own commits are specs and the height viewer; `main`'s are the live-claim rule). If it conflicts, stop and report the files: do not resolve a conflict in `app/control/` by guessing. Then check `webapp/app/replays/control_format.py` says `CONTROL_REVISION = 6` and write down the number this plan takes (7, or the next free one).

Run: `PY -m pytest tests/replays -k "not pg" -q -p no:cacheprovider`
Expected: on 2026-10-05, before that merge, one failure (`test_replay_worker_control.py::test_control_off_answers_404_and_parsing_is_untouched`, `ConnectionResetError`). The run that counts is yours, after the merge: write down every failure you get with its message and the pass count. That list, not this sentence, is what later gates exempt.

- [ ] **Step 2: Add the toy helpers**

In `webapp/tests/replays/control_toys.py`, give `height_rounds` a `t_end` and add the 125 Hz helpers right after it. Replace the whole `height_rounds` function with:

```python
def height_rounds(make, matches: int = 2, rounds: int = 3, t_end: float = 10.0) -> list:
    """[(match id, round number, blob)] for the height build: `make(match index, round number)` gives
    each round's players ({slot: (side, points)}) or a whole blob."""
    out = []
    for m in range(matches):
        for n in range(1, rounds + 1):
            made = make(m, n)
            out.append((f"match-{m}", n, made if "tracks" in made else height_blob(made, t_end=t_end, n=n)))
    return out


FAST_HZ = 125        # the rate real rounds are stored at: the walk rules are tuned on it


def fast_blob(players: dict, *, t_end: float = 10.0, util=(), n: int = 1) -> dict:
    """`height_blob` at FAST_HZ: {slot: (side, [(t, x, y, yaw, z m), ...])}."""
    out = blob({s: (side, [p[:4] for p in pts]) for s, (side, pts) in players.items()}, t_end=t_end, util=util)
    out["round"], out["hz"] = n, FAST_HZ
    for s, (_, pts) in players.items():
        out["tracks"][str(s)] = z_track(pts, 0.0, t_end, hz=FAST_HZ)
    return out


def stair_run(x0: int = 160, x1: int = 224, z0: float = 0.0, z1: float = 4.0, t0: float = 1.0, t1: float = 3.0,
              side: str = "A", y: int = 204):
    """Runs east from x0 to x1 px (9 m in 2 s by default) climbing z0 to z1, between two level runs."""
    return side, [(0.0, x0 - 32, y, 0, z0), (t0, x0, y, 0, z0), (t1, x1, y, 0, z1), (t1 + 1.0, x1 + 32, y, 0, z1),
                  (10.0, x1 + 32, y, 0, z1)]


def fast_rounds(make, matches: int = 2, rounds: int = 3, t_end: float = 10.0) -> list:
    """`height_rounds` at FAST_HZ: `make(match index, round number)` gives each round's players."""
    return [(f"match-{m}", n, fast_blob(make(m, n), t_end=t_end, n=n))
            for m in range(matches) for n in range(1, rounds + 1)]
```

Positions pass through `uv()` (rounded to 1/10000 of the map), so a player placed exactly on a cell boundary
(x a multiple of 8) can land in the cell to its west. Put standing players mid-cell (x = 8k + 4).

- [ ] **Step 3: Write the failing tests**

Create `webapp/tests/replays/test_height_motion.py`:

```python
"""How players move, for the height build (app/control/height_motion.py;
docs/superpowers/specs/2026-10-05-height-slopes-design.md, parts 1 and 1b): walks and blackouts, on toy rounds
stored at the rate of real ones (125 Hz). Positions are minimap pixels; cells are 8 px (1.12 m)."""

import pytest

from app.control import height_build as hb
from app.control import height_motion as hm
from tests.replays.control_toys import FAST_HZ, TOY_Z0, fast_blob, open_hall, stair_run, z_dm

GEO = open_hall()
GRID = 128
Y = 204
ROW = Y // 8


def cell(c: int, r: int = ROW) -> int:
    return r * GRID + c


def test_a_run_up_stairs_is_a_walk_with_each_cells_low_end():
    b = fast_blob({0: stair_run()})
    found = hb.stands(b, GEO)
    [climb] = [w for w in hm.walks(b, GEO, found=found) if cell(22) in w.cells]
    lows = dict(zip(climb.cells, climb.low))
    for c in range(21, 27):        # 4 m over 64 px: half a metre a cell, lowest at each cell's west edge
        assert abs((lows[cell(c)] - TOY_Z0) / 10 - (c * 8 - 160) / 64 * 4.0) <= 0.11, c
    assert not any(cell(c) in s.cells for s in found for c in range(22, 26)), "nobody stood on the stairs"


def test_a_running_jump_and_a_fall_are_not_walks():
    n = int(0.35 * FAST_HZ)
    arc = [(1.0 + i / FAST_HZ, 192 + 32 * i / FAST_HZ, Y, 0, 0.6 * 4 * (i / n) * (1 - i / n)) for i in range(1, n + 1)]
    hop = ("A", [(0.0, 160, Y, 0, 0.0), (1.0, 192, Y, 0, 0.0), *arc, (3.0, 260, Y, 0, 0.0), (10.0, 300, Y, 0, 0.0)])
    b = fast_blob({0: hop})
    assert not [w for w in hm.walks(b, GEO, found=hb.stands(b, GEO)) if w.t0 < 1.25 and w.t1 > 1.1], \
        "past its first and last tenth of a second, the jump is nobody's ground"
    n = int(0.63 * FAST_HZ)        # off a 4 m ledge at a run: free fall at 20 m/s2
    drop = [(1.0 + i / FAST_HZ, 200 + 40 * i / FAST_HZ, Y, 0, 4.0 - 10.0 * (i / FAST_HZ) ** 2) for i in range(1, n + 1)]
    fall = ("A", [(0.0, 160, Y, 0, 4.0), (1.0, 200, Y, 0, 4.0), *drop, (1.64, 225.6, Y, 0, 0.0), (4.0, 300, Y, 0, 0.0),
                  (10.0, 300, Y, 0, 0.0)])
    b = fast_blob({0: fall})
    found = hm.walks(b, GEO, found=hb.stands(b, GEO))
    assert all(w.t1 <= 1.1 or w.t0 >= 1.5 for w in found), "no walk spans the fall"
    before = [z for w in found if w.t1 <= 1.1 for z in w.low]
    assert min(before, default=z_dm(4.0)) >= z_dm(3.9), \
        "what a walk keeps of the fall's start is within a decimetre of the ledge"
    below = GEO.cell_of_px(212, Y)
    assert all(dict(zip(w.cells, w.low)).get(below, 0) <= z_dm(0.2) for w in found), "the cell fallen past: ground only"


def test_a_walk_needs_z_and_leaves_a_stands_samples_to_the_stand():
    level = ("A", [(0.0, 160, Y, 0, 1.0), (10.0, 300, Y, 0, 1.0)])
    b = fast_blob({0: level})
    found = hb.stands(b, GEO)
    assert len(found) == 1 and hm.walks(b, GEO, found=found) == [], "a level walk is already a stand"
    assert len(hm.walks(b, GEO)) == 1, "and a walk when no stand claims it"
    no_z = fast_blob({0: stair_run()})
    for seg in no_z["tracks"]["0"]:
        del seg["z"]
    assert hm.walks(no_z, GEO) == []


def test_a_dash_an_updraft_and_a_listed_cast_start_a_blackout():
    dash = ("A", [(0.0, 160, Y, 0, 0.0), (2.0, 160, Y, 0, 0.0), (2.3, 216, Y, 0, 0.0), (10.0, 216, Y, 0, 0.0)])
    up = ("A", [(0.0, 300, Y, 0, 0.0), (4.0, 300, Y, 0, 0.0), (4.4, 300, Y, 0, 5.0), (5.2, 300, Y, 0, 0.0),
                (10.0, 300, Y, 0, 0.0)])
    runner = ("B", [(0.0, 160, 252, 0, 0.0), (10.0, 400, 252, 0, 0.0)])          # 3.4 m/s: no burst
    cast = {"k": "ability", "t": 6.0, "t1": 6.5, "by": 2, "kind": "GameObject", "code": "Clay", "name": "Q_Explosion",
            "u": 0, "v": 0}
    b = fast_blob({0: dash, 1: up, 2: runner}, util=[cast])
    out = hm.blackouts(b, GEO)
    [(d0, d1)], [(u0, u1)] = out[0], out[1]
    assert 1.9 <= d0 <= 2.01 and 5.2 <= d1 <= 5.45 and 3.9 <= u0 <= 4.01 and 7.3 <= u1 <= 7.55
    assert out[2] == [(6.0, 9.0)], "a listed cast, from its time"
    kept = hb.stands(b, GEO, skip=out)
    assert not any(s.slot == 0 and s.t0 < d1 and s.t1 > d0 for s in kept), "nothing of the dasher inside it"
    assert any(s.slot == 0 and s.t1 <= d0 for s in kept) and any(s.slot == 0 and s.t0 >= d1 for s in kept), \
        "what they did before it and what they do after it count as normal"


def test_a_box_someone_dashed_onto_counts_from_three_seconds_on():
    # Lands on a 1.5 m box at 2.3 s and stays: the stand there starts when the blackout ends.
    onto = ("A", [(0.0, 160, Y, 0, 0.0), (2.0, 160, Y, 0, 0.0), (2.3, 216, Y, 0, 1.5), (10.0, 216, Y, 0, 1.5)])
    b = fast_blob({0: onto})
    skip = hm.blackouts(b, GEO)
    [(_, t1)] = skip[0]
    [after] = [s for s in hb.stands(b, GEO, skip=skip) if s.z == z_dm(1.5)]
    assert after.t0 > t1 - 1e-6 and after.t1 == 10.0


def test_tracks_cut_a_segment_at_a_blackout_and_overlapping_blackouts_merge():
    b = fast_blob({0: ("A", [(0.0, 160, Y, 0, 0.0), (10.0, 300, Y, 0, 0.0)])})
    whole = list(hm.tracks(b))
    pieces = list(hm.tracks(b, {0: [(2.0, 4.0)]}))
    assert len(whole) == 1 and len(pieces) == 2
    assert pieces[0][1][-1] < 2.0 and pieces[1][1][0] > 4.0
    assert len(pieces[0][1]) + len(pieces[1][1]) == len(whole[0][1]) - (2 * FAST_HZ + 1)
    assert hm._merge([(5.0, 8.0), (1.0, 4.0), (3.0, 6.0)]) == [(1.0, 8.0)]
    assert list(hm.tracks(b, {1: [(0.0, 10.0)]}))[0][1].shape == whole[0][1].shape, "another player's blackout"


# ---------------------------------------------------------------- flights (one parabola over a window can't tell)


def flown(z_of, v: float = 5.0, seconds: float = 3.0):
    """A player moving east at `v` m/s whose height is z_of(t), a point per stored sample."""
    px_per_s = v / GEO.m_per_px
    return "A", [(i / FAST_HZ, 100 + px_per_s * i / FAST_HZ, Y, 0, z_of(i / FAST_HZ))
                 for i in range(int(seconds * FAST_HZ) + 1)]


def spans(z_of, v: float = 5.0):
    """(the walks' (t0, t1), the times of the samples in the air, the times of the ones on the ground) for that
    player. Level running is a stand, not a walk, so "the ground" is `on_ground`'s own answer."""
    b = fast_blob({0: flown(z_of, v)}, t_end=3.0)
    [(_, t, x, y, z)] = list(hm.tracks(b))
    air = hm.airborne(x, y, z.astype(float), FAST_HZ, GEO.m_per_px)
    ground = hm.on_ground(x, y, z.astype(float), FAST_HZ, GEO.m_per_px)
    return [(w.t0, w.t1) for w in hm.walks(b, GEO, found=hb.stands(b, GEO))], t[air], t[ground]


def inside(times, t0, t1):
    return bool(((times > t0) & (times < t1)).any())


def drop(height: float, before=lambda t: 0.0, after=lambda t: 0.0):
    """z(t): `before` (ending at `height` at t = 1), a fall of `height` from rest, then `after` from the landing."""
    air = (height / 10) ** 0.5
    return (lambda t: height + before(t - 1) if t < 1 else height - 10 * (t - 1) ** 2 if t < 1 + air
            else after(t - 1 - air)), air


@pytest.mark.parametrize("height", [0.5, 0.9, 1.1, 2.0])
def test_a_fall_off_a_ledge_is_no_walk_whatever_its_height(height):
    z_of, air = drop(height)
    walks, in_air, ground = spans(z_of)
    assert not any(t0 < 1 + air - 0.03 and t1 > 1.03 for t0, t1 in walks), "no walk holds the inside of the fall"
    assert not inside(ground, 1.03, 1 + air - 0.03), "nor is any sample of it ground"
    assert in_air.min() <= 1.02 and in_air.max() >= 1 + air - 0.02, "the whole flight is found"
    assert inside(ground, 0.3, 0.85) and inside(ground, 1 + air + 0.05, 2.7), "the ground before and after it stays"


def test_a_fall_between_two_ramps_is_no_walk():
    # Down a ramp at 2 m/s, off its end, 1.1 m through the air, and up another: the takeoff and the landing bend
    # z the other way from the fall, and one parabola over a window sees almost nothing.
    z_of, air = drop(1.1, before=lambda t: -2 * t, after=lambda t: 2 * t)
    walks, in_air, ground = spans(z_of)
    assert not any(t0 < 1 + air - 0.03 and t1 > 1.03 for t0, t1 in walks)
    assert not inside(ground, 1.03, 1 + air - 0.03)
    assert len(walks) == 2 and walks[0][1] <= 1.0 and walks[1][0] >= 1 + air - 0.01, "a walk down, and a walk up"


@pytest.mark.parametrize("v, name", [(5.0, "running"), (2.9, "walking"), (0.0, "standing")])
def test_a_jump_is_no_walk(v, name):
    walks, in_air, ground = spans(lambda t: 4.6 * (t - 1) - 10 * (t - 1) ** 2 if 1 <= t < 1.46 else 0.0, v)
    assert not any(t0 < 1.43 and t1 > 1.03 for t0, t1 in walks) and not inside(ground, 1.03, 1.43), name
    assert in_air.min() <= 1.02 and in_air.max() >= 1.44, name
    down, _, ground = spans(lambda t: 0.8 if t < 1 else 0.8 + 4.6 * (t - 1) - 10 * (t - 1) ** 2 if t < 1.594 else 0.0, v)
    assert not any(t0 < 1.56 and t1 > 1.03 for t0, t1 in down) and not inside(ground, 1.03, 1.56), \
        "nor a jump down off a 0.8 m ledge"


@pytest.mark.parametrize("name, z_of", [
    ("stairs up", lambda t: 0.0 if t < 1 else 2.5 * (t - 1) if t < 2 else 2.5),
    ("steep stairs up", lambda t: 0.0 if t < 1 else 4.5 * (t - 1) if t < 2 else 4.5),
    ("steep stairs down", lambda t: 4.5 if t < 1 else 4.5 - 4.5 * (t - 1) if t < 2 else 0.0),
    ("over a crest", lambda t: 2.5 * t if t < 1 else 2.5 - 2.5 * (t - 1)),
])
def test_where_a_slope_begins_or_ends_nobody_is_in_the_air(name, z_of):
    walks, in_air, ground = spans(z_of)
    assert len(in_air) == 0, name
    assert len(ground) == 3 * FAST_HZ + 1, "every sample is ground, straight through both bends"
    assert len(walks) == 1, "and the slope between them is one walk"
```

- [ ] **Step 4: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_height_motion.py -q -p no:cacheprovider`
Expected: an error at import, `cannot import name 'height_motion' from 'app.control'`.

- [ ] **Step 5: Add the constants**

In `webapp/app/control/heights.py`, directly after the `PLATFORMS = (...)` line:

```python

# --- slopes (docs/superpowers/specs/2026-10-05-height-slopes-design.md). WALK_MIN_MPS, GRAVITY_MPS2, AIR_*,
# BURST_* and CROSS_REACH are not in that spec: they fill gaps it left.
WALK_S = 0.3               # a walk is at least this long; also the window its two limits are measured over
SLOPE_MAX = 1.0            # steepest walkable slope, rise over run
WALK_ACC_MAX = 4.0         # m/s2 of vertical acceleration over WALK_S: above it the player is in the air
WALK_MIN_MPS = 0.5         # slower than this over WALK_S is standing, not walking
GRAVITY_MPS2 = 20.0        # how fast z's rate of change falls in the air (measured on real rounds)
AIR_RATE_S = 0.1           # the window a vertical or ground speed is measured over when looking for flights
AIR_RATE_SLACK_MPS = 0.6   # z changing this much faster than SLOPE_MAX allows for the ground covered is a flight
AIR_CORE = 0.6             # ... and so is z's rate dropping by this share of gravity, two windows in a row
AIR_FIT_M = 0.1            # a flight lasts for as long as the track stays this close to its arc
AIR_LANDING_MPS = 1.0      # a flight found by its drop alone ends with z's rate jumping up by at least this
LOW_PCT = 10               # a floor's height is this percentile of its samples: the lowest wins
ABILITY_BLACKOUT_S = 3.0   # a player's samples are dropped this long after a movement ability
# `<code>_<name>` of the casts that start a blackout, where a round's `util` records one
AIRBORNE_ABILITIES = ("Clay_Q_Explosion",)
BURST_S = 0.1              # a movement ability with no recorded cast is told by its speed over this long:
BURST_MPS = 12.0           # faster than this along the ground (a dash), or
BURST_UP_MPS = 8.0         # rising faster than this (an updraft, a blast pack)
CROSS_REACH = 2            # a ground run crosses a cell when it has the two sides within this many cells of it
SILENT_DROP_M = 1.0        # the highest fall the unknown still spreads down

# what a cell's height came from (the asset's `kind`)
KIND_NONE, KIND_STANDS, KIND_WALKS, KIND_FILLED, KIND_GRADIENT = 0, 1, 2, 3, 4
# what a connection is (the fifth column of the asset's `edges`)
EDGE_STEP, EDGE_SLIDE, EDGE_FALL = 0, 1, 2
```

- [ ] **Step 6: Write `height_motion.py`**

Create `webapp/app/control/height_motion.py`:

```python
"""How players move, for the height build (docs/superpowers/specs/2026-10-05-height-slopes-design.md, parts 1
and 1b): the tracks of a stored round, the time after a movement ability that is left out of them, and the
**walks**, the second kind of ground evidence beside height_build.py's stands.

- **Tracks.** `tracks` gives each stored segment as arrays. With `skip` ({slot: [(t0, t1), ...]}) the samples
  inside those times are left out and the segment is cut there, so nothing downstream joins the two sides.
- **Blackouts.** `blackouts` is that `skip`: ABILITY_BLACKOUT_S from each cast listed in AIRBORNE_ABILITIES, and
  from each burst of speed no player makes on foot (BURST_MPS along the ground, BURST_UP_MPS upward, over
  BURST_S). Stored rounds don't record a cast for Jett's updraft and dash or Waylay's dash (measured
  2026-10-05), so those are told by the burst; a teleport reads as one too, which is harmless.
- **Walks.** A run of at least WALK_S in which the player moves on the ground: each sample lies in a WALK_S
  window over which the player moved (WALK_MIN_MPS), z changed no faster than SLOPE_MAX against the distance
  covered, and z's acceleration (a least-squares parabola over the window) stayed within WALK_ACC_MAX. In the
  air z follows an arc (about 20 m/s2 in real rounds); on a slope it changes in step with the ground. A sample
  that a stand already holds is not a walk's. A walk keeps, for each cell it passes through, the lowest z it
  had there.

Local tooling only, like the build: the web app never imports this module.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from app.control import heights as hc
from app.control.geometry import CELL, GRID, PX, Geometry

DM = 10.0     # decimetres per metre


@dataclass
class Walk:
    round: int            # index into the build's rounds
    slot: int
    t0: float
    t1: float
    cells: tuple          # flat cells it passes through, in order (no repeats in a row)
    low: tuple            # the lowest z (world dm) of its samples in each of `cells`


def tracks(blob: dict, skip: dict | None = None):
    """(slot, t, x px, y px, z dm | None) per stored segment; with `skip`, per piece of one outside its times."""
    hz = blob["hz"]
    for slot, segments in (blob.get("tracks") or {}).items():
        for seg in segments:
            n = len(seg["u"])
            t = seg["t0"] + np.arange(n) / hz
            x = np.cumsum(np.asarray(seg["u"], np.int64)) * PX / 10000
            y = np.cumsum(np.asarray(seg["v"], np.int64)) * PX / 10000
            z = np.cumsum(np.asarray(seg["z"], np.int64)) if "z" in seg else None
            spans = (skip or {}).get(int(slot))
            if not spans:
                yield int(slot), t, x, y, z
                continue
            keep = np.ones(n, bool)
            for t0, t1 in spans:
                keep &= ~((t >= t0) & (t <= t1))
            for i, j in _runs_of(keep):
                yield int(slot), t[i:j], x[i:j], y[i:j], None if z is None else z[i:j]


def _runs_of(mask: np.ndarray) -> list[tuple[int, int]]:
    """The [i, j) runs of True in `mask`."""
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False]))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def cells_of(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return (np.clip(y, 0, PX - 1).astype(int) // CELL) * GRID + np.clip(x, 0, PX - 1).astype(int) // CELL


def _merge(spans: list) -> list:
    out: list = []
    for t0, t1 in sorted(spans):
        if out and t0 <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], t1))
        else:
            out.append((t0, t1))
    return out


def blackouts(blob: dict, geo: Geometry) -> dict[int, list[tuple[float, float]]]:
    """{slot: [(t0, t1), ...]}: the times each player's samples are left out, merged and in order."""
    out: dict[int, list] = defaultdict(list)
    for e in blob.get("util") or []:
        if e.get("k") == "ability" and e.get("by") is not None \
                and f"{e.get('code')}_{e.get('name')}" in hc.AIRBORNE_ABILITIES:
            out[int(e["by"])].append((float(e["t"]), float(e["t"]) + hc.ABILITY_BLACKOUT_S))
    hz = blob["hz"]
    k = max(1, int(round(hc.BURST_S * hz)))
    for slot, t, x, y, z in tracks(blob):
        if len(t) <= k:
            continue
        fast = np.hypot(x[k:] - x[:-k], y[k:] - y[:-k]) * geo.m_per_px > hc.BURST_MPS * k / hz
        if z is not None:
            fast |= (z[k:] - z[:-k]) / DM > hc.BURST_UP_MPS * k / hz
        for i, j in _runs_of(fast):
            out[slot].append((float(t[i]), float(t[j - 1 + k]) + hc.ABILITY_BLACKOUT_S))
    return {slot: _merge(spans) for slot, spans in out.items()}


def _acc_filter(n: int, hz: float) -> np.ndarray:
    """Weights that give a least-squares parabola's second derivative (per s2) over n samples at `hz`."""
    k = np.arange(n) - (n - 1) / 2
    return 2 * np.linalg.pinv(np.stack([np.ones(n), k, k * k], 1))[2] * hz * hz


def _rate(v: np.ndarray, n: int, hz: float) -> np.ndarray:
    """v's rate of change per second, a least-squares line over the n samples centred on each one; NaN where
    that window doesn't fit in the track."""
    out = np.full(len(v), np.nan)
    if len(v) >= n:
        k = np.arange(n) - (n - 1) / 2
        out[n // 2:len(v) - n // 2] = np.convolve(v, (k / (k @ k) * hz)[::-1], "valid")
    return out


def airborne(x: np.ndarray, y: np.ndarray, z: np.ndarray, hz: float, m_per_px: float) -> np.ndarray:
    """Per sample: the player is in the air (a jump, a fall, a boost's way down).

    One least-squares parabola over a window can't tell: a takeoff or a landing bends z the other way and the
    two cancel. So a flight is found by what only a flight does, and then followed along its arc:

    - a **seed** is a run of samples where z changes faster than SLOPE_MAX allows for the ground covered
      (AIR_RATE_S windows, AIR_RATE_SLACK_MPS of slack), or where z's rate keeps dropping by at least AIR_CORE
      of gravity across two windows in a row (one bend in a slope moves it once, not twice);
    - from the seed, the arc `z = a + b t - GRAVITY_MPS2 / 2 t^2` is fitted and followed both ways for as long
      as the track stays within AIR_FIT_M of it: that is the flight;
    - a flight found only by the second kind of seed must end in a landing (z's rate jumps up by
      AIR_LANDING_MPS where the arc stops fitting). The top of a staircase bends like the start of a fall, and
      has no landing.

    A flight shorter than about a quarter of a second with no fast part isn't found: a hop of a few decimetres.
    """
    n_all = len(z)
    air = np.zeros(n_all, bool)
    n = 2 * int(round(hc.AIR_RATE_S * hz / 2)) + 1
    if n < 3 or n_all < 3 * n:
        return air
    zm = z / DM
    vz = _rate(zm, n, hz)
    vh = np.hypot(_rate(x * m_per_px, n, hz), _rate(y * m_per_px, n, hz))
    s = n - 1
    drop = np.full(n_all, np.nan)
    drop[s:] = vz[s:] - vz[:-s]
    again = np.full(n_all, np.nan)
    again[:-s] = drop[s:]
    least = -hc.AIR_CORE * hc.GRAVITY_MPS2 * s / hz
    with np.errstate(invalid="ignore"):
        steep = np.abs(vz) > hc.SLOPE_MAX * vh + hc.AIR_RATE_SLACK_MPS
        seed = steep | ((drop <= least) & (again <= least))
    t = np.arange(n_all) / hz
    for a, b in _runs_of(seed):
        lo, hi = max(0, a - n // 2), min(n_all, b + n // 2)
        tau = t[lo:hi] - t[a]
        slope, start = np.polyfit(tau, zm[lo:hi] + hc.GRAVITY_MPS2 / 2 * tau ** 2, 1)
        tau = t - t[a]
        fits = np.abs(zm - (start + slope * tau - hc.GRAVITY_MPS2 / 2 * tau ** 2)) <= hc.AIR_FIT_M
        i, j = a, b
        while i > 0 and fits[i - 1]:
            i -= 1
        while j < n_all and fits[j]:
            j += 1
        if not steep[a:b].any():
            before, after = j - 3 - n // 2, j + 2 + n // 2
            if before < 0 or after >= n_all or not (vz[after] - vz[before] >= hc.AIR_LANDING_MPS):
                continue
        air[i:j] = True
    return air


def on_ground(x: np.ndarray, y: np.ndarray, z: np.ndarray, hz: float, m_per_px: float) -> np.ndarray:
    """Per sample: it isn't in a flight (`airborne`), and some WALK_S window holding it is a walking one: it
    holds no airborne sample, the player moved, and the module docstring's two limits hold. Any such window
    rather than every one: where a slope begins or ends the windows across the bend fail the acceleration
    limit, and the ones on either side of it still hold its samples."""
    n = len(z)
    w = int(round(hc.WALK_S * hz)) + 1
    if w < 3 or n < w:
        return np.zeros(n, bool)
    air = airborne(x, y, z, hz, m_per_px)
    span = (w - 1) / hz
    dxy = np.hypot(x[w - 1:] - x[:n - w + 1], y[w - 1:] - y[:n - w + 1]) * m_per_px
    dz = (z[w - 1:] - z[:n - w + 1]) / DM
    acc = np.convolve(z / DM, _acc_filter(w, hz)[::-1], "valid")
    ok = (np.convolve(air.astype(int), np.ones(w, int), "valid") == 0) & (dxy >= hc.WALK_MIN_MPS * span) \
        & (np.abs(dz) <= hc.SLOPE_MAX * dxy + 1 / DM) & (np.abs(acc) <= hc.WALK_ACC_MAX)   # z is whole decimetres
    return (np.convolve(ok, np.ones(w, int)) > 0) & ~air


def walks(blob: dict, geo: Geometry, round_index: int = 0, found=(), skip: dict | None = None) -> list[Walk]:
    """The round's walks, each player's in time order. `found` are the round's stands: their samples are
    theirs. Segments without z give none."""
    hz = blob["hz"]
    need = int(np.ceil(hc.WALK_S * hz - 1e-9)) + 1
    busy: dict[int, list] = defaultdict(list)
    for s in found:
        busy[s.slot].append((s.t0, s.t1))
    out = []
    for slot, t, x, y, z in tracks(blob, skip):
        if z is None:
            continue
        good = on_ground(x, y, z.astype(float), hz, geo.m_per_px)
        for t0, t1 in busy.get(slot, ()):
            good &= ~((t >= t0 - 1e-9) & (t <= t1 + 1e-9))
        for i, j in _runs_of(good):
            if j - i < need:
                continue
            cells = cells_of(x[i:j], y[i:j])
            starts = np.r_[0, np.flatnonzero(cells[1:] != cells[:-1]) + 1]
            low = np.minimum.reduceat(z[i:j], starts)
            out.append(Walk(round_index, slot, float(t[i]), float(t[j - 1]), tuple(int(c) for c in cells[starts]),
                            tuple(int(v) for v in low)))
    out.sort(key=lambda w: (w.slot, w.t0))
    return out
```

- [ ] **Step 7: Let the stands skip a blackout**

In `webapp/app/control/height_build.py`:

Add the import beside the others (keep them sorted):

```python
from app.control import height_motion as hm
from app.control import heights as hc
```

Replace the whole `_tracks` function (its body moved to `height_motion.tracks`) with one line:

```python
_tracks = hm.tracks
```

Change `stands`' signature, docstring and its loop header (the rest of the function is unchanged):

```python
def stands(blob: dict, geo: Geometry, round_index: int = 0, skip: dict | None = None) -> list[Stand]:
    """The round's stands, each player's in time order. Segments without z give none; `skip` leaves out the
    time after a movement ability (height_motion.blackouts)."""
    hz = blob["hz"]
    tol = hc.STAND_TOL_M * DM
    need = int(np.ceil(hc.STAND_S * hz - 1e-9)) + 1     # samples spanning STAND_S
    apex_n = int(round(0.25 * hz))                      # how far either side an apex looks for lower ground
    out = []
    for slot, t, x, y, z in _tracks(blob, skip):
```

- [ ] **Step 8: Run the new tests**

Run: `PY -m pytest tests/replays/test_height_motion.py -q -p no:cacheprovider`
Expected: 18 passed.

If a flight test fails, print `hm.airborne(...)`'s runs for that track before touching a constant: each case in
those tests names its flight's start and end, and the detector should cover it to within three hundredths of a
second. A constant that has to move is a start value moving: report it.

- [ ] **Step 9: Bump `CONTROL_REVISION` and pin its digest**

The new constants are in `heights.py`, which the revision's digest covers. In `webapp/app/replays/control_format.py` set `CONTROL_REVISION = 7` (6 is `main`'s live-claim rule; see the Global Constraints).

Run: `PY -m pytest tests/replays/test_control_format.py::test_the_engine_constants_are_pinned_to_the_control_revision -q -p no:cacheprovider`
Expected: FAIL, and the message prints the new digest (`the engine's or geometry's constants changed (digest <16 hex>)`).

In `webapp/tests/replays/test_control_format.py`, add `7: "<that digest>"` to `PINNED` (leave 6's entry as `main` has it) and this to the comment above it:

```python
# 7: height slopes (docs/superpowers/specs/2026-10-05-height-slopes-design.md): the walk, blackout and gradient
# start values, and SILENT_DROP_M (the unknown stops at a fall higher than it). Unreleased, so re-pinned in
# place as its steps land.
```

Run the same test again. Expected: PASS.

- [ ] **Step 10: Run the control suite**

Run: `PY -m pytest tests/replays -k "control or height or gaps or feature" -q -p no:cacheprovider`
Expected: everything passes except the failures recorded in the baseline (Step 1), failing the same way. In particular `test_control_reference.py` and all of `test_control_heights.py` pass: nothing calls `walks` or `blackouts` yet.

- [ ] **Step 11: Commit**

```bash
git add webapp/app/control/heights.py webapp/app/control/height_motion.py webapp/app/control/height_build.py webapp/app/replays/control_format.py webapp/tests/replays/test_control_format.py webapp/tests/replays/control_toys.py webapp/tests/replays/test_height_motion.py
git commit -F <message file>
```

Message: `Heights: walks and blackouts from a round's tracks (not used by the build yet)`. Write it to a scratch file with the Write tool and pass it with `-F`; end it with the attribution line the session gives.

---

### Task 1b: The condenser records movement-ability casts, and the blackout reads them (decision D1)

Unlike the other tasks, this one starts with a search, because nobody has yet found the signal. What is known
(checked 2026-10-07 on the local export `%TEMP%\valo-replay\0f452716-1e90-4782-afba-29229fdab922`, a match with a
Jett and a Waylay):

- `events.ndjson` spawns an equippable actor for each movement ability: `Ability_Wushu_E_Dash` (Jett's dash),
  `Ability_Wushu_Q_CycloneBoost` (her updraft), `Ability_Terra_Q_DoubleDash` (Waylay's dash). Raze's are
  `Ability_Clay_*`; that match has no Raze.
- No `rpc_received` and no `export_group_received` line names those three by class path. So a use is not an
  event on the ability's own actor, the way it is for `Ability_Wushu_4_Smoke` (21 RPCs).
- `webapp/app/replays/extras.py` already ties a pawn's effect RPCs to the equippable they name in their context
  (`read_raw`: `equip_now[value].votes`, `raw.contexts`; `equippable_claims`). That is the first place to look:
  a pawn effect whose context holds a movement equippable's GUID at the moment of the dash.

**Files:**
- Modify: `webapp/app/replays/extras.py` (`read_raw`, `build_extras`, `util_entries`, the module docstring's entry list), `webapp/app/replays/format.py` (`CONDENSE_REVISION`, the blob's documented keys)
- Modify: `webapp/app/control/heights.py` (`AIRBORNE_ABILITIES`), `webapp/app/control/height_motion.py` (`blackouts`)
- Test: `webapp/tests/replays/test_height_motion.py`, and the condenser's own test file for `extras.py` (find it with `git grep -l "build_extras" -- webapp/tests`)

**Interfaces:**
- Produces:
  - A round's `util` gains `{"k": "cast", "t": <seconds>, "by": <slot>, "code": "Wushu", "name": "E_Dash"}`, one per use of a movement ability. A new kind, not `"ability"`: every reader of `"ability"` entries expects a place and a lifetime, and a cast has neither.
  - The blob gains `"movement_casts": 1`. It says "this round was condensed by a condenser that records casts", so a round with none is known to have had none.
  - `CONDENSE_REVISION` goes up by one from whatever the base has (13 once `afk/2026-10-05-utility-review` is merged; take the next free number, as for `CONTROL_REVISION`).
  - `heights.AIRBORNE_ABILITIES = ("Wushu_Q_CycloneBoost", "Wushu_E_Dash", "Terra_Q_DoubleDash", "Clay_Q_Explosion")`, plus Raze's blast pack cast under whatever name Step 1 finds for it. `Clay_Q_Explosion` stays: it is an `"ability"` entry the condenser already writes, and it lifts teammates who cast nothing.
  - `height_motion.blackouts(blob, geo)`: casts and listed abilities always; the speed rule only when the blob has no `movement_casts`.

- [ ] **Step 1: Find the signal (at most 90 minutes; read-only)**

Write a throwaway script outside the repository that reads that export's `events.ndjson` once (it is 2.6 GB:
stream it, never load it) and, for the GUIDs of the three movement equippables and of the Jett and Waylay pawns,
prints every `rpc_received` and `export_group_received` line that carries one of those GUIDs anywhere in its
payload, with `time_ms`, `function_name` and the payload's keys. Then, for each candidate signal, line its times
up against the times that player's track shows a burst (`height_motion.blackouts` on the stored round: the dash
and the updraft are unmistakable in the track).

A signal is good enough when, over that match: at least 9 in 10 of its events fall within 0.5 s of a burst by the
same player, and at least 9 in 10 of that player's bursts have one. Write down the function name, the payload
field that carries the equippable, and the two counts.

**If no signal passes after 90 minutes, stop this task here.** Recording the cast then needs a change to the
replay parser, which is outside this repository. Do not write Steps 2 to 5. Leave `blackouts` as Task 1 built it
(the speed rule for every round), report what was tried with the counts, and carry on with Task 2: that outcome
is the owner's to decide on, not a reason to stop the plan.

- [ ] **Step 2: Write the failing tests**

In the condenser's test file, in the style of its other `build_extras` tests (it builds a small `events.ndjson`
by hand): a round in which a pawn's effect names a movement equippable gives one `{"k": "cast", ...}` entry with
that player's slot, the equippable's `code` and `name`, and the effect's time; a second effect within 0.2 s of
the first for the same equippable is the same cast, not another; an effect that names a non-movement equippable
gives none; and the blob has `movement_casts == 1` whether or not anybody cast.

Append to `webapp/tests/replays/test_height_motion.py`:

```python
def test_a_round_with_recorded_casts_starts_blackouts_from_them_and_not_from_speed():
    dash = ("A", [(0.0, 160, Y, 0, 0.0), (2.0, 160, Y, 0, 0.0), (2.3, 216, Y, 0, 0.0), (10.0, 216, Y, 0, 0.0)])
    sprint = ("B", [(0.0, 160, 252, 0, 0.0), (4.0, 160, 252, 0, 0.0), (4.3, 216, 252, 0, 0.0), (10.0, 216, 252, 0, 0.0)])
    cast = {"k": "cast", "t": 2.0, "by": 0, "code": "Wushu", "name": "E_Dash"}
    other = {"k": "cast", "t": 6.0, "by": 0, "code": "Wushu", "name": "4_Smoke"}
    b = fast_blob({0: dash, 1: sprint}, util=[cast, other])
    old = hm.blackouts(b, GEO)
    assert 1.9 <= old[0][0][0] <= 2.01 and 1 in old, "condensed before casts were recorded: both are told by speed"
    b["movement_casts"] = 1
    new = hm.blackouts(b, GEO)
    assert new == {0: [(2.0, 5.0)]}, "the cast, from its time; a smoke is no movement ability; speed alone is nothing"
    b["util"] = []
    assert hm.blackouts(b, GEO) == {}, "a round known to have no cast has no blackout, however fast anyone moved"
```

Run both. Expected: the condenser test fails (no `"cast"` entries), the motion test fails on `new`.

- [ ] **Step 3: The condenser**

In `extras.py`: name the movement equippables once (`MOVEMENT_EQUIPPABLES`, by `<code>_<name>` as
`EQUIPPABLE_ARCHETYPE` splits an archetype), collect a cast for each use by the signal Step 1 found, owned by the
pawn that produced it (the same slot lookup the equippable claims use), merge uses of one equippable by one
player within 0.2 s, and write them through `util_entries` as `"cast"` entries in time order with the others.
Write `"movement_casts": 1` where the round's blob is assembled (follow how `format.py` documents the blob's
keys, and add both there). Bump `CONDENSE_REVISION` and say why in its comment, as the earlier bumps do.

Everything that reads `util` must ignore a kind it doesn't know. Check it: `git grep -n '"k"\] ==\|\.get("k")' -- webapp/app webapp/scripts`
and read each reader; one that would fail or draw something for an entry without `u`, `v` or `t1` is fixed to
skip `"cast"`. The replay viewer's JS reads `util` too (`git grep -n "\.k ===" -- webapp/app/static webapp/scripts`).

- [ ] **Step 4: The blackout reads casts**

In `heights.py` set `AIRBORNE_ABILITIES` as above. In `height_motion.blackouts`, the first loop accepts both
kinds, and the speed loop runs only for a round without `movement_casts`:

```python
    for e in blob.get("util") or []:
        if e.get("k") in ("ability", "cast") and e.get("by") is not None \
                and f"{e.get('code')}_{e.get('name')}" in hc.AIRBORNE_ABILITIES:
            out[int(e["by"])].append((float(e["t"]), float(e["t"]) + hc.ABILITY_BLACKOUT_S))
    if blob.get("movement_casts"):
        return {slot: _merge(spans) for slot, spans in out.items()}
```

and its docstring and the module's "Blackouts" bullet say which rounds use which rule. `heights.py` changed, so
re-pin the control revision's digest as Task 1 Step 9 did.

- [ ] **Step 5: Run it on the real export, then the suites**

Condense that export with the new condenser (the way the condenser's tests or `scripts/ingest_replay.py --dry-run`
do it; write nothing to a database) and print, per Jett and Waylay round, the casts found beside the bursts in
the track. Expected: the two lists agree as Step 1's counts said. Keep the printed table: it goes on the review
card for this decision.

Run: `PY -m pytest tests/replays -k "extras or condense or height or control_format or viewer" -q -p no:cacheprovider`
Expected: everything passes except the baseline's failures. A test that pins a condensed blob's exact bytes or
its recipe will need its expected revision moved: that is the bump, not a regression; anything else that changes
in a pinned blob is.

- [ ] **Step 6: Commit**

Message: `Condenser: record movement-ability casts; the height build's blackout reads them (speed only for rounds condensed before)`.

**What this does not do:** re-parse anything. Stored rounds keep their old blobs until the re-parse queue (its
own plan) reaches them; until then their blackouts come from speed. Task 2's frozen rounds are all such rounds,
so the judgment in Task 6 is of the speed rule. The cast rule is judged on the one local export in Step 5 and
again whenever re-parsed rounds exist.

---

### Task 2: Freeze the rounds, measure them, and keep the old rules' builds

Both sets of rules are judged on the same rounds. This task makes one frozen copy of them, measures what the
slope rules rest on from that copy, builds every map under the **old** rules from it, and adds the tool that
compares two builds and refuses to when they read different things.

**Files:**
- Create: `webapp/scripts/measure_height_motion.py`, `webapp/scripts/freeze_height_rounds.py`, `webapp/scripts/compare_height_builds.py`
- Modify: `webapp/scripts/build_control_heights.py` (imports, `index_entry`, new `round_identity` and `build_inputs`, two lines of `main`)
- Create: `webapp/tests/replays/test_compare_height_builds.py`
- Modify: `webapp/tests/replays/test_height_motion.py` (measurement and independent steep-descent tests)
- Modify: `docs/superpowers/specs/2026-10-05-height-slopes-design.md` (a new "Measured" section; open questions 1 and 2; "How it is judged")

**Interfaces:**
- Consumes: `height_motion.tracks`, `airborne`, `on_ground`, `_acc_filter`, `_runs_of`, `cells_of`; `heights` constants; `build_control_heights.blob_rounds(directory, map)` and `db_rounds(map, session_factory)`, both returning `([(match, n, blob)], skipped)`.
- Produces:
  - `measure_height_motion.measure(rounds, geo, rect=None) -> dict`, `lines(name, m, rect=None) -> list[str]`, `main(argv=None, session_factory=None) -> int`.
  - `build_control_heights.round_identity(rows) -> {"rounds", "matches", "rounds_sha"}` for `[[match, n, sha256], ...]`; `build_inputs(args, geo, rounds) -> dict` (`rounds`, `matches`, `rounds_sha` (None from a database), `sight_sha`, `walk_sha`, `preview_min_matches`; raises `ValueError` when a frozen folder changed); `index_entry(build, inputs=None)`: a preview's `<Map>.height.json` gains `"inputs"`.
  - `freeze_height_rounds.freeze(session, map_name, out, min_revision=11) -> dict` (the manifest it writes to `<out>/<Map>.frozen.json`: `map, frozen_at, rounds, matches, rounds_sha, round_list`); `main(argv=None, session_factory=None) -> int`.
  - `compare_height_builds.read(folder, name) -> dict`, `refusals(old, new) -> list[str]`, `flat_ground(old, new) -> {"cells", "lost", "within", "pct"}`, `compare(name, old, new) -> (lines, worse)`, `main(argv=None) -> int` (0; 1 a kill-line share got worse; 2 refused, or no map in both folders).
  - Folders Task 3 and Task 6 read: `%TEMP%\valo-replay\heights-frozen` (the rounds) and `%TEMP%\valo-replay\heights-preview-old` (the old rules' builds).

- [ ] **Step 1: Write the failing tests**

Append to `webapp/tests/replays/test_height_motion.py`:

```python
def test_the_measurement_counts_casts_bursts_flights_and_the_staircase():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import measure_height_motion as measure

    dash = ("A", [(0.0, 160, Y, 0, 0.0), (2.0, 160, Y, 0, 0.0), (2.3, 216, Y, 0, 0.0), (10.0, 216, Y, 0, 0.0)])
    cast = {"k": "ability", "t": 6.0, "t1": 6.5, "by": 0, "kind": "GameObject", "code": "Wushu", "name": "4_Smoke",
            "u": 0, "v": 0}
    hop = flown(lambda t: 4.6 * (t - 1) - 10 * (t - 1) ** 2 if 1 <= t < 1.46 else 0.0, seconds=10.0)
    b = fast_blob({0: dash, 1: stair_run(), 2: hop}, util=[cast])     # control_toys.blob makes every player a Jett
    m = measure.measure([("match-0", 1, b)], GEO, rect=(21, ROW, 26, ROW))
    assert dict(m["hz"]) == {FAST_HZ: 1} and m["segments_with_z"] == 3
    assert m["agent_rounds"]["Jett"] == 1 and m["casts"] == {("Jett", "Wushu_4_Smoke"): 1}
    text = "\n".join(measure.lines("Toy", m, (21, ROW, 26, ROW)))
    assert "Jett: 1 rounds; casts recorded: {'Wushu_4_Smoke': 1}" in text and "Raze: 0 rounds" in text
    assert m["rect_windows"] > 0 and m["rect_walk"] / m["rect_windows"] > 0.9, "the stairs are a walk"
    assert max(float(s.max()) for s in m["speed"]["movers"]) > 12, "the dash is in the speeds"
    assert len(m["flights"]) == 1 and 0.4 <= m["flights"][0] <= 0.6, "the jump is the one flight"
    assert 15 <= m["gravity"][0] <= 25 and "gravity read off the 1 flights" in text


def test_steep_descent_measurement_does_not_filter_out_what_the_air_rule_rejects():
    import measure_height_motion as measure

    # 2.9 m/s horizontally, slope 1.5: a steady slide candidate, which the current air rule calls airborne.
    seconds = 4.0
    pixels = 2.9 * seconds / GEO.m_per_px
    track = ("A", [(0.0, 160, Y, 0, 20.0), (seconds, 160 + pixels, Y, 0, 20.0 - 1.5 * 2.9 * seconds)])
    b = fast_blob({0: track}, t_end=seconds)
    _, _, x, y, z = next(hm.tracks(b))
    assert hm.airborne(x, y, z.astype(float), FAST_HZ, GEO.m_per_px).mean() > 0.8
    got = measure.measure([("match-a", 1, b), ("match-b", 1, b)], GEO)
    assert got["steep"] and max(map(len, got["steep"].values())) == 2
    assert "steady descent candidates" in "\n".join(measure.lines("Toy", got))

```

Create `webapp/tests/replays/test_compare_height_builds.py`:

```python
"""scripts/compare_height_builds.py: two builds of one map side by side (the slopes spec, "How it is judged")."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from app.control import heights as hc

WEBAPP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WEBAPP / "scripts"))

import compare_height_builds as compare  # noqa: E402

GRID, MAXF = 128, hc.MAX_FLOORS
INPUTS = {"rounds_sha": "r" * 16, "rounds": 44, "matches": 2, "sight_sha": "s" * 12, "walk_sha": "w" * 12,
          "preview_min_matches": None}


def write(folder: Path, ground_dm: dict, origin: int, kills: tuple, name="Toy", supported=None, inputs=INPUTS,
          checks=True):
    """A preview: {(y, x): dm above `origin`} as ground, each supported unless listed otherwise."""
    folder.mkdir(parents=True, exist_ok=True)
    floors = np.full((GRID, GRID, MAXF), -1, np.int16)
    sup = np.zeros((GRID, GRID), bool)
    for (y, x), dm in ground_dm.items():
        floors[y, x, 0], sup[y, x] = dm, True if supported is None else (y, x) in supported
    asset = hc.HeightAsset(floors, np.zeros_like(floors), sup, np.zeros((GRID, GRID), bool), np.zeros((0, 5), np.int32),
                           {"origin_z": origin})
    hc.save_asset(folder / f"{name}.height.npz", asset)
    blocked, qualifying = kills
    report = {"supported": 0.5, "unresolved_cells": 0, "unresolved_why": {}, "filled_cells": 0, "one_way_edges": 0,
              "ready": False, "not_ready": ["thin"]}
    if checks:
        report.update({"must_block": {"blocked": 0, "checked": 0, "lines": 0, "passes": True},
                       "kill_lines": {"blocked": blocked, "qualifying": qualifying, "share": blocked / qualifying,
                                      "passes": True}})
    wrapper = {"height_sha": asset.digest, "height": report}
    if inputs is not None:
        wrapper["inputs"] = dict(inputs)
    (folder / f"{name}.height.json").write_text(json.dumps(wrapper), encoding="utf-8")


def test_flat_ground_is_compared_in_world_heights_and_lost_cells_are_counted_apart(tmp_path):
    # Old: origin 100; supported cells at 0, 0, 20 and 40 dm, and one filled. New: origin 90 (a lower floor was
    # found), the same ground 10 dm up in its own frame, except one cell 5 dm lower and one with no height.
    write(tmp_path / "old", {(5, 5): 0, (5, 6): 0, (5, 7): 20, (5, 4): 40, (5, 8): 0}, 100, (0, 100),
          supported={(5, 5), (5, 6), (5, 7), (5, 4)})
    write(tmp_path / "new", {(5, 5): 10, (5, 6): 5, (5, 7): 30, (5, 9): 0}, 90, (1, 100))
    old, new = compare.read(tmp_path / "old", "Toy"), compare.read(tmp_path / "new", "Toy")
    assert compare.refusals(old, new) == []
    flat = compare.flat_ground(old, new)
    assert (flat["cells"], flat["lost"]) == (3, 1), "the lost cell is not in the distribution"
    assert abs(flat["within"] - 2 / 3) < 1e-9 and -0.5 <= min(flat["pct"]) <= -0.45
    lines, worse = compare.compare("Toy", old, new)
    assert worse and any("WORSE" in line for line in lines)
    assert any("supported before, no height now: 1 cells" in line for line in lines)
    assert any("gained 1 cells, lost 2 in all" in line for line in lines)
    assert compare.main(["--old", str(tmp_path / "old"), "--new", str(tmp_path / "new")]) == 1


def test_builds_of_different_rounds_or_without_their_checks_are_refused(tmp_path, capsys):
    ground = {(5, 5): 0}
    write(tmp_path / "old", ground, 100, (0, 10))
    cases = {"other rounds": {"inputs": {**INPUTS, "rounds_sha": "x" * 16}},
             "another walk mask": {"inputs": {**INPUTS, "walk_sha": "y" * 12}},
             "another preview rule": {"inputs": {**INPUTS, "preview_min_matches": 1}},
             "no record of its rounds": {"inputs": None},
             "built from the database": {"inputs": {**INPUTS, "rounds_sha": None}},
             "no checks": {"checks": False}}
    for name, kw in cases.items():
        write(tmp_path / name, ground, 100, (0, 10), **kw)
        old, new = compare.read(tmp_path / "old", "Toy"), compare.read(tmp_path / name, "Toy")
        assert compare.refusals(old, new), name
        assert compare.main(["--old", str(tmp_path / "old"), "--new", str(tmp_path / name)]) == 2, name
    out = capsys.readouterr().out
    assert "REFUSED" in out and "rounds_sha differs" in out and "no kill_lines result" in out
    assert "doesn't say which rounds it read" in out


def test_it_reads_an_older_assets_file_and_says_when_nothing_matches(tmp_path, capsys):
    write(tmp_path / "old", {(5, 5): 0}, 100, (0, 10))
    with np.load(tmp_path / "old" / "Toy.height.npz") as z:      # rewrite it as a version-1 file: 4 edge columns
        parts = {k: z[k] for k in z.files if k != "kind"}
    meta = json.loads(bytes(parts["meta"]).decode("utf-8"))
    meta["version"] = 1
    parts["meta"] = np.frombuffer(json.dumps(meta).encode("utf-8"), np.uint8)
    parts["edges"] = np.zeros((0, 4), np.int32)
    np.savez_compressed(tmp_path / "old" / "Toy.height.npz", **parts)
    write(tmp_path / "new", {(5, 5): 0}, 100, (0, 10))
    assert compare.main(["--old", str(tmp_path / "old"), "--new", str(tmp_path / "new")]) == 0
    assert "old: v1" in capsys.readouterr().out
    assert compare.main(["--old", str(tmp_path / "old"), "--new", str(tmp_path / "empty")]) == 2
```

and append to it:

```python
# ---------------------------------------------------------------- one frozen set of rounds for both builds

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_control_heights import covered_rounds, toy_assets, write_blobs  # noqa: E402
from test_control_store import db, factory, linked  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed  # noqa: E402,F401  (fixture)

import build_control_heights as command  # noqa: E402
import freeze_height_rounds as freeze  # noqa: E402
from app.models.replay import ReplayRound  # noqa: E402
from app.replays import format as fmt  # noqa: E402


def test_a_preview_records_what_it_read_and_two_builds_of_one_folder_agree(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(command, "picture_path", lambda name: tmp_path / f"{name}.height.png")
    monkeypatch.setattr(command, "MUST_BLOCK", tmp_path / "no-must-block.json")
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    base = ["--map", "Ascent", "--blobs-dir", str(blobs), "--preview"]
    assert command.main([*base, "--out", str(tmp_path / "a")], asset_dir=assets) == 0
    assert command.main([*base, "--out", str(tmp_path / "b")], asset_dir=assets) == 0
    a = json.loads((tmp_path / "a" / "Ascent.height.json").read_text(encoding="utf-8"))["inputs"]
    b = json.loads((tmp_path / "b" / "Ascent.height.json").read_text(encoding="utf-8"))["inputs"]
    assert a == b and (a["rounds"], a["matches"]) == (6, 2) and len(a["rounds_sha"]) == 16
    assert len(a["walk_sha"]) == 12 and len(a["sight_sha"]) == 12 and a["preview_min_matches"] is None
    assert compare.main(["--old", str(tmp_path / "a"), "--new", str(tmp_path / "b")]) == 0
    one = next(blobs.glob("*/1.json.gz"))
    one.write_bytes(fmt.encode_blob({**fmt.decode_blob(one.read_bytes()), "note": 1}))    # one round changes
    assert command.main([*base, "--out", str(tmp_path / "c")], asset_dir=assets) == 0
    c = json.loads((tmp_path / "c" / "Ascent.height.json").read_text(encoding="utf-8"))["inputs"]
    assert c["rounds_sha"] != a["rounds_sha"]
    assert compare.main(["--old", str(tmp_path / "a"), "--new", str(tmp_path / "c")]) == 2
    assert command.main([*base, "--preview-min-matches", "1", "--out", str(tmp_path / "d")], asset_dir=assets) == 0
    assert compare.main(["--old", str(tmp_path / "c"), "--new", str(tmp_path / "d")]) == 2, "another preview rule"


def test_freezing_writes_the_stored_bytes_and_a_changed_folder_is_refused(tmp_path, db, linked, monkeypatch, capsys):
    out = tmp_path / "frozen"
    manifest = freeze.freeze(db, linked.map_name, out, min_revision=0)
    rows = db.query(ReplayRound).filter(ReplayRound.replay_id == linked.id).all()
    uuid = str(linked.match_uuid).lower()
    assert manifest["rounds"] == len(rows) == len(list(out.glob("*/*.json.gz"))) and manifest["matches"] == 1
    assert all((out / uuid / f"{r.round_number}.json.gz").read_bytes() == bytes(r.data) for r in rows)
    on_disk = json.loads((out / f"{linked.map_name}.frozen.json").read_text(encoding="utf-8"))
    assert on_disk["rounds_sha"] == manifest["rounds_sha"] and len(on_disk["round_list"]) == len(rows)
    assert freeze.freeze(db, linked.map_name, tmp_path / "again", min_revision=0)["rounds_sha"] == manifest["rounds_sha"]
    assert freeze.freeze(db, linked.map_name, tmp_path / "none", min_revision=10 ** 6)["rounds"] == 0

    class Args:
        blobs_dir, map, preview_min_matches = out, linked.map_name, None

    from tests.replays.control_toys import open_hall

    rounds = [(uuid, r.round_number, None) for r in rows]
    assert command.build_inputs(Args, open_hall(), rounds)["rounds_sha"] == manifest["rounds_sha"]
    (out / uuid / "1.json.gz").write_bytes(b"changed")
    with pytest.raises(ValueError, match="not what was frozen"):
        command.build_inputs(Args, open_hall(), rounds)
    assert freeze.main(["--map", linked.map_name, "--out", str(WEBAPP / "frozen-here")], session_factory=None) == 2, \
        "never inside the repository"
```

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_height_motion.py tests/replays/test_compare_height_builds.py -q -p no:cacheprovider`
Expected: `test_compare_height_builds.py` fails at import (`No module named 'compare_height_builds'`) and the new motion test fails with `No module named 'measure_height_motion'`.

- [ ] **Step 3: The measurement script**

Create `webapp/scripts/measure_height_motion.py`:

```python
"""Measures what the slope rules of the height build rest on, from stored rounds
(docs/superpowers/specs/2026-10-05-height-slopes-design.md, "Open questions" 1 and 2, and its start values).

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\measure_height_motion.py --map Sunset --rect 26,76,28,81
    .\\.venv\\Scripts\\python.exe scripts\\measure_height_motion.py --map Sunset --blobs-dir <dir>

Read-only, and it writes nothing: rounds come from the database it is run against (through
`with_friends_db.py --read-only`) or from `--blobs-dir` (`<match>/<n>.json.gz`), as build_control_heights.py
reads them. It prints:

- the stored sample rates (`hz`), and how many track segments carry heights;
- every ability cast recorded in `util` for Jett, Waylay and Raze (`<code>_<name>` and its count), beside how
  many rounds each of those agents played: a movement ability with no line here is not recorded;
- speeds over BURST_S, for those three agents and for everyone else: along the ground and upward (what
  BURST_MPS and BURST_UP_MPS have to separate);
- vertical acceleration over WALK_S windows while moving: its histogram (the ground sits near 0, free fall
  near the game's gravity), which is what WALK_ACC_MAX has to separate;
- with `--rect x0,y0,x1,y1` (cells, inclusive): the share of moving windows inside it that pass the walk
  rule, and their slopes (the owner's staircase on Sunset is 26,76,28,81);
- steady descents steeper than SLOPE_MAX, by cell, selected independently of `airborne`: candidates for
  inspecting slides the build would read as falls. This is a diagnostic, not proof of ground contact;
  ropes and movement abilities can also produce candidates.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.control import geometry as cg  # noqa: E402
from app.control import height_motion as hm  # noqa: E402
from app.control import heights as hc  # noqa: E402

MOVERS = ("Jett", "Waylay", "Raze")
PCT = [50, 90, 99, 99.9, 99.99, 100]
ACC_BINS = [0, 1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 30, float("inf")]


def measure(rounds, geo, rect=None) -> dict:
    """Everything the command prints, from [(match, n, blob)] of one map."""
    out = {"hz": Counter(), "segments": 0, "segments_with_z": 0, "agent_rounds": Counter(), "casts": Counter(),
           "speed": defaultdict(list), "up": defaultdict(list), "acc": [], "rect_windows": 0, "rect_walk": 0,
           "rect_slopes": [], "steep": defaultdict(set), "rounds": 0, "moving": 0, "air": 0, "ground": 0,
           "flights": [], "gravity": []}
    for match, n, blob in rounds:
        out["rounds"] += 1
        hz = blob["hz"]
        out["hz"][hz] += 1
        agent_of = {int(p["slot"]): p.get("agent") for p in blob.get("players") or []}
        for agent in set(agent_of.values()) & set(MOVERS):
            out["agent_rounds"][agent] += 1
        for e in blob.get("util") or []:
            if e.get("k") == "ability" and agent_of.get(e.get("by")) in MOVERS:
                out["casts"][(agent_of[e["by"]], f"{e.get('code')}_{e.get('name')}")] += 1
        k = max(1, int(round(hc.BURST_S * hz)))
        w = int(round(hc.WALK_S * hz)) + 1
        for slot, t, x, y, z in hm.tracks(blob):
            out["segments"] += 1
            if z is None or len(t) <= max(k, w):
                continue
            out["segments_with_z"] += 1
            who = "movers" if agent_of.get(slot) in MOVERS else "others"
            out["speed"][who].append(np.hypot(x[k:] - x[:-k], y[k:] - y[:-k]) * geo.m_per_px * hz / k)
            out["up"][who].append((z[k:] - z[:-k]) / 10.0 * hz / k)
            span = (w - 1) / hz
            dxy = np.hypot(x[w - 1:] - x[:-(w - 1)], y[w - 1:] - y[:-(w - 1)]) * geo.m_per_px
            dz = (z[w - 1:] - z[:-(w - 1)]) / 10.0
            acc = np.convolve(z / 10.0, hm._acc_filter(w, hz)[::-1], "valid")
            moving = dxy >= hc.WALK_MIN_MPS * span
            out["acc"].append(np.abs(acc[moving]))
            air = hm.airborne(x, y, z.astype(float), hz, geo.m_per_px)
            ground = hm.on_ground(x, y, z.astype(float), hz, geo.m_per_px)
            mid = np.arange(len(dxy)) + w // 2
            out["moving"] += int(moving.sum())
            out["air"] += int((moving & air[mid]).sum())
            out["ground"] += int((moving & ground[mid]).sum())
            for i, j in hm._runs_of(air):
                out["flights"].append((j - i) / hz)
                if j - i >= w:                     # long enough to read gravity off: a free parabola's curvature
                    tau = np.arange(j - i) / hz
                    out["gravity"].append(-2 * float(np.polyfit(tau, z[i:j] / 10.0, 2)[0]))
            # This diagnostic must not depend on `airborne`: its slope rule is what we are measuring.
            # Low acceleration identifies candidates for inspection, not confirmed ground contact.
            quiet = moving & (np.abs(acc) <= hc.WALK_ACC_MAX)
            cells = hm.cells_of(x[mid], y[mid])
            steep = quiet & (dz < 0) & (-dz > hc.SLOPE_MAX * dxy + 0.1)
            for c in set(cells[steep].tolist()):
                out["steep"][c].add((str(match), n, slot))
            if rect is not None:
                x0, y0, x1, y1 = rect
                inside = moving & (cells % cg.GRID >= x0) & (cells % cg.GRID <= x1) \
                    & (cells // cg.GRID >= y0) & (cells // cg.GRID <= y1)
                walking = inside & ground[mid]
                out["rect_windows"] += int(inside.sum())
                out["rect_walk"] += int(walking.sum())
                out["rect_slopes"].append(np.abs(dz[walking]) / dxy[walking])
    return out


def lines(name: str, m: dict, rect=None) -> list[str]:
    out = [f"{name}: {m['rounds']} rounds; hz {dict(m['hz'])}; {m['segments_with_z']}/{m['segments']} segments with z"]
    for agent in MOVERS:
        casts = {k[1]: v for k, v in sorted(m["casts"].items()) if k[0] == agent}
        out.append(f"  {agent}: {m['agent_rounds'][agent]} rounds; casts recorded: {casts or 'none'}")
    for who in ("movers", "others"):
        if m["speed"][who]:
            sp, up = np.concatenate(m["speed"][who]), np.concatenate(m["up"][who])
            out.append(f"  {who}: along the ground m/s, pct {PCT}: {np.percentile(sp, PCT).round(1).tolist()} "
                       f"(BURST_MPS {hc.BURST_MPS:g})")
            out.append(f"  {who}: upward m/s, pct {PCT}: {np.percentile(up, PCT).round(1).tolist()} "
                       f"(BURST_UP_MPS {hc.BURST_UP_MPS:g})")
    if m["acc"]:
        acc = np.concatenate(m["acc"])
        hist, _ = np.histogram(acc, ACC_BINS)
        out.append(f"  |vertical acceleration| over {hc.WALK_S:g} s while moving, {len(acc)} windows "
                   f"(WALK_ACC_MAX {hc.WALK_ACC_MAX:g} m/s2):")
        out += [f"    {lo:>4g} .. {hi:<4g} {count / len(acc):7.2%}" for count, lo, hi in zip(hist, ACC_BINS, ACC_BINS[1:])]
    if m["moving"]:
        out.append(f"  while moving: {m['ground'] / m['moving']:.1%} of samples are a walk's, {m['air'] / m['moving']:.1%} "
                   f"are in a flight; {len(m['flights'])} flights"
                   + (f", lasting (s) pct 10/50/90: {np.percentile(m['flights'], [10, 50, 90]).round(2).tolist()}"
                      if m["flights"] else ""))
    if m["gravity"]:
        out.append(f"  gravity read off the {len(m['gravity'])} flights of {hc.WALK_S:g} s or more, m/s2 pct 10/50/90: "
                   f"{np.percentile(m['gravity'], [10, 50, 90]).round(1).tolist()} (GRAVITY_MPS2 {hc.GRAVITY_MPS2:g})")
    if rect is not None:
        slopes = np.concatenate(m["rect_slopes"]) if m["rect_slopes"] else np.zeros(0)
        share = m["rect_walk"] / m["rect_windows"] if m["rect_windows"] else 0.0
        out.append(f"  cells {rect}: {m['rect_windows']} moving samples, {share:.1%} are a walk's"
                   + (f"; their slope, pct 50/90/99: {np.percentile(slopes, [50, 90, 99]).round(2).tolist()}"
                      if len(slopes) else ""))
    steep = sorted(m["steep"].items(), key=lambda kv: -len(kv[1]))[:15]
    out.append(f"  steady descent candidates steeper than SLOPE_MAX going down, by cell (x, y, passes): "
               f"{[(c % cg.GRID, c // cg.GRID, len(v)) for c, v in steep]}")
    return out


def main(argv: list[str] | None = None, session_factory=None) -> int:
    import build_control_heights as command

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", required=True)
    parser.add_argument("--blobs-dir", type=Path, help="local round blobs, <match>/<n>.json.gz (default: the database)")
    parser.add_argument("--rect", help="x0,y0,x1,y1 in cells, inclusive: the walk rule's pass rate inside it")
    args = parser.parse_args(argv)
    rect = tuple(int(v) for v in args.rect.split(",")) if args.rect else None
    if rect is not None and len(rect) != 4:
        parser.error("--rect takes x0,y0,x1,y1")
    geo = cg.load_geometry(args.map)
    rounds, _ = command.blob_rounds(args.blobs_dir, args.map) if args.blobs_dir \
        else command.db_rounds(args.map, session_factory)
    for line in lines(args.map, measure(rounds, geo, rect), rect):
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: A preview records what it read**

In `webapp/scripts/build_control_heights.py`:

Add `import hashlib` to the standard-library imports and, after them, `import numpy as np`.

Replace `index_entry` with:

```python
def round_identity(rows: list) -> dict:
    """{"rounds", "matches", "rounds_sha"} for [[match, round number, sha256 of the stored blob], ...]: the digest
    two builds share exactly when they read the same rounds with the same bytes."""
    rows = sorted([str(match), int(n), str(sha)] for match, n, sha in rows)
    text = json.dumps(rows, separators=(",", ":"))
    return {"rounds": len(rows), "matches": len({row[0] for row in rows}),
            "rounds_sha": hashlib.sha256(text.encode("ascii")).hexdigest()[:16]}


def build_inputs(args, geo, rounds: list) -> dict:
    """What this build read, recorded beside a preview so two builds can be compared
    (scripts/compare_height_builds.py): the rounds (their identity only from `--blobs-dir`: a database is not
    frozen, so `rounds_sha` is None there), the masks, and the preview override. With a `<Map>.frozen.json`
    in the folder (scripts/freeze_height_rounds.py), the folder must still be what was frozen."""
    masks = {"sight_sha": hashlib.sha256(np.packbits(geo.sight).tobytes()).hexdigest()[:12],
             "walk_sha": hashlib.sha256(np.packbits(geo.walk_px).tobytes()).hexdigest()[:12],
             "preview_min_matches": args.preview_min_matches}
    if not args.blobs_dir:
        return {"rounds": len(rounds), "matches": len({str(match) for match, _, _ in rounds}), "rounds_sha": None,
                **masks}
    rows = [[match, n, hashlib.sha256((args.blobs_dir / str(match) / f"{n}.json.gz").read_bytes()).hexdigest()]
            for match, n, _ in rounds]
    identity = round_identity(rows)
    frozen = args.blobs_dir / f"{args.map}.frozen.json"
    if frozen.is_file():
        was = json.loads(frozen.read_text(encoding="utf-8")).get("rounds_sha")
        if was != identity["rounds_sha"]:
            raise ValueError(f"{args.blobs_dir} is not what was frozen: {frozen.name} says rounds {was}, the folder "
                             f"holds {identity['rounds_sha']}")
    return {**identity, **masks}


def index_entry(build: hb.HeightBuild, inputs: dict | None = None) -> dict:
    """What a built map adds to its index.json entry (build_control_geometry.py keeps these keys); a preview's
    wrapper also says what it read (`inputs`)."""
    entry = {"height_sha": build.asset.digest, "height": build.report}
    if inputs is not None:
        entry["inputs"] = inputs
    return entry
```

In `main`, directly after the line that prints how many rounds were read:

```python
    try:
        inputs = build_inputs(args, geo, rounds)
    except ValueError as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2
```

and the preview's report is written with them: `json.dumps(index_entry(build, inputs), indent=1)` (the committed
path further down keeps `index_entry(build)`: index.json's entry doesn't change).

Add to the module docstring's `--preview` paragraph: "The report beside a preview says what the build read
(`inputs`: the rounds' digest when they came from `--blobs-dir`, the sight and walk masks, the preview override),
so `compare_height_builds.py` can refuse to compare two builds of different rounds."

- [ ] **Step 5: The freeze and compare scripts**

Create `webapp/scripts/freeze_height_rounds.py`:

```python
"""Freezes a map's stored rounds into a local folder, so two height builds can be shown to have read the same
thing (docs/superpowers/specs/2026-10-05-height-slopes-design.md, "How it is judged").

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\freeze_height_rounds.py --map Sunset --out %TEMP%\\valo-replay\\heights-frozen

It only reads the database (always through `with_friends_db.py --read-only`) and writes under `--out`, which
must be outside the repository: every round of the map at condenser revision 11 or later as its stored bytes,
`<out>/<match>/<n>.json.gz` (the layout `build_control_heights.py --blobs-dir` reads), and
`<out>/<Map>.frozen.json`: when it was frozen and each round's match, number and sha256, with their digest
(`rounds_sha`). A build from that folder records the same digest; `compare_height_builds.py` refuses two builds
whose digests differ. Several maps can share one folder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

MIN_REVISION = 11


def freeze(session, map_name: str, out: Path, min_revision: int = MIN_REVISION) -> dict:
    """Writes the map's rounds under `out` and returns the manifest it wrote beside them."""
    import build_control_heights as command
    from app.models.replay import Replay, ReplayRound
    from app.services.replay_control import condense_revision

    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for replay in session.query(Replay).filter(Replay.map_name == map_name).order_by(Replay.id):
        if (condense_revision(replay.recipe) or 0) < min_revision:
            continue
        match = str(replay.match_uuid).lower()
        for row in session.query(ReplayRound).filter(ReplayRound.replay_id == replay.id) \
                .order_by(ReplayRound.round_number):
            data = bytes(row.data)
            (out / match).mkdir(parents=True, exist_ok=True)
            (out / match / f"{row.round_number}.json.gz").write_bytes(data)
            rows.append([match, int(row.round_number), hashlib.sha256(data).hexdigest()])
    manifest = {"map": map_name, "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                **command.round_identity(rows), "round_list": sorted(rows)}
    (out / f"{map_name}.frozen.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None, session_factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", required=True)
    parser.add_argument("--out", type=Path, required=True, help="the folder to write to, outside the repository")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.is_relative_to(WEBAPP_ROOT.parent.resolve()) or any((f / ".git").exists() for f in (out, *out.parents)):
        print(f"REFUSED: --out {args.out} is inside a repository; stored rounds are never written into one",
              file=sys.stderr)
        return 2
    if session_factory is None:
        from app.db import SessionLocal as session_factory
    session = session_factory()
    try:
        manifest = freeze(session, args.map, out)
    finally:
        session.rollback()
        session.close()
    print(f"{args.map}: {manifest['rounds']} rounds of {manifest['matches']} matches frozen in {out} "
          f"(rounds {manifest['rounds_sha']})", flush=True)
    return 0 if manifest["rounds"] else 2


if __name__ == "__main__":
    sys.exit(main())
```

Create `webapp/scripts/compare_height_builds.py`:

```python
"""Two height builds of the same maps side by side: how a change of rules is judged
(docs/superpowers/specs/2026-10-05-height-slopes-design.md, "How it is judged").

    .\\.venv\\Scripts\\python.exe scripts\\compare_height_builds.py --old <dir> --new <dir> [--map Sunset]

`--old` and `--new` are folders written by `build_control_heights.py --preview --out <dir>` (a
`<Map>.height.npz` and its `<Map>.height.json` each), built under the two sets of rules **from the same frozen
rounds** (`freeze_height_rounds.py`, then `--blobs-dir`). It reads the files as they are, whatever height
version wrote them, and writes nothing.

A comparison is refused (exit 2, with the reasons) unless both builds say what they read and it is the same:
the same rounds (their identities and blob hashes, as one digest), the same sight and walk masks, the same
preview override; and unless both carry both checks' results. Two builds of different rounds differ for
reasons that have nothing to do with the rules.

For each map it prints:

- each build's supported share, unresolved cells by reason, cells per kind, connections, kill lines blocked
  and must-block result, and whether it is ready;
- **the cells the old build supported that the new one gives no height**, on their own line: they are not in
  the next figure;
- **the flat-ground check**: over the cells supported in the old build that still have a height, the new
  ground minus the old, in metres: the share within FLAT_TOL_M and its percentiles. A wide shift means "the
  lowest wins" is biting on level ground, and LOW_PCT has to move up;
- the cells that gained a height.

Exits 0, or 1 when any map's kill-line share got worse, or 2 when a comparison was refused or no map is in
both folders.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

FLAT_TOL_M = 0.2
PCT = [1, 5, 25, 50, 75, 95, 99]
SAME = ("rounds_sha", "rounds", "matches", "sight_sha", "walk_sha", "preview_min_matches")


def read(folder: Path, name: str) -> dict:
    """One build as plain arrays: ground in world dm (NaN for none), supported, unresolved, its report and what
    it says it read (`inputs`, None when the build didn't record it)."""
    with np.load(folder / f"{name}.height.npz") as z:
        meta = json.loads(bytes(z["meta"]).decode("utf-8"))
        floors = z["floors"].astype(float)
        ground = np.where(floors[..., 0] >= 0, floors[..., 0] + meta.get("origin_z", 0), np.nan)
        out = {"ground": ground, "supported": z["supported"].astype(bool), "unresolved": z["unresolved"].astype(bool),
               "version": meta.get("version"), "edges": int(len(z["edges"]))}
    path = folder / f"{name}.height.json"
    wrapper = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    out["report"], out["inputs"] = wrapper.get("height") or {}, wrapper.get("inputs")
    return out


def refusals(old: dict, new: dict) -> list[str]:
    """Why the two builds can't be compared; empty when they can."""
    why = []
    for label, build in (("old", old), ("new", new)):
        inputs = build["inputs"]
        if not isinstance(inputs, dict) or not inputs.get("rounds_sha"):
            why.append(f"the {label} build doesn't say which rounds it read (build it with --blobs-dir on the "
                       f"frozen folder)")
        for check in ("kill_lines", "must_block"):
            if not isinstance(build["report"].get(check), dict) or "passes" not in build["report"][check]:
                why.append(f"the {label} build has no {check} result")
    if not why:
        for key in SAME:
            if old["inputs"].get(key) != new["inputs"].get(key):
                why.append(f"{key} differs: old {old['inputs'].get(key)!r}, new {new['inputs'].get(key)!r}")
    return why


def flat_ground(old: dict, new: dict) -> dict:
    """The new ground minus the old (m) over the old build's supported cells that still have a height, and how
    many of the old build's supported cells have none now."""
    was = old["supported"] & ~np.isnan(old["ground"])
    both = was & ~np.isnan(new["ground"])
    diff = (new["ground"][both] - old["ground"][both]) / 10.0
    out = {"cells": int(len(diff)), "lost": int((was & np.isnan(new["ground"])).sum()), "within": None, "pct": []}
    if len(diff):
        out.update({"within": float((np.abs(diff) <= FLAT_TOL_M + 1e-9).mean()),
                    "pct": np.percentile(diff, PCT).round(2).tolist()})
    return out


def summary(build: dict) -> str:
    r = build["report"]
    kills, must = r.get("kill_lines") or {}, r.get("must_block") or {}
    kinds = r.get("cells_by_kind")
    parts = [f"v{build['version']}", f"supported {r.get('supported', float('nan')):.1%}",
             f"unresolved {r.get('unresolved_cells')} {r.get('unresolved_why')}",
             f"kinds {kinds}" if kinds else f"filled {r.get('filled_cells')}",
             f"connections {build['edges']} ({r.get('one_way_edges')} one-way)",
             f"kill lines {kills.get('blocked')}/{kills.get('qualifying')} ({kills.get('share', 0):.2%})",
             f"must-block {must.get('blocked')}/{must.get('checked')} of {must.get('lines')}",
             "READY" if r.get("ready") else "not ready: " + "; ".join(r.get("not_ready") or [])]
    return "; ".join(parts)


def compare(name: str, old: dict, new: dict) -> tuple[list[str], bool]:
    """(the printed lines, whether the kill-line share got worse). Call `refusals` first."""
    flat = flat_ground(old, new)
    had, has = ~np.isnan(old["ground"]), ~np.isnan(new["ground"])
    inputs = new["inputs"]
    out = [f"{name}: {inputs['rounds']} rounds of {inputs['matches']} matches (rounds {inputs['rounds_sha']}, walk mask "
           f"{inputs['walk_sha']}" + (f", floors from {inputs['preview_min_matches']} match(es)"
                                      if inputs.get("preview_min_matches") else "") + ")",
           f"  old: {summary(old)}", f"  new: {summary(new)}",
           f"  supported before, no height now: {flat['lost']} cells"]
    if flat["cells"]:
        out.append(f"  flat ground: {flat['cells']} cells supported before that still have a height, "
                   f"{flat['within']:.1%} within {FLAT_TOL_M} m; new - old, pct {PCT}: {flat['pct']}")
    else:
        out.append("  flat ground: no cell supported in the old build has a height in the new one")
    out.append(f"  heights gained {int((has & ~had).sum())} cells, lost {int((had & ~has).sum())} in all")
    was, now = old["report"]["kill_lines"].get("share"), new["report"]["kill_lines"].get("share")
    worse = was is not None and now is not None and now > was
    if worse:
        out.append(f"  WORSE: kill lines blocked went from {was:.2%} to {now:.2%}")
    return out, worse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--map", action="append", help="only this map (repeatable)")
    args = parser.parse_args(argv)
    names = sorted({p.name[: -len(".height.npz")] for p in args.old.glob("*.height.npz")}
                   & {p.name[: -len(".height.npz")] for p in args.new.glob("*.height.npz")})
    names = [n for n in names if not args.map or n in args.map]
    if not names:
        print(f"no map has a build in both {args.old} and {args.new}", flush=True)
        return 2
    code = 0
    for name in names:
        old, new = read(args.old, name), read(args.new, name)
        why = refusals(old, new)
        if why:
            print(f"{name}: REFUSED, not comparable", flush=True)
            for reason in why:
                print(f"  {reason}", flush=True)
            code = 2
            continue
        lines, worse = compare(name, old, new)
        if worse and code == 0:
            code = 1
        for line in lines:
            print(line, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests**

Run: `PY -m pytest tests/replays/test_height_motion.py tests/replays/test_compare_height_builds.py tests/replays/test_control_heights.py tests/replays/test_height_viewer.py -q -p no:cacheprovider`
Expected: 19 passed in `test_height_motion.py`, 5 in `test_compare_height_builds.py`, and the two existing files unchanged (the viewer reads a preview's report through `usable_report`, which ignores the extra `inputs` key).

- [ ] **Step 7: Freeze the five maps' rounds, once**

From `webapp/`, for each of `Sunset`, `Haven`, `Lotus`, `Ascent`, `Summit`, into one folder outside the repository:

```
PY scripts\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\freeze_height_rounds.py --map Sunset --out %TEMP%\valo-replay\heights-frozen
```

This is the only step of this plan that reads the live database. If the session isn't allowed to, give the owner
the five lines and wait. Expected: one line per map with its rounds, matches and `rounds` digest, a
`<Map>.frozen.json` each, and the rounds under `<match>\<n>.json.gz`. Write the five digests down: they go in
the spec in Step 10 and identify the dataset every later number in this plan comes from.

Do not re-freeze later "to pick up new matches": Task 6 compares against builds of this copy.

- [ ] **Step 8: Measure the five maps from the frozen copy**

```
PY scripts\measure_height_motion.py --map Sunset --blobs-dir %TEMP%\valo-replay\heights-frozen --rect 26,76,28,81
```

and the same without `--rect` for the other four. Read each map's output against this table. Any "stop" row
means: write down the numbers, tell the owner, and do not start Task 3 until they answer.

| Line | Carry on when | Stop when |
| --- | --- | --- |
| `hz` | every round is 100 Hz or more | any round under 60 Hz (the flight rule's 0.1 s windows hold too few samples; the spec's fallback, slope alone, is a change to the spec) |
| acceleration histogram | under 10% of windows fall in 2 .. 8 m/s2 (two separate groups; the prototype saw 8.3%) | the two groups run into each other |
| `gravity read off the ... flights` | its 90th percentile is between 16 and 24 m/s2 | it isn't: `GRAVITY_MPS2` is wrong for this game and every flight is followed along the wrong arc. Report the three percentiles |
| `while moving: ... are in a flight` | between 2% and 15% of moving samples (the prototype saw 7.4% on Sunset) | far outside it: the detector is finding flights that aren't there, or missing them |
| `others: along the ground`, 99.9th percentile | under `BURST_MPS` | at or over it (ordinary running would start blackouts) |
| `others: upward`, 99.9th percentile | under `BURST_UP_MPS` | at or over it (ordinary jumps would) |
| `Raze: ... casts recorded` | `Clay_Q_Explosion` is listed, or there is no Raze round | a Raze played and it isn't (drop it from `AIRBORNE_ABILITIES`, which changes nothing: the burst rule covers her) |
| Sunset `cells (26, 76, 28, 81)` | most moving samples are a walk's (the prototype saw 80%) and their 99th-percentile slope is under `SLOPE_MAX` | under half are |
| `steady descent candidates steeper than SLOPE_MAX going down` (Lotus, Summit) | no cell has many passes | a cluster of cells with several passes each: inspect the tracks for a slide the rules would read as a fall (Review Focus 1); low acceleration alone does not prove ground contact. Report its cells and what inspection shows |

- [ ] **Step 9: Build every map under the old rules, from the frozen copy**

The build still uses the old rules at this commit (nothing calls `walks` or `blackouts` yet).

```
PY scripts\build_control_heights.py --map Sunset --blobs-dir %TEMP%\valo-replay\heights-frozen --preview --out %TEMP%\valo-replay\heights-preview-old
```

and the same for `Haven`, `Lotus`, `Ascent` and `Summit`. For a map with one match add `--preview-min-matches 1`,
and write down which maps got it: the override is recorded in each build's `inputs`, and Task 6 must build those
maps the same way or its comparison is refused.
Expected: five `<Map>.height.npz` and five `<Map>.height.json` in that folder, each `.json` with an `inputs`
whose `rounds_sha` is the map's digest from Step 7.

- [ ] **Step 10: Record it in the spec**

In `docs/superpowers/specs/2026-10-05-height-slopes-design.md`:

- add `## Measured (<date>)` before `## Out of scope`: the five `rounds` digests and round counts from Step 7
  (the dataset), each map's printed output from Step 8 in a code block, one sentence per row of the Step 8 table
  saying what was found, and each map's old-rule report line from Step 9;
- replace open questions 1 and 2 with their answers (no cast was recorded for the updraft or the dashes; the owner
  decided on 2026-10-07 to record them, decision D1 and Task 1b of the plan, with what Task 1b found; rounds
  condensed before that are told by speed; the rate is 125 Hz);
  change section 1b's "Not yet verified" paragraph to say what was found;
- in section 1, replace "The rule is two limits over the run" with the rule as built (decision D2: flights are
  found first, then the two limits), and say why in one sentence (a takeoff or landing cancels a fall's
  curvature in one fitted parabola);
- in "How it is judged", step 1, add: "Both builds are made from one frozen copy of the rounds
  (`scripts/freeze_height_rounds.py`); each records the rounds' digest, and `scripts/compare_height_builds.py`
  refuses two builds whose digests, masks or preview overrides differ."

- [ ] **Step 11: Commit**

```bash
git add webapp/scripts/measure_height_motion.py webapp/scripts/freeze_height_rounds.py webapp/scripts/compare_height_builds.py webapp/scripts/build_control_heights.py webapp/tests/replays/test_height_motion.py webapp/tests/replays/test_compare_height_builds.py docs/superpowers/specs/2026-10-05-height-slopes-design.md
git commit -F <message file>
```

Message: `Heights: freeze the rounds, measure them, and compare two builds only when they read the same`.

---

### Task 3: The build: floors from walks and the low end, gradient fill, slides and falls

**Files:**
- Modify: `webapp/app/control/heights.py` (`HEIGHT_VERSION`, module docstring, `HeightAsset`, `save_asset`, `load_asset`)
- Modify: `webapp/app/control/height_build.py` (module docstring, `off_platforms`, everything from `group_cell` to `connect`, `air_only`, `build`, `report_lines`)
- Modify: `webapp/app/control/topology.py` (`NodeTopology.__init__` reads five columns)
- Modify: `webapp/tests/replays/control_toys.py` (`toy_heights`)
- Modify: `webapp/tests/replays/test_control_heights.py`
- Modify: `webapp/tests/replays/test_control_format.py` (re-pin 7: `HEIGHT_VERSION` is one of the pinned constants)

**Interfaces:**
- Consumes: Task 1's `height_motion` (`Walk`, `blackouts`, `walks`, `cells_of`, `tracks`) and constants.
- Produces:
  - `heights.HEIGHT_VERSION == 2`; `HeightAsset(floors, spread, supported, unresolved, edges, meta={}, kind=None)` with `edges` K x 5 int32 (`cell a, floor a, cell b, floor b, EDGE_*`) and `kind` GRID x GRID int8 (`KIND_*`; told from `supported` when not given). `digest` covers `floors`, `unresolved` and all five edge columns, not `kind`.
  - `height_build._levels(z, rounds, matches) -> list[np.ndarray]` (the supported levels, as masks); `height_build.stand_groups(z, rounds, matches) -> list[float]`.
  - `height_build.group_cell(z, rounds, matches, stand=None) -> (list[(height, spread)], reason | None, ground KIND_*)`.
  - `height_build.cell_floors(found, round_match, geo, found_walks=()) -> (floors, reasons, kinds)`.
  - `height_build.all_ground(rounds, geo) -> (stands, walks, round_match, counts)`; it reads `rounds` once, so `rounds` may be a generator. `all_stands(rounds, geo) -> (stands, round_match, counts)` is kept.
  - `height_build.off_platforms(found, plats, geo)`: stands or walks; a run that loses cells in its middle comes back as separate runs.
  - `height_build.crossings(runs) -> dict[(a, c, b), list[(round, z in a, z in c, z in b)]]`; `fill_gradient(floors, why, crossed, geo) -> ({cell: height}, {(from cell, to cell)})`.
  - `height_build.walked(found, heights, found_walks=(), dt=None) -> (seen, ground)`; `connect(heights, seen, geo, ground=None, granted=frozenset()) -> K x 5 array`.
  - `HeightBuild.walks: list[Walk]`; report keys `walks`, `blackouts`, `cells_by_kind` (`stands, walks, filled, gradient`), `slides`, `falls`, `falls_closed`; `filled_cells` counts both kinds of fill.
  - `control_toys.toy_heights(..., links=[((x, y, floor), (x, y, floor), one_way[, EDGE_*]), ...])`: a one-way link is a fall unless its fourth element says otherwise.

- [ ] **Step 1: Update the existing tests to the new shapes**

In `webapp/tests/replays/test_control_heights.py`:

Imports: add `from app.control import height_motion as hm` after the `height_build` import, and import `fast_rounds` and `stair_run` from `tests.replays.control_toys` (keep the list sorted):

```python
from tests.replays.control_toys import (HZ, TOY_Z0, fast_rounds, height_blob, height_rounds, open_hall, stair_run,
                                        standing, toy_ability, z_dm)
```

`floors_at` reads walks too:

```python
def floors_at(rounds, cell=CELL, geo=GEO):
    found, found_walks, round_match, _ = hb.all_ground(rounds, geo)
    floors, reasons, _ = hb.cell_floors(found, round_match, geo, found_walks)
    return floors.get(cell), reasons.get(cell)
```

Replace `test_group_cell_takes_the_densest_group_first` with:

```python
def test_a_stray_stand_between_two_floors_is_nobodys_floor():
    z = np.array([0, 0, 1, 0, 0, 1, 50, 50, 51, 50, 50, 49, 25.0])
    rounds = np.array([0, 1, 2, 3, 4, 5, 0, 1, 2, 3, 4, 5, 0])
    floors, why, kind = hb.group_cell(z, rounds, rounds // 3)
    assert why is None and [h for h, _ in floors] == [0, 50] and kind == hc.KIND_STANDS, \
        "the single stand at 2.5 m is a band of its own, too thin to be a floor; a floor is its low end"
```

`step_then` moves in a quarter of a second, so it stays a jump and not a walk up a slope (at 0.5 s it passes the
new walk rule, and the cell's floor becomes the slope's low end):

```python
def step_then(x0: int, x1: int, z0: float, z1: float, t_move: float = 5.0, side: str = "A"):
    """Stands at x0 (height z0), then is at x1 (height z1) a quarter of a second later: a jump up or a drop,
    too abrupt to be a walk."""
    return side, [(0.0, x0 + 4, Y, 0, z0), (t_move, x0 + 4, Y, 0, z0), (t_move + 0.25, x1 + 4, Y, 0, z1),
                  (10.0, x1 + 4, Y, 0, z1)]
```

`edges_of` compares the first four columns:

```python
def edges_of(b):
    return {tuple(e[:4]) for e in b.asset.edges.tolist()}
```

In `test_neighbouring_floors_within_a_step_connect_both_ways_diagonals_too`, replace the last line with:

```python
    assert all(len(row) == 5 and row[4] == hc.EDGE_STEP for row in b.asset.edges.tolist())
    assert b.report["edges"] == len(e)
```

`sweep` ran the hall at 15.6 m/s, which is now a dash. Slow it to 5 m/s over a 30 s round. Replace `sweep` with:

```python
SWEEP_S = 30.0     # a sweep's round: 44 m a leg at 5 m/s (a faster one reads as a dash)


def sweep(k: int, z_m: float = 0.0):
    """Player k walks three rows of the hall, end to end, at one height (rows 12 + 3k .. 14 + 3k)."""
    y = 100 + 24 * k
    pts = [(0.0, 100, y), (8.4, 412, y), (9.0, 412, y + 8), (17.4, 100, y + 8), (18.0, 100, y + 16), (26.4, 412, y + 16)]
    return "A" if k < 5 else "B", [(t, x, yy, 0, z_m) for t, x, yy in pts] + [(SWEEP_S, 412, y + 16, 0, z_m)]
```

and give every round that uses `sweep` that length. There are six call sites; each gains `t_end=SWEEP_S`:

| Where | Before | After |
| --- | --- | --- |
| `test_a_floor_reached_only_through_the_air_is_listed` | `b = built(players)` | `b = built(players, t_end=SWEEP_S)` |
| `test_separate_unresolved_areas_are_listed_largest_first` | `b = built(players)` | `b = built(players, t_end=SWEEP_S)` |
| `test_the_bar_refuses_a_thin_map_and_passes_a_covered_one` | `covered = built(lambda m, n: {k: sweep(k) for k in range(9)})` | `covered = built(lambda m, n: {k: sweep(k) for k in range(9)}, t_end=SWEEP_S)` |
| `test_the_report_gives_visited_and_supported_separately` | `r = built(players).report` | `r = built(players, t_end=SWEEP_S).report` |
| `covered_rounds` | `height_rounds(lambda m, n: {...})` | the same call with `, t_end=SWEEP_S` |
| `test_only_a_preview_may_make_floors_from_one_match` | `height_rounds(lambda m, n: {k: sweep(k) for k in range(9)}, matches=1, rounds=6)` | the same call with `, t_end=SWEEP_S` |

`sweep` is defined after two of the tests that use it; `SWEEP_S` is a module constant, so that is fine at call time.

- [ ] **Step 2: Add the new tests**

Append to `webapp/tests/replays/test_control_heights.py`:

```python
# ---------------------------------------------------------------- slopes: bands, walks, gradient fill, slides
# docs/superpowers/specs/2026-10-05-height-slopes-design.md, parts 2 to 5. These rounds are stored at 125 Hz
# (control_toys.fast_blob), the rate the walk rules are tuned on.


def test_band_rules():
    r6 = np.array([0, 1, 2, 3, 4, 5])
    m6, r12, m12 = r6 // 3, np.r_[r6, r6], np.r_[r6 // 3, r6 // 3]
    no, yes = np.zeros(6, bool), np.ones(6, bool)
    # walks alone make the ground floor, at the low end (never the single lowest of several)
    assert hb.group_cell(np.array([20, 22, 24, 26, 28, 30.0]), r6, m6, no) == ([(22, 6)], None, hc.KIND_WALKS)
    two = np.r_[np.zeros(6), np.full(6, 40.0)]
    # an upper band of walks only (a boost passing over) is nobody's floor
    assert hb.group_cell(two, r12, m12, np.r_[yes, no]) == ([(0, 0)], None, hc.KIND_STANDS)
    # with stands it is a second floor
    assert hb.group_cell(two, r12, m12, np.r_[yes, yes]) == ([(0, 0), (40, 0)], None, hc.KIND_STANDS)
    # a tunnel only ever run through, under a bridge people stand on
    assert hb.group_cell(two, r12, m12, np.r_[no, yes]) == ([(0, 0), (40, 0)], None, hc.KIND_WALKS)
    close = np.r_[np.zeros(6), np.full(6, 12.0)]
    # two levels of stands 1.2 m apart: too close to tell apart
    assert hb.group_cell(close, r12, m12, np.r_[yes, yes]) == ([], hb.TOO_CLOSE, hc.KIND_NONE)
    # a box people only run over: one floor, and the lower wins
    assert hb.group_cell(close, r12, m12, np.r_[yes, no]) == ([(0, 12)], None, hc.KIND_STANDS)
    # one match is never a floor
    assert hb.group_cell(np.zeros(6), r6, np.zeros(6, int), no) == ([], None, hc.KIND_NONE)
    # one stray stand 1.9 m under a floor joins its band and moves nothing: not the height, not the spread
    z = np.array([0, 0, 1, 0, 0, 1, 50, 50, 51, 50, 50, 49, 31.0])
    rr = np.array([0, 1, 2, 3, 4, 5, 0, 1, 2, 3, 4, 5, 0])
    floors, why, _ = hb.group_cell(z, rr, rr // 3)
    assert why is None and floors == [(0, 1), (50, 0)], "the stray is nobody's: it isn't in the upper band at all"
    # a level of stands alone that spreads too wide is still refused (a steep ramp people stop on)
    assert hb.group_cell(np.r_[np.zeros(6), np.full(6, 9.0)], r12, m12) == ([], hb.SPREAD, hc.KIND_NONE)
    # stands scattered over 1.8 m with no level among them, and no walk: nothing is a floor, as before
    assert hb.group_cell(np.array([0, 4, 8, 12, 16, 19.0]), r6, m6) == ([], None, hc.KIND_NONE)
    # four bands of stands are refused, never cut down
    stack = np.repeat([0, 30, 60, 90.0], 6)
    assert hb.group_cell(stack, np.tile(r6, 4), np.tile(m6, 4))[1] == hb.TOO_MANY


def test_stairs_nobody_stops_on_get_floors_from_walks():
    # Two players a round run the stairs (rows 25 and 26); a player stands at each end of row 25.
    def players(m, n):
        return {0: stair_run(), 1: stair_run(y=Y + 8), 2: standing(132, Y, 0.0), 3: standing(252, Y, 4.0, "B")}

    b = hb.build(fast_rounds(players), GEO)
    kind = b.asset.kind
    for c in range(21, 27):
        assert kind[ROW, c] == hc.KIND_WALKS and b.asset.supported[ROW, c], c
        assert abs(int(b.asset.floors[ROW, c, 0]) / 10 - (c * 8 - 160) / 64 * 4.0) <= 0.11, c
    assert kind[ROW, 16] == hc.KIND_STANDS and kind[ROW, 31] == hc.KIND_STANDS
    assert b.report["cells_by_kind"]["walks"] >= 12 and b.report["walks"] > 0 and len(b.walks) == b.report["walks"]
    e = {tuple(row[:4]): row[4] for row in b.asset.edges.tolist()}
    for c in range(20, 27):       # half a metre a cell: steps, both ways, all the way up
        assert e[(cell(c), 0, cell(c + 1), 0)] == e[(cell(c + 1), 0, cell(c), 0)] == hc.EDGE_STEP


def test_a_player_after_a_dash_gives_no_floor_for_three_seconds():
    # Six rounds: a dash onto a spot 5 m up (a rooftop nobody can stand on), left again 2 s later.
    def players(m, n):
        dasher = ("A", [(0.0, 160, Y, 0, 0.0), (2.0, 160, Y, 0, 0.0), (2.3, 220, Y, 0, 5.0), (4.3, 220, Y, 0, 5.0),
                        (4.5, 240, Y, 0, 0.0), (10.0, 240, Y, 0, 0.0)])
        return {0: dasher, 1: standing(220, Y, 0.0), 2: standing(220, Y, 0.0, "B")}

    rounds = fast_rounds(players)
    floors, why = floors_at(rounds, cell=GEO.cell_of_px(220, Y))
    assert why is None and heights_m(floors) == [0.0], "the two seconds up there are inside the blackout"
    assert hb.all_ground(rounds, GEO)[3]["blackouts"] >= 6


def crosser(x0, x1, z0, z1, t=2.0, side="A"):
    """Stands at x0, runs to x1 in a second (z0 to z1), stands there."""
    return side, [(0.0, x0, Y, 0, z0), (t, x0, Y, 0, z0), (t + 1.0, x1, Y, 0, z1), (10.0, x1, Y, 0, z1)]


def gradient_rounds(rise, back=False):
    """Columns 23-24 stood on at 0 m and 26-27 at `rise`; column 25 crossed by one player, once in the whole
    build (too few passes for a floor of its own)."""
    def players(m, n):
        out = {0: standing(188, Y, 0.0), 1: standing(196, Y, 0.0), 2: standing(212, Y, rise), 3: standing(220, Y, rise)}
        if (m, n) == (0, 1):
            out[5] = crosser(220, 188, rise, 0.0, side="B") if back else crosser(188, 220, 0.0, rise, side="B")
        return out
    return fast_rounds(players)


def test_a_crossed_slope_is_filled_along_the_gradient_and_a_ledge_is_not():
    b = hb.build(gradient_rounds(1.2), GEO)
    assert b.asset.kind[ROW, 25] == hc.KIND_GRADIENT and int(b.asset.floors[ROW, 25, 0]) == 6
    assert not b.asset.supported[ROW, 25] and not b.asset.unresolved[ROW, 25]
    assert b.report["cells_by_kind"]["gradient"] >= 1
    # the same ground with nobody crossing it: a ledge for all the build knows
    ledge = hb.build(fast_rounds(lambda m, n: {0: standing(188, Y, 0.0), 1: standing(196, Y, 0.0),
                                               2: standing(212, Y, 1.2), 3: standing(220, Y, 1.2)}), GEO)
    assert ledge.asset.unresolved[ROW, 25] and ledge.reasons[cell(25)] == hb.NEIGHBOURS_DISAGREE
    assert ledge.asset.kind[ROW, 25] == hc.KIND_NONE


def test_a_gradient_cell_connects_the_way_it_was_crossed():
    up = hb.build(gradient_rounds(1.8), GEO)
    e = {tuple(row[:4]): row[4] for row in up.asset.edges.tolist()}
    assert int(up.asset.floors[ROW, 25, 0]) == 9, "0.9 m: more than a step from either side"
    for a, b in ((24, 25), (25, 26)):
        assert e[(cell(a), 0, cell(b), 0)] == e[(cell(b), 0, cell(a), 0)] == hc.EDGE_STEP, "climbed: both ways"
    down = hb.build(gradient_rounds(1.8, back=True), GEO)
    e = {tuple(row[:4]): row[4] for row in down.asset.edges.tolist()}
    for a, b in ((26, 25), (25, 24)):
        assert e[(cell(a), 0, cell(b), 0)] == hc.EDGE_SLIDE and (cell(b), 0, cell(a), 0) not in e, "only ever down"
    assert down.report["slides"] == 2 and down.report["falls"] == 0


def test_fill_gradient_rules():
    floors = {cell(24): [(z_dm(0.0), 0)], cell(26): [(z_dm(1.2), 0)]}
    why = {cell(25): hb.NEIGHBOURS_DISAGREE}
    up = hm.Walk(0, 0, 0.0, 1.0, (cell(23), cell(24), cell(25), cell(26)),
                 (z_dm(0.0), z_dm(0.0), z_dm(0.6), z_dm(1.2)))
    assert hb.fill_gradient(floors, why, hb.crossings([up]), GEO) == \
        ({cell(25): z_dm(0.6)}, {(cell(24), cell(25)), (cell(25), cell(26))})
    assert hb.fill_gradient(floors, why, {}, GEO) == ({}, set()), "nobody crossed"
    steep = {cell(24): [(z_dm(0.0), 0)], cell(26): [(z_dm(3.0), 0)]}
    assert hb.fill_gradient(steep, why, hb.crossings([up]), GEO) == ({}, set()), "3 m over 2.24 m"
    two = {cell(24): [(z_dm(0.0), 0), (z_dm(4.0), 0)], cell(26): [(z_dm(1.2), 0)]}
    assert hb.fill_gradient(two, why, hb.crossings([up]), GEO) == ({}, set()), "two floors never anchor a fill"
    cross = {**floors, cell(25, ROW - 1): [(z_dm(3.0), 0)], cell(25, ROW + 1): [(z_dm(4.0), 0)]}
    ns = hm.Walk(0, 1, 0.0, 1.0, (cell(25, ROW - 1), cell(25), cell(25, ROW + 1)),
                 (z_dm(3.0), z_dm(3.5), z_dm(4.0)))
    assert hb.fill_gradient(cross, why, hb.crossings([up, ns]), GEO) == ({}, set()), "the pairs disagree"
    assert hb.fill_gradient(floors, {cell(25): hb.NO_SAMPLES}, hb.crossings([up]), GEO) == ({}, set())
    edge = {0: [(z_dm(0.0), 0)], 2: [(z_dm(1.0), 0)]}        # cell 1 is on the map's top row: no pair runs off it
    over = hm.Walk(0, 0, 0.0, 1.0, (0, 1, 2), (z_dm(0.0), z_dm(0.5), z_dm(1.0)))
    assert hb.fill_gradient(edge, {1: hb.NEIGHBOURS_DISAGREE}, hb.crossings([over]), GEO)[0] == {1: z_dm(0.5)}


def test_a_drop_walked_down_is_a_slide_and_one_fallen_down_is_a_fall():
    # Columns 30-32: a ramp of 3 m over three cells (0.9 of rise per metre), only ever run down.
    # Column 40 at 3 m beside 41 at 0 m: only ever fallen from.
    def players(m, n):
        run_down = ("B", [(0.0, 240, Y, 0, 3.0), (2.0, 240, Y, 0, 3.0), (3.0, 264, Y, 0, 0.0), (10.0, 264, Y, 0, 0.0)])
        fall = ("B", [(0.0, 324, Y, 0, 3.0), (5.0, 324, Y, 0, 3.0), (5.25, 332, Y, 0, 0.0), (10.0, 332, Y, 0, 0.0)])
        return {0: standing(236, Y, 3.0), 1: standing(268, Y, 0.0), 5: run_down, 6: fall}

    b = hb.build(fast_rounds(players), GEO)
    assert [int(b.asset.floors[ROW, c, 0]) for c in (30, 31, 32)] == [20, 10, 0], "each cell's low end"
    e = {tuple(row[:4]): row[4] for row in b.asset.edges.tolist()}
    for a in (30, 31):
        assert e[(cell(a), 0, cell(a + 1), 0)] == hc.EDGE_SLIDE and (cell(a + 1), 0, cell(a), 0) not in e
    assert e[(cell(40), 0, cell(41), 0)] == hc.EDGE_FALL and (cell(41), 0, cell(40), 0) not in e
    assert (b.report["slides"], b.report["falls"], b.report["falls_closed"]) == (2, 1, 1)


def test_the_asset_keeps_kind_and_edge_kinds_and_only_the_edges_move_the_digest(tmp_path):
    b = hb.build(gradient_rounds(1.8, back=True), GEO)
    hc.save_asset(tmp_path / "Toy.height.npz", b.asset)
    back = hc.load_asset(tmp_path / "Toy.height.npz")
    assert back.kind.dtype == np.int8 and (back.kind == b.asset.kind).all() and back.edges.shape[1] == 5
    assert (back.edges == b.asset.edges).all() and back.digest == b.asset.digest
    assert set(np.unique(back.kind).tolist()) >= {hc.KIND_NONE, hc.KIND_STANDS, hc.KIND_GRADIENT}
    other = hc.HeightAsset(back.floors, back.spread, back.supported, back.unresolved, back.edges.copy(), back.meta,
                           back.kind)
    slide = np.flatnonzero(other.edges[:, 4] == hc.EDGE_SLIDE)[0]
    other.edges[slide, 4] = hc.EDGE_FALL
    assert other.digest != back.digest, "the engine reads a connection's kind"
    relabelled = hc.HeightAsset(back.floors, back.spread, back.supported, back.unresolved, back.edges, back.meta,
                                np.where(back.kind == hc.KIND_GRADIENT, hc.KIND_FILLED, back.kind).astype(np.int8))
    assert relabelled.digest == back.digest, "it never reads where a height came from"
    plain = hc.HeightAsset(back.floors, back.spread, back.supported, back.unresolved, back.edges, back.meta)
    assert set(np.unique(plain.kind).tolist()) <= {hc.KIND_NONE, hc.KIND_STANDS, hc.KIND_FILLED}, \
        "without a kind it is told from `supported`"


def test_a_stray_sample_between_two_floors_bridges_nothing():
    r6 = np.array([0, 1, 2, 3, 4, 5])
    two = np.r_[np.zeros(6), np.full(6, 30.0)]
    rr, mm, yes = np.r_[r6, r6, 0], np.r_[r6 // 3, r6 // 3, 0], np.ones(13, bool)
    assert hb.group_cell(two, rr[:12], mm[:12]) == ([(0, 0), (30, 0)], None, hc.KIND_STANDS)
    # one stand half way up (someone on a teammate's head), or one walk (a boost's way up): still two floors
    assert hb.group_cell(np.r_[two, 15.0], rr, mm) == ([(0, 0), (30, 0)], None, hc.KIND_STANDS)
    assert hb.group_cell(np.r_[two, 15.0], rr, mm, np.r_[yes[:12], False]) == ([(0, 0), (30, 0)], None, hc.KIND_STANDS)
    # four strays, still short of a level of their own
    strays = np.r_[two, [12.0, 14, 16, 18]]
    assert hb.group_cell(strays, np.r_[rr[:12], 0, 1, 2, 3], np.r_[mm[:12], 0, 0, 0, 1])[0] == [(0, 0), (30, 0)]
    # a supported level between them is evidence, not noise: the three can't be told apart
    level = np.r_[two, np.full(6, 15.0)]
    assert hb.group_cell(level, np.r_[r6, r6, r6], np.r_[r6 // 3, r6 // 3, r6 // 3])[1] == hb.TOO_CLOSE
    # so are stairs people walk from the ground to within 2 m of the upper floor (the spec's "what stays
    # unresolved, on purpose"): with stands on both levels the cell is refused, never guessed
    ramp = np.r_[two, np.full(6, 8.0), np.full(6, 14.0)]
    walked_up = np.r_[np.ones(12, bool), np.zeros(12, bool)]
    assert hb.group_cell(ramp, np.tile(r6, 4), np.tile(r6 // 3, 4), walked_up)[1] == hb.TOO_CLOSE
    only_run_over = np.r_[np.ones(6, bool), np.zeros(18, bool)]         # nobody stands on the upper level
    floors, why, _ = hb.group_cell(ramp, np.tile(r6, 4), np.tile(r6 // 3, 4), only_run_over)
    assert why is None and floors == [(0, 30)], "one band: its low end, spread over all of it"


LEDGE_MPS = 8.0        # fast enough that a 1 m fall clears a whole cell, slow enough not to be a dash


def ledge_run(height):
    """Six rounds: a player runs at 8 m/s off a ledge `height` up (it ends at x 200) and on along the low ground;
    one stands on top, one below."""
    air = (height / 10) ** 0.5
    px = LEDGE_MPS / GEO.m_per_px

    def z_of(t):
        return height if t < 1 else height - 10 * (t - 1) ** 2 if t < 1 + air else 0.0

    runner = ("B", [(i / 125, 200 - px + px * i / 125, Y, 0, z_of(i / 125)) for i in range(3 * 125 + 1)])
    return fast_rounds(lambda m, n: {0: standing(196, Y, height), 1: standing(228, Y, 0.0), 5: runner}, t_end=3.0)


@pytest.mark.parametrize("height", [0.9, 1.1])
def test_a_ledge_run_off_is_never_a_slope_or_a_slide(height):
    # Near SILENT_DROP_M the difference matters most: a fall read as a walk would fill the ledge in as a slope
    # and let the unknown down it.
    b = hb.build(ledge_run(height), GEO)
    landing = GEO.cell_of_px(200 + (height / 10) ** 0.5 * LEDGE_MPS / GEO.m_per_px, Y) % GRID
    # The level run on top is a stand, and a stand lasts until z has left it by STAND_TOL_M: a tenth of a second
    # past the edge, in cell 25. The cells after that one and before the landing are flown over.
    over = list(range(26, landing))
    assert over, "the fall crosses at least one whole cell"
    assert int(b.asset.floors[ROW, 24, 0]) == round(height * 10) and int(b.asset.floors[ROW, 28, 0]) == 0
    for c in over:
        assert b.asset.kind[ROW, c] == hc.KIND_NONE and b.asset.unresolved[ROW, c], (c, b.asset.kind[ROW, c])
        assert b.reasons[cell(c)] == hb.NEIGHBOURS_DISAGREE, "a ledge for all the build knows: never a height part way down"
    assert b.report["slides"] == 0 and b.report["cells_by_kind"]["gradient"] == 0
    e = {tuple(row[:4]): row[4] for row in b.asset.edges.tolist()}
    assert not any(kind == hc.EDGE_SLIDE for kind in e.values())


def test_a_fall_between_two_ramps_gives_no_floor_under_it_and_no_slide():
    air = (1.1 / 10) ** 0.5
    px = LEDGE_MPS / GEO.m_per_px

    def z_of(t):
        return 3.1 - 2 * t if t < 1 else 1.1 - 10 * (t - 1) ** 2 if t < 1 + air else 2 * (t - 1 - air)

    runner = ("B", [(i / 125, 160 + px * i / 125, Y, 0, z_of(i / 125)) for i in range(3 * 125 + 1)])
    b = hb.build(fast_rounds(lambda m, n: {5: runner}, t_end=3.0), GEO)
    takeoff, landing = GEO.cell_of_px(160 + px, Y) % GRID, GEO.cell_of_px(160 + px * (1 + air), Y) % GRID
    assert landing - takeoff >= 2
    for c in range(takeoff + 1, landing):
        assert b.asset.kind[ROW, c] == hc.KIND_NONE and b.asset.unresolved[ROW, c], c
    assert b.asset.kind[ROW, takeoff - 1] == hc.KIND_WALKS and b.asset.kind[ROW, landing + 1] == hc.KIND_WALKS
    assert b.report["slides"] == 0 and b.report["cells_by_kind"]["gradient"] == 0


def test_a_crossing_counts_only_on_the_floors_it_fills_between():
    floors = {cell(24): [(z_dm(0.0), 0)], cell(26): [(z_dm(1.2), 0)]}
    why = {cell(25): hb.NEIGHBOURS_DISAGREE}

    def crossed(za, zc, zb):
        run = hm.Walk(0, 0, 0.0, 1.0, (cell(24), cell(25), cell(26)), (z_dm(za), z_dm(zc), z_dm(zb)))
        return hb.fill_gradient(floors, why, hb.crossings([run]), GEO)[0]

    assert crossed(0.0, 0.6, 1.2) == {cell(25): z_dm(0.6)}, "up the slope itself"
    assert crossed(4.0, 4.0, 4.0) == {}, "along a bridge 4 m over the two anchors"
    assert crossed(2.0, 2.6, 3.2) == {}, "on something 2 m above them"
    assert crossed(-3.0, -3.0, -3.0) == {}, "through a tunnel under them"
    assert crossed(1.2, 0.6, 0.0) == {}, "going a to b downhill where the floors go uphill"
    assert crossed(0.0, 2.5, 1.2) == {}, "over a hump between them (a jump's arc)"
    level = hb.Stand(0, 0, 0.0, 1.0, z_dm(0.6), (cell(24), cell(25), cell(26)), 0.0, 0.0)
    assert hb.fill_gradient(floors, why, hb.crossings([level]), GEO)[0] == {cell(25): z_dm(0.6)}, \
        "a level run between two cells 1.2 m apart is within one cell's rise of both"


def test_a_run_past_a_platform_is_cut_there():
    # A wall stood on at cell 25: the cells within PLATFORM_R_M of it are dropped, and what is left of a run on
    # either side is two runs. One run across the gap would be a crossing nobody made on the ground.
    cells = tuple(cell(c) for c in range(12, 40))
    run = hm.Walk(0, 0, 0.0, 5.0, cells, tuple(range(len(cells))))
    plat = [(0.0, 9.0, 25 * 8 + 4.0, Y)]
    left, right = hb.off_platforms([run], plat, GEO)
    assert left.cells == cells[:len(left.cells)] and right.cells == cells[-len(right.cells):]
    assert left.low == tuple(range(len(left.cells))) and len(left.cells) + len(right.cells) < len(cells)
    assert left.cells[-1] % GRID < 25 < right.cells[0] % GRID
    seen = hb.crossings([left, right])
    assert all(abs(a % GRID - b % GRID) <= 2 * hc.CROSS_REACH for a, _, b in seen), "nothing spans the gap"
    stand = hb.Stand(0, 0, 0.0, 5.0, z_dm(0.0), cells, 0.0, 0.0)
    assert [type(s) for s in hb.off_platforms([stand], plat, GEO)] == [hb.Stand, hb.Stand]
```

- [ ] **Step 3: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_heights.py -q -p no:cacheprovider`
Expected: many failures, the first of them `AttributeError: module 'app.control.height_build' has no attribute 'all_ground'`.

- [ ] **Step 4: The asset: version 2, `kind`, five edge columns**

In `webapp/app/control/heights.py`:

Set `HEIGHT_VERSION = 2`.

In the module docstring, replace the `supported` and `edges` bullets and add `kind`:

```
- `supported`, GRID x GRID bool: the cell's floors come from enough stands or walks (not filled from neighbours);
- `kind`, GRID x GRID int8: where the cell's ground height came from (KIND_*: stands, walks alone, filled flat,
  filled along a gradient; 0 for none). The engine never reads it;
- `unresolved`, GRID x GRID bool: a walkable cell the build couldn't resolve, which uses today's flat
  sight and walking;
- `edges`, K x 5 int32 (cell a, floor a, cell b, floor b, EDGE_*): a walk from floor a of cell a to floor b of
  the neighbouring cell b, one row per direction. EDGE_STEP rows come in pairs. A one-way row is a drop: an
  EDGE_SLIDE when players went down it on the ground, an EDGE_FALL when through the air;
```

Replace the `edges`/`meta` fields of `HeightAsset` with:

```python
    edges: np.ndarray           # K x 5 int32: cell a, floor a, cell b, floor b, EDGE_*
    meta: dict = field(default_factory=dict)
    kind: np.ndarray | None = None   # GRID x GRID int8, KIND_*; None: told from `supported`

    def __post_init__(self) -> None:
        self.edges = np.asarray(self.edges, np.int32).reshape(-1, 5)
        if self.kind is None:
            has = self.floors[..., 0] >= 0
            self.kind = np.where(has, np.where(self.supported, KIND_STANDS, KIND_FILLED), KIND_NONE).astype(np.int8)
```

In `save_asset`, replace the `edges=` argument's line with:

```python
                        edges=asset.edges.astype("<i4").reshape(-1, 5), kind=asset.kind.astype("i1"),
```

In `load_asset`, replace the returned asset's last line with:

```python
                           z["unresolved"].astype(bool), z["edges"].astype(np.int32).reshape(-1, 5), meta,
                           z["kind"].astype(np.int8))
```

`digest` is unchanged: it already hashes every column of `edges`, and `kind` stays out of it like `supported`.

- [ ] **Step 5: `toy_heights` writes edge kinds, and the topology reads five columns**

In `webapp/tests/replays/control_toys.py`, inside `toy_heights`, replace the block that builds `edges` from `hb.connect` and `links`:

```python
    edges = {tuple(e[:4]): e[4] for e in hb.connect(heights, {}, geo).tolist()}
    for (ax, ay, fa), (bx, by, fb), one_way, *how in links:
        a, b = geo.cell_of_px(ax, ay), geo.cell_of_px(bx, by)
        edges[(a, fa, b, fb)] = (how[0] if how else hc.EDGE_FALL) if one_way else hc.EDGE_STEP
        if not one_way:
            edges[(b, fb, a, fa)] = hc.EDGE_STEP
```

and the `HeightAsset(...)` call's edges argument (its second-to-last line):

```python
                           np.array(sorted((*key, kind) for key, kind in edges.items()), np.int32).reshape(-1, 5),
                           {"origin_z": z_dm(lowest)})
```

Extend its docstring's last sentence: `` `links` = [((x, y, floor), (x, y, floor), one_way[, EDGE_*]), ...] adds a walked connection between two neighbouring cells: a climb (both ways), or a drop (one way: a fall unless the fourth element says EDGE_SLIDE). ``

In `webapp/app/control/topology.py`, `NodeTopology.__init__`, the loop over the asset's edges takes the fifth column (Task 4 uses it):

```python
        for a, fa, b, fb, _ in geo.heights.edges.tolist():
```

- [ ] **Step 6: The build**

In `webapp/app/control/height_build.py`:

Imports: `from dataclasses import dataclass, field, replace`, and after the geometry import `from app.control.height_motion import Walk`.

`off_platforms` handles walks too, and cuts a run where it loses cells: what is left on either side of a
platform is two runs, never one run across ground it wasn't seen on. Replace its loop (from `out = []` to
`return out`) with:

```python
    out = []
    for s in found:
        near = [(x, y) for t0, t1, x, y in plats if t0 <= s.t1 and s.t0 <= t1]
        keep = [not any(((c % GRID + 0.5) * CELL - x) ** 2 + ((c // GRID + 0.5) * CELL - y) ** 2 <= r2
                        for x, y in near) for c in s.cells] if near else [True] * len(s.cells)
        if all(keep):
            out.append(s)
            continue
        for i, j in hm._runs_of(np.array(keep)):      # each stretch that is left is a run of its own
            cut = {"cells": s.cells[i:j]}
            if isinstance(s, Walk):
                cut["low"] = s.low[i:j]
            out.append(replace(s, **cut))
    return out
```

and make its docstring read: "`found` (stands or walks) without the cells within PLATFORM_R_M of a platform that
was up during the run. A run left with no cell is dropped; one that loses cells in its middle becomes a run for
each stretch that is left (they keep the whole run's times, so nothing joins them again). By cell, not by the
run's mean position: a long level walk that starts on a wall has its mean far from it."

The existing tests count stands after `off_platforms`: `test_a_level_walk_that_starts_on_a_sage_wall_keeps_only_its_cells_away_from_it`
expects 18 stands, and still gets them (its walks lose cells at one end only).

Replace everything from `def group_cell(` up to (not including) `def _bbox(` with:

```python
def _enough(count: int, rounds: np.ndarray, matches: np.ndarray) -> bool:
    """The floor rule: FLOOR_MIN_STANDS of them, from FLOOR_MIN_ROUNDS rounds in FLOOR_MIN_MATCHES matches."""
    return count >= hc.FLOOR_MIN_STANDS and len(set(rounds.tolist())) >= hc.FLOOR_MIN_ROUNDS \
        and len(set(matches.tolist())) >= hc.FLOOR_MIN_MATCHES


def _levels(z: np.ndarray, rounds: np.ndarray, matches: np.ndarray) -> list[np.ndarray]:
    """The supported levels among some heights: groups (the densest FLOOR_TOL_M window first, then the next)
    that pass the floor rule, each as a mask over `z`. A height no group holds is in none."""
    tol = hc.FLOOR_TOL_M * DM
    left = np.ones(len(z), bool)
    levels = []
    while left.any():
        zs = np.sort(z[left])
        counts = np.searchsorted(zs, zs + 2 * tol, side="right") - np.arange(len(zs))
        start = zs[int(np.argmax(counts))]       # the densest window (the lowest of equals)
        members = left & (z >= start) & (z <= start + 2 * tol)
        for _ in range(2):                       # settle on the heights around the group's own median
            med = float(np.median(z[members]))
            members = left & (np.abs(z - med) <= tol)
        left &= ~members
        if _enough(int(members.sum()), rounds[members], matches[members]):
            levels.append(members)
    return levels


def stand_groups(z: np.ndarray, rounds: np.ndarray, matches: np.ndarray) -> list[float]:
    """The groups of stands that pass the floor rule: each one's 10th-to-90th percentile spread, in dm."""
    out = []
    for members in _levels(z, rounds, matches):
        p10, p90 = np.percentile(z[members], [10, 90])
        out.append(float(p90 - p10))
    return out


def group_cell(z: np.ndarray, rounds: np.ndarray, matches: np.ndarray,
               stand: np.ndarray | None = None) -> tuple[list[tuple[int, int]], str | None, int]:
    """One cell's ground samples (z in world dm, round index, match index, whether it is a stand: all of them
    without `stand`) as floors: ([(height, spread), ...] lowest first, None, the ground floor's KIND_*), or
    ([], reason, KIND_NONE) when the cell can't be resolved, or ([], None, KIND_NONE) when nothing is a floor.

    Bands are built from supported levels only (`_levels`, over stands and walks alike): levels less than
    FLOOR_SEP_M apart are one band, and a sample no level holds joins a band only when it lies within
    FLOOR_TOL_M of it. So a stray sample between two floors bridges nothing and is nobody's; a slope's levels
    chain into one band. The lowest band is a floor; any band above it also needs a group of stands that
    passes the floor rule (a boost passes through the air above a cell). A floor's height is the low end of its
    band (LOW_PCT, never the single lowest sample of several). Two groups of stands in one band are two
    levels too close to tell apart; the stands of a band with no walk in it may not spread over
    FLOOR_SPREAD_MAX_M."""
    if stand is None:
        stand = np.ones(len(z), bool)
    tol = hc.FLOOR_TOL_M * DM
    levels = sorted(_levels(z, rounds, matches), key=lambda m: float(z[m].min()))
    bands: list[np.ndarray] = []
    for members in levels:
        if bands and z[members].min() - z[bands[-1]].max() < hc.FLOOR_SEP_M * DM:
            bands[-1] = bands[-1] | members
        else:
            bands.append(members.copy())
    loose = ~np.logical_or.reduce(levels) if levels else np.zeros(len(z), bool)
    bands = [band | (loose & (z >= z[band].min() - tol) & (z <= z[band].max() + tol)) for band in bands]
    floors, kinds = [], []
    for members in bands:
        zs, st = z[members], stand[members]
        groups = stand_groups(zs[st], rounds[members][st], matches[members][st])
        if not groups and (floors or st.all()):
            continue      # above the ground, or with no walk in it, a band needs a group of stands
        if len(groups) >= 2:
            return [], TOO_CLOSE, hc.KIND_NONE
        if st.all() and groups[0] > hc.FLOOR_SPREAD_MAX_M * DM:
            return [], SPREAD, hc.KIND_NONE      # as before the slopes: the stands of one level, on a steep ramp
        low, p10 = np.percentile(zs, [hc.LOW_PCT, 10], method="higher")
        p90 = np.percentile(zs, 90, method="lower")
        floors.append((int(round(float(low))), int(round(float(max(p90 - p10, 0))))))
        kinds.append(hc.KIND_STANDS if groups else hc.KIND_WALKS)
    if len(floors) > hc.MAX_FLOORS:
        return [], TOO_MANY, hc.KIND_NONE
    return floors, None, kinds[0] if kinds else hc.KIND_NONE


@dataclass
class HeightBuild:
    asset: hc.HeightAsset
    report: dict
    stands: list = field(default_factory=list)
    reasons: dict = field(default_factory=dict)      # flat cell -> why it is unresolved
    ready: bool = False
    walks: list = field(default_factory=list)


def cell_floors(found: list[Stand], round_match: list[int], geo: Geometry,
                found_walks: list[Walk] = ()) -> tuple[dict, dict, dict]:
    """({cell: [(height, spread), ...]}, {cell: reason}, {cell: the ground floor's KIND_*}) over the walkable
    cells with ground samples. A stand gives each of its cells its median; a walk gives each of its cells the
    lowest z it had there."""
    by_cell: dict[int, list[tuple[int, int, int, bool]]] = defaultdict(list)
    for s in found:
        for cell in set(s.cells):
            by_cell[cell].append((s.z, s.round, round_match[s.round], True))
    for w in found_walks:
        lows: dict[int, int] = {}
        for cell, z in zip(w.cells, w.low):
            lows[cell] = min(z, lows.get(cell, z))
        for cell, z in lows.items():
            by_cell[cell].append((z, w.round, round_match[w.round], False))
    walk = geo.walk.ravel()
    floors, reasons, kinds = {}, {}, {}
    for cell, rows in by_cell.items():
        if not walk[cell]:
            continue
        z, rounds, matches, stand = (np.array(col) for col in zip(*rows))
        got, why, kind = group_cell(z.astype(float), rounds, matches, stand.astype(bool))
        if why is not None:
            reasons[cell] = why
        elif got:
            floors[cell], kinds[cell] = got, kind
    return floors, reasons, kinds


def all_ground(rounds, geo: Geometry) -> tuple[list[Stand], list[Walk], list[int], dict]:
    """Every round's stands and walks, outside blackouts and off platforms; each round's match index; and the
    counts. `rounds` is read once, a round at a time (it may be a generator)."""
    found, found_walks, match_of, dt = [], [], [], []
    counts = {"stands": 0, "walks": 0, "on_platforms": 0, "blackouts": 0, "rounds": 0, "matches": 0,
              "rounds_without_z": 0, "visited": np.zeros(GRID * GRID, bool)}
    for i, (match, _, blob) in enumerate(rounds):
        match_of.append(str(match))
        dt.append(1.0 / blob["hz"])
        skip = hm.blackouts(blob, geo)
        counts["blackouts"] += sum(len(spans) for spans in skip.values())
        mine = stands(blob, geo, i, skip)
        strides = hm.walks(blob, geo, i, mine, skip)
        for _, _, x, y, _ in _tracks(blob):
            counts["visited"][hm.cells_of(x, y)] = True
        if not any("z" in seg for segs in (blob.get("tracks") or {}).values() for seg in segs):
            counts["rounds_without_z"] += 1
        plats = platforms(blob)
        kept = off_platforms(mine, plats, geo)
        counts["on_platforms"] += len(mine) - len(kept)
        found += kept
        found_walks += off_platforms(strides, plats, geo)
    match_ids = sorted(set(match_of))
    counts.update({"stands": len(found), "walks": len(found_walks), "rounds": len(match_of),
                   "matches": len(match_ids), "dt": dt})
    return found, found_walks, [match_ids.index(m) for m in match_of], counts


def all_stands(rounds, geo: Geometry) -> tuple[list[Stand], list[int], dict]:
    """`all_ground` without the walks."""
    found, _, round_match, counts = all_ground(rounds, geo)
    return found, round_match, counts


# ---------------------------------------------------------------- fill, connections, areas


EIGHT = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
# a cell's neighbours directly beside it on opposite sides
OPPOSITE = [((-1, 0), (1, 0)), ((0, -1), (0, 1)), ((-1, -1), (1, 1)), ((-1, 1), (1, -1))]


def _neighbours(cell: int, walk: np.ndarray):
    """The walkable cells among `cell`'s eight neighbours (walk: GRID x GRID)."""
    y, x = divmod(cell, GRID)
    for dy, dx in EIGHT:
        ny, nx = y + dy, x + dx
        if 0 <= ny < GRID and 0 <= nx < GRID and walk[ny, nx]:
            yield ny * GRID + nx


def fill(floors: dict, reasons: dict, geo: Geometry) -> tuple[dict, dict]:
    """({cell: ground height} for the cells filled from their neighbours, {cell: reason} for every other
    walkable cell without a floor, `reasons` included)."""
    walk = geo.walk
    filled, why = {}, dict(reasons)
    tol = hc.FILL_TOL_M * DM
    for cell in np.flatnonzero(walk.ravel()).tolist():
        if cell in floors or cell in why:
            continue
        near, front = {cell}, {cell}
        for _ in range(hc.FILL_R):
            front = {n for c in front for n in _neighbours(c, walk)} - near
            near |= front
        known = [c for c in near if c in floors]
        heights = [h for c in known for h, _ in floors[c]]
        if len(known) < hc.FILL_MIN_NEIGHBOURS:
            why[cell] = NO_SAMPLES
        elif max(heights) - min(heights) > tol:
            why[cell] = NEIGHBOURS_DISAGREE     # a drop or a second floor nearby: never averaged
        else:
            filled[cell] = int(round(float(np.median(heights))))
    return filled, why


def crossings(runs) -> dict:
    """{(a, c, b): [(round, z in a, z in c, z in b), ...]}: each time a ground run (a walk, or a stand that
    moves) went from cell a through c to b, a and b each within CROSS_REACH cells of c in the run's own order,
    with the run's height in the three cells (world dm). `off_platforms` has already cut every run where it
    lost cells, so a run never reaches across ground it wasn't seen on."""
    seen: dict[tuple, list] = defaultdict(list)
    for run in runs:
        cells = run.cells
        zs = [run.z] * len(cells) if isinstance(run, Stand) else run.low
        for j, c in enumerate(cells):
            for i in range(max(0, j - hc.CROSS_REACH), j):
                for k in range(j + 1, min(len(cells), j + 1 + hc.CROSS_REACH)):
                    a, b = cells[i], cells[k]
                    if a != b and a != c and b != c:
                        seen[(a, c, b)].append((run.round, zs[i], zs[j], zs[k]))
    return seen


def _on_floors(crossed: dict, a: int, c: int, b: int, ha: int, hb: int, reach: float) -> bool:
    """Whether some run that went a -> c -> b did so on these two floors: in each anchor it was within `reach`
    of that anchor's floor, it went the way the floors go, and in between it stayed between its two ends. A
    run on a bridge over the anchors, or in a tunnel under them, crosses nothing of theirs."""
    tol = hc.FLOOR_TOL_M * DM
    for _, za, zc, zb in crossed.get((a, c, b), ()):
        if abs(za - ha) <= reach and abs(zb - hb) <= reach and (zb - za) * (hb - ha) >= 0 \
                and min(za, zb) - tol <= zc <= max(za, zb) + tol:
            return True
    return False


def fill_gradient(floors: dict, why: dict, crossed: dict, geo: Geometry) -> tuple[dict, set]:
    """The slopes `fill` refused (the slopes spec, part 3): ({cell: ground height}, {(from cell, to cell)}).
    A NEIGHBOURS_DISAGREE cell is filled when it has sampled single-floor neighbours directly beside it on
    opposite sides, no steeper than SLOPE_MAX between them, and a ground run crossed from one to the other
    through it on those two floors (`crossings`, `_on_floors`: within one cell's steepest rise plus
    FLOOR_TOL_M of each): it takes the mean of the pair. Several pairs must agree within FILL_TOL_M. The second
    result is the steps those runs took (into the cell and out of it, in the direction seen), which `connect`
    takes as walked on the ground. Only `floors` anchor a fill: a filled cell never fills another."""
    filled, granted = {}, set()
    tol = hc.FILL_TOL_M * DM
    reach = (hc.SLOPE_MAX * geo.cell_m + hc.FLOOR_TOL_M) * DM
    for cell, reason in why.items():
        if reason != NEIGHBOURS_DISAGREE:
            continue
        y, x = divmod(cell, GRID)
        means, steps = [], set()
        for (ay, ax), (by, bx) in OPPOSITE:
            if not (0 <= y + ay < GRID and 0 <= x + ax < GRID and 0 <= y + by < GRID and 0 <= x + bx < GRID):
                continue
            a, b = (y + ay) * GRID + x + ax, (y + by) * GRID + x + bx
            if len(floors.get(a, ())) != 1 or len(floors.get(b, ())) != 1:
                continue
            ha, hb_ = floors[a][0][0], floors[b][0][0]
            run_m = 2 * geo.cell_m * (2 ** 0.5 if ay and ax else 1.0)
            if abs(ha - hb_) / DM > hc.SLOPE_MAX * run_m:
                continue
            ab = _on_floors(crossed, a, cell, b, ha, hb_, reach)
            ba = _on_floors(crossed, b, cell, a, hb_, ha, reach)
            if not (ab or ba):
                continue
            means.append((ha + hb_) / 2)
            if ab:
                steps |= {(a, cell), (cell, b)}
            if ba:
                steps |= {(b, cell), (cell, a)}
        if means and max(means) - min(means) <= tol:
            filled[cell] = int(round(float(np.mean(means))))
            granted |= steps
    return filled, granted


def _floor_of(heights: list[int], z: int) -> int | None:
    """The index of the floor a sample at z is on: the highest at or below it (FLOOR_TOL_M of slack), unless
    that is FLOOR_SEP_M or more below (the sample is in the air, or on a level that isn't a floor)."""
    best = None
    for i, h in enumerate(heights):
        if h <= z + hc.FLOOR_TOL_M * DM:
            best = i
    return best if best is not None and z - heights[best] < hc.FLOOR_SEP_M * DM else None


def walked(found: list[Stand], heights: dict, found_walks: list[Walk] = (), dt: list[float] | None = None) -> tuple[dict, dict]:
    """({(cell a, floor a, cell b, floor b): the rounds it was walked in}, the same for the walks made on the
    ground): a to b, neighbouring cells. On the ground: the cells one stand or one walk passes through, and
    from one of a player's runs to their next when no sample lies between them (`dt`: each round's sample
    spacing). Not known to be on the ground: from one stand to the same player's next within CONNECT_S (a jump
    down, a fall)."""
    seen: dict[tuple, set] = defaultdict(set)
    ground: dict[tuple, set] = defaultdict(set)

    def note(a: int, za: int, b: int, zb: int, rnd: int, on_ground: bool) -> None:
        ay, ax = divmod(a, GRID)
        by, bx = divmod(b, GRID)
        if a == b or abs(ay - by) > 1 or abs(ax - bx) > 1 or a not in heights or b not in heights:
            return
        fa, fb = _floor_of(heights[a], za), _floor_of(heights[b], zb)
        if fa is not None and fb is not None:
            seen[(a, fa, b, fb)].add(rnd)
            if on_ground:
                ground[(a, fa, b, fb)].add(rnd)

    def ends(run) -> tuple[int, int]:
        return (run.z, run.z) if isinstance(run, Stand) else (run.low[0], run.low[-1])

    def same_player(a, b) -> bool:
        return a is not None and (a.round, a.slot) == (b.round, b.slot)

    previous = last_stand = None
    for run in sorted([*found, *found_walks], key=lambda r: (r.round, r.slot, r.t0)):
        zs = [run.z] * len(run.cells) if isinstance(run, Stand) else run.low
        for (a, za), (b, zb) in zip(zip(run.cells, zs), zip(run.cells[1:], zs[1:])):
            note(a, za, b, zb, run.round, True)
        # a walk that picks up where the last run stopped (or a stand where a walk did): one ground run. Two
        # stands end to end are left to the rule below: a teleport reads as that.
        if same_player(previous, run) and dt is not None and (isinstance(run, Walk) or isinstance(previous, Walk)) \
                and 0 <= run.t0 - previous.t1 <= 1.5 * dt[run.round]:
            note(previous.cells[-1], ends(previous)[1], run.cells[0], ends(run)[0], run.round, True)
        if isinstance(run, Stand):
            if same_player(last_stand, run) and 0 <= run.t0 - last_stand.t1 <= hc.CONNECT_S:
                note(last_stand.cells[-1], last_stand.z, run.cells[0], run.z, run.round, False)
            last_stand = run
        previous = run
    return seen, ground


def connect(heights: dict, seen: dict, geo: Geometry, ground: dict | None = None,
            granted: set = frozenset()) -> np.ndarray:
    """The directed walks between resolved floors, K x 5 (cell a, floor a, cell b, floor b, EDGE_*), sorted.
    Floors within STEP_UP_M are a step, both ways. A bigger one connects only where it was walked
    (CONNECT_MIN_ROUNDS rounds of `seen`, or a step `fill_gradient` granted): seen going up it is a step both
    ways (what is climbed can be dropped from); seen only going down it is one-way, a slide when it was walked
    on the ground (`ground`, or granted) and a fall otherwise."""
    walk = geo.walk
    step = hc.STEP_UP_M * DM
    ground = ground or {}
    edges: dict[tuple, int] = {}
    for a, floors_a in heights.items():
        for b in _neighbours(a, walk):
            if b not in heights:
                continue
            for i, ha in enumerate(floors_a):
                for j, hb in enumerate(heights[b]):
                    key = (a, i, b, j)
                    # The spec infers this between ground floors; here between any two
                    # floors, so a bridge is walkable along itself where few rounds walked it.
                    if abs(ha - hb) <= step:
                        edges[key] = hc.EDGE_STEP
                        continue
                    given = i == 0 and j == 0 and (a, b) in granted
                    on_ground = given or len(ground.get(key, ())) >= hc.CONNECT_MIN_ROUNDS
                    if not (on_ground or len(seen.get(key, ())) >= hc.CONNECT_MIN_ROUNDS):
                        continue
                    if hb > ha:
                        edges[key] = edges[(b, j, a, i)] = hc.EDGE_STEP     # climbed: it can be dropped from
                    else:
                        edges.setdefault(key, hc.EDGE_SLIDE if on_ground else hc.EDGE_FALL)
    return np.array(sorted((*key, kind) for key, kind in edges.items()), np.int32).reshape(-1, 5)
```

In `air_only`, the edges have five columns:

```python
    pairs = [(ids[(a, i)], ids[(b, j)]) for a, i, b, j, _ in edges.tolist()]
```

Replace the whole `build` function with:

```python
def build(rounds, geo: Geometry) -> HeightBuild:
    """A map's heights from `rounds` = [(match id, round number, blob), ...]. Never refuses: `ready` says
    whether the map reaches the bar, and `report["not_ready"]` why not."""
    found, found_walks, round_match, counts = all_ground(rounds, geo)
    dt = counts.pop("dt")
    floors, reasons, kinds = cell_floors(found, round_match, geo, found_walks)
    filled, why = fill(floors, reasons, geo)
    sloped, granted = fill_gradient(floors, why, crossings([*found, *found_walks]), geo)
    for cell in sloped:
        del why[cell]
    heights = {cell: [h for h, _ in got] for cell, got in floors.items()}
    heights.update({cell: [h] for cell, h in {**filled, **sloped}.items()})
    seen, ground = walked(found, heights, found_walks, dt)
    edges = connect(heights, seen, geo, ground, granted)
    walk = geo.walk.ravel()
    n_walk = int(walk.sum())
    origin = min((h for hs in heights.values() for h in hs), default=0)
    asset_floors = -np.ones((GRID * GRID, hc.MAX_FLOORS), np.int16)
    spread = np.zeros((GRID * GRID, hc.MAX_FLOORS), np.int16)
    supported = np.zeros(GRID * GRID, bool)
    unresolved = np.zeros(GRID * GRID, bool)
    kind = np.zeros(GRID * GRID, np.int8)
    for cell, got in floors.items():
        supported[cell], kind[cell] = True, kinds[cell]
        for i, (h, s) in enumerate(got):
            asset_floors[cell, i], spread[cell, i] = h - origin, s
    for cells, code in ((filled, hc.KIND_FILLED), (sloped, hc.KIND_GRADIENT)):
        for cell, h in cells.items():
            asset_floors[cell, 0], kind[cell] = h - origin, code
    unresolved[list(why)] = True
    count = (asset_floors >= 0).sum(1)
    areas = unresolved_areas(why)
    share, not_ready = readiness(supported, unresolved, count, n_walk)
    visited = counts.pop("visited") & walk
    pairs = {tuple(e[:4]) for e in edges.tolist()}
    drops = [(code, heights[a][i] - heights[b][j]) for a, i, b, j, code in edges.tolist() if code != hc.EDGE_STEP]
    report = {
        "walkable_cells": n_walk, "visited_cells": int(visited.sum()), "supported_cells": int(supported.sum()),
        "filled_cells": len(filled) + len(sloped), "unresolved_cells": len(why),
        "visited": round(float(visited.sum()) / n_walk, 4) if n_walk else 0.0, "supported": round(share, 4),
        "cells_by_kind": {"stands": int((kind == hc.KIND_STANDS).sum()), "walks": int((kind == hc.KIND_WALKS).sum()),
                          "filled": len(filled), "gradient": len(sloped)},
        "cells_2_floors": int((count == 2).sum()), "cells_3_floors": int((count == 3).sum()),
        "refused_cells": sum(1 for r in why.values() if r == TOO_MANY),
        "unresolved_why": dict(Counter(why.values()).most_common()), "unresolved_areas": areas,
        "air_only": air_only(heights, why, edges, geo), "edges": int(len(edges)),
        "one_way_edges": sum(1 for a, i, b, j in pairs if (b, j, a, i) not in pairs),
        "slides": sum(1 for code, _ in drops if code == hc.EDGE_SLIDE),
        "falls": sum(1 for code, _ in drops if code == hc.EDGE_FALL),
        "falls_closed": sum(1 for code, drop in drops if code == hc.EDGE_FALL and drop > hc.SILENT_DROP_M * DM),
        "origin_z": int(origin), **counts, "ready": not not_ready, "not_ready": not_ready,
    }
    meta = {"origin_z": int(origin), "stands": counts["stands"], "walks": counts["walks"], "rounds": counts["rounds"],
            "matches": counts["matches"],
            "walk_sha": hashlib.sha256(np.packbits(geo.walk_px).tobytes()).hexdigest()[:12]}
    report["walk_sha"] = meta["walk_sha"]
    asset = hc.HeightAsset(asset_floors.reshape(GRID, GRID, hc.MAX_FLOORS), spread.reshape(GRID, GRID, hc.MAX_FLOORS),
                           supported.reshape(GRID, GRID), unresolved.reshape(GRID, GRID), edges, meta,
                           kind.reshape(GRID, GRID))
    return HeightBuild(asset, report, found, why, not not_ready, found_walks)
```

Replace the head of `report_lines` (from its `def` line down to, not including, `for area in r["air_only"]:`) with:

```python
def report_lines(name: str, report: dict) -> list[str]:
    """The build's result as printed lines; every unresolved area is a WARNING line (the spec: loudly)."""
    r = report
    k = r["cells_by_kind"]
    out = [f"{name}: {r['stands']} stands and {r['walks']} walks from {r['rounds']} rounds of {r['matches']} matches"
           + (f" ({r['rounds_without_z']} rounds without heights)" if r["rounds_without_z"] else "")
           + (f", {r['on_platforms']} stands by a platform dropped" if r["on_platforms"] else "")
           + (f", {r['blackouts']} blackouts after a movement ability" if r["blackouts"] else ""),
           f"  visited {r['visited']:.1%} of {r['walkable_cells']} walkable cells, supported {r['supported']:.1%} "
           f"(bar {hc.HEIGHT_SUPPORTED_MIN:.0%}; {k['walks']} cells from walks alone), filled {k['filled']} flat and "
           f"{k['gradient']} along a gradient, unresolved {r['unresolved_cells']}",
           f"  cells with 2 floors: {r['cells_2_floors']}, with 3: {r['cells_3_floors']}, refused (more than "
           f"{hc.MAX_FLOORS}): {r['refused_cells']}; walks {r['edges']} ({r['one_way_edges']} one-way: {r['slides']} "
           f"slides, {r['falls']} falls, {r['falls_closed']} of them too high for the unknown)"]
```

Rewrite the module docstring's bullets to say what the build now does (keep its first paragraph, the "Rounds in" line and the last line):

```
- **Stands and walks.** Each player's track is cut into stands: runs of at least STAND_S in which z stays within
  STAND_TOL_M of the run's median. A short stand with lower ground just before and just after it is the top of
  a jump and is dropped (STAND_APEX_S). What is left of the track gives walks (height_motion.py): runs in which
  the player moves along the ground, uphill or down. For ABILITY_BLACKOUT_S after a movement ability a player
  gives neither. Stands and walks near a live temporary platform (heights.PLATFORMS) lose those cells. A stand
  belongs to every cell its samples pass through, with its median z; a walk gives each cell the lowest z it had
  there.
- **Floors.** In each cell those heights are grouped into levels: groups within FLOOR_TOL_M that have
  FLOOR_MIN_STANDS of them from FLOOR_MIN_ROUNDS rounds in FLOOR_MIN_MATCHES matches. Levels less than
  FLOOR_SEP_M apart are one band (a slope's levels chain); a height no level holds joins a band only within
  FLOOR_TOL_M of it, so a stray sample between two floors is nobody's. A band's height is its low end
  (LOW_PCT): a player can't be below the floor. The lowest band needs no stand; any band above it needs a group
  of stands that passes the same rule. Two groups of stands in one band, a band
  of stands alone spread over more than FLOOR_SPREAD_MAX_M, or more than MAX_FLOORS floors leave the cell
  unresolved, with the reason.
- **Unsampled cells.** A walkable cell with no floor takes a ground floor when at least FILL_MIN_NEIGHBOURS cells
  within FILL_R cells of it (by walking) have floors and all of those floors agree within FILL_TOL_M: their
  median, and no upper floors. Where they disagree, it is filled along the gradient when it has single-floor
  neighbours directly beside it on opposite sides, no steeper than SLOPE_MAX between them, and a ground run
  crossed from one to the other through it at those floors' heights: the mean of the pair. Otherwise it is unresolved. Filling never
  averages across a drop nobody walked, and a filled cell never fills another.
- **Connections.** Floors of neighbouring cells (all 8) within STEP_UP_M connect both ways. A bigger step connects
  only where it was walked (CONNECT_MIN_ROUNDS rounds, or the run that crossed a gradient-filled cell). Seen
  going up it connects both ways (what is climbed can be dropped from); seen only going down it is one-way: a
  slide when the player stayed on the ground, a fall otherwise. Unresolved cells carry no connections here: the
  engine gives them today's 2D walking.
```

and in the **Readiness** bullet replace "a floor from stands (not filled)" with "a floor from stands or walks (not filled)".

- [ ] **Step 7: Run the build's tests**

Run: `PY -m pytest tests/replays/test_control_heights.py tests/replays/test_height_motion.py -q -p no:cacheprovider`
Expected: all pass (55 in test_control_heights.py; the independent descent regression adds to the prior 19 motion tests, so record the actual count).

If `test_stairs_nobody_stops_on_get_floors_from_walks` fails on a floor's height, print `b.asset.floors[ROW, 21:27, 0]`: each should be within a decimetre of `(c * 8 - 160) / 64 * 40` dm. A value near 40 means the stairs' cells got stands (the run was slow enough to be one): the walk rule isn't being reached.

- [ ] **Step 8: Re-pin the revision, and run everything that reads an asset**

`HEIGHT_VERSION` is one of the constants `CONTROL_REVISION`'s digest covers. Run
`PY -m pytest tests/replays/test_control_format.py::test_the_engine_constants_are_pinned_to_the_control_revision -q -p no:cacheprovider`,
take the digest its failure prints, and replace revision 7's digest in `PINNED` with it (7 is unreleased; 6 is `main`'s and is never re-pinned here). The
only constant that changed in this task is `HEIGHT_VERSION`; if you changed another, that is a start value moving
and it is reported, not silently re-pinned.

Run: `PY -m pytest tests/replays -k "control or height or gaps or feature" -q -p no:cacheprovider`
Expected: everything passes except the failures recorded in the baseline, failing the same way. `test_control_floors.py` passes unchanged at this commit: the topology reads the fifth column and ignores it until Task 4.

Then: `PY -m pytest tests/replays/test_height_viewer.py -q -p no:cacheprovider`
Expected: PASS (its toy asset has no edges; `kind` is derived).

- [ ] **Step 9: Build one map from the frozen rounds and look at the numbers**

```
PY scripts\build_control_heights.py --map Sunset --blobs-dir %TEMP%\valo-replay\heights-frozen --preview --out %TEMP%\valo-replay\heights-preview
PY scripts\compare_height_builds.py --old %TEMP%\valo-replay\heights-preview-old --new %TEMP%\valo-replay\heights-preview --map Sunset
```

(with `--preview-min-matches 1` on the build if Sunset's old build in Task 2 had it). No database is read.
Expected: the build prints the new report head (`... stands and ... walks from ...`), a non-zero
`cells from walks alone` and a `filled ... along a gradient` count; the comparison is not refused and exits 0.
If it exits 1 (kill lines got worse) or supported went down, stop and report both lines: this is the first
real-data check of the rules, and Task 6 repeats it for every map.

- [ ] **Step 10: Commit**

```bash
git add webapp/app/control/heights.py webapp/app/control/height_build.py webapp/app/control/topology.py webapp/tests/replays/control_toys.py webapp/tests/replays/test_control_heights.py webapp/tests/replays/test_control_format.py
git commit -F <message file>
```

Message: `Heights: floors from walks and the low end, gradient fill, slides and falls; asset version 2`.

---

### Task 4: The unknown stops at a fall too high to land quietly

**Files:**
- Modify: `webapp/app/control/topology.py` (import, `NodeTopology.__init__`, both `spread` methods, module docstring)
- Modify: `webapp/app/control/engine.py` (`Unknown._spread`, the `Unknown` docstring)
- Test: `webapp/tests/replays/test_control_floors.py`

**Interfaces:**
- Consumes: Task 3's `edges` fifth column, `heights.EDGE_FALL`, `heights.SILENT_DROP_M`, `toy_heights(links=[..., kind])`.
- Produces: `NodeTopology.in_cost_quiet` (the `in_cost` table with `inf` on every fall higher than `SILENT_DROP_M`); `FlatTopology.spread(..., quiet=False)` and `NodeTopology.spread(..., quiet=False)`. `links`, `dist`, `label`, `dilate`, `back`, `around` are unchanged.

- [ ] **Step 1: Write the failing tests**

In `webapp/tests/replays/test_control_floors.py`, add `from app.control import heights as hc` to the imports, and replace the whole of `test_the_unknown_goes_down_a_drop_and_never_back_up_it` (its 4 m drop is now a fall too high to take quietly) with:

```python
def drop_ledge(height: float, how: int):
    """West of x 256 is `height` m up; the ledge can be left at y 204 only, by a one-way drop of kind `how`."""
    return toy_heights(f"Ledge{height}-{how}", [HALL], ground=[((96, 96, 256, 296), height)],
                       links=[((252, Y, 0), (260, Y, 0), True, how)])


def unknown_below(geo, from_top: bool = True) -> np.ndarray:
    """A's unknown after an enemy spent a minute on top of the ledge (or below it), A far away on the same side."""
    unk = ce.Unknown(geo)
    enemy = node(geo, 200) if from_top else node(geo, 300)
    mine = node(geo, 400, 110) if from_top else node(geo, 120, 110)
    for t in (0.0, 60.0):
        unk.apply(_Tk(t, _at(0, "A", geo, mine), _at(5, "B", geo, enemy)))
    return unk.cells["A"]


def test_the_unknown_goes_down_a_slide_or_a_low_fall_and_never_back_up():
    # docs/superpowers/specs/2026-10-05-height-slopes-design.md, part 5
    for geo in (drop_ledge(4.0, hc.EDGE_SLIDE), drop_ledge(1.0, hc.EDGE_FALL)):
        assert (node(geo, 252), node(geo, 260), True) in topology.of(geo).links
        assert unknown_below(geo)[node(geo, 300)], "from the top it reaches the low ground"
        up = unknown_below(geo, from_top=False)
        assert up[node(geo, 260)] and not up[node(geo, 252)] and not up[node(geo, 200)], "but never climbs it"
    assert not unknown_below(ledge())[node(ledge(), 300)], "with no walked drop the ledge isn't crossed at all"


def test_the_unknown_stops_at_a_fall_too_high_to_land_quietly():
    assert not unknown_below(drop_ledge(4.0, hc.EDGE_FALL))[node(drop_ledge(4.0, hc.EDGE_FALL), 300)]
    just_over = drop_ledge(1.1, hc.EDGE_FALL)
    assert not unknown_below(just_over)[node(just_over, 300)], "anything over SILENT_DROP_M makes a sound"
    assert hc.SILENT_DROP_M == 1.0


def test_a_real_player_still_drops_off_any_ledge():
    # Only the unknown's spread is closed: walking distances, connected pieces and the engine's links are not.
    geo = drop_ledge(4.0, hc.EDGE_FALL)
    topo = topology.of(geo)
    assert (node(geo, 252), node(geo, 260), True) in topo.links
    assert topo.dist(node(geo, 200))[node(geo, 300)] > 0 and topo.dist(node(geo, 300))[node(geo, 200)] == -1
    grown = topo.dilate(np.eye(1, geo.n, node(geo, 252), dtype=bool)[0], eight=True)
    assert grown[node(geo, 260)], "growing a mask (memory, backfill) still goes over the edge"


def test_a_fall_edge_between_stacked_floors_is_judged_by_its_own_two_floors():
    # A bridge 4 m over the ground: dropping from the bridge onto the ground beside it is a 4 m fall, whatever the
    # ground floor of the bridge's own cell is.
    geo = toy_heights("BridgeDrop", [HALL], upper=[((240, 96, 288, 296), 4.0)],
                      links=[((284, Y, 1), (292, Y, 0), True, hc.EDGE_FALL)])
    topo = topology.of(geo)
    on, beside = node(geo, 284, floor=1), node(geo, 292)
    assert (on, beside, True) in topo.links
    row = topo.in_from[beside].tolist()
    assert np.isinf(topo.in_cost_quiet[beside, row.index(on)]) and np.isfinite(topo.in_cost[beside, row.index(on)])
```

`ledge()` (the existing helper, no arguments) is still used for the no-drop case; `ledge(drop_at=Y)` is no longer called by these tests but other tests in the file may use it, so leave it.

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_floors.py -q -p no:cacheprovider -k "unknown or real_player or fall_edge"`
Expected: `test_the_unknown_stops_at_a_fall_too_high_to_land_quietly` FAILS (the unknown still drops 4 m), and `test_a_fall_edge_between_stacked_floors_is_judged_by_its_own_two_floors` FAILS with `AttributeError: 'NodeTopology' object has no attribute 'in_cost_quiet'`.

- [ ] **Step 3: The quiet cost table**

In `webapp/app/control/topology.py`:

Import the constants (the module imports nothing else from the build):

```python
from app.control import heights as hc
from app.control.geometry import GRID, Geometry
```

In `NodeTopology.__init__`, replace the loop over the asset's edges with:

```python
        loud: set[tuple[int, int]] = set()    # falls too high to land quietly: closed to the unknown's spread
        floors = geo.heights.floors.reshape(GRID * GRID, -1)
        for a, fa, b, fb, kind in geo.heights.edges.tolist():
            na, nb = int(node_of[a, fa]), int(node_of[b, fb])
            if na >= 0 and nb >= 0 and cell_walk[a] and cell_walk[b] and not geo.unresolved[a] and not geo.unresolved[b]:
                walks.add((na, nb))
                if kind == hc.EDGE_FALL and int(floors[a, fa]) - int(floors[b, fb]) > hc.SILENT_DROP_M * 10:
                    loud.add((na, nb))
```

and directly after the two lines that fill `self.in_from` and `self.in_cost`:

```python
        # the same table for a quiet walker: a fall of more than SILENT_DROP_M is not a way in
        is_loud = np.array([pair in loud for pair in map(tuple, pairs.tolist())], bool) if len(pairs) else np.zeros(0, bool)
        self.in_cost_quiet = self.in_cost.copy()
        self.in_cost_quiet[dst[order], column] = np.where(is_loud[order], np.inf, self.in_cost[dst[order], column])
```

`NodeTopology.spread` takes `quiet`:

```python
    def spread(self, reached: np.ndarray, room: np.ndarray, free: np.ndarray, t: float, straight: float,
               links: list, parents: bool = False, solid: np.ndarray | None = None, quiet: bool = False):
        """As `FlatTopology.spread`. With `parents`, a node's source is the first of its `in_from` columns
        (ascending source node id) that gives exactly its final arrival, then a link; -1 as there. With
        `quiet`, no arrival comes down a fall of more than SILENT_DROP_M (heights.py EDGE_FALL)."""
        g = reached.copy()
        cost = (self.in_cost_quiet if quiet else self.in_cost) * straight
```

(the rest of the method is unchanged: the parents are found with the same `cost`.)

`FlatTopology.spread` takes it and ignores it. Change its signature and the first line of its docstring:

```python
    def spread(self, reached: np.ndarray, room: np.ndarray, free: np.ndarray, t: float, straight: float,
               links: list, parents: bool = False, solid: np.ndarray | None = None, quiet: bool = False):
        """The unknown's arrival times relaxed through `room` up to time `t` (engine.Unknown._spread; `quiet`
        means nothing on a flat map, which has no falls):
```

In the module docstring's `NodeTopology` paragraph, after "a drop is one-way", add: `A drop is a slide or a fall (heights.py EDGE_*): the unknown's spread, asked with `quiet`, takes a slide of any height and a fall of at most SILENT_DROP_M, because a higher landing always makes a sound. Everything else walks every drop.`

- [ ] **Step 4: The unknown asks quietly**

In `webapp/app/control/engine.py`, `Unknown._spread`, the call gains `quiet=True`:

```python
        arr, par = self.topo.spread(g, room, free, t, self.geo.cell_m / UNKNOWN_MPS, self.links, parents=True,
                                   solid=solid, quiet=True)
```

At the end of the `Unknown` class docstring's second paragraph, add: `It never comes down a fall higher than SILENT_DROP_M (docs/superpowers/specs/2026-10-05-height-slopes-design.md, part 5): the unknown is where an enemy could have got to while playing quietly, and nobody playing quietly takes a loud drop, whoever is or isn't in earshot.`

- [ ] **Step 5: Run the tests**

Run: `PY -m pytest tests/replays/test_control_floors.py tests/replays/test_control_unknown.py tests/replays/test_control_reference.py -q -p no:cacheprovider`
Expected: all pass. `test_control_reference.py` passing is the proof that a flat map's bytes did not change.

- [ ] **Step 6: The whole suite**

No constant changed in this task (`SILENT_DROP_M` arrived in Task 1, and revision 7's comment in
`webapp/tests/replays/test_control_format.py` already names it), so the pin still matches.

Run: `PY -m pytest tests/replays -k "not pg" -q -p no:cacheprovider`
Expected: only the failures recorded in the baseline, failing the same way.

- [ ] **Step 7: Commit**

```bash
git add webapp/app/control/topology.py webapp/app/control/engine.py webapp/tests/replays/test_control_floors.py
git commit -F <message file>
```

Message: `Control: the unknown stops at a fall too high to land quietly (CONTROL_REVISION 7)`.

---

### Task 5: The viewer shows how each cell got its height

**Files:**
- Modify: `webapp/scripts/height_viewer.py` (`SHAPES`, `map_payload`, module docstring)
- Modify: `webapp/scripts/height_viewer_core.js` (`prepare`, new `kindWord`, the exported `api`)
- Modify: `webapp/scripts/height_viewer.template.html` (one checkbox, `drawMarks`, `describe`, the change listener)
- Test: `webapp/tests/replays/test_height_viewer.py`

**Interfaces:**
- Consumes: `HeightAsset.kind`, `heights.KIND_*`.
- Produces: the payload key `"kinds"` (run-length encoded like `supported`; the payload's `"kind"` already says preview or committed); `HeightCore.kindWord(map, cell) -> string`; the page's `lyNew` layer.

- [ ] **Step 1: Write the failing tests**

Append to `webapp/tests/replays/test_height_viewer.py`:

```python
# ---------------------------------------------------------------- how each cell got its height (slopes spec, 4)


def kinded(tmp_path):
    """synthetic() with one cell of each new kind: (10, 10) from walks alone, (10, 11) filled along a gradient."""
    asset = synthetic()
    asset.kind[10, 10], asset.kind[10, 11] = hc.KIND_WALKS, hc.KIND_GRADIENT
    asset.supported[10, 11] = False
    path = tmp_path / f"{MAP}.height.npz"
    hc.save_asset(path, asset)
    return asset, path


def test_the_payload_carries_each_cells_kind(tmp_path):
    asset, path = kinded(tmp_path)
    p = height_viewer.map_payload(MAP, path, None, "preview")
    flat = []
    for value, run in zip(p["kinds"][0::2], p["kinds"][1::2]):
        flat += [value] * run
    assert flat == asset.kind.ravel().tolist() and p["kind"] == "preview", "beside the source kind, not over it"


@needs_node
def test_the_hover_names_the_kind_and_falls_back_without_one(tmp_path):
    _, path = kinded(tmp_path)
    p = height_viewer.map_payload(MAP, path, None, "preview")
    cells = [cell(10, 10), cell(10, 11), cell(11, 10), cell(12, 10)]
    body = "function run(p, map) { return %s.map(function (c) { return H.kindWord(map, c); }); }" % json.dumps(cells)
    assert run_node(body, {"payload": p}) == ["from walks alone", "filled along a gradient", "from stands",
                                              "filled from neighbours"]
    del p["kinds"]                     # a payload from before the kinds: what `supported` says
    assert run_node(body, {"payload": p}) == ["supported", "filled from neighbours", "supported",
                                              "filled from neighbours"]


def test_the_page_has_the_slope_rules_layer():
    page = (WEBAPP / "scripts" / "height_viewer.template.html").read_text(encoding="utf-8")
    assert 'id="lyNew"' in page and '"lyTwo", "lyDrops", "lySame", "lyNew"' in page and "H.kindWord(map, cell)" in page
```

In that file's toy asset (`synthetic()`), row 12's first two cells are filled and everything else in the block is
supported; the second test's fallback list follows from that and from the one cell the helper marks unsupported.

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_height_viewer.py -q -p no:cacheprovider -k "kind or slope_rules"`
Expected: 3 failed: `KeyError: 'kind'`, a Node error `H.kindWord is not a function`, and the page assertion.

- [ ] **Step 3: The payload**

In `webapp/scripts/height_viewer.py`, add `"kind": (cg.GRID, cg.GRID)` to `SHAPES`, and in `map_payload`'s returned dict, after the `"unresolved"` line:

```python
        "kinds": rle(asset.kind.astype(np.uint8)),               # heights.KIND_*: how each cell got its height
```

In the module docstring's last paragraph, after "hover for each cell's floors", add "and how it got its height", and after "a same-height layer" add ", a layer marking the cells that only the slope rules produce (from walks alone, filled along a gradient)".

- [ ] **Step 4: The core**

In `webapp/scripts/height_viewer_core.js`, `prepare` decodes it (a payload without one gives `null`):

```javascript
  function prepare(p) {
    var n = GRID * GRID;
    return {maxFloors: p.max_floors, floors: Int16Array.from(p.floors), spread: Int16Array.from(p.spread),
            supported: rleDecode(p.supported, n), unresolved: rleDecode(p.unresolved, n),
            walk: rleDecode(p.walk, n), kind: p.kinds ? rleDecode(p.kinds, n) : null, raw: p};
  }
```

Add after `ground`:

```javascript
  // How a cell got its ground height (app/control/heights.py KIND_*). Without kinds (an older payload), what
  // `supported` says.
  var KIND_WORDS = ["no height", "from stands", "from walks alone", "filled from neighbours", "filled along a gradient"];

  function kindWord(map, cell) {
    if (!map.kind) return map.supported[cell] ? "supported" : "filled from neighbours";
    return KIND_WORDS[map.kind[cell]] || "kind " + map.kind[cell];
  }
```

and extend the `api` object's last line:

```javascript
             groundTop: groundTop, colour: colour, cellAt: cellAt, kindWord: kindWord, KIND_WALKS: 2,
             KIND_GRADIENT: 4};
```

- [ ] **Step 5: The page**

In `webapp/scripts/height_viewer.template.html`:

After the `lyFilled` checkbox line, add:

```html
    <label><input type="checkbox" id="lyNew"> from the slope rules (cyan: walks alone, orange: along a gradient)</label><br>
```

In `drawMarks`, between the `lyTwo` block and the `lyDrops` block:

```javascript
    if ($("lyNew").checked && map.kind) {
      c.lineWidth = 2;
      for (cell = 0; cell < GRID * GRID; cell++) {
        var kd = map.kind[cell];
        if (kd === H.KIND_WALKS || kd === H.KIND_GRADIENT) {
          c.strokeStyle = kd === H.KIND_WALKS ? "#00e5ff" : "#ff9800";
          c.strokeRect((cell % GRID) * CELL + 1, Math.floor(cell / GRID) * CELL + 1, CELL - 2, CELL - 2);
        }
      }
    }
```

In `describe`, the floor line names the kind for the ground floor (an upper floor is always from stands):

```javascript
      var s = "floor " + (f.floor + 1) + ": " + m1(f.z) + " (" + (f.floor === 0 ? H.kindWord(map, cell) : "from stands") + ")" +
              ", spread " + m1(f.spread);
```

Near the end of the script, the marks layers' listener gains the new checkbox:

```javascript
  ["lyTwo", "lyDrops", "lySame", "lyNew"].forEach(function (id) { $(id).addEventListener("input", drawMarks); });
```

- [ ] **Step 6: Run the viewer's tests**

Run: `PY -m pytest tests/replays/test_height_viewer.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 7: Look at it**

```
PY scripts\height_viewer.py --dir %TEMP%\valo-replay\heights-preview
```

Open the page it prints. Expected: Sunset loads; ticking the new layer outlines cells on its stairs; hovering one
reads "from walks alone" or "filled along a gradient". (A folder still holding version-1 previews prints
`WARNING <Map>: skipped (... height asset version 1 is not 2)` for those: rebuild them in Task 6.)

- [ ] **Step 8: Commit**

```bash
git add webapp/scripts/height_viewer.py webapp/scripts/height_viewer_core.js webapp/scripts/height_viewer.template.html webapp/tests/replays/test_height_viewer.py
git commit -F <message file>
```

Message: `Height viewer: how each cell got its height, and a layer for the cells the slope rules made`.

---

### Task 6: Judge the rules on the five maps

**Files:**
- Modify: `docs/superpowers/specs/2026-10-05-height-slopes-design.md` (a "Results" section; the status line)

**Interfaces:**
- Consumes: Task 2's frozen rounds (`%TEMP%\valo-replay\heights-frozen`), its old-rule builds (`heights-preview-old`) and `compare_height_builds.py`.
- Produces: nothing in code. The spec's "Results" section, and the owner's verdict.

- [ ] **Step 1: Build the five maps with the new rules, from the same frozen copy**

Empty `%TEMP%\valo-replay\heights-preview`, then for each of `Sunset`, `Haven`, `Lotus`, `Ascent`, `Summit`:

```
PY scripts\build_control_heights.py --map <Map> --blobs-dir %TEMP%\valo-replay\heights-frozen --preview --out %TEMP%\valo-replay\heights-preview
```

with `--preview-min-matches 1` on exactly the maps that had it in Task 2, Step 9. Keep each run's printed report.
No database is read. If a build prints `REFUSED: ... is not what was frozen`, the folder changed since Task 2:
stop; the comparison would mean nothing.

- [ ] **Step 2: Compare**

```
PY scripts\compare_height_builds.py --old %TEMP%\valo-replay\heights-preview-old --new %TEMP%\valo-replay\heights-preview
```

Exit 2 means a comparison was refused (different rounds, masks or preview rule, or a build without its checks):
fix the inputs and build again. Never work around a refusal by editing a report. Then read the output against the
spec's "How it is judged":

| Check | Passes when | If it doesn't |
| --- | --- | --- |
| Kill lines (4) | the command exits 0: no map's blocked share went up | stop; list the map's blocked examples from its report and tell the owner |
| Supported cells lost | `supported before, no height now` is a small share of the old supported cells on every map | report the count per map and, from the new report's `unresolved_why`, where they went. The prototype lost 107 of about 2,900 on one Sunset match; a much larger share means a rule is refusing ground it used to resolve |
| Flat ground (2) | on every map almost every surviving cell is within 0.2 m | report the percentiles. If the 5th percentile is at or under -0.3 m on several maps, "the lowest wins" is biting on level ground: propose a higher `LOW_PCT` to the owner with these numbers; don't change it alone |
| Supported, unresolved by reason | supported is up and `neighbours disagree` is down on every map | report the map where it isn't, with both lines. This is not a stop by itself: the prototype's one match showed no drop in `neighbours disagree` |
| Sunset's blocker | the 13-cell `neighbours disagree` area beside a two-floor cell is gone from `not ready` | say what is left of it (its cells and reasons, from the new report's `unresolved_areas`) |

- [ ] **Step 3: The owner looks**

```
PY scripts\height_viewer.py --dir %TEMP%\valo-replay\heights-preview
```

Give the owner the page's path and ask them to check, with the slope-rules layer on (spec, "How it is judged", 3):
stairs read as a run of steadily changing heights (start with Sunset cells x 26-28, y 76-81); ledge edges are still
two floors; nothing outlined in cyan or orange sits where no player can stand or under a ledge people only fall
from. This step is the owner's: record what they say, in their words.

- [ ] **Step 4: Record the results and close the spec**

In `docs/superpowers/specs/2026-10-05-height-slopes-design.md`, add `## Results (<date>)` after `## Measured`:
the dataset (the five `rounds` digests, which must be the ones recorded in "Measured"); the compare command's
full output in a code block; one line per row of the Step 2 table; the owner's verdict from Step 3; and any start
value that moved, with the numbers that moved it and the owner's approval of the move.

Change the status line to say what is true: `Status: built <date> (plan: docs/superpowers/plans/2026-10-05-height-slopes.md).`
followed by which of the plan's decisions D1 to D7 the owner approved and which are still open. Replace open
question 3 with the answer (decision D3). In the start-values table, set `WALK_ACC_MAX` to its value and add
the rows for the constants of decision D6. In section 2, describe bands as built (decision D4); in section 3,
add that the crossing run must be on the two anchor floors (decision D5).

- [ ] **Step 5: Full suite, and commit**

Run: `PY -m pytest tests/replays -k "not pg" -q -p no:cacheprovider`
Expected: only the failures recorded in the baseline, failing the same way.

```bash
git add docs/superpowers/specs/2026-10-05-height-slopes-design.md
git commit -F <message file>
```

Message: `Heights: the slope rules judged on the five maps, from one frozen copy of the rounds`.

---

## After this plan

- No map's heights go live because of it: an asset still has to reach the bar and be committed (or, after the companion plan, be built by the worker). `CONTROL_REVISION` 7 makes every stored round stale; the idle queue recomputes them, and a flat map's bytes come out the same.
- The companion plan (`2026-10-05-height-auto-rebuild.md`) starts from here. It relies on `all_ground` reading its rounds once (Task 3), on `HEIGHT_VERSION` 2, and on the report keys Task 3 adds.
