# Handoff 16: Peek types and win rates

Read `docs/handoffs/README.md` (ground rules, shared building blocks) and `CLAUDE.md` first. This is hard:
measure feasibility and agree labels with the owner by hand before a design spec, then code.

## Goal

Label how each fight started for the player (jiggle, wide swing, held angle, re-peek), then show win rates by type,
distance and player: "you win 61% of wide swings and 34% of re-peeks". Related to handoff 04 (crosshair): share
its first-sight event if built.

## What exists

- Positions, yaw and z at 16 Hz in the blob (one sample every 62.5 ms).
- Sight per pair: Critical Moments' `_first_sight` and `_sees_point` (`app/moments/fights.py`); duels per fight with
  `you_saw_t`, `they_saw_t`, angles; the tags `saw_first`, `seen_first`, `caught_off_angle`.
- Speed: `_speed` and `_moving` in fights.py, `MOVING_MPS` 2.5, `SPEED_MAX_SPAN_S` 0.2; movement casts are
  excluded from speed (`DASH_EXCLUDE_S`).
- Fight results and kills, time to kill.
- Distance bands (`app/replays/gun_ranges.json`), weapon classes (`app/replays/guns.json`).

## Check first

- Is 16 Hz enough? A jiggle exposes for about 0.2–0.5 s: 3 to 8 samples. Measure whether positions are true samples
  or interpolated, and how much they jitter while standing still. If a jiggle cannot be told apart from noise,
  drop the jiggle type and say so.
- Who opened the sight line: the player moving into the enemy's sight, the enemy moving into theirs, or both.
  This is the core of the labels.

## Draft types (agree with the owner; per player per fight opening)

- **Held angle:** the player was still (speed under ~0.5 m/s over the last 1 s) and the enemy came into their sight.
- **Wide swing:** the player moved into the enemy's sight at speed and kept going well past the corner (sideways
  travel more than X m after first sight; propose 1.5 m).
- **Jiggle / shoulder:** a short exposure (under ~0.4 s) followed by a return to cover, possibly repeated.
- **Re-peek:** the player had lost sight of this enemy within the last few seconds (or traded damage with them) and
  exposed again from about the same spot (within 2 m).
- **Other:** jumps, drops, off-angles, util-forced fights. Do not force a label.

"Cover" is where the player had no sight to the enemy just before: from the sight mask, find when line of sight
opened and on which side of the corner each player was.

## Method

1. For each fight opening: who opened the line, the player's speed and sideways travel before and after, how long
   the exposure lasted, earlier sight of the same enemy in the last N s, distance, weapon.
2. Rule-based labels. Label 50 openings with the owner in the viewer; tune thresholds; freeze them in the spec.
   Report agreement. If agreement is low, stop and report rather than building on it.
3. Outcome: duel won (Critical Moments' result), damage dealt and taken, time to first damage.
4. Per player × type × distance band: win rate shrunk toward the type's average across all players in our replays
   (strangers unnamed), intervals by match.
5. Sanity check: peeker vs holder win rate overall; the known peeker's advantage should show up. If it is far off,
   look for a labelling bug first.

## Output

- **Critical Moments tab:** a peek-type tag per fight ("wide swing, 7 m, won").
- **Player page:** a table of type × win rate with n, against everyone's average; the most common losing type with
  example links into the viewer.

## Storage and pipeline

On the worker in the gap run beside Critical Moments (new fields, moments `VERSION` and `GAPS_REVISION` bump, the
same failure status), after a read-only script proves it.

## Tests

Synthetic tracks for each type around a synthetic corner: held, wide, jiggle, re-peek; noise robustness; who-opened
logic. Add to `tests/suites.toml`; run the slow lane before merging gap-run changes.

## Phases and done criteria

1. Read-only: the sample-rate and jitter report, then the draft rules on 3 replays with 20 (round, time, player,
   label) for checking. Done when the owner has checked them.
2. Hand labelling session and frozen thresholds. Done when agreement is reported.
3. Worker fields and UI.

## Open questions for the owner

- The type list.
- Thresholds after the labelling session.
- Whether "who opened the line" should also show on its own (peeker vs holder) as a simpler first feature.
