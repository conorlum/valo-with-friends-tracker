# Round 2/14 bonus-round denial -- candidate `impact-bonus-denial-rc1`

**Status: implemented, frozen and reviewed; NOT activated.** `ACTIVE_MANIFEST` is None and
`IMPACT_CALCULATION_VERSION` is 2. Nothing was rescored, backfilled or deployed.

- Spec: `docs/superpowers/specs/2026-09-12-econ-bonus-round-denial-design.md`
- Plan: `docs/superpowers/plans/2026-09-12-econ-bonus-round-denial.md`
- Manifest: `candidate-manifest.json`, LF-SHA-256 `2a5d247c8471b7489a2ea718d9fff3597c81b7850f7350cb000c891926798172`,
  scorer revision `c7cbfd7`, release comparator `buy_disruption_v2_30_80_bonus_denial`, weights A=1.25 / B=1 / C=1 (rc2's)
- Declared before scoring: the 2026-09-12 entry in `docs/superpowers/2026-09-07-predeclared-values.md` (commit `10a6867`)

## What changed

A pistol winner's first death in round 2 or 14 with paid kit net of agent utility above 1,500 credits is worth
`V x 1.10 x net denied / 19,500` to the enemy killer (V = 0.8 if the pistol winner still won the round, 1.0 if
they lost) and 80% of that as the victim's debit, replacing background and disruption. Recovery nets surviving
teammates' inferred pickups (credit surplus above utility cost, or the kill-feed upgrade). Every other round, and
every death on the pistol-losing team, scores exactly as the current 30/80 candidate.

## Declared checks

| # | check | result |
|---|---|---|
| 1 | reviewed matches' source rows equal the frozen fingerprints (now incl. kill weapons) | pass: 3104, the ten, 3120 |
| 2 | corpus comparison: parity mismatches / input failures | **0 / 0** over 3,124 of 3,124 matches |
| 3 | rc2 parity: unchanged 30/80 audited at the new revision vs rc2's corpus audit | **identical** on all 18 keys (`rc2-parity.md`) |
| 4 | site reviews reconcile | 3104: 570 checks, the ten: 5,140, 3120 + trace: 958 -- **all pass** |
| 5 | defect reinstatement | **16 of 16** applied and detected (`defect-reinstatement.md`) |

Full test suite at `c7cbfd7`: 1,109 passed, 2 failed -- exactly the two known-red tests
(`test_impact_exante_swing::test_builder_matches_stored_values`, `test_site_stats_cache::test_happy_path_blob_validates`).

## Problem flags (declared)

| flag | value | raised |
|---|---|---|
| (a) rounds 2/14 mean gross > 2x rounds 3/4/15/16 mean (bonus model) | 437.5 vs 310.5 (1.41x) | no |
| (b) any half-round-2 abstention reason > 1% of rounds 2/14 | worst 0.84% of 6,183 rounds; reasons {'final_round': 52, 'surrender': 15}; new bonus reasons fired: none | no |
| (c) any parity or validation failure | none | no |

## The difference, corpus-wide

At C = 1, rc2's frozen weight. Every econ point scales linearly with C: multiply by 3.871 for the owner's C.

Gross econ points per scored round (credit earned + debit charged, both teams):

| round | scored | 30/80 | bonus | change |
|---:|---:|---:|---:|---:|
| 2 | 3,124 | 103.6 | 386.4 | +282.8 |
| 3 | 3,124 | 312.1 | 312.1 | +0.0 |
| 4 | 3,124 | 309.8 | 309.8 | +0.0 |
| 5 | 3,120 | 273.2 | 273.2 | +0.0 |
| 6 | 3,117 | 252.0 | 252.0 | +0.0 |
| 7 | 3,114 | 251.6 | 251.6 | +0.0 |
| 8 | 3,113 | 257.9 | 257.9 | +0.0 |
| 9 | 3,109 | 262.7 | 262.7 | +0.0 |
| 10 | 3,105 | 267.7 | 267.7 | +0.0 |
| 11 | 3,099 | 245.1 | 245.1 | +0.0 |
| 14 | 3,059 | 171.6 | 489.7 | +318.1 |
| 15 | 3,007 | 300.5 | 300.5 | +0.0 |
| 16 | 2,930 | 319.7 | 319.7 | +0.0 |
| 17 | 2,813 | 263.4 | 263.4 | +0.0 |
| 18 | 2,618 | 246.4 | 246.4 | +0.0 |
| 19 | 2,393 | 238.1 | 238.1 | +0.0 |
| 20 | 2,128 | 231.5 | 231.5 | +0.0 |
| 21 | 1,813 | 225.5 | 225.5 | +0.0 |
| 22 | 1,508 | 205.3 | 205.3 | +0.0 |
| 23 | 1,149 | 181.2 | 181.2 | +0.0 |

Rounds 2/14 combined: 137.2 -> 437.5 points per round. Rounds 3/4/15/16: 310.5 under 30/80 and
310.5 under the bonus model. Rounds 2 and 14 move from the quietest econ rounds to the loudest, at
1.41x the rounds-3/4/15/16 level.

Bonus evidence over 6,116 pistol-winner team-rounds (5,047 won by the pistol winner):

- qualifying deaths 10,181; denied 26,369,000 credits, net 25,754,950; recovery netted 2.33%
- survivors 16,536: credit evidence 807, kill-feed in round 92,
  kill-feed carried 198, both 69
- unidentified-weapon flags 4

Player-round econ change: 640,832 of 659,290 rows unchanged; min -206, p1 -78, p99 75, max 553.

Player-match Impact change: mean +4.0, SD 89.7, p1 -195, p5 -118, p50 0, p95 168,
p99 307, min -296, max 702; 15,757 of 31,240 player-matches unchanged.
Within-match rank changes: 2,556 of 31,240.
Leaderboard (104 players with >= 20 matches): Spearman 0.9997,
top-20 overlap 20/20, largest rank move 3 places.

Concrete rounds: `bonus-round-examples.md` (3120 round 2: the pistol winner's round econ -70 -> -591, the pistol
loser's 96 -> 746).

## Files

`candidate-manifest.json`, `model-comparison.json`, `match-3104-site.md`, `fixed-ten-site.md`,
`match-3120-site.md`, `trace-3120-site.md`, `rc2-parity.md`, `defect-reinstatement.md`, `bonus-round-examples.md`.

## Not changed, still open

- The weights are rc2's; the owner's four-term A/D/B/C set (C = 3.871) is a separate decision.
- The stored `spentCredits` table is populated only by future ingests and read by nothing.
- The 30%/80% cliff and the saturating activation outside rounds 2/14 are untouched.
