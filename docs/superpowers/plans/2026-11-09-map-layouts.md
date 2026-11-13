# Map layouts: implementation plan

Design: `docs/superpowers/specs/2026-11-09-map-layouts-design.md` (revised after review, commit `33c048f5`). Revised
after the P4 review (24 findings, all applied; splits W6a/b, W10a/b, W16a/b). Paths are under `webapp/` unless they
start with `replay_worker/` or `docs/`. `<py>` is
`C:\Users\conor\Documents\GitHub\valo-with-friends-tracker\webapp\.venv313-test\Scripts\python.exe`; tests run from
`webapp/` with `-n 0` (the addopts' `-m` stays). `<friends>` is a read-only friends-DB probe:
from the main checkout's `webapp/`, `.venv313\Scripts\python.exe scripts\with_friends_db.py --expect-database
valowithfriendsdb --read-only "<run folder>\probes\probe_w<n>.py"`; each probe puts the worktree's `webapp/` first on
`sys.path`, reads only, and prints one headed block per figure.

Four stacked branches (R1): part 1 `afk/2026-11-09-map-layouts`, part 2 `-2`, part 3 `-3`, part 4 `-4`. Each part ends
with a full-suite checkpoint on its branch; the slow lane runs before wrap-up.

**Every item** in parts 1-3 runs `tests/replays/test_layout_base_identity.py` (the golden pin) and
`tests/test_test_catalog.py`, and adds its own new files to `tests/suites.toml` `[map-layouts]` `guards` in the same
item (never ahead of the file existing: `check` fails on a glob with no match, `scripts/test_catalog.py:163-167`).
Pin parts are the QUEUE.md `Pin parts:` lines A-D.

## Terms

- **asset key**: a layout's name on disk, in the DB and in worker tasks. Base = the bare map name (`Ascent`); later
  layouts `<Map>_<NN>_<NN>` (`Ascent_13_10`). **display id**: `Ascent@base`, `Ascent@13.10`.
- **toy layout**: a synthetic second layout of a test map, `<Toy>_13_10`, built in a tmp asset folder by a test
  fixture from the toy map's own masks with one changed walk/sight block (never committed as an asset). One toy
  layout of a map **with a feature bundle** (`Ascent_13_10` or `Lotus_13_10`, from `fi.CONSUMER_VERSIONS`) is used
  wherever a test covers feature or scale lookups.

## Golden figures (R3 safety line)

The golden file (W4) holds, per map in `index.json`: `geometry_inputs(map, None)`; `resolve_current_geometry(db, map)`
`context.state`, `context.feature_input.key` and `pinned.geometry` on a scratch SQLite DB with no heights or feature
rows **and `control_feature_artifacts.find_artifact_header` monkeypatched to return a fixed digest**, so the six maps
with enabled feature bundles (Abyss, Ascent, Lotus, Split, Summit, Sunset) resolve pinned geometry too, not only
`features_pending` (`replay_control.py:244-247`); `map_layer(map)`; `choke_assets.asset_hash(map)`;
`replay_gaps.hearing_hash()`; `replay_gaps.engine_key` for the map; `height_inputs.geometry_identity(map)`;
`height_inputs.must_block_sha()` computed on **LF-normalised bytes** (the checkout's `must_block.json` is CRLF on
Windows, LF on CI: the helper reads a tmp LF copy passed as `path`); `height_inputs.digest(height_inputs.manifest(map,
FIXED_REPLAYS, identity, must_block_sha()))`; `fmt.assets_revision()`; sha256 of `json.dumps(areas.callouts(map),
sort_keys=True)`; `cf.fingerprint(FIXED_RECIPE, FIXED_SHA, FIXED_LINK, geometry)` and its
`replay_gaps.gap_fingerprint`. It never changes in parts 1-3.

The real-data counterpart: `probe_w4.py` (run on the base code at W4) records **a frozen set**: the max replay id at
W4, and for every replay id up to it the set of (replay_id, round, current control fingerprint, current gap
fingerprint) as the base code computes them (`replay_control.round_fingerprint`, `replay_gaps.gap_fingerprint`), saved
to `<run folder>/probes/w4_set.json` with its sha256; plus per map the digest of `current_manifests` restricted to
those replay ids. Every later probe in parts 1 and 3 recomputes the same set with the branch's code over the same ids
and prints its sha256, which must equal W4's (unit: (replay, round) rows; variant: no flags; file: `w4_set.json`).
The live DB moving (uploads, the dispatcher) doesn't affect it: the fingerprints are computed, not read. Timing:
the controller times probe_w4 once and writes `<s per probe> x 7 runs = total` in LOG before W6a's probe.

## Part 1: layouts and selection (branch `afk/2026-11-09-map-layouts`)

### W4: the golden file and the base-identity test (from the base code)
- Files: new `tests/replays/layout_golden.py` (builds the figures; `python -m tests.replays.layout_golden --write`
  writes them), new `tests/fixtures/control/layout_golden.json`, new `tests/replays/test_layout_base_identity.py`.
- Reads: `app/services/replay_control.py:302` `geometry_inputs`, `:203` `resolve_current_geometry`, `:126`
  `map_layer`; `app/services/control_feature_artifacts.py` `find_artifact_header` (monkeypatched);
  `app/replays/choke_assets.py:89`; `app/services/replay_gaps.py:32,59` and `engine_key`;
  `app/replays/height_inputs.py:64,71,117,129`; `app/replays/format.py:262` `assets_revision`; `app/coach/areas.py:22`;
  `app/replays/control_format.py:149-151`. Scratch-SQLite pattern: `tests/replays/test_control_pinned_inputs.py:74`.
- Test: for every map in `index.json`, each figure equals the golden value; the golden map set equals `index.json`'s;
  the six bundle maps have a non-null `feature_input.key` and pinned geometry in the golden (asserted).
- Bite: mutants (`mutants-W4.json`): an extra key in `geometry_inputs`' dict; `hearing_hash` body changed; a map
  dropped from the golden set; the map name passed to `_identify_features` changed.
- Check: `<py> -m pytest -n 0 tests/replays/test_layout_base_identity.py tests/test_test_catalog.py` green; mutants
  4/4; `probe_w4.py` on `<friends>` run, `w4_set.json` saved, its sha256 and row count in LOG.
- suites.toml: new suite `[map-layouts]`: tests `tests/replays/test_*layout*.py`; guards only files that exist now
  (`tests/replays/layout_golden.py`, `tests/fixtures/control/layout_golden.json`).
- Backend: Codex (offline); the probe is the controller's.

### W5: the layout registry and `layout_for`
- Files: new `app/replays/layouts.py` (standard library only: the worker imports it), new
  `app/static/data/layouts.json`, new `tests/replays/test_layouts.py`.
- API: `parse_patch(branch) -> tuple[int, int] | None` (`release-(\d+)\.(\d+)$`); `ASSET_KEY =
  re.compile(r"^[A-Za-z][A-Za-z0-9]*(_\d+_\d+)?$")`; `map_of(asset_key) -> str` (strips `_\d+_\d+`);
  `key_for(map, patch) -> str`; `Layout(display_id, asset_key, start, drawn_from, region_mask)`;
  `layouts_of(map, registry=None) -> list[Layout]` (base only when the map is absent); `layout_for(map_name,
  game_branch, *, registry=None, index=None) -> Selection(state, layout)` with state `ok | layout_pending |
  layout_unknown`: one layout → base, branch never read; else the latest layout with `start <= patch`; that layout
  with no `index.json` entry → `layout_pending`; branch unparseable → `layout_unknown`.
- `layouts.json`: `{"version": 1, "maps": {"<Map>": [{"id": "<Map>@base", "key": "<Map>", "start": null,
  "drawn_from": "13.08", "regions": null}]}}` for every map in `index.json`. Not read by `assets_revision`,
  `_asset_paths` or any hash.
- Tests (red first): 13.10 after 13.9; `++Ares-Core+release-13.08` → (13, 8); `"b"`, `"synthetic"`, `None` → None;
  a base-only map with branch `"b"` → base, ok; two layouts: 13.09 → base, 13.10 / 13.12 → later; pending; unknown;
  `map_of("Ascent_13_10") == "Ascent"`; `ASSET_KEY` refuses `Ascent@13.10`, `Ascent.x`, `_13_10`. Completeness
  (registry): `layouts.json` maps == `index.json` maps, every base key == its map, every `drawn_from` parses.
- suites.toml: guards += `app/replays/layouts.py`, `app/static/data/layouts.json`.
- Check: `<py> -m pytest -n 0 tests/replays/test_layouts.py tests/replays/test_layout_base_identity.py
  tests/test_test_catalog.py tests/replays/test_control_isolation.py tests/replays/test_replay_isolation.py` green;
  red first.
- Backend: Codex.

### W6a: control selects by layout: `replay_control.py`
- Depends: W5.
- Files: `app/services/replay_control.py`: a helper `layout_of(replay) -> Selection` (calls `layouts.layout_for(
  replay.map_name, replay.game_branch)`); `:203` `resolve_current_geometry(db, asset_key)` reads scale from
  `maps[layouts.map_of(asset_key)]` (`:231`, `:237`); `:302` `geometry_inputs` scale at `:324` the same; `:283`
  `prepare_discovered` takes keys; `app/services/control_feature_artifacts.py:194` (`prepare_artifact` scale by
  `map_of`); `:370` `round_fingerprint` and `:384` `round_control` resolve the replay's key; `:436` `plan` keys
  `contexts` by asset key, adds reasons `layout_pending` / `layout_unknown` (not computable, `:420`); `PlannedRound`
  (`:408`) gains `asset_key: str | None = None` (`None` reads as `map_name`). `RoundControlAnswer` gains
  `layout: str | None = None`. `round_control` on a pending/unknown replay: an `ok` row →
  `RoundControlAnswer("ok", row, stale=True, layout=state)`; none → status `layout_pending` / `layout_unknown`.
  `app/routers/replays.py:338`: the two new statuses with no row join `no_map`/`old_blob` in the 404 branch.
- Locks and heights stay keyed by map name in part 1 (a later layout's `active_digests` lookup finds nothing: flat
  until part 3).
- Tests (red first), new `tests/replays/test_layout_selection.py` (tmp `CONTROL_DIR` monkeypatch as in
  `tests/replays/test_control_assets.py`; toy map from `tests/replays/control_toys.py`; one bundle map's toy layout):
  replays on 13.08 keep the golden fingerprint; a 13.10 replay resolves `<Toy>_13_10` with a different fingerprint;
  the bundle map's toy layout resolves features and `prepare_artifact` without KeyError; adding the layout restales
  only replays from 13.10 on; pending keeps the stored row, `stale=True`, plans nothing; unknown likewise; the
  router answers 404 (not 500) for pending/unknown with no row; a base-only map with branch `"b"` computes.
- Check: that file + `tests/replays/test_control_store.py tests/replays/test_control_pinned_inputs.py`; pin parts
  A, B, C, D; `scripts/control_cases.py` (all cases unchanged); `probe_w6a.py` set sha == W4's.
- Backend: Codex; probe the controller's.

### W6b: control selects by layout: the dispatcher
- Depends: W6a.
- Files: `app/services/replay_control_remote.py:596,598,655,668,684,685` use `p.asset_key or p.map_name` for the
  task's `map`, gap keys and height pushes; `:900` the `prepare_discovered` caller passes the asset key, not
  `Replay.map_name`; `:704` keeps the map name.
- Tests (red first) in `tests/replays/test_layout_selection.py`: a 13.10 replay's planned task carries `map:
  <Toy>_13_10` and its gap key; base tasks unchanged (fake worker in `tests/replays/test_control_remote.py`).
- Check: + `tests/replays/test_control_remote.py`; pin parts A, B, C, D; control_cases.
- Backend: Codex.

### W7: the worker accepts layout keys and names them in `/health`
- Depends: W6b.
- Files: `app/control/geometry.py:546,559` scale from `maps.json[layouts.map_of(name)]`; map-name literals routed
  through `map_of`: `app/control/features.py:1148`, `app/control/feature_runtime.py:115`,
  `app/control/feature_geometry.py:22`, `app/control/rope_ground.py:138`, `app/control/route_runtime.py:12`,
  `app/replays/ascent_features.py:139,204`, `app/replays/map_feature_events.py:23-25,80`,
  `app/replays/map_feature_schema.py:171`; `replay_worker/server.py:1275` `MAP_NAME` → `layouts.ASSET_KEY` semantics
  (stdlib copy if the server must not import `app`: keep the isolation tests green), `:2006,2602,2652` unchanged
  callers; `replay_worker/control_cache.py:21` map group `[A-Za-z][A-Za-z0-9]*(?:_\d+_\d+)?`; `/health`
  (`server.py:2407`) adds `control.layouts`: the sorted asset keys in its own `index.json`. A control run (worker or
  Workflow run, `replay-tasks` is a separate deploy: RENDER_DEPLOY.md:298,461) given a key missing from its own
  `index.json` returns the existing refusal shape with reason `unknown_layout`, which the site counts as held (no try
  spent: the implementer follows the refusal path `held`/`refused` already uses and names it in the report). Site:
  `app/services/replay_control_remote.py:378` beside `supports_gaps`, `supports_layout(health, key)`: base keys
  always; a later key only when listed. Unsupported rounds are held (not sent, no try spent, counted `held`).
- Tests (red first), new `tests/replays/test_layout_worker.py` (patterns: `tests/replays/test_replay_worker_control.py`,
  `test_replay_worker_control_cache.py`, the fake worker in `tests/replays/test_control_remote.py`): the worker
  accepts `Ascent_13_10` (fails before: the base regex refuses `_`); refuses `Ascent@13.10`, `Ascent.13`, `../x`
  (kept as regression beside the accept test); `Ascent_13_10` / `Lotus_13_10` features resolve as their maps' (each
  literal site above, fails before with "Ascent consumer cannot run on another map" or no features); cache file names
  with a later key parse; health lists keys; a run with an unknown key refuses `unknown_layout` and the site spends
  no try; the dispatcher holds a later layout's round for a health without it, sends it once listed; base rounds go
  to an old health unchanged.
- Check: that file + `tests/replays/test_replay_worker_control.py tests/replays/test_replay_worker_control_cache.py
  tests/replays/test_control_remote.py tests/replays/test_control_geometry.py tests/replays/test_replay_isolation.py
  tests/replays/test_control_isolation.py`; pin parts C, D.
- Backend: Codex.

### W8: gaps, chokes and progress by layout
- Depends: W6b.
- Files: `app/services/replay_gaps.py:50,59` take the asset key (chokes by key; hearing unchanged); `:118`,
  `:139` pass the replay's key; `app/services/replay_gaps_store.py:42,51,53` by key (`:75` lock stays the map name);
  `app/services/replay_control_remote.py:420,485,525` chokes by key; `app/services/replay_progress.py:34` adds steps
  `layout_pending` ("Map changed in this patch; redraw pending", False) and `layout_unknown` ("Patch unknown on a map
  with several layouts", False) after `no_map`; `:78-79` by key.
- Tests (red first) in `tests/replays/test_layout_selection.py`: a toy-layout replay's gap fingerprint uses its own
  chokes hash; base's equals golden; progress counts pending/unknown rounds in their steps. Registry: the step
  list test in `tests/replays/test_replay_progress.py` updated.
- Check: + `tests/replays/test_gaps_store.py tests/replays/test_gaps_chokes.py tests/replays/test_replay_progress.py`
  and the timing-gaps files as `test_catalog.py plan` lists them; pin parts C, D; `probe_w8.py` set sha == W4.
- Backend: Codex.

### W9: Critical Moments, Critical Util, the coach and callouts by layout
- Depends: W8.
- Files: `app/services/replay_moments.py:260,269`; `app/services/replay_util.py:103` `area_of(asset_key, …)`, `:205,236`
  context adds `"asset_key"`; `app/coach/areas.py:22,26` read `callouts.json[asset_key]` (base = map name: unchanged);
  `app/coach/control_areas.py:16`, `app/coach/snapshot.py:72`, `app/coach/roundview.py:23,97,101`,
  `app/coach/brief.py:89` pass the key; `app/services/replay_control_views.py:55` by key.
- Tests (red first) in new `tests/replays/test_layout_readers.py`: with a toy layout and a callouts table keyed by
  the toy key, a 13.10 replay's area names come from its layout, a 13.08 replay's from base; moments' current
  fingerprint uses the key; coach snapshot shares use the key.
- Check: + the test files that name the changed functions (`git grep -l -E
  "area_of|area_shares|callouts|load_moments_payload|area_at" -- tests/*.py tests/**/*.py`); pin parts C, D;
  `probe_w9.py` set sha == W4.
- Backend: Codex.

### W10a: the viewer follows the layout
- Depends: W9.
- Files: `app/services/replays.py:94,117` the match context adds `"asset"` (the key) and `"layout"` (display id,
  state); `app/templates/replays/replay.html:27,29` minimap and bullet mask by `match.asset`; a note under the map
  for `layout_pending` ("The map changed in <patch>; this replay's map control is out of date until it is
  redrawn") and `layout_unknown`; `app/routers/replays.py:354,398,434` by key.
- Tests (red first) in new `tests/replays/test_layout_viewer.py` (pattern `tests/replays/test_control_viewer.py`): the
  replay page for a toy-layout replay loads `<key>.png` and `<key>.bullet.png`; base pages render the same two URLs
  as before; the pending note shows.
- Check: + `tests/replays/test_control_viewer.py --require-node`; pin part D.
- Shot: `shot_page.py` of the toy-layout pending page from a local server on scratch data (`web_local.sh`), one
  picture for the part-1 card: expected change = the note text under the map and the minimap URL (`<key>.png`);
  no real page changes (no layout exists).
- Backend: Codex for code and tests; the shot is the controller's.

### W10b: the gap patterns page follows the layout
- Depends: W10a.
- Files: `app/services/gap_patterns.py:128,212,464,481,510` take a layout (`?layout=<key>`, default the map's newest
  with assets), rounds filtered to replays that select it; `app/routers/gap_patterns.py` passes it and lists the
  map's layouts for a picker (hidden with one); `app/static/js/gap_patterns.js:165` minimap by key.
- Tests (red first) in `tests/replays/test_layout_viewer.py` (pattern `tests/replays/test_gap_patterns_web.py`): two
  layouts show the picker and only that layout's rounds; one layout: no picker, output as before.
- Check: + `tests/replays/test_gap_patterns.py tests/replays/test_gap_patterns_web.py`.
- Backend: Codex.

### W11: end to end on a toy layout; part 1 wrap
- Depends: W7, W10b.
- Files: new `tests/replays/test_layout_end_to_end.py`: toy map with a toy layout; replays on 13.08 and 13.10; plan →
  remote dispatch to a fake worker that names the key → store → `round_control` ok and current for both; base replay
  fingerprint == golden; then a pending entry for 13.12: a 13.12 replay keeps its row, stale, nothing sent.
  `test_layout_base_identity.py` gains the same figures through `layout_for(map, "++Ares-Core+release-13.08")` and
  the key path. CLAUDE.md: one paragraph "Map layouts" (registry, keys, `layout_for`, pending/unknown).
- Check: that file + the `[map-layouts]` suite; `probe_w11.py` set sha == W4; then the part-1 checkpoint
  (re-collect, `checkpoint.py request --name cp1`).
- Backend: Codex.

## Part 2: the wall signal (branch `-2`, from part 1)

### W12: the wall-share table (migration 0032)
- Files: new `alembic/versions/0032_replay_round_wall_shares.py` (`down_revision = "0031"`, the current head); model
  in `app/models/replay.py` (beside `ReplayRoundControl`): `replay_round_wall_shares(replay_id int, round_number int,
  asset_key varchar(64), control_fingerprint varchar(64), inside int, total int, computed_at timestamptz)`, PK
  (replay_id, round_number), no foreign keys (readers join to current ok control rows, W15).
- Tests: in `tests/test_migration_lifecycle.py` (the `scratch_database` pattern at `:62`): upgrade 0031 → head →
  downgrade 0031 → head on the scratch PG; table and PK exist after; absent after downgrade; counts of the other
  tables unchanged.
- Check: PostgreSQL lane on `tests/test_migration_lifecycle.py` with `VALO_TEST_DATABASE_URL`; the model-vs-migration
  test if the repo has one (`git grep -l "0031" -- tests`).
- Backend: afk-implementer (PostgreSQL).

### W13: computing the share
- Depends: W12.
- Files: new `app/services/layout_walls.py`: `round_share(control_data: bytes, round_blob) -> (inside, total)`: the
  round's 4 Hz alive player positions (the blob's tracks as `app/coach/roundview.py:103` reads them) mapped to the
  stored row's walk bitmap (`cf.unpack_data`, `cf.walk_bitmap`, `app/replays/control_format.py:209`; cell from uv as
  in `app/coach/control_areas.py:18-22`), `inside` = positions on non-walk cells; `step(session_factory, now,
  budget)`: up to `STEP_ROWS = 20` (D12: kept after W14 timed one replay) ok control rows whose share row is
  missing or has another fingerprint, oldest first; idle only (no upload parse running, no control in flight).
  Hooked in the dispatcher loop beside the heights step (`app/services/replay_control_remote.py:889`), wrapped as
  that step is (`:888-898`: try/except, `loop_ok` / `loop_failed`): a failure never stops map control. Never in
  demo mode.
- Tests (red first), new `tests/replays/test_layout_walls.py`: a toy round with one track crossing a wall cell counts
  it; 4 Hz sampling; dead players not counted; a changed fingerprint recomputes; demo mode does nothing; the step
  waits while a parse runs; a raising `round_share` leaves the dispatcher loop running (`loop_failed` recorded).
  One test row in the real blob shape (pasted in the brief from a dump).
- Check: that file + `tests/replays/test_control_remote.py`; pin parts A, B, C, D; postgres lane
  `tests/test_migration_lifecycle.py`; `probe_w13.py` (`<friends>`): one replay's `round_share` total equals that
  blob's alive 4 Hz sample count (unit: positions; file: the replay's round blobs).
- Backend: Codex for the code; afk-implementer for the postgres lane.

### W14: real-data shares and the threshold (investigation)
- Depends: W13.
- `afk-investigator` (friends DB read-only), committed `layout_walls.round_share` called, never re-implemented:
  `probe_w14.py` prints per map, per replay (uuid, patch): inside, total, share; and for Corrode's 5 replays the
  same with the pre-#197 walk mask (`git show ebe6469b^:webapp/app/static/data/control/Corrode.walk.png`, turned into
  the walk bitmap by the engine's own cell rule, `GRID` 128, `control_format.py:64`). ebe6469b's message gives
  "positions inside walls 1,527 -> 4" in pixels, a cross-check of direction only. Size first: one replay timed; then
  `<s per replay> × 90 = total`; over ~30 min → a resumable batch (`checkpoint.py batch`). The per-replay time also
  sets W13's `STEP_ROWS`.
- Expected: today's Corrode shares near zero (unit: positions; variant: current assets); pre-#197 Corrode clearly
  higher; every map's 13.06 and 13.08 shares similar. A picture: Corrode's positions on non-walk cells over the old
  mask (PNG in the run folder) for the card.
- Output: the threshold (D12, approved: a rise of 0.5 points over the baseline, at least 4 replays; was a flat share): the share above which a patch's replays alarm, and a minimum replay count,
  each with its arithmetic from the table. A tier 2 `D` entry with the table and the picture.

### W15: the `map_layout_walls` Site health error
- Depends: W14.
- Files: `app/services/layout_walls.py` `evaluate(db)`: reads share rows joined to current ok control rows only
  (same replay, round and `control_fingerprint`), so a deleted or recomputed replay's rows count nothing. Per map,
  per layout: baseline = share over replays whose patch ≤ `drawn_from`; for each later patch with ≥ `MIN_REPLAYS`
  replays, share > `THRESHOLD` (both D12, approved) → `site_health.record("map_layout_walls",
  f"{map}:{patch}", …)` with map, patch, replay count, share, baseline and the top 3 callouts where positions land.
  Every open `map_layout_walls` key this pass didn't raise is resolved (a redraw moves a patch's replays to a layout
  whose `drawn_from` covers it, so that patch is never evaluated again). Run after each `step` batch.
  `app/services/site_health.py:712` `RECORDED_KINDS` += `map_layout_walls`; `app/services/replay_errors.py:31,64,69`
  `KINDS`, `GROUPS`, `EVENT_KINDS` (the completeness tests in `tests/test_site_health.py` and
  `tests/replays/test_replay_errors.py` name the kind). CLAUDE.md "Site health": the kind. The spec's §2 key is
  amended to `<map>:<patch>`.
- Tests (red first) in `tests/replays/test_layout_walls.py`: a patch above threshold raises once with its key; after
  a toy redraw (a new layout covering it) the key resolves; under threshold resolves; baseline-only patches never
  raise; demo mode never raises; a share row whose fingerprint isn't current counts nothing.
- Check: + `tests/test_site_health.py tests/replays/test_replay_errors.py tests/test_site_health_checks.py`; pin parts
  C, D; then the part-2 checkpoint (`cp2`).
- Backend: Codex.

## Part 3: per-layout heights (branch `-3`, from part 2). No migration (D3).

### W16a: manifests per layout, `must_block` per key
- Depends: W15.
- Files: `app/replays/height_inputs.py:48,64` `must_block_set/sha(key)`: base reads `must_block.json` exactly as
  today; a later key reads `must_block.<key>.json`; `:76` `geometry_identity` scale by `map_of`; `:117`
  `manifest(...)` gains `regions=None`: absent for a map with one layout (byte-identical), else `{"mask": <region
  mask sha>, "pool": [replay uuids from other layouts]}`.
- Tests (red first), new `tests/replays/test_layout_heights.py`: base manifests digest == golden for every map; a
  toy two-layout map: each layout's manifest has `regions`; the later key's `must_block_set` reads its own file
  (fails before) and base's sha is unchanged when it is added.
- Check: + `tests/replays/test_height_inputs.py`; pin parts A, B, C, D.
- Backend: Codex.

### W16b: pooling, "every 5" per layout, heights rows and holds by key
- Depends: W16a.
- Files: `app/services/replay_heights_remote.py:143` `current_manifests` keyed by asset key: a layout's own replays
  plus other layouts' replays (masked by the worker, W17); `:172` `plan_maps` "every 5" counts the layout's own added
  replays; `FIRST_BUILD_MATCHES` (`:62`) counts the pool; `app/services/control_heights.py:82,90,97,133` rows by key
  (base = map name: unchanged); `app/services/replay_control_remote.py:813,833` held rounds by `p.asset_key or
  p.map_name`; `app/services/replay_control.py` part-1 fallback removed: a later key looks up its own active digest.
- Tests (red first) in `tests/replays/test_layout_heights.py`: the pools hold the other layout's replays; the base
  manifest changes once a later layout exists (the R6 rebuild); "every 5" counts own replays only; a later layout's
  rounds are held during its own build, base's during base's.
- Check: + `tests/replays/test_heights_remote.py tests/replays/test_control_heights.py
  tests/replays/test_control_heights_db.py tests/replays/test_control_remote.py`; pin parts A, B, C, D;
  `probe_w16.py`: set sha == W4, and each map's `current_manifests` digest over W4's replay ids computed by the base
  code (a `git archive 15edeb6a` copy in the run folder) and by the branch in the same probe run, equal.
- Backend: Codex.

### W17: the worker masks other layouts' positions (heights protocol 1)
- Depends: W16b.
- Files: the heights child (`app/control/height_job.py` or what `replay_worker/server.py` heights path calls: the
  implementer greps `heights:` build key at `app/replays/height_inputs.py:135`): positions of rounds from pool
  replays inside the region mask are dropped before the build; `replay_worker/server.py:2407` `/health` `heights`
  adds `protocol: 1`; site `app/services/replay_heights_remote.py:305,313` sends a build whose manifest has `regions`
  only to a worker naming heights protocol 1, else waits (no try spent); a Workflow heights run (separate deploy,
  finding 14) given a `regions` manifest it can't read refuses without spending a try, as W7's `unknown_layout`;
  base builds unchanged.
- Tests (red first) in `tests/replays/test_layout_heights.py` + `tests/replays/test_replay_worker_heights.py`
  pattern: masked positions never reach the build; unmasked pool positions do; an old health holds a regions build
  with no try spent; base builds go to an old health.
- Check: + `tests/replays/test_replay_worker_heights.py tests/replays/test_replay_worker_heights_workflow.py
  tests/replays/test_control_height_job.py tests/replays/test_replay_isolation.py`; pin parts A-D.
- Backend: Codex.

### W18: part 3 wrap
- Depends: W17.
- `tests/replays/test_layout_end_to_end.py` extended: the toy layout's heights build from its pool (fake worker),
  activate under its key, and its rounds then fingerprint with that digest; base's digest unchanged with one layout.
  CLAUDE.md `control_heights` line: keyed by asset key. `probe_w18.py` set sha == W4.
- Check: the `[map-layouts]` and `control-geometry` suites' item lane; then `cp3`; then the slow lane
  (`test_catalog.py plan --slow` over the run's changed paths, re-collected with `-m slow`, `--marker slow`).
- Backend: Codex.

## Part 4: the redraw tool and runbook (branch `-4`, from part 3)

### W19: `scripts/map_layout.py align` and `diff` (ported from `~/rp/corrode`)
- Files: new `scripts/map_layout.py` (argparse subcommands) and `scripts/map_layout_fit.py` (numpy/Pillow: never under
  `app/replays`, which the worker keeps standard-library only, `tests/replays/test_control_isolation.py:37`; the fit:
  rotation ×90, scale, shift on floor masks, IoU; the diff: changed regions as connected components); ported from
  `~/rp/corrode/align.py`, `diff_check.py`, `occupancy.py` (read only; the logic, not the files' paths or data).
- Tests (red first), new `tests/replays/test_map_layout_tool.py`: a synthetic floor mask rotated 90° and scaled 1.3
  is recovered (rotation exact, scale within 0.01, IoU > 0.95); a region added to the toy mask is listed; positions in
  a closed region are counted.
- Real-data probe `probe_w19.py`: align of `~/rp/corrode/Corrode_api.png` (or the picture fit.json names) against the
  **pre-#197** minimap (`git show ebe6469b^:webapp/app/static/img/maps/Corrode.png`, written to the run folder)
  reproduces `~/rp/corrode/fit.json` (the figures' source): rotation 1 (90 CCW), scale 1.34 (±0.01), IoU 0.9074
  (±0.005) (unit: mask IoU).
- suites.toml: guards += `scripts/map_layout*.py`.
- Check: that file + `tests/test_test_catalog.py`; the probe.
- Backend: afk-implementer (reads `~/rp/corrode`).

### W20: `map_layout.py build`
- Depends: W19.
- Files: `scripts/map_layout.py build <Map> <patch> --picture <png> --regions <mask png> --into <asset dir>`: the
  layout minimap in the vendored style (wall transparent, floor (118,118,118), outer 2 px white; pixels outside the
  regions kept), masks via the existing build (`scripts/build_control_geometry.py`), kill lines for the key (or a
  refusal naming the command, since a key without `kill_lines.passes` is `no_map`: `replay_control.py:130`), chokes,
  `tags.json` entry and `callouts.json[key]` copied from the previous layout (callouts in changed regions listed for
  review), `layouts.json` entry, `index.json` / `tags.json` entries for the key, `must_block.<key>.json` (empty).
  Never `maps.json`.
- Tests (red first) in `tests/replays/test_map_layout_tool.py`: on a tmp copy of a toy map's assets, build writes the
  key's files and entries; every base entry and file is byte-identical after; `maps.json` untouched; the new layout
  passes `layouts` completeness, selects for its patch, and `map_layer(key)` is ready, not `no_map`.
- Backend: Codex.

### W21: the overlay page, valorant-api check and patch-notes reader
- Depends: W20.
- Files: `scripts/map_layout.py overlay` (a self-contained HTML in a scratch folder: old, new, positions, each
  region with keep/reject and "minimap can't show" marks, exporting the region mask); positions come from the
  friends DB's stored round blobs for the map (read-only, through `with_friends_db.py --read-only` at redraw time;
  `~/rp/corrode/export` is empty); `fetch-minimap` (valorant-api `/v1/maps`, compares with the vendored minimap;
  `vendor_map_assets.py` reused); `notes <patch>` (reads the patch page's Map Updates section and the map's
  subsection, saves text and picture URLs to the scratch folder). Network and DB reads behind one `fetch(url)` and
  one `positions(map)` function the tests replace.
- Tests (red first): a synthetic page in the patch-notes shape (written in the test, not a copy of Riot's) yields
  the map's lines; a fake valorant-api answer with a changed minimap says "differs"; the overlay HTML has one row
  per region and exports a mask, from synthetic positions.
- Backend: Codex.

### W22: the runbook
- Depends: W21.
- Files: new `docs/map-layout-redraw.md` (from the design's Section 3 and the Corrode runbook in the handoff:
  gather, picture, fit and diff, decide, build, PR; what the merge sets off; the deploy gap), CLAUDE.md pointer,
  `tests/suites.toml` final.
- Check: `[map-layouts]` suite; `tests/test_test_catalog.py`; `cp4`, then `final` at wrap-up.
- Backend: controller (prose) or Codex.

## What each merge sets off (for the cards)

- Part 1: nothing (golden test incl. the six bundle maps, LF-normalised must_block sha, probes' set sha equal W4's).
  No migration.
- Part 2: migration 0032 (one new table) on both services; the wall-share backfill (idle, behind uploads,
  `STEP_ROWS` per pass, wrapped so it never stops control); a first raise only if a patch is over the threshold.
- Part 3: nothing (base manifests equal golden); the worker image ships heights protocol 1; the Workflow image
  (`replay-tasks`) deploys separately and refuses, without a try, a regions build it can't read.
- Part 4: nothing (a script and a doc).
