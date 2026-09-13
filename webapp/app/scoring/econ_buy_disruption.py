"""Buy-disruption economy component: replacement cost and disruption of the next buy.

Spec: docs/superpowers/specs/2026-09-10-econ-buy-disruption-implementation.md,
sections 2-6 and section 12. Section 12 (the owner's 30% absorbed / 80%
disrupted death penalty) SUPERSEDES section 7's wealth-scarcity debit, which
survives here only as the named historical comparator MODEL_V2_WEALTH so the
Abyss before/after table stays reproducible.

    kill credit  = 0.10 * v / 19500 + severity_pool_T / 19500 * v / L_T
    death debit  = rate_T * (0.10 * lost_i / 19500 + severity_pool_T / 19500 * lost_i / L_T)
    rate_T       = 0.80 if severity_pool_T > 0 else 0.30

The component is PURE and RETROSPECTIVE: it reads round N+1, so it abstains
(exactly zero) under use_realized=False -- the leakage gate. Nothing here is
fitted. Every constant is a declared review policy recorded in
docs/superpowers/2026-09-07-predeclared-values.md before it was measured.

THE NET IS SIGNED. This module returns raw, unrounded credits and debits; the
scorer rounds each player-round net exactly once. Nothing here -- or anywhere
downstream -- floors a net at zero or offsets a set of players by its minimum
(owner clarification, 2026-09-10). The min()/max() calls below cap MODEL
QUANTITIES (funding, shortfall, severity), never the resulting net.

A round that cannot be reconstructed ABSTAINS with a named reason rather than
treating missing or invalid economy values as poverty. Callers write 0 for an
abstaining round, and report the reason.

This is a production port of the arithmetic in
docs/superpowers/diagnostics/econ_buy_disruption_reference.py. It must never
import that file: review and runtime share THIS implementation, and the saved
reference artifacts are regression expectations, not a dependency.
"""
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Hashable, Iterable, Mapping, Sequence

from app.scoring import agent_economy, round_rewards, weapon_prices

# ---- Model identities ----------------------------------------------------------
MODEL_SEPARATE_ECON_LEGACY = "separate_econ_legacy"  # app.scoring.econ_component
MODEL_V2_WEALTH = "buy_disruption_v2_wealth"          # historical comparator only
MODEL_V2_30_80 = "buy_disruption_v2_30_80"            # the owner's candidate
MODEL_V2_30_80_BONUS_DENIAL = "buy_disruption_v2_30_80_bonus_denial"  # spec 2026-09-12
BUY_DISRUPTION_MODELS = frozenset({MODEL_V2_WEALTH, MODEL_V2_30_80, MODEL_V2_30_80_BONUS_DENIAL})
# Both carry the 30%/80% death debit; the bonus model differs only in half-round 2.
THIRTY_EIGHTY_MODELS = frozenset({MODEL_V2_30_80, MODEL_V2_30_80_BONUS_DENIAL})

# Bump when the audit record's shape or meaning changes; review tooling keys on it.
AUDIT_VERSION = 1

# ---- Declared review constants (2026-09-10). None are fitted. --------------------
TARGET_KIT = 3900.0          # paid-loadout reference per player, from FULL_COMMIT
TEAM_REFERENCE = 19500.0     # R, five reference kits
BACKGROUND = 0.10            # small equipment-loss value, per credit of exposure
DISRUPTION = 1.00            # buy-disruption coefficient on the severity pool
ACTIVATION_GAP = 3900.0      # a funding gap of one kit activates the whole downgrade
CARRYOVER_FLOOR = 1000.0     # round 2/14 pistol-winner target floor
ABSORBED_RATE = 0.30         # death penalty when the team absorbs the loss
DISRUPTED_RATE = 0.80        # death penalty when the severity pool is positive
WEALTH_ZERO_AT = 6300.0      # comparator only: section 7 scarcity curve
WEALTH_CEILING = 1.5         # comparator only
ROSTER_SIZE = 5
CONTEXT_FULL_BUY_RAW = 4200  # displayed as context only; never a scoring threshold

# ---- Round 2/14 bonus-round denial (spec 2026-09-12). Declared, not fitted. ------------
BONUS_AUDIT_VERSION = 2
BONUS_DENIAL_THRESHOLD = 1500.0   # kit net of agent utility, strictly greater
SWING_VALUE_PER_CREDIT = 1.10     # BACKGROUND + DISRUPTION: a disrupted swing-round loss
BONUS_WON_FACTOR = 0.8            # the pistol winner still won round N
BONUS_LOST_FACTOR = 1.0           # the pistol winner lost round N
SURVIVED_LOSS_REWARD = 1000.0    # owner rule: surviving a round your team lost always banks 1,000


def audit_version_for(model: str) -> int:
    return BONUS_AUDIT_VERSION if model == MODEL_V2_30_80_BONUS_DENIAL else AUDIT_VERSION

_ELIGIBLE_ROUNDS = frozenset(range(2, 12)) | frozenset(range(14, 24))
SURRENDER_MARKER = "Surrendered"


# ---- Inputs ----------------------------------------------------------------------

@dataclass(frozen=True)
class PlayerEconomy:
    """One roster member's economy for round N and N+1.

    `has_current_stats`/`has_next_stats` distinguish a MISSING stat row
    (incomplete_stats) from a present row holding an invalid value
    (invalid_economy_data). Neither is ever read as zero wealth."""

    match_player_id: int
    team: Hashable
    free_ability_credits: float
    loadout: float | None
    next_loadout: float | None
    next_remaining: float | None
    has_current_stats: bool = True
    has_next_stats: bool = True
    agent: str | None = None
    remaining: float | None = None
    kills: int | None = None
    deaths: int | None = None
    next_deaths: int | None = None


@dataclass(frozen=True)
class EconEvent:
    """A source kill event. killer_id None is an environmental / unattributed
    death; event_id is the source KillEvent.id, preserved for trace
    reconciliation."""

    event_id: int | None
    time_seconds: float
    killer_id: int | None
    victim_id: int | None
    weapon: str | None = None


@dataclass(frozen=True)
class RoundEconInputs:
    round_number: int
    last_round_number: int
    team_a: Hashable  # the team an outcome beginning "Team A" refers to
    team_b: Hashable
    players: tuple[PlayerEconomy, ...]
    events: tuple[EconEvent, ...]
    outcome: str | None
    next_outcome: str | None
    pistol_outcome: str | None  # round 1 in the first half, round 13 in the second
    has_next_round: bool
    use_realized: bool = True
    next_events: tuple | None = None
    planted: bool | None = None
    attacking_team: Hashable | None = None
    next_round_reward: Mapping | None = None


# ---- Outputs ---------------------------------------------------------------------

@dataclass(frozen=True)
class TeamBudget:
    """Section 5's team quantities for the team that LOST the equipment.
    Field names match the saved reference artifacts' `budget` records."""

    target: float        # H
    funding: float       # U
    wealth: float        # uncapped next paid + bank (comparator's scarcity input)
    lost: float          # L, first-loss exposure
    shortfall: float     # D
    restorable: float    # Q = min(D, L), audit only
    scarcity: float      # comparator only
    observed_gap: float  # G
    activation: float
    severity_pool: float


@dataclass(frozen=True)
class PlayerLedger:
    match_player_id: int
    team: Hashable
    current_paid: float
    next_loadout: float
    next_paid: float
    next_bank: float
    target: float
    lost: float
    background_credit: float
    disruption_credit: float
    background_debit: float
    disruption_debit: float
    scarcity_debit: float
    penalty_rate: float | None

    @property
    def credit(self) -> float:
        return self.background_credit + self.disruption_credit

    @property
    def debit(self) -> float:
        return self.background_debit + self.disruption_debit + self.scarcity_debit

    @property
    def raw_net(self) -> float:
        # Term-by-term, in the reference's order, so the single rounding in
        # the scorer lands on the same integer as the saved artifacts.
        return (self.background_credit + self.disruption_credit
                - self.background_debit - self.disruption_debit - self.scarcity_debit)


@dataclass(frozen=True)
class EventLedger:
    event_id: int
    time_seconds: float
    killer_id: int | None
    victim_id: int
    killer_team: Hashable | None
    victim_team: Hashable
    kind: str            # enemy | self | team | unknown_killer
    exposure: float      # first-loss paid equipment; 0 for a repeated death
    background_credit: float
    disruption_credit: float
    victim_background_debit: float
    victim_disruption_debit: float
    victim_scarcity_debit: float
    penalty_rate: float | None
    absorbed: bool       # the victim team's funding test absorbed its losses
    bonus_qualifying: bool = False  # scored by the round 2/14 denial ledger

    @property
    def credit(self) -> float:
        return self.background_credit + self.disruption_credit

    @property
    def victim_debit(self) -> float:
        return self.victim_background_debit + self.victim_disruption_debit + self.victim_scarcity_debit


@dataclass(frozen=True)
class SurvivorRecovery:
    """Spec section 4 for one surviving pistol winner. Pickups are INFERRED:
    credits show value that was not bought; a kill-feed weapon shows use,
    not how or when it was acquired."""

    match_player_id: int
    utility_cost: float
    cash: float
    surplus: float
    credit_recovery: float
    feed_recovery: float
    feed_inference: str | None   # "in_round" | "carried" | None
    feed_weapon: str | None
    own_weapon: str | None
    recovery: float              # max(credit_recovery, feed_recovery), never their sum


@dataclass(frozen=True)
class BonusDenialAudit:
    """Spec sections 3-5 for the pistol-winning team in half-round 2."""

    won: bool
    factor: float
    denied: dict             # qualifying victim match_player_id -> denied credits
    survivors: tuple         # SurvivorRecovery, one per surviving pistol winner
    team_recovered: float
    net_denied: dict         # qualifying victim match_player_id -> net denied credits


@dataclass(frozen=True)
class TeamAudit:
    team: Hashable
    budget: TeamBudget
    targets: tuple[float, ...]
    penalty_rate: float | None
    carryover_targets: bool
    pistol_winner: bool
    round_winner: bool
    half_round: int
    deaths: int
    first_loss_players: int
    next_below_raw_4200: int
    credit: float  # raw credit EARNED by this team's players
    debit: float   # raw debit CHARGED to this team's players
    bonus: BonusDenialAudit | None = None  # bonus-denial model, pistol winner, half-round 2


@dataclass(frozen=True)
class RoundEconResult:
    model: str
    audit_version: int
    round_number: int
    abstention: str | None = None
    abstention_detail: str | None = None
    teams: dict = field(default_factory=dict)
    players: dict = field(default_factory=dict)
    events: tuple = ()
    data_quality: tuple = ()

    def raw_net_by_player(self) -> dict[int, float]:
        """Unrounded signed nets; {} when the round abstains."""
        return {pid: ledger.raw_net for pid, ledger in self.players.items()}

    def as_dict(self) -> dict:
        """A JSON-friendly audit record (team keys stringified)."""
        def team_key(team):
            return getattr(team, "value", team)
        return {
            "model": self.model, "audit_version": self.audit_version,
            "round_number": self.round_number, "abstention": self.abstention,
            "abstention_detail": self.abstention_detail,
            "teams": {str(team_key(t)): {**asdict(a), "team": str(team_key(a.team))}
                      for t, a in self.teams.items()},
            "players": {str(pid): {**asdict(p), "team": str(team_key(p.team)),
                                   "credit": p.credit, "debit": p.debit, "raw_net": p.raw_net}
                        for pid, p in self.players.items()},
            "events": [{**asdict(e), "killer_team": None if e.killer_team is None else str(team_key(e.killer_team)),
                        "victim_team": str(team_key(e.victim_team)), "credit": e.credit,
                        "victim_debit": e.victim_debit} for e in self.events],
            "data_quality": list(self.data_quality),
        }


# ---- Pure helpers ------------------------------------------------------------------

def is_eligible_round(round_number: int) -> bool:
    return round_number in _ELIGIBLE_ROUNDS


def half_round_index(round_number: int) -> int:
    return round_number if round_number <= 12 else round_number - 12


def pistol_round_for(round_number: int) -> int:
    return 1 if round_number <= 12 else 13


def outcome_winner(outcome: str | None, team_a: Hashable, team_b: Hashable) -> Hashable | None:
    """Never inferred: an outcome that names neither team gives None."""
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return team_a
    if outcome.startswith("Team B"):
        return team_b
    return None


def _valid_number(value) -> bool:
    return (value is not None and not isinstance(value, bool)
            and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0)


def _require_valid(values: Iterable) -> None:
    if not all(_valid_number(v) for v in values):
        raise ValueError("Missing, negative or nonfinite economy data")


def paid_loadout(loadout: float, free_ability_credits: float) -> float:
    """The equipment-value proxy: raw loadout less the agent's free charge."""
    return max(0, loadout - free_ability_credits)


def buy_targets(current_paid: Sequence[float], half_round: int, pistol_winner: bool) -> tuple:
    """Section 3. The pistol winner's round-2/14 target is its carryover kit,
    fixed from round N's own paid loadout, so losing that round cannot lower
    its own bar. Everyone else targets one reference kit."""
    if half_round == 2 and pistol_winner:
        return tuple(min(TARGET_KIT, max(CARRYOVER_FLOOR, p)) for p in current_paid)
    return tuple(TARGET_KIT for _ in current_paid)


def ordered_events(events: Iterable[EconEvent]) -> list[EconEvent]:
    return sorted(events, key=lambda e: (e.time_seconds, e.event_id))


def first_loss_exposures(paid_by_player: Mapping[int, float],
                         events: Iterable[EconEvent]) -> list[tuple[EconEvent, float]]:
    """Section 4: each player's FIRST chronological death exposes their starting
    paid kit; later deaths expose nothing (no invented replacement kit)."""
    seen: set = set()
    out = []
    for event in ordered_events(events):
        exposure = paid_by_player[event.victim_id] if event.victim_id not in seen else 0.0
        seen.add(event.victim_id)
        out.append((event, exposure))
    return out


def team_budget(targets, next_paid, next_bank, losses) -> TeamBudget:
    """Section 5 for one team. Equipment counts toward funding only up to
    each player's target; bank is pooled."""
    columns = [list(xs) for xs in (targets, next_paid, next_bank, losses)]
    if not all(len(xs) == ROSTER_SIZE for xs in columns):
        raise ValueError("Exactly five complete players are required")
    for xs in columns:
        _require_valid(xs)
    targets, next_paid, next_bank, losses = columns
    target = sum(targets)
    funding = sum(min(p, t) for p, t in zip(next_paid, targets)) + sum(next_bank)
    wealth = sum(next_paid) + sum(next_bank)
    lost = sum(losses)
    shortfall = max(0.0, target - funding)
    observed_gap = sum(max(0.0, t - p) for t, p in zip(targets, next_paid))
    activation = min(1.0, shortfall / ACTIVATION_GAP)
    scarcity = max(0.0, min(WEALTH_CEILING,
                            WEALTH_CEILING * (1 - wealth / (ROSTER_SIZE * WEALTH_ZERO_AT))))
    return TeamBudget(
        target=target, funding=funding, wealth=wealth, lost=lost, shortfall=shortfall,
        restorable=min(shortfall, lost), scarcity=scarcity, observed_gap=observed_gap,
        activation=activation, severity_pool=min(lost, observed_gap * activation),
    )


def event_credit(exposure: float, budget: TeamBudget) -> tuple[float, float]:
    """Section 6: (background, disruption) raw credit for one ENEMY event.
    Allocation is proportional to exposure over ALL of the victim team's
    first losses, so enemies never claim the non-enemy share of the pool."""
    if exposure == 0:
        return 0.0, 0.0
    if exposure > budget.lost:
        raise ValueError("Event exposure exceeds its team's total loss")
    return (BACKGROUND * exposure / TEAM_REFERENCE,
            DISRUPTION * budget.severity_pool / TEAM_REFERENCE * exposure / budget.lost
            if budget.lost else 0.0)


def penalty_rate(budget: TeamBudget) -> float:
    """Binary by owner direction: any positive severity pool selects 80%.
    The step on the background term is retained policy, not smoothed."""
    return DISRUPTED_RATE if budget.severity_pool > 0 else ABSORBED_RATE


def death_debit(loss: float, budget: TeamBudget, model: str) -> tuple[float, float, float]:
    """(background, disruption, scarcity) raw debit on a player's own first loss."""
    if model in THIRTY_EIGHTY_MODELS:
        background, disruption = event_credit(loss, budget)
        rate = penalty_rate(budget)
        return rate * background, rate * disruption, 0.0
    if model == MODEL_V2_WEALTH:
        return BACKGROUND * loss / TEAM_REFERENCE, 0.0, budget.scarcity * loss / TEAM_REFERENCE
    raise ValueError(f"Unknown buy-disruption model {model!r}")


# ---- Round 2/14 bonus-round denial ------------------------------------------------------

def _utility(player: PlayerEconomy) -> float:
    return float(agent_economy.known_utility_cost(player.agent))


def _bonus_denial(inputs, by_id, paid, next_paid, exposures, team, round_winner) -> BonusDenialAudit:
    """Spec sections 3-5 for the pistol-winning team in half-round 2: which first
    deaths qualify, what survivors recovered, and the net denied kit."""
    won = round_winner == team
    denied: dict[int, float] = {}
    dead: set[int] = set()
    for event, exposure in exposures:
        victim = by_id[event.victim_id]
        if victim.team != team:
            continue
        dead.add(victim.match_player_id)
        if exposure == 0:
            continue
        kit = max(0.0, exposure - _utility(victim))
        if kit > BONUS_DENIAL_THRESHOLD:
            denied[victim.match_player_id] = kit
    survivors = tuple(_survivor_recovery(inputs, by_id, paid, next_paid, team, dead, p, won)
                      for p in inputs.players if p.team == team and p.match_player_id not in dead)
    team_recovered = sum(s.recovery for s in survivors)
    total = sum(denied.values())
    keep = max(0.0, 1.0 - team_recovered / total) if total > 0 else 0.0
    return BonusDenialAudit(
        won=won, factor=BONUS_WON_FACTOR if won else BONUS_LOST_FACTOR, denied=denied,
        survivors=survivors, team_recovered=team_recovered,
        net_denied={pid: d * keep for pid, d in denied.items()},
    )


def _event_key(event: EconEvent) -> tuple:
    return (event.time_seconds, event.event_id)


def _survivor_recovery(inputs, by_id, paid, next_paid, team, dead, player, won) -> SurvivorRecovery:
    """Spec section 4 for one surviving pistol winner: credit evidence first,
    kill-feed evidence as a supplement, and the larger of the two -- never both."""
    pid = player.match_player_id
    utility = _utility(player)
    plant = round_rewards.PLANT_BONUS if (inputs.planted and inputs.attacking_team == team) else 0.0
    # A survivor of a round their team LOST banks the survive-loss 1,000, never the team's
    # loss bonus (owner rule; exact on every true survivor in the raw captures -- the only
    # apparent exceptions were spike-detonation deaths, which the kill feed records).
    reward = inputs.next_round_reward[team] if won else SURVIVED_LOSS_REWARD
    cash = min(float(round_rewards.CREDIT_CAP),
               player.remaining + round_rewards.KILL_REWARD * player.kills + plant + reward)
    surplus = (player.next_remaining + next_paid[pid]) - (cash + paid[pid])
    credit_recovery = surplus - utility if surplus > utility else 0.0
    feed_value, inference, weapon, own = _feed_recovery(
        pid, team, by_id, inputs.events, inputs.next_events, cash, player.next_remaining, paid[pid])
    return SurvivorRecovery(
        match_player_id=pid, utility_cost=utility, cash=cash, surplus=surplus,
        credit_recovery=credit_recovery, feed_recovery=feed_value, feed_inference=inference,
        feed_weapon=weapon, own_weapon=own, recovery=max(credit_recovery, feed_value),
    )


def _feed_recovery(pid, team, by_id, events, next_events, cash, next_remaining, paid_self):
    """Spec section 4.2: (value, inference, weapon, own_weapon), or (0, None, None, None).

    Unidentified and non-purchasable names are never evidence and never change
    the survivor's own weapon. A weapon a survivor uses shows use, not
    acquisition: in round N it cannot be a purchase, but the survivor may have
    owned it all along -- so an in-round pickup counts only when their paid kit
    could not cover that weapon plus every priced weapon they had already fired
    (owner rule 2026-09-12: a Vandal + Sheriff kit shows both were owned). In
    round N+1 it counts only when the survivor's spend could not have bought it."""
    prices = weapon_prices.WEAPON_PRICES
    ordered = ordered_events(events)
    death_key: dict[int, tuple] = {}
    for e in ordered:
        if by_id[e.victim_id].team == team and e.victim_id not in death_key:
            death_key[e.victim_id] = _event_key(e)
    # weapon -> earliest death of a teammate who killed with it before dying
    teammate_gun_death: dict[str, tuple] = {}
    for e in ordered:
        k = e.killer_id
        if (k is None or k == pid or k not in death_key
                or weapon_prices.classify(e.weapon) != "priced" or _event_key(e) >= death_key[k]):
            continue
        teammate_gun_death[e.weapon] = min(teammate_gun_death.get(e.weapon, death_key[k]), death_key[k])

    best = (0.0, None, None, None)
    used: list[str] = []
    for e in ordered:
        if e.killer_id != pid or weapon_prices.classify(e.weapon) != "priced":
            continue
        gun, own = e.weapon, (used[-1] if used else None)
        if (own is not None and gun != own and gun not in used and gun in teammate_gun_death
                and _event_key(e) > teammate_gun_death[gun]
                and paid_self < prices[gun] + sum(prices[w] for w in set(used))):
            value = max(0.0, float(prices[gun] - prices[own]))
            if value > best[0]:
                best = (value, "in_round", gun, own)
        used.append(gun)

    own_last = used[-1] if used else None
    first_next = next((e for e in ordered_events(next_events) if e.killer_id == pid), None)
    if (first_next is not None and own_last is not None
            and weapon_prices.classify(first_next.weapon) == "priced"):
        gun = first_next.weapon
        if gun != own_last and gun in teammate_gun_death and cash - next_remaining < prices[gun]:
            value = max(0.0, float(prices[gun] - prices[own_last]))
            if value > best[0]:
                best = (value, "carried", gun, own_last)
    return best


def _bonus_guard(inputs: RoundEconInputs, pistol_winner) -> tuple[str, str] | None:
    """Spec section 7, in its fixed order. Only for the bonus model in half-round 2.
    Missing inputs refuse the round; they are never read as zero or as no kills."""
    team = [p for p in inputs.players if p.team == pistol_winner]
    # A NAMED agent the table lacks is unknown; an absent agent is a missing input.
    unknown = [p.match_player_id for p in team
               if p.agent is not None and agent_economy.known_utility_cost(p.agent) is None]
    if unknown:
        return "unknown_agent_utility", f"match_player_ids {unknown}"
    missing = [f"{p.match_player_id}.agent" for p in team if p.agent is None]
    missing += [f"{p.match_player_id}.{name}" for p in team for name in ("remaining", "kills")
                if not _valid_number(getattr(p, name))]
    missing += [f"{p.match_player_id}.{name}" for p in inputs.players for name in ("deaths", "next_deaths")
                if not _valid_number(getattr(p, name))]
    reward = (inputs.next_round_reward or {}).get(pistol_winner)
    if not isinstance(inputs.planted, bool):
        missing.append("planted")
    if inputs.attacking_team not in (inputs.team_a, inputs.team_b):
        missing.append("attacking_team")
    if not _valid_number(reward):
        missing.append("next_round_reward")
    if inputs.next_events is None:
        missing.append("next_events")
    else:
        missing += [f"event {e.event_id}.weapon" for e in (*inputs.events, *inputs.next_events)
                    if not isinstance(e.weapon, str)]
    if missing:
        return "missing_bonus_inputs", ", ".join(missing)
    team_ids = {p.match_player_id for p in team}
    bad = [e.event_id for e in (*inputs.events, *inputs.next_events)
           if e.killer_id in team_ids and weapon_prices.classify(e.weapon) == "unrecognised"]
    if bad:
        return "unrecognised_weapon", f"event_ids {bad}"
    if (sum(p.deaths for p in inputs.players) > len(inputs.events)
            or sum(p.next_deaths for p in inputs.players) > len(inputs.next_events)):
        return "kill_feed_incomplete", "the deaths stat exceeds the kill events"
    return None


# ---- The round calculator -------------------------------------------------------------

def _abstain(inputs: RoundEconInputs, model: str, reason: str, detail: str | None = None):
    return RoundEconResult(model=model, audit_version=audit_version_for(model),
                           round_number=inputs.round_number, abstention=reason,
                           abstention_detail=detail)


def score_round(inputs: RoundEconInputs, model: str) -> RoundEconResult:
    """Score one round, or abstain with a reason. Guards run in a fixed order
    so a round with several defects always reports the same first reason."""
    if model not in BUY_DISRUPTION_MODELS:
        raise ValueError(f"Unknown buy-disruption model {model!r}")
    rn = inputs.round_number
    team_a, team_b = inputs.team_a, inputs.team_b

    if not inputs.use_realized:
        return _abstain(inputs, model, "ex_ante", "the component reads round N+1")
    if rn >= inputs.last_round_number:
        return _abstain(inputs, model, "final_round")
    if not is_eligible_round(rn):
        return _abstain(inputs, model, "pistol_half_or_ot_boundary")
    if not inputs.has_next_round:
        return _abstain(inputs, model, "missing_next_round")
    if any(SURRENDER_MARKER in (o or "") for o in (inputs.outcome, inputs.next_outcome)):
        return _abstain(inputs, model, "surrender")

    roster = {team_a: [], team_b: []}
    ids = [p.match_player_id for p in inputs.players]
    for player in inputs.players:
        if player.team in roster:
            roster[player.team].append(player)
    if (len(set(ids)) != len(ids) or team_a == team_b
            or sum(len(m) for m in roster.values()) != len(ids)
            or any(len(m) != ROSTER_SIZE for m in roster.values())):
        return _abstain(inputs, model, "incomplete_roster",
                        f"team sizes {[len(m) for m in roster.values()]}, {len(ids)} players")

    missing = [p.match_player_id for p in inputs.players
               if not (p.has_current_stats and p.has_next_stats)]
    if missing:
        return _abstain(inputs, model, "incomplete_stats", f"match_player_ids {missing}")

    by_id = {p.match_player_id: p for p in inputs.players}
    unknown = [e.event_id for e in inputs.events if e.victim_id is None or e.victim_id not in by_id]
    if unknown:
        return _abstain(inputs, model, "unknown_victim", f"event_ids {unknown}")

    event_ids = [e.event_id for e in inputs.events]
    if any(i is None for i in event_ids) or len(set(event_ids)) != len(event_ids):
        return _abstain(inputs, model, "invalid_event_ids", f"event_ids {event_ids}")

    pistol_winner = outcome_winner(inputs.pistol_outcome, team_a, team_b)
    if pistol_winner is None:
        return _abstain(inputs, model, "unknown_pistol_winner", repr(inputs.pistol_outcome))

    invalid = [p.match_player_id for p in inputs.players
               if not all(_valid_number(v) for v in
                          (p.loadout, p.next_loadout, p.next_remaining, p.free_ability_credits))]
    bad_times = [e.event_id for e in inputs.events
                 if not isinstance(e.time_seconds, (int, float)) or not math.isfinite(e.time_seconds)]
    if invalid or bad_times:
        return _abstain(inputs, model, "invalid_economy_data",
                        f"match_player_ids {invalid}, event_ids {bad_times}")

    half_round = half_round_index(rn)
    bonus_team = pistol_winner if (model == MODEL_V2_30_80_BONUS_DENIAL and half_round == 2) else None
    flags_bonus = []
    if bonus_team is not None:
        refused = _bonus_guard(inputs, bonus_team)
        if refused:
            return _abstain(inputs, model, *refused)
        team_ids = {p.match_player_id for p in inputs.players if p.team == bonus_team}
        flags_bonus = [f"unidentified_weapon: event {e.event_id} {e.weapon!r}"
                       for e in (*inputs.events, *inputs.next_events)
                       if e.killer_id in team_ids and weapon_prices.classify(e.weapon) == "unidentified"]

    round_winner = outcome_winner(inputs.outcome, team_a, team_b)
    paid = {pid: paid_loadout(p.loadout, p.free_ability_credits) for pid, p in by_id.items()}
    next_paid = {pid: paid_loadout(p.next_loadout, p.free_ability_credits) for pid, p in by_id.items()}

    exposures = first_loss_exposures(paid, inputs.events)
    lost: dict[int, float] = defaultdict(float)
    for event, exposure in exposures:
        lost[event.victim_id] += exposure

    budgets: dict = {}
    targets_by_player: dict[int, float] = {}
    targets_by_team: dict = {}
    for team, members in roster.items():
        member_ids = [p.match_player_id for p in members]
        targets = buy_targets([paid[m] for m in member_ids], half_round, team == pistol_winner)
        budgets[team] = team_budget(
            targets, [next_paid[m] for m in member_ids],
            [by_id[m].next_remaining for m in member_ids], [lost[m] for m in member_ids],
        )
        targets_by_team[team] = targets
        targets_by_player.update(zip(member_ids, targets))

    rates = {team: penalty_rate(b) if model in THIRTY_EIGHTY_MODELS else None for team, b in budgets.items()}
    bonus = (_bonus_denial(inputs, by_id, paid, next_paid, exposures, bonus_team, round_winner)
             if bonus_team is not None else None)
    bonus_debits: dict[int, tuple[float, float]] = defaultdict(lambda: (0.0, 0.0))

    background_credit: dict[int, float] = defaultdict(float)
    disruption_credit: dict[int, float] = defaultdict(float)
    event_ledgers = []
    flags = list(flags_bonus)
    seen_victims: set = set()
    for event, exposure in exposures:
        victim = by_id[event.victim_id]
        killer = by_id.get(event.killer_id) if event.killer_id is not None else None
        if killer is None:
            kind = "unknown_killer"
            if event.killer_id is not None:
                flags.append(f"killer_not_in_roster: event {event.event_id} killer {event.killer_id}")
        elif killer.match_player_id == victim.match_player_id:
            kind = "self"
        elif killer.team == victim.team:
            kind = "team"
        else:
            kind = "enemy"
        if kind != "enemy":
            flags.append(f"{kind}_death: event {event.event_id}")
        if event.victim_id in seen_victims:
            flags.append(f"repeated_death: event {event.event_id} counts no new kit")
        seen_victims.add(event.victim_id)

        budget = budgets[victim.team]
        in_bonus = bonus is not None and victim.team == bonus_team
        qualifying = False
        event_rate = rates[victim.team]
        if in_bonus:
            # Spec section 5: the denial REPLACES background and disruption for a
            # qualifying first death; the factor and the victim's 80% apply once each.
            vid = victim.match_player_id
            if exposure > 0 and vid in bonus.denied:
                qualifying = True
                value = bonus.factor * SWING_VALUE_PER_CREDIT * bonus.net_denied[vid] / TEAM_REFERENCE
                bg, dis = (0.0, value) if kind == "enemy" else (0.0, 0.0)
                debit_bg, debit_dis, debit_scarcity = 0.0, DISRUPTED_RATE * value, 0.0
                event_rate = DISRUPTED_RATE
            else:
                background = BACKGROUND * exposure / TEAM_REFERENCE
                bg, dis = (background, 0.0) if kind == "enemy" else (0.0, 0.0)
                debit_bg, debit_dis, debit_scarcity = ABSORBED_RATE * background, 0.0, 0.0
                event_rate = ABSORBED_RATE
            previous = bonus_debits[vid]
            bonus_debits[vid] = (previous[0] + debit_bg, previous[1] + debit_dis)
        else:
            bg, dis = event_credit(exposure, budget) if kind == "enemy" else (0.0, 0.0)
            debit_bg, debit_dis, debit_scarcity = death_debit(exposure, budget, model)
        if kind == "enemy":
            background_credit[killer.match_player_id] += bg
            disruption_credit[killer.match_player_id] += dis
        event_ledgers.append(EventLedger(
            event_id=event.event_id, time_seconds=event.time_seconds,
            killer_id=event.killer_id, victim_id=event.victim_id,
            killer_team=killer.team if killer is not None else None, victim_team=victim.team,
            kind=kind, exposure=exposure, background_credit=bg, disruption_credit=dis,
            victim_background_debit=debit_bg, victim_disruption_debit=debit_dis,
            victim_scarcity_debit=debit_scarcity, penalty_rate=event_rate,
            absorbed=(not qualifying) if in_bonus else budget.severity_pool == 0,
            bonus_qualifying=qualifying,
        ))

    ledgers: dict[int, PlayerLedger] = {}
    for pid, player in by_id.items():
        budget = budgets[player.team]
        if bonus is not None and player.team == bonus_team:
            debit_bg, debit_dis = bonus_debits[pid]
            debit_scarcity = 0.0
            player_rate = DISRUPTED_RATE if pid in bonus.denied else ABSORBED_RATE
        else:
            debit_bg, debit_dis, debit_scarcity = death_debit(lost[pid], budget, model)
            player_rate = rates[player.team]
        ledgers[pid] = PlayerLedger(
            match_player_id=pid, team=player.team, current_paid=paid[pid],
            next_loadout=player.next_loadout, next_paid=next_paid[pid],
            next_bank=player.next_remaining, target=targets_by_player[pid], lost=lost[pid],
            background_credit=background_credit[pid], disruption_credit=disruption_credit[pid],
            background_debit=debit_bg, disruption_debit=debit_dis, scarcity_debit=debit_scarcity,
            penalty_rate=player_rate,
        )

    teams = {}
    for team, members in roster.items():
        member_ids = [p.match_player_id for p in members]
        teams[team] = TeamAudit(
            team=team, budget=budgets[team], targets=targets_by_team[team],
            penalty_rate=((DISRUPTED_RATE if bonus.denied else ABSORBED_RATE)
                          if (bonus is not None and team == bonus_team) else rates[team]),
            carryover_targets=half_round == 2 and team == pistol_winner,
            pistol_winner=team == pistol_winner, round_winner=team == round_winner,
            half_round=half_round,
            deaths=sum(1 for e in event_ledgers if e.victim_team == team),
            first_loss_players=sum(1 for m in member_ids if lost[m] > 0),
            next_below_raw_4200=sum(1 for p in members if p.next_loadout < CONTEXT_FULL_BUY_RAW),
            credit=sum(ledgers[m].credit for m in member_ids),
            debit=sum(ledgers[m].debit for m in member_ids),
            bonus=bonus if team == bonus_team else None,
        )

    return RoundEconResult(
        model=model, audit_version=audit_version_for(model), round_number=rn, teams=teams,
        players=ledgers, events=tuple(event_ledgers), data_quality=tuple(flags),
    )
