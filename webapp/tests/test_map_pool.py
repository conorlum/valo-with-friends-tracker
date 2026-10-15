from datetime import datetime, timedelta, timezone

from app.services.map_pool import (
    BLEED_DAYS,
    SAFETY_NET_MIN_MATCHES,
    Change,
    PoolHistory,
)

UTC = timezone.utc


def d(day, hour=0):
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day, hours=hour)


def ch(m, in_pool, day, source="patch_notes"):
    return Change(m, in_pool, d(day), "p", source)


def test_constants():
    assert BLEED_DAYS == 3
    assert SAFETY_NET_MIN_MATCHES == 3


def test_pool_at_before_at_after():
    h = PoolHistory([ch("Bind", True, 10)])
    assert h.pool_at(d(10, -1)) == frozenset()
    assert h.pool_at(d(10)) == frozenset({"Bind"})
    assert h.pool_at(d(11)) == frozenset({"Bind"})
    assert h.in_pool("Bind", d(10))
    assert not h.in_pool("Haven", d(10))


def test_duplicates_fold_and_sorted():
    h = PoolHistory([ch("Bind", True, 5), ch("Abyss", True, 5), ch("Bind", True, 5), ch("Bind", False, 2)])
    assert len(h.changes) == 3
    assert [(c.effective_at, c.map_name) for c in h.changes] == [
        (d(2), "Bind"),
        (d(5), "Abyss"),
        (d(5), "Bind"),
    ]


def test_intervals_out_and_back():
    h = PoolHistory([ch("Bind", True, 0), ch("Bind", False, 10), ch("Bind", True, 20)])
    assert h.intervals("Bind") == [(d(0), d(10)), (d(20), None)]
    assert h.intervals("Haven") == []
    assert h.pool_at(d(15)) == frozenset()


def test_current_pool_and_latest_change():
    h = PoolHistory([ch("Bind", True, 0), ch("Haven", True, 5, "safety_net")])
    assert h.current_pool(d(1)) == frozenset({"Bind"})
    assert h.current_pool() == frozenset({"Bind", "Haven"})
    assert h.latest_change_at(d(100)) == d(0)
    assert h.latest_change_at(d(100), official_only=False) == d(5)
    assert h.latest_change_at(d(-1)) is None
    assert PoolHistory([]).latest_change_at() is None


def test_naive_datetimes_are_utc():
    h = PoolHistory([Change("Bind", True, datetime(2026, 1, 11), "p", "seed")])
    assert h.changes[0].effective_at.tzinfo is not None
    assert h.pool_at(datetime(2026, 1, 11)) == frozenset({"Bind"})
    assert h.pool_at(d(10)) == frozenset({"Bind"})
    h2 = h.with_safety_net([("Haven", datetime(2026, 1, 1, i)) for i in range(3)])
    added = [c for c in h2.changes if c.source == "safety_net"]
    assert added[0].effective_at == d(0)


def base():
    return PoolHistory([ch("Bind", True, 0), ch("Bind", False, 10)])


def test_safety_net_three_strays_add_map():
    h = base().with_safety_net([("Bind", d(20)), ("Bind", d(21)), ("Bind", d(22))])
    added = [c for c in h.changes if c.source == "safety_net"]
    assert added == [Change("Bind", True, d(20), "safety-net", "safety_net")]
    assert len(h.changes) == 3
    assert h.in_pool("Bind", d(21))
    assert not h.in_pool("Bind", d(15))


def test_safety_net_returns_new_and_keeps_original():
    b = base()
    b.with_safety_net([("Bind", d(20)), ("Bind", d(21)), ("Bind", d(22))])
    assert len(b.changes) == 2


def test_safety_net_two_strays_do_not():
    h = base().with_safety_net([("Bind", d(20)), ("Bind", d(21))])
    assert len(h.changes) == 2


def test_safety_net_two_plus_two_separate_intervals():
    h = PoolHistory(
        [ch("Bind", True, 0), ch("Bind", False, 10), ch("Bind", True, 30), ch("Bind", False, 40)]
    )
    h = h.with_safety_net([("Bind", d(20)), ("Bind", d(21)), ("Bind", d(50)), ("Bind", d(51))])
    assert len(h.changes) == 4


def test_safety_net_bleed():
    strays = [("Bind", d(11)), ("Bind", d(12)), ("Bind", d(12, 12))]
    assert len(base().with_safety_net(strays).changes) == 2
    strays = [("Bind", d(14)), ("Bind", d(15)), ("Bind", d(16))]
    added = [c for c in base().with_safety_net(strays).changes if c.source == "safety_net"]
    assert [c.effective_at for c in added] == [d(14)]


def test_safety_net_map_never_in_pool():
    h = PoolHistory([ch("Bind", True, 0)])
    h = h.with_safety_net([("Haven", d(1)), ("Haven", d(2)), ("Haven", d(3)), ("Bind", d(4))])
    added = [c for c in h.changes if c.source == "safety_net"]
    assert [(c.map_name, c.effective_at) for c in added] == [("Haven", d(1))]


def test_safety_net_ignores_matches_in_pool():
    h = PoolHistory([ch("Bind", True, 0)]).with_safety_net([("Bind", d(i)) for i in range(5)])
    assert len(h.changes) == 1
