# Critical Moments - design specification

Date: October 17, 2026. Status: design only; no application changes. Branch `worktree-critical-moments`, cut
from `origin/main` at `a88f2ae`.

## Outcome

Every linked replay gets a **Critical Moments** tab in the replay viewer's side column. A viewer picks a player
and sees every fight that player took, grouped by what kind of fight it was, with no AI involved: the tags are
fixed rules over the stored replay. Each fight is a button that moves the viewer to about 5 seconds before it
started, so the viewer can see how the player got there.

The tab states measurements, not verdicts. "Moving while firing: 9 of 12 shots above 2.5 m/s" is shown; "bad
fight" never is. Wins and losses of the same kind sit together (playbook rule 14).

## Decisions (owner, 2026-10-17)

| # | Decision |
| --- | --- |
| Placement | A tab in the replay viewer's side column, for the picked player, on every linked replay, coach report or not. |
| Contents | Every fight the player was in, tagged and grouped by type, wins and losses together. Nothing filtered out, nothing called good or bad. |
| Process | This spec first; thresholds measured from the whole replay corpus before any code. |
| Sight | "Did you see your killer" and "did your victim see you" are wanted (owner, 2026-10-17). |

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
- **No teammate alive**: the clutch case. Neither of the above, and never shown as "alone".
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

**Near an opponent**: a placed ability alive at the fight's start within **8 m** of an opponent. Only abilities
that deny or damage space count: mollies and other damage zones, slows, cages, trips, turrets, walls. **Smokes
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

"Neither saw, not a wall-bang" is the check's error floor. A gun kill where neither player had the other in sight
and nothing went through a wall is most likely a shot through a smoke edge, a door or a height the 2D line gets
wrong. Rule: **a sight tag is stored only when the killer did see the victim** (or the kill was a wall-bang).
When the killer did not, the fight shows "sight: not measurable" and no tag. That keeps the floor out of the
tags. The killer must have the victim in sight to shoot them, so a missing sighting there means the check failed,
not the player.

## Architecture

### Computation (`app/moments/`, pure)

`app/moments/fights.py` splits a round into fights and tags them. Its inputs are the round blob, the link (sides,
DB-only deaths) and, when available, the control engine's `RoundInputs` and geometry for the sight tags. It
reuses `app/coach/roundview.py` for positions and distances, and `app/coach/detectors.py`'s constants. All
thresholds live at the top of the module with their corpus numbers in a comment, as `detectors.py` does.

It needs the map's geometry for sight, which only the replay worker has loaded (the visibility table takes about
60 s to build per map; it is cached). So the moments are computed **in the control task** (`app/control/task.py`),
next to the gap detector, which already builds `RoundInputs(blob, geo, link)` for the same round. A map without
geometry still gets every tag except sight.

`MOMENTS_REVISION` starts at 1 and changes with any rule change; a stale row is recomputed like a stale gap run.

### Storage (migration 0026)

`replay_round_moments`: one row per replay round (`replay_id`, `round_number`, `status` ok/failed,
`fingerprint`, `moments_revision`, `fights` JSON, `error`, `computed_at`), cascade-deleted with the round, written
by `app/services/replay_moments_store.py`. The JSON holds slots, never names or player ids, like
`replay_gaps`.

A one-off `scripts/compute_moments.py` backfills the 55 stored replays.

### Endpoint

`GET /replays/{match_uuid}/moments.json`: every round's fights for all ten players, names filled in from the
link, with an ETag over the stored rows (`_json_with_etag`). The same 404s as the control routes
(`_control_replay_or_404`), except that a map without geometry still answers, without sight.

### Page

- A **Critical Moments** tab after Analysis in `replays/_player.html`, loaded when first opened.
- A player picker at the top. It defaults to the logged-in viewer when they played in the match, otherwise the
  first player of the attacking team in round 1.
- Groups, each with its count: Moving while firing, Alone, With a teammate, Died not traded, Traded, Traded a
  teammate, Utility on them, Utility near them, Died without seeing the killer, Killed unseen. A fight with
  several tags is in several groups.
- Each row: the round and clock time, the opponents, the outcome (kills / died / traded) and the measured
  numbers of that group's tag. Clicking a row moves the viewer to the round at 5 s before the fight's start
  (playbook rule 17), using the same jump the Analysis tab uses.
- One line under the tabs says what the tags are measured from and that they are not verdicts.

The demo never shows the tab: it has no replays (`store.refuse_demo`).

## Known limits, stated so nobody is surprised

- Speed is the body's speed from a 16 Hz track: it cannot tell a counter-strafe in the middle of a burst from
  standing still. A tag means "moving at the shot", not "inaccurate".
- "With a teammate" needs the teammate to have dealt damage. A teammate holding the same angle and never firing
  counts as nearby but not fighting.
- Sight is 2D lines with heights where the map has them. Doors, rope and elevated angles are where the error floor
  above comes from. "Not measurable" is shown honestly rather than guessed.
- The near-utility list of ability names needs the owner's review; until then an unlisted ability is not counted.

## Later

- The coach brief can take these fights as flags (`detectors.flags`), replacing the per-death flags with
  per-fight ones that already follow rules 15 and 16.
- Totals across a player's matches (the share of their fights taken alone, moving, and so on) for the player page.

## Testing

- Unit tests on hand-built blobs for each tag's edges: a 2.9 s and a 3.1 s gap, exactly 2.5 m/s, a dash shot, a
  shotgun, a trade at the window's edge, a teammate at 19.9 and 20.1 m, a flash hit before the window, a smoke
  near an enemy, a kill where the killer never saw the victim.
- A golden test on one real round of match `ac9fd52a`, whose fights were checked by hand on 2026-10-17 (round 33:
  with a teammate, 2.0 m; round 15: 3v3 before the fight; round 9: killed unseen).
- The corpus survey scripts are rerun after the build, and their numbers must match this spec's within rounding.

## What the owner still owes, at build time

- Review the ability-name groups for "utility near" (damage/deny, smoke, never).
- Confirm 2.5 m/s, 20 m and 8 m after seeing them on a few of their own fights in the built tab.
