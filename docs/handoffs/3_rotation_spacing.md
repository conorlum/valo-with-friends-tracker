HANDOFF 3 — Rotations and spacing (tradeable deaths)

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first. This is hard: start with a research note and a feasibility measurement, then a design spec in docs/superpowers/specs/ for my approval, then code. Never on the demo. Opponents never named. Measurements, not verdicts (like Critical Moments).

TWO PARTS. Do spacing first (easier, more concrete), then rotations.

PART A — SPACING / TRADEABLE DEATHS
Question: when a friend died, could a teammate have traded?
- For each death: every living teammate's distance to the killer and to the victim at the moment of death, and whether they had line of sight to the killer's position within T seconds (the trade window is 6 s elsewhere: CHAIN_S in fight swing; pick T, e.g. 3 s, with me).
- "Tradeable" = a teammate could reach a sight line to the killer within T at run speed. Use the walkable grid and sight mask the control engine already uses (app/control/engine.py, geometry.py). A path distance on the walk grid beats a straight-line one.
- Outcomes: tradeable and traded / tradeable and not traded (the teammate's miss) / untradeable (the victim's spacing, or the team's).
- Per player: untradeable death rate, "left me untraded" counts by teammate, by map/side/phase. Per pair: average spacing when both are alive, and the trade rate (squad.py already counts trades; reuse its trade rule).
- Also team spread over the round (mean pairwise path distance, the largest group) for won vs lost rounds.

PART B — ROTATIONS
Question: did a rotation help or hurt, and was it early or late?
- Detect a rotation: a defender (or attacker lurk) leaves one site/area region for another, crossing a defined mid/rotate path, in callouts.json terms. Define the site regions per map in data, not code.
- What triggered it: the last info before leaving (an enemy seen or heard, a teammate's death, a util reveal, a call we can't see). The control engine's locating events (Unknown._locating) and app/services/replay_moments.py give seen/heard moments.
- Timing: seconds from the trigger to leaving; arrival vs the plant / the fight at the destination.
- Value: compare with similar states where no one rotated, using the round-win estimator (app/services/util_model.py), like Critical Util's no-util baseline (util_values.py "lift"). Shrink toward a prior and show CIs. Sample sizes will be small. Say so.
- Categories to show (no "good/bad" words): rotated on info / rotated with no visible info (possible fake bite) / stayed while the site fell / arrived after the plant.

DATA WE HAVE
Condensed replay positions per tick, deaths, sight (engine), unknown space (app/gaps), map areas (callouts.json + control/<asset>.areas.json), layouts (layouts.py), the kill feed and trades in the DB.

WHERE IT GOES
- Computed on the worker in the gap run next to Critical Moments (app/moments, app/gaps) if it needs geometry, with its own revision/fingerprint and failure status; stored per round like replay_round_moments. A new table means a migration.
- UI: tags in the Critical Moments tab ("untradeable death", "rotation, 4.2 s after info"), and a per-player / per-pair summary on the player page and /squad.
- The coach brief can cite these later (logged ids for app/coach/checker.py).
- Add suites to tests/suites.toml. Run the slow lane before merging changes to gaps/control.

OPEN QUESTIONS FOR ME
- The trade window T, and run speed vs shift-walk for "could reach".
- Site/rotate region definitions per map: whether I hand-draw them in the tagger.

FIRST STEP
Read-only: on 3-5 replays, compute Part A for every death and print a table (victim, nearest teammate path distance, sight possible within T, traded?). I'll check the labels by eye in the viewer before anything else.
