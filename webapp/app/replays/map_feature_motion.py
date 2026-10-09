"""Dormant sliding-door geometry primitives and the tagger preview's Python reference.

An optional feature.sliding record names open/closed states and an axis. Horizontal motion uses an open_center
point. Vertical motion keeps the footprint fixed and lowers a ground-relative blocking band. The closed state's
polygon/polyline footprint is both the panel at closure and the doorway aperture. Translate the panel
linearly from its open centre to its closed centre, then clip coverage to that aperture. This models a
single rigid panel retracting horizontally or rising above the opening; it is not a physics or
bullet-penetration model. No production replay consumer calls these functions.
"""
from __future__ import annotations

import math


def closed_state(feature: dict) -> dict | None:
    slide = feature.get("sliding")
    name = slide.get("closed_state") if isinstance(slide, dict) else None
    states = feature.get("states")
    return next((s for s in states if isinstance(s, dict) and s.get("name") == name), None) if isinstance(states, list) else None


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
    axis = slide.get("axis", "horizontal")
    if axis not in ("horizontal", "vertical"):
        return ["choose whether the panel slides across the map or descends from above"]
    if axis == "vertical":
        entries = closed.get("sight") or []
        if not isinstance(entries, list) or not all(isinstance(o, dict) for o in entries):
            return ["draw valid closed sight edges for the descending panel"]
        for entry in entries:
            rep = ms.Report()
            ms._geometry(rep, "sight", entry.get('geometry'), ("polygon", "polyline"))
            if rep.errors:
                return ["draw valid closed sight edges for the descending panel"]
        return []
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
    if feature["sliding"].get("axis") == "vertical":
        return {**panel, "uv": [list(p) for p in panel["uv"]]}
    centre = panel_center(feature)
    opened = feature["sliding"]["open_center"]["uv"]
    offset = [(opened[i] - centre[i]) * (1 - closure) for i in (0, 1)]
    return {**panel, "uv": [[p[0] + offset[0], p[1] + offset[1]] for p in panel["uv"]]}


def coverage(feature: dict, closure: float):
    """Planar coverage: clipped for horizontal motion, fixed for vertical motion. Not a vertical sight mask."""
    from app.control.features import raster

    panel = pose(feature, closure)
    if panel is None:
        return None
    return raster(panel) & raster(closed_state(feature)["footprint"])


def vertical_problems(feature: dict) -> list[str]:
    """Metric inputs for a descending panel. Approximate player heights are never metres."""
    if not isinstance(feature.get("sliding"), dict) or feature['sliding'].get("axis") != "vertical":
        return []
    slide = feature["sliding"]
    height = fs_known_metres(slide.get("open_clearance"))
    problems = []
    if height is None or height <= 0:
        problems.append("open clearance in metres unresolved (player-height estimate is not a metre measurement)")
    closed = closed_state(feature)
    if closed is None:
        return problems
    primary = closed.get("sight_bounds")
    primary = primary if isinstance(primary, dict) else {}
    primary_lo = fs_known_metres(primary.get("bottom"))
    sight = closed.get("sight") or []
    sight = sight if isinstance(sight, list) else []
    entries = [primary] + [o.get("bounds") if isinstance(o.get("bounds"), dict) else {} for o in sight if isinstance(o, dict)]
    for bounds in entries:
        lo, hi = fs_known_metres(bounds.get("bottom")), fs_known_metres(bounds.get("top"))
        if bounds.get("ref") != "ground" or lo is None or hi is None or not (0 <= lo < hi):
            problems.append("descending panel needs finite ground-relative bottom/top bounds")
            break
        if height is not None and primary_lo is not None and lo + height - primary_lo < hi:
            problems.append("fully open clearance must reach the top of every doorway sight blocker")
            break
    return problems


def fs_known_metres(value) -> float | None:
    from app.replays.map_feature_state import known
    v = known(value)
    return v if v is not None and math.isfinite(v) and value.get("unit") == "m" else None


def vertical_bounds(feature: dict, closure: float, bounds: dict | None = None) -> dict | None:
    """Clip a descending panel to its closed height band; return a zero-width band when fully raised.

    All parts move by the same vertical offset, defined by the main panel's bottom and open clearance.
    Extra sight edges keep their authored geometry and bounds. A zero-width band means no sight blocker.
    """
    if geometry_problems(feature) or vertical_problems(feature) or not isinstance(closure, (int, float)) \
            or isinstance(closure, bool) or not math.isfinite(closure) \
            or feature['sliding'].get('axis') != 'vertical':
        return None
    closed = closed_state(feature)
    primary = closed['sight_bounds']
    target = bounds if bounds is not None else primary
    if not isinstance(target, dict):
        return None
    lo, hi = fs_known_metres(target.get('bottom')), fs_known_metres(target.get('top'))
    if target.get('ref') != 'ground' or lo is None or hi is None or not (0 <= lo < hi):
        return None
    offset = (fs_known_metres(feature['sliding']['open_clearance']) - fs_known_metres(primary['bottom'])) \
        * (1 - min(max(closure, 0.0), 1.0))
    from app.replays.map_feature_schema import known
    return {**target, 'bottom': known(min(lo + offset, hi), 'm'), 'top': known(hi, 'm')}


def sample_effects(geo, mf: dict, feature: dict, closure: float):
    """Dormant consumer primitive: (movement Effects, bounded sight occluders) at a sampled closure.

    Check the whole doorway's placement before sampling. Horizontal retraction needs no walkable floor
    behind the wall. Descent changes the height band, requires measured heights for vision and a verified
    movement threshold for partial traversal. Unknown values remain pending. No permanent geometry mutation.
    """
    import numpy as np
    from app.control import features as cf

    effects = cf.Effects(np.zeros(geo.n, bool))
    problems = geometry_problems(feature)
    if problems:
        effects.pending += problems
        return effects, []
    if feature['sliding'].get('axis') == 'vertical':
        return _vertical_effects(geo, mf, feature, closure, effects)
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


def _vertical_effects(geo, mf, feature, closure, effects):
    from app.control import features as cf
    problems = vertical_problems(feature)
    band = vertical_bounds(feature, closure)
    if problems or band is None:
        effects.pending += problems or ['vertical progress unresolved']
        return effects, []
    closed = closed_state(feature)
    entries = [{'geometry': closed['footprint'], 'bounds': closed['sight_bounds']}] + list(closed.get('sight') or [])
    for i, entry in enumerate(entries):
        p = cf._place_shape(geo, entry['geometry'], f'features.{feature.get("id")}.sliding.aperture[{i}]')
        effects.pending += [f'{r["path"]}: {r["code"]}' for r in p.reasons]
    if effects.pending:
        return effects, []
    gap = fs_known_metres(band['bottom'])
    full_open = min(max(closure, 0.0), 1.0) == 0
    full_closed = min(max(closure, 0.0), 1.0) == 1
    if closed.get('blocks_movement') and not full_open:
        clearance = fs_known_metres(feature['sliding'].get('movement_clearance'))
        if not full_closed and (clearance is None or clearance <= 0):
            effects.pending.append('partial vertical movement needs a verified movement clearance')
        elif full_closed or gap < clearance:
            bound = cf.feature_floor_nodes(geo, mf, feature, cf.to_grid(cf.raster(closed['footprint'])).ravel())
            effects.blocked[bound.nodes] = True
            effects.pending += bound.pending
    occluders = []
    if not closed.get('blocks_sight'):
        return effects, occluders
    if geo.heights is None:
        effects.pending.append('vertical sight needs measured height geometry; flat preview cannot test seeing under a panel')
        return effects, occluders
    for entry in entries:
        sampled = vertical_bounds(feature, closure, entry['bounds'])
        if fs_known_metres(sampled['bottom']) >= fs_known_metres(sampled['top']):
            continue
        occluder, why = cf.resolve_bounds(geo, mf, {'bounds': sampled}, cf.to_px(cf.raster(entry['geometry'])), feature.get('id'))
        if why:
            effects.pending.append(why)
        elif occluder:
            occluders.append(occluder)
    return effects, occluders


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
