"""Human verification is observed, waited for, and never automated."""

import ast
from contextlib import contextmanager
import inspect
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.adapters import tracker_verification as verification
from app.adapters import trackergg_browserstate_source as src
from scripts import ingest_trackergg_player as single
from scripts import refresh_tracked_players as roster


class Clock:
    def __init__(self):
        self.seconds = 0

    def __call__(self):
        return self.seconds


class Page:
    def __init__(self, clock, clear_at=0):
        self.clock = clock
        self.clear_at = clear_at
        self.waits = []

    def title(self):
        return "JuSt A MoMeNt..." if self.clock() < self.clear_at else "Tracker"

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        self.clock.seconds += milliseconds / 1000


@pytest.mark.parametrize("clear_at, waited, alerts", [(0, 0, []), (130, 130, [0, 60, 120])])
def test_wait_for_owner(clear_at, waited, alerts, capsys):
    clock = Clock()
    page = Page(clock, clear_at)
    heard = []
    assert verification.wait_for_owner(
        page, clock=clock, alert=lambda: heard.append(clock())
    ) == waited
    assert heard == alerts
    assert page.waits == [1000] * waited
    assert len(capsys.readouterr().out.splitlines()) == (1 if clear_at else 0)


def test_verification_times_out_at_fifteen_minutes():
    clock = Clock()
    page = Page(clock, float("inf"))
    heard = []
    with pytest.raises(verification.TrackerVerificationTimeout):
        verification.wait_for_owner(page, clock=clock, alert=lambda: heard.append(clock()))
    assert clock() == verification.VERIFY_TIMEOUT_S == 900
    assert heard == list(range(0, 900, 60))


def test_wait_supports_injected_pause_and_timeout():
    clock = Clock()
    page = Page(clock, float("inf"))
    def pause(milliseconds):
        clock.seconds += milliseconds / 1000
    with pytest.raises(verification.TrackerVerificationTimeout):
        verification.wait_for_owner(page, timeout_s=2, clock=clock, pause=pause, alert=lambda: None)
    assert clock() == 2
    assert page.waits == []


def test_absent_or_broken_title_is_not_verification():
    def broken():
        raise RuntimeError("closed page")
    for page in (object(), SimpleNamespace(title=broken), SimpleNamespace(title=lambda: None)):
        assert not verification.is_verification_page(page)


@pytest.mark.parametrize("flags,beeps", [((), 1), ((".muted",), 1), ((".tracker-muted",), 0),
                                          ((".muted", ".tracker-muted"), 0)])
def test_beep_has_its_own_mute_apart_from_claudes(monkeypatch, tmp_path, flags, beeps):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in flags:
        flag = tmp_path / ".claude" / "sounds" / name
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
    sound = SimpleNamespace(Beep=MagicMock())
    monkeypatch.setitem(sys.modules, "winsound", sound)
    monkeypatch.setattr(verification.sys, "platform", "win32")
    verification.beep()
    assert sound.Beep.call_count == beeps


def test_beep_is_noop_off_windows(monkeypatch):
    sound = SimpleNamespace(Beep=MagicMock())
    monkeypatch.setitem(sys.modules, "winsound", sound)
    monkeypatch.setattr(verification.sys, "platform", "linux")
    verification.beep()
    sound.Beep.assert_not_called()


def test_verification_module_never_interacts_with_challenge():
    tree = ast.parse(inspect.getsource(verification))
    forbidden = {"click", "bring_to_front", "evaluate", "locator"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            assert name not in forbidden


class MatchPage(Page):
    def __init__(self, clock, body, clear_at=130, response_delay=12):
        super().__init__(clock, clear_at)
        self.body = body
        self.response_at = clear_at + response_delay
        self.listeners = []
        self.emitted = False

    def on(self, event, callback):
        assert event == "response"
        self.listeners.append(callback)

    def remove_listener(self, event, callback):
        self.listeners.remove(callback)

    def goto(self, url, **kwargs):
        self.url = url

    def wait_for_timeout(self, milliseconds):
        super().wait_for_timeout(milliseconds)
        if not self.emitted and self.clock() >= self.response_at:
            self.emitted = True
            self.emit(self.body, 403 if "data" not in self.body else 200)

    def emit(self, body, status):
        def json_body():
            if isinstance(body, Exception):
                raise body
            return body
        response = SimpleNamespace(
            url=src.MATCH_API_MARKER_TMPL.format(match_id="m1"),
            status=status,
            json=json_body,
        )
        for listener in self.listeners[:]:
            listener(response)


@pytest.fixture
def fake_wait(monkeypatch):
    monkeypatch.setattr(src, "wait_for_owner", lambda page: verification.wait_for_owner(
        page, clock=page.clock, alert=lambda: None
    ))


def test_match_response_after_verification_is_captured(fake_wait):
    page = MatchPage(Clock(), {"data": {"segments": []}})
    assert src.fetch_match_json(page, "m1") == {"segments": []}
    assert page.clock() == 142
    assert page.listeners == []


@pytest.mark.parametrize("blocked_body, status", [
    ({"errors": [{"message": "verification required"}]}, 403),
    (ValueError("HTML response is not JSON"), 429),
    (["unexpected JSON array"], 403),
])
def test_blocked_match_response_does_not_hide_good_response_after_clear(fake_wait, blocked_body, status):
    class BlockedMatchPage(MatchPage):
        def goto(self, url, **kwargs):
            super().goto(url, **kwargs)
            assert verification.is_verification_page(self)
            self.emit(blocked_body, status)
    page = BlockedMatchPage(Clock(), {"data": {"segments": []}})
    assert src.fetch_match_json(page, "m1") == {"segments": []}
    assert page.clock() == 142
    assert page.listeners == []


def test_first_good_match_response_is_kept(fake_wait):
    class RepeatedMatchPage(MatchPage):
        def goto(self, url, **kwargs):
            super().goto(url, **kwargs)
            self.emit({"data": {"first": True}}, 200)
            self.emit({"data": {"replacement": True}}, 200)
            self.emit(ValueError("later invalid JSON"), 429)
    page = RepeatedMatchPage(Clock(), {}, clear_at=0)
    assert src.fetch_match_json(page, "m1") == {"first": True}
    assert page.listeners == []


def test_match_body_without_data_names_status(fake_wait):
    page = MatchPage(Clock(), {"errors": [{"message": "unavailable"}]}, clear_at=0)
    with pytest.raises(RuntimeError, match="403"):
        src.fetch_match_json(page, "m1")
    assert page.listeners == []


def test_only_html_match_response_names_status(fake_wait):
    class HTMLMatchPage(MatchPage):
        def goto(self, url, **kwargs):
            super().goto(url, **kwargs)
            self.emit(ValueError("HTML response is not JSON"), 429)
    page = HTMLMatchPage(Clock(), {}, clear_at=0, response_delay=16)
    with pytest.raises(RuntimeError, match="429"):
        src.fetch_match_json(page, "m1")
    assert page.clock() == 15
    assert page.listeners == []


def test_match_body_wait_is_bounded(fake_wait):
    page = MatchPage(Clock(), {"data": {}}, clear_at=0, response_delay=16)
    with pytest.raises(RuntimeError, match="Did not observe"):
        src.fetch_match_json(page, "m1")
    assert page.clock() == 15
    assert page.listeners == []


@pytest.mark.parametrize("with_check", [True, False])
def test_load_more_waits_for_response_after_verification_clear(fake_wait, with_check):
    def payload(match_id, cursor):
        return {"matches": [{"attributes": {"id": match_id}, "metadata": {"modeName": "Competitive"}}],
                "metadata": {"next": cursor}}

    class HistoryPage(Page):
        def __init__(self):
            super().__init__(Clock())
            self.listeners = []
            self.pending = False
            self.clicks = 0

        def on(self, event, callback):
            self.listeners.append(callback)

        def remove_listener(self, event, callback):
            self.listeners.remove(callback)

        def goto(self, url, **kwargs):
            pass

        def evaluate(self, script):
            assert not verification.is_verification_page(self)
            return {"stats": {"standardProfileMatches": [payload("m1", 1)]}}

        def get_by_role(self, role, name):
            control = MagicMock()
            control.count.return_value = 1
            control.first.is_visible.return_value = True
            control.first.is_enabled.return_value = True
            control.first.click.side_effect = self.load_more
            return control

        def load_more(self):
            self.clicks += 1
            self.pending = True
            if with_check:
                self.clear_at = self.clock() + 130
            else:
                self.emit()

        def emit(self):
            self.pending = False
            response = SimpleNamespace(url=src.HISTORY_API_MARKER + "placeholder",
                                       json=lambda: {"data": payload("m2", None)})
            for listener in self.listeners[:]:
                listener(response)

        def wait_for_timeout(self, milliseconds):
            super().wait_for_timeout(milliseconds)
            if self.pending and self.clock() >= self.clear_at + 5:
                self.emit()

    page = HistoryPage()
    result = src.discover_match_ids_paginated(page, "profile-placeholder", 2)
    assert result.status is src.DiscoveryStatus.COMPLETE
    assert result.match_ids == ["m1", "m2"]
    assert page.clicks == 1 and page.listeners == []
    assert page.waits.count(5000) == (2 if with_check else 1)


@pytest.mark.parametrize("entry", [src._fetch_history_state, src.discover_match_ids_paginated, src.fetch_match_json])
def test_page_load_timeout_propagates_and_detaches_listener(monkeypatch, entry):
    page = MagicMock()
    timeout = verification.TrackerVerificationTimeout("owner did not clear check")
    monkeypatch.setattr(src, "wait_for_owner", MagicMock(side_effect=timeout))
    args = (page, "profile-placeholder", 3) if entry is src.discover_match_ids_paginated else (page, "m1")
    with pytest.raises(verification.TrackerVerificationTimeout):
        entry(*args)
    page.goto.assert_called_once()
    page.evaluate.assert_not_called()
    if entry is not src._fetch_history_state:
        page.remove_listener.assert_called_once()


def test_ingest_timeout_keeps_first_match_without_retry_or_skip(monkeypatch):
    db = MagicMock()
    db.query.return_value.filter_by.return_value.one_or_none.return_value = None
    ledger = MagicMock()
    timeout = verification.TrackerVerificationTimeout("owner did not clear check")
    fetch = MagicMock(side_effect=[{"match": "m1"}, timeout, {"match": "m3"}])
    monkeypatch.setattr(src, "fetch_match_json", fetch)
    monkeypatch.setattr(src, "verify_ingest_preflight", lambda db: None)
    monkeypatch.setattr(src, "load_match", lambda db, payload: SimpleNamespace(id=1, map_name="Haven"))
    monkeypatch.setattr(src, "invalidate_match_players", lambda db, mid: {7})
    monkeypatch.setattr(src, "invalidate_site_stats_cache", lambda db: None)
    monkeypatch.setattr(src, "compute_impact_for_match", lambda db, mid: None)
    monkeypatch.setattr(src.time, "sleep", lambda seconds: None)
    progress = src._ingest_discovered(db, None, ["m1", "m2", "m3"], ledger)
    assert [call.args[1] for call in fetch.call_args_list] == ["m1", "m2"]
    assert progress.ingested == 1 and progress.dirty == {7}
    assert progress.verification_timeout and progress.error is timeout
    assert progress.failed_ids == [] and progress.first_error is None
    ledger.record_match.assert_called_once()
    ledger.record_failure.assert_not_called()


@pytest.mark.parametrize("during_discovery", [True, False])
def test_outcome_carries_verification_timeout(monkeypatch, during_discovery):
    timeout = verification.TrackerVerificationTimeout("owner did not clear check")
    result = src.DiscoveryResult("profile-placeholder", 3, ["m1"], src.DiscoveryStatus.COMPLETE)
    discover = MagicMock(side_effect=timeout) if during_discovery else MagicMock(return_value=result)
    monkeypatch.setattr(src, "discover_match_ids_paginated", discover)
    ingest = MagicMock(return_value=src._IngestProgress(
        dirty={7}, ingested=1, error=timeout, verification_timeout=True
    ))
    monkeypatch.setattr(src, "_ingest_discovered", ingest)
    dirty, outcome = src.ingest_paginated_history(None, None, "profile-placeholder", 3)
    assert outcome.verification_timeout and not outcome.ok
    assert "TrackerVerificationTimeout" in outcome.error
    if during_discovery:
        assert outcome.discovery.status is src.DiscoveryStatus.INCOMPLETE
        assert dirty == set()
        ingest.assert_not_called()
    else:
        assert dirty == {7} and outcome.ingested == 1


@pytest.mark.parametrize("module", [roster, single], ids=["roster", "single"])
def test_command_timeout_stops_roster_but_finishes_run(module, monkeypatch, tmp_path, capsys):
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(["profile-placeholder-1", "profile-placeholder-2"]))
    if module is roster:
        monkeypatch.setattr(module, "ROSTER_PATH", path)
    db, page = MagicMock(), MagicMock()
    factory = MagicMock(return_value=db)
    monkeypatch.setattr(module, "SessionLocal", factory)
    @contextmanager
    def browser():
        yield SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda url: SimpleNamespace(
            contexts=[SimpleNamespace(new_page=lambda: page)])))
    monkeypatch.setattr(module, "sync_playwright", browser)
    monkeypatch.setattr(module, "backfill_unscored_matches", lambda db: {8})
    if module is roster:
        sleep = MagicMock()
        monkeypatch.setattr(module.time, "sleep", sleep)
    end_steps = {}
    for name in ("prewarm_player_cache", "refresh_site_stats", "prewarm_viewer_site_stats", "link_pending_replays"):
        end_steps[name] = MagicMock(return_value=0)
        monkeypatch.setattr(module, name, end_steps[name])
    def ingest(db, page, name, count, ledger):
        ledger.record_match(SimpleNamespace(id=1, external_id="m1", map_name="Haven", played_at=None), name)
        return {7}, src.IngestOutcome(
            src.DiscoveryResult(name, count, ["m1", "m2"], src.DiscoveryStatus.COMPLETE),
            ingested=1, attempted=2, error="TrackerVerificationTimeout", verification_timeout=True,
        )
    fetch = MagicMock(side_effect=ingest)
    monkeypatch.setattr(module, "ingest_paginated_history", fetch)
    ledger_path = tmp_path / "run.jsonl"
    code = (module.main(3, False, ledger_path) if module is roster else
            module.main("profile-placeholder-1", 3, False, ledger_path))
    assert code == 2
    fetch.assert_called_once()
    if module is roster:
        sleep.assert_not_called()
    end_steps["prewarm_player_cache"].assert_called_once_with(db, {7, 8})
    end_steps["refresh_site_stats"].assert_called_once_with(db)
    end_steps["prewarm_viewer_site_stats"].assert_called_once_with(db)
    end_steps["link_pending_replays"].assert_called_once_with(factory)
    db.close.assert_called_once()
    page.close.assert_called_once()
    end = json.loads(ledger_path.read_text().splitlines()[-1])
    assert end["event"] == "run_end" and end["ingested"] == 1
    output = capsys.readouterr().out.lower()
    assert "verification" in output and "re-run" in output


# --- R1.1 F6: the settle wait after a load is at least 8 s ------------------------


@pytest.mark.parametrize("arrives_at, expected_total", [(0, 8), (3, 8), (8, 8), (12, 12), (15, 15)])
def test_match_body_waits_at_least_eight_seconds_then_up_to_fifteen(fake_wait, arrives_at, expected_total):
    class Arriving(MatchPage):
        def goto(self, url, **kwargs):
            super().goto(url, **kwargs)
            if arrives_at == 0:
                self.emit({"data": {"ok": True}}, 200)

    page = Arriving(Clock(), {"data": {"ok": True}}, clear_at=0, response_delay=arrives_at)
    page.emitted = arrives_at == 0
    assert src.fetch_match_json(page, "m1") == {"ok": True}
    assert sum(page.waits) == expected_total * 1000
    assert set(page.waits) == {1000}


def test_match_body_wait_starts_after_the_verification_wait(fake_wait):
    page = MatchPage(Clock(), {"data": {"ok": True}}, clear_at=130, response_delay=0)
    assert src.fetch_match_json(page, "m1") == {"ok": True}
    assert page.clock() == 130 + 8


# --- R1.1 F3: no database transaction while the ingest loop fetches ---------------


def test_ingest_loop_holds_no_transaction_during_any_fetch_attempt(monkeypatch):
    from app.models.match import MatchSource
    from tests.buy_disruption_fixtures import session

    db = session(all_tables=True)
    states = []

    def fetch(page, match_id):
        states.append((match_id, db.in_transaction()))
        if match_id == "m1" and len(states) == 1:
            raise RuntimeError("synthetic dropped connection")
        return {"id": match_id}

    def load(db, payload):
        match = src.Match(external_id=payload["id"], source=MatchSource.SCRAPED, map_name="Haven")
        db.add(match)
        db.commit()
        return match

    monkeypatch.setattr(src, "fetch_match_json", fetch)
    monkeypatch.setattr(src, "verify_ingest_preflight", lambda db: None)
    monkeypatch.setattr(src, "load_match", load)
    monkeypatch.setattr(src, "invalidate_match_players", lambda db, mid: {mid})
    monkeypatch.setattr(src, "invalidate_site_stats_cache", lambda db: None)
    monkeypatch.setattr(src, "compute_impact_for_match", lambda db, mid: db.commit())
    monkeypatch.setattr(src.time, "sleep", lambda seconds: None)
    try:
        progress = src._ingest_discovered(db, None, ["m1", "m2", "m3"])
    finally:
        db.close()
        db.bind.dispose()
    assert progress.ingested == 3 and progress.error is None
    assert states == [("m1", False), ("m1", False), ("m2", False), ("m3", False)]


def test_ingest_loop_carries_on_when_the_commit_before_a_fetch_fails(monkeypatch):
    db = MagicMock()
    db.query.return_value.filter_by.return_value.one_or_none.return_value = None
    db.commit.side_effect = [RuntimeError("synthetic dropped connection")] + [None] * 20
    fetch = MagicMock(side_effect=[{"match": "m1"}, {"match": "m2"}])
    monkeypatch.setattr(src, "fetch_match_json", fetch)
    monkeypatch.setattr(src, "verify_ingest_preflight", lambda db: None)
    monkeypatch.setattr(src, "load_match", lambda db, payload: SimpleNamespace(id=1, map_name="Haven"))
    monkeypatch.setattr(src, "invalidate_match_players", lambda db, mid: {7})
    monkeypatch.setattr(src, "invalidate_site_stats_cache", lambda db: None)
    monkeypatch.setattr(src, "compute_impact_for_match", lambda db, mid: None)
    monkeypatch.setattr(src.time, "sleep", lambda seconds: None)
    progress = src._ingest_discovered(db, None, ["m1", "m2"])
    assert fetch.call_count == 2 and progress.ingested == 2 and progress.error is None
    db.rollback.assert_called()


# --- R1.1 F4: a timeout carries the players already invalidated -------------------


def test_timeout_has_its_own_empty_dirty_set():
    first, second = verification.TrackerVerificationTimeout(), verification.TrackerVerificationTimeout("x")
    assert first.dirty == set() and second.dirty == set()
    first.dirty.add(1)
    assert second.dirty == set()


def test_dedup_and_ingest_hands_dirty_players_to_the_timeout(monkeypatch):
    timeout = verification.TrackerVerificationTimeout("owner did not clear check")
    monkeypatch.setattr(src, "_ingest_discovered", lambda *a, **k: src._IngestProgress(
        dirty={7, 9}, ingested=2, error=timeout, verification_timeout=True))
    with pytest.raises(verification.TrackerVerificationTimeout) as raised:
        src._dedup_and_ingest(MagicMock(), None, ["m1", "m2", "m3"])
    assert raised.value is timeout and raised.value.dirty == {7, 9}


def test_dedup_and_ingest_other_failures_and_success_are_unchanged(monkeypatch):
    failure = RuntimeError("boom")
    progress = src._IngestProgress(dirty={7}, ingested=1, first_error=failure)
    monkeypatch.setattr(src, "_ingest_discovered", lambda *a, **k: progress)
    with pytest.raises(RuntimeError) as raised:
        src._dedup_and_ingest(MagicMock(), None, ["m1"])
    assert raised.value is failure and not hasattr(failure, "dirty")
    progress.first_error = None
    assert src._dedup_and_ingest(MagicMock(), None, ["m1"]) == {7}
