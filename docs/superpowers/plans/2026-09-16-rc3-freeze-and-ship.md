# rc3 freeze-and-ship plan: A 1.0 / B 2.5 / C 2.5 / D 100, trade credit ON

> **SUPERSEDED AS A RUNBOOK (2026-09-16).** After Astra's external review
> (`2026-09-16-rc3-plan-astra-review.md`), the owner adopted stage-and-swap, a database scoring gate, keyed artifacts
> and a stored-v1 last look. **Follow `2026-09-16-rc3-ship-plan-v2.md`.** This file stays the source for its verified
> findings, the credit-ON measurement and details v2 does not change. Its batched-writer backfill, suspended-site
> window and K1/K2 rollbacks must not be used.

**Status: PLAN ONLY.** Written 2026-09-16 by a planning pass from `HANDOFF-rc3-ship-to-site.md`, then checked by
the main session against the code, git and the live Render DB (read-only). Nothing in it has been executed: nothing
is committed, frozen, rescored, activated or deployed.

## Owner decisions (2026-09-16) — these override the plan body where they differ

1. **Lock confirmed as-is** after seeing the credit-ON numbers: A 1 / B 2.5 / C 2.5 / D 100, credit ON, scale 1.0.
   The D2 re-confirmation gate is satisfied.
2. **Add a `trade_credit` column** (new migration **0010**, smallint), which overrides Decision 3's "no migration".
   It adds a Phase C step (model column, `_PERSISTED_FIELDS` in `impact.py`, migration, tests), and 0010 joins
   0008/0009 in H4 and the rollback runbook. The batched writer and the acceptance replay pick the column up through
   `PERSISTED_FIELDS`. It must land **before** the freeze (correction 4).
3. **Python 3.13**, because refreshes run on more than one machine. This overrides Decision 8's "freeze under 3.11".
   Python 3.13 is **not installed** on this machine (only 3.11.4, 3.10 and 3.9 are). Install it and rebuild
   `webapp\.venv` before C12; freeze, test and replay under 3.13.
4. **Ingestion stays frozen until activation** (A3 as written). The declared corpus is the current 3,125 matches.
5. Recommendations the owner did not override: split PR #67 and merge with merge commits; rehearse on a scratch
   database on the Render instance (correction 1); use the batched backfill writer; leave `FormulaWeights` defaults
   unchanged; add the C10 preflight and the C11 test guard.

**Phase B status:** the predeclared ledger entry was drafted on 2026-09-16 and appended to
`docs/superpowers/2026-09-07-predeclared-values.md`. It is **uncommitted, pending the owner's review**. It declares
the Python 3.13 replay must reproduce the 3.11 figures exactly.

Read **"Main-session verification"** first: it records the credit-ON measurement that the plan marks as pending,
and **four corrections** to steps below (marked inline with ⚠).

---

## Main-session verification (2026-09-16)

### Credit-ON corpus measurement — COMPLETE

Read-only, all **3,125 matches** (ids 1–3133, identical id set to the credit-OFF run), 659,500 player-rounds,
A=1 / B=2.5 / C=2.5 / D=100, `trade_credit_scale` 1.0, rc2 econ model, `use_realized_swing`. 15.7 min, 0 failures.
Script `/tmp/bc25/run_on.py` (Bash path); data `C:\Users\Public\Documents\Wondershare\CreatorTemp\bc25on\`;
report `report_on.py` from that session's scratchpad (OFF column reproduced every recorded baseline exactly before
any ON figure was trusted).

| | credit OFF | credit ON |
|---|---:|---:|
| corpus player-match magnitude share: damage / leverage / econ / assists | 43.5 / 37.7 / 8.9 / 9.9 | **41.7 / 40.3 / 8.5 / 9.5** |
| median player-match share (≥12 rounds, n=30,990) | 45.6 / 34.6 / 7.6 / 9.7 | **43.7 / 37.3 / 7.3 / 9.3** |
| impact/round mean / sd | 206.0 / 604.8 | **233.6 / 613.6** |
| impact rank = K−D rank / within one place | 45.4% / 80.8% | **41.1% / 76.9%** |

- Integrity: damage, econ and assists identical OFF vs ON on every row; impact delta = leverage delta on every row;
  impact = sum of four terms on every row; zero negative-damage rows; no player-round loses leverage.
- Credit total 18,204,413 = **15.1% of positive leverage** on the credit-OFF basis (120,812,285 — the basis the
  2026-09-14 RESULT used; 13.5% on the ON basis). 105,669 player-rounds credited (16.0%), mean 172.3 each.
  Net corpus leverage 30,479,853 → 48,684,266. Consistent with the 09-14 RESULT rescaled B=3 → 2.5 (18,197,953) plus
  match 3133's rows.
- Ranks: impact order changes in 2,262 / 3,125 matches (72.4%, mostly adjacent swaps); **#1 changes in 222 (7.1%)**;
  6,884 / 31,250 player-matches change rank (|shift| 1: 6,055, 2: 722, 3: 97, 4: 10); 29,892 player-matches go up,
  1,358 unchanged, 0 down.
- At B=2.5 damage still leads leverage (41.7 vs 40.3); at B=3 on 09-14 leverage led.
- Corpus note: the 2026-09-14 RESULT covered 3,124 matches / 659,290 player-rounds (match 3133 excluded, see below).

### Claims in this plan verified by the main session

- The 210-row gap is exactly match 3133 (Haven 13–8, 21 rounds × 10 players, `created_at` 2026-09-10 07:18 UTC):
  zero `impact_scores` rows; every other match complete; zero orphan impact rows; `round_player_stats` = 659,500.
- `origin/main` is at `IMPACT_CALCULATION_VERSION = 1` with `round(damage_and_assists * 1.25)` hard-coded; the
  branch is at 2. Stored rows are v1.
- `verify_manifest` compares Python major.minor exactly; venv is 3.11.4; the rc2 manifest records `"python": "3.13"`;
  `render.yaml` pins 3.13.5. **No web request path loads the manifest** — the only caller of
  `active_scoring_config()` in `app/` is `compute_impact_for_match` (`impact.py:1577`), and verification is lazy
  (`impact_runtime.py`), so the pin constrains the ingestion environment, not the Render web process.
- 0008 adds the three columns `nullable=False` and then drops `server_default` — after it is applied, a pre-merge
  checkout's scoring fails on NOT NULL.
- `backfill_impact_candidate.py` defaults to `compute_impact_for_match` (per-row path), refuses (exit 3) when other
  sessions are connected, clears all player caches and site stats at start (`:183`) and exit (`:258`), walks every
  match id (`:163`), and accepts by replay (`:219`).
- `release_candidate_review.py:584` checks the three-term identity (no `assists_component`).
- `test_wrapper_still_persists_and_commits` commits through `SessionLocal` to whatever `DATABASE_URL` resolves to.
- `site_stats.py` / `site_stats_cache.py` never read impact rows.
- PR #67: OPEN, MERGEABLE, CLEAN, head `7f4a63b`.
- Round-trip time from this machine to Render: 65 ms median `SELECT 1`, 66 ms median PK lookup on `impact_scores`
  (n=300) — 659,500 per-row lookups alone ≈ 12.1 h. Agrees with finding 1.
- The DB role is `conor`, `rolcreatedb = true`, not superuser. Databases on the instance: `valowithfriendsdb`
  251 MB, `postgres` 7.7 MB.

### ⚠ Corrections to the plan below

1. **There is no local Postgres server: Phase E2, G and F2's "local copy" cannot be built as written.**
   `C:\Program Files\PostgreSQL\18\` has no `share\` directory (the install used `--disable-components server`), so
   `initdb.exe` fails with "postgres.bki does not exist" even though `initdb.exe`/`pg_ctl.exe`/`postgres.exe` are
   present. Finding 6 and contradiction 4 are wrong. Options:
   - **(recommended) A scratch database on the same Render instance**, e.g. `CREATE DATABASE valo_rc3_rehearsal`,
     restored from B0 with `pg_restore`. The role has CREATEDB and the DB is 251 MB. This is how migration 0006 was
     validated. It also fixes correction 2, because the rehearsal pays the same network cost as the real window.
     Set `$MIRROR` to the `$PROD` URL with only the database name replaced, and keep the `$COUNTS` preflight
     (`current_database()` is its first line): the two URLs differ by one path segment. Run heavy rehearsal steps at
     a quiet time; they share the production instance's CPU and IO. Drop the scratch DB afterwards.
   - EDB's no-installer binaries zip (includes `share\`) for a true local server, or modify the existing install to
     add the server component. Either needs a download/installer run by the owner.
2. **A local rehearsal cannot calibrate the maintenance window.** Against a zero-latency mirror, G3/G5 wall-clock
   times omit the ~62–65 ms per round trip that dominates Phase I. Calibrate from the SQL statement count per match
   (C9's `before_cursor_execute` counter) × measured round-trip time, or rehearse on the Render scratch DB
   (correction 1). The I5 abort rule's "rate the rehearsal predicts" must be the network-inclusive prediction.
3. **B1's "commit the entry before anyone opens the credit-ON run's output" can no longer be met.** The full credit-ON
   output was computed and shown to the owner in the session of 2026-09-16, before any rc3 declaration. The
   predeclared entry must say so, citing the figures as "measured on the corpus before this entry (read-only)" — the
   convention the 2026-09-14 trade-credit entry already uses — and must not present them as predeclared results.
4. **Adding a persisted column after the freeze is not "a ~1-hour window" (Decision 3).** `_PERSISTED_FIELDS` is
   defined in `app/scoring/impact.py:1552`, and `impact.py` is the first entry in `HASHED_SOURCES`
   (`impact_manifest.py:38-39`). Persisting `trade_credit` or `assists_component` later changes that digest, so the
   active rc3 manifest stops verifying — `active_scoring_config()` raises and ingestion cannot score — until a new
   candidate is frozen, reviewed and activated, plus the backfill window. Decide Decision 3 before C2, not after.

### Other facts from the same session

- **Ingesting from this branch against production fails today, after a partial write.** Both ingest scripts call
  `backfill_unscored_matches` first, outside any `try` (`refresh_tracked_players.py:55`,
  `ingest_trackergg_player.py:48`). It commits deletes of `site_stats_cache` and the stranded match's players'
  `player_view_cache` rows, then `compute_impact_for_match` fails on its first `ImpactScore` SELECT because production
  lacks the 0008 columns. `load_match` would also fail (`round_player_spend` does not exist). From `origin/main`
  (0007, no new columns, no spend writes) the refresh works and would score 3133 with the v1 formula.
- Nothing has been ingested since 2026-09-01 except 3133. Id 3132 was never committed (a rolled-back insert).
- The live site (checked via `https://valowithfriendstracker.onrender.com` — the sandbox cannot resolve the custom
  domain) returns 200 for 3133's match and round pages, `/matches`, `/sessions`, `/squad`, four player pages and
  `/stats`. `/stats` took 40 s with `site_stats_cache` empty.
- Those GETs refilled caches the normal way: `site_stats_cache` 0 → 1, `player_view_cache` 4,479 → 4,482.

---

The plan as returned by the planning pass follows, unedited except for the ⚠ markers.

Tags: **[V]** verified (file:line or live output) · **[A]** assumed · **[E]** estimate from verified inputs.
Paths are relative to `C:\Users\Conor Lum\Documents\GitHub\valo-with-friends-tracker\`.

## 0. Seven findings that change the picture

1. **The documented backfill takes ~23 hours over this connection.** The rc2 runbook assumes it is quick. It issues
   one `SELECT` per player-round (`webapp/app/scoring/impact.py:1579-1592`) [V]. Writes flush at commit
   (`autoflush=False`, `webapp/app/db.py:22`) through psycopg2's `cursor.executemany`, which is one round-trip per row:
   SQLAlchemy 2.0.35 defaults to `executemany_mode="values_only"`
   (`webapp/.venv/Lib/site-packages/sqlalchemy/dialects/postgresql/psycopg2.py:638,784-794`) [V]. Measured round-trip
   time to Render: median 62.4 ms (range 59–129, 21 samples) [V]. Per match that is 0.32 s to build the rows (16.7 min
   ÷ 3,125) plus 211 rows × 2 × 62 ms ≈ 26.5 s. Across 3,125 matches: **~23 h with the site down**, plus ~22 min of
   acceptance replay [E].
2. **The rc2 tooling cannot freeze or review rc3 unchanged** [V].
   - `verify_manifest` rejects any comparator named in `COMPARATORS` whose values differ from the code's
     (`webapp/app/scoring/impact_manifest.py:304-305`). All code comparators use default weights, so rc3 cannot reuse
     the `…bonus_denial` comparator name with new weights.
   - `build_manifest` hard-codes "weights: A(damage)=1.25, B=1.0, C=1.0" and "trade discount: unchanged" (`:250-251`).
   - All five rc2 reproduction commands would exit 1 for rc3. The site, fixed-ten and trace reviews call a three-term
     `impact == damage + leverage + econ` identity (`webapp/scripts/release_candidate_review.py:584`, reached via
     `:667-668`, `:761`, `:954`), which D=100 breaks on every row with an assist. `compare_econ_models.py` demands
     identical non-econ fields between comparators whose weights differ (`:75-80`).
   - rc2 was frozen with an uncommitted scratchpad script (`docs/superpowers/plans/2026-09-12-bonus-denial-review-fixes.md:236`).
3. **`main` is at `IMPACT_CALCULATION_VERSION = 1`** with an older legacy damage formula
   (`git show origin/main:webapp/app/scoring/impact.py`, lines 43, 618, 638) [V]. The stored rows came from that code
   (see also `impact.py:98-101`). Merging PR #67 alone changes the legacy formula that ingestion uses. It also moves
   the player-page cache version from `4002003001` to `4003003002` (state-diagram version 2→3, impact version 1→2),
   which invalidates every cached player page [V].
4. **Python version pin.** The local venv is **3.11.4**, and only 3.11 and 3.10 are installed. rc2 was frozen under
   3.13, and Render runs 3.13.5. `verify_manifest` compares major.minor (`impact_manifest.py:128,282-285`) [V]. The
   Render web process never scores: no router or `main.py` path ingests [V]. So an rc3 manifest frozen here ties
   **all future scoring to Python 3.11**.
5. **The test suite can write to production.** `webapp/tests/test_impact_exante_swing.py:47-57,121-134` connects
   through `app.db.SessionLocal` and calls `compute_impact_for_match`, which commits [V]. Once 0008 is on Render,
   running pytest in a window where `DATABASE_URL` points at Render would rescore and commit a real match.
6. ⚠ **WRONG — see correction 1.** ~~**A local copy of production is possible without Docker.** The PostgreSQL 18
   folder also contains the server (`initdb.exe`, `pg_ctl.exe`, `postgres.exe`). No local Postgres service is running,
   ports 5432/5433 are free, and C: has 46.9 GB free [V].~~
7. **Cache-rebuild trap.** The backfill deletes every `player_view_cache` row when it starts and when it exits
   (`webapp/scripts/backfill_impact_candidate.py:119-122,183,257-258`; `player_view_cache.py:619-624`) [V]. Afterwards
   `recompute_player_views.py --cached-only` finds nobody to rebuild. The unflagged version walks all **20,784**
   players who have match rows (`recompute_player_views.py:26-31`; live count) [V].

## 1. The plan

### Phase A — Decisions and preconditions (item 1)

- **A1.** The owner confirms in writing, for the ledger: weights A 1.0, B 2.5, C 2.5, D 100; `trade_credit_scale`
  1.0; `enable_trade_credit` True; `enable_econ_component` True; `econ_model` `buy_disruption_v2_30_80_bonus_denial`;
  `use_realized_swing` True; timing candidates off. The weights were chosen from credit-OFF shares, and turning credit
  on shifts share into leverage (+2.6 points at B=3; ledger `docs/superpowers/2026-09-07-predeclared-values.md:1679`),
  so there is an explicit re-confirmation gate at D2.
- **A2.** The owner takes the decisions in section 5.
- **A3.** **Freeze ingestion from now until J5.** Nothing has been ingested since match 3133, created 2026-09-10
  07:18 UTC; it is the only match created after 2026-09-09 [V].
- **A4.** Never run pytest in a PowerShell window where `$env:DATABASE_URL` is set (finding 5).

### Phase B — Predeclared ledger entry (item 3)

- **B1.** ⚠ *See correction 3: the full credit-ON output has already been seen.* Commit the entry **before anyone
  opens the credit-ON run's output**. It must disclose that B and C were chosen after a credit-OFF corpus run at those
  same weights, that a partial credit-ON run (~2000/3125 matches, in `bc25on\`) exists from 2026-09-15, and whether
  the full credit-ON output had already been seen.
- **B2.** Append to `docs/superpowers/2026-09-07-predeclared-values.md` (never edit old entries):
  - **Values:** ~~B = 3.0~~ → B = 2.5; ~~C = 2.347~~ → C = 2.5; A = 1 and D = 100 unchanged. Trade credit is adopted
    ON (owner, 2026-09-16), with `trade_credit_scale` 1.0 applied before B, using the 2026-09-14 schedule and split.
  - **Identity:** structure, econ model, timing flags, candidate id (e.g. `impact-rc3`), folder
    `docs/superpowers/impact-rc3/`, activation at version 3.
  - **Declared RESULT measurements** (credit ON, every match present at the freeze, credit-OFF baseline alongside):
    1. corpus and median player-match term shares;
    2. credit as a share of positive leverage, credited deaths, player-rounds touched, credit by trade second;
    3. impact per round, mean and standard deviation; count of negative-damage player-rounds;
    4. sanity checks: 10 players per match, round-count profile, agreement with K−D rank (exact and within one);
    5. **exact checksums** — row count plus the sums of `impact`, `damage`, `econ_component`, `time_impact`,
       `kill_impact`, `death_impact`, `kill_order_bonus`;
    6. the largest absolute value in each smallint column (`damage` through `econ_pickup` are `SmallInteger`,
       `webapp/app/models/impact_score.py:53-80`).
  - **Declared review checks:** the list in F4. No adoption threshold, as usual.

### Phase C — Implementation on `impact-scoring-impl`, test-first (items 2, 4)

For every step: write the failing test, implement, then **break the implementation on purpose and confirm the test
fails on a value** (not a TypeError).

- **C1. Commit the existing uncommitted diff** after its mutation check: remove `* weights.trade_credit_scale`
  (`impact.py:1452-1455`); `test_trade_credit_scale_multiplies_the_credit_independent_of_leverage` must then fail on a
  value. Nothing records whether this check was already done [A].
- **C2. Put the locked weights in code as a declared comparator, not as defaults.** In `impact_manifest.py:72-87`,
  add `RC3 = "impact_rc3"` to `COMPARATORS`:
  ```
  ImpactScoringConfig(RC3, enable_econ_component=True,
      econ_model=bd.MODEL_V2_30_80_BONUS_DENIAL,
      weights=FormulaWeights(1.0, 2.5, 2.5, 100.0, 1.0),
      enable_trade_credit=True)
  ```
  Tests: (a) a manifest built with `release_comparator=RC3` loads exactly these values; (b) the same manifest with
  `enable_trade_credit` deleted fails `verify_manifest` with "does not match its declared identity". Mutation: setting
  `enable_trade_credit=False` in code makes (a) fail. This closes the brief's missing-key hole, which exists only for
  comparators *not* declared in code: `config_from_dict` defaults the flag to False (`:161-162`) and the identity
  check is skipped for them (`:304`) [V].
- **C3. Leave the `FormulaWeights` defaults alone** (reasons in section 2). Fix only the misleading docstring at
  `impact.py:185` (docstrings are excluded from the source hash, `impact_manifest.py:104-111`) [V]. Add a guard test:
  `FormulaWeights() == FormulaWeights(1.25, 1.0, 1.0, 0.0, 1.0)`.
- **C4. Make `build_manifest` describe what it freezes.** Derive the weights line from the release comparator
  (A, B, C, D, scale) and add a trade-credit line when it is on. Test the text; mutate by restoring the hard-coded line.
- **C5. Record the trade credit schedule readably.** Add `TRADE_CREDIT_SCHEDULE` (`impact.py:390-397`) to
  `current_code_identity()["trade"]` (`impact_manifest.py:131-132`). Today only the `impact.py` source hash covers it.
- **C6. Fix the review tool** (`release_candidate_review.py`): four-term identity at `:584` (add
  `assists_component`); `_identity_block` (`:651-659`) prints D, the credit flag and the scale; `player_match_rows`
  adds up assists and credit; `--weights` (`:1196-1210`, which can only set A/B/C) refuses manifests with D≠0 or
  credit on. Mutation: going back to three terms must produce failures.
- **C7. Replace `compare_econ_models.py` for rc3** with a new read-only `webapp/scripts/compare_rc3_decomposition.py`.
  It checks that raw econ under rc3 equals raw econ under the rc2 configuration, and that the rc3 terms equal
  A·damage_raw, B·(leverage_raw + credit), C·econ_raw and D·assists after the scorer's own rounding. It exits 1 on
  any mismatch.
- **C8. A committed freeze script**, `webapp/scripts/freeze_impact_candidate.py`, replacing rc2's scratchpad one: it
  refuses a dirty `webapp/app` tree; sets `scorer_revision` to HEAD; fingerprints the declared matches in one
  `REPEATABLE READ, READ ONLY` transaction; records match count, max match id, `txid_current_snapshot()` and the
  database name (never the host); writes through `build_manifest` and `write_manifest`. Tests use the sqlite fixtures.
- **C9. A batched backfill writer** (Decision 4), passed through the existing `compute=` hook
  (`backfill_impact_candidate.py:135-138`; this file is not in the source hash) behind a `--batched` flag. Per match:
  one replay, one multi-row upsert of all `PERSISTED_FIELDS` keyed on `(round_id, match_player_id)`, one commit. It
  must **not delete** rows the replay does not produce, so acceptance still reports "unexpected rows". Tests: (a) the
  stored result equals `compute_impact_for_match` field by field, both for a match with no existing rows (like 3133)
  and for a match with legacy rows to update; (b) a per-match cap on SQL statements, counted with SQLAlchemy's
  `before_cursor_execute` event. Mutations: drop `trade_detail`, then `kill_order_bonus`, from the upsert.
- **C10. Ingestion preflight** (Decision 9). In `webapp/scripts/refresh_tracked_players.py` before
  `backfill_unscored_matches` (`:55`), and in the adapter before `load_match`, refuse unless: `alembic_version` equals
  the checkout's migration head; `db.query(ImpactScore).limit(1).all()` succeeds; `active_scoring_config()` resolves
  without raising.
- **C11. Guard the live-database tests** (Decision 10). The `SessionLocal` fixtures in `test_impact_exante_swing.py`,
  `test_impact_reconstruction.py`, `test_kill_order_leverage_gates.py` and `test_kill_order_stage_c0.py` skip unless
  the database host is localhost.
- **C12. Run the full suite.** Only the known-red tests may fail:
  - `test_happy_path_blob_validates` — a stale fixture: its blob has 3 of the 9 required top-level keys
    (`tests/test_site_stats_cache.py:24-32` vs `site_stats_cache.py:369-380`) [V]. Unrelated to scoring.
  - `test_builder_matches_stored_values` — compares the branch's v2 legacy replay against v1 rows in the `.env` Neon
    database [V]. Unrelated.
  - `test_wrapper_still_persists_and_commits` — only until C11 makes it skip.

  Then push, which updates PR #67.

### Phase D — RESULT entry (item 3)

- **D1.** ~~The main session's credit-ON run finishes (**pending**).~~ Done 2026-09-16 (see verification section).
  Compute every B2 measurement over the matches present at the freeze.
- **D2.** The owner re-confirms the locked weights using the credit-ON numbers. Any change means a new declaration and
  a return to C2.
- **D3.** Append the RESULT entry: the figures, the credit-OFF baseline, the checksums and the smallint maxima. Commit.

### Phase E — Backup B0 and a Docker-free local copy (item 6)

Start every session in a **new PowerShell window** in `...\valo-with-friends-tracker\webapp`. Never echo `$PROD`.
```powershell
$PG = "C:\Program Files\PostgreSQL\18\bin"
$BK = "C:\Users\Conor Lum\Documents\valo-backups"      # outside the public repo
$R  = "C:\Users\Conor Lum\Documents\valo-rehearsal"
$PROD = ((Select-String -Path .env.remote -Pattern '^DATABASE_URL=' | Select-Object -First 1).Line -replace '^DATABASE_URL=', '').Trim()
$MIRROR = "postgresql://postgres@localhost:55432/valowithfriendsdb"   # ⚠ correction 1: no local server exists
$M  = "..\docs\superpowers\impact-rc3\candidate-manifest.json"
$RR = "..\docs\superpowers\impact-rc3\review-results.json"
$COUNTS = "SELECT 'db', current_database()::text UNION ALL SELECT 'alembic', version_num::text FROM alembic_version UNION ALL SELECT 'matches', count(*)::text FROM matches UNION ALL SELECT 'max_match', max(id)::text FROM matches UNION ALL SELECT 'rounds', count(*)::text FROM rounds UNION ALL SELECT 'round_player_stats', count(*)::text FROM round_player_stats UNION ALL SELECT 'kill_events', count(*)::text FROM kill_events UNION ALL SELECT 'impact_scores', count(*)::text FROM impact_scores UNION ALL SELECT 'impact_sum', coalesce(sum(impact),0)::text FROM impact_scores UNION ALL SELECT 'player_view_cache', count(*)::text FROM player_view_cache UNION ALL SELECT 'site_stats_cache', count(*)::text FROM site_stats_cache"
$ACCEPT = "SELECT (SELECT count(*) FROM impact_scores) AS impact_rows, (SELECT count(*) FROM round_player_stats) AS stat_rows, (SELECT count(*) FROM round_player_stats s LEFT JOIN impact_scores i ON i.round_id = s.round_id AND i.match_player_id = s.match_player_id WHERE i.round_id IS NULL) AS unscored, (SELECT count(*) FROM impact_scores i LEFT JOIN round_player_stats s ON s.round_id = i.round_id AND s.match_player_id = i.match_player_id WHERE s.round_id IS NULL) AS orphans, (SELECT count(*) FROM impact_scores WHERE econ_impact <> 0 OR swing_impact <> 0) AS legacy_rows, (SELECT count(*) FROM impact_scores i JOIN rounds r ON r.id = i.round_id WHERE r.match_id = 3133) AS m3133, (SELECT sum(impact) FROM impact_scores) AS impact_sum, (SELECT sum(damage) FROM impact_scores) AS damage_sum, (SELECT sum(econ_component) FROM impact_scores) AS econ_sum, (SELECT sum(time_impact) FROM impact_scores) AS time_sum, (SELECT count(*) FROM player_view_cache) AS pvc, (SELECT count(*) FROM site_stats_cache) AS ssc"
& "$PG\psql.exe" -d $PROD -At -F "," -c $COUNTS    # PREFLIGHT before every block: db=valowithfriendsdb, matches 3125, max_match 3133
```

**E1. Backup** (after the credit-ON run has finished; the same block is reused for B1):
```powershell
New-Item -ItemType Directory -Force $BK | Out-Null
$name = "prod-B0-" + (Get-Date -Format "yyyyMMdd-HHmm")
& "$PG\pg_dump.exe" -d $PROD --format=custom --no-owner --no-privileges --file "$BK\$name.dump"
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
& "$PG\psql.exe" -d $PROD -At -F "," -c $COUNTS | Out-File -Encoding ascii "$BK\$name.counts.csv"
```

**E2. Build the local copy and verify the backup by restoring it.** ⚠ *Correction 1: `initdb` cannot run on this
install (no `share\`). Restore into a scratch database on the Render instance instead (`CREATE DATABASE
valo_rc3_rehearsal`, then `pg_restore -d <that URL>`), or install full server binaries first.*
```powershell
New-Item -ItemType Directory -Force $R | Out-Null
& "$PG\initdb.exe" -D "$R\data" -U postgres -A trust -E UTF8                       # first time only
& "$PG\pg_ctl.exe" -D "$R\data" -o "-p 55432" -l "$R\postgres.log" start
& "$PG\psql.exe" -d "postgresql://postgres@localhost:55432/postgres" -c "DROP DATABASE IF EXISTS valowithfriendsdb" -c "CREATE DATABASE valowithfriendsdb"
& "$PG\pg_restore.exe" -d $MIRROR --no-owner --no-privileges --jobs 4 "$BK\$name.dump"
Compare-Object (Get-Content "$BK\$name.counts.csv") (& "$PG\psql.exe" -d $MIRROR -At -F "," -c $COUNTS)   # no output = backup verified
```

### Phase F — Freeze rc3 and its review (item 4; adapted from `docs/superpowers/econ-bonus-denial-candidate-rc2/README.md`)

**F1. Freeze** from the committed Phase C state (clean tree, Python 3.11), reading production read-only. Declared
matches: 3104 (Abyss), the fixed ten, 3120, and **3133**. All 12 of rc2's review matches still have the same
fingerprints on production (12/12) [V live].
```powershell
$env:DATABASE_URL = $PROD
.\.venv\Scripts\python.exe -X utf8 scripts\freeze_impact_candidate.py --candidate-id impact-rc3 --release-comparator impact_rc3 --activation-version 3 --matches 3104,3129,3130,3131,3113,3118,3121,3114,3115,3116,3117,3120,3133 --out $M    # CLI defined in C8
Remove-Item Env:\DATABASE_URL
```
Commit the manifest on its own and **never edit it afterwards**: `review-results.json` is tied to the manifest's hash
(`backfill_impact_candidate.py:93-94`) [V]. Record approval in the ledger or README, not in the manifest.

**F2. Reviews against the local copy** (⚠ *correction 1: the rehearsal copy*); the fingerprint check
(`release_candidate_review.py:1231-1233`) proves it matches production for these matches.
```powershell
$env:DATABASE_URL = $MIRROR
$D = "..\docs\superpowers\impact-rc3"
.\.venv\Scripts\python.exe -X utf8 scripts\release_candidate_review.py --manifest $M --match 3104 --compare site --report-dir $D
.\.venv\Scripts\python.exe -X utf8 scripts\release_candidate_review.py --manifest $M --ten --compare site --report-dir $D
.\.venv\Scripts\python.exe -X utf8 scripts\release_candidate_review.py --manifest $M --match 3120 --trace 3120 --report-dir $D
.\.venv\Scripts\python.exe -X utf8 scripts\compare_rc3_decomposition.py --manifest $M --report-dir $D
.\.venv\Scripts\python.exe -X utf8 scripts\release_candidate_review.py --manifest $M --ten --extra 3104 --extra 3120 --extra 3133 --results $RR
```
No `--corpus` (the rc2 README's reason still applies). The "site" (before) column is the stored v1 `impact`, read with
plain SQL (`:180-188`) [V].

**F3. Tie the RESULT to the frozen configuration.** On the rehearsal copy, replay every match using
`config_from_manifest(load_manifest($M))`, not the scratch script's explicit weights. The B2 checksums and shares must
match the RESULT **exactly**; any difference blocks activation.

**F4. Declared checks**, recorded in `SUMMARY.md` in rc2's format:
1. Fingerprints match for 13 of 13 matches.
2. `verify_manifest` passes and every configuration value is exact.
3. F3 matches exactly, with zero input failures, the four-term identity on every row, zero negative damage, and
   smallint maxima in range.
4. The C7 decomposition check is clean.
5. The site reviews reconcile.
6. Deliberately reinstated defects, in an isolated copy, are each caught: credit flag dropped from the manifest; scale
   ignored; B and C swapped; D left out of `impact`; credit paid to the trader instead of the traded player; credit
   schedule reversed; the three-term reconciliation; the batched writer omitting a field; the batched writer deleting
   rows; activation without the version bump.
7. `review-results.json` covers 13 matches.
8. The test suite matches C12.
9. The Phase G rehearsal passed.

**F5.** Write `README.md` (rc2's structure, every `docker` step replaced by the E/G/I/K commands, plus the Python 3.11
pin, the preflight, the pytest warning and the cache capture) and `SUMMARY.md`. Add an "rc3 review RESULT" ledger
entry. The owner approves.

### Phase G — Rehearsal on the local copy (items 5–7)

⚠ *Corrections 1 and 2: rehearse on the Render scratch database; timings from a zero-latency copy do not transfer.*

- **G1.** Restore B0 into the rehearsal copy (E2); `$env:DATABASE_URL = $MIRROR`.
- **G2.** From the exact commit PR #67 will merge: `Measure-Command { .\.venv\Scripts\python.exe -m alembic upgrade
  head | Out-Host }`, expect alembic 0009. Then dump the copy to `$R\mirror-B1.dump` (as E1, with `-d $MIRROR`).
- **G3.** From the activation branch (I1), print `.\.venv\Scripts\python.exe -c "from app.scoring.impact_runtime
  import active_scoring_config; print(active_scoring_config())"`, then:
  ```powershell
  Measure-Command { .\.venv\Scripts\python.exe -X utf8 scripts\backfill_impact_candidate.py --manifest $M --state "$R\state-rehearsal.json" --confirm-maintenance-window --approved-results $RR --batched | Out-Host }
  & "$PG\psql.exe" -d $MIRROR -x -c $ACCEPT
  ```
  Expected: exit 0; `impact_rows` = `stat_rows` = 659,500; `unscored` 0; `orphans` 0; `legacy_rows` 0 (rc3 writes
  `econ_impact`/`swing_impact` as 0, `impact.py:1522-1530`); `m3133` 210; sums equal the RESULT checksums; `pvc` 0;
  `ssc` 0.
- **G4.** `uvicorn app.main:app` against the rehearsal copy: match 3104's per-player totals equal the AFTER column of
  `match-3104-site.md`. Time the roster prewarm (I7).
- **G5.** Rehearse rollback K1 against `$MIRROR` using `$R\mirror-B1.dump`. Record every duration in the README; they
  calibrate the Phase I estimate (⚠ *correction 2*).

### Phase H — Merge PR #67 **without activation** (items 5, 10)

- **H1.** PR #67 contains Phases C–F but no activation; the owner has approved F5.
- **H2.** Ingestion still frozen; no long-running transactions (nothing older than a few seconds):
  ```powershell
  & "$PG\psql.exe" -d $PROD -c "SELECT pid, application_name, state, now() - xact_start AS xact_age FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND xact_start IS NOT NULL"
  ```
- **H3.** Re-run `$COUNTS`; if it differs from B0's `.counts.csv`, repeat E1.
- **H4. Apply 0008 and 0009 deliberately**, from the exact commit to be merged. Alembic runs both in one transaction
  (`webapp/alembic/env.py:38-40`) [V]. The lock timeout keeps a lock queue from stalling the live site [A: libpq
  honours `PGOPTIONS`].
  ```powershell
  $env:DATABASE_URL = $PROD; $env:PGOPTIONS = "-c lock_timeout=10s"
  .\.venv\Scripts\python.exe -m alembic upgrade head
  Remove-Item Env:\PGOPTIONS; Remove-Item Env:\DATABASE_URL
  & "$PG\psql.exe" -d $PROD -At -c "SELECT version_num FROM alembic_version"      # 0009
  ```
  The current `main` code keeps working: the new columns are additive and the web process never writes
  `impact_scores` [V].
- **H5.** Merge with **"Create a merge commit"** and keep the branch. Squash and rebase merges are enabled on this repo
  [V] and would leave `scorer_revision` pointing at an unreachable commit. Render's build then runs
  `alembic upgrade head` as a no-op [A].
- **H6. Verify:** Render shows the merge commit deployed; `/health` ok; a player page and a match page load. Update
  local main (`git -C .. switch main; git -C .. pull`; local `main` is currently 141 commits behind [V]). Confirm the
  manifest still verifies: `.\.venv\Scripts\python.exe -c "from app.scoring.impact_manifest import load_manifest,
  verify_manifest; verify_manifest(load_manifest(r'..\docs\superpowers\impact-rc3\candidate-manifest.json'));
  print('verifies')"` — it will, because `main`'s two extra commits change nothing under `webapp/` [V].

**What the site serves between H and I:** the branch code with `ACTIVE_MANIFEST=None`; unchanged impact numbers
(stored v1 rows, new columns at 0); a cache miss on every player page (cache version 4003003002), showing the branch's
state-diagram and fight-EV changes; match 3133 still unscored. **Don't prewarm caches in this interval** — activation
invalidates them again. **Keep ingestion frozen:** a checkout from before the merge would now fail with a NOT NULL
error on insert (0008 drops the column defaults,
`webapp/alembic/versions/0008_econ_component_and_kill_order_bonus.py:54,59`) [V], and the branch's v2 legacy formula
differs from the stored v1 rows.

### Phase I — Maintenance window: activation, backfill, caches (items 5, 7, 8)

**I1. The activation PR** (prepared earlier and rehearsed in G3) is one commit off the merged `main`:
- `ACTIVE_MANIFEST = "docs/superpowers/impact-rc3/candidate-manifest.json"` (`impact_runtime.py:25`).
- `IMPACT_CALCULATION_VERSION = 3`, with a history comment (`impact.py:92-102`); this assignment is excluded from the
  source hash (`impact_manifest.py:112-117`) [V].
- Replace two tests that would otherwise fail on activation [V] with rc3 versions asserting the manifest path, the
  configuration values and version 3: `test_runtime_default_is_the_live_legacy_formula`
  (`tests/test_impact_manifest.py:164-166`) and `test_default_persistence_path_is_unchanged_legacy`
  (`tests/test_impact_buy_disruption_integration.py:311-319`).
- Full suite: only the known-red tests fail.

**I2.** Merge it with a merge commit and wait until Render shows the deploy live [A]. Until I3, v3 code is rendering v1
rows; anything it caches in that time is deleted by the backfill [V].

**I3. Take the site offline.** Suspend the web service (Render dashboard → service → Settings → Suspend) [A: UI path];
close any psql or pgAdmin windows; then this must return **0 rows**:
```powershell
& "$PG\psql.exe" -d $PROD -c "SELECT pid, usename, application_name, backend_type, state FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()"
```
Five client sessions were connected during the planning probe, so the backfill would refuse today (exit 3, nothing
written; `backfill_impact_candidate.py:159-161`) [V].

**I4. Backup B1** (E1 with `$name = "prod-B1-" + ...`) — the rollback point: schema 0009 with the v1 rows. Then,
**before the backfill empties the cache**, capture which players to prewarm:
```powershell
$names = Get-Content scripts\tracked_players.json -Raw | ConvertFrom-Json
$ROSTER = ($names | ForEach-Object { "'" + ($_ -replace "'", "''") + "'" }) -join ","
& "$PG\psql.exe" -d $PROD -At -c "SELECT id FROM players WHERE display_name IN ($ROSTER) ORDER BY id" | Out-File -Encoding ascii "$BK\prewarm-roster-ids.txt"
& "$PG\psql.exe" -d $PROD -At -c "SELECT DISTINCT player_id FROM player_view_cache WHERE updated_at >= '2026-08-24' ORDER BY 1" | Out-File -Encoding ascii "$BK\prewarm-recent-ids.txt"
```
All 12 roster names match `players.display_name` [V]. The recent list is about 184 players today [V]. The date filter
excludes 4,118 dead rows at version `2002003001`, all written on 2026-08-23 [V]; a "last 30 days" filter would wrongly
include them (2,243 players) [V].

**I5. Run the backfill** from local `main` at the activation merge, clean tree, `python --version` 3.11.x:
```powershell
$env:DATABASE_URL = $PROD
.\.venv\Scripts\python.exe -X utf8 scripts\backfill_impact_candidate.py --manifest $M --state "$BK\backfill-state-rc3.json" --confirm-maintenance-window --approved-results $RR --batched
$LASTEXITCODE    # 0 scored | 2 incomplete/interrupted/verification_failed: stay suspended, rerun the identical command | 3 refused, nothing written
```
- Open no database session while it runs; it re-checks every 25 matches (`:190-196`) [V].
- Progress without touching the database: `(Get-Content "$BK\backfill-state-rc3.json" -Raw | ConvertFrom-Json).succeeded.Count`.
- Ctrl+C is safe; rerunning resumes from the state file [V, from reading the code].
- **Abort rule:** if the first 100 matches run at less than half the rate the rehearsal predicts, stop and switch to
  Decision 4's alternative. ⚠ *Correction 2: the prediction must include network round trips.*

**I6. Acceptance.** `& "$PG\psql.exe" -d $PROD -x -c $ACCEPT` must match the G3 expectations. The **~210-row gap is
fully explained**: it is match 3133 (210 stat rows, 0 impact rows, exactly 210 unscored player-rounds in the whole
database) [V live]. The backfill walks every match id (`:163`), so 3133's rows are **inserted**. Acceptance compares
the final state with a fresh replay (`:105-116,219-236`), never before/after counts, so no "before" rows are needed
[V]. Passing `--extra 3133` pins those 210 rows in `review-results.json`. The final count is **659,500**, not 659,290.

**I7. Rebuild the player caches** after exit code 0 and before resuming the site, from the activation checkout so the
rows carry version `4003003003`:
```powershell
.\.venv\Scripts\python.exe -c "from app.db import SessionLocal; from app.services.player_view_cache import prewarm_player_cache; ids = {int(x) for x in open(r'C:\Users\Conor Lum\Documents\valo-backups\prewarm-roster-ids.txt') if x.strip()}; db = SessionLocal(); prewarm_player_cache(db, ids); db.close(); print('prewarmed', len(ids))"
```
After J1, run the same command with `prewarm-recent-ids.txt` in the background (network duration unmeasured; the
docs say "several seconds per player" locally [A]). Do **not** run `recompute_player_views.py` unflagged (20,784
players) or with `--cached-only` (it finds nobody).

**I8. `site_stats_cache`.** It reads only match and round rows, never impact scores (`site_stats_cache.py:36-38`) [V],
so rc3 does not change it. A cache miss recomputes live and writes back (`site_stats.py:208-220`) [V]. Refresh it once
after J1: `.\.venv\Scripts\python.exe -c "from app.db import SessionLocal; from app.services.site_stats import
refresh_site_stats; db = SessionLocal(); refresh_site_stats(db); db.close(); print('site stats cached')"`. It was
empty probably because the 2026-09-10 ingest deleted the row, committing before scoring
(`trackergg_browserstate_source.py:440-443`), and never reached `refresh_site_stats` (`refresh_tracked_players.py:80`)
[A]. (Main session: a `/stats` visit on 2026-09-16 refilled it — 1 row.) Its failing test is a stale fixture, not a
data problem [V]. Run `Remove-Item Env:\DATABASE_URL` when finished.

**I9. Window estimate** [E] (⚠ *correction 2 applies to the calibration of these figures*):

| step | time |
|---|---|
| I2 deploy | 5–10 min [A] |
| I3 suspend and session check | 5 min |
| I4 B1 dump (251 MB database, `impact_scores` 84 MB [V]) and capture | 5–15 min [A, depends on bandwidth] |
| I5 writes | ~30 min: 3,125 × (0.32 s + ~4 round-trips × 62 ms) |
| I5 acceptance replay | ~22 min: 3,125 × (0.32 s + ~0.1 s read) |
| I6, I7 roster prewarm, J1–J3 | ~30 min |
| slack (one resume plus a rerun of acceptance) | ~45 min |
| **total** | **~2–2.5 h; book 3 h** (the stock script: ~23 h) |

### Phase J — Post-deploy verification and reopening (item 9)

- **J1.** Resume the web service. Render shows the activation merge commit; `https://valowithfriendstracker.com/health`
  returns `{"status":"ok"}` (the handler opens a DB session, `main.py:48-51`) [V].
- **J2.** `$ACCEPT` again: only `pvc` and `ssc` differ (now above 0). `SELECT version, count(*) FROM player_view_cache
  GROUP BY 1` shows only `4003003003`.
- **J3.** Pages (after picking a player at `/login`; match URLs from `SELECT id, external_id FROM matches WHERE id IN
  (3104, 3133)`): `/matches/<3104 external_id>` — every player's total equals the AFTER column of
  `impact-rc3/match-3104-site.md`, in the same order; `/matches/<3133 external_id>` renders with scores; a roster
  player's `/players/<name>` and `/career`; `/sessions`; `/stats/all`. Expected visible changes: all impact numbers;
  trade counts (6-second window versus v1's 10 seconds, `impact.py:98-101`); state diagrams (already since Phase H).
- **J4. Reopen ingestion** only from local `main` at or after the activation merge, with the Python 3.11 venv (C10
  enforces this): `.\scripts\refresh_remote.ps1 -Count 5`.
- **J5.** After the first new match is ingested:
  ```powershell
  $env:DATABASE_URL = $PROD
  .\.venv\Scripts\python.exe -c "from app.db import SessionLocal; from app.scoring.impact import find_unscored_match_ids; from app.scoring.impact_runtime import active_scoring_config; from scripts.backfill_impact_candidate import replay_diffs; db = SessionLocal(); print('unscored:', find_unscored_match_ids(db)); print(replay_diffs(db, [NEW_MATCH_ID], active_scoring_config()) or 'matches the active configuration')"
  Remove-Item Env:\DATABASE_URL
  ```
  Expect `unscored: []` and "matches the active configuration". Add a short activation note to the ledger (commit
  SHAs, timings, acceptance output). The ingestion freeze ends here.

### Phase K — Rollback without Docker (item 6)

Never reopen a partly converted database. If the backfill exits 2, keep the site suspended and either rerun or roll
back.

**K1. Roll back the activation** (the migrations stay):
1. Suspend the site, stop ingestion, confirm I3's session query returns 0 rows.
2. Restore `impact_scores` from **B1** and empty the caches:
   ```powershell
   & "$PG\psql.exe" -d $PROD -v ON_ERROR_STOP=1 -c "TRUNCATE impact_scores, player_view_cache, site_stats_cache"
   & "$PG\pg_restore.exe" -d $PROD --data-only --no-owner --no-privileges --single-transaction --table=impact_scores "$BK\prod-B1-<ts>.dump"
   & "$PG\psql.exe" -d $PROD -At -F "," -c $COUNTS     # impact_scores and impact_sum must equal prod-B1-<ts>.counts.csv
   ```
   Use B1, not B0 (B0's `impact_scores` lacks the three NOT NULL columns added by 0008). `player_view_cache` is
   truncated rather than restored, avoiding its id-sequence problem (`webapp/RENDER_DEPLOY.md` section (c), step 6).
3. `git revert -m 1 <activation merge SHA>` and push; Render deploys the version-2 code with no migration change [A].
4. Resume the site and `git pull` the ingestion checkout.

K1 is rehearsed in G5. Without B1 there is no practical rollback from this machine: `recompute_impact.py` has the same
per-row cost as the stock backfill, ~23 h [E].

**K2. Undo the PR #67 merge** (only if Phase H breaks the site): suspend the site; from the merged checkout,
`alembic downgrade 0007` against `$PROD` (drops the three columns and `round_player_spend`; no other rows change);
`git revert -m 1 <PR #67 merge>` and push; resume the site. **Downgrade first:** reverted code would run
`alembic upgrade head` against a database at revision 0009, which it does not know; the build would fail and the
merged code would keep serving [A: standard alembic behaviour]. Restore B0 (`pg_restore --clean --if-exists`) only if
data is damaged, then check sequences per `webapp/RENDER_DEPLOY.md` section (c), step 6.

**K3.** Check whether the Render Postgres plan offers point-in-time recovery, and note the time just before I2 [A].

## 2. How the locked weights reach production, and what happens to the A=1.25 path (item 2)

- **Where the weights live:** in exactly two places, cross-checked by `verify_manifest` — the declared `impact_rc3`
  comparator (C2) and the frozen manifest. `ACTIVE_MANIFEST` selects the manifest, which refuses to load at any
  version other than 3 (`impact_runtime.py:32-36`) [V]. Ingestion picks it up through `compute_impact_for_match` →
  `active_scoring_config()` (`impact.py:1574-1578`) [V].
- **The defaults do not change.** They define `live_legacy` and the historical comparators
  (`impact_manifest.py:72-87`). A reaches the legacy formula (`impact.py:1449,1480-1498`). After activation, ingestion
  uses the defaults only when `ACTIVE_MANIFEST` is None, i.e. after a rollback, so changing them would silently change
  the rollback formula. Tests pin them (`test_impact_manifest.py:63`) [V]. B, C and D have no effect on the legacy
  path: econ resolves to an empty dict (`impact.py:1376-1377`), and credit needs both flags on (`:1365`) [V].
- **The legacy A=1.25 path afterwards** remains the reviews' "before" comparator, the rollback formula, and the default
  for analysis tools. Nothing ingests with it. It is the branch's v2 legacy, not the v1 code on `main` that produced
  today's rows — so a rollback is B1 restore plus revert, never a revert alone.

## 3. Verified vs assumed — claims not tagged above

**Verified live, read-only:** 3,125 matches, ids 1–3133; match 3133 with 0 impact rows and 210 stat rows (210
unscored player-rounds in total); PostgreSQL 18.6; database 251 MB; 5 other client sessions at probe time (all the DB
role, one idle in a transaction); cache rows 4,118 at `2002003001` (written 2026-08-23) and 364 at `4002003001`;
20,784 players with match rows; 2,243 players with cache rows; rc2 review fingerprints unchanged for 12 of 12 matches.

**Verified in git and gh:** PR #67 OPEN, MERGEABLE/CLEAN, head `7f4a63b`, no CI checks; `main` unprotected. The branch
is 108 commits ahead of `main` and 2 behind; those 2 change nothing under `webapp/`. This clone's reflog: on
`enemy-at-11-redesign` (no 0008 columns) from 2026-08-27 to 2026-09-15; `impact-scoring-impl` created locally on
2026-09-15. `.env` and `.env.remote` are gitignored and untracked; `.env` was never committed.

**Verified in code:** `render.yaml` — the build runs `alembic upgrade head`, `autoDeploy: true`, Python 3.13.5.
Alembic uses `settings.database_url` (`env.py:11`). Ingestion commits the match before scoring it
(`trackergg_browserstate_source.py:426-443`). `backfill_unscored_matches` runs first, with no exception handling
around it (`refresh_tracked_players.py:55`).

**Assumed:** how Render's suspend, deploy and rollback UI behave; dump and upload durations, and per-player prewarm time
over the network; that libpq applies `PGOPTIONS` to psycopg2 connections; that alembic fails on an unknown database
revision after a code revert; that Docker is absent and that 1,150 tests pass (both from the brief and ledger); that
Neon lacks the 0008 columns (inferred from the failing test); the batched writer's round-trip count; that an
environment variable overrides `.env` (the pydantic-settings default, which `refresh_remote.ps1` already relies on).

## 4. Contradictions with the brief

1. **Section 2 "trap"** — changing A "would make newly-ingested rows inconsistent with the 659,290 already stored".
   Those rows came from `main`'s v1 scorer, which hard-codes `* 1.25` with the old multikill adjustment. The branch's
   legacy path (6-second trade schedule, multikill sign fix, alive-count fix) is already inconsistent with them
   whatever A is. A change to the default has no production effect until PR #67 merges [V].
2. **Section 4.4** — the rc2 README as the reproduction template: all five of its commands exit 1 for rc3 until C2–C7
   are done [V].
3. **Section 4** — "rc2 no longer verifies because its hashed sources changed": also, rc2 is pinned to Python 3.13, so
   it can never verify on this machine, and its comparators lack the `assists`, `trade_credit_scale` and
   `enable_trade_credit` keys [V].
4. ⚠ **WRONG — see correction 1.** ~~**Section 5** — "no local mirror": none exists yet, but the server binaries are
   installed, so one needs no Docker [V].~~
5. **Section 5** — "a writing backfill will be materially slower": quantified, ~23 h with the documented script [E].
6. **Section 5** — "checked-in `webapp/.env`": it is gitignored and was never committed [V].
7. **Section 2** — "the *local* test DB is behind the models": there is no local Postgres; that test uses `.env`'s
   remote Neon database and commits whenever the schema allows [V].
8. **Section 3** — `player_view_cache` 4,479 rows: now 4,482, and 4,118 of those are dead rows at an old version [V].
9. **Section 6.7** — "backfill of 659,290 stored rows": it writes 659,500, including 210 inserts for match 3133 [V].
10. **Section 5** — merging PR #67 "applies 0008 and 0009": it does, and it also bumps the live cache version and
    changes the legacy formula ingestion uses (finding 3) [V].
11. **Not in the brief:** 0008's docstring says `kill_order_bonus` is "Backfilled from the existing kill/death event
    data" (`0008…py:23`), but `upgrade()` only adds zero defaults (`:50-59`). Harmless once I5 runs [V].

## 5. Decisions the owner must make

1. **Re-confirm the lock with credit ON.** Recommend confirming at D2, before the freeze, since the weights were chosen
   from credit-OFF shares.
2. **`FormulaWeights` defaults.** Recommend unchanged, fixing only the docstring (section 2).
3. **Persist `trade_credit` / `assists_component` as columns?** Recommend **no migration for rc3**: `assists_component`
   is exactly 100 × `round_player_stats.assists`; `leverage_component` is exactly `impact − damage − econ_component −
   assists_component` (`impact.py:1451,1479`) [V]; the credit is reproducible exactly by replaying under the frozen
   manifest; and with C9 a later column is a ~1-hour window, not a day. Add a 0010 migration now (before F1) only if
   the site will display credit soon. ⚠ *Correction 4: "later" also means a full refreeze and review, because the
   persisted-field list is inside a hashed source.*
4. **Backfill method.** Recommend the batched writer inside the existing backfill protocol (C9), rehearsed in Phase G:
   ~2–2.5 h. Alternative: backfill the rehearsal copy, then load `impact_scores` into Render with one TRUNCATE +
   `\copy` transaction, then `replay_diffs` against Render — ~1–1.5 h and atomic, but a new production operation
   outside the script's safeguards. Reject the stock script (~23 h).
5. **PR #67: split or whole?** Recommend **split**: merge #67 first without activation (migrations applied in H4), then
   activation as its own one-commit PR in the window. The 108 commits of site changes and the migrations settle outside
   the window; the activation diff is reviewable; rolling back activation (K1) never crosses a migration, whereas
   rolling back a whole-PR merge needs a downgrade before the revert (K2); it matches rc2's runbook. Cost: two full
   cache invalidations, contained by not prewarming between H and I.
6. **Merge method.** Merge commit, keep the branch, so `scorer_revision` stays reachable.
7. **Ingestion freeze.** Recommend frozen until J5. Alternative: before Phase H, ingest only from a clean `origin/main`
   checkout; between H and I, only from the merged `main`. The backfill rescores both.
8. **Python version.** Recommend freezing under 3.11 (what everything so far has run on); document it, and treat any
   interpreter change as requiring a refreeze. Alternative: rebuild the venv on 3.13 before C12 and rerun everything.
9. **Ingestion preflight (C10).** Recommend yes — the cheapest way to prevent stranded matches like 3133.
10. **Guard the live-database tests (C11).** Recommend yes — stops pytest writing to production.
11. **Prewarm scope.** The 12 roster players before resuming the site, ~184 recently cached players after; not all
    20,784.
12. **Window slot.** A quiet 3-hour slot; tell friends that impact numbers, trade counts and state diagrams will change.

## 6. Risks and mitigations

1. **Backfill takes too long.** C9, the Phase G timings (⚠ correction 2), the I5 abort rule, Decision 4's alternative.
2. **The backfill refuses because other sessions are connected** (five at probe time). Suspend the site, run the I3
   check, open no psql during I5.
3. **Matches stranded by the wrong checkout (the 3133 pattern).** Hypothesis: a checkout whose model maps the 0008
   columns, run against a 0007 database, commits the match and deletes cached rows, then fails at the first
   `impact_scores` SELECT (`impact.py:1580-1584`), leaving the session unusable and skipping `refresh_site_stats`.
   Consistent evidence: 3133 is the last match; `site_stats_cache` was empty; the 0008 model changes landed 2026-09-07
   03:26 PDT, before 3133 was created (2026-09-10 00:18 PDT); the `round_player_spend` writes came later, 2026-09-12
   [V]. Not provable from this machine: this clone was on a branch without the 0008 columns at the time, and the impl
   commits were made in another clone [V reflog / A]. Hazard by phase — *today:* running `matches` from this checkout
   crashes at startup on 3133 after committing cache deletions [V, code path]; *after H:* any pre-merge checkout hits a
   NOT NULL error on insert; *after I:* a stale checkout silently writes legacy-scored rows, or a non-3.11 interpreter
   raises `ManifestMismatchError` after `load_match` has committed. Mitigation: the A3 freeze, the C10 preflight, the
   J4/J5 checks, and I5/I6 covering 3133.
4. **Commands silently hitting the old Neon database** (the `.env` fallback): the `$COUNTS` preflight in every block.
5. **pytest writing to production:** A4 and C11.
6. **0008's lock queue stalls the live site:** the H2 check and `lock_timeout` in H4; never migrate during a replay.
7. **Cache traps:** capture prewarm ids at I4, before I5; no prewarm between H and I; prewarm only from the deployed
   commit — a checkout with a different cache version writes rows the site treats as misses
   (`player_view_cache.py:119-124`) [V].
8. **Manifest edited after the review results:** never edit it; record approval elsewhere.
9. **A squash merge orphans `scorer_revision`:** Decision 6.
10. **The RESULT measured by a scratch script that differs from the frozen configuration:** F3's exact match.
11. **Smallint overflow on weighted columns:** the B2 maxima, and the rehearsal writes all 659,500 rows first.
12. **No usable backup, or restoring the wrong one:** B1's restore rehearsed in G5; K1 uses B1 only.
13. **Render UI behaviour unverified:** I2 deploys and waits for live before suspending, so nothing depends on
    deploying while suspended; rollback goes through git revert.
14. **Credit-ON numbers seen before the declaration:** the B1 disclosure (⚠ correction 3) and the F3 exact match.
15. **The repo is public:** dumps, state files and id lists go in `C:\Users\Conor Lum\Documents\valo-backups`, never
    inside the repo.

### Critical files

- `webapp/app/scoring/impact_manifest.py`
- `webapp/scripts/backfill_impact_candidate.py`
- `webapp/scripts/release_candidate_review.py`
- `webapp/app/scoring/impact_runtime.py`
- `webapp/scripts/refresh_tracked_players.py` (with `webapp/app/adapters/trackergg_browserstate_source.py`)
