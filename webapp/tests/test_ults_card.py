"""The /stats Ults card: the include, the fragment route and its table. `demo_mode` is a Jinja global copied at
import (app/templates.py), so the tests set it there. The route is called directly with a fake `page_values`."""

import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import settings
from app.templates import templates
from tests.critutil import test_ult_values as tuv

NAMES = {100: "Alpha", 101: "Bravo", 200: "Charlie", 201: "Delta"}


@pytest.fixture(scope="module")
def block():
    from app.critutil import ult_values

    stored, ctx = tuv.ult_fixture()
    return ult_values.build(tuv._prepared(stored, ctx), ctx, tuv.MODEL, 4200)


def _row(agent, n_uses=9, value=0.031, lo=0.01, hi=0.05, wasted=0):
    return {"agent": agent, "n_uses": n_uses, "n_charges": n_uses, "n_uncompared": 0, "wasted": wasted,
            "value": value, "lo": lo, "hi": hi, "diff": None}


def _held():
    nulls = {"pp": None, "lo": None, "hi": None}
    return {"rounds": 0, "matches": 0, "any": dict(nulls), "used": dict(nulls), "unused": dict(nulls)}


def _block(rows, cast_mean=0.02, orbs=None):
    return {"ranking": {"rows": rows, "cast_mean": cast_mean}, "charges": [],
            "orbs": orbs or {"max_cost": 0, "rows": []}, "held": _held(),
            "risk": {"overall": {"tries": 0, "deaths": 0, "cost_pp": None, "lo": None, "hi": None}, "maps": []}}


def render_table(**ctx):
    ctx.setdefault("ults", None)
    ctx.setdefault("current", True)
    ctx.setdefault("friend_rows", [])
    ctx.setdefault("label", "every uploaded replay")
    return templates.env.get_template("stats/_ults_table.html").render(**ctx)


def _fragment(monkeypatch, block, current=True, group="all", viewer=True, circle=(101, 200)):
    """Calls the route with a fake page_values; returns (rendered html, template context)."""
    from app.routers import site_stats
    from app.services import circle as circle_service
    from app.services import replay_util

    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(replay_util, "page_values", lambda db: ({"ults": block} if block is not None else None, current))
    monkeypatch.setattr(site_stats, "get_current_player",
                        lambda request, db: SimpleNamespace(id=100) if viewer else None)
    monkeypatch.setattr(circle_service, "circle_ids", lambda db, pid: set(circle) - {100})
    monkeypatch.setattr(site_stats, "_player_names", lambda db, ids: {i: NAMES[i] for i in ids if i in NAMES})
    seen = {}
    monkeypatch.setattr(site_stats.templates, "TemplateResponse", lambda req, name, ctx, **kw: seen.update(ctx, name=name))
    site_stats.ults_fragment(SimpleNamespace(), group, object())
    return templates.env.get_template(seen["name"]).render(**seen), seen


# --- the card

def render_card(group="all") -> str:
    return templates.env.get_template("_ults_card.html").render(group=group)


@pytest.mark.parametrize("demo_mode", [False, True])
def test_the_card_shows_only_off_the_demo_site(monkeypatch, demo_mode):
    monkeypatch.setitem(templates.env.globals, "demo_mode", demo_mode)
    html = render_card()
    assert ("Ults" in html) is not demo_mode
    assert ('hx-get="/stats/ults?group=all"' in html) is not demo_mode


def test_the_card_asks_for_the_friends_group_off_the_all_players_page(monkeypatch):
    monkeypatch.setitem(templates.env.globals, "demo_mode", False)
    assert 'hx-get="/stats/ults?group=friends"' in render_card("friends")
    assert 'hx-get="/stats/ults?group=friends"' in render_card(None)


def test_the_stats_sections_include_it_after_the_map_control_card():
    source = templates.env.loader.get_source(templates.env, "stats/_stats_sections.html")[0]
    assert source.index('{% include "_map_control_card.html" %}') < source.index('{% include "_ults_card.html" %}')


# --- the route

def test_the_route_404s_in_demo_mode(monkeypatch):
    from app.routers import site_stats

    monkeypatch.setattr(settings, "demo_mode", True)
    with pytest.raises(HTTPException) as e:
        site_stats.ults_fragment(SimpleNamespace(), "all", object())
    assert e.value.status_code == 404


def test_all_players_shows_agents_and_no_player_name(monkeypatch, block):
    html, _ = _fragment(monkeypatch, block, group="all")
    assert "Jett" in html and "Sage" in html
    assert not any(n in html for n in NAMES.values())


def test_all_players_ignores_the_friends_group_when_logged_out(monkeypatch, block):
    html, _ = _fragment(monkeypatch, block, group="friends", viewer=False)
    assert "Jett" in html and not any(n in html for n in NAMES.values())


def test_friends_shows_the_circles_names_and_no_one_elses(monkeypatch, block):
    html, _ = _fragment(monkeypatch, block, group="friends", circle=(101, 200))
    assert "Bravo" in html and "Charlie" in html          # 101 plays Jett, 200 plays Sage
    assert "Alpha" not in html and "Delta" not in html    # 100 is the viewer without a counted ult, 201 is outside


def test_friends_rows_sit_beneath_their_agent(monkeypatch, block):
    html, _ = _fragment(monkeypatch, block, group="friends", circle=(101,))
    assert html.index("Jett") < html.index("Bravo") < html.index("Sage")


@pytest.mark.parametrize("group,viewer", [("all", True), ("friends", True), ("friends", False)])
def test_the_ranking_caption_says_what_the_agent_rows_are(monkeypatch, block, group, viewer):
    html, _ = _fragment(monkeypatch, block, group=group, viewer=viewer, circle=(101,))
    caption = " ".join(html[html.index("Ult value by agent"):html.index("<svg")].split())
    assert "every uploaded replay" in caption and "viewer's circle" not in caption
    assert "no ult of that side up" in caption and "of that kind" not in caption
    assert "wasted" in caption and "counts as 0" in caption
    assert ("you and your friends" in " ".join(html.split())) is (group == "friends" and viewer)


def test_a_missing_build_says_it_is_being_built(monkeypatch):
    html, _ = _fragment(monkeypatch, None, current=False)
    assert "being built" in html


@pytest.mark.parametrize("data", [{}, {"rows": []}])
def test_a_build_without_ults_renders_the_building_note(data):
    assert "being built" in render_table(ults=data or None)
    assert "being built" in render_table(ults=None)


def test_a_stale_build_shows_with_the_updating_note(monkeypatch, block):
    html, _ = _fragment(monkeypatch, block, current=False)
    assert "Jett" in html and "updating" in html.lower()
    fresh, _ = _fragment(monkeypatch, block, current=True)
    assert "updating" not in fresh.lower()


# --- the table

def test_a_value_prints_in_percentage_points_with_an_explicit_sign():
    html = render_table(ults=_block([_row("Jett", value=0.031, lo=-0.01, hi=0.052), _row("Omen", value=-0.004, lo=-0.02, hi=0.01)]))
    assert "+3.1 pp" in html
    assert re.search(r"[-−]0\.4 pp", html)
    assert "+5.2 pp" in html and re.search(r"[-−]1\.0 pp", html)


def test_an_agent_under_five_uses_has_dashes_and_its_count_and_no_orb_row(block):
    html = render_table(ults=block)
    ranking = html.split('id="ults-orbs"')[0]
    sage = next(line for line in ranking.splitlines() if "Sage" in line)
    assert "—" in sage and "pp" not in sage
    orbs = html.split('id="ults-orbs"')[1].split('id="ults-held"')[0]
    assert "Sage" not in orbs and "Jett" in orbs


def test_a_zero_use_agent_has_a_row_with_zero_uses_and_a_dash():
    html = render_table(ults=_block([_row("Jett"), _row("Cypher", n_uses=0, value=None, lo=None, hi=None)]))
    line = next(line for line in html.split('id="ults-orbs"')[0].splitlines() if "Cypher" in line)
    assert "—" in line and re.search(r"\b0\b", line)


def test_a_muted_orb_row_is_marked_in_text():
    orbs = {"max_cost": 3, "rows": [
        {"agent": "Jett", "cost": 3, "muted": True, "cells": [0.01, 0.02, 0.03]},
        {"agent": "Omen", "cost": 3, "muted": False, "cells": [0.01, 0.02, 0.03]}]}
    html = render_table(ults=_block([_row("Jett"), _row("Omen")], orbs=orbs))
    section = html.split('id="ults-orbs"')[1].split('id="ults-held"')[0]
    jett = next(line for line in section.splitlines() if "Jett" in line)
    omen = next(line for line in section.splitlines() if "Omen" in line)
    assert "CI spans 0" in jett and "CI spans 0" not in omen


def test_the_orb_help_line_and_held_note_are_there(block):
    html = render_table(ults=block)
    assert "the higher cell takes the orb" in html
    assert "not cause and effect" in html
    assert "points held" in html


def test_a_cast_mean_draws_a_reference_line_and_none_draws_none():
    with_line = render_table(ults=_block([_row("Jett")], cast_mean=0.02))
    without = render_table(ults=_block([_row("Jett")], cast_mean=None))
    assert "ults-cast-line" in with_line
    assert "ults-cast-line" not in without
    assert "Jett" in without


def test_the_ranking_is_an_inline_svg_with_a_whisker_per_valued_row(block):
    html = render_table(ults=block)
    assert "<svg" in html and "ults-whisker" in html and "ults-dot" in html


def test_the_table_scrolls_inside_its_card():
    assert "table-scroll" in render_table(ults=_block([_row("Jett")]))
