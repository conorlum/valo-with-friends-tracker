"""The evaluator must compute impact components WITHOUT writing them.

This is the regression test for a data-corruption bug: an earlier design
added a use_realized_swing flag directly to compute_impact_for_match,
which commits unconditionally, so an ex-ante evaluation run would have
overwritten every stored score.

Requires a live database; skips cleanly without one.
"""

import pytest

from app.models import ImpactScore, Round
from app.scoring.impact import IMPACT_CALCULATION_VERSION, build_impact_rows_for_match
from app.scoring.impact_runtime import active_scoring_config

# EVERY persisted field, not a subset: the spec asks for field-by-field
# equality, and a drift in e.g. clutch_kill or trade_detail would otherwise
# pass unnoticed while silently changing what the tooling reads.
PERSISTED_FIELDS = (
    "kill_impact", "death_impact", "impact", "damage", "econ_impact",
    "time_impact", "swing_impact", "econ_kill", "econ_death", "clutch_kill",
    "clutch_death", "post_plant_kill", "post_plant_death", "traded_teammate",
    "traded_by_teammate", "trade_detail",
)


class _SpyDB:
    """Wraps a real session and records any write attempt."""

    def __init__(self, inner):
        self.inner = inner
        self.added = []
        self.commits = 0

    def query(self, *a, **kw):
        return self.inner.query(*a, **kw)

    def add(self, obj):
        self.added.append(obj)
        return self.inner.add(obj)

    def commit(self):
        self.commits += 1
        return self.inner.commit()


@pytest.fixture
def db_session():
    # A disposable test database only: this module calls the scorer's
    # committing wrapper, so a fixture that reached `.env` could rescore real
    # matches. See tests/_postgres.py.
    db = postgres_session_or_skip()
    try:
        db.query(ImpactScore.round_id).limit(1).scalar()
    except Exception as exc:  # pragma: no cover - environment dependent
        db.close()
        pytest.skip(f"no database available: {exc}")
    yield db
    db.close()


def _representative_match_ids(db, per_kind: int = 3) -> list[int]:
    """A few regulation matches AND a few overtime ones. Overtime exercises
    the round>24 branches of the swing factor and the side rule, which a
    single arbitrary match would never touch."""
    from sqlalchemy import func

    rows = (
        db.query(Round.match_id, func.max(Round.round_number).label("last"))
        .group_by(Round.match_id)
        .all()
    )
    regulation = [m for m, last in rows if last <= 24][:per_kind]
    overtime = [m for m, last in rows if last > 24][:per_kind]
    if not regulation and not overtime:
        pytest.skip("no matches in the database")
    return regulation + overtime


@pytest.fixture
def db_and_match(db_session):
    ids = _representative_match_ids(db_session, per_kind=1)
    return db_session, ids[0]


@pytest.fixture
def writing_db_and_match():
    """Its own session, asked for as a WRITING one. The test below calls the
    scorer's committing wrapper on a real match, which no `_rehearsal` restore
    may host: the wrapper updates in place, so a rescore there would change no
    row count and quietly invalidate every measurement taken against it."""
    db = postgres_session_or_skip(writes=True)
    try:
        yield db, _representative_match_ids(db, per_kind=1)[0]
    finally:
        db.close()


def test_builder_writes_nothing(db_and_match):
    db, match_id = db_and_match
    spy = _SpyDB(db)
    rows = build_impact_rows_for_match(spy, match_id)
    assert rows, "expected calculated rows"
    assert spy.added == [], "builder must not add ORM objects"
    assert spy.commits == 0, "builder must not commit"


def test_builder_matches_stored_values(db_session):
    """Field-by-field over several regulation AND overtime matches.

    Only against rows stored at the version this code produces. Comparing
    across a version bump is not a drift check: IMPACT_CALCULATION_VERSION is
    bumped exactly when the algorithm changes values for already-scored rounds,
    so rows from an older version are SUPPOSED to differ and every one of them
    would report as `kill_impact drifted`, hiding real drift in noise. A
    database whose scores all predate the running code skips instead, saying
    which versions it found -- see IMPACT_CALCULATION_VERSION's own history
    comment in app/scoring/impact.py.

    And under the ACTIVE configuration, not the builder's defaults. The builder
    does not resolve ACTIVE_MANIFEST -- compute_impact_for_match does, and this
    test deliberately does not call that wrapper because it commits. Left on
    the defaults the builder computes the LEGACY formula (credit off, econ
    component off, legacy weights) while still stamping the module's
    IMPACT_CALCULATION_VERSION, so after activation both sides would read
    version 3, the version filter above would admit the comparison, and a
    correctly loaded rc3 table would fail this test. Matching version labels do
    not imply matching scoring configurations (external review, finding 5)."""
    config = active_scoring_config()
    build_kwargs = config.build_kwargs() if config is not None else {"use_realized_swing": True}
    checked = 0
    seen_versions = set()
    for match_id in _representative_match_ids(db_session):
        rows = build_impact_rows_for_match(db_session, match_id, **build_kwargs)
        stored = {
            (s.round_id, s.match_player_id): s
            for s in db_session.query(ImpactScore)
            .join(ImpactScore.round)
            .filter_by(match_id=match_id)
            .all()
        }
        if not stored:
            continue
        seen_versions.update(s.scoring_version for s in stored.values())
        for row in rows:
            existing = stored[(row.round_id, row.match_player_id)]
            if existing.scoring_version != row.scoring_version:
                continue
            for field in PERSISTED_FIELDS:
                assert getattr(row, field) == getattr(existing, field), (
                    f"{field} drifted for match {match_id} "
                    f"round {row.round_id}/{row.match_player_id}"
                )
            checked += 1
    if not checked and seen_versions:
        pytest.skip(f"stored scores are version {sorted(seen_versions)}, this code writes "
                    f"{IMPACT_CALCULATION_VERSION}: nothing to compare without a rescore")
    assert checked, "no stored scores found to compare against"


import app.scoring.impact as impact_module
from tests._postgres import postgres_session_or_skip


def test_wrapper_still_persists_and_commits(writing_db_and_match, monkeypatch):
    """The spec requires compute_impact_for_match's behaviour be unchanged.
    The builder test proves the CALCULATION matches; this proves the WRAPPER
    still writes -- otherwise the split could silently turn the scorer into a
    no-op and every ingest would stop scoring."""
    from app.scoring.impact import compute_impact_for_match

    db, match_id = writing_db_and_match
    spy = _SpyDB(db)
    before = db.query(ImpactScore).join(ImpactScore.round).filter_by(match_id=match_id).count()
    compute_impact_for_match(spy, match_id)
    assert spy.commits >= 1, "wrapper must commit"
    after = db.query(ImpactScore).join(ImpactScore.round).filter_by(match_id=match_id).count()
    assert after == before, "re-scoring an existing match must update, not duplicate"


def test_the_builder_defaults_are_not_the_active_configuration():
    """Offline. The trap external review finding 5 named.

    `build_impact_rows_for_match` does NOT resolve ACTIVE_MANIFEST -- only the
    committing wrapper does -- yet every row it returns is stamped with the
    module's IMPACT_CALCULATION_VERSION regardless. So a read-only comparison
    left on the builder's defaults computes the LEGACY formula while claiming
    the ACTIVE version, and a correctly loaded rc3 table reads as drift.

    Pin that the two configurations really do differ, on values, so the trap
    cannot go quiet: matching version labels never imply matching scoring.
    """
    import inspect
    from pathlib import Path

    from app.scoring.impact_manifest import config_from_manifest, load_manifest
    from app.scoring.impact_runtime import REPO_ROOT

    defaults = {name: p.default for name, p
                in inspect.signature(build_impact_rows_for_match).parameters.items()}
    manifest = load_manifest(Path(REPO_ROOT) / "docs/superpowers/impact-rc3/candidate-manifest.json")
    rc3 = config_from_manifest(manifest).build_kwargs()

    assert defaults["enable_trade_credit"] is False, "builder default changed"
    assert rc3["enable_trade_credit"] is True, "rc3 pays the trade credit"
    assert defaults["enable_econ_component"] is False, "builder default changed"
    assert rc3["enable_econ_component"] is True, "rc3 scores the econ component"
    assert defaults["weights"] is None and rc3["weights"] is not None, \
        "rc3 carries explicit weights the builder's defaults do not"


def test_ex_ante_never_calls_the_realized_factor(db_and_match, monkeypatch):
    """The direct proof: if the ex-ante path touched round N+1 data, this
    would raise. Deterministic, unlike comparing outputs on a match that
    may happen to have no disagreement."""
    db, match_id = db_and_match

    def _forbidden(*args, **kwargs):
        raise AssertionError("ex-ante path must not read round N+1 data")

    monkeypatch.setattr(impact_module, "_realized_econ_swing_factor", _forbidden)
    rows = build_impact_rows_for_match(db, match_id, use_realized_swing=False)
    assert rows, "expected calculated rows"


def test_realized_path_does_call_the_realized_factor(db_and_match, monkeypatch):
    """Confirms the monkeypatch above actually has teeth -- otherwise the
    ex-ante test would pass even if the flag were ignored."""
    db, match_id = db_and_match
    calls = []
    original = impact_module._realized_econ_swing_factor

    def _counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(impact_module, "_realized_econ_swing_factor", _counting)
    build_impact_rows_for_match(db, match_id, use_realized_swing=True)
    assert calls, "realized path must consult the realized factor"
