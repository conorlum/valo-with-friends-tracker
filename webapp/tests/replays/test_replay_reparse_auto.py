"""The automatic re-parse queue's site half (app/services/replay_reparse_auto.py;
docs/superpowers/plans/2026-10-07-auto-reparse-queue-impl.md, tasks 4-7): the switch, and which stored replays
are eligible. Synthetic rows on a throwaway sqlite store; no worker."""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_control_store import CONTROL_TABLES  # noqa: E402

from app.config import Settings, settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models.match import Match, MatchSource  # noqa: E402
from app.models.replay import Replay, ReplayDeletion, ReplayUpload  # noqa: E402
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
