"""The map-control drawing page (Stage 6; scripts/control_tagger.py and control_tagger_core.js): its JS
composes the sight and walk masks and runs the kill-line test exactly like app/control/geometry.py, and
exporting an unchanged page gives back the loaded tags.json. The JS checks need Node; they skip without it."""

import base64
import json
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
            labels: T.rleDecode(m.labels, Int32Array), opaque: T.rleDecode(m.opaque)};
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
    const map = load(p.map), e = T.editsFrom(p.entry), m = T.compose(map, e), r = T.riskOne(m.sight, map.lines);
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
    expected = [k for k, (ax, ay, bx, by) in enumerate(lines) if cg.line_blocked(m.sight, (ax, ay), (bx, by))]
    assert got["blocked"] == expected, f"{name} kill lines"
    return got


def test_abyss_tags_and_see_across_paint_compose_exactly_like_geometry():
    got = check("Abyss", TAGS["maps"]["Abyss"])
    assert got["blocked"] == [] and got["passes"] is True        # index.json: 0 of 200 blocked


def test_ascent_walls_only_blocks_the_same_lines_as_index_json():
    got = check("Ascent", TAGS["maps"]["Ascent"])
    index = json.loads((cg.ASSET_DIR / "index.json").read_text(encoding="utf-8"))["maps"]["Ascent"]["kill_lines"]
    assert len(got["blocked"]) == index["blocked"] and got["passes"] == index["passes"]


def test_every_paint_composes_exactly_like_geometry():
    entry = {**TAGS["maps"]["Ascent"], "cover_paint": paint_rect(440, 380, 520, 460),
             "cant_walk_paint": paint_rect(300, 300, 360, 420), "uncertain_paint": paint_rect(0, 0, 1024, 1024),
             "see_across_paint": paint_rect(430, 370, 600, 600)}
    got = check("Ascent", entry)
    assert len(got["blocked"]) > 2, "the cover paint should block some kill lines"


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
    assert control_tagger.main(["--out", str(out), "--map", "Abyss", "--map", "Bind"]) == 0
    page = out.read_text(encoding="utf-8")
    assert "global.TaggerCore = api" in page and "/*DATA*/" not in page and "/*CORE*/" not in page
    data = json.loads(page.split("var DATA = ", 1)[1].split(";\n", 1)[0])
    assert sorted(data["maps"]) == ["Abyss", "Bind"] and data["tags"] == TAGS
    assert data["maps"]["Bind"]["lines"] == [] and len(data["maps"]["Abyss"]["lines"]) == 200
