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
- It runs at link time (store, `link_replays.py`, and the post-crawl hook), best-effort: a failure leaves
  `replays.kill_impact` null and the link stands. It never writes `impact_scores`.
- The page shows per-kill values only when the stored split still reconciles with the current
  `impact_scores` rows (the death side, a cheap sum check); after a rescore it hides them until
  `link_replays.py --refresh-impact` recomputes the split.
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

**W3.1 Link-level gate rows on real DB rows.** `scripts/replay_gate.py` gains `--condensed <pickle>` (a
`CondensedReplay` from `build_replay_bundle.py --reuse-condensed`'s cache, or condensed afresh) and
`--db` (read candidates through `ingest_replay.load_link_candidates`, read-only). With both, every
link-level corruption row of the gate table runs on the real replay against the real `DbMatch`, with and
without decoded winners: shifted round number, identity swap, in-window order swap, changed agent, 3 s
and 0.5 s clock shifts, wrong match, crossed kill-free same-agent slots, moved spawn evidence, the DB's
last k rounds deleted, manifest-only fallback, coverage cut, and the truncation margin. Design: "Stage
1b", Gate. Depends on nothing.
Check: `PY T/test_replay_gate.py` passes (a new synthetic test drives `--db`'s code path with a
stubbed loader); `with_friends_db … replay_gate.py --condensed … --db` on each of the six prints
`0 MISMATCH`.

**W3.2 Condense-level rows on two real exports.** `replay_gate.py --export-dir <dir> --vrf <vrf>` on
Ascent and Abyss (the two that differed most in pass-6 history). About 20 min each; run one at a time.
Check: `0 MISMATCH`, the baseline row `OK` on both.

**W3.3 Findings 14-28.** A scratch script in the run folder (not the repo) reads the six cached condensed
replays, their export manifests and read-only DB rows, and prints counts only (no Subjects, no names):
14 match UUID sources agree; 15 Subjects stable (the same `player_id` has one Subject across replays,
compared in memory, never printed); 16 round index base and `WinningTeam` values; 17 excluded kill
classes; 18 clock offsets and residuals per match and round 1 vs the rest; 19 candidates, pinning,
proximity; 20 proposed backfills (count only); 21 decoded Subjects / MatchID / RoundResults; 22 pawn per
player, 125 Hz, map source; 23 the no-winners rows (from W3.1); 24 post-`RoundEnding` kills and whether
the DB has them; 25 `actor_closed` reasons; 26 surrendered matches in the DB (awarded vs played); 27
partial-bunch errors by channel class; 28 Clove revive evidence (only if a Clove is in the six).
Design: Stage 1b step 3. Check: each finding has a line in the design plan's new "Stage 1b results"
section with its evidence, or a recorded "not answerable" reason.

**W3.4 Plan update.** `docs/replay-viewer-plan.md`: "Stage 1b results" (gate output summary, findings),
decision 1 closed by 15 and 21, decision 5 amended (R3). Check: `grep -n "Stage 1b results"` hits; the
gate output is in it.

### W4. Freeze (branch 1b)

Every `PROVISIONAL(D<old>)` in `app/replays/*.py`, `scripts/*.py` and `replay.js` is decided from the
six-match evidence and re-marked `PROVISIONAL(D<freeze>)` — one grouped tier 2 approval card for the whole
set (judgment.md). Values that the evidence doesn't support changing keep their current number; any change
gets its test updated. The design plan's "Provisional values" list is rewritten as a frozen table with
n=6 and the observed margins. Design: "Provisional values"; Stage 1b gate "Limits". Depends on W3.
Check: `grep -rn "PROVISIONAL(" app scripts` shows only the freeze decision's number (and
`replay.js`'s radii, which are display-only and part of the same card); `PY -m pytest T` passes.

### W5. Competitive fixture (branch 1b)

`scripts/make_replay_fixture.py --export-dir <Ascent> --name competitive --rounds 2 --movement-step 8
--vrf <archive .vrf>` → `tests/fixtures/replay/competitive/`. Tests: the fixture condenses, scans clean
(P-h), decodes Subjects as synthetic, links against a synthetic `DbMatch` built from its own kills.
Design: "Public repo", Stage 1c files. Depends on W4. Check: `PY -m pytest T/test_replay_fixture.py`
passes; `du -sh` of the fixture ≤ 2 MB.

### W6. Stage 1c branch (branch 1c, from `origin/main`)

Carry, from 1b's tip, with `git restore --source`: the replay docs (`replay-viewer-plan.md`,
`replay-viewer-handoff.md`, `replay-pass6-impl-plan.md`, this file); `webapp/replay_parser.json`;
`scripts/{build_replay_parser.ps1, export_replay.ps1, archive_replays.py, with_friends_db.py,
vendor_map_assets.py, ingest_replay.py, make_replay_fixture.py, replay_gate.py}`;
`app/replays/{__init__,format,contract,condense,header,link,extras}.py`;
`static/data/{maps,agents}.json`; `static/img/maps/*`; `tests/replays/*` except the viewer and preview
route tests; `tests/{test_archive_replays,test_vendor_map_assets,test_with_friends_db}.py`;
`tests/fixtures/replay/**`; the `.gitignore` rules. Not carried: `build_replay_bundle.py`,
`render_replay_standalone.py`, the preview route and service, `replay.js`, templates, ability icons,
`abilities.json`, the Watch column, `config.replay_preview_dir` (Stage 2 or the local branch).
`ingest_replay.py` keeps "no writes until Stage 2". Also fixes the wrapper's interpreter path in
worktrees (1c's known fix: `with_friends_db.py` looks for `.venv313` up the tree when its own
`webapp/.venv313` is missing). Design: Stage 1c. Depends on W5.
Check (the 1c gate): `PY -m pytest T tests/test_archive_replays.py tests/test_vendor_map_assets.py
tests/test_with_friends_db.py` passes; the full suites' failing IDs equal the baselines (W13 runs them);
read-only `--dry-run` on a competitive export prints `linked`; `git ls-files | grep -iE
'\.vrf$|valo-replay|events\.ndjson$'` prints nothing outside `tests/fixtures/replay/`; `git diff
origin/main -- webapp/app/scoring webapp/requirements.txt` is empty.

### W7. Stored abilities and shots (branch 2, from 1c)

`app/replays/extras.py` (WorldPositions moved in; `util_entries(extras, n)` → the two new kinds),
`app/replays/condense.py` (`condense(..., extras_events=<path>)`, run inside `condense_export_dir`),
`format.py` (`CONDENSE_REVISION = 5`, the docstring's util kinds), tests
(`T/test_replay_extras.py`: the util envelope, determinism, no identity fields). Design: JSON v1
`util`, R2. Depends on W6.
Check: `PY -m pytest T` passes; a scratch script condenses all six (serially) and prints per-match p95
and total gzipped bytes: every p95 ≤ 60 KB and every match ≤ 1.5 MB (else the plan's quantisation step
is applied and re-measured).

### W8. Stage 2 backend (branch 2)

- `alembic/versions/0012_replays.py` + `app/models/replay.py`: `replays` (with `link_inputs`, `kill_impact
  jsonb null`), `replay_rounds`, `replay_players` (`subject uuid null`, `UNIQUE (replay_id, subject)`),
  `replay_uploads`, `players.riot_subject uuid null unique`, and the unlink trigger (Postgres only).
- `app/replays/store.py`: the advisory lock, the dedupe rule, one multi-row insert per table, link in the
  same transaction, the demo refusal (`settings.demo_mode` or `valomaths_demo`).
- `app/replays/db.py`: the linker's DB layer (`load_link_candidates` moved from `ingest_replay.py`,
  `write_link`, `link_replay(session, replay_id)`), with the backfill behind `BACKFILL_SUBJECTS`
  (default off unless finding 15 passed; a tier 2 card either way).
- `app/services/replay_impact.py` (R3, above).
- `scripts/link_replays.py` (`match_id IS NULL`, or `--uuid`, `--refresh-impact`),
  `scripts/ingest_replay.py` write path (`--replace`), `scripts/reingest_replays.py --dry-run`.
- The best-effort hook at the end of `ingest_trackergg_player.py` and `refresh_tracked_players.py`.
- DB tests on the throwaway cluster (`VALO_TEST_DATABASE_URL=…/valo_replay_test`, `tests/_postgres.py`
  rules): forced-failure rollback, same-sha no-op, the different-sha rules, the trigger, an incomplete
  round set reads as no replay, concurrent stores serialise.
Design: "Storage", "Linking", Stage 2 steps 1-3. Depends on W7.
Check: `PY -m pytest T` passes with and without `VALO_TEST_DATABASE_URL`; `alembic upgrade head` then
`downgrade -1` then `upgrade head` on the cluster exit 0.

### W9. Stage 2 front end (branch 2)

`app/routers/replays.py` (page, `{n}.json` with gzip/ETag/304, `/matches/{external_id}/replay`),
`app/services/replays.py` (valid-replay lookup, page context: players, per-round DB data, kill feed rows
with weapon and per-kill Impact, round stats), `app/main.py` wiring, `templates/replays/{replay.html,
_player.html}`, `static/js/replay.js` (+ `static/data/abilities.json`, ability icons) carried from
`replay-local-preview` and taught to build a round from a stored blob + page data, `style.css`, "Replay"
links in `matches/detail.html` and `_round_detail.html`, the Watch column in `matches/list.html` and
`sessions/detail.html` pointing at `/replays/{uuid}`. Everything 404s or hides in demo mode. Route tests
(demo 404s with rows present, gzip headers and decoded body, ETag/304, replacement, links on initial and
htmx round detail, unlinked pages show no site data). Design: "The viewer", Stage 2 steps 4-7. Depends
on W8.
Check: `PY -m pytest T` passes; a headless Playwright script (`.venv`) opens every round of every
ingested match on the cluster at 1280 px and 390 px and reports no console error and a drawn canvas.

### W10. Stage 2 gate on the throwaway cluster (branch 2)

A run-folder PowerShell script: `initdb` a cluster under `%TEMP%\valo-replay\afk-scratch\pg`, port
55432, `valo_replay_gate` database; load the schema at 0011 and `seed_data/demo_matches.sql`; copy the six
matches' rows (matches, match_players, players, rounds, round_player_stats, kill_events, impact_scores) from
the friends DB with a read-only Python script under the wrapper; row counts and checksums; `upgrade head`,
unchanged-data check; `downgrade -1` and back; ingest all six (`ingest_replay.py` against the cluster);
link-later test (delete a match, check the trigger, restore, `link_replays.py`); uvicorn with
`DEMO_MODE=true` for the 404s; a match page without a replay equal to `origin/main`'s HTML (modulo the
static version). Design: Stage 2 pre-merge gate (R6). Depends on W9.
Check: every gate line prints PASS, recorded in the design plan's Stage 2 section.

### W11. Stage 3 (branch 3, from 2)

`replay_worker/` carried from `afk/2026-09-25-replay-pass6-worker` (`fee18db`) and rebased by hand onto
the current condenser (it calls `condense_export_dir`, so it now returns the ability and shot util); the
web side: `app/routers/replays.py` upload form, `POST /replays/upload`, job page and status,
`app/services/replay_upload.py` (stdlib `urllib` client, store on completion, stuck-job rule), the invite
code in the session, size/magic/rate limits, the nav link; `render.yaml` gains the `replay-worker` pserv
(written, not applied) and `REPLAY_WORKER_URL`/`REPLAY_UPLOAD_CODE` (`sync: false`) on the friends
service. The upload limits follow decision 10 from the observed files (six `.vrf` of 60-91 MB: the cap
becomes max(80 MB, 2 × 91 MB)). Design: Stage 3, decisions 8-10. Depends on W10.
Check: `PY -m pytest T` passes; locally, `server.py` with the local parser build parses one competitive
`.vrf` and the stored blobs are byte-identical to the local ingest's (then the worker's export folder is
deleted: disk).

### W12. Stage 4 (branch 4, from 3)

`app/services/replay_view.py` (alive count per team at any t from the blob plus `db_deaths`; annotations
from `state_replay.replay_round` for included rounds, excluded rounds with their reason; the round's
stored `impact_scores`), the viewer's badge and annotation panel, `scripts/reingest_replays.py` write
mode. Design: Stage 4, amended by R3. Depends on W11.
Check: the Stage 4 gate tests (badge cases, annotations equal `replay_round`, round Impact equals
`get_round_detail`, the amended import gate, `reingest_replays.py --dry-run` cases) pass.

### W13. Final

Full suites on branch 2's tip (`.venv313`, then `.venv`), failing IDs compared with W0's baselines;
each side branch's own replay tests; `git diff origin/main -- webapp/app/scoring webapp/requirements.txt`
empty on every branch; the design plan's "Status and next steps" rewritten. Check: no new failing IDs;
invariants hold.

### B1. The user's steps, in order

Merge 1c; merge 2 (its deploy applies 0012 to both DBs); the first prod ingest of each of the six
through `with_friends_db.py` (not read-only); then Stage 3's service (a Blueprint sync) and code, and
`REPLAY_UPLOAD_CODE`; then Stage 4. One tier 3 decision entry carries the exact commands.

## Stop rules

- A step that fails its check three times is `blocked (failed)`; dependants wait, independent steps go on.
- If W11 or W12 can't be built well on the unmerged stack, stop there and report (R1).
- Disk: delete every export or scratch folder the run creates once its check is recorded; never the six
  exports, their bundles or the archive.
