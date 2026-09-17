# rc3 ship plan v2: keyed artifacts, build-and-rename swap, release write gate

**Status (2026-09-17): Stages 1 to 3 done and the chain verified; the owner confirmed the lock and the Stage 4
RESULT entry is drafted; `valo_rc3_rehearsal` is restored, migrated to 0010 and gated. Nothing is frozen, rescored,
activated or deployed; production is unchanged (alembic 0007, no gate).** Progress, results and deviations from this plan are in section 7. The runbook
with exact commands is `../impact-rc3/README.md`.

**Revision 2 (2026-09-16), after Astra's second review** (`2026-09-16-rc3-plan-astra-review-2.md`, findings B1–B9).
Revision 1 is superseded; the changes are the release write gate (D10), the build-and-rename swap (D9), the rollback
expiry (D11) and the data contracts (§2).

This supersedes the runbook in `2026-09-16-rc3-freeze-and-ship.md` (v1). v1 remains the source for its verified
findings, the credit-ON corpus measurement, and details v2 does not change; where they differ, v2 wins. Astra's two
reviews are recorded verbatim beside this file; §4 gives a disposition for every finding. Exact commands are written
and rehearsed in Stages 3 and 6, and land in the rc3 README runbook, as rc2's did.

## 0. Decisions (owner, 2026-09-16)

| # | decision | why / effect |
|---|---|---|
| D1 | Lock kept: A 1 / B 2.5 / C 2.5 / D 100, trade credit ON, `trade_credit_scale` 1.0 | confirmed after the credit-ON corpus run |
| D2 | Persist `trade_credit` (migration 0010) | before the freeze: the persisted-field list is a hashed source |
| D3 | Python 3.13 for tests, freeze and scoring | refreshes run only on this PC; 3.13 matches Render (3.13.5) and the rc2 freeze environment; the suite is identical on 3.11 and 3.13 (§5) |
| D4 | Ingestion frozen until 48 hours after activation; refreshes only ever run from this PC, by hand | the declared cohort stays fixed, and rollback stays complete (D11) |
| D5 | Replace every score row in one atomic operation, with the site up | fixes Astra A1, A2, A4, A12 |
| D6 | `scoring_version` column (0010) records which formula wrote each row | provenance and audit only — it is NOT the enforcement mechanism (B1) |
| D7 | The stored-v1 -> rc3 comparison is the owner's last look before the freeze | extends A7: approval must see what users will see |
| D8 | PR #67's non-scoring changes are reviewed on the rehearsal database before merging | Astra missing step |
| **D9** | **The swap is build-and-rename, not truncate-and-insert** | B2: the lock is held for milliseconds, readers never see an emptied table, and the old table becomes the rollback source. It reuses migration 0006's proven pattern on this database |
| **D10** | **A release write gate**: a gate table plus statement-level triggers on the score AND ingestion tables, which refuse any write whose connection does not carry the open release's identity | B1: a row-value CHECK cannot tell who is writing. This also enforces the ingestion freeze in the database, so a stale checkout can no longer strand a match |
| **D11** | **Rollback expires when ingestion reopens**: a 48-hour observation hold after activation, during which the gate stays closed and R1 is a complete rollback. After that, problems are fixed forward | B4: once a new match is scored under rc3, restoring the pre-activation table would lose it |
| **D12** | **Event-level credit measurements are dropped from the rc3 RESULT**, citing the 2026-09-14 figures scaled by 2.5/3 | B7: the saved exports cannot produce them, the rule is unchanged since 09-14, and adding an allocation observer would change a hashed source before the freeze |
| D13 | **Point-in-time recovery is available, to any timestamp in the past 7 days** (owner checked the Render Recovery page, 2026-09-17) | The Stage 7 gate is cleared. It restores into a NEW instance, so R3 also means repointing the web service and `.env.remote`. After 7 days recovery rests on the B0/B1 dumps and `impact_scores_v1` |

Defaults adopted without a separate question:
- PR #67 split: merged without activation, then activation as its own one-commit PR; both merged with merge commits.
- `FormulaWeights` defaults unchanged.
- The gate is installed **closed**, and only opens for the rc3 release at the end of the observation hold.
- A small app maintenance switch covers the rare PR #67 rollback, instead of relying on Render suspension (A2).
- **A swap may stall page loads for at most 5 seconds per lock acquisition**, of which it now makes two. If a lock
  does not come free in that time it aborts, changes nothing and is retried.
- Deliberate manual fixes to gated tables (for example merging a renamed friend's player rows) use a documented admin
  identity, and each use is noted in the ledger.

## 1. Invariants

- **No ingestion until the gate opens**, from anywhere. This PC is the only refresher.
- **Production is written only in the named steps:** Stage 7 (migrations, gate install, merge), Stage 8 (build, swap,
  activation merge, cache rebuild) and Stage 9 (rollback). Everything else against production is read-only.
- **The gate is closed from Stage 7 until the end of the 48-hour observation hold.** While closed, only runbook steps
  using the admin identity can write gated tables.
- **R1 is a complete rollback only while the gate is closed.** Reopening ingestion ends it (D11).
- **No production write before the recovery decision:** either point-in-time recovery is confirmed with its window, or
  the owner accepts backup-only recovery, whose restore has been rehearsed in Stage 6.
- **Preflight before every production or rehearsal step.** It asserts `current_database()`, `alembic_version`, match
  count, max id and the gate state against expected values; a mismatch aborts. The two URLs differ only by database
  name, so the name is asserted, never eyeballed.
- **The swap takes no lock on `player_view_cache` at all.** It clears the cache by DELETE, which no page load
  conflicts with, and takes ACCESS EXCLUSIVE only on the score tables. The original order (cache first, then scores)
  was right about requests but still let a page load's own write-through connection queue behind an exclusive cache
  lock with no deadlock to break it (§7, C1).
- **Exit codes gate everything.** Every command's exit code is checked, and a nonzero exit stops the procedure.
- **Every comparison hash must match at every link of the chain** (§2). An explanation does not pass a mismatch: it is
  eliminated, or the ledger entry is re-declared.
- **The operation state is recorded** (built / verified / swapped / rolled back, with timestamps and hashes), so a lost
  connection never leads to a blind repeat.

## 2. Data contracts (B6, B7, B8, A9)

Three separate contracts. None of them is derived from `PERSISTED_FIELDS` at run time: the header is **recorded once**
and reused, so later changes to that list cannot alter the projection.

**(a) Comparison projection** — the only thing the K-chain compares.
- One CSV row per `(round_id, match_player_id)` over the declared cohort, sorted by key.
- Columns: the ordered header recorded at commit A (every field in `PERSISTED_FIELDS` as it stands there, plus
  `trade_credit`, `leverage_component`, `assists_component`). `scoring_version` is **excluded**: it is provenance, and
  it legitimately differs between review (2) and activation (3).
- Identified by SHA-256 over the canonical serialization below.

**(b) Load projection** — what is loaded into the replacement table.
- Exactly the table's columns, including `trade_credit` and `scoring_version`, with `scoring_version = 3` asserted on
  every row before the swap.
- Verified by reading the built table back and re-serializing it with the same canonical serializer, not by comparing
  PostgreSQL's own JSON text.

**(c) Diagnostics and inputs** — everything else, never mixed into (a) or (b):
- the input fingerprint: `match_source_fingerprint` for **all 3,125 matches** (not just the declared 13), computed
  inside the export's `REPEATABLE READ` snapshot, plus counts, max match id, snapshot id and database name;
- the environment: interpreter build and `pip freeze`;
- K-D counts from `kill_events`, read in the same snapshot as the export.

**Canonical serialization**, one implementation used on both sides: UTF-8, LF line endings, a fixed column order,
integers rendered without separators, SQL NULL distinct from JSON null, and `trade_detail` rendered by recursive
canonical JSON (sorted keys, fixed separators) rather than either side's default.

**The chain.** Commit A is the first commit after the declaration (the `trade_credit_scale` change and the exporter);
commit B completes the implementation; commit C is the freeze.

| link | Python | code | configuration | must equal |
|---|---|---|---|---|
| K1 | 3.11 | commit A | explicit locked weights | baseline |
| K2 | 3.13 | commit A | explicit locked weights | K1 (the declared prediction) |
| K3 | 3.13 | commit B | declared `impact_rc3` comparator | K1 |
| K4 | 3.13 | commit C | the manifest, via `config_from_manifest` | K1 |
| K5 | 3.13 | activation commit | active configuration | K1; **K5 is what gets loaded** |

A credit-OFF export is produced at commit A and commit B, identical in configuration except `enable_trade_credit`
false; OFF(B) must equal OFF(A). ON hashes alone do not establish OFF-derived measurements (B7).

## 3. Stages

### Stage 1 — Declaration
1. The owner reviews the rewritten draft entry in `docs/superpowers/2026-09-07-predeclared-values.md`, then it is
   committed on its own.

### Stage 2 — Baseline artifacts and the last look (read-only against production)
1. Mutation-check the uncommitted `trade_credit_scale` change: remove `* weights.trade_credit_scale`, and the scale
   test must fail on a value.
2. Write the exporter test-first against the sqlite fixtures: recorded header, key order, canonical JSON, NULL
   handling, hash stability.
3. Commit both. **This is commit A**, and its export records the header for contract (a).
4. Export K1 (3.11, ON), K2 (3.13, ON) and OFF at commit A (3.13). Three unattended ~16-minute runs.
5. Compute the declared measurements, including the stored-v1 -> rc3 comparison.
6. **The owner's last look (D7).** Confirm the lock and continue, or retune: a new declaration, back to Stage 1.

### Stage 3 — Implementation under 3.13, test-first, each test proven to fail on a value → commit B
0. Create `valo_rc3_rehearsal` on the Render instance from a fresh production `pg_dump`. The database tests need a
   current schema; the Neon database in `.env` predates migration 0006 (§5).
1. **Migration 0010:** `trade_credit` and `scoring_version` (smallint, NOT NULL, existing rows 0 and 1, defaults
   dropped afterwards as 0008 does). Both join the model and `_PERSISTED_FIELDS`; the scorer writes
   `scoring_version = IMPACT_CALCULATION_VERSION`. A test asserts the persisted fields equal the model's non-key
   columns, independent of the shared list (A10).
2. **The release write gate (D10).** A committed SQL runbook file, deliberately **not** an Alembic migration, so a
   downgrade cannot lift it (B3):
   - a one-row `scoring_gate` table holding the state (`closed` / `open`), the open release id and an admin id;
   - a trigger function that raises unless the connection's identity (`current_setting`) matches the open release, or
     the admin id;
   - statement-level triggers for INSERT, UPDATE, DELETE **and TRUNCATE** on `impact_scores`, `matches`,
     `match_players`, `rounds`, `round_player_stats`, `round_player_spend` and `kill_events`.

   The rc3 ingestion sets its identity on each connection **only after** the preflight verifies the manifest, the
   version and the gate; the swap tooling uses the admin identity; the web app sets nothing and writes none of these
   tables (verified: its only writes are `friendships`, `player_view_cache` and `site_stats_cache`).
3. **`COMPARATORS["impact_rc3"]`** carries exactly the locked values; a manifest missing `enable_trade_credit` must
   fail the identity check (v1 C2).
4. **`build_manifest`** describes A/B/C/D, the scale and trade credit; `TRADE_CREDIT_SCHEDULE` joins the code identity.
5. **The review tool:** four-term identities in every reconciliation, accumulator and table (A6); "Before" = stored
   production values, with the legacy replay as a separately labelled diagnostic (A7); the results field list recorded
   and validated on read, with `scoring_version` compared explicitly rather than ignored (A10, B8).
6. **A decomposition check** replaces `compare_econ_models.py` for rc3.
7. **The freeze script** requires the whole tree clean, and records `scorer_revision`, interpreter build, `pip freeze`,
   fingerprints, snapshot id and database name (A11).
8. **Swap tooling**, one committed script, each subcommand all-or-nothing and exit-gated:
   - `export` (Stage 2's exporter, contracts (a) and (b));
   - `build`: create the replacement table, COPY the load projection in, then add the primary key, the
     `match_player_id` index and both foreign keys under temporary names, attach the gate triggers, ANALYZE;
   - `verify-build`: row count 659,500; key set equal to `round_player_stats`; `scoring_version = 3` everywhere;
     read-back canonical hash equal to the load projection; the 13 approved matches equal `review-results.json`; the
     whole-cohort input fingerprint unchanged since the export;
   - `swap`: **one transaction**, `lock_timeout` 5s, locking `player_view_cache` first, then both score tables.
     Rename the old table's constraints and indexes aside, rename `impact_scores` to `impact_scores_v1`, rename the
     replacement into place, rename its constraints and indexes to the canonical names, truncate `player_view_cache`,
     record the state. Today that means `impact_scores_pkey`, `ix_impact_scores_match_player_id`, two foreign keys and
     PG 18's 17 named NOT NULL constraints (22 after 0008 and 0010) — the naming traps migration 0006 already hit;
   - `rollback`: the same transaction shape in reverse, plus setting the gate to closed;
   - every subcommand writes the operation-state record.
9. **Shared ingestion preflight (A3).** Before `backfill_unscored_matches` in both entry points and before
   `load_match`: migration head matches; `ACTIVE_MANIFEST` is set and verifies; `IMPACT_CALCULATION_VERSION` matches
   the manifest; installed packages match the freeze record (A11); the gate is open for this release. Only then is the
   connection identity set. The preflight stops a correct checkout early; the gate stops every other one.
10. **Test guard:** database-backed fixtures refuse to connect before touching any database unless a test database is
    named explicitly; `.env` is never a fallback.
11. **Bounded migrations (A5):** `alembic/env.py` sets `lock_timeout` and `statement_timeout` on Postgres, so Render's
    build-time `upgrade head` is bounded too. Correct 0008's lock docstring.
12. **Maintenance switch:** an environment variable makes every page except `/health` return 503. New file, one include
    line in `main.py`. Used only by R2.
13. **Full suite under 3.13**, database tests against the rehearsal database. Every remaining failure must be confirmed
    environmental and listed.
14. **K3 and OFF(B):** export at commit B through `impact_rc3`; K3 must equal K1 and OFF(B) must equal OFF(A).
15. **Runbook commands** rewritten for 3.13, 0010, the gate and build-and-rename.

### Stage 4 — RESULT entry
From the K3 artifact and OFF(B): every declared measurement. Append the RESULT entry and commit.

### Stage 5 — Freeze and review
1. Freeze at commit C from a clean tree. Commit the manifest alone; never edit it.
2. K4 must equal K1.
3. Reviews against the rehearsal database, fingerprints checked: 3104, the fixed ten, 3120 with trace, and 3133, with
   stored-v1 "Before"; the decomposition check; `review-results.json` for the 13 matches.
4. Declared checks, including defect reinstatement. Write `SUMMARY.md` and the ledger's review RESULT entry; the owner
   approves.

### Stage 6 — Rehearsal on `valo_rc3_rehearsal` (fresh restore; everything timed over the real link)
1. **Backup B0** (`pg_dump`, exit-gated) restored into the rehearsal database; compare normalized measurements with the
   database name asserted separately (A14). This doubles as the rehearsed restore the recovery gate may rely on.
2. **Migrations** (`alembic upgrade head`, timed), then install the gate **closed**.
3. **PR #67 page review (D8):** run the merge commit's app against the rehearsal database; the owner reviews overtime
   state diagrams, side resolution, sessions and match pages.
4. **Forward path:** export → build → verify-build → swap. Then pages under the activation commit, and prewarm with
   per-player coverage **and blob validity** (`decode_cache_row`, A13).
5. **Gate tests:**
   - an actual pre-0010 checkout (an `origin/main` worktree) updating an existing row → refused (B1);
   - an ingestion run without the identity → refused at its first insert, with nothing committed;
   - the rc3 checkout with a verified preflight → allowed.
6. **Concurrency test (B2):** drive page loads that miss the cache while swapping forward and back; confirm no
   deadlock, and measure the longest stalled request against the 5-second budget.
7. **Rollback path:** `rollback` → verify → swap again; kill a swap mid-transaction and confirm the table is unchanged.
8. **R2 dry run:** maintenance switch, downgrade, revert, on the rehearsal database only. This cannot establish
   Render's traffic-cutover timing, which Stage 9 handles with its own verification (B3).
9. Record all durations and a latest-safe-rollback time.

### Stage 7 — Merge PR #67 without activation (site up)
1. **Recovery gate:** point-in-time recovery confirmed with its window, or backup-only recovery accepted with Stage 6.1
   rehearsed. This is the last step before the first production write.
2. Preflight; no long-running transactions; note a recovery restore point.
3. Apply 0008–0010 by hand from the merge commit. **Exit code 0 and `alembic_version` exactly 0010**, or stop and do
   not merge (A5).
4. Install the gate **closed**. From here, no checkout can write scores or ingest, whatever its state.
5. Merge with a merge commit. Render's build runs `upgrade head`: a no-op, and bounded. Verify `/health` and pages.

Between Stages 7 and 8 the site shows today's v1 numbers with the branch's other changes, and caches rebuild lazily.

### Stage 8 — Activation (site up)
1. Preflight, gate closed. **K5** export from the activation commit; it must equal K1.
2. `build` → `verify-build` (including the input-fingerprint recheck and the 13 approved matches).
3. Capture prewarm ids (roster and recently cached players).
4. `swap`. Then re-verify the live table: read-back hash, counts, and 210 rows for match 3133.
5. Merge the activation PR (`ACTIVE_MANIFEST`, `IMPACT_CALCULATION_VERSION = 3`); wait for the deploy to go live.
6. Clear `player_view_cache` once more (it costs nothing and discards anything the old instance cached), prewarm the
   roster with coverage and blob validity, then recent players in the background; refresh site stats.
7. **Acceptance:** `replay_diffs` over every match, read-only, plus the approved-results comparison for the 13 matches
   and a final input-fingerprint check (B8). Any difference → R1.
8. **Page checks:** 3104's totals equal the review's AFTER column; 3133 is scored; roster pages load.
9. **Observation hold: 48 hours with the gate closed** (D11). R1 stays complete throughout.
10. Open the gate for the rc3 release, then reopen ingestion on this PC from the activation commit with the 3.13 venv.
    After the first new match: `scoring_version` = 3, and the match equals its replay. **R1 expires here.**
11. Ledger activation note: SHAs, durations, hashes, gate transitions.
12. Drop the leftover build objects. Keep `impact_scores_v1` at least 14 days, and longer than the recovery window.

### Stage 9 — Rollback
- **R1 (scoring), valid only while the gate is closed:**
  1. `rollback`: one transaction, reverse renames, gate set closed.
  2. Revert the activation PR, with the site up.
  3. Clear the cache again once the revert is live.
  4. Ingestion stays closed until the owner decides.

  After ingestion reopens, R1 no longer applies: fix forward, or capture the current scores first and decide what
  happens to matches ingested after the snapshot (B4).
- **R2 (PR #67), each step verified before the next (B3):**
  1. Deploy the maintenance switch and **confirm it is live**: pages return 503 and old instances have drained.
  2. `alembic downgrade 0007` (after R1, if activation happened).
  3. Revert the merge and deploy. That deploy is what reopens the site; the reverted code has no switch to turn off.
  4. The gate survives the downgrade and stays closed, so scoring stays frozen either way.
- **R3 (last resort):** point-in-time recovery into a new instance, then repoint the web service and `.env.remote`.
  Pending the recovery gate.

## 4. Findings: dispositions

**Astra's first review (A-series)** — all now addressed; the ones Astra rated PARTIAL in its second pass are marked:

| finding | disposition |
|---|---|
| A1 non-atomic rollback | By design: the swap and rollback are single transactions (D5, D9) |
| A2 deploy while suspended | Nothing is suspended; R2 verifies each step (B3 fix) |
| A3 stale scorers | Preflight (3.9) plus the release gate (D10), which reaches checkouts the preflight cannot |
| A4 exit 3 after writes | The in-place backfill is not used; every subcommand is atomic and exit-gated, with an operation-state record |
| A5 unbounded deploy migration | Merge gated on exit code and revision (7.3); `lock_timeout` and `statement_timeout` in `env.py` (3.11) |
| A6 three-term identities | Adopted (3.5) |
| A7 "Before" is a replay | Adopted (3.5), plus the stored-v1 -> rc3 comparison (D7) |
| A8 two credit definitions | Adopted in the ledger |
| A9 unkeyed measurements | Contracts and chain (§2) |
| A10 metadata field list | Recorded header, model-column test, explicit `scoring_version` comparison (3.1, 3.5) |
| A11 environment not frozen | Recorded at the freeze, and the preflight checks installed packages against it (3.9) |
| A12 unrehearsed abort | No method switch; abort = don't swap, or R1 |
| A13 prewarm success | Coverage **and blob validity** (6.4, 8.6) |
| A14 database-name comparison | Adopted (6.1) |

**Astra's second review (B-series):**

| finding | disposition |
|---|---|
| B1 row CHECK cannot identify the writer (P0) | **Design changed:** the release write gate (D10). `scoring_version` is provenance only. Rehearsed with a real pre-0010 checkout (6.5) |
| B2 swap lock order deadlocks with requests (P0) | **Design changed:** build-and-rename (D9), locks taken in request order, 5-second budget, concurrency test (6.6). Rated P1 here: a deadlock aborts one request or the swap, which changes nothing |
| B3 maintenance barrier not established (P0) | Adopted: R2 verifies the maintenance deploy is live and drained before downgrading; the revert deploy reopens; the gate lives outside Alembic so the freeze survives |
| B4 R1 destructive after reopening (P0) | Adopted: D11, the 48-hour hold and an explicit expiry |
| B5 gate transitions on rollback (P1) | Dissolved by D9: renames restore the original table with its constraints; the gate is a table row, not a constraint on data |
| B6 artifact is not loadable (P1) | Adopted: three contracts, recorded header, canonical serializer, read-back verification (§2) |
| B7 ON parity ≠ all measurements (P1) | Adopted: OFF exports at A and B, required equal; event-level credit measures dropped (D12) |
| B8 acceptance drops approval and input checks (P1) | Adopted: whole-cohort input fingerprint rechecked at the write boundary, approved-results comparison kept, built table protected by the gate, operation-state record |
| B9 "29,892 gain" wording (P3) | Adopted in the ledger |
| Recovery not established before mutation | Adopted: an explicit gate at 7.1 |

## 5. Verified environment facts (2026-09-16)

- **Production:** PostgreSQL 18.6; `default_transaction_isolation` is **read committed**, which settles the MVCC
  question Astra had to leave unverified.
- **`impact_scores` today:** indexes `impact_scores_pkey` and `ix_impact_scores_match_player_id`; constraints
  `impact_scores_pkey`, `impact_scores_round_id_fkey`, `impact_scores_match_player_id_fkey` and 17 named NOT NULL
  constraints; **no triggers; no incoming foreign keys**, so a rename swap is clean.
- **The web app writes only** `friendships`, `player_view_cache` and `site_stats_cache`, so gating the ingestion and
  score tables cannot break a page.
- **Tests:** the full suite under Python 3.11.4 and 3.13.15 is identical: **1,148 passed, 1 skipped, 5 failed**. Four
  failures are database tests against the stale Neon database in `.env`, whose `impact_scores` predates migration 0006
  (`column impact_scores.damage does not exist`; one of the four only cascades from the aborted transaction), and one
  is the stale `test_happy_path_blob_validates` fixture. No writes occurred. Stage 3.10 moves database tests to the
  rehearsal database, where the stale fixture should be fixed rather than tolerated.

## 6. Open

- Nothing gating the next step. Point-in-time recovery was the last open question and is answered (D13): any timestamp
  in the past 7 days. `impact_scores_v1` is kept at least 14 days, which already outlasts that window, so the dumps and
  that table are what recovery rests on once 7 days have passed.

## 7. Progress, results and deviations (2026-09-16)

### Stages 1 and 2
- **Stage 1:** the rc3 declaration was committed on its own (08810f7).
- **Commit A** (93c1c03): `trade_credit_scale` and the exporter.
- **K1 did not equal K2.** 18 rows differed, because CPython 3.12 made `sum()` compensated for floats. Every one of
  them differs in `trade_credit`; 7 also differ in `kill_impact`, and one each in `impact` and `leverage_component`
  (the ledger records this; "all trade_credit" was a wrong shorthand here until the third review caught it).
  - The owner chose exact summation (`math.fsum`) for every scoring float sum (b65fd4f).
  - The ledger RESULT (b501ae2) re-declared the chain from that commit as K1' (3.11), K2' (3.13) and OFF(A').
- **K2' = K1'** = `7e5ff2789ed1ea3a61425e1a6138576bc250cddc5de4e63cb3740a6d9f42f1bb`. The files are identical, and so
  are all 3,125 per-match input fingerprints and the configuration.
  - That hash also equals the pre-fix 3.13 export: exact summation reproduced what 3.13's compensated `sum()` already
    computed, and only 3.11's results moved.
- **OFF(A')** = `648df0403a0d48411dfd42c5b7697da1d6952de416e3a1a36bc97fc628460eac`, which also equals the pre-fix 3.13
  OFF export.
- **The last look (2.5, D7)**, from K2' and OFF(A'), is in `C:\Users\Conor Lum\Documents\valo-backups\rc3-artifacts-A2\last-look.json`.
  Diffs against K2': 0 rows vs K1', 0 vs pre-fix K2, and 18 vs pre-fix K1 (all `trade_credit`).
  - **Corpus shares:** damage 41.7%, leverage 40.3%, econ 8.5%, assists 9.5%. Medians over player-matches with at
    least 12 rounds: 43.7, 37.3, 7.3 and 9.3.
  - **Credit:** 18,206,528 persisted (15.1% of positive leverage with the credit off), on 105,669 player-rounds; the
    largest is 981.
  - **Credit off to on:** Impact rises for 29,892 player-matches and falls for none; ranks change for 6,884.
  - **Stored v1 to rc3:** 3,124 matches (3133 is unscored today), 15,179 of 31,240 player-match ranks change, and
    the top player changes in 633 matches. Per-match Spearman median 0.939 (p10 0.842). Roster order is unchanged
    except that Osmin, flatcat, Deemo and ternstyle move among ranks 3–6.
  - **Match 3133** gains its first scores.
  - The owner confirms the lock or retunes (a new declaration).

### Stage 3 commits (`git log --oneline 7f4a63b..HEAD`)

| step | commit | what |
|---|---|---|
| 3.1, 3.2, 3.8, 3.9–3.12 | 60b76f6 | 0010, release write gate, swap tool, ingestion preflight, test guard, bounded migrations, maintenance switch |
| — | b65fd4f | exact summation |
| 2.5 | 0a2cf4f | keyed measurement report |
| 3.5 | 678b690 | review tool: four terms, stored "Before", recorded field list |
| 3.6 | a85f914 | decomposition check against an independent rebuild of the credit |
| 3.7 | 9bda3a9 | freeze script (clean tree, HEAD, environment, fingerprints) |
| A13 | 469f164 | per-player prewarm coverage using the page load's own decode |
| 3.8, §1 | d835ccf | operation-state record; swap/rollback guards; verify-build input fingerprints; `release_preflight.py` |
| 3.10 | 566520a | tests no longer leak rows; tests that empty tables refuse `*_rehearsal` |
| 8.3, 8.6 | f1617a9 | capture the prewarm lists before the swap, prewarm exactly them after |
| D3 | fa54ad3 | refresh scripts run `.venv313` |
| 8.4 | 8cebb5f | `verify-live` |
| §5 | 3b04a84 | the stale site-stats fixture (its rejection tests had been vacuous) |
| 3.14 | 300c4ca | exporter `--comparator` / `--manifest` / `--active`; records HEAD, refuses a dirty tree |
| D3, A11 | 4e2aa23 | ingestion preflight compares the frozen interpreter (major.minor) |

Every new test was shown to fail on a value when the code it guards is broken.

**Commit B is 5c369f8**, and K3 and OFF(B) were exported from it (2026-09-17):
- **K3 (3.13, the `impact_rc3` comparator, clean checkout) = K1'**, byte for byte, with the same input fingerprints.
- **OFF(B) = OFF(A')**, likewise.
- Both sidecars record their source (`comparator impact_rc3`, with `credit_override: off` for OFF(B)) and HEAD.
- Artifacts: `rc3-release\chain\` under the backups directory.

Suite at commit B:
- Python 3.11 offline: 1,248 passed, 48 skipped (database tests), 0 failed.
- Python 3.13 with the database tests on `valo_rc3_test`: 1,283 passed, 13 skipped, 0 failed. The skips are the
  analysis tests that measure the real corpus, which the schema-only scratch database does not have.

One commit lands after B (175d278): installing the gate now gives up after 5 seconds rather than stalling page loads,
because dropping and recreating its triggers takes ACCESS EXCLUSIVE on every gated table (measured on 18.6). It does
not touch scoring, and K4 re-verifies the chain at commit C.

### Deviations from this plan
- **3.0.** Stage 3's database tests used `valo_rc3_test`, a schema-only scratch database (migrations 0001–0010, gate
  installed closed). The production restore, `valo_rc3_rehearsal`, moves to just before Stage 5, because the reviews
  need production's data at schema 0010 (runbook section 4). Tests never run against the rehearsal database, and
  tests that empty tables refuse it.
- **3.8.** Changes to the swap tool:
  - **NOT NULL constraints are not renamed.** PostgreSQL 18's `CREATE TABLE ... LIKE` keeps their canonical names,
    and a table rename does not change them (verified on 18.6 and pinned by a test).
  - **`export` is its own script.**
  - **Added `verify-live`** (8.4).
  - **Added the operation-state record,** `scoring_release_log`, beside the gate and outside Alembic. The swap and
    rollback write their entries inside their own transaction.
  - **A swap refuses** unless the newest build-or-verify entry is a clean `verify-build` of the same table (by oid)
    with no match added since, or when the gate is open.
  - **A rollback refuses** while the gate is open, or once a match has arrived after the swap (D11).
- **§1 preflight.** It is `scripts/release_preflight.py`. The gate installer and the swap tool also assert the database
  name.
- **3.7 and 3.14.** "Clean tree" means `webapp/`, tracked and untracked, not the whole repository. The freeze script
  and the exporter both refuse otherwise. The manifest and artifacts name the code that scored; documents outside
  `webapp/` cannot change a score, and the freeze writes its manifest into `docs/`.
- **Swap and rollback re-read the gate under a row lock** after taking their table locks, so the gate cannot be
  opened between the check and the renames.
- **Verified facts that shaped the runbook:**
  - Render's build runs only `pip install` and `alembic upgrade head`. It does not load seed data, so CLAUDE.md is
    stale there, and no deploy writes a gated table.
  - Migrations 0008–0010 downgrade with DDL only, so R2's downgrade is not refused by the gate.
  - `cache_version()` includes `IMPACT_CALCULATION_VERSION`, so prewarm and coverage run from the activation checkout.

### Third external review (C-series), 2026-09-17
Astra reviewed the implementation, the runbook and the evidence, read-only, at 9bac261. It reproduced every hash and
figure independently. Verdict: NOT READY, on eight findings. Dispositions:

| finding | disposition |
|---|---|
| C1 the standalone cache clear can wedge the site (P0) | **Adopted.** A page load holds its cache read lock while its own second connection writes the cache through, so a TRUNCATE queues between them with no deadlock to break it. Swap, rollback and runbook now clear by DELETE, which takes no lock a reader conflicts with, and the "5 seconds" claim is corrected: `lock_timeout` bounds each acquisition, and the swap now takes two rather than three |
| C2 loaded rows not tied to the approved hash (P1) | **Adopted with a correction.** Recomputing the artifact from the table is impossible: two of its 24 columns are diagnostics the table never stores. Verification now hashes the artifact's own bytes against the chain and compares it to the table by key on the other 22 |
| C3 incomplete approvals pass (P1) | **Adopted.** The approved results must name the manifest by digest, be for its candidate and comparator, cover exactly its declared matches with exactly the table's rows, and match the frozen source fingerprints |
| C4 identity misses pooled connections and rollback reuse (P1) | **Adopted.** The identity is claimed on checkout, not on connect; tested on a real QueuePool, which the NullPool helper had hidden |
| C5 report can measure a short artifact (P1) | **Adopted.** Lockstep refuses either file ending first, a moved corpus is refused, and both artifact hashes are recorded |
| C6 verification survives a same-table edit (P1) | **Adopted.** Per-table write counters are recorded at verification and re-checked under the swap's locks |
| C7 SQL NULL and JSON null are conflated (P2) | **Adopted as a documentation fix.** Production holds both (418,535 and 4,330), the driver returns None for both, and nothing reads the difference; the exporter's docstring claimed a distinction the code never made. Changing the renderer would alter a hashed contract mid-chain |
| C8 rehearsal probes can pass while reporting failure (P2) | **Adopted.** The probes roll back and exit nonzero unless the gate behaved exactly as stated |

It also caught that the background prewarm from 8.6 can still be running during a rollback, writing rc3-derived cache
rows over restored v1 scores; the runbook stops it first now. Its UNVERIFIED list is fair: it could not run the tests
or observe real lock timing, which is what Stage 6 is for.

All eight are fixed in 3f7ea3e..0b69621, each with a test shown to fail on a value when its guard is broken. The
suite after them: 3.11 offline 1,251 passed / 63 skipped, 3.13 with the database tests 1,301 passed / 13 skipped,
0 failed. No scoring source changed (`git diff 5c369f8 HEAD -- webapp/app/scoring webapp/app/models` is empty and the
exporter's diff is its docstring), so K3 and OFF(B) stand and K4 and K5 are still expected to equal K1'.

### Remaining
Stage 3 is complete. Next: the owner confirms the lock from the last look, then the Stage 4 RESULT entry (from K3 and
OFF(B)), the rehearsal restore, and Stage 5's freeze.
