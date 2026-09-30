# Map control Stages 4-7: implementation plan

Source of truth: `docs/replay-map-control-plan.md` (Stages 4-7, "Cell states", "Per-player control", "Heatmaps",
"Map geometry", "Delivery", the Stage 3 settled list). Settled for this build (AFK run 2026-09-30, register):

- **R1.** Stage 4 builds, it doesn't tune: no rule or constant changes, no `CONTROL_REVISION` bump. It ends with a
  shortlist of candidate rounds for a later tuning session with the user.
- **R2.** Stage 6 is a local tool (a polished successor to Stage 0a's tagger) that writes a `tags.json` the user
  copies to `app/static/data/control/`, then rebuilds geometry with `scripts/build_control_geometry.py`.

What exists (Stages 0-3, merged): stored rows in `replay_round_control` (`data`: the served bytes; `summary`: gzip
JSON with per-section totals and per-player stats), the formats and reference decoder in
`app/replays/control_format.py`, `GET /replays/{uuid}/{n}/control.bin`, and `match.control` on the page. Prod has ok
rows for all 224 rounds of the 10 replays: `data` 146-223 KB average per round (max 393 KB), `summary` 37-56 KB
(max 94 KB). One Ascent round: 5,302 cells, 319 ticks, 1.5 MB of raw streams.

## Branches

All in the run's one worktree (`.worktrees/afk-2026-09-30-map-control`), one branch per stage:

| Branch | Base | Holds |
|---|---|---|
| `afk/2026-09-30-map-control` | `origin/main` | this plan, Stage 4 |
| `afk/2026-09-30-map-control-5` | Stage 4's branch | Stage 5 (it shares `replay.js`, `_player.html` and the harness with Stage 4) |
| `afk/2026-09-30-map-control-6` | `origin/main` | Stage 6 (geometry and a local tool only) |
| `afk/2026-09-30-map-control-7` | `origin/main` | Stage 7 (one card) |

Merge order: 4, then 5 (retarget to `main` first), 6 and 7 in any order.

## Shared rules for every step

- The web app never imports `app.control` (numpy/scipy); everything new under `app/services` and `app/routers` is
  stdlib plus the DB (`tests/replays/test_control_isolation.py` stays green).
- Every new route 404s in demo mode (through `_replay_or_404`), and for a map without the layer.
- No change to `app/control/engine.py`'s or `geometry.py`'s constants (the pin in `test_control_format.py` stays
  green); no `CONTROL_REVISION` bump.
- Signed values (control, redundant control) get an explicit +/- (`Replay.signed`); red/green stay semantic.
- JS stays build-free ES5-style like `replay.js`; its pure functions are exported and tested in node
  (`tests/replays/test_replay_viewer.py`'s `run_node` pattern; skipped without node).

## Stage 4: the layer and the player table

### S4.1 The per-player summary endpoint

- **Files:** `app/services/replay_control_views.py` (new), `app/routers/replays.py`,
  `tests/replays/test_control_views.py` (new).
- `load_round_summaries(db, replay)`: one query for the replay's ok rows' `round_number`, `fingerprint`,
  `summary`; unpacks each summary with `cf.unpack_summary` (a bad one is skipped and reported as `failed`).
- `player_tables(summaries, stale)`: per round, each slot's `alive_s`, `control_m2` (control m²·s / alive s),
  `active_m2`, `passive_m2`, `active_ratio`, the live deaths' lost control (`control_m2`, `share_of_team`,
  `by_level_m2`, `went_to_m2`), and each team's redundant control as an average over the live round
  (`redundant_m2s / (t_decided - t_start)`), keyed by side group. Per match: sums of m²·s over rounds divided by
  the summed alive seconds; the active ratio from the summed integrals ("—" when both are zero); lost control as
  the number of live deaths, their summed m² and the mean share; redundant as summed m²·s over summed live
  seconds. A round's live seconds are the sum of its summary's section seconds (the engine's own live window;
  `t_decided` can be null). Rounds without an ok row are listed as `missing` and left out of the match numbers.
- `GET /replays/{uuid}/control/players.json`: 404 in demo mode, for an unknown replay, and when the map has no
  layer (`{"status": "no_map"}`), the blob is too old (`old_blob`) or the replay isn't linked (`unlinked`); else
  200 JSON `{"rounds": {n: {...} | {"status": "missing"}}, "match": {...}, "stale_rounds": [n...]}` with
  `Cache-Control: private, no-cache` and an ETag over the rows' fingerprints and computed times, the current
  stale set and a views version (so a geometry or link change that makes rows stale isn't hidden by a 304).
- The new service and router imports are checked by AST in `test_control_isolation.py`: nothing from numpy,
  scipy, PIL or `app.control` (numpy is already loaded by the web app for fight-EV, so the runtime check can't).
- **Check:** `pytest tests/replays/test_control_views.py tests/replays/test_control_isolation.py -q` passes,
  covering: the per-round averages, the per-match sums (two synthetic rounds with known numbers), a round with a
  null `t_decided`, a missing round, a stale-set change busting the ETag, the demo-mode, no-map and unlinked
  404s.

### S4.2 The JS decoder

- **Files:** `app/static/js/replay_control.js` (new, global `ReplayControl`, `module.exports` for node),
  `tests/replays/test_control_viewer.py` (new).
- `parse(buffer)` takes the **un-gzipped** bytes (the browser removes the `Content-Encoding: gzip` layer; the
  harness and the node tests gunzip the stored `data` themselves): MAGIC, version (refuses one it doesn't know),
  header, the three streams as `Uint8Array` views, and the tick times in seconds (`header.ticks` are 1/`hz` s
  units). `walkCells(header)`: the flat cells. `tickAt(times, t)`: the last tick at or before t (or -1).
- `Cursor(parsed)`: `states(i)` returns tick i's codes (`Uint8Array`), decoding forward from the current tick or
  from the nearest checkpoint at or before i (seek); `mask(stream, slot, i)` does the same for one slot's
  coverage or control mask (it walks the other slots' varints).
- **Check:** `pytest tests/replays/test_control_viewer.py -q` passes: the JS decoder matches
  `cf.decode_states`/`cf.decode_masks` on every tick of the `test_control_format.py` round (engine-computed), in
  order and in a shuffled seek order, for states and for one slot's two masks; a wrong magic or version throws.

### S4.3 The layer in the viewer

- **Files:** `app/static/js/replay.js`, `app/templates/replays/_player.html`, `app/templates/replays/replay.html`,
  `app/static/css/style.css`, `tests/replays/test_control_viewer.py`.
- A "Map control" toggle (`data-replay-layer="control"`), off by default and remembered like the others, shown only
  when the page's `match.control` is set; beside it the "cover not reviewed" badge when `cover_reviewed` is false,
  and a status line: loading, "control not ready for this round" (202), "control failed for this round" (422),
  "computed from older inputs" (`X-Control-Stale`).
- `options.loadControl(n, signal)` returns a promise of `{status, buffer, stale}`; the page's version fetches
  `control.bin` with an `AbortController` signal. A DOM-free `ControlCache(loadControl, limit)` in
  `replay_control.js` keeps at most 3 rounds of parsed control (current, previous, next), aborts a fetch whose
  round is no longer kept, and drops a not-ready or failed answer so returning to the round asks again; the viewer
  prefetches the next round only while the layer is on. The painters live in `replay_control.js` too.
- Drawing: the current tick's states are painted into an offscreen 512 x 512 canvas (4 px a cell) only when the
  tick index, the round or the highlight changes, then drawn over the map and under abilities, players and cones.
  Team colours come from the side group's team (the linked players' `side` -> `team`), falling back to the side
  colours. Active 55%, safe 30%, passive 15%; contested: diagonal stripes of both teams' colours, at 55% for
  `contested_active` and 30% for `contested`. The painter is a pure function over an RGBA array (tested in node).
- A legend row for the layer (the four levels and contested), shown only while it is on.
- **Check:** `pytest tests/replays/test_control_viewer.py -q` passes with 0 skipped (node is installed): the
  painter's pixel for each state code (colour and alpha), stripes alternate within a contested cell's row,
  non-walkable pixels stay transparent; `ControlCache` holds at most 3 rounds, aborts an evicted fetch, and doesn't
  keep a 202 answer.

### S4.4 The player table and click-to-highlight

- **Files:** `app/static/js/replay.js`, `_player.html`, `replay.html`, `style.css`,
  `tests/replays/test_control_viewer.py`.
- A "Control" tab in the side panel (linked, layer maps only), with a Round / Match switch. Columns: player,
  control (average m² while alive, signed), control now (this tick's `control_m2`, round view only), lost at death
  (m² and % of the team), active and passive coverage (average m²), active ratio; one redundant-control row per
  team (signed). The numbers come from `options.loadControlPlayers()` (the S4.1 endpoint), fetched once when the
  tab or the layer is first opened.
- Clicking a player's row, or their dot on the map, highlights them: their control cells filled in their team
  colour at 60% and their coverage cells outlined lighter; clicking again, or Escape, clears it. The highlight
  loads the round's control even while the layer is off, and draws on its own.
- **Check:** node tests pass: `controlTable(players, view)` gives the averages and signs; the highlight painter
  fills control cells and outlines coverage-only cells.

### S4.5 The local preview harness, and a look at real rounds

- **Files:** `scripts/render_replay_standalone.py`, `scripts/export_replay_preview.py` (new),
  `tests/replays/test_replay_viewer.py`.
- `export_replay_preview.py <uuid> --out <dir>` (read-only, run through `with_friends_db.py --read-only`): writes
  the replay's round blobs (`N.json.gz`), control rows (`N.control.bin`), the page context (`context.json`), the
  players JSON and the heatmap JSON (S5.1, once built) to a folder outside the repo.
- `render_replay_standalone.py --blobs <dir>` picks those files up when present: linked mode from `context.json`
  (names and player IDs: the page is written only under `%TEMP%`, never the repo; the docstring says so), and
  `loadControl` / `loadControlPlayers` / `loadHeatmap` from the inlined files (control bytes inlined gunzipped);
  `replay_control.js` inlined. The page-data merge (`withSiteData`) moves from `replay.html` into `replay.js` so
  both pages share it.
- **Check:** the existing standalone tests still pass, plus one with a synthetic control file; then one real
  replay exported and rendered, screenshotted with Playwright (`.venv`), and the screenshots inspected for: the
  layer under players, stripes on contested cells, the table filled, a highlight. Seek time on the real round's
  data in node: a cold seek to the last tick under 100 ms.

### S4.6 Candidate rounds for tuning (R1)

- **Files:** a read-only script in the run folder; the list in the run folder (`REVIEW-ROUNDS.md`), not the repo.
- Reads every stored summary and blob, and picks 8-10 rounds that between them cover: all six maps, both sides
  winning, a post-plant, a retake, heavy utility (smokes, flashes, a camera or drone), an early death and a
  lurker (a high control value far from teammates), each with why it's interesting.
- **Check:** the list exists with 8-10 rounds, all six maps, each with a reason and its link path.

## Stage 5: the match heatmap

### S5.1 The heatmap endpoint

- **Files:** `app/services/replay_control_views.py`, `app/routers/replays.py`, `tests/replays/test_control_views.py`.
- `match_heatmap(summaries, view, group_team)`: sums each section key's totals over the rounds (`r0`, `r1`, ... by
  the round clock; `p0`, ... by the spike clock), plus `all`. Each round's side groups are mapped to attack/defense
  (its `group_side`) or to team 1/team 2 (`group_team`, from the replay's players). Per section: its summed
  seconds and, per walkable cell, three shares: side X holds it (any level), side Y holds it, contested; nobody is
  the rest. Shares are sent as base64 bytes (0-255). A cell index is a position among that round's walkable
  cells, so each round's `walk` bitmap is read from its data header (`cf.unpack_data`); rounds are grouped by an
  identical `walk`, the newest group is used, the others are skipped and counted, and the `walk` is sent. Decoding
  is pure Python over millions of varints for a replay, so each round's decoded section totals are cached
  in-process (LRU) by its fingerprint and computed time; the side/team mapping is applied per request.
- `GET /replays/{uuid}/control/heatmap.json?view=side|team`: the S4.1 statuses and ETag; 400 for another view
  (checked by hand).
- **Check:** `pytest tests/replays/test_control_views.py -q` passes: two synthetic rounds with opposite
  `group_side` sum into the right attack/defense shares; the team view maps by `side_to_team`; `all` equals the
  sum; a round with the same cell count but different cells is skipped; a bad view is a 400. On the real replay
  exported in S4.5, a cold heatmap build takes under 3 s locally (else the tier 2 fallback in Risks).

### S5.2 The heatmap on the page

- **Files:** `replay.js` (or `replay_control.js`), `_player.html`, `replay.html`, `style.css`,
  `tests/replays/test_control_viewer.py`.
- A "Match heatmap" section under the player (linked, layer maps only), loaded when first opened: a canvas of the
  minimap with the chosen share painted per cell; pickers for the view (attack/defense, the default; team 1/team
  2), what to show (the side ahead in each cell: its colour, opacity by its share; or one side's hold; or
  contested) and the time section (whole live round, then each 10 s by the round clock, then each 10 s after the
  plant); a note of the rounds and seconds it covers.
- **Check:** node test of the heatmap painter (a cell's colour and alpha for each mode); screenshot of the real
  replay's heatmap inspected.

## Stage 6: the drawing page (a local tool, R2)

### S6.1 Paints in the geometry

- **Files:** `app/control/geometry.py`, `scripts/build_control_geometry.py`, `tests/replays/test_control_geometry.py`.
- Besides `see_across_paint`, a map's `tags.json` entry may carry `cover_paint` (blocks sight and isn't walkable),
  `cant_walk_paint` (not walkable; sight unchanged) and `uncertain_paint` (no effect on the masks; a note of where
  the 2D map is doubtful). All are the same 256 x 256 bit format. Cover paint joins the `cover` term, so
  see-across can't open it; can't-walk paint only subtracts from `walk`. No new module constants (the pin stays), so
  existing maps' masks are byte-identical, and a map whose paint changes goes stale through its mask hashes in the
  fingerprint, the same as a new tag.
- `build_control_geometry.py` adds `paints` (which are present) and `uncertain_px` to a map's index entry only when
  it has any, so existing entries don't change.
- **Check:** `pytest tests/replays/test_control_geometry.py -q` passes with new tests for each paint's effect on
  sight and walk; with the main checkout's `webapp\.venv313` interpreter, `scripts/build_control_geometry.py`
  over every map first on the unmodified base (baseline: `git status --porcelain app/static/data/control` empty),
  then after the change, still leaves it empty.

### S6.2 The tagging page

- **Files:** `scripts/control_tagger.py` (new), `scripts/control_tagger.template.html` (new),
  `tests/replays/test_control_tagger.py` (new).
- `control_tagger.py [--out <dir>]` builds one self-contained local page (default under `%TEMP%`, never the repo)
  from every map's minimap, the candidate shapes (`geometry.candidates` with the entry's params), the current
  `tags.json` and the Risk 1 kill lines (`tests/fixtures/control/kill_lines.json`).
- On the page: pick a map; tag candidates (cover, see-over, walkable, glyph, see-across; untag); paint and erase
  see-across, cover, can't-walk and uncertain zones with a brush; toggle overlays (the resulting sight and walk
  masks, candidates, kill lines, each paint); the Risk 1 blocked share, live against the 2% bar, with the blocked
  lines drawn; undo; a "cover reviewed" tick per map; the changes since the loaded `tags.json`; export the whole
  `tags.json` (every map, unknown fields kept). Edits are kept in the browser until exported.
- The tool embeds Python-computed base masks (opaque, glyph) and the candidate label map as packed bits, so the
  page never reads colours from a canvas (colour management would change them); its JS only composes tags and
  paints onto them, the same way `geometry.masks` does.
- **Check:** `pytest tests/replays/test_control_tagger.py -q` passes with 0 skipped: the page builds; for Abyss
  (tags and see-across paint) and a synthetic entry with each paint, the page's composition function (run in node
  on the embedded data) equals `geometry.masks` bit for bit; the export of an unchanged page equals the loaded
  `tags.json` data. Then a screenshot of the page inspected.

## Stage 7: the /stats placeholder

### S7.1 The "Coming soon" card

- **Files:** `app/templates/_map_control_card.html` (new), `app/templates/stats/_stats_sections.html`,
  `tests/test_site_stats_page.py` or a new `tests/test_map_control_card.py`.
- A card after the map side card: "Map control by map", coming soon, with the sample-size caveat (positions exist
  only for uploaded replays, one or two per map) and that per-player control numbers are coming too. Wrapped in
  `{% if not demo_mode %}`.
- **Check:** the new test passes: the rendered sections contain the card when `demo_mode` is false and don't when
  it is true (`demo_mode` is a Jinja global copied at import, so the test sets `templates.env.globals`).

## Review

P4 (a fresh reviewer, 2026-09-30): no blockers; 15 should-fix and nit findings, all applied above (null
`t_decided`, the heatmap's `walk`, the tagger's embedded base masks, a DOM-free cache, gzip layers, heatmap cache
and timing bar, ETag staleness, the demo-mode Jinja global, AST isolation, unlinked replays, the geometry baseline,
harness privacy, hand-checked view, check wording).

## Wrap-up checks

- Full suite on `.venv313` on the Stage 5 branch (it holds 4 and 5), compared by name with the known failures
  (`test_default_persistence_path_is_unchanged_legacy`, `test_runtime_default_is_the_live_legacy_formula`,
  `test_impact_version_is_unchanged_on_this_branch`, plus any environmental ones also failing on the base); Stage 6
  and 7 branches run their own new tests plus `test_control_isolation.py` and `test_control_format.py`.
- `docs/replay-map-control-plan.md`'s Stages 4-7 get a short status line each.

## Risks

- **Decode cost in the browser:** a round's raw streams are ~1.5 MB; a seek decodes from a checkpoint (every
  2.5 s), a few hundred ticks at most. Measured by the node test's timing on the real round in S4.5.
- **Heatmap cost on the server:** pure-Python decoding of a replay's totals takes seconds; the in-process cache
  covers repeat views, and the section only loads when opened. If it's too slow on the real replay, store a
  per-replay aggregate at compute time instead (a later change, tier 2).
- **Chrome screenshots time out on these pages:** use Playwright on the local harness page instead.
