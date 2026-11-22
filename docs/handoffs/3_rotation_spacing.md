# Handoff 03: Tradeable deaths, spacing and rotations

Read `docs/handoffs/README.md` (ground rules, shared building blocks) and `CLAUDE.md` first.

This is hard. Work in this order: a feasibility measurement, a short research note, a design spec for the
owner's approval, then code. Do Part A (spacing) fully before starting Part B (rotations).

## Part A: tradeable deaths and spacing

### Question

When a friend died, could a teammate have traded the killer? If not, whose spacing was it: the victim's
(isolated) or the team's (nobody close)?

### What already exists

- Critical Moments already records trades per fight (`app/moments/fights.py` `_trade`) with
  `TRADE_WINDOW_S` = 6 s (pinned equal to Impact's trade window by a test) and `TRADE_RANGE_M` = 20 m, and
  teammates fighting alongside (`_mates`, `WITH_AIM_DEG`). Read these first: part of Part A may be a new view of
  existing data plus a "could have" check.
- `/squad` counts trades between friends (`app/services/squad.py`).
- Sight and walkability: the control engine's sight mask and walk grid (`app/control/engine.py`,
  `app/control/geometry.py`), route distances on the walk grid (`app/control/route_runtime.py` `topology_for`).

### Definitions (declare before measuring)

- **Could trade:** a living teammate T could have got line of sight to the killer's position within W seconds of
  the death, moving at run speed along the walk grid (path distance / run speed ≤ W), and was not blinded,
  stunned or otherwise disabled at the time (status intervals in `player_state`). Propose W = 3 s, distinct from
  the 6 s window for "was traded" (a trade within 6 s that needed 5 s of running is a slow trade, not a quick one).
- **Sight to the killer's position:** the set of cells from which the killer's cell is visible (sight mask, with
  that moment's smokes). The nearest such cell by path distance from T.
- **Outcome classes per death** (one each):
  - traded (within `TRADE_WINDOW_S`);
  - tradeable, not traded (someone could, nobody did);
  - untradeable, isolated (no teammate within reach, while the team was grouped elsewhere: the victim was alone);
  - untradeable, team spread (no teammate within reach of anyone: the team was spread out);
  - not applicable (last alive, post-round, killed by util or the spike).
- **Isolated vs spread:** compare the victim's distance to the nearest teammate with the team's median nearest-
  teammate distance at that moment. Declare the rule.

### Measures

- Per player: share of deaths in each class, by map, side and phase (pre-plant/post-plant), with n.
- Per teammate: "could have traded and didn't" counts (who left whom), with the reason when visible (was looking
  away, was in another fight, was disabled).
- Per pair of friends: median path distance while both alive, share of each other's deaths that were tradeable,
  trade rate when tradeable.
- Team spread over a round: mean pairwise path distance and the largest group within 15 m, sampled every 2 s,
  compared between won and lost rounds by side.

### Validation

Label 40 deaths with the owner in the viewer (round, time list) before trusting the classes; report agreement.

## Part B: rotations

### Question

When a player rotated, what triggered it, when did they leave and arrive, and how did similar situations go
without a rotation?

### Definitions (declare before measuring)

- **Regions per map:** site A, site B (and C), mid, attacker side, defender spawn, built from callouts' `super`
  field plus a small per-map data file for anything the `super` field does not cover. Data, not code. The
  owner may want to draw or adjust them in the tagger; ask.
- **A rotation:** a defender (or an attacker lurking on the far side) whose region changes from one site region
  to the other (possibly through mid or spawn) within the round's live time, staying in the destination for at
  least 2 s. Also "partial rotation": leaves a site toward the other and turns back.
- **Trigger:** the last information event the player's team had before the player started moving: an enemy seen
  by any teammate, a sound locating an enemy (the engine's locating events, `Unknown._locating`; hearing rules in
  `docs/superpowers/specs/2026-10-14-hearing-attribution-design.md`), a teammate's death, a reveal from util,
  a spike seen or the plant. "No visible trigger" is a valid class (a call we cannot see, or a guess).
- **Timing:** trigger to departure, departure to arrival, arrival relative to the plant and to the first fight at
  the destination.

### Value

Compare rounds with a rotation to rounds in a similar state without one, using the round-win estimator
(`app/services/util_model.py`) for the state, the way Critical Util's no-util baseline does (`util_values.py`,
`lift`): the change in win chance over the next 15 s against matched moments. Shrink toward 0 with a prior and
show CIs. Expect very small samples per map; say so on the page.

### Classes to show (no good/bad wording)

- rotated on info; rotated with no visible info (a possible fake); stayed while the other site fell; rotated
  and arrived after the plant; rotated and arrived before the plant.

## Output

- **Critical Moments tab:** a tag on each death ("tradeable: Name 11 m away, not traded" — friend names only,
  opponents by agent) and on each rotation ("left B 2.1 s after Mid contact, arrived at A 0:41, 3 s after plant").
- **Player page:** death classes and rotation timing summary.
- **/squad:** pair spacing and "left untraded" table.
- **Coach:** later, as facts with logged ids the checker trusts (`app/coach/checker.py`); ask the owner.

## Storage and pipeline

- Phase 1: read-only script over stored blobs (`scripts/spacing.py measure`).
- If kept: computed on the worker in the gap run next to Critical Moments (it needs the walk grid and sight), with
  its own result key and failure status, stored per round like `replay_round_moments` (new table or new fields;
  bump `GAPS_REVISION`). The worker image must ship the new module.

## Tests

- Synthetic map grids: a teammate behind a wall at 10 m flat but 40 m path is not tradeable; a blinded teammate is
  not; a teammate in another fight is flagged. Rotation detection on synthetic tracks with boundary jitter.
- Add to `tests/suites.toml`; run the slow lane before merging gap-run changes.

## Phases and done criteria

1. Part A feasibility on 3–5 replays: a table per death (victim, nearest teammate path distance, sight reachable
   within W, traded). Done when the owner has checked it in the viewer.
2. Part A full build and UI.
3. Part B feasibility: rotation detection and trigger finding on 5 replays, checked by eye.
4. Part B value and UI.

## Open questions for the owner

- W for "could trade"; run speed vs walk speed for reach.
- How regions are defined per map (from `super`, hand-drawn in the tagger, or both).

## Pitfalls

- Flat distance lies across walls and floors: always use path distance on the walk grid.
- A teammate who could see the killer's cell may still not see the killer (smoke, facing). "Could trade" is about
  position; facing is reported separately.
- Rotations are rare per map; resist adding thresholds until there is data.
