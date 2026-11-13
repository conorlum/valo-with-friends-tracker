"""The remote Critical Util build's store and the coach's wait on a real PostgreSQL (W14). SQLite cannot show that
the stored products are JSONB objects (the `CAST(:data AS JSONB) -> 'values'` upsert in util_build_remote._store),
nor that no backend idles in a transaction while the loop polls a slow worker or the coach waits. Needs
VALO_TEST_DATABASE_URL (see tests/_postgres.py) and a migrated scratch database from tests/support/postgres.py;
skipped without it. Synthetic toy data only.
"""

import base64
import json
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.critutil import build_digest, corpus
from app.models.kill_event import KillEvent
from app.models.match import Match, MatchPlayer, MatchSource, Team
from app.models.player import Player
from app.models.replay import UtilModel
from app.models.round import Round
from app.services import replay_control_remote, replay_util, util_model, util_values
from app.services import util_build_remote as remote
from tests.critutil.test_build import FRIEND, fake_gate, toy_corpus
from tests.critutil.test_values import MODEL
from tests.critutil.test_util_build_remote import Client
from tests.support.postgres import migrated_database, scratch_database  # noqa: F401 - fixtures

pytestmark = pytest.mark.postgres

WIN = {**MODEL, "key": "toy-win", "excluded": [7]}
NOW = 1_800_000_000.0


@pytest.fixture
def factory(migrated_database):  # noqa: F811
    engine = create_engine(migrated_database)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()


@pytest.fixture
def watcher(factory):
    """Every other backend on this database that is `idle in transaction`, seen from a connection of its own."""
    conn = factory.kw["bind"].connect()

    def idle_in_transaction():
        rows = conn.execute(text(
            "SELECT pid, state, left(query, 80) FROM pg_stat_activity WHERE datname = current_database() "
            "AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'")).all()
        conn.rollback()
        return rows

    try:
        yield idle_in_transaction
    finally:
        conn.close()


def toy_output(monkeypatch):
    """The bytes a worker serves: encode_output of the toy compute, with the refitted estimator."""
    monkeypatch.setattr(util_values, "gate_all_modes", fake_gate(True))
    stored, ctx = toy_corpus()
    rows = [(m, 1, "f" * 16, None, stored[m][1]) for m in sorted(ctx)]
    values = corpus.corpus_from_inputs((stored, ctx, {FRIEND}), rows, WIN)
    return corpus.encode_output({"win": WIN, "values": values}), values


def open_state(refit=True):
    return remote.UtilState(phase="open", key="dispatch-key", build_id="b1", ref="r1", refit=refit,
                            digest=build_digest.digest(remote.WEBAPP), opened_at=NOW)


class SlowWorker:
    """A worker client whose poll and result download each take a while, as a real one's do."""

    def __init__(self, data, state, delay=0.15, values_key="stored-key"):
        import hashlib
        self.data, self.state, self.delay = data, state, delay
        self.done = {"id": state.build_id, "key": state.key, "status": "done", "version": util_values.VERSION,
                     "digest": state.digest, "refit": state.refit, "values_key": values_key,
                     "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        self.polls = 0

    def util_build(self, build_id):
        self.polls += 1
        time.sleep(self.delay)
        if self.polls == 1:
            return {"id": build_id, "key": self.state.key, "status": "running"}
        return dict(self.done)

    def util_result(self, build_id, dest_path):
        time.sleep(self.delay)
        with open(dest_path, "wb") as out:
            out.write(self.data)


@pytest.fixture
def remote_on(monkeypatch):
    monkeypatch.setattr(remote, "settings", SimpleNamespace(util_build_remote=True, demo_mode=False))
    monkeypatch.setattr(replay_control_remote.settings, "replay_control_remote", True)
    monkeypatch.setattr(replay_control_remote.settings, "replay_worker_url", "https://synthetic.invalid")
    monkeypatch.setattr(replay_control_remote.settings, "demo_mode", False)
    monkeypatch.setattr(remote.util_build_health, "record", lambda *a: None)
    monkeypatch.setattr(remote.util_build_health, "set_in_flight", lambda value: None)
    monkeypatch.setattr(remote.util_build_health, "clear_all", lambda: None)


@pytest.fixture
def observation_factory(factory):
    """Migrated PostgreSQL data, inserted out of id order; match 3 has no rounds or kills."""
    with factory() as db:
        assert db.get_bind().dialect.name == "postgresql"
        for mid in (2, 1, 3):
            db.add(Match(id=mid, external_id=f"synthetic-observations-{mid}", source=MatchSource.SCRAPED))
        for mid in (2, 1):
            for slot in (4, 2, 3, 1):
                pid = mid * 10 + slot
                db.add(Player(id=pid, display_name=f"synthetic-{pid}"))
        db.flush()
        for mid in (2, 1):
            for slot in (4, 2, 3, 1):
                pid = mid * 10 + slot
                db.add(MatchPlayer(id=pid, match_id=mid, player_id=pid, agent="Sage",
                                   team=Team.TEAM_1 if slot <= 2 else Team.TEAM_2))
            for n in (2, 1):
                db.add(Round(id=mid * 100 + n, match_id=mid, round_number=n,
                             outcome="Team B Defuse Win" if n == 2 else "Team A Elimination Win",
                             planted=n == 2, plant_time=20.5 if n == 2 else None,
                             exploded=False, defused=n == 2, defuse_time=36.25 if n == 2 else None))
        db.flush()
        for mid in (2, 1):
            for n in (2, 1):
                db.add(KillEvent(id=mid * 100 + n, round_id=mid * 100 + n, weapon="Vandal",
                                 killer_match_player_id=mid * 10 + 1, death_match_player_id=mid * 10 + 3,
                                 event_time_seconds=25.75 if n == 2 else 8.125))
        db.commit()
    return factory


def test_pg_observation_keys_include_empty_match_and_are_deterministic(observation_factory):
    with observation_factory() as db:
        first = remote._observation_keys(db, set())
    with observation_factory() as db:
        second = remote._observation_keys(db, set())
        observations = util_model.load_observations(db, by_match=True)
        assert db.query(Round).filter(Round.match_id == 3).count() == 0
        assert remote._observation_keys(db, {2}) == {key: mid for key, mid in first.items() if mid != 2}
    assert first == second
    assert len(first) == 3 and set(first.values()) == {1, 2, 3}
    assert set(observations) == {1, 2} and all(observations.values())
    assert any(o[2] and o[4] == 20.5 for obs in observations.values() for o in obs)


@pytest.mark.parametrize("model,identity,field,value", [
    (Round, 101, "outcome", "Team B Elimination Win"),
    (Round, 102, "plant_time", 21.75),
    (MatchPlayer, 11, "team", Team.TEAM_2),
    (KillEvent, 101, "event_time_seconds", 9.625),
])
def test_pg_observation_keys_cover_estimator_edits(observation_factory, model, identity, field, value):
    with observation_factory() as db:
        before = {mid: key for key, mid in remote._observation_keys(db, set()).items()}
        setattr(db.get(model, identity), field, value)
        db.commit()
    with observation_factory() as db:
        after = {mid: key for key, mid in remote._observation_keys(db, set()).items()}
    assert before.keys() == after.keys() == {1, 2, 3}
    assert {mid for mid in before if before[mid] != after[mid]} == {1}


def test_pg_push_loads_shards_in_repeatable_read(observation_factory, remote_on, monkeypatch):
    with observation_factory() as db:
        expected = util_model.load_observations(db, by_match=True, match_ids={1, 2, 3})
    original = util_model.load_observations
    snapshots = []

    def load(snapshot, *args, **kwargs):
        assert snapshot.connection().get_isolation_level() == "REPEATABLE READ"
        actual = original(snapshot, *args, **kwargs)
        assert actual == expected
        snapshots.append(snapshot)
        return actual

    monkeypatch.setattr(util_model, "load_observations", load)
    state, client = remote.UtilState(), Client()
    with observation_factory() as db:
        remote.push(db, client, state, {"util": {"digest": build_digest.digest(remote.WEBAPP)}}, NOW)
        assert not db.in_transaction()
    assert len(snapshots) == 1 and not snapshots[0].in_transaction()
    assert state.phase == "open" and len(client.sent("open")) == 1
    shards = {mid: obs for body in client.sent("obs") for entry in body["obs"]
              for mid, obs in json.loads(base64.b64decode(entry["data"]))}
    assert shards == {mid: json.loads(corpus.encode_output(expected.get(mid, []))) for mid in (1, 2, 3)}


def test_pg_concurrent_edit_between_key_check_and_load_abandons(observation_factory, remote_on, monkeypatch):
    with observation_factory() as db:
        old_keys = remote._observation_keys(db, set())
        expected = util_model.load_observations(db, by_match=True, match_ids={1, 2, 3})
    original = util_model.load_observations
    snapshots = []

    def edit_then_load(snapshot, *args, **kwargs):
        assert snapshot.connection().get_isolation_level() == "REPEATABLE READ"
        reader_pid = snapshot.execute(text("SELECT pg_backend_pid()")).scalar_one()
        with observation_factory() as writer:
            assert writer.execute(text("SELECT pg_backend_pid()")).scalar_one() != reader_pid
            writer.get(KillEvent, 101).event_time_seconds = 9.625
            writer.commit()
        # The real key query has established the snapshot before this load. Its rows
        # must remain the old ones, and the fresh check must then abandon that preparation.
        actual = original(snapshot, *args, **kwargs)
        assert actual == expected
        snapshots.append(snapshot)
        return actual

    monkeypatch.setattr(util_model, "load_observations", edit_then_load)
    failures = []
    monkeypatch.setattr(remote.util_build_health, "record", lambda *args: failures.append(args))
    state, client = remote.UtilState(), Client()
    with observation_factory() as db:
        remote.push(db, client, state, {"util": {"digest": build_digest.digest(remote.WEBAPP)}}, NOW)
        assert not db.in_transaction()
    assert len(snapshots) == 1 and not snapshots[0].in_transaction()
    assert not client.sent("obs") and not client.sent("open")
    assert state.phase == "idle" and list(state.tries.values()) == [0]
    assert not failures
    with observation_factory() as db:
        new_keys = remote._observation_keys(db, set())
    assert {key: mid for key, mid in old_keys.items() if mid != 1} == {
        key: mid for key, mid in new_keys.items() if mid != 1}
    assert old_keys != new_keys


def test_pg_step_stores_both_products_as_jsonb_objects(factory, monkeypatch, remote_on):
    data, values = toy_output(monkeypatch)
    state = open_state()
    worker = SlowWorker(data, state, delay=0)
    remote.step(factory, worker, state, NOW)           # running
    remote.step(factory, worker, state, NOW + 1)       # done: collected and stored
    assert state.phase == "idle"
    with factory() as db:
        rows = db.execute(text("SELECT name, jsonb_typeof(data), key, version, "
                               "computed_at > now() - interval '1 minute' FROM util_model ORDER BY name")).all()
    assert rows == [("values", "object", "stored-key", util_values.VERSION, True),
                    ("win", "object", "toy-win", util_model.VERSION, True)]
    with factory() as db:
        assert db.get(UtilModel, "values").data == json.loads(corpus.encode_output(values))
        assert db.get(UtilModel, "win").data == json.loads(corpus.encode_output(WIN))


def test_pg_a_stored_value_is_replaced_not_nested(factory, monkeypatch, remote_on):
    data, _ = toy_output(monkeypatch)
    for tick in range(2):                                # a second build overwrites the first's rows
        state = open_state()
        worker = SlowWorker(data, state, delay=0, values_key=f"key-{tick}")
        remote.step(factory, worker, state, NOW)
        remote.step(factory, worker, state, NOW + 1)
    with factory() as db:
        assert db.execute(text("SELECT count(*) FROM util_model")).scalar() == 2
        assert db.execute(text("SELECT key, jsonb_typeof(data) FROM util_model WHERE name = 'values'")).one() \
            == ("key-1", "object")


def test_pg_no_backend_idles_in_a_transaction_while_the_loop_polls_and_downloads(factory, watcher, monkeypatch,
                                                                                 remote_on):
    data, _ = toy_output(monkeypatch)
    state = open_state()
    worker = SlowWorker(data, state, delay=0.3)
    monkeypatch.setattr(remote, "STATE", state)
    passes = []
    stop = threading.Event()
    original_step = remote.step

    def counted_step(*args, **kwargs):
        try:
            original_step(*args, **kwargs)
        finally:
            passes.append(1)
            if len(passes) == 2:
                stop.set()

    monkeypatch.setattr(remote, "step", counted_step)
    for name in ("register", "unregister", "beat", "loop_ok", "loop_failed"):
        monkeypatch.setattr(remote.site_health, name, lambda *a, **k: None)
    thread = threading.Thread(target=remote.run_forever, args=(factory, worker, stop), kwargs={"every_s": 0})
    seen = []
    thread.start()
    first_seen = {}
    while thread.is_alive():
        now = time.monotonic()
        idle = {pid: (state_, query) for pid, state_, query in watcher()}
        first_seen = {pid: first_seen.get(pid, now) for pid in idle}
        # The store's own short transaction is idle between its statements for a moment; a poll or a download
        # lasts 0.3 s here, so only a backend idle in a transaction for 0.2 s straight is a held transaction.
        seen.extend((pid, idle[pid]) for pid, since in first_seen.items() if now - since >= 0.2)
        time.sleep(0.02)
    thread.join()
    assert len(passes) == worker.polls == 2 and state.phase == "idle"
    assert seen == [], seen
    with factory() as db:
        assert db.execute(text("SELECT jsonb_typeof(data) FROM util_model WHERE name = 'values'")).scalar() \
            == "object"


def test_pg_the_coachs_wait_holds_no_transaction_and_then_reads_the_stored_build(factory, watcher, monkeypatch,
                                                                                 remote_on):
    from app.coach import snapshot
    from app.services import replays

    data, _ = toy_output(monkeypatch)
    with factory() as db:
        key = replay_util.corpus_key(db, [], WIN)
    sleeps, idle = [], []

    def sleep(seconds):
        assert not caller.in_transaction()
        idle.extend(watcher())                           # asleep: the caller's connection must be released
        sleeps.append(seconds)
        if len(sleeps) == 2:                             # the site's loop stores the build meanwhile
            state = open_state()
            worker = SlowWorker(data, state, delay=0, values_key=key)
            remote.step(factory, worker, state, NOW)
            remote.step(factory, worker, state, NOW + 1)

    monkeypatch.setattr(snapshot, "time", SimpleNamespace(monotonic=lambda: len(sleeps) * 1.0, sleep=sleep),
                        raising=False)
    sentinel = SimpleNamespace(id=1, recipe="synthetic-recipe", source_sha256="f" * 64, match_id=7)

    def find_replay(db, uuid):
        db.execute(text("SELECT 1"))  # exercise the caller's transaction release before every sleep
        return sentinel

    monkeypatch.setattr(replays, "find_replay", find_replay)

    def match_payload(db, replay, *, read_only=False):
        assert replay is sentinel and read_only
        return {"rounds": {"1": {"casts": []}}, "updating": False}

    monkeypatch.setattr(replay_util, "match_payload", match_payload)
    with factory() as caller:
        identity, rounds = snapshot._wait_util(caller, "u", session_factory=factory,
                                               deadline=snapshot.time.monotonic() + 100, poll_s=1, ask=lambda: {})
        assert identity == (1, "synthetic-recipe", "f" * 64) and rounds == {"1": {"casts": []}}
    assert len(sleeps) == 2 and idle == [], idle
    with factory() as db:
        assert db.execute(text("SELECT jsonb_typeof(data) FROM util_model WHERE name = 'values'")
                          ).scalar() == "object"
