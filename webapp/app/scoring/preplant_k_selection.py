"""k selection per docs/superpowers/2026-09-07-predeclared-values.md, 'The
k / FLOOR / CEIL / W grid decision'. Deterministic; nothing here reads a
score, a correlation, or |c-1| -- see that section for why."""

from dataclasses import dataclass

from app.scoring.preplant_time_model import PreplantFit, PreplantKillObservation

K_GRID: tuple[float, ...] = (0.2, 0.27, 0.35, 0.45, 0.6, 0.8, 1.0, 1.3, 1.7, 2.2, 2.8)

FLOOR_TARGET = 1.5    # percent; strict <
CEIL_LOW, CEIL_HIGH = 2.0, 4.0  # percent; inclusive
CEIL_CENTER = 3.0


def raw_scalar(fit: PreplantFit, k: float, obs: PreplantKillObservation) -> float:
    """1 + k * logit_lift(adv, side) * shape(dt), BEFORE clamping. Crossing
    rates are counted on this, never on the clamped scalar (the declaration's
    'single easiest mistake' -- scalar is already clamped, testing it against
    0.2/1.7 reports 0.00% at every k)."""
    return 1.0 + k * fit.logit_lift(obs.adv, obs.is_attacker) * fit.shape(obs.dt)


@dataclass
class KSelectionResult:
    selected_k: float
    branch: int
    table: list[dict]


def select_k(
    fit: PreplantFit, observations: list[PreplantKillObservation], total_kills_denominator: int,
) -> KSelectionResult:
    """total_kills_denominator is the TARGET denominator (all non-self kills
    in non-surrendered rounds, 484,610 on the 2026-09-07 snapshot) -- supplied
    by the caller, since this module never runs its own DB query for a number
    computed for an unrelated reason."""
    affected_denominator = len(observations)
    table = []
    for k in K_GRID:
        floor = ceil_ = 0
        for obs in observations:
            raw = raw_scalar(fit, k, obs)
            if raw < 0.2:
                floor += 1
            if raw > 1.7:
                ceil_ += 1
        table.append({
            "k": k,
            "floor_rate_all": 100.0 * floor / total_kills_denominator,
            "ceiling_rate_all": 100.0 * ceil_ / total_kills_denominator,
            "floor_rate_affected": 100.0 * floor / affected_denominator if affected_denominator else 0.0,
            "ceiling_rate_affected": 100.0 * ceil_ / affected_denominator if affected_denominator else 0.0,
        })

    qualifying = [
        row for row in table
        if row["floor_rate_all"] < FLOOR_TARGET and CEIL_LOW <= row["ceiling_rate_all"] <= CEIL_HIGH
    ]
    if qualifying:
        best = min(qualifying, key=lambda r: (abs(r["ceiling_rate_all"] - CEIL_CENTER), r["k"]))
        return KSelectionResult(selected_k=best["k"], branch=1, table=table)

    floor_ok = [row for row in table if row["floor_rate_all"] < FLOOR_TARGET]
    if floor_ok:
        best = min(floor_ok, key=lambda r: (abs(r["ceiling_rate_all"] - CEIL_CENTER), r["k"]))
        return KSelectionResult(selected_k=best["k"], branch=2, table=table)

    return KSelectionResult(selected_k=K_GRID[0], branch=3, table=table)
