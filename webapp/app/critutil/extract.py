"""Critical Util, one round: every cast with the evidence of what followed it (design:
docs/superpowers/specs/2026-10-21-critical-util-design.md).

Runs on the replay worker beside Critical Moments (app/control/task.py, the gap run). Pure: imports only
app.control, app.replays.player_state and app.critutil.constants. Slots only, never names. What a cast is worth is
not decided here: the site turns this evidence into values with the corpus's estimator (app/services/util_values.py).

Output: {"v", "sides", "contact", "plant", "defuse", "t_decided", "kills", "casts", "heals", "unused", "ults",
"avail"}.
"""

from __future__ import annotations

import functools
import math

from app.control import engine
from app.critutil import abilities
from app.critutil.constants import (AGENT_KEYS, BUY_PLACED_S, DEVICE_PATTERNS, SAME_PLACE_M, ENGAGE_LOOKBACK_S, FITS_ANY_FIGHT, FITS_SPIKE,
                                    FLASH_ROW_MATCH_S, FOLLOW_S, GRACE_S, HEAL_FOLLOW_S, HOLD_MATCH_S, MOBILITY_KEYS,
                                    MERGE_S, NEAR_TRIGGER_M, PRESENCE_KINDS, REPEAT_PARTS, REPEAT_S, PRESENCE_STEP_S, SAVE_WINDOW_S,
                                    SIGHT_RANGE_M, SIGHT_STEP_S, SPIKE_NEAR_M, STOPPED_MPS, THROW_MATCH_S, TRIGGER_WINDOW_S, TURN_AWAY_M,
                                    VERSION, WAIT_RING_M, WALL_PATTERNS, ZONE_DAMAGE_LOOKBACK_S, ZONE_RADIUS_M)
from app.moments.constants import ABILITY_KIND

_EPS = 0.01


def _r2(x):
    return None if x is None else round(float(x), 2)


def _px(u, v):
    return u * engine.PX / 10000, v * engine.PX / 10000


def _key_of(name: str | None) -> str | None:
    """The archetype letter of an ability name ("Q_Molotov" -> "Q", "4_Smoke" -> "4"). Not the keybind."""
    if not name or "_" not in name:
        return None
    return name.split("_", 1)[0]


@functools.lru_cache(maxsize=None)
def _entry_for(code_name: str):
    return abilities.lookup(code_name)


def _entry_of(row) -> dict | None:
    """The ability table's entry for a row (matched on `<code>_<name>`), None when the row has no code or fits none."""
    if not row.get("code") or not row.get("name"):
        return None
    return _entry_for(f"{row['code']}_{row['name']}")


def _kinds_of(names: list[str], rows=()) -> set[str]:
    """A cast's kinds: each row the table matches gives that entry's `kinds`; the rest (and every name when no row is
    given) go through today's patterns."""
    kinds = set()
    rest = list(names)
    if rows:
        rest = []
        for r in rows:
            e = _entry_of(r)
            if e is not None:
                kinds.update(e.get("kinds") or ())
            elif r.get("name") or r.get("ability"):
                rest.append(r.get("name") or r.get("ability"))
        names = rest
    kinds |= _pattern_kinds(names)
    if "wall" in kinds and "smoke" in kinds:
        kinds.add("stall")
    return kinds


def _pattern_kinds(names: list[str]) -> set[str]:
    kinds = set()
    for n in names:
        k = ABILITY_KIND.get(n)
        if k:
            kinds.add(k)
        if any(p in n for p in DEVICE_PATTERNS):
            kinds.add("device")
        if any(p in n for p in WALL_PATTERNS):
            kinds.add("wall")
    if "wall" in kinds and "smoke" in kinds:
        kinds.add("stall")          # a smoke wall both blocks sight and splits a site
    return kinds


class _Cast:
    __slots__ = ("i", "by", "agent", "t", "t_end", "u", "v", "rows", "names", "ids", "kinds", "flash", "key",
                 "landed_t", "landed", "end_unknown", "entry")

    def __init__(self, i, row, rnd):
        self.i, self.by, self.agent = i, row.get("by"), row.get("agent")
        self.t = _throw_t(row)              # when it was thrown: a projectile's spawn, a placed piece's `thrown.t0`
        self.t_end, self.end_unknown = _end(row, rnd)
        self.entry = None                   # the table entry of the first of its rows (time order) that has one
        self.u = self.v = None
        self.rows, self.names, self.ids = [], [], set()
        self.flash, self.key = None, _key_of(row.get("name"))
        self.landed_t, self.landed = None, {}   # first landed piece's time; each landed name's first time
        self.add(row, rnd, first=True)

    def add(self, row, rnd, first=False):
        self.rows.append(row)
        end, unknown = _end(row, rnd)
        if not first:               # the row that ends last decides whether the cast's end is a guess
            if end > self.t_end:
                self.t_end, self.end_unknown = end, unknown
            elif end == self.t_end:
                self.end_unknown = self.end_unknown or unknown
        if self.entry is None:
            self.entry = _entry_of(row)
        if self.agent is None:
            self.agent = row.get("agent")
        name = row.get("name") or row.get("ability")
        if name:
            self.names.append(name)
        if row.get("id") is not None:
            self.ids.add(row["id"])
        landed = row.get("k") != "ability" or row.get("kind") != "Projectile"
        if row.get("u") is not None and (self.u is None or (landed and self.landed_t is None)):
            self.u, self.v = row["u"], row["v"]     # where it landed, not where the projectile left the hand
        if landed and row.get("k") == "ability":
            if self.landed_t is None:
                self.landed_t = float(row["t"])
            if name:
                self.landed.setdefault(name, float(row["t"]))


def _throw_t(row) -> float:
    thrown = row.get("thrown") or {}
    return float(thrown["t0"]) if thrown.get("t0") is not None else float(row["t"])


def _end(row, rnd) -> tuple[float, bool]:
    """(when the row ends, whether that is a guess). A row with a close ends there; one without ends at `t` plus the
    table's duration, a placeable at t_decided; with neither it ends at t_decided and is flagged `end_unknown`."""
    t = float(row["t"])
    ends = [x for x in (row.get("t1"), row.get("gone")) if x is not None]
    unknown = False
    if ends:
        end = min(ends)
    else:
        e = _entry_of(row)
        if e is not None and e.get("type") == "placeable":
            end = rnd.t_decided
        elif e is not None and e.get("duration_s") is not None:
            end = t + float(e["duration_s"])
        else:
            end, unknown = rnd.t_decided, True
    return min(max(float(end), t), rnd.t_end), unknown


def _dist_m(rnd, a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1]) * rnd.geo.m_per_px


def _another_use(rnd, c: _Cast, row) -> bool:
    """A landed piece this cast already has, landing again more than REPEAT_S later, is another use of the
    ability (a second smoke), unless the piece is one that repeats within one use (a recon's pings). In the buy
    phase every placement starts at the round's 0 s, so there the same piece at another place is another charge
    (a Cypher's two trips)."""
    name = row.get("name")
    if not name or name not in c.landed or any(p in name for p in REPEAT_PARTS):
        return False
    if float(row["t"]) - c.landed[name] > REPEAT_S:
        return True
    if float(row["t"]) > BUY_PLACED_S or row.get("u") is None or (row.get("thrown") or {}).get("t0") is not None:
        return False
    here = _px(row["u"], row["v"])
    return all(_dist_m(rnd, here, _px(x["u"], x["v"])) > SAME_PLACE_M
               for x in c.rows if x.get("name") == name and x.get("u") is not None)


def _anchored(casts, row):
    """The cast a piece is proven part of: its owner's cast with a row whose `end` is where this piece is (a
    tripwire's second anchor sits on its wire's `end`). None otherwise."""
    if row.get("u") is None:
        return None
    for c in reversed(casts):
        if c.by == row.get("by") and c.flash is None and any(
                isinstance(x.get("end"), list) and len(x["end"]) >= 2
                and abs(x["end"][0] - row["u"]) <= 2 and abs(x["end"][1] - row["v"]) <= 2 for x in c.rows):
            return c
    return None


def build_casts(rnd, blob: dict) -> list[_Cast]:
    """One _Cast per use of a charge. A flash or nearsight row, or a projectile, starts one (a thrown ability); the
    pieces it lands as join it: a piece whose `thrown.t0` is its throw, or one that starts while it is still up
    (a recon's pings, a smoke's zone, a drone's darts). A placed ability with no projectile starts its own. The
    spike is not a cast."""

    util = blob.get("util") or ()
    casts: list[_Cast] = []
    for r in sorted((r for r in util if r.get("k") in ("flash", "nearsight")), key=lambda r: r["t"]):
        c = _Cast(len(casts), r, rnd)
        c.flash = r
        c.kinds = {"blind"} if r["k"] == "flash" else {"status"}
        casts.append(c)
    abilities = sorted((r for r in util if r.get("k") == "ability" and r.get("kind") != "Bomb"),
                       key=lambda r: (r["t"], r.get("kind") != "Projectile", "end" not in r))
    for r in abilities:
        name, by, tt = r.get("name"), r.get("by"), _throw_t(r)
        kinds = _kinds_of([name] if name else [])
        twin = next((c for c in reversed(casts) if c.flash is not None and c.by == by
                     and abs(c.t - tt) <= FLASH_ROW_MATCH_S), None)
        if twin is not None and (not kinds or kinds <= {"blind", "status"}):
            twin.add(r, rnd)                # a flash's own projectile and source rows
            continue
        host = _anchored(casts, r) if r.get("kind") != "Projectile" else None
        if host is None and r.get("kind") != "Projectile":
            for c in reversed(casts):
                if c.flash is not None or c.by != by or c.key != _key_of(name):
                    continue
                if (r.get("thrown") or {}).get("t0") is not None:
                    if abs(c.t - tt) <= THROW_MATCH_S:
                        host = c
                elif c.t - _EPS <= r["t"] <= c.t_end + MERGE_S and not _another_use(rnd, c, r):
                    host = c
                break                         # only the latest use of this ability can own the piece
        if host is not None:
            host.add(r, rnd)
            continue
        c = _Cast(len(casts), r, rnd)
        c.kinds = set()
        casts.append(c)
    for c in casts:
        c.kinds = set(c.kinds) | _kinds_of(c.names, c.rows)
        if any(_key_of(n) == "X" for n in c.names):
            c.kinds.add("ult")
        if any(f"{x.get('code')}_{x.get('name')}" in MOBILITY_KEYS for x in c.rows):
            c.kinds.add("mobility")
        if not c.kinds:
            c.kinds.add("other")
    casts.sort(key=lambda c: (c.t, c.i))
    for i, c in enumerate(casts):
        c.i = i
    return casts


# --- round context


def _sides(rnd) -> dict[int, str]:
    """slot -> "attack" | "defense" from the control link; a round without one (tests, an unlinked replay) keeps
    the team letter, so who is on whose side still holds and only the alive counts are unknown."""
    return {s: rnd.group_side.get(g) or g for s, g in rnd.team.items()}


def _alive_counts(rnd, sides, t) -> dict[str, int]:
    out = {"attack": 0, "defense": 0}
    for s, side in sides.items():
        if side in out and rnd.alive(s, t):
            out[side] += 1
    return out


def _first_contact(blob, rnd) -> float | None:
    ts = [float(r["t"]) for r in blob.get("util") or () if r.get("k") == "damage"
          and r.get("by") is not None and r.get("target") is not None
          and rnd.team.get(r["by"]) is not None and rnd.team.get(r["by"]) != rnd.team.get(r.get("target"))]
    ts += [k[0] for k in rnd.kills]
    return min(ts) if ts else None


def _spike(blob) -> dict | None:
    for r in blob.get("util") or ():
        if r.get("k") == "ability" and r.get("kind") == "Bomb":
            return {"t": float(r["t"]), "u": r.get("u"), "v": r.get("v"), "by": r.get("by"),
                    "defuses": r.get("defuses") or []}
    return None


def _phase(t, contact, plant) -> str:
    if plant is not None and t >= plant:
        return "post_plant"
    if contact is None or t < contact:
        return "pre_contact"
    return "contact"


# --- evidence


def _effects(rnd, blob, casts: list[_Cast]) -> dict[int, list[dict]]:
    """Each cast's effects on players: (target, t0, t1, how) from its flash hits, and the reveal, status and
    ability-damage rows it caused. A row names its cast by `id` (revision 21) or, failing that, by caster and
    ability name inside the cast's life; with neither it is left unattributed."""
    out = {c.i: [] for c in casts}
    by_id = {i: c for c in casts for i in c.ids}
    for c in casts:
        if c.flash is None:
            continue
        for hit in c.flash.get("hits") or ():
            target, t_hit = hit[0], float(hit[1])
            dur = hit[2] if len(hit) > 2 else None
            if dur is not None and dur <= 0:
                continue
            out[c.i].append({"target": target, "t0": t_hit, "t1": t_hit + (dur if dur is not None else 1.5),
                             "how": "blind" if c.flash["k"] == "flash" else "nearsight", "dur_known": dur is not None})
    for r in blob.get("util") or ():
        k = r.get("k")
        if k not in ("reveal", "status", "damage") or r.get("target") is None:
            continue
        if k == "damage" and r.get("src") != "ability" and not r.get("code"):
            continue             # a gun hit
        cast = by_id.get(r.get("id")) if r.get("id") is not None else None
        if cast is None:
            name = r.get("name")
            t = float(r["t"])
            cands = [c for c in casts if c.by == r.get("by") and c.t - _EPS <= t <= c.t_end + 1.0
                     and (not name or name in c.names or not c.names)]
            if k == "damage" and not cands:
                cands = [c for c in casts if c.by == r.get("by") and c.t - _EPS <= t <= c.t_end + 1.0
                         and "damaging" in c.kinds]
            if not cands and r.get("code"):
                # an effect named differently from its object (a dart's reveal): the caster's live cast of that agent
                cands = [c for c in casts if c.by == r.get("by") and c.t - _EPS <= t <= c.t_end + 1.0
                         and r["code"] in {x.get("code") for x in c.rows}]
            if len({c.i for c in cands}) != 1:
                cands = [c for c in cands if name and name in c.names] or cands
            if not cands:
                continue
            cast = max(cands, key=lambda c: c.t)
        t0 = float(r["t"])
        t1 = float(r["t1"]) if r.get("t1") is not None else t0
        how = {"reveal": "reveal", "damage": "damage"}.get(k) or r.get("status") or "status"
        out[cast.i].append({"target": r["target"], "t0": t0, "t1": t1, "how": how, "dur_known": True})
    return out


def _damage_by_zone_before(effects, target, t) -> bool:
    return any(e["target"] == target and e["how"] == "damage" and t - ZONE_DAMAGE_LOOKBACK_S <= e["t1"] + _EPS
               and e["t0"] <= t for e in effects)


def _trigger(c: _Cast) -> float | None:
    for r in c.rows:
        fx = r.get("fx")
        if isinstance(fx, (int, float)):
            return float(fx)
        if isinstance(fx, list) and fx:
            first = fx[0]
            return float(first[0] if isinstance(first, list) else first)
        if isinstance(r.get("raised"), list) and r["raised"]:
            return float(r["raised"][0])
        if r.get("gone") is not None:
            return float(r["gone"])
    return None


def _link_kills(rnd, sides, c: _Cast, effects, trigger) -> list[dict]:
    """The round's kills this cast has evidence on: an enemy it affected killed by the caster's team (or a
    teammate killed by an enemy it affected: the follow-up failed), a victim its zone damaged, a kill near a
    device soon after it fired. Kills up to t_decided only."""
    side = sides.get(c.by)
    out = []
    if side is None:
        return out
    for t, killer, victim in rnd.kills:
        if t > rnd.t_decided:
            continue
        k_side, v_side = sides.get(killer), sides.get(victim)
        if k_side is None or v_side is None or k_side == v_side:
            continue
        ours = k_side == side
        ev = None
        on_victim = [e for e in effects if e["target"] == victim and e["t0"] - _EPS <= t <= e["t1"] + GRACE_S]
        on_killer = [e for e in effects if e["target"] == killer and e["t0"] - _EPS <= t <= e["t1"] + GRACE_S]
        if ours and any(e["how"] != "damage" and e["how"] != "reveal" for e in on_victim):
            ev = "affected"
        elif ours and any(e["how"] == "reveal" for e in on_victim):
            ev = "revealed"
        elif ours and _damage_by_zone_before(effects, victim, t):
            ev = "zone_damage"
        elif not ours and sides.get(killer) != side and on_killer \
                and any(e["how"] not in ("damage", "reveal") for e in on_killer):
            ev = "failed_follow"      # an enemy we affected still killed one of us inside the effect
        elif ours and trigger is not None and "device" in c.kinds and 0 <= t - trigger <= TRIGGER_WINDOW_S \
                and c.u is not None:
            p = rnd.pos(victim, t - _EPS)
            if p is not None and _dist_m(rnd, p, _px(c.u, c.v)) <= NEAR_TRIGGER_M:
                ev = "trigger"
        if ev is not None:
            out.append({"t": _r2(t), "killer": killer, "victim": victim, "ours": ours, "ev": ev})
    return out


def _followed(rnd, blob, sides, c: _Cast, effects) -> bool | None:
    """Did the caster's team damage an enemy this cast affected within FOLLOW_S of the effect starting? None
    when it affected no enemy."""
    side = sides.get(c.by)
    starts = {}
    for e in effects:
        if sides.get(e["target"]) not in (None, side) and e["how"] not in ("damage",):
            starts.setdefault(e["target"], e["t0"])
    if not starts:
        return None
    for r in blob.get("util") or ():
        if r.get("k") != "damage" or sides.get(r.get("by")) != side or r.get("target") not in starts:
            continue
        if 0 <= float(r["t"]) - starts[r["target"]] <= FOLLOW_S + GRACE_S:
            return True
    return any(sides.get(killer) == side and victim in starts and 0 <= t - starts[victim] <= FOLLOW_S + GRACE_S
               for t, killer, victim in rnd.kills)


def _harm(rnd, sides, c: _Cast, effects) -> list[dict]:
    """Teammates this cast blinded, nearsighted or damaged, with what happened to them: died while affected
    (to an enemy), in a fight (took or dealt enemy damage) while affected, or neither."""
    side = sides.get(c.by)
    out = []
    for e in effects:
        mate = e["target"]
        if mate == c.by or sides.get(mate) != side:
            continue
        death = next((t for t, killer, victim in rnd.kills
                      if victim == mate and e["t0"] <= t <= e["t1"] + GRACE_S and sides.get(killer) not in (None, side)
                      and rnd.life_end(mate, e["t0"]) >= t - _EPS), None)
        # `died_t` is that death: the ledger charges this harm with that kill only, never a later life's
        out.append({"mate": mate, "how": e["how"], "t": _r2(e["t0"]), "dur": _r2(e["t1"] - e["t0"]),
                    "died": death is not None, "died_t": _r2(death)})
    return out


def _shapes_of(rnd, sides, c: _Cast):
    """A smoke or wall cast's own shapes in the engine: [(t0, t1, shape)], or None for any other cast."""
    if not ({"smoke", "wall"} & c.kinds) or sides.get(c.by) is None:
        return None
    landed = c.landed_t if c.landed_t is not None else c.t     # the zone's time, not the throw's
    spots = [_px(r["u"], r["v"]) for r in c.rows if r.get("u") is not None and r.get("kind") != "Projectile"]
    mine = [(t0, t1, (x, y, r, solid)) for t0, t1, x, y, r, solid in rnd.smokes
            if c.t - _EPS <= t0 <= c.t_end and any(math.hypot(x - sx, y - sy) < 1.0 for sx, sy in spots)]
    walls = [(t0, t1, w) for t0, t1, w in rnd.walls if abs(t0 - landed) <= 1.0]
    return (mine + walls) or None


def _grid(t0: float, t1: float, step: float):
    """The round's shared sample times in [t0, t1): multiples of `step`, so every cast samples the same instants."""
    t = math.ceil((t0 - _EPS) / step) * step
    while t < t1:
        yield round(t, 4)
        t += step


def _cover_all(rnd, sides, casts: list[_Cast]) -> dict[int, dict]:
    """For each smoke or wall: the sight lines between living enemies and the caster's team that it cut (one its
    own shapes block and bare geometry doesn't), sampled every SIGHT_STEP_S over its life to t_decided. A line two
    casts each cut at the same instant is split between them, never counted twice; each covered teammate who lived
    is one save, given to the cast that covered them longest (spec, "No double counting")."""
    shapes = {c.i: s for c in casts if (s := _shapes_of(rnd, sides, c))}
    by_i = {c.i: c for c in casts}
    acc = {i: {"blocked_s": 0.0, "pairs": set(), "covered": {}, "share": {}} for i in shapes}
    if not shapes:
        return {}
    start = min(by_i[i].t for i in shapes)
    end = max(min(by_i[i].t_end, rnd.t_decided) for i in shapes)
    for t in _grid(start, end, SIGHT_STEP_S):
        live = {}
        for i, s in shapes.items():
            c = by_i[i]
            if not c.t <= t < min(c.t_end, rnd.t_decided):
                continue
            now = [shape for t0, t1, shape in s if t0 <= t < t1]
            if now:
                live[i] = now
        if not live:
            continue
        for a, sa in sides.items():
            if sa is None or not rnd.alive(a, t):
                continue
            pa = rnd.pos(a, t)
            if pa is None:
                continue
            for b, sb in sides.items():
                if sb in (None, sa) or not rnd.alive(b, t):
                    continue
                mine = [i for i in live if sides.get(by_i[i].by) == sa]
                if not mine:
                    continue
                pb = rnd.pos(b, t)
                if pb is None or _dist_m(rnd, pa, pb) > SIGHT_RANGE_M:
                    continue
                la, lb = (pa[0], pa[1], None), (pb[0], pb[1], None)
                if not engine.los(rnd.geo, la, lb, []):
                    continue
                cut = [i for i in mine if not engine.los(rnd.geo, la, lb, live[i])]
                for i in cut:
                    share = SIGHT_STEP_S / len(cut)
                    acc[i]["blocked_s"] += share
                    acc[i]["pairs"].add((a, b))
                    acc[i]["covered"][a] = t
                    acc[i]["share"][a] = acc[i]["share"].get(a, 0.0) + share
    saves: dict[int, int] = {}       # teammate -> the one cast their save goes to
    for i in sorted(acc, key=lambda i: by_i[i].t):
        for a, s in acc[i]["share"].items():
            if a not in saves or s > acc[saves[a]]["share"][a] + _EPS:
                saves[a] = i
    out = {}
    for i, x in acc.items():
        lived = [m for m, last in x["covered"].items()
                 if saves.get(m) == i and rnd.alive(m, min(rnd.t_decided, last + SAVE_WINDOW_S))]
        out[i] = {"blocked_s": _r2(x["blocked_s"]), "pairs": len(x["pairs"]), "covered": sorted(x["covered"]),
                  "covered_lived": sorted(lived)}
    return out


def _presence(rnd, sides, c: _Cast) -> dict | None:
    """Observed enemy presence around a placed area cast (smoke, molly, wall, device) while it was up, to
    t_decided: enemy-seconds inside it, enemy-seconds stopped just outside it (waiting on it), and enemies who
    came to its edge and then turned away without entering (held back). None for any other cast."""
    if c.u is None or not (c.kinds & PRESENCE_KINDS):
        return None
    side = sides.get(c.by)
    kind = next((k for k in ("device", "smoke", "damaging", "status") if k in c.kinds), "default")
    r = ZONE_RADIUS_M[kind]
    x, y = _px(c.u, c.v)
    end = min(c.t_end, rnd.t_decided)
    inside_s = 0.0
    entered, turned, waits = set(), set(), set()
    prev, closest = {}, {}
    for t in _grid(c.t, end, PRESENCE_STEP_S):
        for s, sd in sides.items():
            if sd in (None, side) or not rnd.alive(s, t):
                continue
            p = rnd.pos(s, t)
            if p is None:
                continue
            d = _dist_m(rnd, p, (x, y))
            if d <= r:
                inside_s += PRESENCE_STEP_S
                entered.add(s)
            elif d <= r + WAIT_RING_M:
                q = prev.get(s)
                if q is not None and _dist_m(rnd, p, q) / PRESENCE_STEP_S < STOPPED_MPS:
                    waits.add((t, s))
            if d <= r + WAIT_RING_M:
                closest[s] = min(closest.get(s, d), d)
            elif s in closest and d - closest[s] >= TURN_AWAY_M:
                turned.add(s)           # came to the edge, then went back the way it came
            prev[s] = p
    # `_waits` and `_gap` are for sharing between casts (`_share_presence`), never stored
    return {"r_m": r, "armed_s": _r2(max(0.0, end - c.t)), "inside_s": _r2(inside_s),
            "waiting_s": _r2(len(waits) * PRESENCE_STEP_S), "entered": sorted(entered),
            "held_back": sorted(turned - entered), "_waits": waits,
            "_gap": {s: closest[s] - r for s in turned - entered}}


def _share_presence(presence: dict[int, dict | None]) -> None:
    """An enemy waiting at two casts' edges at once is one enemy-second, split between them; an enemy held back by
    two casts is held back once, by the cast whose edge they came closest to (spec, "No double counting")."""
    claims: dict[tuple, int] = {}
    for p in presence.values():
        for w in (p or {}).get("_waits", ()):
            claims[w] = claims.get(w, 0) + 1
    owner: dict[int, tuple[float, int]] = {}
    for i, p in sorted(presence.items()):
        for s, gap in (p or {}).get("_gap", {}).items():
            if s not in owner or gap < owner[s][0] - _EPS:
                owner[s] = (gap, i)
    for i, p in presence.items():
        if p is None:
            continue
        p["waiting_s"] = _r2(sum(PRESENCE_STEP_S / claims[w] for w in p.pop("_waits")))
        p["held_back"] = sorted(s for s in p.pop("_gap") if owner[s][1] == i)


def _spike_effect(rnd, sides, c: _Cast, spike, effects) -> dict | None:
    """Defuse attempts that overlapped this cast, near the spike, and whether each was stopped (not finished).
    An attempt can be this cast's to stop only when the defuser is the caster's enemy and the cast reached them
    during it (`evidence`): an effect of this cast on the defuser ("effect"), or a damaging zone burning on the
    spike ("zone"). `stopped` is set by `_share_defuses`: each stopped attempt goes to one cast."""
    if spike is None or spike.get("u") is None or c.u is None:
        return None
    gap = _dist_m(rnd, _px(c.u, c.v), _px(spike["u"], spike["v"]))
    if gap > SPIKE_NEAR_M:
        return None
    side = sides.get(c.by)
    attempts = []
    for a in spike.get("defuses") or ():
        if not isinstance(a, list) or len(a) < 3:
            continue
        t0 = float(a[0])
        t1 = float(a[1]) if a[1] is not None else t0
        if t1 < c.t or t0 > c.t_end:
            continue
        finished = bool(a[3]) if len(a) > 3 else None
        evidence = None
        if side is not None and sides.get(a[2]) not in (None, side):
            if any(e["target"] == a[2] and e["how"] != "reveal" and e["t0"] - _EPS <= t1 and e["t1"] + _EPS >= t0
                   for e in effects):
                evidence = "effect"
            elif "damaging" in c.kinds and gap <= ZONE_RADIUS_M["damaging"] and c.t <= t1 <= c.t_end:
                evidence = "zone"
        attempts.append({"t": _r2(t0), "t1": _r2(t1), "by": a[2], "finished": finished, "evidence": evidence})
    return {"near_spike": True, "attempts": attempts, "stopped": 0}


def _share_defuses(spikes: dict[int, dict | None], order: dict[int, float]) -> None:
    """Each defuse attempt that didn't finish is stopped once: by the cast with evidence on it, an effect on the
    defuser before a zone on the spike, the latest such cast on a tie."""
    best: dict[tuple, tuple] = {}
    for i, sp in spikes.items():
        for a in (sp or {}).get("attempts", ()):
            if a["finished"] is not False or a["evidence"] is None:
                continue
            key = (a["t"], a["by"])
            rank = (a["evidence"] == "effect", order[i])
            if key not in best or rank > best[key][0]:
                best[key] = (rank, i)
    for i, sp in spikes.items():
        if sp is None:
            continue
        for a in sp["attempts"]:
            a["stopped_by_this"] = best.get((a["t"], a["by"]), (None, None))[1] == i
        sp["stopped"] = sum(1 for a in sp["attempts"] if a["stopped_by_this"])


# --- heals and unused util


def _vitals_before(ps, slot, t):
    from app.replays.player_state import vitals_at

    return vitals_at(ps, slot, t - _EPS)


def _heals(rnd, blob, sides) -> list[dict]:
    ps = blob.get("player_state")
    rows = [r for r in blob.get("util") or () if r.get("k") == "heal" and r.get("target") is not None]
    out = []
    for r in rows:
        target, t0 = r["target"], float(r["t"])
        t1 = float(r.get("t1") if r.get("t1") is not None else t0)
        before = _vitals_before(ps, target, t0)
        hp_before = None if before is None else before.get("hp")
        life_end = rnd.life_end(target, t1)
        horizon = min(t1 + HEAL_FOLLOW_S, rnd.t_decided)
        dmg =[d for d in blob.get("util") or () if d.get("k") == "damage" and d.get("target") == target
               and t1 <= float(d["t"]) <= horizon and sides.get(d.get("by")) not in (None, sides.get(target))]
        fought = bool(dmg) or any(r2.get("k") == "damage" and r2.get("by") == target
                                  and t1 <= float(r2["t"]) <= horizon for r2 in blob.get("util") or ())
        taken = _damage_taken(ps, target, t1, horizon)
        survived = rnd.alive(target, horizon - _EPS) or life_end >= horizon
        share = None
        if hp_before is not None and taken is not None and r.get("amount") is not None:
            share = _saved_share(ps, r, rows, horizon) if r.get("pool") in ("hp", "armor") else 0.0
        out.append({"t": _r2(t0), "t1": _r2(t1), "by": r.get("by"), "target": target, "pool": r.get("pool"),
                    "amount": _r2(r.get("amount")), "name": r.get("name"), "code": r.get("code"),
                    "hp_before": _r2(hp_before), "taken_after": _r2(taken), "fought": fought,
                    "survived": bool(survived), "saved": None if share is None else share > 0,
                    "saved_share": None if share is None else round(share, 3)})
    return out


def _heal_end(r) -> float:
    return float(r.get("t1") if r.get("t1") is not None else r["t"])


def _saved_share(ps, heal, heals, horizon) -> float | None:
    """This heal's share of a life saved, 0..1 (spec, "Healing"). Within the life the heal ended in, take the
    lowest health (HP + shield) the player was seen at, still alive, between the heal and `horizon`: every other
    heal is in it already. Without this heal they'd have had that low point minus its amount; at or below zero,
    the heal kept them alive. Each heal of that life ending before the low point (no more than HEAL_FOLLOW_S
    before it) that was needed the same way shares the save by amount; one the others alone covered gets nothing
    (20 HP, healed to 80, then to 100, then 25 damage: neither heal was needed). None without vitals."""
    events = ((ps or {}).get("vitals") or {}).get(str(heal["target"])) or []
    t1 = _heal_end(heal)
    at = next((e for e in reversed(events) if e["t"] <= t1 + _EPS), None)
    if at is None:
        return None
    life = at.get("life")
    low = None
    for e in events:
        if e["t"] < t1 - _EPS or e.get("life") != life:
            continue
        if e["t"] > horizon:
            break
        health = (e.get("hp") or 0) + (e.get("sh") or 0)
        if (e.get("hp") or 0) > 0 and (low is None or health < low[0]):
            low = (health, e["t"])
    if low is None:
        return 0.0
    health, t_low = low
    mates = [h for h in heals if h.get("target") == heal["target"] and h.get("amount") is not None
             and h.get("pool") in ("hp", "armor") and t_low - HEAL_FOLLOW_S <= _heal_end(h) <= t_low + _EPS
             and _life_of(events, _heal_end(h)) == life]
    needed = [h for h in mates if health - float(h["amount"]) <= 0]
    if heal not in needed:
        return 0.0
    return float(heal["amount"]) / sum(float(h["amount"]) for h in needed)


def _life_of(events, t):
    at = next((e for e in reversed(events) if e["t"] <= t + _EPS), None)
    return None if at is None else at.get("life")


def _damage_taken(ps, slot, t0, t1):
    """Health lost by `slot` between t0 and t1 from its vitals (hp + shield drops), or None without vitals."""
    events = ((ps or {}).get("vitals") or {}).get(str(slot))
    if not events:
        return None
    lost, prev = 0.0, None
    for e in events:
        if e["t"] < t0:
            prev = e
            continue
        if e["t"] > t1:
            break
        if prev is not None and e.get("life") == prev.get("life"):
            drop = (prev.get("hp") or 0) + (prev.get("sh") or 0) - (e.get("hp") or 0) - (e.get("sh") or 0)
            if drop > 0:
                lost += drop
        prev = e
    return lost


def _held(ps, slot, t) -> dict[str, int]:
    out = {}
    for key, line in (((ps or {}).get("charges") or {}).get(str(slot)) or {}).items():
        n = None
        for p in line:
            if p["t"] <= t:
                n = p["n"]
            else:
                break
        if n:
            out[key] = n
    return out


def _ult_cap(ps, slot) -> int | None:
    """The points this slot's ult needed: the most it held just before any drop to 0 in the round."""
    line = ((ps or {}).get("ult") or {}).get(str(slot)) or []
    caps = [a["n"] for a, b in zip(line, line[1:]) if a["n"] and b["n"] == 0]
    return max(caps) if caps else None


def _ult_at(ps, slot, t):
    n = None
    for p in ((ps or {}).get("ult") or {}).get(str(slot)) or []:
        if p["t"] <= t:
            n = p["n"]
    return n


def _unused(rnd, blob, sides, spike, agents) -> list[dict]:
    """Deaths holding util that fits the fight, in fights the player engaged first. Without charge timelines
    (before revision 21) no claim is made. Full ults never used are the site's (`_ults`)."""
    ps = blob.get("player_state")
    have_charges = bool((ps or {}).get("charges"))
    out = []
    util = blob.get("util") or ()
    for t, killer, victim in rnd.kills:
        if t > rnd.t_decided or sides.get(killer) in (None, sides.get(victim)):
            continue
        incoming = [float(r["t"]) for r in util if r.get("k") == "damage" and r.get("target") == victim
                    and sides.get(r.get("by")) not in (None, sides.get(victim)) and t - ENGAGE_LOOKBACK_S <= r["t"] <= t]
        first_in = min(incoming) if incoming else t
        engaged = any((r.get("k") == "damage" and r.get("by") == victim and sides.get(r.get("target")) not in
                       (None, sides.get(victim))) or (r.get("k") == "shot" and r.get("by") == victim)
                      for r in util if first_in - ENGAGE_LOOKBACK_S <= r["t"] < first_in)
        if not engaged or not have_charges:
            continue
        held = _held(ps, victim, t - _EPS)
        abilities = AGENT_KEYS.get(agents.get(victim) or "", {})
        planted = spike is not None and spike["t"] <= t
        fits = [abilities[k] for k in sorted(held) if k in abilities
                and (abilities[k][1] in FITS_ANY_FIGHT or (planted and abilities[k][1] in FITS_SPIKE))]
        if fits:
            out.append({"slot": victim, "t": _r2(t), "kind": "held_at_death",
                        "abilities": [name for name, _ in fits], "kinds": sorted({kind for _, kind in fits})})
    return out


def _ults(rnd, blob, sides) -> dict[str, dict]:
    """Each slot's ult this round, for the site to find full ults never used: `cap_seen`, the points held just
    before a drop to 0 (a use, which proves the cost; None without one this round), `end`, the points at their
    death or the round's decision (`t`), and whether they used it by then. Whether `end` is full is the site's
    call, from every round of the match (app/services/replay_util.py `unused_ults`): one round rarely proves the
    cost, and a player can hold a full ult all round. Nothing without ult timelines (before revision 19)."""
    ps = blob.get("player_state")
    if not (ps or {}).get("ult"):
        return {}
    util = blob.get("util") or ()
    out = {}
    for slot in sides:
        end = min(rnd.life_end(slot, 0.0) if rnd.alive(slot, 0.0) else 0.0, rnd.t_decided)
        used = any(r.get("by") == slot and _key_of(r.get("name")) == "X" and r["t"] <= end for r in util
                   if r.get("k") == "ability")
        out[str(slot)] = {"cap_seen": _ult_cap(ps, slot), "end": _ult_at(ps, slot, end - _EPS), "t": _r2(end),
                          "used": used}
    return out


# --- the round


def _ult_cost(ps, c: _Cast):
    if "ult" not in c.kinds or c.by is None:
        return None
    n = _ult_at(ps, c.by, c.t - _EPS)
    return n


WATCHER_OF = (("TripWire", "trip"), ("PossessableCamera", "camera"), ("E_Turret", "turret"))


def _probe_kind(c: _Cast) -> str | None:
    """Which unknown-probe item kind this cast is: a molly, a trip, camera or turret, or another fixed watcher
    ("area": an alarmbot, a sensor). None for anything the engine doesn't treat as holding ground."""
    if "device" in c.kinds:
        for part, kind in WATCHER_OF:
            if any(part in n for n in c.names):
                return kind
        return "area"
    if "damaging" in c.kinds:
        return "molly"
    return None


def _holds_by_cast(rnd, casts: list[_Cast], holds: dict | None) -> dict[int, dict]:
    """Each probe item (app/critutil/unknown.py) to one cast: the cast holding the device's own row (`id`, the
    actor); else its owner's cast of the same kind whose landing is nearest its start (a buy-phase placement
    starts at the round's 0), within HOLD_MATCH_S, and for a molly nearest its place. One cast, one item."""
    out: dict[int, dict] = {}
    if not holds:
        return out
    taken = set()
    for item in sorted(holds.get("items") or (), key=lambda i: (i["t0"], i.get("id") is None)):
        best = None
        if item.get("id") is not None:
            best = next(((0.0, c) for c in casts if c.i not in taken and item["id"] in c.ids), None)
        for c in casts if best is None else ():
            if c.i in taken or c.by != item["by"] or _probe_kind(c) != item["kind"]:
                continue
            landed = max(c.landed_t if c.landed_t is not None else c.t, 0.0)
            gap = abs(landed - item["t0"])
            if gap > HOLD_MATCH_S:
                continue
            if item.get("uv") is not None and c.u is not None:
                gap += _dist_m(rnd, _px(*item["uv"]), _px(c.u, c.v))     # a metre is worth a second here
            if best is None or gap < best[0]:
                best = (gap, c)
        if best is not None:
            taken.add(best[1].i)
            out[best[1].i] = {k: item[k] for k in ("up_s", "front_s", "held_m2s", "crossed_s")}
    return out


def round_util(rnd, blob: dict, holds: dict | None = None) -> dict:
    """`holds`: the gap run's unknown probe result (app/critutil/unknown.py), None outside a gap run."""
    sides = _sides(rnd)
    agents = {p["slot"]: p.get("agent") for p in blob.get("players") or ()}
    contact = _first_contact(blob, rnd)
    spike = _spike(blob)
    plant = spike["t"] if spike else None
    casts = build_casts(rnd, blob)
    effects = _effects(rnd, blob, casts)
    ps = blob.get("player_state")
    kills = []
    for t, killer, victim in rnd.kills:
        if t > rnd.t_decided:
            continue
        before = _alive_counts(rnd, sides, t - _EPS)
        kills.append({"t": _r2(t), "killer": killer, "victim": victim, "k_side": sides.get(killer),
                      "v_side": sides.get(victim), "before": [before["attack"], before["defense"]]})
    defuse = next((float(a[1]) for a in (spike or {}).get("defuses") or ()
                   if isinstance(a, list) and len(a) > 3 and a[3] and a[1] is not None), None)
    held = _holds_by_cast(rnd, casts, holds)
    # outcomes two casts can share (a sight line, an enemy at an edge, a stopped defuse) are measured for every
    # cast first and then given out once
    cover = _cover_all(rnd, sides, casts)
    presence = {c.i: _presence(rnd, sides, c) for c in casts}
    _share_presence(presence)
    spikes = {c.i: _spike_effect(rnd, sides, c, spike, effects[c.i]) if plant is not None and c.t >= plant else None
              for c in casts}
    _share_defuses(spikes, {c.i: c.t for c in casts})
    out_casts = []
    for c in casts:
        eff = effects[c.i]
        side = sides.get(c.by)
        enemy_eff = [e for e in eff if sides.get(e["target"]) not in (None, side)]
        trigger = None
        if "device" in c.kinds:     # it fired, was broken, or first touched an enemy: whichever came first
            seen = [x for x in (_trigger(c), *(e["t0"] for e in enemy_eff)) if x is not None and x >= c.t - _EPS]
            trigger = min(seen) if seen else None
        alive = _alive_counts(rnd, sides, c.t)
        out_casts.append({
            "i": c.i, "by": c.by, "agent": c.agent, "t": _r2(c.t), "t_end": _r2(c.t_end), "u": c.u, "v": c.v,
            "names": sorted(set(c.names)), "codes": sorted({r.get("code") for r in c.rows if r.get("code")}),
            "kinds": sorted(c.kinds), "side": side,
            "type": "ult" if "ult" in c.kinds else (c.entry or {}).get("type") or "other",
            "ability_id": f"{c.entry['agent']}/{c.entry['ability']}" if c.entry else None,
            "end_unknown": c.end_unknown,
            "phase": _phase(c.t, contact, plant), "after_decided": c.t > rnd.t_decided,
            "alive": [alive["attack"], alive["defense"]],
            "trigger": _r2(trigger),
            "enemies": sorted({e["target"] for e in enemy_eff}),
            "effects": [{"target": e["target"], "how": e["how"], "t0": _r2(e["t0"]), "t1": _r2(e["t1"]),
                         "known": e["dur_known"]} for e in enemy_eff],
            "kills": _link_kills(rnd, sides, c, eff, trigger),
            "followed": _followed(rnd, blob, sides, c, eff),
            "harm": _harm(rnd, sides, c, eff),
            "cover": cover.get(c.i),
            "presence": presence[c.i],
            "spike": spikes[c.i],
            "hold": held.get(c.i),
            "ult_points": _ult_cost(ps, c),
            "activation": ((c.flash or {}).get("activation") or {}).get("targets_complete"),
        })
    has_causes = any(r.get("k") == "damage" and r.get("code") for r in blob.get("util") or ())
    return {
        "v": VERSION,
        "sides": {str(s): v for s, v in sides.items()},
        "contact": _r2(contact), "plant": _r2(plant), "defuse": _r2(defuse), "t_decided": _r2(rnd.t_decided),
        "kills": kills,
        "casts": out_casts,
        "heals": _heals(rnd, blob, sides),
        "unused": _unused(rnd, blob, sides, spike, agents),
        "ults": _ults(rnd, blob, sides),
        "avail": {"heals": "heal" in {r.get("k") for r in blob.get("util") or ()} or bool((ps or {}).get("charges")),
                  "causes": has_causes, "charges": bool((ps or {}).get("charges")),
                  "ult": bool((ps or {}).get("ult")),
                  "friendly_status": any(r.get("friendly") for r in blob.get("util") or ()) or None,
                  "unknown": bool(holds) and not (holds or {}).get("error")},
    }
