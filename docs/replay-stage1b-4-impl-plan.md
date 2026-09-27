# Replay viewer, Stage 1b → Stage 4: implementation plan

Written 2026-09-27 by the AFK run `2026-09-27-replay-1b-to-2`. The design is
`docs/replay-viewer-plan.md` (pass 6); this file turns what is left of it into ordered, checkable steps.
Where the two disagree, the design plan wins, except where the user's answers of 2026-09-27 (R1-R6 below)
amend it. Nothing here is pushed, merged or deployed: every stage is a local branch, stacked on the one
before (R1).

## What the user settled on 2026-09-27 (the run's register)

- **R1.** Build 1b, 1c, 2, 3 and 4 as stacked local branches (1c from `origin/main`). Stop and report if a
  later stage can't be built well on unmerged ones.
- **R2.** Stored rounds carry the ability objects and shots the local preview draws.
- **R3.** Per-kill Impact goes live, display-only. It amends decision 5 and Stage 4's "no new Impact
  calculation" gate: no scoring change, and Impact values are never recomputed differently.
- **R4.** 1b starts at `replay-local-preview` @ `0570183`; 1c is cut from `origin/main` (`c8b8a36`).
- **R5.** A refusal on real data is a finding, never a reason to loosen a check.
- **R6.** Read-only friends DB through `with_friends_db.py --read-only`; a throwaway local PG18 cluster for
  the Stage 2 gate. No prod writes, prod migrations or demo DB.

## How R2 and R3 change the design plan

**R2: abilities and shots in the stored blob.** They become two new `util` kinds, which the format rules
allow without a `v` bump (a new `k` is ignored by old readers):
- `{"k": "ability", "t": <t0>, "by": <slot|null>, "t1", "kind", "code", "name", "agent", "owner_by", "u",
  "v", ["yaw"], ["thrown"], ["path"], ["owner_d", "other_d"]}` — today's `extras` ability row, with `t0`
  renamed to the envelope's `t` and `slot` to `by`;
- `{"k": "shot", "t", "by", "u", "v", ["u1", "v1"], "gun", "n"}` — today's shot row, the same renames.

`extras.build_extras` moves inside the condenser's run (so the upload worker, which calls the same
`condense_export_dir`, produces identical blobs), with the world-position reader it needs
(`build_replay_bundle.WorldPositions`) moved into `app/replays/extras.py`. `CONDENSE_REVISION` 4 → 5.
The size budget is re-measured on all six competitive matches (W7). The viewer rebuilds its per-round
`extras` object from these `util` entries, so its drawing code is unchanged.

**R3: per-kill Impact.** Decision 5's "per-kill Impact is out of scope" becomes: the page shows each kill's
share of the stored round Impact, computed once by the **existing** scorer, read-only, and stored beside
the link. Concretely:
- `app/services/replay_impact.py` is the only replay module allowed to import the scorer, and only its
  read-only entry points (`build_impact_rows_for_match` with a `kill_observer`, `PERSISTED_FIELDS`,
  `FormulaWeights`, `impact_runtime.active_scoring_config`/`active_manifest`). It computes, per kill,
  `gain = leverage × kill_order_bonus_x_time` and `loss = leverage × death_order_bonus_x_time`, exactly as
  `build_replay_bundle.py` does, and **refuses** (stores nothing) unless every recomputed row's persisted
  fields equal the stored `impact_scores` row and each player's per-kill sums reconcile with it (death side
  exactly; kill side within 1 for rounding, as today's bundle check).
- It runs **after** a link commits, in its own session and transaction (a scorer SQL error inside the store's
  transaction would abort the store and the link: review B2), and only from user-run scripts: the
  `ingest_replay.py` write path, `link_replays.py` (`--refresh-impact` recomputes), and the post-crawl hook,
  which also fills any linked replay whose split is missing. **The web service never runs the scorer**
  (review S3: it may not reach `docs/superpowers/impact-v4/candidate-manifest.json` under `rootDir: webapp`),
  so an uploaded replay shows per-kill Impact after the next crawl or `link_replays.py` run (run decision
  D3). A failure leaves `replays.kill_impact` null and the link stands. It never writes `impact_scores`.
- `kill_impact` stores full-precision `[gain, loss]` per `kill_events.id`, plus a fingerprint (sha256) of the
  persisted fields of the `impact_scores` rows it reconciled against. The page shows per-kill values only
  when that fingerprint equals the current rows'; after any rescore it hides them until the split is
  recomputed. The unlink trigger clears `kill_impact` (its keys are kill IDs).
- "Add up exactly" (R3) means the same reconciliation `build_replay_bundle.py` applies today: the death side
  exactly, the kill side's leverage part within 1 for the stored rows' integer rounding.
- **Scoring inputs stay byte-identical (review B1).** `impact_manifest.HASHED_SOURCES` includes
  `app/models/{match,round,kill_event,impact_score,player}.py`; editing any of them makes every scoring run
  refuse ("differs from the frozen digest"). So Stage 2 edits none of them: the replay models live in a new
  `app/models/replay.py` with no relationship back-references, and `players.riot_subject` exists only in
  the migration and in Core SQL (`app/replays/db.py`). Every branch checks `git diff origin/main --` over
  every HASHED_SOURCES path is empty, and a test asserts the active manifest still verifies.
- Stage 4's import gate is amended, not deleted: nothing under `app/replays/`, `app/routers/`,
  `replay_view.py`, `replay_upload.py` or `replay_worker/` imports `app.scoring.impact`,
  `kill_order_leverage` or `win_probability`; `app/services/replay_impact.py` may import only the
  read-only names above and never `compute_impact_for_match`, a commit or an `add`; and
  `git diff origin/main -- webapp/app/scoring` is empty.

No scoring-release-process step is needed: nothing in `app/scoring/` changes, and the values shown are
the stored ones split by the scorer's own observer.

## Branches and worktrees

| Stage | Branch | Worktree | Cut from |
| --- | --- | --- | --- |
| 1b | `afk/2026-09-27-replay-1b` | `.worktrees/afk-2026-09-27-replay-1b` | `replay-local-preview` @ `0570183` |
| 1c | `afk/2026-09-27-replay-1c` | `.worktrees/afk-2026-09-27-replay-1c` | `origin/main` @ `c8b8a36` |
| 2 | `afk/2026-09-27-replay-2` | `.worktrees/afk-2026-09-27-replay-2` | 1c's tip |
| 3 | `afk/2026-09-27-replay-3` | `.worktrees/afk-2026-09-27-replay-3` | 2's tip |
| 4 | `afk/2026-09-27-replay-4` | `.worktrees/afk-2026-09-27-replay-4` | 3's tip |

Files move between branches with `git restore --source=<ref> -- <paths>` and one commit, never a merge.
Interpreters by absolute path: `webapp\.venv313\Scripts\python.exe` (primary, and anything touching
Impact) and `webapp\.venv\Scripts\python.exe` (second suite run, Playwright). Friends-DB reads go through
`.worktrees/replay-local-preview/webapp/scripts/with_friends_db.py --expect-database valowithfriendsdb
--read-only <.venv313 python> <absolute script path>` (run decision D1). Scratch output goes in the run
folder or `%TEMP%\valo-replay\afk-scratch\`.

## Steps

Each step: files, the design-plan section it implements, dependencies, and a yes/no check.
`PY` = `.venv313` python, `T` = `tests/replays`.

### W3. Stage 1b gate on the competitive exports (branch 1b)

**W3.1 Link-level gate rows on real DB rows.** `scripts/replay_gate.py --condensed <pickle> … --db` (the
pickles are `build_replay_bundle.py`'s `bundle-condensed.pickle`, at `CONDENSE_REVISION` 4): every
link-level row of the gate table is built generically from a linked replay and its real `DbMatch`, with and
without decoded winners, and the same builder also runs on the synthetic match. A row the data has no case
for prints `N/A`, never `OK`; a base replay that refuses prints `FINDING` (R5). The manifest-fallback and
coverage rows are condense-level (W3.2 and the synthetic rows). Design: "Stage 1b", Gate.
Check: `PY -m pytest T/test_replay_gate.py` passes; the real run prints `0 MISMATCH` over all six.
**Re-run after W4** if the freeze changes any condenser or linker value.

**W3.2 Condense-level rows on two real exports.** `replay_gate.py --export-dir <dir> --vrf <vrf>` on
Ascent and Abyss, one at a time. Check: `0 MISMATCH`; the baseline row `OK` or a recorded `FINDING`.

**W3.3 Findings 14-28** (the design's list, "Stage 1b" step 3), answered by a scratch script in the run
folder (never the repo) over the six cached replays, their manifests and read-only DB rows; counts only, no
Subject or name printed. Check: each finding has a line with evidence (or "not answerable" and why) in the
design plan's new "Stage 1b results".

**W3.4 Plan update.** `docs/replay-viewer-plan.md`: "Stage 1b results", decision 1 closed by 15 and 21,
decision 5 amended (R3). Stage 1a's open items (the Swiftplay P-c and P-f findings) are recorded as **still
open** for the user, not closed. Check: `grep -n "Stage 1b results"` hits and holds the gate summary.

### W4. Freeze (branch 1b)

Every `PROVISIONAL(D<old>)` in `app/replays/*.py`, `scripts/*.py` and `replay.js` is decided from the
six-match evidence and re-marked `PROVISIONAL(D<freeze>)`: one grouped tier 2 approval card for the whole
set (judgment.md). Values the evidence doesn't move keep their number; any change updates its test. The
design plan's "Provisional values" becomes a table with n=6 and the observed margins. Depends on W3.
Check: `grep -rn "PROVISIONAL(" webapp/app webapp/scripts` shows only the freeze decision's number;
`PY -m pytest T` passes.

### W5. Competitive fixture (branch 1b)

`scripts/make_replay_fixture.py --export-dir <Ascent> --name competitive --rounds 2 --movement-step 8
--vrf <archive .vrf>` → `tests/fixtures/replay/competitive/`. Tests: it condenses, scans clean (P-h), its
Subjects are synthetic. Depends on W4. Check: `PY -m pytest T/test_replay_fixture.py` passes; the fixture
is ≤ 2 MB.

### W6. Stage 1c branch (branch 1c, from `origin/main`)

First, on the fresh worktree (still equal to `origin/main`), the **`origin/main` baselines**: full suite
under `.venv313`, then `.venv`, serially; the failing-ID sets are saved in the run folder (review S5).
Then carry from 1b's tip with `git restore --source`: the replay docs (`replay-viewer-plan.md`,
`replay-viewer-handoff.md`, `replay-pass6-impl-plan.md`, this file); `webapp/replay_parser.json`;
`scripts/{build_replay_parser.ps1, export_replay.ps1, archive_replays.py, with_friends_db.py,
vendor_map_assets.py, ingest_replay.py, make_replay_fixture.py, replay_gate.py}`;
`app/replays/{__init__,format,contract,condense,header,link}.py`; `static/data/{maps,agents}.json`;
`static/img/maps/*`; `tests/replays/*` except the viewer, preview-route and extras tests;
`tests/{test_archive_replays,test_vendor_map_assets,test_with_friends_db}.py`; `tests/fixtures/replay/**`;
the `.gitignore` rules. Everything else on the local preview branch waits for Stage 2 or stays local.
`test_replay_isolation.py` is adapted to the files 1c has (review S6). `ingest_replay.py` keeps "no writes
until Stage 2". `with_friends_db.py` finds `.venv313` up the tree when `webapp/.venv313` is missing (the
known worktree fix). Design: Stage 1c. Depends on W5.
Check (the 1c gate): `PY -m pytest T tests/test_archive_replays.py tests/test_vendor_map_assets.py
tests/test_with_friends_db.py` passes; full suite `.venv313` failing IDs = the `origin/main` baseline;
read-only `--dry-run` on a competitive export prints `linked`; `git ls-files | grep -iE
'\.vrf$|valo-replay|events\.ndjson$'` prints nothing outside `tests/fixtures/replay/`; `git diff origin/main
--` over `app/scoring`, `requirements.txt` and every HASHED_SOURCES path is empty.

### W7. Stored abilities and shots (branch 2, from 1c)

`app/replays/extras.py` carried in (review n2), `WorldPositions` moved into it, reading movement through a
**second** `MovementStream` (the streaming loader's is consumed once: review S8), and `util_entries()`
producing the two new kinds; `condense_export_dir` runs it after `condense()` (a local import avoids the
`extras` → `condense` cycle) and re-encodes; `format.py` `CONDENSE_REVISION = 5`; tests (the util
envelope, determinism, no identity fields, the streaming/in-memory parity extended to util). Design: JSON
v1 `util`, R2. Depends on W6.
Check: `PY -m pytest T` passes; a scratch script condenses all six serially and prints per-match p95 and
total gzipped bytes, condense time and peak RAM: every p95 ≤ 60 KB and every match ≤ 1.5 MB (else the
design's quantisation step, re-measured).

### W8. Stage 2 backend (branch 2)

- **W8.0** the throwaway cluster (review S4): a run-folder `.ps1` runs `initdb` under
  `%TEMP%\valo-replay\afk-scratch\pg`, starts it on 127.0.0.1:55432, and creates `valo_replay_test` (tests)
  and `valo_replay_gate` / `valo_replay_demo_test` (W10). It stays up through W11 and is stopped and deleted
  at wrap-up.
- `alembic/versions/0012_replays.py` + a new `app/models/replay.py` (no back-references; **no edit to any
  HASHED_SOURCES file**: review B1): `replays` (with `link_inputs`, `kill_impact jsonb null`),
  `replay_rounds`, `replay_players` (`subject uuid null`, `UNIQUE (replay_id, subject)`), `replay_uploads`,
  `players.riot_subject uuid null unique` (migration only), and the unlink trigger, which also clears
  `kill_impact` (review S1).
- `app/replays/store.py`: the advisory lock, the dedupe rule, one multi-row insert per table, link in the
  same transaction, the demo refusal.
- `app/replays/db.py`: `load_link_candidates` (moved from `ingest_replay.py`; `replay_gate.py --db`
  follows it), `write_link`, `link_replay`, `riot_subject` through Core SQL, the backfill behind
  `BACKFILL_SUBJECTS` (a tier 2 card).
- `app/services/replay_impact.py` (R3, above), run after commit in its own session (review B2).
- `scripts/link_replays.py` (`match_id IS NULL`, `--uuid`, `--refresh-impact`), `scripts/ingest_replay.py`
  write path (`--replace`), `scripts/reingest_replays.py --dry-run`, the best-effort crawl hook.
- Tests: DB tests on `valo_replay_test` (forced-failure rollback, same-sha no-op, different-sha rules, the
  trigger incl. `kill_impact`, an incomplete round set reads as no replay, concurrent stores serialise, a
  forced scorer failure leaves the replay stored and linked with `kill_impact` null); R3 unit tests
  (reconcile passes, refuses on a row mismatch, refuses on a sum mismatch, the fingerprint hides a stale
  split); the import/text gate for `replay_impact.py`; the active manifest still verifies.
Design: "Storage", "Linking", Stage 2 steps 1-3. Depends on W7.
Check: `PY -m pytest T` passes with and without `VALO_TEST_DATABASE_URL`; `alembic upgrade head`,
`downgrade -1`, `upgrade head` against the cluster exit 0; the HASHED_SOURCES diff is empty.

### W9. Stage 2 front end (branch 2)

`app/routers/replays.py` (page; `{n}.json` as the stored bytes with `Content-Encoding: gzip`, `ETag` = 16
hex of the bytes' sha256, `Cache-Control: private, no-cache`, `304`; `/matches/{external_id}/replay`),
`app/services/replays.py` (valid-replay lookup; page context: players, per-round DB data, kill feed rows with
weapon and per-kill Impact, round stats, the replay's `source_sha256`), `app/main.py`, templates,
`static/js/replay.js` (+ `abilities.json`, ability icons) carried from `replay-local-preview` and taught to
build a round from a stored blob plus page data, `style.css`, "Replay" links in `matches/detail.html` and
`_round_detail.html`, the Watch column in `matches/list.html` and `sessions/detail.html` pointing at
`/replays/{uuid}`. Demo mode: 404s and no links. Route tests: demo 404s with rows present; gzip headers and
decoded body; ETag/304; a new body after a `--replace` under the same recipe (review S10); links on the
initial and htmx round detail; unlinked pages show no site data; no stale link data after a match delete.
Design: "The viewer", Stage 2 steps 4-7. Depends on W8.
Check: `PY -m pytest T` passes; a headless Playwright script (`.venv`) opens every round of every ingested
match on the cluster at 1280 px and 390 px, plays, scrubs, changes round and clicks a feed row, and reports
no console error and a drawn canvas.

### W10. Stage 2 gate on the throwaway cluster (branch 2)

A run-folder script (review S11 — two databases, no ID collisions):
- `valo_replay_demo_test`: schema at 0011, `seed_data/demo_matches.sql`; row counts and checksums of
  `matches`/`rounds`/`kill_events`; `upgrade head`, unchanged; `downgrade -1` and back.
- `valo_replay_gate`: schema at 0011, the six matches' rows (players, matches, match_players, rounds,
  round_player_stats, kill_events, impact_scores) copied with their own IDs by a read-only Python script
  under the wrapper; `upgrade head`; ingest all six; link-later test (delete a match row → the trigger unlinks
  and clears, `link_inputs` unchanged; restore; `link_replays.py` relinks with the same mapping); the
  unlinked case is that deleted match's replay (the Swiftplay export refuses at condense, review S10);
  uvicorn with `DEMO_MODE=true` for the 404s; a match page without a replay compared with the same page from
  an `origin/main` reference worktree (`.worktrees/afk-2026-09-27-main-ref`) on the same DB.
Design: Stage 2 pre-merge gate (R6). Depends on W9.
Check: every gate line prints PASS, recorded in the design plan's Stage 2 section.

### W11. Stage 3 (branch 3, from 2)

`replay_worker/` from `afk/2026-09-25-replay-pass6-worker` (`fee18db`), carried by hand onto the current
condenser (its `test_replay_isolation.py` edit merged by hand); the web side: upload form, `POST
/replays/upload`, job page and status, `app/services/replay_upload.py` (stdlib `urllib`, store on
completion, the stuck-job rule), the invite code, size/magic/rate limits, the nav link; `render.yaml`: the
`replay-worker` pserv (written, not applied) with a provisional `plan:` from the Windows peak × 2 (a tier 2
card: no container measurement without Docker), `REPLAY_WORKER_URL` via `fromService`,
`REPLAY_UPLOAD_CODE` `sync: false`. Limits per decision 10 from the six files (60-91 MB): cap max(80 MB,
2 × largest), timeout max(180 s, 3 × the slowest local parse), and a check that parse + condense fits the
10-minute stuck-job rule. The worker's temp dir is set explicitly (`REPLAY_WORKER_TMP`) and ≥ 3 GB free is
checked before the local run. Design: Stage 3, decisions 8-10. Depends on W10.
Check: `PY -m pytest T` passes (incl. text file, over-cap and 11th-upload refusals, hung-parse kill, empty
temp dir); locally, `server.py` with the local parser build parses one competitive `.vrf`, and its blobs are
byte-identical to the local ingest's (on Render the comparison is on decoded JSON: review n4); the worker's
temp folder is deleted afterwards.

### W12. Stage 4 (branch 4, from 3)

`app/services/replay_view.py` (alive count per team at any t from the blob plus `db_deaths`; annotations from
`state_replay.replay_round` for included rounds, excluded rounds with their reason; the round's stored
`impact_scores`), the viewer's badge and annotation panel, `scripts/reingest_replays.py` write mode. Design:
Stage 4, amended by R3. Depends on W11.
Check: the Stage 4 gate tests (badge cases, annotations equal `replay_round`, round Impact equals
`get_round_detail`, the amended import gate, `reingest_replays.py --dry-run` cases) pass.

### W13. Final

Full suites: `.venv313` and `.venv` on branch 2's tip, `.venv313` on 4's tip, serially, each compared with
the `origin/main` baselines (review S5); branch 3's replay tests; on every branch `git diff origin/main --`
over `app/scoring`, `requirements.txt` and every HASHED_SOURCES path is empty; the design plan's "Status and
next steps" rewritten; the cluster stopped and its folder deleted. Check: no new failing IDs; invariants hold.

### B1. The user's steps, in order

Merge 1c; merge 2 (its deploy applies 0012 to both DBs); the first prod ingest of each of the six through
`with_friends_db.py` (not read-only); then Stage 3's code and service. **Warning (review S9):** if the
Blueprint auto-syncs, merging Stage 3 creates the paid `replay-worker` service at once. Then
`REPLAY_UPLOAD_CODE`, then Stage 4. One tier 3 decision entry carries the exact commands.

## Stop rules

- A step that fails its check three times is `blocked (failed)`; dependants wait, independent steps go on.
- If W11 or W12 can't be built well on the unmerged stack, stop there and report (R1).
- Disk: delete every export or scratch folder the run creates once its check is recorded; never the six
  exports, their bundles or the archive.

## Review

P4 review (2026-09-27, fresh subagent): 2 blockers (B1 hashed scoring sources, B2 the split inside the store
transaction), 11 should-fix, 5 nits; all applied above. No open blockers.
