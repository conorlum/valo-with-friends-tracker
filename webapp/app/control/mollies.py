"""The damaging-molly traversal contract (docs/superpowers/specs/2026-10-06-replay-player-state-design.md section 5;
the plan's P02 step 6 and its amendment F4/F8/F15/F20; decision D6). Pure: nothing here touches reachability yet
(P07a wires it into the Unknown spread, behind CONTROL 9).

Which zones count is decided here, in code: MOLLY_KEYS, the five sustained DAMAGE_ZONES entries. Their figures
(`radius_m`, `seconds`) live in app/control/utility.json `molly`, provisional, so a changed number changes
`figures_hash` and a prose edit doesn't.

A molly is an `ability` row of `<code>_<name>` in MOLLY_KEYS (not a `Projectile`, the flight). It burns over
`[t, min(t + seconds, t1))`: from its landing for its figure's seconds, capped at the row's end (the actor close,
longer than the fire). The owner is the row's `by`; a row whose owner, place, time or figures are unknown is
skipped and counted, never guessed hostile.

Hazards are per side, keyed by the OWNER's side: `hazards_at("A", t)` are side A's burning mollies, which restrict
`unknown[A]` (where A believes B players may be: B can't walk through A's fire). They never restrict `unknown[B]`
(a player isn't blocked by their own team's molly). A molly whose owner has no side this round is counted and
skipped."""

from __future__ import annotations

import math
from typing import NamedTuple

MOLLY_KEYS = frozenset({"Phoenix_MolotovFire", "Sarge_Q_Molotov_Production", "Pandemic_AcidMolotov_NewMolotov",
                        "Killjoy_4_BeeSwarm_Damage", "Aggrobot_C_ExplodeyPatch"})


class Molly(NamedTuple):
    by: int
    u: float
    v: float
    radius_m: float
    t0: float
    t1: float

    def active(self, t: float) -> bool:
        return self.t0 <= t < self.t1


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _count(diagnostics: dict, case: str) -> None:
    diagnostics[case] = diagnostics.get(case, 0) + 1


def molly_key(row: dict) -> str | None:
    """The row's allow-list key when it is a damaging molly, else None."""
    if not isinstance(row, dict) or row.get("k") != "ability" or row.get("kind") == "Projectile":
        return None
    key = f"{row.get('code')}_{row.get('name')}"
    return key if key in MOLLY_KEYS else None


def molly_zones(util: list, figures: dict) -> tuple[list[Molly], dict]:
    """The round's damaging mollies as `Molly(by, u, v, radius_m, t0, t1)` in row order, and the diagnostics of
    what was skipped."""
    zones: list[Molly] = []
    diagnostics: dict = {}
    for row in util or []:
        key = molly_key(row)
        if key is None:
            continue
        by = row.get("by")
        if isinstance(by, bool) or not isinstance(by, int) or not 0 <= by <= 9:
            _count(diagnostics, "molly without a known owner")
            continue
        figure = figures.get(key) if isinstance(figures, dict) else None
        radius = _num((figure or {}).get("radius_m"))
        seconds = _num((figure or {}).get("seconds"))
        if radius is None or seconds is None or radius <= 0 or seconds <= 0:
            _count(diagnostics, "molly without figures")
            continue
        u, v = _num(row.get("u")), _num(row.get("v"))
        if u is None or v is None:
            _count(diagnostics, "molly without a place")
            continue
        t0 = _num(row.get("t"))
        if t0 is None:
            _count(diagnostics, "molly without a time")
            continue
        t1 = t0 + seconds
        end = _num(row.get("t1"))
        if end is not None:
            t1 = min(t1, end)
        if t1 <= t0:
            _count(diagnostics, "molly ended before it burned")
            continue
        zones.append(Molly(by, u, v, radius, t0, t1))
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
