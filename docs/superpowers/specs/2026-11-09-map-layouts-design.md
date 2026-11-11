# Map layouts: every replay on the map it was played on

Status: design approved by the owner 2026-11-09; revised 2026-11-09 after the AFK run's plan review
(`PLAN-REVIEW` findings 1-15, applied below; the departures from the approved text are listed at the end). Source: the
handoff `~/.claude/handoffs/2026-11-09-map-layout-changes.md` (written after Corrode, PR #197).

## Goal

Riot changes map layouts (Corrode's west side was rebuilt in 13.08; 13.08's notes also list a metal mid wall, a
raised wall and a moved plant zone). Today every map asset is chosen by map name alone, so a redraw recomputes
every stored round of that map on the new layout, height builds pool old and new positions, and the viewer draws
the new minimap under players walking through walls that were not there yet.

After this work, each replay is computed **and drawn** on the layout of the patch it was played on: its control,
timing gaps, Critical Util, Critical Moments, coach brief, callouts, chokes, heights and minimap. A map that changed
has one version of its control per layout. A redraw recomputes the replays from that patch on; older replays move
only if their own layout's heights rebuild (Section 4: at a redraw, the new patch's positions leave the old layout's
height pool inside the changed regions, so the old layout's heights rebuild once, without them).

Out of scope: any scheduled patch-notes read for layouts (the owner triggers redraws), automatic redraws, and the
demo (it never has control).

## 1. Layouts and selection

- **A layout** is one version of one map, named for display by the patch it starts from: `<Map>@base` (a map's
  first layout) and `<Map>@<NN.NN>` (e.g. `Ascent@13.10`). It owns every map asset: minimap, sight / walk / bullet /
  barrier / cover masks, chokes, tags and specials, callouts, plant zones (these live in the map-feature tags and
  `app/control/features.py`, not a file of their own), heights, `must_block` lines, and its **changed-region mask**
  (the pixels that differ from the layout before it, Section 3). Old layouts' assets are kept for good.
- **Asset key.** Every file, database row and worker task names a layout by its asset key, never by the display id.
  For `base` the asset key **is the bare map name** (`Ascent`): today's file stems, feature-artifact keys,
  `control_heights.map_name`, height manifests and worker tasks are already keyed by it, so base changes nothing.
  A later layout's key is `<Map>_<NN>_<NN>` (`Ascent_13_10`): letters, digits and `_` only, so it is safe in file
  names and URLs. The worker's map-name checks (`replay_worker/server.py` `MAP_NAME` regex, the warm-cache file
  pattern in `replay_worker/control_cache.py`) widen in part 1 to accept exactly `<Map>` or `<Map>_<d+>_<d+>`.
- **Registry:** committed `app/static/data/layouts.json`: per map, its layouts in start order, each with its
  display id, asset key, start patch (`null` for `base`), `drawn_from` (the newest patch its assets were checked
  against: `13.08` for every map today), and its changed-region mask file (none for `base`). `index.json` and
  `tags.json` stay keyed by asset key: a new layout adds its own entry beside the old one, and base's entries never
  change. Later layouts' files carry the key in the stem (`Ascent_13_10.walk.png`, minimap
  `app/static/img/maps/Ascent_13_10.png`). A map missing from `layouts.json` has its base layout only.
- **Map-level data never changes for a layout.** `data/maps.json` and `data/agents.json` feed `assets_revision`,
  which is in every parse recipe, so editing them re-parses every stored replay (`REPLAY_REPARSE_AUTO`). A layout
  takes its map's scale from `maps.json[<Map>]` through the asset key's map; no layout entry is ever added there.
- **Patch order:** parse `game_branch` with `release-(\d+)\.(\d+)$` (`++Ares-Core+release-13.08` -> `(13, 8)`);
  compare numerically (13.10 is after 13.9). One parser, used everywhere.
- **Selection** (`layout_for(map_name, game_branch)`, one function): a map with only its base layout returns base and
  never reads the branch (so test and odd branches keep computing). Otherwise a replay uses the latest layout of its
  map whose start is at or before its patch.
  - A committed layout with assets: computed on it.
  - A layout entry with a start patch and **no assets** marks a known change not yet redrawn: its replays get
    status `layout_pending`: nothing is computed for them; rounds already stored on the previous layout are kept and
    shown marked out of date, under a viewer note that the map changed in that patch and the redraw is pending.
    Optional: the owner adds one in a one-line PR when a redraw has to wait.
  - On a map with more than one layout entry, a branch that does not parse: `layout_unknown`, reported, never
    guessed.
- **The asset key is a geometry input.** `geometry_inputs` and `resolve_current_geometry`'s pinned context take an
  asset key, not a map name. For base the key is the map name, so its inputs are exactly today's and no stored
  round restales at merge. A later layout's inputs differ through its own mask hashes, and the worker echoes the
  key as the geometry's name, which `verify_result_inputs` checks. Adding a layout changes only the fingerprints of
  replays that now select it; they recompute on their own when the new layout lands.
- **One choice everywhere:** control (`replay_control.plan`, `round_control`, `map_layer`), timing gaps and their
  store and remote checks, Critical Util areas, Critical Moments, the coach brief and named areas (`app/coach`
  areas, control areas, snapshot), callouts, chokes, the viewer's minimap and bullet mask, and the gap patterns page
  (which shows one layout at a time, newest by default, with a picker when a map has more than one) all go through
  `layout_for`. `/replays` progress (`replay_progress.py`) counts `layout_pending` and `layout_unknown` beside
  `no_map`. Match and map stats stay per map.
- **The deploy gap.** A redraw PR changes assets, not the parse recipe, so a worker still on the previous image would
  look current. The worker's `/health` lists the asset keys it has and a heights protocol number; the site sends a
  layout's rounds and height builds only to a worker that names that key (and protocol), and otherwise waits at no
  cost to the round's tries, as it does for `gaps_protocol`.

## 2. Noticing a change

- **Wall signal (automatic).** For each computed round, the site measures the share of the round's 4 Hz player
  positions on non-walkable cells of the stored row's own walk bitmap, in an idle step behind uploads, and stores it
  in its own table (one row per replay round, keyed by its control fingerprint), never in control's summary (that
  would change every fingerprint). Only shares joined to an `ok` control row with the same replay, round and
  current fingerprint count. Rows without a parsed patch or an `ok` layout selection are skipped. Per map and
  layout asset key, the baseline is the pooled share (sum inside / sum total) over replay patches up to and
  including that layout's `drawn_from`. A layout without `drawn_from` or sampled baseline replays is not evaluated.
  Each later patch with at least **4 distinct replays** raises `map_layout_walls` when its pooled share rises
  **more than 0.5 percentage points** over baseline (both values PROVISIONAL(D12)). The recorded key is
  `<map>:<patch>` (for example `Ascent:13.10`), one per patch over the threshold; every pass resolves open keys
  it didn't raise. Context includes map, patch, layout asset key, replay count, share and baseline in percent
  rounded to three decimals, and the top three callout names and inside-position counts. Only a patch about to
  raise decodes blobs, at most 200 current rounds; missing area names are `unnamed`.
  A new layout whose `drawn_from` covers a patch clears its alarm; a false alarm is cleared the usual way,
  by an entry in `site_health_resolved.json`. A new
  recorded kind goes in `site_health.RECORDED_KINDS` and the card's `KINDS`, `GROUPS`, `EVENT_KINDS` (CLAUDE.md,
  "Site health"). Never in demo mode.
- **Patch notes (on demand only).** No scheduled read. The redraw session (Section 3) reads the patch's notes.
- `scripts/vendor_map_assets.py` keeps printing `superseded` when upstream's minimap changes (a manual check).

The wall signal cannot see changes a minimap does not show (a wall made metal, a raised wall, a moved plant zone),
nor a closed-off area (a new wall leaves no positions inside it). Those reach the owner through the patch notes,
read in the redraw session.

## 3. The redraw (owner-triggered)

The owner starts a Claude session ("Ascent changed in 13.10") that follows `docs/map-layout-redraw.md`, using
`scripts/map_layout.py`: the scripts in `~/rp/corrode` (`align.py`, `diff_check.py`, `build_png.py`,
`occupancy.py`) moved into the repo.

1. **Gather.** The session reads that patch's notes (`playvalorant.com`, the Map Updates section and the map's
   subsection) and saves the change text and before/after pictures to a scratch folder outside the repo; it shows
   the `map_layout_walls` error if one fired.
2. **New map picture.** The session checks valorant-api's minimap; if it differs from the vendored one, it uses
   it. Otherwise it asks the owner to paste one: a Valoplant screenshot or an in-game screenshot, the map only,
   no planner drawings. (Valoplant has no image URLs to fetch.)
3. **Fit and diff** (`map_layout.py align`, `diff`). Fit the new picture to the previous layout's minimap
   (rotation x 90, scale, shift, on floor masks); list every changed region and check each against the new
   patch's replay positions: a closed region should hold about none, an opened one many. A region the replays
   contradict is shown, not drawn.
4. **The owner decides** on an overlay page (old, new, positions, patch pictures side by side): keeps or rejects
   each region, and marks the changes a minimap cannot show (bullet: metal walls; sight and heights: raised
   walls; plant zones). These plus the kept minimap regions are the layout's changed-region mask.
5. **Build** (`map_layout.py build <Map> <patch>`): the layout's minimap in the minimap's own style (wall
   transparent, floor (118,118,118), outer 2 px white; every pixel outside the changed regions kept), its masks,
   chokes, and tags / specials and callouts copied from the previous layout and re-checked in changed regions;
   the `layouts.json` entry and the new `index.json` / `tags.json` entries (never `maps.json`); Risk 1 re-run;
   map-features fixtures and pins regenerated (`tests/replays/map_feature_legacy.py`, `test_control_features.py`).
6. **PR**, which the owner reviews and merges. On deploy, once the worker names the new key, replays from that patch
   on recompute on the new layout, the new layout's heights and the old layout's (without the new patch's positions
   inside the changed regions) build by themselves (Section 4), and the wall error clears.

## 4. Heights, migration, testing

- **Heights per layout.** Height assets are keyed by asset key: `control_heights.map_name` holds it (the bare map
  name for base, so every existing row and digest is base's, unchanged; no migration), as do `active_digests`, the
  manifest in `app/replays/height_inputs.py` and `replay_heights_remote.py`. A layout's build reads its own
  replays' positions everywhere, plus other layouts' replays' positions outside the union of the changed-region
  masks between the two layouts. For a map with one layout the manifest stays byte-identical to today's; the
  pooling and mask fields enter it only once a map has a second layout. "Rebuild every 5 uploads"
  (`REPLAY_HEIGHTS_AUTO`) counts each layout's own uploads; the first build's match minimum counts every match in the
  layout's pool, so a new layout builds at the redraw merge. `must_block` lines for a later layout live in their own
  file (`must_block.<key>.json`), so base's `must_block.json` sha never changes. The worker applies the region mask
  only behind a heights protocol number its `/health` names (Section 1, the deploy gap).
- **Migration.** Every map gets `<Map>@base` from today's assets and names; its geometry inputs and fingerprints
  equal today's, so nothing recomputes. Corrode: its current assets are the 13.08 redraw and all 5 of its replays
  are 13.08, so they are `Corrode@base`; no older layout is needed. No Alembic migration in parts 1 and 3; part 2
  adds one table (the wall shares).
- **R3 proof.** Part 1's first commit, built from the code before any change, writes a golden file per map:
  `geometry_inputs(map, None)`, the pinned geometry from `resolve_current_geometry` with no heights or feature rows,
  the chokes asset hash, the hearing hash, `height_inputs.geometry_identity`, a height manifest digest on fixed
  replays, `must_block` sha, `assets_revision()`, the callouts and a control fingerprint on a fixed recipe. Later
  commits assert the same values through `layout_for` and the base asset key, for every map in `index.json`. The
  golden file never changes in parts 1 to 3.
- **Tests** (each file added to `tests/suites.toml`):
  - patch parsing and ordering (13.10 after 13.9; unparseable branches; base-only maps never read the branch);
  - selection, `layout_pending` (stored rows kept, marked out of date), `layout_unknown`;
  - migration: base fingerprints and inputs equal the golden file for every map;
  - adding a layout restales only replays from its patch on;
  - viewer, callouts, chokes, coach areas, Critical Moments, gap patterns follow the replay's layout;
  - the worker accepts a later layout's key and refuses anything else; the site holds a layout's work for a worker
    that doesn't name it, at no cost in tries;
  - height pooling excludes other layouts' positions inside changed regions; base's manifest unchanged with one
    layout;
  - wall share, the `map_layout_walls` raise and self-clear;
  - end to end on a synthetic second layout of a test map.

## Build order (separate PRs)

1. Layouts, `layout_for`, selection everywhere, the base layout for every map, the worker's key checks and
   capability.
2. The wall signal and its Site health error.
3. Per-layout heights.
4. `scripts/map_layout.py` and `docs/map-layout-redraw.md` (absorbs the Corrode runbook from the handoff).

## Departures from the approved text (plan review, 2026-11-09)

- Base's asset key is the bare map name; later layouts are `<Map>_<NN>_<NN>` on disk and in the database (the
  worker refused `@` and `.`); `Ascent@13.10` is the display id only.
- `index.json` / `tags.json` stay keyed by asset key; `layouts.json` holds the registry only, with a new
  `drawn_from` field the wall signal compares against.
- `maps.json` / `agents.json` never change for a layout (an edit re-parses every replay).
- The worker names its layouts and heights protocol in `/health`; the site waits for it.
- Older replays move once at a redraw, when their layout's heights rebuild without the new patch's positions.
- A new layout's first heights build counts its whole pool, not only its own matches.
- `must_block` lines for later layouts in their own file.
- `layout_pending`: stored rows kept, marked out of date. `layout_unknown` only on maps with more than one layout.
- The wall share is computed on the site from stored rows, into its own table; it is a share of positions.
- The gap patterns page shows one layout at a time.
- No migration for heights (the key goes in `map_name`).
