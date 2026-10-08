"""The per-floor engine on toy maps with heights
(docs/superpowers/specs/2026-10-01-control-heights-design.md, part 4; Testing, "Per-floor engine" and "Trips").
Positions are minimap pixels; cells are 8 px (1.12 m); heights are position-z in metres."""

import numpy as np
import pytest

from app.control import engine as ce
from app.control import geometry as cg
from app.control import heights as hc
from app.control import topology
from app.control.encode import encode_data, encode_summary
from app.replays import control_format as cf
from tests.replays.control_toys import HALL, height_blob, toy_heights

GRID = cg.GRID
Y = 204


def bridge():
    """A plateau 4 m up west of x 240; a bridge from it east to x 288, 4 m up, over ground at 0 m (those
    cells have two floors); everything east of the bridge at 0 m."""
    return toy_heights("Bridge", [HALL], ground=[((96, 96, 240, 296), 4.0)], upper=[((240, 96, 288, 296), 4.0)])


def ledge(drop_at=None, unresolved=()):
    """West of x 256 is 4 m up, east is 0 m. `drop_at` = y px of one cell pair where the ledge can be
    dropped from (one way)."""
    links = [((252, drop_at, 0), (260, drop_at, 0), True)] if drop_at is not None else []
    return toy_heights(f"Ledge{drop_at}{len(unresolved)}", [HALL], ground=[((96, 96, 256, 296), 4.0)], links=links,
                       unresolved=unresolved)


def node(geo, x, y=Y, floor=0):
    return int(geo.node_of[geo.cell_of_px(x, y), floor])


def still(side, x, y, yaw, z):
    return side, [(0.0, x, y, yaw, z)]


def tick(geo, players, t=1.0, **kw):
    return ce.Tick(ce.RoundInputs(height_blob(players, t_end=10.0, **kw), geo), t)


class _Tk:
    """What Unknown reads from a tick (as tests/replays/test_control_unknown.py)."""

    def __init__(self, t, *holders):
        self.t, self.holders, self.smokes = t, {h.slot: h for h in holders}, []


def _at(slot, team, geo, at_node, view=()):
    """A holder standing on `at_node` whose live view (active) is exactly the nodes in `view`."""
    z = np.zeros(geo.n, bool)
    seen = z.copy()
    seen[list(view)] = True
    x, y = geo.centres[at_node]
    return ce.Holder(slot, team, at_node, float(x), float(y), seen.copy(), z.copy(), z.copy(), seen.copy(),
                     seen.copy(), False, "hold")


# ---------------------------------------------------------------- where a player stands, and spotting


def test_a_player_stands_on_the_floor_under_their_own_height():
    geo = bridge()
    tk = tick(geo, {0: still("A", 260, Y, 0, 4.0), 5: still("B", 260, Y, 180, 0.0), 6: still("B", 350, Y, 180, 0.0)})
    assert tk.holders[0].cell == node(geo, 260, floor=1) and tk.holders[5].cell == node(geo, 260, floor=0)
    assert tk.holders[6].cell == geo.cell_of_px(350, Y), "one floor: the cell itself"
    assert all(len(getattr(tk.holders[0], name)) == geo.n for name in ("active", "passive", "watch", "raw", "body"))


def test_an_enemy_in_the_tunnel_is_not_spotted_from_the_bridge_side_or_from_right_above():
    geo = bridge()
    players = {0: still("A", 200, Y, 0, 4.0),        # on the plateau, looking along the bridge
               1: still("A", 260, Y, 0, 4.0),        # on the bridge, right above the enemy
               5: still("B", 260, Y, 180, 0.0),      # in the tunnel
               6: still("B", 276, Y, 180, 4.0)}      # on the bridge
    tk = tick(geo, players)
    assert tk.holders[0].body[node(geo, 276, floor=1)], "the viewer sees the bridge"
    assert 6 in tk.sees[0] and 5 not in tk.sees[0], "seeing the bridge doesn't spot the player in the tunnel"
    assert 5 not in tk.sees[1] and 1 not in tk.sees[5], "nor does standing on it right above them, either way"
    assert 6 in tk.sees[1]
    flat = toy_heights("Bridge-as-flat", [HALL], ground=[((0, 0, 1024, 1024), 0.0)])
    as_2d = tick(flat, players)
    assert 5 in as_2d.sees[0], "in 2D the same enemy is spotted: this is what heights fix"


def test_an_enemy_above_their_floor_is_spotted_at_their_own_height():
    # From 2 m back on a 4 m ledge, a body standing on the ground 3 m out is hidden by the ledge's edge. The
    # same enemy 2.4 m up (boosted, mid-jump, on a Sage wall) shows over it: spotting uses their real height.
    geo = ledge()
    x = 256 - 2 / 0.14
    out = x + 3 / 0.14
    on_ground = tick(geo, {0: still("A", x, Y, 0, 4.0), 5: still("B", out, Y, 180, 0.0)})
    assert not on_ground.holders[0].body[on_ground.holders[5].cell], "the ground there is hidden, and stays hidden"
    assert 5 not in on_ground.sees[0] and on_ground.direct == {}
    raised = tick(geo, {0: still("A", x, Y, 0, 4.0), 5: still("B", out, Y, 180, 2.4)})
    assert raised.holders[5].cell == on_ground.holders[5].cell and not raised.holders[0].body[raised.holders[5].cell]
    assert 5 in raised.sees[0] and raised.direct == {(0, 5): True}, "seen, and in the active cone"
    away = tick(geo, {0: still("A", x, Y, 180, 4.0), 5: still("B", out, Y, 180, 2.4)})
    assert 5 not in away.sees[0], "not behind the viewer"
    flashed = tick(geo, {0: still("A", x, Y, 0, 4.0), 5: still("B", out, Y, 180, 2.4)},
                   util=[{"k": "flash", "t": 0.0, "by": 5, "hits": [[0, 0.5, 2.0]]}])
    assert 5 not in flashed.sees[0], "nor by a flashed viewer"
    # the unknown takes it as a sighting too: the enemy is there, and nowhere else
    unknown = ce.Unknown(geo)
    unknown.apply(raised)
    assert unknown.seen["A"][5][0] == raised.holders[5].cell


def test_seeing_the_bridge_clears_the_bridges_unknown_and_not_the_tunnels():
    geo = bridge()
    tops = [node(geo, x, floor=1) for x in range(244, 288, 8)]
    tunnel = [node(geo, x, floor=0) for x in range(244, 288, 8)]
    unk = ce.Unknown(geo)
    start = np.zeros(geo.n, bool)
    start[tops + tunnel] = True
    unk.begin({"B": (start, {})})                              # A's unknown: both floors of the bridge cells
    viewer = _at(0, "A", geo, node(geo, 200), view=tops)       # A sees the bridge's top, all of it
    enemy = _at(5, "B", geo, node(geo, 400))                   # far away, unseen
    unk.apply(_Tk(0.0, viewer, enemy))
    assert not unk.cells["A"][tops].any(), "the bridge is seen: nobody can be on it"
    assert unk.cells["A"][tunnel].all(), "the tunnel under it isn't: somebody could be in it"


# ---------------------------------------------------------------- walking per floor


def test_the_floors_of_one_cell_are_not_neighbours_and_the_bridge_joins_the_plateau():
    geo = bridge()
    topo = topology.of(geo)
    assert isinstance(topo, topology.NodeTopology) and topo.n == geo.n
    on, under = node(geo, 260, floor=1), node(geo, 260, floor=0)
    assert under not in topo.around(on) and on not in topo.around(under)
    assert node(geo, 268, floor=1) in topo.around(on) and node(geo, 268, floor=0) in topo.around(under)
    assert node(geo, 236) in topo.around(node(geo, 244, floor=1)), "the bridge walks onto the plateau"
    assert node(geo, 236) not in topo.around(node(geo, 244, floor=0)), "the tunnel ends at the plateau's face"
    dist = topo.dist(under)
    assert dist[node(geo, 400)] > 0 and dist[on] == -1 and dist[node(geo, 200)] == -1, "no walk from tunnel to top"
    lab = topo.label(geo.walk_n)
    assert lab[on] == lab[node(geo, 200)] != lab[under] == lab[node(geo, 400)] and lab[on] > 0


def test_the_unknown_stays_on_its_floor():
    geo = bridge()
    unk = ce.Unknown(geo)
    for t in (0.0, 30.0):                                      # an enemy on the low ground east, for 30 s
        unk.apply(_Tk(t, _at(0, "A", geo, node(geo, 120)), _at(5, "B", geo, node(geo, 400))))
    assert unk.cells["A"][[node(geo, x, floor=0) for x in range(244, 288, 8)]].all(), "into the tunnel"
    assert not unk.cells["A"][[node(geo, x, floor=1) for x in range(244, 288, 8)]].any(), "never onto the bridge"
    assert not unk.cells["A"][node(geo, 200)], "nor up the plateau's face"


def drop_ledge(height: float, how: int):
    """West of x 256 is `height` m up; the ledge can be left at y 204 only, by a one-way drop of kind `how`."""
    return toy_heights(f"Ledge{height}-{how}", [HALL], ground=[((96, 96, 256, 296), height)],
                       links=[((252, Y, 0), (260, Y, 0), True, how)])


def unknown_below(geo, from_top: bool = True) -> np.ndarray:
    """A's unknown after an enemy spent a minute on top of the ledge (or below it), A far away on the same side."""
    unk = ce.Unknown(geo)
    enemy = node(geo, 200) if from_top else node(geo, 300)
    mine = node(geo, 400, 110) if from_top else node(geo, 120, 110)
    for t in (0.0, 60.0):
        unk.apply(_Tk(t, _at(0, "A", geo, mine), _at(5, "B", geo, enemy)))
    return unk.cells["A"]


def test_the_unknown_goes_down_a_slide_or_a_low_fall_and_never_back_up():
    # docs/superpowers/specs/2026-10-05-height-slopes-design.md, part 5
    for geo in (drop_ledge(4.0, hc.EDGE_SLIDE), drop_ledge(1.0, hc.EDGE_FALL)):
        assert (node(geo, 252), node(geo, 260), True) in topology.of(geo).links
        assert unknown_below(geo)[node(geo, 300)], "from the top it reaches the low ground"
        up = unknown_below(geo, from_top=False)
        assert up[node(geo, 260)] and not up[node(geo, 252)] and not up[node(geo, 200)], "but never climbs it"
    assert not unknown_below(ledge())[node(ledge(), 300)], "with no walked drop the ledge isn't crossed at all"


def test_the_unknown_stops_at_a_fall_too_high_to_land_quietly():
    assert not unknown_below(drop_ledge(4.0, hc.EDGE_FALL))[node(drop_ledge(4.0, hc.EDGE_FALL), 300)]
    just_over = drop_ledge(1.1, hc.EDGE_FALL)
    assert not unknown_below(just_over)[node(just_over, 300)], "anything over SILENT_DROP_M makes a sound"
    assert hc.SILENT_DROP_M == 1.0


def test_a_real_player_still_drops_off_any_ledge():
    # Only the unknown's spread is closed: walking distances, connected pieces and the engine's links are not.
    geo = drop_ledge(4.0, hc.EDGE_FALL)
    topo = topology.of(geo)
    assert (node(geo, 252), node(geo, 260), True) in topo.links
    assert topo.dist(node(geo, 200))[node(geo, 300)] > 0 and topo.dist(node(geo, 300))[node(geo, 200)] == -1
    grown = topo.dilate(np.eye(1, geo.n, node(geo, 252), dtype=bool)[0], eight=True)
    assert grown[node(geo, 260)], "growing a mask (memory, backfill) still goes over the edge"


def test_a_fall_edge_between_stacked_floors_is_judged_by_its_own_two_floors():
    # A bridge 4 m over the ground: dropping from the bridge onto the ground beside it is a 4 m fall, whatever the
    # ground floor of the bridge's own cell is.
    geo = toy_heights("BridgeDrop", [HALL], upper=[((240, 96, 288, 296), 4.0)],
                      links=[((284, Y, 1), (292, Y, 0), True, hc.EDGE_FALL)])
    topo = topology.of(geo)
    on, beside = node(geo, 284, floor=1), node(geo, 292)
    assert (on, beside, True) in topo.links
    row = topo.in_from[beside].tolist()
    assert np.isinf(topo.in_cost_quiet[beside, row.index(on)]) and np.isfinite(topo.in_cost[beside, row.index(on)])


def test_the_unknown_climbs_a_connected_step():
    geo = toy_heights("Step", [HALL], ground=[((96, 96, 256, 296), 0.5)])      # half a metre: a step, both ways
    unk = ce.Unknown(geo)
    for t in (0.0, 60.0):
        unk.apply(_Tk(t, _at(0, "A", geo, node(geo, 120, 110)), _at(5, "B", geo, node(geo, 300))))
    assert unk.cells["A"][node(geo, 200)]


def test_the_unknown_crosses_an_unresolved_strip_as_it_does_in_2d():
    # The ledge with the column at its foot unresolved: that column walks in 2D, to every floor around it,
    # so the unknown gets from the low ground up onto the ledge through it (uncertain terrain is 2D).
    geo = ledge(unresolved=[(256, 96, 264, 296)])
    strip = geo.cell_of_px(260, Y)
    assert geo.unresolved[strip]
    topo = topology.of(geo)
    assert {node(geo, 252), node(geo, 268)} <= set(topo.around(strip))
    unk = ce.Unknown(geo)
    for t in (0.0, 60.0):
        unk.apply(_Tk(t, _at(0, "A", geo, node(geo, 120, 110)), _at(5, "B", geo, node(geo, 300))))
    assert unk.cells["A"][strip] and unk.cells["A"][node(geo, 200)]


def test_specials_join_every_floor_of_their_cells():
    from tests.replays.control_toys import uv

    special = [{"kind": "teleporter", "a": list(uv(260, Y)), "b": list(uv(400, 110)), "one_way": True}]
    geo = toy_heights("BridgeSpecial", [HALL], ground=[((96, 96, 240, 296), 4.0)],
                      upper=[((240, 96, 288, 296), 4.0)], specials=special)
    links = ce.special_links(geo)
    far = geo.cell_of_px(*geo.px_of_uv(*uv(400, 110)))
    assert sorted(links) == sorted([(node(geo, 260, floor=0), far, True), (node(geo, 260, floor=1), far, True)])


# ---------------------------------------------------------------- the stored picture is per cell


def test_the_collapse_rule():
    geo = bridge()
    on, under = node(geo, 260, floor=1), node(geo, 260, floor=0)
    other_on, other_under = node(geo, 268, floor=1), node(geo, 268, floor=0)
    state = np.zeros(geo.n, np.uint8)
    state[[on, under]] = ce.A_PASSIVE                              # the floors agree
    state[other_on], state[other_under] = ce.A_SAFE, ce.B_PASSIVE  # they don't
    out = ce.collapse_states(geo, state)
    assert out.shape == (GRID * GRID,)
    assert out[under] == ce.A_PASSIVE and out[other_under] == ce.CONTESTED
    state[other_on] = ce.A_ACTIVE
    assert ce.collapse_states(geo, state)[other_under] == ce.CONTESTED_ACTIVE, "any floor active"
    state[other_on], state[other_under] = ce.NONE, ce.B_SAFE
    assert ce.collapse_states(geo, state)[other_under] == ce.CONTESTED, "nobody's against held: they disagree"
    state[geo.cell_of_px(350, Y)] = ce.B_ACTIVE
    assert ce.collapse_states(geo, state)[geo.cell_of_px(350, Y)] == ce.B_ACTIVE, "one floor: that floor's"


def test_three_floors_collapse_by_the_same_rule():
    geo = toy_heights("Three", [HALL], upper=[((240, 96, 288, 296), 4.0), ((240, 96, 288, 296), 8.0)])
    low, mid, top = (node(geo, 260, floor=k) for k in range(3))
    state = np.zeros(geo.n, np.uint8)
    state[[low, mid, top]] = ce.B_SAFE
    assert ce.collapse_states(geo, state)[low] == ce.B_SAFE
    state[mid] = ce.B_PASSIVE
    assert ce.collapse_states(geo, state)[low] == ce.CONTESTED, "two agree, one doesn't"


def test_a_round_on_a_height_map_is_stored_per_walkable_cell():
    geo = bridge()
    players = {0: ("A", [(0.0, 200, Y, 0, 4.0), (6.0, 276, Y, 0, 4.0)]), 1: still("A", 150, 150, 0, 4.0),
               5: ("B", [(0.0, 400, Y, 180, 0.0), (6.0, 270, Y, 180, 0.0)]), 6: still("B", 380, 260, 180, 0.0)}
    blob = height_blob(players, t_end=6.0)
    rc = ce.compute_round(blob, geo)
    cells = int(geo.walk.sum())
    assert rc.states.shape == (len(rc.ticks), cells) and rc.coverage_masks.shape == (len(rc.ticks), 10, cells)
    assert rc.unknown["A"].shape == (len(rc.ticks), cells) and rc.knew_states["A"].shape == rc.states.shape
    assert "approximate heights" not in " ".join(rc.missing_inputs)
    header, streams = cf.unpack_data(encode_data(rc, blob))
    assert header["cells"] == cells and header["revision"] == cf.CONTROL_REVISION
    assert len(cf.walk_bitmap(header)) == cells
    assert cf.unpack_summary(encode_summary(rc, blob))["cells"] == cells
    # the tunnel walker ends under the bridge walker: neither ever spots the other, so both keep their floor
    col = np.flatnonzero(rc.walk_cells == geo.cell_of_px(276, Y))[0]
    assert rc.states[-1, col] in (ce.CONTESTED, ce.CONTESTED_ACTIVE), "A on top, B below: the cell is contested"
    assert all(np.isfinite(p.control_m2s) and p.alive_s > 0 for p in rc.players.values())


def test_a_round_without_heights_uses_the_lowest_floor_and_says_so():
    geo = bridge()
    players = {0: ("A", [(0.0, 260, Y, 0)]), 5: ("B", [(0.0, 400, Y, 180)])}     # four values: no z
    rnd = ce.RoundInputs(height_blob(players, t_end=5.0), geo)
    assert rnd.height(0, 1.0) is None
    tk = ce.Tick(rnd, 1.0)
    assert tk.holders[0].cell == node(geo, 260, floor=0), "the lowest floor of their cell"
    assert tk.holders[0].raw.any()
    assert any(key.startswith("approximate heights") for key in rnd.missing)
    with_z = ce.RoundInputs(height_blob({0: still("A", 260, Y, 0, 4.0), 5: still("B", 400, Y, 180, 0.0)}), geo)
    assert not any(key.startswith("approximate heights") for key in with_z.missing)
    assert with_z.height(0, 1.0) == pytest.approx(4.0)


def test_a_flat_map_has_no_heights_in_its_round():
    from tests.replays.control_toys import blob, open_hall

    rnd = ce.RoundInputs(blob({0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])}), open_hall())
    assert rnd.heights == {} and rnd.height(0, 1.0) is None and not rnd.missing.get("approximate heights")
    assert isinstance(topology.of(open_hall()), topology.FlatTopology)


# ---------------------------------------------------------------- Safe: every node of the unknown is a source


def mound():
    """Ground at 0 m. A mound 6 m up at x 136-160 (clear of the hall's walls), and further east a ridge 3 m up across the hall at
    x 200-224. From the low ground west of the ridge nothing east of it is seen; from the mound's top the
    ground well beyond the ridge is."""
    return toy_heights("Mound", [HALL], ground=[((136, 150, 160, 250), 6.0), ((200, 96, 224, 296), 3.0)])


def region(geo, x0, x1):
    """Every node whose centre is west of x1 and east of x0: a piece of unknown with the mound inside."""
    cx = geo.centres[:, 0]
    return geo.walk_n & (cx >= x0) & (cx < x1)


def test_a_raised_node_inside_the_unknown_sees_what_its_boundary_cannot():
    # The reviewer's case (spec, "Unknown and Safe per floor"): Safe is what no node of a team's unknown
    # sees. The unknown here is everything west of x 200, with the mound in its middle. Its boundary is low
    # ground, which the ridge blocks; the mound's top sees over the ridge.
    geo = mound()
    tk = tick(geo, {0: still("A", 400, 280, 0, 0.0), 5: still("B", 120, 120, 0, 0.0)})
    unknown = region(geo, 0, 200)
    nobody = np.zeros(geo.n, bool)
    target = node(geo, 330)
    assert cg.los(geo, (148, Y, 6.0 + 0.7), (330, Y, 0.3)), "the mound's top sees the far ground"
    assert not cg.los(geo, (196, Y, 0.7), (330, Y, 0.3)), "the unknown's edge doesn't"
    boundary, sources = tk.boundary_seen(unknown, nobody)
    assert not sources[node(geo, 148)], "the mound is interior: not a boundary source"
    assert not boundary[target], "so the boundary shortcut misses it"
    assert tk.comp_seen(unknown, nobody)[target], "every node as a source: it is seen, so it is not Safe"
    tk.unknown = {"A": unknown, "B": nobody}
    assert not tk.unknown_safe("A")[target] and tk.unknown_safe("A")[node(geo, 212)] is not None


@pytest.mark.parametrize("make", [bridge, ledge, mound])
def test_every_node_sees_at_least_what_the_boundary_sees_and_exactly_the_union_of_its_nodes(make):
    geo = make()
    tk = tick(geo, {0: still("A", 400, 280, 0, 0.0), 5: still("B", 120, 120, 0, geo.node_z[node(geo, 120, 120)])})
    nobody = np.zeros(geo.n, bool)
    rng = np.random.default_rng(3)
    differs = 0
    for _ in range(6):
        x0 = int(rng.integers(96, 300))
        mask = region(geo, x0, x0 + int(rng.integers(40, 140)))
        full = tk.comp_seen(mask, nobody)
        boundary, _ = tk.boundary_seen(mask, nobody)
        brute = ce.seen_from(geo, np.flatnonzero(mask), [])
        outside = ~mask
        assert (full[outside] == brute[outside]).all(), "the every-node check is the union of every node's view"
        assert not (boundary & ~full).any(), "and never less than the boundary sees"
        differs += int((full & ~boundary & outside).sum())
    if make is mound:
        assert differs > 0, "on the mound the shortcut is wrong, so it is not kept on a map with heights"


def test_a_flat_map_keeps_the_boundary_shortcut():
    from tests.replays.control_toys import blob, open_hall

    geo = open_hall()
    tk = ce.Tick(ce.RoundInputs(blob({0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])}), geo), 1.0)
    mask = geo.walk_n & (geo.centres[:, 0] < 250)
    nobody = np.zeros(geo.n, bool)
    assert (tk.comp_seen(mask, nobody) == tk.boundary_seen(mask, nobody)[0]).all()
    # and there the shortcut is exact: nothing outside the mask is seen from inside it that the boundary misses
    brute = ce.seen_from(geo, np.flatnonzero(mask), [])
    assert (tk.comp_seen(mask, nobody)[~mask] == brute[~mask]).all()


# ---------------------------------------------------------------- trips and watchers

from tests.replays.control_toys import toy_ability, uv, z_dm  # noqa: E402

M = 1 / 0.14     # px per metre


def trip(geo, a, b, za=None, zb=None):
    """The nodes a tripwire from a to b (px) watches; za/zb are the anchors' heights in metres."""
    extra = {} if za is None else {"z": z_dm(za), "end_z": z_dm(zb)}
    wire = toy_ability("Gumshoe", "4_TripWire", a[0], a[1], 0, kind="GameObject", end=list(uv(*b)), **extra)
    rnd = ce.RoundInputs(height_blob({0: still("A", 120, 120, 0, 0.0), 5: still("B", 400, 280, 0, 0.0)},
                                     t_end=5.0, util=[wire]), geo)
    [w] = rnd.watchers
    return set(w.cells.tolist()), rnd


def columns(geo, nodes):
    return sorted({int(geo.node_cell[n]) % GRID for n in nodes})


def test_a_wire_beside_a_step_extends_to_the_step():
    # Ground at 0 m, a step up to 1.5 m at x 256. A wire 0.4 m above the ground from x 204 to x 240 (its east
    # end hangs 2.2 m short of the step): it extends east to the step's face. Its west end has nothing
    # within 10 m but ... the hall's west wall is 14.5 m away: it stays.
    geo = toy_heights("TripStep", [HALL], ground=[((256, 96, 416, 296), 1.5)])
    got, _ = trip(geo, (204, Y), (240, Y), -0.5, -0.5)          # device z: 0.4 m above the ground at -0.9
    assert columns(geo, got) == list(range(25, 32)), "x 204 on to the foot of the step at x 256: columns 25..31"
    assert all(geo.node_z[n] in (0.0, 1.5) for n in got)


def test_on_a_ramp_a_wire_stops_where_the_ground_reaches_it():
    # A 20% ramp climbing east from x 200. A level wire 0.4 m above the ground at its east anchor (x 200):
    # the ground reaches to within TRIP_HIT_M of it 1.5 m on (0.4 - 0.1 = 0.3 m of climb), 2 m with no
    # tolerance: the spec's "stops at 2 m (within a cell)".
    geo = toy_heights("TripRamp", [HALL], slope=(200, 200 + 20 * M, 0.0, 4.0))
    got, _ = trip(geo, (180, Y), (200, Y), -0.5, -0.5)
    east = max(columns(geo, got)) * 8 + 4
    assert 1.0 <= (east - 200) / M <= 2.0 + 1.12, (east - 200) / M
    assert min(columns(geo, got)) == 22, "the west end, on flat ground with no wall in reach, stays at x 180"


def test_a_wire_between_two_walls_or_with_nothing_in_reach_does_not_change():
    walls = toy_heights("TripWalls", [HALL], walls=[(192, 96, 200, 280), (248, 96, 256, 280)],
                        ground=[((0, 0, 1024, 1024), 0.0)])
    got, _ = trip(walls, (202, Y), (246, Y), -0.5, -0.5)
    assert columns(walls, got) == list(range(25, 31)), "x 200-247: it was already wall to wall"
    geo = toy_heights("TripOpen", [(96, 96, 416, 296)], ground=[((0, 0, 1024, 1024), 0.0)])
    got, _ = trip(geo, (230, Y), (270, Y), -0.5, -0.5)
    assert columns(geo, got) == list(range(28, 34)), "nothing within 10 m either way: unchanged"


def test_a_wire_does_not_extend_onto_a_stacked_floor_or_across_unresolved_ground():
    geo = bridge()
    # under the bridge, in the tunnel: it watches the tunnel's nodes, never the bridge's above it
    got, _ = trip(geo, (250, 150), (250, 250), -0.5, -0.5)
    assert all(n < GRID * GRID for n in got) and all(geo.node_z[n] == 0.0 for n in got)
    # on the bridge: the bridge's nodes, never the tunnel's below it
    on_top, _ = trip(geo, (250, 150), (250, 250), 3.5, 3.5)
    assert all(n >= GRID * GRID for n in on_top) and len(on_top) == len(got)
    # a wire on the low ground east of the bridge, pointing at the tunnel's mouth and on into the tunnel: it
    # stays on the ground floor all the way (the bridge above is another floor)
    into, _ = trip(geo, (300, Y), (330, Y), -0.5, -0.5)
    assert all(geo.node_z[n] == 0.0 for n in into)
    # the ledge with an unresolved column at its foot (x 256-264): a wire ending 2 m short of it doesn't extend
    rough = ledge(unresolved=[(256, 96, 264, 296)])
    got, _ = trip(rough, (300, Y), (284, Y), -0.5, -0.5)
    assert min(columns(rough, got)) == 35, "x 284 is column 35: no extension across uncertain terrain"
    clean = ledge()
    got, _ = trip(clean, (300, Y), (284, Y), -0.5, -0.5)
    assert min(columns(clean, got)) == 32, "with the terrain known it extends to the foot of the ledge"


def test_a_wire_with_no_heights_watches_every_floor_under_its_2d_line_and_marks_the_round():
    geo = bridge()
    got, rnd = trip(geo, (250, 150), (250, 250))
    assert {n >= GRID * GRID for n in got} == {True, False}, "both floors, as in 2D"
    assert rnd.missing["approximate heights (a watcher has no z: 2D sight used)"] == 1
    _, with_z = trip(geo, (250, 150), (250, 250), -0.5, -0.5)
    assert "approximate heights (a watcher has no z: 2D sight used)" not in with_z.missing


def camera_view(geo, z_m, t=1.5):
    extra = {} if z_m is None else {"z": z_dm(z_m)}
    cam = toy_ability("Gumshoe", "E_PossessableCamera", 260, Y, 0, kind="Pawn", yaw=0, possessed=[[1.0, 4.0]],
                      yaws=[[0.0, 0]], **extra)
    rnd = ce.RoundInputs(height_blob({0: still("A", 120, 120, 0, 4.0), 5: still("B", 400, 280, 0, 0.0)},
                                     t_end=5.0, util=[cam]), geo)
    return ce.Tick(rnd, t).holders[0].watch, rnd


def test_a_camera_watches_from_its_own_height():
    geo = bridge()
    low, _ = camera_view(geo, -0.9 + 1.5)          # on the tunnel's wall, 1.5 m above the tunnel floor
    high, _ = camera_view(geo, 3.1 + 1.5)          # above the bridge's deck
    assert low[node(geo, 276, floor=0)] and not low[node(geo, 276, floor=1)]
    assert high[node(geo, 276, floor=1)] and not high[node(geo, 276, floor=0)]
    flat, rnd = camera_view(geo, None)
    assert flat[node(geo, 276, floor=0)] and flat[node(geo, 276, floor=1)], "no z: the 2D view, both floors"
    assert rnd.missing["approximate heights (a watcher has no z: 2D sight used)"] == 1


def test_a_drone_watches_from_its_paths_height():
    geo = bridge()

    def view(z_m):
        point = lambda t, x: [t, *uv(x, Y)] + ([] if z_m is None else [z_dm(z_m)])   # noqa: E731
        drone = toy_ability("Hunter", "E_Drone", 300, Y, 0, t=0.0, t1=5.0, kind="Pawn",
                            path=[point(0.0, 320), point(5.0, 300)], possessed=[[0.0, 5.0]], yaws=[[0.0, 180]])
        rnd = ce.RoundInputs(height_blob({0: still("A", 120, 120, 0, 4.0), 5: still("B", 400, 280, 0, 0.0)},
                                         t_end=5.0, util=[drone]), geo)
        return ce.Tick(rnd, 2.0).holders[0].watch, rnd

    low, rnd = view(0.5)                  # flying low, east of the bridge, looking west into the tunnel
    assert low[node(geo, 260, floor=0)] and not low[node(geo, 252, floor=1)]
    assert not any(k.startswith("approximate heights") for k in rnd.missing)
    high, _ = view(6.0)                   # above the bridge: it sees along the deck
    assert high[node(geo, 252, floor=1)]
    flat, rnd = view(None)
    assert flat[node(geo, 252, floor=1)] and flat[node(geo, 252, floor=0)]
    assert rnd.missing["approximate heights (a watcher has no z: 2D sight used)"] == 1


def test_an_alarmbot_holds_its_own_floor():
    geo = bridge()

    def held(z_m):
        bot = toy_ability("Killjoy", "Q_StealthAlarmbot", 260, Y, 0, kind="GameObject", z=z_dm(z_m))
        rnd = ce.RoundInputs(height_blob({0: still("A", 120, 120, 0, 4.0), 5: still("B", 400, 280, 0, 0.0)},
                                         t_end=5.0, util=[bot]), geo)
        return set(rnd.watchers[0].cells.tolist())

    under, on = held(-0.9), held(3.1)
    assert node(geo, 268, floor=0) in under and node(geo, 268, floor=1) not in under
    assert node(geo, 268, floor=1) in on and node(geo, 268, floor=0) not in on
    assert node(geo, 292) in under and node(geo, 292) not in on, "from the bridge it can't walk down to the ground"



def test_the_task_records_the_heights_and_a_round_counts_looks_into_unresolved_terrain():
    from app.control import task as ct

    geo = ledge(unresolved=[(256, 96, 264, 296)])
    assert ct.geometry_used(geo)["height"] == geo.heights.digest
    from tests.replays.control_toys import open_hall

    assert "height" not in ct.geometry_used(open_hall()), "a flat map's inputs are what they were"
    players = {0: still("A", 200, Y, 0, 4.0), 5: still("B", 380, 280, 180, 0.0)}
    rc_ = ce.compute_round(height_blob(players, t_end=3.0), geo)
    assert rc_.missing_inputs.get("ticks looking through unresolved terrain (2D sight there)", 0) > 0
    clean = ce.compute_round(height_blob(players, t_end=3.0), ledge())
    assert "ticks looking through unresolved terrain (2D sight there)" not in clean.missing_inputs
