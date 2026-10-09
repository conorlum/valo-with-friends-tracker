"""Parent-safe permanent feature snapshots and context-qualified compilation identity."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.replays import map_feature_schema as ms, map_feature_state as state

FEATURE_COMPILER_VERSION = 2
FEATURE_MANIFEST_VERSION = 2
FEATURE_NORMALIZATION_VERSION = 2
FEATURE_CANONICAL_TAG_VERSION = 2
FEATURE_WIRE_VERSION = 1
RUNTIME_CONSUMERS = frozenset()
CONSUMER_VERSIONS = {}


class FeatureInputsError(ValueError):
    pass


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise FeatureInputsError(f'duplicate JSON key: {key}')
        out[key] = value
    return out


def read_json(blob: bytes):
    try:
        return json.loads(blob, object_pairs_hook=_pairs,
                          parse_constant=lambda s: (_ for _ in ()).throw(FeatureInputsError(f'nonfinite {s}')))
    except (ValueError, UnicodeError) as exc:
        raise FeatureInputsError(str(exc)) from exc


@dataclass(frozen=True)
class FeatureKey:
    map_name: str
    height_digest: str
    tags_digest: str
    compiler_version: int


@dataclass(frozen=True)
class FeatureInput:
    key: FeatureKey
    canonical_inputs: bytes
    diagnostic_source: bytes
    source_sha256: str


@dataclass(frozen=True)
class SourceSnapshot:
    map_name: str
    raw_bytes: bytes
    raw_sha256: str
    map_entry_bytes: bytes


def capture_source_snapshot(map_name: str, raw_source: bytes) -> SourceSnapshot:
    raw = bytes(raw_source)
    value = read_json(raw)
    try:
        entry = value['maps'][map_name]
        if not isinstance(entry, dict):
            raise TypeError('map entry must be an object')
        return SourceSnapshot(map_name, raw, hashlib.sha256(raw).hexdigest(), canonical_json(entry))
    except (KeyError, TypeError, ValueError) as exc:
        raise FeatureInputsError(f'{map_name}: invalid or missing map entry: {exc}') from exc


def _entry(source: SourceSnapshot) -> dict:
    if hashlib.sha256(source.raw_bytes).hexdigest() != source.raw_sha256:
        raise FeatureInputsError('source snapshot hash mismatch')
    captured = capture_source_snapshot(source.map_name, source.raw_bytes)
    if captured.map_entry_bytes != source.map_entry_bytes:
        raise FeatureInputsError('source snapshot entry mismatch')
    return read_json(source.map_entry_bytes)


def diagnostic_identity(source: SourceSnapshot) -> tuple[str, str, bytes]:
    entry = _entry(source)
    return hashlib.sha256(ms.tag_canonical_bytes(entry)).hexdigest(), source.raw_sha256, source.raw_bytes


def identify_features(map_name: str, height_digest: str | None, source: SourceSnapshot,
                      base: dict, *, consumers: dict[str, int] | None = None) -> FeatureInput | None:
    if source.map_name != map_name:
        raise FeatureInputsError('snapshot map mismatch')
    entry = _entry(source)
    mf = entry.get('map_features')
    if mf is None:
        return None
    try:
        # Validate unknown JSON values too; source-only floor fields remain archived, not consumed.
        projected = ms.runtime_projection(mf)
        ms.tag_canonical_bytes(projected)
        report = ms.validate(mf, specials=entry.get('specials'))
        if not report.ok:
            raise FeatureInputsError('; '.join(f"{e['where']}: {e['code']}" for e in report.errors))
        versions = dict(CONSUMER_VERSIONS if consumers is None else consumers)
        bundles = [b for b in projected.get('bundles', [])
                   if b.get('enabled') and b.get('runtime_consumer') in versions]
        if not bundles:
            return None
        wanted = {m for b in bundles for m in b.get('members', [])}
        features = {f['id']: f for f in projected.get('features', [])}
        # Parents are required behaviour dependencies, even outside a selected bundle.
        while True:
            parents = {features[m]['parent'] for m in wanted if m in features and features[m].get('parent')}
            if parents <= wanted:
                break
            wanted |= parents
        intended = dict(projected)
        intended['features'] = [f for f in projected.get('features', []) if f['id'] in wanted]
        intended['bundles'] = bundles
        intended['triggers'] = [t for t in projected.get('triggers', [])
                                if any(x.get('feature') in wanted for x in t.get('targets', []))]
        intended['routes'] = [r for r in projected.get('routes', []) if r.get('owner') in wanted]
        outside = [{'id': f['id'], 'base_edits': f['base_edits']}
                   for f in projected.get('features', []) if f['id'] not in wanted and f.get('base_edits')]
        envelope = {'schema': 1, 'normalization': FEATURE_NORMALIZATION_VERSION,
                    'runtime': intended, 'outside_base_edits': outside, 'legacy': base['legacy'], 'base': base,
                    'reducer': {'guards': state.GUARD_VOCABULARY, 'events': list(state.EVENTS),
                                'priority': state.PRIORITY, 'mid_motion': list(state.MID_MOTION),
                                'max_steps': state.MAX_STEPS},
                    'consumers': {b['runtime_consumer']: versions[b['runtime_consumer']] for b in bundles}}
        encoded = canonical_json(ms._js_numbers(envelope))
        key = FeatureKey(map_name, height_digest or 'flat', hashlib.sha256(encoded).hexdigest(),
                         FEATURE_COMPILER_VERSION)
        return FeatureInput(key, encoded, source.raw_bytes, source.raw_sha256)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise FeatureInputsError(str(exc)) from exc
