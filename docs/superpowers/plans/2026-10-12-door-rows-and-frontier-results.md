# Door visibility rows and movement frontiers

This follows the batching and tick-local reuse described in
`2026-10-12-lotus-visibility-reuse-results.md`. It optimizes the existing control
calculation without changing its spatial or timing rules.

## Visibility reuse

Static map visibility is already stored as packed rows. Repeated door-specific
source contexts now promote into complete packed blocking rows, one row per door.
Combining these rows filters static sight before applying the current source union.
Source order, smoke filtering and skipped targets remain part of the calculation.
Later sources that contribute no unseen targets are rejected using packed bits.

The original ray sampler chooses its interval from the longest currently eligible
target. Consequently, door rows are keyed by that interval as well as the source
node, immutable panel raster digest and vertical band. Reusing a row computed with
a different interval could change thin-panel or height-boundary answers. This
implementation retains the original interval instead of introducing a new sampler.

An additional complete-request memo keys the ordered source nodes, all smoke
values, skipped targets, door poses/bounds and conservative source-area hints.
It returns a fresh mask on every hit. Mutable caller-owned rasters, wall abilities
and diagnostic-count requests retain their ordinary calculation paths.

Sparse pair storage remains capped at 32 MiB. Complete door rows have a separate
64 MiB LRU budget; complete requests have a 16 MiB budget. Promotion tracking and
source proof storage are also bounded. A rotating pose may be reused in memory;
only resting-pose rows are saved between rounds/processes.

Resting snapshots use the geometry/height identity, feature artifact digest and a
row-format version. They are compressed, written atomically and read without
pickle. Missing/corrupt/unavailable snapshots fall back to calculation. The default
directory is `feature-rows` inside the existing control cache; tests/benchmarks can
set `CONTROL_FEATURE_ROW_CACHE_DIR`. This is optional local acceleration and adds
no database table or calculation fingerprint change.

## Movement expansion

Adaptive-grid dilation and node arrival-time spread now relax only destinations
of nodes whose arrivals changed, instead of scanning every node on every pass.
The incoming-neighbor reduction uses the original floating-point expressions.
Directed edges, quiet-drop restrictions, diagonal cuts, wait/release times and
reviewed rope travel costs are retained. Parent selection still uses the original
final-arrival tie-breaking order. Flat-grid operations are unchanged.

## Verification

An independent full-graph relaxation oracle checks the frontier on randomized
directed graphs, including zero-cost edges, restricted regions, quiet edges,
waits, diagonal cuts, one-way timed links and exact route parents. Sight tests
compare against the original per-source algorithm and cover height bands, thin
panels, smoke, source order, changed inputs, cache limits, persistence and cache
failure/invalidation.

The full-round comparison uses the same five Lotus rounds and Ascent round 16
as the previous local comparison. It retains the saved compiler-18 artifacts,
height assets, condensed sources, complete analytical schedule and knowledge/Safe
settings. Each round uses a fresh process and an empty feature-row directory;
reported control time includes building and saving the rows. Fresh gaps run from
the newly generated analytical tick cache.

Acceptance requires equality of every decoded control stream, summary, analytical
tick record, route log, gap row and missing-input diagnostic. Comparing blocker
objects excludes only internal cache/source-proof state; physical rasters, bounds
and vertical bands must match. Runtime file digests pin all six tested rounds to
one implementation. Private replay data and receipts stay outside the repository.

Measurements below are local Windows observations, not a worker throughput
guarantee. Background load was not controlled. The height caster and actual
player-position sight checks retain their existing implementation.

## Complete-round results

Times are minutes. Total includes setup, control, encoding, local tick-cache
writing and fresh gaps. The comparison baseline is branch commit `54bae02`, which
already included the previous batching/tick-local reuse improvements.

| Map / round | Before control | After control | Before gaps | After gaps | Before total | After total | Reduction |
|---|---:|---:|---:|---:|---:|---:|---:|
| Lotus 11 | 5.22 | 3.30 | 0.90 | 0.72 | 6.32 | 4.21 | 33.4% |
| Lotus 16 | 5.81 | 3.84 | 1.28 | 1.27 | 7.29 | 5.32 | 27.1% |
| Lotus 3 | 13.57 | 11.00 | 1.98 | 1.96 | 15.77 | 13.20 | 16.4% |
| Lotus 12 | 25.33 | 20.35 | 4.02 | 4.04 | 29.61 | 24.66 | 16.7% |
| Lotus 6 | 30.59 | 20.87 | 5.02 | 3.75 | 35.90 | 24.88 | 30.7% |
| Ascent 16 | 5.34 | 2.56 | 0.61 | 0.61 | 6.10 | 3.33 | 45.4% |

Five Lotus rounds combined: **94.90 → 72.26 minutes**, a 23.9% reduction.
All six combined: **101.00 → 75.59 minutes**, a 25.2% reduction.
Peak observed working set across the six processes: **1427 → 1513 MiB**.

All six passed every exact-output comparison above, covering **3,829 analytical
ticks and 195 gap rows**. The runtime file digests matched across all six runs and
the finished worktree. Control/summary binaries and gap JSON also matched byte for
byte on all five Lotus cases; the comparator verifies decoded output for all six.

Test runs passed: 170 tests in the core control/feature suite, 43 in the final
cache/frontier suite, and 418 in the wider control/gaps regressions. The latter
initially skipped three Node-dependent tests because Node was absent from the
shell path; rerunning the viewer file with the bundled runtime passed all 15
tests. These suite counts overlap and are not a count of distinct tests.

A separate real-geometry check loaded 21,474 resting rows from the Lotus snapshot
and retrieved a matching source/door/interval row without casting new rays. Warm
full-round throughput was not benchmarked: the six timing runs deliberately began
with empty feature-row directories.

## Remaining costs

Wall-stack profiles show the largest improvement in movement expansion. Its share
of control samples fell from 33–49% to 7–15% across the five Lotus rounds.
Visibility union now accounts for 61–80% of control samples; height casting remains
94–96% of gaps samples. These are inclusive hotspot estimates, not isolated timing
or an ablation that assigns gains to each optimization independently.

Complete-row construction adds cold-pass work, so the visibility cache is not a
uniform cold-pass speedup. Repeated requests/rows reuse their answers, and resting
rows can survive process restarts, but the hardest Lotus rounds still take about
25 minutes locally. Further improvement should target visibility and height
casting while retaining the same output checks. Safe, all unknown source cells,
door coverage and traversal rules remain intact.
