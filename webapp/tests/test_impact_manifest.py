"""The frozen candidate manifest and the single runtime switch.

Plan-review finding P2: the complete configuration is frozen BEFORE review,
review/runtime/recompute load the same manifest, and anything missing or
different fails visibly. These tests pin that contract.
"""
import copy
import json

import pytest

from app.models import ImpactScore
from app.scoring import agent_economy
from app.scoring import econ_buy_disruption as bd
from app.scoring import impact, impact_manifest, impact_runtime
from app.scoring.impact import FormulaWeights, build_impact_rows_for_match, compute_impact_for_match
from app.scoring.impact_config import ImpactScoringConfig
from app.scoring.impact_manifest import (
    ManifestMismatchError,
    behavioral_source_digest,
    build_manifest,
    config_from_manifest,
    load_manifest,
    match_source_fingerprint,
    verify_manifest,
    write_manifest,
)
from tests.buy_disruption_fixtures import build_match, session, stat

B_BROKE_NEXT = {8: {f"B{i}": 0 for i in range(1, 6)}}


def _manifest(**overrides):
    kwargs = dict(candidate_id="test", created="2026-09-11", scorer_revision="deadbeef",
                  activation_impact_calculation_version=impact.IMPACT_CALCULATION_VERSION + 1,
                  source_snapshots={"matches": {}})
    kwargs.update(overrides)
    return build_manifest(**kwargs)


@pytest.fixture(autouse=True)
def _fresh_runtime():
    impact_runtime.clear_cache()
    yield
    impact_runtime.clear_cache()


def test_a_fresh_manifest_round_trips_and_verifies(tmp_path):
    path = tmp_path / "manifest.json"
    write_manifest(path, _manifest())
    loaded = load_manifest(path)
    verify_manifest(loaded)
    assert loaded["release_comparator"] == bd.MODEL_V2_30_80
    assert loaded["timing"]["postplant_leverage"] is False


def test_comparator_identities_differ_only_where_declared():
    manifest = _manifest()
    release = config_from_manifest(manifest)
    wealth = config_from_manifest(manifest, bd.MODEL_V2_WEALTH)
    legacy = config_from_manifest(manifest, "live_legacy")
    assert release.econ_model == bd.MODEL_V2_30_80
    assert release.weights == FormulaWeights(1.25, 1.0, 1.0)
    penalty_left, penalty_right = (dict(config_from_manifest(manifest, n).build_kwargs())
                                   for n in manifest["penalty_comparison"])
    assert {k for k in penalty_left if penalty_left[k] != penalty_right[k]} == {"econ_model"}
    assert legacy.build_kwargs()["enable_econ_component"] is False
    assert wealth.econ_model == bd.MODEL_V2_WEALTH


def test_a_changed_calculator_constant_fails_visibly(monkeypatch):
    manifest = _manifest()
    monkeypatch.setattr(bd, "DISRUPTED_RATE", 0.75)
    with pytest.raises(ManifestMismatchError, match="calculator_constants"):
        verify_manifest(manifest)


def test_a_changed_allowance_table_fails_visibly(monkeypatch):
    manifest = _manifest()
    monkeypatch.setitem(agent_economy.AGENT_FREE_ABILITY_CREDITS, "clove", 999)
    with pytest.raises(ManifestMismatchError, match="agent_free_ability_credits"):
        verify_manifest(manifest)


def test_changed_scoring_source_or_missing_digest_fails_visibly():
    manifest = _manifest()
    tampered = copy.deepcopy(manifest)
    tampered["source_digests"]["app/scoring/econ_buy_disruption.py"] = "0" * 64
    with pytest.raises(ManifestMismatchError, match="econ_buy_disruption.py"):
        verify_manifest(tampered)
    dropped = copy.deepcopy(manifest)
    del dropped["source_digests"]["app/scoring/impact.py"]
    with pytest.raises(ManifestMismatchError, match="impact.py"):
        verify_manifest(dropped)


def test_model_definitions_are_covered_by_the_source_digests():
    """A mapped column or enum change alters what the scorer reads without
    touching app/scoring, so the models are hashed too."""
    assert {"app/models/match.py", "app/models/round.py", "app/models/kill_event.py",
            "app/models/impact_score.py"} <= set(impact_manifest.HASHED_SOURCES)
    assert set(_manifest()["source_digests"]) == set(impact_manifest.HASHED_SOURCES)


def test_masking_covers_only_a_lone_version_assignment(tmp_path):
    """`IMPACT_CALCULATION_VERSION = RATE = 2` must not mask RATE as well."""
    chained_two = tmp_path / "two.py"
    chained_two.write_text("IMPACT_CALCULATION_VERSION = RATE = 2\ndef score():\n    return RATE\n")
    chained_three = tmp_path / "three.py"
    chained_three.write_text("IMPACT_CALCULATION_VERSION = RATE = 3\ndef score():\n    return RATE\n")
    assert behavioral_source_digest(chained_two) != behavioral_source_digest(chained_three)


def test_behavioral_digest_ignores_comments_docstrings_and_the_version_bump(tmp_path):
    base = tmp_path / "a.py"
    base.write_text('"""doc"""\nIMPACT_CALCULATION_VERSION = 2\nRATE = 0.8\ndef f():\n    return RATE\n')
    cosmetic = tmp_path / "b.py"
    cosmetic.write_text('"""other doc"""\n# note\nIMPACT_CALCULATION_VERSION = 3\n\nRATE = 0.8  # x\n'
                        'def f():\n    """explained"""\n    return RATE\n')
    behavioral = tmp_path / "c.py"
    behavioral.write_text('"""doc"""\nIMPACT_CALCULATION_VERSION = 2\nRATE = 0.7\ndef f():\n    return RATE\n')
    assert behavioral_source_digest(base) == behavioral_source_digest(cosmetic)
    assert behavioral_source_digest(base) != behavioral_source_digest(behavioral)


@pytest.mark.parametrize("mutate", [
    lambda m: m["timing"].__setitem__("postplant_leverage", True),
    lambda m: m["comparators"].pop(bd.MODEL_V2_WEALTH),
    lambda m: m["comparators"][bd.MODEL_V2_30_80]["weights"].__setitem__("econ", 0.5),
    lambda m: m.__setitem__("econ_scale", 1000.0),
])
def test_unsupported_or_altered_configuration_fails_visibly(mutate):
    manifest = _manifest()
    mutate(manifest)
    with pytest.raises(ManifestMismatchError):
        verify_manifest(manifest)


def test_a_missing_manifest_file_fails_visibly(tmp_path):
    with pytest.raises(ManifestMismatchError, match="missing"):
        load_manifest(tmp_path / "absent.json")


def test_timing_flags_cannot_be_built_without_frozen_artifacts():
    with pytest.raises(ValueError):
        ImpactScoringConfig("x", enable_econ_component=True, enable_postplant_leverage=True).build_kwargs()


def test_source_fingerprint_moves_only_with_its_own_matchs_rows():
    db = session()
    first, players, _ = build_match(db, "fp1", kills={7: [("A1", "B1", 10.0)]})
    before = match_source_fingerprint(db, first.id)
    other, other_players, _ = build_match(db, "fp2", kills={3: [("B1", "A1", 5.0)]})
    stat(db, other, other_players, 3, "A2").loadout = 1
    db.commit()
    assert match_source_fingerprint(db, first.id) == before
    stat(db, first, players, 8, "B4").remaining = 1
    db.commit()
    assert match_source_fingerprint(db, first.id) != before


# ---- the runtime switch ------------------------------------------------------------------

def test_runtime_default_is_the_live_legacy_formula():
    assert impact_runtime.ACTIVE_MANIFEST is None
    assert impact_runtime.active_scoring_config() is None


def test_an_active_manifest_drives_compute_impact_for_match(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    manifest = _manifest()
    write_manifest(path, manifest)
    monkeypatch.setattr(impact_runtime, "ACTIVE_MANIFEST", str(path))
    monkeypatch.setattr(impact, "IMPACT_CALCULATION_VERSION", manifest["activation_impact_calculation_version"])

    db = session()
    match, players, _ = build_match(db, "act", kills={7: [("A1", "B1", 10.0)]},
                                    loadouts=B_BROKE_NEXT, remainings=B_BROKE_NEXT)
    expected = build_impact_rows_for_match(db, match.id, **config_from_manifest(manifest).build_kwargs())
    compute_impact_for_match(db, match.id)
    stored = {(s.round_id, s.match_player_id): s for s in db.query(ImpactScore).all()}
    assert any(row.econ_component < 0 for row in expected)
    for row in expected:
        assert stored[(row.round_id, row.match_player_id)].econ_component == row.econ_component
        assert stored[(row.round_id, row.match_player_id)].impact == row.impact


def test_a_manifest_edited_after_it_was_cached_is_rejected(tmp_path, monkeypatch):
    """A long-running ingest process must not keep scoring under a manifest
    that no longer matches the file on disk."""
    path = tmp_path / "manifest.json"
    manifest = _manifest()
    write_manifest(path, manifest)
    monkeypatch.setattr(impact_runtime, "ACTIVE_MANIFEST", str(path))
    monkeypatch.setattr(impact, "IMPACT_CALCULATION_VERSION", manifest["activation_impact_calculation_version"])
    assert impact_runtime.active_scoring_config() is not None

    tampered = {**manifest, "econ_scale": 1000.0}
    write_manifest(path, tampered)
    with pytest.raises(ManifestMismatchError):
        impact_runtime.active_scoring_config()


def test_activation_without_the_declared_version_bump_fails_visibly(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    write_manifest(path, _manifest())
    monkeypatch.setattr(impact_runtime, "ACTIVE_MANIFEST", str(path))
    with pytest.raises(ManifestMismatchError, match="IMPACT_CALCULATION_VERSION"):
        impact_runtime.active_scoring_config()


# ---- the round 2/14 bonus-denial candidate (spec 2026-09-12 section 9) --------------------

def test_bonus_comparator_differs_from_30_80_only_in_the_model():
    manifest = _manifest(release_comparator=impact_manifest.V2_30_80_BONUS)
    verify_manifest(manifest)
    old = dict(config_from_manifest(manifest, bd.MODEL_V2_30_80).build_kwargs())
    new = dict(config_from_manifest(manifest).build_kwargs())
    assert {k for k in old if old[k] != new[k]} == {"econ_model"}
    assert new["econ_model"] == bd.MODEL_V2_30_80_BONUS_DENIAL


def test_bonus_constants_and_modules_are_frozen():
    manifest = _manifest()
    for name in ("BONUS_AUDIT_VERSION", "BONUS_DENIAL_THRESHOLD", "SWING_VALUE_PER_CREDIT",
                 "BONUS_WON_FACTOR", "BONUS_LOST_FACTOR", "SURVIVED_LOSS_REWARD"):
        assert name in manifest["calculator_constants"]
    assert {"app/scoring/weapon_prices.py", "app/scoring/round_rewards.py"} <= set(manifest["source_digests"])


def test_a_weapon_only_change_moves_the_source_fingerprint():
    from app.models import KillEvent
    db = session()
    match, _, _ = build_match(db, "fpw", kills={7: [("A1", "B1", 10.0)]})
    before = match_source_fingerprint(db, match.id)
    db.query(KillEvent).one().weapon = "Phantom"
    db.commit()
    assert match_source_fingerprint(db, match.id) != before


def test_bonus_release_manifest_describes_the_bonus_rule():
    bonus_text = " ".join(_manifest(release_comparator=impact_manifest.V2_30_80_BONUS)["formula_changes_vs_live_legacy"])
    assert "round 2/14 bonus-round denial" in bonus_text
    assert "round 2/14" not in " ".join(_manifest()["formula_changes_vs_live_legacy"])
