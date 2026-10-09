"""Dormant sliding-door geometry primitives and the tagger preview's Python reference.

An optional feature.sliding record names open/closed states and an open_center point. The closed state's
polygon/polyline footprint is both the panel at closure and the doorway aperture. Translate the panel
linearly from its open centre to its closed centre, then clip coverage to that aperture. This models a
single rigid panel retracting behind a wall, with raster-sized jumps in coverage; it is not a physics or
bullet-penetration model. No production replay consumer calls these functions.
"""
from __future__ import annotations

import math


def closed_state(feature: dict) -> dict | None:
    name = (feature.get("sliding") or {}).get("closed_state")
    return next((s for s in feature.get("states", []) if isinstance(s, dict) and s.get("name") == name), None)


def geometry_problems(feature: dict) -> list[str]:
    """Unresolved or unsupported motion inputs, without substituting guessed facts."""
    from app.replays import map_feature_schema as ms

    slide = feature.get("sliding")
    if not isinstance(slide, dict):
        return ["sliding definition missing"]
    states = feature.get("states")
    if not isinstance(states, list) or not all(isinstance(s, dict) and isinstance(s.get("name"), str) for s in states):
        return ["sliding needs distinct existing open and closed states"]
    names = {s.get("name") for s in states}
    if not isinstance(slide.get("open_state"), str) or not isinstance(slide.get("closed_state"), str) \
            or slide.get("open_state") not in names or slide.get("closed_state") not in names \
            or slide.get("open_state") == slide.get("closed_state"):
        return ["sliding needs distinct existing open and closed states"]
    closed = closed_state(feature)
    panel = (closed or {}).get("footprint")
    rep = ms.Report()
    ms._geometry(rep, "panel", panel, ("polygon", "polyline"))
    if rep.errors:
        return ["draw a valid closed polygon or polyline footprint first"]
    rep = ms.Report()
    ms._geometry(rep, "open_center", slide.get("open_center"), ("point",))
    if rep.errors:
        return ["open panel centre unresolved"]
    if closed.get("sight"):
        return ["separate closed sight edges are not supported by the sliding panel preview"]
    cx, cy = panel_center(feature)
    if slide["open_center"]["uv"] == [cx, cy]:
        return ["open and closed panel centres must differ"]
    return []


def panel_center(feature: dict) -> tuple[float, float]:
    points = closed_state(feature)["footprint"]["uv"]
    return tuple(sum(p[i] for p in points) / len(points) for i in (0, 1))


def pose(feature: dict, closure: float) -> dict | None:
    """0 fully open; 1 fully closed. The translated panel may extend outside the aperture/map."""
    if not isinstance(closure, (int, float)) or isinstance(closure, bool) or not math.isfinite(closure) \
            or geometry_problems(feature):
        return None
    closure = min(max(closure, 0.0), 1.0)
    panel = closed_state(feature)["footprint"]
    centre = panel_center(feature)
    opened = feature["sliding"]["open_center"]["uv"]
    offset = [(opened[i] - centre[i]) * (1 - closure) for i in (0, 1)]
    return {**panel, "uv": [[p[0] + offset[0], p[1] + offset[1]] for p in panel["uv"]]}


def coverage(feature: dict, closure: float):
    """256x256 boolean coverage, clipped to the authored doorway; None means unresolved."""
    from app.control.features import raster

    panel = pose(feature, closure)
    if panel is None:
        return None
    return raster(panel) & raster(closed_state(feature)["footprint"])


def sample_effects(geo, mf: dict, feature: dict, closure: float):
    """Dormant consumer primitive: (movement Effects, bounded sight occluders) at a sampled closure.

    Check the whole doorway's placement before using any partial mask. The open panel behind a wall needs
    no walkable floor of its own. Vision still requires the closed panel's verified vertical bounds; unknown
    bounds remain pending. No all-height default and no permanent geometry mutation.
    """
    import numpy as np
    from app.control import features as cf

    effects = cf.Effects(np.zeros(geo.n, bool))
    problems = geometry_problems(feature)
    if problems:
        effects.pending += problems
        return effects, []
    if coverage(feature, 0).any():
        effects.pending.append("open panel still covers the doorway: open centre needs more travel")
        return effects, []
    closed = closed_state(feature)
    placed = cf._place_shape(geo, closed["footprint"], f'features.{feature.get("id")}.sliding.aperture')
    if placed.reasons:
        effects.pending += [f'{r["path"]}: {r["code"]}' for r in placed.reasons]
        return effects, []
    cells = coverage(feature, closure)
    if cells is None:
        effects.pending.append("sliding progress unresolved")
        return effects, []
    if not cells.any():
        return effects, []
    if closed.get("blocks_movement"):
        bound = cf.feature_floor_nodes(geo, mf, feature, cf.to_grid(cells).ravel())
        effects.blocked[bound.nodes] = True
        effects.pending += bound.pending
    if not closed.get("blocks_sight"):
        return effects, []
    occluder, why = cf.resolve_bounds(geo, mf, {"bounds": closed.get("sight_bounds")},
                                     cf.to_px(cells), feature.get("id"))
    if why:
        effects.pending.append(why)
    return effects, [occluder] if occluder else []


def closure_at(feature: dict, state: dict, t: float) -> float | None:
    """Sample a full reducer state from evaluate(..., until=t), including reversals.

    Broken/unrelated states return None. A duration must be finite and positive. This uses the reducer's
    existing symmetric reversal policy (same speed as the interrupted transition), not an inferred opening
    duration. An observed moving state without a start time remains unresolved.
    """
    slide = feature.get("sliding") or {}
    opened, closed = slide.get("open_state"), slide.get("closed_state")
    if not opened or not closed or opened == closed or not isinstance(t, (int, float)) or isinstance(t, bool) or not math.isfinite(t):
        return None
    motion = state.get("motion")
    if motion:
        duration = motion.get("duration")
        start = motion.get("start")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) \
                or duration <= 0 or not isinstance(start, (int, float)) or isinstance(start, bool) or not math.isfinite(start):
            return None
        if (motion.get("from"), motion.get("to")) not in ((opened, closed), (closed, opened)):
            return None
        progress = min(max((t - start) / duration, 0.0), 1.0)
        return progress if motion["to"] == closed else 1 - progress
    if state.get("state") == opened:
        return 0.0
    if state.get("state") == closed:
        return 1.0
    return None
