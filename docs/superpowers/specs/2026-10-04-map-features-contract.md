# Map features: the frozen contract (M0 exit)

Date: 2026-10-04 (AFK run `2026-10-04-map-tagger`). Plan: `docs/superpowers/plans/2026-10-04-map-interaction-tagger.md`
(M0); design: `docs/superpowers/specs/2026-10-03-map-gimmicks-design.md`. Status: **frozen** on
the agent's reading, approved by the user on 2026-10-04 (run decision D5). Every contract below is exercised by a
synthetic fixture; nothing here is consumed by the engine in this build, no map has an enabled feature, and
every committed map's control inputs and fingerprints are unchanged (`tests/fixtures/control/map_features/legacy_inputs.json`).

Amended by the 2026-10-08 map-features-survive-height-rebuilds design: authored minimap shapes and behaviour are permanent; each required cell with exactly one real floor selects that floor from the compiled height asset, without owner floor tagging. A gimmick touching a multi-floor cell or otherwise unplaceable is pending, contributes no runtime effects, is listed with reasons in that rebuild report, and is never deleted; ground-relative bounds are derived afresh from the selected floor. Section 8 selects immutable per-map compilation artifacts by exact height, normalized tags and compiler inputs, with each round recording its artifact digest. After the replacement ships, tagged maps rebuild and permit manual height activation/off under normal height rules; before then the existing E6 generation guards remain.

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
- Feature: `id`, `name`, `preset`, `kind`, `category` (checklist key), `capabilities`, `states` (`name`, `blocks_movement`, `blocks_sight`, `terminal`, `footprint` geometry, `sight` occluders, `sight_bounds` for the footprint), `initial_state` (null = uncertain, a warning), `transitions`, `reset`, `floors` (preserved legacy source; excluded from runtime), `parent`, `bundle`, `rotation` (`pivot`, `panel`, `direction`, `start_deg`, `end_deg`, `phases`), `noise` (`makes_noise`, `origin`, `notes`; annotation only, no hearing), `base_edits` (`potential_ground`, `remove_sight`, `ground_binding` (preserved legacy source; excluded from runtime), `reclassify` [{`source`, `geometry`}]), `review` (draft / needs_verification / user_reviewed), `notes`, `parser_bindings`; feature and restored-ground placement use the automatic one-floor rule.
- Trigger: `id`, `name`, `type` (switch / shoot / proximity / other), `geometry` (point, polygon or paint), `range` (proximity without an area: a value object, unresolved allowed, never a default radius), `floor` (preserved legacy source; excluded from runtime), `targets` [{`feature`, `event`}] (explicit; nothing pairs by proximity), `timing`, `noise`; trigger placement uses the automatic one-floor rule.
- Route: `id`, `name`, `owner`, `kind` (zipline / rope / teleporter / drop / custom), `endpoints` (exactly two, {`id`, `uv`, `floor`}), `path` (drawing only), `access` (`endpoint_only` or {`sites`: [{`id`, `uv`, `floor`}]}), `directions` [{`from`, `to`, `entry` s, `transit` s, `length` m}], `states` (owner states it runs in, or null), `in_transit` (complete / abort / unresolved); endpoint/access `floor` fields are preserved legacy source excluded from runtime, and placement uses the automatic one-floor rule.
- The legacy `floors` catalogue and its fields are preserved for source round trips but no longer select runtime floors or prompt the owner; compilation derives placement only from shapes and the exact height asset. Schema version remains 1; this explicit semantics change has a new normalization/compiler version, and unsupported source versions remain refused.
- Bundle: `id`, `members`, `enabled`, `runtime_consumer`.
- Geometry: minimap u/v (0..10000): `point`, `polyline` (+ `width`), `polygon` (even-odd), `paint` (the
  tagger's 256 x 256 bit format).
- Editorial (never in a runtime hash): `name`, `notes`, `review`, `ui`, `parser_bindings`, `category`; top-level
  `checklist`, `next_id`, `image_sha`, `runtime_digest`, `ui`, `notes`. List order is editorial too.

Known legacy floor selectors, including ground-relative bounds.floor, are excluded from the new normalized runtime view; other unknown keys are retained and hashed. Ground-relative bounds themselves remain authored behaviour.

## 2. Unresolved and runtime-disabled representation

- An unknown fact is `{"status": "unresolved", "note"?}`; a known one `{"status": "known", "value", "unit"}`.
  Nothing defaults to 0, to every floor, or to an infinite wall.
- A transition with an unresolved duration enters its moving state and schedules nothing; an unresolved
  mid-motion policy rejects presses during the motion; an unresolved guard never fires.
- An occluder with unresolved bounds is saved and pending: it blocks nothing and is reported.
- On a height asset, every required cell of a gimmick must have exactly one real finite floor; zero floors, unresolved heights, multiple floors or unplaceable required geometry makes the whole gimmick pending with no runtime effects, with every reason listed in that rebuild report and all authored data retained. A flat map uses its one walkable 2D node per cell.
- A route arc with an unknown cost exists for reachability only; time queries leave it out and list it.
- A bundle contributes runtime assets and base edits only when enabled, named to a registered runtime consumer, its required behaviour is resolved, every member/dependent placement satisfies the one-floor rule, legacy overlaps are reclassified exactly and nothing outside it overlaps. Otherwise the whole bundle contributes nothing; its pending or disabled status is recorded, independent bundles remain eligible, and a height rebuild passing the height gate still goes live.

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

- Each required cell binds to its sole real floor in the exact height asset named by the compilation; a multi-floor or unplaceable gimmick is pending, and authored bands/old floor IDs never choose among floors.
- Occluder bounds are ground-relative (`ref: ground`, using median physical ground, node z - `STAND_M`, of automatically selected floors under its cells), world (`ref: world`, converted through the exact height asset's `origin_z`), `all_height`, or unresolved; all resolved forms still require gimmick placement to pass the one-floor rule. The band is [bottom, top).
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
| `control/engine:RoundInputs._molly_nodes` | topo.dilate | `walk_only` (added 2026-10-09, replay-player-state run: a molly's footprint, the walk inside its circle) |
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
| `control/features:verify_permanent_context` | specials | `freshness`: verify exact permanent archive context on every invocation |
| `control/feature_diagnostics:diagnose_features` | specials | `base`: diagnostics only, exact admitted/archived permanent context, no runtime effects |
| `control/height_job:features_pending` | specials | `base`: provisional diagnostics only, never a height gate |
| `control/task:geometry_used` | specials | freshness: the manifest's `features` key (W17) |
| `control/utility:_sees_point` | los | `bounded_sight` (added 2026-10-05, utility-review run: whether an enemy sees a Leer's eye) |
| `control/utility:read_pulses` | cast | `bounded_sight` (added 2026-10-05: what a Haunt or recon pulse sees) |
| `control/utility:read_skye_flashes` | cast | `bounded_sight` (added 2026-10-05: what an empty Skye flash rules out) |
| `gaps/detect:GapDetector._sight` | cast | `bounded_sight` |

## 8. Freshness

`features.manifest` is None when no bundle is intended for a registered runtime consumer: no `features` key appears in control inputs and legacy fingerprints remain unchanged; intended pending bundles produce a manifest recording their disabled outcome. Otherwise its digest identifies an immutable per-map database artifact covering intended runtime definitions, pending outcomes, complete compiled nodes / occluders / arcs / base edits and canonical hashes, exact height digest, relevant base/legacy inputs, and schema, normalization, compiler, reducer-semantics and consumer versions; a round records this digest, and verification rehashes loaded bytes and recompiles archived definitions against recorded geometry under the recorded compiler. Editorial edits never move it.
