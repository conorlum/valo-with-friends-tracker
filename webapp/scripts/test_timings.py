"""Per-test run times, committed in tests/timings.json, so a test run can be sized and split into parts without
first timing the whole suite (an AFK run's serial baseline spent 92 min doing only that, 2026-10-28).

    python scripts/test_timings.py refresh <junit.xml> [<junit.xml> ...] [--prune]
    python scripts/test_timings.py show [--by dir|file] [--top N]

`refresh` takes the times from `pytest --junitxml` files, later files winning per test, and keeps every other
test's old time. Skipped tests are left out (a skip's time says nothing). Feed it serial (`-n 0`) runs: a
parallel run's times are inflated by its neighbours. Any subset is fine: one file's run refreshes that file.
`--prune` drops tests whose file no longer exists. `show` totals the file by directory or file.

Run from webapp/. The AFK skill's split_suite.py reads this file through `--times`; a test's id here is pytest's
node id (`tests/x/test_y.py::TestK::test_z[param]`), the same id split_suite.py derives from the XML.
"""

import argparse
import datetime
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

PATH = os.path.join("tests", "timings.json")
ABOUT = ("Seconds per test, serial (-n 0), slow tests included, on the owner's 12-core dev machine. Tests that "
         "build toy maps (tests/replays/control_toys.py) may hold cold-cache times, far above a warm run's. "
         "Refresh: python scripts/test_timings.py refresh <junit.xml>. A machine under load runs up to ~2x these.")


def _file_of(classname: str) -> str | None:
    parts = classname.split(".")
    for n in range(len(parts), 0, -1):
        rel = "/".join(parts[:n]) + ".py"
        if os.path.isfile(rel):
            return rel
    return None


def node_id(case) -> str | None:
    classname = case.get("classname") or ""
    f = _file_of(classname)
    if f is None:
        return None
    module = f[:-3].replace("/", ".")
    rest = classname[len(module) + 1:] if classname.startswith(module + ".") else ""
    return f + ("::" + rest.replace(".", "::") if rest else "") + "::" + case.get("name", "")


def load() -> dict:
    if not os.path.isfile(PATH):
        return {}
    with open(PATH, encoding="utf-8") as f:
        return json.load(f).get("tests", {})


def save(tests: dict) -> None:
    data = {"about": ABOUT, "updated": datetime.date.today().isoformat(),
            "tests": {k: tests[k] for k in sorted(tests)}}
    with open(PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=0, ensure_ascii=False)
        f.write("\n")


def refresh(xmls: list[str], prune: bool) -> int:
    tests = load()
    before = dict(tests)
    unresolved = 0
    for x in xmls:
        for case in ET.parse(x).getroot().iter("testcase"):
            if case.find("skipped") is not None:
                continue
            nid = node_id(case)
            if nid is None:
                unresolved += 1
                continue
            tests[nid] = round(float(case.get("time") or 0), 2)
    dropped = 0
    if prune:
        for nid in [k for k in tests if not os.path.isfile(k.split("::", 1)[0])]:
            del tests[nid]
            dropped += 1
    save(tests)
    changed = sum(1 for k, v in tests.items() if before.get(k) != v)
    print(f"{len(tests)} tests, {changed} new or changed, {dropped} pruned, {unresolved} not found under tests/; "
          f"total {sum(tests.values()) / 60:.1f} min serial")
    return 1 if unresolved and not changed else 0


def show(by: str, top: int) -> int:
    tests = load()
    agg = defaultdict(lambda: [0.0, 0])
    for nid, s in tests.items():
        f = nid.split("::", 1)[0]
        key = os.path.dirname(f) if by == "dir" else f
        agg[key][0] += s
        agg[key][1] += 1
    print(f"{len(tests)} tests, {sum(tests.values()) / 60:.1f} min serial")
    for key, (s, n) in sorted(agg.items(), key=lambda kv: -kv[1][0])[:top]:
        print(f"{s:8.0f} s {n:6d}  {key}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh")
    r.add_argument("xml", nargs="+")
    r.add_argument("--prune", action="store_true")
    s = sub.add_parser("show")
    s.add_argument("--by", choices=("dir", "file"), default="dir")
    s.add_argument("--top", type=int, default=30)
    a = ap.parse_args(argv)
    if not os.path.isdir("tests"):
        print("run from webapp/", file=sys.stderr)
        return 2
    return refresh(a.xml, a.prune) if a.cmd == "refresh" else show(a.by, a.top)


if __name__ == "__main__":
    sys.exit(main())
