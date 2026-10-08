"""The automatic re-parse queue's site half (app/services/replay_reparse_auto.py;
docs/superpowers/plans/2026-10-07-auto-reparse-queue-impl.md, tasks 4-7): the switch, and which stored replays
are eligible, and the pass that recovers, reserves and submits one attempt. Synthetic rows on a throwaway
sqlite store and a protocol fake of the worker; the real worker is in test_replay_archive_web.py."""

import sys
import threading
import uuid
from dataclasses import replace as dc_replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_control_remote import FakeWorker, all_control, put_gap_run  # noqa: E402
from test_control_store import CONTROL_TABLES, put_row  # noqa: E402
from test_replay_store import add_match, condensed, pg  # noqa: E402,F401  (fixtures)
from test_replay_upload import ATTEMPT, AutoWorker, make_stale, reserve, result_of  # noqa: E402

from app.config import Settings, settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models.match import Match, MatchSource  # noqa: E402
from app.models.replay import Replay, ReplayDeletion, ReplayUpload  # noqa: E402
from app.replays import store  # noqa: E402
from app.services import replay_control as rc  # noqa: E402
from app.services import replay_control_remote as remote  # noqa: E402
from app.services import replay_reparse_auto as auto  # noqa: E402
from app.services import replay_upload as uploads  # noqa: E402

OLD, NEW = "recipe-a", "recipe-b"
BUILD = "++Ares-Core+release-13.06"
BUILDS = frozenset({BUILD})
T0 = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
NOW = T0.timestamp()


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[*CONTROL_TABLES, ReplayUpload.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def match_uuid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012x}"


def sha(n: int) -> str:
    return f"{n:064x}"


def add_replay(db, n: int, *, recipe=OLD, source="upload", played: datetime | None = None,
               created: datetime | None = None, build=BUILD) -> Replay:
    """Replay `n`: linked to a match played at `played`, or unlinked and uploaded at `created`."""
    match_id = None
    if played is not None:
        match = Match(external_id=match_uuid(n), source=MatchSource.SCRAPED, map_name="Ascent", played_at=played,
                      team1_rounds_won=13, team2_rounds_won=7)
        db.add(match)
        db.flush()
        match_id = match.id
    replay = Replay(match_uuid=match_uuid(n), match_id=match_id, map_name="Ascent", round_count=20, format_version=1,
                    recipe=recipe, game_branch=build, source_sha256=sha(n), source=source,
                    link_status="linked" if played is not None else "unlinked", link_inputs={},
                    created_at=created or T0 - timedelta(days=30))
    db.add(replay)
    db.commit()
    return replay


def add_attempt(db, replay: Replay, *, target=NEW, phase="accepted", status="failed", error="parse failed",
                finished: datetime | None = None) -> ReplayUpload:
    """An automatic attempt for `replay` as it is now. Unfinished when `status` is queued or parsing."""
    done = status not in uploads.UNFINISHED
    upload = ReplayUpload(id=str(uuid.uuid4()), status=status, error=error if done else None,
                          source_sha256=replay.source_sha256, session_key=uploads.auto_tag(target),
                          created_at=(finished or T0) - timedelta(minutes=5),
                          finished_at=(finished or T0) if done else None,
                          auto_context={**uploads.new_auto_context(replay, target), "phase": phase})
    db.add(upload)
    db.commit()
    return upload


def index(*numbers: int) -> dict:
    return {match_uuid(n): sha(n) for n in numbers}


def states(db, idx, now=NOW, recipe=NEW) -> dict:
    return {int(e.match_uuid[-12:], 16): e.state for e in auto.classify(db, recipe, idx, BUILDS, now)}


# ---------------------------------------------------------------- the switch


def test_the_switch_is_off_by_default_and_every_prerequisite_is_named(monkeypatch):
    assert Settings.model_fields["replay_reparse_auto"].default is False
    for name, value in (("demo_mode", False), ("replay_reparse_auto", True), ("replay_control_remote", True),
                        ("replay_worker_url", "http://127.0.0.1:9")):
        monkeypatch.setattr(settings, name, value)
    assert auto.off_reason(NEW) is None
    for name, value, said in (("demo_mode", True, "demo mode"), ("replay_reparse_auto", False, "REPLAY_REPARSE_AUTO"),
                              ("replay_control_remote", False, "REPLAY_CONTROL_REMOTE"),
                              ("replay_worker_url", None, "worker URL")):
        with monkeypatch.context() as one:
            one.setattr(settings, name, value)
            assert said in auto.off_reason(NEW), name
    monkeypatch.setattr(settings, "demo_mode", True)
    assert auto.off_reason(NEW) == "demo mode", "the demo never starts one, whatever else is set"


def test_a_recipe_too_long_for_the_tag_stops_the_queue(monkeypatch):
    for name, value in (("demo_mode", False), ("replay_reparse_auto", True), ("replay_control_remote", True),
                        ("replay_worker_url", "http://127.0.0.1:9")):
        monkeypatch.setattr(settings, name, value)
    fits = "r" * (auto.TAG_MAX - len(uploads.AUTO_PREFIX))
    assert auto.off_reason(fits) is None and len(uploads.auto_tag(fits)) == ReplayUpload.session_key.type.length
    assert "too long" in auto.off_reason(fits + "r")
    monkeypatch.setattr(uploads, "_site_recipe", None)
    assert auto.off_reason() is None, "the recipe this checkout stamps fits"


def test_the_archive_index_is_unknown_unless_it_was_read():
    assert auto.index_of(None) is None and auto.index_of({}) is None and auto.index_of({"files": "x"}) is None
    assert auto.index_of({"files": []}) == {}
    assert auto.index_of({"files": [{"match_uuid": match_uuid(1).upper(), "sha256": sha(1)}, {"sha256": "x"}]}) == index(1)


# ---------------------------------------------------------------- eligibility


def test_every_stored_replay_gets_one_state(db):
    add_replay(db, 1)                                   # stale, archived, never tried
    add_replay(db, 2, recipe=NEW)                       # already on the target
    add_replay(db, 3, source="local")                   # a stale local ingest
    add_replay(db, 4)                                   # not in the archive
    add_replay(db, 5)                                   # the archive holds another recording of the match
    add_replay(db, 6, build="++Ares-Core+release-1.00")
    add_replay(db, 7)
    db.add(ReplayDeletion(match_uuid=match_uuid(7), reason="asked"))
    db.commit()
    idx = {**index(1, 2, 3, 6, 7), match_uuid(5): sha(99)}
    assert states(db, idx) == {1: "eligible", 2: "current", 3: "local", 4: "no_archive", 5: "other_recording",
                               6: "unsupported_build", 7: "deleted"}
    assert set(states(db, idx).values()) <= set(auto.STATES)
    # An index that couldn't be read is not an empty archive: nothing becomes eligible or "no archive".
    assert states(db, None) == {1: "unknown", 2: "current", 3: "local", 4: "unknown", 5: "unknown",
                                6: "unsupported_build", 7: "deleted"}
    entries = auto.classify(db, NEW, idx, BUILDS, NOW)
    assert auto.held(entries) == frozenset({db.query(Replay).filter(Replay.match_uuid == match_uuid(1)).one().id})
    first = entries[0]
    assert (first.map_name, first.attempts, first.last_error) == ("Ascent", 0, None)


def test_a_stale_local_replay_with_an_archived_file_stays_local(db):
    """The first review's finding 6: the same file being archived does not make a local ingest the queue's."""
    stale = add_replay(db, 1, source="local")
    add_replay(db, 2, source="local", recipe=NEW)
    add_replay(db, 3, recipe=NEW)
    entries = auto.classify(db, NEW, index(1, 2, 3), BUILDS, NOW)
    assert {e.replay_id: e.state for e in entries} == {1: "local", 2: "local", 3: "current"}
    assert auto.held(entries) == frozenset(), "its control stays with the ordinary dispatch"
    add_attempt(db, stale, status="parsing", error=None)   # even a row naming it changes nothing
    assert states(db, index(1, 2, 3))[1] == "local"


def test_a_tombstone_wins_over_every_other_state(db):
    for n, kwargs in ((1, {}), (2, {"source": "local"}), (3, {"recipe": NEW}), (4, {})):
        add_replay(db, n, **kwargs)
        db.add(ReplayDeletion(match_uuid=match_uuid(n), reason="asked"))
    add_attempt(db, db.query(Replay).filter(Replay.match_uuid == match_uuid(4)).one(), status="parsing")
    db.commit()
    assert set(states(db, index(1, 2, 3, 4)).values()) == {"deleted"}
    assert auto.held(auto.classify(db, NEW, index(1, 2, 3, 4), BUILDS, NOW)) == frozenset()


def test_the_order_is_newest_linked_match_then_newest_unlinked_upload(db):
    add_replay(db, 1, played=T0 - timedelta(days=3))
    add_replay(db, 2, created=T0 - timedelta(days=1))                    # unlinked, uploaded yesterday
    add_replay(db, 3, played=T0 - timedelta(days=1), created=T0 - timedelta(days=40))
    add_replay(db, 4, created=T0 - timedelta(hours=1))                   # unlinked, uploaded an hour ago
    entries = auto.classify(db, NEW, index(1, 2, 3, 4), BUILDS, NOW)
    assert [int(e.match_uuid[-12:], 16) for e in entries] == [3, 1, 4, 2]
    assert [e.rank for e in entries] == [0, 1, 2, 3]


def test_one_accepted_failure_backs_off_for_an_hour_and_two_give_up(db):
    replay = add_replay(db, 1)
    add_attempt(db, replay, finished=T0 - timedelta(minutes=10))
    [entry] = auto.classify(db, NEW, index(1), BUILDS, NOW)
    assert (entry.state, entry.attempts, entry.last_error) == ("backoff", 1, "parse failed")
    assert states(db, index(1), now=NOW + auto.BACKOFF_S - 601) == {1: "backoff"}
    assert states(db, index(1), now=NOW + auto.BACKOFF_S - 599) == {1: "eligible"}
    add_attempt(db, replay, finished=T0 + timedelta(hours=2), error="parse timed out")
    [entry] = auto.classify(db, NEW, index(1), BUILDS, NOW + 10 * auto.BACKOFF_S)
    assert (entry.state, entry.attempts, entry.last_error) == ("gave_up", auto.MAX_TRIES, "parse timed out")
    assert auto.held([entry]) == frozenset(), "a replay that is given up on gets its control back"


def test_a_contract_refusal_after_acceptance_is_final_after_one(db):
    add_attempt(db, add_replay(db, 1), error="not condensable: replay_build", finished=T0 - timedelta(days=2))
    [entry] = auto.classify(db, NEW, index(1), BUILDS, NOW)
    assert (entry.state, entry.attempts) == ("gave_up", 1)


def test_only_accepted_attempts_for_this_file_and_target_count(db):
    replay = add_replay(db, 1)
    other = add_replay(db, 2)
    recent = T0 - timedelta(minutes=1)
    add_attempt(db, replay, phase="refused_before_acceptance", error="closed before the worker accepted it",
                finished=recent)                                    # used no parse
    add_attempt(db, replay, phase="reserved", error="an automatic re-parse row without a valid context",
                finished=recent)                                    # never accepted either
    add_attempt(db, replay, target=OLD, finished=recent)            # two tries under the previous recipe
    add_attempt(db, replay, target=OLD, finished=recent)
    add_attempt(db, other, finished=recent)                         # another match's try
    replay.source_sha256 = sha(11)                                  # as if an earlier try was for another file
    add_attempt(db, replay, finished=recent)
    add_attempt(db, replay, finished=recent)
    replay.source_sha256 = sha(1)
    broken = add_attempt(db, replay, finished=recent)               # a row whose context can't be read
    broken.auto_context = {"version": 2}
    db.commit()
    entries = {e.replay_id: e for e in auto.classify(db, NEW, {**index(1, 2)}, BUILDS, NOW)}
    assert (entries[replay.id].state, entries[replay.id].attempts) == ("eligible", 0)
    assert (entries[other.id].state, entries[other.id].attempts) == ("backoff", 1)


def test_a_new_target_recipe_starts_the_count_again(db):
    replay = add_replay(db, 1)
    add_attempt(db, replay, finished=T0 - timedelta(hours=5))
    add_attempt(db, replay, finished=T0 - timedelta(hours=3))
    assert states(db, index(1)) == {1: "gave_up"}
    assert states(db, index(1), recipe="recipe-c") == {1: "eligible"}


@pytest.mark.parametrize("phase, status, state", [("reserved", "queued", "resolving"),
                                                  ("accepted", "parsing", "in_flight")])
@pytest.mark.parametrize("target", [NEW, OLD, "recipe-c"])
def test_an_unfinished_attempt_under_any_recipe_holds_its_replay_and_blocks_the_site(db, phase, status, state, target):
    replay = add_replay(db, 1)
    add_replay(db, 2)
    for _ in range(auto.MAX_TRIES):     # even a replay that would otherwise be given up on
        add_attempt(db, replay, finished=T0 - timedelta(hours=5))
    waiting = add_attempt(db, replay, target=target, phase=phase, status=status)
    assert states(db, index(1, 2)) == {1: state, 2: "eligible"}
    assert states(db, None)[1] == state, "known from the database alone"
    assert [row.id for row in auto.unfinished_attempts(db)] == [waiting.id]
    assert auto.held(auto.classify(db, NEW, index(1, 2), BUILDS, NOW)) == frozenset({1, 2})


def test_unfinished_attempts_are_only_the_automatic_ones_and_include_unreadable_rows(db):
    replay = add_replay(db, 1)
    db.add(ReplayUpload(id=str(uuid.uuid4()), status="parsing", session_key="sess", created_at=T0))
    db.add(ReplayUpload(id=str(uuid.uuid4()), status="queued", session_key="auto_reparse%", created_at=T0))
    add_attempt(db, replay, status="stored", error=None)
    broken = add_attempt(db, replay, status="queued", phase="reserved")
    broken.auto_context = None
    db.commit()
    assert [row.id for row in auto.unfinished_attempts(db)] == [broken.id], "it still blocks a new attempt"
    assert states(db, index(1)) == {1: "backoff"}, "but it names no replay to hold"


def test_classifying_reads_and_never_writes(db):
    add_attempt(db, add_replay(db, 1), status="parsing")
    add_replay(db, 2)
    before = (db.query(Replay).count(), db.query(ReplayUpload).count())
    auto.classify(db, NEW, index(1, 2), BUILDS, NOW)
    assert not db.new and not db.dirty and not db.deleted
    assert (db.query(Replay).count(), db.query(ReplayUpload).count()) == before


# ---------------------------------------------------------------- one pass: recover, reserve, submit (task 5)
# A protocol fake: the worker's receipts are kept apart from its jobs, as on its disk, and every job it ever
# accepted is counted. The real worker is driven by the same step in test_replay_archive_web.py.


class ProtocolWorker:
    def __init__(self, recipe=NEW, files: dict | None = None):
        self.health_body = {"ok": True, "queued": 0, "recipe": recipe,
                            "archive": {"enabled": True, "reparse_protocol": 1},
                            "control": {"enabled": True, "gaps_protocol": 1, "queued": 0, "running": 0}}
        self.files = dict(files or {})     # match uuid -> sha256 of the archived recording
        self.receipts: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.accepted = 0                  # jobs ever started
        self.calls: list[str] = []
        self.acks: list = []
        self.down = False                  # nothing answers
        self.lose_reply = False            # the next POST is accepted, and its answer never arrives
        self.full = False
        self.close_races = False           # the lookup says unknown, then the close finds the job

    def _say(self, name):
        self.calls.append(name)
        if self.down:
            raise uploads.WorkerError("the replay worker is unreachable")

    def _receipt(self, attempt_id):
        receipt = self.receipts[attempt_id]
        if receipt["state"] == "closed":
            return {"code": "closed", "attempt_id": attempt_id, "state": "closed"}
        job = self.jobs.get(receipt["job_id"])
        return {"code": "accepted", "state": "accepted", "attempt_id": attempt_id, "job_id": receipt["job_id"],
                "match_uuid": receipt["match_uuid"], "sha256": receipt["sha256"], "size": 4321,
                "job_status": job["status"] if job else "expired"}

    def health(self):
        self._say("health")
        return self.health_body

    def archive(self):
        self._say("archive")
        return {"files": [{"match_uuid": uuid_, "sha256": sha_} for uuid_, sha_ in self.files.items()]}

    def reparse(self, match_uuid, *, attempt_id=None, expected_sha256=None):
        self._say("reparse")
        assert attempt_id is not None, "the automatic queue never uses the manual route"
        if attempt_id in self.receipts:
            return self._receipt(attempt_id)
        if match_uuid not in self.files:
            return {"code": "no_archived_file"}
        if self.files[match_uuid] != expected_sha256:
            return {"code": "sha_mismatch", "sha256": self.files[match_uuid]}
        if self.full:
            return {"code": "queue_full"}
        job_id = uploads.auto_job_id(attempt_id)
        self.receipts[attempt_id] = {"state": "accepted", "job_id": job_id, "match_uuid": match_uuid,
                                     "sha256": expected_sha256}
        self.jobs[job_id] = {"status": "parsing"}
        self.accepted += 1
        if self.lose_reply:
            self.lose_reply = False
            raise uploads.WorkerError("the replay worker is unreachable")
        return self._receipt(attempt_id)

    def reparse_attempt(self, attempt_id):
        self._say("lookup")
        if attempt_id not in self.receipts or self.close_races:
            return {"code": "unknown_attempt"}
        return self._receipt(attempt_id)

    def close_reparse_attempt(self, attempt_id, match_uuid, expected_sha256):
        self._say("close")
        self.receipts.setdefault(attempt_id, {"state": "closed"})
        return self._receipt(attempt_id)

    def job(self, job_id):
        self._say("job")
        if job_id not in self.jobs:
            raise uploads.WorkerRefused("no such job", 404, {})
        return {"id": job_id, **self.jobs[job_id]}

    def ack(self, job_id, body):
        self.acks.append((job_id, body))
        return {"archived": True, "result": "kept"}

    def health_error(self, *args, **kwargs):
        raise uploads.WorkerError("the replay worker is unreachable")


@pytest.fixture
def on(db, monkeypatch):
    """The switch on, this site on NEW, and a factory on the test's database."""
    monkeypatch.setattr(settings, "replay_reparse_auto", True)
    monkeypatch.setattr(settings, "replay_control_remote", True)
    monkeypatch.setattr(settings, "replay_worker_url", "http://127.0.0.1:9")
    monkeypatch.setattr(uploads, "_site_recipe", NEW)
    monkeypatch.setattr(auto, "_builds", BUILDS)
    return sessionmaker(bind=db.get_bind())


def run(factory, worker, memo=None, now=NOW, in_flight=0):
    counts: dict = {}
    holding = auto.step(factory, worker, memo if memo is not None else auto.Memo(), in_flight, now, counts)
    return holding, {name[len("reparse_"):]: n for name, n in counts.items() if n}


def attempts(db) -> list[ReplayUpload]:
    db.expire_all()
    return auto.unfinished_attempts(db) + [u for u in auto._settled(db)][::-1]


def phase(upload) -> tuple:
    return upload.status, upload.auto_context["phase"]


def stale_one(db, n=1) -> tuple[Replay, ProtocolWorker]:
    replay = add_replay(db, n, played=T0 - timedelta(days=n))
    return replay, ProtocolWorker(files={match_uuid(n): sha(n)})


def test_a_stale_uploaded_replay_is_reserved_committed_and_then_sent_once(db, on):
    replay, worker = stale_one(db)
    memo = auto.Memo()
    holding, counts = run(on, worker, memo)
    assert holding == {replay.id} and counts == {"reserved": 1, "sent": 1}
    [row] = attempts(db)
    assert phase(row) == ("parsing", "accepted") and row.worker_job_id == uploads.auto_job_id(row.id)
    assert row.session_key == uploads.auto_tag(NEW) and row.size_bytes == 4321
    assert row.auto_context == {**uploads.new_auto_context(replay, NEW), "phase": "accepted"}
    assert worker.calls == ["health", "archive", "reparse"], "the receipt is the POST's own answer"
    holding, counts = run(on, worker, memo, now=NOW + 20)
    assert holding == {replay.id} and counts == {} and worker.accepted == 1 and len(attempts(db)) == 1


def test_a_lost_response_recovers_the_same_attempt_and_one_job(db, on):
    replay, worker = stale_one(db)
    worker.lose_reply = True
    assert run(on, worker)[1] == {"reserved": 1}
    [row] = attempts(db)
    assert phase(row) == ("queued", "reserved") and row.worker_job_id is None and worker.accepted == 1
    worker.calls.clear()
    holding, counts = run(on, worker, auto.Memo(), now=NOW + 20)   # another process: no memo
    [again] = attempts(db)
    assert again.id == row.id and phase(again) == ("parsing", "accepted") and counts == {"recovered": 1}
    assert "reparse" not in worker.calls, "the receipt is found by its id: nothing is sent twice"
    assert worker.accepted == 1 and list(worker.jobs) == [uploads.auto_job_id(row.id)] and holding == {replay.id}


def test_a_restart_before_the_first_post_sends_the_same_identity(db, on):
    replay = add_replay(db, 1, played=T0)
    reserved = add_attempt(db, replay, phase="reserved", status="queued")
    worker = ProtocolWorker(files=index(1))
    assert worker.reparse_attempt(reserved.id)["code"] == "unknown_attempt"
    holding, counts = run(on, worker)
    [row] = attempts(db)
    assert row.id == reserved.id and phase(row) == ("parsing", "accepted") and counts == {"recovered": 1}
    assert worker.accepted == 1 and list(worker.receipts) == [reserved.id] and holding == {replay.id}


def test_a_worker_that_is_down_after_the_reservation_keeps_it_and_blocks_another(db, on):
    replay, worker = stale_one(db)

    def lost(*args, **kwargs):
        raise uploads.WorkerError("the replay worker is unreachable")

    worker.reparse = lost   # the POST never reaches the worker
    assert run(on, worker)[1] == {"reserved": 1}
    worker.down = True
    for later in (20, 40):
        holding, counts = run(on, worker, auto.Memo(), now=NOW + later)
        assert (holding, counts) == (frozenset(), {"deferred": 1}), "an unreachable worker: nothing is decided"
    [row] = attempts(db)
    assert phase(row) == ("queued", "reserved") and worker.accepted == 0


def test_an_acceptance_that_could_not_be_recorded_is_found_again_by_its_id(db, on, monkeypatch):
    from sqlalchemy.exc import OperationalError

    replay, worker = stale_one(db)
    real = uploads.bind_accepted

    def failing(session, upload_id, receipt):
        raise OperationalError("UPDATE replay_uploads", {}, Exception("the connection dropped"))

    monkeypatch.setattr(uploads, "bind_accepted", failing)
    assert run(on, worker)[1] == {"reserved": 1}
    assert run(on, worker, now=NOW + 20)[1] == {}, "still unrecorded: it stays the one unfinished attempt"
    [row] = attempts(db)
    assert phase(row) == ("queued", "reserved") and worker.accepted == 1
    monkeypatch.setattr(uploads, "bind_accepted", real)
    assert run(on, worker, now=NOW + 40)[1] == {"recovered": 1}
    [row] = attempts(db)
    assert phase(row) == ("parsing", "accepted") and worker.accepted == 1


def test_a_reservation_that_fails_to_commit_asks_the_worker_nothing(db, on):
    from sqlalchemy.exc import OperationalError

    _, worker = stale_one(db)

    def factory():
        session = on()
        commit = session.commit

        def failing():
            if any(isinstance(row, ReplayUpload) for row in session.new):
                raise OperationalError("INSERT INTO replay_uploads", {}, Exception("the connection dropped"))
            commit()

        session.commit = failing
        return session

    with pytest.raises(OperationalError):
        run(factory, worker)
    assert "reparse" not in worker.calls and worker.accepted == 0 and attempts(db) == []


@pytest.mark.parametrize("change, said", [("gone", "no archived file"), ("other", "another file")])
def test_a_definite_refusal_before_acceptance_uses_no_attempt(db, on, change, said):
    replay, worker = stale_one(db)
    memo = auto.Memo(index=index(1), index_at=NOW)   # what the index said a minute ago
    worker.files = {} if change == "gone" else {match_uuid(1): sha(99)}
    holding, counts = run(on, worker, memo)
    [row] = attempts(db)
    assert phase(row) == ("failed", "refused_before_acceptance") and said in row.error
    assert counts == {"reserved": 1, "refused": 1} and worker.accepted == 0 and worker.receipts == {}
    assert memo.index is None, "the index was wrong: it is read again"
    [entry] = auto.classify(db, NEW, index(1), BUILDS, NOW)
    assert (entry.state, entry.attempts) == ("eligible", 0), "a refusal is not a try"
    # Even against an index that still (wrongly) lists the file, the same replay isn't picked again at once.
    worker.archive = lambda: {"files": [{"match_uuid": match_uuid(1), "sha256": sha(1)}]}
    assert run(on, worker, memo, now=NOW + 20)[1] == {} and len(attempts(db)) == 1
    worker.files = index(1)
    assert run(on, worker, memo, now=NOW + auto.BACKOFF_S + 1)[1] == {"reserved": 1, "sent": 1}


def test_an_accepted_failure_backs_off_then_one_more_and_no_third(db, on):
    replay, worker = stale_one(db)
    memo = auto.Memo()
    run(on, worker, memo)
    [row] = attempts(db)
    worker.jobs[row.worker_job_id] = {"status": "failed", "error": "parse failed"}
    assert run(on, worker, memo, now=NOW + 20)[1] == {"finished": 1}
    [row] = attempts(db)
    assert row.status == "failed" and "parse failed" in row.error and phase(row)[1] == "accepted"
    assert epoch_of(row.finished_at) == NOW + 20, "the injected clock stamps the settlement"
    holding, counts = run(on, worker, memo, now=NOW + 600)
    assert holding == {replay.id} and counts == {}, "backing off: held, and nothing new"
    second_at = NOW + 20 + auto.BACKOFF_S + 1
    assert run(on, worker, memo, now=second_at)[1] == {"reserved": 1, "sent": 1}
    second = attempts(db)[0]
    assert second.id != row.id and worker.accepted == 2
    worker.jobs[second.worker_job_id] = {"status": "failed", "error": "parse failed"}
    assert run(on, worker, memo, now=second_at + 20)[1] == {"finished": 1}
    holding, counts = run(on, worker, memo, now=second_at + 3 * auto.BACKOFF_S)
    assert holding == frozenset() and counts == {} and worker.accepted == 2, "gave up: its control is released"


def test_a_contract_refusal_after_acceptance_ends_the_tries_at_one(db, on):
    replay, worker = stale_one(db)
    memo = auto.Memo()
    run(on, worker, memo)
    [row] = attempts(db)
    worker.jobs[row.worker_job_id] = {"status": "failed", "error": f"{auto.FINAL_REFUSAL}: build mismatch"}
    assert run(on, worker, memo, now=NOW + 20)[1] == {"finished": 1}
    assert run(on, worker, memo, now=NOW + 5 * auto.BACKOFF_S) == (frozenset(), {}) and worker.accepted == 1


def test_an_accepted_result_that_expired_counts_as_the_try_it_was(db, on):
    replay, worker = stale_one(db)
    memo = auto.Memo()
    run(on, worker, memo)
    [row] = attempts(db)
    worker.jobs.clear()   # the worker swept the result; only the receipt is left
    assert run(on, worker, memo, now=NOW + 20)[1] == {"finished": 1}
    [row] = attempts(db)
    assert row.status == "failed" and "expired" in row.error and phase(row)[1] == "accepted"
    [entry] = auto.classify(db, NEW, index(1), BUILDS, NOW + 30)
    assert (entry.state, entry.attempts) == ("backoff", 1)
    # The same id can still fetch its receipt; it can never start another job.
    assert worker.reparse(match_uuid(1), attempt_id=row.id, expected_sha256=sha(1))["job_status"] == "expired"
    assert worker.accepted == 1


def epoch_of(when) -> float:
    return auto.epoch(when)


@pytest.mark.parametrize("switch", [True, False])
@pytest.mark.parametrize("state", ["reserved", "accepted"])
def test_an_older_site_beside_a_newer_worker_leaves_the_newer_attempt_alone(db, on, monkeypatch, state, switch):
    """During a deploy: site process A (on OLD) still runs while the worker and site B are on NEW."""
    replay = add_replay(db, 1, recipe="recipe-0", played=T0)
    row = add_attempt(db, replay, target=NEW, phase=state, status="queued" if state == "reserved" else "parsing")
    before = (row.status, dict(row.auto_context), row.error, row.finished_at)
    worker = ProtocolWorker(recipe=NEW, files=index(1))
    monkeypatch.setattr(uploads, "_site_recipe", OLD)
    monkeypatch.setattr(settings, "replay_reparse_auto", switch)
    holding, counts = run(on, worker)
    [row] = attempts(db)
    assert (row.status, row.auto_context, row.error, row.finished_at) == before
    assert holding == frozenset() and counts == {"deferred": 1}
    assert worker.calls == ["health"] and worker.receipts == {}, "not sent, not closed, not failed"
    monkeypatch.setattr(uploads, "_site_recipe", NEW)       # site B's own pass then resolves it
    monkeypatch.setattr(settings, "replay_reparse_auto", True)
    if state == "reserved":
        assert run(on, worker)[1] == {"recovered": 1} and worker.accepted == 1


def test_a_reservation_for_a_recipe_nobody_runs_any_more_is_fenced(db, on):
    replay = add_replay(db, 1, recipe="recipe-0", played=T0)
    old = add_attempt(db, replay, target=OLD, phase="reserved", status="queued")
    worker = ProtocolWorker(recipe=NEW, files=index(1))   # site and worker have both moved on to NEW
    holding, counts = run(on, worker)
    rows = {u.id: u for u in attempts(db)}
    assert phase(rows[old.id]) == ("failed", "refused_before_acceptance") and "another recipe" in rows[old.id].error
    # The same pass then takes the replay for the recipe both sides are on now, from zero tries.
    assert counts == {"refused": 1, "reserved": 1, "sent": 1} and len(rows) == 2 and holding == {replay.id}
    late = worker.reparse(match_uuid(1), attempt_id=old.id, expected_sha256=sha(1))
    assert late["code"] == "closed" and worker.accepted == 1, "a late request can't start the closed attempt"


HANDSHAKES = {
    "control off": lambda h: h["control"].update(enabled=False),
    "no control block": lambda h: h.pop("control"),
    "no gaps protocol": lambda h: h["control"].pop("gaps_protocol"),
    "no re-parse protocol": lambda h: h["archive"].pop("reparse_protocol"),
    "a later re-parse protocol": lambda h: h["archive"].update(reparse_protocol=2),
    "archive off": lambda h: h["archive"].update(enabled=False),
    "recipe skew": lambda h: h.update(recipe="recipe-z"),
    "no recipe": lambda h: h.pop("recipe"),
}


@pytest.mark.parametrize("case", [*sorted(HANDSHAKES), "unreachable"])
def test_worker_control_off_holds_and_reserves_nothing(db, on, case):
    _, worker = stale_one(db)
    if case == "unreachable":
        worker.down = True
    else:
        HANDSHAKES[case](worker.health_body)
    assert run(on, worker) == (frozenset(), {})
    assert worker.calls == ["health"] and attempts(db) == []


def test_with_the_switch_off_and_nothing_unfinished_the_worker_is_never_asked(db, on, monkeypatch):
    _, worker = stale_one(db)
    monkeypatch.setattr(settings, "replay_reparse_auto", False)
    assert run(on, worker) == (frozenset(), {}) and worker.calls == [] and attempts(db) == []
    monkeypatch.setattr(settings, "replay_reparse_auto", True)
    monkeypatch.setattr(settings, "demo_mode", True)
    add_attempt(db, db.query(Replay).one(), phase="reserved", status="queued")
    assert run(on, worker) == (frozenset(), {}) and worker.calls == [], "the demo resumes nothing either"
    assert run(on, None) == (frozenset(), {})


@pytest.mark.parametrize("gate", ["an upload queued", "an upload parsing", "worker uploads queued",
                                  "site control in flight", "worker control queued", "worker control running",
                                  "worker says nothing about control", "the last attempt just settled"])
def test_nothing_is_reserved_until_everything_is_quiet(db, on, gate):
    replay, worker = stale_one(db)
    in_flight = 0
    if gate.startswith("an upload"):
        db.add(ReplayUpload(id=str(uuid.uuid4()), status=gate.split()[-1], session_key="sess", created_at=T0))
        db.commit()
    elif gate == "worker uploads queued":
        worker.health_body["queued"] = 1
    elif gate == "site control in flight":
        in_flight = 3
    elif gate == "worker says nothing about control":
        del worker.health_body["control"]["running"]
    elif gate.startswith("worker control"):
        worker.health_body["control"][gate.split()[-1]] = 1
    else:
        other = add_replay(db, 2, recipe=NEW)
        add_attempt(db, other, target="recipe-0", finished=T0 - timedelta(seconds=auto.SETTLE_S - 5))
    holding, counts = run(on, worker, in_flight=in_flight)
    assert holding == {replay.id} and counts == {} and "reparse" not in worker.calls
    assert not auto.unfinished_attempts(db)
    if gate == "the last attempt just settled":
        assert run(on, worker, now=NOW + 10)[1] == {"reserved": 1, "sent": 1}


def test_a_reservation_waits_out_a_busy_worker_without_being_closed(db, on):
    replay = add_replay(db, 1, played=T0)
    reserved = add_attempt(db, replay, phase="reserved", status="queued")
    worker = ProtocolWorker(files=index(1))
    worker.health_body["control"]["running"] = 1
    assert run(on, worker) == ({replay.id}, {}) and worker.calls == ["health", "health", "lookup", "archive"]
    worker.health_body["control"]["running"] = 0
    worker.full = True
    assert run(on, worker)[1] == {} and worker.receipts == {}, "queue full: nothing accepted, nothing refused"
    worker.full = False
    assert run(on, worker)[1] == {"recovered": 1}
    assert [u.id for u in attempts(db)] == [reserved.id]


@pytest.mark.parametrize("moved, said", [("current", "changed since"), ("other file", "changed since"),
                                         ("local", "local ingest"), ("deleted", "deleted"), ("gone", "gone"),
                                         ("used up", "used up")])
def test_a_reservation_whose_selection_no_longer_holds_is_fenced_not_sent(db, on, moved, said):
    replay = add_replay(db, 1, played=T0)
    if moved == "used up":
        for hours in (5, 3):
            add_attempt(db, replay, finished=T0 - timedelta(hours=hours))
    reserved = add_attempt(db, replay, phase="reserved", status="queued")
    if moved == "current":
        replay.recipe = NEW
    elif moved == "other file":
        replay.source_sha256 = sha(7)
    elif moved == "local":
        replay.source = "local"
    elif moved == "deleted":
        db.add(ReplayDeletion(match_uuid=match_uuid(1), reason="asked"))
    elif moved == "gone":
        db.delete(replay)
    db.commit()
    worker = ProtocolWorker(files=index(1))
    _, counts = run(on, worker)
    row = db.get(ReplayUpload, reserved.id)
    db.refresh(row)
    assert phase(row) == ("failed", "refused_before_acceptance") and said in row.error
    assert counts == {"refused": 1} and worker.accepted == 0 and worker.receipts[reserved.id]["state"] == "closed"


def test_switching_off_closes_an_unaccepted_reservation_and_starts_nothing(db, on, monkeypatch):
    replay = add_replay(db, 1, played=T0)
    reserved = add_attempt(db, replay, phase="reserved", status="queued")
    worker = ProtocolWorker(files=index(1))
    monkeypatch.setattr(settings, "replay_reparse_auto", False)
    worker.down = True
    assert run(on, worker) == (frozenset(), {"deferred": 1})   # a timeout is not evidence
    assert phase(attempts(db)[0]) == ("queued", "reserved")
    worker.down = False
    holding, counts = run(on, worker)
    [row] = attempts(db)
    assert phase(row) == ("failed", "refused_before_acceptance") and "REPLAY_REPARSE_AUTO is off" in row.error
    assert counts == {"refused": 1} and holding == frozenset() and worker.accepted == 0
    assert worker.reparse(match_uuid(1), attempt_id=reserved.id, expected_sha256=sha(1))["code"] == "closed"
    assert "archive" not in worker.calls, "switched off: the archive is not even read"


@pytest.mark.parametrize("known", ["bound", "lost reply", "found at the close"])
def test_switching_off_still_finishes_an_attempt_the_worker_accepted(db, on, monkeypatch, known):
    replay, worker = stale_one(db)
    worker.lose_reply = known != "bound"
    run(on, worker)
    [row] = attempts(db)
    assert worker.accepted == 1 and phase(row)[1] == ("accepted" if known == "bound" else "reserved")
    monkeypatch.setattr(settings, "replay_reparse_auto", False)
    worker.close_races = known == "found at the close"
    holding, counts = run(on, worker, now=NOW + 20)
    [row] = attempts(db)
    assert phase(row) == ("parsing", "accepted") and holding == {replay.id}, "never closed, and still held"
    assert counts == ({} if known == "bound" else {"recovered": 1})
    worker.close_races = False
    worker.jobs[row.worker_job_id] = {"status": "failed", "error": "parse timed out"}
    holding, counts = run(on, worker, now=NOW + 40)
    assert holding == frozenset() and counts == {"finished": 1} and worker.accepted == 1
    assert run(on, worker, now=NOW + 60) == (frozenset(), {}) and len(attempts(db)) == 1


def test_one_attempt_at_a_time_newest_match_first(db, on):
    add_replay(db, 1, played=T0 - timedelta(days=3))
    newest = add_replay(db, 2, played=T0 - timedelta(days=1))
    worker = ProtocolWorker(files=index(1, 2))
    memo = auto.Memo()
    holding, counts = run(on, worker, memo)
    [row] = attempts(db)
    assert row.auto_context["selected_replay_id"] == newest.id and len(holding) == 2
    assert run(on, worker, memo, now=NOW + 20)[1] == {} and worker.accepted == 1
    worker.jobs[row.worker_job_id] = {"status": "failed", "error": "parse failed"}
    assert run(on, worker, memo, now=NOW + 40)[1] == {"finished": 1}, "and not the next one in the same pass"
    assert run(on, worker, memo, now=NOW + 40 + auto.SETTLE_S - 1)[1] == {}
    assert run(on, worker, memo, now=NOW + 40 + auto.SETTLE_S)[1] == {"reserved": 1, "sent": 1}
    assert auto.unfinished_attempts(db)[0].auto_context["match_uuid"] == match_uuid(1)


def test_only_the_oldest_of_two_unfinished_reservations_is_sent(db, on):
    """Two can't exist under the dispatcher's lock; if they ever do, they must not wait on each other."""
    first = add_attempt(db, add_replay(db, 1, played=T0), phase="reserved", status="queued",
                        finished=T0 - timedelta(minutes=10))
    second = add_attempt(db, add_replay(db, 2, played=T0), phase="reserved", status="queued")
    worker = ProtocolWorker(files=index(1, 2))
    assert run(on, worker)[1] == {"recovered": 1} and list(worker.receipts) == [first.id]
    assert phase(db.get(ReplayUpload, second.id)) == ("queued", "reserved")


def test_the_archive_index_is_read_at_most_once_in_its_interval(db, on):
    add_replay(db, 1, recipe=NEW)
    worker = ProtocolWorker()
    memo = auto.Memo()
    for later in (0, 20, auto.INDEX_S - 1, auto.INDEX_S, auto.INDEX_S + 20):
        run(on, worker, memo, now=NOW + later)
    assert worker.calls.count("archive") == 2 and worker.calls.count("health") == 5
    worker.archive = worker.health_error   # the archive route stops answering
    run(on, worker, memo, now=NOW + 3 * auto.INDEX_S)
    assert memo.index is None and memo.index_at is None, "an unread index is unknown and is read again next pass"


# -- the restoration barrier: the last replacement's control and gaps are back before the next replay is taken


@pytest.fixture
def replaced(db, on, condensed, monkeypatch):  # noqa: F811
    """A real stored replay that an automatic attempt replaced ten minutes ago, with no control yet, and a
    second stale replay waiting its turn."""
    monkeypatch.setattr(uploads, "_site_recipe", condensed.recipe)
    add_match(db)
    replay = db.get(Replay, store.store_replay(db, condensed, source="upload").replay_id)
    assert rc.map_layer(replay.map_name) is not None
    before = replace_recipe(replay, OLD)
    done = ReplayUpload(id=str(uuid.uuid4()), status="stored", store_outcome="replaced", replay_id=replay.id,
                        source_sha256=replay.source_sha256, session_key=uploads.auto_tag(condensed.recipe),
                        created_at=T0 - timedelta(minutes=15), finished_at=T0 - timedelta(minutes=10),
                        auto_context={**uploads.new_auto_context(before, condensed.recipe), "phase": "accepted"})
    db.add(done)
    db.commit()
    waiting = add_replay(db, 2, played=T0 - timedelta(days=2))
    return replay, waiting, ProtocolWorker(recipe=condensed.recipe, files=index(2))


def replace_recipe(replay, recipe):
    """`replay` as it was before the replacement: only what `new_auto_context` reads."""
    from types import SimpleNamespace

    return SimpleNamespace(match_uuid=replay.match_uuid, id=replay.id, recipe=recipe,
                           source_sha256=replay.source_sha256)


def test_the_next_replay_waits_until_the_last_replacement_has_its_control_and_gaps_back(db, replaced):
    replay, waiting, worker = replaced
    on = sessionmaker(bind=db.get_bind())
    rounds = replay.round_count
    pending = auto.restoration(db, replay.recipe)
    assert pending == {"match_uuid": replay.match_uuid.lower(), "replay_id": replay.id, "control_rounds": rounds,
                       "gap_rounds": 0}
    # The worker's queue is empty the whole time (its retries ran out): that alone must not release it.
    for later in (0, 3600):
        holding, counts = run(on, worker, auto.Memo(), now=NOW + later)   # a new memo each time: a restart
        assert counts == {} and "reparse" not in worker.calls and waiting.id in holding
        assert replay.id not in holding, "the replaced replay's own control is not held back"
    for n in range(1, rounds + 1):
        put_row(db, replay, n)
    assert auto.restoration(db, replay.recipe)["gap_rounds"] == rounds, "control is back, its gaps are not"
    assert run(on, worker)[1] == {}
    for n in range(2, rounds + 1):
        put_gap_run(db, replay, n)
    put_gap_run(db, replay, 1, status="failed")   # the detector's own failure under the current keys: settled
    assert auto.restoration(db, replay.recipe) is None
    assert run(on, worker)[1] == {"reserved": 1, "sent": 1}
    assert auto.unfinished_attempts(db)[0].auto_context["selected_replay_id"] == waiting.id


def test_a_rounds_own_control_failure_counts_as_restored(db, replaced):
    replay, _, worker = replaced
    put_row(db, replay, 1, status="failed")
    for n in range(2, replay.round_count + 1):
        put_row(db, replay, n)
        put_gap_run(db, replay, n)
    assert auto.restoration(db, replay.recipe) is None


@pytest.mark.parametrize("release", ["deleted", "superseded", "stale again", "gone"])
def test_a_replacement_that_is_deleted_or_superseded_no_longer_holds_the_queue(db, replaced, release):
    replay, _, worker = replaced
    recipe = replay.recipe
    if release == "deleted":
        db.add(ReplayDeletion(match_uuid=replay.match_uuid, reason="asked"))
    elif release == "superseded":
        replay.source_sha256 = sha(8)
    elif release == "stale again":
        recipe = "recipe-next"   # the site moved on: the replay is eligible again, and nothing waits for it
    else:
        db.query(ReplayUpload).update({"replay_id": None})
    db.commit()
    assert auto.restoration(db, recipe) is None


# -- PostgreSQL only


def test_pg_two_site_processes_on_different_recipes_commit_one_reservation(pg, monkeypatch):  # noqa: F811
    from sqlalchemy import text

    from app.services import replay_control_remote as remote

    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_reparse_auto", True)
    monkeypatch.setattr(settings, "replay_control_remote", True)
    monkeypatch.setattr(settings, "replay_worker_url", "http://127.0.0.1:9")
    monkeypatch.setattr(uploads, "_site_recipe", NEW)
    monkeypatch.setattr(auto, "_builds", BUILDS)
    session = pg()
    add_replay(session, 1, played=T0 - timedelta(days=1))
    add_replay(session, 2, played=T0 - timedelta(days=2))
    worker = ProtocolWorker(recipe=NEW, files=index(1, 2))
    posting, go = threading.Event(), threading.Event()
    accept = worker.reparse

    def slow(*args, **kwargs):   # the first process stops inside its POST: its reservation is committed
        posting.set()
        assert go.wait(30)
        return accept(*args, **kwargs)

    worker.reparse = slow

    def process(out: list, client):
        """One site process's pass, as the dispatcher runs it: its lock session, then the step."""
        lock = pg()
        try:
            if not lock.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": remote.LOCK_ID}).scalar():
                out.append("locked out")
                return
            out.append(run(pg, client)[1])
        finally:
            lock.rollback()
            lock.close()

    first: list = []
    thread = threading.Thread(target=process, args=(first, worker))
    thread.start()
    assert posting.wait(30)
    assert len(auto.unfinished_attempts(session)) == 1, "committed before the worker was asked"
    second: list = []
    process(second, worker)
    assert second == ["locked out"]
    # Without the dispatcher's lock a second process still finds the reservation under the match's lock.
    other = pg()
    entry = [e for e in auto.classify(other, NEW, index(1, 2), BUILDS, NOW) if e.match_uuid == match_uuid(2)][0]
    assert auto._reserve(other, entry, NEW, index(1, 2), T0) is None
    other.close()
    go.set()
    thread.join(30)
    assert first == [{"reserved": 1, "sent": 1}]
    # The other version's process, once it gets the lock: a site on recipe-c beside the NEW worker defers;
    # beside its own worker it waits for the accepted attempt. Neither reserves.
    monkeypatch.setattr(uploads, "_site_recipe", "recipe-c")
    process(second, worker)
    process(second, ProtocolWorker(recipe="recipe-c", files=index(1, 2)))
    assert second[1:] == [{"deferred": 1}, {}]
    session.rollback()
    rows = session.query(ReplayUpload).all()
    assert len(rows) == 1 and worker.accepted == 1 and rows[0].auto_context["phase"] == "accepted"
    session.close()


# ---------------------------------------------------------------- the control cycle and hold-back (task 6)
# The dispatcher's real `cycle`, a fake control client (test_control_remote's) and the protocol fake above as
# the upload client. Real stored replays here: map control is only planned for rounds that exist.

LOCAL_UUID = match_uuid(0x77)


def replay_ids(control) -> set[int]:
    """The replays the control client was sent tasks for, of either kind."""
    return {int(task["key"].split(":")[0]) for task in control.tasks.values()}


def cycle(factory, control, worker, state=None, now=NOW) -> dict:
    return remote.cycle(factory, control, state if state is not None else remote.State(), now=now, worker=worker)


def finish_everything(db, replay) -> None:
    all_control(db, replay)
    for n in range(1, replay.round_count + 1):
        put_gap_run(db, replay, n)


@pytest.fixture
def uploaded(db, on, condensed, monkeypatch):  # noqa: F811
    """A real uploaded replay that this site's recipe makes stale, a local ingest beside it that is just as
    stale, a worker that has both recordings archived, and that recipe: the stored one with another parser
    commit, since map control reads the condense revision out of a recipe."""
    target = "f" * 12 + condensed.recipe[12:]
    assert target != condensed.recipe and rc.blob_too_old(dc_replace(condensed, recipe=target)) is False
    monkeypatch.setattr(uploads, "_site_recipe", target)
    monkeypatch.setattr(auto, "_builds", frozenset({condensed.game_branch}))
    add_match(db)
    stale = db.get(Replay, store.store_replay(db, condensed, source="upload").replay_id)
    local = db.get(Replay, store.store_replay(db, dc_replace(condensed, match_uuid=LOCAL_UUID),
                                              source="local").replay_id)
    assert (stale.source, local.source) == ("upload", "local") and stale.id != local.id
    worker = ProtocolWorker(recipe=target, files={stale.match_uuid.lower(): stale.source_sha256,
                                                LOCAL_UUID: local.source_sha256})
    return stale, local, worker, target


@pytest.mark.parametrize("kind", ["control", "gaps"])
@pytest.mark.parametrize("waiting", ["eligible", "resolving", "in_flight", "backoff"])
def test_a_replay_waiting_for_its_re_parse_gets_no_control_or_gaps_work(db, on, uploaded, waiting, kind):
    stale, local, worker, target = uploaded
    if kind == "gaps":
        all_control(db, stale)                     # its control is current: only its gaps are owed
    if waiting == "eligible":
        worker.health_body["queued"] = 1           # an upload is waiting on the worker: nothing is reserved
    elif waiting == "resolving":
        worker.full = True                         # reserved, and the worker has not accepted it
    elif waiting == "backoff":
        add_attempt(db, stale, target=target, finished=T0 - timedelta(minutes=10))
    control = FakeWorker(gaps=1)
    counts = cycle(on, control, worker)
    db.expire_all()
    states_now = {e.replay_id: e.state for e in auto.classify(db, target, auto.index_of(worker.archive()),
                                                              auto.supported_builds(), NOW)}
    assert states_now == {stale.id: waiting, local.id: "local"}
    assert counts["held_back"] == 1 and replay_ids(control) == {local.id}, "the local replay's control goes on"
    assert counts["sent"] == remote.IN_FLIGHT


def test_a_released_replay_is_planned_at_once_despite_the_throttle(db, on, uploaded):
    stale, local, worker, target = uploaded
    finish_everything(db, local)
    add_attempt(db, stale, target=target, finished=T0 - timedelta(minutes=10))   # one failure: backing off, held
    control, state = FakeWorker(gaps=1), remote.State()
    assert cycle(on, control, worker, state)["sent"] == 0 and state.last_found is False
    assert cycle(on, control, worker, state, now=NOW + 20)["sent"] == 0
    assert state.last_planned == NOW, "nothing changed: the throttle holds"
    add_attempt(db, stale, target=target, finished=T0)                           # the second: it gave up
    counts = cycle(on, control, worker, state, now=NOW + 40)
    assert counts["held_back"] == 0 and counts["sent"] == remote.IN_FLIGHT and replay_ids(control) == {stale.id}


def test_a_finished_replacement_sends_the_new_replays_rounds_in_the_same_cycle(db, on, uploaded, condensed):  # noqa: F811
    stale, local, worker, target = uploaded
    stale_uuid, old_id = stale.match_uuid, stale.id
    finish_everything(db, local)
    control, state = FakeWorker(gaps=1), remote.State()
    first = cycle(on, control, worker, state)
    assert (first["reparse_reserved"], first["reparse_sent"], first["sent"], first["held_back"]) == (1, 1, 0, 1)
    [row] = auto.unfinished_attempts(db)
    worker.jobs[row.worker_job_id] = {"status": "done", "result": result_of(dc_replace(condensed, recipe=target))}
    second = cycle(on, control, worker, state, now=NOW + 20)   # 20 s after a plan that found nothing to send
    db.expire_all()
    new = db.query(Replay).filter(Replay.match_uuid == stale_uuid).one()
    assert (new.recipe, new.source) == (target, "upload")
    assert (second["reparse_finished"], second["held_back"]) == (1, 0)
    assert second["sent"] == remote.IN_FLIGHT and replay_ids(control) == {new.id}
    assert all("gaps" in task for task in control.tasks.values())
    assert len(worker.acks) == 1 and db.get(ReplayUpload, row.id).store_outcome == "replaced"
    # Until those rounds are back, the queue takes no other replay: the barrier reads the database.
    pending = auto.restoration(db, target)
    assert pending["replay_id"] == new.id and pending["control_rounds"] == new.round_count
    assert old_id == new.id or db.get(Replay, old_id) is None


@pytest.mark.parametrize("off", ["the switch", "recipe skew", "no re-parse protocol", "no gaps protocol",
                                 "worker control off", "worker unreachable", "no upload client"])
def test_with_the_queue_stopped_nothing_is_held_or_reserved_and_control_goes_on(db, on, uploaded, monkeypatch, off):
    stale, local, worker, _ = uploaded
    if off == "the switch":
        monkeypatch.setattr(settings, "replay_reparse_auto", False)
    elif off == "recipe skew":
        worker.health_body["recipe"] = "recipe-z"
    elif off == "no re-parse protocol":
        del worker.health_body["archive"]["reparse_protocol"]
    elif off == "no gaps protocol":
        del worker.health_body["control"]["gaps_protocol"]
    elif off == "worker control off":
        worker.health_body["control"]["enabled"] = False
    elif off == "worker unreachable":
        worker.down = True
    finish_everything(db, local)   # only the stale upload has rounds to send
    control = FakeWorker(gaps=1)
    counts = cycle(on, control, None if off == "no upload client" else worker)
    assert counts["held_back"] == 0 and counts["sent"] == remote.IN_FLIGHT and replay_ids(control) == {stale.id}
    assert not any(counts[name] for name in auto.COUNTS) and db.query(ReplayUpload).count() == 0
    assert "reparse" not in worker.calls and "archive" not in worker.calls


def test_switched_off_with_no_attempt_the_cycle_is_the_one_without_an_upload_client(db, on, uploaded, monkeypatch):
    _, _, worker, _ = uploaded
    monkeypatch.setattr(settings, "replay_reparse_auto", False)
    with_client, without = FakeWorker(gaps=1), FakeWorker(gaps=1)
    one = cycle(on, with_client, worker)
    other = cycle(on, without, None)
    assert worker.calls == [], "no health, no archive index, no attempt lookup"
    assert one == other and with_client.tasks == without.tasks and one["sent"] == remote.IN_FLIGHT


def test_a_failing_automatic_pass_holds_nothing_and_control_goes_on(db, on, uploaded, monkeypatch, caplog):
    stale, _, worker, target = uploaded
    reserved = add_attempt(db, stale, target=target, phase="reserved", status="queued")

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(auto, "step", boom)
    control, state = FakeWorker(gaps=1), remote.State()
    state.held = frozenset({stale.id})
    counts = cycle(on, control, worker, state)
    assert counts["held_back"] == 0 and counts["sent"] == remote.IN_FLIGHT and state.held == frozenset()
    assert "automatic re-parse pass failed" in caplog.text
    db.expire_all()
    assert phase(db.get(ReplayUpload, reserved.id)) == ("queued", "reserved"), "the reservation is untouched"


def test_the_dispatcher_starts_with_an_upload_client_whatever_the_switch_says(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_control_remote", True)
    monkeypatch.setattr(settings, "replay_worker_url", "worker:8000")
    monkeypatch.setattr(settings, "replay_reparse_auto", False)
    started = {}

    class Thread:
        def __init__(self, target, args, kwargs, name, daemon):
            started.update(target=target, kwargs=kwargs)

        def start(self):
            pass

    monkeypatch.setattr(remote.threading, "Thread", Thread)
    assert remote.start(lambda: None) is not None
    assert started["target"] is remote.run_forever and isinstance(started["kwargs"]["worker"], uploads.WorkerClient)
    assert started["kwargs"]["worker"].base == "http://worker:8000"


# -- PostgreSQL only: the lock is kept, one reservation, one collection


def pg_settings(monkeypatch, recipe=NEW, switch=True) -> None:
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_reparse_auto", switch)
    monkeypatch.setattr(settings, "replay_control_remote", True)
    monkeypatch.setattr(settings, "replay_worker_url", "http://127.0.0.1:9")
    monkeypatch.setattr(uploads, "_site_recipe", recipe)
    monkeypatch.setattr(auto, "_builds", BUILDS)


def lock_is_free(pg) -> bool:  # noqa: F811
    from sqlalchemy import text

    probe = pg()
    try:
        return bool(probe.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": remote.LOCK_ID}).scalar())
    finally:
        probe.rollback()
        probe.close()


def test_pg_a_failed_transaction_in_the_automatic_pass_keeps_the_lock_and_control_goes_on(pg, condensed,  # noqa: F811
                                                                                          monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    pg_settings(monkeypatch)
    session = pg()
    add_match(session)
    store.store_replay(session, condensed, source="local")
    session.commit()
    session.close()
    failures = []
    real = auto.unfinished_attempts

    def broken(step_session):
        try:
            step_session.execute(text("SELECT * FROM no_such_table_in_this_database"))
        except DBAPIError as error:
            failures.append(type(error).__name__)
        return real(step_session)   # the step's transaction is aborted: this read fails for real

    monkeypatch.setattr(auto, "unfinished_attempts", broken)
    locked = []

    class Control(FakeWorker):
        def submit(self, task):
            locked.append(not lock_is_free(pg))
            return super().submit(task)

    worker, control = ProtocolWorker(), Control()
    counts = remote.cycle(pg, control, remote.State(), now=0, worker=worker)
    assert failures == ["ProgrammingError"] and worker.calls == [], "the pass died in its own session"
    assert counts["sent"] == remote.IN_FLIGHT and counts["held_back"] == 0, "planning used a usable session"
    assert len(locked) == remote.IN_FLIGHT and all(locked), "the dispatcher's lock outlived the failure"
    assert lock_is_free(pg), "and is released when the cycle ends"


def test_pg_two_dispatchers_at_once_reserve_one_attempt(pg, monkeypatch):  # noqa: F811
    pg_settings(monkeypatch)
    session = pg()
    add_replay(session, 1, played=T0 - timedelta(days=1))
    add_replay(session, 2, played=T0 - timedelta(days=2))
    worker = ProtocolWorker(files=index(1, 2))
    posting, go = threading.Event(), threading.Event()
    accept = worker.reparse

    def slow(*args, **kwargs):
        posting.set()
        assert go.wait(30)
        return accept(*args, **kwargs)

    worker.reparse = slow
    first: list = []
    thread = threading.Thread(target=lambda: first.append(cycle(pg, FakeWorker(), worker)))
    thread.start()
    assert posting.wait(30)
    assert cycle(pg, FakeWorker(), worker).get("locked_out") == 1, "the other instance's cycle does nothing"
    assert not lock_is_free(pg), "held through the automatic pass's worker request"
    go.set()
    thread.join(30)
    assert (first[0]["reparse_reserved"], first[0]["reparse_sent"], first[0]["held_back"]) == (1, 1, 2)
    later = cycle(pg, FakeWorker(), worker, now=NOW + 20)
    assert (later["reparse_reserved"], later["held_back"]) == (0, 2)
    assert session.query(ReplayUpload).count() == 1 and worker.accepted == 1
    session.close()


def test_pg_the_cycle_and_the_archive_sync_collect_one_attempt_once(pg, condensed, monkeypatch):  # noqa: F811
    pg_settings(monkeypatch, recipe=condensed.recipe, switch=False)   # collected even with the switch off
    session = pg()
    reserve(session, make_stale(session, condensed), condensed.recipe, phase="accepted")
    session.close()
    worker = AutoWorker(condensed.recipe, result_of(condensed))
    holding, go = threading.Event(), threading.Event()

    def pause(upload):   # the cycle's pass stops just before its commit, holding the match's lock and the row
        holding.set()
        assert go.wait(30)

    monkeypatch.setattr(uploads, "_before_settle", pause)
    counted: list = []
    first = threading.Thread(target=lambda: counted.append(cycle(pg, FakeWorker(), worker)))
    first.start()
    assert holding.wait(30)
    monkeypatch.setattr(uploads, "_before_settle", None)
    second = threading.Thread(target=lambda: uploads.collect_unfinished(pg, worker))
    second.start()
    second.join(1.0)
    assert second.is_alive(), "the archive sync's collector waits for the match's lock"
    go.set()
    first.join(30)
    second.join(30)
    assert counted[0]["reparse_finished"] == 1 and len(worker.acks) == 1, "one store, one settlement, one ack"
    session = pg()
    row = session.get(ReplayUpload, ATTEMPT)
    stored = session.query(Replay).one()
    assert (row.status, row.store_outcome, row.replay_id) == ("stored", "replaced", stored.id)
    assert stored.recipe == condensed.recipe
    session.close()
