# Map control heights, parts 2-5: implementation plan

Spec: `docs/superpowers/specs/2026-10-01-control-heights-design.md` (parts 2, 3, 4, 5 and their Testing
bullets). Part 1 (the archive) is merged and is not touched. Reviewed once
(`2026-10-02-control-heights-impl-review.md`: 1 blocker, 8 should-fixes, 7 nits, all applied here).

## Branches

| Branch | Base | Carries |
| --- | --- | --- |
| `afk/2026-10-02-heights` ("heights") | `worktree-control-fixes` (`fc08370`) | every step |
| `afk/2026-10-02-heights-p2` ("p2") | `origin/main` (`a301653`) | part 2 only (W3-W5), by cherry-pick |

Every command below runs from the branch's `webapp\` folder with
`PY = C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv\Scripts\python.exe`.
`pytest` means `PY -m pytest -q -p no:cacheprovider`. One commit per step, subject `W<n>: ...`.
`CONTROL` means `pytest tests/replays -k control --deselect
tests/replays/test_replay_worker_control.py::test_control_off_answers_404_and_parsing_is_untouched` (that
test fails on the base). `REAL` means `PY scripts\control_reference.py --blobs
%TEMP%\valo-replay\6f12db3e-b2db-4bca-96e4-a837c85ba5a6-current --compare <run folder>\REFERENCE-REAL.json`
printing `identical`.

## Architecture (what the steps build)

**Part 2.** `format.encode_segment` takes an optional `z` list (decimetres of world z, delta-encoded like
`u`); `decode_segment` is unchanged (four values) and `decode_segment_z` returns `(t, u, v, yaw, z | None)`.
`condense.Sample.z` becomes `float | None` (today a missing z is stored as 0.0), and `build_segments` starts a
new run when z presence changes, so a segment has `z` for every sample or not at all. `extras` carries
`location.z`: `_Actor.z`; entry `"z"` (spawn; for a thrown one, where it landed), `thrown.z` (the
projectile's spawn), trip `"end_z"`, and pawn `path` points `[t, u, v, z]` (three values when the sample had
no z). All in world decimetres, ints. `CONDENSE_REVISION` 10 -> 11. `MIN_CONDENSE_REVISION` stays 10
(control still reads revision 10 rounds, as approximate heights). If z pushes a round over the 70 KB budget
it is reported, and the budget is not raised.

**Part 3.** Two modules and a command.

- `app/control/heights.py` (numpy only; imports nothing from `app.control`): every height constant, and
  `HeightAsset` with `save_asset` / `load_asset`. The asset is the `.npz` of the spec plus `edges`
  (K x 4 int32: cell a, floor a, cell b, floor b; one row per direction, for all 8 neighbours) and
  `walk_sha`. Its `digest` is the sha256 of its arrays in a fixed order (12 hex), recorded in `index.json`
  as `height_sha`.
- `app/control/height_build.py` (above `geometry` and `engine`): `stands(blob, geo)` (runs of >= `STAND_S`
  with z within `STAND_TOL_M` of the run's median while alive); `platforms(blob)` (live temporary platforms
  from the util catalog; stands within `PLATFORM_R_M` of one are dropped; the list is a D entry);
  `build(rounds, geo, bar=True)` -> floors per cell (density grouping, the stands/rounds/matches rule,
  spread, > 3 floors refused), fill, connections (observed, one-way drops, inferred), unresolved areas with
  reasons, floors reached only through the air, visited and supported shares, the readiness verdict and
  the report; later `kill_line_check` and `must_block_check`.
- `scripts/build_control_heights.py --map M`: reads rounds read-only (the DB through
  `with_friends_db.py --read-only`, a user step) or from `--blobs-dir <dir>` (local round blobs,
  `<match>/<n>.json.gz`). Without `--preview` it refuses a map below the bar and writes
  `app/static/data/control/<Map>.height.npz` plus the `index.json` fields. With `--preview --out <dir>` it
  builds below the bar and writes only under `--out`, which must be outside the repo. The picture
  (`<Map>.height.png`) always goes under `%TEMP%`.

**Part 4.** The engine's unit becomes a node. Node ids: a cell's lowest floor keeps the cell's flat index
(0 .. GRID*GRID-1); upper floors are appended after GRID*GRID in (cell, floor) order. A map without a
height asset has exactly today's ids, arrays and code path.

- `geometry.Geometry` gains `heights` (None when flat), `n` (node count), `node_cell`, `node_z` (metres above
  the asset's origin; NaN when unresolved), `cell_nodes` (cell -> its nodes) and `centres` extended to
  every node. `load_geometry` loads the asset when `index.json` names one (or an explicit `heights=` path,
  for previews). A walkable cell the asset doesn't know (the walk mask changed since the build) is
  unresolved and counted.
- **The ray test** (`geometry.cast(..., eye_z=None)`): with `eye_z` and heights, per ray a horizon (the
  steepest slope to ground passed so far) and a short list of blocked slope intervals, one per slab passed
  (the line's slope at the plate's height entering and leaving the plate's cell; a slab is committed when
  the ray leaves its cell, so it never blocks targets in its own cell). A node is seen when the slope to
  its body is at least the horizon and outside every interval. In the viewer's own cell only the viewer's
  own node is seen. Entering an unresolved cell drops the height test for the rest of the ray. Without
  `eye_z`, today's answer (every floor of a seen cell on a height map). `geometry.los(geo, a, b)` is one line
  with the same rules, for the kill-line and must-block checks. `build_visibility` has one row per node;
  `cache_key` takes the height digest.
- `app/control/topology.py`: `FlatTopology` (today's `ndimage` calls, verbatim, on GRID x GRID) and
  `NodeTopology`. The node graph: the asset's edges between resolved floors; an unresolved or floorless
  walkable cell is one node with today's 2D links (8 neighbours, both ways) to every floor of each walkable
  neighbour; one-way drops are directed. 4-connected operations use the non-diagonal edges, 8-connected all
  of them, and the unknown's spread costs sqrt(2) on a diagonal as now. Methods: `label`, `dilate`, `bfs`,
  `path_back`, `relax`, `neighbours`, `rim`, `links`. The engine calls only these; every
  `np.zeros(GRID * GRID)` becomes `np.zeros(geo.n)`.
- **Cell-indexed code, by rule.** The holder's own node (the floor of their cell nearest at or below their
  z + 0.5 m, else the lowest): `Tick.live`, `sees` (`e.body[h.node]`), `_live_of`, `unknown_without`, the way
  back, `Knowledge.seen_now` / `tick_for` / `start`, `Unknown.apply` and `_drop_pieces`, `barrier_start`'s
  starts. Every floor of a cell (2D columns): the barrier paint, `Unknown.sealed`'s pinches, smokes and
  damage zones, specials (`special_links` joins every floor of a to every floor of b), `cant_walk_paint`.
- **Engine rules on a height map.** The eye is the player's own z + `EYE_M` (a round without z: the node's
  floor, marked `approximate heights`). The unknown, memory, backfill, Safe and the counterfactual run per
  node. Safe checks every node of the unknown as a source: the boundary first, then the interior for the
  targets still unseen, which is exact. Watchers get a floor and an eye; trips extend along their anchor's
  floor.
- **The collapse to cells** (`RoundControl` stays per walkable cell): a cell's state is its floors' state
  when they all agree (one floor: that floor's); otherwise contested (`CONTESTED_ACTIVE` when any floor is
  active for either team or contested-active, else `CONTESTED`), `NONE` against a held floor included.
  `knew_states` collapse the same way. The unknown and the per-player coverage and control masks are the
  OR of a cell's floors. Stats (m2, m2*s) count nodes. Tier 2 (the register): D entry at W12.
- `CONTROL_REVISION` 3 -> 4 (at W10); `geometry_used`, `geometry_inputs` and the remote geometry check gain
  `height`; `compute_control.py --map`.

## Steps

### Part 5 (heights branch only)

**W2. Revert Ascent's stair paint.** Done (`82daf8c`). Files: `tags.json` (Ascent `cover_paint` back to
`ade5509^`'s), `Ascent.sight.png`, `Ascent.walk.png`, `index.json`. The 1x2 pocket rule is not touched.
Check: the build prints `Ascent: 5220 walkable cells` and `kill lines 9/476`;
`git diff ade5509^ HEAD --stat -- webapp/app/static/data/control/` is empty; `pytest
tests/replays/test_control_assets.py tests/replays/test_control_geometry.py
tests/replays/test_control_unknown.py tests/replays/test_control_store.py` passes.

### Part 2 (heights, each commit cherry-picked to p2)

**W3. Format.** Files: `app/replays/format.py`, `tests/replays/test_replay_format.py`,
`tests/replays/test_replay_util.py` (`c10` -> `c11`). Tests: a segment with z round-trips; one without
decodes as before with z None; `decode_segment` returns four values for both; a z list of the wrong length
is refused; the recipe has `.c11.`.
Check: `pytest tests/replays/test_replay_format.py tests/replays/test_replay_util.py` passes.

**W4. Condenser tracks.** Files: `app/replays/condense.py`, `tests/replays/test_replay_condense.py`. Tests:
every segment of a synthetic match carries `z` in dm; a sample without `position.z` splits its segment and
the z-less part has no `z` key (never 0); a match with no z stores none and splits nothing; gap-filled grid
points interpolate z; the teleport rule still breaks when z is missing.
Check: `pytest tests/replays/test_replay_condense.py tests/replays/test_replay_streaming.py` passes.

**W5. Utility heights.** Files: `app/replays/extras.py`, `tests/replays/test_replay_extras.py`. Tests: an
ability's spawn z, a thrown entry's z, a drone path's `[t, u, v, z]` and both trip anchors' z survive; an
actor with no `location.z` has no `z` key and its path points keep three values. The JS decoder:
`tests/replays/test_replay_viewer.py`'s existing decoder test now runs on blobs with z (not edited, so the
pick to p2 stays clean).
Check: `pytest tests/replays/test_replay_extras.py tests/replays/test_replay_viewer.py
tests/replays/test_replay_isolation.py` passes; on p2, `pytest tests/replays` with the known failure
deselected passes.

### Part 3 (heights)

**W6. Stands and floors.** Files: `app/control/heights.py` (constants), `app/control/height_build.py`
(`stands`, `platforms`, floor grouping), `tests/replays/control_toys.py` (`z_track`, `height_round`), new
`tests/replays/test_control_heights.py`, `tests/replays/test_control_format.py` (the digest also hashes
`heights` and, later, `topology`; `PINNED[3]` re-pinned in place, unreleased). Tests: two floors at 0 and
5 m with airborne samples between; jumps, a rope climb, a boost and a fall make no floor; a platform used in
one match makes no floor, and near a Sage wall the stands are dropped; crouching stays on its floor; a 20%
ramp is one floor and a steeper one is unresolved; four floors are refused and reported. D entry: the
platform list.
Check: `pytest tests/replays/test_control_heights.py tests/replays/test_control_format.py` passes.

**W7. Fill, connections, unresolved areas, readiness, report.** Files: `app/control/height_build.py`,
`tests/replays/test_control_heights.py`. Tests: an unsampled cell between 0 m and 4 m neighbours is
unresolved, between agreeing ones it fills; a walked step connects both ways and a drop seen only downward
is one-way; neighbouring ground floors within `STEP_UP_M` connect; a floor reached only through the air is
listed; unresolved areas carry size, bbox and reason; the bar refuses a map below it and the report gives
visited and supported separately.
Check: `pytest tests/replays/test_control_heights.py` passes.

**W8. Asset, index and the command.** Files: `app/control/heights.py` (`save_asset`, `load_asset`, digest),
`app/control/height_build.py` (picture), `scripts/build_control_heights.py`,
`scripts/build_control_geometry.py` (keeps `height_*` fields; warns when `walk_sha` no longer matches the
asset's), `tests/replays/test_control_heights.py`, `tests/replays/test_control_assets.py`. Tests: the asset
round-trips and its digest is stable; a rewritten index entry keeps its height fields; the command with
`--blobs-dir` on toy blobs and a temp asset dir writes the asset and the index fields when the bar passes,
refuses below it, and with `--preview` writes only under `--out` and refuses an `--out` inside the repo.
Check: `pytest tests/replays/test_control_heights.py tests/replays/test_control_assets.py
tests/replays/test_control_isolation.py` passes; `git status --short app/static/data/control` is empty.

### Part 4 (heights)

**W9. The reference.** Before any engine change. Files: `scripts/control_reference.py` (computes rounds and
prints, per round, sha256 of the decompressed data and summary with the header's `revision` set to 0;
`--blobs <dir>` for local rounds, `--write` / `--compare <json>`),
`tests/fixtures/control/reference_flat.json` (digests of toy rounds: open hall, door hall, midwall, two
rooms with a special, a smoke, a Viper wall, a trip, an alarmbot, a camera, a drone, a death, and a
barrier), `tests/replays/test_control_reference.py`. The real flat round: rounds 1 and 2 of
`%TEMP%\valo-replay\6f12db3e-b2db-4bca-96e4-a837c85ba5a6-current`, digests in the run folder as
`REFERENCE-REAL.json`, never committed.
Check: `pytest tests/replays/test_control_reference.py` passes at this commit; `REFERENCE-REAL.json` has two
rounds; `REAL` prints `identical`.

**W10. Geometry with heights, revision 4.** Files: `app/control/geometry.py` (node tables, asset loading,
`cast` with `eye_z`, `los`, per-node `build_visibility`, `cache_key`), `app/replays/control_format.py`
(`CONTROL_REVISION = 4`), `tests/replays/test_control_format.py` (`PINNED[4]`),
`tests/replays/control_toys.py` (`toy_heights`), new `tests/replays/test_control_sight.py`. Tests (the
spec's Sight list): down from a ledge (3 m hidden, 10 m seen); up at a ledge (1 m past seen, 15 m past
hidden); a continuous ramp; a tunnel under a slab, the same-cell case included; a corner inside the 2D
tolerance; a ray into an unresolved cell gives the 2D answer and is recorded; `los` clear implies `cast`
seen on every toy, and they are equal on the wall-free height toys; a flat geometry's `cast`, rows and
`cache_key` are unchanged.
Check: `pytest tests/replays/test_control_sight.py` and `CONTROL` pass; `REAL` prints `identical`.

**W11. The engine on a topology, no behaviour change.** Files: new `app/control/topology.py`
(`FlatTopology` only), `app/control/engine.py` (every `ndimage` call and GRID x GRID reshape goes through
`geo.topo`; arrays sized `geo.n`), `scripts/render_control_scenes.py` and any other reader of tick arrays.
Check: `CONTROL` passes; `REAL` prints `identical`.

**W12. Per-floor walking, unknown and spotting.** Files: `app/control/topology.py` (`NodeTopology`),
`app/control/engine.py` (the sites listed under "Cell-indexed code", the collapse, `approximate heights`),
new `tests/replays/test_control_floors.py`. Tests: an enemy in a tunnel is not spotted by a viewer who sees
the bridge, nor by one standing on the bridge above them; seeing the bridge clears the bridge's unknown
and not the tunnel's; the unknown spreads up a connected step and not up a one-way drop; the unknown
crosses an unresolved strip; the collapse rule (two and three floors, `NONE` against held); a round
without z uses ground floors and is marked. D entry: the collapse rule.
Check: `pytest tests/replays/test_control_floors.py` and `CONTROL` pass; `REAL` prints `identical`.

**W13. Safe from every node.** Files: `app/control/engine.py` (`comp_seen`, `unknown_safe`),
`tests/replays/test_control_floors.py`. Tests: a raised interior node sees a target the boundary can't
(not Safe); the boundary shortcut against the every-node check on every height toy. D entry: whether the
shortcut is kept (only if proven equal; flat maps keep it, since their output must not change).
Check: as W12.

**W14. Watchers and trips.** Files: `app/control/engine.py` (`_watcher`, `_watch`, `_presence`, `path_at`),
`tests/replays/test_control_floors.py`. Tests: a wire beside a step extends to it; on a 20% ramp it stops at
2 m; between two walls, and with nothing within 10 m, it doesn't change; it doesn't extend onto a stacked
floor or across an unresolved cell; a camera watches from its z and a drone from its path's z; a watcher
with no z uses the ground floor and marks the round.
Check: as W12.

**W15. Freshness and the commands.** Files: `app/control/task.py` (`geometry_used`),
`app/services/replay_control.py` (`geometry_inputs`), `app/services/replay_control_remote.py`
(`_matches_geometry`), `scripts/compute_control.py` (`--map`), `scripts/preview_control_live.py`
(`--heights <npz>`, and the warning when a viewer stands in or looks through an unresolved area), tests
beside each.
Check: `CONTROL` passes; `REAL` prints `identical`; `pytest tests/replays/test_control_store.py
tests/replays/test_control_task.py tests/replays/test_control_remote.py` passes.

**W16. Kill lines and the must-block set.** Files: `app/control/height_build.py` (`kill_line_check`,
`must_block_check`), `scripts/build_control_heights.py` (both in the report; a non-preview build refuses a
map that fails either unless `--accept-failures`), new `tests/replays/control_must_block.json` (case 1's
corridor-to-far-room lines for Ascent, z null until an Ascent match gives them),
`tests/replays/test_control_heights.py`. Tests: qualifying and excluded kills counted by reason; a kill
blocked to the body but not the head is not blocked; a must-block line that isn't blocked fails the build.
Check: `pytest tests/replays/test_control_heights.py` and `CONTROL` pass.

### After the build (run folder, nothing committed)

**W17. Lotus check** -> `LOTUS.md`: condense the three exports at revision 11 into
`%TEMP%\valo-replay\heights-lotus\<uuid>\<n>.json.gz` (one export per foreground call); a known drop's z to
confirm units; blob size with and without z against the 70 KB budget; the height build in preview (shares,
floors, unresolved areas, runtime); one round through the engine flat and with the preview asset (runtime,
Safe shortcut vs every node, kill lines). Check: `LOTUS.md` has every number or says why not.

**W18. `NEXT.md`**, **W19. `FRIENDS-2.md`** (the prompt's content). Check: every step has a command and the
value to expect; the message is under 120 words.

## Risks

- **W11 is the riskiest step**: about 25 `ndimage` call sites move behind the topology. The reference is
  byte-level and covers barriers, watchers and two real rounds, so a slip shows at once.
- **Part 4 may not finish.** Every check from W10 on includes the flat reference, so the branch can always
  ship flat. What doesn't pass is listed in the summary.
- **Three Lotus matches are below the bar** by design; every number from them is provisional, and the start
  values stay one grouped approval.

## User-only (written into `NEXT.md`, not run)

Push, PR and deploy of p2; `reparse_archive.py`; the Ascent case-1 check and a real height build through
`with_friends_db.py --read-only`; committing any real map's asset; `compute_control.py` recompute.
