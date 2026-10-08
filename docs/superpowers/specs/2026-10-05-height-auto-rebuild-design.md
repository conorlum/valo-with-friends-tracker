# Map heights: rebuilt automatically on the replay worker (design)

Status: built 2026-10-08 (plan: docs/superpowers/plans/2026-10-05-height-auto-rebuild.md); on from the merge: `render.yaml` sets REPLAY_HEIGHTS_AUTO=true (the owner's decision, 2026-10-08).

The design below is the one the owner approved on 2026-10-05. Where the build changed it, the section carries a
dated amendment naming the plan's decision (E1 to E9, in the decisions table at the end); the owner's own
decisions (P1 to P8) are unchanged.

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

New table `control_heights` (migration 0019; this spec said 0018, which another change took first):

| Column | Meaning |
| --- | --- |
| `id` | key |
| `map_name` | the map |
| `digest` | the asset's digest (what the fingerprint already uses) |
| `asset` | the `.npz` bytes |
| `report` | the build's report (JSON), including both checks |
| `match_uuids` | the matches it was built from (JSON list) |
| `rules` | `HEIGHT_VERSION` and the constants' hash, so a rule change is visible |
| `status` | `active`, `rejected` or `superseded` |
| `built_at` | when |

One `active` row per map at most.

**Amended 2026-10-08 (plan decisions E1, E2, E3, E9).** The table gains two columns:

| Column | Meaning |
| --- | --- |
| `inputs` | the build's **input manifest** (`app/replays/height_inputs.py`): each replay's uuid, blob recipe, source hash and round count; the sight and walk masks and the scale; `HEIGHT_VERSION` and `HEIGHT_RULES_REVISION`; the must-block file's hash |
| `inputs_sha` | the manifest's digest: what names the build on the worker and what a result must prove it was built from |

`match_uuids` are the manifest's matches. `rules` holds the format (`HEIGHT_VERSION`), the rules' revision
(`HEIGHT_RULES_REVISION` in `app/replays/control_format.py`, bumped by hand when the build's rules change and
pinned by a test to a hash of `app/control/heights.py`'s constants) and that hash. An active row built under
another `HEIGHT_VERSION` is ignored: the map is flat until its next build, which is due at once (E1).
Superseded and rejected rows keep their asset bytes and nothing prunes them, so `activate` can go back (E9).

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

**Amended 2026-10-08 (plan decisions E2, E4, E8; run decision D13, pending the owner's approval).**

- **Input:** every stored round of the map's valid replays at condenser revision 11 or later, as the input
  manifest names them. The child (`replay_worker/height_job.py`) computes the manifest itself from its own
  masks, rules and check set and the replays the web app named, and refuses one it would not have computed
  ("these aren't my inputs"), as it refuses rounds that aren't the manifest's. Either refusal is a failed build.
- **The must-block check fails when its file can't be read**: a missing or unreadable list is not "nothing to
  check". The list lives at `webapp/app/static/data/control/must_block.json` (E8), so the worker image has it.
- **Run alone.** A build starts only when nothing else is running on the control side and no parse is running
  or waiting; nothing starts beside it; it is queued ahead of every control round. A parse arriving always
  kills a running build (where control keeps one child beside a parse, a build keeps none), and the build goes
  back to `queued` with its rounds, uncounted. (Run decision D13, pending the owner's approval.)

### 3. When it runs

The web app's dispatcher decides, once per cycle, per map:

- a rebuild is **due** (amended 2026-10-08, plan decisions E2 and E5; this bullet said "at least
  `HEIGHT_REBUILD_EVERY` (5) matches that were not in its last build, or never built and at least 2
  matches"). The map's current input manifest is compared with its last build's, whatever that build's
  status (`replay_heights_remote.plan_maps`):
  - **no build yet:** due once the map has 2 matches (`FIRST_BUILD_MATCHES`; O1);
  - **the same inputs:** not due; a rejected build is not built again until something changes;
  - **evidence gone** (a match deleted, or its blobs replaced) **or inputs changed** (the masks, the rules or
    the check set): due at once, not after 5 more matches;
  - **only matches added:** due once `HEIGHT_REBUILD_EVERY` (5) of them are new.

  A map with a published feature generation is not rebuilt automatically (section 5, E6);
- a due rebuild is sent only when the worker reports idle (no parse running or queued);
- while a map's rebuild is due or running, the dispatcher **holds that map's rounds** and keeps sending
  other maps' (O4). On the worker a rebuild job starts before any queued control round.

Resulting order of work: parse uploads, then height rebuilds, then missing rounds, then stale rounds.

**Amended 2026-10-08 (what was built).**

- **Where the step sits in a cycle.** The dispatcher's cycle collects finished rounds, then runs the automatic
  re-parse pass, then the height step (in a database session of its own; a failure in it is logged, holds
  nothing that cycle, and round dispatch carries on), then sends rounds. A held map's timing-gaps work waits
  with its control rounds.
- **The worker's five build states**, which every answer names (`replay_worker/server.py` `HeightBuilds`):

  | State | What the web app may do |
  | --- | --- |
  | `collecting` | send rounds (the same round with the same bytes again is free; other bytes are a conflict), start, cancel |
  | `queued`, `running` | wait and ask |
  | `done` | read the result; the newest 8 ended builds stay readable |
  | `failed` | open it again: the same key starts a fresh, empty build |
  | unknown (404) | open it again: the worker restarted, or dropped a collector nobody fed |

  The build's key is its input manifest's digest. Every cycle the dispatcher asks the worker what state the
  build is in and acts on whichever it finds: a restart of the web app adopts the build, a lost response is
  found on the next cycle, and a worker that forgot the build is sent it again from the first round. One
  build at a time (E7).

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
| The build itself fails | logged, retried with the dispatcher's existing limits (amended 2026-10-08: split into the failure table below) |
| The result isn't what it says (malformed, another build's, or failing the integrity half of the gate) | never stored; a failed try (added 2026-10-08, E4) |
| The map's inputs changed while it built | dropped, at no cost; the next cycle plans again (added 2026-10-08) |
| The map has tagged map features enabled | each feature is re-read against the new heights; one that no longer fits is flagged and the map still goes live: see 5 |

**Amended 2026-10-08 (plan decisions E4, E7): what a failure costs.** A build has `MAX_TRIES` (3) tries per
input manifest, counted in the dispatcher's memory, so they start again when the web app restarts (E7; the
owner wants fresh tries after a deploy, which may be what fixes a broken build). When they are spent the map's
rounds are released and sent with the heights it has, and no more tries are made until its inputs change.

| What happened | Counts as | Then |
| --- | --- | --- |
| The worker can't be reached, or answers 503 | nothing | try again next cycle |
| The worker answers 404 (including the open endpoint), or reports rounds missing at start | nothing, up to 2 times combined per try | reopen and send again; after that, a failed try |
| The build is `queued` while the worker is parsing | nothing | wait: a parse always goes first |
| `queued` on an idle worker for 15 minutes, `running` for 2 hours, or not all sent 30 minutes after it was opened | a failed try | cancelled if it is still collecting |
| The worker refuses a request (400, 413), holds other bytes for a round, or a round is larger than a batch | a failed try | cancelled |
| The build fails on the worker, including "these aren't my inputs" | a failed try | |
| The result is malformed, is another build's, or fails integrity | a failed try | nothing stored |
| The map's inputs changed while it built or was being sent, or a feature generation appeared before it was stored | nothing | the build is dropped; the next cycle plans again or skips the map |
| The worker's answer isn't shaped as the protocol says | a failed try | the build is dropped |
| The result's `rules` are not this deploy's format and rules revision | a failed try | nothing stored |
| Another change to the map's heights won the map's lock | a failed try | |
| A match is deleted between the last check of the inputs and the commit | not caught here | the row is stored; the next cycle sees the evidence gone (below) |
| The trusted result fails the gate | not a failure | stored `rejected`; not rebuilt until its inputs change |
| Anything unforeseen in the height step | logged | that cycle holds nothing; round dispatch carries on |

A retry does not always rebuild: the worker answers a key with the build it has, so a try after a `done` build
whose result couldn't be used reads the same answer again. The owner chose to leave it so (2026-10-07).

**The gate has two halves** (amended 2026-10-08, E4), in this order:

1. **Integrity**: the result is this build's (key, map, input digest, round count); its asset bytes are the
   asset it names, of this format, with valid arrays and topology; the report's walkable count and readiness
   match what is derived from the actual map; and the must-block lines, rerun on the asset, give the report's
   counts. The asset is opened by a child process of the web app (`app/control/height_verify.py`), never by
   the web app itself. A failure is the build's failure: retried, nothing stored.
2. **Policy**: the bar and the two checks above. A failure is stored `rejected`.

**Deletions** (amended 2026-10-08, plan decision E5, **approved by the owner 2026-10-07**: "fine"). When the
evidence under a map's heights is removed or replaced (a deleted match, a re-condensed blob), its rebuild is
due at once, not after 5 more matches. If that rebuild is rejected, or fewer than 2 matches are left, the map's
heights are turned **off** rather than left built from evidence that is gone. This follows "its heights go at
the next rebuild; that is the intended meaning of a deletion" above. P3 ("keeping old heights is a fine
fallback") still holds for a build that fails with its evidence intact: the old heights stay.

The bar and the two checks are the ones a local build uses today (the companion spec, section 6): 60%
of walkable cells supported, no large unresolved area beside a two-floor cell, at most 2% of real kill
lines blocked, and every must-block sightline blocked. The report also carries a comparison with the previous asset: cells gained, cells lost, and cells
whose ground moved by more than 0.5 m. It is for looking at afterwards, not a gate.

An off switch and a way back, both one command and both through `with_friends_db.py`:

- `REPLAY_HEIGHTS_AUTO` on the web app, default off in the code and set on in `render.yaml` (amended
  2026-10-08: the owner wants it on from the merge); setting it false stops every automatic rebuild;
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

**Amended 2026-10-08 (plan decision E6; what was built).** A floor binding records `origin_z`, the lowest floor
of the asset it was read from, and its band is rebased to the current asset's lowest floor, so it follows its
floor across rebuilds, a changed origin included. A binding without `origin_z` is pending once the asset
changes. A binding that no longer fits is listed in that rebuild's report. The contract document carries the
matching amendment.

Not delivered: a published feature generation does not survive a rebuild. A map that has one is not rebuilt
automatically, and `activate` and `off` refuse it. This narrows "the map still goes live" above. The owner
approved it as built on 2026-10-08, as the interim: no map has a published generation, and none can get one
until an engine consumer is registered. A follow-up design
(`docs/superpowers/specs/2026-10-08-map-features-survive-height-rebuilds-design.md`) replaces the guard, so
that tagged gimmicks never stop a map's heights from rebuilding.

### 6. Seeing what it did

The height viewer gets a third source, `--db`, beside previews and committed assets: the active asset
of each map, and with `--all` the rejected and superseded ones too. It is read-only and run through
`with_friends_db.py --read-only`, like every other look at the live database.

The replay worker's `/health` says whether a rebuild is queued or running and for which map.

**Amended 2026-10-08 (what was built).** `scripts/control_heights.py export --map X --out <folder>` writes a
map's active asset and its report to a folder outside the repository, in the layout of a preview, read with
`height_viewer.py --dir` or the tagger's `control_tagger.py --heights-dir <folder>`, so features can be tagged
against the heights the database holds. `list` shows each build's map, digest, status, date, match count,
supported share, both checks, time taken and, for a rejected one, why. `/health` also says `idle` (no parse
running or waiting) and `heights: {collecting, queued, running, spool_bytes}`.

### 7. Turning it on (added 2026-10-08)

Amended 2026-10-08: it is on from the merge. `render.yaml` sets `REPLAY_HEIGHTS_AUTO=true` on
valowithfriendstracker, and both services auto-deploy from the merge commit (the web services run
`alembic upgrade head`; migration 0019 creates an empty table on each; the worker's image is rebuilt). The web
service is usually up first. While the worker still answers as the old deploy (another recipe, or no `heights`
field in its health), the dispatcher waits at no cost: no try spent, no rounds held
(`replay_heights_remote._other_deploy`, pinned by
`test_heights_remote.py::test_a_worker_of_another_deploy_is_waited_for_at_no_cost`).

The owner's checks afterwards; none was run by the build:

```
1. Check the worker: GET /health shows "idle" and "heights".
2. Within a few cycles the dispatcher logs "heights: <Map> rebuild (first build), N rounds" and then
   "<Map> <digest> active" or "... rejected (...)" for each map with two or more matches.
3. Look: scripts\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\control_heights.py list
   and scripts\height_viewer.py --db [--all] the same way.
4. To stop: REPLAY_HEIGHTS_AUTO=false (in render.yaml too, or a Blueprint sync turns it back on). To go back on
   one map: control_heights.py activate / off.
```

Two services on different commits that share a recipe but not masks or the check set (one service deployed by
hand) still refuse each build, and three refusals spend that map's tries until the web app restarts.

## Cost, and what isn't known yet

- **The worker's cost on the live site is not measured yet.** Nothing has run there: the numbers below are
  the desk estimate, and the first live cycle's own numbers replace them (the plan's Task 10, Step 5).
- **The desk estimate, measured on the development machine from the live freeze's frozen rounds** (the slopes plan's live freeze, Sunset
  `rounds` digest `bf72113396d33aba`; Sunset is also the map with the most matches in it), with
  `scripts/measure_height_rebuild.py --map Sunset`, 2026-10-08:

  ```
  Sunset: 6 matches, 120 rounds
    sent: 4.6 MB stored, 6.1 MB as base64, 4 batches of 2 MB; largest round 77 KB
    build with both checks: 28 s, peak memory 0.19 GB
    visibility warm-up under the new heights: 75 s
    control: 73 s a round over 3 rounds; all 120 on 2 workers: 73 min
    one cycle here: 75 min (the worker is slower)
  ```

  Recomputing the map's rounds is almost all of a cycle; it grows with the map's history. The batch size is
  2 MB of base64 a request, against the worker's 4 MB request cap: the largest round is far under it. The
  peak memory is that of a build reading one round at a time; Task 5 of the plan repeats it with the
  worker's own round reader.
- The worker's own figures (a build's `seconds` and `peak`, from `control_heights.py list`, and the time from
  a build going active to its map's last round being recomputed) are recorded here from the first live cycle;
  see the plan's Task 10. Until then they are not measured.
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

The plan's decisions (added 2026-10-08; `docs/superpowers/plans/2026-10-05-height-auto-rebuild.md`, where each
has its reason):

| # | Decision | Status (owner, 2026-10-07) |
| --- | --- | --- |
| E1 | An active row built under another `HEIGHT_VERSION` is ignored: the map is flat until its next build, which is due at once (section 1) | Approved as proposed |
| E2 | A build's identity is its input manifest (replays' uuids, blob recipes, source hashes and round counts; masks and scale; `HEIGHT_VERSION` and `HEIGHT_RULES_REVISION`; the must-block file's hash), stored as `inputs` and `inputs_sha` (sections 1 to 3) | Approved as proposed |
| E3 | `HEIGHT_RULES_REVISION` is bumped by hand when the build's rules change, and a test pins it to a hash of the height constants (section 1) | Approved as proposed |
| E4 | The gate has two halves: integrity (a failure is the build's, retried, nothing stored), then policy (a failure is stored `rejected`); the asset is opened by a child process of the web app (section 4) | Approved as proposed |
| E5 | Evidence removed or replaced makes the rebuild due at once; a rejected rebuild, or fewer than 2 matches left, turns the map's heights off (section 4) | Approved: "fine" |
| E6 | A map with a published feature generation is not rebuilt automatically, and `activate` and `off` refuse it (section 5) | Approved as built 2026-10-08, as the interim; replaced by the follow-up design `2026-10-08-map-features-survive-height-rebuilds-design.md` |
| E7 | One rebuild at a time, run alone on the worker; `MAX_TRIES` per input manifest, kept in the dispatcher's memory (sections 3 and 4) | Approved, with the note that a deploy may be what fixes a broken build, so fresh tries after one are wanted |
| E8 | The must-block list moves to `webapp/app/static/data/control/must_block.json` (section 2) | Approved as proposed |
| E9 | Superseded and rejected rows keep their asset bytes; nothing prunes them (section 1) | Approved as proposed |
