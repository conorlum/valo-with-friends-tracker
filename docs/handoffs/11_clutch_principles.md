HANDOFF 11 — Clutch review research: material for my coach playbook principles

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first, especially "AI coach" (the playbook rules). This is a behind-the-scenes research tool for me, not a site page. Its output helps ME write principles; it never writes or edits webapp/coach/playbook.md itself (the principles are my own lines; never write stand-ins). Findings name players and matches, so they stay out of the public repo: write them to a local file I can read (the session scratchpad or a git-ignored folder), not a commit.

GOAL
Measure what separates won and lost clutches (1vX, X >= 1, and close 2vX) in our replays, as evidence I can turn into playbook principles like "take fights one at a time" or "use the clock". Measurements, not verdicts.

WHAT TO MEASURE (per clutch, from the moment the player is last alive until the round ends)
1. Isolation: at each engagement, how many living enemies had line of sight to the player (the control engine's sight: app/control/engine.py; Critical Moments fights, app/moments/fights.py). "Fought one at a time" = share of fights with exactly one enemy able to see them.
2. Clock: time left vs time used; did they plant/defuse with enough time; did they force fights early or late; time spent still vs moving.
3. Unknown space: how much of the map the enemy could be in (the engine's Unknown; app/gaps) and whether the player moved into it or away; back-shots taken (replay_gaps).
4. Info: enemies seen/heard before each fight (locating events), whether they faked (tapped defuse, made noise) — check what the replay has.
5. Util: util left at the start, util used, holding util at death (Critical Util already flags "died holding util").
6. Economy and state: weapon vs enemies' weapons, health, spike state, X.
7. Outcome, and the win chance at the start from app/services/util_model.py (so a 1v3 win counts for more than a 1v1).

ANALYSIS
- Use every clutch in our replays: friends and strangers (both teams are recorded; strangers unnamed, numbered).
- Compare won vs lost clutches, controlling for the starting win chance (residual = outcome - expected). Small sample: report counts, medians and CIs (stats_math.py cluster bootstrap by match), never single p-values. List every measure, including those that show nothing.
- For each candidate pattern: the effect, n, and 3-5 example moments (match uuid, round, time) I can watch in the viewer.

OUTPUT
- A command: scripts/clutch_review.py (through scripts/with_friends_db.py --read-only) that writes a markdown findings report to a local, uncommitted path, and a CSV of every clutch with its measures.
- The report's last section: "candidate principles to consider", each as a one-line observation with its evidence (e.g. "won clutches fought one enemy at a time in 78% of fights vs 41% in lost ones, n=..."). I decide what, if anything, goes in the playbook.
- Optional later: the same measures as coach detectors (app/coach/detectors.py) with logged ids, if I adopt a principle.

RULES
- Read-only on the DB. No new tables unless I ask. Code and tests (tests/suites.toml) can be committed; findings cannot.
- Don't touch playbook.md or its version.

FIRST STEP
Count clutches (by X, won/lost) in the replays, then compute measure 1 (isolation) alone and show me the won/lost comparison with 5 example moments of each.
