"""load_pool_history: seed + fetched rows + safety net, SAVEPOINT fallback, process memo."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, str(Path(__file__).parent))

from app.db import Base
from app.models.map_pool import MapPoolChange, MapPoolPatchNotes
from app.models.match import Match, MatchSource
from app.services import map_pool

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _clear_cache():
    map_pool.clear_pool_cache()
    yield
    map_pool.clear_pool_cache()


@pytest.fixture
def engine(tmp_path):
    eng = create_engine("sqlite:///" + str(tmp_path / "pool.sqlite"))
    Base.metadata.create_all(
        eng, tables=[MapPoolChange.__table__, MapPoolPatchNotes.__table__, Match.__table__]
    )
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with sessionmaker(bind=engine)() as s:
        yield s


def _add_change(db, name, in_pool, when, source="patch_notes", patch="13.12"):
    db.add(MapPoolChange(map_name=name, in_pool=in_pool, effective_at=when, patch=patch, source=source))
    db.commit()


def _out_map_and_time(seed):
    """A current-pool map (fixed, not set order) and a time after every seed change."""
    last = max(c.effective_at for c in seed.changes)
    return sorted(seed.current_pool())[0], last + timedelta(days=1)


def _count_queries(engine):
    n = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _c(*a, **k):
        n["n"] += 1

    return n


def test_seed_history_is_nonempty_and_cached():
    h = map_pool.seed_history()
    assert h.current_pool()
    assert map_pool.seed_history() is h
    assert all(c.source == "seed" for c in h.changes)


def test_fetched_row_folds_into_current_pool(db):
    seed = map_pool.seed_history()
    out_map, when = _out_map_and_time(seed)
    _add_change(db, out_map, False, when)
    h = map_pool.load_pool_history(db)
    assert out_map not in h.current_pool()
    assert out_map in seed.current_pool()


def test_naive_database_times_are_utc(db):
    seed = map_pool.seed_history()
    out_map, when = _out_map_and_time(seed)
    assert out_map in seed.pool_at(when + timedelta(days=1))
    _add_change(db, out_map, False, when.replace(tzinfo=None))
    h = map_pool.load_pool_history(db)
    assert out_map not in h.pool_at(when + timedelta(days=1))


def test_seed_rows_in_the_database_are_ignored(db):
    seed = map_pool.seed_history()
    out_map, when = _out_map_and_time(seed)
    _add_change(db, out_map, False, when.replace(tzinfo=None), source="seed")
    h = map_pool.load_pool_history(db)
    assert h.current_pool() == seed.current_pool()


def test_safety_net_uses_all_matches(db):
    seed = map_pool.seed_history()
    out_map, when = _out_map_and_time(seed)
    _add_change(db, out_map, False, when)
    for i in range(3):
        db.add(Match(external_id=f"m{i}", source=MatchSource.SCRAPED, map_name=out_map,
                     played_at=when + timedelta(days=30 + i)))
    db.commit()
    h = map_pool.load_pool_history(db)
    assert out_map not in h.pool_at(when + timedelta(days=29))
    assert out_map in h.pool_at(when + timedelta(days=40))


def test_missing_tables_fall_back_to_seed_and_session_stays_usable(tmp_path):
    eng = create_engine("sqlite:///" + str(tmp_path / "empty.sqlite"))
    with sessionmaker(bind=eng)() as db:
        h = map_pool.load_pool_history(db)
        assert h.current_pool() == map_pool.seed_history().current_pool()
        assert db.execute(text("select 1")).scalar() == 1
    eng.dispose()


def test_memo_hit_issues_no_query(engine, db):
    first = map_pool.load_pool_history(db)
    n = _count_queries(engine)
    db.rollback()
    second = map_pool.load_pool_history(db)
    assert second is first
    assert n["n"] == 0


def test_ttl_expiry_recomputes(engine, db, monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(map_pool.time, "monotonic", lambda: now["t"])
    map_pool.load_pool_history(db)
    n = _count_queries(engine)
    now["t"] += map_pool._POOL_TTL_SECONDS - 1
    map_pool.load_pool_history(db)
    assert n["n"] == 0
    now["t"] += 2
    map_pool.load_pool_history(db)
    assert n["n"] > 0


def test_clear_pool_cache_recomputes(engine, db):
    map_pool.load_pool_history(db)
    n = _count_queries(engine)
    map_pool.clear_pool_cache()
    map_pool.load_pool_history(db)
    assert n["n"] > 0


def test_pg_missing_tables_fall_back_and_same_session_runs_a_query():
    """Tables dropped inside one outer transaction that is always rolled back; the same session then succeeds."""
    from _postgres import postgres_url_or_skip

    eng = create_engine(postgres_url_or_skip(writes=True))
    with eng.connect() as conn:
        outer = conn.begin()
        try:
            conn.execute(text("DROP TABLE map_pool_changes"))
            session = Session(bind=conn)
            h = map_pool.load_pool_history(session)
            assert h.current_pool() == map_pool.seed_history().current_pool()
            assert session.execute(text("select count(*) from matches")).scalar() is not None
        finally:
            outer.rollback()
    eng.dispose()
