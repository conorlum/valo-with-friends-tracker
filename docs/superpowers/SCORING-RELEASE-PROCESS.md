# How a scoring change gets from an idea to the live site

**Read this first if you are an agent asked to change how Impact is scored, or to ship a change that already
exists.** It is the general process, distilled from two real releases: rc3 (`impact-rc3/README.md`, shipped
2026-09-18) and v4 (`impact-v4/README.md`). Every step here exists because skipping it caused, or would have
caused, a real defect in one of them. Where a step names "the release's runbook", that is the per-release file this
process tells you to create in §C. **Do not replan the process; plan only the release.**

Throughout, `<id>` is the release's short name (`v4`), `<cand>` its candidate id (`impact-v4`), `<cmp>` its release
comparator (`impact_v4`), `N` the new `IMPACT_CALCULATION_VERSION`, and `N-1` the one live now.

---

## 0. Rules for every session

Non-negotiable. Each one is here because breaking it went wrong at least once.

- **The repository is PUBLIC.** Never print, log or commit a database URL or credential. Production is used only as
  `PROD="$(grep '^DATABASE_URL' .env.remote | cut -d= -f2-)"`, passed inline as `DATABASE_URL="$PROD"`, never
  echoed. `.env.remote`, `.env.test` and `.env.rehearsal` are gitignored.
- **Every step asserts its database by name first** (`--expect-database`, `release_preflight.py`). The production
  and rehearsal URLs differ only in that name.
- **Python 3.13 (`webapp/.venv313`) for everything that scores, freezes, verifies or ingests.** The frozen manifest
  records the interpreter, and a 3.11 process fails its verification.
- **`build_impact_rows_for_match` with no kwargs is the LEGACY formula**, not what ships. Every script, harness and
  test that scores MUST pass an explicit configuration. The ledger's 2026-09-21 CORRECTION records a whole
  investigation that measured the wrong formula because of this.
- **Git Bash, from `webapp/`.** Write files with the editor tools, not heredocs: heredocs here mangle backslashes and
  `bash -n` does not catch it. Windows Python wants `C:/...` paths, not `/c/...`. Git pathspecs resolve against the
  current directory, so anchor them with `:/`, and check that they matched files before trusting an empty diff.
- **One corpus replay at a time.** Two at once get OS-killed. Redirect every long run to a log file, run it in the
  background, and wait for the notification rather than polling.
- **Run every long job as a detached Windows process, never as a supervised background task.** The low-memory
  reaper kills supervised tasks: v4 lost an in-window K4 after 22 minutes of scoring, before its sidecar, and two
  waiters besides. `Start-Process powershell -WindowStyle Hidden -PassThru -RedirectStandardOutput <log>` survives;
  only the thing waiting on it can die. Watch the log, and treat the pid disappearing as a terminal event.
- **Scripts outside `webapp/` need `PYTHONPATH`, or feed them on stdin** (`python - < probe.py`). `sys.path[0]` is
  the script's own directory, so `import app` fails. v4 hit this three times: the probes, the acceptance replay and
  the catch-up inventory.
- **Windows paths inside SQL strings need `cygpath -m`.** Git Bash converts argv paths, not string literals.
- **An export is bound to the database it read.** A second restore cannot reuse the first's artifact; re-export.
- **Some production writes need the owner's hands.** Claude Code's auto-mode classifier refused v4's gate open and
  the stale-checkout probe as production writes, and refused adding an allow rule as self-modification. The owner
  ran both with `! <command>`. Hand over the exact one-line command; never route around a refusal.
- **Keep the machine awake** for exports (~30 min each against production) and `verify-build` (~15 min). A sleep
  kills the connection mid-snapshot.
- **Artifacts, dumps and logs live outside the repository** (`$ART`, under `~/Documents/valo-backups/<id>-release/`).
  Their hashes are recorded in the ledger.
- **Database tests that write run against a `*_test` database only** (`tests/_postgres.py` enforces this).
  Read-only tests may use a `*_rehearsal` restore. Never write to a measurement corpus.
- **Exit 2 is ambiguous.** It means "verification found problems" *and* argparse's "bad command line". Read the
  output: a `usage:` block means nothing ran.
- **An instrument that cannot fail is not evidence.** Every checker a declaration relies on needs a test proving it
  can fail, through the same entry point the declaration uses. v4's equivalence checker once reported "clean" against
  a corrupted reference (ledger, ADDENDUM to RESULT, declaration 13).
- **Commit in small, reviewable steps**, and end every message with the attribution line the session gives you.

### Session setup

```bash
cd "$(git rev-parse --show-toplevel)/webapp"
set -o pipefail   # a failing command piped into tee must still fail the block
export PYTHONIOENCODING=utf-8
PY=.venv313/Scripts/python.exe
PGBIN="/c/Program Files/PostgreSQL/18/bin"
ART="$HOME/Documents/valo-backups/<id>-release"; mkdir -p "$ART"
REL=../docs/superpowers/impact-<id>
PROD="$(grep '^DATABASE_URL' .env.remote | cut -d= -f2-)"
case "$PROD" in */valowithfriendsdb) REH="${PROD%/valowithfriendsdb}/valo_<id>_rehearsal" ;; *) echo "STOP: .env.remote is not the production URL shape" ;; esac
# sha/same fail CLOSED: an unreadable sidecar, a missing key or a malformed hash is a STOP, never "EQUAL". (Two
# failed reads used to compare two empty strings and print EQUAL; final review of the process, finding 3.)
sha() { "$PY" -c "import json, re, sys; h = json.load(open(sys.argv[1], encoding='utf-8'))['artifact']['sha256']; assert isinstance(h, str) and re.fullmatch('[0-9a-f]{64}', h), f'not a sha256: {h!r}'; print(h)" "$1"; }
same() { a="$(sha "$1")" || { echo "STOP: cannot read a hash from $1"; return 1; }; b="$(sha "$2")" || { echo "STOP: cannot read a hash from $2"; return 1; }; if [ "$a" = "$b" ]; then echo "EQUAL $a"; else echo "DIFFERENT: $1 $a vs $2 $b"; return 1; fi; }
```

**Every block must be able to fail.** `set -o pipefail` stays on for the session, because without it a command piped
into `tee` reports `tee`'s success. Any helper that compares two values must fail closed on an unreadable input.
Every `psql` script that sets variables with `\gset` must check them afterwards, because a NULL result *unsets*
the variable. Use the helpers above, not the older ones in `impact-rc3/README.md` §0, which print `EQUAL` when both
reads fail.

### Where things are

| what | where |
|---|---|
| declarations and results (append-only ledger) | `docs/superpowers/2026-09-07-predeclared-values.md` |
| the scorer | `app/scoring/impact.py` (`build_impact_rows_for_match`, `_time_factor`, `IMPACT_CALCULATION_VERSION`) |
| one scoring configuration | `app/scoring/impact_config.py` (`ImpactScoringConfig`) |
| declared comparators, manifest, fingerprints | `app/scoring/impact_manifest.py` (`COMPARATORS`, `HASHED_SOURCES`, `SOURCE_FINGERPRINT_VERSION`) |
| the one runtime switch | `app/scoring/impact_runtime.py` (`ACTIVE_MANIFEST`) |
| ingestion's refusal to write under the wrong code | `app/scoring/ingest_preflight.py`, `scripts/sql/release_write_gate.sql` |
| release tools | `scripts/freeze_impact_candidate.py`, `export_impact_artifact.py`, `release_candidate_review.py`, `compare_rc3_decomposition.py`, `swap_impact_scores.py`, `install_release_write_gate.py`, `release_preflight.py`, `prewarm_player_cache_ids.py`, `verify_player_cache_coverage.py`, `verify_cache_matches_scores.py`, `catchup_inventory.py` (§I) |
| a worked example of every stage | `impact-rc3/README.md` (first release, schema migrations included), `impact-v4/README.md` (scoring-only) |

---

## The shape of it

```
 A. decide ─► B. measure ─► C. plan ─► D. implement ─► E. prove ─► F. freeze & review ─► G. rehearse ─► H. activate ─► I. hold & reopen ─► J. close out
   (ledger)     (ledger)     (plan +     (branch,      (reference,   (production reads,     (fresh restore)   (one gated      (48 h, gate      (ledger,
                              reviews)    flags off)    equivalence)  manifest, reviews)                       window)          closed)          memory)
```

**Owner gates** — stop and get an explicit decision at each one. None is implied by an earlier approval.

| gate | before | the question |
|---|---|---|
| G1 | B → C | Does the measurement clear the declared stop rule, and does the formula ship? |
| G2 | C → D | Is the plan approved for **branch-only** implementation, after external review? |
| G3 | E → F | Branch review clean (§E4)? The release's F commands written in its runbook (§F)? May production be **read**, and a rehearsal restore be **created** on the production instance, for the freeze and the reviews? |
| G3s | before F, **schema releases only** | The schema-only release rehearsed (§D′)? **The recovery gate (§H0) cleared** and a fresh restore point recorded? May it migrate production and deploy, **with no activation**? |
| G4 | F → G | Owner's last look at the site comparison; approval of the reviews |
| G5 | G → H | Rehearsal passed end to end, including the real-data tests; concurrency measured or explicitly waived **for this release**; the swap-to-deploy exposure policy chosen; the window's timing and the ingestion freeze accepted; storage and recovery (§H0) confirmed |
| G6 | during H | Swap. Merge and deploy of the activation checkout |
| G7 | I | Reopen the gate: catch-up shown complete, nothing unexplained in the hold |

**The recovery gate (§H0) is cleared before the first write to any production table**: D′'s migration for a
schema-bearing release, and H1's swap otherwise. `CREATE DATABASE` for a rehearsal restore is not such a write; it
touches no production table.

A **defect found at any gate goes back to the earliest phase it invalidates.** A scorer change after F means a
re-freeze and new reviews, because the manifest digests the scoring sources.

---

## A. Decide — declare before measuring

1. Write a **DECLARATION** entry in the ledger *before* running anything: the arms (the change and its comparator,
   always including the shipped formula as `P0`), the targets, the protocol and its floors, **numbered predictions
   with confidence**, and a **stop rule** saying which outcome means the change does not ship.
2. The measurement harness MUST score the shipped configuration explicitly. Load it from the active manifest *as
   data*, or from `COMPARATORS[...]`, never from the scorer's defaults. Use the ex-ante mode for outcome targets:
   anything that reads round N+1 abstains under ex-ante, so contrasts are always within one mode.
3. Include an **identity gate**: the harness with no variant must reproduce the shipped rows bit for bit on a sample,
   or the run stops.
4. Commit the declaration on its own, then measure.

## B. Measure — and score the predictions, including the wrong ones

1. Run the arms. One replay per arm feeds every target. Record the artifacts' paths and hashes.
2. Write the **RESULT** entry: every contrast with its interval and verdict, row motion (what the site will visibly
   do), and **every prediction scored right or WRONG**. A wrong prediction gets a sentence on what it means; it is
   never explained away.
3. Apply the stop rule as written. **Gate G1.**

## C. Plan the release — only the deltas

Write `docs/superpowers/plans/<date>-impact-<id>-plan.md`, modelled on `plans/2026-09-21-impact-v4-no-time-factor-plan.md`.
It covers:

- **what ships** — the flags, defaulting off, and exactly what each one changes and does not change;
- **sequencing** — normally one gated window (§H). The code cannot merge ahead of activation: any change to a hashed
  source makes the live manifest fail verification in every fresh process;
- **the implementation list**, per file, with its tests;
- **the equivalence proof** (§E);
- **freeze and review** deltas, and **rehearsal and activation** deltas;
- **the three cohorts** — measurement (fixed now), review (fixed at freeze), activation (fixed when the gate closes) —
  never conflated;
- **the rollback** and its validity window;
- **a review log**.

Get it **externally reviewed** (read-only, no production access) until a review finds no structural blocker, and
log every finding with its disposition. **Gate G2.**

Then create the release's runbook, `docs/superpowers/impact-<id>/README.md`, from the template in §K. It is the live
checklist for this release, and every later phase ticks it.

## D. Implement — a fresh branch, test-first, flags off

Branch from the commit that carries the plan. For each item, **write the test, run it, show it fails, then write the
code.** The rules that hold for every scoring change:

- **Flags default OFF, and OFF must be bit-identical** to the live formula: the same rows, no new queries, no new keys
  in anything a consumer sees (observer contexts, kwargs dicts). Put new inputs behind the flag.
- **Wire the flag through three places**: `ImpactScoringConfig` (and `build_kwargs`, emitting it **only when True**
  so older configurations' kwargs stay identical), `build_impact_rows_for_match`, and the scorer internals.
  Incompatible combinations raise, in the config and in the scorer.
- **Serialise only when True** in `impact_manifest.config_to_dict`, and read a missing key as False in
  `config_from_dict`. Older frozen comparators then stay byte-identical. **Declare the new comparator in
  `COMPARATORS`**, so a manifest that drops a flag fails `verify_manifest`, and add omission tests.
- **Generate the manifest's description from the flags.** Never hard-code a formula description that a later release
  would freeze wrongly.
- **Any new scorer input is provenance.** Add it to `_FINGERPRINT_QUERIES`, bump `SOURCE_FINGERPRINT_VERSION` and add
  a mutation test per input. Add its model file to `HASHED_SOURCES`, and add its table to the swap's `SOURCE_TABLES`
  (digests and locks) and to the write gate. **Never rewrite old frozen evidence.** And check the swap's rollback
  against log entries written *before* the table joined, where absent is not the same as changed.
- **Consumers outside the scorer** (evaluation, refit, diagnostic shims) keep their semantics **explicitly**, with a
  comment and a test that pins it. Don't let them silently follow a runtime flag.
- **Persisted semantics**: prefer no scoring-column migration. `scoring_version` says which formula wrote a row.
  Record what each column now means in the `ImpactScore` docstring.
- **Tests that encode "the current version is the newest"** must be fixed to key on the formula's generation, not on
  the active manifest.
- **Release tools stay parameterised.** No release-specific default names, identities or versions. Everything is
  explicit and validated.

### D′. Schema-bearing releases only: ship the schema first, as its own release

If the change adds or alters a table or column the scorer reads, **production must be at the new schema before §F**.
The freeze fingerprints the cohort by querying production under the *new* fingerprint contract, so on unmigrated
production it fails with a missing relation or column. The order, modelled on rc3's Stage 7
(`impact-rc3/README.md` §6.8 and §7):

1. The migrations, and any gate or `SOURCE_TABLES` changes for new tables, go in a **schema-only commit with no
   activation**. `ACTIVE_MANIFEST` and `IMPACT_CALCULATION_VERSION` are unchanged. Old code must keep scoring
   correctly on the new schema, so every new column is nullable or defaulted, and old code never reads it.
2. **Rehearse it on a restore**: migrate, timed; install or refresh the gate; serve the site from that checkout; then
   the **R2 dry run** — maintenance mode, `alembic downgrade` to the previous head, preflight, and confirm the gate
   survives the downgrade.
3. **Clear the recovery gate (§H0) first.** This migration is the release's first write to a production table, so
   point-in-time restore must cover it, or backup-only recovery must be accepted explicitly for it, with R2 and R3
   written into the runbook. Then **gate G3s**. Preflight and back up production (`pg_dump`, then
   `pg_restore --list`), record the preflight's server time as the restore point, migrate by hand from a clean
   checkout of exactly the commit that will merge, refresh the gate, merge, and check that the site is healthy.
   Production still scores with the live release.
4. Only now continue to §F, from a restore taken **after** the schema release.

A weights-only or scorer-logic-only release has no D′.

## E. Prove — against an independent reference, never against yourself

A wrapper or hook that reproduces the change captures whatever scorer the *current checkout* imports. Run it on the
implementation branch and both sides execute the new code: a shared regression would match itself.

1. **Declare the release first** — a ledger DECLARATION (v4's is "DECLARATION 13"), **committed before any
   comparison runs**. It pins:
   - the measurement cohort (the id list, and a cohort fingerprint under the *current* fingerprint contract);
   - the new fingerprint contract;
   - the equivalence predictions for **both** scoring modes;
   - any declared extension beyond the measured reference (a clamp, an ambiguity rule).
   Items that need production are marked declared-at-freeze.
2. **Build the reference from unchanged code.** Make a separate `git worktree` at the measurement commit, where the
   scorer equals production. Add a **scripts-only** commit with a row-dump mode. Then prove the scorer is untouched
   with an **anchored, counted** check — from `webapp/`, a bare `webapp/app` pathspec matches nothing and an empty
   diff then proves nothing:
   ```bash
   n=$(git ls-tree -r --name-only <commit> -- :/webapp/app | wc -l)
   [ "$n" -gt 0 ] || echo "STOP: the pathspec matched no files"
   git diff --quiet <commit> HEAD -- :/webapp/app && echo "scorer identical ($n files)" || echo "STOP: webapp/app changed"
   ```
   The dumper must make the same check itself, with the same count, and refuse to run otherwise. v4's does, with
   `-- app` run from `webapp/`, which matches 151 files. Read the live configuration from the original frozen
   manifest **as data**. Dump every `CalculatedImpact` field for the shipped arm and each new arm, ex-ante
   **and** realized, as canonical CSV sorted by `(round_id, match_player_id)`. **Record the sha256s in a ledger
   addendum before comparing.**
3. **Compare** with a checker modelled on `scripts/compare_v4_reference.py`:
   - flags-off vs the shipped arm, and each new comparator vs its arm;
   - both modes, **row by key**, every field;
   - `scoring_version` checked **separately**, against a **required** expected value;
   - plus the full-cohort flags-off comparison as its own command.
   The checker MUST hash the reference file it compares and match it against the pinned hash and row count, refuse
   duplicate keys, and **exit nonzero** on any difference. **Any difference stops the work.** Eliminate it in the
   implementation, or report it. Never adjust the reference.
4. **Suites and review.**
   - Run offline on 3.11 and 3.13, then the DB tests on a local `*_test` scratch database.
   - Compare **named** failures against `origin/main` run the same way. Do not grandfather anything as
     "environmental" without that comparison.
   - Tests that resolve the *live* manifest will fail on the branch, because its digests no longer match. That is
     expected, and it goes green at activation. Name them and their single cause in the RESULT; don't hide them.
   - Then a **read-only external code review** of the whole branch. Fix what it confirms, with tests.
5. Write the **RESULT** for the release declaration, scoring every prediction. **Gate G3.**

## F. Freeze and review (production reads, plus one restore)

**Before gate G3, write this release's F commands into its runbook, with its own values.** Never paste rc3's: its
§4 asserts schema 0007, 3,125 matches, an absent gate and `valo_rc3_rehearsal`, all of which were rc3's preparation
state, not yours. Each step's expectations come from step 0's baseline, recorded read-only.

**One snapshot, three uses.** The review cohort is **chosen on the restore**, frozen from production, and reviewed
against the restore. That only holds if the restore and production agree on those matches' source rows, so step 3
checks it. Otherwise a match ingested after the dump, or an admin correction to an old one, would pass the freeze
and then fail every review.

0. **Baseline, backup and restore.** Record production's baseline read-only: alembic head, match count, max match id,
   and gate state, release id and admin id. These become every later `--expect-*`. Preflight production with them,
   `pg_dump` it (the backup), run `CREATE DATABASE valo_<id>_rehearsal` on the instance (the only statement before
   activation that uses the production URL and is not a read; it touches no production table), restore into it,
   and install its gate closed. Preflight the restore with the same counts. **Measure storage while you are here**
   (§H0). The same restore serves §G.
1. **Fix the review cohort by querying the restore**: the previous release's review matches, plus one match that
   demonstrably exercises each new rule (chosen by query, recorded with the query). The freeze requires `--matches`,
   and acceptance requires review results covering **exactly** that set.
2. **Freeze declaration** in the ledger, before freezing. It records:
   - the review cohort;
   - K-chain expectations: K3 (comparator at the implementation commit) = K4 (through the frozen manifest) on a
     production snapshot, rehearsal-grade; **K4 = K5 (activation checkout) in the window, binding**;
   - the production row-motion tolerance, from the measured motion;
   - how the activation cohort will be fixed.
3. **Freeze** from a clean, committed tree:
   ```bash
   DATABASE_URL="$PROD" $PY scripts/freeze_impact_candidate.py --candidate-id <cand> --release-comparator <cmp> \
       --activation-version N --matches <cohort> --out "$REL/candidate-manifest.json" || echo "STOP: exit $?"
   ```
   Then, **before committing it**, prove the restore holds the same source rows as the freeze for every cohort match:
   ```bash
   DATABASE_URL="$REH" $PY -c "import sys; from app.db import SessionLocal; from app.scoring.impact_manifest import load_manifest, verify_source_snapshots; verify_source_snapshots(SessionLocal(), load_manifest(sys.argv[1])); print('restore matches the freeze')" "$REL/candidate-manifest.json" || echo "STOP: re-restore and freeze again"
   ```
   Commit the manifest **alone**. Never edit it: review results are tied to its hash.
4. **K3, K4** (`export_impact_artifact.py --comparator <cmp>` and `--manifest "$REL/candidate-manifest.json"`; the
   `same` helper is in §0 above). Hashes must be equal. This is **`PREP_CHAIN`**: preparation-grade, and **never**
   the hash a later verification expects (§H1.3).
5. **Reviews, against the rehearsal restore** from step 0 (the review tool checks the manifest's fingerprints):
   - `release_candidate_review.py` per the rc3 runbook §5.3 pattern, for the release's cohort;
   - `compare_rc3_decomposition.py --manifest "$REL/candidate-manifest.json" --matches <cohort>`, which reviews the
     manifest's **release comparator**. **Its assumptions must hold for your release**: it takes raw econ from the
     `V2_30_80_BONUS` comparator and requires the same `econ_model`, so a release that changes the econ model needs
     its own decomposition check, with its assumptions declared and a defect-reinstatement test that shows it can
     fail. Reuse this one only when the econ model is unchanged;
   - `--results "$REL/review-results.json"` covering exactly the frozen cohort.
6. **The owner's last look**: a side-by-side of several friend-group matches on the site, before and after (rc3's
   `SUMMARY.md`), especially when the RESULT's row motion says many matches reorder. **Gate G4.**

## G. Rehearse — the whole window on a fresh restore, timed

1. **Use a fresh restore.** F step 0's restore serves if nothing but reviews has touched it. Otherwise drop it —
   type the name by hand and read it twice — and restore again the same way.
2. **The activation checkout**, in two checks, in this order:
   - **Before branching**, on the reviewed tip: the chain surface is identical to the freeze. Use the `:/`-anchored
     check with the file count (`impact-rc3/README.md` §6). Any difference is a STOP unless this release has recorded
     its own exception, with its evidence, in its runbook. rc3's `write_gate.py` exception does not carry over.
   - Then a local branch off that tip, with **exactly one commit**: `ACTIVE_MANIFEST` →
     `docs/superpowers/impact-<id>/candidate-manifest.json` and `IMPACT_CALCULATION_VERSION` → N, with a history
     comment. Assert that commit is exactly the permitted edit. `git diff --name-only HEAD~1 HEAD` must print only
     `webapp/app/scoring/impact_runtime.py` and `webapp/app/scoring/impact.py`. Then, from the checkout,
     `active_scoring_config().config_id == "<cmp>"` and `IMPACT_CALCULATION_VERSION == N`. `verify_manifest` passes
     only if `impact.py` moved in nothing but the masked version assignment.
3. **Rehearse the window exactly as §H will run it**, recording every duration in `$ART/rehearsal/durations.md`:
   - drain, stranded-match check, close the gate;
   - the **pre-swap capture** of the live rows (a prerequisite for the swap);
   - K4 = K5, build, verify-build (N), swap, deploy locally, restart, verify-live (N);
   - cache DELETE, prewarm, coverage, and **cache/table agreement** (`verify_cache_matches_scores.py`);
   - the gate probes: a stale checkout is refused, a write without identity is refused, the preflighted checkout is
     allowed;
   - **the database tests on real data**, after the swap, from the activation checkout
     (`VALO_TEST_DATABASE_URL="$REH"`, the rc3 §6.4a set, deselecting the committing-wrapper test). The table then
     holds version N and the checkout computes N, so `test_builder_matches_stored_values` checks the loaded table
     end to end. A skip is a finding;
   - **concurrency**: page latencies against the served restore, across a swap and a rollback (rc3 §6.6, including
     its 2xx floor). If the owner declines to measure it, that is a **new, dated waiver for this release** — rc3's
     waiver is not a standing approval;
   - the **exposure policy** for the swap-to-deploy interval, chosen by the owner for this release: accept it, as
     rc3 did (§8.4.0), or `MAINTENANCE_MODE=1` for its duration (rehearse that too);
   - **rollback with its full verification**, then forward again;
   - an **interrupted swap** changes nothing.
4. On a **second restore**, the case that consumes the rollback precondition. First run the forward path on it,
   exactly as step 3 did: build, verify-build, swap, deploy locally, restart, verify-live. Without a completed swap
   there is nothing to roll back, and the refusal would prove nothing. Then open the gate for `<cand>`, ingest one
   match, check it committed with its scores, close the gate, and assert `rollback` refuses **specifically** with
   "matches were ingested after the swap".
5. **Write the window's commands into the release's runbook** from what actually ran. **Gate G5.**

## H. Activate — one gated window

**H0. Before the window.**
- Storage for live + staged + retained tables, indexes and WAL headroom is confirmed.
- **Recovery gate** (rc3 plan, recovery gate; `impact-rc3/README.md` §7.1). Confirm, on the Render Recovery page,
  that point-in-time restore covers the window, and record its retention. If it does not, the owner must accept
  backup-only recovery explicitly, for this release. **R3** — restore into a new instance, then repoint the web
  service and `.env.remote` — is written into the runbook as an executable last resort.
- The exact pre-release **production `main` SHA is recorded** (it is the rollback deployment).
- The retained table name is free.
- A separate checkout of the release-tools commit exists for any database rollback.

**H1. The window**, from a clean checkout of the activation PR's head, in this order:

1. Preflight. **Drain ingestion**: stop every scheduled or running refresh, and wait for the last one to exit.
   Check for **stranded matches** — a match committed without its scores (`find_unscored_match_ids`, in
   **`app.scoring.impact`**, not `ingest_preflight`), the shape of
   the 3133 incident. Then close the gate and hand it to this release's runbook identity, explicitly:
   `install_release_write_gate.py --expect-database valowithfriendsdb --state closed --release-id <live cand>
   --admin-id <id>-runbook --note "..."`. That moment fixes the **activation cohort**: every match now in
   production.
   Take a **fresh backup** now (`pg_dump`, then `pg_restore --list`), and record the preflight's printed server time.
   It is the restore point for R3. The preparation dump from §F is not this checkpoint.
2. **Pre-swap capture**: a canonical `COPY (SELECT <every persisted column> FROM impact_scores ORDER BY round_id,
   match_player_id)`, with its sha256, row count and the cohort's fingerprints recorded.
3. **K4 then K5** on the gated dataset. Equal, or STOP. **Their hash is `CHAIN`** — the value every verification in
   this window passes as `--expect-comparison-sha256`. It is not F4's `PREP_CHAIN`: the activation cohort includes
   every match ingested since F, so the preparation hash cannot match `verify-build` even when K4 = K5 exactly. The
   rehearsal likewise verifies against its own export's hash.
4. **Row motion first**: the pre-swap capture against K5's `.load.csv`, by key, counting rows whose `impact`
   changed and matches whose players' mean-Impact order changed. **Outside the declared band, STOP.** v4's window
   skipped this and swapped anyway. It was measured only at close-out (32.42% / 72.13%, inside the band), so make it a
   printed line in the runbook's commands where it cannot be skipped silently.
   Then `build`, then `verify-build --expect-scoring-version N`, then **capture the prewarm lists**, then `swap`:
   ```bash
   S="--expect-database valowithfriendsdb --identity <id>-runbook --previous-table impact_scores_v<N-1> --rolled-back-table impact_scores_<id>_rolled_back"
   DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py swap --yes $S; echo "swap exit $?"
   DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py state $S
   ```
   Exit 4 is a lock timeout that changed nothing: retry. Exit 3: read the reason. Anything else: run `state` first.
5. **Merge the activation PR** and wait for Render's deploy. **Restart every process.** `_load_verified` caches the
   manifest per process, so a long-running worker must not carry on under the old one.
6. `verify-live --expect-scoring-version N`.
7. **Cache barriers**: `DELETE FROM player_view_cache` (never TRUNCATE), prewarm the roster, coverage,
   **agreement**, the site stats refresh, then the background prewarm with its pid recorded (`impact-rc3/README.md`
   §8.6's chain). Confirm the live cache version includes N.
8. **Acceptance**: every match equals its replay under the active configuration (`replay_diffs`, rc3 §8.7). Then
   **`verify-live` once more**, with every argument (artifact, sidecar, comparison, `--approved`, `--manifest`,
   `--expect-comparison-sha256`, `--expect-scoring-version N`, and the swap names). It is the final input-fingerprint
   and approved-results check: an admin correction plus a rescore can pass the replay while departing from the
   approved export. Then page checks.
9. **The rollback window is open.** Resolve every doubt now, with the gate still closed.

## I. Hold and reopen

1. **Observation hold: 48 hours with the gate closed.** Ordinary rollback stays complete throughout. It exists so
   that problems on the live site surface while rollback is still cheap. The owner may end it early once they have
   looked (rc3 after 21h47m, v4 after about 14h). Record the decision and the time in the runbook, **in the repo**:
   v4's early end was agreed in a session but not written down, and the next session read the runbook and believed
   the hold was still running.
2. **Catch-up inventory, before reopening** (rc3 §8.10a). Read-only, and it may run with the gate closed. Launch the
   scraper Chrome (`scripts/launch_trackergg_chrome.ps1`; if port 9222 stays closed, start `chrome.exe` with the same
   flags through `Start-Process`, which survives the launching shell), then:
   ```bash
   # database half: one row per roster player (name, id, matches, newest played_at, newest external_id, max id)
   "$PGBIN/psql" -X -A -F $'\t' -t -c "<per-player boundary query, impact-v4/README.md §I>" "$PROD" > "$ART/window/catchup-boundary.tsv"
   # browser half: probe one player first, then the roster as a detached job
   DATABASE_URL="$PROD" $PY scripts/catchup_inventory.py "$ART/window/catchup-boundary.tsv" "$ART/window/catchup-inventory.json" "NPrightdolphin#NA1"
   ```
   `catchup_inventory.py` walks each player's All-Acts history down to **that player's own** newest-ingested
   `external_id`, and every id above it goes into the expected set. It never stops at the first already-known match.
   An unknown timestamp, a repeated cursor, a private profile, an unreachable boundary or a cap is **INCOMPLETE**,
   never assumed covered. **The adapter reports a private profile as `NO_HISTORY`**, not `PRIVATE`: for a player the
   database already has matches for, read `NO_HISTORY` as private until the page says otherwise. **Gate G7**: the
   inventory is complete, or the owner accepts each incomplete player by name, and nothing in the hold is unexplained.
3. Open the gate **explicitly for `<cand>`**:
   `install_release_write_gate.py --expect-database valowithfriendsdb --state open --release-id <cand> --admin-id <id>-runbook --note "..."`.
   Then **ingest one match as a canary**: `ingest_trackergg_player.py "<player>" --count 1` for a player whose
   inventory holds exactly one new match at position 0. It must carry `scoring_version` N and equal its replay
   before anything else is ingested. **From that first match, ordinary rollback is gone: fix forward**, as a new
   release through this same process. Then ingest the rest (`refresh_tracked_players.py --count 20`, detached,
   with `--count` at least the deepest boundary position).
4. **Reconcile after reopening.** Every id in the expected sets must be present **and scored** at version N, with
   rows = rounds × 10, and all of them must equal their replay. Report every difference; a clean exit status is not
   completion. Then **sweep again through the reopening moment**: take a fresh boundary and rerun the inventory. Every
   public player should come back at position 0.
5. **Probe that a stale checkout is refused**: probe (a) from the pre-release worktree. **Only while nothing is
   ingesting**, because it compares `sum(impact)` before and after, and a concurrent ingest breaks that comparison
   without anything being wrong.

### Rollback (only before the gate reopens)

In this order, and no other:
1. Stop the background prewarm, and wait on its pid.
2. `rollback` with the explicit names, from the release-tools checkout.
3. Deploy the **recorded pre-release revision** — not a revert commit, because the hashed sources changed — and
   restart.
4. Verify:
   - `state` shows `rollback rolled back`;
   - the restored table's canonical export hashes **equal** to the pre-swap capture;
   - the old manifest verifies under the old revision;
   - a replay of the review cohort matches;
   - cache DELETE, prewarm from the reverted checkout, agreement.
5. The gate stays closed until those pass **and** the owner decides to reopen it for the old release.

`--accept-source-drift` only when the **live** scores are the emergency. It records the override.

## J. Close out

1. Ledger activation note: commit SHAs, durations, hashes, gate transitions.
2. Retention: keep the retained table about two weeks, longer than the recovery window. Drop older ones on their own
   schedule. Drop the rehearsal databases on the Render instance (v4's two held about 800 MB) and remove the
   rehearsal and release-tools worktrees, each on the owner's say-so. Once ingestion has reopened, neither can be
   used for a rollback any more.
3. Analysis hygiene: anything reading stored rows asserts a single `scoring_version`
   (`load_stored_observations` does not filter).
4. **Update this document** with anything the release taught you: a new trap in §0, a new rule in §D. That is how the
   next release avoids replanning.

---

## K. Template: the release's runbook (`docs/superpowers/impact-<id>/README.md`)

```markdown
# Impact <id> — release runbook

Status (<date>): <one line: the phase, what is live, what is next>.
Process: ../SCORING-RELEASE-PROCESS.md. Plan: ../plans/<plan>.md. Ledger entries: <names>.

| | value |
|---|---|
| candidate id / comparator / activation version | <cand> / <cmp> / N |
| branch / implementation tip | ... |
| reference commit / worktree | ... |
| manifest | impact-<id>/candidate-manifest.json @ <sha> |
| retained table / rolled-back table / admin identity | impact_scores_v<N-1> / impact_scores_<id>_rolled_back / <id>-runbook |
| artifacts | ~/Documents/valo-backups/<id>-release/ |
| pre-release production main SHA | (recorded at H0) |

## Phase checklist
| phase | state | evidence (commit / ledger entry / artifact hash) |
|---|---|---|
| A decide · B measure · C plan · D implement · E prove | | |
| F freeze & review · G rehearse · H activate · I hold & reopen · J close out | | |

## Commands for this release
(written from the rehearsal, in the order the window runs them)

## Durations measured in rehearsal
## Decisions the owner made, with dates
```
