# Impact v4 — release runbook

Status (2026-09-21): **phases A–E done on the branch; F (freeze and review) is next and needs production reads.**
Nothing is merged, deployed or frozen. Production runs rc3 (`IMPACT_CALCULATION_VERSION` 3, `ACTIVE_MANIFEST` rc3).

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
| F freeze & review | **next** | commands below; gate G3 (production reads + the restore), then G4 after the reviews |
| G rehearse | not started | |
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
export PYTHONIOENCODING=utf-8
PY=.venv313/Scripts/python.exe
PGBIN="/c/Program Files/PostgreSQL/18/bin"
ART="$HOME/Documents/valo-backups/v4-release"; mkdir -p "$ART/chain" "$ART/rehearsal"
REL=../docs/superpowers/impact-v4
PROD="$(grep '^DATABASE_URL' .env.remote | cut -d= -f2-)"
case "$PROD" in */valowithfriendsdb) REH="${PROD%/valowithfriendsdb}/valo_v4_rehearsal" ;; *) echo "STOP: .env.remote is not the production URL shape" ;; esac
sha() { "$PY" -c "import json, sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['artifact']['sha256'])" "$1"; }
same() { a="$(sha "$1")"; b="$(sha "$2")"; if [ "$a" = "$b" ]; then echo "EQUAL $a"; else echo "DIFFERENT: $1 $a vs $2 $b"; return 1; fi; }
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
| server time | `<fill>` |
| alembic head | `<fill>` — expected `0010` |
| matches / max match id | `<fill>` |
| gate state / release id / admin id | `<fill>` — expected `open` / `impact-rc3` / `rc3-runbook` |
| database size / `impact_scores` size | `<fill>` (storage, process §H0) |

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
| (a) post-decided assist removed | `<fill>` | `review-cohort.sql` (a), confirmed by the scorer |
| (b) kill after a defuse | `<fill>` | (b) |
| (c) Time Win, kill after 100 s | `<fill>` | (c) |

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

Record the K3/K4 hash and the input fingerprint. That hash is `$CHAIN` for every later `--expect-comparison-sha256`.

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

The decomposition check's econ-model assumption holds for v4, which leaves the econ model unchanged. **Read before
the owner's look**: the "site" review's replayed left column is `live_legacy`, because `build_manifest` fixes
`site_comparison = [live_legacy, release]`. The before-state that matters for v4 is **rc3**, which the tool shows as
*what production stores* — the restore's stored rows, which are rc3's. Compare stored against v4, not the legacy
replay against v4. Whether to add `impact_rc3` to `site_comparison` before freezing is an open owner question.

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

To be written from the rehearsal (process §G5), in the order the window runs them.

## Durations measured in rehearsal

None yet.

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
