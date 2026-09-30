# Replay map control: plan

Status: design settled with the user (grilling, 2026-09-29, Q1–Q72). Q53–Q68 come from the second review round,
which also dropped the outline sight mask, the deeper-line rule and the three-way concuss classifier. No code yet.
Branch `map-control`, worktree `../vwft-map-control`. Several rules are a first draft that the user will judge "in
action" (Stage 4).

## What the feature is

1. **Replay layer.** A "Map control" toggle on the replay viewer, next to names, abilities, tracers and cones. It
   paints the map, moment by moment, with what each team controls, what is being fought over, and what is
   inactive.
2. **Per-player control.** How much space each player controls (what the team would lose if they died), how much
   they cover, and how much their team loses when they die. See [Per-player control](#per-player-control).
3. **Match heatmap.** For one replay, the share of time each area spent in each state.
4. **/stats card.** A "Coming soon" placeholder for now. The per-map aggregate across all replays is a stretch
   goal. Sample size is the known problem there: positions exist only for uploaded replays (10 today, one or two
   per map).

**Stretch to-do, only after the main view works fully:** a "what the team knew" toggle. It would compute control
from enemies the team had seen or revealed, plus fading last-known positions, instead of true positions.

## Knowledge model

Control is computed from **true enemy positions**, the omniscient analyst view. The replay knows where everyone
is.

**Who is alive (Q68).** The replay's own alive intervals, plus the deaths only the DB knows about (spike, fall,
self and team kills), merged by the viewer's existing rule (`services/replay_view.py:37–56`, `SAME_DEATH_S`). The
replay wins where both have a death. The link data is then an input to control and is fingerprinted with it.

## Vision

Control is about vision, not about who can get somewhere first.

### Active and passive vision

**Active (held) cone.** Its width depends on how the player is moving, derived from speed over about 0.25 s:

| Movement | Speed | Active cone |
|---|---|---|
| Running | > 5 m/s | +/-2 degrees |
| Walking (and crouch-walking) | 1.5-5 m/s | +/-5 degrees |
| Holding | < 1.5 m/s | +/-10 degrees |

- A fast turn (over about 90 deg/s) gets the running cone while it lasts.
- Jumping and falling would count as running, but the replay stores no height, so they aren't detected (see
  [Known gaps](#known-gaps)).
- The speeds, cone widths and turn rate are tunable constants.

**Passive vision.** The rest of the 103-degree screen. It sees enemies but does not hold the angle.

**Range.** Sight has no distance limit. It runs until a blocker.

### Blockers

- **Walls** come from the minimap's alpha channel, and **cover** from hand-tagged shapes on top of it (see
  [Map geometry](#map-geometry)). Both are 2D only: no elevation.
- **Corner tolerance (Q72).** Sight runs between bodies, not centre points. A line that crosses less than about
  **0.3 m** of wall or cover in total (half a player's width, a tunable constant) counts as clear. A peek shows part
  of the body past a corner while its centre is still behind it; in Stage 0a the only two blocked non-wallbang kills
  on flat ground clipped a corner by one pixel (about 14 cm).
- Smokes block sight into and through them.
  - Most smokes are **hollow**: a player inside sees out to the smoke's edge from the inside, and nobody sees in
    from outside.
  - **Brimstone's smokes are solid.**
- Viper's walls block sight. Harbor's would, but they aren't parsed yet (see [Known gaps](#known-gaps)).

## Cell states

Every walkable cell (about 1 m; 0.96 m on Abyss, 1.12 m on Ascent, 1.32 m on Bind, so m² always uses each map's
scale from `maps.json`) is in one state at each tick.

**Per team, four levels:**

| Level | What it is | Drawn as |
|---|---|---|
| Active | Inside a player's held cone | Team colour, about 55% opacity |
| Safe | Behind the team's lines | Team colour, about 30% |
| Passive | Passive vision, trips, turrets, cameras, scouts | Team colour, about 15% |
| None | Nothing | No paint |

**Contested** is a separate state with no single team colour, drawn as diagonal stripes in both teams' colours.
It is drawn at the active intensity when either team has a cone on the cell, and lighter otherwise.

**How it's drawn.**
- Team colours are team 1 / team 2, the same as the player dots.
- The layer draws **under** players, cones and utility.
- It is **off by default**, and the last choice is remembered (the existing `data-replay-layer` and localStorage
  pattern).

### When both teams claim a cell (Q40, Q55)

**Any cell both teams claim, at any level, is contested**, passive against passive included. A defender looking
out from site and an attacker looking toward site, with a wall between them, contest the ground in between: whoever
walks into it gets seen. This is a starting point: the user will judge it in action.

Measured on 8 rounds each of Split, Haven and Ascent (walls only, 1 Hz): cells in both teams' vision average
2.6–4.2% of the map (p90 6–9%), 15–18% of what either team sees. About three-quarters of them also have a cone on
them, so they draw at the active intensity. Tagged cover will shrink this.

### Safe space

A team controls the space behind its lines: where an enemy would have to cross the team's vision (active or
passive) before they could shoot someone standing there.

1. **Enemy free space.** Flood-fill from each alive enemy through walkable cells the team does not watch.
   Passive vision stops the fill too, because crossing it means being seen.
2. **Safe.** The walkable cells that no cell of the enemy's free space can see.

- **Smokes and walls** are applied at this step, at compute time. The static cell-to-cell visibility bitsets
  alone would see straight through them. The first method is the frontier approximation (Q70, see
  [Settled after the rewrite](#settled-after-the-rewrite-q69q71)).
- **All enemies dead:** nothing is left to fill from, so **the whole map is the team's**. The layer paints it; the
  stats have stopped by then (Q66).
- **When a holder dies**, the space that depended only on them loses cover on that same tick, unless a teammate or
  utility still covers it.

### An enemy in the team's vision (Q56)

An entry standing in the team's vision is fighting for space. Their fill still starts from where they stand, and
they contest:

1. **What they see.** Their vision against the team's claims. The both-claim rule (Q55) already makes this
   contested.
2. **The way back to their team.** The shortest walking route from them to their own team's free space (or to
   their nearest teammate if they have none), through the cells the team watches, widened by about 2 m (a tunable
   constant). Those watched cells become contested: the entry is holding that lane open.

Their own free space is not a defender claim by definition, so contesting it would change nothing. The user will
judge both parts in action.

### Contested: the holder keeps the line, but it is being fought over

A holder's line is contested while any of these is true:

- an enemy has sight of the holder;
- both teams watch the line but can't see each other (a hallway watched from both ends);
- damage utility is on the holder (mollies and similar). The holder can stand in it and choose to die, and the
  angle stays live because they can re-peek;
- wallbang damage hits the holder, for **2 s per hit** (a tunable constant). The replay's `IsWallPenetration`
  flag on `MulticastNotifyDamage_Point` rows gives this. Wallbangs that miss aren't in the data;
- suppression, slow, hinder, tether, decay or fragile is on the holder.

### Statuses on the holder

| Status on the holder | Effect |
|---|---|
| Concussed or stunned | Downgraded to passive while it lasts. See below. |
| Revealed | Downgraded to passive while it lasts. See below. |
| Nearsighted | Vision only inside an 8 m-wide bubble centred on the player. |
| Flashed | No vision at all, active or passive, for the victim's recorded blind duration (`initial_duration_seconds` on `valorant_flash_player_hit`). |

**Concuss and reveal (Q42, Q57).** The holder keeps their line, **downgraded to passive** for the status's
duration. Nothing else is special-cased. A player who flinches into a run gets the running cone from the
movement rules, and a player who backs off loses the space through ordinary line of sight. The tracks show motion,
not intent, so an intent classifier (flinch / back off / tank) was dropped. The user will see how it plays.

**Status durations.** Continuous statuses already carry their recorded start and stop (`extras.py:752–755`), and
suppression uses 8 s (`STATUS_KNOWN_MS`, `extras.py:702`). Only one-shot statuses with no known duration fall back
to the 1 s placeholder (`STATUS_PING_MS`). Those, concusses among them, get Riot's documented values as named
constants.

**Flash duration.** The duration already encodes how badly the victim was flashed, and matches BlindManager to
within about 3 ms:
- turned away: about 2% of the maximum, so it barely registers;
- a full blind: 1.5 s for Phoenix and Yoru, 2.25 s for Breach, KAY/O and Skye.

No thresholds are needed.

**Brief events (Q58).** Control is sampled at 16 Hz. An event shorter than a tick (a 30 ms flash) is snapped to
the nearest tick and counts as one tick. Against a 10 s heatmap section that is at most 0.6%, so no event-boundary
recompute.

**The cells a holder lost (Q55).** Loss is judged for the **team**. A cell a teammate or utility still covers
stays as it was. A cell the team really lost goes to the enemy if the enemy now watches it. Otherwise it is
**contested** for as long as the status lasts, because the enemy used utility to take that space.

## Passive watchers

All of these give passive control.

| Watcher | What it covers |
|---|---|
| Trips (Cypher trapwire, Killjoy alarmbot, Deadlock sensor) | Their line or area, from placement until shot and destroyed, or until their owner dies. Going off does not use them up (`gone` in #91). |
| Killjoy turret | A **100-degree** cone (180 degrees before patch 8.0) along its recorded yaw. The turret doesn't move but sometimes turns onto targets. |
| Cypher's camera | **Full** 103-degree passive vision along the camera's recorded yaw, **only while Cypher is in it**. |
| Sova's drone | **Full** passive vision from the drone's position and yaw, only while flown. |
| Tejo's Stealth Drone | A 60-degree cone out to 18 m, while flown. |
| Skye's Trailblazer | A 90-degree cone out to 22.5 m, while possessed. The 90 degrees is a guess to tune; no official angle exists. |
| Fade's Prowler | **Not modelled.** The replay has no pawn for it, and Fade steers it from her own view. |

**Owner's own view.** While a camera or drone is in use, the owner's own view does not count. The in-use
intervals come from `PossessedCharacter` on the player state switching to the camera or drone pawn and back.
`condense.py` already reads these (`PlayerTable.possession`) but doesn't yet put them in the blob (see
[Inputs the blob lacks](#inputs-the-blob-lacks)). The camera's yaw changes only while it is in use, so it keeps
its last facing after Cypher leaves.

**Utility after its owner dies (Q61, Q71).** Placed utility dies with its owner: trips, the turret and the camera
stop counting at the owner's death. The user is sure of this. The only utility that outlives its owner is a
throwable already in flight (a flash or stun that pops after the thrower died). Stage 0a confirms it in the exports
as a data check; any surprise goes back to the user.

**Scouts** (drone, dog, Tejo's drone) are passive until they concuss or reveal someone. That status then acts on
the victim as above.

## Per-player control

Display-only. The stats may feed Impact some day, but not now, and only through
`docs/superpowers/SCORING-RELEASE-PROCESS.md`.

### Control: what the team would lose (Q49, Q54)

**The space a player controls is what the team would lose if that player died right now.** It is a
counterfactual: the tick as it is, against the same tick with that player dead.

- **"Dead" removes (Q61)** their body's vision (active and passive), the camera or drone they are in, and their
  placed utility. Utility already in flight stays. Their presence as a threat to the enemy goes too.
- **Both teams are recomputed.** The team's fill changes because the cells only that player watched open up. The
  enemy's fill changes because that player is no longer a source for it, so the enemy's safe space can grow.
- **Signed scoring (Q62).** Each cell scores ours +1, contested 0, nobody 0, theirs −1. Control is the drop in the
  team's total. So ours→theirs costs 2, ours→contested or ours→nobody costs 1, and contested→theirs costs 1. This
  is what gives a lurker their value: most of it is denying the enemy's safe space.
- **No terminal flip in a counterfactual (Q63).** The "all enemies dead makes the whole map ours" rule only paints
  the real state. When removing a player empties their team, only the cells their team loses count, not the
  enemy's gain: the last player controls only what they control.
- **Redundant cover is worth zero.** If two players hold the same angle, removing either loses nothing, so neither
  is credited with it in control. The player table shows the team's redundant control as its own number, so the
  totals add up.

The plan's examples:
- A player rotating through spawn who dies (say to a Sova ult) costs the team nothing, so they controlled nothing.
- A player killed out on the edge of the map by a lurker was controlling a lot and gives it all up. The lurker's
  control is high because removing them would give the team back its safe space.

**Lost control** is the same counterfactual at the tick of a real death, reported in m² and as a % of the team's
control. It is split by level (active, safe or passive) and by where the space went: to the enemy, to contested or
to nobody.

**Rate.** The user wants the counterfactual **every tick**. It is built for 16 Hz with the incremental
counterfactual (see [Compute](#compute)). If Stage 0b measures it at more than about 2x the base engine, it falls
back to 2 Hz (Q69). Lost control is always exact at real deaths.

### Coverage: what the player watched (Q60)

Tracked beside control, answering a different question.

- **Active:** their held cone. **Passive:** their passive vision plus their own live utility (trips, turret,
  camera, drone).
- **Shared cells are split evenly** among the teammates covering them, however far apart they stand. Cells only
  one player covers are theirs. (The earlier deeper-line rule is dropped.)
- **Safe space** isn't coverage. It belongs to the whole team's flood fill and reaches players only through
  control.

### How "active" a player is (Q50, Q66)

Per round and per match:
- active m²·s and passive m²·s of coverage, and control m²·s, each divided by the seconds alive: the player's
  average m² while alive;
- the **active ratio**: ∫active ÷ (∫active + ∫passive) over the same time, shown as "—" when both are zero.

**Only the live round counts:** from `t_start` (the round starts) to `t_decided` (the round is won). Nothing from
the buy phase, and nothing after the round is decided. Since placed utility dies with its owner, nothing accrues
after death.

**Stretch:** "space taken", the cells this player's vision flipped from the enemy or nobody to their team.

### Where it shows (Q51, Q65)

- The replay viewer: a per-round and per-match player table beside the layer, with control, lost control,
  coverage (active and passive), the active ratio and the team's redundant control.
- Clicking a player highlights their **control** cells, with their **coverage** as a lighter outline.
- /stats: per-player numbers later, as a "Coming soon", with the same sample-size caveat as the map card.

## Map geometry

### Why not the drawn lines (Q39, superseded by Q53)

The minimap PNG (`app/static/img/maps/<Map>.png`, 1024x1024 RGBA) draws boxes, crates and pillars as light-grey
outlines that the alpha channel doesn't have. But the drawn lines also mark elevation steps, ramps, railings,
windows, ledges, door glyphs, spawn circles and markers. Two independent checks measured it (second review round,
2026-09-29):

| Map | Alpha only | Alpha + all outlines | Closed shapes only |
|---|---|---|---|
| Summit | 0% | 17% | 10% |
| Sunset | 2.3% | 20% | 12% |
| Haven | 0–0.6% | 22–28% | 18% |
| Split | 0–2.5% | 33–40% | 20% |
| Ascent | 3.3–3.4% | 42% | 26% |
| Abyss | 19–20% | 48–52% | 28% |

(Share of kill lines blocked. One check used every gun kill; the other 60 lethal non-wallbang hits per map with
`IsWallPenetration=false`.)

Closed shapes aren't reliably solid either: 15–25 per map contain player positions, some for minutes (platforms
and boxes people stand on). Bind's teleporter lanes are opaque saturated green, so taking the alpha as-is would
make them walkable corridors across the void.

### Masks (Q53)

- **Sight mask:** alpha 0 (walls), plus the shapes a person has tagged as **cover**. Nothing blocks until it is
  tagged.
- **Candidates:** closed shapes and outlines are detected automatically and offered for tagging as **cover**,
  **see-over** (half-cover you can shoot over), **walkable** or **glyph**.
- **Glyphs** are excluded by colour and saturation (Bind's green lanes, markers).
- **Traversal mask:** separate from the sight mask. Alpha 0 means both "wall" and "void" (for example Abyss's
  drops, which can be seen across but not walked). Colour glyphs are removed. Positions observed in replays fill
  in where the mask is wrong.
- **Map specials.** Hand-listed where 2D lies: Bind's teleporters, one-way drops, ropes, doors.

### Which maps get the layer (Q59, Q67)

- A map gets the layer only once **one of its replays passes** the kill-line test (Risk 1). Maps with no replay
  (today Bind, Breeze, Corrode, Fracture, Icebox, Lotus and Pearl) wait for their first upload, which is also their
  validation data.
- A map that passes on walls alone, with its boxes not yet tagged, shows a **"cover not reviewed"** badge. Its sight
  sees through every box, and a wrong choke changes safe space across the whole region, not only near the box.
  The badge goes once its candidates are tagged.

### Grid

The model runs on a grid over the square minimap (the replay's `u`/`v` space, 0..10000).

- **Sight lines:** raycast on the full 1024 mask, or downsample it with "any wall blocks". Majority-vote
  downsampling opens many false sight lines.
- **States** are stored on a **128 grid** (about 1 m).
- **Cell-to-cell visibility bitsets** are built at 128. A 256-grid bitset is 38–97 MB per map, which isn't
  feasible.

## Data

### Recording rate

`blob.hz` is **125**, not 16 (`condense.py:673,1110`); the "16" in `format.py:5` is only an example. Control is
computed and stored at **16 Hz**, subsampled from the 125 Hz tracks (Q36).

### Inputs the blob lacks

Each needs a condenser change, a `CONDENSE_REVISION` bump and a re-ingest that the user runs. They land before
Stage 2 consumes them.

- Flash and nearsight per-target durations: `initial_duration_seconds` on `valorant_flash_player_hit`;
  nearsight's `configured_duration_seconds` on its hit rows. Also the **hit time**: a blob `flash` or `nearsight`
  row's `t` is the cast (`read_util` keeps only the cast row's time), so a flash's blind starts at an unknown pop.
- Camera and drone in-use intervals (`PlayerTable.possession`).
- Camera and turret yaw over time. Pawn paths today are x/y only, one point per 100 ms (`PATH_STEP_MS`,
  `extras.py:433`; built at `extras.py:1017–1021`).
- Wallbang hits: `IsWallPenetration` on `MulticastNotifyDamage_Point`. Only bullet hits carry it;
  `MulticastNotifyDamage_Base` rows don't. About 5% of kills are wallbangs (93 of 1,716 in 11 exports).
- General damage and molly hit times.

Inputs from outside the blob: the link's side mapping and DB-only deaths (Q44, Q68). Control's fingerprint
includes them.

### Data details

- `valorant_flash_exploded` locations are sometimes in metres, not centimetres.
- The blob's top-level `plant` is null. The plant time comes from the Bomb utility row.
- Turret cone: 100 degrees since patch 8.0.
- A track sample can sit at the image edge (one Ascent killer at about (905, 1022) px). The Risk 1 harness filters
  those.

### Sides (Q44)

Assume replays are **linked** to a tracker.gg match; attack/defense comes from the match. Unlinked replays (blob
`side` is A/B, sometimes null) aren't handled yet.

### Known gaps

Handled one at a time, as they come up (Q45):

- **Jumping and falling:** no height is stored.
- **Harbor's walls:** Harbor isn't in any archived replay, so his objects aren't parsed (`extras.py:691`).
- **Breach and Astra concusses:** unproven in the exports.
- **Fade's Prowler:** no pawn in the exports.

## Compute

### Per tick, at 16 Hz

For each team T against enemy team E:

1. **Blockers at t.** Walls and tagged cover, active smokes (hollow or solid), and Viper's walls.
2. **Each alive player's vision.** Movement class and active cone, passive vision, and statuses applied (flash,
   concuss, reveal, nearsight). Cameras and drones in use replace their owner's view.
3. **Passive watchers active at t.**
4. **Safe space.** E's free space by flood fill, then Safe(T) from the visibility bitsets, with smokes and walls.
5. **Contest and loss rules**, cells both teams claim, and the entry's way back.
6. **One state per walkable cell.**
7. **Per player:** coverage (shared cells split evenly) every tick, and control by counterfactual at the rate the
   user picks.

### Cost

Estimates, not measurements, on one CPU core and without smokes:

- **Base:** about 8 ms per team per tick for the flood fill plus the bitset OR at 128, and 15–40 ms for vision (a
  naive numpy raycast of 10 full views measured about 40 ms). That is 51–166 s per 100 s round at 16 Hz.
- **Counterfactual, full recompute:** 10 players × 2 fills (both teams) ≈ 160 ms per tick; the vision is reused.

| Counterfactual rate | Per round | All 10 replays (~230 rounds) |
|---|---|---|
| 16 Hz | 5–7 min | about 20–27 h |
| 2 Hz (the rest at 16 Hz) | 1.4–3.3 min | about 5–13 h |
| 1 Hz | 1.1–3 min | about 4–12 h |

- Rounds are independent, so this parallelises: on 8 cores the full set is about 3 h at 16 Hz, one new replay
  about 20 minutes.
- **Incremental counterfactual.** Keep a per-cell watcher count and, per player, the cells only they watch. If
  those don't touch E's free-space frontier, T's fill is unchanged; if the player shares a fill component with a
  teammate, E's fill is unchanged; otherwise extend the base fill from its frontier and OR only the newly free
  cells. Most players on most ticks then cost little. Opening a key choke or removing a last seed can still need a
  full fill, so Stage 0 times the worst cases too.
- **Smokes** are likely the largest cost: the OR of free-space rows can't subtract a smoke, so the pairs crossing
  a smoke need rechecking (about 17M pair checks per smoked tick, naively).
- **Tuning.** A `CONTROL_REVISION` bump recomputes everything. Stage 4 tunes on a handful of rounds the user
  knows, and recomputes the full set only when a rule settles.

### Where it runs (Q37)

- **A local command the user runs**, like the upload Impact refresh. It is never part of the upload job, so a
  control bug can't break uploads. Uploads are stored during the status poll (`services/replay_upload.py:164`),
  and the worker (`replay_worker/server.py:197`) has no DB credentials.
- The viewer shows "control not ready" until the command has run for that replay. A durable job queue is a
  possible later step.
- **Its own command**, not `reingest_replays.py`. It works for local and uploaded replays alike, from the stored
  `replay_rounds` plus the link data, and never re-parses the exports.
- The definition of control will change even though the replays don't. Control has its own recipe
  (`CONTROL_REVISION`), separate from `CONDENSE_REVISION`.
- One implementation feeds the layer, the per-player stats and the heatmaps.

### Storage

A separate table, `replay_round_control`, keeps the round blobs inside their 70 KB budget.

- **Fingerprint.** Each row fingerprints its inputs: the blob recipe, the link data (sides, DB-only deaths), the
  geometry and its tags, the parameters and `CONTROL_REVISION`. A mismatch means recompute.
- **Deletes.** `ON DELETE CASCADE` from the replay, plus an explicit delete in `store.py:_delete`.
- **`data`.** gzip of the per-tick states at 16 Hz. Ticks after the first are stored as changes only, with **seek
  checkpoints**. The user accepts about 150–400 KB per round (Q36).
- **Per-player masks.** Each player's control cells (at the counterfactual rate) and coverage cells, for the
  click-to-highlight, with their own checkpoints. They count toward the 150–400 KB.
- **Per round, also stored.** Per-cell time totals by state, per heatmap time section and side, and the
  per-player stats, so heatmaps and tables never replay the ticks.

### Delivery

- A separate endpoint, a sibling of `/replays/{uuid}/{n}.json`, with gzip and ETag, and a retryable "not ready"
  response.
- The viewer caches and prefetches round promises (`replay.js:581`). The control fetch needs cancellation, seek
  reconstruction from checkpoints, and cache bounds.

## Heatmaps

- **What they show.** The share of time each cell spent in each state.
- **Time sections.** 10 s sections by the **round clock** up to the plant, then 10 s sections by the **spike
  clock** after it.
- **Excluded time (Q66).** Only `t_start` to `t_decided` counts: no buy phase, and nothing after the round is won
  (running out the clock). The layer still shows it.
- **Match heatmap.** A toggle between attack/defense (the default) and team 1/team 2.
- **/stats.** A "Coming soon" card now. Filling it later will split by attack/defense only. It will use the same
  Friends/All Players rules as the rest of `/stats`: friends from friendships, never from
  `tracked_players.json`. It needs a minimum-sample rule when it's built.
- **Demo site (Q46).** The replay viewer is off there, but `_stats_sections.html` is shared. Hide the Coming soon
  card in demo mode.

## Settled after the rewrite (Q69–Q71)

1. **Counterfactual rate (Q69).** Build for 16 Hz and try the incremental counterfactual in Stage 0b. If it costs
   more than about 2x the base engine, fall back to 2 Hz, with deaths still exact. No new question to the user.
2. **The smoke method (Q70).** Start with the **frontier approximation**: recheck smoke-crossing sight only from
   the edge of the enemy's free space (the cells next to the team's vision), not its interior. Stage 0b compares
   it with the exact recheck on one round. If the difference is noticeable (about 1% of cells or more), move to
   per-smoke shadows.
3. **Utility after its owner dies (Q71).** The user is sure: everything placed dies with its owner. Only
   throwables already in flight still pop. Stage 0a confirms it in the exports as a data check, not a decision.

## Open items

None. Stage 0 measures; the fallbacks above are already agreed.

## Risks

1. **The 2D map has no elevation.** Heights, half-walls you can shoot over and boost spots aren't drawn; see-over
   tags cover only some of it.
   - *Test:* for every **non-wallbang** bullet kill, check that the sight mask's line from killer to victim is
     clear at the kill tick. Wallbangs and ability kills are excluded using the replay's own flag, not the
     killfeed. Non-wallbang shot hits are a second check.
   - *Bar:* **2% or less blocked per map.** Every tag must keep its map under the bar.
   - *Blind spot:* the test catches false blocking, not missing cover (an empty mask passes). Tagging covers that
     side.
2. **Cost.** Sight lines at 16 Hz, smokes and the per-player counterfactual. Measured in Stage 0b.
3. **Storage at 16 Hz**, with per-player masks. Measure. If it's far over 150–400 KB per round, go back to the
   user.
4. **Rules are a first draft.** The cone widths, the dog's angle, the contest rules, "both teams claim is
   contested", the entry's way back and its 2 m width, and the concuss-and-reveal downgrade all get tuned on rounds
   the user knows.
5. **New inputs force re-ingests.** Each input the condenser has to extract bumps `CONDENSE_REVISION`, and the
   user runs the re-ingest.

## Stages

Each stage is a PR, or a commit on this branch, with tests. A stage that changes stored control output bumps
`CONTROL_REVISION`.

0a. **Geometry feasibility (Q64).** No product code.
   - Alpha sight and traversal masks for every map, with colour glyphs removed, rendered over the minimap.
   - Automatic cover candidates, rendered, with player-position counts inside each.
   - A **minimal tagging page**: tag each candidate cover, see-over, walkable or glyph.
   - The Risk 1 test on all 10 replays with the alpha mask, then with a few Ascent boxes tagged: blocked share by
     map and area.
   - Whether trips, the turret and the camera stay active after their owner dies.
   - How often a replay's side is null.
   - **Gate:** the user reviews the geometry report before 0b.
   - **Done 2026-09-29.** Tooling in `webapp/scripts/control_feasibility/`; tags in its `control-tags.json`. All six
     maps with replays pass on walls: Haven 0%, Summit 0.3%, Ascent 0.4%, Split 0.4%, Sunset 0.5%. Abyss passes at
     0% with the user's see-across tags and edge paint, cleaned by `clean_paint.py`. What's left blocked is two
     corner peeks (hence Q72) and three shots over an edge from 1.8–4 m higher or lower (the known 2D limit).
     Placed utility stays in the replay after its owner dies, but Cypher's trips applied nothing after his death
     (51 statuses while alive, 0 after), which fits Q71. No null sides in 1,370 player-rounds. Early timing: the full
     counterfactual costs 1.4–2.1x the base engine per tick, without smokes.
0b. **Engine feasibility.** No product code.
   - A full per-tick prototype on one real round: safe space with smokes, the contest rules, the entry's way back,
     coverage and the counterfactual.
   - Real bytes (with per-player masks) and compute time at 16 Hz, peak memory, browser decode time, and a Render
     CPU estimate.
   - **Counterfactual timing** at 16 Hz, 2 Hz and 1 Hz, full and incremental, including the worst cases.
   - The frontier smoke approximation, its cost, and its difference from the exact recheck on one round (Q70).
   - A list of inputs the condenser does not extract yet.
   - **Gate:** the user reviews the report before Stage 1.
   - **Measured 2026-09-29, gate pending.** Tooling: `engine_proto.py`, `encode_control.py`, `report_0b.py`. Three
     rounds (Ascent r4 at 16 Hz, Ascent r23 and Summit r19 at 4 Hz), walls only. The incremental counterfactual
     costs 0.22–0.74x the base, so 16 Hz holds (Q69); the full recompute is 1.0–4.3x. About 3.5 core-minutes for an
     average round. The Q70 frontier misses up to 2.6% of cells (sight leaves the free space across wall corners
     too); seeing from the fill's whole boundary stays under 1% (open: Q73). Everything at 16 Hz is ~610 KB per
     100 s; states at 16 Hz with highlight masks at 2 Hz is ~390 KB (open: Q74). Browser decode is tens of ms per
     round. The run found flash and nearsight rows carry the cast time, not the hit (see Inputs the blob lacks).
1. **Map geometry assets.**
   - A script that builds each map's sight and traversal masks from alpha plus tags, specials, the uncertain badge
     and visibility bitsets into `app/static/data/control/`.
   - Tests: known sight lines are clear; lines through a wall or a tagged cover shape are blocked; see-over shapes
     don't block.
2. **Control engine.** `app/replays/control.py`: blob and link data in; per-tick states, per-section totals and
   per-player stats out.
   - Synthetic unit tests on toy grids, one or more per rule:
     - movement cones;
     - safe space behind a holder;
     - all enemies dead makes the whole map the team's;
     - the entry: what they see, and their way back;
     - holder death;
     - mutual-sight contest;
     - both teams watching without sight;
     - both teams claiming a cell;
     - hollow vs solid smoke;
     - molly and wallbang contest;
     - concuss and reveal downgrade to passive;
     - nearsight bubble;
     - flash duration;
     - a flashed holder whose teammate still covers the cells (no change);
     - lost cells going to the enemy or contested;
     - trip, and a trip dying with its owner;
     - turret cone;
     - camera replacing its owner's view;
     - coverage split evenly between two holders;
     - control: redundant holders score zero, signed scoring, both teams recomputed, no terminal flip when a team
       empties;
     - the spawn-rotator and lurker examples, with asserted signs.
   - Visual only: geometry quality, and whether the rules look right.
3. **Storage and command.**
   - Migration for `replay_round_control`, with the fingerprint and `ON DELETE CASCADE`.
   - The control command, local and uploaded replays, recomputing rows whose fingerprint is stale.
   - The control endpoint.
   - The user runs it on prod; Claude verifies it read-only.
4. **Replay layer and player table.**
   - The toggle, the fetch, the drawing, the "control not ready" state and the "cover not reviewed" badge.
   - The per-player table and click-to-highlight.
   - Check visually with the local harness (Chrome screenshots time out on these pages).
   - Claude lists rounds and the user picks the review set to tune the rules.
5. **Match heatmap.** Built from the stored per-section totals, with time sections and the side/team toggle.
6. **Polished drawing page.** Over the minimap: see-across, can't-walk, cover and uncertain zones, beyond the
   minimal tagging page from 0a.
7. **/stats placeholder.** The "Coming soon" card next to the map side card, hidden in demo mode.
