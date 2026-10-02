# Map control: heights from the replays, and a kept .vrf archive (design)

Settled with the user on 2026-10-01, while reviewing the Ascent sample (`6f12db3e-b2db-4bca-96e4-a837c85ba5a6`)
on `worktree-control-fixes` (control revision 3, unreleased). Revised the same day after an external review of
commit 9806e34 (13 findings, all checked against the code and all accepted) and four more decisions from the
user. Not built yet; the implementation plan comes after this spec is approved.

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

**A known risk for case 1.** Heights model floors, not railings or wall lips. Seen from a raised corridor, a
floor's edge hides the ground just below it and leaves the far ground visible: with an eye 1.6 m above a
floor 4 m up, a body 40 m away on the lower floor is hidden only from viewers more than about 14.5 m back from
the edge. If the corridor's real terrain doesn't do it, case 1 stays wrong after this work and gets its own
decision; it is not forced by a rule. It is checked on the first Ascent `.vrf` the parser sees (the sample's
own file isn't on the user's machine). The user chose to go ahead regardless: heights are the right model for
the tunnels, caves and trips either way.

## Decisions (the user's, 2026-10-01)

- **Heights only**, no paint tool. Until a map has height data it stays flat 2D, as today.
- **Data source:** store height on every upload from now on, and have friends re-upload the `.vrf` files still
  in their Demos folders (a re-upload replaces the stored rounds, `app/replays/store.py`).
- **Built by a command and committed**, like the sight and walk masks: control changes only when the user
  rebuilds and commits a map's heights.
- **Every floor per cell**, with a review picture for caves whose roof nobody stands on.
- **The engine keeps floors apart** (after review): walking, the unknown, spotting and Safe all work per floor.
  Seeing a bridge doesn't clear the tunnel under it. Only the picture on the site is 2D.
- **Uncertain terrain falls back to 2D locally, loudly** (after review): a cell the build can't resolve uses
  today's flat sight, and every such area is reported where the user will see it, to be fixed later.
- **Every map goes stale once** (after review): turning the height engine on bumps `CONTROL_REVISION`, and
  every round is recomputed once. Flat maps don't keep their rows.
- **Archive eviction by match date; deletion covers everything** (after review): when space runs low the
  oldest matches played go first; a deletion request removes the archived `.vrf` and the stored replay.
- **Keep the `.vrf` files on Render** (the account is free to use), so the next thing the condenser drops
  doesn't need another round of re-uploads.

## Parts

The work is five parts, built and shipped in this order. Part 1 is independent of the rest and can ship on its
own; parts 2-4 are each useful only with the ones before.

1. The `.vrf` archive on the worker.
2. Heights in the stored rounds (condenser revision 11).
3. The height build (`scripts/build_control_heights.py`) and its asset.
4. The per-floor engine: 2.5D sight, floors in walking, unknown, spotting and Safe, and trips that end where
   they meet the ground.
5. Cleanup and rollout.

### 1. The .vrf archive

Revised 2026-10-01 after the part-1 review (`docs/superpowers/plans/2026-10-01-replay-archive-plan-review.md`).

Today the worker writes each upload into a temp folder, deletes the folder before it publishes the result
(`replay_worker/server.py`, `_run`), keeps its job table in memory, and is never told whether the web app
stored the result. The web app marks an upload `stored` even when `store_replay` returned `kept_existing`,
i.e. this file was rejected in favour of another (`app/services/replay_upload.py`, `refresh_job`). The
archive needs all of that changed.

**The switch.** The archive is on only when `REPLAY_ARCHIVE_DIR` is set, is a mount point, and a
create-and-rename probe under it succeeds. Otherwise the worker behaves exactly as today: temp folders under
`REPLAY_WORKER_TMP`, deleted when the job ends, an in-memory job table, nothing kept; an ack answers
`{"archived": false, "reason": "archive off"}` and the web app records that and moves on. `/health` reports
`archive: {"enabled", "reason"?, "kept", "bytes", "oldest_played_at", "budget_bytes", "boot_id"}`. A failed probe
never crashes the worker: uploads keep working without the archive.

**The disk.** A Render persistent disk on the `replay-worker` service (`render.yaml`: `disk`, mounted at
`/var/replay`), 25 GB to start (decided in review, D4: 100 matches x the 200 MB cap plus 5 GB; a Render disk
can grow but never shrink). Only files under
the mount persist, and a service with a disk stops its old instance before starting the new one on deploy (no
zero-downtime deploy, one instance). The disk holds:

- `jobs/<job id>/`: every job's working folder (`upload.vrf`, the parser's `export/`, `job.json` with its
  state, and `result.json` when done). The export is deleted the moment the condenser returns, so a finished
  job holds only its small result.
- `pending/<job id>.vrf` + `<job id>.json`: a parsed upload waiting for the web app's acknowledgement.
- `archive/<match uuid>.vrf` and `archive/index.json`: per match, the accepted file's sha256, its size, the
  map, the match's date (`played_at`, sent by the web app in the ack), the time it was accepted, and the
  `replay_id` of the stored replay it belongs to.
- `tombstones.json`: the worker's copy of the deleted-match list (the web app's `replay_deletions` table is
  the authority, and pushes it).
- `acks/<job id>.json`: the answer given to each ack, so a repeated ack gets the same answer.

The image's user is uid 10001. A Render disk mounts root-owned, so the container starts as root, an entrypoint
`chown`s the mount to 10001 and then drops to it with `setpriv`; the server never runs as root. Verified on the
first deploy (no Docker on the development machine): `/health` says `archive.enabled: true`.

**Space.** A parse needs about 65 times the upload (`replay_worker/README.md`); uploads are capped at
181,035,000 bytes (`replay_upload_max_bytes`, `REPLAY_MAX_BYTES`), so one parse can need about 12 GB. The worker
parses one job at a time. Space is budgeted from the worker's own accounting, never from free space measured
mid-parse:

- `PARSE_RESERVE` = 70 x the cap (the running parse at its largest) + `queue_size` x the cap (uploads waiting).
- `archive budget` = the disk's total − `PARSE_RESERVE` − `ARCHIVE_SLACK` (5 GB) − the bytes in `pending/`.
- Eviction runs before each parse and after each archive write: while the archive's bytes exceed the budget,
  delete the archived file whose match was played earliest (`played_at`, else its accept time). Eviction
  never touches `jobs/` or `pending/`.
- The worker refuses a new upload (`503`, "the worker is busy, try again soon"; the upload page already shows
  the worker's reason) when the bytes held by `jobs/` and `pending/` plus the new upload would eat into the
  parse reserve, i.e. `jobs + pending + upload > total − ARCHIVE_SLACK − 70 x the cap`. The archive itself
  never refuses an upload; it is evicted instead.
- With a 25 GB disk the budget is about 25 − 12.7 − 0.9 − 5 ≈ 6.4 GB of archive, about 35 matches at the
  upload cap or about 70 at today's file sizes; `/health` says how many are kept and the oldest match date.

**Restarts.** A job's state lives in its `job.json`. On start the worker re-queues `queued` and `parsing` jobs
in creation order (a parse restarts from the beginning; one that no longer fits the queue fails with "please
re-upload") and reloads `done` and `failed` jobs with their results, so a deploy loses nothing that was
accepted. A job folder older than `UNCOLLECTED_TTL` (7 days) is deleted with a log line; so is a pending file
no one acknowledges within `PENDING_TTL` (2 days, since pending bytes come out of the archive's budget), and an
ack record after 7 days. Stray temp files in `archive/` are removed on start.

**The acknowledgement.** Right after it stores a job's result, the web app tells the worker what happened:

```
POST /jobs/<job id>/ack   {"match_uuid", "sha256", "outcome", "replay_id", "played_at"}
  outcome: stored | replaced | unchanged | kept_existing | failed
  sha256 / replay_id: the stored replay the DB holds now (null for failed)
```

- The worker checks the job id, the match uuid (from its own result) and, for `stored`/`replaced`/`unchanged`,
  that `sha256` equals its own hash of the upload; any mismatch is refused (`409`) and logged.
- Every mutation of `archive/` and `index.json` happens under one lock (acks, eviction, deletion, sync).
- `stored` / `replaced`, and `unchanged` when the match has no archived file: the pending file is archived. It
  is copied to a temp name in `archive/`, hashed from disk, and renamed into place (atomic on one volume),
  then `index.json` is rewritten the same way.
- **Newer wins.** The version is `replay_id`: every store or replace inserts a new `replays` row inside the
  store's advisory lock, so a larger id is the newer file. An ack whose `replay_id` is smaller than the
  archived entry's is a delayed one: its pending file is deleted and the archive is left alone.
- `unchanged` when the match already has an archived file: the pending file is deleted (same bytes).
- `kept_existing` / `failed`: the pending file is deleted; nothing is archived.
- A tombstoned match's ack is refused and its pending file deleted.
- Acks are idempotent: the same ack twice changes nothing and returns the same answer (from `acks/`).
- The web app records each upload's store outcome (`replay_uploads.store_outcome`) and the worker's answer
  (`replay_uploads.archive_ack`: null until the worker answers). A background thread in the web app (the
  `replay_control_remote` pattern) re-sends missing acks every few minutes for uploads finished within
  `PENDING_TTL`, so a lost ack or a worker restart is recovered. `kept_existing` uploads are shown as such,
  with the store's own reason ("a linked replay of this match already exists", or "the new recording did not
  link, so it doesn't replace the existing unlinked one"), not as plain stored.

**Re-parse from the archive.** A worker job kind, `reparse`: `POST /reparse {"match_uuid"}` copies the
archived file into a job folder and queues it behind every waiting upload; it runs today's parser and
condenser and returns the result exactly like an upload. The web app's admin routes create a
`replay_uploads` row for it and collect it with the same `refresh_job`, which stores it and acks it (outcome
`replaced` or `unchanged`; the archived file stays, since it is the same bytes, and its entry takes the new
`replay_id`). A local command `scripts/reparse_archive.py [--map M] [--since DATE] [--match UUID]` lists the
archive through the web app, then queues and polls one match at a time.

**Deletion on request.** `scripts/delete_replay_data.py <match uuid> [--reason R]` (operator only, through the
web app's admin route, authenticated by the `REPLAY_ADMIN_TOKEN` secret; never a public route):

- adds the match to `replay_deletions` (match uuid, time, reason) in the web app's DB: the tombstone;
- deletes the stored replay, its rounds, players and control rows;
- pushes the deletion to the worker, which deletes any pending and archived file for that match, records the
  tombstone, and drops the result of any job for that match as it finishes (a queued upload's match isn't
  known until it is parsed);
- from then on, a store of that match is refused inside `store_replay` (upload, reparse or local ingest), its
  ack is refused, and a reparse of it is refused, each with a clear message.

Tombstones are **pushed**, never pulled: the worker holds no secret and no route to the web app. The worker
reports a fresh `boot_id` on each start; whenever the web app's thread sees a new one, it pushes the full
tombstone list, and the worker deletes any archived or pending file it holds for a tombstoned match. That
covers a restored disk snapshot: Render snapshots persistent disks automatically, so a deleted file survives in
older snapshots until they expire, and a restored snapshot brings both the file and an older
`tombstones.json` back; the restore restarts the worker, and the next sync deletes it. The upload page and this
spec say plainly that deleted files can live on in snapshots for Render's retention period.

**The upload page's promise changes first.** It says today: "It is parsed on the site's private worker and
then deleted; only the minimap replay is kept." It will say the file is kept privately on the site's worker to
build site features (the oldest matches deleted when space runs out), is never shown or shared, and is deleted
with the match's replay on request (with the snapshot caveat). A `.vrf` holds the whole match (all ten
players' names, positions and timeline), more than the stored replay. The friends are told before the first
file is kept.

**Access:** the archive is reachable only through the worker (a private service); there is no download route.
Local scripts that need raw files keep using local `.vrf` files.

### 2. Heights in the stored rounds

Everything that can watch or stand gets a height. Missing heights are stored as missing, never as zero.

- **Players:** each track segment gains `"z"`: one value per sample, in decimetres of the game's world z,
  delta-encoded like `u` and `v`. A sample whose z the parser didn't give breaks the segment's z (the segment
  carries `z` only when every sample has one; a mixed segment is split).
- **Utility actors:** every utility entry gains its spawn `"z"` (`app/replays/extras.py` reads `location.z`
  next to x and y); a thrown entry gains `"z"` at the landing point.
- **Moving watchers:** a drone's (pawn's) `path` points gain z: `[t, u, v, z]`.
- **Trips:** both anchors' heights, `"z"` and `"end_z"`, carried through the endpoint pairing that already
  exists.
- **Decoders:** `app/replays/format.py` keeps `decode_segment`'s four-value return (t, u, v, yaw) for every
  existing caller, and adds `decode_segment_z` for the height-aware ones; the JS decoder (and the standalone
  page, which embeds it) ignores the new keys, as today's already does.
- `CONDENSE_REVISION` 10 -> 11, so the recipe changes and a re-upload or a reparse replaces the old rounds.
  `FORMAT_VERSION` stays 1: the new keys are optional, and old blobs have none.
- **Units and origin:** the world's z is taken to be centimetres like x and y (both test fixtures' x/y
  conversion matches; to be confirmed on the first stored rounds against a known drop on any map). The
  condenser stores world z as is (no map offset); the height asset carries the map's origin (below).
- **Size:** z changes little between samples, so delta-encoded it should cost a few percent of a round blob;
  measured and reported on the first rounds.

### 3. The height build

`scripts/build_control_heights.py --map Ascent` (read-only on the friends DB, through `with_friends_db.py
--read-only`), writing `app/static/data/control/<Map>.height.npz` and its entry in `index.json`.
`build_control_geometry.py` keeps a map's height fields when it rewrites that map's entry.

**The reference height.** Floors are measured in the player-position coordinate itself: a floor's height is
the height of a standing player's position sample there, not of their feet. That avoids a feet calibration,
which player samples alone can't determine. Every height in the engine is a position-z: an eye is
position-z + `EYE_M`, a body is position-z + `BODY_M`, and ground (for the horizon) is position-z −
`STAND_M`, where `STAND_M` is the position's height above the feet. `STAND_M` only affects how high a
ledge is; it starts at the game's capsule half-height if a source can be found, else 0.9 m, and is checked on
the first known drop. Utility heights are converted the same way: a device's z is its own, and its watch
point is device z + `DEVICE_EYE_M` (0 to start).

**Stands, not samples.** At 125 Hz, 20 samples are 0.16 s of anything, including a jump. The build first cuts
each player's track into **stands**: runs of at least `STAND_S` = 0.3 s in which the player's z stays within
`STAND_TOL_M` = 0.15 m of the run's median and the player is alive. Airborne movement, ropes, boosts and
falls don't make stands, whatever their sample count. A stand belongs to every cell its samples pass through,
with its median z. Rounds are identified as (match uuid, round number).

**Floors.** In each cell, stands are grouped by height (density, not single-linkage gaps: a group's members
lie within `FLOOR_TOL_M` = 0.5 m of the group median, and two groups must be at least `FLOOR_SEP_M` = 2.0 m
apart). A group is a floor if it has at least `FLOOR_MIN_STANDS` = 5 stands from at least 3 rounds in at
least 2 matches, so a Sage wall or other temporary platform used in one match doesn't become a floor.
Stands taken within `PLATFORM_R_M` of a live temporary platform (Sage wall, and others the plan lists from
the util catalog) are dropped first. A floor's height is its group's median; its spread (the group's 10th to
90th percentile) is stored too. A cell whose floor spread is more than `FLOOR_SPREAD_MAX_M` = 0.8 m (a steep
ramp, a mixed cell) is **unresolved**. A cell with more than 3 floors is refused and reported, never cut
down to 3.

**Unsampled cells.** A walkable cell with no floor takes a ground floor only if at least 2 of its neighbours
within `FILL_R` = 2 cells (by walking) have one and they agree within `FILL_TOL_M` = 0.5 m; it gets their
median, and no upper floors. Otherwise it is unresolved. Filling never averages across a drop. Cells off the
walk mask carry no height (walls stay the 2D sight mask).

**Floor connections.** For walking per floor (part 4), the build also records which floors of neighbouring
cells connect:

- **Observed:** a player's stand on floor a in one cell followed by a stand on floor b of the neighbouring
  cell, within `CONNECT_S` = 1.0 s, seen in at least 2 rounds, connects a to b. A step up of more than
  `STEP_UP_M` = 0.7 m seen only downward is one-way (a drop).
- **Inferred:** two neighbouring ground floors within `STEP_UP_M` connect both ways (where nobody walked).
- Ropes, boosts and teleports (Icebox's ropes, the map specials) are not inferred; existing 2D walk specials
  apply to every floor of their cells, and floors reached only through the air are listed in the report.

**Unresolved areas, loudly.** Unresolved cells use today's flat sight in the engine (part 4). The build
groups them into areas and reports each one: its size, its bounding box in minimap px, and why (no samples,
neighbours disagree, spread too wide, too many floors). They are printed as `WARNING` lines at the end of the
build, stored in `index.json`, drawn in red on the review picture, and `preview_control_live.py` prints a
warning when a viewer in the round being previewed stands in or looks through one.

**Readiness.** The report gives two shares of walkable cells: **visited** (any sample) and **supported** (a
floor by the rule above, not filled). On the 22 cached Ascent rounds the old, weaker rule gave 77.1% visited
and only 57.7% supported. The bar is `HEIGHT_SUPPORTED_MIN` = 60% supported plus no unresolved area larger
than `UNRESOLVED_MAX` cells (start 12) that touches a cell with two floors; the build refuses a map below it.
It reports the shares; it doesn't guess how many rounds are still needed.

**Report** (printed and in `index.json`): visited and supported shares, cells with 2 and 3 floors, the
unresolved areas, floors reached only through the air, the kill-line check (part 4), and a picture
`<Map>.height.png` under `%TEMP%` (not committed): the map coloured by ground height, upper floors outlined,
steep drops marked and unresolved areas in red, for the user to spot caves whose roof nobody stands on.

**Asset.** `<Map>.height.npz` holds `floors` (GRID x GRID x 3, int16 decimetres of position-z above
`origin_z`, −1 for none), `spread` (same shape), `unresolved` (bool), `supported` (bool), the floor
connections, `origin_z` (the map's lowest floor, in world decimetres), the units (`dm`), `STAND_M`, and the
build's stand and round counts. Its digest joins the map's geometry inputs (below).

### 4. The per-floor engine

All of this applies only to a map with a height asset. A map without one behaves exactly as today
(after the one global recompute).

**Nodes.** The engine's unit becomes a node: (cell, floor). A cell with one floor has one node, so a flat map
has exactly today's cells. Everything that is a per-cell array today (walk, unknown, regions, Safe, vision,
the visibility bitsets) is per node. The display collapses nodes to cells only when producing the control
layer.

**Walking per floor.** Neighbouring nodes are joined by the build's floor connections; a node's own cell's
other floors are not neighbours. The unknown spreads, and the walk distances run, over these connections.
`cant_walk_paint` and the specials apply to every floor of their cells.

**Spotting uses the enemy's real height.** An enemy is spotted when a viewer's eye has line of sight to that
enemy's body at their own z (their node), not when the viewer's mask covers their 2D cell
(today: `e.body[h.cell]`, `engine.py`). Seeing the bridge doesn't spot the player in the tunnel.

**Unknown and Safe per floor.** Seeing a node clears the unknown on that node only. Safe is what no node of a
team's unknown sees, from every node of it. Today's boundary shortcut (`comp_seen` checks only a component's
boundary cells) is wrong with heights: a raised interior node can see past a low boundary. It is not kept
unless a test shows it equals the check from every node on toy maps with heights and on real rounds; until
then every node is a source (with a measured cost; the per-tick visibility rows are cached, as today). The
same applies to the counterfactual Safe.

**Eye and body.** A cell's floor is seen if a body on it (position-z + `BODY_M`) is in line of sight of the
eye (position-z + `EYE_M`). Start values are those of a standing player, `EYE_M` = 0.7 and `BODY_M` = 0.3
above the position (i.e. about 1.6 m and 1.2 m above the feet if the position sits 0.9 m up); provisional,
tuned only with the blocked-sightline set below.

**The ray test** (`geometry.cast`), along the existing rays and steps, after the 2D wall and smoke checks as
now. Rays that are 2D-blocked stay blocked; heights only ever remove sight.

- **Order:** the source cell is excluded. At each step the target is tested against the horizon first, then
  the step's ground updates the horizon.
- **Ground:** the horizon is the steepest slope from the eye to the ground passed so far, where a cell's
  ground is its lowest floor's position-z − `STAND_M` − `LEDGE_M`. `LEDGE_M` = 0.1 m lowers obstacles a
  little so a flat floor doesn't hide itself. A target is seen if the slope from the eye to its body is at
  least the horizon.
- **Slabs:** an upper floor is a thin plate at its ground height. A ray crossing a plate's cell (not the
  source's or the target's) is blocked if the line's height entering the cell and leaving it lie on opposite
  sides of the plate. A ray whose eye and target are on the same side of a plate passes it.
- **Unresolved cells:** a ray segment that enters an unresolved cell drops the height test for the rest of
  the ray (today's 2D answer), and the tick records that it did, for the preview's warning.
- **Corners:** the existing 2D corner tolerance is unchanged; the height test runs on the same steps.

**Viewers and watchers.** A live player's eye comes from their own z in the round (revision 11 rounds); in a
round without z (revision 10 and older), from the ground floor of their cell, and the round is marked
`approximate heights` in its diagnostics until it is re-parsed from the archive or re-uploaded. Every watcher
gets a floor: the floor of its cell nearest at or below its z.

- A camera or turret watches from its spawn z + `DEVICE_EYE_M`; a drone from its path's z; a trip along its
  wire (below).
- Area watchers (alarmbots, Chamber traps) and the presence bubble reach nodes by walking on their own floor
  and see with the ray test like any other view.
- A watcher with no z (an old round, or the parser didn't give one) uses its cell's ground floor and marks
  the round `approximate heights`.

**Trips.** The wire runs between the anchors' heights, linear from one end to the other. It extends from each
end along its own direction, over the floor it was anchored on (the floor nearest at or below that anchor),
and stops where that floor's ground rises to within `TRIP_HIT_M` = 0.1 m of the wire, at a 2D wall, at a
floor that isn't connected to the anchor's, or at `TRIP_REACH_M` = 10 m; if it finds none of those, the end
stays where it is. A wire crossing an unresolved cell doesn't extend. On a 20% ramp with a wire 0.4 m above
the anchor's ground, the wire stops at 2 m.

**The visibility bitsets** (`build_visibility`, node to node) have one row per node. `cache_key` takes the
height digest.

**Freshness.** Turning the engine on bumps `CONTROL_REVISION` (3 -> 4, or the next unreleased number), so
every round of every map is recomputed once. A height asset's digest joins that map's geometry inputs: in
`geometry_used` (`app/control/task.py`), and so in the round fingerprint (`control_format.fingerprint`), the
service's `geometry_inputs`, the visibility cache key, and the remote worker's geometry check. Rebuilding one
map's heights afterwards makes only that map's rounds stale. `compute_control.py` gains `--map`.

**The kill-line check** (Risk 1, at most 2% blocked) runs with heights, with its terms defined:

- **Qualifying kills:** both killer and victim have a position sample with z within 0.25 s of the kill, both
  stand on resolved cells, and the line is clear in 2D (walls and smokes) today. Excluded kills are counted
  by reason and reported next to the result.
- **Endpoints:** from the killer's eye to the victim's body and to the victim's head (position-z + `EYE_M`);
  a kill line is blocked only if both are.

Passing kill lines can only catch heights that block too much; they can't catch sight through terrain. So the
check also runs a **must-block set**: hand-listed sightlines that are impossible in game (case 1's corridor
to the far room among them, from the user's screenshots), kept in `tests/replays/control_must_block.json`
(map, viewer px and z, target px and z, source). A map's heights are committed only if its kill lines are at
or under the bar and its must-block lines are blocked, or the failures are listed and accepted by the user.
Walls stay infinitely tall and smokes stay columns; both are acknowledged limitations.

### 5. Cleanup and rollout

- **Revert the stair paint** from commit `ade5509`: the stair-edge stroke (x 324-355, y 308-311) and the two
  corner cells come out of Ascent's `cover_paint`, back to the user's own walls (kill lines 9/476, 5,220
  walkable cells). The 1x2 pocket rule from that commit stays. This ships with the current branch, before
  heights exist: the stairs are wrong in the meantime, which the user accepted.
- **Order:**
  1. The archive, its ack protocol, the deletion command and the upload page text (part 1); tell the friends.
  2. Condenser revision 11 (part 2), deployed; friends re-upload what their Demos folders still hold, and
     `reparse_archive.py` covers everything archived since step 1.
  3. Heights (part 3) for the first map that reaches the bar, with an Ascent `.vrf` checked for case 1 as
     soon as one exists; reviewed on its picture, its unresolved areas and its numbers.
  4. The engine (part 4) behind the asset's presence; previewed locally (`preview_control_live.py`) against
     the three cases above and the must-block set before anything is committed; then the one global
     recompute.
  5. Then map by map as each reaches the bar, with Fracture (tunnel), Haven and Icebox (caves) looked at in
     the preview, since the stacked floors are theirs.
- **Recompute:** a committed height asset makes its map's control rows stale; the user runs
  `compute_control.py --map <Map>` when ready, as with any geometry change.

## Testing

- **Format:** a segment with z round-trips; a blob without z decodes as before, and `decode_segment` still
  returns four values; a segment with a missing z sample is split; utility spawn z, drone path z and both
  trip anchors' z survive the condenser; the recipe changes with revision 11; the JS decoder ignores z.
- **Archive** (stub worker, as the upload tests do):
  - with no archive dir (or one that isn't a mount, or fails the probe) the worker behaves as today: the job
    folder is gone when the job ends, nothing in `pending/`, and an ack answers "archive off";
  - a stored job's file is archived only after its `stored` ack; a `kept_existing` or `failed` ack deletes it;
    a mismatched job id, match uuid or sha256 is refused;
  - the same ack twice is a no-op; a delayed older ack (smaller `replay_id`) doesn't replace a newer file; a
    lost ack is re-sent by the web app's next sync pass;
  - a restart re-queues queued and parsing jobs and reloads uncollected results; TTLs delete what nobody
    collects;
  - an upload that would eat into the parse reserve is refused; eviction deletes the earliest-played match
    first and never touches `jobs/` or `pending/`;
  - a reparse stores like an upload and leaves the archived file in place;
  - a deletion removes the stored replay, the control rows, the pending and archived files, and drops a
    finishing job's result; a later store, reparse or ack for that match is refused; a "restored" archive file
    for a tombstoned match is deleted on the next tombstone push (a new `boot_id`);
  - `kept_existing` uploads show the store's reason.
- **Height build** on toy maps (`tests/replays/control_toys.py`):
  - two floors at 0 and 5 m make two floors, and still do with airborne samples between them;
  - repeated jumps, a rope climb, a boost and a fall make no floor;
  - a platform at 3 m used in three rounds of one match makes no floor; near a Sage wall it is dropped;
  - crouching on a floor stays that floor (or is measured and its offset handled);
  - a 20% ramp inside a cell is one floor, a steeper one is unresolved;
  - a cell with four floors is refused and reported;
  - an unsampled cell between 0 m and 4 m neighbours is unresolved, not 2 m; between agreeing neighbours it
    fills;
  - floor connections: a walked step connects both ways, a drop seen only downward is one-way;
  - the bar refuses a map below it, and the report gives visited and supported separately.
- **Sight** on toy maps with heights, with exact geometry:
  - **Down from a ledge:** eye 5.6 m above the lower ground (upper floor 4 m + 1.6 m), ledge 2 m in front of
    the viewer, target body 1.2 m above the lower floor: at 3 m it is hidden, at 10 m it is seen;
  - **Up at a ledge:** eye 1.6 m on the lower floor, ledge face 5 m away, upper floor 4 m: a body on the upper
    floor 1 m past the edge is seen, one 15 m past it is hidden;
  - a continuous ramp hides nothing on itself; a viewer on the ramp sees up and down it;
  - a tunnel under a slab: the tunnel doesn't see the floor above, a viewer above doesn't see into it, and
    both see along their own level;
  - a corner within today's 2D corner tolerance behaves the same with heights;
  - a ray into an unresolved cell gives today's 2D answer and is recorded.
- **Per-floor engine** on toy maps:
  - an enemy in a tunnel is not spotted by a viewer who sees the bridge above;
  - seeing the bridge clears the bridge's unknown and not the tunnel's;
  - Safe with an elevated interior node: a target the boundary can't see but the interior can is not Safe
    (the reviewer's case); the boundary shortcut, if kept, matches the every-node check on all toy maps;
  - the unknown spreads up a connected step and not up a one-way drop.
- **No height asset:** on toy maps and on a real flat round, the control output is byte-identical to the
  reference engine at the commit before part 4, after only the revision number in the header is normalised.
- **Trips:** a wire beside a step extends to the step; on a 20% ramp it stops at 2 m (within a cell); between
  two walls it doesn't change; with nothing within 10 m it doesn't change; it doesn't extend onto a stacked
  floor above or below; across an unresolved cell it doesn't extend.
- **Real data:** the first Ascent match recomputed locally (round 3 at 79.5-82 s; round 4 at 25 s), the kill
  lines with their denominator and exclusions, and the must-block set.

## Out of scope

- A cave roof nobody stands on: shown on the review picture only; if one matters it gets its own decision.
- Railings, wall lips and anything else that isn't a floor (see case 1's risk).
- Smokes and walls in 3D (they stay columns, as now).
- Ropes, boosts and teleports as floor connections, beyond today's 2D specials.
- Downloading archived files to a local machine.

## Open questions for review

- Disk size: decided in review (D4): 25 GB, about 6.4 GB of archive after the parse reserve. It can grow later.
- **The site picture of a cell with two floors** (proposed): if its floors agree, that state; if one floor is
  unknown, unknown; otherwise contested. The tunnel's state is then visible on the site, at the cost of a
  bridge sometimes showing the tunnel's state.
- Start values, each tuned on real data and each change reported with numbers: `EYE_M`, `BODY_M`, `STAND_M`,
  `DEVICE_EYE_M`, `LEDGE_M`, `STAND_S`, `STAND_TOL_M`, `FLOOR_TOL_M`, `FLOOR_SEP_M`, `FLOOR_MIN_STANDS`,
  `FLOOR_SPREAD_MAX_M`, `FILL_R`, `FILL_TOL_M`, `CONNECT_S`, `STEP_UP_M`, `PLATFORM_R_M`,
  `HEIGHT_SUPPORTED_MIN`, `UNRESOLVED_MAX`, `TRIP_HIT_M`, `TRIP_REACH_M`, `PARSE_RESERVE`, `ARCHIVE_SLACK`.
