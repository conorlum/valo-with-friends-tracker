# Buy-disruption 30% / 80% release candidate

Status: **frozen for owner review. NOT active.** Production defaults are
unchanged: `app.scoring.impact_runtime.ACTIVE_MANIFEST` is `None`, so ingestion
and `scripts/recompute_impact.py` still score with the live legacy formula, and
`IMPACT_CALCULATION_VERSION` is still 2. Nothing in the site database has been
rescored by this work.

Plan: `../2026-09-10-econ-buy-disruption-implementation-plan.md`.
Formula: `../specs/2026-09-10-econ-buy-disruption-implementation.md`, section 12.

## Files

| File | What it is |
|---|---|
| `candidate-manifest.json` | The complete frozen configuration: named comparators, weights, scale, calculator constants, trade schedule, agent allowances, timing flags (all off), behavioral digests of every scoring source file, and fingerprints of every reviewed match's source rows. |
| `review-results.json` | Persisted-field values for every reviewed match under the release comparator. The backfill's acceptance check compares the site database against it. |
| `match-3104-site.md`, `match-3104-penalty.md`, `trace-3104-site.md` | Abyss full-Impact before/after, the penalty-only comparison, and the per-kill trace. |
| `fixed-ten-site.md`, `fixed-ten-penalty.md`, `trace-3116-site.md` | The pinned ten-match comparison, and the trace of its pistol-winner-loses-round-14 history. |
| `match-3120-site.md`, `trace-3120-site.md` | The separately selected pistol-winner-loses-round-2 history. |
| `corpus-audit.json`, `corpus-audit.md` | The predeclared read-only corpus checks. |
| `defect-reinstatement.md` | Each defect reinstated behind the same API, and the tests that caught it. |
| `SUMMARY.md` | Implementation summary, test results, remaining findings. |

## Comparators

| Name | Meaning |
|---|---|
| `live_legacy` | Today's runtime formula. Its replay and the persisted site values are reported separately. |
| `separate_econ_legacy` | The older pooled/zero-sum separate-econ allocator. Diagnostic only. |
| `buy_disruption_v2_wealth` | V2 kill credit plus the historical wealth debit (the Abyss -100 to -928 column). |
| `buy_disruption_v2_30_80` | Identical V2 kill credit plus the owner's 30%/80% debit. **The release candidate.** |

The penalty-only comparison is `buy_disruption_v2_wealth` vs
`buy_disruption_v2_30_80`, with gross kill credits and every non-econ term
reconciled as identical. The site comparison is `live_legacy` vs the release
candidate: that is the TOTAL release change, including the move from
`damage + mean(econ, time, swing)` to `A*damage + B*leverage + C*econ`.

## Reproducing the review

From `webapp/`, with local Postgres up (`docker compose -p valomaths-private up -d`):

```powershell
$M = "..\docs\superpowers\econ-buy-disruption-candidate\candidate-manifest.json"
$D = "..\docs\superpowers\econ-buy-disruption-candidate"
.\.venv\Scripts\python.exe scripts\release_candidate_review.py --manifest $M --match 3104 --compare site --report-dir $D
.\.venv\Scripts\python.exe scripts\release_candidate_review.py --manifest $M --match 3104 --compare penalty --report-dir $D
.\.venv\Scripts\python.exe scripts\release_candidate_review.py --manifest $M --trace 3104 --report-dir $D
.\.venv\Scripts\python.exe scripts\release_candidate_review.py --manifest $M --ten --compare site --report-dir $D
.\.venv\Scripts\python.exe scripts\release_candidate_review.py --manifest $M --ten --compare penalty --report-dir $D
.\.venv\Scripts\python.exe scripts\release_candidate_review.py --manifest $M --match 3120 --trace 3120 --report-dir $D
.\.venv\Scripts\python.exe scripts\release_candidate_review.py --manifest $M --corpus --report-dir $D
.\.venv\Scripts\python.exe scripts\release_candidate_review.py --manifest $M --ten --extra 3104 --extra 3120 --results $D\review-results.json
```

Every run verifies the manifest against the checkout and each reviewed match's
source rows against its frozen fingerprint first, and refuses on any
difference. A scoring code change after approval therefore forces the review
to be re-run and re-approved.

## Activation (only after the owner approves these reports)

Run from `webapp/` unless stated. Do every step in one sitting.

1. **Stop everything that can read or write scores.** Stop the uvicorn
   process, any `ingest_trackergg_player.py` / `refresh_tracked_players.py` /
   prewarm run, and close psql or pgAdmin sessions. Confirm nothing else is
   connected (the backfill checks this too and refuses otherwise):

   ```powershell
   docker exec valomaths-private-postgres-1 psql -U valorant -d valorant_igl_tutor -c "SELECT pid, application_name, state FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()"
   ```

2. **Back up the verified database and record the rollback revision.**

   ```powershell
   git rev-parse HEAD
   docker exec valomaths-private-postgres-1 pg_dump -U valorant -d valorant_igl_tutor -Fc -f /tmp/pre-buy-disruption.dump
   docker cp valomaths-private-postgres-1:/tmp/pre-buy-disruption.dump ..\..\pre-buy-disruption.dump
   ```

3. **Activate in one commit.** In `app/scoring/impact_runtime.py` set
   `ACTIVE_MANIFEST = "docs/superpowers/econ-buy-disruption-candidate/candidate-manifest.json"`.
   In `app/scoring/impact.py` set `IMPACT_CALCULATION_VERSION = 3` with a history
   comment. The manifest refuses to load at any other version. Confirm:

   ```powershell
   .\.venv\Scripts\python.exe -c "from app.scoring.impact_runtime import active_scoring_config; print(active_scoring_config())"
   ```

4. **Backfill, inside the maintenance window.**

   ```powershell
   .\.venv\Scripts\python.exe scripts\backfill_impact_candidate.py --manifest ..\docs\superpowers\econ-buy-disruption-candidate\candidate-manifest.json --state ..\..\backfill-state-buy-disruption.json --confirm-maintenance-window --approved-results ..\docs\superpowers\econ-buy-disruption-candidate\review-results.json
   ```

   Exit 0 means every match succeeded, every persisted row equals its replay
   under the frozen configuration, and the approved report's matches equal it
   exactly. Caches are cleared at the start and on every exit.

5. **Rebuild caches:** `.\.venv\Scripts\python.exe scripts\recompute_player_views.py`

6. **Check pages with ingestion still paused.** Start only uvicorn. Match
   3104's player totals must equal `match-3104-site.md`'s AFTER column; open a
   player page and the stats page.

7. **Reopen.** Resume ingestion. After the first new match is ingested, replay
   it with `--manifest ... --match <id> --compare site` and confirm the
   persisted values match.

This backfill also resolves the outstanding rescore: stored rows currently
predate `IMPACT_CALCULATION_VERSION` 2, so `test_builder_matches_stored_values`
is red by design until the site is rescored.

## If the backfill is interrupted or fails

Keep the web process and workers **stopped**. Never reopen a partly converted
database.

- Exit 2 (`incomplete`, `interrupted` or `verification_failed`): read the state
  file, fix the cause, and rerun the identical command; it resumes the declared
  match set with idempotent upserts. Or roll back.
- Exit 3 (refused): nothing was scored; the message says why (window not
  confirmed, sessions connected, manifest not active, different manifest, or the
  match set changed because something ingested during the window).

## Rollback

1. Stop everything, as in activation step 1.
2. Restore the backup:

   ```powershell
   docker cp ..\..\pre-buy-disruption.dump valomaths-private-postgres-1:/tmp/restore.dump
   docker exec valomaths-private-postgres-1 pg_restore -U valorant -d valorant_igl_tutor --clean --if-exists /tmp/restore.dump
   ```

3. Revert the activation commit (`git revert <activation sha>`), restoring
   `ACTIVE_MANIFEST = None` and `IMPACT_CALCULATION_VERSION = 2`.
4. Run `scripts/recompute_player_views.py` so no cache from the candidate version survives.
5. Without a usable backup: after step 3, run `scripts/recompute_impact.py`
   and then `scripts/recompute_player_views.py` inside a maintenance window, so
   the scores, not just the code, return to the legacy formula.
