**2026-10-21 update:** the real-round check and the window runs (5 to ∞) are done. The next work (validation,
ground weighting, Shapley/stretch) is in HANDOFF-death-cost.md, which supersedes "Today's job" below.

Pick up the "statue" rework of lost control at a death (map control engine). Work in the existing worktree
`.claude/worktrees/lost-control-fixes` (branch `worktree-lost-control-fixes`, two WIP commits on top of origin/main
fdc29991, not pushed): enter it with EnterWorktree (path), don't start a new one.

## What was decided (the owner, 2026-10-20; don't re-ask)

- Lost control at a death is measured by a **statue**: the player frozen on their last tick before the death (place,
  view angle, utility still watching, never flashed), stepped in a forked copy of the round's unknown and memory
  beside the real round for `DEATH_WINDOW_S`. Loss = ground the team holds with the statue and not in the real
  round, **team-held ground only, each cell once**, averaged over the window (m²). Shared Safe is nobody's and
  counts nothing; enemy gains on ground the team didn't hold count nothing.
- Dying while flashed still gives up ground (statue unflashed). The last player alive (1vX) gives up nothing.
- Teammates dying within the window of each other: frozen together (joint run), joint loss split **in proportion
  to each one's own solo loss** (evenly if all solos are 0), not 50/50.
- The per-tick "Control" column (instant counterfactual) stays as is for now.
- Window: tunable, **8 s default**; 5 s and 10 s must be computed and compared before merging.

## What is built (engine.py, see the module docstring's "Lost control at a death" bullet)

`DEATH_WINDOW_S`, `TickRunner.fork`, `statue_inputs`, `StatueRun`, `plan_statues`, `statue_lost`, wired into
`compute_round(death_window_s=...)`; `death_window_s=0` keeps the old instant rule (`instant_lost`) so the reference
tests prove the move is the statue's alone. CONTROL_REVISION 20 (main is at 19), constants digest pinned, reference
fixture re-recorded (only barrier/midwall/pawns summaries moved), Control-table tooltip and
docs/replay-map-control-plan.md updated, new tests in tests/replays/test_control_stats.py and
test_control_blind.py. Control test files pass (76). Not yet run: the full suite (known baseline fails:
test_replay_reparse_auto; compare failure sets, not counts).

**Unverified on a real round:** the first real-match run crashed every statue run (`IndexError ... size 4096`):
the fork gave the unknown a fresh RouteLog while its entries point into the old one. Fixed (the fork now copies
the log; regression test `test_a_fork_steps_on_its_own_copy_of_the_round`), but no real round has run since.
Check one first, e.g. round 7 of the match below at 8 s, before the long run.

## Today's job

1. Run the 5, 8 and 10 s windows **together** on match `0b137bdb-4e53-425b-a3b9-d9785390f6c7` (Sunset, 17 rounds),
   with `webapp/scripts/compare_death_windows.py` (main checkout's `.venv` python, run from the worktree's
   `webapp/`, via the PowerShell tool: the Bash guard refuses paths containing GitHub). As written it runs
   window by window; reorder the tasks so the three windows progress round by round, or better, if it's small,
   compute all three windows in one engine pass (the real round is the expensive part; only the statue forks
   differ per window), kept out of the stored summary. Machine: 12 cores, ~16 GB, ~5 GB free; 4 workers took
   200-650 s a round for the instant rule (about 27 min a window).
2. Baseline (today's stored instant-rule numbers): GET
   https://valowithfriendstracker.onrender.com/replays/0b137bdb-4e53-425b-a3b9-d9785390f6c7/control/players.json
3. Show the owner one table, instant vs 5 / 8 / 10, for the deaths they raised, plus timing (statue overhead per
   round vs instant):
   - Momomimo (slot 0): R14 0:06 (instant 4275 m², "she lost basically nothing: 3 teammates behind her"),
     R7 0:26 (instant 1452; "only lost the area I circled", a lane), R9 0:28 (instant 967; "a lot more expensive
     than the other two"). Expect R9 > R14 and R7.
   - ternstyle (slot 4): R12 0:36 (instant 521, dies 0.24 s after NPrightdolphin; "pretty expensive"), R9 0:28
     (instant 5414, dies 0.06 s after Momo). Both are joint groups now.
   - Late few-alive deaths, to check 2vX doesn't dominate: Fade (slot 5) R7 0:43, Jett (slot 8) R9 0:34.
   Also each player's new top 3 ("Most expensive deaths" in the Critical Moments tab ranks by control_m2).
4. The owner picks the window; set DEATH_WINDOW_S, rerun the pinned tests, the full suite, then PR ("push it").
   After merge the owner runs the recompute (`compute_control`), since every round's revision is stale.

Context from the investigation: the old instant rule flooded the enemy's unknown, instantly and at any distance,
into everything the dead player alone held back (spawn included), and counted enemy gains on ground the team
never held (shared Safe flipping) at double weight, which is why late-round deaths ran to thousands of m².
A teammate-race and fixed horizons were tried in scratch and dropped for the statue.
