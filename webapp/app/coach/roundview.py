"""One stored round's primitives for the coach: who was where, who was alive, how far apart. Pure over the
decoded blob (app/replays/format.py); every later module reads rounds through this."""

from __future__ import annotations

import math
from bisect import bisect_right

from app.coach import areas
from app.replays import format as fmt


SAME_DEATH_S = 1.0      # app/control/engine.py's: a site death this close to the replay's own is the same death
STALE_SAMPLE_S = 1.0    # a living player's nearest earlier sample older than this is not a position (tracks are
                        # condensed at sub-second steps, so a real track never trips it)
HALF_S = 3.5            # an unfinished defuse attempt this long (as displayed, 2 decimals) is a half, else a tap
SPIKE_TIMER_S = 45.0    # PROVISIONAL(D3): the spike's fuse, from plant to detonation
_UTIL_KINDS = ("flash", "nearsight", "ability", "cast")
_ROLE_GROUP = {"attack": "A", "defense": "B"}      # app/control/engine.py:321


class RoundView:
    def __init__(self, blob: dict, names: dict[int, str], uv_per_unit: float, map_name: str,
                 callout_table: dict | None = None, groups: dict[int, str] | None = None, db_deaths=(),
                 roles: dict[int, str] | None = None):
        """`roles` is the link's `sides` (slot -> "attack"/"defense"): where the blob gives a player no side the
        group comes from the round's role, as the control engine does, and only then from `groups` (the page's
        fixed team group)."""
        self.blob, self.names, self.uv_per_unit, self.map_name = blob, names, uv_per_unit, map_name
        self._table = callout_table
        self._side = {}
        for p in blob["players"]:
            slot = int(p["slot"])
            group = p.get("side")
            if group is None:
                group = _ROLE_GROUP.get((roles or {}).get(slot))
            if group is None:
                group = (groups or {}).get(slot)
            if group is None:
                raise ValueError(f"round {blob.get('round')}: slot {slot} has no side group and the link names none")
            self._side[slot] = group
        self._db_deaths = [(int(s), float(t)) for s, t in db_deaths]
        self._life: dict[int, list] = {}
        self._samples: dict[int, list] = {}
        self.kills = sorted(blob.get("kills", []), key=lambda k: k["t"])

    number = property(lambda self: int(self.blob["round"]))
    t_end = property(lambda self: float(self.blob["t_end"]))
    t_decided = property(lambda self: float(self.blob["t_decided"]))
    slots = property(lambda self: sorted(self._side))

    def side(self, slot: int) -> str:
        return self._side[slot]

    def teammates(self, slot: int) -> list[int]:
        return [s for s in self.slots if s != slot and self._side[s] == self._side[slot]]

    def enemies(self, slot: int) -> list[int]:
        return [s for s in self.slots if self._side[s] != self._side[slot]]

    def _intervals(self, slot: int) -> list:
        """The slot's lives as [t0, t1]: a stored end of None (alive to the end of the round) reads as infinity,
        and a death only the site's data has ends the life it falls in, as the control engine does."""
        if slot not in self._life:
            lives = [[iv[0], math.inf if iv[1] is None else iv[1]]
                     for iv in self.blob.get("alive", {}).get(str(slot), [])]
            for dead, t in self._db_deaths:
                if dead != slot:
                    continue
                for life in lives:
                    if life[0] <= t < life[1] - SAME_DEATH_S:
                        life[1] = t
                        break
            self._life[slot] = lives
        return self._life[slot]

    def alive(self, slot: int, t: float) -> bool:
        return any(t0 <= t < t1 for t0, t1 in self._intervals(slot))

    def living(self, side: str, t: float) -> list[int]:
        return [s for s in self.slots if self._side[s] == side and self.alive(s, t)]

    def samples(self, slot: int) -> list:
        if slot not in self._samples:
            out = []
            for segment in self.blob.get("tracks", {}).get(str(slot), []):
                out.extend(fmt.decode_segment(segment, self.blob["hz"]))
            self._samples[slot] = sorted(out)
        return self._samples[slot]

    def _sample_at(self, slot: int, t: float):
        samples = self.samples(slot)
        i = bisect_right(samples, (t + 0.5 / self.blob["hz"], math.inf, math.inf, math.inf)) - 1
        return samples[i] if i >= 0 else None

    def area(self, u: float, v: float) -> dict | None:
        return areas.area_at(self.map_name, u, v, self._table)

    def area_names(self) -> list[str]:
        """Every named area of this map, longest first."""
        return sorted({c["name"] for c in areas.callouts(self.map_name, self._table)}, key=lambda a: (-len(a), a))

    def position(self, slot: int, t: float) -> dict:
        if self.alive(slot, t):
            sample = self._sample_at(slot, t)
            if sample is not None and t - sample[0] <= STALE_SAMPLE_S:
                _, u, v, yaw = sample
                return {"alive": True, "u": u, "v": v, "yaw": yaw, "area": self.area(u, v)}
            # alive but not placeable (no sample yet, or a gap in the track): never read as dead
            return {"alive": True, "u": None, "v": None, "area": None, "position": "unknown"}
        ended = [t1 for _, t1 in self._intervals(slot) if t1 <= t]
        samples = self.samples(slot)
        if not ended or not samples:
            return {"alive": False, "died_at": None}
        died = max(ended)
        _, u, v, _ = self._sample_at(slot, died) or samples[-1]
        return {"alive": False, "died_at": died, "u": u, "v": v, "area": self.area(u, v)}

    def metres(self, uv_distance: float) -> float:
        return uv_distance / self.uv_per_unit / 100.0

    def distance_m(self, a: int, b: int, t: float) -> float | None:
        pa, pb = self.position(a, t), self.position(b, t)
        if not (pa["alive"] and pb["alive"]) or pa["u"] is None or pb["u"] is None:
            return None
        return self.metres(math.hypot(pa["u"] - pb["u"], pa["v"] - pb["v"]))

    def spike(self) -> dict | None:
        """The round's Bomb entry (the plant and its defuse attempts), or None."""
        return next((e for e in self.blob.get("util", []) if e.get("kind") == "Bomb"), None)

    def defuse_attempts(self) -> list[dict]:
        """One row per defuse attempt on the Bomb entry: start `t`, stop `t1`, `seconds`, defuser `by`, and
        `result` tap / half / defused. A null stop runs to the defuser's death or the cap (`until` says which)."""
        bomb = self.spike()
        if bomb is None:
            return []
        cap = self.t_end if bomb.get("t1") is None else min(float(bomb["t1"]), self.t_end)
        out, halved = [], False
        for start, stop, slot, finished in bomb.get("defuses") or []:
            start = float(start)
            row = {}
            if stop is None:
                life = next((iv for iv in self._intervals(slot) if iv[0] <= start < iv[1]), None)
                if life is not None and life[1] < cap:
                    stop, row["until"] = float(life[1]), "death"
                else:
                    stop, row["until"] = cap, "round_end"
            stop = float(stop)
            seconds = round(stop - start, 2)
            result = "defused" if finished else "half" if seconds >= HALF_S else "tap"
            out_row = {"t": round(start, 3), "t1": round(stop, 3), "kind": "defuse", "by": self.names.get(slot),
                       "seconds": seconds, "result": result}
            if bomb.get("t") is not None:
                # PROVISIONAL(D3): time left on the spike's fuse when the attempt began
                out_row["spike_left_s"] = round(float(bomb["t"]) + SPIKE_TIMER_S - start, 2)
            out_row.update(row)
            if halved:
                out_row["resumed"] = True      # PROVISIONAL(D3): an attempt after an earlier half
            halved = halved or result == "half"
            out.append(out_row)
        return out

    def utility(self, by: int | None = None, spike: bool = True) -> list[dict]:
        """Flashes, nearsights, abilities and movement casts, in stored order, with names for slots. A flash's
        `hits_complete` is true only when an empty or short hit list is proven to be the whole list. The Bomb
        entry's "Spike" row is left out when `spike` is False."""
        out = []
        for e in self.blob.get("util", []):
            if e.get("k") not in _UTIL_KINDS or (by is not None and e.get("by") != by):
                continue
            if not spike and e.get("kind") == "Bomb":
                continue
            row = {"t": e["t"], "kind": e["k"], "by": self.names.get(e.get("by"))}
            ability = e.get("name") or e.get("code") or e.get("ability")
            if ability:
                row["ability"] = ability
            if e.get("hits") is not None:
                proof = e.get("activation") or {}
                row["hits"] = [{"name": self.names.get(h[0]), "seconds": h[2] if len(h) > 2 else None}
                               for h in e["hits"]]
                row["hits_complete"] = bool(proof.get("state") == "completed" and proof.get("targets_complete"))
            out.append(row)
        return out

    def damage(self) -> list[dict]:
        return [{"t": e["t"], "kind": "damage", "by": self.names.get(e.get("by")),
                 "target": self.names.get(e.get("target")), "source": e.get("src"), "hits": e.get("n")}
                for e in self.blob.get("util", []) if e.get("k") == "damage"]
