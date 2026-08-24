"""Force-buying after LOSING a pistol round -- the mirror image of
app.services.eco_followup (which looks at the WINNING team's buy). Here the
question is: when the pistol-round LOSER spends at least FORCE_THRESHOLD of
its available money (team total loadout / team total (loadout + remaining),
summed across all 5 players) in the very next round -- i.e. forces rather
than eco'ing -- how often does that pay off?

Four independent win rates are tracked, each its own total/win pair since a
short match may not reach every round:
  - "forced": did the force-buy round itself win
  - "next": did the round right after that win
  - "next2": did the round after THAT win
  - "match": did the team go on to win the match overall

A match contributes 0, 1, or 2 samples (one per pistol round -- 1 and 13 --
that was decisively lost AND had a recorded, ostensibly-forced follow-up
round). "friends" scope only counts a sample when the losing team included a
tracked roster player; "all" scope counts every one. Computed alongside the
other site-wide stats in app.services.site_stats._compute_site_stats, off the
same shared match load.
"""

from dataclasses import dataclass

from app.models import Match, Round
from app.models.match import Team
from app.services.player_graphs import win_color
from app.services.player_profile_types import match_win

FORCE_THRESHOLD = 0.7
PISTOL_ROUNDS = (1, 13)
FORCE_BUY_METRICS = ("forced", "next", "next2", "match")


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


def _round_win(rounds_by_number: dict[int, Round], round_number: int, team: Team) -> bool | None:
    r = rounds_by_number.get(round_number)
    if r is None:
        return None
    w = _winner_team(r.outcome)
    if w is None:
        return None
    return w == team


def _pistol_loss_force_samples(match: Match) -> list[tuple[Team, dict[str, bool | None]]]:
    """(losing_team, {"forced": bool, "next": bool|None, "next2": bool|None,
    "match": bool|None}) for each pistol round (1, 13) that was decisively
    lost AND whose immediate follow-up round shows the loser spending >=
    FORCE_THRESHOLD of its available money that round."""
    team_of = {mp.id: mp.team for mp in match.match_players}
    rounds_by_number = {r.round_number: r for r in match.rounds}

    samples: list[tuple[Team, dict[str, bool | None]]] = []
    for pistol_rn in PISTOL_ROUNDS:
        pistol_round = rounds_by_number.get(pistol_rn)
        if pistol_round is None:
            continue
        winner = _winner_team(pistol_round.outcome)
        if winner is None:
            continue
        loser = Team.TEAM_2 if winner == Team.TEAM_1 else Team.TEAM_1

        force_rn = pistol_rn + 1
        force_round = rounds_by_number.get(force_rn)
        if force_round is None:
            continue

        total_loadout = sum(
            stat.loadout for stat in force_round.player_stats if team_of.get(stat.match_player_id) == loser
        )
        total_remaining = sum(
            stat.remaining for stat in force_round.player_stats if team_of.get(stat.match_player_id) == loser
        )
        total_available = total_loadout + total_remaining
        if total_available <= 0:
            continue  # no recorded loadout/remaining stats for this round
        if total_loadout / total_available < FORCE_THRESHOLD:
            continue  # didn't spend enough of what was available to count as a force

        forced_win = _round_win(rounds_by_number, force_rn, loser)
        if forced_win is None:
            continue

        samples.append(
            (
                loser,
                {
                    "forced": forced_win,
                    "next": _round_win(rounds_by_number, force_rn + 1, loser),
                    "next2": _round_win(rounds_by_number, force_rn + 2, loser),
                    "match": match_win(match, _team_string(loser)),
                },
            )
        )
    return samples


def _team_has_roster_player(match: Match, team: Team, roster_player_ids: set[int]) -> bool:
    return any(mp.team == team and mp.player_id in roster_player_ids for mp in match.match_players)


def _empty_variant() -> dict[str, dict[str, int]]:
    return {metric: {"total": 0, "win": 0} for metric in FORCE_BUY_METRICS}


def _accumulate(variant: dict[str, dict[str, int]], outcomes: dict[str, bool | None]) -> None:
    for metric in FORCE_BUY_METRICS:
        outcome = outcomes[metric]
        if outcome is None:
            continue
        bucket = variant[metric]
        bucket["total"] += 1
        if outcome:
            bucket["win"] += 1


def compute_force_buy_stats(matches: list[Match], roster_player_ids: set[int]) -> dict:
    """{"friends": {"forced": {"total", "win"}, "next": {...}, "next2": {...},
    "match": {...}}, "all": {...}}."""
    variants = {"friends": _empty_variant(), "all": _empty_variant()}

    for match in matches:
        for loser, outcomes in _pistol_loss_force_samples(match):
            _accumulate(variants["all"], outcomes)
            if _team_has_roster_player(match, loser, roster_player_ids):
                _accumulate(variants["friends"], outcomes)

    return variants


@dataclass
class ForceBuyRow:
    label: str
    total: int
    win_pct: float
    fill: str


@dataclass
class ForceBuyStats:
    rows: list[ForceBuyRow]
    total_samples: int


_ROW_LABELS = {
    "forced": "Won the forced round",
    "next": "Won the next round",
    "next2": "Won the round after that",
    "match": "Won the match",
}


def build_force_buy_stats(variant: dict[str, dict[str, int]]) -> ForceBuyStats:
    rows: list[ForceBuyRow] = []
    for metric in FORCE_BUY_METRICS:
        bucket = variant.get(metric, {"total": 0, "win": 0})
        total = bucket["total"]
        if total == 0:
            continue
        win_pct = bucket["win"] / total
        rows.append(ForceBuyRow(label=_ROW_LABELS[metric], total=total, win_pct=win_pct, fill=win_color(win_pct)))

    forced_bucket = variant.get("forced", {"total": 0})
    return ForceBuyStats(rows=rows, total_samples=forced_bucket["total"])
