# Height Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local, read-only page that shows a map's height asset over its minimap, so the owner can spot-check
heights by eye ("is this higher or lower than that, and by a lot or a little") and see exactly which
unresolved area blocks a map's readiness.

**Architecture:** A Python script (`webapp/scripts/height_viewer.py`) reads height assets, either the
`--preview` ones `build_control_heights.py` writes to `%TEMP%` or the committed ones. It embeds them, with the
minimap and the build's report, into one self-contained HTML page under `%TEMP%`. It follows
`scripts/control_tagger.py`'s pattern exactly: a template, an inlined pure-JS core and a JSON blob. The JS core
(`height_viewer_core.js`) holds every calculation the page shows. It is tested in node against the Python
build's own rules. The page's drawing and event code sits in the template, and is checked by eye in Task 4.

**Tech Stack:** Python (numpy, scipy.ndimage, PIL, already in `webapp/.venv`), plain browser JS (no
libraries), pytest, node (the JS tests skip without it, like `test_control_tagger.py`).

**Spec:** There is no separate spec. This plan's "Requirements" section is the spec. It comes from the
owner's 2026-10-05 conversation. Background: `docs/superpowers/specs/2026-10-01-control-heights-design.md`
(parts 3 and 4) and `webapp/app/control/heights.py`'s module docstring (the asset format).

## Requirements (the spec)

1. **Read-only and outside the repo.** It never writes a height asset, `index.json`, tags or the DB. The page
   goes under `%TEMP%\valo-height-viewer\` by default. It is a local tool, not a site page. An `--out` inside
   this checkout, or inside any folder with a `.git` above it, is refused (exit 2, nothing written). This is
   the same guard `build_control_heights.py --preview` uses.
2. **Sources:**
   - by default, every `<Map>.height.npz` (plus its `<Map>.height.json` report, if present) in the preview
     folder `%TEMP%\valo-replay\heights-preview` (`--dir` overrides);
   - `--committed` reads the committed assets instead: maps whose `index.json` entry has a `height_sha`, with
     the report from that entry's `height`;
   - `--map` (repeatable) narrows either source.
   - **A report counts only if it belongs to the asset.** Both sources wrap it as `{"height_sha", "height"}`,
     and it is used only when that `height_sha` equals the loaded asset's digest. Otherwise, or when the report
     is missing, unreadable or malformed, the map is still shown from its asset alone, with a warning saying
     why. Support and readiness are then recomputed with `hb.readiness` against the current walk mask and
     labelled "recomputed".
   - A map whose asset can't be loaded, has the wrong array shapes, or has no committed sight/walk masks is
     skipped with a `WARNING`, and the other maps still build.
3. **Heights over the minimap:** cells coloured by ground height with the build picture's ramp (blue low to
   yellow high, `height_build.picture`), with an opacity slider. There is a legend in metres above the map's
   lowest floor. Unresolved cells are red. Cells filled from neighbours (not supported) are dimmed. Cells with
   two floors are outlined white, three floors magenta.
4. **Hover** shows, per cell:
   - its grid position and minimap px;
   - each floor's height in metres, the cell's status written out ("supported" or "filled from neighbours"),
     and the floor's spread;
   - or why it has no height: unresolved, walkable without a height, or not walkable.
5. **Reference comparison:** clicking a cell sets the reference to its lowest floor. Clicking the same cell
   again cycles its floors, keys 1/2/3 pick one, and Esc clears. The hover then also shows, for each floor,
   the difference from the reference (`+1.4 m`) and a word:
   - `same height` for |d| ≤ 0.3 m;
   - `a little higher/lower` below 1.5 m;
   - `much higher/lower` from 1.5 m up.
6. **Same-height layer:** with a reference set, every cell that has a floor within ±tolerance of the reference
   is shaded. The tolerance is a slider from 0.1 to 1.0 m, default 0.3 m.
7. **Unresolved-area list:**
   - areas are labelled exactly as `height_build.readiness` labels them (8-connected);
   - each shows its size, its full bbox (`[x0, y0, x1, y1]` in minimap px) and its reasons (taken from the report when the report has the same area), and
     whether it **blocks readiness** (more than `UNRESOLVED_MAX` cells, touching a cell with 2+ floors);
   - blocking areas are listed first;
   - clicking an area highlights its cells and scrolls to it.
8. **Blocked kill lines:** the report's `kill_lines.examples` are listed. Clicking one draws the killer-victim
   line with both z values (metres above the map's origin, the same scale as the floors). Without a usable
   report the list reads "kill-line check unavailable (no report for this build)", which is never the same as
   a report with zero examples ("none").
9. **Optional drops layer:** marks neighbouring ground cells more than `STEP_UP_M` apart, as the build picture
   does.
10. **Header:**
    - the map picker, the source kind, and matches/rounds;
    - the supported share against the bar, and ready / not ready with the reasons, labelled "recomputed
      against the current walk mask" when they didn't come from the build's report;
    - a warning when the report was missing or ignored, and why;
    - a warning when the asset was built on an older walk mask (`meta.walk_sha` differs from the current
      mask's), and when the preview used `--preview-min-matches`.
11. **Zoom** 0.75×/1×/2×/3× inside a scrolling stage.

## Global Constraints

- Never write inside the repository at run time. The default output is
  `%TEMP%\valo-height-viewer\height-viewer.html`, and `--out` is guarded (Requirement 1). Python's own
  `__pycache__` is not counted: it is gitignored and every script in the repo writes it.
- No new dependencies. No network: the page is self-contained (minimap as base64, data inlined).
- Match `scripts/control_tagger.py` conventions:
  - `render()` replaces `/*CORE*/` and `/*DATA*/null`;
  - `</` in the data is escaped as `<\/`;
  - the core is a UMD-style IIFE that sets `global.HeightCore` and `module.exports`.
- Heights in the asset are whole decimetres above `origin_z`. The page shows metres with one decimal
  (`dm / 10`).
- The colour ramp must equal `height_build.picture`'s:
  - r = 40 + 215f, g = 90 + 150f, b = 200 − 170f, truncated to integers (Python's `astype(uint8)`, i.e.
    `Math.floor` for these non-negative values);
  - `f = clip(dm / top, 0, 1)`, where `top = max(max ground dm, 1)`.
- Thresholds shown to the owner (`SAME_M = 0.3`, `BIG_M = 1.5`) are display choices, defined once in the core.
- Python is `webapp\.venv\Scripts\python.exe`. Run tests from `webapp/`: `.venv\Scripts\python.exe -m pytest
  tests/replays/test_height_viewer.py -q`. In a worktree with no venv, use the main checkout's
  `C:/Users/User/Documents/GitHub/valo-with-friends-tracker/webapp/.venv/Scripts/python.exe` with `cd` into
  the worktree's `webapp/`.

## Review Focus

1. **A preview folder with a stray or broken file.**
   - Broken assets: a `.npz` that isn't one, a `.npz` of an old `HEIGHT_VERSION`, an asset with wrong array
     shapes, a map with no committed sight/walk masks. Expected: that map is skipped with a `WARNING` line
     naming it and the reason, and the other maps still build.
   - Broken or mismatched reports: a missing report, an unreadable one (a directory in its place), a
     malformed one (`[]`), or one whose `height_sha` isn't the asset's. Expected: the map is shown from its
     asset alone, with a warning, and support and readiness are recomputed. A wrong report must never put
     wrong numbers in the header.
   - If no map is left, the script exits 2 and prints the `build_control_heights.py --preview` command to run.
   - Tests:
     - Task 1: `test_payload_without_a_report`, `test_stale_report_is_ignored`, `test_wrong_shapes_are_refused`;
     - Task 3: `test_main_skips_a_broken_map_and_keeps_the_rest`, `test_main_survives_bad_reports`,
       `test_main_with_nothing_to_show_exits_2`.
2. **The blocking flag disagrees with the build.**
   - Risk: the owner will act on "BLOCKS READINESS", so it must match `height_build.readiness` on the same
     arrays, including the boundary (12 cells touching is fine, 13 blocks) and diagonal touching.
   - Test: `test_blocking_matches_readiness` (Task 1).
3. **A committed asset that doesn't match `index.json`.**
   - Expected: `--committed` skips a map whose `.npz` digest differs from the entry's `height_sha`, with a
     warning, rather than showing heights the engine wouldn't load.
   - Test: `test_committed_digest_mismatch_is_skipped` (Task 3).
4. **Floating-point boundaries in the comparison words.**
   - Expected: a difference of exactly 0.3 m reads "same height", and 1.5 m reads "much". Floors are whole dm,
     so `z - refZ` can be `0.30000000000000004`; the core rounds to 0.1 m first.
   - Test: `test_compare_boundaries` (Task 2).
5. **A cell whose floors include an upper floor only (ground −1) or an unresolved cell with stale floors.**
   - Expected: hover lists whatever floors exist. The drops layer and the colour use the ground floor only and
     skip cells without one, exactly as `picture()` does. An unresolved cell's stale ground still counts for
     drops, as it does in `picture()`.
   - Tests (Task 2, on the `edge_cases()` fixture with one cell of each kind): `test_floors_of_lists_present_floors_only`
     and `test_drops_and_colours_match_the_real_picture`. The second compares against the pixels `hb.picture`
     actually draws, not a copy of its loop.
6. **An `--out` that points into the repository.**
   - Expected: refused with exit 2 and nothing written. Otherwise a typo could overwrite `index.json` or an
     asset.
   - Test: `test_out_inside_a_repository_is_refused` (Task 3).

## File Structure

| File | Responsibility |
| --- | --- |
| `webapp/scripts/height_viewer.py` (create) | CLI: find sources, build each map's payload, render, write the page |
| `webapp/scripts/height_viewer_core.js` (create) | Pure functions the page's numbers come from (decode, floors, compare, same-height mask, drops, colour) |
| `webapp/scripts/height_viewer.template.html` (create) | The page: layout, drawing, hover, reference, lists |
| `webapp/tests/replays/test_height_viewer.py` (create) | Python payload/CLI tests and node tests of the core |
| `.gitignore` (modify) | Add `.claude/worktrees/` (the owner's worktree convention) |

---

### Task 1: The map payload (`height_viewer.py`, data half)

**Files:**
- Create: `webapp/scripts/height_viewer.py`
- Create: `webapp/tests/replays/test_height_viewer.py`
- Modify: `.gitignore` (add the line `.claude/worktrees/` under `.worktrees/`)

**Interfaces:**
- Consumes:
  - `app.control.heights`: `HeightAsset`, `load_asset`, `save_asset`, `UNRESOLVED_MAX`, `STEP_UP_M`,
    `MAX_FLOORS`, `HEIGHT_SUPPORTED_MIN`;
  - `app.control.geometry as cg`: `GRID`, `CELL`, `ASSET_DIR`, `MAPS_JSON`, `MINIMAP_DIR`, `read_mask_png`,
    `geometry_from_masks`;
  - `app.control.height_build as hb`: `_bbox`, `unresolved_areas`, `readiness` (tests only).
- Produces:
  - `rle(values) -> list[int]` (`[value, run, ...]`, same as `control_tagger.rle`);
  - `areas(asset, report: dict | None) -> list[dict]`, each `{"cells": [int], "size": int,
    "bbox": [x0, y0, x1, y1], "blocking": bool, "why": {reason: count}}`;
  - `usable_report(wrapper, digest) -> tuple[dict | None, str | None]`: the report inside a
    `{"height_sha", "height"}` wrapper, or `None` plus a note saying why it can't be used;
  - `map_payload(name, asset_path, wrapper, kind, asset_dir=cg.ASSET_DIR) -> dict`. `wrapper` is the preview
    `.json`'s parsed object, or the `index.json` entry, or `None`. The result has keys `name, kind, image,
    cell_m, origin_z, max_floors, step_up_m, supported_min, floors, spread, supported, unresolved, walk,
    areas, kill_lines (list, or None when there is no usable report), summary (with `source`: "report" |
    "recomputed"), report_note, height_sha, walk_stale, preview_min_matches`. Raises `ValueError` for an
    unreadable or wrongly shaped asset, and `OSError`/`KeyError`/`ValueError` for missing map geometry.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/replays/test_height_viewer.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'height_viewer'`.

- [ ] **Step 3: Write the payload half of the script**

```python
"""The height viewer: a read-only page for checking a map's heights by eye
(docs/superpowers/plans/2026-10-05-height-viewer.md; the asset: app/control/heights.py).

    .venv\\Scripts\\python.exe scripts\\height_viewer.py [--map Sunset] [--dir <folder>] [--committed] [--out <file>]

By default it reads every `<Map>.height.npz` (and its `<Map>.height.json` report) in the preview folder
that `build_control_heights.py --preview --out <dir>` writes (default %TEMP%\\valo-replay\\heights-preview).
With --committed it reads the committed assets instead: the maps whose index.json entry has a height_sha.
It writes one self-contained page (default %TEMP%\\valo-height-viewer\\height-viewer.html, never the
repository) and changes nothing else: no asset, no index.json, no tags, no database.

On the page: heights over the minimap (the build picture's colours), hover for each cell's floors, click to
set a reference and read every other floor as higher or lower than it, a same-height layer, the unresolved
areas with the ones that block readiness first, and the kill lines the heights would block.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy import ndimage

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.control import geometry as cg  # noqa: E402
from app.control import height_build as hb  # noqa: E402
from app.control import heights as hc  # noqa: E402

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "height_viewer.template.html"
CORE_JS = HERE / "height_viewer_core.js"
TEMP = Path(os.environ.get("TEMP") or tempfile.gettempdir())
DEFAULT_DIR = TEMP / "valo-replay" / "heights-preview"
DEFAULT_OUT = TEMP / "valo-height-viewer" / "height-viewer.html"
EIGHT = np.ones((3, 3), bool)   # 8-connected, as height_build labels areas


def rle(values: np.ndarray) -> list[int]:
    """[value, run, value, run, ...] over the flat array (control_tagger.rle)."""
    flat = np.asarray(values).ravel()
    if not len(flat):
        return []
    starts = np.r_[0, np.flatnonzero(flat[1:] != flat[:-1]) + 1]
    runs = np.diff(np.r_[starts, len(flat)])
    out = np.empty(2 * len(starts), np.int64)
    out[0::2], out[1::2] = flat[starts], runs
    return out.tolist()


def areas(asset: hc.HeightAsset, report: dict | None) -> list[dict]:
    """The unresolved cells as areas, labelled exactly as height_build.readiness labels them, blocking ones
    first: an area blocks when it has more than UNRESOLVED_MAX cells and touches (8-connected) a cell with
    two or more floors. Reasons come from the report's area with the same size and bbox, if any."""
    count = asset.floor_count().ravel()
    by_two = ndimage.binary_dilation((count >= 2).reshape(cg.GRID, cg.GRID), EIGHT).ravel()
    lab, n = ndimage.label(asset.unresolved, EIGHT)
    lab = lab.ravel()
    known = {(a["cells"], tuple(a["bbox"])): a["why"] for a in (report or {}).get("unresolved_areas", [])}
    out = []
    for k in range(1, n + 1):
        cells = np.flatnonzero(lab == k)
        bbox = hb._bbox(cells)
        out.append({"cells": cells.tolist(), "size": int(len(cells)), "bbox": bbox,
                    "blocking": bool(len(cells) > hc.UNRESOLVED_MAX and by_two[cells].any()),
                    "why": known.get((int(len(cells)), tuple(bbox)), {})})
    out.sort(key=lambda a: (not a["blocking"], -a["size"], a["bbox"]))
    return out


SHAPES = {"floors": (cg.GRID, cg.GRID, hc.MAX_FLOORS), "spread": (cg.GRID, cg.GRID, hc.MAX_FLOORS),
          "supported": (cg.GRID, cg.GRID), "unresolved": (cg.GRID, cg.GRID)}


def load_checked(asset_path: Path) -> hc.HeightAsset:
    """The asset, or ValueError: load_asset checks the version but not the shapes."""
    try:
        asset = hc.load_asset(asset_path)
    except Exception as exc:   # BadZipFile, a truncated file, a wrong version: not a usable asset
        raise ValueError(f"{asset_path.name} is not a readable height asset ({exc})") from exc
    for field_name, shape in SHAPES.items():
        if getattr(asset, field_name).shape != shape:
            raise ValueError(f"{asset_path.name}: {field_name} has shape {getattr(asset, field_name).shape}, "
                             f"not {shape}")
    return asset


def usable_report(wrapper, digest: str) -> tuple[dict | None, str | None]:
    """The build's report when it belongs to this asset (its wrapper's height_sha is the asset's digest),
    else None and why. A wrong report must never put its numbers on the page."""
    if wrapper is None:
        return None, "no report"
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("height"), dict) or "height_sha" not in wrapper:
        return None, "not a report (expected {height_sha, height})"
    if wrapper["height_sha"] != digest:
        return None, f"report is for another build ({wrapper['height_sha']}, this asset is {digest})"
    return wrapper["height"], None


def map_payload(name: str, asset_path: Path, wrapper, kind: str, asset_dir: Path = cg.ASSET_DIR) -> dict:
    """Everything the page shows for one map. `wrapper` is the preview .json or the index.json entry (both
    {"height_sha", "height"}), or None. Raises ValueError for an unusable asset, and OSError/KeyError/ValueError
    when the map's committed geometry can't be read."""
    asset = load_checked(asset_path)
    report, report_note = usable_report(wrapper, asset.digest)
    sight = cg.read_mask_png(asset_dir / f"{name}.sight.png")
    walk_px = cg.read_mask_png(asset_dir / f"{name}.walk.png")
    scale = json.loads(cg.MAPS_JSON.read_text(encoding="utf-8"))[name]["xMultiplier"]
    geo = cg.geometry_from_masks(name, sight, walk_px, scale)
    walk_sha = hashlib.sha256(np.packbits(geo.walk_px).tobytes()).hexdigest()[:12]
    if report is not None:
        summary = {"source": "report", "matches": report.get("matches", asset.meta.get("matches")),
                   "rounds": report.get("rounds", asset.meta.get("rounds")), "supported": report.get("supported"),
                   "ready": report.get("ready"), "not_ready": report.get("not_ready") or []}
        kill_lines = (report.get("kill_lines") or {}).get("examples", [])
    else:   # the build's own rule, against the walk mask as it is now
        share, not_ready = hb.readiness(asset.supported.ravel(), asset.unresolved.ravel(),
                                        asset.floor_count().ravel(), int(geo.walk.sum()))
        summary = {"source": "recomputed", "matches": asset.meta.get("matches"), "rounds": asset.meta.get("rounds"),
                   "supported": round(share, 4), "ready": not not_ready, "not_ready": not_ready}
        kill_lines = None
    return {
        "name": name, "kind": kind,
        "image": base64.b64encode((cg.MINIMAP_DIR / f"{name}.png").read_bytes()).decode("ascii"),
        "cell_m": round(geo.cell_m, 4), "origin_z": asset.origin_z, "max_floors": hc.MAX_FLOORS,
        "step_up_m": hc.STEP_UP_M, "supported_min": hc.HEIGHT_SUPPORTED_MIN,
        "floors": asset.floors.astype(int).ravel().tolist(),     # dm above origin_z, -1 none, cell-major
        "spread": asset.spread.astype(int).ravel().tolist(),
        "supported": rle(asset.supported.astype(np.uint8)),
        "unresolved": rle(asset.unresolved.astype(np.uint8)),
        "walk": rle(geo.walk.astype(np.uint8)),
        "areas": areas(asset, report),
        "kill_lines": kill_lines,
        "summary": summary, "report_note": report_note,
        "height_sha": asset.digest,
        "walk_stale": asset.meta.get("walk_sha") not in (None, walk_sha),
        "preview_min_matches": asset.meta.get("preview_min_matches"),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/replays/test_height_viewer.py -q`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add .gitignore webapp/scripts/height_viewer.py webapp/tests/replays/test_height_viewer.py
git commit -m "Height viewer: per-map payload with readiness-matched unresolved areas"
```

---

### Task 2: The JS core (`height_viewer_core.js`)

**Files:**
- Create: `webapp/scripts/height_viewer_core.js`
- Modify: `webapp/tests/replays/test_height_viewer.py` (append the node tests)

**Interfaces:**
- Consumes: Task 1's payload dict (`floors`, `spread`, `supported`, `unresolved`, `walk`, `max_floors`).
- Produces: the global `HeightCore` (and `module.exports`) with:
  - constants `GRID` (128), `CELL` (8), `PX` (1024), `SAME_M` (0.3), `BIG_M` (1.5);
  - `rleDecode(runs, n) -> Uint8Array`;
  - `prepare(payload) -> map`, with fields `maxFloors, floors (Int16Array), spread (Int16Array), supported,
    unresolved, walk (Uint8Array of GRID*GRID), raw (the payload)`;
  - `floorsOf(map, cell) -> [{floor, z, spread}]`, metres, present floors only, lowest first;
  - `ground(map, cell) -> dm | -1`;
  - `compare(refZ, z) -> {delta, word}`;
  - `sameMask(map, refZ, tolM) -> Uint8Array`;
  - `drops(map, stepM) -> [[cell, "e" | "s"]]`;
  - `groundTop(map) -> dm`;
  - `colour(dm, top) -> [r, g, b]`;
  - `cellAt(xPx, yPx) -> cell`.

- [ ] **Step 1: Append the failing node tests**

```python
CORE = WEBAPP / "scripts" / "height_viewer_core.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

RUN = """
  const H = require(process.argv[1]);
  let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
    const p = JSON.parse(input), map = p.payload ? H.prepare(p.payload) : null;
    process.stdout.write(JSON.stringify(run(p, map)));
  });
"""


def run_node(body: str, payload: dict):
    done = subprocess.run([NODE, "-e", RUN + body, str(CORE)], input=json.dumps(payload), capture_output=True,
                          text=True, encoding="utf-8", timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture
def payload(saved):
    asset, path = saved
    return height_viewer.map_payload(MAP, path, wrap(path, report_for(asset)), "preview")


def edge_cases():
    """synthetic() plus one cell of each awkward kind (Review Focus 5):
    - (40, 40): an upper floor at 3.0 m and no ground;
    - (20, 15): unresolved, but it kept a stale 5.0 m ground, right below the block's (19, 15) at 1.0 m;
    - (11, 11): a 2.0 m step inside the ground block (a drop on all four sides);
    - (13, 11): a 0.6 m step (under STEP_UP_M: no drop)."""
    asset = synthetic()
    asset.floors[40, 40, 1], asset.spread[40, 40, 1] = 30, 1
    asset.unresolved[20, 15] = True
    asset.floors[20, 15, 0], asset.spread[20, 15, 0] = 50, 1
    asset.floors[11, 11, 0] = 30
    asset.floors[13, 11, 0] = 16
    return asset


@pytest.fixture
def edge(tmp_path):
    asset = edge_cases()
    path = tmp_path / f"{MAP}.height.npz"
    hc.save_asset(path, asset)
    return asset, height_viewer.map_payload(MAP, path, None, "preview")


@needs_node
def test_compare_boundaries():
    out = run_node("function run(p) { return p.pairs.map(([a, b]) => H.compare(a, b)); }",
                   {"pairs": [[1.0, 1.3], [1.0, 0.7], [1.0, 1.4], [1.0, 2.4], [1.0, 2.5], [2.5, 1.0], [1.0, 1.0]]})
    assert [o["word"] for o in out] == ["same height", "same height", "a little higher", "a little higher",
                                        "much higher", "much lower", "same height"]
    assert [o["delta"] for o in out] == [0.3, -0.3, 0.4, 1.4, 1.5, -1.5, 0]


@needs_node
def test_floors_of_lists_present_floors_only(edge):
    _, p = edge
    out = run_node("function run(p, m) { return p.cells.map(c => H.floorsOf(m, c)); }",
                   {"payload": p, "cells": [cell(15, 20), cell(12, 12), cell(0, 0), cell(40, 40), cell(20, 15)]})
    assert out[0] == [{"floor": 0, "z": 1.0, "spread": 0.1}, {"floor": 1, "z": 4.0, "spread": 0.1}]
    assert [f["z"] for f in out[1]] == [1.0]
    assert out[2] == []
    assert out[3] == [{"floor": 1, "z": 3.0, "spread": 0.1}]          # upper floor only, no ground
    assert out[4] == [{"floor": 0, "z": 5.0, "spread": 0.1}]          # unresolved, stale floor still listed


@needs_node
def test_decoded_masks_match_the_asset(payload, saved):
    asset, _ = saved
    out = run_node("function run(p, m) { return {s: Array.from(m.supported), u: Array.from(m.unresolved)}; }",
                   {"payload": payload})
    assert out["s"] == asset.supported.ravel().astype(int).tolist()
    assert out["u"] == asset.unresolved.ravel().astype(int).tolist()


@needs_node
def test_same_mask(payload):
    out = run_node("function run(p, m) { return Array.from(H.sameMask(m, p.z, p.tol)); }",
                   {"payload": payload, "z": 4.0, "tol": 0.3})
    assert np.flatnonzero(out).tolist() == [cell(15, 20)]      # only the upper floor is near 4.0 m
    out = run_node("function run(p, m) { return Array.from(H.sameMask(m, p.z, p.tol)); }",
                   {"payload": payload, "z": 1.0, "tol": 0.3})
    assert sum(out) == int((synthetic().floors[..., 0] == 10).sum())


@needs_node
def test_drops_and_colours_match_the_real_picture(edge, tmp_path):
    """Draw hb.picture for the edge-case asset and read its pixels: its black pixels are exactly its drop
    marks, so the core's drops must cover exactly those pixels; a plain ground cell's centre pixel is its
    ramp colour, which the core's colour() must equal."""
    from PIL import Image

    asset, p = edge
    sight = cg.read_mask_png(cg.ASSET_DIR / f"{MAP}.sight.png")
    walk_px = cg.read_mask_png(cg.ASSET_DIR / f"{MAP}.walk.png")
    scale = json.loads(cg.MAPS_JSON.read_text(encoding="utf-8"))[MAP]["xMultiplier"]
    geo = cg.geometry_from_masks(MAP, sight, walk_px, scale)
    png = tmp_path / "picture.png"
    hb.picture(hb.HeightBuild(asset, {}), geo, png)
    img = np.array(Image.open(png).convert("RGB"))
    black = {(int(y), int(x)) for y, x in zip(*np.nonzero((img == 0).all(-1)))}

    got = run_node("""function run(p, m) {
      const top = H.groundTop(m);
      return {drops: H.drops(m, m.raw.step_up_m),
              colours: p.cells.map(c => H.colour(H.ground(m, c), top))}; }""",
                   {"payload": p, "cells": [cell(10, 20), cell(11, 11), cell(18, 25)]})
    C = cg.CELL
    drawn = set()
    for c, side in got["drops"]:
        cy, cx = divmod(c, GRID)
        if side == "e":
            drawn |= {(y, x) for y in range(cy * C, (cy + 1) * C) for x in ((cx + 1) * C - 1, (cx + 1) * C)}
        else:
            drawn |= {(y, x) for y in ((cy + 1) * C - 1, (cy + 1) * C) for x in range(cx * C, (cx + 1) * C)}
    assert drawn == black
    assert len(got["drops"]) == 5                # four round (11, 11), one from (19, 15) to the stale (20, 15)
    for (y, x), rgb in zip([(10, 20), (11, 11), (18, 25)], got["colours"]):
        assert rgb == img[y * C + C // 2, x * C + C // 2].tolist()


@needs_node
def test_colour_matches_picture_ramp():
    out = run_node("function run(p) { return p.dm.map(d => H.colour(d, p.top)); }", {"dm": [0, 15, 30, 45], "top": 30})
    for dm, rgb in zip([0, 15, 30, 45], out):
        f = min(max(dm / 30, 0), 1)
        assert rgb == [int(np.uint8(40 + 215 * f)), int(np.uint8(90 + 150 * f)), int(np.uint8(200 - 170 * f))]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/replays/test_height_viewer.py -q`
Expected: the 6 new tests FAIL with `Cannot find module` in node's stderr (or are skipped if node is missing.
It is at `C:\Program Files\nodejs\node`, so they must not be skipped on this machine).

- [ ] **Step 3: Write the core**

```js
/*
 * The height viewer's model (scripts/height_viewer.py; docs/superpowers/plans/2026-10-05-height-viewer.md).
 * Pure functions, inlined into the page and tested in node (tests/replays/test_height_viewer.py) against the
 * height build's own rules (app/control/height_build.py: `picture` for colours and drops).
 * Heights in a payload are whole decimetres above the map's lowest floor, -1 for none, cell-major
 * (cell * max_floors + floor); everything returned here is in metres.
 */
(function (global) {
  "use strict";
  var GRID = 128, CELL = 8, PX = 1024;
  var SAME_M = 0.3;   // at most this far apart reads "same height" (a display choice)
  var BIG_M = 1.5;    // this far apart or more reads "much higher/lower" (a display choice)

  function rleDecode(runs, n) {
    var out = new Uint8Array(n), pos = 0;
    for (var i = 0; i < runs.length; i += 2) {
      if (runs[i]) out.fill(runs[i], pos, pos + runs[i + 1]);
      pos += runs[i + 1];
    }
    return out;
  }

  function prepare(p) {
    var n = GRID * GRID;
    return {maxFloors: p.max_floors, floors: Int16Array.from(p.floors), spread: Int16Array.from(p.spread),
            supported: rleDecode(p.supported, n), unresolved: rleDecode(p.unresolved, n),
            walk: rleDecode(p.walk, n), raw: p};
  }

  function floorsOf(map, cell) {
    var out = [];
    for (var k = 0; k < map.maxFloors; k++) {
      var i = cell * map.maxFloors + k, dm = map.floors[i];
      if (dm >= 0) out.push({floor: k, z: dm / 10, spread: map.spread[i] / 10});
    }
    return out;
  }

  function ground(map, cell) { return map.floors[cell * map.maxFloors]; }

  function compare(refZ, z) {
    var d = Math.round((z - refZ) * 10) / 10, a = Math.abs(d);
    var word = a <= SAME_M ? "same height" : (a < BIG_M ? "a little " : "much ") + (d > 0 ? "higher" : "lower");
    return {delta: d === 0 ? 0 : d, word: word};
  }

  function sameMask(map, refZ, tolM) {
    var out = new Uint8Array(GRID * GRID);
    for (var c = 0; c < GRID * GRID; c++) {
      for (var k = 0; k < map.maxFloors; k++) {
        var dm = map.floors[c * map.maxFloors + k];
        if (dm >= 0 && Math.round(Math.abs(dm / 10 - refZ) * 10) / 10 <= tolM) { out[c] = 1; break; }
      }
    }
    return out;
  }

  function drops(map, stepM) {
    var out = [], step = stepM * 10;
    for (var cy = 0; cy < GRID; cy++) {
      for (var cx = 0; cx < GRID; cx++) {
        var c = cy * GRID + cx, g = ground(map, c);
        if (g < 0) continue;
        if (cx + 1 < GRID && ground(map, c + 1) >= 0 && Math.abs(g - ground(map, c + 1)) > step) out.push([c, "e"]);
        if (cy + 1 < GRID && ground(map, c + GRID) >= 0 && Math.abs(g - ground(map, c + GRID)) > step) out.push([c, "s"]);
      }
    }
    return out;
  }

  function groundTop(map) {
    var top = -1;
    for (var c = 0; c < GRID * GRID; c++) top = Math.max(top, ground(map, c));
    return Math.max(top, 1);
  }

  function colour(dm, top) {
    var f = Math.min(Math.max(dm / top, 0), 1);
    return [Math.floor(40 + 215 * f), Math.floor(90 + 150 * f), Math.floor(200 - 170 * f)];
  }

  function cellAt(x, y) {
    var cx = Math.min(GRID - 1, Math.max(0, Math.floor(x / CELL)));
    var cy = Math.min(GRID - 1, Math.max(0, Math.floor(y / CELL)));
    return cy * GRID + cx;
  }

  var api = {GRID: GRID, CELL: CELL, PX: PX, SAME_M: SAME_M, BIG_M: BIG_M, rleDecode: rleDecode, prepare: prepare,
             floorsOf: floorsOf, ground: ground, compare: compare, sameMask: sameMask, drops: drops,
             groundTop: groundTop, colour: colour, cellAt: cellAt};
  global.HeightCore = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
```

Note: `picture()` uses `top = max(ground[has].max(), 1.0)`. `groundTop` starts at −1 so a map with no ground
gives 1, the same as Python.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/replays/test_height_viewer.py -q`
Expected: 16 passed, 0 skipped.

- [ ] **Step 5: Commit**

```bash
git add webapp/scripts/height_viewer_core.js webapp/tests/replays/test_height_viewer.py
git commit -m "Height viewer: JS core (floors, compare, same-height, drops, colours) tested against the build"
```

---

### Task 3: The page and the CLI

**Files:**
- Create: `webapp/scripts/height_viewer.template.html`
- Modify: `webapp/scripts/height_viewer.py` (append `sources`, `render`, `main`)
- Modify: `webapp/tests/replays/test_height_viewer.py` (append the CLI tests)

**Interfaces:**
- Consumes: `map_payload` (Task 1), `HeightCore` (Task 2).
- Produces:
  - `sources(directory: Path, committed: bool, names: list[str] | None, asset_dir=cg.ASSET_DIR) -> list[tuple[str, Path, object, str]]`,
    each `(map, asset path, wrapper or None, kind)`. The wrapper is passed to `map_payload` unvalidated, and
    `usable_report` decides;
  - `inside_a_repository(path: Path) -> bool`;
  - `render(maps: dict) -> str`;
  - `main(argv=None, asset_dir=cg.ASSET_DIR) -> int` (0 written; 2 refused or nothing to show).

- [ ] **Step 1: Append the failing CLI tests**

```python
def write_preview(folder: Path, asset, report=None, name=MAP):
    """What build_control_heights.py --preview writes: the asset, and its report under the asset's digest."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.height.npz"
    hc.save_asset(path, asset)
    if report is not None:
        (folder / f"{name}.height.json").write_text(json.dumps(wrap(path, report)), encoding="utf-8")


def test_render_inlines_core_and_escapes_data():
    html = height_viewer.render({"X": {"name": "</script><b>"}})
    assert "HeightCore" in html and "/*CORE*/" not in html and "/*DATA*/null" not in html
    assert "</script><b>" not in html and "<\\/script><b>" in html


def test_main_writes_the_page(tmp_path):
    folder, out = tmp_path / "preview", tmp_path / "out" / "page.html"
    write_preview(folder, synthetic(), report_for(synthetic()))
    assert height_viewer.main(["--dir", str(folder), "--out", str(out)]) == 0
    data = page_data(out)
    m = data["maps"][MAP]
    assert m["summary"]["source"] == "report"
    assert [a["size"] for a in m["areas"] if a["blocking"]] == [13]
    assert "HeightCore" in out.read_text(encoding="utf-8")


def page_data(out: Path) -> dict:
    """The DATA object the page was rendered with."""
    return json.loads(out.read_text(encoding="utf-8").split("var DATA = ", 1)[1].split(";\n", 1)[0])


def test_main_skips_a_broken_map_and_keeps_the_rest(tmp_path, capsys):
    folder, out = tmp_path / "preview", tmp_path / "page.html"
    write_preview(folder, synthetic())                               # good, no report
    write_preview(folder, synthetic(), name="Nowhere")              # no such map's masks
    (folder / "Lotus.height.npz").write_bytes(b"not an npz")         # unreadable
    old = synthetic()
    old.meta = {**old.meta, "version": 99}                           # an old/unknown HEIGHT_VERSION
    write_preview(folder, old, name="Split")
    flat = synthetic()
    flat.floors = flat.floors.reshape(GRID * GRID, MAXF)             # loads, wrong shape
    write_preview(folder, flat, name="Haven")
    assert height_viewer.main(["--dir", str(folder), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    for name in ("Nowhere", "Lotus", "Split", "Haven"):
        assert f"WARNING {name}" in printed
    html = out.read_text(encoding="utf-8")
    assert f'"{MAP}"' in html
    assert all(f'"name":"{n}"' not in html for n in ("Nowhere", "Lotus", "Split", "Haven"))


def test_main_survives_bad_reports(tmp_path, capsys, monkeypatch):
    folder, out = tmp_path / "preview", tmp_path / "page.html"
    write_preview(folder, synthetic(), report_for(synthetic()))                  # Ascent: a good report
    write_preview(folder, synthetic(), name="Sunset")
    (folder / "Sunset.height.json").write_text("[]", encoding="utf-8")           # malformed
    write_preview(folder, synthetic(), name="Split")
    (folder / "Split.height.json").write_text("{not json", encoding="utf-8")     # not JSON
    write_preview(folder, synthetic(), name="Haven")
    (folder / "Haven.height.json").write_text("{}", encoding="utf-8")
    real = Path.read_text

    def unreadable(self, *a, **k):                                              # Haven's report can't be read
        if self.name == "Haven.height.json":
            raise PermissionError("simulated")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert height_viewer.main(["--dir", str(folder), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    for name in ("Sunset", "Split", "Haven"):
        assert f"WARNING {name}" in printed and "showing the asset alone" in printed
    monkeypatch.undo()
    data = page_data(out)
    assert data["maps"]["Ascent"]["summary"]["source"] == "report"
    assert {data["maps"][n]["summary"]["source"] for n in ("Sunset", "Split", "Haven")} == {"recomputed"}


def test_out_inside_a_repository_is_refused(tmp_path, capsys):
    folder = tmp_path / "preview"
    write_preview(folder, synthetic())
    inside = WEBAPP / "app" / "static" / "data" / "control" / "index.json"     # the worst case
    before = inside.read_bytes()
    assert height_viewer.main(["--dir", str(folder), "--out", str(inside)]) == 2
    assert inside.read_bytes() == before and "REFUSED" in capsys.readouterr().out
    other = tmp_path / "another-checkout"
    (other / ".git").mkdir(parents=True)                                         # any folder under a .git
    assert height_viewer.main(["--dir", str(folder), "--out", str(other / "sub" / "p.html")]) == 2
    assert not (other / "sub").exists()


def test_main_with_nothing_to_show_exits_2(tmp_path, capsys):
    assert height_viewer.main(["--dir", str(tmp_path / "empty"), "--out", str(tmp_path / "p.html")]) == 2
    assert "build_control_heights.py" in capsys.readouterr().out
    assert not (tmp_path / "p.html").exists()


def test_map_filter(tmp_path):
    folder = tmp_path / "preview"
    write_preview(folder, synthetic())
    write_preview(folder, synthetic(), name="Sunset")
    assert [s[0] for s in height_viewer.sources(folder, False, ["Sunset"])] == ["Sunset"]


def test_committed_digest_mismatch_is_skipped(tmp_path, capsys):
    import shutil as sh
    assets = tmp_path / "control"
    sh.copytree(cg.ASSET_DIR, assets)
    hc.save_asset(assets / f"{MAP}.height.npz", synthetic())
    index = json.loads((assets / "index.json").read_text(encoding="utf-8"))
    index["maps"][MAP]["height_sha"] = "000000000000"
    (assets / "index.json").write_text(json.dumps(index), encoding="utf-8")
    # --map: other maps may have valid committed heights one day, and this test is about MAP's mismatch only
    assert height_viewer.main(["--committed", "--map", MAP, "--out", str(tmp_path / "p.html")],
                              asset_dir=assets) == 2
    assert f"WARNING {MAP}" in capsys.readouterr().out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/replays/test_height_viewer.py -q`
Expected: the 8 new tests FAIL with `AttributeError: module 'height_viewer' has no attribute 'render'` (or
`'main'`/`'sources'`).

- [ ] **Step 3: Append `sources`, `render` and `main` to `height_viewer.py`**

```python
def sources(directory: Path, committed: bool, names: list[str] | None,
            asset_dir: Path = cg.ASSET_DIR) -> list[tuple[str, Path, object, str]]:
    """(map, asset path, the report's {height_sha, height} wrapper or None, kind) for every asset to show.
    The index.json entry is already that wrapper. A preview report that can't be read is None, with a
    WARNING; one that reads but isn't a wrapper is passed on for usable_report to refuse."""
    out = []
    if committed:
        index_path = asset_dir / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8")).get("maps", {}) if index_path.is_file() else {}
        for name, entry in sorted(index.items()):
            if isinstance(entry, dict) and entry.get("height_sha"):
                out.append((name, asset_dir / f"{name}.height.npz", entry, "committed"))
    else:
        for npz in sorted(directory.glob("*.height.npz")):
            name = npz.name[: -len(".height.npz")]
            if names and name not in names:
                continue
            report_path = npz.with_name(f"{name}.height.json")
            wrapper = None
            if report_path.is_file():
                try:
                    wrapper = json.loads(report_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    print(f"WARNING {name}: can't read {report_path.name} ({type(exc).__name__}); showing the "
                          f"asset alone", flush=True)
            out.append((name, npz, wrapper, "preview"))
    return [s for s in out if not names or s[0] in names]


def inside_a_repository(path: Path) -> bool:
    """This checkout, or any other one (build_control_heights.py's --preview guard: the main checkout and
    each worktree have a `.git`)."""
    out = path.resolve()
    return out.is_relative_to(WEBAPP_ROOT.parent.resolve()) or any((f / ".git").exists() for f in (out, *out.parents))


def render(maps: dict) -> str:
    data = json.dumps({"maps": maps}, separators=(",", ":")).replace("</", "<\\/")
    core = CORE_JS.read_text(encoding="utf-8")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*CORE*/", core).replace("/*DATA*/null", data)


def main(argv: list[str] | None = None, asset_dir: Path = cg.ASSET_DIR) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="the preview folder (default under %%TEMP%%)")
    parser.add_argument("--committed", action="store_true", help="show the committed assets instead of previews")
    parser.add_argument("--map", action="append", help="only this map (repeatable)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the page to write (default under %%TEMP%%)")
    args = parser.parse_args(argv)
    if inside_a_repository(args.out):
        print(f"REFUSED: --out {args.out} is inside a repository; the page is never written into one", flush=True)
        return 2
    index = {}
    if args.committed and (asset_dir / "index.json").is_file():
        index = json.loads((asset_dir / "index.json").read_text(encoding="utf-8")).get("maps", {})
    maps = {}
    for name, path, wrapper, kind in sources(args.dir, args.committed, args.map, asset_dir):
        try:
            payload = map_payload(name, path, wrapper, kind, asset_dir)
        except (OSError, KeyError, ValueError) as exc:
            print(f"WARNING {name}: skipped ({type(exc).__name__}: {exc})", flush=True)
            continue
        if wrapper is not None and payload["report_note"]:
            print(f"WARNING {name}: {payload['report_note']}; showing the asset alone", flush=True)
        if kind == "committed" and payload["height_sha"] != index[name]["height_sha"]:
            print(f"WARNING {name}: skipped ({path.name} is {payload['height_sha']}, index.json says "
                  f"{index[name]['height_sha']}; the engine would refuse it)", flush=True)
            continue
        maps[name] = payload
    if not maps:
        where = "no map has a committed height asset" if args.committed else f"no height assets in {args.dir}"
        print(f"{where}. Build a preview first, e.g.\n  scripts\\with_friends_db.py --expect-database "
              f"valowithfriendsdb --read-only scripts\\build_control_heights.py --map Sunset --preview "
              f"--out \"{DEFAULT_DIR}\"", flush=True)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(maps), encoding="utf-8")
    print(f"{len(maps)} map(s) -> {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`map_payload` (Task 1) already turns an unreadable or wrongly shaped `.npz` into a `ValueError`, so the
`except` above covers it. The `--out` guard runs before anything is read, so a refused run writes nothing,
not even a folder.

- [ ] **Step 4: Write the template**

`webapp/scripts/height_viewer.template.html`:

```html
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Height viewer</title>
<style>
  body { margin: 0; font: 13px/1.4 system-ui, sans-serif; background: #16161a; color: #e6e6ea; }
  header { padding: 8px 12px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; border-bottom: 1px solid #333; }
  .warn { color: #ffb347; } .bad { color: #ff6b6b; } .ok { color: #7bd88f; }
  main { display: flex; gap: 12px; padding: 12px; }
  .scroll { overflow: auto; max-height: calc(100vh - 80px); flex: 1; }
  .stage { position: relative; width: var(--size); height: var(--size); }
  .stage canvas { position: absolute; inset: 0; width: 100%; height: 100%; image-rendering: pixelated; }
  aside { width: 320px; flex: none; overflow: auto; max-height: calc(100vh - 80px); }
  aside h3 { margin: 12px 0 4px; font-size: 13px; }
  .item { padding: 3px 6px; border-radius: 4px; cursor: pointer; } .item:hover { background: #2a2a32; }
  .item.blocking { border-left: 3px solid #ff6b6b; }
  #tip { position: fixed; pointer-events: none; background: #000d; padding: 6px 8px; border-radius: 4px; white-space: pre; display: none; z-index: 2; }
  label { white-space: nowrap; }
</style></head><body>
<header>
  <select id="map"></select>
  <span id="summary"></span>
  <span id="warnings" class="warn"></span>
</header>
<main>
  <div class="scroll" id="scroll"><div class="stage" id="stage" style="--size:1024px">
    <canvas id="base" width="1024" height="1024"></canvas>
    <canvas id="heights" width="1024" height="1024"></canvas>
    <canvas id="marks" width="1024" height="1024"></canvas>
  </div></div>
  <aside>
    <div>Zoom <button data-zoom="768">0.75×</button> <button data-zoom="1024">1×</button>
      <button data-zoom="2048">2×</button> <button data-zoom="3072">3×</button></div>
    <h3>Layers</h3>
    <label><input type="checkbox" id="lyHeights" checked> heights</label>
    <input type="range" id="opacity" min="0" max="100" value="70"><br>
    <label><input type="checkbox" id="lyUnresolved" checked> unresolved (red: flat 2D there)</label><br>
    <label><input type="checkbox" id="lyFilled" checked> dim cells filled from neighbours</label><br>
    <label><input type="checkbox" id="lyTwo" checked> cells with 2+ floors (white 2, magenta 3)</label><br>
    <label><input type="checkbox" id="lyDrops"> drops bigger than a step</label><br>
    <label><input type="checkbox" id="lySame" checked> same height as the reference, ±</label>
    <input type="range" id="tol" min="1" max="10" value="3"> <span id="tolText"></span>
    <div style="margin-top:8px"><canvas id="legend" width="200" height="10"></canvas><div id="legendText"></div></div>
    <h3>Reference</h3>
    <div id="ref"></div>
    <h3>Unresolved areas</h3>
    <div id="areas"></div>
    <h3>Kill lines the heights would block</h3>
    <div id="kills"></div>
  </aside>
</main>
<div id="tip"></div>
<script>
/*CORE*/
</script>
<script>
(function () {
  "use strict";
  var DATA = /*DATA*/null;
  var H = HeightCore, GRID = H.GRID, CELL = H.CELL, PX = H.PX;
  var $ = function (id) { return document.getElementById(id); };
  var map = null, top = 1, ref = null, focusArea = null, focusKill = null, img = new Image();

  function tol() { return $("tol").value / 10; }
  function m1(z) { return z.toFixed(1) + " m"; }
  function signed(d) { return (d > 0 ? "+" : d < 0 ? "−" : "±") + Math.abs(d).toFixed(1) + " m"; }

  Object.keys(DATA.maps).sort().forEach(function (n) {
    var o = document.createElement("option"); o.value = o.textContent = n; $("map").appendChild(o);
  });

  function load(name) {
    map = H.prepare(DATA.maps[name]); top = H.groundTop(map); ref = null; focusArea = focusKill = null;
    img.onload = function () {
      var c = $("base").getContext("2d"); c.clearRect(0, 0, PX, PX); c.drawImage(img, 0, 0, PX, PX);
    };
    img.src = "data:image/png;base64," + map.raw.image;
    var s = map.raw.summary, parts = [name + " (" + map.raw.kind + ")"];
    if (s.matches != null) parts.push(s.matches + " matches, " + s.rounds + " rounds");
    parts.push("supported " + (100 * s.supported).toFixed(1) + "% (bar " + (100 * map.raw.supported_min).toFixed(0) + "%)");
    $("summary").textContent = parts.join(" · ");
    var r = document.createElement("span");
    r.className = s.ready ? "ok" : "bad";
    r.textContent = (s.ready ? " · ready" : " · not ready: " + s.not_ready.join("; ")) +
                    (s.source === "recomputed" ? " (recomputed against the current walk mask)" : "");
    $("summary").appendChild(r);
    var w = [];
    if (map.raw.report_note) w.push("build report not used: " + map.raw.report_note);
    if (map.raw.walk_stale) w.push("built on an older walk mask: cells added since have no height");
    if (map.raw.preview_min_matches) w.push("preview rule: floors from " + map.raw.preview_min_matches + " match(es), not 2");
    $("warnings").textContent = w.join(" · ");
    location.hash = name;
    listAreas(); listKills(); drawHeights(); drawMarks(); showRef();
  }

  function drawHeights() {
    var im = new ImageData(GRID, GRID), a = Math.round(2.55 * $("opacity").value);
    for (var cell = 0; cell < GRID * GRID; cell++) {
      var g = H.ground(map, cell), o = cell * 4, rgb = null, alpha = a;
      if (map.unresolved[cell]) {
        if ($("lyUnresolved").checked) { rgb = [220, 40, 40]; alpha = Math.max(a, 160); }
      } else if (g >= 0 && $("lyHeights").checked) {
        rgb = H.colour(g, top);
        if ($("lyFilled").checked && !map.supported[cell]) alpha = Math.round(a * 0.45);
      }
      if (rgb) { im.data[o] = rgb[0]; im.data[o + 1] = rgb[1]; im.data[o + 2] = rgb[2]; im.data[o + 3] = alpha; }
    }
    var off = document.createElement("canvas"); off.width = off.height = GRID;
    off.getContext("2d").putImageData(im, 0, 0);
    var c = $("heights").getContext("2d");
    c.clearRect(0, 0, PX, PX); c.imageSmoothingEnabled = false; c.drawImage(off, 0, 0, PX, PX);
    var lg = $("legend").getContext("2d");
    for (var x = 0; x < 200; x++) { var rgb2 = H.colour(x / 199 * top, top); lg.fillStyle = "rgb(" + rgb2 + ")"; lg.fillRect(x, 0, 1, 10); }
    $("legendText").textContent = "0 m … " + m1(top / 10) + " above the map's lowest floor";
  }

  function fillCell(c, cell) { c.fillRect((cell % GRID) * CELL, Math.floor(cell / GRID) * CELL, CELL, CELL); }

  function drawMarks() {
    var c = $("marks").getContext("2d"), cell;
    c.clearRect(0, 0, PX, PX);
    if ($("lySame").checked && ref) {
      var same = H.sameMask(map, ref.z, tol());
      c.fillStyle = "rgba(255,255,255,0.4)";
      for (cell = 0; cell < same.length; cell++) if (same[cell]) fillCell(c, cell);
    }
    if ($("lyTwo").checked) {
      c.lineWidth = 1;
      for (cell = 0; cell < GRID * GRID; cell++) {
        var n = H.floorsOf(map, cell).length;
        if (n >= 2) {
          c.strokeStyle = n === 2 ? "#fff" : "#f0f";
          c.strokeRect((cell % GRID) * CELL + 0.5, Math.floor(cell / GRID) * CELL + 0.5, CELL - 1, CELL - 1);
        }
      }
    }
    if ($("lyDrops").checked) {
      c.fillStyle = "#000";
      H.drops(map, map.raw.step_up_m).forEach(function (d) {
        var x = (d[0] % GRID) * CELL, y = Math.floor(d[0] / GRID) * CELL;
        if (d[1] === "e") c.fillRect(x + CELL - 1, y, 2, CELL); else c.fillRect(x, y + CELL - 1, CELL, 2);
      });
    }
    if (focusArea) {
      c.fillStyle = "rgba(255,235,59,0.55)";
      focusArea.cells.forEach(function (cl) { fillCell(c, cl); });
    }
    if (focusKill) {
      var k = focusKill.killer_px, v = focusKill.victim_px;
      c.strokeStyle = "#ffeb3b"; c.lineWidth = 3;
      c.beginPath(); c.moveTo(k[0], k[1]); c.lineTo(v[0], v[1]); c.stroke();
      c.font = "bold 16px system-ui"; c.fillStyle = "#ffeb3b";
      c.fillText("killer z " + m1(focusKill.z[0]), k[0] + 8, k[1] - 8);
      c.fillText("victim z " + m1(focusKill.z[1]), v[0] + 8, v[1] - 8);
    }
    if (ref) {
      var rx = (ref.cell % GRID) * CELL + CELL / 2, ry = Math.floor(ref.cell / GRID) * CELL + CELL / 2;
      c.strokeStyle = "#00e5ff"; c.lineWidth = 2;
      c.beginPath(); c.arc(rx, ry, 10, 0, 2 * Math.PI); c.moveTo(rx - 16, ry); c.lineTo(rx + 16, ry);
      c.moveTo(rx, ry - 16); c.lineTo(rx, ry + 16); c.stroke();
    }
  }

  function describe(cell) {
    var x = cell % GRID, y = Math.floor(cell / GRID);
    var lines = ["cell " + x + ", " + y + "  (px " + x * CELL + ", " + y * CELL + ")"];
    var fl = H.floorsOf(map, cell);
    if (map.unresolved[cell]) lines.push("unresolved: flat 2D sight and walking here");
    else if (!fl.length) lines.push(map.walk[cell] ? "walkable, no height" : "not walkable");
    fl.forEach(function (f) {
      var s = "floor " + (f.floor + 1) + ": " + m1(f.z) + (map.supported[cell] ? " (supported)" : " (filled from neighbours)") +
              ", spread " + m1(f.spread);
      if (ref) { var d = H.compare(ref.z, f.z); s += "   " + signed(d.delta) + ", " + d.word; }
      lines.push(s);
    });
    return lines.join("\n");
  }

  function cellOf(e) {
    var r = $("marks").getBoundingClientRect();
    return H.cellAt((e.clientX - r.left) / r.width * PX, (e.clientY - r.top) / r.height * PX);
  }

  function showRef() {
    $("tolText").textContent = "±" + tol().toFixed(1) + " m";
    if (!ref) { $("ref").textContent = "Click the map to set one (click again or press 1/2/3 for another floor; Esc clears)."; return; }
    var fl = H.floorsOf(map, ref.cell);
    $("ref").textContent = "cell " + (ref.cell % GRID) + ", " + Math.floor(ref.cell / GRID) + ", floor " +
      (fl[ref.k].floor + 1) + " of " + fl.length + ": " + m1(ref.z);
  }

  function setRef(cell, k) {
    var fl = H.floorsOf(map, cell);
    if (!fl.length || k >= fl.length) return;
    ref = {cell: cell, k: k, z: fl[k].z}; showRef(); drawMarks();
  }

  function scrollToBox(b) {
    var s = $("stage").getBoundingClientRect().width / PX, box = $("scroll");
    box.scrollLeft = (b[0] + b[2]) / 2 * s - box.clientWidth / 2;
    box.scrollTop = (b[1] + b[3]) / 2 * s - box.clientHeight / 2;
  }

  function listAreas() {
    var box = $("areas"); box.textContent = "";
    if (!map.raw.areas.length) { box.textContent = "none"; return; }
    map.raw.areas.forEach(function (a) {
      var d = document.createElement("div"), why = Object.keys(a.why).map(function (k) { return k + " " + a.why[k]; });
      d.className = "item" + (a.blocking ? " blocking" : "");
      d.textContent = (a.blocking ? "BLOCKS READINESS · " : "") + a.size + " cells at px [" + a.bbox.join(", ") + "]" +
                      (why.length ? " · " + why.join(", ") : "");
      d.onclick = function () { focusArea = focusArea === a ? null : a; focusKill = null; drawMarks(); scrollToBox(a.bbox); };
      box.appendChild(d);
    });
  }

  function listKills() {
    var box = $("kills"); box.textContent = "";
    if (map.raw.kill_lines === null) { box.textContent = "kill-line check unavailable (no report for this build)"; return; }
    if (!map.raw.kill_lines.length) { box.textContent = "none"; return; }
    map.raw.kill_lines.forEach(function (k) {
      var d = document.createElement("div");
      d.className = "item";
      d.textContent = "match " + k.match.slice(0, 8) + ", round " + k.round + " at " + k.t.toFixed(1) + " s · killer z " +
                      m1(k.z[0]) + ", victim z " + m1(k.z[1]);
      d.onclick = function () {
        focusKill = focusKill === k ? null : k; focusArea = null; drawMarks();
        scrollToBox([Math.min(k.killer_px[0], k.victim_px[0]), Math.min(k.killer_px[1], k.victim_px[1]),
                     Math.max(k.killer_px[0], k.victim_px[0]), Math.max(k.killer_px[1], k.victim_px[1])]);
      };
      box.appendChild(d);
    });
  }

  $("marks").addEventListener("mousemove", function (e) {
    var t = $("tip"); t.textContent = describe(cellOf(e)); t.style.display = "block";
    t.style.left = Math.min(e.clientX + 14, innerWidth - t.offsetWidth - 4) + "px";
    t.style.top = Math.min(e.clientY + 14, innerHeight - t.offsetHeight - 4) + "px";
  });
  $("marks").addEventListener("mouseleave", function () { $("tip").style.display = "none"; });
  $("marks").addEventListener("click", function (e) {
    var cell = cellOf(e), n = H.floorsOf(map, cell).length;
    if (!n) return;
    setRef(cell, ref && ref.cell === cell ? (ref.k + 1) % n : 0);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { ref = null; showRef(); drawMarks(); }
    else if (ref && "123".indexOf(e.key) >= 0) setRef(ref.cell, +e.key - 1);
  });
  ["lyHeights", "lyUnresolved", "lyFilled", "opacity"].forEach(function (id) { $(id).addEventListener("input", drawHeights); });
  ["lyTwo", "lyDrops", "lySame"].forEach(function (id) { $(id).addEventListener("input", drawMarks); });
  $("tol").addEventListener("input", function () { showRef(); drawMarks(); });
  document.querySelectorAll("[data-zoom]").forEach(function (b) {
    b.onclick = function () { $("stage").style.setProperty("--size", b.dataset.zoom + "px"); };
  });
  $("map").onchange = function () { load($("map").value); };

  var first = decodeURIComponent(location.hash.slice(1));
  $("map").value = DATA.maps[first] ? first : $("map").options[0].value;
  load($("map").value);
})();
</script>
</body></html>
```

`page_data()` reads `var DATA = ...;` back out of the written page. The template's line `var DATA =
/*DATA*/null;` must stay on one line, ending in `;` and a newline, for the tests to find it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/replays/test_height_viewer.py -q`
Expected: 24 passed, 0 skipped.

- [ ] **Step 6: Commit**

```bash
git add webapp/scripts/height_viewer.py webapp/scripts/height_viewer.template.html webapp/tests/replays/test_height_viewer.py
git commit -m "Height viewer: page and CLI (previews or committed assets, skips broken maps)"
```

---

### Task 4: Check it on Sunset's real preview, by eye

This is the owner's acceptance check. The page's drawing and events are only verified here. Nothing is
committed except fixes.

**Files:** none new. Fixes go to the Task 3 files, with their own test where a number was wrong.

- [ ] **Step 1: Build the page from the real previews**

Run: `cd webapp && .venv\Scripts\python.exe scripts\height_viewer.py`
Expected: `5 map(s) -> ...\valo-height-viewer\height-viewer.html` (Ascent, Haven, Lotus, Summit and Sunset
are in `%TEMP%\valo-replay\heights-preview` today). No `WARNING` lines, or only ones whose reason is real.

- [ ] **Step 2: Cross-check two numbers against the asset**

Write `%TEMP%\hv_check.py` (no heredocs) with:

```python
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "webapp")
from app.control import heights as hc

a = hc.load_asset(Path(r"C:/Users/User/AppData/Local/Temp/valo-replay/heights-preview/Sunset.height.npz"))
count = a.floor_count()
picks = {
    "a filled cell": np.argwhere((count >= 1) & ~a.supported)[0],
    "a two-floor cell": np.argwhere(count >= 2)[0],
    "a supported cell": np.argwhere((count == 1) & a.supported)[len(np.argwhere((count == 1) & a.supported)) // 2],
}
for label, (y, x) in picks.items():
    print(label, "cell", x, y, "px", x * 8, y * 8, [f / 10 for f in a.floors[y, x] if f >= 0],
          "supported" if a.supported[y, x] else "filled")
```

Run it from the worktree root. Then open the page (`#Sunset`) and hover each printed cell (the px tells you
where; the tooltip's first line gives the cell). The floors must match to 0.1 m, and so must the
supported/filled status.

- [ ] **Step 3: Check the behaviours the owner will use**

Open `file:///C:/Users/User/AppData/Local/Temp/valo-height-viewer/height-viewer.html#Sunset` in Chrome. If
the browser-automation extension is driving, serve the folder over `python -m http.server`, because it
refuses `file://`. Check each:
- the header says `not ready: 1 unresolved area(s) larger than 12 cells ...` and `supported 70.7% (bar 60%)`,
  with no "recomputed" label and no "build report not used" warning (Sunset's report matches its asset);
- the first area in the list is `BLOCKS READINESS · 13 cells at px [352, 672, 415, 687] · neighbours
  disagree 13`. Clicking it highlights a strip just under the small two-floor patch near px (420, 660);
- hovering a supported cell says `(supported)`, and a filled one says `(filled from neighbours)`;
- rebuild with `--dir` pointing at a temp copy of the preview folder whose `Sunset.height.json` is deleted.
  The header must say `(recomputed against the current walk mask)` with the same 70.7%, and the kill-line
  list must say `kill-line check unavailable`, not `none`;
- clicking a ground cell sets the reference; hovering a nearby cell reads `±0.0 m, same height` or a small
  difference; hovering an upper floor of a white-outlined cell reads `much higher`;
- the same-height layer shades the connected flat area around the reference, and dragging the tolerance
  slider widens it;
- the kill-line list has one entry (`match eae6774e, round 16 at 57.5 s`). Clicking it draws the line from
  about px (190, 512) to (168, 325);
- 2× and 3× zoom keep hover positions correct (the tooltip's cell matches the cell under the pointer);
- switching maps resets the reference and updates the lists.

Take screenshots of: the default view, the blocking area highlighted, and a reference with the same-height
layer. Save them to `%TEMP%\valo-height-viewer\`.

- [ ] **Step 4: Fix and commit anything the check found**

For each wrong number, add a failing test to `test_height_viewer.py` first, then fix it. Commit the fixes
together: `git commit -m "Height viewer: fixes from the Sunset check"`. If nothing was wrong, there is
nothing to commit.

- [ ] **Step 5: Run the full file one last time**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/replays/test_height_viewer.py tests/replays/test_control_heights.py -q`
Expected: all pass. `test_control_heights.py` is the build's own suite. The viewer imports
`height_build._bbox`, so a rename there must fail loudly here.
