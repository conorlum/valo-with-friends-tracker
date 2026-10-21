"""The optional per-round `player_state` subsection, version 1 (docs/superpowers/specs/2026-10-06-replay-player-state-design.md
sections 3-4; the plan's P02 and its amendments). Stdlib only: the upload worker copies app/replays.

    "player_state": {
      "version": 1,
      "vitals": {"<slot>": [{"t", "life", "hp", "sh", "mhp", "msh", ["q": "start"]}, ...]},
      "damage_taken": {"<slot>": [t, ...]},
      "spike": [{"t", "s", ["slot"], ["u", "v"]}, ...],
      "ult": {"<slot>": [{"t", "n"}, ...]},
      "orbs": [{"u", "v", "t0", "t1", "end", ["slot"], ["tries"]}, ...],
      "charges": {"<slot>": {"C" | "Q" | "E" | "X": [{"t", "n"}, ...]}}
    }

Every `t` is round seconds from InRound (format.py), negative in the buy phase. Slots are "0".."9" as JSON keys.

`vitals`: one event per confirmed hit (the damage notify's `LifeChangeEvents`) and, from revision 21, per heal of the
HP or armor section (the heal notify's `LifeChanges`: decision D4 said the replay had no heal records; it has them,
just not as AresAttributeSet properties, player_state_extract.py), each a full snapshot of the values AFTER the hit
or heal at `t`: `hp` and
`sh` (shield) current, `mhp` the life's measured max HP (100 in every observed first hit), `msh` its measured max
shield (the armor bought: Light 25, Heavy 50, Regen 25; 0 when none). `life` is the life index within the round (0,
then 1 after a revival). All four keys are required (the plan's amendment F12: combined HP + shield). `hp` and `mhp`
are numbers (`mhp` > 0); `sh` and `msh` are numbers, or both null when the shield isn't proven (never 0 for
unknown): HP is still carried and the bar is unavailable. An event missing a key, or with only one shield value,
is dropped. `q: "start"` marks a round-start seed, only from valid buy-phase evidence. An event isn't merged with
earlier ones: a snapshot stands alone.

`damage_taken`: the distinct times of confirmed positive damage on the slot (self and fall damage included, zero
dropped), sorted. Deduplication (by character, respawn number and life-change index) is the extractor's.

`spike`: state transitions in time order, `s` one of SPIKE_STATES. `carried` may lack `slot` (the state is known,
the carrier isn't: decision D5 fills it only where proven, from the last pickup to a plant, the planter). `u`/`v`
(0..10000) only where a recorded position exists (a dropped spike has one when the drop follows one death: that death's place). At equal `t`, a completed plant
supersedes planting/carried; otherwise the later source order wins (a later pickup supersedes an earlier drop);
two records of that winning state naming different slots contradict each other and the time ends `unknown`.

`ult` (2026-10-18): each slot's ult points, `n`, as the game replicates them: the value before the round (its `t` the
look-back start), then each change. `orbs`: the round's ult orbs, `u`/`v` where they lie, `t0` when they appeared,
`t1` when they went (null: there to the round's end), and `end`: "taken" (by `slot`, the capturer the game named),
"plant" or "round". `tries`: captures started on it and let go, [t, t1, slot] each (`t1` when let go, null if never
seen); a bad one is dropped.

`charges` (revision 21): each slot's ability charges held, `n`, per ability key: "C", "Q", "E" (the inventory's
GrenadeAbilitySlot, Ability1Slot, Ability2Slot) and "X" (UltimateSlot; no export has had one yet), only where the
export proves whose they are. Like `ult`, the value before the round (its `t` the look-back start) when known, then
each change; nothing is assumed before the first one. A bad point is dropped.

`validate` returns a normalized copy or None (unavailable: not a dict, an unsupported version, a part of the wrong
shape). A bad entry is dropped; an absurd count makes that slot's list (or the spike) unavailable. Missing parts are
unavailable, never zero health or a carried spike."""

from __future__ import annotations

import bisect
import math

VERSION = 1
SPIKE_STATES = ("unknown", "carried", "dropped", "planting", "planted", "defused", "detonated")
VITAL_KEYS = ("hp", "sh", "mhp", "msh")
NULLABLE_KEYS = frozenset({"sh", "msh"})    # shield not proven: null, together
QUALITIES = frozenset({"start"})
SLOTS = frozenset(str(s) for s in range(10))
UV_MAX = 10000
T_MIN, T_MAX = -200.0, 1000.0      # round seconds: the buy phase is negative; nothing lasts 1000 s
VALUE_MAX = 1000.0                 # HP or shield: far above any agent's (an overheal included)
LIFE_MAX = 63
MAX_EVENTS = 512                   # per slot per round: a round has tens of hits on one player
MAX_SPIKE = 128


def _num(value, lo: float, hi: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and lo <= value <= hi else None


def _count(diagnostics: dict | None, case: str) -> None:
    if diagnostics is not None:
        diagnostics[case] = diagnostics.get(case, 0) + 1


def _vital(event, diagnostics) -> dict | None:
    if not isinstance(event, dict):
        return None
    t = _num(event.get("t"), T_MIN, T_MAX)
    life = event.get("life")
    if t is None or isinstance(life, bool) or not isinstance(life, int) or not 0 <= life <= LIFE_MAX:
        return None
    out = {"t": t, "life": life}
    for key in VITAL_KEYS:
        if key not in event:
            return None
        if event[key] is None:
            if key not in NULLABLE_KEYS:
                return None
            out[key] = None
            continue
        value = _num(event[key], 0.0, VALUE_MAX)
        if value is None or (key == "mhp" and value <= 0.0):
            return None
        out[key] = value
    if (out["sh"] is None) != (out["msh"] is None):
        return None
    if "q" in event:
        if event["q"] not in QUALITIES:
            return None
        out["q"] = event["q"]
    return out


def _spike(entry) -> dict | None:
    if not isinstance(entry, dict) or entry.get("s") not in SPIKE_STATES:
        return None
    t = _num(entry.get("t"), T_MIN, T_MAX)
    if t is None:
        return None
    out = {"t": t, "s": entry["s"]}
    if entry.get("slot") is not None:
        slot = entry["slot"]
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot <= 9:
            return None
        out["slot"] = slot
    has_u, has_v = entry.get("u") is not None, entry.get("v") is not None
    if has_u or has_v:
        u, v = entry.get("u"), entry.get("v")
        if not all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= UV_MAX for x in (u, v)):
            return None
        out["u"], out["v"] = u, v
    return out


def _spike_order(entries: list[dict], diagnostics: dict | None = None) -> list[dict]:
    """Time order, source order within a time, except that a completed plant goes after any planting or carried
    entry of its own time (it supersedes them). When the entry that wins a time names a slot and another entry of
    that time and state names a different one, the records contradict each other: an `unknown` entry closes the
    time (never a guessed carrier) and the case is counted."""
    entries = sorted(entries, key=lambda e: e["t"])
    out: list[dict] = []
    i = 0
    while i < len(entries):
        j = i
        while j < len(entries) and entries[j]["t"] == entries[i]["t"]:
            j += 1
        group = entries[i:j]
        if any(e["s"] == "planted" for e in group):
            group = sorted(group, key=lambda e: 0 if e["s"] in ("planting", "carried") else 1)
        last = group[-1]
        if "slot" in last and any(e["s"] == last["s"] and "slot" in e and e["slot"] != last["slot"] for e in group):
            _count(diagnostics, "spike: contradictory records at one time (unknown)")
            group = [*group, {"t": last["t"], "s": "unknown"}]
        out += group
        i = j
    return out


ULT_MAX = 20
MAX_ORBS = 16           # per round: a map has two or three spots
ORB_ENDS = frozenset({"taken", "plant", "round"})
CHARGE_KEYS = ("C", "Q", "E", "X")
CHARGE_MAX = 9


def _whole(value, lo: int, hi: int) -> int | None:
    """An integer in [lo, hi], or None. A whole float (3.0) counts as its integer: the viewer's JSON can't tell them
    apart, so both sides read it alike (the 2026-10-18 review's parity case)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        value = int(value)
    return value if lo <= value <= hi else None


def _ult_point(entry) -> dict | None:
    if not isinstance(entry, dict):
        return None
    t, n = _num(entry.get("t"), T_MIN, T_MAX), _whole(entry.get("n"), 0, ULT_MAX)
    if t is None or n is None:
        return None
    return {"t": t, "n": n}


def _charge_point(entry) -> dict | None:
    if not isinstance(entry, dict):
        return None
    t, n = _num(entry.get("t"), T_MIN, T_MAX), _whole(entry.get("n"), 0, CHARGE_MAX)
    if t is None or n is None:
        return None
    return {"t": t, "n": n}


def _orb(entry) -> dict | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("end"), str) or entry["end"] not in ORB_ENDS:
        return None
    u, v = _whole(entry.get("u"), 0, UV_MAX), _whole(entry.get("v"), 0, UV_MAX)
    if u is None or v is None:
        return None
    t0 = _num(entry.get("t0"), T_MIN, T_MAX)
    t1 = None if entry.get("t1") is None else _num(entry.get("t1"), T_MIN, T_MAX)
    if t0 is None or (entry.get("t1") is not None and (t1 is None or t1 < t0)):
        return None
    out = {"u": u, "v": v, "t0": t0, "t1": t1, "end": entry["end"]}
    if entry["end"] == "taken" and entry.get("slot") is not None:
        slot = _whole(entry["slot"], 0, 9)
        if slot is None:
            return None
        out["slot"] = slot
    if isinstance(entry.get("tries"), list):
        tries = [kept for kept in map(_orb_try, entry["tries"]) if kept is not None]
        if tries:
            out["tries"] = tries
    return out


def _orb_try(entry) -> list | None:
    if not isinstance(entry, list) or len(entry) != 3:
        return None
    t, slot = _num(entry[0], T_MIN, T_MAX), _whole(entry[2], 0, 9)
    t1 = None if entry[1] is None else _num(entry[1], T_MIN, T_MAX)
    if t is None or slot is None or (entry[1] is not None and (t1 is None or t1 < t)):
        return None
    return [t, t1, slot]


def validate(obj, diagnostics: dict | None = None) -> dict | None:
    """A normalized copy of a `player_state` subsection, or None when it is unavailable. `diagnostics`, when
    given, counts what was dropped."""
    if not isinstance(obj, dict) or isinstance(obj.get("version"), bool) or obj.get("version") != VERSION:
        return None
    for key, kind in (("vitals", dict), ("damage_taken", dict), ("spike", list), ("ult", dict), ("orbs", list),
                      ("charges", dict)):
        if key in obj and not isinstance(obj[key], kind):
            return None
    out: dict = {"version": VERSION}
    if "vitals" in obj:
        vitals = {}
        for slot, events in obj["vitals"].items():
            if slot not in SLOTS or not isinstance(events, list):
                _count(diagnostics, "vitals: bad slot")
                continue
            if len(events) > MAX_EVENTS:
                _count(diagnostics, "vitals: too many events (slot unavailable)")
                continue
            kept = []
            for event in events:
                checked = _vital(event, diagnostics)
                if checked is None:
                    _count(diagnostics, "vitals: bad event")
                else:
                    kept.append(checked)
            vitals[slot] = sorted(kept, key=lambda e: e["t"])      # stable: source order within a time
        out["vitals"] = vitals
    if "damage_taken" in obj:
        damage = {}
        for slot, times in obj["damage_taken"].items():
            if slot not in SLOTS or not isinstance(times, list):
                _count(diagnostics, "damage_taken: bad slot")
                continue
            if len(times) > MAX_EVENTS:
                _count(diagnostics, "damage_taken: too many times (slot unavailable)")
                continue
            kept = {t for t in (_num(x, T_MIN, T_MAX) for x in times) if t is not None}
            if len(kept) < len(times):
                _count(diagnostics, "damage_taken: bad or repeated time")
            damage[slot] = sorted(kept)
        out["damage_taken"] = damage
    if "spike" in obj:
        if len(obj["spike"]) > MAX_SPIKE:
            _count(diagnostics, "spike: too many transitions (unavailable)")
        else:
            kept = []
            for entry in obj["spike"]:
                checked = _spike(entry)
                if checked is None:
                    _count(diagnostics, "spike: bad transition")
                else:
                    kept.append(checked)
            out["spike"] = _spike_order(kept, diagnostics)
    if "ult" in obj:
        ult = {}
        for slot, line in obj["ult"].items():
            if slot not in SLOTS or not isinstance(line, list) or len(line) > MAX_EVENTS:
                _count(diagnostics, "ult: bad slot")
                continue
            kept = [p for p in (_ult_point(e) for e in line) if p is not None]
            if len(kept) < len(line):
                _count(diagnostics, "ult: bad point")
            ult[slot] = sorted(kept, key=lambda e: e["t"])
        out["ult"] = ult
    if "orbs" in obj:
        if len(obj["orbs"]) > MAX_ORBS:
            _count(diagnostics, "orbs: too many (unavailable)")
        else:
            kept = [o for o in (_orb(e) for e in obj["orbs"]) if o is not None]
            if len(kept) < len(obj["orbs"]):
                _count(diagnostics, "orbs: bad orb")
            out["orbs"] = sorted(kept, key=lambda o: o["t0"])
    if "charges" in obj:
        charges = {}
        for slot, keys in obj["charges"].items():
            if slot not in SLOTS or not isinstance(keys, dict):
                _count(diagnostics, "charges: bad slot")
                continue
            lines = {}
            for key, line in keys.items():
                if key not in CHARGE_KEYS or not isinstance(line, list) or len(line) > MAX_EVENTS:
                    _count(diagnostics, "charges: bad key")
                    continue
                kept = [p for p in (_charge_point(e) for e in line) if p is not None]
                if len(kept) < len(line):
                    _count(diagnostics, "charges: bad point")
                lines[key] = sorted(kept, key=lambda e: e["t"])
            charges[slot] = lines
        out["charges"] = charges
    return out


# ---------------------------------------------------------------- queries (state at t: right-continuous, never future)


def vitals_at(ps: dict | None, slot: int, t: float, life: int | None = None) -> dict | None:
    """The slot's latest vitals event at or before `t`, or None. With `life`, only that life's events: a revived
    player doesn't carry an earlier life's values. Without it, the latest event's own life is assumed current."""
    events = ((ps or {}).get("vitals") or {}).get(str(slot)) or []
    if life is not None:
        events = [e for e in events if e["life"] == life]
    i = bisect.bisect_right([e["t"] for e in events], t)
    return events[i - 1] if i else None


def damaged_by(ps: dict | None, slot: int, t: float) -> bool:
    """Whether the slot took confirmed damage at or before `t` this round."""
    times = ((ps or {}).get("damage_taken") or {}).get(str(slot)) or []
    return bool(times) and times[0] <= t


def spike_at(ps: dict | None, t: float) -> dict | None:
    """The spike's state at `t`: the effective transition at or before it, or None (unknown)."""
    spike = (ps or {}).get("spike") or []
    i = bisect.bisect_right([e["t"] for e in spike], t)
    return spike[i - 1] if i else None


def health_pct(event: dict | None) -> float | None:
    """100 * (hp + sh) / (mhp + msh), clamped to 0..100, from the measured maxima (never a fixed 150; R2). None
    (the unavailable marker) when any value is missing or null, or the denominator is 0."""
    if not event or any(event.get(k) is None for k in VITAL_KEYS):
        return None
    total = event["mhp"] + event["msh"]
    if total <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * (event["hp"] + event["sh"]) / total))
