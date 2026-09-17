# Impact rc3 — release runbook

Status (2026-09-17): **Stages 1 to 5 done. rc3 is frozen at `82d8e6b`, K4 closed the chain through the frozen
manifest, the reviews pass, and `SUMMARY.md` is written. NOT active, nothing in production changed** (alembic 0007,
no gate; the rehearsal database is restored, migrated and gated, and recovery is confirmed for 7 days). Next: the
owner approves `SUMMARY.md`, then section 6, the rehearsal.

rc3 is the locked Impact formula: A 1 (damage) / B 2.5 (leverage) / C 2.5 (econ) / D 100 (assists), trade credit on at
`trade_credit_scale` 1.0, econ model `buy_disruption_v2_30_80_bonus_denial`, realized swing, timing candidates off. Its
comparator is `impact_rc3` (`app/scoring/impact_manifest.py`) and it activates at `IMPACT_CALCULATION_VERSION = 3`.

- Plan (authoritative): `../plans/2026-09-16-rc3-ship-plan-v2.md`. Stage and step numbers below are the plan's.
- Declarations and results: `../2026-09-07-predeclared-values.md` (append-only).
- External reviews: `../plans/2026-09-16-rc3-plan-astra-review.md`, `../plans/2026-09-16-rc3-plan-astra-review-2.md`.

Every command below was written against the tools as committed. **A step that has not been rehearsed on
`valo_rc3_rehearsal` (Stage 6) is not run against production.**

---

## 0. Rules for every session

- **Git Bash, from `webapp/`.** Commands are bash. Run a block, read its output, then go on. A block ends in
  `|| echo "STOP: exit $?"`: if that prints, **stop**. Nothing here is retried blind.
- **Python 3.13 (`.venv313`) for everything that scores, freezes, verifies or ingests** (D3).
- **Never print a database URL.** Pass it inline as `DATABASE_URL="$PROD"` or `"$REH"`. `.env.remote`, `.env.test` and
  `.env.rehearsal` are gitignored; the repository is public.
- **Every step asserts its database by name first** (`--expect-database`, or `release_preflight.py`). The production and
  rehearsal URLs differ only in that name.
- **Artifacts, dumps and logs live outside the repository** (`$ART`).
- **Any database test that writes runs against `valo_rc3_test` only.** The helper refuses `valo_rc3_rehearsal` to a
  test declaring `empties_tables=True` or `writes=True`, because that database is the production restore every
  measurement is taken against, and the scorer's wrapper updates rows in place -- a stray rescore there changes no
  row count and leaves nothing to notice. Read-only tests **may** use it, and section 6 is where they do.
- **Keep the machine awake for the long steps.** An export holds one snapshot for about 30 minutes, and
  `verify-build` fingerprints every match for about 15; if the machine sleeps, the connection dies and the step fails
  with "server closed the connection unexpectedly". That happened to K4 on 2026-09-17 at match 2067. Nothing is
  written by an export or a verification, so the answer is always to run it again -- but do not start one and walk
  away from a machine that sleeps.

### Session setup

```bash
cd "$(git rev-parse --show-toplevel)/webapp"   # run this from anywhere inside the checkout
export PYTHONIOENCODING=utf-8
PY=.venv313/Scripts/python.exe
PGBIN="/c/Program Files/PostgreSQL/18/bin"
ART="$HOME/Documents/valo-backups/rc3-release"      # artifacts, dumps and logs, outside the repository
A2="$HOME/Documents/valo-backups/rc3-artifacts-A2"   # K1', K2' and OFF(A')
RC3=../docs/superpowers/impact-rc3
PROD="$(grep '^DATABASE_URL' .env.remote | cut -d= -f2-)"
case "$PROD" in */valowithfriendsdb) REH="${PROD%/valowithfriendsdb}/valo_rc3_rehearsal" ;; *) echo "STOP: .env.remote is not the production URL shape" ;; esac
TEST="$(grep '^DATABASE_URL' .env.test | cut -d= -f2-)"
mkdir -p "$ART/chain" "$ART/rehearsal" "$ART/activation"

# The chain's comparison hash: K1' (3.11, commit b501ae2), which K2' (3.13) equals. K3 must equal it too (section 2).
CHAIN=7e5ff2789ed1ea3a61425e1a6138576bc250cddc5de4e63cb3740a6d9f42f1bb

sha() { "$PY" -c "import json, sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['artifact']['sha256'])" "$1"; }
same() { a="$(sha "$1")"; b="$(sha "$2")"; if [ "$a" = "$b" ]; then echo "EQUAL $a"; else echo "DIFFERENT: $1 $a vs $2 $b"; return 1; fi; }
```

### Expected values

| | before Stage 7 | after 7.3 | after 7.4 |
|---|---|---|---|
| database | `valowithfriendsdb` | same | same |
| `alembic_version` | 0007 | **0010** | 0010 |
| matches / max match id | 3,125 / 3133 | same | same |
| `round_player_stats` rows (= rc3 score rows) | 659,500 | same | same |
| write gate | absent | absent | **closed** |

Chain hashes (comparison projection, `artifact.sha256` in each sidecar):

| link | Python | code | configuration | sha256 |
|---|---|---|---|---|
| K1' | 3.11 | b501ae2 (fsum) | explicit locked weights | `7e5ff2789ed1ea3a61425e1a6138576bc250cddc5de4e63cb3740a6d9f42f1bb` |
| K2' | 3.13 | same | same | **equal to K1'** (the declared prediction held) |
| OFF(A') | 3.13 | same | credit off | `648df0403a0d48411dfd42c5b7697da1d6952de416e3a1a36bc97fc628460eac` |
| K3, OFF(B) | 3.13 | commit B (5c369f8) | `impact_rc3` comparator | **equal to K1' and OFF(A')** |
| K4 | 3.13 | commit C (freeze) | the manifest, by digest | **equal to K1'** (closed 2026-09-17) |
| K5 | 3.13 | activation commit | the active manifest | must equal K1'; **K5 is what gets loaded** |

K1' input fingerprint over all 3,125 matches: `2c31fbbd9b1507f32631d304dec86885f5b876440a3e31140f4c1769545bdc5f`.

---

## 2. Stage 3 (done 2026-09-17): K3, OFF(B) and the full suite

Kept as the commands that produced the chain. Commit B is 5c369f8, K3 equals K1' and OFF(B) equals OFF(A'), and the
suite at commit B was 1,248 passed / 48 skipped (3.11, offline) and 1,283 passed / 13 skipped (3.13, with the
database tests). From a clean checkout:

```bash
git status --porcelain --untracked-files=all -- . | head -5    # must print nothing
DATABASE_URL="$PROD" $PY scripts/export_impact_artifact.py --out "$ART/chain" --label K3-on --comparator impact_rc3 \
  && DATABASE_URL="$PROD" $PY scripts/export_impact_artifact.py --out "$ART/chain" --label OFFB --comparator impact_rc3 --credit off \
  && same "$ART/chain/K3-on.json" "$A2/K1p-on.json" \
  && same "$ART/chain/OFFB.json" "$A2/KAp-off.json" \
  || echo "STOP: exit $?"
```

A DIFFERENT result is not explained away: it is eliminated, or the chain is re-declared in the ledger.

The full suite, offline on both interpreters and then the database tests on the scratch database:

```bash
env -u DATABASE_URL -u VALO_TEST_DATABASE_URL .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
env -u DATABASE_URL -u VALO_TEST_DATABASE_URL $PY -m pytest -q -p no:cacheprovider
env -u DATABASE_URL VALO_TEST_DATABASE_URL="$TEST" $PY -m pytest -q -p no:cacheprovider
```

## 3. Stage 4: the RESULT entry

```bash
DATABASE_URL="$PROD" $PY scripts/report_rc3_measurements.py --on "$ART/chain/K3-on.csv" --off "$ART/chain/OFFB.csv" \
    --json "$ART/chain/rc3-measurements.json" \
  || echo "STOP: exit $?"
```

It reads production only for source mappings and the stored v1 impact, in one read-only snapshot.

Append the RESULT entry to the ledger from that JSON (event-level credit measures cite the 2026-09-14 figures scaled
by 2.5/3, D12), and commit it on its own.

## 4. The rehearsal database (plan 3.0, needed from Stage 5 on)

Reviews need production's data at schema 0010, which only a restored copy has. This is also backup B0 and the rehearsed
restore the recovery gate may rely on (6.1).

```bash
DATABASE_URL="$PROD" $PY scripts/release_preflight.py --expect-database valowithfriendsdb --expect-alembic 0007 \
    --expect-matches 3125 --expect-max-match-id 3133 --expect-gate absent \
  && time "$PGBIN/pg_dump" --dbname="$PROD" --format=custom --no-owner --no-privileges --file="$ART/B0-valowithfriendsdb.dump" \
  && "$PGBIN/pg_restore" --list "$ART/B0-valowithfriendsdb.dump" > "$ART/B0.list" \
  && "$PGBIN/psql" -v ON_ERROR_STOP=1 -c "CREATE DATABASE valo_rc3_rehearsal" "$PROD" \
  && time "$PGBIN/pg_restore" --dbname="$REH" --no-owner --no-privileges --no-comments --exit-on-error "$ART/B0-valowithfriendsdb.dump" \
  && DATABASE_URL="$REH" $PY scripts/release_preflight.py --expect-database valo_rc3_rehearsal --expect-alembic 0007 \
    --expect-matches 3125 --expect-max-match-id 3133 --expect-gate absent \
  || echo "STOP: exit $?"
```

Measured on 2026-09-17: the dump is 31 MB and takes 33 s, the restore 3 m 13 s, migrations 0007 to 0010 **3.5 s**, and
the gate install under a second. The migration timing is the one Stage 7.3 repeats against production.

`CREATE DATABASE` is the only statement before Stage 7 that uses the production URL and is not a read. It creates a
second database on the instance; it does not touch `valowithfriendsdb`. To start the rehearsal over from a fresh
restore, `DROP DATABASE valo_rc3_rehearsal` first, typed by hand, reading the name twice.

Migrate the rehearsal (timed, exit-gated, then the revision asserted) and install its gate closed (6.2):

```bash
time DATABASE_URL="$REH" $PY -m alembic upgrade head \
  && DATABASE_URL="$REH" $PY scripts/install_release_write_gate.py --expect-database valo_rc3_rehearsal \
    --state closed --release-id impact-rc3 --admin-id rc3-runbook --note "rehearsal" \
  && DATABASE_URL="$REH" $PY scripts/release_preflight.py --expect-database valo_rc3_rehearsal --expect-alembic 0010 \
    --expect-matches 3125 --expect-max-match-id 3133 --expect-gate closed \
  || echo "STOP: exit $?"
```

The normalized comparison (A14) is the rehearsal forward path's export in section 6: same comparison hash and same
input fingerprint as K1', with the database name asserted separately.

## 5. Stage 5: freeze and review

**5.1 Freeze** at commit C from a clean tree, reading production (read-only, one snapshot):

```bash
DATABASE_URL="$PROD" $PY scripts/freeze_impact_candidate.py --candidate-id impact-rc3 --release-comparator impact_rc3 \
    --activation-version 3 --matches 3104,3129,3130,3131,3113,3118,3121,3114,3115,3116,3117,3120,3133 \
    --out "$RC3/candidate-manifest.json" \
  || echo "STOP: exit $? (3 = the tree is not exactly a commit)"
```

Commit the manifest alone. Never edit it: review results are tied to its hash.

**5.2 K4** through the manifest:

```bash
DATABASE_URL="$PROD" $PY scripts/export_impact_artifact.py --out "$ART/chain" --label K4 --manifest "$RC3/candidate-manifest.json" \
  && same "$ART/chain/K4.json" "$ART/chain/K3-on.json" || echo "STOP: exit $?"
```

**5.3 Reviews** against the rehearsal database (the review tool checks the manifest's fingerprints against it):

```bash
M="$RC3/candidate-manifest.json"
DATABASE_URL="$REH" $PY -X utf8 scripts/release_candidate_review.py --manifest $M --match 3104 --compare site --report-dir $RC3 \
  && DATABASE_URL="$REH" $PY -X utf8 scripts/release_candidate_review.py --manifest $M --ten --compare site --report-dir $RC3 \
  && DATABASE_URL="$REH" $PY -X utf8 scripts/release_candidate_review.py --manifest $M --match 3120 --trace 3120 --report-dir $RC3 \
  && DATABASE_URL="$REH" $PY -X utf8 scripts/release_candidate_review.py --manifest $M --match 3133 --compare site --report-dir $RC3 \
  && DATABASE_URL="$REH" $PY -X utf8 scripts/compare_rc3_decomposition.py --manifest $M --report-dir $RC3 \
  && DATABASE_URL="$REH" $PY -X utf8 scripts/release_candidate_review.py --manifest $M --ten --extra 3104 --extra 3120 --extra 3133 \
    --results $RC3/review-results.json \
  || echo "STOP: exit $?"
```

"Before" is what production stores (v1); the legacy replay is its own labelled column. **5.4**: declared checks
including defect reinstatement, `SUMMARY.md`, the ledger's review RESULT, and the owner's approval.

## 6. Stage 6: rehearsal on `valo_rc3_rehearsal`

Everything timed over the real link; write each duration into `$ART/rehearsal/durations.md`.

**The activation commit.** A local branch off **the reviewed tip, not commit C**, with exactly one commit: in
`app/scoring/impact_runtime.py` `ACTIVE_MANIFEST = "docs/superpowers/impact-rc3/candidate-manifest.json"`, and in
`app/scoring/impact.py` `IMPACT_CALCULATION_VERSION = 3` with a history comment. The rehearsal from 6.4 on runs from a
clean checkout of it:

> **Branch off the tip, and check that before branching** (external review, finding 4). Commit C `82d8e6b` is the
> frozen *scoring baseline* — what the manifest pins and what K4 reproduced. It is **not** a runnable execution
> checkout: at C the `impact-rc3/` directory holds only this runbook and the manifest. `review-results.json` arrived
> later in `6f58593`, so a branch off C fails at `verify-build --approved` with a missing file, and also lacks the
> test guards added since. Branching off the tip is safe precisely because the two differ only in documentation and
> tests — assert that rather than trust it, and stop if anything prints:
>
> ```bash
> git diff --stat 82d8e6b HEAD -- webapp/app webapp/scripts webapp/alembic
> ```
>
> Empty output means the scoring code at the tip is byte-identical to the freeze, so the manifest's behavioural
> digests still verify and the chain still holds. The export refuses to run if they do not.

```bash
$PY -c "from app.scoring.impact_runtime import active_scoring_config; print(active_scoring_config())"
```

**6.3 PR #67's pages (D8).** From commit C (no activation), serve the rehearsal database and review overtime state
diagrams, side resolution, sessions and match pages:

```bash
DATABASE_URL="$REH" $PY -m uvicorn app.main:app --port 8001
```

**6.4 Forward path**, from the activation checkout:

```bash
time DATABASE_URL="$REH" $PY scripts/export_impact_artifact.py --out "$ART/rehearsal" --label KR --active --projection both \
  && same "$ART/rehearsal/KR.json" "$ART/chain/K3-on.json" \
  && DATABASE_URL="$REH" $PY scripts/prewarm_player_cache_ids.py capture --roster-out "$ART/rehearsal/roster-ids.txt" \
    --recent-out "$ART/rehearsal/recent-ids.txt" \
  && time DATABASE_URL="$REH" $PY scripts/swap_impact_scores.py build --expect-database valo_rc3_rehearsal \
    --artifact "$ART/rehearsal/KR.load.csv" \
  && time DATABASE_URL="$REH" $PY scripts/swap_impact_scores.py verify-build --expect-database valo_rc3_rehearsal \
    --artifact "$ART/rehearsal/KR.load.csv" --sidecar "$ART/rehearsal/KR.json" --comparison "$ART/rehearsal/KR.csv" --approved "$RC3/review-results.json" \
    --manifest "$RC3/candidate-manifest.json" \
    --expect-comparison-sha256 "$CHAIN" \
  && time DATABASE_URL="$REH" $PY scripts/swap_impact_scores.py swap --yes --expect-database valo_rc3_rehearsal \
  && DATABASE_URL="$REH" $PY scripts/swap_impact_scores.py state --expect-database valo_rc3_rehearsal \
  && time DATABASE_URL="$REH" $PY scripts/swap_impact_scores.py verify-live --expect-database valo_rc3_rehearsal \
    --artifact "$ART/rehearsal/KR.load.csv" --sidecar "$ART/rehearsal/KR.json" --comparison "$ART/rehearsal/KR.csv" --approved "$RC3/review-results.json" \
    --manifest "$RC3/candidate-manifest.json" \
    --expect-comparison-sha256 "$CHAIN" \
  && time DATABASE_URL="$REH" $PY scripts/prewarm_player_cache_ids.py prewarm --ids-file "$ART/rehearsal/roster-ids.txt" \
  && DATABASE_URL="$REH" $PY scripts/verify_player_cache_coverage.py --ids-file "$ART/rehearsal/roster-ids.txt" \
  || echo "STOP: exit $?"
```

The KR input fingerprint must equal K1''s (`inputs.cohort_fingerprint`): that, with the equal hash, is the A14
comparison. Then serve the rehearsal from the activation checkout (`--port 8001`) and check pages.

**6.4a The declared database tests, on real data.** The declaration says the database tests run against the rehearsal
database. On a schema-only scratch database the corpus-measuring ones skip, so they run nowhere; run them here, and
run them **after** the swap, from the activation checkout. That ordering is the point: the table now holds
`scoring_version` 3 and the checkout computes 3, which makes `test_builder_matches_stored_values` an independent
end-to-end check on the loaded table -- one that never touches `verify-build`'s artifact comparison.

```bash
VALO_TEST_DATABASE_URL="$REH" $PY -m pytest \
    tests/test_impact_exante_swing.py tests/test_impact_reconstruction.py \
    tests/test_kill_order_leverage_gates.py tests/test_kill_order_stage_c0.py -q -rs \
    --deselect tests/test_impact_exante_swing.py::test_wrapper_still_persists_and_commits \
  || echo "STOP: exit $?"
```

**13 passed, 0 skipped.** A skip from `test_builder_matches_stored_values` is a finding, not noise: its message names
the versions it found, and a mismatch there means the loaded table is not at the version this checkout writes.

The deselected test calls the scorer's committing wrapper on a real match and belongs on `valo_rc3_test`; the guard
fails it by name if it is not deselected, rather than letting it rescore a match here (`1e0b722`).

Budget **46 minutes** and keep the machine awake: measured 2026-09-17 pre-swap at 45 m 51 s, nearly all of it stage C0
and the leverage gates. Pre-swap that run gave 12 passed and 1 skipped, the skip being the version mismatch above --
production's stored rows are version 1, the branch writes 2 (see `IMPACT_CALCULATION_VERSION`'s history comment).

**6.5 Gate tests.** Each must print the expected outcome.

```bash
# (a) an actual pre-0010 checkout updating an existing row: REFUSED
OLDPY="$(git rev-parse --show-toplevel)/webapp/.venv/Scripts/python.exe"   # 3.11, the pre-rc3 environment
git worktree add "$ART/rehearsal/main-worktree" origin/main
( cd "$ART/rehearsal/main-worktree/webapp" && DATABASE_URL="$REH" "$OLDPY" - <<'EOF'
import sys

from sqlalchemy import text

from app.db import SessionLocal

db = SessionLocal()
before = db.execute(text("SELECT sum(impact) FROM impact_scores")).scalar()
try:
    db.execute(text("UPDATE impact_scores SET impact = impact + 1 WHERE (round_id, match_player_id) IN "
                    "(SELECT round_id, match_player_id FROM impact_scores LIMIT 1)"))
    print("UNEXPECTED: the stale checkout wrote")
    sys.exit(1)
except Exception as exc:
    if "release write gate refused" not in str(exc):
        print(f"UNEXPECTED error: {exc}")
        sys.exit(1)
finally:
    db.rollback()   # nothing here is ever committed
if db.execute(text("SELECT sum(impact) FROM impact_scores")).scalar() != before:
    print("UNEXPECTED: the scores changed")
    sys.exit(1)
print("refused as expected, scores unchanged")
EOF
) || echo "STOP: gate probe (a) did not refuse -- exit $?"
git worktree remove "$ART/rehearsal/main-worktree"

# (b) an ingestion write without the identity, from the rc3 checkout: REFUSED, nothing committed
DATABASE_URL="$REH" $PY - <<'EOF'
import sys
import uuid

from app.db import SessionLocal
from app.models import Match
from app.models.match import MatchSource

db = SessionLocal()
before = db.query(Match).count()
try:
    db.add(Match(external_id=f"gate-probe-{uuid.uuid4()}", source=MatchSource.SCRAPED, map_name="Bind"))
    db.flush()
    print("UNEXPECTED: wrote without an identity")
    sys.exit(1)
except Exception as exc:
    if "release write gate refused" not in str(exc):
        print(f"UNEXPECTED error: {exc}")
        sys.exit(1)
finally:
    db.rollback()
if db.query(Match).count() != before:
    print("UNEXPECTED: the match count changed")
    sys.exit(1)
print("refused as expected, match count unchanged")
EOF
[ $? -eq 0 ] || echo "STOP: gate probe (b) did not refuse"

# (c) the activation checkout, gate open for impact-rc3, preflight verified: ALLOWED (rolled back)
DATABASE_URL="$REH" $PY scripts/install_release_write_gate.py --expect-database valo_rc3_rehearsal --state open --note "gate test c" \
  && DATABASE_URL="$REH" $PY - <<'EOF'
import uuid

from app.db import SessionLocal
from app.models import Match
from app.models.match import MatchSource
from app.scoring.ingest_preflight import verify_ingest_preflight

db = SessionLocal()
try:
    verify_ingest_preflight(db)
    db.add(Match(external_id=f"gate-probe-{uuid.uuid4()}", source=MatchSource.SCRAPED, map_name="Bind"))
    db.flush()
    print("allowed as expected")
finally:
    db.rollback()   # the probe never keeps its match
EOF
probe_c=$?; echo "probe (c) exit $probe_c"
DATABASE_URL="$REH" $PY scripts/install_release_write_gate.py --expect-database valo_rc3_rehearsal --state closed --note "gate tests done" \
  || echo "STOP: the gate did not close again -- exit $?"
[ "$probe_c" -eq 0 ] || echo "STOP: probe (c) failed with exit $probe_c -- the gate refused a write it must allow"
```

Every probe rolls back and exits nonzero unless the gate behaved exactly as stated. Confirm the gate is closed again
before going on.

**The status is captured before the cleanup runs, and tested after it** (external review, finding 3). Previously
probe (c)'s exit was only `echo`ed and the gate-closing command ran unconditionally, so a failed probe followed by a
successful cleanup ended the block with no `STOP` at all — the prose above claimed a broken gate would stop the
rehearsal "instead of printing a line nobody reads", while the command did precisely that. The gate must still be
closed even when the probe fails, which is why the cleanup is not chained behind it.

**6.6 Concurrency (B2).** With the rehearsal served on port 8001 from the activation checkout, record page latencies in
a second terminal, first with no swap running (the baseline), then across a swap and a rollback:

```bash
DATABASE_URL="$REH" $PY - "$ART/rehearsal/recent-ids.txt" > "$ART/rehearsal/recent-paths.txt" <<'EOF'
import sys
import urllib.parse
from app.db import SessionLocal
from app.models import Player
db = SessionLocal()
for line in open(sys.argv[1], encoding="utf-8"):
    if line.strip():
        print("/players/" + urllib.parse.quote(db.get(Player, int(line)).display_name, safe=""))
EOF
for i in $(seq 1 300); do while read -r path; do
  curl -s -o /dev/null -w "%{http_code} %{time_total} $(date +%T) $path\n" "http://127.0.0.1:8001$path"
done < "$ART/rehearsal/recent-paths.txt"; done | tee "$ART/rehearsal/latency.txt"
```

Most of these pages miss the cache (the swap emptied it), which is the path that reads the cache and then the scores.

No deadlock, no 5xx, and no request stalled more than 5 seconds per lock acquisition beyond the baseline (the swap
takes two, and clears the cache by DELETE, which takes no lock a page load conflicts with). A swap that cannot take its locks
exits 4 having changed nothing; that is a pass for this test, and the swap is retried.

**6.7 Rollback path**, then forward again from a fresh build:

```bash
time DATABASE_URL="$REH" $PY scripts/swap_impact_scores.py rollback --yes --expect-database valo_rc3_rehearsal \
  && DATABASE_URL="$REH" $PY scripts/swap_impact_scores.py state --expect-database valo_rc3_rehearsal \
  && "$PGBIN/psql" -v ON_ERROR_STOP=1 -c "DROP TABLE impact_scores_rc3_rolled_back" "$REH" \
  && DATABASE_URL="$REH" $PY scripts/swap_impact_scores.py build --expect-database valo_rc3_rehearsal --artifact "$ART/rehearsal/KR.load.csv" \
  && DATABASE_URL="$REH" $PY scripts/swap_impact_scores.py verify-build --expect-database valo_rc3_rehearsal \
    --artifact "$ART/rehearsal/KR.load.csv" --sidecar "$ART/rehearsal/KR.json" --comparison "$ART/rehearsal/KR.csv" --approved "$RC3/review-results.json" \
    --manifest "$RC3/candidate-manifest.json" \
    --expect-comparison-sha256 "$CHAIN" \
  || echo "STOP: exit $?"
```

Before swapping forward again, prove an interrupted swap changes nothing. In `psql "$REH"` run
`BEGIN; LOCK TABLE impact_scores IN ACCESS SHARE MODE;` and leave it open. In another terminal start
`swap --yes --expect-database valo_rc3_rehearsal` and press Ctrl+C within 5 seconds. `COMMIT;` in psql. `state` must show
no new `swap swapped` entry. Repeat without Ctrl+C: the swap exits 4 after 5 seconds and logs `lock timeout`. Then swap
for real, and `verify-live`.

**6.8 R2 dry run** (rehearsal only): roll back (6.7's first command), serve with `MAINTENANCE_MODE=1` and confirm pages
return 503 while `/health` returns 200, then:

```bash
time DATABASE_URL="$REH" $PY -m alembic downgrade 0007 \
  && DATABASE_URL="$REH" $PY scripts/release_preflight.py --expect-database valo_rc3_rehearsal --expect-alembic 0007 --expect-gate closed \
  && "$PGBIN/psql" -v ON_ERROR_STOP=1 -c "SELECT tgrelid::regclass AS gated_table, count(*) FROM pg_trigger WHERE tgname LIKE 'scoring_gate_%' AND NOT tgisinternal GROUP BY 1 ORDER BY 1" "$REH" \
  || echo "STOP: exit $?"
```

The gate survives the downgrade, still closed, on every table that still exists (`round_player_spend` goes with 0009).
After a real R2, the reverted code sets no identity, so nothing ingests until the owner lifts the gate:
`DROP FUNCTION scoring_gate_guard() CASCADE` (removes every gate trigger; the gate and log tables stay as the record).
Do not rerun `install_release_write_gate.py` below 0009: it refuses because `round_player_spend` no longer exists.

**6.9** Record every duration and the latest safe rollback time.

## 7. Stage 7: PR #67 without activation (production, site up)

**7.1 Recovery gate: cleared (2026-09-17).** The Render Recovery page offers restore to any timestamp in the past 7
days, so production can be recreated at a point before any step below. Two consequences for the steps that follow:
recovery restores into a NEW instance, so R3 also repoints the web service and `.env.remote`; and after 7 days
recovery rests on the B0/B1 dumps and `impact_scores_v1`, which is why that table is kept at least 14 days (8.12).
Note the server time printed by each preflight -- that is the timestamp to restore to.

**7.2 Preflight and backup B1.** Note the printed server time: it is the restore point.

```bash
DATABASE_URL="$PROD" $PY scripts/release_preflight.py --expect-database valowithfriendsdb --expect-alembic 0007 \
    --expect-matches 3125 --expect-max-match-id 3133 --expect-gate absent --max-transaction-seconds 60 \
  && time "$PGBIN/pg_dump" --dbname="$PROD" --format=custom --no-owner --no-privileges --file="$ART/B1-before-0008.dump" \
  && "$PGBIN/pg_restore" --list "$ART/B1-before-0008.dump" > "$ART/B1.list" \
  || echo "STOP: exit $?"
```

**7.3 Migrations by hand**, from a clean checkout of exactly the commit PR #67 will merge:

```bash
git status --porcelain --untracked-files=all -- . | head -5    # must print nothing
git rev-parse HEAD                                              # must be PR #67's head
time DATABASE_URL="$PROD" $PY -m alembic upgrade head \
  && DATABASE_URL="$PROD" $PY scripts/release_preflight.py --expect-database valowithfriendsdb --expect-alembic 0010 \
    --expect-matches 3125 --expect-max-match-id 3133 --expect-gate absent \
  || echo "STOP: exit $? -- do not merge"
```

Migrations run with `lock_timeout` 10s and `statement_timeout` 15min (`app/migration_guards.py`), so a blocked
migration fails instead of waiting. A failure here leaves the database at whatever revision the preflight reports; do
not merge.

**7.4 Install the gate, closed.** From here no checkout can write scores or ingest. Installing drops and recreates the
triggers, which takes ACCESS EXCLUSIVE on each gated table for milliseconds; it gives up after 5 seconds rather than
queue behind a slow page read, having changed nothing. A lock-timeout error means retry, not investigate.

```bash
DATABASE_URL="$PROD" $PY scripts/install_release_write_gate.py --expect-database valowithfriendsdb --state closed \
    --release-id impact-rc3 --admin-id rc3-runbook --note "installed before merging PR #67" \
  && DATABASE_URL="$PROD" $PY scripts/release_preflight.py --expect-database valowithfriendsdb --expect-alembic 0010 --expect-gate closed \
  || echo "STOP: exit $?"
```

**7.5 Merge PR #67** with a merge commit. Render's build runs `alembic upgrade head` (a bounded no-op). When the deploy
is live:

```bash
curl -s -o /dev/null -w "health %{http_code}\n" https://valowithfriendstracker.onrender.com/health
curl -s -o /dev/null -w "player page %{http_code}\n" "https://valowithfriendstracker.onrender.com/players/NPrightdolphin%23NA1"
```

Until Stage 8 the site shows today's v1 scores with the branch's other changes.

## 8. Stage 8: activation (production, site up)

Rebase the activation branch onto the merged `main` (its only change stays the one commit) and open its PR; do not
merge yet. Run 8.1 to 8.4 and 8.6 to 8.7 from a clean checkout of that PR's head.

**8.1 Preflight and K5:**

```bash
DATABASE_URL="$PROD" $PY scripts/release_preflight.py --expect-database valowithfriendsdb --expect-alembic 0010 \
    --expect-matches 3125 --expect-max-match-id 3133 --expect-gate closed \
  && time DATABASE_URL="$PROD" $PY scripts/export_impact_artifact.py --out "$ART/activation" --label K5 --active --projection both \
  && same "$ART/activation/K5.json" "$ART/chain/K3-on.json" \
  || echo "STOP: exit $?"
```

**8.2 Build and verify.** verify-build rechecks every match's input fingerprint against K5's, the approved rows, and
K5's hash against the chain.

```bash
time DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py build --expect-database valowithfriendsdb --artifact "$ART/activation/K5.load.csv" \
  && time DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py verify-build --expect-database valowithfriendsdb \
    --artifact "$ART/activation/K5.load.csv" --sidecar "$ART/activation/K5.json" --comparison "$ART/activation/K5.csv" --approved "$RC3/review-results.json" \
    --manifest "$RC3/candidate-manifest.json" \
    --expect-comparison-sha256 "$CHAIN" \
  || echo "STOP: exit $?"
```

**8.3 Capture the prewarm lists** before the swap empties the cache:

```bash
DATABASE_URL="$PROD" $PY scripts/prewarm_player_cache_ids.py capture --roster-out "$ART/activation/roster-ids.txt" \
    --recent-out "$ART/activation/recent-ids.txt" || echo "STOP: exit $? (1 = a roster name matched no player)"
```

**8.4 Swap**, then verify what the site now reads:

```bash
DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py swap --yes --expect-database valowithfriendsdb; echo "swap exit $?"
DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py state --expect-database valowithfriendsdb
```

Exit 4: a lock did not come free within 5 seconds and nothing changed; run it again. Those 5 seconds bound each
acquisition, not the whole transaction: the swap takes two, and clears the cache by DELETE rather than a third. Exit 3: read the reason. Anything
else (a traceback, a dropped connection): run `state` before anything else. The log holds a `swap swapped` entry
exactly when the swap committed.

```bash
time DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py verify-live --expect-database valowithfriendsdb \
    --artifact "$ART/activation/K5.load.csv" --sidecar "$ART/activation/K5.json" --comparison "$ART/activation/K5.csv" --approved "$RC3/review-results.json" \
    --manifest "$RC3/candidate-manifest.json" \
    --expect-comparison-sha256 "$CHAIN" \
  && "$PGBIN/psql" -v ON_ERROR_STOP=1 -c "SELECT count(*) AS match_3133_rows FROM impact_scores s JOIN rounds r ON r.id = s.round_id WHERE r.match_id = 3133" "$PROD" \
  || echo "STOP: exit $? -- consider R1"
```

Match 3133 must show 210 rows.

**8.5 Merge the activation PR** with a merge commit and wait for the deploy to go live. The live code is confirmed
by the cache version it writes: load one roster page, then

```bash
"$PGBIN/psql" -c "SELECT DISTINCT version FROM player_view_cache ORDER BY 1" "$PROD"
```

must list `4003003003` (schema 4, state diagram 3, fight-EV 3, Impact **3**) for the rows written after the deploy.

**8.6 Caches**, from the activation checkout (the cache version includes `IMPACT_CALCULATION_VERSION`):

```bash
"$PGBIN/psql" -v ON_ERROR_STOP=1 -c "DELETE FROM player_view_cache" "$PROD" \
  && time DATABASE_URL="$PROD" $PY scripts/prewarm_player_cache_ids.py prewarm --ids-file "$ART/activation/roster-ids.txt" \
  && DATABASE_URL="$PROD" $PY scripts/verify_player_cache_coverage.py --ids-file "$ART/activation/roster-ids.txt" \
  && DATABASE_URL="$PROD" $PY -c "from app.db import SessionLocal; from app.services.site_stats import refresh_site_stats; db = SessionLocal(); refresh_site_stats(db); db.close(); print('site stats refreshed')" \
  && { DATABASE_URL="$PROD" $PY scripts/prewarm_player_cache_ids.py prewarm --ids-file "$ART/activation/recent-ids.txt" \
         > "$ART/activation/prewarm-recent.log" 2>&1 & \
       echo $! > "$ART/activation/prewarm-recent.pid"; \
       echo "background prewarm pid $(cat "$ART/activation/prewarm-recent.pid")"; } \
  || echo "STOP: exit $? -- the background prewarm was NOT started"
```

The background prewarm is **inside** the `&&` chain and records its pid. Both matter (external review, finding 3):
previously it was a separate line, so a failed roster prewarm, coverage check or stats refresh printed `STOP` and then
launched the background worker anyway — and since the instruction is to paste a block and read its output afterwards,
reading `STOP` could not prevent the launch. The pid file is what **R1.1** stops and waits on; without it a rollback
has only a presence check, which races.

**8.7 Acceptance**, read-only: every match equals its replay under the active configuration.

```bash
time DATABASE_URL="$PROD" $PY - <<'EOF'
import sqlalchemy as sa
from app.db import SessionLocal
from app.scoring.impact_runtime import active_scoring_config
from scripts.backfill_impact_candidate import replay_diffs
db = SessionLocal()
db.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
ids = [m for (m,) in db.execute(sa.text("SELECT id FROM matches ORDER BY id"))]
diffs = replay_diffs(db, ids, active_scoring_config())
print(f"{len(ids)} matches replayed, {len(diffs)} differ")
for match_id, detail in list(diffs.items())[:20]:
    print(" ", match_id, detail)
db.rollback()
raise SystemExit(1 if diffs else 0)
EOF
```

Then `verify-live` once more (8.4's command) as the final input-fingerprint and approved-results check. Any difference:
R1.

**8.8 Page checks.** Match 3104's player totals equal the AFTER column of `match-3104-site.md`; 3133's page shows
scores; every roster page returns 200.

**8.9 Observation hold: 48 hours, gate closed** (D11). Note the start time. R1 is complete throughout.

**8.10 Reopen ingestion.** R1 expires here.

```bash
DATABASE_URL="$PROD" $PY scripts/install_release_write_gate.py --expect-database valowithfriendsdb --state open --note "48-hour hold over"
```

Then, in PowerShell from the activation checkout's `webapp/`: `.\scripts\refresh_remote.ps1 -Count 5`. It runs
`.venv313`, and the ingestion preflight refuses any other interpreter or package set. After the first new match
(replace `NEW` with its id):

```bash
"$PGBIN/psql" -c "SELECT DISTINCT scoring_version FROM impact_scores s JOIN rounds r ON r.id = s.round_id WHERE r.match_id = NEW" "$PROD"
DATABASE_URL="$PROD" $PY -c "from app.db import SessionLocal; from app.scoring.impact_runtime import active_scoring_config; from scripts.backfill_impact_candidate import replay_diffs; print(replay_diffs(SessionLocal(), [NEW], active_scoring_config()) or 'equals its replay')"
```

`scoring_version` must be 3 only.

**8.11** Ledger activation note: commit SHAs, durations, hashes, gate transitions (`scoring_gate.updated_at`, the log).

**8.12** Keep `impact_scores_v1` at least 14 days, and longer than the recovery window; dropping it is a separate,
later decision.

## 9. Stage 9: rollback

**R1 (scoring). Valid only while the gate is closed and no match has arrived since the swap**; the tool refuses
otherwise.

**This is the only R1 sequence. Run it in this order.** An earlier version of this section printed the rollback
command *above* the instruction to stop the background prewarm, and the short plan omitted the rollback step
altogether — leaving a "rollback" that reverts the code while `impact_scores` still holds rc3, which serves rc3
numbers under v1 code and looks complete (external review, finding 1).

**R1.1 Stop the background prewarm and wait for it to exit.** Step 8.6 leaves one running over the recently cached
players. It reads scores and writes cache rows in one transaction, so a live one both blocks the rollback's rename
until its transaction ends and can write version-3 cache rows *after* the restore. A presence check is not enough —
capture the pid when 8.6 launches it, and wait.

```bash
kill "$(cat "$ART/activation/prewarm-recent.pid")" 2>/dev/null
while kill -0 "$(cat "$ART/activation/prewarm-recent.pid")" 2>/dev/null; do sleep 1; done
echo "background prewarm stopped"
```

**R1.2 Restore the score table** — this is the step that actually undoes the swap:

```bash
DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py rollback --yes --expect-database valowithfriendsdb; echo "rollback exit $?"
DATABASE_URL="$PROD" $PY scripts/swap_impact_scores.py state --expect-database valowithfriendsdb
```

`state` must show a `rollback rolled back` entry. Exit 4 is a lock timeout that changed nothing: run it again.

**R1.3 Revert the activation PR** (site stays up) and wait for the deploy to go live.

**R1.4 Clear and refill the cache, naming the database** — the previous wording passed no database at all:

```bash
"$PGBIN/psql" -v ON_ERROR_STOP=1 -c "DELETE FROM player_view_cache" "$PROD" \
  && DATABASE_URL="$PROD" $PY scripts/prewarm_player_cache_ids.py prewarm --ids-file "$ART/activation/roster-ids.txt" \
  || echo "STOP: exit $?"
```

Never TRUNCATE (see 8.6), and prewarm from the **reverted** checkout, so the cache version it writes matches the code
now serving.

Ingestion stays closed until the owner decides. After ingestion reopens, R1 no longer applies: fix forward, or capture
the current scores first and decide what happens to matches ingested since (B4).

**R2 (PR #67)**, each step verified before the next:

1. Set `MAINTENANCE_MODE=1` on the Render web service. Confirm it is live: a player page returns 503, `/health`
   returns 200, and the deploy's old instance has drained.
2. After R1 if activation happened: `DATABASE_URL="$PROD" $PY -m alembic downgrade 0007`, then the preflight with
   `--expect-alembic 0007 --expect-gate closed`.
3. Revert PR #67's merge and deploy. That deploy reopens the site (the reverted code has no switch); remove
   `MAINTENANCE_MODE` afterwards.
4. The gate survives the downgrade, closed, so nothing ingests (see 6.8 for lifting it).

**R3 (last resort):** point-in-time recovery into a new instance, then repoint the web service and `.env.remote`.
Pending the recovery gate.

## Reference: exit codes

| script | codes |
|---|---|
| `release_preflight.py` | 0 every expectation holds; 3 a mismatch |
| `swap_impact_scores.py` | 0 done; 2 verification found problems; 3 refused, nothing changed; 4 lock timeout, nothing changed |
| `install_release_write_gate.py` | 0 done; 3 connected to a database other than `--expect-database` |
| `freeze_impact_candidate.py` | 0 frozen and verified; 3 the tree is not exactly a commit |
| `prewarm_player_cache_ids.py` | capture: 1 a roster name matched no player; prewarm: 1 a player failed |
| `verify_player_cache_coverage.py` | 0 every player-scope usable; 1 otherwise |
