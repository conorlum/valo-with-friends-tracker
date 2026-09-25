"""One codebase, two sites: the friends tracker and the public ValoMaths demo.

The routes are called directly rather than through starlette's TestClient,
which needs httpx (see test_maintenance_mode.py for why that stays out).
"""

import pytest
from fastapi import HTTPException

from app.config import settings
from app.main import riot_verification
from app.routers import friends
from app.templates import templates


@pytest.mark.parametrize("route", [friends.add_friend_route, friends.remove_friend_route])
def test_demo_mode_blocks_friend_edits_before_touching_the_db(monkeypatch, route):
    monkeypatch.setattr(settings, "demo_mode", True)
    with pytest.raises(HTTPException) as raised:
        route(request=None, display_name="Anyone#NA1", db=None)
    assert raised.value.status_code == 403


def test_friend_edits_are_allowed_outside_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    friends._reject_in_demo_mode()


@pytest.mark.parametrize("demo_mode", [True, False])
@pytest.mark.parametrize("site_name", ["ValoMaths", "ValoWithFriendsTracker"])
def test_the_site_name_renders_from_settings(monkeypatch, site_name, demo_mode):
    monkeypatch.setitem(templates.env.globals, "site_name", site_name)
    monkeypatch.setitem(templates.env.globals, "demo_mode", demo_mode)
    html = templates.env.get_template("landing.html").render(current_player=None)
    assert f"<title>{site_name}</title>" in html
    assert f"<h1>{site_name}</h1>" in html
    assert ("Demo &ndash; sample data" in html) is demo_mode


def test_template_globals_come_from_settings():
    assert templates.env.globals["site_name"] == settings.site_name
    assert templates.env.globals["demo_mode"] == settings.demo_mode


@pytest.mark.parametrize("demo_mode", [True, False])
def test_riot_txt_follows_its_own_setting_not_demo_mode(monkeypatch, demo_mode):
    monkeypatch.setattr(settings, "demo_mode", demo_mode)

    monkeypatch.setattr(settings, "enable_riot_txt", True)
    assert riot_verification().body == b"f212a992-ace0-402a-838d-cad406c48fe2"

    monkeypatch.setattr(settings, "enable_riot_txt", False)
    with pytest.raises(HTTPException) as raised:
        riot_verification()
    assert raised.value.status_code == 404


def test_stats_page_never_reads_the_crawl_roster():
    """tracked_players.json only picks which matches get crawled; the Stats
    page's Friends tab is the logged-in viewer's own friendships, on both
    sites (the demo's seeded friendships make it work there unchanged)."""
    from app.services import site_stats

    assert not hasattr(site_stats, "resolve_roster_player_ids")
    assert not hasattr(site_stats, "ROSTER_PATH")
