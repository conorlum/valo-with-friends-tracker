"""Replay-search index sweep: pacing, cooldown, drain and lifecycle; mocks only, no database or threads."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from app.config import settings
from app.services import replay_search_sweep as sweep


class Stop:
    def __init__(self, stop_on_wait=None):
        self.stopped = False
        self.waits = []
        self.stop_on_wait = stop_on_wait

    def set(self):
        self.stopped = True

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.waits.append(seconds)
        if len(self.waits) == self.stop_on_wait:
            self.set()
        return self.stopped


@pytest.fixture
def batch(monkeypatch):
    monkeypatch.setattr(sweep.deploy_drain, "paused_now", lambda: False)
    db = Mock()
    factory = Mock(return_value=db)
    select = Mock(return_value=[9, 8, 7])
    drop = Mock(return_value=0)

    def rebuild(factory_arg, replay_id):
        assert factory_arg is factory
        db.close.assert_called_once_with()
        return "rebuilt: 4 rows"

    return SimpleNamespace(db=db, factory=factory, select=select, drop=drop,
                           rebuild=Mock(side_effect=rebuild), stop=Stop())


def run_batch(batch, failed_until=None, now=100, **kwargs):
    return sweep.run_pass(batch.factory, batch.stop, {} if failed_until is None else failed_until, now,
                          select=batch.select, rebuild=batch.rebuild, drop=batch.drop, **kwargs)


def test_constants():
    assert (sweep.LOOP_NAME, sweep.CYCLE_S, sweep.FIRST_DELAY_S) == ("search-index", 120, 90)
    assert (sweep.PER_PASS, sweep.PAUSE_S, sweep.RETRY_AFTER_S) == (10, 0.2, 6 * 3600)


def test_order_counts_one_selection_session_drop_once_and_pacing(batch):
    assert run_batch(batch) == {"rebuilt": 3}
    batch.select.assert_called_once_with(batch.db, 10, skip=set())
    batch.drop.assert_called_once_with(batch.db)
    batch.db.commit.assert_called_once_with()
    assert batch.rebuild.call_args_list == [call(batch.factory, i) for i in [9, 8, 7]]
    batch.factory.assert_called_once_with()
    batch.db.close.assert_called_once_with()
    assert batch.stop.waits == [0.2, 0.2]


def test_defaults_are_resolved_at_call_time(batch, monkeypatch):
    monkeypatch.setattr(sweep.replay_search_index, "pending", batch.select)
    monkeypatch.setattr(sweep.replay_search_index, "rebuild", batch.rebuild)
    monkeypatch.setattr(sweep.replay_search_index, "drop_unlinked", batch.drop)
    assert sweep.run_pass(batch.factory, batch.stop, {}, 100) == {"rebuilt": 3}


def test_paused_selects_nothing(batch):
    failed_until = {8: 0}
    assert run_batch(batch, failed_until, paused=lambda: True) == {"paused": 1}
    assert failed_until == {8: 0}
    batch.factory.assert_not_called()
    batch.select.assert_not_called()
    batch.drop.assert_not_called()
    batch.rebuild.assert_not_called()


def test_pause_between_replays_stops_and_cools_nothing(batch):
    failed_until = {}
    paused = Mock(side_effect=[False, False, True])
    assert run_batch(batch, failed_until, paused=paused) == {"rebuilt": 1, "paused": 1}
    batch.rebuild.assert_called_once_with(batch.factory, 9)
    assert failed_until == {}


def test_failure_cools_down_for_six_hours_then_is_retried(batch):
    failed_until = {}
    batch.rebuild.side_effect = ["rebuilt: 1 rows", "failed: boom", "rebuilt: 2 rows"]
    assert run_batch(batch, failed_until) == {"rebuilt": 2, "failed": 1}
    assert failed_until == {8: 100 + 21600}
    batch.select.return_value = []
    assert run_batch(batch, failed_until, now=100 + 3600) == {}
    assert batch.select.call_args == call(batch.db, 10, skip={8})
    assert run_batch(batch, failed_until, now=100 + 21601) == {}
    assert batch.select.call_args == call(batch.db, 10, skip=set())
    assert failed_until == {}


def test_stop_after_first_rebuild(batch):
    def rebuild(*args):
        batch.stop.set()
        return "rebuilt: 1 rows"

    batch.rebuild.side_effect = rebuild
    assert run_batch(batch) == {"rebuilt": 1}
    batch.rebuild.assert_called_once_with(batch.factory, 9)


def test_stop_during_pause(batch):
    batch.stop.stop_on_wait = 1
    assert run_batch(batch) == {"rebuilt": 1}
    assert batch.stop.waits == [0.2]


def test_stop_before_first_rebuild(batch):
    batch.stop.set()
    assert run_batch(batch) == {}
    batch.rebuild.assert_not_called()


def test_selection_error_rolls_back_and_closes(batch):
    batch.select.side_effect = RuntimeError("selection failed")
    with pytest.raises(RuntimeError, match="selection failed"):
        run_batch(batch)
    batch.db.rollback.assert_called()
    batch.db.close.assert_called_once_with()
    batch.rebuild.assert_not_called()


def test_drop_error_does_not_block_rebuilds(batch):
    batch.drop.side_effect = RuntimeError("drop failed")
    assert run_batch(batch) == {"rebuilt": 3}
    batch.db.rollback.assert_called()


@pytest.fixture
def health(monkeypatch):
    events = Mock()
    for name in ["register", "beat", "loop_ok", "loop_failed", "unregister"]:
        monkeypatch.setattr(sweep.site_health, name, getattr(events, name))
    return events


@pytest.mark.parametrize("raises", [False, True])
def test_cadence_health_and_failure_recovery(monkeypatch, health, raises):
    stop = Stop(stop_on_wait=3)
    error = RuntimeError("pass error")
    run_pass = Mock(side_effect=[error if raises else {"rebuilt": 1}, {}])
    monkeypatch.setattr(sweep, "run_pass", run_pass)
    monkeypatch.setattr(sweep.time, "monotonic", lambda: 123)
    factory = Mock()
    sweep.run_forever(factory, stop)
    assert stop.waits == [90, 120, 120]
    assert run_pass.call_count == 2
    for args, kwargs in run_pass.call_args_list:
        assert args[0] is factory and args[1] is stop and args[3] == 123
    assert health.mock_calls == [
        call.register("search-index", 120), call.beat("search-index", "start"),
        call.loop_failed("search-index", error) if raises else call.loop_ok("search-index"),
        call.beat("search-index", "end"), call.beat("search-index", "start"),
        call.loop_ok("search-index"), call.beat("search-index", "end"),
        call.unregister("search-index"),
    ]


def test_stop_in_first_delay_never_runs_a_pass(monkeypatch, health):
    run_pass = Mock()
    monkeypatch.setattr(sweep, "run_pass", run_pass)
    sweep.run_forever(Mock(), Stop(stop_on_wait=1))
    run_pass.assert_not_called()
    assert health.mock_calls == [call.register("search-index", 120), call.unregister("search-index")]


def test_cooldown_dict_is_shared_between_passes(monkeypatch, health):
    stop = Stop()
    seen = []

    def run_pass(factory, event, failed_until, now):
        seen.append(failed_until)
        if len(seen) == 1:
            failed_until[8] = now + 21600
        else:
            assert 8 in failed_until
            stop.set()
        return {}

    monkeypatch.setattr(sweep, "run_pass", run_pass)
    sweep.run_forever(Mock(), stop, first_delay_s=0, every_s=0)
    assert len(seen) == 2 and seen[0] is seen[1]


def test_start_returns_stop_and_starts_daemon(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    thread = Mock()
    monkeypatch.setattr(threading, "Thread", thread)
    factory = Mock()
    stop = sweep.start(factory)
    assert isinstance(stop, threading.Event) and not stop.is_set()
    thread.assert_called_once_with(target=sweep.run_forever, args=(factory, stop), name="search-index", daemon=True)
    thread.return_value.start.assert_called_once_with()


def test_demo_mode_starts_no_thread(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    thread = Mock()
    monkeypatch.setattr(threading, "Thread", thread)
    assert sweep.start(Mock()) is None
    thread.assert_not_called()
