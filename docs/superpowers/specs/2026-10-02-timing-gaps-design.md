# Timing gaps from unknown space: design

Written 2026-10-02. Status: design agreed in conversation, stress-tested in four rounds of questions, and
revised after two external reviews (the code-based findings were re-checked against the source before being
accepted). Revised again after the rebase onto main, to build on the engine's own unknown. No code exists yet.

## Purpose

Find the moments in a round when a player could have been shot in the back by an enemy their team had lost
track of, and the moments when a player really was. For each, record how it came about: the route, the timing,
and what opened it. Then look for routes that recur.

This is architecture for analysing games. There is not enough replay data for statistical belief yet, and
nothing here claims any. Every count the system shows carries its sample size.

## Decisions

| # | Decision |
|---|----------|
| 1 | One detector runs over every round for both teams. "Enemy gaps on a map", "my team's leaks" and "one opponent in one match" are filters on one table. |
| 2 | A gap's target is always an enemy player's back. Sites and the spike are not targets. |
| 3 | Two kinds of row are recorded: **predicted gaps** (unknown space behind a player) and **back-shots** (a player really shot from behind by an untracked enemy). One kind may be pruned later. |
| 4 | Unknown space is the engine's unknown, which spreads at rifle-out shift-walk speed (`UNKNOWN_MPS`, 3.24 m/s). |
| 5 | "Behind" is the rear 120 degrees: within 60 degrees either side of directly behind the player. A predicted gap also needs a clear line to the player; a back-shot does not, so wall-bangs count. No range limit; the distance is stored. |
| 6 | Unknown space collapses on sight, and to a small area on a kill, a spike plant, audible movement, gunfire, and gun damage to a teammate (a player who is shot can work out roughly where from, even with no gun noise). Ability damage does not change it. Remembered ground does not count as sight. These rules are added to the engine's unknown, so the viewer and control change too. |
| 7 | A gap needs at least 5 s since the enemy was last located. This applies to both kinds of row and to every kind of locating event. The one exception is the start of the round: an enemy who has not been located at all this round needs no wait. |
| 8 | A predicted gap records three levels of use: an enemy stood in it, shot the victim, killed the victim. Each keeps its first occurrence, with its own enemy and time. |
| 9 | Every gap is kept, including ones that qualified for under a second; those are flagged as flickers and hidden by default. Each gap stores how close the candidate enemies really were, and context, so pruning is a filter. |
| 10 | One gap per victim and route. Every cell and every enemy reaching the victim by that route belongs to it. The reported row is the merged one: a route whose choke sequence is a prefix of a longer route open for the same victim life at an overlapping time is folded into the longest one (R1). |
| 11 | A predicted gap fires once and stays latched. It closes only after its unknown space has had no line to the victim for 5 s, so a quick look back does not re-arm it. |
| 12 | Patterns are route-based and match on the full choke sequence. |
| 13 | Chokes are auto-detected per map and hand-corrected. Route-shape closeness is a second way to match routes. |
| 14 | The detector reads the engine's unknown through an observer hook on the control compute, and each round's tick records are cached locally so the detector can be re-run alone. |
| 15 | First version includes stored rows, a replay-viewer layer and a pattern page. The viewer draws the spot and the route, not the exposed cells. |
| 16 | The pattern page shows Friends and Everyone, as `/stats` does. Friends is resolved per viewer at query time; no player id is stored on a gap row. |

## Built on the engine's unknown

Rebased onto `origin/main` on 2026-10-02, after the control-heights work merged. Main by then had its own
unknown (`Unknown` in `app/control/engine.py`; design in `docs/map-control-unknown-plan.md`): one region per
enemy with each cell's earliest arrival time, spreading causally at `UNKNOWN_MPS` = 3.24 m/s (rifle-out
shift-walk, measured from replay tracks on 2026-10-01), with true diagonal lengths, the map's specials, the
per-floor nodes of the heights work, sealed pinches beside smokes, and clearing by live control only. The
owner's call (2026-10-02): **gaps use that unknown**, and the new locating rules are **added to the engine
itself**, so the viewer's unknown hatch, Safe and control all change with it.

What the engine's unknown keeps, and gaps therefore inherit:
- At the barrier drop, each enemy's region is their team's start ground behind the barriers.
- Every tick each enemy's region is also pushed out from their true position (the owner confirmed keeping
  this for gaps: a player who ran somewhere creates unknown there as they arrive).
- Pieces of at most 2 cells with no enemy in them are dropped.
- A spotted enemy (in an active view or a watcher) restarts from where they stand.

Consequences:
- `CONTROL_REVISION` goes from 4 to 5, the pinned constants digest and the flat-map reference bytes are
  re-pinned, and every round's control is recomputed (about 45 core-seconds per average round,
  `scripts/compute_control.py`).
- The replay worker's control child computes control only; gaps for rounds it computes are filled by the
  next `compute_control.py` run (section 7).

## Limits

- Awareness is modelled from vision plus five sound and feed rules (kills, plant, audible movement, gunfire,
  gun damage). Hearing ignores walls and masking by other noise.
- A sound collapses the unknown space of the enemy who really made it. A real team hears "a player is here",
  not which one. The enemies named on a gap's candidate list should therefore be read as "one of the
  unaccounted-for enemies". Anonymous handling is a stretch goal.
- Locating events are applied at control ticks. Damage-run starts are added to the tick schedule so that
  each one has a tick; other events are applied at the first tick at or after them, from the enemy's
  position and the time of the event itself.
- Damage is stored as runs (hits within 500 ms of each other merge), so a back-shot is judged at a run's
  start. A hit later in a run cannot be judged on its own.
- Causes name the player whose view or utility released a cell, not which trip or camera.
- The engine's knowledge views ("as Team X knew it", `Knowledge`, `KNEW_RUN_MPS`) are a separate picture and
  are not changed.

## Components

### 1. Reference figures

Named constants. None is taken from memory; each is measured or sourced and the source is recorded beside it.
Those that change the unknown live in `engine.py` and are covered by the pinned constants digest; the rest
live in the gap module.

| Constant | Where | Meaning | Source |
|----------|-------|---------|--------|
| `UNKNOWN_MPS` | engine, exists | Spread speed: rifle-out shift-walk, 3.24 m/s. | Measured 2026-10-01 (`docs/map-control-unknown-plan.md`). |
| `AUDIBLE_MPS` | engine | Movement faster than this is heard. | Proposed 4.5 m/s: above the fastest shift-walk (knife, 4.05 m/s) and below the slowest run measured (rifle, 5.40 m/s), from the same measurement. |
| `FOOTSTEP_RANGE_M` | engine | How far audible movement is heard. Equal to the spike explosion radius. | Valorant wiki; checked by the owner. |
| Gun hearing ranges | engine | Per gun, how far its fire is heard, including silenced guns. | A small checked-in table built from the Valorant wiki weapon pages, shown to the owner to check against the buy menu before use. |
| `KILL_AREA_M`, `PLANT_AREA_M`, `SHOT_AREA_M` | engine | Radius the unknown collapses to. | 5 m each. |
| `DAMAGE_AREA_M` | engine | Radius the unknown collapses to when a teammate takes gun damage. | Proposed 10 m: the victim knows a direction, not a spot. |
| `FOOTSTEP_AREA_M` | engine | Radius the unknown collapses to on audible movement. | Proposed 10 m. |
| `AUDIBLE_WINDOW_S` | engine | The window movement speed is measured over. | Proposed 0.5 s. |
| `MIN_UNSEEN_S` | gaps | Minimum time since the enemy was last located for a gap to count. | 5 s, the owner's figure. |
| `CLOSE_AFTER_S` | gaps | How long a gap's unknown space must have no line to the victim before the gap closes. | 5 s. |
| `FLICKER_S` | gaps | A gap that qualified for less than this in total is flagged as a flicker. | Proposed 1 s. |
| `RESULT_WINDOW_S` | gaps | How long after standing in a gap a shot or kill still counts. | Proposed 3 s. |
| `SHOT_LOOKBACK_S` | gaps | A shooter's own gunfire this soon before their damage is ignored when judging a back-shot. | Proposed 0.5 s. |
| `SHAPE_THRESHOLD_M` | gaps | How close two no-choke routes must be to group together. | Proposed 4 m. |

The gap module's constants are tunable by re-running the detector from the tick cache. The engine's need a
control recompute.

### 2. Chokes

**Detection.** `scripts/build_chokes.py` reads a map's walkable grid and finds narrow passages: places where
the walkable width reaches a local minimum along a corridor. Each choke is a short line of cells across the
passage with an id and a name. Auto-detected chokes are named by number. The map's special links
(teleporters, ropes, drops) are each a choke. On a map with heights a choke covers every floor of its cells.

**Asset.** `static/data/control/<Map>.chokes.json`, checked in beside the existing sight and walk masks.

**Correction.** `scripts/control_tagger.py` gains a choke mode to add, delete, move and rename chokes.
Hand edits are kept when detection is re-run: a choke marked as edited or deleted is not regenerated.

**Freshness.** The asset's hash is part of the gap fingerprint, so editing a map's chokes marks that map's
gap rows stale. Chokes do not affect control.

### 3. Engine observer hook and tick cache

`compute_round` takes an optional observer. With none given, its behaviour and output are unchanged. With one,
each control tick, after `TickRunner.step`, it passes a plain record:

- the time
- each live player's slot, team, node, position and facing (the yaw from `RoundInputs.pos`; `Holder` does not
  keep it)
- for each live player, two masks taken from the tick before remembered ground is added: their own view
  (`active | passive`, which includes the presence bubble) and their live utility (`watch`). A team's
  observed mask is the union, plus each player's own node, exactly the engine's `Tick.live`. The split gives
  the attribution a cause needs: which player, and whether by view or by utility.
- each enemy's unknown, per team: the node indices currently in it and, for each, the id of its arrival entry
  (section 4)
- the arrival entries appended since the previous tick, and the locating events applied at this tick
- the active smokes

A player with no position sample at a tick is absent from that tick's record.

The gap module also reads from the round blob: player tracks, lives, kills and damage runs.

**Line of sight.** "A clear line from node N to player P" means N is among the nodes P's eye sees in any
direction, smokes included: the engine's `cast` over a full circle from P's position, with P's eye height on a
map with heights. One convention, used everywhere below.

**Tick cache.** The compute writes each round's tick records to a file under the existing `.control_cache`
folder, keyed by replay id, round number and the round's control fingerprint. Unknowns are stored as changes
from the previous tick. It is local, gitignored and never in the database. A script re-runs the detector over
cached rounds without running the engine. A round whose cache file is missing or keyed to an old fingerprint
is recomputed through the engine. The cache's size per round is measured in the preview; if it is too large
to keep for every round, the plan's preview step decides a retention rule with the owner.

### 4. Changes to the engine's unknown

**Route history.** The spread (`topology.spread`, both topologies) also returns, for each node it reaches,
the neighbour or link its arrival came from. `Unknown` turns every new or changed arrival into an entry in an
append-only log per team: (node, arrival time, the parent's entry id, the choke sequence id). Entries are
added in arrival-time order within a tick, so an entry only ever points to one added before it, a route can
never loop, and a later clearing or re-entry cannot alter an earlier route. A source entry (barrier ground,
an enemy's own position, a sighting, an area collapse) has no parent. When two arrivals tie, the parent
recorded is the first giving that time in the topology's fixed neighbour order, so a rerun gives the same
route.

**Choke sequence per entry.** An entry's choke sequence is its parent's, plus the choke if its node lies on
one, interned as an id per round. Every unknown node therefore carries its own route and sequence without
tracing. The engine reads choke membership from the choke asset when it is present; without one, every
sequence is empty. This bookkeeping does not change which nodes are unknown or when.

**Locating events.** Each of these restarts that enemy's region, like a sighting does today, but as an area:
the region becomes the unobserved walkable nodes within the radius of the enemy's true position, reached by
walking from it, all at the time of the event, and it spreads on from there.

| Event | Region becomes |
|-------|----------------|
| The team sees E (active view, a watcher, `Tick.direct`) | E's node (unchanged). |
| E kills a player of the team | Within `KILL_AREA_M` of E. |
| E plants the spike | Within `PLANT_AREA_M` of E. |
| E moves faster than `AUDIBLE_MPS` within `FOOTSTEP_RANGE_M` of a live player of the team | Within `FOOTSTEP_AREA_M` of E. |
| E fires a gun within that gun's hearing range of a live player of the team | Within `SHOT_AREA_M` of E. |
| E damages a player of the team with a gun, heard or not, through a wall or not | Within `DAMAGE_AREA_M` of E. |

Positions come from the player tracks at the event's time. A kill row's own coordinates are the victim's, so
the killer's position is read from the killer's track. The planter is the bomb entry's owner in the ability
data (not the unused top-level `plant` field).

Every locating event, sighting included, is recorded per team and enemy as (time, kind), and is passed to the
observer. The gaps' 5 s wait is read from this record.

**The 5 s wait (gaps only).** After any locating event, E cannot be the source of a gap or a back-shot for
`MIN_UNSEEN_S`. The barrier drop is not a locating event: an enemy who has not been located at all this round
needs no wait. An enemy who comes back to life counts as located at that moment.

**Order of events.** Anything that is judged at an event (a back-shot, a level of use, a gap's opening) is
judged against the locating record and the unknown as they stood before that event. The event's own locating
effect is applied afterwards.

**Movement speed.** A player's speed at a moment is the straight-line distance between their positions
`AUDIBLE_WINDOW_S` apart, within one track segment, divided by that time. The same estimator gives a
back-shot's peak speed. Tracks are quantised and short gaps inside a segment are interpolated, so this is an
estimate.

**Lives.** The engine already removes a dead enemy's region. If E comes back to life, their region restarts at
their position at that moment, as a locating event; with no position then, at the first sample of the new
life. A victim who comes back is a new victim for latch purposes.

**Missing or unreliable data.**
- Speed is measured only within a track segment. A break between segments is never bridged, so a teleport is
  not a footstep.
- A live enemy with no position sample: their region keeps spreading; no locating event can fire for them,
  and their distance from a gap's spot is stored as unknown.
- A shot with no gun recorded uses the longest hearing range in the table.
- A plant with no resolved planter locates nobody.
- Each case is counted in the round's existing `missing` inputs, and the gap run records the same counts.

### 5. Predicted gaps

**Exposed and qualifying cells.** For a T player P at a tick, a region cell is *exposed* when it has a clear
line to P. It *qualifies* when it is exposed, lies within P's rear 120 degrees, and the 5 s wait (section 4) is over
for that enemy.

**Routes.** Each region cell carries its own route and **choke sequence** (section 4): the ordered list of
chokes crossed from where that enemy was last located. Cells are never lumped into patches; two neighbouring
cells reached through different chokes belong to different routes.

**Identity.** Within one life of the victim, a gap is identified by victim and choke sequence. Every exposed
cell with that sequence belongs to the one gap, whichever enemy's region it is in and whether or not the
cells touch. A route that crosses no choke has the empty sequence, so a victim has at most one no-choke gap
open at a time. The reported row is the merged one: a gap whose sequence is a prefix of a longer one open for the
same victim life at an overlapping time is folded into the longest (R1; `merge_stacks`, after every gap closes).

**Open.** A gap fires at the first moment any cell with its sequence qualifies. That moment is its opening
time. The spot is the qualifying cell with the earliest arrival, lowest cell number on a tie, and the row's
route is that cell's route. Spot, route and cause are fixed at opening and never change.

**Candidates.** An enemy is a candidate once one of their own cells qualifies with the gap's sequence, the
5 s minimum included. Membership is historical: an enemy stays on the list after their region resets.

**Latch.** While the gap is open, nothing new fires for that victim and choke sequence. If the victim moves
and a cell with a different choke sequence qualifies, that is a different gap; at the end a prefix gap is folded
into the longest overlapping extension, so the reported row is the merged one.

**Close.** A gap has two times: `t_last_exposed`, the last moment any candidate's cell with its sequence was
exposed to the victim, and `t_close`, which is `CLOSE_AFTER_S` after that if no such cell became exposed
again in between. The gap is open until `t_close` for every purpose: the latch, linking, levels of use and
the viewer. It also closes at once when the victim dies and when the round is decided (`t_decided`); nothing
is detected after that point.

The consequences of that one rule:
- The victim turning away and back does not close it, because exposure ignores facing.
- A look back that clears the space, followed by a refill within 5 s, is the same gap. The row records each
  time the victim's own view covered the spot.
- The space being cleared and staying cleared for 5 s closes it; a later return by the same route is a new
  gap with its own cause.
- The victim walking fully out of line for 5 s closes it.
- A candidate being located elsewhere removes their cells. If no candidate's cells remain exposed, the same
  5 s timer runs; there is no immediate close.

**Qualified time and flicker.** `qualified_s` is the total time during which at least one cell of the gap
qualified, counted once however many cells or enemies qualified together. Each state holds from its moment
until the next moment on the detector's timeline, clipped at `t_close`. A gap with `qualified_s` under
`FLICKER_S` is stored with a flicker flag and hidden by default.

**Cause.** Decided at the opening tick, one of:

- `route_released`: a cell on the route stopped being observed and the route completed because of it.
  Recorded: the last route cell to be released, when, which player was observing it, whether by view or
  utility, and why they stopped. The reason is one of `died`, `turned`, `moved`, `blinded` (flash or
  nearsight), `smoked`, `utility_expired` (it ended or was destroyed: the blob does not tell the two apart),
  `utility_left` (stopped using a camera or drone), `other`. The observer named for a cell is the
  lowest-numbered player of the team observing it on the last tick it was observed, by view before utility.
- `victim_turned`: cells of the route were already exposed and the victim turned their back to them.
- `victim_moved`: the victim moved into a position where cells of the route are exposed behind them.
- `open_timing`: no cell on the route was ever observed since the enemy was last located. The gap opened
  because enough time passed, including the case where only the `MIN_UNSEEN_S` wait was outstanding.

When more than one applies, the event closest in time to the opening tick wins; on an exact tie the order is
`route_released`, `victim_turned`, `victim_moved`, `open_timing`.

**Use.** Three levels. Each keeps its first occurrence only, with its own enemy and time:

- `stood`: a candidate enemy's real position was inside a qualifying cell of the gap.
- `shot`: a back-shot (section 6) on the victim by a candidate enemy while the gap was open, or within
  `RESULT_WINDOW_S` of that enemy standing in it.
- `killed`: a candidate enemy killed the victim within the same window.

The row also records whether the victim killed a candidate enemy within that window.

**Context.** Stored at the opening tick so later pruning is a filter: time into the round, players alive on
each side, spike state, whether the victim was seeing any enemy, and each candidate enemy's real distance
from the spot.

### 6. Back-shots

A back-shot is a gun damage run on a player whose attacker is within the victim's rear 120 degrees at the
run's start, judged by the victim's facing and both positions at that moment. Ability damage does not count.
It does not matter whether the victim died. If either position is missing at that moment, nothing is
recorded.

The clear-line requirement applies to predicted gaps only. A back-shot needs the rear 120 degrees and nothing
else, so damage through a wall counts and is tagged.

**The 5 s test.** A back-shot is recorded only if the victim's team had not located the shooter for at least
`MIN_UNSEEN_S` before it, or had not located them at all this round. Shots and damage are recorded separately with their own times and nothing ties a
damage run to the shot that caused it, so the shooter's own gunfire in the `SHOT_LOOKBACK_S` (0.5 s) before
the run's start is ignored for this test. Without that, the shot that did the damage would be heard first
and cancel its own back-shot.

**What a back-shot tells the victim's team.** The damage locates the shooter to within `DAMAGE_AREA_M`, as
any gun damage does (section 4), whether or not the gun was heard. It is applied after the back-shot is
judged. Follow-up damage from the same shooter therefore creates no further rows until they have been lost
for 5 s again.

**Path.** Each back-shot records the shooter's real path from where the victim's team last located them (the
round start if never) to the shot, with times, and the shooter's peak speed along it, so a shooter who ran is
visible in the data. The starting point is the shooter's true position at the locating event, the same origin
a predicted route has. Breaks between track segments are kept as breaks in the stored path and are never
joined. The path's choke sequence is computed only when the path has no break; otherwise it is stored as
unknown, the row is shown in the viewer, and it is left out of patterns.

**Linking.** A back-shot is linked to an open predicted gap on that victim only if the shooter is one of the
gap's candidates. If several qualify, the one whose choke sequence equals the back-shot's is chosen, otherwise
the one whose spot is nearest the shooter. A back-shot with no such gap stands alone; those are the cases the
walk-speed model missed.

### 7. Storage

One migration, two tables, both cascade-deleted with their round as control rows are. The ValoMaths demo gets
empty tables, and the writer refuses to run against a demo database as the control writer does. The migration
number is assigned when the plan is written, after the rebase.

**`replay_round_gap_runs`**: one row per replay round. Columns: `replay_id`, `round_number`, `status`
(`ok` or `failed`), `fingerprint`, `gaps_revision`, `chokes_hash`, `gap_count`, `notes` (JSON counts of the
missing-data cases), `error`, `computed_at`. It separates "computed, no gaps" from "not computed" and identifies stale rounds.

**`replay_gaps`**: one row per predicted gap or back-shot.

| Column | Meaning |
|--------|---------|
| `replay_id`, `round_number`, `seq` | Key. `seq` orders rows within the round. |
| `kind` | `predicted` or `backshot`. |
| `map` | Map name, for per-map queries without a join. |
| `victim_slot` | The victim. Player ids are joined from the replay link at query time. |
| `victim_side` | `attack` or `defense` (the engine's spelling); null for an unlinked replay, which side filters exclude. |
| `t_open`, `t_last_exposed`, `t_close` | Replay-clock seconds. For a back-shot, `t_open` is the damage run's start and the other two are null. |
| `spot_cell`, `victim_cell` | Grid cells at the opening tick. For a back-shot, the spot is the shooter's cell. |
| `distance_m`, `angle_deg` | Spot to victim, and off-facing angle, at the opening tick. |
| `qualified_s`, `flicker` | Total time the gap qualified, and the flicker flag (predicted only). |
| `cause`, `cause_detail` | The cause kind, and JSON with the released cell, time, player, view or utility, and reason (predicted only). |
| `choke_seq` | Ordered choke ids. Null when unknown (a back-shot path with a break). |
| `route` | JSON: thinned path points with times, as a list of unbroken pieces. The modelled route at opening for a predicted gap (one piece), the real path for a back-shot. |
| `candidate_slots` | Integer array of enemy slots it could have been. For a back-shot, the shooter. |
| `candidate_distances` | JSON: each candidate's real distance from the spot when they joined, or null when unknown. |
| `checked_at` | Times the victim's view covered the spot while open (predicted only). |
| `stood_at`, `stood_by`, `shot_at`, `shot_by`, `killed_at`, `killed_by` | First occurrence of each level of use and its enemy slot. A back-shot fills only `killed_at` and `killed_by`, when its shooter killed the victim within `RESULT_WINDOW_S`. |
| `victim_won_at` | When the victim killed a candidate within the result window, if they did. |
| `context` | JSON: time into round, alive counts, spike state, victim seeing an enemy, wall-bang flag, shooter's peak speed. |
| `linked_seq` | For a back-shot, the predicted gap it is linked to, if any. |

**Freshness.** The gap fingerprint hashes the round's control fingerprint, `GAPS_REVISION`, the map's choke
asset hash and the gun hearing table's hash. Any change to the gap rules or constants bumps `GAPS_REVISION`.
Because rows hold slots and not player ids, a changed link cannot leave stale ids behind.

**Writer.**
- `scripts/compute_control.py` passes the observer and writes the tick cache. The detector runs inside the
  worker task in its own failure boundary: a detector error is returned as the gap run's failure and the
  control result is returned unchanged.
- The parent replaces a round's gap run row and all its gap rows in one transaction.
- The planner also selects rounds whose control row is fresh but whose gap run is missing or stale. Those run
  from the tick cache when it is present and current, and through the engine otherwise (without rewriting
  the control row). This is how rounds computed by the replay worker's control child get their gaps.

### 8. Viewer layer

- `GET /replays/{match_uuid}/{round_number}/gaps.json` returns the round's rows, under the same access and
  demo-mode rules as `control.bin`. Like `control.bin`, it still serves a stale run and says it is stale; a
  failed or missing run returns no rows and says which.
- The replay viewer gets a Gaps toggle beside the control layers. While a predicted gap is open at the
  current time it draws the spot, the route with chokes marked, the victim's rear 120-degree arc, and the
  released cell for a `route_released` cause. It does not draw the exposed cells. A back-shot draws the shooter's
  real path.
- A list for the round shows time, kind, victim, cause and level of use. Clicking a row seeks to its opening
  time. Flickers are hidden unless switched on.
- Used and unused gaps, and predicted gaps and back-shots, are drawn differently.

### 9. Pattern page

- One page per map, with filters for kind, side, cause, level of use and time into the round (for example the
  first X seconds only). Flickers are excluded unless switched on.
- **Populations**, as on `/stats`: Everyone is every row in every computed round. Friends is rows involving
  the logged-in viewer or the friendships that viewer owns, resolved at query time by joining slots to player
  ids through the replay link. Logged-out visitors see Everyone only. Rows from unlinked replays count under
  Everyone only.
- Under Friends, a switch chooses between gaps the friends left open (a friend is the victim) and gaps their
  opponents left open (a friend is a candidate enemy or the shooter).
- **Which rounds count.** Only rounds whose gap run is `ok` and whose `gaps_revision` and `chokes_hash` match
  the current code and the current choke asset. Rows computed under older rules or older chokes are never
  mixed in. The page shows how many rounds were left out as stale, failed or not yet computed, so a partial
  recompute is visible.
- Patterns are listed by full choke sequence. Rows with an unknown sequence are left out.
- **Figures per pattern.** Predicted gaps: the count, then the share stood in, shot and killed, each as a
  share of that pattern's predicted gaps, plus the most common cause and the typical time into the round.
  Back-shots: a separate count, split into linked to a predicted gap and standalone, and the share that
  killed, as a share of that pattern's back-shots. A linked pair is one predicted gap and one back-shot; it
  is never added together. The stood and shot filters apply to predicted gaps only. Every figure shows its
  sample size.
- **Route shape**, for rows with the empty choke sequence only, within one map and side. Each route is
  resampled to 16 points equally spaced along its length; the distance between two routes is the mean
  distance between matching points. Rows are taken in a fixed order (match date, replay id, round number,
  `seq`): a route joins the first existing group whose founding route is within `SHAPE_THRESHOLD_M`
  (proposed 4 m), otherwise it founds a new group. In the worst case, when no two routes are alike, this
  compares every pair, so the page groups at most the 2,000 most recent eligible rows per map and side and
  says so when it has cut the list.
- Selecting a pattern draws its routes on the map and lists its rounds, each linking into the viewer.
- The page states that the data is too thin for conclusions. No cache in the first version.
- Nothing on the page is defined by `scripts/tracked_players.json`.

## Testing

- **Engine unknown**: the existing unknown tests keep passing. New, on the toy maps: the spread's parents on
  both topologies (flat and with heights); route history surviving a clear and re-entry; choke sequences
  along a route; the route bookkeeping leaving which nodes are unknown unchanged; each locating event, its
  area and its origin; a second life; each missing-data rule.
- **Control revision**: `CONTROL_REVISION` 5 with a re-pinned constants digest; the flat-map reference rounds
  re-pinned, with the differences explained by the new locating rules only.
- **Event order**: a shot that is both a back-shot and a locating event; a kill that closes a gap it used.
- **Predicted gaps**: the rear 120 degrees boundary; the 5 s minimum; two neighbouring cells with different choke sequences giving two gaps (when neither is a prefix of the other); merging across enemies; prefix folding (R1: longest, then earliest; union of qualified time; transitive);
  the latch; a different choke sequence opening a second gap; each consequence of the close rule; the flicker
  flag; each cause, each release reason and the tie order; each level of use with different enemies.
- **Back-shots**: run start only; gun only; wall-bang tagged; the 5 s minimum; follow-up damage not creating
  rows; linking, including several open gaps and none.
- **Chokes**: synthetic corridors with known pinch points; edited chokes surviving a re-run.
- **Observer hook**: control output is byte-identical with and without an observer.
- **Tick cache**: gaps from the cache equal gaps from a live compute; two rounds of one replay do not collide.
- **Storage and writer**: fingerprint staleness on a `GAPS_REVISION` bump, a choke edit and a hearing-table
  edit; cascade delete; a detector failure leaving the control row intact; replacement of old rows.
- **Friends filter**: both directions, a logged-out viewer, an unlinked replay, and a re-linked replay.
- **By eye**: one or two real rounds previewed in the viewer before any full recompute.

## Build order

1. Reference figures: source the footstep range and the gun hearing table, and have the owner check the
   table.
2. Choke detection, asset and tagger mode.
3. Engine: route history and choke sequences in the unknown (output unchanged).
4. Engine: the locating events; `CONTROL_REVISION` 5.
5. Observer hook and tick cache.
6. Predicted gaps and back-shots, developed against cached rounds.
7. Storage and the compute script.
8. Preview one or two rounds in the viewer, then the full recompute of control and gaps (about 45
   core-seconds per round for control, plus the detector's measured cost).
9. Viewer layer.
10. Pattern page.

## Stretch goals

- Ability range limits: an ability's maximum throw distance bounds where its caster can be (the Reyna flash
  case).
- Direction from damage: collapsing to a wedge in the direction of the shot instead of a circle around the
  shooter.
- Anonymous sounds: a shot or footstep says "a player is here", not which one.
- Naming the specific trip, camera or turret in a cause.
- Drawing the exposed cells in the viewer.

## Out of scope

- Hearing through walls or masked by other noise.
- Statistical claims, significance tests or any effect on Impact scoring.
- Caching for the pattern page.
- Named map areas beyond choke names.
