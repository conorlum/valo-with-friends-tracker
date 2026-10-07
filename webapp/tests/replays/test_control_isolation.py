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
    # Nor the gap detector: the gaps.json route reads stored rows only (plan amendment 7).
    code = ("import sys, app.main; "
            "print(sorted(m for m in sys.modules if m == 'scipy' "
            "or m.startswith(('scipy.', 'app.control', 'app.gaps'))))")
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
    # Answering /health (which names the control child's gaps protocol) loads none of it either.
    code = ("import json, sys, tempfile, threading, urllib.request; from pathlib import Path; "
            "import replay_worker.server as s; "
            "settings = s.Settings(temp_root=Path(tempfile.mkdtemp())); "
            "httpd = s.make_server(s.Worker(settings), control=s.ControlRunner(settings)); "
            "threading.Thread(target=httpd.serve_forever, daemon=True).start(); "
            "body = json.loads(urllib.request.urlopen("
            "f'http://127.0.0.1:{httpd.server_address[1]}/health', timeout=30).read()); "
            "assert body['control']['gaps_protocol'] == 1, body; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('numpy', 'scipy', 'PIL') "
            "or m.startswith(('app.control', 'app.gaps', 'replay_worker.control_job'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=WEBAPP.parent, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "[]"


def test_the_web_apps_control_views_import_nothing_heavy():
    # numpy is already loaded by the web app (fight-EV), so the runtime check above can't see it.
    for path in (WEBAPP / "app" / "services" / "replay_control.py",
                 WEBAPP / "app" / "services" / "replay_control_views.py",
                 WEBAPP / "app" / "routers" / "replays.py",
                 WEBAPP / "app" / "services" / "replay_gaps.py",
                 WEBAPP / "app" / "services" / "replay_gaps_store.py",
                 WEBAPP / "app" / "services" / "replay_gaps_view.py",
                 WEBAPP / "app" / "services" / "gap_patterns.py",
                 WEBAPP / "app" / "routers" / "gap_patterns.py"):
        bad = {name for name in _imports(path)
               if name.split(".")[0] in HEAVY or name.startswith(("app.control", "app.gaps"))}
        assert not bad, f"{path.name} imports {sorted(bad)}"


def test_the_gap_view_rows_load_no_engine_detector_or_numpy():
    # The standalone page and the gaps.json endpoint use replay_gaps_view; it must not pull these in indirectly.
    code = ("import sys, app.services.replay_gaps_view; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('numpy', 'scipy', 'PIL') "
            "or m.startswith(('app.control', 'app.gaps'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=WEBAPP, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "[]"


def test_the_gap_pattern_service_loads_no_engine_detector_or_numpy():
    # The pattern page (plan 3, review amendment 7): standard library and the DB only, indirectly too.
    code = ("import sys, app.services.gap_patterns; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('numpy', 'scipy', 'PIL') "
            "or m.startswith(('app.control', 'app.gaps'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=WEBAPP, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "[]"


def test_control_does_not_import_scoring():
    for path in CONTROL.glob("*.py"):
        scoring = {name for name in _imports(path) if name.startswith("app.scoring")}
        assert not scoring, f"{path.name} imports {sorted(scoring)}"
