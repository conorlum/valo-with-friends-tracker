"""Bounded walking-only rope ground. It never activates terrain sight heights."""
from __future__ import annotations

import copy
import hashlib
import json

import numpy as np

from app.control import geometry as cg, heights as hc, height_refinement as hr
from app.replays.map_feature_artifacts import FeatureArtifactCorrupt

FIELDS = {'v', 'box', 'walk_sha', 'source_height_sha', 'parents', 'coarse', 'fine', 'edges'}
MAX_ROWS = 4096


def _fail(message):
    raise FeatureArtifactCorrupt('rope walking domain: ' + message)


def _integers(value, length=None):
    return isinstance(value, list) and (length is None or len(value) == length) and all(type(v) is int for v in value)


def _bbox(sid):
    if sid < cg.GRID ** 2:
        y, x = divmod(sid, cg.GRID)
        return [x*8, y*8, x*8+8, y*8+8]
    y, x = divmod(sid-cg.GRID**2, cg.GRID*2)
    return [x*4, y*4, x*4+4, y*4+4]


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(c in '0123456789abcdef' for c in value)


def validate_domain(geo, domain):
    if not isinstance(domain, dict) or set(domain) != FIELDS or type(domain['v']) is not int or domain['v'] != 1:
        _fail('unsupported fields/version')
    box = domain['box']
    if not _integers(box, 4) or not (0 <= box[0] < box[2] <= cg.PX and 0 <= box[1] < box[3] <= cg.PX) \
            or max(box[2]-box[0], box[3]-box[1]) > 256:
        _fail('footprint must be a bounded map rectangle up to 256 pixels across')
    expected = hashlib.sha256(np.packbits(geo.walk_px).tobytes()).hexdigest()[:12]
    if domain['walk_sha'] != expected or not _hex(domain['source_height_sha'], 12):
        _fail('permanent walk/source identity mismatch')
    parents = domain['parents']
    if not _integers(parents) or parents != sorted(set(parents)) or len(parents) > 1024 \
            or any(not 0 <= p < cg.GRID**2 for p in parents):
        _fail('invalid refinement parents')
    for p in parents:
        b = _bbox(p)
        if b[0] < box[0] or b[1] < box[1] or b[2] > box[2] or b[3] > box[3]:
            _fail('refinement parent outside domain')
    records = {}
    for label, limit, offset in (('coarse', cg.GRID**2, 0), ('fine', (cg.GRID*2)**2, cg.GRID**2)):
        rows = domain[label]
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            _fail('floor row limit/type')
        previous = -1
        for row in rows:
            if not isinstance(row, list) or len(row) != 3 or type(row[0]) is not int \
                    or not previous < row[0] < limit or row[0] < 0 or not _integers(row[1]) \
                    or not 1 <= len(row[1]) <= hc.MAX_FLOORS or row[1] != sorted(set(row[1])) \
                    or any(abs(z) > 30000 for z in row[1]) or type(row[2]) is not int \
                    or row[2] not in (hc.KIND_STANDS, hc.KIND_WALKS):
                _fail('invalid ordered supported floor rows')
            previous = row[0]
            sid = row[0]+offset
            b = _bbox(sid)
            parent = row[0] if not offset else (row[0]//256//2)*128+(row[0]%256//2)
            if (bool(offset) != (parent in parents)) or b[0] < box[0] or b[1] < box[1] \
                    or b[2] > box[2] or b[3] > box[3] \
                    or geo.walk_px[b[1]:b[3], b[0]:b[2]].mean() <= .5:
                _fail('floor lies outside its walkable footprint/refinement selection')
            records[sid] = (row[1], row[2])
    if not records:
        _fail('empty supported ground')
    edges = domain['edges']
    if not isinstance(edges, list) or len(edges) > MAX_ROWS*16:
        _fail('edge limit/type')
    seen = set()
    for edge in edges:
        if not _integers(edge, 5):
            _fail('invalid edge')
        a, fa, b, fb, kind = edge
        if a not in records or b not in records or not 0 <= fa < len(records[a][0]) \
                or not 0 <= fb < len(records[b][0]) or kind not in (hc.EDGE_STEP, hc.EDGE_SLIDE, hc.EDGE_FALL) \
                or a == b or tuple(edge[:4]) in seen:
            _fail('edge references absent/duplicate ground')
        ba, bb = np.asarray(_bbox(a)), np.asarray(_bbox(b))
        if np.any(np.maximum(ba[:2], bb[:2]) > np.minimum(ba[2:], bb[2:])):
            _fail('edge does not join touching footprints')
        seen.add(tuple(edge[:4]))
    return records


def domains_for(mf):
    owners = {m for b in mf.get('bundles', []) if b.get('enabled') and b.get('runtime_consumer') == 'quiet_rope_v1'
              for m in b.get('members', [])}
    return sorted([(r, r['walking_domain']) for r in mf.get('routes', [])
                   if r.get('owner') in owners and 'walking_domain' in r], key=lambda item: item[0]['id'])


def from_asset(geo, asset, box):
    """Crop directly supported walking ground; never import global sight acceptance or filled floors."""
    coarse, fine, records = [], [], set()
    parents = [] if asset.refinement is None else [p for p in asset.refinement['history']['parents']
        if all(a>=b for a,b in zip(_bbox(p)[:2],box[:2])) and all(a<=b for a,b in zip(_bbox(p)[2:],box[2:]))]
    for cell in np.flatnonzero(asset.supported.ravel()):
        b=_bbox(int(cell))
        if b[0]<box[0] or b[1]<box[1] or b[2]>box[2] or b[3]>box[3]:continue
        levels=[int(z)+asset.origin_z for z in asset.floors.reshape(-1,hc.MAX_FLOORS)[cell] if z>=0]
        coarse.append([int(cell),levels,int(asset.kind.ravel()[cell])]);records.add(int(cell))
    if asset.refinement is not None:
        for i,c in enumerate(asset.fine_cells):
            p=(int(c)//256//2)*128+(int(c)%256//2)
            if p not in parents or not asset.fine_supported[i]:continue
            levels=[int(z)+asset.origin_z for z in asset.fine_floors[i] if z>=0]
            fine.append([int(c),levels,int(asset.fine_kind[i])]);records.add(int(c)+128**2)
    rows=asset.edges.tolist()+([] if asset.refinement is None else asset.fine_edges.tolist())
    domain=dict(v=1,box=list(box),walk_sha=hashlib.sha256(np.packbits(geo.walk_px).tobytes()).hexdigest()[:12],
        source_height_sha=asset.digest,parents=parents,coarse=coarse,fine=fine,
        edges=sorted(e for e in rows if e[0] in records and e[2] in records))
    validate_domain(geo,domain)
    return domain


def prepare(geo, mf):
    """A private geometry copy with supported local walking nodes and explicitly flat sight.

    A selected full height asset wins. The fallback is restricted to Abyss and enabled quiet ropes.
    Its source floors/edges are immutable feature inputs, independent of global height activation.
    """
    domains = domains_for(mf)
    if not domains or (geo.heights is not None and not geo.movement_only):
        return geo
    if geo.name != 'Abyss':
        _fail('fallback is only supported on Abyss')
    if len(domains) > 4:
        _fail('at most four bounded Abyss rope domains are supported')
    digest = hashlib.sha256(json.dumps([d for r,d in domains], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if geo.movement_only:
        if geo.rope_ground_digest != digest:
            _fail('prepared geometry belongs to another walking domain')
        return geo
    records, edges, parents = {}, {}, set()
    for route, domain in domains:
        one = validate_domain(geo, domain)
        for sid, values in one.items():
            if sid in records and records[sid] != values:
                _fail('overlapping domains disagree')
            records[sid] = values
        parents.update(domain['parents'])
        for edge in domain['edges']:
            key = tuple(edge[:4])
            if key in edges and edges[key] != edge[4]:
                _fail('overlapping edge classifications disagree')
            edges[key] = edge[4]
    if any(p in records for p in parents):
        _fail('coarse floor underneath a refined footprint')
    origin = min(z for values,kind in records.values() for z in values)
    if max(z-origin for values,kind in records.values() for z in values)>32767:
        _fail('height frame exceeds storage range')
    floors = np.full((128,128,hc.MAX_FLOORS), -1, np.int16)
    supported = np.zeros((128,128), bool); kinds = np.zeros((128,128), np.int8)
    for sid,(levels,kind) in records.items():
        if sid >= 128**2:continue
        y,x=divmod(sid,128);floors[y,x,:len(levels)]=np.asarray(levels)-origin
        supported[y,x]=True;kinds[y,x]=kind
    unknown=geo.walk & ~supported
    for p in parents:unknown.ravel()[p]=False
    all_edges=[[*key,kind] for key,kind in sorted(edges.items())]
    coarse_edges=[e for e in all_edges if e[0]<128**2 and e[2]<128**2]
    asset=hc.HeightAsset(floors,np.zeros_like(floors),supported,unknown,
        np.asarray(coarse_edges,np.int32).reshape(-1,5),
        dict(version=hc.HEIGHT_VERSION,origin_z=origin,units='dm',stand_m=hc.STAND_M,movement_only=True),kinds)
    if parents:
        history=hr.history(geo.name,sorted(parents))
        asset.refinement=dict(v=1,history=history,history_sha256=hr.identity(history),
            previous_history_sha256=hr.identity(hr.history(geo.name)),evidence_sha256=digest)
        ids=sorted(c for p in parents for c in hr._child_ids(p));asset.fine_cells=np.asarray(ids,np.int32)
        asset.fine_floors=np.full((len(ids),hc.MAX_FLOORS),-1,np.int16)
        asset.fine_spread=np.zeros_like(asset.fine_floors);asset.fine_supported=np.zeros(len(ids),bool)
        asset.fine_unresolved=np.ones(len(ids),bool);asset.fine_kind=np.zeros(len(ids),np.int8)
        for i,c in enumerate(ids):
            if c+128**2 in records:
                levels,kind=records[c+128**2];asset.fine_floors[i,:len(levels)]=np.asarray(levels)-origin
                asset.fine_supported[i]=True;asset.fine_unresolved[i]=False;asset.fine_kind[i]=kind
        asset.fine_edges=np.asarray([e for e in all_edges if e[0]>=128**2 or e[2]>=128**2],np.int32).reshape(-1,5)
    fresh=cg.attach_heights(copy.copy(geo),asset)
    fresh.movement_only=True;fresh.movement_height_sha=asset.digest;fresh.height_sha=None
    fresh.rope_ground_digest=digest
    # Each enabled fallback route must really bind; no partial walking model from a pending rope.
    from app.control.route_runtime import readiness
    owners={r['owner'] for r,d in domains}
    _, reasons=readiness(fresh,mf,owners)
    if reasons:_fail('; '.join(reasons))
    return fresh
