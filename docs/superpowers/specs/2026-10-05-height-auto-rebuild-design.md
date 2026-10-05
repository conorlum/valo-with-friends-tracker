# Map heights: rebuilt automatically on the replay worker (design)

Status: draft for the owner's review, 2026-10-05. Nothing here is built.

Depends on: `2026-10-05-height-slopes-design.md` (the build rules and the lower bar). This spec assumes
that one is built and its rules are trusted without a look at every build.

Background: `docs/map-control-worker-plan.md`, `docs/superpowers/plans/2026-10-05-control-idle-queue.md`
(merged in PR #116), `2026-10-01-control-heights-design.md`, `2026-10-04-map-features-contract.md`.

## What the owner asked for (2026-10-05)

| # | Request |
| --- | --- |
| O1 | Compute heights for the replays there are now. |
| O2 | Rebuild a map's heights every 5 or so new matches of that map. |
| O3 | Run the rebuild on the replay worker, when there is no replay to ingest. |
| O4 | The rebuild goes ahead of recomputing rounds, because new heights make that map's rounds out of date: computing them first would be wasted work. |
| O5 | Each new replay adds to what is known; losing an old `.vrf` to the archive limit must not lose height data. |

## What already exists (main, after PR #116)

- The worker recomputes every round whose control is **missing or stale**, newest match first, only
  while nothing is parsing. A parse arriving kills the running rounds and puts them back at the front of
  the queue.
- A map's height digest is part of each round's fingerprint (`replay_control.geometry_inputs`). So once
  a new height asset is the map's current one, every stored round of that map is stale, and the idle
  queue recomputes them without anything new being built.
- The replay page says which control revision a round was computed under, or why it is out of date.
- Heights are built from the rounds **stored in the database**, not from `.vrf` files. Archive eviction
  deletes only the file, so O5 already holds as long as stored rounds are kept. No cap on them was found
  in the replay services; how much database space they use has not been checked. A deletion request does remove a match's rounds, and its heights go at the next rebuild; that is
  the intended meaning of a deletion.

So three things are missing: somewhere for a worker-built asset to live, the rebuild job itself, and a
gate that works with nobody watching.

## Design

### 1. Heights live in the database

Today a height asset is a file committed to the repo and baked into both deploys. The worker can't
commit, and the web app drops any result whose geometry differs from its own deploy's.

New table `control_heights` (migration 0018):

| Column | Meaning |
| --- | --- |
| `id` | key |
| `map_name` | the map |
| `digest` | the asset's digest (what the fingerprint already uses) |
| `asset` | the `.npz` bytes |
| `report` | the build's report (JSON), including both checks |
| `match_uuids` | the matches it was built from (JSON list) |
| `rules` | `HEIGHT_VERSION` and the constants' hash, so a rule change is visible |
| `status` | `active`, `rejected`, `superseded` or `held` |
| `built_at` | when |

One `active` row per map at most.

- `geometry_inputs` reads the map's active digest from this table. The committed `index.json`
  `height_sha` stays as a fallback for a map with no row, so tests and a checkout without a database
  behave as now. No map has a committed height today.
- The engine gets the asset by digest. Locally (`compute_control.py`) it is read from the database.
  On the worker it is pushed: `POST /heights {map, digest, asset}` stores it in the worker's control
  cache, and each control task names the digest it needs. A worker that doesn't have the digest answers
  so, the web app pushes it and asks again. The cache is lost on redeploy, which is harmless.
- The demo site never has heights and never runs any of this.

### 2. The rebuild job

A second kind of job on the worker's control side, built from the same parts as a control round: a child
process run by the control venv, niced, time- and memory-capped.

- **Input:** every stored round of the map at condenser revision 11 or later, from valid replays, sent
  by the web app in batches (the worker has no database).
- **Work:** `height_build.build`, then the kill-line check and the must-block check, exactly what
  `scripts/build_control_heights.py` runs. The must-block lines move from `tests/replays/` to the
  control data folder so the worker image has them.
- **Output:** the asset bytes, the report and the check results.
- **A parse preempts it** like any control job: killed and requeued, nothing counted as a failure.

### 3. When it runs

The web app's dispatcher decides, once per cycle, per map:

- a rebuild is **due** when the map has at least `HEIGHT_REBUILD_EVERY` (5) matches that are not in its
  active asset's `match_uuids`, or when it has no asset at all and at least 2 matches (O1);
- a due rebuild is sent only when the worker reports idle (no parse running or queued);
- while a map's rebuild is due or running, the dispatcher **holds that map's rounds** and keeps sending
  other maps' (O4). On the worker a rebuild job starts before any queued control round.

Resulting order of work: parse uploads, then height rebuilds, then missing rounds, then stale rounds.

### 4. The gate, with nobody watching

When a build comes back:

| Result | What happens |
| --- | --- |
| Both checks pass, at least 2 matches | stored `active`; the previous asset becomes `superseded`; that map's rounds go stale and the idle queue recomputes them |
| A check fails | stored `rejected` with its report; the previous asset stays active; the same match set is never built again, so a failing map can't loop. The next new match makes it due again |
| The build itself fails | logged, retried with the dispatcher's existing limits |
| The map has tagged map features enabled | stored `held`: see 5 |

The supported share and unresolved areas are recorded but don't refuse a build (the companion spec's
bar). The report also carries a comparison with the previous asset: cells gained, cells lost, and cells
whose ground moved by more than 0.5 m. It is for looking at afterwards, not a gate.

An off switch and a way back, both one command and both through `with_friends_db.py`:

- `REPLAY_HEIGHTS_AUTO` on the web app, default off: nothing changes on deploy until it is set;
- `scripts/control_heights.py list | activate --map X --digest D | off --map X`: make an earlier asset
  (or none) the active one. The map's rounds go stale and are recomputed, like any other change.

### 5. Tagged floors (the map-features contract)

A hand-tagged floor is bound to one height asset by its digest; a binding read from another asset is
pending and binds nothing (`2026-10-04-map-features-contract.md`, frozen). A rebuild would therefore
silently unbind every tagged floor on the map.

No map has an enabled feature today, so the first version takes the safe side: a map whose `index.json`
entry has a `features_sha` never activates a rebuilt asset automatically. The asset is stored `held`,
and the owner re-exports the map's features against it and activates it by hand. Rebinding
automatically (when each band still picks exactly one floor) is a change to the frozen contract and is
left for when a feature is actually enabled.

### 6. Seeing what it did

The height viewer gets a third source, `--db`, beside previews and committed assets: the active asset
of each map, and with `--all` the rejected and held ones too. It is read-only and run through
`with_friends_db.py --read-only`, like every other look at the live database.

The replay worker's `/health` says whether a rebuild is queued or running and for which map.

## Cost, and what isn't known yet

- **Each rebuild recomputes every round of that map.** The work per rebuild grows with the map's
  history. Time per round on the 2-CPU worker has not been measured for this purpose; the first task is
  to measure a build and a round for Sunset and report minutes per rebuild cycle.
- **The first round after new heights rebuilds the map's visibility cache** (minutes, already true after
  any deploy).
- **Batch size.** A map with 20 matches is about 400 round blobs sent to the worker per rebuild. Their
  total size has to be measured against the worker's request cap before the batch protocol is fixed.
- If a rebuild turns out too slow at scale, the fallback is the "master" the owner described: keep each
  round's ground samples once (by match), so a rebuild reads summaries instead of whole rounds. It is
  not built first because it freezes the extraction rules into stored data.

## Out of scope

- Changing how a round's control is computed, or `CONTROL_REVISION`.
- Rebuilding sight and walk masks, tags or barriers: those stay committed files.
- The demo site.

## Decisions that need the owner's yes

| # | Proposed | Why it needs saying |
| --- | --- | --- |
| P1 | Heights move from committed files to a database table | changes where the source of truth is; a checkout alone no longer says what heights the site uses |
| P2 | A build that passes both checks goes live with nobody looking | replaces the picture review before commit |
| P3 | A failed build keeps the old heights and is not retried until a new match arrives | a map can sit on old heights while new matches fail |
| P4 | A map's rounds are held back while its rebuild is due or running | those rounds show "out of date" a little longer, in exchange for not computing them twice |
| P5 | Maps with tagged features never auto-activate (5) | manual step, in exchange for leaving the frozen contract alone |
| P6 | First asset at 2 matches, then every 5 new matches | the "5 or so" from O2, plus a start rule |
