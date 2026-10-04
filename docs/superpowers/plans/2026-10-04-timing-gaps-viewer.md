# Timing gaps, plan 2: stack merge, viewer layer, local site, tagger choke mode

Written 2026-10-04 (AFK run `2026-10-04-gaps-viewer`). Spec:
`docs/superpowers/specs/2026-10-02-timing-gaps-design.md` (sections 2 "Correction", 5, 8; decisions 9-16).
Plan 1 (built, unmerged): `docs/superpowers/plans/2026-10-02-timing-gaps-engine-and-detector.md`. This plan
stacks on its branch, `afk/2026-10-02-timing-gaps` (50123ea). Plan 3 (the pattern page) is
`2026-10-04-timing-gaps-pattern-page.md`.

## Goal

The owner can't judge gaps from plan 1's text list. This plan lets them look at gaps on the map, first in a
standalone page built from the existing two-round preview folder (no database), then in the real replay
viewer running locally on a copy of the local database with one map's rounds computed. Before that, it folds
the "stacked" gaps the preview showed into one row each (R1), and it checks the one gap the owner doubted.

## In scope

1. **Stack merge (R1)** in the detector, `GAPS_REVISION` 1 → 2.
2. **Gap view rows**: one serializer that turns stored-row dicts into what the viewer draws (pixel points,
   choke names and positions), shared by the endpoint and the standalone page.
3. **Viewer layer** (spec section 8): a Gaps checkbox beside the layers, the drawing, a Gaps tab with the list,
   and a plain-words legend (R4).
4. **Standalone page (R2a)**: `scripts/preview_gaps.py` gains a tick-cache mode and writes `N.gaps.json`
   into the preview folder; `scripts/render_replay_standalone.py` inlines them. The page is written under
   `%TEMP%\valo-replay\` (it names real players).
5. **Round 2's first gap (R4)**: an investigation, reported on a card; a fix only if it's in gap code.
6. **`gaps.json` endpoint** (spec section 8), with control.bin's access and demo-mode rules.
7. **Local data (R2b)**: control and gaps computed into `valomaths_gaps_view` (localhost:5433) for Ascent's
   rounds, and `run_local_site.ps1` in the run folder.
8. **Tagger choke mode** (spec section 2, "Correction"), last.

## Out of scope

The engine (`app/control/`, R5), `CONTROL_REVISION`, any shared or remote database, drawing the exposed cells
(spec stretch goal), statistical claims, merging/pushing/deploying (R6).

## Architecture and key decisions

### 1. Stack merge (R1)

R1, settled: a predicted gap whose choke sequence is a prefix of a longer sequence open for the same victim at
an overlapping time is folded into the longest one.

- **Where:** a post-pass in `GapDetector.finish`, after every gap is closed and **before**
  `backshots.add` (linking and `shot` then see the merged gaps) and `_levels`. Online merging would change
  latch behaviour mid-round; a post-pass changes only what is reported.
- **Prefix:** `a` is a prefix of `b` when `len(a) < len(b)` and `b[:len(a)] == a`. The empty sequence is a
  prefix of every sequence, so a no-choke gap folds too (literal R1). Back-shots never merge.
- **Same victim:** same `victim` and same `life`.
- **Overlap:** closed intervals `[t_open, t_close]` intersect.
- **Target:** each gap folds into the longest overlapping extension; ties by earliest `t_open`, then the
  smaller sequence tuple. Targets are resolved transitively (a target that itself folds passes its members on).
  Processing is in a fixed order, so a rerun gives the same rows.
- **What the merged gap keeps** (the provisional part; tier 2 card):
  - from the **longest** member: `choke_seq`, `seq`, `spot`, `victim_node`, `distance_m`, `angle_deg`,
    `route`, `cause`, `cause_detail`, `context` (the full route is what patterns match on; spec decision 12);
  - `t_open` = the earliest member's; `t_last_exposed` and `t_close` = the latest;
  - `qualified_s` = the length of the **union** of the members' qualifying intervals (each `Gap` keeps its
    intervals in a new `qual_spans` list, filled where `qualified_s` is added today), so overlap is counted once
    as the spec's "counted once however many cells qualified together" says; `flicker` follows from it;
  - `candidates`: union, each enemy's distance from the member they joined first; `joined`: the earliest time
    per enemy; `stood_times`: union; `stood_at`/`stood_by`: the earliest; `checked_at`: union, sorted;
  - `context["merged"]`: one `{"choke_seq", "t_open"}` per folded member, so the viewer and a reviewer can see
    what was folded (no new column, no migration).
- **Spec:** decision 10 and section 5's Identity/Latch get one sentence each saying the reported row is the
  merged one; the test "two neighbouring cells with different choke sequences giving two gaps" stays true for
  sequences that are not prefixes of each other.
- `GAPS_REVISION` = 2 in `app/gaps/detect.py` and `app/services/replay_gaps.py` (the existing pin test keeps
  them equal). Nothing is stored anywhere yet, so no row migration.

### 2. View rows: `app/services/replay_gaps_view.py`

Standard library only (no `app.control` import: `tests/replays/test_control_isolation.py`), so the web app
can use it. `view_rows(rows, map_name) -> dict`:

- input: the stored row dicts (`app/gaps/rows.py` `to_rows`'s shape, which is also the `ReplayGap` columns);
- output: `{"chokes": {id: {"name", "x", "y"}}, "rows": [...]}` where each row is the stored row plus
  `spot_xy`, `victim_xy` and, for `route_released`, `released_xy` (cell → pixel centre on the 1024-px map:
  flat cell `c` → `((c % 128) * 8 + 4, (c // 128) * 8 + 4)`; a stored cell is always flat), and `used` =
  the deepest level reached (`killed` > `shot` > `stood` > none);
- choke positions are each choke's mean cell centre from `app/replays/choke_assets.py`; a choke id missing
  from the asset shows as its number.

Pixel coordinates match the engine's (`geometry.centres`) and the stored route's points. The viewer maps them
with `x / 1024 * canvas size`.

### 3. Viewer layer: `app/static/js/replay_gaps.js` + hooks in `replay.js`

Same style as `replay_control.js`: no build step, pure functions exported as `global.ReplayGaps` and
`module.exports` so node tests can call them.

- **Pure functions:** `openAt(rows, t, showFlickers)` (predicted rows with `t_open <= t <= t_close`;
  back-shots within `BACKSHOT_SHOW_S` = 3 s after `t_open`), `listRows(rows, showFlickers)` (time order),
  `describe(row, nameOf, chokes)` (the plain-words strings for the list and the tooltip), `rearArc(yaw)`
  (the start and end angles of the rear 120°, in canvas radians).
- **Viewer hooks** (`replay.js`): `options.gaps` (truthy when the page offers the layer) and
  `options.loadGaps(n)` returning `{status, stale, rows, chokes}`; a `gaps` entry in `LAYERS` (off by
  default; remembered like the others); `drawGaps(ctx, size, hits)` called after the control layer and
  before players; a "Gaps" tab in the side panel with the list, a "show flickers" checkbox (off), and a
  status line (not computed / failed / stale). A list row click seeks to `t_open` and turns the layer on.
- **Drawing**, while a row is open at the current time:
  - predicted gap: the route as a line from where the enemy was last located to the spot, with each choke
    crossed marked by a short bar and its name; the spot as a ring; the victim's rear 120° as a translucent
    wedge from the victim's current dot (their live yaw); for `route_released`, an × on the released cell;
  - used (stood/shot/killed) solid and in the danger colour; unused dashed and in a neutral colour;
  - back-shot: the shooter's real path, each unbroken piece separately, in a third colour, with an arrow at
    the shot.
  - hover tooltips via the existing `hits` list.
- **Legend (R4):** one line per mark in plain words ("Ring: the spot behind the player an enemy could have
  come from unseen"; "Wedge: the player's back, the 120° they weren't looking at"; …), and column headings in
  the list that say what each is ("When (s after the barriers dropped)", "Exposed player", "Could have been",
  "Route (chokes crossed)", "Why it opened", "What happened"). Site copy is tier 1 (judgment.md).
- The `_player.html` partial shows the checkbox, legend and tab only when `gaps` is set in its context.

### 4. Standalone page (R2a)

- `scripts/preview_gaps.py --from-cache`: replays `N.ticks.pkl.gz` through the detector instead of running
  the engine (spec decision 14's point), prints before/after counts, and with `--write-json` writes
  `N.gaps.json` = `view_rows(to_rows(...))` beside the blobs. Without `--from-cache` it behaves as today.
- `scripts/render_replay_standalone.py` reads `N.gaps.json` when present and passes `gaps: true` to the
  partial and `loadGaps` to the viewer, inlining `replay_gaps.js`.
- The page is written into the preview folder under `%TEMP%\valo-replay\`, never the repo.
- Verified in headless Chromium (Playwright from the venv): no console errors, the layer draws (non-empty
  pixels change when toggled), a list row seeks. Screenshots go to the run folder only.

### 5. Round 2's first gap (R4)

Already known: the blob's round clock starts at `InRound` (`app/replays/condense.py` PHASE_IN_ROUND), i.e.
the barrier drop, and `t_start` is 0, so `t - rnd.t_start` is time since the barriers dropped, not buy
phase. To check, from the tick cache and the blob, at the gap's opening tick: the victim's position and
yaw, the spot's bearing and off-facing angle (must be > 120° off facing to be in the rear arc), whether the
spot is in the victim's full-circle sight, the route's points and chokes, the candidates' real positions,
and the cause's released cell and observer. The result goes on a card with the exact replay time to look at.
A wrong result in gap code is fixed (tier 2); one in the engine is a tier 3 card (R5).

### 6. `gaps.json` endpoint

`GET /replays/{match_uuid}/{round_number}/gaps.json` in `app/routers/replays.py`, behind the same
`_replay_or_404` (404 in demo mode) and the same "map has the control layer" and "blob too old" 404s as
`control.bin`, plus `unlinked` (names and sides come from the link). Body:
`{"status": "ok"|"stale"|"failed"|"not_computed", "rows": [...], "chokes": {...}, "revision": n}`.
`stale` still returns rows (as control.bin serves a stale run) and the viewer says so; `failed` and
`not_computed` return no rows. Freshness: the run's fingerprint against
`replay_gaps.gap_fingerprint(round control fingerprint, map)`. JSON with an ETag over the rows
(`_json_with_etag`). The page's context gains `match.gaps` = true when control is offered.

### 7. Local data (R2b)

- The copy database `valomaths_gaps_view` (made by start.ps1 at 0016) holds 106 Ascent replay rounds,
  match 6f12db3e among them, all with control at revision 4 (stale at 5).
- `compute_control.py --match <uuid> --round … --workers 2` in batches of about 10 rounds, foreground, each
  under 10 minutes, with `DATABASE_URL` inline for the copy; match 6f12db3e first, then the rest of Ascent up
  to ~120 rounds. Each batch's count goes in the run's LOG.md.
- `run_local_site.ps1` (run folder): starts uvicorn from the run worktree on the copy DB (port 8011), opens
  `/login` (the site's login is a name picker: the owner picks themselves), then the viewer on round 2 of
  6f12db3e and the pattern page.

### 8. Tagger choke mode

`scripts/control_tagger.py` gains a "Chokes" mode: it embeds every map's `<Map>.chokes.json`; on the page the
owner can select a choke (click its cells), rename it, delete it (tombstone, `deleted: true`), move it (drag
re-places its cells by the drag offset), and add one (paint cells); edited chokes become `source: "hand"`.
Export writes the map's chokes JSON in `choke_assets.save`'s shape, for copying over the asset. The pure
editing functions live in `scripts/control_tagger_core.js` (node-tested); merge-on-redetection is already
`choke_assets.merge`.

## Amendments from review (`2026-10-04-timing-gaps-viewer-review.md`; these override the sections above)

- **Choke hash (1):** `choke_assets.asset_hash` hashes only each choke's `id`, `cells` and `deleted`, as sorted
  JSON, so a rename changes no fingerprint. Done in S1.
- **Merged gap (2, 3):** `context.t_round` is recomputed from the merged `t_open`; `context["merged"]` lists each
  folded member's `choke_seq` and `t_open`; `checked_at` comes from the longest member only. The preview's
  before/after counts, including how many empty-sequence gaps folded, go on the S1 card. The R4 check uses the
  pre-merge row.
- **Start time (4):** `replay_page` takes `t` (seconds) beside `round`, and the page seeks to it after
  `showRound`. In S6.
- **ETag (5):** over the whole `gaps.json` body.
- **Freshness (6):** the current control fingerprint,
  `replay_control.round_fingerprint(replay, side_groups(db, replay), n)`, as `round_control` uses.
- **Imports (7):** `replay_gaps_view.py` and the router import neither `app.control` nor `app.gaps`; add them to
  `tests/replays/test_control_isolation.py`'s list.
- **Choke names (9):** hovering a list row highlights its chokes, with their names, on the map; the legend
  says names are numbers until they are renamed in the tagger's choke mode.
- **Endpoint (n2):** `1 <= round_number <= round_count` as control.bin; tombstoned chokes are left out of
  `chokes`; the `unlinked` 404 is kept (the page hides layers for an unlinked replay).

## Implementation steps

Python: `webapp\.venv\Scripts\python.exe` from `webapp\` (`PY`). Each step is one commit. "Tests pass" means
the named files, foreground, with every known base failure deselected.

**S1. Stack merge.** Files: `app/gaps/detect.py` (`Gap.qual_spans`, `merge_stacks`, called in `finish` before
`backshots.add`; `GAPS_REVISION = 2`), `app/services/replay_gaps.py` (`GAPS_REVISION = 2`), the spec (decision
10, section 5 Identity and Latch, Testing), `scripts/preview_gaps.py` (`--from-cache`: replay
`N.ticks.pkl.gz` through the detector, no engine), new tests in `tests/replays/test_gaps_detect.py`
(a prefix folds; a non-prefix stays; no overlap stays; transitive chain; two extensions → the longest, then the
earliest; union of qualified time counted once; candidates/joined/stood union; `context["merged"]`; a back-shot
links to the merged gap). Check: `PY -m pytest tests/replays/test_gaps_detect.py tests/replays/test_gaps_backshots.py
tests/replays/test_gaps_task.py -q` passes, and `PY scripts/preview_gaps.py --from-cache <preview folder>` prints
round 1 and 2 counts (before: 96 and 122 predicted), logged.

**S2. View rows.** Files: `app/services/replay_gaps_view.py` (`choke_points(map)`, `cell_xy(cell)`,
`view_rows(rows, map_name)`), `tests/replays/test_gaps_view.py`. Check: those tests and
`tests/replays/test_control_isolation.py` pass. Depends on S1 (no; independent) — may run in either order.

**S3. Viewer layer.** Files: `app/static/js/replay_gaps.js` (new), `app/static/js/replay.js` (hooks:
`LAYERS`, `options.gaps`/`loadGaps`, `drawGaps`, the Gaps tab and list, the flicker switch, seek on click,
`options.startTime`), `app/templates/replays/_player.html` (checkbox, legend, tab, panel, behind `gaps`),
`app/static/css/style.css` (gap colours and list), `tests/replays/test_gaps_viewer.py` (node: `openAt`,
`listRows`, `describe` wording names players and chokes, `rearArc`). Check: `test_gaps_viewer.py`,
`test_replay_viewer.py`, `test_control_viewer.py` pass. Depends on S2 (the row shape).

**S4. Standalone page.** Files: `scripts/preview_gaps.py` (`--write-json` writes `N.gaps.json` via `to_rows` +
`view_rows`), `scripts/render_replay_standalone.py` (reads `N.gaps.json`, passes `gaps` and `loadGaps`, inlines
`replay_gaps.js`), a test in `tests/replays/test_replay_viewer.py` or a new `test_gaps_standalone.py` (render a
synthetic folder with a gaps file: the page carries the layer). Check: the test passes; the page is written to
`%TEMP%\valo-replay\<folder>\replay-standalone.html`; a Playwright script in the run folder loads it with no
console errors, toggles Gaps (canvas pixels change), clicks a list row (the clock moves to its `t_open`), and
saves screenshots to the run folder. Depends on S1, S3.

**S5. Round 2's first gap (R4).** No repo change unless the fault is in gap code. A screenshot at round 2,
7.5 s with the layer and the unknown hatch on; the finding goes on a `DECISIONS.md` card. Depends on S4.

**S6. `gaps.json`.** Files: `app/services/replay_gaps.py` (`round_gaps(db, replay, n)` → status, rows),
`app/routers/replays.py` (the route), `app/services/replays.py` (`page_context` sets `match.gaps`),
`app/templates/replays/replay.html` (`loadGaps`, `?t=` start time, include `replay_gaps.js`),
`tests/replays/test_gaps_web.py` (ok / stale / failed / not computed; demo mode 404; no_map / old_blob /
unlinked 404s; ETag 304). Check: `test_gaps_web.py`, `test_replay_routes.py`, `test_control_views.py` pass.
Depends on S2, S3.

**S7. Local data and site (R2b).** No repo change. `compute_control.py` batches on `valomaths_gaps_view` (see
the run's hard rules), match 6f12db3e first; `run_local_site.ps1` in the run folder. Check: a read-only count
of `ok` gap runs at revision 2 for Ascent in the copy DB ≥ the rounds computed; a Playwright run against the
local site (started by the script with `-NoBrowser`) loads the viewer with the Gaps layer and no console errors.
Depends on S1, S6.

**S8. Plan 3** (its own steps in `2026-10-04-timing-gaps-pattern-page.md`). Depends on S6, S7.

**S9. Tagger choke mode.** Files: `scripts/control_tagger.py` (embed chokes), `scripts/control_tagger.template.html`
(mode UI), `scripts/control_tagger_core.js` (pure choke edits: select, rename, delete as tombstone, move by an
offset, add from painted cells, export shape), `tests/replays/test_control_tagger.py` (node tests of the edits
and that the export round-trips through `choke_assets.load`/`merge`). Check: `test_control_tagger.py` passes;
the page builds. Depends on nothing; last by R3.

**Full suite** after S3, S6 and at the end: `tests/replays` in two foreground halves, `test_control_*.py` and
the rest, each with the known base failure deselected; plus `tests/test_*` files a step touched.

## Risks

- **Replay JS size:** `replay.js` is 2,100 lines. The gap code lives in its own file; `replay.js` gets hooks
  only.
- **Compute time:** 106 rounds × 60-90 s / 2 workers ≈ 55-80 minutes of foreground batches. The pattern page
  is useful with fewer; the run stops at ~120 rounds.
- **Merge semantics** change what rows mean; the card shows before/after counts from the preview.
- **Real names:** standalone pages, screenshots and investigation output stay under `%TEMP%` or the run
  folder.
