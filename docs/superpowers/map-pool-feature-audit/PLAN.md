# Local map-feature audit and combined release

Owner scope: Split, Ascent, Sunset, Haven, Summit, Abyss and Lotus. Continue on
`codex/ascent-audit-and-tagger-guidance`, retain a single cumulative review, and publish the resolved
changes together after owner approval. Never merge, change main, or deploy as part of this audit.
Raw recordings, identities, private exports and calibrated coordinates remain outside the public repo.

## Design

Keep the existing per-map annotation and immutable artifact model. A replay records source events for
each supported authored feature; control and gaps consume the same verified artifact and sampled state.
Adding catalogue entries or drawing an outline does not enable a feature. Each feature needs an explicit
runtime consumer with qualifications, source coverage, geometry checks and versioned identity.

Use three behavior families:

- Moving panels: observed start/end/reset/destruction, bounded geometry and motion sampling. Descending
  doors can use the owner's conservative 30% movement cutoff; continuous sight still requires height bounds.
  Rotating doors need their own pivot/panel/travel and return behavior, rather than the descending sampler.
- Breakables: observed intact reset and destruction, with independent movement and sight effects. Their
  removed opening must be reconciled against permanent map paint so a broken object does not leave a wall.
- Traversal: distinct access/landing endpoints and floors, directions, entry/transit costs and availability.
  A vertical rope may have coincident minimap points on different floors. Do not collapse the endpoints,
  infer rope use from ordinary walking, or add permanent graph shortcuts without a supported consumer.

### Quiet rope routes and unknown propagation

Owner policy: an unknown enemy changes between a rope's landings through the rope route, in either
direction, paying its entry and transit time, unless the owner separately reviews a quiet alternative
or a directional restriction. A downward fall or an upward movement ability must not automatically
provide a free or silent alternative to that route. This matches the unknown model's purpose of
tracking where an enemy could reach while playing quietly; it does not depend on inventing a sound
radius or assuming that a particular teammate would hear an action.

Qualify the quiet traversal time separately from an observed climb of unspecified speed. Keep the
owner-entered costs as candidate inputs until reviewed; a movement-based cue or a fast observed climb
alone does not certify a quiet cost. Actual observed movement and supported ability/sound knowledge
events retain their own evidence handling and must not be forced onto the hypothetical quiet route.

Record equipped weapon, jump-assisted entry, pauses and sound uncertainty alongside each reviewed
reference. Do not infer a weapon speed multiplier from clips with different entry methods or inputs.
A provisionally accepted example retains its owner-reported confidence and equipment limits; it is
not silently promoted to a universal measured cost. The owner explicitly deferred the reported silent
fall-and-catch technique: omit that shortcut for this initial model and document the approximation.

The traversal consumer must bind distinct landing nodes even when their minimap coordinates coincide,
add timed directed rope arcs, and prevent unreviewed walk/drop edges from bypassing those arcs between
the landings. Apply this in unknown propagation, counterfactual reachability and gap arrival/path
attribution. Retain genuine alternate walking routes around the map. Reject ambiguous floor bindings
instead of joining all floors or enabling the rule on a flat preview.

Acceptance cases for routes requiring quiet rope use: upward and downward arrivals respect the selected rope cost; a high fall cannot
shorten the downward quiet arrival; coincident coordinates do not create a zero-time cross-floor link;
ability use creates no generic quiet graph edge; and a real alternate walking route remains usable.
Compare fresh control/gap computation with cached output once the consumer and versioned artifacts
are built. These are required runtime checks, not claims that traversal integration already exists.

The catalogue is a checklist to verify with the owner, not proof that every instance has been found.
The owner reviewed Haven on 2026-10-10 and confirmed no interactive gimmicks; its three bomb sites
remain baseline map structure. Preserve this explicit review alongside the empty feature catalogue.
Unknown geometry, source semantics and mid-motion policies stay visible and gated. An approximate
computation policy is labelled as such, never promoted to a measured physical fact.

## Working inventory

Local recordings are available for all seven maps: Split 3, Ascent 3, Sunset 3, Haven 1, Summit 2,
Abyss 1 and Lotus 2 (inventory taken 2026-10-09). File creation times are local recording timestamps,
not verified match start times. Full source details live in the private inventory.

| Map | Authoring/evidence work | Runtime work |
| --- | --- | --- |
| Ascent | Existing doors/glass; reviewed source scenes; finish height/opening calibration and glass footprint | Closing/closed/break/reset consumer built; apply selected cutoff; opening remains unsupported |
| Sunset | Door/switch drawn; owner reviewed close/reopen and two destruction cases; resolve bounds and interaction placement | Bind and decode Sunset source explicitly, support qualified reopening, add site display and control/gap integration |
| Split | All four Vent ropes share an approved quiet reference. Owner selected the shared model cost for Sewers and Heaven and closed their timing review; retain noisy/rejected scenes as evidence. Resolve landing floors and in-transit policy | Qualify traversal representation and graph/visibility integration |
| Haven | Owner reviewed: no interactive gimmicks; no feature drawings required | Preserve baseline behavior |
| Abyss | Owner resolved requested behavior/timing decisions: both breakables matched, B/Spawn estimates selected, gun-out Heaven reference accepted, and Mid quiet travel restricted to one named direction with a selected rope-only cost. Approach/departure use normal unknown speed; physical floors still need binding | Qualify reset/destruction framing; support the reviewed quiet directional restriction separately from actual noisy traversal; reconcile geometry and reuse qualified families |
| Summit | Owner completed authoring/behavior review for three drop-doors and one rope; retain selected approximate motion and quiet traversal policies. Resolve height, landing floors and in-transit handling | Qualify map-specific shootable events/reset coverage and phase-aware control/gaps consumption; retain closed-round persistence and separate lethal-hazard qualification |
| Lotus | Owner matched both rotating-door activation scenes and breakable destruction, authored two switches per door, and reviewed the shared cycle with blocked entry/exit bands and partial moving-panel occlusion. Either side activates the same door; numbered-side guesses are not required for state. Preserve outlines while resolving panel/pivot fields and confirm instance coverage | Qualify bounded switch capture and door-level event identity across switches; add full-cycle rotation/return consumer using one activation clock across passability stages and map-specific breakable bindings; reconcile permanent geometry and heights |

At the inventory stage, existing exports contained candidate WindowShield/Switch classes on Sunset, RespawningWallPlate classes on
Abyss and Lotus, and RespawningPlummetShootable classes on Summit. These paths are discovery evidence,
not reviewed feature bindings. The current parser raw allowlist does not capture the Plummet shootables
or arbitrary rotating-door/traversal actors. The implementation update below supersedes that capture status.

## Guided sequence

1. Preserve original recordings and every owner export. Generate a private seven-map tagger from the
   newest combined draft and retain earlier pages. Merge map-local edits by identity; never replace an
   Ascent draft with the empty Ascent section in a Sunset-only starter export.
2. Start with Split ropes, then check Haven, review Summit, Abyss and Lotus, and finish remaining
   Ascent/Sunset integration. The owner selected Summit after completing Haven's review. Collect feature counts/names before certifying a map complete. This order
   establishes reusable traversal and breakable families before the more complex rotation model.
3. For each map, first draw instances and relevant access points. Agent inspection identifies candidate
   replay rounds/times, then the owner checks specific scenes. Use countdown readings and elapsed times
   with their clock basis explicit; a postplant timer is not the preplant countdown.
4. Implement one behavior family at a time. Add explicit per-map bindings and class/field framing checks,
   require each round's observed reset, and keep malformed/ambiguous/missing evidence pending. Confirm
   behavior in another available recording where possible; distinguish synthetic, structural and human
   scene checks. With only one recording, document the held-out evidence limitation.
5. Build/obtain the exact height context locally, diagnose placement and permanent-domain corrections,
   and qualify only complete artifacts. A wall-mounted switch marker and a walkable interaction location
   are different concepts; retain the original drawing when resolving access placement.
6. Produce a site-code preview per map and an evidence receipt identifying included features. Check
   movement, active/passive sight, unknown propagation, destruction/opening release and gap attribution.
   Compare full control with cached and fresh gaps-only computation on representative real rounds.
7. Freeze the combined map annotations, runtime versions and evidence matrix for review. Keep unresolved
   instances explicitly disabled rather than hiding gaps behind a map-level "complete" status.

## Owner release boundary

Prepare an additive candidate and draft PR with the final scope, tests, unresolved decisions and
rollout/recompute instructions. No new schema is currently planned. Any schema requirement must be
reviewed separately. The owner controls publication of tags, merging and deployment on both sites.
Reparse recordings after deploying compatible parser/web/worker code, then recompute control and gaps
against the reviewed artifact generation. Retain previous artifacts and height inputs for rollback.
Linux worker-image and live deployment checks remain release gates, not conclusions from local previews.

## Implementation update: 2026-10-10

The branch now implements explicit map-pool source binding, reset-aware per-round ledgers, panel
state/motion effects, timed quiet rope directions, measured landing bindings, local quiet connector
cuts, and computation-derived site status. The parser patch extends bounded capture to the selected
Lotus and Summit families. The tagger can record measured landing Z and quiet estimates separately
from physical route directions. These additions implement runtime foundations; they do not activate
the current owner candidate or certify the working inventory as live-ready.

Fresh captures and a read-only live height inventory exposed remaining source, panel, pivot, metre
bound and landing problems. Several maps have no accepted active height asset. See [RELEASE.md](RELEASE.md)
for current evidence, exact blockers, the owner-guided order, real control/gaps qualification and the
owner-controlled rollout. Private report receipts and staged candidates retain the original combined
owner export unchanged. Main, publication, deployment and live recompute remain untouched.
