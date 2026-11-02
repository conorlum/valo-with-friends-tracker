"""An explicitly configured test manifest for this interpreter, without altering any frozen release."""
import sys
from app.scoring import impact, impact_runtime
from app.scoring.impact_manifest import build_manifest, write_manifest
from app.scoring.ingest_preflight import _installed_packages


def test_manifest(monkeypatch, tmp_path):
    # Retain the selected comparator; test-only dependencies (xdist, browser
    # tooling) intentionally differ from the frozen deployment environment.
    comparator = impact_runtime.active_manifest()['release_comparator']
    manifest = build_manifest(candidate_id='coverage-test', created='2026-10-26', scorer_revision='test-only',
        activation_impact_calculation_version=impact.IMPACT_CALCULATION_VERSION,
        source_snapshots={}, release_comparator=comparator)
    manifest['environment'] = {'python': sys.version.split()[0], 'packages': _installed_packages()}
    path = tmp_path/'test-manifest.json'
    write_manifest(path, manifest)
    monkeypatch.setattr(impact_runtime, 'ACTIVE_MANIFEST', str(path))
    impact_runtime.clear_cache()
    return manifest

test_manifest.__test__ = False
