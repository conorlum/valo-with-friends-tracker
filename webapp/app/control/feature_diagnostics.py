"""Complete catalogue diagnostics using the same placement and compilation path as runtime artifacts."""
from app.control import features as compiler
from app.replays import map_feature_inputs as fi, map_feature_schema as schema
from app.replays import map_feature_artifacts as fa


def diagnose_features(geo, source_snapshot, previous=None, *, consumers=None):
    versions = fi.CONSUMER_VERSIONS if consumers is None else consumers
    entry = fi._entry(source_snapshot)
    mf = schema.without_floor_selectors(entry.get('map_features') or schema.empty())
    catalogue, raw_sha, _ = fi.diagnostic_identity(source_snapshot)
    legacy = compiler.legacy_masks(entry)
    statuses = compiler.bundle_status(geo, mf, legacy, frozenset(versions))
    bundles = {b['id']: b for b in mf.get('bundles', [])}
    owner = {member: b['id'] for b in bundles.values() for member in b.get('members', [])}
    entries = []
    for feature in sorted(mf.get('features', []), key=lambda item: item['id']):
        fid, bid = feature['id'], owner.get(feature['id'])
        bundle = bundles.get(bid, {})
        candidate = compiler._candidate_geometry(geo, mf, set(bundle.get('members', [fid])))
        placed = compiler.placement(candidate, mf)[fid]
        reasons = list(placed.reasons)
        reasons.extend(compiler.bounds_problems(candidate, mf, feature))
        if not bundle.get('enabled'):
            runtime = 'disabled'
        elif bundle.get('runtime_consumer') not in versions:
            runtime = 'unregistered'
        else:
            runtime = 'active' if statuses[bid].publishable else 'pending'
        grounds = list(placed.ground_m)
        origin = geo.heights.origin_z / 10 if geo.heights is not None else None
        physical = None if not grounds or origin is None or reasons else [min(grounds) + origin, max(grounds) + origin]
        dependencies = sorted({feature['parent']} if feature.get('parent') else set())
        dependencies += sorted(t['id'] for t in mf.get('triggers', [])
                               if any(target.get('feature') == fid for target in t.get('targets', [])))
        dependencies += sorted(r['id'] for r in mf.get('routes', []) if r.get('owner') == fid)
        entries.append({'id': fid, 'bundle': bid, 'placement': 'pending' if reasons else 'placeable',
                        'runtime': runtime, 'reasons': sorted(reasons, key=lambda r: (r['path'], r['code'])),
                        'dependencies': sorted(set(dependencies)), 'physical_ground_m': physical,
                        'runtime_reasons': statuses[bid].reasons if bid in statuses else ['no owning bundle']})
    pending = sorted(item['id'] for item in entries if item['placement'] == 'pending')
    snapshot = {'v': 1, 'map': geo.name, 'height': geo.height_sha or 'flat', 'source_sha256': raw_sha,
                'catalogue_digest': catalogue, 'pending_ids': pending,
                'physical_ground_m': {item['id']: item['physical_ground_m'] for item in entries}}
    comparison = {'available': False, 'reason': 'previous placement unavailable'}
    if isinstance(previous, dict) and previous.get('v') == 1 and previous.get('map') == geo.name:
        old_pending = set(previous.get('pending_ids', []))
        shifts = []
        ranges = {}
        for item in entries:
            old = (previous.get('physical_ground_m') or {}).get(item['id'])
            new = item['physical_ground_m']
            if old is not None and new is not None:
                delta = [new[i] - old[i] for i in range(2)]
                shifts.extend(abs(value) for value in delta)
                ranges[item['id']] = {'previous': old, 'current': new, 'shift_m': delta}
        comparison = {'available': True, 'previous_height': previous.get('height'),
                      'newly_pending': sorted(set(pending) - old_pending),
                      'still_pending': sorted(set(pending) & old_pending), 'recovered': sorted(old_pending - set(pending)),
                      'ground_ranges': ranges, 'maximum_world_shift_m': max(shifts) if shifts else None,
                      'ground_comparison_reason': None if shifts else 'comparable measured ground unavailable'}
    base = {'sight': fa.pack_mask(geo.sight.astype('uint8').tobytes(), geo.sight.shape),
            'walk': fa.pack_mask(geo.walk_px.astype('uint8').tobytes(), geo.walk_px.shape),
            'barrier': None if geo.barrier is None else fa.pack_mask(geo.barrier.astype('uint8').tobytes(), geo.barrier.shape),
            'scale': geo.uv_per_unit / 10000, 'specials': geo.specials,
            'legacy': {key: fa.pack_mask(mask.astype('uint8').tobytes(), mask.shape) for key, mask in legacy.items()}}
    inp = fi.identify_features(geo.name, geo.height_sha, source_snapshot, base, consumers=versions)
    artifact = compiler.compile_artifact(geo, inp, '0' * 40) if inp is not None else None
    return {'status': 'ok', 'map': geo.name, 'schema': schema.SCHEMA_VERSION, 'compiler': fi.FEATURE_COMPILER_VERSION,
            'normalization': fi.FEATURE_NORMALIZATION_VERSION, 'height': geo.height_sha or 'flat',
            'source_sha256': raw_sha, 'catalogue_digest': catalogue, 'features': entries,
            'counts': {'total_tagged': len(entries), 'placeable': len(entries) - len(pending), 'pending': len(pending)},
            'runtime_manifest': artifact.manifest if artifact is not None else None,
            'snapshot': snapshot, 'comparison': comparison}
