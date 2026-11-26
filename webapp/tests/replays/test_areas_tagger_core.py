import json

import pytest

from app.coach import areas, areas_draft
from tests.coach.test_areas import NAMES, painted_object
from tests.replays.test_map_feature_tagger import run_node


def map_data(grid, walk, *, draft=None, floors=None, existing=None, name="Toy"):
    return {"map": name, "grid": grid, "walk": areas_draft.encode_cells(int(i in walk) for i in range(grid ** 2)),
            "labels": [], "draft": draft or {"names": NAMES, "cells": [[0, grid ** 2]]},
            "existing": existing, "floors": floors or {}}


def test_minimum_separation_floor_bands_have_one_boundary_owner():
    import numpy as np
    from types import SimpleNamespace
    from app.control import heights as hc
    from scripts import control_tagger

    separation = int(hc.FLOOR_SEP_M * 10)
    floors = np.full((1, hc.MAX_FLOORS), -1, dtype=np.int32)
    floors[0, :2] = [100, 100 + separation]
    asset = SimpleNamespace(floors=floors, floor_count=lambda: np.array([2]), fine_cells=None, origin_z=-200)
    bands = control_tagger._area_floors(asset, np.array([True]))["0"]
    assert bands[0][1] + 1 == bands[1][0]
    assert bands[0][0] <= -100 <= bands[0][1]
    assert bands[1][0] <= -100 + separation <= bands[1][1]
    obj = painted_object({0: 1}, bands={"0": [bands[0] + [1], bands[1] + [2]]})
    assert areas.validate(obj) == []


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


@pytest.mark.parametrize("also_parents", [False, True])
def test_export_text_matches_python_and_retains_the_saved_world_bands(also_parents):
    names = [NAMES[2], {**NAMES[1], "name": "Marché"}, NAMES[0]]
    if also_parents:
        names[1]["also_parents"] = [4, 3]
        names.append({**NAMES[2], "id": 4, "name": "Tower"})
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


def linked_names():
    return [
        {**NAMES[0], "id": 1, "name": "A"},
        {**NAMES[0], "id": 2, "name": "B"},
        {**NAMES[0], "id": 3, "name": "C", "parent": 2, "also_parents": [1]},
        {**NAMES[0], "id": 4, "name": "D", "parent": 3},
    ]


@pytest.mark.parametrize("id,ids", [
    (3, [4]), (3, [1, 1]), (1, [2]), (3, [99]), (3, [3]), (3, [2]),
    (3, [0]), (3, [-1]), (3, [True]), (3, [1.5]), (3, "1"), (99, [1]),
    (5, [4]), (3, [5]),
])
def test_also_parent_rejections_are_atomic_and_do_not_checkpoint(id, ids):
    names = linked_names() + [{**NAMES[0], "id": 5, "name": "E", "parent": 1}]
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data), before = JSON.stringify(a);
      const accepted = a.setAlsoParents(p.id, p.ids);
      return {accepted, unchanged: before === JSON.stringify(a), undo: a.undo()};
    }''', {"data": map_data(2, {0}, existing={"names": names, "cells": [[0, 4]]}), "id": id, "ids": ids})
    assert got == {"accepted": False, "unchanged": True, "undo": False}


def test_parent_edits_check_all_paths_and_clear_or_remove_extra_parents():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      const fourth = a.addName("E", 4), cycle = a.setParent(1, 4), depth = a.setParent(2, 1);
      const accepted = a.setAlsoParents(4, [1, 2]), switched = a.setParent(4, 1);
      const afterSwitch = JSON.parse(JSON.stringify(a._row(4)));
      const cleared = a.setParent(4, null), afterClear = JSON.parse(JSON.stringify(a._row(4)));
      const emptied = a.setAlsoParents(3, []);
      return {fourth, cycle, depth, accepted, switched, afterSwitch, cleared, afterClear, emptied,
              afterEmpty: a._row(3)};
    }''', map_data(2, {0}, existing={"names": linked_names(), "cells": [[0, 4]]}))
    assert (got["fourth"], got["cycle"], got["depth"]) == (None, False, False)
    assert got["accepted"] and got["switched"] and got["cleared"] and got["emptied"]
    assert got["afterSwitch"]["also_parents"] == [2]
    assert got["afterClear"]["parent"] is None
    assert "also_parents" not in got["afterClear"]
    assert "also_parents" not in got["afterEmpty"]


def test_add_and_reparent_refuse_depth_four_only_through_extra_parent():
    names = linked_names()[:3]
    names[2]["also_parents"] = [5]
    names.append({**NAMES[0], "id": 5, "name": "E", "parent": 1})
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p), before = JSON.stringify(a);
      const added = a.addName("Too deep", 3), changed = a.setParent(1, 2);
      return {added, changed, unchanged: before === JSON.stringify(a), undo: a.undo()};
    }''', map_data(2, {0}, existing={"names": names, "cells": [[0, 4]]}))
    assert got == {"added": None, "changed": False, "unchanged": True, "undo": False}


def test_merge_rewrites_extra_parents_and_drops_duplicates_parent_and_self():
    names = linked_names()[:3] + [
        {**NAMES[0], "id": 4, "name": "D", "parent": 2, "also_parents": [1, 3]},
        {**NAMES[0], "id": 5, "name": "E", "parent": 1, "also_parents": [3]},
    ]
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      return {merged: a.merge(1, 3), names: a.names};
    }''', map_data(2, {0}, existing={"names": names, "cells": [[0, 4]]}))
    assert got["merged"]
    rows = {row["id"]: row for row in got["names"]}
    assert "also_parents" not in rows[3]
    assert rows[4]["also_parents"] == [3]
    assert rows[5]["parent"] == 3 and "also_parents" not in rows[5]


def test_merge_refuses_cycle_through_second_parent_without_changing_any_state():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p), before = JSON.stringify(a);
      const merged = a.merge(1, 4);
      return {merged, unchanged: before === JSON.stringify(a), undo: a.undo()};
    }''', map_data(2, {0}, existing={"names": linked_names(), "cells": [[1, 4]],
                                  "bands": {"0": [[0, 10, 1], [20, 30, 4]]}}))
    assert got == {"merged": False, "unchanged": True, "undo": False}


def test_second_parent_pin_and_fixture_agree_with_python(monkeypatch):
    from tests.coach import test_area_cases

    # Same five-field shape as the owner-confirmed case rows.
    pin = {"map": "Sunset", "u": 0, "v": 0, "z": None, "area": "A"}
    obj = painted_object({0: 4}, names=linked_names())
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data);
      return {status: a.pinStatus([p.pin]), fixture: a.fixtureFromPins([p.pin]),
              path: a._pinPath(p.pin), text: a.exportText()};
    }''', {"data": map_data(128, {0}, existing=obj, name="Sunset"), "pin": pin})
    assert got["status"][0]["ok"] and got["fixture"] == [pin]
    assert got["path"] == ["B", "C", "D", "A"]
    painted = areas.from_raw("Sunset", got["text"].encode())
    assert painted is not None
    result = areas.area_at("Sunset", 0, 0, painted_table={"Sunset": painted})
    assert result["path"] == ["B", "C", "D"] and result["also"] == ["A"]
    monkeypatch.setattr(test_area_cases.json, "loads", lambda _: got["fixture"])
    monkeypatch.setattr(areas, "load_painted", lambda *args, **kwargs: painted)
    test_area_cases.test_owner_confirmed_area_cases()


def test_undo_restores_full_state_after_parent_edit_merge_and_id_allocation():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      const empty = a.undo();
      a.hand.add(0); a.queue.push({id: 2, cells: [1]});
      const state = () => JSON.stringify({cells:a.cells, bands:a.bands, names:a.names,
                                        next:a._nextId, hand:[...a.hand], queue:a.queue});
      const before = state(), edited = a.setParent(2, 3), parent = state();
      a.hand.add(1); a.queue[0].cells.push(2);
      const mergedBefore = state(), merged = a.merge(2, 3);
      const undoMerge = a.undo(), restoredMerge = state() === mergedBefore;
      const undoParent = a.undo(), restoredParent = state() === before;
      const id = a.addName("New"), undoAdd = a.undo(), restoredId = state() === before;
      return {empty, edited, parentChanged: parent !== before, merged, undoMerge, restoredMerge,
              undoParent, restoredParent, id, undoAdd, restoredId, exhausted: a.undo()};
    }''', map_data(2, {0, 1}, existing={"names": NAMES, "cells": [[2, 1], [1, 3]],
                                    "bands": {"0": [[0, 10, 2], [20, 30, 3]]}}))
    assert got == {"empty": False, "edited": True, "parentChanged": True, "merged": True,
                   "undoMerge": True, "restoredMerge": True, "undoParent": True, "restoredParent": True,
                   "id": 4, "undoAdd": True, "restoredId": True, "exhausted": False}


def test_checkpoint_retains_only_the_last_fifty_snapshots():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      for (let i = 0; i < 51; i++) { a.checkpoint(); a.cells[0] = i + 1; }
      const restored = [];
      while (a.undo()) restored.push(a.cells[0]);
      return restored;
    }''', map_data(2, {0}))
    assert got == list(range(50, 0, -1))


@pytest.mark.parametrize("action", [
    'a.addName("New", 1)', 'a.rename(2, "Corner")', 'a.setParent(2, 3)',
    'a.setAlsoParents(2, [3])', 'a.setSuper(2, "B")', 'a.confirmSuper(2)', 'a.merge(2, 3)',
])
def test_each_one_shot_action_takes_exactly_one_checkpoint(action):
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      const before = JSON.stringify(a.names), accepted = ACTION;
      const undo = a.undo(), restored = JSON.stringify(a.names) === before;
      return {accepted: !!accepted, undo, restored, exhausted: a.undo()};
    }'''.replace("ACTION", action), map_data(2, {0}))
    assert got == {"accepted": True, "undo": True, "restored": True, "exhausted": False}


def test_refused_actions_and_brush_operations_do_not_checkpoint():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p), before = JSON.stringify(a);
      const refused = [a.addName("Mid"), a.addName("Deep", 99), a.rename(99, "New"),
        a.rename(2, "Mid"), a.setParent(1, 2), a.setParent(2, 99), a.setSuper(99, "A"),
        a.setSuper(1, "Unknown"), a.confirmSuper(99), a.merge(1, 99), a.merge(1, 1)];
      const unchanged = before === JSON.stringify(a);
      a.paint([0], 1); a.erase([0]); a.fill(0, 2);
      return {refused, unchanged, undo: a.undo()};
    }''', map_data(2, {0, 1}))
    assert got["refused"] == [None, None] + [False] * 9
    assert got["unchanged"] and got["undo"] is False


def placement_data(grid, walk, cells, *, bands=None, floors=None):
    return map_data(grid, walk, draft={"names": NAMES, "cells": areas_draft.encode_cells(cells),
                                       "bands": bands or {}}, floors=floors)


@pytest.mark.parametrize("old", [0, 1])
@pytest.mark.parametrize("protection", ["hand", "bands", "floors", "both"])
def test_place_respects_area_walls_and_independent_floor_protections(old, protection):
    cells = [old] * 6 + [3, 0] + [0] * 56
    bands = {"1": [[0, 10, old], [20, 30, 2]]} if protection == "bands" else {}
    if protection == "both":
        bands = {"1": [[0, 10, old]]}
    floors = {"1": [[0, 10], [20, 30]]} if protection in ("floors", "both") else {}
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data);
      if (p.protection === "hand") a.paint([1], a.cells[1]);
      const beforeBands = JSON.stringify(a.bands), old = a.cells[1];
      const placed = a.place(0, "New", 3);
      return {placed, protected: a.cells[1] === old, cells: a.cells,
              bandsUnchanged: beforeBands === JSON.stringify(a.bands), hand: [...a.hand],
              parent: a._row(placed.id).parent};
    }''', {"data": placement_data(8, set(range(7)), cells, bands=bands, floors=floors),
           "protection": protection})
    assert got["placed"]["id"] == 4 and got["placed"]["count"] >= 1
    assert got["protected"] and got["bandsUnchanged"]
    assert got["cells"][0] == 4 and got["cells"][6:8] == [3, 0]
    assert got["hand"] == ([1] if protection == "hand" else [])
    assert got["parent"] == 3  # A non-touching parent is permitted.


@pytest.mark.parametrize("reason", ["wall", "hand", "bands", "floors", "both", "taken", "parent", "deep"])
def test_refused_place_is_atomic_and_does_not_checkpoint(reason):
    bands = {"0": [[0, 10, 1], [20, 30, 2]]} if reason == "bands" else {}
    if reason == "both":
        bands = {"0": [[0, 10, 1]]}
    floors = {"0": [[0, 10], [20, 30]]} if reason in ("floors", "both") else {}
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data);
      if (p.reason === "hand") a.paint([0], 1);
      if (p.reason === "deep") { a.addName("Leaf", 2); a._undoStack = []; }
      const state = () => JSON.stringify({model:a, hand:[...a.hand]});
      const before = state();
      const result = a.place(0, p.reason === "taken" ? "Mid" : "New",
                             p.reason === "parent" ? 99 : p.reason === "deep" ? 4 : null);
      return {error: typeof result.error === "string", unchanged: before === state(), undo: a.undo()};
    }''', {"data": placement_data(2, {1} if reason == "wall" else {0, 1}, [1, 1, 0, 0],
                                  bands=bands, floors=floors), "reason": reason})
    assert got == {"error": True, "unchanged": True, "undo": False}


@pytest.mark.parametrize("click,expected", [(0, [4, 1, 1, 1, 1]), (2, [1, 1, 4, 4, 4])])
def test_place_uses_centroid_ties_and_strict_walking_distance(click, expected):
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data);
      a.queue = ["New", "Later"];
      const result = a.place(p.click, "New");
      return {result, cells: a.cells.slice(0, 5), queue: a.queue, hand: [...a.hand]};
    }''', {"data": placement_data(5, set(range(5)), [1] * 5 + [0] * 20), "click": click})
    assert got == {"result": {"id": 4, "count": expected.count(4)}, "cells": expected,
                   "queue": ["Later"], "hand": []}


@pytest.mark.parametrize("old", [0, 1])
def test_place_does_not_cross_a_blocked_diagonal_or_another_area(old):
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      return {result: a.place(0, "New"), cells: a.cells};
    }''', placement_data(3, {0, 4, 5, 8}, [old, 0, 0, 0, old, 3, 0, 0, old]))
    assert got["result"] == {"id": 4, "count": 1}
    assert got["cells"] == [4, 0, 0, 0, old, 3, 0, 0, old]


def test_place_takes_only_the_connected_unnamed_pocket():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      return {result: a.place(0, "New"), cells: a.cells};
    }''', placement_data(4, {0, 1, 2, 3, 15}, [0, 0, 3, 0] + [0] * 12))
    assert got["result"] == {"id": 4, "count": 2}
    assert got["cells"] == [4, 4, 3, 0] + [0] * 12


@pytest.mark.parametrize("outside_walkable", [False, True])
def test_named_place_distances_follow_the_clicked_area_around_a_detour(outside_walkable):
    clicked_area = {0, 1, 2, 3, 4, 9, 14, 19, 24, 23, 22, 21, 20, 15, 10}
    walk = set(range(25)) if outside_walkable else clicked_area
    cells = [1 if cell in clicked_area else 3 if outside_walkable else 0 for cell in range(25)]
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      const result = a.place(10, "New");
      return {result, cells: a.cells};
    }''', placement_data(5, walk, cells))
    # The centre is 14. Cell 0 is close in a straight line, but far around the U.
    expected = cells.copy()
    for cell in (10, 15, 20, 21):
        expected[cell] = 4
    assert got == {"result": {"id": 4, "count": 4}, "cells": expected}


def test_place_can_take_a_named_singleton():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      return {result: a.place(0, "New"), cells: a.cells};
    }''', placement_data(2, {0, 1}, [1, 3, 0, 0]))
    assert got == {"result": {"id": 4, "count": 1}, "cells": [4, 3, 0, 0]}


@pytest.mark.parametrize("action", ["paint", "fill", "erase"])
def test_noop_brush_and_fill_protect_and_undo_restores_hand(action):
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data);
      a.checkpoint();
      const changed = p.action === "fill" ? a.fill(0, 1) :
        p.action === "erase" ? a.erase([3]) : a.paint([0], 1);
      const protectedCell = p.action === "erase" ? 3 : 0;
      const hand = [...a.hand], refused = a.place(protectedCell, "New");
      const undone = a.undo(), afterHand = [...a.hand];
      const placed = a.place(protectedCell, "New");
      return {changed, hand, refused: !!refused.error, undone, afterHand, placed};
    }''', {"data": placement_data(2, {0, 1, 3}, [1, 1, 0, 0]), "action": action})
    assert got["changed"] == 0 and got["refused"] and got["undone"]
    assert got["hand"] == {"paint": [0], "fill": [0, 1], "erase": [3]}[action]
    assert got["afterHand"] == [] and got["placed"]["count"] >= 1


def test_loaded_hand_compares_name_strings_not_ids_and_ignores_walls():
    names = [{**NAMES[0], "id": 8}, {**NAMES[2], "id": 9}]
    got = run_node('''function run(p) { return [...T.Areas.fromData(p).hand]; }''',
                   map_data(2, {0, 1, 2}, draft={"names": NAMES, "cells": [[1, 4]]},
                            existing={"names": names, "cells": [[8, 1], [9, 1], [0, 2]]}))
    assert got == [1, 2]


def test_shrink_reassigns_on_foot_only_selected_cells_and_leaves_unreachable_unnamed():
    walk = {0, 1, 2, 3, 4, 9, 14, 19, 24, 23, 22, 21, 20, 15, 10, 12}
    cells = [0] * 25
    for cell, id in {0: 2, 22: 3, 10: 1, 12: 1, 15: 1}.items():
        cells[cell] = id
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p); a.hand.add(10);
      const count = a.shrink([0, 10, 12], 1);
      return {count, cells: a.cells, hand: [...a.hand], undo: a.undo()};
    }''', placement_data(5, walk, cells))
    assert got["count"] == 2
    assert [got["cells"][i] for i in (0, 10, 12, 15, 22)] == [2, 3, 0, 1, 3]
    assert got["hand"] == [10] and got["undo"] is False


@pytest.mark.parametrize("action", ["shrink", "delete"])
def test_reassignment_ties_choose_lowest_name_id(action):
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data);
      if (p.action === "shrink") a.shrink([2], 1); else a.deleteName(1);
      return a.cells.slice(0, 5);
    }''', {"data": placement_data(5, set(range(5)), [3, 0, 1, 0, 2] + [0] * 20), "action": action})
    assert got == [3, 0, 2, 0, 2]


def test_shrink_floor_picker_reassigns_only_the_selected_name_and_floor():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      const count = a.shrink([1, 2], 1, 1);
      return {count, cells: a.cells.slice(0, 3), bands: a.bands, hand: [...a.hand]};
    }''', placement_data(3, {0, 1, 2}, [3, 1, 2] + [0] * 6,
                         bands={"0": [[0, 10, 3], [20, 30, 2]],
                                "1": [[0, 10, 1], [20, 30, 1]],
                                "2": [[0, 10, 2], [20, 30, 3]]}))
    assert got["count"] == 1 and got["cells"] == [3, 1, 2] and got["hand"] == []
    assert got["bands"]["1"] == [[0, 10, 1], [20, 30, 2]]
    assert got["bands"]["2"] == [[0, 10, 2], [20, 30, 3]]


def test_delete_promotes_children_reassigns_bands_and_exports_valid_file():
    names = linked_names() + [{**NAMES[0], "id": 5, "name": "E", "parent": 2}]
    obj = painted_object({0: 2, 1: 3, 2: 4, 3: 5}, names=names,
                         bands={"0": [[0, 10, 2], [20, 30, 4]],
                                "1": [[0, 10, 3], [20, 30, 2]]})
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p); a.hand.add(0);
      const deleted = a.deleteName(2);
      return {deleted, cells: a.cells.slice(0, 4), text: a.exportText(), hand: [...a.hand]};
    }''', map_data(128, {0, 1, 2, 3}, existing=obj, name="Sunset"))
    assert got["deleted"] and got["cells"][1:] == [3, 4, 5]
    exported = json.loads(got["text"])
    assert areas.validate(exported) == []
    rows = {row["id"]: row for row in exported["names"]}
    assert 2 not in rows and rows[3]["parent"] == 1 and "also_parents" not in rows[3]
    assert rows[5]["parent"] is None and rows[5]["super_source"] == "auto"
    assert all(2 not in row.get("also_parents", []) for row in rows.values())
    assert all(band[2] != 2 for bands in exported["bands"].values() for band in bands)
    assert 0 in got["hand"]


@pytest.mark.parametrize("action", ["delete", "shrink"])
@pytest.mark.parametrize("ground,bands", [
    (1, None),  # The finding's accepted file: name 1 on non-walkable cell 0.
    (1, [[0, 10, 1], [20, 30, 3]]),
    (3, [[0, 10, 3], [20, 30, 1]]),  # Upper-band-only reference.
    (1, [[0, 10, 1], [20, 30, 0]]),  # Clearing collapses to plain unnamed ground.
    (0, [[0, 10, 0], [20, 30, 1], [40, 50, 1]]),
    (1, [[0, 10, 1], [5, 15, 1], [20, 30, 3]]),
    (3, [[0, 10, 3], [20, 30, 2]]),  # Unrelated bands stay intact.
    (0, None),
])
def test_delete_clears_all_wall_references_but_shrink_preserves_them(action, ground, bands):
    wall_cells = (0, 128 ** 2 - 1)
    obj = painted_object({**dict.fromkeys(wall_cells, ground), 1: 1, 2: 3},
                         bands={str(cell): bands for cell in wall_cells} if bands else {})
    assert areas.validate(obj) == []
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data); a.hand = new Set([1]);
      const before = JSON.stringify(a.draftState());
      if(p.action === 'delete') a.deleteName(1);
      else {a.checkpoint(); a.shrink([0, 1, 16383], 1);}
      const saved = a.draftState(), b = T.Areas.fromData(p.data);
      const text = a.exportText(), restored = b.restoreDraft(saved);
      const same = JSON.stringify(b.draftState()) === JSON.stringify(saved);
      const undo = a.undo(), undone = JSON.stringify(a.draftState()) === before;
      return {saved, text, restored, same, undo, undone, exhausted: a.undo()};
    }''', {"data": map_data(128, {1, 2}, existing=obj, name="Sunset"), "action": action})
    assert areas.validate(json.loads(got["text"])) == []
    assert all(got[key] for key in ("restored", "same", "undo", "undone"))
    assert not got["exhausted"] and got["saved"]["hand"] == [1]
    cells = [name for name, count in got["saved"]["cells"] for _ in range(count)]
    assert cells[1:3] == [3, 3]  # Walkable deletion/shrink still uses the nearest remaining area.
    expected_ground = 0 if action == "delete" and ground == 1 else ground
    expected_bands = [[lo, hi, 0 if action == "delete" and name == 1 else name]
                      for lo, hi, name in bands] if bands else None
    if action == "delete" and expected_bands and len({row[2] for row in expected_bands}) == 1:
        expected_bands = None
    for cell in wall_cells:
        assert cells[cell] == expected_ground
        assert got["saved"]["bands"].get(str(cell)) == expected_bands


def test_delete_removes_secondary_parent_without_changing_primary_or_cells():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      const deleted = a.deleteName(1);
      return {deleted, row: a._row(3), cells: a.cells};
    }''', map_data(2, {0, 1}, draft={"names": linked_names(), "cells": [[3, 1], [4, 1], [0, 2]]}))
    assert got["deleted"] and got["row"]["parent"] == 2
    assert "also_parents" not in got["row"] and got["cells"] == [3, 4, 0, 0]


@pytest.mark.parametrize("action", ["shrink", "delete"])
def test_vacating_without_another_named_source_becomes_unnamed(action):
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data);
      if (p.action === "shrink") a.shrink([0], 1); else a.deleteName(1);
      return {cells: a.cells, hand: [...a.hand], bands: a.bands};
    }''', {"data": placement_data(2, {0}, [1, 0, 0, 0]), "action": action})
    assert got == {"cells": [0, 0, 0, 0], "hand": [], "bands": {}}


def test_unknown_delete_and_shrink_are_atomic_and_do_not_checkpoint():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p), before = JSON.stringify(a);
      const deleted = a.deleteName(99), count = a.shrink([0], 99);
      return {deleted, count, unchanged: before === JSON.stringify(a), undo: a.undo()};
    }''', placement_data(2, {0}, [1, 0, 0, 0]))
    assert got == {"deleted": False, "count": 0, "unchanged": True, "undo": False}


@pytest.mark.parametrize("action", ["place", "shrink", "delete"])
def test_undo_exact_state_and_single_checkpoint_after_spatial_action(action):
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data);
      a.hand.add(1); a.queue = ["New", "Later"];
      const state = () => JSON.stringify({cells:a.cells, bands:a.bands, names:a.names,
                                        next:a._nextId, hand:[...a.hand], queue:a.queue});
      const before = state();
      if (p.action === "place") a.place(0, "New", 3);
      if (p.action === "shrink") { a.checkpoint(); a.shrink([0, 1], 1); }
      if (p.action === "delete") a.deleteName(1);
      const changed = state() !== before, undo = a.undo(), restored = state() === before;
      return {changed, undo, restored, exhausted: a.undo()};
    }''', {"data": placement_data(3, {0, 1, 2}, [1, 1, 3] + [0] * 6,
                                  bands={"1": [[0, 10, 1], [20, 30, 2]]}), "action": action})
    assert got == {"changed": True, "undo": True, "restored": True, "exhausted": False}


def test_paste_names_matches_queues_and_undoes_once():
    got = run_node(r'''function run(p) {
      const a = T.Areas.fromData(p), before = JSON.stringify(a);
      const result = a.pasteNames("  MID \r\n\nArch\narch\n top mid \nMID\nNook\n");
      const names = a.names.map(r => [r.id, r.name]), queue = a.unplaced();
      queue.push("Copy only");
      const unplaced = a.unplaced(), undo = a.undo(), restored = before === JSON.stringify(a);
      const first = a.pasteNames("Arch"), second = a.pasteNames("ARCH\nMid");
      return {result, names, unplaced, undo, restored, first, second, queue: a.unplaced()};
    }''', map_data(2, {0}))
    assert got == {
        "result": {"matched": ["MID", "top mid"], "queued": ["Arch", "Nook"], "unlisted": ["A Site"]},
        "names": [[1, "MID"], [2, "top mid"], [3, "A Site"]], "unplaced": ["Arch", "Nook"],
        "undo": True, "restored": True,
        "first": {"matched": [], "queued": ["Arch"], "unlisted": ["Mid", "Top Mid", "A Site"]},
        "second": {"matched": ["Mid"], "queued": [], "unlisted": ["Top Mid", "A Site"]},
        "queue": ["Arch"],
    }


def test_paste_noop_checkpoint_and_export_ignores_queue():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      a.pasteNames("Mid");
      const undo = a.undo(), exhausted = a.undo();
      a.pasteNames("Arch\\nNook");
      return {undo, exhausted, text: a.exportText(), unplaced: a.unplaced()};
    }''', map_data(128, {0}))
    assert got["undo"] and got["exhausted"] is False
    assert got["unplaced"] == ["Arch", "Nook"]
    assert all(name not in got["text"] for name in got["unplaced"])
    assert areas.validate(json.loads(got["text"])) == []


def test_pasted_name_stays_unplaced_until_placed_with_any_case():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p);
      a.pasteNames("Arch");
      const before = a.names.length, placed = a.place(0, "ARCH");
      return {before, placed, names: a.names.map(r => r.name), queue: a.unplaced()};
    }''', map_data(2, {0}))
    assert got == {"before": 3, "placed": {"id": 4, "count": 1},
                   "names": ["Mid", "Top Mid", "A Site", "ARCH"], "queue": []}


def test_paste_preserves_ambiguous_legacy_names_without_choosing_an_id():
    names = [*NAMES, {**NAMES[0], "id": 4, "name": "MID"}]
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p), before = JSON.stringify(a.names);
      const result = a.pasteNames("mid");
      return {result, unchanged: before === JSON.stringify(a.names)};
    }''', map_data(2, {0}, draft={"names": names, "cells": [[0, 4]]}))
    assert got == {"result": {"matched": [], "queued": [], "unlisted": ["Mid", "Top Mid", "A Site", "MID"]},
                   "unchanged": True}


def test_draft_roundtrip_copies_full_state_and_success_can_be_undone():
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p), b = T.Areas.fromData(p);
      a.setAlsoParents(2, [3]); a.paint([1], 2); a.pasteNames("Arch\\nNook");
      a._nextId = 12;
      const draft = a.draftState(), saved = JSON.stringify(draft), before = JSON.stringify(b);
      const input = JSON.parse(saved), restored = b.restoreDraft(input);
      const same = a.exportText() === b.exportText();
      const state = b.draftState();
      input.names[1].also_parents.push(99); input.cells[0][0] = 99;
      input.bands[0][0][0] = -999; input.hand.push(2); input.queue.push("Changed");
      const isolated = JSON.stringify(state) === JSON.stringify(b.draftState());
      draft.names[0].name = "Changed"; draft.cells[0][0] = 99;
      draft.bands[0][0][0] = -999; draft.hand.push(2); draft.queue.push("Changed");
      const detached = saved === JSON.stringify(a.draftState());
      const undo = b.undo(), undone = before === JSON.stringify(b);
      const c = T.Areas.fromData(p);
      // Equivalent walk runs must identify the same walk mask.
      p.walk = [[1, 1], [1, 2], [0, 6]];
      const split = T.Areas.fromData(p).draftState().walk_hash === c.draftState().walk_hash;
      return {restored, same, state, isolated, detached, undo, undone, split};
    }''', map_data(3, {0, 1, 2}, draft={"names": NAMES, "cells": [[1, 1], [0, 8]],
                                                   "bands": {"0": [[-10, 0, 1], [10, 20, 2]]}}))
    assert all(got[key] for key in ("restored", "same", "isolated", "detached", "undo", "undone", "split"))
    state = got["state"]
    assert set(state) == {"v", "map", "grid", "walk_hash", "names", "cells", "bands", "hand", "queue", "next_id"}
    assert (state["v"], state["map"], state["grid"], state["next_id"]) == (1, "Toy", 3, 12)
    assert isinstance(state["walk_hash"], str) and state["walk_hash"]
    assert state["hand"] == [1] and state["queue"] == ["Arch", "Nook"]
    assert state["cells"] == [[1, 1], [2, 1], [0, 7]]
    assert state["bands"] == {"0": [[-10, 0, 1], [10, 20, 2]]}
    assert state["names"][1]["also_parents"] == [3]


@pytest.mark.parametrize("change", [
    "d = null", "d = []", "d.v = true", "d.v = 2", 'd.map = "Other"', "d.grid = 4",
    'd.walk_hash = "old"', "a.walk[2] = 1", "a.grid = 4", 'a.map = "Other"',
    "d.cells = [[99, 9]]", "d.cells = [[0, 8]]", "d.cells = [[0, 10]]",
    "d.cells = [[0, 0], [0, 9]]", "d.cells = [[0, -1], [0, 10]]",
    "d.cells = [[false, 9]]", "d.cells = [[0, 9, 1]]", "d.cells = null",
    'd.bands = {"0": [[0, 10, 0], [10, 20, 1]]}',
    'd.bands = {"0": [[0, 10, 1]]}', 'd.bands = {"0": [[0, 10, 99]]}',
    'd.bands = {"0": [[10, 0, 0]]}', 'd.bands = {"0": [[0.5, 10, 0]]}',
    'd.bands = {"0": []}', 'd.bands = {"00": [[0, 10, 0]]}',
    'd.bands = {"9": [[0, 10, 0]]}', 'd.bands = {"-1": [[0, 10, 0]]}',
    'd.bands = {"1e0": [[0, 10, 0]]}', "d.bands = []", "d.bands = null",
    "d.names = null", "d.names[0] = null", "d.names[1].id = 1",
    'd.names[1].name = "Mid"', 'd.names[1].name = "  "', "d.names[0].name = 1",
    "delete d.names[0].parent", "d.names[0].parent = 2", "d.names[1].parent = 99",
    "d.names[1].also_parents = [3, 3]", "d.names[1].also_parents = [2]",
    'd.names[1].super = "B"', 'd.names[0].super = ""', 'd.names[0].super_source = "other"',
    "d.hand = [8]", "d.hand = [0, 0]", "d.hand = [0.5]", "d.hand = null",
    'd.queue = ["mID"]', 'd.queue = ["Arch", "ARCH"]', 'd.queue = [" "]',
    "d.queue = [1]", "d.queue = null", "d.next_id = 3", "d.next_id = true", "d.next_id = 4.5",
])
def test_restore_draft_rejects_malformed_json_atomically(change):
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data);
      a.rename(3, "Site"); a.hand.add(0); a.queue = ["Later"];
      let d = a.draftState();
      eval(p.change);
      d = JSON.parse(JSON.stringify(d));
      const before = JSON.stringify(a), input = JSON.stringify(d);
      const restored = a.restoreDraft(d);
      return {restored, unchanged: before === JSON.stringify(a), kept: input === JSON.stringify(d)};
    }''', {"data": map_data(3, {0, 1}), "change": change})
    assert got == {"restored": False, "unchanged": True, "kept": True}


def test_restore_draft_band_rules_match_python():
    bands = {"0": [[20, 30, 2], [0, 10, 1], [5, 15, 1]], "1": [[0, 0, 0]]}
    obj = painted_object({0: 1}, bands=bands)
    assert areas.validate(obj) == []
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p), d = a.draftState();
      const restored = a.restoreDraft(d);
      return {restored, text: a.exportText()};
    }''', map_data(128, {0, 1}, existing=obj))
    assert got["restored"]
    assert areas.validate(json.loads(got["text"])) == []


@pytest.mark.parametrize("action", ["add", "rename", "place", "paste"])
@pytest.mark.parametrize("spelling", ["Arch", "ARCH", "  arch  "])
def test_name_actions_reconcile_queue_and_roundtrip_in_one_checkpoint(action, spelling):
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data);
      a.pasteNames("Arch\\nLater");
      if (p.action === "paste") {
        // A collision left by an older editor must also be repaired by paste.
        a.names[2].name = "Arch";
      }
      const before = JSON.stringify(a.draftState()), depth = a._undoStack.length;
      let result;
      if (p.action === "add") result = a.addName(p.spelling);
      if (p.action === "rename") result = a.rename(3, p.spelling);
      if (p.action === "place") result = a.place(0, p.spelling);
      if (p.action === "paste") result = a.pasteNames(p.spelling);
      const saved = a.draftState(), b = T.Areas.fromData(p.data);
      const restored = b.restoreDraft(saved), same = JSON.stringify(saved) === JSON.stringify(b.draftState());
      const checkpoints = a._undoStack.length - depth, undo = a.undo();
      return {result, queue: saved.queue, restored, same, checkpoints, undo,
              undone: before === JSON.stringify(a.draftState())};
    }''', {"data": map_data(2, {0}), "action": action, "spelling": spelling})
    assert got["result"]
    assert got["queue"] == ["Later"]
    assert all(got[key] for key in ("restored", "same", "undo", "undone"))
    assert got["checkpoints"] == 1


@pytest.mark.parametrize("label, action", [
    (label, action) for label in ("MID", "mid") for action in ("load", "add", "rename")
] + [(" Mid", "load"), ("Mid ", "load")])
def test_restore_names_uses_validator_exact_spelling_rule(label, action):
    names = [*NAMES, {**NAMES[0], "id": 4, "name": label}]
    obj = painted_object({0: 1, 1: 4}, names=names)
    assert areas.validate(obj) == []
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data);
      let edited = true;
      if (p.action === "add") edited = !!a.addName(p.label);
      if (p.action === "rename") edited = a.rename(3, p.label);
      a.pasteNames("Later");
      const saved = a.draftState(), b = T.Areas.fromData(p.data);
      const restored = b.restoreDraft(saved);
      return {edited, restored, same: JSON.stringify(saved) === JSON.stringify(b.draftState()), text: b.exportText()};
    }''', {"data": map_data(128, {0, 1}, existing=obj if action == "load" else None),
           "label": label, "action": action})
    assert got["edited"] and got["restored"] and got["same"]
    assert areas.validate(json.loads(got["text"])) == []


@pytest.mark.parametrize("action", ["paint", "erase", "shrink", "delete"])
@pytest.mark.parametrize("source", ["existing", "floors"])
@pytest.mark.parametrize("ranges", [
    [[0, 10], [5, 15], [20, 30]],  # The review's real band row, including its upper area.
    [[0, 10], [10, 15], [20, 30]],  # Inclusive boundary contact is also overlap.
    [[0, 10], [5, 5], [20, 30]],
    [[0, 10], [2, 3], [5, 15], [20, 30]],  # Check non-neighbouring pairs too.
    [[0, 10], [11, 15], [20, 30]],  # Adjacent non-overlapping input remains editable.
])
@pytest.mark.parametrize("picker", [0, 1, "range"])
def test_band_edits_skip_unsafe_cells_and_export_valid_files(action, source, ranges, picker):
    bands = [bounds + [2 if i == len(ranges) - 1 else 1] for i, bounds in enumerate(ranges)]
    obj = painted_object({0: 1, 1: 1, 2: 2}, bands={"0": bands} if source == "existing" else {})
    assert areas.validate(obj) == []
    selected = ranges[0] if picker == "range" else picker
    unsafe = ranges[1][0] <= ranges[0][1]
    got = run_node('''function run(p) {
      const a = T.Areas.fromData(p.data), beforeBands = JSON.stringify(a.bands[0]);
      const beforeHand = a.hand.has(0);
      let count;
      if (p.action === "paint") count = a.paint([0, 1], 2, p.band);
      if (p.action === "erase") count = a.erase([0, 1], p.band);
      if (p.action === "shrink") count = a.shrink([0, 1], 1, p.band);
      if (p.action === "delete") count = a.deleteName(1);
      const saved = a.draftState(), b = T.Areas.fromData(p.data);
      return {count, cell: a.cells[0], bandsUnchanged: beforeBands === JSON.stringify(a.bands[0]),
              handUnchanged: beforeHand === a.hand.has(0), text: a.exportText(), restored: b.restoreDraft(saved)};
    }''', {"data": map_data(128, {0, 1, 2}, existing=obj,
                           floors={"0": ranges} if source == "floors" else {}),
           "action": action, "band": selected})
    assert areas.validate(json.loads(got["text"])) == []
    assert got["restored"]
    if action == "delete":
        assert got["count"] is True and got["cell"] == 2
    elif unsafe:
        assert got["cell"] == 1 and got["bandsUnchanged"] and got["handUnchanged"]
        assert got["count"] == (1 if picker == 0 else 0)
    else:
        assert got["count"] == (2 if picker == 0 else 1)
