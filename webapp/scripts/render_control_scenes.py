"""Render map-control states at chosen moments with one code version, for a before/after review.

    .venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\render_control_scenes.py --code <webapp dir> --label after --out <dir> <match uuid>:<round>@<t> ...

`--code` is a webapp folder whose `app` is imported (this checkout, or main's code exported with
`git archive --format=tar -o main.tar origin/main app` run from webapp/ and extracted under a folder's
`webapp/`), so the same scenes render with either version. Each scene replays the round's ticks up to t
(memory needs every earlier tick), composes the state at t and draws it over the minimap: attack red,
defense blue (light = passive, mid = safe, strong = active), contested purple; players as dots with a
facing line; smokes as circles; Viper's wall (from the blob) as a white line. It writes
<map>_<uuid8>_r<round>_<t>_<label>.png and summary_<label>.json (each side's held m2 per scene).
Rounds come from the database (read-only). Set CONTROL_CACHE_DIR to share one visibility cache between
versions (it is keyed by the masks, so that is safe). scripts/control_compare_page.py puts two labels'
pictures side by side.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--code", type=Path, required=True, help="the webapp folder whose app/ to import")
ap.add_argument("--label", required=True, help="a name for this version (before, after, ...)")
ap.add_argument("--out", type=Path, required=True, help="where the pictures go")
ap.add_argument("scenes", nargs="+", help="<match uuid>:<round>@<seconds>")
args = ap.parse_args()
sys.path.insert(0, str(args.code.resolve()))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from app.control import engine  # noqa: E402
from app.control.task import _load  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models.replay import Replay, ReplayRound  # noqa: E402
from app.replays import format as fmt  # noqa: E402
from app.services import replay_control as rc  # noqa: E402

SIDE_RGB = {"attack": (229, 72, 77), "defense": (58, 160, 255)}
LEVEL_ALPHA = {1: 0.30, 2: 0.50, 3: 0.75}
args.out.mkdir(parents=True, exist_ok=True)
summary = {}
db = SessionLocal()
for spec in args.scenes:
    where, t_req = spec.rsplit("@", 1)
    uuid, round_number = where.split(":")
    replay = db.query(Replay).filter(Replay.match_uuid == uuid).one()
    row = db.get(ReplayRound, (replay.id, int(round_number)))
    link = rc.round_link(replay, rc.side_groups(db, replay), int(round_number))
    geo = _load(replay.map_name)
    blob = fmt.decode_blob(row.data)
    ctl = engine.ControlLink(sides={int(s): v for s, v in link["sides"].items()},
                             db_deaths=tuple((int(s), float(t)) for s, t in link["db_deaths"]))
    rnd = engine.RoundInputs(blob, geo, ctl)
    t_at = engine.snap(float(t_req))
    started = time.perf_counter()
    memory = engine.Memory(geo)
    for t in [float(t) for t in rnd.tick_times() if t < t_at]:
        memory.apply(engine.Tick(rnd, t))
    tick = engine.Tick(rnd, t_at)
    memory.apply(tick)
    state = tick.compose()["state"]
    side_of = {g: rnd.group_side.get(g) for g in ("A", "B")}
    img = Image.open(args.code / "app" / "static" / "img" / "maps" / f"{replay.map_name}.png").convert("RGBA")
    base = Image.new("RGBA", img.size, (20, 22, 26, 255))
    base.alpha_composite(img)
    pic = np.array(base).astype(float)
    G, C = engine.GRID, engine.CELL
    st = state.reshape(G, G)
    groups = (("A", (engine.A_PASSIVE, engine.A_SAFE, engine.A_ACTIVE)),
              ("B", (engine.B_PASSIVE, engine.B_SAFE, engine.B_ACTIVE)))
    for group, codes in groups:
        rgb = np.array(SIDE_RGB.get(side_of[group], (200, 200, 200)), float)
        for level, code_ in enumerate(codes, 1):
            m = (st == code_).repeat(C, 0).repeat(C, 1)
            pic[m, :3] = (1 - LEVEL_ALPHA[level]) * pic[m, :3] + LEVEL_ALPHA[level] * rgb
    m = np.isin(st, (engine.CONTESTED, engine.CONTESTED_ACTIVE)).repeat(C, 0).repeat(C, 1)
    pic[m, :3] = 0.4 * pic[m, :3] + 0.6 * np.array((170, 90, 220))
    out = Image.fromarray(pic.astype(np.uint8))
    draw = ImageDraw.Draw(out)
    for x, y, r, _ in [s for s in rnd.smokes_at(t_at) if isinstance(s, tuple)]:
        draw.ellipse([x - r, y - r, x + r, y + r], outline=(235, 235, 235, 255), width=2)
    for e in blob.get("util") or []:
        if e.get("k") == "ability" and e.get("points") and any(
                a <= t_at < (b if b is not None else 1e9) for a, b in e.get("on") or []):
            draw.line([(u * 1024 / 10000, v * 1024 / 10000) for u, v in e["points"]], fill=(255, 255, 255, 255), width=4)
    for s, h in tick.holders.items():
        col = SIDE_RGB.get(ctl.sides.get(s), (255, 255, 255))
        pos = rnd.pos(s, t_at)
        yaw = np.radians(pos[2]) if pos else 0.0
        draw.line([h.x, h.y, h.x + 22 * np.cos(yaw), h.y + 22 * np.sin(yaw)], fill=(255, 255, 255, 255), width=2)
        draw.ellipse([h.x - 7, h.y - 7, h.x + 7, h.y + 7], fill=col + (255,), outline=(255, 255, 255, 255), width=2)
    stem = f"{replay.map_name}_{uuid[:8]}_r{round_number}_{t_req}"
    out.convert("RGB").resize((768, 768)).save(args.out / f"{stem}_{args.label}.png")
    m2 = (geo.m_per_px * C) ** 2
    counts = {side_of[g]: int(np.isin(state, codes).sum() * m2) for g, codes in groups}
    counts["contested"] = int(np.isin(state, (engine.CONTESTED, engine.CONTESTED_ACTIVE)).sum() * m2)
    summary[stem] = counts
    print(f"{args.label} {stem}: {counts} ({time.perf_counter() - started:.0f} s)", flush=True)
(args.out / f"summary_{args.label}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
