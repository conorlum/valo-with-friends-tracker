HANDOFF 5 — Coach trends: a report across a player's replays

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first, especially the "AI coach" section, and docs/superpowers/specs/2026-10-12-ai-coach-design.md. Design spec in docs/superpowers/specs/ for my approval before code. Never on the demo. Opponents never named.

GOAL
A coaching report across one player's last N replays (3-5 minimum, up to ~10), finding the bigger trends that one match can't show: habits that repeat, strengths that hold up, and what changed over time. Still evidence-linked: every trend cites the moments (match, round, time) that back it.

KEEP THE COACH'S RULES
- Every run is Opus 5.5 at medium effort (coach_report.MODEL, EFFORT). No option changes it.
- Only players on scripts/tracked_players.json. Refuse otherwise.
- The playbook (webapp/coach/playbook.md) is my principles; never write principles for me. Version bumps only with my approval. Refuse at version 0 / no principles, like the existing commands.
- The model's working dir holds only `q`; the snapshot stays one level up; child env is an allow-list (coach_report.child_env); never skip permission checks.
- The checker (app/coach/checker.py) still holds every cited figure to the run log.
- Enemy names hidden (snapshot.hide_enemy_names).

DESIGN SKETCH (propose better if you find it)
1. Inputs: the player's matches with a linked replay, newest first, at least 3. Per match: the existing snapshot (app/coach/snapshot.py, one file per round via snapfile.py), plus Critical Moments, Critical Util and gap rows that are current.
2. Two stages so the context stays small:
   a. Per-match digests: either the existing single-match reports (coach_points), if current, or a cheap structured digest per match (counts and tagged moments from detectors.py, moments, util). Prefer structured data over earlier model text. Earlier reports can be a hint, not evidence.
   b. A trend run over all digests, with `q` extended to query any of the N matches (q --match <uuid> ...), logged so the checker can verify.
3. Output: 3 trends that repeat across matches (with "seen in 4 of 5 matches, 11 moments"), 3 strengths, and 1-2 "changed since earlier matches" (better or worse), each linking to every moment.
4. Use my feedback: open coach_feedback marked Wrong on single-match points should steer the trend run away from the same mistake (include them as data, not instructions).
5. Checker additions: a trend's "N of M matches" and moment counts must match the cited moments; each cited moment must be in the log.

STORAGE AND PAGES
- New tables (migration): coach_trend_reports (player, match set, version, model, playbook version) and coach_trend_points with cited moments (match uuid, round, t). Immutable; re-runs add a version, like coach_reports.
- Page: /players/<id>/coach (or a tab on the player page): the trends, each moment a link to /replays/<uuid>/coach at that round and time. Feedback per moment the same way as today (coach_feedback-like table or a widened one).
- Command: `coach-trends "Name#TAG" [-Matches 5] [-DryRun] [-PromptOnly]` (scripts/coach_trends.ps1 + .py), the same refusals as coach.ps1.
- Account groups: allow `?accounts=all` later (all of a person's accounts' replays).

OPEN QUESTIONS FOR ME
- N: last 5, or every replay with a cap.
- Whether to require current single-match reports first, or build digests directly.
- Cost limit per run.

FIRST STEP
-PromptOnly for one player with 5 replays: write the digests and the trend prompt to the run dir, report their sizes in tokens, and stop. I'll read them before any model call.
