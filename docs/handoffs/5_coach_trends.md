# Handoff 05: Coach trends across a player's replays

Read `docs/handoffs/README.md` (ground rules), `CLAUDE.md` (especially "AI coach") and
`docs/superpowers/specs/2026-10-12-ai-coach-design.md` first.

## Goal

A coaching report across one player's last N replays (at least 3, ideally 5, capped around 10) that finds what
one match cannot: habits that repeat, strengths that hold up, and what changed over time. Every trend cites the
moments (match, round, time) that back it, like a single-match point does.

## This is the coach design's phase 2

The coach spec already plans it ("Later phases": **Phase 2, cross-match report**: one player across their coached
matches, which themes recur, on which maps and sides, with links to every instance; it reads stored points and
their themes). The schema is ready: `coach_reports.kind` allows `'cross_match'` and `match_id` is nullable
(`app/models/coach.py`). The gate between phases is the owner's say-so: confirm with the owner that phase 2 starts
now before building.

## The coach's rules still apply (all of them)

- Every run is Opus 5.5 at medium effort (`coach_report.MODEL`, `EFFORT`); no option changes it.
- Only players on `scripts/tracked_players.json`; anyone else refuses the whole run.
- `webapp/coach/playbook.md` is the owner's principles and rules. Never write principles. Any edit bumps
  `version` and only the owner approves edits. Refuse at version 0 or with nothing under `# Principles`, like the
  existing commands.
- The model's working directory holds only `q`; the snapshot and log stay one level up; the child environment is
  an allow-list (`coach_report.child_env`); never add a flag that skips permission checks.
- The checker (`app/coach/checker.py`) holds every figure to the run log.
- Opponent names hidden (`snapshot.hide_enemy_names`); the page swaps any left in stored text.
- The model writes in its own plain voice; no voice samples go in the prompt.

## Inputs

Two sources, and the design must decide how they combine:

1. **Stored single-match points** (`coach_points`: polarity, theme, title, body, instances) for the player's
   coached matches, with their feedback (`coach_feedback`: verdict Right/Wrong/Correction per moment, notes,
   area corrections). This is what the coach spec planned. Cheap; but limited to matches already coached, and
   it builds on model text.
2. **Structured per-match digests** built fresh from data: the snapshot's detector output
   (`app/coach/detectors.py`), Critical Moments, Critical Util and gap rows that are current. More reliable as
   evidence; costs more context.

Proposal: trends must be **evidenced from data** (2), with stored points (1) used as hints of what to look for.
A point marked Wrong by feedback is passed as "this was judged wrong" data, so the trend run does not repeat it.

## Design sketch (propose better if the code suggests it)

- **Snapshot:** one directory per run with every included match's snapshot (one file per round, as today,
  `app/coach/snapfile.py`), plus a digest file per match.
- **Query tool:** extend `q` with a match selector (`q --match <uuid> ...`, or match ids 1..N in the brief) so the
  model can drill into any included match; every call is logged with the match id so the checker can verify.
- **Brief:** per match: map, side, result, the player's agent and role, detector counts, the strongest moments.
  Across matches: the same counts side by side, so recurring patterns are visible without reading every round.
- **Output shape:** up to three recurring "to improve" trends, three recurring strengths, and one or two
  "changed" items (better or worse between older and newer matches). Each with "seen in K of N matches, M
  moments", and every moment as an instance (match uuid, round, time).
- **Checker additions:** K and M must equal the cited instances; each instance must be backed by a logged answer
  from that match; a trend needs at least 2 matches (propose 3 when N ≥ 5).
- **Account groups:** later, a person's combined accounts (`?accounts=all`); not in the first version.

## Storage and pages

- `coach_reports` row with `kind='cross_match'`, `match_id` NULL, the player, version, playbook version, model.
  A table linking the report to the matches it covered (`coach_report_matches`, migration) so the page can show
  the set and tell when it is out of date (a newer replay exists).
- Points in `coach_points` as today; instances gain a match uuid (check the instances JSON shape; migrate if
  needed).
- Page: `/players/<id>/coach` (or a tab on the player page): the trends, each instance linking to
  `/replays/<uuid>/coach` at that round and time. Feedback per instance and per point, as today
  (`coach_feedback`), and "Prompt out" works the same way.
- Register the route in `site_health.PAGE_ROUTES`; 404 on the demo.

## Command

`coach-trends "Name#TAG" [-Matches 5] [-DryRun] [-PromptOnly]`: `scripts/coach_trends.ps1` and a Python entry
(or a `--cross-match` mode of `coach_report.py` if that is cleaner). Same refusals as `coach.ps1`. Several
players run one after another, never at once (the owner's rule for the coach).

## Tests

- Checker rules for K/M counts and cross-match instances; refusal paths (off roster, playbook version 0, fewer
  than 3 replays); storage version numbering for `cross_match`; demo 404. Add to `tests/suites.toml`.

## Phases and done criteria

1. `-PromptOnly` for one player with 5 replays: write the digests, brief and prompt, report their sizes in tokens,
   stop. Done when the owner has read them.
2. One real run with `-DryRun`. Done when the owner has reviewed the output and the checker's results.
3. Storage, page and feedback. Done when a stored report renders and feedback round-trips.

## Open questions for the owner

- Confirm phase 2 starts now (the coach spec's gate).
- N: last 5, or every replay with a cap.
- Whether matches must have single-match reports first.
- Cost ceiling per run (tokens), and whether `-Team` should exist for trends.
