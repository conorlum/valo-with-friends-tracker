"""The damaging-molly traversal contract (docs/superpowers/specs/2026-10-06-replay-player-state-design.md section 5;
the plan's P02 step 6 and its amendment F4/F8/F15/F20; decision D6). Pure: nothing here touches reachability (the
engine's RoundInputs._mollies wires it into the Unknown spread, behind CONTROL 9).

Which zones count is decided here, in code: MOLLY_KEYS, the owner's list (D6, 2026-10-09 review: "these are all the
mollies that will stop unknown"): Breach's Aftershock, Brimstone's Incendiary and Orbital Strike, Gekko's Mosh Pit,
KAY/O's FRAG/ment, Killjoy's Nanoswarm, Phoenix's Hot Hands, Tejo's Guided Salvo and Armageddon, and Viper's Snake
Bite. Vyse's Razorvine is on the list too, but no stored replay has shown its object yet, so it has no key. Each
zone's shape and whether it passes walls are here too (SHAPES, THROUGH_WALLS); its numbers (`radius_m`, `length_m`,
`width_m`, `seconds`) live in app/control/utility.json `molly`, from each ability's wiki page, so a changed number
changes `figures_hash` and a prose edit doesn't.

A molly is an `ability` row of `<code>_<name>` in MOLLY_KEYS. Only FRAG/ment's is a `Projectile` (it sticks where it
lands and goes off); every other key's projectile is just the flight. It holds from when it shows on the ground
(the owner's call: players back off from the indicator) for its figure's seconds, capped at the row's end and at
its `gone`: `[t, min(t + seconds, t1, gone))`, where `t` is
- the row's own time (a landed molly, Aftershock stuck to its wall, Orbital Strike's indicator, Guided Salvo's
  missile on the ground);
- FRAG/ment: its landing, the first `flight` point it stays within LANDED_UV of from then on; its place is the
  flight's last point. A FRAG/ment without a flight has no place (the replay doesn't always send one);
- an Armageddon segment: its cast's start (the `Cashew_X_SegmentManager` row by the same owner at most
  ARMAGEDDON_CAST_S before it, when the path shows), else its own time.

Shapes: a circle of `radius_m` round its place; Aftershock a capsule, `radius_m` round a segment `length_m` long
from where it stuck along the row's `yaw` (Breach fires it through the wall he faces); an Armageddon segment a strip
`length_m` along the path and `width_m` across it, the path's direction from its cast's first segment to its last,
else from the cast's place to it, else the cast's yaw.

The owner is the row's `by`; a row whose owner, place, time or figures are unknown is skipped and counted, never
guessed hostile.

Hazards are per side, keyed by the OWNER's side: `hazards_at("A", t)` are side A's burning mollies, which restrict
`unknown[A]` (where A believes B players may be: B can't walk through A's fire). They never restrict `unknown[B]`
(a player isn't blocked by their own team's molly). A molly whose owner has no side this round is counted and
skipped."""

from __future__ import annotations

import math
from typing import NamedTuple

MOLLY_KEYS = frozenset({"Phoenix_MolotovFire", "Sarge_Q_Molotov_Production", "Pandemic_AcidMolotov_NewMolotov",
                        "Killjoy_4_BeeSwarm_Damage", "Aggrobot_C_ExplodeyPatch", "Breach_4_FusionBlast",
                        "Sarge_X_OrbitalStrike_Production", "Cashew_E_AirStrikeMortar", "Cashew_X_Segment",
                        "Grenadier_Q_SemtexBasic"})
PROJECTILE_KEYS = frozenset({"Grenadier_Q_SemtexBasic"})     # the projectile itself is the zone
SHAPES = {"Breach_4_FusionBlast": "capsule", "Cashew_X_Segment": "strip"}          # the rest are circles
# The wiki: Nanoswarm is the wall-piercing molotov; Orbital Strike, Aftershock and Armageddon strike through walls.
THROUGH_WALLS = frozenset({"Killjoy_4_BeeSwarm_Damage", "Sarge_X_OrbitalStrike_Production", "Breach_4_FusionBlast",
                           "Cashew_X_Segment"})
ARMAGEDDON = "Cashew_X_Segment"
ARMAGEDDON_CAST = "Cashew_X_SegmentManager"
ARMAGEDDON_CAST_S = 7.0          # cast to its last segment: 3.08 s windup + 15 x 0.25 s (wiki/Armageddon), rounded up
LANDED_UV = 20                   # FRAG/ment has landed once every later flight point is within this of it


class Molly(NamedTuple):
    by: int
    u: float
    v: float
    radius_m: float
    t0: float
    t1: float
    shape: str = "circle"
    length_m: float = 0.0
    width_m: float = 0.0
    angle: float = 0.0           # map degrees (0 = +u, 90 = +v): the capsule's axis, the strip's path
    through: bool = False        # passes walls: every walkable cell of the shape, not only those walked to
    z: object = None             # the row's height in decimetres, for the floor

    def active(self, t: float) -> bool:
        return self.t0 <= t < self.t1


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _count(diagnostics: dict, case: str) -> None:
    diagnostics[case] = diagnostics.get(case, 0) + 1


def _key(row: dict) -> str | None:
    if not isinstance(row, dict) or row.get("k") != "ability":
        return None
    return f"{row.get('code')}_{row.get('name')}"


def molly_key(row: dict) -> str | None:
    """The row's allow-list key when it is a damaging molly, else None."""
    key = _key(row)
    if key not in MOLLY_KEYS:
        return None
    if (row.get("kind") == "Projectile") != (key in PROJECTILE_KEYS):
        return None
    return key


def _landing(row: dict) -> tuple[float, float, float] | None:
    """FRAG/ment's landing: (time, u, v) from its flight, or None without one."""
    flight = row.get("flight")
    if not isinstance(flight, list) or len(flight) < 2:
        return None
    points = []
    for p in flight:
        if not isinstance(p, (list, tuple)) or len(p) < 3:
            return None
        t, u, v = (_num(x) for x in p[:3])
        if t is None or u is None or v is None:
            return None
        points.append((t, u, v))
    last = points[-1]
    for i, (t, u, v) in enumerate(points):
        if all(math.hypot(q[1] - u, q[2] - v) <= LANDED_UV for q in points[i:]):
            return t, last[1], last[2]
    return last


def _armageddon(util: list) -> dict[int, tuple[float | None, float]]:
    """Per Armageddon segment row (by its index in `util`): (its cast's start or None, the path's angle)."""
    casts = [(_num(r.get("t")), r) for r in util if _key(r) == ARMAGEDDON_CAST]
    segments = [(i, r) for i, r in enumerate(util) if _key(r) == ARMAGEDDON]
    groups: dict[int, list[tuple[int, dict]]] = {}
    lone: list[tuple[int, dict]] = []
    for i, r in segments:
        t = _num(r.get("t"))
        owned = [(ct, c) for ct, c in casts
                 if ct is not None and t is not None and c.get("by") == r.get("by") and 0 <= t - ct <= ARMAGEDDON_CAST_S]
        if owned:
            groups.setdefault(id(max(owned, key=lambda x: x[0])[1]), []).append((i, r))
        else:
            lone.append((i, r))
    out: dict[int, tuple[float | None, float]] = {}
    by_id = {id(c): c for _, c in casts}
    for cid, members in groups.items():
        cast = by_id[cid]
        placed = sorted(((_num(r.get("t")), _num(r.get("u")), _num(r.get("v"))) for _, r in members
                         if None not in (_num(r.get("t")), _num(r.get("u")), _num(r.get("v")))))
        cu, cv = _num(cast.get("u")), _num(cast.get("v"))
        if len(placed) >= 2 and (placed[0][1], placed[0][2]) != (placed[-1][1], placed[-1][2]):
            angle = math.degrees(math.atan2(placed[-1][2] - placed[0][2], placed[-1][1] - placed[0][1]))
        elif placed and cu is not None and cv is not None and (cu, cv) != (placed[0][1], placed[0][2]):
            angle = math.degrees(math.atan2(placed[0][2] - cv, placed[0][1] - cu))
        else:
            angle = _num(cast.get("yaw")) or 0.0
        for i, _ in members:
            out[i] = (_num(cast.get("t")), angle)
    for i, r in lone:
        out[i] = (None, _num(r.get("yaw")) or 0.0)
    return out


def molly_zones(util: list, figures: dict) -> tuple[list[Molly], dict]:
    """The round's damaging mollies as `Molly`s in row order, and the diagnostics of what was skipped."""
    zones: list[Molly] = []
    diagnostics: dict = {}
    util = [r for r in util or [] if isinstance(r, dict)]
    paths = _armageddon(util)
    for i, row in enumerate(util):
        key = molly_key(row)
        if key is None:
            continue
        by = row.get("by")
        if isinstance(by, bool) or not isinstance(by, int) or not 0 <= by <= 9:
            _count(diagnostics, "molly without a known owner")
            continue
        figure = (figures.get(key) if isinstance(figures, dict) else None) or {}
        radius, seconds = _num(figure.get("radius_m")), _num(figure.get("seconds"))
        shape = SHAPES.get(key, "circle")
        length, width = _num(figure.get("length_m")) or 0.0, _num(figure.get("width_m")) or 0.0
        if (radius is None or seconds is None or radius <= 0 or seconds <= 0
                or (shape == "capsule" and length <= 0) or (shape == "strip" and (length <= 0 or width <= 0))):
            _count(diagnostics, "molly without figures")
            continue
        t0 = _num(row.get("t"))
        if key in PROJECTILE_KEYS:
            landed = _landing(row)
            if landed is None:
                _count(diagnostics, "molly without a place")
                continue
            t0, u, v = landed
        else:
            u, v = _num(row.get("u")), _num(row.get("v"))
            if u is None or v is None:
                _count(diagnostics, "molly without a place")
                continue
        if t0 is None:
            _count(diagnostics, "molly without a time")
            continue
        start, angle = t0, _num(row.get("yaw")) or 0.0
        if key == ARMAGEDDON:
            cast, angle = paths.get(i, (None, angle))
            if cast is None:
                _count(diagnostics, "armageddon segment without its cast (held from its own time)")
            else:
                start = min(cast, t0)
        t1 = t0 + seconds
        end, gone = _num(row.get("t1")), _num(row.get("gone"))
        if end is not None:
            t1 = min(t1, end)
        if gone is not None and start < gone:     # the object went first (a gone at or before the start: no cut)
            t1 = min(t1, gone)
        if t1 <= start:
            _count(diagnostics, "molly ended before it burned")
            continue
        zones.append(Molly(by, u, v, radius, start, t1, shape, length, width, angle, key in THROUGH_WALLS,
                           row.get("z")))
    return zones, diagnostics


class Hazards:
    """Per-side burning mollies over time, keyed by the owner's side (see the module docstring)."""

    def __init__(self, zones: list[Molly], side_of: dict, diagnostics: dict | None = None):
        self.by_side: dict[str, list[Molly]] = {}
        for z in zones:
            side = side_of.get(z.by)
            if side is None:
                if diagnostics is not None:
                    _count(diagnostics, "molly owner without a side")
                continue
            self.by_side.setdefault(side, []).append(z)
        for side in self.by_side:
            self.by_side[side].sort(key=lambda z: (z.t0, z.t1))

    def hazards_at(self, side: str, t: float) -> list[Molly]:
        """Side `side`'s mollies burning at `t` (`t0 <= t < t1`): they restrict `unknown[side]`."""
        return [z for z in self.by_side.get(side, []) if z.active(t)]

    def reopen_times(self, side: str) -> list[float]:
        """The sorted, distinct ends of `side`'s mollies: the instants its unknown may spread into them again."""
        return sorted({z.t1 for z in self.by_side.get(side, [])})

    def transitions(self) -> list[float]:
        """Every start and end, sorted and distinct: the analytic instants the engine's clock must stop at."""
        return sorted({x for zs in self.by_side.values() for z in zs for x in (z.t0, z.t1)})
