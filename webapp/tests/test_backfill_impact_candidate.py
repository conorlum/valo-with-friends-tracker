"""The maintenance-window backfill: refusal, interruption, recovery, acceptance.

Plan section 6 and plan-review finding P1: an interrupted multi-match rescore
must never leave caches built from mixed versions, a concurrent ingest must not
race the declared match set, and acceptance requires every declared match to
equal its frozen replay -- not merely that no exception was raised.
"""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import scripts.backfill_impact_candidate as backfill
from app.db import Base
from app.models import ImpactScore, Player
from app.models.player_view_cache import PlayerViewCache
from app.models.site_stats_cache import SiteStatsCache
from app.scoring import impact, impact_runtime
from app.scoring.impact import build_impact_rows_for_match, compute_impact_for_match
from app.scoring.impact_manifest import build_manifest, config_from_manifest, lf_sha256, write_manifest
from tests.buy_disruption_fixtures import build_match

B_BROKE_NEXT = {8: {f"B{i}": 0 for i in range(1, 6)}}


def _quiet(*_):
    pass


def _manifest(candidate_id="bf-test", activation_version=None):
    if activation_version is None:
        activation_version = impact.IMPACT_CALCULATION_VERSION + 1
    return build_manifest(candidate_id=candidate_id, created="2026-09-11", scorer_revision="test",
                          activation_impact_calculation_version=activation_version,
                          source_snapshots={"matches": {}})


@pytest.fixture
def world(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'site.sqlite'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    matches = []
    for i in range(3):
        match, _, _ = build_match(db, f"bf{i}", kills={7: [("A1", "B1", 10.0), ("A2", "B2", 20.0)]},
                                  loadouts=B_BROKE_NEXT, remainings=B_BROKE_NEXT)
        compute_impact_for_match(db, match.id)  # the live legacy site before activation
        matches.append(match.id)
    db.close()

    manifest_path = tmp_path / "manifest.json"
    manifest = _manifest()
    write_manifest(manifest_path, manifest)
    impact_runtime.clear_cache()
    monkeypatch.setattr(impact, "IMPACT_CALCULATION_VERSION", manifest["activation_impact_calculation_version"])
    monkeypatch.setattr(impact_runtime, "ACTIVE_MANIFEST", str(manifest_path))
    yield SimpleNamespace(Session=Session, matches=matches, manifest_path=manifest_path,
                          manifest=manifest, state=tmp_path / "state.json")
    impact_runtime.clear_cache()


def run(world, **kwargs):
    kwargs.setdefault("session_checker", lambda db: [])
    kwargs.setdefault("confirm_maintenance_window", True)
    return backfill.run_backfill(world.Session, world.manifest_path, world.state, log=_quiet, **kwargs)


def _seed_caches(db):
    player = db.query(Player).first()
    if not db.query(PlayerViewCache).filter_by(player_id=player.id, scope="career").count():
        db.add(PlayerViewCache(player_id=player.id, scope="career", data={}, version=1))
    db.merge(SiteStatsCache(id=1, data={}, version=1))
    db.commit()


def _cache_rows(world):
    db = world.Session()
    try:
        return db.query(PlayerViewCache).count() + db.query(SiteStatsCache).count()
    finally:
        db.close()


def _persisted_equals_candidate(world, match_id):
    db = world.Session()
    try:
        config = config_from_manifest(world.manifest)
        expected = backfill.result_rows(build_impact_rows_for_match(db, match_id, **config.build_kwargs()))
        return backfill.persisted_rows(db, match_id) == expected
    finally:
        db.close()


# ---- refusals ----------------------------------------------------------------------------

def test_refuses_without_the_maintenance_confirmation(world):
    with pytest.raises(backfill.BackfillRefused, match="maintenance"):
        run(world, confirm_maintenance_window=False)
    assert not world.state.exists()


def test_refuses_while_another_process_is_connected(world):
    with pytest.raises(backfill.BackfillRefused, match="uvicorn"):
        run(world, session_checker=lambda db: ["pid 7 app='uvicorn'"])
    assert not any(_persisted_equals_candidate(world, m) for m in world.matches)


def test_refuses_a_manifest_that_is_not_the_active_runtime_configuration(world, monkeypatch):
    monkeypatch.setattr(impact_runtime, "ACTIVE_MANIFEST", None)
    with pytest.raises(backfill.BackfillRefused, match="active runtime"):
        run(world)


# ---- interruption and recovery -------------------------------------------------------------

def test_interrupted_backfill_records_progress_clears_caches_and_resumes(world):
    db = world.Session()
    _seed_caches(db)
    db.close()
    failing = {world.matches[1]}

    def flaky(db, match_id, config):
        if match_id in failing:
            raise RuntimeError("worker crashed")
        compute_impact_for_match(db, match_id, config=config)
        _seed_caches(db)  # a stray request repopulating caches from a mixed table

    state = run(world, compute=flaky)
    assert state["status"] == "incomplete"
    assert state["attempted"] == world.matches
    assert state["succeeded"] == [world.matches[0], world.matches[2]]
    assert list(state["failed"]) == [str(world.matches[1])]
    assert "worker crashed" in state["failed"][str(world.matches[1])]
    assert json.loads(world.state.read_text()) == state
    assert _cache_rows(world) == 0
    assert not _persisted_equals_candidate(world, world.matches[1])  # still legacy: mixed, flagged

    failing.clear()
    state = run(world, compute=flaky)
    assert state["status"] == "scored"
    assert sorted(state["succeeded"]) == world.matches
    assert state["failed"] == {}
    assert all(_persisted_equals_candidate(world, m) for m in world.matches)
    assert _cache_rows(world) == 0


def test_a_session_connecting_mid_run_interrupts_the_backfill(world, monkeypatch):
    monkeypatch.setattr(backfill, "SESSION_CHECK_EVERY", 1)
    calls = iter([[], ["pid 9 app='ingest'"]])
    with pytest.raises(backfill.BackfillRefused, match="mid-run"):
        run(world, session_checker=lambda db: next(calls))
    state = json.loads(world.state.read_text())
    assert state["status"] == "interrupted"
    assert state["succeeded"] == [world.matches[0]]
    assert _cache_rows(world) == 0


def test_resume_under_a_different_manifest_is_refused(world):
    run(world, compute=lambda db, match_id, config: (_ for _ in ()).throw(RuntimeError("x")))
    write_manifest(world.manifest_path, _manifest(
        candidate_id="a-different-candidate",
        activation_version=world.manifest["activation_impact_calculation_version"]))
    impact_runtime.clear_cache()
    with pytest.raises(backfill.BackfillRefused, match="different manifest"):
        run(world)


def test_a_match_ingested_during_the_window_is_refused_on_resume(world):
    def fail_second(db, match_id, config):
        if match_id == world.matches[1]:
            raise RuntimeError("x")
        compute_impact_for_match(db, match_id, config=config)

    assert run(world, compute=fail_second)["status"] == "incomplete"
    db = world.Session()
    build_match(db, "late-ingest", kills={7: [("A1", "B1", 10.0)]})
    db.close()
    with pytest.raises(backfill.BackfillRefused, match="match set changed"):
        run(world)


# ---- acceptance ----------------------------------------------------------------------------

def test_a_match_left_with_stale_or_missing_rows_fails_acceptance(world):
    def partial(db, match_id, config):
        if match_id == world.matches[1]:
            return  # "succeeds" but leaves the legacy rows in place
        compute_impact_for_match(db, match_id, config=config)
        if match_id == world.matches[2]:
            db.query(ImpactScore).filter(ImpactScore.match_player_id.in_(
                [r.match_player_id for r in db.query(ImpactScore).limit(1)])).delete(synchronize_session=False)
            db.commit()

    state = run(world, compute=partial)
    assert state["status"] == "verification_failed"
    assert set(state["verification"]["replay_differences"]) >= {str(world.matches[1])}
    assert _cache_rows(world) == 0


def test_approved_results_must_match_the_persisted_scores(world, tmp_path):
    db = world.Session()
    config = config_from_manifest(world.manifest)
    rows = backfill.result_rows(build_impact_rows_for_match(db, world.matches[0], **config.build_kwargs()))
    db.close()
    approved = {"manifest_lf_sha256": lf_sha256(world.manifest_path),
                "matches": {str(world.matches[0]): {"rows": rows}}}
    good = tmp_path / "approved.json"
    good.write_text(json.dumps(approved))
    assert run(world, approved_results_path=good)["status"] == "scored"

    key = next(iter(rows))
    approved["matches"][str(world.matches[0])]["rows"][key][0] += 1
    bad = tmp_path / "tampered.json"
    bad.write_text(json.dumps(approved))
    world.state.unlink()
    state = run(world, approved_results_path=bad)
    assert state["status"] == "verification_failed"
    assert state["verification"]["approved_result_differences"]
