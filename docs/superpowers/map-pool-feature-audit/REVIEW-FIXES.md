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
  walking entry or exit, checking both directions separately. Reviewed walk
  exemptions cannot reopen a faster rope bypass.
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

Combined identities: condenser 18, control 15, gaps 3, feature compiler 16;
Ascent consumer 4, quiet-rope consumer 6 and map-pool consumer 6. There is no
additional schema migration or Impact scoring change in this follow-up.

## Qualified feature selection

| Map | Active features | Pending |
| --- | ---: | --- |
| Ascent | 3 | None |
| Sunset | 1 | None |
| Split | 2 | Four Vent ropes |
| Haven | 0 | No tagged gimmick |
| Summit | 4 | None |
| Abyss | 6 | None |
| Lotus | 3 | None |

These are private-candidate compilation results against local selected assets,
not evidence that the same heights are selected live. The total is **19 active
features, four pending**, replacing the historical all-23 claim.

The owner confirmed a walking/stair exit from Split's upper Vent area. Current
cuts isolate a 78-node area. An attempted exemption through a multiply sampled
height cell reopened a 0.399 s shortcut around a 2 s rope. The exemption was
removed and regression-tested. The four affected bundles remain pending; their
cuts are not installed in the active runtime, so ordinary base walking remains.
The necessary clarification is the location/path of the ordinary exit on the
map. A replay is not required merely to mark it. Its support and route cost must
then be qualified together before enabling these bundles.

## Validation

Linux CI builds the production worker image and pinned patched parser, then
executes archive restoration, adaptive panel and quiet-route regressions inside
that image, including the walking-only archive and its preserved stair exit.
The run at code commit `0a0fb56` passed:
[worker image review](https://github.com/conorlum/valo-with-friends-tracker/actions/runs/38253548461).

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
(33 rows). Split round 2 passes 33/33 and identical fresh/cached gaps (8 rows),
with only its two qualified ropes enabled. Split's sampled on/off scene has no
control difference; independent graph checks establish the two reviewed 2 s
arcs against longer alternative walks. Do not claim every feature affects every
scene.

Real-geometry unknown propagation checks also exercise all 13 enabled directed
rope arcs on Split, Summit and Abyss. Each destination remains unreachable just
before its reviewed cost and arrives at that cost. Twelve prevent premature
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
change viewer presentation, docstrings, tests and CI rather than calculation.
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

1. Mark and qualify Split's missing walking exit, or leave its four Vent ropes
   pending. Separately establish Lotus's runtime on the worker through a
   controlled canary; do not treat compilation qualification as runtime approval.
   If its runtime exceeds the configured budget, approve a separate budget
   change or defer Lotus activation. The other 16 locally qualified features
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
