import math
from collections import defaultdict
from dataclasses import dataclass

import networkx as nx
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models import ImpactScore, KillEvent, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import Team
from app.scoring import econ_buy_disruption, econ_component, round_rewards
from app.scoring.agent_economy import free_ability_credits
from app.scoring.plant_window import attacking_team as _plant_window_attacking_team
from app.scoring.plant_window import effective_plant_time
from app.scoring.plant_window import round_decided
from app.scoring.plant_window import seconds_to_plant
from app.scoring.preplant_empirical_factor import empirical_preplant_factor

# The conversion of the empirical pre-plant win-rate lift into a scoring
# multiplier (docs/superpowers/2026-09-08-preplant-empirical-factor-candidate.md).
# A policy choice, not fitted -- see enable_preplant_empirical's docstring below.
_PREPLANT_EMPIRICAL_STRENGTH = 3.0


@dataclass
class CalculatedImpact:
    """One (round, match_player)'s computed impact, with NO ORM identity and
    no session attachment. build_impact_rows_for_match returns these;
    compute_impact_for_match is the only thing that turns them into rows."""

    round_id: int
    match_player_id: int
    kill_impact: int
    death_impact: int
    impact: int
    damage: int
    econ_impact: int
    time_impact: int
    swing_impact: int
    econ_kill: int
    econ_death: int
    clutch_kill: int
    clutch_death: int
    post_plant_kill: int
    post_plant_death: int
    traded_teammate: int
    traded_by_teammate: int
    trade_detail: dict | None
    # Migration 0008 (docs/superpowers/specs/2026-09-04-econ-impact-separate-component-design.md,
    # section 8c-i / persistence). kill_order_bonus is the net kill_order_bonus
    # (no time/econ/swing multiplier) -- the harness derives time_delta =
    # time_impact - kill_order_bonus from it. econ_component/econ_pickup are
    # written as 0 for now; their real computation lands with the econ
    # component itself. Defaulted so existing positional callers keep working.
    kill_order_bonus: int = 0
    econ_component: int = 0
    # NOT persisted (absent from _PERSISTED_FIELDS): the weighted leverage
    # term as it actually enters `impact`, so a review tool can show that
    # impact == damage + leverage_component + econ_component exactly. The
    # `time_impact` column stays UNWEIGHTED -- the eval harness derives
    # time_delta from it and reweighting it would redefine a column
    # consumers read by name.
    leverage_component: int = 0
    econ_pickup: int = 0
    # NOT persisted, like leverage_component: D * assists as it enters `impact`.
    assists_component: int = 0
    # Persisted from migration 0010: the IMPACT_CALCULATION_VERSION that
    # computed this row, stamped by the builder below. Provenance for stored
    # rows -- the release write gate, not this value, decides who may write.
    scoring_version: int = 0
    # NOT persisted: B * the trade credit, the part of leverage_component paid
    # for being traded (enable_trade_credit).
    trade_credit: int = 0


@dataclass(frozen=True)
class ImpactInputIssue:
    kind: str
    round_number: int
    event_id: int | None = None
    match_player_id: int | None = None
    detail: str = ""


class ImpactInputError(ValueError):
    """A match whose combat inputs cannot be scored, raised before any row is
    built or persisted. Structured so a backfill can record exactly which
    event or player blocked it."""

    def __init__(self, match_id: int, issues):
        self.match_id = match_id
        self.issues = tuple(issues)
        super().__init__(f"match {match_id}: " + "; ".join(
            f"{i.kind} (round {i.round_number}, event {i.event_id}, player {i.match_player_id})"
            for i in self.issues))


# Bump whenever compute_impact_for_match's scoring algorithm changes in a way
# that changes ImpactScore values for previously-scored rounds -- folded
# MECHANICALLY into app.services.player_view_cache.cache_version() (Step 3b,
# docs/player_page_render_speed.txt) so a player_view_cache row computed from
# stale ImpactScore rows never outlives a rescoring. Same rationale/pattern
# as app.services.fight_ev.CALCULATION_VERSION.
# 2 (2026-09-10): the trade-cost schedule replaced `trade_time / 10`, the trade
# window closed from 10s to 6s for both the discount and the displayed count,
# and a killer who dies to their OWN side no longer trades the victim back.
# Every stored ImpactScore row predates this and needs a rescore.
# 3 (2026-09-17): activates the frozen rc3 manifest named by
# app.scoring.impact_runtime.ACTIVE_MANIFEST -- A 1 (damage) / B 2.5 (leverage) /
# C 2.5 (econ) / D 100 (assists), trade credit on at scale 1.0, econ model
# buy_disruption_v2_30_80_bonus_denial, realized swing, timing candidates off.
# Version 2 was never loaded into any database: production's stored rows are
# version 1, and they are replaced wholesale by the rc3 swap rather than
# rescored in place.
IMPACT_CALCULATION_VERSION = 3

_KILL_ORDER_GRAPH = nx.DiGraph()
_KILL_ORDER_GRAPH.add_weighted_edges_from(
    [
        ("5v5", "4v5", 150),
        ("5v5", "5v4", 150),
        ("4v5", "3v5", 130),
        ("4v5", "4v4", 140),
        ("5v4", "4v4", 140),
        ("5v4", "5v3", 130),
        ("3v5", "2v5", 90),
        ("3v5", "3v4", 120),
        ("4v4", "3v4", 170),
        ("4v4", "4v3", 170),
        ("5v3", "4v3", 120),
        ("5v3", "5v2", 90),
        ("2v5", "1v5", 50),
        ("2v5", "2v4", 70),
        ("3v4", "2v4", 130),
        ("3v4", "3v3", 160),
        ("4v3", "3v3", 160),
        ("4v3", "4v2", 130),
        ("5v2", "4v2", 70),
        ("5v2", "5v1", 50),
        ("1v5", "0v5", 40),
        ("1v5", "1v4", 60),
        ("2v4", "1v4", 80),
        ("2v4", "2v3", 130),
        ("3v3", "2v3", 180),
        ("3v3", "3v2", 180),
        ("4v2", "3v2", 130),
        ("4v2", "4v1", 80),
        ("5v1", "4v1", 60),
        ("5v1", "5v0", 40),
        ("1v4", "0v4", 50),
        ("1v4", "1v3", 70),
        ("2v3", "1v3", 140),
        ("2v3", "2v2", 170),
        ("3v2", "2v2", 170),
        ("3v2", "3v1", 140),
        ("4v1", "3v1", 70),
        ("4v1", "4v0", 50),
        ("1v3", "0v3", 70),
        ("1v3", "1v2", 120),
        ("2v2", "1v2", 200),
        ("2v2", "2v1", 200),
        ("3v1", "2v1", 120),
        ("3v1", "3v0", 70),
        ("1v2", "0v2", 130),
        ("1v2", "1v1", 190),
        ("2v1", "1v1", 190),
        ("2v1", "2v0", 130),
        ("1v1", "0v1", 250),
        ("1v1", "1v0", 250),
    ]
)


FORCE_THRESHOLD = 3250
FULL_BUY_THRESHOLD = 4200
WIN_BONUS = 3000
SURVIVE_LOSS_BONUS = 1000  # documented for completeness; see _econ_swing_risk_factor
KILL_REWARD = 200
PLANT_BONUS = 300

# Relative importance of each factor when combining them into kill_impact/death_impact.
# Equal weights would reproduce a plain average; these are a starting proposal, not a
# final tuning -- adjust freely.
FACTOR_WEIGHTS = {
    "econ": 1.0,
    "time": 1.0,
    "swing": 1.0,
}
_FACTOR_WEIGHT_TOTAL = math.fsum(FACTOR_WEIGHTS.values())


@dataclass(frozen=True)
class FormulaWeights:
    """A, B, C and D of the new structure:

        impact = A*damage + B*(kill_order_bonus x time_factor) + C*econ_component + D*assists

    The defaults ARE today's declared candidate, so FormulaWeights() changes
    nothing: 1.25 is the literal the damage term has always carried, and the
    other two are 1.0 because neither term was ever weighted.

    `econ` multiplies ON TOP of ECON_SCALE, which is a dispersion anchor
    (econ_component's SD matched to time_impact's), not a fitted weight. So
    C = 1.0 means "the declared anchor", and C is dimensionless around it.

    `assists` (D) is a flat amount per assist on the raw assist count. Nothing
    is carved out of the damage term for it: damage keeps Valorant's 25 for
    each non-damaging assist, so it cannot go negative (owner, 2026-09-14). It
    defaults to 0, so a candidate that does not set it pays nothing extra.

    `damage` is the only one that also reaches the LEGACY branch, because
    `damages` is shared; the other three weight terms that exist only in the
    new structure and are inert when enable_econ_component is False.

    `trade_credit_scale` multiplies trade_credit_x_time (enable_trade_credit)
    BEFORE `leverage` does, so it stacks with B rather than replacing it: the
    schedule's 60%-to-30% shares stay B's usual leverage-vs-other-terms ratio,
    and this knob only turns trade credit itself up or down against that.
    Defaults to 1.0, so a candidate that does not set it scores identically to
    one with no scale term at all.
    """

    damage: float = 1.25
    leverage: float = 1.0
    econ: float = 1.0
    assists: float = 0.0
    trade_credit_scale: float = 1.0


_ECON_TIER_CODES = {"SAVE": 8, "ECO": 6, "FORCE": 5, "FULL_BUY": 4}


def econ_tier_name(loadout: int) -> str:
    if loadout < 1000:
        return "SAVE"
    if loadout < FORCE_THRESHOLD:
        return "ECO"
    if loadout < FULL_BUY_THRESHOLD:
        return "FORCE"
    return "FULL_BUY"


def _categorize_econ(loadout: int) -> int:
    return _ECON_TIER_CODES[econ_tier_name(loadout)]


def _kill_order_bonus(team1_kill_index: int, team2_kill_index: int, kill_team: Team, self_kill: bool) -> float:
    before_node = f"{team1_kill_index}v{team2_kill_index}"
    if self_kill:
        if kill_team == Team.TEAM_1:
            team2_kill_index -= 1
        else:
            team1_kill_index -= 1
    else:
        if kill_team == Team.TEAM_1:
            team1_kill_index -= 1
        else:
            team2_kill_index -= 1
    after_node = f"{team1_kill_index}v{team2_kill_index}"

    try:
        return _KILL_ORDER_GRAPH[before_node][after_node]["weight"]
    except KeyError:
        return 100


def _time_factor(
    round_row: Round, kill_time: float, for_death: bool = False,
    is_attacker: bool | None = None, enable_preplant_empirical: bool = False,
    use_realized: bool = True, alive_counts: tuple[int, int] | None = None,
    postplant_factor_table=None, enable_postplant_leverage: bool = False,
    self_kill: bool = False, decided_only: bool = False,
) -> float:
    # Impact v4 (declaration 12, arm N; enable_decided_only_time). NO time
    # factor: every kill and death is worth 1.0 before and after the plant --
    # no ramp, no plant+38..45 override -- and 0.0 once the round is decided.
    # It comes BEFORE every other branch, the legacy exploded/defused 0.5
    # included, because it replaces the whole function rather than adjusting
    # one regime of it. A self-kill's or environmental death's DEATH side
    # follows the same rule: the caller never evaluates the kill side for one.
    if decided_only:
        return 0.0 if round_decided(round_row, kill_time) else 1.0

    # Mirrors the original's chronological state machine (planted/plantedTime/
    # exploded/defused flags updated as the event log is walked), reconstructed
    # from the round's final planted/plant_time/exploded/defuse_time. The
    # post-plant window and ramp only apply once a plant has actually happened
    # in this round, and only to kills at or after the plant.
    #
    # PHANTOM PLANTS (review finding 5). This raw read is DELIBERATELY not
    # routed through plant_window.effective_plant_time, which additionally
    # excludes a planted round decided by the round timer (a "Time Win" -- a
    # plant that never armed for real). Every branch below that reads
    # `plant_time` is TODAY'S SHIPPED SCORING: the exploded/defused early
    # return, the plant+38..45 override and the ramp. Routing them through the
    # helper would change stored Impact for 76 rounds without a version bump,
    # and would silently move arm 0, the reference arm of the five-arm report.
    # The flag-gated paths -- and only those -- ask the helper instead, below.
    plant_time = round_row.plant_time if round_row.planted else None

    exploded_effective = round_row.exploded and plant_time is not None and kill_time >= plant_time + 45
    defused_effective = (
        round_row.defused and round_row.defuse_time is not None and kill_time >= round_row.defuse_time
    )
    if exploded_effective or defused_effective:
        return 0.5

    # Part 4 (spec, "Part 4 -- the post-plant regime"). When enabled, the
    # measured leverage ratio REPLACES both the side-blind ramp and the
    # flat plant+38..45 override: the ramp is backwards for attacker kills
    # and for defender deaths, and the override pays 1.75 to both sides at
    # the moment their stakes are furthest apart (in a 1v1 at t=38 the
    # attacker carries 10x the defender's risk, M26). "No hard
    # discontinuity at plant+38" -- the measured shape already contains
    # both deadlines and does not need either hard-coded.
    #
    # Nothing here is gated by use_realized: at a post-plant kill the
    # plant has already happened, so seconds-since-plant, the alive counts
    # and the side are all known at kill time. That is the mirror of Part
    # 3's leakage gate and it is exact -- V's own fitting leakage is
    # handled by the out-of-fold table in evaluation, not by this switch.
    #
    # Hoisted ABOVE the legacy post-plant block so it can decline an event the
    # legacy block still claims. Two declines, both because the factor was
    # never estimated on that event and the centring constant therefore does
    # not cover it -- in both cases the event falls through to the legacy
    # branches, exactly as arm 0 scores it:
    #   * finding 5 -- a phantom plant has no effective plant time, and
    #     extract_postplant_kills drops those rounds from the fit;
    #   * finding 6 -- a self-kill's killer and victim are the same player, so
    #     `not is_attacker` would name the WRONG side, and
    #     extract_postplant_kills drops self-kills from the fit too.
    # For a genuine plant this gate is character-for-character equivalent to
    # the legacy `plant_time is not None and kill_time >= plant_time` it used
    # to sit inside: effective_plant_time returns plant_time unchanged there.
    if (
        enable_postplant_leverage and postplant_factor_table is not None
        and alive_counts is not None and is_attacker is not None
        and not self_kill
    ):
        effective_plant = effective_plant_time(round_row)
        if effective_plant is not None and kill_time >= effective_plant:
            attackers_alive, defenders_alive = alive_counts
            return postplant_factor_table.factor(
                attackers_alive, defenders_alive, int(kill_time - effective_plant),
                # is_attacker describes the KILLER, so the victim is on the
                # other side. D is keyed on the VICTIM's side -- that split is
                # the entire reason the factor is side-dependent. Sound only
                # because self-kills are excluded above.
                victim_is_attacker=not is_attacker,
            )

    if plant_time is not None and kill_time >= plant_time:
        if plant_time + 38 <= kill_time <= plant_time + 45:
            # A kill in this window is denying/clutching a near-explosion round, so
            # it's highly valuable. A death in this window isn't the mirror-image
            # punishment -- the round is basically already decided in the killer's
            # favor by then -- so it gets the same discount as a death after the
            # round has already been decided (exploded/defused).
            return 0.5 if for_death else 1.75
        return 1 + (kill_time - plant_time) / 53

    # Pre-plant. Legacy flat 1.0 unless the empirical timing modifier is
    # explicitly enabled (docs/superpowers/2026-09-08-preplant-empirical-
    # factor-candidate.md). enable_preplant_empirical defaults False and is
    # NOT the Part 3 spec's model-based scalar (app.scoring.preplant_scalar,
    # still dormant/unwired) -- this is a separate, explicitly non-monotone,
    # uncentered empirical curve wired in at the user's direction. It does
    # not satisfy Part 3's Testing section monotonicity assertion; that
    # tension is recorded, not resolved, here. NEVER flip this default as
    # part of an unrelated change -- activating it is a deliberate rollout
    # decision (version bump + rescore), same as the model-based scalar.
    #
    # Finding 5, pre-plant half: seconds-to-plant is measured against the
    # EFFECTIVE plant. On a phantom plant the curve would otherwise be read at
    # a distance from a plant that never armed, and the curve was fitted on
    # "non-self pre-plant kills in a non-phantom round" (spec, Part 3
    # Observations) -- a population these 537 kills are not in. They fall
    # through to the flat legacy 1.0 below.
    if enable_preplant_empirical and is_attacker is not None:
        effective_plant = effective_plant_time(round_row)
        if effective_plant is not None:
            return empirical_preplant_factor(
                effective_plant - kill_time, is_attacker,
                strength=_PREPLANT_EMPIRICAL_STRENGTH, use_realized=use_realized,
            )
    return 1


# The trade-cost schedule, declared by the project owner 2026-09-10. Each pair
# is (upper bound in seconds, COST multiplier charged on the traded death); a
# trade at or beyond TRADE_WINDOW_SECONDS is not a trade at all and the death is
# charged in full.
#
# Replaces `trade_time / 10`, which was linear, free at t=0, and still granted a
# discount at 9s. Two deliberate changes: a traded death is never free, because
# you did still die; and slower trades are forgiven substantially less.
TRADE_COST_SCHEDULE: tuple[tuple[float, float], ...] = (
    (1.0, 0.05),
    (2.0, 0.10),
    (3.0, 0.17),
    (4.0, 0.35),
    (5.0, 0.50),
    (6.0, 0.75),
)
TRADE_WINDOW_SECONDS = 6.0

# The trade CREDIT schedule, declared by the project owner 2026-09-14: each pair
# is (upper bound in seconds, share of the TRADE KILL's leverage credited to the
# player who was traded), timed from that player's own death. It pays for the
# space and pressure an entry creates -- a Clove killed in her ult and traded
# gets it too. Added on top of the trader's kill, never taken from it.
TRADE_CREDIT_SCHEDULE: tuple[tuple[float, float], ...] = (
    (1.0, 0.60),
    (2.0, 0.54),
    (3.0, 0.48),
    (4.0, 0.42),
    (5.0, 0.36),
    (6.0, 0.30),
)


def _trade_kill(round_kills: list[dict], checking_kill: dict, team_of: dict | None = None):
    """(trade kill, seconds after the death) for a death, or None if it was not
    traded: the first death of its killer inside TRADE_WINDOW_SECONDS that is
    not a kill by the killer's own side. A self or environmental death of the
    killer counts. See _traded_factor for the rulings behind each clause."""
    killer_id = checking_kill["killer_match_player_id"]
    death_time = checking_kill["event_time_seconds"]
    for kill in round_kills:
        if kill["death_match_player_id"] == killer_id:
            trade_time = kill["event_time_seconds"] - death_time
            if 0 <= trade_time < TRADE_WINDOW_SECONDS:
                if team_of is not None:
                    avenger = kill["killer_match_player_id"]
                    if (
                        avenger is not None
                        and avenger != killer_id
                        and team_of.get(avenger) == team_of.get(killer_id)
                    ):
                        continue  # team-kill: not a trade, keep looking
                return kill, trade_time
    return None


def _traded_factor(
    round_kills: list[dict], checking_kill: dict, self_kill: bool,
    team_of: dict | None = None,
) -> float:
    """The COST multiplier charged on a traded death, from TRADE_COST_SCHEDULE.

    A traded death is never free -- 5% is the floor, because you did still die
    -- and a gap of TRADE_WINDOW_SECONDS or more is not a trade at all.

    `team_of` maps match_player_id -> team. When it is given, a killer who dies
    to their OWN side does not trade the victim back -- nobody on the victim's
    team avenged them (project owner's ruling, 2026-09-10). A killer who
    self-kills or falls to the environment DOES still count as a trade, which
    is the larger population and is deliberately kept.

    The search continues past a team-kill rather than stopping, so a killer who
    is team-killed, revived, and then killed again by the victim's own side
    inside the window is still a trade.

    Omitting `team_of` skips the team check only. It does NOT restore the old
    `trade_time / 10` curve or the 10s window, so docs/superpowers/diagnostics
    (the one caller that omits it) no longer reproduces the numbers it
    recorded -- re-derive rather than re-run it if those are needed again.
    """
    if self_kill:
        return 1

    traded = _trade_kill(round_kills, checking_kill, team_of)
    if traded is None:
        return 1
    _, trade_time = traded
    for upper, cost in TRADE_COST_SCHEDULE:
        if trade_time < upper:
            return cost
    return 1


def _trade_credits_for_round(round_kills: list[dict], team_of: dict) -> dict[int, float]:
    """{match_player_id: unweighted leverage credit} for players who were traded.

    Needs each kill's `kill_order_bonus_x_time` already set. Only a death to an
    enemy earns credit, and only a trade kill by a teammate pays it (a killer
    who dies to themself or the environment is still a trade for the death
    discount, but there is no teammate's kill to share). When one trade kill
    avenges several teammates, each timed share is scaled by max/sum, so the
    credited shares add up to the fastest one's."""
    by_trade: dict[int, list[tuple[dict, int, float]]] = defaultdict(list)
    for kill in round_kills:
        killer_id, victim_id = kill["killer_match_player_id"], kill["death_match_player_id"]
        if killer_id is None or killer_id == victim_id or team_of[killer_id] == team_of[victim_id]:
            continue
        traded = _trade_kill(round_kills, kill, team_of)
        if traded is None:
            continue
        trade, trade_time = traded
        avenger = trade["killer_match_player_id"]
        if avenger is None or avenger == trade["death_match_player_id"]:
            continue
        share = next(share for upper, share in TRADE_CREDIT_SCHEDULE if trade_time < upper)
        by_trade[id(trade)].append((trade, victim_id, share))

    credits: dict[int, float] = defaultdict(float)
    for avenged in by_trade.values():
        shares = [share for _, _, share in avenged]
        # math.fsum, not sum(): Python 3.12 made sum() compensated, and these
        # shares ([0.3, 0.36, 0.42] in match 89) summed to 1.0799999999999998 on
        # 3.11 and 1.08 on 3.13, which moved a credit across a rounding boundary.
        # Exact summation is the same on every interpreter.
        scale = max(shares) / math.fsum(shares)
        for trade, victim_id, share in avenged:
            credits[victim_id] += share * scale * trade["kill_order_bonus_x_time"]
    return credits


def _players_by_name(db: Session, match_id: int) -> dict[str, list[int]]:
    """{lowercased Player.display_name: [match_player_id, ...]} for one match.

    Assistants are stored per kill as Riot IDs (kill_events.source_meta), and
    they are matched to this match's players case-insensitively, as declaration
    12's N+A arm measured. A list, not a single id, so a case-insensitive
    collision is SEEN as ambiguous rather than resolved by whichever row the
    query happened to return last (the measured reference's dict did that; on
    the measurement corpus there are no collisions, declaration 13)."""

    by_name: dict[str, list[int]] = defaultdict(list)
    rows = (db.query(MatchPlayer.id, Player.display_name)
            .join(Player, Player.id == MatchPlayer.player_id)
            .filter(MatchPlayer.match_id == match_id)
            .order_by(MatchPlayer.id))
    for match_player_id, name in rows:
        by_name[(name or "").lower()].append(match_player_id)
    return by_name


def _remove_post_decided_assists(
    round_row, kill: dict, assistants, players_by_name: dict[str, list[int]],
    round_stats: dict[int, dict], removed: dict[int, int],
) -> dict:
    """Impact v4 (declaration 12, arm N+A; remove_post_decided_assists).

    Each assistant on a kill made after the round was decided loses one assist
    from the assists component, counted into `removed` ({match_player_id: n}).
    Returns what happened, for kill_observer; nothing returned feeds a score.

    CLAMP (declared in declaration 13 as a defensive extension, plan R10): a
    player never loses more assists than their scoreboard row records, so the
    component cannot go below zero. The measured reference subtracted without
    a clamp; on the measurement corpus the clamp fires on no row. A removal the
    clamp refuses is reported as `clamped`.

    An assistant who maps to no player (`unmapped`) or to more than one
    (`ambiguous`) is left in and reported."""
    decided = round_decided(round_row, kill["event_time_seconds"])
    report = {"decided": decided, "removed": [], "clamped": [], "unmapped": [], "ambiguous": []}
    if not decided:
        return report
    for name in assistants or ():
        match_player_ids = players_by_name.get(str(name).lower(), [])
        if not match_player_ids:
            report["unmapped"].append(name)
            continue
        if len(match_player_ids) > 1:
            report["ambiguous"].append(name)
            continue
        (match_player_id,) = match_player_ids
        available = (round_stats.get(match_player_id) or {}).get("assists") or 0
        if removed.get(match_player_id, 0) < available:
            removed[match_player_id] = removed.get(match_player_id, 0) + 1
            report["removed"].append(match_player_id)
        else:
            report["clamped"].append(match_player_id)
    return report


def _clutch_bucket(own_alive: int, opp_alive: int) -> bool:
    # Priority order so a kill is only ever classified into one bucket:
    # lone survivor (1vX), an even state resolving to a man advantage (XvX),
    # or fighting back from a 2-vs-3-or-more disadvantage (2vX, X > 2).
    if own_alive == 1:
        return True
    if own_alive == opp_alive:
        return True
    if own_alive == 2 and opp_alive > 2:
        return True
    return False


# A kill with one of these proves its killer was alive when it landed. Lingering
# utility (Showstopper, Orbital Strike, Boom Bot, turrets, mollies) can kill after
# its user has died, and generic feed names ("Weapon", "Primary", "Unknown") could
# be either, so a kill with anything else proves nothing about the killer.
_KILL_PROVES_KILLER_ALIVE = frozenset({
    "Classic", "Shorty", "Frenzy", "Ghost", "Sheriff", "Stinger", "Spectre", "Bucky", "Judge",
    "Bulldog", "Guardian", "Phantom", "Vandal", "Marshal", "Outlaw", "Operator", "Ares", "Odin",
    "Bandit", "Melee", "Blade Storm", "Overdrive", "Headhunter", "Tour De Force",
})


def _present_players(round_stats: dict[int, dict], round_kills: list[dict], team_of: dict[int, Team]) -> dict[Team, int]:
    """Players actually in the round, per team: five, less anyone whose row
    shows they were absent. A disconnected or AFK player's row is all zeros --
    no score, kills, deaths, assists or loadout -- and Valorant's combat-score
    kill bonus does not count them as alive. Anyone in the kill feed was in the
    round whatever their row says, and a missing row is not evidence of absence."""
    in_feed = {pid for kill in round_kills
               for pid in (kill["killer_match_player_id"], kill["death_match_player_id"]) if pid is not None}
    present = {Team.TEAM_1: 5, Team.TEAM_2: 5}
    for match_player_id, stats in round_stats.items():
        took_part = any(stats[key] for key in ("score", "kills", "deaths", "assists", "loadout"))
        if not took_part and match_player_id not in in_feed:
            present[team_of[match_player_id]] -= 1
    return present


def _round_stats_for_presence(db) -> dict[int, dict[int, dict]]:
    """{round_id: {match_player_id: row}} for _present_players, for replays
    that load the whole corpus with raw SQL instead of one match."""
    out: dict[int, dict[int, dict]] = defaultdict(dict)
    for row in db.execute(text(
        "SELECT round_id, match_player_id, score, kills, deaths, assists, loadout FROM round_player_stats"
    )).mappings():
        out[row["round_id"]][row["match_player_id"]] = dict(row)
    return out


def _scoreable_kills(round_kills: list[dict], team_of: dict) -> list[dict]:
    """The kills an alive-count walk can place: a known victim, and a killer
    that is known or absent (an environmental death)."""
    return [
        kill for kill in round_kills
        if kill["death_match_player_id"] in team_of
        and (kill["killer_match_player_id"] is None or kill["killer_match_player_id"] in team_of)
    ]


def _alive_before_each_kill(
    round_kills: list[dict], team_of: dict[int, Team], present: dict[Team, int],
) -> list[dict[Team, int]]:
    """Each team's alive count just before every kill of a round.

    A death removes a player from the victim's team -- a team kill included.
    A revive is inferred from the feed, which carries no revive events: a dead
    player is back when they next die, or when they next kill with something
    that needs its user alive, strictly after their death (a kill logged at the
    same instant is a trade). They count as dead until that appearance, the
    earliest moment the feed proves them alive. Against the raw captures'
    per-kill player positions this reproduces the true count on 1,286 of 1,289
    kills; the three misses are revived players back up before the feed shows it.
    """
    alive = dict(present)
    died_at: dict[int, float] = {}
    before = []
    for kill in round_kills:
        killer_id, victim_id = kill["killer_match_player_id"], kill["death_match_player_id"]
        when = kill["event_time_seconds"]
        if victim_id in died_at:
            alive[team_of[victim_id]] += 1
            del died_at[victim_id]
        if (killer_id in died_at and killer_id != victim_id
                and kill["weapon"] in _KILL_PROVES_KILLER_ALIVE and when > died_at[killer_id]):
            alive[team_of[killer_id]] += 1
            del died_at[killer_id]
        before.append(dict(alive))
        alive[team_of[victim_id]] -= 1
        died_at[victim_id] = when
    return before


def _did_team_win(outcome: str, team: Team) -> bool:
    team_letter = outcome.split("Team ")[1][0]
    return (team_letter == "A" and team == Team.TEAM_1) or (team_letter == "B" and team == Team.TEAM_2)


def _rounds_since_last_win(round_outcomes: dict[int, str], round_number: int, team: Team) -> int:
    loss_streak = 0
    r = round_number - 1
    while r > 0:
        if _did_team_win(round_outcomes[r], team):
            return loss_streak
        loss_streak += 1
        r -= 1
    return loss_streak


def _min_next_round_econ_bonus(round_outcomes: dict[int, str], round_number: int, team: Team) -> int:
    loss_streak = _rounds_since_last_win(round_outcomes, round_number, team)
    if loss_streak == 0:
        return 1900
    if loss_streak == 1:
        return 2400
    return 2900


def _attacking_team(round_number: int) -> Team | None:
    # Thin wrapper around the one consolidated attacking-side helper (Part 1,
    # docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md).
    # _econ_swing_risk_factor early-returns for round_number > 24 before ever
    # consulting this, so extending it to cover OT cannot change any score
    # through this path -- kept only so the module has no second copy of the
    # convention.
    return _plant_window_attacking_team(round_number)


def _econ_swing_risk_factor(
    round_outcomes: dict[int, str],
    round_player_stats: dict[int, dict[int, dict]],
    match_players: dict[int, MatchPlayer],
    round_number: int,
    team: Team,
    round_row: Round,
) -> float:
    if round_number in (1, 13):
        return 1.5
    if round_number in (12, 24):
        return 1
    if round_number > 24:
        return 1

    loadout_threshold = FULL_BUY_THRESHOLD
    vandal_cost = 2900
    econ_bonus = _min_next_round_econ_bonus(round_outcomes, round_number, team)
    plant_bonus = PLANT_BONUS if round_row.planted and _attacking_team(round_number) == team else 0

    cant_buy_next = 0
    can_buy_next = 0
    can_buy_if_win = 0
    can_buy_double = 0
    can_buy_if_win_double = 0
    need_to_buy_next = 0
    bought_in = 0

    for match_player_id, stat in round_player_stats[round_number].items():
        if match_players[match_player_id].team != team:
            continue

        # Kill reward and plant bonus are paid out between rounds, so they're
        # already-known money this player will have next round on top of
        # whatever's left in "remaining" -- unlike the win/loss bonus, which
        # depends on next round's still-undecided outcome.
        known_next_round_extra = stat["kills"] * KILL_REWARD + plant_bonus
        remaining = stat["remaining"] + known_next_round_extra
        need_to_buy_next += 1 if stat["deaths"] > 0 else 0
        current_loadout = stat["loadout"]

        if remaining + econ_bonus < loadout_threshold:
            cant_buy_next += 1
        if remaining + econ_bonus >= loadout_threshold:
            can_buy_next += 1
        if remaining + WIN_BONUS >= loadout_threshold:
            can_buy_if_win += 1
        if remaining + econ_bonus >= loadout_threshold + vandal_cost:
            can_buy_next -= 1
            can_buy_double += 1
        if remaining + WIN_BONUS >= loadout_threshold + vandal_cost:
            can_buy_if_win -= 1
            can_buy_if_win_double += 1
        if current_loadout >= loadout_threshold:
            bought_in += 1

    swing_factor = round((bought_in + cant_buy_next - can_buy_double + 3) * 0.01, 2)

    if can_buy_next + 2 * can_buy_double >= 5:
        low_risk = 0.7 - round((can_buy_next + 2 * can_buy_double) * 0.05, 2)
        return round(low_risk + bought_in * swing_factor, 1)

    if round_number in (2, 14):
        swing_factor = 0.15
        return round(1 + (cant_buy_next * swing_factor) + ((need_to_buy_next - can_buy_next) * 0.1), 2)

    if can_buy_next + can_buy_double < 5:
        return round(
            1
            + (0.67 * (bought_in * swing_factor) + (cant_buy_next * swing_factor))
            + ((need_to_buy_next - (can_buy_if_win + 2 * can_buy_if_win_double)) * swing_factor),
            2,
        )

    return 1


def _realized_econ_swing_factor(
    round_player_stats: dict[int, dict[int, dict]],
    match_players: dict[int, MatchPlayer],
    round_number: int,
    team: Team,
) -> float:
    # Valorant resets economy at halftime, so there's no real next-round link to
    # check out of the last round of a half, or past the modeled OT boundary.
    if round_number in (12, 24) or round_number > 24:
        return 1.0

    next_round_stats = round_player_stats.get(round_number + 1)
    if not next_round_stats:
        return 1.0

    denied_count = 0
    for match_player_id, stat in next_round_stats.items():
        if match_players[match_player_id].team != team:
            continue
        if stat["loadout"] < FULL_BUY_THRESHOLD:
            denied_count += 1

    # Symmetric to _econ_swing_risk_factor's ~0.5-1.5 range: 0 denied -> 0.5 (team
    # recovered fully), 5 denied -> 1.5 (fully denied), 2.5 -> neutral midpoint.
    return round(0.5 + 0.2 * denied_count, 2)


def _combine_swing_factors(swing: float, realized: float) -> float:
    # Only trust a combined signal when the pre-round prediction and the actual
    # next-round outcome agree on direction (both fragile or both safe) -- if they
    # disagree, neither claim survives, so treat the round as neutral. Realized is
    # ground truth, so when they do agree, take whichever magnitude is larger.
    swing_above = swing > 1
    realized_above = realized > 1
    if swing == 1 or realized == 1 or swing_above != realized_above:
        return 1.0
    return max(swing, realized, key=lambda x: abs(x - 1))


def find_unscored_match_ids(db: Session) -> list[int]:
    """Match IDs that have MatchPlayer rows (i.e. were fully ingested) but no
    ImpactScore rows at all -- the signature of a match whose data committed
    successfully but compute_impact_for_match then never ran or didn't finish
    (e.g. the ingesting process was killed/crashed between the two, which
    trackergg_browserstate_source.py's cache-invalidation-before-scoring
    ordering already anticipates as a possible outcome). A match with only
    *some* players/rounds scored isn't included here -- that would be a
    different, partial failure this hasn't been seen to produce; this only
    catches the "scoring never started at all" case.
    """
    return [
        match_id
        for (match_id,) in (
            db.query(MatchPlayer.match_id)
            .outerjoin(ImpactScore, ImpactScore.match_player_id == MatchPlayer.id)
            .group_by(MatchPlayer.match_id)
            .having(func.count(ImpactScore.round_id) == 0)
            .all()
        )
    ]


def _econ_components_for_round(
    round_number: int, is_final_round: bool, kills: list[dict],
    match_players: dict[int, MatchPlayer],
    round_player_stats: dict[int, dict[int, dict]],
    use_realized: bool,
    econ_observer=None,
) -> dict[int, float]:
    """econ_component per match_player for one round (econ spec, sections
    4-7). Returns {} when the round abstains, so callers write 0.

    Guards run BEFORE any division, and abstention is zero credit -- never
    the 0.5 baseline, and never an average taken over whichever rows happen
    to exist.
    """
    if not use_realized:
        return {}  # the leakage gate: this component reads round N+1
    if is_final_round:
        return {}
    if not (econ_component.is_early_regime(round_number)
            or econ_component.is_late_regime(round_number)):
        return {}  # pistols, halftime, overtime

    this_round = round_player_stats.get(round_number) or {}
    next_round = round_player_stats.get(round_number + 1) or {}
    if not next_round:
        return {}

    roster: dict[Team, list[int]] = defaultdict(list)
    for match_player_id, mp in match_players.items():
        roster[mp.team].append(match_player_id)

    committed: dict[int, float] = {}
    for match_player_id in match_players:
        stat = this_round.get(match_player_id)
        if stat is None:
            return {}  # a roster member missing a round N record -> abstain
        committed[match_player_id] = econ_component.committed_value(
            stat["loadout"], match_players[match_player_id].agent
        )

    econ_round_by_team: dict[Team, float] = {}
    for team, members in roster.items():
        enemies = [m for m in match_players if match_players[m].team != team]
        if any(m not in next_round for m in enemies):
            econ_round_by_team[team] = 0.0
            continue
        below = sum(
            1 for m in enemies if next_round[m]["loadout"] < econ_component.FULL_BUY_THRESHOLD
        )
        wealth = sum(next_round[m]["loadout"] + next_round[m]["remaining"] for m in enemies)
        mean_committed = math.fsum(committed[m] for m in enemies) / len(enemies) if enemies else None
        econ_round_by_team[team] = econ_component.econ_round(econ_component.EconRoundInputs(
            round_number=round_number, is_final_round=is_final_round,
            enemy_below_full_buy_next=below, enemy_wealth_next=wealth,
            enemy_roster_size=len(enemies), enemy_mean_committed=mean_committed,
        ))

    removed: dict[int, float] = defaultdict(float)
    lost: dict[int, float] = defaultdict(float)
    for kill in kills:
        killer_id = kill["killer_match_player_id"]
        victim_id = kill["death_match_player_id"]
        if killer_id not in match_players or victim_id not in match_players:
            continue
        # Self-kills and environmental deaths are excluded ENTIRELY -- no
        # credit and no debit. They are not transfers: no enemy gains from
        # them, and they do not appear in removed(opp), so section 6's
        # denominator cannot express them.
        if killer_id == victim_id:
            continue
        if match_players[killer_id].team == match_players[victim_id].team:
            continue
        removed[killer_id] += committed[victim_id]
        lost[victim_id] += committed[victim_id]

    attributions = econ_component.attribute_econ(
        [
            econ_component.PlayerRemoval(
                player=match_player_id, team=match_players[match_player_id].team,
                removed=removed.get(match_player_id, 0.0),
                lost=lost.get(match_player_id, 0.0),
            )
            for match_player_id in match_players
        ],
        econ_round_by_team=econ_round_by_team,
    )
    scaled = {
        match_player_id: econ_component.ECON_SCALE * attribution.value
        for match_player_id, attribution in attributions.items()
    }

    if econ_observer is not None:
        # REPORTING ONLY -- the return value above is already final. Reports
        # the whole division so a review can show where each player's econ
        # came from: the team magnitude, its denominator, and each player's
        # numerator on both the credit and the debit side.
        removed_by_team: dict = {}
        for match_player_id, mp in match_players.items():
            removed_by_team[mp.team] = (
                removed_by_team.get(mp.team, 0.0) + removed.get(match_player_id, 0.0)
            )
        econ_observer(
            round_number=round_number,
            econ_round_by_team=dict(econ_round_by_team),
            removed_by_team=removed_by_team,
            players={
                match_player_id: {
                    "team": match_players[match_player_id].team,
                    "committed": committed.get(match_player_id, 0.0),
                    "removed": removed.get(match_player_id, 0.0),
                    "lost": lost.get(match_player_id, 0.0),
                    "credit": attribution.credit,
                    "debit": attribution.debit,
                    "value": attribution.value,
                    "scaled": scaled[match_player_id],
                }
                for match_player_id, attribution in attributions.items()
            },
        )
    return scaled


_ECON_MODELS = (
    frozenset({econ_buy_disruption.MODEL_SEPARATE_ECON_LEGACY})
    | econ_buy_disruption.BUY_DISRUPTION_MODELS
)


def _resolve_econ_model(
    enable_econ_component: bool, econ_model: str | None, neutralize_econ_terms: bool,
) -> str | None:
    """The econ model this build scores with, or None for the live legacy
    formula. Invalid combinations are rejected rather than silently ignored."""
    if econ_model is None:
        return econ_buy_disruption.MODEL_SEPARATE_ECON_LEGACY if enable_econ_component else None
    if not enable_econ_component:
        raise ValueError(
            f"econ_model={econ_model!r} requires enable_econ_component=True; the legacy "
            "formula has no separate econ component")
    if econ_model not in _ECON_MODELS:
        raise ValueError(f"Unknown econ_model {econ_model!r}; expected one of {sorted(_ECON_MODELS)}")
    if neutralize_econ_terms and econ_model in econ_buy_disruption.BUY_DISRUPTION_MODELS:
        raise ValueError(
            "neutralize_econ_terms is an arm of the legacy combination; it cannot be "
            f"combined with econ_model={econ_model!r}")
    return econ_model


def _combat_input_issues(
    match_players: dict[int, MatchPlayer],
    round_player_stats: dict[int, dict[int, dict]],
    round_kills: dict[int, list[dict]],
) -> list[ImpactInputIssue]:
    """What would stop combat scoring from reconstructing a round, checked
    BEFORE the trade, economy-differential and kill-order loops index anything.

    A known victim with a NULL killer is not an issue: it is an environmental
    death, routed as a death on the victim's own side (see the kill-order
    loop). An unknown victim is: no team can be charged the death, so the
    round state cannot be reconstructed, and nothing is invented to fill it.
    """
    issues = []
    for round_number in sorted(round_kills):
        stats = round_player_stats.get(round_number, {})
        for kill in round_kills[round_number]:
            killer_id = kill["killer_match_player_id"]
            victim_id = kill["death_match_player_id"]
            event_id = kill["id"]
            if victim_id is None or victim_id not in match_players:
                issues.append(ImpactInputIssue("unknown_victim", round_number, event_id, victim_id))
                continue
            if killer_id is not None and killer_id not in match_players:
                issues.append(ImpactInputIssue("killer_not_in_match", round_number, event_id, killer_id))
            elif killer_id is not None and killer_id not in stats:
                issues.append(ImpactInputIssue(
                    "killer_missing_round_stats", round_number, event_id, killer_id))
            if victim_id not in stats:
                issues.append(ImpactInputIssue(
                    "victim_missing_round_stats", round_number, event_id, victim_id))
    return issues


def _combined_swing_factors_for_round(
    round_outcomes: dict[int, str],
    round_player_stats: dict[int, dict[int, dict]],
    match_players: dict[int, MatchPlayer],
    round_number: int,
    round_row: Round,
    use_realized_swing: bool,
) -> tuple[float, float]:
    """(TEAM_1, TEAM_2) combined swing factors -- the legacy swing term."""
    team1_swing = _econ_swing_risk_factor(
        round_outcomes, round_player_stats, match_players, round_number, Team.TEAM_1, round_row
    )
    team2_swing = _econ_swing_risk_factor(
        round_outcomes, round_player_stats, match_players, round_number, Team.TEAM_2, round_row
    )
    # Ex-ante mode drops the realized term entirely. _realized_econ_swing_factor
    # reads round N+1's loadouts, so any forward-looking model trained on a
    # swing_impact that includes it is leaking. See the spec's LEAKAGE section.
    if use_realized_swing:
        team1_realized_swing = _realized_econ_swing_factor(
            round_player_stats, match_players, round_number, Team.TEAM_1
        )
        team2_realized_swing = _realized_econ_swing_factor(
            round_player_stats, match_players, round_number, Team.TEAM_2
        )
        return (_combine_swing_factors(team1_swing, team1_realized_swing),
                _combine_swing_factors(team2_swing, team2_realized_swing))
    return team1_swing, team2_swing


def _buy_disruption_econ_for_round(
    round_number: int, rounds_by_number: dict[int, Round], kills: list[dict],
    match_players: dict[int, MatchPlayer],
    round_player_stats: dict[int, dict[int, dict]],
    model: str, use_realized: bool, econ_observer=None,
    next_kills: list[dict] | None = None,
) -> dict[int, float]:
    """ECON_SCALE * raw signed net per match_player for one round, from the
    production calculator (spec 2026-09-10, section 12). {} when the round
    abstains. Rounding -- once, after C -- is the caller's.

    The whole ordered source-event ledger is passed, including environmental
    deaths, which the calculator charges to their victim without enemy credit.
    """
    this_round = round_player_stats.get(round_number) or {}
    next_round = round_player_stats.get(round_number + 1) or {}
    next_row = rounds_by_number.get(round_number + 1)
    pistol_row = rounds_by_number.get(econ_buy_disruption.pistol_round_for(round_number))
    # Inputs below this line feed only the bonus-denial model (spec 2026-09-12
    # section 8); every other model ignores them. None stays None -- the
    # calculator refuses a round with missing inputs rather than assume zero.
    winners = {number: econ_buy_disruption.outcome_winner(row.outcome, Team.TEAM_1, Team.TEAM_2)
               for number, row in rounds_by_number.items()}
    next_round_reward = {team: round_rewards.round_reward(winners, round_number + 1, team)
                         for team in (Team.TEAM_1, Team.TEAM_2)}
    inputs = econ_buy_disruption.RoundEconInputs(
        round_number=round_number,
        last_round_number=max(rounds_by_number),
        team_a=Team.TEAM_1, team_b=Team.TEAM_2,
        players=tuple(
            econ_buy_disruption.PlayerEconomy(
                match_player_id=match_player_id, team=mp.team,
                free_ability_credits=free_ability_credits(mp.agent),
                loadout=this_round.get(match_player_id, {}).get("loadout"),
                next_loadout=next_round.get(match_player_id, {}).get("loadout"),
                next_remaining=next_round.get(match_player_id, {}).get("remaining"),
                has_current_stats=match_player_id in this_round,
                has_next_stats=match_player_id in next_round,
                agent=mp.agent,
                remaining=this_round.get(match_player_id, {}).get("remaining"),
                kills=this_round.get(match_player_id, {}).get("kills"),
                deaths=this_round.get(match_player_id, {}).get("deaths"),
                next_deaths=next_round.get(match_player_id, {}).get("deaths"),
            )
            for match_player_id, mp in sorted(match_players.items())
        ),
        events=tuple(
            econ_buy_disruption.EconEvent(
                event_id=kill["id"], time_seconds=kill["event_time_seconds"],
                killer_id=kill["killer_match_player_id"], victim_id=kill["death_match_player_id"],
                weapon=kill.get("weapon"),
            )
            for kill in kills
        ),
        outcome=rounds_by_number[round_number].outcome,
        next_outcome=next_row.outcome if next_row is not None else None,
        pistol_outcome=pistol_row.outcome if pistol_row is not None else None,
        has_next_round=next_row is not None,
        use_realized=use_realized,
        next_events=(tuple(
            econ_buy_disruption.EconEvent(
                event_id=kill["id"], time_seconds=kill["event_time_seconds"],
                killer_id=kill["killer_match_player_id"], victim_id=kill["death_match_player_id"],
                weapon=kill.get("weapon"),
            )
            for kill in next_kills
        ) if (next_row is not None and next_kills is not None) else None),
        planted=rounds_by_number[round_number].planted,
        attacking_team=_attacking_team(round_number),
        next_round_reward=next_round_reward,
    )
    result = econ_buy_disruption.score_round(inputs, model)
    if econ_observer is not None:
        # REPORTING ONLY, and called for abstaining rounds too so a review can
        # show every played round and its reason.
        econ_observer(round_number=round_number, model=model,
                      audit_version=result.audit_version, result=result)
    return {
        match_player_id: econ_component.ECON_SCALE * net
        for match_player_id, net in result.raw_net_by_player().items()
    }


def build_impact_rows_for_match(
    db: Session, match_id: int, use_realized_swing: bool = True,
    enable_preplant_empirical: bool = False,
    enable_postplant_leverage: bool = False, postplant_factor_table=None,
    enable_econ_component: bool = False, neutralize_econ_terms: bool = False,
    kill_observer=None, econ_observer=None, weights: "FormulaWeights | None" = None,
    econ_model: str | None = None, enable_trade_credit: bool = False,
    enable_decided_only_time: bool = False, remove_post_decided_assists: bool = False,
) -> list[CalculatedImpact]:
    """kill_observer, when given, is called once per kill AFTER that kill has
    been fully scored, with the scorer's own mutated kill dict and the round
    context it was scored in. It is a REPORTING hook for trace/review tooling
    and must never influence scoring -- a test pins that passing one leaves
    every returned row identical. It exists so a review tool reports what the
    scorer computed rather than re-deriving it; re-derivation is how
    preplant_fit_support and ten diagnostics silently drifted from this
    module's own replay policy.

    `econ_model` selects the separate econ component's model when
    enable_econ_component is True: None keeps `separate_econ_legacy`; the
    buy-disruption models live in app.scoring.econ_buy_disruption. Combat
    inputs are validated before any loop indexes them; an unscoreable match
    raises ImpactInputError before a single row is built.

    Impact v4 (declaration 12; both default OFF, and OFF reads nothing new):
    `enable_decided_only_time` replaces the time factor with 1.0, or 0.0 once
    the round is decided (_time_factor's `decided_only`), and cannot be
    combined with either legacy timing candidate. `remove_post_decided_assists`
    takes each assist on a kill made after the round was decided out of the
    assists component (_remove_post_decided_assists), and reports what it did
    in kill_observer's context under "post_decided_assists"."""
    if enable_decided_only_time and (enable_preplant_empirical or enable_postplant_leverage):
        raise ValueError("enable_decided_only_time cannot be combined with "
                         "enable_preplant_empirical or enable_postplant_leverage")
    weights = weights or FormulaWeights()
    resolved_econ_model = _resolve_econ_model(enable_econ_component, econ_model, neutralize_econ_terms)
    bypass_legacy_swing = resolved_econ_model in econ_buy_disruption.BUY_DISRUPTION_MODELS
    rounds = db.query(Round).filter_by(match_id=match_id).order_by(Round.round_number).all()
    rounds_by_number: dict[int, Round] = {r.round_number: r for r in rounds}
    round_number_by_round_id: dict[int, int] = {r.id: r.round_number for r in rounds}
    round_outcomes: dict[int, str] = {r.round_number: r.outcome for r in rounds}

    match_players: dict[int, MatchPlayer] = {
        mp.id: mp for mp in db.query(MatchPlayer).filter_by(match_id=match_id).all()
    }
    # Built once for _traded_factor's team check (a killer who dies to their
    # own side did not trade the victim back).
    team_of: dict[int, Team] = {mp_id: mp.team for mp_id, mp in match_players.items()}

    round_player_stats: dict[int, dict[int, dict]] = defaultdict(dict)
    for stat in db.query(RoundPlayerStat).join(Round).filter(Round.match_id == match_id).all():
        round_number = round_number_by_round_id[stat.round_id]
        round_player_stats[round_number][stat.match_player_id] = {
            "score": stat.score,
            "kills": stat.kills,
            "deaths": stat.deaths,
            "assists": stat.assists,
            "loadout": stat.loadout,
            "remaining": stat.remaining,
        }

    round_kills: dict[int, list[dict]] = defaultdict(list)
    # remove_post_decided_assists only: {KillEvent.id: assistants}. Kept OUT of
    # the kill dicts, which kill_observer sees, so a flags-off replay reads and
    # reports exactly what it did before the flag existed.
    assistants_by_kill_id: dict[int, list] = {}
    for kill in (
        db.query(KillEvent)
        .join(Round)
        .filter(Round.match_id == match_id)
        .order_by(KillEvent.event_time_seconds, KillEvent.id)
        .all()
    ):
        round_number = round_number_by_round_id[kill.round_id]
        round_kills[round_number].append(
            {
                # The source KillEvent.id, kept so economy audits and traces
                # reconcile to real events in (event_time_seconds, id) order.
                "id": kill.id,
                "killer_match_player_id": kill.killer_match_player_id,
                "death_match_player_id": kill.death_match_player_id,
                "event_time_seconds": kill.event_time_seconds,
                # Read only by the bonus-denial econ model's kill-feed evidence.
                "weapon": kill.weapon,
            }
        )
        if remove_post_decided_assists:
            assistants_by_kill_id[kill.id] = (kill.source_meta or {}).get("assistants") or []

    players_by_name = _players_by_name(db, match_id) if remove_post_decided_assists else {}
    # {round_number: {match_player_id: assists removed}} (remove_post_decided_assists).
    removed_assists: dict[int, dict[int, int]] = defaultdict(dict)

    issues = _combat_input_issues(match_players, round_player_stats, round_kills)
    if issues:
        raise ImpactInputError(match_id, issues)

    # Trade detection: kill D1 (A kills B, an enemy kill), followed inside the
    # trade window by kill D2 where B's teammate C kills A. From C's
    # perspective, C traded for B; from B's perspective, B was traded by C.
    #
    # Shares TRADE_WINDOW_SECONDS with the scoring discount (project owner,
    # 2026-09-10). These were 10s while the discount ramped over 10s, so a
    # 9.5s "trade" -- forgiven only 5% by the scorer -- was still DISPLAYED as
    # a full trade on the match page, indistinguishable from an instant one.
    time_to_trade = TRADE_WINDOW_SECONDS
    trade_kill_counts: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    trade_death_counts: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    trade_kill_targets: dict[int, dict[int, dict[int, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    trade_death_sources: dict[int, dict[int, dict[int, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    for round_number, kills in round_kills.items():
        for d1 in kills:
            a_id, b_id = d1["killer_match_player_id"], d1["death_match_player_id"]
            if a_id is None or b_id is None or a_id == b_id:
                continue
            b_team = match_players[b_id].team
            if match_players[a_id].team == b_team:
                continue

            for d2 in kills:
                c_id, victim2_id = d2["killer_match_player_id"], d2["death_match_player_id"]
                if victim2_id != a_id or c_id is None or c_id == victim2_id:
                    continue
                trade_time = d2["event_time_seconds"] - d1["event_time_seconds"]
                if 0 <= trade_time <= time_to_trade and match_players[c_id].team == b_team:
                    trade_kill_counts[round_number][c_id] += 1
                    trade_death_counts[round_number][b_id] += 1
                    trade_kill_targets[round_number][c_id][b_id] += 1
                    trade_death_sources[round_number][b_id][c_id] += 1
                    break

    # Econ-differential factor per kill.
    for round_number, kills in round_kills.items():
        for kill in kills:
            killer_id = kill["killer_match_player_id"]
            death_id = kill["death_match_player_id"]
            # An environmental death (no killer) takes the self-kill policy:
            # a death with no enemy on the other end.
            self_kill = killer_id is None or killer_id == death_id
            death_econ = round_player_stats[round_number][death_id]["loadout"]
            if self_kill:
                kill["econ_differential_factor"] = _categorize_econ(death_econ)
                kill["econ_mismatch"] = False
            else:
                killer_econ = round_player_stats[round_number][killer_id]["loadout"]
                killer_tier = _categorize_econ(killer_econ)
                death_tier = _categorize_econ(death_econ)
                kill["econ_differential_factor"] = killer_tier / death_tier
                kill["econ_mismatch"] = killer_tier != death_tier

    # Leverage credited to traded players, per round (enable_trade_credit).
    trade_credit_by_round: dict[int, dict[int, float]] = {}

    # Kill-order bonuses, decorated per kill.
    for round_number, kills in round_kills.items():
        round_row = rounds_by_number[round_number]
        alive_before = _alive_before_each_kill(
            kills, team_of, _present_players(round_player_stats[round_number], kills, team_of))

        if bypass_legacy_swing:
            # The buy-disruption composite never reads swing (swing_impact is
            # written 0 under the new structure), so the legacy economy work
            # is skipped rather than computed and discarded (plan review, P1).
            team1_combined_swing = team2_combined_swing = None
        else:
            team1_combined_swing, team2_combined_swing = _combined_swing_factors_for_round(
                round_outcomes, round_player_stats, match_players, round_number, round_row,
                use_realized_swing,
            )

        for kill_index, kill in enumerate(kills):
            killer_id = kill["killer_match_player_id"]
            death_id = kill["death_match_player_id"]
            # An environmental death (known victim, no killer) is routed as a
            # death on the VICTIM's own side: their team loses a player and
            # they are charged the death, but no kill is credited to anyone.
            # This is the existing self-kill policy, not an invented killer.
            environmental = killer_id is None
            self_kill = environmental or killer_id == death_id
            killer_team = match_players[death_id].team if environmental else match_players[killer_id].team
            # Despite the names, team1_kill_index holds TEAM_2's alive count and
            # team2_kill_index holds TEAM_1's -- the convention _kill_order_bonus
            # and the code below were written against.
            team1_kill_index = alive_before[kill_index][Team.TEAM_2]
            team2_kill_index = alive_before[kill_index][Team.TEAM_1]

            # Valorant's own combat-score kill-order bonus: 150 for a kill against a
            # still-full 5-player enemy team, decrementing 20 per further kill landed
            # against that same team this round, regardless of the killer's own losses.
            # This has nothing to do with our kill_order_bonus graph below -- it has to
            # be backed out of the raw ACS number so damages reflects pure damage+assists.
            victim_team_alive = team1_kill_index if killer_team == Team.TEAM_1 else team2_kill_index
            # A team kill earns no combat score, so there is no bonus in the ACS
            # to back out of it. Subtracting one drove the damage term negative
            # (owner, 2026-09-13): 43 player-rounds, down to -150 for a team kill
            # at 5v5. Team kills are excluded from the multikill count below for
            # the same reason.
            team_kill = not self_kill and match_players[death_id].team == killer_team
            kill["scores_a_kill"] = not self_kill and not team_kill
            kill["acs_bonus"] = max(0, 150 - 20 * (5 - victim_team_alive)) if kill["scores_a_kill"] else 0

            combined_swing_factor = team1_combined_swing if killer_team == Team.TEAM_2 else team2_combined_swing
            kill_order_bonus = _kill_order_bonus(team1_kill_index, team2_kill_index, killer_team, self_kill)

            # Alive counts BEFORE this kill, in (attackers, defenders) order --
            # Part 4's V table is keyed that way. Note the index names are
            # inverted relative to the teams they count (see the comment where
            # they are initialised): team2_kill_index holds TEAM_1's alive
            # count and team1_kill_index holds TEAM_2's.
            attacking = _attacking_team(round_number)
            if attacking is None:
                alive_counts = None
            elif attacking == Team.TEAM_1:
                alive_counts = (team2_kill_index, team1_kill_index)
            else:
                alive_counts = (team1_kill_index, team2_kill_index)

            kill["kill_order_bonus"] = kill_order_bonus if not self_kill else 0
            # Arm 4 of the predeclared five-arm protocol (econ spec 8d-i): the
            # two old econ terms pinned to their NEUTRAL 1.0 inside today's
            # combination structure. It exists because deleting terms from
            # damages + mean(econ, time, swing) also changes the mean's arity,
            # so without it arm 1 confounds "econ information is gone" with
            # "the remaining components were re-weighted".
            econ_differential = 1.0 if neutralize_econ_terms else kill["econ_differential_factor"]
            kill["kill_order_bonus_x_econ"] = (
                kill_order_bonus * econ_differential if not self_kill else 0
            )
            kill["kill_order_bonus_x_time"] = (
                kill_order_bonus * _time_factor(
                    round_row, kill["event_time_seconds"],
                    is_attacker=(attacking == killer_team),
                    enable_preplant_empirical=enable_preplant_empirical,
                    use_realized=use_realized_swing,
                    alive_counts=alive_counts,
                    postplant_factor_table=postplant_factor_table,
                    enable_postplant_leverage=enable_postplant_leverage,
                    decided_only=enable_decided_only_time,
                ) if not self_kill else 0
            )
            if neutralize_econ_terms:
                combined_swing_factor = 1.0
            if combined_swing_factor is None:
                kill["kill_order_bonus_x_swing"] = 0.0
            else:
                kill["kill_order_bonus_x_swing"] = kill_order_bonus * combined_swing_factor if not self_kill else 0

            death_order_bonus = kill_order_bonus * _traded_factor(
                kills, kill, self_kill, team_of=team_of)
            kill["death_order_bonus"] = death_order_bonus

            if self_kill:
                if kill["econ_differential_factor"] == 4:
                    death_econ_factor = 0.9
                elif kill["econ_differential_factor"] == 5:
                    death_econ_factor = 0.85
                elif kill["econ_differential_factor"] == 6:
                    death_econ_factor = 0.75
                else:
                    death_econ_factor = 0.15
            else:
                death_econ_factor = econ_differential

            kill["death_order_bonus_x_econ"] = death_order_bonus * death_econ_factor
            kill["death_order_bonus_x_time"] = death_order_bonus * _time_factor(
                round_row, kill["event_time_seconds"], for_death=True,
                # Always the KILLER's side, per the candidate doc: a death is the
                # transfer of what the victim's team lost, referencing the same
                # event/side the kill-side scalar used -- not the victim's side.
                # Part 4 takes the same value for kill and death by the same
                # argument: the event transfers D, the killer is credited it and
                # the victim debited it (spec, "Deaths").
                is_attacker=(attacking == killer_team),
                enable_preplant_empirical=enable_preplant_empirical,
                use_realized=use_realized_swing,
                alive_counts=alive_counts,
                postplant_factor_table=postplant_factor_table,
                enable_postplant_leverage=enable_postplant_leverage,
                # Finding 6. The is_attacker convention above is correct and
                # stays; what it cannot express is a self-kill, where killer
                # and victim are the SAME player and Part 4's
                # `victim_is_attacker = not is_attacker` therefore names the
                # opposite side. _time_factor declines the Part 4 table for
                # these and falls back to the legacy factor. The KILL side
                # needs no such flag: `... if not self_kill else 0` above
                # means _time_factor is never evaluated for a self-kill there.
                self_kill=self_kill,
                # Impact v4: a self-kill or environmental death costs K*1
                # before the round is decided and 0 after (_traded_factor is
                # 1 for a self-kill), exactly as arm N scored it.
                decided_only=enable_decided_only_time,
            )
            kill["death_order_bonus_x_swing"] = (
                death_order_bonus * combined_swing_factor if combined_swing_factor is not None else 0.0
            )

            killer_own_alive = team2_kill_index if killer_team == Team.TEAM_1 else team1_kill_index
            killer_opp_alive = team1_kill_index if killer_team == Team.TEAM_1 else team2_kill_index
            kill["killer_clutch"] = not self_kill and _clutch_bucket(killer_own_alive, killer_opp_alive)
            kill["victim_clutch"] = not self_kill and _clutch_bucket(killer_opp_alive, killer_own_alive)

            plant_time = round_row.plant_time if round_row.planted else None
            kill["is_post_plant"] = plant_time is not None and kill["event_time_seconds"] >= plant_time

            assist_report = None
            if remove_post_decided_assists:
                assist_report = _remove_post_decided_assists(
                    round_row, kill, assistants_by_kill_id.get(kill["id"]), players_by_name,
                    round_player_stats[round_number], removed_assists[round_number])

            if kill_observer is not None:
                # Alive counts are reported PRE-decrement, which is the state
                # kill_order_bonus was keyed on just above. seconds_to_plant is
                # the effective-plant offset (positive before the plant), the
                # same quantity the pre-plant curve reads.
                kill_observer(
                    round_number=round_number,
                    kill_index=kill_index,
                    kill=kill,
                    context={
                        "self_kill": self_kill,
                        "environmental": environmental,
                        "killer_team": killer_team,
                        "killer_is_attacker": attacking == killer_team,
                        "killer_team_alive": killer_own_alive,
                        # For a self or environmental death the victim is on
                        # the KILLER's side (killer_team is the victim's team),
                        # so the opponent's count would name the wrong team.
                        "victim_team_alive": killer_own_alive if self_kill else killer_opp_alive,
                        "kill_order_bonus_raw": kill_order_bonus,
                        "seconds_to_plant": seconds_to_plant(round_row, kill["event_time_seconds"]),
                        "combined_swing_factor": combined_swing_factor,
                        "round_outcome": round_row.outcome,
                        "planted": round_row.planted,
                        "plant_time": round_row.plant_time,
                        # Present only with remove_post_decided_assists on, so
                        # a flags-off observer sees exactly the old context.
                        **({"post_decided_assists": assist_report}
                           if assist_report is not None else {}),
                    },
                )

        # After the loop: a credit is a share of the TRADE kill's leverage, so
        # every kill's kill_order_bonus_x_time must already be set. Like the
        # other weighted terms it exists only in the new structure.
        if enable_trade_credit and enable_econ_component:
            trade_credit_by_round[round_number] = _trade_credits_for_round(kills, team_of)

    calculated: list[CalculatedImpact] = []

    # Aggregate per (round, match_player) and write impact_scores.
    last_round_number = max(round_player_stats) if round_player_stats else 0
    for round_number, mp_stats in round_player_stats.items():
        round_row = rounds_by_number[round_number]
        kills = round_kills.get(round_number, [])

        if resolved_econ_model is None:
            econ_by_player = {}
        elif resolved_econ_model == econ_buy_disruption.MODEL_SEPARATE_ECON_LEGACY:
            econ_by_player = _econ_components_for_round(
                round_number, round_number >= last_round_number, kills, match_players,
                round_player_stats, use_realized=use_realized_swing,
                econ_observer=econ_observer,
            )
        else:
            econ_by_player = _buy_disruption_econ_for_round(
                round_number, rounds_by_number, kills, match_players, round_player_stats,
                resolved_econ_model, use_realized=use_realized_swing, econ_observer=econ_observer,
                next_kills=round_kills.get(round_number + 1, []),
            )

        for match_player_id, stat in mp_stats.items():
            acs = stat["score"]
            kill_order_bonus_x_econ_sum = 0.0
            kill_order_bonus_x_time_sum = 0.0
            kill_order_bonus_x_swing_sum = 0.0
            kill_order_bonus_sum = 0.0
            clutch_kill_sum = 0.0
            post_plant_kill_sum = 0.0
            econ_mismatch_kill_sum = 0.0

            scoring_kills = 0
            for kill in kills:
                if kill["killer_match_player_id"] == match_player_id:
                    acs -= kill["acs_bonus"]
                    scoring_kills += kill["scores_a_kill"]
                    kill_order_bonus_x_econ_sum += kill["kill_order_bonus_x_econ"]
                    kill_order_bonus_x_time_sum += kill["kill_order_bonus_x_time"]
                    kill_order_bonus_x_swing_sum += kill["kill_order_bonus_x_swing"]
                    kill_order_bonus_sum += kill["kill_order_bonus"]
                    if kill["killer_clutch"]:
                        clutch_kill_sum += kill["kill_order_bonus"]
                    if kill["is_post_plant"]:
                        post_plant_kill_sum += kill["kill_order_bonus_x_time"]
                    if kill["econ_mismatch"]:
                        econ_mismatch_kill_sum += kill["kill_order_bonus_x_econ"]

            # Valorant's own multikill bonus: +50 for each kill after the first in
            # a round. It is part of the ACS, not of damage, so it is stripped out
            # (owner, 2026-09-13). The old line read `acs - (-50 * kills_in_round)`,
            # which ADDED 50 per kill instead -- +250 on an ace -- so this is a sign
            # fix, not a new adjustment. Fitting combat score against damage dealt
            # over the raw captures puts the per-extra-kill term at 48.7, which is
            # this 50. acs already has the kill-order bonuses removed above, so what
            # remains is damage plus assist points.
            if scoring_kills > 1:
                acs -= 50 * (scoring_kills - 1)
            damage_and_assists = acs

            death_order_bonus_x_econ_sum = 0.0
            death_order_bonus_x_time_sum = 0.0
            death_order_bonus_x_swing_sum = 0.0
            death_order_bonus_sum = 0.0
            clutch_death_sum = 0.0
            post_plant_death_sum = 0.0
            econ_mismatch_death_sum = 0.0
            for kill in kills:
                if kill["death_match_player_id"] == match_player_id:
                    death_order_bonus_x_econ_sum += kill["death_order_bonus_x_econ"]
                    death_order_bonus_x_time_sum += kill["death_order_bonus_x_time"]
                    death_order_bonus_x_swing_sum += kill["death_order_bonus_x_swing"]
                    death_order_bonus_sum += kill["death_order_bonus"]
                    if kill["victim_clutch"]:
                        clutch_death_sum += kill["death_order_bonus"]
                    if kill["is_post_plant"]:
                        post_plant_death_sum += kill["death_order_bonus_x_time"]
                    if kill["econ_mismatch"]:
                        econ_mismatch_death_sum += kill["death_order_bonus_x_econ"]

            damages = round(damage_and_assists * weights.damage)
            econ_component_value = round(weights.econ * econ_by_player.get(match_player_id, 0.0))
            # D x ELIGIBLE assists: with remove_post_decided_assists off,
            # nothing is ever removed and this is D x the scoreboard count.
            # stat["assists"] itself is never changed.
            eligible_assists = stat["assists"] - removed_assists[round_number].get(match_player_id, 0)
            assists_component_value = round(weights.assists * eligible_assists)
            trade_credit_x_time = (
                trade_credit_by_round.get(round_number, {}).get(match_player_id, 0.0)
                * weights.trade_credit_scale
            )
            time_impact_value = round(kill_order_bonus_x_time_sum + trade_credit_x_time - death_order_bonus_x_time_sum)
            leverage_component_value = round(
                weights.leverage * (kill_order_bonus_x_time_sum + trade_credit_x_time - death_order_bonus_x_time_sum)
            )

            if enable_econ_component:
                # Econ spec section 8: impact = damage + leverage + econ,
                # replacing damages + mean(econ, time, swing) in which all
                # three terms shared kill_order_bonus. swing_impact leaves the
                # FORMULA here; its column stays and is written as 0 below,
                # because the evaluation harness reads it by name and silently
                # changing its meaning would invalidate every stored
                # comparison. time_impact does NOT die -- it IS the leverage
                # component under the new structure.
                kill_impact = round(
                    damages + weights.leverage * (kill_order_bonus_x_time_sum + trade_credit_x_time)
                ) + assists_component_value
                death_impact = round(weights.leverage * death_order_bonus_x_time_sum)
                # Reconciliation, exactly: the four ints below are the four
                # terms of the formula and nothing else. kill_impact minus
                # death_impact can differ from leverage_component_value by 1,
                # because each rounds independently -- so `impact` is built
                # from the terms, never from that subtraction.
                impact = damages + leverage_component_value + econ_component_value + assists_component_value
            else:
                kill_impact = round(
                    damages
                    + (
                        FACTOR_WEIGHTS["econ"] * kill_order_bonus_x_econ_sum
                        + FACTOR_WEIGHTS["time"] * kill_order_bonus_x_time_sum
                        + FACTOR_WEIGHTS["swing"] * kill_order_bonus_x_swing_sum
                    )
                    / _FACTOR_WEIGHT_TOTAL
                )
                death_impact = round(
                    (
                        FACTOR_WEIGHTS["econ"] * death_order_bonus_x_econ_sum
                        + FACTOR_WEIGHTS["time"] * death_order_bonus_x_time_sum
                        + FACTOR_WEIGHTS["swing"] * death_order_bonus_x_swing_sum
                    )
                    / _FACTOR_WEIGHT_TOTAL
                )
                impact = kill_impact - death_impact

            traded_teammate_targets = {
                str(k): v for k, v in trade_kill_targets[round_number].get(match_player_id, {}).items()
            }
            traded_by_teammate_sources = {
                str(k): v for k, v in trade_death_sources[round_number].get(match_player_id, {}).items()
            }

            calculated.append(
                CalculatedImpact(
                    round_id=round_row.id,
                    match_player_id=match_player_id,
                    kill_impact=kill_impact,
                    death_impact=death_impact,
                    impact=impact,
                    damage=damages,
                    leverage_component=leverage_component_value if enable_econ_component else 0,
                    assists_component=assists_component_value if enable_econ_component else 0,
                    trade_credit=round(weights.leverage * trade_credit_x_time),
                    scoring_version=IMPACT_CALCULATION_VERSION,
                    # econ_impact and swing_impact keep their columns but are
                    # written as 0 once the new structure is live -- they left
                    # the formula, and redefining a column consumers read by
                    # name would invalidate every stored comparison.
                    econ_impact=(
                        0 if enable_econ_component
                        else round(kill_order_bonus_x_econ_sum - death_order_bonus_x_econ_sum)
                    ),
                    time_impact=time_impact_value,
                    swing_impact=(
                        0 if enable_econ_component
                        else round(kill_order_bonus_x_swing_sum - death_order_bonus_x_swing_sum)
                    ),
                    econ_component=econ_component_value,
                    kill_order_bonus=round(kill_order_bonus_sum - death_order_bonus_sum),
                    econ_kill=round(econ_mismatch_kill_sum),
                    econ_death=round(econ_mismatch_death_sum),
                    clutch_kill=round(clutch_kill_sum),
                    clutch_death=round(clutch_death_sum),
                    post_plant_kill=round(post_plant_kill_sum),
                    post_plant_death=round(post_plant_death_sum),
                    traded_teammate=trade_kill_counts[round_number].get(match_player_id, 0),
                    traded_by_teammate=trade_death_counts[round_number].get(match_player_id, 0),
                    trade_detail=(
                        {"t": traded_teammate_targets, "s": traded_by_teammate_sources}
                        if (traded_teammate_targets or traded_by_teammate_sources)
                        else None
                    ),
                )
            )

    return calculated


_PERSISTED_FIELDS = (
    "kill_impact", "death_impact", "impact", "damage", "econ_impact",
    "time_impact", "swing_impact", "econ_kill", "econ_death", "clutch_kill",
    "clutch_death", "post_plant_kill", "post_plant_death", "traded_teammate",
    "traded_by_teammate", "trade_detail", "kill_order_bonus", "econ_component",
    "econ_pickup",
    # Migration 0010. tests/test_impact_persistence_contract.py holds this list
    # to the model's own columns, so a column added without its field here (or
    # the reverse) fails independently of anything that reads this tuple.
    "trade_credit", "scoring_version",
)

# Public alias for tooling that must compare EVERY persisted value (the
# backfill's acceptance check): a subset would let stale columns through.
PERSISTED_FIELDS = _PERSISTED_FIELDS


def compute_impact_for_match(db: Session, match_id: int, config=None) -> None:
    """Compute and persist. The calculation lives in build_impact_rows_for_match
    so the evaluation tooling can call it read-only -- see
    docs/superpowers/specs/2026-09-01-impact-win-correlation-design.md.

    `config` (an app.scoring.impact_config.ImpactScoringConfig) is the one
    explicit scoring configuration. None resolves app.scoring.impact_runtime's
    active configuration, which is itself None -- the live legacy formula --
    until a frozen manifest is deliberately activated."""
    if config is None:
        from app.scoring.impact_runtime import active_scoring_config  # avoids an import cycle

        config = active_scoring_config()
    kwargs = config.build_kwargs() if config is not None else {"use_realized_swing": True}
    for calculated in build_impact_rows_for_match(db, match_id, **kwargs):
        impact_score = (
            db.query(ImpactScore)
            .filter_by(round_id=calculated.round_id, match_player_id=calculated.match_player_id)
            .one_or_none()
        )
        if impact_score is None:
            impact_score = ImpactScore(
                round_id=calculated.round_id, match_player_id=calculated.match_player_id
            )
            db.add(impact_score)
        for field in _PERSISTED_FIELDS:
            setattr(impact_score, field, getattr(calculated, field))
    db.commit()
