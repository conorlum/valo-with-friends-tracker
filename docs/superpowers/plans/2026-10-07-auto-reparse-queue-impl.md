# Auto re-parse queue: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stored replay condensed under an older recipe is parsed again by the replay worker from its archived `.vrf` and replaced on the site, one at a time, behind uploads, with nobody running a script; one environment variable turns it on and off, and one admin route shows progress.

**Architecture:** A new module, `app/services/replay_reparse_auto.py`, holds one `step` that the control dispatcher's `cycle` calls between collecting and submitting. The step compares the worker's recipe (new in `/health`) with the site's, polls the one re-parse in flight, classifies every stored replay, and submits the newest eligible one to the worker's existing `POST /reparse`. Each attempt is a `replay_uploads` row whose `session_key` carries the target recipe, so the retry record survives restarts and needs no schema change. The dispatcher holds back control rounds of replays that are about to be replaced.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0, stdlib `urllib` clients, pytest. No migration, no new dependency.

**Spec:** `docs/superpowers/plans/2026-10-07-auto-reparse-queue.md` (decisions 1-10 and the owner's answers).

One departure from the spec: decision 2 reads the worker's archive index "only when the step is about to choose". The step needs the index every cycle to know which replays' control to hold back, so here it is read at most once every `INDEX_S` (120 s) and kept in memory between cycles.

**Depends on:** `docs/superpowers/plans/2026-10-07-worker-gaps-and-kill-one-impl.md`, merged first. This plan edits `_submit`, `_sendable` and `cycle` in `app/services/replay_control_remote.py` as that plan leaves them, and the switch should not go on before timing gaps are computed on the worker.

## Global Constraints

- Branch from `origin/main` after `git fetch`, once the first plan's PR is merged. Work in a worktree under `.claude/worktrees/`.
- Run tests from `webapp/` in the worktree with the main checkout's interpreter, `webapp\.venv313\Scripts\python.exe` of the main checkout, as `PY -m pytest ... -p no:cacheprovider` (written `PY -m pytest` below).
- Before Task 1, run `PY -m pytest tests/replays -k "not pg" -p no:cacheprovider -q` on the clean branch and write the counts into the PR description. `test_replay_worker_control.py::test_control_off_answers_404_and_parsing_is_untouched` is known to fail on Windows; leave it alone.
- `REPLAY_REPARSE_AUTO` defaults to false and is commented out in `render.yaml`. Nothing on Render is changed by this branch; switching it on is the owner's.
- The feature never runs in demo mode, and never without `REPLAY_CONTROL_REMOTE` and `REPLAY_WORKER_URL`. With the switch set but those missing, the site starts normally and says why nothing runs.
- A match in `replay_deletions` is never submitted and never stored. The candidate list starts from existing `replays` rows, never from the worker's archive index.
- Staleness is inequality with the current recipe, never ordering.
- One automatic re-parse in flight, site-wide. At most `MAX_TRIES = 2` attempts per file per recipe.
- No schema change. The attempt record is `replay_uploads` with `session_key = "auto-reparse:<recipe>"`.
- The web service never runs the scorer. `app/replays/store.py` imports no scoring code (`tests/replays/test_replay_isolation.py`).
- The worker's code stays standard library only (`tests/replays/test_control_isolation.py`); it holds no database credentials.
- The repo is public: no credentials, no real player names, no machine paths in code, tests or docs.
- Comments and docstrings match the surrounding code: plain sentences, reasons not narration, docs referenced by path.

## Review Focus

1. **The recipe changes while a re-parse is in flight** (a deploy of either service). Expected: the result is not stored, the row fails with a reason that names the recipe, and the old replay is untouched. Pinned in Task 3 and Task 5.
2. **A match is deleted on request while its re-parse is in flight.** Expected: no replay comes back and the row fails. Pinned in Task 5.
3. **The site restarts with a re-parse in flight.** Expected: it is collected, and no second one is submitted beside it. Pinned in Task 5.
4. **The worker evicts the archived file between the index read and the submit** (a 404). Expected: no row is written, nothing counts as an attempt, and the replay is then listed as having no archived file. Pinned in Task 5.
5. **The step raises** (a bug, a database error). Expected: the control dispatcher still runs that cycle and holds nothing back. Pinned in Task 6.

---

## File structure

| File | Change |
|---|---|
| `webapp/app/replays/contract.py` | Task 1: `current_recipe` |
| `replay_worker/server.py`, `replay_worker/README.md` | Task 1: `recipe` in `/health` |
| `webapp/tests/replays/test_replay_worker.py` | Task 1 |
| `webapp/app/replays/store.py`, `webapp/tests/replays/test_replay_store.py` | Task 2 |
| `webapp/app/services/replay_upload.py`, `webapp/tests/replays/test_replay_archive_web.py` | Task 3 |
| `webapp/app/config.py` | Task 4: the setting |
| `webapp/app/services/replay_reparse_auto.py` (new) | Tasks 4, 5, 7 |
| `webapp/tests/replays/test_replay_reparse_auto.py` (new) | Tasks 4-7 |
| `webapp/app/services/replay_control_remote.py` | Task 6 |
| `webapp/app/routers/replay_admin.py`, `webapp/scripts/reparse_archive.py` | Task 7 |
| `render.yaml`, `webapp/RENDER_DEPLOY.md`, `docs/replay-viewer-plan.md`, `webapp/app/main.py` | Task 8 |

---

### Task 1: The worker says which recipe it stamps

**Files:**
- Modify: `webapp/app/replays/contract.py`
- Modify: `replay_worker/server.py`, `replay_worker/README.md`
- Test: `webapp/tests/replays/test_replay_worker.py`

**Interfaces:**
- Produces: `app.replays.contract.current_recipe(pin: ParserPin | None = None, static_dir: Path | None = None) -> str`; `Worker.recipe: str | None`; `GET /health` body gains `"recipe"`.

- [ ] **Step 1: Write the failing test**

In `webapp/tests/replays/test_replay_worker.py`, add `from app.replays.contract import current_recipe  # noqa: E402` beside the other `app.replays` import, and append:

```python
def test_health_reports_the_recipe_a_parse_stamps(tmp_path, stub):
    worker, httpd, base = start(tmp_path, stub)
    try:
        done = wait(base, post(base, vrf_bytes())[1]["id"])
        assert done["status"] == "done", done
        health = get(base, "/health")[1]
        assert health["recipe"] == done["result"]["recipe"] == current_recipe()
    finally:
        httpd.shutdown()
```

- [ ] **Step 2: Run it and see it fail**

Run: `PY -m pytest tests/replays/test_replay_worker.py -k recipe -p no:cacheprovider -q`
Expected: FAIL with `ImportError: cannot import name 'current_recipe'`.

- [ ] **Step 3: Add `current_recipe`**

In `webapp/app/replays/contract.py`, add `from app.replays import format as fmt` after the standard-library imports, and add after `load_pin`:

```python
def current_recipe(pin: ParserPin | None = None, static_dir: Path | None = None) -> str:
    """The recipe a replay condensed now would carry (format.recipe): the pinned parser commit, the condenser
    and format revisions, and the map and agent assets. The web app computes it without the parser, to find
    stored replays that are stale; the replay worker reports it in /health."""
    return fmt.recipe((pin or load_pin()).commit, fmt.assets_revision(static_dir or fmt.STATIC_DIR))
```

- [ ] **Step 4: Report it from the worker**

In `replay_worker/server.py`, change the contract import to:

```python
from app.replays.contract import ContractError, current_recipe  # noqa: E402
```

In `Worker.__init__`, after `self.wake = threading.Event()`, add:

```python
        # What a parse here stamps on its result. The web app queues an automatic re-parse only while this
        # equals its own (the two services deploy separately); a worker that can't say still parses.
        try:
            self.recipe = current_recipe()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self.recipe = None
```

In `make_handler`'s `do_GET`, in the `/health` branch, add after the line that sets `body["archive"]`:

```python
                body["recipe"] = worker.recipe
```

In the module docstring, change the `/health` line to:

```
    GET  /health      {"ok": true, "queued", "limits", "recipe",
                       "control": {"enabled", "queued", "running", "warm", "preempted"}}
```

In `replay_worker/README.md`, change the `/health` row of the HTTP table to:

```markdown
| `GET /health` | `{"ok": true, "queued", "limits", "recipe", "control", "archive"}`; `recipe` is what a parse here stamps on its result |
```

- [ ] **Step 5: Run the worker's tests**

Run: `PY -m pytest tests/replays/test_replay_worker.py tests/replays/test_replay_worker_archive.py tests/replays/test_control_isolation.py tests/replays/test_replay_contract.py -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add webapp/app/replays/contract.py replay_worker/server.py replay_worker/README.md webapp/tests/replays/test_replay_worker.py
git commit -m "worker: /health reports the recipe a parse stamps"
```

---

### Task 2: A same-file replace keeps the per-kill Impact split

**Files:**
- Modify: `webapp/app/replays/store.py` (`store_replay`)
- Test: `webapp/tests/replays/test_replay_store.py`

**Interfaces:**
- Produces: `store_replay` behaviour only. When the existing row is the same `source_sha256`, was linked, and the new row links to the same match, `kill_impact` is copied to the new row.

Why this is safe: `replays.kill_impact` is `{kill_events.id: [gain, loss]}` plus a fingerprint of the match's stored `impact_scores` rows (`app/replays/db.py`, `kill_impact_for_page`). It depends on the match's rows, not on the replay's blobs, and the page shows it only while that fingerprint is current. `write_link` clears it because only a user-run script may compute it; a copy computes nothing.

- [ ] **Step 1: Write the failing tests**

Append to `webapp/tests/replays/test_replay_store.py`, before the `# ---- PostgreSQL only` comment line:

```python
def test_a_new_recipe_of_the_same_file_keeps_the_per_kill_split(db, condensed):
    add_match(db)
    first = store.store_replay(db, condensed, source="upload")
    split = {"fingerprint": "abc", "kills": {"1": [10.0, -5.0]}}
    db.get(Replay, first.replay_id).kill_impact = split
    db.commit()
    result = store.store_replay(db, replace(condensed, recipe=condensed.recipe + "x"), source="upload")
    row = db.get(Replay, result.replay_id)
    assert result.action == "replaced" and row.recipe.endswith("x") and row.link_status == "linked"
    assert row.kill_impact == split


def test_another_recording_never_inherits_the_split(db, condensed):
    add_match(db)
    first = store.store_replay(db, condensed, source="local")
    db.get(Replay, first.replay_id).kill_impact = {"fingerprint": "abc", "kills": {"1": [10.0, -5.0]}}
    db.commit()
    other = replace(condensed, source_sha256="f" * 64)
    result = store.store_replay(db, other, source="local", replace=True)
    assert result.action == "replaced" and db.get(Replay, result.replay_id).kill_impact is None


def test_the_split_is_not_carried_to_a_replay_that_no_longer_links(db, condensed):
    add_match(db)
    first = store.store_replay(db, condensed, source="upload")
    db.get(Replay, first.replay_id).kill_impact = {"fingerprint": "abc", "kills": {"1": [10.0, -5.0]}}
    db.commit()
    db.query(KillEvent).delete()
    db.query(Round).delete()
    db.query(MatchPlayer).delete()
    db.query(Match).delete()
    db.commit()
    result = store.store_replay(db, replace(condensed, recipe=condensed.recipe + "x"), source="upload")
    row = db.get(Replay, result.replay_id)
    assert row.link_status != "linked" and row.kill_impact is None
```

- [ ] **Step 2: Run them and see the first fail**

Run: `PY -m pytest tests/replays/test_replay_store.py -k "split" -p no:cacheprovider -q`
Expected: `test_a_new_recipe_of_the_same_file_keeps_the_per_kill_split` FAILS on `row.kill_impact == split` (it is None). The other two already pass; they pin what must stay true.

- [ ] **Step 3: Carry the split**

In `webapp/app/replays/store.py`, in `store_replay`, replace from the line `existing =session.query(Replay)...` down to the final `return StoreResult(action, row.id, status, row.link_report or {})` with:

```python
        existing = session.query(Replay).filter(Replay.match_uuid == uuid).one_or_none()
        same_source = existing is not None and existing.source_sha256 == condensed.source_sha256
        if existing is not None and not replace:
            if same_source and existing.recipe == condensed.recipe:
                session.rollback()
                return StoreResult("unchanged", existing.id, existing.link_status, {"reason": "same file and recipe"})
            if not same_source and replay_db.is_linked(existing):
                session.rollback()
                return StoreResult("kept_existing", existing.id, existing.link_status,
                                   {"reason": "a linked replay of this match exists; the new recording was not stored"})
            if not same_source:
                # An unlinked or refused replay can't squat the UUID: the new one replaces it only
                # if it links, and a savepoint undoes the attempt otherwise.
                savepoint = session.begin_nested()
                _delete(session, existing)
                row = _insert(session, condensed, source)
                status = replay_db.link_replay(session, row)
                if status != "linked":
                    savepoint.rollback()
                    session.rollback()
                    return StoreResult("kept_existing", existing.id, existing.link_status,
                                       {"reason": "the new recording did not link, so it doesn't replace the "
                                                  "existing unlinked one", "new_link_status": status})
                savepoint.commit()
                session.commit()
                return StoreResult("replaced", row.id, status, row.link_report or {})
        action = "stored"
        carried = None
        if existing is not None:
            # The same recording under a new recipe (a re-parse): the per-kill Impact split describes the
            # match's impact_scores rows, not the blobs, so it is kept when the new row links to the same
            # match. The page still shows it only while its fingerprint is current (db.kill_impact_for_page).
            if same_source and replay_db.is_linked(existing) and existing.kill_impact:
                carried = (existing.match_id, existing.kill_impact)
            _delete(session, existing)
            action = "replaced"
        row = _insert(session, condensed, source)
        status = replay_db.link_replay(session, row)
        if carried is not None and status == "linked" and row.match_id == carried[0]:
            row.kill_impact = carried[1]
        session.commit()
        return StoreResult(action, row.id, status, row.link_report or {})
```

In the module docstring, change the second dedupe bullet to:

```
   - the same `source_sha256`, another recipe (a re-ingest or a re-parse after a condenser change):
     replaced, keeping the per-kill Impact split when it links to the same match;
```

- [ ] **Step 4: Run the store's tests**

Run: `PY -m pytest tests/replays/test_replay_store.py tests/replays/test_replay_isolation.py tests/replays/test_replay_impact.py -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/replays/store.py webapp/tests/replays/test_replay_store.py
git commit -m "replays: a same-file replace keeps the per-kill Impact split"
```

---

### Task 3: A tagged re-parse stores only the recipe it asked for

**Files:**
- Modify: `webapp/app/services/replay_upload.py`
- Test: `webapp/tests/replays/test_replay_archive_web.py`

**Interfaces:**
- Produces:
  - `uploads.AUTO_PREFIX = "auto-reparse:"`
  - `uploads.auto_recipe(session_key: str | None) -> str | None`
  - `uploads.refresh_job` fails a row whose `session_key` is `AUTO_PREFIX + <recipe>` when the result's recipe is another one, with `error` containing `"another recipe"`.
  - `uploads.collect_unfinished(session_factory, worker, now=None, session_prefix: str | None = None) -> int`

- [ ] **Step 1: Write the failing tests**

In `webapp/tests/replays/test_replay_archive_web.py`, add to the standard-library imports:

```python
import uuid
from datetime import datetime, timezone
```

Append:

```python
def tagged_reparse(db, client, recipe):
    """What app/services/replay_reparse_auto.py writes when it submits one: a row tagged with the recipe asked for."""
    job = client.reparse(MATCH_UUID)
    upload = ReplayUpload(id=str(uuid.uuid4()), status="parsing", source_sha256=job["sha256"], size_bytes=job["size"],
                          session_key=uploads.AUTO_PREFIX + recipe, client_ip=None,
                          created_at=datetime.now(timezone.utc), worker_job_id=job["id"])
    db.add(upload)
    db.commit()
    return upload


def finish(db, client, upload):
    for _ in range(300):
        upload = uploads.refresh_job(db, upload, client)
        if upload.status not in uploads.UNFINISHED:
            return upload
        time.sleep(0.1)
    raise AssertionError("the re-parse never finished")


def test_a_tagged_reparse_replaces_a_stale_replay_and_keeps_the_file(db, tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        client = uploads.WorkerClient(base)
        stored = collect(db, client, vrf_bytes())
        replay = db.get(Replay, stored.replay_id)
        current, replay.recipe = replay.recipe, "an-older-recipe"
        db.commit()
        upload = finish(db, client, tagged_reparse(db, client, current))
        assert (upload.status, upload.store_outcome, upload.archive_ack) == ("stored", "replaced", "kept")
        assert db.query(Replay).one().recipe == current
        assert (disk / "archive" / f"{MATCH_UUID}.vrf").read_bytes() == vrf_bytes()
    finally:
        httpd.shutdown()


def test_a_tagged_reparse_from_a_worker_on_another_recipe_is_not_stored(db, tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        client = uploads.WorkerClient(base)
        stored = collect(db, client, vrf_bytes())
        replay = db.get(Replay, stored.replay_id)
        replay.recipe = "an-older-recipe"
        db.commit()
        upload = finish(db, client, tagged_reparse(db, client, "the-recipe-the-site-wanted"))
        assert upload.status == "failed" and "another recipe" in upload.error and upload.store_outcome == "failed"
        kept = db.query(Replay).one()
        assert kept.id == stored.replay_id and kept.recipe == "an-older-recipe", "the old replay is untouched"
        assert (disk / "archive" / f"{MATCH_UUID}.vrf").read_bytes() == vrf_bytes(), "and so is its file"
    finally:
        httpd.shutdown()


def test_collect_unfinished_can_take_only_the_tagged_rows(engine, db, tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        client = uploads.WorkerClient(base)
        stored = collect(db, client, vrf_bytes())
        current = db.get(Replay, stored.replay_id).recipe
        tagged = tagged_reparse(db, client, current)
        other = ReplayUpload(id=str(uuid.uuid4()), status="parsing", session_key="a-friend",
                             created_at=datetime.now(timezone.utc), worker_job_id="no-such-job")
        db.add(other)
        db.commit()
        factory = sessionmaker(bind=engine)
        for _ in range(300):
            if uploads.collect_unfinished(factory, client, session_prefix=uploads.AUTO_PREFIX):
                break
            time.sleep(0.1)
        db.expire_all()
        assert db.get(ReplayUpload, tagged.id).status == "stored"
        assert db.get(ReplayUpload, other.id).status == "parsing", "a friend's upload is left to its own poll"
    finally:
        httpd.shutdown()


def test_auto_recipe_reads_only_a_tagged_session_key():
    assert uploads.auto_recipe(uploads.AUTO_PREFIX + "abc.c13.f1.a0") == "abc.c13.f1.a0"
    assert uploads.auto_recipe("admin-reparse") is None and uploads.auto_recipe(None) is None
```

- [ ] **Step 2: Run them and see them fail**

Run: `PY -m pytest tests/replays/test_replay_archive_web.py -k "tagged or auto_recipe" -p no:cacheprovider -q`
Expected: FAIL with `AttributeError: module 'app.services.replay_upload' has no attribute 'AUTO_PREFIX'`.

- [ ] **Step 3: Add the tag, the check and the filter**

In `webapp/app/services/replay_upload.py`, add after `RECENT_LISTED`:

```python
# An automatic re-parse's row (app/services/replay_reparse_auto.py): `session_key` is this, then the recipe
# it was asked for. The row is the whole record of the attempt, so nothing else needs storing.
AUTO_PREFIX = "auto-reparse:"


def auto_recipe(session_key: str | None) -> str | None:
    """The recipe an automatic re-parse was asked for, or None for any other upload."""
    if session_key and session_key.startswith(AUTO_PREFIX):
        return session_key[len(AUTO_PREFIX):]
    return None
```

In `refresh_job`, inside the `try:` of the `if status == "done":` branch, add after the `source_sha256` check:

```python
            wanted = auto_recipe(upload.session_key)
            if wanted is not None and condensed.recipe != wanted:
                # The worker was redeployed between the submit and the parse, or the site was. Storing it would
                # replace the replay and its control with something still stale.
                raise ValueError(f"the worker is on another recipe ({condensed.recipe})")
```

Change `collect_unfinished`'s signature and its first query:

```python
def collect_unfinished(session_factory, worker: WorkerClient, now: datetime | None = None,
                       session_prefix: str | None = None) -> int:
```

and replace

```python
        ids = [upload_id for (upload_id,) in
               session.query(ReplayUpload.id).filter(ReplayUpload.status.in_(UNFINISHED))
               .order_by(ReplayUpload.created_at)]
```

with:

```python
        query = session.query(ReplayUpload.id).filter(ReplayUpload.status.in_(UNFINISHED))
        if session_prefix is not None:
            query = query.filter(ReplayUpload.session_key.like(session_prefix + "%"))
        ids = [upload_id for (upload_id,) in query.order_by(ReplayUpload.created_at)]
```

Add to `collect_unfinished`'s docstring: "`session_prefix` takes only the rows whose `session_key` starts with it (the automatic re-parses)."

- [ ] **Step 4: Run the upload and archive tests**

Run: `PY -m pytest tests/replays/test_replay_archive_web.py tests/replays/test_replay_upload.py -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/replay_upload.py webapp/tests/replays/test_replay_archive_web.py
git commit -m "uploads: a tagged re-parse stores only the recipe it asked for"
```

---

### Task 4: The setting, and which stored replays are eligible

**Files:**
- Modify: `webapp/app/config.py`
- Create: `webapp/app/services/replay_reparse_auto.py`
- Create: `webapp/tests/replays/test_replay_reparse_auto.py`

**Interfaces:**
- Consumes: `current_recipe` (Task 1), `uploads.AUTO_PREFIX` (Task 3).
- Produces:
  - `settings.replay_reparse_auto: bool`
  - `auto.MAX_TRIES = 2`, `auto.BACKOFF_S = 3600`, `auto.HELD = ("eligible", "backoff", "in_flight")`
  - `auto.tag(recipe: str) -> str`
  - `auto.off_reason() -> str | None`, `auto.enabled() -> bool`
  - `auto.Entry(replay_id: int, match_uuid: str, map_name: str, state: str, rank: tuple, attempts: int, last_error: str | None)`
  - `auto.classify(session, recipe: str, index: dict[str, str] | None, builds: frozenset[str], now: float) -> list[Entry]`; `state` is one of `current`, `deleted`, `in_flight`, `gave_up`, `unsupported_build`, `unknown`, `no_archive`, `other_recording`, `backoff`, `eligible`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/replays/test_replay_reparse_auto.py`:

```python
"""Automatic re-parses of stale replays (app/services/replay_reparse_auto.py;
docs/superpowers/plans/2026-10-07-auto-reparse-queue.md), on SQLite with fake worker clients."""

import base64
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_control_store import CONTROL_TABLES  # noqa: E402
from test_replay_store import add_match, condensed  # noqa: E402,F401  (fixture)

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models.match import Match  # noqa: E402
from app.models.replay import Replay, ReplayDeletion, ReplayUpload  # noqa: E402
from app.replays import store  # noqa: E402
from app.replays.contract import current_recipe, load_pin  # noqa: E402
from app.services import replay_reparse_auto as auto  # noqa: E402
from app.services import replay_upload as uploads  # noqa: E402

# An older recipe. Its condenser revision is 10, map control's floor (replay_control.MIN_CONDENSE_REVISION), so
# the replay's rounds are still computable: the dispatcher tests below need rounds it would send.
OLD = "000000000000.c10.f1.a00000000"
# A fixed clock, in the past, for the rows the tests write themselves: the step's gates compare it with real
# time in the tests that also let rows finish "now".
T0 = 1_700_000_000.0


@pytest.fixture
def factory(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_reparse_auto", True)
    monkeypatch.setattr(settings, "replay_control_remote", True)
    monkeypatch.setattr(settings, "replay_worker_url", "worker:8080")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[*CONTROL_TABLES, ReplayUpload.__table__])
    return sessionmaker(bind=engine)


@pytest.fixture
def db(factory):
    session = factory()
    yield session
    session.close()


@pytest.fixture
def stale(db, condensed):
    """A linked, uploaded replay stored under an older recipe."""
    assert condensed.recipe == current_recipe(), "the fixture is condensed under this checkout's recipe"
    add_match(db)
    replay = db.get(Replay, store.store_replay(db, condensed, source="upload").replay_id)
    assert replay.link_status == "linked"
    replay.recipe = OLD
    db.commit()
    return replay


def attempt(db, recipe, sha, status="failed", error="parse failed", finished=T0):
    """One earlier automatic re-parse of a file, as its replay_uploads row."""
    done = status not in uploads.UNFINISHED
    db.add(ReplayUpload(id=str(uuid.uuid4()), status=status, error=error if status == "failed" else None,
                        source_sha256=sha, session_key=auto.tag(recipe),
                        created_at=datetime.fromtimestamp(finished - 60, timezone.utc),
                        finished_at=datetime.fromtimestamp(finished, timezone.utc) if done else None,
                        worker_job_id="gone"))
    db.commit()


def state_of(db, replay, index="archived", now=T0 + 10 * auto.BACKOFF_S, recipe=None):
    if index == "archived":
        index = {MATCH_UUID: replay.source_sha256}
    db.expire_all()
    [entry] = auto.classify(db, recipe or current_recipe(), index, load_pin().supported_builds, now)
    return entry


# ---------------------------------------------------------------- which replays are eligible


def test_a_stale_replay_with_its_file_archived_is_eligible(db, stale):
    entry = state_of(db, stale)
    assert (entry.state, entry.replay_id, entry.match_uuid, entry.attempts) == ("eligible", stale.id, MATCH_UUID, 0)


def test_a_replay_on_the_current_recipe_is_current(db, stale):
    stale.recipe = current_recipe()
    db.commit()
    assert state_of(db, stale).state == "current"


def test_each_missing_condition_makes_it_ineligible(db, stale):
    assert state_of(db, stale, index={}).state == "no_archive"
    assert state_of(db, stale, index={MATCH_UUID: "f" * 64}).state == "other_recording"
    assert state_of(db, stale, index=None).state == "unknown", "the worker couldn't be asked"
    stale.game_branch = "++Ares-Core+release-1.00"
    db.commit()
    assert state_of(db, stale).state == "unsupported_build"
    db.add(ReplayDeletion(match_uuid=MATCH_UUID, reason="asked"))
    db.commit()
    assert state_of(db, stale).state == "deleted"


def test_attempts_under_the_current_recipe_back_off_and_then_give_up(db, stale):
    recipe, sha = current_recipe(), stale.source_sha256
    attempt(db, recipe, sha, error="parse timed out: please try again")
    waiting = state_of(db, stale, now=T0 + auto.BACKOFF_S - 1)
    assert (waiting.state, waiting.attempts, waiting.last_error) == ("backoff", 1, "parse timed out: please try again")
    assert state_of(db, stale, now=T0 + auto.BACKOFF_S).state == "eligible"
    attempt(db, recipe, sha, finished=T0 + 2 * auto.BACKOFF_S)
    gone = state_of(db, stale)
    assert (gone.state, gone.attempts, gone.last_error) == ("gave_up", auto.MAX_TRIES, "parse failed")


def test_a_contract_refusal_is_final_after_one(db, stale):
    attempt(db, current_recipe(), stale.source_sha256, error="not condensable: replay_build")
    assert state_of(db, stale).state == "gave_up"


def test_a_stored_result_that_left_the_replay_stale_counts_as_an_attempt(db, stale):
    attempt(db, current_recipe(), stale.source_sha256, status="stored")
    assert state_of(db, stale).attempts == 1


def test_an_unfinished_attempt_is_in_flight(db, stale):
    attempt(db, current_recipe(), stale.source_sha256, status="parsing")
    assert state_of(db, stale).state == "in_flight"


def test_attempts_under_another_recipe_or_for_another_file_dont_count(db, stale):
    attempt(db, "some-earlier-recipe", stale.source_sha256)
    attempt(db, "some-earlier-recipe", stale.source_sha256)
    attempt(db, current_recipe(), "e" * 64)
    entry = state_of(db, stale)
    assert (entry.state, entry.attempts) == ("eligible", 0), "a new recipe gives every file fresh attempts"


def test_newest_match_first_then_unlinked_by_upload_time(db, stale):
    db.get(Match, stale.match_id).played_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    db.commit()
    linked_rank = state_of(db, stale).rank
    stale.link_status, stale.match_id = "unlinked", None
    db.commit()
    unlinked_rank = state_of(db, stale).rank
    assert linked_rank[0] == 0 and unlinked_rank[0] == 1 and linked_rank < unlinked_rank
    older = (0, -datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp(), -1)
    assert linked_rank < older, "a later played_at sorts first"


def test_the_switch_and_what_it_needs(monkeypatch, factory):
    assert auto.enabled() and auto.off_reason() is None
    monkeypatch.setattr(settings, "replay_reparse_auto", False)
    assert not auto.enabled() and "REPLAY_REPARSE_AUTO" in auto.off_reason()
    monkeypatch.setattr(settings, "replay_reparse_auto", True)
    monkeypatch.setattr(settings, "replay_control_remote", False)
    assert not auto.enabled() and "REPLAY_CONTROL_REMOTE" in auto.off_reason()
    monkeypatch.setattr(settings, "replay_control_remote", True)
    monkeypatch.setattr(settings, "replay_worker_url", None)
    assert not auto.enabled()
    monkeypatch.setattr(settings, "replay_worker_url", "worker:8080")
    monkeypatch.setattr(settings, "demo_mode", True)
    assert not auto.enabled() and auto.off_reason() == "demo mode"


def test_the_setting_is_off_by_default():
    from app.config import Settings

    assert Settings.model_fields["replay_reparse_auto"].default is False


def test_the_tag_fits_the_column():
    assert auto.tag("abc") == "auto-reparse:abc"
    assert len(auto.tag(current_recipe())) <= ReplayUpload.__table__.c.session_key.type.length
```

- [ ] **Step 2: Run them and see them fail**

Run: `PY -m pytest tests/replays/test_replay_reparse_auto.py -p no:cacheprovider -q`
Expected: FAIL at import with `ImportError: cannot import name 'replay_reparse_auto'`.

- [ ] **Step 3: Add the setting**

In `webapp/app/config.py`, add after `replay_control_remote: bool = False`:

```python
    # Stored replays condensed under an older recipe are re-parsed by the worker from its .vrf archive
    # (app/services/replay_reparse_auto.py). Off by default: it rewrites stored replays. It runs inside the
    # map-control dispatcher, so it also needs REPLAY_CONTROL_REMOTE and REPLAY_WORKER_URL, and never demo mode.
    replay_reparse_auto: bool = False
```

- [ ] **Step 4: Write the module's first half**

Create `webapp/app/services/replay_reparse_auto.py`:

```python
"""Stored replays condensed under an older recipe are parsed again by the replay worker, with nobody
running a script (docs/superpowers/plans/2026-10-07-auto-reparse-queue.md).

The worker has no database, so the web app drives it, from the map-control dispatcher's own cycle
(app/services/replay_control_remote.py): re-parses and control compete for one worker, and a re-parse makes
that replay's control stale, so one loop orders them. Each cycle, `step`:

1. polls the one re-parse in flight (the same `refresh_job` as an upload: one locked store, the link, the ack);
2. does nothing more unless the worker's recipe (its /health) equals the site's and its archive is on;
3. classifies every stored replay (`classify`) and returns the ids the dispatcher holds control back for:
   those about to be replaced;
4. submits the newest eligible match to the worker's `POST /reparse`, when no upload is waiting or
   unfinished, the worker's control queue has drained and SETTLE_S has passed since the last one finished.

A replay is eligible when its recipe is not the current one (inequality, never ordering), the worker's
archive holds the same file (the same sha256), the match isn't tombstoned, the pinned parser supports its
game build, and it has had fewer than MAX_TRIES attempts under the current recipe. The list starts from
`replays` rows, never from the archive, so this only ever replaces a replay that exists.

Every attempt is a `replay_uploads` row whose `session_key` is `auto-reparse:<the recipe asked for>`: the
record survives a restart, and a new recipe is a new tag, so every file gets fresh attempts when the code
changes. `refresh_job` refuses to store a result that carries another recipe.

Off unless REPLAY_REPARSE_AUTO is set, the dispatcher is on, and never in demo mode. Standard library and
the DB only.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import settings
from app.models.match import Match
from app.models.replay import Replay, ReplayDeletion, ReplayUpload
from app.services import replay_upload as uploads

log = logging.getLogger(__name__)

MAX_TRIES = 2                   # attempts per file per recipe, whatever went wrong
BACKOFF_S = 3600                # between them
FINAL = "not condensable"       # a contract refusal (an unsupported build, a bad export): it would repeat
HELD = ("eligible", "backoff", "in_flight")   # states whose control the dispatcher holds back
TAG_MAX = 64                    # replay_uploads.session_key's length


def tag(recipe: str) -> str:
    return uploads.AUTO_PREFIX + recipe


def off_reason() -> str | None:
    """Why automatic re-parses can't run on this deploy, or None when they can."""
    if settings.demo_mode:
        return "demo mode"
    if not settings.replay_reparse_auto:
        return "REPLAY_REPARSE_AUTO is not set"
    if not (settings.replay_control_remote and settings.replay_worker_url):
        return "the map-control dispatcher is off (REPLAY_CONTROL_REMOTE and REPLAY_WORKER_URL)"
    return None


def enabled() -> bool:
    return off_reason() is None


def _stamp(value: datetime | None) -> float:
    if value is None:
        return 0.0
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()


@dataclass
class Entry:
    replay_id: int
    match_uuid: str
    map_name: str
    state: str
    rank: tuple = ()                # sorts newest match first (the control queue's D2 order)
    attempts: int = 0               # finished attempts under the current recipe
    last_error: str | None = None


def classify(session, recipe: str, index: dict[str, str] | None, builds: frozenset[str], now: float) -> list[Entry]:
    """Every stored replay with its state under `recipe`. `index` is the worker's archive, match uuid ->
    sha256, or None when it couldn't be read; `builds` the game builds the pinned parser supports."""
    deleted = {str(u).lower() for (u,) in session.query(ReplayDeletion.match_uuid)}
    tries: dict[str, list] = defaultdict(list)
    for sha, status, error, finished in (session.query(ReplayUpload.source_sha256, ReplayUpload.status,
                                                       ReplayUpload.error, ReplayUpload.finished_at)
                                         .filter(ReplayUpload.session_key == tag(recipe))):
        tries[sha].append((status, error, _stamp(finished)))
    out = []
    for replay, played in (session.query(Replay, Match.played_at)
                           .outerjoin(Match, Match.id == Replay.match_id).order_by(Replay.id)):
        match_uuid = str(replay.match_uuid).lower()
        if replay.link_status == "linked" and played is not None:
            rank = (0, -_stamp(played), -replay.id)
        else:
            rank = (1, -_stamp(replay.created_at), -replay.id)
        entry = Entry(replay.id, match_uuid, replay.map_name, "current", rank)
        out.append(entry)
        if replay.recipe == recipe:
            continue
        mine = tries.get(replay.source_sha256, [])
        done = sorted((t for t in mine if t[0] not in uploads.UNFINISHED), key=lambda t: t[2])
        entry.attempts = len(done)
        entry.last_error = done[-1][1] if done else None
        if match_uuid in deleted:
            entry.state = "deleted"
        elif len(done) < len(mine):
            entry.state = "in_flight"
        elif len(done) >= MAX_TRIES or any((error or "").startswith(FINAL) for _, error, _ in done):
            entry.state = "gave_up"
        elif replay.game_branch not in builds:
            entry.state = "unsupported_build"
        elif index is None:
            entry.state = "unknown"
        elif match_uuid not in index:
            entry.state = "no_archive"
        elif index[match_uuid] != replay.source_sha256:
            entry.state = "other_recording"
        elif done and now - done[-1][2] < BACKOFF_S:
            entry.state = "backoff"
        else:
            entry.state = "eligible"
    return out
```

- [ ] **Step 5: Run the tests**

Run: `PY -m pytest tests/replays/test_replay_reparse_auto.py tests/replays/test_control_isolation.py -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add webapp/app/config.py webapp/app/services/replay_reparse_auto.py webapp/tests/replays/test_replay_reparse_auto.py
git commit -m "re-parse queue: the setting, and which stored replays are eligible"
```

---

### Task 5: The step: poll the one in flight, check the gates, submit the next

**Files:**
- Modify: `webapp/app/services/replay_reparse_auto.py`
- Test: `webapp/tests/replays/test_replay_reparse_auto.py`

**Interfaces:**
- Consumes: `classify`, `Entry`, `HELD`, `tag`, `enabled` (Task 4); `uploads.collect_unfinished(..., session_prefix=)` and `refresh_job`'s recipe check (Task 3); `current_recipe` and the worker's `health()["recipe"]` (Task 1).
- Produces, for Task 6:
  - `auto.SETTLE_S = 180`, `auto.INDEX_S = 120`
  - `auto.Memo(index: dict[str, str] | None = None, index_at: float | None = None)`
  - `auto.step(session, session_factory, worker, memo: Memo, control_in_flight: int, now: float, counts: dict) -> frozenset[int]`: the replay ids whose control the dispatcher holds back. `worker` is an `uploads.WorkerClient` (`health`, `archive`, `reparse`, `job`, `ack`). `counts` must already have the keys `reparse_sent`, `reparse_finished`, `reparse_refused`.

- [ ] **Step 1: Write the fake worker and the failing tests**

Append to `webapp/tests/replays/test_replay_reparse_auto.py`:

```python
# ---------------------------------------------------------------- the step


class FakeWorker:
    """The worker as replay_upload.WorkerClient sees it: /health, /archive, /reparse, /jobs/{id} and the ack."""

    def __init__(self, files, recipe=None):
        self.files = dict(files)                 # match uuid -> sha256: the archive index
        self.recipe = current_recipe() if recipe is None else recipe
        self.queued, self.control, self.archive_on, self.down = 0, {"queued": 0, "running": 0}, True, False
        self.asked, self.jobs, self.index_reads = [], {}, 0

    def health(self):
        if self.down:
            raise uploads.WorkerError("the replay worker is unreachable")
        return {"ok": True, "queued": self.queued, "recipe": self.recipe,
                "archive": {"enabled": self.archive_on}, "control": {"enabled": True, **self.control}}

    def archive(self):
        self.index_reads += 1
        return {"files": [{"match_uuid": u, "sha256": s} for u, s in self.files.items()]}

    def reparse(self, match_uuid):
        if match_uuid not in self.files:
            raise uploads.WorkerRefused("this match has no archived file", 404, {})
        job_id = f"r{len(self.asked)}"
        self.asked.append(match_uuid)
        self.jobs[job_id] = {"status": "queued"}
        return {"id": job_id, "status": "queued", "kind": "reparse", "sha256": self.files[match_uuid], "size": 1}

    def job(self, job_id):
        return self.jobs[job_id]

    def ack(self, job_id, body):
        return {"archived": True, "result": "kept"}


def result_of(condensed, recipe=None):  # noqa: F811
    """A finished job's result, as replay_worker/server.py builds it."""
    c = condensed if recipe is None else replace(condensed, recipe=recipe)
    return {"match_uuid": c.match_uuid, "map_name": c.map_name, "game_branch": c.game_branch,
            "source_sha256": c.source_sha256, "recipe": c.recipe, "hz": c.hz, "round_count": c.round_count,
            "rounds": {str(n): base64.b64encode(data).decode("ascii") for n, data in c.encoded_rounds().items()},
            "players": c.players, "link_inputs": c.link_inputs, "report": {}}


def step(factory, worker, memo, now=None, control_in_flight=0):
    """One step with its own session, as the dispatcher's cycle runs it. Returns (held ids, counts)."""
    counts = defaultdict(int)
    session = factory()
    try:
        held = auto.step(session, factory, worker, memo, control_in_flight, time.time() if now is None else now,
                         counts)
    finally:
        session.rollback()
        session.close()
    return held, counts


def later():
    """Past SETTLE_S and BACKOFF_S after anything the test has just finished."""
    return time.time() + auto.BACKOFF_S + auto.SETTLE_S + 1


def auto_rows(db):
    db.expire_all()
    return db.query(ReplayUpload).filter(ReplayUpload.session_key.like(uploads.AUTO_PREFIX + "%")) \
        .order_by(ReplayUpload.created_at).all()


def test_a_stale_replay_is_submitted_held_back_and_replaced(factory, db, stale, condensed):
    worker, memo = FakeWorker({MATCH_UUID: stale.source_sha256}), auto.Memo()
    old_id = stale.id
    held, counts = step(factory, worker, memo)
    assert worker.asked == [MATCH_UUID] and counts["reparse_sent"] == 1 and held == {old_id}
    [row] = auto_rows(db)
    assert (row.status, row.session_key, row.source_sha256, row.worker_job_id) == \
        ("parsing", auto.tag(current_recipe()), stale.source_sha256, "r0")
    held, counts = step(factory, worker, memo)                     # still queued on the worker
    assert worker.asked == [MATCH_UUID] and held == {old_id}, "one in flight, and its control stays held"
    worker.jobs["r0"] = {"status": "done", "result": result_of(condensed)}
    held, counts = step(factory, worker, memo)
    assert counts["reparse_finished"] == 1 and held == frozenset()
    [row] = auto_rows(db)
    assert (row.status, row.store_outcome) == ("stored", "replaced")
    replay = db.query(Replay).one()
    assert replay.recipe == current_recipe() and replay.link_status == "linked"   # SQLite may reuse the old id
    assert step(factory, worker, memo, now=later())[1]["reparse_sent"] == 0, "nothing is left to do"


@pytest.mark.parametrize("break_it", [
    lambda w: setattr(w, "recipe", "another-recipe"),             # a worker still on the old code, or ahead
    lambda w: setattr(w, "recipe", ""),                           # a worker too old to say
    lambda w: setattr(w, "archive_on", False),
    lambda w: setattr(w, "down", True),
])
def test_nothing_is_submitted_or_held_unless_the_worker_matches(factory, db, stale, break_it):
    worker = FakeWorker({MATCH_UUID: stale.source_sha256})
    break_it(worker)
    held, counts = step(factory, worker, auto.Memo())
    assert not worker.asked and held == frozenset() and not auto_rows(db)


def test_nothing_runs_while_the_switch_is_off(factory, db, stale, monkeypatch):
    monkeypatch.setattr(settings, "replay_reparse_auto", False)
    worker = FakeWorker({MATCH_UUID: stale.source_sha256})
    assert step(factory, worker, auto.Memo()) == (frozenset(), {}) and not worker.asked


@pytest.mark.parametrize("busy", ["upload_queued", "upload_unfinished", "control_on_the_worker", "control_in_flight"])
def test_it_waits_for_uploads_and_for_control_to_drain_but_keeps_control_held(factory, db, stale, busy):
    worker = FakeWorker({MATCH_UUID: stale.source_sha256})
    in_flight = 0
    if busy == "upload_queued":
        worker.queued = 1
    elif busy == "upload_unfinished":
        db.add(ReplayUpload(id=str(uuid.uuid4()), status="parsing", session_key="a-friend",
                            created_at=datetime.now(timezone.utc)))
        db.commit()
    elif busy == "control_on_the_worker":
        worker.control = {"queued": 0, "running": 1}
    else:
        in_flight = 3
    held, counts = step(factory, worker, auto.Memo(), control_in_flight=in_flight)
    assert not worker.asked and held == {stale.id}


def test_the_next_one_waits_for_the_last_to_settle(factory, db, stale):
    attempt(db, current_recipe(), "e" * 64, status="stored", finished=T0)     # another file's, just stored
    worker, memo = FakeWorker({MATCH_UUID: stale.source_sha256}), auto.Memo()
    assert not step(factory, worker, memo, now=T0 + auto.SETTLE_S - 1)[1]["reparse_sent"]
    assert step(factory, worker, memo, now=T0 + auto.SETTLE_S)[1]["reparse_sent"] == 1


def test_a_failed_parse_is_tried_once_more_after_the_backoff_and_then_left(factory, db, stale):
    worker, memo = FakeWorker({MATCH_UUID: stale.source_sha256}), auto.Memo()
    step(factory, worker, memo)
    worker.jobs["r0"] = {"status": "failed", "error": "parse failed"}
    held, counts = step(factory, worker, memo)
    assert counts["reparse_finished"] == 1 and held == {stale.id}, "backing off: its control is still held"
    assert worker.asked == [MATCH_UUID]
    held, counts = step(factory, worker, memo, now=later())
    assert worker.asked == [MATCH_UUID, MATCH_UUID]
    worker.jobs["r1"] = {"status": "failed", "error": "parse timed out"}
    step(factory, worker, memo, now=later())
    held, counts = step(factory, worker, memo, now=later() + auto.BACKOFF_S)
    assert worker.asked == [MATCH_UUID, MATCH_UUID] and held == frozenset(), "given up: control is released"
    assert [r.status for r in auto_rows(db)] == ["failed", "failed"]
    assert db.query(Replay).one().recipe == OLD


def test_an_unsupported_build_reported_by_the_worker_is_never_asked_again(factory, db, stale):
    worker, memo = FakeWorker({MATCH_UUID: stale.source_sha256}), auto.Memo()
    step(factory, worker, memo)
    worker.jobs["r0"] = {"status": "failed", "error": "not condensable: replay_build"}
    step(factory, worker, memo)
    step(factory, worker, memo, now=later())
    assert worker.asked == [MATCH_UUID]


def test_a_new_recipe_gives_a_given_up_file_fresh_attempts(factory, db, stale, monkeypatch):
    attempt(db, current_recipe(), stale.source_sha256)
    attempt(db, current_recipe(), stale.source_sha256, finished=T0 + 1)
    worker, memo = FakeWorker({MATCH_UUID: stale.source_sha256}), auto.Memo()
    assert not step(factory, worker, memo, now=later())[1]["reparse_sent"]
    monkeypatch.setattr(auto, "current_recipe", lambda: "a-newer-recipe")
    worker.recipe = "a-newer-recipe"
    assert step(factory, worker, memo, now=later())[1]["reparse_sent"] == 1
    assert auto_rows(db)[-1].session_key == auto.tag("a-newer-recipe")


def test_the_recipe_changing_mid_flight_stores_nothing(factory, db, stale, condensed):
    worker, memo = FakeWorker({MATCH_UUID: stale.source_sha256}), auto.Memo()
    step(factory, worker, memo)
    worker.jobs["r0"] = {"status": "done", "result": result_of(condensed, recipe="a-newer-recipe")}
    held, counts = step(factory, worker, memo)
    [row] = auto_rows(db)
    assert row.status == "failed" and "another recipe" in row.error
    kept = db.query(Replay).one()
    assert kept.id == stale.id and kept.recipe == OLD


def test_a_match_deleted_mid_flight_never_comes_back(factory, db, stale, condensed):
    worker, memo = FakeWorker({MATCH_UUID: stale.source_sha256}), auto.Memo()
    step(factory, worker, memo)
    db.add(ReplayDeletion(match_uuid=MATCH_UUID, reason="asked"))    # what POST /admin/replays/delete does
    store._delete(db, db.get(Replay, stale.id))
    db.commit()
    worker.jobs["r0"] = {"status": "done", "result": result_of(condensed)}
    step(factory, worker, memo)
    [row] = auto_rows(db)
    assert row.status == "failed" and "deleted on request" in row.error
    assert db.query(Replay).count() == 0
    assert step(factory, worker, memo, now=later())[1]["reparse_sent"] == 0


def test_a_tombstoned_match_is_never_submitted(factory, db, stale):
    db.add(ReplayDeletion(match_uuid=MATCH_UUID, reason="asked"))
    db.commit()
    worker = FakeWorker({MATCH_UUID: stale.source_sha256})
    held, counts = step(factory, worker, auto.Memo())
    assert not worker.asked and held == frozenset()


def test_a_restart_collects_the_one_in_flight_and_submits_no_second(factory, db, stale, condensed):
    worker = FakeWorker({MATCH_UUID: stale.source_sha256})
    step(factory, worker, auto.Memo())
    held, counts = step(factory, worker, auto.Memo())               # a new process: nothing in memory
    assert worker.asked == [MATCH_UUID] and held == {stale.id}
    worker.jobs["r0"] = {"status": "done", "result": result_of(condensed)}
    assert step(factory, worker, auto.Memo())[1]["reparse_finished"] == 1
    assert db.query(Replay).one().recipe == current_recipe()


def test_a_file_evicted_since_the_index_was_read_is_no_attempt(factory, db, stale):
    worker, memo = FakeWorker({MATCH_UUID: stale.source_sha256}), auto.Memo()
    memo.index, memo.index_at = {MATCH_UUID: stale.source_sha256}, time.time()
    worker.files = {}                                               # evicted on the worker meanwhile
    held, counts = step(factory, worker, memo)
    assert counts["reparse_refused"] == 1 and not auto_rows(db) and memo.index is None
    held, counts = step(factory, worker, memo)                      # the index is read again
    assert held == frozenset() and not auto_rows(db)
    assert state_of(db, stale, index={}).state == "no_archive"


def test_the_archive_index_is_read_at_most_once_per_window(factory, db, stale):
    worker, memo = FakeWorker({MATCH_UUID: stale.source_sha256}), auto.Memo()
    worker.queued = 1                                               # keep it from submitting
    for t in (T0, T0 + 20, T0 + auto.INDEX_S - 1):
        step(factory, worker, memo, now=t)
    assert worker.index_reads == 1
    step(factory, worker, memo, now=T0 + auto.INDEX_S)
    assert worker.index_reads == 2
```

- [ ] **Step 2: Run them and see them fail**

Run: `PY -m pytest tests/replays/test_replay_reparse_auto.py -p no:cacheprovider -q`
Expected: the Task 4 tests pass; the new ones FAIL with `AttributeError: module 'app.services.replay_reparse_auto' has no attribute 'Memo'`.

- [ ] **Step 3: Write the step**

In `webapp/app/services/replay_reparse_auto.py`, change the imports to add:

```python
import uuid

from sqlalchemy import func

from app.replays.contract import current_recipe, load_pin
```

(`uuid` beside `logging`; `func` and the contract import beside the other third-party and `app` imports.) Add after `TAG_MAX`:

```python
SETTLE_S = 180                  # after one finishes, before the next: the dispatcher's PLAN_IDLE_S and a cycle
INDEX_S = 120                   # how long the worker's archive index is trusted
```

Append to the module:

```python
@dataclass
class Memo:
    """What one process remembers between cycles. Nothing here matters for correctness: a restart reads the
    archive index again and finds the re-parse in flight in `replay_uploads`."""
    index: dict[str, str] | None = None     # the worker's archive: match uuid -> sha256
    index_at: float | None = None


def _index(worker, memo: Memo, now: float) -> dict[str, str] | None:
    if memo.index is not None and memo.index_at is not None and now - memo.index_at < INDEX_S:
        return memo.index
    try:
        files = worker.archive().get("files", [])
    except uploads.WorkerError:
        memo.index, memo.index_at = None, None
        return None
    memo.index = {str(f["match_uuid"]).lower(): f.get("sha256") for f in files}
    memo.index_at = now
    return memo.index


def _unfinished(session, only_auto: bool) -> int:
    query = session.query(ReplayUpload.id).filter(ReplayUpload.status.in_(uploads.UNFINISHED))
    if only_auto:
        query = query.filter(ReplayUpload.session_key.like(uploads.AUTO_PREFIX + "%"))
    return query.count()


def _may_start(session, health: dict, control_in_flight: int, now: float) -> bool:
    """A friend's upload never waits behind a backlog, and one replay at a time is without control: no
    re-parse starts while an upload is queued on the worker or unfinished here, while any control job is in
    flight or on the worker, or within SETTLE_S of the last re-parse finishing (the dispatcher needs that
    long to send the replaced replay's rounds, so an empty control queue means what it says)."""
    control = health.get("control") or {}
    if health.get("queued") or control_in_flight or control.get("queued") or control.get("running"):
        return False
    if _unfinished(session, only_auto=False):
        return False
    last = (session.query(func.max(ReplayUpload.finished_at))
            .filter(ReplayUpload.session_key.like(uploads.AUTO_PREFIX + "%")).scalar())
    return last is None or now - _stamp(last) >= SETTLE_S


def _submit(session_factory, worker, memo: Memo, entry: Entry, recipe: str, counts: dict) -> None:
    try:
        job = worker.reparse(entry.match_uuid)
    except uploads.WorkerRefused:
        # 404: evicted since the index was read; 409: deleted on request. Neither is an attempt, and the
        # next look at the index or the tombstones answers it.
        memo.index, memo.index_at = None, None
        counts["reparse_refused"] += 1
        return
    except uploads.WorkerError:
        return                                   # unreachable, or its queue is full: the next cycle asks again
    session = session_factory()
    try:
        session.add(ReplayUpload(id=str(uuid.uuid4()), status="parsing", source_sha256=job.get("sha256"),
                                 size_bytes=job.get("size"), session_key=tag(recipe), client_ip=None,
                                 created_at=datetime.now(timezone.utc), worker_job_id=job.get("id")))
        session.commit()
    finally:
        session.rollback()
        session.close()
    counts["reparse_sent"] += 1
    log.info("automatic re-parse: asked for %s (%s), attempt %d", entry.match_uuid, entry.map_name, entry.attempts + 1)


def step(session, session_factory, worker, memo: Memo, control_in_flight: int, now: float,
         counts: dict) -> frozenset[int]:
    """One pass, inside the dispatcher's cycle and under its advisory lock. Returns the replay ids whose
    control rounds the dispatcher holds back: computing control on a blob about to be replaced is thrown
    away. `session` is the cycle's (reads only: a commit on it would release the lock); stores go through
    `session_factory`."""
    if not enabled():
        return frozenset()
    # The one in flight first, whatever the gates say: a result is collected even if the recipes have
    # since moved apart (it is then refused, and that is its attempt).
    if _unfinished(session, only_auto=True):
        counts["reparse_finished"] += uploads.collect_unfinished(session_factory, worker,
                                                                  session_prefix=uploads.AUTO_PREFIX)
        session.expire_all()
    recipe = current_recipe()
    if len(tag(recipe)) > TAG_MAX:
        return frozenset()
    try:
        health = worker.health()
    except uploads.WorkerError:
        return frozenset()
    # The two services deploy separately. A worker on other code would hand back a result that is still
    # stale, so nothing is asked of it; and nothing is held back, so control carries on as before.
    if health.get("recipe") != recipe or not (health.get("archive") or {}).get("enabled"):
        return frozenset()
    index = _index(worker, memo, now)
    entries = classify(session, recipe, index, load_pin().supported_builds, now)
    held = frozenset(e.replay_id for e in entries if e.state in HELD)
    if _unfinished(session, only_auto=True):
        return held                              # one at a time
    ready = sorted((e for e in entries if e.state == "eligible"), key=lambda e: e.rank)
    if ready and _may_start(session, health, control_in_flight, now):
        _submit(session_factory, worker, memo, ready[0], recipe, counts)
    return held
```

- [ ] **Step 4: Run the tests**

Run: `PY -m pytest tests/replays/test_replay_reparse_auto.py -p no:cacheprovider -q`
Expected: PASS.

Two things to check if one fails. `test_nothing_runs_while_the_switch_is_off` compares the counts with `{}`: a `defaultdict` that was never written to equals `{}`. And `test_a_file_evicted_since_the_index_was_read_is_no_attempt` relies on `_submit` clearing `memo.index` on a 404.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/replay_reparse_auto.py webapp/tests/replays/test_replay_reparse_auto.py
git commit -m "re-parse queue: the step that polls, gates and submits one re-parse at a time"
```

---

### Task 6: The dispatcher runs the step and holds back rounds that are about to be replaced

**Files:**
- Modify: `webapp/app/services/replay_control_remote.py` (`State`, `_sendable`, `_submit`, `cycle`, `run_forever`, `start`, the module docstring)
- Test: `webapp/tests/replays/test_replay_reparse_auto.py`

**Interfaces:**
- Consumes: `auto.step`, `auto.Memo`, `auto.enabled`, `auto.off_reason` (Tasks 4-5); `_sendable(state, now, planned, gaps_only, gap_prints)` and `_submit(session, client, state, now, counts)` as the first plan leaves them.
- Produces:
  - `State.reparse: auto.Memo`
  - `cycle(session_factory, client, state, now=None, worker=None) -> dict`; with `worker` (an `uploads.WorkerClient`) it runs the step. Counts gain `reparse_sent`, `reparse_finished`, `reparse_refused`, `held_back`.
  - `run_forever(session_factory, client, stop, every_s=CYCLE_S, worker=None)`

- [ ] **Step 1: Write the failing tests**

Append to `webapp/tests/replays/test_replay_reparse_auto.py`:

```python
# ---------------------------------------------------------------- inside the control dispatcher's cycle

from test_control_remote import FakeWorker as ControlWorker  # noqa: E402

from app.models.replay import ReplayRoundControl  # noqa: E402
from app.services import replay_control_remote as remote  # noqa: E402


def test_control_of_a_replay_waiting_for_its_reparse_is_held_back_then_sent(factory, db, stale, condensed):
    control, worker, state = ControlWorker(), FakeWorker({MATCH_UUID: stale.source_sha256}), remote.State()
    counts = remote.cycle(factory, control, state, now=time.time(), worker=worker)
    assert counts["reparse_sent"] == 1 and counts["sent"] == 0 and counts["gaps_sent"] == 0
    assert counts["held_back"] == stale.round_count and not control.tasks
    worker.jobs["r0"] = {"status": "done", "result": result_of(condensed)}
    counts = remote.cycle(factory, control, state, now=time.time() + remote.PLAN_IDLE_S + 1, worker=worker)
    assert counts["reparse_finished"] == 1 and counts["held_back"] == 0
    assert counts["sent"] == remote.IN_FLIGHT, "the replaced replay's rounds go out in the same cycle"
    new_id = db.query(Replay).one().id
    assert all(int(t["key"].split(":")[0]) == new_id for t in control.tasks.values())


@pytest.mark.parametrize("why", ["switch_off", "another_recipe", "no_worker_client"])
def test_control_carries_on_as_before_when_no_reparse_can_run(factory, db, stale, monkeypatch, why):
    worker = FakeWorker({MATCH_UUID: stale.source_sha256})
    if why == "switch_off":
        monkeypatch.setattr(settings, "replay_reparse_auto", False)
    elif why == "another_recipe":
        worker.recipe = "another-recipe"
    control = ControlWorker()
    counts = remote.cycle(factory, control, remote.State(), now=time.time(),
                          worker=None if why == "no_worker_client" else worker)
    assert counts["sent"] == remote.IN_FLIGHT and counts["held_back"] == 0 and not worker.asked


def test_no_reparse_starts_while_control_is_in_flight(factory, db, stale):
    control = ControlWorker(answer=lambda task: {"status": "queued"})
    worker, state = FakeWorker({MATCH_UUID: stale.source_sha256}), remote.State()
    state.in_flight["9:1:f"] = remote.InFlight("j-other", 9, 1, "f" * 16, "Ascent", 0.0)   # another replay's round
    control.tasks["j-other"] = {"key": "9:1:f", "map": "Ascent"}
    counts = remote.cycle(factory, control, state, now=time.time(), worker=worker)
    assert counts["reparse_sent"] == 0 and not worker.asked and counts["held_back"] == stale.round_count


def test_a_step_that_raises_never_stops_control(factory, db, stale, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("a bug in the step")

    monkeypatch.setattr(auto, "step", boom)
    control = ControlWorker()
    counts = remote.cycle(factory, control, remote.State(), now=time.time(),
                          worker=FakeWorker({MATCH_UUID: stale.source_sha256}))
    assert counts["sent"] == remote.IN_FLIGHT and counts["held_back"] == 0


def test_gaps_only_rounds_of_a_waiting_replay_are_held_back_too(factory, db, stale):
    from test_control_store import put_row

    for n in range(1, stale.round_count + 1):
        put_row(db, stale, n)                                       # current control, no gaps
    worker = FakeWorker({MATCH_UUID: stale.source_sha256})
    worker.queued = 1                                               # an upload is waiting: no re-parse yet
    control = ControlWorker()
    counts = remote.cycle(factory, control, remote.State(), now=time.time(), worker=worker)
    assert counts["gaps_sent"] == 0 and not control.tasks
    assert db.query(ReplayRoundControl).count() == stale.round_count


def test_start_hands_the_dispatcher_a_worker_client_only_when_the_switch_is_on(factory, monkeypatch):
    seen = {}

    class Thread:
        def __init__(self, target, args, kwargs, name, daemon):
            seen.update(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(remote.threading, "Thread", Thread)
    assert remote.start(factory) is not None and isinstance(seen["worker"], uploads.WorkerClient)
    monkeypatch.setattr(settings, "replay_reparse_auto", False)
    assert remote.start(factory) is not None and seen["worker"] is None
```

- [ ] **Step 2: Run them and see them fail**

Run: `PY -m pytest tests/replays/test_replay_reparse_auto.py -k "cycle or held or carries or in_flight or raises or start_hands" -p no:cacheprovider -q`
Expected: FAIL with `TypeError: cycle() got an unexpected keyword argument 'worker'`.

- [ ] **Step 3: Wire the step into the cycle**

In `webapp/app/services/replay_control_remote.py`, add to the imports:

```python
from app.services import replay_reparse_auto
from app.services import replay_upload as uploads
```

Add a field at the end of `State` (after `last_found`):

```python
    reparse: replay_reparse_auto.Memo = field(default_factory=replay_reparse_auto.Memo)
```

Replace `_sendable` with a version that takes the held replay ids:

```python
def _sendable(state: State, now: float, planned: list, gaps_only: bool, gap_prints: dict,
              held: frozenset = frozenset()) -> tuple[list, dict]:
    """The planned rounds that aren't in flight, haven't used up their tries, and aren't of a replay that is
    about to be re-parsed (`held`: its blob is about to be replaced); and each one's key."""
    todo, keys = [], {}
    for p in planned:
        if p.replay_id in held:
            continue
        key = _key(p, gaps_only, gap_prints)
        if key not in state.in_flight and state.may_try(key, now):
            todo.append(p)
            keys[id(p)] = key
    return todo, keys
```

In `_submit`, change the signature to

```python
def _submit(session, client, state: State, now: float, counts: dict, held: frozenset = frozenset()) -> None:
```

and its two `_sendable` calls to pass `held`, with the count of held control rounds after the first:

```python
    todo, keys = _sendable(state, now, planned, False, gap_prints, held)
    counts["held_back"] += sum(1 for p in planned if p.replay_id in held)
```

```python
    todo, keys = _sendable(state, now, replay_gaps.plan_gaps(session, planned, every), True, gap_prints, held)
```

Replace `cycle` with:

```python
def cycle(session_factory, client, state: State, now: float | None = None, worker=None) -> dict:
    """One pass: collect, the automatic re-parse step when `worker` (the upload client) is given, then
    submit. Returns what it did, by kind."""
    now = time.time() if now is None else now
    counts: dict[str, int] = {k: 0 for k in ("sent", "stored", "stored_failed", "skipped", "forgotten", "timed_out",
                                             "dropped_revision", "dropped_geometry", "infra_failed", "busy",
                                             "unreachable", "gaps_sent", "gaps_stored", "gaps_dropped",
                                             "gaps_skipped", "reparse_sent", "reparse_finished", "reparse_refused",
                                             "held_back")}
    session = session_factory()
    try:
        if session.get_bind().dialect.name == "postgresql":
            if not session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_ID}).scalar():
                counts["locked_out"] = 1
                return counts
        _collect(session_factory, client, state, now, counts)
        held: frozenset = frozenset()
        if worker is not None:
            try:
                held = replay_reparse_auto.step(session, session_factory, worker, state.reparse,
                                                len(state.in_flight), now, counts)
            except Exception:  # noqa: BLE001 - a re-parse problem must never stop map control
                log.exception("automatic re-parse step failed")
                held = frozenset()
        _submit(session, client, state, now, counts, held)
        return counts
    finally:
        session.rollback()
        session.close()
```

`_submit` returns early when it is throttled or full, before it counts `held_back`; that is fine, the count is for the log.

Replace `run_forever` and `start` with:

```python
def run_forever(session_factory, client, stop: threading.Event, every_s: float = CYCLE_S, worker=None) -> None:
    state = State()
    while not stop.wait(every_s):
        try:
            counts = cycle(session_factory, client, state, worker=worker)
        except Exception:  # noqa: BLE001 - the dispatcher must never take the site down
            log.exception("map control dispatch failed")
            continue
        did = {k: v for k, v in counts.items() if v}
        if did:
            log.info("map control dispatch: %s; %d in flight", ", ".join(f"{k} {v}" for k, v in sorted(did.items())),
                     len(state.in_flight))


def start(session_factory) -> threading.Event | None:
    """Starts the dispatcher thread when it is enabled; returns its stop event. With REPLAY_REPARSE_AUTO it
    also runs the automatic re-parse step (app/services/replay_reparse_auto.py)."""
    if not enabled():
        if settings.replay_reparse_auto and not settings.demo_mode:
            log.warning("automatic re-parse is set but not running: %s", replay_reparse_auto.off_reason())
        return None
    worker = uploads.client() if replay_reparse_auto.enabled() else None
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(session_factory, ControlClient(settings.replay_worker_url), stop),
                     kwargs={"worker": worker}, name="control-dispatch", daemon=True).start()
    log.info("map control dispatch on: every %ss, up to %d rounds in flight", CYCLE_S, IN_FLIGHT)
    if worker is not None:
        log.info("automatic re-parse on: one at a time, behind uploads, newest match first")
    return stop
```

In the module docstring, add a paragraph before the one that starts "Off unless REPLAY_CONTROL_REMOTE":

```
With REPLAY_REPARSE_AUTO, each cycle also runs the automatic re-parse step between the two
(app/services/replay_reparse_auto.py): it re-parses stale replays one at a time on the same worker, and names
the replays whose rounds this dispatcher holds back, since their blobs are about to be replaced.
```

- [ ] **Step 4: Run both test files and the isolation tests**

Run: `PY -m pytest tests/replays/test_replay_reparse_auto.py tests/replays/test_control_remote.py tests/replays/test_control_isolation.py -p no:cacheprovider -q`
Expected: PASS.

If `VALO_TEST_DATABASE_URL` is set, also run `PY -m pytest tests/replays/test_control_remote.py -k pg -p no:cacheprovider -q`. Expected: PASS; the step runs inside the cycle that holds the advisory lock, so one instance at a time runs it.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/replay_control_remote.py webapp/tests/replays/test_replay_reparse_auto.py
git commit -m "control dispatch: run the re-parse step and hold back rounds about to be replaced"
```

---

### Task 7: Progress without logs: the status route and `--status`

**Files:**
- Modify: `webapp/app/services/replay_reparse_auto.py` (`status`)
- Modify: `webapp/app/routers/replay_admin.py`
- Modify: `webapp/scripts/reparse_archive.py`
- Test: `webapp/tests/replays/test_replay_reparse_auto.py`

**Interfaces:**
- Consumes: `classify`, `off_reason`, `current_recipe`, `tag`, `TAG_MAX`.
- Produces:
  - `auto.status(session, worker) -> dict` with keys `running: bool`, `reason: str | None`, `site_recipe: str`, `worker_recipe: str | None`, `counts: dict[str, int]`, `gave_up: list[dict]` (each `match_uuid`, `map`, `attempts`, `last_error`), `control_rounds_waiting: int`, `gap_rounds_waiting: int`. `worker` may be None.
  - `GET /admin/replays/reparse/status`
  - `reparse_archive.describe(status: dict) -> list[str]` and the `--status` flag.

- [ ] **Step 1: Write the failing tests**

Append to `webapp/tests/replays/test_replay_reparse_auto.py`:

```python
# ---------------------------------------------------------------- progress


def test_status_counts_every_replay_and_names_the_ones_given_up_on(factory, db, stale):
    attempt(db, current_recipe(), stale.source_sha256)
    attempt(db, current_recipe(), stale.source_sha256, finished=T0 + 1)
    body = auto.status(db, FakeWorker({MATCH_UUID: stale.source_sha256}))
    assert body["running"] is True and body["reason"] is None
    assert body["site_recipe"] == body["worker_recipe"] == current_recipe()
    assert body["counts"] == {"gave_up": 1}
    assert body["gave_up"] == [{"match_uuid": MATCH_UUID, "map": stale.map_name, "attempts": 2,
                                "last_error": "parse failed"}]
    assert body["control_rounds_waiting"] == stale.round_count and body["gap_rounds_waiting"] == 0


@pytest.mark.parametrize("break_it, said", [
    (lambda w: setattr(w, "recipe", "another-recipe"), "another recipe"),
    (lambda w: setattr(w, "archive_on", False), "archive is off"),
    (lambda w: setattr(w, "down", True), "unreachable"),
])
def test_status_says_the_one_reason_nothing_is_running(factory, db, stale, break_it, said):
    worker = FakeWorker({MATCH_UUID: stale.source_sha256})
    break_it(worker)
    body = auto.status(db, worker)
    assert body["running"] is False and said in body["reason"]
    assert sum(body["counts"].values()) == 1, "the replays are still counted"


def test_status_with_the_switch_off_still_counts(factory, db, stale, monkeypatch):
    monkeypatch.setattr(settings, "replay_reparse_auto", False)
    body = auto.status(db, FakeWorker({MATCH_UUID: stale.source_sha256}))
    assert body["running"] is False and "REPLAY_REPARSE_AUTO" in body["reason"]
    assert body["counts"] == {"eligible": 1}
    assert auto.status(db, None)["counts"] == {"unknown": 1}, "no worker address: the archive can't be read"


def test_the_admin_route_answers_with_the_status(factory, db, stale, monkeypatch):
    from app.routers import replay_admin as admin

    worker = FakeWorker({MATCH_UUID: stale.source_sha256})
    monkeypatch.setattr(admin.uploads, "client", lambda: worker)
    body = admin.reparse_auto_status(db)
    assert body["counts"] == {"eligible": 1} and body["running"] is True
    route = next(r for r in admin.router.routes if r.path == "/admin/replays/reparse/status")
    assert route.methods == {"GET"} and any(d.call is admin.require_admin for d in route.dependant.dependencies)


def test_the_script_prints_the_status(monkeypatch, capsys):
    sys.path.insert(0, str(HERE.parents[1] / "scripts"))
    import reparse_archive

    body = {"running": False, "reason": "REPLAY_REPARSE_AUTO is not set", "site_recipe": "abc.c13.f1.a0",
            "worker_recipe": "abc.c13.f1.a0", "counts": {"current": 40, "eligible": 3, "gave_up": 1},
            "gave_up": [{"match_uuid": MATCH_UUID, "map": "Ascent", "attempts": 2, "last_error": "parse failed"}],
            "control_rounds_waiting": 12, "gap_rounds_waiting": 5}
    lines = reparse_archive.describe(body)
    assert lines[0] == "automatic re-parse: not running (REPLAY_REPARSE_AUTO is not set)"
    assert "  current 40, eligible 3, gave_up 1" in lines
    assert f"  gave up: {MATCH_UUID} (Ascent), 2 attempts: parse failed" in lines
    assert "  waiting: 12 round(s) for map control, 5 for timing gaps" in lines
    monkeypatch.setattr(reparse_archive, "call", lambda site, method, path: body)
    assert reparse_archive.main(["--status"]) == 0
    assert "automatic re-parse: not running" in capsys.readouterr().out
```

- [ ] **Step 2: Run them and see them fail**

Run: `PY -m pytest tests/replays/test_replay_reparse_auto.py -k "status or script" -p no:cacheprovider -q`
Expected: FAIL with `AttributeError: module 'app.services.replay_reparse_auto' has no attribute 'status'`.

- [ ] **Step 3: Write `status`**

In `webapp/app/services/replay_reparse_auto.py`, add `import time` beside `import logging`, `from collections import Counter, defaultdict` in place of the `defaultdict` import, and append:

```python
def status(session, worker) -> dict:
    """What the owner reads instead of logs (GET /admin/replays/reparse/status). All of it comes from the
    database and two worker calls, so it is right after a restart. `worker` is None without a worker address."""
    from app.services import replay_control, replay_gaps

    recipe = current_recipe()
    reason, worker_recipe, index = off_reason(), None, None
    if len(tag(recipe)) > TAG_MAX:
        reason = reason or "the recipe is too long for the attempt record"
    if worker is not None:
        try:
            health = worker.health()
            worker_recipe = health.get("recipe")
            if (health.get("archive") or {}).get("enabled"):
                index = {str(f["match_uuid"]).lower(): f.get("sha256") for f in worker.archive().get("files", [])}
            else:
                reason = reason or "the worker's archive is off"
            if worker_recipe != recipe:
                reason = reason or f"the worker is on another recipe ({worker_recipe or 'not reported'})"
        except uploads.WorkerError:
            index = None
            reason = reason or "the replay worker is unreachable"
    entries = classify(session, recipe, index, load_pin().supported_builds, time.time())
    planned = [p for p in replay_control.plan(session) if p.computable]
    gaps = replay_gaps.plan_gaps(session, planned, replay_control.plan(session, force=True))
    return {"running": reason is None, "reason": reason, "site_recipe": recipe, "worker_recipe": worker_recipe,
            "counts": dict(Counter(e.state for e in entries)),
            "gave_up": [{"match_uuid": e.match_uuid, "map": e.map_name, "attempts": e.attempts,
                         "last_error": e.last_error} for e in entries if e.state == "gave_up"],
            "control_rounds_waiting": len(planned), "gap_rounds_waiting": len(gaps)}
```

The two service imports are inside the function because `replay_control_remote` imports this module and those two import nothing of it; keeping them local keeps this module's import list to what `step` needs.

- [ ] **Step 4: Add the admin route**

In `webapp/app/routers/replay_admin.py`, add to the imports:

```python
from app.services import replay_reparse_auto
```

Add to the module docstring's route list:

```
    GET  /admin/replays/reparse/status  the automatic re-parse queue: whether it runs and why not, how many
                                     replays are current, waiting or given up on (and why), and the rounds
                                     still waiting for control and timing gaps.
```

Add after `reparse_status`:

```python
@router.get("/reparse/status", dependencies=[Depends(require_admin)])
def reparse_auto_status(db: Session = Depends(get_db)):
    worker = uploads.client() if settings.replay_worker_url else None
    return replay_reparse_auto.status(db, worker)
```

- [ ] **Step 5: Add `--status` to the script**

In `webapp/scripts/reparse_archive.py`, add to the docstring's usage block:

```
    .venv\\Scripts\\python.exe scripts\\reparse_archive.py --status
```

and after it: "`--status` prints the automatic re-parse queue's progress (app/services/replay_reparse_auto.py) and does nothing else."

Add above `main`:

```python
def describe(status: dict) -> list[str]:
    """The lines `--status` prints for GET /admin/replays/reparse/status."""
    state = "running" if status.get("running") else f"not running ({status.get('reason')})"
    lines = [f"automatic re-parse: {state}",
             f"  recipe: site {status.get('site_recipe')}, worker {status.get('worker_recipe') or 'not reported'}"]
    counts = status.get("counts") or {}
    lines.append("  " + (", ".join(f"{name} {counts[name]}" for name in sorted(counts)) or "no stored replays"))
    for entry in status.get("gave_up") or []:
        lines.append(f"  gave up: {entry['match_uuid']} ({entry.get('map')}), {entry.get('attempts')} attempts: "
                     f"{entry.get('last_error')}")
    lines.append(f"  waiting: {status.get('control_rounds_waiting', 0)} round(s) for map control, "
                 f"{status.get('gap_rounds_waiting', 0)} for timing gaps")
    return lines
```

In `main`, add the argument after `--dry-run`:

```python
    parser.add_argument("--status", action="store_true", help="print the automatic re-parse queue's progress and stop")
```

and right after `args = parser.parse_args(argv)`:

```python
    if args.status:
        try:
            print("\n".join(describe(call(args.site, "GET", "/admin/replays/reparse/status"))), flush=True)
        except AdminError as error:
            print(f"could not read the status: {error}", file=sys.stderr)
            return 1
        return 0
```

- [ ] **Step 6: Run the tests**

Run: `PY -m pytest tests/replays/test_replay_reparse_auto.py tests/replays/test_replay_archive_web.py -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add webapp/app/services/replay_reparse_auto.py webapp/app/routers/replay_admin.py webapp/scripts/reparse_archive.py webapp/tests/replays/test_replay_reparse_auto.py
git commit -m "re-parse queue: a status route and reparse_archive.py --status"
```

---

### Task 8: The switch in `render.yaml`, the docs, and the checks only the live services can give

**Files:**
- Modify: `render.yaml`, `webapp/app/main.py`, `webapp/RENDER_DEPLOY.md`, `docs/replay-viewer-plan.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing code reads.

- [ ] **Step 1: Declare the switch, commented out**

In `render.yaml`, add after the commented `REPLAY_CONTROL_REMOTE` lines of the `valowithfriendstracker` service:

```yaml
      # Stale replays re-parsed by the worker from its .vrf archive, one at a time behind uploads
      # (docs/superpowers/plans/2026-10-07-auto-reparse-queue.md). Off until the user turns it on: it
      # rewrites stored replays, and it follows every later recipe change by itself. Needs
      # REPLAY_CONTROL_REMOTE; set it in the dashboard (or uncomment) after the steps in RENDER_DEPLOY.md.
      # - key: REPLAY_REPARSE_AUTO
      #   value: "true"
```

- [ ] **Step 2: The lifespan comment**

In `webapp/app/main.py`, in `lifespan`, change the first comment's two lines to:

```python
    # Map control for new replays on the replay worker: off unless REPLAY_CONTROL_REMOTE is set
    # (app/services/replay_control_remote.py; the engine is never imported by the web app). With
    # REPLAY_REPARSE_AUTO the same thread also re-parses stale replays (app/services/replay_reparse_auto.py).
```

- [ ] **Step 3: The switch-on steps**

In `webapp/RENDER_DEPLOY.md`, add at the end:

```markdown
**Automatic re-parse (2026-10-07).** Off until `REPLAY_REPARSE_AUTO=true` is set on
`valowithfriendstracker` (never on `valomaths`). Before setting it:

1. Both services are on the merge that added it, and the timing-gaps-on-the-worker merge before it. Check
   with `scripts\reparse_archive.py --status`: the site's and the worker's recipes are the same.
2. Re-parse one match by hand, `scripts\reparse_archive.py --match <uuid>`, and open its replay page: the new
   data is there, control comes back within a few minutes, and the per-kill Impact numbers are still shown.
3. Look at the worker's memory graph for that parse.

Then set the variable. The web service restarts, and `--status` reads "running". It takes the newest match
first, one at a time, and waits for uploads and for map control to drain between matches, so a full archive
takes days of the worker's idle time. Unsetting the variable stops new re-parses at the next restart; one
already on the worker still finishes and is stored. A replay listed under "gave up" failed twice under the
current recipe; it gets fresh attempts when the recipe next changes.
```

- [ ] **Step 4: The freshness rule's new route**

In `docs/replay-viewer-plan.md`, in the section "Freshness: the recipe", replace the last bullet ("A stale **local** replay is re-ingested...") with:

```markdown
- A stale **local** replay is re-ingested from the archive, after verifying the `.vrf`'s sha256. A stale
  **upload** still plays; when the worker has its `.vrf` archived, it is re-parsed automatically
  (`REPLAY_REPARSE_AUTO`; `docs/superpowers/plans/2026-10-07-auto-reparse-queue.md`), and otherwise it needs a
  re-upload.
```

- [ ] **Step 5: The whole replay suite**

Run: `PY -m pytest tests/replays -k "not pg" -p no:cacheprovider -q`
Expected: the baseline's pass count plus this plan's new tests; the only failure is the known Windows one.

If `VALO_TEST_DATABASE_URL` is set, also run `PY -m pytest tests/replays -k pg -p no:cacheprovider -q`. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add render.yaml webapp/app/main.py webapp/RENDER_DEPLOY.md docs/replay-viewer-plan.md
git commit -m "docs: the automatic re-parse switch and its switch-on steps"
```

- [ ] **Step 7: Write the live checks into the PR description**

These cannot be checked from the tests; list them in the PR for the owner:

1. After a normal deploy of both services, `--status` shows the same recipe for the site and the worker. This has never been compared across the two images.
2. The time for one parse on the 2-CPU worker, and for one match's control and gaps, which together set how long the archive takes.
3. An upload sent while a re-parse runs starts as soon as that parse ends.
4. A deploy of either service while a re-parse is queued: the row fails with "another recipe" or is collected, and nothing is stored twice.
5. Memory and disk on the worker during a re-parse of the largest archived file.
