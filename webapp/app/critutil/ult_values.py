"""An ult's lift window: its active time (windows.window, with Gekko's detain) plus a per-ult tail.

`ult_segments(round_data, item, entry)` returns `(segments, mode)`, mode "round" or "window". The entry's
`ult_window` (abilities.json, a site-side field the extractor never reads) is a number (tail seconds after the
active time), "total:N" (N seconds from the start) or "round" (the use to the round's end). Never capped at 45 s.
"""

from __future__ import annotations

from app.services import util_values

DEFAULT_TAIL_S = 5.0
THRASH_DETAIN_S = 6.0       # the wiki's detain, from Thrash last seen
THRASH_END_S = 2.0          # the owner's: the cocoon's end, plus 2 s


def _thrash_end(item: dict) -> float | None:
    ends = [e["t1"] for e in item.get("effects") or [] if e.get("t1") is not None]
    if ends:
        return max(ends) + THRASH_END_S
    seen = (item.get("signals") or {}).get("seen_end")
    return None if seen is None else seen + THRASH_DETAIN_S + THRASH_END_S


def ult_segments(round_data: dict, item: dict, entry: dict | None) -> tuple[list, str]:
    td = float(round_data.get("t_decided") or 0.0)
    spec = (entry or {}).get("ult_window", DEFAULT_TAIL_S)
    segs = [list(s) for s in util_values.item_segments(round_data, item, "ult")]
    start = segs[0][0] if segs else item.get("t")
    if start is None:
        return [], "window"
    if spec == "round":
        return ([[start, td]] if td > start else []), "round"
    if isinstance(spec, str) and spec.startswith("total:"):
        end = min(start + float(spec.split(":", 1)[1]), td)
        return ([[start, end]] if end > start else []), "window"
    if (entry or {}).get("ability") == "Thrash" and (entry or {}).get("agent") == "Gekko":
        end = _thrash_end(item)
        if end is not None:
            segs = [[start, end]]
    if not segs:
        return [], "window"
    segs[-1][1] += float(spec)
    out = []
    for a, b in segs:
        b = min(b, td)
        if b > a:
            out.append([a, b])
    return out, "window"
