"""Stage 0b (docs/replay-map-control-plan.md): the full control rules, tick by tick, on one real round.

Feasibility tooling, not product code. Walls and see-across tags only (no cover tags yet). Per tick
at 16 Hz, from the round blob:

1. Vision: each alive player's 103-degree view, raycast on the 1024 sight mask with smokes (hollow
   and solid) and the Q72 corner tolerance; the held cone from movement; statuses (flash,
   nearsight, concuss/reveal downgrade). The same views are also built from the static cell bitsets
   (the cell's 360-degree row, cut to the cone and the smokes' shadows), to compare.
2. Passive watchers: trips, alarmbots, Chamber traps, flown drones (owner's own view dropped).
3. Safe space: each team's free space (flood fill from its alive players through cells the other
   team doesn't watch) and the cells it sees, smoke-aware, from the fill's frontier only (Q70).
4. The contest rules: both-claim, contested holders (enemy sight, enemy damage areas, statuses),
   the entry's way back (Q56), cells lost to a status (Q55).
5. Coverage (shared cells split evenly) and control: the signed counterfactual (Q49, Q54, Q62, Q63),
   both full (both fills from scratch) and incremental, every tick.

Also, on sampled smoked ticks: frontier-only against the exact recheck from every free cell (Q70).

Writes `<out>/engine/<Map>_r<round>.npz` (states, per-player masks) and `.json` (timings, checks).

    .venv313\\Scripts\\python.exe scripts\\control_feasibility\\engine_proto.py <out_dir> <blob.json> <round> [--every N]
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

MAPS_JSON = Path(__file__).resolve().parents[2] / "app" / "static" / "data" / "maps.json"
TAGS = Path(__file__).resolve().parent / "control-tags.json"

G = 128
PX = 1024
CELL = PX // G
HZ = 16
FOV_HALF = 51.5
RAY_STEP_DEG = 0.5
RAY_STEP_PX = 2
RAY_MAX_PX = 1500
CORNER_TOLERANCE_M = 0.3  # Q72: a line through less wall than this, in total, is clear
RUN_MPS, WALK_MPS = 5.0, 1.5
CONE_HALF = {"run": 2.0, "walk": 5.0, "hold": 10.0}
FAST_TURN_DPS = 90.0
SPEED_WINDOW_S = 0.25
NEARSIGHT_RADIUS_M = 4.0
WAY_BACK_WIDTH_M = 2.0

# Placeholders until the blob carries them (plan: "Inputs the blob lacks"). A flash row's `t` is
# the cast, not the hit, and the per-target blind time is dropped by the condenser.
FLASH_FULL_S = {"phoenix": 1.5, "yoru": 1.5, "breach": 2.25, "kayo": 2.25, "skye": 2.25}
FLASH_DEFAULT_S = 1.5
FLASH_FUSE_S = 0.5
NEARSIGHT_S = {"omen_paranoia": 2.0}
NEARSIGHT_DEFAULT_S = 1.0
ALARMBOT_M = 5.5
CHAMBER_TRAP_M = 7.0

# `<code>_<name>` -> (radius in world units, solid). Radii are the viewer's (replay.js ABILITY_STYLES).
SMOKES = [(re.compile(p), r, solid) for p, r, solid in [
    (r"^Wraith_4_Smoke$", 410, False), (r"^Smonk_NewSmoke(_PDS)?$", 410, False), (r"^Gumshoe_Q_Cage$", 330, False),
    (r"^Rift_E_SmokeZone$", 475, False), (r"^Pandemic_4_SmokeZone$", 450, False),
    (r"^Pandemic_X_Circular$", 900, False), (r"^Wushu_4_SmokeZone$", 335, False),
    (r"^Sarge_4_Smoke_Production", 415, True), (r"^Iris_E_Smoke$", 400, False)]]
DAMAGE = [(re.compile(p), r) for p, r in [
    (r"^Phoenix_MolotovFire$", 450), (r"^Sarge_Q_Molotov_Production$", 450),
    (r"^Pandemic_AcidMolotov_NewMolotov$", 450), (r"^Aggrobot_C_ExplodeyPatch$", 450),
    (r"^Killjoy_4_BeeSwarm_Damage$", 450), (r"^Hunter_4_ExplosiveBolt_Explosion$", 350),
    (r"^Clay_Q_Explosion$", 450), (r"^Cashew_X_Segment$", 350), (r"^Sarge_X_OrbitalStrike", 900),
    (r"^Cashew_E_Explosion$", 400)]]
# Flown drones: (cone half-angle, range in m or None).
DRONES = {"Hunter_E_Drone": (FOV_HALF, None), "Guide_Q_PossessableScout": (45.0, 22.5)}
CONTEST_STATUSES = {"slowed", "tethered", "hindered", "fragile", "suppressed", "decay", "decayed"}
DOWNGRADE_STATUSES = {"concussed", "stunned"}

# Cell state codes (one per walkable cell per tick).
NONE, A_PASSIVE, A_SAFE, A_ACTIVE, B_PASSIVE, B_SAFE, B_ACTIVE, CONTESTED_ACTIVE, CONTESTED = range(9)
EIGHT = np.ones((3, 3), bool)


def peak_memory_mb() -> float:
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    c = Counters()
    c.cb = ctypes.sizeof(c)
    process = ctypes.windll.kernel32.GetCurrentProcess
    process.restype = ctypes.c_void_p
    info = ctypes.windll.psapi.GetProcessMemoryInfo
    info.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    info(process(), ctypes.byref(c), c.cb)
    return c.PeakWorkingSetSize / 1e6


# ---------------------------------------------------------------- geometry


@dataclass
class Geometry:
    name: str
    wall: np.ndarray          # 1024 sight walls
    walk: np.ndarray          # G x G walkable cells
    m_per_px: float
    uv_per_unit: float
    tol_hits: int             # wall samples a ray may cross (Q72)
    rows: np.ndarray = None   # packed 360-degree visibility per walkable cell
    row_of: np.ndarray = None  # flat cell -> row index, -1 if not walkable
    centres: np.ndarray = None  # flat cell -> (x, y) px
    build_s: float = 0.0

    @property
    def cell_m(self) -> float:
        return self.m_per_px * CELL


def load_geometry(out: Path, name: str) -> Geometry:
    tags = json.loads(TAGS.read_text())["maps"] if TAGS.is_file() else {}
    if name in tags:
        raise SystemExit(f"{name} has see-across tags; this prototype handles untagged maps only")
    walk_px = np.array(Image.open(out / "geometry" / f"{name}.walk.png")) > 0
    walk = walk_px.reshape(G, CELL, G, CELL).mean((1, 3)) > 0.5
    scale = json.loads(MAPS_JSON.read_text())[name]["xMultiplier"]
    m_per_px = 1 / (scale * PX * 100)
    tol_hits = int(CORNER_TOLERANCE_M / m_per_px // RAY_STEP_PX)
    geo = Geometry(name, ~walk_px, walk, m_per_px, scale * 10000, tol_hits)
    ys, xs = np.divmod(np.arange(G * G), G)
    geo.centres = np.stack([xs * CELL + CELL / 2, ys * CELL + CELL / 2], 1).astype(np.float32)
    cache = out / "engine" / f"{name}.vis_t{tol_hits}.npz"
    if cache.is_file():
        z = np.load(cache)
        geo.rows, geo.row_of, geo.build_s = z["rows"], z["row_of"], float(z["build_s"])
        return geo
    started = time.time()
    cells = np.flatnonzero(walk.ravel())
    rows = np.zeros((len(cells), G * G // 8), np.uint8)
    angles = np.arange(0, 360, RAY_STEP_DEG)
    for i, c in enumerate(cells):
        x, y = geo.centres[c]
        rows[i] = np.packbits(cast(geo, float(x), float(y), angles, []))
    row_of = -np.ones(G * G, np.int32)
    row_of[cells] = np.arange(len(cells))
    geo.rows, geo.row_of, geo.build_s = rows, row_of, time.time() - started
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, rows=rows, row_of=row_of, build_s=geo.build_s)
    return geo


def cast(geo: Geometry, x: float, y: float, angles_deg: np.ndarray, smokes: list) -> np.ndarray:
    """Flat G*G cells seen from (x, y) px along the given rays. Walls stop a ray once it has
    crossed more than the corner tolerance; a hollow smoke stops it at its edge (in or out), a solid
    one as soon as it is inside."""
    a = np.deg2rad(angles_deg)
    dx, dy = np.cos(a), np.sin(a)
    seen = np.zeros(G * G, bool)
    live = np.ones(len(a), bool)
    hits = np.zeros(len(a), np.int16)
    started_in = [(x - s[0]) ** 2 + (y - s[1]) ** 2 < s[2] ** 2 for s in smokes]
    for step in range(0, RAY_MAX_PX, RAY_STEP_PX):
        px = x + dx * step
        py = y + dy * step
        live &= (px >= 0) & (px < PX) & (py >= 0) & (py < PX)
        pxc = np.clip(px, 0, PX - 1).astype(np.int32)
        pyc = np.clip(py, 0, PX - 1).astype(np.int32)
        if step > 3:
            hits += geo.wall[pyc, pxc]
            live &= hits <= geo.tol_hits
        for (sx, sy, r, solid), inside0 in zip(smokes, started_in):
            inside = (px - sx) ** 2 + (py - sy) ** 2 < r * r
            live &= ~inside if solid and step > 0 else inside == inside0
        if not live.any():
            break
        seen[(pyc[live] // CELL) * G + pxc[live] // CELL] = True
    return seen


def smoke_blocks(p: np.ndarray, q: np.ndarray, smoke) -> np.ndarray:
    """S x N: does the segment from each p (S x 2) to each q (N x 2) cross the smoke?"""
    sx, sy, r, solid = smoke
    dx = q[None, :, 0] - p[:, None, 0]
    dy = q[None, :, 1] - p[:, None, 1]
    fx = sx - p[:, None, 0]
    fy = sy - p[:, None, 1]
    l2 = np.maximum(dx * dx + dy * dy, 1e-6)
    tt = np.clip((fx * dx + fy * dy) / l2, 0, 1)
    hit = (fx - tt * dx) ** 2 + (fy - tt * dy) ** 2 < r * r
    if solid:
        return hit
    in_p = fx ** 2 + fy ** 2 < r * r
    in_q = (q[None, :, 0] - sx) ** 2 + (q[None, :, 1] - sy) ** 2 < r * r
    return hit & ~(in_p & in_q)


def seen_from(geo: Geometry, src: np.ndarray, smokes: list, skip: np.ndarray | None = None,
              src_xy: np.ndarray | None = None) -> np.ndarray:
    """Flat cells any of the source cells sees (their static rows), minus pairs a smoke blocks.
    `skip` (flat) marks targets that needn't be rechecked (the free space itself)."""
    src = src[geo.row_of[src] >= 0]
    if not len(src):
        return np.zeros(G * G, bool)
    packed = geo.rows[geo.row_of[src]]
    static = np.unpackbits(np.bitwise_or.reduce(packed, axis=0))[: G * G].astype(bool)
    if not smokes:
        return static
    cand = static & ~skip if skip is not None else static.copy()
    targets = np.flatnonzero(cand)
    out = static & ~cand
    if not len(targets):
        return out
    p_all = geo.centres[src] if src_xy is None else src_xy
    q = geo.centres[targets]
    seen_t = np.zeros(len(targets), bool)
    for lo in range(0, len(src), 256):
        rows = np.unpackbits(packed[lo:lo + 256], axis=1)[:, : G * G][:, targets].astype(bool)
        p = p_all[lo:lo + 256]
        for smoke in smokes:
            rows &= ~smoke_blocks(p, q, smoke)
        seen_t |= rows.any(0)
    out[targets] = seen_t
    return out


# ---------------------------------------------------------------- the round


def decode(arr):
    return np.cumsum(np.asarray(arr, dtype=np.int64))


def util_key(e: dict) -> str:
    return f"{e.get('code')}_{e.get('name')}"


@dataclass
class Watcher:
    kind: str       # trip, area, drone
    by: int
    t0: float
    t1: float
    cells: np.ndarray | None = None      # flat, for fixed watchers
    path: list | None = None
    half: float = 0.0
    range_m: float | None = None


class Round:
    def __init__(self, blob: dict, geo: Geometry):
        self.blob = blob
        self.geo = geo
        hz = blob["hz"]
        self.hz = hz
        self.side = {p["slot"]: p["side"] for p in blob["players"]}
        self.agent = {p["slot"]: p["agent"] for p in blob["players"]}
        self.tracks = {}
        for s, segs in blob["tracks"].items():
            parts = [(g["t0"] + np.arange(len(g["u"])) / hz, decode(g["u"]), decode(g["v"]),
                      np.mod(decode(g["yaw"]), 360)) for g in segs]
            self.tracks[int(s)] = tuple(np.concatenate([p[i] for p in parts]) for i in range(4))
        self.lives = {int(s): [(a, b if b is not None else math.inf) for a, b, _ in ivs]
                      for s, ivs in blob["alive"].items()}
        self.smokes, self.damage, self.watchers = [], [], []
        self.flashed, self.nearsight = defaultdict(list), defaultdict(list)
        self.downgraded, self.contest_status = defaultdict(list), defaultdict(list)
        self.missing = defaultdict(int)
        px_per_uv = PX / 10000
        util = blob["util"]
        for e in util:
            key, k = util_key(e), e["k"]
            if k == "ability" and e.get("kind") != "Projectile":
                t1 = min(x for x in (e.get("t1"), e.get("gone"), blob["t_end"]) if x is not None)
                for pat, r, solid in SMOKES:
                    if pat.match(key):
                        self.smokes.append((e["t"], t1, e["u"] * px_per_uv, e["v"] * px_per_uv,
                                            r * geo.uv_per_unit * px_per_uv, solid))
                for pat, r in DAMAGE:
                    if pat.match(key):
                        self.damage.append((e["t"], t1, e["u"] * px_per_uv, e["v"] * px_per_uv,
                                            r * geo.uv_per_unit * px_per_uv, e.get("by")))
                self._watcher(e, key, t1)
            elif k == "flash":
                pop = next((a["t"] for a in util if a["k"] == "ability" and a.get("by") == e["by"]
                            and (a.get("thrown") or {}).get("t0") == e["t"]), e["t"] + FLASH_FUSE_S)
                agent = (e.get("ability") or "").split("_")[0]
                dur = FLASH_FULL_S.get(agent, FLASH_DEFAULT_S)
                for s in e.get("targets", []):
                    self.flashed[s].append((pop, pop + dur))
                self.missing["flash hit time and blind duration (placeholder used)"] += len(e.get("targets", []))
            elif k == "nearsight":
                dur = NEARSIGHT_S.get(e.get("ability"), NEARSIGHT_DEFAULT_S)
                for s in e.get("targets", []):
                    self.nearsight[s].append((e["t"], e["t"] + dur))
                self.missing["nearsight hit time and duration (placeholder used)"] += len(e.get("targets", []))
            elif k == "reveal":
                self.downgraded[e["target"]].append((e["t"], e["t1"]))
            elif k == "status":
                if e["status"] in DOWNGRADE_STATUSES:
                    self.downgraded[e["target"]].append((e["t"], e["t1"]))
                elif e["status"] in CONTEST_STATUSES:
                    self.contest_status[e["target"]].append((e["t"], e["t1"]))
            if key.startswith("Gumshoe_E_PossessableCamera") and k == "ability":
                self.missing["Cypher camera: no in-use intervals or yaw (not modelled)"] += 1
            if key.startswith("Killjoy_") and "Turret" in key:
                self.missing["Killjoy turret: no yaw over time (not modelled)"] += 1

    def _watcher(self, e: dict, key: str, t1: float) -> None:
        px_per_uv = PX / 10000
        geo = self.geo
        by = e.get("by")
        if by is None:
            return
        if key == "Gumshoe_4_TripWire" and e.get("end"):
            a = np.array([e["u"], e["v"]]) * px_per_uv
            b = np.array(e["end"]) * px_per_uv
            n = int(np.abs(b - a).max()) + 1
            xs = np.linspace(a[0], b[0], n)
            ys = np.linspace(a[1], b[1], n)
            cells = np.unique((ys // CELL).astype(int).clip(0, G - 1) * G + (xs // CELL).astype(int).clip(0, G - 1))
            self.watchers.append(Watcher("trip", by, e["t"], t1, cells=cells))
        elif key in ("Killjoy_Q_StealthAlarmbot", "Deadeye_E_Trap"):
            r_px = (ALARMBOT_M if "Alarmbot" in key else CHAMBER_TRAP_M) / geo.m_per_px
            x, y = e["u"] * px_per_uv, e["v"] * px_per_uv
            c = int(y // CELL) * G + int(x // CELL)
            near = ((geo.centres[:, 0] - x) ** 2 + (geo.centres[:, 1] - y) ** 2) < r_px ** 2
            if geo.row_of[c] >= 0:
                row = np.unpackbits(geo.rows[geo.row_of[c]])[: G * G].astype(bool)
                self.watchers.append(Watcher("area", by, e["t"], t1, cells=np.flatnonzero(near & row)))
        elif key in DRONES and e.get("path"):
            half, rng = DRONES[key]
            self.watchers.append(Watcher("drone", by, e["t"], t1, path=e["path"], half=half, range_m=rng))

    def alive(self, s: int, t: float) -> bool:
        return any(a <= t < b for a, b in self.lives.get(s, []))

    def pos(self, s: int, t: float):
        tr = self.tracks.get(s)
        if tr is None:
            return None
        ts = tr[0]
        i = int(np.searchsorted(ts, t))
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(ts) and abs(ts[j] - t) <= 1.5 / self.hz and (best is None or abs(ts[j] - t) < abs(ts[best] - t)):
                best = j
        if best is None:
            return None
        return tr[1][best] * PX / 10000, tr[2][best] * PX / 10000, float(tr[3][best])

    def movement(self, s: int, t: float, now) -> str:
        before = self.pos(s, t - SPEED_WINDOW_S)
        if before is None:
            return "hold"
        dist_m = math.hypot(now[0] - before[0], now[1] - before[1]) * self.geo.m_per_px
        turn = abs((now[2] - before[2] + 180) % 360 - 180) / SPEED_WINDOW_S
        speed = dist_m / SPEED_WINDOW_S
        if speed > RUN_MPS or turn > FAST_TURN_DPS:
            return "run"
        return "walk" if speed >= WALK_MPS else "hold"

    @staticmethod
    def during(ivs, t) -> bool:
        return any(a <= t < b for a, b in ivs)

    def smokes_at(self, t):
        return [(x, y, r, solid) for t0, t1, x, y, r, solid in self.smokes if t0 <= t < t1]


# ---------------------------------------------------------------- one tick


@dataclass
class Holder:
    slot: int
    team: str
    cell: int
    x: float
    y: float
    active: np.ndarray       # flat G*G, after statuses
    passive: np.ndarray
    watch: np.ndarray        # own live watchers (passive)
    raw: np.ndarray          # body view before statuses, for the loss rule
    body: np.ndarray         # body view after statuses (what they can see: enemy sight of a holder)
    flagged: bool            # enemy damage area or a contesting status on them
    mode: str


@dataclass
class Fill:
    free: np.ndarray                  # G x G
    comps: list = field(default_factory=list)   # (mask G x G, seen flat, slots in it)
    seen: np.ndarray = None           # flat

    def safe(self, walk: np.ndarray) -> np.ndarray:
        return walk & ~self.seen.reshape(G, G) & ~self.free


def sector(geo: Geometry, x: float, y: float, yaw: float, half: float) -> np.ndarray:
    """Flat cells whose centre lies within yaw +/- half of (x, y), widened by the cell's own angular size."""
    dx = geo.centres[:, 0] - x
    dy = geo.centres[:, 1] - y
    dist = np.hypot(dx, dy)
    diff = np.abs((np.degrees(np.arctan2(dy, dx)) - yaw + 180) % 360 - 180)
    return (diff <= half + np.degrees(np.arctan2(CELL / 2, np.maximum(dist, 1e-3)))) | (dist < CELL)


def view_bitset(geo: Geometry, x: float, y: float, yaw: float, half: float, smokes: list) -> np.ndarray:
    c = int(y // CELL) * G + int(x // CELL)
    if geo.row_of[c] < 0:
        return np.zeros(G * G, bool)
    view = np.unpackbits(geo.rows[geo.row_of[c]])[: G * G].astype(bool) & sector(geo, x, y, yaw, half)
    if smokes and view.any():
        idx = np.flatnonzero(view)
        p = np.array([[x, y]], np.float32)
        blocked = np.zeros(len(idx), bool)
        for s in smokes:
            blocked |= smoke_blocks(p, geo.centres[idx], s)[0]
        view[idx[blocked]] = False
    return view


class Tick:
    """The inputs of one tick, and `compose` (the state) with or without a removed player."""

    def __init__(self, rnd: Round, t: float, timings: dict, vision_mode: str = "ray", compare_bitset: bool = False):
        geo = self.geo = rnd.geo
        self.rnd, self.t = rnd, t
        self.smokes = rnd.smokes_at(t)
        started = time.perf_counter()
        self.holders: dict[int, Holder] = {}
        self.vision_diff = []
        drones_by = {}
        for w in rnd.watchers:
            if w.kind == "drone" and w.t0 <= t < w.t1 and rnd.alive(w.by, t):
                drones_by[w.by] = w
        bitset_s = 0.0
        for s in sorted(rnd.side):
            if not rnd.alive(s, t):
                continue
            p = rnd.pos(s, t)
            if p is None:
                continue
            x, y, yaw = p
            cell = int(min(y, PX - 1) // CELL) * G + int(min(x, PX - 1) // CELL)
            mode = rnd.movement(s, t, p)
            angles = yaw + np.arange(-FOV_HALF, FOV_HALF + 1e-9, RAY_STEP_DEG)
            if vision_mode == "ray":
                raw = cast(geo, x, y, angles, self.smokes)
            else:
                raw = view_bitset(geo, x, y, yaw, FOV_HALF, self.smokes)
            if compare_bitset:
                b0 = time.perf_counter()
                other = view_bitset(geo, x, y, yaw, FOV_HALF, self.smokes) if vision_mode == "ray" else \
                    cast(geo, x, y, angles, self.smokes)
                bitset_s += time.perf_counter() - b0
                union = (raw | other).sum()
                self.vision_diff.append(((raw ^ other).sum() / union) if union else 0.0)
            if s in drones_by:
                raw = np.zeros(G * G, bool)  # flying a drone: their own view doesn't count
            body = raw.copy()
            if rnd.during(rnd.flashed[s], t):
                body[:] = False
            elif rnd.during(rnd.nearsight[s], t):
                r = NEARSIGHT_RADIUS_M / geo.m_per_px
                body &= ((geo.centres[:, 0] - x) ** 2 + (geo.centres[:, 1] - y) ** 2) < r * r
            cone = sector(geo, x, y, yaw, CONE_HALF[mode])
            if rnd.during(rnd.downgraded[s], t):
                active = np.zeros(G * G, bool)
            else:
                active = body & cone
            passive = body & ~active
            watch = np.zeros(G * G, bool)
            for w in rnd.watchers:
                if w.by != s or not (w.t0 <= t < w.t1):
                    continue
                if w.kind in ("trip", "area"):
                    watch[w.cells] = True
                elif w.kind == "drone":
                    at = path_at(w.path, t)
                    if at is None:
                        continue
                    dx_, dy_, heading = at
                    dv = cast(geo, dx_, dy_, heading + np.arange(-w.half, w.half + 1e-9, RAY_STEP_DEG), self.smokes)
                    if w.range_m:
                        r = w.range_m / geo.m_per_px
                        dv &= ((geo.centres[:, 0] - dx_) ** 2 + (geo.centres[:, 1] - dy_) ** 2) < r * r
                    watch |= dv
            enemy_side = "B" if rnd.side[s] == "A" else "A"
            flagged = rnd.during(rnd.contest_status[s], t) or any(
                t0 <= t < t1 and (x - dx_) ** 2 + (y - dy_) ** 2 < r * r and rnd.side.get(by) == enemy_side
                for t0, t1, dx_, dy_, r, by in rnd.damage)
            self.holders[s] = Holder(s, rnd.side[s], cell, x, y, active, passive, watch, raw, body, flagged, mode)
        timings["vision"] += time.perf_counter() - started - bitset_s
        timings["vision_other"] += bitset_s
        # enemy sight of each holder: sees[e] = set of holders e's body view reaches
        self.sees = {e.slot: {h.slot for h in self.holders.values() if h.team != e.team and e.body[h.cell]}
                     for e in self.holders.values()}
        self._dist: dict[int, np.ndarray] = {}

    # --- building blocks

    def team(self, side: str, removed: int | None) -> list[Holder]:
        return [h for h in self.holders.values() if h.team == side and h.slot != removed]

    def claims(self, side: str, removed: int | None):
        hs = self.team(side, removed)
        z = np.zeros(G * G, bool)
        active, passive, raw = z.copy(), z.copy(), z.copy()
        for h in hs:
            active |= h.active
            passive |= h.passive | h.watch
            raw |= h.raw | h.watch
        passive &= ~active
        return active, passive, active | passive, raw

    def fill(self, side: str, blocked_by: np.ndarray, removed: int | None, smoke_mode: str = "frontier") -> Fill | None:
        """`side`'s free space through walkable cells `blocked_by` (the other team's watched cells) misses."""
        hs = self.team(side, removed)
        if not hs:
            return None
        walk = self.geo.walk
        watched = blocked_by.reshape(G, G)
        open_ = walk & ~watched
        for h in hs:
            open_.flat[h.cell] = True
        lab, _ = ndimage.label(open_)
        free = np.zeros((G, G), bool)
        comps = []
        by_label = defaultdict(list)
        for h in hs:
            by_label[lab.flat[h.cell]].append(h.slot)
        for lbl, slots in by_label.items():
            mask = lab == lbl
            free |= mask
            comps.append((mask, self.comp_seen(mask, watched, smoke_mode), slots))
        seen = np.zeros(G * G, bool)
        for _, s, _ in comps:
            seen |= s
        return Fill(free, comps, seen)

    def comp_seen(self, mask: np.ndarray, watched: np.ndarray, smoke_mode: str) -> np.ndarray:
        walk = self.geo.walk
        if smoke_mode == "exact":
            src = np.flatnonzero(mask)
            return seen_from(self.geo, src, self.smokes, skip=mask.ravel())
        edge = mask & ndimage.binary_dilation(watched & walk, EIGHT)
        if smoke_mode == "boundary":
            # Every free cell next to anything not free (the team's vision, walls, other pockets). The
            # frontier sees most of it cheaply; the whole boundary rechecks only the targets it missed.
            first = seen_from(self.geo, np.flatnonzero(edge), self.smokes, skip=mask.ravel())
            rim = mask & ndimage.binary_dilation(~mask, EIGHT) & ~edge
            return first | seen_from(self.geo, np.flatnonzero(rim), self.smokes, skip=mask.ravel() | first)
        if smoke_mode == "static":
            return seen_from(self.geo, np.flatnonzero(mask), [])
        if smoke_mode == "literal":
            interior = mask & ~edge
            return seen_from(self.geo, np.flatnonzero(edge), self.smokes, skip=mask.ravel()) | \
                seen_from(self.geo, np.flatnonzero(interior), [])
        src = np.flatnonzero(edge)
        if not len(src):  # an enclosed pocket with no team vision next to it sees only walls' worth
            src = np.flatnonzero(mask)
        return seen_from(self.geo, src, self.smokes, skip=mask.ravel())

    def dist_from(self, h: Holder) -> np.ndarray:
        """Walking distance (8-connected cells) from a holder, cached for the tick."""
        if h.slot not in self._dist:
            walk = self.geo.walk
            dist = np.full((G, G), -1, np.int32)
            front = np.zeros((G, G), bool)
            front.flat[h.cell] = True
            seen = front.copy()
            d = 0
            while front.any():
                dist[front] = d
                d += 1
                nxt = ndimage.binary_dilation(front, EIGHT) & walk & ~seen
                seen |= nxt
                front = nxt
            self._dist[h.slot] = dist
        return self._dist[h.slot]

    def way_back(self, e: Holder, target: np.ndarray, watched_other: np.ndarray) -> np.ndarray:
        dist = self.dist_from(e)
        reach = target & (dist >= 0)
        if not reach.any():
            return np.zeros(G * G, bool)
        d = np.where(reach, dist, np.iinfo(np.int32).max)
        cur = np.unravel_index(int(np.argmin(d)), d.shape)
        path = np.zeros((G, G), bool)
        path[cur] = True
        while dist[cur] > 0:
            cy, cx = cur
            best = None
            for oy in (-1, 0, 1):
                for ox in (-1, 0, 1):
                    ny, nx = cy + oy, cx + ox
                    if 0 <= ny < G and 0 <= nx < G and dist[ny, nx] == dist[cur] - 1:
                        best = (ny, nx)
                        break
                if best:
                    break
            cur = best
            path[cur] = True
        width = max(1, round(WAY_BACK_WIDTH_M / self.geo.cell_m))
        return (ndimage.binary_dilation(path, EIGHT, iterations=width).ravel() & watched_other)

    # --- the state

    def compose(self, removed: int | None = None, base: dict | None = None, full: bool = True,
                smoke_mode: str = "frontier", stats: dict | None = None) -> dict:
        """The tick's cell states. `removed` drops one player (the counterfactual). With `base` and
        not `full`, fills are reused or extended from the base instead of recomputed."""
        geo, walk = self.geo, self.geo.walk
        cl = {s: self.claims(s, removed) for s in ("A", "B")}
        fills = {}
        rside = self.holders[removed].team if removed is not None else None
        for side in ("A", "B"):
            other = "B" if side == "A" else "A"
            if base is None or full:
                fills[side] = self.fill(side, cl[other][2], removed, smoke_mode)
                continue
            old = base["fills"][side]
            if side == rside:
                # the removed player's own fill loses their source: drop their component unless shared
                if old is None:
                    fills[side] = None
                    continue
                if base["cl"][other][2][self.holders[removed].cell]:
                    # their cell was forced open inside the other team's vision; it may have bridged the fill
                    fills[side] = self.fill(side, cl[other][2], removed, smoke_mode)
                    stats["own_fill_bridge_recomputed"] += 1
                    continue
                keep = [(m, s, [x for x in slots if x != removed]) for m, s, slots in old.comps]
                keep = [c for c in keep if c[2]]
                if not keep:
                    fills[side] = None
                    stats["own_fill_empty"] += 1
                    continue
                if len(keep) == len(old.comps):
                    fills[side] = old
                    stats["own_fill_same"] += 1
                else:
                    free = np.zeros((G, G), bool)
                    seen = np.zeros(G * G, bool)
                    for m, s, _ in keep:
                        free |= m
                        seen |= s
                    fills[side] = Fill(free, keep, seen)
                    stats["own_fill_dropped_comp"] += 1
            else:
                # the other team's fill: cells only the removed player watched open up
                if old is None:
                    fills[side] = None
                    continue
                opened = (base["cl"][other][2] & ~cl[other][2]).reshape(G, G)
                if not (opened & ndimage.binary_dilation(old.free, EIGHT)).any():
                    fills[side] = old
                    stats["enemy_fill_same"] += 1
                    continue
                watched = cl[other][2].reshape(G, G)
                open_ = walk & ~watched
                slots = [h for h in self.team(side, removed)]
                for h in slots:
                    open_.flat[h.cell] = True
                lab, _ = ndimage.label(open_)
                labels = {lab.flat[h.cell] for h in slots}
                free = np.isin(lab, list(labels))
                added = free & ~old.free
                edge = added & ndimage.binary_dilation((watched & walk) if smoke_mode == "frontier" else ~free, EIGHT)
                seen = old.seen | seen_from(geo, np.flatnonzero(edge), self.smokes, skip=free.ravel())
                fills[side] = Fill(free, [], seen)
                stats["enemy_fill_extended"] += 1
        level = {}
        for side in ("A", "B"):
            other = "B" if side == "A" else "A"
            active, passive, _, _ = cl[side]
            lv = np.zeros(G * G, np.int8)
            lv[passive] = 1
            f_enemy = fills[other]
            safe = walk.ravel() if f_enemy is None else f_enemy.safe(walk).ravel()
            lv[safe] = np.maximum(lv[safe], 2)
            lv[active] = 3
            lv[~walk.ravel()] = 0
            level[side] = lv
        both = (level["A"] > 0) & (level["B"] > 0)
        contested = both.copy()
        extra = {"holder": 0, "way_back": 0, "loss": 0}
        for side in ("A", "B"):
            other = "B" if side == "A" else "A"
            hs = self.team(side, removed)
            is_contested = {h.slot: h.flagged or any(e in self.sees and h.slot in self.sees[e]
                                                     for e in (x.slot for x in self.team(other, removed)))
                            for h in hs}
            steady = np.zeros(G * G, bool)
            fought = np.zeros(G * G, bool)
            for h in hs:
                steady |= h.watch
                if is_contested[h.slot]:
                    fought |= h.active | h.passive
                else:
                    steady |= h.active | h.passive
            held = fought & ~steady
            extra["holder"] += int((held & ~contested).sum())
            contested |= held
            # the entry's way back (Q56): `other`'s players standing in `side`'s vision
            watched = cl[side][2]
            f_other = fills[other]
            for e in self.team(other, removed):
                if not watched[e.cell]:
                    continue
                target = np.zeros((G, G), bool)
                if f_other is not None:
                    for m, _, slots in (f_other.comps or [(f_other.free, None, [x.slot for x in self.team(other, removed)])]):
                        if any(x != e.slot for x in slots):
                            target |= m
                if not target.any():
                    for x in self.team(other, removed):
                        if x.slot != e.slot:
                            target.flat[x.cell] = True
                if target.any():
                    wb = self.way_back(e, target, watched)
                    extra["way_back"] += int((wb & ~contested).sum())
                    contested |= wb
            # cells lost to a status (Q55): nobody else claims them, so they are fought over
            lost = cl[side][3] & ~cl[side][2] & (level[side] == 0) & (level[other] == 0)
            extra["loss"] += int((lost & ~contested).sum())
            contested |= lost
        contested &= walk.ravel()
        any_active = (level["A"] == 3) | (level["B"] == 3)
        state = np.zeros(G * G, np.uint8)
        state[level["A"] > 0] = level["A"][level["A"] > 0]
        state[level["B"] > 0] = 3 + level["B"][level["B"] > 0]
        state[contested] = np.where(any_active[contested], CONTESTED_ACTIVE, CONTESTED)
        return {"state": state, "fills": fills, "cl": cl, "extra": extra}

    def coverage(self) -> dict[int, tuple[float, float, np.ndarray]]:
        """Per player: active and passive cells, shared cells split evenly (Q60), and the mask."""
        out = {}
        for side in ("A", "B"):
            hs = self.team(side, None)
            n_act = sum(h.active.astype(np.int8) for h in hs) if hs else None
            n_pas = sum((h.passive | h.watch).astype(np.int8) for h in hs) if hs else None
            for h in hs:
                pas = h.passive | h.watch
                act = float((1 / n_act[h.active]).sum()) if h.active.any() else 0.0
                psv = float((1 / n_pas[pas]).sum()) if pas.any() else 0.0
                out[h.slot] = (act, psv, h.active | pas)
        return out


def path_at(path: list, t: float):
    if not path or t < path[0][0] or t > path[-1][0]:
        return None
    for i in range(1, len(path)):
        if path[i][0] >= t:
            p, q = path[i - 1], path[i]
            f = (t - p[0]) / (q[0] - p[0]) if q[0] > p[0] else 0
            u = p[1] + (q[1] - p[1]) * f
            v = p[2] + (q[2] - p[2]) * f
            heading = math.degrees(math.atan2(q[2] - p[2], q[1] - p[1])) if (q[1], q[2]) != (p[1], p[2]) else 0.0
            return u * PX / 10000, v * PX / 10000, heading
    return None


def score(state: np.ndarray, side: str) -> np.ndarray:
    ours = (state >= 1) & (state <= 3) if side == "A" else (state >= 4) & (state <= 6)
    theirs = (state >= 4) & (state <= 6) if side == "A" else (state >= 1) & (state <= 3)
    return ours.astype(np.int8) - theirs.astype(np.int8)


# ---------------------------------------------------------------- the run


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("blob", type=Path)
    ap.add_argument("round")
    ap.add_argument("--every", type=int, default=1, help="use every Nth 16 Hz tick (quick runs)")
    ap.add_argument("--smoke-every", type=int, default=8, help="exact smoke recheck on every Nth smoked tick")
    ap.add_argument("--vision", choices=("ray", "bitset"), default="ray")
    ap.add_argument("--safe-sources", choices=("frontier", "boundary"), default="frontier",
                    help="which free cells Safe is seen from: next to the team's vision (Q70), or next to anything")
    args = ap.parse_args()
    data = json.loads(args.blob.read_text())
    blob = data["rounds"][args.round]
    name = data["map"]
    geo = load_geometry(args.out, name)
    print(f"{name}: {int(geo.walk.sum())} walkable cells, visibility {geo.build_s:.0f}s "
          f"{geo.rows.nbytes / 1e6:.1f} MB, corner tolerance {geo.tol_hits} sample(s)", flush=True)
    rnd = Round(blob, geo)
    walk_flat = geo.walk.ravel()
    n_walk = int(walk_flat.sum())
    ticks = np.arange(0, blob["t_end"], 1 / HZ)[:: args.every]
    timings = defaultdict(float)
    per_player = []   # (tick, slot, incremental ms, branch)
    stats = defaultdict(int)
    states, control, coverage_masks, control_masks = [], [], [], []
    cf_mismatch = []
    smoke_rows = []
    vision_diffs = []
    slots = sorted(rnd.side)
    smoked_seen = 0
    extra_totals = defaultdict(int)
    for n, t in enumerate(ticks):
        tick = Tick(rnd, float(t), timings, args.vision, compare_bitset=(n % 4 == 0))
        vision_diffs.extend(tick.vision_diff)
        t0 = time.perf_counter()
        base = tick.compose(smoke_mode=args.safe_sources)
        for k, v in base["extra"].items():
            extra_totals[k] += v
        t1 = time.perf_counter()
        cov = tick.coverage()
        t2 = time.perf_counter()
        timings["base"] += t1 - t0
        timings["coverage"] += t2 - t1
        state = base["state"]
        states.append(state[walk_flat])
        cov_row, ctl_row, ctl_masks = [], [], []
        for s in slots:
            c = cov.get(s)
            cov_row.append(c[2][walk_flat] if c else np.zeros(n_walk, bool))
        for s in slots:
            if s not in tick.holders:
                ctl_row.append((np.nan, np.nan, 0.0, 0.0))
                ctl_masks.append(np.zeros(n_walk, bool))
                continue
            side = tick.holders[s].team
            a = time.perf_counter()
            full = tick.compose(removed=s, smoke_mode=args.safe_sources)
            b = time.perf_counter()
            branch_before = dict(stats)
            inc = tick.compose(removed=s, base=base, full=False, smoke_mode=args.safe_sources, stats=stats)
            c_ = time.perf_counter()
            branch = [k for k in stats if stats[k] != branch_before.get(k, 0)]
            timings["cf_full"] += b - a
            timings["cf_incremental"] += c_ - b
            per_player.append((n, s, (c_ - b) * 1000, (b - a) * 1000, "+".join(sorted(branch))))
            base_score = score(state, side)
            last = len(tick.team(side, s)) == 0
            values = []
            for cf in (full, inc):
                if last:  # Q63: no terminal flip; the last player controls only what the team loses
                    values.append(float((base_score > 0).sum()))
                else:
                    values.append(float(base_score.sum() - score(cf["state"], side).sum()))
            drop = base_score - score(inc["state"], side) if not last else (base_score > 0).astype(np.int8)
            mask = drop > 0
            ctl_masks.append(mask[walk_flat])
            ctl_row.append((values[0], values[1], cov[s][0], cov[s][1]))
            diff = int((full["state"] != inc["state"]).sum())
            cf_mismatch.append(diff)
        control.append(ctl_row)
        coverage_masks.append(np.stack(cov_row))
        control_masks.append(np.stack(ctl_masks))
        # Q70: frontier-only against the exact recheck, on sampled smoked ticks
        if tick.smokes:
            smoked_seen += 1
            if smoked_seen % args.smoke_every == 1 or args.smoke_every == 1:
                row = {"t": round(float(t), 3), "smokes": len(tick.smokes)}
                safes = {}
                for mode in ("frontier", "boundary", "exact", "literal", "static"):
                    a = time.perf_counter()
                    cmp_ = tick.compose(smoke_mode=mode)
                    row[f"{mode}_ms"] = round((time.perf_counter() - a) * 1000, 1)
                    safes[mode] = cmp_
                for mode in ("frontier", "boundary", "literal", "static"):
                    row[f"{mode}_state_diff"] = int((safes[mode]["state"] != safes["exact"]["state"]).sum())
                smoke_rows.append(row)
        if n % 80 == 0:
            done = n + 1
            print(f"t={t:6.2f} ({done}/{len(ticks)}) vision {timings['vision'] / done * 1000:.0f} base "
                  f"{timings['base'] / done * 1000:.0f} full {timings['cf_full'] / done * 1000:.0f} inc "
                  f"{timings['cf_incremental'] / done * 1000:.0f} ms/tick; peak {peak_memory_mb():.0f} MB", flush=True)

    out = args.out / "engine"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{name}_r{args.round}_{args.safe_sources}_every{args.every}"
    np.savez_compressed(out / f"{stem}.npz", states=np.stack(states), coverage=np.stack(coverage_masks),
                        control_masks=np.stack(control_masks), control=np.array(control, np.float32),
                        walk=geo.walk, ticks=ticks, slots=np.array(slots))
    nt = len(ticks)
    inc_ms = np.array([p[2] for p in per_player])
    full_ms = np.array([p[3] for p in per_player])
    worst = sorted(per_player, key=lambda p: -p[2])[:8]
    live_mask = (ticks >= blob["t_start"]) & (ticks < (blob["t_decided"] or blob["t_end"]))
    st = np.stack(states)
    share = {k: float((st == v).mean()) for k, v in
             (("none", NONE), ("contested_active", CONTESTED_ACTIVE), ("contested", CONTESTED))}
    share.update({"A": float(((st >= 1) & (st <= 3)).mean()), "B": float(((st >= 4) & (st <= 6)).mean())})
    ctl = np.array(control, np.float64)
    result = {
        "map": name, "round": args.round, "uuid": data["uuid"], "hz": HZ / args.every, "ticks": nt,
        "round_seconds": round(float(blob["t_end"]), 1), "live_seconds": round(float(live_mask.sum()) / HZ * args.every, 1),
        "walkable_cells": n_walk, "cell_m": round(geo.cell_m, 3), "vision_mode": args.vision,
        "visibility_build_s": round(geo.build_s, 1), "visibility_mb": round(geo.rows.nbytes / 1e6, 1),
        "corner_tolerance_samples": geo.tol_hits,
        "ms_per_tick": {k: round(v / nt * 1000, 2) for k, v in timings.items()},
        "cf_per_player_ms": {"incremental_mean": round(float(inc_ms.mean()), 2),
                             "incremental_p95": round(float(np.percentile(inc_ms, 95)), 2),
                             "incremental_max": round(float(inc_ms.max()), 2),
                             "full_mean": round(float(full_ms.mean()), 2),
                             "full_max": round(float(full_ms.max()), 2)},
        "cf_branches": dict(stats),
        "cf_worst": [{"t": round(float(ticks[p[0]]), 3), "slot": p[1], "incremental_ms": round(p[2], 1),
                      "full_ms": round(p[3], 1), "branch": p[4]} for p in worst],
        "cf_incremental_vs_full": {"player_ticks": len(cf_mismatch),
                                   "identical": int(sum(d == 0 for d in cf_mismatch)),
                                   "mean_cells_differing": round(float(np.mean(cf_mismatch)), 3),
                                   "max_cells_differing": int(max(cf_mismatch)),
                                   "control_abs_diff_mean": round(float(np.nanmean(np.abs(ctl[..., 0] - ctl[..., 1]))), 3)},
        "vision_bitset_vs_ray": {"samples": len(vision_diffs),
                                 "mean_share_differing": round(float(np.mean(vision_diffs)), 4) if vision_diffs else None,
                                 "p95_share_differing": round(float(np.percentile(vision_diffs, 95)), 4) if vision_diffs else None},
        "smoke_check": smoke_rows,
        "state_share": share,
        "safe_sources": args.safe_sources,
        "contest_extra_cells_per_tick": {k: round(v / nt, 1) for k, v in extra_totals.items()},
        "missing_inputs": dict(rnd.missing),
        "peak_memory_mb": round(peak_memory_mb(), 0),
    }
    (out / f"{stem}.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k not in ("smoke_check", "cf_worst")}, indent=1))


if __name__ == "__main__":
    main()
