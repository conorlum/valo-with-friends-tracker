"""Build app/data/plant_zones.json from the minimaps' shaded plant sites. Read-only on the database.

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\build_plant_zones.py [--archive DIR] [--check]

Each zone is the minimap's yellow-tinted area in its real shape, moved by a small per-map alignment
fitted to where planters stood (linked replays, plus archived tracker.gg payloads with --archive).
A map is written only with at least MIN_PLANTERS positions and MIN_INSIDE_PCT of them inside its
zone; any other map keeps the plain 0.7 m position. --check prints without writing.
"""
import argparse
from collections import defaultdict
import gzip
import json
import math
from pathlib import Path
import statistics
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image
from scipy import ndimage
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Match
from app.models.replay import Replay, ReplayRound
from app.replays import layouts
from app.replays.format import decode_blob, decode_segment
from app.services import plant_position as position
from app.services import round_plants as plants

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
MIN_TINT = 15
MAX_RED_GREEN_GAP = 8
CLOSE_ITERATIONS = 4
MIN_PIECE_PIXELS = 1500
MAX_SHIFT = 4
BOX_MARGIN = 24
MIN_PLANTERS = 100
MIN_INSIDE_PCT = 98.0


def zone_mask(rgba):
    """The tinted site shading, with lines drawn over it closed and specks dropped."""
    rgba = rgba.astype(int)
    tint = ((rgba[..., 3] > 0) & (rgba[..., 0] - rgba[..., 2] >= MIN_TINT)
            & (np.abs(rgba[..., 0] - rgba[..., 1]) <= MAX_RED_GREEN_GAP))
    closed = ndimage.binary_closing(tint, iterations=CLOSE_ITERATIONS)
    labels, count = ndimage.label(closed)
    if not count:
        return closed
    sizes = ndimage.sum(closed, labels, range(1, count + 1))
    return np.isin(labels, [i + 1 for i, size in enumerate(sizes) if size >= MIN_PIECE_PIXELS])


def pixels(points):
    return np.array([(int(v / position.UV_SCALE * position.GRID_SIZE),
                      int(u / position.UV_SCALE * position.GRID_SIZE)) for u, v in points], dtype=int)


def inside_count(zone, cells, dy=0, dx=0):
    ys, xs = cells[:, 0] - dy, cells[:, 1] - dx
    valid = (ys >= 0) & (ys < zone.shape[0]) & (xs >= 0) & (xs < zone.shape[1])
    return int(zone[ys[valid], xs[valid]].sum())


def best_shift(zone, cells):
    """The (dy, dx) that puts most planters inside; ties go to the smallest move."""
    options = ((inside_count(zone, cells, dy, dx), -(abs(dy) + abs(dx)), -dy, -dx)
               for dy in range(-MAX_SHIFT, MAX_SHIFT + 1) for dx in range(-MAX_SHIFT, MAX_SHIFT + 1))
    _, _, dy, dx = max(options)
    return -dy, -dx


def shifted(zone, dy, dx):
    return ndimage.shift(zone.astype(np.uint8), (dy, dx), order=0, mode="constant").astype(bool)


def fill_planted_holes(zone, cells):
    """Boxes are unshaded holes in a site; one that at least two planters stood on is part of the zone."""
    holes = ndimage.binary_fill_holes(zone) & ~zone
    labels, count = ndimage.label(holes)
    if not count:
        return zone
    valid = (cells[:, 0] >= 0) & (cells[:, 0] < zone.shape[0]) & (cells[:, 1] >= 0) & (cells[:, 1] < zone.shape[1])
    hits = np.bincount(labels[cells[valid, 0], cells[valid, 1]], minlength=count + 1)
    return zone | np.isin(labels, [i for i in range(1, count + 1) if hits[i] >= 2])


def entry_for(zone, walk):
    ys, xs = np.nonzero(zone)
    x0, y0 = max(0, int(xs.min()) - BOX_MARGIN), max(0, int(ys.min()) - BOX_MARGIN)
    x1 = min(zone.shape[1], int(xs.max()) + BOX_MARGIN + 1)
    y1 = min(zone.shape[0], int(ys.max()) + BOX_MARGIN + 1)
    return {"box": [x0, y0, x1, y1],
            "zone": position.encode_runs(zone[y0:y1, x0:x1].ravel().tolist()),
            "walk": position.encode_runs(walk[y0:y1, x0:x1].ravel().tolist())}


def replay_plants(db):
    """Per asset key: planter position, final aim and exact spike position of each replay plant."""
    found = defaultdict(list)
    replays = [(r.id, r.map_name, r.game_branch) for r in db.scalars(select(Replay).where(
        Replay.link_status == "linked", Replay.match_id.is_not(None)).order_by(Replay.id))]
    for replay_id, map_name, branch in replays:
        key = plants.asset_key_for(map_name, branch)
        for row in db.scalars(select(ReplayRound).where(ReplayRound.replay_id == replay_id)):
            try:
                blob = decode_blob(row.data)
            except OSError:
                blob = json.loads(zlib.decompress(row.data))
            bomb = next((e for e in blob.get("util") or ()
                         if e.get("k") == "ability" and e.get("kind") == "Bomb"), None)
            if bomb is None:
                continue
            attempts = [a for a in (blob.get("player_state") or {}).get("plants") or ()
                        if a[1] is not None and abs(a[1] - bomb["t"]) <= 0.2]
            if len(attempts) != 1:
                continue
            samples = [s for segment in (blob.get("tracks") or {}).get(str(attempts[0][2]), ())
                       for s in decode_segment(segment, blob["hz"])]
            if not samples:
                continue
            at = min(samples, key=lambda s: abs(s[0] - attempts[0][1]))
            if abs(at[0] - attempts[0][1]) <= 0.05:
                found[key].append(dict(u=at[1], v=at[2], yaw=at[3], bomb_u=bomb["u"], bomb_v=bomb["v"],
                                       scale=abs(plants._maps()[map_name].x_mult) * 1e6))
        db.rollback()
    return found


def archive_planters(db, directory):
    """Per asset key: where tracker.gg says each planter stood, from archived payloads."""
    found = defaultdict(list)
    names = dict(db.execute(select(Match.external_id, Match.map_name)).all())
    db.rollback()
    for path in sorted(Path(directory).glob("*.json.gz")):
        map_name = names.get(path.name[:-len(".json.gz")])
        if map_name not in plants._maps():
            continue
        payload = json.loads(gzip.decompress(path.read_bytes()))
        for summary in payload.get("segments") or ():
            if isinstance(summary, dict) and summary.get("type") == "round-summary":
                plant = plants.plant_from_summary(summary)
                if plant is not None and "view" in plant:
                    found[plants.asset_key_for(map_name, None)].append(
                        plants.to_uv(map_name, plant["planter_x"], plant["planter_y"]))
    return found


def replay_error(key, rows):
    errors = []
    for row in rows:
        radians = math.radians(row["yaw"])
        point_at = lambda metres: (row["u"] + math.cos(radians) * metres * row["scale"],
                                   row["v"] + math.sin(radians) * metres * row["scale"])
        u, v = point_at(position.pick_step(key, point_at))
        errors.append(math.hypot(u - row["bomb_u"], v - row["bomb_v"]) / row["scale"])
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        replays = replay_plants(db)
        archived = archive_planters(db, args.archive) if args.archive else {}
    written = {}
    for map_name, rows in sorted(layouts.load_registry()["maps"].items()):
        for row in rows:
            key = row["key"]
            image = STATIC / "img" / "maps" / f"{key}.png"
            walk_path = STATIC / "data" / "control" / f"{key}.walk.png"
            if not image.is_file() or not walk_path.is_file():
                print(f"{key}: no minimap or walk mask, not written")
                continue
            points = [(r["u"], r["v"]) for r in replays.get(key, ())] + list(archived.get(key, ()))
            if len(points) < MIN_PLANTERS:
                print(f"{key}: {len(points)} planter positions, needs {MIN_PLANTERS}, not written")
                continue
            cells = pixels(points)
            zone = zone_mask(np.asarray(Image.open(image).convert("RGBA")))
            dy, dx = best_shift(zone, cells)
            zone = fill_planted_holes(shifted(zone, dy, dx), cells)
            inside =100 * inside_count(zone, cells) / len(cells)
            if inside < MIN_INSIDE_PCT:
                print(f"{key}: {len(points)} planters, shift ({dy}, {dx}), {inside:.1f}% inside, not written")
                continue
            walk = np.asarray(Image.open(walk_path).convert("L")) > 127
            if inside_count(walk, cells) * 2 < len(cells):
                walk = ~walk
            written[key] = {**entry_for(zone, walk), "shift": [dy, dx], "planters": len(points),
                            "inside_pct": round(inside, 1)}
            print(f"{key}: {len(points)} planters, shift ({dy}, {dx}), {inside:.1f}% inside")
    output = {"version": 1, "maps": written}
    if not args.check:
        position.ZONES_PATH.write_text(json.dumps(output, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"wrote {position.ZONES_PATH} ({position.ZONES_PATH.stat().st_size / 1024:.0f} KB), {len(written)} maps")
    position._zones_file.cache_clear()
    position._grids.cache_clear()
    if args.check:
        position._zones_file = lambda: output
    errors = [e for key, rows in replays.items() if key in written for e in replay_error(key, rows)]
    if errors:
        print(f"against {len(errors)} replay plants (the alignment was fitted on them): mean error "
              f"{statistics.mean(errors):.3f} m, within 0.2 m {100 * sum(e <= .2 for e in errors) / len(errors):.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
