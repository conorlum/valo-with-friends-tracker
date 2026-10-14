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
someone who could have made the sound.

**Carries that gun** (case 3): the replay records no loadout, only each shot's gun (`util` rows `k == "shot"`,
`gun`). An enemy carries gun G in a round when any of their shots in that round is a G shot (`RoundInputs.shots`).
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
- No new constant, no new switch. `CONTROL_REVISION` 15 -> 16 (`app/replays/control_format.py:47`), once, in the
  last engine item, with its history line.

### 3.3 The "why the unknown changed" list

Today a footstep collapse writes a reason `(t, slot, "locate", "footsteps", "")`, shown on the Control tab under
the enemy's name. A dropped sound writes nothing, because it changed nothing (the register's "nothing when it
changed nothing"). An attributed sound keeps today's reason. Site copy, tier 1.

### 3.4 Gaps

`app/gaps/detect.py` reads the engine's locating events per tick (`rec.events`, `detect.py:276-303`) and its
`LEGACY_EVENTS` still name `footsteps` and `gunfire`. It has no attribution of its own, so it follows the engine
with no code change: a dropped sound no longer resets an enemy's unseen clock (`wait_over`). That can change what
the Gaps tab shows (more gaps, since an enemy stays unlocated longer). `app/services/replay_gaps.py` only reads
the hearing table. Whether the Gaps change is right goes on a card with a before/after count from the judged
round.

### 3.5 Tests

- Unit tests on a small synthetic round (the existing `tests/control` fixtures for `_locating`): a footstep with
  two enemies alive and the other unseen collapses nothing; with the other in live sight it collapses; with the
  other dead it collapses; gunfire with a gun only X has fired collapses; with a gun both fired, nothing; with no
  gun recorded, nothing. Red first.
- Kills, plant and damage still locate (an existing test, kept).
- The judged real-round case: bug 4, round 13 at 27.5 s, the right side stays in blue's unknown
  (`tests/fixtures/control/unknown_cases.json`, `scripts/control_cases.py`).
- Pins re-recorded in the same item: the control reference outputs move with the rule.

## 4. Bugs 1-3

Each is investigated as one case first (R3): reproduce with `scripts/preview_control_live.py`, find the cause, fix
only if clear and contained, otherwise a card with the evidence. Filled in after W3.

### 4.1 Bug 1: sight through a closed door (round 3, ~9.5 s)

(pending W3)

### 4.2 Bug 2: Clove's sight through a wall corner (round 7, 33.2 s)

(pending W3)

### 4.3 Bug 3: contested room with no orange claim (round 13, 11.2 s)

(pending W3)

## 5. Decisions

| # | Decision | Choice | Why |
| --- | --- | --- | --- |
| 1 | Live sight test | The engine's spotted test, at the tick | R1 says live sight; the engine already has one test for it |
| 2 | Gunfire judged at which tick | The tick that applies it (<= 0.5 s later) | Events are applied per tick; no per-event sight exists |
| 3 | "Carries" a gun | Fired it at any time in the round | No loadout in the replay; card |
| 4 | Reason for a dropped sound | None | It changed nothing |
| 5 | Gaps detector | No code change, follows the engine | It reads the engine's events; card on the Gaps change |

## 6. Risks

- Fewer locating events makes unknowns larger on every round: control shares move on every stored round after the
  recompute. Expected; the judged cases and the reference pins show the size.
- The gun reading can mis-attribute (3.1). Bounded: it only matters when a second enemy holds the same gun unfired.
- Moving the spotted test before the loop must not change order effects: it reads only the tick's holders and
  views, which the loop doesn't modify.
