# External review of the rc3 plan — Astra (ChatGPT agent), 2026-09-16

Recorded verbatim as delivered to the owner. Astra reviewed read-only: the working copy at `7f4a63b` with the
uncommitted changes, plus the review bundle. It made no database connections, test runs, checkout changes or GitHub
writes. Plan line numbers cited below refer to `2026-09-16-rc3-freeze-and-ship.md` as it stood when reviewed.

The main session checked A4, A6, A7 and A10 against the code and found all four correct. Dispositions for every
finding are in `2026-09-16-rc3-ship-plan-v2.md`, section 4.

---

1. Verdict
NOT READY.
- Rollback can leave impact_scores empty: K1 commits the truncation before attempting the restore. See A1.
- Rollback deployment is not established: K1/K2 require deployment while suspended, despite the plan claiming otherwise. See A2.
- The ingestion preflight admits stale scoring: matching migrations and a non-raising configuration lookup do not establish rc3 activation. See A3.
Reviewed read-only; no database connections, application/test execution, checkout changes, or GitHub writes. The ZIP's documents match the working copy. Code citations below use pinned commit 7f4a63b0ac853236ec1a866e15caf55f2136e9d2, unless explicitly marked main@54c3e56; bundle: citations refer to the ZIP.
2. Findings
A1 — P0 — K1: truncation and restoration are not atomic.
Problem: The restore's transaction cannot undo the preceding, separately committed truncation.
Evidence: bundle:docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md:540 executes "TRUNCATE impact_scores, player_view_cache, site_stats_cache" through psql; line 541 starts a separate pg_restore --single-transaction. Neither command is followed by an exit-code gate.
Failure scenario: The dump path is wrong, restoration fails, or the connection drops. Restoration rolls back, but the production score table remains empty. A successful subsequent count query can obscure the failed command.
Fix: Validate the archive and target first. Restore into staging and validate it before replacing scores in one transaction, or execute truncation and restoration within one demonstrably shared transaction. Stop on every native-command failure; require successful data verification before reverting or reopening.
A2 — P0 — K1/K2: the rollback depends on unverified suspended-service deployment behavior.
Evidence: The plan says "Suspend the site" at bundle:docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md:537, then "push; Render deploys" at line 546, then "Resume" at line 547. K2 repeats this dependency at lines 552–556. Line 682 nevertheless claims "nothing depends on deploying while suspended."
Render documents suspended services as a reason deploy hooks return 409 Conflict. That establishes a real restriction, although it does not settle every automatic-deployment or resume path. Render deploy-hook documentation
Failure scenario: After K2 drops the columns, the revert has not deployed. Resuming the previous application exposes code expecting the removed schema. In K1, resumed activation code can instead regenerate caches under the wrong scoring version.
Fix: Establish and rehearse an exact rollback deployment sequence, with traffic blocked independently until the intended commit and schema are verified. UNVERIFIED: which commit this service would resume and whether a suspended push would deploy automatically. Do not make either assumption a recovery dependency.
A3 — P0 — C10, J4, K1: the preflight neither enforces activation nor covers every initial write.
Evidence:
- C10 requires only that active_scoring_config() "resolves without raising": bundle:docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md:257–260.
- webapp/app/scoring/impact_runtime.py:55: "return None if ACTIVE_MANIFEST is None else _verified()[1]".
- webapp/scripts/ingest_trackergg_player.py:48: "dirty = backfill_unscored_matches(db)".
- webapp/app/adapters/trackergg_browserstate_source.py:479–482: cache invalidation, "db.commit()", then scoring.
- webapp/app/scoring/impact.py:98–102 documents the changed legacy behavior and sets "IMPACT_CALCULATION_VERSION = 2".
Failure scenario: A machine remains on the post-H, pre-activation checkout. Its migration head matches production, its model query succeeds, and None passes the proposed configuration check. It writes v2 legacy scores among rc3 rows. Following K1, that same scorer writes v2 rows among restored v1 rows. Separately, the single-player script can reach stranded-match cache writes before either proposed preflight location.
Fix: Put a shared preflight before backfill_unscored_matches in both entry points and before direct ingestion writes. Require the expected active manifest, version 3, and approved configuration identity—not merely a non-raising lookup. Explicitly preserve the ingestion freeze after K1. Account for every ingestion machine; new checks cannot constrain old checkouts that lack them.
A4 — P0 — I5: "exit 3 = nothing written" is false after startup.
Evidence:
- Plan: bundle:docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md:464: "3 refused, nothing written".
- webapp/scripts/backfill_impact_candidate.py:190–196 checks sessions during the loop and raises "BackfillRefused" after recording an interruption.
- Lines 200–205 call the writer and record successful matches.
- Lines 274–276 convert every BackfillRefused into "return 3".
Failure scenario: A session appears after 25 committed matches. Exit 3 leaves mixed scores, contrary to the operational interpretation in I5.
The Ctrl+C description is also overstated: lines 199–203 catch Exception, not KeyboardInterrupt; the CLI does not normalize Ctrl+C into exit 2 or reliably record an interrupted status.
Fix: Distinguish refusal before writes from interruption after writes. Treat every nonzero or abnormal exit after startup as potentially partial. Preserve idempotent resume, but explicitly handle interruption and test interruption after commit, before state-file replacement, and during acceptance.
A5 — P0 — H4/H5: a failed bounded migration can become an unbounded deployment migration.
Evidence:
- bundle:docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md:401–404 sets lock_timeout, runs Alembic, removes the setting, and queries the revision without checking the migration exit code.
- render.yaml:10: "alembic upgrade head".
- webapp/alembic/versions/0008_econ_component_and_kill_order_bonus.py:33–34 incorrectly describes a "SHARE lock".
ADD COLUMN ordinarily requires ACCESS EXCLUSIVE. lock_timeout bounds waiting for locks, not the duration for which an acquired lock remains held. PostgreSQL ALTER TABLE, timeout documentation
Failure scenario: H4 times out; the operator proceeds to H5. Render retries unapplied migrations without H4's timeout, potentially blocking live requests.
Fix: Make H4 exit status and exact revision 0010 mandatory merge gates. Restore environment variables in finally; establish bounded migration execution and failure handling for the deployment path too. Correct the lock description and rehearse contention.
A6 — P1 — C6/F2: the prescribed review-tool repair misses additional failing identities.
Evidence:
- C6 specifically names the identity at line 584: bundle:docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md:238–241.
- webapp/scripts/release_candidate_review.py:698–699 also reconciles totals using only [r["damage"], r["leverage"], r["econ"]].
- Line 885 independently reconciles trace totals with [t["damage"], t["leverage"], t["econ"]].
- Lines 871–874 accumulate trace damage, leverage, economy, and impact, but no assists.
Failure scenario: Implementing C6's specified changes still leaves match, fixed-ten, and trace reports failing on assisted players. Trace tables continue displaying an incomplete decomposition.
Fix: Update every frozen-mode reconciliation, accumulator, equation, and table. Exercise all F2 commands with nonzero assists and trade credit. Credit must remain a disclosed part of leverage, not become a fifth additive term.
A7 — P1 — F2/F4: the report's "Before" column is not production's stored v1 score.
Evidence:
- Plan: bundle:docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md:342–343: "The 'site' (before) column is the stored v1 impact".
- webapp/scripts/release_candidate_review.py:665 computes both sides with "score_with".
- Lines 700–703 print l['impact'] as Before and stored.get(...) in a separate Persisted column.
- Lines 975–976 calculate changes from the two replays.
Failure scenario: Approval treats a v2-legacy-to-rc3 delta or ranking change as the actual v1-production-to-rc3 change.
Fix: Label the replay baseline explicitly and calculate deployment deltas and rankings from stored v1 values. Preserve the legacy replay as a separate diagnostic.
A8 — P1 — B/D/F3: the old "credit total" and the new persisted trade_credit are different measurements.
Evidence:
- bundle:local-analysis/report_on.py:36: "dl = [b - a for a, b in zip(...)]" for ON/OFF leverage.
- Lines 84–85 label and sum this as credit.
- webapp/app/scoring/impact.py:1446–1447 rounds combined leverage.
- Line 1506 separately computes "trade_credit=round(weights.leverage * trade_credit_x_time)".
- bundle:docs/superpowers/2026-09-07-predeclared-values.md:1777–1778 adds trade_credit to the checksum list.
Failure scenario: A correct persisted-credit sum differs from 18,204,413 because subtracting rounded totals is not equivalent to rounding credit separately. The replay is incorrectly rejected, or implementation changes are made merely to force equality.
Fix: Declare separate names and expectations for ON-minus-OFF leverage and persisted rounded credit. Report both totals, their difference, and separate touched-row definitions. UNVERIFIED: their actual difference on this corpus; the supplied measurement does not record the persisted-credit field.
A9 — P1 — B/F3: the supplied measurements cannot establish exact row-level reproduction.
Evidence:
- bundle:local-analysis/run_on.py:23 initializes arrays containing only damage, leverage, economy, assists, and impact.
- Lines 53–55 append values without row identifiers.
- webapp/app/scoring/impact.py:1086 loads player-round stats with .all() and no explicit ordering.
- bundle:local-analysis/report_on.py:35–38 compares arrays using zip.
- Lines 40–43 print identity failures rather than asserting their absence.
- The ledger's prediction at bundle:docs/superpowers/2026-09-07-predeclared-values.md:1781–1783 compares displayed figures.
Failure scenario: A changed query order produces false row differences; alternatively, offsetting score changes preserve sums and displayed shares and pass the stated reproduction prediction.
Fix: Preserve canonical results keyed by (round_id, match_player_id), with explicit field names and source fingerprints. Compare keys and values; make discrepancies fail the report. Retain displayed aggregates as secondary checks. If the original keyed baseline cannot be reconstructed, limit the claim to reproduction of the recorded aggregates.
A10 — P1 — C6/F2/C9: review-results metadata misdescribes its positional values.
Evidence:
- webapp/scripts/release_candidate_review.py:930 declares only six fields beginning "impact", "econ_component", "damage".
- Line 934 obtains rows through result_rows.
- webapp/scripts/backfill_impact_candidate.py:56,82 serializes impact.PERSISTED_FIELDS.
- webapp/app/scoring/impact.py:1541–1546 defines a longer list beginning "kill_impact", "death_impact", "impact".
- Backfill lines 93–101 compare positional rows without validating the declared field list.
Failure scenario: A reviewer or independent checker interprets the first array value as impact when it is kill impact. Adding trade_credit increases the inconsistency. Writer and acceptance code can also share the same accidentally incomplete field list.
Fix: Generate and validate the exact ordered field list, or use named values. Require the declared 13-match set and its fingerprints before writing. Add an independent persistence-contract assertion so removing trade_credit from the shared list cannot remove it from both implementation and acceptance unnoticed.
A11 — P1 — C8/F1: Python 3.13 and source hashes do not freeze the complete execution environment.
Evidence:
- webapp/app/scoring/impact_manifest.py:128 records only Python "major.minor".
- Lines 125–136 record no installed dependency versions.
- webapp/requirements.txt:1–16 pins direct requirements but supplies no complete transitive lock.
- render.yaml:8–9 upgrades pip and resolves dependencies again.
- C8 checks only a dirty webapp/app tree: bundle:docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md:247.
Failure scenario: Two Python 3.13 machines pass manifest verification with different resolved environments. A modified review or backfill script outside webapp/app can also run while the freeze script records HEAD as its provenance.
Fix: Record and reproduce the resolved dependency environment and exact interpreter build; verify the complete release/tooling tree is committed. Keep the arithmetic digest's intentional activation exclusions, but separately bind review/backfill tooling to its reviewed revision. UNVERIFIED: any actual dependency-induced score drift.
A12 — P2 — I5/Decision 4: the abort path switches to an unrehearsed production writer.
Evidence: bundle:docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md:469–470 says to switch methods after slow initial progress. Lines 635–637 describe the alternative as a new TRUNCATE/\copy operation "outside the script's safeguards." G3/G5 rehearse the batched writer and rollback, not this alternative.
Failure scenario: After partial production conversion, the operator must improvise export fields, transaction handling, acceptance, and state-file treatment during the outage.
Fix: Either implement and rehearse that alternative before the window, including interrupted-copy recovery, or make the abort action the rehearsed rollback. Set the cutoff from measured recovery time as well as throughput.
A13 — P2 — I7/J2: the prewarm success message proves only how many players were requested.
Evidence:
- webapp/app/services/player_view_cache.py:659–666 catches each player's exception, rolls back, logs it, and continues.
- bundle:docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md:481 then prints "prewarmed", len(ids).
- J2 checks cache versions, not roster coverage.
Failure scenario: Some or all roster prewarms fail, but the command exits successfully and reports all requested players. Reopening exposes expensive cold-page recomputations.
Fix: Verify both expected scopes for every roster ID at version 4003003003, including blob validity, and report actual successes and failures.
A14 — P2 — E2/G: the corrected remote rehearsal cannot pass the existing backup comparison literally.
Evidence:
- The corrected scratch name is valo_rc3_rehearsal: bundle:docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md:91–95.
- $COUNTS includes "current_database()" at line 292.
- Line 315 compares the entire production and scratch outputs and requires "no output = backup verified".
Failure scenario: A correct restore always differs in database name. Repeating the restore cannot resolve it; ignoring comparison output can conceal genuine differences.
Fix: Assert each target's database identity separately, then compare normalized, deterministically ordered data measurements. Make restore and comparison failures explicit gates.
3. Corrections and contradictions
Corrections 1–4
1. CONFIRMED, accepting the stated machine facts. The missing server installation is recorded at bundle:docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md:87–95: "initdb.exe fails" and the recommended Render scratch database. No production or installation recheck was performed. A14 remains necessary.
2. CONFIRMED. The scorer performs separate reads at webapp/app/scoring/impact.py:1073,1079,1086,1099–1103; writes additionally occur through the wrapper. A zero-latency rehearsal cannot measure the supplied Render latency. The precise window remains UNVERIFIED until rehearsal.
3. CONFIRMED. The draft explicitly states the ON output was "shown to the owner before this entry": bundle:docs/superpowers/2026-09-07-predeclared-values.md:1724–1728.
4. CONFIRMED. webapp/app/scoring/impact_manifest.py:39,54 hashes both "app/scoring/impact.py" and "app/models/impact_score.py"; impact.py:1541–1546 contains the persistence list. Both sides of the proposed column addition therefore belong before freezing.
The eleven "Contradictions with the brief"
Item    Assessment and evidence
1. Legacy scoring already differs    CONFIRMED, with a qualification. main@54c3e56:webapp/app/scoring/impact.py:617–618 adds the negative multikill adjustment; pinned branch impact.py:1417–1418 subtracts 50 * (scoring_kills - 1). However, "no production effect until merge" relies on the ingestion freeze: local checkouts can write production before deployment.
2. rc2 reproduction commands fail for rc3    CONFIRMED in substance. Review line 584 omits assists; comparison script webapp/scripts/compare_econ_models.py:80–82 demands equal non-economy fields. A6 identifies additional repairs beyond those listed.
3. rc2 also has interpreter/configuration incompatibilities    CONFIRMED for the stated 3.11 environment. impact_manifest.py:128,281–284 compares Python identity; lines 303–304 require exact declared comparator dictionaries. "Never on this machine" is too absolute once its interpreter changes; source/configuration incompatibilities remain.
4. Local mirror needs no installation    REFUTED by correction 1, as the plan already acknowledges: bundle plan line 90 says "Finding 6 and contradiction 4 are wrong".
5. Stock backfill is materially slower    CONFIRMED structurally; exact 23-hour duration remains an estimate. impact.py:1568–1581 performs a lookup for every calculated row and commits changes. The supplied latency is accepted.
6. .env was checked in    REFUTED. .gitignore:3 contains ".env"; bundle plan line 586 records it as untracked and never committed.
7. "Local test DB" is misleading and tests can commit    CONFIRMED. webapp/app/config.py:6 loads ".env"; test_impact_exante_swing.py:52,131 uses SessionLocal() and invokes the committing scorer. The Neon destination is accepted from the materials, not independently accessed.
8. Cache count changed    CONFIRMED as a supplied live fact. Bundle plan lines 124–125 record "4,479 → 4,482". Not rechecked.
9. Backfill includes 3133    CONFIRMED. backfill_impact_candidate.py:163 enumerates every match; impact.py:1574–1578 inserts absent scores. No existing score rows are required.
10. Merge changes more than migrations    CONFIRMED. player_graphs.py:33 sets state-diagram version 3; player_view_cache.py:119–123 incorporates it and impact version; impact.py:102 sets version 2.
11. Migration 0008 does not calculate the new values    CONFIRMED. Its lines 54 and 59 add default-zero columns and remove their server defaults; no scoring replay occurs.


Additional conclusions within the requested areas:
- Migration compatibility: old-main web reads can survive the additive schema while ingestion remains frozen. Old-main inserts cannot satisfy 0008's new non-null columns after defaults are removed. The pinned branch's mapped ImpactScore reads fail against 0007; tracker ingestion also requires 0009. The proposed 0010's exact compatibility is UNVERIFIED until written.
- K2's Alembic ordering is correct for a source-code revert: the reverted migration graph cannot resolve the newer database revision. That does not establish the suspended Render deployment sequence. Render artifact rollback is different: it skips the build step. Render rollback documentation
- PGOPTIONS: supported. Psycopg accepts libpq environment parameters, and PostgreSQL documents PGOPTIONS. Its live effective value remains untested. Psycopg connection documentation, PostgreSQL environment variables
- Comparator defaults: no findings with C2 correctly implemented. config_from_dict defaults missing credit to false, but the raw-dictionary identity comparison at impact_manifest.py:303–304 rejects missing keys for a declared rc3 comparator.
- scorer_revision reachability: merge commits preserve ancestry. The claim that squash/rebase necessarily makes the old commit immediately unreachable is REFUTED as an absolute if the original branch remains. The recommended merge method is still sound.
- Cache versions and capture order: no findings. The composite formula supports 4002003001 → 4003003002 → 4003003003; capturing IDs before deletion avoids the --cached-only trap. A13 concerns success verification.
- site_stats_cache scoring dependency: no findings. site_stats_cache.py:38–39 describes raw match/round/player inputs; the checked consumer does not read impact scores. The known-red fixture has three keys at test_site_stats_cache.py:24–28, versus nine required at site_stats_cache.py:370–380.
4. Ledger draft
- Disclosure is adequate; the heading is misleading. Line 1698 says "declared before scoring," while lines 1724–1728 correctly disclose prior scoring and inspection. Rename the uncommitted heading to identify a post-measurement decision and prospective replay declaration. Do not characterize the previous measurements as preregistered.
- The prediction is falsifiable at the displayed-aggregate level, but insufficient to prove identical scoring. Lines 1781–1783 specify shares to 0.1 point and exact totals/counts/extremes. A9 explains the stronger evidence needed. Define mean/SD precision and what happens after a discrepancy is "explained"; explanation alone is not successful reproduction.
- Separate the two credit definitions. The draft explains ON-minus-OFF rounding at lines 1735–1737, but later requests persisted-credit checksums. Apply A8 consistently.
- The "B and C swapped" mutation needs an explicit target. Lines 1703 and 1795 declare equal weights and require detection of a swap. Swapping rc3 constants is a no-op. Existing unequal-weight tests provide an appropriate implementation-level control: test_formula_weights.py:142 uses FormulaWeights(econ=2.0) and line 160 uses FormulaWeights(leverage=2.0).
- Rank definitions need to be attached to the declaration. bundle:local-analysis/report_on.py:104–105 breaks impact ties by player ID, whereas lines 147–148 use stable input order for impact-versus-K−D ranks. The latter K−D is computed from kill events at lines 131–135. Declare those choices explicitly.
- One quoted extreme lacks supporting output. Ledger line 1738 says "Largest per-round credit 981," but the bundled report output contains no credit maximum and its script does not print one. UNVERIFIED from the supplied report. Add its calculation and provenance.
- Resolve the measurement/freeze ordering. Ledger lines 1769–1770 require RESULT measurements through config_from_manifest, while Phase D precedes manifest creation in F1. Specify the immutable configuration artifact used for D, or place that measurement after manifest creation while retaining declaration-before-run.
- Preserve the original cohort after a freeze violation. Lines 1753–1755 allow retaking results over a larger corpus. Keep the original declared cohort's result and report additions separately; otherwise "exact reproduction" changes its population.
5. Missing steps
- Before H4: record the supported rollback deployment procedure and rehearse K2, not only K1. G5 currently specifies only "rollback K1" at bundle:docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md:386.
- Before C12: place the database-test guard before any fixture connection, and verify all four named fixtures refuse unintended destinations. The dangerous test actually commits at test_impact_exante_swing.py:131; removing DATABASE_URL alone still permits .env fallback.
- Before F1: complete 0010's migration/model/persistence contract, environment record, complete-tree cleanliness check, and the independent artifact-field checks in A10. Then regenerate the review artifacts once.
- Before H5: review the non-activation behavior on representative overtime player pages. player_graphs.py:23–33 explicitly admits overtime and bumps its version; state_replay.py:135–140 changes side resolution. An unchanged score total does not validate those newly shipped views.
- Before I3: record the measured backfill, acceptance, prewarm, and recovery durations, with a latest safe rollback time. Remove the unprepared method switch unless A12 is addressed.
- Before executing the runbook: reconcile executable commands and expectations to Python 3.13 / migration 0010. The owner overrides are clear, but body lines 374, 404, 448, 460, and 520 still direct operators toward 0009 or 3.11.
- Before the first production mutation: establish the availability and retention of PITR if it is part of recovery. The current plan defers that question to K3, bundle plan line 559.
6. Questions for the owner
1. Which machines or scheduled processes can ingest, and how will each remain frozen until its rc3 checkout and environment are verified?
2. What is the maximum acceptable outage, including rollback, that should determine the abort deadline?
3. Which supported Render procedure will keep traffic blocked while the rollback commit is deployed and verified?
4. Does the actual database plan provide PITR, and what retention window is available?
