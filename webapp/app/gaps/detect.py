"""Predicted gaps (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 5): unknown space behind
a player, read online from the control engine's tick records (app/control/observe.py).

A cell of an enemy's unknown is *exposed* to a player P when P's eye has a clear line to it: a full-circle
`geometry.cast` from P's real position at P's eye height, with the tick's smokes (R13). It *qualifies* when it
is exposed, lies in P's rear 120 degrees, and the enemy has been unlocated for MIN_UNSEEN_S (or never located
this round). One gap per victim life and choke sequence: it opens when a cell of that sequence first
qualifies, stays latched, and closes CLOSE_AFTER_S after its last exposure to a candidate's cell (facing
ignored), on the victim's death, or at t_decided; nothing is detected after t_decided.

Order within a tick:
1. Open gaps are expired against their previous `t_last_exposed` and closed on a death (R10).
2. The tick's releases (nodes the team stopped observing) are recorded with who observed them and why (R12).
3. Each victim's exposure is judged. An enemy with a locating event in this tick's record is judged against
   the previous tick's unknown and the locating history before this tick's events (R7).
4. This tick's events join the locating history.

Stacks (R1): in `finish`, once every gap is closed, a predicted gap whose choke sequence is a prefix of a longer
sequence open for the same victim life at an overlapping time is folded into the longest one (`merge_stacks`);
the reported row is the merged one. `GapDetector(merge=False)` keeps the pre-merge rows.

Back-shots (section 6) are added in `finish` by app/gaps/backshots.py, after the merge; `killed` and
`victim_won_at` are set after them, on `shot`'s window (open, or RESULT_WINDOW_S after a stand)."""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from app.control.engine import RAY_STEP_DEG
from app.control.geometry import cast

MIN_UNSEEN_S = 5.0
CLOSE_AFTER_S = 5.0
FLICKER_S = 1.0
RESULT_WINDOW_S = 3.0
SHOT_LOOKBACK_S = 0.5
BEHIND_DEG = 120.0
ROUTE_THIN_S = 0.5
GAPS_REVISION = 2    # 2: stack merge (R1)
TURN_DEG = 1.0      # a facing change of at most this between two ticks is not a turn (aim noise)
CAUSE_ORDER = ("route_released", "victim_turned", "victim_moved", "open_timing")
REASONS = ("died", "turned", "moved", "blinded", "smoked", "utility_expired", "utility_left", "other")
_CODE = {r: i for i, r in enumerate(REASONS)}
FULL_CIRCLE = np.arange(0.0, 360.0, RAY_STEP_DEG)


def off_facing(yaw: float, x0: float, y0: float, x1: float, y1: float) -> float:
    """Degrees (0..180) between facing `yaw` from (x0, y0) and the bearing to (x1, y1)."""
    bearing = math.degrees(math.atan2(y1 - y0, x1 - x0))
    return float(abs((bearing - yaw + 180) % 360 - 180))


def round_context(rnd, t: float, victim_sees_enemy: bool | None) -> dict:
    """A row's context at t (section 5 Context, section 7): time into round, alive counts, spike state and
    whether the victim saw an enemy (None for a back-shot: no view masks after the last tick)."""
    alive = Counter(team for s, team in rnd.team.items() if rnd.alive(s, t))
    return {"t_round": round(t - rnd.t_start, 3), "alive": {"A": int(alive["A"]), "B": int(alive["B"])},
            "spike": "planted" if rnd.plant is not None and rnd.plant <= t else "not planted",
            "victim_sees_enemy": victim_sees_enemy}


def _turned(yaw0: float, yaw1: float) -> bool:
    return abs((yaw1 - yaw0 + 180) % 360 - 180) > TURN_DEG


def _smoke_key(s):
    return s if isinstance(s, tuple) else id(s)     # a utility Wall compares by identity


@dataclass
class Gap:
    kind: str                         # "predicted" | "backshot"
    victim: int
    team: str                         # the victim's side group, "A" / "B"
    seq: int                          # choke sequence id in the victim team's route log; -1 for none known
    choke_seq: tuple | None
    t_open: float
    spot: int                         # node
    victim_node: int
    distance_m: float
    angle_deg: float
    route: list                       # [[[t, x px, y px], ...], ...]: unbroken pieces
    life: int = 0
    cause: str | None = None
    cause_detail: dict | None = None
    candidates: dict = field(default_factory=dict)     # enemy slot -> distance m from the spot when they joined
    t_last_exposed: float | None = None
    t_close: float | None = None
    qualified_s: float = 0.0
    checked_at: list = field(default_factory=list)
    stood_at: float | None = None
    stood_by: int | None = None
    shot_at: float | None = None
    shot_by: int | None = None
    killed_at: float | None = None
    killed_by: int | None = None
    victim_won_at: float | None = None
    context: dict = field(default_factory=dict)
    linked: "Gap | None" = None
    stood_times: dict = field(default_factory=lambda: defaultdict(list))   # enemy -> every tick stood in it
    joined: dict = field(default_factory=dict)        # enemy -> when they joined the candidates (back-shot linking)
    qual_spans: list = field(default_factory=list)    # [[a, b], ...]: the intervals counted in qualified_s
    _qual: bool = False               # qualified at the last tick (its state holds until the next)
    _checked: bool = False            # the victim's view covered the spot at the last tick

    @property
    def flicker(self) -> bool:
        return self.kind == "predicted" and self.qualified_s < FLICKER_S

    def _qualified(self, a: float, b: float) -> None:
        """Counts [a, b] as qualifying time, kept as a span too (a span continuing the last one extends it)."""
        if b <= a:
            return
        self.qualified_s += b - a
        if self.qual_spans and self.qual_spans[-1][1] == a:
            self.qual_spans[-1][1] = b
        else:
            self.qual_spans.append([a, b])


class GapDetector:
    """`step(rec, logs)` per tick record in time order (an observer, or the tick cache's replay), then
    `finish()` for the gaps. `logs` is side -> RouteLog (Unknown.log)."""

    def __init__(self, geo, rnd, merge: bool = True):
        self.geo, self.rnd, self.merge = geo, rnd, merge
        n = geo.n
        self.notes: Counter = Counter()
        self._noted: set = set()
        self.gaps: list[Gap] = []
        self.open: dict[tuple, Gap] = {}                   # (victim, life, seq id) -> gap
        self.located: dict[str, dict[int, list]] = {"A": defaultdict(list), "B": defaultdict(list)}
        # per side and node: when it was last released (stopped being observed), by whom, how and why
        self.rel_t = {s: np.full(n, -np.inf) for s in ("A", "B")}
        self.rel_player = {s: np.full(n, -1, np.int64) for s in ("A", "B")}
        self.rel_util = {s: np.zeros(n, bool) for s in ("A", "B")}
        self.rel_reason = {s: np.zeros(n, np.int8) for s in ("A", "B")}
        self._observed: dict = {"A": None, "B": None}
        self._owner: dict = {"A": None, "B": None}         # (owner per node, credited to utility per node)
        self._entries_prev: dict = {"A": {}, "B": {}}      # the last tick's entry arrays (copies), for R7
        self._players_prev: dict = {}                      # slot -> the last tick's PlayerView
        self._victim_prev: dict = {}                       # slot -> what the last tick saw of them as a victim
        self._tick_cache: dict = {}
        self.smokes_prev: list = []
        self.prev_t: float | None = None
        self.done = False
        self._finished: list | None = None
        self.timing = {"steps": 0, "seconds": 0.0, "casts": 0}

    # ---------------------------------------------------------------- helpers

    def _note(self, case: str, slot: int | None = None) -> None:
        """Count a case once per (case, slot) per round, as the engine's `missing` does (R20)."""
        if (case, slot) not in self._noted:
            self._noted.add((case, slot))
            self.notes[case] += 1

    def _metres(self, x0, y0, x1, y1) -> float:
        return float(math.hypot(float(x1) - float(x0), float(y1) - float(y0)) * self.geo.m_per_px)

    def _cell(self, node: int) -> int:
        return int(self.geo.node_cell[node]) if self.geo.heights is not None else int(node)

    def _life(self, slot: int, t: float) -> int:
        for i, (a, b) in enumerate(self.rnd.lives.get(slot, [])):
            if a <= t < b:
                return i
        return -1

    def _life_end(self, g: Gap) -> float:
        lives = self.rnd.lives.get(g.victim, [])
        return float(lives[g.life][1]) if 0 <= g.life < len(lives) else math.inf

    @staticmethod
    def _view(p) -> np.ndarray:
        """The player's own sight (R12): `view`, or live less utility on a record without one."""
        return p.view if p.view is not None else p.live & ~p.utility

    def _sight(self, x, y, eye_z, node, smokes) -> np.ndarray:
        """Every node with a clear line from (x, y) at eye height `eye_z`, in any direction (R13)."""
        record: dict = {}
        out = cast(self.geo, float(x), float(y), FULL_CIRCLE, smokes, eye_z=eye_z, own=int(node), record=record)
        self.timing["casts"] += 1
        if record.get("unresolved_rays"):
            self._note("exposure rays through unresolved terrain (2D sight there)")
        return out

    def last_located(self, side: str, enemy: int, before: float, inclusive: bool = True,
                     ignore_gunfire_from: float | None = None) -> float | None:
        times = [te for te, kind in self.located[side].get(enemy, ())
                 if (te <= before if inclusive else te < before)
                 and not (ignore_gunfire_from is not None and kind == "gunfire" and te >= ignore_gunfire_from)]
        return max(times) if times else None

    def wait_over(self, side: str, enemy: int, t: float, **kw) -> bool:
        last = self.last_located(side, enemy, t, **kw)
        return last is None or t - last >= MIN_UNSEEN_S

    def _route(self, log, entry: int) -> list:
        entries = log.trace(entry)
        pts, last = [], None
        for e in entries:
            te = float(log.t[e])
            if last is None or te - last >= ROUTE_THIN_S or e == entries[-1]:
                x, y = self.geo.centres[int(log.node[e])]
                pts.append([round(te, 3), round(float(x), 1), round(float(y), 1)])
                last = te
        return [pts]

    # ---------------------------------------------------------------- per tick

    def step(self, rec, logs) -> None:
        if self.done:
            return
        started = time.perf_counter()
        t = float(rec.t)
        self._tick_cache = {}
        self._expire(t)                                    # R10: against the previous t_last_exposed
        if t >= self.rnd.t_decided:                        # nothing is detected once the round is decided
            # but events from before it, snapped onto this tick, still join the history (back-shots read it)
            for side, evs in rec.events.items():
                for enemy, te, kind in evs:
                    if te < self.rnd.t_decided:
                        self.located[side][int(enemy)].append((float(te), str(kind)))
            self._close_all(self.rnd.t_decided)
            self.done = True
            return
        for g in self.open.values():                       # the last tick's state held until now
            if g._qual and self.prev_t is not None:
                g._qualified(self.prev_t, t)
            g._qual = False
        evented = {side: {int(e) for e, _, _ in evs} for side, evs in rec.events.items()}
        for side in ("A", "B"):
            self._observe(side, rec)
        judged = {side: self._judged(side, rec, evented.get(side, set())) for side in ("A", "B")}
        for slot in sorted(rec.players):
            p = rec.players[slot]
            self._victim(p, rec, logs[p.team], judged.get(p.team, {}))
        # R7: this tick's locating events join the history only after the victims were judged
        for side, evs in rec.events.items():
            for enemy, te, kind in evs:
                self.located[side][int(enemy)].append((float(te), str(kind)))
        self._entries_prev = {side: {int(e): a.copy() for e, a in enemies.items()}
                              for side, enemies in rec.unknown.items()}
        self._players_prev = dict(rec.players)
        self.smokes_prev = list(rec.smokes)
        self.prev_t = t
        self.timing["steps"] += 1
        self.timing["seconds"] += time.perf_counter() - started

    def _judged(self, side: str, rec, evented: set) -> dict:
        """enemy -> the entry array this tick is judged against: the previous tick's for an enemy with a
        locating event in this record (R7; none before their first tick), else this tick's."""
        out = {}
        for enemy, ent in (rec.unknown.get(side) or {}).items():
            enemy = int(enemy)
            if enemy in evented:
                prev = self._entries_prev.get(side, {}).get(enemy)
                if prev is not None:
                    out[enemy] = prev
            else:
                out[enemy] = ent
        return out

    def _due(self, g: Gap, upto: float, held: bool = False) -> float | None:
        """When an open gap closes, if by `upto`: CLOSE_AFTER_S after its last exposure (unless `held`: the
        last tick's exposure holds to the end of the timeline), or the end of the victim's life."""
        times = [self._life_end(g)]
        if g.t_last_exposed is not None and not held:
            times.append(g.t_last_exposed + CLOSE_AFTER_S)
        c = min(times)
        return c if c <= upto else None

    def _expire(self, t: float) -> None:
        for key, g in list(self.open.items()):
            c = self._due(g, t)
            if c is not None:
                self._close(key, c)

    def _observe(self, side: str, rec) -> None:
        """Which nodes `side` observes this tick, and the nodes it released since the last tick, with the
        observer named for each: the lowest-numbered player observing it on the last tick it was observed,
        by view (their own sight and own node) before utility, a node in both credited to view (R12)."""
        n = self.geo.n
        view_any, util_any, live_any = np.zeros(n, bool), np.zeros(n, bool), np.zeros(n, bool)
        owner_v, owner_u = np.full(n, -1, np.int64), np.full(n, -1, np.int64)
        for s in sorted((s for s, p in rec.players.items() if p.team == side), reverse=True):
            p = rec.players[s]
            v = self._view(p).copy()
            v[p.node] = True
            owner_v[v] = s
            owner_u[p.utility] = s
            view_any |= v
            util_any |= p.utility
            live_any |= p.live
        observed = view_any | util_any | live_any
        prev = self._observed[side]
        if prev is not None:
            released = np.flatnonzero(prev & ~observed)
            if len(released):
                p_owner, p_util = self._owner[side]
                own, util = p_owner[released], p_util[released]
                codes = np.full(len(released), _CODE["other"], np.int8)
                for s, u in sorted(set(zip(own.tolist(), util.tolist()))):
                    m = (own == s) & (util == u)
                    codes[m] = self._reasons(int(s), bool(u), released[m], rec)
                self.rel_t[side][released] = float(rec.t)
                self.rel_player[side][released] = own
                self.rel_util[side][released] = util
                self.rel_reason[side][released] = codes
        self._observed[side] = observed
        self._owner[side] = (np.where(view_any, owner_v, owner_u), ~view_any & util_any)

    def _reasons(self, slot: int, util: bool, nodes: np.ndarray, rec) -> np.ndarray:
        """Why `slot` stopped observing `nodes` at this tick (codes into REASONS)."""
        rnd, t = self.rnd, float(rec.t)
        out = np.full(len(nodes), _CODE["other"], np.int8)
        if slot < 0:
            return out
        if not rnd.alive(slot, t):
            out[:] = _CODE["died"]
            return out
        p = rec.players.get(slot)
        if p is None:                                      # alive, but no position sample: not `died` (R12)
            self._note("release by an observer without a position", slot)
            return out
        if util:
            out[:] = _CODE[self._util_reason(slot, t, nodes)]
            return out
        if any(a <= t < b for a, b in rnd.flashed.get(slot, [])) or \
                any(a <= t < b for a, b in rnd.nearsight.get(slot, [])):
            out[:] = _CODE["blinded"]
            return out
        rest = np.ones(len(nodes), bool)
        blocked = self._smoked(p, rec)
        if blocked is not None:
            rest = ~blocked[nodes]
            out[~rest] = _CODE["smoked"]
        before = self._players_prev.get(slot)
        if before is not None and before.node != p.node:
            out[rest] = _CODE["moved"]
        elif before is not None and _turned(before.yaw, p.yaw):
            out[rest] = _CODE["turned"]
        return out

    def _smoked(self, p, rec) -> np.ndarray | None:
        """Nodes a smoke (or wall) that started since the last tick blocks from p: seen without the new ones
        and not with them (R12). None when nothing new went up."""
        old = {_smoke_key(s) for s in self.smokes_prev}
        new = [s for s in rec.smokes if _smoke_key(s) not in old]
        if not new or self.prev_t is None:
            return None
        key = ("smoked", p.slot)
        if key not in self._tick_cache:
            kept = [s for s in rec.smokes if _smoke_key(s) in old]
            with_all = self._sight(p.x, p.y, p.eye_z, p.node, rec.smokes)
            without = self._sight(p.x, p.y, p.eye_z, p.node, kept)
            self._tick_cache[key] = without & ~with_all
        return self._tick_cache[key]

    def _util_reason(self, slot: int, t: float, nodes: np.ndarray) -> str:
        """`utility_left` / `utility_expired` from a watcher of `slot` that could have covered `nodes`: a fixed
        watcher (trip, alarmbot, trap) only when its `cells` include one of them; a camera, drone or turret
        (no stored cells: its sight is cast per tick) is not filtered."""
        prev_t = self.prev_t
        if prev_t is None:
            return "other"
        mine = [w for w in self.rnd.watchers
                if w.by == slot and (w.cells is None or np.isin(nodes, w.cells).any())]
        for w in mine:
            if w.kind in ("camera", "drone") and w.in_use:
                was = any(a <= prev_t < b for a, b in w.in_use)
                now = any(a <= t < b for a, b in w.in_use)
                if was and not now and w.t0 <= t < w.t1:
                    return "utility_left"
        for w in mine:
            if prev_t < w.t1 <= t:
                return "utility_expired"
        return "other"

    def _victim(self, p, rec, log, judged: dict) -> None:
        t, side = float(rec.t), p.team
        life = self._life(p.slot, t)
        if life < 0:
            self._note("player with a position outside their lives", p.slot)
            return
        prev = self._victim_prev.get(p.slot)
        if prev is not None and prev["t"] != self.prev_t:
            prev = None                                    # absent last tick: no turn or move to compare
        exposed: dict[int, list] = defaultdict(list)       # seq id -> [(enemy, exposed nodes)]
        quals: dict[int, list] = defaultdict(list)         # seq id -> [(enemy, qualifying nodes)]
        front = np.zeros(self.geo.n, bool)                 # exposed unknown nodes not behind the victim (I1)
        sight = None
        if any((ent >= 0).any() for ent in judged.values()):
            sight = self._sight(p.x, p.y, p.eye_z, p.node, rec.smokes)
            cx, cy = self.geo.centres[:, 0], self.geo.centres[:, 1]
            for enemy, ent in sorted(judged.items()):
                nodes = np.flatnonzero(sight & (ent >= 0))
                if not len(nodes):
                    continue
                seqs = log.seq[ent[nodes]]
                bearing = np.degrees(np.arctan2(cy[nodes] - p.y, cx[nodes] - p.x))
                behind = np.abs((bearing - p.yaw + 180) % 360 - 180) > BEHIND_DEG
                front[nodes[~behind]] = True
                wait = self.wait_over(side, enemy, t)
                for sid in np.unique(seqs).tolist():
                    m = seqs == sid
                    exposed[sid].append((enemy, nodes[m]))
                    if wait and (m & behind).any():
                        quals[sid].append((enemy, nodes[m & behind]))
        for sid, found in sorted(quals.items()):
            key = (p.slot, life, sid)
            g = self.open.get(key)
            if g is None:
                g = self._open_gap(p, rec, log, sid, found, life, judged, prev)
                self.open[key] = g
                self.gaps.append(g)
            g._qual = True
            for enemy, _ in found:                         # R9: a candidate joins only by qualifying
                if enemy not in g.candidates:
                    e = rec.players.get(enemy)
                    sx, sy = self.geo.centres[g.spot]
                    g.candidates[enemy] = None if e is None else round(self._metres(e.x, e.y, sx, sy), 2)
                    g.joined[int(enemy)] = t
                    if e is None:
                        self._note("candidate without a position", enemy)
            qualifying = set(np.concatenate([nodes for _, nodes in found]).tolist())
            for enemy in g.candidates:
                e = rec.players.get(enemy)
                if e is not None and e.node in qualifying:
                    g.stood_times[enemy].append(t)
                    if g.stood_at is None:
                        g.stood_at, g.stood_by = t, int(enemy)
        view = self._view(p)
        for (slot, lf, sid), g in self.open.items():
            if slot != p.slot or lf != life:
                continue
            if any(enemy in g.candidates for enemy, _ in exposed.get(sid, ())):   # R9: candidates only
                g.t_last_exposed = t
            covered = bool(view[g.spot])
            if covered and not g._checked:
                g.checked_at.append(t)
            g._checked = covered
        self._victim_prev[p.slot] = {"t": t, "node": p.node, "yaw": p.yaw, "x": p.x, "y": p.y, "eye_z": p.eye_z,
                                     "smokes": list(rec.smokes), "sight": sight, "front": front}

    def _open_gap(self, p, rec, log, sid: int, found: list, life: int, judged: dict, prev) -> Gap:
        side, t = p.team, float(rec.t)
        best = None
        for enemy, nodes in found:       # the earliest arrival; on a tie the lowest cell (spec), then node, enemy
            ent = judged[enemy]
            for node, arrival in zip(nodes.tolist(), log.t[ent[nodes]].tolist()):
                k = (arrival, self._cell(node), node, enemy)
                if best is None or k < best:
                    best = k
        arrival, _, spot, enemy = best
        entry = int(judged[enemy][spot])
        sx, sy = self.geo.centres[spot]
        g = Gap("predicted", int(p.slot), side, int(sid), tuple(int(c) for c in log.seqs[sid]), t, int(spot),
                int(p.node), round(self._metres(p.x, p.y, sx, sy), 2), round(off_facing(p.yaw, p.x, p.y, sx, sy), 1),
                self._route(log, entry), life=life, t_last_exposed=t)
        g.cause, g.cause_detail = self._cause(p, side, sid, enemy, log, entry, float(arrival), t, found, prev)
        view = self._view(p)
        g.context = round_context(self.rnd, t, any(e.team != side and bool(view[e.node]) for e in rec.players.values()))
        return g

    def _cause(self, p, side, sid, enemy, log, entry, arrival, t, found, prev) -> tuple[str, dict]:
        """Section 5's cause, with R11: `open_timing` only when no route node was released since the enemy
        was last located in a way that held the route back; `victim_turned` and `victim_moved` judged
        independently. The event closest in time to the opening wins; on a tie, CAUSE_ORDER.

        A release counts for `route_released` only when the route's own entry at that node arrived at or after
        it ("the route completed because of it"): a route that reached a node before the node was observed
        keeps its old entry there, and a later glance at the node did not hold it back (review fix; the
        fallback to `open_timing` is the controller's ruling D5)."""
        options = []
        since = self.last_located(side, enemy, t)          # before this tick's events (R7)
        trace = log.trace(entry)
        route = log.node[trace]
        rel = self.rel_t[side][route]
        hit = np.isfinite(rel) & (rel >= (since if since is not None else -np.inf)) & (log.t[trace] >= rel)
        if hit.any():
            i = int(np.argmax(np.where(hit, rel, -np.inf)))
            node = int(route[i])
            options.append((float(rel[i]), "route_released", {
                "cell": self._cell(node), "t": float(rel[i]), "player": int(self.rel_player[side][node]),
                "by": "utility" if self.rel_util[side][node] else "view",
                "reason": REASONS[int(self.rel_reason[side][node])]}))
        else:
            ready = arrival if since is None else max(arrival, since + MIN_UNSEEN_S)
            options.append((min(ready, t), "open_timing", {}))
        if prev is not None:
            # turned, per cell (final review I1): a node qualifying now was exposed and in front of the victim at
            # the last tick, and the facing changed. Spread into the rear arc of nodes that were not exposed in
            # front is not a turn, however the aim jitters.
            if any(prev["front"][nodes].any() for _, nodes in found) and _turned(prev["yaw"], p.yaw):
                options.append((t, "victim_turned", {}))
            # moved: a new node, and a qualifying cell that had no line from the old position
            if prev["node"] != p.node:
                old = prev["sight"]
                if old is None:
                    old = self._sight(prev["x"], prev["y"], prev["eye_z"], prev["node"], prev["smokes"])
                if any((~old[nodes]).any() for _, nodes in found):
                    options.append((t, "victim_moved", {}))
        best = max(options, key=lambda o: (o[0], -CAUSE_ORDER.index(o[1])))
        return best[1], best[2]

    def _close(self, key, t_close: float) -> None:
        g = self.open.pop(key)
        t_close = min(float(t_close), self.rnd.t_decided)
        if g._qual and self.prev_t is not None:
            g._qualified(self.prev_t, t_close)
        g._qual = False
        g.t_close = t_close

    def _close_all(self, t: float) -> None:
        for key in list(self.open):
            self._close(key, t)

    # ---------------------------------------------------------------- end

    def finish(self) -> list[Gap]:
        if self._finished is not None:
            return self._finished
        if not self.done:
            # the last tick came before t_decided: its state holds until then, so a gap exposed at the last
            # tick stays open to t_decided; any other closes CLOSE_AFTER_S after its last exposure or at a death
            upto = self.rnd.t_decided
            for key, g in list(self.open.items()):
                held = self.prev_t is not None and g.t_last_exposed == self.prev_t
                c = self._due(g, upto, held)
                if c is not None:
                    self._close(key, c)
            self._close_all(upto)
            self.done = True
        from app.gaps import backshots

        if self.merge:
            self.gaps = merge_stacks(self.gaps, getattr(self.rnd, "t_start", None))
        backshots.add(self)
        self._levels()
        self._finished = sorted(self.gaps, key=lambda g: (g.t_open, g.victim, g.kind))
        return self._finished

    def _levels(self) -> None:
        """`killed` and the victim's own kill (section 5, Use; ruling D6): a candidate killed the victim, or the
        victim killed a candidate, in `shot`'s window: while the gap was open ([t_open, t_close]) or within
        RESULT_WINDOW_S after that candidate stood in it. A shot is not an anchor. The enemy must be on the
        candidate list by the kill. First occurrence only. A kill after t_decided never counts (final review M4:
        nothing is detected after that point)."""
        decided = getattr(self.rnd, "t_decided", math.inf)
        for g in self.gaps:
            if g.kind != "predicted":
                continue
            for kt, killer, victim in sorted(self.rnd.kills):
                if kt > decided:
                    break
                if victim == g.victim and g.killed_at is None and in_use_window(g, killer, kt):
                    g.killed_at, g.killed_by = float(kt), int(killer)
                if killer == g.victim and g.victim_won_at is None and in_use_window(g, victim, kt):
                    g.victim_won_at = float(kt)


def is_open(g: Gap, t: float) -> bool:
    """g was open at t: [t_open, t_close], both ends included (a death closing the gap is at t_close). The one
    definition shared by linking, `shot` and the levels (final review M5)."""
    t_close = g.t_close if g.t_close is not None else math.inf
    return g.t_open <= t <= t_close


def in_use_window(g: Gap, enemy: int, t: float) -> bool:
    """`enemy`, a candidate of g by t, acted at t while g was open (`is_open`) or within RESULT_WINDOW_S after
    they stood in it."""
    joined = g.joined.get(enemy)
    if joined is None or joined > t:
        return False
    return is_open(g, t) or any(s <= t <= s + RESULT_WINDOW_S for s in g.stood_times.get(enemy, ()))


# ---------------------------------------------------------------- stack merge (R1)


def _is_prefix(a: tuple, b: tuple) -> bool:
    """a is a strict prefix of b; the empty sequence is a prefix of every longer one."""
    return len(a) < len(b) and tuple(b[:len(a)]) == tuple(a)


def _overlaps(g: Gap, h: Gap) -> bool:
    """Closed intervals [t_open, t_close] intersect (an unclosed gap runs on)."""
    gc = g.t_close if g.t_close is not None else math.inf
    hc = h.t_close if h.t_close is not None else math.inf
    return g.t_open <= hc and h.t_open <= gc


def _union(spans) -> list:
    out: list = []
    for a, b in sorted((float(a), float(b)) for a, b in spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


# The merge's details (empty sequence folds, overlap test, which member's fields survive).
def merge_stacks(gaps: list[Gap], t_start: float | None = None) -> list[Gap]:
    """R1: a predicted gap whose choke sequence is a strict prefix of another open for the same victim and life
    at an overlapping time folds into its longest such extension (ties: earliest t_open, then the smaller
    sequence, then list order), resolved transitively. Returns `gaps` without the folded members, in order; the
    survivor (the longest member) takes the merged times, qualified time (the union of spans, counted once),
    candidates, joined, stood and `context["merged"]`. Back-shots and gaps with no sequence are untouched."""
    pred = [g for g in gaps if g.kind == "predicted" and g.choke_seq is not None]
    order = {id(g): i for i, g in enumerate(pred)}
    target: dict[int, Gap] = {}
    for g in pred:
        ext = [h for h in pred if h is not g and h.victim == g.victim and h.life == g.life
               and _is_prefix(g.choke_seq, h.choke_seq) and _overlaps(g, h)]
        if ext:
            target[id(g)] = min(ext, key=lambda h: (-len(h.choke_seq), h.t_open, tuple(h.choke_seq), order[id(h)]))
    if not target:
        return list(gaps)

    def root(g: Gap) -> Gap:
        while id(g) in target:            # each step is strictly longer, so this ends
            g = target[id(g)]
        return g

    groups: dict[int, list[Gap]] = defaultdict(list)
    for g in pred:
        if id(g) in target:
            groups[id(root(g))].append(g)
    folded = {id(g) for members in groups.values() for g in members}
    for g in pred:
        if id(g) in groups:
            _fold(g, groups[id(g)], t_start)
    return [g for g in gaps if id(g) not in folded]


def _fold(keep: Gap, folded: list[Gap], t_start: float | None) -> None:
    members = sorted([keep] + folded, key=lambda g: (g.t_open, tuple(g.choke_seq)))
    t_open = members[0].t_open
    shift = t_open - keep.t_open
    exposed = [g.t_last_exposed for g in members if g.t_last_exposed is not None]
    closes = [g.t_close for g in members]
    keep.t_last_exposed = max(exposed) if exposed else None
    keep.t_close = None if any(c is None for c in closes) else max(closes)
    keep.qual_spans = _union(s for g in members for s in g.qual_spans)
    keep.qualified_s = sum(b - a for a, b in keep.qual_spans)
    joined: dict = {}
    candidates: dict = {}
    for g in members:                     # in t_open order: on a tie in joined, the earlier member's distance
        for enemy, dist in g.candidates.items():
            tj = g.joined.get(enemy, g.t_open)
            if enemy not in joined or tj < joined[enemy]:
                joined[enemy], candidates[enemy] = tj, dist
    keep.joined = {e: joined[e] for e in sorted(joined)}
    keep.candidates = {e: candidates[e] for e in sorted(candidates)}
    stood: dict = defaultdict(list)
    for g in members:
        for enemy, ts in g.stood_times.items():
            stood[enemy].extend(ts)
    keep.stood_times = defaultdict(list, {e: sorted(set(ts)) for e, ts in sorted(stood.items())})
    first = min(((g.stood_at, g.stood_by) for g in members if g.stood_at is not None), default=None)
    keep.stood_at, keep.stood_by = first if first is not None else (None, None)
    # the context describes the merged t_open: the earliest member's (alive counts, spike, what the victim saw);
    # spot, route, cause, distance and angle stay the longest member's, opened at `route_opened`
    context = dict(members[0].context)
    context.pop("merged", None)
    context["route_opened"] = keep.t_open
    if t_start is not None:
        context["t_round"] = round(t_open - t_start, 3)
    elif "t_round" in context:
        context["t_round"] = round(context["t_round"] + shift, 3)
    context["merged"] = [{"choke_seq": [int(c) for c in g.choke_seq], "t_open": g.t_open}
                         for g in members if g is not keep]
    keep.context = context
    keep.t_open = t_open
