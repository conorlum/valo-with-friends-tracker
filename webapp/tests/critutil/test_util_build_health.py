"""Critical Util build health uses queued events and stays light enough for the card."""

import subprocess
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.services import site_health, util_build_health, util_rebuild


KEYS = ("dispatch", "push", "run", "collect", "store", "gave_up", "digest")


@pytest.fixture(autouse=True)
def reset_build_health(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    util_build_health.set_in_flight(False)
    yield
    util_build_health.set_in_flight(False)


@pytest.mark.parametrize("key", KEYS)
def test_record_queues_one_failure_with_its_context(key):
    context = {"ref": "test-build", "attempt": 2}
    util_build_health.record(key, "build failed", context)
    event, = site_health._queue
    assert (event["op"], event["kind"], event["key"]) == (
        "record", "util_build_failed", f"remote:{key}")
    assert event["error"] == "build failed"
    assert event["context"] == context


def test_clear_all_queues_every_remote_key_and_the_local_child():
    assert util_build_health.KEYS == KEYS
    util_build_health.clear_all()
    events = list(site_health._queue)
    assert len(events) == len(KEYS) + 1
    assert {event["key"] for event in events} == {f"remote:{key}" for key in KEYS} | {"build"}
    assert all(event["op"] == "resolve" and event["kind"] == "util_build_failed"
               and event["prefix"] is False for event in events)


@pytest.mark.parametrize("key", ["unknown", "remote:run", "", None])
def test_unknown_key_raises_without_queuing(key):
    with pytest.raises(ValueError):
        util_build_health.record(key, "build failed", {})
    assert not site_health._queue


def test_in_flight_tracks_remote_builds_and_the_local_child(monkeypatch):
    class Child:
        done = False

        def poll(self):
            return 0 if self.done else None

    assert util_build_health.in_flight() is False
    util_build_health.set_in_flight(True)
    assert util_build_health.in_flight() is True
    child = Child()
    monkeypatch.setattr(util_rebuild, "_child", child)
    util_build_health.set_in_flight(False)
    assert util_build_health.in_flight() is True
    child.done = True
    assert util_build_health.in_flight() is False
    util_build_health.set_in_flight(True)
    assert util_build_health.in_flight() is True
    util_build_health.set_in_flight(False)
    assert util_build_health.in_flight() is False


def test_demo_mode_does_not_queue_health_writes(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    util_build_health.record("run", "build failed", {})
    util_build_health.clear_all()
    assert not site_health._queue


def test_import_and_in_flight_do_not_load_the_remote_builder_or_heavy_modules():
    script = """
import sys
from app.services import util_build_health
assert util_build_health.in_flight() is False
for name in ('app.services.util_build_remote', 'app.critutil.corpus', 'numpy', 'scipy'):
    assert name not in sys.modules, name
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
