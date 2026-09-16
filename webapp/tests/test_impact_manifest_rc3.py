"""The rc3 release comparator, declared in code so a frozen file cannot drift.

Declared 2026-09-16 in docs/superpowers/2026-09-07-predeclared-values.md. The
danger these tests exist for: `config_from_dict` defaults a missing
`enable_trade_credit` to False, so a manifest that merely LOSES the key would
ship credit-off scoring while claiming to be rc3. Declaring impact_rc3 in
COMPARATORS makes verify_manifest compare the frozen dictionary against the
code's own, and reject exactly that.
"""

import pytest

from app.scoring import impact as impact_module
from app.scoring.impact_manifest import (
    COMPARATORS,
    RC3,
    ManifestMismatchError,
    build_manifest,
    config_from_manifest,
    current_code_identity,
    verify_manifest,
)

LOCKED = dict(damage=1.0, leverage=2.5, econ=2.5, assists=100.0, trade_credit_scale=1.0)


def _manifest():
    return build_manifest(
        candidate_id="impact-rc3-test",
        created="2026-09-16T00:00:00Z",
        scorer_revision="0" * 40,
        activation_impact_calculation_version=3,
        source_snapshots={"matches": {}},
        release_comparator=RC3,
    )


def test_the_declared_comparator_carries_the_locked_values():
    config = COMPARATORS[RC3]
    weights = config.weights
    assert (weights.damage, weights.leverage, weights.econ, weights.assists,
            weights.trade_credit_scale) == (1.0, 2.5, 2.5, 100.0, 1.0)
    assert config.enable_trade_credit is True
    assert config.enable_econ_component is True
    assert config.econ_model == "buy_disruption_v2_30_80_bonus_denial"


def test_a_manifest_built_here_loads_back_as_the_locked_configuration():
    config = config_from_manifest(_manifest())
    weights = config.weights
    assert {k: getattr(weights, k) for k in LOCKED} == LOCKED
    assert config.enable_trade_credit is True


def test_a_manifest_that_loses_the_credit_flag_is_rejected():
    """The whole reason rc3 is declared in code: without the flag the frozen
    file still parses, and it parses as credit OFF."""
    manifest = _manifest()
    del manifest["comparators"][RC3]["enable_trade_credit"]
    with pytest.raises(ManifestMismatchError) as caught:
        verify_manifest(manifest)
    assert any("does not match its declared identity" in d for d in caught.value.differences)


def test_a_manifest_with_edited_weights_is_rejected():
    manifest = _manifest()
    manifest["comparators"][RC3]["weights"]["econ"] = 2.347
    with pytest.raises(ManifestMismatchError) as caught:
        verify_manifest(manifest)
    assert any("does not match its declared identity" in d for d in caught.value.differences)


def test_a_manifest_built_from_this_checkout_verifies():
    verify_manifest(_manifest())


def test_the_manifest_describes_the_weights_and_credit_it_freezes():
    changes = " | ".join(_manifest()["formula_changes_vs_live_legacy"])
    assert "A(damage)=1.0" in changes and "B(leverage)=2.5" in changes
    assert "C(econ)=2.5" in changes and "D(assists)=100.0" in changes
    assert "trade credit: ON at scale 1.0" in changes
    assert "A(damage)=1.25" not in changes


def test_a_credit_off_release_says_so():
    manifest = build_manifest(
        candidate_id="rc2-shape", created="2026-09-16T00:00:00Z", scorer_revision="0" * 40,
        activation_impact_calculation_version=3, source_snapshots={"matches": {}},
        release_comparator="buy_disruption_v2_30_80_bonus_denial",
    )
    changes = " | ".join(manifest["formula_changes_vs_live_legacy"])
    assert "trade credit OFF" in changes
    assert "A(damage)=1.25" in changes, "that comparator really does use the default weights"


def test_the_code_identity_pins_the_credit_schedule():
    trade = current_code_identity()["trade"]
    assert trade["credit_schedule"] == [list(step) for step in impact_module.TRADE_CREDIT_SCHEDULE]
    assert trade["credit_schedule"][0] == [1.0, 0.60]
