# Handoff 04: Crosshair placement and time to target

Read `docs/handoffs/README.md` (ground rules, shared building blocks) and `CLAUDE.md` first.

## Goal

Two aim measures per player, tracked across matches:

1. **Crosshair placement proxy:** at the moment an enemy first becomes visible to the player, the horizontal angle
   between where the player faces and the enemy.
2. **Time to target:** from that first sight to (a) facing within the on-target band and (b) the first shot and
   first damage.

## What already exists (read first; a lot of this is built)

Critical Moments (`app/moments/fights.py`, constants in `app/moments/constants.py`) already computes per kill:

- `spot`: when the killer first saw the victim (searched back `SPOT_LOOKBACK_S` 10 s at 16 Hz, `SIGHT_STEP_S`).
- `angle` and `band` at the spot: `_off` gives the horizontal angle between yaw and the bearing to the target;
  `_band` labels it "on" (≤ `CROSSHAIR_ON_DEG` 5°), "near" (≤ `CROSSHAIR_NEAR_DEG` 20°) or "off".
- `first_shot`, `ttk_shot` (kill time − first shot), `ttk_spot` (kill time − spot), and `clean` with a reason
  (`never_saw`, `no_shots`, `fired_before_spot`, `slow_start` over `CLEAN_SPOT_TO_SHOT_S` 1 s, `moving`).
- Per duel: `you_saw_t`, `they_saw_t`, `you_angle`, `they_angle` (`_duels`, `_first_sight`), and the tags
  `saw_first`, `seen_first`, `caught_off_angle`.
- These are shown in the replay's Critical Moments tab (`app/static/js/moments.js`,
  `app/services/replay_moments.py`).

So the new work is mainly:

- a **time-to-on-target** measure (when the angle first drops into the on band after the spot), which does not
  exist yet;
- measures for **every first sight**, not only kills (lost duels and fights with no kill count too: `_duels`
  already has the first-sight angle for every opponent in a fight);
- **aggregation across matches** and a player-page view;
- a check of whether these measures predict duel outcomes at all.

## Check first

- Yaw only, or pitch too? The blob stores yaw; search the parser export (`movement.ndjson`,
  `app/replays/contract.py`) for pitch. Without pitch, this is horizontal placement only, and the page must say so
  (head height cannot be judged).
- Real time resolution of yaw: 16 Hz on the grid, but is it interpolated from sparser samples? Measure how often
  yaw actually changes between consecutive samples during a fight. Do not report times finer than the data
  supports (at 16 Hz, one step is 62.5 ms).
- Whether `spot` is computed with the killer's view cone or only line of sight; the angle at "first visible"
  must use line of sight ignoring facing (otherwise a player facing away never "sees" the enemy and the angle is
  always small).

## Definitions (declare before measuring)

- **Event:** each (player, enemy) pair's first moment of clear line of sight in a fight, with the player alive and
  not blinded, stunned or nearsighted (status intervals), and the enemy alive.
- **Placement error:** the angle at the event, 0–180°. Also at +125 ms and +250 ms (2 and 4 samples).
- **On target:** angle ≤ an angular half-width that depends on distance: atan(body half-width / distance), with a
  floor of `CROSSHAIR_ON_DEG` at long range. Propose body half-width 0.4 m. So at 5 m on target is about 4.6°,
  at 20 m the 5° floor applies. Declare it.
- **Time to on target:** first sample at or after the event with angle ≤ on-target width. Censored if the fight
  ends first.
- **Time to first shot / first damage:** from the event to the player's first shot / first gun damage on that
  enemy.
- **Exclusions:** the enemy appeared behind (> 90°: that is a gap/back-shot, `app/gaps`), the player was mid-flick
  or turning fast before the event (yaw speed over the last 250 ms above a threshold: report as its own class
  "turning"), the enemy was behind smoke (spam), the player was using a non-gun ability.

## Splits

- Distance bands from `app/replays/gun_ranges.json`.
- Who exposed whom: the player was holding (speed < 0.5 m/s over the last 0.5 s) vs moving into the enemy
  (overlap with handoff 16, peek types).
- Weapon class (`app/replays/guns.json`).
- Common angle vs not: was the enemy at a spot many enemies appear from (a high-traffic cell for that map)? Hard;
  optional.

## Does it predict anything?

Logistic regression of duel won on placement error (and time to target), controlling for distance band,
holding/moving, weapon, health, with a cluster bootstrap by match. Report the effect per 10° of error. If the
effect is null, the page shows the metric without implying it matters, and the owner decides whether to keep it.

## Validation

Pick 20 events across 3 replays; list (round, time, player, enemy, angle, time to on target) so the owner can step
through them in the viewer, where yaw cones are drawn. Agreement first, numbers second.

## Output

- **Player page:** median placement error with IQR and n, share on/near/off, median time to on target and to
  first damage, by distance band, and a trend over matches (a simple line per match with n per point).
- **Friends table:** the same measures for each friend, plus "everyone in our replays" (strangers pooled,
  unnamed) as the reference line.
- **Critical Moments tab:** add time to on target to the existing angle/ttk lines.
- **Coach:** these numbers are already partly in the brief through Critical Moments; extend with logged ids if
  added.

## Storage and pipeline

- New fields in the Critical Moments output (the gap run on the worker): bump the moments `VERSION` and
  `GAPS_REVISION`, so stored rows are recomputed; the worker image ships `app/moments`.
- Aggregates computed on read and cached with the player view caches (new scope in `player_view_cache`,
  invalidated on ingest/link as today).

## Tests

- `_off` and the distance-dependent on-target width on synthetic positions; time to on target with a synthetic
  yaw track; exclusions (behind, blinded, turning). Add to `tests/suites.toml`; run the slow lane.

## Phases and done criteria

1. Read-only report: yaw resolution, pitch presence, placement error for every first sight in 3 replays, 20
   events for checking. Done when the owner has checked them.
2. Time to on target and the predictive check. Done when the effect on duel wins is reported.
3. Worker fields, aggregation, UI. Done when tests and the slow lane pass and the demo shows nothing.

## Open questions for the owner

- The on-target width rule.
- Whether to show strangers' reference line.
- Whether to keep the feature if it does not predict duel results.
