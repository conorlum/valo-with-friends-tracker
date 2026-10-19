"""The wide layout moves cards into rows; it must not add or drop a section.

`collect_h2` reads a page template and every literal `{% include %}` under it and
returns the multiset of its `<h2>` texts. The expected multisets below were taken
from the templates before the layout change (order is not compared). Jinja in a
heading is kept as written, whitespace-normalised.
"""
import re
from collections import Counter
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"
TEMPLATES = APP / "templates"
CSS = APP / "static" / "css" / "style.css"

H2 = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.S | re.I)
INCLUDE = re.compile(r"""\{%-?\s*include\s+(?:"([^"]+)"|'([^']+)')""")


def _norm(s: str) -> str:
    s = re.sub(r"\{#.*?#\}", "", s, flags=re.S)
    s = re.sub(r"<[^>]*>", "", s)
    return " ".join(s.split())


def collect_h2(name: str) -> Counter:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    found = Counter(_norm(m) for m in H2.findall(text))
    for m in INCLUDE.finditer(text):
        found += collect_h2(m.group(1) or m.group(2))
    return found


ECON = '{{ econ_tier_heading | default("Economy win rate by buy tier") }}'

EXPECTED = {
    "matches/detail.html": {
        "Economy win rate by buy tier": 1,
        "Match Data Highlights": 1,
        "Round win rate by game state": 1,
        "Rounds": 1,
        "Scoreboard": 1,
        "Shoutouts": 1,
    },
    "players/detail.html": {
        "Fight-EV: converting duels into round-win leverage": 1,
        "Kill impact vs. death impact by match": 1,
        "Kill order graph": 1,
        "Map streaks": 1,
        "Maps": 1,
        "Match Data Highlights": 1,
        "Round win rate by game state": 1,
        ECON: 1,
        "{{ form_title }}": 1,
        "{{ pistol_card_title }}": 1,
    },
    "sessions/detail.html": {
        "Fun Stats": 1,
        "Impact Leaderboard": 1,
        "KDA by match": 1,
        "Matches": 1,
        "Roster": 1,
        "Round win rate by game state": 1,
        "Shoutouts": 1,
        ECON: 1,
    },
    "squad/detail.html": {
        "Shoutouts": 1,
        "Your friends, ranked": 1,
        "{{ form_title }}": 1,
    },
    "stats/detail.html": {
        "Attack vs. defense win rate by map": 2,
        "Buying after a pistol win": 2,
        "Force buy vs. full save": 2,
        "Forcing after losing a pistol": 2,
        "Map control by map": 2,
        "Match win rate by halftime score": 2,
        "Match win rate by pistol/round outcome combo": 2,
        "Match win rate by rounds reached": 2,
        "Round-win momentum": 2,
        "{{ pistol_card_title }}": 2,
    },
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_page_sections_unchanged(name):
    assert collect_h2(name) == Counter(EXPECTED[name])


def test_match_page_has_no_cumulative_impact_card():
    assert "Cumulative Impact" not in collect_h2("matches/detail.html")


# ---- the shared CSS ----

def _rules(css: str):
    """(selector, body) for each flat rule; media blocks are skipped here."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(s.strip(), b) for s, b in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def test_wide_page_css():
    css = CSS.read_text(encoding="utf-8")
    rules = _rules(css)
    by_sel = {s: b for s, b in rules}

    for sel in (
        "main:has(.page-wide)",
        ".page-row",
        ".page-row--60-40",
        ".page-row--halves",
        ".page-row > :only-child",
        ".page-stack",
    ):
        assert sel in by_sel, sel
    assert "grid-column: 1 / -1" in by_sel[".page-row > :only-child"]
    assert ".page-row > *, .page-stack > *" in by_sel

    # every grid-template-columns in a .page-row rule uses minmax(0, and no bare 1fr
    checked = 0
    for sel, body in rules:
        if ".page-row" not in sel:
            continue
        for value in re.findall(r"grid-template-columns\s*:\s*([^;]+);", body):
            checked += 1
            assert "minmax(0," in value.replace(" ", "").replace("minmax(0,", "minmax(0,"), (sel, value)
            assert not re.search(r"(?<!minmax\(0,)(?<![\w.])\d*\.?\d*fr\b", re.sub(r"minmax\([^)]*\)", "", value)), (sel, value)
    assert checked >= 3

    # the 1300px media rule: after the modifier rules, names them, sets one column
    media = re.search(r"@media\s*\(max-width:\s*1300px\)\s*\{(.*?\})\s*\}", css, re.S)
    assert media, "no 1300px media rule"
    inner = media.group(1)
    assert ".page-row, .page-row--60-40, .page-row--halves" in inner
    assert re.search(r"grid-template-columns:\s*minmax\(0,\s*1fr\)\s*;", inner)
    for sel in (".page-row--60-40 {", ".page-row--halves {", ".page-row > :only-child {"):
        assert css.index(sel) < media.start(), sel

    # round detail: two minmax(0, ...) cells, one column in the same media rule, defined before it
    assert ".round-detail-cols" in by_sel
    assert "minmax(0," in by_sel[".round-detail-cols"].replace(" ", "").replace("minmax(0,", "minmax(0,")
    assert not re.search(r"(?<![\w.])\d*\.?\d*fr\b", re.sub(r"minmax\([^)]*\)", "", by_sel[".round-detail-cols"]))
    assert ".round-detail-cols" in inner
    assert css.index(".round-detail-cols {") < media.start()
