"""Explicit, unpersisted matches: expected aggregates belong in each test."""
from app.models import Match, MatchPlayer, Round, RoundPlayerStat
from app.models.match import MatchSource, Team


def match(winners="", *, start=1, score=(13, 5), roster=((1, 2), (3, 4)), map_name="Bind"):
    result = Match(id=1, external_id="stats-fixture", source=MatchSource.SCRAPED,
                   map_name=map_name, team1_rounds_won=score[0], team2_rounds_won=score[1])
    result.match_players = [
        MatchPlayer(id=pid, player_id=pid, team=team, agent="Jett")
        for team, ids in zip((Team.TEAM_1, Team.TEAM_2), roster) for pid in ids
    ]
    result.rounds = [Round(round_number=rn, outcome={"A": "Team A Elimination Win",
                                                   "B": "Team B Elimination Win"}.get(w), player_stats=[])
                     for rn, w in enumerate(winners, start)]
    return result


def economy(result, rn, budgets):
    """budgets: {match_player_id: (loadout, remaining)}."""
    rnd = next(r for r in result.rounds if r.round_number == rn)
    rnd.player_stats = [RoundPlayerStat(match_player_id=pid, loadout=loadout, remaining=remaining)
                        for pid, (loadout, remaining) in budgets.items()]
    return result
