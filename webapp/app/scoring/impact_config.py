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

    def build_kwargs(self) -> dict:
        if self.enable_postplant_leverage or self.enable_preplant_empirical:
            # Both need a frozen table/curve artifact and centering constant
            # to be reproducible; this configuration cannot pin them yet.
            raise ValueError(
                f"{self.config_id}: timing candidates are not supported by a frozen "
                "configuration without their table artifacts")
        return {
            "use_realized_swing": self.use_realized_swing,
            "enable_econ_component": self.enable_econ_component,
            "econ_model": self.econ_model,
            "weights": self.weights,
        }
