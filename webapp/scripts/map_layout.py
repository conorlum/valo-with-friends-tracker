"""Map layout tooling. Subcommands (a table, later items add more):

    align  --picture <png> --crop x0,y0,x1,y1 --ours <png> --out <dir>
           fit the picture's floor onto our minimap; writes aligned.png, diff.png, fit.json
    diff   --ours <png> --aligned-fit <dir>   (reads <dir>/fit.json and the picture it names) -> prints regions
    build  <Map> <patch, 13.10 form> --picture <aligned png> --regions <mask png> --into <asset dir>
           [--kill-lines <json>]: the layout's minimap, masks, chokes, tags, callouts, layouts.json and
           index.json entries and an empty must_block.<key>.json, all under --into (a tree shaped like
           webapp/app/static; default the real one). The base layout and maps.json are never touched.

Offline only: no database, no network.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import map_layout_fit as fit  # noqa: E402

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
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
