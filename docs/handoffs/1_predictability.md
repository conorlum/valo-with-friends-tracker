HANDOFF 1 — Opening predictability + prediction scoring

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first. Write a design spec in docs/superpowers/specs/ and get my approval before any code. Never on the demo (demo_mode). Opponents are never named (app/services/enemy_names.py). Every figure shows its sample size.

GOAL
Show how predictable each friend (and the 5-stack) is in the first part of a round, and score how well a model could predict them. If a stranger watched our last 20 Ascent attack rounds, how often could they call where Sova goes?

DATA WE HAVE
- Condensed replay blobs per round (app/replays/condense.py, format.py): player positions (u, v), yaw and z on a grid, plus round phases, barrier drop, the plant and deaths.
- Map callouts: app/static/data/callouts.json. Layouts per patch: app/replays/layouts.py (layout_for). Only compare rounds on the same layout.
- Round context from the DB: side, round number, economy (round type, loadouts), score.

WHAT TO BUILD
1. Opening trajectory per player per round: positions from barrier drop to T = 20 s / 30 s (or first contact, whichever comes first). Resample to fixed time steps.
2. Turn each trajectory into a route label: the sequence of callout areas visited (dedupe repeats). Then cluster routes per (player or agent, map, side) into "openers". Start simple: the exact callout sequence up to the first 2-3 areas. Try DTW / Fréchet k-medoids only if callouts are too coarse.
3. Team setup: the multiset of the 5 openers is the team default (on defence, the starting setup).
4. Prediction scoring (the core):
   - Baseline A: always predict the player's most common opener on that map and side.
   - Model B: predict from context (round type: pistol / eco / force / full; previous round won or lost; score state; the last round's opener). A shrunk frequency table, like util_model's parent-cell shrinkage, is enough to start. No black box.
   - Evaluate leave-one-match-out (every round predicted only from other matches). Report top-1 accuracy, log loss, and entropy in bits per opener (0 = always the same).
   - Predictability score per player/map/side = top-1 accuracy of B, with a CI (cluster bootstrap by match; stats_math.py has helpers).
5. Does it matter? Win rate (and Impact / fight EV) when the player used their common opener vs a rarer one, with an honest CI.
6. Timing tells: the time of first arrival at each choke/area by round type. Flag low-variance timings ("B main at 0:09 ± 0.6 s on ecos").

WHERE IT GOES
- Compute on the replay worker or as a batch script (scripts/predictability.py through scripts/with_friends_db.py --read-only first). Store results in a new table only once the numbers are worth it (new Alembic migration).
- UI: a "Predictability" section on the player page (per map: openers as small minimap path thumbnails with %, the score, entropy) and a team view on /squad.
- Any background job must check deploy_drain.paused.
- Add new tests/modules to tests/suites.toml.

OPEN QUESTIONS FOR ME (ask, don't guess)
- Cut-off time (20 s, 30 s or first contact).
- Per player, or per player+agent.
- Minimum rounds before a score is shown (suggest 15 per map+side).

FIRST STEP
Read-only feasibility: count replay rounds per player/map/side, extract openers for one map, print the opener table and baseline-A accuracy. Show me that before designing the UI.
