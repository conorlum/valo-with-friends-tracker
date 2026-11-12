import json

from app.coach import areas, areas_draft
from tests.coach.test_areas import NAMES, painted_object
from tests.replays.test_map_feature_tagger import run_node


def map_data(grid, walk, *, draft=None, floors=None, existing=None, name="Toy"):
    return {"map": name, "grid": grid, "walk": areas_draft.encode_cells(int(i in walk) for i in range(grid ** 2)),
            "labels": [], "draft": draft or {"names": NAMES, "cells": [[0, grid ** 2]]},
            "existing": existing, "floors": floors or {}}


def test_paint_erase_and_fill_respect_walls_and_blocked_diagonals():
    walk = {0, 1, 3, 4, 7, 9, 10, 14}
    got = run_node('''function run(p) {
      const a = T.Areas.fromData({areas: p});
      const painted = a.paint([0, 2], 2);
      const filled = a.fill(1, 3);
      const cleared = a.erase([0]);
      return {painted, filled, cleared, cells: a.cells};
    }''', map_data(6, walk))
    assert (got["painted"], got["filled"], got["cleared"]) == (1, 2, 1)
    assert got["cells"][1] == got["cells"][7] == 3
    assert all(got["cells"][i] == 0 for i in (0, 2, 3, 4, 9, 10, 14))


def test_name_edits_refuse_cycles_and_depth_four_then_merge_cells_and_bands():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      const branch = a.addName("Arch", 1), leaf = a.addName("Nook", branch);
      const cycle = a.setParent(1, leaf), depth = a.setParent(branch, 2);
      const addedTooDeep = a.addName("Beyond", leaf);
      const renamed = a.rename(branch, "Stairs");
      a.paint([0, 1], branch);
      a.paint([1], branch, 1);
      a.paint([1], leaf, 0);
      const merged = a.merge(branch, 2);
      return {branch, leaf, cycle, depth, addedTooDeep, renamed, merged, cells: a.cells, bands: a.bands,
              names: a.names};
    }''', map_data(3, set(range(9)), floors={"1": [[-20, 0], [20, 40]]}))
    assert (got["branch"], got["leaf"]) == (4, 5)
    assert (got["cycle"], got["depth"], got["addedTooDeep"]) == (False, False, None)
    assert got["renamed"] and got["merged"]
    assert got["cells"][:2] == [2, 5]
    assert got["bands"] == {"1": [[-20, 0, 5], [20, 40, 2]]}
    assert [(row["id"], row["parent"]) for row in got["names"]] == [(1, None), (2, 1), (3, None), (5, 2)]


def test_auto_supers_match_python_and_a_confirmed_root_updates_its_children():
    names = [
        {"id": 1, "name": "B Main", "parent": None, "super": "A", "super_source": "auto"},
        {"id": 2, "name": "Corner", "parent": 1, "super": "A", "super_source": "auto"},
        {"id": 3, "name": "Mid", "parent": None, "super": "A", "super_source": "auto"},
        {"id": 4, "name": "Ramp", "parent": None, "super": "C", "super_source": "set"},
        {"id": 5, "name": "Nook", "parent": 4, "super": "C", "super_source": "auto"},
    ]
    cells = [1, 1, 2, 2, 2, 3, 4, 5, 0]
    draft_ids = [12, 11, 12, 12, 12, 11, 11, 11, 0]
    draft_names = [
        {"id": 11, "name": "A Main", "parent": None, "super": "A", "super_source": "auto"},
        {"id": 12, "name": "B Main", "parent": None, "super": "B", "super_source": "auto"},
    ]
    draft = {"names": draft_names, "cells": areas_draft.encode_cells(draft_ids)}
    existing = {"names": names, "cells": areas_draft.encode_cells(cells), "bands": {}}
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      const auto = JSON.parse(JSON.stringify(a.autoSupers()));
      const before = a.stillAuto();
      const set = a.setSuper(2, "C"), confirmed = a.confirmSuper(3);
      return {auto, before, set, confirmed, after: a.stillAuto(), names: a.names};
    }''', map_data(3, set(range(9)), draft=draft, existing=existing))
    expected = areas_draft.auto_super(names, cells, [{11: "A", 12: "B", 0: None}[i] for i in draft_ids])
    assert got["auto"] == expected
    assert (got["before"], got["after"]) == (3, 0)
    assert got["set"] and got["confirmed"]
    assert [(row["super"], row["super_source"]) for row in got["names"][:3]] == [
        ("C", "set"), ("C", "set"), ("Mid", "set"),
    ]


def test_auto_supers_include_upper_only_areas_and_vote_once_per_cell():
    names = [
        {"id": 1, "name": "Mid", "parent": None, "super": "Mid", "super_source": "auto"},
        {"id": 2, "name": "Lift", "parent": None, "super": "A", "super_source": "auto"},
        {"id": 3, "name": "Lift Edge", "parent": 2, "super": "A", "super_source": "auto"},
        {"id": 4, "name": "Tower", "parent": None, "super": "C", "super_source": "auto"},
        {"id": 5, "name": "Tower Nook", "parent": 4, "super": "C", "super_source": "auto"},
    ]
    cells = [1, 1, 1, 0]
    bands = {"0": [[0, 10, 1], [20, 30, 3]], "1": [[0, 10, 1], [20, 30, 4]],
             "2": [[0, 10, 1], [20, 30, 4], [40, 50, 5]]}
    draft = {"names": [{**names[1], "id": 11, "name": "A Main"}, {**names[1], "id": 12, "name": "B Main", "super": "B"}],
             "cells": [[12, 1], [11, 1], [12, 1], [0, 1]]}
    existing = {"names": names, "cells": areas_draft.encode_cells(cells), "bands": bands}
    got = run_node('''function run(p) { return T.Areas.fromData(p).autoSupers(); }''',
                   map_data(2, {0, 1, 2}, draft=draft, existing=existing))
    assert [row["super"] for row in got] == ["Mid", "B", "B", "A", "A"]
    assert got == areas_draft.auto_super(names, cells, ["B", "A", "B", None], bands=bands)


def test_pins_use_the_painted_path_on_the_observed_floor_and_export_only_case_fields():
    pin = {"map": "Toy", "u": 1000, "v": 1000, "z": 75, "area": "Mid", "match": "example",
           "player": "synthetic"}
    pins = [pin, {**pin, "z": None}, {**pin, "z": 10}, {**pin, "map": "Other"}]
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data);
      const before = a.pinStatus(p.pins).map(r => r.ok);
      a.paint([0], 3);
      a.paint([0], 2, [60, 90]);
      const painted = a.pinStatus(p.pins).map(r => r.ok);
      const fixture = a.fixtureFromPins(p.pins);
      a.paint([0], 2, 0);
      return {before, painted, fixture, same: a.pinStatus(p.pins).map(r => r.ok), bands: a.bands};
    }''', {"data": map_data(3, {0}, floors={"0": [[0, 30], [60, 90]]}), "pins": pins})
    assert got["before"] == [False, False, False, False]
    assert got["painted"] == [True, False, False, False]
    assert got["fixture"] == [{key: pin[key] for key in ("map", "u", "v", "z", "area")}]
    assert got["same"] == [True, True, True, False]
    assert got["bands"] == {}


def test_similarity_and_affine_transforms_map_reference_points_exactly():
    similarity = [[[0, 0], [4, -5]], [[3, 0], [4, 1]]]
    affine = [[[1, 1], [7, 6]], [[4, 1], [13, 3]], [[1, 5], [11, 18]]]
    got = run_node('''function run(p) {
      return {similar: T.Areas.solveAlign(p.similarity), affine: T.Areas.solveAlign(p.affine),
              degenerate: T.Areas.solveAlign([p.affine[0], p.affine[0], p.affine[2]])};
    }''', {"similarity": similarity, "affine": affine})
    assert got["similar"] == [[0, -2, 4], [2, 0, -5]]
    assert got["affine"] == [[2, 1, 4], [-1, 3, 4]]
    assert got["degenerate"] is None


def test_export_text_matches_python_and_retains_the_saved_world_bands():
    names = [NAMES[2], {**NAMES[1], "name": "Marché"}, NAMES[0]]
    existing = painted_object({0: 1, 10: 3}, names=names, bands={"0": [[60, 90, 2], [0, 30, 1]]})
    existing["reviewed"] = False
    data = map_data(128, {0, 10}, floors={"0": [[-2, 20], [58, 82]]}, existing=existing, name="Sunset")
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      return [a.exportText(), a.exportText(), a.floorBands(0)];
    }''', data)
    expected = areas.dumps({**existing, "reviewed": True})
    assert got[:2] == [expected, expected]
    assert got[2] == [[0, 30], [60, 90]]
    assert areas.validate(json.loads(got[0])) == []
