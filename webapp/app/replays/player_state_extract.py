"""Health and spike state from a parser export, for each round blob's `player_state` (player_state.py; the
replay player-state plan's P03 and its amendments; decisions D4 and D5). Stdlib only: the upload worker copies
app/replays. `condense()` builds the subsection from rows already in its stream, so the full and the streaming
loaders give the same result; `attach_extras` then adds what only the extras pass proves (the planter).

What the export proves (replay 7a278f4b, W3 EVIDENCE B, C and E):

- **Health.** Each damage notify on a player's pawn carries `LifeChangeEvents`, a replicated array of sections,
  each with the component it changed, its value after the hit (`LifeResult`), the change (`DeltaLife`) and an
  alive bit; the sections' changes sum to `DamageTaken` on 934 of 934 hits. One section is the pawn's HP. It is
  never named, so it is learned: the section that every lethal hit on that pawn leaves dead (Clove's extra
  sections sometimes also report dead; only the HP section is in all). Another is the armor, when the player has
  one: its component is the armor item's actor guid + 2, and the item's archetype gives its capacity (the max
  shield). A hit with no armor section has no armor (a depleted armor keeps its section at 0, and armored players
  had one on every hit, falls included). Max HP has no field: MAX_HP is the measured pre-hit value (below).
- **No heals.** The export has no heal, regen or overheal rows (D4): a vitals event is the state after a hit and
  stays until the next one; a rise seen at the next hit is that hit's snapshot. Nothing is calculated from
  outgoing damage and nothing is invented between hits.
- **Damage taken.** Every damage notify on a player's own pawn with positive `DamageTaken`: self, friendly, fall
  and spike damage included, zero dropped. One hit is one (victim pawn, `VictimRespawnNumber`,
  `LifeChangeEventIndex`); a repeat of that key is the same hit and counts once.
- **Spike (D5, option A).** The game state's `BombState` gives exact transition times: 2 dropped, 3 carried,
  4 planted, 5 detonated, 6 defused; 1 (spawned) and 0 (round over) are `unknown`. Who carries is proven only
  from the last pickup to a completed plant, by the planter (the spike can't change hands without a drop); the
  first carrier, any other carrier and a dropped spike's place are not decoded, so they stay absent. Never a
  pickup by distance.

Diagnostics are anonymous counts (no names, Subjects or guids) for the condense report."""

from __future__ import annotations

import base64
import binascii
import math
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from app.replays.contract import GAME_STATE_PATH, RPC_DAMAGE

# W3 EVIDENCE B: the HP section's value before a slot's first hit of a round was 100 on 202 of 202; the export
# has no MaxLife field. Measured, not a game constant typed in.
MAX_HP = 100.0
# The armor item's archetype -> its capacity, the max pre-hit value seen on its section (Light 18/18 first
# values, Heavy 113/113 bought, Plasma = Regen Shield 31/31).
ARMOR_CAPACITY = {"Default__LightArmorItem_C": 25.0, "Default__HeavyArmorItem_C": 50.0,
                  "Default__PlasmaArmorItem_C": 25.0}
ARMOR_SECTION_OFFSET = 2          # an armor section's component = the armor item's actor guid + 2
BOMB_STATES = {2: "dropped", 3: "carried", 4: "planted", 5: "detonated", 6: "defused"}
PLANT_MATCH_S = 1.0               # the BombState plant and the planted spike's spawn (12 ms apart in R1)
SUM_TOLERANCE = 0.01              # sections' changes vs DamageTaken (float32 values)
VALUE_TOLERANCE = 0.01
PROVEN_PLANTER = frozenset({"instigator", "planted"})   # extras' owner_by values that name the planter
SPIKE_LOOKBACK_MS = 200_000       # player_state.T_MIN: a round's transitions start at most this long before it

# LifeChangeEvents handles (the parser's export of the struct).
H_COMPONENT, H_RESULT, H_DELTA, H_ALIVE = 11, 12, 13, 14


class _Bits:
    def __init__(self, data: bytes, count: int):
        self.data, self.count, self.pos = data, count, 0

    def read(self, size: int) -> int:
        if size < 0 or self.pos + size > self.count:
            raise ValueError("past the end")
        value = 0
        for i in range(size):
            value |= ((self.data[(self.pos + i) >> 3] >> ((self.pos + i) & 7)) & 1) << i
        self.pos += size
        return value

    def packed(self) -> int:
        value, shift = 0, 0
        while True:
            byte = self.read(8)
            value |= (byte >> 1) << shift
            shift += 7
            if not byte & 1:
                return value
            if shift > 63:
                raise ValueError("packed int too long")


def _packed_in(raw: int, size: int) -> int:
    return _Bits(raw.to_bytes((size + 7) // 8, "little"), size).packed()


def decode_life_changes(blob) -> list[dict] | None:
    """The sections of a damage notify's `LifeChangeEvents` ({BitCount, Data}): [{g, res, d, alive}], `alive`
    None when the section doesn't carry it, or None when the blob can't be read whole. Bits are LSB-first; an
    intpacked count, then per element an intpacked index and (intpacked handle, intpacked size, size bits)
    fields until handle 0."""
    if not isinstance(blob, dict) or not isinstance(blob.get("Data"), str):
        return None
    count = blob.get("BitCount")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        return None
    try:
        data = base64.b64decode(blob["Data"], validate=True)
        if count > 8 * len(data):
            return None
        bits = _Bits(data, count)
        out = []
        for _ in range(bits.packed()):
            bits.packed()                       # the element's index
            fields = {}
            while True:
                handle = bits.packed()
                if handle == 0:
                    break
                size = bits.packed()
                fields[handle] = (size, bits.read(size))
            if H_COMPONENT not in fields or H_RESULT not in fields or H_DELTA not in fields:
                return None
            if fields[H_RESULT][0] != 32 or fields[H_DELTA][0] != 32:
                return None
            section = {"g": _packed_in(fields[H_COMPONENT][1], fields[H_COMPONENT][0]),
                       "res": struct.unpack("<f", struct.pack("<I", fields[H_RESULT][1]))[0],
                       "d": struct.unpack("<f", struct.pack("<I", fields[H_DELTA][1]))[0],
                       "alive": fields[H_ALIVE][1] if H_ALIVE in fields and fields[H_ALIVE][0] == 1 else None}
            if not (math.isfinite(section["res"]) and math.isfinite(section["d"])):
                return None
            out.append(section)
        return out
    except (ValueError, binascii.Error, struct.error):
        return None


@dataclass
class _Hit:
    t_ms: int
    order: int
    slot: int
    pawn: int
    taken: float
    sections: list[dict] | None       # None: no LifeChangeEvents, or unreadable


@dataclass
class PlayerStateInputs:
    """What `read_player_state` found over the whole export, before it is cut into rounds."""
    hits: list[_Hit] = field(default_factory=list)
    hp_section: dict[int, int] = field(default_factory=dict)            # pawn -> its HP section's component
    armor: dict[int, list[tuple[int, float]]] = field(default_factory=dict)  # actor guid -> [(spawn ms, capacity)]
    spike: list[tuple[int, str]] = field(default_factory=list)          # (ms, state), changes only
    has_life_changes: bool = False
    has_damage: bool = False
    has_spike: bool = False
    diagnostics: Counter = field(default_factory=Counter)

    def capacity(self, section: int, t_ms: int) -> float | None:
        spawns = [cap for at, cap in self.armor.get(section - ARMOR_SECTION_OFFSET, []) if at <= t_ms]
        return spawns[-1] if spawns else None


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def read_player_state(export, players) -> PlayerStateInputs:
    """One pass over the export's events (the rows the condenser already keeps): damage notifies on players' own
    pawns, armor items' spawns and the game state's BombState."""
    out = PlayerStateInputs()
    seen: set[tuple[int, int, int]] = set()
    lethal: dict[int, list[set[int]]] = defaultdict(list)
    bomb: list[tuple[int, int]] = []
    for row in export.events:
        data = row.data
        kind = data.get("type")
        if kind == "actor_spawned":
            capacity = ARMOR_CAPACITY.get(str(data.get("archetype_path") or ""))
            if capacity is not None:
                out.armor.setdefault(int(data.get("actor_net_guid") or 0), []).append((row.time_ms, capacity))
            continue
        if kind == "export_group_received" and data.get("export_group_path") == GAME_STATE_PATH:
            payload = data.get("payload")
            if isinstance(payload, dict) and "BombState" in payload:
                value = payload["BombState"]
                if isinstance(value, int) and not isinstance(value, bool):
                    bomb.append((row.time_ms, value))
                else:
                    out.diagnostics["spike: unreadable BombState"] += 1
            continue
        if kind != "rpc_received" or data.get("function_name") not in RPC_DAMAGE:
            continue
        payload = data.get("payload")
        if not isinstance(payload, dict):
            continue
        pawn = int(payload.get("Character") or 0)
        slot = players.pawn_slot.get(pawn)
        if slot is None:
            continue                     # an object, or a possessed pawn: not a player's health
        out.has_damage = True
        taken = _number(payload.get("DamageTaken"))
        if taken is None or taken <= 0:
            out.diagnostics["damage: zero damage (dropped)"] += 1
            continue
        respawn, index = payload.get("VictimRespawnNumber"), payload.get("LifeChangeEventIndex")
        if isinstance(respawn, int) and isinstance(index, int):
            key = (pawn, respawn, index)
            if key in seen:
                out.diagnostics["damage: repeated hit (same victim, respawn, index)"] += 1
                continue
            seen.add(key)
        else:
            out.diagnostics["damage: no hit identity (kept, not deduplicated)"] += 1
        sections = None
        if "LifeChangeEvents" in payload:
            out.has_life_changes = True
            sections = decode_life_changes(payload["LifeChangeEvents"])
            if sections is None:
                out.diagnostics["vitals: undecodable LifeChangeEvents"] += 1
            elif abs(sum(-s["d"] for s in sections) - taken) > SUM_TOLERANCE:
                out.diagnostics["vitals: sections don't sum to DamageTaken"] += 1
                sections = None
        out.hits.append(_Hit(row.time_ms, row.index, slot, pawn, taken, sections))
        if sections is not None and (payload.get("DamageKilledTarget") is True
                                     or payload.get("AliveAfterDamage") is False):
            lethal[pawn].append({s["g"] for s in sections if s["alive"] == 0})
    for pawn, sets in lethal.items():
        armor_sections = {g for g in set.union(*sets) if g - ARMOR_SECTION_OFFSET in out.armor}
        common = set.intersection(*sets) - armor_sections
        if len(common) == 1:
            out.hp_section[pawn] = common.pop()
        else:
            out.diagnostics["vitals: HP section unresolved (lethal hits disagree)"] += 1
    for pawn in sorted({h.pawn for h in out.hits if h.sections is not None} - set(lethal)):
        out.diagnostics["vitals: HP section unresolved (owner never died)"] += 1
    out.hits.sort(key=lambda h: (h.t_ms, h.order))
    bomb.sort(key=lambda item: item[0])
    out.has_spike = bool(bomb)
    for t_ms, value in bomb:
        state = BOMB_STATES.get(value)
        if state is None:
            if value not in (0, 1):
                out.diagnostics["spike: unknown BombState value"] += 1
            state = "unknown"
        out.spike.append((t_ms, state))       # repeats collapse per round (round_player_state)
    return out


def _seconds(t_ms: int, start: int) -> float:
    return round((t_ms - start) / 1000.0, 3)


def _vital(hit: _Hit, hp_section: int, inputs: PlayerStateInputs, life_armor: dict, life_key) -> dict | None:
    """The snapshot after `hit`, or None when its HP section is missing. `life_armor[life_key]` remembers this
    life's armor (capacity, last value) for hits that don't carry its section."""
    diagnostics = inputs.diagnostics
    hp = next((s for s in hit.sections if s["g"] == hp_section), None)
    if hp is None:
        diagnostics["vitals: hit without the HP section"] += 1
        return None
    if hp["res"] - hp["d"] > MAX_HP + VALUE_TOLERANCE:
        # Phoenix's ult: the "lethal" hit returns him at 100 (LifeResult 100, alive). The snapshot is still exact.
        diagnostics["vitals: HP above max before the hit (a respawn)"] += 1
    if hp["res"] > MAX_HP + VALUE_TOLERANCE:
        diagnostics["vitals: HP above the measured max"] += 1
    armors, unknown = [], False
    for s in hit.sections:
        if s["g"] == hp_section:
            continue
        capacity = inputs.capacity(s["g"], hit.t_ms)
        if capacity is not None:
            armors.append((s, capacity))
        elif abs(s["d"]) > VALUE_TOLERANCE:
            unknown = True
    sh: float | None
    msh: float | None
    if unknown:
        diagnostics["shield: an unknown section took damage"] += 1
        sh = msh = None
    elif len(armors) > 1:
        diagnostics["shield: two armor sections"] += 1
        sh = msh = None
    elif armors:
        section, capacity = armors[0]
        if section["res"] > capacity + VALUE_TOLERANCE or section["res"] < -VALUE_TOLERANCE:
            diagnostics["shield: above its armor's capacity"] += 1
            sh = msh = None
        else:
            sh, msh = max(0.0, section["res"]), capacity
            life_armor[life_key] = (capacity, sh)
    elif life_key in life_armor:
        capacity, last = life_armor[life_key]
        if last > VALUE_TOLERANCE:
            diagnostics["shield: armor section missing while shield > 0"] += 1
            sh = msh = None
        else:
            sh, msh = 0.0, capacity
    else:
        sh, msh = 0.0, 0.0
    return {"hp": round(max(0.0, hp["res"]), 3), "sh": None if sh is None else round(sh, 3), "mhp": MAX_HP,
            "msh": msh}


def round_player_state(inputs: PlayerStateInputs, start: int, end: int, prev_end: int | None, life_of) -> dict | None:
    """One round's `player_state` (format version 1), or None when the export has none of its sources.

    `life_of(slot, t_ms)` -> the life index (the round's alive interval holding `t_ms`) or None. Hits count from
    InRound to the window's end, like the blob's other rows; spike transitions from after the previous round's
    window (the buy phase, negative times). A part is present when the export has its source at all, so an empty
    part means "none this round", and a missing one "never decoded"."""
    if not (inputs.has_damage or inputs.has_spike):
        return None
    out: dict = {"version": 1}
    hits = [h for h in inputs.hits if start <= h.t_ms <= end]
    if inputs.has_life_changes:
        vitals: dict[str, list] = defaultdict(list)
        life_armor: dict = {}
        for hit in hits:
            hp_section = inputs.hp_section.get(hit.pawn)
            if hit.sections is None or hp_section is None:
                continue
            life = life_of(hit.slot, hit.t_ms)
            if life is None:
                inputs.diagnostics["vitals: hit outside a life"] += 1
                continue
            values = _vital(hit, hp_section, inputs, life_armor, (hit.slot, life))
            if values is not None:
                vitals[str(hit.slot)].append({"t": _seconds(hit.t_ms, start), "life": life, **values})
        out["vitals"] = dict(sorted(vitals.items(), key=lambda kv: int(kv[0])))
    if inputs.has_damage:
        taken: dict[str, set] = defaultdict(set)
        for hit in hits:
            taken[str(hit.slot)].add(_seconds(hit.t_ms, start))
        out["damage_taken"] = {slot: sorted(times) for slot, times in sorted(taken.items(), key=lambda kv: int(kv[0]))}
    if inputs.has_spike:
        lo = max(prev_end if prev_end is not None else -math.inf, start - SPIKE_LOOKBACK_MS)
        spike = []
        for t_ms, state in inputs.spike:
            if lo < t_ms <= end and (not spike or spike[-1]["s"] != state):
                spike.append({"t": _seconds(t_ms, start), "s": state})
        out["spike"] = spike
    return out


def attribute_planter(state: dict | None, util: list[dict], diagnostics: Counter | dict) -> None:
    """D5: the planter carried the spike from its last pickup to the plant. In place on a round's `player_state`:
    the `carried` transition right before the round's one `planted` transition gets the planter's slot, and the
    `planted` one the spike's place, both from the planted spike's `ability` row (extras: `by` with an
    `owner_by` that names the planter; `u`/`v` its spawn point) within PLANT_MATCH_S of the plant. Anything less
    proven is left absent and counted."""
    spike = (state or {}).get("spike")
    if not spike:
        return

    def count(case: str) -> None:
        diagnostics[case] = diagnostics.get(case, 0) + 1

    planted = [i for i, e in enumerate(spike) if e["s"] == "planted"]
    if len(planted) != 1:
        if planted:
            count("spike: conflicting plants (not attributed)")
        return
    i = planted[0]
    rows = [u for u in util if u.get("k") == "ability" and u.get("kind") == "Bomb"
            and isinstance(u.get("t"), (int, float)) and abs(u["t"] - spike[i]["t"]) <= PLANT_MATCH_S]
    if len(rows) != 1:
        count("spike: no Bomb row at the plant" if not rows else "spike: conflicting plants (not attributed)")
        return
    bomb = rows[0]
    if isinstance(bomb.get("u"), int) and isinstance(bomb.get("v"), int):
        spike[i]["u"], spike[i]["v"] = bomb["u"], bomb["v"]
    planter = bomb.get("by")
    if not isinstance(planter, int) or isinstance(planter, bool) or bomb.get("owner_by") not in PROVEN_PLANTER:
        count("spike: planter not proven")
        return
    if i == 0 or spike[i - 1]["s"] != "carried":
        count("spike: no pickup right before the plant")
        return
    spike[i - 1]["slot"] = planter
    count("spike: planter attributed")
