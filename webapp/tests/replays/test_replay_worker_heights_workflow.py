"""Height builds as Workflow runs (spec 2026-10-14, step 4): the heights task with a stub child, then a build
end to end through HeightBuilds, ControlRunner and a fake Render."""

import base64
import json
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from fake_render import FakeRender  # noqa: E402
from replay_tasks import tasks  # noqa: E402
from replay_worker import control_cache, server  # noqa: E402

STUB = """
import json, os, sys, pathlib, time
task = json.loads(sys.stdin.buffer.read())
time.sleep(float(os.environ.get("HEIGHT_STUB_SLEEP", "0")))
folder = pathlib.Path(task["dir"])
cache = pathlib.Path(os.environ["CONTROL_CACHE_DIR"])
rounds = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
prev = (cache / "heights" / f"{task['map']}.{task.get('previous')}.height.npz").is_file()
print(json.dumps({"status": "ok", "key": task["key"], "rounds_seen": rounds, "had_previous": prev,
                  "asset": "YQ=="}))
"""


class FakeLink:
    def __init__(self, files):
        self.files, self.result = files, None

    def fetch(self, name, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.files[name])
        return Path(dest)

    def post_file(self, route, src):
        pass

    def post_result(self, result):
        self.result = result


@pytest.fixture
def stub(tmp_path, monkeypatch):
    path = tmp_path / "stub_height.py"
    path.write_text(STUB, encoding="utf-8")
    monkeypatch.setenv("REPLAY_HEIGHT_CMD", json.dumps([sys.executable, str(path)]))
    monkeypatch.setenv("REPLAY_CONTROL_MEMORY_MB", "0")


def test_heights_task_unpacks_the_rounds_and_passes_previous(tmp_path, stub):
    spool = tmp_path / "spool"
    (spool / "m1").mkdir(parents=True)
    (spool / "m1" / "1.json.gz").write_bytes(b"r")
    tar = control_cache.pack(spool, tmp_path / "rounds.tar").read_bytes()
    task = {"key": "k", "map": "Ascent", "dir": "/worker/path/ignored", "previous": "d0"}
    link = FakeLink({"task": json.dumps(task).encode(), "rounds": tar, "heights/Ascent.d0.height.npz": b"p"})
    tasks.heights({"job_id": "j", "token": "t", "map": "Ascent", "files": ["heights/Ascent.d0.height.npz"]}, link)
    assert link.result["status"] == "ok"
    assert link.result["rounds_seen"] == ["m1/1.json.gz"] and link.result["had_previous"] is True


def test_heights_task_refuses_a_bad_tar_as_infra(tmp_path, stub):
    link = FakeLink({"task": b'{"key": "k", "map": "Ascent"}', "rounds": b"not a tar"})
    tasks.heights({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    assert link.result["status"] == "failed" and link.result["error_kind"] == "infra"
