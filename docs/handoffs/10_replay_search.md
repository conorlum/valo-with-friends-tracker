HANDOFF 10 — Replay search: find every round that matches a situation

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first. Short design spec in docs/superpowers/specs/ for my approval, then build. Never on the demo. Opponents never named. Low priority; keep it simple and cheap to maintain.

GOAL
A search page over replay rounds: "3v4 retakes on B, spike planted, more than 20 s left", "rounds where Name died first on Ascent attack", "every Sova ult". Each result opens the replay viewer at that moment (round + time). Mostly a tool for coaching one player and for me checking rules, so it should be fast to use, not pretty.

WHAT EXISTS (reuse, don't rebuild)
- Man-advantage states over time: app/services/state_replay.py, app/services/replay_view.py (alive_steps, round_annotations), replay_match_states table.
- Fights and tags: replay_round_moments (Critical Moments). Util casts and values: replay_round_util. Gaps / back-shots: replay_gaps. Kill feed, economy, plants: DB tables.
- Areas: app/static/data/callouts.json; layouts.py for asset keys.
- The coach query tool (app/coach/query.py) already answers many questions over one match's snapshot; check whether its filters can be reused.

FILTERS (first version)
- Match: map, date range, players present (friends circle, app/services/circle.py), side.
- Round: round type (pistol/eco/force/full, ours and theirs), won/lost, score range, planted (site, if stored), round label (handoff 09, if built).
- Moment: man-advantage state (e.g. 3v4) with optional "spike planted" and "time left >= N s"; an event kind (kill, death, first death, plant, defuse, ult cast, ability cast by agent/slot, Critical Moments tag, back-shot).
- Player: who (friend), agent.
Results: one row per matching moment, sorted newest first, with "open at t-3 s" links (check which URL params the viewer takes for round and time; add ?round=&t= if missing).

IMPLEMENTATION
- An index table built from stored rows: replay_moment_index (match, replay, round, t, kind, player, agent, state, planted, time_left, tags jsonb), filled when a replay's gap run / util rows become current, with a revision column; rebuilt by a script for old replays. Queried with plain SQL with indexes on (kind, map) etc.
- Or, if the data is small enough (count replays first), query the existing tables directly with no new table. Prefer this if it's fast enough: less to keep in sync.
- Page: /replays/search (app/routers/replays.py or a new router), a form with GET params so a search is a shareable URL. Register the route in site_health.PAGE_ROUTES (a test fails otherwise).
- "Saved searches" later, if ever.

TESTS
Pure filter-building tests and one Postgres-lane test on a tiny fixture; add to tests/suites.toml.

FIRST STEP
Count replays/rounds/moments in the friends DB (read-only) and time a direct query for "3v4 with the spike planted" across them. Pick the no-new-table design if it runs under ~300 ms.
