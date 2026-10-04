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

## Risks

- **Replay JS size:** `replay.js` is 2,100 lines. The gap code lives in its own file; `replay.js` gets hooks
  only.
- **Compute time:** 106 rounds × 60-90 s / 2 workers ≈ 55-80 minutes of foreground batches. The pattern page
  is useful with fewer; the run stops at ~120 rounds.
- **Merge semantics** change what rows mean; the card shows before/after counts from the preview.
- **Real names:** standalone pages, screenshots and investigation output stay under `%TEMP%` or the run
  folder.
