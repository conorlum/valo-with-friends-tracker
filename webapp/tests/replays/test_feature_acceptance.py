"""Synthetic cross-process release evidence; recorded timings are observations, never limits."""
import base64
import copy
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest
from map_feature_artifact_toys import base_case, snapshot_case, geometry_case, source_case
from app.replays import map_feature_artifacts as fa, map_feature_inputs as fi, control_format as cf, format as fmt
from app.control.features import compile_artifact, verify_artifact

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))


@pytest.mark.parametrize('case', ['small', 'larger', 'all_pending'])
def test_artifact_cross_process_measurements(case, record_property):
    from app.control import heights as hc
    import tempfile
    entry = source_case()
    if case == 'larger':
        template = entry['map_features']['features'][0]
        features, bundles = [], []
        for ordinal in range(1, 7):
            feature = copy.deepcopy(template)
            feature.update(id=f'feature-{ordinal}', bundle=f'bundle-{ordinal}')
            feature['states'] = [dict(copy.deepcopy(template['states'][0]), name=f'state-{i}') for i in range(4)]
            feature['initial_state'] = 'state-0'
            features.append(feature)
            bundles.append({'id': f'bundle-{ordinal}', 'enabled': True, 'runtime_consumer': 'test', 'members': [feature['id']]})
        entry['map_features'].update(features=features, bundles=bundles)
    geo = geometry_case(multi=case == 'all_pending')
    source = snapshot_case(entry)
    inp = fi.identify_features('Summit', geo.height_sha, source, base_case(), consumers={'test': 1})
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'height.npz'
        hc.save_asset(path, geo.heights)
        height = path.read_bytes()
    request = {'mode': 'compile', 'inputs': {'key': asdict(inp.key), 'canonical_inputs': base64.b64encode(inp.canonical_inputs).decode()},
               'height': base64.b64encode(height).decode()}
    probe = '''import json,sys,time,zlib
from app.control.feature_job import run
from app.control.task import peak_memory
request=json.load(sys.stdin); start=time.perf_counter(); artifact=run(request)
print(json.dumps({'artifact':artifact,'seconds':time.perf_counter()-start,'peak':peak_memory(),'python':sys.version,'zlib':zlib.ZLIB_RUNTIME_VERSION}))'''
    start = time.perf_counter()
    child = subprocess.run([sys.executable, '-c', probe], input=json.dumps(request), text=True,
                           capture_output=True, check=True, timeout=120)
    cold_elapsed = time.perf_counter() - start
    reply = json.loads(child.stdout)
    artifact = fa.decode_artifact(fi.canonical_json(reply['artifact']))
    direct = compile_artifact(geo, inp, artifact.code_commit)
    assert artifact.digest == direct.digest and fa.expanded_assets(artifact.assets) == fa.expanded_assets(direct.assets)
    cache = fa.VerifiedArtifactCache()
    cache.get_or_verify(artifact, lambda a: verify_artifact(a, geo))
    start = time.perf_counter()
    for _ in range(3):
        assert cache.get_or_verify(artifact, lambda a: pytest.fail('verified hit recompiles')) == artifact
    measurements = {'case': case, 'cold_process_seconds': cold_elapsed, 'cold_compile_height_seconds': reply['seconds'],
                    'verified_hit_seconds': (time.perf_counter() - start) / 3, 'peak_child_bytes': reply['peak'],
                    'wire_bytes': len(fa.encode_artifact(artifact)), 'expanded_bytes': len(fa.expanded_assets(artifact.assets)),
                    'compressed_bytes': len(artifact.assets), 'inputs_bytes': len(artifact.inputs), 'height_bytes': len(height),
                    'python': reply['python'], 'zlib': reply['zlib']}
    for key, value in measurements.items():
        record_property(key, value)
    print('FEATURE_MEASUREMENT ' + json.dumps(measurements, sort_keys=True))
    if case == 'all_pending':
        assert artifact.manifest['active_bundles'] == [] and artifact.digest


def test_actual_control_runner_fresh_children_and_persistent_pool(tmp_path, monkeypatch, record_property):
    """Test-only registry/geometry setup wraps the real control_job -> compute_task lifecycle."""
    from replay_worker import server
    from app.control import task, geometry as cg, heights as hc
    from control_toys import blob
    geo = geometry_case()
    cg.visibility(geo, tmp_path / 'visibility')
    inp = fi.identify_features('Summit', geo.height_sha, snapshot_case(), base_case(), consumers={'test': 1})
    artifact = compile_artifact(geo, inp, '0' * 40)
    cache = tmp_path / 'cache'
    height_path = tmp_path / 'height.npz'
    hc.save_asset(height_path, geo.heights)
    settings = server.Settings(control_cache_dir=cache)
    server.store_height(settings, 'Summit', geo.height_sha, height_path.read_bytes())
    fa.store_cached_artifact(cache / 'features', artifact, lambda a: verify_artifact(a, geo))
    round_blob = blob({0: ('A', [(0, 324, 324, 0)]), 1: ('B', [(0, 348, 324, 180)])}, t_end=1)
    round_blob['map'] = 'Summit'
    geometry = task.geometry_used(geo) | {'features': artifact.digest}
    link = {'sides': {'0': 'attack', '1': 'defense'}, 'db_deaths': []}
    envelope = cf.input_envelope('synthetic.c11.f1.a1', '0' * 64, link, geometry)
    request = {'key': 'first', 'map': 'Summit', 'blob': base64.b64encode(fmt.encode_blob(round_blob)).decode(),
               'link': link, 'height': geo.height_sha, 'height_mode': 'asset', 'features': artifact.digest,
               'geometry': geometry, 'inputs': envelope}
    wrapper = tmp_path / 'control_probe.py'
    wrapper.write_text(f'''import sys,json,os
from pathlib import Path
sys.path[:0]=[{str(HERE)!r},{str(REPO)!r},{str(HERE.parents[1])!r}]
os.environ['CONTROL_CACHE_DIR']={str(cache)!r}
from app.replays import map_feature_inputs as fi
fi.CONSUMER_VERSIONS={{'test':1}}
from app.control import task,geometry as cg,heights as hc
from map_feature_artifact_toys import geometry_case
geo=geometry_case(flat=True)
cg.attach_heights(geo, hc.load_asset(Path({str(cache / 'heights' / ('Summit.' + geo.height_sha + '.height.npz'))!r})))
cg.visibility(geo, Path({str(tmp_path / 'visibility')!r}))
task._load=lambda *a,**k: geo
from replay_worker.control_job import run
print(json.dumps(run(json.load(sys.stdin))))
''', encoding='utf-8')
    runner = server.ControlRunner(server.Settings(control_cmd=[sys.executable, str(wrapper)], control_cache_dir=cache))
    results, elapsed = [], []
    for index in range(2):
        start = time.perf_counter()
        job = runner.submit(request | {'key': str(index)})
        deadline = time.monotonic() + 60
        while runner.get(job.id).status not in ('done', 'failed') and time.monotonic() < deadline:
            time.sleep(.05)
        job = runner.get(job.id)
        assert job.status == 'done' and job.result['status'] == 'ok', job.public()
        results.append(job.result)
        elapsed.append(time.perf_counter() - start)
    assert results[0]['data'] == results[1]['data'] and results[0]['summary'] == results[1]['summary']
    monkeypatch.setattr(fi, 'CONSUMER_VERSIONS', {'test': 1})
    monkeypatch.setattr(cg, 'cache_dir', lambda: cache)
    monkeypatch.setattr(task, '_load', lambda *a, **kw: geo)
    monkeypatch.setattr(task, '_ARTIFACTS', fa.VerifiedArtifactCache())
    local = {**request, 'blob': fmt.encode_blob(round_blob)}
    task.compute_task(local)
    start = time.perf_counter()
    result = task.compute_task(local)
    assert result['status'] == 'ok'
    persistent = time.perf_counter() - start
    plain = task.compute_task({k: v for k, v in local.items() if k not in ('features', 'geometry', 'inputs')})
    assert plain['status'] == 'ok' and plain['data'] == result['data']
    summary = cf.unpack_summary(result['summary'])
    del summary['provenance']
    assert summary == cf.unpack_summary(plain['summary'])
    measurements = {'worker_first_seconds': elapsed[0], 'worker_warm_disk_fresh_child_seconds': elapsed[1],
                    'persistent_pool_hit_seconds': persistent, 'worker_peak_bytes': max(r['peak'] or 0 for r in results)}
    for key, value in measurements.items():
        record_property(key, value)
    print('FEATURE_MEASUREMENT ' + json.dumps(measurements, sort_keys=True))
