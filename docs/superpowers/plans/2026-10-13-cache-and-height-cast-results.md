# Exact visibility reuse and height-caster follow-up

The change reduces repeated sight work while retaining the reviewed control,
Safe, unknown movement and gaps behavior. It follows the six-round benchmark
recorded in `2026-10-12-door-rows-and-frontier-results.md`; the comparison baseline
is branch commit `cd574b2`.

## Implementation

Door rows now earn promotion through repeated requests and enough requested
targets. Promotion reuses already computed source/target answers at the same
sampling interval. An evicted row must earn promotion again. The 64 MiB row
budget allows at most 16 MiB of moving rows, with resting rows borrowing unused
space. Separate eviction lists avoid repeatedly scanning the whole cache.
Ready rows remain usable when another panel still needs sparse checks. That
fallback returns exact bits for the requested targets only; partial results
never enter the complete-row cache or its persistent snapshot.

Ability walls can participate in whole-request reuse. Cache keys snapshot their
segment coordinates and retain exact source ordering, smoke values, vertical
bands and door poses. Changed walls miss the cache. Mutable or untrusted panel
rasters retain the uncached path. Existing sampling-step keys remain necessary
because the longest requested target determines the original sampling interval.

Height casting skips absent upper-floor columns and compacts stopped rays after
at least a quarter of the current batch has stopped. Remaining rays retain their
original angles, sample positions, slab state and horizon state. Fixed height
arithmetic moves outside the sampling loop without changing subtraction order.
These derived values are computed per cast, so changes to caller-owned terrain
are still observed.

Gaps keeps a bounded 32 MiB exact sight-result cache across ticks. Its key includes
the exact player position, eye height, source node and physical blocker values.
Replacing geometry or its relevant arrays/configuration invalidates the cache;
the geometry itself remains fixed during a detector run. This does not group
positions or remove source cells.

Loading the worker's tick cache used to restore NumPy panel masks as writable,
which denied them reuse. The loader now checks each stored panel's shape, type,
SHA-256 digest and raster bounds before restoring read-only status. Invalid or
legacy panels keep ordinary sight computation. A shared mask cannot make a
malformed panel eligible for reuse. The tick-cache format remains compatible.

Cache budgets account for stored payloads and conservative per-entry overhead;
they are not a cap on the whole Python process. Sparse door pairs retain their
32 MiB budget and whole visibility requests their 16 MiB budget. Only complete
resting-door rows persist, with the existing geometry/features/version identity
and best-effort atomic replacement. Moving rows and gaps results are process-local.

## Verification and measurement

The final focused suite passed **302 tests**, with **3 skipped disposable
PostgreSQL-only storage cases**. Viewer tests passed. An independent frozen copy
of the previous height caster checks exact nodes and diagnostics on overlapping
floors, adaptive cells, missing heights, smoke, walls, varied angles and mutable
terrain. Cache tests cover admission, eviction, mixed readiness, changed walls,
geometry replacement, persistence and panel verification after pickle loading.

The six full cold runs passed exact comparisons of decoded control streams,
summaries, analytical ticks, route logs, gaps and missing-input diagnostics.
Physical blocker masks, bounds and vertical bands must match; only internal
cache/source-proof metadata is excluded. The runs cover **3,829 analytical ticks
and 195 gap rows**. Runtime file digests pin every round to one implementation.
Private replay inputs and receipts stay outside this public repository.

The same five Lotus rounds and Ascent round 16 use the previous compiler-18
artifacts, height assets, condensed sources and complete analytical schedule.
Knowledge and Safe stay enabled. Each cold round starts a fresh process and an
empty persistent door-row directory. Base geometry uses the same existing disk
cache as the baseline. Fresh gaps replay newly written analytical tick records.
Total time includes setup, control, encoding, tick-cache writing and gaps.
Both implementations include approximately 20 Hz wall-stack profiling.

Times below are minutes on the local Windows machine. Background load was not
controlled; these are observations, not worker throughput guarantees. The changes
were measured together, not as individual performance ablations.

| Map / round | Previous total | New cold total | New warm total | Cold reduction | Warm reduction vs previous |
|---|---:|---:|---:|---:|---:|
| Lotus 11 | 4.21 | 3.90 | 3.82 | 7.4% | 9.3% |
| Lotus 16 | 5.32 | 5.14 | 5.02 | 3.3% | 5.6% |
| Lotus 3 | 13.20 | 12.15 | 12.17 | 7.9% | 7.8% |
| Lotus 12 | 24.66 | 22.44 | 23.11 | 9.0% | 6.3% |
| Lotus 6 | 24.88 | 24.46 | 23.61 | 1.7% | 5.1% |
| Ascent 16 | 3.33 | 3.25 | 3.69 | 2.5% | -10.7% |

Five Lotus rounds combined: **72.26 previous, 68.10 cold and 67.72 warm minutes**.
Cold used 5.8% less time than the previous implementation; warm used 6.3% less.
The warm run used 0.6% less time than the new cold run.

All six combined: **75.59 previous, 71.35 cold and 71.41 warm minutes**.
Cold used 5.6% less time than the previous implementation; warm used 5.5% less.
The warm run used 0.1% more time than the new cold run: effectively no additional
overall improvement in this batch.

Peak observed working set: **1513 MiB previous, 1545 MiB cold and 1546 MiB warm**.

All six warm runs also matched their new cold counterparts exactly under the same
checks, covering another 3,829 analytical ticks and 195 gap rows.

Each warm run uses a fresh process and the complete resting-door snapshot saved
by its matching cold round. Sparse pairs, whole requests, moving rows and gaps
memoization start empty. This measures process restart reuse of those saved rows;
it does not measure an already warm worker serving many different rounds.

| Stage, all six rounds | Previous minutes | Cold minutes | Warm minutes |
|---|---:|---:|---:|
| Control | 61.93 | 61.86 | 61.84 |
| Gaps | 12.36 | 8.17 | 8.21 |

Cold gaps used 47,668 actual casts and reused 29,170 exact requests (38.0% of
these requests). Its combined stage time fell by 33.9%.

## Limits

The cold benefit is mostly in gaps. Control time is not uniformly lower: Lotus
16, Lotus 6 and Ascent 16 were slower in that stage in this batch, while total time
was lower. The hardest Lotus rounds still take over twenty minutes locally.
This change does not establish a two-to-four-minute target for every round.
Warm Ascent was slower than both its previous and new cold runs. There is one
measurement per case and background load was not controlled, so these observations
do not establish a repeatable per-round warm-cache speedup or its absence.

The cold Lotus 12 profile still places approximately 77% of rotating-phase control
samples inside feature visibility union, including approximately 40% inside door
row lookup/construction. Height casting still appears in approximately 92% of
rotating-phase gaps samples. These are overlapping, inclusive wall-stack estimates,
not isolated timings. Further work should target the repeated visibility unions
and moving-panel checks; saved resting rows alone did not resolve that cost.

No live database, deployment or main-branch change is part of this work.
