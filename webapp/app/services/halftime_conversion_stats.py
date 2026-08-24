"""Match-win rate by halftime score -- given a team won N of the first 12
rounds (round 13+ not looked at here), what fraction of THOSE matches did
they go on to win overall?

Same two-samples-per-match shape as app.services.round_combo_stats: a match
with a full, decisive first half AND a decisive final result contributes one
sample per team (team 1's "9 rounds at half" is team 2's "3 rounds at half"),
so a match with an equal final round count (forfeit/never completed) is
dropped from both.
"""

from dataclasses import dataclass

from app.models import Match
from app.models.match import Team
from app.services.player_graphs import win_color
from app.services.player_profile_types import match_win

HALF_ROUNDS = tuple(range(1, 13))


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


def _empty_bucket() -> dict[str, int]:
    return {"total": 0, "win": 0}


def _halftime_samples(match: Match) -> list[tuple[Team, int, bool]]:
    """(team, own_half_wins, won_match) per team's perspective -- only when
    every round 1-12 AND the match itself have a decisive outcome."""
    rounds_by_number = {r.round_number: r for r in match.rounds}
    winners: list[Team] = []
    for rn in HALF_ROUNDS:
        r = rounds_by_number.get(rn)
        if r is None:
            return []
        w = _winner_team(r.outcome)
        if w is None:
            return []
        winners.append(w)

    team1_half_wins = sum(1 for w in winners if w == Team.TEAM_1)
    team2_half_wins = 12 - team1_half_wins

    samples: list[tuple[Team, int, bool]] = []
    for team, half_wins in ((Team.TEAM_1, team1_half_wins), (Team.TEAM_2, team2_half_wins)):
        won_match = match_win(match, _team_string(team))
        if won_match is None:
            return []
        samples.append((team, half_wins, won_match))
    return samples


def _team_has_roster_player(match: Match, team: Team, roster_player_ids: set[int]) -> bool:
    return any(mp.team == team and mp.player_id in roster_player_ids for mp in match.match_players)


def _accumulate(buckets: dict[str, dict[str, int]], own_half_wins: int, won_match: bool) -> None:
    bucket = buckets.setdefault(str(own_half_wins), _empty_bucket())
    bucket["total"] += 1
    if won_match:
        bucket["win"] += 1


def compute_halftime_conversion_stats(matches: list[Match], roster_player_ids: set[int]) -> dict:
    """{"friends": {"0": {"total", "win"}, ..., "12": {...}}, "all": {...}} --
    key is the team's OWN round count after 12 rounds (their opponent's is
    implicitly 12 minus that)."""
    variants: dict[str, dict[str, dict[str, int]]] = {"friends": {}, "all": {}}

    for match in matches:
        for team, own_half_wins, won_match in _halftime_samples(match):
            _accumulate(variants["all"], own_half_wins, won_match)
            if _team_has_roster_player(match, team, roster_player_ids):
                _accumulate(variants["friends"], own_half_wins, won_match)

    return variants


@dataclass
class HalftimeConversionRow:
    own: int
    opp: int
    total: int
    win_pct: float
    fill: str


@dataclass
class HalftimeConversionStats:
    rows: list[HalftimeConversionRow]


def build_halftime_conversion_stats(buckets: dict[str, dict[str, int]]) -> HalftimeConversionStats:
    rows: list[HalftimeConversionRow] = []
    for own in range(12, -1, -1):
        bucket = buckets.get(str(own))
        if not bucket or bucket["total"] == 0:
            continue
        total = bucket["total"]
        win = bucket["win"]
        win_pct = win / total
        rows.append(
            HalftimeConversionRow(own=own, opp=12 - own, total=total, win_pct=win_pct, fill=win_color(win_pct))
        )
    return HalftimeConversionStats(rows=rows)
