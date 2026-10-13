# AI coach plans: review of the amended parts (P4, 2026-10-12)

An independent reviewer read what commit `73cf263` rewrote (plan 1 Tasks 2-8, plan 3 Tasks 3-4, plan 2's
`short_note`) against the real code under `webapp/`. Nothing was executed apart from small regex and `--help`
checks. This file gives each finding, its evidence, and what the author did with it. The changes themselves
are in each plan's "Amendments after the P4 review" table (rows `R-F<n>`), which win over the task text.

Plans: P1 = `2026-10-12-ai-coach-1-brief-and-queries.md`, P2 = `2026-10-12-ai-coach-2-storage-and-page.md`,
P3 = `2026-10-12-ai-coach-3-coach-run-and-feedback.md`. Line numbers are as of `73cf263`.

## Verdict

One blocker, in plan 3's checker: a number written with its unit attached was never checked, so a made-up
distance or time would have been published. It is fixed in the plan's code. Every other finding is a
should-fix or a nit; all were applied except F15 (noted only). All fixtures hold the relationship their test
claims, and the four stated pass counts match the plan text. No open blocker.

## Findings

| # | Severity | Where | Finding | Evidence | Disposition |
| --- | --- | --- | --- | --- | --- |
| F1 | blocker | P3:661-662 | `_NUMBER`'s lookahead `(?![\w#])` rejects a digit followed by a letter, so "5m", "3s", "40hp" are never checked and "35.7m" is read as 35; `_VERSUS` misses "2vs1", "2 v 1", "2 on 1". | The plan's regexes run on those strings gave no match. | **Fixed in the code.** New `_NUMBER`, `_VERSUS`, `_ORDINAL`, `_THOUSANDS`, checked on 22 strings before the edit; four tests added. |
| F2 | should-fix | P3:729-731 | A true negative figure is always refused: "Impact of -250" yields 250, and `_matches(250, [-250.0])` is false. | `impact` and `death_impact` are negative in the rounds an "improve" point is about; the plan's own fixture uses -250. | **Fixed in the code**: `_matches` compares sizes. One test. |
| F3 | should-fix | P3:814-826 | Cited evidence is tied to the round only: a `distance` asked at second 50 grounds "when you died" at second 20; `distance P0 P5` grounds a figure said of another player. | P3:746-764 never links names to answers. | **Part fixed**: new `_in_moment` ties a one-second answer to the instance's window; two tests. Names stay unlinked: added to Step 5b's known-and-accepted list and to the owner's card. |
| F4 | should-fix | P3:1437 | Step 7's probes never test a read outside the run directory. `--restricted` confines the file tools; Bash is the one tool left, and the `q` script prints the webapp's path. | `claude --help` 2.1.296; `webapp/scripts/coach_report.py` in the plan, `write_q`. | **Applied, narrowed**: one added probe, `cat ../probe.txt` on a planted harmless file. The reviewer's `env` and `ls ~/.claude` probes are not run: an unrefused one would send real values to the model. |
| F5 | should-fix | P3:1287-1288 | `child_env` removes one name; any other secret in the owner's shell reaches the model. | `webapp/scripts/with_friends_db.py:131` sets only `DATABASE_URL` today. | **Applied**: an allow-list; the test is extended. |
| F6 | nit | P3:1228 | A repair reply with `index` `"1"` (a string) is dropped silently. | | **Applied.** |
| F7 | nit | P3:1367 | `snap.slot_of(args.player)` is outside the `try`: an unknown player ends in a traceback. | | **Applied.** |
| F8 | owner's | P1:1974, P3:657 | `where` returns `hp` and `shield`, the checker treats both as measured, so a published point may say "61 hp". The owner's rule for the viewer is that health is never a number. | `webapp/app/static/js/replay.js:2318-2321` (`healthWord`). | **Applied as the cautious reading**: the viewer's three words; provisional, on a card. |
| F9 | should-fix | P1:262, 446-451 | With no side in the blob, the engine maps the round's role to a group (`A` attacks), which flips at the half; RoundView used the page's fixed team group, so in one half `knew` would be the enemy's. | `webapp/app/control/engine.py:319-321`; `webapp/app/services/replays.py:119`. | **Applied**: derive as the engine does; a guard in `knew` and `control`; two tests. |
| F10 | should-fix | P1:509-521 | A living player with no usable sample reads as dead, so `where` and the NvM counts are wrong; a stale sample is given as current. | `webapp/app/replays/condense.py:1072-1073` (unobserved spans). | **Applied**: alive with position unknown; two tests. |
| F11 | nit | P1:1472-1473 | `_stack` ends at the first second the player is not alive, so a late life skips the check. | `webapp/app/replays/condense.py:1011-1012`. | **Applied.** One test. |
| F12 | nit | P1:1404 | The trade search reads every kill, so a kill after the round was decided counts as a trade. | P1:1199 (the task's first rule). | **Applied.** One test. |
| F13 | nit | P1:2083 | `except (TypeError, ValueError)` also catches faults inside a query and reports them as "wrong arguments". | | **Applied.** |
| F14 | nit | P1:1834 | The "dead: no health" assertion cannot fail: round 1 has no vitals. | | **Applied.** |
| F15 | nit | P1:1939-1941 | Every `./q` call imports the database layer (no connection is made). | P1:998. | **Not changed**: only matters if calls are slow; noted in the plan. |

Plan 2's `short_note`: no findings (the macro and its test agree; plan 3 does not use the note's text).

## Checked and found correct

- **Real functions and shapes:** `fmt.encode_segment` / `decode_segment` (`app/replays/format.py:135,147`);
  `cf.pack_data`, `unpack_data`, `walk_bitmap`, `decode_states` with bit and nibble order
  (`app/control/control_format.py:152-205`); `find_replay`, `round_blob`, `page_context`
  (`app/services/replays.py:39-184`); `round_control`, `round_link` (`app/services/replay_control.py:301-352`);
  the control header's `knew`, `knowledge_events`, `group_side` (`app/control/encode.py:95-154`); sighting runs
  `[t0, t1, u, v]` (`app/control/engine.py:1923-1931`); `vitals_at` (`app/replays/player_state.py:200`);
  `TRADE_WINDOW_SECONDS = 6.0` (`app/scoring/impact.py:411`, read only).
- **Pass counts:** test_roundview 10, test_knowledge 7, test_control_areas 4, test_detectors 20,
  test_checker 27, each equal to the plan text (before the tests this review adds).
- **Fixtures worked by hand:** 700 uv is 10.0 m; mid to site 60.6 m (over the 20 m range); A Site to B Site
  121.2 m; all nine `control_over` assertions; the stack flag at second 10 and the "leaves within the hold"
  mover; last player alive; post-decision deaths; zero-second and null flash hits; the brief's economy sums.
- **Across plans:** the checker's measured keys match what `query` and the flags emit; the prompt's command
  list matches `query.COMMANDS`; `Playbook`, `ReportError`, `validate_payload`, `resolve`, `active_notes` exist
  as used.
- **Claude Code flags** (2.1.296, from `--help`; no session started): `-p`, `--output-format json`,
  `--restricted`, `--safe-mode`, `--strict-mcp-config`, `--tools`, `--allowedTools`, `--model`, `--resume` all
  exist. No permission-skipping flag is used, and a test guards that.
- **Publication and scope:** run directories and payloads are under the temp folder; `coach_snapshot.py`
  refuses an output path inside the repo; test names are synthetic; `app/scoring/` is imported only; coach
  data is not served in demo mode.
- **Not covered by this review:** plan 1 Tasks 1 and 9; plan 3 Tasks 1, 2 and 5-7; the rest of plan 2; how
  the real `claude` behaves at run time (Step 7).
