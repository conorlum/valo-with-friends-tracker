"""Stage 0a (docs/replay-map-control-plan.md, Risk 1): the kill-line test on the alpha mask, and the
per-candidate data the tagging page needs.

Feasibility tooling, not product code. Reads `<out>/kills/*.json` (kill_lines.py) and
`<out>/geometry/` (geometry.py). A kill qualifies when its lethal row is a bullet hit
(`MulticastNotifyDamage_Point`) with `IsWallPenetration` false, both positions are known within
MAX_DT_MS of the kill, and neither sits on the image edge. For each qualifying kill it records
whether the sight walls (alpha 0 plus glyphs) block the line, and which candidates it crosses.

Writes `<out>/risk1.json` and `<out>/risk1/<Map>.png` (blocked lines red, clear green).

    .venv313\\Scripts\\python.exe scripts\\control_feasibility\\risk1.py <out_dir>
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

MAPS_DIR = Path(__file__).resolve().parents[2] / "app" / "static" / "img" / "maps"
END_SKIP_PX = 3
MAX_DT_MS = 100
EDGE_UV = 20
BAR = 0.02


def px(pos: dict) -> tuple[int, int]:
    return min(1023, int(pos["u"] / 10000 * 1024)), min(1023, int(pos["v"] / 10000 * 1024))


def line(a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    n = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
    if n == 0:
        return []
    xs = np.round(np.linspace(a[0], b[0], n + 1)).astype(int)
    ys = np.round(np.linspace(a[1], b[1], n + 1)).astype(int)
    return list(zip(xs.tolist(), ys.tolist()))[END_SKIP_PX:-END_SKIP_PX or None]


def qualifies(k: dict) -> str | None:
    """None when the kill counts, else why not."""
    if k.get("rpc") != "MulticastNotifyDamage_Point":
        return "not a bullet hit"
    if k.get("wall_pen") is not False:
        return "wallbang" if k.get("wall_pen") else "no wallbang flag"
    for side in ("killer_pos", "victim_pos"):
        pos = k.get(side)
        if not pos or abs(pos["dt_ms"]) > MAX_DT_MS:
            return "no position"
        if min(pos["u"], pos["v"]) < EDGE_UV or max(pos["u"], pos["v"]) > 10000 - EDGE_UV:
            return "position on the image edge"
    if k["killer"] == k["victim"]:
        return "self kill"
    return None


def main(out: Path) -> None:
    geo = out / "geometry"
    by_map: dict[str, list[dict]] = defaultdict(list)
    positions: dict[str, list] = defaultdict(list)
    for path in sorted((out / "kills").glob("*.json")):
        data = json.loads(path.read_text())
        for k in data["kills"]:
            k["replay"] = data["uuid"]
            by_map[data["map"]].append(k)
        for pts in data["positions"].values():
            positions[data["map"]].extend((p[1], p[2]) for p in pts)
    (out / "risk1").mkdir(exist_ok=True)
    result = {"bar": BAR, "maps": {}}
    for name in sorted(by_map):
        rgba = np.array(Image.open(MAPS_DIR / f"{name}.png").convert("RGBA"))
        walk = np.array(Image.open(geo / f"{name}.walk.png")) > 0
        enc = np.array(Image.open(geo / f"{name}.labels.png")).astype(np.int32)
        labels = enc[..., 0] + 256 * enc[..., 1]
        cands = json.loads((geo / f"{name}.json").read_text())["candidates"]
        kind = {c["id"]: c["kind"] for c in cands}
        occupancy = Counter()
        for u, v in positions[name]:
            x, y = px({"u": u, "v": v})
            if labels[y, x]:
                occupancy[int(labels[y, x])] += 1
        skipped = Counter()
        lines = []
        for k in by_map[name]:
            why = qualifies(k)
            if why:
                skipped[why] += 1
                continue
            a, b = px(k["killer_pos"]), px(k["victim_pos"])
            pts = line(a, b)
            crossed = sorted({int(labels[y, x]) for x, y in pts if labels[y, x]})
            # "hard": the outside void or a glyph, which no tag changes. Inner voids are candidates:
            # walls unless tagged see-across.
            hard = any(not walk[y, x] and not labels[y, x] for x, y in pts)
            voids = any(kind.get(c) == "void" for c in crossed)
            lines.append({"a": a, "b": b, "hard": hard, "wall": hard or voids, "crossed": crossed,
                          "dz": k["killer_pos"]["z"] - k["victim_pos"]["z"], "replay": k["replay"][:8],
                          "t_ms": k["t_ms"]})
        n = len(lines)
        wall = sum(l["wall"] for l in lines)
        hard = sum(l["hard"] for l in lines)
        all_cover = sum(l["wall"] or any(kind.get(c) != "void" for c in l["crossed"]) for l in lines)
        crossings = Counter(c for l in lines if not l["hard"] for c in l["crossed"])
        replays = sorted({k["replay"] for k in by_map[name]})
        result["maps"][name] = {
            "replays": len(replays), "kills": len(by_map[name]), "qualifying": n, "skipped": dict(skipped),
            "blocked_alpha": wall, "blocked_alpha_share": round(wall / n, 4) if n else None,
            "passes_alpha": bool(n) and wall / n <= BAR,
            "blocked_if_every_inner_void_is_see_across_share": round(hard / n, 4) if n else None,
            "blocked_if_every_candidate_is_cover_share": round(all_cover / n, 4) if n else None,
            "blocked_alpha_dz": [l["dz"] for l in lines if l["wall"]],
            "candidates": {str(c["id"]): {"crossings": crossings.get(c["id"], 0),
                                          "occupancy": occupancy.get(c["id"], 0)} for c in cands},
            "lines": lines,
        }
        canvas = Image.alpha_composite(Image.new("RGBA", (1024, 1024), (25, 25, 25, 255)),
                                       Image.fromarray(rgba)).convert("RGB")
        draw = ImageDraw.Draw(canvas)
        for l in sorted(lines, key=lambda l: l["wall"]):
            draw.line([tuple(l["a"]), tuple(l["b"])], fill=(235, 60, 60) if l["wall"] else (60, 200, 90), width=1)
        canvas.save(out / "risk1" / f"{name}.png")
        share = f"{wall / n:.1%}" if n else "n/a"
        print(f"{name}: {len(replays)} replays, {n} qualifying of {len(by_map[name])} kills, alpha blocked {wall} "
              f"({share}), if inner voids seen across {hard / n if n else 0:.1%}, "
              f"every-candidate-as-cover {all_cover / n if n else 0:.1%}, skipped {dict(skipped)}")
    (out / "risk1.json").write_text(json.dumps(result))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
