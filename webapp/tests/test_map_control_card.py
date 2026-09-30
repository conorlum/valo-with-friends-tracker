"""The /stats map control card (docs/replay-map-control-plan.md, Stage 7 and R3.4): on the friends site it
loads the per-map table; on the demo, where the replay viewer is off, it isn't there. `demo_mode` is a
Jinja global copied at import (app/templates.py), so the tests set it there."""

import pytest

from app.templates import templates


def render_card(group="all") -> str:
    return templates.env.get_template("_map_control_card.html").render(group=group)


@pytest.mark.parametrize("demo_mode", [False, True])
def test_the_card_shows_only_off_the_demo_site(monkeypatch, demo_mode):
    monkeypatch.setitem(templates.env.globals, "demo_mode", demo_mode)
    html = render_card()
    assert ("Map control by map" in html) is not demo_mode
    assert ('hx-get="/stats/map-control?group=all"' in html) is not demo_mode


def test_the_card_asks_for_the_friends_group_off_the_all_players_page(monkeypatch):
    monkeypatch.setitem(templates.env.globals, "demo_mode", False)
    assert 'hx-get="/stats/map-control?group=friends"' in render_card("friends")
    assert 'hx-get="/stats/map-control?group=friends"' in render_card(None)    # the career fragment


def test_the_stats_sections_include_it_after_the_map_side_card():
    source = templates.env.loader.get_source(templates.env, "stats/_stats_sections.html")[0]
    assert source.index('{% include "_map_side_card.html" %}') < source.index('{% include "_map_control_card.html" %}')
