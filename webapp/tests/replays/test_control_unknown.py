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
    far = lambda: _at(6, "B", geo, 400, 110)  # noqa: E731   (a teammate of B's who stays alive)
    unk.apply(_Tk(0.0, a(), _at(5, "B", geo, 400, 280), far()))
    unk.apply(_Tk(1.0, a(), _at(5, "B", geo, 400, 280), far()))
    before = unk.cells["A"].copy()
    unk.apply(_Tk(2.0, a(), far()))          # B5 is dead: no holder
    after = unk.cells["A"]
    assert after[before].all(), "what was out stays"
    assert after.sum() > before.sum(), "and keeps spreading from itself"


def test_unknown_clears_when_the_whole_enemy_team_is_dead():
    geo = open_hall()
    unk = ce.Unknown(geo)
    a = lambda: _at(0, "A", geo, 120, 200)  # noqa: E731
    unk.apply(_Tk(0.0, a(), _at(5, "B", geo, 400, 200)))
    unk.apply(_Tk(1.0, a(), _at(5, "B", geo, 400, 200)))
    assert unk.cells["A"].any()
    unk.apply(_Tk(1.5, a()))
    assert not unk.cells["A"].any(), "nobody left: nobody could be anywhere"
    assert unk.cells["B"].any(), "A is alive: B's unknown is untouched"


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


# ---------------------------------------------------------------- backfill and Safe (Task 3)

from tests.replays.control_toys import midwall_hall  # noqa: E402


def _band(geo, x0, x1, y0=96, y1=296):
    cx, cy = geo.centres[:, 0], geo.centres[:, 1]
    return geo.walk.ravel() & (cx >= x0) & (cx < x1) & (cy >= y0) & (cy < y1)


def _tick(geo, players, t=1.0, **kw):
    return ce.Tick(ce.RoundInputs(blob(players, **kw), geo), t)


def _lines(geo, views: dict):
    """A tick whose holders see exactly `views`: {slot: (team, x, y, active flat)}."""
    tk = _tick(geo, {s: still(team, x, y, 0) for s, (team, x, y, _) in views.items()})
    z = np.zeros(GRID * GRID, bool)
    tk.holders = {s: ce.Holder(s, team, geo.cell_of_px(x, y), x, y, act.copy(), z.copy(), z.copy(), act.copy(),
                               act.copy(), False, "hold") for s, (team, x, y, act) in views.items()}
    tk._back = {}
    return tk


def test_backfill_never_claims_where_an_enemy_could_be():
    geo = open_hall()
    tk = _lines(geo, {0: ("A", 150, 200, _band(geo, 240, 256)), 5: ("B", 400, 200, _band(geo, 300, 316))})
    assert tk.backfill("A")[0][_col(geo, 110, 120)], "without unknown: behind A's line is A's"
    strip = _band(geo, 96, 140)
    tk.unknown = {"A": strip.copy(), "B": np.zeros(GRID * GRID, bool)}
    tk._back = {}
    back = tk.backfill("A")[0]
    assert not back[strip].any() and back[_col(geo, 200, 250)]


def test_safe_is_what_unknown_cannot_see():
    geo = midwall_hall()       # a wall down x 248-264 from the north wall to y 248: a gap to the south
    tk = _tick(geo, {0: still("A", 120, 120, 180), 5: still("B", 400, 120, 0)})
    tk.unknown = {"A": _band(geo, 300, 400, 96, 160), "B": np.zeros(GRID * GRID, bool)}
    safe = tk.unknown_safe("A")
    assert safe[_col(geo, 150, 120)], "behind the wall from unknown: Safe"
    assert not safe[_col(geo, 350, 120)], "unknown itself is not Safe"
    assert not safe[_col(geo, 330, 250)], "unknown sees it"
    assert tk.unknown_safe("B")[_col(geo, 350, 120)], "a team with no unknown: all Safe"
    # through compose: give B an unknown over the west half, so B isn't Safe there and nothing is contested
    tk.unknown["B"] = _band(geo, 96, 248)
    tk._usafe = {}
    state = tk.compose()["state"]
    assert int(state[_col(geo, 150, 120)]) == ce.A_SAFE


def test_a_smoke_hides_ground_from_unknown():
    geo = open_hall()
    smoke = {"k": "ability", "t": 0.0, "t1": 10.0, "by": 5, "kind": "Zone", "code": "Wraith", "name": "4_Smoke",
             "u": uv(250, 200)[0], "v": uv(250, 200)[1]}
    tk = _tick(geo, {0: still("A", 120, 120, 180), 5: still("B", 400, 120, 0)}, util=[smoke])
    tk.unknown = {"A": _band(geo, 300, 316, 192, 208), "B": np.zeros(GRID * GRID, bool)}
    safe = tk.unknown_safe("A")
    assert safe[_col(geo, 150, 200)], "straight through the smoke: hidden"
    assert not safe[_col(geo, 290, 200)], "this side of the smoke: seen"


def test_a_player_holding_a_choke_controls_the_ground_behind_it():
    from tests.replays.control_toys import door_hall
    geo = door_hall()          # a wall at x 200-216 with a one-cell door at its south end (y 288-296)
    tk = _tick(geo, {0: still("A", 208, 292, 0), 5: still("B", 400, 150, 180)})
    tk.unknown = {"A": _band(geo, 300, 416), "B": _band(geo, 96, 200)}
    behind = _col(geo, 120, 120)
    without = tk.unknown_without("A", 0)
    assert without[behind] and not tk.unknown["A"][behind], "without A in the door, unknown pours into the west room"
    base, cf = tk.compose()["state"], tk.compose(removed=0)["state"]
    assert int(base[behind]) in A_OWN and int(cf[behind]) not in A_OWN
    assert tk.compose(removed=0, base=tk.compose(), full=False)["state"][behind] == cf[behind]


def test_ground_unknown_could_reach_anyway_is_not_a_players_control():
    geo = open_hall()
    tk = _tick(geo, {0: still("A", 150, 200, 180), 5: still("B", 400, 200, 0)})
    tk.unknown = {"A": _band(geo, 400, 416), "B": np.zeros(GRID * GRID, bool)}
    gained = tk.unknown_without("A", 0) & ~tk.unknown["A"]
    assert gained.any()
    assert not (gained & ~tk.live[0]).any(), "only the cells their own live control held back"


def test_a_dead_team_holds_no_safe_ground():
    geo = midwall_hall()       # the wall hides the west half's north from A's unknown in the east
    tk = _tick(geo, {0: still("A", 150, 200, 0), 5: still("B", 400, 120, 0)}, deaths={0: 0.5})
    assert 0 not in tk.holders
    tk.unknown = {"A": _band(geo, 300, 400, 96, 160), "B": np.zeros(GRID * GRID, bool)}
    assert tk.unknown_safe("A")[_col(geo, 150, 120)], "the hidden ground is outside A's unknown's sight"
    state = tk.compose()["state"]
    walk = geo.walk.ravel()
    assert not np.isin(state[walk], A_OWN).any(), "nobody alive on A: A holds nothing"
    assert not np.isin(state[walk], (ce.CONTESTED, ce.CONTESTED_ACTIVE)).any()


def test_without_a_player_unknown_eats_a_teammates_remembered_ground_behind_them():
    from tests.replays.control_toys import door_hall
    geo = door_hall()          # A0 holds the one-cell door; A1 stands in the west room facing its west wall
    tk = _tick(geo, {0: still("A", 208, 292, 0), 1: still("A", 104, 200, 180), 5: still("B", 400, 150, 180)})
    tk.unknown = {"A": _band(geo, 300, 416), "B": _band(geo, 96, 200)}
    room = _band(geo, 96, 200)
    mem = ce.Memory(geo)
    mem.cells[1] = room.copy()            # A1 saw the whole west room earlier
    mem.apply(tk, tk.unknown)
    behind = _col(geo, 180, 120)
    assert not tk.live[1][behind] and tk.holders[1].passive[behind], "remembered, not seen now"
    base, cf = tk.compose()["state"], tk.compose(removed=0)["state"]
    assert int(base[behind]) in A_OWN
    assert int(cf[behind]) not in A_OWN, "without A0, unknown reaches it and the memory ends"


def test_a_player_holds_a_presence_bubble_behind_them():
    geo = open_hall()
    m = geo.m_per_px
    tk = _tick(geo, {0: still("A", 300, 200, 0), 5: still("B", 120, 120, 0)})      # A faces east
    a = tk.holders[0]
    assert ce.PRESENCE_M == 2.0
    walk = geo.walk.ravel()
    d = np.hypot(geo.centres[:, 0] - 300, geo.centres[:, 1] - 200) * m
    bubble = walk & (d <= ce.PRESENCE_M)
    assert (a.passive | a.active)[bubble].all(), "within 2 m, all round them, is held"
    assert a.passive[_col(geo, 300 - round(1.5 / m), 200)], "1.5 m behind: passive"
    assert not (a.passive | a.active)[_col(geo, 300 - round(3.5 / m), 200)], "3.5 m behind: not held"
    assert tk.live[0][bubble].all(), "and it holds unknown back"


def test_a_flashed_player_has_no_presence_bubble():
    geo = open_hall()
    flash = {"k": "flash", "t": 0.5, "by": 5, "ability": "Phoenix_Q", "hits": [[0, 0.5, 2.5]]}
    tk = _tick(geo, {0: still("A", 300, 200, 0), 5: still("B", 120, 120, 0)}, util=[flash])
    assert ce._during(tk.rnd.flashed[0], 1.0)
    a = tk.holders[0]
    assert not a.passive[_col(geo, 300 - round(1.5 / geo.m_per_px), 200)], "flashed: nothing, as before"


def test_the_knowledge_views_use_the_same_unknown():
    geo = open_hall()
    rnd = ce.RoundInputs(blob({0: still("A", 120, 200, 0), 5: still("B", 400, 200, 180)}), geo)
    tk = ce.Tick(rnd, 1.0)
    tk.unknown = {"A": _band(geo, 300, 316), "B": _band(geo, 100, 116)}
    kt = ce.Knowledge(rnd, "A").tick_for(tk, 1.0)
    assert kt.unknown is tk.unknown
