"""The player page's wide layout (S8): cards in side-by-side rows. Rendered with StrictUndefined so a
sparse context can't pass."""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
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


def _graph():
    node = SimpleNamespace(label="5v5", sublabel="50%", x=1, y=1, radius=5, fill="#fff",
                           text_fill="#000", short_label="")
    edge = SimpleNamespace(x1=0, y1=0, x2=1, y2=1, stroke="#fff", stroke_width=1, weight=1,
                           source="5v5", target="4v5", label="2", label_x=1, label_y=1)
    return SimpleNamespace(nodes=[node], edges=[edge], view_box="0 0 10 10")


def _stat(key):
    return SimpleNamespace(key=key, matches_played=3, win_rate=0.5, wins=1, losses=1,
                           average_impact=5.0, average_kill_impact=6.0, average_death_impact=-1.0)


def _ctx(round_win=True, scope="recent", demo_mode=False):
    profile = SimpleNamespace(
        player=SimpleNamespace(display_name="Aa#NA1"),
        agent_counts=Counter({"Jett": 2}),
        overall_average_impact=1.0, overall_average_round_win_impact=2.0, overall_average_death_impact=-3.0,
        matches=[1, 2, 3], avg_traded_teammate=1.0, avg_traded_by_teammate=1.0,
        top_traded_teammate=[("Bb#NA1", 2)], top_traded_by_teammate=[],
        agent_stats=[_stat("Jett")], map_stats=[_stat("Haven"), _stat("Ascent"), _stat("Bind")],
    )
    tier = SimpleNamespace(total_rounds=2, tiers=["full"], tier_labels={"full": "Full"},
                           cells={("full", "full"): SimpleNamespace(win_pct=0.5, fill="#fff", wins=1, total=2,
                                                                    avg_loadout_ratio=0.5)})
    pistol = SimpleNamespace(lost_both_total=1, won_one_total=1, won_both_total=1,
                             lost_both_wins=0, won_one_wins=1, won_both_wins=1,
                             lost_both_win_pct=0.0, won_one_win_pct=1.0, won_both_win_pct=1.0,
                             lost_both_fill="#fff", won_one_fill="#fff", won_both_fill="#fff")
    return {
        "filter_empty": False, "scope": scope, "profile": profile, "demo_mode": demo_mode,
        "view_query": "", "filter_label": "",
        "form": SimpleNamespace(total_matches=0), "recent_rows": None,
        "round_win_graph": _graph() if round_win else SimpleNamespace(nodes=[], edges=[], view_box=""),
        "kill_order_graph": _graph(),
        "top_kill_differentials": [("5v5", 2)], "top_death_differentials": [("5v5", -2)],
        "fight_ev_data": {"attacking_all_teammates": []},
        "econ_tier_matrix": tier, "econ_pistol_stats": None, "econ_loadout_scatter": None,
        "pistol_match_stats": pistol,
        "chart_data": {}, "highlights_chart_data": {}, "map_chart_data": {},
    }


def _render(**kw):
    env = templates.env.overlay(undefined=jinja2.StrictUndefined)
    return _parse(env.get_template("players/_profile_sections.html").render(**_ctx(**kw)))


def _rows(root):
    return [n for n in root.walk() if "page-row" in n.classes]


def _row_of(root, h2):
    rows = [r for r in _rows(root) if h2 in [c.h2() for c in r.children]]
    assert len(rows) == 1, h2
    return rows[0]


@pytest.fixture(params=[False, True], ids=["site", "demo"])
def root(request):
    return _render(demo_mode=request.param)


PAIRS = [
    ("Form", "Match Data Highlights"),
    ("Round win rate by game state", "Kill order graph"),
    ("Economy win rate by buy tier", "Pistols won into match wins"),
]


@pytest.mark.parametrize("left,right", PAIRS)
def test_pairs_share_a_halves_row_in_order(root, left, right):
    row = _row_of(root, left)
    assert "page-row--halves" in row.classes
    assert [c.h2() for c in row.children] == [left, right]


def test_full_width_cards_are_not_in_a_row(root):
    for title in ("Fight-EV: converting duels into round-win leverage", "Maps",
                  "Kill impact vs. death impact by match"):
        cards = [n for n in root.walk() if "card" in n.classes and n.h2() == title]
        assert len(cards) == 1
        p = cards[0].parent
        while p is not None:
            assert "page-row" not in p.classes and "page-stack" not in p.classes
            p = p.parent


def test_maps_comes_before_the_economy_row(root):
    order = [n for n in root.walk() if "card" in n.classes and n.h2() in
             ("Maps", "Economy win rate by buy tier", "Kill impact vs. death impact by match")]
    assert [c.h2() for c in order] == ["Maps", "Economy win rate by buy tier",
                                        "Kill impact vs. death impact by match"]


def test_no_script_inside_a_row_or_stack(root):
    assert _rows(root)
    for n in root.walk():
        if "page-row" in n.classes or "page-stack" in n.classes:
            assert all(c.tag != "script" for c in n.walk())
            assert all("card" in c.classes or "page-stack" in c.classes for c in n.children)


def test_every_table_in_a_row_is_scrollable(root):
    tables = [t for r in _rows(root) for t in r.walk() if t.tag == "table"]
    assert len(tables) == 4  # agents, two swing tables, econ matrix
    for t in tables:
        p, ok = t.parent, False
        while p is not None and "page-row" not in p.classes:
            ok = ok or "table-scroll" in p.classes or "chart-scroll" in p.classes
            p = p.parent
        assert ok


def test_named_tables_use_table_scroll(root):
    for title in ("Match Data Highlights", "Kill order graph", "Maps"):
        card = [n for n in root.walk() if "card" in n.classes and n.h2() == title][0]
        for t in [n for n in card.walk() if n.tag == "table"]:
            p = t.parent
            while p is not card and "table-scroll" not in p.classes:
                p = p.parent
            assert p is not card


def test_no_round_win_graph_leaves_kill_order_alone_in_its_row():
    root = _render(round_win=False)
    row = _row_of(root, "Kill order graph")
    assert [c.h2() for c in row.children] == ["Kill order graph"]


def test_career_scope_has_the_same_rows():
    row = _row_of(_render(scope="career"), "Kill order graph")
    assert [c.h2() for c in row.children] == ["Round win rate by game state", "Kill order graph"]


def test_detail_page_opts_in_to_page_wide():
    src = (Path(__file__).resolve().parents[1] / "app" / "templates" / "players" / "detail.html").read_text(
        encoding="utf-8")
    assert src.count('class="page-wide"') == 1
