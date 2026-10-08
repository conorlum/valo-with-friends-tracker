# Map features: the frozen contract (M0 exit)

Date: 2026-10-04 (AFK run `2026-10-04-map-tagger`). Plan: `docs/superpowers/plans/2026-10-04-map-interaction-tagger.md`
(M0); design: `docs/superpowers/specs/2026-10-03-map-gimmicks-design.md`. Status: **frozen** on
the agent's reading, approved by the user on 2026-10-04 (run decision D5). Every contract below is exercised by a
synthetic fixture; nothing here is consumed by the engine in this build, no map has an enabled feature, and
every committed map's control inputs and fingerprints are unchanged (`tests/fixtures/control/map_features/legacy_inputs.json`).

Amended 2026-10-08 (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 5, the owner's P5): a
floor binding no longer has to be read from the map's current height asset. It records the lowest floor of the
asset it was read from (origin_z) and its band is rebased to the current asset's, so a tagged feature persists
across rebuilds and follows its floor; one whose rebased band stops picking exactly one floor per cell is pending
and is listed in that rebuild's report. **Not amended:** section 8. A published generation still names the height
digest; a map that has one is not rebuilt automatically and its heights can't be changed by hand until generations
can be recompiled.

## Code

| Piece | Module | Tests |
| --- | --- | --- |
| Schema, presets, validation, references, runtime digest | `webapp/app/replays/map_feature_schema.py` (stdlib) | `test_map_feature_schema.py` |
| State reducer | `webapp/app/replays/map_feature_state.py` (stdlib) and `TaggerCore.Features.run` in `webapp/scripts/control_tagger_core.js` | `test_map_feature_state.py`, `test_map_feature_tagger.py` (exact trace parity) |
| Compilation, sight adapters, bundles, routes, manifest | `webapp/app/control/features.py` | `test_control_features.py` |

## 1. Schema fields (`map_features`, version 1)

One object per map entry in `tags.json`, beside the existing keys. Unknown keys are kept at every level.

- Top level: `version` (1; any other value is refused, never migrated by guess), `next_id` (high-water mark;
  ids are `<kind>-<n>` for feature, trigger, route, floor, bundle and are never reused), `features`,
  `triggers`, `routes`, `floors`, `bundles`, `checklist`; export metadata `image_sha`, `runtime_digest`.
- Feature: `id`, `name`, `preset`, `kind`, `category` (checklist key), `capabilities`, `states`
  (`name`, `blocks_movement`, `blocks_sight`, `terminal`, `footprint` geometry, `sight` occluders,
  `sight_bounds` for the footprint), `initial_state` (null = uncertain, a warning), `transitions`, `reset`,
  `floors` (floor ids, or unresolved), `parent`, `bundle`, `rotation` (`pivot`, `panel`, `direction`,
  `start_deg`, `end_deg`, `phases`), `noise` (`makes_noise`, `origin`, `notes`; annotation only, no hearing),
  `base_edits` (`potential_ground`, `remove_sight`, `ground_binding`, `reclassify` [{`source`, `geometry`}]),
  `review` (draft / needs_verification / user_reviewed), `notes`, `parser_bindings`.
- Trigger: `id`, `name`, `type` (switch / shoot / proximity / other), `geometry` (point, polygon or paint),
  `range` (proximity without an area: a value object, unresolved allowed, never a default radius), `floor`,
  `targets` [{`feature`, `event`}] (explicit; nothing pairs by proximity), `timing`, `noise`.
- Route: `id`, `name`, `owner`, `kind` (zipline / rope / teleporter / drop / custom), `endpoints` (exactly two,
  {`id`, `uv`, `floor`}), `path` (drawing only), `access` (`endpoint_only` or {`sites`: [{`id`, `uv`, `floor`}]}),
  `directions` [{`from`, `to`, `entry` s, `transit` s, `length` m}], `states` (owner states it runs in, or
  null), `in_transit` (complete / abort / unresolved).
- Floor binding: `id`, `label`, `z_band` [lo, hi] (metres of position-z above `origin_z`) or null (a manual
  label), `origin_z` (the lowest floor, in world decimetres, of the asset the band was read from), `height_sha`
  (that asset: a record, not a condition).
- Bundle: `id`, `members`, `enabled`, `runtime_consumer`.
- Geometry: minimap u/v (0..10000): `point`, `polyline` (+ `width`), `polygon` (even-odd), `paint` (the
  tagger's 256 x 256 bit format).
- Editorial (never in a runtime hash): `name`, `notes`, `review`, `ui`, `parser_bindings`, `category`; top-level
  `checklist`, `next_id`, `image_sha`, `runtime_digest`, `ui`, `notes`. List order is editorial too.

## 2. Unresolved and runtime-disabled representation

- An unknown fact is `{"status": "unresolved", "note"?}`; a known one `{"status": "known", "value", "unit"}`.
  Nothing defaults to 0, to every floor, or to an infinite wall.
- A transition with an unresolved duration enters its moving state and schedules nothing; an unresolved
  mid-motion policy rejects presses during the motion; an unresolved guard never fires.
- An occluder with unresolved bounds is saved and pending: it blocks nothing and is reported.
- A floor binding without a band, with a band whose frame is unknown (no `origin_z`, and another asset than the
  map's), or matching zero or several floors of a cell after rebasing, is pending: it binds nothing (never all
  floors).
- A route arc with an unknown cost exists for reachability only; time queries leave it out and list it.
- A bundle publishes its base edits only when enabled, named to a registered runtime consumer
  (`features.RUNTIME_CONSUMERS`, empty in this build), its behaviour is resolved, its floor bindings are
  verified, legacy overlaps are reclassified exactly and nothing outside it overlaps. Otherwise nothing of
  it is published and the map's geometry is unchanged.

## 3. Reducer ordering and cancellation

- Events: switch, shoot, activate (cause not yet classified), destroy, proximity_enter, proximity_leave,
  reset, observed (a decoder's verified state), and the reducer's own motion_complete and scheduled.
- Equal-time order: reset < destroy < motion_complete < switch = shoot = activate = observed <
  proximity_enter < proximity_leave < scheduled; then input order; input events before scheduled ones at a
  full tie. So destruction beats the obsolete completion it cancels, and an entering occupant keeps a door
  open when another leaves at the same instant.
- Every accepted transition increments `generation` and clears pending follow-ups; a reset starts a new
  `epoch` (and generation) and clears state, occupancy, pending and queued presses. A scheduled event whose
  (generation, epoch) no longer match is `stale`.
- Terminal states reject everything but reset. A row from the moving state answers first during motion;
  otherwise the moving transition's `mid_motion` policy (ignore, queue, restart, reverse, unresolved) does.
- Occupancy: ids count once (duplicates ignored), anonymous events count as anonymous; guards see the
  occupancy after the event's own change. Activation evidence never manufactures occupancy.

## 4. Height references and sight

- Floors are bound by height bands in metres above the binding's own `origin_z`; before use a band is rebased
  into the `node_z` frame (position-z metres above the map's current lowest floor).
- Occluder bounds are ground-relative (`ref: ground`, on a bound floor: the floor's physical ground is its
  node z - `STAND_M`, the median over the occluder's cells), world (`ref: world`, converted through the
  height asset's `origin_z`), `all_height`, or unresolved. The band is [bottom, top).
- One rule for every consumer (`features.blocked_lines`): the eye-to-target line is sampled every pixel
  (ends excluded) and is blocked inside an occluder's mask at a height inside its band; a straight-up line is
  blocked when its z span meets the band. Eye = node z + `EYE_M`; target body = node z + `BODY_M`. A node
  with no height is tested in 2D (and counted).
- Base visibility stays the maximally open map; occluders only remove sight (`cast_with`, `los_with`,
  `seen_from_with`). A flat map has no heights: resolved bounds block in 2D there.

## 5. One base domain

The round's walkable ground is the permanent ground plus every publishable bundle's potential ground,
fixed for the round, so node indices never depend on a feature's state. A state adds a movement block over
nodes (`movement_blocks`) and occluders; opening a feature removes only its own contribution. Restored ground
on a height map needs a floor in the height asset (rebuild the heights); it never takes the unresolved-cell
fallback that joins every neighbouring floor.

## 6. Traversal metric policies

| Policy | Meaning |
| --- | --- |
| `walk_only` | 8-connected walking over the base domain minus blocked nodes; transport never counts (ownership metrics, metres) |
| `transport_reachability` | can it be reached at all: walking plus every permitted arc (known or unknown cost) |
| `transport_time` | seconds: walking at a speed plus resolved arcs' entry + transit; temporal queries respect availability and the in-transit policy (unresolved policies left out) |
| `bounded_sight` | sight through `cast_with` / `los_with` / `seen_from_with` with the state's occluders |
| `base` | runs on the permanent map only (asset builds, freshness inputs); features never change it |

Return paths search the reversed graph (`TraversalGraph.reverse`). Legacy `specials` stay as they are and
read-only; a route repeating one is flagged by validation so it can't run twice.

## 7. Consumers

Every function in `app/control` and `app/gaps` that reaches a topology, a sight query or the specials
(found by `webapp/tests/replays/map_feature_consumers.py`; `test_map_feature_schema.py` checks this table
names each). **None is switched to features in this build.**

| Consumer | Uses | Policy when features are enabled |
| --- | --- | --- |
| `control/chokes:detect` | specials | `base` (chokes come from the permanent map) |
| `control/engine:RoundInputs._add_watcher` | cast, topo.dilate | `bounded_sight`; `walk_only` |
| `control/engine:RoundInputs._trip_end` | topo.around | `walk_only` |
| `control/engine:Tick.__init__` | cast | `bounded_sight` |
| `control/engine:Tick._backfill_shares` | topo.dilate | `walk_only` |
| `control/engine:Tick._direct` | los | `bounded_sight` |
| `control/engine:Tick._flood` | topo.label | `transport_reachability` |
| `control/engine:Tick._links` | special_links, topo.links | `transport_reachability` (arcs replace the all-floor specials for features) |
| `control/engine:Tick._pocket` | topo.label | `walk_only` |
| `control/engine:Tick._presence` | seen_from, topo.dilate | `bounded_sight`; `walk_only` |
| `control/engine:Tick._watch` | cast | `bounded_sight` |
| `control/engine:Tick.boundary_seen` | seen_from, topo.dilate, topo.edge_out | `bounded_sight`; `walk_only` |
| `control/engine:Tick.comp_seen` | seen_from | `bounded_sight` |
| `control/engine:Tick.compose` | seen_from, topo.dilate, topo.label | `bounded_sight`; `transport_reachability` |
| `control/engine:Tick.dist_from` | topo.dist | `walk_only` (step counts stay steps; no seconds mixed in) |
| `control/engine:Tick.fill` | topo.label | `transport_reachability` |
| `control/engine:Tick.way_back` | topo.back, topo.dilate | `walk_only` on the reversed graph |
| `control/engine:Unknown.__init__` | special_links | `transport_time` (arcs in seconds; unknown costs left out) |
| `control/engine:Unknown._area` | topo.dilate | `walk_only` |
| `control/engine:Unknown._drop_pieces` | topo.label | `transport_reachability` |
| `control/engine:Unknown._spread` | topo.spread | `transport_time`, temporal (availability history; reopened paths restart eligibility) |
| `control/engine:_share_by_walk` | topo.dilate | `walk_only` |
| `control/engine:barrier_start` | topo.around, topo.label | `walk_only` |
| `control/engine:possible_region` | topo.dilate | `transport_time`, temporal (knowledge regions respect when passages were open) |
| `control/engine:special_links` | specials | `base` (legacy specials, unchanged) |
| `control/geometry:build_visibility` | cast | `base` (the open map's cached rows) |
| `control/height_build:geo_los` | los | `base` |
| `control/task:geometry_used` | specials | freshness: the manifest's `features` key (W17) |
| `control/utility:_sees_point` | los | `bounded_sight` (added 2026-10-05, utility-review run: whether an enemy sees a Leer's eye) |
| `control/utility:read_pulses` | cast | `bounded_sight` (added 2026-10-05: what a Haunt or recon pulse sees) |
| `control/utility:read_skye_flashes` | cast | `bounded_sight` (added 2026-10-05: what an empty Skye flash rules out) |
| `gaps/detect:GapDetector._sight` | cast | `bounded_sight` |

## 8. Freshness

`features.manifest` is None for a map with no publishable bundle: no `features` key appears in its control
inputs, so its fingerprint is unchanged. Otherwise it covers the enabled runtime definitions, compiled
blocked nodes / occluders / arcs (hashed from canonical bytes), the height digest and the schema, compiler,
reducer-semantics and consumer versions; `features.verify` re-hashes what was loaded and recompiles the
definitions. Editorial edits never move it.
