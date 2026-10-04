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
    assert control_tagger.main(["--out", str(out), "--map", "Abyss", "--map", "Bind"]) == 0
    page = out.read_text(encoding="utf-8")
    assert "global.TaggerCore = api" in page and "/*DATA*/" not in page and "/*CORE*/" not in page
    data = json.loads(page.split("var DATA = ", 1)[1].split(";\n", 1)[0])
    assert sorted(data["maps"]) == ["Abyss", "Bind"] and data["tags"] == TAGS
    assert data["maps"]["Bind"]["lines"] == [] and len(data["maps"]["Abyss"]["lines"]) == 200


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
