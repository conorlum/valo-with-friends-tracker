# Hearing attribution and three vision bugs (design, 2026-10-14)

Input: `docs/control-vision-bugs.md` (four bugs, all in replay `3b29b5e9-ba26-4b0b-bca3-8eda8d150215`, Sunset).
Bug 4 is the priority; bugs 1-3 are each one case first. Settled answers (the owner, scoping 2026-10-14):

- **R1** Footsteps are anonymous. They shrink no named enemy's unknown, unless only one enemy could have made them:
  that enemy is the only one alive, or every other living enemy is in the listening team's live sight at that
  moment. Otherwise footsteps change no one's unknown.
- **R2** Heard gunfire follows R1, with the gun as a clue: it is attributed when only one living enemy carries
  that gun. Kills, the plant and gun damage to the team keep naming the enemy as today.
- **R4** No new switch; one `CONTROL_REVISION` bump (15 -> 16) for the whole run, which flags stored rounds stale;
  the worker recomputes them by itself after the deploy (`REPLAY_REPARSE_AUTO`).

## 1. Goal and scope

In: who a heard sound is pinned to in the control engine's per-enemy unknown (`app/control/engine.py`
`Unknown._locating`, `Unknown.apply`), the reasons it records, one revision bump, judged cases. Bugs 1-3: one
case each, fixed only if the cause is clear and contained.

Out: Impact scoring; the hearing table's figures (ranges stay as they are, provisional until the owner's in-game
tests); utility sounds that name their caster by being agent-specific (`utility.heard`: an Omen ult is Omen);
any recompute (the revision bump only flags rows stale); the database.

## 2. What the engine does today

The unknown is kept per enemy (`engine.py:36-44`). For each enemy `slot` of the listening side, `Unknown.apply`
(`engine.py:2241-2281`) collects `hits` from `_locating` (`engine.py:2443-2478`): the enemy's kills of the team,
the plant, gun damage to the team, gunfire within that gun's hearing range of a listening teammate, and, at the
tick, the enemy moving faster than `AUDIBLE_MPS` within `FOOTSTEP_RANGE_M` of one. The latest hit collapses that
enemy's region to an area round them (`FOOTSTEP_AREA_M` 10 m for footsteps, `SHOT_AREA_M` for gunfire). So a
footstep made by enemy X wipes X's region everywhere else, even though the listener can't tell X from his
teammates. With the per-enemy union that is the team's unknown, the far side of the map (where X could have been)
turns from unknown into the team's Safe ground, and against the other team's memory there into nobody's (the D5
rule): the grey area in bug 4.

## 3. Design

### 3.1 Who a sound can be pinned to

A sound made by enemy X at time te is **attributable** to X for side S when:

1. X is the only enemy of S alive at te; or
2. every other living enemy of S is in S's live sight at that moment; or
3. (gunfire only, R2) the gun narrows it: X is the only living enemy of S that carries that gun.

A sound that isn't attributable is dropped: it changes no one's unknown and records no locating event (so the
gaps detector, which reads those events, sees nothing either; see 3.4).

**Live sight at that moment** reuses the engine's own "spotted" test from `Unknown.apply` (`engine.py:2286-2289`):
the enemy's holder stands on a cell in one of the side's active views or a watcher's (`spots[h.cell]`), or is seen
at their own height in an active cone (`tick.direct`). It is evaluated at the tick being applied. Footsteps are
already judged at the tick (`t`). Gunfire events sit in `(since, t]`, at most one tick step (0.5 s, less when
events add ticks) before it: they are judged with the same tick's sight. Agent's reading; recorded in the
decisions log.

**Alive** for case 1 and 2 means alive at te (`rnd.alive`), so an enemy killed between te and t still counts as
someone who could have made the sound; having no holder at t, they count as unseen, which errs toward naming
nobody. On a round's first tick (`since = -inf`) gunfire can sit more than one step before the tick; the same
rule applies. A sound with no gun recorded gets no gun clue, but cases 1 and 2 still apply.

**Carries that gun** (case 3): the replay records no loadout, only each shot's gun (`util` rows `k == "shot"`,
`gun`). An enemy carries gun G at te when one of their shots at or before te in that round is a G shot
(`RoundInputs.shots`; R2's "at that time": a gun picked up later doesn't count before it is fired).
A shot with no gun recorded (`gun is None`) gives no clue. A gun carried but never fired is invisible to this
reading, so attribution by gun can be wrong when a second enemy holds the same gun and never fires it (most likely
for the free sidearm). This is a reading of R2 the owner may want a say in: it goes on the grouped card.

### 3.2 Code shape

- `RoundInputs.carriers(gun) -> set[int]`: the slots with a shot of `gun` in the round, cached once per round
  from `self.shots`.
- `Unknown.apply` computes, once per side and before the per-enemy loop, the set `spotted` of enemies of the side
  that are in live sight now (the same test as today's per-enemy check, moved up; the per-enemy check then reads
  the set). The per-enemy loop is unchanged otherwise.
- `Unknown._locating(rnd, side, slot, since, t, spotted)` keeps the kill, plant and damage rows as they are, and
  for `footsteps` and `gunfire` calls `_attributable(rnd, side, slot, te, spotted, gun=...)`, which implements 3.1.
  A dropped sound adds nothing to `hits`.
- The engine header's unknown rule (`engine.py:36-44`) and `_locating`'s docstring say who a sound names.
- No new constant, no new switch. `CONTROL_REVISION` 15 -> 16 (`app/replays/control_format.py:47`), once, after
  every engine change (hearing and bug 3), with its history line. Its pins: `tests/replays/test_control_figures.py:91`
  (`(18, 15)` -> `(18, 16)`) and `tests/replays/test_control_format.py:42-61` (add `16` with the unchanged digest,
  as 5 and 6 do).

### 3.3 The "why the unknown changed" list

Today a footstep collapse writes a reason `(t, slot, "locate", "footsteps", "")`, shown on the Control tab under
the enemy's name. A dropped sound writes nothing, because it changed nothing (the register's "nothing when it
changed nothing"). An attributed sound keeps today's reason. Site copy, tier 1.

### 3.4 Gaps

`app/gaps/detect.py` reads the engine's locating events per tick (`rec.events`, `detect.py:276-303`) and its
`LEGACY_EVENTS` still name `footsteps` and `gunfire`. It has no attribution of its own, so it follows the engine
with no code change: a dropped sound no longer resets an enemy's unseen clock (`wait_over`). That can change what
the Gaps tab shows (more gaps, since an enemy stays unlocated longer), and the back-shots too
(`app/gaps/backshots.py:130,136` read `wait_over`/`last_located`). The coach's knowledge events
(`app/control/encode.py:95,156` -> `app/coach/knowledge.py`) lose the dropped sounds the same way.
`app/services/replay_gaps.py` only reads the hearing table. Whether the Gaps change is right goes on a card with a
before/after count of gaps and back-shots from the judged round.

### 3.5 Tests

- Unit tests on a small synthetic round (patterns in `tests/replays/test_control_unknown.py` and
  `test_control_reasons.py`): a footstep with two enemies alive and the other unseen collapses nothing; with the
  other in live sight it collapses; with the other dead it collapses; gunfire with a gun only X has fired by then
  collapses; with a gun both have fired, nothing; a gun the other fires only later still collapses; no gun
  recorded with the other unseen, nothing; no gun with the other dead, collapses. Red first.
- Bug 3: red-first unit tests for both `collapse_states` branches (a toy adaptive geometry and the floors toy) and
  the section totals; the old pins at `test_control_floors.py:265-272` re-recorded with a comment citing this run.
- Bug 1: a test that rasterises the closed Sunset door on the committed Sunset masks and finds no open 8-connected
  path across the doorway, and that bundle-5 stays publishable.
- Pictures for the cards: local `scripts/preview_control_live.py` before/after images at bug 4 (round 13, 27.5 s)
  and bug 3 (round 13, 11.2 s), a Gaps before/after count for round 13, and bug 1's raster before/after.
- Kills, plant and damage still locate (an existing test, kept).
- The judged real-round case: bug 4, round 13 at 27.5 s, the right side stays in blue's unknown
  (`tests/fixtures/control/unknown_cases.json`, `scripts/control_cases.py`).
- Pins re-recorded in the same item: the control reference outputs move with the rule.

## 4. Bugs 1-3

Each is investigated as one case first (R3): reproduce with `scripts/preview_control_live.py`, find the cause, fix
only if clear and contained, otherwise a card with the evidence. Filled in after W3.

### 4.1 Bug 1: sight through a closed door (round 3, ~9.5 s)

**Cause (found).** The closed Sunset Door (`feature-1`, bundle-5, `map_pool_replay_v1`; footprint in
`app/static/data/control/tags.json` around lines 54383-54395) is rasterised by `features.py:70-105`, which keeps a
4 px paint cell only when its centre is inside the polygon. The panel covers px rows 448-479 at x 412-415, while
the doorway in `Sunset.sight.png` is open from y 446 to 482: a 2 px slit at the north end and a 3 px slit at the
south end. Chamber (slot 7, group B) sees 4 cells west of the door at 9.5 s, all through the south slit
((356,484), (364,484), (372,484), (260,492)). The door's state (closed since 6.783 s), its height band and the
sight cache are ruled out. A sweep from every walkable cell east of the door to 674 west targets: 1,849 leaking
pairs today, 0 with the panel over rows 444-483.

**Fix (tier 2, contained).** Extend the closed door's footprint in `tags.json` so it reaches the doorway's walls
at both ends (top edge to uv y <= ~4340 at x 4028-4061, bottom edge to uv y >= ~4715), checked by a test that
loads the Sunset masks, rasterises the closed footprint and asserts that no 8-connected open path crosses the
doorway (west (400,465) to east (420,465) inside x 396-431, y 430-500). Data, not code: the general fix (a
"touched" raster for closed door masks, `map_feature_motion.py:274`) also moves Ascent's doors and is left as a
note on the card.

**How it ships (P2 review, checked in code).** No owner step. The edit changes the feature key's tags digest, so
`resolve_current_geometry` reports `features_pending` (`app/services/replay_control.py:211-213`); the web service's
control dispatcher compiles the artifact on its next pass against the active Sunset height row
(`app/services/replay_control_remote.py:640` -> `app/services/control_feature_artifacts.py:151-162`, retried every
300 s) and pushes it to the worker (`replay_control_remote.py:533`). Heights don't read tags, so no height rebuild.
The stored editorial `runtime_digest` (`tags.json` ~54599) is re-derived with `ms.runtime_digest`. Risk: a
footprint pushed into the doorway's walls can fail `bundle_status`'s overlap or outside-bundle checks
(`features.py:1107-1120`), and bundle-5 would silently stop publishing (the door vanishes). The test therefore
also asserts bundle-5 stays publishable on Sunset. The same leak sweep runs read-only over the other published
door bundles (Summit, Abyss, Lotus) and its counts go on the card; they are not fixed in this run (one case).

### 4.2 Bug 2: Clove's sight through a wall corner (round 7, 33.2 s)

**Cause (found): not a sight leak.** Clove is slot 3, group B. At the stored 33.0 s tick, 49 of her cells sit in
the circled area: 0 active, 30 passive, 19 none, and a flat cast from her 33.0 s spot sees none of them. She did
see down that diagonal at 30.25-31.5 s (39 of the 49 cells reachable then, 26 with a clear straight line). Memory
(D6) keeps the thin strip she saw (34 cells), and backfill fills the room pocket the strip closes off (14 cells).
Gone by 35 s.

**Fix: none in this run (tier 3 question).** Whether a strip remembered from a ray a cell or two wide should count,
or should be able to seal a pocket for backfill, is a rule of the owner's (D6 memory, backfill). Card with the
pictures; nothing built.

### 4.3 Bug 3: contested room with no orange claim (round 13, 11.2 s)

**Cause (found): not tracers.** The stored 11.0 s frame marks 3 cells ([52,388], [60,388], [68,388]) as
`contested_active`; only Chamber (slot 7, group B) covers them, and they are in A's unknown. Bullets never feed
control (`geometry.py:233-237`: the bullet mask is the viewer's only). The contest comes from `collapse_states`
(`engine.py:1234-1261`), which turns a cell's several nodes (floors, or the adaptive height grid's sub-nodes) into
one stored state and calls the cell contested whenever the codes differ, even when every node is blue (here
`b_active` on the edge of Chamber's cone, `b_safe` beside it). 884 such cell-frames in round 13 alone. The local
flat engine can't show it (no Sunset height asset locally).

**Fix (tier 2, contained).** One helper, used by every place that turns a cell's nodes into one state: both
branches of `collapse_states` and the section-totals copy of the rule (`engine.py:3079-3091`), so the stored
picture, `knew_states` (`engine.py:3019`), `cells_now` (3072) and the section totals agree. The rule: a cell is
contested only when its nodes hold claims of both teams or a contested node (CONTESTED_ACTIVE when any node is
active, as today); otherwise it takes the highest state among its nodes, so one team's nodes, with or without
nobody's nodes beside them, give that team's highest state, and all nobody's gives nobody's. Contested means
claims from both sides, so one team plus nobody isn't a contest. This changes a rule pinned by
`test_control_floors.py:265-272` ("nobody's against held: they disagree", "two agree, one doesn't"), from the
heights spec's two-floor proposal (`2026-10-01-control-heights-design.md:464`, marked proposed, whose "one floor
unknown -> unknown" was never built). The card gives how many of round 13's 884 blue-only contested cell-frames
each variant clears (same-team only; same-team plus nobody's) and a before/after picture. Card as built.

## 5. Decisions

| # | Decision | Choice | Why |
| --- | --- | --- | --- |
| 1 | Live sight test | The engine's spotted test, at the tick | R1 says live sight; the engine already has one test for it |
| 2 | Gunfire judged at which tick | The tick that applies it (<= 0.5 s later) | Events are applied per tick; no per-event sight exists |
| 3 | "Carries" a gun | Fired it at or before te in the round | No loadout in the replay; R2's "at that time"; card |
| 4 | Reason for a dropped sound | None | It changed nothing |
| 5 | Gaps detector | No code change, follows the engine | It reads the engine's events; card on the Gaps change |
| 6 | Bug 1 | Data fix to the closed door's footprint, provisional | Cause clear; edits reviewed geometry, so a card |
| 7 | Bug 2 | Nothing built; a question card | It is the memory/backfill rule, the owner's |
| 8 | Bug 3 | One team's nodes (with or without nobody's) collapse to its highest state, in one helper used by all three places, provisional | Contested is two sides' claims; reverses a proposed pin, so a card |
| 9 | Judged cases | Bug 4 (unknown, side A, 6 cells) and bug 3 (state, 4 cells) into `unknown_cases.json` | R6. Both run flat locally (no Sunset height asset), so bug 3's passes even today: its bite is the unit tests; bug 1's check is the raster test |

## 7. P2 review (2026-10-14, afk-reviewer)

No blockers. All nine should-fixes applied above: the section-totals copy of the collapse rule (4.3); nobody's
beside one team is no contest (4.3); red-first bug 3 tests and the re-pinned floors test (3.5); the revision pins
and the bump after every engine change (3.2); back-shots and the coach's knowledge events as consumers (3.4);
"carries" at or before te (3.1); no-gun gunfire still under cases 1-2 (3.1, 3.5); how the door fix ships, with the
publishability check and the other doors' sweep (4.1); pictures for every card (3.5). Nits applied: test file
names; dead-before-t and first-tick notes (3.1); the flat-run note on the cases (decision 9).

## 6. Risks

- Fewer locating events makes unknowns larger on every round: control shares move on every stored round after the
  recompute. Expected; the judged cases and the reference pins show the size.
- The gun reading can mis-attribute (3.1). Bounded: it only matters when a second enemy holds the same gun unfired.
- Moving the spotted test before the loop must not change order effects: it reads only the tick's holders and
  views, which the loop doesn't modify.
