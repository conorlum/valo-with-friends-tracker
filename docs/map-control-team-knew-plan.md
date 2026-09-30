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

## Build order

1. Knowledge pass + `Tick(view=...)` + toy tests (an unseen lurker is absent; a lost enemy lingers passive for the
   fade then vanishes; a revealed enemy is placed; T's own players unchanged).
2. Encoding: the two streams, `DATA_VERSION` 2, reference decoder, JS decoder (reads 1 and 2).
3. Viewer picker and last-known markers.
4. Real-data look on two rounds computed locally (no DB writes).
