"""The active manifest stays verifiable when Render hides repo-level docs."""
import pytest

from app.scoring import impact_runtime
from app.scoring.impact_manifest import ManifestMismatchError, lf_sha256


@pytest.fixture(autouse=True)
def clear_runtime_cache():
    impact_runtime.clear_cache()
    yield
    impact_runtime.clear_cache()


@pytest.fixture
def manifest_roots(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    mirror = tmp_path / "mirror"
    repo.mkdir()
    mirror.mkdir()
    monkeypatch.setattr(impact_runtime, "REPO_ROOT", repo)
    monkeypatch.setattr(impact_runtime, "MIRROR_ROOT", mirror, raising=False)
    return repo, mirror


def write_manifest(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


def test_docs_take_precedence_over_mirror(manifest_roots):
    repo, mirror = manifest_roots
    active = "docs/superpowers/x/m.json"
    docs = write_manifest(repo / active)
    write_manifest(mirror / "x/m.json")

    assert impact_runtime.manifest_path(active) == docs


def test_missing_docs_use_mirror(manifest_roots):
    _, mirror = manifest_roots
    mirrored = write_manifest(mirror / "x/m.json")

    assert impact_runtime.manifest_path("docs/superpowers/x/m.json") == mirrored


def test_both_missing_report_docs_path(manifest_roots, monkeypatch):
    repo, _ = manifest_roots
    active = "docs/superpowers/x/m.json"
    monkeypatch.setattr(impact_runtime, "ACTIVE_MANIFEST", active)

    assert impact_runtime.manifest_path(active) == repo / active
    with pytest.raises(ManifestMismatchError) as exc:
        impact_runtime._verified()
    assert "manifest file is missing" in str(exc.value)
    assert str(repo / active) in str(exc.value)


def test_other_relative_paths_do_not_use_mirror(manifest_roots):
    repo, mirror = manifest_roots
    active = "other/m.json"
    write_manifest(mirror / active)

    assert impact_runtime.manifest_path(active) == repo / active


@pytest.mark.parametrize("exists", [False, True])
def test_absolute_path_is_unchanged(manifest_roots, tmp_path, exists):
    absolute = tmp_path / "absolute.json"
    if exists:
        write_manifest(absolute)

    assert impact_runtime.manifest_path(str(absolute)) == absolute


def test_active_manifest_mirror_matches_docs():
    active = impact_runtime.ACTIVE_MANIFEST
    if active is None:
        pytest.skip("Legacy scoring has no active manifest")
    mirror = impact_runtime.MIRROR_ROOT / active.removeprefix("docs/superpowers/")

    assert mirror.is_file()
    assert lf_sha256(mirror) == lf_sha256(impact_runtime.REPO_ROOT / active)


def test_real_mirror_verifies_without_docs(tmp_path, monkeypatch):
    monkeypatch.setattr(impact_runtime, "REPO_ROOT", tmp_path)

    assert impact_runtime.active_scoring_config().config_id == "impact_v4"
