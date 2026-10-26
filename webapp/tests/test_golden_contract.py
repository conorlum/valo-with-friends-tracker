"""T02: a missing or damaged reference must never approve its own output."""
import json
import pytest
from tests import test_impact_golden_both_regimes as golden


def test_missing_baseline_fails_and_creates_nothing(tmp_path, monkeypatch):
    folder = tmp_path / "absent"
    monkeypatch.setattr(golden, "GOLDEN_DIR", folder)
    with pytest.raises(AssertionError, match="Missing reviewed golden"):
        golden._check_against_golden("missing.json", {"impact": 1})
    assert not folder.exists()


@pytest.mark.parametrize("contents,error", [('not json', json.JSONDecodeError), ('{"impact": 2}', AssertionError)])
def test_bad_or_changed_baseline_fails_without_rewriting(tmp_path, monkeypatch, contents, error):
    monkeypatch.setattr(golden, "GOLDEN_DIR", tmp_path)
    path = tmp_path / "reviewed.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(error): golden._check_against_golden(path.name, {"impact": 1})
    assert path.read_text(encoding="utf-8") == contents


def test_unreadable_baseline_is_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(golden, "GOLDEN_DIR", tmp_path)
    path = tmp_path / "baseline.json"
    path.write_text('{}', encoding="utf-8")
    def denied(*a, **kw): raise PermissionError("fixture unreadable")
    monkeypatch.setattr(type(path), "read_text", denied)
    with pytest.raises(PermissionError): golden._check_against_golden(path.name, {})


def test_explicit_regeneration_always_fails_for_review(tmp_path, monkeypatch):
    monkeypatch.setattr(golden, "GOLDEN_DIR", tmp_path)
    monkeypatch.setattr(golden, "REGENERATE", True)
    with pytest.raises(pytest.fail.Exception, match="regenerated"):
        golden._check_against_golden("new.json", {"impact": 1})
    assert json.loads((tmp_path/"new.json").read_text()) == {"impact": 1}
