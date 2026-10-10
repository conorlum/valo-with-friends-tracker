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

The catalogue is a checklist to verify with the owner, not proof that every instance has been found.
Haven's empty checklist needs an explicit review; it must not silently certify no features.
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
| Split | Confirm all ropes, mark bottom/top/access and floors; inspect upward/downward traversal | Qualify traversal representation and graph/visibility integration |
| Haven | Review whether any interactive features need annotation | Preserve baseline behavior when review confirms none |
| Abyss | Catalogue lists breakable blocks and ropes; confirm instances and review one break/traversal | Bind reset/destruction and reuse qualified breakable/traversal family |
| Summit | Catalogue lists three drop-doors and ropes; confirm instances, trigger method and drop/reset behavior | Decode map-specific shootable actors; verify motion and terminal/round reset semantics |
| Lotus | Catalogue lists rotating doors, a breakable door and ropes; confirm all instances and operating cycles | Add rotation/return consumer and map-specific breakable bindings; reuse traversal family |

Existing exports contain candidate WindowShield/Switch classes on Sunset, RespawningWallPlate classes on
Abyss and Lotus, and RespawningPlummetShootable classes on Summit. These paths are discovery evidence,
not reviewed feature bindings. The current parser raw allowlist does not capture the Plummet shootables
or arbitrary rotating-door/traversal actors; extend bounded capture only after identifying those actors.

## Guided sequence

1. Preserve original recordings and every owner export. Generate a private seven-map tagger from the
   newest combined draft and retain earlier pages. Merge map-local edits by identity; never replace an
   Ascent draft with the empty Ascent section in a Sunset-only starter export.
2. Start with Split ropes, then check Haven, review Abyss, Summit and Lotus, and finish remaining
   Ascent/Sunset integration. Collect feature counts/names before certifying a map complete. This order
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
