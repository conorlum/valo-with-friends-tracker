# Impact rc3 — review summary

Candidate `impact-rc3`, frozen at `82d8e6b`, manifest LF-SHA-256
`8e5c637b34b2ccb767fe2d16b18017eb8571f158623653c8f9bc4bf2ae10e20c`.

**Status: frozen and reviewed, NOT active.** `ACTIVE_MANIFEST` is None, `IMPACT_CALCULATION_VERSION` is 2 on the
branch and 1 on `main`, production is at alembic 0007 with no gate, and every stored score is still v1.

This answers the nine review checks declared in `../2026-09-07-predeclared-values.md` ("rc3: the owner's lock after
measurement"). Five are answered here in full; three are answered for everything that can be checked before the
rehearsal, and say what remains; check 9 is the rehearsal itself, which comes next.

The measurements are in the ledger's RESULT entry of 2026-09-17, not repeated here.

| check | verdict |
|---|---|
| 1 source fingerprints | **pass** for the 13 declared matches; the whole-cohort half is a Stage 6/8 step |
| 2 manifest identity | **pass** |
| 3 the chain | **pass** — see below for K4 |
| 4 decomposition | **pass**, 20,022 checks, 0 mismatches |
| 5 the reviews reconcile | **pass**, 7,166 checks across four reports |
| 6 defects reinstated | **pass**, all twelve, on values |
| 7 approved results | **pass** for coverage and field list; the comparison against the built table is a Stage 6/8 step |
| 8 the full suite | **pass**, with one declared deviation about which database |
| 9 the rehearsal | **not yet run** |

---

## 1. Source fingerprints

`match_source_fingerprint` for each of the 13 declared matches — 3104, the fixed ten (3129, 3130, 3131, 3113, 3118,
3121, 3114, 3115, 3116, 3117), 3120 and 3133 — was taken inside one repeatable-read snapshot at the freeze and is
recorded in the manifest. `review-results.json` carries the same fingerprint for every match, and they agree with the
manifest's, so the rows reviewed are the rows frozen.

The whole-cohort fingerprint over all 3,125 matches, `2c31fbbd9b1507f32631d304dec86885f5b876440a3e31140f4c1769545bdc5f`,
is carried by every chain artifact. **Remaining:** that it is unchanged between the export and the load is checked by
`verify-build` at the rehearsal and again at activation, because only then does a load exist.

## 2. Manifest identity

`verify_manifest` passes against the running code. The release comparator is the declared `impact_rc3`, and its values
are exact: `enable_trade_credit: true`, `trade_credit_scale: 1.0`, A/B/C/D = 1.0/2.5/2.5/100.0,
`econ_model: buy_disruption_v2_30_80_bonus_denial`, `use_realized_swing: true`, both timing candidates false,
`activation_impact_calculation_version: 3`.

A manifest that merely *loses* `enable_trade_credit` is rejected rather than read as credit off — the reason rc3 is
declared in code (`test_a_manifest_that_loses_the_credit_flag_is_rejected`, and defect 1 in
`defect-reinstatement.md`).

The manifest also pins the trade schedules, the calculator constants, the agent allowances and behavioural digests of
every scoring source, plus the environment the ingestion preflight will require: Python 3.13.15 and 41 packages.

## 3. The chain

| link | code | configuration | result |
|---|---|---|---|
| K1' (3.11), K2' (3.13) | `b501ae2`, whose `webapp/` tree is commit A | explicit locked weights | equal, byte for byte |
| K3, OFF(B) | commit B `5c369f8` | the `impact_rc3` comparator | K3 = K1', OFF(B) = OFF(A') |
| K4 | the freeze's code | **the frozen manifest**, by digest | **K4 = K1'** |

Comparison hash: `7e5ff2789ed1ea3a61425e1a6138576bc250cddc5de4e63cb3740a6d9f42f1bb` (ON) and
`648df0403a0d48411dfd42c5b7697da1d6952de416e3a1a36bc97fc628460eac` (OFF).

**K4 closes the chain.** Scored on 2026-09-17 through `config_from_manifest` on the frozen file — the sidecar records
`kind: manifest` with LF-SHA-256 `8e5c637b…`, a clean checkout, and the same weights and credit flag — it reproduces
K1' byte for byte: 659,500 rows, 53,437,561 bytes, and the same per-match fingerprint for all 3,125 matches. So the
frozen manifest, read back and applied, is the scoring the owner approved, and not merely a description of it.

That export ran at `c07d011` rather than at the freeze commit `82d8e6b`; the diff between them over `app/scoring`,
`app/models` and the exporter is empty, and the manifest verifies against the running code or the export refuses.
An earlier attempt died two-thirds through when the machine slept under its 30-minute snapshot; exports write
nothing, and the re-run is the one recorded here.

Row-level assertions over all 659,500 player-rounds, from the measurements and the decomposition check: impact equals
its four terms on every row; zero negative-damage rows; every smallint column's largest absolute value is 2,397 or
below, far inside the type; `trade_credit`'s largest is 981.

Zero input failures: every one of the 3,125 matches scored in every export, with no match skipped or refused.

## 4. Decomposition

`compare_rc3_decomposition.py` rebuilt every term of the 13 declared matches from the sources, independently of the
scorer's own trade-credit function: **20,022 checks, 0 mismatches** (`rc3-decomposition.md`). Raw econ equals the rc2
configuration's raw econ; `C*econ`, `B*leverage`, `D*assists` and the persisted `trade_credit` each equal their weight
times the rebuilt raw value after the scorer's rounding; damage is independent of the credit; and with the credit off,
no credit is paid.

## 5. The reviews reconcile

Four reports, all against the rehearsal database restored from production: `match-3104-site.md` (570 checks),
`fixed-ten-site.md` (5,140), `trace-3120-site.md` (958) and `match-3133-site.md` (498). Every reconciliation passes,
under the four-term identity wherever a total is shown.

"Before" is **what production stores today**, not a replay. The legacy replay appears as its own column, and for match
3104 it differs from the stored value for 10 of 10 players — which is why the distinction matters. Match 3133's
players read `n/a (unscored)`, since production holds no rows for that match.

`scoring_version` is compared explicitly rather than ignored: `verify-build` asserts it is 3 on every built row, and
reports the review-time versions the approved results carry, instead of silently dropping the field.

## 6. Defects reinstated

All twelve, each caught on a value. `defect-reinstatement.md` records the mutation and the test for each. Two of them
had no coverage until 2026-09-17: the leverage weight applied to the econ term (invisible while B = C = 2.5, so it is
now tested with unequal weights) and D dropped from `impact` (still computed and stored, so only the identity notices).

Items 11 and 12 — a real pre-0010 checkout and a real ingestion run — are proven by tests today and repeated against a
real `origin/main` worktree and the real ingestion path in the rehearsal's gate probes.

## 7. Approved results

`review-results.json` names the frozen manifest by digest, is for candidate `impact-rc3` and comparator `impact_rc3`,
and covers exactly the 13 matches the manifest declares, with 2,820 rows — every row the table holds for those matches,
including 3133's 210. Its field list is recorded in the file and validated on read against the model's own columns.

**Remaining:** the comparison against the built table happens in `verify-build`, at the rehearsal and again before the
swap at activation. Since 2026-09-17 that check also refuses an approval that covers no matches, misses rows, or names
another manifest.

## 8. The full suite

At the reviewed code: **1,251 passed, 63 skipped, 0 failed** under Python 3.11 offline, and **1,301 passed, 13
skipped, 0 failed** under Python 3.13 with the database tests enabled. No failure is tolerated or explained away.

The 13 skips are analysis tests that measure the real corpus (stage C0, leverage gates, ex-ante swing,
reconstruction), which skip when the database they are pointed at has no matches.

**Declared deviation, now narrowed (2026-09-17, `1e0b722`).** The declaration says the database tests run against the
rehearsal database. At the reviewed code they ran against `valo_rc3_test`, a schema-only scratch database, because
some of them empty the tables they use — which would quietly invalidate a rehearsal restored from production.

That was true but too broad, and it cost coverage rather than merely deviating: a scratch database holds no matches,
so the 13 corpus-measuring tests skipped, and ran nowhere at all. Twelve of the thirteen only read, and Stage 6 runs
them against the restore, which is what the declaration asked for.

The thirteenth genuinely cannot. `test_wrapper_still_persists_and_commits` calls the scorer's committing wrapper on a
real match; the wrapper UPDATES in place, so on the rehearsal database it would rescore a match without changing any
row count — no failure, no trace, and every later measurement quietly wrong. It keeps `valo_rc3_test`.

The guard was widened to enforce that line: `writes=True` now joins `empties_tables=True`, and both are held to a
`*_test` database. Until this change a test that merely wrote was allowed on the restore. Plan v2 section 7 records it.

## 9. The rehearsal

**Not yet run.** It is Stage 6 of the plan and the next step after this review is approved: migrations and the gate
install, the forward path (export, build, verify, swap, verify-live), the three gate probes, page loads that miss the
cache during a forward and a rollback swap with the longest stall recorded, the rollback path and an interrupted swap,
prewarm coverage including cache-blob validity, the PR #67 page review, and the PR #67 rollback path, with every
duration recorded.

Already measured while building the rehearsal database: the backup is 31 MB and takes 33 s, the restore 3 m 13 s,
migrations 0007 to 0010 **3.5 s**, and the gate install under a second.

---

## What the owner is approving

That rc3 — as frozen, measured and reviewed above — is the scoring to rehearse and then activate. Approval does not
touch production: the first production write is Stage 7, after the rehearsal, and each of its steps is asked for
separately.
