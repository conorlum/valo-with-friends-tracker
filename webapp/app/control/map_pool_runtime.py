"""Explicit source-bound state/motion sampling for the reviewed map-pool families."""
from __future__ import annotations

import math
import numpy as np

from app.replays import map_feature_events as events, map_feature_state as reducer
from app.replays.map_feature_inputs import read_json
from app.replays.map_feature_artifacts import FeatureArtifactCorrupt
from app.replays.control_format import GRID_HZ as ROTATION_HZ

CONSUMER = 'map_pool_replay_v1'
MAPS = frozenset(events.MAP_PREFIXES)


def rotation_cells(panel):
    """Cells whose interiors intersect a polygon; a whole 1x1 cell must clear the slab.

    Include edges as well as paint centres, so a thin corner cannot disappear between
    paint samples. Touching a cell boundary alone does not occupy the adjacent cell.
    """
    from app.control import features as cf
    from app.control.geometry import GRID
    cells = cf.to_grid(cf.raster(panel))
    points = np.asarray(panel['uv'], dtype=float)
    step = 10000 / GRID
    x, y = np.meshgrid((np.arange(GRID) + .5) * step, (np.arange(GRID) + .5) * step)
    half = step / 2 - 1e-7
    for a, b in zip(points, np.roll(points, -1, axis=0)):
        low, high = np.zeros_like(x), np.ones_like(x)
        valid = np.ones_like(cells)
        for coord, origin, delta in ((x, a[0], b[0]-a[0]), (y, a[1], b[1]-a[1])):
            if abs(delta) < 1e-12:
                valid &= np.abs(origin-coord) < half
            else:
                t0, t1 = (coord-half-origin)/delta, (coord+half-origin)/delta
                low = np.maximum(low, np.minimum(t0, t1))
                high = np.minimum(high, np.maximum(t0, t1))
        cells |= valid & (low <= high)
    return cells


def rotation_effects(geo, mf, feature, panel):
    from app.control import features as cf
    # Permanent walls/void already exclude movement; only the swept walkable floor
    # needs a height binding. Ambiguous or missing walkable floors still fail closed.
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
            raise FeatureArtifactCorrupt('active map features require their matching map RPC ledger')
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
                raise FeatureArtifactCorrupt('; '.join(timeline['reasons']))
            result = reducer.evaluate({**f, 'initial_state': timeline['initial']}, timeline['events'],
                                      until=blob['t_end'])
            for e in result['trace']:
                if e.get('notes') or (e['result'] == 'rejected' and e.get('reason') not in ('terminal', 'mid_motion_ignore')):
                    raise FeatureArtifactCorrupt('source event cannot be reduced: ' + str(e))
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
            elif f.get('rotation'):
                fraction = rotation_fraction(f, timeline, t)
                panel = cf.rotation_pose(f, fraction)
                if panel is None:
                    raise FeatureArtifactCorrupt('rotating panel pose unresolved')
                key = (f['id'], fraction)
                if key not in self._rotation_masks:
                    self._rotation_masks[key] = rotation_effects(self.geo, mf, f, panel)
                effects = self._rotation_masks[key]
                blocker, why = cf.resolve_bounds(self.geo, mf, {'bounds': f['rotation']['sight_bounds']},
                                                 cf.to_px(cf.raster(panel)), f['id'])
                occ = [blocker] if blocker else []
                if why:
                    raise FeatureArtifactCorrupt(why)
            else:
                effects = cf.movement_blocks(self.geo, single, pick)
                occ, pending = cf.sight_occluders(self.geo, single, pick)
                effects.pending += pending
            if effects.pending:
                raise FeatureArtifactCorrupt('; '.join(effects.pending))
            blocked |= effects.blocked; occluders += occ
        self._t, self._sample = t, (blocked, occluders)
        return self._sample
