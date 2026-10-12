# Abyss rope completion

The Abyss model remains supported, but [Post-review fixes](REVIEW-FIXES.md)
supersedes the calculation identities, release selection and validation below.
The historical all-23 claim no longer applies to the combined candidate.

The earlier candidate had enough reviewed rope behavior but deferred all four routes behind a
map-wide height gate. Existing recordings now supply the missing positions: Spawn's upper exit
is a grounded walking sample, and Mid starts on a safe launch platform across a pit rather than
at an imaginary bottom landing. Native motion supplies the rope location regardless of equipment;
qualified stand/walk samples supply approach and departure locations separately.

## Local walking model

Keep the rejected global height asset inactive. An enabled Abyss quiet-rope route may archive a
bounded `walking_domain`: measured coarse/fine floor footprints and their existing walking edges,
with the exact permanent walk-mask identity and diagnostic source-height identity. Only directly
supported ground is retained; unsampled ground stays unknown and no new walkable pixels are opened.
The compiler checks bounds, floor ordering, edges, shared-domain consistency and endpoint binding.
This data travels in the existing immutable per-map feature artifact, not a new DB table or a
worker-local mutable file.

When the selected map height is flat, the union of enabled rope domains supplies local walking
nodes. Sight remains explicitly 2D: these local floors are never ground/ceiling occluders. Timed
quiet arcs and local cross-level cuts prevent walking shortcuts around the rope. Ordinary observed
movement remains separate from quiet reachability. Artifact identities and visibility-cache keys
include the derived walking model; repeated preparation must not alter cached base geometry.

When a full accepted map height is available, use it instead and revalidate world-height endpoint
bindings against that generation. A rejected full sight model is never promoted by this fallback.
Unsupported inputs fail closed. This is an estimate for unknown reachability, not a complete 3D map.

## Route policies

Heaven uses 2.023 seconds, B three seconds and Spawn 2.5 seconds, in both quiet directions. Mid
connects the safe Choke 2 launch to the top exit in one quiet direction. It includes normal unknown
horizontal approach/catch time and the owner's additional 1.5-second climb, then resumes normal
walking. It creates no walking node in the pit, no quiet reverse arc and no synthetic deaths.

## Verification and release

Prove supported walking exits, exclusion of platforms/abilities, same-source temporal pairing,
bounded/canonical domains, unchanged 2D sight, absent off-ground nodes, timed arrivals, forbidden
reverse Mid passage, immutable cached geometry and artifact transport/cache-loss reproduction.
Then compute representative real rounds with sampled full/incremental control and independent
fresh/cached gaps checks. Keep source/state checks distinct from whole-round calculations.

Update the private annotation/mask/index proposal and PR #128. Main, annotation publication, live
height activation, deployment and live recompute remain owner-controlled. No additional replay
input or worker-memory approval is required before investigating the existing evidence.

## Completed checks

The compiler-15 / quiet-consumer-5 candidate selects all 23 tagged features across the seven-map
pool, including all four Abyss ropes. All seven allowed Abyss quiet directions honor their costs.
Mid's local reverse crossing is unreachable; no pit walking pixels are opened. Each rope exit
allows onward walking, including over eight metres within ten seconds. One hundred terrain sight
lines match the existing 2D model. All 23 captured Abyss rounds initialize their source/state model.

| Fresh Abyss scene | Sampled full/incremental control | Gap rows | Full / fresh gaps-only / cached |
| --- | ---: | ---: | --- |
| Round 1, B reference and both breakables | 58/58 | 36 | Identical |
| Round 12, Mid crossing | 55/55 | 19 | Identical |
| Round 15, reviewed Heaven ride | 36/36 | 21 | Identical |
| Round 18, reviewed Spawn ride | 41/41 | 15 | Identical |

These whole-round checks exclude knowledge layers. Source warnings remain in the private receipts:
unknown gun hearing range, track-break speed and an ignored utility exclusion, where applicable.
The walking-only/2D-sight mode is an explicit calculation note. Their 91 combined gap rows are
not an engine-accuracy score; parity verifies the three calculation paths agree.

The final rope/runtime/archive/transport/height/adaptive/measurement suites pass 196 tests;
the control-engine/task/gaps suites pass 143. Earlier compiler/diagnostic/panel/acceptance checks
pass 102 tests; those overlap the later suites. Isolated compilation reproduces the same archive
without global height bytes. The global height verifier explicitly rejects walking-only assets.
Seven publication mask sets were regenerated; all six available legacy kill-line fixtures pass.
Lotus has no legacy fixture. The Linux image has not been built locally because Docker is unavailable.
Local browser previews remain unverified because the browser tool blocks file URLs.

The Mid route's total estimate is about 3.405 seconds: 1.905 seconds of normal unknown horizontal
approach/catch plus the owner's 1.5-second climb. The other reviewed costs remain unchanged. The
terrain-only refinement investigation reduced some false blocks but did not pass all global gates;
neither diagnostic height candidate was activated or substituted for an accepted live selection.
