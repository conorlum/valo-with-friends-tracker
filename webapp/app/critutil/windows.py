"""Each cast's up-time, from its ability table rule (abilities.json `rule`): when it could act for its side.

`window(item, entry, t_decided, on_known=True)` returns `{"segments": [[t0, t1], ...], "rule": str, "fallback": bool}`.
`item` is a stored cast (round_util `casts`), a heal row (`heals`, no `type`/`signals`) or an ult slot's `use` dict.
`entry` is its abilities.json entry or None. Segments are sorted, do not overlap, and are cut at `t_decided`.
`fallback` is True when the rule's own signal was missing and the table duration was used instead.
g = the entry's `grace_s` (default 1), D = its `duration_s` (may be None), start = signals.landed, else the cast's t.

  duration    [t, start + D + g], cut at seen_end + g when its pieces were last seen sooner. D None: seen_end + g.
  fixed:N     [t, t + N].
  flash       with a pop and hits: [t, pop + longest hit, at most 4 s]; a pop and no hits: [t, pop + g]; no pop but
              effects (Gekko's Dizzy): the latest effect end, each at most t0 + 4 s; neither: duration. Fallback
              when there is no pop.
  status      the latest effect end: a known one ends at t1 + 2 s, an unknown one at t0 + D + g, cut at seen_end + g
              when no end is known (a Leer shot down). No usable effect: duration (cut the same way).
  placeable   [armed (at least 0), (seen_end or t_decided) + g] minus every `off` span. With `after_trigger_s` A
              and a trigger (signals.go_off, else the cast's `trigger`), it ends at trigger + A + g instead (Shear's
              wall, Fakeout's flash). With `from_trigger` it starts at the trigger, and never set off it has none
              (Nanoswarm: only while it damages).
  possessed   each span its owner looked through it, plus g after it (Spycam: 5 s); never looked through: none.
  travel      [t, seen_end + D + g]: the flight, then the status it leaves (Paranoia); no seen_end: fallback.
  tag         a cast that tagged an enemy: [t, seen_end + g] (Spycam dart: until it is taken out, plus 5 s), no
              seen_end: the last effect end + g; a cast that tagged nobody: none.
  on_spans    each stored `on` span, plus g after it (Viper: 5 s). None stored: with on_known, off its whole life; otherwise duration, fallback.
  life        [start, seen_end]; no seen_end: [start, start + D + g], fallback (Viper's Pit).
  heal        a heal row: [t, death or t_decided]; a heal-type cast: [t, t_decided].
  ult:raze    [ult_drop, t + g]; no drop: fallback.
  ult:omen    [t, (spike_pickup if she got the spike, else arrival) + 10 s]; no arrival: fallback.
  ult:thrash  [moved, seen_end + g]; either missing: fallback.
  ult:duel    [t, duel_end + g]; missing: fallback.
  ult:capture [t, capture_end + g]; missing: fallback.
  ult:seekers [t, latest effect end + 2 s]; else [t, seen_end]; else fallback.
  ult:until_end / ult:until_death
              on the `use` dict: [first use, the earlier of ult_end / death, else t_decided]; no use: none.
  A fallback with D None gives no segments.
"""

from __future__ import annotations

FLASH_CAP_S = 4.0
STATUS_KNOWN_TAIL_S = 2.0
OMEN_TAIL_S = 10.0
SEEKERS_TAIL_S = 2.0
STATUS_HOWS_OUT = ("damage", "reveal")


def _cut(segments, t_decided):
    out = []
    for a, b in sorted(segments, key=lambda s: s[0]):
        if a is None or b is None:
            continue
        if t_decided is not None:
            b = min(b, t_decided)
        if b > a:
            if out and a < out[-1][1]:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
    return out


def _minus(span, holes):
    out = [list(span)]
    holes = [[h0, float("inf") if h1 is None else h1] for h0, h1 in holes if h0 is not None]  # open: off to the end
    for h0, h1 in sorted(holes, key=lambda s: s[0]):
        nxt = []
        for a, b in out:
            if h1 <= a or h0 >= b:
                nxt.append([a, b])
                continue
            if h0 > a:
                nxt.append([a, h0])
            if h1 < b:
                nxt.append([h1, b])
        out = nxt
    return out


def window(item: dict, entry: dict | None, t_decided: float, on_known: bool = True) -> dict:
    is_heal = "type" not in item and "signals" not in item
    rule = "heal" if is_heal else ((entry or {}).get("rule") or "duration")
    if entry is None and not is_heal:
        rule = "duration"
    g = (entry or {}).get("grace_s")
    g = 1.0 if g is None else g
    D = (entry or {}).get("duration_s")
    s = item.get("signals") or {}
    t = item.get("t")
    start = s.get("landed") if s.get("landed") is not None else t
    seen = s.get("seen_end")
    effects = item.get("effects") or []
    fallback = False

    def timed(cap=True):
        """start + D + g, cut at seen_end + g; D None: seen_end + g or nothing. -> [segments]"""
        if D is None:
            return [[t, seen + g]] if seen is not None else []
        end = start + D + g
        if cap and seen is not None and seen + g < end:
            end = seen + g
        return [[t, end]]

    def fb():
        nonlocal fallback
        fallback = True
        return [[t, t + D + g]] if D is not None else []

    segs: list = []
    if rule == "duration":
        segs = timed()
    elif rule.startswith("fixed:"):
        segs = [[t, t + float(rule.split(":", 1)[1])]]
    elif rule == "flash":
        pop = s.get("pop")
        hits = s.get("hits") or []
        if pop is not None and hits:
            segs = [[t, pop + min(max(h[1] - h[0] for h in hits), FLASH_CAP_S)]]
        elif pop is not None:
            segs = [[t, pop + g]]
        elif effects:
            fallback = True
            segs = [[t, max(min(e["t1"], e["t0"] + FLASH_CAP_S) for e in effects)]]
        else:
            fallback = True
            segs = timed()
    elif rule == "status":
        ends, known = [], False
        for e in effects:
            if e.get("how") in STATUS_HOWS_OUT:
                continue
            if e.get("known"):
                known = True
                ends.append(e["t1"] + STATUS_KNOWN_TAIL_S)
            elif D is not None:
                ends.append(e["t0"] + D + g)
        if ends and not known and seen is not None:     # only unknown ends (Leer): it can't outlast its pieces
            ends = [min(max(ends), seen + g)]
        segs = [[t, max(ends)]] if ends else timed()
    elif rule == "placeable":
        went = s.get("go_off") if s.get("go_off") is not None else item.get("trigger")
        after = (entry or {}).get("after_trigger_s")
        if (entry or {}).get("from_trigger"):
            a = went
        else:
            a = max(s["armed"] if s.get("armed") is not None else t, 0.0)
        if a is None:
            segs = []                                   # never set off (a Nanoswarm left hidden)
        else:
            if after is not None and went is not None:
                b = went + after + g
            else:
                b = (seen if seen is not None else t_decided) + g
            segs = _minus([a, b], s.get("off") or [])
    elif rule == "possessed":
        segs = [[a, (t_decided if b is None else b) + g] for a, b in (s.get("possessed") or [])]
    elif rule == "travel":
        segs = [[t, seen + (D or 0.0) + g]] if seen is not None else fb()
    elif rule == "tag":
        if not item.get("enemies"):
            segs = []                                   # tagged nobody: nothing it could tell
        elif seen is not None:
            segs = [[t, seen + g]]
        else:
            ends = [e.get("t1") if e.get("t1") is not None else e["t0"] for e in effects]
            segs = [[t, max(ends) + g]] if ends else fb()
    elif rule == "on_spans":
        on = s.get("on") or []
        if on:
            segs = [[a, t_decided if b is None else b + g] for a, b in on]     # an open span is on to the end
        elif on_known:
            segs = []
        else:
            fallback = True
            segs = [[t, start + D + g]] if D is not None else []
    elif rule == "life":
        if seen is not None:
            segs = [[start, seen]]
        else:
            fallback = True
            segs = [[start, start + D + g]] if D is not None else []
    elif rule == "heal":
        if is_heal:
            d = item.get("death")
            segs = [[t, d if d is not None else t_decided]]
        else:
            segs = [[t, t_decided]]
    elif rule == "ult:raze":
        segs = [[s["ult_drop"], t + g]] if s.get("ult_drop") is not None else fb()
    elif rule == "ult:omen":
        arr = s.get("arrival")
        if arr is None:
            segs = fb()
        else:
            pick = s.get("spike_pickup")
            anchor = pick if (s.get("got_spike") and pick is not None) else arr
            segs = [[t, anchor + OMEN_TAIL_S]]
    elif rule == "ult:thrash":
        if s.get("moved") is not None and seen is not None:
            segs = [[s["moved"], seen + g]]
        else:
            segs = fb()
    elif rule == "ult:duel":
        segs = [[t, s["duel_end"] + g]] if s.get("duel_end") is not None else fb()
    elif rule == "ult:capture":
        segs = [[t, s["capture_end"] + g]] if s.get("capture_end") is not None else fb()
    elif rule == "ult:seekers":
        if effects:
            segs = [[t, max(e["t1"] for e in effects) + SEEKERS_TAIL_S]]
        elif seen is not None:
            segs = [[t, seen]]
        else:
            segs = fb()
    elif rule in ("ult:until_end", "ult:until_death"):
        if t is None:
            segs = []
        else:
            stops = [x for x in (s.get("ult_end"), s.get("death")) if x is not None]
            segs = [[t, min(stops) if stops else t_decided]]
    else:
        segs = fb()
    return {"segments": _cut(segs, t_decided), "rule": rule, "fallback": fallback}
