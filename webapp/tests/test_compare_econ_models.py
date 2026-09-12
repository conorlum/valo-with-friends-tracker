"""compare_econ_models: parity holds where declared, and the differences are counted."""
import scripts.compare_econ_models as cmp
from app.scoring import impact
from app.scoring import impact_manifest as im
from tests.buy_disruption_fixtures import build_match, session


def _manifest():
    return im.build_manifest(candidate_id="t", created="2026-09-12", scorer_revision="t",
                             activation_impact_calculation_version=impact.IMPACT_CALCULATION_VERSION + 1,
                             source_snapshots={"matches": {}}, release_comparator=im.V2_30_80_BONUS)


def test_small_corpus_reconciles_and_reports_the_round_two_change():
    db = session()
    build_match(db, "c1", kills={2: [("B1", "A1", 10.0)], 5: [("A2", "B2", 5.0)]},
                weapons={2: ["Spectre"]}, loadouts={2: {"A1": 2600}},
                outcomes={2: "Team B Elimination Win"}, count_stats=True)
    build_match(db, "c2", kills={7: [("A1", "B1", 10.0)]}, count_stats=True)
    report = cmp.compare(db, _manifest(), min_matches=1)
    assert report["parity_mismatches"] == {}
    assert report["input_validation_failures"] == {}
    assert report["matches_scored"] == 2
    assert report["bonus"]["qualifying_deaths"] == 1
    r2, r5 = report["by_round_number"]["2"], report["by_round_number"]["5"]
    assert r2["new_gross_points_per_round"] > r2["old_gross_points_per_round"]
    assert r5["new_gross_points_per_round"] == r5["old_gross_points_per_round"]
