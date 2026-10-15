"""Map streaks over the real map pool history (R1): pure builder, DB wrapper, card template."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.match import Match, MatchPlayer, MatchSource, Team
from app.models.player import Player
from app.services import map_pool, map_streaks
from app.services.map_pool import Change, PoolHistory
from app.services.map_streaks import MapStreak, MapStreakWindow, build_map_streaks

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)
NOW = datetime(2026, 12, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clear_cache():
    map_pool.clear_pool_cache()
    yield
    map_pool.clear_pool_cache()


def d(n: int) -> datetime:
    return T0 + timedelta(days=n)


def hist(*changes):
    return PoolHistory([Change(m, v, t, "t", "seed") for m, v, t in changes])


def ms(*pairs):
    """pairs of (map, day)."""
    return [(m, d(day)) for m, day in pairs]


def one(history, matches, name="A"):
    rows = {s.map_name: s for s in build_map_streaks(history, matches, now=NOW)}
    return rows.get(name)


def test_out_of_pool_match_is_skipped_no_count_no_reset():
    h = hist(("A", True, d(0)), ("B", True, d(0)))
    # A leaves the pool at day 10 and comes back at day 30
    h = hist(("A", True, d(0)), ("B", True, d(0)), ("A", False, d(10)), ("A", True, d(30)))
    s = one(h, ms(("B", 1), ("B", 2), ("B", 15), ("B", 16)))
    assert s.current.gap == 2  # the two matches while A was out were skipped


def test_run_carries_on_when_map_returns():
    h = hist(("A", True, d(0)), ("B", True, d(0)), ("A", False, d(10)), ("A", True, d(30)))
    s = one(h, ms(("B", 1), ("B", 2), ("B", 15), ("B", 31), ("B", 32)))
    assert s.current.gap == 4
    assert s.record.gap == 4


def test_play_resets_to_zero():
    h = hist(("A", True, d(0)), ("B", True, d(0)))
    s = one(h, ms(("B", 1), ("B", 2), ("A", 3), ("B", 4)))
    assert s.current.gap == 1
    assert s.record.gap == 2


def test_play_while_listed_out_still_resets():
    h = hist(("A", True, d(0)), ("B", True, d(0)), ("A", False, d(10)), ("A", True, d(30)))
    s = one(h, ms(("B", 1), ("B", 2), ("A", 15), ("B", 31)))
    assert s.current.gap == 1
    assert s.record.gap == 2


def test_current_is_trailing_run():
    h = hist(("A", True, d(0)), ("B", True, d(0)))
    s = one(h, ms(("B", 1), ("B", 2), ("B", 3), ("A", 4), ("B", 5), ("B", 6)))
    assert s.current.gap == 2
    assert s.record.gap == 3
    assert not s.record_is_current


def test_record_is_longest_and_latest_on_tie():
    h = hist(("A", True, d(0)), ("B", True, d(0)))
    s = one(h, ms(("B", 1), ("B", 2), ("A", 3), ("B", 10), ("B", 11), ("A", 12), ("B", 20)))
    assert s.record.gap == 2
    assert s.record.first_at == d(10)
    assert s.record.last_at == d(11)


def test_record_is_current_on_tie():
    h = hist(("A", True, d(0)), ("B", True, d(0)))
    s = one(h, ms(("B", 1), ("B", 2), ("A", 3), ("B", 4), ("B", 5)))
    assert s.current.gap == s.record.gap == 2
    assert s.record_is_current
    # the tie resolves to the latest run, which is the current one
    assert s.record.first_at == d(4)


def test_record_is_current_false_on_zero_zero():
    h = hist(("A", True, d(0)), ("B", True, d(0)))
    s = one(h, ms(("A", 1)))
    assert s.current.gap == 0 and s.record.gap == 0
    assert not s.record_is_current


def test_first_last_at_and_none_at_zero():
    h = hist(("A", True, d(0)), ("B", True, d(0)))
    s = one(h, ms(("B", 1), ("B", 2), ("B", 3), ("A", 4), ("B", 5)))
    assert s.record.first_at == d(1) and s.record.last_at == d(3)
    assert s.current.first_at == d(5) and s.current.last_at == d(5)
    z = one(h, ms(("B", 1), ("A", 2)))
    assert z.current == MapStreakWindow(0, None, None)


def test_first_at_skips_out_of_pool_matches():
    h = hist(("A", True, d(5)), ("B", True, d(0)))
    s = one(h, ms(("B", 1), ("B", 6), ("B", 7)))
    assert s.current.first_at == d(6)  # day 1 was before A joined the pool


def test_map_with_no_match_in_pool_or_on_it_is_omitted():
    h = hist(("A", True, d(20)), ("B", True, d(0)))
    assert one(h, ms(("B", 1), ("B", 2))) is None


def test_map_outside_current_pool_gets_no_row():
    h = hist(("A", True, d(0)), ("B", True, d(0)), ("A", False, d(50)))
    rows = build_map_streaks(h, ms(("B", 1), ("A", 2), ("B", 3)), now=NOW)
    assert [r.map_name for r in rows] == ["B"]


def test_rows_sorted_by_name():
    h = hist(("C", True, d(0)), ("A", True, d(0)), ("B", True, d(0)))
    rows = build_map_streaks(h, ms(("A", 1), ("B", 2), ("C", 3)), now=NOW)
    assert [r.map_name for r in rows] == ["A", "B", "C"]


def test_compute_map_streaks_reads_player_matches(tmp_path, monkeypatch):
    eng = create_engine("sqlite:///" + str(tmp_path / "s.sqlite"))
    Base.metadata.create_all(
        eng, tables=[Player.__table__, Match.__table__, MatchPlayer.__table__]
    )
    h = hist(("A", True, d(0)), ("B", True, d(0)))
    monkeypatch.setattr(map_streaks, "load_pool_history", lambda db: h)
    monkeypatch.setattr(
        map_streaks, "build_map_streaks",
        lambda history, pm, now=None: build_map_streaks(history, pm, now=NOW),
    )
    with sessionmaker(bind=eng)() as db:
        p = Player(display_name="x#1")
        q = Player(display_name="y#2")
        db.add_all([p, q])
        db.flush()
        for i, (m, day, mine) in enumerate(
            [("B", 3, True), ("A", 2, True), ("B", 1, True), ("B", 4, False), ("B", None, True)]
        ):
            match = Match(
                external_id=f"e{i}", source=MatchSource.SCRAPED, map_name=m,
                played_at=d(day) if day is not None else None,
            )
            db.add(match)
            db.flush()
            db.add(MatchPlayer(
                match_id=match.id, player_id=(p if mine else q).id, agent="Jett", team=Team.TEAM_1,
            ))
        db.commit()
        rows = map_streaks.compute_map_streaks(db, p.id)
    by = {r.map_name: r for r in rows}
    assert by["A"].current.gap == 1  # B(1), A(2), B(3): only B(3) after the play
    assert by["B"].current.gap == 0
    assert by["B"].record.gap == 1
    eng.dispose()


def _render(streaks):
    from app.templates import templates
    return templates.env.get_template("players/_map_streaks.html").render(
        map_streaks=streaks, profile=SimpleNamespace(player=SimpleNamespace(display_name="Foo#NA1")),
    )


def _streak(first, last, gap=3, is_current=False):
    rec = MapStreakWindow(gap, first, last)
    return MapStreak("Ascent", MapStreakWindow(0, None, None), rec, is_current)


def test_template_record_date_range():
    html = _render([_streak(datetime(2026, 3, 5), datetime(2026, 5, 9))])
    assert "Mar 2026 &ndash; May 2026" in html


def test_template_single_month_and_no_line_at_zero():
    html = _render([_streak(datetime(2026, 3, 5), datetime(2026, 3, 9))])
    assert "Mar 2026" in html and "&ndash;" not in html
    html0 = _render([_streak(None, None, gap=0)])
    assert "stat-sub" not in html0


def test_template_empty_state():
    assert "No matches yet while these maps were in the pool for Foo" in _render([])
