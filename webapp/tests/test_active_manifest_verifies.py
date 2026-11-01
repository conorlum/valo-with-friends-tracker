"""The live scoring manifest must verify against this checkout.

Every ingest runs verify_ingest_preflight, which refuses to score when the
active manifest's frozen source digests no longer match the code. PR #176
added index=True to app/models/round.py (a digested file) and nothing failed
until the next ingest was refused. This catches that at review time: an edit
to a file in HASHED_SOURCES either has to leave its AST unchanged or go
through a re-freeze (docs/superpowers/SCORING-RELEASE-PROCESS.md).
"""
from app.scoring import impact_runtime


def test_the_active_manifest_verifies_against_this_checkout():
    if impact_runtime.ACTIVE_MANIFEST is None:
        return
    impact_runtime.clear_cache()
    assert impact_runtime.active_manifest() is not None
