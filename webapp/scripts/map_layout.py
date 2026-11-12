"""Map layout tooling. Subcommands (a table, later items add more):

    align  --picture <png> --crop x0,y0,x1,y1 --ours <png> --out <dir>
           fit the picture's floor onto our minimap; writes aligned.png, diff.png, fit.json
    diff   --ours <png> --aligned-fit <dir>   (reads <dir>/fit.json and the picture it names) -> prints regions
    build  <Map> <patch, 13.10 form> --picture <aligned png> --regions <mask png> --into <asset dir>
           [--kill-lines <json>]: the layout's minimap, masks, chokes, tags, callouts, layouts.json and
           index.json entries and an empty must_block.<key>.json, all under --into (a tree shaped like
           webapp/app/static; default the real one). The base layout and maps.json are never touched.

    overlay --ours <png> --fit <align out dir> --map <Map> --out <scratch dir>
           overlay.html (self-contained): old, new, replay positions, each region with keep / reject /
           "minimap can't show", and a button that exports the kept regions as a 1024x1024 mask png
           (the input of `build --regions`)
    fetch-minimap <Map> --out <dir>
           compares valorant-api's minimap with the vendored one: same | differs (image saved to --out) |
           patched, upstream unchanged. Never writes under app/.
    notes <patch, 13.10 form> --map <Map> --out <dir> [--url <page>]
           the patch page's Map Updates section, the map's subsection: notes-<patch>-<Map>.txt and
           notes-<patch>-<Map>.images.txt in --out; exit 1 when the map has no subsection

Everything but overlay/fetch-minimap/notes is offline. Those three read the network through `fetch(url)` and the
friends DB through `positions(map)` (run under `with_friends_db.py --expect-database valowithfriendsdb
--read-only`); nothing else here touches either.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html as html_lib
import io
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import map_layout_fit as fit  # noqa: E402
import vendor_map_assets  # noqa: E402

SIZE = 1024


def _crop(text: str) -> tuple[int, int, int, int]:
    parts = tuple(int(p) for p in text.split(","))
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--crop needs x0,y0,x1,y1")
    return parts  # type: ignore[return-value]


def _load(picture: Path, crop, ours_path: Path):
    shot = Image.open(picture).convert("RGB")
    if crop:
        shot = shot.crop(crop)
    pic = fit.picture_floor_mask(np.array(shot))
    ours = fit.ours_floor_mask(np.array(Image.open(ours_path).convert("RGBA")))
    return shot, pic, ours


def cmd_align(args) -> int:
    shot, pic, ours = _load(args.picture, args.crop, args.ours)
    res = fit.fit_mask(pic, ours, size=SIZE)
    res["crop"] = list(args.crop) if args.crop else None
    res["picture"] = str(args.picture)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "fit.json").write_text(json.dumps(res), encoding="utf-8")
    fit.warp(shot, res["rot"], res["scale"], res["tx"], res["ty"], SIZE, Image.BICUBIC).save(out / "aligned.png")
    new = fit.warp_mask(pic, res["rot"], res["scale"], res["tx"], res["ty"], SIZE)
    Image.fromarray(fit.diff_image(ours, new)).save(out / "diff.png")
    print(f"best IoU {res['iou']:.4f} rot {res['rot'] * 90} CCW scale {res['scale']:.4f} "
          f"at ({res['tx']:.1f}, {res['ty']:.1f})")
    print("removed px", int((ours & ~new).sum()), "added px", int((~ours & new).sum()))
    return 0


def cmd_diff(args) -> int:
    res = json.loads((args.fit / "fit.json").read_text(encoding="utf-8"))
    crop = tuple(res["crop"]) if res.get("crop") else None
    _, pic, ours = _load(Path(res["picture"]), crop, args.ours)
    new = fit.warp_mask(pic, res["rot"], res["scale"], res["tx"], res["ty"], SIZE)
    for r in fit.diff_regions(ours, new, min_px=args.min_px):
        x0, y0, x1, y1 = r["bbox"]
        print(f"  {r['kind']:7s} px {r['area']:5d}  box x {x0}-{x1} y {y0}-{y1}")
    return 0


STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
KILL_LINES_COMMAND = ("scripts/control_feasibility/kill_lines.py (extracts the qualifying kills of the archived "
                      "replays), then scripts/control_feasibility/risk1.py, then add the result under "
                      "\"maps\" in tests/fixtures/control/kill_lines.json")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value, indent: int, *, sort: bool = False) -> None:
    """Writes `value` keeping the file's own newline style (a Windows checkout has CRLF) and its final newline,
    or the lack of one (tags.json has none), so the file's untouched text keeps its bytes."""
    raw = path.read_bytes() if path.is_file() else b"\n"
    crlf = b"\r\n" in raw
    text = json.dumps(value, indent=indent, ensure_ascii=False, sort_keys=sort) + ("\n" if raw.endswith(b"\n") else "")
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def _patch(text: str) -> tuple[int, int]:
    try:
        major, minor = text.split(".")
        return int(major), int(minor)
    except ValueError:
        raise SystemExit(f"patch must look like 13.10, not {text!r}")


def cmd_build(args) -> int:
    import hashlib

    import build_control_geometry as bcg
    from app.control import chokes as chokes_mod
    from app.control import geometry as cg
    from app.replays import choke_assets, layouts

    into, name = args.into, args.map
    patch = _patch(args.patch)
    key = layouts.key_for(name, patch)
    if not layouts.ASSET_KEY.fullmatch(key):
        raise SystemExit(f"{key!r} is not an asset key")
    control, img_dir = into / "data" / "control", into / "img" / "maps"
    registry = _read_json(into / "data" / "layouts.json")
    existing = layouts.layouts_of(name, registry)
    if any(row.asset_key == key for row in existing) or (control / f"{key}.sight.png").exists():
        raise SystemExit(f"{key} already exists under {into}; nothing written")
    previous = existing[-1]
    if previous.start is not None and previous.start >= patch:
        raise SystemExit(f"{key} must start after {previous.display_id}")
    scale = _read_json(into / "data" / "maps.json")[name]["xMultiplier"]

    # The minimap: the aligned picture's floor inside the regions, the previous layout's pixels outside.
    regions = np.array(Image.open(args.regions).convert("L")) > 0
    shot = np.array(Image.open(args.picture).convert("RGB"))
    old_png = img_dir / f"{previous.asset_key}.png"
    old = np.array(Image.open(old_png).convert("RGBA"))
    new_rgba = fit.layout_minimap(old, fit.picture_floor_mask(shot), regions)
    png = img_dir / f"{key}.png"
    Image.fromarray(new_rgba, "RGBA").save(png)

    # Tags: the previous layout's entry, tied to the new minimap.
    tags = _read_json(control / "tags.json")
    entry = json.loads(json.dumps((tags.get("maps") or {}).get(previous.asset_key) or {}))
    entry.pop("image_sha", None)
    lines = None
    kill_path = args.kill_lines or bcg.KILL_LINES
    if kill_path.is_file():
        lines = (_read_json(kill_path).get("maps") or {}).get(key) or None
    row = bcg.build_map(key, entry, lines, control, png=png, scale=scale)
    entry["image_sha"] = row["image_sha"]
    tags.setdefault("maps", {})[key] = entry
    _write_json(control / "tags.json", tags, 2)

    # Chokes, detected on the new masks.
    rgba = np.array(Image.open(png).convert("RGBA"))
    m = cg.masks(rgba, entry)
    geo = cg.geometry_from_masks(key, m.sight, m.walk, scale)
    merged, next_id = choke_assets.merge([], chokes_mod.detect(geo), 1)
    choke_assets.save(key, merged, next_id, control)

    # The regions mask beside the control assets, the empty check set, index.json, layouts.json.
    regions_name = f"{key}.regions.png"
    cg.write_mask_png(control / regions_name, regions)
    (control / f"must_block.{key}.json").write_text('{"lines": []}\n', encoding="utf-8")
    index = _read_json(control / "index.json")
    index["maps"][key] = row
    index["maps"] = dict(sorted(index["maps"].items()))
    _write_json(control / "index.json", index, 1)
    rows = list(registry["maps"].get(name) or [
        {"id": f"{name}@base", "key": name, "start": None, "drawn_from": "13.08", "regions": None}])
    rows.append({"id": f"{name}@{args.patch}", "key": key, "start": args.patch,
                 "drawn_from": args.patch, "regions": regions_name})
    registry["maps"][name] = rows
    registry["maps"] = dict(sorted(registry["maps"].items()))
    _write_json(into / "data" / "layouts.json", registry, 1)

    # Callouts: copied from the previous layout; the ones in a changed region are listed for review.
    callouts_path = into / "data" / "callouts.json"
    callouts = _read_json(callouts_path)
    copied = callouts.get(previous.asset_key) or callouts.get(name) or []
    callouts[key] = json.loads(json.dumps(copied))
    _write_json(callouts_path, dict(sorted(callouts.items())), 1)

    print(f"{key}: {row['walkable_cells']} walkable cells, {len(merged)} chokes, files under {into}")
    review = fit.callouts_in(copied, regions)
    print(f"{len(review)} of {len(copied)} callouts lie in a changed region; review them:")
    for c in review:
        print(f"  {c['name']}  (u {c['u']}, v {c['v']})")
    if lines is None:
        print(f"{key} has no kill lines, so its index entry has no kill-line result and the map reads as "
              f"`no_map` until it has them. Produce them with {KILL_LINES_COMMAND}, "
              f"then re-run the geometry build for {key}.", file=sys.stderr)
        return 2
    kl = row["kill_lines"]
    print(f"kill lines {kl['blocked']}/{kl['qualifying']} blocked, passes {kl['passes']}")
    return 0


def fetch(url: str) -> bytes:
    """Every network read of this tool goes through here (tests replace it)."""
    return vendor_map_assets.fetch(url)


POSITION_ROUND_CAP = 200


def positions(map_name: str) -> list[tuple[int, int]]:
    """Alive 4 Hz replay positions on `map_name` as minimap pixels (0..1023), from the stored round blobs of at
    most POSITION_ROUND_CAP rounds. Reads the database through the app's session, so run it under
    `scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only`. Tests replace this."""
    from app.db import SessionLocal
    from app.models.replay import Replay, ReplayRound
    from app.replays import format as fmt

    out: list[tuple[int, int]] = []
    db = SessionLocal()
    try:
        rows = (db.query(ReplayRound.data).join(Replay, Replay.id == ReplayRound.replay_id)
                .filter(Replay.map_name == map_name, ReplayRound.data.isnot(None))
                .order_by(ReplayRound.replay_id.desc(), ReplayRound.round_number)
                .limit(POSITION_ROUND_CAP).all())
        for (data,) in rows:
            try:
                blob = fmt.decode_blob(data)
            except Exception:  # noqa: BLE001 - one unreadable round does not stop the tool
                continue
            for slot, segments in blob.get("tracks", {}).items():
                lives = [(t0, float("inf") if t1 is None else t1) for t0, t1, _ in blob.get("alive", {}).get(slot, [])]
                for segment in segments:
                    for t, u, v, _ in fmt.decode_segment(segment, blob["hz"]):
                        if abs(round(t * 4) - t * 4) > 1e-6 or not any(t0 <= t < t1 for t0, t1 in lives):
                            continue
                        out.append((min(SIZE - 1, int(u / fmt.UV_SCALE * SIZE)),
                                    min(SIZE - 1, int(v / fmt.UV_SCALE * SIZE))))
    finally:
        db.close()
    return out


def _data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _floor_image(mask: np.ndarray) -> Image.Image:
    out = np.zeros(mask.shape + (4,), np.uint8)
    out[mask] = (200, 200, 200, 255)
    return Image.fromarray(out, "RGBA")


OVERLAY_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Layout overlay: __MAP__</title>
<style>
body{font-family:sans-serif;background:#111;color:#ddd;margin:12px}
#stage{position:relative;width:1024px;height:1024px;background:#090f14}
#stage canvas,#stage img{position:absolute;left:0;top:0}
table{border-collapse:collapse;margin-top:12px}td,th{border:1px solid #444;padding:3px 8px;text-align:left}
</style></head><body>
<h1>__MAP__: old, new, positions</h1>
<p><label><input type="checkbox" id="show-old" checked> old (ours)</label>
<label><input type="checkbox" id="show-new" checked> new (picture)</label>
<label><input type="checkbox" id="show-pos" checked> positions (__NPOS__)</label>
<label><input type="checkbox" id="show-reg" checked> regions</label></p>
<div id="stage">
<img id="old" width="1024" height="1024" style="opacity:.5" src="__OLD__">
<img id="new" width="1024" height="1024" style="opacity:.5" src="__NEW__">
<canvas id="regions" width="1024" height="1024"></canvas>
<canvas id="pos" width="1024" height="1024"></canvas>
</div>
<table><tr><th>#</th><th>kind</th><th>area</th><th>bbox</th><th>positions inside</th><th>decision</th></tr>
__ROWS__
</table>
<p><button id="export">Export kept regions as mask png</button>
<button id="marks">Show the "minimap can't show" list</button></p>
<pre id="marks-out"></pre>
<script>
var POSITIONS = __POSITIONS__;
var COLOURS = {added: [60, 200, 90], removed: [230, 60, 60]};
var rows = Array.prototype.slice.call(document.querySelectorAll("tr.region"));
function decision(row) {
  var r = row.querySelector("input[type=radio]:checked");
  return r ? r.value : "keep";
}
function load(row) {
  return new Promise(function (resolve) {
    var img = new Image();
    img.onload = function () { resolve(img); };
    img.src = row.getAttribute("data-mask");
  });
}
function draw() {
  var ctx = document.getElementById("regions").getContext("2d");
  ctx.clearRect(0, 0, 1024, 1024);
  var shown = document.getElementById("show-reg").checked;
  Promise.all(rows.map(load)).then(function (imgs) {
    imgs.forEach(function (img, i) {
      var d = decision(rows[i]);
      if (!shown || d === "reject") { return; }
      var tmp = document.createElement("canvas");
      tmp.width = tmp.height = 1024;
      var t = tmp.getContext("2d");
      t.drawImage(img, 0, 0);
      var data = t.getImageData(0, 0, 1024, 1024), c = COLOURS[rows[i].getAttribute("data-kind")];
      for (var p = 0; p < data.data.length; p += 4) {
        var on = data.data[p] > 0;
        data.data[p] = c[0]; data.data[p + 1] = c[1]; data.data[p + 2] = c[2];
        data.data[p + 3] = on ? (d === "cant-show" ? 90 : 170) : 0;
      }
      t.putImageData(data, 0, 0);
      ctx.drawImage(tmp, 0, 0);
    });
  });
}
function drawPositions() {
  var ctx = document.getElementById("pos").getContext("2d");
  ctx.clearRect(0, 0, 1024, 1024);
  if (!document.getElementById("show-pos").checked) { return; }
  ctx.fillStyle = "rgba(255,230,0,.6)";
  POSITIONS.forEach(function (p) { ctx.fillRect(p[0] - 1, p[1] - 1, 3, 3); });
}
function layers() {
  document.getElementById("old").style.display = document.getElementById("show-old").checked ? "" : "none";
  document.getElementById("new").style.display = document.getElementById("show-new").checked ? "" : "none";
  drawPositions(); draw();
}
["show-old", "show-new", "show-pos", "show-reg"].forEach(function (id) {
  document.getElementById(id).addEventListener("change", layers);
});
rows.forEach(function (row) { row.addEventListener("change", draw); });
document.getElementById("export").addEventListener("click", function () {
  var canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1024;
  var ctx = canvas.getContext("2d");
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, 1024, 1024);
  Promise.all(rows.map(load)).then(function (imgs) {
    ctx.globalCompositeOperation = "lighten";
    imgs.forEach(function (img, i) { if (decision(rows[i]) === "keep") { ctx.drawImage(img, 0, 0); } });
    var a = document.createElement("a");
    a.href = canvas.toDataURL("image/png");
    a.download = "regions-__MAP__.png";
    a.click();
  });
});
document.getElementById("marks").addEventListener("click", function () {
  document.getElementById("marks-out").textContent = rows.filter(function (r) { return decision(r) === "cant-show"; })
    .map(function (r) { return "region " + r.getAttribute("data-index") + " " + r.getAttribute("data-kind") +
      " bbox " + r.getAttribute("data-bbox"); }).join("\\n");
});
layers();
</script></body></html>
"""


def cmd_overlay(args) -> int:
    res = json.loads((args.fit / "fit.json").read_text(encoding="utf-8"))
    crop = tuple(res["crop"]) if res.get("crop") else None
    _, pic, ours = _load(Path(res["picture"]), crop, args.ours)
    new = fit.warp_mask(pic, res["rot"], res["scale"], res["tx"], res["ty"], SIZE)
    regions = fit.diff_regions(ours, new, min_px=args.min_px)
    spots = [(int(x), int(y)) for x, y in positions(args.map)]
    counts = fit.count_positions(regions, spots)
    rows = []
    for i, (r, n) in enumerate(zip(regions, counts), 1):
        x0, y0, x1, y1 = r["bbox"]
        mask = _data_uri(Image.fromarray(r["mask"].astype(np.uint8) * 255, "L"))
        radios = "".join(
            f'<label><input type="radio" name="d{i}" value="{v}"{" checked" if v == "keep" else ""}> {label}</label> '
            for v, label in (("keep", "keep"), ("reject", "reject"), ("cant-show", "minimap can't show")))
        rows.append(
            f'<tr class="region" data-index="{i}" data-kind="{r["kind"]}" data-area="{r["area"]}" '
            f'data-bbox="{x0},{y0},{x1},{y1}" data-positions="{n}" data-mask="{mask}">'
            f'<td>{i}</td><td>{r["kind"]}</td><td>{r["area"]}</td><td>x {x0}-{x1} y {y0}-{y1}</td>'
            f'<td>{n}</td><td>{radios}</td></tr>')
    page = (OVERLAY_PAGE.replace("__MAP__", html_lib.escape(args.map))
            .replace("__NPOS__", str(len(spots)))
            .replace("__OLD__", _data_uri(_floor_image(ours))).replace("__NEW__", _data_uri(_floor_image(new)))
            .replace("__ROWS__", "\n".join(rows)).replace("__POSITIONS__", json.dumps([list(s) for s in spots])))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "overlay.html").write_text(page, encoding="utf-8")
    print(f"{len(regions)} regions, {len(spots)} positions -> {args.out / 'overlay.html'}")
    return 0


def _sha12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def cmd_fetch_minimap(args) -> int:
    body = json.loads(fetch(f"{vendor_map_assets.API}/maps"))
    if body.get("status") != 200:
        raise SystemExit(f"valorant-api returned status {body.get('status')}")
    found = [m for m in vendor_map_assets.playable_maps(body["data"]) if m["displayName"] == args.map]
    if not found:
        raise SystemExit(f"valorant-api has no playable map called {args.map!r}")
    upstream = fetch(found[0]["displayIcon"])
    vendored = STATIC / "img" / "maps" / f"{args.map}.png"
    patched_from = vendor_map_assets.PATCHED_MINIMAPS.get(args.map)
    new_sha = _sha12(upstream)
    if patched_from is not None and new_sha == patched_from:
        print("patched, upstream unchanged")
        return 0
    if vendored.is_file() and _sha12(vendored.read_bytes()) == new_sha:
        print("same")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.map}.png").write_bytes(upstream)
    note = " (a hand patch stands; upstream changed under it)" if patched_from is not None else ""
    print(f"differs: upstream {new_sha}, vendored "
          f"{_sha12(vendored.read_bytes()) if vendored.is_file() else 'missing'}{note}; saved {args.out / (args.map + '.png')}")
    return 0


class _NotesParser(HTMLParser):
    """Flat events from a page: ("h", level, text), ("line", text), ("img", url)."""

    BLOCKS = {"p", "li", "br", "div", "ul", "ol", "tr", "section", "article", "blockquote", "figure", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events: list[tuple] = []
        self._buf: list[str] = []
        self._heading: int | None = None

    def _flush(self):
        text = " ".join("".join(self._buf).split())
        self._buf = []
        if text:
            self.events.append(("h", self._heading, text) if self._heading else ("line", text))

    def handle_starttag(self, tag, attrs):
        if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            self._flush()
            self._heading = int(tag[1])
        elif tag in self.BLOCKS:
            self._flush()
        elif tag == "img":
            d = dict(attrs)
            url = d.get("src") or d.get("data-src")
            if url:
                self.events.append(("img", url))

    def handle_endtag(self, tag):
        if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            self._flush()
            self._heading = None
        elif tag in self.BLOCKS:
            self._flush()

    def handle_data(self, data):
        self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def map_notes(page: str, map_name: str) -> tuple[list[str], list[str]] | None:
    """(text lines, image urls) of the map's subsection under the page's "Map Updates" heading, or None."""
    parser = _NotesParser()
    parser.feed(page)
    parser.close()
    updates = None      # level of the open Map Updates heading
    mine = None         # level of the open map heading
    lines: list[str] = []
    urls: list[str] = []
    found = False
    for ev in parser.events:
        if ev[0] == "h":
            level, text = ev[1], ev[2]
            if mine is not None and level <= mine:
                mine = None
            if updates is not None and level <= updates:
                updates = None
            if updates is None and "map updates" in text.lower():
                updates = level
            elif updates is not None and mine is None and map_name.lower() in text.lower():
                mine, found = level, True
        elif mine is not None:
            (lines if ev[0] == "line" else urls).append(ev[1])
    return (lines, urls) if found else None


def cmd_notes(args) -> int:
    major, minor = _patch(args.patch)
    url = args.url or f"https://playvalorant.com/en-us/news/game-updates/valorant-patch-notes-{major}-{minor}/"
    got = map_notes(fetch(url).decode("utf-8", "replace"), args.map)
    if got is None:
        print(f"no {args.map} subsection under Map Updates in {url}", file=sys.stderr)
        return 1
    lines, urls = got
    args.out.mkdir(parents=True, exist_ok=True)
    stem = f"notes-{args.patch}-{args.map}"
    (args.out / f"{stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (args.out / f"{stem}.images.txt").write_text("".join(u + "\n" for u in urls), encoding="utf-8")
    print(f"{len(lines)} lines, {len(urls)} pictures -> {args.out / stem}.txt")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("align", help="fit a picture onto our minimap")
    a.add_argument("--picture", type=Path, required=True)
    a.add_argument("--crop", type=_crop, default=None)
    a.add_argument("--ours", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    a.set_defaults(func=cmd_align)
    d = sub.add_parser("diff", help="list the changed regions of a fit")
    d.add_argument("--fit", type=Path, required=True, help="the --out dir of an align run")
    d.add_argument("--ours", type=Path, required=True)
    d.add_argument("--min-px", type=int, default=60)
    d.set_defaults(func=cmd_diff)
    b = sub.add_parser("build", help="a new layout's assets and registry entries")
    b.add_argument("map")
    b.add_argument("patch", help="the patch the layout starts at, 13.10 form")
    b.add_argument("--picture", type=Path, required=True, help="the aligned picture (align's aligned.png)")
    b.add_argument("--regions", type=Path, required=True, help="mask png: the changed regions")
    b.add_argument("--into", type=Path, default=STATIC, help="an asset tree shaped like app/static")
    b.add_argument("--kill-lines", type=Path, default=None, help="kill lines json (default the tests' fixture)")
    b.set_defaults(func=cmd_build)
    o = sub.add_parser("overlay", help="a self-contained html to review the changed regions")
    o.add_argument("--ours", type=Path, required=True)
    o.add_argument("--fit", type=Path, required=True, help="the --out dir of an align run")
    o.add_argument("--map", required=True)
    o.add_argument("--out", type=Path, required=True, help="a scratch dir")
    o.add_argument("--min-px", type=int, default=60)
    o.set_defaults(func=cmd_overlay)
    f = sub.add_parser("fetch-minimap", help="compare valorant-api's minimap with the vendored one")
    f.add_argument("map")
    f.add_argument("--out", type=Path, required=True, help="a scratch dir for a differing image")
    f.set_defaults(func=cmd_fetch_minimap)
    n = sub.add_parser("notes", help="a patch page's Map Updates text for one map")
    n.add_argument("patch", help="13.10 form")
    n.add_argument("--map", required=True)
    n.add_argument("--out", type=Path, required=True)
    n.add_argument("--url", default=None, help="override the patch page url")
    n.set_defaults(func=cmd_notes)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
