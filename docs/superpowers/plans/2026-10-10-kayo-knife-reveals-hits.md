# KAY/O Knife Reveals Who It Hits — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A ZERO/point pulse spots the enemies it hits: KAY/O's team gets each one's exact position at the pulse. When the pulse's hit list is complete, every living enemy it missed is ruled out of its radius.

**Architecture:** The only change in behaviour is in `read_knives` (`webapp/app/control/utility.py`). It turns a pulse into `Info`s for each enemy separately, where it used to judge the whole team at once (zero, all, or nothing). A hit becomes a `locate`: the same operation a Neural Theft or recon reveal uses. The engine already treats a `locate` as a sighting, so it joins `Unknown.events` and resets the last-located time. A miss becomes an `exclude` of the radius disk. Bumping `CONTROL_REVISION` makes every stored round stale so the rounds get recomputed.

**Tech Stack:** Python 3.13, numpy, pytest (toy maps from `tests/replays/control_toys.py`), plain JS label table.

**Spec:** the owner's bug report (2026-10-10): "KAY/O knife shows players that are there so it counts as spotted in the radius of the knife." This **supersedes decision 10** of `docs/superpowers/specs/2026-10-05-replay-control-review-design.md` ("Partial hits do nothing, including no per-target shrinking"). Task 2 records the change there.

## Global Constraints

- Branch: `worktree-fix-kayo-knife-reveals-hits`, worktree `.claude/worktrees/fix-kayo-knife-reveals-hits`, based on `origin/main` @ `1d951c4`. This is the first of a series of separate bug-fix branches.
- Run Python from `webapp/` with the main checkout's venv: `/c/Users/User/Documents/GitHub/valo-with-friends-tracker/webapp/.venv313/Scripts/python.exe` (the worktree has no venv of its own). Below this is written `$PY`.
- A knife is only ever read from rows written by condenser revision 13 or later (`activation` + `pulse`). Rows without them still tell nothing. There is no re-condense; a control recompute is enough.
- Positive evidence (a recorded hit) needs `activation.state == "completed"` and a `pulse`. Negative evidence (a miss) also needs `activation.targets_complete`.
- An ulting Veto can't be suppressed. Every knife `Info` carries `detail={"needs": "suppress"}`, and the engine drops it for him (`engine.py`, the `utility.affects` filter). The reader no longer special-cases him.
- Never merge into `main`. Open a PR and hand back the URL.

## Review Focus

1. **A hit on an enemy with no position at the pulse.** Expect the enemy to be held inside the radius (`restrict`), not dropped and not located at a stale point. Pinned in Task 1 (`test_a_hit_without_a_position_is_held_inside_the_radius`).
2. **A hit recorded but the hit list incomplete.** Expect the hit still to locate and nobody to be excluded. Pinned in Task 1 (`test_an_incomplete_hit_list_still_spots_who_it_hit_but_clears_nothing`).
3. **An enemy who dies between the throw and the pulse, or one hit after their death.** Expect the dead to produce nothing. Pinned in Task 1 (`test_the_dead_dont_count_at_the_pulse`).
4. **The parser also emits a `reveal` row for the same hit.** That would give two `locate`s at the same instant and the same place, which is harmless to the region. The reasons list would show both, though. Checked in Task 3 Step 2 against a real knife round, without changing code unless it's seen.
5. **A located enemy's region really collapses in the engine,** not just in the reader's output. Pinned in Task 1 (`test_a_hit_spots_the_enemy_in_the_engine`).

---

### Task 1: Per-enemy knife reader

**Files:**
- Modify: `webapp/app/control/utility.py` (`read_knives`, ~line 213-245)
- Modify: `webapp/app/static/js/replay_control.js:488` (reason labels)
- Modify: `webapp/app/replays/control_format.py:47` (`CONTROL_REVISION`)
- Test: `webapp/tests/replays/test_control_knife_skye.py`, `webapp/tests/replays/test_control_reasons.py:105`, `webapp/tests/replays/test_control_veto.py:80-87`

**Interfaces:**
- Consumes: `rnd.pos(slot, t) -> (x, y) | None`, `rnd.height(slot, t)`, `rnd.alive(slot, t)`, `rnd.miss(msg, slot)`, `rnd.team`, `rnd.geo.m_per_px`, `ce.PX` (all existing, used the same way by `read_reveals`).
- Produces: reasons `"knife_hit"` (kind `locate`, or `restrict` when unplaced) and `"knife_miss"` (kind `exclude`). `"knife_zero"` and `"knife_all"` are removed everywhere.

- [ ] **Step 1: Rewrite the knife tests to the new rule**

In `webapp/tests/replays/test_control_knife_skye.py`, replace the module docstring's first line and every test from `test_a_knife_that_hit_nobody_rules_its_radius_out_for_every_living_enemy` through `test_an_ulting_veto_keeps_his_region_and_makes_all_impossible` with the following. Keep `test_the_knife_radius_is_the_replays_and_goes_through_walls` and `test_a_zero_knife_clears_its_radius_from_the_unknown` unchanged. They still hold: `knife([])` gives excludes, and `infos[0]` is a disk.

```python
"""KAY/O's knife and Skye's flash as information (W12; the 2026-10-05 review items 10, 14, 15; item 10 superseded
2026-10-10: a knife spots who it hits): hit, missed, proven complete versus missing or unresolved; Veto; KAY/O's own
sight."""
```

```python
def test_a_knife_that_hit_nobody_rules_its_radius_out_for_every_living_enemy():
    assert infos([knife([])]) == [(5, "exclude", "knife_miss"), (6, "exclude", "knife_miss")]


def test_a_knife_that_hit_everyone_living_spots_each_of_them():
    assert infos([knife([5, 6])]) == [(5, "locate", "knife_hit"), (6, "locate", "knife_hit")]


def test_a_partial_knife_spots_who_it_hit_and_rules_the_rest_out_of_its_radius():
    assert infos([knife([5])]) == [(5, "locate", "knife_hit"), (6, "exclude", "knife_miss")]


def test_a_hit_is_located_where_the_enemy_stands_at_the_pulse():
    b = blob(players(), t_end=10.0, util=[knife([5], t=3.0)])
    rnd = ce.RoundInputs(b, open_hall())
    [hit] = [i for i in rnd.infos if i.reason == "knife_hit"]
    assert (hit.t, hit.slot, hit.kind) == (3.0, 5, "locate")
    assert (hit.x, hit.y) == pytest.approx(tuple(rnd.pos(5, 3.0))[:2])
    assert hit.detail["needs"] == "suppress"


def test_the_dead_dont_count_at_the_pulse():
    assert infos([knife([5])], deaths={6: 2.5}) == [(5, "locate", "knife_hit")], "B6 died before it pulsed"
    assert infos([knife([5])], deaths={6: 3.5}) == [(5, "locate", "knife_hit"), (6, "exclude", "knife_miss")]
    assert infos([knife([5])], deaths={5: 2.5}) == [(6, "exclude", "knife_miss")], "a hit on the dead is no hit"


def test_allies_hit_are_not_hits():
    assert infos([knife([0])]) == [(5, "exclude", "knife_miss"), (6, "exclude", "knife_miss")]


def test_without_proof_of_a_pulse_nothing_is_inferred():
    assert infos([knife([5], state="unknown", complete=False)]) == []


def test_an_incomplete_hit_list_still_spots_who_it_hit_but_clears_nothing():
    assert infos([knife([], complete=False)]) == []
    assert infos([knife([5], complete=False)]) == [(5, "locate", "knife_hit")]


def test_a_legacy_knife_row_without_activation_tells_nothing():
    row = toy_ability("Grenadier", "E_SuppressionPulse", 300, 200, 0, t=2.0, t1=16.0, kind="GameObject", fx=[3.0])
    assert infos([row]) == []


def test_an_ulting_veto_keeps_his_region():
    assert infos([knife([])], veto=5) == [(6, "exclude", "knife_miss")]
    assert infos([knife([6])], veto=5) == [(6, "locate", "knife_hit")], "B5 couldn't be hit: not ruled out"


def test_a_hit_without_a_position_is_held_inside_the_radius():
    b = blob(players(), t_end=10.0, util=[knife([5])])
    rnd = ce.RoundInputs(b, open_hall())
    rnd.pos = lambda slot, t: None
    out = ut.read_knives(rnd)
    assert [(i.slot, i.kind, i.reason) for i in out] == [(5, "restrict", "knife_hit"), (6, "exclude", "knife_miss")]
    assert out[0].radius_m == pytest.approx(R_UV * ce.PX / 10000 * rnd.geo.m_per_px)


def test_a_hit_spots_the_enemy_in_the_engine():
    geo = open_hall()
    b = blob(players(), t_end=10.0, util=[knife([5], t=6.0, r=600)])
    out = {}

    class Watch:
        def on_tick(self, tick, unknown):
            out[round(tick.t, 6)] = {s: np.isfinite(r) for s, r in unknown.reached["A"].items()}

    ce.compute_round(b, geo, knowledge=False, observer=Watch())
    p = ce.RoundInputs(b, geo).pos(5, 6.0)
    own = geo.node_at(geo.cell_of_px(p[0], p[1]), None)
    assert out[6.0][5][own], "B5's region holds where the knife found him"
    assert out[6.0][5].sum() < out[5.5][5].sum(), "and collapses there"
```

In `webapp/tests/replays/test_control_reasons.py:105`, replace `"knife_zero", "knife_all",` with `"knife_hit", "knife_miss",`.

In `webapp/tests/replays/test_control_veto.py:80-87`, replace every `"knife_zero"` with `"knife_miss"`. The test is about the `needs` filter, and the reason string only has to be a real one.

- [ ] **Step 2: Run the tests to see them fail**

Run: `$PY -m pytest tests/replays/test_control_knife_skye.py tests/replays/test_control_reasons.py tests/replays/test_control_veto.py -q`
Expected: FAIL. The partial, all, dead, incomplete, Veto, unplaced and engine knife tests fail on the old `knife_zero`/`knife_all` output. `test_every_reason_the_engine_can_emit_has_words_in_the_panel` fails on `knife_hit`. The veto test still passes.

- [ ] **Step 3: Rewrite `read_knives`**

In `webapp/app/control/utility.py`, replace the whole of `read_knives` with:

```python
def read_knives(rnd) -> list[Info]:
    """KAY/O's ZERO/point (item 10, superseded 2026-10-10: the knife reveals who it hits), once it is proven to have
    pulsed (condenser `activation`): each living enemy it hit is spotted where they stand at the pulse (held inside
    its radius when they have no position then). With a complete hit list, each living enemy it missed is outside
    its radius (through walls). An incomplete list clears nothing. An ulting Veto can't be hit: his miss is dropped
    by the engine (needs: suppress), so his region is kept."""
    from app.control import engine as ce

    out = []
    for e in rnd.blob.get("util") or []:
        if e.get("k") != "ability" or f"{e.get('code')}_{e.get('name')}" != KNIFE or e.get("by") is None:
            continue
        act, pulse = e.get("activation") or {}, e.get("pulse")
        if act.get("state") != "completed" or not pulse or pulse.get("t") is None:
            continue
        side, t = rnd.team.get(int(e["by"])), float(pulse["t"])
        if side is None:
            continue
        living = _living_enemies(rnd, side, t)
        hit = set(pulse.get("hits") or []) & set(living)
        disk = None
        if pulse.get("r") is not None:
            disk = dict(x=e["u"] * ce.PX / 10000, y=e["v"] * ce.PX / 10000,
                        radius_m=float(pulse["r"]) * ce.PX / 10000 * rnd.geo.m_per_px)
        source = f"{KNIFE}@{e['t']}"
        detail = {"needs": "suppress", "hits": sorted(hit), "living": living}
        for slot in living:
            if slot in hit:
                p = rnd.pos(slot, t)
                if p is not None:
                    out.append(Info(t, side, slot, "locate", "knife_hit", x=p[0], y=p[1], z=rnd.height(slot, t),
                                    source=source, detail=detail))
                    continue
                rnd.miss("knife hit without a position (held inside its radius)", slot)
                if disk is not None:
                    out.append(Info(t, side, slot, "restrict", "knife_hit", **disk, source=source, detail=detail))
            elif act.get("targets_complete") and disk is not None:
                out.append(Info(t, side, slot, "exclude", "knife_miss", **disk, source=source, detail=detail))
    return out
```

Then fix the `CAPABILITIES["suppress"]` comment only if it mentions the old rule (it doesn't today; leave it).

- [ ] **Step 4: Labels and revision**

In `webapp/app/static/js/replay_control.js:488`, replace
`knife_zero: "knife hit nobody: not in its radius", knife_all: "knife hit everyone: inside its radius",`
with
`knife_hit: "hit by KAY/O's knife: revealed", knife_miss: "missed by KAY/O's knife: not in its radius",`

In `webapp/app/replays/control_format.py:47`:

```python
CONTROL_REVISION = 10    # 10: KAY/O's knife spots who it hits and rules its radius out for who it missed
```

- [ ] **Step 5: Run the tests to see them pass, then the whole control suite**

Run: `$PY -m pytest tests/replays/test_control_knife_skye.py tests/replays/test_control_reasons.py tests/replays/test_control_veto.py -q`
Expected: PASS.

Run: `$PY -m pytest tests/replays -q -k "control or gaps"`
Expected: PASS. A failure that names `CONTROL_REVISION` or a pinned fingerprint means a pin was missed. Fix the pin; don't revert the bump. Also grep for leftovers: `grep -rn "knife_zero\|knife_all" app tests` should print nothing.

- [ ] **Step 6: Commit**

```bash
git add app/control/utility.py app/static/js/replay_control.js app/replays/control_format.py tests/replays/test_control_knife_skye.py tests/replays/test_control_reasons.py tests/replays/test_control_veto.py
git commit -F <scratch file>   # "control: KAY/O's knife spots the enemies it hits" + attribution line
```

### Task 2: Record the superseded decision

**Files:**
- Modify: `docs/superpowers/specs/2026-10-05-replay-control-review-design.md:30`
- Modify: `docs/superpowers/plans/2026-10-05-replay-control-review-results.md:26`

- [ ] **Step 1: Edit decision 10 in the design spec.** Replace the line with:

```markdown
- **10:** ~~KAY/O knife gives unknown information only when it suppresses zero enemies or every living enemy. Zero hits clear the suppress volume for susceptible enemies; all hits restrict them to that volume. Partial hits do nothing, including no per-target shrinking.~~ **Superseded 2026-10-10 (owner):** the knife reveals the enemies it hits, so each living enemy hit is spotted (located) where they stand at the pulse. With a complete hit list, every living susceptible enemy it missed is excluded from the radius. An incomplete list spots its hits and clears nothing. Plan: `docs/superpowers/plans/2026-10-10-kayo-knife-reveals-hits.md`.
```

- [ ] **Step 2: Append to row 10's evidence cell in the results table:** ` Superseded 2026-10-10: hits are spotted, misses excluded (CONTROL_REVISION 10).`

- [ ] **Step 3: Commit** (`docs: knife decision 10 superseded`, via `git commit -F`).

### Task 3: Preview on a real round, then hand off

No code changes unless Step 2 finds a duplicate.

- [ ] **Step 1: Pick a round.** Ask the owner which match/round showed the bug. Failing that, find one: for a candidate match uuid from the replay list, fetch `https://valowithfriendstracker.onrender.com/replays/<uuid>/<n>.json` (gzip) for each round. Look for a `util` row with `code == "Grenadier"`, `name == "E_SuppressionPulse"`, `activation.state == "completed"` and a non-empty `pulse.hits`, and prefer a partial hit.

- [ ] **Step 2: Check for duplicate reveals.** In that blob, list `util` rows with `k == "reveal"` and `by` equal to the KAY/O's slot within 1 s of `pulse.t`. If any exist for the hit targets, tell the owner. Two `locate`s at one instant don't change the region, but the reasons list would name both. Don't dedupe unless asked.

- [ ] **Step 3: Preview** (per the "small sample before full recompute" rule):
  `$PY scripts/preview_control_live.py <uuid> <round> --tag knife-hits`. Open the page. At the pulse, the hit enemy's unknown collapses to their position with reason "hit by KAY/O's knife: revealed". When the hit list is complete, the missed enemies' unknown has a hole the size of the radius. The page names real players, so keep it local.

- [ ] **Step 4: Push and open the PR** (`gh pr create --body-file <scratch file>`). The body states that `CONTROL_REVISION` 10 makes every stored round stale. After merge, the owner runs `scripts/compute_control.py` through `with_friends_db.py` (dry run first) in a normal terminal. Also note that the unmerged `codex/ascent-audit-and-tagger-guidance` branch sets its own `CONTROL_REVISION = 9` for the Ascent features, a number main already uses. Whichever of the two merges second takes the next free number. Hand back the URL; never merge.
