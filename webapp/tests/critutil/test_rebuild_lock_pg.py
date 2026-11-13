"""Critical Util's rebuild lock (app/services/util_model.py `rebuild_lock`) on a real PostgreSQL: a second rebuild
waits for the first's transaction to end. Advisory locks only, no tables. Needs VALO_TEST_DATABASE_URL (see
tests/_postgres.py); skipped without it.
"""

import threading
import time

from app.services import util_model
from tests._postgres import postgres_session_or_skip


def test_pg_a_second_rebuild_waits_for_the_first_to_commit():
    first = postgres_session_or_skip()
    second = postgres_session_or_skip()
    order = []
    try:
        with util_model.rebuild_lock(first, "values"):
            order.append("first in")

            def other():
                with util_model.rebuild_lock(second, "values"):
                    order.append("second in")
                second.commit()

            thread = threading.Thread(target=other)
            thread.start()
            time.sleep(0.5)
            assert order == ["first in"], "the second waits while the first holds the lock"
            first.commit()          # the transaction ends, and the lock with it
        thread.join(timeout=5)
        assert order == ["first in", "second in"]
    finally:
        first.close()
        second.close()


def test_pg_the_estimator_and_the_values_have_their_own_locks():
    a, b = postgres_session_or_skip(), postgres_session_or_skip()
    try:
        with util_model.rebuild_lock(a, "win"), util_model.rebuild_lock(b, "values"):
            pass                    # would block forever on one shared key
    finally:
        a.rollback()
        b.rollback()
        a.close()
        b.close()


def test_pg_no_connection_idles_in_a_transaction_or_holds_a_lock_through_a_compute(monkeypatch):
    # R3: during the minutes of compute this backend is neither `idle in transaction` nor holding an advisory lock
    from sqlalchemy import text

    from app.services import replay_util

    db = postgres_session_or_skip()
    watcher = postgres_session_or_skip()
    seen = {}

    def slow(inputs, rows, model, **kw):
        pid = pid_of[0]
        seen["state"] = watcher.execute(text("SELECT state FROM pg_stat_activity WHERE pid = :p"), {"p": pid}).scalar()
        seen["locks"] = watcher.execute(text("SELECT count(*) FROM pg_locks WHERE pid = :p AND locktype = 'advisory'"),
                                        {"p": pid}).scalar()
        watcher.rollback()
        return {"rows": []}

    try:
        pid_of = [db.execute(text("SELECT pg_backend_pid()")).scalar()]
        db.rollback()
        monkeypatch.setattr(replay_util, "current_rows", lambda *a, **k: [])
        monkeypatch.setattr(replay_util, "corpus_inputs", lambda *a: ({}, {}, set()))
        monkeypatch.setattr(replay_util, "corpus_from_inputs", slow)
        db.execute(text("SELECT 1"))                    # a transaction is open on entry
        replay_util.build_corpus(db)
        assert seen["state"] != "idle in transaction" and seen["locks"] == 0, seen
    finally:
        db.rollback()
        db.close()
        watcher.close()
