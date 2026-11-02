"""T07: real command mains and ledgers, with only browser/network/work boundaries replaced."""
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from scripts import ingest_trackergg_player as single, refresh_tracked_players as roster
from app.adapters.trackergg_browserstate_source import DiscoveryResult, DiscoveryStatus, IngestOutcome


@pytest.fixture(params=[single, roster], ids=['single', 'roster'])
def command(request, monkeypatch, tmp_path):
    module = request.param
    names = ['One#TEST', 'Private#TEST', 'Broken#TEST', 'Last#TEST'] if module is roster else ['One#TEST']
    path = tmp_path/'roster.json'; path.write_text(json.dumps(names))
    if module is roster: monkeypatch.setattr(module, 'ROSTER_PATH', path)
    session, page = MagicMock(), MagicMock()
    factory = MagicMock(return_value=session)
    monkeypatch.setattr(module, 'SessionLocal', factory)
    @contextmanager
    def browser():
        yield SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda url: SimpleNamespace(
            contexts=[SimpleNamespace(new_page=lambda: page)])))
    monkeypatch.setattr(module, 'sync_playwright', browser)
    if module is roster: monkeypatch.setattr(module.time, 'sleep', lambda delay: None)
    mocks = {}
    for name in ('prewarm_player_cache', 'refresh_site_stats', 'prewarm_viewer_site_stats', 'link_pending_replays'):
        mocks[name] = MagicMock(return_value=0)
        monkeypatch.setattr(module, name, mocks[name])
    monkeypatch.setattr(module, 'backfill_unscored_matches', lambda db: {7})
    ledger = tmp_path/'run.jsonl'
    def invoke(no_prewarm=False):
        return module.main(20, no_prewarm, ledger) if module is roster else module.main(names[0], 20, no_prewarm, ledger)
    return SimpleNamespace(module=module, names=names, db=session, factory=factory, page=page, mocks=mocks,
                           invoke=invoke, ledger=ledger)


def outcome(name, status=DiscoveryStatus.COMPLETE, **kw):
    return IngestOutcome(discovery=DiscoveryResult(name, 20, ['m1'], status=status), **kw)


@pytest.mark.parametrize('no_prewarm', [False, True])
def test_main_aggregates_dirty_ids_refreshes_stats_and_records_end(command, monkeypatch, no_prewarm):
    c = command
    def ingest(db, page, name, count, ledger):
        if name.startswith('Private'): return set(), outcome(name, DiscoveryStatus.PRIVATE)
        if name.startswith('Broken'): raise RuntimeError('synthetic player failure')
        ledger.record_match(SimpleNamespace(id=1, external_id=name, map_name='Bind', played_at=None), name)
        return {2, 7}, outcome(name, attempted=1, ingested=1)
    monkeypatch.setattr(c.module, 'ingest_paginated_history', ingest)
    assert c.invoke(no_prewarm) == (2 if c.module is roster else 0)
    if no_prewarm:
        c.mocks['prewarm_player_cache'].assert_not_called()
        c.mocks['prewarm_viewer_site_stats'].assert_not_called()
    else:
        c.mocks['prewarm_player_cache'].assert_called_once_with(c.db, {2, 7})
        c.mocks['prewarm_viewer_site_stats'].assert_called_once_with(c.db)
    c.mocks['refresh_site_stats'].assert_called_once_with(c.db)
    c.mocks['link_pending_replays'].assert_called_once_with(c.factory)
    c.db.close.assert_called_once(); c.page.close.assert_called_once()
    lines = [json.loads(line) for line in c.ledger.read_text().splitlines()]
    assert lines[-1]['event'] == 'run_end' and lines[-1]['ingested'] == (2 if c.module is roster else 1)


def test_unchanged_run_does_no_cache_work(command, monkeypatch):
    c = command
    monkeypatch.setattr(c.module, 'backfill_unscored_matches', lambda db: set())
    monkeypatch.setattr(c.module, 'ingest_paginated_history', lambda db,page,name,*a: (set(), outcome(name)))
    assert c.invoke() == 0
    for name in ('prewarm_player_cache', 'refresh_site_stats', 'prewarm_viewer_site_stats'):
        c.mocks[name].assert_not_called()


def test_cdp_failure_still_closes_session_and_ends_ledger(command, monkeypatch):
    c = command
    @contextmanager
    def failed(): raise RuntimeError('CDP unavailable'); yield
    monkeypatch.setattr(c.module, 'sync_playwright', failed)
    with pytest.raises(RuntimeError, match='CDP unavailable'): c.invoke()
    c.db.close.assert_called_once()
    assert json.loads(c.ledger.read_text().splitlines()[-1])['event'] == 'run_end'


def test_unexpected_ingest_error_releases_page_and_keeps_truthful_exit(command, monkeypatch, capsys):
    c = command
    def failed(*a): raise RuntimeError('injected error')
    monkeypatch.setattr(c.module, 'ingest_paginated_history', failed)
    if c.module is single:
        with pytest.raises(RuntimeError, match='injected error'): c.invoke()
    else:
        assert c.invoke() == 2
        assert 'unexpected error' in capsys.readouterr().out
    c.page.close.assert_called_once(); c.db.close.assert_called_once()
    assert json.loads(c.ledger.read_text().splitlines()[-1])['event'] == 'run_end'


def test_cache_refresh_failure_still_finishes_ledger_and_closes_database(command, monkeypatch):
    c = command
    monkeypatch.setattr(c.module, 'ingest_paginated_history', lambda db,page,name,*a: ({2}, outcome(name)))
    c.mocks['refresh_site_stats'].side_effect = RuntimeError('refresh failed')
    with pytest.raises(RuntimeError, match='refresh failed'): c.invoke()
    c.db.close.assert_called_once(); c.page.close.assert_called_once()
    assert json.loads(c.ledger.read_text().splitlines()[-1])['event'] == 'run_end'
