"""Ground sound, unknown footprints and private output identities are separate facts."""
import copy, json
from pathlib import Path
import numpy as np
import pytest

from app.control import geometry as cg, heights as hc, adaptive as ag, terrain_audit
from tests.replays.test_adaptive_ground import mixed, CHILDREN


def ledge(kind):
    geo = mixed((0, 12, None, 12))
    low, high = ag.BASE+CHILDREN[0], ag.BASE+CHILDREN[1]
    geo.heights.fine_edges = np.array([(low, 0, high, 0, hc.EDGE_STEP), (high, 0, low, 0, kind)], np.int32)
    return cg.attach_heights(geo, geo.heights)


@pytest.mark.parametrize('kind, field', [(hc.EDGE_FALL, 'blocked_large_falls'),
    (hc.EDGE_SLIDE, 'quiet_large_ground_descents'), (hc.EDGE_STEP, 'quiet_large_step_descents_to_review')])
def test_audit_distinguishes_grounded_returns_noisy_falls_and_legacy_step_candidates(kind, field):
    geo = ledge(kind)
    before = geo.heights.digest
    got = terrain_audit.audit(geo, [{'id':'1','bbox_px':[200,200,208,208]}], context_px=0)
    region = got['regions'][0]
    assert region[field] == 1
    assert region['unresolved_nodes'] == 1
    assert region['unresolved_area_px2'] == 16
    assert region['unresolved_route_edges'] > 0  # Unknown fallback is reported, not declared fixed.
    assert len(region['large_descent_edges']) == 1
    if kind == hc.EDGE_FALL:
        assert region['large_descent_edges'][0]['known_quiet_alternative_exists'] is False
    assert geo.heights.digest == before and not got['runtime_changes_applied']


def test_noisy_direct_drop_can_have_a_known_quiet_route_via_landings():
    geo = mixed((0, 12, 6, 6))
    low, high = ag.BASE+CHILDREN[0], ag.BASE+CHILDREN[1]
    geo.heights.fine_edges = np.concatenate((geo.heights.fine_edges,
        np.array([(high, 0, low, 0, hc.EDGE_FALL)], np.int32)))
    geo = cg.attach_heights(geo, geo.heights)
    result = terrain_audit.audit(geo, [{'id':'1','bbox_px':[200,200,208,208]}], context_px=0)
    edge = result['regions'][0]['large_descent_edges'][0]
    assert not edge['quiet'] and edge['known_quiet_alternative_exists']


@pytest.mark.parametrize('regions', [
    [None],
    [{'id':'1','bbox_px':[200,200,200,208]}],
    [{'id':'1','bbox_px':[200,200,float('nan'),208]}],
    [{'id':'1','bbox_px':[False,200,208,208]}],
    [{'id':'1','bbox_px':[-1,200,208,208]}],
    [{'id':'1','bbox_px':[200,200,208,208]}]*2,
])
def test_invalid_or_duplicate_scopes_are_rejected(regions):
    with pytest.raises(ValueError):
        terrain_audit.audit(ledge(hc.EDGE_FALL), regions)


def test_cli_pins_asset_and_regions_and_never_writes_into_the_repository(tmp_path, monkeypatch):
    from scripts import audit_terrain_routes as cli
    geo = ledge(hc.EDGE_FALL)
    source = tmp_path/'source'; source.mkdir()
    height = source/'Split.height.npz'; hc.save_asset(height,geo.heights)
    regions = source/'regions.json'
    regions.write_text(json.dumps({'map':'Split','regions':[{'id':'1','bbox_px':[200,200,208,208]}]}))
    monkeypatch.setattr(cli.cg, 'load_base_geometry', lambda name: copy.copy(geo))
    target = tmp_path/'report'
    with pytest.raises(ValueError, match='digest'):
        cli.run(height,'0'*12,regions,target)
    assert not target.exists()
    result = cli.run(height,geo.heights.digest,regions,target)
    assert result['regions_sha256'] and result['asset_sha256']
    assert json.loads((target/'report.json').read_text()) == result
    with pytest.raises(ValueError, match='already exists'):
        cli.run(height,geo.heights.digest,regions,target)
    with pytest.raises(ValueError, match='Git repository'):
        cli.run(height,geo.heights.digest,regions,Path(cli.__file__).parents[2]/'private-terrain-evidence')
