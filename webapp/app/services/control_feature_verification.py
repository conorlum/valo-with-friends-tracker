"""Independent archive integrity, historical reproduction and current freshness verdicts."""
import base64
import json
from dataclasses import dataclass

from app.models.replay import Replay
from app.replays import control_format as cf
from app.replays.map_feature_artifacts import (FeatureArtifactError, FeatureArtifactMissing,
    UnsupportedFeatureCompiler, check_artifact, encode_artifact)
from app.replays.map_feature_inputs import canonical_json
from app.services.control_feature_artifacts import load_artifact, archived_height_bytes, run_feature_child


@dataclass(frozen=True)
class VerificationResult:
    integrity: str
    recompilation: str
    freshness: str
    reasons: tuple[str, ...] = ()


def _freshness(db, row):
    from app.services import replay_control as rc
    replay = db.get(Replay, row.replay_id)
    if replay is None:
        return 'unavailable', 'current replay unavailable'
    context = rc.resolve_current_geometry(db, replay.map_name)
    if context.state != 'ready':
        return 'unavailable', 'current inputs unavailable: ' + context.state
    fingerprint = rc.round_fingerprint(replay, rc.side_groups(db, replay), row.round_number, context=context)
    if fingerprint is None:
        return 'unavailable', 'current fingerprint unavailable'
    return ('current' if fingerprint == row.fingerprint else 'stale'), None


def verify_stored_round(db, row):
    freshness, why = _freshness(db, row)
    reasons = [why] if why else []
    try:
        summary = cf.unpack_summary(row.summary) if row.summary is not None else {}
        provenance = summary.get('provenance')
        if provenance is None:
            return VerificationResult('unavailable', 'unavailable', freshness,
                                      tuple(reasons + ['legacy round has no recorded feature provenance']))
        inputs = provenance['inputs']
        if provenance['v'] != 1 or set(inputs) != {'control', 'data', 'summary', 'recipe', 'source', 'link', 'geometry', 'figures'}:
            raise ValueError('invalid recorded provenance version/fields')
        fingerprint = cf.fingerprint_from_inputs(inputs)
        if fingerprint != row.fingerprint or provenance['fingerprint'] != fingerprint:
            raise ValueError('recorded fingerprint does not match provenance')
        geometry = inputs['geometry']
        artifact = load_artifact(db, geometry['features'])
        check_artifact(artifact)
        replay = db.get(Replay, row.replay_id)
        if replay is None or artifact.key.map_name != replay.map_name or \
                artifact.key.height_digest != (geometry.get('height') or 'flat'):
            raise ValueError('recorded artifact map/height does not match provenance')
        base = json.loads(artifact.inputs)['base']
        from app.replays.map_feature_sources import descriptor_digest
        if any(descriptor_digest(base[kind]) != geometry.get(kind) for kind in ('sight', 'walk')) or \
                base.get('barrier_sha') != geometry.get('barrier'):
            raise ValueError('recorded permanent masks do not match archive')
        if base['scale'] != geometry.get('scale') or base['specials'] != geometry.get('specials'):
            raise ValueError('recorded permanent context does not match provenance')
        height = archived_height_bytes(db, artifact.digest)
    except FeatureArtifactMissing as exc:
        return VerificationResult('unavailable', 'unavailable', freshness, tuple(reasons + [str(exc)]))
    except Exception as exc:
        return VerificationResult('failed', 'unavailable', freshness, tuple(reasons + [str(exc)]))
    request = {'mode': 'verify', 'artifact': json.loads(encode_artifact(artifact)),
               'height': None if height is None else base64.b64encode(height).decode('ascii')}
    try:
        result = json.loads(run_feature_child('verify', canonical_json(request)))
        if result != {'integrity': 'verified', 'recompilation': 'verified', 'digest': artifact.digest}:
            raise FeatureArtifactError('unexpected historical verification reply')
        return VerificationResult('verified', 'verified', freshness, tuple(reasons))
    except UnsupportedFeatureCompiler as exc:
        return VerificationResult('verified', 'unsupported', freshness, tuple(reasons + [str(exc)]))
    except Exception as exc:
        return VerificationResult('verified', 'failed', freshness, tuple(reasons + [str(exc)]))
