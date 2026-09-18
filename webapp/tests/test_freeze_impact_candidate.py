"""Freezing refuses anything but a commit, and records what the chain needs.

rc2 was frozen by an uncommitted scratch script, so its freeze could never be
reproduced. These pin the replacement: a dirty or partly untracked webapp/ is
refused, and a manifest it writes verifies, names its release comparator, and
carries the fingerprints, freeze context and environment.
"""

import json

import pytest

from app.scoring import impact
from app.scoring.impact_manifest import RC3, config_from_manifest, load_manifest
from scripts import freeze_impact_candidate as freezer
from tests.buy_disruption_fixtures import build_match, session


def test_a_modified_tracked_file_is_refused():
    def fake_git(*args):
        return " M webapp/app/scoring/impact.py\n" if args[0] == "status" else "abc\n"

    with pytest.raises(freezer.FreezeRefused) as caught:
        freezer.require_clean_webapp(fake_git)
    assert "impact.py" in str(caught.value)


def test_an_untracked_script_is_refused():
    """The rc2 failure mode exactly: the code that froze it was never committed."""
    def fake_git(*args):
        return "?? webapp/scripts/scratch_freeze.py\n" if args[0] == "status" else "abc\n"

    with pytest.raises(freezer.FreezeRefused):
        freezer.require_clean_webapp(fake_git)


def test_a_clean_webapp_freezes_at_head():
    def fake_git(*args):
        return "" if args[0] == "status" else "0123456789abcdef\n"

    assert freezer.require_clean_webapp(fake_git) == "0123456789abcdef"


def test_a_frozen_manifest_verifies_and_carries_the_chain_context(tmp_path):
    db = session(all_tables=True)
    match, _, _ = build_match(db, "freeze-a", kills={3: [("A1", "B1", 10.0)]})
    other, _, _ = build_match(db, "freeze-b", kills={3: [("B2", "A2", 12.0)]})
    out = tmp_path / "candidate-manifest.json"

    manifest = freezer.freeze(
        db, candidate_id="impact-rc3-test", release_comparator=RC3,
        activation_version=impact.IMPACT_CALCULATION_VERSION + 1, match_ids=[match.id, other.id],
        out_path=out, scorer_revision="f" * 40, packages=["numpy==2.4.6", "sqlalchemy==2.0.35"])

    written = load_manifest(out)
    assert written == json.loads(out.read_text(encoding="utf-8")) == manifest
    assert written["scorer_revision"] == "f" * 40
    assert set(written["source_snapshots"]["matches"]) == {str(match.id), str(other.id)}
    assert written["freeze_context"]["match_count"] == 2
    assert written["freeze_context"]["declared_matches"] == [match.id, other.id]
    assert written["environment"]["packages"] == ["numpy==2.4.6", "sqlalchemy==2.0.35"]
    config = config_from_manifest(written)
    assert config.enable_trade_credit is True and config.weights.assists == 100.0
