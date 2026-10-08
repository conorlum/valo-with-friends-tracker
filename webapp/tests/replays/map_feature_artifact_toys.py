"""Small permanent-source fixtures; importing this module needs no geometry runtime."""
import base64
import json


def source_case():
    from app.replays.map_feature_schema import empty, known
    mf = empty()
    for ordinal, col in ((1, 40), (2, 43)):
        x, y = col * 8, 320
        shape = {'type': 'polygon', 'uv': [[a * 10000 / 1024, b * 10000 / 1024]
                 for a, b in ((x, y), (x + 8, y), (x + 8, y + 8), (x, y + 8))]}
        mf['features'].append({
            'id': f'feature-{ordinal}', 'name': f'Door {ordinal}',
            'bundle': f'bundle-{ordinal}', 'initial_state': 'closed', 'transitions': [],
            'states': [{'name': 'closed', 'blocks_movement': True, 'blocks_sight': True,
                        'footprint': shape, 'sight_bounds': {'ref': 'ground',
                            'bottom': known(0, 'm'), 'top': known(3, 'm')}},
                       {'name': 'open', 'blocks_movement': False, 'blocks_sight': False}],
        })
        mf['bundles'].append({'id': f'bundle-{ordinal}', 'enabled': ordinal == 1,
                              'runtime_consumer': 'test', 'members': [f'feature-{ordinal}']})
    return {'map_features': mf, 'specials': []}


def base_case():
    walk = bytearray(1024 * 1024 // 8)
    for row in range(320, 328):
        walk[row * 128 + 40:row * 128 + 44] = b'\xff' * 4
    def descriptor(packed):
        return {'shape': [1024, 1024], 'bitorder': 'little', 'count': 1024 * 1024,
                'bytes': len(packed), 'data': base64.b64encode(packed).decode('ascii')}
    return {'sight': descriptor(bytes(v ^ 255 for v in walk)),
            'walk': descriptor(walk), 'barrier': None,
            'scale': 7e-5, 'specials': [], 'legacy': {}}


def snapshot_case(entry=None, *, whitespace=False):
    from app.replays.map_feature_inputs import capture_source_snapshot
    raw = json.dumps({'maps': {'Summit': source_case() if entry is None else entry}},
                     indent=2 if whitespace else None, ensure_ascii=False).encode('utf-8')
    return capture_source_snapshot('Summit', raw)
