# Handoffs

Each file here is a starting brief for a fresh Claude Code session working on one idea. Paste the file's
path (or its contents) into a new session: "Read docs/handoffs/README.md and docs/handoffs/NN_name.md and start".

They are briefs, not specs. Each one ends in a first step that is read-only and small, so the owner sees
real numbers before anything is designed in detail. The design spec a session writes goes in
`docs/superpowers/specs/`, and the owner approves it before code.

## Ground rules for every handoff

Read `CLAUDE.md` first. These are the rules from it that every handoff touches:

- **The repo is public.** Never commit a credential. Never commit research that names friends, opponents or
  match ids (findings go to a local, uncommitted file; code and tests can be committed). Fight swing's research
  lives outside the repo for this reason.
- **Never on the demo.** Every new page, route and job checks `demo_mode` and is off (404 for routes) on the
  `valomaths` site. The demo must never show scraped tracker.gg data or replay-derived analysis.
- **Opponents are never named** on any page the coach or analysis produces: they are "the enemy <agent>"
  (`app/services/enemy_names.py`). Strangers in pooled statistics are numbered, never named.
- **Spec first.** Declare definitions, thresholds, windows and the analysis before measuring (the scoring
  release process's habit, `docs/superpowers/SCORING-RELEASE-PROCESS.md`). If a measurement changes a
  definition, write down the change and why.
- **Measurements, not verdicts.** Like Critical Moments: show "won 4 of 11 wide swings", never "bad peek".
  Wins and losses of the same kind sit together.
- **Every figure carries its sample size**, and estimates carry an interval. Use the cluster bootstrap by match
  (`app/services/stats_math.py`: `cluster_bootstrap_ci`, `paired_bootstrap_delta`). Small samples get shrunk
  toward a parent (the pattern in `app/services/util_model.py`: a thin cell falls back on its parent).
- **No leakage.** A model never values a round from a match it was fitted on: cross-fit by match
  (`app/services/win_probability.py` docstring explains why this is mandatory).
- **Database access** for measurement goes through `scripts/with_friends_db.py --read-only`. Tests never touch
  the friends or demo DB (`docs/testing.md`).
- **Background work** (any new loop or scheduler) checks `deploy_drain.paused` before admitting new work, and
  records failures in Site health (`app/services/site_health.py`: add the kind to `RECORDED_KINDS`, the card's
  `KINDS`, `GROUPS` and `EVENT_KINDS`).
- **New routes** go in `site_health.PAGE_ROUTES` (a test fails on an unlisted GET route).
- **New test files and app modules** go in `tests/suites.toml` (`tests/test_test_catalog.py` fails otherwise).
  `python scripts/test_catalog.py plan --git` names the suites a change needs. Run the slow lane before merging
  changes to control, gaps, heights or features.
- **Worker-side changes** (anything in the gap run: `app/control/task.py`, `app/moments`, `app/gaps`,
  `app/critutil`) bump the right revision (`GAPS_REVISION`, gap protocol) so stored rows go stale and the
  worker recomputes them; the worker image must ship the new code (`replay_worker/Dockerfile`).
- **Layouts:** compare positions only within one map layout (`app/replays/layouts.py` `layout_for`, asset key
  `<Map>` or `<Map>_<major>_<minor>`).
- **Impact scoring is out of scope** for every handoff here. Anything that would change Impact follows the
  scoring release process instead.

## Shared building blocks (most handoffs use several)

| What | Where |
|---|---|
| Condensed replay round blob: positions (u, v), yaw, z at 16 Hz (`GRID_HZ`), util rows, shots, damage, phases, plant, player_state (status, health, ult points, plants, orbs) | `app/replays/condense.py`, `app/replays/format.py`, `app/replays/player_state_extract.py` |
| Map control engine: sight (103° view, smokes, walls), per-enemy unknown space spreading at `UNKNOWN_MPS` 3.24 m/s, control per tick (`TICK_STEP_S` 0.5 s plus event ticks) | `app/control/engine.py`, `app/control/observe.py`, `app/control/task.py` |
| Critical Moments: fights (split at `FIGHT_GAP_S` 3 s), who saw whom first, crosshair angle at the spot (`CROSSHAIR_ON_DEG` 5°, `CROSSHAIR_NEAR_DEG` 20°), time to kill, trades (`TRADE_WINDOW_S` 6 s, `TRADE_RANGE_M` 20 m), moving shots (`MOVING_MPS` 2.5) | `app/moments/fights.py`, `app/moments/constants.py`, table `replay_round_moments`, `app/services/replay_moments.py` |
| Gaps and back-shots | `app/gaps/`, table `replay_gaps` |
| Critical Util: casts and their effects, ult timelines, unused util, unknown probe for devices and mollies | `app/critutil/extract.py`, `app/critutil/unknown.py`, table `replay_round_util`, `app/services/replay_util.py`, `app/services/util_values.py` |
| Round-win estimator P(attackers win \| alive, spike, time) | `app/services/util_model.py` (`p_attack`, `p_side`) |
| Post-plant V(a, d, t) table (Impact's) | `app/scoring/postplant_value_table.py` |
| Man-advantage round replay over the tracker.gg kill feed | `app/services/state_replay.py` |
| Map areas | `app/static/data/callouts.json` (each callout has `name`, `super` such as A/B/Mid, `u`, `v`), reviewed paint `app/static/data/control/<asset>.areas.json`, chokes `app/static/data/control/<Map>.chokes.json` |
| The viewer | `app/static/js/replay.js` (canvas), `/replays/<uuid>?round=n&t=s` opens a round at a time |
| Friends circle | `app/services/circle.py` |
| tracker.gg tables | `rounds` (outcome, planted, plant_time, defused, exploded), `kill_events` (killer, victim, weapon, time, `source_meta`), `round_player_stats` (kills, deaths, assists, loadout, remaining), `round_player_spend`, `impact_scores` |

## Index

| # | File | Idea |
|---|---|---|
| 01 | `1_predictability.md` | Opening predictability and prediction scoring |
| 02 | `2_control_value.md` | Which areas are worth controlling |
| 03 | `3_rotation_spacing.md` | Tradeable deaths, spacing and rotations |
| 04 | `4_crosshair.md` | Crosshair placement and time to target |
| 05 | `5_coach_trends.md` | Coach trends across a player's replays |
| 06 | `6_highlights.md` | Clip export and auto highlights |
| 07 | `7_ults.md` | Ult value, rankings and orb priority |
| 08 | `8_momentum.md` | What makes players play better |
| 09 | `09_round_loss_labels.md` | Why we lost each round |
| 10 | `10_replay_search.md` | Search rounds by situation |
| 11 | `11_clutch_principles.md` | Clutch research for the playbook |
| 12 | `12_execute_timing.md` | Execute timing |
| 13 | `13_plant_heatmap.md` | Plant spots and win rate |
| 14 | `14_post_plant_positions.md` | Post-plant positions and value |
| 15 | `15_info_value.md` | Unknown space cleared and held by util |
| 16 | `16_peek_types.md` | Peek types and win rates |
