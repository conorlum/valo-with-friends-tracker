HANDOFF 15 — Value of info: how much unknown each util clears or holds

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first, especially "Critical Util", and docs/superpowers/specs/2026-10-21-critical-util-design.md ("Hold space"). Design spec in docs/superpowers/specs/ for my approval before code. Never on the demo. Opponents never named. Every figure with n.

GOAL
For every util, measure how much of the enemy-could-be-here space (the map-control unknown) it cleared or held, then what that was worth. Two different jobs; never rank them on one scale:
- HOLD (trips, cameras, turrets, area watchers, mollies): the unknown stopped at the util's edge. Trips will hold far more than anything; that's expected, show it per kind.
- CLEAR (recon darts, drones, Fade prowlers/haunt, Gekko Wingman scans, Sova ult reveals, any reveal): the unknown removed at once when enemies were revealed or an area was shown empty.

WHAT EXISTS
- app/critutil/unknown.py: the unknown probe already measures, per trip / area watcher / camera / turret / molly, `front_s` (unknown at its edge), `held_m2s` (ground only it held) and `crossed_s`. It runs in the gap run (app/control/task.py, observe.py TickRecord). Stored in replay_round_util.
- The engine's Unknown (app/control/engine.py: Unknown.apply, _locating) shrinks on sight, reveals, sounds, kills.
- Values: app/services/util_values.py (ledger + jobs weights fitted on round wins, no-util baseline lift); util_model.py round-win estimator.

BUILD
1. Clear measure for reveal util: per cast, the unknown area (m²) of the caster's side before and after the reveal (e.g. the tick before and 0.5 s after), and how many enemies it located (named by the engine's per-enemy unknown) and how precisely (each located enemy's unknown area before vs after). Also "cleared empty": cells the reveal showed had no one.
2. Per-enemy: total m² of unknown removed, and m²·s (how long it stayed removed before spreading back at UNKNOWN_MPS).
3. Hold measures: reuse unknown.py's numbers; add a per-kind table so trips are compared with trips.
4. What it led to: kills on located enemies within N s (critutil already links kills on affected enemies), teammates' movement into the cleared space (did someone act on the info?), and the win-chance change (util_model) after it.
5. Credit: the caster gets the info; "acted on it" credit to whoever took a kill or moved into cleared space within N s. Declare N and the rules in the spec.

OUTPUT
- Critical Util tab per cast: "cleared 340 m², found 2 enemies (unknown 410 → 25 m²); Jett killed one 3.1 s later". For holds: "held B main for 41 s, crossed 0 s".
- /util: per agent/ability kind, median m² cleared / held, found-enemy rate, acted-on rate, value; per friend vs the ability's average. Holds and clears in separate tables.
- If it adds value: a "cover info" job in util_values' fitted weights (that's a scoring-adjacent change of the util build; follow the build's adoption rule: adopted only when it beats the current weights on held-out matches).

WHERE IT GOES
In the gap run next to the unknown probe (gap protocol / GAPS_REVISION bump; the worker image must ship it: replay_worker/Dockerfile ships app/critutil). Keep it pure numpy like unknown.py. Add suites to tests/suites.toml; run the slow lane before merging gaps/control changes.

FIRST STEP
Read-only on 3 replays: for every reveal cast, print unknown m² before/after, enemies located, and the 5 largest for me to check in the viewer.
