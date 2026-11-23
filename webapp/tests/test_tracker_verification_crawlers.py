"""One verification timeout stops each crawler without losing completed work."""

from contextlib import contextmanager
import importlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.adapters.tracker_verification import TrackerVerificationTimeout
from app.adapters.trackergg_browserstate_source import DiscoveryResult, DiscoveryStatus


CRAWLERS = (
    "snowball_1hour", "backfill_to_100", "crawl_map_diversity_data", "catchup_inventory",
)
MESSAGE = (
    "Human verification timed out. Clear the check in the browser, "
    "then re-run the same command to resume; completed matches are kept."
)


@pytest.fixture(params=CRAWLERS)
def crawler(request, monkeypatch, tmp_path):
    # These standalone scripts configure terminal streams at import time.
    with monkeypatch.context() as streams:
        for stream in (sys.stdout, sys.stderr):
            streams.setattr(stream, "reconfigure", lambda **kwargs: None, raising=False)
        module = importlib.import_module(f"scripts.{request.param}")
    db, page = MagicMock(), MagicMock()
    context = SimpleNamespace(pages=[], new_page=lambda: page)
    browser = SimpleNamespace(contexts=[context])

    @contextmanager
    def playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda url: browser))

    monkeypatch.setattr(module, "sync_playwright", playwright)
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    sleep = MagicMock()
    monkeypatch.setattr(module.time, "sleep", sleep)
    end_steps = {}
    for name in ("prewarm_player_cache", "refresh_site_stats", "prewarm_viewer_site_stats"):
        if hasattr(module, name):
            end_steps[name] = MagicMock(return_value=0)
            monkeypatch.setattr(module, name, end_steps[name])
    if request.param == "snowball_1hour":
        monkeypatch.setattr(module, "total_match_count", lambda db: 0)
    elif request.param == "backfill_to_100":
        monkeypatch.setattr(module, "match_count", lambda db, name: 0)
    elif request.param == "crawl_map_diversity_data":
        monkeypatch.setattr(module, "_enqueue_new_opponents", lambda db, state: 0)
    else:
        db.execute.return_value.all.return_value = []
    return SimpleNamespace(
        module=module, name=request.param, db=db, page=page, sleep=sleep,
        end_steps=end_steps, state=tmp_path / "state.json", out=tmp_path / "inventory.json",
        roster=tmp_path / "roster.json", boundary=tmp_path / "boundary.tsv",
    )


def run_crawler(crawler, monkeypatch, players, timeout_at=None):
    module = crawler.module
    crawler.state.write_text(json.dumps({"crawled": [], "queue": players}))
    crawler.roster.write_text(json.dumps(players))
    boundary = {name: {"external_id": "boundary-match"} for name in players}
    calls = []

    def load(*args, **kwargs):
        name = args[1] if crawler.name == "catchup_inventory" else args[2]
        calls.append(name)
        if name == timeout_at:
            raise TrackerVerificationTimeout("owner did not clear check")
        if crawler.name == "catchup_inventory":
            return DiscoveryResult(
                name, args[2], ["new-match", "boundary-match"], DiscoveryStatus.COMPLETE,
            )
        return {7}

    if crawler.name == "snowball_1hour":
        monkeypatch.setattr(module, "STATE_PATH", crawler.state)
        monkeypatch.setattr(module, "ingest_recent_matches", load)
        invoke = lambda: module.main(1)
    elif crawler.name == "backfill_to_100":
        monkeypatch.setattr(module, "TARGETS", players)
        monkeypatch.setattr(module, "ingest_full_history", load)
        invoke = module.main
    elif crawler.name == "crawl_map_diversity_data":
        monkeypatch.setattr(module, "ingest_full_history", load)
        invoke = lambda: module.main(None, None, None, crawler.state)
    else:
        monkeypatch.setattr(module, "ROSTER_PATH", crawler.roster)
        monkeypatch.setattr(module, "load_boundary", lambda path: boundary)
        monkeypatch.setattr(module, "discover_match_ids_paginated", load)
        invoke = lambda: module.main(crawler.boundary, crawler.out, set())
    try:
        code = invoke()
    except SystemExit as exc:
        code = exc.code
    return code, calls


def test_first_player_timeout_stops_run(crawler, monkeypatch, capsys):
    players = ["profile-placeholder-1", "profile-placeholder-2"]
    code, calls = run_crawler(crawler, monkeypatch, players, timeout_at=players[0])
    assert calls == players[:1]
    assert isinstance(code, int) and code != 0
    crawler.sleep.assert_not_called()
    crawler.page.close.assert_called_once()
    crawler.db.close.assert_called_once()
    output = capsys.readouterr().out
    assert MESSAGE in output
    assert "skipping" not in output
    if crawler.name == "crawl_map_diversity_data":
        assert json.loads(crawler.state.read_text()) == {"crawled": [], "queue": players}
    elif crawler.name == "catchup_inventory":
        saved = json.loads(crawler.out.read_text())
        assert saved["complete"] is False
        assert saved["partial"] is True
        assert saved["incomplete_players"] == players[:1]


def test_timeout_preserves_completed_work(crawler, monkeypatch):
    players = ["profile-placeholder-1", "profile-placeholder-2", "profile-placeholder-3"]
    code, calls = run_crawler(crawler, monkeypatch, players, timeout_at=players[1])
    assert calls == players[:2]
    assert isinstance(code, int) and code != 0
    assert crawler.sleep.call_count == 1
    crawler.page.close.assert_called_once()
    crawler.db.close.assert_called_once()
    if crawler.end_steps:
        crawler.end_steps["prewarm_player_cache"].assert_called_once_with(crawler.db, {7})
        crawler.end_steps["refresh_site_stats"].assert_called_once_with(crawler.db)
        crawler.end_steps["prewarm_viewer_site_stats"].assert_called_once_with(crawler.db)
    elif crawler.name == "crawl_map_diversity_data":
        assert json.loads(crawler.state.read_text()) == {
            "crawled": players[:1], "queue": players[1:],
        }
    else:
        saved = json.loads(crawler.out.read_text())
        assert saved["complete"] is False
        assert saved["partial"] is True
        assert saved["results"][0]["riot_id"] == players[0]
        assert saved["results"][0]["status"] == "COMPLETE"
        assert saved["results"][0]["to_ingest"] == ["new-match"]
        assert saved["to_ingest_union"] == ["new-match"]
        assert saved["incomplete_players"] == players[1:2]


def test_timeout_after_ingested_matches_still_prewarms_them(crawler, monkeypatch):
    """R1.1 F4: matches added before the timeout are invalidated, so they are pre-warmed too."""
    if not crawler.end_steps:
        pytest.skip("this crawler runs no pre-warm")
    module = crawler.module
    players = ["profile-placeholder-1", "profile-placeholder-2"]
    prior = TrackerVerificationTimeout("owner did not clear check")
    prior.dirty = {7, 9}

    def load(*args, **kwargs):
        raise prior

    crawler.state.write_text(json.dumps({"crawled": [], "queue": players}))
    if crawler.name == "snowball_1hour":
        monkeypatch.setattr(module, "STATE_PATH", crawler.state)
        monkeypatch.setattr(module, "ingest_recent_matches", load)
        code = module.main(1)
    else:
        monkeypatch.setattr(module, "TARGETS", players)
        monkeypatch.setattr(module, "ingest_full_history", load)
        code = module.main()
    assert code != 0
    crawler.end_steps["prewarm_player_cache"].assert_called_once_with(crawler.db, {7, 9})


def test_successful_run_exits_zero(crawler, monkeypatch):
    players = ["profile-placeholder-1", "profile-placeholder-2"]
    code, calls = run_crawler(crawler, monkeypatch, players)
    assert calls == players
    assert code == 0
    if crawler.name == "catchup_inventory":
        saved = json.loads(crawler.out.read_text())
        assert saved["complete"] is True
        assert saved["partial"] is False
