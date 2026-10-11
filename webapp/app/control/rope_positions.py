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
