"""Place each map's buy-phase barriers from the replays' round starts.

    .venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\place_control_barriers.py [--map Ascent] [--pictures <dir>] [--write]

The user's rules (2026-09-30): a barrier is perpendicular to the walls of the passage it blocks, and it
sits at the most forward start positions of the team behind it (at t = 0, when the barriers drop,
players stand pressed against them). The hand-drawn barrier lines in tags.json (the drawing page's
Barrier tool, scripts/control_tagger.py) only say which passage each barrier is in:

- each stroke's direction is the shortest wall-to-wall chord through its centre; a wall notch can make
  a slightly slanted chord shortest, so among chords within CHORD_SLACK of it the one nearest the hand
  line's own angle wins;
- the start positions in that passage (within REACH_M walking of it) say whose side it is; the line goes
  MARGIN_M past that team's most forward one (a lone one more than OUTLIER_M ahead of the next is
  dropped) and is redrawn one paint cell wide from wall to wall;
- a stroke keeps its place with fewer than MIN_DOTS positions, for a move over MAX_MOVE_M, or where the
  moved line would cross a passage much wider than the stroke's.

For each map it prints each stroke, then checks the hand-drawn and the placed lines: a leak (the two
sides' start grounds joined), start positions off their own side (a player standing on a line cell is
judged by the open cells next to them, as app/control/engine.py Memory.start does), each side's ground,
and every area that changes owner between the two (a hand line with a gap shows up here). Start
positions are read from the database (read-only), or from `--starts <json>` ({map: [[side, x px,
y px], ...]}). With `--write`, maps with no leak get the placed lines in tags.json; then run
scripts/build_control_geometry.py --map <Map> for each.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from scipy import ndimage

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.control import geometry as cg  # noqa: E402

MARGIN_M, OUTLIER_M, MIN_DOTS, MAX_MOVE_M = 0.5, 1.5, 3, 3.0
NEAR_ALONG_M, MAX_CHORD_M, REACH_M, CHORD_SLACK = 8.0, 25.0, 12.0, 1.15
P = cg.PAINT_GRID
STEP = cg.PX // P


def round_starts(db, names: set[str] | None = None) -> dict[str, list[tuple[str, float, float]]]:
    """Every stored round's start positions on linked replays: {map: [(side, x px, y px), ...]}."""
    from app.control import engine
    from app.models.replay import Replay, ReplayRound
    from app.replays import format as fmt
    from app.services import replay_control as rc

    out: dict[str, list] = {}
    for replay in db.query(Replay).filter(Replay.link_status == "linked").order_by(Replay.id):
        if names and replay.map_name not in names:
            continue
        groups = rc.side_groups(db, replay)
        rows = out.setdefault(replay.map_name, [])
        for row in db.query(ReplayRound).filter(ReplayRound.replay_id == replay.id):
            sides = rc.round_link(replay, groups, row.round_number)["sides"]
            blob = fmt.decode_blob(row.data)
            for s, segs in (blob.get("tracks") or {}).items():
                side = sides.get(int(s), sides.get(s))
                seg = next((g for g in segs if g["t0"] <= 1.0 / blob["hz"]), None)
                if side is None or seg is None:
                    continue
                rows.append((side, float(engine._decode(seg["u"])[0]) * cg.PX / 10000,
                             float(engine._decode(seg["v"])[0]) * cg.PX / 10000))
    return out


def _march(walk: np.ndarray, c: np.ndarray, d: np.ndarray, limit: float):
    cells, k = [], 0.0
    while k <= limit:
        q = (c + k * d).round().astype(int)
        if not (0 <= q[0] < P and 0 <= q[1] < P) or not walk[q[0], q[1]]:
            return cells, True
        cells.append(tuple(q))
        k += 0.5
    return cells, False


def _chord(walk, c, d, limit):
    a, wa = _march(walk, c, d, limit)
    b, wb = _march(walk, c, -d, limit)
    return (len(a) + len(b)) * 0.5, wa and wb, a + b


def _reach(walk, src, limit):
    seen = np.zeros(walk.shape, bool)
    seen[src] = True
    front = seen.copy()
    for _ in range(limit):
        front = ndimage.binary_dilation(front) & walk & ~seen
        if not front.any():
            break
        seen |= front
    return seen


def place(geo: cg.Geometry, hand: np.ndarray, starts) -> tuple[np.ndarray, list[str]]:
    """The placed barrier lines (P x P cells) from the hand strokes `hand` (P x P cells) and the start
    positions [(side, x px, y px), ...], with one note per stroke."""
    cell_m = geo.m_per_px * STEP
    walk = geo.walk_px.reshape(P, STEP, P, STEP).mean((1, 3)) > 0.5
    xy = np.array([[y / STEP, x / STEP] for _, x, y in starts]) if starts else np.zeros((0, 2))
    sides = np.array([s for s, _, _ in starts])
    labels, n = ndimage.label(hand, np.ones((3, 3), bool))
    new = np.zeros((P, P), bool)
    notes = []
    for i in range(1, n + 1):
        ys, xs = np.nonzero(labels == i)
        c = np.array([ys.mean(), xs.mean()])
        cc = tuple(c.round().astype(int))
        if not walk[cc]:   # a stroke's centre off the floor: the nearest walkable cell close by
            y0, x0 = max(0, cc[0] - 4), max(0, cc[1] - 4)
            near = np.argwhere(walk[y0:cc[0] + 5, x0:cc[1] + 5]) + [y0, x0]
            if len(near):
                c = near[np.argmin(((near - c) ** 2).sum(1))].astype(float)
        where = f"stroke {i} at minimap px ({c[1] * STEP:.0f}, {c[0] * STEP:.0f})"
        cand = []
        for ang in np.arange(0, 180, 1):
            d = np.array([np.sin(np.radians(ang)), np.cos(np.radians(ang))])
            ln, closed, _ = _chord(walk, c, d, MAX_CHORD_M / cell_m)
            if closed:
                cand.append((ln, ang, d))
        if not cand:
            notes.append(f"{where}: no wall-to-wall chord; kept as drawn")
            new |= labels == i
            continue
        shortest = min(ln for ln, _, _ in cand)
        pts = np.stack([ys, xs], 1).astype(float)
        sv, vt = np.linalg.svd(pts - pts.mean(0), full_matrices=False)[1:]
        if len(ys) >= 3 and sv[0] > 3 * max(sv[1], 1e-9):
            hand_ang = np.degrees(np.arctan2(vt[0][0], vt[0][1])) % 180
            _, width, b = min(((min(abs(a - hand_ang), 180 - abs(a - hand_ang)), ln, d)
                               for ln, a, d in cand if ln <= CHORD_SLACK * shortest), key=lambda r: (r[0], r[1]))
        else:
            width, _, b = min(cand, key=lambda r: r[0])
        p = np.array([-b[1], b[0]])     # along the passage
        ok = _reach(walk, tuple(c.round().astype(int)), int(REACH_M / cell_m))
        rel = xy - c
        qb, qp = (rel @ b, rel @ p) if len(xy) else (np.zeros(0), np.zeros(0))
        pc = np.clip(xy.round().astype(int), 0, P - 1) if len(xy) else np.zeros((0, 2), int)
        inside = (ok[pc[:, 0], pc[:, 1]] & (np.abs(qb) <= width / 2 + 1) & (np.abs(qp) <= NEAR_ALONG_M / cell_m)
                  if len(xy) else np.zeros(0, bool))
        offset, note = 0.0, "too few start positions: kept where drawn"
        if inside.sum() >= MIN_DOTS:
            team = Counter(sides[inside]).most_common(1)[0][0]
            mine = inside & (sides == team)
            sigma = 1.0 if (qp[mine] > 0).sum() >= (qp[mine] < 0).sum() else -1.0
            fwd = np.sort(sigma * qp[mine])        # small = toward the enemy
            dropped = 0
            while len(fwd) > MIN_DOTS and fwd[1] - fwd[0] > OUTLIER_M / cell_m and dropped < 2:
                fwd, dropped = fwd[1:], dropped + 1
            if len(fwd) >= MIN_DOTS:
                offset = sigma * (fwd[0] - MARGIN_M / cell_m)
                note = (f"{int(mine.sum())} {team} positions; {MARGIN_M:.1f} m past the most forward"
                        f"{f' ({dropped} lone ones dropped)' if dropped else ''}, moved {abs(offset) * cell_m:.1f} m")
        if abs(offset) * cell_m > MAX_MOVE_M:
            note += f"; a {abs(offset) * cell_m:.1f} m move is too far to trust: kept where drawn"
            offset = 0.0
        ln2, closed2, cells = _chord(walk, c + offset * p, b, MAX_CHORD_M / cell_m)
        if offset and (not closed2 or not cells or ln2 > 1.5 * width + 2 / cell_m):
            note += f"; there the passage is {ln2 * cell_m:.0f} m wide (vs {width * cell_m:.0f} m): kept where drawn"
            ln2, closed2, cells = _chord(walk, c, b, MAX_CHORD_M / cell_m)
        if not cells:
            notes.append(f"{where}: {note}; no line through it: kept as drawn")
            new |= labels == i
            continue
        for q in cells:
            new[q] = True
        notes.append(f"{where}: {note}; line {ln2 * cell_m:.1f} m")
    return new, notes


def check(geo: cg.Geometry, cells: np.ndarray, starts) -> dict:
    """Leak, start positions off their own side, positions on a line cell, each side's ground (m2) and
    the owner of every engine cell (0 nobody, 1 attack, 2 defense) for barrier lines `cells`."""
    barrier = cg.barrier_cells(cells.repeat(STEP, 0).repeat(STEP, 1))
    regions, _ = ndimage.label(geo.walk & ~barrier)
    pos = [(side, geo.cell_of_px(x, y)) for side, x, y in starts]
    labs: dict[str, list[int]] = {}
    for side, c in pos:
        if regions.flat[c]:
            labs.setdefault(side, []).append(int(regions.flat[c]))
    ground = {s: Counter(v).most_common(1)[0][0] for s, v in labs.items()}
    off = on_line = 0
    for side, c in pos:
        lab = int(regions.flat[c])
        if not lab:
            y, x = divmod(c, cg.GRID)
            near = {int(v) for v in regions[max(0, y - 1):y + 2, max(0, x - 1):x + 2].ravel() if v}
            on_line += 1
            if ground.get(side) not in near:
                off += 1
        elif lab != ground.get(side):
            off += 1
    owner = np.zeros(regions.shape, np.int8)
    for s, lab in ground.items():
        owner[regions == lab] = 1 if s == "attack" else 2
    m2 = (geo.m_per_px * cg.CELL) ** 2
    return {"leak": len(set(ground.values())) < len(ground), "off": off, "on_line": on_line,
            "areas": {s: float((regions == lab).sum() * m2) for s, lab in ground.items()}, "owner": owner}


def changed_areas(geo: cg.Geometry, before: dict, after: dict, min_m2: float = 40.0) -> list[str]:
    """Every area that changes owner between two `check` results."""
    names = {0: "nobody", 1: "attack", 2: "defense"}
    m2 = (geo.m_per_px * cg.CELL) ** 2
    diff = before["owner"] != after["owner"]
    lab, _ = ndimage.label(diff)
    out = []
    for k, sl in enumerate(ndimage.find_objects(lab), 1):
        m = lab[sl] == k
        if m.sum() * m2 < min_m2:
            continue
        b = names[int(np.bincount(before["owner"][sl][m]).argmax())]
        a = names[int(np.bincount(after["owner"][sl][m]).argmax())]
        out.append(f"area x {sl[1].start * cg.CELL}-{sl[1].stop * cg.CELL}, y {sl[0].start * cg.CELL}-"
                   f"{sl[0].stop * cg.CELL} ({m.sum() * m2:.0f} m2): {b} -> {a}")
    return out


def picture(geo: cg.Geometry, name: str, hand: np.ndarray, new: np.ndarray, result: dict, starts, path: Path) -> None:
    """The minimap with each side's start ground tinted, the hand lines pink, the placed lines white,
    attackers yellow and defenders cyan."""
    from PIL import Image

    img = np.array(Image.open(cg.MINIMAP_DIR / f"{name}.png").convert("RGBA"))
    bg = Image.new("RGBA", img.shape[1::-1], (20, 22, 26, 255))
    bg.alpha_composite(Image.fromarray(img))
    pic = np.array(bg)
    for code, col in ((1, (229, 72, 77)), (2, (58, 160, 255))):
        m = (result["owner"] == code).repeat(cg.CELL, 0).repeat(cg.CELL, 1)
        pic[m, :3] = (0.55 * pic[m, :3] + 0.45 * np.array(col)).astype(np.uint8)
    pic[hand.repeat(STEP, 0).repeat(STEP, 1)] = (255, 90, 200, 255)
    pic[new.repeat(STEP, 0).repeat(STEP, 1)] = (255, 255, 255, 255)
    for s, x, y in starts:
        x, y = int(x), int(y)
        pic[max(0, y - 2):y + 3, max(0, x - 2):x + 3] = (255, 214, 0, 255) if s == "attack" else (0, 229, 200, 255)
    Image.fromarray(pic).convert("RGB").save(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", action="append", help="only this map (repeatable; default: every map with barrier paint)")
    ap.add_argument("--starts", type=Path, help="start positions from this JSON instead of the database")
    ap.add_argument("--pictures", type=Path, help="write <Map>_barriers.png pictures here")
    ap.add_argument("--write", action="store_true", help="save the placed lines into tags.json (maps with no leak)")
    args = ap.parse_args(argv)
    tags = cg.load_tags()
    names = args.map or sorted(m for m, e in tags.get("maps", {}).items() if e.get("barrier_paint"))
    if args.starts:
        starts = json.loads(args.starts.read_text(encoding="utf-8"))
    else:
        from app.db import SessionLocal
        with SessionLocal() as db:
            starts = round_starts(db, set(names))
    written = []
    for name in names:
        entry = tags["maps"].get(name) or {}
        if not entry.get("barrier_paint"):
            print(f"\n== {name}: no barrier paint")
            continue
        geo = cg.load_geometry(name)
        rows = [tuple(r) for r in starts.get(name, [])]
        hand = cg.unpack_paint(entry["barrier_paint"])[::STEP, ::STEP]
        new, notes = place(geo, hand, rows)
        print(f"\n== {name}: {len(notes)} strokes, {len(rows)} start positions")
        for note in notes:
            print("  " + note)
        results = {"hand-drawn": check(geo, hand, rows), "placed": check(geo, new, rows)}
        for label, r in results.items():
            print(f"  {label:10}: {'LEAKS' if r['leak'] else 'no leak'}, {r['off']} off their own side, "
                  f"{r['on_line']} on a line cell; " + ", ".join(f"{s} {a:.0f} m2" for s, a in sorted(r["areas"].items())))
        for line in changed_areas(geo, results["hand-drawn"], results["placed"]):
            print("  changes owner: " + line)
        if args.pictures:
            args.pictures.mkdir(parents=True, exist_ok=True)
            path = args.pictures / f"{name}_barriers.png"
            picture(geo, name, hand, new, results["placed"], rows, path)
            print(f"  picture: {path}")
        if args.write and not results["placed"]["leak"]:
            entry["barrier_paint"] = cg.pack_paint(new)
            written.append(name)
    if args.write:
        (cg.ASSET_DIR / "tags.json").write_text(json.dumps(tags, indent=1) + "\n", encoding="utf-8")
        print(f"\nwrote {', '.join(written) or 'nothing'}; now run scripts/build_control_geometry.py "
              + " ".join(f"--map {n}" for n in written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
