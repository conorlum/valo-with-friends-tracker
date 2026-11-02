"""T08: HTTP profile reads, actual PostgreSQL cache writes and committed social changes."""
from urllib.parse import quote
import pytest
from app.models import PlayerViewCache, AccountGroupViewCache, Player, Friendship
from app.routers import players as routes, friends as friend_routes
from app.services.account_groups import link_account
from app.services.player_view_cache import cache_version, store_player_views
from app.services.player_views import compute_player_views
from tests.support.site import pg_site  # noqa: F401
from tests.support.matches import history, game

PATH = '/players/Main%23TEST'


def test_cold_warm_recent_career_and_filtered_rows_use_real_storage(pg_site, monkeypatch):
    client, factory = pg_site
    main, enemy, _ = history(factory, 31)
    monkeypatch.setattr(routes, 'PAGE_BOOTSTRAP_DRAWS', 10)
    for suffix, key, count in [('', 'recent', 30), ('/career', 'career', 31),
                                ('?filter=agent:Jett', 'recent|agent:Jett', 16)]:
        assert client.get(PATH+suffix).status_code == 200
        with factory() as fresh:
            row = fresh.query(PlayerViewCache).filter_by(player_id=main, scope=key).one()
            assert row.version == cache_version() and len(row.data['profile']['matches']) == count
        with monkeypatch.context() as scoped:
            scoped.setattr(routes, 'compute_player_views', lambda *a, **kw: pytest.fail('warm request recomputed'))
            assert client.get(PATH+suffix).status_code == 200


@pytest.mark.parametrize('combined', [False, True])
@pytest.mark.parametrize('state', ['corrupt', 'stale'])
def test_corrupt_or_old_filtered_rows_recompute_and_replace(pg_site, monkeypatch, combined, state):
    client, factory = pg_site
    main, _, alt = history(factory)
    monkeypatch.setattr(routes, 'PAGE_BOOTSTRAP_DRAWS', 10)
    suffix = '?filter=agent:Jett' + ('&accounts=all' if combined else '')
    if combined:
        with factory() as db: assert link_account(db, main, alt) is None; db.commit()
    assert client.get(PATH+suffix).status_code == 200
    model = AccountGroupViewCache if combined else PlayerViewCache
    with factory() as db:
        row = db.query(model).filter_by(scope='recent|agent:Jett').one()
        if state == 'corrupt': row.data = {'bad': 'blob'}
        else: row.version -= 1
        db.commit()
    assert client.get(PATH+suffix).status_code == 200
    with factory() as fresh:
        row = fresh.query(model).filter_by(scope='recent|agent:Jett').one()
        assert row.version == cache_version() and len(row.data['profile']['matches']) == 2


def test_cache_write_failure_still_serves_live_content_and_keeps_request_usable(pg_site, monkeypatch):
    client, factory = pg_site
    history(factory)
    def refused(*a, **kw): raise RuntimeError('cache write unavailable')
    monkeypatch.setattr(routes, 'store_player_views', refused)
    response = client.get(PATH)
    assert response.status_code == 200 and 'Main' in response.text
    assert client.get('/players').status_code == 200
    with factory() as fresh: assert fresh.query(PlayerViewCache).count() == 0


def test_friend_mutation_and_cache_retirement_commit_together_but_failure_rolls_back(pg_site, monkeypatch):
    client, factory = pg_site
    main, enemy, alt = history(factory)
    with factory() as db:
        for pid in (main, alt):
            views = compute_player_views(db, db.get(Player, pid), 30, 10)
            store_player_views(db, pid, {'recent': views})
    client.post('/login', data={'display_name':'Main#TEST'})
    real = friend_routes._invalidate_owner_circle
    def fail_after_delete(*args): real(*args); raise RuntimeError('mutation interrupted')
    with monkeypatch.context() as scoped:
        scoped.setattr(friend_routes, '_invalidate_owner_circle', fail_after_delete)
        with pytest.raises(RuntimeError, match='mutation interrupted'):
            client.post('/friends/add', data={'display_name':'Enemy#TEST'})
    with factory() as fresh:
        assert fresh.query(Friendship).count() == 0
        assert {r.player_id for r in fresh.query(PlayerViewCache)} == {main, alt}
    assert client.post('/friends/add', data={'display_name':'Enemy#TEST'}).status_code == 303
    with factory() as fresh:
        assert fresh.query(Friendship).count() == 1
        assert {r.player_id for r in fresh.query(PlayerViewCache)} == {alt}
    assert client.get(PATH).status_code == 200
