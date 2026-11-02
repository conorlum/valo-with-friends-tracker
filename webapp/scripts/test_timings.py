"""Per-test run times, committed in tests/timings.json, so a test run can be sized and split into parts without
first timing the whole suite (an AFK run's serial baseline spent 92 min doing only that, 2026-10-28).

    python scripts/test_timings.py refresh <junit.xml> [<junit.xml> ...] [--prune]
    python scripts/test_timings.py show [--by dir|file] [--top N]

The file lists, per test file, every test of at least LISTED_S by name, and the rest only as a count and a
total: a part is packed in minutes, so a quick test's own time doesn't matter, and the names of ~5000 quick tests
were 85% of the file. A test that isn't listed is timed at its file's mean quick test.

`refresh` takes the times from `pytest --junitxml` files (later files winning per test) and changes only the files
they ran. A file the XMLs ran whole (at least as many tests as it holds) is replaced; a partly run file has its
ran tests updated, and its quick-test count and total replaced only when the run had at least as many quick tests.
Skipped tests are left out (a skip's time says nothing). Feed it serial (`-n 0`) runs: a parallel run's times are
inflated by its neighbours. `--prune` drops files that no longer exist. `show` totals the file by directory or file.

Run from webapp/. The AFK skill's split_suite.py reads this file through `--times`; a test's id is pytest's node id
(`tests/x/test_y.py::TestK::test_z[param]`) split at its first `::`, the same id split_suite.py derives from the XML.
"""

import argparse
import datetime
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

PATH = os.path.join("tests", "timings.json")
LISTED_S = 0.5
ABOUT = ("Seconds per test, serial (-n 0), slow tests included, on the owner's 12-core dev machine. Per test file: "
         f"each test of {LISTED_S} s or more by name, the rest as [count, total s]. Tests that build toy maps "
         "(tests/replays/control_toys.py) may hold cold-cache times, far above a warm run's. Refresh: python "
         "scripts/test_timings.py refresh <junit.xml>. A machine under load runs up to ~2x these.")


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
    """{file: {"tests": {name: s}, "rest": [count, total]}}; the flat {"tests": {node id: s}} form is read too."""
    if not os.path.isfile(PATH):
        return {}
    with open(PATH, encoding="utf-8") as f:
        data = json.load(f)
    if "files" in data:
        return {f: {"tests": dict(e.get("tests", {})), "rest": list(e.get("rest", [0, 0.0]))}
                for f, e in data["files"].items()}
    return _compact({nid: s for nid, s in data.get("tests", {}).items()})


def _compact(flat: dict) -> dict:
    files = defaultdict(lambda: {"tests": {}, "rest": [0, 0.0]})
    for nid, s in flat.items():
        f, name = nid.split("::", 1)
        if s >= LISTED_S:
            files[f]["tests"][name] = s
        else:
            files[f]["rest"][0] += 1
            files[f]["rest"][1] += s
    return dict(files)


def save(files: dict) -> None:
    out = {}
    for f in sorted(files):
        e = files[f]
        entry = {}
        if e["tests"]:
            entry["tests"] = {k: round(e["tests"][k], 1) for k in sorted(e["tests"])}
        if e["rest"][0]:
            entry["rest"] = [e["rest"][0], round(e["rest"][1], 1)]
        if entry:
            out[f] = entry
    data = {"about": ABOUT, "updated": datetime.date.today().isoformat(), "files": out}
    with open(PATH, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=0, ensure_ascii=False)
        fh.write("\n")


def count(e: dict) -> int:
    return len(e["tests"]) + e["rest"][0]


def total(e: dict) -> float:
    return sum(e["tests"].values()) + e["rest"][1]


def refresh(xmls: list[str], prune: bool) -> int:
    files = load()
    ran, unresolved = {}, 0
    for x in xmls:
        for case in ET.parse(x).getroot().iter("testcase"):
            if case.find("skipped") is not None:
                continue
            nid = node_id(case)
            if nid is None:
                unresolved += 1
                continue
            ran[nid] = float(case.get("time") or 0)
    replaced = updated = 0
    for f, new in _compact(ran).items():
        old = files.get(f)
        if old is None or count(new) >= count(old):
            files[f] = new
            replaced += 1
            continue
        for name in [n for n in old["tests"] if f + "::" + n in ran]:
            del old["tests"][name]                 # re-filed below, listed or quick, from this run
        old["tests"].update(new["tests"])
        if new["rest"][0] >= old["rest"][0]:
            old["rest"] = new["rest"]
        updated += 1
    dropped = 0
    if prune:
        for f in [f for f in files if not os.path.isfile(f)]:
            del files[f]
            dropped += 1
    save(files)
    n = sum(count(e) for e in files.values())
    print(f"{n} tests in {len(files)} files: {replaced} files replaced, {updated} partly updated, {dropped} pruned, "
          f"{unresolved} tests not found under tests/; total {sum(total(e) for e in files.values()) / 60:.1f} min serial")
    return 1 if unresolved and not ran else 0


def show(by: str, top: int) -> int:
    files = load()
    agg = defaultdict(lambda: [0.0, 0])
    for f, e in files.items():
        key = os.path.dirname(f) if by == "dir" else f
        agg[key][0] += total(e)
        agg[key][1] += count(e)
    print(f"{sum(count(e) for e in files.values())} tests, {sum(total(e) for e in files.values()) / 60:.1f} min serial")
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
