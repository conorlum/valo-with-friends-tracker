"""Optional height-build diagnostic snapshot protocol; unrelated to evidence identity."""
import base64
import hashlib

from app.replays.map_feature_inputs import capture_source_snapshot, canonical_json, FeatureInputsError

DIAGNOSTICS_PROTOCOL = 1
MAX_DIAGNOSTIC_BYTES = 2 * 1024 * 1024
MAX_PREVIOUS_BYTES = 256 * 1024


def diagnostic_error(map_name, reason, *, code='source_failure', source_sha256=None):
    return {'v': 1, 'map': map_name, 'status': 'error', 'code': code, 'reason': str(reason),
            'raw_source_sha256': source_sha256, 'raw_source_b64': None, 'previous': None}


def diagnostic_envelope(map_name, raw, previous=None, *, bounded=True):
    digest = hashlib.sha256(raw).hexdigest()
    try:
        capture_source_snapshot(map_name, raw)
        if previous is not None and len(canonical_json(previous)) > MAX_PREVIOUS_BYTES:
            previous = None
        envelope = {'v': 1, 'map': map_name, 'status': 'ok', 'raw_source_b64': base64.b64encode(raw).decode('ascii'),
                    'raw_source_sha256': digest, 'previous': previous}
        if bounded and len(canonical_json(envelope)) > MAX_DIAGNOSTIC_BYTES:
            return diagnostic_error(map_name, 'diagnostic snapshot exceeds transport limit',
                                    code='diagnostic_unavailable', source_sha256=digest)
        return envelope
    except (ValueError, TypeError) as exc:
        return diagnostic_error(map_name, exc, source_sha256=digest)


def read_diagnostic(envelope, map_name):
    if not isinstance(envelope, dict) or envelope.get('v') != 1 or envelope.get('map') != map_name:
        raise FeatureInputsError('diagnostic map/version mismatch')
    if envelope.get('status') != 'ok':
        raise FeatureInputsError(envelope.get('reason') or 'diagnostic source unavailable')
    try:
        raw = base64.b64decode(envelope['raw_source_b64'], validate=True)
        if hashlib.sha256(raw).hexdigest() != envelope['raw_source_sha256']:
            raise FeatureInputsError('diagnostic raw source hash mismatch')
        if envelope.get('previous') is not None and len(canonical_json(envelope['previous'])) > MAX_PREVIOUS_BYTES:
            raise FeatureInputsError('previous diagnostic snapshot exceeds limit')
        return capture_source_snapshot(map_name, raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise FeatureInputsError(str(exc)) from exc
