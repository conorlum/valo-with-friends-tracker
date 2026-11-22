# Handoff 10: Replay search: find every round that matches a situation

Read `docs/handoffs/README.md` (ground rules, shared building blocks) and `CLAUDE.md` first. A short design spec
for the owner's approval, then build.

## Goal and scope

A search page over replay rounds: "3v4 retakes on B, spike planted, more than 20 s left", "rounds where Name died
first on Ascent attack", "every Sova ult". Each result opens the viewer at that moment.

The owner expects it to be used mainly when coaching one player and when checking rules for other features, not as
a daily page. So: cheap to build, cheap to keep working, fast to use; no polish beyond that. Prefer no new
derived table at all if direct queries are fast enough.

## What exists to query

- Man-advantage states over time: `app/services/state_replay.py`, `app/services/replay_view.py` (`alive_steps`,
  `round_annotations`).
- Fights and their tags: `replay_round_moments.fights` (Critical Moments).
- Casts and their effects: `replay_round_util.data` (Critical Util), with ult uses.
- Gaps and back-shots: `replay_gaps`.
- Kill feed, economy, plants: `kill_events`, `round_player_stats`, `rounds`.
- Round labels from handoff 09, if built.
- The coach query tool (`app/coach/query.py`: `where`, `knew`, `distance`, `control`, `timeline`) answers many
  questions about one match's snapshot; check whether parts can be reused for filters.
- The viewer opens a round at a time with `/replays/<uuid>?round=n&t=s`.

## Filters (first version)

- **Match:** map, date range, friends present (from the circle, `app/services/circle.py`), our side.
- **Round:** round type (ours and theirs), won or lost, score range, planted (and site if known), round label.
- **Moment:** a man-advantage state (e.g. 3v4 from our side) with "spike planted" and "time left at least N s";
  or an event: kill, death, first death, plant, defuse, ability cast (by agent and slot), ult cast, a Critical
  Moments tag, a back-shot.
- **Player:** who (a friend), agent.

Results: one row per matching moment, newest first, with map, round, score, the moment, and a link that opens the
viewer 3 s before it. Paged (50 per page).

## Implementation choice (decide from measurements)

1. **Direct queries** over existing tables, with Python filtering of JSON columns where needed. Choose this if a
   typical search across all replays runs in under about 300 ms.
2. **A small index table** (`replay_moment_index`: replay, round, t, kind, slot, agent, side, our alive, their
   alive, planted, time left, tags), filled when a replay's gap run and util rows become current, with a revision
   column and a backfill script. Choose this only if 1 is too slow.

## Page

- `/replays/search` with a GET form, so every search is a shareable URL. Plain HTML form; no client framework.
- Register in `site_health.PAGE_ROUTES`; 404 on the demo; logged-in viewers only (friends' data).
- Opponents by agent only in results.

## Tests

- Filter parsing and query building (pure); one Postgres-lane test on a tiny fixture with two replays. Add to
  `tests/suites.toml`.

## Phases and done criteria

1. Count replays, rounds and moments in the friends DB (read-only); time a direct query for "3v4 with the spike
   planted". Done when the choice between 1 and 2 is made with the numbers.
2. The page with match, round and state filters. Done when a search returns correct links.
3. Event and player filters.

## Open questions for the owner

- Which filters matter most for coaching sessions.
- Whether results should export as a list for the coach (round + time pairs to paste into a prompt).
