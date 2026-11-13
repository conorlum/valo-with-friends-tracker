"""The map layout tool's fit, diff and position count (scripts/map_layout_fit.py), on toy masks."""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import map_layout_fit as fit  # noqa: E402

SIZE = 256


def _ours() -> np.ndarray:
    """An asymmetric L-ish shape with a notch, so rotation is identifiable."""
    m = np.zeros((SIZE // 2, SIZE // 2), bool)
    m[30:100, 40:60] = True
    m[80:100, 40:110] = True
    m[30:45, 60:75] = True
    return np.kron(m, np.ones((2, 2), bool))


def _picture_of(ours: np.ndarray, rot_back: int, scale: float) -> np.ndarray:
    """A picture mask that fit() must map onto ours with rotation (4 - rot_back) % 4 and the given scale."""
    ys, xs = np.nonzero(ours)
    crop = ours[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    img = Image.fromarray(crop.astype(np.uint8) * 255).rotate(90 * rot_back, expand=True)
    img = img.resize((round(img.width / scale), round(img.height / scale)), Image.NEAREST)
    pic = np.zeros((img.height + 10, img.width + 10), bool)
    pic[5:5 + img.height, 5:5 + img.width] = np.array(img) > 0
    return pic


def test_fit_recovers_rotation_and_scale():
    ours = _ours()
    pic = _picture_of(ours, rot_back=3, scale=1.3)
    res = fit.fit_mask(pic, ours, size=SIZE, scales=np.arange(1.0, 1.8, 0.01))
    assert res["rot"] == 1
    assert abs(res["scale"] - 1.3) < 0.01
    assert res["iou"] > 0.95
    assert set(res) >= {"rot", "scale", "tx", "ty", "iou"}


def test_diff_lists_added_and_removed_regions():
    ours = _ours()
    new = ours.copy()
    new[5:25, 5:25] = True            # floor in the picture only
    new[170:190, 100:130] = False     # floor in ours only (inside the doubled L)
    regions = fit.diff_regions(ours, new, min_px=20)
    added = [r for r in regions if r["kind"] == "added"]
    removed = [r for r in regions if r["kind"] == "removed"]
    assert len(added) == 1 and len(removed) == 1
    assert added[0]["bbox"] == (5, 5, 24, 24)
    assert 380 <= added[0]["area"] <= 400   # the opening trims the corners
    assert removed[0]["bbox"] == (100, 170, 129, 189)


def test_positions_counted_inside_regions_only():
    ours = _ours()
    new = ours.copy()
    new[5:25, 5:25] = True
    regions = fit.diff_regions(ours, new, min_px=20)
    positions = [(10, 10), (20, 20), (15, 6), (50, 50), (100, 100), (30, 10)]
    counts = fit.count_positions(regions, positions)
    assert counts == [3]


# ---- build: a new layout's assets and registry entries, base untouched -------------------------------------
import json  # noqa: E402

import map_layout  # noqa: E402
import pytest  # noqa: E402
from app.control import geometry as cg  # noqa: E402
from app.replays import layouts  # noqa: E402
from app.services import replay_control as rc  # noqa: E402

KEY = "Toy_13_10"
BACKGROUND = (9, 15, 20)


def _w(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8")


def _toy_minimap() -> Image.Image:
    px = np.zeros((1024, 1024, 4), np.uint8)
    px[200:300, 160:480] = (255, 255, 255, 255)
    px[202:298, 162:478] = (118, 118, 118, 255)
    return Image.fromarray(px, "RGBA")


def _toy_tree(root: Path) -> Path:
    """A tree shaped like webapp/app/static with one base map, Toy."""
    control = root / "data" / "control"
    control.mkdir(parents=True)
    (root / "img" / "maps").mkdir(parents=True)
    _toy_minimap().save(root / "img" / "maps" / "Toy.png")
    cg.write_mask_png(control / "Toy.sight.png", np.zeros((1024, 1024), bool))
    cg.write_mask_png(control / "Toy.walk.png", np.ones((1024, 1024), bool))
    (control / "must_block.json").write_text('{"lines": []}\n', encoding="utf-8")
    (control / "Toy.chokes.json").write_text('{"version": 1, "map": "Toy", "next_id": 1, "chokes": []}\n',
                                              encoding="utf-8")
    _w(control / "index.json", {"version": 1, "maps": {"Toy": {"walk_sha": "base", "kill_lines": {"passes": True}}}})
    _w(control / "tags.json", {"version": 1, "maps": {"Toy": {"image_sha": "x", "tags": [], "cover_reviewed": True}}})
    _w(root / "data" / "maps.json", {"Toy": {"xMultiplier": 7e-05}})
    _w(root / "data" / "layouts.json", {"version": 1, "maps": {"Toy": [
        {"id": "Toy@base", "key": "Toy", "start": None, "drawn_from": "13.08", "regions": None}]}})
    _w(root / "data" / "callouts.json", {"Toy": [
        {"name": "A Inside", "super": "A", "u": 5300, "v": 2400},       # px (543, 245): in the changed region
        {"name": "A Outside", "super": "A", "u": 2000, "v": 2400}]})
    return root


def _inputs(tmp_path: Path):
    arr = np.full((1024, 1024, 3), BACKGROUND, np.uint8)
    arr[200:300, 160:600] = (60, 120, 90)           # the old room plus a new wing to the right
    Image.fromarray(arr).save(tmp_path / "aligned.png")
    picture = tmp_path / "floor.png"                 # align's floor.png: the fitted floor mask
    Image.fromarray(fit.picture_floor_mask(arr).astype(np.uint8) * 255, "L").save(picture)
    region = np.zeros((1024, 1024), bool)
    region[150:350, 480:620] = True
    regions = tmp_path / "regions.png"
    cg.write_mask_png(regions, region)
    kill_lines = tmp_path / "kill_lines.json"
    _w(kill_lines, {"maps": {KEY: [[200, 250, 440, 250], [210, 230, 430, 270]]}})
    return picture, regions, kill_lines


def _snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _run(root, tmp_path, *extra, kill_lines=True):
    picture, regions, lines = _inputs(tmp_path)
    argv = ["build", "Toy", "13.10", "--floor", str(picture), "--regions", str(regions), "--into", str(root)]
    if kill_lines:
        argv += ["--kill-lines", str(lines)]
    return map_layout.main(argv + list(extra))


@pytest.mark.parametrize("raw", [b'{\r\n  "a": 1\r\n}', b'{\r\n  "a": 1\r\n}\r\n', b'{\n  "a": 1\n}\n', b'{\n  "a": 1\n}'])
def test_rewriting_a_json_file_keeps_its_line_endings_and_final_newline(tmp_path, raw):
    path = tmp_path / "f.json"
    path.write_bytes(raw)
    map_layout._write_json(path, {"a": 1}, 2)
    assert path.read_bytes() == raw


def test_build_writes_the_keys_files_and_entries(tmp_path, capsys):
    root = _toy_tree(tmp_path / "static")
    assert _run(root, tmp_path) == 0
    control = root / "data" / "control"
    for suffix in ("sight", "walk", "bullet", "regions"):
        assert (control / f"{KEY}.{suffix}.png").is_file()
    assert (control / f"{KEY}.chokes.json").is_file()
    assert json.loads((control / f"must_block.{KEY}.json").read_text()) == {"lines": []}
    assert (root / "img" / "maps" / f"{KEY}.png").is_file()
    rows = json.loads((root / "data" / "layouts.json").read_text())["maps"]["Toy"]
    assert rows[1] == {"id": "Toy@13.10", "key": KEY, "start": "13.10", "drawn_from": "13.10",
                       "regions": f"{KEY}.regions.png"}
    row = json.loads((control / "index.json").read_text())["maps"][KEY]
    assert row["kill_lines"]["passes"] is True and row["walkable_cells"] > 0
    tags = json.loads((control / "tags.json").read_text())["maps"]
    assert tags[KEY]["cover_reviewed"] is True and tags[KEY]["image_sha"] != "x"
    callouts = json.loads((root / "data" / "callouts.json").read_text())
    assert callouts[KEY] == callouts["Toy"]
    out = capsys.readouterr().out
    assert "A Inside" in out and "A Outside" not in out


def test_build_minimap_is_vendored_style_and_keeps_pixels_outside_regions(tmp_path):
    root = _toy_tree(tmp_path / "static")
    _run(root, tmp_path)
    new = np.array(Image.open(root / "img" / "maps" / f"{KEY}.png").convert("RGBA"))
    old = np.array(_toy_minimap())
    outside = np.ones((1024, 1024), bool)
    outside[150:350, 480:620] = False
    assert (new[outside] == old[outside]).all()
    assert tuple(new[250, 550]) == (118, 118, 118, 255)       # new floor inside the regions
    assert tuple(new[200, 550]) == (255, 255, 255, 255)       # outer 2 px white
    assert new[250, 599, 3] == 255 and new[250, 610, 3] == 0  # floor ends at the picture's edge: wall transparent


def test_build_leaves_every_base_entry_and_file_untouched(tmp_path):
    root = _toy_tree(tmp_path / "static")
    before = _snapshot(root)
    _run(root, tmp_path)
    after = _snapshot(root)
    edited = ("data/layouts.json", "data/callouts.json", "data/control/index.json", "data/control/tags.json")
    for name, data in before.items():
        if name not in edited:
            assert after[name] == data, name
    was, now = json.loads(before["data/layouts.json"]), json.loads(after["data/layouts.json"])
    assert now["maps"]["Toy"][:1] == was["maps"]["Toy"]
    was, now = json.loads(before["data/callouts.json"]), json.loads(after["data/callouts.json"])
    assert now["Toy"] == was["Toy"]
    for name in edited[2:]:
        was, now = json.loads(before[name]), json.loads(after[name])
        assert now["maps"]["Toy"] == was["maps"]["Toy"]
        assert {k: v for k, v in now.items() if k != "maps"} == {k: v for k, v in was.items() if k != "maps"}
    assert f"data/control/{KEY}.sight.png" in after


def test_built_layout_selects_is_complete_and_has_a_ready_layer(tmp_path, monkeypatch):
    root = _toy_tree(tmp_path / "static")
    _run(root, tmp_path)
    control = root / "data" / "control"
    registry = layouts.load_registry(root / "data" / "layouts.json")
    index = json.loads((control / "index.json").read_text())
    for row in registry["maps"]["Toy"]:
        assert layouts.ASSET_KEY.fullmatch(row["key"]) and row["key"] in index["maps"]
        for suffix in ("sight", "walk"):
            assert (control / f"{row['key']}.{suffix}.png").is_file()
        assert row["regions"] is None or (control / row["regions"]).is_file()
    chosen = layouts.layout_for("Toy", "release-13.10", registry=registry, index=index)
    assert chosen.state == "ok" and chosen.layout.asset_key == KEY
    assert layouts.layout_for("Toy", "release-13.08", registry=registry, index=index).layout.asset_key == "Toy"
    monkeypatch.setattr(rc, "CONTROL_DIR", control)
    monkeypatch.setattr(rc, "MAPS_JSON", root / "data" / "maps.json")
    rc._assets.cache_clear()
    try:
        assert rc.map_layer(KEY) is not None
    finally:
        rc._assets.cache_clear()


def test_build_without_kill_lines_writes_the_rest_then_refuses(tmp_path, capsys):
    root = _toy_tree(tmp_path / "static")
    assert _run(root, tmp_path, kill_lines=False) == 2
    assert (root / "data" / "control" / f"{KEY}.sight.png").is_file()
    assert json.loads((root / "data" / "control" / "index.json").read_text())["maps"][KEY]["kill_lines"] is None
    err = capsys.readouterr().err
    assert "kill_lines.py" in err and "risk1.py" in err and KEY in err


def test_build_completes_a_pending_entry_registered_without_assets(tmp_path):
    """R2-7 (2026-11-12): the design lets the owner register a layout before its redraw (layout_pending); build
    then completes that entry rather than refusing the key."""
    root = _toy_tree(tmp_path / "static")
    registry = json.loads((root / "data" / "layouts.json").read_text(encoding="utf-8"))
    registry["maps"]["Toy"] = [{"id": "Toy@base", "key": "Toy", "start": None, "drawn_from": "13.08",
                                "regions": None},
                               {"id": "Toy@13.10", "key": KEY, "start": "13.10", "drawn_from": "13.10",
                                "regions": None}]
    _w(root / "data" / "layouts.json", registry)
    assert _run(root, tmp_path) == 0
    rows = json.loads((root / "data" / "layouts.json").read_text(encoding="utf-8"))["maps"]["Toy"]
    assert [row["key"] for row in rows] == ["Toy", KEY]
    assert rows[1]["regions"] == f"{KEY}.regions.png"
    with pytest.raises(SystemExit):
        _run(root, tmp_path)


def test_build_refuses_a_key_that_already_exists(tmp_path):
    root = _toy_tree(tmp_path / "static")
    assert _run(root, tmp_path) == 0
    before = _snapshot(root)
    with pytest.raises(SystemExit):
        _run(root, tmp_path)
    assert _snapshot(root) == before


# ---- overlay, fetch-minimap, notes: network and database reads behind fetch() and positions() -----------------
import hashlib  # noqa: E402
import io  # noqa: E402

PATCH_PAGE = """<html><body>
<h2>Agent Updates</h2><p>Nothing about maps here.</p>
<H2 class="x">MAP UPDATES</H2>
<h3>Ascent</h3><p>Ascent line one.</p>
<h3>Toy Town</h3>
<p>Moved the <b>east</b> door.</p>
<ul><li>Opened a new wing</li></ul>
<img src="https://example.test/toy-a.jpg" alt="a"><img data-src="https://example.test/toy-b.jpg">
<h3>Haven</h3><p>Haven line.</p><img src="https://example.test/haven.jpg">
<h2>Bug Fixes</h2><p>Unrelated.</p>
</body></html>"""


def _png_bytes(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (8, 8), color).save(buf, "PNG")
    return buf.getvalue()


def test_notes_pulls_the_map_subsection_of_a_synthetic_page(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(map_layout, "fetch", lambda url: asked.append(url) or PATCH_PAGE.encode())
    assert map_layout.main(["notes", "13.10", "--map", "Toy", "--out", str(tmp_path)]) == 0
    assert asked == ["https://playvalorant.com/en-us/news/game-updates/valorant-patch-notes-13-10/"]
    text = (tmp_path / "notes-13.10-Toy.txt").read_text(encoding="utf-8").splitlines()
    assert "Moved the east door." in text and "Opened a new wing" in text
    assert not any("Ascent" in line or "Haven" in line or "Unrelated" in line for line in text)
    urls = (tmp_path / "notes-13.10-Toy.images.txt").read_text(encoding="utf-8").split()
    assert urls == ["https://example.test/toy-a.jpg", "https://example.test/toy-b.jpg"]


def test_notes_without_a_map_subsection_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(map_layout, "fetch", lambda url: PATCH_PAGE.encode())
    assert map_layout.main(["notes", "13.10", "--map", "Lotus", "--out", str(tmp_path), "--url", "http://x/"]) == 1
    assert "Lotus" in capsys.readouterr().err
    assert not list(tmp_path.glob("notes-*"))


def _api(icon):
    return json.dumps({"status": 200, "data": [{"displayName": "Toy", "displayIcon": icon, "uuid": "u",
                                                 "mapUrl": "/Game/Maps/Toy/Toy", "xMultiplier": 1, "yMultiplier": 1,
                                                 "xScalarToAdd": 1, "yScalarToAdd": 1}]}).encode()


def _fetch_minimap(tmp_path, monkeypatch, icon_png, vendored_png, patched=None):
    root = tmp_path / "static"
    (root / "img" / "maps").mkdir(parents=True, exist_ok=True)
    (root / "img" / "maps" / "Toy.png").write_bytes(vendored_png)
    monkeypatch.setattr(map_layout, "STATIC", root)
    if patched is not None:
        monkeypatch.setattr(map_layout.vendor_map_assets, "PATCHED_MINIMAPS", {"Toy": patched})
    monkeypatch.setattr(map_layout, "fetch", lambda url: _api("http://i/toy.png") if "v1/maps" in url else icon_png)
    return root


def test_fetch_minimap_says_differs_and_saves_the_new_image(tmp_path, monkeypatch, capsys):
    new, old = _png_bytes((1, 2, 3, 255)), _png_bytes((9, 9, 9, 255))
    root = _fetch_minimap(tmp_path, monkeypatch, new, old)
    out = tmp_path / "scratch"
    assert map_layout.main(["fetch-minimap", "Toy", "--out", str(out)]) == 0
    assert capsys.readouterr().out.strip().startswith("differs")
    assert (out / "Toy.png").read_bytes() == new
    assert (root / "img" / "maps" / "Toy.png").read_bytes() == old


def test_fetch_minimap_same_and_patched_unchanged(tmp_path, monkeypatch, capsys):
    same = _png_bytes((1, 2, 3, 255))
    _fetch_minimap(tmp_path, monkeypatch, same, same)
    assert map_layout.main(["fetch-minimap", "Toy", "--out", str(tmp_path / "o")]) == 0
    assert capsys.readouterr().out.strip() == "same"
    other = _png_bytes((5, 5, 5, 255))
    _fetch_minimap(tmp_path, monkeypatch, same, other, patched=hashlib.sha256(same).hexdigest()[:12])
    assert map_layout.main(["fetch-minimap", "Toy", "--out", str(tmp_path / "o")]) == 0
    assert capsys.readouterr().out.strip() == "patched, upstream unchanged"
    assert not (tmp_path / "o" / "Toy.png").exists()


def test_overlay_has_one_row_per_region_and_exports_a_mask(tmp_path, monkeypatch):
    _inputs(tmp_path)
    picture = tmp_path / "aligned.png"
    ours = tmp_path / "ours.png"
    _toy_minimap().save(ours)
    fit_dir = tmp_path / "fit"
    fit_dir.mkdir()
    (fit_dir / "fit.json").write_text(json.dumps({"rot": 0, "scale": 1.0, "tx": 0, "ty": 0, "crop": None,
                                                  "picture": str(picture)}), encoding="utf-8")
    asked = []
    monkeypatch.setattr(map_layout, "positions",
                        lambda name, patch: asked.append((name, patch)) or ([(500, 250), (520, 260), (50, 50)], 3))
    out = tmp_path / "scratch"
    assert map_layout.main(["overlay", "--ours", str(ours), "--fit", str(fit_dir), "--map", "Toy",
                            "--patch", "13.10", "--out", str(out)]) == 0
    assert asked == [("Toy", (13, 10))]
    html = (out / "overlay.html").read_text(encoding="utf-8")
    assert "positions from 3 replays on 13.10 or later" in html
    assert html.count('class="region"') == 1
    assert 'data-kind="added"' in html and 'data-positions="2"' in html
    for control in ("keep", "reject", "cant-show"):
        assert f'value="{control}"' in html
    assert "toDataURL" in html and "data:image/png;base64," in html
    assert "http://" not in html and "https://" not in html


def test_the_geometry_build_takes_a_layout_key_but_not_a_stray_minimap(tmp_path):
    import build_control_geometry as bcg

    for stem in ("Toy", "Toy_13_10", "Toy_old", "Other", "Toy_13_10_copy"):
        Image.new("RGBA", (4, 4)).save(tmp_path / f"{stem}.png")
    assert bcg.buildable_names(tmp_path, {"Toy": {"xMultiplier": 7e-05}}) == ["Toy", "Toy_13_10"]


# ---- review fixes (2026-11-12) ----------------------------------------------------------------------------------
def test_a_transparent_picture_reads_its_transparent_pixels_as_background(tmp_path):
    """S6: a vendored minimap's walls are transparent with black RGB underneath: wall, not floor."""
    px = np.zeros((64, 64, 4), np.uint8)                       # transparent black everywhere
    px[10:30, 10:30] = (118, 118, 118, 255)                    # one floor room
    path = tmp_path / "upstream.png"
    Image.fromarray(px, "RGBA").save(path)
    floor = fit.picture_floor_mask(np.array(map_layout._rgb(path)))
    assert floor[20, 20] and not floor[50, 50]
    assert floor.sum() < 30 * 30


def test_align_writes_the_exact_floor_mask_build_reads(tmp_path):
    """S6: build reads align's floor.png, not a re-threshold of aligned.png's padded, resampled pixels."""
    ours = tmp_path / "ours.png"
    _toy_minimap().save(ours)
    arr = np.full((300, 400, 3), BACKGROUND, np.uint8)
    arr[50:150, 40:360] = (60, 120, 90)
    picture = tmp_path / "shot.png"
    Image.fromarray(arr).save(picture)
    out = tmp_path / "fit"
    assert map_layout.main(["align", "--picture", str(picture), "--ours", str(ours), "--out", str(out)]) == 0
    res = json.loads((out / "fit.json").read_text(encoding="utf-8"))
    floor = np.array(Image.open(out / "floor.png")) > 0
    pic = fit.picture_floor_mask(arr)
    assert (floor == fit.warp_mask(pic, res["rot"], res["scale"], res["tx"], res["ty"], 1024)).all()
    # aligned.png pads with black, which a re-threshold reads as floor; floor.png has none there.
    padded = fit.picture_floor_mask(np.array(Image.open(out / "aligned.png").convert("RGB")))
    assert padded.sum() > floor.sum()


def test_extra_regions_join_the_regions_file_but_not_the_minimap(tmp_path):
    """S8: a raised wall changes heights, not the minimap: its mask goes in the regions file only."""
    root = _toy_tree(tmp_path / "static")
    extra = np.zeros((1024, 1024), bool)
    extra[220:240, 200:220] = True                              # inside the old room, outside --regions
    path = tmp_path / "extra.png"
    cg.write_mask_png(path, extra)
    assert _run(root, tmp_path, "--extra-regions", str(path)) == 0
    regions = np.array(Image.open(root / "data" / "control" / f"{KEY}.regions.png")) > 0
    assert regions[230, 210] and regions[250, 550]
    old = np.array(_toy_minimap())
    new = np.array(Image.open(root / "img" / "maps" / f"{KEY}.png").convert("RGBA"))
    assert (new[220:240, 200:220] == old[220:240, 200:220]).all()


def test_fetch_minimap_after_a_redraw_compares_with_the_newest_layout(tmp_path, monkeypatch, capsys):
    """S15: upstream serving the first redraw's picture is not news on the second redraw."""
    base = _png_bytes((9, 9, 9, 255))
    root = _fetch_minimap(tmp_path, monkeypatch, b"", base)
    first = np.zeros((1024, 1024, 4), np.uint8)
    first[100:400, 100:400] = (118, 118, 118, 255)
    Image.fromarray(first, "RGBA").save(root / "img" / "maps" / f"{KEY}.png")      # build's re-drawn bytes
    upstream = first.copy()
    upstream[100:400, 100:400] = (130, 130, 130, 255)                                # Riot's own colours
    buf = io.BytesIO()
    Image.fromarray(upstream, "RGBA").save(buf, "PNG")
    _w(root / "data" / "layouts.json", {"version": 1, "maps": {"Toy": [
        {"id": "Toy@base", "key": "Toy", "start": None, "drawn_from": "13.08", "regions": None},
        {"id": "Toy@13.10", "key": KEY, "start": "13.10", "drawn_from": "13.10", "regions": None}]}})
    monkeypatch.setattr(map_layout, "fetch",
                        lambda url: _api("http://i/toy.png") if "v1/maps" in url else buf.getvalue())
    assert map_layout.main(["fetch-minimap", "Toy", "--out", str(tmp_path / "o")]) == 0
    assert capsys.readouterr().out.strip() == f"same floor as {KEY}"
    assert not (tmp_path / "o").exists()
    # R2-6: a 20 x 20 closure keeps the overall IoU over 0.99, but it is a change to look at
    upstream[200:220, 200:220] = (0, 0, 0, 0)
    buf.seek(0), buf.truncate()
    Image.fromarray(upstream, "RGBA").save(buf, "PNG")
    assert map_layout.main(["fetch-minimap", "Toy", "--out", str(tmp_path / "o")]) == 0
    assert capsys.readouterr().out.startswith("differs")
    assert (tmp_path / "o" / "Toy.png").is_file()


def test_notes_url_keeps_the_patchs_zero(tmp_path, monkeypatch):
    """F10: Riot's page for 13.08 is valorant-patch-notes-13-08."""
    asked = []
    monkeypatch.setattr(map_layout, "fetch", lambda url: asked.append(url) or PATCH_PAGE.encode())
    map_layout.main(["notes", "13.08", "--map", "Toy", "--out", str(tmp_path)])
    assert asked == ["https://playvalorant.com/en-us/news/game-updates/valorant-patch-notes-13-08/"]


def test_kill_lines_writes_risk1s_qualifying_endpoints_under_the_key(tmp_path):
    """S14: kill_lines.py's output becomes the fixture's [kx, ky, vx, vy] lines under the layout key."""
    def kill(**extra):
        return {"rpc": "MulticastNotifyDamage_Point", "wall_pen": False, "killer": 1, "victim": 2, "t_ms": 1,
                "killer_pos": {"u": 5000, "v": 2500, "z": 0, "dt_ms": 0},
                "victim_pos": {"u": 6000, "v": 2500, "z": 0, "dt_ms": 0}, **extra}

    kills = tmp_path / "kills"
    _w(kills / "a.json", {"uuid": "a", "map": "Toy", "positions": {},
                          "kills": [kill(), kill(wall_pen=True), kill(rpc="other")]})
    _w(kills / "b.json", {"uuid": "b", "map": "Other", "positions": {}, "kills": [kill()]})
    fixture = tmp_path / "kill_lines.json"
    fixture.write_bytes(b'{"note": "n", "maps": {"Toy": [[1, 2, 3, 4]]}}\n')
    assert map_layout.main(["kill-lines", "--kills", str(kills), "--map", "Toy", "--key", KEY,
                            "--into", str(fixture)]) == 0
    assert json.loads(fixture.read_text()) == {"note": "n", "maps": {"Toy": [[1, 2, 3, 4]],
                                                                    KEY: [[512, 256, 614, 256]]}}
    assert fixture.read_bytes().startswith(b'{"note": "n", "maps": {"Toy": [[1, 2, 3, 4]], ')
    with pytest.raises(SystemExit):
        map_layout.main(["kill-lines", "--kills", str(kills), "--map", "Toy", "--key", "Other_13_10",
                         "--into", str(fixture)])
    before = fixture.read_bytes()
    with pytest.raises(SystemExit):     # R2-13: the base key's lines are the base map's, never replaced here
        map_layout.main(["kill-lines", "--kills", str(kills), "--map", "Toy", "--key", "Toy",
                         "--into", str(fixture)])
    assert fixture.read_bytes() == before


from test_control_store import db, factory  # noqa: E402,F401


def test_positions_read_only_the_target_patchs_replays_on_quarter_seconds(db, factory, monkeypatch):
    """S7 + F3: the overlay's positions come from replays on --patch or later, 4 a second whatever the start."""
    import app.db
    from app.models.replay import Replay, ReplayRound
    from app.replays import format as fmt

    monkeypatch.setattr(app.db, "SessionLocal", factory)

    def add(patch, u, serial):
        row = Replay(match_uuid=f"00000000-0000-4000-8000-{serial:012d}", map_name="Toy", round_count=1,
                     format_version=1, recipe="toy", game_branch=f"++Ares-Core+release-{patch}",
                     source_sha256="0" * 64, source="local", link_status="unlinked", link_inputs={})
        db.add(row)
        db.commit()
        blob = {"v": 1, "round": 1, "map": "Toy", "hz": 16, "t_start": 0.0, "t_decided": 1.0, "t_end": 1.0,
                "players": [{"slot": 0, "agent": "Jett", "side": "A"}],
                "tracks": {"0": [fmt.encode_segment(1 / 16, [u] * 16, [5000] * 16, [0] * 16)]},
                "alive": {"0": [[0.0, None, None]]}, "crouch": {}, "kills": [], "plant": None, "defuse": None,
                "util": []}
        db.add(ReplayRound(replay_id=row.id, round_number=1, data=fmt.encode_blob(blob)))
        db.commit()

    add("13.08", 1000, 1)
    add("13.10", 5000, 2)
    add("13.11", 6000, 3)
    spots, replays = map_layout.positions("Toy", (13, 10))
    assert replays == 2
    assert sorted({x for x, _ in spots}) == [512, 614]
    assert len(spots) == 2 * 4, "a 16-sample segment from 1/16 s keeps its 4 quarter seconds"
