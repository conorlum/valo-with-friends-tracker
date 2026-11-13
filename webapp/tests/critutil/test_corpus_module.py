"""The portable corpus build and the complete, standard-library build digest."""

import hashlib
import json
import pickle
from pathlib import Path
import subprocess
import sys

import pytest

from app.services import replay_util
from tests.critutil.test_build import FRIEND, MODEL, fake_gate, toy_corpus


WEBAPP_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("passing, expected", [
    (False, "db7a7e70be884e33de37d8a5a4038f6d0e64965ea39ec96c0f215cb44f9f16cd"),
    (True, "f8ecf96a2cd7e1e3d1baf089bc69610892b9efe38c48213c45aa23383163070d"),
])
def test_build_matches_before_extraction(monkeypatch, passing, expected):
    """Pin the unchanged builder's complete output with both passing and failing gates (R4)."""
    from app.critutil import corpus

    monkeypatch.setattr(corpus.util_values, "gate_all_modes", fake_gate(passing))
    stored, ctx = toy_corpus()
    out = corpus.build_from_rounds(stored, ctx, MODEL, {FRIEND})
    assert hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest() == expected


def test_corpus_build_without_sqlalchemy():
    stored, ctx = toy_corpus()
    expected = json.dumps(replay_util.build_from_rounds(stored, ctx, MODEL, {FRIEND}), sort_keys=True)
    script = """
import json
import pickle
import sys
for name in ('sqlalchemy', 'app.models', 'app.db'):
    sys.modules[name] = None
from app.critutil.corpus import build_from_rounds
print(json.dumps(build_from_rounds(*pickle.loads(sys.stdin.buffer.read())), sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=WEBAPP_ROOT,
        input=pickle.dumps((stored, ctx, MODEL, {FRIEND})), capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().strip() == expected


def test_service_reexports_moved_names():
    from app.critutil import corpus

    names = (
        "build_from_rounds", "corpus_from_inputs", "_ability_styles", "ability_label", "kind_label", "label_of",
        "area_of", "buy_type", "_round_data", "_cast_rows", "_mark_combos", "_gate_list", "_item_of", "lineup_score",
        "LABELS", "FULL_BUY_THRESHOLD", "PISTOL_ROUNDS", "NO_LIFT", "PAGE_VERSION", "EXAMPLES_KEPT", "COMBO_S",
        "COMBO_UV", "SHRINK_K", "MIN_CASTS",
    )
    for name in names:
        assert getattr(replay_util, name) is getattr(corpus, name), name


def test_missing_replay_js_raises(monkeypatch, tmp_path):
    from app.critutil import corpus

    monkeypatch.setattr(corpus, "__file__", str(tmp_path / "app" / "critutil" / "corpus.py"))
    corpus._ability_styles.cache_clear()
    try:
        with pytest.raises(FileNotFoundError, match="replay.js"):
            corpus._ability_styles()
    finally:
        corpus._ability_styles.cache_clear()


def test_corpus_from_inputs_keeps_fingerprints(monkeypatch):
    from app.critutil import corpus

    stored, ctx, friends, model = {}, {}, {1}, {"cells": {}}
    calls = []
    table = {}
    monkeypatch.setattr(corpus, "build_from_rounds",
                        lambda *args, **kw: calls.append((args, kw)) or {"rows": []})
    out = corpus.corpus_from_inputs((stored, ctx, friends), [(7, 2, "fp-a"), (7, 3, "fp-b")], model,
                                    painted_table=table)
    assert calls == [((stored, ctx, model, friends), {"painted_table": table})]
    assert calls[0][1]["painted_table"] is table
    assert out == {"rows": [], "fingerprints": {"7": {"2": "fp-a", "3": "fp-b"}}}


@pytest.fixture
def digest_tree(tmp_path):
    from app.critutil import build_digest

    for relative in build_digest.SOURCES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"first line\nsecond line\n")
    return tmp_path


def test_digest_covers_all_build_sources():
    from app.critutil import build_digest

    expected = {p.relative_to(WEBAPP_ROOT).as_posix() for p in (WEBAPP_ROOT / "app" / "critutil").rglob("*.py")}
    expected.update({
        "app/critutil/abilities.json", "app/services/util_values.py", "app/services/util_model.py",
        "app/static/js/replay.js", "app/static/data/callouts.json", "app/coach/__init__.py", "app/coach/areas.py",
        "app/replays/__init__.py", "app/replays/control_format.py", "app/replays/format.py",
        "app/replays/player_state.py",
    })
    assert set(build_digest.SOURCES) == expected


# Package markers with no build logic: shipped in the worker image, never part of what the build computes.
_UNDIGESTED = {"app/__init__.py", "app/services/__init__.py"}


def test_digest_covers_every_webapp_module_the_build_imports():
    """The hand list above can't notice a new import; this walks the build's real import closure (a fresh
    process: corpus, an area lookup and a refit, with no site modules preloaded)."""
    import subprocess
    import sys

    from app.critutil import build_digest

    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "root = Path.cwd().resolve()\n"
        "from app.critutil import corpus\n"
        "corpus.area_of('Ascent', 0.5, 0.5, None, painted_table={})\n"
        "print(json.dumps(sorted(Path(m.__file__).resolve().relative_to(root).as_posix()\n"
        "      for m in list(sys.modules.values())\n"
        "      if getattr(m, '__file__', None) and Path(m.__file__).resolve().is_relative_to(root))))\n"
    )
    out = subprocess.run([sys.executable, "-c", script], cwd=WEBAPP_ROOT, capture_output=True, text=True,
                         check=True, env={**__import__("os").environ, "PYTHONPATH": str(WEBAPP_ROOT)})
    imported = set(__import__("json").loads(out.stdout))
    assert "app/critutil/corpus.py" in imported
    assert imported - _UNDIGESTED <= set(build_digest.SOURCES), imported - _UNDIGESTED - set(build_digest.SOURCES)


def test_digest_uses_sorted_posix_paths_and_bytes(digest_tree):
    from app.critutil import build_digest

    expected = hashlib.sha256()
    for relative in sorted(build_digest.SOURCES):
        expected.update(relative.encode("utf-8"))
        expected.update((digest_tree / relative).read_bytes())
    assert build_digest.digest(digest_tree) == expected.hexdigest()[:16]


def test_digest_normalizes_crlf(digest_tree):
    from app.critutil import build_digest

    before = build_digest.digest(digest_tree)
    for relative in build_digest.SOURCES:
        path = digest_tree / relative
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert build_digest.digest(digest_tree) == before


def test_digest_changes_with_replay_js(digest_tree):
    from app.critutil import build_digest

    before = build_digest.digest(digest_tree)
    (digest_tree / "app/static/js/replay.js").write_bytes(b"First line\nsecond line\n")
    assert build_digest.digest(digest_tree) != before


def test_digest_ignores_unrelated_files(digest_tree):
    from app.critutil import build_digest

    before = build_digest.digest(digest_tree)
    (digest_tree / "unrelated.txt").write_text("not a build input", encoding="utf-8")
    assert build_digest.digest(digest_tree) == before


@pytest.mark.parametrize("relative", ["app/static/js/replay.js", "app/critutil/constants.py"])
def test_digest_missing_source_raises(digest_tree, relative):
    from app.critutil import build_digest

    (digest_tree / relative).unlink()
    with pytest.raises(FileNotFoundError):
        build_digest.digest(digest_tree)


def test_digest_import_needs_only_standard_library():
    script = """
import sys
for name in ('sqlalchemy', 'app.models', 'app.db', 'numpy', 'scipy', 'app.critutil.corpus'):
    sys.modules[name] = None
from app.critutil.build_digest import digest
print(digest('.'))
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=WEBAPP_ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.strip()) == 16
