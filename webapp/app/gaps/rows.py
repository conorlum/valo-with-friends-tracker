"""Gaps as storage rows (timing-gaps spec, section 7)."""

from __future__ import annotations


def to_rows(gaps, rnd, geo) -> list[dict]:
    seq_of = {id(g): i for i, g in enumerate(gaps)}
    out = []
    for i, g in enumerate(gaps):
        side = rnd.group_side.get(g.team)
        out.append({
            "seq": i, "kind": g.kind, "map": geo.name, "victim_slot": g.victim, "victim_side": side,
            "t_open": g.t_open, "t_last_exposed": g.t_last_exposed, "t_close": g.t_close,
            "spot_cell": int(geo.node_cell[g.spot]) if geo.heights is not None else int(g.spot),
            "victim_cell": int(geo.node_cell[g.victim_node]) if geo.heights is not None else int(g.victim_node),
            "distance_m": g.distance_m, "angle_deg": g.angle_deg,
            "qualified_s": round(g.qualified_s, 3) if g.kind == "predicted" else None, "flicker": g.flicker,
            "cause": g.cause, "cause_detail": g.cause_detail,
            "choke_seq": None if g.choke_seq is None else list(g.choke_seq), "route": g.route,
            "candidate_slots": sorted(g.candidates), "candidate_distances": {str(k): v for k, v in g.candidates.items()},
            "checked_at": g.checked_at or None, "stood_at": g.stood_at, "stood_by": g.stood_by,
            "shot_at": g.shot_at, "shot_by": g.shot_by, "killed_at": g.killed_at, "killed_by": g.killed_by,
            "victim_won_at": g.victim_won_at, "context": g.context,
            "linked_seq": seq_of.get(id(g.linked)) if g.linked is not None else None,
        })
    return out
