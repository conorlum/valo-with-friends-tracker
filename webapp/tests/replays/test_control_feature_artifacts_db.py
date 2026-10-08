"""Approved disposable SQLite archive tests; never use the configured site engine."""
import copy
import gzip
import hashlib
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from map_feature_artifact_toys import base_case, geometry_case, snapshot_case


@pytest.fixture
def archive_factory(tmp_path):
    from app.db import Base
    from app.models.replay import ControlFeatureArtifact, ControlHeight
    engine = create_engine('sqlite:///' + str(tmp_path / 'archive.sqlite'))
    Base.metadata.create_all(engine, tables=[ControlFeatureArtifact.__table__, ControlHeight.__table__])
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture
def artifact():
    from app.replays.map_feature_inputs import identify_features
    from app.control.features import compile_artifact
    geo = geometry_case(flat=True)
    inp = identify_features('Summit', None, snapshot_case(), base_case(), consumers={'test': 1})
    return compile_artifact(geo, inp, '0' * 40)


def test_same_key_shares_one_immutable_row_and_audit_changes_are_idempotent(archive_factory, artifact):
    from app.models.replay import ControlFeatureArtifact
    from app.services.control_feature_artifacts import store_artifact, load_artifact
    with archive_factory() as db:
        a = store_artifact(db, artifact)
        equivalent = replace(artifact, code_commit='a' * 40,
                             assets=gzip.compress(gzip.decompress(artifact.assets), compresslevel=1, mtime=12))
        assert store_artifact(db, equivalent) == a
        db.commit()
    with archive_factory() as db:
        assert load_artifact(db, a.digest) == a
        assert db.query(ControlFeatureArtifact).count() == 1


def test_same_key_never_overwrites_a_hash_valid_different_compilation(archive_factory, artifact):
    from app.replays.map_feature_artifacts import canonical_json, FeatureArtifactError
    from app.services.control_feature_artifacts import store_artifact, load_artifact
    import json
    parts = json.loads(gzip.decompress(artifact.assets))
    parts['states']['feature-1:closed']['blocked'] = [5161]
    manifest = copy.deepcopy(artifact.manifest)
    manifest['compiled']['states'] = hashlib.sha256(canonical_json(parts['states'])).hexdigest()
    bad = replace(artifact, manifest=manifest, assets=gzip.compress(canonical_json(parts), mtime=0),
                  digest=hashlib.sha256(canonical_json(manifest)).hexdigest())
    with archive_factory() as db:
        store_artifact(db, artifact)
        with pytest.raises(FeatureArtifactError, match='conflict'):
            store_artifact(db, bad)
        assert load_artifact(db, artifact.digest) == artifact
        db.rollback()


def test_archive_does_not_commit_callers_transaction(archive_factory, artifact):
    from app.services.control_feature_artifacts import store_artifact, find_artifact
    with archive_factory() as db:
        store_artifact(db, artifact)
        db.rollback()
    with archive_factory() as db:
        assert find_artifact(db, artifact.key) is None


def test_header_lookup_does_not_decompress_assets_but_full_read_rehashes(archive_factory, artifact, monkeypatch):
    from app.models.replay import ControlFeatureArtifact
    from app.replays.map_feature_artifacts import FeatureArtifactCorrupt
    from app.services import control_feature_artifacts as service
    with archive_factory() as db:
        service.store_artifact(db, artifact)
        db.commit()
        row = db.get(ControlFeatureArtifact, artifact.digest)
        row.assets = b'corrupt cache-independent archive'
        db.commit()
        header = service.find_artifact_header(db, artifact.key)
        assert header.digest == artifact.digest and not hasattr(header, 'assets')
        with pytest.raises(FeatureArtifactCorrupt):
            service.load_artifact(db, artifact.digest)


def test_missing_lookup_is_explicit_and_exact_keys_do_not_fall_back(archive_factory, artifact):
    from app.replays.map_feature_artifacts import FeatureArtifactMissing
    from app.services.control_feature_artifacts import store_artifact, load_artifact, find_artifact
    with archive_factory() as db:
        with pytest.raises(FeatureArtifactMissing):
            load_artifact(db, artifact.digest)
        store_artifact(db, artifact)
        assert find_artifact(db, replace(artifact.key, height_digest='a' * 12)) is None
        assert find_artifact(db, replace(artifact.key, compiler_version=99)) is None


def test_separate_connections_converge_on_existing_durable_representation(archive_factory, artifact):
    from app.models.replay import ControlFeatureArtifact
    from app.services.control_feature_artifacts import store_artifact
    with archive_factory() as first, archive_factory() as second:
        store_artifact(first, artifact)
        first.commit()
        assert store_artifact(second, replace(artifact, code_commit='b' * 40)) == artifact
        second.commit()
        assert second.query(ControlFeatureArtifact).count() == 1


def test_nonflat_archive_requires_its_exact_retained_height(archive_factory):
    from app.control.features import compile_artifact
    from app.replays.map_feature_inputs import identify_features
    from app.replays.map_feature_artifacts import FeatureArtifactMissing
    from app.services.control_feature_artifacts import store_artifact
    geo = geometry_case()
    inp = identify_features('Summit', geo.height_sha, snapshot_case(), base_case(), consumers={'test': 1})
    artifact = compile_artifact(geo, inp, '0' * 40)
    with archive_factory() as db:
        with pytest.raises(FeatureArtifactMissing, match='height'):
            store_artifact(db, artifact)


def test_concurrent_first_inserts_use_a_savepoint_and_converge(archive_factory, artifact, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from app.services import control_feature_artifacts as service
    from app.models.replay import ControlFeatureArtifact
    first_read = threading.local()
    barrier = threading.Barrier(2)
    real_find = service.find_artifact
    def synchronized_find(db, key):
        result = real_find(db, key)
        if not getattr(first_read, 'seen', False):
            first_read.seen = True
            barrier.wait(timeout=30)
        return result
    monkeypatch.setattr(service, 'find_artifact', synchronized_find)
    def insert(commit):
        with archive_factory() as db:
            result = service.store_artifact(db, replace(artifact, code_commit=commit))
            db.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(insert, 'a' * 40), pool.submit(insert, 'b' * 40)]
        a, b = [job.result(timeout=60) for job in jobs]
    assert a == b
    with archive_factory() as db:
        assert db.query(ControlFeatureArtifact).count() == 1
def test_committed_height_is_retained_without_creating_activation_history(archive_factory, tmp_path):
    from app.control import heights as hc
    from app.control.features import compile_artifact
    from map_feature_artifact_toys import geometry_case, snapshot_case, base_case
    from app.replays.map_feature_inputs import identify_features
    from app.services import control_feature_artifacts as service
    from app.models.replay import ControlHeight
    geo = geometry_case()
    path = tmp_path / 'committed.npz'
    hc.save_asset(path, geo.heights)
    raw = path.read_bytes()
    inp = identify_features('Summit', geo.height_sha, snapshot_case(), base_case(), consumers={'test': 1})
    artifact = compile_artifact(geo, inp, '0' * 40)
    with archive_factory() as db:
        service.store_artifact(db, artifact, height_bytes=raw)
        db.commit()
        assert db.query(ControlHeight).count() == 0
        assert service.archived_height_bytes(db, artifact.digest) == raw
        from app.models.replay import ControlFeatureArtifact
        row = db.get(ControlFeatureArtifact, artifact.digest)
        row.height_asset = b'corrupt'
        db.flush()
        from app.replays.map_feature_artifacts import FeatureArtifactCorrupt
        with pytest.raises(FeatureArtifactCorrupt):
            service.archived_height_bytes(db, artifact.digest)
