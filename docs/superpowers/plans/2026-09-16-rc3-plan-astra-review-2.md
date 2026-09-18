# Second external review of the rc3 plan — Astra (ChatGPT agent), 2026-09-16

Recorded verbatim as delivered to the owner. Astra reviewed plan v2 and the rewritten (uncommitted) ledger entry
read-only from the owner's working copy, and independently analysed the saved JSON arrays. Its first review is in
`2026-09-16-rc3-plan-astra-review.md`.

The main session verified B1 (already suspected), B2's lock cycle (`app/services/players.py:56` reads
`player_view_cache` before `app/services/player_data.py:95` reads `impact_scores`), and migration 0006's
build-then-rename swap. It also settled one of Astra's UNVERIFIED points: production's
`default_transaction_isolation` is `read committed`.

Dispositions are in `2026-09-16-rc3-ship-plan-v2.md` section 4 (revision 2).

---

1. Verdict
Ledger: COMMIT AFTER FIXES.
Plan v2: NOT READY as written.
The redesign fixes several important problems, especially non-atomic restoration and misleading historical measurements. The remaining problems affect the release contract, not just implementation details.
Three highest risks:
- The version CHECK permits an old scorer to overwrite version-3 rows without changing their version.
- The live swap reverses the request path's table-lock order, creating a concrete deadlock and request-failure scenario.
- R2 does not explicitly wait for maintenance deployment and old-worker drainage before downgrading the schema.
Review was read-only: no file changes, database connections, test runs, application execution, or GitHub writes. I independently analyzed the saved JSON arrays.
Citation shorthand below:
- P = revised plan (docs/superpowers/plans/2026-09-16-rc3-ship-plan-v2.md)
- L = ledger (docs/superpowers/2026-09-07-predeclared-values.md)
- Code paths are relative to C:\Users\Conor Lum\Documents\GitHub\valo-with-friends-tracker.

2. Disposition audit
"RESOLVED" means adequately addressed in the proposed plan—not implemented or demonstrated.

| Original finding | Disposition | Evidence and remaining issue |
|---|---|---|
| A1: non-atomic rollback | RESOLVED | P:121 specifies "one transaction." This removes the original separately committed truncation. New gate-transition defects remain; B5. |
| A2: deployment while suspended | PARTIAL | P:138 introduces a "maintenance switch"; P:206–209 replaces suspension. Deployment completion and worker drainage are missing; B3. |
| A3: stale scorers/preflight | PARTIAL | P:127 puts preflight "before backfill_unscored_matches in both entry points." Correct. P:133's "gate stops every other checkout" is false; B1. |
| A4: misleading partial-backfill exits | RESOLVED | P:111 makes individual commands "all-or-nothing and exit-gated"; production no longer uses the incremental writer. Interrupted-operation recovery still needs explicit phase handling. |
| A5: unbounded deployment migration | PARTIAL | P:179 requires exit 0 and "exactly 0010"; P:136 adds deployment-path lock_timeout. However, lock-wait timeout is not an execution/outage bound; B2. |
| A6: missed three-term identities | RESOLVED | P:105 explicitly covers "every reconciliation, accumulator and table." |
| A7: misleading Before column | RESOLVED | P:106: "'Before' = stored production values." L:1818–1824 separates the stored cohort from 3133. |
| A8: two credit definitions | RESOLVED | L:1807–1809 distinguishes "leverage difference" from "persisted credit." |
| A9: unkeyed reproduction | PARTIAL | P:49 introduces keyed rows, but the serialization/load contract and auxiliary measurements remain incomplete; B6–B7. The historical positional limitation is now disclosed at L:1742. |
| A10: misleading field metadata | PARTIAL | P:99 requires a model-independent contract; P:107 derives and validates the field list. Adding version provenance creates a new review-versus-activation comparison problem; B6/B8. |
| A11: incomplete environment freeze | PARTIAL | P:109–110 records the whole-tree revision, interpreter and pip freeze. Recording an environment is not requiring its reproduction. The hash chain establishes corpus-output parity, not dependency identity or behavior on future matches. |
| A12: unrehearsed alternative writer | RESOLVED | P:230: "no method switch; abort = don't swap, or R1." |
| A13: prewarm claimed success | PARTIAL | P:168 requires both scopes at the new version. It should also explicitly require blob validity; version/presence alone is weaker than decode_cache_row, which calls _validate_blob at player_view_cache.py:565. |
| A14: database-name comparison | RESOLVED | P:161–162: "normalized measurements," with the database name "asserted separately." |

Ledger notes from the first review:

| Note | Disposition | Evidence |
|---|---|---|
| Misleading preregistration heading | RESOLVED | L:1698 now says "owner's lock after measurement." |
| Weak reproduction prediction | PARTIAL | L:1799 correctly says an explanation does not pass a mismatch; L:1812 specifies precision. Artifact/measurement coverage still needs B6–B7. |
| Two credit definitions | RESOLVED | L:1750–1751 explicitly says the leverage delta is "not" the row-field sum. |
| Equal B/C mutation | RESOLVED | L:1841–1842 explicitly tests unequal weights and acknowledges the no-op at B=C. |
| Rank definitions | PARTIAL | L:1764 specifies K-D source and tie handling. L:1762–1763 still places "29,892 gain" under rank changes although it means impact gain; B9. |
| Unsupported maximum 981 | RESOLVED | L:1756–1757 identifies separate saved-array calculation; independently reproduced. |
| Measurement before manifest exists | RESOLVED | L:1795 names commit B's comparator; P:142 exports through impact_rc3. |
| Preserve original cohort | RESOLVED | L:1778–1779 retains the original cohort and reports additions separately. Enforcement remains incomplete; B8. |

Previously missing steps:

| Missing step | Disposition | Evidence |
|---|---|---|
| Rehearse PR #67 rollback | PARTIAL | P:173 includes R2, but a scratch database/local app cannot establish Render traffic-cutover timing; B3. |
| Guard fixtures before connecting | RESOLVED | P:134–135 says "before touching any database" and prohibits .env fallback. |
| Complete schema/environment/contracts before freeze | PARTIAL | P:93–110 covers these, but B6's artifact/version contract must be settled first. |
| Review non-scoring pages | RESOLVED | P:165–166 explicitly includes overtime, side resolution, sessions and match pages. |
| Measure durations and rollback cutoff | PARTIAL | P:174 requires durations and a latest-safe-rollback time. P:235 wrongly treats the acceptable-outage question as "largely moot"; B2. |
| Synchronize executable runbook | RESOLVED | P:143–144 explicitly requires rewriting for 3.13, 0010 and stage-and-swap before use. |
| Establish recovery availability before mutation | NOT RESOLVED | P:177 says "if recovery is available"; P:255 leaves it pending. There is no explicit pre-mutation decision gate establishing either available recovery or the accepted alternative. |

3. New findings

B1 — P0 — A row-version CHECK does not identify the writer
Location: P:97–98, 127–133, 169, 181; L:1721.
Evidence: P:133 says "the gate stops every other checkout." The current writer at impact.py:1590 assigns only its known fields:
for field in _PERSISTED_FIELDS:
setattr(impact_score, field, getattr(calculated, field))

Problem/scenario: After activation, an old checkout loads an existing score row and updates its known columns. It does not map or assign scoring_version, so the stored value remains 3. The CHECK passes while the arithmetic becomes old-formula arithmetic. An old INSERT would fail on the new required columns; that does not protect UPDATE.
The CHECK also does not prohibit DELETE/TRUNCATE, protect earlier ingestion commits, or verify that an explicit config supplied to compute_impact_for_match matches the active manifest. Moreover, version-3 writes are permitted immediately after Stage 7's gate is installed—not "no checkout" writes as P:181 claims. CHECK constraints evaluate row values, not writer provenance.
Fix: For accidental stale checkouts, require a transaction-local writer/release identity through a database trigger, rejecting missing or mismatched identity even when the stored row already says 3. The current writer must verify the manifest/configuration before establishing that identity. Explicitly fence DELETE/TRUNCATE and maintain a closed state until writes are authorized. A restricted writer role with revoked legacy privileges is another option; administrative swapping needs a separately controlled path.
Rehearse an actual pre-column checkout updating an existing version-3 row, not merely a new writer explicitly assigning 2.

B2 — P0 — The swap's lock order conflicts with player requests
Location: P:118–126, 177, 235.
Evidence: P:118 orders:
"truncates impact_scores, inserts from stage, truncates player_view_cache"

But services/players.py:56 first queries:
db.query(Player, PlayerViewCache)

The cache-miss path later queries scores at player_data.py:95. db.py:29 retains the session until the request dependency closes it.
Problem/scenario: A request holds a read lock on the cache. The swap obtains its exclusive score-table lock. The request then waits for scores; the swap subsequently waits for the cache. That is a concrete lock cycle. A deadlock victim or timeout interrupts the request or swap.
Additionally, copying 659,500 rows and validating while retaining the exclusive lock blocks score readers for the operation's duration. lock_timeout does not bound that duration. PostgreSQL documents that TRUNCATE takes ACCESS EXCLUSIVE and blocks concurrent operations.
Fix: Specify and rehearse coordination covering requests, prewarm and their separate cache-writing sessions. Use a proven common lock/admission protocol, a drained maintenance interval, or a redesigned MVCC-safe replacement strategy. Establish an execution deadline and acceptable blocked-request duration, not just lock-wait timeout. Test cache misses during forward and rollback swaps.

B3 — P0 — R2's maintenance barrier is not established before downgrade
Location: P:138–139, 173, 205–209.
Evidence: P:206 immediately sequences:
"Turn the maintenance switch on."
"alembic downgrade 0007"

Problem/scenario: Saving an environment variable is not an instantaneous traffic barrier. Render's documented choices include saving without deployment; deployed changes create a replacement instance. During deployment, the original instance continues serving until traffic switches, and its process can remain alive afterward. Downgrading immediately can therefore expose an old request to removed columns.
The eventual revert also removes the newly introduced maintenance middleware. The final "switch off" cannot be relied on as the reopening barrier once the old application no longer implements that switch.
There is also a write-freeze gap: R2 before activation removes the scoring_version column underlying the version-3 CHECK, but specifies no independent replacement for P:28's "after any rollback" freeze.
Fix: Require successful deployment of maintenance-enabled code, verified traffic blocking, and drainage of old requests/workers before downgrade. Specify the exact compatible revert SHA and when its deployment may reopen traffic. Preserve the score-write freeze independently of the column being removed. Do not claim scratch-database rehearsal proves the provider's deployment transitions.

B4 — P0 — R1 becomes destructive after ingestion reopens
Location: P:43–44, 195–204.
Evidence: P:195 says "Reopen ingestion"; P:202 subsequently permits "Run rollback," which restores the pre-activation snapshot.
Problem/scenario: A new match is ingested after activation. R1 then truncates all scores and restores a snapshot that predates that match. Its match/source rows remain, but its scores disappear; the refuse-all gate prevents automatic repair. Retaining the snapshot for 14 days does not make it a complete rollback source for those 14 days.
Fix: Define R1's validity boundary. Either restrict this exact rollback to before ingestion reopens, or specify preservation and approved old-formula handling of post-snapshot matches. Capture the current score state before a later rollback. Snapshot retention and rollback coverage must be separate concepts.

B5 — P1 — Rollback and re-swap omit necessary gate transitions
Location: P:116–123, 171.
Evidence: P:121 restores rows and then "adds a refuse-all gate." P:171 requires:
"rollback → verify → swap again"

Problem/scenario: If implemented as CHECK (false) without NOT VALID, adding the gate rejects the restored nonempty table. With NOT VALID, restoration works—but the next swap's INSERT is rejected unless it first replaces that gate. The specified swap only "validates the gate."
Snapshot verification also specifies only count and sums, while exact row restoration includes keys and JSON data.
Fix: Define the state transitions explicitly: restore original values, install refuse-all without validating existing rows, and replace that gate appropriately before re-swap—all within their respective transactions. Use explicit column lists and preserve snapshot trade_credit=0 and scoring_version=1; do not relabel restored v1 rows as 3. Verify keyed, full-field equality before committing restoration.
A CTAS table is a valid data snapshot, but it does not carry the original table's constraints/indexes/defaults. Treat it as an immutable data source, not a schema replacement. Existing-row validation is skipped only when explicitly requested.

B6 — P1 — The comparison artifact is not a loadable persistence artifact
Location: P:49–53, 97–98, 113–115; L:1783–1785, 1797, 1853.
Evidence: L:1783 defines:
"every field in PERSISTED_FIELDS at commit A, then trade_credit, leverage_component and assists_component"

Yet P:113 says:
"create impact_scores_stage like impact_scores, and \copy the artifact into it."

Problem/scenario: The artifact includes two diagnostic columns absent from the table and excludes the subsequently required scoring_version. Direct copying cannot satisfy this schema.
The commit-A anchor itself is sensible: taken literally, it fixes the projection despite later changes to PERSISTED_FIELDS. Re-evaluating the list dynamically would instead duplicate trade_credit, introduce scoring_version, and break the chain. The declaration must explicitly prohibit that.
There is a second version issue: the planned review field list includes scoring_version, but the branch remains version 2 until activation. Full persisted review rows therefore legitimately differ from activation rows tagged 3.
Finally, "JSON with sorted keys" does not specify an identical Python/PostgreSQL byte stream. JSONB does not preserve input key order or whitespace.
Fix: Declare separate, versioned contracts for:
- The immutable score-comparison projection used by K1–K5.
- The persistence/load projection, including explicitly asserted version 3.
- Diagnostic fields and any auxiliary data.
Freeze the ordered header; define encoding, line endings, CSV quoting, integer rendering, SQL NULL versus JSON null, and recursive JSON serialization. Prefer applying one canonical serializer to exported and database-readback values. Specify how stored rows reproduce or separately verify diagnostic terms. Do not silently omit provenance from full persistence acceptance.

B7 — P1 — ON parity does not establish all declared RESULT measurements
Location: P:60, 142, 147; L:1800–1814.
Evidence: L:1802 says measurements from K2 and K3 "by the chain … must be identical." But P:147 requests every measurement "from the K3 artifact."
Problem/scenario: A change affects only credit-OFF behavior. Every ON hash still matches, but OFF→ON credit deltas and rank changes differ. L:1800 implicitly requires a same-commit OFF counterpart, but neither its exact configuration transformation nor its equality obligation is stated.
The CSV also lacks the event-level information needed for credited deaths by trade second and the source mappings needed for K-D comparisons. L:1811 invokes the kill observer, but impact.py:1339 calls that observer before credit allocation at lines 1365–1366; it does not directly emit final allocated credit.
Fix: Explicitly declare B's OFF run as the same configuration with only credit disabled, and require equality with A's 3.13 OFF baseline where identical measurements are predicted. Bind the necessary source mappings and credit-event diagnostics to the same cohort/revision. Define how final split allocations are obtained; they cannot be claimed as already contained in the CSV.

B8 — P1 — Replacement acceptance drops independent approval and input-state checks
Location: P:36–38, 56–57, 114–120, 193.
Evidence: P:193 specifies only:
"the existing replay_diffs over every match"

The existing backfill separately records backfill_impact_candidate.py:232:
"match_set_changed"
"approved_result_differences"
"sessions_at_end"

Problem/scenario: Matching replay and persisted output proves self-consistency, not agreement with the approved report. Counts/max ID and 13 fingerprints also do not bind all source data used for 3,125 matches. An untracked source edit outside those 13 matches can leave counts and keys unchanged. A recorded transaction snapshot identifier is not a content checksum or proof that every export query shared one snapshot.
Verification before a later swap also needs protection against intervening stage/input changes.
Fix: Bind the exact cohort and relevant input contents to a consistent export snapshot. Recheck the declared input/cohort identity at the write boundary, make verified staging immutable or lock/reverify it, and explicitly compare the approved 13-match results and manifest identity. Define the version-provenance comparison from B6 rather than letting 2→3 become either an unexplained failure or a silently ignored field.

B9 — P3 — "29,892 gain" describes impact, not rank
Location: L:1762–1763.
Evidence: L:1762 places:
"29,892 gain, 1,358 are unchanged, none lose"

immediately after the rank-change counts. The report's line 33 explicitly says:
"player-match impact up 29,892 / unchanged 1,358 / down 0"

Problem/scenario: Readers interpret this as rank gains, which it is not.
Fix: Say "29,892 player-match impact values increase…" and keep that statement separate from rank movement.

4. Answers to Q1–Q7

Q1. Swap concurrency
Partial table: Not through an ordinary reader observing uncommitted INSERT progress; the transaction and exclusive lock prevent that.
Empty table: A pre-existing transaction alone is insufficient to establish this. For the shown direct ORM reads under READ COMMITTED, a request that already read scores holds a lock blocking TRUNCATE; one that reads afterward normally obtains a fresh statement snapshot. The stronger claim that these requests necessarily see empty scores would be unsupported.
However, TRUNCATE can appear empty to an older retained snapshot that had not previously accessed the table. PostgreSQL explicitly documents that caveat. The application does not explicitly set isolation in db.py:21, so the actual deployed default is UNVERIFIED under this review's no-database rule.
Cache persistence: The current request path's separate cache-writing session is not, by itself, proof of stale resurrection: the outer request retains its score-table read lock while _run_in_own_session(store_player_views, …) executes (routers/players.py:139–147). That blocks the swap until the request releases its lock. An anomalous result from a different isolation/path could nevertheless be cached; cache writes do not validate the scoring generation of the input data.
Deployment ordering: player_view_cache.py:562 rejects a different cache version, and its version incorporates IMPACT_CALCULATION_VERSION at line 123. Thus 2→3 normally invalidates pre-activation caches; reverting 3→2 similarly invalidates version-3 caches produced during rollback's intermediate period. Neither transition prevents lock failures or proves safe concurrent execution. Old-worker writes can also overwrite a newer cache with an older-tagged blob, causing misses.
Definite finding: The lock-order cycle in B2, not an unconditional claim of partial/empty READ COMMITTED reads.

Q2. Gate coverage
Yes: A model lacking scoring_version can UPDATE an existing version-3 row and pass the CHECK. The existing assignment loop proves the path; B1.
Other gaps include DELETE/TRUNCATE, source/cache commits preceding scoring, explicit alternate configuration under version-3 code, and absence of a genuinely closed pre-activation state.
The smallest targeted stale-writer protection is a database-enforced writer identity that must be supplied by the current transaction, rather than inferred from the existing row. Couple that with configuration verification and explicit destructive-operation privileges. This protects against accidental old checkouts, not a privileged administrator deliberately bypassing controls.

Q3. Artifact definition
Conditionally unambiguous: "At commit A" fixes a projection if it means an immutable recorded header. It must not mean "whatever PERSISTED_FIELDS contains at each later run."
Not loadable as specified: Diagnostic columns do not exist in the table, and scoring_version is missing. B6.
Canonical equality is achievable, but not yet specified. Numeric sums can agree after defining fields/types and null handling; JSON has no meaningful generic "column sum." PostgreSQL JSONB text is not a promised serialization match for Python's sorted-key JSON. Canonicalize typed values with the same explicit contract on both sides.

Q4. Chain completeness
K3 requires a credit-OFF counterpart. L:1800 declares matching commit/interpreter, but does not completely specify its configuration or require OFF-baseline equality. B7.
Planned score arithmetic should not legitimately change: adding persistence columns, using scale 1.0, and switching to an equivalent manifest should preserve the frozen score projection.
Provenance legitimately changes: scoring_version becomes 3 at activation, whereas P:98 assigns the current calculation version and L:1868 keeps it 2 before activation. That must be handled explicitly in persistence/review contracts—not normalized away without a declared assertion.
ON hashes alone also cannot prove identical OFF-derived or event-derived measurements.

Q5. Rollback
R1 is atomic in concept, but incomplete as specified. It needs:
- Explicit restore columns and original values, including credit 0/version 1.
- A refuse-all gate that does not validate existing rows.
- A defined transition permitting a subsequent administrative re-swap.
- Exact keyed snapshot verification and protection against snapshot replacement.
- A cutoff or policy for post-snapshot matches.
CTAS's missing indexes/constraints/defaults do not invalidate its use as a data snapshot. They do mean its integrity must be verified and it must not be substituted wholesale for the production table.
R2 does not yet establish safe sequencing. It can avoid mismatched-schema service only after the maintenance deployment has actually blocked traffic and old workers have drained. Its reverted code no longer implements the switch, and its database write freeze must survive schema downgrade. B3.

Q6. Dropped safeguards

| Safeguard | Assessment |
|---|---|
| Refuse all other sessions | Deliberately removed, but "no long-running transactions" is not an equivalent concurrency protocol. B2. |
| Exact match-set change detection | Weaker count/max and key checks replace it; not equivalent for empty matches or changed source contents. B8. |
| Approved-results comparison | Not explicitly retained in Stage 8 acceptance; replay_diffs is a different check. B8. |
| Player-cache clearing | Retained transactionally, but concurrency needs B2. Both-scope coverage is added; blob validity should be explicit. |
| Site-stats cache clearing | No findings for score-only replacement. site_stats_cache.py:38–39 says these aggregates read raw match/round/player data rather than impact scores. |
| Per-match resumable state | No longer necessary for an atomic table replacement. An equivalent operation-state record is still needed to distinguish staged, snapshotted, committed-but-unverified and rolled-back states, particularly after lost connections. Blindly repeating snapshot creation or swap is unsafe. |

Q7. Ledger numbers
Confirmed by independent saved-array analysis:
- 21,837,990 × 2.5 / 3 = 18,198,325.
- 18,204,413 − 18,198,325 = 6,088.
- Maximum positional ON-minus-OFF leverage difference: 981.
- Total difference: 18,204,413; positive differences: 105,669; minimum difference: 0.
- Shares, impact mean/SD/extremes, within-match rank changes and impact-gain counts reproduce the quoted figures.
The earlier committed ledger distinguishes the 21,837,543 row-credit sum from the 21,837,990 leverage difference. The rewritten arithmetic correctly uses the latter. Attribution of the entire 6,088 to 3133 plus rounding remains UNVERIFIED; "consistent with" is appropriately limited.
The saved report supports:
- Lines 15–16: all quoted shares.
- Lines 30–32: 2,262 changed match orders, 222 changed leaders, 6,884 changed player-match ranks.
- Line 33: 29,892 impact increases, not rank gains.
- Lines 36–37: K-D identical-rank percentages 45.4→41.1, within-one 80.8→76.9.
K-D percentages are verified against the report, not independently recomputed from database events.
Both pytest logs list the same five failures and, at line 928:
5 failed, 1148 passed, 1 skipped, 33 warnings

That confirms identical test outcomes, not numerical scorer parity. Exact interpreter patch versions are not established by the logs themselves; those remain supplied provenance. The stale fixture is a test-maintenance defect, not evidence that it should remain an allowed failure in the corrected environment.

5. Ledger entry
Must fix before commit:
1. Replace L:1721's false stale-checkout guarantee with the actual enforcement requirement; add the old-model UPDATE test to L:1851.
2. Settle the immutable comparison header, load projection, canonical serialization and scoring_version treatment before making K1=K5 an append-only promise.
3. Explicitly declare K3's OFF configuration and required OFF comparison; qualify L:1803's claim that every measurement follows from the ON chain.
4. Declare the auxiliary evidence needed for event-level credit and K-D measurements.
5. Change "29,892 gain" to "29,892 player-match impact values increase."
Can wait until the specified implementation/rehearsal gates:
- Implementing migration, trigger/privilege enforcement, exporter and swap tooling.
- Producing the new keyed artifacts and measured row-credit sum.
- Executable rollback commands, concurrency tests and measured deadlines.
- Environment reproduction checks, valid-blob prewarm checks, and resolution of the stale test fixture.
- Recovery availability and retention—but only with an explicit decision before production mutation.
The historical disclosure, distinction between credit measures, corrected arithmetic, maximum 981, unequal-weight mutation and preserved-cohort language need no further substantive correction.

6. Questions for the owner
1. What maximum blocked-request/error interval is acceptable for forward swap and rollback? Keeping the service running does not eliminate this limit.
2. Must R1 remain available after ingestion reopens? If so, what should happen to matches added after the v1 snapshot?
3. Is PITR available, with what retention and restore procedure? If unavailable, is a rehearsed backup-based recovery path acceptable before any production mutation?
