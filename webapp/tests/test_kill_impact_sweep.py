"""Kill-impact sweep pacing, cooldown, drain and lifecycle; no database or threads."""

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from app.services import kill_impact_sweep as sweep


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

    def refresh(factory_arg, replay_id):
        assert factory_arg is factory
        db.rollback.assert_called_once_with()
        db.close.assert_called_once_with()
        return "stored"

    return SimpleNamespace(db=db, factory=factory, select=select,
                           refresh=Mock(side_effect=refresh), stop=Stop())


def run_batch(batch, failed_until=None, now=100, **kwargs):
    return sweep.run_pass(batch.factory, batch.stop,
                          {} if failed_until is None else failed_until, now,
                          select=batch.select, refresh=batch.refresh, **kwargs)


def test_order_counts_selection_session_and_between_split_pacing(batch):
    assert run_batch(batch) == {"stored": 3}
    batch.select.assert_called_once_with(batch.db, 5, skip=set())
    assert batch.refresh.call_args_list == [call(batch.factory, i) for i in [9, 8, 7]]
    batch.factory.assert_called_once_with()
    batch.db.rollback.assert_called_once_with()
    batch.db.close.assert_called_once_with()
    assert batch.stop.waits == [0.5, 0.5]


def test_default_select_and_refresh_are_resolved_at_call_time(batch, monkeypatch):
    monkeypatch.setattr(sweep, "pending_replay_ids", batch.select)
    monkeypatch.setattr(sweep.replay_impact, "refresh_replay_impact", batch.refresh)
    assert sweep.run_pass(batch.factory, batch.stop, {}, 100) == {"stored": 3}


def test_failure_cooldown_expires_and_refusal_is_counted(batch):
    failed_until = {}
    batch.refresh.side_effect = ["stored", "failed: refused", "stored"]
    assert run_batch(batch, failed_until) == {"stored": 2, "failed": 1}
    assert failed_until == {8: 100 + 21600}
    batch.select.return_value = []
    assert run_batch(batch, failed_until, now=100 + 3600) == {}
    assert batch.select.call_args == call(batch.db, 5, skip={8})
    assert run_batch(batch, failed_until, now=100 + 21601) == {}
    assert batch.select.call_args == call(batch.db, 5, skip=set())
    assert failed_until == {}


def test_not_linked_has_no_cooldown(batch):
    batch.select.return_value = [9]
    batch.refresh.side_effect = None
    batch.refresh.return_value = "not linked"
    failed_until = {}
    assert run_batch(batch, failed_until) == {"not linked": 1}
    assert failed_until == {}


def test_stop_after_first_refresh(batch):
    def refresh(*args):
        batch.stop.set()
        return "stored"

    batch.refresh.side_effect = refresh
    assert run_batch(batch) == {"stored": 1}
    batch.refresh.assert_called_once_with(batch.factory, 9)


def test_stop_during_pause(batch):
    batch.stop.stop_on_wait = 1
    assert run_batch(batch) == {"stored": 1}
    batch.refresh.assert_called_once_with(batch.factory, 9)
    assert batch.stop.waits == [0.5]


def test_stop_before_first_split(batch):
    batch.stop.set()
    assert run_batch(batch) == {}
    batch.refresh.assert_not_called()


def test_drain_before_selection_keeps_cooldowns_untouched(batch):
    failed_until = {8: 0}
    assert run_batch(batch, failed_until, paused=lambda: True) == {"paused": 1}
    assert failed_until == {8: 0}
    batch.factory.assert_not_called()
    batch.select.assert_not_called()
    batch.refresh.assert_not_called()


def test_drain_after_first_split_does_not_cool_down_remaining_ids(batch):
    failed_until = {}
    paused = Mock(side_effect=[False, False, True])
    assert run_batch(batch, failed_until, paused=paused) == {"stored": 1, "paused": 1}
    batch.refresh.assert_called_once_with(batch.factory, 9)
    assert failed_until == {}
    batch.select.return_value = [8, 7]
    batch.refresh.reset_mock()
    batch.db.reset_mock()
    assert run_batch(batch, failed_until, paused=lambda: False) == {"stored": 2}
    assert batch.select.call_args == call(batch.db, 5, skip=set())
    assert batch.refresh.call_args_list == [call(batch.factory, 8), call(batch.factory, 7)]


def test_selection_error_rolls_back_and_closes(batch):
    batch.select.side_effect = RuntimeError("selection failed")
    with pytest.raises(RuntimeError, match="selection failed"):
        run_batch(batch)
    batch.db.rollback.assert_called_once_with()
    batch.db.close.assert_called_once_with()
    batch.refresh.assert_not_called()


@pytest.fixture
def health(monkeypatch):
    events = Mock()
    for name in ["register", "beat", "loop_ok", "loop_failed", "unregister"]:
        monkeypatch.setattr(sweep.site_health, name, getattr(events, name))
    return events


@pytest.mark.parametrize("raises", [False, True])
def test_cadence_health_and_failure_recovery(monkeypatch, health, raises):
    stop = Stop(stop_on_wait=3)
    error = RuntimeError("split error")
    run_pass = Mock(side_effect=[error if raises else {"stored": 1}, {}])
    monkeypatch.setattr(sweep, "run_pass", run_pass)
    monkeypatch.setattr(sweep.time, "monotonic", lambda: 123)
    factory = Mock()
    sweep.run_forever(factory, stop)
    assert stop.waits == [60, 30, 30]
    assert run_pass.call_count == 2
    for args, kwargs in run_pass.call_args_list:
        assert args[0] is factory and args[1] is stop and args[3] == 123
    assert health.mock_calls == [
        call.register("kill-impact", 30), call.beat("kill-impact", "start"),
        call.loop_failed("kill-impact", error) if raises else call.loop_ok("kill-impact"),
        call.beat("kill-impact", "end"), call.beat("kill-impact", "start"),
        call.loop_ok("kill-impact"), call.beat("kill-impact", "end"),
        call.unregister("kill-impact"),
    ]


def test_stop_in_first_delay_never_runs_a_pass(monkeypatch, health):
    run_pass = Mock()
    monkeypatch.setattr(sweep, "run_pass", run_pass)
    sweep.run_forever(Mock(), Stop(stop_on_wait=1))
    run_pass.assert_not_called()
    assert health.mock_calls == [call.register("kill-impact", 30), call.unregister("kill-impact")]


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


@pytest.mark.parametrize("counts", [{"paused": 1}, {"failed": 1}])
def test_drain_and_refused_split_are_clean_passes(monkeypatch, health, counts):
    monkeypatch.setattr(sweep, "run_pass", Mock(return_value=counts))
    sweep.run_forever(Mock(), Stop(stop_on_wait=2))
    health.loop_ok.assert_called_once_with("kill-impact")
    health.loop_failed.assert_not_called()
