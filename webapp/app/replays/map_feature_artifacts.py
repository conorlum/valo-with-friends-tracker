"""Immutable feature archive wire format and bounded integrity checks; standard library only."""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import zlib
from collections import OrderedDict
from dataclasses import asdict, dataclass
from math import prod

from app.replays.map_feature_inputs import FeatureKey, FEATURE_WIRE_VERSION, canonical_json, read_json

MAX_WIRE_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_CACHE_ENTRIES = 16
MAX_CACHE_BYTES = 64 * 1024 * 1024


class FeatureArtifactError(ValueError):
    pass


class FeatureArtifactMissing(FeatureArtifactError):
    pass


class FeatureArtifactCorrupt(FeatureArtifactError):
    pass


class UnsupportedFeatureCompiler(FeatureArtifactError):
    pass


@dataclass(frozen=True)
class FeatureArtifact:
    digest: str
    key: FeatureKey
    manifest: dict
    inputs: bytes
    assets: bytes
    code_commit: str


@dataclass(frozen=True)
class FeatureArtifactRef:
    digest: str
    key: FeatureKey
    manifest: dict


def feature_failure(error: FeatureArtifactError) -> dict:
    if isinstance(error, UnsupportedFeatureCompiler):
        kind, code = 'compat', 'features_unsupported'
    elif isinstance(error, FeatureArtifactMissing):
        kind, code = 'infra', 'features_missing'
    else:
        kind, code = 'infra', 'features_corrupt'
    return {'error_kind': kind, 'error_code': code, 'error': str(error)}


def _b64(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > (MAX_WIRE_BYTES * 4 // 3 + 4):
        raise FeatureArtifactCorrupt('base64 size/type limit')
    try:
        raw = base64.b64decode(value, validate=True)
        if base64.b64encode(raw).decode('ascii') != value:
            raise FeatureArtifactCorrupt('noncanonical base64')
        return raw
    except (ValueError, binascii.Error) as exc:
        raise FeatureArtifactCorrupt('invalid base64') from exc


def _shape(shape) -> int:
    if not isinstance(shape, (tuple, list)) or not 1 <= len(shape) <= 4 or \
            any(type(n) is not int or n <= 0 for n in shape):
        raise FeatureArtifactCorrupt('invalid mask shape')
    count = prod(shape)
    if count > MAX_EXPANDED_BYTES:
        raise FeatureArtifactCorrupt('mask element limit')
    return count


def pack_mask(raw: bytes, shape: tuple[int, ...]) -> dict:
    count = _shape(shape)
    if len(raw) != count or any(v not in (0, 1) for v in raw):
        raise FeatureArtifactCorrupt('mask values/shape mismatch')
    packed = bytearray((count + 7) // 8)
    for i, bit in enumerate(raw):
        if bit:
            packed[i // 8] |= 1 << (i % 8)
    return {'shape': list(shape), 'bitorder': 'little', 'count': count, 'bytes': len(packed),
            'data': base64.b64encode(packed).decode('ascii')}


def unpack_mask(descriptor: dict) -> bytes:
    try:
        count = _shape(descriptor['shape'])
        if descriptor['bitorder'] != 'little' or type(descriptor['count']) is not int or descriptor['count'] != count \
                or type(descriptor['bytes']) is not int or descriptor['bytes'] != (count + 7) // 8:
            raise FeatureArtifactCorrupt('invalid mask descriptor')
        packed = _b64(descriptor['data'])
        if len(packed) != descriptor['bytes'] or (count % 8 and packed[-1] >> (count % 8)):
            raise FeatureArtifactCorrupt('mask byte count/padding mismatch')
        return bytes((packed[i // 8] >> (i % 8)) & 1 for i in range(count))
    except (KeyError, TypeError, IndexError) as exc:
        raise FeatureArtifactCorrupt('invalid mask descriptor') from exc


def expanded_assets(blob: bytes) -> bytes:
    """One complete gzip member, bounded before allocating expanded data."""
    if not isinstance(blob, bytes) or len(blob) > MAX_WIRE_BYTES:
        raise FeatureArtifactCorrupt('compressed artifact limit')
    try:
        reader = zlib.decompressobj(zlib.MAX_WBITS | 16)
        data = reader.decompress(blob, MAX_EXPANDED_BYTES + 1)
        if len(data) > MAX_EXPANDED_BYTES or reader.unconsumed_tail:
            raise FeatureArtifactCorrupt('expanded artifact limit')
        data += reader.flush(MAX_EXPANDED_BYTES + 1 - len(data))
        if len(data) > MAX_EXPANDED_BYTES:
            raise FeatureArtifactCorrupt('expanded artifact limit')
        if not reader.eof or reader.unused_data:
            raise FeatureArtifactCorrupt('incomplete or trailing gzip content')
        return data
    except zlib.error as exc:
        raise FeatureArtifactCorrupt('invalid gzip content') from exc


def _key(value: dict) -> FeatureKey:
    try:
        key = FeatureKey(**value)
        if not isinstance(key.map_name, str) or not 1 <= len(key.map_name) <= 64 \
                or not isinstance(key.height_digest, str) or not re.fullmatch(r'flat|[0-9a-f]{12}', key.height_digest) \
                or not isinstance(key.tags_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', key.tags_digest) \
                or type(key.compiler_version) is not int or key.compiler_version <= 0:
            raise FeatureArtifactCorrupt('invalid artifact key')
        return key
    except (TypeError, AttributeError) as exc:
        raise FeatureArtifactCorrupt('invalid artifact key') from exc


def check_header(ref: FeatureArtifactRef) -> None:
    try:
        if _key(asdict(ref.key)) != ref.key or ref.manifest['key'] != asdict(ref.key) \
                or ref.manifest['v'] != 2 or hashlib.sha256(canonical_json(ref.manifest)).hexdigest() != ref.digest:
            raise FeatureArtifactCorrupt('manifest/key/content address mismatch')
    except (ValueError, TypeError, KeyError) as exc:
        raise FeatureArtifactCorrupt(str(exc)) from exc


def _masks(value):
    if isinstance(value, dict):
        if 'bitorder' in value:
            unpack_mask(value)
        else:
            for item in value.values():
                _masks(item)
    elif isinstance(value, list):
        for item in value:
            _masks(item)


def check_artifact(artifact: FeatureArtifact) -> None:
    try:
        check_header(FeatureArtifactRef(artifact.digest, artifact.key, artifact.manifest))
        if len(artifact.inputs) > MAX_EXPANDED_BYTES:
            raise FeatureArtifactCorrupt('input size limit')
        inputs = read_json(artifact.inputs)
        if canonical_json(inputs) != artifact.inputs:
            raise FeatureArtifactCorrupt('noncanonical inputs')
        inputs_sha = hashlib.sha256(artifact.inputs).hexdigest()
        if inputs_sha != artifact.key.tags_digest or artifact.manifest['inputs_sha256'] != inputs_sha:
            raise FeatureArtifactCorrupt('input digest mismatch')
        _masks(inputs)
        expanded = expanded_assets(artifact.assets)
        parts = read_json(expanded)
        if not isinstance(parts, dict) or canonical_json(parts) != expanded:
            raise FeatureArtifactCorrupt('noncanonical compiled parts')
        hashes = {k: hashlib.sha256(canonical_json(v)).hexdigest() for k, v in parts.items()}
        if hashes != artifact.manifest['compiled']:
            raise FeatureArtifactCorrupt('compiled part digest mismatch')
        _masks(parts)
        n = parts.get('nodes')
        if type(n) is not int or not 1 <= n <= 128 * 128 * 3:
            raise FeatureArtifactCorrupt('invalid node count')
        for state in parts.get('states', {}).values():
            blocked = state['blocked']
            if not isinstance(blocked, list) or any(type(x) is not int or not 0 <= x < n for x in blocked) \
                    or blocked != sorted(set(blocked)):
                raise FeatureArtifactCorrupt('invalid blocked nodes')
        if not isinstance(artifact.code_commit, str) or not re.fullmatch(r'[0-9a-f]{40}', artifact.code_commit):
            raise FeatureArtifactCorrupt('invalid audit commit')
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        if isinstance(exc, FeatureArtifactCorrupt):
            raise
        raise FeatureArtifactCorrupt(str(exc)) from exc


def encode_artifact(artifact: FeatureArtifact) -> bytes:
    check_artifact(artifact)
    wire = canonical_json({'wire': FEATURE_WIRE_VERSION, 'digest': artifact.digest, 'key': asdict(artifact.key),
                           'manifest': artifact.manifest, 'inputs': base64.b64encode(artifact.inputs).decode('ascii'),
                           'assets': base64.b64encode(artifact.assets).decode('ascii'), 'code_commit': artifact.code_commit})
    if len(wire) > MAX_WIRE_BYTES:
        raise FeatureArtifactCorrupt('artifact wire limit')
    return wire


def decode_artifact(blob: bytes) -> FeatureArtifact:
    if len(blob) > MAX_WIRE_BYTES:
        raise FeatureArtifactCorrupt('artifact wire limit')
    try:
        wire = read_json(blob)
        if wire['wire'] != FEATURE_WIRE_VERSION or set(wire) != {'wire', 'digest', 'key', 'manifest', 'inputs', 'assets', 'code_commit'}:
            raise FeatureArtifactCorrupt('unsupported artifact wire version/fields')
        artifact = FeatureArtifact(wire['digest'], _key(wire['key']), wire['manifest'], _b64(wire['inputs']),
                                   _b64(wire['assets']), wire['code_commit'])
        check_artifact(artifact)
        return artifact
    except (ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, FeatureArtifactCorrupt):
            raise
        raise FeatureArtifactCorrupt(str(exc)) from exc


class VerifiedArtifactCache:
    """Process-local LRU. First use recompiles; every hit rehashes complete immutable bytes."""
    def __init__(self, *, max_entries=MAX_CACHE_ENTRIES, max_bytes=MAX_CACHE_BYTES):
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._items = OrderedDict()
        self.bytes_used = 0

    @property
    def entries(self):
        return len(self._items)

    def discard(self, digest):
        found = self._items.pop(digest, None)
        if found is not None:
            self.bytes_used -= found[1]

    def get_or_verify(self, artifact, verify):
        try:
            check_artifact(artifact)
            found = self._items.get(artifact.digest)
            if found is not None:
                check_artifact(found[0])
                self._items.move_to_end(artifact.digest)
                return artifact
            verify(artifact)
            weight = len(artifact.inputs) + len(artifact.assets) + len(expanded_assets(artifact.assets)) \
                     + len(canonical_json(artifact.manifest))
            if self.max_entries > 0 and weight <= self.max_bytes:
                while self._items and (len(self._items) >= self.max_entries or self.bytes_used + weight > self.max_bytes):
                    self.discard(next(iter(self._items)))
                self._items[artifact.digest] = (artifact, weight)
                self.bytes_used += weight
            return artifact
        except FeatureArtifactError:
            self.discard(artifact.digest)
            raise
