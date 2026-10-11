"""Mixed 8/4-pixel ground footprints. Parent cells remain the display projection only."""
from __future__ import annotations

import copy
import math
from collections import defaultdict

import numpy as np
from scipy import ndimage

from app.control import heights as hc, height_refinement as hr, height_motion as hm

BASE, FINE, PX, CELL = 128 ** 2, 256, 1024, 8


def validate_arrays(raw, receipt):
    if not isinstance(receipt, dict) or receipt.get('v') != 1:
        raise ValueError('invalid refinement receipt')
    history = receipt['history']
    if history != hr.history(history['map'], history['parents']) or hr.identity(history) != receipt['history_sha256']:
        raise ValueError('refinement history identity mismatch')
    for name in ('previous_history_sha256', 'evidence_sha256'):
        value = receipt.get(name)
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('invalid refinement evidence identity')
    cells = raw['fine_cells']
    if cells.dtype != np.dtype('<i4') or cells.ndim != 1 or len(cells) > FINE ** 2 \
            or np.any(cells < 0) or np.any(cells >= FINE ** 2) or np.any(np.diff(cells) <= 0):
        raise ValueError('invalid child footprint identities')
    expected = sorted(c for p in history['parents'] for c in hr._child_ids(p))
    if cells.tolist() != expected:
        raise ValueError('child footprints disagree with refinement history')
    for name, dtype, shape in (('fine_floors', '<i2', (len(cells), hc.MAX_FLOORS)),
        ('fine_spread', '<i2', (len(cells), hc.MAX_FLOORS)), ('fine_supported', '?', (len(cells),)),
        ('fine_unresolved', '?', (len(cells),)), ('fine_kind', 'i1', (len(cells),))):
        if raw[name].dtype != np.dtype(dtype) or raw[name].shape != shape:
            raise ValueError(f'invalid {name} array')
    floors = raw['fine_floors']; has = floors >= 0
    if np.any(floors < -1) or np.any(has[:, 1:] & ~has[:, :-1]) \
            or np.any(has[:, 1:] & (floors[:, 1:] <= floors[:, :-1])) or np.any(raw['fine_spread'] < 0):
        raise ValueError('invalid child floor ordering/spread')
    if np.any(raw['fine_supported'] & ~has[:, 0]) or np.any(raw['fine_unresolved'] & has[:, 0]) \
            or not np.isin(raw['fine_kind'], range(hc.KIND_GRADIENT + 1)).all():
        raise ValueError('invalid child support/refusal metadata')
    edges = raw['fine_edges']
    if edges.dtype != np.dtype('<i4') or edges.ndim != 2 or edges.shape[1] != 5:
        raise ValueError('invalid mixed footprint edges')
    # Endpoint and geometric validation also runs against the actual walk mask at attachment.
    if len(edges) and (np.any(edges[:, (0, 2)] < 0) or np.any(edges[:, (0, 2)] >= BASE + FINE ** 2)
            or np.any(edges[:, (1, 3)] < 0) or np.any(edges[:, (1, 3)] >= hc.MAX_FLOORS)
            or not np.isin(edges[:, 4], (hc.EDGE_STEP, hc.EDGE_SLIDE, hc.EDGE_FALL)).all()
            or len(np.unique(edges[:, :4], axis=0)) != len(edges)):
        raise ValueError('invalid mixed edge endpoints/kinds')


def attach(geo, asset):
    """Attach selected child leaves without changing any reliable parent's footprint."""
    # Called after ordinary attachment, so unselected node IDs and old edges retain their meaning.
    parents = asset.refinement['history']['parents']
    if asset.refinement['history']['map'] != geo.name:
        raise ValueError('refinement belongs to another map')
    validate_arrays({name: getattr(asset, name) for name in ('fine_cells', 'fine_floors', 'fine_spread',
        'fine_supported', 'fine_unresolved', 'fine_kind', 'fine_edges')}, asset.refinement)
    selected = np.asarray(parents, int)
    if np.any(asset.floors.reshape(BASE, -1)[selected] >= 0) or asset.supported.ravel()[selected].any() \
            or asset.unresolved.ravel()[selected].any() or np.isin(asset.edges[:, (0, 2)], selected).any():
        raise ValueError('coarse ground remains underneath selected child footprints')
    count = BASE + FINE ** 2
    ids = np.arange(BASE)
    y, x = np.divmod(ids, 128)
    bbox = np.zeros((count, 4), np.int16)
    bbox[:BASE] = np.column_stack((x * 8, y * 8, (x + 1) * 8, (y + 1) * 8))
    y, x = np.divmod(np.arange(FINE ** 2), FINE)
    bbox[BASE:] = np.column_stack((x * 4, y * 4, (x + 1) * 4, (y + 1) * 4))
    grid = np.repeat(np.repeat(ids.reshape(128, 128), 2, 0), 2, 1)
    spatial_walk = np.zeros(count, bool); spatial_walk[:BASE] = geo.walk.ravel()
    spatial_nodes = np.full((count, hc.MAX_FLOORS), -1, np.int32)
    spatial_nodes[:BASE] = geo.node_of
    node_space = geo.node_cell.tolist()
    node_parent = geo.node_cell.tolist(); node_z = geo.node_z.tolist(); node_walk = geo.walk_n.tolist()
    node_area = [1.] * geo.n
    all_nodes = [list(row[row >= 0]) for row in geo.node_of]
    for p in parents:
        spatial_walk[p] = False
        spatial_nodes[p] = -1
        for n in all_nodes[p]:
            node_walk[n] = False; node_area[n] = 0
        all_nodes[p] = []
    fine_walk = geo.walk_px.reshape(FINE, 4, FINE, 4).mean((1, 3)) > .5
    for child, floors in zip(asset.fine_cells, asset.fine_floors):
        child = int(child); sid = BASE + child; p = hm.coarse_of_fine(child)
        cy, cx = divmod(child, FINE); grid[cy, cx] = sid
        walk = bool(fine_walk[cy, cx] and geo.walk.ravel()[p]); spatial_walk[sid] = walk
        if not walk and np.any(floors >= 0):
            raise ValueError('child floor lies outside permanent walk mask')
        for level in range(max(1, int((floors >= 0).sum()))):
            n = len(node_z); spatial_nodes[sid, level] = n
            node_space.append(sid); node_parent.append(p); all_nodes[p].append(n)
            node_z.append(float(floors[level]) / 10 if floors[level] >= 0 else math.nan)
            node_walk.append(walk); node_area.append(.25)
    geo.n = len(node_z)
    geo.node_space = np.asarray(node_space, np.int32)
    geo.node_cell = np.asarray(node_parent, np.int32)
    geo.node_z = np.asarray(node_z, float); geo.walk_n = np.asarray(node_walk, bool)
    geo.node_area = np.asarray(node_area, float)
    width = max(hc.MAX_FLOORS, max(map(len, all_nodes), default=0))
    geo.node_of = np.full((BASE, width), -1, np.int32)
    for p, nodes in enumerate(all_nodes):
        geo.node_of[p, :len(nodes)] = nodes
    geo.space_nodes, geo.space_walk, geo.space_bbox, geo.space_grid = spatial_nodes, spatial_walk, bbox, grid
    floor0 = spatial_nodes[:, 0]
    geo.space_unresolved = spatial_walk & ((floor0 < 0) | np.isnan(geo.node_z[np.maximum(floor0, 0)]))
    geo.centres = ((bbox[geo.node_space, :2].astype(float) + bbox[geo.node_space, 2:]) / 2).astype(np.float32)
    geo.node_bbox = bbox[geo.node_space]
    geo.unresolved = geo.to_cells(geo.space_unresolved[geo.node_space] & geo.walk_n)
    geo.rows = geo.row_of = geo.topo = None
    validate_edges(geo, asset.fine_edges)
    return geo


def touching(a, b):
    overlap = np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2])
    return bool(np.all(overlap >= 0) and np.any(overlap == 0))


def neighbours(geo):
    """Distinct leaves sharing an edge/corner; never connect two floors of one footprint."""
    grid = geo.space_grid
    pairs = set()
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = grid[max(0, -dy):FINE-max(0, dy), max(0, -dx):FINE-max(0, dx)]
        b = grid[max(0, dy):FINE-max(0, -dy), max(0, dx):FINE-max(0, -dx)]
        for sa, sb in np.unique(np.column_stack((a.ravel(), b.ravel())), axis=0):
            if sa != sb and geo.space_walk[sa] and geo.space_walk[sb]:
                pairs.add(tuple(sorted((int(sa), int(sb)))))
    return sorted(pairs)


def validate_edges(geo, edges):
    for a, fa, b, fb, kind in edges:
        if not geo.space_walk[a] or not geo.space_walk[b] or a == b \
                or not touching(geo.space_bbox[a], geo.space_bbox[b]) \
                or geo.space_nodes[a, fa] < 0 or geo.space_nodes[b, fb] < 0 \
                or geo.space_unresolved[a] or geo.space_unresolved[b]:
            raise ValueError('mixed edge names nonadjacent, missing or unresolved ground')


def graph(geo):
    """Runtime adjacency, including the existing unresolved-ground fallback, at leaf scale."""
    walks, loud = set(), set()
    for edges in (geo.heights.edges, geo.heights.fine_edges):
        for a, fa, b, fb, kind in edges:
            na, nb = int(geo.space_nodes[a, fa]), int(geo.space_nodes[b, fb])
            if na < 0 or nb < 0 or not geo.space_walk[a] or not geo.space_walk[b]:
                continue
            walks.add((na, nb))
            if kind == hc.EDGE_FALL and geo.node_z[na] - geo.node_z[nb] > hc.SILENT_DROP_M:
                loud.add((na, nb))
    for a, b in neighbours(geo):
        if not geo.space_unresolved[a] and not geo.space_unresolved[b]:
            continue
        for na in geo.space_nodes[a]:
            for nb in geo.space_nodes[b]:
                if na >= 0 and nb >= 0:
                    walks.update(((int(na), int(nb)), (int(nb), int(na))))
    # Corner contact needs both side footprints and a qualified route around each corner.
    # A resolved ledge cannot be jumped diagonally merely because its boxes touch.
    for na, nb in list(walks):
        sides = corner_sides(geo, int(geo.node_space[na]), int(geo.node_space[nb]))
        if sides is None:
            continue
        if any(s < 0 or not geo.space_walk[s] or not any(n >= 0 and (na, int(n)) in walks and (int(n), nb) in walks
                for n in geo.space_nodes[s]) for s in sides):
            walks.discard((na, nb)); loud.discard((na, nb))
    return walks, loud


def corner_sides(geo, a, b):
    ba, bb = geo.space_bbox[a], geo.space_bbox[b]
    lo, hi = np.maximum(ba[:2], bb[:2]), np.minimum(ba[2:], bb[2:])
    if np.any(lo != hi):
        return None
    x, y = lo
    if not (0 < x < PX and 0 < y < PX):
        return (-1, -1)
    quadrants = {geo.space_at_px(x + dx, y + dy) for dx in (-.1, .1) for dy in (-.1, .1)}
    sides = sorted(quadrants - {a, b})
    return tuple(sides) if len(sides) == 2 else (-1, -1)


def area_readiness(geo, asset):
    """Unchanged bars in physical area: each coarse footprint is four 4-pixel squares."""
    supported = np.repeat(np.repeat(asset.supported, 2, 0), 2, 1)
    unresolved = geo.space_unresolved[geo.space_grid]
    counts = (geo.space_nodes >= 0).sum(1)[geo.space_grid]
    for c, ok in zip(asset.fine_cells, asset.fine_supported):
        supported.ravel()[int(c)] = bool(ok)
    walk = geo.space_walk[geo.space_grid]
    supported &= walk
    by_two = ndimage.binary_dilation(counts >= 2, np.ones((3, 3), bool))
    lab, _ = ndimage.label(unresolved, np.ones((3, 3), bool))
    sizes = np.bincount(lab.ravel()) / 4
    blocking = [float(sizes[k]) for k in set(lab[unresolved & by_two]) if sizes[k] > hc.UNRESOLVED_MAX]
    walk_px2, support_px2 = int(walk.sum()) * 16, int(supported.sum()) * 16
    share = support_px2 / walk_px2 if walk_px2 else 0
    reasons = []
    if share < hc.HEIGHT_SUPPORTED_MIN:
        reasons.append(f'supported {share:.1%} is under {hc.HEIGHT_SUPPORTED_MIN:.0%}')
    if blocking:
        reasons.append(f'{len(blocking)} unresolved area(s) larger than {hc.UNRESOLVED_MAX} parent-cell areas '
                       f'touch a footprint with two floors (largest {max(blocking):g} parent-cell areas)')
    return {'supported': round(share, 4), 'walkable_area_px2': walk_px2, 'supported_area_px2': support_px2,
            'unresolved_area_px2': int(unresolved.sum()) * 16, 'ready': not reasons, 'not_ready': reasons}


def refine(build, geo, found, walks, round_match, direct_floors, reasons, previous):
    """Overlay selected child evidence, preserving the coarse builder on reliable parents."""
    evidence = hr.analyze(geo, found, walks, round_match, direct_floors, reasons, previous)
    history = evidence['history']; asset = build.asset
    asset.refinement = {'v': 1, 'history': history, 'history_sha256': evidence['history_sha256'],
        'previous_history_sha256': evidence['previous_history_sha256'], 'evidence_sha256': evidence['evidence_sha256']}
    # Keep removed-mask selections in immutable history with empty, nonwalkable children.
    children = {c['cell']: c for c in evidence['children']}
    ids = sorted(c for p in history['parents'] for c in hr._child_ids(p))
    asset.fine_cells = np.asarray(ids, np.int32)
    asset.fine_floors = np.full((len(ids), hc.MAX_FLOORS), -1, np.int16)
    asset.fine_spread = np.zeros_like(asset.fine_floors)
    asset.fine_supported = np.zeros(len(ids), bool); asset.fine_unresolved = np.zeros(len(ids), bool)
    asset.fine_kind = np.zeros(len(ids), np.int8); asset.fine_edges = np.empty((0, 5), np.int32)
    origin = min([asset.origin_z] + [z for c in children.values() for z, _ in c['floors_world_dm']])
    shift = asset.origin_z - origin
    maximum = max([int(asset.floors.max()) + shift] + [z - origin for c in children.values() for z, _ in c['floors_world_dm']])
    if maximum > np.iinfo(np.int16).max:
        raise ValueError('refined height frame exceeds asset range')
    if shift:
        asset.floors[asset.floors >= 0] += shift
        asset.meta['origin_z'] = origin
    for i, child in enumerate(ids):
        row = children.get(child)
        cy, cx = divmod(child, FINE)
        if row is None or not geo.walk.ravel()[hm.coarse_of_fine(child)] \
                or geo.walk_px[cy*4:cy*4+4, cx*4:cx*4+4].mean() <= .5:
            continue
        for level, (z, spread) in enumerate(row['floors_world_dm']):
            asset.fine_floors[i, level] = z - origin; asset.fine_spread[i, level] = spread
        asset.fine_supported[i] = bool(row['floors_world_dm'])
        asset.fine_unresolved[i] = not bool(row['floors_world_dm'])
        asset.fine_kind[i] = row['kind']
    # Old whole-parent ground and edges cannot remain active underneath child footprints.
    parents = set(history['parents'])
    for p in parents:
        y, x = divmod(p, 128)
        asset.floors[y, x] = -1; asset.supported[y, x] = False
        asset.unresolved[y, x] = False; asset.kind[y, x] = hc.KIND_NONE
    coarse_edges = asset.edges.copy()
    asset.edges = np.asarray([e for e in asset.edges if e[0] not in parents and e[2] not in parents], np.int32).reshape(-1, 5)
    from app.control.geometry import attach_heights
    candidate = attach_heights(copy.copy(geo), asset)
    if not len(ids):
        build.report.update(refinement=asset.refinement, refined_parents=0, refined_children=0,
                            walkable_area_px2=build.report['walkable_cells'] * 64,
                            supported_area_px2=build.report['supported_cells'] * 64)
        return build
    # Explicit qualified parent transitions may be rebased onto touching child leaves of the same height.
    prior_floor = {p: [int(z) for z in row if z >= 0] for p, row in enumerate(asset.floors.reshape(BASE, -1))}
    for p, got in direct_floors.items():
        prior_floor[p] = [z - origin for z, _ in got]
    parent_edges = {(int(a), int(fa), int(b), int(fb)): int(k) for a, fa, b, fb, k in coarse_edges}
    observed = defaultdict(set)
    for run in walks:
        for (ca, za), (cb, zb) in zip(zip(run.fine_cells, run.fine_low), zip(run.fine_cells[1:], run.fine_low[1:])):
            ya, xa = divmod(ca, FINE); yb, xb = divmod(cb, FINE)
            a, b = int(candidate.space_grid[ya, xa]), int(candidate.space_grid[yb, xb])
            if a == b or not touching(candidate.space_bbox[a], candidate.space_bbox[b]):
                continue
            matches = []
            for sid, z in ((a, za), (b, zb)):
                matches.append([i for i, n in enumerate(candidate.space_nodes[sid]) if n >= 0 and
                    np.isfinite(candidate.node_z[n]) and abs(candidate.node_z[n] - (z-origin)/10) <= hc.FLOOR_TOL_M])
            if all(len(m) == 1 for m in matches):
                observed[(a, matches[0][0], b, matches[1][0])].add(run.round)
    edge_rows = []
    for a, b in neighbours(candidate):
        if a < BASE and b < BASE:
            continue
        pa = a if a < BASE else hm.coarse_of_fine(a - BASE)
        pb = b if b < BASE else hm.coarse_of_fine(b - BASE)
        for fa, na in enumerate(candidate.space_nodes[a]):
            for fb, nb in enumerate(candidate.space_nodes[b]):
                if na < 0 or nb < 0 or np.isnan(candidate.node_z[na]) or np.isnan(candidate.node_z[nb]):
                    continue
                za, zb = candidate.node_z[na], candidate.node_z[nb]
                if abs(za - zb) <= hc.STEP_UP_M:
                    edge_rows += [(a, fa, b, fb, hc.EDGE_STEP), (b, fb, a, fa, hc.EDGE_STEP)]
                else:
                    for s, fs, d, fd, zs, zd in ((a, fa, b, fb, za, zb), (b, fb, a, fa, zb, za)):
                        if len(observed.get((s, fs, d, fd), ())) >= hc.CONNECT_MIN_ROUNDS:
                            if zd > zs:
                                edge_rows += [(s, fs, d, fd, hc.EDGE_STEP), (d, fd, s, fs, hc.EDGE_STEP)]
                            else:
                                edge_rows.append((s, fs, d, fd, hc.EDGE_SLIDE))
                if abs(za-zb) > hc.STEP_UP_M and pa != pb:
                    ia = [i for i, z in enumerate(prior_floor.get(pa, [])) if abs(z / 10 - za) <= hc.FLOOR_TOL_M]
                    ib = [i for i, z in enumerate(prior_floor.get(pb, [])) if abs(z / 10 - zb) <= hc.FLOOR_TOL_M]
                    if len(ia) == len(ib) == 1:
                        for s, fs, d, fd, key in ((a, fa, b, fb, (pa, ia[0], pb, ib[0])),
                                               (b, fb, a, fa, (pb, ib[0], pa, ia[0]))):
                            if key in parent_edges:
                                edge_rows.append((s, fs, d, fd, parent_edges[key]))
    # An observed climb wins over a separately observed descent, as in the coarse builder.
    unique = {}
    for a, fa, b, fb, kind in edge_rows:
        key = (a, fa, b, fb)
        unique[key] = min(kind, unique.get(key, kind))
    asset.fine_edges = np.asarray(sorted((*key, kind) for key, kind in unique.items()), np.int32).reshape(-1, 5)
    validate_edges(candidate, asset.fine_edges)
    # Preserve legacy diagnostics explicitly; their 8px regions are not mixed leaf counts.
    build.report['coarse_diagnostics'] = {key: copy.deepcopy(build.report[key]) for key in
        ('walkable_cells', 'visited_cells', 'visited', 'cells_by_kind', 'unresolved_cells',
         'unresolved_areas', 'unresolved_why', 'refused_cells', 'air_only', 'edges', 'one_way_edges',
         'slides', 'falls', 'falls_closed')}
    build.report['diagnostic_grid_px'] = 8
    build.report['runtime_grid_px'] = [8, 4]
    build.report.update(area_readiness(candidate, asset), refinement=asset.refinement,
                        **leaf_counts(candidate, asset), refined_parents=len(parents), refined_children=len(ids))
    build.ready = build.report['ready']
    return build


def leaf_counts(geo, asset):
    spaces = np.unique(geo.space_grid)
    walking = spaces[geo.space_walk[spaces]]
    counts = (geo.space_nodes[walking] >= 0).sum(1)
    has = np.isfinite(geo.node_z[geo.space_nodes[walking, 0]])
    return {'walkable_cells': int(len(walking)),
            'supported_cells': int(asset.supported.sum() + asset.fine_supported.sum()),
            'floor_cells': int(has.sum()), 'unresolved_cells': int(geo.space_unresolved[walking].sum()),
            'cells_2_floors': int((counts == 2).sum()), 'cells_3_floors': int((counts == 3).sum())}
