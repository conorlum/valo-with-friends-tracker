# Buy-disruption 30%/80% candidate: implementation and review summary

Date 2026-09-11. Branch `impact-scoring-impl`. **Not activated, not rescored, not
deployed, not pushed.** `ACTIVE_MANIFEST` is `None` and `IMPACT_CALCULATION_VERSION`
is 2, so the site still scores with the live legacy formula. Activation and
rollback steps: [README](README.md).

Commits: `61737f7` implementation and tests; `827ef4c` frozen manifest,
runbook and ledger declaration (before any integrated review); a final commit
adds these review artifacts.

## What was built

| Piece | Where |
|---|---|
| Production calculator: V2 kill credit, 30%/80% death debit, historical wealth debit as a named comparator, typed audit, named abstentions | `webapp/app/scoring/econ_buy_disruption.py` |
| Scorer integration: `econ_model` selection, early input validation and routing, event IDs, swing bypass, C once / round once | `webapp/app/scoring/impact.py` |
| One explicit configuration; frozen manifest with verification; the single runtime switch (off) | `impact_config.py`, `impact_manifest.py`, `impact_runtime.py` |
| Maintenance-window backfill with interruption recovery | `webapp/scripts/backfill_impact_candidate.py` |
| Review tool frozen mode: named comparators, reconciled match/penalty/trace/ten/corpus views, acceptance results | `webapp/scripts/release_candidate_review.py` |

Nothing in production imports `docs/`. Review, backfill and runtime all call
the same calculator through `build_impact_rows_for_match`.

## The four plan-review requirements

1. **Validate and route before legacy preprocessing.** `_combat_input_issues`
   runs before the trade, econ-differential and kill-order loops. A known victim
   with a NULL killer is routed as a death on the victim's own side (the existing
   self-kill policy): their team loses a player, they are charged the death, no
   kill is credited, and the econ ledger debits their first kit without enemy
   credit. An unknown victim or missing participant stats raise a structured
   `ImpactInputError` naming the round, event and player, before any row is built
   or persisted. A missing bystander stat or invalid next-round economy abstains
   econ only. `KillEvent.id` travels on every kill dict and every audit event in
   `(event_time_seconds, id)` order.
2. **Local maintenance-window backfill with recovery.** It refuses without
   confirmation, with other DB sessions connected, or unless the manifest is the
   active runtime configuration. It records the declared match set and
   attempted/succeeded/failed IDs after every match, and resumes. It refuses a
   changed manifest or match set, and clears caches at start and on every exit.
   It accepts only when every persisted row equals a fresh frozen replay and the
   approved review results.
3. **Distinct comparators.** `live_legacy` (replay, and persisted values shown
   separately), `separate_econ_legacy` (diagnostic), `buy_disruption_v2_wealth`,
   `buy_disruption_v2_30_80`. The penalty view reconciles identical gross credits
   and non-econ fields; the site view is labelled the total release change.
4. **Freeze before review.** The manifest pins comparators, weights, scale,
   calculator constants, trade schedule, allowance table, timing flags (off),
   AST digests of every scoring source, the Python version, and source-row
   fingerprints for all 12 reviewed matches. It was committed before any
   integrated run, and every run verified it first.

## Results

### Abyss 3104 through the integrated scorer

Every player's econ total equals the frozen artifact: Osmin +708, DoubleBl1nd
+277, VorteXx +209, Helpless +196, ZETA +194, ternstyle +148, NPrightdolphin +111,
Najumi +49, Mokalover +4, 1xgoofy **-101**; TEAM_1 +502, TEAM_2 +1293. The wealth
comparator reproduces -100 ... -928 (TEAM_1 -4057, TEAM_2 -2537), with gross
credits identical to 30/80.

Full Impact, live legacy -> candidate ([site report](match-3104-site.md)): the
biggest gains are ZETA +721, Osmin +441 and Helpless +390; NPrightdolphin -144 and
ternstyle -100 fall. Only Osmin and NPrightdolphin swap ranks (3<->2). Penalty
only, wealth -> 30/80 ([penalty report](match-3104-penalty.md)): every player
+652 to +957, no rank change. [Per-kill trace](trace-3104-site.md): R6 TEAM_1 is
constrained (D=3,650, pool 9,171.79, 80%), and Osmin's kill on 1xgoofy credits
124.04 against a 99.23 debit. R3 shows three absorbed deaths per team at 30%.

### Fixed ten ([site](fixed-ten-site.md), [penalty](fixed-ten-penalty.md))

- **Site (total release change).** Mean full-Impact change is +157.7 per
  player-match (median +137, SD 234.9, range -280 to +899). 25 of 100 players
  change rank within their match.
- **Econ.** C*econ per player-match averages +152.9 (range -245 to +789) and is
  negative for 23 of 100.
- **Rate split.** Of team-rounds with lost equipment, 296 are absorbed (30%) and
  37 constrained (80%).
- **Penalty only.** Wealth -> 30/80 adds +679.9 on average; 16 rank changes.

### Pistol-winner-loses histories

- **Match 3116 R14** is in the ten ([trace](trace-3116-site.md)).
  - Pistol winner TEAM_2 loses round 14.
  - Its carryover targets hold at H=15,800 against funding U=14,800.
  - The next buys are 950-1,300 each, so D=1,000 and the severity pool is
    2,589.74: a constrained 80% round.
- **Match 3120 R2** was selected separately from outcomes only
  ([report](match-3120-site.md), [trace](trace-3120-site.md)):
  - TEAM_1 won the pistol and lost round 2.
  - Its carryover targets stay at the round-2 kits (H=16,350) instead of dropping.
  - Its next buy is 800/700/1,000/1,850/1,900 against a pooled bank of 10,000.
  - That leaves a 100-credit funding gap, so activation is only 0.0256 and the
    severity pool 258.97. Each disruption credit is therefore about 3 points.
  - But the positive pool flips all five TEAM_1 death debits to **80%**.

### Read-only corpus audit ([JSON](corpus-audit.json), [page](corpus-audit.md))

This was run as declared, on all 3,124 local matches in one repeatable-read
snapshot.

- **Coverage.** Every match scored: 659,290 player-rounds, **0** input-validation
  failures, **0** identity mismatches. The identities checked were econ row vs
  calculator, the impact identity, gross credit wealth vs 30/80, and every non-econ
  field wealth vs 30/80.
- **Econ rounds.** 52,111 scored; 10,545 pistol/half/OT; 3,124 final; 149 surrender.
- **Events in scored rounds.** 382,988 enemy, 2,546 self, 70 team. There are no
  environmental/NULL-killer events in this corpus.
- **Team-rounds with lost equipment.** 86,194 at 30%, 13,817 at 80% (13.8%).
- **Scored-round team net signs.** 28,737 both positive, 23,370 mixed, **0 both
  negative**, 4 both zero.
- **C*econ per player-round.** Mean +7.08, SD 35.47, p1 -79, p50 0, p99 +140,
  range -215 to +959. 37.0% negative, 28.7% zero, 34.3% positive.
- **C*econ per player-match.** Mean +149.4, SD 187.5, p5 -115, p50 +128,
  p95 +485, range -486 to +1,469. 19.8% negative.
- **Full Impact vs the live-legacy replay.**
  - Per player-match: mean +163.3, SD 222.4, p5 -155, p95 +562, range -618 to
    +1,608.
  - 19.0% of players change rank within their match.
- **Leaderboard** (104 players with at least 20 matches, average Impact per
  played round): Spearman 0.998, top-20 overlap 20/20, largest move 7 places.
- **Persisted rows.** Persisted totals differ from the live replay for 30,434 of
  31,240 player-matches, because stored rows predate version 2.

These are descriptive. No weight, scale or constant was changed from them.

## Verification

- **Tests.** 1009 passed. The 2 failures are the known drift guard
  (`test_builder_matches_stored_values`, red by design until the site is
  rescored) and the pre-existing `test_site_stats_cache` failure. Baseline before
  this work was 889 passed with the same two failures. 120 tests are new.
- **Numerical failures before fixes.**
  - The calculator tests against a null-bodied API gave 49 value failures.
  - The integration tests before routing reproduced `KeyError: None` on an
    environmental death in both the legacy and candidate paths.
  - Before routing, candidate econ came out as the legacy allocator's values,
    e.g. 756 instead of 222.
  - Abyss round 2 gave 0 instead of -16.
- **Defects reinstated behind the same API.** All 37 were detected; most by
  value assertions, the crash defects by the reinstated exception
  ([record](defect-reinstatement.md)).
- **Abyss parity.** All 200 eligible player-rounds and 152 events for both
  artifacts match, pure and through the build path. Rounded nets are exact,
  unrounded values within 1e-12 relative, and event IDs match. The frozen
  artifacts are pinned by LF-normalized SHA-256.
- **Legacy preservation.** The routed scorer against HEAD over all 3,124 matches
  shows 0 differing rows of 659,290, in each of live legacy, ex-ante, separate
  econ and arm 4.
- **Crash smoke.** All 3,124 matches under both buy-disruption models raised no
  exceptions.
- **Reconciliation.** Every integrated run passes, with zero errors:

  | Run | Checks |
  |---|---:|
  | Abyss 3104 site | 570 |
  | Abyss 3104 penalty | 5,195 |
  | Abyss 3104 trace | 580 |
  | Fixed ten site | 5,140 |
  | Fixed ten penalty | 46,877 |
  | 3120 match and trace | 958 |

## Remaining findings

1. **The 30%/80% step fires on tiny gaps in real play** (3120 R2, D=100). This is
   retained owner policy, not smoothed; the owner may want to look at it.
2. **Positive aggregate econ is inherent.** It averages +149 per player-match
   across the corpus and +153 in the ten. It shifts players up and is not
   zero-centered; no re-anchoring was done. Individual player-matches are still
   negative 19.8% of the time.
3. **The econ spread no longer matches its scale anchor.** ECON_SCALE 1007.9209
   was anchored so econ's SD matched time_impact's (179). Under this candidate,
   player-round C*econ has SD 35.5, so econ is far narrower than that anchor
   implied, and the leaderboard barely moves (Spearman 0.998). A scale or C
   proposal is a separate owner decision, shown against these reviewed values.
4. **Round Win Impact ignores econ.** Under the new structure, `kill_impact` and
   `death_impact` exclude econ, while `impact` includes it. The match page's
   "Round Win Impact" is computed from kill/death impact, so it ignores econ
   entirely. This is pre-existing new-structure behaviour and was not changed.
5. **Legacy team-kill handling is wrong in combat state** (pre-existing,
   unchanged). A non-self team kill decrements the *opposing* team's alive count
   and credits the killer kill-order bonus. The corpus has 122 such events.
   Changing it alters combat scoring and needs its own decision.
6. **Persisted site values are stale.** They predate `IMPACT_CALCULATION_VERSION`
   2 and differ from the live replay for every reviewed player. Activation's
   backfill resolves this; until then the drift guard stays red.
7. **Timing candidates are excluded.** Post-plant leverage and the pre-plant
   curve are OFF in every comparator. Shipping them needs their table artifacts
   frozen, which manifest v1 deliberately rejects.
8. **Current-round bank is not validated.** The reference walkthrough also
   validated the current round's bank, which the formula never reads. Production
   abstains only on inputs it uses.
9. **Not in scope, still open.** `TRADE_COST_SCHEDULE` is recorded in the
   manifest but not yet in the predeclared-values ledger (handoff item 2).
   `webapp/econ_issue_prompt.md` remains untracked and untouched.
