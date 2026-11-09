# Map layouts: every replay on the map it was played on

Status: design approved by the owner 2026-11-09. Not built. Source: the handoff
`~/.claude/handoffs/2026-11-09-map-layout-changes.md` (written after Corrode, PR #197).

## Goal

Riot changes map layouts (Corrode's west side was rebuilt in 13.08; 13.08's notes also list a metal mid wall, a
raised wall and a moved plant zone). Today every map asset is chosen by map name alone, so a redraw recomputes
every stored round of that map on the new layout, height builds pool old and new positions, and the viewer draws
the new minimap under players walking through walls that were not there yet.

After this work, each replay is computed **and drawn** on the layout of the patch it was played on: its control,
timing gaps, Critical Util, coach brief, callouts, chokes, heights and minimap. A map that changed has one version
of its control per layout. A redraw recomputes only replays from that patch on; older replays never move.

Out of scope: any scheduled patch-notes read for layouts (the owner triggers redraws), automatic redraws, and the
demo (it never has control).

## 1. Layouts and selection

- **A layout** is one version of one map, named by the patch it starts from: `<Map>@base` (a map's first layout)
  and `<Map>@<NN.NN>` (e.g. `Ascent@13.10`). It owns every map asset: minimap, sight / walk / bullet / barrier /
  cover masks, chokes, tags and specials, callouts, plant zones, heights, and its **changed-region mask** (the
  pixels that differ from the layout before it, Section 3). Old layouts' assets are kept for good.
- **Registry:** committed `app/static/data/layouts.json`: per map, its layouts in start order, each with its start
  patch (`null` for `base`) and its asset hashes (what `index.json` holds per map today, moved under the layout).
  A `base` layout keeps today's file names (`Ascent.walk.png`); later layouts carry the layout in the name
  (`Ascent@13.10.walk.png`, minimap `app/static/img/maps/Ascent@13.10.png`). Heights stay in the database
  (Section 4).
- **Patch order:** parse `game_branch` (`++Ares-Core+release-13.08`) to `(13, 8)`; compare numerically (13.10 is
  after 13.9). One parser, used everywhere.
- **Selection:** a replay uses the latest layout of its map whose start is at or before its patch.
  - A committed layout with assets: computed on it.
  - A layout entry with a start patch and **no assets** marks a known change not yet redrawn: its replays get
    status `layout_pending`: nothing computed, the viewer notes that the map changed in that patch and the redraw
    is pending. Optional: the owner adds one in a one-line PR when a redraw has to wait.
  - A branch that does not parse: `layout_unknown`, reported, never guessed.
- **The layout id is a geometry input.** `geometry_inputs` and `resolve_current_geometry`'s pinned context take a
  layout, not a map name, and the layout id enters the fingerprint for every layout except `base`, whose inputs
  are exactly today's (so no stored round restales on migration). Adding a layout changes only the fingerprints
  of replays that now select it; a replay computed on the old layout before a redraw recomputes on its own when
  the new layout lands.
- **One choice everywhere:** control (`replay_control.plan`, `round_control`), timing gaps, Critical Util, the
  coach brief, callouts / named areas, chokes, and the viewer's minimap all go through one function,
  `layout_for(replay)`. `/replays` progress (`replay_progress.py`) counts `layout_pending` and `layout_unknown`
  beside `no_map`.

## 2. Noticing a change

- **Wall signal (automatic).** When control computes a round it also records the share of the round's 4 Hz
  player positions on non-walkable pixels of that layout's walk mask, stored per replay. When replays from a
  patch newer than the map's current layout land in walls well above that layout's normal share, Site health
  raises an error, `map_layout_walls`: the map, the first patch, the number of replays, and where the positions
  land. The threshold is set from today's replays (Corrode's pre-redraw image put 1,527 position pixels in walls;
  the redraw puts 4). It clears itself once a new layout covers those replays (they recompute and stop landing in
  walls); a false alarm is cleared the usual way, by an entry in `site_health_resolved.json`. A new recorded kind
  goes in `site_health.RECORDED_KINDS` and the card's `KINDS`, `GROUPS`, `EVENT_KINDS` (CLAUDE.md, "Site
  health"). Never in demo mode.
- **Patch notes (on demand only).** No scheduled read. The redraw session (Section 3) reads the patch's notes.
- `scripts/vendor_map_assets.py` keeps printing `superseded` when upstream's minimap changes (a manual check).

The wall signal cannot see changes a minimap does not show (a wall made metal, a raised wall, a moved plant
zone). Those reach the owner through the patch notes, read in the redraw session.

## 3. The redraw (owner-triggered)

The owner starts a Claude session ("Ascent changed in 13.10") that follows `docs/map-layout-redraw.md`, using
`scripts/map_layout.py`: the scripts in `~/rp/corrode` (`align.py`, `diff_check.py`, `build_png.py`,
`occupancy.py`) moved into the repo.

1. **Gather.** The session reads that patch's notes (`playvalorant.com`, the Map Updates section and the map's
   subsection) and saves the change text and before/after pictures to a scratch folder; it shows the
   `map_layout_walls` error if one fired.
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
   the `layouts.json` entry; Risk 1 re-run; map-features fixtures and pins regenerated
   (`tests/replays/map_feature_legacy.py`, `test_control_features.py`).
6. **PR**, which the owner reviews and merges. On deploy, replays from that patch on recompute on the new layout,
   its heights build by themselves (Section 4), and the wall error clears.

## 4. Heights, migration, testing

- **Heights per layout.** Height assets are keyed by layout in the database (`control_heights` rows,
  `active_digests`, the manifest in `app/replays/height_inputs.py`, `replay_heights_remote.py`). A layout's
  build reads its own replays' positions everywhere, plus other layouts' replays' positions outside the union of
  the changed-region masks between the two layouts. "Rebuild every 5 uploads" (`REPLAY_HEIGHTS_AUTO`) counts per
  layout. Existing active heights become the `base` layout's, digests unchanged.
- **Migration.** Every map gets `<Map>@base` from today's assets and names; its geometry inputs and fingerprints
  equal today's, so nothing recomputes. Corrode: its current assets are the 13.08 redraw and all 5 of its replays
  are 13.08, so they are `Corrode@base`; no older layout is needed.
- **Tests** (each file added to `tests/suites.toml`):
  - patch parsing and ordering (13.10 after 13.9; unparseable branches);
  - selection, `layout_pending`, `layout_unknown`;
  - migration: `base` fingerprints equal today's for every map;
  - adding a layout restales only replays from its patch on;
  - viewer, callouts, chokes, coach areas follow the replay's layout;
  - height pooling excludes other layouts' positions inside changed regions;
  - wall share, the `map_layout_walls` raise and self-clear;
  - end to end on a synthetic second layout of a test map.

## Build order (separate PRs)

1. Layouts, `layout_for`, selection everywhere, the `base` migration.
2. The wall signal and its Site health error.
3. Per-layout heights.
4. `scripts/map_layout.py` and `docs/map-layout-redraw.md` (absorbs the Corrode runbook from the handoff).
