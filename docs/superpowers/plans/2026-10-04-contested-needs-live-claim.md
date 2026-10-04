# Contested Needs a Live Claim: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A cell is contested only when at least one team holds it with something live. Two inferred claims
against each other (memory, Safe, backfill, a knowledge picture's remembered enemy view) make it nobody's. An
enemy's live view of ground a player only remembers ends that memory.

**Architecture:** Two small changes to `webapp/app/control/engine.py`:
- `Tick.compose` gains one masking step after the existing "Safe against Safe is nobody's" block. It uses a
  new `Tick.live_claims(side, removed)`, built on the per-player `Tick.live` masks the engine already keeps
  (vision, watchers and the player's own cell, before memory is added).
- `Memory.apply` drops remembered cells that an enemy holds live this tick.

`scripts/control_cases.py` learns a `state` kind of judged case, so the user's three images are pinned as real
rounds. Built on one branch that combines the two unmerged stacks, because the user's Ascent case only appears
with the hearing engine (timing-gaps / gaps-viewer).

**Tech Stack:** Python 3.13, numpy, pytest; the engine's toy maps in `tests/replays/control_toys.py`.

**Spec:** the user's decision D5 = option B in the AFK run `2026-10-04-control-bugs` (review, 2026-10-04),
restated here because the run folder is outside the repo:

> B: a cell is contested only when at least one team holds it with something live (a view, a watcher,
> presence); two inferred claims (memory, Safe, backfill) against each other make it nobody's; and an enemy's
> live view ends remembered ground.

The user's own wording, from the review: "unknown should not produce contested areas when degrading passive
control area."

Evidence: three real cases where nobody on either side was looking, yet the ground showed as contested.

| Case | Round, time | Cells (px, 1024 minimap) | Claims today | Expected |
| --- | --- | --- | --- | --- |
| Ascent `6f12db3e-b2db-4bca-96e4-a837c85ba5a6` | r2, 15.7 s | two patches by B site | blue memory (S1mpLy) vs orange Safe | nobody's |
| Sunset `0f452716-1e90-4782-afba-29229fdab922` (image 15) | r7, 55.7 s | the mid band | A Safe vs B memory | nobody's |
| Sunset (image 14) | r7, 26.4 s | SW of SpidaChickens | A memory vs B memory | A's: an A player looked after B did, which ended B's memory |
| Sunset (image 14) | r7, 26.4 s | SW of SpidaChickens | A memory vs B's live view | B's |

The cells in Task 2 were picked with the gaps-viewer engine on 2026-10-04 (`tmp_pick_cells.py` in the run
folder). Every one of them is contested today.

The plan was dry-run on 2026-10-04 in a throwaway copy of `afk/2026-10-04-gaps-viewer`, with Task 3's and Task
4's code applied:
- all 11 new toy tests passed, and so did the 2 Task 3 tests added after the review;
- `test_control_unknown.py` and `test_control_engine.py` passed, 77 tests;
- all four cases passed on the run folder's saved blobs (`tmp_case_states.py`).

The "memory against memory" cells came out as A's, not nobody's. Task 4 explains why: an A player looked at
that ground after B's memory of it was made, so B's memory ended. The case expects A's.

## Global Constraints

- No production writes, no reparse, no full recompute. Check on the judged rounds only (`scripts/control_cases.py`);
  the user runs any recompute.
- `CONTROL_REVISION` stays **5**: it is unreleased (`origin/main` has 4), so its rules change in place. Add no
  new engine constant, so the constants digest pinned in `tests/replays/test_control_format.py` is untouched
  by Tasks 2-4.
- Keep the existing "ground both teams hold only as Safe is nobody's" block exactly as it is. The new rule is
  added after it, so the one edge it decides (a live passive view on ground Safe for both) is unchanged.
- This repo is public: commit no credentials. Match uuids are already public on the site and in the cases file.
- No heredocs in shell commands. Commit messages go in a file: `git commit -F <file>`.
- Python: `C:/Users/User/Documents/GitHub/valo-with-friends-tracker/webapp/.venv/Scripts/python.exe` (written
  `$PY` below). Run every command from the worktree's `webapp/` folder.
- Run test suites in the foreground, one at a time. The user may be gaming on the same machine.
- Known failure on both bases, always deselected:
  `tests/replays/test_replay_worker_control.py::test_control_off_answers_404_and_parsing_is_untouched`.

## Review Focus

1. **The counterfactual** (control credit removes one player): `live_claims` must leave out the removed player,
   or a player's credit for holding a contest disappears. The full and incremental paths must agree. Tests:
   `test_live_claims_leave_out_the_removed_player`, `test_the_counterfactual_applies_the_rule_full_and_incremental`
   (Task 3).
2. **Knowledge pictures:** `Tick.extra_passive` (an enemy's remembered view in a team's picture) is inferred,
   and a picture holds only the enemies the team sees. If either counts as live, the team pictures keep fake
   contests. Tests: `test_a_pictures_remembered_enemy_view_is_not_live`,
   `test_a_knowledge_picture_counts_only_the_enemies_the_team_sees_as_live` (Task 3).
3. **Watchers and presence are live:** a trip or turret watching ground, or a player's 4 m presence bubble,
   must keep a real contest and must end enemy memory. Tests: `test_a_live_claim_against_an_inferred_one_stays_contested`
   (Task 3, parametrized over view and watcher) and `test_an_enemy_holding_it_live_ends_remembered_ground`
   (Task 4, parametrized over active, passive and watch).
4. **Ended memory must not come back** when the enemy looks away. A cell is only re-remembered after being
   seen again. Test: `test_ground_an_enemy_saw_does_not_come_back_as_memory` (Task 4).
5. **Real rounds:** the toy maps can't show the hearing engine's Safe. Pinned by the four judged cases in Task 2,
   which must fail before Task 3 and pass after Task 4.

---

### Task 1: The combined base

The rule has to land where the hearing code is, and both unmerged stacks edit `engine.py`. So start from a
branch that has both.

**Files:**
- Modify (conflicts only): `webapp/app/control/engine.py`, `webapp/app/control/topology.py`,
  `webapp/tests/replays/test_control_format.py`, and any other file git reports.

**Interfaces:**
- Produces: a branch with `afk/2026-10-04-gaps-viewer` (hearing, gaps) and `afk/2026-10-04-control-bugs` (trip
  seal, KJ off, the sliver rule, `scripts/control_cases.py`, `tests/fixtures/control/unknown_cases.json`).

- [ ] **Step 1: Make the worktree.** Use `EnterWorktree` with name `control-live-claim`. It creates
  `.claude/worktrees/control-live-claim` on its own branch and moves the session into it. Then point the new,
  empty branch at the gaps-viewer stack:

```bash
git status --short          # expect nothing: a fresh worktree
git reset --hard afk/2026-10-04-gaps-viewer
git merge --no-ff afk/2026-10-04-control-bugs
```

- [ ] **Step 2: Resolve the conflicts.** Keep both sides' behaviour.
  - `engine.py`: keep the hearing code (`HEARING`, locating events) and the control-bugs changes:
    `Unknown._trips`, the `off` spans in the watcher build, `NARROW_ROOM_CELLS`, `Unknown._narrow`.
  - `topology.py`: keep both sides' changes to `spread`.
  - `test_control_format.py`: the `PINNED[5]` digest. Leave it for Step 3.

- [ ] **Step 3: Re-pin revision 5 in place.** Run:

```bash
$PY -m pytest tests/replays/test_control_format.py -q
```

  If `test_the_engine_constants_are_pinned_to_the_control_revision` fails, copy the digest from its assertion
  message into `PINNED[5]`. Add one comment line: "re-pinned in place: the control-bugs and gaps-viewer stacks
  combined (2026-10-04)". Run it again and expect a pass.

- [ ] **Step 4: Full suite in two halves** (about 8 minutes each):

```bash
$PY -m pytest tests/replays -q -k "test_control" --deselect tests/replays/test_replay_worker_control.py::test_control_off_answers_404_and_parsing_is_untouched
$PY -m pytest tests -q -k "not test_control" --deselect tests/replays/test_replay_worker_control.py::test_control_off_answers_404_and_parsing_is_untouched
```

  Expected: no failures. Fix merge mistakes until both halves pass.

- [ ] **Step 5: The existing judged cases still pass:**

```bash
$PY scripts/control_cases.py
```

  Expected: `2/2 cases pass` (`sunset-r4-corner-below-stub`, `sunset-r15-sliver-beside-osmin`).

- [ ] **Step 6: Commit the merge** (message in a file):
  `Merge the control-bugs stack into the gaps-viewer stack (base for the live-claim rule)`.

---

### Task 2: `state` judged cases, and the four cases (red)

**Files:**
- Modify: `webapp/scripts/control_cases.py` (`check_case`, `main`'s print line)
- Modify: `webapp/tests/fixtures/control/unknown_cases.json` (note, four cases)
- Test: `webapp/tests/replays/test_control_cases.py`

**Interfaces:**
- Produces: a case with `"expect": "state"` and `"states": [<names from control_format.STATE_NAMES>]`. It passes
  when every listed cell's state, at the tick at or before `t`, is one of `states`. `side` stays required for
  the file's shape but isn't read for `state` cases.

- [ ] **Step 1: Write the failing tests.** Append to `tests/replays/test_control_cases.py`:

```python
def test_a_state_case_checks_each_cells_state():
    data = _round()
    own = dict(_case("state", [[160, 200]]), states=["a_passive", "a_safe", "a_active"])
    assert cc.check_case(own, data)["passes"], "at 2 s x 160 is deep in A's start ground"
    nobody = dict(_case("state", [[160, 200]]), states=["none"])
    result = cc.check_case(nobody, data)
    assert not result["passes"] and result["wrong"] == [[160, 200]]


def test_a_state_case_off_the_walkable_ground_fails():
    result = cc.check_case(dict(_case("state", [[2, 2]]), states=["none"]), _round())
    assert not result["passes"] and result["wrong"] == [[2, 2]]


def test_a_state_case_checks_every_listed_cell_not_just_the_first():
    """x 160 is A's at 2 s; x 248, by the barrier, is in A's unknown by then (A's unknown has walked to x 240),
    so it isn't."""
    case = dict(_case("state", [[160, 200], [248, 200]]), states=["a_passive", "a_safe", "a_active"])
    result = cc.check_case(case, _round())
    assert not result["passes"] and result["wrong"] == [[248, 200]]
```

  In `test_the_committed_cases_are_well_formed`, replace the `expect` assertion line with:

```python
        assert c["expect"] in ("unknown", "clear", "state") and c["side"] in ("A", "B"), c["id"]
        if c["expect"] == "state":
            assert c["states"] and set(c["states"]) <= set(cf.STATE_NAMES), c["id"]
```

  and add `from app.replays import control_format as cf  # noqa: E402` to the imports.

- [ ] **Step 2: Run, expect a failure:**
  `$PY -m pytest tests/replays/test_control_cases.py -q`. The two new tests fail: `check_case` reads the
  unknown stream for every case.

- [ ] **Step 3: Implement.** Replace `check_case` in `scripts/control_cases.py`:

```python
def check_case(case: dict, data: bytes) -> dict:
    """{"passes", "wrong" (the listed cells that aren't as expected), "t_checked"} for one case against a
    round's stored control bytes (gzipped, as compute_task and control.bin give them). 'unknown'/'clear'
    cases read the side group's unknown; 'state' cases read the cell's state, which must be one of the case's
    `states` (control_format.STATE_NAMES). A cell off the round's walkable ground is wrong whatever is
    expected: it can't be checked."""
    from app.control.geometry import CELL, GRID
    from app.replays import control_format as cf

    header, streams = cf.unpack_data(data if data[:2] == b"\x1f\x8b" else gzip.compress(data))
    times = [tk / header["hz"] for tk in header["ticks"]]
    i = max([k for k, t in enumerate(times) if t <= case["t"] + 1e-9], default=0)
    cells = header["cells"]
    index = {c: k for k, c in enumerate(cf.walk_bitmap(header))}
    if case["expect"] == "state":
        frame = cf.decode_states(streams["states"], i + 1, cells)[i]
        allowed = set(case["states"])
        ok = lambda k: cf.STATE_NAMES[frame[k]] in allowed  # noqa: E731
    else:
        name = f"unknown_{case['side'].lower()}"
        if name not in streams:
            raise SystemExit(f"{case['id']}: the round has no {name} stream (computed before the unknown?)")
        unknown = cf.decode_masks(streams[name], i + 1, cells, [c[0] for c in header["unknown_checkpoints"]],
                                  slots=1)[i][0]
        want = case["expect"] == "unknown"
        ok = lambda k: bool(unknown[k]) == want  # noqa: E731
    wrong = []
    for x, y in case["cells"]:
        k = index.get(int(y // CELL) * GRID + int(x // CELL))
        if k is None or not ok(k):
            wrong.append([x, y])
    return {"passes": not wrong, "wrong": wrong, "t_checked": times[i]}
```

  In `main`, replace the PASS/FAIL print with:

```python
        want = "/".join(c["states"]) if c["expect"] == "state" else c["expect"]
        detail = "" if got["passes"] else f"; not {want}: {got['wrong']}"
        print(f"{verdict} {c['id']} (round {c['round']} at {got['t_checked']:.2f} s, side {c['side']}, "
              f"{len(c['cells'])} cells must be {want}{detail})")
```

  (delete the old `detail = …` line). Update the module docstring's second sentence to name the `state` kind.

- [ ] **Step 4: Run, expect a pass:** `$PY -m pytest tests/replays/test_control_cases.py -q`.

- [ ] **Step 5: Add the four cases.** In `tests/fixtures/control/unknown_cases.json`, add to `note`:
  "'state' cases list `states` (control_format.STATE_NAMES); each listed cell's state must be one of them."
  Then append:

```json
  {"id": "ascent-r2-memory-vs-safe-by-b-site",
   "match": "6f12db3e-b2db-4bca-96e4-a837c85ba5a6", "map": "Ascent", "round": 2, "t": 15.7,
   "side": "A", "expect": "state", "states": ["none"],
   "cells": [[932, 532], [956, 548], [964, 572], [980, 604], [684, 660], [684, 676]],
   "source": "the user, 2026-10-04 (review of the control-bugs run): 'areas that should not be contested ... unknown should not produce contested areas when degrading passive control area'. Two patches at the edge of blue's ground by B site: blue's claim is S1mpLy's memory, orange's is Safe (the hearing engine heard blue's footsteps at 8.4-9.0 s). Nobody on either side holds it live: nobody's (D5 B)."},
  {"id": "sunset-r7-safe-vs-memory-mid-band",
   "match": "0f452716-1e90-4782-afba-29229fdab922", "map": "Sunset", "round": 7, "t": 55.7,
   "side": "A", "expect": "state", "states": ["none"],
   "cells": [[540, 340], [468, 412], [540, 436], [532, 468], [508, 484]],
   "source": "the user, 2026-10-04 (image 15): the band from A-main across mid. A's claim is Safe, B's is SpidaChickens' memory; nobody is looking at it: nobody's (D5 B)."},
  {"id": "sunset-r7-latest-look-wins-sw-of-spida",
   "match": "0f452716-1e90-4782-afba-29229fdab922", "map": "Sunset", "round": 7, "t": 26.4,
   "side": "A", "expect": "state", "states": ["a_passive", "a_safe", "a_active"],
   "cells": [[468, 612], [452, 636], [452, 652], [436, 660]],
   "source": "the user, 2026-10-04 (image 14): contested today between NPrightdolphin's memory (A) and Sogeking's memory (B). An A player looked at it after Sogeking did, which ends B's memory (D5 B: an enemy's live view ends remembered ground), so it is A's remembered ground."},
  {"id": "sunset-r7-live-view-ends-memory-sw-of-spida",
   "match": "0f452716-1e90-4782-afba-29229fdab922", "map": "Sunset", "round": 7, "t": 26.4,
   "side": "A", "expect": "state", "states": ["b_passive", "b_safe", "b_active"],
   "cells": [[476, 612], [548, 628], [548, 636], [548, 644]],
   "source": "the user, 2026-10-04 (image 14): NPrightdolphin's memory against SpidaChickens' live passive view. B is looking at it, so A's memory of it ends: B's (D5 B)."}
```

- [ ] **Step 6: Confirm they are red on the combined base.** The first run computes two rounds (about 2 minutes):

```bash
$PY scripts/control_cases.py
```

  Expected: the 2 old cases PASS. The 4 new ones FAIL, and every listed cell is contested on the base. The
  report only names the failing cells.
  - If in doubt, check the states with
    `C:\Users\User\.claude\afk\2026-10-04-control-bugs\tmp_case_states.py <this worktree's webapp>`.
  - If a listed cell is not contested on this base (a merge changed the round), replace it with one that is,
    of the same claim class. `tmp_pick_cells.py` in the same folder lists each contested cell's claim types;
    point its `WEBAPP` at this worktree. Record the swap in the commit message.

- [ ] **Step 6b: The gaps baseline** (for Task 5 Step 3), taken before any rule code. First check that the
  folder `%TEMP%\valo-replay\6f12db3e-b2db-4bca-96e4-a837c85ba5a6-afk-live-claim-base` doesn't exist (if it
  does, add a suffix). Then:

```bash
$PY scripts/preview_control_live.py 6f12db3e-b2db-4bca-96e4-a837c85ba5a6 2 --tag afk-live-claim-base
$PY scripts/preview_gaps.py "%TEMP%\valo-replay\6f12db3e-b2db-4bca-96e4-a837c85ba5a6-afk-live-claim-base"
```

  Record round 2's predicted count in the run's `LOG.md`.

- [ ] **Step 7: Commit:** `control_cases: 'state' cases; the user's three D5 images as cases (red)`.

---

### Task 3: Contested needs a live claim (`compose`)

**Files:**
- Modify: `webapp/app/control/engine.py`: add `Tick.live_claims` next to `Tick._live_of`; in `Tick.compose`,
  add the new block right after the existing "ground both teams hold only as Safe" block; update the module
  docstring's **Contests** bullet.
- Test: `webapp/tests/replays/test_control_unknown.py` (append; its helpers `_lines`, `_band`, `open_hall`,
  `GRID`, `ce` already exist there)

**Interfaces:**
- Consumes: `Tick.live: dict[int, np.ndarray]` and `Tick._live_of(h) -> np.ndarray` (vision, watchers and the
  player's own cell, before memory), `Tick.team(side, removed)`.
- Produces: `Tick.live_claims(side: str, removed: int | None = None) -> np.ndarray`. It returns flat bool masks
  over `geo.n`, already limited to walkable cells.

- [ ] **Step 1: Write the failing tests.** Append to `tests/replays/test_control_unknown.py`:

```python
# ---------------------------------------------------------------- contested needs a live claim (D5, 2026-10-04)


def _relive(tk):
    """`_lines` swaps the holders after the tick was built: rebuild `live` from the new ones."""
    tk.live = {}
    for s, h in tk.holders.items():
        lv = h.active | h.passive | h.watch
        lv[h.cell] = True
        tk.live[s] = lv
    return tk


def _hall(a0=None, b5=None):
    """Open hall: A0 at (200, 250), A1 at (300, 280), B5 at (400, 200), each seeing exactly what's given.
    Nobody stands in the west band (x 96-160): a player's own cell is live, which would keep it contested."""
    geo = open_hall()
    z = np.zeros(GRID * GRID, bool)
    tk = _lines(geo, {0: ("A", 200, 250, z if a0 is None else a0), 1: ("A", 300, 280, z),
                      5: ("B", 400, 200, z if b5 is None else b5)})
    for h in tk.holders.values():
        assert not _band(geo, *WEST_X)[h.cell], "keep players out of the west band"
    return geo, _relive(tk)


def _remember(tk, geo, slots, cells):
    mem = ce.Memory(geo)
    for s in slots:
        mem.cells[s] = cells.copy()
    mem.apply(tk, tk.unknown)
    return mem


WEST_X = (96, 160)
CONTESTED = (ce.CONTESTED, ce.CONTESTED_ACTIVE)


def test_memory_against_safe_is_nobodys():
    """Ascent round 2 at 15.6 s (the user, 2026-10-04): one team remembers it, the other holds it as Safe."""
    geo, tk = _hall()
    west = _band(geo, *WEST_X)
    z = np.zeros(GRID * GRID, bool)
    tk.unknown = {"A": _band(geo, 380, 416), "B": z}     # B could be in the east strip; A nowhere
    assert tk.unknown_safe("B")[west].all() and not tk.unknown_safe("A")[west].any()
    _remember(tk, geo, [0], west)
    assert tk.holders[0].memory[west].all()
    assert (tk.compose()["state"][west] == ce.NONE).all(), "memory against Safe: nobody's"


def test_memory_against_memory_is_nobodys():
    geo, tk = _hall()
    west = _band(geo, *WEST_X)
    tk.unknown = {"A": _band(geo, 380, 416), "B": _band(geo, 200, 232)}    # both see the whole hall: no Safe
    assert not tk.unknown_safe("A")[west].any() and not tk.unknown_safe("B")[west].any()
    _remember(tk, geo, [0, 5], west)
    assert tk.holders[0].memory[west].all() and tk.holders[5].memory[west].all()
    assert (tk.compose()["state"][west] == ce.NONE).all(), "memory against memory: nobody's"


@pytest.mark.parametrize("how", ["view", "watcher"])
def test_a_live_claim_against_an_inferred_one_stays_contested(how):
    west = _band(open_hall(), *WEST_X)
    z = np.zeros(GRID * GRID, bool)
    geo, tk = _hall(a0=west if how == "view" else None)
    if how == "watcher":
        tk.holders[0].watch = west.copy()
        _relive(tk)
    tk.unknown = {"A": _band(geo, 380, 416), "B": z}     # B holds the whole hall as Safe
    assert np.isin(tk.compose()["state"][west], CONTESTED).all(), "A holds it live against B's Safe"


def test_a_pictures_remembered_enemy_view_is_not_live():
    geo, tk = _hall()
    west = _band(geo, *WEST_X)
    tk.unknown = {"A": _band(geo, 380, 416), "B": _band(geo, 200, 232)}
    _remember(tk, geo, [0], west)
    tk.extra_passive = {"B": west.copy()}                # B's picture: an A player's last view, remembered
    assert (tk.compose()["state"][west] == ce.NONE).all(), "a remembered view is inferred, not live"


def test_live_claims_leave_out_the_removed_player():
    west = _band(open_hall(), *WEST_X)
    geo, tk = _hall(a0=west)
    assert tk.live_claims("A")[west].all()
    assert not tk.live_claims("A", removed=0)[west].any(), "without A0 nothing of A's is live there"
    assert tk.live_claims("A", removed=5)[west].all(), "removing an enemy changes nothing of A's"


def test_the_counterfactual_applies_the_rule_full_and_incremental():
    """Control credit recomputes the tick without one player: the rule must hold there too, on both paths."""
    geo, tk = _hall()
    west = _band(geo, *WEST_X)
    tk.unknown = {"A": _band(geo, 380, 416), "B": np.zeros(GRID * GRID, bool)}
    _remember(tk, geo, [0], west)
    base = tk.compose()
    full = tk.compose(removed=1)["state"]
    inc = tk.compose(removed=1, base=base, full=False)["state"]
    assert (full[west] == ce.NONE).all(), "without A1, A0's memory against B's Safe is still nobody's"
    assert np.array_equal(full[west], inc[west]), "the incremental counterfactual agrees"


def test_a_knowledge_picture_counts_only_the_enemies_the_team_sees_as_live():
    from tests.replays.control_toys import blob
    geo = open_hall()
    rnd = ce.RoundInputs(blob({0: still("A", 120, 200, 180), 5: still("B", 400, 200, 0)}), geo)
    tk = ce.Tick(rnd, 1.0)
    tk.unknown = {"A": _band(geo, 300, 316), "B": _band(geo, 100, 116)}
    assert 5 not in tk.sees[0] and 0 not in tk.sees[5], "they face apart: neither sees the other"
    assert tk.live_claims("B")[tk.live[5] & geo.walk_n].all()
    kt = ce.Knowledge(rnd, "A").tick_for(tk, 1.0)
    assert 5 not in kt.holders, "A's picture has no unseen B player"
    assert not kt.live_claims("B").any(), "so nothing of B's is live in it"
```

  Expected before and after the change:
  - **Pass before and after:** `test_a_live_claim_against_an_inferred_one_stays_contested`. It guards against
    the rule reaching too far.
  - **Fail before, from the missing method alone:** `test_live_claims_leave_out_the_removed_player` and
    `test_a_knowledge_picture_…`. Both were checked passing with the planned code (2026-10-04 review).

- [ ] **Step 2: Run, expect failures:**
  `$PY -m pytest tests/replays/test_control_unknown.py -q -k "nobodys or not_live or removed_player or stays_contested or counterfactual or knowledge_picture"`.
  - The two `…_is_nobodys` tests, `…_not_live` and `…_full_and_incremental` fail: the cells come out
    `CONTESTED`.
  - `test_live_claims_leave_out_the_removed_player` and `test_a_knowledge_picture_…` fail with
    `AttributeError: 'Tick' object has no attribute 'live_claims'`.
  - The two `stays_contested` cases pass.

- [ ] **Step 3: Implement.** In `engine.py`, add to `Tick` directly after `_live_of`:

```python
    def live_claims(self, side: str, removed: int | None = None) -> np.ndarray:
        """Flat cells `side` holds with something live, without `removed`: a view, a watcher or a player's own
        cell (`live`, before Memory). Memory, Safe, backfill and a knowledge picture's remembered enemy view
        are inferred and aren't in it (the user's call, 2026-10-04: D5)."""
        out = np.zeros(self.geo.n, bool)
        for h in self.team(side, removed):
            out |= self._live_of(h)
        return out & self.geo.walk_n
```

  In `compose`, directly after the existing block that ends with `level["B"][both] = 0` (still inside the
  same `if self.unknown is not None:`), add:

```python
            # a cell both teams claim with nothing live from either (memory, Safe, backfill or a picture's
            # remembered view against each other) is nobody's: contested needs someone holding it (D5, 2026-10-04)
            idle = (level["A"] > 0) & (level["B"] > 0) & ~self.live_claims("A", removed) & ~self.live_claims("B", removed)
            level["A"][idle] = 0
            level["B"][idle] = 0
```

  In the module docstring's **Contests** bullet, after "Both teams claiming a cell (Q40, Q55)", insert: "when at
  least one of them holds it live (a view, a watcher, their own cell; 2026-10-04, D5): inferred claims
  against each other (memory, Safe, backfill) are nobody's".

- [ ] **Step 4: Run, expect a pass:** the same command as Step 2, then the whole file:
  `$PY -m pytest tests/replays/test_control_unknown.py tests/replays/test_control_engine.py -q`.
  `test_ground_safe_for_both_teams_is_nobodys` and `test_a_seen_player_does_not_contest_ground_they_only_remember`
  must still pass.

- [ ] **Step 5: Two of the four cases turn green:**
  `$PY scripts/control_cases.py`. Expected:
  - PASS for `ascent-r2-…` and `sunset-r7-safe-vs-memory-…`, and for the 2 old cases.
  - `sunset-r7-live-view-ends-memory-…` still FAILs, as contested: A's memory against B's live view.
  - `sunset-r7-latest-look-wins-…` still FAILs, as `none`: both memories are still there.
  - Task 4 fixes both.

- [ ] **Step 6: Commit:** `Map control: contested needs a live claim on at least one side (D5)`.

---

### Task 4: An enemy's live view ends remembered ground (`Memory.apply`)

**Files:**
- Modify: `webapp/app/control/engine.py`: `Memory.apply`, the `Memory` docstring, and the module docstring's
  **Memory (D6)** bullet.
- Test: `webapp/tests/replays/test_control_unknown.py` (append; uses `_Tk`, `_at`, `_halves` from the top of
  the file and the Task 3 helpers)

**Interfaces:**
- Consumes: `Holder.active`, `.passive`, `.watch`, `.cell`, `.team`. When `Memory.apply` runs, `passive` doesn't
  hold this tick's memory yet: `apply` is what adds it.
- Produces: no new names. After `Memory.apply`, a holder's `memory` holds no cell that an enemy holds live this
  tick.

- [ ] **Step 1: Write the failing tests.** Append:

```python
@pytest.mark.parametrize("how", ["active", "passive", "watch"])
def test_an_enemy_holding_it_live_ends_remembered_ground(how):
    """Image 14 (the user, 2026-10-04): A remembers ground that B is looking at now: it's B's, not contested."""
    geo, tk = _hall()
    west = _band(geo, *WEST_X)
    setattr(tk.holders[5], how, west.copy())
    _relive(tk)
    tk.unknown = {"A": _band(geo, 380, 416), "B": _band(geo, 200, 232)}
    mem = _remember(tk, geo, [0], west)
    assert not tk.holders[0].memory[west].any() and not mem.cells[0][west].any(), "B holds it live: A's memory ends"
    assert not np.isin(tk.compose()["state"][west], CONTESTED).any()


def test_an_enemy_standing_in_remembered_ground_ends_it_there():
    geo, tk = _hall()
    spot = tk.holders[5].cell
    remembered = np.zeros(GRID * GRID, bool)
    remembered[spot] = True
    tk.unknown = {"A": np.zeros(GRID * GRID, bool), "B": np.zeros(GRID * GRID, bool)}
    mem = _remember(tk, geo, [0], remembered)
    assert not mem.cells[0][spot], "B5 stands there"


def test_ground_an_enemy_saw_does_not_come_back_as_memory():
    geo = open_hall()
    west, _ = _halves(geo)
    z = np.zeros(GRID * GRID, bool)
    none = {"A": z, "B": z}
    mem = ce.Memory(geo)
    mem.apply(_Tk(0.0, _at(0, "A", geo, 120, 200, west), _at(5, "B", geo, 400, 200)), none)
    a1 = _at(0, "A", geo, 120, 200)
    mem.apply(_Tk(1.0, a1, _at(5, "B", geo, 400, 200, west)), none)
    assert not a1.passive[west].any(), "B looked at it: A's memory of it ends"
    a2 = _at(0, "A", geo, 120, 200)
    mem.apply(_Tk(2.0, a2, _at(5, "B", geo, 400, 200)), none)
    assert not a2.passive[west].any(), "and doesn't come back when B looks away"
```

- [ ] **Step 2: Run, expect failures:**
  `$PY -m pytest tests/replays/test_control_unknown.py -q -k "ends_remembered or ends_it_there or come_back"`.
  All five fail: A's memory survives the enemy's view.

- [ ] **Step 3: Implement.** In `Memory.apply`, replace the first two loops (from the `del self.cells[s]` loop
  through the `self.cells[h.slot] &= ~unknown[h.team]` line) with:

```python
        for s in [s for s in self.cells if s not in tick.holders]:
            del self.cells[s]                     # memory dies with its player
        # what each team holds live now (views, watchers, own cell; this tick's memory isn't added yet): an
        # enemy holding remembered ground live ends it (the user's call, 2026-10-04: D5)
        live: dict[str, np.ndarray] = {}
        for h in tick.holders.values():
            lv = h.active | h.passive | h.watch
            lv[h.cell] = True
            live[h.team] = live[h.team] | lv if h.team in live else lv
        for h in tick.holders.values():
            if h.slot in self.cells:
                self.cells[h.slot] &= ~(h.active | h.passive)    # seen again: live, not memory
                if unknown is not None:
                    self.cells[h.slot] &= ~unknown[h.team]       # an enemy could be there now
                for team, lv in live.items():
                    if team != h.team:
                        self.cells[h.slot] &= ~lv                # an enemy holds it live now
```

  Leave the second loop (adding memory to `passive`, remembering what is seen now) unchanged.
  - Docstrings: in `Memory`'s docstring, change "it drops what the team's unknown has reached" to "it drops
    what the team's unknown has reached or an enemy holds live".
  - Module **Memory (D6)** bullet: after "until the team's unknown reaches it", add "or an enemy holds it live:
    a view, a watcher, standing in it (2026-10-04, D5)".

- [ ] **Step 4: Run, expect a pass:** the Step 2 command, then
  `$PY -m pytest tests/replays/test_control_unknown.py tests/replays/test_control_engine.py -q`.

- [ ] **Step 5: All six cases pass:** `$PY scripts/control_cases.py`. Expected: `6/6 cases pass`.

- [ ] **Step 6: Commit:** `Map control: an enemy's live view ends remembered ground (D5)`.

---

### Task 5: The rest of the system still agrees

The new rule feeds the knowledge pictures and every per-player control figure; check them before handing over.
The gaps detector should be unaffected:
- the observer (`app/control/observe.py`) gets `Tick.live`, `Tick.view` and the unknown's entries, none of
  which hold memory;
- the engine calls it before `compose`.

Task 5 confirms that (review finding 7, 2026-10-04).

**Files:** none expected. A change found here gets its own fix-and-test commit.

- [ ] **Step 1: Full suite in two halves** (Task 1 Step 4's commands). Expected: no failures. If a test written
  for the old rules fails (a contest it expected between two inferred claims), decide from its docstring. If
  that test pins the user's earlier call, stop and ask; otherwise update it, quote the old and new assertion in
  the commit message, and keep the case it covered.

- [ ] **Step 2: Previews with the new rule, in run-owned folders.** `preview_control_live.py` empties its output
  folder (`%TEMP%\valo-replay\<uuid>-<tag>`) before writing. Its default tag (`rev5`) is a folder an earlier run
  owns, so always pass `--tag`, and first check that the folder doesn't exist. If it does, add a suffix
  (`-2`, `-3`, …) until it doesn't:

```bash
$PY scripts/preview_control_live.py 6f12db3e-b2db-4bca-96e4-a837c85ba5a6 2 --tag afk-live-claim
$PY scripts/preview_control_live.py 0f452716-1e90-4782-afba-29229fdab922 7 --tag afk-live-claim
```

- [ ] **Step 3: Gaps are unchanged.** Run `$PY scripts/preview_gaps.py "%TEMP%\valo-replay\6f12db3e-b2db-4bca-96e4-a837c85ba5a6-afk-live-claim"`
  and compare its round 2 predicted count with the base's (Task 2 Step 6b). Expected: equal.
  - If they differ, investigate it as a regression. Neither change should touch the detector's inputs (see this
    task's intro). Don't bump `GAPS_REVISION` for it.
  - The gaps-viewer run's 67 is history from another base, not the comparison.

- [ ] **Step 4: Look at it.** Open the two `preview.html` pages from Step 2. Check Ascent round 2 at 15.7 s, and
  Sunset round 7 at 26.4 s and 55.7 s. The circled areas should be plain ground, not stripes:
  - nobody's at Ascent 15.7 s and Sunset 55.7 s;
  - B's at 26.4 s for the live-view cells, A's for the latest-look cells.

  Take screenshots for the hand-over.

- [ ] **Step 5: Hand over.** Report:
  - the branch and its commits;
  - the six cases;
  - the suite result;
  - the gaps count before/after;
  - the screenshots.

  Remind the user that the local `valomaths_gaps_view` copy and the `%TEMP%` previews were computed before this
  change and aren't flagged stale (the stored fingerprint doesn't cover engine code), so recompute those three
  rounds before judging them on the local site. Production recomputes everything anyway after the merge
  (revision 4 to 5).

## Notes (out of scope, for the user)

- **Viewer legibility:** a side group's unknown is drawn as hatch lines along "/" in the enemy's colour, the same
  direction as contested stripes. Ground in an unknown can still read as "contested" at a glance. That needs a
  viewer change (a different angle or pattern), not an engine one.
- **Two-floor cells:** `collapse_states` shows a cell as contested when its floors disagree, and "nobody's
  against held" counts as disagreeing. A floor this rule clears, under a floor one team holds, still shows as
  contested. That is the existing heights rule, unchanged here. Say if it shows up in a judged round.
- **The route-back contest (Q56) is not covered by this rule.** An enemy standing in a team's claimed ground
  contests their shortest way back to their team (`way_back`, on the cells that team claims, memory and backfill
  included), whoever is looking at it. D5 was about two claims meeting, so the plan leaves Q56 as it is (review
  finding 3, 2026-10-04). Whether a remembered route segment should count is the user's call.
- **Rejected: running the new rule when the tick has no unknown** (review finding 3). `Tick.unknown` is `None`
  only for toy ticks built without one; `TickRunner.step` sets it on every real tick, and knowledge pictures
  copy it. The existing Safe/Safe block is gated the same way, and the older toy tests rely on that fallback.
