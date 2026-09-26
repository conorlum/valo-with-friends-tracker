"""Replay code never feeds Impact: nothing under app/replays imports the scoring models.

One script reads Impact: scripts/build_replay_bundle.py (the local preview) calls the live
scorer read-only for per-kill values. It may import only the read-only entry points, never the
one that persists (compute_impact_for_match), and the preview's route imports no scoring or DB."""

import ast
from pathlib import Path

REPLAYS = Path(__file__).resolve().parents[2] / "app" / "replays"
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
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
                                                                   "replay_gate.py", "render_replay_standalone.py"))]
    for path in files:
        if not path.exists():
            continue
        hits = [name for name in _imports(path) if name.startswith(FORBIDDEN)]
        assert hits == [], f"{path.name} imports {hits}"


BUNDLE_SCORING_ALLOWED = {
    "app.scoring.impact", "app.scoring.impact.PERSISTED_FIELDS", "app.scoring.impact.FormulaWeights",
    "app.scoring.impact.build_impact_rows_for_match", "app.scoring.impact_runtime",
    "app.scoring.impact_runtime.active_manifest", "app.scoring.impact_runtime.active_scoring_config",
}


def test_the_bundle_script_only_reads_impact():
    names = _imports(SCRIPTS / "build_replay_bundle.py")
    scoring = {name for name in names if name.startswith("app.scoring")}
    assert scoring <= BUNDLE_SCORING_ALLOWED, scoring - BUNDLE_SCORING_ALLOWED
    text = (SCRIPTS / "build_replay_bundle.py").read_text(encoding="utf-8")
    assert "compute_impact_for_match" not in text and ".commit(" not in text and ".add(" not in text


def test_the_preview_route_reads_no_scoring_and_no_db():
    names = _imports(REPLAYS.parents[0] / "routers" / "replay_preview.py")
    assert not {n for n in names if n.startswith(("app.scoring", "app.db", "app.models", "sqlalchemy"))}
