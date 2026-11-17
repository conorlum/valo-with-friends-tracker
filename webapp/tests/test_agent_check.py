"""Daily agent inventory checks use fake fetches and the health queue."""
import json
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from app.services import agent_check as check, site_health


def agent(code, name, playable=True):
    return {"developerName": code, "displayName": name,
            "isPlayableCharacter": playable, "role": {"displayName": "Duelist"}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(check.settings, "demo_mode", False)
    monkeypatch.setattr(check, "_last_check", None)
    monkeypatch.setattr(check.condense, "load_agents", lambda: {"Terra": "Waylay", "Aggrobot": "Gekko"})
    monkeypatch.setattr(check.deploy_drain, "paused_now", lambda: False)


def fetch_rows(*rows):
    def fetch(url):
        assert url == check.API_URL
        return json.dumps({"data": list(rows)})
    return fetch


def agent_ops():
    return [op for op in site_health._queue if op["kind"] == "agent_unsupported"]


def test_missing_supported_case_and_nonplayable():
    result = check.check(fetch_rows(agent("Terra", "Waylay"), agent("Paprika", "Hush"),
                                    agent("aggrobot", "Gekko"), agent("TrainingBot", "Bot", False)))
    assert result == {"listed": 3, "missing": 1, "skipped": False}
    ops = agent_ops()
    assert [(op["op"], op["key"]) for op in ops] == [
        ("resolve", "Terra"), ("record", "Paprika"), ("resolve", "aggrobot")]
    assert ops[1]["error"] == "Hush (Paprika) is not supported yet"
    assert ops[1]["context"] == {"display_name": "Hush", "role": "Duelist"}


@pytest.mark.parametrize("body", [
    '{"data": {}}', '{"data": null}', 'not json', '{}',
    json.dumps({"data": [agent("Paprika", "Hush"), {"displayName": "Bad"}]}),
    json.dumps({"data": [agent("Terra", "Waylay"), {"developerName": "Bad"}]}),
    json.dumps({"data": [agent("Paprika", "Hush"), None]}),
])
def test_malformed_response_changes_no_agent_rows(body):
    check.check(lambda url: body)
    assert agent_ops() == []
    assert [(op["kind"], op["key"]) for op in site_health._queue] == [
        ("loop_failed", check.CHECK_KEY)]


def test_fetch_failure_changes_no_agent_rows():
    def fail(url):
        raise OSError("synthetic outage")
    check.check(fail)
    assert agent_ops() == []
    [failure] = site_health._queue
    assert failure["kind"] == "loop_failed" and failure["key"] == check.CHECK_KEY


def test_drain_skips_without_fetching_or_recording(monkeypatch):
    monkeypatch.setattr(check.deploy_drain, "paused_now", lambda: True)
    def forbidden(url):
        pytest.fail("fetch during drain")
    assert check.check(forbidden) == {"listed": 0, "missing": 0, "skipped": True}
    assert not site_health._queue


def test_session_is_closed_before_fetch(monkeypatch):
    opened = []
    @contextmanager
    def factory():
        opened.append(True)
        yield "synthetic session"
        opened.pop()
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    def paused(db, at):
        assert db == "synthetic session" and at == now
        return False
    monkeypatch.setattr(check.deploy_drain, "paused", paused)
    def fetch(url):
        assert not opened
        return '{"data": []}'
    assert check.check(fetch, now, session_factory=factory)["skipped"] is False


def test_enabled_and_start_are_off_in_demo(monkeypatch):
    monkeypatch.setattr(check.settings, "agent_check_auto", False)
    assert check.start(None) is None
    monkeypatch.setattr(check.settings, "agent_check_auto", True)
    assert check.enabled()
    monkeypatch.setattr(check.settings, "demo_mode", True)
    assert not check.enabled() and check.start(None) is None


def test_newly_supported_agent_resolves_same_key(monkeypatch):
    fetch = fetch_rows(agent("Paprika", "Hush"))
    check.check(fetch)
    monkeypatch.setattr(check.condense, "load_agents", lambda: {"Paprika": "Hush"})
    check.check(fetch)
    assert [(op["op"], op["key"]) for op in agent_ops()] == [
        ("record", "Paprika"), ("resolve", "Paprika")]
    assert site_health._queue[-1]["op"] == "resolve"
    assert site_health._queue[-1]["key"] == check.CHECK_KEY


class Cycles:
    """A foreground loop driver with deterministic waits and clock advances."""
    def __init__(self, advances, clock):
        self.advances = iter(advances)
        self.clock = clock
        self.stopped = False
        self.waits = []

    def wait(self, seconds):
        self.waits.append(seconds)
        advance = next(self.advances, None)
        if advance is None:
            self.stopped = True
        else:
            self.clock[0] += advance
        return self.stopped

    def is_set(self):
        return self.stopped


def test_cadence_runs_once_until_last_check_is_a_day_old(monkeypatch):
    clock, calls = [0], []
    monkeypatch.setattr(check.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(check, "MIN_GAP_S", 1000)
    def fake(*args, **kwargs):
        calls.append(clock[0])
        assert site_health.loops()[check.LOOP_NAME]["allowed_s"] == 26 * 3600
        return {"listed": 0, "missing": 0, "skipped": False}
    monkeypatch.setattr(check, "check", fake)
    stop = Cycles([0, 1, 1, 1000], clock)
    check.run_forever(None, None, stop, every_s=0, first_delay_s=0)
    assert calls == [0, 1002]
    assert check._last_check == 1002
    assert check.LOOP_NAME not in site_health.loops()


def test_paused_then_resumed_retries_without_waiting_a_day(monkeypatch):
    clock, calls, pauses = [0], [], []
    monkeypatch.setattr(check.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(check, "DRAIN_RETRY_S", 0)
    def paused():
        assert check._last_check is None
        assert not site_health._queue
        pauses.append(clock[0])
        return clock[0] == 0
    monkeypatch.setattr(check.deploy_drain, "paused_now", paused)
    def fetch(url):
        calls.append(clock[0])
        return '{"data": []}'
    stop = Cycles([0, 1], clock)
    check.run_forever(None, fetch, stop, every_s=86400, first_delay_s=0)
    assert pauses == [0, 1] and calls == [1]
    assert stop.waits == [0, 0, 86400]
    assert check._last_check == 1
