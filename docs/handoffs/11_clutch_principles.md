# Handoff 11: Clutch research: material for the owner's coach playbook

Read `docs/handoffs/README.md` (ground rules), `CLAUDE.md` (especially "AI coach") and `webapp/coach/playbook.md`
first.

## What this is, and what it is not

A behind-the-scenes research tool for the owner. It measures what separates won and lost clutches in our replays
and writes up candidate patterns with evidence, so the owner can decide on new principles or rules for the coach
playbook.

It does **not** write or edit `webapp/coach/playbook.md`, its principles, or its version. The principles are the
owner's own lines (CLAUDE.md: "Never write stand-in principles for them"). The report's suggestions are
observations with evidence; the owner writes any principle.

Findings name players and matches, so the report and CSV are written to a local, uncommitted path (the session
scratchpad, or a git-ignored folder; check `.gitignore` and add one if needed). Code and tests can be committed.

## Scope

- Clutches: a player is the last alive on their side against N ≥ 1 enemies, until the round ends. Also, as a
  separate group, close 2vN situations (two left against two or three).
- Every clutch in our replays: friends and strangers (both teams are recorded). Strangers are numbered in the
  output, never named.

## What exists

- Sight, view cones and blinding: `app/control/engine.py`; who could see whom per moment: Critical Moments' sight
  checks (`app/moments/fights.py` `_sees_point`, `_first_sight`).
- Unknown space and back-shots: the engine's `Unknown`, `app/gaps/` (`replay_gaps`).
- Locating events (seen, heard): `Unknown._locating`, hearing rules in
  `docs/superpowers/specs/2026-10-14-hearing-attribution-design.md`.
- Util left, used, and "died holding util": Critical Util (`app/critutil/extract.py` `_unused`, `replay_round_util`).
- Health and armour, statuses: `player_state` in the blob.
- Plant and defuse tries (`player_state.plants`, defuse records), the spike state.
- Starting win chance: `app/services/util_model.py`.
- Coach detectors (`app/coach/detectors.py`) may already compute parts of this; reuse them.

## Measures (per clutch, from its start to the round end)

1. **Isolation:** at each engagement (first sight or damage between the player and an enemy), how many living
   enemies had line of sight to the player. "One at a time" = share of engagements where exactly one could.
2. **Clock:** time left at the start; time spent still vs moving; whether they planted or started a defuse with
   enough time; when the first engagement happened relative to the time left.
3. **Unknown space:** how much of the map the enemies could be in at the start and over time (the engine's unknown
   for the player's side); whether the player moved into or away from it; back-shots taken.
4. **Information:** enemies seen or heard before each engagement; whether the player faked (a defuse tap, noise
   on one side then moved): check what the blob records.
5. **Util:** util held at the start, used during, held at death.
6. **State:** weapons vs the enemies' weapons, health, the spike, N, and side.
7. **Outcome:** won or lost, and the starting win chance, so a 1v3 win counts for more than a 1v1.

## Analysis

- Compare won and lost clutches on each measure, controlling for the starting win chance (outcome minus
  expected), within groups of N.
- Small samples: counts, medians and intervals (cluster bootstrap by match), never a single p-value as a verdict.
  Report every measure, including those that show nothing.
- For each pattern that stands out: its size, n, and 3–5 example moments (match uuid, round, time) the owner can
  watch in the viewer, both ones that followed the pattern and ones that did not.

## Output

- `scripts/clutch_review.py` (through `scripts/with_friends_db.py --read-only`) writes:
  - a markdown report: counts by N, every measure won vs lost with intervals, examples;
  - a CSV of every clutch with its measures (for the owner to sort and filter);
  - a final section, "Candidate patterns for the owner", one line each with its evidence, e.g. "won clutches
    fought one enemy at a time in 78% of engagements vs 41% in lost ones (n = ...)". No principle wording.
- Optional later, only if the owner adopts a principle: the measure as a coach detector with logged ids the checker
  trusts.

## Rules

- Read-only on the database. No new tables unless the owner asks.
- No change to `playbook.md` or its version.

## Tests

Pure tests for clutch detection (start, N, end), isolation counting on a synthetic round, and the outcome-minus-
expected comparison. Add to `tests/suites.toml`.

## Phases and done criteria

1. Count clutches by N, won and lost. Compute isolation alone; show the comparison with 5 examples of each. Done
   when the owner has watched the examples.
2. The rest of the measures and the full report. Done when the owner has the report and CSV locally.

## Open questions for the owner

- Whether 2vN situations are in scope.
- Which measures matter most to check first after isolation.
