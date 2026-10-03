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
  its own spread at UNKNOWN_MPS (each cell no sooner than a walk of its true length, diagonals sqrt(2);
  across specials, through smokes but not through a pinch under GAP_SEAL_M between a smoke or wall
  ability and the map's wall). The team's live control clears it on contact. It is kept per enemy: one
  the team spots starts again from where they were seen, and a dead one's goes with them. An enemy is also
  located, as an area round them, by a kill, the plant, audible movement, gunfire within hearing and gun
  damage (the timing-gaps spec, section 4); a revived enemy restarts where they stand.
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

**Heights** (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 4). On a map with a
height asset every per-place array is per node (a floor of a cell; geometry.py), a player stands on the
floor under their own z and sees from their own eye height, an enemy is spotted on the node they stand on,
and walking, the unknown, memory, backfill and Safe keep floors apart (topology.py). Only the stored
result is per cell: `collapse_states`. A round without z (before condenser revision 11) uses each cell's
lowest floor and is counted in `missing_inputs`. A map without an asset is untouched by any of this.

Inputs the condenser added in revision 10 (flash/nearsight `hits`, `possessed`, `yaws`, `damage`
rows) are used when present; older blobs fall back to 0b's placeholders, counted in
`missing_inputs`. Local tooling only: the web app never imports this module.
"""

from __future__ import annotations

import bisect
import copy
import json
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from scipy import ndimage
from app.control import chokes
from app.control import heights as hc
from app.control import observe
from app.control import topology
from app.control.routes import RouteLog
from app.replays import choke_assets
from app.control.geometry import CELL, GRID, PX, RAY_STEP_DEG, Geometry, Wall, cast, los, visibility, wall_blocks
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
# at shift-walk with a rifle out (the user's call, 2026-10-01): measured from replay tracks, walking is
# 0.60 x running in every tier, knife 6.75 -> 4.05, pistol 5.73 -> 3.43, rifle 5.40 -> 3.24 m/s.
UNKNOWN_MPS = 3.24
# Presence (the user's call, 2026-10-01): an enemy can't walk past a player within arm's reach unseen, so
# each live player holds the walkable ground within this radius as passive (not while flashed).
PRESENCE_M = 4.0
# A pinch between a sight blocker (a smoke's edge, a wall ability's line) and the map's wall narrower than
# this is sealed to the unknown: nobody squeezes through it unseen (the user's call, 2026-10-01). The gas
# itself stays walkable.
GAP_SEAL_M = 1.5
# A piece of a team's unknown this small (cells, 8-connected; a 1x2) with no enemy in it is dropped
# (the user's call, 2026-10-01: what vision has eaten down to that is gone).
DROP_PIECE_CELLS = 2
# Locating events (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 4): what a team hears
# or learns from the feed collapses that enemy's unknown to an area round them. Ranges: app/control/hearing.json.
KILL_AREA_M = 5.0
PLANT_AREA_M = 5.0
SHOT_AREA_M = 5.0
DAMAGE_AREA_M = 10.0
FOOTSTEP_AREA_M = 10.0
AUDIBLE_MPS = 4.5            # above the fastest shift-walk (knife, 4.05 m/s), below the slowest run (rifle, 5.40)
AUDIBLE_WINDOW_S = 0.5
_HEARING_FILE = json.loads((Path(__file__).with_name("hearing.json")).read_text(encoding="utf-8"))
# only the numeric fields (pinned with the other constants): editing a citation in `sources` changes nothing
HEARING = {"footstep_range_m": float(_HEARING_FILE["footstep_range_m"]),
           "default_gun_m": float(_HEARING_FILE["default_gun_m"]),
           "guns": {str(k): float(v) for k, v in _HEARING_FILE["guns"].items()}}
FOOTSTEP_RANGE_M = HEARING["footstep_range_m"]
GUN_HEARING_M = dict(HEARING["guns"])
GUN_HEARING_DEFAULT_M = HEARING["default_gun_m"]     # a gun named but not in the table (a shot with no gun: the longest)
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
    eye: float | None = None             # a fixed device's eye height (m, as node_z); None: 2D
    node: int | None = None              # the node it sits on (heights only)
    origin: int = 0                      # the map's origin_z (world dm), for a drone path's heights


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
        self._segment: dict[int, np.ndarray] = {}   # slot -> each sample's track segment index
        self.tracks = {}
        self.heights: dict[int, np.ndarray] = {}     # slot -> position-z per sample, metres above the map's origin
        origin = geo.heights.origin_z if geo.heights is not None else 0
        for s, segs in (blob.get("tracks") or {}).items():
            parts = [(g["t0"] + np.arange(len(g["u"])) / self.hz, _decode(g["u"]), _decode(g["v"]),
                      np.mod(_decode(g["yaw"]), 360)) for g in segs]
            self.tracks[int(s)] = tuple(np.concatenate([p[i] for p in parts]) for i in range(4))
            self._segment[int(s)] = np.concatenate([np.full(len(g["u"]), i) for i, g in enumerate(segs)]) if segs \
                else np.zeros(0, int)
            if geo.heights is not None and segs:
                # NaN where a segment was stored without z (before revision 11, or the parser gave none)
                self.heights[int(s)] = np.concatenate([
                    (_decode(g["z"]) - origin) / 10.0 if "z" in g else np.full(len(g["u"]), np.nan) for g in segs])
        self.lives = self._lives()
        self.smokes, self.damage_zones, self.watchers = [], [], []
        self.walls: list[tuple[float, float, Wall]] = []
        self.flashed, self.nearsight = defaultdict(list), defaultdict(list)
        self.downgraded, self.contest_status = defaultdict(list), defaultdict(list)
        self.hit_contest = defaultdict(list)
        self.missing: dict[str, int] = defaultdict(int)
        self._missed: set[tuple[str, int | None]] = set()    # (case, slot) already counted (R20)
        self.events: list[float] = []
        self.shot_times: list[float] = []
        self.plant: float | None = None
        # locating events (timing gaps, section 4)
        self.planter: tuple[float, int] | None = None     # (time, slot) of the plant, when its owner is known
        self.shots: list[tuple[float, int, str | None]] = []                 # (t, by, gun)
        self.gun_runs: list[tuple[float, float, int, int, bool]] = []       # (t, t1, by, target, wall), enemies only
        self.kills = [(float(k["t"]), int(k["killer"]), int(k["victim"])) for k in blob.get("kills") or []
                      if k.get("killer") is not None and k.get("victim") is not None]
        self._by_slot: dict[int, tuple[list[float], list[tuple]]] | None = None
        self.reveals: list[tuple[float, float, int | None, int]] = []   # (t0, t1, by, target)
        self._read_util()
        if geo.heights is not None:
            if any(np.isnan(z).any() for z in self.heights.values()):
                self.missing["approximate heights (a player's track has no z: lowest floor used)"] += 1
            if geo.height_unknown:
                self.missing["height asset built on another walk mask (flat 2D in the cells it lacks)"] += 1

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

    def revival(self, s: int, t: float) -> tuple[float, float, float] | None:
        """Where and when slot s, alive at t in a later life, is first located in it (timing gaps, section 4;
        R14): at the life's start when there is a sample then, else at the first sample since (if by t), as
        (time, x px, y px); None without one yet."""
        a = next((a for a, b in self.lives.get(s, []) if a <= t < b), None)
        if a is None:
            return None
        p = self.pos(s, a)
        if p is not None:
            return float(a), p[0], p[1]
        tr = self.tracks.get(s)
        if tr is None:
            return None
        i = int(np.searchsorted(tr[0], a))
        if i >= len(tr[0]) or tr[0][i] > t:
            return None
        return float(tr[0][i]), tr[1][i] * PX / 10000, tr[2][i] * PX / 10000

    def deaths(self) -> list[tuple[int, float]]:
        return sorted(((s, b) for s, ivs in self.lives.items() for _, b in ivs if b != math.inf),
                      key=lambda d: (d[1], d[0]))

    # --- positions

    def _sample(self, s: int, t: float) -> int | None:
        """The index of slot s's sample nearest t (within a sample and a half), or None."""
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
        return best

    def pos(self, s: int, t: float):
        best = self._sample(s, t)
        if best is None:
            return None
        tr = self.tracks[s]
        return tr[1][best] * PX / 10000, tr[2][best] * PX / 10000, float(tr[3][best])

    def height(self, s: int, t: float) -> float | None:
        """Slot s's position-z at t in metres above the map's origin; None on a flat map, or when the
        round has no z for them there."""
        best = self._sample(s, t) if s in self.heights else None
        if best is None or np.isnan(self.heights[s][best]):
            return None
        return float(self.heights[s][best])

    def node(self, s: int, t: float, x: float, y: float) -> int:
        """The node slot s stands on at t: the floor of their cell under their z (the lowest without z)."""
        return self.geo.node_at(self.geo.cell_of_px(x, y), self.height(s, t))

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

    def speed(self, s: int, t: float) -> float | None:
        """Metres per second over the last AUDIBLE_WINDOW_S, within one track segment (a break is never
        bridged, so a teleport is not a footstep); None without two samples in the same segment."""
        a, b = self._sample(s, t - AUDIBLE_WINDOW_S), self._sample(s, t)
        if a is None or b is None or self._segment[s][a] != self._segment[s][b]:
            return None
        tr = self.tracks[s]
        dt = tr[0][b] - tr[0][a]
        if dt <= 0:
            return None
        dist_px = math.hypot((tr[1][b] - tr[1][a]) * PX / 10000, (tr[2][b] - tr[2][a]) * PX / 10000)
        return float(dist_px * self.geo.m_per_px / dt)

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
                    if e.get("by") is not None:
                        self.planter = (float(e["t"]), int(e["by"]))
                    else:
                        self.miss("plant without a planter (locates nobody)", None)
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
                if e.get("by") is not None:
                    self.shots.append((float(e["t"]), int(e["by"]), e.get("gun")))
                    if e.get("gun") is None:
                        self.miss("shot without a gun (longest hearing range used)", int(e["by"]))
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
        if e.get("src") == "gun":
            self.gun_runs.append((float(e["t"]), float(t1), int(by), int(target), bool(e.get("wall"))))
            # every gun damage run gets a tick, the first at or after its start, so it is applied there (section 4)
            self.events.append(snap_after(e["t"]))

    def miss(self, case: str, slot: int | None) -> None:
        """Count a missing-data case once per (case, slot) per round (R20), however often it is met."""
        if (case, slot) not in self._missed:
            self._missed.add((case, slot))
            self.missing[case] += 1

    def locating_rows(self, slot: int) -> tuple[list[float], list[tuple]]:
        """Slot's possible locating events, time-sorted, as (times, rows); a row is (t, kind, detail):
        `kill` (victim), `plant` (None), `damage` (target), `gunfire` (gun)."""
        if self._by_slot is None:
            rows: dict[int, list[tuple]] = defaultdict(list)
            for te, killer, victim in self.kills:
                rows[killer].append((te, "kill", victim))
            if self.planter is not None:
                rows[self.planter[1]].append((self.planter[0], "plant", None))
            for te, _, by, target, _ in self.gun_runs:
                rows[by].append((te, "damage", target))
            for te, by, gun in self.shots:
                rows[by].append((te, "gunfire", gun))
            self._by_slot = {}
            for s, rs in rows.items():
                rs.sort(key=lambda r: r[0])
                self._by_slot[s] = ([r[0] for r in rs], rs)
        return self._by_slot.get(slot, ([], []))

    def _watcher(self, e: dict, key: str, t0: float, t1: float) -> None:
        px_per_uv = PX / 10000
        geo = self.geo
        by = e.get("by")
        if by is None:
            return
        x, y = e["u"] * px_per_uv, e["v"] * px_per_uv
        # With heights every watcher gets a floor (the one nearest at or below its z) and an eye (its own z
        # + DEVICE_EYE_M); one with no z sits on its cell's lowest floor, sees in 2D and marks the round.
        z = self._device_z(e.get("z"))
        node = eye = None
        if geo.heights is not None:
            node = geo.node_at(geo.cell_of_px(x, y), None if z is None else z + hc.STAND_M)
            eye = None if z is None or np.isnan(geo.node_z[node]) else z + hc.DEVICE_EYE_M
        if key == TRIPWIRE and e.get("end"):
            a = np.array([x, y])
            b = np.array(e["end"]) * px_per_uv
            z_end = self._device_z(e.get("end_z"))
            self._no_z(z, z_end)
            self.watchers.append(Watcher("trip", by, t0, t1, cells=self._trip_nodes(a, b, z, z_end)))
        elif key in AREA_TRIPS:
            r_px = AREA_TRIPS[key] / geo.m_per_px
            c = geo.cell_of_px(x, y)
            near = ((geo.centres[:, 0] - x) ** 2 + (geo.centres[:, 1] - y) ** 2) < r_px ** 2
            if geo.heights is None:
                if geo.row_of[c] >= 0:
                    row = np.unpackbits(geo.rows[geo.row_of[c]])[: geo.n].astype(bool)
                    self.watchers.append(Watcher("area", by, t0, t1, cells=np.flatnonzero(near & row)))
            elif geo.walk_n[node]:
                # it reaches nodes by walking on its own floor, and sees them with the ray test
                self._no_z(z)
                # with no z (eye None) the cast is the 2D one; it needs no visibility rows, which a
                # geometry that just had heights attached doesn't have yet (the height build's checks)
                seen = cast(geo, x, y, np.arange(0, 360, RAY_STEP_DEG), [], eye_z=eye, own=node)
                start = np.zeros(geo.n, bool)
                start[node] = True
                steps = int(math.ceil(AREA_TRIPS[key] / geo.cell_m)) + 1
                walked = topology.of(geo).dilate(start, eight=True, iterations=steps, within=geo.walk_n)
                self.watchers.append(Watcher("area", by, t0, t1, cells=np.flatnonzero(near & seen & walked)))
        elif key in DRONES and e.get("path"):
            half, rng = DRONES[key]
            in_use = [_span(a, b if b is not None else t1) for a, b in e["possessed"]] if "possessed" in e else None
            if in_use is None:
                self.missing["drone flown intervals (whole life used)"] += 1
            if geo.heights is not None and any(len(point) < 4 for point in e["path"]):
                self.missing["approximate heights (a watcher has no z: 2D sight used)"] += 1
            self.watchers.append(Watcher("drone", by, t0, t1, path=e["path"], yaws=e.get("yaws"), in_use=in_use,
                                         half=half, range_m=rng,
                                         origin=geo.heights.origin_z if geo.heights is not None else 0))
        elif key == CAMERA:
            if "possessed" not in e:
                self.missing["Cypher camera: no in-use intervals (not modelled)"] += 1
                return
            in_use = [_span(a, b if b is not None else t1) for a, b in e["possessed"]]
            if "yaws" not in e:
                self.missing["camera yaw over time (placed yaw used)"] += 1
            self._no_z(z)
            self.watchers.append(Watcher("camera", by, t0, t1, x=x, y=y, yaw=e.get("yaw"), yaws=e.get("yaws"),
                                         in_use=in_use, half=FOV_HALF, eye=eye, node=node))
        elif key == TURRET:
            if "yaws" not in e:
                self.missing["turret yaw over time (placed yaw used)"] += 1
            if e.get("yaw") is None and not e.get("yaws"):
                return
            self._no_z(z)
            self.watchers.append(Watcher("turret", by, t0, t1, x=x, y=y, yaw=e.get("yaw"), yaws=e.get("yaws"),
                                         half=TURRET_HALF, eye=eye, node=node))

    def _device_z(self, z_dm) -> float | None:
        """A stored utility height (world dm) in metres above the map's origin; None on a flat map, and
        with no stored height."""
        if self.geo.heights is None:
            return None
        if z_dm is None:
            return None
        return (z_dm - self.geo.heights.origin_z) / 10.0

    def _no_z(self, *heights) -> None:
        """Marks the round when a watcher on a map with heights was stored without one."""
        if self.geo.heights is not None and any(z is None for z in heights):
            self.missing["approximate heights (a watcher has no z: 2D sight used)"] += 1

    def _trip_nodes(self, a: np.ndarray, b: np.ndarray, za: float | None, zb: float | None) -> np.ndarray:
        """The nodes a tripwire from a to b (px) watches. On a flat map, or with no anchor heights: the
        cells under the 2D line (every floor of them). With heights the wire runs between its anchors'
        heights and each end is first extended (`_trip_end`); it watches, in each cell it crosses, the
        floor nearest at or below it."""
        geo = self.geo
        heights = geo.heights is not None and za is not None and zb is not None
        if heights:
            length = float(np.hypot(*(b - a))) * geo.m_per_px
            slope = (zb - za) / length if length > 0 else 0.0
            a2, b2 = self._trip_end(a, za, b, zb), self._trip_end(b, zb, a, za)
            za += -slope * float(np.hypot(*(a2 - a))) * geo.m_per_px
            zb += slope * float(np.hypot(*(b2 - b))) * geo.m_per_px
            a, b = a2, b2
        n = int(np.abs(b - a).max()) + 1
        xs = np.linspace(a[0], b[0], n)
        ys = np.linspace(a[1], b[1], n)
        cells = (ys // CELL).astype(int).clip(0, GRID - 1) * GRID + (xs // CELL).astype(int).clip(0, GRID - 1)
        if not heights:
            return _every_floor(geo, np.unique(cells))
        zs = np.linspace(za, zb, n)
        return np.unique([geo.node_at(int(c), float(z) + hc.STAND_M) for c, z in zip(cells, zs)])

    def _trip_end(self, end: np.ndarray, z_end: float, other: np.ndarray, z_other: float) -> np.ndarray:
        """Where a wire's end really is (the spec's "Trips"): from `end` on along the wire's own line, over
        the floor it was anchored on, to where that floor's ground rises to within TRIP_HIT_M of the wire,
        or a 2D wall, or a floor that isn't connected to the anchor's; at most TRIP_REACH_M. With none of
        those in reach, or an unresolved cell on the way, the end stays where it is."""
        geo = self.geo
        topo = topology.of(geo)
        span = float(np.hypot(*(end - other)))
        if span < 1e-6:
            return end
        ux, uy = (end - other) / span
        slope = (z_end - z_other) / (span * geo.m_per_px)
        cell = geo.cell_of_px(end[0], end[1])
        if geo.unresolved[cell]:
            return end
        node = geo.node_at(cell, z_end + hc.STAND_M)
        for step in range(1, int(hc.TRIP_REACH_M / geo.m_per_px) + 1):
            x, y = end[0] + ux * step, end[1] + uy * step
            if not (0 <= x < PX and 0 <= y < PX):
                return end
            here = np.array([x, y])
            if geo.sight[int(y), int(x)]:
                return np.array([x - ux, y - uy])             # a 2D wall: the last open point before it
            cell = geo.cell_of_px(x, y)
            if cell != geo.node_cell[node]:
                if geo.unresolved[cell]:
                    return end                                # uncertain terrain: no extension
                onward = [int(m) for m in geo.node_of[cell] if m >= 0 and int(m) in topo.around(node)]
                if not onward:
                    return np.array([x - ux, y - uy])         # the anchor's floor ends: its last point
                node = min(onward, key=lambda m: abs(geo.node_z[m] - geo.node_z[node]))
            wire = z_end + slope * step * geo.m_per_px
            if geo.node_z[node] - hc.STAND_M >= wire - hc.TRIP_HIT_M:
                return here                                   # the ground has risen to the wire
        return end

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


def path_z(path: list, t: float) -> float | None:
    """The stored height (world dm) along a [[t, u, v, z], ...] path at t; None outside it, or where
    either point beside t was stored without one."""
    if not path or t < path[0][0] or t > path[-1][0]:
        return None
    for i in range(1, len(path)):
        if path[i][0] >= t:
            p, q = path[i - 1], path[i]
            if len(p) < 4 or len(q) < 4:
                return None
            f = (t - p[0]) / (q[0] - p[0]) if q[0] > p[0] else 0
            return p[3] + (q[3] - p[3]) * f
    return path[0][3] if len(path) == 1 and len(path[0]) > 3 else None


# ---------------------------------------------------------------- one tick


@dataclass
class Holder:
    slot: int
    team: str
    cell: int                # the node they stand on (a cell on a flat map; a floor of one with heights)
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
    free: np.ndarray                             # flat, per node
    comps: list = field(default_factory=list)    # (mask flat, seen flat or None, slots in it)
    seen: np.ndarray = None                      # flat; None on a tick with unknown (nothing reads it)

    def safe(self, walk: np.ndarray) -> np.ndarray:
        return walk & ~self.seen & ~self.free


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
        return np.zeros(geo.n, bool)
    packed = geo.rows[geo.row_of[src]]
    static = np.unpackbits(np.bitwise_or.reduce(packed, axis=0))[: geo.n].astype(bool)
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
        rows = np.unpackbits(packed[lo:lo + 256], axis=1)[:, : geo.n][:, targets].astype(bool)
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
        if geo.heights is None:
            out.append((a, b, bool(sp.get("one_way"))))
        else:   # the 2D specials apply to every floor of their cells (the spec: "Walking per floor")
            out += [(int(na), int(nb), bool(sp.get("one_way")))
                    for na in geo.node_of[a] if na >= 0 for nb in geo.node_of[b] if nb >= 0]
    return out


def _every_floor(geo: Geometry, cells: np.ndarray) -> np.ndarray:
    """Flat cells as node indices: every floor of each (the cells themselves on a flat map)."""
    if geo.heights is None:
        return cells
    nodes = geo.node_of[cells].ravel()
    return nodes[nodes >= 0]


def collapse_states(geo: Geometry, state: np.ndarray) -> np.ndarray:
    """A tick's node states as cell states, for the stored picture (the spec's two-floor rule): a cell
    whose floors all agree has that state; otherwise it is contested, CONTESTED_ACTIVE when any of its
    floors is active for either team (or contested-active). Nobody's against held counts as disagreeing.
    A flat map's states are returned as they are."""
    if geo.n == GRID * GRID:
        return state
    out = state[: GRID * GRID].copy()
    cells = geo.node_cell[GRID * GRID:]
    upper = state[GRID * GRID:]
    differs = np.zeros(GRID * GRID, bool)
    differs[cells[upper != out[cells]]] = True
    active = np.isin(state, (A_ACTIVE, B_ACTIVE, CONTESTED_ACTIVE))
    out[differs] = np.where(geo.to_cells(active)[differs], CONTESTED_ACTIVE, CONTESTED)
    return out


class Tick:
    """The inputs of one tick, and `compose` (the state) with or without a removed player."""

    def __init__(self, rnd: RoundInputs, t: float, timings: dict | None = None):
        geo = self.geo = rnd.geo
        self.topo = topology.of(geo)
        self.rnd, self.t = rnd, t
        self.smokes = rnd.smokes_at(t)
        self.fallbacks: dict = {}     # "unresolved_rays": rays that met unresolved terrain and went 2D
        timings = timings if timings is not None else defaultdict(float)
        started = time.perf_counter()
        self.holders: dict[int, Holder] = {}
        looks: dict[int, tuple] = {}   # slot -> (yaw, eye, flashed, nearsighted, active cone's half or None)
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
            cell = rnd.node(s, t, x, y)
            mode = rnd.movement(s, t, p)
            if s in using:
                raw = np.zeros(geo.n, bool)  # in a camera or drone: their own view doesn't count
            else:
                raw = cast(geo, x, y, yaw + np.arange(-FOV_HALF, FOV_HALF + 1e-9, RAY_STEP_DEG), self.smokes,
                           eye_z=self._eye(s, t, cell), own=cell, record=self.fallbacks)
            body = raw.copy()
            if _during(rnd.flashed[s], t):
                body[:] = False
            elif _during(rnd.nearsight[s], t):
                r = NEARSIGHT_RADIUS_M / geo.m_per_px
                body &= ((geo.centres[:, 0] - x) ** 2 + (geo.centres[:, 1] - y) ** 2) < r * r
            if _during(rnd.downgraded[s], t):
                active = np.zeros(geo.n, bool)
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
            if geo.heights is not None and s not in using:
                looks[s] = (yaw, self._eye(s, t, cell), _during(rnd.flashed[s], t), _during(rnd.nearsight[s], t),
                            None if _during(rnd.downgraded[s], t) else CONE_HALF[mode])
        # (viewer, enemy) -> in the viewer's active cone: enemies seen at their own height, above their floor
        self.direct: dict[tuple[int, int], bool] = self._direct(looks) if looks else {}
        timings["vision"] += time.perf_counter() - started
        # slot -> the live control that holds unknown back: vision, watchers and their own cell (before
        # Memory adds remembered ground to `passive`)
        self.live: dict[int, np.ndarray] = {}
        # slot -> the player's own view (active | passive, presence included), before Memory: what the gap
        # detector credits to vision (timing gaps, R12)
        self.view: dict[int, np.ndarray] = {}
        for s, h in self.holders.items():
            self.view[s] = h.active | h.passive
            lv = h.active | h.passive | h.watch
            lv[h.cell] = True
            self.live[s] = lv
        # enemy sight of each holder: sees[e] = the holders e's body view reaches
        self.sees = {e.slot: {h.slot for h in self.holders.values()
                              if h.team != e.team and (e.body[h.cell] or (e.slot, h.slot) in self.direct)}
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
        # flat cells in a pinch (Unknown.sealed) at this tick, so the counterfactual's unknown keeps them
        # shut too; set with `unknown` by compute_round
        self.sealed: np.ndarray | None = None
        self._usafe: dict = {}   # side, or (side, removed slot) -> its Safe cells from unknown
        self._ucf: dict[tuple[str, int], np.ndarray] = {}   # (side, removed slot) -> unknown_without

    def _eye(self, s: int, t: float, node: int) -> float | None:
        """Slot s's eye height: their own z + EYE_M, the floor they stand on without z, None (2D) on a
        flat map or an unresolved cell."""
        if self.geo.heights is None:
            return None
        z = self.rnd.height(s, t)
        if z is None:
            z = self.geo.node_z[node]
        return None if np.isnan(z) else float(z) + hc.EYE_M

    def _direct(self, looks: dict) -> dict[tuple[int, int], bool]:
        """Enemies a player sees at the enemy's own height (a map with heights only). The view over nodes
        tests a body standing on each floor, which is right for ground and for anyone standing on it; an
        enemy above their floor (mid-jump, boosted, on a Sage wall) is tested here by the one exact line
        from the viewer's eye to their real body, under the same limits as the view: the field of view,
        a flash, nearsight, smokes and walls. It only ever adds a sighting."""
        geo, rnd, t = self.geo, self.rnd, self.t
        out = {}
        for s, (yaw, eye, flashed, nearsighted, cone) in looks.items():
            if eye is None or flashed:
                continue
            e = self.holders[s]
            for h in self.holders.values():
                z = rnd.height(h.slot, t) if h.team != e.team else None
                floor = geo.node_z[h.cell]
                if z is None or np.isnan(floor) or z - floor <= hc.STAND_TOL_M or e.body[h.cell]:
                    continue
                dx, dy = h.x - e.x, h.y - e.y
                off = abs((math.degrees(math.atan2(dy, dx)) - yaw + 180) % 360 - 180)
                if off > FOV_HALF or (nearsighted and math.hypot(dx, dy) * geo.m_per_px >= NEARSIGHT_RADIUS_M):
                    continue
                if los(geo, (e.x, e.y, eye), (h.x, h.y, z + hc.BODY_M), self.smokes, record=self.fallbacks):
                    out[(s, h.slot)] = cone is not None and off <= cone
        return out

    def _presence(self, x: float, y: float, cell: int) -> np.ndarray:
        """Walkable cells within PRESENCE_M of (x, y) px, reached by walking from the player's cell
        (8-connected) and in their line of sight (smoke-aware, all round them): anything that blocks
        sight stops the bubble (the user's call, 2026-10-01)."""
        geo = self.geo
        start = np.zeros(geo.n, bool)
        start[cell] = True
        steps = int(math.ceil(PRESENCE_M / geo.cell_m)) + 1
        reach = self.topo.dilate(start, eight=True, iterations=steps, within=geo.walk_n)
        r = PRESENCE_M / geo.m_per_px
        near = reach & (((geo.centres[:, 0] - x) ** 2 + (geo.centres[:, 1] - y) ** 2) <= r * r)
        sight = seen_from(geo, np.array([cell]), self.smokes)
        sight[cell] = True
        return near & sight

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
        own = np.zeros(self.geo.n, bool)
        enemy = np.zeros(self.geo.n, bool)
        for h in self.team(side, removed):
            own |= h.active | h.passive | h.watch
        for e in self.team(other, removed):
            enemy |= e.active | e.passive | e.watch
        extra = self.extra_passive.get(other) if removed is None else None
        if extra is not None:
            enemy |= extra
        lab = self.topo.label(self.geo.walk_n & ~own)
        sources = {int(lab[e.cell]) for e in self.team(other, removed)}
        seed = self.seeds.get(other) if removed is None else None
        if seed is not None:
            sources |= set(np.unique(lab[seed]).tolist())
        reach = self._reach(lab, sources - {0})
        pocket = ((lab > 0) & ~np.isin(lab, list(reach))) & ~enemy
        if self.unknown is not None:
            pocket &= ~self.unknown_for(side, removed)   # never where an enemy could be (docs/map-control-unknown-plan.md)
        return pocket

    def _backfill_shares(self, side: str, removed: int | None) -> dict[int, np.ndarray]:
        pocket = self._pocket(side, removed)
        if not pocket.any():
            return {}
        owner = np.full(self.geo.n, -1, np.int16)
        fronts = {}
        for h in sorted(self.team(side, removed), key=lambda h: h.slot):
            first = self.topo.dilate(h.active) & pocket & (owner < 0)
            if first.any():
                owner[first] = h.slot
                fronts[h.slot] = first
        while fronts:
            grown = {}
            for slot, front in sorted(fronts.items()):
                new = self.topo.dilate(front) & pocket & (owner < 0)
                if new.any():
                    owner[new] = slot
                    grown[slot] = new
            fronts = grown
        return {int(s): owner == s for s in np.unique(owner[owner >= 0]).tolist()}

    def _watch(self, s: int, t: float) -> np.ndarray:
        geo, rnd = self.geo, self.rnd
        watch = np.zeros(geo.n, bool)
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
                eye = own = None
                z_dm = path_z(w.path, t) if geo.heights is not None else None
                if z_dm is not None:      # a drone watches from its path's own height, over the floor below it
                    z = (z_dm - w.origin) / 10.0
                    own = geo.node_at(geo.cell_of_px(dx_, dy_), z + hc.STAND_M)
                    eye = None if np.isnan(geo.node_z[own]) else z + hc.DEVICE_EYE_M
                dv = cast(geo, dx_, dy_, heading + np.arange(-w.half, w.half + 1e-9, RAY_STEP_DEG), self.smokes,
                          eye_z=eye, own=own, record=self.fallbacks)
                if w.range_m:
                    r = w.range_m / geo.m_per_px
                    dv &= ((geo.centres[:, 0] - dx_) ** 2 + (geo.centres[:, 1] - dy_) ** 2) < r * r
                watch |= dv
            elif w.kind == "camera":
                if not _during(w.in_use, t):
                    continue
                yaw = _yaw_at(w, t, 0.0)
                watch |= cast(geo, w.x, w.y, yaw + np.arange(-w.half, w.half + 1e-9, RAY_STEP_DEG), self.smokes,
                              eye_z=w.eye, own=w.node, record=self.fallbacks)
            elif w.kind == "turret":
                yaw = _yaw_at(w, t, 0.0)
                watch |= cast(geo, w.x, w.y, yaw + np.arange(-w.half, w.half + 1e-9, RAY_STEP_DEG), self.smokes,
                              eye_z=w.eye, own=w.node, record=self.fallbacks)
        return watch

    # --- building blocks

    def team(self, side: str, removed: int | None) -> list[Holder]:
        return [h for h in self.holders.values() if h.team == side and h.slot != removed]

    def claims(self, side: str, removed: int | None):
        z = np.zeros(self.geo.n, bool)
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
        return special_links(self.geo) + self.topo.links

    def _reach(self, lab: np.ndarray, start: set[int]) -> set[int]:
        """Labels reachable from `start` through the map's specials (teleporters, ropes, drops)."""
        links = self._links()
        if not links:
            return set(start)
        edges: dict[int, set[int]] = defaultdict(set)
        for a, b, one_way in links:
            la, lb = int(lab[a]), int(lab[b])
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
        open_ = self.geo.walk_n & ~watched
        for h in self.team(side, removed):
            open_[h.cell] = True
        seed = self.seeds.get(side) if removed is None else None
        if seed is not None:
            open_ |= seed
        return open_

    def fill(self, side: str, blocked_by: np.ndarray, removed: int | None) -> Fill | None:
        """`side`'s free space through walkable cells `blocked_by` (the other team's watched cells) misses."""
        hs = self.team(side, removed)
        seed = self.seeds.get(side) if removed is None else None
        if seed is not None and not seed.any():
            seed = None
        if not hs and seed is None:
            return None
        watched = blocked_by
        lab = self.topo.label(self._open(side, watched, removed))
        free = np.zeros(self.geo.n, bool)
        by_reach: dict[frozenset, list[int]] = defaultdict(list)
        for h in hs:
            by_reach[frozenset(self._reach(lab, {int(lab[h.cell])}))].append(h.slot)
        if seed is not None:   # an unknown enemy's possible positions: a source with no player in it
            for label in set(np.unique(lab[seed]).tolist()) - {0}:
                # slot -1: someone unseen, so an entry's way back (compose) can lead here too
                by_reach[frozenset(self._reach(lab, {label}))].append(-1)
        # what the fill sees is the old Safe (Fill.safe); with unknown, Safe is unknown_safe and nothing
        # reads it, so it isn't computed (the components and their players still are: the way back)
        need_seen = self.unknown is None
        comps = []
        for labels, slots in by_reach.items():
            mask = np.isin(lab, list(labels))
            free |= mask
            comps.append((mask, self.comp_seen(mask, watched) if need_seen else None, slots))
        seen = None
        if need_seen:
            seen = np.zeros(self.geo.n, bool)
            for _, s, _ in comps:
                seen |= s
        return Fill(free, comps, seen)

    def comp_seen(self, mask: np.ndarray, watched: np.ndarray) -> np.ndarray:
        """Q73: what a free component sees, from its whole boundary (every free cell next to anything
        not free), smoke-aware. The frontier (next to the team's vision) sees most of it; the rest of
        the boundary rechecks only the targets the frontier missed.

        On a flat map the boundary is enough: whatever an interior cell sees, the boundary cell its line
        of sight leaves through sees too. With heights it isn't: a raised interior node sees over what
        stops the low boundary (tests/replays/test_control_floors.py has the case). So there every node
        of the component is a source: after the boundary, the interior rechecks the targets still unseen,
        which is exact and cheap, since the boundary has seen nearly all of it."""
        boundary, sources = self.boundary_seen(mask, watched)
        if self.geo.heights is None:
            return boundary
        rest = mask & ~sources
        return boundary | seen_from(self.geo, np.flatnonzero(rest), self.smokes, skip=mask | boundary)

    def boundary_seen(self, mask: np.ndarray, watched: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(what the component's boundary sees, the boundary nodes it was seen from): the shortcut that is
        the whole answer on a flat map."""
        walk = self.geo.walk_n
        edge = mask & self.topo.dilate(watched & walk, eight=True)
        first = seen_from(self.geo, np.flatnonzero(edge), self.smokes, skip=mask)
        rim = self.topo.edge_out(mask) & ~edge
        return first | seen_from(self.geo, np.flatnonzero(rim), self.smokes, skip=mask | first), edge | rim

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
            walk = self.geo.walk_n
            others = np.zeros(self.geo.n, bool)
            for h in self.team(side, removed):
                others |= self._live_of(h)
            held = others | self._live_of(self.holders[removed])
            src = self.unknown[side].copy()
            shut = np.zeros(self.geo.n, bool) if self.sealed is None else self.sealed.copy()
            for e in self.holders.values():
                if e.team != side:
                    src[e.cell] = True
                    shut[e.cell] = False       # as in Unknown.apply: one standing in a pinch is in it
            open_ = walk & ~shut               # a sealed pinch stays shut without them too
            gained = self._flood(src & ~others, open_ & ~others) & ~self._flood(src & ~held, open_ & ~held)
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
        lab = self.topo.label(room, eight=True)
        labels = set(np.unique(lab[src & room]).tolist()) - {0}
        if not labels:
            return np.zeros(self.geo.n, bool)
        return np.isin(lab, list(self._reach(lab, labels)))

    def unknown_safe(self, side: str, removed: int | None = None) -> np.ndarray:
        """Safe (docs/map-control-unknown-plan.md): walkable cells outside `side`'s unknown that no cell of
        it sees, smoke-aware (`comp_seen`'s boundary method, Q73); with `removed` on `side`, from the
        counterfactual's unknown. Cached for the tick; the knowledge pictures share it."""
        unk_flat = self.unknown_for(side, removed)
        key = side if unk_flat is self.unknown[side] else (side, removed)
        if key not in self._usafe:
            walk = self.geo.walk_n
            unk = unk_flat
            if not unk.any():
                self._usafe[key] = walk.copy()
            else:
                watched = np.zeros(self.geo.n, bool)
                for h in self.team(side, removed):
                    watched |= h.active | h.passive | h.watch
                seen = self.comp_seen(unk, watched)
                self._usafe[key] = (walk & ~unk) & ~seen
        return self._usafe[key]

    def dist_from(self, h: Holder) -> np.ndarray:
        """Walking distance (8-connected cells) from a holder, cached for the tick."""
        if h.slot not in self._dist:
            self._dist[h.slot] = self.topo.dist(h.cell)
        return self._dist[h.slot]

    def way_back(self, e: Holder, target: np.ndarray, watched_other: np.ndarray) -> np.ndarray:
        """Q56: the shortest walk from entry `e` to `target`, widened by WAY_BACK_WIDTH_M, on the
        cells the other team watches."""
        dist = self.dist_from(e)
        reach = target & (dist >= 0)
        if not reach.any():
            return np.zeros(self.geo.n, bool)
        d = np.where(reach, dist, np.iinfo(np.int32).max)
        cur = int(np.argmin(d))
        path = np.zeros(self.geo.n, bool)
        path[cur] = True
        while dist[cur] > 0:
            cur = self.topo.back(dist, cur)
            path[cur] = True
        width = max(1, round(WAY_BACK_WIDTH_M / self.geo.cell_m))
        return self.topo.dilate(path, eight=True, iterations=width) & watched_other

    # --- the state

    def compose(self, removed: int | None = None, base: dict | None = None, full: bool = True,
                stats: dict | None = None) -> dict:
        """The tick's cell states. `removed` drops one player (the counterfactual). With `base` and
        not `full`, fills are reused or extended from the base instead of recomputed."""
        geo, walk = self.geo, self.geo.walk_n
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
                    free = np.zeros(geo.n, bool)
                    seen = None if old.seen is None else np.zeros(geo.n, bool)
                    for m, s, _ in keep:
                        free |= m
                        if seen is not None:
                            seen |= s
                    fills[side] = Fill(free, keep, seen)
                    stats["own_fill_dropped_comp"] += 1
            else:
                # the other team's fill: cells only the removed player watched open up
                if old is None:
                    fills[side] = None
                    continue
                opened = base["cl"][other][2] & ~cl[other][2]
                if not (opened & self.topo.dilate(old.free, eight=True)).any():
                    fills[side] = old
                    stats["enemy_fill_same"] += 1
                    continue
                watched = cl[other][2]
                lab = self.topo.label(self._open(side, watched, removed))
                labels = self._reach(lab, {int(lab[h.cell]) for h in self.team(side, removed)})
                free = np.isin(lab, list(labels))
                seen = None
                if old.seen is not None:   # only without unknown (see `fill`)
                    added = free & ~old.free
                    edge = added & self.topo.dilate(~free, eight=True)
                    seen = old.seen | seen_from(geo, np.flatnonzero(edge), self.smokes, skip=free)
                # the components and their players, as `fill` groups them: the entry's way back (Q56)
                # reads which players share a component (only their seen sets are left out)
                by_reach: dict[frozenset, list[int]] = defaultdict(list)
                for h in self.team(side, removed):
                    by_reach[frozenset(self._reach(lab, {int(lab[h.cell])}))].append(h.slot)
                comps = [(np.isin(lab, list(ls)), None, slots) for ls, slots in by_reach.items()]
                fills[side] = Fill(free, comps, seen)
                stats["enemy_fill_extended"] += 1
        level = {}
        for side in ("A", "B"):
            other = "B" if side == "A" else "A"
            active, passive, _, _ = cl[side]
            lv = np.zeros(geo.n, np.int8)
            lv[passive] = 1
            f_enemy = fills[other]
            if self.unknown is not None:
                # nobody alive on the side: it holds nothing, Safe included
                safe = self.unknown_safe(side, removed) if self.team(side, removed) else np.zeros(geo.n, bool)
            else:
                safe = walk if f_enemy is None else f_enemy.safe(walk)
            lv[safe] = np.maximum(lv[safe], 2)
            if removed is None:
                self._safe[side] = safe & walk
            lv[active] = 3
            lv[~walk] = 0
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
            steady = np.zeros(geo.n, bool)
            fought = np.zeros(geo.n, bool)
            for h in hs:
                steady |= h.watch
                # a seen holder's live lines are fought over; ground they only remember is not (2026-10-01)
                live = h.active | (h.passive if h.memory is None else h.passive & ~h.memory)
                if is_contested[h.slot]:
                    fought |= live
                else:
                    steady |= live
            if self.unknown is not None:
                # only where an enemy could be (the user's call, 2026-10-01): enclosed ground stays theirs
                fought &= self.unknown_for(side, removed)
            contested |= fought & ~steady
            # the entry's way back (Q56): `other`'s players standing in `side`'s vision
            watched = cl[side][2]
            f_other = fills[other]
            for e in self.team(other, removed):
                if not watched[e.cell]:
                    continue
                target = np.zeros(geo.n, bool)
                if f_other is not None:
                    comps = f_other.comps or [(f_other.free, None, [x.slot for x in self.team(other, removed)])]
                    for m, _, slots in comps:
                        if any(x != e.slot for x in slots):
                            target |= m
                if not target.any():
                    for x in self.team(other, removed):
                        if x.slot != e.slot:
                            target[x.cell] = True
                if target.any():
                    contested |= self.way_back(e, target, watched)
            # cells lost to a status (Q55): nobody else claims them, so they are fought over
            contested |= cl[side][3] & ~cl[side][2] & (level[side] == 0) & (level[other] == 0)
        contested &= walk
        any_active = (level["A"] == 3) | (level["B"] == 3)
        state = np.zeros(geo.n, np.uint8)
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
            none = np.zeros(self.geo.n, bool)
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
    region = np.zeros(geo.n, bool)
    region[start] = True
    n = max(0, int(steps))
    if n == 0:   # scipy reads iterations < 1 as "until nothing changes"
        return region
    # one masked dilation, n steps (it leaves cells outside the mask as they were: `start` stays)
    open_ = geo.walk_n & ~watched
    return topology.of(geo).dilate(region, eight=True, iterations=n, within=open_)


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
                self.start[s] = rnd.node(s, rnd.t_start, p[0], p[1])

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
        watched = np.zeros(geo.n, bool)
        for h in tick.holders.values():
            if h.team == self.side:
                watched |= h.active | h.passive | h.watch
        seed = np.zeros(geo.n, bool)
        remembered = np.zeros(geo.n, bool)
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
        kt.sees = {x.slot: {h.slot for h in holders.values()
                            if h.team != x.team and (x.body[h.cell] or (x.slot, h.slot) in tick.direct)}
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
    topo = topology.of(geo)
    barrier = geo.to_nodes(geo.barrier.ravel())
    open_ = geo.walk_n & ~barrier
    regions = topo.label(open_)
    out = {}
    for side in ("A", "B"):
        hs = [h for h in tick.holders.values() if h.team == side and open_[h.cell]]
        ids = {int(regions[h.cell]) for h in hs}
        if not hs:
            continue
        starts = {h.slot: h.cell for h in hs}
        # a player pressed against a barrier can stand on a line cell: they start from the
        # neighbouring open cell in their teammates' ground
        for h in tick.holders.values():
            if h.team == side and h.slot not in starts and barrier[h.cell]:
                for near in topo.around(h.cell):
                    if int(regions[near]) in ids:
                        starts[h.slot] = near
                        break
        area = np.isin(regions, list(ids))
        if any(area[e.cell] for e in tick.holders.values() if e.team != side):
            # the paint has a gap: this side's ground reaches an enemy's start, so it means nothing
            tick.rnd.missing["barrier paint leaks (no start ground)"] += 1
            continue
        out[side] = (area, starts)
    return out


class Unknown:
    """Each team's unknown across a round's ticks (docs/map-control-unknown-plan.md): the cells where an
    enemy of the team could be. `begin` takes the barrier drop (each team's unknown is the enemy's start
    ground). `apply` runs on each tick in time order, after the tick's vision and before Memory.apply:
    the enemy's live players push it out from their own cells, it spreads at UNKNOWN_MPS through
    walkable cells and across the map's specials, and the team's live control (its players' active and
    passive vision, their watchers and their own cells) clears it and stops it. Smokes don't stop it:
    you can walk through a smoke.

    Everything in it is reachable at UNKNOWN_MPS (the user's rule, 2026-10-01): each unknown cell keeps
    the earliest time an enemy could have been there (`reached`), and a neighbour joins at that time plus
    its step's true length (a cell straight, sqrt(2) diagonal; a special one cell) over UNKNOWN_MPS, but
    never before it was last free: ground the team watched on the tick before is entered from this tick
    on. Exact at any tick spacing, so nothing is carried between ticks.

    Each enemy has their own (the user's call, 2026-10-01; the team's unknown is all of them together): one
    the team spots, in a player's active sight or a watcher, can only be where they stand, so theirs starts
    again from that spot and time and walks out from it once they're out of sight. A dead enemy's goes with
    them."""

    def __init__(self, geo: Geometry, chokes: np.ndarray | None = None):
        self.geo = geo
        self.topo = topology.of(geo)
        self.cells = {"A": np.zeros(geo.n, bool), "B": np.zeros(geo.n, bool)}
        self.reached: dict[str, dict[int, np.ndarray]] = {"A": {}, "B": {}}   # side -> enemy slot -> flat
        self.seen: dict[str, dict[int, tuple[int, float]]] = {"A": {}, "B": {}}   # enemy slot -> (cell, t) last spotted
        self._start = {"A": None, "B": None}       # the barrier drop's ground, for each enemy's first tick
        self.free_since = {"A": None, "B": None}   # flat: when each cell was last not the team's live control
        self._room = {"A": None, "B": None}        # the last tick's room, to see what was freed since
        self.t: float | None = None
        self.links = special_links(geo)
        # the map wall's face (unwalkable pixels beside a walkable one, at their centres) and each pixel's
        # distance to the nearest unwalkable one, for the pinches of GAP_SEAL_M
        wp = geo.walk_px.astype(bool)
        face = ~wp & ndimage.binary_dilation(wp)
        fy, fx = np.nonzero(face)
        self.face = np.stack([fx + 0.5, fy + 0.5], axis=1)
        self.to_wall_px = ndimage.distance_transform_edt(wp)
        self._sealed_key, self._sealed = None, np.zeros(geo.n, bool)
        # route history (timing gaps, section 4): bookkeeping only; it never changes which nodes are unknown
        self.chokes = chokes if chokes is not None else np.full(geo.n, -1, np.int32)
        self.log = {"A": RouteLog(), "B": RouteLog()}
        self.entry: dict[str, dict[int, np.ndarray]] = {"A": {}, "B": {}}   # side -> enemy -> entry per node
        # side -> enemy -> the entry of their last sighting (self.seen), a source while it lasts
        self._seen_entry: dict[str, dict[int, int]] = {"A": {}, "B": {}}
        # locating events (timing gaps, section 4)
        self.located: dict[str, dict[int, float]] = {"A": {}, "B": {}}    # side -> enemy -> last locating time
        self.events: dict[str, list[tuple[int, float, str]]] = {"A": [], "B": []}   # this tick's (enemy, t, kind)
        self._prev_t: float | None = None
        self._dead: dict[str, set[int]] = {"A": set(), "B": set()}      # enemies whose region went with a death
        self._pending: dict[str, set[int]] = {"A": set(), "B": set()}   # revived, waiting for a first sample (R14)

    def sealed(self, smokes) -> np.ndarray:
        """Flat cells in a pinch narrower than GAP_SEAL_M between one of `smokes` (the tick's sight
        blockers: smoke circles and wall abilities) and the map's wall. For each point of the wall's face
        that close to a blocker, the shortest segment from the blocker's edge to it spans a pinch when it
        crosses open floor (its middle is walkable, outside the gas, and at least a third of its length from
        any wall: a segment hugging a wall's face, where a screen meets a door's jamb, spans nothing). The
        cells it crosses are sealed, two cells thick so no diagonal step slips between them. The gas itself
        stays walkable: a screen across a door or a smoke filling a corridor seals nothing."""
        key = tuple(repr(s) for s in smokes or ())
        if key == self._sealed_key:
            return self._sealed
        out = np.zeros(GRID * GRID, bool)
        reach = GAP_SEAL_M / self.geo.m_per_px
        wp = self.geo.walk_px
        for smoke in smokes or ():
            if isinstance(smoke, Wall):
                feet = []
                for x0, y0, x1, y1 in smoke.segs:
                    dx, dy = x1 - x0, y1 - y0
                    f = np.clip(((self.face[:, 0] - x0) * dx + (self.face[:, 1] - y0) * dy)
                                / max(dx * dx + dy * dy, 1e-6), 0, 1)
                    feet.append(np.stack([x0 + f * dx, y0 + f * dy], axis=1))
                lens = np.stack([np.hypot(*(self.face - ft).T) for ft in feet])
                foot = np.stack(feet)[lens.argmin(0), np.arange(len(self.face))]
                inside = np.zeros(len(self.face), bool)
            else:
                sx, sy, r, _ = smoke
                d = np.hypot(self.face[:, 0] - sx, self.face[:, 1] - sy)
                foot = np.array([sx, sy]) + (self.face - [sx, sy]) * (r / np.maximum(d, 1e-6))[:, None]
                inside = d <= r
            seg = self.face - foot
            length = np.hypot(seg[:, 0], seg[:, 1])
            keep = ~inside & (length > 0.5) & (length < reach)
            mid = foot + seg / 2
            mx = np.clip(mid[:, 0].astype(int), 0, wp.shape[1] - 1)
            my = np.clip(mid[:, 1].astype(int), 0, wp.shape[0] - 1)
            keep &= wp[my, mx] & (self.to_wall_px[my, mx] >= length / 3)
            if not isinstance(smoke, Wall):
                keep &= np.hypot(mid[:, 0] - sx, mid[:, 1] - sy) > r
            for a, s, n in zip(foot[keep], seg[keep], length[keep]):
                k = np.linspace(0.0, 1.0, int(n // 2) + 2)[:, None]
                side = np.array([-s[1], s[0]]) / n * (CELL / 2)       # half a cell to each side: two thick
                for off in (-side, 0 * side, side):
                    pts = a + k * s + off
                    out[[self.geo.cell_of_px(x, y) for x, y in pts]] = True
        out = self.geo.to_nodes(out) & self.geo.walk_n      # a pinch seals every floor of its cells
        self._sealed_key, self._sealed = key, out
        return out

    def begin(self, areas: dict) -> None:
        for side in ("A", "B"):
            other = "B" if side == "A" else "A"
            if other in areas:
                self.cells[side] = areas[other][0].copy()
                self._start[side] = areas[other][0].copy()

    def apply(self, tick) -> None:
        walk = self.geo.walk_n
        n = self.geo.n
        t = self.t = tick.t
        pinched = self.sealed(getattr(tick, "smokes", None))
        rnd = getattr(tick, "rnd", None)
        locates = rnd is not None and hasattr(rnd, "locating_rows")    # a real round (not a test's bare tick)
        since = -math.inf if self._prev_t is None else self._prev_t   # events in (since, t]: R8
        for side in ("A", "B"):
            self.events[side] = []
            enemies = self._enemies(tick, side)
            for gone in set(self.reached[side]) - enemies:   # dead: nowhere
                del self.reached[side][gone]
                self.seen[side].pop(gone, None)
                self.entry[side].pop(gone, None)
                self._seen_entry[side].pop(gone, None)
                self._pending[side].discard(gone)
                if locates:
                    self._dead[side].add(gone)
            if not enemies:
                self.cells[side] = np.zeros(n, bool)   # nobody left: nobody could be anywhere
                continue
            live = np.zeros(n, bool)
            spots = np.zeros(n, bool)
            shut = pinched.copy()
            for h in tick.holders.values():
                if h.team == side:
                    live |= h.active | h.passive | h.watch
                    live[h.cell] = True
                    spots |= h.active | h.watch
                else:
                    shut[h.cell] = False                       # one standing in a pinch is in it
            room = walk & ~live & ~shut
            free = self.free_since[side]
            if free is None:
                free = np.full(n, t)
            else:
                free = np.where(room & ~self._room[side], t, free)   # freed since the last tick: from now
            self.free_since[side], self._room[side] = free, room
            cells = np.zeros(n, bool)
            for slot in sorted(enemies):
                reached = self.reached[side].get(slot)
                before = np.full(n, np.inf) if reached is None else reached.copy()
                sources: dict[int, float] = {}       # this tick's sources (route history only)
                area: np.ndarray | None = None       # an area collapse's nodes; its centre is a source (R15)
                centre: int | None = None
                h = tick.holders.get(slot)
                hits: list[tuple[float, str, float, int, float, float]] = []   # (t, kind, radius m, node, x, y)
                if reached is None:
                    reached = np.full(n, np.inf)
                    if slot in self._dead[side]:
                        # a second life: not the barrier ground again, but where they stand, once they have a
                        # position (R14: with none yet, at the first sample of the new life)
                        self._dead[side].discard(slot)
                        self._pending[side].add(slot)
                    elif self._start[side] is not None:
                        reached[self._start[side]] = t        # the barrier drop's ground: there now
                        sources.update(dict.fromkeys(np.flatnonzero(self._start[side]).tolist(), t))
                if slot in self._pending[side]:
                    back = rnd.revival(slot, t)
                    if back is not None:
                        tb, x, y = back
                        hits.append((tb, "revived", 0.0, rnd.node(slot, tb, x, y), x, y))
                if locates:
                    hits += self._locating(rnd, side, slot, since, t)
                if hits:
                    # every event is recorded, in time order; the region collapses round the latest (on a tie,
                    # the smaller area)
                    hits.sort(key=lambda e: (e[0], e[2]))
                    te, _, radius, centre, x, y = max(hits, key=lambda e: (e[0], -e[2]))
                    area = self._area(centre, x, y, radius, room)
                    reached = np.full(n, np.inf)
                    reached[area] = te
                    before = np.full(n, np.inf)     # every route starts again from the area's centre
                    sources = {centre: te}
                    self.seen[side].pop(slot, None)
                    self._seen_entry[side].pop(slot, None)
                    self._pending[side].discard(slot)
                    self.located[side][slot] = te
                    self.events[side] += [(slot, e[0], e[1]) for e in hits]
                # in an active view or a watcher's; or seen at their own height in an active cone (Tick._direct)
                in_cone = any(active and target == slot and viewer in tick.holders and tick.holders[viewer].team == side
                              for (viewer, target), active in getattr(tick, "direct", {}).items())
                if h is not None and (spots[h.cell] or in_cone):
                    reached = np.full(n, np.inf)    # spotted: there, and nowhere else
                    self.seen[side][slot] = (h.cell, t)
                    self._seen_entry[side][slot] = self.log[side].add(h.cell, t, -1, int(self.chokes[h.cell]))
                    before = np.full(n, np.inf)     # every route starts again from the sighting
                    sources = {}
                    area = centre = None
                    self.located[side][slot] = t
                    self.events[side].append((slot, t, "seen"))      # after the tick's other events (R8)
                if h is not None:
                    if t < reached[h.cell]:
                        sources[h.cell] = t
                    reached[h.cell] = min(reached[h.cell], t)   # an enemy pushes it out from where they stand
                reached[~room] = np.inf
                reached, parent = self._spread(reached, room, free, t, self.seen[side].get(slot))
                self._record(side, slot, before, reached, parent, sources, centre, area)
                self.reached[side][slot] = reached
                cells |= np.isfinite(reached)
            self.cells[side] = self._drop_pieces(side, cells, tick)
        self._prev_t = t

    def _heard_by(self, rnd, side: str, te: float, x: float, y: float, range_m: float) -> bool:
        """Whether a live player of `side` is within range_m of (x, y) px at te (walls ignored)."""
        r = range_m / self.geo.m_per_px
        for s, team in rnd.team.items():
            if team != side or not rnd.alive(s, te):
                continue
            p = rnd.pos(s, te)
            if p is not None and (p[0] - x) ** 2 + (p[1] - y) ** 2 <= r * r:
                return True
        return False

    def _locating(self, rnd, side: str, slot: int, since: float, t: float
                  ) -> list[tuple[float, str, float, int, float, float]]:
        """Every event in (since, t] that locates enemy `slot` for `side` (R8), as (time, kind, area radius
        m, node, x px, y px) at the event's own time and the enemy's position then: a kill of one of the
        team, the plant, gun damage to one of the team (heard or not), gunfire within that gun's hearing of
        a live player of the team, and, at t, moving faster than AUDIBLE_MPS within FOOTSTEP_RANGE_M of one.
        An event without a position for the enemy locates nothing and is counted (R20)."""
        out = []
        times, rows = rnd.locating_rows(slot)
        for te, kind, detail in rows[bisect.bisect_right(times, since):bisect.bisect_right(times, t)]:
            if kind in ("kill", "damage") and rnd.team.get(detail) != side:
                continue
            p = rnd.pos(slot, te)
            if p is None:
                rnd.miss("locating event without a position", slot)
                continue
            if kind == "gunfire":
                if detail is None:      # no gun recorded: the longest hearing range in the table (spec)
                    rng = max(GUN_HEARING_M.values(), default=GUN_HEARING_DEFAULT_M)
                else:
                    rng = GUN_HEARING_M.get(detail, GUN_HEARING_DEFAULT_M)
                if not self._heard_by(rnd, side, te, p[0], p[1], rng):
                    continue
                radius = SHOT_AREA_M
            else:
                radius = {"kill": KILL_AREA_M, "plant": PLANT_AREA_M, "damage": DAMAGE_AREA_M}[kind]
            out.append((te, kind, radius, rnd.node(slot, te, p[0], p[1]), p[0], p[1]))
        v = rnd.speed(slot, t)
        if v is None:
            if rnd.alive(slot, t - AUDIBLE_WINDOW_S):    # alive across the window, yet no speed for it
                rnd.miss("speed across a track break or missing sample", slot)
        elif v > AUDIBLE_MPS:
            p = rnd.pos(slot, t)
            if self._heard_by(rnd, side, t, p[0], p[1], FOOTSTEP_RANGE_M):
                out.append((t, "footsteps", FOOTSTEP_AREA_M, rnd.node(slot, t, p[0], p[1]), p[0], p[1]))
        return out

    def _area(self, node: int, x: float, y: float, radius_m: float, room: np.ndarray) -> np.ndarray:
        """Nodes within radius_m of (x, y) px reached by walking from `node` through `room` (the unobserved
        walkable nodes), plus `node` itself."""
        seed = np.zeros(self.geo.n, bool)
        seed[node] = True
        if radius_m <= 0:
            return seed
        within = room | seed
        steps = max(1, int(math.ceil(radius_m / self.geo.cell_m)))
        grown = self.topo.dilate(seed, eight=True, iterations=steps, within=within)
        r = radius_m / self.geo.m_per_px
        near = (self.geo.centres[:, 0] - x) ** 2 + (self.geo.centres[:, 1] - y) ** 2 <= r * r
        return (grown & within & near) | seed

    def _drop_pieces(self, side: str, cells: np.ndarray, tick) -> np.ndarray:
        """`cells` less its 8-connected pieces of at most DROP_PIECE_CELLS that no enemy stands in (the
        user's call, 2026-10-01: what vision has eaten down to that is gone; a real enemy there makes
        unknown of their own). Dropped from every enemy's unknown, and a sighting inside one with it."""
        if not cells.any():
            return cells
        lab = self.topo.label(cells, eight=True)
        size = np.bincount(lab)
        small = size <= DROP_PIECE_CELLS
        small[0] = False
        for h in tick.holders.values():
            if h.team != side:
                small[lab[h.cell]] = False
        if not small.any():
            return cells
        drop = small[lab]
        for slot, reached in self.reached[side].items():
            reached[drop] = np.inf
            if slot in self.entry[side]:
                self.entry[side][slot][drop] = -1
            if slot in self.seen[side] and drop[self.seen[side][slot][0]]:
                del self.seen[side][slot]
                self._seen_entry[side].pop(slot, None)
        return cells & ~drop

    @staticmethod
    def _enemies(tick, side: str) -> set[int]:
        """The enemies of `side` alive at the tick: from the round's lives when the tick has them (a live
        player can lack a position sample), else from its holders."""
        rnd = getattr(tick, "rnd", None)
        if rnd is not None:
            return {s for s, team in rnd.team.items() if team != side and rnd.alive(s, tick.t)}
        return {h.slot for h in tick.holders.values() if h.team != side}

    def _spread(self, reached: np.ndarray, room: np.ndarray, free: np.ndarray, t: float,
                seen: tuple[int, float] | None = None) -> tuple[np.ndarray, np.ndarray]:
        """`reached` relaxed through `room` up to time `t`: each cell's earliest arrival from a
        neighbour (after the cell was last freed); arrivals later than `t` are not there yet. `seen`
        (cell, time), where the enemy was last spotted, is a source from that time even while the team
        still watches that cell or has just freed it (they were in it); it is in the result only once it is
        in `room`. Also each arrival's parent node (topology.spread, `parents`; -1 where none)."""
        g = reached.copy()
        if seen is not None:
            g[seen[0]] = min(g[seen[0]], seen[1])
        if not np.isfinite(g).any():
            return g, np.full(len(g), -1, np.int64)
        arr, par = self.topo.spread(g, room, free, t, self.geo.cell_m / UNKNOWN_MPS, self.links, parents=True)
        if seen is not None and not room[seen[0]]:
            # topology.spread finds parents among the final arrivals, where a sighting the team still
            # watches is absent. Every other node outside `room` was cleared before the spread, so an
            # arrival that came from a neighbour (it beat its starting value) but found no parent there
            # came from the sighting (R15)
            orphan = np.isfinite(arr) & (par < 0) & (arr < g)
            par[orphan] = seen[0]
        return arr, par

    def _record(self, side: str, slot: int, before: np.ndarray, after: np.ndarray, parent: np.ndarray,
                sources: dict[int, float], centre: int | None = None, area: np.ndarray | None = None) -> None:
        """Route history (timing gaps, section 4), bookkeeping only. Logs every node whose arrival is new or
        changed, in arrival order so each parent's entry exists first, and every one of this tick's
        `sources` (node -> time: barrier ground, own-position push, an area collapse's centre) even where the
        team observes it (R15). A source entry has no parent. A node whose spread parent is a source uses
        that source's entry: the enemy's sighting (self.seen) has one entry for as long as it lasts, made when
        they were spotted. The other nodes of an area collapse (`area`, all at the centre's time) take the
        centre's entry as their parent; the centre is logged first among them."""
        log = self.log[side]
        ent = self.entry[side].get(slot)
        ent = np.full(len(after), -1, np.int64) if ent is None else ent.copy()
        fin = np.isfinite(after)
        ent[~fin] = -1
        changed = np.flatnonzero(fin & ((~np.isfinite(before)) | (after != before)))
        observed = [node for node in sources if not fin[node]]
        if not len(changed) and not observed:
            self.entry[side][slot] = ent
            return
        nodes = np.concatenate([changed, np.asarray(observed, np.int64)])
        times = np.concatenate([after[changed], np.asarray([sources[x] for x in observed], np.float64)])
        first = (nodes != centre) if centre is not None else np.ones(len(nodes), bool)   # the centre first on a tie
        seen = self.seen[side].get(slot)
        seen_e = self._seen_entry[side].get(slot)
        # a source node outside the final arrivals -> its entry (the review of Task 4: never an erased push's)
        fixed: dict[int, int] = {} if seen_e is None else {seen[0]: seen_e}
        choke = self.chokes
        for i in np.lexsort((nodes, first, times)).tolist():
            node, tt = int(nodes[i]), float(times[i])
            p = int(parent[node]) if fin[node] else -1
            if p < 0 and area is not None and node != centre and area[node] and centre in fixed \
                    and tt == sources[centre]:
                e = log.add(node, tt, fixed[centre], int(choke[node]))   # a member of the area: from its centre
            elif p < 0:                                      # a source
                if seen_e is not None and node == seen[0] and tt == seen[1]:
                    e = seen_e
                else:
                    e = log.add(node, tt, -1, int(choke[node]))
                if node == centre:
                    fixed[node] = e
            else:
                pe = int(ent[p]) if fin[p] else fixed.get(p, -1)
                e = log.add(node, tt, pe, int(choke[node]))
            if fin[node]:
                ent[node] = e
        self.entry[side][slot] = ent


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
            for slot, share in _share_by_walk(area, starts, topology.of(self.geo)).items():
                self.cells[slot] = share

    def apply(self, tick, unknown: dict | None = None) -> None:
        walk = self.geo.walk_n
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


def _share_by_walk(area: np.ndarray, starts: dict[int, int], topo) -> dict[int, np.ndarray]:
    """Each node of `area` (flat) to the start nearest it in 4-connected walking steps; a tie goes to
    the lower slot. Nodes no start can reach go to nobody."""
    owner = np.full(len(area), -1, np.int16)
    fronts = {}
    for slot, cell in sorted(starts.items()):
        if owner[cell] < 0:
            owner[cell] = slot
            fronts[slot] = owner == slot
    while fronts:
        grown = {}
        for slot, front in sorted(fronts.items()):
            new = topo.dilate(front, within=area & (owner < 0))
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


class TickRunner:
    """What each tick of a round needs before `compose`, in time order: on the first tick the barrier
    drop (each team's start ground, to both), then the unknown, then remembered ground on top of it.
    compute_round and the scene renderer (scripts/render_control_scenes.py) both step through this, so a
    scene can't drift from the stored result (the code review, 2026-10-01: it once skipped both)."""

    def __init__(self, geo: Geometry, chokes: np.ndarray | None = None):
        self.geo = geo
        self.memory = Memory(geo)
        self.unknown = Unknown(geo, chokes)
        self.started = False

    def step(self, tick: "Tick", timings: dict | None = None) -> "Tick":
        timings = timings if timings is not None else defaultdict(float)
        a = time.perf_counter()
        if not self.started:
            areas = barrier_start(self.geo, tick)
            self.memory.begin(areas)
            self.unknown.begin(areas)
            self.started = True
        self.unknown.apply(tick)              # before memory: live vision only clears it
        timings["unknown"] += time.perf_counter() - a
        a = time.perf_counter()
        self.memory.apply(tick, self.unknown.cells)
        timings["memory"] += time.perf_counter() - a
        tick.unknown = {side: cells.copy() for side, cells in self.unknown.cells.items()}
        tick.sealed = self.unknown.sealed(tick.smokes)   # cached per smokes: the one apply just used
        return tick


def compute_round(blob: dict, geo: Geometry, link: ControlLink | None = None, *,
                  ticks: np.ndarray | None = None, full_every: int = 0, knowledge: bool = True,
                  observer=None) -> RoundControl:
    """Control for one round. `ticks` overrides the Q75 schedule (tests, parity checks);
    `full_every` N > 0 also runs the full counterfactual on every Nth tick and compares. `knowledge`
    also builds each team's picture of the round (R3.3): what it knew, not the true positions.
    `observer`, when given, is called as observer(record, unknown) after each tick's unknown
    (app/control/observe.py); it must not change either. An observer with an `on_tick` method is called as
    observer.on_tick(tick, unknown) instead and builds the record itself (observe.record), inside its own
    failure boundary, so an error there is the observer's and never control's (the tick cache's guard)."""
    visibility(geo)
    rnd = RoundInputs(blob, geo, link)
    times = rnd.tick_times() if ticks is None else np.asarray(ticks, float)
    nxt = np.append(times[1:], rnd.t_end)
    weights = np.maximum(nxt - times, 0.0)
    walk_flat = geo.walk.ravel()             # the stored result is per walkable cell
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
    runner = TickRunner(geo, chokes.node_chokes(geo, choke_assets.load(geo.name)))
    unknown_masks = {side: np.zeros((n_ticks, n_walk), bool) for side in ("A", "B")}
    know ={side: Knowledge(rnd, side) for side in ("A", "B")} if knowledge else {}
    knew_states = {side: np.zeros((n_ticks, n_walk), np.uint8) for side in know}

    for n, t in enumerate(times):
        t = float(t)
        tick = runner.step(Tick(rnd, t, timings), timings)
        if observer is not None:
            on_tick = getattr(observer, "on_tick", None)
            if on_tick is not None:
                on_tick(tick, runner.unknown)
            else:
                observer(observe.record(tick, runner.unknown), runner.unknown)
        if tick.fallbacks.get("unresolved_rays"):
            # a viewer stood in, or looked through, terrain the heights don't know: 2D sight there (the spec:
            # "reported where the user will see it"; preview_control_live.py warns on it)
            rnd.missing["ticks looking through unresolved terrain (2D sight there)"] += 1
        for side in ("A", "B"):
            unknown_masks[side][n] = geo.to_cells(tick.unknown[side])[walk_flat]
        a = time.perf_counter()
        base = tick.compose()
        b = time.perf_counter()
        cov = tick.coverage()
        timings["base"] += b - a
        timings["coverage"] += time.perf_counter() - b
        state = base["state"]
        states[n] = collapse_states(geo, state)[walk_flat]
        if prev_state is not None and rnd.t_start < t <= rnd.t_decided:
            _credit_taken(tick, cov, prev_state, state, players, cell_m2)
        prev_state = state
        if know:
            k0 = time.perf_counter()
            for side, kn in know.items():
                knew_states[side][n] = collapse_states(geo, kn.tick_for(tick, t).compose()["state"])[walk_flat]
            timings["knowledge"] += time.perf_counter() - k0
        live_w =max(0.0, min(t + weights[n], rnd.t_decided) - max(t, rnd.t_start))
        owned = {s: float((score(state, s) > 0).sum()) for s in ("A", "B")}
        ctl_sum = {"A": 0.0, "B": 0.0}
        dying = {slot: t_death for slot, t_death in death_ticks.get(n, [])}
        for s, h in tick.holders.items():
            side = h.team
            act, psv, act_mask, pas_mask = cov[s]
            coverage_masks[n, s] = geo.to_cells(act_mask | pas_mask)[walk_flat]
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
            control_masks[n, s] = geo.to_cells(drop > 0)[walk_flat]
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
