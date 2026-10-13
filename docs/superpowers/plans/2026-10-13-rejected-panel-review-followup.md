# Rejected panel recovery and current worker-image validation

The review of `29faa99` found one correctness defect: tick-cache loading rejected
a panel's raster identity or bounds, but ordinary sight calculation continued to
use the rejected bounds to cull lines. This could expose targets behind the panel
even though result memoization correctly refused it.

## Fix and regression

`BoundedOccluder.discard_acceleration()` clears raster identity, pixel bounds and
source proofs together. Both rejection paths in the tick-cache loader call it.
The physical mask, owner and vertical band remain unchanged. A rejected panel
therefore uses complete ordinary sight sampling, with no derived culling or cache
proofs. Shared raster storage with a verified panel cannot restore its eligibility.
Verified panels retain their bounds and read-only masks.

The behavioral regression writes and reloads an actual compressed tick cache,
then compares the gaps detector's complete sight mask with an independently
constructed blocker carrying the same physical raster/band and no acceleration
metadata. It checks two successive ticks, an actually hidden target, and denied
memoization. Incorrect bounds, a bad digest, missing identity and missing bounds
are covered. Before the fix, the first three cases failed visibility comparison;
afterward all pass. The earlier metadata-only test no longer requires rejected
bounds to survive.

## Validation

The broader local Windows suite passed **401 tests**, with **3 disposable
PostgreSQL-only storage cases skipped**. Coverage includes all gaps tests,
visibility caches, the frozen height-caster oracle, movement-frontier oracle,
adaptive panels, rope walking ground, quiet routes and walking exits. Viewer
checks ran with the bundled Node runtime. Counts overlap previous runs.

The production Dockerfile built successfully in the local Docker Linux engine,
including its pinned parser source, patch verification, .NET publication and
runtime import smoke test. The build context contained only tracked worker inputs;
private recordings and environment files were excluded.

The expanded worker suite passed **175 tests inside that image**, with no skips.
It includes the new visibility-row tests, height compaction, gaps observer and
exact sight-cache tests, and topology frontier tests alongside the existing
panel/rope regressions. The GitHub workflow now runs the same suite and mounts
the control-reference helper read-only for the observer test.

The tested runtime was Python 3.12.3, NumPy 2.4.6, SciPy 1.18.1 and Pillow 10.4.0.
The local image identity was
`sha256:17a055e2025d909ac0559689091ff928181d53d0425a75b89481f6b3e9f52f47`.
Image source digests and the complete worker-input snapshot are checked against
the current branch files; JUnit results and the receipt remain in the private
review directory.

The accepted map approximations, calculation settings, parser pin and physical
rules did not change. Full-round timings in the previous report remain measurements
of `29faa99`; they were not rerun or relabeled as measurements of this fix.

## Remaining release gates

The confirmed code defect and local Linux image-validation gap are addressed.
A GitHub CI run on the published final commit still needs to pass; an older
remote-head run does not cover these local changes.

The owner-controlled [map-pool release process](../map-pool-feature-audit/RELEASE.md)
still requires annotation approval, agreement between the deployed web/worker
recipes, accepted height selection and reparse/recompute canaries. The local
image test does not certify production throughput or PostgreSQL trigger/lock
behavior.

Lotus retains the production-runtime gate documented in
[REVIEW-FIXES.md](../map-pool-feature-audit/REVIEW-FIXES.md). Confirm actual deployed
limits and run its production-hardware canary before expanding activation. Worker
defaults are 900 seconds for a regular round, 1,800 seconds for initial map warming
and a 2,048 MB Linux child address-space cap. No limits or activation decisions
were changed here. If the canary fails, holding Lotus or changing its runtime
budget requires the owner's decision.

No main merge, push, deployment, live database write, publication, live height
activation or archive recompute was performed during this follow-up.
