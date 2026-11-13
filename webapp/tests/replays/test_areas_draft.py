import numpy as np

from app.coach import areas_draft
from app.control import geometry as cg
from app.control import heights as hc
from scripts import control_tagger
from tests.coach.test_areas import painted_object, write_paint


def label(name, region, x, y, grid):
    return {"name": name, "super": region, "u": (x + 0.5) * 10000 / grid, "v": (y + 0.5) * 10000 / grid}


def expand(runs):
    return [value for value, length in runs for _ in range(length)]


def test_walk_distance_respects_the_wall_between_labels():
    grid = 6
    walk = {y * grid + x for y in range(grid) for x in range(grid) if x != 3}
    labels = [label("A Main", "A", 0, 0, grid), label("B Main", "B", 4, 5, grid)]
    got = areas_draft.draft("Toy", walk, labels, grid=grid)
    cells = expand(got["cells"])
    assert cells[5 * grid + 2] == 1  # closer to B Main in a straight line, but across the wall
    assert cells[5 * grid + 3:5 * grid + 5] == [0, 2]


def test_a_label_on_a_wall_moves_to_the_nearest_walk_cell_and_leaves_an_island_unpainted():
    grid = 6
    bitmap = np.zeros((grid, grid), dtype=bool)
    bitmap.flat[[0, 1, 35]] = True
    labels = [label("A Main", "A", 2, 0, grid)]
    got = areas_draft.draft("Toy", bitmap, labels, grid=grid)
    assert got == areas_draft.draft("Toy", {35, 1, 0}, labels, grid=grid)
    assert got["cells"] == [[1, 2], [0, 34]]


def test_diagonal_steps_cost_one_and_cannot_cross_a_wall_corner():
    grid = 8
    walk = set(range(grid * grid))
    labels = [label("Zig", "A", 1, 1, grid), label("Axis", "B", 0, 5, grid)]
    got = areas_draft.draft("Toy", walk, labels, grid=grid)
    assert [n["name"] for n in got["names"]] == ["Axis", "Zig"]  # ID order is independent of label order
    assert expand(got["cells"])[5 * grid + 5] == 2  # four diagonal steps vs five straight steps
    blocked = areas_draft.draft("Toy", {0, grid + 1}, [label("A Main", "A", 0, 0, grid)], grid=grid)
    assert blocked["cells"] == [[1, 1], [0, 63]]


def test_equal_step_distance_prefers_the_first_label_not_its_name_id():
    grid = 6
    labels = [label("Zig", "A", 0, 0, grid), label("Axis", "B", 2, 0, grid)]
    got = areas_draft.draft("Toy", set(range(grid * grid)), labels, grid=grid)
    assert expand(got["cells"])[1] == 2


def test_auto_super_uses_the_roots_name_or_its_cells_and_children_inherit():
    names = [
        {"id": 1, "name": "B Main", "parent": None, "super": "A", "super_source": "auto"},
        {"id": 2, "name": "Corner", "parent": 1, "super": "A", "super_source": "auto"},
        {"id": 3, "name": "Mid", "parent": None, "super": "A", "super_source": "auto"},
        {"id": 4, "name": "Ramp", "parent": None, "super": "C", "super_source": "set"},
        {"id": 5, "name": "Nook", "parent": 4, "super": "C", "super_source": "auto"},
    ]
    got = areas_draft.auto_super(names, [1, 1, 2, 2, 2, 3, 4, 5], ["B", "A", "B", "B", "B", "A", "A", "A"])
    assert [(r["super"], r["super_source"]) for r in got] == [
        ("B", "auto"), ("B", "auto"), ("Mid", "auto"), ("C", "set"), ("C", "set"),
    ]
    assert names[0]["super"] == "A"  # the draft input is reusable


def test_sunset_area_data_uses_the_committed_walk_mask(tmp_path):
    got = control_tagger.areas_data("Sunset", None, areas_dir=tmp_path)
    walk = cg.load_base_geometry("Sunset").walk.ravel()
    assert got["map"] == "Sunset" and got["grid"] == cg.GRID and len(got["labels"]) == 17
    assert expand(got["walk"]) == walk.astype(int).tolist()
    cells = np.asarray(expand(got["draft"]["cells"]))
    assert 0 < (cells != 0).sum() <= walk.sum()
    assert not cells[~walk].any()
    assert got["existing"] is None and got["floors"] == {}


def test_area_data_loads_reviewed_and_unreviewed_editor_files(tmp_path):
    obj = painted_object({0: 1})
    for reviewed in (True, False):
        saved = obj | {"reviewed": reviewed}
        write_paint(tmp_path, saved)
        assert control_tagger.areas_data("Sunset", None, areas_dir=tmp_path)["existing"] == saved


def test_area_floor_bands_use_world_decimetres_from_an_export(tmp_path):
    walk = cg.load_base_geometry("Sunset").walk
    lower, single = np.flatnonzero(walk)[:2]
    floors = np.full((*walk.shape, hc.MAX_FLOORS), -1, np.int16)
    floors.reshape(-1, hc.MAX_FLOORS)[lower, :2] = [0, 40]
    floors.reshape(-1, hc.MAX_FLOORS)[single, 0] = 20
    asset = hc.HeightAsset(floors, np.zeros_like(floors), floors[..., 0] >= 0, np.zeros_like(walk),
                           np.zeros((0, 5), np.int32), {"origin_z": -130})
    hc.save_asset(tmp_path / "Sunset.height.npz", asset)
    got = control_tagger.areas_data("Sunset", tmp_path, areas_dir=tmp_path)
    assert got["floors"] == {str(lower): [[-140, -120], [-100, -80]]}
