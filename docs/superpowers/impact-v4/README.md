# Impact v4 — release runbook

Status (2026-09-23): **SWAPPED. Production serves `impact_scores` at version 4 as of 07:12:54 UTC. The gate is closed; the activation PR is open for the owner to merge and deploy.**
Manifest frozen at `2e5140e`. **The swap has happened**: production's `impact_scores` holds version 4 rows. The
deployed code is still rc3 until the activation PR is merged, which is the accepted swap-to-deploy exposure.

- Process: `../SCORING-RELEASE-PROCESS.md`. This file is v4's instance of it.
- Plan (the spec): `../plans/2026-09-21-impact-v4-no-time-factor-plan.md` (r5).
- Ledger (`../2026-09-07-predeclared-values.md`): the CORRECTION, DECLARATION 12 and RESULT, DECLARATION 13 with its
  ADDENDUM, and the RESULT for declaration 13 with its ADDENDUM.

**What ships:** no time factor — T = 1 for every kill and death, and T = 0 once the round is decided
(`enable_decided_only_time`). Assists on kills made after the round was decided are not paid
(`remove_post_decided_assists`). Both flags are on in `impact_v4`, and both default off everywhere else.

| | value |
|---|---|
| candidate id / comparator / activation version | `impact-v4` / `impact_v4` (diagnostic `impact_v4_n`) / **4** |
| branch / implementation tip | `impact-v4-implementation` @ `7f6c0ed` (not pushed) |
| reference commit / worktree | `c470670` on `v4-reference-f96aee9`, at `../vwft-v4-reference` |
| manifest | `impact-v4/candidate-manifest.json` — **not yet frozen** |
| retained / rolled-back table / admin identity | `impact_scores_v3` / `impact_scores_v4_rolled_back` / `v4-runbook` |
| artifacts | `~/Documents/valo-backups/v4-release/` |
| measurement corpus | local `valo_v4` (PG18, port 5434, 3,198 matches); scratch `valo_v4_test` |
| pre-release production `main` SHA | recorded at H0 |

## Phase checklist

| phase | state | evidence |
|---|---|---|
| A decide | done | DECLARATION 12, `cecd1c5` |
| B measure | done | RESULT declaration 12, `f96aee9`. `N vs P0` improves on both targets; stop rule cleared |
| C plan | done | plan r5, `4ecf7f0`, after four external reviews (R1–R11, S1–S6, T1–T5, U1–U3) |
| D implement | done | `71ad905` `de1494f` `2bfff71` `c8f1ce3` `8b72d0d` `af8d3d1` `edcec2a` `a9140fb` `27daaf2` |
| E prove | done | DECLARATION 13 `82ee277`; reference hashes `97ed0b0`; comparison tool `0003010`; RESULT `852dc49`; review fixes `9bb5618` `6d00d08`; addendum `7f6c0ed`. Seven comparisons, 0 rows differing over 674,530 rows each, both modes, byte-identical, and the hardened checker exits 0 |
| F freeze & review | **done, awaiting G4** | manifest `2e5140e` (LF-sha `2f33f137…`); PREP_CHAIN `2cd448e2…` (K3 = K4); reviews reconcile, decomposition 29,606 checks / 0 mismatches; RESULT `55c7fd8` |
| G rehearse | **done bar one item, awaiting G5** | Section G below. Forward path, caches, three probes, rollback verified byte-identical, forward again, interrupted swap changes nothing, post-ingest refusal exact. Real-data suite 17 passed / 1 skipped / 1 deselected. **Outstanding: ingest one real tracker.gg match after a swap and confirm it scores at version 4** |
| H activate | not started | |
| I hold & reopen | not started | |
| J close out | not started | |

## F. Freeze and review — v4's commands (process §F)

Written before gate G3, so nothing in F is improvised. Run from `webapp/` in Git Bash, one block at a time; a block
that prints `STOP` stops the phase. **Every value marked `<fill>` is recorded here when first read**, and never
guessed.

### Session setup (process §0)

```bash
cd "$(git rev-parse --show-toplevel)/webapp"
set -o pipefail   # a failing command piped into tee must still fail the block (final review, finding 1)
export PYTHONIOENCODING=utf-8
PY=.venv313/Scripts/python.exe
PGBIN="/c/Program Files/PostgreSQL/18/bin"
ART="$HOME/Documents/valo-backups/v4-release"; mkdir -p "$ART/chain" "$ART/rehearsal"
REL=../docs/superpowers/impact-v4
PROD="$(grep '^DATABASE_URL' .env.remote | cut -d= -f2-)"
case "$PROD" in */valowithfriendsdb) REH="${PROD%/valowithfriendsdb}/valo_v4_rehearsal" ;; *) echo "STOP: .env.remote is not the production URL shape" ;; esac
# sha/same fail CLOSED: an unreadable sidecar, a missing key or a malformed hash is a STOP, never "EQUAL". (Two
# failed reads used to compare two empty strings and print EQUAL; final review of the process, finding 3.)
sha() { "$PY" -c "import json, re, sys; h = json.load(open(sys.argv[1], encoding='utf-8'))['artifact']['sha256']; assert isinstance(h, str) and re.fullmatch('[0-9a-f]{64}', h), f'not a sha256: {h!r}'; print(h)" "$1"; }
same() { a="$(sha "$1")" || { echo "STOP: cannot read a hash from $1"; return 1; }; b="$(sha "$2")" || { echo "STOP: cannot read a hash from $2"; return 1; }; if [ "$a" = "$b" ]; then echo "EQUAL $a"; else echo "DIFFERENT: $1 $a vs $2 $b"; return 1; fi; }
git status --porcelain --untracked-files=all -- . | head -5    # must print nothing
```

### F0. Baseline, backup, restore

**Read-only baseline.** The printed values become every later `--expect-*`. Record them in the table below.

```bash
"$PGBIN/psql" -v ON_ERROR_STOP=1 -At \
    -c "SELECT current_database(), now()" \
    -c "SELECT version_num FROM alembic_version" \
    -c "SELECT count(*), max(id) FROM matches" \
    -c "SELECT state, release_id, admin_id FROM scoring_gate" \
    -c "SELECT pg_size_pretty(pg_database_size(current_database())), pg_size_pretty(pg_total_relation_size('impact_scores'))" \
    "$PROD" | tee "$ART/F0-baseline.txt" || echo "STOP: exit $?"
```

| baseline (F0) | value |
|---|---|
| server time | `2026-09-22 04:44:55 UTC` (preflight `04:45:14`) |
| alembic head | `0010` ✓ |
| matches / max match id | `3,649` / `3657` |
| gate state / release id / admin id | `open` / `impact-rc3` / `rc3-runbook` ✓ |
| database size / `impact_scores` size | 392 MB / 101 MB; 769,120 rows, all `scoring_version` 3 |
| `impact_scores_v1` / `impact_scores_v3` | present (rc3's, kept to 2026-10-03) / free ✓ |
| other databases on the instance | `valo_rc3_rehearsal` 324 MB, `valo_rc3_test` 10 MB, and now `valo_v4_rehearsal`; **confirmed on the Render dashboard 2026-09-22: 15 GB, 8.3% used** (~1.25 GB) |
| backup B0 | `B0-v4-valowithfriendsdb.dump`, 49.7 MB, 60 s, sha256 `0d1723cf4deffcd47ff0a2184115ea141bdcf69c69c4242ebda13c201d9789f5` |
| rehearsal restore | `valo_v4_rehearsal`: restored in 4 m 22 s; gate closed, `impact-rc3` / `v4-runbook`, guarding `players`; preflight equal to production |

**Stop and ask** if the alembic head is not `0010` or the gate is not `open`/`impact-rc3`. Either means production
is not in the state the plan assumes.

```bash
E_ALEMBIC=<fill>; E_MATCHES=<fill>; E_MAXID=<fill>
DATABASE_URL="$PROD" $PY scripts/release_preflight.py --expect-database valowithfriendsdb --expect-alembic "$E_ALEMBIC" \
    --expect-matches "$E_MATCHES" --expect-max-match-id "$E_MAXID" --expect-gate open \
  && time "$PGBIN/pg_dump" --dbname="$PROD" --format=custom --no-owner --no-privileges --file="$ART/B0-v4-valowithfriendsdb.dump" \
  && "$PGBIN/pg_restore" --list "$ART/B0-v4-valowithfriendsdb.dump" > "$ART/B0-v4.list" \
  && sha256sum "$ART/B0-v4-valowithfriendsdb.dump" | tee "$ART/B0-v4.sha256" \
  || echo "STOP: exit $?"
```

**The one non-read (gate G3).** It creates a second database on the instance and touches no production table.

```bash
"$PGBIN/psql" -v ON_ERROR_STOP=1 -c "CREATE DATABASE valo_v4_rehearsal" "$PROD" \
  && time "$PGBIN/pg_restore" --dbname="$REH" --no-owner --no-privileges --no-comments --exit-on-error "$ART/B0-v4-valowithfriendsdb.dump" \
  && DATABASE_URL="$REH" $PY scripts/install_release_write_gate.py --expect-database valo_v4_rehearsal \
    --state closed --release-id impact-rc3 --admin-id v4-runbook --note "v4 rehearsal restore" \
  && DATABASE_URL="$REH" $PY scripts/release_preflight.py --expect-database valo_v4_rehearsal --expect-alembic "$E_ALEMBIC" \
    --expect-matches "$E_MATCHES" --expect-max-match-id "$E_MAXID" --expect-gate closed \
  || echo "STOP: exit $?"
```

The restore's gate is reinstalled **from this branch**, so on the rehearsal it also guards `players`, as v4's will in
production.

### F1. Review cohort, chosen on the restore

```bash
"$PGBIN/psql" -v ON_ERROR_STOP=1 -At -f "$REL/review-cohort.sql" "$REH" | tee "$ART/F1-cohort.txt" || echo "STOP: exit $?"
```

The last line is `a|b|c|cohort`. Record the picks in the table below. Confirm (a) through the scorer itself, not only
the SQL:

```bash
A=<fill>; COHORT=<fill>
DATABASE_URL="$REH" $PY -c "
import sys
from app.db import SessionLocal
from app.scoring.impact import build_impact_rows_for_match
from app.scoring.impact_manifest import COMPARATORS, V4
n = [0]
def obs(round_number, kill_index, kill, context):
    n[0] += len((context.get('post_decided_assists') or {}).get('removed', []))
build_impact_rows_for_match(SessionLocal(), int(sys.argv[1]), kill_observer=obs, **COMPARATORS[V4].build_kwargs())
print(f'match {sys.argv[1]}: {n[0]} assists removed')
sys.exit(0 if n[0] else 1)" "$A" || echo "STOP: (a) removes nothing through the scorer"
```

| review cohort (F1) | match | query |
|---|---|---|
| rc3's thirteen | `3104,3129,3130,3131,3113,3118,3121,3114,3115,3116,3117,3120,3133` | rc3 freeze |
| (a) post-decided assist removed | **3655** | `review-cohort.sql` (a); `impact_v4` removes 1 assist, through the scorer |
| (b) kill after a defuse | **3652** | (b) |
| (c) Time Win, kill after 100 s | **3642** | (c) |

**Cohort (16):** `3104,3129,3130,3131,3113,3118,3121,3114,3115,3116,3117,3120,3133,3655,3652,3642`

### F2. Freeze declaration

In the ledger, **committed before F3**. It holds:
- the cohort above;
- K3 = K4 rehearsal-grade, **K4 = K5 binding in the window**;
- the production row-motion tolerance, declared around the measured 32.1% of rows and 72.0% of matches reordered
  (rc3 live, local corpus);
- the activation cohort, defined as every match in production when the gate closes at H1.1;
- the separability wording, "below both carried floors", for `N+A vs N` (R5.7).

### F3. Freeze, then prove the restore matches it

```bash
git status --porcelain --untracked-files=all -- . | head -5    # must print nothing: the freeze refuses a dirty tree
DATABASE_URL="$PROD" $PY scripts/freeze_impact_candidate.py --candidate-id impact-v4 --release-comparator impact_v4 \
    --activation-version 4 --matches "$COHORT" --out "$REL/candidate-manifest.json" \
  && DATABASE_URL="$REH" $PY -c "import sys; from app.db import SessionLocal; from app.scoring.impact_manifest import load_manifest, verify_source_snapshots; verify_source_snapshots(SessionLocal(), load_manifest(sys.argv[1])); print('restore matches the freeze')" "$REL/candidate-manifest.json" \
  || echo "STOP: exit $? (3 = the tree is not exactly a commit; a mismatch = re-restore and freeze again)"
```

Commit the manifest **alone**, and never edit it.

### F4. K3 and K4 (about 30 minutes each; keep the machine awake)

```bash
time DATABASE_URL="$PROD" $PY scripts/export_impact_artifact.py --out "$ART/chain" --label K3 --comparator impact_v4 \
  && time DATABASE_URL="$PROD" $PY scripts/export_impact_artifact.py --out "$ART/chain" --label K4 --manifest "$REL/candidate-manifest.json" \
  && same "$ART/chain/K4.json" "$ART/chain/K3.json" \
  || echo "STOP: exit $?"
```

Record the K3/K4 hash and its input fingerprint as **`PREP_CHAIN`**. It is **preparation-grade**: it proves the frozen
manifest reproduces the comparator on production *as it stood at F4*. It is **not** the hash later verifications
expect. Every `verify-build` / `verify-live` passes `--expect-comparison-sha256` for the export of **the database and
moment it verifies**: the rehearsal's own export (§G), and in the window the in-window **K4 = K5** hash, `CHAIN`
(process §H1.3). The activation cohort includes every match ingested after F4, so the preparation hash would fail
`verify-build` even with K4 = K5 exactly (final review, finding 2).

### F5. Reviews, against the restore

```bash
M="$REL/candidate-manifest.json"; B=<fill>; C=<fill>
DATABASE_URL="$REH" $PY -X utf8 scripts/release_candidate_review.py --manifest $M --ten --compare site --report-dir $REL \
  && ( for m in 3104 3133 "$A" "$B" "$C"; do DATABASE_URL="$REH" $PY -X utf8 scripts/release_candidate_review.py --manifest $M --match "$m" --compare site --report-dir $REL || exit 1; done ) \
  && DATABASE_URL="$REH" $PY -X utf8 scripts/release_candidate_review.py --manifest $M --match 3120 --trace 3120 --report-dir $REL \
  && DATABASE_URL="$REH" $PY -X utf8 scripts/compare_rc3_decomposition.py --manifest $M --matches "$COHORT" --report-dir $REL \
  && DATABASE_URL="$REH" $PY -X utf8 scripts/release_candidate_review.py --manifest $M --ten --extra 3104 --extra 3120 --extra 3133 \
    --extra "$A" --extra "$B" --extra "$C" --results $REL/review-results.json \
  || echo "STOP: exit $?"
```

The decomposition check's econ-model assumption holds for v4, which leaves the econ model unchanged.

**What the owner sees at G4** (settled by the final review, with evidence). The site review's **Before, Change and
Rank columns use the stored scores**, not the replay (`release_candidate_review.py` ~730/747), and so does the
ten-match summary (~1060). On the restore, the stored rows are rc3's, so the comparison shown is **rc3 → v4**, which is
the one that matters. `site_comparison` stays `[live_legacy, impact_v4]`: the legacy replay is only an extra
diagnostic column. And a third entry would break the renderer, which unpacks exactly two comparators (~1043).

**Gate G4:** the owner's side-by-side look at several friend-group matches (72% of matches reorder), and approval of
the reviews.

## Swap-tool flags for this release

Rc3's runbook used defaults that no longer exist. Every swap-tool command in this release passes:

```bash
S="--expect-database <db> --identity v4-runbook --previous-table impact_scores_v3 --rolled-back-table impact_scores_v4_rolled_back"
# verify-build / verify-live additionally:  --expect-scoring-version 4
```

Equivalence checker: `scripts/compare_v4_reference.py --reference DIR --out DIR --expect-scoring-version 3` (3 on
the branch, where there is no bump). It exits 1 on any difference.

## Commands for this release

Written from the rehearsal (process G5), in the order the window runs them. Every one of these ran on
`valo_v4_rehearsal` with only the database name changed. Run each block on its own and read its output; a block that
prints `STOP` stops the window.

### Session setup (every block assumes this)

```bash
cd "$(git rev-parse --show-toplevel)/webapp"
set -o pipefail
export PYTHONIOENCODING=utf-8
PY=.venv313/Scripts/python.exe
PGBIN="/c/Program Files/PostgreSQL/18/bin"
ART="$HOME/Documents/valo-backups/v4-release"; mkdir -p "$ART/window"
ARTW="$(cygpath -m "$ART")"    # for any path that lands inside a SQL string
REL=../docs/superpowers/impact-v4
PROD="$(grep '^DATABASE_URL' .env.remote | cut -d= -f2-)"   # never printed
S="--expect-database valowithfriendsdb --identity v4-runbook --previous-table impact_scores_v3 --rolled-back-table impact_scores_v4_rolled_back"
```

Use the `sha`/`same` helpers from the process doc section 0, which fail closed.

### H1.1 Preflight

Drain ingestion first and wait for the last refresh to exit. Then check for stranded matches, close the gate, and
take the window's own backup.

```bash
DATABASE_URL="$PROD" $PY -c "from app.db import SessionLocal; from app.scoring.impact import find_unscored_match_ids; print(find_unscored_match_ids(SessionLocal()))"
DATABASE_URL="$PROD" $PY scripts/install_release_write_gate.py --expect-database valowithfriendsdb \
    --state closed --release-id impact-rc3 --admin-id v4-runbook --note "impact-v4 activation window"
time "$PGBIN/pg_dump" --dbname="$PROD" --format=custom --no-owner --no-privileges --file="$ART/window/W0-valowithfriendsdb.dump"
"$PGBIN/pg_restore" --list "$ART/window/W0-valowithfriendsdb.dump" > "$ART/window/W0.list"
sha256sum "$ART/window/W0-valowithfriendsdb.dump" | tee "$ART/window/W0.sha256"
```

Closing the gate fixes the **activation cohort**. Record the printed server time: it is R3's restore point. The F0
dump is not this checkpoint. Then capture the cohort's id list and hash it, because both exports are pinned to it:

```bash
"$PGBIN/psql" -v ON_ERROR_STOP=1 -At -c "SELECT string_agg(id::text, ',' ORDER BY id) FROM matches" "$PROD" > "$ART/window/match-ids.txt"
sha256sum "$ART/window/match-ids.txt"
MATCHES="$(cat "$ART/window/match-ids.txt")"
```

### H1.2 Pre-swap capture

Measured 20 s for 769,120 rows. **`$ARTW`, not `$ART`** — the path sits inside a SQL string, where MSYS does not
convert it.

```bash
COLS="round_id, match_player_id, kill_impact, death_impact, impact, damage, econ_impact, time_impact, swing_impact, econ_kill, econ_death, clutch_kill, clutch_death, post_plant_kill, post_plant_death, traded_teammate, traded_by_teammate, trade_detail, kill_order_bonus, econ_component, econ_pickup, trade_credit, scoring_version"
"$PGBIN/psql" -v ON_ERROR_STOP=1 -c "\copy (SELECT $COLS FROM impact_scores ORDER BY round_id, match_player_id) TO '$ARTW/window/preswap-rc3-rows.csv' WITH (FORMAT csv, HEADER true)" "$PROD"
sha256sum "$ART/window/preswap-rc3-rows.csv" | tee "$ART/window/preswap-rc3-rows.sha256"
```

Keep that hash. It is what a rollback is verified against.

### H1.3 K4 then K5 — one export per job

**Each takes about 43 minutes and holds the whole corpus in memory: never run two in one job.** K5 needs
`--projection both`, which writes the `.load.csv` the swap builds from; the load projection comes from the same
in-memory rows and cannot be added afterwards without rescoring.

```bash
time DATABASE_URL="$PROD" $PY scripts/export_impact_artifact.py --out "$ART/window" --label K4 \
    --manifest "$REL/candidate-manifest.json" --matches "$MATCHES"
# a separate job:
time DATABASE_URL="$PROD" $PY scripts/export_impact_artifact.py --out "$ART/window" --label K5 \
    --active --projection both --matches "$MATCHES"
same "$ART/window/K5.json" "$ART/window/K4.json"     # EQUAL, or STOP
CHAIN=<the equal sha256>
```

`CHAIN` is **this window's** hash. It is neither F4's `PREP_CHAIN` nor the rehearsal's `2cd448e2...`: the activation
cohort includes every match ingested since F, so an older hash cannot match and must never be pasted in here.

### H1.4 Build, verify, capture the prewarm lists, swap

Measured: build 1 m 01 s, verify-build 22–23 min, swap 37–41 s.

```bash
DATABASE_URL="$PROD" $PY scripts/prewarm_player_cache_ids.py capture \
    --roster-out "$ART/window/roster-ids.txt" --recent-out "$ART/window/recent-ids.txt"
time DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py build $S --artifact "$ART/window/K5.load.csv"
time DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py verify-build $S \
    --artifact "$ART/window/K5.load.csv" --sidecar "$ART/window/K5.json" --comparison "$ART/window/K5.csv" \
    --approved "$REL/review-results.json" --manifest "$REL/candidate-manifest.json" \
    --expect-comparison-sha256 "$CHAIN" --expect-scoring-version 4
time DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py swap --yes $S; echo "swap exit $?"
DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py state $S
```

Exit 4 is a lock timeout that changed nothing: retry. Exit 3: read the reason. Anything else: run `state` first.
`verify-build` must print `clean` with `problems: []`, `scoring_versions: [4]`, and a `read_back_sha256` equal to the
load artifact's.

### H1.5–H1.6 Merge, deploy, restart, verify-live

Merge the activation PR, wait for Render's deploy, then **restart every process** — `_load_verified` caches the
manifest per process, so a long-running worker would carry on under the old one.

```bash
time DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py verify-live $S \
    --artifact "$ART/window/K5.load.csv" --sidecar "$ART/window/K5.json" --comparison "$ART/window/K5.csv" \
    --approved "$REL/review-results.json" --manifest "$REL/candidate-manifest.json" \
    --expect-comparison-sha256 "$CHAIN" --expect-scoring-version 4
```

Measured 23 m 01 s. The swap-to-deploy interval is **accepted and served through**, per the policy recorded above.

### H1.7 Cache barriers

```bash
"$PGBIN/psql" -v ON_ERROR_STOP=1 -c "DELETE FROM player_view_cache" "$PROD"
time DATABASE_URL="$PROD" $PY scripts/prewarm_player_cache_ids.py prewarm --ids-file "$ART/window/roster-ids.txt"
DATABASE_URL="$PROD" $PY scripts/verify_player_cache_coverage.py --ids-file "$ART/window/roster-ids.txt"
DATABASE_URL="$PROD" $PY scripts/verify_cache_matches_scores.py --ids-file "$ART/window/roster-ids.txt" --expect-database valowithfriendsdb
```

The DELETE will report **0 rows**: the swap clears the cache itself, inside its transaction. That is expected and not
a sign the command failed. Prewarm measured 2 m 23 s for 12 players; coverage must print every roster scope usable at
a cache version beginning **4** (the rehearsal's was `4003003004`); agreement measured 4.8 s.

### H1.8 Acceptance

Replay diffs (rc3 8.7), then `verify-live` once more with every argument, then the page checks. Then the database
tests on real data, **one file at a time** — the whole set in one process was stopped by the machine's low-memory
reaper at about ten minutes.

```bash
VALO_TEST_DATABASE_URL="$PROD" $PY -m pytest tests/test_impact_exante_swing.py -q -rs \
    --deselect tests/test_impact_exante_swing.py::test_wrapper_still_persists_and_commits
VALO_TEST_DATABASE_URL="$PROD" $PY -m pytest tests/test_impact_reconstruction.py -q -rs
VALO_TEST_DATABASE_URL="$PROD" $PY -m pytest tests/test_kill_order_leverage_gates.py -q -rs
VALO_TEST_DATABASE_URL="$PROD" $PY -m pytest tests/test_kill_order_stage_c0.py -q -rs   # about 55 minutes
```

Expect **17 passed, 1 skipped, 1 deselected**. The skip must be `test_impact_reconstruction`, naming version 4. A
skip from `test_builder_matches_stored_values` is a finding, not noise.

### Rollback, valid only while the gate is still closed

```bash
DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py rollback --yes $S
```

Measured 21–23 s. Verify it the hard way: re-run H1.2's `\copy` against the restored table and confirm its sha256
equals the pre-swap capture's — on the rehearsal the two were byte-identical. Rollback **stops being available the
moment a match is ingested**: the refusal is `matches were ingested after the swap`, exit 3, nothing changed. If
`impact_scores_v4_rolled_back` already exists from an earlier rollback, drop it first, or the tool refuses for that
reason instead and the message misleads.

## G. Rehearsal -- what ran and what it produced

On `valo_v4_rehearsal`, from the activation checkout `e075441` (`5f2180b` + the single activation commit).
Full durations in `$ART/rehearsal/durations.md`.

**G1. The restore serves.** F0's restore was touched by nothing but reviews. Re-read 2026-09-22: alembic `0010`,
3,649 matches / max id 3657, gate `closed` / `impact-rc3` / `v4-runbook` with `updated_at` still the restore moment,
769,120 `impact_scores` rows all at `scoring_version` 3. No re-restore needed.

**G2. The activation checkout, both checks.**

- Chain surface identical to the freeze `2e5140e`, **36 files compared**, over the `:/`-anchored pathspec.
  **v4 records no exception**: rc3's `write_gate.py` exception does not carry over and is not needed.
- Branch `impact-v4-activation` = the reviewed tip plus **exactly one commit** `e075441`.
  `git diff --name-only HEAD~1 HEAD` prints only `webapp/app/scoring/impact.py` and
  `webapp/app/scoring/impact_runtime.py`. From that checkout: `active_scoring_config().config_id` = `impact_v4`,
  `IMPACT_CALCULATION_VERSION` = 4, manifest LF-sha `2f33f137...` equal to the freeze, `verify_manifest` passing
  over its 14 pinned sources.

**G3a. The rehearsal chain, K4R = K5R.** Both pinned with `--matches` to the restore's 3,649 ids, whose list
hashes to `92077535...` -- **byte-identical to the id list pinned at F4**, so the rehearsal cohort is exactly the
one production was measured over.

| export | source | sha256 | cohort fingerprint | rows | wall clock |
|---|---|---|---|---:|---|
| K4R | frozen manifest | `2cd448e2...` | `768c86b3...` | 769,120 | 42 m 51 s |
| K5R | `--active`, `--projection both` | `2cd448e2...` | `768c86b3...` | 769,120 | 43 m 20 s |

**`CHAIN` for this rehearsal = `2cd448e2edb3d4616dfd3ce7abf0150a33a851ab126dc21245ef3e430c4424c2`**, which every
rehearsal `verify-build` / `verify-live` passes as `--expect-comparison-sha256`. It happens to equal F4's
`PREP_CHAIN` because the restore is production as it stood at F0 -- that is a confirmation, not a requirement, and
the window's own `CHAIN` will differ (it covers every match ingested since).

K5R's load projection: 769,120 rows, sha256 `32f3994c...`. It is the artifact the swap builds from.

> **`--projection both` is not optional.** Without it the export writes only the comparison projection, no
> `<label>.load.csv`, and `build` has nothing to load -- 43 minutes spent for an artifact the swap cannot use. The
> load projection is written from the same in-memory rows as the comparison, so it cannot be added afterwards
> without re-scoring.

> **Gate probe (c) needs `--release-id impact-v4`.** `verify_ingest_preflight` compares the gate's `release_id`
> against the manifest's `candidate_id`, and the restore's gate reads `impact-rc3`. rc3 §6.5 omits the flag
> because its gate already named its own release; copied verbatim here, probe (c) fails on a gate mismatch rather
> than proving anything.

> **The pre-swap capture needs a Windows path.** `psql` is a native Windows binary, and a path inside a
> `\copy ... TO '...'` SQL string is **not** converted by MSYS the way an argv path is. `$ART` expands to
> `/c/Users/...` and psql answers `No such file or directory` in half a second. Use `ARTW="$(cygpath -m "$ART")"`
> for anything that lands inside SQL. The same split bit the K4R/K5R comparison, where `json.load` in a `python -c`
> string literal could not open `/c/...` either -- argv is converted, string literals are not.


**G3b. Forward path on the restore.** Every step from the activation checkout, gate closed throughout.

| step | result |
|---|---|
| pre-swap capture | 769,120 rc3 rows, canonical column order, sha256 `37c81af8...`, 20 s |
| `build` | `impact_scores_new`, 769,120 rows, oid 74833, 1 m 02 s |
| `verify-build` | **clean**, `problems: []`, `scoring_versions: [4]`, read-back sha `32f3994c...` equal to the load artifact, comparison `2cd448e2...` = CHAIN, 3,460 approved rows checked against `review-results.json` |
| `swap` | committed, exit 0, 41.33 s, retained oid 69970 -> `impact_scores_v3`, built oid 74833 -> `impact_scores`, 10 constraints/indexes renamed |

**The swap's 41 s is not 41 s of stalled site.** The transaction is deliberately ordered so that the expensive
part -- `_require_clean_verification` re-digesting seven tables, which rc3 measured at about 31 s for six -- runs
under `SHARE` locks on the source tables. `SHARE` stops writers, not readers, and ingestion is already drained and
gated by then. Only afterwards does the transaction take `ACCESS EXCLUSIVE` on the live table, and that window
covers the renames alone: ten `ALTER ... RENAME` statements. The design comment in `swap_impact_scores.py` says
exactly why (31 s inside the exclusive lock would be six times the 5 s stall budget).

What this rehearsal therefore does **not** establish is the size of that stall in wall-clock terms, because
concurrency measurement is waived for v4 and nothing was requesting pages while the swap ran. The reasoning above
is from the code and rc3's measurement, not from a v4 observation. Anyone who wants the number must measure it.


> **Probe (a) is not rc3's probe any more, and the difference is worth stating.** rc3 §6.5 (a) reads "an actual
> pre-0010 checkout updating an existing row". At v4 that description is false: `origin/main` (`6f45476`) carries
> migration `0010`, carries `write_gate.py`, and its `ACTIVE_MANIFEST` names rc3's manifest. It is not a stale
> checkout at all -- it is **the code currently deployed in production**. The probe still belongs here, and arguably
> proves something more useful for v4: the deployed rc3 code, run against a v4-swapped database, cannot write to it.
> It is refused because a raw write claims no release identity and the gate's trigger requires one -- not because
> of anything to do with `0010`. The runbook says "the deployed production checkout", not "a pre-0010 checkout".


**G3c. Caches, probes and rollback.**

| step | result |
|---|---|
| `DELETE FROM player_view_cache` | **0 rows** -- the swap's own `_clear_player_cache` had already emptied it. The explicit DELETE is belt-and-braces, not the thing doing the work |
| prewarm (12 roster players) | 12 of 12, 2 m 23 s, slowest player 20.9 s |
| coverage | expected cache version **`4003003004`**, 24 of 24 player-scopes usable. The leading 4 is `IMPACT_CALCULATION_VERSION` in the composite |
| agreement (`verify_cache_matches_scores.py`) | cache agrees with scores, 24 scopes / 12 players, tolerance 1e-9, 4.8 s |
| probe (a) deployed production checkout (`6f45476`, Python 3.11) | **refused as expected, scores unchanged** |
| probe (b) activation checkout, no identity | **refused as expected, match count unchanged** |
| probe (c) gate open for `impact-v4`, preflighted | **allowed as expected**, write identity `impact-v4`; gate closed again afterwards |
| `rollback` | committed, exit 0, 21.21 s; oids reversed exactly (69970 back to `impact_scores`, 74833 to `impact_scores_v4_rolled_back`); the tool closes the gate itself |
| rollback verification | the restored table re-exported canonically hashes to **`37c81af8...`, byte-identical to the pre-swap capture** |

> **Run the probe scripts through stdin, not as file arguments.** `python probes/probe_a.py` puts the probe's own
> directory on `sys.path[0]`, so `from app.db import ...` raises `ModuleNotFoundError` from inside the checkout it
> is supposed to be testing. `python - < probes/probe_a.py` reproduces rc3's heredoc behaviour (cwd on the path)
> without a heredoc, which this repo mangles.


> **An export is bound to the database it was read from.** `verify-build` on the second restore refused with
> `the export read database valo_v4_rehearsal, but this is valo_v4_rehearsal2`, although everything else was clean
> and the seven row digests were byte-identical between the two restores. The sidecar records its source database
> and the check is on provenance, not content. This costs the window nothing -- there the export and `verify-build`
> are both on production -- but it means **a second restore cannot reuse the first one's artifact**: it needs its
> own export, about 43 minutes. Plan for that, or run the late tests on the first restore once its rollback test
> has been scored.


**G3d. Forward again, interrupted swap, and the consumed rollback precondition.**

| step | result |
|---|---|
| forward again (`build` / `verify-build` / `swap`) | 1 m 03 s / 22 m 38 s clean / 41.11 s -- the second swap reproduces the first to within 0.2 s |
| second restore `valo_v4_rehearsal2` | restored in 4 m 24 s, gate closed, preflight holds |
| `verify-build` on the second restore | **refused on provenance** (see the note above). Left unswapped; it is the untouched spare |
| third `build` / `verify-build` on rehearsal1 | 1 m 01 s / 23 m 30 s clean, oid 79941 |
| **interrupted swap** (SIGKILL at 12 s, inside the digest phase, locks held) | exit 137 and **nothing changed**: no `impact_scores_v3`, live table still version 3 at 769,120 rows, newest log entry still `verify-build clean` (id 16), both oids unchanged |
| third `swap` | committed, 36.56 s |
| one gated match committed after the swap | match 3659 under identity `impact-v4`; `max(matches.id)` 3657 -> 3659 |
| `rollback` afterwards | **REFUSED, exit 3, nothing changed**, specifically: `matches were ingested after the swap (max id 3657 then, 3659 now)` |

The refusal was made to have exactly one possible cause: `impact_scores_v4_rolled_back` was dropped first, so the
tool could not refuse merely because its destination name was occupied.

**Three swaps and two rollbacks, all clean: 41.33 s, 41.11 s, 36.56 s and 21.21 s, 22.97 s.** The first rollback was
verified the hard way -- the restored table re-exported canonically was byte-identical to the pre-swap capture.


**G3e. The declared database tests, on real data, after the swap.** Run from the activation checkout against
`valo_v4_rehearsal` with the committing-wrapper test deselected. **Run one file at a time** -- the whole set in one
process was stopped by the machine's low-memory reaper at about 10 minutes.

| file | result | time |
|---|---|---|
| `test_impact_exante_swing.py` | **5 passed**, 1 deselected | 10 s |
| `test_impact_reconstruction.py` | 4 passed, **1 skipped (expected)** | 7 s |
| `test_kill_order_leverage_gates.py` | 5 passed | 43 s |
| `test_kill_order_stage_c0.py` | 3 passed | **54 m 43 s** |

**17 passed, 1 skipped, 1 deselected.** Both version-sensitive expectations landed the right way round, which is
the whole reason the process runs this suite after the swap rather than before:

- `test_builder_matches_stored_values` **passed** -- the table holds version 4 and this checkout computes version 4,
  so it is an end-to-end check on the loaded rows that never touches `verify-build`'s artifact comparison. A skip
  here would have been a finding.
- `test_impact_reconstruction` **skipped**, naming what it found: `no rows below scoring_version 3: the table holds
  [4]`. That identity is the legacy combination step and v4 cannot satisfy it by construction, exactly as rc3
  could not.

Two corrections to rc3's numbers, for whoever reads this next:

- rc3 budgets **46 minutes** for the set and says stage C0 and the leverage gates are "nearly all of it". At v4 it
  is **56 minutes**, and it is stage C0 **alone** -- the leverage gates take 43 seconds. Budget for one long file,
  not two.
- rc3 expects **13 passed, 1 skipped** over 14 collected. v4 collects **19** (18 after the deselect) because tests
  have been added since. The expectation to carry into the window is 17 passed, 1 skipped, 1 deselected.


**Where the rehearsal databases stand now**, so nobody has to rediscover it:

- `valo_v4_rehearsal` -- swapped forward to v4, with `impact_scores_v3` retained and match **3659** committed after
  the swap. Its rollback precondition is deliberately consumed; the refusal above is the evidence. Nothing further
  is owed by it.
- `valo_v4_rehearsal2` -- restored, gated, preflighted, **never swapped** (its `verify-build` refused on
  provenance). It is the clean spare. It would need its own ~43-minute export before it could be swapped.
- Both can be dropped once G5 is settled. They cost about 800 MB of the instance's 15 GB.

## H0. Before the window

| item | state |
|---|---|
| storage for live + staged + retained tables, indexes and WAL headroom | **confirmed 2026-09-22 on the Render dashboard: 15 GB, 8.3% used** (~1.25 GB). The swap needs one extra copy of `impact_scores` (101 MB) plus its indexes while both tables exist |
| pre-release production `main` SHA (the rollback deployment) | **`6f45476df7fcf55574a2506f2850fb85b587643b`** — `Merge pull request #70 from conorlum/trackergg-paginated-history` |
| retained table name free | **yes**: production holds only `impact_scores` and `impact_scores_v1`; both `impact_scores_v3` and `impact_scores_v4_rolled_back` are free |
| a separate checkout of the release-tools commit for a database rollback | **`$ART/release-tools`**, a detached worktree at `bcb83b9`. It carries `swap_impact_scores.py` with this release's flags, and its `ACTIVE_MANIFEST` is still rc3's — deliberately, so reverting the deployment and rolling the database back do not disagree |
| recovery gate: point-in-time restore covers the window, with its retention recorded | **7 days**, carried from rc3 §7.1 ("restore to any timestamp in the past 7 days", cleared 2026-09-17) and confirmed unchanged by the owner on 2026-09-22. **Not re-read on the Render dashboard today** — if that matters to whoever runs the window, re-read it. Recovery restores into a NEW instance, so R3 also repoints the web service and `.env.remote`. Past 7 days, recovery rests on the W0 dump and `impact_scores_v1` |
| the outstanding rehearsal item | **WAIVED, owner decision 2026-09-22.** The real-match ingest is not rehearsed. What that leaves unproven: that ingestion through the tracker.gg adapter commits a match **with scores at version 4**. The neighbouring halves are proven — probe (c) showed a preflighted write is allowed and claims identity `impact-v4`, and the post-ingest rollback refusal is exact — but the adapter's own commit-then-score path is not exercised under v4. Its first real test will be the first match ingested after G7 reopens the gate, on production, with no rehearsal behind it. H1 and I.3 already require that match to carry `scoring_version` 4 and equal its replay, so treat that check as load-bearing rather than routine |

**The activation PR is a real merge, not a fast-forward.** `origin/main` moved to `6f45476` (PR #70) after this branch
was cut, so `impact-v4-activation` is 6 commits behind it. Nothing in the chain surface differs — PR #70 touched only
the tracker.gg adapter, and every scoring file is identical — but H1.5 has to reconcile those six commits, and the
chain-surface check must be re-run on whatever tip the merge produces before the window relies on it.

**R3, the last resort**, stays as written in the plan: restore into a new instance, then repoint the web service and
`.env.remote`.

## H1. The activation window -- what actually ran

Opened 2026-09-23 on the owner's authorisation of 2026-09-22: **everything through the swap, then a PR for the
owner to merge.** The merge, the deploy, `verify-live` and the cache rebuild are deliberately NOT in this scope.

| step | result |
|---|---|
| ingestion drained | no python processes, no project scheduled tasks. Ingestion here is manual |
| stranded matches | **NONE** |
| gate closed | `open`/`impact-rc3`/`rc3-runbook` -> `closed`/`impact-rc3`/`v4-runbook`, note "impact-v4 activation window" |
| preflight | alembic `0010`, **3,649 matches, max id 3657**, gate closed. Identical to F0, so no match arrived between the freeze and the window |
| **R3 restore point (server time)** | **`2026-09-23 04:16:10.790079+00`** |
| W0 backup | 56 s, 49,712,365 bytes, sha256 `fd6b7f6cdfa4b28f72c9679794b40310452781f8603a7257366ed52f772a2d8a`; `pg_restore --list` reads 171 entries |
| activation cohort id list | 3,649 ids, sha256 `92077535d7af2ccbd529b34fd722ee22c9daafa9c330208dac184e189dea8362` -- **the same list pinned at F4 and in the rehearsal** |
| pre-swap capture | 769,120 rc3 rows, 23 s, sha256 `37c81af806c58ec559deb4c9c8b47fafc4e1eac6c7061e5dbde2a0cb44865a65` -- **byte-identical to the rehearsal's**, so production's live rows and the restore's were the same rows |
| K4 (frozen manifest) | 45.0 min, sha256 `2cd448e2...`, cohort fingerprint `768c86b3...` |
| K5 (`--active --projection both`) | 42.3 min, same sha256 and fingerprint; load projection `32f3994c...`, 769,120 rows |
| **`CHAIN`** | **`2cd448e2edb3d4616dfd3ce7abf0150a33a851ab126dc21245ef3e430c4424c2`** -- equal to F4's `PREP_CHAIN` and the rehearsal's, because **no match was ingested between the freeze and the window**, so the activation cohort is F4's cohort exactly. The runbook's warning that it would differ is right in general and simply did not bite here |
| prewarm lists | 12 roster players, 2,238 recently cached beyond them |
| `build` | 1.07 min, `impact_scores_new` oid 80081, 769,120 rows |
| `verify-build` | **clean**, 22.5 min. `problems: []`, `scoring_versions: [4]`, `read_back_sha256` = `32f3994c...` = the load artifact, `comparison_sha256` = CHAIN, manifest `2f33f137...`, 3,460 approved rows checked |
| **`swap`** | **committed 2026-09-23 07:12:54 UTC, exit 0, 36.98 s** -- inside the rehearsal range of 36.56-41.33 s. Retained oid 65265 -> `impact_scores_v3`; built oid 80081 -> `impact_scores` |
| state after the swap | `impact_scores` 769,120 rows **all version 4**; `impact_scores_v3` 769,120 rows at version 3; `impact_scores_v1` still present; cache cleared to 0; gate closed; release log id 8 `swapped` under `v4-runbook` |

> **The exports were reaped twice as Claude Code background tasks and had to be run detached.** The first in-window
> K4 died after 22 minutes of scoring but before writing its sidecar -- `K4.csv` on disk, no `K4.json`, unusable --
> which is the same failure the ledger records at F4. The fix that worked: run each export as a **detached Windows
> process** (`Start-Process ... -WindowStyle Hidden -RedirectStandardOutput`), which the reaper does not supervise,
> and have the session merely wait on its output file. Do that from the start next time; it costs nothing and it
> removes a 45-minute retry risk from the critical path.



> **The process doc names a function that does not exist.** H1.1 says to check for stranded matches with
> `find_unscored_match_ids` imported from `app.scoring.ingest_preflight`. It lives in **`app.scoring.impact`**.
> Corrected in the commands above; worth fixing in the process doc at close-out.

## Durations measured in rehearsal

| step | duration | note |
|---|---|---|
| K4R export (frozen manifest, 3,649 matches) | 42 m 51 s | 22.3 min of scoring, the rest per-match fingerprinting |
| K5R export (activation checkout `--active --projection both`) | 43 m 20 s | 22.0 min scoring; load projection written from the same rows |
| `build` (COPY 769,120 rows into `impact_scores_new`) | 1 m 02 s | oid 74833 |
| `verify-build` (all arguments, `--expect-scoring-version 4`) | 22 m 35 s | 20.25 min of it is input fingerprinting |
| pre-swap capture (769,120 rc3 rows, canonical order) | 20 s | sha256 `37c81af8...` |
| `swap` | 41.33 s | ~31 s of it is in-transaction re-digest under SHARE; ACCESS EXCLUSIVE covers the renames only |
| `verify-live` (all arguments, `--expect-scoring-version 4`) | 23 m 01 s | clean |
| cache DELETE + prewarm 12 players | 2 m 23 s | DELETE was already 0; the swap clears the cache itself |
| cache coverage + agreement | 5 s | 24 of 24 scopes, version 4003003004 |
| gate probes (a)(b)(c) | under 1 min total | all three as expected |
| `rollback` | 21.21 s | restored rows byte-identical to the pre-swap capture |
| forward again: `build` | 1 m 03 s | oid 74965 |
| forward again: `verify-build` | 22 m 38 s | clean |
| forward again: `swap` | 41.11 s | reproduces the first swap's 41.33 s |
| second restore (`valo_v4_rehearsal2`, from B0) | 4 m 24 s | gate closed, preflight holds: alembic 0010, 3,649 matches, max id 3657 |
| third `build` / `verify-build` (rehearsal1) | 1 m 01 s / 23 m 30 s | for the interrupted-swap test |
| interrupted swap (SIGKILL at 12 s) | 12 s | nothing changed |
| third `swap` | 36.56 s | |
| real-data suite, `test_impact_exante_swing.py` | 10 s | 5 passed, 1 deselected |
| real-data suite, `test_impact_reconstruction.py` | 7 s | 4 passed, 1 skipped (expected) |
| real-data suite, `test_kill_order_leverage_gates.py` | 43 s | 5 passed |
| real-data suite, `test_kill_order_stage_c0.py` | **54 m 43 s** | 3 passed -- this file is effectively the whole suite budget |

Full record, including the machine and checkout they were measured on: `$ART/rehearsal/durations.md`.

## Waivers and policies for this release

### Concurrency measurement -- WAIVED, owner decision 2026-09-22

Process G3 asks for page latencies against the served restore, across a swap and a rollback (rc3 section 6.6 and
its 2xx floor). The owner declined to measure it for v4. **This waiver is v4 alone**: it does not inherit rc3's
approval, and it does not carry to any later release, which must ask again.

What it costs, stated rather than argued away: nothing in this release measures how pages behave while the swap
runs. The swap is a table rename inside one transaction and rc3 ran the same shape without incident, but that is
rc3's evidence, not v4's. The exposure decision below was taken knowing this was not measured.

### Swap-to-deploy exposure -- ACCEPTED, owner decision 2026-09-22

Between the swap and the deploy, `impact_scores` holds version 4 rows while the deployed code still computes rc3.
The owner accepts that interval and serves through it, as rc3 did (rc3 section 8.4.0). No maintenance mode: G3
rehearses no maintenance path and H1 turns none on.

## Known state on the branch

- **15 tests fail on the branch, all from one cause.** Fixtures resolve the repository's rc3 manifest, whose source
  digests no longer match this code. The same 15 fail on `origin/main` under Python 3.11. They go green at
  activation. Owner decision, 2026-09-21: **leave them red and documented**.
- **2 tests fail as they do on `origin/main`**: `test_default_persistence_path_is_unchanged_legacy` and
  `test_runtime_default_is_the_live_legacy_formula`.
- **The plan contradicts the code in two places.** The code and the ledger are right:
  - §2.6's statement that `webapp/app` is identical at `f96aee9` and on `origin/main` stopped being literally true
    at PR #70. Only the tracker.gg adapter differs, and every scoring file is identical.
  - The plan's swap commands assume rc3's defaults, which no longer exist.

## Decisions the owner made

| date | decision |
|---|---|
| 2026-09-21 | Ship `N` and `N+A` per declaration 12's stop rule. `N+A` ships on concept; it is untestable |
| 2026-09-21 | Leave the 15 active-manifest test failures red and documented until activation |
| 2026-09-21 | Leave rc3's runbook commands as rc3's record; this runbook carries the new flags |
| 2026-09-22 | **Gate G4 approved**: the rc3 -> v4 site comparison over the fixed ten and both review rounds. Phase G may start |
| 2026-09-22 | **Concurrency is not measured for v4** -- a new, dated waiver for this release only. rc3's waiver does not carry over, and this one does not carry to a later release |
| 2026-09-22 | **Swap-to-deploy exposure accepted**, as rc3 did (rc3 §8.4.0). No maintenance mode; the site serves through the interval |
| 2026-09-22 | **Render disk confirmed on the dashboard: 15 GB, 8.3% used** (~1.25 GB). The plan's ~15 GB from memory was right; H0's storage gate can cite the dashboard |
| 2026-09-22 | **Gate G5 accepted**, conditional: the rehearsal and the window commands are accepted as they stand, with the real-match ingest left as an explicit open item to close before H0 |
| 2026-09-22 | **Real-match ingest rehearsal waived.** Phase G ships without it; the first post-G7 ingest is the first test of that path |
