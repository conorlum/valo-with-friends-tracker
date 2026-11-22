# Handoff 08: What makes players play better?

Read `docs/handoffs/README.md` (ground rules) and `CLAUDE.md` first.

This is research. Write a pre-declared analysis plan in `docs/superpowers/specs/` for the owner's approval,
measure, and only then propose a page. Findings name friends, so the findings report stays out of the public
repo (local, uncommitted); the code and the plan can be committed.

## Questions (primary, declared up front)

1. After a multikill round (3k or more), does the player play better in the next rounds?
2. After getting the round's first kill, does the player play better in the next rounds?

## Exploratory (reported, all of them, nulls included)

Winning the previous round; winning a clutch; a team ace; the team's win or loss streak; a comeback from a deficit;
full buy after a save; pistol round won or lost; the first-half result; the session's match number (from
`app/services/sessions.py`); time of day; who is in the squad; the agent; the map; being ahead or behind in kills
in the match.

## Data

- **tracker.gg data** covers every match and is the main data set: `rounds` (outcome, plant), `kill_events` (with
  times), `round_player_stats` (kills, deaths, assists, loadout, remaining), `round_player_spend`, `impact_scores`.
- **Replays** add duel details for a smaller set: Critical Moments (who saw first, duel results, time to kill).
- **Existing services to reuse:** `app/services/session_stats.py`, `match_streaks.py`, `round_streak_stats.py`,
  `fight_ev.py` (duel conversion per man-advantage state against a teammate benchmark), `state_replay.py`
  (duels with states from the kill feed), `stats_math.py` (logistic fits, cluster bootstrap).

## The trap (the plan must address each)

- **Good day / weak lobby:** a player who just got a 3k is probably having a good match or facing weaker
  opponents. Comparing their next rounds to their career average finds "momentum" that is just that. Use
  **within-match comparisons**: rounds after the event vs the same player's other rounds in the same match (match
  fixed effects, or a paired difference per match).
- **Regression to the mean:** the event round itself is extreme by selection. Never compare to the event round.
- **Economy:** a multikill often means a better buy next round. Control for the player's and both teams' loadout.
- **Outcome measures tied to the round result** are circular: avoid round wins as the main outcome.
- **Side and half:** the side changes at round 13; control for side and round number.
- **Multiple testing:** declare the two primary questions; treat the rest as exploratory and report all of them.

## Performance measures (declare one primary)

- **Duel win rate** in the next K rounds, from the kill feed: kills / (kills + deaths) in fights, preferably
  adjusted for man-advantage state (fight-EV's conversion against the state's benchmark).
- **Opening duel won** (first kill vs first death) in rounds where the player takes the opening duel.
- Kills per round, Impact per round (`impact_scores`), first-blood rate as secondary measures.
- Replay-only secondary: saw-first rate, time to kill, crosshair band (handoff 04).

Propose: primary = state-adjusted duel win rate over the next K = 1, 2, 3 rounds.

## Method

- One row per (player, match, round): performance in round r+k, the condition at round r, controls (loadouts,
  side, round number, score difference, man-advantage states fought in).
- **Model:** logistic (for duel outcomes, one row per duel) or linear (per round) with a match-by-player fixed
  effect, or the equivalent paired comparison. Report the effect as percentage points of duel win rate.
- **Pooled first** across all players in our matches (the friend group plus strangers in those matches, numbered,
  never named; strangers add many rows), then **per friend** with shrinkage toward the pooled effect.
- **Intervals:** cluster bootstrap by match.
- **Placebo check:** the "effect" of a multikill in round r on round r−1 (before it happened) should be zero. If
  it is not, the controls are not doing their job.

## Output

1. A findings report (local, uncommitted): every condition's effect with CI and n, the placebo check, and plain
   conclusions.
2. If something holds up, a "What brings out your best" section on the player page: the conditions with the
   largest effects for that player, each with n and a CI, worded as "your duel win rate is 6 points higher in the
   two rounds after...", never as a cause.

## Tests

Pure tests for row building (no future data leaks into controls), the within-match comparison on a simulated data
set with a planted effect and with none, and the placebo check. Add to `tests/suites.toml`.

## Phases and done criteria

1. Counts: player-rounds with each condition. Then question 1 alone with the within-match comparison and the
   placebo check. Done when the owner has the effect, CI and n.
2. Question 2 and the exploratory list. Done when the full report exists.
3. Page, if the owner wants it.

## Open questions for the owner

- The primary performance measure and K.
- Whether strangers count in the pooled model.
- Whether "who is in the squad" results are shown on the site (they could cause arguments).
