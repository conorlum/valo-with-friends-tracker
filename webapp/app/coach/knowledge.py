"""What a side knew of each enemy by a time, from map control's stored rows: `header["knew"]` (sightings, each
with a place) and `header["knowledge_events"]` (every locating event, sound included, with a reason and no place).
The spec's "knowable": team knowledge, perfect comms."""

from __future__ import annotations


def signals(events: dict | None, side: str, t: float, names: dict[int, str]) -> list[dict] | None:
    if events is None:
        return None
    return [{"t": row[0], "name": names.get(int(row[1])), "reason": row[2]}
            for row in events.get(side) or [] if row[0] <= t]


def known_enemies(knew: dict, side: str, t: float, view) -> list[dict]:
    out = []
    for slot_text, runs in (knew.get(side) or {}).items():
        seen = [run for run in runs if run[0] <= t]
        if not seen:
            continue
        slot = int(slot_text)
        t0, t1, u, v = seen[-1]
        in_view = t <= t1
        if in_view:
            now = view.position(slot, t)
            if now.get("alive") and now.get("u") is not None:
                u, v = now["u"], now["v"]
        out.append({"slot": slot, "name": view.names.get(slot), "first_seen": seen[0][0],
                    "last_seen": t if in_view else t1, "in_view": in_view, "u": u, "v": v,
                    "area": view.area(u, v), "alive": view.alive(slot, t)})
    return sorted(out, key=lambda e: e["slot"])
