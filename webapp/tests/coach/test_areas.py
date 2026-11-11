import json
from copy import deepcopy

import pytest

from app.coach import areas
from app.replays import control_format as cf
from app.replays import format as fmt
from scripts import fetch_callouts

TABLE = {"Toy": [{"name": "A Site", "super": "A", "u": 1000, "v": 1000},
                 {"name": "B Site", "super": "B", "u": 9000, "v": 9000}]}


def test_area_is_the_nearest_callout():
    assert areas.area_at("Toy", 1200, 900, table=TABLE) == {
        "name": "A Site", "super": "A", "path": ["A Site"], "approximate": True,
    }
    assert areas.area_at("Toy", 8000, 8500, table=TABLE)["name"] == "B Site"


def test_a_map_without_callouts_has_no_areas():
    assert areas.area_at("Unknown", 5000, 5000, table=TABLE) is None
    assert areas.callouts("Unknown", TABLE) == []


NAMES = [
    {"id": 1, "name": "Mid", "parent": None, "super": "Mid", "super_source": "auto"},
    {"id": 2, "name": "Top Mid", "parent": 1, "super": "Mid", "super_source": "auto"},
    {"id": 3, "name": "A Site", "parent": None, "super": "A", "super_source": "set"},
]


def painted_object(cells, *, names=NAMES, bands=None):
    runs = []
    for cell in range(cf.GRID ** 2):
        name_id = cells.get(cell, 0)
        if runs and runs[-1][0] == name_id:
            runs[-1][1] += 1
        else:
            runs.append([name_id, 1])
    return {"version": 1, "map": "Sunset", "reviewed": True, "names": deepcopy(names),
            "cells": runs, "bands": bands or {}}


def cell_at(u, v):
    return int(v / fmt.UV_SCALE * cf.GRID) * cf.GRID + int(u / fmt.UV_SCALE * cf.GRID)


def write_paint(tmp_path, obj, map_name="Sunset"):
    path = tmp_path / f"{map_name}.areas.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_reviewed_paint_replaces_approximate_evidence_positions(tmp_path):
    points = [(3475, 4892), (8627, 4063)]
    names = deepcopy(NAMES)
    names[0]["name"] = "Mid Courtyard"
    names[1]["name"] = "Market"
    paint = write_paint(tmp_path, painted_object(dict(zip(map(lambda uv: cell_at(*uv), points), (2, 3))), names=names))
    assert [areas.area_at("Sunset", *uv, areas_dir=tmp_path) for uv in points] == [
        {"name": "Market", "super": "Mid", "path": ["Mid Courtyard", "Market"], "approximate": False},
        {"name": "A Site", "super": "A", "path": ["A Site"], "approximate": False},
    ]
    assert areas.area_at("Sunset", points[0][0] + fmt.UV_SCALE / cf.GRID, points[0][1], areas_dir=tmp_path) is None
    assert areas.is_painted("Sunset", areas_dir=tmp_path)
    assert areas.tree("Sunset", areas_dir=tmp_path) == [
        {"name": "Mid Courtyard", "parent": None, "super": "Mid"},
        {"name": "Market", "parent": "Mid Courtyard", "super": "Mid"},
        {"name": "A Site", "parent": None, "super": "A"},
    ]
    assert areas.all_names("Sunset", areas_dir=tmp_path) == ["Mid Courtyard", "Market", "A Site"]
    paint.unlink()
    guesses = [areas.area_at("Sunset", *uv, areas_dir=tmp_path) for uv in points]
    assert [(g["name"], g["approximate"]) for g in guesses] == [("Mid Courtyard", True), ("A Elbow", True)]
    assert not areas.is_painted("Sunset", areas_dir=tmp_path)
    assert areas.tree("Sunset", areas_dir=tmp_path) is None
    assert areas.all_names("Sunset", areas_dir=tmp_path) == [c["name"] for c in areas.callouts("Sunset")]


@pytest.mark.parametrize("change", [{"reviewed": False}, {"reviewed": 1}, {"version": 2}, {"map": "Bind"}, None])
def test_ignores_unreviewed_or_invalid_file(tmp_path, change, caplog):
    obj = painted_object({cell_at(3475, 4892): 2})
    path = write_paint(tmp_path, obj | (change or {}))
    if change is None:
        path.write_text("{", encoding="utf-8")
    assert areas.area_at("Sunset", 3475, 4892, areas_dir=tmp_path)["approximate"] is True
    if change != {"reviewed": False}:
        assert path.name in caplog.text


def test_floor_bands_select_the_specific_name(tmp_path):
    u, v = 3475, 4892
    single_u = u + fmt.UV_SCALE / cf.GRID
    cell = cell_at(u, v)
    single = cell_at(single_u, v)
    write_paint(tmp_path, painted_object(
        {cell: 1, single: 2}, bands={str(cell): [[0, 30, 1], [60, 90, 3]], str(single): [[0, 30, 2]]},
    ))
    assert areas.area_at("Sunset", u, v, 75, areas_dir=tmp_path)["name"] == "A Site"
    assert areas.area_at("Sunset", u, v, 10, areas_dir=tmp_path)["name"] == "Mid"
    assert areas.area_at("Sunset", u, v, areas_dir=tmp_path) is None
    assert areas.area_at("Sunset", u, v, 45, areas_dir=tmp_path) is None
    assert areas.area_at("Sunset", single_u, v, areas_dir=tmp_path) == {
        "name": "Top Mid", "super": "Mid", "path": ["Mid", "Top Mid"], "approximate": False,
    }


def test_export_is_deterministic():
    obj = painted_object({10: 1, 20: 2}, bands={"20": [[0, 30, 2]], "10": [[0, 30, 1]]})
    assert areas.validate(obj) == []
    assert areas.dumps(obj) == areas.dumps(obj)
    shuffled = deepcopy(obj)
    shuffled["names"].reverse()
    shuffled["bands"] = dict(reversed(list(obj["bands"].items())))
    assert areas.dumps(obj) == areas.dumps(shuffled)
    assert json.loads(areas.dumps(obj)) == obj


def test_validation_reports_bad_tree_and_unknown_cell():
    obj = painted_object({10: 99})
    obj["names"][0]["parent"] = 2
    del obj["names"][1]["super"]
    problems = areas.validate(obj)
    assert any("cycle" in p for p in problems)
    assert any("super" in p for p in problems)
    assert any("99" in p for p in problems)


def test_hash_tracks_reviewed_file_bytes_and_ignores_drafts(tmp_path):
    assert areas.table_hash(tmp_path) is None
    obj = painted_object({10: 2})
    write_paint(tmp_path, obj | {"reviewed": False})
    assert areas.table_hash(tmp_path) is None
    path = write_paint(tmp_path, obj)
    initial = areas.table_hash(tmp_path)
    assert initial is not None
    path.write_text(json.dumps(obj) + "\n", encoding="utf-8")
    assert areas.table_hash(tmp_path) != initial


class FakeMap:
    def to_uv(self, x, y):
        return int(y), int(x)


def test_convert_names_callouts_and_matches_maps_by_uuid():
    api = [{"uuid": "u-1", "displayName": "Ignored name",
            "callouts": [{"regionName": "Main", "superRegionName": "A", "location": {"x": 10.0, "y": 20.0}}]},
           {"uuid": "u-2", "displayName": "No callouts", "callouts": None},
           {"uuid": "u-9", "displayName": "Not ours", "callouts": []}]
    out = fetch_callouts.convert(api, {"Toy": FakeMap(), "Bare": FakeMap()}, {"Toy": "u-1", "Bare": "u-2"})
    assert out == {"Toy": [{"name": "A Main", "super": "A", "u": 20, "v": 10}], "Bare": []}
