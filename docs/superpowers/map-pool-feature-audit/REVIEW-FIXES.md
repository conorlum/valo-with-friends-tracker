# Post-review release status

This document supersedes the compiler-15 release claims in the older runbooks.
The feature branch includes current main's player-state, blindness, damaging
molly and corrected Abyss barrier behavior. Main and both deployed sites remain
unchanged by this work. PR #128 remains a draft stacked on #127.

## Completed corrections

- Sampled descending and horizontal panels bind exact spatial leaves through
  the compiler's shared reducer, including qualified shallow wall attachments.
  Door qualification samples open, partial, cutoff and closed movement states.
- Lotus's rotating doors use local 4-pixel computational leaves across their
  sweeps. Movement blocks only above 50% coverage of a leaf. Sight keeps the
  moving slab. Existing heights, unknowns, stacked floors and directed walking
  edges are retained; this tessellation cannot be activated as measured terrain.
- Compiler/runtime checks reject rope cuts that remove an existing landing
  walking entry or exit, checking both directions separately. Explicitly reviewed
  rope-only endpoints are bound to their route and measured landing; this does
  not waive the gate for other landings or maps. Qualification also rejects an
  ordinary walk between a rope-only endpoint and its opposite landing, even if
  slower than the rope. Reviewed walk exemptions cannot reopen a faster bypass.
- Feature controls, status and seekable events are exposed for all six supported
  feature-bearing maps in linked and standalone viewers. Standalone feature
  panels do not expose linked-only Control/Gaps tabs, including when those
  contexts are accidentally supplied; the regression covers all seven maps.
- Missing, partial or unbound round sources are durable input failures with
  reparse guidance, without three infrastructure retries. Gaps-only input
  failures preserve the existing control row. Corrupt definitions remain errors.
- The private annotation proposal was regenerated from current main's legacy
  tags. Abyss barrier paint and PNG match current main byte for byte. The only
  intentional legacy edit is the owner-reviewed Ascent Catwalk corner.
- Trusted interior panel bounds reject sight segments that cannot intersect
  the panel before pixel sampling. Border masks retain the conservative clipping
  path. The unculled oracle and unchanged sample interval protect thin panels,
  height bands, vertical rays and out-of-map endpoints.
  Interior panels also restrict work to original sample indices within their
  enclosing rectangle, with padding at interval ends. This skips irrelevant
  samples without changing the pixel positions; untrusted and border masks
  retain the complete clipped path. A representative Lotus batch of 30 sources,
  5,938 targets and three occluders gives identical answers in 0.025 s versus
  0.288 s for segment culling alone. This microbenchmark is not a whole-round
  throughput claim. Cache experiments gave marginal gains and were not shipped.

Combined identities: condenser 18, control 15, gaps 3, feature compiler 17;
Ascent consumer 4, quiet-rope consumer 7 and map-pool consumer 6. The rope-only
policy is an archived runtime input and changes the feature identity; endpoints
must still rebind against each selected height generation. There is no
additional schema migration or Impact scoring change in this follow-up.

## Qualified feature selection

| Map | Active features | Pending |
| --- | ---: | --- |
| Ascent | 3 | None |
| Sunset | 1 | None |
| Split | 6 | None |
| Haven | 0 | No tagged gimmick |
| Summit | 4 | None |
| Abyss | 6 | None |
| Lotus | 3 | None |

These are private-candidate compilation results against local selected assets,
not evidence that the same heights are selected live. After the owner's Vent
topology correction the total is **23 qualified features, zero pending bundles**.
Lotus still has the separate runtime gate below.

The owner clarified that the yellow Vent area is below both sides and ropes
are its only entrances/exits. It had been mislabeled as an upper area in the
earlier handoff. Measured lower landings are about 3.5 m and the upper landings
about 7.5 m. The 78-node lower component separated from the 5,852-node ordinary
walking area is therefore intentional. Routes 21, 23, 25 and 31 declare only
their lower endpoint `a` in `quiet_rope_only_endpoints`; all upper endpoints
retain the normal walking-entry/exit gate. The discarded 0.399 s guessed stair
connector remains absent. All six Split ropes now qualify; the four Vent ropes
keep the reviewed 2 s costs in both directions. No heights or walkable ground
were invented to enable them.

## Validation

Linux CI builds the production worker image and pinned patched parser, then
executes archive restoration, adaptive panel and quiet-route regressions inside
that image, including the walking-only archive and its preserved stair exit.
The run at code commit `f338f0a` passed:
[worker image review](https://github.com/conorlum/valo-with-friends-tracker/actions/runs/38302548730).
The endpoint-scoped correction also passes 98 focused schema, input identity,
compiler/runtime archive, walking-exit and rope-cost tests. The default exit gate
remains enforced; malformed declarations, wrong/unresolved landing heights and
exceptions claimed by another route cannot waive it. The later separation guard
passes 35 affected-file tests, including a slower walking bypass and restored
rope-only archives.

The full local replay suite recorded 2,860 passes and 22 skips, with seven
failures subsequently corrected: a standalone panel exposed linked-only tabs,
preview fixtures inspected a developer's installed parser, a freshness assertion
still expected condenser 15, and three walking-archive tests used a fixture
that isolated its upper landing under the new exit gate. The corrected fixtures
preserve a supported stair path slower than the rope, use real unknown walking
speed, and verify that the reverse direction has no rope shortcut while the
ordinary walk survives. No production gate was weakened. Final affected-file
reruns pass 95 geometry/archive/freshness/sampling tests and 158 viewer/preview
tests. The full suite was not repeated after these focused corrections. Database
checks use isolated SQLite; PostgreSQL suites remain skipped.

Fresh combined-recipe full rounds include knowledge layers. Ascent round 16
passes 50/50 sampled counterfactual comparisons and identical fresh/cached gaps
(33 rows), recorded under compiler 16. A new compiler-17 Split round 2 run at
`82949a3` enables all six ropes and passes 33/33 with eight identical fresh/cached
gap rows, taking 310.93 s and about 210 MB peak locally. It uses the exact same
recording as the earlier two-rope run. The final candidate is reverified under
the later separation guard, which rejects additional invalid inputs without
changing this qualified calculation. The earlier Split on/off scene had no
control difference; independent real graph checks establish material rope
arrival delays. Do not claim every feature affects every scene.

Real-geometry unknown propagation checks now exercise all 21 enabled directed
rope arcs on Split, Summit and Abyss. Each destination remains unreachable just
before its reviewed cost and arrives at that cost. Twenty prevent premature
arrival in the unqualified base graph; Abyss Mid supplies the missing one-way
connection instead. This is a material reachability check independent of whether
a particular recorded scene changes its final control masks.

Abyss round 15 passes the actual worker child entry point with verified pinned
inputs and archived artifact transport, including knowledge and full-round
gaps. Features-on produces 21 gap rows; features-off produces 19. On this local
run it took 605.10 s and about 315 MB peak, versus 137.80 s and about 211 MB
without the artifact (about 4.4 times the elapsed time). The baseline uses the
same permanent masks but lacks the artifact's local walking model. Concurrent
local timings are not a production throughput forecast.

Fresh gaps-only and cached gaps-only runs independently reproduce all 21 rows
from the feature-enabled production worker run.

The final complete Lotus round at calculation commit `8317268` passes 89/89
counterfactual checks and fresh/cached parity for 50 gap rows. Its encoded
control, summary and gaps are byte-identical to the saved unoptimized result
under the same recording, artifact, grid and selected height. Later commits
change viewer presentation and add the endpoint-scoped Split topology correction;
Lotus's numerical calculation is unchanged. Its artifact identity is refreshed
under compiler 17, so the saved full-round receipt remains compiler-16 evidence,
not a new compiler-17 full-round certification.
The final run took 3,848.95 s (about 64 minutes) and peaked at 1,459,228,672 bytes,
versus 3,654.39 s and 1,455,529,984 bytes for the earlier run. These concurrent
local timings demonstrate no whole-round speedup; the microbenchmark gains do
not close the runtime gate. Worker defaults allow 900 s per regular round and
1,800 s for initial map warming, with a 2,048 MB Linux child address-space cap.
No timeout, worker count or memory limit has been raised. A production-hardware
canary must establish an acceptable runtime before activating Lotus or expanding
its live queue. An owner-approved runtime-budget change or a rollout excluding
Lotus would be separate decisions; the default is to hold its activation.

Boundary checks compare enabled and disabled features on the same prepared
permanent domain/grid. They separately record movement/sight masks, timed rope
costs and sampled full/incremental agreement. Ascent, Sunset, Summit and Abyss
show material control differences at checked boundaries. Private receipts
retain source hashes, artifact identities, selected heights and missing-input
notes. Existing terrain fallbacks are not waived by parity.

The independent legacy-input snapshot from current main remains unchanged. Its
captured control revision is 9, so the comparison pins that revision while this
branch uses 15. This corrects the merge-time test assumption, not its expected
map masks, game figures or recorded fingerprints.

The worker receipt records the commit at completion; its archived source-file
hashes identify the copied worker code at invocation. The later walk-exemption
guard has no effect on the accepted Abyss candidate, which has no exemptions.
Historical compiler-14/15 results are retained as history, not current release
certification. Interactive browser QA remains a separate owner check: the
computer-use browser blocks local file previews.

## Owner-controlled release

1. Establish Lotus's runtime on the worker through a
   controlled canary; do not treat compilation qualification as runtime approval.
   If its runtime exceeds the configured budget, approve a separate budget
   change or defer Lotus activation. The other 20 locally qualified features
   can be considered separately after their release gates pass.
2. Review the private tags/masks/index proposal, including the explicit Ascent
   corner change. Publishing annotations is separate from merging code.
3. Merge the prerequisite #127, retarget #128 to current main, and approve its
   merge. Verify the web and worker deploy the same parser/control identities.
4. Confirm accepted live height selections through normal gates. Never activate
   rejected Abyss global heights or computational/local walking assets as sight
   terrain.
5. Reparse one canary recording per feature map on the combined recipe, then
   recompute and inspect states, identities, control/gaps and failure notes.
   Recompute alone cannot supply missing native messages. Expand the existing
   stale-result workflow after the canaries pass.

The default input policy remains fail-closed with reparse guidance; feature-free
fallback under a feature-bearing fingerprint is not silently published. No live
database, height selection, annotation publication, deployment or merge has been
performed during these corrections.
