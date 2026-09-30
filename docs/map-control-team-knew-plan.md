# Map control "what the team knew": plan (R3.3, P1)

The plan's stretch toggle (`docs/replay-map-control-plan.md`, "What the feature is"): "compute control from enemies
the team had seen or revealed, plus fading last-known positions, instead of true positions." Today control is the
omniscient analyst's view: every enemy's true position and view. This adds each team's own picture.

## What it shows

A second mode of the layer: **"As Team 1 knew it"** and **"As Team 2 knew it"** beside the true view. In team T's
picture:

- **T's own players** are exactly as today (T always knows where its own players are and where they look).
- **An enemy T can see now** (any T player's body view reaches them, or T's camera, drone, turret or trip sees
  them) or **has revealed** (a T reveal on them: Sova's bolt, Fade's haunt, a dart, a Cypher trap) is where they
  really are, with their real view.
- **An enemy T has lost sight of** stays at their **last-known position**, facing where they last faced, for
  `KNEW_FADE_S` (4 s) after T last saw them. Their vision still counts as the enemy's, but only as **passive**
  (T knows someone is there, not what they hold), and it stops at the fade.
- **An enemy T has never seen, or lost for longer than the fade,** isn't there at all: not a source of the enemy's
  free space and not a watcher. T assumes nothing about them.
- Enemy utility T can see (smokes, walls, mollies, the spike) counts as today: it's on everyone's screen. Enemy
  trips, cameras and turrets count only once T has seen them (they're placed, so "seen" once is enough while
  they last).

The state per cell is then computed by the same engine (`compose`) on these inputs, so the levels, safe space,
contested ground and the entry's way back all follow. Per-player control, coverage and heatmaps stay on the true
view only (the knowledge view is a picture, not a stat).

## Why it matters

The difference between the two pictures is the information gap: ground T thinks is safe but isn't (an unseen
lurker), and ground T concedes to an enemy who already left (a stale last-known). That's the point of the toggle.

## Architecture

- **Engine** (`app/control/engine.py`): a `Knowledge(rnd, side)` pass computed once per round: for each enemy, the
  intervals T sees them (from the per-tick `sees` sets plus watchers' views and reveals) and, between sightings, the
  last-known point and yaw with its fade. `Tick(rnd, t, view=side)` then builds the enemy holders from it instead
  of the tracks: true where seen, last-known (passive only) inside the fade, absent otherwise. One extra `compose`
  per team per tick, with no counterfactual: about +2 x the base cost (~50-90 ms a tick each), roughly +60% of a
  round's compute (the counterfactual is most of it).
- **Seeing**, exactly: T sees enemy e at tick t when e is alive and, for some alive T player, e's cell is in that
  player's `body` view (after flashes and nearsight), or e's cell is in a T watcher's view (camera in use, drone
  flown, turret) or trip area, or a T reveal on e is running.
- **Storage:** two more state streams in `data`, `knew_a` and `knew_b` (the same per-tick encoding as `states`),
  about +2 x the states stream (~+130 KB a round gzipped; rounds were ~190 KB): **`DATA_VERSION` 2** and
  **`CONTROL_REVISION` 3** (after R3.2's 2). The per-slot masks stay true-view only.
- **Viewer:** a picker next to the Map control toggle: True positions / As Team 1 knew it / As Team 2 knew it.
  The last-known enemies are drawn as hollow markers with a fading ring (the viewer already draws hollow
  last-known markers for track gaps). A viewer that gets a version-1 row shows the true view only.
- **Freshness:** the new revision makes every stored round stale; stale rows are still served (true view only, as
  they have no knowledge streams), and the user reruns `scripts/compute_control.py` (tier 3).

## Decisions for the user (tier 2, with cards)

1. **Fade time:** 4 s (Valorant's own minimap keeps a last-seen marker briefly; 4 s is a middle guess), tunable.
2. **Last-known enemies are passive only**, never active or safe-space sources beyond their passive view.
3. **"Seen" includes any teammate's body view, watchers and reveals, but not sound** (the replay has no footsteps
   data) and not the kill feed.
4. **Enemy placed utility counts once seen once.** A trip nobody on T has seen is unknown to T.
5. **Stats stay true-view only.**

## Risks

- Cost: +60% compute per round, on top of the recompute the revision already needs (about 3 core-hours today).
- Storage: rows grow from ~190 KB to ~320 KB on average; still inside the 150-400 KB the user accepted (Q36).
- It changes the stored format, so it lands on its own branch after R3.2, and old viewers must still read the
  true view: the decoder has to accept version 1 and version 2.

## Revised after the P2 review (2026-09-30): these override the sections above

A fresh reviewer found 2 blockers and 6 should-fixes; all applied.

- **B1 No format break.** `DATA_VERSION` stays 1: the knowledge picture is two *optional* streams, `knew_a` and
  `knew_b` (same per-tick encoding as `states`), with their own offsets in a header key `knew_checkpoints`. Old
  rows and old viewers read the true view exactly as today; the viewer shows the picker only when the streams are
  there. No further revision: this branch stacks on the space-taken branch, which already bumps
  `CONTROL_REVISION` to 2 (unreleased), so one recompute fills both. `compute_control.py --no-knew` computes
  without it (true view only).
- **B2 Unknown enemies are a region, not absent (tier 2, card D7).** "Absent" made the whole map T's safe space at
  every round start. Instead, an enemy T doesn't see now is a **possible-positions region**: from where T last saw
  them (or their spawn position at the round start, if never seen), through walkable cells T doesn't watch, out to
  the distance they could have run since (`KNEW_RUN_MPS` 6.75 m/s x the time since). The region is a source of the
  enemy's free space (so T's safe space ends where an unknown enemy could be) but has no vision of its own. A seen
  enemy is exact (true position and view); for `KNEW_FADE_S` (3 s) after a sighting they also keep their last
  view as passive. The "all enemies dead" rule applies only when they really are all dead.
- **S3 Vision is reused.** Knowledge is tracked causally inside `compute_round`'s tick loop (per team, a last-seen
  record per enemy: time, cell, position, yaw, body view), and each team's picture is a `compose` on a tick derived
  from the base tick's holders (no recasting): T's own holders unchanged; seen enemies as they are; recently lost
  ones with their stored last view as passive; everyone else only as region seeds.
- **S4 Utility and reveals.** An enemy's placed watchers count only while their owner is seen (a simplification:
  "seen once" utility would need per-object sighting; noted on D7). Reveals keep their `by` so "a T reveal on e"
  marks e as seen.
- **S5 Contests in the picture.** `sees` is recomputed from the known holders only (T can't be contested by an
  enemy it doesn't know is there); last-known views don't contest; damage on T's players still does (T feels the
  hits). Sightings are checked on control ticks only (0.5 s plus events), so a peek between ticks with no shot is
  missed.
- **S6 Viewer.** The header carries each team's sightings (`knew: {A: {slot: [[t0, t1], ...]}, B: ...}`) for
  markers: a lost enemy is a dashed diamond at their last-seen point, fading over `KNEW_FADE_S`. In a knowledge
  picture the true enemy dots are drawn dimmed, and the Control table and highlight stay true-view (labelled).
- **S7 Storage** is measured on real rounds in the build (p90 and max against 400 KB); the knowledge streams store
  only cells where they differ from the true state at that tick (a diff), which is usually small.
- **Cost:** about +55-70% of the per-round compute with vision reused.

## Build order

1. Knowledge pass + `Tick(view=...)` + toy tests (an unseen lurker is absent; a lost enemy lingers passive for the
   fade then vanishes; a revealed enemy is placed; T's own players unchanged).
2. Encoding: the two streams, `DATA_VERSION` 2, reference decoder, JS decoder (reads 1 and 2).
3. Viewer picker and last-known markers.
4. Real-data look on two rounds computed locally (no DB writes).
