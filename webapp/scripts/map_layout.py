"""Map layout tooling. Subcommands (a table, later items add more):

    align  --picture <png> --crop x0,y0,x1,y1 --ours <png> --out <dir>
           fit the picture's floor onto our minimap; writes aligned.png, diff.png, fit.json
    diff   --ours <png> --aligned-fit <dir>   (reads <dir>/fit.json and the picture it names) -> prints regions

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
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
