Validate "lost control at a death" (the statue) against two yardsticks and test weighting ground by value: items
4, 5 and 6 below. Work in the existing worktree `.claude/worktrees/lost-control-fixes` (branch
`worktree-lost-control-fixes`): enter it with EnterWorktree (path). Read HANDOFF.md first for the statue itself.

## Where things stand (2026-10-21; decided by the owner, don't re-ask)

- The statue (HANDOFF.md) is built and runs on real rounds. The fork crash is fixed and confirmed on match
  `0b137bdb-4e53-425b-a3b9-d9785390f6c7`.
- **Window:** 5, 8, 10, 15, 20 s and ∞ were all run on that match. 15 s, 20 s and ∞ were dropped: they're no
  better on the owner's calls, cost +47 / +55 / +79% over the real round, and put nearly every death in a group.
  It's between 5 and 8 s. The owner wants Shapley groups tested at 8 s (running at handoff, see below).
- **Dropped:** a route-to-nearest-ally measure (unrelated to every area measure, rank agreement ≤ 0.22, mostly 0)
  and a fading statue (item 3).
- **Found while testing Shapley:** the jump in grouped deaths (Momo R7: 95 m² at 5 s, 485 at 8 s) comes from the
  **group stretch**, not the split. Each member is measured from the group's first death to its last death +
  window (14 s for R7) but divided by the window. Momo's solo run over that stretch is already 513, and Shapley
  gives her 514. Item 1 below is that comparison.
- The owner's calls on that match: Momo R9 0:28 > R14 0:06 and R7 0:26 (R14: "she lost basically nothing: 3
  teammates behind her"; R7: "only lost the area I circled", a lane); ternstyle R12 0:36 "pretty expensive";
  late few-alive deaths (Fade R7 0:43, Jett R9 0:34) shouldn't dominate. 5 s gets Momo's order right; only 8 s
  makes ternstyle R12 expensive.

- **Shapley at 8 s (done, `shap_8.json`):**
  - Rank agreement with today's proportional split is 0.97, and it doesn't change any of the owner's calls: Momo
    R7 514 is still above R9 224.
  - It does move single grouped deaths a lot (NPrightdolphin R4 0:47: 142 → 957; DoubleBl1nd R4 0:56: 912 → 337).
  - No negative shares.
  - Cost: 185 runs, +56% over the real round, against +31% for the proportional split.
  - Dividing by the stretch doesn't fix Momo either (R7 292 vs R9 150). Her R7 loss is real statue loss between
    5 and 14 s after the death, so only a 5 s window ranks R9 above it.

## Tools

- `webapp/scripts/compare_death_windows.py` (uncommitted in the worktree) reads the friends site's public
  responses only, with no DB and no writes but its output JSON. Modes:
  - `... <uuid> <out.json> <workers> 5 8 10`: several windows in one engine pass (the real round is computed
    once, and statue runs are shared across windows). `inf` = until the round is decided, averaged over the time
    from the death to the decision.
  - `... <uuid> <out.json> <workers> --shapley 8`: Shapley groups. Every subset of a group gets a run (2^N − 1),
    and per death it records the Shapley share, the proportional share (today's rule, with solos over the group
    stretch), the solo, the joint and the group size.
  - No windows: the real round only (instant rule).
  - Always: the route measure (cheap, dropped; ignore).
  - `--rounds 7,9` limits the rounds.
- How to run: PowerShell tool, the main checkout's python
  (`C:\Users\Conor Lum\Documents\GitHub\valo-with-friends-tracker\webapp\.venv\Scripts\python.exe`), from the
  worktree's `webapp/`, with the script's full path. Run in the background; 4 workers. A pass over a 17-round match
  takes about 25 min for the real round alone, plus 19–31% for 5–8 s statues. Machine: 12 cores, ~5 GB free.
- Results: `death-cost-results/` in the worktree root. It's excluded via `.git/info/exclude`, never commit it: it
  names real players.
  - `baseline.json`: the stored instant-rule numbers from `/replays/<uuid>/control/players.json`.
  - `all_windows.json` (5/8/10), `long_windows.json` (15/20/∞), `route_all.json`, `shap_r7.json`.
  - `shap_8.json`: copy it in from the session scratchpad once the run finishes, if it isn't there yet.
  - Table scripts `table.py`, `long_table.py`, `shap_table.py`: run each with the folder as the argument. Each
    death row is (round, slot, t), matched by t within 0.05 s.
- Replay links for showing the owner a death: `/replays/<uuid>?round=N&t=S` (`app/routers/replays.py`
  `viewer_context`, `start_time`). Check that `t` is on the same clock as a death's `t` in players.json (seconds
  from the round's start) before relying on it.

## Item 1 (do first, cheap): stretch-fair groups, as a comparison column

`shap_table.py` already has "Shapley per 8 s of stretch" (the Shapley share × 8 / the group's stretch). Also add
"each member only over their own 8 s inside the joint run": record per-tick loss on the joint run (see item 2),
then count each member's ticks in [t_i, t_i + 8). Show both next to proportional and Shapley.

Item 2 is worth doing for 4 and 6, which run many variants over many matches. Store each statue run's loss per
tick in the script's output (7 floats a tick: lost, 3 levels, 3 went_to; about 5 ticks a second), not in the DB.
That's about 3 KB a run and under 1 MB a match. Then one long run (say 15 s) answers every window and stretch rule
afterwards without another engine pass.

## Item 4: a pairwise yardstick from the owner

Seven hand-picked deaths overfit. Build about 30 pairs, owner answers "which of these two deaths cost more
ground?", and score every variant by the share of pairs it agrees with.

- **Pairs:** mostly from matches the owner played (they remember them; `scripts/tracked_players.json`'s owner is
  NPrightdolphin#NA1), across several matches and maps. Pick pairs where the variants disagree (instant vs 5 s vs
  8 s vs Shapley vs stretch-fair), plus a few control pairs where everything agrees. That's where an answer tells
  variants apart.
- **Page:** a local HTML file, not an artifact (the owner can't open artifact links). Per pair: both deaths with
  the replay link at each moment, player/agent/round/time, and three buttons: A, B, "about the same". Answers go
  to localStorage plus an "export JSON" button, so the owner can paste the result back. Show no model numbers on
  the page, so the answers aren't anchored.
- **Scoring:** agreement % per variant. Count "about the same" as agreement when the two values are within 25% of
  each other, and report the threshold. Report it per pair type too (grouped deaths vs solo, late few-alive vs
  early), since that's where the variants differ.
- Needs statue runs on more matches first: pick 4–6 matches with replays (the site's `/replays` page lists them).

## Item 5: weight ground by what it's worth

Today 1 m² of spawn counts the same as 1 m² of a site entrance. The precedents are soccer's "pitch value"
(Fernández & Bornn, "Wide Open Spaces", Sloan 2018) and WARDS' points-of-interest bonus (Chitayat et al. 2020).

- Try in the script only (`StatueRun.step`: replace `geo.area(lost) * cell_m2` with a weighted sum). Candidate
  weights, each normalised so the map's mean weight is 1:
  - by walking distance to the nearest site (bomb-site areas from `app/static/data/callouts.json`, or the spike
    plant positions in the corpus);
  - by callout area importance, using the corpus: how often each area was held by the round's winner;
  - zero weight on the dying side's own spawn.
- Compare each weighting on item 4's pairs and item 6's test. The owner's R14 call ("3 teammates behind her") and
  the R7 lane call are good sanity checks.
- Keep the stored m² unweighted unless the owner decides otherwise. A weighting would be a new figure next to it,
  not a change to it.

## Item 6: does lost control predict losing the round?

The objective test, from MOBA analytics (Maymin, "Smart kills and worthless deaths", JQAS 2021: a death is
judged by the change in win probability).

- **Baseline:** P(round win) right after each death from the state then: score, both sides' loadout/credits
  (`RoundPlayerStat.loadout` is gear carried, not spend), players alive each side, time left, spike planted.
  Logistic regression, cross-validated by match.
- **Test:** add the death's lost control (each variant, unweighted and item 5's weights). Use out-of-fold log
  loss / Brier. A variant that doesn't improve on the baseline isn't measuring something that matters.
- **Data:** only matches with replays have control. Statue runs cost about 30 min a match on 4 workers, so 40+
  matches is an overnight job (the afk skill, `~/.claude/skills/afk`). Store per-tick loss (item 2) so every
  variant comes from one pass. The friends DB is read through `scripts/with_friends_db.py`; read-only URLs only
  (see the read-only prod checks memory).
- Watch the confounding: a death that loses a lot of ground often happens in a round already going badly.
  Players alive and time left must be in the baseline, or the test just rediscovers "deaths are bad".

## Then

The owner picks the rule (window, Shapley or not, stretch rule, weighting). Port it into the engine
(`plan_statues`/`statue_lost` in `app/control/engine.py`), set `DEATH_WINDOW_S`, rerun the pinned tests
(CONTROL_REVISION bump pins, see the revision-bump memory) and the full suite (compare failure sets with the
known baseline), then "push it". After merge the owner runs the recompute.
