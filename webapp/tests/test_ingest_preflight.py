"""A checkout proves it may write before it writes, not after it commits.

Each check here corresponds to a way the September 2026 stranding could have
been caught early: match 3133 committed, its caches were deleted, and only then
did scoring fail because the checkout's ORM knew columns the database did not.
"""

import pytest

from app.scoring import ingest_preflight
from app.scoring.ingest_preflight import (
    IngestRefused,
    check_environment,
    check_gate_and_claim,
    check_readable,
    check_schema,
    check_scoring_configuration,
)
from app.scoring.write_gate import GateState

MANIFEST = {"candidate_id": "impact-rc3", "environment": {"packages": ["numpy==2.4.6"]}}


class _Stub:
    """Just enough session for the checks under test."""

    def __init__(self, *, version="0010", read_error=None):
        self.version = version
        self.read_error = read_error
        self.claimed = None

    def execute(self, *_args, **_kwargs):
        stub = self

        class _Result:
            def scalar(self_inner):
                return stub.version

        return _Result()

    def query(self, _model):
        stub = self

        class _Query:
            def limit(self_inner, _n):
                return self_inner

            def all(self_inner):
                if stub.read_error:
                    raise stub.read_error
                return []

        return _Query()


def test_a_database_behind_this_checkout_is_refused(monkeypatch):
    monkeypatch.setattr(ingest_preflight, "_migration_head", lambda: "0010")
    with pytest.raises(IngestRefused) as caught:
        check_schema(_Stub(version="0009"))
    assert "0009" in str(caught.value) and "0010" in str(caught.value)


def test_a_matching_migration_head_passes(monkeypatch):
    monkeypatch.setattr(ingest_preflight, "_migration_head", lambda: "0010")
    check_schema(_Stub(version="0010"))


def test_an_unreadable_scores_table_is_refused():
    """Exactly the 3133 failure, moved before the first commit."""
    with pytest.raises(IngestRefused) as caught:
        check_readable(_Stub(read_error=RuntimeError("column impact_scores.damage does not exist")))
    assert "impact_scores" in str(caught.value)
    assert "already committed" in str(caught.value)


def test_no_active_manifest_is_refused(monkeypatch):
    from app.scoring import impact_runtime

    monkeypatch.setattr(impact_runtime, "active_manifest", lambda: None)
    monkeypatch.setattr(impact_runtime, "active_scoring_config", lambda: None)
    with pytest.raises(IngestRefused) as caught:
        check_scoring_configuration()
    assert "ACTIVE_MANIFEST is None" in str(caught.value)


def test_a_manifest_that_does_not_verify_is_refused(monkeypatch):
    from app.scoring import impact_runtime

    def _raise():
        raise RuntimeError("source digest differs")

    monkeypatch.setattr(impact_runtime, "active_manifest", _raise)
    with pytest.raises(IngestRefused) as caught:
        check_scoring_configuration()
    assert "does not verify" in str(caught.value)


def test_environment_drift_is_refused(monkeypatch):
    monkeypatch.setattr(ingest_preflight, "_installed_packages", lambda: ["numpy==2.4.7"])
    with pytest.raises(IngestRefused) as caught:
        check_environment(MANIFEST)
    assert "frozen with" in str(caught.value)


def test_a_matching_environment_passes(monkeypatch):
    monkeypatch.setattr(ingest_preflight, "_installed_packages", lambda: ["numpy==2.4.6"])
    check_environment(MANIFEST)


def test_a_manifest_without_a_recorded_environment_is_not_blocked():
    check_environment({"candidate_id": "impact-rc3"})


def test_a_missing_gate_is_refused(monkeypatch):
    monkeypatch.setattr(ingest_preflight, "read_gate", lambda _db: None)
    with pytest.raises(IngestRefused) as caught:
        check_gate_and_claim(_Stub(), MANIFEST)
    assert "not installed" in str(caught.value)


def test_a_closed_gate_is_refused(monkeypatch):
    monkeypatch.setattr(ingest_preflight, "read_gate",
                        lambda _db: GateState("closed", "impact-rc3", "rc3-runbook", "hold"))
    with pytest.raises(IngestRefused) as caught:
        check_gate_and_claim(_Stub(), MANIFEST)
    assert "frozen" in str(caught.value)


def test_a_gate_open_for_another_release_is_refused(monkeypatch):
    monkeypatch.setattr(ingest_preflight, "read_gate",
                        lambda _db: GateState("open", "impact-rc4", "rc3-runbook", None))
    with pytest.raises(IngestRefused) as caught:
        check_gate_and_claim(_Stub(), MANIFEST)
    assert "impact-rc4" in str(caught.value) and "impact-rc3" in str(caught.value)


def test_an_open_gate_claims_the_identity(monkeypatch):
    claimed = {}
    monkeypatch.setattr(ingest_preflight, "read_gate",
                        lambda _db: GateState("open", "impact-rc3", "rc3-runbook", None))
    monkeypatch.setattr(ingest_preflight, "install_write_identity",
                        lambda _db, identity: claimed.setdefault("identity", identity))
    assert check_gate_and_claim(_Stub(), MANIFEST) == "impact-rc3"
    assert claimed["identity"] == "impact-rc3"
