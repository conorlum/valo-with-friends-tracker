"""Layout assets across worker admission, consumers and non-spending dispatch."""

import io
import json
from types import SimpleNamespace

import pytest

from test_control_remote import FakeWorker
from test_control_store import db, factory, linked
from test_replay_store import condensed, pg
from test_layout_selection import layout_assets
from test_replay_worker_control_workflow import FakeLink
from app.control import geometry as cg
from app.replays import layouts
from app.services import replay_control as rc
from app.services import replay_control_remote as remote
from replay_tasks import tasks
from replay_worker import control_cache, server


@pytest.mark.parametrize('key', ['Ascent', 'Ascent_13_10', 'Lotus_13_10', 'Map2',
    'Ascent@13.10', 'Ascent.13', '../x', '1Map', 'Ascent_13_10_more', 'Ascent\n'])
def test_worker_asset_key_pattern(key):
    assert bool(server.MAP_NAME.fullmatch(key)) == bool(layouts.ASSET_KEY.fullmatch(key))


@pytest.mark.parametrize('name', ['Ascent', 'Lotus'])
@pytest.mark.parametrize('loader', [cg.load_geometry, cg.load_base_geometry])
def test_geometry_uses_base_scale_and_layout_masks(tmp_path, monkeypatch, name, loader):
    import numpy as np
    key = name + '_13_10'
    mask = np.zeros((cg.PX, cg.PX), dtype=bool)
    cg.write_mask_png(tmp_path / f'{key}.walk.png', mask)
    cg.write_mask_png(tmp_path / f'{key}.sight.png', mask)
    (tmp_path / 'tags.json').write_text('{"maps": {}}')
    (tmp_path / 'maps.json').write_text(json.dumps({name: {'xMultiplier': 7e-5}}))
    monkeypatch.setattr(cg, 'MAPS_JSON', tmp_path / 'maps.json')
    geo = loader(key, tmp_path)
    assert geo.name == key
    assert geo.m_per_px == pytest.approx(1 / (7e-5 * cg.PX * 100))


def test_layout_cache_names():
    for folder, digest in [('', 'a' * 16), ('feature-rows/', 'b' * 64)]:
        assert control_cache.valid(f'{folder}Ascent_13_10.{digest}.npz', 'Ascent_13_10')
        assert not control_cache.valid(f'{folder}Ascent_13_10.{digest}.npz', 'Ascent')


def test_ascent_capture_and_runtime_keep_map_behavior(tmp_path):
    from test_ascent_features import capture, runtime_case
    from app.replays import ascent_features as af
    from app.control.feature_runtime import FeatureRuntime
    from app.control.features import bundle_status
    manifest = capture(tmp_path, [(0, 'reset'), (1000, 2)])
    base = af.read_capture(tmp_path, 'Ascent', manifest)
    assert af.read_capture(tmp_path, 'Ascent_13_10', manifest) == base
    replay = SimpleNamespace(map_name='Ascent_13_10', report={}, rounds={1: {}})
    af.attach(replay, tmp_path, manifest, [(500, 2000, 3000)])
    assert replay.rounds[1]['map_features']['status'] == 'decoded'
    geo, mf, _, blob = runtime_case()
    baseline = bundle_status(geo, mf)
    geo.name = 'Ascent_13_10'
    assert bundle_status(geo, mf) == baseline
    assert FeatureRuntime(geo, blob).active


def test_lotus_capture_panels_and_runtime_keep_map_behavior(tmp_path):
    from test_map_feature_events import capture
    from test_map_pool_runtime import lotus, runtime_case
    from app.replays import map_feature_events as events
    from app.control.feature_geometry import panel_parents
    from app.control.features import bundle_status
    from app.control.map_pool_runtime import MapPoolRuntime
    manifest = capture(tmp_path, [(0, 1, 'Switch_HiddenTemple1'), (1200, 0, 'Switch_HiddenTemple1')])
    assert events.read_ledger(tmp_path, 'Lotus_13_10', manifest) == events.read_ledger(tmp_path, 'Lotus', manifest)
    replay = SimpleNamespace(map_name='Lotus_13_10', report={}, rounds={1: {}})
    events.attach(replay, tmp_path, manifest, [(1000, 1500, 2000)])
    assert replay.rounds[1]['map_messages']['map'] == 'Lotus_13_10'
    geo, blob = runtime_case(lotus(), 'Lotus')
    from app.replays.map_feature_inputs import read_json
    mf = read_json(geo.features.inputs)['runtime']
    parents, baseline = panel_parents(geo, mf), bundle_status(geo, mf)
    assert parents
    geo.name = 'Lotus_13_10'
    assert panel_parents(geo, mf) == parents
    assert bundle_status(geo, mf) == baseline
    assert MapPoolRuntime(geo, blob).active


def test_sunset_property_capture_keeps_map_behavior(tmp_path, monkeypatch):
    from test_map_feature_events import test_raw_capture_retains_explicit_state_witness_and_corruption_disables_binding
    from app.replays import map_feature_events as events
    original = events.read_ledger
    monkeypatch.setattr(events, 'read_ledger', lambda path, name, manifest:
                        original(path, name + '_13_10', manifest))
    test_raw_capture_retains_explicit_state_witness_and_corruption_disables_binding(tmp_path)


def test_abyss_fallback_uses_map_family(monkeypatch):
    from app.control import rope_ground
    from app.replays.map_feature_artifacts import FeatureArtifactCorrupt
    monkeypatch.setattr(rope_ground, 'domains_for', lambda mf: [('r', {})] * 5)
    geo = SimpleNamespace(name='Abyss_13_10', heights=None, movement_only=False)
    with pytest.raises(FeatureArtifactCorrupt, match='at most four bounded Abyss rope domains'):
        rope_ground.prepare(geo, {})


@pytest.mark.parametrize('name', ['Ascent', 'Lotus', 'Abyss', 'Split', 'Summit'])
def test_map_checklists_and_quiet_route_readiness(name):
    from app.replays.map_feature_schema import checklist_seed
    from app.control.route_runtime import readiness
    assert checklist_seed(name + '_13_10') == checklist_seed(name)
    if name in ('Abyss', 'Split', 'Summit'):
        geo = SimpleNamespace(name=name, heights=object())
        baseline = readiness(geo, {}, [])
        geo.name += '_13_10'
        assert readiness(geo, {}, []) == baseline


def handler(tmp_path, monkeypatch, *, task=None, accepts=False):
    webapp = tmp_path / 'webapp'
    index = webapp / 'app/static/data/control/index.json'
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({'maps': {'Ascent': {}, 'Ascent_13_10': {}}}))
    monkeypatch.setattr(server, 'WEBAPP', webapp)
    worker = SimpleNamespace(settings=server.Settings(), queue=SimpleNamespace(qsize=lambda: 0),
        memory_cap=0, archive_status=lambda: {}, recipe=None, idle=lambda: True)
    control = SimpleNamespace(enabled=True, settings=server.Settings(), counts=lambda: {},
        remote=lambda job: False,
        submit=(lambda task: SimpleNamespace(id='synthetic', status='queued')) if accepts else
               (lambda task: pytest.fail('unknown layout reached queue')))
    cls = server.make_handler(worker, control)
    h = cls.__new__(cls)
    h._send = lambda code, body: (code, body)
    h.path = '/health'
    if task is not None:
        raw = json.dumps(task).encode()
        h.headers = {'Content-Length': str(len(raw))}
        h.rfile = io.BytesIO(raw)
    return h


def test_health_names_own_layouts(tmp_path, monkeypatch):
    h = handler(tmp_path, monkeypatch)
    assert h.do_GET()[1]['control']['layouts'] == ['Ascent', 'Ascent_13_10']


def test_http_unknown_layout_before_missing_height(tmp_path, monkeypatch):
    task = {'key': 'synthetic', 'map': 'Ascent_99_99', 'blob': '', 'link': {}, 'height': 'a' * 12}
    code, body = handler(tmp_path, monkeypatch, task=task)._control()
    assert code == 409
    assert body['code'] == 'unknown_layout'


@pytest.mark.parametrize('key,expected', [('Ascent', 202), ('Ascent_13_10', 202),
    ('Ascent@13.10', 400), ('Ascent.13', 400), ('../x', 400)])
def test_http_layout_admission(tmp_path, monkeypatch, key, expected):
    # Exercise the real handler in memory: no socket or local server.
    h = handler(tmp_path, monkeypatch, accepts=True,
                task={'key': 'synthetic', 'map': key, 'blob': '', 'link': {}})
    assert h._control()[0] == expected


def test_workflow_known_layout_preserves_task(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, 'WEBAPP', tmp_path)
    index = tmp_path / 'app/static/data/control/index.json'
    index.parent.mkdir(parents=True)
    index.write_text('{"maps": {"Ascent_13_10": {}}}')
    task = {'key': 'synthetic', 'map': 'Ascent_13_10', 'blob': '', 'link': {}}
    raw = json.dumps(task).encode()
    received = []
    def child(*args):
        received.append(args[2])
        return {'status': 'ok'}
    monkeypatch.setattr(tasks, '_run_child', child)
    link = FakeLink({'task': raw})
    tasks.control({'map': task['map'], 'files': []}, link)
    assert received == [raw]
    assert link.result == {'status': 'ok'}


@pytest.mark.parametrize('visibility', [None, 'Ascent_13_10.' + 'a' * 16 + '.npz'])
def test_workflow_unknown_layout_never_runs_child(tmp_path, monkeypatch, visibility):
    monkeypatch.setattr(tasks, 'WEBAPP', tmp_path)
    index = tmp_path / 'app/static/data/control/index.json'
    index.parent.mkdir(parents=True)
    index.write_text('{"maps": {"Ascent": {}}}')
    monkeypatch.setattr(tasks, '_run_child', lambda *a, **kw: pytest.fail('unknown layout ran child'))
    task = {'key': 'synthetic', 'map': 'Ascent_13_10', 'blob': '', 'link': {}}
    link = FakeLink({'task': json.dumps(task).encode()})
    tasks.control({'map': task['map'], 'files': [visibility] if visibility else [], 'visibility': visibility}, link)
    assert link.result['status'] == 'failed'
    assert link.result['error_code'] == 'unknown_layout'
    assert link.result['error_kind'] == 'compat'


def test_client_maps_unknown_layout(monkeypatch):
    client = remote.ControlClient('unused')
    def conflict(*args):
        raise remote.Conflict({'code': 'unknown_layout', 'map': 'Ascent_13_10'})
    monkeypatch.setattr(client, '_call', conflict)
    with pytest.raises(remote.UnknownLayout):
        client.submit({'map': 'Ascent_13_10'})


@pytest.mark.parametrize('health', [{}, {'control': {}}, {'control': {'layouts': None}}])
def test_old_health_supports_only_base(health):
    assert remote.supports_layout(health, 'Ascent')
    assert not remote.supports_layout(health, 'Ascent_13_10')
    assert remote.supports_layout({'control': {'layouts': ['Ascent_13_10']}}, 'Ascent_13_10')


def test_dispatch_holds_then_sends(factory, db, linked, layout_assets, monkeypatch):
    linked.map_name, linked.game_branch = 'Toy', 'release-13.10'
    db.commit()
    [planned] = rc.plan(db, rounds={1})
    monkeypatch.setattr(rc, 'plan', lambda *a, **kw: [planned])
    worker, state = FakeWorker(), remote.State()
    counts = remote.cycle(factory, worker, state, now=0)
    assert counts['held'] == 1 and counts['sent'] == 0
    assert not state.tries and not worker.tasks
    old_health = worker.health
    worker.health = lambda: {**old_health(), 'control': {'layouts': ['Toy_13_10']}}
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts['sent'] == 1 and counts['held'] == 0
    assert next(iter(worker.tasks.values()))['map'] == 'Toy_13_10'


@pytest.mark.parametrize('workflow', [False, True])
def test_unknown_layout_spends_no_try(factory, db, linked, monkeypatch, workflow):
    [planned] = rc.plan(db, rounds={1})
    monkeypatch.setattr(rc, 'plan', lambda *a, **kw: [planned])
    worker, state = FakeWorker(), remote.State()
    if workflow:
        worker.answer = lambda task: {'status': 'failed', 'error_kind': 'compat',
            'error_code': 'unknown_layout', 'error': 'layout absent from run image'}
    else:
        def refuse(task):
            raise remote.UnknownLayout(task['map'])
        worker.submit = refuse
    events = []
    monkeypatch.setattr(remote.site_health, 'count', lambda *a, **kw: events.append(a))
    counts = remote.cycle(factory, worker, state, now=0)
    if workflow:
        counts = remote.cycle(factory, worker, state, now=1)
    assert counts['held'] == 1
    assert counts['refused'] == 0
    assert not state.tries
    assert not any(e[0] == 'worker_refused' for e in events)
