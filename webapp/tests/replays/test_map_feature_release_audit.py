import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import audit_map_feature_release as audit


@pytest.mark.parametrize('windows', [[], [[1, 2, 3]], {'source_sha256': 'wrong', 'windows': [[1, 2, 3]]},
    {'source_sha256': 'recording', 'windows': [[1, 2, 3], [2, 3, 4]]},
    {'source_sha256': 'recording', 'windows': [[1, 3, 2]]}])
def test_audit_refuses_unidentified_or_invalid_round_clocks(monkeypatch, windows):
    monkeypatch.setattr(audit.contract, 'load_manifest', lambda _: {'source_sha256': 'recording'})
    monkeypatch.setattr(audit.events, 'read_ledger', lambda *args: ([], {}))
    monkeypatch.setattr(audit, 'diagnose', lambda *args, **kwargs: pytest.fail('geometry must not run'))
    raw = json.dumps({'maps': {'Lotus': {'map_features': {}}}}).encode()
    with pytest.raises(ValueError):
        audit.audit(raw, 'Lotus', Path('.'), windows)


def test_report_separates_bound_sources_pending_geometry_and_rejected_height():
    report = {'map': 'Lotus', 'source_bindings': [{'id': 'f1', 'name': '<door>', 'readiness': [],
        'rounds': [{'round': 1, 'status': 'bound'}, {'round': 2, 'status': 'pending'}]}],
        'geometry': {'features': [{'id': 'f1', 'placement': 'pending', 'reasons': []}]},
        'site_height_selection': {'status': 'rejected', 'height_gate_reasons': ['unresolved support']}}
    page = audit.render(report)
    assert '1/2 rounds bound; pending rounds 2' in page
    assert 'Height asset: rejected' in page and 'unresolved support' in page
    assert '&lt;door&gt;' in page and '<door>' not in page


def test_worker_packages_every_pinned_source_patch_in_build_and_runtime():
    # Both apply_parser_pin and runtime load_pin read these files. A missing COPY
    # breaks deployment even when local parser and engine tests succeed.
    webapp = Path(__file__).resolve().parents[2]
    dockerfile = (webapp.parent / 'replay_worker/Dockerfile').read_text()
    pin = json.loads((webapp / 'replay_parser.json').read_text())
    for patch in pin['source_patches']:
        for root in ('/pin', '/srv/webapp'):
            assert f"COPY webapp/{patch['file']} {root}/{patch['file']}" in dockerfile
