"""Ascent evidence consumer. A verified artifact and complete source timeline are both required."""
from __future__ import annotations

import numpy as np

from app.replays.ascent_features import KEYS, VERSION, state_at
from app.replays.map_feature_artifacts import FeatureArtifactCorrupt, expanded_assets
from app.replays.map_feature_inputs import read_json

CONSUMER = 'ascent_replay_v1'


def readiness(feature):
    from app.replays.map_feature_motion import geometry_problems, vertical_problems, movement_problems
    key = feature.get('replay_key')
    reasons = []
    if key not in KEYS:
        return ['explicit Ascent replay binding missing']
    if feature.get('parent') or feature.get('rotation') or feature.get('platform'):
        reasons.append('Ascent consumer does not implement attached or rotating platforms')
    names = {s['name'] for s in feature.get('states', [])}
    expected = {'intact', 'broken'} if key == 'ascent_heaven_glass' else {'open', 'closing', 'closed', 'broken'}
    if not expected <= names:
        reasons.append('required replay states missing')
    broken = next((s for s in feature.get('states', []) if s['name'] == 'broken'), {})
    if not broken.get('terminal') or broken.get('blocks_sight') or broken.get('blocks_movement'):
        reasons.append('broken must be terminal and nonblocking')
    if key != 'ascent_heaven_glass':
        opened = next((s for s in feature.get('states', []) if s['name'] == 'open'), {})
        closed = next((s for s in feature.get('states', []) if s['name'] == 'closed'), {})
        if opened.get('blocks_sight') or opened.get('blocks_movement') or opened.get('sight'):
            reasons.append('open doorway must be nonblocking')
        if not closed.get('blocks_sight') or not closed.get('blocks_movement'):
            reasons.append('closed doorway must block sight and movement')
        if (feature.get('sliding') or {}).get('axis') != 'vertical':
            reasons.append('Ascent doors require descending geometry')
        if (feature.get('sliding') or {}).get('open_state') != 'open' or \
                (feature.get('sliding') or {}).get('closed_state') != 'closed':
            reasons.append('Ascent door endpoints must be named open and closed')
        reasons += geometry_problems(feature) + vertical_problems(feature)
        reasons += movement_problems(feature)
    else:
        if feature.get('sliding'):
            reasons.append('Ascent glass has no sliding motion')
        intact = next((s for s in feature.get('states', []) if s['name'] == 'intact'), {})
        if not intact.get('blocks_movement') or not intact.get('footprint'):
            reasons.append('intact glass needs a movement footprint')
        if intact.get('blocks_sight') or intact.get('sight'):
            reasons.append('Ascent glass must be transparent; review unused sight geometry')
    return reasons


def prepare_geometry(geo):
    """Use the archived permanent domain, without mutating a cached map or changing node identities."""
    if not geo.features:
        return geo
    from app.control import features as cf
    from app.control import geometry as cg
    from app.replays.map_feature_artifacts import FeatureArtifact
    if not isinstance(geo.features, FeatureArtifact):
        raise FeatureArtifactCorrupt('runtime needs an archived feature artifact')
    artifact = geo.features
    assets = read_json(expanded_assets(artifact.assets))
    base = assets['base_domain']
    sight = cf._archived_mask(base['sight'], (cg.PX, cg.PX))
    walk = cf._archived_mask(base['walk'], (cg.PX, cg.PX))
    if np.array_equal(sight, geo.sight) and np.array_equal(walk, geo.walk_px):
        return geo
    fresh = cg.geometry_from_masks(geo.name, sight, walk, geo.uv_per_unit / 10000, geo.specials)
    if geo.heights is not None:
        cg.attach_heights(fresh, geo.heights)
    if fresh.n != geo.n or not np.array_equal(fresh.node_cell, geo.node_cell):
        raise FeatureArtifactCorrupt('feature domain changes archived node identities')
    fresh.barrier, fresh.barrier_sha = geo.barrier, geo.barrier_sha
    fresh.features, fresh.features_sha = artifact, artifact.digest
    return fresh


class FeatureRuntime:
    def __init__(self, geo, blob):
        self.geo, self.items, self.times = geo, [], set()
        self._time, self._sample = None, None
        self._masks = {}
        self._releases = None
        self.transitions = []
        from app.control.map_pool_runtime import MapPoolRuntime
        self.pool = MapPoolRuntime(geo, blob)
        self.times.update(self.pool.transitions)
        self.transitions = sorted(self.times)
        if not geo.features:
            return
        artifact = geo.features
        envelope = read_json(artifact.inputs)
        mf = envelope['runtime']
        bundles = {b['id']: b for b in mf.get('bundles', [])}
        members = {m for bid in artifact.manifest['active_bundles']
                   if bundles[bid].get('runtime_consumer') == CONSUMER for m in bundles[bid]['members']}
        if not members:
            return
        data = blob.get('map_features') or {}
        if data.get('v') != VERSION or data.get('status') != 'decoded' or geo.name != 'Ascent':
            if members:
                raise FeatureArtifactCorrupt('active Ascent features require a complete map-message timeline')
            return
        evidence = {f['key']: f for f in data.get('features', [])}
        for f in mf.get('features', []):
            if f['id'] not in members:
                continue
            key = f.get('replay_key')
            if readiness(f) or key not in evidence:
                raise FeatureArtifactCorrupt('active feature definition/binding unsupported')
            timeline = evidence[key]
            if timeline.get('initial') == 'unknown':
                raise FeatureArtifactCorrupt('feature round reset unobserved')
            self.items.append((mf, f, timeline))
            self.times.update(float(e['t']) for e in timeline['events'])
            if f.get('sliding'):
                from app.replays.map_feature_motion import fs_known_metres, closed_state, movement_cutoff
                top = fs_known_metres(f['sliding']['open_clearance'])
                bottom = fs_known_metres(closed_state(f)['sight_bounds']['bottom'])
                clearance = fs_known_metres(f['sliding'].get('movement_clearance'))
                if top > bottom:
                    fraction = movement_cutoff(f)
                    if fraction is None:
                        fraction = min(max((top-clearance)/(top-bottom), 0), 1)
                    for event in timeline['events']:
                        if event['state'] == 'closing':
                            self.times.add(float(event['t']) + 5 * fraction)
        self.transitions = sorted(self.times)
        # Fail the round instead of computing through a coverage hole or unsupported opening.
        for _, _, timeline in self.items:
            terminal = timeline['initial'] == 'broken'
            for event in timeline['events']:
                if event['state'] == 'unknown' and not terminal:
                    raise FeatureArtifactCorrupt('unverified opening/default state in active feature timeline')
                terminal |= event['state'] == 'broken'

    @property
    def active(self):
        return bool(self.items) or self.pool.active

    def metadata(self, blob):
        """Calculation provenance and a reducer trace for the site's existing feature layer."""
        if not self.geo.features:
            return None
        mf = read_json(self.geo.features.inputs)['runtime']
        active = set(self.geo.features.manifest['active_bundles'])
        quiet = {m for b in mf.get('bundles', []) if b['id'] in active
                 and b.get('runtime_consumer') == 'quiet_rope_v1' for m in b['members']}
        keys = [f['replay_key'] for _, f, _ in self.items]
        keys += [f['id'] for _, f, _ in self.pool.items] + sorted(quiet)
        if not keys:
            return None
        payload = []
        from app.replays.map_feature_state import evaluate
        for _, f, timeline in self.pool.items:
            trace = evaluate({**f, 'initial_state': timeline['initial']}, timeline['events'], until=blob['t_end'])['trace']
            states = {s['name']: s for s in f['states']}
            events = [{'t': e['t'], 'state': e['state'], 'moving': e['moving'],
                       'blocks_movement': bool(states[e['state']].get('blocks_movement'))}
                      for e in trace if e['result'] == 'applied']
            if f.get('sliding'):
                for event in events:
                    sampled = evaluate({**f, 'initial_state': timeline['initial']}, timeline['events'],
                                       until=event['t'])['state']
                    event['motion'] = sampled.get('motion')
            shapes = [s['footprint'] for s in f['states'] if s.get('footprint')]
            pivot = (f.get('rotation') or {}).get('pivot')
            uv = pivot['uv'] if pivot and pivot.get('type') == 'point' else None
            if uv is None and shapes:
                uv = [sum(p[i] for p in shapes[0]['uv']) / len(shapes[0]['uv']) for i in (0, 1)]
            payload.append({'key': f['id'], 'name': f.get('name', f['id']), 'uv': uv,
                'mode': 'reducer_trace', 'initial': timeline['initial'], 'events': events,
                'initial_blocks_movement': bool(states[timeline['initial']].get('blocks_movement'))})
            if f.get('sliding'):
                from app.replays.map_feature_motion import movement_cutoff
                payload[-1]['sliding'] = {'open_state': f['sliding']['open_state'],
                    'closed_state': f['sliding']['closed_state'], 'movement_cutoff': movement_cutoff(f)}
        for f in mf.get('features', []):
            if f['id'] in quiet:
                route = next(r for r in mf['routes'] if r.get('owner') == f['id'])
                payload.append({'key': f['id'], 'name': f.get('name', f['id']), 'uv': route['endpoints'][0]['uv'],
                    'mode': 'reducer_trace', 'initial': f['initial_state'], 'events': [],
                    'initial_blocks_movement': False})
        timeline = (blob.get('map_features') or {}).get('sha256')
        timeline = timeline or ((blob.get('map_messages') or {}).get('report') or {}).get('sha256')
        result = {'artifact': self.geo.features_sha, 'timeline': timeline, 'keys': keys}
        if payload:
            result['features'] = payload
        return result

    def sample(self, t):
        from app.control import features as cf
        from app.replays.map_feature_motion import sample_effects
        if self._time == t:
            return self._sample
        pool_blocked, pool_occluders = self.pool.sample(t)
        blocked, occluders = pool_blocked.copy(), list(pool_occluders)
        for mf, f, timeline in self.items:
            current = state_at(timeline, t)
            from app.replays.map_feature_state import evaluate
            source_feature = {**f, 'initial_state': timeline['initial']}
            reduced = evaluate(source_feature, [{'t': e['t'], 'kind': 'observed', 'state': e['state']}
                                               for e in timeline['events']], until=t)['state']
            if reduced['state'] != current['state']:
                raise FeatureArtifactCorrupt('source timeline disagrees with the feature reducer')
            if current['pending']:
                raise FeatureArtifactCorrupt('feature endpoint/state unobserved at calculation time')
            if current['state'] == 'broken':
                continue
            if f.get('sliding'):
                effects, occ = sample_effects(self.geo, mf, f, current['closure'])
            else:
                states = {f['id']: current['state']}
                single = {**mf, 'features': [f]}
                effects = cf.movement_blocks(self.geo, single, states)
                occ, pending = cf.sight_occluders(self.geo, single, states)
                effects.pending += pending
            if effects.pending:
                raise FeatureArtifactCorrupt('; '.join(effects.pending))
            blocked |= effects.blocked
            # Vertical masks are fixed. Reuse their arrays across ticks and in the pickle observer cache.
            for index, blocker in enumerate(occ):
                mask_key = (f['id'], index)
                blocker.mask = self._masks.setdefault(mask_key, blocker.mask)
                occluders.append(blocker)
        self._time, self._sample = t, (blocked, occluders)
        return self._sample

    def reopened_by(self, t):
        # Include exact release times, never retroactively spread through a closed panel.
        out = np.full(self.geo.n, -np.inf)
        if self._releases is None:
            self._releases = []
            for when in self.transitions:
                before = self.sample(np.nextafter(when, -np.inf))[0]
                after = self.sample(when)[0]
                nodes = np.flatnonzero(before & ~after)
                if len(nodes):
                    self._releases.append((when, nodes))
        for when, nodes in self._releases:
            if when > t:
                break
            out[nodes] = when
        return out
