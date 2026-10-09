"""One bounded isolated compile/diagnose/verify request. Parents never import this module."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from app.replays.map_feature_inputs import FeatureInput, FeatureKey, canonical_json, read_json
from app.replays.map_feature_artifacts import (MAX_WIRE_BYTES, FeatureArtifactError, FeatureArtifactCorrupt,
                                               decode_artifact, encode_artifact, feature_failure)


def _height(value):
    if value is None:
        return None
    from app.control.heights import load_asset
    raw = base64.b64decode(value, validate=True)
    with tempfile.TemporaryDirectory(prefix='feature-height-') as folder:
        path = Path(folder) / 'height.npz'
        path.write_bytes(raw)
        return load_asset(path)


def _commit():
    try:
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, timeout=5, check=False)
        value = result.stdout.decode('ascii').strip()
        if result.returncode == 0 and len(value) == 40:
            return value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return '0' * 40  # packaged image may omit Git; this audit field never selects executable code


def run(request):
    from app.control.features import compile_artifact, verify_artifact, geometry_from_feature_inputs
    mode = request.get('mode')
    if mode == 'compile':
        if set(request) != {'mode', 'inputs', 'height'} or set(request['inputs']) != {'key', 'canonical_inputs'}:
            raise FeatureArtifactCorrupt('conflicting compile request fields')
        inp = FeatureInput(FeatureKey(**request['inputs']['key']),
                           base64.b64decode(request['inputs']['canonical_inputs'], validate=True), b'', '')
        geo = geometry_from_feature_inputs(inp, _height(request['height']))
        return json.loads(encode_artifact(compile_artifact(geo, inp, _commit())))
    if mode == 'verify':
        if set(request) != {'mode', 'artifact', 'height'}:
            raise FeatureArtifactCorrupt('conflicting verify request fields')
        artifact = decode_artifact(canonical_json(request['artifact']))
        inp = FeatureInput(artifact.key, artifact.inputs, b'', '')
        geo = geometry_from_feature_inputs(inp, _height(request['height']))
        verify_artifact(artifact, geo)
        return {'integrity': 'verified', 'recompilation': 'verified', 'digest': artifact.digest}
    if mode == 'diagnose':
        if set(request) != {'mode', 'map', 'source', 'base', 'height'}:
            raise FeatureArtifactCorrupt('conflicting diagnostic request fields')
        from app.control.feature_diagnostics import diagnose_features
        from app.replays.map_feature_diagnostics import read_diagnostic
        source = read_diagnostic(request['source'], request['map'])
        asset = _height(request['height'])
        key = FeatureKey(request['map'], asset.digest if asset is not None else 'flat', '0' * 64, 2)
        inp = FeatureInput(key, canonical_json({'base': request['base']}), b'', '')
        geo = geometry_from_feature_inputs(inp, asset)
        return diagnose_features(geo, source, request['source'].get('previous'))
    raise FeatureArtifactCorrupt(f'unsupported feature child mode: {mode}')


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_WIRE_BYTES + 1)
        if len(raw) > MAX_WIRE_BYTES:
            raise FeatureArtifactCorrupt('feature request size limit')
        result = run(read_json(raw))
        reply = {'ok': True, 'result': result}
    except Exception as exc:
        error = exc if isinstance(exc, FeatureArtifactError) else FeatureArtifactCorrupt(str(exc))
        failure = feature_failure(error)
        reply = {'ok': False, 'code': failure['error_code'], 'reason': failure['error']}
    sys.stdout.buffer.write(canonical_json(reply))
    sys.stdout.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main())
