# Handoff: the spike plant's start tap (a sound, a progress ring, and in map control)

Written 2026-11-17 from the owner's watch-through of replay `ee6e0ba7-9e9b-40e0-8e7e-6bc3945cb262` (Haven).
Item 8 in `replay-issues-ee6e0ba7/ISSUES.md`; screenshot `replay-issues-ee6e0ba7/08-r7-plant-progress-and-sound.png`
(R7 27.1 s, NPrightdolphin planting).

## What the owner wants

**A. Viewer**
1. When a player taps the spike to start planting, show it as a **loud sound**: starting a plant makes noise
   in game.
2. Draw a **progress ring** around the planter. A full ring is a full plant. If they stop early, the ring stays
   at how far the plant got.

**B. Map control (owner's ruling, 2026-11-17)**
The start tap is a **sound and is handled like gunfire**:
- It is heard by enemies within the spike tap's hearing range (`spike_tap_range_m` in
  `app/control/hearing.json`, unless measured otherwise).
- When heard, it places the planter within a **5 m radius** of where they stood. A planter is at least on
  site; the 5 m area narrows that.
- It counts whether or not the plant finishes, so an abandoned plant still gives away where the planter is.
- **Open question for the owner:** the other anonymous sounds (defuse tap, orb, footsteps) only name a player
  under R1, when only one enemy could have made the sound. Does the plant tap follow R1, or always name the
  planter? The assumption so far is R1, but confirm before building.

## What's there today

- Viewer: a `"planting"` spike state with the planter's slot and start time (`app/static/js/replay.js`: spike
  states ≈ 941, the spike-state builder ≈ 1424–1460 with `since`, the "is planting the spike" tooltip ≈ 3245).
  The ring's progress is (t − since) ÷ the plant time. When the state leaves `"planting"` without a plant, the
  ring keeps the fraction it reached.
- Control (`app/control/engine.py`): the planter is located only when the plant **completes** (`"plant"`,
  `PLANT_AREA_M = 5.0`, ≈ 636–641, 852). The only `"tap"` is a **defuse** start, taken from the planted spike's
  `defuses` (≈ 643–645). Taps and orbs are anonymous sounds within `SOUND_RANGE_M` and placed within
  `SOUND_AREA_M = 5.0` (≈ 165–170, 3151–3156). `control_reasons.py` already has a `"tap"` reason, but check
  whether a distinct reason (`"plant_tap"`) reads better in the viewer.
- The plant's start time and planter: check whether the condensed blob carries the `"planting"` interval on
  the control side (`player_state`), or whether control needs a new field from the condenser.

## Plan shape

Two PRs. The viewer part (A) is display-only and can ship alone. The control part (B) is a CONTROL revision
bump plus a re-parse of every replay, and it breaks pinned digest tests (memory "revision bump tests"). Preview
it on one or two rounds first (R7 of this replay), and run the slow lane before merging.

## Constraints

- Work in a worktree (`EnterWorktree`). Open a PR and give the owner the link. **Never merge.**
- The demo must keep working: the viewer change has to cope with replays that lack the new field.
