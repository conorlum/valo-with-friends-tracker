"""Tiny persisted player-page history, with explicit score fixtures (no scoring run)."""
from datetime import datetime, timedelta, timezone
from app.models import Player, Match, MatchPlayer, Round, RoundPlayerStat, KillEvent, ImpactScore
from app.models.match import MatchSource, Team


def game(db, player, enemy, i, agent='Jett', external_id=None):
    m = Match(external_id=external_id or f'journey-{i}', source=MatchSource.SCRAPED, map_name='Ascent',
              played_at=datetime(2026,10,1,tzinfo=timezone.utc)+timedelta(hours=i),
              team1_rounds_won=13, team2_rounds_won=5)
    db.add(m); db.flush()
    a = MatchPlayer(match_id=m.id, player_id=player.id, agent=agent, team=Team.TEAM_1)
    b = MatchPlayer(match_id=m.id, player_id=enemy.id, agent='Reyna', team=Team.TEAM_2)
    db.add_all([a,b]); db.flush()
    rnd = Round(match_id=m.id, round_number=1, outcome='Team A Elimination Win')
    db.add(rnd); db.flush()
    for mp,k,d in [(a,1,0),(b,0,1)]:
        db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, kills=k, deaths=d, assists=0,
                              loadout=800, remaining=0, score=200*k))
        db.add(ImpactScore(round_id=rnd.id, match_player_id=mp.id, kill_impact=100*k,
                           death_impact=100*d, impact=100*(k-d)))
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=a.id, death_match_player_id=b.id,
                     weapon='Classic', event_time_seconds=10))
    db.flush()
    return m, a


def history(factory, count=4):
    with factory() as db:
        me, enemy, alt = [Player(display_name=n) for n in ('Main#TEST','Enemy#TEST','Alt#TEST')]
        db.add_all([me,enemy,alt]); db.flush()
        for i in range(count): game(db, me, enemy, i, 'Jett' if i%2==0 else 'Sova')
        db.commit()
        return me.id, enemy.id, alt.id
