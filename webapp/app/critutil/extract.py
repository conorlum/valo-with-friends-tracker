"""Critical Util, one round: every cast with the evidence of what followed it (design:
docs/superpowers/specs/2026-10-21-critical-util-design.md).

Runs on the replay worker beside Critical Moments (app/control/task.py, the gap run). Pure: imports only
app.control, app.replays.player_state and app.critutil.constants. Slots only, never names. What a cast is worth is
not decided here: the site turns this evidence into values with the corpus's estimator (app/services/util_values.py).

Output: {"v", "sides", "plant", "defuse", "t_decided", "kills", "casts", "heals", "unused", "avail"}.
"""

from __future__ import annotations

import math

from app.control import engine
from app.critutil.constants import (AGENT_KEYS, DEVICE_PATTERNS, ENGAGE_LOOKBACK_S, FITS_ANY_FIGHT, FITS_SPIKE,
                                    FLASH_ROW_MATCH_S, FOLLOW_S, GRACE_S, HEAL_FOLLOW_S, LIFE_CAP_S, MERGE_M,
                                    MERGE_S, NEAR_TRIGGER_M, PART_WORDS, PRESENCE_STEP_S, SAVE_WINDOW_S,
                                    SIGHT_RANGE_M, SIGHT_STEP_S, SPIKE_NEAR_M, STOPPED_MPS, TRIGGER_WINDOW_S,
                                    VERSION, WAIT_RING_M, WALL_PATTERNS, ZONE_DAMAGE_LOOKBACK_S, ZONE_RADIUS_M)
from app.moments.constants import ABILITY_KIND

SKIP_KINDS = ("Projectile", "Bomb")
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


def _kinds_of(names: list[str]) -> set[str]:
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


def _is_part(name: str | None) -> bool:
    return bool(name) and any(w in name for w in PART_WORDS)


class _Cast:
    __slots__ = ("i", "by", "agent", "t", "t_end", "u", "v", "rows", "names", "ids", "kinds", "flash")

    def __init__(self, i, row, t_cap):
        self.i, self.by, self.agent = i, row.get("by"), row.get("agent")
        self.t = float(row["t"])
        self.t_end = _end(row, t_cap)
        self.u, self.v = row.get("u"), row.get("v")
        self.rows, self.names, self.ids = [row], [], set()
        self.flash = None
        self._add_names(row)

    def _add_names(self, row):
        name = row.get("name") or row.get("ability")
        if name:
            self.names.append(name)
        if row.get("id") is not None:
            self.ids.add(row["id"])

    def add(self, row, t_cap):
        self.rows.append(row)
        self.t_end = max(self.t_end, _end(row, t_cap))
        if self.u is None and row.get("u") is not None:
            self.u, self.v = row["u"], row["v"]
        self._add_names(row)


def _end(row, t_cap) -> float:
    t = float(row["t"])
    ends = [x for x in (row.get("t1"), row.get("gone")) if x is not None]
    end = min(ends) if ends else t + LIFE_CAP_S
    return min(max(float(end), t), t_cap)


def _dist_m(rnd, a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1]) * rnd.geo.m_per_px


def build_casts(rnd, blob: dict) -> list[_Cast]:
    """One _Cast per use of a charge: an ability row (not a projectile or the spike), or a flash/nearsight row,
    with the pieces of one cast (a manager and its zones, a trip's second wire) merged into it."""
    t_cap = rnd.t_end
    util = blob.get("util") or ()
    casts: list[_Cast] = []
    flashes = [r for r in util if r.get("k") in ("flash", "nearsight")]
    for r in sorted(flashes, key=lambda r: r["t"]):
        c = _Cast(len(casts), r, t_cap)
        c.flash = r
        c.kinds = {"blind"} if r["k"] == "flash" else {"status"}
        casts.append(c)
    abilities = sorted((r for r in util if r.get("k") == "ability" and r.get("kind") not in SKIP_KINDS),
                       key=lambda r: r["t"])
    for r in abilities:
        name = r.get("name")
        kinds = _kinds_of([name] if name else [])
        # the ability row of a flash or nearsight that already has its flash row
        twin = next((c for c in casts if c.flash is not None and c.by == r.get("by")
                     and abs(c.t - r["t"]) <= FLASH_ROW_MATCH_S and (kinds & {"blind", "status"} or not kinds)
                     and r.get("agent") == c.agent), None)
        if twin is not None and (not kinds or kinds <= {"blind", "status"}):
            twin.add(r, t_cap)
            continue
        host = None
        for c in reversed(casts):
            if c.flash is not None or c.by != r.get("by") or r["t"] - c.t > MERGE_S:
                continue
            if _key_of(c.names[0] if c.names else None) != _key_of(name):
                continue
            near = (c.u is None or r.get("u") is None
                    or _dist_m(rnd, _px(c.u, c.v), _px(r["u"], r["v"])) <= MERGE_M)
            if near or _is_part(name) or any(_is_part(n) for n in c.names):
                host = c
                break
        if host is not None:
            host.add(r, t_cap)
            continue
        c = _Cast(len(casts), r, t_cap)
        c.kinds = set()
        casts.append(c)
    for c in casts:
        c.kinds = set(c.kinds) | _kinds_of(c.names)
        if any(_key_of(n) == "X" for n in c.names):
            c.kinds.add("ult")
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
        died = any(victim == mate and e["t0"] <= t <= e["t1"] + GRACE_S and sides.get(killer) not in (None, side)
                   for t, killer, victim in rnd.kills)
        out.append({"mate": mate, "how": e["how"], "t": _r2(e["t0"]), "dur": _r2(e["t1"] - e["t0"]),
                    "died": died})
    return out


def _cover(rnd, sides, c: _Cast) -> dict | None:
    """For a smoke or wall: sight lines between living enemies and the caster's team that this cast alone cut,
    sampled every SIGHT_STEP_S over its life to t_decided. None for any other cast, or without a smoke shape."""
    if not ({"smoke", "wall"} & c.kinds):
        return None
    side = sides.get(c.by)
    if side is None:
        return None
    mine = [(t0, t1, x, y, r, solid) for t0, t1, x, y, r, solid in rnd.smokes
            if c.u is not None and abs(t0 - c.t) <= 1.0 and math.hypot(x - _px(c.u, c.v)[0], y - _px(c.u, c.v)[1]) < 1.0]
    walls = [(t0, t1, w) for t0, t1, w in rnd.walls if abs(t0 - c.t) <= 1.0]
    if not mine and not walls:
        return None
    end = min(c.t_end, rnd.t_decided)
    blocked_s, pairs, covered = 0.0, set(), {}
    t = c.t
    while t < end:
        shapes = [(x, y, r, solid) for t0, t1, x, y, r, solid in mine if t0 <= t < t1] + \
                 [w for t0, t1, w in walls if t0 <= t < t1]
        if shapes:
            for a, sa in sides.items():
                if sa != side or not rnd.alive(a, t):
                    continue
                pa = rnd.pos(a, t)
                if pa is None:
                    continue
                for b, sb in sides.items():
                    if sb in (None, side) or not rnd.alive(b, t):
                        continue
                    pb = rnd.pos(b, t)
                    if pb is None or _dist_m(rnd, pa, pb) > SIGHT_RANGE_M:
                        continue
                    la, lb = (pa[0], pa[1], None), (pb[0], pb[1], None)
                    if engine.los(rnd.geo, la, lb, []) and not engine.los(rnd.geo, la, lb, shapes):
                        blocked_s += SIGHT_STEP_S
                        pairs.add((a, b))
                        covered[a] = t
        t += SIGHT_STEP_S
    lived = [m for m, last in covered.items()
             if rnd.alive(m, min(rnd.t_decided, last + SAVE_WINDOW_S))]
    return {"blocked_s": _r2(blocked_s), "pairs": len(pairs), "covered": sorted(covered),
            "covered_lived": sorted(lived)}


def _presence(rnd, sides, c: _Cast) -> dict | None:
    """Observed enemy presence around a placed cast while it was up, to t_decided: enemy-seconds inside it,
    enemy-seconds stopped just outside it (waiting on it), and enemies who came within the ring and never
    entered (went around or held back)."""
    if c.u is None:
        return None
    side = sides.get(c.by)
    kind = next((k for k in ("device", "smoke", "damaging", "status") if k in c.kinds), "default")
    r = ZONE_RADIUS_M[kind]
    x, y = _px(c.u, c.v)
    end = min(c.t_end, rnd.t_decided)
    inside_s = waiting_s = 0.0
    entered, approached = set(), set()
    prev = {}
    t = c.t
    while t < end:
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
                approached.add(s)
                q = prev.get(s)
                if q is not None and _dist_m(rnd, p, q) / PRESENCE_STEP_S < STOPPED_MPS:
                    waiting_s += PRESENCE_STEP_S
            prev[s] = p
        t += PRESENCE_STEP_S
    return {"r_m": r, "armed_s": _r2(max(0.0, end - c.t)), "inside_s": _r2(inside_s), "waiting_s": _r2(waiting_s),
            "entered": sorted(entered), "held_back": sorted(approached - entered)}


def _spike_effect(rnd, sides, c: _Cast, spike) -> dict | None:
    """Defuse attempts that overlapped this cast, near the spike, and whether each was stopped (not finished)."""
    if spike is None or spike.get("u") is None or c.u is None:
        return None
    if _dist_m(rnd, _px(c.u, c.v), _px(spike["u"], spike["v"])) > SPIKE_NEAR_M:
        return None
    attempts = []
    for a in spike.get("defuses") or ():
        if not isinstance(a, list) or len(a) < 3:
            continue
        t0 = float(a[0])
        t1 = float(a[1]) if a[1] is not None else t0
        if t1 < c.t or t0 > c.t_end:
            continue
        attempts.append({"t": _r2(t0), "by": a[2], "finished": bool(a[3]) if len(a) > 3 else None})
    return {"near_spike": True, "attempts": attempts,
            "stopped": sum(1 for a in attempts if a["finished"] is False)}


# --- heals and unused util


def _vitals_before(ps, slot, t):
    from app.replays.player_state import vitals_at

    return vitals_at(ps, slot, t - _EPS)


def _heals(rnd, blob, sides) -> list[dict]:
    ps = blob.get("player_state")
    out = []
    for r in blob.get("util") or ():
        if r.get("k") != "heal" or r.get("target") is None:
            continue
        target, t0 = r["target"], float(r["t"])
        t1 = float(r.get("t1") if r.get("t1") is not None else t0)
        before = _vitals_before(ps, target, t0)
        hp_before = None if before is None else before.get("hp")
        sh_before = 0 if before is None else (before.get("sh") or 0)
        life_end = rnd.life_end(target, t1)
        horizon = min(t1 + HEAL_FOLLOW_S, rnd.t_decided)
        dmg =[d for d in blob.get("util") or () if d.get("k") == "damage" and d.get("target") == target
               and t1 <= float(d["t"]) <= horizon and sides.get(d.get("by")) not in (None, sides.get(target))]
        fought = bool(dmg) or any(r2.get("k") == "damage" and r2.get("by") == target
                                  and t1 <= float(r2["t"]) <= horizon for r2 in blob.get("util") or ())
        taken = _damage_taken(ps, target, t1, horizon)
        survived = rnd.alive(target, horizon - _EPS) or life_end >= horizon
        saved = None
        if hp_before is not None and taken is not None:
            saved = bool(survived and taken >= hp_before + sh_before and r.get("pool") in ("hp", "armor"))
        out.append({"t": _r2(t0), "t1": _r2(t1), "by": r.get("by"), "target": target, "pool": r.get("pool"),
                    "amount": _r2(r.get("amount")), "name": r.get("name"), "code": r.get("code"),
                    "hp_before": _r2(hp_before), "taken_after": _r2(taken), "fought": fought,
                    "survived": bool(survived), "saved": saved})
    return out


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
    """Deaths holding util that fits the fight, in fights the player engaged first; and full ults never used,
    once per player-round. Without charge timelines (before revision 21) no death claim is made."""
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
        keys = AGENT_KEYS.get(agents.get(victim) or "", {})
        planted = spike is not None and spike["t"] <= t
        fits = [k for k in held if k in keys and (keys[k] in FITS_ANY_FIGHT or (planted and keys[k] in FITS_SPIKE))]
        if fits:
            out.append({"slot": victim, "t": _r2(t), "kind": "held_at_death", "keys": sorted(fits),
                        "kinds": sorted({keys[k] for k in fits})})
    for slot in sides:
        cap = _ult_cap(ps, slot)
        if cap is None:
            continue
        end = min(rnd.life_end(slot, 0.0) if rnd.alive(slot, 0.0) else 0.0, rnd.t_decided)
        n = _ult_at(ps, slot, end - _EPS)
        if n is not None and n >= cap:
            used = any(r.get("by") == slot and _key_of(r.get("name")) == "X" and r["t"] <= end for r in util
                       if r.get("k") == "ability")
            if not used:
                out.append({"slot": slot, "t": _r2(end), "kind": "ult_unused", "points": n})
    return out


# --- the round


def _ult_cost(ps, c: _Cast):
    if "ult" not in c.kinds or c.by is None:
        return None
    n = _ult_at(ps, c.by, c.t - _EPS)
    return n


def round_util(rnd, blob: dict) -> dict:
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
    out_casts = []
    for c in casts:
        eff = effects[c.i]
        side = sides.get(c.by)
        enemy_eff = [e for e in eff if sides.get(e["target"]) not in (None, side)]
        trigger = _trigger(c) if "device" in c.kinds else None
        alive = _alive_counts(rnd, sides, c.t)
        out_casts.append({
            "i": c.i, "by": c.by, "agent": c.agent, "t": _r2(c.t), "t_end": _r2(c.t_end), "u": c.u, "v": c.v,
            "names": sorted(set(c.names)), "kinds": sorted(c.kinds), "side": side,
            "phase": _phase(c.t, contact, plant), "after_decided": c.t > rnd.t_decided,
            "alive": [alive["attack"], alive["defense"]],
            "trigger": _r2(trigger),
            "enemies": sorted({e["target"] for e in enemy_eff}),
            "effects": [{"target": e["target"], "how": e["how"], "t0": _r2(e["t0"]), "t1": _r2(e["t1"]),
                         "known": e["dur_known"]} for e in enemy_eff],
            "kills": _link_kills(rnd, sides, c, eff, trigger),
            "followed": _followed(rnd, blob, sides, c, eff),
            "harm": _harm(rnd, sides, c, eff),
            "cover": _cover(rnd, sides, c),
            "presence": _presence(rnd, sides, c),
            "spike": _spike_effect(rnd, sides, c, spike) if plant is not None and c.t >= plant else None,
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
        "avail": {"heals": "heal" in {r.get("k") for r in blob.get("util") or ()} or bool((ps or {}).get("charges")),
                  "causes": has_causes, "charges": bool((ps or {}).get("charges")),
                  "ult": bool((ps or {}).get("ult")), "friendly_status": False},
    }
