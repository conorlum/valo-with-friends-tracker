# Height auto rebuild: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The replay worker rebuilds a map's heights whenever it has 5 new matches (the first time at 2), ahead of recomputing that map's rounds; a build that reaches the bar and passes both checks goes live with nobody looking, a failed one keeps the old heights, and one command turns it off or puts an earlier asset back.

**Architecture:** Heights move from committed files to a `control_heights` table; the map's active digest there is what a round's fingerprint reads, so activating an asset makes that map's rounds stale and the existing idle queue recomputes them. A stdlib module both services share, `app/replays/height_inputs.py`, says what a build is made from (its **input manifest**: the replays' blob identities, the masks, the rule revisions, the check set); that manifest's digest names the job on the worker, decides when a rebuild is due, and is what a result must prove it was built from before it is activated. The worker has no database, so the web app's dispatcher drives it: each cycle it asks the worker what state the build is in and acts on that (send rounds, start, wait, collect), so a restart on either side resumes instead of looping. The engine gets an asset by digest: pushed to the worker's cache, read from the database locally.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL (SQLite in tests), stdlib `http.server`/`subprocess`/`threading` on the worker, numpy/scipy in the control venv and in one verifying child of the web app.

**Spec:** `docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md`. It depends on `2026-10-05-height-slopes-design.md`, whose plan (`2026-10-05-height-slopes.md`) must be merged first. This plan asks for changes to the spec and to the frozen map-features contract; each is written as a step of a task (Tasks 2, 8 and 10), never made silently.

## What has and hasn't been measured or checked

| Claim | Status |
| --- | --- |
| A stored round is about 44 KB gzipped (2.8 MB for 64 local rounds), so a 20-match map is about 18 MB, 24 MB as base64 | **Prototype measurement** on `%TEMP%\valo-replay\heights-local` (3 matches). Task 1 repeats it on the frozen rounds of the slopes plan. |
| A map's rounds decoded at once don't fit the worker's 2 GB child cap, so the build reads one round at a time | **Estimate** from the 125 Hz rate; not measured on the frozen dataset. Task 1 measures a streaming probe; Task 5 repeats the authoritative gate with BlobDir. |
| 22 Sunset rounds build in about 5 s on the development machine | **Prototype measurement**, without the kill-line check. |
| The cost of a whole rebuild cycle on the worker: the build, the first round's visibility warm-up under the new heights, and recomputing every round of the map | **Not measured.** The spec asks for it. Task 1 measures all three locally and gives an estimate; Task 10 records the first live cycle's own numbers. |
| Activating the digest that is already active turns heights off (review finding 7) | **Reproduced 2026-10-07** in a scratch script (SQLAlchemy on SQLite, a stand-in table): retiring the active row with a bulk update and then assigning `ACTIVE` on the loaded row leaves `superseded` in the database; this plan's `_make_active`, run from its code block, leaves `active`. Not run against the real model or PostgreSQL: Task 2's tests do that. |
| The migration number and what this plan stacks on | **Checked 2026-10-07:** `origin/main` (`0caa172`) has one alembic head, `0017`, so `0018` is free today; its `CONTROL_REVISION` is 6, so the slopes plan takes 7. Check both again on the day (Task 1, Step 1). |
| Revised review fixes, checked 2026-10-07 | **Scratch verification:** 56 focused tests passed using the literal revised blocks with the slopes changes overlaid in memory on unchanged worktree modules: 20 motion/measurement, 15 iterator/verifier, 19 integrity-policy and 2 cost-selection tests. Separate assertions passed for the planned model/service on in-memory SQLite, the literal dispatcher with isolated planning (malformed health, open/known-build 404 exhaustion, no charge for unreachability/503, generation on collection), same-digest viewer history, and the handler method's 400/404 distinction. This is not a full implementation run. |
| Remaining integration gates | **Not run after these corrections:** the full replay suite, real HTTP restart/dispatcher fixtures, the web app's verifier subprocess integration, PostgreSQL/concurrency, migration, frozen-data timing/memory and live cycle. Run the task gates after transcription; this review changed plans only. |
| Everything else in this plan's code | **Written against the code as it stands and not run, except for the focused scratch checks above.** It builds on the slopes plan, which isn't built. Expect small slips (a fixture's name, an import, an exact count); fix those in place. A test that fails for a reason the plan didn't foresee is reported, not rewritten to pass. |

The spec's own decisions (P1 to P8) have the owner's approval. Of the decisions below, the owner answered all but
one on 2026-10-07: E1 to E4, E8 and E9 approved as proposed; E5 approved; E7 approved, with the note that a deploy
may be the very thing fixing a broken build, so fresh tries after one are wanted. E6 was looked at and not
decided: the guard is built as the default.

## Decisions this plan takes that the spec does not (for the owner to confirm at review)

| # | Decision | Why |
| --- | --- | --- |
| E1 | An active row built under another `HEIGHT_VERSION` is ignored: the map is flat until its next build, and that build is due at once. | `load_asset` refuses another version, so every round of the map would fail on the worker after a format change. |
| E2 | **A build's identity is its input manifest**, not its list of matches: each replay's uuid, blob recipe, source hash and round count; the sight and walk masks and the scale; `HEIGHT_VERSION` and a new `HEIGHT_RULES_REVISION`; the must-block file's hash. The table gains two columns for it, `inputs` and `inputs_sha` (a change to the spec's section 1). | Matches alone miss a re-condensed blob, a changed rule that keeps the format, a re-drawn mask and a changed check set: the worker could hand back an old result for new inputs, or a needed rebuild would never be due (review finding 5). |
| E3 | `HEIGHT_RULES_REVISION` (stdlib, in `app/replays/control_format.py`) is bumped by hand when the build's rules change, and a test pins it to a hash of `heights.py`'s constants, as `CONTROL_REVISION` is pinned. | The web app can't import the numpy module that holds the constants, and a rule change must make every map's rebuild due. A hash of source files would rebuild every map on a comment edit. |
| E4 | The gate has two halves. **Integrity** (the result is the job's, its asset bytes and topology are valid, the report agrees with readiness derived from the actual map and must-block checks independently rerun on the asset): a failure is the build's failure, retried, nothing stored. **Policy** (the bar and the two checks): a failure is stored `rejected`. The asset is opened by a child process of the web app, never by the web app itself. | The spec's gate assumes an honest, whole result. A missing must-block file passed as "nothing to check", and arbitrary bytes were accepted as an asset (review finding 9). |
| E5 | **When the evidence under a map's heights is removed or replaced** (a deleted match, a re-condensed blob), its rebuild is due at once, not after 5 more matches. If that rebuild is rejected, or fewer than 2 matches are left, the map's heights are turned **off** rather than left built from evidence that is gone. | The spec: a deletion's "heights go at the next rebuild; that is the intended meaning of a deletion". The owner's P3 ("keeping old heights is a fine fallback") was about a build that fails, not about data that was deleted. The two pull against each other here. **Owner, 2026-10-07: approved as written ("fine"): the heights go off.** |
| E6 | **A map with a published feature generation** (index.json `features_sha`) **is not rebuilt automatically, and `activate` refuses it**, until generations can survive a rebuild. Task 8 makes a floor binding follow its floor across rebuilds (including a changed origin) and reports features that no longer fit; it does **not** make a published generation survive, so the owner's P5 is delivered for bindings only. | A generation's manifest names the height digest, so after a rebuild its verification fails and every round of the map would fail. No map has a generation today. **This narrows the owner's decision that a map with tagged features still goes live** (spec section 5); holding is the smaller harm, but it is the owner's call. The alternative is to build generation compatibility first. **Owner, 2026-10-07: not decided ("I still don't understand but I don't think this matters much"). The guard is built as the default; this is not an approval of narrowing P5, and it is asked again before any map gets a published generation.** |
| E7 | One rebuild at a time; on the worker it runs alone. Its retry budget (`MAX_TRIES` per input manifest) lives in the dispatcher's memory and starts again when the web app restarts. | Memory, and the spec's "retried with the dispatcher's existing limits", which are in memory too. A build that fails every time costs three tries per web-app restart. |
| E8 | The must-block list moves to `webapp/app/static/data/control/must_block.json`, which the site serves like the other control assets. | The spec: "to the control data folder so the worker image has them". It holds sightline coordinates. |
| E9 | Superseded and rejected rows keep their asset bytes; nothing prunes them. | A few hundred KB each; `activate` needs them to go back. |

## Corrections from the 2026-10-07 review

The literal steps and tests now validate raw asset structure, derive readiness from the real map, rerun its
must-block lines, guard result storage when a generation appears, bound 404s and malformed health responses,
measure a streaming workload using the requested map only, distinguish malformed requests from unknown
build IDs, update changed test-double signatures, and preserve distinct viewer rows at the same digest.
These are plan corrections, not a claim that the product is implemented. E6 stays undecided with its default
guard; all accepted thresholds, E5, retries of the same done answer and the separate input-check/map-lock
transactions remain as approved.

## Global Constraints

- The slopes plan is merged: `HEIGHT_VERSION` is 2, `height_build.all_ground` reads its rounds once, the asset has `kind` and five edge columns, and `%TEMP%\valo-replay\heights-frozen` holds the frozen rounds.
- Work on a branch cut from `origin/main` after that merge. Run tests from `webapp/` with the main checkout's interpreter: `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv313\Scripts\python.exe -m pytest ... -p no:cacheprovider` (written `PY -m pytest` below).
- Baseline: before Task 1, run `PY -m pytest tests/replays -k "not pg" -q` on the unchanged base and write down every failure with its message. A test is exempt from a task's gate only if it failed in that run with the same message. Nothing is exempt by its exception type alone.
- **The migration's number is whatever is free on the base.** Run `PY -m alembic heads` first: this plan writes `0018` and `down_revision = "0017"`; if the head is not `0017`, use the next number and the real head, everywhere the plan says 0018.
- Values, verbatim from the spec, none of which changes here: `HEIGHT_REBUILD_EVERY` 5; first build at 2 matches; rounds at condenser revision 11 or later from valid replays; table `control_heights` with columns `id, map_name, digest, asset, report, match_uuids, rules, status, built_at` (plus E2's two); statuses `active`, `rejected`, `superseded`; at most one `active` row per map; `REPLAY_HEIGHTS_AUTO` on the web app, **default off**; `scripts/control_heights.py list | activate --map X --digest D | off --map X`; viewer `--db` and `--all`.
- The bar and the checks are the ones a local build uses: `HEIGHT_SUPPORTED_MIN` 0.60, no unresolved area over `UNRESOLVED_MAX` beside a two-floor cell, at most `KILL_LINE_BAR` 2% of kill lines blocked, every must-block line blocked. Their values move to a stdlib module so the web app can read them; **the values do not change**.
- A build that fails the gate keeps the old heights (P3), except as E5 says.
- **Out of scope, do not touch:** `CONTROL_REVISION`, how a round's control is computed, Impact scoring, sight and walk masks, tags, barriers.
- The demo site never has heights and never runs any of this: every new loop is behind `enabled()` checks that are False in demo mode; the migration creates an empty table there.
- The web app never imports `app.control` or numpy through the new services (it starts one child process to open an asset); the worker's server process never imports numpy or `app.control` (`tests/replays/test_control_isolation.py`).
- This plan never reads or writes the live database, and changes nothing on Render. Turning `REPLAY_HEIGHTS_AUTO` on, rebuilding the worker image and running the migration are the owner's steps, written down in Task 10.
- This repository is public. Never commit a credential; never print a connection string.
- No shell heredocs. Comments and docstrings match the surrounding code. `AGENTS.md` mirrors `CLAUDE.md`: a change to one is made to both in the same commit.

## Review Focus

1. **A deploy that changes the height rules while a map has active heights.** Expected: no round of that map fails; a format change leaves the map flat until its rebuild lands; any rule change makes the rebuild due at once. Pinned in Task 2 (`test_a_row_from_another_format_is_not_active`, `test_the_height_rules_are_pinned_to_their_revision`) and Task 7 (`test_a_changed_rule_mask_or_check_set_makes_the_rebuild_due_at_once`).
2. **A call site that forgets to pass the active digests.** Expected: no call under `app/` or `scripts/` omits `heights`. Pinned in Task 2 by an AST test.
3. **A restart on either side in the middle of a rebuild, or a lost response.** Expected: the dispatcher adopts whatever state the worker's build is in; it never resends to a started build, never loops on a conflict, and a build the worker forgot is sent again from the first round. Pinned in Task 6 (the worker's states over real HTTP) and Task 7 (`test_a_restarted_dispatcher_adopts_...`, `test_a_lost_start_response_is_recovered_by_asking`, both against the real handler).
4. **A result that isn't what it says.** A missing check file, a report whose numbers don't add up, bytes that aren't the asset named, a result for other inputs. Expected: never active. Pinned in Tasks 2, 5 and 7.
5. **A map held forever.** A malformed result, an answer that isn't shaped as the protocol says, a refused request, a round too big to send, a collector nobody feeds, a build that fails every time. Expected: each spends the build's budget or ends it; when the budget is spent the map's rounds flow again; other maps and ordinary round dispatch are never stopped. Pinned in Task 7.
6. **The dispatcher's lock.** The cycle's session holds a transaction-scoped advisory lock; `activate`, `deactivate` and `store_build` all commit. Expected: the height step writes only through sessions of its own, so the cycle's transaction is never ended from inside it. Pinned in Task 7 (`test_the_height_step_never_commits_or_rolls_back_the_dispatchers_own_session`).

---

## File structure

| File | Responsibility | Task |
| --- | --- | --- |
| `webapp/scripts/measure_height_rebuild.py` (new) | a rebuild cycle's cost: what is sent, the build, the warm-up, the rounds | 1 |
| `webapp/app/replays/control_format.py`, `webapp/app/control/heights.py` | `HEIGHT_VERSION`, `HEIGHT_RULES_REVISION`, the bar's two numbers, where stdlib code can read them | 2 |
| `webapp/app/replays/height_inputs.py` (new), `webapp/app/static/data/control/must_block.json` (moved) | the input manifest and the check set, shared by both services | 2 |
| `webapp/alembic/versions/0018_control_heights.py` (new), `webapp/app/models/replay.py` | the table | 2 |
| `webapp/app/services/control_heights.py` (new), `webapp/app/control/height_verify.py` (new) | read, verify, gate, store, activate, under one lock per map | 2 |
| `webapp/app/services/replay_control.py` and its callers | the active digest in a round's inputs | 2 |
| `webapp/app/control/geometry.py`, `webapp/app/control/task.py`, `replay_worker/control_job.py`, `webapp/scripts/compute_control.py` | the engine gets an asset by digest | 3 |
| `replay_worker/server.py`, `webapp/app/services/replay_control_remote.py` | push an asset; a round names its digest; conflicts are per endpoint | 4 |
| `webapp/app/control/height_job.py` (new), `webapp/scripts/build_control_heights.py`, `replay_worker/height_job.py` (new) | the build as one function and one child, checks that fail closed | 5 |
| `replay_worker/server.py` | build jobs: states, immutable uploads, caps, expiry | 6 |
| `webapp/app/services/replay_heights_remote.py` (new), `webapp/app/services/replay_control_remote.py`, `webapp/app/config.py` | when a rebuild is due; hold, send, resume, verify, gate | 7 |
| `webapp/app/control/features.py`, `webapp/app/replays/map_feature_schema.py`, `webapp/scripts/control_tagger*.js`, the contract | a floor binding follows its floor; maps with a generation are held | 8 |
| `webapp/scripts/control_heights.py` (new), `webapp/scripts/height_viewer.py`, `webapp/scripts/control_tagger.py` | the operator's commands; the viewer's and the tagger's database source | 9 |
| `docs/map-control-worker-plan.md`, `webapp/RENDER_DEPLOY.md`, `replay_worker/Dockerfile`, `replay_worker/README.md`, `CLAUDE.md`, `AGENTS.md`, the spec | deploy notes, spec changes | 10 |

---

### Task 1: Measure a rebuild cycle

The spec's "Cost" section asks what a rebuild costs before the protocol is fixed: not only what is sent, but the
build, the visibility warm-up the first round under new heights pays, and recomputing every round of the map.

**Files:**
- Create: `webapp/scripts/measure_height_rebuild.py`
- Test: `webapp/tests/replays/test_control_heights_db.py` (new; later tasks add to it)
- Modify: `docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md` ("Cost, and what isn't known yet")

**Interfaces:**
- Consumes: the frozen rounds of the slopes plan (`<dir>/<match>/<n>.json.gz`); `build_control_heights.run_checks`; `height_build.build`; `geometry.load_geometry(name, heights=path)`, `geometry.visibility`; `task.compute_task`, `task.peak_memory`.
- Produces: `measure_height_rebuild.sizes(directory, map_name) -> dict` (`matches, rounds, bytes, largest`), `estimate(sizes, build_s, warm_s, round_s, workers=2) -> dict`, `main(argv=None) -> int`. Nothing later imports them.

- [ ] **Step 1: Record the baseline and the migration head**

Run: `PY -m pytest tests/replays -k "not pg" -q -p no:cacheprovider` and `PY -m alembic heads`
Write down every failing test with its message, and the head revision (this plan assumes `0017`).

- [ ] **Step 2: Write the failing test**

Create `webapp/tests/replays/test_control_heights_db.py`:

```python
"""Heights in the database (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md): what a rebuild
costs, the table's reads and writes, the active digest in a round's inputs, the gate, and the operator's
commands. SQLite, and no engine in this process."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WEBAPP / "scripts"))

from test_control_store import db, factory, linked, put_row  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed  # noqa: E402,F401  (fixture)


def test_sizes_and_the_cycle_estimate(tmp_path):
    import measure_height_rebuild as measure

    for match, n, size in (("m1", 1, 1000), ("m1", 2, 3000), ("m2", 1, 2000)):
        (tmp_path / match).mkdir(exist_ok=True)
        (tmp_path / match / f"{n}.json.gz").write_bytes(b"x" * size)
    got = measure.sizes(tmp_path)
    assert got == {"matches": 2, "rounds": 3, "bytes": 6000, "largest": 3000}
    est = measure.estimate(got, build_s=30.0, warm_s=120.0, round_s=40.0, workers=2)
    assert est == {"sent_mb": 0.008, "batches": 1, "build_min": 0.5, "warm_min": 2.0, "rounds_min": 1.0,
                   "cycle_min": 3.5}


def test_cost_probe_uses_only_the_requested_maps_rounds_and_can_read_them_twice(tmp_path):
    import measure_height_rebuild as measure
    from app.replays import format as fmt

    for match, map_name in (("a-bind", "Bind"), ("z-ascent", "Ascent")):
        (tmp_path / match).mkdir()
        (tmp_path / match / "1.json.gz").write_bytes(fmt.encode_blob({"v": 1, "map": map_name}))
    rounds = measure.FrozenRounds(tmp_path, "Ascent")
    assert [p.parent.name for p in rounds.paths[:3]] == ["z-ascent"]  # the timing loop's exact selection
    for _ in range(2):
        assert [(m, n, b["map"]) for m, n, b in rounds] == [("z-ascent", 1, "Ascent")]
    assert measure.sizes(tmp_path, "Ascent")["rounds"] == 1

```

- [ ] **Step 3: Run it to see it fail**

Run: `PY -m pytest tests/replays/test_control_heights_db.py -q -p no:cacheprovider`
Expected: FAIL, `ModuleNotFoundError: No module named 'measure_height_rebuild'`.

- [ ] **Step 4: Write the script**

Create `webapp/scripts/measure_height_rebuild.py`:

```python
"""What one height rebuild cycle costs for a map, measured on this machine from frozen rounds
(docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, "Cost, and what isn't known yet").

    .\\.venv\\Scripts\\python.exe scripts\\measure_height_rebuild.py --map Sunset --blobs-dir %TEMP%\\valo-replay\\heights-frozen

It reads only that folder (`<match>/<n>.json.gz`, as `freeze_height_rounds.py` writes it) and writes only under
a temp folder. It prints the four parts of a cycle:

- **sent**: the rounds' size as stored and as the base64 a request carries, and the batches of BATCH_BYTES;
- **build**: seconds and peak memory of the build with both checks;
- **warm-up**: seconds to build the map's visibility under the new heights (the first round after a rebuild
  pays this);
- **rounds**: seconds per round of control under those heights, over `--rounds` of them (default 3), and so
  the minutes to recompute every round of the map on `--workers` workers (default 2, the replay worker's).

The worker is slower than a desk: these are a floor, and the first live cycle's own numbers replace them.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

BATCH_BYTES = 2_000_000       # app/services/replay_heights_remote.py sends at most this much base64 a request


class FrozenRounds:
    """Re-iterable rounds for this measurement; keeps paths, never a list of decoded blobs.

    Task 5 replaces this measurement-only adapter with the worker's BlobDir and repeats the memory gate.
    """
    def __init__(self, directory: Path, map_name: str):
        from app.replays import format as fmt

        self.paths = [p for p in sorted(Path(directory).glob("*/*.json.gz"),
                                       key=lambda p: (p.parent.name, int(p.name.split(".")[0])))
                      if fmt.decode_blob(p.read_bytes()).get("map") == map_name]

    def __iter__(self):
        from app.replays import format as fmt

        for path in self.paths:
            yield path.parent.name, int(path.name.split(".")[0]), fmt.decode_blob(path.read_bytes())


def sizes(directory: Path, map_name: str | None = None) -> dict:
    """{"matches", "rounds", "bytes", "largest"} over `<directory>/<match>/<n>.json.gz`. With `map_name`, only
    that map's rounds (the blobs are decoded to tell)."""
    paths = sorted(Path(directory).glob("*/*.json.gz"))
    if map_name:
        from app.replays import format as fmt

        paths = [p for p in paths if fmt.decode_blob(p.read_bytes()).get("map") == map_name]
    lengths = [p.stat().st_size for p in paths]
    return {"matches": len({p.parent.name for p in paths}), "rounds": len(paths), "bytes": sum(lengths),
            "largest": max(lengths, default=0)}


def estimate(found: dict, build_s: float, warm_s: float, round_s: float, workers: int = 2) -> dict:
    b64 = found["bytes"] * 4 // 3
    rounds_min = found["rounds"] * round_s / workers / 60
    return {"sent_mb": round(b64 / 1e6, 3), "batches": max(1, -(-b64 // BATCH_BYTES)),
            "build_min": round(build_s / 60, 2), "warm_min": round(warm_s / 60, 2), "rounds_min": round(rounds_min, 2),
            "cycle_min": round(build_s / 60 + warm_s / 60 + rounds_min, 2)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", required=True)
    parser.add_argument("--blobs-dir", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3, help="rounds of control to time (default 3)")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)

    import build_control_heights as command
    from app.control import geometry as cg
    from app.control import height_build as hb
    from app.control import heights as hc
    from app.control.task import compute_task, peak_memory

    found = sizes(args.blobs_dir, args.map)
    if not found["rounds"]:
        print(f"no rounds of {args.map} in {args.blobs_dir}", flush=True)
        return 2
    with tempfile.TemporaryDirectory(prefix="valo-rebuild-cost-") as folder:
        os.environ["CONTROL_CACHE_DIR"] = folder          # a cold visibility cache, and none left behind
        geo = cg.load_geometry(args.map)
        started = time.perf_counter()
        rounds = FrozenRounds(args.blobs_dir, args.map)
        build = hb.build(rounds, geo)
        command.run_checks(args.map, geo, build, rounds)
        build_s, peak = time.perf_counter() - started, peak_memory()
        asset = Path(folder) / f"{args.map}.height.npz"
        hc.save_asset(asset, build.asset)
        started = time.perf_counter()
        cg.visibility(cg.load_geometry(args.map, heights=asset))
        warm_s = time.perf_counter() - started
        took = []
        for path in rounds.paths[: max(1, args.rounds)]:
            result = compute_task({"key": path.name, "map": args.map, "blob": path.read_bytes(), "heights": str(asset),
                                   "link": {"sides": {}, "db_deaths": []}})
            if result["status"] == "ok":
                took.append(result["seconds"])
        if not took:
            print("no round computed: nothing to time", flush=True)
            return 2
        round_s = sum(took) / len(took)
    est = estimate(found, build_s, warm_s, round_s, args.workers)
    print(f"{args.map}: {found['matches']} matches, {found['rounds']} rounds", flush=True)
    print(f"  sent: {found['bytes'] / 1e6:.1f} MB stored, {est['sent_mb']:.1f} MB as base64, {est['batches']} batches of "
          f"{BATCH_BYTES / 1e6:g} MB; largest round {found['largest'] / 1e3:.0f} KB", flush=True)
    print(f"  build with both checks: {build_s:.0f} s, peak memory {(peak or 0) / 1e9:.2f} GB", flush=True)
    print(f"  visibility warm-up under the new heights: {warm_s:.0f} s", flush=True)
    print(f"  control: {round_s:.0f} s a round over {len(took)} rounds; all {found['rounds']} on {args.workers} workers: "
          f"{est['rounds_min']:.0f} min", flush=True)
    print(f"  one cycle here: {est['cycle_min']:.0f} min (the worker is slower)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the test**

Run: `PY -m pytest tests/replays/test_control_heights_db.py -q -p no:cacheprovider`
Expected: 2 passed.

- [ ] **Step 6: Measure Sunset and the map with the most matches, and stop if a number is out of range**

```
PY scripts\measure_height_rebuild.py --map Sunset --blobs-dir %TEMP%\valo-replay\heights-frozen
```

Run it in a normal terminal (the warm-up takes minutes). No database is read. Carry on when the largest round
is under 1.5 MB (it fits a 2 MB batch as base64), the base64 total is under 200 MB (the worker's spool cap in
Task 6 is 512 MB for all builds together), and the build's peak memory is under 1.5 GB (the worker's child cap is
2 GB). This measures a re-iterable, one-round-at-a-time input, not `blob_rounds`' decoded list. Otherwise
stop and report the lines: the batch size, the spool cap or the streaming build has to change first.
Task 5 repeats the memory gate with the worker's actual `BlobDir`; Task 1's number is provisional until then.

- [ ] **Step 7: Record it**

In the spec's "Cost, and what isn't known yet" section, replace the first three bullets with the printed lines
for each map measured, labelled as measured on the development machine from the frozen rounds (name the dataset
by its `rounds` digest), with the batch size chosen (2 MB of base64 a request, against the worker's 4 MB request
cap), and add: "The worker's own figures (a build's `seconds` and `peak`, and the time from a build going active
to its map's last round being recomputed) are recorded here from the first live cycle; see the plan's Task 10."

- [ ] **Step 8: Commit**

```bash
git add webapp/scripts/measure_height_rebuild.py webapp/tests/replays/test_control_heights_db.py docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md
git commit -F <message file>
```

Message: `Heights: measure what a rebuild cycle costs`. (Write every commit message to a scratch file with the Write tool and pass it with `-F`; end it with the attribution line the session gives.)

---

### Task 2: Heights live in the database, behind one lock, a manifest and a gate

**Files:**
- Modify: `webapp/app/replays/control_format.py`, `webapp/app/control/heights.py` (the shared constants)
- Move: `webapp/tests/replays/control_must_block.json` -> `webapp/app/static/data/control/must_block.json` (`git mv`)
- Create: `webapp/app/replays/height_inputs.py`
- Create: `webapp/alembic/versions/0018_control_heights.py` (see the Global Constraints for its number)
- Modify: `webapp/app/models/replay.py`, `webapp/app/models/__init__.py`
- Create: `webapp/app/control/height_verify.py`, `webapp/app/services/control_heights.py`
- Modify: `webapp/app/services/replay_control.py`, `replay_control_store.py`, `replay_control_views.py`, `replay_gaps.py`, `gap_patterns.py`, `replay_control_remote.py`; `webapp/scripts/compute_control.py`, `webapp/scripts/build_control_heights.py` (the must-block path)
- Modify: `webapp/tests/replays/test_replay_store.py` (`TABLES`), `test_control_isolation.py`, `test_control_format.py`, `test_control_heights.py` and `test_compare_height_builds.py` (the must-block path)
- Test: `webapp/tests/replays/test_control_heights_db.py`, `webapp/tests/replays/test_height_inputs.py` (new)

**Interfaces:**
- Produces:
  - `control_format.HEIGHT_VERSION` (2), `HEIGHT_RULES_REVISION` (1), `HEIGHT_SUPPORTED_MIN` (0.60), `KILL_LINE_BAR` (0.02). `heights.py` re-exports the first, third and fourth (same values, same names).
  - `height_inputs.MUST_BLOCK: Path`, `MIN_CONDENSE_REVISION = 11`, `CheckSetError`.
  - `height_inputs.must_block_set(path=None) -> tuple[list, str]` (the lines and the file's 12-hex hash; raises `CheckSetError` when the file is missing, not JSON, or has no `lines` list). `must_block_sha(path=None) -> str | None` (None instead of raising).
  - `height_inputs.geometry_identity(map_name, control_dir=None, maps_json=None) -> dict | None` (`{"sight", "walk", "scale"}` from index.json and maps.json; None for a map without geometry).
  - `height_inputs.manifest(map_name, replays, geometry, must_block) -> dict` where `replays` is `[[match uuid, recipe, source sha256, round count], ...]`; `digest(manifest) -> str` (16 hex); `key(manifest) -> str` (`"heights:<map>:<digest>"`); `rounds_expected(manifest) -> int`; `matches(manifest) -> list[str]`; `changes(old, new) -> {"added": [...], "gone": [...], "other": bool}`.
  - `ControlHeight` (table `control_heights`): `id, map_name, digest, asset, report, match_uuids, rules, inputs, inputs_sha, status, built_at`.
  - `height_verify.describe(data, map_name, *, asset_dir=None, must_block=None) -> dict` and `python -m app.control.height_verify --map X --asset-dir D --must-block F` (asset bytes on stdin; validated asset identity, actual map counts/readiness and rerun must-block outcome, or `{"error"}`, on stdout).
  - `control_heights.verify_asset(data, map_name, timeout_s=120, *, asset_dir=None, must_block=None) -> dict` (runs that child with this deploy's paths, or explicit test paths).
  - `control_heights.active_digests(db) -> dict[str, str]`; `rows(db, map_name=None)`; `last_builds(db) -> dict[str, ControlHeight]`; `active_rows(db) -> dict[str, ControlHeight]`; `asset_bytes(db, map_name, digest) -> bytes | None`.
  - `control_heights.integrity(result_digest, report, verified, must_block) -> list[str]` (why a result can't be trusted; empty when it can) and `control_heights.gate(report) -> list[str]` (why a trusted build may not go live).
  - `control_heights.store_build(db, *, map_name, digest, asset, report, inputs, rules) -> tuple[str, list[str]]` (`("active" | "rejected", reasons)`; raises `Busy` when another change to the map won; commits).
  - `control_heights.activate(db, map_name, digest, generation=None) -> str | None`; `deactivate(db, map_name, generation=None) -> bool`; both take the map's lock and recheck the current generation; only an allowed change commits. `store_build` raises `HasGeneration` without storing when a generation is present.
  - `replay_control.geometry_inputs(map_name, heights=None)`, `round_fingerprint(replay, groups, n, heights=None)`.

- [ ] **Step 1: Move the must-block list, and put the shared numbers where stdlib code can read them**

```bash
git mv webapp/tests/replays/control_must_block.json webapp/app/static/data/control/must_block.json
```

In `webapp/app/replays/control_format.py`, after `SUMMARY_VERSION = 1`:

```python
# Map heights (app/control/heights.py re-exports the first, third and fourth). Here, in a stdlib-only module,
# so the web app can judge a stored asset and a build's report without importing numpy.
HEIGHT_VERSION = 2            # the asset's format: an asset of another version can't be loaded
# The rules a build follows. Bump it for ANY change to how rounds become heights (a constant in
# app/control/heights.py, the code of height_build.py or height_motion.py): every map's rebuild is then due.
# tests/replays/test_height_inputs.py pins it to the constants' hash, as CONTROL_REVISION is pinned.
HEIGHT_RULES_REVISION = 1
HEIGHT_SUPPORTED_MIN = 0.60   # the bar: share of walkable cells with a supported floor
KILL_LINE_BAR = 0.02          # the kill-line check: at most this share of real kill lines blocked
```

In `webapp/app/control/heights.py`, delete the three assignments (`HEIGHT_VERSION = 2`, `HEIGHT_SUPPORTED_MIN = 0.60`, `KILL_LINE_BAR = 0.02`) and import them with the other imports:

```python
from app.replays.control_format import HEIGHT_SUPPORTED_MIN, HEIGHT_VERSION, KILL_LINE_BAR  # noqa: F401
```

The module docstring's last sentence becomes: "This module imports nothing from app.control (only the stdlib-only app.replays.control_format, which holds the numbers the web app reads too), so geometry.py can read it."

Run: `PY -m pytest tests/replays/test_control_format.py tests/replays/test_control_heights.py -q -p no:cacheprovider`
Expected: `test_control_format.py` passes (the names and values in `heights.py` are the same, so `CONTROL_REVISION`'s digest is too). `test_control_heights.py` fails only where it reads the old must-block path; Step 7 fixes those.

- [ ] **Step 2: Write the failing tests**

Create `webapp/tests/replays/test_height_inputs.py`:

```python
"""What a height build is made from (app/replays/height_inputs.py;
docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md): the input manifest both services compute,
and the check set, which is never "nothing to check" because its file is missing."""

import hashlib
import json

import pytest

from app.control import heights as hc
from app.replays import control_format as cf
from app.replays import height_inputs as hi

GEOMETRY = {"sight": "s" * 12, "walk": "w" * 12, "scale": 7e-5}
REPLAYS = [["m2", "p.c11.f1.a1", "b" * 64, 20], ["m1", "p.c11.f1.a1", "a" * 64, 24]]

# HEIGHT_RULES_REVISION -> the hash of app/control/heights.py's constants it was released with. Changed a
# constant, or the build's code? Bump HEIGHT_RULES_REVISION in app/replays/control_format.py and pin it here.
PINNED_RULES = {1: "<12 hex: run the test once and copy the digest it prints>"}


def test_the_height_rules_are_pinned_to_their_revision():
    digest = hc.rules()["constants"]
    assert PINNED_RULES.get(cf.HEIGHT_RULES_REVISION) == digest, (
        f"the height constants changed (hash {digest}): bump HEIGHT_RULES_REVISION in "
        f"app/replays/control_format.py and pin {{{cf.HEIGHT_RULES_REVISION + 1}: '{digest}'}} here")
    assert hc.rules() == {"version": cf.HEIGHT_VERSION, "revision": cf.HEIGHT_RULES_REVISION, "constants": digest}


def test_the_manifest_is_the_same_whatever_order_the_replays_come_in():
    a = hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)
    b = hi.manifest("Sunset", REPLAYS[::-1], dict(GEOMETRY), "k" * 12)
    assert hi.digest(a) == hi.digest(b) and len(hi.digest(a)) == 16
    assert hi.key(a) == f"heights:Sunset:{hi.digest(a)}"
    assert hi.rounds_expected(a) == 44 and hi.matches(a) == ["m1", "m2"]
    assert a["height_version"] == cf.HEIGHT_VERSION and a["height_rules"] == cf.HEIGHT_RULES_REVISION
    json.dumps(a)


@pytest.mark.parametrize("name, change", [
    ("a re-condensed blob", lambda r, g, k: ([[r[0][0], "p.c12.f1.a1", r[0][2], r[0][3]], r[1]], g, k)),
    ("a replaced source file", lambda r, g, k: ([[r[0][0], r[0][1], "c" * 64, r[0][3]], r[1]], g, k)),
    ("a round fewer", lambda r, g, k: ([[r[0][0], r[0][1], r[0][2], 19], r[1]], g, k)),
    ("a new match", lambda r, g, k: ([*r, ["m3", "p.c11.f1.a1", "d" * 64, 22]], g, k)),
    ("a deleted match", lambda r, g, k: (r[:1], g, k)),
    ("a re-drawn walk mask", lambda r, g, k: (r, {**g, "walk": "x" * 12}, k)),
    ("a re-drawn sight mask", lambda r, g, k: (r, {**g, "sight": "x" * 12}, k)),
    ("another check set", lambda r, g, k: (r, g, "z" * 12)),
    ("no check set", lambda r, g, k: (r, g, None)),
])
def test_every_input_moves_the_digest(name, change):
    base = hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)
    assert hi.digest(hi.manifest("Sunset", *change(REPLAYS, GEOMETRY, "k" * 12))) != hi.digest(base), name


def test_the_rule_revisions_are_inputs_too(monkeypatch):
    base = hi.digest(hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12))
    monkeypatch.setattr(cf, "HEIGHT_RULES_REVISION", cf.HEIGHT_RULES_REVISION + 1)
    assert hi.digest(hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)) != base
    monkeypatch.undo()
    monkeypatch.setattr(cf, "HEIGHT_VERSION", cf.HEIGHT_VERSION + 1)
    assert hi.digest(hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)) != base


def test_changes_tell_added_matches_from_evidence_that_is_gone():
    old = hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)
    more = hi.manifest("Sunset", [*REPLAYS, ["m3", "p", "d" * 64, 22]], GEOMETRY, "k" * 12)
    assert hi.changes(old, more) == {"added": ["m3"], "gone": [], "other": False}
    fewer = hi.manifest("Sunset", REPLAYS[:1], GEOMETRY, "k" * 12)
    assert hi.changes(old, fewer) == {"added": [], "gone": ["m1"], "other": False}
    recondensed = hi.manifest("Sunset", [["m1", "p.c12", "a" * 64, 24], REPLAYS[0]], GEOMETRY, "k" * 12)
    assert hi.changes(old, recondensed) == {"added": [], "gone": ["m1"], "other": False}, "replaced counts as gone"
    remasked = hi.manifest("Sunset", REPLAYS, {**GEOMETRY, "walk": "x" * 12}, "k" * 12)
    assert hi.changes(old, remasked) == {"added": [], "gone": [], "other": True}
    assert hi.changes(None, old) == {"added": ["m1", "m2"], "gone": [], "other": True}, "a row from before manifests"


def test_a_missing_or_broken_check_set_is_an_error_and_an_empty_one_is_not(tmp_path):
    with pytest.raises(hi.CheckSetError, match="missing"):
        hi.must_block_set(tmp_path / "none.json")
    assert hi.must_block_sha(tmp_path / "none.json") is None
    for text in ("not json", "[]", '{"lines": 3}', '{"other": []}'):
        (tmp_path / "bad.json").write_text(text, encoding="utf-8")
        with pytest.raises(hi.CheckSetError):
            hi.must_block_set(tmp_path / "bad.json")
    (tmp_path / "empty.json").write_text('{"lines": []}', encoding="utf-8")
    lines, sha = hi.must_block_set(tmp_path / "empty.json")
    assert lines == [] and sha == hashlib.sha256(b'{"lines": []}').hexdigest()[:12]
    assert hi.must_block_sha(tmp_path / "empty.json") == sha


def test_the_committed_check_set_is_readable_and_the_geometry_identity_matches_the_index():
    lines, sha = hi.must_block_set()
    assert isinstance(lines, list) and len(sha) == 12
    index = json.loads((hi.CONTROL_DIR / "index.json").read_text(encoding="utf-8"))["maps"]
    name = sorted(index)[0]
    got = hi.geometry_identity(name)
    assert got["sight"] == index[name]["sight_sha"] and got["walk"] == index[name]["walk_sha"] and got["scale"]
    assert hi.geometry_identity("NoSuchMap") is None
```

Append to `webapp/tests/replays/test_control_heights_db.py`:

```python
import ast  # noqa: E402

from app.models.replay import ControlHeight  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from app.replays import height_inputs as hi  # noqa: E402
from app.services import control_heights as ch  # noqa: E402
from app.services import replay_control as rc  # noqa: E402

RULES = {"version": cf.HEIGHT_VERSION, "revision": cf.HEIGHT_RULES_REVISION, "constants": "c0ffee"}
CHECKS = "k" * 12


def report(**changes) -> dict:
    """A build's report as the worker returns it, whole and consistent: 6,000 walkable cells, 4,200 supported."""
    out = {"walkable_cells": 6000, "supported_cells": 4200, "supported": 0.7, "ready": True, "not_ready": [],
           "kill_lines": {"qualifying": 400, "blocked": 2, "share": 0.005, "passes": True},
           "must_block": {"set": CHECKS, "lines": 3, "checked": 3, "unchecked": 0, "blocked": 3, "passes": True}}
    for key, value in changes.items():
        out[key] = {**out[key], **value} if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def inputs(*uuids) -> dict:
    return hi.manifest("Toy", [[u, "p.c11.f1.a1", u * 8, 20] for u in uuids or ("m1", "m2")],
                       {"sight": "s", "walk": "w", "scale": 7e-5}, CHECKS)


def build(db, name, digest, rep=None, uuids=("m1", "m2"), rules=RULES):
    return ch.store_build(db, map_name=name, digest=digest, asset=b"npz-" + digest.encode(),
                          report=report() if rep is None else rep, inputs=inputs(*uuids), rules=dict(rules))


def verified(digest, **changes) -> dict:
    return {"digest": digest, "version": cf.HEIGHT_VERSION, "walkable_cells": 6000,
            "supported_cells": 4200, "floor_cells": 5000, "ready": True, "not_ready": [],
            "must_block": report()["must_block"], **changes}


def test_a_build_at_the_bar_goes_live_and_the_one_before_it_is_superseded(db, linked):
    name = linked.map_name
    assert ch.active_digests(db) == {} and "height" not in rc.geometry_inputs(name, ch.active_digests(db))
    assert build(db, name, "aaaaaaaaaaaa") == ("active", [])
    assert ch.active_digests(db) == {name: "aaaaaaaaaaaa"}
    assert rc.geometry_inputs(name, ch.active_digests(db))["height"] == "aaaaaaaaaaaa"
    assert build(db, name, "bbbbbbbbbbbb", uuids=("m1", "m2", "m3")) == ("active", [])
    assert ch.active_digests(db) == {name: "bbbbbbbbbbbb"}
    assert [(r.digest, r.status) for r in ch.rows(db, name)] == [("bbbbbbbbbbbb", "active"), ("aaaaaaaaaaaa", "superseded")]
    assert ch.asset_bytes(db, name, "aaaaaaaaaaaa") == b"npz-aaaaaaaaaaaa" and ch.asset_bytes(db, name, "nope") is None
    last = ch.last_builds(db)[name]
    assert last.match_uuids == ["m1", "m2", "m3"] and last.inputs_sha == hi.digest(inputs("m1", "m2", "m3"))
    assert ch.active_rows(db)[name].digest == "bbbbbbbbbbbb"


@pytest.mark.parametrize("rep, why", [
    (report(ready=False, not_ready=["supported 41.0% is under 60%"], supported_cells=2460, supported=0.41), "under 60%"),
    (report(ready=False, not_ready=["1 unresolved area(s) larger than 12 cells touch a cell with two floors"]), "unresolved"),
    (report(kill_lines={"blocked": 12, "share": 0.03, "passes": False}), "kill lines"),
    (report(must_block={"blocked": 2, "passes": False}), "must-block"),
    (report(must_block={"checked": 2, "unchecked": 1, "blocked": 2, "passes": False}), "must-block"),
])
def test_a_build_below_the_bar_or_failing_a_check_is_rejected_and_the_old_heights_stay(db, linked, rep, why):
    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa")
    assert ch.integrity("cccccccccccc", rep, verified("cccccccccccc", supported_cells=rep["supported_cells"], ready=rep["ready"],
                                 not_ready=rep["not_ready"], must_block=rep["must_block"]), CHECKS) == [], "an honest failing build is still a whole result"
    status, reasons = build(db, name, "cccccccccccc", rep=rep)
    assert status == "rejected" and any(why in r for r in reasons)
    assert ch.active_digests(db) == {name: "aaaaaaaaaaaa"}
    assert ch.last_builds(db)[name].status == "rejected", "the last build is the rejected one: it sets the next due"


@pytest.mark.parametrize("name, rep, seen, why", [
    ("the asset is other bytes", report(), verified("dddddddddddd"), "is not the asset"),
    ("the asset can't be opened", report(), {"error": "BadZipFile"}, "could not be opened"),
    ("another format", report(), verified("cccccccccccc", version=cf.HEIGHT_VERSION + 1), "height version"),
    ("its count isn't the asset's", report(), verified("cccccccccccc", supported_cells=4100), "supported cells"),
    ("the share isn't the counts'", report(supported=0.9), verified("cccccccccccc"), "share"),
    ("invented map size", report(walkable_cells=6000), verified("cccccccccccc", walkable_cells=8000), "walkable"),
    ("hidden unresolved area", report(), verified("cccccccccccc", ready=False, not_ready=["unresolved"]), "readiness"),
    ("empty evidence for a nonempty set", report(must_block={"lines": 0, "checked": 0, "blocked": 0}),
     verified("cccccccccccc"), "must-block"),
    ("ready, with reasons", report(not_ready=["thin"]), verified("cccccccccccc"), "ready"),
    ("more blocked than qualifying", report(kill_lines={"blocked": 500}), verified("cccccccccccc"), "kill lines"),
    ("no kill qualified", report(kill_lines={"qualifying": 0, "blocked": 0, "share": 0.0}), verified("cccccccccccc"), "kill lines"),
    ("passes, over the bar", report(kill_lines={"blocked": 40, "share": 0.1}), verified("cccccccccccc"), "kill lines"),
    ("a share that isn't a number", report(kill_lines={"share": float("nan")}), verified("cccccccccccc"), "kill lines"),
    ("another check set", report(must_block={"set": "o" * 12}), verified("cccccccccccc"), "check set"),
    ("no check set", report(must_block={"set": None}), verified("cccccccccccc"), "check set"),
    ("passes, with a line unchecked", report(must_block={"checked": 2, "unchecked": 1}), verified("cccccccccccc"), "must-block"),
    ("no checks at all", {"walkable_cells": 6000, "supported_cells": 4200, "supported": 0.7, "ready": True,
                          "not_ready": []}, verified("cccccccccccc"), "kill lines"),
    ("not a report", ["ready"], verified("cccccccccccc"), "not a report"),
])
def test_a_result_that_is_not_what_it_says_is_never_trusted(name, rep, seen, why):
    reasons = ch.integrity("cccccccccccc", rep, seen, CHECKS)
    assert reasons and any(why in r for r in reasons), (name, reasons)


def test_a_missing_check_set_on_this_side_trusts_nothing():
    assert any("check set" in r for r in ch.integrity("cccccccccccc", report(), verified("cccccccccccc"), None))


def test_store_build_rechecks_a_generation_without_relying_on_the_planners_lookup(db, linked, monkeypatch):
    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa")
    before = ch.active_digests(db)
    real = rc.geometry_inputs
    monkeypatch.setattr(rc, "geometry_inputs", lambda m, heights=None: {**(real(m, heights) or {}), "features": "new-generation"})
    with pytest.raises(ch.HasGeneration):
        build(db, name, "bbbbbbbbbbbb")
    assert ch.active_digests(db) == before and len(ch.rows(db, name)) == 1
    assert "generation" in ch.activate(db, name, "aaaaaaaaaaaa")
    with pytest.raises(ch.HasGeneration):
        ch.deactivate(db, name)
    assert ch.active_digests(db) == before


def test_a_row_from_another_format_is_not_active(db, linked):
    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa", rules={**RULES, "version": cf.HEIGHT_VERSION + 1})
    assert db.query(ControlHeight).one().status == "active"
    assert ch.active_digests(db) == {}, "an asset this deploy can't load is never a round's input"


def test_new_heights_make_a_maps_rounds_stale_and_they_are_still_served(db, linked):
    put_row(db, linked, 1)
    assert (rc.round_control(db, linked, 1).status, rc.round_control(db, linked, 1).stale) == ("ok", False)
    build(db, linked.map_name, "aaaaaaaaaaaa")
    answer = rc.round_control(db, linked, 1)
    assert (answer.status, answer.stale) == ("ok", True), "the page keeps the old round until its recompute lands"
    assert [p.reason for p in rc.plan(db, rounds={1})] == ["stale"]
    ch.deactivate(db, linked.map_name)
    assert rc.round_control(db, linked, 1).stale is False, "and going back makes it current again"


def test_activate_puts_an_earlier_asset_back_and_off_leaves_none(db, linked):
    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa")
    build(db, name, "bbbbbbbbbbbb")
    assert ch.activate(db, name, "aaaaaaaaaaaa") is None and ch.active_digests(db) == {name: "aaaaaaaaaaaa"}
    assert "no build" in ch.activate(db, name, "zzzzzzzzzzzz")
    build(db, name, "dddddddddddd", rules={**RULES, "version": cf.HEIGHT_VERSION + 1})
    assert "can't load it" in ch.activate(db, name, "dddddddddddd")
    assert ch.deactivate(db, name) is True and ch.active_digests(db) == {} and ch.deactivate(db, name) is False
    assert {r.status for r in ch.rows(db, name)} == {"superseded"}


def test_activating_the_digest_that_is_already_active_leaves_it_active(db, linked, factory):
    # The row is loaded as active; retiring "the active row" in SQL and then setting the loaded one active
    # again changes nothing the ORM can see, and the map would be left with no heights.
    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa")
    for _ in range(2):
        assert ch.activate(db, name, "aaaaaaaaaaaa") is None
        other = factory()                                   # read with a session that loaded nothing before
        assert ch.active_digests(other) == {name: "aaaaaaaaaaaa"}
        assert [r.status for r in ch.rows(other, name)] == ["active"]
        other.close()
    stale = factory()
    row = stale.query(ControlHeight).one()                  # a session holding the row from before a change
    ch.deactivate(db, name)
    assert ch.activate(stale, name, "aaaaaaaaaaaa") is None and row.status == "active"
    assert ch.active_digests(factory()) == {name: "aaaaaaaaaaaa"}
    stale.close()


def test_pg_two_changes_to_one_map_wait_for_each_other(pg):
    # The one-active-row index refuses a second active row but doesn't order two writers. The map's advisory
    # lock does: the second waits, then acts on what the first left.
    import threading

    from sqlalchemy import text

    from app.replays import db as replay_db

    first, second = pg(), pg()
    first.execute(text("DELETE FROM control_heights"))
    first.commit()
    try:
        build(first, "Toy", "aaaaaaaaaaaa")
        build(first, "Toy", "bbbbbbbbbbbb")
        replay_db.advisory_lock(first, ch.lock_name("Toy"))          # hold the map's lock in an open transaction
        done = {}
        worker = threading.Thread(target=lambda: done.update(result=ch.activate(second, "Toy", "aaaaaaaaaaaa")))
        worker.start()
        worker.join(timeout=1.0)
        assert worker.is_alive(), "the second change waits for the map's lock"
        first.rollback()
        worker.join(timeout=30)
        assert done == {"result": None}
        assert ch.active_digests(first) == {"Toy": "aaaaaaaaaaaa"}
        assert [r.status for r in ch.rows(first, "Toy")].count("active") == 1
        racing = [threading.Thread(target=lambda s=s, d=d: ch.activate(s, "Toy", d))
                  for s, d in ((first, "bbbbbbbbbbbb"), (second, "aaaaaaaaaaaa"))]
        for t in racing:
            t.start()
        for t in racing:
            t.join(timeout=30)
        first.expire_all()
        assert [r.status for r in ch.rows(first, "Toy")].count("active") == 1, "whichever won, exactly one is active"
    finally:
        first.rollback()
        first.execute(text("DELETE FROM control_heights"))
        first.commit()
        first.close()
        second.close()


def _calls_without_heights(path: Path) -> list[int]:
    """Line numbers of geometry_inputs(...) / round_fingerprint(...) calls that don't pass `heights`."""
    need = {"geometry_inputs": 2, "round_fingerprint": 4}        # how many positional arguments reach `heights`
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name in need and len(node.args) < need[name] and not any(k.arg == "heights" for k in node.keywords):
                out.append(node.lineno)
    return out


def test_every_caller_passes_the_active_heights():
    # A call that leaves `heights` out falls back to the committed files: its fingerprint would never match a
    # row computed with the database's heights, and the round would read as stale forever.
    files = [*(WEBAPP / "app").rglob("*.py"), *(WEBAPP / "scripts").glob("*.py")]
    missing = {str(p.relative_to(WEBAPP)): lines for p in files if (lines := _calls_without_heights(p))}
    assert missing == {}, missing
```

`pg` comes from `test_replay_store` (add it to that import line): it needs `VALO_TEST_DATABASE_URL` pointing at a
`*_test` database at the migration head and is skipped without one.

- [ ] **Step 3: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_height_inputs.py tests/replays/test_control_heights_db.py -q -p no:cacheprovider`
Expected: errors at import: `cannot import name 'height_inputs' from 'app.replays'`.

- [ ] **Step 4: The input manifest**

Create `webapp/app/replays/height_inputs.py`:

```python
"""What a map's height build is made from, as both services say it
(docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, sections 2 to 4).

The **input manifest** names everything a build reads: each replay's stored blobs (its match, the recipe they
were condensed under, the source file's hash and how many rounds), the map's sight and walk masks and its
scale, the asset format and the build rules' revision, and the must-block check set. Its digest:

- names the build on the replay worker, so a result is never reused for other inputs;
- decides when a rebuild is due (app/services/replay_heights_remote.py), by comparing it with the last
  build's;
- is what the worker's child recomputes from its own files and echoes, and what the web app compares again
  just before it activates a result.

The **check set** is the hand-kept list of sightlines that are impossible in game (`must_block.json`). A file
that is missing or unreadable is an error here, never an empty list: an unattended gate must not pass because
it had nothing to check. A readable file with no line for a map is a real answer.

Standard library only: the web app, the worker's server and its children all import this module.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.replays import control_format as cf
from app.replays import format as fmt

CONTROL_DIR = fmt.STATIC_DIR / "data" / "control"
MAPS_JSON = fmt.STATIC_DIR / "data" / "maps.json"
MUST_BLOCK = CONTROL_DIR / "must_block.json"
MIN_CONDENSE_REVISION = 11       # blobs from before it carry no heights
MANIFEST_VERSION = 1


class CheckSetError(ValueError):
    """The must-block check set can't be read: nothing may pass the check."""


def must_block_set(path: Path | None = None) -> tuple[list, str]:
    """(the lines, 12 hex of the file's bytes). Raises CheckSetError for a missing or malformed file."""
    path = MUST_BLOCK if path is None else Path(path)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CheckSetError(f"{path.name} is missing or unreadable: {error}") from error
    try:
        lines = json.loads(raw.decode("utf-8"))["lines"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as error:
        raise CheckSetError(f"{path.name} is not a check set: {type(error).__name__}") from error
    if not isinstance(lines, list):
        raise CheckSetError(f"{path.name} is not a check set: `lines` is not a list")
    return lines, hashlib.sha256(raw).hexdigest()[:12]


def must_block_sha(path: Path | None = None) -> str | None:
    try:
        return must_block_set(path)[1]
    except CheckSetError:
        return None


def geometry_identity(map_name: str, control_dir: Path | None = None, maps_json: Path | None = None) -> dict | None:
    """The masks a build reads, by the hashes index.json records, and the map's scale. None without geometry."""
    try:
        entry = json.loads(((control_dir or CONTROL_DIR) / "index.json").read_text(encoding="utf-8")) \
            .get("maps", {}).get(map_name)
        scale = (json.loads((maps_json or MAPS_JSON).read_text(encoding="utf-8")).get(map_name) or {}) \
            .get("xMultiplier")
    except (OSError, ValueError):
        return None
    if not isinstance(entry, dict) or not entry.get("sight_sha") or not entry.get("walk_sha"):
        return None
    return {"sight": entry["sight_sha"], "walk": entry["walk_sha"], "scale": scale}


def manifest(map_name: str, replays: list, geometry: dict, must_block: str | None) -> dict:
    """`replays` = [[match uuid, recipe, source sha256, round count], ...], in any order."""
    return {"v": MANIFEST_VERSION, "map": map_name, "height_version": cf.HEIGHT_VERSION,
            "height_rules": cf.HEIGHT_RULES_REVISION, "min_condense_revision": MIN_CONDENSE_REVISION,
            "geometry": {"sight": geometry.get("sight"), "walk": geometry.get("walk"), "scale": geometry.get("scale")},
            "must_block": must_block,
            "replays": sorted([str(u).lower(), str(recipe), str(source), int(count)]
                              for u, recipe, source, count in replays)}


def digest(m: dict) -> str:
    """16 hex over the whole manifest."""
    return hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def key(m: dict) -> str:
    return f"heights:{m['map']}:{digest(m)}"


def rounds_expected(m: dict) -> int:
    return sum(int(row[3]) for row in m["replays"])


def matches(m: dict) -> list[str]:
    return sorted(row[0] for row in m["replays"])


def changes(old: dict | None, new: dict) -> dict:
    """How `new` differs from the last build's manifest: the matches added, the ones whose evidence is gone
    (deleted, or replaced by other blobs under the same match), and whether anything else moved (the masks, the
    rules, the check set). With no old manifest (a row from before manifests) everything else counts as moved."""
    now = {row[0]: row for row in new["replays"]}
    if old is None:
        return {"added": sorted(now), "gone": [], "other": True}
    was = {row[0]: row for row in old.get("replays") or []}
    rest = {k: v for k, v in new.items() if k != "replays"}
    return {"added": sorted(set(now) - set(was)),
            "gone": sorted(u for u, row in was.items() if now.get(u) != row),
            "other": rest != {k: v for k, v in old.items() if k != "replays"}}
```

In `webapp/app/control/heights.py`, after `class HeightError`, add `rules()` (the constants' hash, for the pin and for a row's record; the revisions come from the stdlib module):

```python
def rules() -> dict:
    """What an asset was built under: the format, the rules' revision, and a hash of this module's constants
    (tests/replays/test_height_inputs.py pins the revision to it)."""
    from app.replays import control_format as cf

    constants = {name: value for name, value in sorted(globals().items())
                 if name.isupper() and isinstance(value, (int, float, str, tuple))}
    text = json.dumps(constants, sort_keys=True, default=str)
    return {"version": HEIGHT_VERSION, "revision": cf.HEIGHT_RULES_REVISION,
            "constants": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]}
```

(`HEIGHT_RULES_REVISION` is read inside the function and never bound as a module name here: a new upper-case name in this module would move `CONTROL_REVISION`'s digest.)

Run `PY -m pytest tests/replays/test_height_inputs.py::test_the_height_rules_are_pinned_to_their_revision -q -p no:cacheprovider`, copy the hash its failure prints into `PINNED_RULES[1]`, and run it again. Expected: PASS.

- [ ] **Step 5: The table**

Create `webapp/alembic/versions/0018_control_heights.py`:

```python
"""control heights

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-05

`control_heights`: a map's height assets, built on the replay worker and stored here
(docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 1). One row per build, with the
`.npz` bytes, the build's report, the matches it was built from and the whole input manifest
(app/replays/height_inputs.py) with its digest; at most one `active` row per map, which is the digest every
round of that map is computed with.

Additive, and empty on both sites when it is created: no map has heights yet, and the ValoMaths demo never will.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

J = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "control_heights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("map_name", sa.String(64), nullable=False),
        sa.Column("digest", sa.String(12), nullable=False),
        sa.Column("asset", sa.LargeBinary(), nullable=False),
        sa.Column("report", J, nullable=False),
        sa.Column("match_uuids", J, nullable=False),
        sa.Column("rules", J, nullable=False),
        sa.Column("inputs", J, nullable=False),
        sa.Column("inputs_sha", sa.String(16), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'rejected', 'superseded')", name="ck_control_heights_status"),
    )
    op.create_index("ix_control_heights_map_built", "control_heights", ["map_name", "built_at"])
    op.create_index("uq_control_heights_active", "control_heights", ["map_name"], unique=True,
                    postgresql_where=sa.text("status = 'active'"), sqlite_where=sa.text("status = 'active'"))


def downgrade() -> None:
    op.drop_index("uq_control_heights_active", table_name="control_heights")
    op.drop_index("ix_control_heights_map_built", table_name="control_heights")
    op.drop_table("control_heights")
```

In `webapp/app/models/replay.py`, add `text` to the `sqlalchemy` import list and, after `ReplayRoundControl`:

```python
class ControlHeight(Base):
    """One build of a map's heights (migration 0018; docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md,
    section 1). Written by the web app's dispatcher from the replay worker's builds and by
    scripts/control_heights.py, both through app/services/control_heights.py. `asset` is the `.npz` as
    app/control/heights.py saves it; the web app never opens it itself. `inputs` is the build's input manifest
    (app/replays/height_inputs.py) and `inputs_sha` its digest; `match_uuids` are that manifest's matches."""

    __tablename__ = "control_heights"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'rejected', 'superseded')", name="ck_control_heights_status"),
        Index("ix_control_heights_map_built", "map_name", "built_at"),
        Index("uq_control_heights_active", "map_name", unique=True, postgresql_where=text("status = 'active'"),
              sqlite_where=text("status = 'active'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    map_name: Mapped[str] = mapped_column(String(64), nullable=False)
    digest: Mapped[str] = mapped_column(String(12), nullable=False)
    asset: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    report: Mapped[dict] = mapped_column(JSONType, nullable=False)
    match_uuids: Mapped[list] = mapped_column(JSONType, nullable=False)
    rules: Mapped[dict] = mapped_column(JSONType, nullable=False)        # heights.rules(): version, revision, constants
    inputs: Mapped[dict] = mapped_column(JSONType, nullable=False)
    inputs_sha: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

In `webapp/app/models/__init__.py`, add `ControlHeight` to the `app.models.replay` import and to `__all__`.

In `webapp/tests/replays/test_replay_store.py`, add `ControlHeight.__table__` to `TABLES` (and `ControlHeight` to its `app.models.replay` import): most replay test fixtures build their databases from that list.

- [ ] **Step 6: The verifying child, and the service**

Create `webapp/app/control/height_verify.py`:

```python
"""Validates a worker height asset against this deploy's map and check set in a child process.

    <python> -m app.control.height_verify --map Ascent --asset-dir <dir> --must-block <file> < asset.npz

Raw arrays are checked before load_asset can cast or reshape them. The description then derives the real
walkable count, readiness and must-block outcomes; a report cannot declare those facts itself. The web app
uses app/services/control_heights.py without importing numpy or app.control in its own process.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


def describe(data: bytes, map_name: str, *, asset_dir: Path | None = None, must_block: Path | None = None) -> dict:
    try:
        import numpy as np
        from app.control import geometry as cg, height_build as hb, heights as hc
        from app.replays import height_inputs as hi

        directory = Path(asset_dir or hi.CONTROL_DIR)
        scale = json.loads(cg.MAPS_JSON.read_text(encoding="utf-8"))[map_name]["xMultiplier"]
        geo = cg.geometry_from_masks(map_name, cg.read_mask_png(directory / f"{map_name}.sight.png"),
                                     cg.read_mask_png(directory / f"{map_name}.walk.png"), scale,
                                     (cg.load_tags(directory).get("maps", {}).get(map_name) or {}).get("specials") or [])
        walk = geo.walk
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "asset.height.npz"
            path.write_bytes(data)
            with np.load(path, allow_pickle=False) as raw:
                shapes = {"floors": (cg.GRID, cg.GRID, hc.MAX_FLOORS),
                          "spread": (cg.GRID, cg.GRID, hc.MAX_FLOORS),
                          "supported": (cg.GRID, cg.GRID), "unresolved": (cg.GRID, cg.GRID),
                          "kind": (cg.GRID, cg.GRID)}
                dtypes = {"floors": np.dtype("<i2"), "spread": np.dtype("<i2"), "supported": np.dtype(bool),
                          "unresolved": np.dtype(bool), "kind": np.dtype("i1"), "edges": np.dtype("<i4")}
                arrays = {name: raw[name] for name in dtypes}
                for name, shape in shapes.items():
                    if arrays[name].shape != shape:
                        raise ValueError(f"{name} has shape {arrays[name].shape}, not {shape}")
                for name, dtype in dtypes.items():
                    if arrays[name].dtype != dtype:
                        raise ValueError(f"{name} has dtype {arrays[name].dtype}, not {dtype}")
                edges = arrays["edges"]
                if edges.ndim != 2 or edges.shape[1] != 5:
                    raise ValueError(f"edges has shape {edges.shape}, not K x 5")
                meta = json.loads(bytes(raw["meta"]).decode("utf-8"))
                if not isinstance(meta, dict) or type(meta.get("origin_z")) is not int \
                        or meta.get("units") != "dm" or meta.get("stand_m") != hc.STAND_M:
                    raise ValueError("invalid height frame or units")
                floors, spread = arrays["floors"], arrays["spread"]
                has = floors >= 0
                if (floors < -1).any() or (has[..., 1:] & ~has[..., :-1]).any() \
                        or (has[..., 1:] & (floors[..., 1:] <= floors[..., :-1])).any():
                    raise ValueError("floors must be contiguous, increasing, and use -1 for missing")
                if (spread < 0).any():
                    raise ValueError("negative floor spread")
                supported, unresolved, kind = arrays["supported"], arrays["unresolved"], arrays["kind"]
                if (has[..., 0] & ~walk).any() or (supported & (~walk | ~has[..., 0])).any() \
                        or (unresolved & (~walk | has[..., 0])).any():
                    raise ValueError("floor/support/unresolved cells disagree with the walk mask or floors")
                if not np.isin(kind, [hc.KIND_NONE, hc.KIND_STANDS, hc.KIND_WALKS, hc.KIND_FILLED,
                                     hc.KIND_GRADIENT]).all():
                    raise ValueError("unknown ground kind")
                flat = floors.reshape(-1, hc.MAX_FLOORS)
                if len(edges):
                    a, fa, b, fb, how = edges.T
                    if ((a < 0) | (a >= cg.GRID ** 2) | (b < 0) | (b >= cg.GRID ** 2)).any():
                        raise ValueError("edge cell out of bounds")
                    if ((fa < 0) | (fa >= hc.MAX_FLOORS) | (fb < 0) | (fb >= hc.MAX_FLOORS)).any():
                        raise ValueError("edge floor out of bounds")
                    if ((flat[a, fa] < 0) | (flat[b, fb] < 0)).any():
                        raise ValueError("edge names a missing floor")
                    if ((a == b) | (np.abs(a % cg.GRID - b % cg.GRID) > 1)
                            | (np.abs(a // cg.GRID - b // cg.GRID) > 1)).any():
                        raise ValueError("edge cells are not distinct eight-neighbours")
                    if not np.isin(how, [hc.EDGE_STEP, hc.EDGE_SLIDE, hc.EDGE_FALL]).all():
                        raise ValueError("unknown edge kind")
                    if len(np.unique(edges[:, :4], axis=0)) != len(edges):
                        raise ValueError("duplicate edge endpoints")
            asset = hc.load_asset(path)  # version check, after validation without lossy casts/reshapes
        count = asset.floor_count().ravel()
        share, reasons = hb.readiness(asset.supported.ravel(), asset.unresolved.ravel(), count, int(walk.sum()))
        lines, sha = hi.must_block_set(must_block)
        must = {"set": sha, **hb.must_block_check(lines, cg.attach_heights(geo, asset), map_name)}
        return {"digest": asset.digest, "version": int(asset.meta["version"]), "map": map_name,
                "walkable_cells": int(walk.sum()), "supported_cells": int(asset.supported.sum()),
                "floor_cells": int((count > 0).sum()), "supported": round(share, 4),
                "ready": not reasons, "not_ready": reasons, "must_block": must}
    except Exception as error:  # noqa: BLE001 - unreadable asset, map or check set: trust nothing
        return {"error": f"{type(error).__name__}: {error}"[:300]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", required=True)
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--must-block", type=Path, required=True)
    args = parser.parse_args()
    sys.stdout.write(json.dumps(describe(sys.stdin.buffer.read(), args.map, asset_dir=args.asset_dir,
                                       must_block=args.must_block)))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Create `webapp/app/services/control_heights.py`:

```python
"""A map's height assets in the database (`control_heights`, migration 0018;
docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, sections 1 and 4).

- `active_digests`: each map's active digest, which is what a round's fingerprint reads
  (app/services/replay_control.py `geometry_inputs`). A row of another HEIGHT_VERSION is left out: this
  deploy's engine can't load it, so the map computes flat until its next build.
- **Trusting a result** (`verify_asset`, `integrity`): before anything is stored, the asset's bytes are opened
  by a child process (app/control/height_verify.py; this process never imports numpy) and must be the asset
  the result names, of this format; the raw arrays and topology must be valid; the report's walkable count and readiness must match the
  child deriving them on this map; and its must-block counts and outcome must match the child rerunning
  this deploy's actual lines. Kill-line counts and shares must add up (the child has no replay blobs). A result that fails
  any of it is the build's failure: nothing is stored.
- **The gate** (`gate`, `store_build`): a trusted build goes live when the map is at the bar and both checks
  pass (`active`; the one before it becomes `superseded`); otherwise it is stored `rejected` with its report
  and the previous asset stays. Either way it is the map's last build, which decides when the next one is due
  (app/services/replay_heights_remote.py).
- `activate` and `deactivate`: the operator's way back (scripts/control_heights.py), and how a map whose
  evidence was deleted is turned off.

Every change to which row is active (a build going live, `activate`, `deactivate`) takes the map's advisory
lock first and writes with SQL statements by row id, never through loaded objects: two writers wait for each
other, and activating the row that is already active changes nothing. The one-active-row index is the
backstop, not the ordering.

Making a row active or not changes the map's fingerprint inputs, so its stored rounds turn stale; they stay
on the page, marked out of date, until the idle queue has recomputed them.

Standard library and the DB only.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from app.models.replay import ControlHeight
from app.replays import control_format as cf
from app.replays import db as replay_db
from app.replays import height_inputs

ACTIVE, REJECTED, SUPERSEDED = "active", "rejected", "superseded"
WEBAPP_ROOT = Path(__file__).resolve().parents[2]


class Busy(Exception):
    """Another change to this map's heights got there first; nothing was written."""


class HasGeneration(Exception):
    """The map has a published feature generation, which names its current heights."""


GENERATION_NOTE = ("{map} has a published feature generation ({generation}); it names the heights it was compiled "
                   "against, so changing them would make every round of the map fail verification. Republish the "
                   "generation against the heights you want first")


def _generation_note(map_name: str, supplied: str | None = None) -> str | None:
    from app.services import replay_control  # lazy: this service is imported by replay_control

    generation = (replay_control.geometry_inputs(map_name, heights=None) or {}).get("features") or supplied
    return GENERATION_NOTE.format(map=map_name, generation=generation) if generation else None


def lock_name(map_name: str) -> str:
    return f"control-heights:{map_name}"


def _usable(rules: dict | None) -> bool:
    return (rules or {}).get("version") == cf.HEIGHT_VERSION


def active_digests(db) -> dict[str, str]:
    """{map: digest} of the active rows this deploy can load. One query."""
    rows_ = db.query(ControlHeight.map_name, ControlHeight.digest, ControlHeight.rules) \
        .filter(ControlHeight.status == ACTIVE)
    return {name: digest for name, digest, rules in rows_ if _usable(rules)}


def rows(db, map_name: str | None = None) -> list[ControlHeight]:
    """Builds, newest first, without their asset bytes."""
    query = db.query(ControlHeight).options(defer(ControlHeight.asset))
    if map_name:
        query = query.filter(ControlHeight.map_name == map_name)
    return query.order_by(ControlHeight.built_at.desc(), ControlHeight.id.desc()).all()


def last_builds(db) -> dict[str, ControlHeight]:
    """Each map's newest build (any status), without its asset and report."""
    out: dict[str, ControlHeight] = {}
    query = db.query(ControlHeight).options(defer(ControlHeight.asset), defer(ControlHeight.report)) \
        .order_by(ControlHeight.built_at.desc(), ControlHeight.id.desc())
    for row in query:
        out.setdefault(row.map_name, row)
    return out


def active_rows(db) -> dict[str, ControlHeight]:
    """Each map's active row (any format), without its asset and report."""
    query = db.query(ControlHeight).options(defer(ControlHeight.asset), defer(ControlHeight.report)) \
        .filter(ControlHeight.status == ACTIVE)
    return {row.map_name: row for row in query}


def asset_bytes(db, map_name: str, digest: str) -> bytes | None:
    row = db.query(ControlHeight.asset).filter(ControlHeight.map_name == map_name, ControlHeight.digest == digest) \
        .order_by(ControlHeight.id.desc()).first()
    return None if row is None else bytes(row[0])


def verify_asset(data: bytes, map_name: str, timeout_s: float = 120.0, *,
                 asset_dir: Path | None = None, must_block: Path | None = None) -> dict:
    """What the bytes are, by app/control/height_verify.py in a child process: its description, or {"error"}."""
    try:
        done = subprocess.run([sys.executable, "-m", "app.control.height_verify", "--map", map_name,
                               "--asset-dir", str(asset_dir or height_inputs.CONTROL_DIR),
                               "--must-block", str(must_block or height_inputs.MUST_BLOCK)],
                              input=data, capture_output=True, timeout=timeout_s, cwd=str(WEBAPP_ROOT), check=False)
        found = json.loads(done.stdout or b"{}")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {"error": f"{type(error).__name__}: {error}"[:300]}
    return found if isinstance(found, dict) and found else {"error": f"the verifier exited {done.returncode}"}


def _count(value) -> bool:
    return type(value) is int and value >= 0


def _share(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def integrity(result_digest: str, report, verified: dict, must_block: str | None) -> list[str]:
    """Why a build's result can't be trusted; empty when it can. `verified` is `verify_asset`'s answer for the
    result's bytes, `must_block` this deploy's check-set hash (None when its own file is unreadable: then
    nothing is trusted). Whether the build is good enough is `gate`'s question, not this one's."""
    why = []
    if verified.get("error"):
        why.append(f"the asset could not be opened: {verified['error']}")
    else:
        if verified.get("digest") != result_digest:
            why.append(f"the bytes are {verified.get('digest')}: that is not the asset {result_digest}")
        if verified.get("version") != cf.HEIGHT_VERSION:
            why.append(f"the asset is height version {verified.get('version')}, not {cf.HEIGHT_VERSION}")
    if not isinstance(report, dict):
        return [*why, "the result's report is not a report"]
    walkable, supported = report.get("walkable_cells"), report.get("supported_cells")
    if not _count(walkable) or not _count(supported) or walkable == 0 or supported > walkable:
        why.append("the report's cell counts are missing or impossible")
    else:
        if not verified.get("error") and verified.get("supported_cells") != supported:
            why.append(f"the report says {supported} supported cells, the asset has {verified.get('supported_cells')}")
        if not _share(report.get("supported")) or abs(report["supported"] - supported / walkable) > 0.001:
            why.append("the report's supported share isn't its counts'")
    if not verified.get("error"):
        if verified.get("walkable_cells") != walkable:
            why.append("the report's walkable cells aren't this map's walkable cells")
        if report.get("ready") != verified.get("ready") or report.get("not_ready") != verified.get("not_ready"):
            why.append("the report's readiness isn't the asset's readiness on this map")
    ready, not_ready = report.get("ready"), report.get("not_ready")
    if type(ready) is not bool or not isinstance(not_ready, list) or ready == bool(not_ready):
        why.append("the report's ready flag and its reasons disagree")
    kills = report.get("kill_lines")
    if not isinstance(kills, dict) or not _count(kills.get("qualifying")) or not _count(kills.get("blocked")) \
            or kills["qualifying"] == 0 or kills["blocked"] > kills["qualifying"] or not _share(kills.get("share")) \
            or abs(kills["share"] - kills["blocked"] / kills["qualifying"]) > 0.001 \
            or type(kills.get("passes")) is not bool \
            or kills["passes"] != (kills["blocked"] / kills["qualifying"] <= cf.KILL_LINE_BAR):
        why.append("the kill lines' result is missing, checked no kill, or doesn't add up")
    must = report.get("must_block")
    if not isinstance(must, dict) or not all(_count(must.get(k)) for k in ("lines", "checked", "unchecked", "blocked")) \
            or must["checked"] + must["unchecked"] != must["lines"] or must["blocked"] > must["checked"] \
            or type(must.get("passes")) is not bool \
            or must["passes"] != (must["unchecked"] == 0 and must["blocked"] == must["checked"]):
        why.append("the must-block result is missing or doesn't add up")
    if must_block is None or not isinstance(must, dict) or must.get("set") != must_block:
        why.append(f"the must-block check ran against check set {(must or {}).get('set') if isinstance(must, dict) else None}, "
                   f"this deploy's is {must_block}")
    actual_must = verified.get("must_block")
    if not verified.get("error") and (not isinstance(actual_must, dict) or not isinstance(must, dict)
            or any(must.get(k) != actual_must.get(k) for k in
                   ("set", "lines", "checked", "unchecked", "blocked", "passes"))):
        why.append("the must-block evidence isn't the check run on this asset and this map")
    return why


def gate(report: dict) -> list[str]:
    """Why a trusted build may not go live (the bar and the two checks a local build uses); empty when it may."""
    why = []
    if not report.get("ready"):
        why += list(report.get("not_ready") or ["below the bar"])
    if report["supported_cells"] / report["walkable_cells"] < cf.HEIGHT_SUPPORTED_MIN and report.get("ready"):
        why.append(f"supported {report['supported_cells'] / report['walkable_cells']:.1%} is under "
                   f"{cf.HEIGHT_SUPPORTED_MIN:.0%}")
    kills, must = report["kill_lines"], report["must_block"]
    if not kills["passes"]:
        why.append(f"kill lines: {kills['blocked']} of {kills['qualifying']} blocked by the heights")
    if not must["passes"]:
        why.append(f"must-block: {must['blocked']} of {must['lines']} impossible sightlines blocked, "
                   f"{must['unchecked']} could not be checked")
    return why


def _make_active(db, map_name: str, row_id: int | None) -> None:
    """Under the map's lock: every other active row of the map becomes superseded, then row `row_id` (if any)
    is active. SQL by id, so it doesn't matter what any session has loaded, and the row that is already active
    is never retired. The caller commits."""
    others = db.query(ControlHeight).filter(ControlHeight.map_name == map_name, ControlHeight.status == ACTIVE)
    if row_id is not None:
        others = others.filter(ControlHeight.id != row_id)
    others.update({ControlHeight.status: SUPERSEDED}, synchronize_session=False)
    if row_id is not None:
        db.query(ControlHeight).filter(ControlHeight.id == row_id) \
            .update({ControlHeight.status: ACTIVE}, synchronize_session=False)


def store_build(db, *, map_name: str, digest: str, asset: bytes, report: dict, inputs: dict,
                rules: dict) -> tuple[str, list[str]]:
    """Stores a trusted build behind the gate and commits: ("active", []) or ("rejected", why). Raises Busy
    when another change to the map's heights won the race, or HasGeneration when a published feature
    generation is present at the final lookup under the map lock; nothing is written then."""
    why = gate(report)
    try:
        replay_db.advisory_lock(db, lock_name(map_name))
        note = _generation_note(map_name)
        if note:
            db.rollback()
            raise HasGeneration(note)  # even when it was published after planning: no row and no activation
        row = ControlHeight(map_name=map_name, digest=digest, asset=asset, report=report,
                            match_uuids=height_inputs.matches(inputs), rules=rules, inputs=inputs,
                            inputs_sha=height_inputs.digest(inputs), status=REJECTED if why else SUPERSEDED)
        db.add(row)
        db.flush()                       # it has an id, and is not active yet
        if not why:
            _make_active(db, map_name, row.id)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise Busy(map_name) from error
    db.expire_all()
    return (REJECTED, why) if why else (ACTIVE, [])


def activate(db, map_name: str, digest: str, generation: str | None = None) -> str | None:
    """Makes the map's newest build with this digest the active one (it may be already). None when done, else
    why not."""
    replay_db.advisory_lock(db, lock_name(map_name))
    note = _generation_note(map_name, generation)
    if note:
        db.rollback()
        return note
    found = db.query(ControlHeight.id, ControlHeight.rules) \
        .filter(ControlHeight.map_name == map_name, ControlHeight.digest == digest) \
        .order_by(ControlHeight.id.desc()).first()
    if found is None:
        db.rollback()
        return f"no build of {map_name} has digest {digest}"
    row_id, rules = found
    if not _usable(rules):
        db.rollback()
        return (f"{digest} is height version {(rules or {}).get('version')}, this deploy's is {cf.HEIGHT_VERSION}: "
                f"the engine can't load it")
    try:
        _make_active(db, map_name, row_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        return f"another change to {map_name}'s heights got there first; look again and retry"
    db.expire_all()
    return None


def deactivate(db, map_name: str, generation: str | None = None) -> bool:
    """Leaves the map with no active heights (flat). Whether there was one."""
    replay_db.advisory_lock(db, lock_name(map_name))
    note = _generation_note(map_name, generation)
    if note:
        db.rollback()
        raise HasGeneration(note)
    had = db.query(ControlHeight.id).filter(ControlHeight.map_name == map_name,
                                            ControlHeight.status == ACTIVE).first() is not None
    _make_active(db, map_name, None)
    db.commit()
    db.expire_all()
    return had
```

On SQLite (tests, a checkout's own database) `advisory_lock` does nothing and the index alone decides; that is
enough for one process. On PostgreSQL the lock is `pg_advisory_xact_lock(hashtext(name))`, released by the
commit or rollback that ends each of these functions.

- [ ] **Step 7: The must-block path's other readers**

Run: `git grep -n "control_must_block\|MUST_BLOCK"`

- `webapp/scripts/build_control_heights.py`: replace its `MUST_BLOCK = ...` line with `MUST_BLOCK = height_inputs.MUST_BLOCK` and import `from app.replays import height_inputs  # noqa: E402`; its docstring's path becomes `app/static/data/control/must_block.json`. (Task 5 replaces how it reads the file.)
- `webapp/app/control/height_build.py`: `must_block_check`'s docstring names the new path.
- `webapp/tests/replays/test_control_heights.py`: `test_the_committed_must_block_set_is_well_formed` reads `height_inputs.MUST_BLOCK`; the `no_picture` fixture and the tests that patch `command.MUST_BLOCK` are unchanged for now.
- `webapp/tests/replays/test_compare_height_builds.py` (the slopes plan's): unchanged, it patches `command.MUST_BLOCK`.

- [ ] **Step 8: A round's inputs read the active digest**

In `webapp/app/services/replay_control.py`:

Import the service: `from app.services import control_heights` (with the other `app` imports).

`geometry_inputs` takes the active digests. Change its signature, add one paragraph to its docstring, and replace the `height_sha` lines:

```python
def geometry_inputs(map_name: str, heights: dict | None = None) -> dict | None:
```

```
    `heights` is `control_heights.active_digests(db)`: a map's active digest in the database is its heights
    (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 1). The committed index.json
    `height_sha` is the fallback for a map with no active row, and all there is when `heights` is None (a
    checkout without a database, most tests). Every caller under app/ and scripts/ passes it
    (tests/replays/test_control_heights_db.py).
```

```python
    height = (heights or {}).get(map_name) or entry.get("height_sha")
    if height:
        inputs["height"] = height
```

`round_fingerprint` passes it on:

```python
def round_fingerprint(replay: Replay, groups: dict[int, str], n: int, heights: dict | None = None) -> str | None:
    geometry = geometry_inputs(replay.map_name, heights)
```

In `round_control`, the stale test becomes:

```python
    heights = control_heights.active_digests(db)
    stale = row.fingerprint != round_fingerprint(replay, side_groups(db, replay), n, heights)
```

In `plan`, read them once, after `valid = _valid_ids(db, replays)`:

```python
    heights = control_heights.active_digests(db)
```

and use them: `geometry = geometry_inputs(replay.map_name, heights)`.

The other callers, one line each:

| File | Change |
| --- | --- |
| `app/services/replay_control_store.py` (`store_round`) | `from app.services import control_heights`; `current = replay_control.round_fingerprint(replay, replay_control.side_groups(session, replay), round_number, control_heights.active_digests(session))` |
| `app/services/replay_control_views.py` (`load_round_summaries`) | before the loop `heights = control_heights.active_digests(db)` (import the service); in the loop `replay_control.round_fingerprint(replay, groups, n, heights)` |
| `app/services/replay_gaps.py` | `control = replay_control.round_fingerprint(replay, replay_control.side_groups(db, replay), n, control_heights.active_digests(db))` (import the service) |
| `app/services/gap_patterns.py` (`m_per_px`) | `inputs = replay_control.geometry_inputs(map_name, heights=None)` (only the scale is read) |
| `app/services/replay_control_remote.py` | `_matches_geometry(result, map_name, heights)` uses `replay_control.geometry_inputs(map_name, heights)`; `_collect` becomes `_collect(session_factory, session, client, state, now, counts)`, reads `heights = control_heights.active_digests(session)` at its top and passes it (`cycle` gives it its own session); in `_submit`, `heights = control_heights.active_digests(session)` once and `replay_control.geometry_inputs(p.map_name, heights)` for the features lookup |
| `scripts/compute_control.py` (`store_result`) | `replay_control.geometry_inputs(planned.map_name, heights=None)` (only `features` is read) |

Audit test doubles as part of this signature change: `rg -n 'geometry_inputs|round_fingerprint|_collect' webapp/tests`.
In `test_control_store.py::test_the_fingerprint_moves_with_each_input`, replace `lambda name: geometry` with
`lambda name, heights=None: geometry`; keep its changed-mask fingerprint assertion. Update every other
patched callable to accept the actual new signature (including `_collect`'s `session`). Do not suppress these
failures as baseline failures: they are introduced by this task. The AST test below audits production callers;
the full replay suite exercises these test doubles.

In `webapp/tests/replays/test_control_isolation.py`, add `WEBAPP / "app" / "services" / "control_heights.py"` and `WEBAPP / "app" / "replays" / "height_inputs.py"` to the list in `test_the_web_apps_control_views_import_nothing_heavy`.

- [ ] **Step 9: Run the tests**

Run: `PY -m pytest tests/replays/test_height_inputs.py tests/replays/test_control_heights_db.py tests/replays/test_control_isolation.py -q -p no:cacheprovider`
Expected: all pass (the `pg` test is skipped without `VALO_TEST_DATABASE_URL`). Before Step 6's `_make_active` exists in this form, `test_activating_the_digest_that_is_already_active_leaves_it_active` is the test that would fail: if you want to see the defect it guards, write `activate` first as "retire the active row with a bulk update, then set `row.status = ACTIVE` on the loaded row" and watch it leave the map with no active row.

Then the suite: `PY -m pytest tests/replays -k "not pg" -q -p no:cacheprovider`
Expected: only the failures recorded in the baseline, failing the same way. A test that fails with `no such table: control_heights` builds its own table list: add `ControlHeight.__table__` to it (the known ones are `test_ingest_replay.py`'s two `create_all` calls).

- [ ] **Step 10: The migration runs, and the PostgreSQL test**

With the local Postgres up (`docker compose -p valomaths-private up -d` from `webapp/`):

Run: `PY -m alembic upgrade head` then `PY -m alembic downgrade -1` then `PY -m alembic upgrade head`
Expected: each ends without an error, and `PY -m alembic current` prints the new head.

With `VALO_TEST_DATABASE_URL` set to a `*_test` database at that head: `PY -m pytest tests/replays/test_control_heights_db.py -k pg -q -p no:cacheprovider`
Expected: 1 passed. If no such database is set up, say so in the task's report: the concurrency test did not run.

- [ ] **Step 11: Commit**

```bash
git add webapp/alembic/versions webapp/app/models webapp/app/replays webapp/app/control/heights.py webapp/app/control/height_verify.py webapp/app/control/height_build.py webapp/app/services webapp/app/static/data/control/must_block.json webapp/scripts webapp/tests/replays
git commit -F <message file>
```

Message: `Heights: the control_heights table, the input manifest, one lock per map, and a result that must prove itself`.

---

### Task 3: The engine gets an asset by its digest

**Files:**
- Modify: `webapp/app/control/geometry.py` (`height_cache_path`, `load_geometry`)
- Modify: `webapp/app/control/task.py` (`_load`, `compute_task`, module docstring)
- Modify: `webapp/scripts/compute_control.py` (`run`)
- Test: `webapp/tests/replays/test_control_task.py`

**Interfaces:**
- Consumes: `heights.load_asset(path)`, `HeightAsset.digest`, `geometry.cache_dir()`, Task 2's `control_heights.active_digests` and `asset_bytes`.
- Produces:
  - `geometry.height_cache_path(name: str, digest: str, directory: Path | None = None) -> Path` (`<cache>/heights/<Map>.<digest>.height.npz`).
  - `load_geometry(name, asset_dir=ASSET_DIR, heights: Path | None = None)`: `heights` now replaces only the height source; a feature generation named by index.json is still loaded.
  - A control task may carry `"height": "<digest>"`: the child loads `height_cache_path(map, digest)` and refuses (`GeometryError`, an infra failure) when the file is missing or its digest is another.
  - `task.height_file(name, digest) -> Path`.
  - `compute_control.cache_heights(session, maps) -> dict[str, str]` ({map: digest} it wrote or found in the local cache).

- [ ] **Step 1: Write the failing tests**

Append to `webapp/tests/replays/test_control_task.py` (it already imports `pytest`; add what is missing from these imports at its top):

```python
from app.control import geometry as cg
from app.control import height_build as hb
from app.control import heights as hc
from app.control import task as control_task
from tests.replays.control_toys import height_rounds, open_hall, standing


def _toy_asset():
    geo = open_hall()
    return hb.build(height_rounds(lambda m, n: {0: standing(204, 204, 0.0), 1: standing(212, 204, 0.0)}), geo).asset


def test_a_task_naming_a_height_digest_reads_it_from_the_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_CACHE_DIR", str(tmp_path))
    asset = _toy_asset()
    path = cg.height_cache_path("Toy", asset.digest)
    assert path == tmp_path / "heights" / f"Toy.{asset.digest}.height.npz"
    with pytest.raises(cg.GeometryError, match="not on this machine"):
        control_task.height_file("Toy", asset.digest)
    path.parent.mkdir(parents=True)
    hc.save_asset(path, asset)
    assert control_task.height_file("Toy", asset.digest) == path
    other = cg.height_cache_path("Toy", "0" * 12)
    hc.save_asset(other, asset)                           # a file whose bytes are another asset's
    with pytest.raises(cg.GeometryError, match="is not the asset"):
        control_task.height_file("Toy", "0" * 12)
    assert not other.exists(), "a wrong file is removed, so the next push replaces it"


def test_a_failure_to_find_the_heights_is_the_machines_not_the_rounds(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_CACHE_DIR", str(tmp_path))
    out = control_task.compute_task({"key": "k", "map": "Ascent", "height": "f" * 12, "blob": b"", "link":
                                     {"sides": {}, "db_deaths": []}})
    assert out["status"] == "failed" and out["error_kind"] == "infra" and "not on this machine" in out["error"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_task.py -q -p no:cacheprovider -k "height"`
Expected: FAIL, `AttributeError: module 'app.control.geometry' has no attribute 'height_cache_path'`.

- [ ] **Step 3: The cache path, and `load_geometry`'s `heights` replaces only the heights**

In `webapp/app/control/geometry.py`, after `cache_dir`:

```python
def height_cache_path(name: str, digest: str, directory: Path | None = None) -> Path:
    """Where a height asset fetched by its digest is kept: beside the visibility cache, never in the repository
    (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 1)."""
    return Path(directory or cache_dir()) / "heights" / f"{name}.{digest}.height.npz"
```

In `load_geometry`, the early return for `heights` skipped the feature generation. Replace everything from `if heights is not None:` to the end of the function with:

```python
    index_path = asset_dir / "index.json"
    row = (json.loads(index_path.read_text(encoding="utf-8")).get("maps", {}).get(name) or {}) if index_path.is_file() else {}
    if row.get("features_sha"):          # never on a committed map in this build: no feature is enabled
        from app.control import features

        geo.features = features.load_generation(asset_dir, row["features_sha"])
        geo.features_sha = row["features_sha"]
    if heights is not None:              # a preview's, or one fetched by its digest (height_cache_path)
        return attach_heights(geo, hc.load_asset(heights))
    wanted = row.get("height_sha")
    if wanted:
        asset = hc.load_asset(asset_dir / f"{name}.height.npz")
        if asset.digest != wanted:
            raise GeometryError(f"{name}.height.npz is {asset.digest}, index.json says {wanted}; rebuild the heights")
        attach_heights(geo, asset)
    return geo
```

and its docstring's last sentence becomes: "`heights` loads that asset file instead: a preview's, or the map's active asset fetched by its digest (the database's, which wins over a committed one)."

- [ ] **Step 4: The task names a digest**

In `webapp/app/control/task.py`:

Add to the module docstring's first paragraph: "A task may name the map's heights by digest (`\"height\"`): the asset is then read from the control cache (`geometry.height_cache_path`), where the replay worker's `/heights` endpoint or scripts/compute_control.py put it; a missing or wrong file is the machine's failure."

Add before `_load`:

```python
def height_file(name: str, digest: str) -> Path:
    """The cached asset a task names, checked: it must be there and be that asset. A wrong file is removed."""
    from app.control import geometry
    from app.control import heights as hc

    path = geometry.height_cache_path(name, digest)
    if not path.is_file():
        raise geometry.GeometryError(f"{name}: height asset {digest} is not on this machine")
    try:
        found = hc.load_asset(path).digest
    except Exception as error:  # noqa: BLE001 - a truncated or foreign file
        found = f"unreadable ({type(error).__name__})"
    if found != digest:
        path.unlink(missing_ok=True)
        raise geometry.GeometryError(f"{name}: {path.name} is not the asset {digest} (it is {found})")
    return path
```

`_load` takes the digest and resolves it to a file once per geometry it loads. Change its signature and first lines:

```python
def _load(name: str, heights: str | None = None, digest: str | None = None):
    from app.control import features, geometry

    if heights is None and digest:
        heights = str(geometry.height_cache_path(name, digest))
    # A map's own geometry is keyed by its name; with an active feature generation, by that generation too, so
    # a worker never keeps computing with a superseded one (absent for every map today: the key is unchanged).
    generation = features.active_sha(name)
    key = name if heights is None and generation is None else (name, heights, generation)
    if key not in _GEOMETRY:
        if digest:
            height_file(name, digest)
        geo = geometry.load_geometry(name, heights=Path(heights) if heights else None)
```

(the rest of the function is unchanged). In `compute_task`: `geo = _load(task["map"], task.get("heights"), task.get("height"))`.

`replay_worker/control_job.py` passes the task through untouched, so it needs no change; add to its docstring's task line: `` and optionally `"height"` (the digest of the map's heights, already pushed to this worker's cache) ``.

- [ ] **Step 5: The local command reads the asset from the database**

In `webapp/scripts/compute_control.py`, add before `run`:

```python
def cache_heights(session, maps) -> dict[str, str]:
    """{map: digest} for the maps whose heights are in the database, each asset written to the local control
    cache (app/control/geometry.py `height_cache_path`) when it isn't there yet."""
    from app.control import geometry
    from app.services import control_heights

    out = {}
    active = control_heights.active_digests(session)
    for name in maps:
        digest = active.get(name)
        if not digest:
            continue
        path = geometry.height_cache_path(name, digest)
        if not path.is_file():
            data = control_heights.asset_bytes(session, name, digest)
            if data is None:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        out[name] = digest
    return out
```

In `run`, the visibility warm-up loads each map with its database heights, and each task names them. Replace the warm-up loop with:

```python
    reader = session_factory()   # blobs are read as their rounds start, not all up front
    heights = cache_heights(reader, sorted({p.map_name for p in todo}))
    reader.rollback()
    for name in sorted({p.map_name for p in todo}):
        started = time.time()
        cached = geometry.height_cache_path(name, heights[name]) if name in heights else None
        geo = geometry.visibility(geometry.load_geometry(name, heights=cached))
        print(f"{name}: visibility {geo.visibility_source} in {time.time() - started:.0f}s"
              + (f" (heights {heights[name]})" if name in heights else ""), flush=True)
```

delete the later line `reader = session_factory()   # blobs are read as their rounds start, not all up front` (it moved up), and after the `task = {...}` literal add:

```python
                if p.map_name in heights:
                    task["height"] = heights[p.map_name]
```

In the module docstring's "Which rounds" bullet, replace "after committing one map's heights, `--map <Map>` recomputes just that map's stale rounds" with "after a map's heights change (a new active row in `control_heights`), `--map <Map>` recomputes just that map's stale rounds; the asset is read from the database into `webapp/.control_cache/heights/`".

- [ ] **Step 6: Run the tests**

Run: `PY -m pytest tests/replays/test_control_task.py tests/replays/test_control_store.py tests/replays/test_control_features.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add webapp/app/control/geometry.py webapp/app/control/task.py webapp/scripts/compute_control.py replay_worker/control_job.py webapp/tests/replays/test_control_task.py
git commit -F <message file>
```

Message: `Control: a round's task names its heights by digest; the local command reads them from the database`.

---

### Task 4: The worker keeps pushed assets, and a round names its digest

**Files:**
- Modify: `replay_worker/server.py` (`Settings`, `ControlJob`, `ControlRunner.submit` and `_next`, the handler)
- Modify: `webapp/app/services/replay_control_remote.py` (`ControlClient`, `_submit`)
- Test: `webapp/tests/replays/test_replay_worker_control.py`, `webapp/tests/replays/test_control_remote.py`

**Interfaces:**
- Consumes: Task 3's task key `"height"`; `control_heights.asset_bytes`.
- Produces:
  - Worker: `Settings.control_cache_dir: Path` (env `CONTROL_CACHE_DIR`, default `webapp/.control_cache`); `POST /heights` body `{"map", "digest", "asset" (base64)}` -> `200 {"stored": true}` (400 bad body, 404 control off, 413 too large); `POST /control` answers `409 {"error", "height"}` when the task names a digest that isn't in the cache; `ControlJob.warm_key` (`"<map>:<digest or empty>"`), which is what "warm" is tracked by.
  - Web app: `replay_control_remote.Conflict(body: dict)` (any 409, with the worker's JSON body) and `Rejected(code, body)` (400 or 413: the request itself is refused, and asking again won't help), raised by `ControlClient._call`; `NeedsHeight(digest)`, raised only by `ControlClient.submit` when a control round's conflict names a height digest; `ControlClient.push_height(map_name, digest, asset: bytes) -> dict`; tasks carry `"height"` when the map's heights come from the database. A 409 means something different at each endpoint (Task 6 adds more), so `_call` never decides what one means.

- [ ] **Step 1: Write the failing worker tests**

Append to `webapp/tests/replays/test_replay_worker_control.py`:

```python
# ---------------------------------------------------------------- heights by digest
# (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 1)


def height_task(key, digest, map_name="Ascent", mode="ok"):
    return {**task(key, map_name, mode), "height": digest}


def test_a_pushed_asset_is_kept_and_a_round_naming_a_missing_one_is_asked_for(tmp_path, runner):
    r, _ = runner(control_cache_dir=tmp_path / "cache")
    base = serve(tmp_path, r)
    code, body = http(base, "/control", height_task("h:1:f", "a" * 12))
    assert code == 409 and body["height"] == "a" * 12, "the web app pushes it and asks again"
    assert http(base, "/heights", {"map": "Ascent", "digest": "a" * 12, "asset": "bnB6"})[0] == 200
    assert (tmp_path / "cache" / "heights" / f"Ascent.{'a' * 12}.height.npz").read_bytes() == b"npz"
    code, body = http(base, "/control", height_task("h:1:f", "a" * 12))
    assert code == 202
    for bad in ({"map": "../x", "digest": "a" * 12, "asset": "bnB6"}, {"map": "Ascent", "digest": "nope", "asset": "bnB6"},
                {"map": "Ascent", "digest": "a" * 12, "asset": "***"}, {"map": "Ascent"}):
        assert http(base, "/heights", bad)[0] == 400, bad


def test_only_a_few_assets_per_map_are_kept(tmp_path, runner):
    r, _ = runner(control_cache_dir=tmp_path / "cache")
    base = serve(tmp_path, r)
    for i in range(server.HEIGHTS_KEPT + 2):
        assert http(base, "/heights", {"map": "Ascent", "digest": f"{i:012x}", "asset": "bnB6"})[0] == 200
        time.sleep(0.02)
    kept = sorted(p.name for p in (tmp_path / "cache" / "heights").iterdir())
    assert len(kept) == server.HEIGHTS_KEPT and f"Ascent.{server.HEIGHTS_KEPT + 1:012x}.height.npz" in kept


def test_a_new_height_digest_makes_the_map_cold_again(tmp_path, runner):
    # The visibility cache is per heights: the first round under a new digest runs alone, as a map's first does.
    cache = tmp_path / "cache" / "heights"
    cache.mkdir(parents=True)
    for digest in ("a" * 12, "b" * 12):
        (cache / f"Ascent.{digest}.height.npz").write_bytes(b"npz")
    r, log = runner(control_cache_dir=tmp_path / "cache")
    first = r.submit(height_task("w:1:f", "a" * 12))
    wait_all(r, [first])
    assert r.counts()["warm"] == [f"Ascent:{'a' * 12}"]
    jobs = [r.submit(height_task(f"w:{n}:g", "b" * 12, mode="sleep:0.4")) for n in (2, 3, 4)]
    wait_all(r, jobs)
    most, events = overlaps(log)
    starts = [t for t, kind, _ in events if kind == "start"][1:]
    ends = [t for t, kind, _ in events if kind == "end"][1:]
    assert starts[1] >= ends[0], "the first round under the new heights finished before another started"
```

`overlaps` splits a log file's name on `_`; a map name has none, so it keeps working.

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_replay_worker_control.py -q -p no:cacheprovider -k "pushed or few_assets or cold_again"`
Expected: FAIL, `TypeError: Settings.__init__() got an unexpected keyword argument 'control_cache_dir'`.

- [ ] **Step 3: The worker**

In `replay_worker/server.py`:

Add a module constant beside `CONTROL_FINISHED_KEPT`:

```python
HEIGHTS_KEPT = 3                 # pushed height assets kept per map (the newest); a missing one is pushed again
MAP_NAME = re.compile(r"^[A-Za-z0-9]{1,64}$")
HEIGHT_DIGEST = re.compile(r"^[0-9a-f]{12}$")
```

(`import re` and `import base64` at the top if they aren't there.)

`Settings` gains, after `control_max_bytes`:

```python
    # Where control children keep their caches (CONTROL_CACHE_DIR, as app/control/geometry.py reads it); pushed
    # height assets live in its `heights` folder.
    control_cache_dir: Path = field(default_factory=lambda: WEBAPP / ".control_cache")
```

and in `from_env`: `if env.get("CONTROL_CACHE_DIR"): settings.control_cache_dir = Path(env["CONTROL_CACHE_DIR"])`.

Add after `_child_setup`:

```python
def height_path(settings: Settings, map_name: str, digest: str) -> Path:
    """app/control/geometry.py `height_cache_path`, without importing it (this process has no numpy)."""
    return settings.control_cache_dir / "heights" / f"{map_name}.{digest}.height.npz"


def store_height(settings: Settings, map_name: str, digest: str, data: bytes) -> None:
    """Writes a pushed asset whole (a temp file, then a rename), and keeps the map's newest HEIGHTS_KEPT."""
    path = height_path(settings, map_name, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    partial.write_bytes(data)
    os.replace(partial, path)
    mine = sorted(path.parent.glob(f"{map_name}.*.height.npz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in mine[HEIGHTS_KEPT:]:
        try:
            old.unlink()
        except OSError:
            pass
```

`ControlJob` gains a field, with a default, at the end of the dataclass:

```python
    warm_key: str = ""           # what "warm" is tracked by: the map and its heights (their visibility cache)
```

In `ControlRunner.submit`, build the job with it:

```python
            job = ControlJob(uuid.uuid4().hex, key, str(task["map"]), json.dumps(task).encode("utf-8"),
                             warm_key=f"{task['map']}:{task.get('height') or ''}")
```

In `_next`, warmth goes by `warm_key` (which rounds may share a map is unchanged):

```python
        busy_maps = {self.jobs[j].map for j in self.running}
        warming_maps = {self.jobs[j].map for j, warming in self.running.items() if warming}
        for job_id in self.pending:
            job = self.jobs[job_id]
            if job.warm_key in self.warm:
                if job.map in warming_maps:
                    continue
                return job, False
            if job.map not in busy_maps:
                return job, True
        return None
```

and in `_run`'s settlement, `self.warm.add(job.warm_key)` and `self.warm.discard(job.warm_key)` replace the two lines that used `job.map`. The class docstring's "the map is warm once one of its rounds gets past loading" becomes "a map is warm, under one height digest, once one of its rounds gets past loading".

The existing tests that read `counts()["warm"]` expect map names: a task without heights now has the key `"Ascent:"`. Update those assertions (`["Ascent"]` becomes `["Ascent:"]`, and so on) where the suite shows them failing.

In the handler, `do_POST` routes the new path first:

```python
            if self.path == "/heights":
                return self._heights()
```

and beside `_control`:

```python
        def _heights(self):
            if control is None or not control.enabled:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "map control is off on this worker"})
            body = self._json(limit=control.settings.control_max_bytes)
            if body is None:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a height asset"})
            name, digest = str(body.get("map") or ""), str(body.get("digest") or "")
            try:
                data = base64.b64decode(body.get("asset") or "", validate=True)
            except (ValueError, TypeError):
                data = b""
            if not MAP_NAME.match(name) or not HEIGHT_DIGEST.match(digest) or not data:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a height asset"})
            store_height(control.settings, name, digest, data)
            return self._send(HTTPStatus.OK, {"stored": True})
```

In `_control`, after the task is parsed and before `control.submit(task)`:

```python
            digest = task.get("height")
            if digest and not (MAP_NAME.match(str(task["map"])) and HEIGHT_DIGEST.match(str(digest))
                               and height_path(control.settings, str(task["map"]), str(digest)).is_file()):
                return self._send(HTTPStatus.CONFLICT, {"error": "height asset missing", "height": digest})
```

Add both to the module docstring's endpoint list:

```
    POST /heights     body: JSON {map, digest, asset (base64 .npz)}: a map's height asset, kept in the control
                      cache for the rounds that name it. 200 {"stored": true}, 400 not one, 404 control is off.
```

and to `/control`'s line: "409 {error, height} when the task names a height digest this worker doesn't have".

- [ ] **Step 4: Run the worker tests**

Run: `PY -m pytest tests/replays/test_replay_worker_control.py tests/replays/test_control_isolation.py -q -p no:cacheprovider`
Expected: all pass but the failures recorded in the baseline, failing the same way (after the `warm` assertions are updated).

- [ ] **Step 5: Write the failing dispatcher tests**

In `webapp/tests/replays/test_control_remote.py`, give `FakeWorker` a cache. Add to its `__init__`: `self.heights, self.pushed = set(), []`, replace `submit`'s first lines and add `push_height`:

```python
    def submit(self, task):
        if task.get("height") and (task["map"], task["height"]) not in self.heights:
            raise remote.NeedsHeight(task["height"])
        if self.busy_after is not None and len(self.tasks) >= self.busy_after:
            raise remote.WorkerBusy("503")
        job_id = f"j{len(self.tasks)}"
        self.tasks[job_id] = task
        return {"id": job_id, "status": "queued"}

    def push_height(self, map_name, digest, asset):
        self.heights.add((map_name, digest))
        self.pushed.append((map_name, digest, asset))
        return {"stored": True}
```

and make `ok` answer with the geometry the task was computed with:

```python
    def ok(self, task):
        geometry = dict(rc.geometry_inputs(task["map"]))
        if task.get("height"):
            geometry["height"] = task["height"]
        return {"status": "done", "result": {"status": "ok", "data": base64.b64encode(gzip.compress(b"d")).decode(),
                                             "summary": base64.b64encode(cf.pack_summary({})).decode(),
                                             "revision": cf.CONTROL_REVISION, "data_version": cf.DATA_VERSION,
                                             "geometry": geometry}}
```

Append:

```python
from app.services import control_heights as ch  # noqa: E402

from app.replays import height_inputs as hi  # noqa: E402

RULES = {"version": cf.HEIGHT_VERSION, "revision": cf.HEIGHT_RULES_REVISION, "constants": "c"}


def good_report() -> dict:
    """A report that is whole and passes the gate (app/services/control_heights.py `gate`)."""
    return {"walkable_cells": 10, "supported_cells": 7, "supported": 0.7, "ready": True, "not_ready": [],
            "kill_lines": {"qualifying": 400, "blocked": 2, "share": 0.005, "passes": True},
            "must_block": {"set": "k" * 12, "lines": 0, "checked": 0, "unchecked": 0, "blocked": 0, "passes": True}}


def heights(db, name, digest, asset, *uuids):
    manifest = hi.manifest(name, [[u, "p.c11", u * 8, 20] for u in uuids], {"sight": "s", "walk": "w", "scale": 1}, "k" * 12)
    assert ch.store_build(db, map_name=name, digest=digest, asset=asset, report=good_report(), inputs=manifest,
                          rules=dict(RULES)) == ("active", [])


def test_a_map_with_heights_in_the_database_sends_their_digest_and_pushes_the_asset_once(factory, db, linked):
    heights(db, linked.map_name, "a" * 12, b"npz", "m1", "m2")
    worker, state = FakeWorker(), remote.State()
    assert remote.cycle(factory, worker, state, now=0)["sent"] == remote.IN_FLIGHT
    assert worker.pushed == [(linked.map_name, "a" * 12, b"npz")], "pushed when the worker said it was missing, once"
    assert all(t["height"] == "a" * 12 for t in worker.tasks.values())
    assert remote.cycle(factory, worker, state, now=1)["stored"] == remote.IN_FLIGHT
    stored, planned = rows(db, linked), {p.round_number: p.fingerprint for p in rc.plan(db, force=True)}
    assert len(stored) == remote.IN_FLIGHT and all(stored[n].fingerprint == planned[n] for n in stored)


def test_a_result_computed_with_other_heights_is_dropped(factory, db, linked):
    heights(db, linked.map_name, "a" * 12, b"npz", "m1", "m2")
    worker, state = FakeWorker(), remote.State()
    remote.cycle(factory, worker, state, now=0)
    heights(db, linked.map_name, "b" * 12, b"npz2", "m1", "m2", "m3")           # new heights while they ran
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["dropped_geometry"] == remote.IN_FLIGHT and counts["stored"] == 0
    assert rows(db, linked) == {}
```

Run: `PY -m pytest tests/replays/test_control_remote.py -q -p no:cacheprovider -k "heights"`
Expected: FAIL, `AttributeError: module 'app.services.replay_control_remote' has no attribute 'NeedsHeight'`.

- [ ] **Step 6: The dispatcher**

In `webapp/app/services/replay_control_remote.py`:

After `WorkerBusy`:

```python
class Conflict(Exception):
    """The worker answered 409: the request is understood and can't be done as things stand. `body` is what it
    said; what that means is the caller's to decide, endpoint by endpoint."""

    def __init__(self, body: dict):
        super().__init__(str(body.get("error") or "conflict"))
        self.body = body


class Rejected(Exception):
    """The worker refused the request itself (400, 413): sending it again unchanged can't work."""

    def __init__(self, code: int, body: dict):
        super().__init__(f"{code}: {body.get('error') or 'refused'}")
        self.code, self.body = code, body


class NeedsHeight(Exception):
    """The worker doesn't have the height asset a control round names: push it and ask again."""

    def __init__(self, digest: str):
        super().__init__(digest)
        self.digest = digest
```

`ControlClient` gains `push_height`; `_call` raises `Conflict` and `Rejected` with the worker's body; and `submit`, the one caller for which a conflict can mean a missing height, says so:

```python
    def push_height(self, map_name: str, digest: str, asset: bytes) -> dict:
        body = json.dumps({"map": map_name, "digest": digest, "asset": base64.b64encode(asset).decode("ascii")})
        return self._call(urllib.request.Request(f"{self.base}/heights", data=body.encode("utf-8"), method="POST",
                                                 headers={"Content-Type": "application/json"}))
```

```python
            if error.code in (400, 409, 413):
                try:
                    said = json.loads(error.read() or b"{}")
                except ValueError:
                    said = {}
                said = said if isinstance(said, dict) else {}
                if error.code == 409:
                    raise Conflict(said) from error
                raise Rejected(error.code, said) from error
```

(put it before the `404` test in the `HTTPError` branch.) `submit` becomes:

```python
    def submit(self, task: dict) -> dict:
        body = json.dumps(task).encode("utf-8")
        try:
            return self._call(urllib.request.Request(f"{self.base}/control", data=body, method="POST",
                                                     headers={"Content-Type": "application/json"}))
        except Conflict as conflict:
            if conflict.body.get("height"):
                raise NeedsHeight(str(conflict.body["height"])) from conflict
            raise Unreachable(f"the worker refused the round: {conflict}") from conflict
        except Rejected as refused:               # as before this change: a refused round is tried again later
            raise Unreachable(f"the worker refused the round: {refused}") from refused
```

`job` (a GET) can't meet either. Add to `test_the_client_maps_the_workers_answers` in `test_control_remote.py`, in the shape of its other cases (it patches `urlopen` to raise an `HTTPError` of a given code): a 409 whose body is `{"error": "height asset missing", "height": "a" * 12}` raises `NeedsHeight` from `submit` and `Conflict` from `_call`; a 409 without `height` raises `Unreachable` from `submit`; a 413 raises `Rejected` from `_call` and `Unreachable` from `submit`.

In `_submit`, the task names the database's digest, and a missing asset is pushed once. After the `features` lines, replace the `try: answer = client.submit(task)` block with:

```python
        digest = heights.get(p.map_name)        # only the database's: a committed asset is in the worker's image
        if digest:
            task["height"] = digest
        try:
            try:
                answer = client.submit(task)
            except NeedsHeight:
                asset = control_heights.asset_bytes(session, p.map_name, digest) if digest else None
                if asset is None:
                    counts["unreachable"] += 1
                    state.last_found = True
                    break
                client.push_height(p.map_name, digest, asset)
                counts["pushed"] += 1
                answer = client.submit(task)
        except WorkerBusy:
            counts["busy"] += 1
            state.last_found = True
            break
        except (WorkerGone, Unreachable, NeedsHeight):
            counts["unreachable"] += 1
            state.last_found = True
            break
```

and add `"pushed"` to the keys `cycle` starts `counts` with. In the module docstring's "Submit" step add: "A round of a map whose heights are in the database names their digest; a worker that doesn't have the asset says so, gets it pushed and is asked again."

- [ ] **Step 7: Run the tests**

Run: `PY -m pytest tests/replays/test_control_remote.py tests/replays/test_control_heights_db.py -q -p no:cacheprovider`
Expected: all pass (the `pg` test is skipped without `-k "not pg"` only if no Postgres is configured; it passes with the local one up).

- [ ] **Step 8: Commit**

```bash
git add replay_worker/server.py webapp/app/services/replay_control_remote.py webapp/tests/replays/test_replay_worker_control.py webapp/tests/replays/test_control_remote.py
git commit -F <message file>
```

Message: `Worker: keeps pushed height assets; a round names its heights and a map is warm per digest`.

---

### Task 5: The build as one function and one child, with checks that fail closed

**Files:**
- Create: `webapp/app/control/height_job.py`
- Modify: `webapp/scripts/build_control_heights.py` (uses it)
- Create: `replay_worker/height_job.py`
- Test: `webapp/tests/replays/test_control_height_job.py` (new); `webapp/tests/replays/test_control_heights.py` and `test_compare_height_builds.py` (what they patch)

**Interfaces:**
- Consumes: `height_build.build`, `kill_line_check`, `must_block_check` (each reads `rounds` in one pass); `geometry.attach_heights`; `heights.save_asset`, `load_asset`, `rules`; Task 2's `height_inputs` (`must_block_set`, `CheckSetError`, `geometry_identity`, `manifest`, `digest`, `rounds_expected`).
- Produces:
  - `height_job.BlobDir(directory, map_name)`: re-iterable, one decoded round in memory at a time; `len()` is its files; after a pass `.skipped` counts other maps' rounds, `.read` lists this map's round files and `.per_match()` gives `{match: rounds of this map}`.
  - `height_job.run_checks(map_name, flat_geo, build, rounds, must_block=None) -> dict` (`{"kill_lines", "must_block"}`, also stored in `build.report`). The must-block result always carries `"set"` (the check file's hash, or None) and, when the file can't be read, `"passes": False` with an `"error"`.
  - `height_job.compare(new, old) -> dict | None`; `height_job.features_pending(map_name, geo, asset_dir=None) -> list[dict]` (Task 8 fills it; here it returns `[]`).
  - `height_job.run(map_name, rounds, *, previous: Path | None = None, asset_dir=None, must_block=None) -> dict`: `{"status": "ok", "map", "digest", "asset" (bytes), "report", "rules", "seconds", "peak"}`.
  - Child: `python -m replay_worker.height_job < {"key", "map", "dir", "manifest", "previous"?} > result`, with `asset` in base64 and `"inputs_sha"`, `"rounds"` added. A manifest this worker would not have computed itself, or a spool that isn't the manifest's rounds, is `{"status": "failed", "error_kind": "inputs"}`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_control_height_job.py`:

```python
"""The height build as a job (app/control/height_job.py, replay_worker/height_job.py;
docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 2): rounds read one at a time from a
folder, the two checks (which fail closed), the comparison with the asset before, the child's JSON, and the
web app's verifier on what it returns."""

import base64
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from test_control_heights import covered_rounds, level_walk, toy_assets, write_blobs  # noqa: E402

from app.control import geometry as cg  # noqa: E402
from app.control import height_job, height_verify  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from app.replays import height_inputs as hi  # noqa: E402
from app.services import control_heights as ch  # noqa: E402


@pytest.fixture
def checks(tmp_path):
    """A readable check set with no line for the toy map: a real answer, "nothing to check here"."""
    path = tmp_path / "must_block.json"
    path.write_text(json.dumps({"lines": [{"map": "Bind", "viewer": [1, 1, 0], "target": [2, 2, 0], "source": "x"}]}),
                    encoding="utf-8")
    return path


def test_rounds_are_read_one_at_a_time_and_twice(tmp_path):
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    write_blobs(tmp_path / "blobs", [("match-9", 1, covered_rounds()[0][2])], name="Bind")
    source = height_job.BlobDir(blobs, "Ascent")
    first = [(m, n) for m, n, _ in source]
    assert first == [(f"match-{m}", n) for m in (0, 1) for n in (1, 2, 3)] and source.skipped == 1
    assert source.per_match() == {"match-0": 3, "match-1": 3}
    assert [(m, n) for m, n, _ in source] == first, "the checks read it again after the build"
    assert len(source) == 7 and all(blob["map"] == "Ascent" for _, _, blob in source)


def test_a_build_runs_from_a_folder_and_its_result_is_what_the_web_app_verifies(tmp_path, checks):
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    out = height_job.run("Ascent", height_job.BlobDir(blobs, "Ascent"), asset_dir=assets, must_block=checks)
    assert out["status"] == "ok" and out["map"] == "Ascent" and len(out["digest"]) == 12 and out["rules"] == hc.rules()
    report = out["report"]
    assert report["ready"] and report["kill_lines"]["passes"] and report["kill_lines"]["qualifying"] == 6
    assert report["must_block"] == {"set": hi.must_block_sha(checks), "lines": 0, "checked": 0, "unchecked": 0,
                                    "blocked": 0, "passes": True, "results": []}
    assert report["rounds"] == 6 and report["matches"] == 2 and report["compare"] is None and report["features"] == []
    json.dumps(report)                                   # it is stored as JSON
    seen = height_verify.describe(out["asset"], "Ascent", asset_dir=assets, must_block=checks)
    assert "error" not in seen
    assert (seen["digest"], seen["version"]) == (out["digest"], cf.HEIGHT_VERSION)
    assert (seen["walkable_cells"], seen["supported_cells"], seen["ready"], seen["not_ready"], seen["must_block"]) == (
        report["walkable_cells"], report["supported_cells"], report["ready"], report["not_ready"], report["must_block"])
    assert ch.verify_asset(out["asset"], "Ascent", asset_dir=assets, must_block=checks) == seen, "the same answer from the child process the web app starts"
    assert ch.integrity(out["digest"], report, seen, hi.must_block_sha(checks)) == []
    assert ch.gate(report) == []


def test_a_missing_or_broken_check_set_fails_the_check(tmp_path):
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    for bad in (tmp_path / "missing.json", tmp_path / "broken.json"):
        if bad.name == "broken.json":
            bad.write_text("not json", encoding="utf-8")
        out = height_job.run("Ascent", height_job.BlobDir(blobs, "Ascent"), asset_dir=assets, must_block=bad)
        must = out["report"]["must_block"]
        assert must["passes"] is False and must["set"] is None and "error" in must and must["lines"] == 0
        assert ch.gate(out["report"]) and ch.integrity(out["digest"], out["report"], height_verify.describe(out["asset"], "Ascent", asset_dir=assets, must_block=bad),
                                                       None), "neither half of the gate lets it through"


def test_corrupt_or_foreign_bytes_are_never_an_asset(tmp_path, checks):
    assets = toy_assets(tmp_path)
    out = height_job.run("Ascent", height_job.BlobDir(write_blobs(tmp_path / "blobs", covered_rounds()), "Ascent"),
                         asset_dir=assets, must_block=checks)
    sha = hi.must_block_sha(checks)
    for data in (b"", b"not a zip", out["asset"][:200], out["asset"][::-1]):
        seen = ch.verify_asset(data, "Ascent", asset_dir=assets, must_block=checks)
        assert "error" in seen and ch.integrity(out["digest"], out["report"], seen, sha)
    other = height_job.run("Ascent", height_job.BlobDir(
        write_blobs(tmp_path / "b2", covered_rounds({9: level_walk(320, 328, 6.0, side="B")})), "Ascent"),
        asset_dir=assets, must_block=checks)
    assert other["digest"] != out["digest"]
    swapped = ch.integrity(out["digest"], out["report"], ch.verify_asset(other["asset"], "Ascent", asset_dir=assets, must_block=checks), sha)
    assert any("is not the asset" in r for r in swapped), "a real asset, but not the one the result names"


def _raw_parts(data):
    import io
    import numpy as np

    with np.load(io.BytesIO(data), allow_pickle=False) as raw:
        return {k: raw[k].copy() for k in raw.files}


def _npz(parts):
    import io
    import numpy as np

    out = io.BytesIO()
    np.savez_compressed(out, **parts)
    return out.getvalue()


@pytest.mark.parametrize("damage", ["spread shape", "edge shape", "cell index", "floor index", "missing floor",
                                   "not neighbours", "edge kind", "ground kind", "floor gap", "floor sentinel",
                                   "lossy cast", "support on void"])
def test_structurally_invalid_assets_are_refused_before_the_engine_can_read_them(tmp_path, checks, damage):
    import numpy as np

    assets = toy_assets(tmp_path)
    out = height_job.run("Ascent", height_job.BlobDir(write_blobs(tmp_path / "blobs", covered_rounds()), "Ascent"),
                         asset_dir=assets, must_block=checks)
    parts = _raw_parts(out["asset"])
    a = int(np.flatnonzero(parts["floors"][..., 0].ravel() >= 0)[0])
    b = a + 1
    if damage == "spread shape":
        parts["spread"] = parts["spread"].reshape(-1)
    elif damage == "edge shape":
        parts["edges"] = np.array([a, 0, b, 0, hc.EDGE_STEP], np.int32)
    elif damage in ("cell index", "floor index", "missing floor", "not neighbours", "edge kind"):
        edge = [a, 0, b, 0, hc.EDGE_STEP]
        if damage == "cell index": edge[2] = cg.GRID ** 2 + 50
        if damage == "floor index": edge[3] = hc.MAX_FLOORS
        if damage == "missing floor": edge[3] = 1
        if damage == "not neighbours": edge[2] = a + 3
        if damage == "edge kind": edge[4] = 99
        parts["edges"] = np.array([edge], np.int32)
    elif damage == "ground kind":
        parts["kind"].flat[a] = 99
    elif damage == "floor gap":
        parts["floors"].reshape(-1, hc.MAX_FLOORS)[a] = [0, -1, 40]
    elif damage == "floor sentinel":
        parts["floors"].reshape(-1, hc.MAX_FLOORS)[a, 2] = -2
    elif damage == "lossy cast":
        parts["floors"] = parts["floors"].astype(np.int32)
    else:
        parts["supported"][0, 0] = True
    seen = height_verify.describe(_npz(parts), "Ascent", asset_dir=assets, must_block=checks)
    assert "error" in seen, (damage, seen)
    assert ch.integrity(out["digest"], out["report"], seen, hi.must_block_sha(checks))


def test_reports_cannot_invent_map_size_or_hide_an_unresolved_area(tmp_path, checks):
    import numpy as np

    assets = toy_assets(tmp_path)
    out = height_job.run("Ascent", height_job.BlobDir(write_blobs(tmp_path / "blobs", covered_rounds()), "Ascent"),
                         asset_dir=assets, must_block=checks)
    seen = height_verify.describe(out["asset"], "Ascent", asset_dir=assets, must_block=checks)
    forged = {**out["report"], "walkable_cells": seen["supported_cells"], "supported": 1.0}
    assert seen["walkable_cells"] >= seen["supported_cells"]
    # Make a self-consistent but false denominator even if every toy cell happens to be supported.
    forged["walkable_cells"] *= 2
    forged["supported"] = forged["supported_cells"] / forged["walkable_cells"]
    assert any("walkable" in r for r in ch.integrity(out["digest"], forged, seen, hi.must_block_sha(checks)))
    parts = _raw_parts(out["asset"])
    parts["floors"][24:27, 24:29] = -1  # 15 unresolved cells beside a two-floor cell
    parts["supported"][24:27, 24:29] = False
    parts["unresolved"][24:27, 24:29] = True
    parts["kind"][24:27, 24:29] = hc.KIND_NONE
    parts["floors"][24, 23, 1] = 40
    # Remove links to the cells whose floors were removed, to keep this a structurally valid asset.
    removed = set((y * cg.GRID + x) for y in range(24, 27) for x in range(24, 29))
    parts["edges"] = np.array([e for e in parts["edges"] if int(e[0]) not in removed and int(e[2]) not in removed],
                              np.int32).reshape(-1, 5)
    seen = height_verify.describe(_npz(parts), "Ascent", asset_dir=assets, must_block=checks)
    assert "error" not in seen and seen["supported"] >= cf.HEIGHT_SUPPORTED_MIN
    assert not seen["ready"] and any("unresolved" in r for r in seen["not_ready"])
    forged = {**out["report"], "supported_cells": seen["supported_cells"], "supported": seen["supported"],
              "ready": True, "not_ready": []}
    assert any("readiness" in r for r in ch.integrity(seen["digest"], forged, seen, hi.must_block_sha(checks)))
    honest = {**forged, "ready": seen["ready"], "not_ready": seen["not_ready"]}
    assert ch.integrity(seen["digest"], honest, seen, hi.must_block_sha(checks)) == []
    assert ch.gate(honest)  # valid failing build is rejected by policy, not treated as corrupt


def test_empty_must_block_evidence_cannot_pass_a_nonempty_maps_set(tmp_path, checks):
    assets = toy_assets(tmp_path)
    out = height_job.run("Ascent", height_job.BlobDir(write_blobs(tmp_path / "blobs", covered_rounds()), "Ascent"),
                         asset_dir=assets, must_block=checks)
    checks.write_text(json.dumps({"lines": [{"map": "Ascent", "viewer": [100, 100, None],
                                             "target": [120, 100, None], "source": "needs heights"}]}), encoding="utf-8")
    sha = hi.must_block_sha(checks)
    seen = height_verify.describe(out["asset"], "Ascent", asset_dir=assets, must_block=checks)
    assert "error" not in seen and seen["must_block"]["lines"] == 1 and not seen["must_block"]["passes"]
    forged = {**out["report"], "must_block": {"set": sha, "lines": 0, "checked": 0, "unchecked": 0,
                                            "blocked": 0, "passes": True}}
    assert any("must-block" in r for r in ch.integrity(out["digest"], forged, seen, sha))


def test_the_report_compares_with_the_asset_before(tmp_path, checks):
    assets = toy_assets(tmp_path)
    before = height_job.run("Ascent", height_job.BlobDir(write_blobs(tmp_path / "a", covered_rounds()), "Ascent"),
                            asset_dir=assets, must_block=checks)
    (tmp_path / "before.npz").write_bytes(before["asset"])
    raised = covered_rounds({9: level_walk(320, 328, 6.0, side="B")})        # a platform 6 m up, two cells
    after = height_job.run("Ascent", height_job.BlobDir(write_blobs(tmp_path / "b", raised), "Ascent"),
                           previous=tmp_path / "before.npz", asset_dir=assets, must_block=checks)
    assert after["report"]["compare"] == {"previous": before["digest"], "cells_gained": 0, "cells_lost": 0,
                                          "cells_moved": 0}, "the ground didn't move: the platform is an upper floor"
    old = hc.load_asset(tmp_path / "before.npz")
    new = hc.load_asset(tmp_path / "before.npz")
    new.floors[20, 20, 0] += 6                    # the ground 0.6 m higher in one cell
    new.floors[21, 21, 0] = -1                    # one cell lost
    assert height_job.compare(new, old) == {"previous": old.digest, "cells_gained": 0, "cells_lost": 1, "cells_moved": 1}
    assert height_job.compare(new, None) is None


def child_task(tmp_path, blobs, assets, checks, monkeypatch, replays=None):
    """A task as the worker's server writes it, with the manifest the web app would send for these blobs."""
    monkeypatch.setattr(hi, "MUST_BLOCK", checks)
    monkeypatch.setattr(hi, "CONTROL_DIR", assets)
    monkeypatch.setattr(cg, "ASSET_DIR", assets)
    geometry = hi.geometry_identity("Ascent")
    manifest = hi.manifest("Ascent", replays or [["match-0", "p.c11", "a" * 64, 3], ["match-1", "p.c11", "b" * 64, 3]],
                           geometry, hi.must_block_sha())
    return {"key": hi.key(manifest), "map": "Ascent", "dir": str(blobs), "manifest": manifest}


def test_the_child_answers_in_json_and_proves_what_it_was_built_from(tmp_path, checks, monkeypatch):
    from replay_worker import height_job as child

    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    task = child_task(tmp_path, blobs, assets, checks, monkeypatch)
    out = child.run(task)
    assert out["status"] == "ok" and out["key"] == task["key"] and out["map"] == "Ascent" and out["rounds"] == 6
    assert out["inputs_sha"] == hi.digest(task["manifest"])
    json.dumps(out)
    assert height_verify.describe(base64.b64decode(out["asset"]), "Ascent", asset_dir=assets, must_block=checks)["digest"] == out["digest"]


@pytest.mark.parametrize("name, spoil", [
    ("a match with a round missing", lambda t, blobs: (blobs / "match-1" / "3.json.gz").unlink()),
    ("a match the manifest doesn't name", lambda t, blobs: (blobs / "match-1").rename(blobs / "match-7")),
    ("other rules on this worker", lambda t, blobs: t["manifest"].update(height_rules=t["manifest"]["height_rules"] + 1)),
    ("another walk mask on this worker", lambda t, blobs: t["manifest"]["geometry"].update(walk="x" * 12)),
    ("another check set on this worker", lambda t, blobs: t["manifest"].update(must_block="z" * 12)),
    ("a manifest for another map", lambda t, blobs: t["manifest"].update(map="Bind")),
    ("no manifest", lambda t, blobs: t.pop("manifest")),
    ("no rounds folder", lambda t, blobs: t.update(dir=str(blobs / "nowhere"))),
])
def test_the_child_refuses_inputs_that_are_not_the_manifests(tmp_path, checks, monkeypatch, name, spoil):
    from replay_worker import height_job as child

    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    task = child_task(tmp_path, blobs, assets, checks, monkeypatch)
    spoil(task, blobs)
    out = child.run(task)
    assert out["status"] == "failed" and out["error_kind"] == "inputs" and "asset" not in out, name
```

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_height_job.py -q -p no:cacheprovider`
Expected: FAIL at import, `cannot import name 'height_job' from 'app.control'`.

- [ ] **Step 3: The job module, and the script on top of it**

Create `webapp/app/control/height_job.py`:

```python
"""A map's height build as one job (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 2):
what scripts/build_control_heights.py runs at a desk and what the replay worker's child
(replay_worker/height_job.py) runs with nobody watching.

`run` builds the map from its rounds (app/control/height_build.py), runs the kill-line and must-block checks
on the new heights, compares them with the asset before, and returns the asset's bytes with its report. It
never decides whether the asset goes live: the web app verifies the result and applies the gate
(app/services/control_heights.py).

**The checks fail closed.** The must-block check needs its check set (app/replays/height_inputs.py): a file
that is missing or unreadable fails the check and says why. A readable set with no line for this map passes,
with `lines: 0`. The result always names the set it was run against (`set`), so the web app can tell a check
run against another deploy's list.

Rounds are read one at a time and more than once (the build, then the kill lines), so they come as something
re-iterable: a list, or a `BlobDir` over a folder of stored blobs, which holds one decoded round in memory at
a time. A map's rounds decoded all at once don't fit the worker's memory.

Local tooling, like the engine: the web app never imports this module.
"""

from __future__ import annotations

import copy
import json
import tempfile
import time
from collections import Counter
from pathlib import Path

import numpy as np

from app.control import geometry as cg
from app.control import height_build as hb
from app.control import heights as hc
from app.control.task import peak_memory
from app.replays import format as fmt
from app.replays import height_inputs

MOVED_M = 0.5                                       # a cell whose ground moved more than this is reported


class BlobDir:
    """A map's rounds in `<directory>/<match>/<n>.json.gz`, in match then round order, decoded as they are read."""

    def __init__(self, directory: Path, map_name: str):
        self.map_name = map_name
        self.paths = sorted(Path(directory).glob("*/*.json.gz"),
                            key=lambda p: (p.parent.name, int(p.name.split(".")[0])))
        self.skipped = 0
        self.read: list[Path] = []          # the files of this map's rounds, from the last pass

    def __len__(self) -> int:
        return len(self.paths)

    def __iter__(self):
        self.skipped, self.read = 0, []
        for path in self.paths:
            blob = fmt.decode_blob(path.read_bytes())
            if blob.get("map") != self.map_name:
                self.skipped += 1
                continue
            self.read.append(path)
            yield path.parent.name, int(path.name.split(".")[0]), blob

    def per_match(self) -> dict:
        """{match: how many of its rounds were this map's}, from the last pass over the folder."""
        return dict(Counter(path.parent.name for path in self.read))


def run_checks(map_name: str, flat_geo, build: hb.HeightBuild, rounds, must_block: Path | None = None) -> dict:
    """The kill-line and must-block checks on the new heights, stored in the build's report."""
    geo = cg.attach_heights(copy.copy(flat_geo), build.asset)
    kills = hb.kill_line_check(rounds, geo)
    try:
        lines, sha = height_inputs.must_block_set(must_block)
        must = {"set": sha, **hb.must_block_check(lines, geo, map_name)}
    except height_inputs.CheckSetError as error:
        must = {"set": None, "lines": 0, "checked": 0, "unchecked": 0, "blocked": 0, "passes": False, "results": [],
                "error": str(error)}
    build.report["kill_lines"], build.report["must_block"] = kills, must
    return {"kill_lines": kills, "must_block": must}


def compare(new: hc.HeightAsset, old: hc.HeightAsset | None) -> dict | None:
    """The new ground against the asset before, in world heights: cells that gained a height, lost theirs, or
    moved by more than MOVED_M. For looking at afterwards; never a gate."""
    if old is None:
        return None
    had, has = old.floors[..., 0] >= 0, new.floors[..., 0] >= 0
    both = had & has
    shift = (new.floors[..., 0].astype(int) + new.origin_z) - (old.floors[..., 0].astype(int) + old.origin_z)
    return {"previous": old.digest, "cells_gained": int((has & ~had).sum()), "cells_lost": int((had & ~has).sum()),
            "cells_moved": int((both & (np.abs(shift) > MOVED_M * 10)).sum())}


def features_pending(map_name: str, geo, asset_dir: Path | None = None) -> list[dict]:
    """The map's tagged features that no longer fit the new heights (section 5). None are read yet."""
    return []


def run(map_name: str, rounds, *, previous: Path | None = None, asset_dir: Path | None = None,
        must_block: Path | None = None) -> dict:
    """Builds `map_name` from `rounds` (re-iterable [(match, n, blob)]) and checks it. `previous` is the asset
    file it replaces, for the comparison; `must_block` another check file than the committed one (tests)."""
    started = time.time()
    flat_geo = cg.load_geometry(map_name, asset_dir or cg.ASSET_DIR)
    build = hb.build(rounds, flat_geo)
    run_checks(map_name, flat_geo, build, rounds, must_block)
    old = None
    if previous is not None and Path(previous).is_file():
        try:
            old = hc.load_asset(Path(previous))
        except Exception:  # noqa: BLE001 - an asset of another format: nothing to compare with
            old = None
    build.report["compare"] = compare(build.asset, old)
    build.report["features"] = features_pending(map_name, cg.attach_heights(copy.copy(flat_geo), build.asset),
                                                asset_dir)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"{map_name}.height.npz"
        hc.save_asset(path, build.asset)
        data = path.read_bytes()
    return {"status": "ok", "map": map_name, "digest": build.asset.digest, "asset": data,
            "report": json.loads(json.dumps(build.report, default=str)), "rules": hc.rules(),
            "seconds": round(time.time() - started, 1), "peak": peak_memory()}
```

`hb.must_block_check` already returns `lines, checked, blocked, passes, unchecked, results`; for a set with no
line for the map it gives zeros and `passes: True`, which is the "valid check set, nothing for this map" case.

In `webapp/scripts/build_control_heights.py`:

- delete the `MUST_BLOCK = ...` line and import the job: `from app.control import height_job  # noqa: E402` (keep the `height_inputs` import);
- `blob_rounds` returns a re-iterable source instead of a list of decoded blobs. Other maps' rounds are only known once the folder has been read, so they are reported after the build:

```python
def blob_rounds(directory: Path, map_name: str) -> tuple[height_job.BlobDir, dict]:
    """The map's rounds from `<directory>/<match>/<n>.json.gz`, read one at a time. What it skipped (other
    maps' rounds) is on the source once it has been read (`.skipped`)."""
    return height_job.BlobDir(directory, map_name), {}
```

  In `main`, after the `built in` line add:

```python
    if getattr(rounds, "skipped", 0):
        print(f"  {rounds.skipped} round(s) of other maps skipped", flush=True)
```

  The slopes plan's `build_inputs` iterates `rounds` to hash each round's file, which would decode every blob once more. Move the `inputs = build_inputs(args, geo, rounds)` call (and its `REFUSED` branch) to just after `build = hb.build(rounds, geo)`, and in `build_inputs` hash the files the build read:

```python
    rows = [[path.parent.name, int(path.name.split(".")[0]), hashlib.sha256(path.read_bytes()).hexdigest()]
            for path in rounds.read]
```

  (`rounds.read` is the list of this map's round files from the pass `hb.build` just made.) The slopes plan's tests of `build_inputs` pass `[(match, n, None)]` lists: give them a small stand-in with a `read` list of those paths, or keep the old branch for a plain list (`if not hasattr(rounds, "read")`).

- `run_checks` keeps its printing and takes its numbers from the job, with a module-level `MUST_BLOCK = None` that tests may point at another file:

```python
MUST_BLOCK = None      # another check file than app/static/data/control/must_block.json (tests)


def run_checks(map_name: str, flat_geo, build: hb.HeightBuild, rounds: list) -> dict:
    """The kill-line and must-block checks on the new heights; printed, and stored in the report."""
    checks = height_job.run_checks(map_name, flat_geo, build, rounds, MUST_BLOCK)
    kills, must = checks["kill_lines"], checks["must_block"]
```

  (delete the lines that computed `geo`, `kills`, `lines` and `must` and the final `build.report[...] = ...` assignment; keep every `print`, replacing `MUST_BLOCK.name` with `height_inputs.MUST_BLOCK.name`; after the must-block line print `must["error"]` when there is one; `return checks`.)

- the refusal below it already refuses a map whose `must_block` check doesn't pass, so a missing check file now refuses a local, non-preview build too. That is intended: say so in the module docstring ("a check file that can't be read fails the must-block check").

In `webapp/tests/replays/test_control_heights.py` and `test_compare_height_builds.py`: the fixtures that set `command.MUST_BLOCK` to a path that doesn't exist now make the must-block check **fail**. Point them at a readable empty set instead:

```python
    empty = tmp_path / "no-lines.json"
    empty.write_text('{"lines": []}', encoding="utf-8")
    monkeypatch.setattr(command, "MUST_BLOCK", empty)
```

`test_the_command_refuses_a_map_whose_must_block_lines_cant_be_checked_yet` keeps its own file with an unchecked line. Add one test beside it:

```python
def test_the_command_refuses_a_map_when_the_check_set_cant_be_read(tmp_path, capsys, no_picture, monkeypatch):
    monkeypatch.setattr(command, "MUST_BLOCK", tmp_path / "gone.json")
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs)], asset_dir=assets) == 2
    captured = capsys.readouterr()
    assert "missing or unreadable" in captured.out and "must_block" in captured.err
    assert not (assets / "Ascent.height.npz").exists()
```

- [ ] **Step 4: The worker's child**

Create `replay_worker/height_job.py`:

```python
"""One map's height build, run as a child process by the replay worker
(docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 2).

    <control venv python> -m replay_worker.height_job  < task.json  > result.json

The task (stdin): `{"key", "map", "dir", "manifest", "previous"?}`. `dir` holds the map's stored rounds as the
server spooled them, `<match>/<n>.json.gz`; `manifest` is the web app's input manifest for this build
(app/replays/height_inputs.py); `previous` is the digest of the asset being replaced, read from this worker's
control cache when it is there (for the report's comparison only).

Before it builds, the child recomputes the manifest from its own files (its rule revisions, its masks, its
check set) with the replays the web app named, and refuses when the digest differs: the two services deploy
separately, and a build under other rules or masks is not the build that was asked for. After it builds, the
rounds it read must be the manifest's, match by match. Either refusal is `error_kind: "inputs"`.

The result (stdout): app/control/height_job.py's, with the asset's bytes in base64, `inputs_sha` (the
manifest's digest, as this worker computed it) and `rounds`. Like control_job.py, the server never imports
this module or the engine, and every app import is inside `run`.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"


def run(task: dict) -> dict:
    if str(WEBAPP) not in sys.path:
        sys.path.insert(0, str(WEBAPP))
    try:
        from app.control import geometry, height_job
        from app.replays import height_inputs as hi

        theirs = task.get("manifest")
        name = str(task.get("map"))
        if not isinstance(theirs, dict) or theirs.get("map") != name or not isinstance(theirs.get("replays"), list):
            return {"status": "failed", "error_kind": "inputs", "error": "the task has no manifest for this map",
                    "key": task.get("key")}
        mine = hi.manifest(name, theirs["replays"], hi.geometry_identity(name) or {}, hi.must_block_sha())
        if hi.digest(mine) != hi.digest(theirs):
            differ = sorted(k for k in mine if mine[k] != theirs.get(k))
            return {"status": "failed", "error_kind": "inputs", "key": task.get("key"),
                    "error": f"this worker's build inputs are not the web app's: {', '.join(differ)} differ"}
        folder = Path(task["dir"])
        if not folder.is_dir():
            return {"status": "failed", "error_kind": "inputs", "error": f"no rounds at {folder}",
                    "key": task.get("key")}
        source = height_job.BlobDir(folder, name)
        previous = geometry.height_cache_path(name, task["previous"]) if task.get("previous") else None
        result = height_job.run(name, source, previous=previous)
        expected = {row[0]: int(row[3]) for row in theirs["replays"]}
        if source.per_match() != expected:
            return {"status": "failed", "error_kind": "inputs", "key": task.get("key"),
                    "error": f"the rounds read ({source.per_match()}) are not the manifest's ({expected})"}
        result["asset"] = base64.b64encode(result["asset"]).decode("ascii")
        result["rounds"] = sum(expected.values())
        result["inputs_sha"] = hi.digest(mine)
    except Exception as error:  # noqa: BLE001 - the machine's: memory, a broken blob, an import
        result = {"status": "failed", "error_kind": "infra", "error": f"{type(error).__name__}: {error}"[:1500]}
    result["key"] = task.get("key")
    return result


def main() -> int:
    task = json.loads(sys.stdin.buffer.read())
    sys.stdout.write(json.dumps(run(task)))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests**

Run: `PY -m pytest tests/replays/test_control_height_job.py tests/replays/test_control_heights.py tests/replays/test_compare_height_builds.py tests/replays/test_control_isolation.py -q -p no:cacheprovider`
Expected: all pass. (`test_the_worker_copied_code_stays_stdlib_only` reads every import in `replay_worker/*.py`, inner ones too, and only objects to numpy, scipy and PIL by name: `height_job.py` imports `app.*` modules only, like `control_job.py`.)

- [ ] **Step 6: Repeat the cost gate with the worker's input reader**

In `measure_height_rebuild.py`, import `BlobDir` from `app.control.height_job`, replace
`rounds = FrozenRounds(...)` with `rounds = BlobDir(...)`, and time only `rounds.read` (the selected paths from
the last pass), not all `rounds.paths`. Remove `FrozenRounds`; update the Task 1 test to construct `BlobDir`,
iterate it twice, and assert `rounds.read` contains only the requested map's paths. Add `len(source.read) == 6`
to Task 5's mixed-map iterator test. Run the probe in a fresh process on the same frozen maps as Task 1.

The largest-round and total-size limits stay the same. The authoritative build peak must be under 1.5 GB with
this actual streaming reader and both checks. Record it separately from Task 1's provisional measurement;
stop if it fails. Re-run the cost-probe and height-job tests after this replacement.

- [ ] **Step 7: Commit**

```bash
git add webapp/scripts/measure_height_rebuild.py webapp/app/control/height_job.py webapp/scripts/build_control_heights.py replay_worker/height_job.py webapp/tests/replays
git commit -F <message file>
```

Message: `Heights: the build as one job, reading a round at a time, with checks that fail closed and a result that names its inputs`.

---

### Task 6: The worker runs a build

A build on the worker is always in one of five states, and every request says which, so the web app can pick up
wherever it was after a restart on either side:

| State | What the web app may do | What the worker answers |
| --- | --- | --- |
| `collecting` | send rounds, start, cancel | rounds are acknowledged; the same round with the same bytes is acknowledged again for free; the same round with other bytes is a conflict |
| `queued`, `running` | wait and ask | sending rounds is a conflict that says the state; starting again answers the state, no conflict |
| `done` | read the result | the result stays readable for the newest few builds |
| `failed` | open it again | opening the same key starts a fresh, empty build |
| unknown (404) | open it again | the worker restarted, or dropped a collector nobody fed |

**Files:**
- Modify: `replay_worker/server.py` (`Settings`, `ControlJob`, `ControlRunner`, new `HeightBuilds`, the handler, `main`)
- Modify: `replay_worker/Dockerfile`
- Test: `webapp/tests/replays/test_replay_worker_heights.py` (new)

**Interfaces:**
- Consumes: Task 5's child (`python -m replay_worker.height_job`); `ControlRunner`'s queue, pool and preemption.
- Produces:
  - `Settings.height_cmd: list[str]` (env `REPLAY_HEIGHT_CMD`), `height_timeout_s: float` (3600, `REPLAY_HEIGHT_TIMEOUT_S`), `height_spool_bytes: int` (512 MB, for all builds together), `height_collect_ttl_s: float` (3600).
  - `HEIGHT_COLLECTING_MAX = 2` (builds collecting at once), `HEIGHT_BUILDS_KEPT = 8` (ended builds whose answer stays readable).
  - `ControlJob.kind: str` (`"control"` or `"heights"`).
  - `ControlRunner.submit_build(key, map_name, task: dict) -> ControlJob`: queued at the front; it starts only when nothing else is running, and nothing else starts while one is queued or running.
  - `HeightBuilds(settings, control)`: `open(key, map_name, rounds, manifest) -> dict`, `add(build_id, rounds: list[dict]) -> dict`, `start(build_id, previous) -> dict`, `cancel(build_id) -> bool`, `get(build_id) -> dict | None`, `status() -> dict`, `sweep(now=None)`. Errors: `LookupError` (unknown), `HeightConflict(reason, **facts)` (wrong state, or other bytes), `OverflowError` (the spool or the collectors are full), `ValueError` (not a build, not a round).
  - HTTP:
    - `POST /heights/build` `{key, map, rounds, manifest}` -> `202` the build (`{"id", "key", "map", "status", "received", "expected"}`); `400` not a build; `503` too many collecting.
    - `POST /heights/build/{id}/rounds` `{"rounds": [{"match", "n", "blob" (base64)}]}` -> `200 {"received"}`; `409 {"error": "not collecting", "status"}` or `409 {"error": "other bytes", "match", "n"}`; `413` the spool is full; `400`; `404`.
    - `POST /heights/build/{id}/start` `{"previous"?}` -> `202` the build; `409 {"error": "rounds are missing", "received", "expected"}`; `404`.
    - `POST /heights/build/{id}/cancel` -> `200 {"cancelled": true}`; `409 {"error": "not collecting", "status"}`; `404`.
    - `GET /heights/build/{id}` -> the build, with `"result"` when done and `"error"` when failed.
    - `GET /health` gains `"idle"` and `"heights": {"collecting", "queued", "running", "spool_bytes"}`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_replay_worker_heights.py`:

```python
"""Height builds on the replay worker (replay_worker/server.py HeightBuilds;
docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, sections 2 and 3), with stub children: the
five states and what each allows, uploads that are immutable and counted once, the caps and the expiry, a build
that runs alone and ahead of control rounds, a parse preempting it, and the HTTP answers. Localhost only; no DB,
no engine."""

import base64
import json
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from replay_worker import server  # noqa: E402
from test_replay_worker_control import STUB, http, task, wait_all, wait_until  # noqa: E402

BUILD_STUB = """
import json, os, sys, time
task = json.loads(sys.stdin.buffer.read())
files = sorted(os.path.relpath(os.path.join(d, f), task["dir"]).replace(os.sep, "/")
               for d, _, fs in os.walk(task["dir"]) for f in fs)
open(os.path.join({log!r}, f"{{time.time():.6f}}_build_{{task['map']}}_{{os.getpid()}}"), "w").close()
time.sleep({sleep})
if {fail!r}:
    print("not json")
else:
    print(json.dumps({{"status": "ok", "map": task["map"], "digest": "d" * 12, "asset": "bnB6", "rounds": len(files),
                      "report": {{"files": files, "previous": task.get("previous")}}, "rules": {{"version": 2}},
                      "inputs_sha": task["manifest"]["sha"], "key": task["key"]}}))
"""
UUID = "0f452716-1e90-4782-afba-29229fdab922"
MANIFEST = {"sha": "m" * 16}


@pytest.fixture
def worker(tmp_path):
    log = tmp_path / "events"
    log.mkdir()
    control_stub = tmp_path / "stub_control.py"
    control_stub.write_text(STUB.format(log=str(log)), encoding="utf-8")

    def make(sleep=0.0, fail=False, **overrides):
        build_stub = tmp_path / f"stub_build_{sleep}_{fail}.py"
        build_stub.write_text(BUILD_STUB.format(log=str(log), sleep=sleep, fail=fail), encoding="utf-8")
        settings = server.Settings(temp_root=tmp_path, control_cmd=[sys.executable, str(control_stub)],
                                   height_cmd=[sys.executable, str(build_stub)], **overrides)
        control = server.ControlRunner(settings)
        return control, server.HeightBuilds(settings, control), log
    return make


def rounds(*numbers, match=UUID, text="blob"):
    return [{"match": match, "n": n, "blob": base64.b64encode(f"{text}{n}".encode()).decode()} for n in numbers]


def finished(builds, build_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        got = builds.get(build_id)
        if got["status"] in ("done", "failed"):
            return got
        time.sleep(0.05)
    raise AssertionError("the build didn't finish")


def spool(control) -> Path:
    return control.settings.temp_root / "height_builds"


def test_rounds_are_spooled_and_the_build_reads_them(worker):
    control, builds, _ = worker()
    job = builds.open("heights:Sunset:abc", "Sunset", 3, MANIFEST)
    assert job["status"] == "collecting" and builds.open("heights:Sunset:abc", "Sunset", 3, MANIFEST)["id"] == job["id"]
    assert builds.add(job["id"], rounds(1, 2))["received"] == 2
    with pytest.raises(server.HeightConflict) as early:
        builds.start(job["id"], None)
    assert early.value.reason == "rounds are missing" and early.value.facts == {"received": 2, "expected": 3}
    builds.add(job["id"], rounds(3))
    assert builds.start(job["id"], "a" * 12)["status"] in ("queued", "running")
    got = finished(builds, job["id"])
    assert got["status"] == "done" and got["result"]["report"]["previous"] == "a" * 12
    assert got["result"]["report"]["files"] == [f"{UUID}/{n}.json.gz" for n in (1, 2, 3)]
    assert got["result"]["inputs_sha"] == MANIFEST["sha"], "the child was given the manifest"
    builds.sweep()
    assert not any(spool(control).iterdir()), "the spool goes when the build ends; its answer stays"
    assert builds.get(job["id"])["status"] == "done"


def test_an_upload_is_immutable_and_counted_once(worker):
    control, builds, _ = worker()
    job = builds.open("k", "Sunset", 2, MANIFEST)
    assert builds.add(job["id"], rounds(1, 2))["received"] == 2
    used = builds.status()["spool_bytes"]
    for _ in range(3):                                   # the same batch again, as after a lost acknowledgement
        assert builds.add(job["id"], rounds(1, 2))["received"] == 2
    assert builds.status()["spool_bytes"] == used, "an unchanged round is acknowledged and not charged again"
    with pytest.raises(server.HeightConflict) as other:
        builds.add(job["id"], rounds(2, text="other"))
    assert other.value.reason == "other bytes" and other.value.facts == {"match": UUID, "n": 2}
    assert builds.status()["spool_bytes"] == used
    assert (spool(control) / job["id"] / UUID / "2.json.gz").read_bytes() == b"blob2", "the first bytes stay"
    with pytest.raises(ValueError):
        builds.add(job["id"], rounds(3)), "more rounds than the build was opened with"


def test_bad_rounds_are_refused_and_never_written(worker):
    control, builds, _ = worker()
    job = builds.open("k", "Sunset", 1, MANIFEST)
    for bad in ([{"match": "../x", "n": 1, "blob": "YQ=="}], [{"match": UUID, "n": "1; rm", "blob": "YQ=="}],
                [{"match": UUID, "n": 1, "blob": "***"}], [{"match": UUID}], [{"match": UUID, "n": 0, "blob": "YQ=="}]):
        with pytest.raises(ValueError):
            builds.add(job["id"], bad)
    assert builds.get(job["id"])["received"] == 0 and not list((spool(control) / job["id"]).iterdir())
    for key, name, count, manifest in (("k2", "../Sunset", 1, MANIFEST), ("k3", "Sunset", 0, MANIFEST),
                                       ("k4", "Sunset", 1, "not a manifest")):
        with pytest.raises(ValueError):
            builds.open(key, name, count, manifest)
    assert len(list(spool(control).iterdir())) == 1, "a refused open leaves no folder behind"


def test_each_state_allows_only_what_it_should(worker):
    control, builds, _ = worker(sleep=0.8)
    job = builds.open("k", "Sunset", 1, MANIFEST)
    builds.add(job["id"], rounds(1))
    builds.start(job["id"], None)
    wait_until(lambda: builds.get(job["id"])["status"] == "running")
    for attempt in (lambda: builds.add(job["id"], rounds(1)), lambda: builds.cancel(job["id"])):
        with pytest.raises(server.HeightConflict) as refused:
            attempt()
        assert refused.value.reason == "not collecting" and refused.value.facts["status"] == "running"
    assert builds.start(job["id"], None)["status"] == "running", "starting again answers the state: no conflict"
    assert builds.open("k", "Sunset", 1, MANIFEST)["status"] == "running", "and so does opening it again"
    finished(builds, job["id"])
    assert builds.open("k", "Sunset", 1, MANIFEST)["status"] == "done", "a finished build is answered again"
    with pytest.raises(LookupError):
        builds.add("nope", rounds(1))


def test_a_failed_build_is_opened_afresh(worker):
    control, builds, _ = worker(fail=True)
    failed = builds.open("k-fail", "Haven", 1, MANIFEST)
    builds.add(failed["id"], rounds(1))
    builds.start(failed["id"], None)
    assert finished(builds, failed["id"])["status"] == "failed"
    again = builds.open("k-fail", "Haven", 1, MANIFEST)
    assert again["id"] != failed["id"] and again["status"] == "collecting" and again["received"] == 0


def test_the_spool_and_the_collectors_are_bounded_for_all_builds_together(worker):
    control, builds, _ = worker(height_spool_bytes=12)
    a = builds.open("ka", "Sunset", 2, MANIFEST)
    b = builds.open("kb", "Haven", 2, MANIFEST)
    builds.add(a["id"], rounds(1))                        # 5 bytes
    builds.add(b["id"], rounds(1))                        # 5 more: 10 of 12
    with pytest.raises(OverflowError):
        builds.add(a["id"], rounds(2))
    assert builds.get(a["id"])["received"] == 1 and builds.status()["spool_bytes"] == 10
    with pytest.raises(OverflowError):
        builds.open("kc", "Lotus", 1, MANIFEST)           # HEIGHT_COLLECTING_MAX collectors already
    assert builds.cancel(a["id"]) is True and builds.get(a["id"]) is None
    assert builds.status()["spool_bytes"] == 5 and not (spool(control) / a["id"]).exists()
    assert builds.open("kc", "Lotus", 1, MANIFEST)["status"] == "collecting"
    other = builds.open("kd", "Haven", 2, MANIFEST)       # new inputs for a map still collecting: the old one goes
    assert builds.get(b["id"]) is None and other["id"] != b["id"] and builds.status()["spool_bytes"] == 0


def test_a_collector_nobody_feeds_expires_and_only_the_newest_answers_are_kept(worker):
    control, builds, _ = worker(height_collect_ttl_s=100)
    idle = builds.open("idle", "Sunset", 2, MANIFEST)
    builds.add(idle["id"], rounds(1))
    builds.sweep(now=time.time() + 50)
    assert builds.get(idle["id"])["status"] == "collecting"
    builds.sweep(now=time.time() + 101)
    assert builds.get(idle["id"]) is None and not (spool(control) / idle["id"]).exists()
    done = []
    for i in range(server.HEIGHT_BUILDS_KEPT + 2):
        job = builds.open(f"k{i}", "Sunset", 1, MANIFEST)
        builds.add(job["id"], rounds(1))
        builds.start(job["id"], None)
        finished(builds, job["id"])
        done.append(job["id"])
        builds.sweep()
    assert [builds.get(i) is not None for i in done] == [False, False] + [True] * server.HEIGHT_BUILDS_KEPT


def test_a_build_runs_alone_and_ahead_of_queued_rounds(worker):
    control, builds, log = worker(sleep=0.6)
    control.idle = lambda: False                       # nothing starts yet
    waiting = [control.submit(task(f"r:{n}:f", "Ascent", "sleep:0.3")) for n in (1, 2)]
    job = builds.open("k", "Sunset", 1, MANIFEST)
    builds.add(job["id"], rounds(1))
    builds.start(job["id"], None)
    control.idle = lambda: True
    control.wake.set()
    finished(builds, job["id"])
    wait_all(control, waiting)
    events = sorted(p.name.split("_")[:2] for p in log.iterdir())
    assert events[0][1] == "build", "the rebuild starts before any queued round"
    build_end = float(events[0][0]) + 0.6
    assert all(float(t) >= build_end - 0.05 for t, kind in events[1:] if kind == "start"), "and runs alone"


def test_a_parse_preempts_a_build_and_it_starts_again_with_its_rounds(worker):
    control, builds, log = worker(sleep=1.0)
    job = builds.open("k", "Sunset", 1, MANIFEST)
    builds.add(job["id"], rounds(1))
    builds.start(job["id"], None)
    wait_until(lambda: builds.get(job["id"])["status"] == "running")
    time.sleep(0.5)                                    # the child has started and logged; it sleeps a second
    assert control.preempt() == 1
    wait_until(lambda: builds.get(job["id"])["status"] in ("queued", "running", "done"))
    got = finished(builds, job["id"])
    assert got["status"] == "done" and control.counts()["preempted"] == 1
    assert sum(1 for p in log.iterdir() if "_build_" in p.name) == 2, "killed once, then run to the end"


def serve(control, builds):
    httpd = server.make_server(server.Worker(control.settings), control=control, heights=builds)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def test_the_http_answers_each_state_and_health(worker):
    control, builds, _ = worker(sleep=0.5)
    base = serve(control, builds)
    code, job = http(base, "/heights/build", {"key": "k", "map": "Sunset", "rounds": 2, "manifest": MANIFEST})
    assert code == 202 and job["status"] == "collecting" and (job["received"], job["expected"]) == (0, 2)
    valid = {"key": "other", "map": "Sunset", "rounds": 2, "manifest": MANIFEST}
    for missing in ("key", "map", "rounds", "manifest"):
        assert http(base, "/heights/build", {k: v for k, v in valid.items() if k != missing})[0] == 400, missing
    assert http(base, "/heights/build/unknown/start", {})[0] == 404
    path = f"/heights/build/{job['id']}"
    assert http(base, f"{path}/rounds", {"rounds": rounds(1)}) == (200, {"received": 1})
    health = http(base, "/health")[1]
    assert health["heights"]["collecting"] == ["Sunset"] and health["heights"]["spool_bytes"] == 5 and health["idle"] is True
    code, body = http(base, f"{path}/start", {})
    assert code == 409 and body == {"error": "rounds are missing", "received": 1, "expected": 2}
    assert http(base, f"{path}/rounds", {"rounds": [{"match": "x"}]})[0] == 400
    code, body = http(base, f"{path}/rounds", {"rounds": rounds(1, text="other")})
    assert code == 409 and body == {"error": "other bytes", "match": UUID, "n": 1}
    http(base, f"{path}/rounds", {"rounds": rounds(2)})
    assert http(base, f"{path}/start", {})[0] == 202
    code, body = http(base, f"{path}/rounds", {"rounds": rounds(2)})
    assert code == 409 and body["error"] == "not collecting" and body["status"] in ("queued", "running")
    assert http(base, f"{path}/start", {})[0] == 202, "asking to start a started build is not an error"
    code, again = http(base, "/heights/build", {"key": "k", "map": "Sunset", "rounds": 2, "manifest": MANIFEST})
    assert code == 202 and again["id"] == job["id"] and again["status"] in ("queued", "running")
    assert http(base, f"{path}/cancel", {})[0] == 409
    finished(builds, job["id"])
    code, got = http(base, path)
    assert code == 200 and got["status"] == "done" and got["result"]["digest"] == "d" * 12
    for missing in ("/heights/build/nope", "/heights/build/nope/start", "/heights/build/nope/rounds", "/heights/build/nope/cancel"):
        assert http(base, missing, None if missing.endswith("nope") else {"rounds": []})[0] == 404, missing
    code, job = http(base, "/heights/build", {"key": "k2", "map": "Haven", "rounds": 1, "manifest": MANIFEST})
    assert http(base, f"/heights/build/{job['id']}/cancel", {}) == (200, {"cancelled": True})
    assert http(base, f"/heights/build/{job['id']}")[0] == 404
```

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_replay_worker_heights.py -q -p no:cacheprovider`
Expected: FAIL, `TypeError: Settings.__init__() got an unexpected keyword argument 'height_cmd'`.

- [ ] **Step 3: Settings and the runner**

In `replay_worker/server.py`:

`Settings`, after `control_cache_dir`:

```python
    # Height builds (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md): one more kind of control
    # child, run alone and ahead of control rounds. Their rounds are spooled under `temp_root`.
    height_cmd: list[str] = field(default_factory=lambda: [sys.executable, "-m", "replay_worker.height_job"])
    height_timeout_s: float = 3600.0
    height_spool_bytes: int = 512 * 1024 * 1024      # every build's rounds together
    height_collect_ttl_s: float = 3600.0             # a build nobody has sent a round to for this long is dropped
```

and in `from_env`:

```python
        if env.get("REPLAY_HEIGHT_CMD"):
            settings.height_cmd = json.loads(env["REPLAY_HEIGHT_CMD"])
        settings.height_timeout_s = float(env.get("REPLAY_HEIGHT_TIMEOUT_S", settings.height_timeout_s))
```

`ControlJob` gains `kind: str = "control"` (after `warm_key`).

`ControlRunner.submit_build`, after `submit`:

```python
    def submit_build(self, key: str, map_name: str, task: dict) -> ControlJob:
        """A height build: queued ahead of every control round, deduped by its key like one."""
        with self.lock:
            existing = self.jobs.get(self.by_key.get(key, ""))
            if existing is not None and existing.status in ("queued", "running", "done"):
                return existing
            job = ControlJob(uuid.uuid4().hex, key, map_name, json.dumps(task).encode("utf-8"), kind="heights")
            self.jobs[job.id] = job
            self.by_key[key] = job.id
            self.pending.insert(0, job.id)
        self.wake.set()
        return job
```

`_next` puts builds first and alone. At its top, after the `control_workers`/`idle` test:

```python
        if any(self.jobs[j].kind == "heights" for j in self.running):
            return None                              # a build runs alone
        for job_id in self.pending:
            if self.jobs[job_id].kind == "heights":
                return (self.jobs[job_id], False) if not self.running else None    # and before any round
```

In `_run`, the command and the timeout depend on the kind:

```python
        build = job.kind == "heights"
        timeout = settings.height_timeout_s if build else \
            settings.control_warm_timeout_s if warming else settings.control_timeout_s
```

and `subprocess.Popen(settings.height_cmd if build else settings.control_cmd, **kwargs)`.

In its settlement, a build never touches warmth, and its finished job is not counted against the control jobs kept. Wrap the two warmth lines:

```python
                if job.kind == "control":
                    if status == "done" or kind == "engine":
                        self.warm.add(job.warm_key)
                    else:
                        self.warm.discard(job.warm_key)
```

and in the pruning that follows, only control jobs are pruned (`HeightBuilds.sweep` forgets a build's job when it forgets the build):

```python
                finished = sorted((j for j in self.jobs.values() if j.finished and j.kind == "control"),
                                  key=lambda j: j.finished)
```

Add after `get`:

```python
    def forget(self, job_id: str) -> None:
        """Drops a finished job and its answer (a height build's, when its build is dropped)."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is not None and job.finished:
                del self.jobs[job_id]
                if self.by_key.get(job.key) == job_id:
                    del self.by_key[job.key]
```

A preempted build is requeued at the front by the existing branch, with its task: nothing to change there.

- [ ] **Step 4: `HeightBuilds`**

Add after `ControlRunner`:

```python
MATCH_ID = re.compile(r"^[0-9a-fA-F-]{8,64}$")
HEIGHT_COLLECTING_MAX = 2        # builds collecting rounds at once (the web app sends one at a time)
HEIGHT_BUILDS_KEPT = 8           # ended builds whose answer stays readable (the newest)
ROUNDS_PER_MATCH_MAX = 99


class HeightConflict(Exception):
    """The build is not in a state that allows this, or a round arrived again with other bytes."""

    def __init__(self, reason: str, **facts):
        super().__init__(reason)
        self.reason, self.facts = reason, facts


class HeightBuilds:
    """Height builds as the web app drives them (the module docstring's endpoints): open one, send it the
    map's stored rounds in batches, start it, ask after it. The rounds are spooled to
    `<temp>/height_builds/<id>/<match>/<n>.json.gz`, which is the layout the child reads.

    - **One build per key**, and the key is the build's input manifest: opening a key again answers the build
      there is, in whatever state; only a failed one is replaced by a fresh, empty one.
    - **Uploads are immutable.** A round is kept by its sha256: the same bytes again are acknowledged and cost
      nothing; other bytes for a round already received are a conflict and change nothing.
    - **Bounded.** The spool of all builds together stays under `height_spool_bytes`; at most
      HEIGHT_COLLECTING_MAX builds collect at once; opening a build for a map drops any other still collecting
      for it (its inputs are out of date); a collector nobody feeds for `height_collect_ttl_s` is dropped; of
      the builds that ended, the newest HEIGHT_BUILDS_KEPT keep their answer.
    - **Nothing is left behind**: a build's folder goes when it ends, is cancelled, expires or is replaced, and
      whatever a restart left when the server starts."""

    def __init__(self, settings: Settings, control: ControlRunner):
        self.settings, self.control = settings, control
        self.root = settings.temp_root / "height_builds"
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        # id -> {id, key, map, expected, manifest, received {(match, n): (sha256, size)}, job, touched, ended}
        self.builds: dict[str, dict] = {}
        threading.Thread(target=self._sweep_forever, name="height-builds", daemon=True).start()

    # ---- what the web app asks for

    def open(self, key: str, map_name: str, rounds: int, manifest) -> dict:
        if not MAP_NAME.match(str(map_name)) or type(rounds) is not int or rounds < 1 or not isinstance(manifest, dict):
            raise ValueError("not a height build")
        with self.lock:
            for build in list(self.builds.values()):
                if build["key"] == key:
                    if self._state(build) != "failed":
                        return self._public(build)       # still going, or done: the same build and its answer
                    self._drop(build)                    # it failed: the web app is trying again, from nothing
            for other in [b for b in self.builds.values() if b["map"] == map_name and b["job"] is None]:
                self._drop(other)                        # other inputs for this map, never started
            if sum(1 for b in self.builds.values() if b["job"] is None) >= HEIGHT_COLLECTING_MAX:
                raise OverflowError("too many builds are collecting")
            build = {"id": uuid.uuid4().hex, "key": key, "map": map_name, "expected": rounds, "manifest": manifest,
                     "received": {}, "job": None, "touched": time.time(), "ended": None}
            (self.root / build["id"]).mkdir()
            self.builds[build["id"]] = build
            return self._public(build)

    def add(self, build_id: str, rounds: list) -> dict:
        """Keeps a batch of rounds, all or none of it."""
        with self.lock:
            build = self._collecting(build_id)
            fresh: dict[tuple, bytes] = {}
            for row in rounds:
                try:
                    match, n = str(row["match"]).lower(), int(row["n"])
                    data = base64.b64decode(row["blob"], validate=True)
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError("not a round") from error
                if not MATCH_ID.match(match) or not 1 <= n <= ROUNDS_PER_MATCH_MAX or str(row["n"]) != str(n) or not data:
                    raise ValueError("not a round")
                sha = hashlib.sha256(data).hexdigest()
                had = build["received"].get((match, n))
                if had is not None and had[0] != sha or fresh.get((match, n), data) != data:
                    raise HeightConflict("other bytes", match=match, n=n)
                if had is None:
                    fresh[(match, n)] = data
            if len(build["received"]) + len(fresh) > build["expected"]:
                raise ValueError("more rounds than the build was opened with")
            if self._spooled() + sum(len(d) for d in fresh.values()) > self.settings.height_spool_bytes:
                raise OverflowError("the spool is full")
            for (match, n), data in fresh.items():
                folder = self.root / build["id"] / match
                folder.mkdir(exist_ok=True)
                partial = folder / f"{n}.json.gz.tmp"
                partial.write_bytes(data)
                os.replace(partial, folder / f"{n}.json.gz")
                build["received"][(match, n)] = (hashlib.sha256(data).hexdigest(), len(data))
            build["touched"] = time.time()
            return {"received": len(build["received"])}

    def start(self, build_id: str, previous: str | None) -> dict:
        with self.lock:
            build = self.builds.get(build_id)
            if build is None:
                raise LookupError(build_id)
            if build["job"] is None:
                if len(build["received"]) != build["expected"]:
                    raise HeightConflict("rounds are missing", received=len(build["received"]),
                                         expected=build["expected"])
                task = {"key": build["key"], "map": build["map"], "dir": str(self.root / build["id"]),
                        "manifest": build["manifest"]}
                if previous and HEIGHT_DIGEST.match(str(previous)):
                    task["previous"] = str(previous)
                build["job"] = self.control.submit_build(build["key"], build["map"], task).id
            return self._public(build)

    def cancel(self, build_id: str) -> bool:
        with self.lock:
            self._drop(self._collecting(build_id))
            return True

    def get(self, build_id: str) -> dict | None:
        with self.lock:
            build = self.builds.get(build_id)
            return None if build is None else self._public(build)

    def status(self) -> dict:
        with self.lock:
            out = {"collecting": [], "queued": [], "running": []}
            for build in self.builds.values():
                state = self._state(build)
                if state in out:
                    out[state].append(build["map"])
            return {**{k: sorted(v) for k, v in out.items()}, "spool_bytes": self._spooled()}

    # ---- housekeeping

    def sweep(self, now: float | None = None) -> None:
        """Deletes the spool of every build that has ended (its answer stays), drops collectors nobody has fed
        for `height_collect_ttl_s`, and forgets all but the newest HEIGHT_BUILDS_KEPT ended builds."""
        now = time.time() if now is None else now
        with self.lock:
            for build in list(self.builds.values()):
                state = self._state(build)
                if state in ("done", "failed") and build["ended"] is None:
                    build["ended"], build["received"] = now, {}
                    shutil.rmtree(self.root / build["id"], ignore_errors=True)
                elif state == "collecting" and now - build["touched"] > self.settings.height_collect_ttl_s:
                    self._drop(build)
            ended = sorted((b for b in self.builds.values() if b["ended"] is not None), key=lambda b: b["ended"])
            for old in ended[:-HEIGHT_BUILDS_KEPT] if len(ended) > HEIGHT_BUILDS_KEPT else []:
                self._drop(old)

    def _sweep_forever(self) -> None:
        while True:
            time.sleep(1.0)
            try:
                self.sweep()
            except Exception:  # noqa: BLE001 - housekeeping must never stop
                traceback.print_exc()

    # ---- under the lock

    def _collecting(self, build_id: str) -> dict:
        build = self.builds.get(build_id)
        if build is None:
            raise LookupError(build_id)
        if build["job"] is not None:
            raise HeightConflict("not collecting", status=self._state(build))
        return build

    def _spooled(self) -> int:
        return sum(size for b in self.builds.values() for _, size in b["received"].values())

    def _state(self, build: dict) -> str:
        if build["job"] is None:
            return "collecting"
        job = self.control.get(build["job"])
        return "failed" if job is None else job.status

    def _public(self, build: dict) -> dict:
        body = {"id": build["id"], "key": build["key"], "map": build["map"], "status": self._state(build),
                "received": len(build["received"]) if build["ended"] is None else build["expected"],
                "expected": build["expected"]}
        job = self.control.get(build["job"]) if build["job"] else None
        if build["job"] and job is None:
            body["error"] = "the build was forgotten"
        elif job is not None:
            if job.error:
                body["error"] = job.error
            if job.result is not None:
                body["result"] = job.result
        return body

    def _drop(self, build: dict) -> None:
        self.builds.pop(build["id"], None)
        shutil.rmtree(self.root / build["id"], ignore_errors=True)
        if build["job"]:
            self.control.forget(build["job"])
```

(`import shutil`, `import hashlib` and `import traceback` at the top if they aren't there.) `self.control.get` and `forget` take the runner's own lock, never this one, so the two locks are always taken in the same order.

- [ ] **Step 5: The HTTP**

`make_handler(worker, control=None, heights=None)` and `make_server(worker, host, port, control=None, heights=None)` take the builds and pass them on.

`/health`:

```python
                body["idle"] = worker.idle()
                body["heights"] = heights.status() if heights else {"collecting": [], "queued": [], "running": [],
                                                                    "spool_bytes": 0}
```

`do_GET`, before the `/control/` branch:

```python
            if self.path.startswith("/heights/build/"):
                found = heights.get(self.path[len("/heights/build/"):]) if heights else None
                if found is None:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": "no such height build"})
                return self._send(HTTPStatus.OK, found)
```

`do_POST`, before `/heights`:

```python
            if self.path == "/heights/build" or self.path.startswith("/heights/build/"):
                return self._height_build()
```

and the method. Each failure has its own status, so the web app never has to guess what a 409 meant:

```python
        def _height_build(self):
            if heights is None or control is None or not control.enabled:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "map control is off on this worker"})
            body = self._json(limit=control.settings.control_max_bytes)
            if body is None:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not JSON, or too large"})
            parts = self.path[len("/heights/build"):].strip("/").split("/")
            try:
                if parts == [""]:
                    made = heights.open(str(body["key"]), str(body["map"]), body["rounds"], body.get("manifest"))
                    return self._send(HTTPStatus.ACCEPTED, made)
                if len(parts) == 2 and parts[1] == "rounds":
                    if not isinstance(body.get("rounds"), list):
                        raise ValueError("rounds must be a list")
                    return self._send(HTTPStatus.OK, heights.add(parts[0], body["rounds"]))
                if len(parts) == 2 and parts[1] == "start":
                    return self._send(HTTPStatus.ACCEPTED, heights.start(parts[0], body.get("previous")))
                if len(parts) == 2 and parts[1] == "cancel":
                    return self._send(HTTPStatus.OK, {"cancelled": heights.cancel(parts[0])})
            except KeyError:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a height build"})
            except LookupError:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "no such height build"})
            except HeightConflict as conflict:
                return self._send(HTTPStatus.CONFLICT, {"error": conflict.reason, **conflict.facts})
            except OverflowError as full:
                opening = parts == [""]
                return self._send(HTTPStatus.SERVICE_UNAVAILABLE if opening else HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                                  {"error": str(full)})
            except (TypeError, ValueError):
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a height build"})
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
```

In `main`: `heights = HeightBuilds(settings, control) if control is not None else None`, passed to `make_server`.

Add the five endpoints, the state table at the top of this task, and the two `/health` keys to the module docstring, and the settings to its environment list.

In `replay_worker/Dockerfile`: add `replay_worker.height_job`, `app.control.height_job` and `app.replays.height_inputs` to the smoke-test import line, and to the `ENV` block:

```
    REPLAY_HEIGHT_CMD='["/opt/control-venv/bin/python", "-m", "replay_worker.height_job"]' \
```

`webapp/tests/replays/test_replay_worker_control.py::test_the_image_builds_a_control_venv_with_the_web_apps_pins` reads the Dockerfile: extend it with an assertion that the text contains `REPLAY_HEIGHT_CMD` and `replay_worker.height_job` (using whatever name it gives the file's text).

- [ ] **Step 6: Run the worker's tests**

Run: `PY -m pytest tests/replays/test_replay_worker_heights.py tests/replays/test_replay_worker_control.py tests/replays/test_replay_worker.py tests/replays/test_control_isolation.py -q -p no:cacheprovider`
Expected: all pass but the failures recorded in the baseline.

- [ ] **Step 7: Commit**

```bash
git add replay_worker/server.py replay_worker/Dockerfile webapp/tests/replays/test_replay_worker_heights.py webapp/tests/replays/test_replay_worker_control.py
git commit -F <message file>
```

Message: `Worker: height builds with resumable states, immutable uploads and bounded spool, run alone ahead of control rounds`.

---

### Task 7: The dispatcher decides when, holds the map's rounds, resumes, verifies and applies the gate

**Files:**
- Modify: `webapp/app/config.py` (`replay_heights_auto`)
- Create: `webapp/app/services/replay_heights_remote.py`
- Modify: `webapp/app/services/replay_control_remote.py` (`ControlClient`, `State`, `_submit`, `cycle`, docstring)
- Modify: `webapp/tests/replays/test_control_isolation.py`
- Test: `webapp/tests/replays/test_heights_remote.py` (new)

**Interfaces:**
- Consumes: Task 2's `control_heights` and `height_inputs`; Task 4's `Conflict`, `Rejected`, `push_height`; Task 6's endpoints and states; `replay_control._valid_ids`, `condense_revision`, `geometry_inputs`.
- Produces:
  - `settings.replay_heights_auto: bool = False` (env `REPLAY_HEIGHTS_AUTO`).
  - `replay_heights_remote`: `HEIGHT_REBUILD_EVERY = 5`, `FIRST_BUILD_MATCHES = 2`, `BATCH_BYTES = 2_000_000`, `BATCHES_PER_CYCLE = 8`, `COLLECT_STALE_S = 1800`, `QUEUED_STALE_S = 900`, `STALE_BUILD_S = 7200`, `RESENDS_MAX = 2`, `MAX_TRIES = 3`, `BACKOFF_S = 300`.
  - `enabled() -> bool`; `current_manifests(db) -> dict[str, tuple[dict, list]]` ({map: (manifest, [(replay id, match uuid, round count)])}); `Due(map_name, manifest, replays, why)`; `Plan(due, off, skipped)`; `plan_maps(db) -> Plan`; `Build`, `HeightState`, `BuildFailed`; `step(session_factory, session, client, hstate, now, counts) -> set[str]` (the maps whose rounds are held).
  - `ControlClient.health()`, `.open_build(key, map_name, rounds, manifest)`, `.send_rounds(build_id, rounds)`, `.start_build(build_id, previous)`, `.cancel_build(build_id)`, `.build(build_id)`.
  - `State.heights: HeightState`; `_submit(..., held=frozenset())`.

**What each failure does.** This table is the contract the tests pin:

| What happened | Counts as | Then |
| --- | --- | --- |
| The worker can't be reached, or answers 503 | nothing | try again next cycle |
| The worker answers 404, including the open endpoint, or reports rounds missing at start | nothing, up to `RESENDS_MAX` times combined per try | reopen/resend; after that a failed try; a successful collecting response does not charge the same 404 twice |
| The build is `queued` while the worker is parsing | nothing | wait: a parse always goes first |
| `queued` on an idle worker for `QUEUED_STALE_S`, `running` for `STALE_BUILD_S`, or not all sent after `COLLECT_STALE_S` | a failed try | cancel it if it is still collecting |
| The worker refuses a request (400, 413), holds other bytes for a round, or a round is larger than `BATCH_BYTES` | a failed try | cancel it |
| The build fails on the worker (including "these aren't my inputs") | a failed try | |
| The result is malformed, is another build's, or fails `integrity` | a failed try | nothing stored |
| The map's inputs changed while it built or was being sent, or a feature generation appeared before collection/storage | nothing | drop the build; the next cycle replans or skips the map; no height activation |
| The worker's answer isn't shaped as the protocol says, including health before any Build exists (`idle` must be a bool) | a failed try for the selected manifest | the build is dropped; exhaustion releases the map's rounds |
| The result's `rules` are not this deploy's format and rules revision | a failed try | nothing stored: a row this deploy can't load would leave the map flat and never due |
| Another change to the map's heights won the lock | a failed try | |
| A match is deleted between the last check of the inputs and the commit | not caught here | the row is stored; the next cycle's plan sees evidence gone and rebuilds at once, or turns the heights off |
| The trusted result fails the gate | not a failure | stored `rejected`; not rebuilt until its inputs change |
| `MAX_TRIES` failed tries for one input manifest | | the map's rounds are released and sent with the heights it has; no more tries until the inputs change, or the web app restarts (the budget is in memory, like control's) |
| Anything unforeseen raises inside the height step | `heights_error` | logged; this cycle holds nothing and round dispatch carries on |

Two things this table does not promise, so nobody reads them into it:

- **A retry does not always rebuild.** The worker answers a key with the build it has, so a try after a `done`
  build whose result couldn't be used reads the same result again. Retrying helps where the failure was on this
  side (the lock, the verifying child, a broken transfer); a result that is wrong on the worker stays wrong until
  its inputs change or the worker forgets the build, and the three tries only bound how long the map is held.
  Making `cancel` drop an ended build would turn each try into a rebuild; the owner chose not to (2026-10-07:
  "leave as planned").
- **The dispatcher's own session never commits.** It holds the cycle's transaction-scoped lock
  (`pg_try_advisory_xact_lock`), which a commit or a rollback releases. Every write of this step (a build stored,
  heights turned off) goes through a session of its own, as the control rounds' stores already do.

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_heights_remote.py`. It drives the real dispatcher against the **real worker
handler over HTTP** (`replay_worker.server`), with stub children: a fake worker that accepts anything is what hid
the restart loop in review.

```python
"""The web app's height-rebuild dispatcher (app/services/replay_heights_remote.py;
docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, sections 3 and 4), on SQLite, against the real
worker handler over HTTP with stub children: when a rebuild is due, which rounds wait for it, how it resumes
after a restart on either side, what each failure costs, and that nothing goes live without proving itself."""

import base64
import json
import sys
import threading
import uuid
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from replay_worker import server  # noqa: E402
from test_control_store import db, factory, linked  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed  # noqa: E402,F401  (fixture)
from test_replay_worker_control import STUB  # noqa: E402

from app.config import settings  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.models.replay import ControlHeight, Replay, ReplayRound  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from app.replays import height_inputs as hi  # noqa: E402
from app.services import control_heights as ch  # noqa: E402
from app.services import replay_control as rc  # noqa: E402
from app.services import replay_control_remote as remote  # noqa: E402
from app.services import replay_heights_remote as hr  # noqa: E402

GRID = 128

TEST_CHECK_BYTES = b'{"lines": []}'
TEST_CHECK_SHA = __import__("hashlib").sha256(TEST_CHECK_BYTES).hexdigest()[:12]

# The build child: it answers what `mode.json` says, with the real manifest digest and a real asset's bytes.
BUILD_STUB = """
import base64, json, os, sys, time
sys.path.insert(0, {webapp!r})
from app.replays import height_inputs as hi
task = json.loads(sys.stdin.buffer.read())
mode = json.load(open({mode!r}))
time.sleep(mode.get("sleep", 0))
files = [f for d, _, fs in os.walk(task["dir"]) for f in fs]
out = {{"status": "ok", "key": task["key"], "map": task["map"], "inputs_sha": hi.digest(task["manifest"]),
       "rounds": len(files), "digest": mode["digest"], "asset": base64.b64encode(open(mode["asset"], "rb").read()).decode(),
       "report": mode["report"], "rules": mode["rules"], "seconds": 3.0, "peak": 1}}
out.update(mode.get("change", {{}}))
for gone in mode.get("drop", []):
    out.pop(gone, None)
print(mode["raw"] if "raw" in mode else json.dumps(out))
"""


def real_asset(path: Path, supported: int = 7, shift: int = 0) -> hc.HeightAsset:
    """A height asset with `supported` supported cells in one row, saved at `path`."""
    floors = np.full((GRID, GRID, hc.MAX_FLOORS), -1, np.int16)
    floors[40, 40:40 + supported, 0] = shift
    has = floors[..., 0] >= 0
    asset = hc.HeightAsset(floors, np.zeros_like(floors), has, np.zeros((GRID, GRID), bool), np.zeros((0, 5), np.int32),
                           {"origin_z": 100})
    hc.save_asset(path, asset)
    return asset


def good_report(supported: int = 7, walkable: int = 10, **changes) -> dict:
    out = {"walkable_cells": walkable, "supported_cells": supported, "supported": round(supported / walkable, 4),
           "ready": True, "not_ready": [],
           "kill_lines": {"qualifying": 400, "blocked": 2, "share": 0.005, "passes": True},
           "must_block": {"set": TEST_CHECK_SHA, "lines": 0, "checked": 0, "unchecked": 0, "blocked": 0,
                          "passes": True}}
    for key, value in changes.items():
        out[key] = {**out[key], **value} if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


class Rig:
    """A real worker (handler, control runner, height builds) on localhost with stub children, and the real client."""

    def __init__(self, tmp_path: Path, **overrides):
        self.tmp = tmp_path
        (tmp_path / "events").mkdir(exist_ok=True)
        control_stub = tmp_path / "stub_control.py"
        control_stub.write_text(STUB.format(log=str(tmp_path / "events")), encoding="utf-8")
        self.mode_path = tmp_path / "mode.json"
        build_stub = tmp_path / "stub_build.py"
        build_stub.write_text(BUILD_STUB.format(webapp=str(WEBAPP), mode=str(self.mode_path)), encoding="utf-8")
        self.asset = real_asset(tmp_path / "asset.npz")
        self.mode(digest=self.asset.digest, asset=str(tmp_path / "asset.npz"), report=good_report(), rules=hc.rules())
        self.settings = server.Settings(temp_root=tmp_path / "jobs", control_cache_dir=tmp_path / "cache",
                                        control_cmd=[sys.executable, str(control_stub)],
                                        height_cmd=[sys.executable, str(build_stub)], **overrides)
        (tmp_path / "jobs").mkdir(exist_ok=True)
        self.control = server.ControlRunner(self.settings)
        self.builds = server.HeightBuilds(self.settings, self.control)
        self.worker = server.Worker(self.settings)
        self.httpd = server.make_server(self.worker, control=self.control, heights=self.builds)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.client = remote.ControlClient(f"http://127.0.0.1:{self.httpd.server_address[1]}")
        self.state = remote.State()
        self.t = 0.0

    def mode(self, **values):
        current = json.loads(self.mode_path.read_text(encoding="utf-8")) if self.mode_path.exists() else {}
        self.mode_path.write_text(json.dumps({**current, **values}), encoding="utf-8")

    def cycle(self, factory, step_s: float = 400.0) -> dict:
        self.t += step_s
        return remote.cycle(factory, self.client, self.state, now=self.t)

    def until(self, factory, done, cycles: int = 40, step_s: float = 400.0) -> list:
        """Cycles until `done(counts so far)` (the children are real processes: a build takes a moment)."""
        import time

        seen = []
        for _ in range(cycles):
            seen.append(self.cycle(factory, step_s))
            if done(seen):
                return seen
            time.sleep(0.15)
        raise AssertionError(f"not reached in {cycles} cycles: {[{k: v for k, v in c.items() if v} for c in seen]}")

    def restart_dispatcher(self):
        self.state = remote.State()

    def restart_worker_while_collecting(self):
        assert not self.control.running  # no child is being abandoned by this test helper
        self.httpd.shutdown()
        self.httpd.server_close()
        self.control.idle = lambda: False
        with self.builds.lock:
            self.builds.builds.clear()  # retire the old sweeper's entries before a new spool is created
        self.control = server.ControlRunner(self.settings)
        self.builds = server.HeightBuilds(self.settings, self.control)
        self.httpd = server.make_server(self.worker, control=self.control, heights=self.builds)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.client = remote.ControlClient(f"http://127.0.0.1:{self.httpd.server_address[1]}")
        assert not self.control.jobs and not self.control.by_key and not self.builds.builds


def total(seen: list, key: str) -> int:
    return sum(c.get(key, 0) for c in seen)


@pytest.fixture
def rig(tmp_path):
    made = Rig(tmp_path)
    yield made
    made.httpd.shutdown()


@pytest.fixture(autouse=True)
def on(monkeypatch, tmp_path):
    from app.control import geometry as cg

    directory = tmp_path / "verification-assets"
    directory.mkdir()
    walk_px = np.zeros((cg.PX, cg.PX), bool)
    walk_px[40 * cg.CELL:41 * cg.CELL, 40 * cg.CELL:50 * cg.CELL] = True
    for name in ("Ascent", "Bind"):
        cg.write_mask_png(directory / f"{name}.walk.png", walk_px)
        cg.write_mask_png(directory / f"{name}.sight.png", walk_px)
    (directory / "tags.json").write_text('{"maps": {}}', encoding="utf-8")
    (directory / "index.json").write_text(json.dumps({"maps": {
        name: {"sight_sha": "toy-sight", "walk_sha": "toy-walk"} for name in ("Ascent", "Bind")}}), encoding="utf-8")
    checks = tmp_path / "verification-checks.json"
    checks.write_bytes(TEST_CHECK_BYTES)
    monkeypatch.setattr(hi, "CONTROL_DIR", directory)
    monkeypatch.setattr(hi, "MUST_BLOCK", checks)
    monkeypatch.setattr(settings, "replay_heights_auto", True)
    monkeypatch.setattr(settings, "replay_control_remote", True)
    monkeypatch.setattr(settings, "replay_worker_url", "http://worker")
    monkeypatch.setattr(hi, "MIN_CONDENSE_REVISION", 0)       # the synthetic replay's recipe is an old revision


def clone(db, replay, count):
    """`count` more valid replays of the same map: copies of `replay` under new match ids and source hashes."""
    made = []
    for _ in range(count):
        copy = Replay(**{c.name: getattr(replay, c.name) for c in Replay.__table__.columns
                         if c.name not in ("id", "match_uuid", "match_id", "source_sha256")},
                      match_uuid=str(uuid.uuid4()), match_id=None, source_sha256=uuid.uuid4().hex * 2)
        copy.link_status = "unlinked"
        db.add(copy)
        db.flush()
        for row in db.query(ReplayRound).filter(ReplayRound.replay_id == replay.id):
            db.add(ReplayRound(replay_id=copy.id, round_number=row.round_number, data=row.data))
        made.append(copy)
    db.commit()
    return made


def delete(db, replay):
    db.query(ReplayRound).filter(ReplayRound.replay_id == replay.id).delete()
    db.query(Replay).filter(Replay.id == replay.id).delete()
    db.commit()


def one_round_batches(db, monkeypatch):
    """Batches that hold one or two rounds, one batch a cycle: a build that takes several cycles to send."""
    monkeypatch.setattr(hr, "BATCHES_PER_CYCLE", 1)
    monkeypatch.setattr(hr, "BATCH_BYTES", max(len(base64.b64encode(r.data)) for r in db.query(ReplayRound)))


def whole_batches(monkeypatch):
    monkeypatch.setattr(hr, "BATCHES_PER_CYCLE", 8)
    monkeypatch.setattr(hr, "BATCH_BYTES", 2_000_000)


def stored(db, name, digest="a" * 12, report=None, manifest=None, rules=None):
    manifest = manifest or hr.current_manifests(db)[name][0]
    return ch.store_build(db, map_name=name, digest=digest, asset=b"old", report=report or good_report(),
                          inputs=manifest, rules=rules or hc.rules())


# ---------------------------------------------------------------- when a rebuild is due


def test_nothing_is_due_with_one_match_and_the_first_build_is_due_at_two(db, linked):
    assert hr.plan_maps(db).due == []
    clone(db, linked, 1)
    [due] = hr.plan_maps(db).due
    assert due.map_name == linked.map_name and due.why == "first build" and len(due.replays) == 2
    assert hi.rounds_expected(due.manifest) == 2 * linked.round_count


def test_after_a_build_the_next_is_due_five_new_matches_later_whatever_its_status(db, linked):
    clone(db, linked, 1)
    stored(db, linked.map_name, report=good_report(ready=False, not_ready=["thin"]))        # rejected
    clone(db, linked, hr.HEIGHT_REBUILD_EVERY - 1)
    assert hr.plan_maps(db).due == [], "four new matches: a failing map can't loop"
    clone(db, linked, 1)
    assert [d.why for d in hr.plan_maps(db).due] == ["5 new matches"]


@pytest.mark.parametrize("name", ["rule", "format", "mask", "check set"])
def test_a_changed_rule_mask_or_check_set_makes_the_rebuild_due_at_once(db, linked, monkeypatch, name):
    clone(db, linked, 1)
    stored(db, linked.map_name)
    assert hr.plan_maps(db).due == []
    if name == "rule":
        monkeypatch.setattr(cf, "HEIGHT_RULES_REVISION", cf.HEIGHT_RULES_REVISION + 1)
    elif name == "format":
        monkeypatch.setattr(cf, "HEIGHT_VERSION", cf.HEIGHT_VERSION + 1)
    elif name == "mask":
        real = hi.geometry_identity
        monkeypatch.setattr(hi, "geometry_identity", lambda m, *a, **k: {**real(m), "walk": "x" * 12})
    else:
        monkeypatch.setattr(hi, "must_block_sha", lambda *a, **k: "z" * 12)
    assert [d.why for d in hr.plan_maps(db).due] == ["its inputs changed"], name


def test_deleted_or_recondensed_evidence_is_due_at_once_and_too_little_left_turns_heights_off(db, linked):
    others = clone(db, linked, 2)
    stored(db, linked.map_name)
    others[0].recipe = others[0].recipe + ".x"                 # the same match, other blobs
    db.commit()
    assert [d.why for d in hr.plan_maps(db).due] == ["its evidence changed"]
    stored(db, linked.map_name, digest="b" * 12)
    delete(db, others[0])
    assert [d.why for d in hr.plan_maps(db).due] == ["its evidence changed"], "not after five more matches: now"
    delete(db, others[1])                                      # one match left: no floor can have two matches
    plan = hr.plan_maps(db)
    assert plan.due == [] and plan.off == [linked.map_name], "heights built from deleted matches don't stay"
    delete(db, linked)
    assert hr.plan_maps(db).off == [linked.map_name]


def test_a_map_with_a_published_feature_generation_is_not_rebuilt(db, linked, monkeypatch):
    clone(db, linked, 1)
    real = rc.geometry_inputs
    monkeypatch.setattr(rc, "geometry_inputs", lambda m, heights=None: {**real(m, heights), "features": "feat" * 4})
    plan = hr.plan_maps(db)
    assert plan.due == [] and linked.map_name in plan.skipped and "feature generation" in plan.skipped[linked.map_name]


# ---------------------------------------------------------------- the whole cycle, over the real handler


def test_a_due_maps_rounds_wait_and_its_build_goes_live(rig, factory, db, linked):
    clone(db, linked, 1)
    first = rig.cycle(factory)
    assert first["sent"] == 0, "the map's rounds are held while its rebuild is due or running"
    seen = [first, *rig.until(factory, lambda s: total(s, "heights_live") == 1)]
    row = db.query(ControlHeight).one()
    assert (row.status, row.digest, bytes(row.asset)) == ("active", rig.asset.digest, (rig.tmp / "asset.npz").read_bytes())
    assert row.inputs_sha == hi.digest(row.inputs) and len(row.match_uuids) == 2
    assert row.report["seconds"] == 3.0, "what the build cost is kept with it"
    assert total(seen, "heights_sent") == 2 * linked.round_count, "every round sent exactly once"
    assert rig.state.heights.build is None and hr.plan_maps(db).due == []
    after = rig.until(factory, lambda s: total(s, "sent") > 0)
    assert total(after, "pushed") == 1, "the rounds name the new heights, and the worker is given them"
    assert (rig.tmp / "cache" / "heights" / f"{linked.map_name}.{rig.asset.digest}.height.npz").is_file()


def test_a_build_below_the_bar_is_rejected_the_old_heights_stay_and_the_rounds_flow(rig, factory, db, linked):
    clone(db, linked, 1)
    stored(db, linked.map_name)                                # active, built from these two matches
    clone(db, linked, hr.HEIGHT_REBUILD_EVERY)                 # five more: due, with nothing gone
    rig.mode(report=good_report(supported=4, ready=False, not_ready=["supported 40.0% is under 60%"]),
             **{"asset": str(rig.tmp / "four.npz"), "digest": real_asset(rig.tmp / "four.npz", supported=4).digest})
    seen = rig.until(factory, lambda s: total(s, "heights_rejected") == 1)
    assert total(seen, "heights_failed") == 0
    assert ch.active_digests(db) == {linked.map_name: "a" * 12}, "keeping the old heights is the fallback"
    assert [r.status for r in ch.rows(db, linked.map_name)] == ["rejected", "active"]
    assert total(rig.until(factory, lambda s: total(s, "sent") > 0), "sent") > 0
    assert hr.plan_maps(db).due == [], "not built again until its inputs change"


def test_a_restarted_dispatcher_adopts_a_running_build_and_never_resends_to_it(rig, factory, db, linked):
    clone(db, linked, 1)
    rig.mode(sleep=1.5)
    rig.until(factory, lambda s: rig.builds.status()["running"] or rig.builds.status()["queued"])
    [build] = rig.builds.builds.values()
    rig.restart_dispatcher()                                   # the web app restarted: nothing in memory
    seen = rig.until(factory, lambda s: total(s, "heights_live") == 1)
    assert total(seen, "heights_sent") == 0, "it asked the worker what state the build was in, and waited"
    assert total(seen, "heights_failed") == 0 and len(rig.builds.builds) == 1
    assert [b["id"] for b in rig.builds.builds.values()] == [build["id"]]
    assert db.query(ControlHeight).count() == 1


def test_a_restarted_dispatcher_finishes_a_half_sent_build_without_double_counting(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    one_round_batches(db, monkeypatch)
    rig.cycle(factory)
    rig.cycle(factory)
    [build] = rig.builds.builds.values()
    assert 0 < len(build["received"]) < 2 * linked.round_count
    spooled = rig.builds.status()["spool_bytes"]
    rig.restart_dispatcher()
    whole_batches(monkeypatch)
    rig.cycle(factory)                                         # sends everything again: the worker keeps what it had
    [again] = rig.builds.builds.values()
    assert again["id"] == build["id"] and len(again["received"]) == 2 * linked.round_count
    assert rig.builds.status()["spool_bytes"] > spooled, "the rounds it lacked were added; the rest cost nothing"
    assert sum(size for _, size in again["received"].values()) == rig.builds.status()["spool_bytes"]
    seen = rig.until(factory, lambda s: total(s, "heights_live") == 1)
    assert total(seen, "heights_failed") == 0 and len(rig.builds.builds) == 1


def test_a_lost_start_response_is_recovered_by_asking(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    real = rig.client.start_build
    lost = {"n": 0}

    def start_then_lose_the_answer(build_id, previous):
        real(build_id, previous)                               # the worker did start it
        if lost["n"] == 0:
            lost["n"] += 1
            raise remote.Unreachable("the answer never arrived")

    monkeypatch.setattr(rig.client, "start_build", start_then_lose_the_answer)
    seen = rig.until(factory, lambda s: total(s, "heights_live") == 1)
    assert lost["n"] == 1 and total(seen, "heights_failed") == 0 and len(rig.builds.builds) == 1
    assert total(seen, "heights_sent") == 2 * linked.round_count, "nothing was sent to the started build"


def test_a_real_worker_restart_resends_from_the_first_round(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    one_round_batches(db, monkeypatch)
    first = rig.cycle(factory, step_s=10)
    assert first["heights_sent"] > 0 and rig.state.heights.build.todo
    rig.restart_worker_while_collecting()
    whole_batches(monkeypatch)
    seen = rig.until(factory, lambda s: total(s, "heights_live") == 1, step_s=10)
    assert total(seen, "heights_sent") == 2 * linked.round_count
    assert total(seen, "heights_failed") == 0


@pytest.mark.parametrize("where", ["open", "known build"])
def test_repeated_404s_exhaust_the_budget_and_release_the_map(rig, factory, db, linked, monkeypatch, where):
    clone(db, linked, 1)
    if where == "known build":
        one_round_batches(db, monkeypatch)
        rig.cycle(factory, step_s=10)
        assert rig.state.heights.build.job_id is not None

    def forgotten(*args, **kwargs):
        raise remote.WorkerGone("404: height builds unavailable")

    monkeypatch.setattr(rig.client, "open_build", forgotten)
    monkeypatch.setattr(rig.client, "build", forgotten)
    seen = rig.until(factory, lambda s: total(s, "heights_failed") == hr.MAX_TRIES, cycles=60, step_s=400)
    assert not ch.active_digests(db) and db.query(ControlHeight).count() == 0
    assert rig.state.heights.build is None and all(n == hr.MAX_TRIES for n, _ in rig.state.heights.tries.values())
    assert total(rig.until(factory, lambda s: total(s, "sent") > 0), "sent") > 0


@pytest.mark.parametrize("answer", [[], {}, {"idle": "yes"}, {"idle": 1}, {"idle": None}])
def test_malformed_health_before_open_spends_the_selected_manifests_budget(rig, factory, db, linked,
                                                                        monkeypatch, answer):
    clone(db, linked, 1)
    manifest = hr.current_manifests(db)[linked.map_name][0]
    monkeypatch.setattr(rig.client, "health", lambda: answer)
    seen = rig.until(factory, lambda s: total(s, "heights_failed") == hr.MAX_TRIES, cycles=60)
    assert rig.state.heights.tries[hi.key(manifest)][0] == hr.MAX_TRIES
    assert not rig.builds.builds and rig.state.heights.build is None and db.query(ControlHeight).count() == 0
    assert total(rig.until(factory, lambda s: total(s, "sent") > 0), "sent") > 0


# ---------------------------------------------------------------- a result that isn't what it says


@pytest.mark.parametrize("name, mode", [
    ("not JSON", {"raw": "not json"}),
    ("no asset", {"drop": ["asset"]}),
    ("an asset that isn't base64", {"change": {"asset": "***"}}),
    ("bytes that aren't an asset", {"change": {"asset": base64.b64encode(b"not a zip").decode()}}),
    ("another build's key", {"change": {"key": "heights:Other:0000000000000000"}}),
    ("another map", {"change": {"map": "Bind"}}),
    ("other inputs", {"change": {"inputs_sha": "0" * 16}}),
    ("fewer rounds than the manifest", {"change": {"rounds": 1}}),
    ("a digest the bytes don't have", {"change": {"digest": "0" * 12}}),
    ("a report that isn't one", {"change": {"report": "ready"}}),
    ("a report whose counts aren't the asset's", {"change": {"report": good_report(supported=9)}}),
    ("no must-block evidence", {"change": {"report": {k: v for k, v in good_report().items() if k != "must_block"}}}),
    ("a check run against another list", {"change": {"report": good_report(must_block={"set": "o" * 12})}}),
    ("rules that aren't an object", {"change": {"rules": "v2"}}),
    ("rules of another format", {"change": {"rules": {"version": cf.HEIGHT_VERSION + 1,
                                                       "revision": cf.HEIGHT_RULES_REVISION, "constants": "x"}}}),
    ("rules of another revision", {"change": {"rules": {"version": cf.HEIGHT_VERSION,
                                                         "revision": cf.HEIGHT_RULES_REVISION + 1, "constants": "x"}}}),
])
def test_a_result_that_cant_be_trusted_is_never_stored_and_spends_the_budget(rig, factory, db, linked, name, mode):
    clone(db, linked, 1)
    rig.mode(**mode)
    seen = rig.until(factory, lambda s: total(s, "heights_failed") == hr.MAX_TRIES, cycles=60)
    assert db.query(ControlHeight).count() == 0, name
    assert total(seen, "heights_live") == 0 and total(seen, "heights_rejected") == 0
    more = rig.until(factory, lambda s: total(s, "sent") > 0)
    assert total(more, "heights_failed") == 0, "the budget is spent: the map's rounds flow, with no more tries"
    assert all("height" not in json.loads(job.task or b"{}") for job in rig.control.jobs.values() if job.kind == "control")


def test_a_build_that_fails_on_the_worker_spends_the_budget_and_other_maps_are_never_stopped(rig, factory, db, linked):
    clone(db, linked, 1)
    other = clone(db, linked, 1)[0]
    other.map_name = "Bind"                                    # a second map with one match: nothing due for it
    db.commit()
    rig.mode(raw="not json")
    first = rig.cycle(factory)
    assert first["sent"] > 0, "the other map's rounds go out while this map's are held"
    seen = rig.until(factory, lambda s: total(s, "heights_failed") == hr.MAX_TRIES, cycles=60)
    assert total(seen, "heights_live") == 0


# ---------------------------------------------------------------- failures that must not hold a map forever


def test_a_refused_request_is_a_failed_try_not_an_unreachable_worker(tmp_path, factory, db, linked):
    clone(db, linked, 1)
    rig = Rig(tmp_path, height_spool_bytes=10)                 # the worker answers 413 to the first batch
    try:
        seen = rig.until(factory, lambda s: total(s, "heights_failed") == hr.MAX_TRIES, cycles=60)
        assert total(seen, "unreachable") == 0 and not rig.builds.builds, "and the half-opened build was cancelled"
        assert total(rig.until(factory, lambda s: total(s, "sent") > 0), "sent") > 0
    finally:
        rig.httpd.shutdown()


def test_a_round_too_large_to_send_fails_the_build_instead_of_being_sent(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    monkeypatch.setattr(hr, "BATCH_BYTES", 8)
    seen = rig.until(factory, lambda s: total(s, "heights_failed") == hr.MAX_TRIES, cycles=60)
    assert total(seen, "heights_sent") == 0 and not rig.builds.builds


def test_a_build_nobody_can_finish_sending_is_given_up_on(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    answers = iter([True])                                     # idle once, to open it; then a parse that never ends
    monkeypatch.setattr(rig.client, "health", lambda: {"idle": next(answers, False)})
    rig.cycle(factory, step_s=10)
    assert rig.state.heights.build is not None
    seen = [rig.cycle(factory, step_s=hr.COLLECT_STALE_S + 1)]
    assert total(seen, "heights_failed") == 1 and rig.state.heights.build is None and not rig.builds.builds


def test_queued_behind_a_parse_is_never_a_failure_and_queued_on_an_idle_worker_is(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    rig.control.idle = lambda: False                           # the runner starts nothing
    rig.until(factory, lambda s: rig.builds.status()["queued"], step_s=10)
    monkeypatch.setattr(rig.client, "health", lambda: {"idle": False})      # because the worker is parsing
    for _ in range(4):
        assert rig.cycle(factory, step_s=hr.QUEUED_STALE_S)["heights_failed"] == 0
    monkeypatch.setattr(rig.client, "health", lambda: {"idle": True})       # idle, and still not started
    rig.cycle(factory, step_s=10)
    assert rig.cycle(factory, step_s=hr.QUEUED_STALE_S + 1)["heights_failed"] == 1


def test_an_error_inside_the_height_step_never_stops_round_dispatch(rig, factory, db, linked, monkeypatch):
    def boom(session):
        raise RuntimeError("unforeseen")

    monkeypatch.setattr(hr, "plan_maps", boom)
    counts = rig.cycle(factory)
    assert counts["heights_error"] == 1 and counts["sent"] == remote.IN_FLIGHT


def test_an_answer_that_isnt_shaped_as_the_protocol_says_spends_a_try_and_is_not_met_again(rig, factory, db, linked,
                                                                                         monkeypatch):
    clone(db, linked, 1)
    rig.mode(sleep=1.5)
    rig.until(factory, lambda s: rig.builds.status()["running"] or rig.builds.status()["queued"])
    real = rig.client.build
    monkeypatch.setattr(rig.client, "build", lambda build_id: ["not", "a", "build"])
    counts = rig.cycle(factory)
    assert counts["heights_failed"] == 1 and counts.get("heights_error", 0) == 0
    assert rig.state.heights.build is None, "dropped here: left to the cycle's catch-all it would repeat every cycle"
    monkeypatch.setattr(rig.client, "build", real)
    seen = rig.until(factory, lambda s: total(s, "heights_live") == 1)
    assert total(seen, "heights_failed") == 0 and len(rig.builds.builds) == 1, "the next try adopts the worker's build"


def test_the_height_step_never_commits_or_rolls_back_the_dispatchers_own_session(rig, factory, db, linked):
    # That session holds the cycle's transaction-scoped lock on PostgreSQL: ending its transaction in the middle
    # of a cycle would let a second dispatcher in. Turning heights off and storing a build both write, and both
    # must do it through a session of their own.
    import time

    from sqlalchemy import event

    def watched():
        session, ended = factory(), []
        event.listen(session, "after_commit", lambda s: ended.append("commit"))
        event.listen(session, "after_rollback", lambda s: ended.append("rollback"))
        return session, ended

    others = clone(db, linked, 1)
    stored(db, linked.map_name)
    delete(db, others[0])                                      # one match left under active heights: off
    session, ended = watched()
    counts = {}
    hr.step(factory, session, rig.client, hr.HeightState(), 1.0, counts)
    assert counts["heights_off"] == 1 and ended == []
    session.close()
    check = factory()
    assert ch.active_digests(check) == {}
    check.close()

    clone(db, linked, 1)                                       # two matches again: a build, stored active
    hstate, t = hr.HeightState(), 1.0
    for _ in range(40):
        session, ended = watched()
        counts, t = {}, t + 400.0
        hr.step(factory, session, rig.client, hstate, t, counts)
        assert ended == [], "nor while a build is sent, waited for, verified and stored"
        session.close()
        if counts["heights_live"]:
            break
        time.sleep(0.15)
    assert counts["heights_live"] == 1


def test_generation_published_during_collection_keeps_the_pending_result_from_being_stored(rig, factory, db,
                                                                                         linked, monkeypatch):
    clone(db, linked, 1)
    manifest = hr.current_manifests(db)[linked.map_name][0]
    build = hr.Build(linked.map_name, hi.key(manifest), manifest, [], opened_at=0)
    result = {"key": build.key, "map": build.map_name, "inputs_sha": hi.digest(manifest),
              "rounds": hi.rounds_expected(manifest), "digest": rig.asset.digest, "report": good_report(),
              "rules": hc.rules(), "asset": base64.b64encode((rig.tmp / "asset.npz").read_bytes()).decode()}
    stored(db, linked.map_name)
    before = ch.active_digests(db)
    real = rc.geometry_inputs
    monkeypatch.setattr(rc, "geometry_inputs", lambda m, heights=None: {**(real(m, heights) or {}), "features": "new-generation"})
    counts = dict.fromkeys(hr.COUNTS, 0)
    with pytest.raises(hr._Stale):
        hr._finish(factory, db, build, {"status": "done", "result": result}, counts)
    assert ch.active_digests(db) == before and db.query(ControlHeight).count() == 1
    assert counts["heights_live"] == counts["heights_rejected"] == counts["heights_failed"] == 0
    assert linked.map_name in hr.plan_maps(db).skipped


# ---------------------------------------------------------------- inputs that move under a build


def test_a_match_deleted_while_its_rounds_are_being_sent_drops_the_build(rig, factory, db, linked, monkeypatch):
    others = clone(db, linked, 2)
    one_round_batches(db, monkeypatch)
    rig.cycle(factory)
    first_key = rig.state.heights.build.key
    delete(db, others[0])
    counts = rig.cycle(factory)
    assert counts["heights_stale"] == 1 and counts["heights_failed"] == 0
    whole_batches(monkeypatch)
    rig.until(factory, lambda s: total(s, "heights_live") == 1)
    row = db.query(ControlHeight).one()
    assert hi.key(row.inputs) != first_key and len(row.match_uuids) == 2, "built from the matches that are left"


def test_a_match_deleted_after_the_build_started_keeps_its_result_from_going_live(rig, factory, db, linked):
    others = clone(db, linked, 2)
    rig.mode(sleep=1.5)
    rig.until(factory, lambda s: rig.builds.status()["running"] or rig.builds.status()["queued"])
    delete(db, others[0])
    seen = rig.until(factory, lambda s: total(s, "heights_stale") >= 1)
    assert total(seen, "heights_live") == 0, "a result for inputs that are gone is never activated"
    assert db.query(ControlHeight).count() == 0
    rig.mode(sleep=0)
    rig.until(factory, lambda s: total(s, "heights_live") == 1)
    assert len(db.query(ControlHeight).one().match_uuids) == 2


def test_a_good_result_for_inputs_that_changed_meanwhile_is_not_stored(rig, factory, db, linked):
    # Between the last plan and the result arriving, a match was deleted: the result is whole and honest, and
    # still must not go live.
    others = clone(db, linked, 2)
    manifest, replays = hr.current_manifests(db)[linked.map_name]
    build = hr.Build(linked.map_name, hi.key(manifest), manifest, [], opened_at=0.0)
    result = {"key": build.key, "map": build.map_name, "inputs_sha": hi.digest(manifest),
              "rounds": hi.rounds_expected(manifest), "digest": rig.asset.digest, "report": good_report(),
              "rules": hc.rules(), "asset": base64.b64encode((rig.tmp / "asset.npz").read_bytes()).decode()}
    delete(db, others[0])
    counts = dict.fromkeys(hr.COUNTS, 0)
    with pytest.raises(hr._Stale):
        hr._finish(factory, db, build, {"status": "done", "result": result}, counts)
    assert db.query(ControlHeight).count() == 0 and counts["heights_live"] == 0


def test_evidence_deleted_and_the_rebuild_rejected_turns_the_maps_heights_off(rig, factory, db, linked):
    others = clone(db, linked, 2)
    stored(db, linked.map_name)
    delete(db, others[0])
    rig.mode(report=good_report(supported=4, ready=False, not_ready=["supported 40.0% is under 60%"]),
             **{"asset": str(rig.tmp / "four.npz"), "digest": real_asset(rig.tmp / "four.npz", supported=4).digest})
    seen = rig.until(factory, lambda s: total(s, "heights_rejected") == 1)
    assert total(seen, "heights_off") == 1 and ch.active_digests(db) == {}
    assert [r.status for r in ch.rows(db, linked.map_name)] == ["rejected", "superseded"]


def test_fewer_than_two_matches_left_turns_heights_off_without_a_build(rig, factory, db, linked):
    others = clone(db, linked, 1)
    stored(db, linked.map_name)
    delete(db, others[0])
    counts = rig.cycle(factory)
    assert counts["heights_off"] == 1 and ch.active_digests(db) == {} and not rig.builds.builds
    assert counts["sent"] > 0, "and its rounds are recomputed flat"


def test_it_is_off_by_default_and_in_demo_mode(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    monkeypatch.setattr(settings, "replay_heights_auto", False)
    assert rig.cycle(factory)["sent"] == remote.IN_FLIGHT and not rig.builds.builds
    monkeypatch.setattr(settings, "replay_heights_auto", True)
    monkeypatch.setattr(settings, "demo_mode", True)
    assert hr.enabled() is False
```

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_heights_remote.py -q -p no:cacheprovider`
Expected: FAIL at import, `cannot import name 'replay_heights_remote' from 'app.services'`.

- [ ] **Step 3: The setting**

In `webapp/app/config.py`, after `replay_control_remote`:

```python
    # Height rebuilds on the replay worker (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md):
    # a map's heights are rebuilt every few new matches and go live when they pass the gate. Off by default, so
    # a deploy changes nothing until it is set; needs REPLAY_CONTROL_REMOTE too, and is always off in demo mode.
    replay_heights_auto: bool = False
```

- [ ] **Step 4: The client's build calls**

In `webapp/app/services/replay_control_remote.py`, `ControlClient` gains (after `push_height`):

```python
    def _post(self, path: str, body: dict) -> dict:
        return self._call(urllib.request.Request(f"{self.base}{path}", data=json.dumps(body).encode("utf-8"),
                                                 method="POST", headers={"Content-Type": "application/json"}))

    def health(self) -> dict:
        return self._call(urllib.request.Request(f"{self.base}/health"))

    def open_build(self, key: str, map_name: str, rounds: int, manifest: dict) -> dict:
        return self._post("/heights/build", {"key": key, "map": map_name, "rounds": rounds, "manifest": manifest})

    def send_rounds(self, build_id: str, rounds: list) -> dict:
        return self._post(f"/heights/build/{build_id}/rounds", {"rounds": rounds})

    def start_build(self, build_id: str, previous: str | None) -> dict:
        return self._post(f"/heights/build/{build_id}/start", {"previous": previous} if previous else {})

    def cancel_build(self, build_id: str) -> dict:
        return self._post(f"/heights/build/{build_id}/cancel", {})

    def build(self, build_id: str) -> dict:
        return self._call(urllib.request.Request(f"{self.base}/heights/build/{build_id}"))
```

These raise Task 4's `Conflict` (409, with the worker's body) and `Rejected` (400, 413) as they are: each caller in the next step decides what its conflict means. Only `submit` (a control round) turns a conflict into `NeedsHeight`.

- [ ] **Step 5: The rebuild dispatcher**

Create `webapp/app/services/replay_heights_remote.py`:

```python
"""Height rebuilds on the replay worker, driven from the web app
(docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, sections 3 and 4). Called once per cycle by
the map-control dispatcher (app/services/replay_control_remote.py `cycle`), under its lock.

- **Due** (`plan_maps`). A map's inputs are its manifest (app/replays/height_inputs.py): its valid replays at
  condenser revision MIN_CONDENSE_REVISION or later, its masks, the rule revisions, the check set. Against the
  map's last build, whatever that build's status:
  - no build yet: due with FIRST_BUILD_MATCHES matches;
  - the same inputs: not due (a rejected build is not built again until something changes);
  - evidence gone (a match deleted, or its blobs replaced), or the masks, rules or check set changed: due at
    once;
  - only matches added: due with HEIGHT_REBUILD_EVERY of them.
  A map left with fewer than FIRST_BUILD_MATCHES matches whose active heights were built from evidence that is
  gone has its heights turned off: a deletion means the data goes. A map with a published feature generation
  is never rebuilt automatically (its generation names the height digest and would stop verifying).
- **Held.** While a map's rebuild is due or running, its rounds are not sent for recomputing: new heights
  would make them out of date at once. Other maps' rounds keep flowing. The page keeps showing a held round's
  stored control, marked out of date.
- **Sent, resumably.** One build at a time, opened only while the worker reports idle. Every cycle starts by
  asking the worker what state the build is in (its job when the dispatcher knows one; else by opening the key,
  which answers the build there is) and acts on that: `collecting` sends rounds and then starts it; `queued`
  and `running` wait; `done` is collected; `failed` is a failed try. So a restart of the web app adopts the
  build, a lost response is found on the next cycle, and a worker that forgot the build is sent it again from
  the first round. A started build is never sent to.
- **Trusted, then gated** (`_finish`). A result must be this build's (key, map, input digest, round count),
  its bytes must be the asset it names (app/services/control_heights.py `verify_asset`, `integrity`), and the
  map's inputs must still be the ones it was built from; then `store_build` applies the gate: active, or
  rejected with the old heights kept. A rejected rebuild of a map whose active heights rest on evidence that is
  gone turns them off.
- **Bounded.** Every failure either costs nothing (the worker is away, or parsing), or spends one of MAX_TRIES
  tries for that input manifest (the module's tests pin which). When they are spent the map's rounds are
  released and no more tries are made until its inputs change. The budget is in memory: a restart of the web
  app starts it again.

Off unless REPLAY_HEIGHTS_AUTO is set (and the control dispatcher is on), and never in demo mode. Standard
library and the DB only; the asset is opened by a child process.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import load_only

from app.config import settings
from app.models.replay import Replay, ReplayRound
from app.replays import control_format as cf
from app.replays import height_inputs as hi
from app.services import control_heights, replay_control

log = logging.getLogger(__name__)

HEIGHT_REBUILD_EVERY = 5
FIRST_BUILD_MATCHES = 2
BATCH_BYTES = 2_000_000          # base64 of rounds per request (the worker's request cap is 4 MB)
BATCHES_PER_CYCLE = 8
COLLECT_STALE_S = 1800           # a build whose rounds aren't all sent this long after it was opened
QUEUED_STALE_S = 900             # a build queued this long on a worker that says it is idle
STALE_BUILD_S = 7200             # a build seen running this long
RESENDS_MAX = 2                  # times a build may be sent again from the first round (a forgotten build, lost rounds)
MAX_TRIES = 3
BACKOFF_S = 300
COUNTS = ("heights_live", "heights_rejected", "heights_failed", "heights_stale", "heights_off", "heights_sent")


def enabled() -> bool:
    return bool(settings.replay_heights_auto and settings.replay_control_remote and settings.replay_worker_url
                and not settings.demo_mode)


class BuildFailed(Exception):
    """This try at the build is over, and it spends one of the manifest's tries."""


class _Stale(Exception):
    """The map's inputs are no longer the build's: drop the build, at no cost."""


@dataclass
class Due:
    map_name: str
    manifest: dict
    replays: list          # [(replay id, match uuid, round count)], in replay order
    why: str


@dataclass
class Plan:
    due: list = field(default_factory=list)
    off: list = field(default_factory=list)         # maps whose heights are to be turned off
    skipped: dict = field(default_factory=dict)     # map -> why it is never rebuilt automatically


@dataclass
class Build:
    map_name: str
    key: str
    manifest: dict
    rounds: list                          # every (replay id, match uuid, round number) of the build
    opened_at: float
    previous: str | None = None
    todo: list = field(default_factory=list)        # the rounds not sent yet to the worker's current build
    job_id: str | None = None
    resends: int = 0                      # 404s and missing-round resends share one bounded allowance
    queued_since: float | None = None
    running_since: float | None = None


@dataclass
class HeightState:
    build: Build | None = None
    tries: dict = field(default_factory=dict)       # manifest key -> (failures, last)

    def failed(self, key: str, now: float) -> None:
        count, _ = self.tries.get(key, (0, 0.0))
        self.tries[key] = (count + 1, now)

    def may_try(self, key: str, now: float) -> bool:
        count, last = self.tries.get(key, (0, 0.0))
        return count < MAX_TRIES and (count == 0 or now - last >= BACKOFF_S)

    def spent(self, key: str) -> bool:
        return self.tries.get(key, (0, 0.0))[0] >= MAX_TRIES


def current_manifests(db) -> dict[str, tuple[dict, list]]:
    """{map: (its input manifest now, [(replay id, match uuid, round count)])} for every map with geometry and at
    least one valid replay new enough to carry heights."""
    replays = db.query(Replay).options(load_only(
        Replay.id, Replay.match_uuid, Replay.map_name, Replay.recipe, Replay.source_sha256, Replay.round_count,
        Replay.format_version)).order_by(Replay.id).all()
    valid = replay_control._valid_ids(db, replays)
    by_map: dict[str, list] = {}
    for replay in replays:
        if replay.id in valid and (replay_control.condense_revision(replay.recipe) or 0) >= hi.MIN_CONDENSE_REVISION:
            by_map.setdefault(replay.map_name, []).append(replay)
    must_block = hi.must_block_sha()
    out = {}
    for name, rows in by_map.items():
        geometry = hi.geometry_identity(name)
        if geometry is None:
            continue
        uuids = [str(r.match_uuid).lower() for r in rows]
        out[name] = (hi.manifest(name, [[u, r.recipe, r.source_sha256, r.round_count] for u, r in zip(uuids, rows)],
                                 geometry, must_block),
                     [(r.id, u, r.round_count) for u, r in zip(uuids, rows)])
    return out


def plan_maps(db) -> Plan:
    """What is due, what is to be turned off, and what is left alone (the module docstring's "Due")."""
    plan = Plan()
    now = current_manifests(db)
    last, active = control_heights.last_builds(db), control_heights.active_rows(db)
    for name in sorted(set(now) | set(active)):
        if (replay_control.geometry_inputs(name, heights=None) or {}).get("features"):
            plan.skipped[name] = "it has a published feature generation, which a rebuild would stop verifying"
            continue
        manifest, replays = now.get(name, (None, []))
        gone = bool(manifest is None or (name in active and hi.changes(active[name].inputs, manifest)["gone"]))
        if len(replays) < FIRST_BUILD_MATCHES:
            if name in active and gone:
                plan.off.append(name)          # too little left to build from, and what is active rests on what is gone
            continue
        row = last.get(name)
        if row is None:
            plan.due.append(Due(name, manifest, replays, "first build"))
        elif row.inputs_sha == hi.digest(manifest):
            if row.status == control_heights.REJECTED and gone:
                plan.off.append(name)          # the rebuild after a deletion was rejected: the old heights go
        else:
            moved = hi.changes(row.inputs, manifest)
            if moved["gone"] or gone:
                plan.due.append(Due(name, manifest, replays, "its evidence changed"))
            elif moved["other"]:
                plan.due.append(Due(name, manifest, replays, "its inputs changed"))
            elif len(moved["added"]) >= HEIGHT_REBUILD_EVERY:
                plan.due.append(Due(name, manifest, replays, f"{len(moved['added'])} new matches"))
    plan.due.sort(key=lambda d: (d.why != "its evidence changed", d.map_name))
    return plan


def _batch(session, build: Build) -> list:
    """The next rounds to send, up to BATCH_BYTES of base64; they stay in `todo` until the worker has them."""
    out, size = [], 0
    for replay_id, uuid, n in build.todo:
        row = session.get(ReplayRound, (replay_id, n))
        if row is None:
            raise _Stale(f"round {n} of {uuid} is gone")
        blob = base64.b64encode(row.data).decode("ascii")
        if len(blob) > BATCH_BYTES:
            raise BuildFailed(f"round {n} of {uuid} is {len(blob)} bytes as base64: too large to send")
        if out and size + len(blob) > BATCH_BYTES:
            break
        out.append({"match": uuid, "n": n, "blob": blob})
        size += len(blob)
    return out


def _turn_off(session_factory, map_name: str) -> bool:
    """Leaves the map without active heights, through a session of its own. The cycle's session holds the
    dispatcher's transaction-scoped lock, and `deactivate` commits: on that session it would let a second
    dispatcher in for the rest of the cycle."""
    writer = session_factory()
    try:
        return control_heights.deactivate(writer, map_name)
    except control_heights.HasGeneration as guarded:
        log.info("heights: %s", guarded)
        return False
    finally:
        writer.rollback()
        writer.close()


def _cancel(client, build: Build) -> None:
    """Best effort: a build still collecting on the worker is dropped there."""
    from app.services.replay_control_remote import Conflict, Rejected, Unreachable, WorkerBusy, WorkerGone

    if build.job_id:
        try:
            client.cancel_build(build.job_id)
        except (Conflict, Rejected, Unreachable, WorkerBusy, WorkerGone):
            pass


def _finish(session_factory, session, build: Build, job: dict, counts: dict) -> None:
    """A finished build: trusted, still wanted, then stored behind the gate."""
    if (replay_control.geometry_inputs(build.map_name, heights=None) or {}).get("features"):
        raise _Stale("a feature generation was published while this build was pending")
    result = job.get("result")
    try:
        if not isinstance(result, dict):
            raise ValueError("it has no result")
        if result.get("key") != build.key or result.get("map") != build.map_name:
            raise ValueError("it is another build's")
        if result.get("inputs_sha") != hi.digest(build.manifest):
            raise ValueError("it was built from other inputs")
        if result.get("rounds") != hi.rounds_expected(build.manifest):
            raise ValueError(f"it read {result.get('rounds')} rounds, the manifest has {hi.rounds_expected(build.manifest)}")
        digest, report, rules = result["digest"], result["report"], result["rules"]
        if not isinstance(digest, str) or len(digest) != 12 or not isinstance(rules, dict):
            raise ValueError("its digest or its rules are malformed")
        if (rules.get("version"), rules.get("revision")) != (cf.HEIGHT_VERSION, cf.HEIGHT_RULES_REVISION):
            # `rules` is what the row records and what `active_digests` reads to tell a loadable asset: a row
            # stored with another deploy's would be active, never used, and never due again.
            raise ValueError(f"it was built under height version {rules.get('version')}, rules "
                             f"{rules.get('revision')}: not this deploy's")
        asset = base64.b64decode(result["asset"], validate=True)
    except (KeyError, TypeError, ValueError) as error:              # binascii.Error is a ValueError
        raise BuildFailed(f"the worker's result can't be used: {error}") from error
    why = control_heights.integrity(digest, report, control_heights.verify_asset(asset, build.map_name), hi.must_block_sha())
    if why:
        raise BuildFailed("the worker's result can't be trusted: " + "; ".join(why))
    current = current_manifests(session).get(build.map_name)
    if current is None or hi.digest(current[0]) != hi.digest(build.manifest):
        raise _Stale("the map's inputs changed while it was built")
    writer = session_factory()
    try:
        status, reasons = control_heights.store_build(
            writer, map_name=build.map_name, digest=digest, asset=asset, inputs=build.manifest, rules=rules,
            report={**report, "seconds": result.get("seconds"), "peak": result.get("peak")})
        if status == control_heights.REJECTED:
            active = control_heights.active_rows(writer).get(build.map_name)
            if active is not None and hi.changes(active.inputs, build.manifest)["gone"]:
                control_heights.deactivate(writer, build.map_name)
                counts["heights_off"] += 1
    except control_heights.HasGeneration as guarded:
        raise _Stale(str(guarded)) from guarded
    except control_heights.Busy as busy:
        raise BuildFailed("another change to the map's heights got there first") from busy
    finally:
        writer.rollback()
        writer.close()
    counts["heights_live" if status == control_heights.ACTIVE else "heights_rejected"] += 1
    log.info("heights: %s %s %s%s", build.map_name, digest, status, f" ({'; '.join(reasons)})" if reasons else "")


def _idle(client) -> bool:
    answer = client.health()
    if not isinstance(answer, dict) or type(answer.get("idle")) is not bool:
        raise BuildFailed("the worker's health has no boolean idle field")
    return answer["idle"]


def _resend(build: Build, why: str) -> None:
    build.resends += 1
    if build.resends > RESENDS_MAX:
        raise BuildFailed(why)


def _advance(session_factory, session, client, build: Build, now: float, counts: dict) -> bool:
    """Asks the worker what state the build is in and does the one thing that state allows. True when the build
    is over (its result stored)."""
    from app.services.replay_control_remote import Conflict, Rejected, WorkerGone

    try:
        if build.job_id is None:
            # Opening a key answers the build the worker has for it, in whatever state (a restart of the web app
            # adopts it); only when there is none, or it failed, is this a fresh one to send rounds to.
            state = client.open_build(build.key, build.map_name, len(build.rounds), build.manifest)
            build.job_id = state.get("id")
            build.queued_since = build.running_since = None
            if state.get("status") == "collecting":
                build.todo = list(build.rounds)       # immutable uploads: what it already has is acknowledged for free
            else:
                build.todo = []
        else:
            state = client.build(build.job_id)        # 404: the worker forgot it (WorkerGone, bounded here before `step` spends a try)
        status = state.get("status")
        if status == "collecting":
            if now - build.opened_at > COLLECT_STALE_S:
                raise BuildFailed("its rounds could not all be sent in time")
            if not _idle(client):
                return False                          # a parse is running: sending waits, like everything else
            for _ in range(BATCHES_PER_CYCLE):
                if not build.todo:
                    break
                batch = _batch(session, build)
                try:
                    client.send_rounds(build.job_id, batch)
                except Conflict as conflict:
                    if conflict.body.get("error") == "not collecting":
                        return False                  # it was started (a start whose answer was lost): ask again
                    raise BuildFailed(f"the worker refused a round: {conflict.body.get('error')}") from conflict
                build.todo = build.todo[len(batch):]
                counts["heights_sent"] += len(batch)
            if build.todo:
                return False
            if build.previous:
                asset = control_heights.asset_bytes(session, build.map_name, build.previous)
                if asset is not None:
                    client.push_height(build.map_name, build.previous, asset)
            try:
                client.start_build(build.job_id, build.previous)
            except Conflict as conflict:
                if conflict.body.get("error") != "rounds are missing":
                    raise BuildFailed(f"the worker refused to start it: {conflict.body.get('error')}") from conflict
                _resend(build, "the worker keeps losing rounds")
                build.todo = list(build.rounds)       # immutable uploads: what it has is acknowledged for free
            return False
        if status == "queued":
            build.running_since = None
            if _idle(client):
                build.queued_since = now if build.queued_since is None else build.queued_since
                if now - build.queued_since > QUEUED_STALE_S:
                    raise BuildFailed("it sat queued on an idle worker and never started")
            else:
                build.queued_since = None             # waiting behind a parse is never a failure
            return False
        if status == "running":
            build.queued_since = None
            build.running_since = now if build.running_since is None else build.running_since
            if now - build.running_since > STALE_BUILD_S:
                raise BuildFailed("it ran too long")
            return False
        if status == "done":
            _finish(session_factory, session, build, state if "result" in state else client.build(build.job_id), counts)
            return True
        raise BuildFailed(str(state.get("error") or f"the worker says the build is {status!r}"))
    except WorkerGone:
        # Includes 404 while opening, before there is a job id. Reopening after this 404 is already charged;
        # a successful collecting response must not charge a second time for the same lost build.
        _resend(build, "the worker keeps forgetting or refusing the height-build endpoint")
        build.job_id = None
        build.queued_since = build.running_since = None
        return False
    except Rejected as refused:
        raise BuildFailed(f"the worker refused the request ({refused})") from refused


def step(session_factory, session, client, hstate: HeightState, now: float, counts: dict) -> set[str]:
    """One cycle's work on height rebuilds. Returns the maps whose rounds are held."""
    from app.services.replay_control_remote import Conflict, Unreachable, WorkerBusy

    for key in COUNTS:
        counts.setdefault(key, 0)
    plan = plan_maps(session)
    for name in plan.off:
        if _turn_off(session_factory, name):          # never on `session`: it must not commit (the cycle's lock)
            counts["heights_off"] += 1
            log.info("heights: %s off: what its heights were built from is gone", name)
    due = {hi.key(d.manifest): d for d in plan.due}
    held = {d.map_name for key, d in due.items() if not hstate.spent(key)}
    build = hstate.build
    if build is not None and build.key not in due:
        _cancel(client, build)                        # its inputs are no longer the map's
        hstate.build = build = None
        counts["heights_stale"] += 1
    attempt_key = build.key if build is not None else None
    try:
        if build is None:
            ready = [d for key, d in due.items() if hstate.may_try(key, now)]
            if not ready:
                return held
            d = ready[0]
            attempt_key = hi.key(d.manifest)  # health validation can fail before a Build is created
            if not _idle(client):
                return held
            rounds = [(rid, uuid, n) for rid, uuid, count in d.replays for n in range(1, count + 1)]
            build = hstate.build = Build(d.map_name, hi.key(d.manifest), d.manifest, rounds, opened_at=now,
                                         previous=control_heights.active_digests(session).get(d.map_name))
            log.info("heights: %s rebuild (%s), %d rounds", d.map_name, d.why, len(rounds))
        held.add(build.map_name)
        if _advance(session_factory, session, client, build, now, counts):
            hstate.build = None
            held.discard(build.map_name)
    except (Unreachable, WorkerBusy):
        counts["unreachable"] = counts.get("unreachable", 0) + 1
    except _Stale as stale:
        log.info("heights: %s build dropped: %s", build.map_name, stale)
        _cancel(client, build)
        hstate.build = None
        counts["heights_stale"] += 1
    except (BuildFailed, Conflict, AttributeError, KeyError, TypeError, ValueError) as error:
        # The last four: a worker's answer that isn't shaped as the protocol says (`state.get` on a list, a
        # missing field). Handled here, where the build can be dropped; left to `cycle`'s catch-all the build
        # would stay and meet the same answer every cycle without spending a try. A database error is none of
        # these and still reaches `cycle`, which rolls back and takes its lock again.
        log.warning("heights: %s build failed: %s: %s", build.map_name if build else "?", type(error).__name__, error)
        if build is not None:
            _cancel(client, build)
            hstate.build = None
        if attempt_key is not None:
            hstate.failed(attempt_key, now)
            if hstate.spent(attempt_key):
                held.discard(due[attempt_key].map_name)
        counts["heights_failed"] += 1
    return held
```

- [ ] **Step 6: Wire it into the cycle**

In `webapp/app/services/replay_control_remote.py`:

`State` gains the rebuild's state (importing inside the default factory avoids a cycle between the two modules):

```python
def _height_state():
    from app.services.replay_heights_remote import HeightState

    return HeightState()
```

```python
    heights: object = field(default_factory=_height_state)     # replay_heights_remote.HeightState
```

`_submit` holds the maps it is told to:

```python
def _submit(session, client, state: State, now: float, counts: dict, held=frozenset()) -> None:
```

and directly after `todo` is first built from `plan`:

```python
    todo = [p for p in todo if p.map_name not in held]       # a map whose heights are being rebuilt waits for them
```

and at the very end of the function, so a plan that found only held rounds doesn't start the idle throttle:

```python
    if held:
        state.last_found = True
```

`cycle` runs the rebuild step between collecting and submitting, and nothing it does can stop the rest:

```python
        _collect(session_factory, session, client, state, now, counts)
        held = frozenset()
        if replay_heights_remote.enabled():
            try:
                held = frozenset(replay_heights_remote.step(session_factory, session, client, state.heights, now, counts))
            except Exception:  # noqa: BLE001 - a height rebuild must never stop map control
                log.exception("height rebuild step failed")
                session.rollback()
                counts["heights_error"] = 1
                if session.get_bind().dialect.name == "postgresql":      # the rollback released the cycle's lock
                    if not session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_ID}).scalar():
                        return counts
        _submit(session, client, state, now, counts, held)
        return counts
```

with `from app.services import replay_heights_remote` among the imports (it imports this module's exceptions inside its functions, so the import order is safe).

Extend the module docstring with a step between Collect and Submit: "**Rebuild heights** (app/services/replay_heights_remote.py, when REPLAY_HEIGHTS_AUTO is set): a map whose heights are due for a rebuild has its rounds held back while the worker builds them, so parse uploads come first, then height rebuilds, then missing rounds, then stale ones."

In `webapp/tests/replays/test_control_isolation.py`, add `WEBAPP / "app" / "services" / "replay_heights_remote.py"` to the same list as in Task 2.

- [ ] **Step 7: Run the tests**

Run: `PY -m pytest tests/replays/test_heights_remote.py tests/replays/test_control_remote.py tests/replays/test_control_isolation.py -q -p no:cacheprovider`
Expected: all pass. These tests start real child processes; the whole file takes a minute or two.

The tests step the clock by 400 s a cycle because a failed try may only be repeated after `BACKOFF_S`; if a
"spends the budget" test stops short of `MAX_TRIES`, check that before anything else. If a test of the worker's
states fails here but passes in `test_replay_worker_heights.py`, the dispatcher is assuming something about a
state: read the table at the top of Task 6, not the test.

- [ ] **Step 8: Commit**

```bash
git add webapp/app/config.py webapp/app/services/replay_heights_remote.py webapp/app/services/replay_control_remote.py webapp/tests/replays/test_heights_remote.py webapp/tests/replays/test_control_isolation.py
git commit -F <message file>
```

Message: `Heights: the dispatcher rebuilds a map's heights when its inputs call for it, resumes a build after a restart, and activates only a result that proves itself`.

---

### Task 8: A tagged floor follows its floor across rebuilds; a published generation holds its map

The spec's section 5 (the owner's P5) wants a tagged feature to persist across rebuilds and follow the floor.
Two things stand in the way, and this task settles one and guards the other.

- **The band's frame moves.** A floor binding's `z_band` is "metres above the map's lowest floor" of the asset
  it was read from, and `Geometry.node_z` is in the current asset's frame. A rebuild that finds lower ground
  moves the origin, and the same numbers then name another height (review finding 10). So a binding records
  its source asset's origin (`origin_z`, world decimetres), and the band is **rebased** to the current asset's
  origin before it is used. A binding with a band but no recorded origin is in an unknown frame: it binds only
  while the map still has the asset it was read from, and is pending after that.
- **A published generation names the height digest.** `features.manifest` hashes `geo.height_sha` and the
  compiled node sets, so after any rebuild `verify_features` refuses the generation and every round of the map
  fails (review finding 11). This task does **not** fix that. It keeps Task 7's guard (such a map is never
  rebuilt automatically), makes `activate` and `off` refuse it too, and pins the limitation with a test.
  **P5 is delivered for bindings only**; what remains is in "After this plan", and in the spec change of Task 10.

**Files:**
- Modify: `webapp/app/control/features.py` (`floor_nodes`, new `_band_shift`, `bundle_status`, `diagnose`, the module docstring's "Floors" bullet)
- Modify: `webapp/app/replays/map_feature_schema.py` (`_floor`), `webapp/scripts/control_tagger_core.js` (its twin), `webapp/scripts/control_tagger_features.js` (a new floor records its origin)
- Modify: `webapp/app/control/height_job.py` (`features_pending`)
- Modify: `webapp/app/services/control_heights.py` (`activate`, `deactivate` refuse a map with a generation)
- Modify: `docs/superpowers/specs/2026-10-04-map-features-contract.md`
- Test: `webapp/tests/replays/test_control_features.py`, `test_map_feature_schema.py`, `test_map_feature_tagger.py`, `test_control_height_job.py`, `test_control_heights_db.py`

**Interfaces:**
- Consumes: `Geometry.node_z` (metres above the current asset's `origin_z`), `HeightAsset.origin_z` (world dm), `features.state_problems(geo, mf, feature) -> list[str]`, `geometry.load_tags(asset_dir)`, `replay_control.geometry_inputs(map, heights=None)["features"]`.
- Produces:
  - A floor binding is `{"id", "label", "z_band": [lo, hi] | None, "height_sha", "origin_z": int | None}`: `z_band` in metres above `origin_z`, the lowest floor of the asset it was read from, in world decimetres.
  - `features._band_shift(geo, binding) -> float | None`: metres to add to the band to read it in `geo`'s frame; None when the binding's frame is unknown.
  - `features.diagnose` codes: `rebound_floor` (information: read from another asset, rebased), `unframed_floor` (the frame is unknown and the asset has changed: pending). `stale_floor` is gone.
  - Schema warning `unframed_floor` for a banded floor without an integer `origin_z`, in Python and in the tagger's JS.
  - `height_job.features_pending(map_name, geo, asset_dir=None) -> [{"feature": id, "problems": [...]}]`, stored in a build's report under `features`.
  - `control_heights.activate` / `deactivate` take `generation: str | None = None` and refuse when it is set.

- [ ] **Step 1: Write the failing tests**

In `webapp/tests/replays/test_control_features.py`, `floors_for(geo)` builds the fixture floors. Give the first four the origin they were read under, and split the old "stale" case in two:

```python
def floors_for(geo):
    origin = geo.heights.origin_z
    return [{"id": "floor-1", "label": "ground", "z_band": [-0.5, 1.0], "height_sha": geo.height_sha, "origin_z": origin},
            {"id": "floor-2", "label": "bridge", "z_band": [3.0, 5.0], "height_sha": geo.height_sha, "origin_z": origin},
            {"id": "floor-3", "label": "manual", "z_band": None, "height_sha": None},
            {"id": "floor-4", "label": "both", "z_band": [-1.0, 5.0], "height_sha": geo.height_sha, "origin_z": origin},
            # read from an earlier asset whose origin was recorded: rebased, and it binds
            {"id": "floor-5", "label": "rebound", "z_band": [-0.5, 1.0], "height_sha": "000000000000", "origin_z": origin},
            # read from an earlier asset, origin unknown: nobody can say what its numbers mean now
            {"id": "floor-6", "label": "unframed", "z_band": [-0.5, 1.0], "height_sha": "000000000000"}]
```

Then, in the five places that used `floor-5` as the stale binding, use `floor-6` (it is pending, as `floor-5` was), and rename "stale" to "unframed" in their comments and in `cases`:

1. the loop under `# never every floor by default: ...`: `for floors in (None, [], ["floor-3"], ["floor-6"], ["floor-4"]):`
2. `test_diagnose_flags_diagonal_leaks_off_ground_endpoints_and_stale_floors`: rename it `..._and_floors_read_from_another_asset`; its last assertion becomes `assert got == {("floor-5", "rebound_floor"), ("floor-6", "unframed_floor")}`
3. the sight-occluder loop: `door_mf(geo, "floor-6", 0, 3)` in place of `floor-5`
4. the `bundle_status` loop: `((on_floor, "floor-6", ["floor-1"]), "not verified")`
5. `test_a_bundle_whose_states_bind_no_floor_publishes_nothing`: `"unframed": ["floor-6"],`

and append:

```python
# ---------------------------------------------------------------- a floor binding across a height rebuild
# (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 5)


def deeper_bridge():
    """bridge() after a rebuild found ground 2 m lower in the far corner: every floor is where it was in the
    world, and the map's lowest floor, the frame `node_z` is measured from, is 2 m lower."""
    return toy_heights("BridgeDeeper", [HALL], ground=[((96, 96, 240, 296), 4.0), ((400, 280, 416, 296), -2.0)],
                       upper=[((240, 96, 288, 296), 4.0)])


def test_a_band_is_rebased_when_a_rebuild_moves_the_maps_lowest_floor():
    old, new = bridge(), deeper_bridge()
    assert old.heights.origin_z - new.heights.origin_z == 20 and old.height_sha != new.height_sha
    door = rect(256, 160, 264, 232)
    cells = np.flatnonzero(grid_rect(256, 160, 264, 232).ravel())
    read_on_old = floors_for(old)                        # what the tagger saved before the rebuild
    for name, floor, level in (("ground", "floor-1", 0), ("bridge", "floor-2", 1)):
        mf = {**ms.empty(), "floors": read_on_old, "features": [feature("feature-1", door, [floor])]}
        before, after = cf.movement_blocks(old, mf), cf.movement_blocks(new, mf)
        assert before.pending == [] and after.pending == [], name
        assert sorted(old.node_cell[np.flatnonzero(before.blocked)]) == sorted(cells.tolist()), name
        assert sorted(new.node_cell[np.flatnonzero(after.blocked)]) == sorted(cells.tolist()), name
        assert (np.flatnonzero(after.blocked) == new.node_of[cells, level]).all(), "the same physical floor"
    # the same numbers read in the new frame without rebasing would be wrong: the ground band [-0.5, 1.0] holds
    # no floor of these cells there (the ground is at 2.0 m, the bridge at 6.0 m)
    assert not ((new.node_z[new.node_of[cells, 0]] >= -0.5) & (new.node_z[new.node_of[cells, 0]] <= 1.0)).any()
    assert cf._band_shift(new, read_on_old[0]) == pytest.approx(2.0) and cf._band_shift(old, read_on_old[0]) == 0.0


def test_a_band_with_no_recorded_origin_binds_only_while_its_asset_is_the_maps():
    old, new = bridge(), deeper_bridge()
    door = rect(256, 160, 264, 232)
    legacy = [{"id": "floor-1", "label": "ground", "z_band": [-0.5, 1.0], "height_sha": old.height_sha}]
    mf = {**ms.empty(), "floors": legacy, "features": [feature("feature-1", door, ["floor-1"])]}
    assert cf.movement_blocks(old, mf).pending == [] and cf.movement_blocks(old, mf).blocked.any()
    after = cf.movement_blocks(new, mf)
    assert not after.blocked.any() and any("origin" in p for p in after.pending), "pending, and it says why"
    assert {(d["where"], d["code"]) for d in cf.diagnose(new, {**ms.empty(), "floors": legacy})} == \
        {("floor-1", "unframed_floor")}
    assert cf._band_shift(new, legacy[0]) is None


def test_a_floor_that_moved_out_of_its_band_is_pending_and_the_rest_still_bind():
    old = bridge()
    lowered = toy_heights("BridgeLower", [HALL], ground=[((96, 96, 240, 296), 4.0)], upper=[((240, 96, 288, 296), 1.5)])
    door = rect(256, 160, 264, 232)
    mf = {**ms.empty(), "floors": floors_for(old),
          "features": [feature("feature-1", door, ["floor-1"]), feature("feature-2", door, ["floor-2"])]}
    assert cf.state_problems(lowered, mf, mf["features"][0]) == [], "the ground is where it was"
    assert cf.state_problems(lowered, mf, mf["features"][1]), "the bridge is 2.5 m lower now: out of its band"


def test_a_published_generation_does_not_survive_a_new_height_digest():
    # The limitation this plan leaves, pinned: a generation's manifest names the heights it was compiled
    # against, so the same definitions on rebuilt heights are another generation. Until generations can be
    # recompiled, a map that has one is never rebuilt automatically (tests/replays/test_heights_remote.py) and
    # `activate` refuses it (tests/replays/test_control_heights_db.py).
    old, new = bridge(), deeper_bridge()
    test = frozenset({"test"})
    block = rect(256, 160, 264, 232)
    f = breakable("feature-1", block, remove=block, floors=["floor-1"])
    mf = {**ms.empty(), "floors": floors_for(old), "features": [f],
          "bundles": [{"id": "bundle-2", "members": ["feature-1"], "enabled": True, "runtime_consumer": "test"}]}
    before, after = cf.manifest(old, mf, consumers=test), cf.manifest(new, mf, consumers=test)
    assert before is not None and after is not None, "the bundle is publishable on both: its floor rebased"
    assert before["height"] == old.height_sha and after["height"] == new.height_sha
    assert cf.manifest_digest(before) != cf.manifest_digest(after)
    assets = cf.compile_assets(old, mf, cf.bundle_status(old, mf, consumers=test))
    assert cf.verify(before, assets, new, mf, consumers=test), "verification of the old generation on the new heights fails"
```

(`toy_heights`, `HALL`, `breakable`, `rect`, `grid_rect`, `feature` and `bridge` are that file's own helpers. If `features.verify`'s signature there differs from `verify(expected, assets, geo, mf, legacy, consumers)`, call it the way the file's other tests do: the assertion is that it returns problems.)

Append to `webapp/tests/replays/test_map_feature_schema.py`, in the style of its other validation tests (it has a helper that returns a report's `(where, code)` pairs; use it):

```python
def test_a_banded_floor_without_its_assets_origin_is_warned_about():
    mf = {**ms.empty(), "floors": [
        {"id": "floor-1", "label": "ground", "z_band": [-0.5, 1.0], "height_sha": "a" * 12, "origin_z": -120},
        {"id": "floor-2", "label": "old", "z_band": [-0.5, 1.0], "height_sha": "a" * 12},
        {"id": "floor-3", "label": "odd", "z_band": [-0.5, 1.0], "height_sha": "a" * 12, "origin_z": "low"}]}
    report = ms.validate(mf)
    codes = {(w["where"], w["code"]) for w in report["warnings"]}
    assert ("floor-2", "unframed_floor") in codes and ("floor-3", "unframed_floor") in codes
    assert not any(where == "floor-1" for where, _ in codes)
```

(If `ms.validate` returns its warnings under another shape, read them the way the test above it does.)

Append to `webapp/tests/replays/test_map_feature_tagger.py`:

```python
def test_a_floor_picked_in_the_tagger_records_the_assets_origin():
    source = (WEBAPP / "scripts" / "control_tagger_features.js").read_text(encoding="utf-8")
    assert "height_sha: fd.height_sha, origin_z: fd.origin_z" in source
    core = (WEBAPP / "scripts" / "control_tagger_core.js").read_text(encoding="utf-8")
    assert '"unframed_floor"' in core
```

(a source check, weaker than a run: the floor picker has no node-side entry point. If that file's helpers can
drive the picker, replace this with a run that picks a floor and asserts the saved object carries `origin_z`.)

Append to `webapp/tests/replays/test_control_height_job.py`:

```python
def test_a_build_lists_the_tagged_features_that_no_longer_fit(tmp_path, monkeypatch):
    from app.control import features
    from tests.replays.control_toys import HALL, toy_heights

    geo = toy_heights("FeatJob", [HALL], upper=[((240, 96, 288, 296), 4.0)])     # a bridge 4 m over the ground
    assets = tmp_path / "assets"
    assets.mkdir()

    def tags(floors):
        mf = {"version": 1, "floors": floors,
              "features": [{"id": "feature-1", "floors": ["floor-1"]}, {"id": "feature-2", "floors": ["floor-2"]}]}
        (assets / "tags.json").write_text(json.dumps({"maps": {"FeatJob": {"map_features": mf}}}), encoding="utf-8")

    seen = []
    monkeypatch.setattr(features, "state_problems",
                        lambda g, mf, f: seen.append(f["id"]) or (["no floor in its band"] if f["id"] == "feature-2" else []))
    tags([{"id": "floor-1", "z_band": [-0.5, 1.0], "height_sha": "old", "origin_z": 0}, {"id": "floor-2", "z_band": [9, 10]}])
    assert height_job.features_pending("FeatJob", geo, assets) == [{"feature": "feature-2",
                                                                    "problems": ["no floor in its band"]}]
    assert seen == ["feature-1", "feature-2"]
    (assets / "tags.json").write_text(json.dumps({"maps": {}}), encoding="utf-8")
    assert height_job.features_pending("FeatJob", geo, assets) == []
    (assets / "tags.json").unlink()
    assert height_job.features_pending("FeatJob", geo, assets) == [], "no tags file: nothing to re-read"
```

Append to `webapp/tests/replays/test_control_heights_db.py`:

```python
def test_a_map_with_a_published_feature_generation_cant_have_its_heights_changed_by_hand(db, linked):
    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa")
    build(db, name, "bbbbbbbbbbbb")
    refused = ch.activate(db, name, "aaaaaaaaaaaa", generation="feat0000feat0000")
    assert "feature generation" in refused and ch.active_digests(db) == {name: "bbbbbbbbbbbb"}
    with pytest.raises(ch.HasGeneration):
        ch.deactivate(db, name, generation="feat0000feat0000")
    assert ch.active_digests(db) == {name: "bbbbbbbbbbbb"}
```

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_features.py tests/replays/test_map_feature_schema.py tests/replays/test_map_feature_tagger.py tests/replays/test_control_height_job.py tests/replays/test_control_heights_db.py -q -p no:cacheprovider`
Expected: the new tests fail (`module 'app.control.features' has no attribute '_band_shift'`, the `floor-5` cases still pending, no `unframed_floor`), and both published-generation tests already pass: generation incompatibility is true today, and the manual guard was installed in Task 2.

- [ ] **Step 3: The binding rule**

In `webapp/app/control/features.py`:

Add before `floor_nodes`:

```python
def _band_shift(geo: Geometry, binding: dict) -> float | None:
    """Metres to add to a binding's `z_band` to read it in `geo`'s frame. A band is in metres above the lowest
    floor of the asset it was read from (`origin_z`, world dm); `geo.node_z` is above the lowest floor of the
    asset the map has now, and a rebuild that finds lower ground moves that. None when the binding doesn't say
    where its frame was and the map's asset is no longer the one it was read from: its numbers can't be read."""
    origin = binding.get("origin_z")
    if isinstance(origin, (int, float)) and not isinstance(origin, bool):
        return (origin - geo.heights.origin_z) / 10.0
    return 0.0 if binding.get("height_sha") == geo.height_sha else None
```

In `floor_nodes`, replace the `height_sha` check and the line that unpacks the band:

```python
    shift = _band_shift(geo, binding)
    if shift is None:
        return Binding(np.zeros(0, np.int64), [f"floor {binding.get('id')!r} was read from height asset "
                                               f"{binding.get('height_sha')!r} and doesn't record its origin; the "
                                               f"map has {geo.height_sha!r}: read the floor again in the tagger"])
    lo, hi = binding["z_band"][0] + shift, binding["z_band"][1] + shift
```

`bundle_status`: the verification line becomes

```python
                    if not isinstance(fl.get("z_band"), list) or _band_shift(geo, fl) is None:
```

`diagnose`: the stale-floor entry becomes two:

```python
        if fl.get("z_band") is not None and geo.heights is not None and fl.get("height_sha") != geo.height_sha:
            if _band_shift(geo, fl) is None:
                out.append({"where": fl.get("id"), "code": "unframed_floor",
                            "message": f"read from height asset {fl.get('height_sha')!r} with no origin recorded; "
                                       f"the map has {geo.height_sha!r}: read the floor again"})
            else:
                out.append({"where": fl.get("id"), "code": "rebound_floor",
                            "message": f"read from height asset {fl.get('height_sha')!r}; it follows the map's "
                                       f"{geo.height_sha!r} by its band, rebased to the new lowest floor"})
```

In the module docstring's **Floors** bullet, replace the sentence about unbanded, stale or ambiguous bindings with: "A binding is {`z_band`: [lo, hi] metres above `origin_z`, the lowest floor (world dm) of the asset it was read from; `height_sha`: that asset}. The band is rebased to the map's current lowest floor before it is used, so a feature follows its floor when the heights are rebuilt (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 5). On a map with a height asset an unbanded or ambiguous binding, or one whose frame is unknown (no `origin_z`, and another asset than the map's), is pending and blocks nothing".

- [ ] **Step 4: The schema and the tagger record the origin**

In `webapp/app/replays/map_feature_schema.py`, `_floor`, after the `unbound_floor` branch:

```python
    elif not (type(fl.get("origin_z")) is int):
        rep.warn(fid, "unframed_floor", "a height band without the lowest floor (origin_z) of the asset it was read "
                                        "from: it stops binding when the map's heights are rebuilt")
```

In `webapp/scripts/control_tagger_core.js`, the same branch after the `unbound_floor` line:

```javascript
      else if (!Number.isInteger(fl.origin_z)) warn(fl.id, "unframed_floor", "a height band without the lowest floor (origin_z) of the asset it was read from: it stops binding when the map's heights are rebuilt");
```

In `webapp/scripts/control_tagger_features.js`, the floor the picker creates records it (`fd` is the map's floor data, which already carries `origin_z`):

```javascript
      var a = F.addObject(next, "floor", { label: "floor at " + z.toFixed(1) + " m", z_band: [Math.round((z - 0.75) * 100) / 100, Math.round((z + 0.75) * 100) / 100], height_sha: fd.height_sha, origin_z: fd.origin_z });
```

and where a floor's band is shown (`"Height band"` row in that file), show the origin beside the asset: `" m (asset " + fl.height_sha + ", origin " + (Number.isInteger(fl.origin_z) ? fl.origin_z + " dm" : "not recorded") + ")"`.

`origin_z` is a runtime field (it changes what a band means), so it must reach a generation's hash: check that the list of editorial keys in `map_feature_schema.py` does not name it, and that `test_map_feature_schema.py`'s runtime-digest test still passes. If that file enumerates a floor's runtime keys, add `origin_z` there.

- [ ] **Step 5: The build re-reads the map's features, and a hand change refuses a map with a generation**

In `webapp/app/control/height_job.py`, replace `features_pending`:

```python
def features_pending(map_name: str, geo, asset_dir: Path | None = None) -> list[dict]:
    """The map's tagged features that no longer fit the new heights (section 5): a floor binding whose band,
    rebased to the new lowest floor, picks no floor or several in one of its cells, or whose frame is unknown.
    Each is listed for the owner. `geo` has the new heights attached."""
    from app.control import features

    try:
        entry = cg.load_tags(asset_dir or cg.ASSET_DIR).get("maps", {}).get(map_name) or {}
    except (OSError, ValueError):
        return []
    mf = entry.get("map_features")
    if not isinstance(mf, dict):
        return []
    out = []
    for feature in mf.get("features") or []:
        problems = features.state_problems(geo, mf, feature)
        if problems:
            out.append({"feature": feature.get("id"), "problems": problems})
    return out
```

Task 2 already installs `HasGeneration`, `GENERATION_NOTE` and the independent generation lookup under the
map lock in `store_build`, `activate` and `deactivate`; Task 7 checks it again on collection and handles it
without storing/activating the pending build. Keep those guards. The optional `generation` on the manual
functions is an additional caller hint, never a substitute for the service's current lookup.

Callers pass `(replay_control.geometry_inputs(map_name, heights=None) or {}).get("features")`: the operator's
command (Task 9) and both dispatcher `deactivate` calls, still through their own writer session. `_turn_off`
already logs `HasGeneration` and returns False; `_finish` drops the pending build as stale, without charging
a failed try. A map that acquired a generation after planning must still preserve its old active digest.

Add to the service docstring: "A map with a published feature generation can't have its heights changed, by
the dispatcher or by hand: the generation would stop verifying (the plan's undecided default E6)."

This is a final current-generation check, not a transaction shared with index.json publication. Before any
map gets a generation, revisit E6 with the owner and coordinate publication with the height writer (or build
generation compatibility). Do not claim the DB lock alone serializes a filesystem publication after its check.

- [ ] **Step 6: Amend the contract**

In `docs/superpowers/specs/2026-10-04-map-features-contract.md` (frozen; this is the amendment the spec's section 5 calls for, and it says so):

- under the title block: `Amended <date> (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 5, the owner's P5): a floor binding no longer has to be read from the map's current height asset. It records the lowest floor of the asset it was read from (origin_z) and its band is rebased to the current asset's, so a tagged feature persists across rebuilds and follows its floor; one whose rebased band stops picking exactly one floor per cell is pending and is listed in that rebuild's report. **Not amended:** section 8. A published generation still names the height digest; a map that has one is not rebuilt automatically and its heights can't be changed by hand until generations can be recompiled.`
- section 1, the Floor binding line: `` `z_band` [lo, hi] (metres of position-z above `origin_z`) or null (a manual label), `origin_z` (the lowest floor, in world decimetres, of the asset the band was read from), `height_sha` (that asset: a record, not a condition) ``.
- section 2, the floor-binding bullet: "A floor binding without a band, with a band whose frame is unknown (no `origin_z`, and another asset than the map's), or matching zero or several floors of a cell after rebasing, is pending: it binds nothing (never all floors)."
- section 4, first bullet: "Floors are bound by height bands in metres above the binding's own `origin_z`; before use a band is rebased into the `node_z` frame (position-z metres above the map's current lowest floor)."

- [ ] **Step 7: Run the tests**

Run: `PY -m pytest tests/replays/test_control_features.py tests/replays/test_control_height_job.py tests/replays/test_control_heights_db.py tests/replays/test_map_feature_schema.py tests/replays/test_map_feature_state.py tests/replays/test_map_feature_tagger.py tests/replays/test_control_tagger.py -q -p no:cacheprovider`
Expected: all pass. A schema test that pins the validation's exact warnings for a fixture with banded floors will now see `unframed_floor` for floors without `origin_z`: give those fixture floors an `origin_z` (they describe floors read from an asset) rather than loosening the test.

- [ ] **Step 8: Commit**

```bash
git add webapp/app/control/features.py webapp/app/control/height_job.py webapp/app/replays/map_feature_schema.py webapp/app/services/control_heights.py webapp/scripts/control_tagger_core.js webapp/scripts/control_tagger_features.js docs/superpowers/specs/2026-10-04-map-features-contract.md webapp/tests/replays
git commit -F <message file>
```

Message: `Map features: a floor binding records its asset's origin and follows its floor across rebuilds; a map with a published generation keeps its heights`.

---

### Task 9: The operator's commands, and a database source for the viewer and the tagger

**Files:**
- Create: `webapp/scripts/control_heights.py`
- Modify: `webapp/scripts/height_viewer.py` (`--db`, `--all`, `db_sources`, `map_payload`)
- Modify: `webapp/scripts/control_tagger.py` (`floor_data`, `--heights-dir`)
- Test: `webapp/tests/replays/test_control_heights_db.py`, `test_height_viewer.py`, `test_map_feature_tagger.py`

**Interfaces:**
- Consumes: Task 2's `control_heights.rows`, `activate`, `deactivate`, `asset_bytes`, `active_rows`, `gate`; Task 8's `generation` argument.
- Produces:
  - `control_heights.py list [--map X]`, `activate --map X --digest D`, `off --map X`, `export --map X --out <dir>`; `main(argv=None, session_factory=None) -> int` (0 done, 2 refused).
  - `export` writes the map's active asset as `<dir>/<Map>.height.npz` and `<dir>/<Map>.height.json` (`{"height_sha", "height"}`: the layout a preview has), outside the repository.
  - `height_viewer.db_sources(rows, folder, names, every) -> list[tuple[str, Path, dict, str]]`; `map_payload(name, asset_path, wrapper, kind, asset_dir=..., map_name=None)`.
  - `control_tagger.floor_data(name, asset_dir=cg.ASSET_DIR, heights_dir=None)`: with `heights_dir`, the map's heights come from `<heights_dir>/<Map>.height.npz` (an export) instead of the committed asset; `control_tagger.py --heights-dir <dir>`.

- [ ] **Step 1: Write the failing tests**

Append to `webapp/tests/replays/test_control_heights_db.py`:

```python
def test_the_operator_lists_activates_exports_and_turns_off(factory, db, linked, capsys, tmp_path, monkeypatch):
    import control_heights as command

    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa", rep=report(seconds=310.0))
    build(db, name, "bbbbbbbbbbbb", rep=report(ready=False, not_ready=["thin"]))
    assert command.main(["list"], session_factory=factory) == 0
    out = capsys.readouterr().out
    assert f"{name} aaaaaaaaaaaa active" in out and "supported 70.0%" in out and "310 s" in out
    assert f"{name} bbbbbbbbbbbb rejected" in out and "rejected: thin" in out
    assert command.main(["activate", "--map", name, "--digest", "aaaaaaaaaaaa"], session_factory=factory) == 0
    assert "recomputed" in capsys.readouterr().out, "activating what is active is done, not an error"
    db.expire_all()
    assert ch.active_digests(db) == {name: "aaaaaaaaaaaa"}
    assert command.main(["activate", "--map", name, "--digest", "000000000000"], session_factory=factory) == 2
    out_dir = tmp_path / "export"
    assert command.main(["export", "--map", name, "--out", str(out_dir)], session_factory=factory) == 0
    assert (out_dir / f"{name}.height.npz").read_bytes() == b"npz-aaaaaaaaaaaa"
    wrapper = json.loads((out_dir / f"{name}.height.json").read_text(encoding="utf-8"))
    assert wrapper["height_sha"] == "aaaaaaaaaaaa" and wrapper["height"]["supported_cells"] == 4200
    assert command.main(["export", "--map", name, "--out", str(WEBAPP / "here")], session_factory=factory) == 2
    monkeypatch.setattr(command, "generation_of", lambda map_name: "feat0000feat0000")
    assert command.main(["off", "--map", name], session_factory=factory) == 2
    assert command.main(["activate", "--map", name, "--digest", "aaaaaaaaaaaa"], session_factory=factory) == 2
    assert "feature generation" in capsys.readouterr().err
    monkeypatch.setattr(command, "generation_of", lambda map_name: None)
    assert command.main(["off", "--map", name], session_factory=factory) == 0
    db.expire_all()
    assert ch.active_digests(db) == {}
    assert command.main(["off", "--map", name], session_factory=factory) == 0 and "no active heights" in capsys.readouterr().out
    assert command.main(["export", "--map", name, "--out", str(out_dir)], session_factory=factory) == 2
```

(add `import json` to that file's imports.)

Append to `webapp/tests/replays/test_height_viewer.py`:

```python
def test_db_sources_write_the_stored_assets_for_the_page(tmp_path):
    from types import SimpleNamespace

    asset = synthetic()
    path = tmp_path / "a.npz"
    hc.save_asset(path, asset)
    data = path.read_bytes()
    report = report_for(asset)
    rows = [SimpleNamespace(id=3, map_name=MAP, digest=asset.digest, status="active", report=report, asset=data),
            SimpleNamespace(id=2, map_name=MAP, digest="0" * 12, status="rejected", report={}, asset=data),
            SimpleNamespace(id=1, map_name="Bind", digest="1" * 12, status="superseded", report={}, asset=data)]
    got = height_viewer.db_sources(rows, tmp_path / "out", None, every=False)
    assert [(name, kind) for name, _, _, kind in got] == [(MAP, "active")]
    name, written, wrapper, _ = got[0]
    assert written.read_bytes() == data and wrapper == {"height_sha": asset.digest, "height": report}
    every = height_viewer.db_sources(rows, tmp_path / "out", [MAP], every=True)
    assert [(name, kind) for name, _, _, kind in every] == [(MAP, "active"), (f"{MAP} rejected {'0' * 12} row 2", "rejected")]
    label = f"{MAP} rejected {'0' * 12} row 2"
    payload = height_viewer.map_payload(label, every[1][1], every[1][2], "rejected", map_name=MAP)
    assert payload["name"] == label and payload["kind"] == "rejected" and payload["height_sha"] == asset.digest


def test_db_history_keeps_distinct_rows_with_the_same_engine_digest(tmp_path):
    from types import SimpleNamespace

    current = synthetic()
    older = synthetic()
    older.kind[current.supported] = hc.KIND_WALKS
    assert current.digest == older.digest  # kind is deliberately outside the engine hash
    rows = []
    for row_id, status, asset in ((3, "active", current), (2, "rejected", older), (1, "rejected", current)):
        path = tmp_path / f"{row_id}.npz"
        hc.save_asset(path, asset)
        rows.append(SimpleNamespace(id=row_id, map_name=MAP, digest=asset.digest, status=status,
                                    report={"row": row_id}, asset=path.read_bytes()))
    got = height_viewer.db_sources(rows, tmp_path / "out", None, every=True)
    assert len({label for label, _, _, _ in got}) == len({path for _, path, _, _ in got}) == 3
    assert got[0][0] == MAP
    for row, (_, written, wrapper, _) in zip(rows, got):
        assert written.read_bytes() == row.asset and wrapper["height"] == {"row": row.id}
    assert np.array_equal(hc.load_asset(got[0][1]).kind, current.kind)
    assert np.array_equal(hc.load_asset(got[1][1]).kind, older.kind)

```

Append to `webapp/tests/replays/test_map_feature_tagger.py`:

```python
def test_the_tagger_reads_a_maps_heights_from_an_export_when_given_one(tmp_path):
    from app.control import heights as hc

    import control_tagger

    floors = np.full((128, 128, hc.MAX_FLOORS), -1, np.int16)
    floors[40, 40, 0] = 7
    asset = hc.HeightAsset(floors, np.zeros_like(floors), floors[..., 0] >= 0, np.zeros((128, 128), bool),
                           np.zeros((0, 5), np.int32), {"origin_z": -130})
    hc.save_asset(tmp_path / "Toy.height.npz", asset)
    (tmp_path / "index.json").write_text(json.dumps({"maps": {"Toy": {}}}), encoding="utf-8")
    assert control_tagger.floor_data("Toy", tmp_path) is None, "no committed heights"
    got = control_tagger.floor_data("Toy", tmp_path, heights_dir=tmp_path)
    assert got["height_sha"] == asset.digest and got["origin_z"] == -130
    assert control_tagger.floor_data("Other", tmp_path, heights_dir=tmp_path) is None
```

(use that file's existing imports for `np`, `json` and `control_tagger` if it has them.)

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_heights_db.py tests/replays/test_height_viewer.py tests/replays/test_map_feature_tagger.py -q -p no:cacheprovider -k "operator or db_sources or export"`
Expected: FAIL, `No module named 'control_heights'`, `module 'height_viewer' has no attribute 'db_sources'`, `floor_data() got an unexpected keyword argument 'heights_dir'`.

- [ ] **Step 3: The command**

Create `webapp/scripts/control_heights.py`:

```python
"""The map heights the replay worker built: look at them, put an earlier one back, turn a map's off, or write
the active one to a folder (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 4).

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\control_heights.py list [--map Sunset]
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\control_heights.py export --map Sunset --out %TEMP%\\valo-replay\\heights-db
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb scripts\\control_heights.py activate --map Sunset --digest 0123456789ab
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb scripts\\control_heights.py off --map Sunset

`list` prints every build in `control_heights`, newest first: its map, digest, status, when it was built, from
how many matches, its supported share, both checks, how long it took, and for a rejected one why.
`activate` makes an earlier build the map's active heights (activating the one that is active changes nothing);
`off` leaves the map with none (flat). Either way the map's stored rounds turn stale and the replay worker
recomputes them when it is idle; the pages keep showing the stored ones, marked out of date, until then.
Neither stops the next automatic rebuild: for that, unset REPLAY_HEIGHTS_AUTO on the web service. Both refuse a
map with a published feature generation, which names the heights it was compiled against.
`export` writes the map's active asset and its report to a folder outside the repository, in the layout of a
preview, for `height_viewer.py --dir` and `control_tagger.py --heights-dir`.

Exits 0 when done, 2 when refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def generation_of(map_name: str) -> str | None:
    """The map's published feature generation (index.json `features_sha`), if it has one."""
    from app.services import replay_control

    return (replay_control.geometry_inputs(map_name, heights=None) or {}).get("features")


def describe(row) -> str:
    from app.services import control_heights

    report = row.report or {}
    kills, must = report.get("kill_lines") or {}, report.get("must_block") or {}
    parts = [f"{row.map_name} {row.digest} {row.status}", f"built {row.built_at:%Y-%m-%d %H:%M}" if row.built_at else "",
             f"{len(row.match_uuids or [])} matches", f"inputs {row.inputs_sha}",
             f"supported {report['supported']:.1%}" if isinstance(report.get("supported"), float) else "",
             f"kill lines {kills.get('blocked', '?')}/{kills.get('qualifying', '?')}",
             f"must-block {'pass' if must.get('passes') else 'FAIL'}",
             f"{report['seconds']:.0f} s" if isinstance(report.get("seconds"), (int, float)) else "",
             f"height version {(row.rules or {}).get('version')}, rules {(row.rules or {}).get('revision')}"]
    if row.status == control_heights.REJECTED:
        try:
            parts.append("rejected: " + "; ".join(control_heights.gate(report)))
        except (KeyError, TypeError, ZeroDivisionError):
            parts.append("rejected")
    if report.get("features"):
        parts.append(f"{len(report['features'])} tagged feature(s) no longer fit: "
                     + ", ".join(str(f.get("feature")) for f in report["features"]))
    return "; ".join(p for p in parts if p)


def inside_a_repository(path: Path) -> bool:
    out = path.resolve()
    return out.is_relative_to(WEBAPP_ROOT.parent.resolve()) or any((f / ".git").exists() for f in (out, *out.parents))


def main(argv: list[str] | None = None, session_factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list").add_argument("--map")
    activate = commands.add_parser("activate")
    activate.add_argument("--map", required=True)
    activate.add_argument("--digest", required=True)
    commands.add_parser("off").add_argument("--map", required=True)
    export = commands.add_parser("export")
    export.add_argument("--map", required=True)
    export.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    from app.services import control_heights

    if session_factory is None:
        from app.db import SessionLocal as session_factory
    session = session_factory()
    try:
        if args.command == "list":
            found = control_heights.rows(session, args.map)
            for row in found:
                print(describe(row), flush=True)
            if not found:
                print("no heights have been built" + (f" for {args.map}" if args.map else ""), flush=True)
            return 0
        if args.command == "export":
            if inside_a_repository(args.out):
                print(f"REFUSED: --out {args.out} is inside a repository", file=sys.stderr)
                return 2
            row = control_heights.active_rows(session).get(args.map)
            data = None if row is None else control_heights.asset_bytes(session, args.map, row.digest)
            if data is None:
                print(f"REFUSED: {args.map} has no active heights in the database", file=sys.stderr)
                return 2
            report = next(r.report for r in control_heights.rows(session, args.map) if r.id == row.id)
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / f"{args.map}.height.npz").write_bytes(data)
            (args.out / f"{args.map}.height.json").write_text(
                json.dumps({"height_sha": row.digest, "height": report}, indent=1) + "\n", encoding="utf-8")
            print(f"{args.map}: {row.digest} written to {args.out}", flush=True)
            return 0
        generation = generation_of(args.map)
        if args.command == "activate":
            why = control_heights.activate(session, args.map, args.digest, generation=generation)
            if why:
                print(f"REFUSED: {why}", file=sys.stderr)
                return 2
            print(f"{args.map}: {args.digest} is active. Rounds computed with other heights are stale and will be "
                  f"recomputed when the replay worker is idle.", flush=True)
            return 0
        try:
            had = control_heights.deactivate(session, args.map, generation=generation)
        except control_heights.HasGeneration as refused:
            print(f"REFUSED: {refused}", file=sys.stderr)
            return 2
        print(f"{args.map}: heights off; its rounds are stale and will be recomputed flat." if had
              else f"{args.map}: no active heights; nothing changed.", flush=True)
        return 0
    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: The viewer reads the database**

In `webapp/scripts/height_viewer.py`, add after `sources`:

```python
def db_sources(rows, folder: Path, names: list[str] | None, every: bool) -> list[tuple[str, Path, object, str]]:
    """`sources` for the builds stored in `control_heights` (rows carry id, map_name, digest, status, report and
    asset): each map's active build, and with `every` the rejected and superseded ones too, in the order given.
    Each asset is written under `folder` (a temp folder, never the repository) for `map_payload` to read.
    The page calls the active one by its map's name and the others `<Map> <status> <digest> row <id>`. The row id also names the temp file: two builds can
    share an engine digest while their kind, support metadata and reports differ."""
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for row in rows:
        if names and row.map_name not in names:
            continue
        if row.status != "active" and not every:
            continue
        path = folder / f"{row.map_name}.{row.id}.{row.digest}.height.npz"
        path.write_bytes(bytes(row.asset))
        label = row.map_name if row.status == "active" else f"{row.map_name} {row.status} {row.digest} row {row.id}"
        out.append((label, path, {"height_sha": row.digest, "height": row.report or {}}, row.status))
    return out
```

`map_payload` reads the geometry and the minimap by the map's real name; a label such as `Sunset rejected 0123...` is not one. Add a parameter `map_name: str | None = None` after `asset_dir`, set `real = map_name or name` at its top, and use `real` everywhere the function reads a file or `maps.json` (the two mask paths, the `xMultiplier` lookup, `geometry_from_masks`, the minimap image); `"name": name` in the payload stays the label.

In `main`:

```python
    parser.add_argument("--db", action="store_true",
                        help="show the heights stored in the database (run through with_friends_db.py --read-only)")
    parser.add_argument("--all", action="store_true", help="with --db: the rejected and superseded builds too")
```

(`parser.error("--all is for --db only")` when `args.all and not args.db`), and where the sources are gathered, replace the single `sources(...)` call with:

```python
    real = {}
    if args.db:
        from app.db import SessionLocal
        from app.models.replay import ControlHeight

        session = SessionLocal()
        try:
            stored = session.query(ControlHeight) \
                .order_by(ControlHeight.map_name, ControlHeight.built_at.desc(), ControlHeight.id.desc()).all()
            found = db_sources(stored, TEMP / "valo-height-viewer" / "db", args.map, args.all)
            real = {label: row.map_name for row in stored
                    for label in (row.map_name, f"{row.map_name} {row.status} {row.digest}")}
        finally:
            session.rollback()
            session.close()
    else:
        found = sources(args.dir, args.committed, args.map, asset_dir)
    for name, path, wrapper, kind in found:
        try:
            payload = map_payload(name, path, wrapper, kind, asset_dir, real.get(name))
```

The "nothing to show" message gains a third case: `"no heights are stored in the database"` when `args.db`.

In the module docstring, add: "With `--db` it reads the heights the replay worker built, from `control_heights`: each map's active asset, and with `--all` the rejected and superseded ones too. Run that through `scripts\with_friends_db.py --expect-database valowithfriendsdb --read-only`; it reads only."

- [ ] **Step 5: The tagger reads an export**

In `webapp/scripts/control_tagger.py`, `floor_data` takes a folder of exported heights, which wins over the committed asset:

```python
def floor_data(name: str, asset_dir: Path = cg.ASSET_DIR, heights_dir: Path | None = None) -> dict | None:
    """The map's height asset for the floor picker: each cell's floors (position-z dm above the lowest floor, -1
    none) as base64 int16, with the digest and the origin a floor binding records. From `heights_dir` when it
    holds `<Map>.height.npz` (the active asset, written by `control_heights.py export`: a map's heights live in
    the database once the replay worker builds them), else the committed asset. None for a map without heights:
    its floors can only be manual labels, unresolved until an asset exists."""
    exported = None if heights_dir is None else Path(heights_dir) / f"{name}.height.npz"
    if exported is not None and exported.is_file():
        asset = hc.load_asset(exported)
    else:
        index_path = asset_dir / "index.json"
        entry = (json.loads(index_path.read_text(encoding="utf-8")).get("maps", {}).get(name) or {}) if index_path.is_file() else {}
        if not entry.get("height_sha"):
            return None
        asset = hc.load_asset(asset_dir / f"{name}.height.npz")
    return {"height_sha": asset.digest, "origin_z": asset.origin_z, "max_floors": hc.MAX_FLOORS,
            "floors": base64.b64encode(np.ascontiguousarray(asset.floors, dtype="<i2").tobytes()).decode("ascii")}
```

`features_data(names)` gains `heights_dir=None` and passes it (`floor_data(n, heights_dir=heights_dir)`), and the command gains `--heights-dir` (a `Path`, default None) handed down to it: follow how the script passes its other options to `build`. Say in its docstring: "`--heights-dir <dir>`: read each map's heights from an export of the database's active asset (`control_heights.py export`) instead of a committed one."

- [ ] **Step 6: Run the tests**

Run: `PY -m pytest tests/replays/test_control_heights_db.py tests/replays/test_height_viewer.py tests/replays/test_map_feature_tagger.py tests/replays/test_control_tagger.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add webapp/scripts/control_heights.py webapp/scripts/height_viewer.py webapp/scripts/control_tagger.py webapp/tests/replays
git commit -F <message file>
```

Message: `Heights: list, activate, off and export for the operator; the viewer and the tagger read the stored assets`.

---

### Task 10: Spec changes and deploy notes

**Files:**
- Modify: `docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md`
- Modify: `docs/map-control-worker-plan.md`, `webapp/RENDER_DEPLOY.md`, `replay_worker/README.md`
- Modify: `CLAUDE.md` **and** `AGENTS.md` (the same change to both: `AGENTS.md` mirrors `CLAUDE.md`)

**Interfaces:** none (documents only).

- [ ] **Step 1: Bring the spec in line with what was built**

In `docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md`. Each item is a change to an approved spec:
write it as a dated amendment naming the plan's decision, and leave the owner's own decisions (P1 to P8) as they are.

- **Section 1**, the table: add `inputs` (the build's input manifest, `app/replays/height_inputs.py`) and `inputs_sha` (its digest). `rules` holds the format, the rules' revision and the constants' hash. Say that `match_uuids` are the manifest's matches. (Decision E2.)
- **Section 2**: the input is "every stored round of the map's valid replays at condenser revision 11 or later, as the input manifest names them"; the child refuses a manifest it would not have computed itself; the must-block check fails when its file can't be read. (E2, E4.)
- **Section 3**, "A rebuild is due": replace the bullet with the four cases of `plan_maps` (no build yet; same inputs; evidence gone or inputs changed; matches added), and add the worker's five build states and that the dispatcher resumes from whichever it finds. (E2, E5.)
- **Section 4**, the table: split "The build itself fails" into the failure table of the plan's Task 7; add the row "The result isn't what it says: never stored, a failed try"; add "The map's inputs changed while it built: dropped, at no cost". Add under it the two halves of the gate (integrity, then policy). (E4.)
- **Section 4**, a new paragraph on deletions, **approved by the owner 2026-10-07**: evidence that is removed makes the rebuild due at once; a rejected rebuild, or too few matches left, turns the map's heights off. Quote P3 beside it and say that P3 still holds for a build that fails with its evidence intact. (E5.)
- **Section 5**: the binding records `origin_z` and its band is rebased; a binding without one is pending once the asset changes. Then, plainly: "Not delivered: a published feature generation does not survive a rebuild. A map that has one is not rebuilt automatically, and `activate` and `off` refuse it. This narrows 'the map still goes live' above; it is the owner's to accept or to ask for generation compatibility instead; as of 2026-10-07 the owner has not decided, and no map has a published generation." (E6.)
- **Section 6**: `control_heights.py export`, and the tagger's `--heights-dir`.
- **Decisions table**: add E1 to E9 from this plan, each with the owner's answer of 2026-10-07 as the plan's own table records it (E6: "not decided; guard built as the default"). Do not write an approval the owner hasn't given.
- **Status line**: `Status: built <date> (plan: docs/superpowers/plans/2026-10-05-height-auto-rebuild.md); not turned on: REPLAY_HEIGHTS_AUTO is off until the owner sets it.`

- [ ] **Step 2: The deploy notes**

`docs/map-control-worker-plan.md`: add a section "Height rebuilds (2026-10-05)" pointing at the spec and this plan, with the order of work (parse uploads, height rebuilds, missing rounds, stale rounds), the endpoints (`POST /heights`, `/heights/build`, `/heights/build/{id}/rounds`, `/start`, `/cancel`, `GET /heights/build/{id}`), the five build states, and that the worker's cache and spool are lost on redeploy and rebuilt on demand.

`webapp/RENDER_DEPLOY.md`: under the friends service's environment, `REPLAY_HEIGHTS_AUTO` (default off; needs `REPLAY_CONTROL_REMOTE`; never set on `valomaths`). Under the worker: `REPLAY_HEIGHT_CMD` and `REPLAY_HEIGHT_TIMEOUT_S` (both have defaults in the image), and that the image must be rebuilt for this change. Then the turn-on order, which goes in the spec too:

```
1. Merge. Both web services run `alembic upgrade head` on build: the new migration creates an empty table on each.
2. Rebuild and deploy the replay worker (its image gains replay_worker/height_job.py, app/replays/height_inputs.py
   and the must-block list). The two services must be on the same commit before step 4: a worker with other
   rules, masks or check set refuses every build ("these aren't my inputs"), and three refusals spend a map's
   tries until the web app restarts.
3. Check the worker: GET /health shows "idle" and "heights".
4. Set REPLAY_HEIGHTS_AUTO=true on valowithfriendstracker only. Within a few cycles the dispatcher logs
   "heights: <Map> rebuild (first build), N rounds" and then "<Map> <digest> active" or "... rejected (...)"
   for each map with two or more matches.
5. Look: scripts\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\control_heights.py list
   and scripts\height_viewer.py --db [--all] the same way.
6. To stop: unset REPLAY_HEIGHTS_AUTO. To go back on one map: control_heights.py activate / off.
```

`replay_worker/README.md`: the build child, its endpoints and states, in the shape the control child is described there.

`CLAUDE.md` and `AGENTS.md`, the same text in both, in the `webapp/` section: "`control_heights` (migration <its number>): a map's height assets, built on the replay worker and activated behind a gate (`app/replays/height_inputs.py`, `app/services/control_heights.py`, `app/services/replay_heights_remote.py`; `REPLAY_HEIGHTS_AUTO`, default off; `scripts/control_heights.py list | activate | off | export`). The demo never has any." Update the migrations list in each if it enumerates them. Then check the two files still differ only where they did before:

Run: `git diff --no-index --stat CLAUDE.md AGENTS.md` before and after the edit.
Expected: the same lines differ before and after (today: the header, and a "Stats page caches" section `AGENTS.md` lacks). If the edit added a difference, fix it. Do not use this task to reconcile the older differences.

- [ ] **Step 3: Run the whole suite**

Run: `PY -m pytest tests/replays -k "not pg" -q -p no:cacheprovider`, then with `VALO_TEST_DATABASE_URL` set `PY -m pytest tests/replays -k "pg" -q -p no:cacheprovider`
Expected: only the failures recorded in the baseline, failing the same way. Say in the report whether the `pg` tests ran.

- [ ] **Step 4: Commit**

```bash
git add docs webapp/RENDER_DEPLOY.md replay_worker/README.md CLAUDE.md AGENTS.md
git commit -F <message file>
```

Message: `Docs: height rebuilds on the replay worker, the spec's amendments, and the order to turn them on`.

- [ ] **Step 5 (the owner's, after deploy): record the first live cycle**

Not part of the branch. Once `REPLAY_HEIGHTS_AUTO` has been on for one rebuild of one map, the spec's "Cost" section gets the worker's own numbers in place of Task 1's desk estimate: the build's `seconds` and `peak` (from `control_heights.py list`), and the time from the "active" log line to the map's last stale round being recomputed (from the dispatcher's log, or `compute_control.py --dry-run` showing none left for the map). Until then the section says the worker's cost is not measured.

---

## After this plan

- **Nothing is on until the owner sets `REPLAY_HEIGHTS_AUTO`**, and the owner answered the decisions on 2026-10-07: E1 to E5 and E7 to E9 approved (E5: deleted evidence and a rejected rebuild turn the heights off). E6 is not answered: it still changes what the approved spec says, and the guard is only the default.
- **Feature generations and rebuilds (E6).** Before any map gets a published generation, either its manifest stops naming the height digest and the compiled assets are rebuilt at load, or a rebuild republishes it. Until then such a map is held: not rebuilt, and its heights can't be changed by hand.
- **What a retry is for (decided 2026-10-07: leave as planned).** A try after a `done` build whose result couldn't be used reads the worker's same answer again (Task 7's failure table). Making each try a real rebuild would need `POST /heights/build/{id}/cancel` to drop an ended build too; the owner chose not to build that.
- **The lock and the inputs are not checked in one transaction (decided 2026-10-07: leave it).** `_finish` compares the map's inputs with the build's and then `store_build` takes the map's lock; a deletion in between is caught by the next cycle's plan, not by the store. The owner's view: unlikely enough here not to close.
- **Validation still owed.** Except for the one reproduction named in the table at the top (`_make_active` on a stand-in table, 2026-10-07), this plan's code was not run; the changes made to Task 7 that day (the dispatcher's own session never commits, a result's `rules` must be this deploy's, a mis-shaped answer spends a try) were written and not run either. The PostgreSQL concurrency test needs a `*_test` database. The worker image has never been built on the development machine (Docker doesn't run there): its first build is Render's.
- **The "master" of ground samples** the spec keeps in reserve is not built: build it only if a real rebuild proves too slow.
