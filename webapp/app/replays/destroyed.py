"""Destroyed devices: an own-side device row whose object was shot (`gone`, extras.py) before anything set it off.

Stdlib only (the worker copies app/replays). The set-off rules copy Critical Util's (app/critutil/extract.py:
`_trigger_kind`, `_effects`, `_set_off`), so a device counts as destroyed here exactly when Critical Util calls it
"broken": its own signals (fx, raised, an Alarmbot's chase yaw) or an enemy it touched came first, and "fired" wins a
tie. `ALARMBOT_YAW_WINDOW_S` and `_EPS` are copies; a test holds them equal to Critical Util's."""

from __future__ import annotations

ALARMBOT_YAW_WINDOW_S = 1.5
_EPS = 0.01
_EFFECT_TAIL_S = 1.0            # extract._effects: a row names a cast up to this long after the cast's end
_PAIR_T1_S = 0.05               # a trip's two anchors close within this of each other
_TRIP, _SECOND = "4_TripWire", "4_TripWire_SecondWire"

# (code, name) of an ability row -> the device it is. A trip's SecondWire is its far anchor (one trip, paired below).
# Names from real data (facts of the Info/util signals build) and app/critutil/abilities.json (the sound sensor).
DESTROYABLE: dict[tuple[str, str], str] = {
    ("Gumshoe", _TRIP): "trip",
    ("Gumshoe", _SECOND): "trip",
    ("Gumshoe", "E_PossessableCamera"): "camera",
    ("Killjoy", "Q_StealthAlarmbot"): "alarmbot",
    ("Killjoy", "E_Turret"): "turret",
    ("Cable", "Q_SoundSensor"): "sound_sensor",
    ("BountyHunter", "E_Divebomb"): "haunt",
    ("BountyHunter", "E_LoSReveal_Source_Reactivate"): "haunt",
    ("Hunter", "Q_SonarBolt"): "recon",
    ("Hunter", "Q_RevealBolt"): "recon",
}

# The names a device's effects on a player carry (a recon's ping, a sound sensor's fissure) besides its own rows'.
_EFFECT_NAMES = {
    "recon": {"Q_SonarPing"},
    "sound_sensor": {"Q_SoundSensor_Fissure"},
}


class _Device:
    def __init__(self, device: str, rows: list[dict]):
        self.device, self.rows = device, rows
        main = rows[0]
        self.owner = main.get("by")
        self.code = main.get("code")
        self.t = min(float(r["t"]) for r in rows)
        self.ids = {r["id"] for r in rows if r.get("id") is not None}
        self.names = {r.get("name") for r in rows} | _EFFECT_NAMES.get(device, set())
        gone = [float(r["gone"]) for r in rows if r.get("gone") is not None]
        self.gone = min(gone) if gone else None
        ends = [float(r["t1"]) for r in rows if r.get("t1") is not None]
        self.end = max(ends + ([self.gone] if self.gone is not None else [])) if ends or gone else None


def _is_device_row(r) -> bool:
    return r.get("k") == "ability" and r.get("t") is not None and (r.get("code"), r.get("name")) in DESTROYABLE


def _pair_trips(rows: list[dict]) -> list[_Device]:
    """Devices from the device rows. A main `4_TripWire` and its `4_TripWire_SecondWire` are one trip: paired by the
    main row's `end` when it has one, else by owner, close `t1` and nearest id. Any other row is a device alone."""
    out: list[_Device] = []
    mains = sorted((r for r in rows if r["name"] == _TRIP), key=lambda r: (r.get("id") or 0, float(r["t"])))
    seconds = sorted((r for r in rows if r["name"] == _SECOND), key=lambda r: (r.get("id") or 0, float(r["t"])))
    taken: set[int] = set()
    for m in mains:
        pick = None
        end = m.get("end")
        if isinstance(end, list) and len(end) >= 2:
            pick = next((s for s in seconds if id(s) not in taken and s.get("by") == m.get("by")
                         and abs(s["u"] - end[0]) <= 1 and abs(s["v"] - end[1]) <= 1), None)
        if pick is None:
            def close(s):
                a, b = (m.get("t1"), s.get("t1")) if m.get("t1") is not None and s.get("t1") is not None \
                    else (m["t"], s["t"])
                return abs(float(a) - float(b)) <= _PAIR_T1_S
            near = [s for s in seconds if id(s) not in taken and s.get("by") == m.get("by") and close(s)]
            if near:
                pick = min(near, key=lambda s: (abs((s.get("id") or 0) - (m.get("id") or 0)), s.get("id") or 0))
        if pick is not None:
            taken.add(id(pick))
            out.append(_Device("trip", [m, pick]))
        else:
            out.append(_Device("trip", [m]))
    for s in seconds:
        if id(s) not in taken:
            out.append(_Device("trip", [s]))
    for r in rows:
        if r["name"] not in (_TRIP, _SECOND):
            out.append(_Device(DESTROYABLE[(r["code"], r["name"])], [r]))
    return out


def _own_signal(dev: _Device) -> float | None:
    """The earliest of the device's own firing signals (critutil `_trigger_kind`, "fired" ones): fx, raised, an
    Alarmbot's later yaw near its close."""
    seen = []
    for r in dev.rows:
        fx = r.get("fx")
        if isinstance(fx, (int, float)):
            seen.append(float(fx))
        elif isinstance(fx, list) and fx:
            first = fx[0]
            seen.append(float(first[0] if isinstance(first, list) else first))
        if isinstance(r.get("raised"), list) and r["raised"]:
            seen.append(float(r["raised"][0]))
        yaws = r.get("yaws")
        if r.get("code") == "Killjoy" and r.get("name") == "Q_StealthAlarmbot" and r.get("t1") is not None \
                and isinstance(yaws, list):
            later = [float(y[0]) for y in yaws[1:] if isinstance(y, list) and y
                     and float(r["t1"]) - ALARMBOT_YAW_WINDOW_S <= float(y[0]) <= float(r["t1"]) + _EPS]
            if later:
                seen.append(later[0])
    return min(seen) if seen else None


def _touches(devices: list[_Device], util, side_of) -> dict[int, float]:
    """Each device's first touch on an enemy: the earliest status/reveal row on an enemy of its owner attributed to
    it the way critutil `_effects` attributes a row to a cast: by `id`, else by caster and the row's name inside the
    device's life (+1 s), else by the owner's live device of the row's code; several: the one placed latest."""
    by_id = {i: d for d in devices for i in d.ids}
    first: dict[int, float] = {}
    for r in util:
        if r.get("k") not in ("reveal", "status") or r.get("target") is None or r.get("t") is None:
            continue
        t = float(r["t"])
        dev = by_id.get(r["id"]) if r.get("id") is not None else None
        if dev is None:
            name = r.get("name")

            def live(d):
                end = d.end if d.end is not None else float("inf")
                return d.owner == r.get("by") and d.t - _EPS <= t <= end + _EFFECT_TAIL_S
            cands = [d for d in devices if live(d) and (not name or name in d.names)]
            if not cands and r.get("code"):
                cands = [d for d in devices if live(d) and d.code == r["code"]]
            if not cands:
                continue
            dev = max(cands, key=lambda d: d.t)
        if side_of.get(r["target"]) in (None, side_of.get(dev.owner)):
            continue
        first[id(dev)] = min(first.get(id(dev), t), t)
    return first


def destroyed_devices(util_rows, side_of: dict[int, str], t_decided: float) -> list[dict]:
    """Devices shot before they were set off, by owner: `{"t": gone, "owner": slot, "side", "device", "u", "v", "z",
    "id"}`, sorted by t. `side_of` maps a slot to its side; a device whose owner it doesn't know is skipped. `z` is
    the row's own, None when it has none. A trip's point is the midpoint of its two anchors."""
    util = list(util_rows or ())
    rows = [r for r in util if _is_device_row(r) and r.get("by") in side_of]
    devices = _pair_trips(rows)
    touched = _touches(devices, util, side_of)
    out = []
    for dev in devices:
        if dev.gone is None or not dev.gone < t_decided:
            continue
        fired = [x for x in (_own_signal(dev), touched.get(id(dev))) if x is not None]
        if fired and min(fired) <= dev.gone:
            continue                    # set off first; a tie is "fired" (critutil's rule)
        main = dev.rows[0]
        if len(dev.rows) == 2:
            a, b = dev.rows
            u, v = (a["u"] + b["u"]) / 2, (a["v"] + b["v"]) / 2
            z = a.get("z") if a.get("z") is not None else b.get("z")
        else:
            end = main.get("end") if main.get("name") == _TRIP else None
            if isinstance(end, list) and len(end) >= 2:
                u, v = (main["u"] + end[0]) / 2, (main["v"] + end[1]) / 2
            else:
                u, v = main.get("u"), main.get("v")
            z = main.get("z")
        out.append({"t": dev.gone, "owner": dev.owner, "side": side_of[dev.owner], "device": dev.device,
                    "u": u, "v": v, "z": z, "id": main.get("id")})
    return sorted(out, key=lambda e: (e["t"], e["owner"], e["id"] if e["id"] is not None else -1))
