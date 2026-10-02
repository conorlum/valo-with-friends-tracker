"""Sight with heights on toy maps, with exact geometry
(docs/superpowers/specs/2026-10-01-control-heights-design.md, part 4, "The ray test"; Testing, "Sight").

Heights here are position-z in metres (heights.py): a floor at z is a player standing with their position
at z, their feet STAND_M below it, their eye EYE_M above it and their body point BODY_M above it. So the
spec's "eye 1.6 m above the feet" is z + 0.7, and "a body 1.2 m above the floor" is z + 0.3.
The toys are Ascent's scale: 0.14 m a pixel, 1.12 m a cell."""

import numpy as np
import pytest

from app.control import geometry as cg
from app.control import heights as hc
from tests.replays.control_toys import HALL, door_hall, open_hall, toy_heights

M = 1 / 0.14            # pixels per metre
Y = 204                 # a row through the middle of the hall (cell row 25)
EAST, WEST = np.array([0.0]), np.array([180.0])
FAN = np.arange(-3, 3.01, 0.5)


def node(geo, x, y=Y, floor=0):
    return int(geo.node_of[geo.cell_of_px(x, y), floor])


def eye(z_m: float) -> float:
    return z_m + hc.EYE_M


def body(z_m: float) -> float:
    return z_m + hc.BODY_M


def ledge():
    """The west of the hall (x < 256) is 4 m up; the east is at 0 m. The drop is the cell edge at x 256."""
    return toy_heights("Ledge", [HALL], ground=[((96, 96, 256, 296), 4.0)])


# ---------------------------------------------------------------- the spec's two ledge cases


def test_down_from_a_ledge_the_ground_just_below_is_hidden_and_the_far_ground_is_seen():
    # Eye 5.6 m above the lower ground (upper floor 4 m + 1.6 m), the ledge 2 m in front of the viewer:
    # a body 1.2 m above the lower floor is hidden at 3 m and seen at 10 m.
    geo = ledge()
    x = 256 - 2 * M
    seen = cg.cast(geo, x, Y, EAST + FAN, [], eye_z=eye(4.0))
    near, far = node(geo, x + 3 * M), node(geo, x + 10 * M)
    assert not seen[near] and seen[far]
    assert not cg.los(geo, (x, Y, eye(4.0)), (x + 3 * M, Y, body(0.0)))
    assert cg.los(geo, (x, Y, eye(4.0)), (x + 10 * M, Y, body(0.0)))
    assert seen[node(geo, x - 30)] is np.False_, "nothing behind a 6-degree fan"
    assert seen[node(geo, 250)], "the upper floor up to the edge"
    flat2d = cg.cast(geo, x, Y, EAST + FAN, [])
    assert flat2d[near] and flat2d[far], "in 2D both are seen: heights only ever remove sight"
    assert not (seen & ~flat2d).any()


def test_up_at_a_ledge_a_body_at_the_edge_is_seen_and_one_far_back_is_hidden():
    # Eye 1.6 m up on the lower floor, the ledge face 5 m away, the upper floor 4 m up: a body on the
    # upper floor 1 m past the edge is seen, one 15 m past it is hidden.
    geo = ledge()
    x = 256 + 5 * M
    seen = cg.cast(geo, x, Y, WEST + FAN, [], eye_z=eye(0.0))
    at_edge, far_back = node(geo, 256 - 1 * M), node(geo, 256 - 15 * M)
    assert seen[at_edge] and not seen[far_back]
    assert cg.los(geo, (x, Y, eye(0.0)), (256 - 1 * M, Y, body(4.0)))
    assert not cg.los(geo, (x, Y, eye(0.0)), (256 - 15 * M, Y, body(4.0)))
    assert seen[node(geo, 270)], "the lower floor between them"


def test_a_flat_floor_never_hides_itself():
    # Heights that are all one level change nothing: the height test agrees with 2D everywhere, on an
    # open hall and behind a door (the 2D corner tolerance is the same with heights).
    for flat_geo, name, walls in ((open_hall(), "FlatOpen", ()), (door_hall(), "FlatDoor", [(200, 96, 216, 288)])):
        geo = toy_heights(name, [HALL], walls=walls, ground=[((0, 0, 1024, 1024), 2.5)])
        assert geo.n == cg.GRID * cg.GRID and geo.heights is not None
        for x, y in ((150, 150), (300, 250), (190, 292), (228, 290)):
            angles = np.arange(0, 360, cg.RAY_STEP_DEG)
            with_heights = cg.cast(geo, x, y, angles, [], eye_z=eye(2.5))
            assert (with_heights == cg.cast(flat_geo, x, y, angles, [])).all(), (name, x, y)
        assert (np.unpackbits(geo.rows, axis=1)[:, : geo.n] == np.unpackbits(flat_geo.rows, axis=1)).all()


# ---------------------------------------------------------------- ramps


def ramp():
    """The ground climbs 4 m between x 160 and x 320 (22.4 m: an 18% ramp), flat either side."""
    return toy_heights("Ramp", [HALL], slope=(160, 320, 0.0, 4.0))


def z_at(geo, x, y=Y):
    return float(geo.node_z[geo.cell_of_px(x, y)])


@pytest.mark.parametrize("x", [120, 164, 240, 316, 380])
def test_a_continuous_ramp_hides_nothing_on_itself(x):
    geo = ramp()
    seen = cg.cast(geo, x, Y, np.r_[EAST + FAN, WEST + FAN], [], eye_z=eye(z_at(geo, x)))
    row = [node(geo, cx) for cx in range(100, 412, 8)]
    assert all(seen[n] for n in row), "from the bottom, the middle and the top, up it and down it"
    assert (seen == cg.cast(geo, x, Y, np.r_[EAST + FAN, WEST + FAN], [])).all()


# ---------------------------------------------------------------- a tunnel under a slab


def bridge():
    """A plateau 4 m up west of x 240. From it a bridge runs east to x 288 (six cells), 4 m up, over
    ground at 0 m: those cells have two floors. East of the bridge everything is at 0 m."""
    return toy_heights("Bridge", [HALL], ground=[((96, 96, 240, 296), 4.0)], upper=[((240, 96, 288, 296), 4.0)])


def test_the_bridge_has_two_floors_and_the_node_ids_keep_the_cells():
    geo = bridge()
    cells = geo.heights.floor_count().ravel()
    assert geo.n == cg.GRID * cg.GRID + int((cells == 2).sum()) and (cells == 2).sum() == 6 * 25
    under, on = node(geo, 260, floor=0), node(geo, 260, floor=1)
    assert under == geo.cell_of_px(260, Y) and on >= cg.GRID * cg.GRID
    assert (geo.node_z[under], geo.node_z[on]) == (0.0, 4.0) and geo.node_cell[on] == under
    assert tuple(geo.centres[on]) == tuple(geo.centres[under])
    assert geo.node_at(under, 0.1) == under and geo.node_at(under, 3.8) == on and geo.node_at(under, 9.0) == on
    assert geo.node_at(under, -5.0) == under, "below every floor: the lowest"
    assert geo.walk_n[on] and geo.walk_n.sum() == geo.walk.sum() + 150


def test_the_tunnel_and_the_bridge_see_along_their_own_level_and_not_each_other():
    geo = bridge()
    # in the tunnel, under the middle of the bridge, looking both ways along it
    tunnel = cg.cast(geo, 260, Y, np.r_[EAST + FAN, WEST + FAN], [], eye_z=eye(0.0), own=node(geo, 260, floor=0))
    assert tunnel[node(geo, 244, floor=0)] and tunnel[node(geo, 284, floor=0)] and tunnel[node(geo, 350)]
    assert not any(tunnel[node(geo, x, floor=1)] for x in range(244, 288, 8)), "not the floor above"
    assert tunnel[node(geo, 260, floor=0)] and not tunnel[node(geo, 260, floor=1)], "nor through its own roof"
    # on the bridge, above the same spot
    top = cg.cast(geo, 260, Y, np.r_[EAST + FAN, WEST + FAN], [], eye_z=eye(4.0), own=node(geo, 260, floor=1))
    assert top[node(geo, 244, floor=1)] and top[node(geo, 284, floor=1)] and top[node(geo, 200)]
    assert not any(top[node(geo, x, floor=0)] for x in range(244, 288, 8)), "not into the tunnel"
    assert top[node(geo, 400)], "but the ground beyond the bridge's end, over its edge"
    for x in range(244, 288, 8):
        assert not cg.los(geo, (260, Y, eye(0.0)), (x + 0.5, Y, body(4.0))), x
        assert not cg.los(geo, (260, Y, eye(4.0)), (x + 0.5, Y, body(0.0))), x
    assert cg.los(geo, (260, Y, eye(0.0)), (350, Y, body(0.0))) and cg.los(geo, (260, Y, eye(4.0)), (200, Y, body(4.0)))


def test_a_slab_blocks_a_line_only_inside_its_own_cells_and_the_same_both_ways():
    geo = bridge()      # the deck's last cell is x 280-288; its plate is at 3.1 m
    low, high = (287.5, Y, 0.7), (290.0, Y, 4.3)       # crosses 3.1 m at x 289.2, past the deck's edge
    assert cg.los(geo, low, high) and cg.los(geo, high, low)
    low, high = (281.0, Y, 0.7), (287.0, Y, 4.3)       # crosses it at x 285, inside the deck
    assert not cg.los(geo, low, high) and not cg.los(geo, high, low)


def test_straight_up_or_down_a_slab_is_in_the_way():
    geo = bridge()
    assert not cg.los(geo, (260, Y, 0.7), (260, Y, 4.3)) and not cg.los(geo, (260, Y, 4.3), (260, Y, 0.7))
    assert cg.los(geo, (260, Y, 0.2), (260, Y, 2.0)), "both under the deck"
    assert cg.los(geo, (350, Y, 0.7), (350, Y, 4.3)), "no deck here"


def test_from_outside_the_bridge_hides_what_is_on_it_past_its_edge_and_shows_the_tunnel():
    geo = bridge()
    seen = cg.cast(geo, 330, Y, WEST + FAN, [], eye_z=eye(0.0))
    assert seen[node(geo, 284, floor=1)], "a body at the bridge's edge, seen from below"
    assert not seen[node(geo, 244, floor=1)], "one further back on it is behind the deck"
    assert all(seen[node(geo, x, floor=0)] for x in range(244, 288, 8)), "the tunnel, all the way in"
    assert not seen[node(geo, 200)], "the plateau behind the tunnel's end wall is hidden by its own face"


# ---------------------------------------------------------------- unresolved terrain


def test_a_ray_into_an_unresolved_cell_gives_the_2d_answer_and_is_recorded():
    # The ledge again, with an unresolved strip (x 264-272) just below it. A ray that crosses the strip is
    # 2D from there on: the ground the ledge would hide is seen, as it is today.
    geo = toy_heights("LedgeUnresolved", [HALL], ground=[((96, 96, 256, 296), 4.0)], unresolved=[(264, 96, 272, 296)])
    assert geo.unresolved[geo.cell_of_px(268, Y)] and np.isnan(geo.node_z[geo.cell_of_px(268, Y)])
    x = 256 - 2 * M
    record = {}
    seen = cg.cast(geo, x, Y, EAST + FAN, [], eye_z=eye(4.0), record=record)
    assert not seen[node(geo, 260)], "before the strip the height test still holds"
    assert seen[node(geo, 268)] and seen[node(geo, 276)] and seen[node(geo, 284)], "from the strip on: 2D"
    assert record["unresolved_rays"] == len(FAN)
    clean = {}
    cg.cast(ledge(), x, Y, EAST + FAN, [], eye_z=eye(4.0), record=clean)
    assert clean == {}
    record = {}
    assert cg.los(geo, (x, Y, eye(4.0)), (280, Y, body(0.0)), record=record) and record["unresolved_rays"] == 1
    assert not cg.los(geo, (x, Y, eye(4.0)), (260, Y, body(0.0)))


# ---------------------------------------------------------------- cast and los check each other


@pytest.mark.parametrize("make", [ledge, ramp, bridge])
def test_what_a_single_line_sees_the_rays_see_too(make):
    # `los` is one exact line; `cast` is a fan of rays over cells. Wherever the line from a standing eye to
    # a body at a cell's centre is clear, the 360-degree rays from that eye must see the cell's node.
    geo = make()
    rng = np.random.default_rng(7)
    walkable = np.flatnonzero(geo.walk_n)
    for src in rng.choice(walkable, 12, replace=False):
        sx, sy = geo.centres[src]
        seen = cg.cast(geo, float(sx), float(sy), np.arange(0, 360, cg.RAY_STEP_DEG), [],
                       eye_z=eye(geo.node_z[src]), own=int(src))
        for dst in rng.choice(walkable, 60, replace=False):
            if geo.node_cell[dst] == geo.node_cell[src]:
                continue
            tx, ty = geo.centres[dst]
            if cg.los(geo, (float(sx), float(sy), eye(geo.node_z[src])), (float(tx), float(ty), body(geo.node_z[dst]))):
                assert seen[dst], (make.__name__, int(src), int(dst))


def test_the_visibility_rows_are_per_node_and_their_cache_key_takes_the_heights():
    geo, flat = bridge(), open_hall()
    assert geo.rows.shape == (int(geo.walk_n.sum()), (geo.n + 7) // 8) and len(geo.row_of) == geo.n
    on, under = node(geo, 260, floor=1), node(geo, 260, floor=0)
    row = lambda n: np.unpackbits(geo.rows[geo.row_of[n]])[: geo.n].astype(bool)   # noqa: E731
    assert row(on)[node(geo, 200)] and not row(under)[node(geo, 200)]
    assert row(under)[node(geo, 350)] and not row(under)[on] and not row(on)[under]
    assert cg.cache_key(geo) != cg.cache_key(flat) and cg.cache_key(geo) != cg.cache_key(ledge())


def test_a_flat_maps_cast_rows_and_cache_key_are_what_they_were():
    geo = open_hall()
    assert geo.heights is None and geo.n == cg.GRID * cg.GRID and geo.rows.shape[1] == cg.GRID * cg.GRID // 8
    angles = np.arange(0, 360, cg.RAY_STEP_DEG)
    same = cg.cast(geo, 200, 200, angles, [], eye_z=3.0) == cg.cast(geo, 200, 200, angles, [])
    assert same.all(), "an eye height means nothing without heights"
    assert cg.cast(geo, 200, 200, angles, []).shape == (cg.GRID * cg.GRID,)
    # pinned: the key of the open hall before heights existed (a changed key rebuilds every map's bitsets)
    assert cg.cache_key(geo)[:16] == "4155a82bc6ac462f"
    assert cg.los(geo, (150, 150, None), (300, 250, None)) and not cg.los(door_hall(), (150, 150, None), (300, 150, None))


def test_load_geometry_takes_a_preview_asset_and_checks_a_committed_ones_digest(tmp_path):
    import json

    flat = open_hall()
    geo = bridge()
    for name in ("sight", "walk"):
        cg.write_mask_png(tmp_path / f"Ascent.{name}.png", flat.sight if name == "sight" else flat.walk_px)
    (tmp_path / "tags.json").write_text(json.dumps({"maps": {}}), encoding="utf-8")
    hc.save_asset(tmp_path / "Ascent.height.npz", geo.heights)
    assert cg.load_geometry("Ascent", tmp_path).heights is None, "no index entry: flat"
    preview = cg.load_geometry("Ascent", tmp_path, heights=tmp_path / "Ascent.height.npz")
    assert preview.n == geo.n and preview.height_sha == geo.heights.digest
    (tmp_path / "index.json").write_text(json.dumps({"maps": {"Ascent": {"height_sha": geo.heights.digest}}}),
                                         encoding="utf-8")
    assert cg.load_geometry("Ascent", tmp_path).n == geo.n
    (tmp_path / "index.json").write_text(json.dumps({"maps": {"Ascent": {"height_sha": "0" * 12}}}), encoding="utf-8")
    with pytest.raises(cg.GeometryError):
        cg.load_geometry("Ascent", tmp_path)


def test_a_walkable_cell_the_asset_does_not_know_is_unresolved_and_counted():
    import copy

    geo = bridge()
    wider = copy.copy(toy_heights("Bridge-flat-wide", [(96, 96, 424, 296)], ground=[((0, 0, 1024, 1024), 0.0)]))
    assert geo.height_unknown == 0
    fresh = cg.geometry_from_masks("Wide", wider.sight, wider.walk_px, 7e-5)
    cg.attach_heights(fresh, geo.heights)      # the walk mask grew by one column since the heights were built
    assert fresh.height_unknown == 25 and fresh.unresolved.sum() == 25
    assert np.isnan(fresh.node_z[fresh.cell_of_px(420, Y)])
