"""Shared artifact geometry and replay-state consumers, with the Ascent timeline adapter."""
from __future__ import annotations

import math

import numpy as np

from app.replays.ascent_features import KEYS, VERSION, reverses, state_at
from app.replays.map_feature_artifacts import FeatureArtifactCorrupt, FeatureSourceUnavailable, expanded_assets
from app.replays.map_feature_inputs import read_json

from app.replays import layouts

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
    expected = {'intact', 'broken'} if key == 'ascent_heaven_glass' else {'open', 'opening', 'closing', 'closed', 'broken'}
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
    from app.control.feature_geometry import prepare
    geo = prepare(geo, read_json(artifact.inputs)['runtime'])
    assets = read_json(expanded_assets(artifact.assets))
    if geo.panel_grid_digest and (assets.get('nodes') != geo.n or assets.get('panel_grid') !=
            {'v': 1, 'digest': geo.panel_grid_digest, 'leaf_px': 4, 'selected_height': geo.height_sha}):
        raise FeatureArtifactCorrupt('archived panel tessellation differs from runtime nodes')
    if geo.movement_only and (assets.get('nodes') != geo.n or assets.get('walking_model') !=
            {'kind': 'local_rope_ground', 'digest': geo.rope_ground_digest,
             'asset': geo.movement_height_sha, 'terrain_sight': 'flat'}):
        raise FeatureArtifactCorrupt('archived rope walking model differs from runtime nodes')
    base = assets['base_domain']
    sight = cf._archived_mask(base['sight'], (cg.PX, cg.PX))
    walk = cf._archived_mask(base['walk'], (cg.PX, cg.PX))
    if np.array_equal(sight, geo.sight) and np.array_equal(walk, geo.walk_px):
        return geo
    fresh = cg.geometry_from_masks(geo.name, sight, walk, geo.uv_per_unit / 10000, geo.specials)
    fresh.cover_px, fresh.cover_sha = geo.cover_px, geo.cover_sha   # before the heights: they read the pieces
    if geo.heights is not None:
        cg.attach_heights(fresh, geo.heights)
        fresh.movement_only, fresh.movement_height_sha = geo.movement_only, geo.movement_height_sha
        fresh.height_sha, fresh.rope_ground_digest = geo.height_sha, geo.rope_ground_digest
        fresh.panel_grid_digest, fresh.panel_height_source = geo.panel_grid_digest, geo.panel_height_source
    if fresh.n != geo.n or not np.array_equal(fresh.node_cell, geo.node_cell) \
            or not np.array_equal(fresh.node_space, geo.node_space):
        raise FeatureArtifactCorrupt('feature domain changes archived node identities')
    fresh.barrier, fresh.barrier_sha = geo.barrier, geo.barrier_sha
    fresh.features, fresh.features_sha = artifact, artifact.digest
    return fresh


CLOCK = 10 ** 6   # the analytic clock's resolution (`engine.analytic_times` rounds to 6 decimals)


def _live_openings(timeline):
    """(start, next event's time or inf) for each opening a whole door makes, in one pass over the events.

    An opening after destruction is ignored, as `state_at` ignores it."""
    events = timeline['events']
    terminal = timeline.get('initial') == 'broken'
    for i, event in enumerate(events):
        terminal |= event['state'] == 'broken'
        if event['state'] == 'opening' and not terminal:
            yield float(event['t']), (events[i+1]['t'] if i + 1 < len(events) else math.inf)


def _opening_release(feature, start, duration, later, movement_blocked):
    """The first instant on the analytic clock at which an opening door no longer blocks movement.

    Searched on that clock, so the instant the engine evaluates is the one checked here: a release found on
    the float line (62.97500000000001) rounds back to a blocked 62.975. None when the opening is cut short
    by the next event before it releases, or when blocking is undecided."""
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not duration > 0 \
            or not math.isfinite(duration):
        return None
    # Until the next event the door is this one motion (reversals already failed the round), so the search
    # samples it alone instead of rescanning the whole timeline per step.
    motion = {'initial': 'closed', 'closing_s': duration, 'events': [{'t': start, 'state': 'opening'}]}

    def blocked(k):
        return movement_blocked(feature, state_at(motion, k / CLOCK)['closure'])

    lo = math.ceil(start * CLOCK - 1e-3)                    # blocked: closure 1 at the start
    # 1 ms past the nominal end, inside the 0.25 s grace: at the end itself closure can read 2e-16 (a zero cutoff).
    hi = round((start + duration) * CLOCK) + 1000
    if math.isfinite(later):                                # a last opening has no next event (round end)
        hi = min(hi, math.ceil(later * CLOCK - 1e-3) - 1)
    if hi <= lo or blocked(lo) is not True or blocked(hi) is not False:
        return None
    while hi - lo > 1:                                      # closure falls monotonically while opening
        mid = (lo + hi) // 2
        found = blocked(mid)
        if found is None:
            return None
        lo, hi = (lo, mid) if found is False else (mid, hi)
    return hi / CLOCK


class FeatureRuntime:
    def __init__(self, geo, blob):
        self.geo, self.items, self.times = geo, [], set()
        self._time, self._sample = None, None
        self._masks = {}
        self._releases = None
        self.t_end = blob.get('t_end')
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
        if data.get('v') != VERSION or data.get('status') != 'decoded' or layouts.map_of(geo.name) != 'Ascent':
            if members:
                raise FeatureSourceUnavailable('active Ascent features require a complete map-message timeline')
            return
        evidence = {f['key']: f for f in data.get('features', [])}
        for f in mf.get('features', []):
            if f['id'] not in members:
                continue
            key = f.get('replay_key')
            if readiness(f) or key not in evidence:
                if readiness(f):
                    raise FeatureArtifactCorrupt('active feature definition/binding unsupported')
                raise FeatureSourceUnavailable('feature source binding absent from this round')
            timeline = evidence[key]
            if timeline.get('initial') == 'unknown':
                raise FeatureSourceUnavailable('feature round reset unobserved')
            if reverses(timeline):
                raise FeatureSourceUnavailable('door reversed mid-motion; partial doors are not modelled')
            self.items.append((mf, f, timeline))
            self.times.update(float(e['t']) for e in timeline['events'])
            if f.get('sliding'):
                from app.replays.map_feature_motion import fs_known_metres, closed_state, movement_cutoff, movement_blocked
                top = fs_known_metres(f['sliding']['open_clearance'])
                bottom = fs_known_metres(closed_state(f)['sight_bounds']['bottom'])
                clearance = fs_known_metres(f['sliding'].get('movement_clearance'))
                if top > bottom:
                    fraction = movement_cutoff(f)
                    if fraction is None:
                        fraction = min(max((top-clearance)/(top-bottom), 0), 1)
                    duration = timeline.get('closing_s')
                    for event in timeline['events']:
                        if event['state'] == 'closing':
                            # No usable duration: state_at is pending there, and the round fails when sampled.
                            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                                self.times.add(float(event['t']) + duration * fraction)
                    for start, later in _live_openings(timeline):
                        release = _opening_release(f, start, duration, later, movement_blocked)
                        if release is not None:
                            self.times.add(release)
        self.transitions = sorted(self.times)
        # Fail the round instead of computing through a coverage hole.
        for _, _, timeline in self.items:
            terminal = timeline['initial'] == 'broken'
            for event in timeline['events']:
                if event['state'] == 'unknown' and not terminal:
                    raise FeatureSourceUnavailable('unqualified door-state parameter in active feature timeline')
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
            if f.get('rotation'):
                payload[-1]['movement_geometry'] = 'rotating_panel'
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
                raise FeatureSourceUnavailable('source timeline disagrees with the feature reducer')
            if current['pending']:
                raise FeatureSourceUnavailable('feature endpoint/state unobserved at calculation time')
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
                blocker.cache_resting = current['state'] not in ('closing', 'opening')
                occluders.append(blocker)
        self._time, self._sample = t, (blocked, occluders)
        return self._sample

    def reopened_by(self, t):
        # Include exact release times, never retroactively spread through a closed panel.
        out = np.full(self.geo.n, -np.inf)
        if self._releases is None:
            self._releases = []
            for when in self.transitions:
                # A release projected past the round's end (a door still moving when the round was clipped)
                # is never reached; sampling there can find another door past its motion grace and fail the round.
                if self.t_end is not None and when >= self.t_end:
                    break
                # Moving panels use 1/16-second occupancy steps. Stay outside their
                # floating-point boundary tolerance when checking the preceding pose.
                # The analytic clock's previous tick too: a release at 8.001001 minus 1e-6 is the float
                # 8.001000000000001, already clear, so the release went unrecorded.
                before = self.sample(min(when - 1e-6, (math.ceil(when * CLOCK - 1e-3) - 1) / CLOCK))[0]
                after = self.sample(when)[0]
                nodes = np.flatnonzero(before & ~after)
                if len(nodes):
                    self._releases.append((when, nodes))
        for when, nodes in self._releases:
            if when > t:
                break
            out[nodes] = when
        return out
