"""Match-win rate by "a team reached N round wins at some point in the
match" -- regardless of the opponent's score at that moment or at any other
time. Since a team's round count only ever increases by 1 per round won, a
team whose FINAL round count is >= N necessarily passed through exactly N at
some point, so "reached N" is just a >= filter on the final score already
stored on Match (team1_rounds_won/team2_rounds_won) -- no round-by-round
replay needed, unlike app.services.halftime_conversion_stats.

Same two-samples-per-match shape as that module and app.services.
round_combo_stats: a tied/undecided match (match_win returns None) is
excluded from both teams' samples.
"""

from dataclasses import dataclass

from app.models import Match
from app.models.match import Team
from app.services.player_graphs import win_color
from app.services.player_profile_types import match_win


def _team_string(team: Team) -> str:
    return team.value if hasattr(team, "value") else team


def _empty_bucket() -> dict[str, int]:
    return {"total": 0, "win": 0}


def _team_has_roster_player(match: Match, team: Team, roster_player_ids: set[int]) -> bool:
    return any(mp.team == team and mp.player_id in roster_player_ids for mp in match.match_players)


def _accumulate(buckets: dict[str, dict[str, int]], score: int, won_match: bool) -> None:
    bucket = buckets.setdefault(str(score), _empty_bucket())
    bucket["total"] += 1
    if won_match:
        bucket["win"] += 1


def compute_score_reached_stats(matches: list[Match], roster_player_ids: set[int]) -> dict:
    """{"friends": {"0": {"total", "win"}, "1": {...}, ...}, "all": {...}} --
    key N means "this team's final round count was >= N" (i.e. they reached
    N round wins at some point), value is whether they went on to win the
    match. A team's own sample at N=0 is every decisive match they played
    (everyone "reaches" 0 rounds won), so that bucket's win rate is ~50%."""
    variants: dict[str, dict[str, dict[str, int]]] = {"friends": {}, "all": {}}

    for match in matches:
        for team, final_score in ((Team.TEAM_1, match.team1_rounds_won), (Team.TEAM_2, match.team2_rounds_won)):
            won_match = match_win(match, _team_string(team))
            if won_match is None:
                continue
            is_roster = _team_has_roster_player(match, team, roster_player_ids)
            for n in range(final_score + 1):
                _accumulate(variants["all"], n, won_match)
                if is_roster:
                    _accumulate(variants["friends"], n, won_match)

    return variants


@dataclass
class ScoreReachedRow:
    score: int
    total: int
    win_pct: float
    fill: str


@dataclass
class ScoreReachedStats:
    rows: list[ScoreReachedRow]


def build_score_reached_stats(buckets: dict[str, dict[str, int]]) -> ScoreReachedStats:
    rows: list[ScoreReachedRow] = []
    max_score = max((int(k) for k in buckets), default=-1)
    for score in range(0, max_score + 1):
        bucket = buckets.get(str(score))
        if not bucket or bucket["total"] == 0:
            continue
        total = bucket["total"]
        win = bucket["win"]
        win_pct = win / total
        rows.append(ScoreReachedRow(score=score, total=total, win_pct=win_pct, fill=win_color(win_pct)))
    return ScoreReachedStats(rows=rows)
