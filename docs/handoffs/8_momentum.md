HANDOFF 8 — What makes players play better? (momentum / hot hand)

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first. Research first: a pre-declared analysis plan in docs/superpowers/specs/ for my approval, then measure, then build a page only if something holds up. Never on the demo. Every figure with n and a CI. Opponents never named.

QUESTIONS
1. Does getting a multikill (2k/3k/4k/ace) make a player play better in the following rounds?
2. Does even one kill early in a round (or getting the first kill) raise their performance later?
3. More broadly: what conditions go with a player playing above their own normal? Candidates: winning the previous round, a clutch, a team ace, a big swing on the score (win streaks, comebacks), economy (full buy after a save), pistol round results, a session's match number, time of day, who's in the squad, the agent, the map, the first-half result, being ahead/behind in kills.

DATA
- tracker.gg data in the DB covers many matches: rounds, round_player_stats, kill_events with times, impact_scores, economy (round_player_spend). This is the main data set (much larger than replays).
- Replays add positions and fight details (Critical Moments: duel win rate, saw first) for a smaller set.
- Existing services: session_stats.py, match_streaks.py, round_streak_stats.py, fight_ev.py (duel conversion by man-advantage state with a teammate benchmark), stats_math.py (cluster bootstrap), win_probability.py (cross-fitting rules: read its docstring).

THE TRAP (put this in the plan)
A player who just got a 3k is probably on a good day, or against weaker opponents. "Better after a 3k" can be that, not momentum. And performance measures that depend on the round outcome are circular. Control for it:
- Compare within the same match: rounds after a multikill vs the player's other rounds in the same match (match fixed effects). This removes "good day / weak lobby".
- Outcome measures that are per-duel and state-adjusted: duel win rate in the next K rounds (opening duels especially), fight EV against the benchmark (fight_ev.py), kills per round, Impact per round, first-blood rate. Avoid round wins as the main outcome.
- Control for economy (a multikill often means a good buy next round), side, and round number.
- Regression to the mean: a 3k round is extreme by definition. The comparison is the player's baseline, not the 3k round.
- Multiple testing: many conditions will be tested; declare the main questions first, treat the rest as exploratory, report all of them (including nulls).

METHOD
- Per round per player: a performance score (pick: opening duel won, duel win share, Impact).
- Model: performance in round r+k ~ condition at round r + match fixed effect + economy + side + round number, a logistic or linear mixed model, or a simpler matched comparison. k = 1, 2, 3, the rest of the half.
- Pooled across all players first (the friend group + strangers in the same matches, unnamed), then per friend with shrinkage toward the pooled effect.
- Cluster bootstrap CIs by match.

OUTPUT
- A short findings write-up first (a markdown report in the scratchpad or a local file I can read; it names friends, so don't commit it — research stays out of the public repo, like fight swing's).
- If it holds up: a "What brings out your best" section on the player page: the conditions with the largest per-player effects, each with n and a CI, worded as associations, not causes.

OPEN QUESTIONS FOR ME
- The main performance measure, and K.
- Whether strangers count in the pooled model (more data; they're never named).

FIRST STEP
Read-only: count player-rounds with each condition, then answer question 1 alone with the within-match comparison (duel win rate in the next 1-3 rounds after a 3k+ vs the player's other rounds in that match). Report the effect, the CI and n before going wider.
