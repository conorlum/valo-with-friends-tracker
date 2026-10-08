"""Parent-safe exact-key immutable archive operations; the caller owns its transaction."""
from sqlalchemy.exc import IntegrityError

from app.models.replay import ControlFeatureArtifact, ControlHeight
from app.replays.map_feature_inputs import FeatureKey
from app.replays.map_feature_artifacts import (
    FeatureArtifact, FeatureArtifactRef, FeatureArtifactError, FeatureArtifactMissing,
    check_artifact, check_header, expanded_assets,
)


def _query(db, key):
    return db.query(ControlFeatureArtifact).filter_by(map_name=key.map_name, height_digest=key.height_digest,
                                                    tags_digest=key.tags_digest, compiler_version=key.compiler_version)


def _artifact(row):
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


def store_artifact(db, artifact: FeatureArtifact) -> FeatureArtifact:
    check_artifact(artifact)
    existing = find_artifact(db, artifact.key)
    if existing is not None:
        return _equivalent(existing, artifact)
    if artifact.key.height_digest != 'flat':
        height = db.query(ControlHeight.asset).filter_by(map_name=artifact.key.map_name,
                                                       digest=artifact.key.height_digest).first()
        if height is None or not height.asset:
            raise FeatureArtifactMissing('referenced historical height is missing')
    key = artifact.key
    row = ControlFeatureArtifact(digest=artifact.digest, map_name=key.map_name, height_digest=key.height_digest,
                                 tags_digest=key.tags_digest, compiler_version=key.compiler_version,
                                 manifest=artifact.manifest, inputs=artifact.inputs, assets=artifact.assets,
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
