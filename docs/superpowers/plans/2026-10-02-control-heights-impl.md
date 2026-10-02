# Map control heights, parts 2-5: implementation plan

Spec: `docs/superpowers/specs/2026-10-01-control-heights-design.md` (parts 2, 3, 4, 5 and their Testing
bullets). Part 1 (the archive) is merged and is not touched.

## Branches

| Branch | Base | Carries |
| --- | --- | --- |
| `afk/2026-10-02-heights` ("heights") | `worktree-control-fixes` (`fc08370`) | every step |
| `afk/2026-10-02-heights-p2` ("p2") | `origin/main` (`a301653`) | part 2 only (W3-W5), by cherry-pick |

Every command below runs from the branch's `webapp\` folder with
`PY = C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv\Scripts\python.exe`.
`pytest` means `PY -m pytest -q -p no:cacheprovider`. One commit per step, subject `W<n>: ...`.

## Architecture (what the steps build)

**Part 2.** `format.encode_segment` takes an optional `z` list (decimetres of world z, delta-encoded like
`u`); `decode_segment` is unchanged (four values) and `decode_segment_z` returns `(t, u, v, yaw, z | None)`.
`condense.Sample.z` becomes `float | None` (today a missing z is stored as 0.0), and `build_segments` starts a
new run when z presence changes, so a segment has `z` for every sample or not at all. `extras` carries
`location.z`: `_Actor.z` and `_Equip` untouched; entry `"z"` (spawn), `thrown.z` (the projectile's spawn; the
landing point is the entry's own `z`), trip `"end_z"`, and pawn `path` points `[t, u, v, z]` (three values when
the sample had no z). All in world decimetres, ints. `CONDENSE_REVISION` 10 -> 11. `MIN_CONDENSE_REVISION`
stays 10 (control still reads revision 10 rounds, as approximate heights).

**Part 3.** A library module `app/control/heights.py` (numpy only, imported by the engine and the script) and
the command `scripts/build_control_heights.py`.

- `heights.stands(blob, geo)`: per player, runs of >= `STAND_S` with z within `STAND_TOL_M` of the run's
  median while alive -> `(slot, t0, t1, median z, cells passed)`. `heights.platforms(blob)`: live temporary
  platforms (Sage wall and the others listed from the util catalog) as `(t0, t1, x, y)`; stands within
  `PLATFORM_R_M` of one are dropped.
- `heights.build(rounds, geo, bar=True)` -> `HeightBuild`: floors per cell (density grouping, the
  stands/rounds/matches rule, spread, > 3 floors refused), fill, connections (observed, one-way drops,
  inferred), unresolved areas with reasons, visited and supported shares, readiness verdict, report dict.
- `HeightAsset` (`heights.save_asset` / `heights.load_asset`): the `.npz` of the spec plus `edges`
  (K x 4 int32: cell a, floor a, cell b, floor b; one row per direction) and `walk_sha`; `digest` is the
  sha256 of the file's arrays in a fixed order (12 hex), recorded in `index.json` as `height_sha`.
- The script: `--map M` reads rounds read-only (the DB through `with_friends_db.py --read-only`, a user
  step) or from `--blobs-dir <dir>` (local `*.json.gz` round blobs named `<match>/<n>.json.gz`).
  Without `--preview` it refuses a map below the bar and writes `app/static/data/control/<Map>.height.npz`
  plus the `index.json` fields. With `--preview --out <dir outside the repo>` it builds below the bar, writes
  the asset and picture to that folder only, and refuses an `--out` inside the repo. The picture
  (`<Map>.height.png`) always goes under `%TEMP%`.

**Part 4.** The engine's unit becomes a node. Node ids: a cell's lowest floor keeps the cell's flat index
(0 .. GRID*GRID-1); upper floors are appended after GRID*GRID in (cell, floor) order. A map without a
height asset has exactly today's ids, arrays and code path.

- `geometry.Geometry` gains `heights` (None when flat), `n` (node count), `node_cell`, `node_z` (metres above
  the asset's origin; NaN when unresolved), and `centres` extended to every node. `load_geometry` loads the
  asset when `index.json` names one (or an explicit `heights=` path, for previews).
- `geometry.cast(..., eye_z=None)`: with `eye_z` and heights, the 2.5D ray test (horizon, slabs, unresolved
  fallback) returning seen nodes; without, today's answer (every floor of a seen cell on a height map).
  `geometry.los(geo, a, b, smokes=())`: one line of sight with the same rules, used by the kill-line and
  must-block checks and to cross-check `cast` in tests. `build_visibility` has one row per node; `cache_key`
  takes the height digest.
- `app/control/topology.py`: `FlatTopology` (today's `ndimage` calls, verbatim, on GRID x GRID) and
  `NodeTopology` (the asset's edges as sparse graphs: 4- and 8-connected, one-way drops as directed links).
  Methods: `label`, `dilate`, `bfs`, `path_back`, `spread_step`, `neighbours`, `rim`. The engine calls only
  these; every `np.zeros(GRID * GRID)` becomes `np.zeros(geo.n)`.
- Engine rules on a height map: a holder's node is the floor of their cell nearest at or below their z (+
  0.5 m); their eye is their own z + `EYE_M`; spotting is `e.body[h.node]`; the unknown, memory, backfill,
  Safe and the counterfactual run per node; Safe checks every node of the unknown as a source (the boundary
  first, then the interior for targets still unseen, which is exact); watchers get a floor and an eye; trips
  extend along their anchor's floor. `RoundControl` stays per walkable cell: states collapse with the
  two-floor rule (floors agree -> that state; else contested), and the unknown and the per-player masks are
  the OR of a cell's floors. Stats (m2, m2*s) count nodes.
- `CONTROL_REVISION` 3 -> 4; `geometry_used`, `geometry_inputs` and the remote geometry check gain
  `height`; `compute_control.py --map`.

## Steps

### Part 5 (heights branch only)

**W2. Revert Ascent's stair paint.** Files: `app/static/data/control/tags.json` (Ascent `cover_paint` back to
`ade5509^`'s), `Ascent.sight.png`, `Ascent.walk.png`, `index.json` (rebuilt with
`PY scripts\build_control_geometry.py --map Ascent`). The 1x2 pocket rule (`DROP_PIECE_CELLS`, engine.py) is
not touched.
Check: the build prints `Ascent: 5220 walkable cells` and `kill lines 9/476`; `index.json`'s Ascent
`sight_sha` and `walk_sha` equal those in `git show ade5509^:webapp/app/static/data/control/index.json`;
`pytest tests/replays/test_control_assets.py tests/replays/test_control_geometry.py
tests/replays/test_control_unknown.py tests/replays/test_control_store.py` passes.

### Part 2 (heights, each commit cherry-picked to p2 and re-tested there)

**W3. Format.** Files: `app/replays/format.py`, `tests/replays/test_replay_format.py`. `encode_segment(...,
z=None)`, `decode_segment_z`, `CONDENSE_REVISION = 11`, the docstring. Tests: a segment with z round-trips;
one without decodes as before with z None; `decode_segment` returns four values for both; a z list of the
wrong length is refused; the recipe has `.c11.`.
Check: `pytest tests/replays/test_replay_format.py` passes.

**W4. Condenser tracks.** Files: `app/replays/condense.py`, `tests/replays/replay_synthetic.py` (z on synthetic
movement rows), `tests/replays/test_replay_condense.py`, `tests/replays/test_replay_util.py` (`c10` -> `c11`).
Tests: every segment of a synthetic match with z carries `z` in dm; a sample without `position.z` splits its
segment and the z-less part has no `z` key (never 0); gap-filled grid points interpolate z; the teleport rule
still breaks on a jump in x/y when z is missing.
Check: `pytest tests/replays/test_replay_condense.py tests/replays/test_replay_util.py
tests/replays/test_replay_streaming.py` passes.

**W5. Utility heights and the JS decoder.** Files: `app/replays/extras.py`, `tests/replays/test_replay_extras.py`,
`tests/replays/test_replay_viewer.py` (the node test of `replay.js`: a blob with `z` keys decodes to the same
tracks). Tests: an ability's spawn z, a thrown entry's z, a drone path's `[t, u, v, z]` and both trip anchors'
z survive the condenser; an actor with no `location.z` has no `z` key.
Check: `pytest tests/replays/test_replay_extras.py tests/replays/test_replay_viewer.py
tests/replays/test_replay_util.py tests/replays/test_replay_isolation.py` passes on both branches; then the
part-2 selection on p2: `pytest tests/replays -k "replay_ and not control"`.

### Part 3 (heights)

**W6. Stands and floors.** Files: `app/control/heights.py` (constants, `stands`, `platforms`, floor grouping),
`tests/replays/control_toys.py` (`z_track`: a toy track with heights; `height_round`), new
`tests/replays/test_control_heights.py`. Tests (spec's Height build list, first six bullets): two floors at 0
and 5 m with airborne samples between; jumps, a rope climb, a boost and a fall make no floor; a platform used
in one match makes no floor, and near a Sage wall the stands are dropped; crouching stays on its floor; a 20%
ramp is one floor and a steeper one is unresolved; four floors are refused and reported.
Check: `pytest tests/replays/test_control_heights.py` passes.

**W7. Fill, connections, unresolved areas, readiness, report.** Files: `app/control/heights.py`,
`tests/replays/test_control_heights.py`. Tests: an unsampled cell between 0 m and 4 m neighbours is
unresolved, between agreeing ones it fills; a walked step connects both ways and a drop seen only downward is
one-way; neighbouring ground floors within `STEP_UP_M` connect; unresolved areas carry size, bbox and reason;
the bar refuses a map below it and the report gives visited and supported separately.
Check: `pytest tests/replays/test_control_heights.py` passes.

**W8. Asset, index and the command.** Files: `app/control/heights.py` (`save_asset`, `load_asset`, digest,
picture), `scripts/build_control_heights.py`, `scripts/build_control_geometry.py` (keeps `height_*` fields of a
map's entry), `tests/replays/test_control_heights.py`, `tests/replays/test_control_assets.py`. Tests: the
asset round-trips and its digest is stable; `build_control_geometry.build_map` rewriting an entry keeps its
height fields; the command with `--blobs-dir` on toy blobs and a temp asset dir writes the asset and the
index fields when the bar passes, refuses below it, and with `--preview` writes only under `--out` and
refuses an `--out` inside the repo.
Check: `pytest tests/replays/test_control_heights.py tests/replays/test_control_assets.py
tests/replays/test_control_isolation.py` passes; `git status --short app/static/data/control` is empty.

### Part 4 (heights)

**W9. The reference.** Before any engine change. Files: `scripts/control_reference.py` (computes rounds with
the engine and prints, per round, sha256 of the data and the summary with the header's `revision` set to 0),
`tests/fixtures/control/reference_flat.json` (the digests of a fixed set of toy rounds: open hall, door hall,
midwall, two rooms with a special, a smoke, a Viper wall, a trip, a death), `tests/replays/test_control_reference.py`
(recomputes and compares). The real flat round: the same script on a local revision-10 Ascent round
(`%TEMP%\valo-replay\6f12db3e-...`), digests written to the run folder as `REFERENCE-REAL.json`, never
committed.
Check: `pytest tests/replays/test_control_reference.py` passes at this commit; `REFERENCE-REAL.json` exists
with at least one round.

**W10. Geometry with heights.** Files: `app/control/geometry.py` (node tables, asset loading, `cast` with
`eye_z`, `los`, per-node `build_visibility`, `cache_key`), `app/control/heights.py` (the height constants the
ray test reads), `tests/replays/control_toys.py` (`toy_heights`: a toy geometry with a hand-made asset), new
`tests/replays/test_control_sight.py`. Tests (spec's Sight list): down from a ledge (3 m hidden, 10 m seen);
up at a ledge (1 m past seen, 15 m past hidden); a continuous ramp; a tunnel under a slab; a corner inside
the 2D tolerance; a ray into an unresolved cell gives the 2D answer and is recorded; `los` agrees with `cast`
on every toy; a flat geometry's `cast`, rows and `cache_key` are unchanged.
Check: `pytest tests/replays/test_control_sight.py tests/replays/test_control_geometry.py
tests/replays/test_control_reference.py` passes.

**W11. The engine on a topology, no behaviour change.** Files: new `app/control/topology.py`,
`app/control/engine.py` (every `ndimage` call and GRID x GRID reshape goes through `geo.topo`; arrays sized
`geo.n`), `scripts/render_control_scenes.py` and any other script that reads tick arrays, `tests/replays/
test_control_isolation.py` if it lists modules. Only `FlatTopology` exists.
Check: `pytest tests/replays/test_control_reference.py tests/replays -k control` passes (the base's known
failure deselected); `PY scripts\control_reference.py` on the real round matches `REFERENCE-REAL.json`.

**W12. Per-floor walking, unknown and spotting.** Files: `app/control/topology.py` (`NodeTopology`),
`app/control/engine.py` (holder node and eye from z, `e.body[h.node]`, the collapse to cells, `approximate
heights` in `missing_inputs`), new `tests/replays/test_control_floors.py`. Tests (spec's Per-floor engine
list): an enemy in a tunnel is not spotted by a viewer who sees the bridge; seeing the bridge clears the
bridge's unknown and not the tunnel's; the unknown spreads up a connected step and not up a one-way drop;
the collapse rule; a round without z uses ground floors and is marked.
Check: `pytest tests/replays/test_control_floors.py tests/replays/test_control_reference.py` passes.

**W13. Safe from every node.** Files: `app/control/engine.py` (`comp_seen` and `unknown_safe` on a height
map), `tests/replays/test_control_floors.py`. Tests: the reviewer's case (a raised interior node sees a
target the boundary can't: not Safe); the boundary shortcut against the every-node check on every height
toy (the result decides whether the shortcut may be kept: a D entry); the cost of both printed by the
Lotus check.
Check: `pytest tests/replays/test_control_floors.py tests/replays/test_control_reference.py` passes.

**W14. Watchers and trips.** Files: `app/control/engine.py` (`_watcher`, `_watch`, `_presence`),
`tests/replays/test_control_floors.py`. Tests (spec's Trips list and Viewers and watchers): a wire beside a
step extends to it; on a 20% ramp it stops at 2 m; between two walls, and with nothing within 10 m, it
doesn't change; it doesn't extend onto a stacked floor or across an unresolved cell; a camera watches from
its z; a watcher with no z uses the ground floor and marks the round.
Check: `pytest tests/replays/test_control_floors.py tests/replays/test_control_reference.py` passes.

**W15. Freshness and the commands.** Files: `app/replays/control_format.py` (`CONTROL_REVISION = 4`),
`tests/replays/test_control_format.py` (`PINNED[4]`), `app/control/task.py` (`geometry_used`), `app/services/
replay_control.py` (`geometry_inputs`), `app/services/replay_control_remote.py` (`_matches_geometry`),
`scripts/compute_control.py` (`--map`), `scripts/preview_control_live.py` (`--heights <npz>`, and the warning
when a viewer stands in or looks through an unresolved area), tests beside each.
Check: `pytest tests/replays -k control` passes (known failure deselected); the reference test still passes
with the revision normalised.

**W16. Kill lines and the must-block set.** Files: `app/control/heights.py` (`kill_line_check`,
`must_block_check`), `scripts/build_control_heights.py` (both in the report; a non-preview build refuses a
map that fails either unless `--accept-failures`), new `tests/replays/control_must_block.json` (case 1's
corridor-to-far-room lines for Ascent, z left null until an Ascent match gives them),
`tests/replays/test_control_heights.py`. Tests: qualifying and excluded kills counted by reason; a kill
blocked to the body but not the head is not blocked; a must-block line that isn't blocked fails the build.
Check: `pytest tests/replays/test_control_heights.py` passes.

### After the build (run folder, nothing committed)

**W17. Lotus check** -> `LOTUS.md`: condense the three exports at revision 11 into
`%TEMP%\valo-replay\heights-lotus\<uuid>\<n>.json.gz` (one export per foreground call); a known drop's z to
confirm units; blob size with and without z; the height build in preview (shares, floors, unresolved areas,
runtime); one round through the engine flat and with the preview asset (runtime, Safe shortcut vs every
node, kill lines). Check: `LOTUS.md` has every number or says why not.

**W18. `NEXT.md`**, **W19. `FRIENDS-2.md`** (the prompt's content). Check: every step has a command and the
value to expect; the message is under 120 words.

## Risks

- **W11 is the riskiest step**: about 25 `ndimage` call sites move behind the topology. The reference test
  is byte-level, so a slip shows at once; the step doesn't proceed until it is identical.
- **Part 4 may not finish.** Each of W12-W16 leaves the flat path identical (the reference test is in every
  check), so the branch is always in a state that could ship flat. What doesn't pass is listed in the summary.
- **Three Lotus matches are below the bar** by design; every number from them is provisional, and the start
  values stay one grouped approval.
- **Cherry-picks to p2** touch only condenser files; a conflict is resolved by hand and logged.

## User-only (written into `NEXT.md`, not run)

Push, PR and deploy of p2; `reparse_archive.py`; the Ascent case-1 check and a real height build through
`with_friends_db.py --read-only`; committing any real map's asset; `compute_control.py` recompute.
