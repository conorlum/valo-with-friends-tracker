"""Quiet rope travel on measured landings; ordinary movement keeps its own topology.

World position-z is metres in the replay frame, not feet height or a retired authoring
floor label. All runtime routes come from a verified, enabled per-map artifact.
"""
from __future__ import annotations

import math
import numpy as np

CONSUMER = 'quiet_rope_v1'
MAPS = frozenset({'Split', 'Summit', 'Abyss'})


def landing_node(geo, cell, landing, *, xy=None):
    from app.replays.map_feature_state import known
    if geo.heights is None:
        return None, 'rope landing requires measured heights'
    if not isinstance(landing, dict):
        return None, 'invalid landing'
    value, tolerance = known(landing.get('world_z')), known(landing.get('tolerance'))
    if value is None or tolerance is None or not math.isfinite(value) or not math.isfinite(tolerance) \
            or not 0 < tolerance <= .5 or landing['world_z'].get('unit') != 'm' \
            or landing['tolerance'].get('unit') != 'm':
        return None, 'landing needs world position-z and tolerance up to 0.5 metres'
    if geo.adaptive and xy is None:
        return None, 'mixed-grid landing needs its exact XY'
    unresolved = geo.unresolved_at_px(*xy) if geo.adaptive else geo.unresolved[cell] or geo.heights.unresolved.ravel()[cell]
    if not geo.walk.ravel()[cell] or unresolved:
        return None, 'landing ground/height unresolved'
    nodes = geo.nodes_at_px(*xy) if geo.adaptive else geo.node_of[cell]
    nodes = nodes[nodes >= 0]
    world = geo.node_z[nodes] + geo.heights.origin_z / 10
    matches = nodes[np.isfinite(world) & (np.abs(world - value) <= tolerance)]
    if len(matches) != 1:
        return None, 'landing height missing or ambiguous'
    return int(matches[0]), None


def readiness(geo, mf, members):
    from app.control import features as cf
    reasons = []
    if geo.name not in MAPS or geo.heights is None:
        reasons.append('quiet ropes require a supported map and measured height asset')
    features = {f['id']: f for f in mf.get('features', [])}
    routes = [r for r in mf.get('routes', []) if r.get('owner') in members]
    for fid in members:
        f = features.get(fid, {})
        if f.get('preset') != 'vertical_rope' or f.get('transitions') or any(
                s.get('blocks_sight') or s.get('blocks_movement') for s in f.get('states', [])):
            reasons.append(f'{fid}: quiet consumer only supports static nonblocking ropes')
        if not any(r.get('owner') == fid for r in routes):
            reasons.append(f'{fid}: route missing')
    for r in routes:
        if r.get('kind') != 'rope' or r.get('states') is not None or r.get('access') != 'endpoint_only':
            reasons.append(f"{r['id']}: only unconditional endpoint ropes are supported")
        if not all(e.get('landing') for e in r.get('endpoints', [])):
            reasons.append(f"{r['id']}: measured landings missing")
        # Quiet directions are separate from the physical/observed route directions.
        if not r.get('quiet_directions'):
            reasons.append(f"{r['id']}: explicit quiet directions and costs required")
        for cut in r.get('quiet_cuts', []):
            nodes = []
            for point in (cut.get('from', {}), cut.get('to', {})):
                node, why = cf._point_node(geo, mf, point.get('uv'), landing=point.get('landing'))
                if why:
                    reasons.append(f"{r['id']}: quiet cut {why}")
                nodes.append(node)
            if None not in nodes:
                cells = geo.node_cell[nodes]
                delta = np.abs(np.array(divmod(int(cells[0]), 128)) - divmod(int(cells[1]), 128))
                if max(delta) > 1 or nodes[0] == nodes[1]:
                    reasons.append(f"{r['id']}: quiet cut must be a local directed connector")
    sub = {**mf, 'routes': [dict(r, directions=r.get('quiet_directions', [])) for r in routes]}
    arcs, pending = cf.compile_routes(geo, sub)
    reasons += pending
    for a in arcs:
        if a.cost_s is None or not math.isfinite(a.cost_s) or a.cost_s <= 0:
            reasons.append(f'{a.route}: finite positive quiet travel time required')
    return arcs, reasons


class QuietTopology:
    """Use reviewed seconds for quiet ropes, preserving walk/trip/arrival-parent rules.

    A cut names directed measured node pairs for the rope's local connector only.
    Ordinary observed movement and all non-quiet queries still use the base topology.
    """
    def __init__(self, base, arcs, cuts=()):
        self.base, self.arcs, self.cuts = base, tuple(arcs), frozenset(cuts)
        if not hasattr(base, 'in_from'):
            raise ValueError('quiet rope topology requires measured floor nodes')
        self.cost = base.in_cost_quiet.copy()
        for src, dst in self.cuts:
            self.cost[dst, base.in_from[dst] == src] = np.inf

    def __getattr__(self, name):
        return getattr(self.base, name)

    def spread(self, reached, room, free, t, straight, links, parents=False, solid=None, quiet=False):
        if not quiet:
            return self.base.spread(reached, room, free, t, straight, links, parents, solid, quiet)
        # NodeTopology owns the vectorised relaxation and the parent selection.
        # A shallow copy keeps cached geometry and trip cuts isolated from other consumers.
        import copy
        topo = copy.copy(self.base)
        topo.in_cost_quiet = self.cost
        timed = []
        for link in links:
            a, b, one_way = link[:3]
            for src, dst in [(a, b)] + ([] if one_way else [(b, a)]):
                if (src, dst) not in self.cuts:
                    timed.append((src, dst, True, *link[3:]))
        timed += [(a.src, a.dst, True, a.cost_s) for a in self.arcs]
        return topo.spread(reached, room, free, t, straight, timed, parents, solid, quiet=True)


def local_rope_cuts(geo, base, arcs):
    """Separate local rope levels without assigning every unsampled approach to the bottom.

    Unknown leaves use their nearest measured walking support to choose a cut side, never
    a fabricated height. Distance follows existing walk edges, not XY across a wall.
    Only the local level boundary is cut. Bundle qualification also checks that this
    approximation has not isolated a landing from its pre-existing main walking area.
    """
    from app.control.geometry import CELL
    from app.control.engine import UNKNOWN_MPS
    cuts = set()
    dst = np.broadcast_to(np.arange(geo.n)[:, None], base.in_from.shape)
    valid = (base.in_from < geo.n) & np.isfinite(base.in_cost_quiet)
    src, targets = base.in_from[valid], dst[valid]
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra
    graph = csr_matrix((base.in_cost_quiet[valid], (src, targets)), shape=(geo.n, geo.n))
    sides = {}
    for arc in arcs:
        low, high = sorted((float(geo.node_z[arc.src]), float(geo.node_z[arc.dst])))
        if not math.isfinite(low) or not math.isfinite(high) or high - low < 1.5:
            continue
        center = (geo.centres[arc.src] + geo.centres[arc.dst]) / 2
        # An ordinary walk escaping this neighbourhood already costs at least the rope ride.
        # Scale the radius in metres/time, rather than assuming the same minimap scale everywhere.
        radius = max(2 * CELL, float(np.linalg.norm(geo.centres[arc.src] - geo.centres[arc.dst])) / 2 +
                     arc.cost_s * UNKNOWN_MPS / (2 * geo.m_per_px) + CELL)
        local = np.linalg.norm(geo.centres - center, axis=1) <= radius
        key = (low, high)
        if key not in sides:
            measured = np.isfinite(geo.node_z) & getattr(geo, 'walk_n', True)
            upper = measured & (geo.node_z >= (low + high) / 2)
            below = measured & ~upper
            to_high = dijkstra(graph, indices=np.flatnonzero(upper), min_only=True, directed=False)
            to_low = dijkstra(graph, indices=np.flatnonzero(below), min_only=True, directed=False)
            sides[key] = upper | (~measured & (to_high < to_low))
        upper = sides[key]
        unknown = ~np.isfinite(geo.node_z[src]) | ~np.isfinite(geo.node_z[targets])
        height_jump = np.abs(geo.node_z[src] - geo.node_z[targets]) >= 1.5
        crosses = local[src] & local[targets] & (upper[src] != upper[targets]) & (unknown | height_jump)
        cuts.update(zip(src[crosses].tolist(), targets[crosses].tolist()))
    return cuts


def qualified_cuts(geo, mf, members, base, arcs):
    """Share compiler/runtime cuts, preserving explicitly reviewed ordinary walk edges.

    A walk connection can only retain an existing quiet edge, never create one or
    assign a floor. A single unknown leaf may be crossed from measured ground;
    two unknown ends or an ambiguous stacked floor cannot bind. Explicit quiet
    cuts take precedence over these exemptions.
    """
    from app.control import features as cf
    from app.control.adaptive import touching
    from app.replays.map_feature_state import known
    from app.replays.map_feature_schema import _uv
    cuts, reasons = local_rope_cuts(geo, base, arcs), []
    retained_walk = False
    for route in mf.get('routes', []):
        if route.get('owner') not in members:
            continue
        connections = route.get('quiet_walk_connections', [])
        if not isinstance(connections, list) or len(connections) > 64:
            reasons.append(f"{route['id']}: too many or invalid quiet walk connections")
            continue
        for connection in connections:
            nodes, heights = [], []
            connection = connection if isinstance(connection, dict) else {}
            for point in (connection.get('from', {}), connection.get('to', {})):
                point = point if isinstance(point, dict) else {}
                uv, landing = point.get('uv'), point.get('landing', {})
                landing = landing if isinstance(landing, dict) else {}
                z, tol = known(landing.get('world_z')), known(landing.get('tolerance'))
                if not _uv(uv) or z is None or tol is None or not math.isfinite(z) \
                        or not math.isfinite(tol) or not 0 < tol <= .5 \
                        or landing['world_z'].get('unit') != 'm' or landing['tolerance'].get('unit') != 'm':
                    nodes.append(None); continue
                xy = np.asarray(uv, float) * 1024 / 10000
                candidates = geo.nodes_at_px(*xy)
                candidates = candidates[candidates >= 0]
                world = geo.node_z[candidates] + geo.heights.origin_z / 10
                matches = candidates[np.isfinite(world) & (np.abs(world-z) <= tol)]
                # Unknown single-leaf ground is an existing fallback, not a measured landing.
                if len(matches) == 1:
                    node = int(matches[0])
                elif len(candidates) == 1 and np.isnan(world[0]):
                    node = int(candidates[0])
                else:
                    node = None
                nodes.append(node); heights.append(z)
            valid = None not in nodes and len(heights) == 2
            if valid:
                a, b = nodes
                boxes = geo.node_bbox if geo.adaptive else np.column_stack((geo.centres-4, geo.centres+4))
                valid = a != b and geo.walk_n[a] and geo.walk_n[b] and touching(boxes[a], boxes[b]) \
                    and np.isfinite(base.in_cost_quiet[b, base.in_from[b] == a]).any() \
                    and np.isfinite(geo.node_z[nodes]).any() and abs(heights[0]-heights[1]) < 1.5
            if valid:
                cuts.discard(tuple(nodes))
                retained_walk = True
            else:
                reasons.append(f"{route['id']}: quiet walk connection is not an existing local supported walk")
    for route in mf.get('routes', []):
        if route.get('owner') not in members:
            continue
        for cut in route.get('quiet_cuts', []):
            nodes = []
            for point in (cut.get('from', {}), cut.get('to', {})):
                node, why = cf._point_node(geo, mf, point.get('uv'), landing=point.get('landing'))
                if why:
                    reasons.append(f"{route['id']}: quiet cut {why}")
                nodes.append(node)
            if None not in nodes:
                cuts.add(tuple(nodes))
    if retained_walk:
        reasons += walk_bypass_problems(geo, base, arcs, cuts)
    return cuts, reasons


def walk_bypass_problems(geo, base, arcs, cuts):
    """A walk exemption cannot reopen a faster, unqualified rope level change.

    Costs use the runtime's distance scale and unknown speed. Timed rope arcs
    are excluded; a retained edge must stand on its ordinary walking merits.
    Check all routes together because one exemption can bypass another rope.
    """
    from app.control.engine import UNKNOWN_MPS
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra
    dst = np.broadcast_to(np.arange(geo.n)[:, None], base.in_from.shape)
    valid = (base.in_from < geo.n) & np.isfinite(base.in_cost_quiet)
    src, targets = base.in_from[valid], dst[valid]
    keep = np.array([(int(a), int(b)) not in cuts for a, b in zip(src, targets)])
    graph = csr_matrix((base.in_cost_quiet[valid][keep] * geo.cell_m / UNKNOWN_MPS,
                        (src[keep], targets[keep])), shape=(geo.n, geo.n))
    distances, bad = {}, set()
    for arc in arcs:
        if arc.src not in distances:
            distances[arc.src] = dijkstra(graph, indices=arc.src, directed=True,
                                         limit=max(a.cost_s for a in arcs if a.src == arc.src))
        walk = float(distances[arc.src][arc.dst])
        if math.isfinite(walk) and walk + 1e-6 < arc.cost_s:
            bad.add(arc.route)
    return [f'{r}: retained quiet walk bypasses reviewed rope travel time' for r in sorted(bad)]


def walking_exit_problems(geo, base, arcs, cuts):
    """A local heuristic cannot remove an existing connection to the main walking area.

    Pre-existing separate platforms are preserved as separate; timed rope arcs are
    excluded from this check because they must not conceal a removed stair/walk exit.
    """
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components
    dst = np.broadcast_to(np.arange(geo.n)[:, None], base.in_from.shape)
    valid = (base.in_from < geo.n) & np.isfinite(base.in_cost_quiet)
    src, targets = base.in_from[valid], dst[valid]
    def components(keep):
        _, labels = connected_components(csr_matrix((np.ones(int(keep.sum())),
            (src[keep], targets[keep])), shape=(geo.n, geo.n)), directed=False)
        sizes = np.bincount(labels[geo.walk_n], minlength=geo.n)
        return labels, int(np.argmax(sizes))
    before, main = components(np.ones(len(src), bool))
    after, remaining = components(np.array([(int(a), int(b)) not in cuts for a, b in zip(src, targets)]))
    bad = {a.route for a in arcs for n in (a.src, a.dst)
           if before[n] == main and after[n] != remaining}
    return [f'{r}: quiet rope cuts remove the landing walking exit' for r in sorted(bad)]


def topology_for(geo):
    from app.control import topology, features as cf
    from app.replays.map_feature_inputs import read_json
    from app.replays.map_feature_artifacts import FeatureArtifactCorrupt
    base = topology.of(geo)
    if not geo.features:
        return base
    artifact = geo.features
    mf = read_json(artifact.inputs)['runtime']
    members = {m for b in mf.get('bundles', []) if b['id'] in artifact.manifest['active_bundles']
               and b.get('runtime_consumer') == CONSUMER for m in b['members']}
    if not members:
        return base
    arcs, reasons = readiness(geo, mf, members)
    cuts, cut_reasons = qualified_cuts(geo, mf, members, base, arcs)
    reasons += cut_reasons
    reasons += walking_exit_problems(geo, base, arcs, cuts)
    if reasons:
        raise FeatureArtifactCorrupt('; '.join(reasons))
    return QuietTopology(base, arcs, cuts)
