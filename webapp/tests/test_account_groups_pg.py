"""Account group writes against a real PostgreSQL: row locks and unique
violations can't be seen on sqlite. Needs VALO_TEST_DATABASE_URL (see
tests/_postgres.py); skipped without it.
"""

import threading

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import AccountGroup, AccountGroupMember, Player
from app.services.account_groups import link_account, make_main_account, unlink_account

_TABLES = ("account_group_view_cache", "account_group_members", "account_groups", "player_view_cache",
           "friendships", "impact_scores", "kill_events", "round_player_stats", "rounds", "match_players",
           "matches", "players")


def _empty(engine):
    with engine.begin() as conn:
        for table in _TABLES:
            conn.execute(text(f"DELETE FROM {table}"))


@pytest.fixture
def pg():
    from _postgres import postgres_url_or_skip

    engine = create_engine(postgres_url_or_skip(empties_tables=True))
    _empty(engine)
    yield sessionmaker(bind=engine)
    _empty(engine)
    engine.dispose()


def _make_players(factory, *names):
    with factory() as s:
        players = [Player(display_name=n) for n in names]
        s.add_all(players)
        s.commit()
        return [p.id for p in players]


def _state(factory):
    """Read back through a fresh session: (main_player_id, member ids) per group."""
    with factory() as s:
        return [
            (g.main_player_id, sorted(m.player_id for m in s.query(AccountGroupMember).filter_by(group_id=g.id)))
            for g in s.query(AccountGroup)
        ]


def test_pg_leave_waits_for_a_make_main_holding_the_group_lock(pg):
    main, x, y = _make_players(pg, "main#1", "x#2", "y#3")
    with pg() as s:
        assert link_account(s, main, x) is None
        assert link_account(s, main, y) is None
        s.commit()

    s1 = pg()
    assert make_main_account(s1, main, x) is True       # S1 now holds the group's lock, uncommitted

    started = threading.Event()
    outcome = {}

    def leave():
        s2 = pg()
        try:
            s2.execute(text("SET lock_timeout = '2s'"))
            started.set()
            outcome["left"] = unlink_account(s2, x, x)  # x is main once S1 commits: may not leave
            s2.commit()
        except Exception as exc:   # pragma: no cover - reported by the assert below
            outcome["error"] = exc
        finally:
            s2.close()

    thread = threading.Thread(target=leave)
    thread.start()
    assert started.wait(5)
    thread.join(0.8)
    assert thread.is_alive(), "the leave ran without waiting for the group lock"

    s1.commit()
    s1.close()
    thread.join(5)
    assert not thread.is_alive()
    assert "error" not in outcome, outcome.get("error")
    assert outcome["left"] is False

    [(main_id, members)] = _state(pg)
    assert main_id == x
    assert main_id in members and sorted(members) == sorted([main, x, y])


def test_pg_two_opposite_links_one_wins_and_the_other_fails_cleanly(pg):
    a, b = _make_players(pg, "a#1", "b#2")
    first_done = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    outcome = {}

    def first():
        s = pg()
        try:
            outcome["first"] = link_account(s, a, b)
            first_done.set()
            release.wait(5)
            s.commit()
        except Exception as exc:   # pragma: no cover
            outcome["first_error"] = exc
        finally:
            s.close()

    def second():
        s = pg()
        try:
            s.execute(text("SET lock_timeout = '5s'"))
            second_started.set()
            try:
                outcome["second"] = link_account(s, b, a)
                s.commit()
            except Exception as exc:
                s.rollback()
                outcome["second_error"] = exc
        finally:
            s.close()

    t1 = threading.Thread(target=first)
    t1.start()
    assert first_done.wait(5)
    t2 = threading.Thread(target=second)
    t2.start()
    assert second_started.wait(5)
    t2.join(0.8)
    assert t2.is_alive(), "the opposite link didn't queue behind the first one's member row"
    release.set()
    t1.join(5)
    t2.join(10)
    assert not t1.is_alive() and not t2.is_alive()

    assert outcome.get("first") is None and "first_error" not in outcome
    assert isinstance(outcome.get("second_error"), IntegrityError), outcome
    [(main_id, members)] = _state(pg)
    assert main_id == a and members == sorted([a, b])
