"""Quiet rope travel on measured landings; ordinary movement keeps its own topology.

World position-z is metres in the replay frame, not feet height or a retired authoring
floor label. All runtime routes come from a verified, enabled per-map artifact.
"""
from __future__ import annotations

import math
import numpy as np

CONSUMER = 'quiet_rope_v1'
MAPS = frozenset({'Split', 'Summit', 'Abyss'})


def landing_node(geo, cell, landing):
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
    if not geo.walk.ravel()[cell] or geo.unresolved[cell] or geo.heights.unresolved.ravel()[cell]:
        return None, 'landing ground/height unresolved'
    nodes = geo.node_of[cell]
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
    cuts = set()
    # Each quiet cut is two measured endpoint/access records, not unstable node IDs.
    for r in mf.get('routes', []):
        if r.get('owner') not in members:
            continue
        for cut in r.get('quiet_cuts', []):
            nodes = []
            for point in (cut.get('from', {}), cut.get('to', {})):
                node, why = cf._point_node(geo, mf, point.get('uv'), landing=point.get('landing'))
                if why:
                    reasons.append(f"{r['id']}: quiet cut {why}")
                nodes.append(node)
            if None not in nodes:
                cuts.add(tuple(nodes))
    if reasons:
        raise FeatureArtifactCorrupt('; '.join(reasons))
    return QuietTopology(base, arcs, cuts)
