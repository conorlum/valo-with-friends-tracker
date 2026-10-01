# Map control: heights from the replays, and a kept .vrf archive (design)

Settled with the user on 2026-10-01, while reviewing the Ascent sample (`6f12db3e-b2db-4bca-96e4-a837c85ba5a6`)
on `worktree-control-fixes` (control revision 3, unreleased). Not built yet; the implementation plan comes
after this spec is approved.

## Why

Map control's geometry is two flat masks per map (sight and walk, `app/control/geometry.py`). Three things on
one Ascent staircase (x 326-369, y 272-309 px) showed what flat can't do:

1. **Seeing down from above.** In round 3 at 79.5-82 s, attackers on the upper corridor (x 368-435,
   y 200-367) had the far room past the doorway at (145-187, 380) as passive sight from about 40 m and
   contested it. The user: "you cant see that in game due to elevation." 17 of the corridor's 158 cells see
   23-45 cells of that room in 2D.
2. **A trip anchored at height.** NPrightdolphin's trip runs from (433, 300) to (329, 300). Its east end is
   0.4 m from a wall; its west end is 7.5 m from one in 2D, because in game it is anchored on the stairs. The
   attackers' unknown walks round that end (round 4: it gets there at 24.2 s), so the stairs the trip holds
   show as nobody's.
3. **Paint can't fix it.** Cover paint on the stair edge stopped the sightline but blocks walking too
   ("players can walk on that space"), and one real kill line crosses that edge at x 359.

The user doesn't want to fix elevation by painting: there are many height changes across 13 maps, painting
them exactly is error-prone, and caves and tunnels (Fracture's tunnel under part of a site, caves on Haven and
Icebox) need more than one floor per cell. The replays already know every player's height: the parser reads
z for every position sample (`app/replays/condense.py`, `Sample.z`), and the condenser drops it.

## Decisions (the user's, 2026-10-01)

- **Heights only**, no paint tool. Until a map has height data it stays flat 2D, as today.
- **Data source:** store height on every upload from now on, and have friends re-upload the `.vrf` files still
  in their Demos folders (a re-upload replaces the stored rounds, `app/replays/store.py`).
- **Built by a command and committed**, like the sight and walk masks: control changes only when the user
  rebuilds and commits a map's heights.
- **Model B, every floor per cell**, with a review picture for caves whose roof nobody stands on.
- **Keep the `.vrf` files on Render** (the account is free to use), so the next thing the condenser drops
  doesn't need another round of re-uploads.

## Parts

The work is five parts, built and shipped in this order; each one is useful on its own.

1. The `.vrf` archive on the worker.
2. Heights in the stored rounds (condenser revision 11).
3. The height build (`scripts/build_control_heights.py`) and its asset.
4. 2.5D sight in the engine, and trips that end where the floor steps.
5. Cleanup and rollout.

### 1. The .vrf archive

- **Where:** a Render persistent disk on the `replay-worker` service (`render.yaml`: `disk`, mounted at e.g.
  `/var/replay-archive`, 50 GB to start). A service with a disk deploys with a short gap (no zero-downtime
  deploy) and runs as one instance; the worker already runs as one instance and an upload during a deploy
  is retried by the user.
- **What is kept:** after a job parses and the web app has stored the result (status `stored`), the worker
  moves the uploaded file to `<archive>/<match uuid>.vrf`. A failed parse or a refused file isn't kept. A
  second upload of the same match replaces the file only if the new one is stored.
- **Retention:** everything, until the disk passes 90%; then the oldest files are deleted until it is under
  80%. Nobody has to remember a purge. (The user can switch this to "newest N" at review.)
- **Re-parse from the archive:** a new worker job kind, `reparse`, takes a match uuid, runs today's parser
  and condenser on the archived file and returns the result exactly like an upload; the web app's collector
  (`replay_upload.collect`) stores it, so a condenser change reaches every archived match with no re-upload.
  A local command `scripts/reparse_archive.py [--map M] [--since DATE]` queues them through the web app (one
  at a time, the worker's queue of 5 shared with uploads; uploads first).
- **The upload page's promise changes first.** It says today: "It is parsed on the site's private worker and
  then deleted; only the minimap replay is kept." It will say the file is kept privately on the site's worker
  to build site features (oldest deleted when space runs out) and is never shown or shared. A `.vrf` holds the
  whole match (all ten players' names, positions and timeline), more than the stored replay. The friends are
  told before the first file is kept.
- **Access:** the archive is reachable only through the worker (a private service); there is no download
  route. Local scripts that need raw files keep using local `.vrf` files.

### 2. Heights in the stored rounds

- Each track segment gains `"z"`: one value per sample, in decimetres of the game's world z, delta-encoded
  like `u` and `v` (`app/replays/format.py` `encode_segment` / `decode_segment` grow an optional z).
- A trip's util entry gains `"z"` and `"end_z"` (its anchors' heights; `app/replays/extras.py` already reads
  the end point).
- `CONDENSE_REVISION` 10 -> 11, so the recipe changes and a re-upload replaces the old rounds.
  `FORMAT_VERSION` stays 1: the new keys are optional, old blobs have none, and the viewer ignores them.
- **Units:** the world's z is taken to be centimetres like x and y (to check on the first stored rounds
  against a known drop, e.g. the stairs above).
- **Size:** z changes little between samples, so delta-encoded it should cost a few percent of a round blob;
  measured and reported on the first rounds.

### 3. The height build

`scripts/build_control_heights.py --map Ascent` (read-only on the friends DB, through `with_friends_db.py
--read-only`), writing `app/static/data/control/<Map>.height.npz` and a row in `index.json`.

- **Samples:** every live player's position sample with z, from the map's stored rounds of revision 11 and
  up, into its GRID cell (8 px, 1.12 m on Ascent). Jumps, ropes and boosts are filtered by the floor rule
  below, not by guessing when a player is airborne.
- **Feet, not centre:** the parser's z is probably the body's centre. The offset to the feet is measured
  from the data (the lowest well-sampled height in a flat open room against the minimap's floor), not
  assumed, and stored with the asset.
- **Floors:** a cell's samples sorted by height split into floors at every gap of `FLOOR_GAP_M` = 2.0 m; a
  floor needs at least `FLOOR_MIN_SAMPLES` samples (start 20) from at least 3 different rounds, so a jump,
  a rope, a boost or an odd sample doesn't make one.
  Each floor is its median height. Up to 3 floors per cell. The lowest is solid ground; the ones above are
  thin slabs (a bridge, a tunnel's roof that is a site floor).
- **Unsampled walkable cells** take the ground height of their nearest sampled cells (by walking distance,
  inverse-distance weighted); they get no upper floors. Cells off the walk mask carry no height (walls stay
  the 2D sight mask).
- **Readiness:** the build refuses a map with under `HEIGHT_COVERAGE_MIN` = 60% of walkable cells sampled
  directly (tunable once real numbers exist), and says how many more rounds it thinks it needs.
- **Report** (printed and in `index.json`): sampled share, cells with 2 and 3 floors, the calibration offset,
  the kill-line check with heights (below), and a picture `<Map>.height.png` under `%TEMP%` (not committed):
  the map coloured by ground height, upper floors outlined, and steep drops marked, for the user to spot
  caves whose roof nobody stands on (the review list; nothing is flagged automatically).
- **Asset:** `<Map>.height.npz` holds `floors` (GRID x GRID x 3, int16 decimetres above the map's lowest
  ground, -1 for none), `sampled` (bool), the calibration offset and the build's sample count. Its digest
  joins the geometry digest that control rows are fingerprinted with, so rebuilding one map's heights makes
  only that map's rounds stale.

### 4. 2.5D sight, and trips

All of it applies only to a map with a height asset; without one, every function behaves exactly as today
(and the stored rounds of those maps don't go stale).

- **Eye and body:** a viewer's eye is at their feet plus `EYE_M`; a cell is seen if a body standing on one
  of its floors (feet plus `BODY_M`, a point between waist and head) is in line of sight. Start values
  `EYE_M` = 1.6 and `BODY_M` = 1.2, checked against the kill lines (a shooter's eye must see the victim) and
  the user's screenshots.
- **The ray test** (`geometry.cast`), along the existing rays and steps, after the 2D wall and smoke checks
  as now:
  - **Ground:** the classic horizon test. The ray keeps the steepest slope from the eye to the ground seen
    so far (plus `LEDGE_M`, a small allowance so a flat floor doesn't hide itself); a point on a floor is
    seen if the slope from the eye to the body there is at least that steep. One comparison per step.
  - **Slabs:** an upper floor is a thin plate at its height. The line from the eye to a target is blocked if
    it crosses a plate between the two (it passes from above to below a cell's slab height, or the reverse,
    within that cell). Plates are rare, so each ray keeps a short list of the ones it has passed.
  - A 2D cell counts as seen if a body on any of its floors is seen (control stays 2D).
- **Viewer heights:** a live player's eye comes from their own z in the round (revision 11 rounds); for a
  round without z (revision 10 and older), from the ground floor of their cell; such rounds are wrong under
  a bridge until re-uploaded or re-parsed from the archive. Watchers: a camera or turret at its floor plus
  its own height; a trip's line at its anchors' z.
- **The visibility bitsets** (`build_visibility`, cell to cell, for Safe and `seen_from`) are built per
  floor of each source cell; a cell with two floors has two rows. `cache_key` takes the height digest.
- **Presence bubble:** reach by walking as now; its sight check uses the heights like any other view.
- **Trips:** a trip's watched line extends from each end along its own direction until it meets a 2D wall
  or a floor more than `TRIP_STEP_M` = 1.0 m above the wire (the stairs above), at most `TRIP_REACH_M` = 10 m;
  if it finds neither, the end stays where it is. In game a trip always runs wall to wall; on a flat map
  its end can float where the anchor is a step.
- **Walking is unchanged:** heights never change the walk mask or the unknown's spread.
- **The kill-line check** (Risk 1, at most 2% blocked) runs with heights: each line from the killer's eye
  (their z at the kill) to the victim's body. A map whose heights block more than the bar is not committed
  until the cause is found.

### 5. Cleanup and rollout

- **Revert the stair paint** from commit `ade5509`: the stair-edge stroke (x 324-355, y 308-311) and the two
  corner cells come out of Ascent's `cover_paint`, back to the user's own walls (kill lines 9/476, 5,220
  walkable cells). The 1x2 pocket rule from that commit stays. This ships with the current branch, before
  heights exist: the stairs are wrong in the meantime, which the user accepted.
- **Order:**
  1. The archive and the upload page text (part 1), and tell the friends.
  2. Condenser revision 11 (part 2), deployed; friends re-upload what their Demos folders still hold.
  3. Heights (part 3) for Ascent first, the map with the screenshots; reviewed on its picture and numbers.
  4. The engine (part 4) behind the asset's presence; the Ascent sample previewed locally
     (`preview_control_live.py`) against the three cases above before anything is committed.
  5. Then map by map as each reaches the coverage bar, with Fracture (tunnel), Haven and Icebox (caves)
     looked at in the preview, since the stacked floors are theirs.
- **Recompute:** a committed height asset makes its map's control rows stale; the user runs
  `compute_control.py` for that map when ready, as with any geometry change.

## Testing

- Format: a segment with z round-trips; a blob without z decodes as before; recipe changes with revision 11.
- Archive: a stored job's file is moved to the archive; a failed one is deleted; the 90% rule deletes oldest
  first; a `reparse` job on an archived file stores like an upload (stub worker, as the upload tests do).
- Height build on toy maps (`tests/replays/control_toys.py`): samples on two floors make two floors; a jump
  doesn't; unsampled cells fill from neighbours; the coverage bar refuses; the calibration offset is found.
- Sight on toy maps with heights:
  - a viewer at the top of a step can't see the far floor below it, but can see the floor near the edge;
  - from below, the upper floor near the edge is seen, the far upper floor isn't;
  - a tunnel under a slab: the tunnel doesn't see the floor above, a viewer above doesn't see into it, and
    both see along their own level;
  - a map with no height asset gives exactly today's output (the pinned control digest is unchanged).
- Trips: a wire whose end floats beside a step extends to the step; one between two walls doesn't change;
  one with neither within 10 m doesn't change.
- Real data: the Ascent sample recomputed locally; round 3 at 79.5-82 s (the far room not contested),
  round 4 at 25 s (the stairs held behind the trip), and the kill lines with heights at or under the bar.

## Out of scope

- A cave roof nobody stands on: shown on the review picture only; if one matters it gets its own decision.
- Heights for walking (drops you can't climb back up are already `cant_walk_paint` and the specials).
- Smokes and walls in 3D (they stay columns, as now).
- Downloading archived files to a local machine.

## Open questions for review

- Disk size (50 GB to start) and the 90% / 80% rule, or "keep the newest N" instead.
- `EYE_M`, `BODY_M`, `LEDGE_M`, `FLOOR_GAP_M`, `FLOOR_MIN_SAMPLES`, `HEIGHT_COVERAGE_MIN`, `TRIP_STEP_M` and
  `TRIP_REACH_M` start at the values above and are tuned on real data, each change reported with numbers.
