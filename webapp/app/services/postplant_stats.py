"""Pure figures from current post-plant rows supplied by the caller.

Callers enforce the gap fingerprint/revision contract and select the site, spot
and population before aggregation. Values stay in probability units; only pts
formats percentage points. This module never loads or builds a win model.

area_table returns {areas: [row, ...], distinct_n: int, site_mean: dict}.
player_blocks returns side summaries containing total_n, eligible and blocks,
including a summary when none of that side's blocks meets the display minimum.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from app.coach import areas
from app.services import util_values
from app.services.stats_math import cluster_bootstrap_ci

CALCULATION_VERSION = 2
M = 10
MIN_N = 20
CROSSFIRE_DEG = 60.0
PLAYER_MIN_N = 20
PLAYER_ROW_MIN_N = 8


@dataclass(frozen=True)
class RoundInput:
    match_id: object
    replay_uuid: str
    round_number: int
    map_name: str
    asset_key: str
    site: str
    spot: str
    data: dict
    clock_offset: float | None
    slot_players: dict[int, int | None]


def painted_tables(asset_keys) -> dict:
    """Read each layout's paint once per build, including absent paint."""
    return {key: areas.load_painted(key) for key in dict.fromkeys(asset_keys)}


def area_of(asset_key, u, v, z, *, painted_table) -> str:
    found = areas.area_at(asset_key, u, v, z, painted_table=painted_table)
    return found["name"] if found else "Unnamed"


def crossfire_flags(data: dict) -> set[tuple[float, int]]:
    plant = data.get("plant")
    if not plant or plant.get("u") is None or plant.get("v") is None:
        return set()
    groups = defaultdict(list)
    for t, slot, u, v, z, line in data.get("samples", ()):
        side = data.get("sides", {}).get(str(slot))
        if not line or side is None or u is None or v is None:
            continue
        du, dv = u - plant["u"], v - plant["v"]
        if du == dv == 0:  # No bearing at the spike itself.
            continue
        groups[t, side].append((slot, math.degrees(math.atan2(dv, du))))
    flags = set()
    for (t, _), bearings in groups.items():
        for i, (slot, bearing) in enumerate(bearings):
            for other, angle in bearings[i + 1:]:
                separation = abs((bearing - angle + 180) % 360 - 180)
                if other != slot and separation > CROSSFIRE_DEG + 1e-10:
                    flags.update(((t, slot), (t, other)))
    return flags


def kill_swings(round_input: RoundInput, model) -> dict:
    if model is None:
        return {}
    data = round_input.data
    plant = data.get("plant")
    fields = ("t", "killer", "victim", "k_side", "v_side", "before")
    round_data = {"clock_offset": round_input.clock_offset,
                  "plant": plant["t"] if plant else None,
                  "kills": [{key: k[key] for key in fields} for k in data.get("kills", ())]}
    return util_values.swings(round_data, model)


def _usable(r):
    return bool(r.data.get("plant") and r.data.get("sides_known"))


def _identity(r, slot):
    return (r.replay_uuid, r.round_number, slot)


def _same_side(r, slot, side):
    return r.data.get("sides", {}).get(str(slot)) == side


def _included(r, slot, side, include_slot):
    return slot is not None and _same_side(r, slot, side) and include_slot(r, slot)


def _enemy_kill(k):
    ks, vs = k.get("k_side"), k.get("v_side")
    return ks in ("attack", "defense") and vs in ("attack", "defense") and ks != vs


def _counts():
    return {"players": set(), "kills": 0, "deaths": 0, "win_gained": 0.0, "net_win": 0.0}


def _new_area(name):
    return dict(_counts(), name=name, held_s=0.0, line_s=0.0, crossfire_s=0.0,
                time_share_sum=0.0, examples=[], groups=defaultdict(_counts))


def _ratio(value, denominator):
    return value / denominator if denominator else None


def _interval(groups, field):
    if len(groups) < 2:
        return None

    def rate(clustered):
        rows = [row for group in clustered for row in group]
        return _ratio(sum(row[field] for row in rows), sum(len(row["players"]) for row in rows))

    lo, hi = cluster_bootstrap_ci(rate, {key: [row] for key, row in groups.items()})
    return (lo, hi) if math.isfinite(lo) and math.isfinite(hi) else None


def _area_table(rounds, model, *, side, include_slot, tables):
    rows = {}
    distinct = set()
    for r in rounds:
        if not _usable(r):
            continue
        data = r.data
        step = data["step"]
        flags = crossfire_flags(data)
        samples_by_player = defaultdict(list)
        for t, slot, u, v, z, line in data.get("samples", ()):
            if _included(r, slot, side, include_slot):
                samples_by_player[slot].append((t, u, v, z, line))
        for slot, samples in samples_by_player.items():
            identity = _identity(r, slot)
            distinct.add(identity)
            for t, u, v, z, line in samples:
                name = area_of(r.asset_key, u, v, z, painted_table=tables)
                row = rows.setdefault(name, _new_area(name))
                row["players"].add(identity)
                row["groups"][r.match_id]["players"].add(identity)
                row["held_s"] += step
                row["line_s"] += step if line else 0
                row["crossfire_s"] += step if (t, slot) in flags else 0
                row["time_share_sum"] += 1 / len(samples)

        swings = kill_swings(r, model)
        for k in data.get("kills", ()):
            swing = swings.get((round(float(k["t"]), 2), k["killer"], k["victim"]), 0.0)
            for role, prefix, count in (("killer", "k", "kills"), ("victim", "v", "deaths")):
                if role == "killer" and not _enemy_kill(k):
                    continue
                slot = k[role]
                u, v, z = (k.get(prefix + axis) for axis in ("u", "v", "z"))
                if not _included(r, slot, side, include_slot) or u is None or v is None:
                    continue
                name = area_of(r.asset_key, u, v, z, painted_table=tables)
                row = rows.setdefault(name, _new_area(name))
                for target in (row, row["groups"][r.match_id]):
                    target[count] += 1
                    target["win_gained"] += swing if role == "killer" else 0
                    target["net_win"] += swing if role == "killer" else -swing
                if role == "killer" and model is not None:
                    row["examples"].append({
                        "swing": swing,
                        "url": f"/replays/{r.replay_uuid}?round={r.round_number}&t={max(k['t'] - 2, 0):.1f}"})

    total_n = len(distinct)
    site_mean = {field: _ratio(sum(row[field] for row in rows.values()), total_n)
                 for field in ("kills", "win_gained", "net_win")}
    if model is None:
        site_mean.update(win_gained=None, net_win=None)
    output = []
    for name, raw in sorted(rows.items()):
        n = len(raw["players"])
        row = {key: raw[key] for key in ("name", "held_s", "kills", "deaths", "win_gained", "net_win")}
        row.update(n=n, kd=_ratio(raw["kills"], raw["deaths"]),
                   deaths_per_min=_ratio(raw["deaths"] * 60, raw["held_s"]),
                   line_share=_ratio(raw["line_s"], raw["held_s"]),
                   crossfire_share=_ratio(raw["crossfire_s"], raw["held_s"]),
                   time_share=_ratio(raw["time_share_sum"], n),
                   coloured=name != "Unnamed" and n >= MIN_N,
                   marker=name != "Unnamed",
                   examples=sorted(raw["examples"], key=lambda e: (-e["swing"], e["url"]))[:3])
        row["deaths_per_n"] = _ratio(raw["deaths"], n)
        for field, ci in (("kills", "kills_ci"), ("win_gained", "gained_ci"), ("net_win", "net_ci")):
            available = field == "kills" or model is not None
            row[field] = raw[field] if available else None
            row[field + "_per_n"] = _ratio(raw[field], n) if available else None
            mean = site_mean[field]
            row["shrunk_" + field + "_per_n"] = (
                (raw[field] + M * mean) / (n + M) if n and mean is not None and available else None)
            row[ci] = _interval(raw["groups"], field) if n and available else None
        output.append(row)
    return {"areas": output, "distinct_n": total_n, "site_mean": site_mean}


def area_table(rounds, model, *, side, include_slot=lambda r, slot: True) -> dict:
    """Aggregate a caller-filtered site/side/spot/population, including event-only areas.

    n counts distinct round/slot visits, held_s counts samples times their step,
    and time_share averages each visiting player's fraction of round samples.
    Site shrinkage divides total events by distinct player-post-plants, rather
    than by summed area visits. Intervals resample whole matches on raw rates.
    A zero-death K/D is None (no finite ratio).
    """
    rounds = list(rounds)
    tables = painted_tables(r.asset_key for r in rounds)
    return _area_table(rounds, model, side=side, include_slot=include_slot, tables=tables)


def site_totals(rounds, model, side) -> dict:
    """Unshrunk, located enemy kills and all deaths, independently of areas (R14)."""
    totals = {"kills": 0, "deaths": 0, "win_gained": 0.0, "net_win": 0.0}
    distinct = set()
    for r in rounds:
        if not _usable(r):
            continue
        distinct.update(_identity(r, s[1]) for s in r.data.get("samples", ()) if _same_side(r, s[1], side))
        swings = kill_swings(r, model)
        for k in r.data.get("kills", ()):
            swing = swings.get((round(float(k["t"]), 2), k["killer"], k["victim"]), 0.0)
            if (_enemy_kill(k) and _same_side(r, k["killer"], side)
                    and k.get("ku") is not None and k.get("kv") is not None):
                totals["kills"] += 1
                totals["win_gained"] += swing
                totals["net_win"] += swing
            if _same_side(r, k["victim"], side) and k.get("vu") is not None and k.get("vv") is not None:
                totals["deaths"] += 1
                totals["net_win"] -= swing
    if model is None:
        totals.update(win_gained=None, net_win=None)
    totals["distinct_n"] = len(distinct)
    return totals


def player_blocks(rounds, player_slots, model) -> list[dict]:
    """Side summaries, with visible layout/site blocks and eligibility over ALL blocks.

    player_slots is already deduplicated and agent-filtered by the caller. No
    account identities are inferred here. A player's area held_share is its
    seconds divided by that player's total held seconds in the block.
    """
    rounds = list(rounds)
    groups = defaultdict(list)
    populations = defaultdict(set)

    def selected(r, slot):
        return player_slots.get((r.replay_uuid, r.round_number)) == slot

    for r in rounds:
        if not _usable(r):
            continue
        slot = player_slots.get((r.replay_uuid, r.round_number))
        side = r.data.get("sides", {}).get(str(slot))
        if side not in ("attack", "defense"):
            continue
        groups[r.asset_key, r.site, side].append(r)
        if any(s[1] == slot for s in r.data.get("samples", ())):
            populations[side].add(_identity(r, slot))
    tables = painted_tables(r.asset_key for r in rounds)
    blocks = defaultdict(list)
    for (asset_key, site, side), group in sorted(groups.items()):
        n = len({_identity(r, s[1]) for r in group for s in r.data.get("samples", ())
                 if selected(r, s[1])})
        if n < PLAYER_ROW_MIN_N:
            continue
        table = _area_table(group, model, side=side, include_slot=selected, tables=tables)
        held = sum(row["held_s"] for row in table["areas"])
        for row in table["areas"]:
            row["held_share"] = _ratio(row["held_s"], held)
        blocks[side].append(dict(table, asset_key=asset_key, map_name=group[0].map_name,
                                 site=site, side=side, n=n))
    return [{"side": side, "total_n": len(populations[side]),
             "eligible": len(populations[side]) >= PLAYER_MIN_N, "blocks": blocks[side]}
            for side in ("attack", "defense") if populations[side]]


def pts(fraction) -> str:
    return f"{fraction * 100:+.1f} pts"


def sentence(block, area_rows) -> str | None:
    """Describe the player's holding and Everyone's best coloured, shrunk net value."""
    held = [row for row in block["areas"] if row.get("held_s", 0) > 0]
    valued = [row for row in area_rows if row.get("coloured")
              and row.get("shrunk_net_win_per_n") is not None]
    if not held or not valued:
        return None
    most = max(held, key=lambda row: row["held_s"])
    best = max(valued, key=lambda row: row["shrunk_net_win_per_n"])
    share = most["held_share"]
    return (f"You hold from {most['name']} {share:.0%} of post-plant time; "
            f"{best['name']} gets {pts(best['shrunk_net_win_per_n'])} net win per post-plant (n={best['n']})")
