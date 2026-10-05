"""The height viewer (scripts/height_viewer.py, height_viewer_core.js; the plan
docs/superpowers/plans/2026-10-05-height-viewer.md): the page's payload is the asset's own numbers, its
unresolved areas and blocking flags match height_build.readiness, and the core's comparisons, same-height
mask, drops and colours match the build's rules. The JS checks need Node; they skip without it."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(WEBAPP / "scripts"))

import height_viewer  # noqa: E402

from app.control import geometry as cg  # noqa: E402
from app.control import height_build as hb  # noqa: E402
from app.control import heights as hc  # noqa: E402

GRID, MAXF = cg.GRID, hc.MAX_FLOORS
MAP = "Ascent"   # any map with committed sight/walk masks and a minimap; the heights below are synthetic


def cell(y, x):
    return y * GRID + x


def synthetic(extra_unresolved=()):
    """A toy asset: ground 1.0 m on rows 10-19 x cols 10-29, a two-floor cell at (15, 20) (1.0 m and 4.0 m),
    a 13-cell unresolved run diagonally touching it (blocks), a 12-cell run touching it (doesn't), and a
    13-cell run far away (doesn't). Cells (12, 10) and (12, 11) are filled, not supported."""
    floors = np.full((GRID, GRID, MAXF), -1, np.int16)
    floors[10:20, 10:30, 0] = 10
    floors[15, 20, 1] = 40
    unresolved = np.zeros((GRID, GRID), bool)
    unresolved[16, 21:34] = True          # 13 cells; (16, 21) touches (15, 20) diagonally
    unresolved[14, 21:33] = True          # 12 cells; touches, but not over UNRESOLVED_MAX
    unresolved[60, 60:73] = True          # 13 cells, nowhere near a two-floor cell
    for y, x in extra_unresolved:
        unresolved[y, x] = True
    floors[unresolved] = -1
    supported = floors[..., 0] >= 0
    supported[12, 10] = supported[12, 11] = False
    spread = np.where(floors >= 0, 1, -1).astype(np.int16)
    meta = {"origin_z": 10, "matches": 5, "rounds": 100, "walk_sha": "not-the-current"}
    return hc.HeightAsset(floors, spread, supported, unresolved, np.zeros((0, 4), np.int32), meta)


def report_for(asset):
    why = {int(c): "neighbours disagree" for c in np.flatnonzero(asset.unresolved.ravel())}
    return {"unresolved_areas": hb.unresolved_areas(why), "supported": 0.707, "visited": 0.87, "matches": 5,
            "rounds": 100, "ready": False, "not_ready": ["1 unresolved area(s) ..."],
            "kill_lines": {"qualifying": 667, "blocked": 1, "share": 0.0015, "passes": True,
                           "examples": [{"match": "eae6774e", "round": 16, "t": 57.54, "killer_px": [190, 512],
                                         "victim_px": [168, 325], "z": [1.8, 4.0]}]}}


def wrap(path, report):
    """The preview .json's shape (build_control_heights.index_entry): the report under the asset's digest."""
    return {"height_sha": hc.load_asset(path).digest, "height": report}


def walk_cells():
    walk_px = cg.read_mask_png(cg.ASSET_DIR / f"{MAP}.walk.png")
    return walk_px.reshape(GRID, cg.CELL, GRID, cg.CELL).mean((1, 3)) > 0.5


@pytest.fixture
def saved(tmp_path):
    asset = synthetic()
    path = tmp_path / f"{MAP}.height.npz"
    hc.save_asset(path, asset)
    return asset, path


def test_payload_carries_the_assets_numbers(saved):
    asset, path = saved
    p = height_viewer.map_payload(MAP, path, wrap(path, report_for(asset)), "preview")
    assert len(p["floors"]) == GRID * GRID * MAXF
    assert p["floors"][cell(15, 20) * MAXF: cell(15, 20) * MAXF + MAXF] == [10, 40, -1]
    assert p["origin_z"] == 10 and p["max_floors"] == MAXF and p["step_up_m"] == hc.STEP_UP_M
    assert p["height_sha"] == hc.load_asset(path).digest
    assert p["walk_stale"] is True                       # meta says "not-the-current"
    assert p["summary"]["source"] == "report" and p["report_note"] is None
    assert p["summary"]["matches"] == 5 and p["summary"]["supported"] == 0.707
    assert p["kill_lines"][0]["z"] == [1.8, 4.0]
    assert p["image"] and p["cell_m"] > 0


def test_blocking_matches_readiness(saved):
    asset, path = saved
    found = height_viewer.areas(asset, report_for(asset))
    blocking = [a for a in found if a["blocking"]]
    assert [a["size"] for a in blocking] == [13]
    assert found[0]["blocking"]                           # blocking areas first
    assert cell(16, 21) in blocking[0]["cells"]
    _, not_ready = hb.readiness(asset.supported.ravel(), asset.unresolved.ravel(), asset.floor_count().ravel(),
                                int((asset.floors[..., 0] >= 0).sum()))
    assert any("1 unresolved area(s) larger than" in r and "largest 13" in r for r in not_ready)


def test_areas_take_reasons_from_the_report(saved):
    asset, _ = saved
    found = height_viewer.areas(asset, report_for(asset))
    assert all(a["why"] == {"neighbours disagree": a["size"]} for a in found)
    assert all(a["why"] == {} for a in height_viewer.areas(asset, None))


def recomputed(asset):
    """What the build's own rule says for this asset against the current walk mask."""
    return hb.readiness(asset.supported.ravel(), asset.unresolved.ravel(), asset.floor_count().ravel(),
                        int(walk_cells().sum()))


def test_payload_without_a_report(saved):
    asset, path = saved
    p = height_viewer.map_payload(MAP, path, None, "preview")
    share, not_ready = recomputed(asset)
    s = p["summary"]
    assert s["source"] == "recomputed" and p["report_note"] == "no report"
    assert s["matches"] == 5                             # from the asset's meta
    assert s["supported"] == round(share, 4) and s["ready"] is False and s["not_ready"] == not_ready
    assert any("largest 13" in r for r in s["not_ready"])
    assert p["kill_lines"] is None                       # unknown, not "none"
    assert len(p["areas"]) == 3


def test_stale_report_is_ignored(saved):
    asset, path = saved
    lying = {**report_for(asset), "supported": 0.1, "ready": True, "not_ready": []}
    p = height_viewer.map_payload(MAP, path, {"height_sha": "000000000000", "height": lying}, "preview")
    assert p["summary"]["source"] == "recomputed"
    assert p["summary"]["supported"] != 0.1 and p["summary"]["ready"] is False
    assert "000000000000" in p["report_note"] and p["kill_lines"] is None


def test_report_with_an_empty_kill_list_is_none_not_unknown(saved):
    asset, path = saved
    report = report_for(asset)
    report["kill_lines"]["examples"] = []
    assert height_viewer.map_payload(MAP, path, wrap(path, report), "preview")["kill_lines"] == []


@pytest.mark.parametrize("wrapper, note", [([], "not a report"), ({"height_sha": "x"}, "not a report"),
                                           ({"height": []}, "not a report")])
def test_malformed_wrappers_are_ignored(saved, wrapper, note):
    _, path = saved
    p = height_viewer.map_payload(MAP, path, wrapper, "preview")
    assert p["summary"]["source"] == "recomputed" and note in p["report_note"]


def test_wrong_shapes_are_refused(tmp_path):
    asset = synthetic()
    asset.unresolved = asset.unresolved.ravel()          # loads fine, but isn't GRID x GRID
    path = tmp_path / f"{MAP}.height.npz"
    hc.save_asset(path, asset)
    with pytest.raises(ValueError, match="shape"):
        height_viewer.map_payload(MAP, path, None, "preview")
