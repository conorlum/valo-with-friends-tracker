"""Pure measurements over public replay page data and decoded round blobs.

No database or fetching: callers supply the page context, RoundView and RoundInputs.
Slots are the only player identifiers returned. Situation windows include their start
and exclude their end; each team enters each situation kind only once per round.
"""

from __future__ import annotations

import ast
from bisect import bisect_right
import gzip
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
from app.critutil.extract import _ult_at, _ult_cap, build_casts  # noqa: E402
from app.moments.constants import FIGHT_GAP_S, MOVING_MPS, SIGHT_STEP_S  # noqa: E402
from app.moments.fights import _events, _first_sight, _speed, _split  # noqa: E402
from app.replays import player_state  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from app.replays.control_reasons import classify  # noqa: E402


class ControlRound:
    """Read a stored control payload; decode each side's unknown masks on demand.

    Stored ticks are grid units, while sightings and knowledge events already use
    seconds. Checkpoint entries name frame indices, not times.
    """

    def __init__(self, raw_bytes: bytes):
        packed = raw_bytes if raw_bytes.startswith(b"\x1f\x8b") else gzip.compress(raw_bytes)
        self.header, self._streams = cf.unpack_data(packed)
        self.ticks = [t / self.header["hz"] for t in self.header["ticks"]]
        self.knew = self.header.get("knew") or {}
        self.knowledge_events = self.header.get("knowledge_events") or {}
        self._unknown = {}
        self._walk_cells = None

    @property
    def has_unknown(self) -> bool:
        return any("unknown_" + group in self._streams for group in ("a", "b"))

    def _mask(self, group, t):
        i = bisect_right(self.ticks, t) - 1
        name = "unknown_" + group.lower()
        if i < 0 or name not in self._streams:
            return None
        if name not in self._unknown:
            checkpoints = [row[0] for row in self.header["unknown_checkpoints"]]
            self._unknown[name] = np.asarray(cf.decode_unknown(
                self._streams[name], len(self.ticks), self.header["cells"], checkpoints), dtype=bool)
        return self._unknown[name][i]

    def unknown_share(self, group, t) -> float | None:
        mask = self._mask(group, t)
        return float(np.mean(mask)) if mask is not None and len(mask) else None

    def nearest_unknown_m(self, group, x_px, y_px, t, geo) -> float | None:
        mask = self._mask(group, t)
        if mask is None or not np.any(mask):
            return None
        if self._walk_cells is None:
            self._walk_cells = np.asarray(cf.walk_bitmap(self.header), dtype=int)
        cells = self._walk_cells[mask]
        if geo.cell_of_px(x_px, y_px) in cells:
            return 0.0
        centres = geo.centres[cells]
        return float(np.min(np.hypot(centres[:, 0] - x_px, centres[:, 1] - y_px))) * geo.m_per_px

    def located(self, group, enemy, t0, t1) -> str | None:
        for start, stop, *_ in self.knew.get(group, {}).get(str(enemy), []):
            if start <= t1 and stop >= t0:
                return "seen"
        for t, slot, reason, kind in self.knowledge_events.get(group, []):
            if slot == enemy and t0 <= t <= t1 and reason != "seen" and classify(reason, kind) == "found":
                return "heard"
        return None


def unknown(rnd, ctl: ControlRound, row: dict) -> dict:
    """Unknown shares and approach on consecutive moving track ticks (R7).

    No movement or no usable distance pairs gives a null approach share. Missing
    tracks, gaps, or an empty unknown mask never imply movement toward unknown.
    """
    out = dict.fromkeys(("unknown_share_start", "unknown_share_end", "into_unknown_share"))
    if not ctl.has_unknown:
        return out
    slot, start, end = row["slot"], row["t_s"], row["t_end_window"]
    group = rnd.team[slot]
    out["unknown_share_start"] = ctl.unknown_share(group, start)
    out["unknown_share_end"] = ctl.unknown_share(group, end)
    track = rnd.tracks.get(slot)
    approaches = []
    if track is not None:
        for a, b in zip(track[0], track[0][1:]):
            if not start <= a < b < end or not (rnd.alive(slot, a) and rnd.alive(slot, b)):
                continue
            speed = _speed(rnd, slot, b)
            if speed is None or speed < MOVING_MPS:
                continue
            pa, pb = rnd.pos(slot, a), rnd.pos(slot, b)
            if pa is None or pb is None:
                continue
            before = ctl.nearest_unknown_m(group, *pa[:2], a, rnd.geo)
            after = ctl.nearest_unknown_m(group, *pb[:2], b, rnd.geo)
            if before is not None and after is not None:
                approaches.append(before - after > 0.5)
    out["into_unknown_share"] = sum(approaches) / len(approaches) if approaches else None
    return out


def backshots(rnd, blob: dict, slot: int, t0: float, t1: float) -> int:
    """Count incoming enemy damage rows outside the yaw cone, once per damage run.

    Aggregated runs have no individual hit positions; classify their recorded start.
    Walls, smoke and blinds do not change this bearing-only measurement (R7).
    """
    count = 0
    for row in blob.get("util") or []:
        if row.get("k") != "damage" or row.get("target") != slot or not t0 <= row["t"] <= t1:
            continue
        enemy = row.get("by")
        if rnd.team.get(enemy) in (None, rnd.team.get(slot)):
            continue
        p, q = rnd.pos(slot, row["t"]), rnd.pos(enemy, row["t"])
        if p is None or q is None:
            continue
        bearing = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
        count += abs((bearing - p[2] + 180) % 360 - 180) > ce.FOV_HALF
    return count


def information(ctl: ControlRound, group, engagements: list[dict]) -> dict:
    """Share of engagements with every involved enemy located in the preceding 5 s.

    A multi-enemy fight is known only if each enemy was located; one known enemy
    cannot establish that the others were known (R8). Sight takes precedence.
    """
    known = [bool(e["enemies"]) and all(ctl.located(group, s, e["t"] - 5, e["t"]) is not None
                                      for s in e["enemies"]) for e in engagements]
    return {"located_share": sum(known) / len(known) if known else None}


def fakes(view, rnd, blob: dict, row: dict, engagements: list[dict]) -> dict:
    """Defuse taps and proven unfinished plants, and engagements after release (R8).

    Attempts must start in the player's window, have a known stop, and begin while
    an enemy lives. A plant is finished only at a matching planted transition,
    as in clock(). A fake is followed at most once in the 5 s after its stop.
    """
    slot, start, end = row["slot"], row["t_s"], row["t_end_window"]
    attempts = [(r["t"], r["t1"]) for r in view.defuse_attempts()
                if r["by"] == view.names.get(slot) and r["result"] == "tap" and "until" not in r]
    ps = blob.get("player_state") or {}
    for t, stop, planter in ps.get("plants") or []:
        if planter != slot or stop is None:
            continue
        finished = any(e["s"] == "planted" and abs(e["t"] - stop) <= 0.001
                       and e.get("slot", slot) == slot for e in ps.get("spike") or [])
        if not finished:
            attempts.append((t, stop))
    stops = [stop for t, stop in attempts if start <= t < end and t <= stop <= end
             and rnd.alive(slot, t) and any(view.alive(s, t) for s in view.enemies(slot))]
    followed = sum(any(stop <= e["t"] <= min(stop + 5, end) and e["enemies"]
                       for e in engagements) for stop in stops)
    return {"fakes": len(stops), "fake_followed": followed if stops else None}


def ult_caps(blobs_by_round: dict, agents: dict) -> dict[int, int]:
    """DB-free match cap pass from app/services/replay_util.py:unused_ults.

    extract._ults records cap_seen via _ult_cap. Use that same proof directly
    from the blobs, then take its match maximum, without importing the service.
    Agents identify match slots; no agent-table cost is assumed without a use.
    """
    caps = {}
    for blob in blobs_by_round.values():
        ps = blob.get("player_state") or {}
        for slot in agents:
            cap = _ult_cap(ps, slot)
            if cap:
                key = int(slot)
                caps[key] = max(caps.get(key, 0), int(cap))
    return caps


def util(rnd, blob: dict, slot: int, t_s: float, t_death: float | None, cap: int | None) -> dict:
    """Held non-ult charges, one use per built cast, and proven ult readiness (R9)."""
    ps = blob.get("player_state") or {}
    charges = {key: line for key, line in ((ps.get("charges") or {}).get(str(slot)) or {}).items()
               if key != "X"}

    def held(t):
        # Like extract._held, but preserve missing evidence instead of reading it
        # as zero (player_state.py's charges contract: nothing before first point).
        if not charges:
            return None
        total = 0
        for line in charges.values():
            points = [p for p in line if p["t"] <= t]
            if not points or points[-1].get("n") is None:
                return None
            total += points[-1]["n"]
        return total

    stop = min(t_death, rnd.t_decided) if t_death is not None else rnd.t_decided
    points = _ult_at(ps, slot, t_s)
    return {"held_start": held(t_s), "held_at_death": held(t_death) if t_death is not None else None,
            "used": sum(c.by == slot and t_s <= c.t <= stop for c in build_casts(rnd, blob)),
            "ult_ready": points >= cap if cap is not None and points is not None else None}


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
