"""The wide layout of /squad and /stats (S11): card pairs side by side in rows. Rendered with
StrictUndefined; the stats data is a permissive stand-in so every card renders."""
from html.parser import HTMLParser
from types import SimpleNamespace

import jinja2
import pytest

from app.templates import templates

VOID = {"br", "img", "input", "meta", "link", "hr", "col", "area", "base", "source", "wbr"}


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.parent, self.children, self.text = tag, parent, [], ""
        self.attrs = dict(attrs)

    @property
    def classes(self):
        return (self.attrs.get("class") or "").split()

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def h2(self):
        for n in self.walk():
            if n.tag == "h2":
                return n.text.strip()
        return None


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = Node("root", [], None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.text += data


def _parse(html):
    p = _Tree()
    p.feed(html)
    return p.root


class Any(float):
    """Every attribute is another Any, it iterates over two, prints as a number."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Any(1)

    def __iter__(self):
        return iter([Any(1), Any(2)])

    def __len__(self):
        return 2


def _shoutouts(n):
    return [SimpleNamespace(agent="Jett", display_name=f"p{i}#NA1", headline=f"h{i}", detail=f"d{i}")
            for i in range(n)]


def _squad_ctx(shoutouts=8):
    pair = Any(1)
    pair.display_name = "Aa#NA1"
    overview = SimpleNamespace(squad_size=3, total_matches_together=4, total_rounds_together=50,
                               form=Any(1), shoutouts=_shoutouts(shoutouts), pairs=[pair])
    return {"request": None, "site_name": "X", "overview": overview, "scope": "recent",
            "recent_rows": None, "recent_rows_url": "/x", "current_player": None}


def _stats_ctx(pistol=True):
    ctx = {"request": None, "site_name": "X", "subject_label": "you", "group": "all", "scope": "recent",
           "current_player": None, "friends_login_note": "n", "eco_followup_min_samples": 5,
           "enemy_at_11_response_min_samples": 5}
    for k in ("pistol_match_stats", "eco_followup_stats", "first_half_combo_stats", "full_combo_stats",
              "map_side_stats", "halftime_conversion_stats", "score_reached_stats", "round_streak_stats",
              "force_buy_stats", "enemy_at_11_response_stats"):
        ctx[k] = Any(1)
    if not pistol:
        ctx["pistol_match_stats"] = SimpleNamespace(lost_both_total=0, won_one_total=0, won_both_total=0)
    return ctx


def _render(name, ctx, demo_mode):
    env = templates.env.overlay(undefined=jinja2.StrictUndefined)
    ctx = dict(ctx, demo_mode=demo_mode)
    return _parse(env.get_template(name).render(**ctx))


DEMO = pytest.mark.parametrize("demo_mode", [False, True], ids=["site", "demo"])


def _rows(root):
    return [n for n in root.walk() if "page-row" in n.classes]


def _ancestor_card(n):
    while "card" not in n.classes:
        n = n.parent
    return n


def _row_checks(root):
    for n in root.walk():
        if "page-row" in n.classes or "page-stack" in n.classes:
            assert all(c.tag != "script" for c in n.walk())
            assert all("card" in c.classes or "page-stack" in c.classes for c in n.children)
    for row in _rows(root):
        for t in (n for n in row.walk() if n.tag == "table"):
            p = t.parent
            while p is not row and "table-scroll" not in p.classes and "chart-scroll" not in p.classes:
                p = p.parent
            assert p is not row


def _row_of(root, h2):
    return [r for r in _rows(root) if h2 in [c.h2() for c in r.children]]


def _top_card(root, h2):
    return [n for n in root.walk() if "card" in n.classes and n.h2() == h2]


PAIRS = [
    ["Pistols won into match wins", "Buying after a pistol win"],
    ["Forcing after losing a pistol", "Force buy vs. full save"],
    ["Round-win momentum", "Match win rate by halftime score"],
    ["Match win rate by rounds reached", "Attack vs. defense win rate by map"],
]
COMBO = "Match win rate by pistol/round outcome combo"
CONTROL = "Map control by map"


@DEMO
def test_stats_pairs_in_rows(demo_mode):
    root = _render("stats/_stats_sections.html", _stats_ctx(), demo_mode)
    rows = _rows(root)
    assert all("page-row--halves" in r.classes for r in rows)
    assert [[c.h2() for c in r.children] for r in rows] == PAIRS
    _row_checks(root)


@DEMO
def test_stats_lone_cards_are_not_in_rows(demo_mode):
    root = _render("stats/_stats_sections.html", _stats_ctx(), demo_mode)
    cards = [n for n in root.walk() if "card" in n.classes]
    names = [c.h2() for c in cards]
    assert names.index(COMBO) == 4
    lone = [COMBO] + ([] if demo_mode else [CONTROL])
    for n in lone:
        assert "page-row" not in cards[names.index(n)].parent.classes
    if not demo_mode:
        assert names[-1] == CONTROL


def test_stats_pistol_absent_leaves_eco_alone():
    root = _render("stats/_stats_sections.html", _stats_ctx(pistol=False), False)
    first = _rows(root)[0]
    assert len(first.children) == 1
    assert first.children[0].h2() != "Pistols won into match wins"


def test_stats_detail_has_one_page_wide():
    for group in ("all", "friends"):
        ctx = _stats_ctx()
        ctx["group"] = group
        root = _render("stats/detail.html", ctx, False)
        assert len([n for n in root.walk() if "page-wide" in n.classes]) == 1
        _row_checks(root)


@DEMO
def test_squad_form_and_shoutouts_row(demo_mode):
    root = _render("squad/_squad_sections.html", _squad_ctx(), demo_mode)
    rows = _rows(root)
    assert len(rows) == 1 and "page-row--60-40" in rows[0].classes
    assert [c.h2() for c in rows[0].children] == ["Squad form", "Shoutouts"]
    cont = [n for n in root.walk() if "shoutout-rows" in n.classes]
    assert len(cont) == 1 and "shoutout-rows--side" in cont[0].classes
    sizes = [len([c for c in r.children if "shoutout-card" in c.classes]) for r in cont[0].children]
    assert sizes == [3, 3, 2]
    ranked = _top_card(root, "Your friends, ranked")
    assert len(ranked) == 1 and "page-row" not in ranked[0].parent.classes
    _row_checks(root)


def test_squad_without_shoutouts_leaves_form_alone():
    root = _render("squad/_squad_sections.html", _squad_ctx(0), False)
    row = _rows(root)[0]
    assert [c.h2() for c in row.children] == ["Squad form"]


def test_squad_detail_has_one_page_wide():
    root = _render("squad/detail.html", _squad_ctx(), False)
    assert len([n for n in root.walk() if "page-wide" in n.classes]) == 1
    assert [n for n in root.walk() if n.tag == "script"]
    _row_checks(root)
