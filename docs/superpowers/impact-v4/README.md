# Impact v4 — release runbook

Status (2026-09-21): **phases A–E done on the branch; F (freeze and review) is next and needs production reads.**
Nothing is merged, deployed or frozen. Production runs rc3 (`IMPACT_CALCULATION_VERSION` 3, `ACTIVE_MANIFEST` rc3).

- Process: `../SCORING-RELEASE-PROCESS.md`. This file is v4's instance of it.
- Plan (the spec): `../plans/2026-09-21-impact-v4-no-time-factor-plan.md` (r5).
- Ledger (`../2026-09-07-predeclared-values.md`): the CORRECTION, DECLARATION 12 and RESULT, DECLARATION 13 with its
  ADDENDUM, and the RESULT for declaration 13 with its ADDENDUM.

**What ships:** no time factor — T = 1 for every kill and death, and T = 0 once the round is decided
(`enable_decided_only_time`). Assists on kills made after the round was decided are not paid
(`remove_post_decided_assists`). Both flags are on in `impact_v4`, and both default off everywhere else.

| | value |
|---|---|
| candidate id / comparator / activation version | `impact-v4` / `impact_v4` (diagnostic `impact_v4_n`) / **4** |
| branch / implementation tip | `impact-v4-implementation` @ `7f6c0ed` (not pushed) |
| reference commit / worktree | `c470670` on `v4-reference-f96aee9`, at `../vwft-v4-reference` |
| manifest | `impact-v4/candidate-manifest.json` — **not yet frozen** |
| retained / rolled-back table / admin identity | `impact_scores_v3` / `impact_scores_v4_rolled_back` / `v4-runbook` |
| artifacts | `~/Documents/valo-backups/v4-release/` |
| measurement corpus | local `valo_v4` (PG18, port 5434, 3,198 matches); scratch `valo_v4_test` |
| pre-release production `main` SHA | recorded at H0 |

## Phase checklist

| phase | state | evidence |
|---|---|---|
| A decide | done | DECLARATION 12, `cecd1c5` |
| B measure | done | RESULT declaration 12, `f96aee9`. `N vs P0` improves on both targets; stop rule cleared |
| C plan | done | plan r5, `4ecf7f0`, after four external reviews (R1–R11, S1–S6, T1–T5, U1–U3) |
| D implement | done | `71ad905` `de1494f` `2bfff71` `c8f1ce3` `8b72d0d` `af8d3d1` `edcec2a` `a9140fb` `27daaf2` |
| E prove | done | DECLARATION 13 `82ee277`; reference hashes `97ed0b0`; comparison tool `0003010`; RESULT `852dc49`; review fixes `9bb5618` `6d00d08`; addendum `7f6c0ed`. Seven comparisons, 0 rows differing over 674,530 rows each, both modes, byte-identical, and the hardened checker exits 0 |
| F freeze & review | **next** | needs production reads and gate G3/G4 |
| G rehearse | not started | |
| H activate | not started | |
| I hold & reopen | not started | |
| J close out | not started | |

## F. Freeze and review — what is still to be decided

- **Review cohort.** rc3's ten (`3129,3130,3131,3113,3118,3121,3114,3115,3116,3117`), plus `3104,3120,3133`, plus
  three matches **chosen by query against production and recorded**:
  - one with an assist on a kill after the round was decided that is demonstrably removed;
  - one with a kill after a defuse;
  - one Time Win round with a kill after 100 s.
- **Freeze declaration** in the ledger, before freezing. It holds:
  - the cohort;
  - the K-chain: K3 = K4 rehearsal-grade, **K4 = K5 binding in the window**;
  - the production row-motion tolerance, around the measured 32.1% of rows and 72.0% of matches reordered (rc3 live);
  - the wording "below both carried floors" for the untestable `N+A vs N` (R5.7).
- **Owner's last look.** 72% of matches reorder, so compare several friend-group matches on the site before
  approving.

## Swap-tool flags for this release

Rc3's runbook used defaults that no longer exist. Every swap-tool command in this release passes:

```bash
S="--expect-database <db> --identity v4-runbook --previous-table impact_scores_v3 --rolled-back-table impact_scores_v4_rolled_back"
# verify-build / verify-live additionally:  --expect-scoring-version 4
```

Equivalence checker: `scripts/compare_v4_reference.py --reference DIR --out DIR --expect-scoring-version 3` (3 on
the branch, where there is no bump). It exits 1 on any difference.

## Commands for this release

To be written from the rehearsal (process §G5), in the order the window runs them.

## Durations measured in rehearsal

None yet.

## Known state on the branch

- **15 tests fail on the branch, all from one cause.** Fixtures resolve the repository's rc3 manifest, whose source
  digests no longer match this code. The same 15 fail on `origin/main` under Python 3.11. They go green at
  activation. Owner decision, 2026-09-21: **leave them red and documented**.
- **2 tests fail as they do on `origin/main`**: `test_default_persistence_path_is_unchanged_legacy` and
  `test_runtime_default_is_the_live_legacy_formula`.
- **The plan contradicts the code in two places.** The code and the ledger are right:
  - §2.6's statement that `webapp/app` is identical at `f96aee9` and on `origin/main` stopped being literally true
    at PR #70. Only the tracker.gg adapter differs, and every scoring file is identical.
  - The plan's swap commands assume rc3's defaults, which no longer exist.

## Decisions the owner made

| date | decision |
|---|---|
| 2026-09-21 | Ship `N` and `N+A` per declaration 12's stop rule. `N+A` ships on concept; it is untestable |
| 2026-09-21 | Leave the 15 active-manifest test failures red and documented until activation |
| 2026-09-21 | Leave rc3's runbook commands as rc3's record; this runbook carries the new flags |
