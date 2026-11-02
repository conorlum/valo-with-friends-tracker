"""Which tests to run for a change, how long they take, and how the run went: the suites in tests/suites.toml.

    python scripts/test_catalog.py list
    python scripts/test_catalog.py plan [PATH ...] [--git [BASE]] [--suite NAME ...] [--slow]
    python scripts/test_catalog.py run  [PATH ...] [--git [BASE]] [--suite NAME ...] [--slow] [--browser] [--record]
    python scripts/test_catalog.py check

`list` prints every suite: what it checks, its files and tests, and its serial time from tests/timings.json.
`plan` picks the suites a change needs (the paths given, or with `--git` everything changed against BASE, default
origin/main, plus uncommitted work), collects their tests with pytest to sort them into lanes by marker, and
prints each lane's estimated time, a window, and its command. `run` runs those lanes serially and reports each
suite against its estimate: passed, failed, skipped, actual time, and any test far slower than its recorded time.
`--record` then refreshes tests/timings.json from the run. `check` fails if a test file is in no suite or in two,
a glob matches nothing, or a file under app/ or ../replay_worker/ is guarded by no suite (tests/test_test_catalog.py
runs it).

Lanes: default (no marker), slow, postgres (needs VALO_TEST_DATABASE_URL; slow ones only with --slow), browser
(run only with --browser), corpus (never; optional measurements). Times are serial (`-n 0`) on the owner's dev
machine; the window allows for a loaded machine (up to ~2x) and pytest's start-up. Run from webapp/.
"""

import argparse
import ast
import contextlib
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path, PurePosixPath

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_timings  # noqa: E402

CATALOG = os.path.join("tests", "suites.toml")
LANES = ("default", "slow", "postgres", "browser", "corpus")
EXPR = {
    "default": "not slow and not postgres and not browser and not corpus",
    "slow": "slow and not postgres and not browser and not corpus",
    "postgres": "postgres and not corpus and not browser and not slow",
    "postgres+slow": "postgres and not corpus and not browser",
    "browser": "browser",
}
# Files that are guarded but whose change can reach any test: every suite is picked.
BROAD = ("pytest.ini", "tests/conftest.py", "requirements*.txt", "tests/support/**")
REQUIRED = ("app/**/*.py", "app/**/*.js", "app/**/*.html", "app/**/*.json", "app/**/*.css", "../replay_worker/**/*.py")
NOT_CODE = (".md", ".txt", ".png", ".jpg", ".svg")     # changed, unguarded and no test's business: not reported
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".pytest_cache", "build", "dist"}
SLOWER = 3.0            # a test this many times its recorded time (and at least SLOWER_MIN_S over it) is flagged
SLOWER_MIN_S = 2.0


# ------------------------------------------------------------------------------------------------ the catalog

def load_catalog(path: str = CATALOG) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def match(path: str, pattern: str) -> bool:
    return PurePosixPath(path).full_match(pattern)


def all_files() -> list[str]:
    """Every file the catalog can name, relative to webapp/: webapp's own (not dot folders or venvs), the repo
    root's top-level files and ../replay_worker/."""
    out = []
    for root, prefix in ((".", ""), ("../replay_worker", "../replay_worker/")):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".") and "venv" not in d]
            rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
            for name in filenames:
                out.append(prefix + (name if rel == "." else f"{rel}/{name}"))
    out += ["../" + n for n in os.listdir("..") if os.path.isfile(os.path.join("..", n))]
    return out


def test_files(files: list[str]) -> list[str]:
    return sorted(f for f in files if match(f, "tests/**/test_*.py"))


def suite_of(catalog: dict, tests: list[str]) -> dict[str, list[str]]:
    """{test file: [suites whose `tests` globs match it]}."""
    owners = {t: [] for t in tests}
    for name, s in catalog.items():
        if not isinstance(s, dict):
            continue
        for t in tests:
            if any(match(t, g) for g in s.get("tests", [])):
                owners[t].append(name)
    return owners


def suites(catalog: dict) -> dict:
    return {k: v for k, v in catalog.items() if isinstance(v, dict)}


def normalise(path: str) -> str:
    """A path given relative to webapp/, to the repo root (git's form), or absolute -> relative to webapp/."""
    p = path.replace("\\", "/")
    if os.path.isabs(path):
        p = os.path.relpath(path).replace(os.sep, "/")
    if p.startswith("./"):
        p = p[2:]
    if p.startswith("webapp/"):
        return p[len("webapp/"):]
    if os.path.exists(p) or p.startswith("../"):
        return p
    return "../" + p           # a repo-root path outside webapp/, as git prints it


def select(catalog: dict, owners: dict[str, list[str]], changed: list[str]) -> tuple[dict, list[str], bool]:
    """({suite: [why ...]}, [changed files no suite guards], broad?). A changed test file picks its suite, a
    guarded file the suites that guard it, a helper in a test folder every suite with tests in that folder."""
    picked: dict[str, list[str]] = defaultdict(list)
    unguarded, broad = [], False
    for f in changed:
        if any(match(f, g) for g in BROAD):
            broad = True
        hit = False
        for t_suite in owners.get(f, []):
            picked[t_suite].append(f"{f} (its test)")
            hit = True
        for name, s in suites(catalog).items():
            if any(match(f, g) for g in s.get("guards", [])):
                if f + " (its test)" not in picked[name]:
                    picked[name].append(f)
                hit = True
        if not hit and f.startswith("tests/") and f.endswith(".py"):
            folder = f.rsplit("/", 1)[0]
            for t, names in owners.items():
                if t.rsplit("/", 1)[0] == folder:
                    for n in names:
                        if f not in picked[n]:
                            picked[n].append(f + " (a helper in its folder)")
                        hit = True
        if not hit and not f.endswith(NOT_CODE):
            unguarded.append(f)
    if broad:
        for name in suites(catalog):
            picked.setdefault(name, [])
    return {k: v for k, v in picked.items() if v or broad}, unguarded, broad


def check(catalog: dict, files: list[str]) -> list[str]:
    problems = []
    tests = test_files(files)
    owners = suite_of(catalog, tests)
    for t, names in owners.items():
        if not names:
            problems.append(f"{t}: in no suite")
        elif len(names) > 1:
            problems.append(f"{t}: in {len(names)} suites ({', '.join(names)})")
    for name, s in suites(catalog).items():
        if not str(s.get("about", "")).strip():
            problems.append(f"[{name}]: no `about`")
        if not s.get("tests"):
            problems.append(f"[{name}]: no `tests`")
        for key in ("tests", "guards"):
            for g in s.get(key, []):
                pool = tests if key == "tests" else files
                if not any(match(f, g) for f in pool):
                    problems.append(f"[{name}] {key}: {g!r} matches nothing")
    guarded = [g for s in suites(catalog).values() for g in s.get("guards", [])]
    for f in files:
        if any(match(f, r) for r in REQUIRED) and not any(match(f, g) for g in guarded):
            problems.append(f"{f}: guarded by no suite")
    return problems


# ------------------------------------------------------------------------------------------------ times

def fmt(s: float) -> str:
    if s < 90:
        return f"{s:.0f} s"
    if s < 90 * 60:
        return f"{s / 60:.1f} min"
    return f"{s / 3600:.1f} h"


def test_time(times: dict, nid: str) -> float | None:
    """Recorded serial seconds for a node id: by name, else its file's mean quick test, else None."""
    f, name = nid.split("::", 1)
    e = times.get(f)
    if e is None:
        return None
    if name in e["tests"]:
        return e["tests"][name]
    n, total = e["rest"]
    return total / n if n else 0.0


def window(est: float, n: int) -> tuple[float, float]:
    start = 4.0 + 0.0015 * n         # interpreter, imports and collection
    return 0.8 * est + start, 2.0 * est + 1.5 * start


def lane_of(markers: set[str]) -> str:
    for lane in ("corpus", "browser", "postgres", "slow"):
        if lane in markers:
            return lane
    return "default"


def collect(files: list[str]) -> list[tuple[str, set[str]]]:
    """Every test in `files` with its markers, from an in-process pytest collection (all markers, serial)."""
    import pytest

    class Grab:
        items: list = []

        def pytest_collection_finish(self, session):
            self.items = [(i.nodeid, {m.name for m in i.iter_markers()}) for i in session.items]

    grab = Grab()
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rc = pytest.main([*files, "--collect-only", "-q", "-m", "", "-n", "0", "-p", "no:cacheprovider"],
                         plugins=[grab])
    if rc not in (0, 5):
        sys.stderr.write(out.getvalue()[-3000:])
        raise SystemExit(f"pytest could not collect the selected files (exit {rc})")
    return grab.items


def toy_cache_cold() -> tuple[bool, list[str]]:
    """(the toy-map visibility cache has no build for the current casting code?, the files its key covers) --
    the same key as tests/replays/control_toys.py's `_cache_dir`, read without importing it."""
    src = Path("tests/replays/control_toys.py")
    sources: list[str] = []
    for node in ast.parse(src.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_BUILD_SOURCES" for t in node.targets):
            sources = list(ast.literal_eval(node.value))
    configured = os.environ.get("CONTROL_TOY_CACHE_DIR", "")
    if configured.lower() == "off":
        return True, sources
    root = Path(configured) if configured else Path.home() / ".cache" / "valo-tests" / "control-toys"
    digest = hashlib.sha256()
    for rel in sources:
        digest.update(rel.encode() + b"\0" + Path(rel).read_bytes().replace(b"\r\n", b"\n") + b"\0")
    here = root / digest.hexdigest()[:16]
    return not (here.is_dir() and any(here.iterdir())), sources


# ------------------------------------------------------------------------------------------------ plan

def git_changed(base: str) -> list[str]:
    """Repo-root paths changed on this branch since it left `base`, plus uncommitted and untracked files; deleted
    files are left out."""
    def lines(*args, cwd=None):
        r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)
        if r.returncode:
            raise SystemExit(f"git {' '.join(args)}: {r.stderr.strip()}")
        return [x for x in r.stdout.splitlines() if x.strip()]
    top = lines("rev-parse", "--show-toplevel")[0]
    names = set(lines("diff", "--name-only", f"{base}...HEAD", cwd=top))
    names |= set(lines("diff", "--name-only", "HEAD", cwd=top))
    names |= set(lines("ls-files", "--others", "--exclude-standard", cwd=top))
    return sorted(n for n in names if os.path.exists(os.path.join(top, n)))


def build_plan(a, catalog: dict, files: list[str]) -> dict:
    tests = test_files(files)
    owners = suite_of(catalog, tests)
    changed = [normalise(p) for p in a.paths]
    if a.git is not None:
        changed += [normalise(p) for p in git_changed(a.git)]
    changed = sorted(set(changed))
    if a.suite:
        unknown = [s for s in a.suite if s not in suites(catalog)]
        if unknown:
            raise SystemExit(f"no such suite: {', '.join(unknown)} (see `list`)")
        picked = {s: ["asked for"] for s in a.suite}
        unguarded, broad = [], False
    else:
        if not changed:
            raise SystemExit("nothing to plan: give paths, --git or --suite")
        picked, unguarded, broad = select(catalog, owners, changed)
    chosen = sorted(t for t, names in owners.items() if names and names[0] in picked)
    items = collect(chosen) if chosen else []
    times = test_timings.load()
    by_lane: dict[str, list] = defaultdict(list)
    per_suite: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    unknown_n = 0
    for nid, marks in items:
        lane = lane_of(marks)
        if lane == "postgres" and "slow" in marks:
            lane = "postgres+slow"
        s = test_time(times, nid)
        if s is None:
            unknown_n += 1
            s = 0.1
        by_lane[lane].append((nid, s, marks))
        suite = owners[nid.split("::", 1)[0]][0]
        per_suite[suite][lane][0] += 1
        per_suite[suite][lane][1] += s
    return {"changed": changed, "picked": picked, "unguarded": unguarded, "broad": broad, "owners": owners,
            "by_lane": by_lane, "per_suite": per_suite, "unknown": unknown_n, "items": items, "times": times}


def lanes_to_run(p: dict, a) -> list[tuple[str, str, bool, str]]:
    """[(lane, marker expression, runs?, why not)] in run order."""
    has_db = bool(os.environ.get("VALO_TEST_DATABASE_URL"))
    out = []
    for lane in ("default", "slow", "postgres", "postgres+slow", "browser"):
        if not p["by_lane"].get(lane):
            continue
        why = ""
        if lane in ("slow", "postgres+slow") and not a.slow:
            why = "add --slow"
        if lane.startswith("postgres") and not has_db:
            why = "set VALO_TEST_DATABASE_URL (a disposable *_test database; see docs/testing.md)"
        if lane == "browser" and not getattr(a, "browser", False):
            why = "add --browser (needs Playwright's browser installed)"
        out.append((lane, EXPR[lane], not why, why))
    return out


def lane_files(p: dict, lane: str) -> list[str]:
    return sorted({nid.split("::", 1)[0] for nid, _, _ in p["by_lane"][lane]})


def command(p: dict, lane: str, expr: str) -> list[str]:
    extra = ["--require-browser"] if lane == "browser" else []
    return [sys.executable, "-m", "pytest", "-n", "0", "-m", expr, *extra, *lane_files(p, lane)]


def show_cmd(cmd: list[str]) -> str:
    shown = ["python" if c == sys.executable else (f'"{c}"' if " " in c else c) for c in cmd]
    return " ".join(shown)


def print_plan(p: dict, a, catalog: dict) -> None:
    S = suites(catalog)
    if p["changed"]:
        print(f"Changed ({len(p['changed'])}):")
        for f in p["changed"]:
            names = [n for n, why in p["picked"].items() if any(w.split(" (")[0] == f for w in why)]
            other = "not code, no tests" if f.endswith(NOT_CODE) else "NO SUITE GUARDS THIS"
            print(f"  {f}  ->  {', '.join(names) if names else other}")
    if p["broad"]:
        print("A change here can reach any test: every suite is picked.")
    print(f"\nSuites ({len(p['picked'])}):")
    for name in sorted(p["picked"]):
        lanes = p["per_suite"].get(name, {})
        parts = ", ".join(f"{lane} {n} ({fmt(s)})" for lane, (n, s) in sorted(lanes.items()))
        print(f"  {name}: {S[name]['about']}")
        print(f"      {parts or 'no tests collected'}")
    print("\nLanes (serial, -n 0):")
    any_run = False
    for lane, expr, runs, why in lanes_to_run(p, a):
        rows = p["by_lane"][lane]
        est = sum(s for _, s, _ in rows)
        lo, hi = window(est, len(rows))
        state = "RUNS" if runs else f"skipped: {why}"
        print(f"  {lane}: {len(rows)} tests, about {fmt(est)} (window {fmt(lo)} to {fmt(hi)})  [{state}]")
        print(f"      {show_cmd(command(p, lane, expr))}")
        any_run |= runs
    if p["by_lane"].get("corpus"):
        print(f"  corpus: {len(p['by_lane']['corpus'])} tests, never run here (optional measurements)")
    if not any_run:
        print("  nothing runs with these options")
    notes = []
    if p["unguarded"]:
        notes.append(f"{len(p['unguarded'])} changed file(s) are guarded by no suite (listed above). If they are "
                     "code, add them to a suite's `guards` in tests/suites.toml, or run the default lane: "
                     "python -m pytest -n 0")
    if p["unknown"]:
        notes.append(f"{p['unknown']} test(s) have no recorded time and were counted at 0.1 s each "
                     "(scripts/test_timings.py refresh, or run with --record)")
    geo = sum(1 for _, m in p["items"] if "geometry" in m)
    if geo and toy_cache_cold()[0]:
        notes.append(f"{geo} geometry test(s) selected and the toy-map visibility cache has no build for the current "
                     f"casting code ({', '.join(toy_cache_cold()[1])}): the first run rebuilds the toy maps and can "
                     "take many times the estimate (test_control_floors.py: 587 s cold, 8 s warm)")
    if notes:
        print("\nNotes:")
        for n in notes:
            print(f"  - {n}")


# ------------------------------------------------------------------------------------------------ run

def run(p: dict, a, catalog: dict) -> int:
    print_plan(p, a, catalog)
    xmls, rc_all = [], 0
    for lane, expr, runs, _ in lanes_to_run(p, a):
        if not runs:
            continue
        xml = os.path.join(tempfile.mkdtemp(prefix="test-catalog-"), f"{lane}.xml")
        cmd = command(p, lane, expr) + ["-q", "-rfE", "-p", "no:cacheprovider", f"--junitxml={xml}"]
        print(f"\n=== {lane} ===", flush=True)
        rc = subprocess.run(cmd).returncode
        if rc not in (0, 5):
            rc_all = 1
        if os.path.isfile(xml):
            xmls.append(xml)
    report(p, xmls)
    if a.record and xmls:
        print()
        test_timings.refresh(xmls, prune=False)
    return rc_all


def report(p: dict, xmls: list[str]) -> None:
    owners, times = p["owners"], p["times"]
    rows: dict[str, dict] = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0, "s": 0.0, "est": 0.0})
    failed, slower = [], []
    for x in xmls:
        for case in ET.parse(x).getroot().iter("testcase"):
            nid = test_timings.node_id(case)
            if nid is None:
                continue
            f = nid.split("::", 1)[0]
            suite = (owners.get(f) or ["(no suite)"])[0]
            s = float(case.get("time") or 0)
            r = rows[suite]
            if case.find("failure") is not None or case.find("error") is not None:
                r["failed"] += 1
                failed.append((suite, nid))
            elif case.find("skipped") is not None:
                r["skipped"] += 1
                continue
            else:
                r["passed"] += 1
            r["s"] += s
            rec = test_time(times, nid)
            if rec is not None:
                r["est"] += rec
                if s > SLOWER * rec and s - rec > SLOWER_MIN_S:
                    slower.append((s, rec, nid))
    print("\nReport (time = passed and failed tests; estimate = their recorded serial times):")
    for suite in sorted(rows):
        r = rows[suite]
        verdict = "ok" if not r["failed"] else "FAILED"
        print(f"  {suite}: {verdict}  {r['passed']} passed, {r['failed']} failed, {r['skipped']} skipped;  "
              f"{fmt(r['s'])} (estimate {fmt(r['est'])})")
    if failed:
        print("\nFailed:")
        for suite, nid in failed:
            print(f"  [{suite}] {nid}")
    if slower:
        print(f"\nSlower than recorded (over {SLOWER:g}x and {SLOWER_MIN_S:g} s more):")
        for s, rec, nid in sorted(slower, reverse=True)[:15]:
            print(f"  {fmt(s)} vs {fmt(rec)}  {nid}")


# ------------------------------------------------------------------------------------------------ list

def list_suites(catalog: dict, files: list[str]) -> None:
    tests = test_files(files)
    owners = suite_of(catalog, tests)
    times = test_timings.load()
    grand = [0, 0, 0.0]
    for name, s in suites(catalog).items():
        mine = [t for t, n in owners.items() if n and n[0] == name]
        n = sum(test_timings.count(times[t]) for t in mine if t in times)
        secs = sum(test_timings.total(times[t]) for t in mine if t in times)
        untimed = sum(1 for t in mine if t not in times)
        extra = f", {untimed} file(s) untimed" if untimed else ""
        print(f"{name}  ({len(mine)} files, {n} tests, {fmt(secs)} serial incl. slow{extra})")
        print(f"    {s['about']}")
        grand[0] += len(mine)
        grand[1] += n
        grand[2] += secs
    print(f"\n{len(suites(catalog))} suites, {grand[0]} files, {grand[1]} tests, {fmt(grand[2])} serial. "
          "`plan --suite NAME` splits a suite into lanes; `plan --git` picks suites for a branch.")


# ------------------------------------------------------------------------------------------------ main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("check")
    for cmd in ("plan", "run"):
        p = sub.add_parser(cmd)
        p.add_argument("paths", nargs="*")
        p.add_argument("--git", nargs="?", const="origin/main", default=None, metavar="BASE")
        p.add_argument("--suite", nargs="+")
        p.add_argument("--slow", action="store_true")
        p.add_argument("--browser", action="store_true")
        if cmd == "run":
            p.add_argument("--record", action="store_true")
    a = ap.parse_args(argv)
    if not os.path.isfile(CATALOG):
        print("run from webapp/", file=sys.stderr)
        return 2
    catalog = load_catalog()
    files = all_files()
    if a.cmd == "list":
        list_suites(catalog, files)
        return 0
    if a.cmd == "check":
        problems = check(catalog, files)
        for pr in problems:
            print(pr)
        n = len(test_files(files))
        print(f"{len(problems)} problem(s); {len(suites(catalog))} suites, {n} test files")
        return 1 if problems else 0
    plan = build_plan(a, catalog, files)
    if a.cmd == "plan":
        print_plan(plan, a, catalog)
        return 0
    return run(plan, a, catalog)


if __name__ == "__main__":
    sys.exit(main())
