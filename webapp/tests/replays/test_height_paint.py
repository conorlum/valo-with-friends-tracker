"""Height brushes use strict, disjoint fine-cell masks, independent of tags."""

import base64
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from app.control import geometry as cg
from app.control import height_build as hb, heights as hc, height_refinement as hr
from app.replays import height_inputs as hi
from tests.replays.test_control_heights import (
    covered_rounds, level_walk, ledge_geo, standing, toy_assets, write_blobs, command,
)
from tests.replays.control_toys import TOY_Z0


def write_paint(folder, document):
    (folder / "height_paint.json").write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture
def cells():
    mask = np.zeros((256, 256), dtype=bool)
    mask[0, 0] = mask[2, 3] = mask[17, 128] = mask[255, 255] = True
    return mask


def test_no_file_means_no_paint(tmp_path):
    assert cg.load_height_paint("Toy", tmp_path) == (None, None)


@pytest.mark.parametrize("maps", [{}, {"Toy": {}}, {"Other": {}}])
def test_no_map_or_brush_means_no_paint(tmp_path, maps):
    write_paint(tmp_path, {"maps": maps})
    assert cg.load_height_paint("Toy", tmp_path) == (None, None)


def test_ground_cells_and_canonical_sha_match_manifest(tmp_path, cells):
    entry = {"ground": cg.pack_paint(cells)}
    write_paint(tmp_path, {"maps": {"Toy": entry}})
    masks, sha = cg.load_height_paint("Toy", tmp_path)
    assert set(masks) == {"ground"}
    assert masks["ground"].dtype == np.bool_
    np.testing.assert_array_equal(masks["ground"], cells)
    expected = hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
    assert sha == expected and len(sha) == 12
    (tmp_path / "index.json").write_text(json.dumps({"maps": {
        "Toy": {"sight_sha": "s", "walk_sha": "w"}}}), encoding="utf-8")
    (tmp_path / "maps.json").write_text('{"Toy": {"xMultiplier": 0.00007}}', encoding="utf-8")
    assert hi.geometry_identity("Toy", tmp_path, tmp_path / "maps.json")["height_paint"] == sha


def test_disjoint_brushes_are_allowed(tmp_path, cells):
    other = np.zeros_like(cells)
    other[0, 1] = True
    write_paint(tmp_path, {"maps": {"Toy": {
        "open": cg.pack_paint(cells), "ground": cg.pack_paint(other), "top": cg.pack_paint(~(cells | other))}}})
    masks, sha = cg.load_height_paint("Toy", tmp_path)
    assert sha
    np.testing.assert_array_equal(masks["open"], cells)
    np.testing.assert_array_equal(masks["ground"], other)
    np.testing.assert_array_equal(masks["top"], ~(cells | other))


def test_overlap_is_rejected(tmp_path, cells):
    entry = {brush: cg.pack_paint(cells) for brush in ("open", "ground")}
    write_paint(tmp_path, {"maps": {"Toy": entry}})
    with pytest.raises(cg.GeometryError, match="overlap"):
        cg.load_height_paint("Toy", tmp_path)


@pytest.mark.parametrize("payload", [
    "", "bad base64!", "!" + base64.b64encode(bytes(8192)).decode() + "!",
    base64.b64encode(bytes(8191)).decode(), base64.b64encode(bytes(8193)).decode(), "é",
])
def test_bad_payload_is_rejected(tmp_path, payload):
    write_paint(tmp_path, {"maps": {"Toy": {"ground": payload}}})
    with pytest.raises(cg.GeometryError):
        cg.load_height_paint("Toy", tmp_path)


@pytest.mark.parametrize("text", [
    "not json", "null", "[]", "{}", '{"maps": []}', '{"maps": {"Toy": []}}',
    '{"maps": {"Toy": {"ground": null}}}', '{"maps": {"Toy": {"unknown": ""}}}',
    '{"maps": {}, "other": {}}',
])
def test_bad_document_is_rejected(tmp_path, text):
    (tmp_path / "height_paint.json").write_text(text, encoding="utf-8")
    with pytest.raises(cg.GeometryError):
        cg.load_height_paint("Toy", tmp_path)


def test_unreadable_file_is_rejected(tmp_path):
    (tmp_path / "height_paint.json").mkdir()
    with pytest.raises(cg.GeometryError):
        cg.load_height_paint("Toy", tmp_path)


def test_all_zero_payload_means_no_paint(tmp_path):
    write_paint(tmp_path, {"maps": {"Toy": {
        brush: cg.pack_paint(np.zeros((256, 256), dtype=bool)) for brush in ("open", "ground", "top")}}})
    assert cg.load_height_paint("Toy", tmp_path) == (None, None)


def test_geometry_has_unattached_height_paint_by_default():
    assert cg.Geometry.__dataclass_fields__["height_paint"].default is None
    assert cg.Geometry.__dataclass_fields__["height_paint_sha"].default is None


BASELINE = Path(__file__).parents[1] / "fixtures/control/height_paint_baseline.json"


def npz_content_sha(path) -> str:
    """sha256 over every array saved in the asset (name, dtype, shape, bytes; names sorted). Not the file's own
    bytes: an .npz is deflated, and zlib writes different bytes on Windows and Linux for the same arrays."""
    h = hashlib.sha256()
    with np.load(path) as z:
        for name in sorted(z.files):
            a = np.ascontiguousarray(z[name])
            h.update(f"{name}|{a.dtype.str}|{a.shape}|".encode("ascii") + a.tobytes())
    return h.hexdigest()


def ledge_build_inputs():
    import copy
    return copy.copy(ledge_geo()), covered_rounds({9: level_walk(240, 272, 4.0, side="B")})


def paint_mask(*cells):
    mask = np.zeros((256, 256), bool)
    for x, y in cells:
        mask[y, x] = True
    return mask


def evidence(cell, floors):
    # The same evidence shape emitted by height_refinement.analyze.
    y, x = divmod(cell, 256)
    return {"cell": cell, "bbox_px": [x*4, y*4, x*4+4, y*4+4],
            "floors_world_dm": [[z, 3] for z in floors], "kind": hc.KIND_STANDS,
            "reason": None if floors else "no samples", "observations": len(floors)}


@pytest.mark.parametrize("brush,own,area,touch,expected", [
    ("ground", [42, 82], None, [], [42]),
    ("top", [42, 82], None, [], [82]),
    ("open", [82], [22, 82], [], [22, 82]),
    ("ground", [], None, [2, 4, 6], [4]),
    ("open", [], [82], [22], [22, 82]),
    ("open", [], [82], [70], []),
    ("open", [82], [82, 110], [], []),
    ("open", [82], [82, 90], [22], [22, 82]),
    ("ground", [], [2, 6], [], [2]),
    ("top", [], [2, 6], [], [6]),
    ("open", [82], None, [62], [62, 82]),
    ("open", [82], None, [63], []),
    ("open", [22, 82, 122], None, [], [22, 122]),
    ("ground", [], None, [], []),
    ("top", [], None, [], []),
    ("open", [82], None, [], []),
    ("ground", [], None, [2, 6], [2]),
    ("top", [], None, [2, 6], [2]),
])
def test_apply_donors_and_exact_counts(brush, own, area, touch, expected):
    from app.control import height_paint as hp
    cell = 10*256+10
    rows = [evidence(cell, own)]
    cells = [(10, 10)]
    if area is not None:
        cells.append((11, 11))
        rows.append(evidence(cell+257, area))
    for dx, z in enumerate(touch):
        rows.append(evidence(9*256+9+dx, [z]))
    original = {r["cell"]: list(r["floors_world_dm"]) for r in rows}
    result = hp.apply(rows, {brush: paint_mask(*cells)}, {})
    assert rows[0]["floors_world_dm"] == [[z, 0] for z in expected]
    unresolved = sum(not r["floors_world_dm"] for r in rows[:len(cells)])
    assert result == {**dict.fromkeys(("open", "ground", "top"), 0),
                      brush: len(cells)-unresolved, "unresolved": unresolved}
    for row in rows[len(cells):]:
        assert row["floors_world_dm"] == original[row["cell"]]


def test_apply_uses_coarse_touching_floor():
    from app.control import height_paint as hp
    rows = [evidence(10*256+10, []), evidence(10*256+11, [])]
    counts = hp.apply(rows, {"ground": paint_mask((10, 10), (11, 10))}, {4*128+5: [42]})
    assert [r["floors_world_dm"] for r in rows] == [[[42, 0]], [[42, 0]]]
    assert counts == {"open": 0, "ground": 2, "top": 0, "unresolved": 0}


def test_painted_parents_are_walkable():
    from app.control import height_paint as hp
    walk = np.zeros((128, 128), bool)
    walk[5, 5] = True
    assert hp.painted_parents({"ground": paint_mask((10, 10), (30, 30))}, walk) == {5*128+5}


@pytest.mark.parametrize("donor", ["painted", "neighbour", "coarse_footprint", "coarse_parent"])
def test_apply_ignores_unwalkable_targets_and_donors(donor):
    import copy
    from app.control import height_paint as hp
    target, blocked = 10*256+10, 10*256+11
    rows = [evidence(target, [])]
    coarse = {}
    mask = paint_mask((10, 10))
    walk = np.ones((256, 256), bool)
    if donor.startswith("coarse"):
        # The touching footprint belongs to an unrefined parent.
        coarse[4*128+5] = [42]
        walk[8:10, 10:12] = False
        if donor == "coarse_footprint":
            walk[8, 10:12] = True  # parent eligible, touching children ineligible
    else:
        rows.append(evidence(blocked, [42]))
        walk.ravel()[blocked] = False
        if donor == "painted":
            mask.ravel()[blocked] = True
    original = copy.deepcopy(rows)
    control = copy.deepcopy(rows)
    hp.apply(control, {"ground": mask}, coarse)
    assert control[0]["floors_world_dm"] == [[42, 0]]
    counts = hp.apply(rows, {"ground": mask}, coarse, walk=walk)
    assert rows[0]["floors_world_dm"] == []
    assert counts == {"open": 0, "ground": 0, "top": 0, "unresolved": 1}
    assert rows[1:] == original[1:]


def test_painted_parents_ignore_unwalkable_fine_paint():
    from app.control import height_paint as hp
    walk = np.ones((128, 128), bool)
    fine_walk = np.ones((256, 256), bool)
    fine_walk[10, 10] = False
    assert hp.painted_parents({"ground": paint_mask((10, 10))}, walk, fine_walk) == set()


@pytest.mark.slow
@pytest.mark.parametrize("paint_blocked", [False, True])
@pytest.mark.parametrize("sample_walkable", [False, True])
@pytest.mark.parametrize("blocked_rows_walkable", [0, 2])
def test_partial_parent_paint_uses_only_children_reaching_asset(
        paint_blocked, sample_walkable, blocked_rows_walkable):
    from tests.replays.control_toys import TOY_SCALE, height_rounds
    walk_px = np.zeros((1024, 1024), bool)
    walk_px[200:208, 200:208] = True
    walk_px[200:204, 200:204] = False
    walk_px[200:200+blocked_rows_walkable, 200:204] = True
    geo = cg.geometry_from_masks("PartialPaint", ~walk_px, walk_px, TOY_SCALE, None)
    assert geo.walk[25, 25]  # three eligible children in a walkable parent
    rounds = height_rounds(lambda m, n: {
        0: standing(202, 202, 4.0),
        **({1: standing(206, 202, 0.0)} if sample_walkable else {}),
    })
    found, _, round_match, _ = hb.all_ground(rounds, geo)
    previous = hr.history(geo.name, [25*128+25])
    analyzed = hr.analyze(geo, found, [], round_match, {}, {}, previous)
    assert analyzed["children"][0]["floors_world_dm"] == [[TOY_Z0+40, 0]]
    paint_cells = [(51, 50), (50, 50)] if paint_blocked else [(51, 50)]
    geo.height_paint = {"ground": paint_mask(*paint_cells)}
    built = hb.build(rounds, geo, refinement_history=previous)
    target = int(np.flatnonzero(built.asset.fine_cells == 50*256+51)[0])
    blocked = int(np.flatnonzero(built.asset.fine_cells == 50*256+50)[0])
    assert (built.asset.fine_floors[blocked] == -1).all()
    assert built.report["painted"] == {"open": 0, "ground": int(sample_walkable), "top": 0}
    assert built.report["paint_unresolved"] == int(not sample_walkable)
    assert built.asset.fine_unresolved[target] == (not sample_walkable)
    assert built.asset.fine_supported[target] == sample_walkable
    expected = [TOY_Z0] if sample_walkable else []
    floors = built.asset.fine_floors[target]
    assert (floors[floors >= 0] + built.asset.origin_z).tolist() == expected


def test_donors_exclude_other_brushes_refined_parent_and_grid_wrap():
    from app.control import height_paint as hp
    rows = [evidence(0, []), evidence(1, [82]), evidence(65535, [42])]
    counts = hp.apply(rows, {"ground": paint_mask((0, 0)), "top": paint_mask((1, 0))}, {0: [22]})
    assert rows[0]["floors_world_dm"] == []
    assert rows[1]["floors_world_dm"] == [[82, 0]]
    assert counts == {"open": 0, "ground": 0, "top": 1, "unresolved": 1}


@pytest.mark.slow
def test_unpainted_ledge_matches_prechange_baseline(tmp_path):
    geo, rounds = ledge_build_inputs()
    built = hb.build(rounds, geo, refinement_history=hr.history(geo.name))
    path = tmp_path / "ledge.npz"
    hc.save_asset(path, built.asset)
    baseline = json.loads(BASELINE.read_text())
    assert npz_content_sha(path) == baseline["ledge_content_sha256"]
    assert json.loads(json.dumps(built.report)) == baseline["ledge_report"]
    assert "painted" not in built.report


@pytest.mark.slow
def test_unpainted_preview_matches_prechange_baseline(tmp_path, monkeypatch):
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    checks = tmp_path / "checks.json"
    checks.write_text('{"lines": []}')
    monkeypatch.setattr(command, "MUST_BLOCK", checks)
    monkeypatch.setattr(command, "picture_path", lambda name: tmp_path / "picture.png")
    out = tmp_path / "preview"
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs), "--preview", "--out", str(out)],
                        asset_dir=assets) == 0
    baseline = json.loads(BASELINE.read_text())
    assert npz_content_sha(out / "Ascent.height.npz") == baseline["preview_content_sha256"]
    assert json.loads((out / "Ascent.height.json").read_text())["height"] == baseline["preview_report"]


@pytest.mark.slow
@pytest.mark.parametrize("brush,expected", [("ground", [0]), ("top", [40]), ("open", [0, 40])])
def test_brush_asset_roundtrip_and_support(tmp_path, brush, expected):
    geo, rounds = ledge_build_inputs()
    mask = np.zeros((256, 256), bool)
    mask[50, 61:69] = True
    parents = {25*128+x for x in range(30, 35)}
    before = hb.build(rounds, geo, refinement_history=hr.history(geo.name, parents))
    geo.height_paint = {brush: mask}
    built = hb.build(rounds, geo, refinement_history=hr.history(geo.name))
    assert parents <= set(built.report["refinement"]["history"]["parents"])
    assert built.report["painted"] == {**dict.fromkeys(("open", "ground", "top"), 0), brush: 8}
    assert built.report["paint_unresolved"] == 0
    np.testing.assert_array_equal(built.asset.fine_supported, before.asset.fine_supported)
    path = tmp_path / "paint.npz"
    hc.save_asset(path, built.asset)
    back = hc.load_asset(path)
    for i, c in enumerate(back.fine_cells):
        if mask.ravel()[c]:
            assert (back.fine_floors[i][back.fine_floors[i] >= 0] + back.origin_z).tolist() == [TOY_Z0+z for z in expected]
            assert back.fine_kind[i] == hc.KIND_FILLED
            assert not back.fine_unresolved[i]
    cg.attach_heights(geo, back)
    geo.height_paint = None
    later = hb.build(rounds, geo, refinement_history=built.report["refinement"]["history"])
    assert later.report["refinement"]["history"] == built.report["refinement"]["history"]


def test_paint_requires_history():
    geo, rounds = ledge_build_inputs()
    geo.height_paint = {"ground": paint_mask((60, 51))}
    with pytest.raises(ValueError, match="height paint needs a refinement history"):
        hb.build(rounds, geo)


@pytest.mark.slow
def test_unknown_ground_is_resolved_without_gaining_support():
    import copy
    from app.control.adaptive import area_readiness
    geo, rounds = ledge_build_inputs()
    geo = copy.copy(geo)
    previous = hr.history(geo.name, [25*128+31])
    before = hb.build(rounds, geo, refinement_history=previous)
    geo.height_paint = {"ground": paint_mask((62, 51), (63, 51))}
    built = hb.build(rounds, geo, refinement_history=previous)
    indices = np.isin(built.asset.fine_cells, [51*256+62, 51*256+63])
    assert before.asset.fine_unresolved[indices].all()
    assert not built.asset.fine_unresolved[indices].any()
    assert not built.asset.fine_supported[indices].any()
    candidate = cg.attach_heights(copy.copy(geo), built.asset)
    assert area_readiness(candidate, built.asset)["unresolved_area_px2"] == before.report["unresolved_area_px2"]-32


@pytest.mark.slow
def test_ground_paint_clears_a_kill_line_across_the_ledge_strip():
    import copy
    from tests.replays.test_control_heights import kill_round
    geo, rounds = ledge_build_inputs()
    before = hb.build(rounds, geo, refinement_history=hr.history(geo.name))
    mask = np.zeros((256, 256), bool)
    mask[50, 61:69] = True
    geo.height_paint = {"ground": mask}
    after = hb.build(rounds, geo, refinement_history=hr.history(geo.name))
    kills = [kill_round([(2.0, 0, 5)], {0: standing(248, 204, 4.0),
                                      5: standing(284, 204, 0.0, "B")})]
    blocked = hb.kill_line_check(kills, cg.attach_heights(copy.copy(geo), before.asset))
    clear = hb.kill_line_check(kills, cg.attach_heights(copy.copy(geo), after.asset))
    assert (blocked["qualifying"], blocked["blocked"]) == (1, 1)
    assert (clear["qualifying"], clear["blocked"]) == (1, 0)


@pytest.mark.slow
def test_painted_preview_equals_worker_with_cover(tmp_path, monkeypatch):
    import io
    from app.control import height_job, height_verify
    from app.services import control_heights as ch
    assets = toy_assets(tmp_path)
    cover = np.zeros((1024, 1024), bool)
    cover[200:208, 280:288] = True
    cg.write_mask_png(assets / "Ascent.cover.png", cover)
    index = json.loads((assets / "index.json").read_text())
    index["maps"]["Ascent"]["cover_sha"] = hashlib.sha256(np.packbits(cover).tobytes()).hexdigest()[:12]
    (assets / "index.json").write_text(json.dumps(index))
    mask = paint_mask((62, 50), (63, 50))
    write_paint(assets, {"maps": {"Ascent": {"ground": cg.pack_paint(mask)}}})
    _, rounds = ledge_build_inputs()
    blobs = write_blobs(tmp_path / "blobs", rounds)
    checks = tmp_path / "checks.json"
    checks.write_text('{"lines": []}')
    monkeypatch.setattr(command, "MUST_BLOCK", checks)
    monkeypatch.setattr(command, "picture_path", lambda name: tmp_path / "picture.png")
    worker = height_job.run("Ascent", height_job.BlobDir(blobs, "Ascent"), asset_dir=assets, must_block=checks)
    out = tmp_path / "preview"
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs), "--preview", "--out", str(out)],
                        asset_dir=assets) == 0
    asset = hc.load_asset(out / "Ascent.height.npz")
    report = json.loads((out / "Ascent.height.json").read_text())["height"]
    assert asset.digest == worker["digest"]
    assert report == worker["report"]
    assert asset.digest == hc.load_asset(io.BytesIO(worker["asset"])).digest
    indices = np.isin(asset.fine_cells, np.flatnonzero(mask))
    assert (asset.fine_floors[indices, 0]+asset.origin_z == TOY_Z0).all()
    assert (asset.fine_floors[indices, 1:] == -1).all()
    seen = height_verify.describe(worker["asset"], "Ascent", asset_dir=assets, must_block=checks)
    assert "error" not in seen
    assert ch.integrity(worker["digest"], report, seen, hi.must_block_sha(checks)) == []


@pytest.mark.slow
def test_open_without_low_donor_saves_loads_and_verifies(tmp_path):
    import io
    from app.control import height_job, height_verify
    assets = toy_assets(tmp_path)
    mask = paint_mask((50, 50))
    write_paint(assets, {"maps": {"Ascent": {"open": cg.pack_paint(mask)}}})
    checks = tmp_path / "checks.json"
    checks.write_text('{"lines": []}')
    built = height_job.run("Ascent", covered_rounds(), asset_dir=assets, must_block=checks)
    asset = hc.load_asset(io.BytesIO(built["asset"]))
    index = list(asset.fine_cells).index(50*256+50)
    assert asset.fine_unresolved[index] and not asset.fine_supported[index]
    assert (asset.fine_floors[index] == -1).all()
    assert built["report"]["paint_unresolved"] == 1
    cg.attach_heights(cg.load_base_geometry("Ascent", assets), asset)
    # Verification consumes the finished asset, without reading paint.
    (assets / "height_paint.json").write_text("invalid JSON")
    seen = height_verify.describe(built["asset"], "Ascent", asset_dir=assets, must_block=checks)
    assert "error" not in seen
