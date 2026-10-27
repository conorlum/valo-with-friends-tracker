"""T11: the checker must find a forbidden import through every supported form."""
import pytest
from tests.support.architecture import imports


@pytest.mark.parametrize('source', ['import app.scoring.impact', 'from app.scoring import impact',
    'from ...scoring import impact', 'import importlib\nimportlib.import_module("app.scoring.impact")',
    '__import__("app.scoring.impact")'])
def test_forbidden_import_is_found_in_a_nested_module(tmp_path, source):
    nested = tmp_path/'nested'; nested.mkdir()
    path = nested/'bad.py'; path.write_text(source)
    assert 'app.scoring.impact' in imports(path, 'app.replays.nested.bad')


def test_allowed_runtime_and_lookalike_string_are_not_mislabeled(tmp_path):
    path = tmp_path/'allowed.py'
    path.write_text('from app.scoring import impact_runtime\nmessage="import app.scoring.impact"')
    names = imports(path, 'app.services.replay_impact')
    assert 'app.scoring.impact_runtime' in names and 'app.scoring.impact' not in names
