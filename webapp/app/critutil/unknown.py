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

Separately, InfoMeasure reads the engine's applied operations and watcher footprints for per-cast clear
area, located enemies and hold seconds. Its failure leaves these legacy items intact.

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
        self.info_error: str | None = None
        try:
            self.info = InfoMeasure(self)
        except Exception as error:  # reporting must never disable legacy holds
            self.info_error = f"{type(error).__name__}: {error}"

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
        if getattr(self, "info_error", None) is None and hasattr(self, "info"):
            try:
                self.info.step(rec)
            except Exception as error:  # noqa: BLE001 - independent from legacy reporting
                self.info_error = f"{type(error).__name__}: {error}"
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
        out = {"error": self.error,
                "items": [{"what": i["what"], "kind": i["kind"], "by": i["by"], "t0": round(i["t0"], 2),
                           "t1": round(i["t1"], 2), "id": i["id"], "uv": None if i["uv"] is None else list(i["uv"]),
                           "up_s": round(i["up_s"], 2), "front_s": round(i["front_s"], 2),
                           "held_m2s": round(i["held_m2s"], 1), "crossed_s": round(i["crossed_s"], 2)}
                          for i in self.items]}
        if hasattr(self, "info_error"):
            try:
                out["info"] = None if self.error or self.info_error else self.info.finish()
            except Exception:  # noqa: BLE001 - preserve the legacy figures even if serialization fails
                out["info"] = None
        return out


class InfoMeasure:
    """Applied information and held edges, independent of the legacy shared-cell accounting.

    Regions belong to the engine and change in place. Keep our own previous masks, including each enemy's
    region for locate attribution. Identity comes only from applied operations or watcher actors/items.
    """

    def __init__(self, probe):
        from app.critutil.extract import _info_items_by_cast, build_casts

        self.probe, self.rnd = probe, probe.rnd
        self.casts = build_casts(self.rnd, self.rnd.blob)
        self.by_id = {actor: cast for cast in self.casts for actor in cast.ids}
        self.rows = {tuple(row.get(k) for k in ("by", "code", "name", "t")): row
                     for row in self.rnd.blob.get("util") or ()}
        self.by_item = _info_items_by_cast(self.rnd, self.casts, {"items": probe.items})
        self.previous: dict = {}
        self.prev_t = None
        self.measures: dict = {}
        self.located: dict = {}
        self.started: set = set()
        self.held = [(item, index) for index, item in enumerate(probe.items)]
        for w in self.rnd.watchers:
            if w.kind == "drone" and w.by is not None and self.rnd.team.get(w.by) is not None:
                row_key = next((key for key, row in self.rows.items()
                                if row.get("by") == w.by and row.get("path") is w.path), None)
                self.held.append(({"kind": w.kind, "by": w.by, "t0": w.t0, "t1": w.t1,
                                   "side": self.rnd.team[w.by], "watcher": w, "mask": None,
                                   "id": w.src, "row_key": row_key, "uv": None}, None))

    def _entry(self, *, cast_id=None, row_key=None, item=None, index=None, t=0.0):
        from app.critutil.extract import _cast_of_row

        actor = item.get("id") if item is not None else None
        identity = cast_id if cast_id is not None else actor
        if row_key is None and item is not None:
            row_key = item.get("row_key")
        cast = self.by_id.get(identity)
        if cast is None and row_key is not None:
            row = self.rows.get(tuple(row_key))
            if row is not None:
                cast = _cast_of_row(row, self.casts, self.by_id)
        if cast is None and item is not None:
            cast = self.by_item.get(index)
        if cast is not None and (cast.entry or {}).get("ability") == "Spycam dart":
            # Use the same live-camera join as extract._info_by_cast before sharing any cells.
            cast = _cast_of_row({"by": cast.by, "t": t, "code": "Gumshoe", "name": "E_PossessableCamera"},
                                [c for c in self.casts if (c.entry or {}).get("ability") == "Spycam"], self.by_id)
        key = (("cast", identity) if identity is not None else
               ("row", tuple(row_key)) if row_key is not None else ("item", index))
        if cast is not None:
            key = ("joined", cast.i)
        if key not in self.measures:
            from app.critutil.info import empty

            self.measures[key] = {"cast_id": identity, "row_key": row_key, "item": index, "id": actor,
                                  "by": item["by"] if item is not None else (row_key[0] if row_key else None),
                                  "kind": item["kind"] if item is not None else "info",
                                  "t0": float(item["t0"] if item is not None else t),
                                  "uv": None if item is None or item["uv"] is None else list(item["uv"]),
                                  **empty()}
        elif item is not None:
            self.measures[key].update(item=index, id=actor, kind=item["kind"], by=item["by"])
        return key

    def step(self, rec):
        t = float(rec.t)
        dt = 0.0 if self.prev_t is None else min(max(t - self.prev_t, 0.0), MAX_DT_S)
        self.prev_t = t
        current = {side: {enemy: self.probe._nodes(np.asarray(arr) >= 0).copy()
                          for enemy, arr in enemies.items()} for side, enemies in (rec.unknown or {}).items()}
        if t >= self.rnd.t_decided:
            self.previous = current
            return
        for side, enemies in current.items():
            if not enemies:
                continue
            u = np.zeros(self.probe.n, bool)
            before = np.zeros(self.probe.n, bool)
            seen = np.zeros(self.probe.n, bool)
            for arr in enemies.values():
                u |= arr
            for arr in self.previous.get(side, {}).values():
                before |= arr
            for p in rec.players.values():
                if p.team == side and p.view is not None:
                    seen |= self.probe._nodes(p.view)
            removed = before & ~u & ~seen
            footprints = {}
            for cast_id, row_key, kind, enemy, nodes in (getattr(rec, "applied", {}) or {}).get(side, ()):
                if kind not in ("exclude", "restrict", "locate") or enemy not in enemies:
                    continue
                if cast_id is None and row_key is None:
                    continue
                key = self._entry(cast_id=cast_id, row_key=row_key, t=t)
                mask = _mask_of(nodes, self.probe.n)
                footprints.setdefault(key, np.zeros(self.probe.n, bool))[:] |= mask
                if (kind == "locate" and enemy in self.previous.get(side, {})
                        and not (enemies[enemy] & seen).any()):
                    self.located.setdefault(key, set()).add(enemy)
                    self.measures[key]["located"] = len(self.located[key])
            watchers = []
            for index, (item, legacy_index) in enumerate(self.held):
                if item["side"] != side or not self.probe._live(item, t):
                    continue
                mask, w = item["mask"], item["watcher"]
                dynamic = item["kind"] in ("camera", "drone", "turret")
                if dynamic:
                    if w.in_use is not None and not any(a <= t < b for a, b in w.in_use):
                        continue
                    p = rec.players.get(item["by"])
                    if p is None or p.utility is None:
                        continue
                    mask = self.probe._nodes(p.utility).copy()
                    for other, _ in self.held:
                        if (other["by"] == item["by"] and other["kind"] in STATIC_KINDS
                                and self.probe._live(other, t)):
                            mask &= ~other["mask"]
                if not mask.any():
                    continue
                key = self._entry(item=item, index=legacy_index, t=t)
                if dynamic and (item["kind"] != "turret" or index not in self.started):
                    footprints.setdefault(key, np.zeros(self.probe.n, bool))[:] |= mask
                self.started.add(index)
                watchers.append((key, mask, self.probe.topo.dilate(mask) & ~mask, dynamic))
            # One cast holds its combined live footprint. Unknown in any piece means it was crossed;
            # several pieces holding at once are still one tick, and expired pieces contribute nothing.
            combined = {}
            for key, mask, _, dynamic in watchers:
                if key not in combined:
                    combined[key] = (mask.copy(), dynamic)
                else:
                    combined[key][0][:] |= mask
            watchers = [(key, mask, self.probe.topo.dilate(mask) & ~mask, dynamic)
                        for key, (mask, dynamic) in combined.items()]
            clears = {key: mask & removed for key, mask in footprints.items()}
            count = np.zeros(self.probe.n, np.int32)
            for mask in clears.values():
                count += mask
            for key, mask in clears.items():
                if mask.any():
                    # Geometry.area carries the floor weights, just as held_m2s does.
                    self.measures[key]["cleared_m2"] += sum(
                        self.rnd.geo.area(mask & (count == n)) / n for n in np.unique(count[mask])
                    ) * self.probe.cell_m2
            clearing = {key for key, _, _, dynamic in watchers
                        if dynamic and key in clears and clears[key].any()}
            holding = set()
            for key, mask, ring, _ in watchers:
                if key in clearing:
                    continue
                if not (mask & u).any() and (ring & u).any() and (ring & ~u).any():
                    holding.add(key)
            for key in holding:
                self.measures[key]["hold_s"] += dt
        self.previous = current

    def finish(self):
        return [{**measure, "cleared_m2": round(float(measure["cleared_m2"]), 1),
                 "hold_s": round(float(measure["hold_s"]), 2), "located": int(measure["located"]),
                 "t0": round(float(measure["t0"]), 2)} for measure in self.measures.values()]


def _mask_of(nodes, n: int) -> np.ndarray:
    mask = np.zeros(n, bool)
    mask[np.asarray(nodes, int)] = True
    return mask
