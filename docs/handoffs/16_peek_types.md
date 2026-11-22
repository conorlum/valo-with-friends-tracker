HANDOFF 16 — Peek types: jiggle, wide swing, held angle, re-peek

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first. This is hard: research and a hand-labelled check first, then a design spec in docs/superpowers/specs/ for my approval, then code. Never on the demo. Opponents never named. Every figure with n. Measurements, not verdicts.

GOAL
Label how each fight started for each player, then show win rates per type, distance and player: "you win 61% of wide swings and 34% of re-peeks". Related to handoff 04 (crosshair/yaw); share its first-sight event if built.

WHAT EXISTS
- Positions, yaw (no pitch: check) and z per tick in condensed blobs (app/replays/condense.py; check the real sample rate GRID_HZ and smoothing first; a jiggle is ~0.2-0.5 s, so the data may be too coarse: find out before anything else).
- Sight per pair from the control engine (app/control/engine.py); fights and "saw first" from Critical Moments (app/moments/fights.py), which already tags moving while firing (speed > 2.5 m/s per shot).
- Shots and damage events; gun_ranges.json, guns.json for distance bands and weapon classes.

DRAFT TYPES (agree with me; per player per fight opening)
- Held angle: the player was still (speed < ~0.5 m/s over the last 1 s) and the enemy came into their sight.
- Wide swing: the player moved into the enemy's sight at speed, crossing well past the corner (lateral travel > X m after first sight).
- Jiggle / shoulder: short exposure (< ~0.4 s) and back to cover, repeated or followed by a re-expose.
- Re-peek: the player had lost sight of this enemy (or traded damage) within the last few seconds and exposed again to the same enemy from the same spot.
- Off-angle / jump / other: what's left. Don't force a label.
Each needs "cover": the cell where the player had no sight to the enemy just before. Use the sight mask to find when line of sight opened and which side of the corner each player was on.

METHOD
1. For each fight opening: the moment line of sight opened between P and E (who moved into whom: whose movement opened it), P's speed and lateral travel before/after, exposure duration, prior sight of E in the last N s.
2. Rule-based labels first. Hand-label 50 openings with me in the viewer; tune thresholds; freeze them in the spec. Report the agreement rate; if it's low, say so and stop.
3. Outcome: duel won (Critical Moments' fight result), damage dealt/taken, time to first damage.
4. Per player × type × distance band: win rate shrunk toward the type's average across all players in our replays (strangers unnamed), CI by match (stats_math.py).
5. Peeker's advantage check: peeker vs holder win rate overall, as a sanity check against what's known.

OUTPUT
- Critical Moments tab: a peek-type tag per fight ("wide swing, 7 m, won").
- Player page: a table of types × win rate with n, vs everyone's average, and the most common losing type with example links into the viewer.

WHERE IT GOES
On the worker in the gap run beside Critical Moments (new fields, revision bump, its own failure status), after a read-only script proves it. Add suites to tests/suites.toml; run the slow lane before merging gaps/control changes.

FIRST STEP
Read-only: report the position/yaw sample rate and smoothing, then label 3 replays' fight openings with the draft rules and give me 20 (round, time, player, label) to check in the viewer.
