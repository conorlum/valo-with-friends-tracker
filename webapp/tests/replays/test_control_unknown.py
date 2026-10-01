"""Unknown control (docs/map-control-unknown-plan.md) on toy maps: where an enemy of the team could be,
pushed out by the enemy's players and the barrier drop, spreading at UNKNOWN_MPS, cleared by the
team's live control. Positions are minimap pixels; cells are 8 px (1.12 m)."""

import copy

import numpy as np
import pytest

from app.control import engine as ce
from app.control.geometry import GRID
from tests.replays.control_toys import open_hall, two_rooms, uv


class _Tk:
    """Just what Unknown and Memory read from a tick: its time and holders."""

    def __init__(self, t, *holders):
        self.t, self.holders = t, {h.slot: h for h in holders}


def _at(slot, team, geo, x, y, cells=()):
    """A holder standing at (x, y) px whose live view (active) is exactly `cells` (flat indices or mask)."""
    z = np.zeros(GRID * GRID, bool)
    view = z.copy()
    cells = np.asarray(cells)
    view[cells if cells.dtype == bool else cells.astype(int)] = True
    return ce.Holder(slot, team, geo.cell_of_px(x, y), float(x), float(y), view.copy(), z.copy(), z.copy(),
                     view.copy(), view.copy(), False, "hold")


def _halves(geo):
    walk = np.flatnonzero(geo.walk.ravel())
    x = walk % GRID
    mid = geo.cell_of_px(256, 200) % GRID
    return walk[x < mid], walk[x >= mid]


def _barrier_hall():
    """The open hall with a barrier line down x 256 (column 32): A starts west of it, B east."""
    geo = copy.copy(open_hall())    # open_hall() is shared: don't leave a barrier on it
    geo.barrier = np.zeros((GRID, GRID), bool)
    geo.barrier[:, geo.cell_of_px(256, 200) % GRID] = True
    return geo


def _col(geo, x_px, y_px=200):
    return geo.cell_of_px(x_px, y_px)


def test_unknown_spreads_from_an_enemy_at_a_shift_walk():
    geo = open_hall()
    unk = ce.Unknown(geo)
    a, b = (lambda: _at(0, "A", geo, 120, 200)), (lambda: _at(5, "B", geo, 400, 200))
    unk.apply(_Tk(0.0, a(), b()))
    e = _col(geo, 400)
    assert np.flatnonzero(unk.cells["A"]).tolist() == [e], "at once: only the enemy's own cell"
    unk.apply(_Tk(1.0, a(), b()))
    steps = int(ce.UNKNOWN_MPS * 1.0 // geo.cell_m)
    assert steps == 3
    assert unk.cells["A"][e - steps] and not unk.cells["A"][e - steps - 1], "8-connected steps at UNKNOWN_MPS"
    assert unk.cells["B"][_col(geo, 120) + steps], "each team's unknown comes from the other team's players"


def test_live_vision_pushes_unknown_back_and_it_refills_when_they_look_away():
    geo = open_hall()
    west, east = _halves(geo)
    unk = ce.Unknown(geo)
    b = lambda: _at(5, "B", geo, 400, 200)  # noqa: E731
    for t in (0.0, 30.0):                    # 30 s: the whole hall but A's own cell
        unk.apply(_Tk(t, _at(0, "A", geo, 120, 200), b()))
    assert unk.cells["A"][_col(geo, 200)] and not unk.cells["A"][_col(geo, 120)], "a player's own cell is clear"
    unk.apply(_Tk(30.1, _at(0, "A", geo, 120, 200, east), b()))    # A sees the east half (and B in it)
    assert not unk.cells["A"][east].any(), "cleared as far as they see"
    assert unk.cells["A"][_col(geo, 200)], "the west half they don't see stays unknown"
    unk.apply(_Tk(31.1, _at(0, "A", geo, 120, 200), b()))           # they look away
    steps = 3     # 3.5 m/s for 1 s is 3 whole 1.12 m cells; the carry left from earlier ticks is under a cell
    for k in range(steps):
        assert unk.cells["A"][_col(geo, 256 + 8 * k + 4)], f"refilled {k + 1} cell(s) east of the old line"
    assert not unk.cells["A"][_col(geo, 300)], "not yet further"
    assert unk.cells["A"][_col(geo, 400)], "and from B again"


def test_a_dead_enemy_stops_pushing_unknown_but_what_is_out_stays():
    geo = open_hall()
    unk = ce.Unknown(geo)
    a = lambda: _at(0, "A", geo, 120, 200)  # noqa: E731
    unk.apply(_Tk(0.0, a(), _at(5, "B", geo, 400, 200)))
    unk.apply(_Tk(1.0, a(), _at(5, "B", geo, 400, 200)))
    before = unk.cells["A"].copy()
    unk.apply(_Tk(2.0, a()))                 # B is dead: no holder
    after = unk.cells["A"]
    assert after[before].all(), "what was out stays"
    assert after.sum() > before.sum(), "and keeps spreading from itself"


def test_without_barrier_paint_unknown_starts_at_the_enemies_alone():
    geo = open_hall()
    tk = _Tk(0.0, _at(0, "A", geo, 120, 200), _at(5, "B", geo, 400, 200))
    areas = ce.barrier_start(geo, tk)
    assert areas == {}
    unk = ce.Unknown(geo)
    unk.begin(areas)
    unk.apply(tk)
    assert np.flatnonzero(unk.cells["A"]).tolist() == [_col(geo, 400)]
    assert np.flatnonzero(unk.cells["B"]).tolist() == [_col(geo, 120)]


def test_at_the_barrier_drop_each_teams_unknown_is_the_enemys_side():
    geo = _barrier_hall()
    west, east = _halves(geo)
    line = geo.barrier.ravel()
    tk = _Tk(0.0, _at(0, "A", geo, 150, 200), _at(5, "B", geo, 400, 200))
    areas = ce.barrier_start(geo, tk)
    assert set(areas) == {"A", "B"} and areas["A"][1] == {0: _col(geo, 150)}
    unk = ce.Unknown(geo)
    unk.begin(areas)
    unk.apply(tk)
    assert unk.cells["A"][east[~line[east]]].all() and not unk.cells["A"][west].any()
    assert unk.cells["B"][west[west != _col(geo, 150)]].all() and not unk.cells["B"][east].any()


def test_an_enemy_the_team_sees_pushes_no_unknown_until_out_of_sight():
    geo = open_hall()
    west, east = _halves(geo)
    unk = ce.Unknown(geo)
    unk.apply(_Tk(0.0, _at(0, "A", geo, 120, 200, east), _at(5, "B", geo, 400, 200)))
    assert not unk.cells["A"].any(), "B stands in A's view: A knows where B is"
    near = geo.walk.ravel() & (geo.centres[:, 0] < 330)
    unk.apply(_Tk(0.0625, _at(0, "A", geo, 120, 200, near), _at(5, "B", geo, 400, 200)))
    assert unk.cells["A"][_col(geo, 400)], "out of sight: B's cell is unknown on that tick"


def test_unknown_crosses_a_one_way_special_only_one_way():
    geo = two_rooms([{"kind": "drop", "a": list(uv(170, 170)), "b": list(uv(310, 170)), "one_way": True}])
    west_room = lambda u: u[_col(geo, 120, 120)]   # noqa: E731
    east_room = lambda u: u[_col(geo, 360, 120)]   # noqa: E731
    down = ce.Unknown(geo)
    for t in (0.0, 30.0):
        down.apply(_Tk(t, _at(5, "B", geo, 120, 120)))
    assert east_room(down.cells["A"]), "from the west room it drops into the east room"
    up = ce.Unknown(geo)
    for t in (0.0, 30.0):
        up.apply(_Tk(t, _at(5, "B", geo, 360, 120)))
    assert not west_room(up.cells["A"]), "but never climbs back"
    assert not ce.Unknown(two_rooms()).cells["A"].any()


@pytest.mark.parametrize("hz", [16, 4])
def test_spread_does_not_depend_on_tick_spacing(hz):
    geo = open_hall()
    unk = ce.Unknown(geo)
    for k in range(2 * hz + 1):
        unk.apply(_Tk(k / hz, _at(0, "A", geo, 120, 200), _at(5, "B", geo, 400, 200)))
    steps = int(ce.UNKNOWN_MPS * 2.0 // geo.cell_m)
    e = _col(geo, 400)
    assert unk.cells["A"][e - steps] and not unk.cells["A"][e - steps - 1]


# ---------------------------------------------------------------- remembered ground (Task 2)

from tests.replays.control_toys import blob  # noqa: E402

A_OWN = (ce.A_PASSIVE, ce.A_SAFE, ce.A_ACTIVE)


def still(side, x, y, yaw):
    return side, [(0.0, x, y, yaw)]


def test_remembered_ground_lasts_until_unknown_reaches_it():
    geo = open_hall()
    west, _ = _halves(geo)
    z = np.zeros(GRID * GRID, bool)
    mem = ce.Memory(geo)
    mem.apply(_Tk(0.0, _at(0, "A", geo, 120, 200, west)), {"A": z, "B": z})
    blind = _at(0, "A", geo, 120, 200)
    mem.apply(_Tk(30.0, blind), {"A": z, "B": z})
    assert blind.passive[west].all(), "no enemy could have got there: still held after 30 s"
    reached = z.copy()
    reached[west[west % GRID >= 28]] = True           # unknown has walked into the west half's last 4 columns
    eaten = _at(0, "A", geo, 120, 200)
    mem.apply(_Tk(31.0, eaten), {"A": reached, "B": z})
    assert not eaten.passive[reached].any(), "unknown eats remembered ground"
    held = np.zeros(GRID * GRID, bool)
    held[west] = True
    assert eaten.passive[held & ~reached].all(), "and nothing else"


def test_the_spawn_is_held_until_an_enemy_could_have_walked_there():
    geo = _barrier_hall()
    # each team faces its own back wall: nobody watches the barrier
    players = {0: still("A", 120, 200, 180), 5: still("B", 400, 200, 0)}
    rc = ce.compute_round(blob(players, t_end=6.0), geo, ticks=[0.0, 1.0, 2.0])
    col = lambda x: int(np.searchsorted(rc.walk_cells, _col(geo, x)))   # noqa: E731
    assert rc.states[0, col(248)] in A_OWN and rc.states[0, col(160)] in A_OWN, "at the drop: A's whole side"
    assert rc.states[1, col(248)] not in A_OWN, "1 s later the cell by the barrier is gone: no grace"
    assert rc.states[2, col(160)] in A_OWN, "deep in A's side: still held at 2 s"
    assert rc.unknown["A"][2, col(240)] and not rc.unknown["A"][2, col(160)]
    assert rc.unknown["A"].shape == rc.states.shape
