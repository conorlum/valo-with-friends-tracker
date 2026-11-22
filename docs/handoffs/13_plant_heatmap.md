HANDOFF 13 — Plant spot heatmap: where you plant and how often you win

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first. Design spec in docs/superpowers/specs/ for my approval before code. Never on the demo. Every figure with n and a CI.

GOAL
Per map and site: a heatmap of where the spike was planted and the attackers' round-win rate after planting there. "Default plant on Ascent A wins 58%, the open plant wins 71%."

DATA
- Replays: every plant with the planter's position. Condensed blobs carry the plant ("plant" in app/replays/format.py; revision 32 records every plant attempt in player_state.plants). Spike ground position is drawn by the viewer (spike icon), so the plant point is in the blob. Use the completed plant's position.
- Outcome and context: rounds (planted, plant_time, outcome, end type: detonation/defuse/elimination), economy, alive counts at the plant (state_replay.py / replay_view alive_steps).
- Both teams in every replay count (strangers unnamed), which doubles the sample. tracker.gg data alone has no plant position (check: kill_events may carry positions; if plant location exists in the tracker.gg JSON, say so: that would cover every match, not just replays).
- Layouts: group by asset key (app/replays/layouts.py); a layout change resets the map.

METHOD (declare before measuring)
- Cluster plant points per site: a grid of ~2 m cells, smoothed with a small kernel, or named spots (k-means / manual named spots like "default", "safe", "open"); I'd like named spots in the end, so propose clusters and I'll name them.
- Win rate per spot, shrunk toward the site's overall post-plant win rate (beta prior, like util_model's parent-cell shrinkage), so a 2-round spot doesn't show 100%.
- Adjust for the state at the plant: a 5v2 plant wins regardless of spot. Report both raw and adjusted: adjusted = actual minus the expected win chance at the plant (app/services/util_model.py, which knows alive counts, spike and time).
- Also per spot: median defuse attempts, % of defuses stopped, how long defenders took to start the first defuse.

OUTPUT
- A page per map (e.g. /maps/<map>/plants, or a tab on a map page): the minimap with plant dots/heat coloured by adjusted win rate (diverging scale; sample size shown), a site selector, attack-side filter by our team / everyone.
- Hover a spot: n, raw and adjusted win %, examples linking to the replay viewer at the plant.
- Use the replay viewer's map transform and minimap assets so positions line up (replay.js map pixels transform; app/static/data).

WHERE IT GOES
- A pure function from plant rows to the heatmap payload, cached per map+layout (a small cache table or the site_stats cache pattern; cleared on a new replay link).
- Add suites to tests/suites.toml. Register any new page in site_health.PAGE_ROUTES.

FIRST STEP
Read-only: count plants per map/site/layout in replays, check whether tracker.gg match JSON has plant locations, and print the plant points of the busiest map as a quick PNG over its minimap.
