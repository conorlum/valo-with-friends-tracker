# Map interaction tagger: implementation plan

Date: 2026-10-04. Planning deliverable only; implementation has not started.
Revised after the user's pasted static review: all eight findings are incorporated below.
The schema is provisional until the M0 consumer-contract prototype passes.

Design context: `docs/superpowers/specs/2026-10-03-map-gimmicks-design.md`.

## Outcome

Extend the existing local map-control tagger so the user can supply every map feature's location,
geometry, connections, and behavior visually. The user should be able to mark a switch or trigger,
paint the object it affects, connect zipline/rope/teleporter endpoints, preview each state, and
export a complete annotation file without writing JSON or knowing replay actor GUIDs.

The tool produces map definitions. Per-round activation times still come from replay decoders.
An annotation can be complete for the user while its replay signal remains unverified; keep those
two statuses distinct. Painting alone must never silently activate an unsupported engine rule.

## Extend the existing tool

Keep `webapp/scripts/control_tagger.py` as the entry point and its self-contained local HTML output.
Reuse the minimaps, permanent paints, import/export, existing masks and kill-line diagnostics.
Extend `control_tagger_core.js` and `control_tagger.template.html`; keep model operations testable
outside the DOM. Existing annotations and unknown fields must round-trip unchanged.

Add zoom/pan, feature selection and state previews to the existing page. No account or backend is
needed to annotate a map. Save drafts locally and provide downloadable exports; the page does not
write directly into the repository or trigger any control recompute.

## User workflow

1. Select a map and see its feature checklist, existing annotations, and selected floor.
2. Choose an interaction preset: drop-door, switch door, proximity door, rotating door,
   breakable block/door, zipline, vertical rope, teleporter, or a custom feature.
3. Give the feature a readable name, such as "Mid drop-door". Generate a stable map-scoped ID.
4. Draw its geometry using the appropriate paint, line, point, polygon, or endpoints.
5. Mark/link its switches or triggers. Add additional triggers to the same object where needed.
6. Set its round-start state and behavior using plain-language controls. Unknown facts can remain
   explicitly "Needs verification"; no guessed timing, height, range, or travel speed.
7. Preview states or run a simulated activation sequence. Inspect movement, sight, and connections.
8. Review validation, save/export, and move to the next feature or map.

Example: create "A door" using the switch-door preset, paint its closed footprint, place a nearby
switch, link the switch to that door, mark it breakable, and compare open/closed/broken previews.
Example: create a vertical rope, place its lower landing, change floor, and place its upper landing.
Both landings may share the same minimap position; their floors still distinguish them.

## Drawing tools and the information they capture

| Tool | How the user draws | Information saved |
| --- | --- | --- |
| Switch / trigger | Click a switch or shootable target; paint an activation area for proximity | Trigger type, location/area, floor, target feature IDs, activation behavior, optional notes |
| Door / moving obstruction | Paint closed footprint; draw sight-blocking edge; edit alternate states | Movement footprint, sight geometry, affected floors, initial state, state changes, optional motion phases |
| Breakable paint | Brush or polygon over one selected object | Intact footprint, movement/sight effects, cleared footprint when broken, reset each round, optional parent door |
| Zipline here | Draw a line/polyline and place both landings | Route, endpoint locations/floors, allowed directions, activation/access areas, travel timing status |
| Vertical rope here | Place lower/upper landings, with optional rope location/attachment marker | Same-position or separate endpoints, lower/upper floors, directions, travel timing status |
| Teleporter | Paint entry area and place exit landing; link them | Entry/exit geometry, floors, direction, transit timing status, optional noise cue |
| Custom connection / drop | Draw endpoints and optional route | One-way/two-way route and floor restrictions for future exceptions |

Keep the requested labels visible in the palette: **Switch/trigger**, **Breakable**, **Zipline**,
and **Vertical rope**. Other presets guide the user through those tools instead of making them
assemble a door's states manually from unrelated paints.

Every brush stroke belongs to the selected feature and state. Two nearby breakables remain two
objects; erasing or breaking one cannot remove the other. Object selection, rename, duplicate,
move, erase geometry, and delete are distinct operations with undo/redo.

Breakable is a capability as well as a preset: a switch-operated Ascent/Sunset door can be both
closeable and breakable. Triggers are linked objects, not marks assumed to affect the nearest door.
Support multiple triggers per feature and explicit multi-target triggers without implicit pairing.

### Properties panel

Show relevant controls for the selected preset; do not expose engine implementation details in
the ordinary workflow.

- Name, feature type, notes, and annotation status: draft / needs verification / user reviewed.
- Initial state; guarded state changes; whether activation can repeat; reset at the next round.
- Separate "Blocks movement" and "Blocks vision" controls for each obstruction state, initially
  both checked for the doors/breakables identified by the user.
- Trigger mode: use switch, shoot trigger, proximity occupancy, or other/unknown. Selecting
  proximity requires an area or an explicitly unresolved range; no invented default radius.
- Breakability and terminal broken state for the identified per-round breakables. A button cannot
  restore a destroyed object within the same round.
- Direction arrows and endpoint/floor assignments for traversal routes. Distinguish route travel
  from normal walking; unknown travel duration/speed remains explicit.
- Timing: immediate, known duration/offset, or unknown. Movement and sight transition times may
  differ. Keep duration and cooldown optional unless verified for that mechanic.
- "Makes noise" plus optional sound origin and notes. No default hearing radius or guaranteed
  player attribution. Hearing behavior remains a separate engine design decision.

### Behavior contract: guarded transitions

Store a transition table, not only a state list and repeatable flag. Each row has a stable ID,
source state(s), action/event, declarative guard, destination state, separate movement/sight timing,
optional automatic follow-up, and cancellation policy. Conditions use a versioned vocabulary;
never export executable expressions. Unknown mechanics remain explicit unresolved fields.
Plain-language preset controls edit this table for the user.

Events distinguish switch use, shoot-trigger activation, destruction, proximity enter/leave,
occupancy change, scheduled motion completion, and round reset. Proximity policies specify first
entry, continuous occupancy, last exit, optional close delay, and what re-entry does to a pending
close. Motion policies specify ignore/restart/queue/reverse or unresolved for a mid-motion press;
only supported, verified policies become runtime-ready.

The pure reducer tracks state, occupancy identities/counts where supplied, transition generation,
and round epoch. Scheduled follow-ups carry their generation/epoch. Destruction cancels pending
state/motion completions; terminal broken rejects later switch/completion events. Reset starts
a new epoch and clears pending actions and occupancy. Specify equal-time ordering, including
destruction taking precedence over obsolete completion of the destroyed feature. A superseding
motion or proximity re-entry invalidates timers according to the declared policy.

Use one reducer contract for sandbox and later runtime interpretation, with stdlib Python as
the reference and JavaScript checked against the same event-sequence fixtures. Simulated
occupants are sandbox inputs, not inferred replay evidence. Decoders may provide verified observed
states directly; activation-only evidence cannot manufacture missing occupancy/guard information.
Keep evidence provenance and unresolved causal interpretation explicit.

### Rotating doors

Provide a pivot marker, panel footprint, rotation direction, start/end orientation, and optional
intermediate poses. A motion slider displays the panel and resulting blocked passages. A simple
rigid panel may derive poses from the pivot; custom phase geometry can override that approximation.
Repeated activations replay the declared motion sequence and return to its declared stable state.
Do not assume every door rotates 90/180 degrees, has the same duration, or accepts a press mid-motion.
Unsupported or uncertain motion details are stored as such and shown in the checklist.

## Floors, coordinates, and missing minimap features

Store canonical geometry in existing minimap coordinates (0..10000), independent of canvas zoom,
browser size, or paint grid. Retain the existing 256x256 paint bit format for footprint masks where
appropriate; rasterize point/line/polygon tools deterministically. Display the engine's 128-cell
grid as an optional overlay so narrow-door or diagonal leaks are visible.

Embed available height/floor metadata in the page using the generator. Let users pick a landing's
floor visually, use a known height if available, or mark it unresolved. Do not use 0 as a substitute
for unknown height. Manual floor labels are annotations until bound to actual engine nodes;
changing height assets must revalidate those bindings rather than silently redirect a route.

Support two different floors at the same u/v position. A selected feature can affect one floor,
several explicitly selected floors, or a verified vertical span. Never default a narrow door or
rope to every floor of an overlapping minimap cell. The preview must make floor selection visible.

### Bounded sight geometry

Movement floors and sight bounds are different properties. For each sight-blocking panel/shape,
store XY geometry and bottom/top bounds as world-z metres, or verified offsets from a bound
floor's physical ground. Record units, reference, uncertainty and the source floor-asset digest.
Existing assets hold standing-player position-z: physical ground is `position_z - STAND_M`.
The compiler resolves bounds to the geometry's map-relative metres. The ordinary properties
panel uses ground-relative measurements; raw coordinates remain advanced metadata.

Do not reuse the unbounded XY `Wall` for a floor-limited feature. A bounded occluder tests a sight
segment's z at its XY intersection against the vertical bounds. `cast` must distinguish target
nodes/slopes instead of stopping every height at the first XY crossing; `los` checks the actual
eye-to-target segment; `seen_from` filters cached source/target pairs with the same vertical
intersection semantics and explicit consumer height offsets. Use identical boundary conventions
and include vertical same-XY sight cases.

Unknown bounds remain saveable/pending, never an assumed floor-height or infinite door. Verified
all-height blockers may use explicit all-height mode. Unresolved height queries are reported
instead of silently claimed to be floor-correct.

Allow routes and landings to be drawn even where the minimap lacks a zipline or rope symbol.
Missing base walkability is flagged, not snapped to distant ground. A separate, feature-owned
"Accessible when open/broken" paint can restore genuine passage ground omitted from the base
map; show the exact pixels/cells it opens and require review of that geometry.

### Base-geometry reconciliation

Provide three independently editable, feature-owned definitions: **Potential ground**, **Remove
baked-in sight obstruction**, and **Ground/floor binding**. Restore walkability, reclassify the
feature's baked-in sight pixels, and bind restored ground to actual floors/connections separately.
Preview each edit and the combined result; one channel never implicitly changes another.

For overlapping legacy cover/can't-walk tags or paint, show the source and require explicit
reclassification of the exact feature-owned portion. Preserve genuine permanent walls and
unrelated features. Record ownership/resolution; never apply blanket mask subtraction. Multiple
blockers combine by union/reference ownership, so opening one removes only its contribution.
Ambiguous ownership remains pending.

Newly restored cells require verified floor bindings and explicit neighboring connections; do
not inherit the current unresolved-cell fallback that connects every neighboring floor. Stale
height assets invalidate these bindings. Gate base edits, floor bindings, initial-state blockers,
behavior support and runtime consumer as one activation bundle. Dependent/overlapping edits that
cannot be enabled independently share an atomic bundle. A pending bundle publishes none of its
base-opening edits; current published geometry remains unchanged. Successful compilation alone
does not enable a dynamic feature before its runtime/replay inputs exist.

### Directed traversal contract

Routes compile into directed arcs between explicitly bound access/landing nodes. Each direction
records endpoint-only or specific intermediate access, entry/transit costs, physical path length
where applicable, units, active states/availability intervals, and known/unresolved status. Time
costs use seconds; walking length uses metres. Durations use constants or verified length/speed
rules separately per direction. Never replace an unknown transit cost with one walking step.
Same-XY ropes require distinct floor bindings.

A polyline is route geometry, not automatically intermediate access or walkable ground. Where
boarding/alighting partway is supported, annotate access sites and derive directed sub-arcs/costs.
Endpoint-only routes stay endpoint-only. Teleporters do not open ground or sight between endpoints.

One compiled graph supplies every consumer. Boolean reachability uses permitted directed arcs.
Ordinary walking-distance queries use metres. Movement-time distances/backtracking, Unknown
arrival times and knowledge regions use entry + transit seconds on transport arcs and length/speed
on walking arcs. Update step-count callers explicitly; never mix seconds into cell-count arrays.
Pure walking/4-connected ownership metrics keep their existing definition; any metric allowed to
use transport gets a named, tested policy. Directed return paths search the correct reversed graph.
Temporal reachability respects availability and the declared policy for transit already underway
at closure. An unresolved policy is saveable but prevents runtime activation where required.

M0 maps every current reachability/distance/backtracking/Unknown/knowledge caller to this contract
and proves that no consumer silently inherits today's all-floor, one-step special-link behavior.

## Map checklist

Seed editable checklist categories from the user's catalogue, with no fabricated placements:

| Map | Checklist |
| --- | --- |
| Abyss | Breakable blocks; vertical ropes |
| Ascent | Switch-operated breakable doors |
| Bind | Teleporters; noise cue annotation |
| Breeze | Proximity-operated doors; noise cues; vertical ropes |
| Corrode | User reported no features |
| Fracture | One proximity-operated door; noise cue; vertical ropes |
| Haven | User reported no features |
| Icebox | Zipline; vertical ropes |
| Lotus | Rotating doors; breakable door; vertical ropes |
| Pearl | User reported no features |
| Split | Vertical ropes |
| Summit | Three drop-doors; vertical ropes |
| Sunset | Closeable breakable door |

Show not started / in progress / user reviewed per category, plus "No such feature" and notes.
Unknown counts remain unknown. Add arbitrary instances without a hardcoded maximum; enforce
the known one/three counts only as review warnings. "No features reported" is user input, not a
verified engine assertion. Keep interaction review separate from the current cover-reviewed badge.

## Storage contract

Define a versioned `map_features` object in each map's existing `tags.json` entry. Its version is
independent of existing tags/paint compatibility. Freeze the schema before UI implementation.
Freeze only after M0 proves the reducer, bounded sight, reconciled ground and directed-route
contracts on synthetic fixtures. M1 formalizes that tested shape.

The object contains:

- Features: stable ID, readable name, preset/kind, capabilities, states, guarded transition table,
  initial/reset behavior, floor bindings, bounded sight geometry per state, base-edit ownership,
  motion description where relevant, notes/review status.
- Triggers: stable ID, type, point/area geometry, floor binding, explicit target feature IDs and
  activation action, optional timing/noise metadata.
- Routes: stable ID, owning feature, bound endpoints/access sites, endpoint/intermediate access
  policy, optional path, directed arcs/cost components with units and known/unknown status,
  availability and in-transit policy if conditional.
- Checklist: map/category review status and explicitly unresolved facts.

Use references instead of copying whole definitions into triggers. Renaming preserves identity.
Deletion surfaces linked references and offers a concrete cascade or relink action; it cannot
leave hidden dangling links. Multiple overlapping features compose; opening one cannot clear
another feature's blockers or a permanent wall.
Duplication allocates fresh IDs for copied features/owned components and remaps internal links;
external targets remain only through an explicit duplicate action. Reference validation and
delete/relink include parent doors, route owners, trigger targets, transitions and optional bindings.

The map definition does not include a recording's activation timestamps. A later replay decoder
binds actor-path identities to these stable feature IDs. Parser bindings are optional advanced
metadata owned by investigation tooling; ordinary annotation never requires GUID entry.

Provide a stdlib-only schema reader/validator in `app/replays/map_feature_schema.py` for the shared
structural contract. This location is copied into the upload worker alongside the condenser.
Numerical geometry compilation stays in `app/control`, preserving upload-worker import isolation. Runtime
imports must not pull the tagger's DOM, image generation, or scipy into the replay condenser.

Export round-trips every map and all existing/unknown fields. Include a map image checksum and
feature-definition digest for relevant edits. Hash runtime geometry/behavior separately from
names, notes and review status so cosmetic annotation changes do not force a control recompute.
Legacy `specials` stay readable; define explicit
IDs/migration if importing one as an editable route so it cannot run twice. A map with no new
features retains existing geometry and control inputs exactly.

### Versioned consumed-input manifest

Define one manifest covering runtime definitions, compiled masks/nodes/arcs/bounded occluders,
floor bindings and height hashes, enabled activation bundles, and schema/compiler/consumer
semantics and versions. Hash actual canonical definitions and consumed asset bytes, not merely
planner index claims. Editorial metadata stays outside runtime hashes; enabling features changes
runtime inputs.

Both local and remote tasks receive expected inputs and load one immutable asset generation.
Before computing, verify loaded content and definition-to-compiled-asset correspondence against
that manifest. Results return the consumed manifest. Before storage, compare it with the planned
manifest and freshly resolved current inputs, including replay/link data. Edits during computation
reject obsolete output; never store old output with a new fingerprint. Update the local command
and shared store path as well as the remote six-key comparison. Input mismatch is a retryable
infrastructure/staleness outcome, not a bad round.

Build and verify a complete content-addressed asset generation, then atomically replace its active
manifest pointer. In-flight tasks keep their immutable snapshot. Worker geometry/topology caches
use map + consumed-manifest digest instead of map name alone. Services reread the active pointer
before planning/storage and invalidate process input caches on generation change. Maps with no
enabled features retain absent feature keys and current fingerprints; the additive manifest
contract must not force an unrelated-map recompute merely by existing.
This compatibility guarantee applies to the additive annotation-tool build. A later engine rule
activation and `CONTROL_REVISION` bump still follows the existing global versioning policy.

## Preview, validation, and saving

### Preview

- Layer toggles for permanent paint, features, triggers, routes, floors, sight blockers, blocked
  movement, and the engine grid. Labels and direction arrows remain readable while zoomed.
- Select open/closed/intact/broken states and scrub motion. Trigger clicks simulate their declared
  action; Reset round restores all initial states. This is a sandbox, not observed replay evidence.
- Place start/end probes to show whether a route is reachable and which links it uses; place a
  sight probe to show a test line blocked/unblocked only when backed by the shared engine consumer
  or M0 contract prototype. Otherwise show geometry/state overlays and mark numerical probes
  unavailable. Do not build a second pathing engine just for JavaScript previews. Contract parity
  covers compiled nodes, directed arcs, bounded sight and state reduction, not masks alone.
- Show new accessible ground and disconnected landings. Features invisible on the minimap remain
  visible as overlays. Compare multiple simultaneous feature states.
- Existing permanent-wall kill-line diagnostics remain evaluated against their original static
  geometry. Dynamic-state sight tests are separate: historical lines cannot all be required to
  pass when a door is manually forced closed without knowing its replay state at each kill.

### Validation

Structural errors: duplicate IDs, dangling references, malformed coordinates/paints, invalid state
names/references, missing required endpoint geometry, impossible dimensions, or non-finite values.
Invalid import is transactional: keep the current draft and report actionable errors by feature.

Warnings/unresolved facts: endpoint not on accessible ground, unresolved floor/height, ambiguous
overlapping floors, trigger with no target, uncertain initial state/timing, zero-length route
without distinct verified floors, motion not fully described, stale image/floor bindings, feature
footprint leaks across a passage, and mismatch with known map feature counts. Same-position rope
endpoints on distinct floors are legitimate and must not be rejected as a zero-length horizontal
route. Unknown replay signals do not prevent the user saving their map annotations.
Also validate guards/timer references, cost units, bounded-sight references, base-edit ownership,
stale floor bindings, and unsupported intermediate-access/in-transit policies.

Allow "Save draft" even when incomplete. "Export reviewed annotations" requires structurally valid
data and makes unresolved geometry explicit; do not imply runtime readiness. An engine compiler
only enables definitions with validated geometry and supported behavior. A map summary separately
reports user annotation completeness, geometry readiness, and replay-signal readiness.

### Saving

Autosave every feature edit with a visible saved/unsaved/error indicator. Catch local-storage
quota/permission failures and offer download; never silently imply persistence. Persist features,
triggers, routes, checklist edits, and permanent paints together. Undo/redo includes links and
property changes, not just strokes. Preserve edits while switching maps/floors and changing tools.

Import/export the whole catalogue; untouched maps and unknown fields remain unchanged. Show a
review of affected maps/features when replacing draft content. Handle incompatible schema versions
explicitly and scope saved drafts to their source catalogue so an old local draft cannot silently
overwrite newer annotations. Plain JSON remains the portable backup.

Validated imports transactionally replace the **canonical catalogue**, or use an explicit merge
with a shown conflict policy. Editable projections reference that canonical object; export must
not clone the original page's `DATA.tags` after a different file was imported. Autosave includes
the complete canonical catalogue, including unknown top-level/map/feature/nested fields and maps
not embedded in the current page. Non-renderable maps remain preserved/exportable. Incompatible
versions are reported without replacement; keep the current draft and offer a copy of the file.

Draft provenance records source/imported catalogue digest, map-image digests, floor-asset digests,
schema version, snapshot revision and checksum. Keep a recoverable previous valid snapshot; write
the new snapshot completely before advancing the active pointer. Corrupt or failed migrations do
not overwrite the last good copy. Maintain a draft index with recovery/download for older sources.
Changed catalogue/images/floors prompt explicit restore/revalidation rather than hide older work
or silently apply it to new geometry. Rebinding/migration never guesses a floor or loses unknown
fields. Cover interrupted saves, imported sources and failed migrations as well as quota failures.

## Implementation milestones

### M0 — Consumer-contract prototype before schema freeze

Files: provisional `app/replays/map_feature_schema.py` and `map_feature_state.py` (stdlib-only),
`app/control/features.py`, `control_tagger_core.js`, and focused synthetic contract tests.

Before building annotation forms, prototype the reducer, floor/ground binding, bounded occluders,
directed route arcs/costs, atomic activation bundles and consumed-input manifest. Exercise existing
geometry/topology consumers through explicit adapters on synthetic fixtures; identify required
caller changes. Production dynamic execution and replay decoding stay disabled. Share compiled
data and reducer semantics with subsequent milestones instead of discarding this prototype for
a separate preview engine. Compare JavaScript/Python only where both implement the same contract.

Required fixtures and acceptance:

- Compound door destroyed mid-motion; stale completion and switch press cannot restore it.
  Reset with pending transitions clears their epoch; repeated/mid-motion actions follow guards.
- Two proximity occupants: first entry opens, one departure does not close, last exit follows
  the declared close policy, and re-entry invalidates a superseded close.
- Stacked-floor door: lower walking is blocked while upper walking remains open; sight above,
  below and between floors respects actual bounds in `cast`, `los` and `seen_from`.
- Baked-in breakable over transparent pixels and legacy paint: ground/sight/floor restoration
  compiles together; permanent walls and another overlapping feature remain intact. Pending
  consumer or stale floor bindings leave all base edits unpublished.
- Same-position rope: verified lower-to-upper arcs without same-floor shortcuts; directed
  transport costs agree across graph consumers. Endpoint-only zipline versus intermediate-access
  route remains distinct, including one-way return-path and temporal-availability cases.
- Definition/compiled-asset mismatch is detected; enabled-set/semantics changes alter the manifest;
  editorial-only changes do not. Legacy/no-feature inputs remain byte/fingerprint-compatible.

Exit gate: document exact schema fields, reducer ordering/cancellation, height references,
traversal metric policies for every consumer and unresolved/runtime-disabled representation.
Freeze only when these contracts can represent and consume the fixtures. No whole-map annotation
request or production asset publication at this milestone.

### M1 — Schema and pure model

Files: `webapp/app/replays/map_feature_schema.py` and `map_feature_state.py` (stdlib-only),
`webapp/scripts/control_tagger_core.js`, new schema/model tests under `webapp/tests/replays/`.

Formalize the M0-tested schema, define preset defaults with unknowns explicit, and add pure operations for
feature/trigger/route creation, linking, states, validation, import/export, and draft snapshots.
Keep old `tags.json` unchanged unless features are added. Preserve unknown fields.

Acceptance: legacy and new schemas round-trip; multiple objects/links retain identity; deletion
and undo restore references; malformed imports leave current edits intact. Fixture includes a
closeable AND breakable door, rotating door, teleporter, zipline, and same-u/v vertical rope.
Test canonical import -> edit -> autosave -> reload -> export with unknown fields at every level
and imported maps absent from the page. Duplicate IDs remap internal references; delete/relink
covers every reference type. Incompatible imports and failed migrations preserve the current draft.

### M2 — Map editor interaction and persistence

Files: `control_tagger.template.html`, `control_tagger_core.js`, `test_control_tagger.py`.

Add feature palette/list, properties panel, zoom/pan, points/lines/polygons, per-object brush masks,
drag/edit, link picking, direction arrows, undo/redo, draft autosave, and import/export review.
Retain the existing permanent-paint tools and support keyboard access and legible selected states.

Acceptance: the user can annotate multiple nearby objects without mixing their paint, link both
a switch and a shootable trigger, and add a zipline/rope without writing JSON. Saving/reloading,
changing maps, undoing a deletion, and importing a replacement do not lose other maps' work.
Cover old-draft recovery after source/image/floor changes, corrupt active snapshots with valid
previous copies, interrupted saves, imported catalogue provenance and quota/permission failures.

### M3 — Floors and traversal routes

Files: `control_tagger.py`, its template/core, `app/replays/map_feature_schema.py`, relevant tests.

Embed floor information and provide landing/floor selection, unresolved markers, route properties,
and the three base-reconciliation tools (ground, sight removal, floor binding). Audit all seven
rope maps through their checklist;
annotations need not wait for local replay coverage on every map.

Acceptance: a same-position lower-to-upper rope remains two distinct landings; a zipline missing
from the minimap can be drawn; unresolved floor bindings remain unresolved after export/import;
no route silently snaps to a different floor or opens an intervening horizontal corridor.
Explicitly author directional entry/transit costs and endpoint-only/intermediate access. Unknown
bounds/costs/floors remain saveable, while compilation/readiness explains exactly what is pending.

### M4 — State/motion previews and map checklist

Files: tagger template/core and schema/reducer tests; `webapp/app/control/features.py` for the
M0-proven deterministic geometry contract, with parity tests beyond mask composition.

Add state selection, trigger simulation, reset-round, rotation poses/slider, composed blocker
overlays, and catalogue completion. Compile new feature geometry separately from permanent walls.
Support reviewed base reconciliation without clearing unrelated geometry or publishing pending bundles.

Acceptance: intact/broken, open/closed/broken, repeated rotation, multiple independent features,
and round reset preview correctly. JavaScript masks agree with Python compilation on synthetic
fixtures. All existing tagger mask-parity tests still pass. Permanent kill-line checks do not
mislabel manually closed doors as bad permanent cover.
Guarded state reduction, compiled floor nodes, directed arcs and bounded occluders also agree
with the shared consumer contract. Numerical route/sight probes remain unavailable unless they
invoke that shared consumer; drawing-only overlays must not claim full control-engine parity.

### M5 — Control-consumption contract and freshness

Files: `app/control/features.py`, `geometry.py`, `topology.py`,
`scripts/build_control_geometry.py`, `app/services/replay_control.py`,
`app/services/replay_control_remote.py`, `app/services/replay_control_store.py`,
`app/control/task.py`, `scripts/compute_control.py`, and relevant worker/task input transport.

Publish compiled assets as immutable generations and transport/verify the expected-versus-consumed
manifest on both local and remote tasks/storage. Refresh service caches and key worker caches by
generation. Keep stable output cell indices across feature states. The M0-proven per-tick
walk/sight/route contract is available for later engine integration, but unsupported features and
their base-opening edits remain disabled together. Do not activate dynamic behavior at this step.

Acceptance: runtime feature edits change the affected inputs, while editorial edits do not. Stale
local and remote assets, definition/compiled disagreement and stale cache generations refuse
computation or storage. Editing inputs during a task rejects old results even if their own manifest
is internally valid. Interrupted publication leaves the last complete generation usable. Verify
actual consumed node/route/occluder hashes, not just an echoed expected manifest. No-feature maps
retain current fingerprints. User-reviewed unsupported bundles remain visibly pending/disabled.

### M6 — Visual QA and first user annotation pass

Build a temporary page and exercise it through the browser. Verify screenshots at normal and
zoomed sizes, feature selection, state/floor switches, route arrows, motion, import/export,
storage failure, and undo/redo. Reopen an exported catalogue and compare it with the original edits.
Include canonical imported unknown fields, recovered previous drafts after source/asset changes,
compound-object deletion/duplication, bounded-sight fields, unresolved policies and pending base edits.

Start with one Summit door and one rope, then a toy switch-operated breakable door and a zipline.
Use a preview catalogue for QA rather than overwrite committed production annotations. Give the
user the runnable tool and short in-page instructions for painting the remaining maps.

Acceptance: the user can provide locations, geometry, behavior and uncertainty for every listed
mechanic; the tool does not require replay identifiers or manual JSON edits. Show the catalogue
summary and examples before declaring the tool ready for map annotation.

## Checks and boundaries

- Run focused schema/reducer/model/tagger tests and compiled-consumer parity checks after milestones.
- Preserve existing static tagger exports/masks and import isolation. Add tests for meaningful
  persistence/reference failures, floor ambiguity, and geometry composition rather than UI styling.
- Keep this work on the existing `codex/map-gimmicks` worktree; reconcile current upstream and any
  timing-gap work before engine integration. No new branch/worktree is required just for this plan.
- Do not alter Impact scoring, write production data, deploy, or run a full control recompute as
  part of building the annotation tool.
- Replay-signal investigation and time-aware Unknown/knowledge integration follow the gimmick
  design. The tool makes those definitions possible; it does not replace that work.
- Later replay extraction must update the streaming event filter as well as extras collection;
  streaming/full-loader signal parity is a release gate, not only a decoder unit test.
- Once engine integration exists, validate Summit rounds 1–2 before accepting broader recomputation.

## Definition of done

The deliverable is an extended local tagger, its versioned export contract, validation/compiler
support, focused tests, browser-verified workflow, and concise instructions. It covers switches,
shootable/proximity triggers, independently breakable objects, compound door behavior, rotating
panels, teleporters, ziplines, and vertical ropes. Every map can record reviewed annotations or
explicitly unresolved facts. All collected information is usable by later replay/control work
without requiring the user to repaint it or hand-edit engine configuration.

## Review finding traceability

| Pasted finding | Resolution / implementation gate |
| --- | --- |
| 1: Guarded behavior and cancellation | Behavior contract, M0 event sequences, M1 shared reducer |
| 2: Sight bounds distinct from movement floors | Bounded sight contract, M0 stacked-floor tests, M3 authoring |
| 3: Baked-in base geometry and pending edits | Three reconciliation tools, ownership, atomic bundles, M0/M3/M5 gates |
| 4: Directed traversal/access/cost semantics | Directed arc contract and consumer policy map before M0 exit |
| 5: Actual consumed freshness, both execution paths | Versioned manifest, content-addressed publication, M0/M5 checks |
| 6: Canonical import and reference preservation | Canonical catalogue replacement/merge, M1/M2 round-trip tests |
| 7: Provenance and draft recovery | Snapshot provenance/previous copy/recovery index, M2 failure tests |
| 8: Contracts before UI/schema freeze | New M0 before M1; conditional shared-consumer probes |

These are engineering contract requirements. User input is needed only for mechanics that cannot
be established from evidence, or to approve an approximation of unresolved geometry/timing.
Hearing remains unresolved and is not inferred from geometry/noise annotations.
