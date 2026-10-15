"""Explicit source-bound state/motion sampling for the reviewed map-pool families."""
from __future__ import annotations

import math
import numpy as np

from app.replays import map_feature_events as events, map_feature_state as reducer
from app.replays.map_feature_inputs import read_json
from app.replays.map_feature_artifacts import FeatureArtifactCorrupt, FeatureSourceUnavailable
from app.replays.control_format import GRID_HZ as ROTATION_HZ

CONSUMER = 'map_pool_replay_v1'
MAPS = frozenset(events.MAP_PREFIXES)
ROTATION_BLOCK_FRACTION = .5


def rotation_coverage(panel, grid=None):
    """Exact polygon area in each movement cell, independent of paint-centre samples."""
    from app.control.geometry import GRID
    GRID = GRID if grid is None else grid
    coverage = np.zeros((GRID, GRID), float)
    points = np.asarray(panel['uv'], dtype=float)
    step = 10000 / GRID
    lower = np.maximum(0, np.floor(points.min(axis=0)/step).astype(int))
    upper = np.minimum(GRID, np.ceil(points.max(axis=0)/step).astype(int))
    for row in range(lower[1], upper[1]):
        for col in range(lower[0], upper[0]):
            # Clip in local cell coordinates to avoid cancellation in area sums
            # far from the map origin. A cell is a unit square in this frame.
            polygon = ((points / step) - [col, row]).tolist()
            for axis, bound, sign in ((0, 0, 1), (0, 1, -1), (1, 0, 1), (1, 1, -1)):
                if not polygon:
                    break
                clipped = []
                previous = polygon[-1]
                before = sign * (previous[axis]-bound) >= 0
                for current in polygon:
                    inside = sign * (current[axis]-bound) >= 0
                    if inside != before:
                        fraction = (bound-previous[axis]) / (current[axis]-previous[axis])
                        clipped.append([previous[i] + fraction*(current[i]-previous[i]) for i in (0, 1)])
                    if inside:
                        clipped.append(current)
                    previous, before = current, inside
                polygon = clipped
            if len(polygon) >= 3:
                coverage[row, col] = min(1., abs(sum(a[0]*b[1]-b[0]*a[1]
                    for a, b in zip(polygon, polygon[1:]+polygon[:1]))) / 2)
    return coverage


def rotation_cells(panel):
    """Owner's permissive movement rule: only a majority-covered cell is blocked."""
    # A mathematical half remains open despite roundoff at rotated/shared edges.
    return rotation_coverage(panel) > ROTATION_BLOCK_FRACTION + 1e-10


def rotation_effects(geo, mf, feature, panel):
    from app.control import features as cf
    # Permanent walls/void already exclude movement; only the swept walkable floor
    # needs a height binding. Ambiguous or missing walkable floors still fail closed.
    if geo.adaptive:
        coverage = rotation_coverage(panel, 256)
        areas = np.bincount(geo.space_grid.ravel(), weights=coverage.ravel(), minlength=len(geo.space_walk))
        counts = np.bincount(geo.space_grid.ravel(), minlength=len(geo.space_walk))
        cells = (areas / np.maximum(counts, 1) > ROTATION_BLOCK_FRACTION + 1e-10) & geo.space_walk
    else:
        cells = rotation_cells(panel).ravel() & geo.walk.ravel()
    effects = cf.empty_effects(geo)
    if cells.any():
        binding = cf.feature_floor_nodes(geo, mf, feature, cells)
        effects.blocked[binding.nodes] = True
        effects.pending += binding.pending
    return effects


def rotation_fraction(feature, timeline, t):
    """One activation clock, sampled after each 1/16 second of physical movement."""
    _, fraction = sampled_state(feature, timeline, t)
    duration = reducer.known(feature['rotation']['duration'])
    turn = math.floor(fraction)
    ticks = math.floor((fraction-turn) * duration * ROTATION_HZ + 1e-8)
    return turn + ticks / (duration * ROTATION_HZ)


def readiness(feature):
    from app.replays import map_feature_motion as motion, map_feature_schema as ms
    reasons = events.binding_problems(feature.get('replay_source'))
    if feature.get('parent') or feature.get('platform'):
        reasons.append('attached panels/platforms unsupported')
    if feature.get('initial_state') is None:
        reasons.append('round start state missing')
    for row in feature.get('transitions', []):
        timings = []
        if row.get('motion') is not None:
            timings.append((row['motion'].get('duration'), True))
        if row.get('follow_up') is not None:
            timings.append((row['follow_up'].get('after'), False))
        for value, positive in timings:
            seconds = reducer.known(value)
            if seconds is None or not math.isfinite(seconds) or seconds < 0 or (positive and seconds == 0):
                reasons.append('transition timing unresolved')
        if row.get('motion') and row.get('mid_motion') not in ('ignore', 'restart', 'queue', 'reverse'):
            reasons.append('mid-motion policy unresolved')
        if 'unresolved' in str(row.get('guard')):
            reasons.append('transition guard unresolved')
    if feature.get('sliding'):
        reasons += motion.geometry_problems(feature) + motion.vertical_problems(feature) + motion.movement_problems(feature)
    rot = feature.get('rotation')
    if rot:
        rep = ms.Report()
        ms._geometry(rep, 'pivot', rot.get('pivot'), ('point',))
        ms._geometry(rep, 'panel', rot.get('panel'), ('polygon',))
        if rep.errors:
            reasons.append('rotation needs a point pivot and physical panel')
        duration = reducer.known(rot.get('duration'))
        if duration is None or not math.isfinite(duration) or duration <= 0:
            reasons.append('full rotation cycle duration unresolved')
        if rot.get('phases'):
            reasons.append('map-pool consumer requires continuous rotation, not preview phases')
        if any(reducer.known(rot.get(k)) is None or not math.isfinite(reducer.known(rot.get(k)))
               for k in ('start_deg', 'end_deg')) \
                or (rot.get('direction') or {}).get('value') not in ('cw', 'ccw') \
                or (rot.get('direction') or {}).get('status') != 'known':
            reasons.append('rotation angles/direction unresolved')
        if not isinstance(rot.get('sight_bounds'), dict):
            reasons.append('rotating panel height bounds missing')
        if any(row.get('motion') and row.get('mid_motion') != 'ignore' for row in feature.get('transitions', [])):
            reasons.append('rotation currently requires ignore during motion')
    return reasons


def sampled_state(feature, timeline, t):
    """A single activation clock survives every intermediate door phase."""
    source = {**feature, 'initial_state': timeline['initial']}
    result = reducer.evaluate(source, timeline['events'], until=t)
    state = result['state']
    rot = feature.get('rotation')
    fraction = None
    if rot:
        duration = reducer.known(rot.get('duration'))
        if duration is not None and duration > 0:
            starts = [e['t'] for e in result['trace'] if e['result'] == 'applied'
                      and e['kind'] in ('switch', 'activate', 'shoot')]
            fraction = 0.0 if not starts else len(starts) - 1 + min(max((t-starts[-1])/duration, 0), 1)
    return state, fraction


class MapPoolRuntime:
    def __init__(self, geo, blob):
        self.geo, self.items, self.transitions = geo, [], []
        self._t, self._sample = None, None
        self._rotation_masks = {}
        self._rotation_sight = {}
        self._fixed_sight = {}
        self._sliding_masks = {}
        self._source_areas = {}
        if not geo.features:
            return
        artifact = geo.features
        mf = read_json(artifact.inputs)['runtime']
        members = {m for b in mf.get('bundles', []) if b['id'] in artifact.manifest['active_bundles']
                   and b.get('runtime_consumer') == CONSUMER for m in b['members']}
        if not members:
            return
        data = blob.get('map_messages') or {}
        if geo.name not in MAPS or data.get('map') != geo.name or data.get('v') != events.VERSION:
            raise FeatureSourceUnavailable('active map features require their matching map RPC ledger; reparse this recording')
        times = set()
        for f in mf.get('features', []):
            if f['id'] not in members:
                continue
            problems = readiness(f)
            if problems:
                raise FeatureArtifactCorrupt('; '.join(problems))
            timeline = events.bind_round(f, data.get('ledger', []), data.get('report', {}),
                data['start_ms'], data['end_ms'], data['previous_end_ms'])
            if timeline['status'] != 'bound':
                raise FeatureSourceUnavailable('; '.join(timeline['reasons']))
            result = reducer.evaluate({**f, 'initial_state': timeline['initial']}, timeline['events'],
                                      until=blob['t_end'])
            for e in result['trace']:
                if e.get('notes') or (e['result'] == 'rejected' and e.get('reason') not in ('terminal', 'mid_motion_ignore')):
                    raise FeatureSourceUnavailable('source event cannot be reduced: ' + str(e))
                times.add(e['t'])
            # A descending panel can change movement between source events.
            if f.get('sliding'):
                from app.replays.map_feature_motion import movement_cutoff
                cutoff = movement_cutoff(f)
                if cutoff is None:
                    raise FeatureArtifactCorrupt('map-pool descending doors need an explicit movement cutoff')
                for e in timeline['events']:
                    reduced = reducer.evaluate({**f, 'initial_state': timeline['initial']}, timeline['events'], until=e['t'])['state']
                    motion = reduced.get('motion')
                    if motion:
                        opened, closed = f['sliding']['open_state'], f['sliding']['closed_state']
                        part = cutoff if motion['to'] == closed else 1-cutoff
                        if motion['from'] in (opened, closed) and motion['to'] in (opened, closed):
                            times.add(motion['start'] + motion['duration'] * part)
            self.items.append((mf, f, timeline))
            if f.get('rotation'):
                from app.control import features as cf
                duration = reducer.known(f['rotation']['duration'])
                starts = [e['t'] for e in result['trace'] if e['result'] == 'applied'
                          and e['kind'] in ('switch', 'activate', 'shoot')]
                # Cell releases must be calculation boundaries: unknown cannot enter
                # newly vacated floor using a travel time from before it was clear.
                previous = None
                for start in starts:
                    for step in range(math.ceil(duration * ROTATION_HZ) + 1):
                        when = start + min(step / ROTATION_HZ, duration)
                        if when > blob['t_end']:
                            break
                        fraction = rotation_fraction(f, timeline, when)
                        key = (f['id'], fraction)
                        if key not in self._rotation_masks:
                            self._rotation_masks[key] = rotation_effects(geo, mf, f, cf.rotation_pose(f, fraction))
                        effects = self._rotation_masks[key]
                        if effects.pending:
                            raise FeatureArtifactCorrupt('; '.join(effects.pending))
                        if previous is not None and not np.array_equal(previous, effects.blocked):
                            times.add(when)
                        previous = effects.blocked
        self.transitions = sorted(times)

    @property
    def active(self):
        return bool(self.items)

    def sample(self, t):
        from app.control import features as cf
        from app.replays import map_feature_motion as motion
        if t == self._t:
            return self._sample
        blocked, occluders = np.zeros(self.geo.n, bool), []
        for mf, f, timeline in self.items:
            state, _ = sampled_state(f, timeline, t)
            pick = {f['id']: state['state']}
            single = {**mf, 'features': [f]}
            if f.get('sliding'):
                closure = motion.closure_at(f, state, t)
                if closure is None and next(s for s in f['states'] if s['name'] == state['state']).get('terminal'):
                    continue
                effects, occ = motion.sample_effects(self.geo, mf, f, closure)
                # Height bands change while descending, but the panel's XY mask is fixed.
                for index, blocker in enumerate(occ):
                    blocker.mask = self._sliding_masks.setdefault((f['id'], index), blocker.mask)
                    blocker.cache_resting = closure in (0., 1.)
            elif f.get('rotation'):
                fraction = rotation_fraction(f, timeline, t)
                panel = cf.rotation_pose(f, fraction)
                if panel is None:
                    raise FeatureArtifactCorrupt('rotating panel pose unresolved')
                key = (f['id'], fraction)
                if key not in self._rotation_masks:
                    self._rotation_masks[key] = rotation_effects(self.geo, mf, f, panel)
                effects = self._rotation_masks[key]
                if key not in self._rotation_sight:
                    blocker, why = cf.rotation_sight(self.geo, mf, f, panel)
                    if why:
                        raise FeatureArtifactCorrupt(why)
                    if blocker is not None:
                        blocker.cache_resting = fraction == math.floor(fraction)
                    if blocker is not None and f.get('sight_source_area') is not None:
                        from app.control.feature_visibility import source_area
                        if f['id'] not in self._source_areas:
                            self._source_areas[f['id']] = source_area(self.geo, f)
                        blocker.source_area = self._source_areas[f['id']]
                    self._rotation_sight[key] = [blocker] if blocker else []
                occ = self._rotation_sight[key]
            else:
                effects = cf.movement_blocks(self.geo, single, pick)
                key = (f['id'], state['state'])
                if key not in self._fixed_sight:
                    self._fixed_sight[key] = cf.sight_occluders(self.geo, single, pick)
                occ, pending = self._fixed_sight[key]
                effects.pending += pending
            if effects.pending:
                raise FeatureArtifactCorrupt('; '.join(effects.pending))
            blocked |= effects.blocked; occluders += occ
        self._t, self._sample = t, (blocked, occluders)
        return self._sample
