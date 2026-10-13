"""Local computational tessellation for moving panels; never a new measured height asset.

Lotus uses 4-pixel leaves across the swept door area. Children inherit the selected
parent's existing floor model, including unknowns and stacked floors. Existing
measured fine leaves win. Permanent masks and height acceptance remain unchanged.
"""
from __future__ import annotations

import copy
import hashlib

import numpy as np

from app.control import adaptive as ag, geometry as cg, heights as hc, height_refinement as hr
from app.replays.map_feature_inputs import canonical_json
from app.replays.map_feature_artifacts import FeatureArtifactCorrupt

MAX_PANEL_PARENTS = 1024


def panel_parents(geo, mf):
    if geo.name != 'Lotus' or geo.heights is None or geo.movement_only:
        return []
    owners = {m for b in mf.get('bundles', []) if b.get('enabled')
              and b.get('runtime_consumer') == 'map_pool_replay_v1' for m in b.get('members', [])}
    selected = set()
    for f in mf.get('features', []):
        rot = f.get('rotation')
        if f.get('id') not in owners or not rot:
            continue
        from app.control.map_pool_runtime import readiness
        if readiness(f):
            continue
        pivot = np.asarray(rot['pivot']['uv'], float) * cg.PX / 10000
        points = np.asarray(rot['panel']['uv'], float) * cg.PX / 10000
        radius = float(np.linalg.norm(points-pivot, axis=1).max())
        y, x = np.mgrid[:cg.GRID, :cg.GRID]
        near = np.hypot(x*8+4-pivot[0], y*8+4-pivot[1]) <= radius + np.sqrt(2)*4
        selected.update(np.flatnonzero(near).tolist())
    if len(selected) > MAX_PANEL_PARENTS:
        raise FeatureArtifactCorrupt('rotating panel tessellation exceeds bounded doorway area')
    return sorted(selected)


def prepare(geo, mf):
    from app.control.rope_ground import prepare as rope_prepare
    geo = rope_prepare(geo, mf)
    parents = panel_parents(geo, mf)
    if not parents:
        return geo
    digest = hashlib.sha256(canonical_json({'v': 1, 'map': geo.name, 'parents': parents,
        'selected_height': geo.height_sha, 'leaf_px': 4})).hexdigest()
    if geo.panel_grid_digest == digest:
        return geo
    source = geo.panel_height_source or geo.heights
    previous = set(source.refinement['history']['parents']) if source.refinement else set()
    added = set(parents) - previous
    if not added:
        return geo
    asset = copy.deepcopy(source)
    history = hr.history(geo.name, sorted(previous | added))
    asset.refinement = dict(v=1, history=history, history_sha256=hr.identity(history),
        previous_history_sha256=hr.identity(hr.history(geo.name, sorted(previous))), evidence_sha256=digest)
    ids = sorted(c for p in history['parents'] for c in hr._child_ids(p))
    old_rows = {int(c): i for i, c in enumerate(source.fine_cells)} if source.refinement else {}
    asset.fine_cells = np.asarray(ids, np.int32)
    for name, dtype, shape in [('floors', np.int16, (len(ids), hc.MAX_FLOORS)),
        ('spread', np.int16, (len(ids), hc.MAX_FLOORS)), ('supported', bool, (len(ids),)),
        ('unresolved', bool, (len(ids),)), ('kind', np.int8, (len(ids),))]:
        values = np.zeros(shape, dtype=dtype)
        if name == 'floors':
            values.fill(-1)
        coarse = getattr(source, name).reshape((ag.BASE, hc.MAX_FLOORS) if name in ('floors', 'spread') else ag.BASE)
        for i, c in enumerate(ids):
            p = (c//256//2)*128 + c%256//2
            if c in old_rows:
                values[i] = getattr(source, 'fine_'+name)[old_rows[c]]
            elif geo.walk_px[(c//256)*4:(c//256)*4+4, (c%256)*4:(c%256)*4+4].mean() > .5 and geo.walk.ravel()[p]:
                values[i] = coarse[p]
        setattr(asset, 'fine_'+name, values)
    for p in added:
        asset.floors.reshape(ag.BASE, -1)[p] = -1
        asset.spread.reshape(ag.BASE, -1)[p] = 0
        asset.supported.ravel()[p] = asset.unresolved.ravel()[p] = False
        asset.kind.ravel()[p] = hc.KIND_NONE
    # Rebase qualified directed edges onto touching children. No new cross-floor
    # edge is inferred. Same-parent children share only their inherited floor.
    def leaves(sid):
        return [ag.BASE+c for c in hr._child_ids(sid)] if sid in added else [sid]
    def bbox(sid):
        if sid < ag.BASE:
            y, x = divmod(sid, 128); return np.array([x*8, y*8, x*8+8, y*8+8])
        y, x = divmod(sid-ag.BASE, 256); return np.array([x*4, y*4, x*4+4, y*4+4])
    rows = source.edges.tolist() + (source.fine_edges.tolist() if source.refinement else [])
    projected = set()
    fine_lookup = {ag.BASE+c: row for c, row in zip(ids, asset.fine_floors)}
    def has(sid, level):
        return (fine_lookup[sid][level] >= 0 if sid >= ag.BASE else asset.floors.reshape(ag.BASE, -1)[sid, level] >= 0)
    for a, fa, b, fb, kind in rows:
        for sa in leaves(a):
            for sb in leaves(b):
                if has(sa, fa) and has(sb, fb) and ag.touching(bbox(sa), bbox(sb)):
                    projected.add((sa, fa, sb, fb, kind))
    for p in added:
        children = leaves(p)
        for a in children:
            for b in children:
                if a != b and ag.touching(bbox(a), bbox(b)):
                    for level in range(hc.MAX_FLOORS):
                        if has(a, level) and has(b, level):
                            projected.add((a, level, b, level, hc.EDGE_STEP))
    asset.edges = np.asarray(sorted(e for e in projected if e[0] < ag.BASE and e[2] < ag.BASE), np.int32).reshape(-1, 5)
    asset.fine_edges = np.asarray(sorted(e for e in projected if e[0] >= ag.BASE or e[2] >= ag.BASE), np.int32).reshape(-1, 5)
    asset.meta['feature_grid'] = True
    fresh = cg.attach_heights(copy.copy(geo), asset)
    fresh.height_sha = geo.height_sha
    fresh.panel_grid_digest, fresh.panel_height_source = digest, source
    return fresh
