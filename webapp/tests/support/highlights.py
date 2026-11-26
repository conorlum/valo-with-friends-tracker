"""Synthetic linked highlights, with the friends on team-2 and asymmetric scores."""
import copy

from app.models import Friendship, KillEvent, Match, MatchPlayer, Player, Round
from app.models.match import MatchSource, Team
from app.models.replay import Replay, ReplayRound
from app.replays import format as fmt
from app.replays.condense import condense_export_dir
from app.replays.store import store_replay
from tests.replays.replay_synthetic import AGENT_NAMES, SyntheticMatch
from tests.replays.test_replay_store import LINK_KILLS, OFFSET, ROUNDS


def highlight_replay(factory, tmp_path):
    kills = copy.deepcopy(LINK_KILLS)
    kills[5] = [(10., 5, 0), (14., 5, 1), (18., 5, 2)]
    kills[7] = [(8., 6, 0), (9., 7, 1), (10., 8, 2),
                (12., 3, 6), (13., 3, 7), (14., 3, 8), (15., 3, 9),
                (20., 5, 3), (25., 5, 4)]
    winners = {n: 'Red' if n in (2, 6) else 'Blue' for n in range(1, ROUNDS + 1)}
    synthetic = SyntheticMatch(rounds=ROUNDS, kills=kills, winners=winners)
    condensed = condense_export_dir(synthetic.write(tmp_path / 'export'), source_sha256=synthetic.source_sha256)
    with factory() as db:
        match = Match(external_id=condensed.match_uuid, source=MatchSource.SCRAPED, map_name='Ascent',
                      team1_rounds_won=2, team2_rounds_won=13)
        db.add(match)
        db.flush()
        players, mps = [], []
        for slot in range(10):
            player = Player(display_name=f'Fixture{slot}#TEST')
            db.add(player)
            db.flush()
            mp = MatchPlayer(match_id=match.id, player_id=player.id, agent=AGENT_NAMES[slot],
                             team=Team.TEAM_1 if slot < 5 else Team.TEAM_2)
            db.add(mp)
            db.flush()
            players.append(player)
            mps.append(mp.id)
        for n, script in kills.items():
            rnd = Round(match_id=match.id, round_number=n,
                        outcome=f"Team {'A' if winners[n] == 'Red' else 'B'} Elimination Win")
            db.add(rnd)
            db.flush()
            for t, killer, victim in script:
                db.add(KillEvent(round_id=rnd.id, killer_match_player_id=mps[killer],
                                 death_match_player_id=mps[victim], weapon='Vandal', event_time_seconds=t + OFFSET))
        db.add_all(Friendship(owner_player_id=players[5].id, friend_player_id=p.id) for p in players[6:])
        db.commit()
        result = store_replay(db, condensed, source='local')
        replay = db.get(Replay, result.replay_id)
        assert replay.link_status == 'linked'
        return (replay.match_uuid, replay.id, '/matches/' + match.external_id,
                [p.display_name for p in players[5:]], [p.display_name for p in players[:5]])


def quiet_replay(factory, tmp_path):
    result = highlight_replay(factory, tmp_path)
    with factory() as db:
        replay = db.get(Replay, result[1])
        round_ids = db.query(Round.id).filter(Round.match_id == replay.match_id)
        db.query(KillEvent).filter(KillEvent.round_id.in_(round_ids)).delete(synchronize_session=False)
        for row in db.query(ReplayRound).filter(ReplayRound.replay_id == replay.id):
            blob = fmt.decode_blob(row.data)
            blob['kills'] = []
            blob['alive'] = {str(slot): [[0., None, 'round_end']] for slot in range(10)}
            row.data = fmt.encode_blob(blob)
        replay.kill_map, replay.db_deaths = {}, {}
        db.commit()
    return result
