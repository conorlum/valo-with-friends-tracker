"""Round-win momentum: given a team won round R, what fraction of the time
did they ALSO win the next 1, 2, 3, 4, and 5 rounds in a row (R+1..R+k all
won)? Unlike halftime_conversion_stats/score_reached_stats, this isn't about
the match's final result at all -- it's purely about round-to-round
continuation, so every round in the match (not just a fixed pistol/half
cutoff) is a sample, and a match with a tied/unresolved final score still
contributes its round-level samples.

Sides swap at the round-13 half boundary (and every single round once
overtime starts, per attacking_team_for_round) -- winning round 12 as the
attacker says nothing about round 13, where the same team is now defending a
fresh economy on the other side. So a streak window is only counted while it
stays on one side of a swap: reuses app.services.map_side_stats.
attacking_team_for_round to detect a swap between consecutive rounds, and
stops extending the window (for that k and every larger k) the moment one is
crossed -- same "exclude incomplete windows" treatment as a missing/
non-decisive round.
"""

from dataclasses import dataclass

from app.models import Match
from app.models.match import Team
from app.services.map_side_stats import attacking_team_for_round
from app.services.player_graphs import win_color

MAX_STREAK = 5


def _winner_team(outcome: str | None) -> Team | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return Team.TEAM_1
    if outcome.startswith("Team B"):
        return Team.TEAM_2
    return None


def _team_has_roster_player(match: Match, team: Team, roster_player_ids: set[int]) -> bool:
    return any(mp.team == team and mp.player_id in roster_player_ids for mp in match.match_players)


def _empty_bucket() -> dict[str, int]:
    return {"total": 0, "win": 0}


def _accumulate(buckets: dict[str, dict[str, int]], k: int, won_all: bool) -> None:
    bucket = buckets.setdefault(str(k), _empty_bucket())
    bucket["total"] += 1
    if won_all:
        bucket["win"] += 1


def compute_round_streak_stats(matches: list[Match], roster_player_ids: set[int]) -> dict:
    """{"friends": {"1": {"total", "win"}, ..., "5": {...}}, "all": {...}} --
    key k means "won round R, then also won each of R+1..R+k", value is
    whether that k-round follow-up streak actually happened."""
    variants: dict[str, dict[str, dict[str, int]]] = {"friends": {}, "all": {}}

    for match in matches:
        rounds_by_number = {r.round_number: r for r in match.rounds}
        if not rounds_by_number:
            continue
        max_round = max(rounds_by_number)

        winners: dict[int, Team] = {}
        for rn, r in rounds_by_number.items():
            w = _winner_team(r.outcome)
            if w is not None:
                winners[rn] = w
        if not winners:
            continue

        for team in (Team.TEAM_1, Team.TEAM_2):
            is_roster = _team_has_roster_player(match, team, roster_player_ids)
            for rn in range(1, max_round + 1):
                if winners.get(rn) != team:
                    continue
                prev_round = rn
                won_all = True
                for k in range(1, MAX_STREAK + 1):
                    next_round = rn + k
                    if attacking_team_for_round(prev_round) != attacking_team_for_round(next_round):
                        break  # side swap since the last round -- momentum doesn't carry across it
                    w = winners.get(next_round)
                    if w is None:
                        break
                    won_all = won_all and (w == team)
                    _accumulate(variants["all"], k, won_all)
                    if is_roster:
                        _accumulate(variants["friends"], k, won_all)
                    prev_round = next_round

    return variants


@dataclass
class RoundStreakRow:
    next_n: int
    total: int
    win_pct: float
    fill: str


@dataclass
class RoundStreakStats:
    rows: list[RoundStreakRow]


def build_round_streak_stats(buckets: dict[str, dict[str, int]]) -> RoundStreakStats:
    rows: list[RoundStreakRow] = []
    for k in range(1, MAX_STREAK + 1):
        bucket = buckets.get(str(k))
        if not bucket or bucket["total"] == 0:
            continue
        total = bucket["total"]
        win = bucket["win"]
        win_pct = win / total
        rows.append(RoundStreakRow(next_n=k, total=total, win_pct=win_pct, fill=win_color(win_pct)))
    return RoundStreakStats(rows=rows)
