"""Pure reference arithmetic for the 2026-09-10 spec, not production scoring."""
from dataclasses import dataclass
import math

TARGET = 3900.0
R = 19500.0
BACKGROUND = 0.10
DISRUPTION = 1.00
REVIEW_SCALE = 1007.9209


def _valid(values):
    if any(v is None or not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("Missing, negative or nonfinite economy data")


def buy_targets(paid, half_round, pistol_winner):
    _valid(paid)
    if half_round == 2 and pistol_winner:
        return [min(TARGET, max(1000.0, p)) for p in paid]
    return [TARGET for _ in paid]


@dataclass(frozen=True)
class Budget:
    target: float
    funding: float
    wealth: float
    lost: float
    shortfall: float
    restorable: float
    scarcity: float
    observed_gap: float
    activation: float
    severity_pool: float


def team_budget(targets, next_paid, next_bank, losses):
    if not all(len(xs) == 5 for xs in (targets, next_paid, next_bank, losses)):
        raise ValueError("Exactly five complete players are required")
    for xs in (targets, next_paid, next_bank, losses):
        _valid(xs)
    target = sum(targets)
    funding = sum(min(p, t) for p, t in zip(next_paid, targets)) + sum(next_bank)
    wealth = sum(next_paid) + sum(next_bank)
    lost = sum(losses)
    shortfall = max(0.0, target - funding)
    observed_gap = sum(max(0.0, t-p) for t, p in zip(targets, next_paid))
    activation = min(1.0, shortfall / TARGET)
    return Budget(target, funding, wealth, lost, shortfall, min(shortfall, lost),
                  max(0.0, min(1.5, 1.5 * (1 - wealth / (5 * 6300)))),
                  observed_gap, activation, min(lost, observed_gap * activation))


def first_loss_exposures(paid_by_player, events):
    _valid(paid_by_player.values())
    seen, out = set(), {}
    for event in sorted(events, key=lambda e: (e["time"], e["id"])):
        victim = event["victim"]
        out[event["id"]] = paid_by_player.get(victim, 0.0) if victim not in seen else 0.0
        seen.add(victim)
    return out


def event_credit(value, budget, *, enemy=True):
    _valid([value])
    if not enemy or value == 0:
        return 0.0, 0.0
    if value > budget.lost:
        raise ValueError("Event exposure exceeds its team's total loss")
    return BACKGROUND * value / R, (DISRUPTION * budget.severity_pool / R * value / budget.lost
                                   if budget.lost else 0.0)


def own_debit(loss, budget):
    _valid([loss])
    return BACKGROUND * loss / R, budget.scarcity * loss / R


def buy_linked_debit(loss, budget):
    """Owner's 30% absorbed / 80% disrupted candidate, separate from V2."""
    background, disruption = event_credit(loss, budget)
    rate = 0.80 if budget.severity_pool > 0 else 0.30
    return rate * background, rate * disruption
