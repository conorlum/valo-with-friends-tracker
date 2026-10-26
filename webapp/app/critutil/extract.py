"""Critical Util, one round: every cast with the evidence of what followed it (design:
docs/superpowers/specs/2026-10-21-critical-util-design.md).

Runs on the replay worker beside Critical Moments (app/control/task.py, the gap run). Pure: imports only
app.control, app.replays.player_state and app.critutil.constants. Slots only, never names. What a cast is worth is
not decided here: the site turns this evidence into values with the corpus's estimator (app/services/util_values.py).

Output: {"v", "sides", "agents", "contact", "plant", "defuse", "t_decided", "kills", "casts", "heals", "unused", "ults",
"avail"}.
"""

from __future__ import annotations

import functools
import math
import re

from app.control import engine
from app.critutil import abilities, windows
from app.critutil.constants import (AGENT_KEYS, ALARMBOT_YAW_WINDOW_S, ARC_ROSE, ROSE_REPOP_S, TRADEMARK_FIRE_LEAD_S, TRADEMARK_PIECE_LEAD_S, BUFF_MATCH_S, BUY_PLACED_S, DEVICE_PATTERNS, SAME_PLACE_M, ENGAGE_LOOKBACK_S, FITS_ANY_FIGHT, FITS_SPIKE,
                                    FLASH_ROW_MATCH_S, FLASH_TAIL_S, FOLLOW_S, GRACE_S, HEAL_FOLLOW_S, HOLD_MATCH_S, MOBILITY_KEYS,
                                    MERGE_S, NEAR_TRIGGER_M, PRESENCE_KINDS, REPEAT_PARTS, REPEAT_S, PRESENCE_STEP_S, SAVE_WINDOW_S,
                                    SIGHT_RANGE_M, SIGHT_STEP_S, SPIKE_NEAR_M, STOPPED_MPS, THROW_MATCH_S, TRIGGER_WINDOW_S, TURN_AWAY_M,
                                    ULT_SHOT_MATCH_S, ULT_WEAPONS, VERSION, WAIT_RING_M, WALL_PATTERNS, ZONE_DAMAGE_LOOKBACK_S, ZONE_RADIUS_M)
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


def _entry_by_match(match: str) -> dict | None:
    return next((e for e in abilities.load() if e["match"] == match), None)


@functools.lru_cache(maxsize=None)
def _parents_for(code_name: str) -> frozenset:
    """The `match` strings of the table entries that list `<code>_<name>` among their pieces."""
    return frozenset(e["match"] for e in abilities.load()
                     if any(re.search(p, code_name) for p in e.get("pieces") or ()))


def _code_name(row) -> str | None:
    """`<code>_<name>`, the key the table matches; a flash or nearsight row carries its name as `ability`."""
    name = row.get("name") or row.get("ability")
    return f"{row['code']}_{name}" if row.get("code") and name else None


def _parents_of(row) -> frozenset:
    key = _code_name(row)
    return _parents_for(key) if key else frozenset()


def _entry_of(row) -> dict | None:
    """The ability table's entry for a row (matched on `<code>_<name>`), None when the row has no code or fits none."""
    key = _code_name(row)
    return _entry_for(key) if key else None


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
        if not first and self.flash is None:    # a flash ends with its own row; its projectile never stretches it
            # the row that ends last decides whether the cast's end is a guess
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


def _ult_row(row) -> bool:
    """An ult's row: an X-slot name, unless its table entry says it is something else (Astra's stars are placed
    in Astral Form, not her ult)."""
    if _key_of(row.get("name") or row.get("ability")) != "X":
        return False
    e = _entry_of(row)
    return e is None or e.get("type") == "ult"


def _throw_t(row) -> float:
    thrown = row.get("thrown") or {}
    return float(thrown["t0"]) if thrown.get("t0") is not None else float(row["t"])


def _end(row, rnd) -> tuple[float, bool]:
    """(when the row ends, whether that is a guess). A row with a close ends there; one without ends at `t` plus the
    table's duration, a placeable at t_decided; with neither it ends at t_decided and is flagged `end_unknown`."""
    t = float(row["t"])
    ends = [x for x in (row.get("t1"), row.get("gone")) if x is not None]
    unknown = False
    pop = row.get("pop") if row.get("k") == "flash" else None
    if pop and pop.get("t") is not None:
        end = float(pop["t"]) + FLASH_TAIL_S      # a flash is over a second after it pops
    elif ends:
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
    go = (c.entry or {}).get("go_off")
    if go and re.search(go, f"{row.get('kind')}:{_code_name(row) or ''}"):
        return False                # the moment this use was set off (Razorvine's patch), not another use
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
    """One _Cast per use of a charge. A flash or nearsight row, or a projectile, starts one (a thrown ability); a
    caster's buff rows that start together (Harmonize on Miks and the teammate) are one; a movement `cast` row is
    one; the pieces it lands as join it: a piece whose `thrown.t0` is its throw, or one that starts while it is still up
    (a recon's pings, a smoke's zone, a drone's darts). A placed ability with no projectile starts its own. The
    spike is not a cast."""
    util = blob.get("util") or ()
    casts: list[_Cast] = []
    codes = {}                              # slot -> its code, for flash rows that carry none
    for r in util:
        if r.get("k") == "ability" and r.get("code") and r.get("by") is not None:
            codes.setdefault(r["by"], r["code"])
    for r in sorted((r for r in util if r.get("k") in ("flash", "nearsight")), key=lambda r: r["t"]):
        if not r.get("code") and codes.get(r.get("by")):
            r = {**r, "code": codes[r["by"]]}
        c = _Cast(len(casts), r, rnd)
        c.flash = r
        c.kinds = {"blind"} if r["k"] == "flash" else {"status"}
        casts.append(c)
    for r in sorted((r for r in util if r.get("k") == "buff" and r.get("by") is not None), key=lambda r: r["t"]):
        host = next((c for c in reversed(casts) if c.by == r["by"] and c.rows[0].get("k") == "buff"
                     and abs(c.t - float(r["t"])) <= BUFF_MATCH_S), None)
        if host is not None:
            host.add(r, rnd)                # the teammate a Harmonize was cast on: the same use
        else:
            c = _Cast(len(casts), r, rnd)
            c.kinds = set()
            casts.append(c)
    for r in sorted((r for r in util if r.get("k") == "cast" and r.get("by") is not None), key=lambda r: r["t"]):
        c = _Cast(len(casts), r, rnd)       # a movement ability (Jett's dash): no object, no place, one row a use
        c.kinds = set()
        casts.append(c)
    abilities =sorted((r for r in util if r.get("k") == "ability" and r.get("kind") != "Bomb"),
                       key=lambda r: (r["t"], r.get("kind") != "Projectile", "end" not in r))
    for r in abilities:
        name, by, tt = r.get("name"), r.get("by"), _throw_t(r)
        kinds = _kinds_of([name] if name else [])
        entry, parents = _entry_of(r), _parents_of(r)
        twin = next((c for c in reversed(casts) if c.flash is not None and c.by == by
                     and abs(c.t - tt) <= FLASH_ROW_MATCH_S), None)
        if twin is not None and twin.entry is not None and twin.entry["match"] in parents:
            twin.add(r, rnd)                # the table's own piece of this flash (its projectile lingers past the pop)
            continue
        if twin is not None and entry is None and (not kinds or kinds <= {"blind", "status"}):
            pop = twin.flash.get("pop") or {}
            if pop.get("t") is not None:
                bound = float(pop["t"]) + FLASH_TAIL_S
            elif twin.flash["k"] == "nearsight":
                bound = _end(twin.flash, rnd)[0] + FLASH_TAIL_S
            else:
                bound = None
            if bound is None or _end(r, rnd)[0] <= bound + _EPS:
                twin.add(r, rnd)            # a flash's own projectile and source rows the table doesn't name
                continue
        host = _anchored(casts, r) if r.get("kind") != "Projectile" else None
        if host is None and parents:        # a piece of a table ability: its owner's latest cast of the parent
            for c in reversed(casts):
                if c.flash is not None or c.by != by or c.entry is None or c.entry["match"] not in parents:
                    continue
                if r.get("code") == "Deadeye" and r.get("name") == "E_Slow_Large" \
                        and (r.get("thrown") or {}).get("t0") is None and not _in_dart_window(r, _trap_t1(c)):
                    break                   # a slow with no throw far from the trap's close is an ult kill's, its own cast
                if ((r.get("thrown") or {}).get("t0") is not None and abs(c.t - tt) <= THROW_MATCH_S) \
                        or c.t - _EPS <= r["t"] <= c.t_end + MERGE_S:
                    host = c
                break
        if host is None and not parents and r.get("kind") != "Projectile":
            for c in reversed(casts):
                if c.flash is not None or c.by != by or c.key != _key_of(name) or c.rows[0].get("k") in ("buff", "cast"):
                    continue
                if entry is not None and (c.entry is None or c.entry["match"] != entry["match"]):
                    break                   # a row with its own ability only joins a cast of that ability
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
        if c.entry is None and len(parents) == 1:
            parent = _entry_by_match(next(iter(parents)))
            if parent is not None and parent.get("rule") == "flash":
                # A flash ability's piece with no flash row (an Arc Rose put down in the buy phase): a use of
                # that ability, whose pops can't be seen (trap-jobs D12).
                c.entry = parent
                c.kinds = set(parent.get("kinds") or ())
        casts.append(c)
    for c in casts:
        c.kinds = set(c.kinds) | _kinds_of(c.names, c.rows)
        if any(_ult_row(r) for r in c.rows):
            c.kinds.add("ult")
        if any(f"{x.get('code')}_{x.get('name')}" in MOBILITY_KEYS for x in c.rows):
            c.kinds.add("dash")
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
                # and of the same ability slot (a Jett's ult knives are never her dash's)
                key = _key_of(name)
                cands = [c for c in casts if c.by == r.get("by") and c.t - _EPS <= t <= c.t_end + 1.0
                         and r["code"] in {x.get("code") for x in c.rows}
                         and (key is None or c.key is None or c.key == key)]
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


def _is_trap_row(r) -> bool:
    return r.get("code") == "Deadeye" and r.get("name") == "E_Trap"


def _trap_t1(c: _Cast) -> float | None:
    """When a Trademark cast's trap row closed (None for any other cast)."""
    return next((float(r["t1"]) for r in c.rows if _is_trap_row(r) and r.get("t1") is not None), None)


def _in_dart_window(r, trap_t1) -> bool:
    """A Trademark piece (its dart or its slow) whose `t` is 1.8-2.2 s before the trap row's close: the trap fired."""
    lo, hi = TRADEMARK_PIECE_LEAD_S
    return trap_t1 is not None and lo <= trap_t1 - float(r["t"]) <= hi


def _is_rose(c: _Cast) -> bool:
    return c.entry is not None and (c.entry.get("agent"), c.entry.get("ability")) == ARC_ROSE


def _rose_pops(c: _Cast) -> list[float]:
    """An Arc Rose's pops: its flash row's `pop`, then each group of blinds more than ROSE_REPOP_S after the last
    pop. A pop that blinded nobody is still a pop. Never its `fx` (its arming, or its reveal at Vyse's death)."""
    if c.flash is None:
        return []
    pops = []
    pop = (c.flash.get("pop") or {}).get("t")
    if pop is not None:
        pops.append(float(pop))
    for t in sorted(float(h[1]) for h in c.flash.get("hits") or () if len(h) > 1):
        if not pops or t > pops[-1] + ROSE_REPOP_S:
            pops.append(t)
    return sorted(pops)


def _rose_gone(c: _Cast) -> float | None:
    gone = [float(r["gone"]) for r in c.rows if r.get("gone") is not None]
    return min(gone) if gone else None


def _rose_end(rnd, c: _Cast) -> float:
    """When the rose itself left the map: its object's close (a cast started by its flash row ends with the
    flash), else the round's end."""
    ends = [float(r["t1"]) for r in c.rows if r.get("k") == "ability" and r.get("t1") is not None]
    return min(max(ends), rnd.t_end) if ends else rnd.t_end


def _trigger_kind(c: _Cast) -> tuple[float, str] | None:
    """(when the device was set off, "fired" | "broken"), the first of its signals in this order: fx, raised, gone,
    then a Trademark's dart or slow, then an Alarmbot's second yaw. An Arc Rose has its own: its first pop, else its
    destruction."""
    if _is_rose(c):
        pops, gone = _rose_pops(c), _rose_gone(c)
        if pops and (gone is None or pops[0] <= gone + _EPS):
            return pops[0], "fired"
        return (gone, "broken") if gone is not None else None
    for r in c.rows:
        fx = r.get("fx")
        if isinstance(fx, (int, float)):
            return float(fx), "fired"
        if isinstance(fx, list) and fx:
            first = fx[0]
            return float(first[0] if isinstance(first, list) else first), "fired"
        if isinstance(r.get("raised"), list) and r["raised"]:
            return float(r["raised"][0]), "fired"
        if r.get("gone") is not None:
            return float(r["gone"]), "broken"
    trap_t1 = _trap_t1(c)
    for r in c.rows:
        if r.get("code") == "Deadeye" and r.get("name") in ("4_Trap_Dart", "E_Slow_Large") \
                and _in_dart_window(r, trap_t1):
            return _throw_t(r) - TRADEMARK_FIRE_LEAD_S, "fired"
        yaws = r.get("yaws")
        if r.get("code") == "Killjoy" and r.get("name") == "Q_StealthAlarmbot" and r.get("t1") is not None \
                and isinstance(yaws, list):
            later = [float(y[0]) for y in yaws[1:] if isinstance(y, list) and y
                     and float(r["t1"]) - ALARMBOT_YAW_WINDOW_S <= float(y[0]) <= float(r["t1"]) + _EPS]
            if later:
                return later[0], "fired"
    return None


def _trigger(c: _Cast) -> float | None:
    got = _trigger_kind(c)
    return got[0] if got else None


def _set_off(rnd, c: _Cast, enemy_eff, trigger) -> tuple[float | None, str | None]:
    """How a device was set off: "fired" (its own signal or an enemy touching it), "broken" (it vanished), "closed"
    (its row ended before the round was decided with its owner alive, a quiet close), else None. Only `fired` and
    `broken` carry a trigger time; the caller's `trigger` is untouched by "closed"."""
    if trigger is not None:
        got = _trigger_kind(c)
        return trigger, (got[1] if got and abs(got[0] - trigger) <= _EPS else "fired")
    if _is_rose(c):
        # An Arc Rose that leaves quietly with Vyse alive was pulled back (trap-jobs D12: popped, destroyed or recalled)
        end = _rose_end(rnd, c)
        if end < rnd.t_decided - _EPS and rnd.alive(c.by, end):
            return end, "recalled"
        return None, None
    if any(r.get("t1") is not None for r in c.rows) and c.t_end < rnd.t_decided - _EPS \
            and rnd.alive(c.by, c.t_end):
        return c.t_end, "closed"
    return None, None


def _rose(rnd, c: _Cast, set_off_kind) -> dict | None:
    """An Arc Rose's whole story: every pop, and how it ended: "popped" (its last pop), "destroyed" (shot, popped or
    not), "recalled", "revealed" (Vyse died while it stood) or "stood" (to the round's end). `seen` is False for a rose
    with no flash row (put down in the buy phase), whose pops the replay doesn't carry."""
    if not _is_rose(c):
        return None
    pops, gone = _rose_pops(c), _rose_gone(c)
    if gone is not None:
        how = "destroyed"
    elif set_off_kind == "recalled":
        how = "recalled"
    elif not rnd.alive(c.by, min(_rose_end(rnd, c), rnd.t_decided) - _EPS):
        how = "revealed"            # Vyse died while it stood: it is off, though its object may stay to the round's end
    elif pops:
        how = "popped"
    else:
        how = "stood"
    return {"pops": [_r2(t) for t in pops], "ended": how, "gone": _r2(gone), "seen": c.flash is not None}


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


def _death_after(rnd, slot, t: float) -> float | None:
    """When the slot's life that holds t ended before the round did: a death, the link's database-only ones and
    a disconnect included (the round's lives, not only its kills). None while they lived on (or no such life)."""
    end = next((b for a, b in rnd.lives.get(slot, ()) if a <= t < b), None)
    if end is None:                     # not alive at t (a buy-phase placement clamped to 0): their first death
        return next((tt for tt, _a, b in sorted(rnd.kills) if tt > t and b == slot), None)
    return None if end == math.inf or end >= rnd.t_end - _EPS else float(end)


def _owner_death(rnd, c: _Cast) -> float | None:
    """When the cast's owner died after throwing it, for a sentinel's placed util (`off_at_death`), else None."""
    if not (c.entry or {}).get("off_at_death"):
        return None
    return _death_after(rnd, c.by, c.t)


def _go_off_t(c: _Cast) -> float | None:
    """When the cast was set off: its first row matching the entry's `go_off` (Nanoswarm's swarm), else None."""
    go = (c.entry or {}).get("go_off")
    ts = [float(r["t"]) for r in c.rows if go and re.search(go, f"{r.get('kind')}:{_code_name(r) or ''}")]
    return min(ts) if ts else None


def _active_spans(rnd, c: _Cast) -> list[list[float]]:
    """When the cast could act, to t_decided, as sorted disjoint spans: its life; for one that acts only once set
    off (`from_trigger`), from its set-off to its set-off pieces' end (else the entry's `after_trigger_s`): a
    Nanoswarm never set off has none, one set off burns 4 s; inside its stored `on` spans when a row carries them
    (Viper's clouds; `on: []` is known never on; a row with no `on` key, stored before them, keeps its life); minus
    its `off` spans; cut at its owner's death when it is a sentinel's placed util (the object stays in the
    replay, but it is off)."""
    end = min(c.t_end, rnd.t_decided)
    death = _owner_death(rnd, c)
    if death is not None:
        end = min(end, death)
    start = c.t
    entry = c.entry or {}
    if entry.get("from_trigger"):
        start = _go_off_t(c)
        if start is None:
            return []
        go = entry.get("go_off")
        pieces = [float(min(x for x in (r.get("t1"), r.get("gone")) if x is not None)) for r in c.rows
                  if re.search(go, f"{r.get('kind')}:{_code_name(r) or ''}")
                  and (r.get("t1") is not None or r.get("gone") is not None)]     # observed ends only
        after = entry.get("after_trigger_s")
        end = min(end, max(pieces) if pieces else (start + after if after is not None else end))
    spans = [[start, end]]
    if any("on" in r for r in c.rows):
        on = [[a, end if b is None else b] for r in c.rows for a, b in (r.get("on") or ())]
        spans = [[max(a, start), min(b, end)] for a, b in on if min(b, end) > max(a, start)]
    off = [[a, b] for r in c.rows for a, b in (r.get("off") or ())]
    return windows._cut([[a, b] for s0, s1 in spans for a, b in windows._minus([s0, s1], off) if b > a], None)


def _area_dist_m(rnd, p, hulls) -> float:
    """Metres from a point to the nearest of an area's convex hulls (map px): 0 inside one."""
    best = math.inf
    for hull in hulls:
        n = len(hull)
        signs = set()
        for i in range(n):
            (ax, ay), (bx, by) = hull[i], hull[(i + 1) % n]
            cross = (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)
            if abs(cross) > _EPS:
                signs.add(cross > 0)
            dx, dy = bx - ax, by - ay
            k = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
            best = min(best, math.hypot(p[0] - ax - k * dx, p[1] - ay - k * dy))
        if n >= 3 and len(signs) <= 1:
            return 0.0
    return best * rnd.geo.m_per_px


def _presence(rnd, sides, c: _Cast) -> dict | None:
    """Observed enemy presence around a placed area cast (smoke, molly, wall, device) while it could act
    (`_active_spans`): enemy-seconds inside it, enemy-seconds stopped just outside it (waiting on it), and enemies
    who came to its edge and then turned away without entering (held back). None for any other cast."""
    if c.u is None or not (c.kinds & PRESENCE_KINDS):
        return None
    side = sides.get(c.by)
    kind = next((k for k in ("device", "smoke", "damaging", "status") if k in c.kinds), "default")
    r = ZONE_RADIUS_M[kind]
    x, y = _px(c.u, c.v)
    area = next(([[_px(*pt) for pt in hull] for hull in row["area"]] for row in c.rows if row.get("area")), None)
    if area:
        r = 0.0                     # its real ground (Viper's Pit, CONDENSE 23): distances are to its edge

        def dist(p):
            return _area_dist_m(rnd, p, area)
    else:
        def dist(p):
            return _dist_m(rnd, p, (x, y))
    spans = _active_spans(rnd, c)
    inside_s = 0.0
    entered, waits = set(), set()
    gaps: dict[int, float] = {}         # each enemy who turned away: how close they came to the edge first
    for a, b in spans:
        # each active span on its own: a speed or an approach from before an off span says nothing of this one
        prev, closest = {}, {}
        for t in _grid(a, b, PRESENCE_STEP_S):
            for s, sd in sides.items():
                if sd in (None, side) or not rnd.alive(s, t):
                    continue
                p = rnd.pos(s, t)
                if p is None:
                    continue
                d = dist(p)
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
                    gaps[s] = min(gaps.get(s, closest[s] - r), closest[s] - r)   # came to the edge, then went back
                prev[s] = p
    # `_waits` and `_gap` are for sharing between casts (`_share_presence`), never stored
    # `life_s`: how long its object was there at all, active or not (a device "gone at once" is wasted; one left
    # hidden is not, by that rule: util_values.is_wasted)
    return {"r_m": r, "area": bool(area), "armed_s": _r2(sum(b - a for a, b in spans)),
            "life_s": _r2(max(0.0, min(c.t_end, rnd.t_decided) - c.t)), "inside_s": _r2(inside_s),
            "waiting_s": _r2(len(waits) * PRESENCE_STEP_S), "entered": sorted(entered),
            "held_back": sorted(set(gaps) - entered), "_waits": waits,
            "_gap": {s: g for s, g in gaps.items() if s not in entered}}


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
    spike, active (`_active_spans`) when the attempt stopped ("zone"). `stopped` is set by `_share_defuses`: each stopped attempt goes to one cast."""
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
            elif "damaging" in c.kinds and gap <= ZONE_RADIUS_M["damaging"] \
                    and any(a - _EPS <= t1 <= b + _EPS for a, b in _active_spans(rnd, c)):
                evidence = "zone"       # burning on the spike when the attempt stopped: not a hidden or off one
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
        death = next((t for t, _k, v in sorted(rnd.kills) if v == target and t >= t1), None)
        end = death if death is not None else rnd.t_decided
        lost = _damage_taken(ps, target, t1, end)
        kept = None if lost is None or hp_before is None else lost > hp_before
        hits_after = None
        if kept:
            t_cross = _loss_crossing(ps, target, t1, end, hp_before)
            hits_after = 0 if t_cross is None else sum(
                int(d.get("n") or 1) for d in blob.get("util") or ()
                if d.get("k") == "damage" and d.get("target") == target and d.get("src") == "gun"
                and sides.get(d.get("by")) not in (None, sides.get(target))
                and t_cross - _EPS <= float(d["t"]) <= end)
        out.append({"t": _r2(t0), "t1": _r2(t1), "by": r.get("by"), "target": target, "pool": r.get("pool"),
                    "amount": _r2(r.get("amount")), "name": r.get("name"), "code": r.get("code"),
                    "hp_before": _r2(hp_before), "taken_after": _r2(taken), "fought": fought,
                    "survived": bool(survived), "saved": None if share is None else share > 0,
                    "saved_share": None if share is None else round(share, 3),
                    "death": _r2(death), "kept_alive": kept, "hits_after": hits_after})
    return out


def _loss_crossing(ps, slot, t0, t1, limit) -> float | None:
    """The time of the vitals event at which the health lost since t0 (as `_damage_taken` adds it) first exceeds
    `limit`, or None."""
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
                if lost > limit:
                    return float(e["t"])
        prev = e
    return None


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


def _first_use(util, slot, agent, end):
    """The time of the slot's first use of a timed ult (the shot, knife or gun-less shot that proves it, or Reyna
    equipping Empress, else its heal on activation), up to `end`; None when it has none (in an old blob, Reyna at
    full health shows no heal)."""
    if agent == "Reyna":
        # Empress fires the moment it is equipped (revision 22's `equip` rows); blobs from before have only its heal
        want = lambda r: r.get("name") == "X_Frenzy" and r.get("k") in ("equip", "heal")
    elif agent == "Chamber":
        want = lambda r: r.get("k") == "shot" and r.get("gun") == "Tour de Force"
    elif agent == "Jett":
        want = lambda r: (r.get("k") == "damage" and r.get("name") == "X_Dagger_Production"
                          and r.get("from") == "equippable")
    elif agent == "Neon":
        want = lambda r: r.get("k") == "shot" and r.get("gun") is None
    else:
        return None
    ts = [r["t"] for r in util if r.get("by") == slot and r["t"] <= end and want(r)]
    return min(ts) if ts else None


def _use(rnd, ps, util, slot, agent, end) -> dict | None:
    entry = next((e for e in abilities.load() if e.get("agent") == agent and e.get("type") == "ult"
                  and e.get("rule") in ("ult:until_end", "ult:until_death")), None) if agent else None
    if entry is None:
        return None
    t = _first_use(util, slot, agent, end)
    line = ((ps or {}).get("ult") or {}).get(str(slot)) or []
    end_t = death = None
    if t is not None:
        end_t = next((p["t"] for p in line if p["t"] > t and p["n"] == 0), None)
        death = next((k for k, a, b in sorted(rnd.kills) if k > t and b == slot), None)
    return {"t": _r2(t), "ability_id": f"{entry['agent']}/{entry['ability']}", "type": "ult",
            "signals": {"ult_end": _r2(end_t), "death": _r2(death)}}


def _ults(rnd, blob, sides, agents=None) -> dict[str, dict]:
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
        used = any(r.get("by") == slot and _ult_row(r) and r["t"] <= end for r in util if r.get("k") == "ability")
        out[str(slot)] = {"cap_seen": _ult_cap(ps, slot), "end": _ult_at(ps, slot, end - _EPS), "t": _r2(end),
                          "used": used, "use": _use(rnd, ps, util, slot, (agents or {}).get(slot), end)}
    return out


# --- the round


def _ult_cost(ps, c: _Cast):
    if "ult" not in c.kinds or c.by is None:
        return None
    n = _ult_at(ps, c.by, c.t - _EPS)
    return n


SPIKE_PICKUP_S = 15.0


def _omen_spike(rnd, blob, c: _Cast, arrival) -> dict:
    """Whether the Omen who landed picked the spike up within SPIKE_PICKUP_S of arriving (a `carried` entry after a
    `dropped` one), and whether it was planted later. The picker is the entry's slot, else the alive player nearest
    the drop place."""
    out = {"spike_pickup": None, "got_spike": None, "got_spike_planted": None}
    line = (blob.get("player_state") or {}).get("spike")
    if arrival is None or not line:
        return out
    out["got_spike"] = False
    pick = None
    for prev, cur in zip(line, line[1:]):
        if cur.get("s") == "carried" and prev.get("s") == "dropped" and arrival <= cur["t"] <= arrival + SPIKE_PICKUP_S:
            pick = (prev, cur)
            break
    if pick is None:
        return out
    prev, cur = pick
    t = float(cur["t"])
    out["spike_pickup"] = _r2(t)
    picker = cur.get("slot")
    if picker is None and prev.get("u") is not None and prev.get("v") is not None:
        spot = _px(prev["u"], prev["v"])
        best = None
        for p in blob.get("players") or ():
            slot = p["slot"] if isinstance(p, dict) else p
            if not rnd.alive(slot, t):
                continue
            pos = rnd.pos(slot, t)
            if pos is None:
                continue
            d = _dist_m(rnd, pos, spot)
            if best is None or d < best[0]:
                best = (d, slot)
        picker = best[1] if best else None
    if picker is None:
        out["got_spike"] = None
        return out
    out["got_spike"] = picker == c.by
    if out["got_spike"]:
        out["got_spike_planted"] = any(e.get("s") == "planted" and e["t"] > t for e in line)
    return out


def _signals(rnd, blob, sides, c: _Cast, enemy_eff: list[dict], cast_type: str) -> dict:
    """Stored facts the site's windows are cut from later (nothing is windowed here): when it landed, when its pieces
    were last seen, a flash's pop, the enemy hits and effect ends, a wall's `on` and a device's `off` spans, when a
    placeable armed, the ult line's drop, and for an ult the facts its table rule asks for."""
    ends = []
    for r in c.rows:
        if (r.get("k") == "ability" and r.get("kind") == "Projectile") or r.get("k") in ("flash", "nearsight"):
            continue
        e = [x for x in (r.get("t1"), r.get("gone")) if x is not None]
        if e and min(e) < rnd.t_end - _EPS:
            ends.append(min(e))
    pop = ((c.flash or {}).get("pop") or {}).get("t")
    on = sorted(([a, b] for r in c.rows for a, b in (r.get("on") or ())), key=lambda x: x[0])
    off = sorted(([a, b] for r in c.rows for a, b in (r.get("off") or ())), key=lambda x: x[0])
    ult_line = ((blob.get("player_state") or {}).get("ult") or {}).get(str(c.by)) or []
    drop = None
    for prev, cur in zip(ult_line, ult_line[1:]):
        if cur["n"] == 0 and prev["n"] > 0 and c.t - 12.0 <= cur["t"] <= c.t + _EPS:
            drop = cur["t"]
    s = {
        "landed": _r2(c.landed_t),
        "seen_end": _r2(max(ends)) if ends else None,
        "pop": _r2(pop),
        "hits": [[_r2(e["t0"]), _r2(e["t1"]), e["dur_known"]] for e in enemy_eff if e["how"] in ("blind", "nearsight")],
        "effect_ends": [[_r2(e["t1"]), e["dur_known"]] for e in enemy_eff if e["how"] not in ("damage", "reveal")],
        "on": [[_r2(a), _r2(b)] for a, b in on],
        "off": [[_r2(a), _r2(b)] for a, b in off],
        "armed": _r2(c.landed_t) if cast_type == "placeable" else None,
        "ult_drop": _r2(drop),
    }
    if (c.entry or {}).get("go_off"):   # when it was set off (Nanoswarm's swarm, Razorvine's patch)
        went = _go_off_t(c)
        s["go_off"] = _r2(went)
        # and when its hidden object was destroyed before that (the owner: worse than never setting it off)
        go = c.entry["go_off"]
        gone = [float(r["gone"]) for r in c.rows if r.get("gone") is not None
                and not re.search(go, f"{r.get('kind')}:{_code_name(r) or ''}")]
        s["destroyed"] = _r2(min(gone)) if gone and (went is None or min(gone) < went) else None
    if cast_type == "placeable":        # a sentinel's placed util turns off when its owner dies (the owner, 2026-10-24)
        s["owner_death"] = _r2(_death_after(rnd, c.by, c.t))
    if (c.entry or {}).get("rule") == "possessed":      # a camera: when its owner was looking through it
        s["possessed"] = sorted(([_r2(a), _r2(b)] for r in c.rows for a, b in (r.get("possessed") or ())),
                                key=lambda x: x[0])
    if cast_type != "ult":
        return s
    rule = (c.entry or {}).get("rule")
    if rule == "ult:omen":
        row = next((r for r in c.rows if _code_name(r) == "Wraith_X_GlobalTeleport_Intention"), None) or {}
        s["arrival"] = _r2(row.get("t1"))
        s["arrival_outcome"] = row.get("outcome")
        s.update(_omen_spike(rnd, blob, c, s["arrival"]))
    elif rule == "ult:thrash":
        row = next((r for r in c.rows if "RollyPolly" in (_code_name(r) or "")), None) or {}
        p = row.get("possessed")
        s["moved"] = _r2(p[0][0] if isinstance(p, list) and p else row.get("t"))
    elif rule == "ult:duel":
        k = next(((t, a, b) for t, a, b in sorted(rnd.kills) if c.t < t <= c.t + 12.0 and c.by in (a, b)), None)
        s["duel_end"] = _r2(k[0]) if k else None
        s["duel_won"] = (k[1] == c.by) if k else None
    elif rule == "ult:capture":
        ts = [e["t1"] for e in enemy_eff if e["how"] == "cocooned"]
        s["capture_end"] = _r2(max(ts)) if ts else None
    elif rule in ("ult:until_end", "ult:until_death"):
        s["ult_end"] = _r2(next((p["t"] for p in ult_line if p["t"] > c.t + _EPS and p["n"] == 0), None))
        s["death"] = _r2(next((t for t, a, b in sorted(rnd.kills) if t > c.t and b == c.by), None))
    return s


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


def _ult_weapon_kills(rnd, util, slot, agent, since=None, until=None) -> list[dict]:
    """The kills an ult that is a weapon made (ULT_WEAPONS), from `since` on: the killer's shot at the kill's moment
    was the ult's gun, or their last damage run on the victim, ending at the kill, was the ult, or (a "window"
    ult) any kill up to `until`, the ult's end."""
    proof = ULT_WEAPONS.get(agent)
    if proof is None:
        return []
    how, what = proof
    out = []
    for t, killer, victim in rnd.kills:
        if killer != slot or t > rnd.t_decided or (since is not None and t < since - _EPS):
            continue
        if how == "window":
            hit = since is not None and until is not None and t <= until + _EPS
        elif how == "shot":
            hit = any(r.get("k") == "shot" and r.get("by") == slot and abs(float(r["t"]) - t) <= ULT_SHOT_MATCH_S
                      and r.get("gun") == what for r in util)
        else:
            runs = [r for r in util if r.get("k") == "damage" and r.get("by") == slot and r.get("target") == victim
                    and float(r["t"]) <= t + ULT_SHOT_MATCH_S]
            last = max(runs, key=lambda r: float(r["t"])) if runs else None
            hit = last is not None and _code_name(last) == what \
                and abs(float(last.get("t1") or last["t"]) - t) <= ULT_SHOT_MATCH_S
        if hit:
            out.append({"t": _r2(t), "killer": killer, "victim": victim, "ours": True, "ev": "ult_weapon"})
    return out


def _with_ult_kills(linked: list[dict], ult: list[dict]) -> list[dict]:
    """One entry per kill: a kill the ult made itself replaces this cast's other evidence on it (a rocket's zone
    damage), so the tab and the coach count it once."""
    mine = {(k["t"], k["killer"], k["victim"]) for k in ult}
    return sorted([k for k in linked if (k["t"], k["killer"], k["victim"]) not in mine] + ult, key=lambda k: k["t"])


def _timed_ult_casts(rnd, sides, ps, ults, agents, contact, plant, first_i, util=()) -> list[dict]:
    """One cast per timed ult used this round (Chamber, Jett, Neon, Reyna: `_ults`' `use`), which leave no ability
    row to make a cast of: type ult, its window from the use's signals, no place and no effects of its own. An ult
    that is a gun (Chamber's) has its own kills (`_ult_weapon_kills`). `from_use` marks it, so the baseline doesn't
    count the use twice."""
    out = []
    for slot, u in sorted(ults.items(), key=lambda x: int(x[0])):
        use = (u or {}).get("use") or {}
        if use.get("t") is None:
            continue
        s, t = int(slot), float(use["t"])
        sig = use.get("signals") or {}
        stops = [x for x in (sig.get("ult_end"), sig.get("death")) if x is not None]
        t_end = min([*stops, rnd.t_decided])
        held = [p["n"] for p in ((ps or {}).get("ult") or {}).get(slot) or [] if p["t"] <= t and p["n"]]
        alive = _alive_counts(rnd, sides, t)
        out.append({
            "i": first_i + len(out), "by": s, "agent": agents.get(s), "t": _r2(t),
            "t_end": _r2(t_end), "u": None, "v": None, "names": [], "codes": [],
            "kinds": ["ult"], "side": sides.get(s), "type": "ult", "ability_id": use.get("ability_id"),
            "end_unknown": False, "phase": _phase(t, contact, plant), "after_decided": t > rnd.t_decided,
            "alive": [alive["attack"], alive["defense"]], "trigger": None, "enemies": [], "effects": [],
            "kills": _ult_weapon_kills(rnd, util, s, agents.get(s), since=t, until=t_end), "followed": None, "harm": [], "cover": None, "presence": None, "spike": None, "hold": None,
            "ult_points": held[-1] if held else None, "activation": None, "signals": dict(sig), "from_use": True,
        })
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
    # util still up after the plant counts, wherever it was thrown: `_spike_effect` checks each attempt's overlap
    spikes = {c.i: _spike_effect(rnd, sides, c, spike, effects[c.i]) if plant is not None and c.t_end >= plant
              else None for c in casts}
    _share_defuses(spikes, {c.i: c.t for c in casts})
    out_casts = []
    for c in casts:
        eff = effects[c.i]
        side = sides.get(c.by)
        enemy_eff = [e for e in eff if sides.get(e["target"]) not in (None, side)]
        trigger = set_off_t = set_off_kind = None
        if "device" in c.kinds:    # it fired, was broken, or first touched an enemy: whichever came first
            seen = [x for x in (_trigger(c), *(e["t0"] for e in enemy_eff)) if x is not None and x >= c.t - _EPS]
            trigger = min(seen) if seen else None
            set_off_t, set_off_kind = _set_off(rnd, c, enemy_eff, trigger)
        alive = _alive_counts(rnd, sides, c.t)
        cast_type = "ult" if "ult" in c.kinds else (c.entry or {}).get("type") or "other"
        out_casts.append({
            "i": c.i, "by": c.by, "agent": c.agent, "t": _r2(c.t), "t_end": _r2(c.t_end), "u": c.u, "v": c.v,
            "names": sorted(set(c.names)), "codes": sorted({r.get("code") for r in c.rows if r.get("code")}),
            "kinds": sorted(c.kinds), "side": side,
            "type": cast_type,
            "ability_id": f"{c.entry['agent']}/{c.entry['ability']}" if c.entry else None,
            "end_unknown": c.end_unknown,
            "phase": _phase(c.t, contact, plant), "after_decided": c.t > rnd.t_decided,
            "alive": [alive["attack"], alive["defense"]],
            "trigger": _r2(trigger),
            "set_off_t": _r2(set_off_t), "set_off_kind": set_off_kind,
            "rose": _rose(rnd, c, set_off_kind),
            "enemies": sorted({e["target"] for e in enemy_eff}),
            "effects": [{"target": e["target"], "how": e["how"], "t0": _r2(e["t0"]), "t1": _r2(e["t1"]),
                         "known": e["dur_known"]} for e in enemy_eff],
            "kills": _with_ult_kills(_link_kills(rnd, sides, c, eff, trigger), _ult_weapon_kills(
                rnd, blob.get("util") or (), c.by, agents.get(c.by) or c.agent, since=c.t) if cast_type == "ult" else []),
            "followed": _followed(rnd, blob, sides, c, eff),
            "harm": _harm(rnd, sides, c, eff),
            "cover": cover.get(c.i),
            "presence": presence[c.i],
            "spike": spikes[c.i],
            "hold": held.get(c.i),
            "ult_points": _ult_cost(ps, c),
            "activation": ((c.flash or {}).get("activation") or {}).get("targets_complete"),
            "signals": _signals(rnd, blob, sides, c, enemy_eff, cast_type),
        })
    ults = _ults(rnd, blob, sides, agents)
    out_casts.extend(_timed_ult_casts(rnd, sides, ps, ults, agents, contact, plant, len(out_casts),
                                      blob.get("util") or ()))
    has_causes = any(r.get("k") == "damage" and r.get("code") for r in blob.get("util") or ())
    return {
        "v": VERSION,
        "sides": {str(s): v for s, v in sides.items()},
        "agents": {str(s): agents.get(s) for s in sides},
        "contact": _r2(contact), "plant": _r2(plant), "defuse": _r2(defuse), "t_decided": _r2(rnd.t_decided),
        "kills": kills,
        # each slot's lives, [start, end | null]: who is alive when, revives included (the kills alone can't say)
        "lives": {str(s): [[_r2(a), None if b == math.inf else _r2(b)] for a, b in rnd.lives.get(s, ())]
                  for s in sides},
        "casts": out_casts,
        "heals": _heals(rnd, blob, sides),
        "unused": _unused(rnd, blob, sides, spike, agents),
        "ults": ults,
        "avail": {"heals": "heal" in {r.get("k") for r in blob.get("util") or ()} or bool((ps or {}).get("charges")),
                  "causes": has_causes, "charges": bool((ps or {}).get("charges")),
                  "ult": bool((ps or {}).get("ult")),
                  "friendly_status": any(r.get("friendly") for r in blob.get("util") or ()) or None,
                  "unknown": bool(holds) and not (holds or {}).get("error")},
    }
