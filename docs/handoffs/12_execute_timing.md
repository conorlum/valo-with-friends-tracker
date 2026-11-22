HANDOFF 12 — Execute timing: does our util land when we swing?

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first, especially "Critical Util". Research + design spec in docs/superpowers/specs/ for my approval before code. Never on the demo. Opponents never named. Every figure with n. The hard part is deciding what an execute is: start there and check it by eye with me.

GOAL
For attacking site takes (and defensive retakes), measure how well the team's utility and entries line up: the gap between flashes/smokes/stuns landing and the first teammate swinging in, and whether that timing goes with winning the take. Target finding: "our flashes pop 1.4 s before anyone swings".

STEP 1 — DETECT AN EXECUTE (declare rules; validate on 30 by hand)
Candidate definition, to refine:
- Site regions and their entrances per map, as data (callouts.json areas; maybe a small per-map file naming which areas are a site's entrances; ask me whether I hand-draw them in the tagger).
- An execute = within a window W (e.g. 8 s), at least K (e.g. 2) attacking util casts land on or toward the site / entrance areas (smokes, flashes, stuns, mollies, recon: replay_round_util has cast kind, place, time, who it affected) AND at least 2 attackers cross an entrance into the site.
- Execute start = the first such util landing; entry = the first attacker crossing into the site area (positions from condensed blobs); the take = until the plant or the attackers' deaths.
- Exclude: rounds with no execute (picks, rush with no util: tag as "dry hit" if 3+ enter with no util), retakes handled separately with the same rules mirrored.

STEP 2 — MEASURE PER EXECUTE
- Flash lead: entry time minus each flash's pop time (negative = swung before the flash). Use the flash's real effect window (status intervals: who it blinded, from critutil extract).
- Smoke timing: smokes up before entry, and how long before (too early = they fade during the take).
- Spread of entries: time from the first to the third attacker entering (a trickle vs together).
- Util overlap: was a flashed defender killed while blinded (critutil already links kills on affected enemies).
- Outcome: plant (yes/no), round won, kills/deaths in the take, win chance change over the take (app/services/util_model.py).

STEP 3 — ANALYSIS
- Distributions per map/site for our team, and the same measured for strangers' executes in the same replays (unnamed) as a reference.
- Which timing measures go with a successful take, controlling for the man advantage at the start. Small sample: CIs by match (stats_math.py), report nulls too.

OUTPUT
- Replay viewer: an "Execute" marker on the timeline with its numbers ("flash lead -0.3 s, entries over 2.1 s").
- Team page (/squad or /util): per map/site, median flash lead, entry spread, take success, with n; "best executes" list linking into the viewer.
- Coach facts later (logged ids for app/coach/checker.py; ask me).

WHERE IT GOES
Computed on the worker in the gap run next to Critical Util (needs positions + util rows), with its own revision and failure status, or a read-only script first. Add suites to tests/suites.toml; run the slow lane before merging gaps/control changes.

FIRST STEP
Read-only: apply the candidate execute rule to 5 replays, print every detected execute (round, start, entry, util used, outcome) and every attack round it missed, so I can check them in the viewer.
