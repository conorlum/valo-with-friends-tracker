# Handoff 07: Ult value, ult rankings and who should take orbs

Read `docs/handoffs/README.md` (ground rules), `CLAUDE.md` (especially "Critical Util") and
`docs/superpowers/specs/2026-10-21-critical-util-design.md` first.

## Goal

Three questions:

1. How much does each ult use add to its side's round-win chance?
2. Which ults are the most impactful, per agent and per friend?
3. Who should take ult orbs: whose ult points are worth most, so the team knows who to leave orbs for?

## What already exists (read first; a lot is built)

- **Ult timelines** per player per round in the blob's `player_state.ult` (from condense revision 19). The
  extractor's `_ults` (`app/critutil/extract.py`) records per slot: `cap_seen` (points held just before a drop to
  0, which proves the cost), `end` (points at death or the round's decision), `used`, and `use` (the use's
  evidence, including timed ults like Tour de Force shots).
- **Ult casts** are casts with kind `ult` (slot X in `app/critutil/abilities.json`); `_ult_cost` reads the points
  held before a cast.
- **Unused ults:** `app/services/replay_util.py` `unused_ults` lists full ults never used per player-round, with
  the cost proven from the match.
- **Values:** `app/services/util_values.py` values every cast with the ledger-and-jobs `value` and the no-util
  baseline `lift` (change in the side's win chance over the next 10 s vs similar moments without that kind of util
  up, shrunk toward the ability's prior). Ult-specific rules exist (e.g. an ult that is a gun takes the kill's gun
  share; an Omen ult that carries the spike to the plant has its own weight).
- **Orbs:** `UltPointOrb` actors with where they lie and when they close, capture tries (`orb_tries`) and the
  capturer (`OrbPickedUpRPC`), in `app/replays/player_state_extract.py`.
- **Win estimator:** `app/services/util_model.py`.

So the new work is mainly a view of existing values for ults, a longer window for ults, the rankings, and the orb
analysis.

## Check first (read-only, `scripts/critical_util.py show` / `measure` through `with_friends_db.py --read-only`)

- Every agent's ult is in `abilities.json` with its effects, and every use is detected (count uses per agent;
  compare with ult timelines' drops to 0).
- Which ults currently get a `lift` and a `value`, and with what n per agent.
- Whether the 10 s lift window is enough for ults that last longer (Viper's Pit, Killjoy's Lockdown, Brimstone's
  Orbital Strike lingering, Reyna's Empress, Neon's Overdrive).

## Part 1: value per ult use (declare before measuring)

- **Window:** for ults, a longer lift window: propose 20 s, or to the round's decision if sooner. Declare it per ult
  type if needed (a fixed window per agent's ult, in data).
- **Round-level check:** win rate in rounds where the ult was used vs similar rounds (same state at the moment of
  use, same side, round type) where that player had it ready and did not use it. This catches ults whose value
  is not in the first 20 s.
- **Selection effect:** players use ults in rounds they think matter or can win. Conditioning on state at the
  moment of use removes part of it; write down what remains.

## Part 2: rankings

- **Per agent's ult** across everyone in our replays (both teams; strangers unnamed): mean lift with CI and n, by
  side and by use context (attack execute, retake, post-plant, defence hold).
- **Per friend:** their ult's mean lift vs that ult's average.
- **Wasted ults:** died holding a full ult (from `_ults` and `unused_ults`); rounds a full ult was held without use
  (how many rounds in a row); ults used after the round was decided.

## Part 3: who should take orbs

- **Value of one point** for a player = (their ult's value per use, shrunk toward the agent's average) / (the ult's
  cost in points), adjusted for timing: a point that completes an ult this round is worth more than one that leaves
  the player still 3 short. Propose: value × P(the point is the one that makes the ult ready within the next 2
  rounds), from points held and the usual point gain per round.
- **At each orb capture:** which living teammates could have taken it (distance and time to reach from positions),
  whose point was worth the most, and who actually took it.
- **Report:** "orb taken by the best-value teammate X% of the time"; per map, where orbs are and who tends to take
  them.
- Transparent: show the table that produced each recommendation.

## Output

- `/util` gains an "Ults" section: the ranking table (per agent's ult and per friend), wasted ults per friend, and
  the orb table.
- Critical Util tab: a line per ult use with its lift over the ult window.
- Viewer: an orb note at capture ("taken by Name; best value: Name (1 point from ult)").

## Storage and pipeline

- If the ult values fit the existing util build, add them there: the build runs on the worker
  (`app/critutil/corpus.py`) and its digest (`app/critutil/build_digest.py`) must match between site and worker;
  any change to files the build reads changes the digest. Otherwise a separate stored product keyed by its own
  `corpus_key`, never built inside a request.
- Orb analysis from stored blobs and util rows, computed on read and cached, or added to the gap run's output if it
  needs positions at capture time (bump `GAPS_REVISION`).

## Tests

- Ult window selection, the round-level comparison on synthetic rounds, point-value timing, orb candidate
  selection. Add to `tests/suites.toml`.

## Phases and done criteria

1. Read-only check above. Done when the owner has n per agent and the coverage gaps.
2. Part 1 and Part 2 numbers in a report. Done when the owner has seen the ranking with CIs.
3. Part 3 report. Done when the orb table is reviewed.
4. UI. Done when `/util` shows it, tests pass, the demo shows nothing.

## Open questions for the owner

- Ult windows per agent.
- Whether strangers count in the agent rankings (default yes, unnamed).
- How to word orb recommendations (a table vs "should have been X").
