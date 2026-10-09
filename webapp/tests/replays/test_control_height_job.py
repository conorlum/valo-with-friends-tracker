"""The height build as a job (app/control/height_job.py, replay_worker/height_job.py;
docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 2): rounds read one at a time from a
folder, the two checks (which fail closed), the comparison with the asset before, the child's JSON, and the
web app's verifier on what it returns."""

import base64
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from test_control_heights import covered_rounds, level_walk, toy_assets, write_blobs  # noqa: E402

from app.control import geometry as cg  # noqa: E402
from app.control import height_job, height_verify  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from app.replays import height_inputs as hi  # noqa: E402
from app.services import control_heights as ch  # noqa: E402


@pytest.fixture
def checks(tmp_path):
    """A readable check set with no line for the toy map: a real answer, "nothing to check here"."""
    path = tmp_path / "must_block.json"
    path.write_text(json.dumps({"lines": [{"map": "Bind", "viewer": [1, 1, 0], "target": [2, 2, 0], "source": "x"}]}),
                    encoding="utf-8")
    return path


@pytest.mark.parametrize('source', ['missing', 'malformed'])
def test_real_builder_and_verifier_do_not_read_tags(tmp_path, checks, source):
    assets = toy_assets(tmp_path)
    tags = assets / 'tags.json'
    if source == 'missing':
        tags.unlink()
    else:
        tags.write_text('not JSON')
    blobs = write_blobs(tmp_path / 'blobs', covered_rounds())
    out = height_job.run('Ascent', height_job.BlobDir(blobs, 'Ascent'), asset_dir=assets, must_block=checks)
    seen = height_verify.describe(out['asset'], 'Ascent', asset_dir=assets, must_block=checks)
    assert 'error' not in seen and seen['digest'] == out['digest']
    assert ch.integrity(out['digest'], out['report'], seen, hi.must_block_sha(checks)) == []


def test_rounds_are_read_one_at_a_time_and_twice(tmp_path):
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    write_blobs(tmp_path / "blobs", [("match-9", 1, covered_rounds()[0][2])], name="Bind")
    source = height_job.BlobDir(blobs, "Ascent")
    first = [(m, n) for m, n, _ in source]
    assert first == [(f"match-{m}", n) for m in (0, 1) for n in (1, 2, 3)] and source.skipped == 1
    assert source.per_match() == {"match-0": 3, "match-1": 3} and len(source.read) == 6
    assert [(m, n) for m, n, _ in source] == first, "the checks read it again after the build"
    assert len(source) == 7 and all(blob["map"] == "Ascent" for _, _, blob in source)


def test_a_build_runs_from_a_folder_and_its_result_is_what_the_web_app_verifies(tmp_path, checks):
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    out = height_job.run("Ascent", height_job.BlobDir(blobs, "Ascent"), asset_dir=assets, must_block=checks)
    assert out["status"] == "ok" and out["map"] == "Ascent" and len(out["digest"]) == 12 and out["rules"] == hc.rules()
    report = out["report"]
    assert report["ready"] and report["kill_lines"]["passes"] and report["kill_lines"]["qualifying"] == 6
    assert report["must_block"] == {"set": hi.must_block_sha(checks), "lines": 0, "checked": 0, "unchecked": 0,
                                    "blocked": 0, "passes": True, "results": []}
    assert report["rounds"] == 6 and report["matches"] == 2 and report["compare"] is None
    assert report['features']['status'] == 'error' and report['features']['code'] == 'source_failure'
    json.dumps(report)                                   # it is stored as JSON
    seen = height_verify.describe(out["asset"], "Ascent", asset_dir=assets, must_block=checks)
    assert "error" not in seen
    assert (seen["digest"], seen["version"]) == (out["digest"], cf.HEIGHT_VERSION)
    assert (seen["walkable_cells"], seen["supported_cells"], seen["ready"], seen["not_ready"], seen["must_block"]) == (
        report["walkable_cells"], report["supported_cells"], report["ready"], report["not_ready"], report["must_block"])
    assert ch.verify_asset(out["asset"], "Ascent", asset_dir=assets, must_block=checks) == seen, "the same answer from the child process the web app starts"
    assert ch.integrity(out["digest"], report, seen, hi.must_block_sha(checks)) == []
    assert ch.gate(report) == []


def test_a_missing_or_broken_check_set_fails_the_check(tmp_path):
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    for bad in (tmp_path / "missing.json", tmp_path / "broken.json"):
        if bad.name == "broken.json":
            bad.write_text("not json", encoding="utf-8")
        out = height_job.run("Ascent", height_job.BlobDir(blobs, "Ascent"), asset_dir=assets, must_block=bad)
        must = out["report"]["must_block"]
        assert must["passes"] is False and must["set"] is None and "error" in must and must["lines"] == 0
        assert ch.gate(out["report"]) and ch.integrity(out["digest"], out["report"], height_verify.describe(out["asset"], "Ascent", asset_dir=assets, must_block=bad),
                                                       None), "neither half of the gate lets it through"


def test_corrupt_or_foreign_bytes_are_never_an_asset(tmp_path, checks):
    assets = toy_assets(tmp_path)
    out = height_job.run("Ascent", height_job.BlobDir(write_blobs(tmp_path / "blobs", covered_rounds()), "Ascent"),
                         asset_dir=assets, must_block=checks)
    sha = hi.must_block_sha(checks)
    for data in (b"", b"not a zip", out["asset"][:200], out["asset"][::-1]):
        seen = ch.verify_asset(data, "Ascent", asset_dir=assets, must_block=checks)
        assert "error" in seen and ch.integrity(out["digest"], out["report"], seen, sha)
    other = height_job.run("Ascent", height_job.BlobDir(
        write_blobs(tmp_path / "b2", covered_rounds({9: level_walk(320, 328, 6.0, side="B")})), "Ascent"),
        asset_dir=assets, must_block=checks)
    assert other["digest"] != out["digest"]
    swapped = ch.integrity(out["digest"], out["report"], ch.verify_asset(other["asset"], "Ascent", asset_dir=assets, must_block=checks), sha)
    assert any("is not the asset" in r for r in swapped), "a real asset, but not the one the result names"


def _raw_parts(data):
    import io
    import numpy as np

    with np.load(io.BytesIO(data), allow_pickle=False) as raw:
        return {k: raw[k].copy() for k in raw.files}


def _npz(parts):
    import io
    import numpy as np

    out = io.BytesIO()
    np.savez_compressed(out, **parts)
    return out.getvalue()


@pytest.mark.parametrize("damage", ["spread shape", "edge shape", "cell index", "floor index", "missing floor",
                                   "not neighbours", "edge kind", "ground kind", "floor gap", "floor sentinel",
                                   "lossy cast", "support on void"])
def test_structurally_invalid_assets_are_refused_before_the_engine_can_read_them(tmp_path, checks, damage):
    import numpy as np

    assets = toy_assets(tmp_path)
    out = height_job.run("Ascent", height_job.BlobDir(write_blobs(tmp_path / "blobs", covered_rounds()), "Ascent"),
                         asset_dir=assets, must_block=checks)
    parts = _raw_parts(out["asset"])
    a = int(np.flatnonzero(parts["floors"][..., 0].ravel() >= 0)[0])
    b = a + 1
    if damage == "spread shape":
        parts["spread"] = parts["spread"].reshape(-1)
    elif damage == "edge shape":
        parts["edges"] = np.array([a, 0, b, 0, hc.EDGE_STEP], np.int32)
    elif damage in ("cell index", "floor index", "missing floor", "not neighbours", "edge kind"):
        edge = [a, 0, b, 0, hc.EDGE_STEP]
        if damage == "cell index": edge[2] = cg.GRID ** 2 + 50
        if damage == "floor index": edge[3] = hc.MAX_FLOORS
        if damage == "missing floor": edge[3] = 1
        if damage == "not neighbours": edge[2] = a + 3
        if damage == "edge kind": edge[4] = 99
        parts["edges"] = np.array([edge], np.int32)
    elif damage == "ground kind":
        parts["kind"].flat[a] = 99
    elif damage == "floor gap":
        parts["floors"].reshape(-1, hc.MAX_FLOORS)[a] = [0, -1, 40]
    elif damage == "floor sentinel":
        parts["floors"].reshape(-1, hc.MAX_FLOORS)[a, 2] = -2
    elif damage == "lossy cast":
        parts["floors"] = parts["floors"].astype(np.int32)
    else:
        parts["supported"][0, 0] = True
    seen = height_verify.describe(_npz(parts), "Ascent", asset_dir=assets, must_block=checks)
    assert "error" in seen, (damage, seen)
    assert ch.integrity(out["digest"], out["report"], seen, hi.must_block_sha(checks))


def test_reports_cannot_invent_map_size_or_hide_an_unresolved_area(tmp_path, checks):
    import numpy as np

    assets = toy_assets(tmp_path)
    out = height_job.run("Ascent", height_job.BlobDir(write_blobs(tmp_path / "blobs", covered_rounds()), "Ascent"),
                         asset_dir=assets, must_block=checks)
    seen = height_verify.describe(out["asset"], "Ascent", asset_dir=assets, must_block=checks)
    forged = {**out["report"], "walkable_cells": seen["supported_cells"], "supported": 1.0}
    assert seen["walkable_cells"] >= seen["supported_cells"]
    # Make a self-consistent but false denominator even if every toy cell happens to be supported.
    forged["walkable_cells"] *= 2
    forged["supported"] = forged["supported_cells"] / forged["walkable_cells"]
    assert any("walkable" in r for r in ch.integrity(out["digest"], forged, seen, hi.must_block_sha(checks)))
    parts = _raw_parts(out["asset"])
    parts["floors"][24:27, 24:29] = -1  # 15 unresolved cells beside a two-floor cell
    parts["supported"][24:27, 24:29] = False
    parts["unresolved"][24:27, 24:29] = True
    parts["kind"][24:27, 24:29] = hc.KIND_NONE
    parts["floors"][24, 23, 1] = 40
    # Remove links to the cells whose floors were removed, to keep this a structurally valid asset.
    removed = set((y * cg.GRID + x) for y in range(24, 27) for x in range(24, 29))
    parts["edges"] = np.array([e for e in parts["edges"] if int(e[0]) not in removed and int(e[2]) not in removed],
                              np.int32).reshape(-1, 5)
    seen = height_verify.describe(_npz(parts), "Ascent", asset_dir=assets, must_block=checks)
    assert "error" not in seen and seen["supported"] >= cf.HEIGHT_SUPPORTED_MIN
    assert not seen["ready"] and any("unresolved" in r for r in seen["not_ready"])
    forged = {**out["report"], "supported_cells": seen["supported_cells"], "supported": seen["supported"],
              "ready": True, "not_ready": []}
    assert any("readiness" in r for r in ch.integrity(seen["digest"], forged, seen, hi.must_block_sha(checks)))
    honest = {**forged, "ready": seen["ready"], "not_ready": seen["not_ready"]}
    assert ch.integrity(seen["digest"], honest, seen, hi.must_block_sha(checks)) == []
    assert ch.gate(honest)  # valid failing build is rejected by policy, not treated as corrupt


def test_empty_must_block_evidence_cannot_pass_a_nonempty_maps_set(tmp_path, checks):
    assets = toy_assets(tmp_path)
    out = height_job.run("Ascent", height_job.BlobDir(write_blobs(tmp_path / "blobs", covered_rounds()), "Ascent"),
                         asset_dir=assets, must_block=checks)
    checks.write_text(json.dumps({"lines": [{"map": "Ascent", "viewer": [100, 100, None],
                                             "target": [120, 100, None], "source": "needs heights"}]}), encoding="utf-8")
    sha = hi.must_block_sha(checks)
    seen = height_verify.describe(out["asset"], "Ascent", asset_dir=assets, must_block=checks)
    assert "error" not in seen and seen["must_block"]["lines"] == 1 and not seen["must_block"]["passes"]
    forged = {**out["report"], "must_block": {"set": sha, "lines": 0, "checked": 0, "unchecked": 0,
                                            "blocked": 0, "passes": True}}
    assert any("must-block" in r for r in ch.integrity(out["digest"], forged, seen, sha))


def test_the_report_compares_with_the_asset_before(tmp_path, checks):
    assets = toy_assets(tmp_path)
    before = height_job.run("Ascent", height_job.BlobDir(write_blobs(tmp_path / "a", covered_rounds()), "Ascent"),
                            asset_dir=assets, must_block=checks)
    (tmp_path / "before.npz").write_bytes(before["asset"])
    raised = covered_rounds({9: level_walk(320, 328, 6.0, side="B")})        # a platform 6 m up, two cells
    after = height_job.run("Ascent", height_job.BlobDir(write_blobs(tmp_path / "b", raised), "Ascent"),
                           previous=tmp_path / "before.npz", asset_dir=assets, must_block=checks)
    assert after["report"]["compare"] == {"previous": before["digest"], "cells_gained": 0, "cells_lost": 0,
                                          "cells_moved": 0}, "the ground didn't move: the platform is an upper floor"
    old = hc.load_asset(tmp_path / "before.npz")
    new = hc.load_asset(tmp_path / "before.npz")
    new.floors[20, 20, 0] += 6                    # the ground 0.6 m higher in one cell
    new.floors[21, 21, 0] = -1                    # one cell lost
    assert height_job.compare(new, old) == {"previous": old.digest, "cells_gained": 0, "cells_lost": 1, "cells_moved": 1}
    assert height_job.compare(new, None) is None


def child_task(tmp_path, blobs, assets, checks, monkeypatch, replays=None):
    """A task as the worker's server writes it, with the manifest the web app would send for these blobs."""
    monkeypatch.setattr(hi, "MUST_BLOCK", checks)
    monkeypatch.setattr(hi, "CONTROL_DIR", assets)
    monkeypatch.setattr(cg, "ASSET_DIR", assets)
    geometry = hi.geometry_identity("Ascent")
    manifest = hi.manifest("Ascent", replays or [["match-0", "p.c11", "a" * 64, 3], ["match-1", "p.c11", "b" * 64, 3]],
                           geometry, hi.must_block_sha())
    return {"key": hi.key(manifest), "map": "Ascent", "dir": str(blobs), "manifest": manifest}


def test_the_child_answers_in_json_and_proves_what_it_was_built_from(tmp_path, checks, monkeypatch):
    from replay_worker import height_job as child

    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    task = child_task(tmp_path, blobs, assets, checks, monkeypatch)
    out = child.run(task)
    assert out["status"] == "ok" and out["key"] == task["key"] and out["map"] == "Ascent" and out["rounds"] == 6
    assert out["inputs_sha"] == hi.digest(task["manifest"])
    json.dumps(out)
    assert height_verify.describe(base64.b64decode(out["asset"]), "Ascent", asset_dir=assets, must_block=checks)["digest"] == out["digest"]


@pytest.mark.parametrize("name, spoil", [
    ("a match with a round missing", lambda t, blobs: (blobs / "match-1" / "3.json.gz").unlink()),
    ("a match the manifest doesn't name", lambda t, blobs: (blobs / "match-1").rename(blobs / "match-7")),
    ("other rules on this worker", lambda t, blobs: t["manifest"].update(height_rules=t["manifest"]["height_rules"] + 1)),
    ("another walk mask on this worker", lambda t, blobs: t["manifest"]["geometry"].update(walk="x" * 12)),
    ("another check set on this worker", lambda t, blobs: t["manifest"].update(must_block="z" * 12)),
    ("a manifest for another map", lambda t, blobs: t["manifest"].update(map="Bind")),
    ("no manifest", lambda t, blobs: t.pop("manifest")),
    ("no rounds folder", lambda t, blobs: t.update(dir=str(blobs / "nowhere"))),
])
def test_the_child_refuses_inputs_that_are_not_the_manifests(tmp_path, checks, monkeypatch, name, spoil):
    from replay_worker import height_job as child

    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    task = child_task(tmp_path, blobs, assets, checks, monkeypatch)
    spoil(task, blobs)
    out = child.run(task)
    assert out["status"] == "failed" and out["error_kind"] == "inputs" and "asset" not in out, name


def test_a_build_lists_the_tagged_features_that_no_longer_fit(tmp_path, monkeypatch):
    from app.control import features
    from tests.replays.control_toys import HALL, toy_heights

    geo = toy_heights("FeatJob", [HALL], upper=[((240, 96, 288, 296), 4.0)])     # a bridge 4 m over the ground
    assets = tmp_path / "assets"
    assets.mkdir()

    def tags(floors):
        mf = {"version": 1, "floors": floors,
              "features": [{"id": "feature-1", "floors": ["floor-1"]}, {"id": "feature-2", "floors": ["floor-2"]}]}
        (assets / "tags.json").write_text(json.dumps({"maps": {"FeatJob": {"map_features": mf}}}), encoding="utf-8")

    seen = []
    monkeypatch.setattr(features, "state_problems",
                        lambda g, mf, f: seen.append(f["id"]) or (["no floor in its band"] if f["id"] == "feature-2" else []))
    tags([{"id": "floor-1", "z_band": [-0.5, 1.0], "height_sha": "old", "origin_z": 0}, {"id": "floor-2", "z_band": [9, 10]}])
    report = height_job.features_pending('FeatJob', geo, assets)
    assert report['status'] == 'ok' and report['counts']['total_tagged'] == 2
    assert [f['id'] for f in report['features']] == ['feature-1', 'feature-2']
    assert all(f['runtime'] == 'disabled' for f in report['features'])
    assert not seen, 'deprecated floor labels and disabled runtime states are not an eligibility gate'
    (assets / "tags.json").write_text(json.dumps({"maps": {}}), encoding="utf-8")
    assert height_job.features_pending('FeatJob', geo, assets)['code'] == 'source_failure'
    (assets / "tags.json").unlink()
    assert height_job.features_pending('FeatJob', geo, assets)['code'] == 'source_failure'
