"""Map control as Workflow runs (spec 2026-10-14, step 2): the control task against a fake worker link, then
ControlRunner end to end through a fake Render. The child is a stub command, as in test_replay_worker_control."""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from fake_render import FakeRender  # noqa: E402
from replay_tasks import tasks  # noqa: E402
from replay_worker import runs, server  # noqa: E402

STUB = """
import json, os, sys, pathlib, time
task = json.loads(sys.stdin.buffer.read())
if str(task["link"].get("mode", "")).startswith("sleep"):
    time.sleep(float(task["link"]["mode"].split(":")[1]))
cache = pathlib.Path(os.environ["CONTROL_CACHE_DIR"])
had = sorted(p.relative_to(cache).as_posix() for p in cache.rglob("*") if p.is_file())
if task["link"].get("build"):
    (cache / f"{task['map']}.{'c' * 16}.npz").write_bytes(b"built")
if task["link"].get("learn"):              # feature rows learned: the same file, rewritten in place
    rows = cache / "feature-rows" / f"{task['map']}.{'d' * 64}.npz"
    rows.write_bytes(rows.read_bytes() + b"+more")
mode = task["link"].get("mode", "ok")
if mode == "engine":
    print(json.dumps({"status": "failed", "error_kind": "engine", "error": "no side"}))
else:
    print(json.dumps({"status": "ok", "data": "ZA==", "summary": "cw==", "key": task["key"], "had": had}))
"""


class FakeLink:
    def __init__(self, files: dict[str, bytes]):
        self.files, self.posted, self.result, self.order = files, {}, None, []

    def fetch(self, name, dest):
        if name not in self.files:
            raise OSError(f"404 {name}")             # as urllib's HTTPError (an OSError) for a pruned file
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.files[name])
        return Path(dest)

    def post_file(self, route, src):
        self.order.append(route)
        self.posted[route] = Path(src).read_bytes()

    def post_result(self, result):
        self.order.append("result")
        self.result = result


@pytest.fixture
def stub(tmp_path, monkeypatch):
    path = tmp_path / "stub.py"
    path.write_text(STUB, encoding="utf-8")
    monkeypatch.setenv("REPLAY_CONTROL_CMD", json.dumps([sys.executable, str(path)]))
    monkeypatch.setenv("REPLAY_CONTROL_MEMORY_MB", "0")
    return path


def round_task(key="k1", map_name="Ascent", **link):
    return {"key": key, "map": map_name, "blob": "", "link": {"sides": {}, "db_deaths": [], **link}}


def test_control_task_mirrors_caches_runs_the_child_and_posts_new_caches_first(stub):
    link = FakeLink({"task": json.dumps(round_task(build=True)).encode(),
                     "Ascent.0123456789abcdef.npz": b"old", "heights/Ascent.d1.height.npz": b"h"})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent",
                   "files": ["Ascent.0123456789abcdef.npz", "heights/Ascent.d1.height.npz"]}, link)
    assert link.result["status"] == "ok"
    assert link.result["had"] == ["Ascent.0123456789abcdef.npz", "heights/Ascent.d1.height.npz"]
    assert link.order == [f"cache/Ascent.{'c' * 16}.npz", "result"]
    assert link.posted[f"cache/Ascent.{'c' * 16}.npz"] == b"built"


def test_control_task_posts_a_cache_rewritten_in_place(stub):
    rows = f"feature-rows/Ascent.{'d' * 64}.npz"
    link = FakeLink({"task": json.dumps(round_task(learn=True)).encode(), rows: b"rows"})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": [rows]}, link)
    route = "cache/" + rows.replace("/", "%2F")
    assert link.order == [route, "result"] and link.posted[route] == b"rows+more"


def test_control_task_skips_a_cache_file_it_cannot_fetch(stub):
    link = FakeLink({"task": json.dumps(round_task()).encode()})       # the listed cache was pruned meanwhile
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": ["Ascent.0123456789abcdef.npz"]}, link)
    assert link.result["status"] == "ok" and link.result["had"] == []


def test_control_task_passes_an_engine_failure_through(stub):
    link = FakeLink({"task": json.dumps(round_task(mode="engine")).encode()})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    assert link.result == {"status": "failed", "error_kind": "engine", "error": "no side"}


def test_control_task_reports_a_child_that_prints_nothing_as_infra(tmp_path, monkeypatch):
    silent = tmp_path / "silent.py"
    silent.write_text("", encoding="utf-8")
    monkeypatch.setenv("REPLAY_CONTROL_CMD", json.dumps([sys.executable, str(silent)]))
    link = FakeLink({"task": json.dumps(round_task()).encode()})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    assert link.result["status"] == "failed" and link.result["error_kind"] == "infra"
