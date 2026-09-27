"""Replay code never feeds Impact: nothing under app/replays, nor the replay scripts, imports
the scoring models (docs/replay-viewer-plan.md, decision 5)."""

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
                                                                   "replay_gate.py"))]
    for path in files:
        if not path.exists():
            continue
        hits = [name for name in _imports(path) if name.startswith(FORBIDDEN)]
        assert hits == [], f"{path.name} imports {hits}"
