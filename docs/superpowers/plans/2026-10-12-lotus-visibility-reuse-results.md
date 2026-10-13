# Lotus visibility batching and repeated-request reuse

This change reduces repeated visibility work while retaining the previously reviewed
control and gaps behavior. It applies to feature-bearing maps generally; the measured
comparison uses five complete Lotus rounds from one locally available recording.

## Implementation and correctness boundaries

`control.features.seen_from_with` batches consecutive sources only after a conservative
proof shows that each source's entire static visibility row misses every feature
blocker. Rotating panels use the enclosing sweep and the existing authored source
area proof. Fixed panels use their enclosing rectangle. Heights and smoke are ignored
in this proof because they can only reduce visibility. Mutable or untrusted rasters
retain individual checks. Fixed-panel proof storage is bounded to 64 entries separately
from the existing 32 MiB checked-pair cache.

Source order remains unchanged: batching a later source early would change the
remaining targets and therefore the longest-ray sampling interval of an earlier panel
check. Smoke filtering is performed once per source pair rather than also in a
preliminary union. Processing stops when all candidate targets are covered. Calls that
request diagnostic counts retain the logical individual-source checks.

`gaps.detect.GapDetector._sight` shares identical requests within a single read-only
tick. The key includes exact position, eye height, node and blocker references; retaining
the references prevents temporary object ID reuse. The memo clears before each tick.
Direct mutable feature rasters remain uncached and cached results are read-only. The
cast counter now measures physical casts. Fallback notes remain available.

Safe, unknown movement, height interpretation, door geometry, majority coverage,
analytical timing and gap qualification rules remain unchanged. This introduces no
representative-cell sampling or approximate room grouping. Artifact/compiler identities
are unchanged because the optimization preserves results.

## Measured comparison

All five final runs passed exact output parity and used the same runtime file digests.
Elapsed minutes, including setup and output writing:

| Round | Before control | After control | Before gaps | After gaps | Before total | After total | Total reduction |
|---|---:|---:|---:|---:|---:|---:|---:|
| 11 | 6.16 | 5.22 | 1.00 | 0.90 | 7.35 | 6.32 | 14.0% |
| 16 | 6.69 | 5.81 | 1.90 | 1.28 | 8.79 | 7.29 | 17.0% |
| 3 | 17.15 | 13.57 | 3.06 | 1.98 | 20.44 | 15.77 | 22.8% |
| 12 | 30.32 | 25.33 | 6.17 | 4.02 | 36.75 | 29.61 | 19.4% |
| 6 | 31.14 | 30.59 | 5.75 | 5.02 | 37.15 | 35.90 | 3.4% |

Combined runtime was 110.48 → 94.90 minutes: 15.59 minutes saved, or 14.1% less
elapsed time. Combined control time fell 12.0%; gaps time fell 26.2%. Peak observed
working set was 1431 MiB before and 1427 MiB after. A preliminary trial of round 11
was preserved separately and excluded; the table uses its final-code rerun.

Both versions use identical full round inputs, compiler-18 artifact, height asset,
existing geometry visibility cache, `knowledge=True`, `full_every=0` and the default
complete analytical schedule. Each round runs in a fresh subprocess with a cold bounded
feature cache. Fresh control produces tick records, then fresh worker-style gaps replay
those records. Total time includes setup, encoding and writing the tick cache.

The measurements are local Windows wall time with approximately 20 Hz stack sampling.
Sampler overhead and background load were not controlled. These results do not establish
live worker throughput or a guaranteed speedup. Raw replay inputs, outputs and detailed
receipts remain in private local artifacts.

## Validation

The affected suite passed 387 tests, with three PostgreSQL-only tests skipped. One
Windows path-length failure was resolved by rerunning that test with a shorter temporary
directory. After the final remaining-target counter refinement, 29 focused tests passed.
Coverage includes mixed affected/unaffected sources, smoke, skips, thin panels, height
bands, immutable/mutable masks, changed sight inputs, fallback notes and tick boundaries.

The complete-round comparison checks control headers and every binary stream, summaries,
gap rows, tick headers and every analytical record, route logs and missing-input
diagnostics. When comparing cached blocker objects, only internal source-area proof
state is excluded; actual rasters, bounds and height bands must match. Every receipt
records the modified runtime file digests so all final measurements use the same code.
The five rounds contain 3504 analytical ticks and 162 gap rows in both versions.

## Remaining cost and release

Sampling still identifies visibility unions and movement expansion as substantial control
costs. In the final runs visibility unions account for roughly 34–56% of control stack
samples, movement expansion 33–49%, and height casting 94–96% of gaps samples.
These inclusive percentages overlap and are hotspot estimates, rather
than exact CPU accounting. Further optimization should target those operations before
changing the model by removing Safe or sampling fewer unknown positions.

Some local full-round durations exceed the worker's default normal 900-second budget;
verify deployed limits and worker hardware when preparing the release. This change does
not adjust worker timeouts, activate artifacts, publish data or deploy a release.
