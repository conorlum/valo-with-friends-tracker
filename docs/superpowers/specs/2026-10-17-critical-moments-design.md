# Critical Moments - design specification

Date: October 17, 2026. Status: design only; no application changes. Branch `worktree-critical-moments`, cut
from `origin/main` at `a88f2ae`.

## Outcome

Every linked replay gets a **Critical Moments** tab in the replay viewer's side column. A viewer picks a player
and sees every fight that player took, grouped by what kind of fight it was, with no AI involved: the tags are
fixed rules over the stored replay. Each fight is a button that moves the viewer to about 3 seconds before it
started, so the viewer can see how the player got there.

The tab states measurements, not verdicts. "Moving while firing: 9 of 12 shots above 2.5 m/s" is shown; "bad
fight" never is. Wins and losses of the same kind sit together (playbook rule 14).

## Decisions (owner, 2026-10-17)

| # | Decision |
| --- | --- |
| Placement | A tab in the replay viewer's side column, for the picked player, on every linked replay, coach report or not. |
| Contents | Every fight the player was in, tagged and grouped by type, wins and losses together. Nothing filtered out, nothing called good or bad. |
| Process | This spec first; thresholds measured from the whole replay corpus before any code. |
| Sight | "Did you see your killer" and "did your victim see you" are wanted. Spam through smoke is real, so a kill with no sight is not an error. |
| More tags | Control lost at death (with the most expensive deaths shown), saw-first and time to kill (medians over clean kills only), information given away, and utility on you, mollies included. |
| Where | Computed in the gaps block of the control task on the worker. |

## What a fight is

A **fight** is the gun and ability damage and the kills between one player and the enemy side, in time order,
split wherever nothing happens between them for **3 seconds** (the same 3 seconds as playbook rule 15). Its
start is the first damage either way; its end is the last damage or kill. Its **opponents** are the enemies who
dealt or took damage in it. Only the live round counts (`t <= t_decided`), as in `app/coach/detectors.py`.

Corpus (55 linked replays): 20,622 fights at a 3 s gap; 21,788 at 2 s and 18,770 at 5 s, so the split is not
sensitive to the exact value. That is about 37 fights per player per match.

Each fight carries its **outcome**: the player's kills in it, whether the player died in it, and whether that
death was traded (below). The man count (`own`v`enemy`) is taken just before the start, never after a kill
inside the fight (rule 15).

## Tags

A fight carries every tag that applies. Each tag stores the numbers behind it, and the tab shows them.

### 1. Moving while firing

The player's speed at each of their shots in the fight (from 1 s before the start to the end), from the 16 Hz
track: the distance between the samples either side of the shot over the time between them. A shot within
0.75 s of a movement-ability cast (`cast` rows: Jett, Waylay dashes) has no speed: the dash is not the player's
walking.

- **Threshold: 2.5 m/s.** Corpus, rifle shots in fights (n = 54,143): median 0.0, 75th percentile 0.8, 90th
  2.5 m/s. 69% of rifle shots are under 0.5 m/s, then a long tail to 6 m/s, so 2.5 m/s marks a shot clearly
  taken while moving, not a slow walk or the end of a counter-strafe.
- **Tagged** when at least half the player's measured shots in the fight, and at least 2, are above the
  threshold. Corpus: about 1,800 of the 15,821 fights with measured shots (about 11%; the survey bucketed the
  share to one decimal and did not require 2 shots).
- **Not tagged for** shotguns (Shorty, Bucky, Judge) or anything outside the gun table (melee, ability guns):
  moving is how those are used. The fight still shows its speeds.
- Shown: "N of M shots above 2.5 m/s; first shot at X m/s".

### 2. Traded

Uses `app/coach/detectors.py`'s rule and `TRADE_WINDOW_S` (the scoring formula's own trade window) so the two
never disagree.

- **Died, traded**: the player's killer was killed by a teammate within the window. Shows who and after how long.
- **Died, not traded**.
- **Traded a teammate**: the player's kill in this fight was on an enemy who had killed a teammate within the
  window. New; the same window.

### 3. Teammate nearby

The distance to every living teammate just before the fight starts; the nearest is shown.

- **Threshold: 20 m**, `detectors.TRADE_RANGE_M`, unchanged. Corpus (n = 18,704 fights with a teammate alive):
  median nearest teammate 14.2 m, 75th percentile 24.2 m, 90th 36.1 m.
- **Alone**: nearest teammate farther than 20 m. Wins as well as losses (owner's feedback, report 20).
- **With a teammate**: a teammate within 20 m who also dealt damage to an opponent of this fight during it. This
  is the "double swing" the owner pointed out in round 33 of the first match reviewed.
- **No teammate alive**: the clutch case. Neither of the above, and never shown as "alone"; it has its own group, "Clutch (no teammate alive)", whose rows give the man count ("1v3 clutch").
- Also shown, not a tag: **who did the first damage**, the player or the opponent. A fight the enemy started
  may have been forced (owner's feedback, report 20).

### 4. Utility

Utility from the player's side, on or near this fight's opponents, from 3 s before the start to the end.

**On an opponent**, exact, from rows that name their target:
- a flash hit (`flash` row `hits`), with its duration;
- a status (`status` rows: concuss, slow, suppress, and so on);
- a reveal (`reveal` rows: Sova dart, Fade prowler, Cypher camera and so on);
- ability damage (`damage` with `src: "ability"`).
Corpus: 4,611 such rows across the fights (status 1,417, reveal 1,323, ability damage 1,109, flash hit 762).

**Near an opponent**: a placed ability alive at the fight's start within **8 m** of an opponent. Every ability is one of
the owner's seven kinds (D5, `constants.ABILITY_KIND`): damaging, info, smoke, teleport, heal, status
(non-damaging: slows, concusses, tethers, suppress, nearsight) and blind; walls, decoys, shields and buffs are none.
Damaging, info, status and blind count as near, and each entry and row names its kind ("utility: 1 damaging,
1 status"); utility on a player is labelled the same way from its event (damage, reveal, status, nearsight,
flash). **Smokes
are listed separately as "smoke near"**: a smoke near an enemy is not utility on them, and smokes are the most
common placed ability within 8 m (corpus: 4_Smoke 904, NewSmoke 840, E_Smoke 471 among the within-8 m rows).
Movement and self abilities (stims, Clove's orbs, teleporters) never count. The list of which ability names
belong to which group is a table in the module, reviewed with the owner when the build starts.
- **Threshold: 8 m**, about the 10th percentile of the distance from placed utility to the nearest opponent at
  the start of a fight (corpus n = 90,935: 10th percentile 7.5 m, median 30.6 m).

Shown: who used it ("your" or a teammate's name), what it was, and on whom.

### 5. Sight

Uses the control engine's own check, `app/control/utility.py` `_sees_point`: the viewer is alive, not flashed,
has the point inside their field of view (`engine.FOV_HALF`, 51.5 degrees either side) and a clear line to it
past walls and the smokes up at the time; nearsight limits the range. It is tested at 16 Hz from 3 s before the
first damage to the kill.

- **Died without seeing your killer**: in that window the player never had the killer in sight.
- **Killed unseen**: the player's victim never had the player in sight.
- **Wall-bang**: shown on either tag when the killing damage went through a wall (`damage.wall`).

Corpus, all live-round gun kills (n = 8,734) on the 7 maps with geometry:

| Map | Victim never saw killer | Killer never saw victim | Neither saw, not a wall-bang |
| --- | --- | --- | --- |
| Abyss | 9.2% | 2.9% | 2.4% |
| Haven | 10.3% | 3.4% | 2.9% |
| Split | 12.8% | 4.4% | 3.9% |
| Lotus | 12.9% | 5.0% | 3.9% |
| Summit | 12.8% | 4.8% | 4.1% |
| Sunset | 15.3% | 5.2% | 4.7% |
| Ascent | 15.6% | 6.5% | 5.1% |

You do not need to see an enemy to shoot them: players spam through smokes and get lucky (owner, 2026-10-17).
The "neither saw" kills were split by testing the killer-to-victim line again with smokes ignored (corpus, the
341 such kills):

| Why neither saw | Kills | Share of all gun kills |
| --- | --- | --- |
| Through smoke: walls clear, a smoke in the way | 308 | 3.5% |
| Walls in the way at every moment (Ascent 18, Summit 5, others 1-3) | 32 | 0.4% |
| Killer flashed | 1 | 0.0% |

So the check's real error floor is about 0.4% of kills (most likely doors and heights the line gets wrong), not
the 2-5% the first table suggests. Rules:

- A kill whose line was clear except for a smoke is tagged **Through smoke**, on the killer's fight and on the
  victim's. It also counts for "killed unseen" and "died without seeing the killer".
- A kill where walls block the line both ways and nothing went through a wall shows "sight: not measurable" and
  carries no sight tag.
- The rest are tagged as measured.

### 6. Control lost at the death

Map control already measures, for every death in the live round, the area the team lost when that player died
(`deaths[].control_m2` and `share_of_team` in the round summary; the Control tab's "Lost" column). The moments
are computed in the same task as control, so the fight's death takes that number as it is.

- Shown on every death: "the team lost N m² (P% of its control)".
- At the top of the tab, before the groups: **Most expensive deaths**, the player's three deaths with the most
  control lost in the match, each a jump like any fight (owner, 2026-10-17). This is the owner's point from
  report 20: "my team would lose a lot of control if I die".

### 7. Who saw whom first, and time to kill

The sight check of section 5 also gives, for each kill, the start of the killer's last unbroken sighting of the
victim before it (a gap of up to 0.25 s does not break it): the **spot**. And the killer's **first shot** is the
first of the run of shots that ended in the kill (shots more than 0.6 s apart start a new run).

Per fight, always shown:
- **Saw first**: who had the other in sight first, and by how much ("they had you in view 0.6 s before you saw
  them"). That separates a held angle from a peek.
- **Time to kill from the first shot** and **from the spot**, for each of the player's kills; and for each death,
  the killer's.

Not every kill is a reaction fight: a player may watch an enemy who hasn't seen them, wait for a teammate, or
spray from movement and then stop (owner, 2026-10-17). So a kill is **clean** only when the killer saw the
victim, the shot run starts no earlier than the spot and within 1.0 s of it, no gap in the run is over 0.6 s, and
at most one shot in it was above 2.5 m/s. A kill that is not clean still shows its numbers, with the reason
("first shot 2.3 s after seeing them", "moving", "fired before seeing them", "never saw them").

**Averages use clean kills only**, and the tab shows medians, never means, with the count they are over
("median 0.31 s over 14 clean kills"). Fewer than 5 clean kills: no median, only the list.

Corpus (8,673 live-round gun kills):

| | Kills | Share |
| --- | --- | --- |
| Clean | 5,138 | 59% |
| First shot more than 1 s after the spot | 2,385 | 27% |
| Moving (two or more shots above 2.5 m/s) | 514 | 6% |
| Killer never saw the victim (smoke, wall-bang) | 338 | 4% |
| Fired before the spot (pre-fire, spam into a corner) | 298 | 3% |
| No shots stored | 61 | 1% |

Clean kills: time to kill from the first shot has a median of 0.27 s (10th percentile 0.00: a one-shot kill; 90th
0.81 s); from the spot, a median of 0.81 s (10th 0.44, 90th 1.38); spot to first shot, a median of 0.50 s (10th
0.28, 90th 0.82). The spot is when the victim enters the killer's 103-degree field of view with a clear line, not
when they are on the crosshair, so spot-to-shot includes turning to them.

### 7a. Where the enemy was on the screen

The owner, 2026-10-18: an enemy who shows up within a few degrees of the crosshair means something different from
one who surprised the player on another angle. The replay stores only the horizontal facing (`yaw`, whole
degrees), not the vertical one, so the angle is left-right only: an enemy straight above the crosshair counts
as on it. The facing is fine enough: while firing, it changes by a median of 0 degrees per 1/16 s (90th
percentile 2 degrees).

**The angle at the spot**: at each player's own spot of the other (section 7), the horizontal angle between
their facing and the bearing to the other player. It is measured at each player's own spot, never at the first
damage: at the first damage the shooter is aiming at their target by definition, which would make every winner
look "on the crosshair". Three bands:
- **On the crosshair**: 5 degrees or less.
- **Near**: over 5 and up to 20 degrees.
- **Off-angle**: over 20 degrees, up to the edge of the 103-degree view.

Corpus, the killer's angle at their spot (8,335 kills where the killer saw the victim):

| Angle at the spot | Kills | Share clean | Spot to first shot, median (clean) | Time to kill from the spot, median (clean) |
| --- | --- | --- | --- | --- |
| up to 2 degrees | 1,576 | 78% | 0.44 s | 0.69 s |
| 2 to 5 | 1,847 | 77% | 0.47 s | 0.75 s |
| 5 to 10 | 1,719 | 66% | 0.50 s | 0.81 s |
| 10 to 20 | 1,347 | 51% | 0.55 s | 0.88 s |
| 20 to 51.5 | 1,846 | 36% | 0.63 s | 0.94 s |

41% of kills start with the enemy on the crosshair, and those are about 0.2 s quicker. The off-angle ones are
clean less often because a flick to 40 degrees takes time, so part of the 1.0 s rule in section 7 is turning, not
waiting. So:
- **The reaction and time-to-kill medians are shown per band**, never as one number across bands.
- Each fight shows both players' angles at their spots: "they appeared 3 degrees off your crosshair; you were 31
  degrees off theirs".
- Groups: **Caught off-angle** (the opponent's spot of the player came first and the player's own angle at
  their spot was over 20 degrees), and a match count: "N of your M fights started with the enemy off your
  crosshair (over 20 degrees)".

### 8. Information given away

Map control stores every event that located a player for the enemy (`signals`: time, slot, reason) in 1,180 of the
corpus's 1,201 rounds. Corpus counts by reason: seen 30,117, damage 14,689, footsteps 9,351, kill 8,457,
gunfire 5,712, and the enemy's recon (Sova and Fade reveals, cameras, trips, drones and so on) about 11,000
together. A median of 6 per player-round.

For the picked player, per match and per round:
- **Heard**: footsteps. The player's own noise; the owner's noise-discipline principle.
- **Shots heard outside a fight**: gunfire not inside one of the player's fights.
- **Found by utility**: the enemy's recon and traps (recon, haunt, camera_dart, drone_dart, trip, tejo_drone,
  neural_theft, and the other utility reasons).
- **Seen** is counted but not listed: being seen is part of playing.
Consecutive events with the same reason less than 2 s apart are one moment. Each "heard" and "found by utility"
moment is a jump. These are the enemy's knowledge, so they say what the player gave away, never what they should
have done (playbook rule 13).

### 9. Utility on you

The reverse of section 4: the enemy's utility on the player, from 3 s before the fight to its end.
- Flash hits, statuses (concuss, slow, suppress and so on), reveals and ability damage on the player, exact from
  the rows that name their target.
- **Mollied off a spot**: the player was inside an enemy damage zone when it appeared, and left it. The zones and
  their shapes come from `app/control/mollies.py` (the owner's list `MOLLY_KEYS`: Aftershock, Incendiary, Orbital
  Strike, Mosh Pit, FRAG/ment, Nanoswarm, Hot Hands, Guided Salvo, Armageddon, Snake Bite), so the moments and
  control agree on what a molly is. A player made to move by a molly is being utiled even if it never damaged
  them (owner, 2026-10-17).
- Shown on the fight: what hit them, from whom, and when.

### Considered and left out (owner, 2026-10-17)

Health going into a fight; weapon matchup (the database already has loadouts); peek order in a double swing; what
the player did after a kill.

## Architecture

### Computation (`app/moments/`, pure)

`app/moments/fights.py` splits a round into fights and tags them. Its inputs are the round blob, the link (sides,
DB-only deaths) and, when available, the control engine's `RoundInputs` and geometry for the sight tags. It
reuses `app/coach/roundview.py` for positions and distances. All thresholds live at the top of the module with
their corpus numbers in a comment, as `detectors.py` does.

The worker's interpreter has no SQLAlchemy, and `detectors.py` imports `app.scoring.impact`, which needs it. So
the moments module never imports `detectors.py`. The trade window and the 20 m trade range move into a leaf
module (`app/coach/fight_constants.py`) that both import, so the two can never disagree.

It needs the map's geometry for sight, which only the replay worker has loaded (the visibility table takes about
60 s to build per map; it is cached). Gaps are not a workflow task of their own: they are a `gaps` block on the
control task, plus a gaps-only task for a round whose control is already current
(`app/services/replay_control_remote.py`). The moments join the **gaps block** (owner, 2026-10-17): the same
task in `app/control/task.py` that runs the gap detector over `RoundInputs(blob, geo, link)` returns the round's
fights as well. A map without geometry still gets every tag except sight.

`MOMENTS_REVISION` starts at 1 and changes with any rule change. It is part of the gap fingerprint, so a rule
change makes every round stale and the existing gaps-only path recomputes them. That is also the backfill of the
55 stored replays, with no script of its own. The worker's `control.gaps_protocol` goes to 2, so a worker that
can't compute moments is never sent a block that asks for them.

### Storage (migration 0026)

`replay_round_moments`: one row per replay round (`replay_id`, `round_number`, `status` ok/failed,
`fingerprint`, `moments_revision`, `fights` JSON, `error`, `computed_at`), cascade-deleted with the round. It is
written by the gaps' guarded writer (`app/services/replay_gaps_store.py`) in the same transaction as the round's
gap run. The JSON holds slots, never names or player ids, like `replay_gaps`.

### Endpoint

`GET /replays/{match_uuid}/moments.json`: every round's fights for all ten players, names filled in from the
link, with an ETag over the stored rows (`_json_with_etag`). The same 404s as the control routes
(`_control_replay_or_404`), except that a map without geometry still answers, without sight.

### Page

- A **Critical Moments** tab after Analysis in `replays/_player.html`, loaded when first opened.
- A player picker at the top. It defaults to the logged-in viewer when they played in the match, otherwise the
  first player of the attacking team in round 1.
- At the top: **Most expensive deaths** (section 6), then a one-line summary: median time to kill from the first
  shot and from the spot over clean kills (section 7), and the match's information given away (section 8).
- Groups, each with its count: Moving while firing, Alone, With a teammate, Clutch (no teammate alive), Died not traded, Traded, Traded a
  teammate, Utility on them, Utility near them, Smoke near them, Utility on you, Mollied off a spot, Saw them
  first, Seen first, Caught off-angle,
  Died without seeing the killer, Killed unseen, Through smoke, Other fights, Heard, Found by utility. A fight
  with several tags is in several groups; "Other fights" holds every fight no group above shows (a clutch with
  no other tag, or a fight with none), so every fight is listed.
- Each row: the round and clock time, the opponents, the outcome (kills / died / traded) and the measured
  numbers of that group's tag. Clicking a row moves the viewer to the round at 3 s before the fight's start
  (playbook rule 17), using the same jump the Analysis tab uses.
- One line under the tabs says what the tags are measured from and that they are not verdicts.

The demo never shows the tab: it has no replays (`store.refuse_demo`).

## Known limits, stated so nobody is surprised

- Speed is the body's speed from a 16 Hz track: it cannot tell a counter-strafe in the middle of a burst from
  standing still. A tag means "moving at the shot", not "inaccurate".
- "With a teammate" needs the teammate to have dealt damage. A teammate holding the same angle and never firing
  counts as nearby but not fighting.
- Sight is 2D lines with heights where the map has them. Doors, rope and elevated angles are where the 0.4% error floor
  above comes from. "Not measurable" is shown honestly rather than guessed.
- The near-utility list of ability names needs the owner's review; until then an unlisted ability is not counted.

## Build amendments (independent review, 2026-10-18)

An independent review of this spec against the repo found places where it could not be built as written. These
amendments win over the sections above where they differ.

- **Control lost (6) and information given away (8) are joined on the site, not computed in the gaps block.**
  Almost every round's moments come from a gaps-only task, which runs no control summary and no knowledge pass
  (`app/control/task.py` gaps-only path). The fights and every other tag stay in the gaps block. The endpoint
  reads each death's `control_m2` and `share_of_team` from the stored control summaries
  (`replay_control_views.load_round_summaries`) and the locating events from the stored control data's
  `knowledge_events` header (already collapsed at 1.0 s by the encoder; the 2 s grouping of section 8 goes on
  top). The endpoint's ETag covers the moments rows and the summaries.
- **No geometry, no moments.** A round on a map without geometry is never sent a control task, so it has no
  moments. The tab is shown where the Control tab is (`linked and control`), and the endpoint uses
  `_control_replay_or_404` unchanged.
- **Revision.** The moments ride `GAPS_REVISION` (3 -> 4, in `app/gaps/detect.py` and
  `app/services/replay_gaps.py`): the worker reports its own revision, so an old image's result is never taken
  as current. An ok gaps result with no `moments` key is untrusted and retried. Merging makes every stored gap
  run stale until the worker's gaps-only backlog drains: the Gaps tab says stale and `/gaps/<map>` leaves those
  rounds out meanwhile.
- **Where the code lives.** `app/moments/` (`__init__.py`, `constants.py`, `fights.py`). `fights.py` imports
  only `app.control.{engine, utility, mollies}` and works in the engine's own units (`RoundInputs`: pixels,
  `m_per_px`, yaw); it does not use `app/coach/roundview.py`. `constants.py` is standard library only and holds
  `TRADE_WINDOW_S = 6.0` and `TRADE_RANGE_M = 20.0`; `app/coach/detectors.py` imports them from it, and a
  site-side test pins `TRADE_WINDOW_S == float(app.scoring.impact.TRADE_WINDOW_SECONDS)` (no edit to
  `app/scoring/`). The worker image copies `app/moments` and smoke-imports `app.moments.fights`. The web app
  never imports `app.moments.fights`.
- **The gun table** (survey `GUN_CLASS`) moves into `app/moments/constants.py`. Every class except shotgun and
  "other" can be tagged moving.
- **Fight events.** An event's time is its `t` (not `t1`). Damage and kills with `t > t_decided` are dropped.
  The 3 s gap is measured from the previous event's `t`. A DB-only death (no killer) is not a fight event;
  "died" comes from kill rows only.
- **Spot and clean-kill details**, each a named constant: the spot is looked for up to 10 s back from the kill;
  the run is walked backward from the killer's last shot at or before kill + 0.05 s; "no earlier than the spot"
  allows 0.05 s; a kill needs gun damage from the killer to the victim within 10 s before it to be measured.
- **Saw first and angles for every fight**: over fight start - 3 s to its end, for the player and each opponent,
  each one's first sight of the other in that window; a fight where neither saw the other shows none.
- **Mollied off a spot**: the player was inside a zone from `mollies.molly_zones` when it appeared, alive, and
  was outside it before the zone ended.
- **Dash shots**: only blobs with `movement_casts` exclude them; older blobs exclude none.
- **The jump** is `window.viewer.showRound(round, t)` (as the coach panel does), not the Analysis tab's
  in-round seek.
- **Picker default**: the logged-in player (`get_current_player`) mapped to a slot through the page's players;
  otherwise the lowest slot of the round-1 attacking team.
- **Moments have their own status** in their row. They are written only where the gap run is written (never
  on the writer's "already current" return).
- **Demo**: reads already 404 in demo mode (`_replay_or_404`, `replays_enabled`).
- **Sight is 2D**: `_sees_point` passes no height, so the "with heights" in the known limits is wrong.

## Later

- The coach brief can take these fights as flags (`detectors.flags`), replacing the per-death flags with
  per-fight ones that already follow rules 15 and 16.
- Totals across a player's matches (the share of their fights taken alone, moving, and so on) for the player page.

## Testing

- Unit tests on hand-built blobs for each tag's edges: a 2.9 s and a 3.1 s gap, exactly 2.5 m/s, a dash shot, a
  shotgun, a trade at the window's edge, a teammate at 19.9 and 20.1 m, a flash hit before the window, a smoke
  near an enemy, a kill where the killer never saw the victim, a kill through smoke, a first shot 0.99 and 1.01 s
  after the spot, a molly that lands on the player and one that lands beside them, footsteps 1.9 and 2.1 s apart.
- A golden test on one real round of match `ac9fd52a`, whose fights were checked by hand on 2026-10-17 (round 33:
  with a teammate, 2.0 m; round 15: 3v3 before the fight; round 9: killed unseen).
- The corpus survey scripts are rerun after the build, and their numbers must match this spec's within rounding.

## What the owner still owes, at build time

- Review the ability-name groups for "utility near" (damage/deny, smoke, never).
- Confirm 2.5 m/s, 20 m and 8 m after seeing them on a few of their own fights in the built tab.
- Confirm the clean-kill rules (start within 1.0 s of the spot, gaps up to 0.6 s, at most one moving shot) on a
  few of their own kills, clean and not.
- Say which locating reasons count as "found by utility" if the list in section 8 is wrong.
- Confirm the crosshair bands (5 and 20 degrees) on a few of their own fights.
