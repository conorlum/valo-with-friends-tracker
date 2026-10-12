# Enabling more features with existing replay evidence

This checkpoint supersedes the original eleven-feature selection in FIRST-PASS.md. It keeps
the owner's approximate geometry, timing and quiet-travel decisions. It does not require a
complete 3D map, change main, activate rejected heights or publish private owner annotations.

## Revised candidate

| Map | Enabled features in the private candidate | Deferred |
| --- | --- | --- |
| Ascent | Both descending doors and Heaven glass | None |
| Sunset | Descending/reopening door | None |
| Split | All six ropes | None |
| Haven | Existing terrain/control baseline | No tagged gimmicks |
| Summit | Cave, Mid and Heaven drop doors; Heaven rope | None |
| Abyss | Both breakables, independently qualified on the existing flat fallback | Four height-dependent ropes |
| Lotus | Both rotating doors and breakable | None |

Nineteen features are selected; four remain explicitly disabled. Actual live compilation still
uses the selected height generation and fresh source timelines. The private selection is a
publication proposal, not live activation.

## Changes

An explicitly source-bound door or breakable whose blocking states specify `all_height` can
use existing walkable 2D nodes where sampled ground is absent. Ground is not invented. Solid
walls do not gain walking nodes; multiple measured floors, finite sight bands, sliding and
rotating panels retain their existing placement requirements. This removes Summit Heaven's
dependency on samples in every small footprint cell while preserving its source-bound state
and shared one-second descent / half-second blocking slam.

Rope landing proposals use actual grounded samples close to boarding/departure, rather than
the mean XY of a long stationary-height run. Samples must agree in recording, round and player
slot and resolve to distinct physical nodes in an exact height asset. Native vertical rides
corroborate the connection; ride extrema never supply a ground height, and the owner's quiet
times are not inferred from speed or equipment. All six Split ropes and Summit Heaven now
have supported bindings from existing recordings. Abyss Heaven and B have prepared landing
bindings but remain disabled with the other Abyss ropes.

The timing check also exposed a pre-existing shortcut: one missing-height 2D node could join
both sides of a vertical rope, bypassing its timed arc. The quiet-rope consumer now removes
local cross-level shortcuts around measured rope landings. The neighbourhood scales with map
distance and the reviewed travel time. Missing-height nodes retain the local lower 2D fallback;
no world height is assigned to them. Observed movement, same-level approaches and measured
gradual slopes remain unchanged. All fourteen Split/Summit directions honor the reviewed
two-second cost in the local arrival checks. This remains an estimate at unsampled upper edges.

Abyss's fifteen failing terrain kill lines cross neither tagged breakable. The breakables
compile on the existing flat fallback and clear only at their explicit destruction events.
The rejected height asset is retained for diagnosis, not activated or used for that acceptance
calculation. This does not claim that Abyss's height problems are solved. Never force an
existing accepted live height selection off merely to reproduce a flat preview.

Compiler revision is 14; quiet-rope consumer is 4. Existing per-map artifact keys, round
fingerprints and worker recipe checks invalidate earlier calculations under the changed inputs.
No schema, Impact scoring, scheduler or demo-data changes are needed.

## Validation of this follow-up

The compiler, runtime, artifact, acceptance, job and transport suites pass 129 tests. The
landing-evidence, quiet-route, planar-panel, diagnostics and identity suites pass 64 tests.
These are the final follow-up runs; older totals in FIRST-PASS.md describe earlier checkpoints.
All fourteen real Split/Summit quiet directions honor their reviewed two-second cost. Ten
movement/sight boundary checks pass around Summit Heaven's descent/slam and both Abyss
destruction events. Source/state models initialize across 66 captured Split/Summit/Abyss
rounds; that count is separate from whole-round control/gaps calculations. Seven mask sets were
regenerated. All six available legacy geometry kill-line fixtures pass; Lotus has no legacy fixture.

Private whole-round receipts retain exact candidate, code-diff, height, source and artifact
identities. Each completed scene compares full control with fresh gaps-only and cached gaps,
and samples full/incremental control equivalence. These checks exclude knowledge layers.
Unresolved sampled terrain still uses the recorded 2D sight fallback; Abyss explicitly uses
the existing flat model. Successful parity does not certify deferred ropes or fill missing
terrain evidence. The Linux worker image has not been built locally because Docker is unavailable.


### Current whole-round scenes

| Scene | Sampled full/incremental control | Gap rows | Full / fresh gaps-only / cached |
| --- | ---: | ---: | --- |
| Summit round 1 | 39/39 | 0 | Identical |
| Abyss round 1 | 58/58 | 37 | Identical |
| Split round 13 | 42/42 | 10 | Identical |
| Summit round 17 | 35/35 | 21 | Identical |

These four compiler-14 scenes use the final application diff. Their combined gap-row count is
68; this is a count of results across these scenes, not a new engine accuracy measure.
The earlier compiler-13 checks remain historical evidence for the other map behaviors.

## Remaining evidence and release

Abyss Mid still needs distinct grounded landings and the named-side binding for its approved
one-way 1.5-second quiet climb. Spawn lacks a complete pair of landings that resolves on the
current asset. All four ropes require an accepted height generation; Heaven/B's prepared
world-height bindings must be rechecked against that generation. Ordinary additional replays
can improve the height build, but disabled bundles require a later reviewed candidate update.

The owner-controlled rollout in FIRST-PASS.md still applies: merge prerequisites through the
owner, publish approved annotation/mask/index files through the reviewed release, deploy matching
web/worker recipes, reparse old recordings and check current canaries. The code PR by itself
does not publish the private annotations. No additional worker-memory rehearsal is required.
