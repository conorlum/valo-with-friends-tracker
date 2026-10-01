"""Map control for one replay round (docs/replay-map-control-plan.md; Stage 2).

A round blob (format.py) and its link data in; per-tick cell states, per-player control and
coverage, per-section totals and per-player stats out. Ported from the Stage 0b prototype
(`scripts/control_feasibility/engine_proto.py`) with the rules settled after it:

- **Ticks (Q75).** Every TICK_STEP_S, plus a tick at each event, all on the 1/GRID_HZ s grid:
  deaths, each ability's placement, throw and end, flash and nearsight hits (and their ends), status
  and reveal starts and ends, and shots (at most one per SHOT_WINDOW_S). A death gets two ticks: the
  last grid point before it (the dying player is alive there, so their lost control is exact) and
  the first at or after it (the state without them). Status and utility intervals are snapped to
  the grid too. Integrals weight each tick by the time to the next one, clipped at the live round's
  and each heatmap section's edges.
- **Vision.** Each alive player's 103-degree view, raycast on the sight mask with smokes (hollow or
  solid), Viper's wall while it is up (its laid line; no corner tolerance) and the Q72 corner
  tolerance (geometry.py). The held cone's width follows the
  movement over SPEED_WINDOW_S. Flashed: nothing. Nearsighted: a bubble. Concussed, stunned or
  revealed: all of it passive (Q42, Q57).
- **Memory (D6).** Ground a player saw and looked away from stays theirs as passive control until
  the team's unknown reaches it (2026-10-01; it replaced decay from open ground). Memory dies with
  its player. When the buy-phase barriers drop, each team remembers its side of them (the barrier
  paint).
- **Backfill.** Ground behind a player's watched line, back to the team's control, that no enemy can
  walk into without crossing the team's claims (and the enemy doesn't claim), is passive control of
  the player nearest it from their live view (`Tick.backfill`). Recomputed each tick, never remembered;
  where it is already the team's Safe ground it credits nobody (coverage), as before. Never on a cell
  in the team's unknown.
- **Unknown (2026-10-01; docs/map-control-unknown-plan.md).** Each team's unknown is where an enemy
  could be: pushed out by the enemy's live players, the enemy's side of the barriers at the drop, and
  its own spread at UNKNOWN_MPS (8-connected, across specials, through smokes). The team's live
  control clears it on contact.
- **Watchers** (passive): trips, alarmbots, Chamber traps, Killjoy's turret, Cypher's camera while
  he is in it, and flown drones; a camera or drone in use replaces its owner's own view. Placed
  utility dies with its owner (Q71): a watcher counts only while its owner is alive.
- **Safe (Q73; 2026-10-01).** A team's Safe ground is what no cell of its unknown sees, smoke-aware,
  from the unknown's boundary. A tick built without unknown (tests) falls back to the instant flood:
  each team's free space from its alive players through walkable cells the other team doesn't watch
  (map specials link cells); the other team's Safe is what no free cell sees. Seen from the fill's boundary, smoke-aware: the frontier (next to the team's
  vision) first, then the rest of the boundary for the targets it missed.
- **Contests.** Both teams claiming a cell (Q40, Q55); a holder an enemy sees, stands in enemy
  damage utility, took a wallbang, or has a contesting status (their cells, unless steady cover
  also holds them); the entry's way back (Q56); cells a team lost to a status (Q55).
- **Control (Q49, Q54, Q61-Q63).** The signed drop in the team's score (ours +1, contested and
  nobody 0, theirs -1) when one player is removed, both teams recomputed. The incremental method
  reuses the base tick's fills where it can; a removed player who stood in enemy vision may have
  bridged their own team's fill, so that fill is recomputed (the 0b bridge fix).
  `full_every` also runs the full recompute, to measure the difference.
- **Stats (Q50, Q66)** count the live round only (`t_start` to `t_decided`).

Inputs the condenser added in revision 10 (flash/nearsight `hits`, `possessed`, `yaws`, `damage`
rows) are used when present; older blobs fall back to 0b's placeholders, counted in
`missing_inputs`. Local tooling only: the web app never imports this module.
"""

from __future__ import annotations

import copy
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace

import numpy as np
from scipy import ndimage

from app.control.geometry import CELL, GRID, PX, RAY_STEP_DEG, Geometry, Wall, cast, visibility, wall_blocks
from app.replays.control_format import CONTROL_REVISION  # noqa: F401 - stdlib-only, so the web app can read it

TICK_STEP_S = 0.5
GRID_HZ = 16
SHOT_WINDOW_S = 0.25
SECTION_S = 10.0

FOV_HALF = 51.5
RUN_MPS, WALK_MPS = 5.0, 1.5
# "What the team knew" (docs/map-control-team-knew-plan.md; D7, approved): an enemy the team isn't
# seeing could be anywhere it could have run to since (at this speed) through ground the team doesn't
# watch; a just-lost enemy keeps their last view as passive for KNEW_FADE_S.
KNEW_RUN_MPS = 6.75
KNEW_FADE_S = 3.0
# Unknown (docs/map-control-unknown-plan.md, 2026-10-01): where an enemy of a team could be. It spreads
# at Valorant's shift-walk, the speed an enemy can move without being heard.
UNKNOWN_MPS = 3.5
# Presence (the user's call, 2026-10-01): an enemy can't walk past a player within arm's reach unseen, so
# each live player holds the walkable ground within this radius as passive (not while flashed).
PRESENCE_M = 2.0
CONE_HALF = {"run": 2.0, "walk": 5.0, "hold": 10.0}
FAST_TURN_DPS = 90.0
SPEED_WINDOW_S = 0.25
NEARSIGHT_RADIUS_M = 4.0       # the plan's 8 m-wide bubble
WAY_BACK_WIDTH_M = 2.0
WALLBANG_CONTEST_S = 2.0       # per wallbang hit (the plan)
TURRET_HALF = 50.0             # 100 degrees since patch 8.0
ALARMBOT_M = 5.5
CHAMBER_TRAP_M = 7.0
# A DB-only death within this long before a replay death of the same slot is that death, not
# another: services/replay_view.py's SAME_DEATH_S (repeated so the engine doesn't import the DB models).
SAME_DEATH_S = 1.0

# Placeholders for blobs from before revision 10 (plan: "Inputs the blob lacks"). A flash row's `t`
# is the cast, not the hit, and the per-target blind time was dropped by the condenser.
FLASH_FULL_S = {"phoenix": 1.5, "yoru": 1.5, "breach": 2.25, "kayo": 2.25, "skye": 2.25}
FLASH_DEFAULT_S = 1.5
FLASH_FUSE_S = 0.5
NEARSIGHT_S = {"omen_paranoia": 2.0}
NEARSIGHT_DEFAULT_S = 1.0      # also a hit with no configured duration (Reyna's Leer)
# How long one enemy ability-damage hit keeps its victim contested past the hit.
DAMAGE_CONTEST_PAD_S = 0.5

# `<code>_<name>` -> (radius in world units, solid). Radii are the viewer's (replay.js ABILITY_STYLES).
SMOKES = [(re.compile(p), r, solid) for p, r, solid in [
    (r"^Wraith_4_Smoke$", 410, False), (r"^Smonk_NewSmoke(_PDS)?$", 410, False), (r"^Gumshoe_Q_Cage$", 330, False),
    (r"^Rift_E_SmokeZone$", 475, False), (r"^Pandemic_4_SmokeZone$", 450, False),
    (r"^Pandemic_X_Circular$", 900, False), (r"^Wushu_4_SmokeZone$", 335, False),
    (r"^Sarge_4_Smoke_Production", 415, True), (r"^Iris_E_Smoke$", 400, False)]]
DAMAGE_ZONES = [(re.compile(p), r) for p, r in [
    (r"^Phoenix_MolotovFire$", 450), (r"^Sarge_Q_Molotov_Production$", 450),
    (r"^Pandemic_AcidMolotov_NewMolotov$", 450), (r"^Aggrobot_C_ExplodeyPatch$", 450),
    (r"^Killjoy_4_BeeSwarm_Damage$", 450), (r"^Hunter_4_ExplosiveBolt_Explosion$", 350),
    (r"^Clay_Q_Explosion$", 450), (r"^Cashew_X_Segment$", 350), (r"^Sarge_X_OrbitalStrike", 900),
    (r"^Cashew_E_Explosion$", 400)]]
# Flown drones: (cone half-angle, range in m or None).
DRONES = {"Hunter_E_Drone": (FOV_HALF, None), "Guide_Q_PossessableScout": (45.0, 22.5)}
VIPER_WALL = "Pandemic_E_SmokeScreenManager"   # the condenser's `points` and `on` (extras.py)
CAMERA = "Gumshoe_E_PossessableCamera"
TURRET = "Killjoy_E_Turret"
TRIPWIRE = "Gumshoe_4_TripWire"
AREA_TRIPS = {"Killjoy_Q_StealthAlarmbot": ALARMBOT_M, "Deadeye_E_Trap": CHAMBER_TRAP_M}
CONTEST_STATUSES = {"slowed", "tethered", "hindered", "fragile", "suppressed", "decay", "decayed"}
DOWNGRADE_STATUSES = {"concussed", "stunned"}

# Cell state codes (one per walkable cell per tick), in side-group terms (A/B, the blob's `side`).
NONE, A_PASSIVE, A_SAFE, A_ACTIVE, B_PASSIVE, B_SAFE, B_ACTIVE, CONTESTED_ACTIVE, CONTESTED = range(9)
N_STATES = 9
EIGHT = np.ones((3, 3), bool)


class ControlError(ValueError):
    pass


@dataclass(frozen=True)
class ControlLink:
    """What control needs from the replay's link: each slot's side this round ("attack" or
    "defense"), and the deaths only the DB knows (spike, fall, self and team kills) as
    (slot, time on the replay clock)."""
    sides: dict = field(default_factory=dict)
    db_deaths: tuple = ()


def snap(t: float) -> float:
    return round(t * GRID_HZ) / GRID_HZ


def snap_before(t: float) -> float:
    """The last grid point strictly before t."""
    return (math.ceil(t * GRID_HZ - 1e-9) - 1) / GRID_HZ


def snap_after(t: float) -> float:
    """The first grid point at or after t."""
    return math.ceil(t * GRID_HZ - 1e-9) / GRID_HZ


def _span(a: float, b: float) -> tuple[float, float]:
    """An interval snapped to the grid, at least one grid step long, so it holds its own tick."""
    a, b = snap(a), snap(b)
    return (a, b) if b > a else (a, a + 1 / GRID_HZ)


def util_key(e: dict) -> str:
    return f"{e.get('code')}_{e.get('name')}"


def _decode(arr) -> np.ndarray:
    return np.cumsum(np.asarray(arr, dtype=np.int64))


def _step_at(points, t: float, default=None):
    """The value of a [[t, value], ...] series at t: the last one at or before t."""
    value = default
    for tt, v in points or []:
        if tt > t:
            break
        value = v
    return value


def _during(ivs, t: float) -> bool:
    return any(a <= t < b for a, b in ivs)


# ---------------------------------------------------------------- the round


@dataclass
class Watcher:
    kind: str             # trip, area, drone, camera, turret
    by: int
    t0: float
    t1: float
    cells: np.ndarray | None = None      # flat, for fixed watchers
    path: list | None = None
    x: float = 0.0
    y: float = 0.0
    yaw: float | None = None
    yaws: list | None = None
    in_use: list | None = None           # [(t0, t1)] a camera or drone is possessed/flown
    half: float = 0.0
    range_m: float | None = None


class RoundInputs:
    """One round's players, lives, positions, statuses and utility, with every interval snapped
    to the tick grid."""

    def __init__(self, blob: dict, geo: Geometry, link: ControlLink | None = None):
        link = link or ControlLink()
        self.blob, self.geo, self.link = blob, geo, link
        self.hz = blob["hz"]
        self.t_start = float(blob.get("t_start") or 0.0)
        self.t_end = float(blob["t_end"])
        self.t_decided = float(blob["t_decided"]) if blob.get("t_decided") is not None else self.t_end
        self.team: dict[int, str] = {}
        for p in blob["players"]:
            group = p.get("side")
            if group is None:
                side = link.sides.get(p["slot"])
                group = {"attack": "A", "defense": "B"}.get(side)
            if group is None:
                raise ControlError(f"slot {p['slot']} has no side group and the link names no side")
            self.team[p["slot"]] = group
        self.group_side = {}
        for slot, side in link.sides.items():
            if slot in self.team:
                self.group_side.setdefault(self.team[slot], side)
        self.tracks = {}
        for s, segs in (blob.get("tracks") or {}).items():
            parts = [(g["t0"] + np.arange(len(g["u"])) / self.hz, _decode(g["u"]), _decode(g["v"]),
                      np.mod(_decode(g["yaw"]), 360)) for g in segs]
            self.tracks[int(s)] = tuple(np.concatenate([p[i] for p in parts]) for i in range(4))
        self.lives = self._lives()
        self.smokes, self.damage_zones, self.watchers = [], [], []
        self.walls: list[tuple[float, float, Wall]] = []
        self.flashed, self.nearsight = defaultdict(list), defaultdict(list)
        self.downgraded, self.contest_status = defaultdict(list), defaultdict(list)
        self.hit_contest = defaultdict(list)
        self.missing: dict[str, int] = defaultdict(int)
        self.events: list[float] = []
        self.shot_times: list[float] = []
        self.plant: float | None = None
        self.reveals: list[tuple[float, float, int | None, int]] = []   # (t0, t1, by, target)
        self._read_util()

    # --- lives

    def _lives(self) -> dict[int, list[tuple[float, float]]]:
        lives: dict[int, list[list[float]]] = {}
        for s, ivs in (self.blob.get("alive") or {}).items():
            lives[int(s)] = [[iv[0], iv[1] if iv[1] is not None else math.inf] for iv in ivs]
        for slot, t in self.link.db_deaths:
            for life in lives.get(slot, []):
                start, end = life
                if start <= t and t < end - SAME_DEATH_S:
                    life[1] = t
                    break
        return {s: [(a, b) for a, b in ivs] for s, ivs in lives.items()}

    def alive(self, s: int, t: float) -> bool:
        return any(a <= t < b for a, b in self.lives.get(s, []))

    def life_end(self, s: int, t: float) -> float:
        return next((b for a, b in self.lives.get(s, []) if a <= t < b), t)

    def deaths(self) -> list[tuple[int, float]]:
        return sorted(((s, b) for s, ivs in self.lives.items() for _, b in ivs if b != math.inf),
                      key=lambda d: (d[1], d[0]))

    # --- positions

    def pos(self, s: int, t: float):
        tr = self.tracks.get(s)
        if tr is None:
            return None
        ts = tr[0]
        i = int(np.searchsorted(ts, t))
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(ts) and abs(ts[j] - t) <= 1.5 / self.hz and \
                    (best is None or abs(ts[j] - t) < abs(ts[best] - t)):
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

    # --- utility

    def _read_util(self) -> None:
        px_per_uv = PX / 10000
        geo, util = self.geo, self.blob.get("util") or []
        for e in util:
            key, k = util_key(e), e.get("k")
            if k == "ability":
                end = min(x for x in (e.get("t1"), e.get("gone"), self.t_end) if x is not None)
                self.events += [e["t"], end]
                if (e.get("thrown") or {}).get("t0") is not None:
                    self.events.append(e["thrown"]["t0"])
                if e.get("kind") == "Bomb" and self.plant is None:
                    self.plant = snap(e["t"])
                if e.get("kind") == "Projectile":
                    continue
                t0, t1 = _span(e["t"], end)
                if key == VIPER_WALL and e.get("points"):
                    self._wall(e, end)
                for pat, r, solid in SMOKES:
                    if pat.match(key):
                        self.smokes.append((t0, t1, e["u"] * px_per_uv, e["v"] * px_per_uv,
                                            r * geo.uv_per_unit * px_per_uv, solid))
                for pat, r in DAMAGE_ZONES:
                    if pat.match(key):
                        self.damage_zones.append((t0, t1, e["u"] * px_per_uv, e["v"] * px_per_uv,
                                                  r * geo.uv_per_unit * px_per_uv, e.get("by")))
                self._watcher(e, key, t0, t1)
            elif k == "flash":
                self._flash(e, util)
            elif k == "nearsight":
                self._nearsight(e)
            elif k == "reveal":
                self.downgraded[e["target"]].append(_span(e["t"], e["t1"]))
                self.reveals.append((*_span(e["t"], e["t1"]), e.get("by"), e["target"]))
                self.events += [e["t"], e["t1"]]
            elif k == "status":
                if e.get("status") in DOWNGRADE_STATUSES:
                    self.downgraded[e["target"]].append(_span(e["t"], e["t1"]))
                elif e.get("status") in CONTEST_STATUSES:
                    self.contest_status[e["target"]].append(_span(e["t"], e["t1"]))
                self.events += [e["t"], e["t1"]]
            elif k == "shot":
                self.shot_times.append(e["t"])
            elif k == "damage":
                self._damage(e)
        if not any(e.get("k") == "damage" for e in util):
            self.missing["damage hits (enemy damage zones only)"] += 1

    def _flash(self, e: dict, util: list) -> None:
        if "hits" in e:
            for slot, t, dur in e["hits"]:
                if dur is None:
                    dur = FLASH_FULL_S.get((e.get("ability") or "").split("_")[0], FLASH_DEFAULT_S)
                    self.missing["flash hit without a duration (placeholder used)"] += 1
                self.flashed[slot].append(_span(t, t + dur))
                self.events += [t, t + dur]
            return
        pop = next((a["t"] for a in util if a.get("k") == "ability" and a.get("by") == e.get("by")
                    and (a.get("thrown") or {}).get("t0") == e["t"]), e["t"] + FLASH_FUSE_S)
        dur = FLASH_FULL_S.get((e.get("ability") or "").split("_")[0], FLASH_DEFAULT_S)
        for s in e.get("targets", []):
            self.flashed[s].append(_span(pop, pop + dur))
            self.events += [pop, pop + dur]
            self.missing["flash hit time and blind duration (placeholder used)"] += 1

    def _nearsight(self, e: dict) -> None:
        if "hits" in e:
            for slot, t, dur in e["hits"]:
                if dur is None:
                    dur = NEARSIGHT_DEFAULT_S
                    self.missing["nearsight hit without a duration (placeholder used)"] += 1
                self.nearsight[slot].append(_span(t, t + dur))
                self.events += [t, t + dur]
            return
        dur = NEARSIGHT_S.get(e.get("ability"), NEARSIGHT_DEFAULT_S)
        for s in e.get("targets", []):
            self.nearsight[s].append(_span(e["t"], e["t"] + dur))
            self.events += [e["t"], e["t"] + dur]
            self.missing["nearsight hit time and duration (placeholder used)"] += 1

    def _damage(self, e: dict) -> None:
        by, target = e.get("by"), e.get("target")
        if by is None or target is None or self.team.get(by) is None or self.team.get(by) == self.team.get(target):
            return
        t1 = e.get("t1", e["t"])
        if e.get("wall"):
            self.hit_contest[target].append(_span(e["t"], t1 + WALLBANG_CONTEST_S))
        elif e.get("src") == "ability":
            self.hit_contest[target].append(_span(e["t"], t1 + DAMAGE_CONTEST_PAD_S))

    def _watcher(self, e: dict, key: str, t0: float, t1: float) -> None:
        px_per_uv = PX / 10000
        geo = self.geo
        by = e.get("by")
        if by is None:
            return
        x, y = e["u"] * px_per_uv, e["v"] * px_per_uv
        if key == TRIPWIRE and e.get("end"):
            a = np.array([x, y])
            b = np.array(e["end"]) * px_per_uv
            n = int(np.abs(b - a).max()) + 1
            xs = np.linspace(a[0], b[0], n)
            ys = np.linspace(a[1], b[1], n)
            cells = np.unique((ys // CELL).astype(int).clip(0, GRID - 1) * GRID
                              + (xs // CELL).astype(int).clip(0, GRID - 1))
            self.watchers.append(Watcher("trip", by, t0, t1, cells=cells))
        elif key in AREA_TRIPS:
            r_px = AREA_TRIPS[key] / geo.m_per_px
            c = geo.cell_of_px(x, y)
            near = ((geo.centres[:, 0] - x) ** 2 + (geo.centres[:, 1] - y) ** 2) < r_px ** 2
            if geo.row_of[c] >= 0:
                row = np.unpackbits(geo.rows[geo.row_of[c]])[: GRID * GRID].astype(bool)
                self.watchers.append(Watcher("area", by, t0, t1, cells=np.flatnonzero(near & row)))
        elif key in DRONES and e.get("path"):
            half, rng = DRONES[key]
            in_use = [_span(a, b if b is not None else t1) for a, b in e["possessed"]] if "possessed" in e else None
            if in_use is None:
                self.missing["drone flown intervals (whole life used)"] += 1
            self.watchers.append(Watcher("drone", by, t0, t1, path=e["path"], yaws=e.get("yaws"), in_use=in_use,
                                         half=half, range_m=rng))
        elif key == CAMERA:
            if "possessed" not in e:
                self.missing["Cypher camera: no in-use intervals (not modelled)"] += 1
                return
            in_use = [_span(a, b if b is not None else t1) for a, b in e["possessed"]]
            if "yaws" not in e:
                self.missing["camera yaw over time (placed yaw used)"] += 1
            self.watchers.append(Watcher("camera", by, t0, t1, x=x, y=y, yaw=e.get("yaw"), yaws=e.get("yaws"),
                                         in_use=in_use, half=FOV_HALF))
        elif key == TURRET:
            if "yaws" not in e:
                self.missing["turret yaw over time (placed yaw used)"] += 1
            if e.get("yaw") is None and not e.get("yaws"):
                return
            self.watchers.append(Watcher("turret", by, t0, t1, x=x, y=y, yaw=e.get("yaw"), yaws=e.get("yaws"),
                                         half=TURRET_HALF))

    def _wall(self, e: dict, end: float) -> None:
        """Viper's wall blocks sight while it is up (`on`), along its laid line (`points`)."""
        wall = Wall.from_points([(u * PX / 10000, v * PX / 10000) for u, v in e["points"]])
        if wall is None:
            return
        for a, b in e.get("on") or []:
            b = end if b is None else min(b, end)
            if b > a:
                self.walls.append((*_span(a, b), wall))
                self.events += [a, b]

    def smokes_at(self, t: float) -> list:
        """What blocks sight at t: the smokes as (x, y, r, solid), then the walls that are up."""
        return ([(x, y, r, solid) for t0, t1, x, y, r, solid in self.smokes if t0 <= t < t1]
                + [w for t0, t1, w in self.walls if t0 <= t < t1])

    # --- ticks

    def tick_times(self) -> np.ndarray:
        """Q75: every TICK_STEP_S, plus events on the 1/GRID_HZ grid, within [0, t_end)."""
        times = set(np.round(np.arange(0.0, self.t_end, TICK_STEP_S), 6).tolist())
        times.update(snap(t) for t in self.events)
        for _, t in self.deaths():
            # Before the death: the dying player's lost control, exact. At or after: the state without them.
            times.update((snap_before(t), snap_after(t)))
        windows = {}
        for t in sorted(self.shot_times):
            windows.setdefault(math.floor(t / SHOT_WINDOW_S), snap(t))
        times.update(windows.values())
        return np.array(sorted(t for t in times if 0.0 <= t < self.t_end))


def path_at(path: list, t: float):
    """(x px, y px, heading) along a [[t, u, v], ...] path, or None outside it."""
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
    if len(path) == 1:
        return path[0][1] * PX / 10000, path[0][2] * PX / 10000, 0.0
    return None


# ---------------------------------------------------------------- one tick


@dataclass
class Holder:
    slot: int
    team: str
    cell: int
    x: float
    y: float
    active: np.ndarray       # flat GRID*GRID, after statuses
    passive: np.ndarray
    watch: np.ndarray        # own live watchers (passive)
    raw: np.ndarray          # body view before statuses, for the loss rule
    body: np.ndarray         # body view after statuses (what they can see: enemy sight of a holder)
    flagged: bool            # enemy damage, a wallbang or a contesting status on them
    mode: str
    memory: np.ndarray | None = None   # the remembered part of `passive` (Memory.apply), flat


@dataclass
class Fill:
    free: np.ndarray                             # GRID x GRID
    comps: list = field(default_factory=list)    # (mask GRID x GRID, seen flat, slots in it)
    seen: np.ndarray = None                      # flat

    def safe(self, walk: np.ndarray) -> np.ndarray:
        return walk & ~self.seen.reshape(GRID, GRID) & ~self.free


def sector(geo: Geometry, x: float, y: float, yaw: float, half: float) -> np.ndarray:
    """Flat cells whose centre lies within yaw +/- half of (x, y), widened by the cell's own angular size."""
    dx = geo.centres[:, 0] - x
    dy = geo.centres[:, 1] - y
    dist = np.hypot(dx, dy)
    diff = np.abs((np.degrees(np.arctan2(dy, dx)) - yaw + 180) % 360 - 180)
    return (diff <= half + np.degrees(np.arctan2(CELL / 2, np.maximum(dist, 1e-3)))) | (dist < CELL)


def smoke_blocks(p: np.ndarray, q: np.ndarray, smoke) -> np.ndarray:
    """S x N: does the segment from each p (S x 2) to each q (N x 2) cross the smoke (or wall)?"""
    if isinstance(smoke, Wall):
        return wall_blocks(p, q, smoke)
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


def seen_from(geo: Geometry, src: np.ndarray, smokes: list, skip: np.ndarray | None = None) -> np.ndarray:
    """Flat cells any of the source cells sees (their static rows), minus pairs a smoke blocks.
    `skip` (flat) marks targets that needn't be rechecked (the free space itself)."""
    src = src[geo.row_of[src] >= 0]
    if not len(src):
        return np.zeros(GRID * GRID, bool)
    packed = geo.rows[geo.row_of[src]]
    static = np.unpackbits(np.bitwise_or.reduce(packed, axis=0))[: GRID * GRID].astype(bool)
    if not smokes:
        return static
    cand = static & ~skip if skip is not None else static.copy()
    targets = np.flatnonzero(cand)
    out = static & ~cand
    if not len(targets):
        return out
    q = geo.centres[targets]
    seen_t = np.zeros(len(targets), bool)
    for lo in range(0, len(src), 256):
        rows = np.unpackbits(packed[lo:lo + 256], axis=1)[:, : GRID * GRID][:, targets].astype(bool)
        p = geo.centres[src[lo:lo + 256]]
        for smoke in smokes:
            rows &= ~smoke_blocks(p, q, smoke)
        seen_t |= rows.any(0)
    out[targets] = seen_t
    return out


def _yaw_at(w: Watcher, t: float, default: float) -> float:
    if w.yaws:
        return float(_step_at(w.yaws, t, w.yaws[0][1]))
    return float(w.yaw) if w.yaw is not None else default


def special_links(geo: Geometry) -> list[tuple[int, int, bool]]:
    """The map's specials (teleporters, ropes, drops) as (cell a, cell b, one way)."""
    out = []
    for sp in geo.specials:
        try:
            a = geo.cell_of_px(*geo.px_of_uv(*sp["a"]))
            b = geo.cell_of_px(*geo.px_of_uv(*sp["b"]))
        except (KeyError, TypeError):
            continue
        out.append((a, b, bool(sp.get("one_way"))))
    return out


class Tick:
    """The inputs of one tick, and `compose` (the state) with or without a removed player."""

    def __init__(self, rnd: RoundInputs, t: float, timings: dict | None = None):
        geo = self.geo = rnd.geo
        self.rnd, self.t = rnd, t
        self.smokes = rnd.smokes_at(t)
        timings = timings if timings is not None else defaultdict(float)
        started = time.perf_counter()
        self.holders: dict[int, Holder] = {}
        using = {}   # owner -> the camera or drone they're in
        for w in rnd.watchers:
            if w.kind in ("drone", "camera") and w.t0 <= t < w.t1 and rnd.alive(w.by, t):
                if w.in_use is None or _during(w.in_use, t):
                    using[w.by] = w
        for s in sorted(rnd.team):
            if not rnd.alive(s, t):
                continue
            p = rnd.pos(s, t)
            if p is None:
                continue
            x, y, yaw = p
            cell = geo.cell_of_px(x, y)
            mode = rnd.movement(s, t, p)
            if s in using:
                raw = np.zeros(GRID * GRID, bool)  # in a camera or drone: their own view doesn't count
            else:
                raw = cast(geo, x, y, yaw + np.arange(-FOV_HALF, FOV_HALF + 1e-9, RAY_STEP_DEG), self.smokes)
            body = raw.copy()
            if _during(rnd.flashed[s], t):
                body[:] = False
            elif _during(rnd.nearsight[s], t):
                r = NEARSIGHT_RADIUS_M / geo.m_per_px
                body &= ((geo.centres[:, 0] - x) ** 2 + (geo.centres[:, 1] - y) ** 2) < r * r
            if _during(rnd.downgraded[s], t):
                active = np.zeros(GRID * GRID, bool)
            else:
                active = body & sector(geo, x, y, yaw, CONE_HALF[mode])
            passive = body & ~active
            if not _during(rnd.flashed[s], t):
                passive |= self._presence(x, y, cell) & ~active
            watch = self._watch(s, t)
            enemy = "B" if rnd.team[s] == "A" else "A"
            flagged = _during(rnd.contest_status[s], t) or _during(rnd.hit_contest[s], t) or any(
                t0 <= t < t1 and (x - zx) ** 2 + (y - zy) ** 2 < r * r and rnd.team.get(by) == enemy
                for t0, t1, zx, zy, r, by in rnd.damage_zones)
            self.holders[s] = Holder(s, rnd.team[s], cell, x, y, active, passive, watch, raw, body, flagged, mode)
        timings["vision"] += time.perf_counter() - started
        # slot -> the live control that holds unknown back: vision, watchers and their own cell (before
        # Memory adds remembered ground to `passive`)
        self.live: dict[int, np.ndarray] = {}
        for s, h in self.holders.items():
            lv = h.active | h.passive | h.watch
            lv[h.cell] = True
            self.live[s] = lv
        # enemy sight of each holder: sees[e] = the holders e's body view reaches
        self.sees = {e.slot: {h.slot for h in self.holders.values() if h.team != e.team and e.body[h.cell]}
                     for e in self.holders.values()}
        self._dist: dict[int, np.ndarray] = {}
        # side -> flat cells an unknown enemy could be in: extra sources of that side's free space
        # (a team's knowledge picture only; see `knowledge_tick`)
        self.seeds: dict[str, np.ndarray] = {}
        # side -> flat cells claimed as passive by no player (a just-lost enemy's remembered view)
        self.extra_passive: dict[str, np.ndarray] = {}
        self._back: dict[str, dict[int, np.ndarray]] = {}
        self._safe: dict[str, np.ndarray] = {}    # side -> its Safe cells, from the full compose
        # side -> flat cells where an enemy of that side could be (Unknown; set by compute_round). None
        # for a tick built on its own: then Safe is the instant flood (Q73), as before unknown.
        self.unknown: dict[str, np.ndarray] | None = None
        self._usafe: dict = {}   # side, or (side, removed slot) -> its Safe cells from unknown
        self._ucf: dict[tuple[str, int], np.ndarray] = {}   # (side, removed slot) -> unknown_without

    def _presence(self, x: float, y: float, cell: int) -> np.ndarray:
        """Walkable cells within PRESENCE_M of (x, y) px, reached by walking from the player's cell
        (8-connected), so the bubble doesn't pass through walls."""
        geo = self.geo
        start = np.zeros((GRID, GRID), bool)
        start.flat[cell] = True
        steps = int(math.ceil(PRESENCE_M / geo.cell_m)) + 1
        reach = ndimage.binary_dilation(start, EIGHT, iterations=steps, mask=geo.walk).ravel()
        r = PRESENCE_M / geo.m_per_px
        return reach & (((geo.centres[:, 0] - x) ** 2 + (geo.centres[:, 1] - y) ** 2) <= r * r)

    def backfill(self, side: str, removed: int | None = None) -> dict[int, np.ndarray]:
        """Backfill (the user's rule, 2026-09-30): ground behind a player's watched line, back to the
        team's control, becomes that player's passive control. A pocket is walkable ground the team
        doesn't claim (live, remembered or watched) that no enemy (nor, in a team's knowledge picture,
        an unseen enemy's possible position) can walk into without crossing the team's claims, less
        the enemy's own claims. Each pocket cell goes to the player nearest it in 4-connected steps from
        their live view (ties to the lower slot); a pocket no live view borders goes to nobody.
        Returns slot -> flat cells. With `removed`: a player with no share only shrinks the others' to
        what is still a pocket without them; one with a share has it split again among the rest."""
        if side not in self._back:
            self._back[side] = self._backfill_shares(side, None)
        shares = self._back[side]
        if removed is None or not shares:
            return shares
        if removed in shares:
            return self._backfill_shares(side, removed)
        pocket = self._pocket(side, removed)
        return {s: m & pocket for s, m in shares.items() if (m & pocket).any()}

    def _pocket(self, side: str, removed: int | None) -> np.ndarray:
        other = "B" if side == "A" else "A"
        own = np.zeros(GRID * GRID, bool)
        enemy = np.zeros(GRID * GRID, bool)
        for h in self.team(side, removed):
            own |= h.active | h.passive | h.watch
        for e in self.team(other, removed):
            enemy |= e.active | e.passive | e.watch
        extra = self.extra_passive.get(other) if removed is None else None
        if extra is not None:
            enemy |= extra
        lab, _ = ndimage.label(self.geo.walk & ~own.reshape(GRID, GRID))
        sources = {int(lab.flat[e.cell]) for e in self.team(other, removed)}
        seed = self.seeds.get(other) if removed is None else None
        if seed is not None:
            sources |= set(np.unique(lab.ravel()[seed]).tolist())
        reach = self._reach(lab, sources - {0})
        pocket = ((lab > 0) & ~np.isin(lab, list(reach))).ravel() & ~enemy
        if self.unknown is not None:
            pocket &= ~self.unknown_for(side, removed)   # never where an enemy could be (docs/map-control-unknown-plan.md)
        return pocket

    def _backfill_shares(self, side: str, removed: int | None) -> dict[int, np.ndarray]:
        pocket = self._pocket(side, removed).reshape(GRID, GRID)
        if not pocket.any():
            return {}
        owner = np.full((GRID, GRID), -1, np.int16)
        fronts = {}
        for h in sorted(self.team(side, removed), key=lambda h: h.slot):
            first = ndimage.binary_dilation(h.active.reshape(GRID, GRID)) & pocket & (owner < 0)
            if first.any():
                owner[first] = h.slot
                fronts[h.slot] = first
        while fronts:
            grown = {}
            for slot, front in sorted(fronts.items()):
                new = ndimage.binary_dilation(front) & pocket & (owner < 0)
                if new.any():
                    owner[new] = slot
                    grown[slot] = new
            fronts = grown
        return {int(s): (owner == s).ravel() for s in np.unique(owner[owner >= 0]).tolist()}

    def _watch(self, s: int, t: float) -> np.ndarray:
        geo, rnd = self.geo, self.rnd
        watch = np.zeros(GRID * GRID, bool)
        for w in rnd.watchers:
            if w.by != s or not (w.t0 <= t < w.t1):
                continue
            if w.kind in ("trip", "area"):
                watch[w.cells] = True
            elif w.kind == "drone":
                if w.in_use is not None and not _during(w.in_use, t):
                    continue
                at = path_at(w.path, t)
                if at is None:
                    continue
                dx_, dy_, heading = at
                heading = _yaw_at(w, t, heading) if w.yaws else heading
                dv = cast(geo, dx_, dy_, heading + np.arange(-w.half, w.half + 1e-9, RAY_STEP_DEG), self.smokes)
                if w.range_m:
                    r = w.range_m / geo.m_per_px
                    dv &= ((geo.centres[:, 0] - dx_) ** 2 + (geo.centres[:, 1] - dy_) ** 2) < r * r
                watch |= dv
            elif w.kind == "camera":
                if not _during(w.in_use, t):
                    continue
                yaw = _yaw_at(w, t, 0.0)
                watch |= cast(geo, w.x, w.y, yaw + np.arange(-w.half, w.half + 1e-9, RAY_STEP_DEG), self.smokes)
            elif w.kind == "turret":
                yaw = _yaw_at(w, t, 0.0)
                watch |= cast(geo, w.x, w.y, yaw + np.arange(-w.half, w.half + 1e-9, RAY_STEP_DEG), self.smokes)
        return watch

    # --- building blocks

    def team(self, side: str, removed: int | None) -> list[Holder]:
        return [h for h in self.holders.values() if h.team == side and h.slot != removed]

    def claims(self, side: str, removed: int | None):
        z = np.zeros(GRID * GRID, bool)
        active, passive, raw = z.copy(), z.copy(), z.copy()
        # in the counterfactual, the unknown that pours in also ends teammates' remembered ground (rule 4)
        eaten = self.unknown_for(side, removed) if self.unknown is not None and removed is not None else None
        for h in self.team(side, removed):
            active |= h.active
            mine = h.passive if eaten is None or h.memory is None else h.passive & ~(h.memory & eaten)
            passive |= mine | h.watch
            raw |= h.raw | h.watch
        for share in self.backfill(side, removed).values():
            passive |= share
        extra = self.extra_passive.get(side) if removed is None else None
        if extra is not None:
            passive |= extra
        passive &= ~active
        return active, passive, active | passive, raw

    def _links(self) -> list[tuple[int, int, bool]]:
        return special_links(self.geo)

    def _reach(self, lab: np.ndarray, start: set[int]) -> set[int]:
        """Labels reachable from `start` through the map's specials (teleporters, ropes, drops)."""
        links = self._links()
        if not links:
            return set(start)
        edges: dict[int, set[int]] = defaultdict(set)
        for a, b, one_way in links:
            la, lb = int(lab.flat[a]), int(lab.flat[b])
            if la and lb and la != lb:
                edges[la].add(lb)
                if not one_way:
                    edges[lb].add(la)
        seen, todo = set(start), list(start)
        while todo:
            for nxt in edges.get(todo.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return seen

    def _open(self, side: str, watched: np.ndarray, removed: int | None) -> np.ndarray:
        open_ = self.geo.walk & ~watched
        for h in self.team(side, removed):
            open_.flat[h.cell] = True
        seed = self.seeds.get(side) if removed is None else None
        if seed is not None:
            open_ |= seed.reshape(GRID, GRID)
        return open_

    def fill(self, side: str, blocked_by: np.ndarray, removed: int | None) -> Fill | None:
        """`side`'s free space through walkable cells `blocked_by` (the other team's watched cells) misses."""
        hs = self.team(side, removed)
        seed = self.seeds.get(side) if removed is None else None
        if seed is not None and not seed.any():
            seed = None
        if not hs and seed is None:
            return None
        watched = blocked_by.reshape(GRID, GRID)
        lab, _ = ndimage.label(self._open(side, watched, removed))
        free = np.zeros((GRID, GRID), bool)
        by_reach: dict[frozenset, list[int]] = defaultdict(list)
        for h in hs:
            by_reach[frozenset(self._reach(lab, {int(lab.flat[h.cell])}))].append(h.slot)
        if seed is not None:   # an unknown enemy's possible positions: a source with no player in it
            for label in set(np.unique(lab.ravel()[seed]).tolist()) - {0}:
                # slot -1: someone unseen, so an entry's way back (compose) can lead here too
                by_reach[frozenset(self._reach(lab, {label}))].append(-1)
        comps = []
        for labels, slots in by_reach.items():
            mask = np.isin(lab, list(labels))
            free |= mask
            comps.append((mask, self.comp_seen(mask, watched), slots))
        seen = np.zeros(GRID * GRID, bool)
        for _, s, _ in comps:
            seen |= s
        return Fill(free, comps, seen)

    def comp_seen(self, mask: np.ndarray, watched: np.ndarray) -> np.ndarray:
        """Q73: what a free component sees, from its whole boundary (every free cell next to anything
        not free), smoke-aware. The frontier (next to the team's vision) sees most of it; the rest of
        the boundary rechecks only the targets the frontier missed."""
        walk = self.geo.walk
        edge = mask & ndimage.binary_dilation(watched & walk, EIGHT)
        first = seen_from(self.geo, np.flatnonzero(edge), self.smokes, skip=mask.ravel())
        rim = mask & ndimage.binary_dilation(~mask, EIGHT) & ~edge
        return first | seen_from(self.geo, np.flatnonzero(rim), self.smokes, skip=mask.ravel() | first)

    def unknown_for(self, side: str, removed: int | None = None) -> np.ndarray:
        """`side`'s unknown at this tick, or in the counterfactual without `removed` when they are on
        `side` (`unknown_without`)."""
        if removed is not None and removed in self.holders and self.holders[removed].team == side:
            return self.unknown_without(side, removed)
        return self.unknown[side]

    def unknown_without(self, side: str, removed: int) -> np.ndarray:
        """The counterfactual's unknown (the user's rule, 2026-10-01): unknown comes only from its sources
        (the unknown already out and the enemy's players), and without `removed` there is less live
        control to hold it back. What its sources can walk to without them, but not with them (instantly,
        8-connected and across specials), joins the unknown; ground it could reach anyway but hasn't yet
        is not theirs to hold."""
        key = (side, removed)
        if key not in self._ucf:
            walk = self.geo.walk.ravel()
            others = np.zeros(GRID * GRID, bool)
            for h in self.team(side, removed):
                others |= self._live_of(h)
            held = others | self._live_of(self.holders[removed])
            src = self.unknown[side].copy()
            for e in self.holders.values():
                if e.team != side:
                    src[e.cell] = True
            gained = self._flood(src & ~others, walk & ~others) & ~self._flood(src & ~held, walk & ~held)
            self._ucf[key] = self.unknown[side] | gained
        return self._ucf[key]

    def _live_of(self, h: Holder) -> np.ndarray:
        if h.slot in self.live:
            return self.live[h.slot]
        lv = h.active | h.passive | h.watch
        lv[h.cell] = True
        return lv

    def _flood(self, src: np.ndarray, room: np.ndarray) -> np.ndarray:
        """Flat cells of `room` connected (8-connected, and across the map's specials) to `src`."""
        lab, _ = ndimage.label(room.reshape(GRID, GRID), EIGHT)
        labels = set(np.unique(lab.ravel()[src & room]).tolist()) - {0}
        if not labels:
            return np.zeros(GRID * GRID, bool)
        return np.isin(lab, list(self._reach(lab, labels))).ravel()

    def unknown_safe(self, side: str, removed: int | None = None) -> np.ndarray:
        """Safe (docs/map-control-unknown-plan.md): walkable cells outside `side`'s unknown that no cell of
        it sees, smoke-aware (`comp_seen`'s boundary method, Q73); with `removed` on `side`, from the
        counterfactual's unknown. Cached for the tick; the knowledge pictures share it."""
        unk_flat = self.unknown_for(side, removed)
        key = side if unk_flat is self.unknown[side] else (side, removed)
        if key not in self._usafe:
            walk = self.geo.walk
            unk = unk_flat.reshape(GRID, GRID)
            if not unk.any():
                self._usafe[key] = walk.ravel().copy()
            else:
                watched = np.zeros(GRID * GRID, bool)
                for h in self.team(side, removed):
                    watched |= h.active | h.passive | h.watch
                seen = self.comp_seen(unk, watched.reshape(GRID, GRID))
                self._usafe[key] = (walk & ~unk).ravel() & ~seen
        return self._usafe[key]

    def dist_from(self, h: Holder) -> np.ndarray:
        """Walking distance (8-connected cells) from a holder, cached for the tick."""
        if h.slot not in self._dist:
            walk = self.geo.walk
            dist = np.full((GRID, GRID), -1, np.int32)
            front = np.zeros((GRID, GRID), bool)
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
        """Q56: the shortest walk from entry `e` to `target`, widened by WAY_BACK_WIDTH_M, on the
        cells the other team watches."""
        dist = self.dist_from(e)
        reach = target & (dist >= 0)
        if not reach.any():
            return np.zeros(GRID * GRID, bool)
        d = np.where(reach, dist, np.iinfo(np.int32).max)
        cur = np.unravel_index(int(np.argmin(d)), d.shape)
        path = np.zeros((GRID, GRID), bool)
        path[cur] = True
        while dist[cur] > 0:
            cy, cx = cur
            best = None
            for oy in (-1, 0, 1):
                for ox in (-1, 0, 1):
                    ny, nx = cy + oy, cx + ox
                    if 0 <= ny < GRID and 0 <= nx < GRID and dist[ny, nx] == dist[cur] - 1:
                        best = (ny, nx)
                        break
                if best:
                    break
            cur = best
            path[cur] = True
        width = max(1, round(WAY_BACK_WIDTH_M / self.geo.cell_m))
        return ndimage.binary_dilation(path, EIGHT, iterations=width).ravel() & watched_other

    # --- the state

    def compose(self, removed: int | None = None, base: dict | None = None, full: bool = True,
                stats: dict | None = None) -> dict:
        """The tick's cell states. `removed` drops one player (the counterfactual). With `base` and
        not `full`, fills are reused or extended from the base instead of recomputed."""
        geo, walk = self.geo, self.geo.walk
        stats = stats if stats is not None else defaultdict(int)
        cl = {s: self.claims(s, removed) for s in ("A", "B")}
        fills = {}
        rside = self.holders[removed].team if removed is not None else None
        for side in ("A", "B"):
            other = "B" if side == "A" else "A"
            if base is None or full:
                fills[side] = self.fill(side, cl[other][2], removed)
                continue
            old = base["fills"][side]
            if side == rside:
                # the removed player's own fill loses their source: drop their component unless shared
                if old is None:
                    fills[side] = None
                    continue
                if base["cl"][other][2][self.holders[removed].cell]:
                    # their cell was forced open inside the other team's vision; it may have bridged the fill
                    fills[side] = self.fill(side, cl[other][2], removed)
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
                    free = np.zeros((GRID, GRID), bool)
                    seen = np.zeros(GRID * GRID, bool)
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
                opened = (base["cl"][other][2] & ~cl[other][2]).reshape(GRID, GRID)
                if not (opened & ndimage.binary_dilation(old.free, EIGHT)).any():
                    fills[side] = old
                    stats["enemy_fill_same"] += 1
                    continue
                watched = cl[other][2].reshape(GRID, GRID)
                lab, _ = ndimage.label(self._open(side, watched, removed))
                labels = self._reach(lab, {int(lab.flat[h.cell]) for h in self.team(side, removed)})
                free = np.isin(lab, list(labels))
                added = free & ~old.free
                edge = added & ndimage.binary_dilation(~free, EIGHT)
                seen = old.seen | seen_from(geo, np.flatnonzero(edge), self.smokes, skip=free.ravel())
                fills[side] = Fill(free, [], seen)
                stats["enemy_fill_extended"] += 1
        level = {}
        for side in ("A", "B"):
            other = "B" if side == "A" else "A"
            active, passive, _, _ = cl[side]
            lv = np.zeros(GRID * GRID, np.int8)
            lv[passive] = 1
            f_enemy = fills[other]
            if self.unknown is not None:
                # nobody alive on the side: it holds nothing, Safe included
                safe = self.unknown_safe(side, removed) if self.team(side, removed) else np.zeros(GRID * GRID, bool)
            else:
                safe = walk.ravel() if f_enemy is None else f_enemy.safe(walk).ravel()
            lv[safe] = np.maximum(lv[safe], 2)
            if removed is None:
                self._safe[side] = safe & walk.ravel()
            lv[active] = 3
            lv[~walk.ravel()] = 0
            level[side] = lv
        if self.unknown is not None:
            # ground both teams hold only as Safe (neither unknown sees it) is nobody's (the user's call, 2026-10-01)
            both = (level["A"] == 2) & (level["B"] == 2)
            level["A"][both] = 0
            level["B"][both] = 0
        contested = (level["A"] > 0) & (level["B"] > 0)
        for side in ("A", "B"):
            other = "B" if side == "A" else "A"
            hs = self.team(side, removed)
            is_contested = {h.slot: h.flagged or any(e in self.sees and h.slot in self.sees[e]
                                                     for e in (x.slot for x in self.team(other, removed)))
                            for h in hs}
            steady = np.zeros(GRID * GRID, bool)
            fought = np.zeros(GRID * GRID, bool)
            for h in hs:
                steady |= h.watch
                if is_contested[h.slot]:
                    fought |= h.active | h.passive
                else:
                    steady |= h.active | h.passive
            contested |= fought & ~steady
            # the entry's way back (Q56): `other`'s players standing in `side`'s vision
            watched = cl[side][2]
            f_other = fills[other]
            for e in self.team(other, removed):
                if not watched[e.cell]:
                    continue
                target = np.zeros((GRID, GRID), bool)
                if f_other is not None:
                    comps = f_other.comps or [(f_other.free, None, [x.slot for x in self.team(other, removed)])]
                    for m, _, slots in comps:
                        if any(x != e.slot for x in slots):
                            target |= m
                if not target.any():
                    for x in self.team(other, removed):
                        if x.slot != e.slot:
                            target.flat[x.cell] = True
                if target.any():
                    contested |= self.way_back(e, target, watched)
            # cells lost to a status (Q55): nobody else claims them, so they are fought over
            contested |= cl[side][3] & ~cl[side][2] & (level[side] == 0) & (level[other] == 0)
        contested &= walk.ravel()
        any_active = (level["A"] == 3) | (level["B"] == 3)
        state = np.zeros(GRID * GRID, np.uint8)
        state[level["A"] > 0] = level["A"][level["A"] > 0]
        state[level["B"] > 0] = 3 + level["B"][level["B"] > 0]
        state[contested] = np.where(any_active[contested], CONTESTED_ACTIVE, CONTESTED)
        return {"state": state, "fills": fills, "cl": cl}

    def coverage(self) -> dict[int, tuple[float, float, np.ndarray, np.ndarray]]:
        """Per player (Q60): active and passive cells, shared cells split evenly, and the active and
        passive masks."""
        out = {}
        for side in ("A", "B"):
            hs = self.team(side, None)
            if not hs:
                continue
            # backfill on ground the team already holds as Safe stays the team's, credited to nobody
            safe = self._safe.get(side)
            back = {s: m & ~safe if safe is not None else m for s, m in self.backfill(side).items()}
            none = np.zeros(GRID * GRID, bool)
            n_act = sum(h.active.astype(np.int8) for h in hs)
            n_pas = sum((h.passive | h.watch | back.get(h.slot, none)).astype(np.int8) for h in hs)
            for h in hs:
                pas = (h.passive | h.watch | back.get(h.slot, none)) & ~h.active
                act = float((1 / n_act[h.active]).sum()) if h.active.any() else 0.0
                psv = float((1 / n_pas[pas]).sum()) if pas.any() else 0.0
                out[h.slot] = (act, psv, h.active, pas)
        return out


# ---------------------------------------------------------------- what the team knew (R3.3)


def possible_region(geo: Geometry, start: int, watched: np.ndarray, steps: int) -> np.ndarray:
    """Flat cells an enemy last at `start` could be in now: at most `steps` 8-connected moves through
    walkable cells the team doesn't watch (`watched`, flat). `start` itself always counts."""
    open_ = geo.walk & ~watched.reshape(GRID, GRID)
    region = np.zeros((GRID, GRID), bool)
    region.flat[start] = True
    front = region.copy()
    for _ in range(max(0, int(steps))):
        nxt = ndimage.binary_dilation(front, EIGHT) & open_ & ~region
        if not nxt.any():
            break
        region |= nxt
        front = nxt
    return region.ravel()


class Knowledge:
    """One team's picture of the enemy, built tick by tick (docs/map-control-team-knew-plan.md).

    An enemy the team sees now (a player's view after flashes, one of its watchers, or its reveal) is
    exact. Every other live enemy is a region of where they could be: from where the team last saw
    them (or their start position, if never), through ground the team doesn't watch, as far as
    KNEW_RUN_MPS since. A just-lost enemy also keeps their last view, as passive only, for
    KNEW_FADE_S. Deaths are known (the kill feed)."""

    def __init__(self, rnd: RoundInputs, side: str):
        self.rnd, self.side = rnd, side
        self.enemy = "B" if side == "A" else "A"
        self.last: dict[int, tuple] = {}                 # slot -> (t, cell, body, x, y)
        self.sightings: dict[int, list] = defaultdict(list)   # slot -> [[t0, t1, u, v], ...]
        self.prev_t: float | None = None
        self.start: dict[int, int] = {}
        for s, team in rnd.team.items():
            if team != self.enemy:
                continue
            p = rnd.pos(s, rnd.t_start)
            tr = rnd.tracks.get(s)
            if p is None and tr is not None and len(tr[0]):
                p = (tr[1][0] * PX / 10000, tr[2][0] * PX / 10000)
            if p is not None:
                self.start[s] = rnd.geo.cell_of_px(p[0], p[1])

    def seen_now(self, tick: Tick, t: float) -> set[int]:
        mine = [h for h in tick.holders.values() if h.team == self.side]
        seen = set()
        for e in tick.holders.values():
            if e.team != self.enemy:
                continue
            if any(e.slot in tick.sees.get(h.slot, ()) or h.watch[e.cell] for h in mine) or any(
                    t0 <= t < t1 and target == e.slot and self.rnd.team.get(by) == self.side
                    for t0, t1, by, target in self.rnd.reveals):
                seen.add(e.slot)
        return seen

    def tick_for(self, tick: Tick, t: float) -> Tick:
        """The tick as this team knew it: vision reused, enemy holders and seeds replaced."""
        geo = self.rnd.geo
        seen = self.seen_now(tick, t)
        for s in seen:
            e = tick.holders[s]
            self.last[s] = (t, e.cell, e.body, e.x, e.y)
            u, v = round(e.x * 10000 / PX), round(e.y * 10000 / PX)
            runs = self.sightings[s]
            if runs and self.prev_t is not None and runs[-1][1] == self.prev_t:
                runs[-1][1], runs[-1][2], runs[-1][3] = round(t, 4), u, v
            else:
                runs.append([round(t, 4), round(t, 4), u, v])
        # a seen enemy brings their live view only: their remembered ground (D6) is theirs to know
        holders = {s: h if h.team == self.side else replace(h, passive=h.body & ~h.active)
                   for s, h in tick.holders.items() if h.team == self.side or s in seen}
        watched = np.zeros(GRID * GRID, bool)
        for h in tick.holders.values():
            if h.team == self.side:
                watched |= h.active | h.passive | h.watch
        seed = np.zeros(GRID * GRID, bool)
        remembered = np.zeros(GRID * GRID, bool)
        for s, team in self.rnd.team.items():
            if team != self.enemy or s in seen or not self.rnd.alive(s, t):
                continue
            if s in self.last:
                t0, cell, body, _, _ = self.last[s]
                if t - t0 <= KNEW_FADE_S:
                    remembered |= body     # their last view, as passive claims only: not a player
            elif s in self.start:
                t0, cell = self.rnd.t_start, self.start[s]
            else:
                continue
            seed |= possible_region(geo, cell, watched, KNEW_RUN_MPS * max(0.0, t - t0) / geo.cell_m)
        seed &= ~watched       # a possible position never opens a hole in what the team watches
        self.prev_t = t
        kt = copy.copy(tick)
        kt.holders = holders
        # only enemies the team sees can contest its players
        kt.sees = {x.slot: {h.slot for h in holders.values() if h.team != x.team and x.body[h.cell]}
                   for x in holders.values()}
        kt._dist = {}
        kt._back, kt._safe = {}, {}    # backfill again, against what the team knew
        kt.seeds = {self.enemy: seed} if seed.any() else {}
        kt.extra_passive = {self.enemy: remembered} if remembered.any() else {}
        return kt


def barrier_start(geo: Geometry, tick) -> dict[str, tuple[np.ndarray, dict[int, int]]]:
    """The barriers drop: side -> (its start ground, flat: the 4-connected walkable region around its
    players, cut by the barrier paint; {slot: start cell}). A side whose ground reaches an enemy's start
    (the paint has a gap) is left out and counted; no paint, no start ground."""
    if geo.barrier is None:
        return {}
    open_ = geo.walk & ~geo.barrier
    regions, _ = ndimage.label(open_)
    out = {}
    for side in ("A", "B"):
        hs = [h for h in tick.holders.values() if h.team == side and open_.ravel()[h.cell]]
        ids = {int(regions.ravel()[h.cell]) for h in hs}
        if not hs:
            continue
        starts = {h.slot: h.cell for h in hs}
        # a player pressed against a barrier can stand on a line cell: they start from the
        # neighbouring open cell in their teammates' ground
        for h in tick.holders.values():
            if h.team == side and h.slot not in starts and geo.barrier.ravel()[h.cell]:
                y, x = divmod(h.cell, GRID)
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1),
                               (y - 1, x - 1), (y - 1, x + 1), (y + 1, x - 1), (y + 1, x + 1)):
                    if 0 <= ny < GRID and 0 <= nx < GRID and int(regions[ny, nx]) in ids:
                        starts[h.slot] = ny * GRID + nx
                        break
        area = np.isin(regions, list(ids))
        if any(area.ravel()[e.cell] for e in tick.holders.values() if e.team != side):
            # the paint has a gap: this side's ground reaches an enemy's start, so it means nothing
            tick.rnd.missing["barrier paint leaks (no start ground)"] += 1
            continue
        out[side] = (area.ravel(), starts)
    return out


class Unknown:
    """Each team's unknown across a round's ticks (docs/map-control-unknown-plan.md): the cells where an
    enemy of the team could be. `begin` takes the barrier drop (each team's unknown is the enemy's start
    ground). `apply` runs on each tick in time order, after the tick's vision and before Memory.apply:
    the enemy's live players push it out from their own cells, it spreads at UNKNOWN_MPS through
    walkable cells in 8-connected steps and across the map's specials, and the team's live control
    (its players' active and passive vision, their watchers and their own cells) clears it and stops it.
    Smokes don't stop it: you can walk through a smoke."""

    def __init__(self, geo: Geometry):
        self.geo = geo
        self.cells = {"A": np.zeros(GRID * GRID, bool), "B": np.zeros(GRID * GRID, bool)}
        self.carry = {"A": 0.0, "B": 0.0}         # metres of spread not yet a whole cell step
        self.t: float | None = None
        self.links = special_links(geo)

    def begin(self, areas: dict) -> None:
        for side in ("A", "B"):
            other = "B" if side == "A" else "A"
            if other in areas:
                self.cells[side] = areas[other][0].copy()

    def apply(self, tick) -> None:
        walk = self.geo.walk.ravel()
        dt = 0.0 if self.t is None else max(0.0, tick.t - self.t)
        self.t = tick.t
        for side in ("A", "B"):
            if not self._enemy_alive(tick, side):
                self.cells[side] = np.zeros(GRID * GRID, bool)   # nobody left: nobody could be anywhere
                continue
            live = np.zeros(GRID * GRID, bool)
            unk = self.cells[side].copy()
            for h in tick.holders.values():
                if h.team == side:
                    live |= h.active | h.passive | h.watch
                    live[h.cell] = True
                else:
                    unk[h.cell] = True            # an enemy pushes it out from where they stand
            room = walk & ~live
            self.carry[side] += UNKNOWN_MPS * dt
            steps = int(self.carry[side] // self.geo.cell_m)
            self.carry[side] -= steps * self.geo.cell_m
            self.cells[side] = self._spread(unk & room, room, steps)

    @staticmethod
    def _enemy_alive(tick, side: str) -> bool:
        """Any enemy of `side` alive at the tick: from the round's lives when the tick has them (a live
        player can lack a position sample), else from its holders."""
        rnd = getattr(tick, "rnd", None)
        if rnd is not None:
            return any(team != side and rnd.alive(s, tick.t) for s, team in rnd.team.items())
        return any(h.team != side for h in tick.holders.values())

    def _spread(self, cells: np.ndarray, room: np.ndarray, steps: int) -> np.ndarray:
        g, r = cells.reshape(GRID, GRID), room.reshape(GRID, GRID)
        for _ in range(steps):
            nxt = ndimage.binary_dilation(g, EIGHT) & r
            for a, b, one_way in self.links:
                if g.flat[a] and r.flat[b]:
                    nxt.flat[b] = True
                if not one_way and g.flat[b] and r.flat[a]:
                    nxt.flat[a] = True
            if np.array_equal(nxt, g):
                break
            g = nxt
        return g.ravel().copy()


class Memory:
    """Remembered ground across a round's ticks (D6). `begin` takes the barrier drop (each team
    remembers its side, shared out to its players by walking distance). `apply` runs on each tick in
    time order, after Unknown.apply and before `compose`: it drops what the team's unknown has
    reached, adds what is left to each holder's passive cells, then remembers what they see now."""

    def __init__(self, geo: Geometry):
        self.geo = geo
        self.cells: dict[int, np.ndarray] = {}    # slot -> flat remembered cells

    def begin(self, areas: dict) -> None:
        for _, (area, starts) in areas.items():
            for slot, share in _share_by_walk(area.reshape(GRID, GRID), starts).items():
                self.cells[slot] = share.ravel()

    def apply(self, tick, unknown: dict | None = None) -> None:
        walk = self.geo.walk.ravel()
        for s in [s for s in self.cells if s not in tick.holders]:
            del self.cells[s]                     # memory dies with its player
        for h in tick.holders.values():
            if h.slot in self.cells:
                self.cells[h.slot] &= ~(h.active | h.passive)    # seen again: live, not memory
                if unknown is not None:
                    self.cells[h.slot] &= ~unknown[h.team]       # an enemy could be there now
        for h in tick.holders.values():
            seen = (h.active | h.passive) & walk
            if h.slot in self.cells:
                h.memory = self.cells[h.slot] & ~h.active
                h.passive = h.passive | h.memory
                self.cells[h.slot] |= seen
            else:
                self.cells[h.slot] = seen


def _share_by_walk(area: np.ndarray, starts: dict[int, int]) -> dict[int, np.ndarray]:
    """Each cell of `area` (GRID x GRID) to the start cell nearest it in 4-connected walking steps;
    a tie goes to the lower slot. Cells no start can reach go to nobody."""
    owner = np.full(area.shape, -1, np.int16)
    fronts = {}
    for slot, cell in sorted(starts.items()):
        y, x = divmod(cell, GRID)
        if owner[y, x] < 0:
            owner[y, x] = slot
            fronts[slot] = owner == slot
    while fronts:
        grown = {}
        for slot, front in sorted(fronts.items()):
            new = ndimage.binary_dilation(front, mask=area & (owner < 0))
            new &= owner < 0
            if new.any():
                owner[new] = slot
                grown[slot] = new
        fronts = grown
    return {slot: owner == slot for slot in starts if (owner == slot).any()}


def score(state: np.ndarray, side: str) -> np.ndarray:
    """Q62: ours +1, contested and nobody 0, theirs -1."""
    a = (state >= A_PASSIVE) & (state <= A_ACTIVE)
    b = (state >= B_PASSIVE) & (state <= B_ACTIVE)
    ours, theirs = (a, b) if side == "A" else (b, a)
    return ours.astype(np.int8) - theirs.astype(np.int8)


def level_of(state: np.ndarray, side: str) -> np.ndarray:
    """0 none, 1 passive, 2 safe, 3 active for `side`'s own states; 0 elsewhere."""
    base = A_PASSIVE if side == "A" else B_PASSIVE
    lv = state.astype(np.int16) - base + 1
    return np.where((lv >= 1) & (lv <= 3), lv, 0)


# ---------------------------------------------------------------- the round's result


@dataclass
class Section:
    key: str                 # "r0", "r1", ... by the round clock; "p0", ... by the spike clock
    t0: float
    t1: float
    seconds: float = 0.0
    totals: np.ndarray = None   # N_STATES x walkable cells: seconds in each state


@dataclass
class PlayerStats:
    slot: int
    team: str
    side: str | None
    alive_s: float = 0.0
    active_m2s: float = 0.0
    passive_m2s: float = 0.0
    control_m2s: float = 0.0
    taken_m2: float = 0.0     # space taken (CONTROL_REVISION 2): see compute_round
    deaths: list = field(default_factory=list)

    def per_second(self, value: float) -> float | None:
        return value / self.alive_s if self.alive_s > 0 else None

    @property
    def active_ratio(self) -> float | None:
        both = self.active_m2s + self.passive_m2s
        return self.active_m2s / both if both > 0 else None

    def as_dict(self) -> dict:
        return {"slot": self.slot, "team": self.team, "side": self.side, "alive_s": _r(self.alive_s, 3),
                "active_m2s": _r(self.active_m2s), "passive_m2s": _r(self.passive_m2s),
                "control_m2s": _r(self.control_m2s),
                "active_m2": _r(self.per_second(self.active_m2s)), "passive_m2": _r(self.per_second(self.passive_m2s)),
                "control_m2": _r(self.per_second(self.control_m2s)), "active_ratio": _r(self.active_ratio, 3),
                "taken_m2": _r(self.taken_m2), "deaths": self.deaths}


def _r(value, digits: int = 1):
    return None if value is None else round(float(value), digits)


@dataclass
class RoundControl:
    round: int | None
    map: str
    ticks: np.ndarray               # tick times (s, replay clock)
    weights: np.ndarray             # seconds to the next tick
    walk_cells: np.ndarray          # flat cell index of each walkable cell (the columns below)
    states: np.ndarray              # ticks x walkable cells, uint8 state codes
    control: np.ndarray             # ticks x 10, control in m2 (NaN when not alive)
    control_masks: np.ndarray       # ticks x 10 x walkable cells: the cells a player's removal loses
    coverage_masks: np.ndarray      # ticks x 10 x walkable cells: active or passive coverage
    sections: list
    players: dict                   # slot -> PlayerStats
    # side group -> m2*s of the team's own area minus its players' control (signed)
    redundant_m2s: dict
    group_side: dict                # side group -> "attack" / "defense" (from the link)
    cell_m2: float
    missing_inputs: dict
    timings: dict                   # seconds in total, by part
    cf_check: dict                  # incremental vs full on the checked ticks
    revision: int = CONTROL_REVISION
    # what each side group knew (R3.3): ticks x walkable cells, and each enemy's sightings
    knew_states: dict | None = None     # side group -> uint8 states in that team's picture
    knew_sightings: dict | None = None  # side group -> {enemy slot: [[t0, t1, u, v], ...]}
    unknown: dict | None = None         # side group -> ticks x walkable cells, bool: its unknown


def _section_bounds(rnd: RoundInputs) -> list[tuple[str, float, float]]:
    """Heatmap sections of the live round: SECTION_S by the round clock up to the plant, then by
    the spike clock."""
    lo, hi = rnd.t_start, rnd.t_decided
    out = []
    plant = rnd.plant if rnd.plant is not None and lo < rnd.plant < hi else None
    edge = plant if plant is not None else hi
    t, i = lo, 0
    while t < edge:
        out.append((f"r{i}", t, min(t + SECTION_S, edge)))
        t, i = t + SECTION_S, i + 1
    if plant is not None:
        t, i = plant, 0
        while t < hi:
            out.append((f"p{i}", t, min(t + SECTION_S, hi)))
            t, i = t + SECTION_S, i + 1
    return out


def compute_round(blob: dict, geo: Geometry, link: ControlLink | None = None, *,
                  ticks: np.ndarray | None = None, full_every: int = 0, knowledge: bool = True) -> RoundControl:
    """Control for one round. `ticks` overrides the Q75 schedule (tests, parity checks);
    `full_every` N > 0 also runs the full counterfactual on every Nth tick and compares. `knowledge`
    also builds each team's picture of the round (R3.3): what it knew, not the true positions."""
    visibility(geo)
    rnd = RoundInputs(blob, geo, link)
    times = rnd.tick_times() if ticks is None else np.asarray(ticks, float)
    nxt = np.append(times[1:], rnd.t_end)
    weights = np.maximum(nxt - times, 0.0)
    walk_flat = geo.walk.ravel()
    walk_cells = np.flatnonzero(walk_flat)
    n_walk, n_ticks = len(walk_cells), len(times)
    cell_m2 = geo.cell_m ** 2
    states = np.zeros((n_ticks, n_walk), np.uint8)
    control = np.full((n_ticks, 10), np.nan, np.float32)
    control_masks = np.zeros((n_ticks, 10, n_walk), bool)
    coverage_masks = np.zeros((n_ticks, 10, n_walk), bool)
    timings: dict[str, float] = defaultdict(float)
    branches: dict[str, int] = defaultdict(int)
    cf_check = {"player_ticks": 0, "identical": 0, "max_cells_differing": 0}
    players = {s: PlayerStats(s, rnd.team[s], rnd.group_side.get(rnd.team[s])) for s in sorted(rnd.team)}
    redundant = {"A": 0.0, "B": 0.0}
    bounds = _section_bounds(rnd)
    sections = [Section(k, a, b, 0.0, np.zeros((N_STATES, n_walk), np.float32)) for k, a, b in bounds]
    death_ticks: dict[int, list[tuple[int, float]]] = defaultdict(list)
    index = {round(float(t), 6): i for i, t in enumerate(times)}
    for slot, t in rnd.deaths():
        i = index.get(round(snap_before(t), 6))
        if i is not None:
            death_ticks[i].append((slot, t))
    prev_state = None
    memory = Memory(geo)
    unknown = Unknown(geo)
    unknown_masks = {side: np.zeros((n_ticks, n_walk), bool) for side in ("A", "B")}
    know ={side: Knowledge(rnd, side) for side in ("A", "B")} if knowledge else {}
    knew_states = {side: np.zeros((n_ticks, n_walk), np.uint8) for side in know}

    for n, t in enumerate(times):
        t = float(t)
        tick = Tick(rnd, t, timings)
        a = time.perf_counter()
        if n == 0:
            areas = barrier_start(geo, tick)
            memory.begin(areas)
            unknown.begin(areas)
        unknown.apply(tick)                   # before memory: live vision only clears it
        timings["unknown"] += time.perf_counter() - a
        a = time.perf_counter()
        memory.apply(tick, unknown.cells)
        timings["memory"] += time.perf_counter() - a
        tick.unknown = {side: cells.copy() for side, cells in unknown.cells.items()}
        for side in ("A", "B"):
            unknown_masks[side][n] = tick.unknown[side][walk_flat]
        a = time.perf_counter()
        base = tick.compose()
        b = time.perf_counter()
        cov = tick.coverage()
        timings["base"] += b - a
        timings["coverage"] += time.perf_counter() - b
        state = base["state"]
        states[n] = state[walk_flat]
        if prev_state is not None and rnd.t_start < t <= rnd.t_decided:
            _credit_taken(tick, cov, prev_state, state, players, cell_m2)
        prev_state = state
        if know:
            k0 = time.perf_counter()
            for side, kn in know.items():
                knew_states[side][n] = kn.tick_for(tick, t).compose()["state"][walk_flat]
            timings["knowledge"] += time.perf_counter() - k0
        live_w =max(0.0, min(t + weights[n], rnd.t_decided) - max(t, rnd.t_start))
        owned = {s: float((score(state, s) > 0).sum()) for s in ("A", "B")}
        ctl_sum = {"A": 0.0, "B": 0.0}
        dying = {slot: t_death for slot, t_death in death_ticks.get(n, [])}
        for s, h in tick.holders.items():
            side = h.team
            act, psv, act_mask, pas_mask = cov[s]
            coverage_masks[n, s] = (act_mask | pas_mask)[walk_flat]
            c0 = time.perf_counter()
            cf = tick.compose(removed=s, base=base, full=False, stats=branches)
            timings["cf_incremental"] += time.perf_counter() - c0
            if full_every and n % full_every == 0:
                c1 = time.perf_counter()
                full = tick.compose(removed=s)
                timings["cf_full"] += time.perf_counter() - c1
                diff = int((full["state"] != cf["state"]).sum())
                cf_check["player_ticks"] += 1
                cf_check["identical"] += diff == 0
                cf_check["max_cells_differing"] = max(cf_check["max_cells_differing"], diff)
            base_score = score(state, side)
            last = not tick.team(side, s)
            if last:  # Q63: no terminal flip; the last player controls only what the team loses
                drop = (base_score > 0).astype(np.int8)
            else:
                drop = base_score - score(cf["state"], side)
            value = float(drop.sum())
            control[n, s] = value * cell_m2
            control_masks[n, s] = (drop > 0)[walk_flat]
            ctl_sum[side] += value
            if s in dying:
                players[s].deaths.append(_lost(state, cf["state"], side, drop, last, owned[side], cell_m2,
                                               dying[s], rnd))
            # this player's share of the tick: up to their death, inside the live round
            own_w = max(0.0, min(t + weights[n], rnd.t_decided, rnd.life_end(s, t)) - max(t, rnd.t_start))
            if own_w > 0:
                ps = players[s]
                ps.alive_s += own_w
                ps.active_m2s += act * cell_m2 * own_w
                ps.passive_m2s += psv * cell_m2 * own_w
                ps.control_m2s += value * cell_m2 * own_w
        if live_w > 0:
            for side in ("A", "B"):
                if any(h.team == side for h in tick.holders.values()):
                    redundant[side] += (owned[side] - ctl_sum[side]) * cell_m2 * live_w
            lo, hi = max(t, rnd.t_start), min(t + weights[n], rnd.t_decided)
            cols = np.arange(n_walk)
            for sec in sections:
                overlap = min(hi, sec.t1) - max(lo, sec.t0)
                if overlap > 0:
                    sec.seconds += overlap
                    np.add.at(sec.totals, (states[n], cols), overlap)
    missing = dict(rnd.missing)
    return RoundControl(blob.get("round"), blob.get("map", geo.name), times, weights, walk_cells, states, control,
                        control_masks, coverage_masks, sections, players, redundant, dict(rnd.group_side), cell_m2,
                        missing, {**timings, "branches": dict(branches)}, cf_check,
                        knew_states=knew_states or None, unknown=unknown_masks,
                        knew_sightings={side: {s: runs for s, runs in kn.sightings.items()}
                                        for side, kn in know.items()} or None)


def _credit_taken(tick: Tick, cov: dict, prev: np.ndarray, state: np.ndarray, players: dict, cell_m2: float,
                  already: dict | None = None) -> None:
    """Space taken (the plan's stretch stat; docs/map-control-space-taken-impl.md): cells that were the
    enemy's or nobody's at the previous tick and are the team's now, shared evenly among the team's
    players whose coverage (active, passive or own utility) includes them at this tick. Ground that
    became the team's with nobody watching it (the lines moved) is taken by nobody. Every flip counts
    (D6, 2026-09-30): memory keeps ground a cone swept past, so a flip is ground really won, and ground
    that decayed and was won back counts again. With `already` (slot -> the cells credited so far),
    each cell counts once per player instead (the earlier rule, kept for comparisons)."""
    # Contested -> ours doesn't count ("from the enemy or nobody"); unwatched flips go to nobody.
    for side in ("A", "B"):
        hs = [h for h in tick.holders.values() if h.team == side and h.slot in cov]
        if not hs:
            continue
        before = score(prev, side)
        flipped = (score(state, side) > 0) & ((before < 0) | (prev == NONE))
        if not flipped.any():
            continue
        seen = {h.slot: cov[h.slot][2] | cov[h.slot][3] for h in hs}
        count = sum(m.astype(np.int16) for m in seen.values())
        for h in hs:
            mine = flipped & seen[h.slot]
            if already is not None:
                done = already.setdefault(h.slot, np.zeros(len(state), bool))
                mine &= ~done
                done |= mine
            if mine.any():
                players[h.slot].taken_m2 += float((1.0 / count[mine]).sum()) * cell_m2


def _lost(state: np.ndarray, cf_state: np.ndarray, side: str, drop: np.ndarray, last: bool, owned: float,
          cell_m2: float, t_death: float, rnd: RoundInputs) -> dict:
    """Lost control at a real death: what the team lost, by the level it held and where it went."""
    lost = drop > 0
    lv = level_of(state, side)
    by_level = {name: float((lost & (lv == code)).sum()) * cell_m2
                for name, code in (("active", 3), ("safe", 2), ("passive", 1))}
    by_level["not_held"] = float((lost & (lv == 0)).sum()) * cell_m2
    theirs = score(cf_state, side) < 0
    contested = (cf_state == CONTESTED) | (cf_state == CONTESTED_ACTIVE)
    if last:  # Q63: the enemy's gain doesn't count, so what the last player held goes to nobody
        went = {"enemy": 0.0, "contested": 0.0, "nobody": float(lost.sum()) * cell_m2}
    else:
        went = {"enemy": float((lost & theirs).sum()) * cell_m2,
                "contested": float((lost & contested).sum()) * cell_m2,
                "nobody": float((lost & ~theirs & ~contested).sum()) * cell_m2}
    value = float(drop.sum()) * cell_m2
    return {"t": round(t_death, 3), "live": rnd.t_start <= t_death <= rnd.t_decided,
            "control_m2": round(value, 1),
            "share_of_team": round(value / (owned * cell_m2), 4) if owned > 0 else None,
            "by_level_m2": {k: round(v, 1) for k, v in by_level.items()},
            "went_to_m2": {k: round(v, 1) for k, v in went.items()}}
