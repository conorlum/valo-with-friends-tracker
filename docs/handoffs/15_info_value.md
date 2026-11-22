# Handoff 15: Info value: how much unknown each util clears or holds

Read `docs/handoffs/README.md` (ground rules, shared building blocks), `CLAUDE.md` (especially "Critical
Util") and `docs/superpowers/specs/2026-10-21-critical-util-design.md` ("Hold space") first. Design spec for the
owner's approval before code.

## Goal

For every util, measure how much of the enemy-could-be-here space (the map-control unknown) it cleared or held,
and what that led to. Two different jobs, never ranked on one scale:

- **Hold:** trips, cameras, turrets, area watchers, mollies. The unknown stops at the util's edge. Trips will hold
  far more than anything else; that is expected, so compare within each kind.
- **Clear:** recon darts, drones, Fade's prowlers and Haunt, Gekko's Wingman scan, Sova's ult reveals, any reveal
  or area shown empty. The unknown removed at once.

## What already exists (the hold side is mostly built)

`app/critutil/unknown.py`, the **unknown probe**, runs in the gap run beside the gap detector
(`app/control/task.py`, stepping `app/control/observe.py` `TickRecord`s). For each trip, area watcher, camera or
turret and each molly, while it was up (a device watching with its owner alive, a molly burning), its team had
enemies alive and the round was not decided, it measures:

- `front_s`: seconds the unknown sat right at its edge without being inside it (the util was the line the unknown
  stopped at);
- `held_m2s`: square metres × seconds of its cells no teammate's own view covered (ground only it held);
- `crossed_s`: seconds the unknown was inside its cells (an enemy could be past it).

It is pure numpy over the record and never changes the engine's arrays. Results are stored in
`replay_round_util` with the gap run's keys.

The engine's `Unknown` (`app/control/engine.py`: `Unknown.apply`, `_locating`) keeps the unknown per enemy and
shrinks it on sight, reveals, sounds (attributed per the hearing design), kills and the plant; it spreads at
`UNKNOWN_MPS` 3.24 m/s.

## New work

### 1. Clear measures for reveal util

Per reveal cast, from the caster's side's unknown:

- unknown area (m²) just before the reveal and 0.5 s after; the difference is the area cleared;
- per enemy: how much that enemy's own unknown shrank (located: from a large area to near a point), and how
  many enemies were located;
- "shown empty": cells the reveal covered where no enemy was, removed from every enemy's unknown;
- duration: m² × s, how long the cleared area stayed cleared before the unknown spread back.

Check first how reveals enter the engine (reveal statuses, the drone's sight, Sova's dart pulses) so the probe
reads the same events the engine already applies; do not re-model reveals.

### 2. What it led to

- Kills on located enemies within N s (propose 5 s; Critical Util already links kills to casts with evidence).
- Teammates acting on it: a teammate moving into cleared space, or turning to face a located enemy, within N s.
- The win-chance change after it (`app/services/util_model.py`).
- Credit: the caster for the info; "acted on" credit to whoever got the kill or moved, recorded separately.

### 3. Hold measures, presented per kind

Use the probe's numbers as they are, with a per-kind table (trip vs trip, camera vs camera, molly vs molly) and
per-player comparisons against the kind's average.

### 4. Value (optional, after 1–3)

If the measures add something, a "cleared info" job in Critical Util's fitted weights. That changes the util
build: it is adopted only if it beats the current weights on held-out matches (the build's own adoption rule),
and the build digest changes on both site and worker.

## Output

- **Critical Util tab, per cast:** clears: "cleared 340 m², found 2 enemies (their unknown 410 → 25 m²); Name
  killed one 3.1 s later". Holds: "held B Main for 41 s, crossed 0 s".
- **`/util`:** per ability, median m² cleared or held, found-enemy rate, acted-on rate; per friend vs the ability's
  average. Holds and clears in separate tables.

## Storage and pipeline

- In the gap run, next to the unknown probe, pure numpy like `unknown.py`. Bump the gap protocol and
  `GAPS_REVISION`; the site and the worker image must both speak it (`replay_worker/Dockerfile` ships
  `app/critutil`).
- The build digest (`app/critutil/build_digest.py`) changes if the build reads the new fields.

## Tests

Synthetic unknown grids: a reveal over an area with and without enemies, located-enemy shrink, re-spread timing;
the per-kind hold tables. Add to `tests/suites.toml`; run the slow lane before merging gap-run changes.

## Phases and done criteria

1. Read-only on 3 replays: for every reveal cast, unknown m² before and after, enemies located, the 5 largest
   for checking in the viewer. Done when the owner has checked them.
2. Clear measures and "led to" in the gap run. Done when stored rows carry them and tests pass.
3. UI. Then, only if wanted, the value job.

## Open questions for the owner

- N for "led to".
- Whether to show holds and clears on one page or two.
