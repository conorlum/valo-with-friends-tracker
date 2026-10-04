"""Route history in the engine's unknown (timing-gaps spec, section 4): every unknown node has an entry
whose route leads back through earlier entries to a source, labelled with the chokes it crosses, and none
of it changes which nodes are unknown."""

import numpy as np
import pytest

from app.control import engine as ce
from app.control.routes import RouteLog
from tests.replays.control_toys import door_hall, open_hall, reference_rounds
from tests.replays.test_control_unknown import _Tk, _at


def test_routelog_traces_and_interns_choke_sequences():
    log = RouteLog()
    a = log.add(5, 0.0, -1, -1)
    b = log.add(6, 0.3, a, 7)
    c = log.add(7, 0.6, b, 7)        # still on choke 7: not repeated
    d = log.add(8, 0.9, c, 9)
    assert log.trace(d) == [a, b, c, d]
    assert log.seqs[log.seq[d]] == (7, 9)
    assert log.seqs[log.seq[a]] == () and log.seq[a] == 0
    assert len(log) == 4 and log.node[d] == 8 and log.t[d] == 0.9


def test_routelog_grows_past_its_capacity():
    log = RouteLog(capacity=2)
    prev = -1
    for i in range(9):
        prev = log.add(i, float(i), prev, -1)
    assert len(log) == 9 and log.trace(prev) == list(range(9))


class _Round:
    """Just what Unknown reads of the round: the lives (slots 0 (A) and 5 (B), alive throughout) and no watchers."""
    watchers = []
    team = {0: "A", 5: "B"}

    @staticmethod
    def alive(slot, t):
        return True


class _Alive(_Tk):
    rnd = _Round()


def _walk_east(geo, unk, times):
    for t in times:
        unk.apply(_Tk(t, _at(0, "A", geo, 120, 200), _at(5, "B", geo, 400, 200)))


def test_every_unknown_node_has_a_route_to_a_source():
    geo = door_hall()
    unk = ce.Unknown(geo)
    _walk_east(geo, unk, [0.0, 1.0, 2.0, 3.0])
    ent = unk.entry["A"][5]
    log = unk.log["A"]
    unknown_nodes = np.flatnonzero(np.isfinite(unk.reached["A"][5]))
    assert set(np.flatnonzero(ent >= 0).tolist()) == set(unknown_nodes.tolist())
    for node in unknown_nodes.tolist():
        route = log.trace(int(ent[node]))
        assert log.node[route[-1]] == node and log.parent[route[0]] == -1
        times = [log.t[e] for e in route]
        assert times == sorted(times)
        assert log.t[route[-1]] == unk.reached["A"][5][node]


def test_route_history_leaves_the_unknown_unchanged():
    geo = door_hall()
    chokes = np.full(geo.n, -1, np.int32)
    chokes[geo.cell_of_px(208, 292)] = 1
    plain, labelled = ce.Unknown(geo), ce.Unknown(geo, chokes=chokes)
    _walk_east(geo, plain, [0.0, 1.0, 2.0, 5.0, 9.0])
    _walk_east(geo, labelled, [0.0, 1.0, 2.0, 5.0, 9.0])
    assert np.array_equal(plain.cells["A"], labelled.cells["A"])
    assert np.array_equal(plain.reached["A"][5], labelled.reached["A"][5])


def test_routes_through_the_door_carry_its_choke():
    geo = door_hall()
    door = geo.cell_of_px(208, 292)
    chokes = np.full(geo.n, -1, np.int32)
    chokes[door] = 1
    unk = ce.Unknown(geo, chokes=chokes)
    _walk_east(geo, unk, [0.0, 4.0, 8.0, 12.0, 16.0])
    west = geo.cell_of_px(150, 200)      # behind the wall: only reachable through the door
    ent = unk.entry["A"][5]
    assert ent[west] >= 0
    assert unk.log["A"].seqs[unk.log["A"].seq[ent[west]]] == (1,)


def test_a_cleared_node_loses_its_entry_and_routes_survive():
    geo = open_hall()
    unk = ce.Unknown(geo)
    _walk_east(geo, unk, [0.0, 2.0])
    log = unk.log["A"]
    ent = unk.entry["A"][5].copy()
    target = int(np.flatnonzero(ent >= 0)[-1])
    route_before = log.trace(int(ent[target]))
    watched = np.zeros(geo.n, bool)
    watched[target] = True
    unk.apply(_Tk(2.5, _at(0, "A", geo, 120, 200, cells=watched), _at(5, "B", geo, 400, 200)))
    assert unk.entry["A"][5][target] == -1
    assert log.trace(route_before[-1]) == route_before, "an earlier route is never rewritten"


def test_a_watched_sighting_roots_its_neighbours_routes():
    """R15: the sighting cell gets an entry even while the team still watches it, and the unobserved
    nodes reached from it trace back to that entry; clearing and re-entry keep the old trace intact."""
    geo = open_hall()
    unk = ce.Unknown(geo)
    enemy = geo.cell_of_px(400, 200)
    spot = np.zeros(geo.n, bool)
    spot[enemy] = True
    # the team spots the enemy (only their cell is watched), then keeps watching that cell while the
    # enemy, still alive, has no position sample (so no push: the sighting is their only source)
    unk.apply(_Tk(0.0, _at(0, "A", geo, 120, 200, cells=spot), _at(5, "B", geo, 400, 200)))
    unk.apply(_Alive(1.0, _at(0, "A", geo, 120, 200, cells=spot)))
    log = unk.log["A"]
    ent = unk.entry["A"][5]
    assert ent[enemy] == -1                       # still watched: not unknown
    root = [e for e in range(len(log)) if log.node[e] == enemy and log.parent[e] == -1 and log.t[e] == 0.0]
    assert len(root) == 1
    reached = np.flatnonzero(ent >= 0)
    assert len(reached) > 0
    for node in reached.tolist():
        assert log.trace(int(ent[node]))[0] == root[0]
    # clear one of them, then let it come back: its old route is untouched and its new one also roots there
    target = int(reached[0])
    old_route = log.trace(int(ent[target]))
    both = spot.copy()
    both[target] = True
    unk.apply(_Alive(1.5, _at(0, "A", geo, 120, 200, cells=both)))
    assert unk.entry["A"][5][target] == -1
    unk.apply(_Alive(2.5, _at(0, "A", geo, 120, 200, cells=spot)))    # freed: entered from now on
    unk.apply(_Alive(4.0, _at(0, "A", geo, 120, 200, cells=spot)))
    new = int(unk.entry["A"][5][target])
    assert new >= 0 and new != old_route[-1]
    assert log.trace(old_route[-1]) == old_route
    assert log.trace(new)[0] == root[0]


@pytest.mark.parametrize("name", ["barrier", "midwall"])
def test_every_tick_of_a_reference_round_keeps_routes_consistent(name):
    """Through a whole toy round (barrier ground, sightings, smokes, deaths): at every tick each enemy's
    unknown nodes are exactly those with an entry, each entry is that node at its arrival time, and its
    parent is an earlier entry at a time no later."""
    geo, blob, link = reference_rounds()[name]
    rnd = ce.RoundInputs(blob, geo, link)
    runner = ce.TickRunner(geo)
    unk = runner.unknown
    checked = 0
    for t in rnd.tick_times():
        runner.step(ce.Tick(rnd, float(t)))
        for side in ("A", "B"):
            log = unk.log[side]
            for slot, reached in unk.reached[side].items():
                ent = unk.entry[side][slot]
                fin = np.isfinite(reached)
                assert np.array_equal(ent >= 0, fin)
                e = ent[fin]
                assert np.array_equal(log.node[e], np.flatnonzero(fin))
                assert np.array_equal(log.t[e], reached[fin])
                p = log.parent[e]
                assert (p < e).all()
                # an area collapse puts every member at the event's time with the centre as parent (spec section 4,
                # "Locating events"), so a parent's time may equal its child's
                assert (log.t[p[p >= 0]] <= log.t[e[p >= 0]]).all()
                checked += int(fin.sum())
    assert checked > 0


def test_an_observed_source_still_gets_an_entry():
    """R15: the enemy's own-position push gets an entry even when the team observes that node."""
    geo = open_hall()
    unk = ce.Unknown(geo)
    enemy = geo.cell_of_px(400, 200)
    seen = np.zeros(geo.n, bool)
    seen[enemy] = True
    h = _at(0, "A", geo, 120, 200)
    h.passive = seen.copy()                       # passive: observed, but not a sighting
    unk.apply(_Tk(0.0, h, _at(5, "B", geo, 400, 200)))
    log = unk.log["A"]
    assert any(log.node[e] == enemy and log.parent[e] == -1 for e in range(len(log)))
    assert unk.entry["A"][5][enemy] == -1
