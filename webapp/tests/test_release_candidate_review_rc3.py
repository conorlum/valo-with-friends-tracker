"""The review tool, held to what rc3 actually ships.

Two external-review findings on the rc2 tooling, both reproduced here:
- A6: every reconciliation added impact up from damage + leverage + econ. rc3
  adds D*assists as a fourth term, so the three-term identity fails on every
  player-round with an assist -- the reviews could not pass on correct scores.
- A7: the site review's "Before" column was a REPLAY of the legacy code, not
  what production stores, so approval would have judged the wrong change.
"""

import pytest

import scripts.backfill_impact_candidate as backfill
import scripts.release_candidate_review as review
from app.models import ImpactScore, RoundPlayerStat
from app.scoring import impact
from app.scoring.impact import compute_impact_for_match
from app.scoring.impact_manifest import RC3, build_manifest
from tests.buy_disruption_fixtures import build_match, session

B_BROKE_NEXT = {8: {f"B{i}": 0 for i in range(1, 6)}}


def _rc3_manifest():
    return build_manifest(candidate_id="rc3-review-test", created="2026-09-16", scorer_revision="test",
                          activation_impact_calculation_version=impact.IMPACT_CALCULATION_VERSION + 1,
                          source_snapshots={"matches": {}}, release_comparator=RC3)


def _match_with_assists(db, external_id="rc3-review"):
    match, players, _ = build_match(
        db, external_id, kills={7: [("A1", "B1", 10.0), ("B2", "A2", 20.0), ("A3", "B2", 21.0)]},
        loadouts=B_BROKE_NEXT, remainings=B_BROKE_NEXT, scores={7: {"A1": 220}}, count_stats=True)
    # build_match writes assists=0 everywhere; give a few players assists so the
    # D*assists term is nonzero where the three-term identity would break.
    for name in ("A1", "A3", "B2"):
        db.query(RoundPlayerStat).filter(RoundPlayerStat.match_player_id == players[name].id).update(
            {"assists": 2})
    db.flush()
    return match, players


def test_site_review_of_rc3_reconciles_with_the_assists_term():
    db = session(all_tables=True)
    match, _ = _match_with_assists(db)
    rec = review.Reconciler()
    review.render_match_review(db, match.id, _rc3_manifest(), "site", rec)
    assert rec.checks > 0
    assert rec.failures == [], rec.failures


def test_the_assists_term_is_really_present_so_three_terms_would_fail():
    """Guards the test above against passing vacuously on a fixture with no assists."""
    db = session(all_tables=True)
    match, _ = _match_with_assists(db)
    rows = review.score_with(db, match.id, review.config_from_manifest(_rc3_manifest()))["rows"]
    with_assists = [r for r in rows if r.assists_component]
    assert with_assists, "the fixture must produce nonzero D*assists"
    assert any(r.impact != r.damage + r.leverage_component + r.econ_component for r in with_assists)


def test_trace_of_rc3_reconciles_with_the_assists_term():
    db = session(all_tables=True)
    match, _ = _match_with_assists(db)
    rec = review.Reconciler()
    text = review.render_frozen_trace(db, match.id, _rc3_manifest(), rec, "site")
    assert rec.failures == [], rec.failures
    assert "D*assists" in text and "D=100.0" in text
    assert "Trade credit is ON" in text


def test_site_before_is_what_production_stores_not_a_replay():
    db = session(all_tables=True)
    match, players = _match_with_assists(db)
    compute_impact_for_match(db, match.id)  # the live legacy formula writes "production"
    stored = {}
    for row in db.query(ImpactScore).all():
        stored[row.match_player_id] = stored.get(row.match_player_id, 0) + row.impact
    # Make production disagree with the legacy replay for one player, as it does
    # for real (every stored row was written by an older version of the scorer).
    first = db.query(ImpactScore).filter_by(match_player_id=players["A1"].id).first()
    first.impact += 1000
    db.flush()
    stored[players["A1"].id] += 1000

    rec = review.Reconciler()
    text, data = review.render_match_review(db, match.id, _rc3_manifest(), "site", rec)
    for mp, entry in data["players"].items():
        assert entry["stored"]["impact"] == stored[mp]
    a1 = data["players"][players["A1"].id]
    assert a1["stored"]["impact"] != a1["left"]["impact"], "stored and replay must be told apart"
    assert f"{a1['stored']['impact']:+,}" in text
    assert "Before is what production stores today" in text


def test_a_match_with_no_stored_rows_is_reported_unscored_not_invented():
    db = session(all_tables=True)
    match, _ = _match_with_assists(db, "rc3-unscored")
    rec = review.Reconciler()
    text, data = review.render_match_review(db, match.id, _rc3_manifest(), "site", rec)
    assert all(entry["stored"]["impact"] is None for entry in data["players"].values())
    assert "n/a (unscored)" in text


def test_review_results_label_their_values_with_the_persisted_fields(tmp_path):
    db = session(all_tables=True)
    match, _ = _match_with_assists(db)
    manifest = _rc3_manifest()
    path = tmp_path / "manifest.json"
    path.write_text("{}", encoding="utf-8")
    results = review.build_review_results(db, path, manifest, [match.id])
    assert results["fields"] == list(impact.PERSISTED_FIELDS)
    first_row = next(iter(results["matches"][str(match.id)]["rows"].values()))
    assert len(first_row) == len(results["fields"])


def test_acceptance_refuses_results_whose_labels_do_not_match_their_values():
    approved = {"manifest_lf_sha256": "sha", "fields": ["impact", "econ_component"], "matches": {}}
    diffs = backfill.persisted_result_diffs(None, approved, "sha")
    assert any("label their values" in d for d in diffs)


def test_a_weights_override_is_refused_when_a_used_comparator_carries_d_or_the_credit():
    with pytest.raises(SystemExit) as caught:
        review.apply_weight_override(_rc3_manifest(), "sha", "1.25,1.0,1.0")
    assert "impact_rc3" in str(caught.value) and "cannot express" in str(caught.value)


def test_an_override_keeps_every_comparators_own_d_and_credit_scale():
    """An rc2-shape review never uses impact_rc3, so the override is allowed --
    but it must not quietly strip D and the scale from that comparator."""
    manifest = build_manifest(candidate_id="rc2-shape", created="2026-09-16", scorer_revision="test",
                              activation_impact_calculation_version=impact.IMPACT_CALCULATION_VERSION + 1,
                              source_snapshots={"matches": {}},
                              release_comparator="buy_disruption_v2_30_80_bonus_denial")
    overridden, _ = review.apply_weight_override(manifest, "sha", "1.25,1.0,1.0")
    rc3_weights = overridden["comparators"][RC3]["weights"]
    assert rc3_weights["assists"] == 100.0
    assert rc3_weights["trade_credit_scale"] == 1.0
    assert rc3_weights["damage"] == 1.25
