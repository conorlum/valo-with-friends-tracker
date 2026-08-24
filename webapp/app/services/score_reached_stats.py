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

Capped at N=12: reaching 13 is (with rare OT exceptions) the win condition
itself, so that bucket's win rate is trivially ~97-99% and adds nothing over
just knowing the team won -- not a useful row. What IS useful about the 13+
region is how often it happens at all, so it's replaced with a single
match-level "how often did this reach overtime" rate instead.
"""

from dataclasses import dataclass

from app.models import Match
from app.models.match import Team
from app.services.player_graphs import win_color
from app.services.player_profile_types import match_win

MAX_DISPLAYED_SCORE = 12

# A match that's still undecided after round 24 (12-12) goes to overtime, so
# any round numbered past 24 only exists in an OT match -- see
# app.services.map_side_stats.attacking_team_for_round's docstring for the
# empirically-verified round-numbering convention this relies on.
LAST_REGULATION_ROUND = 24


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


def _match_went_to_ot(match: Match) -> bool:
    return any(r.round_number > LAST_REGULATION_ROUND for r in match.rounds)


def compute_score_reached_stats(matches: list[Match], roster_player_ids: set[int]) -> dict:
    """{"friends": {"buckets": {"0": {"total", "win"}, ..., "12": {...}}, "ot":
    {"total", "count"}}, "all": {...}}. A bucket key N means "this team's
    final round count was >= N" (capped at MAX_DISPLAYED_SCORE -- see module
    docstring), value is whether they went on to win the match. A team's own
    sample at N=0 is every decisive match they played (everyone "reaches" 0
    rounds won), so that bucket's win rate is ~50%. "ot" is match-level (one
    sample per decisive match, not per team) -- "total" decisive matches and
    "count" of those that went to overtime."""
    buckets: dict[str, dict[str, dict[str, int]]] = {"friends": {}, "all": {}}
    ot: dict[str, dict[str, int]] = {"friends": {"total": 0, "count": 0}, "all": {"total": 0, "count": 0}}

    for match in matches:
        for team, final_score in ((Team.TEAM_1, match.team1_rounds_won), (Team.TEAM_2, match.team2_rounds_won)):
            won_match = match_win(match, _team_string(team))
            if won_match is None:
                continue
            is_roster = _team_has_roster_player(match, team, roster_player_ids)
            for n in range(min(final_score, MAX_DISPLAYED_SCORE) + 1):
                _accumulate(buckets["all"], n, won_match)
                if is_roster:
                    _accumulate(buckets["friends"], n, won_match)

        team1_won = match_win(match, "team-1")
        if team1_won is None:
            continue
        went_to_ot = _match_went_to_ot(match)
        is_roster_match = any(mp.player_id in roster_player_ids for mp in match.match_players)
        ot["all"]["total"] += 1
        if went_to_ot:
            ot["all"]["count"] += 1
        if is_roster_match:
            ot["friends"]["total"] += 1
            if went_to_ot:
                ot["friends"]["count"] += 1

    return {
        "friends": {"buckets": buckets["friends"], "ot": ot["friends"]},
        "all": {"buckets": buckets["all"], "ot": ot["all"]},
    }


@dataclass
class ScoreReachedRow:
    score: int
    total: int
    win_pct: float
    fill: str


@dataclass
class ScoreReachedStats:
    rows: list[ScoreReachedRow]
    ot_total: int
    ot_pct: float | None


def build_score_reached_stats(variant: dict) -> ScoreReachedStats:
    buckets = variant["buckets"]
    rows: list[ScoreReachedRow] = []
    max_score = min(max((int(k) for k in buckets), default=-1), MAX_DISPLAYED_SCORE)
    for score in range(0, max_score + 1):
        bucket = buckets.get(str(score))
        if not bucket or bucket["total"] == 0:
            continue
        total = bucket["total"]
        win = bucket["win"]
        win_pct = win / total
        rows.append(ScoreReachedRow(score=score, total=total, win_pct=win_pct, fill=win_color(win_pct)))

    ot = variant["ot"]
    ot_total = ot["total"]
    ot_pct = ot["count"] / ot_total if ot_total else None
    return ScoreReachedStats(rows=rows, ot_total=ot_total, ot_pct=ot_pct)
