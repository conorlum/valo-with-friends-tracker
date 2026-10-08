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
    """Just what Unknown and Memory read from a tick: its time, holders and what blocks sight."""

    def __init__(self, t, *holders, smokes=()):
        self.t, self.holders, self.smokes = t, {h.slot: h for h in holders}, list(smokes)


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
    assert steps >= 2, "the toy spreads more than one step in a second"
    assert unk.cells["A"][e - steps] and not unk.cells["A"][e - steps - 1], "8-connected steps at UNKNOWN_MPS"
    assert unk.cells["B"][_col(geo, 120) + steps], "each team's unknown comes from the other team's players"


def test_unknown_walks_diagonals_at_their_true_length():
    """Everything in the unknown is reachable at UNKNOWN_MPS: a diagonal step is sqrt(2) cells long, so in
    2 s (6.48 m) an enemy gets 5 cells (5.6 m) west but only 4 diagonal steps (6.3 m), not 5 (7.9 m)."""
    geo = open_hall()
    unk = ce.Unknown(geo)
    a, b = (lambda: _at(0, "A", geo, 120, 120)), (lambda: _at(5, "B", geo, 404, 284))
    for t in (0.0, 2.0):
        unk.apply(_Tk(t, a(), b()))
    at = lambda dx, dy: unk.cells["A"][geo.cell_of_px(404 - 8 * dx, 284 - 8 * dy)]  # noqa: E731
    assert at(5, 0) and not at(6, 0), "straight: 5 cells"
    assert at(4, 4) and not at(5, 5), "diagonal: 4 steps, not 5"
    assert at(4, 2) and not at(5, 3), "between: octile length (5.4 m reached, 7.0 m not)"


@pytest.mark.parametrize("block_top, sealed", [(208, True), (224, False)])
def test_unknown_cannot_squeeze_through_a_pinch_between_a_smoke_and_a_wall(block_top, sealed):
    """A screen across the hall at y 200; A watches everything north of it. South of it a map wall block
    (x 300-316, down from `block_top`) shuts a west pocket off from B but for the strip between the
    block and the screen: 1.1 m wide, under GAP_SEAL_M, nobody squeezes through unseen (the user's call,
    2026-10-01); 2.8 m wide, they can. Smokes stay walkable: the strip is sealed, not the gas."""
    from app.control.geometry import Wall
    from tests.replays.control_toys import HALL, toy_geometry

    geo = toy_geometry(f"Pinch{block_top}", [HALL], [(300, block_top, 316, 296)])
    north = geo.walk.ravel() & (geo.centres[:, 1] < 200)
    screen = Wall(segs=np.array([[96.0, 200.0, 416.0, 200.0]]))
    unk = ce.Unknown(geo)
    for t in (0.0, 30.0):
        unk.apply(_Tk(t, _at(0, "A", geo, 150, 150, north), _at(5, "B", geo, 400, 280), smokes=[screen]))
    assert unk.cells["A"][geo.cell_of_px(360, 260)], "B's side of the block is unknown"
    assert unk.cells["A"][geo.cell_of_px(200, 260)] != sealed, "the pocket: only through the strip"


def test_a_screen_across_a_narrow_door_or_a_smoke_filling_a_corridor_stays_walkable():
    """A pinch is a gap beside the gas with the wall across from it. A screen laid across a 2.2 m door, or
    a smoke filling a 2.2 m corridor, leaves no such gap: the unknown walks through the gas as ever."""
    from app.control.geometry import Wall
    from tests.replays.control_toys import HALL, toy_geometry

    geo = toy_geometry("Door16", [HALL], [(200, 96, 216, 200), (200, 216, 216, 296)])   # door y 200-216
    screen = Wall(segs=np.array([[208.0, 96.0, 208.0, 296.0]]))
    smoke = (208.0, 208.0, 3.0 / geo.m_per_px, False)
    for blockers in ([screen], [smoke]):
        unk = ce.Unknown(geo)
        for t in (0.0, 30.0):
            unk.apply(_Tk(t, _at(0, "A", geo, 120, 120), _at(5, "B", geo, 400, 280), smokes=blockers))
        assert unk.cells["A"][geo.cell_of_px(150, 250)], f"through the door past {blockers[0]!r}"


def test_live_vision_pushes_unknown_back_and_it_refills_when_they_look_away():
    geo = open_hall()
    west, east = _halves(geo)
    unk = ce.Unknown(geo)
    b = lambda: _at(5, "B", geo, 400, 200)  # noqa: E731
    for t in (0.0, 30.0):                    # 30 s: the whole hall but A's own cell
        unk.apply(_Tk(t, _at(0, "A", geo, 120, 200), b()))
    assert unk.cells["A"][_col(geo, 200)] and not unk.cells["A"][_col(geo, 120)], "a player's own cell is clear"
    band = east[geo.centres[east, 0] < 330]                         # x 256-330: short of B at 400
    unk.apply(_Tk(30.1, _at(0, "A", geo, 120, 200, band), b()))    # A sees a band, not B
    assert not unk.cells["A"][band].any(), "cleared as far as they see"
    assert unk.cells["A"][_col(geo, 200)], "the west half they don't see stays unknown"
    unk.apply(_Tk(31.1, _at(0, "A", geo, 120, 200), b()))           # they look away
    assert not unk.cells["A"][_col(geo, 260)], "the ground is free from this tick on: nobody is in it yet"
    unk.apply(_Tk(32.1, _at(0, "A", geo, 120, 200), b()))
    for k in range(2):      # cell k + 1 east of the old line is reached at 31.1 + (k + 1) x 1.12 m / 3.24 m/s
        assert unk.cells["A"][_col(geo, 256 + 8 * k + 4)], f"refilled {k + 1} cell(s) east of the old line"
    assert not unk.cells["A"][_col(geo, 256 + 8 * 2 + 4)], "the third at 32.13 s: not yet"
    assert unk.cells["A"][_col(geo, 400)], "and from B again"


def test_a_dead_enemys_unknown_goes_with_them_and_their_teammates_stays():
    """Each enemy has their own unknown (the user's call, 2026-10-01), so a dead one's is dropped whole."""
    geo = open_hall()
    unk = ce.Unknown(geo)
    a = lambda: _at(0, "A", geo, 120, 200)  # noqa: E731
    far = lambda: _at(6, "B", geo, 400, 110)  # noqa: E731   (a teammate of B's who stays alive)
    unk.apply(_Tk(0.0, a(), _at(5, "B", geo, 400, 280), far()))
    unk.apply(_Tk(1.0, a(), _at(5, "B", geo, 400, 280), far()))
    assert unk.cells["A"][_col(geo, 400, 280)]
    unk.apply(_Tk(2.0, a(), far()))          # B5 is dead: no holder
    after = unk.cells["A"]
    assert not after[_col(geo, 400, 280)], "B5's ground: out of B6's walk in 2 s"
    assert after[_col(geo, 400, 110)] and after[_col(geo, 400, 110) + GRID * 3], "B6's stays and spreads"


def _spot(slot, team, geo, x, y, cells, via="active"):
    """A holder at (x, y) px that sees `cells` with their own eyes (`active`) or through a watcher (`watch`)."""
    h = _at(slot, team, geo, x, y)
    view = np.zeros(GRID * GRID, bool)
    view[np.asarray(cells, int)] = True
    setattr(h, via, view)
    return h


@pytest.mark.parametrize("via", ["active", "watch"])
def test_spotting_an_enemy_shrinks_their_unknown_to_where_they_stand(via):
    """Round 4 of the sample at 56.5 s: S1mpLy, the attackers' last player, was in NPrightdolphin's sight
    and their unknown still covered the map. Spotted (by a player's eyes or a watcher), an enemy can
    only be where they stand; out of sight again, their unknown walks out from there."""
    geo = open_hall()
    unk = ce.Unknown(geo)
    b = lambda: _at(5, "B", geo, 400, 200)  # noqa: E731
    for t in (0.0, 30.0):
        unk.apply(_Tk(t, _at(0, "A", geo, 120, 120), b()))
    assert unk.cells["A"][_col(geo, 200, 280)], "30 s unseen: anywhere in the hall"
    unk.apply(_Tk(30.5, _spot(0, "A", geo, 120, 120, [_col(geo, 400)], via), b()))
    assert not unk.cells["A"].any(), "spotted: B is where A sees them, nowhere else"
    unk.apply(_Tk(31.5, _at(0, "A", geo, 120, 120), b()))
    steps = int(ce.UNKNOWN_MPS * 1.0 // geo.cell_m)
    assert unk.cells["A"][_col(geo, 400) - steps] and not unk.cells["A"][_col(geo, 400) - steps - 1], \
        "a second later: a second's walk from where they were seen"
    assert not unk.cells["A"][_col(geo, 200, 280)]


@pytest.mark.parametrize("pocket, kept", [(1, False), (2, False), (3, True)])
def test_a_pocket_of_unknown_vision_has_eaten_down_to_a_1x2_is_dropped(pocket, kept):
    """Round 3 of the sample at 98.0 s: vision had eaten a pocket down to 2 cells, and it grew back as the
    ground round it was freed. A piece of a team's unknown of at most DROP_PIECE_CELLS with no enemy in it
    is dropped for good (the user's call, 2026-10-01: a real enemy there makes unknown of their own)."""
    geo = open_hall()
    unk = ce.Unknown(geo)
    b = lambda: _at(5, "B", geo, 400, 200)  # noqa: E731
    for t in (0.0, 30.0):
        unk.apply(_Tk(t, _at(0, "A", geo, 120, 120), b()))
    # three cells as three of a 2x2 (the judged round 4 corner's shape): a straight 1x3 is a sliver, dropped
    # whatever its length (test_a_sliver_of_unknown_one_cell_wide_is_dropped)
    hole = [geo.cell_of_px(*p) for p in [(200, 200), (208, 200), (208, 192)][:pocket]]
    east = geo.walk.ravel() & (geo.centres[:, 0] > 330)
    view = geo.walk.ravel() & ~east
    view[hole] = False
    unk.apply(_Tk(30.5, _at(0, "A", geo, 120, 120, view), b()))
    assert (unk.cells["A"][hole] == kept).all()
    assert unk.cells["A"][_col(geo, 400)], "the big piece, with B in it, stays"
    unk.apply(_Tk(31.0, _at(0, "A", geo, 120, 120, view), b()))
    assert (unk.cells["A"][hole] == kept).all(), "a dropped pocket doesn't come back"


@pytest.mark.parametrize("shape, kept", [("strip", False), ("strip_with_enemy", True), ("two_wide", True)])
def test_a_sliver_of_unknown_one_cell_wide_is_dropped(shape, kept):
    """Sunset round 15 at 86 s: a 22-cell strip of A's unknown, one cell wide, along a wall next to Osmin, that
    nobody fits in (the user, 2026-10-04). Any length of one-cell-wide piece with no enemy in it goes; two cells
    wide stays."""
    geo = open_hall()
    unk = ce.Unknown(geo)
    enemy_at = (200 + 8 * 3, 200) if shape == "strip_with_enemy" else (400, 200)
    b = lambda: _at(5, "B", geo, *enemy_at)  # noqa: E731
    for t in (0.0, 30.0):
        unk.apply(_Tk(t, _at(0, "A", geo, 120, 120), b()))
    hole = [geo.cell_of_px(200 + 8 * k, 200) for k in range(10)]
    if shape == "two_wide":
        hole += [geo.cell_of_px(200 + 8 * k, 208) for k in range(10)]
    east = geo.walk.ravel() & (geo.centres[:, 0] > 330)
    view = geo.walk.ravel() & ~east
    view[hole] = False
    unk.apply(_Tk(30.5, _at(0, "A", geo, 120, 120, view), b()))
    assert (unk.cells["A"][hole] == kept).all()
    assert unk.cells["A"][_col(geo, 400)], "the big piece stays"


def test_a_pocket_with_an_enemy_in_it_stays_however_small():
    geo = open_hall()
    unk = ce.Unknown(geo)
    b = lambda: _at(5, "B", geo, 200, 200)  # noqa: E731
    for t in (0.0, 30.0):
        unk.apply(_Tk(t, _at(0, "A", geo, 120, 120), b()))
    view = geo.walk.ravel().copy()
    view[_col(geo, 200)] = False                 # A sees all but the cell B stands in
    unk.apply(_Tk(30.5, _at(0, "A", geo, 120, 120, view), b()))
    assert np.flatnonzero(unk.cells["A"]).tolist() == [_col(geo, 200)]


def test_each_enemy_is_tracked_on_their_own():
    """Two enemies spotted at different moments: neither tick sees both, yet each one's unknown is cut back
    to their own spot, so the far end of the hall is clear."""
    geo = open_hall()
    unk = ce.Unknown(geo)
    b5, b6 = (lambda: _at(5, "B", geo, 400, 120)), (lambda: _at(6, "B", geo, 400, 280))
    for t in (0.0, 30.0):
        unk.apply(_Tk(t, _at(0, "A", geo, 120, 200), b5(), b6()))
    far = _col(geo, 150, 280)
    unk.apply(_Tk(30.5, _spot(0, "A", geo, 120, 200, [_col(geo, 400, 120)]), b5(), b6()))
    assert unk.cells["A"][far], "B6 unseen: still anywhere"
    unk.apply(_Tk(31.0, _spot(0, "A", geo, 120, 200, [_col(geo, 400, 280)]), b5(), b6()))
    assert not unk.cells["A"][far], "B5 seen 0.5 s ago and B6 now: nobody can be at the far end"
    assert unk.cells["A"][_col(geo, 400, 120) - 1], "B5 half a second's walk from where they were seen"


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


def test_stepping_a_tick_runner_gives_the_stored_state():
    """scripts/render_control_scenes.py composes a scene by stepping engine.TickRunner up to t. The code
    review (2026-10-01): it used to apply memory alone (no barrier drop, no unknown) and differed from
    compute_round on 695 cells of this toy at 2 s."""
    geo = _barrier_hall()
    players = {0: still("A", 120, 200, 180), 5: still("B", 400, 200, 0)}
    b = blob(players, t_end=6.0)
    rc = ce.compute_round(b, geo, ticks=[0.0, 1.0, 2.0])
    rnd = ce.RoundInputs(b, geo)
    runner = ce.TickRunner(geo)
    for t in (0.0, 1.0, 2.0):
        tick = runner.step(ce.Tick(rnd, t))
    assert np.array_equal(tick.compose()["state"][rc.walk_cells], rc.states[2])


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


def test_without_a_player_unknown_still_cant_squeeze_through_a_sealed_pinch():
    """The code review (2026-10-01): the counterfactual flooded through a pinch the seal closes, so the
    player watching it was credited with holding the pocket behind it. A1 watches the 1.1 m strip between
    the screen and the block; without them the strip is still sealed (GAP_SEAL_M), so the pocket stays clear."""
    from app.control.geometry import Wall
    from tests.replays.control_toys import HALL, toy_geometry

    geo = toy_geometry("Pinch208", [HALL], [(300, 208, 316, 296)])
    north = geo.walk.ravel() & (geo.centres[:, 1] < 200)
    strip = _band(geo, 296, 320, 196, 212)
    tk = _lines(geo, {0: ("A", 150, 150, north), 1: ("A", 290, 180, strip), 5: ("B", 400, 280, np.zeros(GRID * GRID, bool))})
    tk.live = {}
    tk.smokes = [Wall(segs=np.array([[96.0, 200.0, 416.0, 200.0]]))]
    tk.unknown = {"A": _band(geo, 320, 416, 208, 296), "B": np.zeros(GRID * GRID, bool)}
    tk.sealed = ce.Unknown(geo).sealed(tk.smokes)
    assert tk.sealed[strip].any(), "the strip is a pinch"
    assert not tk.unknown_without("A", 1)[_col(geo, 200, 260)], "the pocket behind the sealed strip"


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


def test_a_seen_player_does_not_contest_ground_they_only_remember():
    geo = midwall_hall()       # a wall down x 248-264 from the north wall to y 248
    # A0 and B5 face each other through the south gap: each is seen, so each one's live lines are contested
    tk = _tick(geo, {0: still("A", 150, 270, 0), 5: still("B", 400, 270, 180)})
    assert 0 in tk.sees[5] and 5 in tk.sees[0]
    # each team's unknown is the other's half: B could be anywhere east, A anywhere west
    tk.unknown = {"A": _band(geo, 264, 416), "B": _band(geo, 96, 248)}
    corner = _band(geo, 380, 416, 96, 130)            # behind B, out of everyone's view
    assert not tk.holders[5].body[corner].any() and not tk.holders[0].body[corner].any()
    mem = ce.Memory(geo)
    mem.cells[5] = corner.copy()                      # B5 saw that corner earlier
    mem.apply(tk, tk.unknown)
    state = tk.compose()["state"]
    assert np.isin(state[corner], (ce.B_PASSIVE, ce.B_SAFE)).all(), "remembered ground stays B's in a fight"


def test_a_seen_player_contests_their_lines_only_where_an_enemy_could_be():
    geo = midwall_hall()       # a wall down x 248-264 from the north wall to y 248
    # A0 and B5 face each other through the south gap; A0 also sees north-east of the west half, B5 doesn't
    tk = _tick(geo, {0: still("A", 150, 270, 0), 5: still("B", 400, 270, 180)})
    spot = _col(geo, 230, 200)
    assert 0 in tk.sees[5] and tk.holders[0].body[spot] and not tk.holders[5].body[spot]
    tk.unknown = {"A": _band(geo, 264, 416), "B": _band(geo, 96, 248)}
    assert int(tk.compose()["state"][spot]) in A_OWN, "no enemy could be there: A's, fight or no fight"
    tk.unknown["A"] = _band(geo, 264, 416) | _band(geo, 210, 248, 180, 220)
    tk._usafe, tk._back = {}, {}
    assert int(tk.compose()["state"][spot]) in (ce.CONTESTED, ce.CONTESTED_ACTIVE), "an enemy could be there"


def test_ground_safe_for_both_teams_is_nobodys():
    from tests.replays.control_toys import door_hall
    geo = door_hall()          # the west room is hidden from the east hall but through the door
    tk = _tick(geo, {0: still("A", 400, 280, 0), 5: still("B", 380, 120, 0)})
    tk.unknown = {"A": _band(geo, 300, 416, 96, 160), "B": _band(geo, 300, 416, 96, 160)}
    room = _col(geo, 120, 120)
    assert tk.unknown_safe("A")[room] and tk.unknown_safe("B")[room], "neither unknown sees the west room"
    assert int(tk.compose()["state"][room]) == ce.NONE, "Safe for both cancels out: nobody's"


def test_a_player_holds_a_presence_bubble_behind_them():
    geo = open_hall()
    m = geo.m_per_px
    tk = _tick(geo, {0: still("A", 300, 200, 0), 5: still("B", 120, 120, 0)})      # A faces east
    a = tk.holders[0]
    assert ce.PRESENCE_M == 4.0
    walk = geo.walk.ravel()
    d = np.hypot(geo.centres[:, 0] - 300, geo.centres[:, 1] - 200) * m
    bubble = walk & (d <= ce.PRESENCE_M)
    assert (a.passive | a.active)[bubble].all(), "within the radius, all round them, is held"
    assert a.passive[_col(geo, 300 - round(1.5 / m), 200)], "1.5 m behind: passive"
    assert not (a.passive | a.active)[_col(geo, 300 - round(5.5 / m), 200)], "5.5 m behind: not held"
    assert tk.live[0][bubble].all(), "and it holds unknown back"


def test_the_presence_bubble_stops_at_anything_that_blocks_sight():
    geo = open_hall()
    m = geo.m_per_px
    smoke = {"k": "ability", "t": 0.0, "t1": 10.0, "by": 5, "kind": "Zone", "code": "Wraith", "name": "4_Smoke",
             "u": uv(270, 200)[0], "v": uv(270, 200)[1]}
    # A faces east; a smoke sits just behind them (west), so the ground past it is out of their sight
    tk = _tick(geo, {0: still("A", 300, 200, 0), 5: still("B", 120, 120, 0)}, util=[smoke])
    a = tk.holders[0]
    past = _col(geo, 300 - round(3.8 / m), 200)       # 3.8 m behind them, beyond the smoke's far edge
    los = ce.seen_from(geo, np.array([a.cell]), tk.smokes)
    assert not los[past], "the smoke hides it"
    assert not a.passive[past], "so the bubble doesn't hold it"


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


# ---------------------------------------------------------------- trips seal (2026-10-04)

from tests.replays.control_toys import HALL, toy_geometry  # noqa: E402


def _wire(x0, y0, x1, y1, by=0):
    u, v = uv(x0, y0)
    return {"k": "ability", "t": 0.0, "t1": 30.0, "by": by, "kind": "GameObject", "code": "Gumshoe",
            "name": "4_TripWire", "u": u, "v": v, "end": list(uv(x1, y1))}


def _unknown_reaches(geo, players, util, x, y, t_end=20.0):
    # the last tick a second before the round's end: the wire (like every watcher) stops at t_end
    rc = ce.compute_round(blob(players, t_end=t_end, util=util), geo, ticks=[0.0, 5.0, 10.0, t_end - 1.0])
    return bool(rc.unknown["A"][-1, int(np.searchsorted(rc.walk_cells, _col(geo, x, y)))])


def test_unknown_cannot_step_diagonally_through_a_wire_laid_at_45_degrees():
    """A wire's cells are sampled every pixel, so a slanted wire is a 4-connected staircase, except where its line
    passes exactly through cell corners: there its cells touch only diagonally. This one runs corner to corner
    all the way (x = y + 56, cells are 8 px). A diagonal step may not cut past a trip cell."""
    geo = open_hall()
    players = {0: still("A", 120, 250, 180), 5: still("B", 400, 150, 0)}   # either side of x = y + 56
    wire = _wire(152, 96, 352, 296)
    assert _unknown_reaches(geo, players, [], 250, 280), "without the wire, B could walk there"
    assert not _unknown_reaches(geo, players, [wire], 250, 280), "the wire crosses the hall wall to wall"


def test_unknown_cannot_squeeze_between_a_wires_end_and_a_wall_corner():
    """Round 2 of the Sunset sample at 32.5 s: the wire's last cell touched a wall corner only diagonally, and
    the unknown stepped round it into the room behind the wire."""
    geo = toy_geometry("TripCorner", [HALL], [(216, 256, 416, 296)])   # the south-east block is wall
    players = {0: still("A", 400, 120, 0), 5: still("B", 120, 280, 180)}
    wire = _wire(212, 96, 212, 255)        # column 26, rows 12-31; the wall starts at column 27, row 32
    assert _unknown_reaches(geo, players, [], 380, 200), "without the wire, B could walk there"
    assert not _unknown_reaches(geo, players, [wire], 380, 200), "the only way in is past the wire's end"


def test_a_map_with_heights_seals_a_wires_end_the_same_way():
    """The per-floor topology cuts the same diagonal walks: one floor everywhere spreads as the flat map does."""
    from app.control import topology
    from tests.replays.control_toys import toy_heights

    walls = [(216, 256, 416, 296)]
    flat = toy_geometry("TripCorner", [HALL], walls)
    floors = toy_heights("TripCornerH", [HALL], walls=walls)
    assert isinstance(topology.of(floors), topology.NodeTopology)
    solid = np.zeros(GRID * GRID, bool)
    solid[[r * GRID + 26 for r in range(12, 32)]] = True
    got = {}
    for name, geo in (("flat", flat), ("floors", floors)):
        node_solid = solid[geo.node_cell] if geo.heights is not None else solid
        room = geo.walk_n & ~node_solid if geo.heights is not None else geo.walk.ravel() & ~solid
        g = np.full(geo.n, np.inf)
        g[geo.cell_of_px(120, 280)] = 0.0
        got[name] = topology.of(geo).spread(g, room, np.zeros(geo.n), 30.0, 0.35, [], solid=node_solid)
    east = geo.cell_of_px(380, 200)
    assert not np.isfinite(got["flat"][east]) and not np.isfinite(got["floors"][east])
    assert np.array_equal(got["floors"][:GRID * GRID], got["flat"])


def test_a_wire_on_the_upper_floor_seals_only_that_floor():
    """Two floors everywhere, the same wire as above but on the upper floor only, and the lower floor's column
    26 watched (not solid): the upper floor stays sealed at the wall corner, the lower floor's diagonal past
    it is open (the review of PR #113: a trip used to cut every floor of its cells)."""
    from app.control import topology
    from tests.replays.control_toys import toy_heights

    geo = toy_heights("TripCornerTwoFloors", [HALL], upper=[(HALL, 4.0)], walls=[(216, 256, 416, 296)])
    column = [r * GRID + 26 for r in range(12, 32)]
    lower, upper = geo.node_of[column, 0], geo.node_of[column, 1]
    assert (lower >= 0).all() and (upper >= 0).all()
    solid = np.zeros(geo.n, bool)
    solid[upper] = True
    room = geo.walk_n & ~solid
    room[lower] = False
    start, east = geo.cell_of_px(120, 280), geo.cell_of_px(380, 200)
    got = {}
    for floor in (0, 1):
        g = np.full(geo.n, np.inf)
        g[geo.node_of[start, floor]] = 0.0
        got[floor] = topology.of(geo).spread(g, room, np.zeros(geo.n), 30.0, 0.35, [], solid=solid)
    assert np.isfinite(got[0][geo.node_of[east, 0]]), "the lower floor walks round the column's end"
    assert not np.isfinite(got[1][geo.node_of[east, 1]]), "the upper floor's wire still seals"


@pytest.mark.parametrize("heights", [False, True])
def test_a_locating_events_area_does_not_cut_past_a_wires_end(heights):
    """An area collapse (a kill, a shot) grows through the same walks as the spread: not diagonally past the
    team's live trip at a wall corner (the review of PR #113)."""
    from tests.replays.control_toys import toy_heights

    walls = [(216, 256, 416, 296)]
    geo = toy_heights("TripCornerH", [HALL], walls=walls) if heights else toy_geometry("TripCorner", [HALL], walls)
    wire = np.zeros(GRID * GRID, bool)
    wire[[r * GRID + 26 for r in range(12, 32)]] = True
    solid = wire[geo.node_cell] if heights else wire
    room = (geo.walk_n if heights else geo.walk.ravel()) & ~solid
    unk = ce.TickRunner(geo).unknown
    x, y = 204, 268                                  # just west of the wire's end, below it
    centre = geo.cell_of_px(x, y)
    past = geo.cell_of_px(228, 252)                  # east of the wire's end, one diagonal step round it
    assert unk._area(centre, x, y, 10.0, room)[past], "unsealed, the area reaches round the end"
    assert not unk._area(centre, x, y, 10.0, room, solid)[past]


def test_a_dead_cyphers_wire_seals_nothing():
    geo = open_hall()
    players = {0: still("A", 120, 250, 180), 1: still("A", 110, 110, 180), 5: still("B", 400, 150, 0)}
    rc = ce.compute_round(blob(players, t_end=20.0, util=[_wire(152, 96, 352, 296)], deaths={0: 1.0}), geo,
                          ticks=[0.0, 5.0, 10.0, 19.0])
    assert rc.unknown["A"][-1, int(np.searchsorted(rc.walk_cells, _col(geo, 250, 280)))]


# ---------------------------------------------------------------- contested needs a live claim (D5, 2026-10-04)


def _relive(tk):
    """`_lines` swaps the holders after the tick was built: rebuild `live` from the new ones."""
    tk.live = {}
    for s, h in tk.holders.items():
        lv = h.active | h.passive | h.watch
        lv[h.cell] = True
        tk.live[s] = lv
    return tk


def _hall(a0=None, b5=None):
    """Open hall: A0 at (200, 250), A1 at (300, 280), B5 at (400, 200), each seeing exactly what's given.
    Nobody stands in the west band (x 96-160): a player's own cell is live, which would keep it contested."""
    geo = open_hall()
    z = np.zeros(GRID * GRID, bool)
    tk = _lines(geo, {0: ("A", 200, 250, z if a0 is None else a0), 1: ("A", 300, 280, z),
                      5: ("B", 400, 200, z if b5 is None else b5)})
    for h in tk.holders.values():
        assert not _band(geo, *WEST_X)[h.cell], "keep players out of the west band"
    return geo, _relive(tk)


def _remember(tk, geo, slots, cells):
    mem = ce.Memory(geo)
    for s in slots:
        mem.cells[s] = cells.copy()
    mem.apply(tk, tk.unknown)
    return mem


WEST_X = (96, 160)
CONTESTED = (ce.CONTESTED, ce.CONTESTED_ACTIVE)


def test_memory_against_safe_is_nobodys():
    """Ascent round 2 at 15.6 s (the user, 2026-10-04): one team remembers it, the other holds it as Safe."""
    geo, tk = _hall()
    west = _band(geo, *WEST_X)
    z = np.zeros(GRID * GRID, bool)
    tk.unknown = {"A": _band(geo, 380, 416), "B": z}     # B could be in the east strip; A nowhere
    assert tk.unknown_safe("B")[west].all() and not tk.unknown_safe("A")[west].any()
    _remember(tk, geo, [0], west)
    assert tk.holders[0].memory[west].all()
    assert (tk.compose()["state"][west] == ce.NONE).all(), "memory against Safe: nobody's"


def test_memory_against_memory_is_nobodys():
    geo, tk = _hall()
    west = _band(geo, *WEST_X)
    tk.unknown = {"A": _band(geo, 380, 416), "B": _band(geo, 200, 232)}    # both see the whole hall: no Safe
    assert not tk.unknown_safe("A")[west].any() and not tk.unknown_safe("B")[west].any()
    _remember(tk, geo, [0, 5], west)
    assert tk.holders[0].memory[west].all() and tk.holders[5].memory[west].all()
    assert (tk.compose()["state"][west] == ce.NONE).all(), "memory against memory: nobody's"


@pytest.mark.parametrize("how", ["view", "watcher"])
def test_a_live_claim_against_an_inferred_one_stays_contested(how):
    west = _band(open_hall(), *WEST_X)
    z = np.zeros(GRID * GRID, bool)
    geo, tk = _hall(a0=west if how == "view" else None)
    if how == "watcher":
        tk.holders[0].watch = west.copy()
        _relive(tk)
    tk.unknown = {"A": _band(geo, 380, 416), "B": z}     # B holds the whole hall as Safe
    assert np.isin(tk.compose()["state"][west], CONTESTED).all(), "A holds it live against B's Safe"


def test_a_pictures_remembered_enemy_view_is_not_live():
    geo, tk = _hall()
    west = _band(geo, *WEST_X)
    tk.unknown = {"A": _band(geo, 380, 416), "B": _band(geo, 200, 232)}
    _remember(tk, geo, [0], west)
    tk.extra_passive = {"B": west.copy()}                # B's picture: an A player's last view, remembered
    assert (tk.compose()["state"][west] == ce.NONE).all(), "a remembered view is inferred, not live"


def test_live_claims_leave_out_the_removed_player():
    west = _band(open_hall(), *WEST_X)
    geo, tk = _hall(a0=west)
    assert tk.live_claims("A")[west].all()
    assert not tk.live_claims("A", removed=0)[west].any(), "without A0 nothing of A's is live there"
    assert tk.live_claims("A", removed=5)[west].all(), "removing an enemy changes nothing of A's"


def test_the_counterfactual_applies_the_rule_full_and_incremental():
    """Control credit recomputes the tick without one player: the rule must hold there too, on both paths."""
    geo, tk = _hall()
    west = _band(geo, *WEST_X)
    tk.unknown = {"A": _band(geo, 380, 416), "B": np.zeros(GRID * GRID, bool)}
    _remember(tk, geo, [0], west)
    base = tk.compose()
    full = tk.compose(removed=1)["state"]
    inc = tk.compose(removed=1, base=base, full=False)["state"]
    assert (full[west] == ce.NONE).all(), "without A1, A0's memory against B's Safe is still nobody's"
    assert np.array_equal(full[west], inc[west]), "the incremental counterfactual agrees"


def test_a_knowledge_picture_counts_only_the_enemies_the_team_sees_as_live():
    geo = open_hall()
    rnd = ce.RoundInputs(blob({0: still("A", 120, 200, 180), 5: still("B", 400, 200, 0)}), geo)
    tk = ce.Tick(rnd, 1.0)
    tk.unknown = {"A": _band(geo, 300, 316), "B": _band(geo, 100, 116)}
    assert 5 not in tk.sees[0] and 0 not in tk.sees[5], "they face apart: neither sees the other"
    assert tk.live_claims("B")[tk.live[5] & geo.walk_n].all()
    kt = ce.Knowledge(rnd, "A").tick_for(tk, 1.0)
    assert 5 not in kt.holders, "A's picture has no unseen B player"
    assert not kt.live_claims("B").any(), "so nothing of B's is live in it"


@pytest.mark.parametrize("how", ["active", "passive", "watch"])
def test_an_enemy_holding_it_live_ends_remembered_ground(how):
    """Image 14 (the user, 2026-10-04): A remembers ground that B is looking at now: it's B's, not contested."""
    geo, tk = _hall()
    west = _band(geo, *WEST_X)
    setattr(tk.holders[5], how, west.copy())
    _relive(tk)
    tk.unknown = {"A": _band(geo, 380, 416), "B": _band(geo, 200, 232)}
    mem = _remember(tk, geo, [0], west)
    assert not tk.holders[0].memory[west].any() and not mem.cells[0][west].any(), "B holds it live: A's memory ends"
    assert not np.isin(tk.compose()["state"][west], CONTESTED).any()


def test_an_enemy_standing_in_remembered_ground_ends_it_there():
    geo, tk = _hall()
    spot = tk.holders[5].cell
    remembered = np.zeros(GRID * GRID, bool)
    remembered[spot] = True
    tk.unknown = {"A": np.zeros(GRID * GRID, bool), "B": np.zeros(GRID * GRID, bool)}
    mem = _remember(tk, geo, [0], remembered)
    assert not mem.cells[0][spot], "B5 stands there"


def test_ground_an_enemy_saw_does_not_come_back_as_memory():
    geo = open_hall()
    west, _ = _halves(geo)
    z = np.zeros(GRID * GRID, bool)
    none = {"A": z, "B": z}
    mem = ce.Memory(geo)
    mem.apply(_Tk(0.0, _at(0, "A", geo, 120, 200, west), _at(5, "B", geo, 400, 200)), none)
    a1 = _at(0, "A", geo, 120, 200)
    mem.apply(_Tk(1.0, a1, _at(5, "B", geo, 400, 200, west)), none)
    assert not a1.passive[west].any(), "B looked at it: A's memory of it ends"
    a2 = _at(0, "A", geo, 120, 200)
    mem.apply(_Tk(2.0, a2, _at(5, "B", geo, 400, 200)), none)
    assert not a2.passive[west].any(), "and doesn't come back when B looks away"
