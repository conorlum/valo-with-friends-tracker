"""Chokes (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 2): narrow passages found in a
map's walkable grid, kept per map, with hand edits surviving a re-run."""

import numpy as np

from app.control import chokes as ck
from app.replays import choke_assets as ca
from tests.replays.control_toys import door_hall, midwall_hall, open_hall, two_rooms, uv


def test_an_open_hall_has_no_chokes():
    assert ck.detect(open_hall()) == []


def test_a_one_cell_door_is_one_choke_across_it():
    geo = door_hall()
    found = ck.detect(geo)
    assert len(found) == 1
    door = geo.cell_of_px(208, 292)          # the door: the gap under the wall's south end
    assert door in found[0]


def test_a_wall_corner_is_not_a_choke():
    """The door hall's only choke is the door: its wall corners give no diagonal pinch (R3)."""
    geo = door_hall()
    found = ck.detect(geo)
    assert len(found) == 1
    from app.control.geometry import GRID
    rows = {c // GRID for c in found[0]}
    cols = {c % GRID for c in found[0]}
    # one column wide (the door's) or one row (the line across it); never a diagonal sprawl
    assert len(rows) == 1 or len(cols) == 1


def test_the_passage_under_a_wall_is_a_choke():
    geo = midwall_hall()
    found = ck.detect(geo)
    under = geo.cell_of_px(256, 272)
    assert any(under in cells for cells in found)


def test_a_special_link_is_a_choke_of_its_two_ends():
    geo = two_rooms([{"kind": "drop", "a": list(uv(170, 170)), "b": list(uv(310, 170)), "one_way": True}])
    found = ck.detect(geo)
    a, b = geo.cell_of_px(170, 170), geo.cell_of_px(310, 170)
    assert sorted([a, b]) in found


def test_merge_keeps_hand_edits_and_tombstones():
    hand = ca.Choke(1, "A Main", [10, 11], source="hand")
    gone = ca.Choke(2, "2", [50, 51], source="auto", deleted=True)
    old_auto = ca.Choke(3, "3", [90, 91], source="auto")
    merged, next_id = ca.merge([hand, gone, old_auto], [[10, 11], [50, 51], [200, 201]], 4)
    by_id = {c.id: c for c in merged}
    assert by_id[1].name == "A Main" and by_id[1].source == "hand", "a hand choke is kept as it is"
    assert by_id[2].deleted, "a deleted choke stays deleted"
    assert 3 not in by_id, "an auto choke detection no longer finds is dropped"
    new = [c for c in merged if c.cells == [200, 201]]
    assert len(new) == 1 and new[0].id == 4 and new[0].name == "4", "new ids continue from the high-water mark"
    assert next_id == 5
    assert not any(c.cells == [10, 11] and c.source == "auto" for c in merged), "no duplicate of a hand choke"
    assert not any(c.cells == [50, 51] and not c.deleted for c in merged), "a tombstone suppresses re-detection"


def test_identical_detection_twice_keeps_ids_and_names():
    first, nid = ca.merge([], [[1, 2], [30, 31]], 1)
    first[0].name = "renamed"
    second, nid2 = ca.merge(first, [[1, 2], [30, 31]], nid)
    assert [(c.id, c.name, c.cells) for c in second] == [(c.id, c.name, c.cells) for c in first]
    assert nid2 == nid


def test_dropping_every_choke_then_adding_one_gets_a_fresh_id():
    first, nid = ca.merge([], [[1, 2], [30, 31]], 1)
    assert [c.id for c in first] == [1, 2] and nid == 3
    empty, nid = ca.merge(first, [], nid)
    assert empty == [] and nid == 3, "the high-water mark survives an empty result"
    again, nid = ca.merge(empty, [[70, 71]], nid)
    assert [c.id for c in again] == [3], "an id is never reused"


def test_save_load_and_hash(tmp_path):
    chokes = [ca.Choke(1, "1", [5, 6])]
    ca.save("Toy", chokes, 7, tmp_path)
    assert ca.load("Toy", tmp_path) == chokes
    assert ca.load_next_id("Toy", tmp_path) == 7
    assert ca.load_next_id("None", tmp_path) == 1
    h = ca.asset_hash("Toy", tmp_path)
    assert len(h) == 16
    ca.save("Toy", [ca.Choke(1, "Door", [5, 6])], 7, tmp_path)
    assert ca.asset_hash("Toy", tmp_path) != h, "renaming a choke changes the hash"
    assert ca.load("None", tmp_path) is None and ca.asset_hash("None", tmp_path) is None


def test_the_asset_folder_is_the_control_masks_folder():
    from app.control import geometry
    assert ca.ASSET_DIR.resolve() == geometry.ASSET_DIR.resolve()


def test_node_chokes_marks_cells_and_skips_deleted():
    geo = open_hall()
    out = ck.node_chokes(geo, [ca.Choke(1, "1", [5, 6]), ca.Choke(2, "2", [7], deleted=True)])
    assert out.dtype == np.int32 and len(out) == geo.n
    assert out[5] == 1 and out[6] == 1 and out[7] == -1 and out[8] == -1
    assert (ck.node_chokes(geo, None) == -1).all()
