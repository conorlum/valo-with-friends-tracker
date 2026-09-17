# Impact rc3 — the remaining steps, for review

**Purpose.** Stages 1 to 5 are done and have been reviewed three times. This document covers what is left: the
rehearsal, the first production writes, activation, and the rollback paths. It is written to be reviewed *before* any
of it runs, by someone reading the repository read-only.

**Review this document against the code, not against itself.** The runbook `README.md` holds the exact commands; this
holds the reasoning, the ordering, and the things I am not sure about. Where the two disagree, that is a finding.

---

## 1. Where the release stands

| | state |
|---|---|
| Branch | `impact-scoring-impl`, **never pushed**. rc3 frozen at `82d8e6b` |
| Production | **untouched**: alembic 0007, no write gate, every score still v1, match 3133 unscored |
| `valo_rc3_rehearsal` | production restore, migrated to 0010, gate installed **closed** |
| Recovery | point-in-time restore to any timestamp in the past 7 days, into a *new* instance |
| Not yet created | **the activation commit** — it does not exist yet (see 3.1) |

**What is already proven.** The comparison-projection hash `7e5ff278…42f1bb` is reproduced by four independent
recomputations of all 659,500 rows: K1' (Python 3.11), K2' (3.13), K3 (through the declared comparator from a clean
checkout), and K4 (through the *frozen manifest, by digest*). Credit-off reproduces `648df040…` twice. The
decomposition check rebuilt every term of 13 matches from the sources independently of the scorer: 20,022 checks, 0
mismatches. All twelve declared defects were reinstated and caught on values.

**What is not proven, and cannot be until a table exists.** Two of the nine declared review checks are structurally
incomplete: that the cohort fingerprint is unchanged *between export and load*, and that the built table matches the
approved results. Both are `verify-build`/`verify-live` steps, which need a load. Check 9 is the rehearsal itself.

---

## 2. The invariants every remaining step must preserve

A finding is anything that could break one of these without stopping.

1. **K5 is what gets loaded.** The artifact exported at the activation commit must hash to `7e5ff278…42f1bb`. If it
   does not, nothing is built.
2. **No production write happens that the rehearsal has not performed first** against `valo_rc3_rehearsal`.
3. **Every step names its database** (`--expect-database`, or `release_preflight.py`). Production and rehearsal URLs
   differ only in the database name.
4. **The write gate is closed from Stage 7.4 until 48 hours after activation.** Nothing ingests in that window, and
   R1 (scoring rollback) stays valid only while it holds.
5. **A swap either commits or changes nothing.** `scoring_release_log` carries a `swap swapped` entry exactly when it
   committed; that log is the authority after any interruption, not inference.
6. **The player cache is cleared by DELETE, never TRUNCATE.** A page load holds its cache read lock while its own
   second connection writes through; a TRUNCATE between them wedges with no deadlock to break it.
7. **The chain is append-only.** The ledger takes correction entries, never edits.

---

## 3. Stage 6 — the rehearsal

Entirely on `valo_rc3_rehearsal`. Production is not touched. Every duration is recorded.

### 3.1 Build the activation commit (does not exist yet)

A local branch off `82d8e6b` with **exactly one commit**: `ACTIVE_MANIFEST` from `None` to the manifest path
(`app/scoring/impact_runtime.py:25`) and `IMPACT_CALCULATION_VERSION` from 2 to 3 (`app/scoring/impact.py:107`).
Everything from 3.3 on runs from a clean checkout of it, and Stage 8 rebases this same commit onto the merged `main`.

*Why one commit:* it is the thing that is reviewed, rebased, merged and, if needed, reverted. A second commit makes the
revert a judgement call.

### 3.2 The read-only corpus tests against the restore (new, added 2026-09-17)

The declaration said the database tests run against the rehearsal database. They ran against a schema-only scratch
database, where the 13 corpus-measuring tests *skip* — so they ran nowhere. Twelve only read and belong on the restore.

The thirteenth, `test_wrapper_still_persists_and_commits`, calls the scorer's committing wrapper on a real match. The
wrapper UPDATES in place, so on the restore it would rescore a match without changing any row count. It keeps the
scratch database, and the helper now enforces that: `writes=True` joins `empties_tables=True`, both held to `*_test`
(`1e0b722`).

**Open: before or after the swap?** `test_builder_matches_stored_values` compares the builder field-by-field against
*stored* scores. Before the swap, stored is production's v1 and the running code computes legacy — and those are known
to disagree (10 of 10 players on match 3104). After the swap from the activation checkout, stored is rc3 and the code
computes rc3, which makes the test an independent end-to-end check on the loaded table that does not go through
`verify-build`'s artifact comparison at all. A run is in flight to settle this.

### 3.3 to 3.9 — the rehearsal proper

| step | what it proves | what I would want checked |
|---|---|---|
| 6.3 PR #67 pages | overtime diagrams, side resolution, sessions, matches render on real data | that a page review catches a *wrong number*, not just a 200 |
| 6.4 forward path | export KR → build → verify-build → swap → verify-live → prewarm → coverage | that KR's hash **and** input fingerprint equal K3's, not only the hash |
| 6.5 gate probes | a real `origin/main` worktree refused; an identity-less ingest refused; the activation checkout allowed | that each probe rolls back and exits nonzero on the wrong outcome |
| 6.6 concurrency | page loads across a swap and a rollback: no deadlock, no 5xx, no stall >5s per lock over baseline | whether the baseline is measured on the same cache state as the test |
| 6.7 rollback + interrupted swap | Ctrl+C'd and lock-timed-out swaps both change nothing | that `state` is what decides, not the exit code |
| 6.8 R2 dry run | maintenance mode, downgrade to 0007, gate survives still closed | that the downgrade is DDL-only and so not refused by the gate |
| 6.9 | durations and the latest safe rollback time recorded | — |

---

## 4. Stage 7 — first production writes, site up

1. **7.2** preflight (0007, 3,125 matches, max id 3133, gate absent, no long transactions) then backup **B1**. The
   preflight prints server time; that is the restore point.
2. **7.3** `alembic upgrade head` by hand from a clean checkout of PR #67's head, then preflight for exactly 0010.
   Migrations run with `lock_timeout` 10s and `statement_timeout` 15min, so a blocked migration fails rather than
   waits. **A failure here means do not merge.**
3. **7.4** install the write gate **closed**. From here no checkout can write scores or ingest.
4. **7.5** merge PR #67 with a merge commit. Render's build is `pip install` + `alembic upgrade head` — verified in
   `render.yaml`; it does **not** load seed data, whatever CLAUDE.md says. Until Stage 8 the site shows v1 scores with
   the branch's other changes.

---

## 5. Stage 8 — activation, site up

Rebase the activation commit onto merged `main`, open its PR, do not merge yet. 8.1–8.4 and 8.6–8.7 run from a clean
checkout of that PR's head.

1. **8.1** preflight, then export **K5** with `--active`. K5 must equal the chain hash or nothing proceeds.
2. **8.2** build, then `verify-build`: every match's input fingerprint against K5's, the approved rows, `scoring_version`
   3 on every built row, and K5's hash against the chain.
3. **8.3** capture the prewarm id lists *before* the swap empties the cache.
4. **8.4** swap, then `state`, then `verify-live`. Match 3133 must show 210 rows — its first score ever.
5. **8.5** merge the activation PR. The live code is confirmed by the cache version it writes: `4003003003`.
6. **8.6** clear the cache (DELETE), prewarm the roster from the activation checkout, verify coverage, refresh site
   stats, and launch a **background** prewarm over recently cached players.
7. **8.7** acceptance, read-only: every one of the 3,125 matches equals its replay under the active configuration.
8. **8.8** page checks against the recorded review reports. **8.9** 48-hour hold, gate closed. **8.10** reopen
   ingestion; R1 expires here. **8.11** ledger note. **8.12** keep `impact_scores_v1` at least 14 days.

---

## 6. Stage 9 — rollback

- **R1 (scoring).** Valid only while the gate is closed and no match has arrived since the swap; the tool refuses
  otherwise. Stop 8.6's background prewarm first, revert the activation PR, clear the cache, prewarm from the reverted
  checkout.
- **R2 (PR #67).** Maintenance mode → (R1 if needed) → `alembic downgrade 0007` → revert the merge → deploy. The gate
  survives the downgrade, closed, so nothing ingests until it is lifted deliberately with
  `DROP FUNCTION scoring_gate_guard() CASCADE`.
- **R3 (last resort).** Point-in-time recovery into a new instance, then repoint the web service and `.env.remote`.

---

## 7. What I am unsure about — the review I actually want

1. **The window between 8.4 and 8.5.** The swap lands rc3 scores while the deployed code is still
   `IMPACT_CALCULATION_VERSION = 2`. Pages read impact from the table, so they would display rc3 numbers under v2 code
   until the deploy is live; cache rows written in that window carry `…002` and are cleared in 8.6. Is anything in the
   serving path *computing* impact rather than reading it? Should the swap follow the merge instead? The window is
   bounded by Render's deploy time, which is not measured anywhere.

2. **The background prewarm versus R1.** 8.6 launches a prewarm in the background. R1 says stop it first — but that is
   an instruction, not a guard. If a rollback runs while it is alive, it caches rc3 numbers over restored v1 scores.
   Should `rollback` refuse while it can see one running, or should the prewarm take a marker the tool checks?

3. **Cache consistency is coverage, not equality.** `verify-live` checks the table; the site reads
   `player_view_cache`. `verify_player_cache_coverage.py` checks coverage and blob validity. Nothing checks that a
   cached blob's numbers agree with the swapped table. Is that gap real, and does it matter given 8.6 clears first?

4. **Match 3133** has been stranded unscored since 2026-09-10 by the branch ingest hazard. It gets its first score at
   activation and has no before/after to reconcile. Is its source data actually complete, or is it stranded because
   something about it is wrong?

5. **Interpreter split.** The manifest pins **Python 3.13.15** and 41 packages for ingestion; `render.yaml` pins
   **3.13.5** for serving. Ingestion only ever runs locally, and serving should only read — but the cache is written by
   *both* Render and the local prewarm, under the same version number. If any cached value is a float sum, the two
   could disagree. This predates rc3; I want to know whether it is genuinely inert.

6. **How long ingestion is frozen.** The gate closes at 7.4 and opens at 8.10 — Stage 7, plus the rehearsal, plus
   Stage 8, plus 48 hours. Matches played in that window must be backfilled afterwards from tracker.gg. Nobody has
   costed that window or confirmed the backfill depth is sufficient.

7. **A defect class I found by reading, not by testing** (`8854c0f`): five runbook commands carried a literal `\n`
   where a line continuation belonged, from a heredoc that mangled the backslash. Pasted, `\n` collapses to a stray
   `n` argument and argparse exits **2** — which the runbook's own table reads as "verification found problems". Two
   of the five were production steps, so the failure mode was an operator concluding rc3 failed verification and
   reaching for R1, when the command never ran. All 31 blocks now parse under `bash -n`, but `bash -n` would not have
   caught this one. **What else in these documents is valid syntax with the wrong meaning?**

---

## 8. What counts as a finding

Ranked by what would actually change the plan:

- A way for a production step to change something while *appearing* to succeed.
- A way for the chain to be satisfied by an artifact that is not what gets loaded.
- An ordering where a failure leaves production in a state no documented rollback recovers.
- A check that cannot fail — a test, probe or assertion that would pass even if the thing it guards were broken.
- A command whose documented exit code means something different from what it does.

Something already recorded as a deviation or a trap is not a finding unless the record is wrong.
