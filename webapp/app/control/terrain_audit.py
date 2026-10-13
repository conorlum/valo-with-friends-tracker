"""Read-only ground-graph facts for owner-supplied, approximate terrain regions.

Labels do not grant edges or elevations. A quiet large STEP descent is a review
candidate, while an observed grounded SLIDE can legitimately remain quiet.
"""
from __future__ import annotations

import math
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import breadth_first_order

from app.control import heights as hc, topology


def audit(geo, regions, *, context_px=16):
    if geo.heights is None:
        raise ValueError('terrain audit requires an explicit height asset')
    if not math.isfinite(context_px) or not 0 <= context_px <= 128:
        raise ValueError('invalid terrain context margin')
    checked = []
    identities = set()
    for region in regions:
        if not isinstance(region, dict):
            raise ValueError('terrain regions must be objects')
        identity, bbox = region.get('id'), region.get('bbox_px')
        if not isinstance(identity, str) or not identity or identity in identities:
            raise ValueError('terrain region IDs must be nonempty and unique')
        if not isinstance(bbox, list) or len(bbox) != 4 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox
        ) or not (0 <= bbox[0] < bbox[2] <= 1024 and 0 <= bbox[1] < bbox[3] <= 1024):
            raise ValueError('invalid terrain region bbox_px')
        identities.add(identity)
        checked.append(region)
    topo = topology.of(geo)
    src, dst = topo.src, topo.dst
    quiet = {(int(s), int(d)) for d in range(geo.n)
             for s, cost in zip(topo.in_from[d], topo.in_cost_quiet[d])
             if s < geo.n and np.isfinite(cost)}
    node_of = geo.space_nodes if geo.adaptive else geo.node_of
    kinds = {}
    for array in (geo.heights.edges, geo.heights.fine_edges if geo.adaptive else []):
        for a, fa, b, fb, kind in array:
            kinds[(int(node_of[a, fa]), int(node_of[b, fb]))] = int(kind)
    edge_kind = np.array([kinds.get((int(a), int(b)), -1) for a, b in zip(src, dst)], int)
    edge_quiet = np.array([(int(a), int(b)) in quiet for a, b in zip(src, dst)], bool)
    drop = geo.node_z[src] - geo.node_z[dst]
    large = np.isfinite(drop) & (drop > hc.SILENT_DROP_M + 1e-9)
    unknown = ~np.isfinite(geo.node_z[src]) | ~np.isfinite(geo.node_z[dst])
    # A noisy direct drop may have a perfectly valid quiet route via stairs/landings.
    # Exclude unknown fallback so a path through missing ground is never called proven.
    known_quiet = edge_quiet & ~unknown
    ground_graph = csr_matrix((np.ones(int(known_quiet.sum()), bool),
        (src[known_quiet], dst[known_quiet])), shape=(geo.n, geo.n))
    reachable = {}
    spaces = geo.node_space if geo.adaptive else geo.node_cell
    node_bbox = geo.node_bbox if geo.adaptive else np.column_stack((
        spaces % 128 * 8, spaces // 128 * 8, spaces % 128 * 8 + 8, spaces // 128 * 8 + 8))
    counts = (geo.space_nodes >= 0).sum(1) if geo.adaptive else geo.heights.floor_count().ravel()
    results = []
    for region in checked:
        x0, y0, x1, y1 = region['bbox_px']
        mask = geo.walk_n & (node_bbox[:, 2] > x0-context_px) & (node_bbox[:, 0] < x1+context_px) \
            & (node_bbox[:, 3] > y0-context_px) & (node_bbox[:, 1] < y1+context_px)
        touches = mask[src] | mask[dst]
        candidates = touches & large & edge_quiet & (edge_kind == hc.EDGE_STEP)
        detail = []
        for i in np.flatnonzero(touches & large):
            a, b = int(src[i]), int(dst[i])
            alternative = None
            if not edge_quiet[i]:
                if a not in reachable:
                    reachable[a] = set(breadth_first_order(ground_graph, a, directed=True, return_predecessors=False).tolist())
                alternative = b in reachable[a]
            detail.append({'from_node': a, 'to_node': b, 'from_bbox_px': node_bbox[a].tolist(),
                'to_bbox_px': node_bbox[b].tolist(), 'from_world_m': float(geo.node_z[a]+geo.heights.origin_z/10),
                'to_world_m': float(geo.node_z[b]+geo.heights.origin_z/10),
                'drop_m': round(float(drop[i]), 4), 'kind': int(edge_kind[i]), 'quiet': bool(edge_quiet[i]),
                'known_quiet_alternative_exists': alternative})
        results.append({'id': region['id'], 'description': region.get('description', ''),
            'bbox_px': region['bbox_px'], 'context_px': context_px,
            'walkable_nodes': int(mask.sum()), 'unresolved_nodes': int((mask & ~np.isfinite(geo.node_z)).sum()),
            'unresolved_area_px2': float(geo.node_area[mask & ~np.isfinite(geo.node_z)].sum()*64),
            'multiple_floor_footprints': int(len(np.unique(spaces[mask & (counts[spaces] >= 2)]))),
            'unresolved_route_edges': int((touches & unknown).sum()),
            'quiet_large_step_descents_to_review': int(candidates.sum()),
            'quiet_large_ground_descents': int((touches & large & edge_quiet & (edge_kind == hc.EDGE_SLIDE)).sum()),
            'blocked_large_falls': int((touches & large & ~edge_quiet & (edge_kind == hc.EDGE_FALL)).sum()),
            'large_descent_edges': detail})
    return {'map': geo.name, 'height_sha': geo.heights.digest, 'regions': results,
        'qualification': 'Approximate owner regions plus context; not an exact colored footprint or a release certificate.',
        'runtime_changes_applied': False}
