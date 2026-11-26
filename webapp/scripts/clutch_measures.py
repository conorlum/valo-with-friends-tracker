"""Pure measurements over public replay page data and decoded round blobs.

No database or fetching: callers supply the page context, RoundView and RoundInputs.
Slots are the only player identifiers returned. Situation windows include their start
and exclude their end; each team enters each situation kind only once per round.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path
import sys

import numpy as np

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.coach.fights import _gun_at, _tier  # noqa: E402
from app.coach.roundview import RoundView, SPIKE_TIMER_S  # noqa: E402
from app.control import engine as ce, stance as st_mod  # noqa: E402
from app.control.utility import _sees_point  # noqa: E402
from app.moments.constants import FIGHT_GAP_S, MOVING_MPS, SIGHT_STEP_S  # noqa: E402
from app.moments.fights import _events, _first_sight, _speed, _split  # noqa: E402
from app.replays import player_state  # noqa: E402


def sides(ctx: dict, n: int) -> dict:
    """DB-free copy of scripts/preview_control_live.py:link_for's sides output.

    Attacker rule copied from app/scoring/plant_window.py:attacking_team:
    team-1 attacks 1..12, team-2 13..24, then alternates starting with team-1.
    """
    if n < 1:
        return {}
    attacking = "team-1" if n <= 12 or (n >= 25 and (n - 25) % 2 == 0) else "team-2"
    out = {}
    for slot, p in sorted(ctx["players"].items(), key=lambda kv: int(kv[0])):
        team = ctx["match"]["side_to_team"].get(p["side"])
        if team is not None:
            out[str(slot)] = "attack" if team == attacking else "defense"
    return out


def db_deaths(ctx: dict, n: int) -> list:
    """DB-free copy of scripts/preview_control_live.py:link_for's death output."""
    offset = ctx["match"].get("clock_offset") or 0.0
    return sorted([d["slot"], round(float(d["t_db"]) - offset, 3)]
                  for d in ctx["rounds"][str(n)].get("db_deaths", []) if d.get("slot") is not None)


def situations(ctx: dict, n: int, blob: dict) -> tuple[list[dict], list[dict]]:
    """First 1vN and close 2vN per team, with a separate window for each player.

    Counts come from the page's team-indexed alive_steps. RoundView._intervals
    cuts DB-only deaths with replay_view.alive_steps' same one-second rule.
    Cutoffs count situations, including a 2vN pair once, rather than player rows.
    They retain the situation identity, exact enemy count and entry time for reporting.
    n_at_start retains the exact enemy count before the report's 3+ grouping.
    """
    page = ctx["rounds"][str(n)]
    roles = sides(ctx, n)
    teams = {int(s): ctx["match"]["side_to_team"].get(p["side"])
             for s, p in ctx["players"].items()}
    view = RoundView(blob, {int(s): str(s) for s in ctx["players"]}, 1, None,
                     groups={int(s): p["side"] for s, p in ctx["players"].items()},
                     roles={int(s): role for s, role in roles.items()}, db_deaths=db_deaths(ctx, n))
    decided = float(blob["t_decided"])
    rows, cutoffs, entered = [], [], set()
    for t, a, b in sorted(page["alive_steps"], key=lambda step: step[0]):
        for team, own, enemies in (("team-1", a, b), ("team-2", b, a)):
            if own == 1 and enemies >= 1:
                kind, group = "1vN", f"1v{enemies}" if enemies < 3 else "1v3+"
            elif own == 2 and enemies in (2, 3):
                kind, group = "2vN", f"2v{enemies}"
            else:
                continue
            key = (team, kind)
            if key in entered:
                continue
            entered.add(key)
            won = page["db"]["winner"] == team
            situation_id = f"{ctx['match']['uuid']}:{n}:{team}:{kind}"
            if t >= decided:
                cutoffs.append({"situation_id": situation_id, "team": team, "kind": kind,
                                "n_enemies": enemies, "group": group, "won": won, "t": t})
                continue
            living = [s for s in view.slots if teams.get(s) == team and view.alive(s, t)]
            for slot in living:
                death = next(end for start, end in view._intervals(slot) if start <= t < end)
                rows.append({
                    "situation_id": situation_id,
                    "kind": kind, "team": team, "side": roles[str(slot)], "slot": slot,
                    "n_enemies": enemies, "own_alive": own, "t_s": t,
                    "t_end_window": min(death, decided), "won": won,
                    "alive_at_end": view.alive(slot, decided), "group": group, "n_at_start": enemies,
                })
    return rows, cutoffs


def time_left(blob: dict, t: float) -> float:
    """Round seconds remaining at a replay time (also usable by W2 engagements)."""
    spike = player_state.spike_at(blob.get("player_state"), t)
    if spike is not None and spike["s"] == "planted":
        return SPIKE_TIMER_S - (t - spike["t"])
    return 100.0 - t


def engagements(rnd, blob: dict, slot: int, t0: float, t1: float) -> list[dict]:
    """Fights and first sight per enemy, either way, in the closed [t0, t1] window.

    Reuse Critical Moments' event ordering and damage-run split. A first sight
    near a fight involving that enemy belongs to the fight; a sight of another
    enemy remains separate. The earliest direction is one sight engagement.
    Callers fill clock's time_left_first_engagement using time_left at the first engagement.
    """
    t1 = min(t1, rnd.t_decided)
    groups = _split([e for e in _events(rnd, blob, slot) if t0 <= e[0] <= t1])
    fights = [{"t": group[0][0], "enemies": sorted({e[3] for e in group}), "kind": "fight"}
              for group in groups]
    spans = [(fight, max(e[4] for e in group)) for fight, group in zip(fights, groups)]
    out = list(fights)
    for enemy in sorted(s for s, team in rnd.team.items() if team is not None and team != rnd.team[slot]):
        firsts = [_first_sight(rnd, viewer, target, t0, t1)[0]
                  for viewer, target in ((slot, enemy), (enemy, slot))]
        seen = [t for t in firsts if t is not None]
        if not seen:
            continue
        t = min(seen)
        if any(enemy in fight["enemies"] and fight["t"] - FIGHT_GAP_S <= t <= end + FIGHT_GAP_S
               for fight, end in spans):
            continue
        out.append({"t": t, "enemies": [enemy], "kind": "sight"})
    return sorted(out, key=lambda e: (e["t"], e["kind"] != "fight", e["enemies"]))


def _clear_line(rnd, viewer: int, t: float, x: float, y: float, target: int | None = None) -> bool:
    """Copy of utility._sees_point with only its FOV check omitted (R5/D3).

    Keep blinds, smoke, boxed stance, top pieces and airborne pair sight identical.
    """
    if not rnd.alive(viewer, t) or any(a <= t < b for a, b in rnd.flashed.get(viewer, [])):
        return False
    p = rnd.pos(viewer, t)
    if p is None:
        return False
    vs = st_mod.boxed_stance(rnd, viewer, t)
    ex, ey = (p[0], p[1]) if vs is None else vs.eye_px
    sv = () if vs is None else vs.see_over
    st = ()
    ts = None
    if target is not None:
        ts = st_mod.boxed_stance(rnd, target, t)
        if ts is not None:
            x, y = ts.eye_px
            st = ts.see_over
    if ts is None and len(rnd.geo.top_nodes):
        tops = np.flatnonzero(rnd.geo.top_leaf == rnd.geo.space_at_px(x, y))
        if len(tops):
            st = (int(rnd.geo.top_piece[tops[0]]),)
    if any(a <= t < b for a, b in rnd.nearsight.get(viewer, [])) and \
            math.hypot(x - ex, y - ey) * rnd.geo.m_per_px >= ce.NEARSIGHT_RADIUS_M:
        return False
    if target is not None:
        seen = ce._air_pair_sight(rnd, viewer, target, t, rnd.smokes_at(t))
        if seen is not None:
            return seen
    return ce.los(rnd.geo, (ex, ey, None), (x, y, None), rnd.smokes_at(t), see_over=(sv, st))


def isolation(rnd, slot: int, t: float, enemies) -> dict:
    """Distinct living enemies seeing the clutcher anywhere in [t-.5, t+.5]."""
    in_view, clear_line = set(), set()
    enemies = set(enemies)
    k = 0
    while t - 0.5 + k * SIGHT_STEP_S <= t + 0.5:
        sample = t - 0.5 + k * SIGHT_STEP_S
        k += 1
        if not rnd.alive(slot, sample):
            continue
        p = rnd.pos(slot, sample)
        if p is None:
            continue
        for enemy in enemies:
            if not rnd.alive(enemy, sample):
                continue
            if enemy not in in_view and _sees_point(rnd, enemy, sample, p[0], p[1], target=slot):
                in_view.add(enemy)
            if enemy not in clear_line and _clear_line(rnd, enemy, sample, p[0], p[1], target=slot):
                clear_line.add(enemy)
    return {"in_view": len(in_view), "clear_line": len(clear_line)}


def isolation_summary(rows: list[dict]) -> dict:
    """One-at-a-time share excludes unseen engagements from its denominator."""
    isolated = sum(row["in_view"] == 1 for row in rows)
    unseen = sum(row["in_view"] == 0 for row in rows)
    seen = len(rows) - unseen
    return {"engagements": len(rows), "isolated": isolated, "unseen": unseen,
            "one_at_a_time": isolated / seen if seen else None}


def clock(view, rnd, blob: dict, row: dict) -> dict:
    """Clock, movement and attempts starting within the player's alive window.

    Still share uses the moments speed rule on available alive track samples;
    missing speeds are excluded, and no usable samples means unknown (None).
    A plant finishes only when its recorded end matches a planted transition.
    """
    slot, start, end = row["slot"], row["t_s"], row["t_end_window"]
    ps = blob.get("player_state") or {}
    speeds = []
    track = rnd.tracks.get(slot)
    if track is not None:
        for t in track[0]:
            if start <= t < end and view.alive(slot, t):
                speed = _speed(rnd, slot, t)
                if speed is not None:
                    speeds.append(speed)
    plants = []
    for t0, t1, planter in ps.get("plants") or []:
        if planter != slot or not start <= t0 < end:
            continue
        finished = t1 is not None and any(
            e["s"] == "planted" and abs(e["t"] - t1) <= 0.001 and e.get("slot", slot) == slot
            for e in ps.get("spike") or [])
        plants.append({"t": t0, "t1": t1, "by": slot,
                       "enough_time": 100.0 - t0 >= 4.0, "finished": finished})
    defuses = []
    for attempt in view.defuse_attempts():
        if attempt["by"] != view.names.get(slot) or not start <= attempt["t"] < end:
            continue
        spike_left = attempt.get("spike_left_s")
        defuses.append({**attempt, "by": slot,
                        "enough_time": None if spike_left is None else spike_left >= 7.0,
                        "reached_half": attempt["result"] in ("half", "defused")})
    return {"time_left_start": time_left(blob, start), "time_left_first_engagement": None,
            "still_share": sum(s < MOVING_MPS for s in speeds) / len(speeds) if speeds else None,
            "plant_attempts": plants, "defuse_attempts": defuses}


def state(view, rnd, blob: dict, ctx: dict, n: int, row: dict) -> dict:
    """State at entry: life-aware coach gun tiers, vitals, economy and spike.

    Loadouts sum all page stat rows for each team, including dead teammates.
    Missing observations stay unknown; an absent stat row contributes no sum.
    """
    slot, t = row["slot"], row["t_s"]
    util = sorted(blob.get("util") or [], key=lambda event: event["t"])
    holds = [e for e in util if e.get("k") == "hold"]
    shots = [e for e in util if e.get("k") == "shot"]

    def tier(s):
        gun, _ = _gun_at(view, s, t, holds, shots)
        return _tier(gun)

    ps = blob.get("player_state") or {}
    vital = player_state.vitals_at(ps, slot, t)
    # A revived player's vitals cannot come from an earlier life.
    lives = view._intervals(slot)
    life_index = next((i for i, (a, b) in enumerate(lives) if a <= t < b), None)
    if vital is not None and life_index is not None and life_index > 0 and vital["t"] < lives[life_index][0]:
        vital = None
    hp_shield = (vital["hp"] + vital["sh"] if vital is not None
                 and vital.get("hp") is not None and vital.get("sh") is not None else None)
    stats = ctx["rounds"][str(n)].get("stats") or {}
    if isinstance(stats, str):
        stats = ast.literal_eval(stats)
    loadouts = {}
    for s, stat in stats.items():
        if isinstance(stat, str):
            stat = ast.literal_eval(stat)
        p = ctx["players"].get(str(s))
        team = ctx["match"]["side_to_team"].get(p["side"]) if p else None
        if team is not None and stat.get("loadout") is not None:
            loadouts[team] = loadouts.get(team, 0) + stat["loadout"]
    spike = player_state.spike_at(ps, t)
    return {"gun_tier": tier(slot),
            "enemy_gun_tiers": {s: tier(s) for s in view.enemies(slot) if view.alive(s, t)},
            "hp_shield": hp_shield, "team_loadouts": loadouts,
            "spike_state": spike["s"] if spike is not None else None,
            "n_enemies": row["n_enemies"], "side": row["side"]}
