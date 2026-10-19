"""Account groups at the route level: demo gating, groups of one, who may
change a group, the combined fragments and the chips.

Routes are called directly with a hand-built Request (no TestClient: httpx
stays out, see tests/test_site_modes.py). Templates normally look the
logged-in player up on their own SessionLocal; here that lookup (and the
player page's own-session map-streaks worker) is pointed at the test DB.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.config import settings
from app.models import AccountGroup, AccountGroupMember, AccountGroupViewCache, ImpactScore, Round
from app.routers import accounts as accounts_router
from app.routers import players as players_router
from app.services.account_groups import link_account, set_account_label
from app.services.auth import SESSION_KEY, get_current_player
from app.services.player_view_cache import invalidate_all_player_caches
from app.templates import templates
from tests.test_account_groups import _play, _players, db  # noqa: F401  (db is a fixture)


def _request(player=None, path="/", query=b""):
    session = {SESSION_KEY: player.id} if player is not None else {}
    return Request({
        "type": "http", "method": "GET", "path": path, "query_string": query, "headers": [],
        "scheme": "http", "server": ("testserver", 80), "session": session,
    })


@pytest.fixture
def site(db, monkeypatch):
    """Templates and the player page's worker session read the test DB."""
    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(players_router, "SessionLocal", factory)

    def _current(request):
        return {"current_player": get_current_player(request, db)}

    monkeypatch.setattr(templates, "context_processors", [_current])
    return db


def _demo(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setitem(templates.env.globals, "demo_mode", True)


def _group(db, labels=None):
    """main#1 (main) + alt#2 + sub#3, each with one match. Returns the players."""
    main, alt, sub = _players(db, "main#1", "alt#2", "sub#3")
    for day, player in enumerate((main, alt, sub), start=1):
        mp = _play(db, player, day, f"ext-{player.display_name[:3]}")
        round_row = Round(match_id=mp.match_id, round_number=1)
        db.add(round_row)
        db.flush()
        db.add(ImpactScore(match_player_id=mp.id, round_id=round_row.id, kill_impact=5, death_impact=0, impact=5))
    assert link_account(db, main.id, alt.id) is None
    assert link_account(db, main.id, sub.id) is None
    db.commit()
    return main, alt, sub


def _body(response) -> str:
    return response.body.decode()


def _member_rows(db):
    db.expire_all()
    return sorted((m.player_id, m.group_id, m.label) for m in db.query(AccountGroupMember).all())


def _main_id(db):
    db.expire_all()
    return db.query(AccountGroup).one().main_player_id


# --- demo ----------------------------------------------------------------------


def test_demo_refuses_the_accounts_page(site, monkeypatch):
    main, _, _ = _group(site)
    _demo(monkeypatch)
    with pytest.raises(HTTPException) as err:
        accounts_router.accounts_page(_request(main, "/accounts"), db=site)
    assert err.value.status_code == 403


def test_demo_player_page_has_no_chips_and_ignores_accounts_all(site, monkeypatch):
    main, _, _ = _group(site)
    normal = players_router.player_detail(
        _request(main), "main#1", accounts="all", db=site
    )
    assert "All accounts" in _body(normal)      # the group is real, so the demo case below bites

    _demo(monkeypatch)
    for accounts in (None, "all"):
        page = players_router.player_detail(_request(main), "main#1", accounts=accounts, db=site)
        html = _body(page)
        assert "All accounts" not in html
        assert "account-toggle" not in html
        assert "accounts=all" not in html
        assert page.context["accounts_combined"] is False


def test_demo_account_view_has_no_members_or_group(site, monkeypatch):
    main, _, _ = _group(site)
    _demo(monkeypatch)
    view = players_router._account_view(site, main, "all")
    assert view.members == [] and view.group is None and view.player_ids is None


# --- groups of one --------------------------------------------------------------


def _group_of_one(db):
    main, alt, _ = _players(db, "main#1", "alt#2", "x#3")
    _play(db, main, 1, "ext-main")
    link_account(db, main.id, alt.id)
    db.commit()
    db.query(AccountGroupMember).filter_by(player_id=alt.id).delete()   # as a deleted sub's CASCADE would
    db.commit()
    assert db.query(AccountGroup).count() == 1
    return main


def test_accounts_page_treats_a_group_of_one_as_no_group(site):
    main = _group_of_one(site)
    page = accounts_router.accounts_page(_request(main, "/accounts"), db=site)
    assert page.context["members"] == []
    html = _body(page)
    assert "See all" not in html
    assert "Only main#1 so far" in html


def test_player_page_shows_no_chips_for_a_group_of_one(site):
    main = _group_of_one(site)
    for accounts in (None, "all"):
        page = players_router.player_detail(_request(main), "main#1", accounts=accounts, db=site)
        html = _body(page)
        assert "All accounts" not in html and "account-toggle" not in html
        assert page.context["accounts_combined"] is False


# --- who may change a group -----------------------------------------------------


def test_a_sub_cant_label_make_main_or_remove_another_member(site):
    main, alt, sub = _group(site)
    before = _member_rows(site)
    assert _main_id(site) == main.id

    request = _request(alt, "/accounts")
    accounts_router.label_account_route(request, display_name="sub#3", label="hijack", db=site)
    accounts_router.label_account_route(request, display_name="main#1", label="hijack", db=site)
    accounts_router.make_main_route(request, display_name="sub#3", db=site)
    accounts_router.make_main_route(request, display_name="alt#2", db=site)
    accounts_router.remove_account_route(request, display_name="sub#3", db=site)
    accounts_router.remove_account_route(request, display_name="main#1", db=site)

    assert _member_rows(site) == before
    assert _main_id(site) == main.id


def test_the_main_can_label_and_hand_over(site):
    main, alt, sub = _group(site)
    request = _request(main, "/accounts")
    accounts_router.label_account_route(request, display_name="alt#2", label="Duelist", db=site)
    assert dict((pid, label) for pid, _, label in _member_rows(site))[alt.id] == "Duelist"
    accounts_router.make_main_route(request, display_name="sub#3", db=site)
    assert _main_id(site) == sub.id


def test_a_sub_can_leave_its_own_group_entry(site):
    main, alt, sub = _group(site)
    accounts_router.remove_account_route(_request(alt, "/accounts"), display_name="alt#2", db=site)
    assert [pid for pid, _, _ in _member_rows(site)] == [main.id, sub.id]


# --- combined fragments ---------------------------------------------------------


class _Spy:
    """Stands in for `templates` and records each response's context."""

    def __init__(self):
        self.contexts = []

    def TemplateResponse(self, request, name, context):
        self.contexts.append(context)
        return templates.TemplateResponse(request, name, context)


def test_career_with_accounts_all_covers_every_accounts_matches(site, monkeypatch):
    main, _, _ = _group(site)
    spy = _Spy()
    monkeypatch.setattr(players_router, "templates", spy)

    players_router.player_career_fragment(_request(main), "main#1", accounts="all", db=site)
    combined = {m.match.external_id for m in spy.contexts[-1]["profile"].matches}
    assert combined == {"ext-mai", "ext-alt", "ext-sub"}
    assert spy.contexts[-1]["accounts_combined"] is True

    players_router.player_career_fragment(_request(main), "main#1", accounts=None, db=site)
    assert {m.match.external_id for m in spy.contexts[-1]["profile"].matches} == {"ext-mai"}


def test_recent_matches_with_accounts_all_lists_every_accounts_matches(site):
    main, _, _ = _group(site)
    combined = _body(players_router.player_recent_matches_fragment(_request(main), "main#1", accounts="all", db=site))
    for external_id in ("ext-mai", "ext-alt", "ext-sub"):
        assert f"/matches/{external_id}" in combined
    alone = _body(players_router.player_recent_matches_fragment(_request(main), "main#1", accounts=None, db=site))
    assert "/matches/ext-mai" in alone and "/matches/ext-alt" not in alone


# --- chips ----------------------------------------------------------------------


def test_chips_list_the_main_first_and_escape_labels(site):
    main, alt, _ = _group(site)
    set_account_label(site, main.id, alt.id, "<b>x</b>")
    site.commit()

    html = _body(players_router.player_detail(_request(main), "alt#2", accounts=None, db=site))
    chips = html[html.index("account-toggle"):]
    assert chips.index('title="main#1') < chips.index('title="alt#2') < chips.index('title="sub#3')
    assert 'account-main-tag">main<' in chips
    assert chips.count("account-main-tag") == 1
    assert "&lt;b&gt;x&lt;/b&gt;" in chips
    assert "<b>x</b>" not in html


# --- cache ----------------------------------------------------------------------


def test_invalidate_all_player_caches_clears_group_rows(site):
    main, _, _ = _group(site)
    group_id = site.query(AccountGroup).one().id
    site.add(AccountGroupViewCache(group_id=group_id, scope="recent", fingerprint="fp", version=1, data={}))
    site.commit()
    assert site.query(AccountGroupViewCache).count() == 1

    invalidate_all_player_caches(site)
    site.commit()
    site.expire_all()
    assert site.query(AccountGroupViewCache).count() == 0


# --- a lost race on the add route -----------------------------------------------

LOST_RACE = "That account was just linked to another group."


def _lose_a_race(monkeypatch, error):
    """link_account writes a duplicate member row (a real IntegrityError on
    flush) or raises `error` -- either way the session is left mid-write."""
    def racing(db, owner_id, other_id):
        if error is not None:
            raise error
        db.add(AccountGroupMember(player_id=owner_id, group_id=1))
        db.flush()

    monkeypatch.setattr(accounts_router, "link_account", racing)


def test_add_route_lost_race_on_a_unique_violation_shows_the_message(site, monkeypatch):
    main, alt, sub = _group(site)
    _lose_a_race(monkeypatch, None)
    response = accounts_router.add_account_route(_request(main, "/accounts/add"), display_name="alt#2", db=site)
    assert response.status_code == 200
    assert LOST_RACE in _body(response)
    assert _member_rows(site) == sorted((p.id, 1, None) for p in (main, alt, sub))


def test_add_route_lost_race_on_a_deadlock_shows_the_message(site, monkeypatch):
    from sqlalchemy.exc import OperationalError

    class Deadlock(Exception):
        pgcode = "40P01"

    main, _, _ = _group(site)
    _lose_a_race(monkeypatch, OperationalError("INSERT", {}, Deadlock()))
    response = accounts_router.add_account_route(_request(main, "/accounts/add"), display_name="alt#2", db=site)
    assert response.status_code == 200
    assert LOST_RACE in _body(response)


def test_remove_route_locks_the_group_before_clearing_caches(site, monkeypatch):
    # A link locks the group, then clears caches; an unlink must take them in
    # the same order or the two can deadlock over one group.
    main, _, _ = _group(site)
    order = []
    real_lock = accounts_router.lock_group
    monkeypatch.setattr(accounts_router, "lock_group", lambda db, pid: order.append("lock") or real_lock(db, pid))
    monkeypatch.setattr(accounts_router, "invalidate_player_cache", lambda db, ids: order.append("caches"))
    response = accounts_router.remove_account_route(_request(main, "/accounts/remove"), display_name="sub#3", db=site)
    assert response.status_code == 303
    assert order == ["lock", "caches"]


def test_remove_route_deadlock_shows_a_message_and_changes_nothing(site, monkeypatch):
    from sqlalchemy.exc import OperationalError

    class Deadlock(Exception):
        pgcode = "40P01"

    main, alt, sub = _group(site)

    def deadlocked(db, actor_id, other_id):
        db.query(AccountGroupMember).filter_by(player_id=other_id).delete()
        raise OperationalError("DELETE", {}, Deadlock())

    monkeypatch.setattr(accounts_router, "unlink_account", deadlocked)
    response = accounts_router.remove_account_route(_request(main, "/accounts/remove"), display_name="sub#3", db=site)
    assert response.status_code == 200
    assert accounts_router.CHANGED_AT_ONCE_MESSAGE in _body(response)
    assert _member_rows(site) == sorted((p.id, 1, None) for p in (main, alt, sub))


def test_add_route_other_operational_errors_are_not_swallowed(site, monkeypatch):
    from sqlalchemy.exc import OperationalError

    class Other(Exception):
        pgcode = "57014"

    main, _, _ = _group(site)
    _lose_a_race(monkeypatch, OperationalError("INSERT", {}, Other()))
    with pytest.raises(OperationalError):
        accounts_router.add_account_route(_request(main, "/accounts/add"), display_name="alt#2", db=site)
