"""Match-win rate broken down by how a team did in the pistol round(s) and
their immediate follow-up round(s) -- e.g. "won the pistol, lost round 2":
what fraction of THOSE matches were won overall? Two granularities:

  - "first_half": round 1 (pistol) x round 2 -- 4 combinations (WW/WL/LW/LL)
  - "full": round 1 x round 2 x round 13 (pistol) x round 14 -- 16 combinations

Unlike app.services.eco_followup (which only looks at the pistol WINNER's
side), this needs the full win/loss space for both teams -- a losing team's
"lost the pistol, lost round 2" combo matters here too. So a match with every
relevant round AND the match itself decided contributes exactly 2 samples per
granularity (one per team), which are each other's logical complement (team
1's WW is team 2's LL, etc.).
"""

import itertools
from dataclasses import dataclass

from app.models import Match
from app.models.match import Team
from app.services.player_graphs import win_color
from app.services.player_profile_types import match_win

FIRST_HALF_ROUNDS = (1, 2)
FULL_ROUNDS = (1, 2, 13, 14)

ROUND_LABELS = {
    "first_half": ["Round 1 (Pistol)", "Round 2"],
    "full": ["Round 1 (Pistol)", "Round 2", "Round 13 (Pistol)", "Round 14"],
}


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


def _combo_key(flags: list[bool]) -> str:
    return "".join("W" if f else "L" for f in flags)


def _combo_samples(match: Match, round_numbers: tuple[int, ...]) -> list[tuple[Team, str, bool]]:
    """(team, combo_key, won_match) for each team's perspective -- only when
    every round in round_numbers AND the match itself have a decisive
    outcome. A tied/unresolved match excludes BOTH teams' samples, same as
    match_win's own None-for-a-tie contract."""
    rounds_by_number = {r.round_number: r for r in match.rounds}
    winners: list[Team] = []
    for rn in round_numbers:
        r = rounds_by_number.get(rn)
        if r is None:
            return []
        w = _winner_team(r.outcome)
        if w is None:
            return []
        winners.append(w)

    samples: list[tuple[Team, str, bool]] = []
    for team in (Team.TEAM_1, Team.TEAM_2):
        won_match = match_win(match, _team_string(team))
        if won_match is None:
            return []
        combo = _combo_key([w == team for w in winners])
        samples.append((team, combo, won_match))
    return samples


def _empty_bucket() -> dict[str, int]:
    return {"total": 0, "win": 0}


def _team_has_roster_player(match: Match, team: Team, roster_player_ids: set[int]) -> bool:
    return any(mp.team == team and mp.player_id in roster_player_ids for mp in match.match_players)


def _accumulate(buckets: dict[str, dict[str, int]], combo: str, won_match: bool) -> None:
    bucket = buckets.setdefault(combo, _empty_bucket())
    bucket["total"] += 1
    if won_match:
        bucket["win"] += 1


def compute_round_combo_stats(matches: list[Match], roster_player_ids: set[int]) -> dict:
    """{"friends": {"first_half": {...}, "full": {...}}, "all": {...}} --
    each inner dict is combo_key ("WW", "WLWL", ...) -> {"total", "win"}
    (count of team-perspective samples with that round-outcome pattern, and
    how many of those went on to win the match)."""
    variants: dict[str, dict[str, dict[str, dict[str, int]]]] = {
        "friends": {"first_half": {}, "full": {}},
        "all": {"first_half": {}, "full": {}},
    }

    for match in matches:
        for granularity, round_numbers in (("first_half", FIRST_HALF_ROUNDS), ("full", FULL_ROUNDS)):
            for team, combo, won_match in _combo_samples(match, round_numbers):
                _accumulate(variants["all"][granularity], combo, won_match)
                if _team_has_roster_player(match, team, roster_player_ids):
                    _accumulate(variants["friends"][granularity], combo, won_match)

    return variants


@dataclass
class RoundComboRow:
    outcomes: list[str]
    total: int
    win_pct: float
    fill: str


@dataclass
class RoundComboStats:
    round_labels: list[str]
    rows: list[RoundComboRow]
    total_samples: int


def build_round_combo_stats(buckets: dict[str, dict[str, int]], granularity: str) -> RoundComboStats:
    round_labels = ROUND_LABELS[granularity]
    rows: list[RoundComboRow] = []
    total_samples = 0
    for combo in ("".join(bits) for bits in itertools.product("WL", repeat=len(round_labels))):
        bucket = buckets.get(combo)
        if not bucket or bucket["total"] == 0:
            continue
        total = bucket["total"]
        win = bucket["win"]
        total_samples += total
        win_pct = win / total
        rows.append(RoundComboRow(outcomes=list(combo), total=total, win_pct=win_pct, fill=win_color(win_pct)))

    # Highest match win rate first -- reads as a natural best-to-worst
    # ranking rather than the arbitrary WW/WL/LW/LL enumeration order.
    rows.sort(key=lambda r: -r.win_pct)
    return RoundComboStats(round_labels=round_labels, rows=rows, total_samples=total_samples)
