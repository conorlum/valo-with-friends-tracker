"""Parent-safe exact-key immutable archive operations; the caller owns its transaction."""
import base64
import json
import subprocess
import sys
import tempfile
import hashlib
from dataclasses import asdict
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from app.models.replay import ControlFeatureArtifact, ControlHeight
from app.replays.map_feature_inputs import FeatureKey, canonical_json
from app.replays.map_feature_artifacts import (
    FeatureArtifact, FeatureArtifactRef, FeatureArtifactError, FeatureArtifactMissing,
    check_artifact, check_header, expanded_assets,
    decode_artifact, UnsupportedFeatureCompiler, FeatureArtifactCorrupt, MAX_WIRE_BYTES,
)


def _query(db, key):
    return db.query(ControlFeatureArtifact).filter_by(map_name=key.map_name, height_digest=key.height_digest,
                                                    tags_digest=key.tags_digest, compiler_version=key.compiler_version)


def _artifact(row):
    if row.height_asset is not None and hashlib.sha256(row.height_asset).hexdigest() != row.height_asset_sha256:
        raise FeatureArtifactCorrupt('retained height checksum mismatch')
    result = FeatureArtifact(row.digest, FeatureKey(row.map_name, row.height_digest, row.tags_digest, row.compiler_version),
                             row.manifest, bytes(row.inputs), bytes(row.assets), row.code_commit)
    check_artifact(result)
    return result


def find_artifact(db, key: FeatureKey) -> FeatureArtifact | None:
    row = _query(db, key).one_or_none()
    return None if row is None else _artifact(row)


def find_artifact_header(db, key: FeatureKey) -> FeatureArtifactRef | None:
    row = _query(db, key).with_entities(ControlFeatureArtifact.digest, ControlFeatureArtifact.manifest).one_or_none()
    if row is None:
        return None
    ref = FeatureArtifactRef(row.digest, key, row.manifest)
    check_header(ref)
    return ref


def load_artifact(db, digest: str) -> FeatureArtifact:
    row = db.get(ControlFeatureArtifact, digest)
    if row is None:
        raise FeatureArtifactMissing(f'feature archive {digest} is missing')
    return _artifact(row)


def _equivalent(existing, incoming):
    if existing.digest != incoming.digest or existing.inputs != incoming.inputs \
            or expanded_assets(existing.assets) != expanded_assets(incoming.assets):
        raise FeatureArtifactError('immutable feature key conflict')
    return existing


def archived_height_bytes(db, digest):
    row = db.get(ControlFeatureArtifact, digest)
    if row is None:
        raise FeatureArtifactMissing('feature archive missing')
    _artifact(row)
    if row.height_digest == 'flat':
        return None
    if row.height_asset is not None:
        return bytes(row.height_asset)
    height = db.query(ControlHeight.asset).filter_by(map_name=row.map_name, digest=row.height_digest).first()
    if height is None:
        raise FeatureArtifactMissing('historical height missing')
    return bytes(height.asset)


def store_artifact(db, artifact: FeatureArtifact, *, height_bytes=None) -> FeatureArtifact:
    if artifact.key.height_digest == 'flat' and height_bytes is not None:
        raise FeatureArtifactCorrupt('flat artifact cannot retain a height asset')
    check_artifact(artifact)
    existing = find_artifact(db, artifact.key)
    if existing is not None:
        return _equivalent(existing, artifact)
    if artifact.key.height_digest != 'flat':
        height = db.query(ControlHeight.asset).filter_by(map_name=artifact.key.map_name,
                                                       digest=artifact.key.height_digest).first()
        if (height is None or not height.asset) and height_bytes is None:
            raise FeatureArtifactMissing('referenced historical height is missing')
        if height_bytes is not None:
            from app.replays.map_feature_artifacts import encode_artifact
            request = {'mode': 'verify', 'artifact': json.loads(encode_artifact(artifact)),
                       'height': base64.b64encode(height_bytes).decode('ascii')}
            run_feature_child('verify', canonical_json(request))
    key = artifact.key
    row = ControlFeatureArtifact(digest=artifact.digest, map_name=key.map_name, height_digest=key.height_digest,
                                 tags_digest=key.tags_digest, compiler_version=key.compiler_version,
                                 manifest=artifact.manifest, inputs=artifact.inputs, assets=artifact.assets,
                                 height_asset=height_bytes,
                                 height_asset_sha256=None if height_bytes is None else hashlib.sha256(height_bytes).hexdigest(),
                                 code_commit=artifact.code_commit)
    connection = db.connection()
    if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
        # sqlite3 legacy transaction mode does not BEGIN on SELECT/SAVEPOINT. Releasing the
        # first savepoint would otherwise commit the insertion before the caller can roll back.
        connection.exec_driver_sql('BEGIN')
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = find_artifact(db, key)
        if existing is None:
            raise FeatureArtifactError('archive digest conflicts with another key')
        return _equivalent(existing, artifact)
    return artifact


class FeaturePreparationPending(Exception):
    def __init__(self, key, reason):
        self.key, self.reason = key, reason
        super().__init__(reason)


def run_feature_child(mode: str, payload: bytes) -> bytes:
    if len(payload) > MAX_WIRE_BYTES:
        raise FeatureArtifactCorrupt('feature request size limit')
    try:
        request = json.loads(payload)
        if request.get('mode') != mode:
            raise FeatureArtifactCorrupt('feature child mode mismatch')
        with tempfile.TemporaryFile() as output:
            done = subprocess.run([sys.executable, '-m', 'app.control.feature_job'], input=payload,
                                  stdout=output, stderr=subprocess.DEVNULL, timeout=120,
                                  cwd=Path(__file__).parents[2], check=False)
            if done.returncode or output.tell() > MAX_WIRE_BYTES:
                raise FeatureArtifactCorrupt('feature child exit/output limit')
            output.seek(0)
            reply = json.load(output)
        if not reply.get('ok'):
            error = UnsupportedFeatureCompiler if reply.get('code') == 'features_unsupported' else FeatureArtifactCorrupt
            raise error(reply.get('reason', 'feature child failed'))
        return canonical_json(reply['result'])
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, FeatureArtifactError):
            raise
        raise FeatureArtifactCorrupt(f'feature child failed: {type(exc).__name__}: {exc}') from exc


def prepare_artifact(db, inputs, height_bytes):
    try:
        existing = find_artifact(db, inputs.key)
        if existing is not None:
            return existing
        request = {'mode': 'compile', 'inputs': {'key': asdict(inputs.key),
                   'canonical_inputs': base64.b64encode(inputs.canonical_inputs).decode('ascii')},
                   'height': None if height_bytes is None else base64.b64encode(height_bytes).decode('ascii')}
        artifact = decode_artifact(run_feature_child('compile', canonical_json(request)))
        if artifact.key != inputs.key or artifact.inputs != inputs.canonical_inputs:
            raise FeatureArtifactCorrupt('child compiled different inputs')
        return store_artifact(db, artifact, height_bytes=height_bytes)
    except Exception as exc:
        raise FeaturePreparationPending(inputs.key, str(exc)) from exc
