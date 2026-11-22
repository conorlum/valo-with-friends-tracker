HANDOFF 7 — Ult value: win % added per ult, ult rankings, and who should take orbs

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first, especially "Critical Util", and docs/superpowers/specs/2026-10-21-critical-util-design.md. Design spec in docs/superpowers/specs/ for my approval before code. Never on the demo. Opponents never named. Every figure with n and a CI.

GOAL (three questions)
1. How much does each ult use add to its side's round-win chance?
2. Which ults are the most impactful (per agent's ult, and per friend's use of it)?
3. Who should take the ult orbs: which agents' (and players') ult points are worth the most, so the team knows who to give orbs to.

WHAT EXISTS
- Critical Util already extracts each cast and its effects (app/critutil/extract.py → replay_round_util), and values them (app/services/util_values.py): the ledger-and-jobs `value` and the no-util baseline `lift` (the change in win chance over the next 10 s vs similar moments with no util of that kind up, shrunk toward the ability's prior). Ults are slot "X" in app/critutil/abilities.json. Check first: are all ults in abilities.json with their effects, and does the current build already value X casts? This may mostly be a new view of existing values plus a few fixes.
- The round-win estimator: app/services/util_model.py.
- Orb pickups: OrbPickedUpRPC in the replay (app/replays/contract.py RPC_ORB_PICKUP; attach_orb_tries in player_state_extract.py). Orb tries/attempts are stored.
- Ult points: check whether the replay exports ult points directly. If not, reconstruct: +1 per kill, death, plant, defuse, orb; the agent's cost (7/8/9 points, put the costs in data, per patch).

PART 1 — VALUE PER ULT USE
- Per X cast: lift and value from util_values (use the stored build; requests never build).
- Ults often last longer than 10 s or win a whole retake: consider a longer window for X (e.g. 20 s, or to the round end) and declare it in the spec before measuring. Compare to the round-level view: win rate in rounds with the ult used vs similar rounds (same state, round type, side) without it.
- Watch the selection effect: ults get used in winnable or important rounds. Condition on state; say what remains.

PART 2 — RANKINGS
- Per agent ult (across everyone in our replays, both teams, strangers unnamed): mean lift with CI, n, by side (attack/retake/defence).
- Per friend: their ult's lift vs that ult's average ("your Raze ult adds +7%, the average Raze ult +4%").
- Wasted ults: died with the ult ready (holding the ult, like "deaths holding util" in critutil), ult ready for N rounds unused.

PART 3 — WHO SHOULD TAKE ORBS
- Value of one ult point for a player = (their ult's value per use) / (the ult's cost), adjusted for how soon they'll use it (points already held, rounds until use).
- Per round, at each orb: which living teammate's point was worth the most, vs who actually took it. Show "orb taken by the best-value teammate X% of the time".
- Simple and transparent; no black box.

WHERE IT GOES
- Values: an extra product in the existing util build if it fits (app/critutil/corpus.py runs on the worker; keep the build digest rules), else a separate stored product keyed by corpus_key.
- UI: an "Ults" section on /util (ranking table + per friend), a line in the Critical Util tab per ult cast, and orb notes in the viewer.
- Add suites to tests/suites.toml.

FIRST STEP
Read-only (scripts/critical_util.py measure / show through with_friends_db.py --read-only): list X casts in the current build with lift/value, n per agent, and whether ult points can be read or must be rebuilt. Report before designing.
