""""The enemy just clinched their 11th round win -- when the responding
team can't afford a full team buy, is it better to force it (spend
basically everything you've got right now) or to hold back and guarantee a
real buy next round?" A team that reaches 11 round wins is two wins from
taking the whole match (13), so this is the round right before a possible
loss snowballs into match point for them.

Every sample lands in one of two buckets, or is excluded if neither
genuinely describes it:

  - "force_buy": spent FORCE_SPEND_RATIO or more of the team's available
    money (loadout / (loadout + remaining)) this round. No rifle-count
    requirement -- a force buy can be a single rifle carried by whoever has
    the most money, SMGs across the board, anything, as long as it's
    basically everything the team had.
  - "full_save": this round's own loadout genuinely weak (total loadout <
    FULL_SAVE_LOADOUT_MAX -- see below, this is NOT the same check as the
    shared "not a comfortable full buy" exclusion), AND the team's banked
    money (this round's remaining) PLUS the real credit bonus it will
    actually earn going into next round (computed from the real
    win/loss-streak history via app.scoring.credit_events.round_bonus, not
    a flat guess) projects to at least SAVE_TARGET_TOTAL -- $4700/player,
    the standard full-buy save target. There's also an upper bound: if the
    team's money THIS round alone (no bonus needed) already covers a rifle
    for everyone (RIFLE_COST_TOTAL) and still clears the save target
    afterward, holding to $0 spent isn't a genuine forced save -- it's
    spare wealth -- so that's excluded too (FULL_SAVE_CEILING_TOTAL). In
    practice this ceiling has never fired in this dataset (checked
    2026-08-27): teams facing this trigger are economically constrained
    enough that it never comes up, but it's kept as a correctness guard per
    the user's explicit request rather than dropped as dead code.

FULL_SAVE_LOADOUT_MAX exists because loadout reflects what a player is
CARRYING this round, not what they spent -- a player who survived the
previous round keeps their gun for free, so a "full_save" sample with no
own-round loadout cap could still show a substantial team loadout from
survivors, not a fresh purchase. This was caught by the user questioning
why full_save's immediate-round win rate (49%, uncapped) came out HIGHER
than force_buy's (46%) despite force_buy having objectively better
weaponry -- checking the actual numbers showed full_save's median loadout
was $13,450 (barely below force_buy's $17,900), nowhere near a real eco
round. Adding the $10,000 cap (reused from this module's own v17 history)
drops full_save's immediate win rate to 28% at n=50 -- consistent with
"weaker gear should mean a lower single-round win rate," which the previous,
uncapped definition was masking.

In both cases, a response round where the team could already afford a full
buy (total loadout >= FULL_BUY_LOADOUT_MIN) isn't a real decision -- "no
risk, just another round" -- so it's excluded before either check runs.

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

This module went through several redesigns in one session before landing
here -- team-total buy tiers, then a per-player rifle-count model -- each
replaced because it couldn't cleanly express what the user actually meant
by "force buy" (any spend-everything round, not specifically 2-4 rifles) or
"full save" (a save target with real economic teeth, not just "didn't spend
much"). SAVE_TARGET_PER_PLAYER=4700 and RIFLE_COST_PER_PLAYER=2900 are the
user's own domain figures (a rifle + shield + utility costs almost exactly
$4700 -- matches the sharp loadout-histogram spike found earlier in this
module's history at 4400-5000/player -- and $2900 is a rifle alone).
FORCE_SPEND_RATIO=0.85 and FULL_BUY_LOADOUT_MIN=20000 are unchanged from
earlier validation against this same trigger's loadout/spend-ratio
distribution.

Sample counts at these thresholds (2026-08-27 snapshot): force_buy 693
all-scope / 73 friends-scope, full_save 50 all-scope / 3 friends-scope.
full_save's friends-scope count stays thin regardless of threshold tuning --
confirmed with the user this reflects real behavior (they IGL most of the
group's games and rarely call a full save at this trigger except in the
bleakest rounds), not a bucket-boundary problem, and the loadout-cap fix
above shrank it further (14 -> 3) since a real hard save is even rarer than
"low spend, high bank" was. Read full_save from "All Players" scope only --
"friends" scope isn't just thin here, it's too small to report a percentage
from at all.
"""

from dataclasses import dataclass

from app.models import Match
from app.models.match import Team
from app.scoring.credit_events import round_bonus
from app.services.player_graphs import win_color
from app.services.player_profile_types import match_win

ENEMY_WIN_TRIGGER = 11
PLAYERS_PER_TEAM = 5

FULL_BUY_LOADOUT_MIN = 20000
FORCE_SPEND_RATIO = 0.85
FULL_SAVE_LOADOUT_MAX = 10000

SAVE_TARGET_PER_PLAYER = 4700
RIFLE_COST_PER_PLAYER = 2900
SAVE_TARGET_TOTAL = SAVE_TARGET_PER_PLAYER * PLAYERS_PER_TEAM
RIFLE_COST_TOTAL = RIFLE_COST_PER_PLAYER * PLAYERS_PER_TEAM
FULL_SAVE_CEILING_TOTAL = SAVE_TARGET_TOTAL + RIFLE_COST_TOTAL

BUY_CATEGORIES = ("force_buy", "full_save")
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


def classify_response(total_loadout: int, total_remaining: int, next_round_bonus_total: int) -> str | None:
    """None means "not part of this analysis" -- neither a genuine
    spend-it-all force buy nor a genuine, economically real full save.
    `next_round_bonus_total` is the REAL credit bonus (win bonus, or the
    real loss-streak-scaled loss bonus) the team will earn going into next
    round, summed across all 5 players -- see
    app.scoring.credit_events.round_bonus. force_buy is checked first: a
    round where the team spent >= FORCE_SPEND_RATIO of its money is a force
    buy even if it also happens to project a healthy bank balance.
    full_save additionally requires this round's OWN loadout to be under
    FULL_SAVE_LOADOUT_MAX -- loadout reflects what's carried this round
    (including free carryover from surviving the previous round), not just
    fresh spend, so without this cap a "full_save" sample could still be
    fielding real guns via carryover."""
    total_available = total_loadout + total_remaining
    if total_available <= 0:
        return None
    if total_loadout >= FULL_BUY_LOADOUT_MIN:
        return None  # already a comfortable full buy this round -- no risk, not part of this analysis
    if total_loadout / total_available >= FORCE_SPEND_RATIO:
        return "force_buy"
    if total_loadout >= FULL_SAVE_LOADOUT_MAX:
        return None  # not spend-heavy enough to force-buy, but still carrying too much gear to call a real save
    projected_next_total = total_remaining + next_round_bonus_total
    if projected_next_total >= SAVE_TARGET_TOTAL and total_remaining < FULL_SAVE_CEILING_TOTAL:
        return "full_save"
    return None


def _enemy_at_11_response_samples(match: Match) -> list[tuple[Team, str, dict[str, bool | None]]]:
    """(responder_team, buy_category, {"immediate": bool, "next": bool|None,
    "match": bool|None}) for each team in `match` whose opponent's
    cumulative round-win count reached ENEMY_WIN_TRIGGER (11) at some round,
    AND whose immediate follow-up round has recorded loadout/remaining
    stats for the responding team, AND whose buy that round classifies as
    force_buy or full_save (see classify_response). A match contributes 0,
    1, or 2 samples -- one per team that had to respond to the OTHER team
    reaching 11 wins."""
    team_of = {mp.id: mp.team for mp in match.match_players}
    rounds_by_number = {r.round_number: r for r in match.rounds}
    if not rounds_by_number:
        return []
    max_round_number = max(rounds_by_number)
    round_outcomes = {rn: r.outcome for rn, r in rounds_by_number.items()}

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
        if total_loadout + total_remaining <= 0:
            continue  # no recorded loadout/remaining stats for this round

        immediate_winner = _winner_team(response_round.outcome)
        if immediate_winner is None:
            continue
        immediate_win = immediate_winner == responder

        # round_bonus only needs response_rn's own outcome (known: immediate_winner)
        # plus earlier rounds' outcomes to walk a loss streak back -- it doesn't
        # require response_rn + 1 to actually exist in the data.
        next_round_bonus_total = (
            round_bonus(round_outcomes, response_rn + 1, _team_string(responder)) * PLAYERS_PER_TEAM
        )
        category = classify_response(total_loadout, total_remaining, next_round_bonus_total)
        if category is None:
            continue  # neither a genuine force buy nor a genuine, economically real full save

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
    """{"friends": {"force_buy": {"immediate": {"total","win"}, "next":
    {...}, "match": {...}}, "full_save": {...}}, "all": {...}}. "friends"
    scope only counts a sample when the RESPONDING team (the one that must
    decide how to buy) includes a tracked roster player; "all" scope counts
    every sample in the DB. A sample that doesn't classify as either
    force_buy or full_save is excluded entirely (see classify_response) --
    not counted in either scope."""
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


_CATEGORY_LABELS = {"force_buy": "Force Buy", "full_save": "Full Save"}

_FORCE_SPEND_PCT = int(round(FORCE_SPEND_RATIO * 100))

# Precomputed once from the classify_response thresholds so the table always
# matches the actual bucket boundaries -- if any of the constants above
# change, these follow without a separate edit.
_CATEGORY_RANGE_LABELS = {
    "force_buy": f"{_FORCE_SPEND_PCT}%+ of available money spent this round",
    "full_save": (
        f"under {FULL_SAVE_LOADOUT_MAX:,} loadout this round, banked + next round's real credit bonus "
        f"projects to {SAVE_TARGET_TOTAL:,}+ (~{SAVE_TARGET_PER_PLAYER:,}/player)"
    ),
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
