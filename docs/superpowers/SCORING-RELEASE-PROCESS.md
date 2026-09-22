# How a scoring change gets from an idea to the live site

**Read this first if you are an agent asked to change how Impact is scored, or to ship a change that already
exists.** It is the general process, distilled from two real releases: rc3 (`impact-rc3/README.md`, shipped
2026-09-18) and v4 (`impact-v4/README.md`). Every step here exists because skipping it caused, or would have
caused, a real defect in one of them. Where a step names "the release's runbook", that is the per-release file this
process tells you to create in §3. **Do not replan the process; plan only the release.**

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
export PYTHONIOENCODING=utf-8
PY=.venv313/Scripts/python.exe
PGBIN="/c/Program Files/PostgreSQL/18/bin"
ART="$HOME/Documents/valo-backups/<id>-release"; mkdir -p "$ART"
REL=../docs/superpowers/impact-<id>
PROD="$(grep '^DATABASE_URL' .env.remote | cut -d= -f2-)"
case "$PROD" in */valowithfriendsdb) REH="${PROD%/valowithfriendsdb}/valo_<id>_rehearsal" ;; *) echo "STOP: .env.remote is not the production URL shape" ;; esac
```

### Where things are

| what | where |
|---|---|
| declarations and results (append-only ledger) | `docs/superpowers/2026-09-07-predeclared-values.md` |
| the scorer | `app/scoring/impact.py` (`build_impact_rows_for_match`, `_time_factor`, `IMPACT_CALCULATION_VERSION`) |
| one scoring configuration | `app/scoring/impact_config.py` (`ImpactScoringConfig`) |
| declared comparators, manifest, fingerprints | `app/scoring/impact_manifest.py` (`COMPARATORS`, `HASHED_SOURCES`, `SOURCE_FINGERPRINT_VERSION`) |
| the one runtime switch | `app/scoring/impact_runtime.py` (`ACTIVE_MANIFEST`) |
| ingestion's refusal to write under the wrong code | `app/scoring/ingest_preflight.py`, `scripts/sql/release_write_gate.sql` |
| release tools | `scripts/freeze_impact_candidate.py`, `export_impact_artifact.py`, `release_candidate_review.py`, `compare_rc3_decomposition.py`, `swap_impact_scores.py`, `install_release_write_gate.py`, `release_preflight.py`, `prewarm_player_cache_ids.py`, `verify_player_cache_coverage.py`, `verify_cache_matches_scores.py` |
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
| G3 | E → F | Branch review clean (§E4)? May production be **read** for the freeze and reviews? |
| G4 | F → G | Owner's last look at the site comparison; approval of the reviews; may a rehearsal database be **created** on the production instance? |
| G5 | G → H | Rehearsal passed end to end; the window's timing and the ingestion freeze accepted; storage confirmed |
| G6 | during H | Swap. Merge and deploy of the activation checkout |
| G7 | I | Reopen the gate: catch-up shown complete, nothing unexplained in the hold |

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

If the change needs a **schema migration**, add rc3's Stage 7 (`impact-rc3/README.md` §7): a PR that migrates and
installs the gate without activating, rehearsed first, with its own rollback (R2).

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
   scorer equals production. Add a **scripts-only** commit with a row-dump mode, assert `git diff <commit> --
   webapp/app` is empty (the dumper should refuse otherwise), and read the live configuration from the original
   frozen manifest **as data**. Dump every `CalculatedImpact` field for the shipped arm and each new arm, ex-ante
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

## F. Freeze and review (production reads only)

1. **Fix the review cohort** by querying production: the previous release's review matches, plus one match that
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
   Commit the manifest **alone**. Never edit it: review results are tied to its hash.
4. **K3, K4** (`export_impact_artifact.py --comparator <cmp>` and `--manifest "$REL/candidate-manifest.json"`; the
   `same` helper is in `impact-rc3/README.md` §0). Hashes must be equal.
5. **Reviews, against a rehearsal restore** (§G1 creates it; the review tool checks the manifest's fingerprints):
   - `release_candidate_review.py` per the rc3 runbook §5.3 pattern, for the release's cohort;
   - `compare_rc3_decomposition.py --manifest "$REL/candidate-manifest.json" --matches <cohort>`, which reviews the
     manifest's **release comparator**;
   - `--results "$REL/review-results.json"` covering exactly the frozen cohort.
6. **The owner's last look**: a side-by-side of several friend-group matches on the site, before and after (rc3's
   `SUMMARY.md`), especially when the RESULT's row motion says many matches reorder. **Gate G4.**

## G. Rehearse — the whole window on a fresh restore, timed

1. **Restore.** Take a backup dump of production, `CREATE DATABASE valo_<id>_rehearsal` on the instance (the only
   non-read before activation, and it touches no production table), restore into it, and install its gate closed
   (`impact-rc3/README.md` §4). **Measure storage now** (§H0).
2. **The activation checkout**: a local branch off **the reviewed tip**, with exactly one commit: `ACTIVE_MANIFEST` →
   `docs/superpowers/impact-<id>/candidate-manifest.json` and `IMPACT_CALCULATION_VERSION` → N, with a history
   comment. Before relying on it, assert the chain surface is identical to the freeze (the `:/`-anchored check with
   the file count, `impact-rc3/README.md` §6). Any file other than a recorded exception is a STOP.
3. **Rehearse the window exactly as §H will run it**, recording every duration in `$ART/rehearsal/durations.md`:
   - drain, stranded-match check, close the gate;
   - the **pre-swap capture** of the live rows (a prerequisite for the swap);
   - K4 = K5, build, verify-build (N), swap, deploy locally, restart, verify-live (N);
   - cache DELETE, prewarm, coverage, and **cache/table agreement** (`verify_cache_matches_scores.py`);
   - the gate probes: a stale checkout is refused, a write without identity is refused, the preflighted checkout is
     allowed;
   - **rollback with its full verification**, then forward again;
   - an **interrupted swap** changes nothing.
4. On a **second restore**, the case that consumes the rollback precondition: open the gate, ingest one match, close
   it, and assert `rollback` refuses **specifically** with "matches were ingested after the swap".
5. **Write the window's commands into the release's runbook** from what actually ran. **Gate G5.**

## H. Activate — one gated window

**H0. Before the window.**
- Storage for live + staged + retained tables, indexes and WAL headroom is confirmed.
- The exact pre-release **production `main` SHA is recorded** (it is the rollback deployment).
- The retained table name is free.
- A separate checkout of the release-tools commit exists for any database rollback.

**H1. The window**, from a clean checkout of the activation PR's head, in this order:

1. Preflight. **Drain ingestion**, check for **stranded matches**, and close the gate. That moment fixes the
   **activation cohort**: every match now in production.
2. **Pre-swap capture**: a canonical `COPY (SELECT <every persisted column> FROM impact_scores ORDER BY round_id,
   match_player_id)`, with its sha256, row count and the cohort's fingerprints recorded.
3. **K4 then K5** on the gated dataset. Equal, or STOP.
4. `build`, then `verify-build --expect-scoring-version N`, then **capture the prewarm lists**, then `swap`:
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
8. **Acceptance**: every match equals its replay under the active configuration (`replay_diffs`, rc3 §8.7), then
   page checks.
9. **The rollback window is open.** Resolve every doubt now, with the gate still closed.

## I. Hold and reopen

1. **Observation hold: 48 hours with the gate closed.** Ordinary rollback stays complete throughout.
2. **Catch-up inventory** (rc3 §8.10a): a per-player boundary, not one date. Tracker.gg history pages hold 20
   matches. An unreachable boundary is **incomplete**, never assumed covered. **Show the catch-up is complete before
   reopening.** **Gate G7.**
3. Open the gate **explicitly for `<cand>`**:
   `install_release_write_gate.py --expect-database valowithfriendsdb --state open --release-id <cand> --note "..."`.
   Ingest; the first new match must carry `scoring_version` N and equal its replay. Probe that a stale checkout is
   refused. **From here ordinary rollback is gone: fix forward**, as a new release through this same process.

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
   schedule.
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
