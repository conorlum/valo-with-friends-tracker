"""The review tool's frozen-manifest mode.

Plan section 3 and plan-review P2: named comparator identities (live legacy,
old separate-econ, V2 wealth debit, V2 30/80), no implicit timing, the same
production calculator as the runtime, reconciled traces, and a fail-visible
check that the reviewed source rows are the frozen ones.
"""
import dataclasses

import pytest

import scripts.backfill_impact_candidate as backfill
import scripts.release_candidate_review as review
from app.scoring import econ_buy_disruption as bd
from app.scoring import impact
from app.scoring.impact import compute_impact_for_match
from app.scoring.impact_manifest import (
    ManifestMismatchError,
    build_manifest,
    config_from_manifest,
    lf_sha256,
    match_source_fingerprint,
    verify_source_snapshots,
    write_manifest,
)
from tests.buy_disruption_fixtures import build_match, session, stat

B_BROKE_NEXT = {8: {f"B{i}": 0 for i in range(1, 6)}}


def _manifest(snapshots=None):
    return build_manifest(candidate_id="review-test", created="2026-09-11", scorer_revision="test",
                          activation_impact_calculation_version=impact.IMPACT_CALCULATION_VERSION + 1,
                          source_snapshots={"matches": snapshots or {}})


def _environmental_match(db):
    return build_match(db, "rv", kills={7: [("A1", "B1", 10.0), (None, "B2", 20.0), ("B3", "A3", 30.0)]},
                       loadouts=B_BROKE_NEXT, remainings=B_BROKE_NEXT, scores={7: {"A1": 220}})


def test_frozen_pairs_are_named_and_never_enable_timing():
    manifest = _manifest()
    assert [n for n, _ in review.frozen_pair(manifest, "site")] == ["live_legacy", bd.MODEL_V2_30_80]
    assert [n for n, _ in review.frozen_pair(manifest, "penalty")] == [bd.MODEL_V2_WEALTH, bd.MODEL_V2_30_80]
    assert [n for n, _ in review.frozen_pair(manifest, "separate")] == [
        bd.MODEL_SEPARATE_ECON_LEGACY, bd.MODEL_V2_30_80]
    for compare in ("site", "penalty", "separate"):
        for _, config in review.frozen_pair(manifest, compare):
            kwargs = config.build_kwargs()
            assert "postplant_factor_table" not in kwargs
            assert "enable_postplant_leverage" not in kwargs
            assert "enable_preplant_empirical" not in kwargs


def test_site_review_reconciles_and_keeps_econ_signed():
    db = session()
    match, players, _ = _environmental_match(db)
    rec = review.Reconciler()
    text, data = review.render_match_review(db, match.id, _manifest(), "site", rec)
    assert rec.failures == []
    assert rec.checks > 0
    victim = data["players"][players["B2"].id]
    assert victim["left"]["econ"] == 0
    assert victim["right"]["econ"] == -177
    assert victim["right"]["credit"] == 0
    assert victim["right"]["debit"] == pytest.approx(0.176 * 1007.9209)
    assert "TOTAL release change" in text
    assert "live_legacy" in text and bd.MODEL_V2_30_80 in text


def test_penalty_review_holds_gross_credit_and_combat_identical():
    db = session()
    match, _, _ = _environmental_match(db)
    rec = review.Reconciler()
    _, data = review.render_match_review(db, match.id, _manifest(), "penalty", rec)
    assert rec.failures == []
    for player in data["players"].values():
        assert player["left"]["credit"] == player["right"]["credit"]
        assert player["left"]["damage"] == player["right"]["damage"]
        assert player["left"]["leverage"] == player["right"]["leverage"]


def test_penalty_reconciliation_detects_a_credit_that_differs():
    db = session()
    match, players, _ = _environmental_match(db)
    manifest = _manifest()
    left = review.score_with(db, match.id, config_from_manifest(manifest, bd.MODEL_V2_WEALTH))
    right = review.score_with(db, match.id, config_from_manifest(manifest, bd.MODEL_V2_30_80))
    result = right["econ"][7]["result"]
    killer = players["A1"].id
    tampered = dict(result.players)
    tampered[killer] = dataclasses.replace(tampered[killer], disruption_credit=tampered[killer].disruption_credit + 1e-6)
    right["econ"][7] = {**right["econ"][7], "result": dataclasses.replace(result, players=tampered)}
    rec = review.Reconciler()
    review.reconcile_penalty_pair(rec, left, right)
    assert any("gross credit" in failure for failure in rec.failures)


def test_trace_explains_every_event_its_absorption_and_its_rate():
    db = session()
    match, _, ids = _environmental_match(db)
    rec = review.Reconciler()
    text = review.render_frozen_trace(db, match.id, _manifest(), rec)
    assert rec.failures == []
    for event_id in ids[7]:
        assert f"event {event_id} |" in text
    assert "unknown_killer" in text
    assert "80%, constrained" in text and "30%, absorbed" in text
    assert "CONSTRAINED next buy" in text and "ABSORBED its losses" in text
    assert "Next bank" in text and "pistol_half_or_ot_boundary" in text


def test_frozen_review_refuses_source_rows_that_changed_after_the_freeze():
    db = session()
    match, players, _ = _environmental_match(db)
    manifest = _manifest({str(match.id): match_source_fingerprint(db, match.id)})
    verify_source_snapshots(db, manifest, [match.id])
    stat(db, match, players, 8, "B1").remaining = 5
    db.commit()
    with pytest.raises(ManifestMismatchError, match="changed since the freeze"):
        verify_source_snapshots(db, manifest, [match.id])


def test_pistol_winner_losing_round_two_is_selected_from_outcomes_only():
    db = session()
    lost, _, _ = build_match(db, "p-lost", outcomes={1: "Team B Elimination Win", 2: "Team A Elimination Win"})
    build_match(db, "p-held")
    surrendered, _, _ = build_match(db, "p-surr", outcomes={1: "Team B Elimination Win",
                                                            2: "Team A Surrendered Win"})
    found = review.pistol_winner_round_two_losses(db)
    assert [(f["match_id"], f["round"]) for f in found] == [(lost.id, 2)]
    assert review.pistol_winner_round_two_losses(db, exclude={lost.id}) == []


def test_review_results_are_exactly_what_backfill_acceptance_compares(tmp_path):
    db = session()
    match, _, _ = _environmental_match(db)
    manifest = _manifest()
    path = tmp_path / "manifest.json"
    write_manifest(path, manifest)
    results = review.build_review_results(db, path, manifest, [match.id])
    compute_impact_for_match(db, match.id, config=config_from_manifest(manifest))
    assert backfill.persisted_result_diffs(db, results, lf_sha256(path)) == []
    key = next(iter(results["matches"][str(match.id)]["rows"]))
    results["matches"][str(match.id)]["rows"][key][1] -= 1
    assert backfill.persisted_result_diffs(db, results, lf_sha256(path))


def test_corpus_identity_and_validation_failures_fail_the_run():
    """A corpus mismatch must reach the exit status, not just the JSON."""
    rec = review.Reconciler()
    review.register_corpus_failures(rec, {"identity_mismatches": {"impact_identity": 1},
                                          "input_validation_failures": {"7": "boom"},
                                          "matches_requested": 2, "matches_scored": 1})
    assert len(rec.failures) >= 2
    clean = review.Reconciler()
    review.register_corpus_failures(clean, {"identity_mismatches": {}, "input_validation_failures": {},
                                            "matches_requested": 2, "matches_scored": 2})
    assert clean.failures == []


def test_a_weights_override_restamps_the_reported_identity():
    manifest = _manifest()
    overridden, label = review.apply_weight_override(manifest, "abc123", "1.25,1.0,2.0")
    assert overridden["comparators"][bd.MODEL_V2_30_80]["weights"]["econ"] == 2.0
    assert "abc123" in label and "NOT THE FROZEN CANDIDATE" in label
    assert "WEIGHTS-OVERRIDE" in overridden["candidate_id"]
    assert any("C(econ)=2.0" in change for change in overridden["formula_changes_vs_live_legacy"])


def test_corpus_audit_counts_identities_and_signs_on_a_small_corpus():
    db = session()
    first, _, _ = _environmental_match(db)
    second, _, _ = build_match(db, "rv2", kills={5: [("B1", "A1", 3.0)], 7: [("A2", "B3", 4.0)]})
    audit = review.corpus_audit(db, _manifest(), min_matches=1)
    assert audit["matches_scored"] == 2
    assert audit["input_validation_failures"] == {}
    assert audit["identity_mismatches"] == {}
    assert audit["event_kinds_in_scored_rounds"]["unknown_killer"] == 1
    assert audit["player_rounds"] == 160
    assert audit["econ_component_player_round"]["negative"] > 0
