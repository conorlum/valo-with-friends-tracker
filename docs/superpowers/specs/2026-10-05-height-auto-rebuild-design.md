# Map heights: rebuilt automatically on the replay worker (design)

Status: draft for the owner's review, 2026-10-05. Nothing here is built.

Depends on: `2026-10-05-height-slopes-design.md` (the build rules). This spec assumes
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

- a rebuild is **due** when the map has at least `HEIGHT_REBUILD_EVERY` (5) matches that were not in its
  last build (active or rejected), or when it has never been built and has at least 2 matches (O1);
- a due rebuild is sent only when the worker reports idle (no parse running or queued);
- while a map's rebuild is due or running, the dispatcher **holds that map's rounds** and keeps sending
  other maps' (O4). On the worker a rebuild job starts before any queued control round.

Resulting order of work: parse uploads, then height rebuilds, then missing rounds, then stale rounds.

**What the page shows meanwhile** (the owner, 2026-10-05): always the newest control there is. A round
computed under the previous heights stays on the page, marked out of date, until its recompute is
stored; then the page shows the new one. Nothing is hidden or blanked while the worker catches up. This
is how a stale row is already answered today (`replay_control.round_control`: status `ok`, `stale`
true), so holding a map's rounds back only delays the new version, never removes the old.

### 4. The gate, with nobody watching

When a build comes back:

| Result | What happens |
| --- | --- |
| The map is at the bar and both checks pass | stored `active`; the previous asset becomes `superseded`; that map's rounds go stale and the idle queue recomputes them |
| Below the bar, or a check fails | stored `rejected` with its report; the previous asset, if any, stays active; it is built again only after 5 more matches, so a failing map can't loop |
| The build itself fails | logged, retried with the dispatcher's existing limits |
| The map has tagged map features enabled | stored `held`: see 5 |

The bar and the two checks are the ones a local build uses today (the companion spec, section 6): 60%
of walkable cells supported, no large unresolved area beside a two-floor cell, at most 2% of real kill
lines blocked, and every must-block sightline blocked. The report also carries a comparison with the previous asset: cells gained, cells lost, and cells
whose ground moved by more than 0.5 m. It is for looking at afterwards, not a gate.

An off switch and a way back, both one command and both through `with_friends_db.py`:

- `REPLAY_HEIGHTS_AUTO` on the web app, default off: nothing changes on deploy until it is set;
- `scripts/control_heights.py list | activate --map X --digest D | off --map X`: make an earlier asset
  (or none) the active one. The map's rounds go stale and are recomputed, like any other change.

### 5. Tagged floors (the map-features contract)

A hand-tagged floor is bound to one height asset by its digest; a binding read from another asset is
pending and binds nothing (`2026-10-04-map-features-contract.md`, frozen). A rebuild would therefore
silently unbind every tagged floor on the map.

**Agreed by the owner, 2026-10-05** ("yes that's what I meant"). The aim:

- a tagged feature (a gimmick: a door, a breakable, a rope) must **persist** across rebuilds. New replay
  data never changes what the feature is or where it is;
- the **heights** under it must follow the data. When a rebuild says the floor there is at a different
  height, the feature moves with the floor, without the owner re-tagging it.

How: a binding keeps its height band (it already stores one) and
stops requiring the same digest. After each rebuild every binding is re-read against the new asset. If
its band still picks exactly one floor in each of its cells, it follows the new heights by itself. If
not (the floor moved out of the band, or the cell gained a second floor inside it), that one feature is
pending and listed for the owner, and the rest of the map still goes live. This replaces the frozen
contract's "read from another height asset is pending" rule; the contract document is amended in the
same change, with this spec named as the reason. No map has an enabled feature today, so no stored
round changes because of it.

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

## Decisions

| # | Decision | Status (owner, 2026-10-05) |
| --- | --- | --- |
| P1 | Heights move from committed files to a database table | Approved: "if that's what it takes to get the worker to recompute the heights it's worth it" |
| P2 | A build at the bar that passes both checks goes live with nobody looking | Approved |
| P3 | A failed build keeps the old heights | Approved: "keeping old heights is a fine fallback" |
| P4 | A map's rounds are held back while its rebuild is due or running; the page keeps showing the previous version until the new one is stored | Approved, with the display rule in section 3 |
| P5 | Tagged features persist and follow the new heights by their band; one that no longer fits is flagged, and the map still goes live (section 5) | Approved |
| P8 | The two checks are enough for now | Approved for now; the owner expects to add more checks to the gate later |
| P6 | First build at 2 matches, then every 5 new matches | Approved |
| P7 | The 60% bar stays as the gate | Approved (the companion spec, O5) |
