"""Map control is local tooling (docs/replay-map-control-plan.md, "Where it runs"): the web app never
imports it, the upload worker's stdlib-only code never pulls in its dependencies, and it never
touches Impact scoring (control is display-only)."""

import ast
import subprocess
import sys
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[2]
REPLAYS = WEBAPP / "app" / "replays"
CONTROL = WEBAPP / "app" / "control"
WORKER = WEBAPP.parent / "replay_worker"
HEAVY = ("numpy", "scipy", "PIL")


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_web_app_does_not_import_the_engine():
    code = ("import sys, app.main; "
            "print(sorted(m for m in sys.modules if m == 'scipy' or m.startswith(('scipy.', 'app.control'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=WEBAPP, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "[]"


def test_the_worker_copied_code_stays_stdlib_only():
    # replay_worker/Dockerfile copies app/replays into an image with no numpy, scipy or Pillow.
    files = [*REPLAYS.glob("*.py"), *WORKER.glob("*.py")]
    assert files
    for path in files:
        heavy = {name for name in _imports(path) if name.split(".")[0] in HEAVY}
        assert not heavy, f"{path.name} imports {sorted(heavy)}"


def test_the_worker_server_never_loads_the_engine():
    # Map control runs in child processes (replay_worker/control_job.py) with their own interpreter;
    # the server itself stays importable where there is no numpy (docs/map-control-worker-plan.md).
    code = ("import sys, replay_worker.server; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('numpy', 'scipy', 'PIL') "
            "or m.startswith(('app.control', 'replay_worker.control_job'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=WEBAPP.parent, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "[]"


def test_the_web_apps_control_views_import_nothing_heavy():
    # numpy is already loaded by the web app (fight-EV), so the runtime check above can't see it.
    for path in (WEBAPP / "app" / "services" / "replay_control.py",
                 WEBAPP / "app" / "services" / "replay_control_views.py",
                 WEBAPP / "app" / "routers" / "replays.py",
                 WEBAPP / "app" / "services" / "replay_gaps.py",
                 WEBAPP / "app" / "services" / "replay_gaps_store.py"):
        bad = {name for name in _imports(path) if name.split(".")[0] in HEAVY or name.startswith("app.control")}
        assert not bad, f"{path.name} imports {sorted(bad)}"


def test_control_does_not_import_scoring():
    for path in CONTROL.glob("*.py"):
        scoring = {name for name in _imports(path) if name.startswith("app.scoring")}
        assert not scoring, f"{path.name} imports {sorted(scoring)}"
