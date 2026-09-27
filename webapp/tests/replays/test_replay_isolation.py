"""Replay code never feeds Impact: nothing under app/replays, nor the replay scripts, imports
the scoring models (docs/replay-viewer-plan.md, decision 5)."""

import ast
from pathlib import Path

REPLAYS = Path(__file__).resolve().parents[2] / "app" / "replays"
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
WORKER = Path(__file__).resolve().parents[3] / "replay_worker"
FORBIDDEN = ("app.scoring.impact", "app.scoring.kill_order_leverage", "app.scoring.win_probability")


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_replay_code_does_not_import_scoring():
    files = [*REPLAYS.glob("*.py"), *(SCRIPTS / name for name in ("ingest_replay.py", "make_replay_fixture.py",
                                                                   "replay_gate.py")),
             *WORKER.glob("*.py")]
    for path in files:
        if not path.exists():
            continue
        hits = [name for name in _imports(path) if name.startswith(FORBIDDEN)]
        assert hits == [], f"{path.name} imports {hits}"


SPLIT_SCORING_ALLOWED = {
    "app.scoring.impact", "app.scoring.impact.PERSISTED_FIELDS", "app.scoring.impact.FormulaWeights",
    "app.scoring.impact.build_impact_rows_for_match", "app.scoring.impact_runtime",
    "app.scoring.impact_runtime.active_manifest", "app.scoring.impact_runtime.active_scoring_config",
    # The crawl's ingest preflight: checks, then claims the release write gate's identity for the
    # user-run scripts' `players.riot_subject` backfill. It scores nothing.
    "app.scoring.ingest_preflight", "app.scoring.ingest_preflight.verify_ingest_preflight",
}
APP = REPLAYS.parent


def test_the_worker_is_covered_and_imports_no_database_code():
    files = list(WORKER.glob("*.py"))
    assert files, "replay_worker/ must exist on this branch"
    for path in files:
        hits = [name for name in _imports(path) if name.startswith(("sqlalchemy", "app.db", "app.models", "app.config"))]
        assert hits == [], f"{path.name} imports {hits}"


def test_the_per_kill_split_only_reads_impact():
    """Decision 5 (amended): app/services/replay_impact.py is the one replay module that imports
    scoring code, only its read-only entry points, and it never persists a score."""
    path = APP / "services" / "replay_impact.py"
    scoring = {name for name in _imports(path) if name.startswith("app.scoring")}
    assert scoring <= SPLIT_SCORING_ALLOWED, scoring - SPLIT_SCORING_ALLOWED
    body = path.read_text(encoding="utf-8")
    assert "compute_impact_for_match" not in body and ".add(" not in body


def test_web_code_imports_no_scoring_and_not_the_split():
    """The web service never runs the scorer (AFK run decision D3): no router, and no replay
    service a router uses, imports scoring code or replay_impact."""
    files = [*(APP / "routers").glob("*.py"), *(APP / "services").glob("replay*.py"), *REPLAYS.glob("*.py")]
    for path in files:
        if path.name == "replay_impact.py":
            continue
        names = _imports(path)
        assert not [n for n in names if n.startswith(FORBIDDEN)], path.name
        assert not [n for n in names if n.startswith("app.services.replay_impact")], path.name


def test_no_scoring_hashed_source_changed():
    """migration 0012 adds players.riot_subject without touching the ORM models the scoring
    manifest hashes (app/scoring/impact_manifest.py HASHED_SOURCES)."""
    import subprocess

    from app.scoring.impact_manifest import HASHED_SOURCES

    root = APP.parents[1]
    diff = subprocess.run(["git", "diff", "--name-only", "origin/main", "--", *(f"webapp/{p}" for p in HASHED_SOURCES)],
                          cwd=root, capture_output=True, text=True)
    if diff.returncode != 0:
        import pytest

        pytest.skip("no origin/main to compare with")
    assert diff.stdout.strip() == ""
