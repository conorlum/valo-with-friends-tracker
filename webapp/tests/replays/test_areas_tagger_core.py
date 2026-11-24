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
