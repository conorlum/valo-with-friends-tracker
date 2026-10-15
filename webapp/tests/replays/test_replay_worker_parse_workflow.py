"""Parsing as Workflow runs (spec 2026-10-14, step 3): the parse task, then the worker's parse threads end to end
through a fake Render."""

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
from replay_worker import parse_job, server  # noqa: E402


class FakeLink:
    def __init__(self, files):
        self.files, self.result = files, None

    def fetch(self, name, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.files[name])
        return Path(dest)

    def post_result(self, result):
        self.result = result


def test_parse_refuses_another_recipe(monkeypatch):
    monkeypatch.setattr(tasks, "_recipe", lambda: "r-new")
    called = []
    monkeypatch.setattr(parse_job, "parse_file", lambda *a, **k: called.append(1))
    link = FakeLink({"vrf": b"x"})
    tasks.parse({"job_id": "j", "token": "t", "recipe": "r-old", "sha256": "s"}, link)
    assert link.result["error_kind"] == "deploy" and called == []      # refused before any parse


def test_parse_reports_a_parse_failure_with_detail(monkeypatch):
    monkeypatch.setattr(tasks, "_recipe", lambda: "r")

    def boom(*a, **k):
        raise parse_job.ParseFailed("parse failed", "Boom: bad chunk", 3.5)
    monkeypatch.setattr(parse_job, "parse_file", boom)
    link = FakeLink({"vrf": b"x"})
    tasks.parse({"job_id": "j", "token": "t", "recipe": "r", "sha256": "s"}, link)
    assert link.result == {"status": "failed", "error_kind": "parse", "error": "parse failed",
                           "detail": "Boom: bad chunk", "parse_seconds": 3.5}


def test_parse_posts_the_result(monkeypatch):
    monkeypatch.setattr(tasks, "_recipe", lambda: "r")
    monkeypatch.setattr(parse_job, "parse_file", lambda *a, **k: ({"match_uuid": "M", "map_name": "Ascent"}, 4.2))
    link = FakeLink({"vrf": b"x"})
    tasks.parse({"job_id": "j", "token": "t", "recipe": "r", "sha256": "s"}, link)
    assert link.result == {"status": "ok", "result": {"match_uuid": "M", "map_name": "Ascent"}, "parse_seconds": 4.2}
