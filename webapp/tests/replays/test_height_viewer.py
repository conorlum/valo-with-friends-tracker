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


# ---------------------------------------------------------------- how each cell got its height (slopes spec, 4)


def kinded(tmp_path):
    """synthetic() with one cell of each new kind: (10, 10) from walks alone, (10, 11) filled along a gradient."""
    asset = synthetic()
    asset.kind[10, 10], asset.kind[10, 11] = hc.KIND_WALKS, hc.KIND_GRADIENT
    asset.supported[10, 11] = False
    path = tmp_path / f"{MAP}.height.npz"
    hc.save_asset(path, asset)
    return asset, path


def test_the_payload_carries_each_cells_kind(tmp_path):
    asset, path = kinded(tmp_path)
    p = height_viewer.map_payload(MAP, path, None, "preview")
    flat = []
    for value, run in zip(p["kinds"][0::2], p["kinds"][1::2]):
        flat += [value] * run
    assert flat == asset.kind.ravel().tolist() and p["kind"] == "preview", "beside the source kind, not over it"


@needs_node
def test_the_hover_names_the_kind_and_falls_back_without_one(tmp_path):
    _, path = kinded(tmp_path)
    p = height_viewer.map_payload(MAP, path, None, "preview")
    cells = [cell(10, 10), cell(10, 11), cell(11, 10), cell(12, 10)]
    body = "function run(p, map) { return %s.map(function (c) { return H.kindWord(map, c); }); }" % json.dumps(cells)
    assert run_node(body, {"payload": p}) == ["from walks alone", "filled along a gradient", "from stands",
                                              "filled from neighbours"]
    del p["kinds"]                     # a payload from before the kinds: what `supported` says
    assert run_node(body, {"payload": p}) == ["supported", "filled from neighbours", "supported",
                                              "filled from neighbours"]


def test_the_page_has_the_slope_rules_layer():
    page = (WEBAPP / "scripts" / "height_viewer.template.html").read_text(encoding="utf-8")
    assert 'id="lyNew"' in page and '"lyTwo", "lyDrops", "lySame", "lyNew"' in page and "H.kindWord(map, cell)" in page


def test_db_sources_write_the_stored_assets_for_the_page(tmp_path):
    from types import SimpleNamespace

    asset = synthetic()
    path = tmp_path / "a.npz"
    hc.save_asset(path, asset)
    data = path.read_bytes()
    report = report_for(asset)
    rows = [SimpleNamespace(id=3, map_name=MAP, digest=asset.digest, status="active", report=report, asset=data),
            SimpleNamespace(id=2, map_name=MAP, digest="0" * 12, status="rejected", report={}, asset=data),
            SimpleNamespace(id=1, map_name="Bind", digest="1" * 12, status="superseded", report={}, asset=data)]
    got = height_viewer.db_sources(rows, tmp_path / "out", None, every=False)
    assert [(name, kind) for name, _, _, kind in got] == [(MAP, "active")]
    name, written, wrapper, _ = got[0]
    assert written.read_bytes() == data and wrapper == {"height_sha": asset.digest, "height": report}
    every = height_viewer.db_sources(rows, tmp_path / "out", [MAP], every=True)
    assert [(name, kind) for name, _, _, kind in every] == [(MAP, "active"), (f"{MAP} rejected {'0' * 12} row 2", "rejected")]
    label = f"{MAP} rejected {'0' * 12} row 2"
    payload = height_viewer.map_payload(label, every[1][1], every[1][2], "rejected", map_name=MAP)
    assert payload["name"] == label and payload["kind"] == "rejected" and payload["height_sha"] == asset.digest


def test_db_history_keeps_distinct_rows_with_the_same_engine_digest(tmp_path):
    from types import SimpleNamespace

    current = synthetic()
    older = synthetic()
    older.kind[current.supported] = hc.KIND_WALKS
    assert current.digest == older.digest  # kind is deliberately outside the engine hash
    rows = []
    for row_id, status, asset in ((3, "active", current), (2, "rejected", older), (1, "rejected", current)):
        path = tmp_path / f"{row_id}.npz"
        hc.save_asset(path, asset)
        rows.append(SimpleNamespace(id=row_id, map_name=MAP, digest=asset.digest, status=status,
                                    report={"row": row_id}, asset=path.read_bytes()))
    got = height_viewer.db_sources(rows, tmp_path / "out", None, every=True)
    assert len({label for label, _, _, _ in got}) == len({path for _, path, _, _ in got}) == 3
    assert got[0][0] == MAP
    for row, (_, written, wrapper, _) in zip(rows, got):
        assert written.read_bytes() == row.asset and wrapper["height"] == {"row": row.id}
    assert np.array_equal(hc.load_asset(got[0][1]).kind, current.kind)
    assert np.array_equal(hc.load_asset(got[1][1]).kind, older.kind)
