"""The map_features schema (app/replays/map_feature_schema.py): presets with unknowns explicit, structural
validation and warnings, the reference graph, and the runtime/editorial split (plan M0/M1, "Storage contract"
and "Validation")."""

import ast
import base64
import copy
import json
import sys
from pathlib import Path

import pytest

from app.replays import map_feature_schema as ms
from app.replays import map_feature_state as fs

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
FIXTURES = HERE.parent / "fixtures" / "control" / "map_features"
CATALOGUE = json.loads((FIXTURES / "catalogue_full.json").read_text(encoding="utf-8"))
MF = CATALOGUE["maps"]["Ascent"]["map_features"]


def codes(items):
    return sorted({i["code"] for i in items})


def test_the_full_fixture_is_valid_and_its_warnings_are_the_unresolved_facts():
    rep = ms.validate(MF, "Ascent")
    assert rep.errors == []
    got = {(w["where"], w["code"]) for w in rep.warnings}
    assert ("floor-3", "unresolved_floor") in got
    assert ("feature-1.transitions[1]", "unresolved_timing") in got and ("feature-1.transitions[1]", "unresolved_policy") in got
    assert ("route-10.directions[1]", "unresolved_cost") in got and ("route-12.directions[0]", "unresolved_cost") in got
    assert not any(code == "zero_length" for _, code in got), "a same-position rope on two verified floors is legitimate"
    assert ms.validate(CATALOGUE["maps"]["Atlantis"]["map_features"]).ok


def test_round_trip_keeps_unknown_fields_at_every_level():
    again = ms.canonical(json.loads(json.dumps(MF)))
    assert again == MF
    assert again["x_features_unknown"] == "kept" and again["features"][0]["x_feature_unknown"] == {"deep": {"deeper": True}}
    assert again["features"][0]["states"][0]["sight"][0]["x_occluder_unknown"] == "kept"
    assert again["triggers"][0]["targets"][0]["x_target_unknown"] == 0


def test_every_preset_is_valid_and_reducible_with_its_unknowns_explicit():
    for name in ms.PRESETS:
        mf = ms.empty()
        fid, mf = ms.allocate(mf, "feature")
        mf["features"].append({"id": fid, "name": name, **ms.preset(name)})
        rep = ms.validate(mf)
        assert rep.errors == [], (name, rep.errors)
        text = json.dumps(ms.preset(name))
        assert '"value": 0' not in text, f"{name} must not guess a zero"
        fs.run(mf["features"][0], [{"t": 1, "kind": "switch"}])        # runs, whatever the outcome
    door = ms.make_breakable(ms.preset("switch_door"))
    assert "breakable" in door["capabilities"] and door["states"][-1] == {"name": "broken", "blocks_movement": False,
                                                                          "blocks_sight": False, "terminal": True}
    assert ms.make_breakable(door) == door
    assert ms.preset("proximity_door")["transitions"][2]["follow_up"]["after"]["status"] == "unresolved"
    assert ms.preset("drop_door")["transitions"][0]["motion"]["duration"]["status"] == "unresolved"
    with pytest.raises(ValueError):
        ms.preset("lift")


def test_ids_are_allocated_past_every_id_in_use_and_never_reused():
    mf = copy.deepcopy(MF)
    mf["next_id"] = 3                      # a hand-edited file that went backwards
    new, out = ms.allocate(mf, "trigger")
    assert new == "trigger-14" and out["next_id"] == 15 and mf["next_id"] == 3      # bundle-13 is the highest in use
    assert ms.allocate(MF, "trigger")[0] == "trigger-20"
    with pytest.raises(ValueError):
        ms.allocate(mf, "door")


def bad(mutate, code, where=None):
    mf = copy.deepcopy(MF)
    mutate(mf)
    rep = ms.validate(mf, "Ascent")
    assert code in codes(rep.errors), rep.errors
    if where:
        assert any(e["where"] == where for e in rep.errors if e["code"] == code)


def test_structural_errors():
    bad(lambda m: m["triggers"].append(copy.deepcopy(m["triggers"][0])), "duplicate_id", "trigger-7")
    bad(lambda m: m["triggers"][0]["targets"].append({"feature": "feature-99", "event": "switch"}), "dangling_reference", "trigger-7")
    bad(lambda m: m["routes"][0].update(owner="feature-99"), "dangling_reference")
    bad(lambda m: m["features"][0].update(floors=["floor-99"]), "dangling_reference")
    bad(lambda m: m["bundles"][0]["members"].append("feature-42"), "dangling_reference")
    bad(lambda m: m["features"][0].update(initial_state="ajar"), "bad_state_reference")
    bad(lambda m: m["features"][0]["transitions"][0].update(to="ajar"), "bad_state_reference")
    bad(lambda m: m["features"][0]["transitions"][0].update(guard={"eval": "x"}), "bad_guard")
    bad(lambda m: m["features"][0]["transitions"][0].update(event="motion_complete"), "bad_event")
    bad(lambda m: m["features"][0]["transitions"][0].update(mid_motion="sometimes"), "bad_policy")
    bad(lambda m: m["features"][0]["states"][0]["footprint"]["uv"].__setitem__(0, [4000, 12000]), "bad_coordinates")
    bad(lambda m: m["features"][0]["states"][0]["footprint"]["uv"].__setitem__(0, [float("nan"), 1]), "bad_coordinates")
    bad(lambda m: m["features"][0]["states"][0]["footprint"].update(uv=[[1, 1], [2, 2]]), "bad_coordinates")
    bad(lambda m: m["features"][0]["states"][0]["footprint"].update(type="paint", cells="AAAA"), "bad_paint")
    bad(lambda m: m["features"][0]["states"][0]["sight"][0]["bounds"]["top"].update(value=-1), "bad_value")
    bad(lambda m: m["features"][0]["states"][0]["sight"][0]["bounds"]["bottom"].update(value=5), "bad_dimensions")
    bad(lambda m: m["features"][0]["states"][0]["sight"][0]["bounds"].pop("floor"), "bad_bounds")
    bad(lambda m: m["features"][0]["transitions"][0]["motion"]["duration"].update(unit="ms"), "bad_unit")
    bad(lambda m: m["routes"][0]["endpoints"].pop(), "missing_endpoint")
    bad(lambda m: m["routes"][0]["endpoints"][0].update(uv=None), "missing_endpoint")
    bad(lambda m: m["routes"][0]["directions"][0].update(to="c"), "bad_route")
    bad(lambda m: m["routes"][0]["directions"][0].pop("entry"), "missing_value")
    bad(lambda m: m["floors"][0].update(z_band=[3, 1]), "bad_dimensions")
    bad(lambda m: m["triggers"][2].update(geometry={"type": "point", "uv": [7000, 7000]}), "missing_range")
    bad(lambda m: m.update(features=[{"id": "trigger-1"}]), "bad_id")
    bad(lambda m: m["features"][0]["base_edits"].update(reclassify=[{"source": "everything"}]) if "base_edits" in m["features"][0]
        else m["features"][0].update(base_edits={"reclassify": [{"source": "everything"}]}), "bad_reclassify")
    paint = base64.b64encode(bytes(ms.PAINT_BYTES)).decode()
    ok = copy.deepcopy(MF)
    ok["features"][0]["states"][0]["footprint"] = {"type": "paint", "cells": paint}
    assert ms.validate(ok).ok


def test_incompatible_versions_are_refused_not_migrated():
    for v in (2, None, "1"):
        mf = copy.deepcopy(MF)
        mf["version"] = v
        rep = ms.validate(mf)
        assert codes(rep.errors) == ["incompatible_version"]
    assert ms.check_version([]) is not None and ms.check_version(MF) is None


def test_warnings_for_unresolved_and_suspicious_annotations():
    mf = copy.deepcopy(MF)
    mf["triggers"][0]["targets"] = []
    mf["routes"][0]["endpoints"][1]["floor"] = "floor-1"           # same place, same floor
    mf["features"][1]["rotation"]["pivot"] = None
    mf["triggers"][2]["geometry"] = None
    mf["triggers"][2]["range"] = {"status": "unresolved"}
    mf["features"][2]["initial_state"] = None
    got = {(w["where"], w["code"]) for w in ms.validate(mf).warnings}
    assert ("trigger-7", "no_target") in got
    assert ("route-10", "zero_length") in got
    assert ("feature-2", "motion_incomplete") in got
    assert ("trigger-9", "unresolved_range") in got
    assert ("feature-3", "uncertain_initial_state") in got
    # a rope whose floors are only manual labels is a warning too, never an error
    mf = copy.deepcopy(MF)
    mf["routes"][0]["endpoints"][1]["floor"] = "floor-3"
    mf["routes"][0]["endpoints"][0]["floor"] = {"status": "unresolved"}
    rep = ms.validate(mf)
    assert rep.ok and ("route-10", "zero_length") in {(w["where"], w["code"]) for w in rep.warnings}


def test_known_counts_are_review_warnings_only():
    mf = ms.empty()
    for _ in range(2):
        fid, mf = ms.allocate(mf, "feature")
        mf["features"].append({"id": fid, "category": "drop_doors", **ms.preset("drop_door")})
    rep = ms.validate(mf, "Summit")
    assert rep.ok and ("drop_doors", "count_mismatch") in {(w["where"], w["code"]) for w in rep.warnings}
    for _ in range(1):
        fid, mf = ms.allocate(mf, "feature")
        mf["features"].append({"id": fid, "category": "drop_doors", **ms.preset("drop_door")})
    assert "count_mismatch" not in codes(ms.validate(mf, "Summit").warnings)
    assert [c["expected"] for c in ms.checklist_seed("Summit")] == [3, None]
    assert ms.checklist_seed("Fracture")[0]["expected"] == 1 and ms.checklist_seed("Haven") == []
    assert sorted(ms.CHECKLIST_SEED) == ["Abyss", "Ascent", "Bind", "Breeze", "Corrode", "Fracture", "Haven", "Icebox",
                                         "Lotus", "Pearl", "Split", "Summit", "Sunset"]


def test_a_route_repeating_a_legacy_special_is_flagged():
    specials = [{"a": [1000, 8000], "b": [3000, 8000], "one_way": False}]
    got = {(w["where"], w["code"]) for w in ms.validate(MF, specials=specials).warnings}
    assert ("route-11", "duplicates_special") in got


def test_references_cover_every_reference_type():
    refs = ms.references(MF)
    fields = {f.split(".")[0] for _, f, _ in refs}
    assert {"targets", "owner", "members", "floors", "states", "endpoints", "floor"} <= fields
    assert ("trigger-8", "targets") in ms.referrers(MF, "feature-2")
    assert ms.referrers(MF, "floor-2") == [("feature-3", "floors"), ("trigger-9", "floor"), ("route-10", "endpoints.b.floor")]
    mf = copy.deepcopy(MF)
    mf["features"][1]["parent"] = "feature-1"
    mf["features"][0]["bundle"] = "bundle-13"
    assert ("feature-2", "parent") in ms.referrers(mf, "feature-1") and ("feature-1", "bundle", "bundle-13") in ms.references(mf)


def test_editorial_edits_leave_the_runtime_digest_and_runtime_edits_change_it():
    base = ms.runtime_digest(MF)
    for edit in (lambda m: m["features"][0].update(name="Renamed", notes="x", review="user_reviewed"),
                 lambda m: m["checklist"]["switch_breakable_doors"].update(status="user_reviewed"),
                 lambda m: m.update(next_id=99, image_sha="abc", runtime_digest="whatever"),
                 lambda m: m["features"].reverse(),
                 lambda m: m["triggers"][0].update(name="Other", parser_bindings={"guid": "x"})):
        mf = copy.deepcopy(MF)
        edit(mf)
        assert ms.runtime_digest(mf) == base
    for edit in (lambda m: m["triggers"][0]["targets"][0].update(feature="feature-2"),
                 lambda m: m["features"][0].update(initial_state="closed"),
                 lambda m: m["bundles"][0].update(enabled=True),
                 lambda m: m["routes"][0]["directions"][0]["transit"].update(value=2.5),
                 lambda m: m["features"][0]["transitions"].reverse(),
                 lambda m: m["features"][0]["states"][0]["sight"][0]["bounds"]["top"].update(value=3.0)):
        mf = copy.deepcopy(MF)
        edit(mf)
        assert ms.runtime_digest(mf) != base
    assert ms.editorial_digest(MF) != ms.editorial_digest({**MF, "checklist": {}})


def test_contract_doc_names_a_policy_for_every_consumer():
    sys.path.insert(0, str(HERE))
    import map_feature_consumers

    found = map_feature_consumers.consumers()
    assert len(found) >= 25, "the scan should see the engine's consumers"
    doc = (WEBAPP.parent / "docs" / "superpowers" / "specs" / "2026-10-04-map-features-contract.md").read_text(encoding="utf-8")
    rows = {line.split("|")[1].strip().strip("`"): line for line in doc.splitlines() if line.startswith("| `")}
    missing = sorted(set(found) - set(rows))
    assert not missing, f"consumers without a policy in the contract doc: {missing}"
    policies = ("walk_only", "transport_reachability", "transport_time", "bounded_sight", "base", "freshness")
    for key in found:
        assert any(p in rows[key].split("|")[3] for p in policies), key


def test_the_new_modules_import_only_the_standard_library_and_app_replays():
    # the worker image copies only app/__init__.py, app/replays and app/control (replay_worker/Dockerfile)
    for name in ("map_feature_schema.py", "map_feature_state.py"):
        tree = ast.parse((WEBAPP / "app" / "replays" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            for mod in mods:
                top = mod.split(".")[0]
                assert top in sys.stdlib_module_names or mod.startswith("app.replays") or top == "__future__", (name, mod)


def test_a_banded_floor_without_its_assets_origin_is_warned_about():
    mf = {**ms.empty(), "floors": [
        {"id": "floor-1", "label": "ground", "z_band": [-0.5, 1.0], "height_sha": "a" * 12, "origin_z": -120},
        {"id": "floor-2", "label": "old", "z_band": [-0.5, 1.0], "height_sha": "a" * 12},
        {"id": "floor-3", "label": "odd", "z_band": [-0.5, 1.0], "height_sha": "a" * 12, "origin_z": "low"}]}
    report = ms.validate(mf)
    codes = {(w["where"], w["code"]) for w in report.warnings}
    assert ("floor-2", "unframed_floor") in codes and ("floor-3", "unframed_floor") in codes
    assert not any(where == "floor-1" for where, _ in codes)
