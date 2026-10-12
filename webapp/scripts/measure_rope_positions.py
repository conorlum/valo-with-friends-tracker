"""Measure rope XY from local replay movement, regardless of weapon, speed or noise.

Explicit inputs: --tags --map --export --source --context --out PRIVATE_DIR.
The context contains matching source_sha256, absolute round windows and pawn_slot.
Optional --frozen and --height add qualified stand/walk landing proposals. Neither mode activates runtime.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.control.rope_positions import coordinate_cluster, vertical_runs, rope_seed
from app.replays import contract, map_feature_schema as ms
from app.replays.condense import load_maps
from app.replays.feature_audit import private_output


def measure(tags, map_name, export_dir, context):
    mf = tags['maps'][map_name]['map_features']
    validation = ms.validate(mf, map_name)
    if not validation.ok:
        raise ValueError('source annotations fail schema validation')
    features = {f['id']: f for f in mf['features']}
    ropes = [r for r in mf['routes'] if r.get('kind') == 'rope' and r.get('endpoints')
             and features.get(r.get('owner'), {}).get('preset') == 'vertical_rope']
    if not ropes:
        raise ValueError('no placed vertical ropes to measure')
    seeds = [rope_seed(r, features[r['owner']]) for r in ropes]
    windows = context['windows']
    if not isinstance(windows, list) or not windows or any(
            not isinstance(w, (list, tuple)) or len(w) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in w)
            or not 0 <= w[0] < w[1] <= w[2] for w in windows) \
            or any(a[2] > b[0] for a, b in zip(windows, windows[1:])):
        raise ValueError('invalid absolute round windows')
    starts = [w[0] for w in windows]
    pawns = {int(p): int(slot) for p, slot in context['pawn_slot'].items()}
    if not pawns or any(p <= 0 or slot < 0 or slot >= 10 for p, slot in pawns.items()) \
            or len(set(pawns.values())) != len(pawns):
        raise ValueError('invalid pawn/slot identities')
    map_info = load_maps()[map_name]
    active, sampled, rides = {}, {}, [[] for _ in ropes]

    def finish(pawn):
        visit = active.pop(pawn, None)
        if visit:
            index, ri, samples = visit
            for run in vertical_runs(samples):
                rides[index].append({**run, 'round': ri + 1, 'slot': pawns[pawn],
                    'uv': list(map_info.to_uv(*run['world_xy_cm'])),
                    'elapsed_s': (run['t0_ms'] - starts[ri]) / 1000})

    count, digest = 0, hashlib.sha256()
    movement_path = Path(export_dir) / 'movement.ndjson'
    before = movement_path.stat()
    with movement_path.open('rb') as stream:
        while True:
            line = stream.readline(8 * 1024 * 1024 + 1)
            if not line:
                break
            if len(line) > 8 * 1024 * 1024:
                raise ValueError('movement row exceeds size limit')
            digest.update(line)
            count += 1
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('movement row must be an object')
            pawn = row.get('shooter_character_net_guid') or row.get('actor_net_guid')
            if pawn not in pawns:
                continue
            when, pos = row.get('time_ms'), row.get('position')
            if not isinstance(when, (int, float)) or not math.isfinite(when) or row.get('error_sentinel') \
                    or not isinstance(pos, dict) or not all(isinstance(pos.get(k), (int, float))
                        and math.isfinite(pos[k]) for k in ('x', 'y', 'z')):
                finish(pawn)
                continue
            if when - sampled.get(pawn, -10000) < 50:
                continue
            sampled[pawn] = when
            ri = bisect.bisect_right(starts, when) - 1
            if ri < 0 or when > windows[ri][1]:
                finish(pawn)
                continue
            uv = map_info.to_uv(pos['x'], pos['y'])
            distances = [math.dist(uv, seed) for seed in seeds]
            index = min(range(len(ropes)), key=distances.__getitem__)
            if distances[index] > 160:
                finish(pawn)
                continue
            old = active.get(pawn)
            if old and (old[:2] != (index, ri) or when - old[2][-1]['t'] > 300 or len(old[2]) >= 10000):
                finish(pawn)
            if pawn not in active:
                active[pawn] = (index, ri, [])
            active[pawn][2].append({'t': when, **pos})
    for pawn in list(active):
        finish(pawn)
    after = movement_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('movement export changed during measurement')
    return {'map': map_name, 'rows': count, 'movement_sha256': digest.hexdigest(), 'runtime_enabled': False,
        'qualification': 'coordinate proposals only; no travel-cost, attachment or landing-floor certification',
        'ropes': [{'route': route['id'], 'name': features[route['owner']]['name'], 'seed_uv': seed,
                   **coordinate_cluster(evidence)} for route, seed, evidence in zip(ropes, seeds, rides)]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('tags', 'export', 'source', 'context', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--map', required=True)
    parser.add_argument('--frozen', type=Path)
    parser.add_argument('--height', type=Path)
    parser.add_argument('--asset-dir', type=Path)
    args = parser.parse_args(argv)
    try:
        out = private_output(args.out, args.export)
        manifest = contract.load_manifest(args.export)
        context = json.loads(args.context.read_bytes())
        source_sha = contract.sha256_file(args.source)
        if context.get('source_sha256') != source_sha or manifest.get('source_sha256') != source_sha:
            raise ValueError('source recording, movement export and context identities disagree')
        raw = args.tags.read_bytes()
        result = measure(json.loads(raw), args.map, args.export, context)
        if bool(args.frozen) != bool(args.height):
            raise ValueError('--frozen and --height must be supplied together')
        if args.frozen:
            import numpy as np
            from app.control import geometry as cg, heights as hc
            from app.control.rope_positions import ground_samples, landing_pairs
            from app.replays.condense import read_game_state
            from analyze_height_refinement import FrozenRounds
            frozen = FrozenRounds(args.frozen)
            if frozen.map_name != args.map:
                raise ValueError('frozen recording map differs')
            match = str(read_game_state(contract.load_export_streaming(args.export)).match_uuid)
            geo = cg.attach_heights(cg.load_base_geometry(args.map,args.asset_dir or cg.ASSET_DIR), hc.load_asset(args.height))
            expected_walk = hashlib.sha256(np.packbits(geo.walk_px).tobytes()).hexdigest()[:12]
            if geo.heights.meta.get('walk_sha', expected_walk) != expected_walk:
                raise ValueError('height asset was built on another permanent walk mask; supply its --asset-dir')
            for rope in result['ropes']:
                rides = [dict(match=match, round=r['round'], slot=r['slot'], start_s=r['elapsed_s'],
                              end_s=r['elapsed_s']+(r['t1_ms']-r['t0_ms'])/1000,
                              uv=r['uv'], run_z_range_m=[z/100 for z in r['run_z_range_cm']])
                         for r in rope['rides']]
                points = []
                for match_id, number, blob in frozen:
                    points += ground_samples(blob, geo, match_id, number, rides,
                                             center_uv=rope['uv'] or rope['seed_uv'])
                pairs = landing_pairs(rides, points, max_distance_px=64)
                rope['landing_proposals'] = pairs[:8]
                rope['landing_proposal_count'] = len(pairs)
            result['landing_inputs'] = {'height_sha': geo.height_sha,
                                       'height_file_sha256': contract.sha256_file(args.height),
                                       'frozen_sha256': contract.sha256_file(args.frozen)}
            result['qualification'] = 'coordinate and grounded landing proposals; reviewed costs/directions and runtime remain separate'
        result.update(source_sha256=source_sha, tags_sha256=hashlib.sha256(raw).hexdigest(),
                      context_sha256=hashlib.sha256(args.context.read_bytes()).hexdigest(),
                      manifest_sha256=contract.sha256_file(args.export / 'manifest.json'),
                      tool_sha256=contract.sha256_file(Path(__file__)))
        out.mkdir(parents=True)
        (out / 'positions.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'map': args.map, 'rows': result['rows'], 'out': str(out),
            'ropes': [{k: r[k] for k in ('route', 'name', 'status', 'uv')} for r in result['ropes']]}))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
