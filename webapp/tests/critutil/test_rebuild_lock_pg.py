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
