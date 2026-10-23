# Fight swing: expensive fights in Critical Moments

Status: approved by the owner 2026-10-23 ("write the plan and build it out"); first build on
`worktree-fight-swing`. Research lives outside the repo (it names real players).

## What it is for

Critical Moments lists fights by tags that measure what happened. Fight swing adds one more: a fight that cost
the team a lot of map control relative to what it took from the enemy. These are fights a player should not
have taken, or should not have put themselves in position to take, or at least should look at. It is a
measurement, not a verdict: the owner judged a forced fight and a failed trade as worth looking at, and an
unlucky one as not.

## Definition (declared before the build; values from the research below)

- **A team fight** is the round's live deaths (either side, `t_start <= t <= t_decided`) chained while each is
  within `CHAIN_S = 6 s` of the previous one (the repo's trade window). A disconnect (a life ending with cause
  `left`) is not a death.
- **Before / after.** Held ground is the mean over `AVG_S = 2 s` of stored frames, ending `PRE_S = 1 s` before the
  first death (before) and ending `POST_S = 5 s` after the last death, capped at `t_decided` (after).
- **The fight's ground** is every walkable cell within `NEAR_M = 20 m` (flat distance) of any of the fight's
  deaths, plus, for each dead player on a setup sentinel (Chamber, Cypher, Deadlock, Killjoy, Veto, Vyse), the
  cells their removal would have lost (`control_masks`) `HELD_LEAD_S = 0.5 s` before they died, where their team
  held or contested the cell. A sentinel's trips and camera going down is real lost control wherever it is
  (owner, 2026-10-23); everyone else's far-off ground mostly moves for reasons unrelated to the fight.
- **Lost ground** of a side group = its held cells (passive, safe or active) in the fight's ground, before
  minus after, in m².
- **Swing** for a side = enemy lost - own lost. Negative: the fight cost us more than it cost them.
- **Wipes are left out**: a fight after which either side has nobody alive is judged another way (owner).
- **Expensive fight** (the tag): not a wipe, and swing <= `-EXPENSIVE_FIGHT_M2 = -500 m²`, on the fight of every
  player who died in it.

## Evidence (research, 2026-10-23)

79 graded deaths from 5 matches, 55 non-wipe team fights. Agreement (Spearman) with the fight's kill
difference: map-wide +0.26, 20 m +0.49, 20 m plus sentinel ground +0.44; on the 29 fights with no setup agent
among our dead: map-wide +0.33, 20 m +0.62. The owner judged 9 fights: all 7 they would flag rank above both
they would not (ranks of 55: flagged #1, #3, #5, #8, #9, #12, #16; not flagged #18, #43). The cut-off falls
between a forced fight at -465 m² and a reasonable trade attempt at -460: the difference there is the
decision, which control does not see. The chosen variant was picked after seeing those 9 judgments.

Rejected on the way: map-wide ground (one second of inferred ground can flip 3,800 m² far from any fight), a
40 m radius (too large), every dead player's removal mask (late in a round it is nearly the whole map),
active-only ground (tracks the kills worse).

## Where it is computed and stored

- `app/control/fight_swing.py`: pure (numpy only), `round_fights(...)` from the round's deaths, teams, agents,
  lives, death positions and the engine's stored frames (`states`, `control_masks`). Constants live there.
- `engine.compute_round` calls it after the tick loop; `RoundControl.fights` holds the list.
- `encode.encode_summary` stores it as `summary["fights"]`: per fight `t0`, `t1` (first and last death),
  `deaths` `[[slot, t, group], ...]`, `wipe`, `measured` `[t_before, t_after]`, `area_m2`, `lost_m2`
  `{"A": .., "B": ..}`.
- `CONTROL_REVISION` 19 -> 20: every stored round is recomputed by the replay worker after deploy, as for any
  revision. A round stored before has no `fights` and shows no tag. (The unshipped statue work on
  `worktree-lost-control-fixes` also wanted 20; whichever merges second takes 21.)

## Where it is shown

- `app/services/replay_moments.py` (pure): for each fight in which the player died, `team_fight` = the team fight
  holding that death (within `DEATH_MATCH_S`): `t0`, `t1`, `ours`, `theirs` (deaths per side), `swing_m2` for the
  player's side, `wipe`; and the tag `expensive_fight` on the flagged ones. The player summary gets
  `expensive_fights`: their up to 3 most negative non-wipe team fights. `load_moments_payload` reads the
  summaries it already loads.
- `app/static/js/moments.js`: a "Most expensive fights" list under "Most expensive deaths", and an "Expensive
  fight" group, each row "fight swing -1,551 m² (4 for 1)". Signed values carry their sign in the text.

## Tests

- `tests/replays/test_fight_swing.py`: chaining, before/after windows and the `t_decided` cap, the 20 m area,
  the sentinel-only mask (masked to held or contested cells), wipe detection, dead-time and post-decision
  deaths ignored, empty rounds.
- Engine: `compute_round` on a toy map stores `fights` and the summary round-trips it.
- `tests/replays/test_replay_moments.py` (or its existing home): attach, tag threshold, wipes untagged, summary
  order; `tests/replays/test_moments_viewer.py`: the row text and the new list.
- Revision pins (`test_control_format`, `test_control_figures`, replay format pins) move with the bump.
- `-m slow` before merging (the change touches `app/control`).

## Out of scope / later

- Tuning the cut-off on more owner judgments (the #10-#20 range is where it falls).
- Wipes, judged another way.
- Attributing a fight to the player who started it rather than to everyone who died in it.
