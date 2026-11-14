"""The map-control drawing page (Stage 6; scripts/control_tagger.py and control_tagger_core.js): its JS
composes the sight and walk masks and runs the kill-line test exactly like app/control/geometry.py, and
exporting an unchanged page gives back the loaded tags.json. The JS checks need Node; they skip without it."""

import base64
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(WEBAPP / "scripts"))

import control_tagger  # noqa: E402

from app.control import geometry as cg  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.control import height_refinement as hr  # noqa: E402

CORE = WEBAPP / "scripts" / "control_tagger_core.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
TAGS = json.loads((cg.ASSET_DIR / "tags.json").read_text(encoding="utf-8"))
LINES = json.loads(control_tagger.KILL_LINES.read_text(encoding="utf-8"))["maps"]

RUN = """
  const T = require(process.argv[1]);
  let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
    const p = JSON.parse(input);
    process.stdout.write(JSON.stringify(run(p)));
  });
  function load(m) {
    return {candidates: m.candidates, params: m.params, image_sha: m.image_sha, lines: m.lines,
            labels: T.rleDecode(m.labels, Int32Array), opaque: T.rleDecode(m.opaque),
            outline: m.outline ? T.rleDecode(m.outline) : null};
  }
  function packBits(a) {
    const out = new Uint8Array(a.length / 8);
    for (let i = 0; i < a.length; i++) if (a[i]) out[i >> 3] |= 128 >> (i & 7);
    return Buffer.from(out).toString("base64");
  }
"""


def run_node(body: str, payload):
    completed = subprocess.run([NODE, "-e", RUN + body, str(CORE)], input=json.dumps(payload), capture_output=True,
                               text=True, encoding="utf-8", timeout=120, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


COMPOSE = """
  function run(p) {
    const map = load(p.map), e = T.editsFrom(p.entry), m = T.compose(map, e), r = T.riskOne(m.sight, map.lines, m.cover);
    return {sight: packBits(m.sight), walk: packBits(m.walk), blocked: r.blocked, share: r.share, passes: r.passes};
  }
"""


def paint_rect(x0, y0, x1, y1) -> str:
    cells = np.zeros((cg.PAINT_GRID, cg.PAINT_GRID), bool)
    cells[y0 // 4:y1 // 4, x0 // 4:x1 // 4] = True
    return base64.b64encode(np.packbits(cells.ravel().astype(np.uint8), bitorder="little").tobytes()).decode()


def python_masks(name, entry):
    rgba = np.array(Image.open(cg.MINIMAP_DIR / f"{name}.png").convert("RGBA"))
    return cg.masks(rgba, entry)


def check(name, entry):
    m = python_masks(name, entry)
    lines = LINES.get(name) or []
    got = run_node(COMPOSE, {"map": control_tagger.map_data(name, entry, lines), "entry": entry})
    assert got["sight"] == base64.b64encode(np.packbits(m.sight)).decode(), f"{name} sight"
    assert got["walk"] == base64.b64encode(np.packbits(m.walk)).decode(), f"{name} walk"
    on = (lambda x, y: m.cover is not None and bool(m.cover[min(1023, int(y)), min(1023, int(x))]))
    expected = [k for k, (ax, ay, bx, by) in enumerate(lines) if not (on(ax, ay) or on(bx, by))
                and cg.line_blocked(m.sight, (ax, ay), (bx, by))]
    assert got["blocked"] == expected, f"{name} kill lines"
    return got


def test_abyss_tags_and_see_across_paint_compose_exactly_like_geometry():
    got = check("Abyss", TAGS["maps"]["Abyss"])
    assert got["blocked"] == [] and got["passes"] is True        # index.json: 0 of 200 blocked


def test_summit_cover_tags_compose_and_judge_kill_lines_like_geometry():
    # 22 box tags: each box is its outline's inside, and lines with an end on a box aren't judged (2026-10-26)
    got = check("Summit", TAGS["maps"]["Summit"])
    index = json.loads((cg.ASSET_DIR / "index.json").read_text(encoding="utf-8"))["maps"]["Summit"]["kill_lines"]
    assert len(got["blocked"]) == index["blocked"] and got["passes"] is True


def test_with_every_kill_line_on_cover_nothing_is_judged_alike_in_both():
    # Branch review B21: no judged line is no evidence; Python and the tagger both say so (share and passes None).
    entry = {**TAGS["maps"]["Ascent"], "cover_paint": paint_rect(0, 0, 1024, 1024)}
    got = check("Ascent", entry)
    m = python_masks("Ascent", entry)
    py = cg.kill_line_result(m.sight, LINES["Ascent"], m.cover)
    assert py["qualifying"] == 0 and py["share"] is None and py["passes"] is None
    assert got["passes"] is None


def test_ascent_walls_only_blocks_the_same_lines_as_index_json():
    got = check("Ascent", TAGS["maps"]["Ascent"])
    index = json.loads((cg.ASSET_DIR / "index.json").read_text(encoding="utf-8"))["maps"]["Ascent"]["kill_lines"]
    assert len(got["blocked"]) == index["blocked"] and got["passes"] == index["passes"]


def test_every_paint_composes_exactly_like_geometry():
    entry = {**TAGS["maps"]["Ascent"], "cover_paint": paint_rect(440, 380, 520, 460),
             "cant_walk_paint": paint_rect(300, 300, 360, 420), "uncertain_paint": paint_rect(0, 0, 1024, 1024),
             "see_across_paint": paint_rect(430, 370, 600, 600), "barrier_paint": paint_rect(0, 0, 1024, 1024)}
    got = check("Ascent", entry)
    assert len(got["blocked"]) > 2, "the cover paint should block some kill lines"
    # a barrier paint changes neither mask
    bare = {k: v for k, v in entry.items() if k != "barrier_paint"}
    rgba = np.array(Image.open(cg.MINIMAP_DIR / "Ascent.png").convert("RGBA"))
    with_barrier, without = cg.masks(rgba, entry), cg.masks(rgba, bare)
    assert (with_barrier.sight == without.sight).all() and (with_barrier.walk == without.walk).all()


def test_a_candidate_tag_composes_exactly_like_geometry():
    rgba = np.array(Image.open(cg.MINIMAP_DIR / "Split.png").convert("RGBA"))
    _, found = cg.candidates(rgba, {})
    closed = [c for c in found if c["kind"] == "closed"][:3]
    entry = {**TAGS["maps"]["Split"], "tags": [{"id": c["id"], "tag": t, "kind": c["kind"], "bbox": c["bbox"], "px": c["px"]}
                                              for c, t in zip(closed, ("cover", "seeover", "walkable"))]}
    check("Split", entry)


EXPORT = """
  function run(p) {
    const maps = {}, edits = {};
    for (const name of Object.keys(p.maps)) { maps[name] = load(p.maps[name]); edits[name] = T.editsFrom(p.tags.maps[name]); }
    const untouched = T.exportTags(p.tags, maps, edits);
    for (const name of Object.keys(edits)) edits[name].touched = true;
    const touched = T.exportTags(p.tags, maps, edits);
    edits.Ascent.tags[p.newId] = "cover";
    edits.Ascent.paints.cant_walk_paint = new Uint8Array(T.P * T.P); edits.Ascent.paints.cant_walk_paint[5] = 1;
    edits.Ascent.cover_reviewed = true;
    const edited = T.exportTags(p.tags, maps, edits);
    return {untouched, touched, edited};
  }
"""


def test_an_unchanged_page_exports_the_loaded_tags():
    names = ["Abyss", "Ascent"]
    maps = {n: control_tagger.map_data(n, TAGS["maps"][n], LINES.get(n) or []) for n in names}
    candidate = next(c for c in maps["Ascent"]["candidates"] if c["kind"] == "closed")
    got = run_node(EXPORT, {"tags": TAGS, "maps": maps, "newId": candidate["id"]})
    assert got["untouched"] == TAGS
    assert got["touched"] == TAGS, "re-packed paint and kept rows must round-trip"
    ascent = got["edited"]["maps"]["Ascent"]
    assert ascent["tags"] == [{"id": candidate["id"], "tag": "cover", "kind": "closed", "bbox": candidate["bbox"],
                               "px": candidate["px"]}]
    assert ascent["params"] == maps["Ascent"]["params"] and ascent["image_sha"] == maps["Ascent"]["image_sha"]
    assert ascent["cover_reviewed"] is True and ascent["specials"] == TAGS["maps"]["Ascent"]["specials"]
    assert got["edited"]["maps"]["Abyss"] == TAGS["maps"]["Abyss"]
    # the export is a valid tags.json: geometry builds from it and the new tag blocks sight
    rgba = np.array(Image.open(cg.MINIMAP_DIR / "Ascent.png").convert("RGBA"))
    m = cg.masks(rgba, ascent)
    x0, y0, x1, y1 = candidate["bbox"]
    assert m.sight[y0:y1 + 1, x0:x1 + 1].any()


def test_the_page_builds_with_every_map(tmp_path):
    out = tmp_path / "tagger.html"
    assert control_tagger.main(["--out", str(out), "--map", "Abyss", "--map", "Breeze"]) == 0
    page = out.read_text(encoding="utf-8")
    assert "global.TaggerCore = api" in page and "/*DATA*/" not in page and "/*CORE*/" not in page
    data = json.loads(page.split("var DATA = ", 1)[1].split(";\n", 1)[0])
    assert sorted(data["maps"]) == ["Abyss", "Breeze"] and data["tags"] == TAGS
    assert data["height_paint"] == json.loads((cg.ASSET_DIR / "height_paint.json").read_text(encoding="utf-8"))
    assert data["maps"]["Breeze"]["lines"] == [] and len(data["maps"]["Abyss"]["lines"]) == 200
    assert all("height_overlay" not in m for m in data["maps"].values())


@pytest.fixture
def exported_height_page(tmp_path):
    floors = np.full((cg.GRID, cg.GRID, hc.MAX_FLOORS), -1, np.int16)
    floors[20, 20, 0] = 0
    unresolved = np.zeros((cg.GRID, cg.GRID), bool)
    unresolved[21, 21] = True
    fine = np.full((4, hc.MAX_FLOORS), -1, np.int16)
    fine[0, :2] = [0, 60]
    fine[2, 0] = 60
    for name in ("Ascent", "Abyss"):
        history = hr.history(name, [10 * cg.GRID + 10])
        asset = hc.HeightAsset(
            floors.copy(), np.zeros_like(floors), floors[..., 0] >= 0, unresolved.copy(),
            np.empty((0, 5), np.int32),
            refinement={"v": 1, "history": history, "history_sha256": hr.identity(history),
                        "previous_history_sha256": hr.identity(hr.history(name)), "evidence_sha256": "0" * 64},
            fine_cells=np.array(hr._child_ids(10 * cg.GRID + 10), np.int32), fine_floors=fine.copy(),
            fine_spread=np.zeros_like(fine), fine_supported=fine[:, 0] >= 0,
            fine_unresolved=np.array([False, True, False, True]),
            fine_kind=np.array([hc.KIND_STANDS, hc.KIND_NONE, hc.KIND_STANDS, hc.KIND_NONE], np.int8),
            fine_edges=np.empty((0, 5), np.int32),
        )
        hc.save_asset(tmp_path / f"{name}.height.npz", asset)
        (tmp_path / f"{name}.height.json").write_text(
            json.dumps({"height_sha": asset.digest, "height": {}}), encoding="utf-8")
    out = tmp_path / "tagger.html"
    assert control_tagger.main(["--out", str(out), "--heights-dir", str(tmp_path),
                                "--map", "Ascent", "--map", "Abyss", "--map", "Breeze"]) == 0
    page = out.read_text(encoding="utf-8")
    return page, json.loads(page.split("var DATA = ", 1)[1].split(";\n", 1)[0])


def test_exported_heights_embed_a_review_png_per_exported_map(exported_height_page):
    _, data = exported_height_page
    assert {name for name, m in data["maps"].items() if "height_overlay" in m} == {"Ascent", "Abyss"}
    for name in ("Ascent", "Abyss"):
        with Image.open(io.BytesIO(base64.b64decode(data["maps"][name]["height_overlay"], validate=True))) as img:
            assert img.format == "PNG" and img.size == (1024, 1024)
            assert img.getpixel((85, 81)) == (220, 40, 40)  # unresolved child
            assert img.getpixel((169, 169)) == (220, 40, 40)  # unresolved parent
            assert img.getpixel((81, 80)) == (255, 255, 255)  # two-floor child mark
            assert img.getpixel((81, 81)) == (40, 90, 200)  # low child
            assert img.getpixel((81, 85)) == (255, 240, 30)  # high child


def test_height_overlay_controls_draw_under_paint_and_hide_on_unexported_maps(exported_height_page):
    page, _ = exported_height_page
    assert page.index('id="base"') < page.index('id="heightOverlay"') < page.index('id="tint"')
    assert 'id="heightControls" hidden' in page
    modes = re.search(r"var modes = \{([^}]+)\}", page).group(1)
    assert "j" not in dict(re.findall(r'(\w+)\s*:\s*"([^"]+)"', modes))
    assert 'e.key.toLowerCase() === "j"' in page
    assert '$("showHeights").addEventListener("change", paintHeights)' in page
    assert '$("heightOpacity").addEventListener("input", paintHeights)' in page
    assert "paintHeights();" in page_function(page, "recompute")
    got = run_node("""
      function run(p) {
        var PX = 1024, draws = [], clears = 0;
        var ctx = {clearRect: () => clears++, drawImage: function (img) { draws.push([img, this.globalAlpha]); }};
        var elements = {heightOverlay: {getContext: () => ctx}, heightControls: {},
                        showHeights: {checked: true}, heightOpacity: {value: '60'}, heightOpacityText: {}};
        var $ = id => elements[id], state = {map: 'Ascent'};
        var DATA = {maps: {Ascent: {height_overlay: 'png'}, Breeze: {}}};
        var maps = {Ascent: {heightImage: 'loaded'}, Breeze: {}};
        eval(p.draw);
        paintHeights();
        elements.heightOpacity.value = '25'; paintHeights();
        elements.showHeights.checked = false; paintHeights();
        state.map = 'Breeze'; paintHeights();
        return {draws, clears, hidden: elements.heightControls.hidden,
                opacityText: elements.heightOpacityText.textContent, alpha: ctx.globalAlpha};
      }
    """, {"draw": page_function(page, "paintHeights")})
    assert got == {"draws": [["loaded", 0.6], ["loaded", 0.25]], "clears": 4,
                   "hidden": True, "opacityText": "25%", "alpha": 1}


def test_the_page_includes_areas_data_panel_and_distinct_mode_keys(tmp_path):
    out = tmp_path / "tagger.html"
    assert control_tagger.main(["--out", str(out), "--map", "Sunset", "--areas-dir", str(tmp_path)]) == 0
    page = out.read_text(encoding="utf-8")
    assert "/*AREAS_UI*/" not in page and "window.taggerAreas" in page
    for element in ("areasCard", "areasCanvas", "areasFloor", "areasPinsFile", "areasReferenceFile", "areasExport",
                    "areasExportFixture"):
        assert f'id="{element}"' in page
    data = json.loads(page.split("var DATA = ", 1)[1].split(";\n", 1)[0])["maps"]["Sunset"]["areas"]
    assert data["map"] == "Sunset" and len(data["labels"]) == 17 and data["draft"]["cells"]
    mode_keys = re.search(r"var modes = \{([^}]+)\}", page)
    pairs = re.findall(r'(\w+)\s*:\s*"([^"]+)"', mode_keys.group(1))
    assert len({key for key, _ in pairs}) == len(pairs)
    assert {"k": "chokes", "a": "choke_paint", "f": "features", "m": "areas"}.items() <= set(pairs)
    assert all(f'data-mode="{mode}"' in page for _, mode in pairs)
    assert {"o": "open", "h": "ground", "t": "top"}.items() <= set(pairs)


def height_cells(*indices):
    cells = np.zeros((cg.PAINT_GRID, cg.PAINT_GRID), bool)
    cells.ravel()[list(indices)] = True
    return cg.pack_paint(cells)


def page_function(page, name):
    match = re.search(r"^  function " + name + r"\([^\n]*\) \{.*?^  \}", page, re.M | re.S)
    assert match, name
    return match.group(0)


@pytest.fixture
def height_page(tmp_path):
    source = {"maps": {"Ascent": {"open": height_cells(0, 7, 8, 65535), "ground": height_cells(12),
                                   "top": height_cells(256)}, "Abyss": {"top": height_cells(19)}}}
    path = tmp_path / "height_paint.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    out = tmp_path / "height-tagger.html"
    assert control_tagger.main(["--out", str(out), "--map", "Ascent", "--height-paint", str(path)]) == 0
    page = out.read_text(encoding="utf-8")
    data = json.loads(page.split("var DATA = ", 1)[1].split(";\n", 1)[0])
    assert data["height_paint"] == source
    expected_sha = hashlib.sha256(json.dumps(source, separators=(",", ":")).encode()).hexdigest()
    assert data["height_paint_sha"] == expected_sha
    return page, data


def test_height_brushes_are_exclusive_and_erase_only_the_selected_brush():
    got = run_node("""
      function run(p) {
        const store = {};
        T.paintHeightCells(store, 'open', [0, 7, 8], 1);
        T.paintHeightCells(store, 'ground', [7, 8], 1);
        const before = T.HEIGHT_PAINTS.map(k => Array.from(store[k] || []).slice(0, 9));
        T.paintHeightCells(store, 'top', [8], 1);
        T.paintHeightCells(store, 'ground', [7, 8], 0);
        return {kinds: T.HEIGHT_PAINTS, before,
                after: T.HEIGHT_PAINTS.map(k => Array.from(store[k] || []).slice(0, 9))};
      }
    """, {})
    assert got["kinds"] == ["open", "ground", "top"]
    assert [i for i, v in enumerate(got["before"][0]) if v] == [0]
    assert [i for i, v in enumerate(got["before"][1]) if v] == [7, 8]
    assert [[i for i, v in enumerate(row) if v] for row in got["after"]] == [[0], [], [8]]


def test_height_export_round_trips_and_python_loads_js_packing(height_page, tmp_path):
    _, data = height_page
    got = run_node("""
      function run(p) {
        const edits = {Ascent: T.editsFrom(p.tags.maps.Ascent, p.height_paint.maps.Ascent)};
        const pageState = {edits, height_paint: p.height_paint};
        const unchanged = T.heightPaintExport(pageState);
        edits.Ascent.touched = true;
        const tags = T.exportTags(p.tags, {Ascent: load(p.maps.Ascent)}, edits);
        edits.Ascent.heightPaints.top.fill(0);
        edits.Empty = T.editsFrom({}, {open: T.packPaint(new Uint8Array(T.P * T.P))});
        const emptied = T.heightPaintExport(pageState);
        return {unchanged, emptied, tags, legacyPaints: T.PAINTS};
      }
    """, data)
    assert got["unchanged"] == data["height_paint"]
    assert got["tags"] == data["tags"]
    assert not {"open", "ground", "top", "heightPaints"}.intersection(got["legacyPaints"])
    assert got["emptied"] == {"maps": {"Ascent": {k: v for k, v in data["height_paint"]["maps"]["Ascent"].items()
                                                if k != "top"}, "Abyss": data["height_paint"]["maps"]["Abyss"]}}
    (tmp_path / "height_paint.json").write_text(json.dumps(got["unchanged"]), encoding="utf-8")
    masks, sha = cg.load_height_paint("Ascent", tmp_path)
    assert sha
    for kind, expected in {"open": [0, 7, 8, 65535], "ground": [12], "top": [256]}.items():
        assert np.flatnonzero(masks[kind]).tolist() == expected


def test_page_height_dab_undo_save_restore_erase_and_feature_replacement(height_page):
    page, data = height_page
    assert "T.paintHeightCells" in page_function(page, "dab")
    assert 'id="exportHeightPaint"' in page and 'a.download = "height_paint.json"' in page
    for kind, label in [("open", "Open underneath"), ("ground", "Ground only"), ("top", "Solid to the top")]:
        assert f'data-mode="{kind}"' in page and label in page
    functions = "\n".join(page_function(page, name) for name in
                          ["cellsCopy", "snapshot", "save", "restore", "mapEdits", "beforeChange", "changed", "dab", "doUndo"])
    features = (WEBAPP / "scripts" / "control_tagger_features.js").read_text(encoding="utf-8")
    functions += "\n" + page_function(features, "rebuildLegacy")
    got = run_node("""
      function run(p) {
        var edits = {Ascent: T.editsFrom(p.data.tags.maps.Ascent, p.data.height_paint.maps.Ascent)};
        var state = {map: 'Ascent', mode: 'ground', lastPaint: 'ground', brush: 1};
        var undo = [], chokeState = {}, P = T.P, PX = T.PX, STORE = 'test-height';
        var memory = {}, localStorage = {setItem: (k,v) => memory[k] = v, getItem: k => memory[k]};
        var $ = () => ({}), recompute = () => {}, saveChokes = () => {};
        eval(p.functions);
        const original = T.heightPaintExport({edits});
        beforeChange(); dab(2, 2); changed();
        const painted = T.heightPaintExport({edits});
        doUndo();
        const undone = T.heightPaintExport({edits});
        beforeChange(); dab(2, 2); changed();
        edits.Ascent = T.editsFrom({}); restore();
        const restored = T.heightPaintExport({edits});
        state.mode = 'erase'; dab(2, 2); changed();
        const erased = T.heightPaintExport({edits});
        var TG = {edits, loaded: {}}, D = {maps: {Ascent: {}}}, canonical = {maps: {Ascent: {}}};
        rebuildLegacy(true);
        const replaced = T.heightPaintExport({edits});
        save();
        edits.Ascent = T.editsFrom(p.data.tags.maps.Ascent, p.data.height_paint.maps.Ascent); restore();
        const replacedRestored = T.heightPaintExport({edits});
        T.HEIGHT_PAINTS.forEach(kind => T.paintHeightCells(edits.Ascent.heightPaints, kind, [7,8,12,256,65535], 0));
        edits.Ascent.touched = true; save();
        edits.Ascent = T.editsFrom(p.data.tags.maps.Ascent, p.data.height_paint.maps.Ascent); restore();
        return {original, painted, undone, restored, erased, replaced, replacedRestored,
                cleared: T.heightPaintExport({edits})};
      }
    """, {"functions": functions, "data": data})
    assert got["undone"] == got["original"]
    assert got["restored"] == got["painted"]
    assert got["painted"] != got["original"]
    assert got["replaced"] == got["erased"]
    assert got["replacedRestored"] == got["erased"]
    assert got["cleared"] == {"maps": {}}
    erased = got["erased"]["maps"]["Ascent"]
    assert erased["open"] == height_cells(7, 8, 65535)
    assert erased["ground"] == height_cells(12)


@pytest.mark.parametrize("empty_height", [False, True])
@pytest.mark.parametrize("current_draft", [None, {}, {"Ascent": {"tags": {"2": "walkable"},
                                                            "paints": {}, "cover_reviewed": False}}])
def test_legacy_draft_upgrade_keeps_embedded_heights(height_page, empty_height, current_draft):
    page, data = height_page
    if empty_height:
        data["height_paint"] = {"maps": {}}
        data["height_paint_sha"] = hashlib.sha256(b'{"maps":{}}').hexdigest()
    store_code = page.split('  var STORE = ', 1)[1].split('  var $ = ', 1)[0]
    functions = "\n".join(page_function(page, name) for name in ["cellsCopy", "save", "restore"])
    legacy = {"Ascent": {"tags": {"1": "cover"}, "paints": {"cover_paint": height_cells(3, 9)},
                         "cover_reviewed": True}}
    got = run_node("""
      function run(p) {
        var DATA = p.data, STORE, memory = {};
        var localStorage = {setItem: (k,v) => memory[k] = v, getItem: k => memory[k]};
        var edits = {Ascent: T.editsFrom(DATA.tags.maps.Ascent, DATA.height_paint.maps.Ascent)};
        // The key and row format used before height painting was added.
        const legacyKey = 'control-tagger-v3:' + (function (s) {
          var h = 5381;
          for (var i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
          return h.toString(36) + '.' + s.length;
        })(JSON.stringify(DATA.tags));
        memory[legacyKey] = JSON.stringify(p.legacy);
        eval('STORE = ' + p.store); eval(p.functions);
        if (p.current !== null) memory[STORE] = JSON.stringify(p.current);
        const embedded = T.heightPaintExport({edits});
        restore();
        const first = {tags: edits.Ascent.tags, touched: edits.Ascent.touched,
                       cover: edits.Ascent.cover_reviewed,
                       paint: edits.Ascent.paints.cover_paint ? T.packPaint(edits.Ascent.paints.cover_paint) : null,
                       heights: T.heightPaintExport({edits})};
        const migrated = memory[STORE] ? JSON.parse(memory[STORE]) : null;
        // Reload from the new key after changing the old draft: migration happens once.
        memory[legacyKey] = JSON.stringify({});
        edits.Ascent = T.editsFrom(DATA.tags.maps.Ascent, DATA.height_paint.maps.Ascent);
        restore();
        return {first, embedded, migrated, reloadedTags: edits.Ascent.tags,
                reloadedHeights: T.heightPaintExport({edits}),
                key: STORE, expectedKey: legacyKey + ':' + DATA.height_paint_sha};
      }
    """, {"data": data, "store": store_code, "functions": functions, "legacy": legacy,
          "current": current_draft})
    assert got["key"] == got["expectedKey"]
    assert got["first"]["heights"] == got["reloadedHeights"] == got["embedded"]
    if current_draft is None:
        assert got["first"]["tags"] == got["reloadedTags"] == legacy["Ascent"]["tags"]
        assert got["first"]["touched"] is True
        assert got["first"]["cover"] is True
        assert got["first"]["paint"] == legacy["Ascent"]["paints"]["cover_paint"]
        assert got["migrated"]["Ascent"]["tags"] == legacy["Ascent"]["tags"]
        assert got["migrated"]["Ascent"]["heightTouched"] is False
    else:
        assert got["migrated"] == current_draft
        assert got["first"]["tags"] == got["reloadedTags"]
        assert got["first"]["tags"] != legacy["Ascent"]["tags"]


def test_height_source_identity_prevents_an_old_local_draft(height_page):
    page, data = height_page
    store_code = page.split('  var STORE = ', 1)[1].split('  var $ = ', 1)[0]
    functions = "\n".join(page_function(page, name) for name in ["save", "restore"])
    got = run_node("""
      function run(p) {
        var DATA = p.data, STORE, memory = {};
        var localStorage = {setItem: (k,v) => memory[k] = v, getItem: k => memory[k]};
        var edits = {Ascent: T.editsFrom(DATA.tags.maps.Ascent, DATA.height_paint.maps.Ascent)};
        eval('STORE = ' + p.store); eval(p.functions);
        const oldKey = STORE;
        T.paintHeightCells(edits.Ascent.heightPaints, 'top', [0], 1);
        edits.Ascent.touched = true; save();
        const saved = T.heightPaintExport({edits});
        edits.Ascent = T.editsFrom(DATA.tags.maps.Ascent, DATA.height_paint.maps.Ascent); restore();
        const same = T.heightPaintExport({edits});
        DATA.height_paint = {maps: {Ascent: {ground: p.newPaint}}};
        DATA.height_paint_sha = p.newSha;
        eval('STORE = ' + p.store);
        edits.Ascent = T.editsFrom(DATA.tags.maps.Ascent, DATA.height_paint.maps.Ascent); restore();
        return {oldKey, newKey: STORE, saved, same, changed: T.heightPaintExport({edits})};
      }
    """, {"data": data, "store": store_code, "functions": functions, "newPaint": height_cells(23),
          "newSha": hashlib.sha256(json.dumps({"maps": {"Ascent": {"ground": height_cells(23)}}},
                                             separators=(",", ":")).encode()).hexdigest()})
    assert got["same"] == got["saved"]
    assert got["oldKey"] != got["newKey"]
    assert got["changed"] == {"maps": {"Ascent": {"ground": height_cells(23)}}}


def test_page_draws_all_three_height_brushes_transparently(height_page):
    page, data = height_page
    colours = re.search(r"  var PAINT_COLOURS = (.*?);", page, re.S).group(0)
    got = run_node("""
      function run(p) {
        var P = T.P, PX = T.PX, image;
        var maps = {Ascent: {labels: new Int32Array(PX * PX), opaque: new Uint8Array(PX * PX)}};
        var edits = {Ascent: T.editsFrom({}, p.paint)};
        var state = {map: 'Ascent', masks: {}, byId: {}};
        var $ = id => ({checked: id === 'showPaint', getContext: () => ({
          createImageData: () => ({data: new Uint8ClampedArray(PX * PX * 4)}),
          putImageData: img => image = img.data
        })});
        function mapEdits() {return edits.Ascent;}
        eval(p.colours); eval(p.draw); paintTint();
        return [0,12,256].map(cell => {
          const pixel = Math.floor(cell / P) * 4 * PX + (cell % P) * 4;
          return Array.from(image.slice(pixel * 4, pixel * 4 + 4));
        });
      }
    """, {"paint": data["height_paint"]["maps"]["Ascent"], "colours": colours,
          "draw": page_function(page, "paintTint")})
    assert got == [[66, 180, 255, 150], [100, 210, 100, 150], [255, 150, 60, 150]]


@pytest.mark.parametrize("document", [{"maps": []}, {"maps": {"Ascent": {"open": "bad"}}},
                                     {"maps": {"Ascent": {"open": height_cells(7), "top": height_cells(7)}}}])
def test_page_rejects_invalid_height_paint(tmp_path, document):
    path = tmp_path / "height_paint.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError):
        control_tagger.render(TAGS, {}, height_paint=path)


def test_page_missing_height_paint_loads_an_empty_document(tmp_path):
    page = control_tagger.render(TAGS, {}, height_paint=tmp_path / "missing.json")
    data = json.loads(page.split("var DATA = ", 1)[1].split(";\n", 1)[0])
    assert data["height_paint"] == {"maps": {}}


# ---- Choke mode (timing-gaps spec section 2: add, delete, move, rename; hand edits survive re-detection) ----

def choke(id, cells, name=None, source="auto", deleted=False):
    return {"id": id, "name": name or str(id), "cells": cells, "source": source, "deleted": deleted}


CHOKE_EDITS = """
  function run(p) {
    const before = JSON.stringify(p.chokes);
    const out = {
      selectHit: T.selectAt(p.chokes, 130), selectMiss: T.selectAt(p.chokes, 5), selectDeleted: T.selectAt(p.chokes, 900),
      renamed: T.rename(p.chokes, 1, "A Main"), removed: T.remove(p.chokes, 1),
      moved: T.move(p.chokes, 1, 1, -1, 5), movedHand: T.move(p.chokes, 2, -2, 0, 5, [p.chokes[0], p.chokes[2]]),
      movedLoadedHand: T.move(p.chokes, 2, -2, 0, 5),
      movedOff: T.move(p.chokes, 2, 0, 200, 5), movedLow: T.move(p.chokes, 1, 1, 0, 1),
      movedNone: T.move(p.chokes, 1, 0, 0, 5),
      movedTwice: T.move(T.move(p.chokes, 1, 1, 0, 5).chokes, 1, 1, 0, 6, p.chokes),
      renamedThenMoved: T.move(T.rename(p.chokes, 1, "A Main"), 1, 0, 1, 5, p.chokes),
      added: T.add(p.chokes, [4000, 3999, 4000], 5), addedLow: T.add(p.chokes, [10], 1), addedNone: T.add(p.chokes, [], 5),
      exported: T.exportAsset("Ascent", [p.chokes[2], p.chokes[0], p.chokes[1]], 2),
    };
    out.untouched = JSON.stringify(p.chokes) === before;
    return out;
  }
"""


def test_choke_edits_select_rename_delete_move_add_and_export():
    chokes = [choke(1, [129, 130, 258]),                          # row 1 col 1, row 1 col 2, row 2 col 2
              choke(2, [127, 255], name="Edge", source="hand"),   # the right edge, rows 0 and 1
              choke(3, [900], deleted=True, source="hand")]
    got = run_node(CHOKE_EDITS, {"chokes": chokes})
    assert got["untouched"], "edits must not change their input"
    assert got["selectHit"]["id"] == 1 and got["selectMiss"] is None and got["selectDeleted"] is None

    assert got["renamed"][0] == choke(1, [129, 130, 258], name="A Main", source="hand")
    assert got["renamed"][1:] == chokes[1:]
    # a delete is a tombstone that keeps its cells
    assert got["removed"][0] == choke(1, [129, 130, 258], source="hand", deleted=True)

    # moving a detected choke: one column right, one row up; its old cells keep a tombstone with a new id
    moved = got["moved"]
    assert moved["chokes"][0] == choke(1, [2, 3, 131], source="hand")
    assert moved["chokes"][3] == choke(5, [129, 130, 258], source="hand", deleted=True)
    assert moved["nextId"] == 6 and len(moved["chokes"]) == 4
    # a choke added this session (not in the loaded list) moves without a tombstone; cells pushed off the
    # grid's edge are dropped, never wrapped
    assert got["movedHand"]["chokes"][1] == choke(2, [125, 253], name="Edge", source="hand")
    assert got["movedHand"]["nextId"] == 5 and len(got["movedHand"]["chokes"]) == 3
    # a hand choke that was loaded leaves a tombstone on its loaded cells like any other
    assert got["movedLoadedHand"]["chokes"][1] == choke(2, [125, 253], name="Edge", source="hand")
    assert got["movedLoadedHand"]["chokes"][3] == choke(5, [127, 255], source="hand", deleted=True)
    assert got["movedOff"]["chokes"] == chokes, "a move that drops every cell is refused"
    assert got["movedNone"] == {"chokes": chokes, "nextId": 5}
    # one tombstone per detected choke, on its detected cells, however many steps it moves
    twice = got["movedTwice"]
    assert twice["chokes"][0]["cells"] == [131, 132, 260] and twice["nextId"] == 6
    assert [c for c in twice["chokes"] if c["deleted"] and c["id"] != 3] == [choke(5, [129, 130, 258], source="hand", deleted=True)]
    # a choke renamed (so already "hand") before its first move still leaves the tombstone, given the loaded list
    both = got["renamedThenMoved"]
    assert both["chokes"][0] == choke(1, [257, 258, 386], name="A Main", source="hand")
    assert both["chokes"][3] == choke(5, [129, 130, 258], source="hand", deleted=True) and both["nextId"] == 6
    # a new id comes after the highest id even when the given next id is lower
    assert got["movedLow"]["chokes"][3]["id"] == 4 and got["movedLow"]["nextId"] == 5

    added = got["added"]
    assert added["chokes"][3] == choke(5, [3999, 4000], source="hand") and added["nextId"] == 6
    assert got["addedLow"]["chokes"][3]["id"] == 4 and got["addedLow"]["nextId"] == 5
    assert got["addedNone"] == {"chokes": chokes, "nextId": 5}

    assert got["exported"] == {"version": 1, "map": "Ascent", "next_id": 4, "chokes": chokes}


CHOKE_EXPORT = """
  function run(p) {
    const text = T.assetText;
    const c = p.chokes, n = p.nextId, a = p.a, b = p.b;
    const moved = T.move(c, b, 0, 1, n), added = T.add(c, p.newCells, n);
    return {
      unchanged: text(T.exportAsset("Ascent", c, n)),
      unicode: text(T.exportAsset("Ascent", T.rename(c, a, p.unicodeName), n)),
      renamed: text(T.exportAsset("Ascent", T.rename(c, a, "Heaven"), n)),
      moved: text(T.exportAsset("Ascent", moved.chokes, moved.nextId)),
      removed: text(T.exportAsset("Ascent", T.remove(c, a), n)),
      added: text(T.exportAsset("Ascent", added.chokes, added.nextId)),
    };
  }
"""


def test_a_choke_export_round_trips_through_choke_assets(tmp_path):
    from app.replays import choke_assets

    data = control_tagger.choke_data("Ascent")
    live = [c for c in data["chokes"] if not c["deleted"]]
    assert len(live) >= 2, "the Ascent asset should have chokes"
    a, b = live[0]["id"], live[1]["id"]
    used = {x for c in data["chokes"] for x in c["cells"]}
    new_cells = [x for x in range(128 * 64, 128 * 64 + 3) if x not in used]
    unicode_name = "Café ☕ \U0001F642 \x7f"          # accented, BMP symbol, astral emoji, DEL
    got = run_node(CHOKE_EXPORT, {"chokes": data["chokes"], "nextId": data["next_id"], "a": a, "b": b, "newCells": new_cells,
                                  "unicodeName": unicode_name})

    # the unchanged export is byte for byte what choke_assets.save writes for the same chokes
    saved = tmp_path / "saved"; saved.mkdir()
    choke_assets.save("Ascent", choke_assets.load("Ascent"), choke_assets.load_next_id("Ascent"), saved)
    assert got["unchanged"] == (saved / "Ascent.chokes.json").read_text(encoding="utf-8")
    # and so is one with non-ASCII names: escaped as json.dumps's ensure_ascii does, surrogate pairs included
    loaded = choke_assets.load("Ascent")
    for c in loaded:
        if c.id == a:
            c.name, c.source = unicode_name, "hand"
    choke_assets.save("Ascent", loaded, choke_assets.load_next_id("Ascent"), saved)
    expected = (saved / "Ascent.chokes.json").read_text(encoding="utf-8")
    assert "\\ud83d\\ude42" in expected and "\\u007f" in expected and expected.isascii()
    assert got["unicode"] == expected

    def asset(kind):
        folder = tmp_path / kind; folder.mkdir(exist_ok=True)
        (folder / "Ascent.chokes.json").write_text(got[kind], encoding="utf-8")
        return choke_assets.load("Ascent", folder), choke_assets.asset_hash("Ascent", folder), folder

    base, base_hash, _ = asset("unchanged")
    assert [vars(c) for c in base] == data["chokes"] and base_hash == choke_assets.asset_hash("Ascent")
    renamed, renamed_hash, _ = asset("renamed")
    assert next(c for c in renamed if c.id == a).name == "Heaven" and renamed_hash == base_hash, "a rename is free"
    for kind in ("moved", "removed", "added"):
        chokes, digest, folder = asset(kind)
        assert digest != base_hash, f"{kind} must stale the map's gap rows"
        assert choke_assets.load_next_id("Ascent", folder) > max(c.id for c in chokes)
    added, _, _ = asset("added")
    assert added[-1].cells == new_cells and added[-1].source == "hand" and added[-1].name == str(added[-1].id)


CHOKE_HAND_EDITS = """
  function run(p) {
    let c = p.chokes, n = p.nextId;
    c = T.rename(c, p.renamed, "Garden");
    c = T.rename(c, p.moved, "Tree");                       // renamed first, as the page allows
    const m = T.move(c, p.moved, p.dCol, 0, n, p.chokes); c = m.chokes; n = m.nextId;
    c = T.remove(c, p.removed);
    return T.exportAsset("Ascent", c, n);
  }
"""


def test_re_detection_keeps_the_hand_edits(tmp_path):
    from app.replays import choke_assets

    data = control_tagger.choke_data("Ascent")
    autos = [c for c in data["chokes"] if c["source"] == "auto" and not c["deleted"]]
    detected = [c["cells"] for c in autos]               # detection finds exactly what it found before
    renamed, removed = autos[0]["id"], autos[1]["id"]
    others = {x for c in data["chokes"] for x in c["cells"]}

    def shifted(c, d):
        return [x + d for x in c["cells"] if 0 <= x % 128 + d < 128]

    # a choke to move whose new cells touch no other choke (so no detected choke is dropped by overlap)
    moved, d_col = next((c["id"], d) for c in autos[2:] for d in (4, -4, 6, -6)
                        if len(shifted(c, d)) == len(c["cells"]) and not set(shifted(c, d)) & (others - set(c["cells"]))
                        and not set(shifted(c, d)) & set(c["cells"]))
    body = run_node(CHOKE_HAND_EDITS, {"chokes": data["chokes"], "nextId": data["next_id"], "renamed": renamed,
                                       "moved": moved, "removed": removed, "dCol": d_col})
    folder = tmp_path / "edited"; folder.mkdir()
    (folder / "Ascent.chokes.json").write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    edited, next_id = choke_assets.load("Ascent", folder), choke_assets.load_next_id("Ascent", folder)

    merged, merged_next = choke_assets.merge(edited, detected, next_id)
    by_id = {c.id: c for c in merged}
    old_moved = next(c for c in autos if c["id"] == moved)
    assert by_id[renamed].name == "Garden" and by_id[renamed].source == "hand"
    assert by_id[removed].deleted and by_id[removed].cells == next(c for c in autos if c["id"] == removed)["cells"]
    assert by_id[moved].cells == sorted(shifted(old_moved, d_col)) and by_id[moved].name == "Tree"
    assert any(c.deleted and c.cells == old_moved["cells"] for c in merged), "the moved choke's old place is a tombstone"
    # nothing regenerated: no new ids, every other detected choke keeps its id, and the result is the edit
    assert merged_next == next_id and [vars(c) for c in merged] == [vars(c) for c in edited]
    assert sum(1 for c in merged if not c.deleted) == sum(1 for c in data["chokes"] if not c["deleted"]) - 1


CHOKE_SESSION_1 = """
  function run(p) {
    return T.assetText(T.exportAsset("Ascent", T.rename(p.chokes, p.moved, "Tree"), p.nextId));
  }
"""

CHOKE_SESSION_2 = """
  function run(p) {
    const loaded = p.chokes;                                  // the list as this session loaded it
    const once = T.move(loaded, p.moved, p.dCol, 0, p.nextId, loaded);
    const twice = T.move(once.chokes, p.moved, p.dCol, 0, once.nextId, loaded);
    return {once: once, text: T.assetText(T.exportAsset("Ascent", twice.chokes, twice.nextId))};
  }
"""


def test_a_move_after_export_and_reload_still_leaves_its_tombstone(tmp_path):
    from app.replays import choke_assets

    data = control_tagger.choke_data("Ascent")
    autos = [c for c in data["chokes"] if c["source"] == "auto" and not c["deleted"]]
    detected = [c["cells"] for c in autos]
    others = {x for c in data["chokes"] for x in c["cells"]}

    def shifted(c, d):
        return [x + d for x in c["cells"] if 0 <= x % 128 + d < 128]

    def clear(c, d):                                     # shifting by d and by 2d stays whole and touches nothing
        return all(len(shifted(c, k)) == len(c["cells"]) and not set(shifted(c, k)) & others for k in (d, 2 * d))

    moved, d_col = next((c["id"], d) for c in autos for d in (4, -4, 6, -6, 8, -8) if clear(c, d))
    old = next(c for c in autos if c["id"] == moved)

    # session 1: rename it (now "hand") and export; session 2 loads that export fresh
    first = tmp_path / "session1"; first.mkdir()
    (first / "Ascent.chokes.json").write_text(run_node(CHOKE_SESSION_1, {"chokes": data["chokes"], "nextId": data["next_id"],
                                                                         "moved": moved}), encoding="utf-8")
    reloaded = control_tagger.choke_data("Ascent", first)
    assert next(c for c in reloaded["chokes"] if c["id"] == moved)["source"] == "hand"

    got = run_node(CHOKE_SESSION_2, {"chokes": reloaded["chokes"], "nextId": reloaded["next_id"], "moved": moved, "dCol": d_col})
    tomb = [c for c in got["once"]["chokes"] if c["deleted"] and c["cells"] == old["cells"]]
    assert len(tomb) == 1 and tomb[0]["id"] == reloaded["next_id"], "the first move tombstones the loaded cells"
    second = tmp_path / "session2"; second.mkdir()
    (second / "Ascent.chokes.json").write_text(got["text"], encoding="utf-8")
    edited, next_id = choke_assets.load("Ascent", second), choke_assets.load_next_id("Ascent", second)
    assert sum(1 for c in edited if c.deleted and c.cells == old["cells"]) == 1, "moving twice leaves one tombstone"
    assert next(c for c in edited if c.id == moved).cells == sorted(shifted(old, 2 * d_col))

    # re-detection finds the old passage again, and the tombstone stops it coming back
    merged, merged_next = choke_assets.merge(edited, detected, next_id)
    assert merged_next == next_id and [vars(c) for c in merged] == [vars(c) for c in edited]


def test_a_map_without_a_chokes_asset_gets_an_empty_list(tmp_path):
    assert control_tagger.choke_data("Ascent", tmp_path) == {"chokes": [], "next_id": 1}


def test_the_page_builds_with_the_choke_mode(tmp_path):
    out = tmp_path / "tagger.html"
    assert control_tagger.main(["--out", str(out), "--map", "Ascent"]) == 0
    page = out.read_text(encoding="utf-8")
    data = json.loads(page.split("var DATA = ", 1)[1].split(";\n", 1)[0])
    assert data["maps"]["Ascent"]["chokes"] == control_tagger.choke_data("Ascent")
    for needle in ('data-mode="chokes"', 'data-mode="choke_paint"', 'id="exportChokes"', 'id="chokeRename"',
                   'id="chokeDelete"', 'id="chokeMake"', "app/static/data/control/&lt;Map&gt;.chokes.json",
                   "Renaming changes no fingerprint", "scripts/compute_control.py", "T.exportAsset(", "T.assetText(body)", "chokeNextId()", 'a.download = state.map + ".chokes.json"'):
        assert needle in page, needle
    assert "Backspace" not in page, "only Delete deletes a choke"
    # the page's own script parses
    script = page.split("<script>")[2].split("</script>")[0]
    completed = subprocess.run([NODE, "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"], input=script,
                               capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
