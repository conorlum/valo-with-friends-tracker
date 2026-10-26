"""Critical Util: what each device and molly held of the map-control unknown (design:
docs/superpowers/specs/2026-10-21-critical-util-design.md, "Hold space"; owner, 2026-10-21: "trips hold unknown").

The unknown is where an enemy could be (app/control/engine.py `Unknown`): it spreads through walkable cells and the
team's live control stops it, a device's watched cells included, and a burning molly blocks it
(app/control/mollies.py). Stepped by the gap run beside the gap detector (app/control/task.py), one tick record
at a time (app/control/observe.py `TickRecord`), it measures for each trip, area watcher, camera or turret and each
molly, while it was up the way the engine has it (a device watching and its owner alive, a molly burning), its
owner's team had enemies alive and the round was not yet decided:

- `front_s`: seconds the unknown was right at its edge (a neighbouring cell could hold an enemy) without being
  inside it: the device or molly was the line the unknown stopped at.
- `held_m2s`: square metres times seconds of its cells that no teammate's own view covered: ground only it held.
- `crossed_s`: seconds the unknown was inside its cells (an enemy could be past it or in it).

Pure numpy over the record; never changes the engine's arrays.
"""

from __future__ import annotations

import numpy as np

STATIC_KINDS = ("trip", "area")         # Watcher kinds with fixed cells
DYNAMIC_KINDS = ("camera", "turret")    # watch what they see: their owner's utility mask on the tick
MAX_DT_S = 1.0


class UnknownProbe:
    def __init__(self, rnd):
        from app.control.route_runtime import topology_for

        self.rnd, geo = rnd, rnd.geo
        self.topo = topology_for(geo)
        self.cell_m2 = float(geo.cell_m) ** 2
        self.n = geo.n
        self.items: list[dict] = []
        for w in rnd.watchers:
            if w.by is None or rnd.team.get(w.by) is None:
                continue
            if w.kind in STATIC_KINDS and w.cells is not None:
                mask = np.zeros(self.n, bool)
                mask[w.cells] = True
                self._add("device", w.kind, w.by, w.t0, w.t1, mask, watcher=w)
            elif w.kind in DYNAMIC_KINDS:
                self._add("device", w.kind, w.by, w.t0, w.t1, None, watcher=w)
        for side, molly, nodes in getattr(rnd, "mollies", ()):
            mask = np.asarray(nodes, bool) if np.asarray(nodes).dtype == bool else _mask_of(nodes, self.n)
            self._add("molly", "molly", molly.by, molly.t0, molly.t1, mask, side=side,   # kind "molly" too
                      uv=(molly.u, molly.v))
        self.prev_t: float | None = None
        self.error: str | None = None

    def _add(self, what, kind, by, t0, t1, mask, side=None, watcher=None, uv=None):
        ring = None if mask is None else (self.topo.dilate(mask) & ~mask)
        self.items.append({"what": what, "kind": kind, "by": by, "t0": float(t0), "t1": float(t1), "mask": mask,
                           "ring": ring, "side": side or self.rnd.team.get(by), "watcher": watcher,
                           "id": getattr(watcher, "src", None), "uv": uv,
                           "front_s": 0.0, "held_m2s": 0.0, "crossed_s": 0.0, "up_s": 0.0})

    def _live(self, item, t: float) -> bool:
        """Up the way the engine has it: a device while it is watching (not switched off: a Killjoy device out of
        range, a suppressed Cypher) and its owner is alive (Q71); a molly while it burns. Never after the round
        was decided."""
        if t >= self.rnd.t_decided:
            return False
        w = item["watcher"]
        if w is None:
            return item["t0"] <= t < item["t1"]
        from app.control.engine import _watching

        return _watching(w, t) and self.rnd.alive(w.by, t)

    def step(self, rec) -> None:
        """One tick. Errors are kept, never raised: the gap run is never failed by this probe."""
        if self.error is not None:
            return
        try:
            self._step(rec)
        except Exception as error:  # noqa: BLE001 - this probe's failure, reported in finish()
            self.error = f"{type(error).__name__}: {error}"

    def _step(self, rec) -> None:
        t = float(rec.t)
        dt = 0.0 if self.prev_t is None else min(max(t - self.prev_t, 0.0), MAX_DT_S)
        self.prev_t = t
        if dt <= 0:
            return
        unknown, view, live = {}, {}, []
        for item in self.items:
            if not self._live(item, t):
                continue
            side = item["side"]
            if side not in unknown:
                enemies = (rec.unknown or {}).get(side) or {}
                if not enemies:
                    unknown[side] = None        # no enemy alive: nothing to hold
                else:
                    u = np.zeros(self.n, bool)
                    for arr in enemies.values():
                        u |= self._nodes(np.asarray(arr) >= 0)
                    unknown[side] = u
                seen = np.zeros(self.n, bool)
                for p in rec.players.values():
                    if p.team == side and p.view is not None:
                        seen |= self._nodes(p.view)
                view[side] = seen
            if unknown[side] is None:
                continue
            mask, ring = item["mask"], item["ring"]
            if mask is None:                    # a camera or turret: what its owner's utility watches this tick
                p = rec.players.get(item["by"])
                if p is None or p.utility is None:
                    continue
                mask = self._nodes(p.utility)
                if not mask.any():
                    continue
                ring = self.topo.dilate(mask) & ~mask
            live.append((item, mask, ring))
        # ground two of a team's items hold at once is held once: each cell's area is split between them
        count = {}
        for item, mask, _ in live:
            side = item["side"]
            count[side] = count.get(side, 0) + mask.astype(np.int16)
        for item, mask, ring in live:
            side, u = item["side"], unknown[item["side"]]
            item["up_s"] += dt
            if (mask & u).any():
                item["crossed_s"] += dt
            elif (ring & u).any():
                item["front_s"] += dt
            only = mask & ~view[side]
            if only.any():
                held = 0.0
                for k in np.unique(count[side][only]).tolist():
                    held += self.rnd.geo.area(only & (count[side] == k)) / k
                item["held_m2s"] += held * self.cell_m2 * dt

    def _nodes(self, arr) -> np.ndarray:
        """A mask as a node mask: a flat GRID*GRID one (a player's view) goes to every floor of each cell."""
        arr = np.asarray(arr, bool)
        return arr if len(arr) == self.n else self.rnd.geo.to_nodes(arr)

    def finish(self) -> dict:
        return {"error": self.error,
                "items": [{"what": i["what"], "kind": i["kind"], "by": i["by"], "t0": round(i["t0"], 2),
                           "t1": round(i["t1"], 2), "id": i["id"], "uv": None if i["uv"] is None else list(i["uv"]),
                           "up_s": round(i["up_s"], 2), "front_s": round(i["front_s"], 2),
                           "held_m2s": round(i["held_m2s"], 1), "crossed_s": round(i["crossed_s"], 2)}
                          for i in self.items]}


def _mask_of(nodes, n: int) -> np.ndarray:
    mask = np.zeros(n, bool)
    mask[np.asarray(nodes, int)] = True
    return mask
