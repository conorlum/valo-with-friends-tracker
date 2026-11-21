# Per-round map gimmicks

Design draft, 2026-10-03; expanded with the user's map examples on 2026-10-04. The user selected
**both movement and sight** for Summit's closed doors. The model must cover reversible, breakable,
moving, and proximity-triggered features. Hearing semantics remain a separate question; this
document does not authorize a full-corpus recompute.
Revised after static review: schema freeze follows a consumer-contract prototype, as specified in
`docs/superpowers/plans/2026-10-04-map-interaction-tagger.md` (M0).

## Scope

Start with Summit's three drop-doors. Give later maps the same event/state interface, but investigate
each map's mechanics and replay signals separately. Static connections (teleporters, ropes, drops)
remain geometry specials; a stateful feature can eventually enable/disable one of those connections.
Do not assume every map feature is a closing door.

## Mechanics the model must express

These cases were supplied by the user on 2026-10-04. They are requirements for the model, not
claims that their replay signals, timings, or geometry have already been verified.

| Map | User-identified feature | Investigation requirement |
| --- | --- | --- |
| Abyss | Breakable blocks; vertical ropes | Intact blocks movement and sight; broken removes the obstruction; intact again next round. Audit rope traversal against the height topology. |
| Ascent | Switch-operated, breakable doors | Opening/closing and destruction are separate transitions; broken means the obstruction is gone for that round. Verify allowable switch transitions individually. |
| Bind | Teleporters that move the player and make a loud noise | Identify entry/exit routes and replay evidence for transit; traversal links must not create sight through the teleporter or ground along a straight line between entry and exit. Noise is a separate cue. |
| Breeze | Noisy proximity-operated doors; vertical ropes | Verify each door's exact opening/closing rules and replay evidence. Audit rope traversal against the height topology. |
| Corrode | None identified by the user | No feature-specific investigation currently requested. |
| Fracture | One noisy proximity-operated door; vertical ropes | User corrected the count to **one door**. Verify that door's opening/closing rules and replay evidence. Audit rope traversal against the height topology. |
| Haven | None identified by the user | No feature-specific investigation currently requested. |
| Icebox | Zipline, possibly absent from the minimap; vertical ropes | Investigate zipline endpoints/traversal independently of the minimap. Vertical ropes require a topology audit before deciding whether extra handling is needed. |
| Lotus | Repeatedly activated rotating doors; breakable door; vertical ropes | Moving panels change geometry through their motion. The breakable door has intact/broken states and round reset, independently of rotating-door activations. Audit rope traversal against the height topology. |
| Pearl | None identified by the user | No feature-specific investigation currently requested. |
| Split | Vertical ropes only | Audit whether existing floor connections already represent rope travel; extra handling remains undecided. |
| Summit | Three drop-doors; vertical ropes | Open passage becomes blocked during a drop and stays closed that round. First live validation case. Audit rope traversal against the height topology. |
| Sunset | Closeable, breakable door like Ascent | Investigate switch/closure and terminal destruction separately, with per-round initial state/reset. |

This is the current user-supplied investigation list, not an independently verified exhaustive
catalogue. In particular, "none identified" does not assert that the map has no such features.

### Traversal features and vertical ropes

Map gimmicks include static traversal connections as well as stateful obstructions. Bind's
teleporters and Icebox's zipline need explicit route geometry wherever the minimap and ordinary
walking graph omit the connection. A teleporter connects its entry to its exit; it does not make
the intervening map cells walkable or visible. Transit direction, cost, availability, and event
evidence must be investigated per feature rather than inherited blindly from a door.

The user identified vertical ropes on **Abyss, Breeze, Fracture, Icebox, Lotus, Split, and Summit**;
their need for special handling remains undecided. Audit all seven maps: compare actual rope
endpoints/floors and replay movement with the existing height topology. Add explicit vertical
traversal only where the existing graph
does not represent that route correctly. Do not paint a rope as a sight wall or flatten a vertical
route into unrestricted horizontal walking. This audit may identify the same gap on other maps;
it does not imply that every rope requires a new replay state decoder.

Use feature-specific named states and, when needed, motion phases. `open`, `closed`, `broken`, and
`rotating` are examples, not an exhaustive engine enum. A rotating panel can obstruct one route
while opening another; its geometry describes the panel's position as well as its passage.
Activation evidence and effective geometry time must remain distinct: a button press or an effect
start is not automatically the instant that walking or sight becomes possible/impossible.

Each feature defines its round-start state and allowable transitions. Destruction is terminal
within that round for these breakable features; it cannot be reversed by a later switch or animation
Stop. Repeated legitimate activations are retained; only duplicate evidence for the same activation
is deduplicated. Round resets reconstruct the initial state independently.

Transitions include source state, action/event, guard, destination, movement/sight timing,
automatic follow-up and cancellation policy. Proximity occupancy distinguishes first entry and
last departure; mid-motion presses have explicit verified or unresolved policies. Completion
events carry transition-generation and round-epoch tokens: destruction cancels obsolete motion,
and a new round rejects prior timers. A pure reducer contract drives sandbox and later runtime
interpretation, with shared fixtures for Python/JavaScript implementations. Observed replay
states remain evidence; missing occupancy or guard information is never inferred from a button
event alone.

## Proposed interface

1. A stdlib-only replay module owns per-map signal decoders. Identify Summit door actors by the
   `DescentBox_v5_`, `DescentBox_v9_`, and `DescentBox_v12_` actor-path prefixes. Resolve GUIDs only
   within the current export. Numeric effect/container/object GUIDs are not stable identifiers.
2. Each round gains a `map_features` field containing observed feature identities, their initial
   states, and ordered transitions, for example `{"id": "summit_door_mid", "t": 12.5,
   "state": "closed"}`. A transition may also carry a verified motion interval or phase timeline.
   Preserve activation time separately from effective walk/sight changes where they differ.
   Times use the existing InRound-relative clock. A missing field means an
   old condenser, not evidence that every door stayed open; explicit empty transitions on a
   supported, observed feature means no change was recorded. Report absent/unsupported evidence.
3. Per-map definitions in `tags.json` bind feature IDs/states to traversal-blocking cells/nodes
   and bounded sight-blocking geometry. Geometry is independent of replay actor identity. Floor
   restrictions must be explicit where a feature covers only one floor; never silently block
   every level of an overlapping cell. Validate definitions and include them in control freshness.
4. The engine derives one immutable feature state per tick and uses it everywhere: Unknown,
   Safe, backfill, presence, walking distances/way back, knowledge-view possible regions, live
   vision/watchers, and both incremental and full counterfactuals. Base geometry and output cell
   indices remain fixed for the round, covering the union of potentially available ground across
   all feature states. Blocked cells contribute no held area or coverage. Transition ticks include
   effective state changes and geometry-changing motion phases, not merely button/effect starts.

The replay field's exact envelope should be settled alongside the decoder. The map-annotation
schema first needs the M0 reducer/compiler/consumer prototype before freeze. Keep format v1 readable
for older clients; bump the condenser recipe and control revision for the new computations.

## Movement and memory

A closed door forbids crossing its passage. It does not clear Unknown already beyond it: an enemy
could have crossed before closure. Keep arrival-time history in each still-open disconnected region.
Clear only the door footprint itself. Opening a passage later must restart eligibility at that
transition time, so old arrival times cannot spread retroactively through a previously closed door.
Knowledge-view possible regions must also respect the history of when passages were traversable:
reflooding from an old last-seen point through only the current open mask would grant an enemy
travel time during which a door was actually closed. A repeated open/close cycle needs the same
temporal handling as Unknown; it is not enough to replace the mask at the current tick.
Reject diagonal and special-link bypasses of the footprint. Counterfactuals use the same feature
state as the real tick; removing a player never opens a door.

Movement and sight effects are independent. Do not represent a physical door solely as an ability
wall: Viper's wall blocks sight but is intentionally walkable, and smoke pinch sealing has different
semantics. If a closed door forms a new wall face for pinch detection, its state must participate in
that geometry/cache key as well.

Traversal routes use explicitly bound directed node arcs, direction-specific entry/transit costs
in seconds, optional physical length in metres, availability, and endpoint-only or specified
intermediate access. No all-floor endpoint cross-product or one-step default cost. Boolean
reachability, walking metrics, time-distance/backtracking, Unknown and knowledge use the same
compiled graph with explicit metric policies; temporal consumers respect availability history.
Same-position ropes join the identified floors without inventing a horizontal shortcut.

## Sight

Temporary `Wall` support provides an existing XY-intersection mechanism, but its current geometry
is vertically unbounded. Floor-limited doors need bounded occluders with XY geometry, bottom/top
height, units/reference and uncertainty. Movement-floor selection alone does not define sight
bounds. Floor assets store standing-player position-z; physical ground is `position_z - STAND_M`.
Resolve world-z or verified ground-relative bounds to the engine's map-relative metres.

`cast`, `los`, and `seen_from` must share vertical-intersection semantics. Casting checks each
target node's slope through the obstruction instead of stopping every height at an XY hit.
Direct sight tests the actual eye-to-target line; cached visibility filtering tests source/target
pairs with explicit height offsets. A line above/below a lower-floor door remains possible where
the permanent geometry permits it. Unknown bounds remain saved/pending, not an implicit infinite
wall; verified all-height features can declare that mode explicitly. Include same-XY vertical
sight tests. One unblocked source can still see a target hidden from another. Keep visibility for the
maximally unobstructed map, with removable blocks absent and moving door panels excluded from the
permanent walls. Initial intact/closed states then add their blockers. Breaking a block removes
its override and reveals the already-cached sight, avoiding a rebuild. The cached walk-node domain
likewise includes that feature's potentially open footprint. Existing minimap masks must be audited
for removable features; simply clearing an override cannot restore sight or walking baked out of
the base assets. Permanent walls stay permanent.

Author potential ground, removal of feature-owned baked-in sight, and restored-ground floor
bindings separately. Reconcile exact legacy cover/can't-walk portions explicitly while retaining
genuine permanent walls and other features. New ground must not inherit the unresolved-height
fallback that joins every neighboring floor. Stale height bindings remain pending. Base edits,
floor bindings, initial obstruction, behavior and runtime support enable atomically as a bundle;
pending bundles publish no opening edits. Interdependent overlapping edits share one bundle.

Finite stable states can use prevalidated geometry; moving doors need a map-specific motion
description or verified phase sequence. Do not assume the same animation or timing across maps.

## Runtime input integrity

Use a versioned consumed-input manifest for definitions, compiled geometry/routes/occluders,
floor bindings, enabled bundles and compiler/consumer semantics. Local and remote tasks verify
the actually loaded bytes against expected inputs before computation, then storage compares
returned consumed inputs with planned and current inputs. An edit during computation cannot
store stale results under a new fingerprint. Publish verified immutable asset generations
atomically and key geometry/topology caches by input digest rather than map name alone.
Editorial metadata does not change runtime hashes. The annotation-tool build preserves absent
feature keys and legacy fingerprints on maps without enabled features; later engine rule/revision
releases follow the existing control versioning policy.

## Noise cues

Record geometry transitions separately from any verified audible activation cue. The current
knowledge-view design explicitly excludes sound (`docs/map-control-team-knew-plan.md`, rule 3).
Whether to preserve cues only or extend hearing now is pending the user's choice. Do not infer
that all players hear an activation or assign it to a specific enemy merely because it happened:
hearing range, timing, occlusion, side information, and what the cue actually proves need their own
rules. An observed proximity opening can be evidence without supplying an exact player position.

## Summit evidence and unresolved questions

Re-exported the local Summit replay with the existing pinned parser on 2026-10-03:
`2db387cd-f254-4f42-bb56-319c08ababe0.vrf`.

Confirmed round-1 effect pairs on `EffectManagerComponent`:

| Actor prefix | Play (replay s) | Matching Stop (replay s) | Duration |
| --- | ---: | ---: | ---: |
| DescentBox_v12_ | 63.109 | 64.859 | 1.750 s |
| DescentBox_v9_ | 79.496 | 81.246 | 1.750 s |

Both actors' spawn locations are null. Effect translations are zero, so neither provides the
door's map position. The effect's Stop ends an animation; it does **not** reopen the door.
Door damage notifications are not the drop trigger.

Before tagging production geometry, verify:

- Which actor is mid, and which are the other two doors.
- Each passage's exact wall-to-wall footprint and sight segment on the minimap.
- When the falling door first prevents passage/sight: Play, Stop, or a point during the animation.
  Preserve Play/Stop evidence in the extractor rather than hardcoding an unverified 1.75 s delay.
- Round resets and how missing starts/stops should be reported. Reset each round independently;
  do not carry a closed state into the next round.

## Implementation and validation sequence

1. Verify identities, geometry, and closure timing; write small sanitized fixtures without player
   identities or replay exports. Keep the multi-gigabyte export in temporary storage.
   For the annotation tool, first run plan M0 on synthetic compound-door, stacked-floor and
   directed-transport fixtures before freezing the schema or building annotation forms.
2. Add decoder tests for changed GUIDs, unrelated effects, duplicate signals, paired stops,
   reset, out-of-round events, explicit empty evidence, and streaming/full-loader parity.
3. Add toy engine tests: open-before/blocked-after; no diagonal bypass; independent sight/walk;
   Unknown already beyond the door persists; reopened paths do not backdate arrivals; multiple
   doors compose; per-floor restrictions; round reset; knowledge and counterfactual parity.
   For the shared model, include initially intact -> broken, terminal destruction, repeated
   open/close cycles, and moving panels changing more than one passage. Map-specific live
   validation remains staged: Summit first, then one investigated mechanic at a time.
   Include destruction during motion, obsolete completion, multiple proximity occupants,
   pending reset, bounded cross-floor sight, owned base restoration, and directed traversal costs.
4. Wire geometry validation/freshness and recipe/revision updates; preserve no-feature reference
   outputs and the upload worker's stdlib-only imports.
5. Condense and compute **Summit round 1 only**, with frames before/during/after the mid-door drop
   and full-vs-incremental checks at relevant ticks. Add round 2 only to verify reset if needed.
6. Present the small-round preview. A broader recompute follows only after the user accepts it.

No Impact scoring change, database write, deployment, or full-corpus recompute is part of this
design draft. Check fresh origin/main and any timing-gap work before engine edits; this worktree
was created from fetched origin/main on branch `codex/map-gimmicks`.
