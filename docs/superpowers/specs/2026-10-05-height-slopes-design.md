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
  horizontal movement and without acceleration; in the air z follows an arc. The rule is two limits over
  the run: slope `|dz| / |dxy|` at most `SLOPE_MAX`, and vertical acceleration at most `WALK_ACC_MAX`.
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

Not yet verified: that each of these casts is actually recorded in `util` (an updraft places nothing in
the world). The first task checks that on real rounds with a Jett and a Waylay, and reports any that
are missing.

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
   unresolved cells by reason, cells per kind, kill lines blocked, must-block results.
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

## Out of scope

- A tool to set heights by hand. The owner's 2026-10-01 decision ("heights only, no paint tool") stands.
- A landing as something that locates an enemy (like footsteps do). Section 5 only stops the unknown
  spreading over a high ledge; it doesn't add a new way to hear someone.
- Sloped cells inside the engine. A cell still has one flat height per floor; a staircase is a run of
  small steps, each within `STEP_UP_M` of the next, which the engine already walks and sees across.
- Storing heights anywhere but the committed file, and rebuilding automatically: the companion spec.

## Open questions

1. Whether the movement-ability casts are recorded in `util` (first task; see 1b).
2. The stored sample rate, and whether an acceleration test is possible at it (first task; see 1).
3. Whether a stand on a slope should count as a walk sample or keep its own median. Proposed: its
   samples join the band like any others, so the low end decides.
