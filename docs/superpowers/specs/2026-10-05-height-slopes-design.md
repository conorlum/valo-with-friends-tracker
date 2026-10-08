# Map heights: slopes, stairs and movement abilities (design)

Status: draft for the owner's review, 2026-10-05. Nothing here is built.

Companion: `2026-10-05-height-auto-rebuild-design.md` (the automatic rebuild). That spec depends on this
one: an unattended rebuild is only safe with rules the owner trusts without looking.

Background: `2026-10-01-control-heights-design.md` (parts 3 and 4), `webapp/app/control/height_build.py`,
`webapp/app/control/heights.py`. The viewer used to judge the result: `webapp/scripts/height_viewer.py`.

## The problem

No map reaches the readiness bar today, and the owner's reading of the Sunset viewer (2026-10-05) is that
most of what keeps maps under the bar is slopes, not missing data:

- Most small unresolved areas (1 to 10 connected cells) are stairs and slopes. The build can't give them
  a height, for three reasons that are all in the rules, not in the data:
  1. only **stands** count (0.3 s with z steady within 0.15 m), so a player running up stairs gives no
     samples at all;
  2. a cell whose floor spreads over more than 0.8 m is refused (`spread too wide`);
  3. an unsampled cell is filled only when its neighbours agree within 0.5 m, which they never do on a
     slope (`neighbours disagree`).
- Beyond slopes, the remaining gaps are places with no data yet. Those fix themselves as matches arrive.

Previews on the live rounds, 2026-10-05:

| Map | Matches | Supported | Not ready because |
| --- | --- | --- | --- |
| Sunset | 5 | 70.7% | one 13-cell `neighbours disagree` area beside a two-floor cell |
| Haven | 3 | 59.6% | supported under 60% |
| Lotus | 1 | 53.1% | supported; a 31-cell area; kill lines 4/142 blocked (fails) |
| Ascent | 2 | 42.2% | supported; a 21-cell area |
| Summit | 1 | 28.6% | supported |

## What the owner decided (2026-10-05)

| # | Decision |
| --- | --- |
| O1 | Use moving players' heights on slopes, so stairs get data. |
| O2 | Fill along a gradient: where neighbours on opposite sides differ but line up as a slope, interpolate instead of refusing. |
| O3 | Where samples of one floor disagree, the **lowest** wins: a player can't be below the floor, and anything that lifts them (an ability, a jump) is undone by the next player who walks there. |
| O4 | A cell with two floors on Sunset is not a tunnel: it is a cell that straddles a big drop (a ledge edge). Sunset has no caves or tunnels. Real overlaps exist on other maps (Haven, Fracture). |
| O5 | The 60% bar stays. It is a fine bar and maps will reach it with time. (This replaces an earlier idea of turning heights on below it.) |
| O6 | After a movement ability (Jett's updraft, a dash by Jett or Waylay), ignore that player's height for about 3 seconds. By then they have landed, on the ground or on a box, and what they do next counts as normal. Sage's wall is the outlier among abilities for making false floors; these others mostly add noise. |
| O7 | The Sunset staircase the owner marked (cells x 26-28, y 76-81) is the test case for gradient fill: 2 m of rise over 3 cells. |

## Design

### 1. Ground samples: stands and walks

Today a floor is made from stands only. Add a second kind of ground evidence, the **walk**: a run of a
player's track, at least `WALK_S` long, in which the player is moving on the ground rather than through
the air.

- A walk is told from a jump or a fall by its vertical motion: on the ground z changes in step with
  horizontal movement and without acceleration; in the air z follows an arc. As built (decision D2 of the
  plan), flights are found first, by what only a flight does (z changing faster than `SLOPE_MAX` allows, or
  z's rate dropping by most of gravity twice in a row, then followed along the arc), and the two limits
  (slope `|dz| / |dxy|` at most `SLOPE_MAX`, vertical acceleration at most `WALK_ACC_MAX`) are applied to
  what is left: one parabola fitted over a window can't tell, because a takeoff or a landing bends z the
  other way and cancels a fall's curvature.
- A flat walk is just a walk with zero slope, so walks also add samples on level ground where nobody
  stopped.
- Stands are kept exactly as they are. They remain the only evidence that can create an **upper** floor
  (see 2).
- The existing exclusions apply to walks too: samples near a live temporary platform are dropped, and
  rounds without z give nothing.

Whether acceleration can be measured depends on the stored sample rate (`blob["hz"]`). The first task of
the build is to measure that on real rounds (a known staircase and a known jump on Sunset) and report it.
If the rate is too low for an acceleration test, the fallback is the slope limit alone, with rule 2's
"lowest wins" removing what jumps leave behind. That fallback is a change to this spec and is reported
before it is adopted.

### 1b. A blackout after movement abilities (O6)

A stored round's `util` lists each ability cast with its time, its name and the slot that cast it. For
the abilities in `AIRBORNE_ABILITIES`, that player's samples are dropped from the cast until
`ABILITY_BLACKOUT_S` (3 s) later. This applies to stands and walks alike.

- Start list: Jett's updraft and dash, Waylay's dashes, Raze's blast pack. The list is a named constant
  like `PLATFORMS`, extended when a new agent needs it.
- It works beside the walk rule, not instead of it: an ordinary jump has no cast, and Raze's pack also
  lifts teammates who cast nothing. Those are still handled by the walk rule and by "lowest wins".
- A player who lands on a box and stays there is counted from 3 s on, which is right: the box top is a
  real place to stand, and the two-match rule decides whether it becomes a floor.

Checked (2026-10-05 and 2026-10-08): none of the three movement casts was recorded in `util`; only Raze's
`Clay_Q_Explosion` is (an `ability` row). The owner decided on 2026-10-07 to record them (the plan's
decision D1, its Task 1b). The condenser (revision 14) now records Jett's and Waylay's dashes as `cast` rows,
told by the effect played on the caster's character as the dash starts (43 of 43 Jett dashes in four local
matches, every Waylay dash in three). Jett's updraft has no play that can be told apart, so it is still
told by speed: a round condensed with casts reads only upward bursts, and a round condensed before it reads
every burst (`BURST_MPS` along the ground, `BURST_UP_MPS` upward). Every round stored today is of the second
kind.

### 2. Floors: the lowest of each band

Per cell, the ground samples (stand samples and walk samples) are grouped into bands as stands are
grouped today: floors of one cell are at least `FLOOR_SEP_M` (2 m) apart.

- **A band's height is the low end of its samples** (`LOW_PCT`, start value the 10th percentile), not the
  median (O3). A percentile rather than the single lowest sample, so one bad sample can't sink a cell.
- **The lowest band needs no stand.** Walks alone can make a cell's ground floor. This is what gives
  stairs a height.
- **An upper band needs stands**, as today (`FLOOR_MIN_STANDS` from `FLOOR_MIN_ROUNDS` rounds in
  `FLOOR_MIN_MATCHES` matches). A boost or an updraft passes through the air above a cell; without this
  rule it could become a floor.
- The two-match rule stays for every band: one match's wall or boost can't become a floor.
- `spread too wide` no longer refuses a band whose spread comes from walks. A slope cell is expected to
  spread; its height is the low end. The spread is still recorded.
- `floors too close` and `too many floors` are unchanged: those cells stay unresolved, never guessed.

What this does at a ledge edge (O4): the cell has stands at the top and at the bottom, so it still has
two floors, exactly as now. A player dropping off the ledge is in the air, so their samples are not
walks and don't join either band.

What stays unresolved, on purpose:

| Case | Why it can't be resolved |
| --- | --- |
| Two levels under 2 m apart in one cell (a box top, a low ledge) | the bands merge; the lower one wins and the upper is lost. Reported as `floors too close` when both have stands |
| Stairs that rise to within 2 m of a level above, in the same cell | the stair samples bridge the gap between the bands |
| A real lower level (tunnel) nobody has walked yet | only the upper band exists until data arrives |

### 3. Filling along a gradient

Today an unsampled cell takes its neighbours' median only when they agree within `FILL_TOL_M`. Add one
case, tried only when that one fails:

- the cell has known neighbours **directly beside it on opposite sides** (north and south, east and west,
  or a diagonal pair), each with a single floor;
- the slope between the two is at most `SLOPE_MAX` (rise over run 1.0: with 1 m cells, up to 2 m between
  neighbours two cells apart);
- **a player was seen crossing between the two sides on the ground**, in either direction: a walk (1)
  that runs from one side to the other. This is what tells a slope from a ledge. Coming off a ledge a
  player is in the air; going down a ramp, even one that can only be slid down, they stay on it. The
  direction they were seen going decides how the filled cell connects (see 5);
- then the cell takes the mean of the pair. If several pairs qualify they must agree within `FILL_TOL_M`,
  or the cell stays `neighbours disagree`.

A filled cell still never fills another.

The limit is a slope, not a height, because of the owner's test case (O7). On Sunset a cell is 1.0 m.
That staircase reads, west to east, 2.0 m, then 2.3-2.6, 2.8-3.1, 3.2-3.5, then 4.0 m: about 0.67 of rise
per metre. Its one unresolved cell (27, 81) sits between 2.4 m and 3.5 m. A first draft of this rule used
a fixed 1.4 m, which that cell passes, but only just; a slightly steeper stair would have been refused.

The same numbers show the stairs already resolve from stands alone when enough players have stopped on
them: every other cell of that staircase is supported today. Walk samples (1) matter for stairs nobody
stops on.

### 4. The asset says how each cell got its height

Add `kind` (GRID x GRID, int8) to the asset: 0 no height, 1 stands, 2 walks only, 3 filled flat,
4 filled along a gradient. `supported` stays and means kinds 1 and 2. `HEIGHT_VERSION` goes to 2; no
height asset is committed today, so nothing has to be migrated.

The viewer shows the kind in the hover and gets a layer that marks kinds 2 and 4, so the owner can see
exactly which cells the new rules produced and judge them by eye.

### 5. Drops, slides and the unknown (an engine change)

The owner, 2026-10-05:

- Lotus and Summit each have a ramp that can be slid down silently but not walked up. The unknown (where
  an unseen enemy could be) must spread **down** it and never up.
- A ledge must stop the unknown from going over it when the drop is high enough that landing always
  makes a sound. Start value 1 m; the owner thinks the real figure is lower.

Today the build records a connection seen only going down as one-way, and the engine lets the unknown
spread down every one-way connection whatever its height (`app/control/topology.py`). There is no noise
value stored for a player: the engine infers footsteps from speed (over 4.5 m/s within hearing range of
an opponent), and has no notion of a landing.

The change:

- **The asset says what kind each connection is.** `edges` gains a fifth column: 0 a step (both ways,
  as now), 1 a **slide** (one-way down, the player stayed on the ground: a walk went down it), 2 a
  **fall** (one-way down, through the air).
- **The engine's unknown** spreads across steps both ways and down slides, as now. It spreads down a
  fall only when the drop is at most `SILENT_DROP_M` (1.0 m). A higher fall is closed to the unknown.
- Everything else that uses connections (walking distances, connected pieces, memory, backfill) is
  unchanged: a real player still drops off any ledge.

This is a change to what a round computes, so `CONTROL_REVISION` goes up and every stored round on a map
with heights is recomputed by the idle queue. A flat map's bytes don't change (the reference test pins
them). No map has heights live today, so in practice nothing is recomputed that wouldn't be anyway.

**A high fall is always closed, whoever is or isn't in earshot** (the owner, 2026-10-05). The
alternative considered was closing it only while an opponent is within hearing range of the landing
spot, as the footstep rule does. The owner's reason for not doing that: the unknown is not a model of
what was heard. It is where an enemy could have got to while playing quietly, gun out, clearing as they
go, which is why it spreads at walking speed and not at running speed. A player moving like that doesn't
take a loud drop. Tying the rule to who can hear would be modelling perfect knowledge, which nobody has
and which helps nobody.

### 6. The bar

Unchanged (O5). A map's heights turn on when all of these hold, exactly as today:

- at least `HEIGHT_SUPPORTED_MIN` (60%) of walkable cells are supported;
- no unresolved area larger than `UNRESOLVED_MAX` (12) cells touches a cell with two floors;
- the **kill-line check** passes: of the real kills in the rounds where both players stand on resolved
  cells and the line between them is clear in 2D, at most 2% may be blocked by the heights. A kill
  happened, so the two could see each other; a height that says otherwise is wrong;
- the **must-block check** passes: a hand-kept list of sightlines that are impossible in the game
  (`control_must_block.json`), each of which the heights must block.

What changes is how many cells count: walk-only cells are supported (4), and slope cells stop being
unresolved, so both numbers should move toward the bar on every map. Sunset's one blocker is a 13-cell
`neighbours disagree` area, the slope signature; whether the new rules clear it is one of the results
reported in "How it is judged".

## How it is judged

No rule here is adopted on argument. For each of the five maps, on the live rounds:

1. Build with the old rules and the new ones, and print both reports side by side: supported share,
   unresolved cells by reason, cells per kind, kill lines blocked, must-block results. Both builds are made
   from one frozen copy of the rounds (`scripts/freeze_height_rounds.py`); each records the rounds' digest,
   and `scripts/compare_height_builds.py` refuses two builds whose digests, masks or preview overrides
   differ.
2. **The flat-ground check.** On cells that have a stand floor under the old rules, compare old height
   (median) with new (low end). They should agree within 0.2 m on almost every cell. A wide shift means
   "lowest wins" is biting on level ground (crouching lowers a player's z by up to 0.5 m), and `LOW_PCT`
   has to move up. Report the distribution, not a pass/fail.
3. The owner looks at each map in the viewer with the new-kinds layer on. Stairs should read as a run of
   steadily changing heights; ledge edges should still be two floors.
4. The kill-line share must not get worse on any map.

## Start values (all tuned on real data, each change reported with numbers)

| Name | Start | Meaning |
| --- | --- | --- |
| `WALK_S` | 0.3 s | shortest run that counts as a walk |
| `SLOPE_MAX` | 1.0 | steepest walkable slope, rise over run |
| `WALK_ACC_MAX` | to be measured | vertical acceleration above which a run is airborne |
| `LOW_PCT` | 10 | the percentile of a band taken as its height |
| `SILENT_DROP_M` | 1.0 m | the highest fall the unknown still spreads down (the owner expects this to come down) |
| `ABILITY_BLACKOUT_S` | 3 s | how long a player's samples are dropped after a movement ability |
| `AIRBORNE_ABILITIES` | Jett updraft and dash, Waylay dashes, Raze blast pack | the casts that start a blackout |

## Measured (2026-10-08)

The dataset: one frozen copy of the live rounds (`scripts/freeze_height_rounds.py`, read-only, 2026-10-08),
every round of each map at condenser revision 11 or later. Every number in this section and in "Results"
comes from it.

| Map | Rounds | Matches | `rounds` digest |
| --- | --- | --- | --- |
| Sunset | 120 | 6 | `bf72113396d33aba` |
| Haven | 102 | 5 | `21b0e37f3cf39904` |
| Lotus | 61 | 3 | `a7816868f11f77be` |
| Ascent | 105 | 5 | `44abedc00b552628` |
| Summit | 37 | 2 | `c7a7215a467a3b17` |

`scripts/measure_height_motion.py --blobs-dir <frozen folder>` (Sunset with `--rect 26,76,28,81`, the
owner's staircase):

```
Sunset: 120 rounds; hz {125: 120}; 1317/1317 segments with z
  Jett: 60 rounds; casts recorded: {'Wushu_4_Smoke': 40, 'Wushu_4_SmokeZone': 40, '_Spike': 1}
  Waylay: 60 rounds; casts recorded: {'Terra_C_TimeSlowGrenade': 35, 'Terra_C_TimeSlowGrenade_Explosion': 35, 'Terra_E_RewindTime_ForObservers': 12, 'Terra_E_RewindTime_RewindTarget': 48, 'Terra_X_DelayedBeam_Beam': 7, '_Spike': 3}
  Raze: 80 rounds; casts recorded: {'Clay_4_Projectile_Primary': 58, 'Clay_4_Projectile_Secondary': 232, 'Clay_4_Projectile_SecondarySpawner': 58, 'Clay_E_Boomba': 54, 'Clay_Q_Explosion': 44, 'Clay_Q_Satchel_Arming': 43, 'Clay_X_Rocket': 8, '_Spike': 1}
  movers: along the ground m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [3.3, 6.5, 7.8, 27.6, 44.6, 49.9] (BURST_MPS 12)
  movers: upward m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [0.0, 0.0, 4.2, 8.3, 15.6, 24.0] (BURST_UP_MPS 8)
  others: along the ground m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [3.3, 6.5, 7.2, 9.4, 12.4, 52.6] (BURST_MPS 12)
  others: upward m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [0.0, 0.0, 3.1, 5.2, 6.2, 41.7] (BURST_UP_MPS 8)
  |vertical acceleration| over 0.3 s while moving, 5438973 windows (WALK_ACC_MAX 4 m/s2):
       0 .. 1     82.68%
       1 .. 2      2.45%
       2 .. 3      1.62%
       3 .. 4      1.47%
       4 .. 6      3.77%
       6 .. 8      0.91%
       8 .. 10     0.84%
      10 .. 12     0.63%
      12 .. 15     0.66%
      15 .. 20     2.11%
      20 .. 30     2.11%
      30 .. inf    0.76%
  while moving: 92.7% of samples are a walk's, 6.7% are in a flight; 8208 flights, lasting (s) pct 10/50/90: [0.22, 0.37, 0.72]
  gravity read off the 5665 flights of 0.3 s or more, m/s2 pct 10/50/90: [-4.3, 2.9, 19.9] (GRAVITY_MPS2 20)
  cells (26, 76, 28, 81): 46622 moving samples, 88.4% are a walk's; their slope, pct 50/90/99: [0.48, 0.52, 0.58]
  steady descent candidates steeper than SLOPE_MAX going down, by cell (x, y, passes): [(61, 43, 8), (25, 57, 6), (58, 83, 5), (28, 39, 4), (29, 57, 4), (29, 39, 4), (25, 58, 4), (54, 79, 3), (27, 56, 3), (53, 80, 3), (85, 36, 3), (24, 56, 3), (24, 57, 3), (23, 59, 3), (21, 62, 3)]
```

```
Haven: 102 rounds; hz {125: 102}; 1094/1094 segments with z
  Jett: 90 rounds; casts recorded: {'Wushu_4_Smoke': 38, 'Wushu_4_SmokeZone': 38, '_Spike': 1}
  Waylay: 63 rounds; casts recorded: {'Terra_C_TimeSlowGrenade': 40, 'Terra_C_TimeSlowGrenade_Explosion': 40, 'Terra_E_RewindTime_ForObservers': 18, 'Terra_E_RewindTime_RewindTarget': 72, 'Terra_X_DelayedBeam_Beam': 8}
  Raze: 0 rounds; casts recorded: none
  movers: along the ground m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [3.3, 6.4, 7.7, 27.5, 28.7, 31.4] (BURST_MPS 12)
  movers: upward m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [0.0, 0.0, 4.2, 9.4, 17.2, 20.8] (BURST_UP_MPS 8)
  others: along the ground m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [3.3, 6.5, 7.2, 9.7, 12.1, 20.4] (BURST_MPS 12)
  others: upward m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [0.0, 0.0, 3.1, 5.2, 6.2, 10.4] (BURST_UP_MPS 8)
  |vertical acceleration| over 0.3 s while moving, 3906821 windows (WALK_ACC_MAX 4 m/s2):
       0 .. 1     82.86%
       1 .. 2      2.85%
       2 .. 3      1.69%
       3 .. 4      1.41%
       4 .. 6      3.80%
       6 .. 8      0.82%
       8 .. 10     0.69%
      10 .. 12     0.54%
      12 .. 15     0.55%
      15 .. 20     1.94%
      20 .. 30     2.07%
      30 .. inf    0.77%
  while moving: 93.0% of samples are a walk's, 6.4% are in a flight; 5421 flights, lasting (s) pct 10/50/90: [0.22, 0.39, 0.73]
  gravity read off the 3944 flights of 0.3 s or more, m/s2 pct 10/50/90: [-3.8, 16.6, 19.9] (GRAVITY_MPS2 20)
  steady descent candidates steeper than SLOPE_MAX going down, by cell (x, y, passes): [(81, 69, 7), (80, 63, 7), (58, 80, 6), (41, 16, 6), (79, 63, 6), (79, 64, 6), (58, 81, 6), (79, 65, 6), (80, 69, 5), (58, 79, 5), (58, 83, 5), (79, 66, 4), (56, 83, 4), (57, 84, 4), (56, 78, 4)]
```

```
Lotus: 61 rounds; hz {125: 61}; 665/668 segments with z
  Jett: 17 rounds; casts recorded: {'Wushu_4_Smoke': 13, 'Wushu_4_SmokeZone': 13}
  Waylay: 37 rounds; casts recorded: {'Terra_C_TimeSlowGrenade': 30, 'Terra_C_TimeSlowGrenade_Explosion': 30, 'Terra_E_RewindTime_ForObservers': 12, 'Terra_E_RewindTime_RewindTarget': 39, 'Terra_X_DelayedBeam_Beam': 4, '_Spike': 3}
  Raze: 37 rounds; casts recorded: {'Clay_4_Projectile_Primary': 26, 'Clay_4_Projectile_Secondary': 104, 'Clay_4_Projectile_SecondarySpawner': 26, 'Clay_E_Boomba': 17, 'Clay_Q_Explosion': 17, 'Clay_Q_Satchel_Arming': 18, 'Clay_X_Rocket': 1, '_Spike': 1}
  movers: along the ground m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [3.2, 6.2, 7.8, 27.8, 29.8, 32.5] (BURST_MPS 12)
  movers: upward m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [0.0, 0.0, 3.1, 7.3, 12.5, 24.0] (BURST_UP_MPS 8)
  others: along the ground m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [3.2, 6.3, 7.2, 8.5, 12.5, 58.6] (BURST_MPS 12)
  others: upward m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [0.0, 0.0, 3.1, 5.2, 6.2, 8.3] (BURST_UP_MPS 8)
  |vertical acceleration| over 0.3 s while moving, 3250450 windows (WALK_ACC_MAX 4 m/s2):
       0 .. 1     81.98%
       1 .. 2      2.46%
       2 .. 3      1.73%
       3 .. 4      1.55%
       4 .. 6      4.73%
       6 .. 8      1.10%
       8 .. 10     1.04%
      10 .. 12     0.79%
      12 .. 15     0.77%
      15 .. 20     1.62%
      20 .. 30     1.65%
      30 .. inf    0.57%
  while moving: 94.0% of samples are a walk's, 5.3% are in a flight; 4291 flights, lasting (s) pct 10/50/90: [0.22, 0.37, 0.71]
  gravity read off the 2848 flights of 0.3 s or more, m/s2 pct 10/50/90: [-4.1, 3.3, 20.0] (GRAVITY_MPS2 20)
  steady descent candidates steeper than SLOPE_MAX going down, by cell (x, y, passes): [(61, 52, 4), (70, 57, 4), (91, 71, 3), (111, 30, 3), (92, 72, 3), (20, 67, 3), (107, 36, 3), (23, 59, 2), (69, 60, 2), (23, 60, 2), (111, 28, 2), (61, 50, 2), (62, 52, 2), (91, 77, 2), (91, 79, 2)]
```

```
Ascent: 105 rounds; hz {125: 105}; 1125/1125 segments with z
  Jett: 58 rounds; casts recorded: {'Wushu_4_Smoke': 48, 'Wushu_4_SmokeZone': 48, '_Spike': 2}
  Waylay: 47 rounds; casts recorded: {'Terra_C_TimeSlowGrenade': 30, 'Terra_C_TimeSlowGrenade_Explosion': 30, 'Terra_E_RewindTime_ForObservers': 19, 'Terra_E_RewindTime_RewindTarget': 50, 'Terra_X_DelayedBeam_Beam': 5, '_Spike': 1}
  Raze: 46 rounds; casts recorded: {'Clay_4_Projectile_Primary': 42, 'Clay_4_Projectile_Secondary': 168, 'Clay_4_Projectile_SecondarySpawner': 42, 'Clay_E_Boomba': 21, 'Clay_Q_Explosion': 42, 'Clay_Q_Satchel_Arming': 43, 'Clay_X_Rocket': 5}
  movers: along the ground m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [3.4, 6.5, 8.3, 27.1, 30.4, 33.2] (BURST_MPS 12)
  movers: upward m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [0.0, 0.0, 4.2, 9.4, 16.7, 30.2] (BURST_UP_MPS 8)
  others: along the ground m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [3.2, 6.4, 7.2, 7.8, 11.8, 61.6] (BURST_MPS 12)
  others: upward m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [0.0, 0.0, 4.2, 6.2, 6.2, 42.7] (BURST_UP_MPS 8)
  |vertical acceleration| over 0.3 s while moving, 4464614 windows (WALK_ACC_MAX 4 m/s2):
       0 .. 1     82.52%
       1 .. 2      1.68%
       2 .. 3      1.23%
       3 .. 4      1.17%
       4 .. 6      3.31%
       6 .. 8      0.90%
       8 .. 10     0.87%
      10 .. 12     0.80%
      12 .. 15     0.83%
      15 .. 20     2.86%
      20 .. 30     2.75%
      30 .. inf    1.07%
  while moving: 91.1% of samples are a walk's, 8.1% are in a flight; 7332 flights, lasting (s) pct 10/50/90: [0.22, 0.39, 0.82]
  gravity read off the 5337 flights of 0.3 s or more, m/s2 pct 10/50/90: [-4.6, 16.0, 19.9] (GRAVITY_MPS2 20)
  steady descent candidates steeper than SLOPE_MAX going down, by cell (x, y, passes): [(40, 98, 10), (58, 56, 9), (67, 29, 7), (41, 100, 7), (79, 55, 7), (41, 98, 6), (41, 101, 5), (36, 12, 5), (79, 56, 5), (40, 108, 4), (43, 98, 4), (46, 27, 4), (78, 56, 4), (42, 97, 4), (63, 27, 4)]
```

```
Summit: 37 rounds; hz {125: 37}; 406/406 segments with z
  Jett: 18 rounds; casts recorded: {'Wushu_4_Smoke': 4, 'Wushu_4_SmokeZone': 4}
  Waylay: 0 rounds; casts recorded: none
  Raze: 0 rounds; casts recorded: none
  movers: along the ground m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [4.2, 6.6, 7.3, 27.7, 28.6, 29.4] (BURST_MPS 12)
  movers: upward m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [0.0, 0.0, 4.2, 9.4, 11.5, 11.5] (BURST_UP_MPS 8)
  others: along the ground m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [3.3, 6.3, 7.1, 7.4, 11.8, 34.4] (BURST_MPS 12)
  others: upward m/s, pct [50, 90, 99, 99.9, 99.99, 100]: [0.0, 0.0, 3.1, 5.2, 6.2, 41.7] (BURST_UP_MPS 8)
  |vertical acceleration| over 0.3 s while moving, 1605616 windows (WALK_ACC_MAX 4 m/s2):
       0 .. 1     87.59%
       1 .. 2      1.20%
       2 .. 3      0.95%
       3 .. 4      0.97%
       4 .. 6      2.36%
       6 .. 8      0.66%
       8 .. 10     0.62%
      10 .. 12     0.50%
      12 .. 15     0.52%
      15 .. 20     1.93%
      20 .. 30     1.99%
      30 .. inf    0.72%
  while moving: 93.2% of samples are a walk's, 6.3% are in a flight; 2288 flights, lasting (s) pct 10/50/90: [0.22, 0.37, 0.72]
  gravity read off the 1629 flights of 0.3 s or more, m/s2 pct 10/50/90: [-5.2, 1.6, 19.8] (GRAVITY_MPS2 20)
  steady descent candidates steeper than SLOPE_MAX going down, by cell (x, y, passes): [(97, 72, 3), (87, 80, 3), (60, 80, 2), (87, 81, 2), (78, 45, 2), (18, 61, 2), (63, 83, 2), (86, 83, 2), (10, 66, 2), (86, 80, 2), (60, 47, 2), (67, 76, 1), (96, 74, 1), (100, 69, 1), (87, 77, 1)]
```

Against the plan's table (Task 2 Step 8), every map carries on:

- `hz`: 125 Hz in every round of every map.
- Acceleration: the windows between 2 and 8 m/s2 are 7.8% (Sunset), 7.7% (Haven), 9.1% (Lotus), 6.6%
  (Ascent) and 4.9% (Summit), all under 10%: the ground and free fall stay two groups.
- Gravity read off the flights: the 90th percentile is 19.8 to 20.0 m/s2 on every map (`GRAVITY_MPS2` 20).
- In a flight while moving: 5.3% (Lotus) to 8.1% (Ascent), inside 2% to 15%.
- Everyone but Jett, Waylay and Raze, along the ground, 99.9th percentile: 7.4 to 9.7 m/s, under
  `BURST_MPS` 12.
- The same, upward: 5.2 to 6.2 m/s, under `BURST_UP_MPS` 8.
- Raze: `Clay_Q_Explosion` is listed on every map she was played (Sunset, Lotus, Ascent).
- Sunset's staircase (cells 26-28, 76-81): 88.4% of moving samples are a walk's; their slope is 0.48 at the
  median and 0.58 at the 99th percentile, under `SLOPE_MAX`.
- Steady descents steeper than `SLOPE_MAX` (Lotus, Summit): no cell has more than 4 passes (Lotus 61,52 and
  70,57) or 3 (Summit). Inspected pass by pass, every one is a single 0.3 s window dropping 0.3 to 2.4 m that
  the air rule already marks 61% to 90% in flight: drops off a ledge, not a sustained slide.

The old rules' builds of this copy (`build_control_heights.py --blobs-dir <frozen folder> --preview`, no
preview override), each recording its map's digest:

```
Sunset: 12748 stands from 120 rounds of 6 matches, 61 stands by a platform dropped
  visited 88.5% of 6207 walkable cells, supported 73.6% (bar 60%), filled 704, unresolved 932
  kill lines: 1/789 blocked by heights (0.1%, bar 2%): PASS; excluded: blocked in 2D (wall or smoke) 68, on an unresolved cell 22, no killer 5, no position with z near the kill 1
  must-block: 0/0 blocked, 0 not checked (no heights yet: fill them in control_must_block.json): PASS
Haven: 8386 stands from 102 rounds of 5 matches, 169 stands by a platform dropped
  visited 86.5% of 5837 walkable cells, supported 69.2% (bar 60%), filled 843, unresolved 956
  NOT READY: 1 unresolved area(s) larger than 12 cells touch a cell with two floors (largest 13 cells)
  kill lines: 3/697 blocked by heights (0.4%, bar 2%): PASS; excluded: blocked in 2D (wall or smoke) 47, on an unresolved cell 28, no killer 2, no position with z near the kill 1
  must-block: 0/0 blocked, 0 not checked (no heights yet: fill them in control_must_block.json): PASS
Lotus: 7504 stands from 61 rounds of 3 matches, 19 stands by a platform dropped
  visited 88.8% of 5643 walkable cells, supported 71.7% (bar 60%), filled 876, unresolved 721
  kill lines: 4/418 blocked by heights (1.0%, bar 2%): PASS; excluded: blocked in 2D (wall or smoke) 33, on an unresolved cell 11, no killer 4
  must-block: 0/0 blocked, 0 not checked (no heights yet: fill them in control_must_block.json): PASS
Ascent: 8491 stands from 105 rounds of 5 matches, 64 stands by a platform dropped
  visited 89.3% of 5220 walkable cells, supported 68.9% (bar 60%), filled 738, unresolved 885
  kill lines: 13/678 blocked by heights (1.9%, bar 2%): PASS; excluded: blocked in 2D (wall or smoke) 82, on an unresolved cell 26, no killer 1
  must-block: 0/0 blocked, 3 not checked (no heights yet: fill them in control_must_block.json): FAIL
Summit: 2702 stands from 37 rounds of 2 matches, 24 stands by a platform dropped
  visited 79.5% of 6031 walkable cells, supported 48.5% (bar 60%), filled 1641, unresolved 1467
  NOT READY: supported 48.5% is under 60%
  kill lines: 1/225 blocked by heights (0.4%, bar 2%): PASS; excluded: on an unresolved cell 20, blocked in 2D (wall or smoke) 19, no killer 1
  must-block: 0/0 blocked, 0 not checked (no heights yet: fill them in control_must_block.json): PASS
```

## Out of scope

- A tool to set heights by hand. The owner's 2026-10-01 decision ("heights only, no paint tool") stands.
- A landing as something that locates an enemy (like footsteps do). Section 5 only stops the unknown
  spreading over a high ledge; it doesn't add a new way to hear someone.
- Sloped cells inside the engine. A cell still has one flat height per floor; a staircase is a run of
  small steps, each within `STEP_UP_M` of the next, which the engine already walks and sees across.
- Storing heights anywhere but the committed file, and rebuilding automatically: the companion spec.

## Open questions

1. Answered: no movement cast was recorded in `util` (Raze's blast pack is, as an `ability` row). The owner
   decided on 2026-10-07 to record them (decision D1, Task 1b of the plan): the condenser now records both
   dashes; the updraft has no signal and stays told by speed, as are all rounds condensed before (see 1b).
2. Answered: 125 Hz in every stored round of the five maps (Measured, below); the acceleration test works
   at it.
3. Whether a stand on a slope should count as a walk sample or keep its own median. Proposed: its
   samples join the band like any others, so the low end decides.
