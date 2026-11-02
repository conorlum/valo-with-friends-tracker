"""T04/T09: requests cross real session middleware, routing, form parsing and the database."""
from urllib.parse import quote, urlencode
import pytest
from app.models import Player
from app.config import settings
from tests.support.site import site  # noqa: F401


def add(factory, *names):
    with factory() as db:
        rows = [Player(display_name=n) for n in names]
        db.add_all(rows); db.commit()
        return [p.id for p in rows]


def test_login_redirect_logout_and_repeated_logout(site):
    client, factory = site
    add(factory, "Alice#NA1")
    assert client.get('/login').status_code == 200
    reply = client.post('/login', data={"display_name": "Alice#NA1"})
    assert reply.status_code == 303 and reply.headers["location"] == "/"
    cookie = reply.headers["set-cookie"]
    assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()
    root = client.get('/')
    assert root.status_code == 307 and root.headers['location'] == '/players/Alice%23NA1'
    assert client.get(root.headers['location']).status_code == 200
    assert client.post('/logout').status_code == 303
    assert client.get('/').status_code == 200
    assert client.post('/logout').status_code == 303


@pytest.mark.parametrize("data,status", [({}, 422), ({"display_name": "unknown"}, 404)])
def test_bad_login_changes_no_session(site, data, status):
    client, _ = site
    assert client.post('/login', data=data).status_code == status
    assert not client.cookies
    assert client.get('/').status_code == 200


def test_missing_player_and_tampered_session_are_anonymous(site):
    client, factory = site
    ids = add(factory, "Gone#1")
    client.post('/login', data={"display_name": "Gone#1"})
    with factory() as db: db.delete(db.get(Player, ids[0])); db.commit()
    assert client.get('/').status_code == 200
    client.cookies['session'] = 'not-a-valid-signature'
    assert client.get('/').status_code == 200


@pytest.mark.parametrize("query,expected", [("  alice#na1 ", "Alice#NA1"), ("ALICE", None),
    ("Alice#NA2", "Alice#NA2"), ("Legacy#Habit", "Legacy"), ("#", None), ("  ", None),
    ("%", None), ("A_", None), ("雪 & Space#1", "雪 & Space#1")])
def test_search_unique_full_ids_demo_fallback_and_literal_input(site, query, expected):
    client, factory = site
    add(factory, "Alice#NA1", "Alice#NA2", "Legacy", "雪 & Space#1")
    response = client.get('/search?' + urlencode({"q": query}))
    if expected:
        assert response.status_code == 303
        assert response.headers['location'] == '/players/' + quote(expected, safe='')
        assert client.get(response.headers['location']).status_code == 200
    else:
        assert response.status_code == 200
        if query.strip(): assert 'class="not-found"' in response.text


def test_ambiguous_case_insensitive_exact_name_never_falls_through_to_another_account(site):
    client, factory = site
    add(factory, 'Same', 'same', 'Same#TEST')
    response = client.get('/search?q=SAME')
    assert response.status_code == 200 and 'class="not-found"' in response.text


@pytest.mark.parametrize('name', ['100%#TEST','A_B#TEST'])
def test_wildcard_characters_are_literal_when_a_matching_account_exists(site, name):
    client, factory = site
    add(factory, name, 'Unrelated#TEST')
    response = client.get('/search?'+urlencode({'q':name.split('#')[0]}))
    assert response.status_code == 303 and response.headers['location'] == '/players/'+quote(name,safe='')


@pytest.mark.parametrize("target,location", [("/players/Legacy?filter=role:duelist", "/players/Legacy?filter=role:duelist"),
    ("https://elsewhere.invalid", "/friends"), ("//elsewhere.invalid", "/friends"), ("", "/friends")])
def test_friend_redirect_is_local_after_real_form_post(site, target, location):
    client, factory = site
    add(factory, "Me", "Other")
    client.post('/login', data={"display_name": "Me"})
    response = client.post('/friends/add', data={"display_name": "Other", "next": target})
    assert response.status_code == 303 and response.headers['location'] == location


@pytest.mark.parametrize("path", ['/riot.txt', '//riot.txt'])
@pytest.mark.parametrize("demo", [False, True])
def test_riot_verification_http_is_independent_of_demo(site, monkeypatch, path, demo):
    client, _ = site
    monkeypatch.setattr(settings, 'demo_mode', demo)
    monkeypatch.setattr(settings, 'enable_riot_txt', True)
    response = client.get(path)
    assert response.status_code == 200 and response.content == b'f212a992-ace0-402a-838d-cad406c48fe2'
    assert response.headers['content-type'].startswith('text/plain')
    monkeypatch.setattr(settings, 'enable_riot_txt', False)
    assert client.get(path).status_code == 404


def test_https_cookie_setting_is_enforced_by_session_middleware():
    from fastapi import FastAPI, Request
    from starlette.middleware.sessions import SessionMiddleware
    from tests.support.http import Client
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key='test-only', https_only=True)
    @app.get('/')
    def set_session(request: Request): request.session['player_id'] = 1; return {}
    cookie = Client(app, scheme='https').get('/').headers['set-cookie'].lower()
    assert '; secure' in cookie and '; httponly' in cookie and 'samesite=lax' in cookie
