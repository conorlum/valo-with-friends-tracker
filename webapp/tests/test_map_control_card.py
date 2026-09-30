"""The /stats map control placeholder (docs/replay-map-control-plan.md, Stage 7): shown on the friends site,
hidden on the demo, where the replay viewer is off. `demo_mode` is a Jinja global copied at import
(app/templates.py), so the tests set it there."""

import pytest

from app.templates import templates


def render_sections() -> str:
    # The card needs no stats context; the other cards render nothing without theirs.
    return templates.env.get_template("_map_control_card.html").render()


@pytest.mark.parametrize("demo_mode", [False, True])
def test_the_card_shows_only_off_the_demo_site(monkeypatch, demo_mode):
    monkeypatch.setitem(templates.env.globals, "demo_mode", demo_mode)
    html = render_sections()
    assert ("Map control by map" in html) is not demo_mode
    assert ("Coming soon" in html) is not demo_mode


def test_the_stats_sections_include_it_after_the_map_side_card():
    source = templates.env.loader.get_source(templates.env, "stats/_stats_sections.html")[0]
    assert source.index('{% include "_map_side_card.html" %}') < source.index('{% include "_map_control_card.html" %}')
