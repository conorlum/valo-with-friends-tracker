# Map interaction tagger: step-level implementation plan

Date: 2026-10-04 (AFK run `2026-10-04-map-tagger`, stage P3). Input:
`docs/superpowers/plans/2026-10-04-map-interaction-tagger.md` ("the plan"; milestones M0-M6) and
`docs/superpowers/specs/2026-10-03-map-gimmicks-design.md` ("the design").

Boundaries (run register R1-R4): build M0-M6 on top of fresh origin/main, which already has timing-gaps
(`app/control/chokes.py`, `app/gaps/`, `app/control/routes.py`), so M5 is in scope. No engine rule
activation, no `CONTROL_REVISION` bump, no Impact change, no DB write, deploy, recompute or production asset
publication. Hearing stays unresolved: noise is stored as annotation only. Browser QA is headless Playwright
from the venv, screenshots in the run folder; the committed `tags.json` is never overwritten with preview data.

## Conventions used by every step

- Python: `<venv>` = `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv\Scripts\python.exe`.
  Every check runs from the worktree's `webapp` folder: `<venv> -m pytest -p no:cacheprovider -q <selection>`.
- Node checks run inside pytest through the existing `run_node` pattern of `test_control_tagger.py` (node
  on PATH; `C:\Program Files\nodejs\node.exe`).
- Shared fixtures live in `webapp/tests/fixtures/control/map_features/` (JSON) and are read by both the Python
  and the JS tests, so parity is checked on the same inputs.
- New stdlib-only modules: `app/replays/map_feature_schema.py`, `app/replays/map_feature_state.py`. They import
  nothing outside the standard library and `app.replays` (checked by `test_control_isolation.py`, which globs
  every `app/replays/*.py`, so no new isolation test is needed; each step that touches them reruns it).
- Numerical compilation: `app/control/features.py` (numpy/scipy allowed; never imported by the web app).
- Existing behaviour is pinned by `tests/replays/test_control_tagger.py`, `test_control_reference.py`,
  `test_control_geometry.py`, `test_control_floors.py`, `test_control_sight.py`, `test_control_task.py`,
  `test_control_remote.py`, `test_control_store.py` and the gaps tests. A step that edits a module reruns the
  tests that import it (`git grep -l <module> -- webapp/tests`).
- Full suite: 879 s on base, so it is split into two foreground halves (`tests/replays/test_control_*` +
  `test_gaps_*`; the rest of `tests/replays`) and run every 2-3 steps and at wrap-up, with
  `--deselect tests/replays/test_replay_worker_control.py::test_control_off_answers_404_and_parsing_is_untouched`.

## Contract decisions taken here (agent decides; listed on the M0 freeze card)

- **Schema shape** (`map_features`, version 1), one object per map entry in `tags.json`:
  `{"version": 1, "features": [...], "triggers": [...], "routes": [...], "checklist": {...},
  "next_id": n}`. Every object keeps unknown keys. IDs are `"<kind>-<n>"` strings, map-scoped, allocated
  from `next_id` and never reused.
- **Unresolved values** are `{"status": "unresolved", "note": ...}` objects, never `0`, `null`-as-guess or
  a default. A known number is `{"status": "known", "value": v, "unit": "s"|"m"}`.
- **Runtime vs editorial**: `name`, `notes`, `review`, `checklist`, `ui` and `parser_bindings` are
  editorial; everything else in features/triggers/routes is runtime. `runtime_digest` hashes the canonical
  JSON of the runtime projection only.
- **Reducer**: events at equal time are ordered by kind priority `reset < destroy < motion_complete <
  switch/shoot < proximity_leave < proximity_enter < scheduled`, then by input order. Scheduled
  completions carry `(generation, epoch)`; a mismatch is dropped and reported as `stale`.
- **Height reference**: bounds are `{"ref": "ground"|"world", "bottom", "top", "unit": "m", "floor": <floor
  binding id>}`; ground-relative resolves through the bound floor's node z minus `STAND_M`.
- **Consumer policies**: every reachability/distance/spread/sight caller in `app/control` and `app/gaps` is
  listed with a policy (`walk_only`, `transport_reachability`, `transport_time`, `bounded_sight`) in the M0
  contract doc. No consumer is switched over in this build (R3); adapters are opt-in.

## Steps (the run's work queue continues at W3)

Each step: one commit `W<n>: <goal>`. "Covers" names plan acceptance bullets (M0.1 = M0's first fixture
bullet, M1.a = M1's first acceptance sentence, and so on; see the coverage table).

### W3 — Python reducer and shared event fixtures (M0)
- Files: `app/replays/map_feature_state.py`; `tests/fixtures/control/map_features/reducer_cases.json`;
  `tests/replays/test_map_feature_state.py`.
- Does: pure reducer over a feature's transition table: `initial(feature)`, `apply(state, event)`,
  `run(feature, events)` -> trace. State carries `state`, `epoch`, `generation`, `occupants`, `pending`
  (scheduled follow-ups with generation/epoch), `terminal`. Events: `switch`, `shoot`, `destroy`,
  `proximity_enter`/`proximity_leave` (occupant id), `motion_complete`, `scheduled`, `reset`. Guards from a
  closed vocabulary (`state_in`, `not_terminal`, `occupants_eq`, `occupants_gt`, `motion_idle`). Mid-motion
  policy `ignore|restart|queue|reverse|unresolved`; `unresolved` rejects the event with a reason.
- Check: `-k` not needed: `tests/replays/test_map_feature_state.py tests/replays/test_control_isolation.py` passes,
  and the fixture file holds the five M0.1/M0.2 cases (destroyed mid-motion; stale completion; switch after
  break; reset with pending; two occupants with re-entry cancelling a close; equal-time destroy vs
  completion).
- Covers: M0.1, M0.2. Depends: W0.

### W4 — JavaScript reducer and parity (M0)
- Files: `scripts/control_tagger_core.js` (a `Features` section exported on `TaggerCore`),
  `tests/replays/test_map_feature_tagger.py`.
- Does: a line-for-line port of W3's reducer; the test runs every `reducer_cases.json` case through node and
  Python and compares traces exactly.
- Check: `tests/replays/test_map_feature_tagger.py -k reducer tests/replays/test_control_tagger.py` passes.
- Covers: M0 "Compare JavaScript/Python only where both implement the same contract". Depends: W3.

### W5 — Schema module: shapes, presets, validation, references, digests (M0 provisional, M1)
- Files: `app/replays/map_feature_schema.py`, `tests/fixtures/control/map_features/catalogue_full.json`
  (closeable+breakable door, rotating door, teleporter, zipline, same-u/v rope, proximity door, triggers,
  checklist, unknown fields at every level), `tests/replays/test_map_feature_schema.py`.
- Does: `SCHEMA_VERSION`, `PRESETS` (drop-door, switch door, proximity door, rotating door, breakable,
  zipline, vertical rope, teleporter, custom; unknown timing/height/range as unresolved objects),
  `CHECKLIST_SEED` (the plan's map table, no placements), `validate(mf) -> Report(errors, warnings)` with
  every structural error and warning the plan lists, `references(mf)` (every typed reference: trigger
  targets, parent doors, route owners, transition states, floor bindings, bundle members),
  `runtime_projection`/`runtime_digest`/`editorial_digest`, `canonical(mf)` round trip that preserves unknown
  keys, `check_version` (incompatible -> error, no migration guess).
- Check: `tests/replays/test_map_feature_schema.py tests/replays/test_control_isolation.py` passes; it includes
  same-position rope on distinct floors = no error/no warning, same-position same-floor = warning, renaming
  leaves `runtime_digest` unchanged, editing a trigger target changes it.
- Covers: M1.a (round trip), M1 fixture list, validation section, M0.6 (editorial vs runtime). Depends: W0.

### W6 — Feature geometry compile: rasterisation, footprints, per-floor movement (M0)
- Files: `app/control/features.py`, `tests/replays/test_control_features.py`,
  `tests/replays/control_toys.py` (only if a synthetic stacked geometry helper is needed).
- Does: deterministic rasterisers (point, polyline with width, polygon even-odd at 256-paint-cell centres)
  over minimap u/v -> 256x256 cells; `state_blockers(geo, mf, states)` -> per-node movement block mask and
  per-state sight paint; floor bindings select nodes (`floor_nodes(geo, binding)`; unresolved binding -> no
  nodes + a pending reason; never every floor); `compose_masks(base_sight, base_walk, mf, states)` for the
  preview parity in W15.
- Check: `tests/replays/test_control_features.py -k "raster or footprint or floor"` passes, including the M0.3
  movement part: on a synthetic two-floor geometry, a lower-floor door blocks the lower node and leaves the
  upper node walkable.
- Covers: M0.3 (movement), "never default a narrow door or rope to every floor". Depends: W5.

### W7 — Bounded occluders and sight adapters (M0)
- Files: `app/control/features.py`, `tests/replays/test_control_features.py`.
- Does: `BoundedOccluder(segs px, bottom_z, top_z, mode)` with resolution of ground/world bounds;
  `segment_blocked(occ, a(x,y,z), b(x,y,z))` (z at the XY crossing inside [bottom, top], inclusive bottom,
  exclusive top); adapters `cast_with(geo, ..., occluders)` (filters `geometry.cast`'s nodes by testing the
  eye-to-node-body segment per node, so different target heights behind one XY crossing differ),
  `los_with(geo, a, b, smokes, occluders)`, `seen_from_with(geo, src, smokes, occluders, eye_dz, body_dz)`.
  Unknown bounds -> occluder `pending` (not applied, reported); `all_height` mode explicit.
- Check: `tests/replays/test_control_features.py -k "sight or occluder"` passes: stacked-floor sight above,
  below and between floors agrees across `cast_with`, `los_with`, `seen_from_with`; same-XY vertical case;
  unknown bounds are reported as pending and block nothing.
- Covers: M0.3 (sight). Depends: W6.

### W8 — Base reconciliation and activation bundles (M0)
- Files: `app/control/features.py`, `tests/replays/test_control_features.py`.
- Does: the three feature-owned channels (`potential_ground`, `remove_sight`, `ground_binding`), each its own
  paint; `reconcile(base_masks, legacy_entry, mf, bundles)` composes only `enabled` bundles; a bundle is
  enabled only when every member's floor bindings are verified and fresh (height digest matches), its
  behaviour is supported and `runtime_consumer` is declared; overlapping owners compose by union, so opening
  one feature removes only its own contribution; permanent walls (base sight outside every owned
  `remove_sight` region) are never cleared; legacy cover/can't-walk overlap needs an explicit
  `reclassify` entry or the bundle stays pending.
- Check: `tests/replays/test_control_features.py -k "bundle or reconcile"` passes (M0.4 fixture: baked-in
  breakable over transparent pixels and legacy paint; second overlapping feature intact; stale binding and
  missing consumer each leave every base edit unpublished).
- Covers: M0.4, plan "Base-geometry reconciliation". Depends: W6.

### W9 — Directed traversal arcs and consumer policies (M0)
- Files: `app/control/features.py`, `tests/replays/test_control_features.py`.
- Does: `compile_routes(geo, mf)` -> directed arcs `(src node, dst node, entry_s, transit_s, length_m,
  states, status)`; endpoint-only vs listed access sites (sub-arcs); unknown costs stay `None` with status
  `unresolved` (never one step); `TraversalGraph(geo, arcs, policy)` with `reachable`, `walk_metres`
  (transport excluded), `time_seconds` (walking at a given speed + arc costs; unresolved arcs excluded and
  reported), `reverse()` for return paths, `available_at(t, availability)`.
- Check: `tests/replays/test_control_features.py -k "route or traversal"` passes: same-position rope joins the
  two bound floors only (no same-floor shortcut); zipline endpoint-only vs intermediate access differ;
  one-way return path uses the reversed graph; temporal availability; with no features
  `engine.special_links(geo)` output is unchanged.
- Covers: M0.5, plan "Directed traversal contract". Depends: W6.

### W10 — Consumed-input manifest (M0)
- Files: `app/control/features.py`, `tests/replays/test_control_features.py`.
- Does: `manifest(map_name, entry, compiled, height_sha)` -> `{"v", "schema", "compiler", "runtime",
  "compiled": {...hashes of node masks, arcs, occluders}, "bindings", "bundles"}` and `manifest_digest`;
  `verify(manifest, compiled)` -> mismatch list; `None` for a map with no enabled features.
- Check: `tests/replays/test_control_features.py -k manifest` passes: editorial edits leave the digest,
  enabling a bundle or changing semantics/compiler version changes it, a tampered compiled asset is
  detected, and `replay_control.geometry_inputs(<every committed map>)` is unchanged.
- Covers: M0.6. Depends: W7, W8, W9.

### W11 — M0 exit: contract document and consumer map (M0 gate)
- Files: `docs/superpowers/specs/2026-10-04-map-features-contract.md`; `tests/replays/test_map_feature_schema.py`
  (a doc-coverage test).
- Does: documents the frozen schema fields, reducer ordering/cancellation, height references, the
  unresolved/runtime-disabled representation, and a consumer table covering every caller of `topo.dist`,
  `topo.back`, `topo.spread`, `topo.label`, `topo.dilate`, `special_links`, `cast`, `los`, `seen_from` in
  `app/control/*.py` and `app/gaps/*.py`, each with a policy and "not switched in this build".
- Check: `tests/replays/test_map_feature_schema.py -k contract_doc` passes (it greps those call sites and asserts
  each enclosing function name appears in the doc's table).
- Covers: M0 exit gate. Decision: grouped tier 2 freeze card. Depends: W3-W10.

### W12 — JS model operations and canonical catalogue (M1)
- Files: `scripts/control_tagger_core.js`, `tests/replays/test_map_feature_tagger.py`.
- Does: `Features.create(mf, preset, name)`, `addTrigger`, `link`, `unlink`, `rename`, `remove(mf, id)` ->
  `{mf, dangling}` with `cascade`/`relink` options, `duplicate` (fresh IDs, internal links remapped,
  external targets only with `keepExternal`), `setGeometry`, history (`History` of immutable snapshots, undo
  /redo including links and properties), `importCatalogue(current, text)` (transactional: errors leave
  current; incompatible version -> refused with report), `exportCatalogue(canonical, edits)` (whole catalogue,
  unknown fields everywhere, maps absent from the page kept).
- Check: `tests/replays/test_map_feature_tagger.py -k "model or catalogue"` passes; every JS output is fed to
  Python `validate` with zero errors; delete -> undo restores references; duplicate remaps; import ->
  edit -> export keeps unknown fields at catalogue/map/feature/nested levels and a map not on the page.
- Covers: M1.a-M1.d. Depends: W4, W5.

### W13 — Draft snapshots and recovery (M1/M2)
- Files: `scripts/control_tagger_core.js`, `tests/replays/test_map_feature_tagger.py`.
- Does: `Drafts(storage, source)` over an injected key/value store: snapshot = `{provenance: {catalogue
  digest, image digests, floor digests, schema version}, revision, checksum, body}`; writes the new
  snapshot fully, then advances the active pointer, keeping the previous one; `load()` falls back to the
  previous on a bad checksum; `index()` lists drafts per source; quota/permission errors return
  `{ok: false, error}` and never claim saved; a draft from another source is offered, never applied.
- Check: `tests/replays/test_map_feature_tagger.py -k drafts` passes (interrupted save, corrupt active, quota
  error, source/image/floor change, failed migration).
- Covers: M2.c, plan "Saving". Depends: W12.

### W14 — Page: features panel, tools, properties, persistence (M2)
- Files: `scripts/control_tagger.py` (embeds `map_features` presets, checklist seed, schema version, floor
  metadata per map), `scripts/control_tagger.template.html`, `tests/replays/test_map_feature_tagger.py`.
- Does: a Features mode beside the existing modes: preset palette with the labels Switch/trigger,
  Breakable, Zipline, Vertical rope (plus the other presets); feature list; properties panel (status,
  initial state, transitions as plain controls, blocks movement / blocks vision per state, trigger mode,
  timing known/unknown, makes noise); zoom/pan; point/line/polygon/brush tools that write to the selected
  feature and state; link picking; direction arrows; undo/redo (Ctrl Z / Ctrl Y); autosave indicator via
  `Drafts`; import review dialog; export.
- Check: `tests/replays/test_map_feature_tagger.py -k page tests/replays/test_control_tagger.py` passes (page
  builds, script parses, needles for each control, existing exports unchanged).
- Covers: M2.a, M2.b (in-page; exercised in W19). Depends: W12, W13.

### W15 — Floors, routes and raster parity (M3)
- Files: `scripts/control_tagger_core.js`, template, `scripts/control_tagger.py`,
  `tests/replays/test_map_feature_tagger.py`.
- Does: landing/floor picker (known floors from the map's height asset when present; manual labels marked
  unresolved otherwise), route properties (directions, per-direction entry/transit cost known/unknown,
  endpoint-only / access sites), the three reconciliation tools; JS rasterisers and `composeFeatures` match
  W6 bit for bit.
- Check: `tests/replays/test_map_feature_tagger.py -k "raster or route or floor"` passes: JS vs Python cells on
  the fixtures; a same-position rope exports two landings on two floors; unresolved floors survive
  export -> import; Python `compile_routes` on the JS export creates no same-floor or horizontal arc.
- Covers: M3.a-M3.c. Depends: W6, W9, W14.

### W16 — State previews, rotation, checklist (M4)
- Files: `app/control/features.py` (rotation poses), core JS, template, tests.
- Does: state selector, trigger simulation through the JS reducer, Reset round, rotation slider (poses from
  pivot + start/end angle, or custom phases), composed blocker overlays, catalogue checklist with not
  started / in progress / user reviewed / no such feature; static kill-line diagnostics stay on permanent
  geometry only.
- Check: `tests/replays/test_map_feature_tagger.py -k "preview or rotation or checklist"
  tests/replays/test_control_features.py -k rotation tests/replays/test_control_tagger.py` passes: JS
  composed masks equal Python for intact/broken, open/closed/broken, a rotation pose, two features at once
  and after reset; the kill-line result for a map is identical with a closed door present.
- Covers: M4.a-M4.c. Depends: W8, W15.

### W17 — Freshness: geometry inputs and cache keys (M5)
- Files: `app/control/features.py`, `app/services/replay_control.py`, `app/control/task.py`,
  `app/services/replay_control_remote.py`, `app/services/replay_gaps.py` (only if its key needs it),
  tests in `test_control_features.py`, `test_control_task.py`, `test_control_remote.py`.
- Does: `geometry_inputs` gains a `features` key only when the map's index entry names an enabled feature
  generation (none do: R3); `geometry_used` reports the consumed manifest digest the same way; the remote
  comparison includes `features`; the task's geometry cache is keyed by `(map, heights, features digest)`.
- Check: those three files pass plus `tests/replays/test_control_reference.py tests/replays/test_gaps_task.py`;
  a test asserts every committed map's `geometry_inputs` and the fingerprint of a fixed link are byte-identical
  to base.
- Covers: M5 "No-feature maps retain current fingerprints", cache keys. Depends: W10.

### W18 — Freshness: generations, verification, storage rejection (M5)
- Files: `scripts/build_control_geometry.py` (writes `features/<digest>.json` generations and swaps a pointer
  atomically; only for maps with enabled bundles, i.e. none today), `app/control/task.py` (verifies loaded
  compiled features against the expected manifest; mismatch -> `infra`), `app/services/replay_control_store.py`
  and `scripts/compute_control.py` (store rejects a result whose consumed manifest differs from the planned or
  current one), tests.
- Check: `tests/replays/test_control_features.py -k generation tests/replays/test_control_task.py
  tests/replays/test_control_store.py` passes: interrupted publication leaves the last generation; stale
  generation refused; edit during a task rejects the old result; consumed hashes come from the loaded bytes.
- Covers: M5 remaining acceptance. Depends: W17.

### W19 — Visual QA, preview catalogue, instructions (M6)
- Files: `scripts/control_tagger.template.html` (short in-page instructions), `scripts/control_tagger.py`
  docstring; run-folder only: `qa/preview_catalogue.json`, `qa/qa_tagger.py`, screenshots.
- Does: builds the page with `--tags <preview catalogue>` into the run folder, drives it headless with
  Playwright: normal and zoomed screenshots, select features, switch states/floors, route arrows, rotation
  slider, undo/redo, storage-quota failure (stubbed `localStorage`), export -> reimport equality with
  Python validation; preview catalogue = one Summit door + one rope, a toy switch breakable door, a zipline.
- Check: `<venv> <run folder>\qa\qa_tagger.py` exits 0 and writes the screenshots; the exported
  catalogue validates in Python with zero errors and equals the edits.
- Covers: M6. Depends: W14-W16.

## Coverage table

| Plan acceptance | Steps |
| --- | --- |
| M0.1 compound door destroyed mid-motion, stale completion, reset epoch, guards | W3, W4 |
| M0.2 two proximity occupants, last exit, re-entry | W3, W4 |
| M0.3 stacked-floor door: walking per floor; sight in cast/los/seen_from | W6, W7 |
| M0.4 baked-in breakable, legacy paint, pending bundles unpublished | W8 |
| M0.5 same-position rope, transport costs, endpoint-only vs intermediate, return path, temporal | W9 |
| M0.6 manifest mismatch, enabled-set/semantics change, editorial no-op, legacy fingerprints | W5, W10, W17 |
| M0 exit gate | W11 |
| M1 round trip, identity, delete/undo, malformed import | W5, W12 |
| M1 fixture (door closeable+breakable, rotating, teleporter, zipline, same-u/v rope) | W5 |
| M1 canonical import -> edit -> autosave -> reload -> export, unknown fields | W12, W13 |
| M1 duplicate remap, delete/relink every reference type, incompatible imports | W12 |
| M2 multiple nearby objects, switch + shoot trigger, zipline/rope without JSON | W14, W19 |
| M2 save/reload, map change, undo deletion, import replacement | W13, W14, W19 |
| M2 recovery, corrupt snapshot, interrupted save, provenance, quota | W13, W19 |
| M3 same-position rope, zipline off-minimap, unresolved floors survive, no snapping | W15 |
| M3 directional costs, endpoint/intermediate, pending explained | W9, W15 |
| M4 state previews, rotation, multiple features, reset; JS = Python masks | W16 |
| M4 existing mask parity, kill-lines not mislabelled | W16 (+ test_control_tagger.py every step) |
| M4 reducer, nodes, arcs, occluders agree with shared contract; probes unavailable otherwise | W4, W15, W16 |
| M5 runtime vs editorial inputs, stale refused, edit-during-task, interrupted publication, no-feature fingerprints | W17, W18 |
| M6 browser QA, preview catalogue, instructions | W19 |

## Not in this build

Engine consumption of features (no consumer switched), replay decoders, Summit identity/timing, hearing,
recompute, publication of a real catalogue. Numerical route/sight probes in the page stay "unavailable"
(the plan allows this; the shared consumer is Python).
