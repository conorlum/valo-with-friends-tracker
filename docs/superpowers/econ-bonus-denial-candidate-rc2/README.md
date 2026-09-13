# Round 2/14 bonus-round denial -- release candidate rc2

Status: **frozen for owner review. NOT active.** Production defaults are unchanged:
`app.scoring.impact_runtime.ACTIVE_MANIFEST` is `None`, so ingestion and `scripts/recompute_impact.py` still score
with the live legacy formula, and `IMPACT_CALCULATION_VERSION` is still 2. Nothing in the site database has been
rescored by this work.

- Spec: `../specs/2026-09-12-econ-bonus-round-denial-design.md` (with its 2026-09-12 amendments in sections 4.1 and 4.2)
- Plans: `../plans/2026-09-12-econ-bonus-round-denial.md`, `../plans/2026-09-12-bonus-denial-review-fixes.md`
- Declaration: the "bonus-round denial rc2" amendment in `../2026-09-07-predeclared-values.md`

rc2 supersedes `../econ-bonus-denial-candidate/` (rc1) after the code review fixes. rc1's reports are kept
unchanged as the record of what rc1 scored.

## Files

| File | What it is |
|---|---|
| `candidate-manifest.json` | The complete frozen configuration: comparators, weights, scale, calculator constants (including the bonus constants and `SURVIVED_LOSS_REWARD`), trade schedule, agent allowances, timing flags (all off), behavioral digests of every scoring source, and fingerprints of every reviewed match's source rows (kill weapons included). |
| `review-results.json` | Persisted-field values for every reviewed match under the release comparator. The backfill's acceptance check compares the site database against it. |
| `model-comparison.json` | Corpus comparison of `buy_disruption_v2_30_80` vs the bonus model, with parity reconciliation. |
| `match-3104-site.md`, `fixed-ten-site.md`, `match-3120-site.md`, `trace-3120-site.md` | Full-Impact before/after vs `live_legacy`, and the per-kill trace with the bonus-denial audit. |
| `rc2-parity.md` | The unchanged 30/80 corpus audit at this revision vs the 30/80 candidate's committed audit. |
| `defect-reinstatement.md` | Each defect reinstated behind the same API, and the tests that caught it. |
| `SUMMARY.md` | Results of every declared check, flags, and what changed relative to rc1. |

## Comparators

| Name | Meaning |
|---|---|
| `live_legacy` | Today's runtime formula. |
| `buy_disruption_v2_30_80` | The current 30/80 econ candidate. Carried unchanged; the parity check audits it. |
| `buy_disruption_v2_30_80_bonus_denial` | 30/80 plus the round 2/14 bonus-round denial. **The release candidate.** |
| `buy_disruption_v2_wealth`, `separate_econ_legacy` | Historical comparators, required by the manifest; not reviewed here. |

## Reproducing the review

From `webapp/`, with local Postgres up (`docker compose -p valomaths-private up -d`):

```powershell
$M = "..\docs\superpowers\econ-bonus-denial-candidate-rc2\candidate-manifest.json"
$D = "..\docs\superpowers\econ-bonus-denial-candidate-rc2"
.\.venv\Scripts\python.exe -X utf8 scripts\release_candidate_review.py --manifest $M --match 3104 --compare site --report-dir $D
.\.venv\Scripts\python.exe -X utf8 scripts\release_candidate_review.py --manifest $M --ten --compare site --report-dir $D
.\.venv\Scripts\python.exe -X utf8 scripts\release_candidate_review.py --manifest $M --match 3120 --trace 3120 --report-dir $D
.\.venv\Scripts\python.exe -X utf8 scripts\compare_econ_models.py --manifest $M --report-dir $D
.\.venv\Scripts\python.exe -X utf8 scripts\release_candidate_review.py --manifest $M --ten --extra 3104 --extra 3120 --results $D\review-results.json
```

Do **not** run `release_candidate_review.py --corpus` against this manifest. Its wealth-vs-release gross-credit
identity holds only when the release is 30/80, so it would count every changed round-2/14 kill as a mismatch.
`compare_econ_models.py` replaces it with victim-side parity.

Every run verifies the manifest against the checkout, and each reviewed match's source rows against its frozen
fingerprint, first. Any difference is refused, so a scoring code change after approval forces the review to be
re-run and re-approved.

**Older candidates at this revision:** the 30/80 manifest in `../econ-buy-disruption-candidate/` and the bonus rc1
manifest no longer verify, because their hashed sources changed. Activate either only from a checkout of its own
scorer revision: `bba279b` (30/80) or `c7cbfd7` (bonus rc1).

## Activation (only after the owner approves these reports)

Run from `webapp/` unless stated. Do every step in one sitting.

1. **Stop everything that can read or write scores.** Stop uvicorn, any `ingest_trackergg_player.py`,
   `refresh_tracked_players.py` or prewarm run, and close psql or pgAdmin sessions. Confirm nothing else is
   connected (the backfill checks this too and refuses otherwise):

   ```powershell
   docker exec valomaths-private-postgres-1 psql -U valorant -d valorant_igl_tutor -c "SELECT pid, application_name, state FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()"
   ```

2. **Apply migration 0009** (additive `round_player_spend` table; the scorer does not read it):
   `.\.venv\Scripts\python.exe -m alembic upgrade head`

3. **Back up the database and record the rollback revision.**

   ```powershell
   git rev-parse HEAD
   docker exec valomaths-private-postgres-1 pg_dump -U valorant -d valorant_igl_tutor -Fc -f /tmp/pre-bonus-denial.dump
   docker cp valomaths-private-postgres-1:/tmp/pre-bonus-denial.dump ..\..\pre-bonus-denial.dump
   ```

4. **Activate in one commit.**
   - In `app/scoring/impact_runtime.py`, set
     `ACTIVE_MANIFEST = "docs/superpowers/econ-bonus-denial-candidate-rc2/candidate-manifest.json"`.
   - In `app/scoring/impact.py`, set `IMPACT_CALCULATION_VERSION = 3` with a history comment. The manifest
     refuses to load at any other version.
   - Confirm:

   ```powershell
   .\.venv\Scripts\python.exe -c "from app.scoring.impact_runtime import active_scoring_config; print(active_scoring_config())"
   ```

5. **Backfill, inside the maintenance window.**

   ```powershell
   .\.venv\Scripts\python.exe scripts\backfill_impact_candidate.py --manifest ..\docs\superpowers\econ-bonus-denial-candidate-rc2\candidate-manifest.json --state ..\..\backfill-state-bonus-denial.json --confirm-maintenance-window --approved-results ..\docs\superpowers\econ-bonus-denial-candidate-rc2\review-results.json
   ```

   Exit 0 means every match succeeded, every persisted row equals its replay under the frozen configuration,
   and the approved report's matches equal it exactly.

6. **Rebuild caches:** `.\.venv\Scripts\python.exe scripts\recompute_player_views.py`

7. **Check pages with ingestion still paused.** Start only uvicorn. Match 3104's player totals must equal the
   AFTER column of `match-3104-site.md`.

8. **Reopen.** Resume ingestion. After the first new match, compare its persisted rows against a replay (empty
   output means they agree):

   ```powershell
   .\.venv\Scripts\python.exe -c "from app.db import SessionLocal; from app.scoring.impact_runtime import active_scoring_config; from scripts.backfill_impact_candidate import replay_diffs; print(replay_diffs(SessionLocal(), [NEW_MATCH_ID], active_scoring_config()) or 'matches the active configuration')"
   ```

## If the backfill is interrupted or fails

Keep the web process and workers **stopped**. Never reopen a partly converted database.

- Exit 2 (`incomplete`, `interrupted` or `verification_failed`): read the state file, fix the cause, and rerun
  the identical command; it resumes with idempotent upserts. Or roll back.
- Exit 3 (refused): nothing was scored; the message says why.

## Rollback

1. Stop everything, as in activation step 1.
2. Restore the backup:

   ```powershell
   docker cp ..\..\pre-bonus-denial.dump valomaths-private-postgres-1:/tmp/restore.dump
   docker exec valomaths-private-postgres-1 pg_restore -U valorant -d valorant_igl_tutor --clean --if-exists /tmp/restore.dump
   ```

3. Revert the activation commit, restoring `ACTIVE_MANIFEST = None` and `IMPACT_CALCULATION_VERSION = 2`.
4. Run `scripts/recompute_player_views.py`.
5. Without a usable backup: after step 3, run `scripts/recompute_impact.py` then `scripts/recompute_player_views.py`
   inside a maintenance window.
