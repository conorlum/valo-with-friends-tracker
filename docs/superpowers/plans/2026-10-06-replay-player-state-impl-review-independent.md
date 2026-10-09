# Replay player state - independent implementation-plan review

Date: October 8, 2026. Reviewer: a fresh subagent with no part in writing the plan. It read the plan, the design,
`CLAUDE.md`, the run's settled answers and the real code on the base (`origin/main` `cf75c29`: CONDENSE 14 /
CONTROL 8, utility-review and height-slopes work merged). It reported and edited nothing. The plan's author
applied each finding; the result is the "Amendments after the independent review" section at the end of
[the plan](2026-10-06-replay-player-state-impl.md), which wins over the plan's earlier text where they differ.

No blocker. 14 should-fix, 6 nits. Every finding is accepted unless the disposition says otherwise.

## Findings

| # | Severity | Finding | Evidence | Disposition |
| --- | --- | --- | --- | --- |
| F1 | should-fix | Holding both revision bumps until P08 would stamp P03's re-condensed blobs with the old recipe | `app/replays/format.py:69` `CONDENSE_REVISION = 14`; recipe built at `:186-187`; `tests/replays/test_replay_format.py:61` pins 14; `scripts/control_cases.py` refuses a blob whose `condense_revision` doesn't match its manifest | Accepted. CONDENSE 15 lands in P03's commit, CONTROL 9 in P07's (P07a rides on it). P08 checks the bumps and freshness |
| F2 | should-fix | A zero-length flash blinds for 1/16 s, and a blind can start up to 31 ms before the hit | `app/control/engine.py:232-235` `_span` ("at least one grid step long"); `:561-566` appends `_span(t, t+dur)`; `snap()` `:218`; design section 2 ("Zero duration yields no lasting blind", no future effects) | Accepted. Flash/nearsight intervals become exact `[t, t+dur)`, zero length dropped, ends added to `RoundInputs.transitions` (`:333`) so they are analytic instants like ability walls |
| F3 | should-fix | The frozen `midwall` reference digest will move (blind, exact flash ends, a hostile molly over a player) | `tests/replays/control_toys.py:143-158`; `test_control_reference.py:35-39` byte identity | Accepted. Named in advance; one re-record commit per semantic change with an independent expected-result test; other references must stay identical |
| F4 | should-fix | Ability-wall blockers ignore team and erase occupancy under them, so the molly rule can't reuse them as is | `engine.py:885-900` `blocked_at`/`reopened_by` take no side; `:2124-2129`, `:2139-2142` clear `reached` under walls; `TickRunner.step` `:2658-2662` one shared `tick.sealed`; `Knowledge.tick_for` `:1805` | Accepted. Per-side hazards next to `blockers`; spread with zone nodes out of `room`, interior arrivals restored without growth; `free = max(free, reopened)` pattern (`:2130-2132`); per-side seal for `unknown_without`; side hazard added to knowledge walls |
| F5 | should-fix | No database-free way to preview local re-condensed blobs with control | `scripts/export_replay_preview.py`, `render_control_scenes.py` need the friends-DB wrapper; `preview_control_live.py:85-88` fetches public blobs only | Accepted. P09 adds a local `--blobs <dir>` mode to `preview_control_live.py` (tested on a toy), or splits viewer-only and control previews |
| F6 | should-fix | Merging CONDENSE 15 starts the live automatic re-parse of every archived upload; CONTROL 9 makes all control stale | `render.yaml:50-51` `REPLAY_REPARSE_AUTO: "true"`; `app/services/replay_reparse_auto.py:4-6`; `control_format.py:111-116` fingerprint includes the recipe | Accepted. The revision card and runbook say so with counts; recompute timing stays the owner's |
| F7 | should-fix | A patch written into `replay_parser.json` rebuilds the worker's parser at merge, and patches don't change the recipe | `replay_worker/Dockerfile:10-12`; `contract.py:104-109` recipe uses the parser commit only | Accepted. Any parser patch is an unwired file plus decoder tests and a tier 3 card; the pin is not edited; provenance enforcement becomes a design note |
| F8 | should-fix | Ability names in `utility.json` aren't hashed, so an allow-list there would invalidate nothing | `control_format.py:83-93` `_numbers` keeps numbers only; `figures_hash` `:96-108`; `replay_gaps.py:45-46` | Accepted. Radii/durations in `utility.json` (hashed); the classification in engine code beside `DAMAGE_ZONES` (covered by the CONTROL bump). Test a semantic change and a prose edit |
| F9 | should-fix | The stdlib-only rule lives in `test_control_isolation.py`, not `test_replay_isolation.py` | `tests/replays/test_control_isolation.py:14,37-44` | Accepted. Both files run in P02, P03 and P08 |
| F10 | should-fix | Device credit is per holder, so "team-only device contribution" needs a team-level source | `Tick._watch` `engine.py:1305-1343`; `claims` `:1355-1359`; `coverage` `:1718-1720`; `compose(removed=s)` `:2769`; operated cameras/drones `:1118-1121`, `:1333-1338` | Accepted. While blinded, a holder's trip/area/turret watch moves to a per-side independent source (like `extra_passive`, `:1180-1181`, `:1362-1364`); an operated camera or drone gives no sight |
| F11 | should-fix | Own-cell clearing is physical evidence, not a personal claim | `Unknown.apply` `:2040-2041`; gap `_observe` `detect.py:309`; `live_claims` `:1496-1503` | Accepted. The own cell stays in Unknown and the observer; only control claims are removed. No PlayerView or cache shape change; assert cache `FORMAT == 1` |
| F12 | should-fix | The health contract text predates the settled combined HP + shield rule | plan P02 step 5, acceptance row REQUEST-03; design section 3 | Accepted (settled answer R2). Vitals carry current/max shield as required fields |
| F13 | should-fix | The viewer already draws status chips and drops stale round loads | `replay.js:1719-1750` `drawStatuses`, gated at `:2684-2687`; `showRound` `loadSeq` `:782-790`; `extrasFromUtil` `:751-766` | Accepted. P05 extends `drawStatuses` and moves it out of the abilities gate; P04 step 5 is a regression test of the existing token |
| F14 | should-fix | `statusesAt` has an inclusive end and an invented 1 s, and also drives device-down drawing | `replay.js:401-405`; `utilSuppressedAt` `:385-387` | Accepted. New `playerConditionsAt`; `statusesAt` unchanged |
| F15 | nit | Serializing traversal state into the gaps cache is probably unnecessary | `cache.py:73` stores `Unknown.entry`; `app/gaps` runs no reachability; engine key follows the control fingerprint (`replay_gaps.py:50-53`) | Accepted. No cache shape change expected; bump only with a real new field |
| F16 | nit | "Distinguish uncredited independent utility in summaries" changes `SUMMARY_VERSION` | `control_format.py:49` | Accepted. Dropped; `redundant` (`engine.py:2804`) already shows it |
| F17 | nit | Adding match targeting to `reingest_replays.py` and the friends-DB match query are out of scope | `reingest_replays.py:76-79`; `compute_control.py:313-315` | Accepted. The runbook names the gap; the dry-run command is text for the owner |
| F18 | nit | The geometry builder rewrites every Abyss PNG and its index row | `build_control_geometry.py:55-59,62-69` | Accepted. P06 checks sight/walk/bullet bytes identical and the index row differs only in `barrier_sha` |
| F19 | nit | P08's single `pytest tests\replays -q` contradicts the suite-in-parts rule | run rules | Accepted |
| F20 | nit | `DAMAGE_ZONES` mixes sustained zones with instant blasts | `engine.py:171-176`; contest flag uses enemy-owned zones `:1151-1153` | Accepted. The first allow-list is the five sustained entries with existing radii, provisional; Orbital Strike and Mosh Pit's arming delay go on the REQUEST-05 card |

## Overlap with code already on the base

| Area | On the base | Owner of the rest |
| --- | --- | --- |
| Status/flash intervals | `condense.py:337-355` (`UTIL_HITS`, `_hit_duration`), `read_util` `:408`; `extras.py:855-889` statuses, `find_statuses` `:914`; engine fallbacks `engine.py:155-161`, `_flash`/`_nearsight` `:557-597`, Tick blanking `:1137-1148`; gap "blinded" reason `detect.py:349-351`; viewer `statusesAt` `replay.js:401-405`, `drawStatuses` `:1719-1750` | P02 normalizer + JS mirror; P04 `playerConditionsAt`; P05 chips; P07 eligibility |
| Exact event boundaries | `RoundInputs.blockers`/`transitions` `engine.py:330-333`; `tick_times` `:909-924`; `analytic_times` `:926-936`; analytic loop `:2684-2689,2718-2722` | P02 adds flash/nearsight ends and molly lifecycles to `transitions` |
| Observer/gaps cache | `observe.py:12-42` `PlayerView`; `cache.py:22` `FORMAT = 1`; `replay_gaps.py:50-67`; `GAPS_REVISION = 2` | P08 parity; no shape change expected |
| Parser pin/provenance | `webapp/replay_parser.json`; `contract.py:35,86,104-112`; `replay_worker/Dockerfile:10-12` | P03: an unwired patch only if needed |
| Geometry and barriers | `build_control_geometry.py`; `geometry.py:393-419`; `barrier_start` `engine.py:1833-1864`; `Unknown.begin` `:1985-1990`; `Memory.begin` `:2467-2470`; `place_control_barriers.py` | P06, Abyss only |
| Temporary movement restrictions | `_ability_wall` `engine.py:833-873`, `blocked_at`/`reopened_by` `:885-900`, `Unknown.apply` `:2004-2008,2124-2142`, seal `:2658-2662`, knowledge walls `:1805`, topology `solid` `topology.py:143-150,330-331` | P07a per-side hazards on this pattern |
| Spike rendering | `spikeAt` `replay.js:524-543`; spike pass in `drawAbilities` `:1433,1624-1660`; `renderHud` `:1754-1770`; `extras.py:78,238,265-266,361,705` | P03 extraction (evidence-gated); P04 selector; P05 one pass outside the abilities layer |
| Damage zones | `DAMAGE_ZONES` `engine.py:171-176`, `damage_zones` `:496-499`, contest `:1151-1153`; per-hit ability damage `condense.py:930,961`; damage notifies kept by streaming `contract.py:290-293` | P07a allow-list; `DAMAGE_ZONES` unchanged |

## Notes

- `test_replay_viewer.py` runs Node (`subprocess.run([NODE, "-e", ...])`); 25 passed, 0 skipped on the base.
  Report skips with `-rs`.
- `test_control_unknown.py` takes 83 s, 49 s of it in one test.
- Revision pins a bump must update: `test_replay_format.py:61`, `test_control_format.py:86`, `test_gaps_task.py`.
- Damage-visibility rows (`MulticastNotifyDamage_*`) are already kept by the streaming filter; only HP/shield
  values may need new records.
