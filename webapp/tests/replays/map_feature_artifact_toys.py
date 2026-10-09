"""Small permanent-source fixtures; importing this module needs no geometry runtime."""
import base64
import json

import pytest


@pytest.fixture(autouse=True)
def synthetic_consumer_runtime(monkeypatch):
    """Explicit test runtime support, copied into fresh verifier children independently of requests."""
    import subprocess
    from app.replays import map_feature_inputs as fi
    monkeypatch.setattr(fi, 'CONSUMER_VERSIONS', {'test': 1})
    popen = subprocess.Popen
    def launch(args, *positional, **kwargs):
        if isinstance(args, (list, tuple)) and list(args[1:]) == ['-m', 'app.control.feature_job']:
            code = ("from app.replays import map_feature_inputs as fi; "
                    f"fi.CONSUMER_VERSIONS={dict(fi.CONSUMER_VERSIONS)!r}; "
                    "from app.control.feature_job import main; raise SystemExit(main())")
            args = [args[0], '-c', code]
        return popen(args, *positional, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', launch)


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


def geometry_case(*, ground_dm=0, origin_dm=100, multi=False, unresolved=False, flat=False):
    import numpy as np
    from app.control.geometry import geometry_from_masks, attach_heights
    from app.control.heights import HeightAsset, MAX_FLOORS
    walk = np.zeros((1024, 1024), dtype=bool)
    walk[320:328, 320:352] = True
    geo = geometry_from_masks('Summit', ~walk, walk, 7e-5, [])
    if flat:
        return geo
    floors = np.full((128, 128, MAX_FLOORS), -1, dtype=np.int16)
    floors[40, 40:44, 0] = ground_dm
    if multi:
        floors[40, 40, 1] = ground_dm + 40
    if unresolved:
        floors[40, 40, :] = -1
    missing = floors[..., 0] < 0
    asset = HeightAsset(floors, np.zeros_like(floors), ~missing, missing,
                        np.empty((0, 5), dtype=np.int32), {'origin_z': origin_dm})
    return attach_heights(geo, asset)
