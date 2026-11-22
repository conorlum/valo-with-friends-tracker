# Handoff 13: Plant spots and the win rate after planting there

Read `docs/handoffs/README.md` (ground rules, shared building blocks) and `CLAUDE.md` first. Design spec for
the owner's approval before code. Companion to handoff 14 (post-plant positions): they share plant data, the map
payload and the page.

## Goal

Per map and site, a heatmap of where the spike was planted and the attackers' round-win rate after planting
there, adjusted for how many players were alive. "The default plant on Ascent A wins 58%; the open plant wins 71%
once you account for numbers."

## Data

- **Replays:** the plant's position. The blob carries the plant (`"plant"` in `app/replays/format.py`), every plant
  attempt from condense revision 32 (`player_state.plants`), and the spike's ground position (the viewer draws it).
  Use the completed plant's position.
- **tracker.gg:** check whether the match JSON the adapter reads (`app/adapters/trackergg_browserstate_source.py`)
  includes a plant location (and whether `kill_events.source_meta` holds positions). If it does, the heatmap can
  cover every match, not only replays: report this first, it changes the sample size a lot.
- **Outcomes and state:** `rounds` (planted, plant_time, outcome, defused, exploded), alive counts at the plant
  (`state_replay.py` for tracker.gg rounds, `replay_view.alive_steps` for replays), economy.
- **Expected win chance at the plant:** `app/services/util_model.py` (planted state, alive counts, time), or Impact's
  post-plant table V(a, d, t) (`app/scoring/postplant_value_table.py`). Use one, say which; the util model is
  fitted on matches without a replay, so it is out of sample for replay plants.
- **Layouts:** group by asset key (`app/replays/layouts.py`); a layout change starts a new map.

## Method (declare before measuring)

- **Spots:** cluster plant points per site. Start with a grid of about 2 m cells smoothed with a small kernel for
  the heatmap; for the table, propose named clusters (k-means or DBSCAN on positions) and let the owner name them
  ("default", "safe", "open", "behind box").
- **Win rate per spot:** shrunk toward the site's overall post-plant win rate with a beta prior (strength M
  observations, propose M = 8), like `util_model`'s parent-cell shrinkage, so a two-round spot never shows 100%.
- **Adjusted:** actual result minus the expected win chance at the plant, averaged per spot: positive means the
  spot did better than the numbers alone predicted.
- **Also per spot:** defuse attempts started, defuses stopped, time from the plant to the first defuse attempt,
  and the plant time (early vs late plants).
- **Both teams:** every replay records both sides, so strangers' plants count (unnamed); show "our team" and
  "everyone" as two views.

## Output

- A per-map page (or a tab on a map page if one exists): the minimap with plant points or heat coloured by
  adjusted win rate (diverging scale), a site selector, our team vs everyone, n shown; cells under a minimum count
  are drawn faint.
- Hover a spot: n, raw win %, adjusted %, example rounds linking to the viewer at the plant
  (`/replays/<uuid>?round=n&t=`).
- Use the viewer's map transform and minimap images (`replay.js`'s map-pixel transform, `app/static/data/maps.json`)
  so points line up exactly.

## Storage and pipeline

- A pure function from plant rows to the payload; cached per map and layout (the stats cache pattern), cleared when
  a replay links or a match is ingested.
- If tracker.gg has plant positions, extract them at ingest into a column or a small table.
- Register the page in `site_health.PAGE_ROUTES`; 404 on the demo.

## Tests

Spot clustering on synthetic points, shrinkage, the adjusted rate, the map transform round trip. Add to
`tests/suites.toml`.

## Phases and done criteria

1. Read-only: plants per map, site and layout in replays; whether tracker.gg has plant positions; a quick PNG of
   the busiest map's plant points over its minimap. Done when the owner has seen them.
2. Spots, rates, adjustment. Done when the table with n and intervals exists for every map with data.
3. Page. Done when it renders, off on the demo.

## Open questions for the owner

- Named spots or a pure heatmap.
- Minimum n before a spot is coloured.
