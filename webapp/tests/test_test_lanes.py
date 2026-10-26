"""T01: prove selection and required-prerequisite checks can fail."""
from pathlib import Path
import pytest
from tests.support.lanes import calls_postgres


def test_classifier_follows_fixture_helpers_and_handles_cycles():
    def ordinary(): return 1
    def guarded():
        from tests._postgres import postgres_url_or_skip
        return postgres_url_or_skip()
    assert calls_postgres(guarded)
    assert not calls_postgres(ordinary)


@pytest.fixture
def child(pytester, monkeypatch):
    root = Path(__file__).parent
    monkeypatch.setenv('PYTHONPATH', str(root.parent))
    monkeypatch.delenv('VALO_TEST_DATABASE_URL', raising=False)
    pytester.makeconftest((root/'conftest.py').read_text(encoding='utf-8'))
    pytester.makeini('[pytest]\nmarkers =\n slow: expensive\n postgres: real db\n corpus: measurement\n browser: real browser\n')
    return pytester


def test_required_postgres_missing_is_a_configuration_failure(child):
    child.makepyfile('def test_ok(): assert True')
    result = child.runpytest_subprocess('-o', 'addopts=', '--require-postgres')
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(['*VALO_TEST_DATABASE_URL is required*'])


def test_missing_required_node_fails(child):
    child.makeconftest((Path(__file__).parent/'conftest.py').read_text(encoding='utf-8') +
                      '\nshutil.which = lambda name: None\n')
    child.makepyfile('def test_ok(): assert True')
    result = child.runpytest_subprocess('-o', 'addopts=', '--require-node')
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(['*node is required*'])


def test_shared_postgres_url_refuses_parallel_workers_before_connecting(child, monkeypatch):
    monkeypatch.setenv('VALO_TEST_DATABASE_URL','postgresql://u@example.invalid/scratch_test')
    child.makepyfile('def test_ok(): assert True')
    result = child.runpytest_subprocess('-o','addopts=','-n','2')
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(['*requires -n 0*'])


def test_missing_sanitized_fixture_fails_the_required_lane(child):
    child.makepyfile('def test_ok(): assert True')
    result = child.runpytest_subprocess('-o', 'addopts=', '--require-fixtures')
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(['*Required sanitized replay fixture missing*'])


def test_marker_selection_includes_deliberately_failing_slow_test(child):
    child.makepyfile('import pytest\ndef test_fast(): assert True\n@pytest.mark.slow\ndef test_slow(): assert False')
    child.runpytest_subprocess('-o','addopts=', '-m','not slow').assert_outcomes(passed=1, deselected=1)
    child.runpytest_subprocess('-o','addopts=', '-m','slow').assert_outcomes(failed=1, deselected=1)


def test_required_browser_skip_becomes_a_failure(child):
    child.makepyfile('import pytest\n@pytest.mark.browser\ndef test_browser(): pytest.skip("browser absent")')
    child.runpytest_subprocess('-o','addopts=', '--require-browser').assert_outcomes(failed=1)


def test_postgres_marker_follows_test_and_fixture_calls(child):
    child.makepyfile('''import pytest
@pytest.fixture
def database():
    from tests._postgres import postgres_session_or_skip
    return postgres_session_or_skip()
def test_database(database): pass
def test_offline(): assert True
''')
    child.runpytest_subprocess('-o','addopts=', '-m','postgres').assert_outcomes(skipped=1, deselected=1)


def test_all_worker_build_inputs_trigger_the_existing_image_review():
    import fnmatch
    import yaml
    root = Path(__file__).parents[2]
    workflow = yaml.safe_load((root/'.github/workflows/replay-worker-review.yml').read_text())
    trigger = workflow.get('on',workflow.get(True))
    paths = trigger['pull_request']['paths']
    for changed in ('replay_worker/server.py','replay_tasks/tasks.py','webapp/app/moments/fights.py',
                    'webapp/app/control/engine.py','webapp/app/gaps/detect.py','webapp/app/replays/condense.py',
                    'webapp/app/static/data/maps.json','webapp/requirements.txt','webapp/tests/conftest.py',
                    'webapp/tests/support/lanes.py','webapp/tests/fixtures/replay/swiftplay/manifest.json'):
        assert any(fnmatch.fnmatchcase(changed, pattern) for pattern in paths), changed
