# Handoff 01: Opening predictability and prediction scoring

Read `docs/handoffs/README.md` (ground rules, shared building blocks) and `CLAUDE.md` first.

## Goal

Measure how predictable each friend, and the five-stack as a team, is in the first part of a round, and score
it the honest way: build a model that tries to predict where they go, and report how often it is right on
rounds it never saw.

The question we want answered on the page: "If an opponent had watched our last 20 Ascent attack rounds, how
often could they call where our Sova goes, and does it cost us when they can?"

Three outputs, in order of priority:

1. **Openers.** Each player's usual routes per map and side, with how often each is used.
2. **Prediction score.** How well a context-aware model predicts the opener, compared with "always guess the
   most common one". Entropy in bits as a plain spread measure.
3. **Does it matter.** Round results and fight results when a player used their usual opener vs a rarer one,
   and timing tells (arrivals at chokes that hardly vary).

## What already exists

- Positions, yaw and z per player at 16 Hz in the condensed round blob, plus the barrier drop and buy phase
  (`app/replays/condense.py`, `format.py`; barrier handling in the player-state design,
  `docs/superpowers/specs/2026-10-06-replay-player-state-design.md`).
- Callouts with a `super` region (A, B, Mid...) per map in `app/static/data/callouts.json`, and the map's chokes
  in `app/static/data/control/<Map>.chokes.json`. Reviewed area paint in `control/<asset>.areas.json`
  (`app/services/replay_areas.py`).
- First contact: Critical Moments fights (`app/moments/fights.py`) give every fight's start time per player.
- Round context: side (`state_replay.attacking_team_for_round`), round type from loadouts
  (`round_player_stats.loadout`, `remaining`; existing round-type logic in `app/services/force_buy_stats.py`,
  `eco_followup.py`, `economy_graphs.py` — reuse the existing classifier, do not write a new one), score, the
  previous round's result.
- Statistics helpers: `app/services/stats_math.py` (log loss, cluster bootstrap).

## Definitions (declare in the spec before measuring)

- **Opening window.** From barrier drop to the earliest of: T seconds after the drop (propose 20 s on attack,
  15 s on defence), the player's first fight start, or their death. Measure the distribution of first-contact
  times per map first and pick T so most openings are not cut short.
- **Route.** The sequence of named areas a player's position passes through in the window, with repeats
  collapsed and areas entered for less than ~0.5 s dropped (noise at boundaries). Area = the callout whose
  painted region contains the position; fall back to the nearest callout point if paint is missing.
- **Opener.** A route truncated to its first K areas (propose K = 3), or the route's final area in the window
  (where they set up). Try both; choose the one that gives stable clusters with interpretable names. Only move to
  trajectory clustering (DTW or discrete Fréchet with k-medoids on resampled paths) if callout-level routes turn
  out too coarse or too noisy; say which and why.
- **Team default.** The multiset of five openers on one side in one round. On defence this is the setup.
- **Unit of analysis.** Per player per map per side per layout. Optionally per player + agent; decide with the
  owner (an agent change usually changes the route).

## Prediction scoring (the core)

- **Baseline A:** predict the player's most common opener on that map and side (from the training matches).
- **Model B:** a frequency table over context with shrinkage toward the parent context, like `util_model`:
  context keys from most to least specific, e.g. (round type, previous result, last opener) → (round type) → ().
  Each cell's distribution is pulled toward its parent with a prior of M observations (start with M = 5).
  Transparent and explainable; no gradient boosting or neural model.
- **Evaluation:** leave-one-match-out. Every round in match m is predicted by a model built only from the
  player's other matches. Report top-1 accuracy, top-2 accuracy, log loss, and Brier score; the gain of B over A.
- **Predictability score** shown on the page: B's top-1 accuracy with a cluster-bootstrap CI by match. Also
  the Shannon entropy of the opener distribution in bits (0 = always the same) as the simple spread measure.
- **Team predictability:** predict the full five-player default (exact match), and the number of players whose
  opener is predicted correctly per round.
- A floor: show nothing for a player/map/side with fewer than N rounds (propose 15) or fewer than 3 matches.

## Does it matter?

- Round win rate, first-fight win rate and Impact per round when the player used their most common opener vs
  anything else, by map and side, with CIs, controlling for round type (an eco opener differs anyway).
- Predicted-correctly vs not: did rounds where model B would have called the opener go worse? This is the
  question that makes the feature useful and the one most likely to come back null with our sample. Report it
  either way.
- **Timing tells:** per choke (chokes.json) and per round type, the first arrival time after barrier drop.
  Flag a timing as a tell when its spread (IQR) is under ~1.5 s over at least 8 rounds.

## Validation

- Pick 20 rounds; draw each player's opening path over the minimap with the route label next to it, and have the
  owner confirm the labels look right.
- Sanity check: strangers' rounds in our replays (both teams are recorded) should show higher entropy on
  average than a five-stack's defaults, or at least the same; a five-stack that is more random than solo-queue
  strangers would suggest a labelling bug.

## Output

- **Player page:** a "Predictability" section per map and side: up to four openers drawn as small minimap path
  thumbnails with their share and n, the predictability score with its CI, entropy in bits, the timing tells,
  and the "does it matter" line. Links to example rounds (`/replays/<uuid>?round=n&t=...`).
- **/squad:** the team's defaults per map and side, how often the same default repeats, the team prediction
  score.
- Respect account groups (`?accounts=all`) and the role/agent filter if cheap; otherwise say they are not
  supported yet.

## Storage and pipeline

- Phase 1 is a read-only script: `scripts/predictability.py measure` through `with_friends_db.py --read-only`.
- If kept: compute openers per replay round once (pure function over the blob, cheap) and store them in a small
  table (`replay_round_openers`: replay, round, slot, layout key, route, opener, window end, revision), filled
  when a replay is linked and by a backfill script. Scores are computed on read and cached in the player view
  caches (`player_view_cache` with a new scope, or a separate cache with the same invalidation rules).
- No background loop needed if computed on link; if one is added, it checks `deploy_drain.paused`.

## Tests

- Pure tests for route extraction (synthetic tracks crossing painted areas, boundary noise), opener truncation,
  the shrunk predictor, and leave-one-match-out evaluation (no leakage: a test that the model for match m never
  saw match m's rounds).
- Add suites to `tests/suites.toml`.

## Phases and done criteria

1. Feasibility (read-only): rounds per player/map/side; first-contact time distribution; openers for one map;
   baseline A's accuracy. Done when the owner has seen the opener table and sample drawings.
2. Model B and evaluation on all maps. Done when the gain over A, with CIs, is reported for every player with
   enough rounds.
3. "Does it matter" and timing tells. Done when results are reported, nulls included.
4. UI and storage. Done when the player page and /squad show it, tests pass, nothing appears on the demo.

## Open questions for the owner

- The window: fixed T, or until first contact.
- Per player, or per player + agent.
- The minimum sample before a score shows.
- Whether strangers' openers are shown anywhere (as an anonymous reference only), or only used for sanity checks.

## Pitfalls

- Spawn positions are fixed per side; ignore the buy phase entirely or every route starts the same.
- Layout changes rewrite routes: never pool across layouts.
- Small samples make everyone look predictable with a lookup model; leave-one-match-out is what stops that.
