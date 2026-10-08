"""Immutable planned inputs and actual-input verification, without the control engine."""
from dataclasses import dataclass
import json
import zlib

from app.replays import control_format as cf


class InvalidControlInputs(ValueError):
    pass


class StaleControlInputs(InvalidControlInputs):
    pass


@dataclass(frozen=True, init=False)
class PinnedInputs:
    geometry_json: bytes
    envelope_json: bytes | None
    feature_key: object
    artifact_digest: str | None
    source_sha256: str | None
    height_mode: str

    def __init__(self, geometry, *, envelope=None, feature_key=None, artifact_digest=None,
                 source_sha256=None, height_mode='flat'):
        for name, value in {'geometry_json': json.dumps(geometry, sort_keys=True).encode(),
                            'envelope_json': None if envelope is None else json.dumps(envelope, sort_keys=True).encode(),
                            'feature_key': feature_key, 'artifact_digest': artifact_digest,
                            'source_sha256': source_sha256, 'height_mode': height_mode}.items():
            object.__setattr__(self, name, value)

    @property
    def geometry(self):
        return json.loads(self.geometry_json)

    @property
    def envelope(self):
        return None if self.envelope_json is None else json.loads(self.envelope_json)

    def for_round(self, envelope):
        return PinnedInputs(self.geometry, envelope=envelope, feature_key=self.feature_key,
                            artifact_digest=self.artifact_digest, source_sha256=self.source_sha256,
                            height_mode=self.height_mode)


def verify_result_inputs(planned, result):
    if result.get('error_kind') in ('infra', 'compat'):
        raise InvalidControlInputs('retryable failure cannot be stored')
    if result.get('status') != 'ok':
        return None
    if planned is None:
        if (result.get('geometry') or {}).get('features'):
            raise InvalidControlInputs('feature result has no pinned inputs')
        return None
    if result.get('geometry') != planned.geometry:
        raise InvalidControlInputs('actual geometry differs from planned geometry')
    if not planned.artifact_digest:
        return None
    try:
        summary = cf.unpack_summary(result['summary'])
        provenance = summary['provenance']
        inputs = provenance['inputs']
        if provenance['v'] != 1 or inputs != planned.envelope or \
                inputs['geometry']['features'] != planned.artifact_digest or \
                provenance['fingerprint'] != cf.fingerprint_from_inputs(inputs) or \
                inputs['figures'] != cf.figures_hash() or \
                (inputs['control'], inputs['data'], inputs['summary']) != \
                (cf.CONTROL_REVISION, cf.DATA_VERSION, cf.SUMMARY_VERSION):
            raise InvalidControlInputs('invalid actual-input provenance')
        return provenance
    except (ValueError, KeyError, TypeError, OSError, EOFError, zlib.error) as exc:
        raise InvalidControlInputs(str(exc)) from exc
