# Round 2/14 bonus-round denial -- candidate `impact-bonus-denial-rc2`

**Status: implemented, frozen and reviewed; NOT activated.** `ACTIVE_MANIFEST` is None and
`IMPACT_CALCULATION_VERSION` is 2. Nothing was rescored, backfilled or deployed.

- Manifest: `candidate-manifest.json`, LF-SHA-256 `85b873cd19803c831c20930f3f3c84a8f6f6f3ff6197edb464f0182fd4156f68`,
  scorer revision `90d9cd1`, release comparator `buy_disruption_v2_30_80_bonus_denial`, weights A=1.25 / B=1 / C=1
- Declared before scoring: the "bonus-round denial rc2" amendment in `../2026-09-07-predeclared-values.md` (commit `25c4c14`)
- Supersedes rc1 (`../econ-bonus-denial-candidate/`), whose reports are kept as recorded. Runbook: `README.md`.

## What rc2 changes relative to rc1

Code-review fixes F1-F7 (see the ledger amendment and `../plans/2026-09-12-bonus-denial-review-fixes.md`):

- **Owner rule, in-round pickups:** a kill-feed pickup counts only when the survivor's paid kit is below the picked-up
  gun's price plus every gun they had already fired (a Vandal + Sheriff kit shows both were owned).
- **Owner rule, survive-loss reward:** a survivor of a round their team lost banks 1,000, never the loss bonus. This was
  verified at team level on the raw captures, where the apparent exceptions were spike-detonation deaths.
- **Traces** explain bonus rounds with the denial audit; **the comparison** averages only scored rounds; **the manifest**
  describes the bonus rule; **ingestion** skips an invalid spend value; this README and `review-results.json` make the
  candidate activation-ready.

## Declared checks

| # | check | result |
|---|---|---|
| 1 | reviewed matches' source rows equal the frozen fingerprints | pass: 3104, the ten, 3120 |
| 2 | corpus comparison: parity mismatches / input failures | **0 / 0** over 3,124 of 3,124 matches |
| 3 | unchanged 30/80 corpus audit at `90d9cd1` vs the 30/80 candidate's | **identical** on all 18 keys (`rc2-parity.md`) |
| 4 | site reviews reconcile | 3104: 570 checks, the ten: 5,140, 3120 + trace: 958 -- **all pass** |
| 5 | defect reinstatement | **24 of 24** applied mutations detected, of 24 (`defect-reinstatement.md`) |
| + | `review-results.json` for backfill acceptance | written for 12 matches; its run's reconciliation: 5,140 checks pass |

Full test suite at `90d9cd1`: 1,116 passed, 2 failed -- exactly the two known-red tests.

## Problem flags (declared)

| flag | value | raised |
|---|---|---|
| (a) rounds 2/14 mean gross > 2x rounds 3/4/15/16 mean (bonus model, scored rounds only) | 442.5 vs 316.1 (1.40x) | no |
| (b) any half-round-2 abstention reason > 1% of rounds 2/14 | worst 0.84% of 6,183; reasons {'final_round': 52, 'surrender': 15}; new bonus reasons fired: none | no |
| (c) any parity or validation failure | none | no |

## The difference from the current 30/80 candidate (C = 1; multiply econ points by 3.871 for the owner's C)

Gross econ points per scored round:

| round | scored | 30/80 | bonus rc2 | change |
|---:|---:|---:|---:|---:|
| 2 | 3,124 | 103.6 | 386.6 | +283.0 |
| 3 | 3,124 | 312.1 | 312.1 | +0.0 |
| 4 | 3,114 | 310.8 | 310.8 | +0.0 |
| 5 | 3,109 | 274.2 | 274.2 | +0.0 |
| 6 | 3,104 | 253.1 | 253.1 | +0.0 |
| 7 | 3,098 | 252.9 | 252.9 | +0.0 |
| 8 | 3,093 | 259.6 | 259.6 | +0.0 |
| 9 | 3,087 | 264.6 | 264.6 | +0.0 |
| 10 | 3,080 | 269.8 | 269.8 | +0.0 |
| 11 | 3,076 | 247.0 | 247.0 | +0.0 |
| 14 | 2,992 | 175.4 | 500.8 | +325.4 |
| 15 | 2,922 | 309.2 | 309.2 | +0.0 |
| 16 | 2,807 | 333.7 | 333.7 | +0.0 |
| 17 | 2,614 | 283.5 | 283.5 | +0.0 |
| 18 | 2,391 | 269.8 | 269.8 | +0.0 |
| 19 | 2,128 | 267.8 | 267.8 | +0.0 |
| 20 | 1,813 | 271.7 | 271.7 | +0.0 |
| 21 | 1,508 | 271.1 | 271.1 | +0.0 |
| 22 | 1,149 | 269.5 | 269.5 | +0.0 |
| 23 | 778 | 267.6 | 267.6 | +0.0 |

Rounds 2/14 combined: 138.7 -> 442.5 points per scored round, 1.40x rounds 3/4/15/16 (316.1).

Player-round econ change: 640,832 of 659,290 rows unchanged; p1 -78, p99 75.
Player-match Impact change: mean +4.0, p1 -195, p50 0, p99 307, min -296, max 702.
Within-match rank changes 2,550 of 31,240; leaderboard Spearman
0.9997, top-20 overlap 20/20.

## What moved from rc1 to rc2

rc1's per-round averages counted abstained rounds (67 of rounds 2/14) as scored, so its rounds-2/14 figure is slightly low.

| quantity | rc1 (as recorded) | rc2 | change |
|---|---:|---:|---:|
| rounds 2/14 gross points per round | 437.5 | 442.5 | 5.0 |
| qualifying deaths | 10,181 | 10,181 | 0 |
| denied credits | 26,369,000 | 26,369,000 | 0 |
| net denied credits | 25,754,950 | 25,766,900 | 11,950 |
| recovery netted | 2.33% | 2.28% | |
| survivors with credit evidence | 807 | 816 | 9 |
| survivors with in-round kill-feed pickup | 92 | 64 | -28 |
| survivors with carried kill-feed pickup | 198 | 199 | 1 |
| survivors with both | 69 | 68 | -1 |
| leaderboard Spearman vs 30/80 | 0.9997 | 0.9997 | |

## Files

`README.md`, `candidate-manifest.json`, `review-results.json`, `model-comparison.json`, `match-3104-site.md`,
`fixed-ten-site.md`, `match-3120-site.md`, `trace-3120-site.md`, `rc2-parity.md`, `defect-reinstatement.md`.
