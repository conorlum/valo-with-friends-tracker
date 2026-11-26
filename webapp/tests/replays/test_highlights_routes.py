"""Highlights DB inputs and HTTP contracts, using only synthetic SQLite data."""
import html
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models import Friendship, Player
from app.models.replay import Replay
from app.replays import format as fmt
from app.routers import replays as routes
from app.services import highlights, replay_control_views, replay_util, replays, util_model
from tests.support.highlights import highlight_replay, quiet_replay
from tests.support.site import site  # noqa: F401
from tests.replays.test_replay_routes import request


@pytest.fixture
def linked(site, tmp_path):
    return highlight_replay(site[1], tmp_path)


def test_payload_routes_and_privacy(site, linked):
    client, _ = site
    uuid, _, _, friends, enemies = linked
    response = client.get(f'/replays/{uuid}/highlights.json')
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {'potg', 'players', 'highlights', 'missing', 'clip_names', 'home_team', 'uuid'}
    assert body['highlights'] == highlights.order(body['highlights'])
    assert {'fight', 'winchance'} <= set(body['missing'])
    assert body['home_team'] == 'team-2'
    assert len(body['players']) == 5
    assert body['players']['5']['name'] == friends[0].split('#')[0]
    assert any(i['kind'] == 'clutch' and i['summary'] == '1v2 clutch' for i in body['highlights'])
    triple = next(i for i in body['highlights'] if i['round'] == 5 and i['kind'] == '3k')
    assert triple['score'] == [3, 1]
    fragment = client.get(f'/replays/{uuid}/potg')
    assert fragment.status_code == 200
    text = html.unescape(fragment.text)
    assert body['potg']['player'] in text and body['potg']['summary'] in text
    assert f'/replays/{uuid}?round=' in text and '&until=' in text
    assert '&export=1&hl=' + body['potg']['id'] in text
    for name in enemies:
        assert name.split('#')[0] not in response.text + fragment.text
    assert client.get(f'/replays/{uuid}/highlights.json', headers={'If-None-Match': response.headers['etag']}).status_code == 304


@pytest.mark.parametrize('suffix', ['highlights.json', 'potg'])
def test_refusals(site, linked, monkeypatch, suffix):
    client, factory = site
    uuid, rid, *_ = linked
    path = f'/replays/{uuid}/{suffix}'
    monkeypatch.setattr(settings, 'demo_mode', True)
    assert client.get(path).status_code == 404
    monkeypatch.setattr(settings, 'demo_mode', False)
    with factory() as db:
        db.get(Replay, rid).link_status = 'unlinked'
        db.commit()
    assert client.get(path).status_code == 404


def test_quiet_replay_keeps_every_friend(site, tmp_path):
    client, factory = site
    uuid, _, _, friends, _ = quiet_replay(factory, tmp_path)
    body = client.get(f'/replays/{uuid}/highlights.json').json()
    assert body['potg'] is None and body['highlights'] == []
    assert {p['name'] for p in body['players'].values()} == {n.split('#')[0] for n in friends}
    assert all(p['agent'] and p['best'] is None for p in body['players'].values())
    assert client.get(f'/replays/{uuid}/potg').text == ''


@pytest.mark.parametrize('away_slots', [[3], list(range(5))], ids=['away-friend', 'tie'])
def test_only_home_team_friends_get_highlights(site, linked, away_slots):
    client, factory = site
    with factory() as db:
        replay = db.get(Replay, linked[1])
        page = replays.page_context(db, replay)
        db.add_all(Friendship(owner_player_id=page['players']['5']['player_id'],
                              friend_player_id=page['players'][str(slot)]['player_id'])
                   for slot in away_slots)
        db.commit()
        loaded = highlights.load_inputs(db, replay)
        assert all(not loaded['players'][slot]['friend'] for slot in away_slots)
    response = client.get(f'/replays/{linked[0]}/highlights.json')
    assert response.status_code == 200
    body = response.json()
    if len(away_slots) == 5:
        assert body['home_team'] is None
        assert body['potg'] is None and body['players'] == {} and body['highlights'] == []
        assert body['clip_names'] == {s: p['agent'] for s, p in page['players'].items()}
    else:
        assert body['home_team'] == 'team-2'
        assert set(body['players']) == {str(s) for s in range(5, 10)}
        assert body['clip_names']['3'] == 'the enemy ' + page['players']['3']['agent']
        items = [body['potg'], *body['highlights']]
        for slot, player in body['players'].items():
            assert player['name'] == body['clip_names'][slot]
            items.extend([player['best'], *player['more']])
        for item in filter(None, items):
            assert item['slot'] >= 5
            assert item['player'] == body['clip_names'][str(item['slot'])]


@pytest.mark.parametrize('suffix', ['potg', 'highlights.json'])
def test_each_request_loads_page_context_once(site, linked, monkeypatch, suffix):
    calls = []
    original = replays.page_context
    def counted(*args, **kwargs):
        calls.append(args[1].id)
        return original(*args, **kwargs)
    monkeypatch.setattr(replays, 'page_context', counted)
    response = site[0].get(f'/replays/{linked[0]}/{suffix}')
    assert response.status_code == 200
    # match_payload shares the page too; Critical Util's calculations are unchanged.
    assert calls == [linked[1]]


@pytest.mark.parametrize('suffix', ['potg', 'highlights.json'])
@pytest.mark.parametrize('owner', [{'slot': None}, {'slot': 'unknown'}, {'slot': []}, {}],
                         ids=['null', 'invalid-string', 'invalid-type', 'missing'])
def test_unowned_util_casts_do_not_hide_owned_casts(site, linked, monkeypatch, suffix, owner):
    client, factory = site
    cast = {'i': 0, 'slot': '5', 't': 11., 'value': 8.25, 'ability': 'Q', 'scored': True}
    unowned = {k: v for k, v in cast.items() if k != 'slot'} | owner
    monkeypatch.setattr(replay_util, 'match_payload', lambda *a, **kw: {
        'updating': False, 'rounds': {'5': {'casts': [unowned, cast]}}})
    response = client.get(f'/replays/{linked[0]}/{suffix}')
    assert response.status_code == 200
    with factory() as db:
        replay = db.get(Replay, linked[1])
        inputs = highlights.load_inputs(db, replay)
        assert inputs['util'] == [dict(cast, slot=5, round=5)]
        assert any(i['kind'] == 'util' and i['slot'] == 5 and i['metric'] == 8.25
                   for i in highlights.find(inputs)['highlights'])


def test_loader_and_payload_accept_precomputed_page(site, linked, monkeypatch):
    with site[1]() as db:
        replay = db.get(Replay, linked[1])
        page = replays.page_context(db, replay)
        expected_util = replay_util.match_payload(db, replay, read_only=True)
        def unexpected(*a, **kw):
            raise AssertionError('A supplied page must be reused, including in match_payload')
        monkeypatch.setattr(replays, 'page_context', unexpected)
        assert replay_util.match_payload(db, replay, read_only=True, page=page) == expected_util
        assert highlights.load_inputs(db, replay, page=page)['home_team'] == 'team-2'
        assert highlights.payload(db, replay, page=page)['potg'] is not None


def test_viewer_labels_scores_and_tie(site, linked, monkeypatch):
    def no_highlights(*args):
        raise AssertionError('The viewer must not compute highlights')
    monkeypatch.setattr(highlights, 'load_inputs', no_highlights)
    _, factory = site
    with factory() as db:
        replay = db.get(Replay, linked[1])
        page = routes.viewer_context(request(), db, replay)['replay_data']
        assert page['round_scores']['5'] == [3, 1]
        assert page['clip_names']['0'] == 'the enemy Jett'
        assert page['clip_names']['5'] == 'Fixture5'
        enemies = db.query(Player).filter(Player.display_name.in_(linked[4])).all()
        db.add_all(Friendship(owner_player_id=enemies[0].id, friend_player_id=p.id) for p in enemies[1:])
        db.commit()
        page = routes.viewer_context(request(), db, replay)['replay_data']
        assert page['clip_names'] == {s: p['agent'] for s, p in page['players'].items()}


def test_clip_labels_and_before_round_scores():
    players = {'0': {'name': 'Main#TEST', 'agent': 'Jett', 'team': 'team-2'},
               '1': {'name': 'Opponent#TEST', 'agent': 'Sage', 'team': 'team-1'}}
    assert highlights.clip_labels(players, 'team-2') == {'0': 'Main', '1': 'the enemy Sage'}
    assert highlights.clip_labels(players, None) == {'0': 'Jett', '1': 'Sage'}
    rounds = {'1': {'db': {'winner': 'team-1'}}, '2': {'db': {'winner': 'team-2'}},
              '3': {'db': {'winner': None}}}
    assert highlights.round_scores(rounds, None)['3'] == [1, 1]


def test_loader_freshness_and_cast_shape(site, linked, monkeypatch):
    _, factory = site
    current = {'deaths': [[0, 10., 'A']], 't0': 9., 't1': 12., 'lost_m2': {'A': 10., 'B': 0.}, 'wipe': False}
    stale = dict(current, lost_m2={'A': 9999., 'B': 0.})
    monkeypatch.setattr(replay_control_views, 'load_round_summaries', lambda *a: SimpleNamespace(
        summaries={5: {'fights': [current]}, 7: {'fights': [stale]}}, stale=[7]))
    calls = []
    cast = {'i': 0, 'slot': 5, 't': 11., 'value': 8.25, 'ability': 'Q', 'scored': False}
    def util(*a, **kw):
        calls.append(kw)
        return {'updating': False, 'rounds': {'5': {'casts': [cast]}}}
    monkeypatch.setattr(replay_util, 'match_payload', util)
    monkeypatch.setattr(util_model, 'load', lambda db: {'excluded': []})
    with factory() as db:
        replay = db.get(Replay, linked[1])
        # page_context has its own decodes; count only the loader's additional pass.
        page = replays.page_context(db, replay)
        monkeypatch.setattr(replays, 'page_context', lambda *a: page)
        decoded = []
        decode = fmt.decode_blob
        def tracked(data):
            decoded.append(data)
            return decode(data)
        monkeypatch.setattr(fmt, 'decode_blob', tracked)
        inputs = highlights.load_inputs(db, replay)
        assert len(decoded) == replay.round_count
        assert inputs['team_fights'] == {5: [current]}
        assert inputs['model'] is None
        assert inputs['util'] == [dict(cast, round=5)]
        assert calls == [{'read_only': True, 'page': page}]
        assert inputs['rounds'][3]['score'] == {'team-1': 1, 'team-2': 1}
        assert 'winchance' in highlights.find(inputs)['missing']
        assert all(i['kind'] != 'util' for i in highlights.find(inputs)['highlights'])
        monkeypatch.setattr(replay_util, 'match_payload', lambda *a, **kw: {'updating': True, 'rounds': {'5': {'casts': [cast]}}})
        assert 'util' in highlights.payload(db, replay)['missing']
        monkeypatch.setattr(replay_control_views, 'load_round_summaries', lambda *a: SimpleNamespace(
            summaries={7: {'fights': [stale]}}, stale=[7]))
        assert highlights.load_inputs(db, replay)['team_fights'] is None


def test_loader_normalizes_string_util_slot(site, linked, monkeypatch):
    _, factory = site
    cast = {'slot': '6', 't': 11., 'value': 8.25, 'scored': True, 'ability': 'Q'}
    monkeypatch.setattr(replay_util, 'match_payload', lambda *a, **kw: {
        'updating': False, 'rounds': {'5': {'casts': [cast]}}})
    with factory() as db:
        replay = db.get(Replay, linked[1])
        inputs = highlights.load_inputs(db, replay)
        result = highlights.find(inputs)
        assert any(i['kind'] == 'util' and i['slot'] == 6 for i in result['highlights'])
        assert 'util' not in result['missing']
        assert inputs['util'] == [dict(cast, slot=6, round=5)]
        assert cast['slot'] == '6'


def test_corrected_lives_keep_db_deaths_and_revives():
    from app.services.replay_view import corrected_lives
    blob = {'alive': {'5': [[0., 15., 'kill'], [20., None, 'round_end']]}}
    lives = corrected_lives(blob, [{'slot': 5, 't_db': 10.4}], .4)
    assert lives == {5: [[0., 10.], [20., None]]}
    assert blob['alive']['5'][0][1] == 15.


@pytest.mark.parametrize('start,end,query', [
    (1.234, 12.345, 't=1.23&until=12.35'), (1.234, None, 't=1.23'),
    (1.125, 2.125, 't=1.13&until=2.13'), (1.005, 2.005, 't=1.00&until=2.00')])
def test_moment_url(start, end, query):
    assert routes.moment_url('uuid', {'round': 5, 't_start': start, 't_end': end}) == '/replays/uuid?round=5&' + query
