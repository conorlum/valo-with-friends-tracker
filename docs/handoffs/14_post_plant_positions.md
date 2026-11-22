# Handoff 14: Post-plant positions: where do players get the most value after the plant?

Read `docs/handoffs/README.md` (ground rules, shared building blocks) and `CLAUDE.md` first. Design spec for
the owner's approval before code. Shares plant data and the page with handoff 13.

## Goal

After the spike is planted: where do attackers stand when they get kills and win-chance gains, and where do
defenders retake from? Per map and site, a heatmap of positions coloured by the value players got from them.
"On Haven C, players holding from Long get 0.4 kills per post-plant and +9% win chance; players on site get 0.1."

## What exists

- Positions per tick (the blob), deaths with killer and victim positions, sight (`app/control/engine.py`), Critical
  Moments fights and kill records (`app/moments/fights.py`: who saw whom, through smoke, wall-bangs).
- Plant time and position (handoff 13).
- Win chance: `app/services/util_model.py`; Impact's post-plant V(a, d, t) table
  (`app/scoring/postplant_value_table.py`) is another, finer per-second estimate of the same thing.
- Each kill's win-chance swing, the way Critical Util's ledger computes it (`app/services/util_values.py`).
- Layouts: `app/replays/layouts.py`.

## Definitions (declare before measuring)

- **Post-plant window:** the plant to the round's end.
- **Where a player played from:** two views. (a) Kill positions: the killer's position at each kill and the
  victim's at each death. (b) Time spent: each player's positions sampled every 0.5 s, on a ~2 m grid or as
  named areas (callouts).
- **Plant-relative frame:** positions also expressed as distance and bearing from the spike, because "Long" for
  one plant spot is a different spot for another. Show both the map view and the plant-relative view.

## Measures (attackers and retaking defenders separately)

- Kills and deaths per post-plant from each cell or area; kill/death ratio from there.
- Win-chance gained by kills from there (the swing of each kill), per post-plant spent there.
- Survival: how long players holding there lived.
- Spike coverage: did the holder have sight on the spike (could they stop a defuse)? Share of time with sight.
- Crossfires: two attackers with sight on the spike from bearings more than 60° apart.
- On-site vs off-site: holding inside the site region vs outside it.
- Shrink each cell or area toward the site's mean; adjust for alive counts at the plant like handoff 13.

## Output

- A "Post-plant" tab on handoff 13's page: a heatmap of value per position, with toggles for kills, win chance and
  survival, attack vs retake, our team vs everyone (strangers unnamed), n shown.
- Per player: their post-plant positions vs the high-value ones ("you hold on site 70% of the time; Long gets three
  times the kills").
- Each cell links to example post-plant moments in the viewer.

## Storage and pipeline

- Pure functions over stored rows, cached per map and layout. Sight on the spike for every sampled position needs
  the engine: if it is too slow on the site, compute it on the worker next to Critical Moments (new fields, bump
  `GAPS_REVISION`).
- Register the page; 404 on the demo.

## Tests

Plant-relative transform, sight-on-spike on a synthetic grid, crossfire angles, shrinkage. Add to
`tests/suites.toml`; slow lane if gap-run code changes.

## Phases and done criteria

1. Read-only: on the map with most replays, every post-plant kill with killer position, distance to the spike,
   sight on the spike; counts per site; a PNG of kill positions. Done when the owner has seen it.
2. Measures with shrinkage and intervals. Done when the table exists.
3. Page. Done when it renders, off on the demo.

## Open questions for the owner

- Cells or named areas.
- Whether retakes come in the first version.
