"""One explicit Impact scoring configuration, shared by review, recompute and ingestion.

build_impact_rows_for_match takes a dozen keyword flags. Passing them ad hoc
is how the review tool came to enable post-plant leverage and rebuild its
table implicitly (plan review, finding P2). A configuration is a frozen
value: two callers holding the same config score identically.
"""
from dataclasses import dataclass, field

from app.scoring.impact import FormulaWeights


@dataclass(frozen=True)
class ImpactScoringConfig:
    config_id: str
    use_realized_swing: bool = True
    enable_econ_component: bool = False
    econ_model: str | None = None
    weights: FormulaWeights = field(default_factory=FormulaWeights)
    enable_postplant_leverage: bool = False
    enable_preplant_empirical: bool = False
    enable_trade_credit: bool = False
    # Impact v4 (declaration 12, plan 2026-09-21-impact-v4 section 2.2). Both
    # default OFF, so every configuration that predates them scores as before.
    enable_decided_only_time: bool = False
    remove_post_decided_assists: bool = False

    def __post_init__(self):
        if self.enable_decided_only_time and (
                self.enable_preplant_empirical or self.enable_postplant_leverage):
            # Decided-only time REPLACES the time factor; stacking a legacy
            # timing candidate on it would score neither measured arm.
            raise ValueError(
                f"{self.config_id}: enable_decided_only_time cannot be combined with "
                "enable_preplant_empirical or enable_postplant_leverage")

    def build_kwargs(self) -> dict:
        if self.enable_postplant_leverage or self.enable_preplant_empirical:
            # Both need a frozen table/curve artifact and centering constant
            # to be reproducible; this configuration cannot pin them yet.
            raise ValueError(
                f"{self.config_id}: timing candidates are not supported by a frozen "
                "configuration without their table artifacts")
        kwargs = {
            "use_realized_swing": self.use_realized_swing,
            "enable_econ_component": self.enable_econ_component,
            "econ_model": self.econ_model,
            "weights": self.weights,
            "enable_trade_credit": self.enable_trade_credit,
        }
        # The v4 flags are passed only when True, so a pre-v4 configuration's
        # kwargs stay the exact dict they always were (the scorer defaults both
        # to False) -- the same rule the manifest's serialisation follows.
        if self.enable_decided_only_time:
            kwargs["enable_decided_only_time"] = True
        if self.remove_post_decided_assists:
            kwargs["remove_post_decided_assists"] = True
        return kwargs
