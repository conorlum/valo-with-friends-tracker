"""T14: exercise the adapter and dates without importing the local GUI utility."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
import pytest
from app.models import Match, Player, Round, KillEvent, RoundPlayerStat, ImpactScore
from tests.support.site import site  # noqa: F401


@pytest.fixture
def adapter(monkeypatch):
    path = Path(__file__).parents[1]/'app/adapters/demo_match_source.py'
    rows = {name: {'Agent': agent, 'Team': team, 'RoundInfo': [{'Score': 200, 'Kills': 1, 'Deaths': 0,
            'Assists': 0, 'Loadout': 800, 'Remaining': 0}]} for name,agent,team in
            [('SampleA','Jett','team-1'),('SampleB','Sova','team-2')]}
    events = [{'Event': 'Kill', 'killerTeam': 'team-1', 'killerCharacter': 'Jett', 'deathTeam': 'team-2',
               'deathCharacter': 'Sova', 'killWeapon': 'Classic', 'eventTime': 18},
              {'Event': 'Planted', 'eventTime': 40}, {'Event': 'Defused', 'eventTime': 60}]
    pipeline = SimpleNamespace(parseRoundOutcome=lambda key: {'1': 'Team B Defuse Win'},
        parseRoundKillList=lambda key: {'1': events}, parsePlayerRoundInfo=lambda key: rows,
        reverseAgentTeamToPlayerUsername=lambda rows: {'team-1Jett':'SampleA', 'team-2Sova':'SampleB'})
    monkeypatch.setitem(sys.modules, 'matchDataPipeline', pipeline)
    spec = importlib.util.spec_from_file_location('demo_adapter_under_test', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module, pipeline


@pytest.mark.parametrize('key,expected', [('Haven101725908', datetime(2025,10,17,21,8)),
    ('Sunset11625924', datetime(2025,11,6,21,24)), ('Bind0101251200', datetime(2025,1,1,12,0)),
    ('Unknown', None), ('Haven000025908', None), ('Havenabcdefghij', None)])
def test_filename_dates_and_known_override(adapter, key, expected):
    module, _ = adapter
    assert module._parse_played_at(key) == expected


def test_adapter_maps_rounds_events_and_replaces_without_orphans(site, adapter):
    _, factory = site
    module, _ = adapter
    with factory() as db:
        m = module.load_match(db, 'Haven101725908')
        assert (m.map_name, m.team1_rounds_won, m.team2_rounds_won) == ('Haven', 0, 1)
        rnd = db.query(Round).one()
        assert rnd.plant_time == 40 and rnd.defused and rnd.defuse_time == 60
        event = db.query(KillEvent).one()
        assert event.weapon == 'Classic' and event.event_time_seconds == 18
        db.add(ImpactScore(round_id=rnd.id, match_player_id=event.killer_match_player_id,
                           kill_impact=123, death_impact=0, impact=123)); db.commit()
    with factory() as db:
        module.load_match(db, 'Haven101725908')
    with factory() as fresh:
        assert fresh.query(Match).count() == 1 and fresh.query(Player).count() == 2
        assert fresh.query(RoundPlayerStat).count() == 2 and fresh.query(KillEvent).count() == 1
        assert fresh.query(ImpactScore).count() == 0


def test_failed_replacement_rolls_back_the_original(site, adapter):
    _, factory = site
    module, pipeline = adapter
    with factory() as db: module.load_match(db, 'Haven101725908')
    def malformed(key): raise ValueError('malformed parsed input')
    pipeline.parseRoundOutcome = malformed
    with factory() as db:
        with pytest.raises(ValueError, match='malformed'): module.load_match(db, 'Haven101725908')
        db.rollback()
    with factory() as fresh:
        assert fresh.query(Match).count() == 1 and fresh.query(KillEvent).count() == 1
