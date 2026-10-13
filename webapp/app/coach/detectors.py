"""Moments worth the coach's attention, found by fixed rules over one round. A flag is a pointer with measured
values, never a verdict: the model decides what it means and checks it with the query commands.

Thresholds are first values, to be reviewed against real rounds (plan 1, Task 9) and changed only here."""

from __future__ import annotations

from app.coach import areas, knowledge
from app.scoring.impact import TRADE_WINDOW_SECONDS

TRADE_WINDOW_S = float(TRADE_WINDOW_SECONDS)   # the scoring formula's own trade window
TRADE_RANGE_M = 20.0        # a teammate farther than this at the death could not have traded it
PARTNER_WINDOW_S = 5.0      # a teammate beside the player who died this recently was in the same fight
STACK_ENEMIES = 4           # this many enemies known on one site is a stack
STACK_FRESH_S = 10.0        # a sighting older than this no longer places an enemy
STACK_HOLD_S = 8.0          # still on another site this long after the stack was known
FLASH_FOLLOWUP_S = 3.0      # a kill on a flashed enemy inside this is credited to the flash
_EPS = 0.01


def _flag(view, kind: str, t: float, **values) -> dict:
    return {"kind": kind, "round": view.number, "t": round(float(t), 2), "values": values}


def _live_kills(view) -> list[dict]:
    """Kills while the round was still open: later ones stay in the blob and decide nothing."""
    return [k for k in view.kills if k["t"] <= view.t_decided]


def _fallen_partner(view, slot, t: float, live: list[dict]):
    """The teammate who died within PARTNER_WINDOW_S before `t` while within trading range of the player or in
    the same named area: (slot, seconds before, metres then). The nearest in time; None when there was none."""
    for k in sorted(live, key=lambda k: -k["t"]):
        mate = k["victim"]
        if mate not in view.teammates(slot) or not 0 < t - k["t"] <= PARTNER_WINDOW_S:
            continue
        then = k["t"] - _EPS
        if not view.alive(slot, then):
            continue
        metres = view.distance_m(slot, mate, then)
        here, there = view.position(slot, then).get("area"), view.position(mate, then).get("area")
        same_area = bool(here and there and here["name"] == there["name"])
        if metres is not None and (metres <= TRADE_RANGE_M or same_area):
            return mate, round(t - k["t"], 2), round(metres, 1)
    return None


def _deaths(view, slot):
    out = []
    live = _live_kills(view)
    for k in live:
        if k["victim"] != slot:
            continue
        t, killer, before = k["t"], k.get("killer"), k["t"] - _EPS
        mates = [m for m in view.teammates(slot) if view.alive(m, before)]
        gaps = [d for d in (view.distance_m(slot, m, before) for m in mates) if d is not None]
        nearest = round(min(gaps), 1) if gaps else None
        traded_by = None
        if killer is not None and killer != slot:
            for k2 in live:                                    # R-F12: only kills inside the open round
                if k2["victim"] == killer and k2.get("killer") in view.teammates(slot) \
                        and 0 <= k2["t"] - t <= TRADE_WINDOW_S:
                    traded_by = (k2["killer"], round(k2["t"] - t, 2))
                    break
        name = view.names.get(killer) if killer is not None else None
        if traded_by:
            out.append(_flag(view, "death_traded", t, killer=name, traded_by=view.names.get(traded_by[0]),
                             after_s=traded_by[1], nearest_teammate_m=nearest))
        elif nearest is not None and nearest > TRADE_RANGE_M:      # None: nobody left, or no position to measure
            partner = _fallen_partner(view, slot, t, live)
            if partner:
                # the teammate beside them died moments before: a fight taken together and lost, not a death
                # alone (owner's feedback on report 1). The living teammates were still far.
                out.append(_flag(view, "death_after_partner", t, killer=name, partner=view.names.get(partner[0]),
                                 after_s=partner[1], partner_m=partner[2], nearest_teammate_m=nearest,
                                 teammates_alive=len(mates)))
            else:
                out.append(_flag(view, "death_untraded_alone", t, killer=name, nearest_teammate_m=nearest,
                                 teammates_alive=len(mates)))
        own = len(view.living(view.side(slot), before))
        enemy = len([e for e in view.enemies(slot) if view.alive(e, before)])
        if own > enemy:
            out.append(_flag(view, "death_with_advantage", t, killer=name, alive_before={"own": own, "enemy": enemy}))
    return out


def _first_contact(view, slot):
    kills = _live_kills(view)
    if not kills:
        return []
    first = kills[0]
    if first.get("killer") == slot:
        return [_flag(view, "first_kill", first["t"], victim=view.names.get(first["victim"]))]
    if first["victim"] == slot:
        killer = first.get("killer")
        return [_flag(view, "first_death", first["t"], killer=view.names.get(killer) if killer is not None else None)]
    return []


def _flashes(view, slot):
    out, mates, enemies = [], set(view.teammates(slot)), set(view.enemies(slot))
    for e in view.blob.get("util", []):
        if e.get("k") != "flash" or e.get("by") != slot:
            continue
        # a stored hit of zero seconds blinded nobody; null is a hit of unknown length
        hits = [h for h in e.get("hits") or [] if len(h) < 3 or h[2] is None or h[2] > 0]
        on_enemy = [h for h in hits if h[0] in enemies]
        on_mate = [h for h in hits if h[0] in mates]
        proof = e.get("activation") or {}
        if on_mate:
            out.append(_flag(view, "flash_hit_teammate", e["t"], hit=[view.names.get(h[0]) for h in on_mate]))
        if not on_enemy:
            if not on_mate and proof.get("state") == "completed" and proof.get("targets_complete"):
                out.append(_flag(view, "flash_hit_no_enemy", e["t"]))
            continue
        hit_at = {h[0]: h[1] for h in on_enemy}
        followed = [k for k in _live_kills(view) if k["victim"] in hit_at
                    and (k.get("killer") == slot or k.get("killer") in mates)
                    and 0 <= k["t"] - hit_at[k["victim"]] <= FLASH_FOLLOWUP_S]
        if followed:
            out.append(_flag(view, "flash_led_to_kill", e["t"], hit=[view.names.get(s) for s in hit_at],
                             killed=[view.names.get(k["victim"]) for k in followed],
                             killers=[view.names.get(k["killer"]) for k in followed]))
    return out


def _site_of(area: dict | None) -> str | None:
    return area["super"] if area and area["super"] in areas.SITES else None


def _stack(view, slot, knew, role):
    if not knew:
        return []
    side, t = view.side(slot), 0.0
    while t <= view.t_decided:
        if not view.alive(slot, t):
            t += 1.0                                           # R-F11: a late-starting life is still scanned
            continue
        here = view.position(slot, t)
        mine = _site_of(here.get("area"))
        if mine is not None:
            placed: dict[str, list] = {}
            for e in knowledge.known_enemies(knew, side, t, view):
                site = _site_of(e["area"])
                if site and e["alive"] and (e["in_view"] or t - e["last_seen"] <= STACK_FRESH_S):
                    placed.setdefault(site, []).append(e["name"])
            for site, names in sorted(placed.items()):
                later = t + STACK_HOLD_S
                if site == mine or len(names) < STACK_ENEMIES or later > view.t_decided or not view.alive(slot, later):
                    continue
                after = view.position(slot, later)
                if _site_of(after.get("area")) == mine:
                    return [_flag(view, "stack_seen_elsewhere", t, site=site, enemies_seen=len(names), enemies=names,
                                  player_area_then=here["area"]["name"], player_area_after=after["area"]["name"],
                                  hold_s=STACK_HOLD_S, role=role)]
        t += 1.0
    return []


def flags(view, slot: int, knew: dict | None, role: str | None = None) -> list[dict]:
    found = _deaths(view, slot) + _first_contact(view, slot) + _flashes(view, slot) + _stack(view, slot, knew, role)
    return sorted(found, key=lambda f: (f["t"], f["kind"]))
