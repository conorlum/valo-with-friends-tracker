"""Speed-independent rope coordinate evidence; this does not qualify travel costs or floors."""
from __future__ import annotations

import math
import statistics


def vertical_runs(points):
    """Stable-XY, monotone-Z portions of a near-rope visit, in world centimetres.

    A pause or horizontal departure ends a portion. There is no climb-speed ceiling
    and no weapon/audio filter. Acceleration rejects typical jump/fall arcs, but
    the result remains geometric evidence, not proof of rope attachment.
    """
    runs, run, sign = [], [], 0
    for a, b in zip(points, points[1:]):
        dt = (b['t'] - a['t']) / 1000
        dz = b['z'] - a['z']
        direction = 1 if dz > 0 else -1
        horizontal = math.hypot(b['x'] - a['x'], b['y'] - a['y']) / dt if dt > 0 else math.inf
        if not (0 < dt <= .3 and abs(dz) > 1.5 and horizontal <= 45):
            if run:
                runs.append(run)
            run, sign = [], 0
        elif sign != direction:
            if run:
                runs.append(run)
            run, sign = [a, b], direction
        else:
            run.append(b)
    if run:
        runs.append(run)
    accepted = []
    for run in runs:
        duration = (run[-1]['t'] - run[0]['t']) / 1000
        speeds = [(b['z'] - a['z']) * 1000 / (b['t'] - a['t']) for a, b in zip(run, run[1:])]
        speed = statistics.median(speeds)
        mad = statistics.median(abs(v - speed) for v in speeds)
        xy = [statistics.median(p[k] for p in run) for k in ('x', 'y')]
        radius = max(math.hypot(p['x'] - xy[0], p['y'] - xy[1]) for p in run)
        if duration >= .25 and abs(run[-1]['z'] - run[0]['z']) >= 150 \
                and mad <= max(30, abs(speed) * .15) and radius <= 25:
            accepted.append({'t0_ms': run[0]['t'], 't1_ms': run[-1]['t'], 'world_xy_cm': xy,
                'radius_cm': radius, 'speed_cm_s': speed, 'speed_mad_cm_s': mad,
                'run_z_range_cm': sorted((run[0]['z'], run[-1]['z']))})
    return accepted


def coordinate_cluster(rides, radius_uv=25):
    """One vote per ride, requiring repeated rounds and a dominant spatial cluster.

    Duplicated frames/RPCs do not increase support. Separate equal-size clusters
    remain ambiguous instead of averaging two locations into a fabricated rope.
    """
    unique = {(r['round'], r['slot'], r['t0_ms'], r['t1_ms']): r for r in rides}
    ordered = sorted(unique.values(), key=lambda r: (r['round'], r['slot'], r['t0_ms'], r['t1_ms']))
    if not ordered:
        return {'status': 'insufficient', 'uv': None, 'rides': []}
    center = [statistics.median(r['uv'][i] for r in ordered) for i in (0, 1)]
    members = [r for r in ordered if math.dist(r['uv'], center) <= radius_uv]
    if len(members) < 2 or len({r['round'] for r in members}) < 2 or len(members) / len(ordered) < .75:
        return {'status': 'insufficient_or_ambiguous', 'uv': None, 'rides': ordered}
    center = [round(statistics.median(r['uv'][i] for r in members), 2) for i in (0, 1)]
    return {'status': 'coordinate_proposal', 'uv': center, 'rides': members,
            'excluded_rides': len(ordered) - len(members),
            'radius_uv': max(math.dist(r['uv'], center) for r in members)}


def landing_pairs(rides, ground, *, max_gap_s=2, max_distance_px=22):
    """Propose distinct measured landings from ground samples beside a vertical ride.

    Callers qualify the ground samples and resolve their nodes on an exact height asset first.
    Each sample's t0/t1 is its actual sampling interval, not the interval of a long standing run
    whose mean XY may be far from the rope. Match/round/slot identities must all agree. Ride
    extrema only corroborate the span: they never supply an endpoint height or a travel cost.
    These are geometry proposals; reviewed quiet costs/directions remain separate.
    """
    pairs = []
    for ride in rides:
        ends = []
        for edge in (ride['start_s'], ride['end_s']):
            options = []
            for sample in ground:
                if any(sample.get(k) != ride.get(k) for k in ('match', 'round', 'slot')):
                    continue
                if any(ride.get(k) is None for k in ('match', 'round', 'slot')) or sample.get('node') is None:
                    continue
                gap = max(sample['t0'] - edge, edge - sample['t1'], 0)
                distance = math.dist(sample['uv'], ride['uv']) * 1024 / 10000
                if gap <= max_gap_s and distance <= max_distance_px:
                    options.append({**sample, 'gap': gap, 'distance_px': distance})
            ends.append(options)
        for a in ends[0]:
            for b in ends[1]:
                if a['node'] == b['node'] or abs(a['world_z'] - b['world_z']) < 1.5:
                    continue
                low, high = sorted((a['world_z'], b['world_z']))
                span = ride['run_z_range_m']
                if span[0] < low - .5 or span[1] > high + .5 or span[1] - span[0] < (high - low) * .5:
                    continue
                pairs.append({'ride': ride, 'a': a, 'b': b,
                    'score': a['gap'] + b['gap'] + (a['distance_px'] + b['distance_px']) / 100})
    return sorted(pairs, key=lambda pair: pair['score'])
