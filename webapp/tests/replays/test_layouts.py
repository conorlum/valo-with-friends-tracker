"""Layout selection uses stored release branches and local asset availability only."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.replays import layouts


DATA = Path(__file__).resolve().parents[2] / "app" / "static" / "data"
BASE = {"id": "Ascent@base", "key": "Ascent", "start": None,
        "drawn_from": "13.08", "regions": None}
LATER = {"id": "Ascent@13.10", "key": "Ascent_13_10", "start": "13.10",
         "drawn_from": "13.10", "regions": "regions/Ascent_13_10.png"}


@pytest.fixture
def registry():
    return {"version": 1, "maps": {"Ascent": [BASE, LATER]}}


@pytest.mark.parametrize("branch, expected", [
    ("++Ares-Core+release-13.08", (13, 8)),
    ("release-13.10", (13, 10)),
    ("b", None), ("synthetic", None), (None, None), (13, None),
    ("release-13.08-extra", None),
])
def test_parse_patch(branch, expected):
    assert layouts.parse_patch(branch) == expected


@pytest.mark.parametrize("branch", ["b", None, "garbage", object()])
def test_base_only_never_reads_branch_or_index(branch, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("base-only selection must not parse a branch or load an index")

    monkeypatch.setattr(layouts, "parse_patch", forbidden)
    monkeypatch.setattr(layouts.json, "loads", forbidden)
    selected = layouts.layout_for("Ascent", branch, registry={"maps": {"Ascent": [BASE]}})
    assert selected == layouts.Selection("ok", layouts.Layout("Ascent@base", "Ascent", None, (13, 8), None))


@pytest.mark.parametrize("patch, key", [("13.09", "Ascent"), ("13.10", "Ascent_13_10"),
                                       ("13.12", "Ascent_13_10"), ("1.00", "Ascent")])
def test_selects_latest_layout(patch, key, registry):
    selected = layouts.layout_for("Ascent", f"++Ares-Core+release-{patch}", registry=registry,
                                  index={"maps": {"Ascent": {}, "Ascent_13_10": {}}})
    assert selected.state == "ok"
    assert selected.layout.asset_key == key


def test_missing_selected_assets_are_pending(registry):
    selected = layouts.layout_for("Ascent", "release-13.10", registry=registry, index={"maps": {"Ascent": {}}})
    assert selected.state == "layout_pending"
    assert selected.layout.display_id == "Ascent@13.10"
    assert selected.layout.start == (13, 10)
    assert selected.layout.drawn_from == (13, 10)
    assert selected.layout.region_mask == LATER["regions"]


@pytest.mark.parametrize("branch", ["b", "synthetic", None])
def test_multiple_layouts_need_a_known_patch(branch, registry):
    assert layouts.layout_for("Ascent", branch, registry=registry, index={"maps": {}}) == layouts.Selection(
        "layout_unknown", None)


def test_numeric_sort_and_selection(registry):
    ninth = dict(LATER, id="Ascent@13.9", key="Ascent_13_9", start="13.9")
    registry["maps"]["Ascent"] = [LATER, ninth, BASE]
    assert [row.start for row in layouts.layouts_of("Ascent", registry)] == [None, (13, 9), (13, 10)]
    selected = layouts.layout_for("Ascent", "release-13.10", registry=registry,
                                  index={"maps": {"Ascent": {}, "Ascent_13_9": {}, "Ascent_13_10": {}}})
    assert selected.layout.asset_key == "Ascent_13_10"


def test_absent_map_gets_base():
    base = layouts.Layout("NewMap@base", "NewMap", None, None, None)
    assert layouts.layouts_of("NewMap", {"maps": {}}) == [base]
    assert layouts.layout_for("NewMap", "b", registry={"maps": {}}, index={"maps": {}}) == layouts.Selection("ok", base)


def test_explicit_empty_registry_uses_fallback():
    assert layouts.layouts_of("Ascent", {"maps": {}})[0].drawn_from is None


@pytest.mark.parametrize("key, accepted", [("Ascent", True), ("Ascent_13_10", True),
                                         ("Ascent@13.10", False), ("Ascent.x", False), ("_13_10", False),
                                         ("A" * 64, True), ("A" * 65, False), ("A" * 58 + "_13_10", True),
                                         ("A" * 59 + "_13_10", False)])
def test_asset_key_syntax(key, accepted):
    assert bool(layouts.ASSET_KEY.fullmatch(key)) is accepted


def test_asset_key_helpers():
    assert layouts.map_of("Ascent_13_10") == "Ascent"
    assert layouts.map_of("Ascent") == "Ascent"
    assert layouts.key_for("Ascent", (13, 10)) == "Ascent_13_10"
    assert layouts.key_for("Ascent", None) == "Ascent"


def test_load_registry_from_path(tmp_path, registry):
    path = tmp_path / "layouts.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    assert layouts.load_registry(path) == registry


def test_default_registry_is_complete():
    registry = layouts.load_registry()
    index = json.loads((DATA / "control" / "index.json").read_text(encoding="utf-8"))
    assert registry["version"] == 1
    assert len(registry["maps"]) == 13
    assert registry["maps"].keys() == index["maps"].keys()
    assert list(registry["maps"]) == sorted(registry["maps"])
    for name, rows in registry["maps"].items():
        assert rows == [{"id": f"{name}@base", "key": name, "start": None,
                         "drawn_from": "13.08", "regions": None}]
        first = layouts.layouts_of(name)[0]
        assert first.asset_key == name
        assert first.start is None
        assert first.drawn_from == layouts.parse_patch(f"release-{rows[0]['drawn_from']}") == (13, 8)
        assert layouts.layout_for(name, "b") == layouts.Selection("ok", first)
    assert b"\r" not in (DATA / "layouts.json").read_bytes()


def test_default_index_is_used(registry):
    assert layouts.layout_for("Ascent", "release-13.09", registry=registry).state == "ok"
    assert layouts.layout_for("Ascent", "release-13.10", registry=registry).state == "layout_pending"


def test_value_objects_are_frozen():
    base = layouts.Layout("Ascent@base", "Ascent", None, None, None)
    with pytest.raises(FrozenInstanceError):
        base.asset_key = "Other"
    selected = layouts.Selection("ok", base)
    with pytest.raises(FrozenInstanceError):
        selected.state = "layout_pending"
