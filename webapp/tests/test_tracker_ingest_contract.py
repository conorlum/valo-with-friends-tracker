"""T02/T06: durable ingestion and recovery through real loader, gate, scorer and cache deletion."""
import copy
import json
import pytest
from sqlalchemy import text
from app.adapters import trackergg_browserstate_source as source
from app.models import Player, Match, Round, KillEvent, ImpactScore, PlayerViewCache, RoundPlayerStat
from app.models.round_player_spend import RoundPlayerSpend
from app.scoring import impact_runtime, impact
from app.scoring.ingest_preflight import IngestRefused
from app.services.player_views import compute_player_views
from app.services.player_view_cache import store_player_views
from scripts.install_release_write_gate import install
from tests.support.tracker import payload, IDS
from tests.support.runtime import test_manifest


@pytest.fixture
def ingestion(pg_factory, monkeypatch, tmp_path):
    manifest = test_manifest(monkeypatch, tmp_path)
    with pg_factory() as db:
        install(db, state='open', release_id=manifest['candidate_id'], admin_id='coverage-admin', note='scratch tests')
        db.commit()
    monkeypatch.setattr(source.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(source, 'fetch_match_json', lambda page, match_id: payload(match_id))
    return pg_factory


def snapshot(factory):
    with factory() as db:
        return {model.__tablename__: db.query(model).count() for model in
                (Player, Match, Round, KillEvent, RoundPlayerStat, RoundPlayerSpend, ImpactScore, PlayerViewCache)}


def test_real_ingest_persists_runtime_scores_spend_assists_and_deduplicates(ingestion, monkeypatch):
    with ingestion() as db:
        result = source._ingest_discovered(db, None, ['complete'])
        assert result.ingested == 1 and result.error is None
    with ingestion() as fresh:
        m = fresh.query(Match).one()
        assert fresh.query(Player).count() == 10 and fresh.query(Round).count() == 2
        assert fresh.query(KillEvent).count() == 4 and fresh.query(RoundPlayerStat).count() == 20
        assert fresh.query(RoundPlayerSpend).count() == 18
        assert 0 in {s.spent for s in fresh.query(RoundPlayerSpend)}
        calculated = {(r.round_id, r.match_player_id): r for r in impact.build_impact_rows_for_match(
            fresh, m.id, **impact_runtime.active_scoring_config().build_kwargs())}
        stored = fresh.query(ImpactScore).all()
        assert len(stored) == len(calculated) == 20
        for row in stored:
            expected = calculated[(row.round_id, row.match_player_id)]
            assert all(getattr(row, field) == getattr(expected, field) for field in impact.PERSISTED_FIELDS)
        assert {s.scoring_version for s in stored} == {impact.IMPACT_CALCULATION_VERSION}
        assert any(s.trade_credit > 0 for s in stored)
    before = snapshot(ingestion)
    def no_fetch(*a): raise AssertionError('duplicate fetched again')
    monkeypatch.setattr(source, 'fetch_match_json', no_fetch)
    with ingestion() as db:
        again = source._ingest_discovered(db, None, ['complete'])
        assert again.already_present == 1 and again.attempted == 0
    assert snapshot(ingestion) == before


def test_partial_load_rolls_back_and_next_match_on_same_session_succeeds(ingestion, monkeypatch):
    damaged = payload('broken')
    next(s for s in reversed(damaged['segments']) if s['type'] == 'player-round')['stats'].pop('score')
    monkeypatch.setattr(source, 'fetch_match_json', lambda page, key: damaged if key == 'broken' else payload(key))
    with ingestion() as db:
        result = source._ingest_discovered(db, None, ['broken', 'good'])
        assert result.failed_ids == ['broken'] and result.ingested == 1
    with ingestion() as fresh:
        assert [m.external_id for m in fresh.query(Match)] == ['good']
        assert fresh.query(Player).count() == 10


def test_score_failure_retires_stale_caches_and_backfill_repairs_once(ingestion, monkeypatch, tmp_path):
    from app.scoring.ingest_preflight import verify_ingest_preflight
    with ingestion() as db:
        verify_ingest_preflight(db)
        db.add_all([Player(display_name=name) for name in [*IDS, 'Unrelated#TEST']]); db.commit()
        players = db.query(Player).all()
        for p in players:
            views = compute_player_views(db, p, 30, 10)
            store_player_views(db, p.id, {'recent': views, 'career': views, 'recent|agent:Jett': views})
        unrelated = players[-1].id
    real_score = source.compute_impact_for_match
    def failed(*a, **kw): raise RuntimeError('injected scorer failure')
    with monkeypatch.context() as scoped:
        scoped.setattr(source, 'compute_impact_for_match', failed)
        with ingestion() as db:
            result = source._ingest_discovered(db, None, ['stranded'], source.IngestLedger(tmp_path/'ledger.jsonl'))
            assert result.ingested == 1 and 'injected scorer' in str(result.error)
    with ingestion() as fresh:
        assert fresh.query(Match).count() == 1 and fresh.query(ImpactScore).count() == 0
        assert {r.player_id for r in fresh.query(PlayerViewCache)} == {unrelated}
    with ingestion() as db: source.backfill_unscored_matches(db)
    after = snapshot(ingestion)
    assert after['impact_scores'] == 20
    with monkeypatch.context() as scoped:
        scoped.setattr(source, 'compute_impact_for_match', lambda *a, **kw: pytest.fail('backfill repeated scoring'))
        with ingestion() as db: assert source.backfill_unscored_matches(db) == set()
    assert snapshot(ingestion) == after


@pytest.mark.parametrize('refusal', ['closed-gate', 'missing-manifest'])
def test_preflight_refuses_before_any_import_or_cache_write(ingestion, monkeypatch, refusal, tmp_path):
    if refusal == 'closed-gate':
        with ingestion() as db: db.execute(text("UPDATE scoring_gate SET state='closed' WHERE id")); db.commit()
    else: monkeypatch.setattr(impact_runtime, 'ACTIVE_MANIFEST', str(tmp_path/'missing.json'))
    before = snapshot(ingestion)
    with ingestion() as db:
        with pytest.raises(IngestRefused): source._ingest_discovered(db, None, ['must-not-import'])
        db.rollback()
    assert snapshot(ingestion) == before


def test_surrender_padding_is_skipped_even_with_stats_and_kills(ingestion, monkeypatch):
    data = payload('surrender', rounds=3)
    summaries = [s for s in data['segments'] if s['type'] == 'round-summary']
    summaries[-1]['stats']['roundResult']['value'] = source.SURRENDERED_ROUND_RESULT
    monkeypatch.setattr(source, 'fetch_match_json', lambda *a: data)
    with ingestion() as db: assert source._ingest_discovered(db, None, ['surrender']).ingested == 1
    with ingestion() as db:
        assert [r.round_number for r in db.query(Round).order_by(Round.round_number)] == [1, 2]
        assert db.query(RoundPlayerStat).count() == 20 and db.query(KillEvent).count() == 4
