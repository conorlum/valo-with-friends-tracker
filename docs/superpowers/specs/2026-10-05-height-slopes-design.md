# Map heights: slopes, stairs and a lower bar (design)

Status: draft for the owner's review, 2026-10-05. Nothing here is built.

Companion: `2026-10-05-height-auto-rebuild-design.md` (the automatic rebuild). That spec depends on this
one: an unattended rebuild is only safe with rules the owner trusts without looking.

Background: `2026-10-01-control-heights-design.md` (parts 3 and 4), `webapp/app/control/height_build.py`,
`webapp/app/control/heights.py`. The viewer used to judge the result: `webapp/scripts/height_viewer.py`.

## The problem

No map reaches the readiness bar today, and the owner's reading of the Sunset viewer (2026-10-05) is that
the bar is measuring the wrong thing:

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
| O5 | Compute heights for what there is now, rather than waiting for every map to reach 60%. |

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
- the two differ by at most `2 x STEP_UP_M` (1.4 m), i.e. the cell would be within a step of both;
- then the cell takes the mean of the pair. If several pairs qualify they must agree within `FILL_TOL_M`,
  or the cell stays `neighbours disagree`.

It never interpolates across a bigger difference, so a real drop stays a drop. A filled cell still never
fills another.

### 4. The asset says how each cell got its height

Add `kind` (GRID x GRID, int8) to the asset: 0 no height, 1 stands, 2 walks only, 3 filled flat,
4 filled along a gradient. `supported` stays and means kinds 1 and 2. `HEIGHT_VERSION` goes to 2; no
height asset is committed today, so nothing has to be migrated.

The viewer shows the kind in the hover and gets a layer that marks kinds 2 and 4, so the owner can see
exactly which cells the new rules produced and judge them by eye.

### 5. The bar

Proposed, **needs the owner's yes** (O5 says to go below 60%, but not what replaces it):

- A map's heights may be turned on when it has at least **2 matches**, the **kill-line check** passes
  (at most 2% of real kill lines blocked) and the **must-block check** passes.
- The supported share and the unresolved areas are still computed and shown, but no longer refuse a
  build. An unresolved cell keeps today's flat sight and walking, so a map with gaps is never worse than
  it is now in those cells.
- `HEIGHT_SUPPORTED_MIN` and `UNRESOLVED_MAX` stay in the report as "quality" numbers.

On match count and kill lines, with today's data and today's rules, this would turn on Sunset, Haven
and Ascent, and leave Lotus (one match, fails kill lines) and Summit (one match) off. Their must-block
results were not read for this draft and could still refuse one of the three.

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

## Out of scope

- A tool to set heights by hand. The owner's 2026-10-01 decision ("heights only, no paint tool") stands.
- Sloped cells inside the engine. A cell still has one flat height per floor; a staircase is a run of
  small steps, each within `STEP_UP_M` of the next, which the engine already walks and sees across.
- Storing heights anywhere but the committed file, and rebuilding automatically: the companion spec.

## Open questions

1. Decision 5 (the bar): confirm or change.
2. The stored sample rate, and whether an acceleration test is possible at it (first task; see 1).
3. Whether a stand on a slope should count as a walk sample or keep its own median. Proposed: its
   samples join the band like any others, so the low end decides.
