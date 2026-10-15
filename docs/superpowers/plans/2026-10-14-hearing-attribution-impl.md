# Hearing attribution and two vision fixes: implementation plan (2026-10-14)

Design: `docs/superpowers/specs/2026-10-14-hearing-attribution-design.md` (sections cited as D§). Paths are under
`webapp/` unless they start with `docs/`. Python: the main checkout's `webapp/.venv313`. Tests run from `webapp/`.

Pin tests (run by every item that touches control code or its outputs):
`tests/replays/test_control_reference.py` (toy digests in `tests/fixtures/control/reference_flat.json`, re-recorded
with `scripts/control_reference.py --toys --write` and a dated note in the test's docstring),
`test_control_blind.py` and `test_control_mollies.py` (read the same fixture), `test_control_format.py`,
`test_control_figures.py`, `test_control_isolation.py`, `test_control_cases.py`, `test_control_reasons.py`,
`test_control_pinned_inputs.py`.
Hearing consumers: `tests/coach/test_knowledge.py`, `tests/coach/test_query.py`, `tests/replays/test_control_causality.py`,
`test_control_engine.py`, `test_control_reveals.py`, `test_control_teleports.py`, `test_control_utility.py`,
`test_control_unknown.py`, `test_gap_patterns.py`, `test_gaps_locating.py`, `test_gaps_observe.py`,
`test_gaps_task.py`, `test_gaps_backshots.py`, `test_player_state_freshness.py`.
Collapse consumers: `tests/replays/test_control_floors.py`, `test_adaptive_ground.py`.

Don't add uppercase module constants in the control modules (`app/control/engine.py`, geometry, utility, mollies,
...): the revision's constants digest would move. A pin turning red from a rule change is expected: re-record it in
the same step.

## S1 (W7) Hearing attribution in the engine (D§3)

Files: `app/control/engine.py` (`RoundInputs` ~305-390, `locating_rows` 638-655; `Unknown.apply` 2202-2299;
`_locating` 2443-2478; `_enemies` 2577-2582; header 36-44). New tests in `tests/replays/test_control_hearing.py`,
following the toy-round pattern of `tests/replays/test_control_unknown.py` / `test_gaps_locating.py`.

1. `RoundInputs.carriers(gun, te) -> set[int]`: slots with a shot of `gun` at or before `te` this round (from
   `self.shots`); `gun is None` -> empty set.
2. In `Unknown.apply`, per side, before the per-enemy loop: `spotted` = enemies of the side whose holder exists and
   `spots[h.cell] or in_cone` (the test at 2286-2289, moved up); the in-loop check reads `slot in spotted`.
3. `_locating(rnd, side, slot, since, t, spotted)` and `_attributable(rnd, side, slot, te, spotted, gun=None,
   gunfire=False)`: `others = {s for s, tm in rnd.team.items() if tm != side and s != slot and rnd.alive(s, te)}`;
   True if `not others`, or `others <= spotted`, or (gunfire and `gun` is not None and
   `rnd.carriers(gun, te) & (others | {slot}) == {slot}`). Called for footsteps and gunfire only, **after** the
   `rnd.pos` / `rnd.miss` accounting and **before** `_heard_by`, so missing-data counts don't depend on it.
   Kill, plant, damage unchanged. A dropped sound adds nothing.
4. Header rule text (36-44) and `_locating` docstring updated.

Tests (red first): footsteps two enemies alive, other unseen -> no collapse; other spotted -> collapse; other dead
-> collapse; gunfire, gun only X fired by te -> collapse; both fired it -> none; the other fires it only after te
-> collapse; no gun, other unseen -> none; no gun, other dead -> collapse; a kill still collapses with others
unseen.
Pins: re-record the toy digests that move (`scripts/control_reference.py --toys --write`; docstring note
"Re-recorded 2026-10-14 for CONTROL_REVISION 16: a heard sound names nobody unless only one enemy could have
made it"). The historical midwall pins (`test_control_reference.py:68-176`, `MIDWALL_BEFORE_*`) patch old rules in:
also patch `ce.Unknown._attributable` to return True there (that is the old rule, and proves the old pins held it).

Check: the new tests, the hearing consumers, the pin tests.

## S2 (W8) Bug 3: one collapse rule for a cell's nodes (D§4.3)

Files: `app/control/engine.py` `collapse_states` (1234-1261) and the section-totals copy (~3077-3091).

1. Helper `_collapse_nodes(state, groups, n_groups)` over walkable nodes (`geo.walk_n`): per group, hi_a = max A
   code (1-3), hi_b = max B code (4-6) (order passive < safe < active, as `level_of` 2785-2789), contested = any
   node 7-8, active = any node in (A_ACTIVE, B_ACTIVE, CONTESTED_ACTIVE). Contested (CONTESTED_ACTIVE if active
   else CONTESTED) when contested or (hi_a and hi_b); else hi_a or hi_b; else NONE. Returns also which groups had
   any node, so empty groups keep today's value (adaptive: NONE; totals: unselected spaces stay out).
2. The adaptive branch (groups `node_cell`), the floors branch (every cell has a ground node) and the totals
   (groups `node_space`) call it.

Tests (red first): adaptive toy B_ACTIVE + B_SAFE sub-nodes -> B_ACTIVE; B_ACTIVE + NONE -> B_ACTIVE; A_ACTIVE +
B_SAFE -> CONTESTED_ACTIVE; floors B_SAFE + B_PASSIVE -> B_SAFE; NONE + B_SAFE -> B_SAFE; three floors B_SAFE,
B_PASSIVE, B_SAFE -> B_SAFE; totals: a leaf with B_ACTIVE + B_SAFE nodes adds its time to the B_ACTIVE row and
nothing to CONTESTED_ACTIVE.
Pins: `test_control_floors.py:262` and `:272` re-recorded to the new values, each with a comment "2026-10-14 run:
one team's nodes are that team's; contested needs both sides". Line 256 and `test_adaptive_ground.py:115-117`
(A against B) stay as they are.

Check: new tests, `test_control_floors.py`, `test_adaptive_ground.py`, the pin tests.

## S3 (W9) Bug 1: close the Sunset door's end slits (D§4.1)

Files: `app/static/data/control/tags.json` (Sunset `map_features`, feature-1 / bundle-5 closed footprint
~54383-54395; editorial `runtime_digest` ~54599), new test `tests/replays/test_sunset_door_seals.py`.

1. Test first (red): flat Sunset (`cg.load_geometry('Sunset')`), rasterise the closed door footprint as the runtime
   does (`features.py:70-105`), and assert no 8-connected open sight path from (400,465) to (420,465) inside
   x 396-431, y 430-500. And: bundle-5's `bundle_status` reasons are exactly
   `['moving panel sight requires a measured height asset']` (what base gives on flat geometry: no placement,
   bounds, state or overlap reason added).
2. Edit the footprint's end vertices in place (top edge to uv y <= ~4340 at x 4028-4061, bottom to >= ~4715),
   only as far as the raster needs to meet the walls; update the `runtime_digest` line with
   `app/replays/map_feature_schema.py:380` `runtime_digest`. Never re-dump the file: `git diff --stat` must show a
   handful of lines.

Check: the new test, `tests/replays/test_control_features.py`, `git grep -l "map_features" -- tests` files, pin tests.

## S4 (W10) Revision 16 and the judged cases (R4, R6)

Files: `app/replays/control_format.py:47`, `tests/replays/test_control_format.py:42-61` (`PINNED`),
`tests/replays/test_control_figures.py:91`, `tests/fixtures/control/unknown_cases.json`.

1. `CONTROL_REVISION = 16    # 16: anonymous hearing (R1/R2) and one collapse rule per cell`; `PINNED[16]` with a
   comment (the constants digest, `b3c6e0438e897969` if unchanged); `(18, 15)` -> `(18, 16)`.
2. Cases (fields per `test_control_cases.py:65-74`):
   - `sunset-r13-footsteps-name-nobody`: match `3b29b5e9-ba26-4b0b-bca3-8eda8d150215`, map Sunset, round 13, t 27.5,
     side A, expect unknown, cells [812,340],[844,388],[876,420],[980,380],[972,508],[820,540]; source: the owner,
     2026-10-14 (bug 4), the diagnosis numbers, and that it runs flat locally.
   - `sunset-r13-blue-room-not-contested`: same match, round 13, t 11.2, side B, expect state, states
     `["b_passive","b_safe","b_active"]`, cells [52,388],[60,388],[68,388],[100,388]; source: the owner, 2026-10-14
     (bug 3); passes flat even on base (its bite is S2's unit tests).
3. `scripts/control_cases.py --id <each new id>`, then the whole file once (public responses only).

Check: `test_control_format.py`, `test_control_figures.py`, `test_control_cases.py`; control_cases.py all pass.

## S5a (W11) Bug 4 pictures, gaps and back-shot counts (investigator, no commits)

In-process, from the run folder's `tmp_w2_run.py` and its cached inputs: "before" = patch
`engine.Unknown._attributable = lambda *a, **k: True` (the old rule; `w2_base_*.png` already exist); "after" = this
branch. Bug 4 round 13 27.5 s side A unknown, before/after picture; the gaps detector over round 13: gaps and
back-shots counts before/after, plus over two or three other rounds of the same match.

## S5b (W12) Bug 3 and bug 1 pictures, the other doors' sweep (investigator, no commits)

Bug 3 "before": `git show 571e656:webapp/app/control/engine.py` into a run-folder file, imported with
`importlib.util.spec_from_file_location`, its `collapse_states` against this branch's on the toy and on the
stored round-13 frame (flat locally: show the toy result plus the stored picture). Count, over round 13's stored
frames, the blue-only contested cell-frames the two variants would clear where node states are known (else say
not computable locally). Bug 1 "before/after": raster from `git show 571e656:webapp/app/static/data/control/tags.json`
vs the branch. The leak sweep over the other published door bundles (Summit, Abyss, Lotus): counts only.

## S6 Wrap-up

Update `docs/control-vision-bugs.md` (bugs 1, 3, 4 to Fixed with their commits; bug 2 stays open with the
question). Write the cards: the gun reading and the R1 readings (grouped), the Gaps/back-shots change, the bug 1
data fix, the bug 3 reversal, the revision bump, the bug 2 question. Full suite in parts (prereqs.json parts plus
the run's new test files), known failures compared by prefix.
