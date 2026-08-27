""""The enemy just clinched their 11th round win -- what should we buy the
very next round?" A team that reaches 11 round wins is two wins from taking
the whole match (13), so this is the round right before a possible loss
snowballs into match point for them. Bucket the RESPONDING team's (the one
facing that 11th loss) buy in the immediate next round into one of four
named tiers -- Eco/Save, Half Buy, Force Buy, Full Buy -- against three
independent win rates: the response round itself, the round right after
that, and the match overall.

Unlike app.services.eco_followup / app.services.force_buy_stats (both
pistol-round-anchored, so exactly two possible trigger rounds per match:
1 and 13), "enemy reaches 11 wins" can happen at any round number depending
on how the match unfolds -- found by replaying each team's cumulative
round-win count round by round rather than checking two fixed round
numbers. A team's cumulative win count only ever increases by 1 and a
decisively-won match's winner necessarily passes through 11 wins at some
point, so most decisive matches contribute at least one sample; a close
match where the loser also reaches 11 (e.g. final score 13-11) contributes
a second, independent sample for the other team's response.

Buy-tier thresholds (ECO_LOADOUT_MAX, FULL_BUY_LOADOUT_MIN, FORCE_SPEND_RATIO
below) were chosen from the observed distribution of responding-team total
loadout at this exact trigger (2026-08-27 snapshot of this DB, 3795
"all"-scope samples): loadout percentiles were roughly p5=7700, p10=10300,
p25=14850, p50=18850, p75=21550, p90=22950 -- a small low tail (eco), a
sizeable middle spread (half/force), and a large cluster from ~18000-24000
(full buy, comfortably affording 5 rifles + shields + utility). A team
below ECO_LOADOUT_MAX kept its money back regardless of what fraction of
its cash that represents. A team at/above FULL_BUY_LOADOUT_MIN has enough
gear for a real buy whether or not it spent every last credit doing so. In
between, spend_ratio (loadout / (loadout + remaining)) separates a
deliberate Half Buy (held some money back) from a Force Buy (spent nearly
everything it had despite that not amounting to a full buy) --
FORCE_SPEND_RATIO=0.85 sits above this trigger's median spend_ratio (~0.80,
higher than the 0.7 threshold app.services.force_buy_stats uses for its
earlier, smaller-economy pistol-round-loss context) so "half" and "force"
each stay populous rather than "force" swallowing nearly every mid-loadout
round. All four tiers clear MIN_SAMPLES_FOR_BEST in both "friends" and
"all" scope at these thresholds.
"""

from dataclasses import dataclass

from app.models import Match
from app.models.match import Team
from app.services.player_graphs import win_color
from app.services.player_profile_types import match_win

ENEMY_WIN_TRIGGER = 11

ECO_LOADOUT_MAX = 10000
FULL_BUY_LOADOUT_MIN = 20000
FORCE_SPEND_RATIO = 0.85

BUY_CATEGORIES = ("eco", "half", "force", "full")
RESPONSE_METRICS = ("immediate", "next", "match")

# A tier needs at least this many samples before it's eligible to be
# highlighted as the "best" response -- otherwise a handful of rounds in a
# sparse tier could claim a high rate off pure noise. Same convention as
# app.services.eco_followup.MIN_SAMPLES_FOR_BEST.
MIN_SAMPLES_FOR_BEST = 20


def _winner_team(outcome: str | None) -> Team | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return Team.TEAM_1
    if outcome.startswith("Team B"):
        return Team.TEAM_2
    return None


def _team_string(team: Team) -> str:
    return team.value if hasattr(team, "value") else team


def classify_buy(total_loadout: int, spend_ratio: float) -> str:
    if total_loadout < ECO_LOADOUT_MAX:
        return "eco"
    if total_loadout >= FULL_BUY_LOADOUT_MIN:
        return "full"
    if spend_ratio >= FORCE_SPEND_RATIO:
        return "force"
    return "half"


def _enemy_at_11_response_samples(match: Match) -> list[tuple[Team, str, dict[str, bool | None]]]:
    """(responder_team, buy_category, {"immediate": bool, "next": bool|None,
    "match": bool|None}) for each team in `match` whose opponent's
    cumulative round-win count reached ENEMY_WIN_TRIGGER (11) at some round,
    AND whose immediate follow-up round has recorded loadout/remaining
    stats for the responding team. A match contributes 0, 1, or 2 samples --
    one per team that had to respond to the OTHER team reaching 11 wins."""
    team_of = {mp.id: mp.team for mp in match.match_players}
    rounds_by_number = {r.round_number: r for r in match.rounds}
    if not rounds_by_number:
        return []
    max_round_number = max(rounds_by_number)

    cumulative_wins = {Team.TEAM_1: 0, Team.TEAM_2: 0}
    trigger_round_for_enemy: dict[Team, int] = {}
    for round_number in range(1, max_round_number + 1):
        r = rounds_by_number.get(round_number)
        if r is None:
            continue
        winner = _winner_team(r.outcome)
        if winner is None:
            continue
        cumulative_wins[winner] += 1
        if cumulative_wins[winner] == ENEMY_WIN_TRIGGER and winner not in trigger_round_for_enemy:
            trigger_round_for_enemy[winner] = round_number

    samples: list[tuple[Team, str, dict[str, bool | None]]] = []
    for enemy, trigger_round_number in trigger_round_for_enemy.items():
        responder = Team.TEAM_2 if enemy == Team.TEAM_1 else Team.TEAM_1
        response_rn = trigger_round_number + 1
        response_round = rounds_by_number.get(response_rn)
        if response_round is None:
            continue

        total_loadout = sum(
            stat.loadout for stat in response_round.player_stats if team_of.get(stat.match_player_id) == responder
        )
        total_remaining = sum(
            stat.remaining for stat in response_round.player_stats if team_of.get(stat.match_player_id) == responder
        )
        total_available = total_loadout + total_remaining
        if total_available <= 0:
            continue  # no recorded loadout/remaining stats for this round
        spend_ratio = total_loadout / total_available
        category = classify_buy(total_loadout, spend_ratio)

        immediate_winner = _winner_team(response_round.outcome)
        if immediate_winner is None:
            continue
        immediate_win = immediate_winner == responder

        next_round = rounds_by_number.get(response_rn + 1)
        next_win = None
        if next_round is not None:
            next_winner = _winner_team(next_round.outcome)
            if next_winner is not None:
                next_win = next_winner == responder

        samples.append(
            (
                responder,
                category,
                {
                    "immediate": immediate_win,
                    "next": next_win,
                    "match": match_win(match, _team_string(responder)),
                },
            )
        )
    return samples


def _team_has_roster_player(match: Match, team: Team, roster_player_ids: set[int]) -> bool:
    return any(mp.team == team and mp.player_id in roster_player_ids for mp in match.match_players)


def _empty_variant() -> dict[str, dict[str, dict[str, int]]]:
    return {cat: {metric: {"total": 0, "win": 0} for metric in RESPONSE_METRICS} for cat in BUY_CATEGORIES}


def _accumulate(variant: dict[str, dict[str, dict[str, int]]], category: str, outcomes: dict[str, bool | None]) -> None:
    for metric in RESPONSE_METRICS:
        outcome = outcomes[metric]
        if outcome is None:
            continue
        bucket = variant[category][metric]
        bucket["total"] += 1
        if outcome:
            bucket["win"] += 1


def compute_enemy_at_11_response_stats(matches: list[Match], roster_player_ids: set[int]) -> dict:
    """{"friends": {"eco": {"immediate": {"total","win"}, "next": {...},
    "match": {...}}, "half": {...}, "force": {...}, "full": {...}}, "all":
    {...}}. "friends" scope only counts a sample when the RESPONDING team
    (the one that must decide how to buy) includes a tracked roster player;
    "all" scope counts every sample in the DB."""
    variants = {"friends": _empty_variant(), "all": _empty_variant()}

    for match in matches:
        for responder, category, outcomes in _enemy_at_11_response_samples(match):
            _accumulate(variants["all"], category, outcomes)
            if _team_has_roster_player(match, responder, roster_player_ids):
                _accumulate(variants["friends"], category, outcomes)

    return variants


@dataclass
class EnemyAt11Row:
    category: str
    label: str
    range_label: str
    total: int
    immediate_win_pct: float
    immediate_fill: str
    next_total: int
    next_win_pct: float | None
    next_fill: str | None
    match_total: int
    match_win_pct: float | None
    match_fill: str | None


@dataclass
class EnemyAt11Stats:
    rows: list[EnemyAt11Row]
    total_samples: int
    best_immediate_row: EnemyAt11Row | None
    best_next_row: EnemyAt11Row | None
    best_match_row: EnemyAt11Row | None


_CATEGORY_LABELS = {"eco": "Eco / Save", "half": "Half Buy", "force": "Force Buy", "full": "Full Buy"}

_FORCE_SPEND_PCT = int(round(FORCE_SPEND_RATIO * 100))

# Precomputed once from the classify_buy thresholds so the table always
# matches the actual bucket boundaries -- if ECO_LOADOUT_MAX/
# FULL_BUY_LOADOUT_MIN/FORCE_SPEND_RATIO ever change, these follow without a
# separate edit.
_CATEGORY_RANGE_LABELS = {
    "eco": f"under {ECO_LOADOUT_MAX:,} credits",
    "half": f"{ECO_LOADOUT_MAX:,}-{FULL_BUY_LOADOUT_MIN:,} credits, under {_FORCE_SPEND_PCT}% spent",
    "force": f"{ECO_LOADOUT_MAX:,}-{FULL_BUY_LOADOUT_MIN:,} credits, {_FORCE_SPEND_PCT}%+ spent",
    "full": f"{FULL_BUY_LOADOUT_MIN:,}+ credits",
}


def build_enemy_at_11_response_stats(variant: dict[str, dict[str, dict[str, int]]]) -> EnemyAt11Stats:
    rows: list[EnemyAt11Row] = []
    total_samples = 0
    for category in BUY_CATEGORIES:
        tier = variant.get(category, {})
        immediate = tier.get("immediate", {"total": 0, "win": 0})
        next_ = tier.get("next", {"total": 0, "win": 0})
        match = tier.get("match", {"total": 0, "win": 0})
        if immediate["total"] == 0:
            continue

        total_samples += immediate["total"]
        immediate_win_pct = immediate["win"] / immediate["total"]
        next_win_pct = next_["win"] / next_["total"] if next_["total"] else None
        match_win_pct = match["win"] / match["total"] if match["total"] else None
        rows.append(
            EnemyAt11Row(
                category=category,
                label=_CATEGORY_LABELS[category],
                range_label=_CATEGORY_RANGE_LABELS[category],
                total=immediate["total"],
                immediate_win_pct=immediate_win_pct,
                immediate_fill=win_color(immediate_win_pct),
                next_total=next_["total"],
                next_win_pct=next_win_pct,
                next_fill=win_color(next_win_pct) if next_win_pct is not None else None,
                match_total=match["total"],
                match_win_pct=match_win_pct,
                match_fill=win_color(match_win_pct) if match_win_pct is not None else None,
            )
        )

    eligible = [r for r in rows if r.total >= MIN_SAMPLES_FOR_BEST]
    eligible_for_next = [r for r in eligible if r.next_win_pct is not None and r.next_total >= MIN_SAMPLES_FOR_BEST]
    eligible_for_match = [r for r in eligible if r.match_win_pct is not None and r.match_total >= MIN_SAMPLES_FOR_BEST]
    return EnemyAt11Stats(
        rows=rows,
        total_samples=total_samples,
        best_immediate_row=max(eligible, key=lambda r: r.immediate_win_pct, default=None),
        best_next_row=max(eligible_for_next, key=lambda r: r.next_win_pct, default=None),
        best_match_row=max(eligible_for_match, key=lambda r: r.match_win_pct, default=None),
    )
