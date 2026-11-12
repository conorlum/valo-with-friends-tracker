"""The remote Critical Util build's store and the coach's wait on a real PostgreSQL (W14). SQLite cannot show that
the stored products are JSONB objects (the `CAST(:data AS JSONB) -> 'values'` upsert in util_build_remote._store),
nor that no backend idles in a transaction while the loop polls a slow worker or the coach waits. Needs
VALO_TEST_DATABASE_URL (see tests/_postgres.py) and a migrated scratch database from tests/support/postgres.py;
skipped without it. Synthetic toy data only.
"""

import json
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.critutil import build_digest, corpus
from app.models.replay import UtilModel
from app.services import replay_util, util_model, util_values
from app.services import util_build_remote as remote
from tests.critutil.test_build import FRIEND, fake_gate, toy_corpus
from tests.critutil.test_values import MODEL
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
    monkeypatch.setattr(remote.util_build_health, "record", lambda *a: None)
    monkeypatch.setattr(remote.util_build_health, "set_in_flight", lambda value: None)
    monkeypatch.setattr(remote.util_build_health, "clear_all", lambda: None)


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
    from app.services import replay_control_remote as control

    data, _ = toy_output(monkeypatch)
    state = open_state()
    worker = SlowWorker(data, state, delay=0.3)
    monkeypatch.setattr(remote, "STATE", state)
    passes = []
    stop = SimpleNamespace(wait=lambda *a: len(passes) >= 2, is_set=lambda: True)
    monkeypatch.setattr(control, "cycle", lambda *a, **k: passes.append(1) or {})
    for name in ("register", "unregister", "beat", "loop_ok", "loop_failed"):
        monkeypatch.setattr(control.site_health, name, lambda *a, **k: None)
    thread = threading.Thread(target=control.run_forever, args=(factory, worker, stop), kwargs={"every_s": 0})
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
    assert worker.polls == 2 and state.phase == "idle"
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
        idle.extend(watcher())                           # asleep: the caller's connection must be released
        sleeps.append(seconds)
        if len(sleeps) == 2:                             # the site's loop stores the build meanwhile
            state = open_state()
            worker = SlowWorker(data, state, delay=0, values_key=key)
            remote.step(factory, worker, state, NOW)
            remote.step(factory, worker, state, NOW + 1)

    monkeypatch.setattr(snapshot, "time", SimpleNamespace(monotonic=lambda: len(sleeps) * 1.0, sleep=sleep),
                        raising=False)
    sentinel = SimpleNamespace(match_id=7)
    monkeypatch.setattr(replays, "find_replay", lambda db, uuid: sentinel)
    monkeypatch.setattr(replay_util, "match_payload",
                        lambda db, replay, read_only=False: {"rounds": {"1": {"casts": []}}, "updating": False})
    with factory() as caller:
        replay, rounds = snapshot._wait_util(caller, "u", 7, session_factory=factory, util_wait_s=100,
                                             poll_s=1, ask=lambda: {})
        assert replay is sentinel and rounds == {"1": {"casts": []}}
    assert len(sleeps) == 2 and idle == [], idle
    with factory() as db:
        assert db.execute(text("SELECT jsonb_typeof(data) FROM util_model WHERE name = 'values'")
                          ).scalar() == "object"
