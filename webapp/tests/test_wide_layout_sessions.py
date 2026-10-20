"""The session page's wide layout (S10): row 1 a stack of Roster and Impact Leaderboard | Shoutouts,
row 2 Round win rate | Fun Stats, row 3 Economy | KDA by match, Matches below. Rendered with
StrictUndefined so a sparse context can't pass."""
from datetime import datetime, timezone
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


FUN_FIELDS = ["biggest_multi_kill", "longest_kill_streak", "most_multi_kills", "most_kills_on_enemy_top_frag",
              "most_deaths_to_enemy_bottom_frag", "most_clutches", "round_mvp", "eco_frags", "op_crutch",
              "most_trades_made", "most_traded", "most_econ_upset_deaths", "most_round_changer",
              "most_xvx_kills", "most_spike_deaths", "post_plant_menace", "most_ghost_rounds",
              "most_entry_kills", "most_late_round_kills", "most_first_deaths_in_losses",
              "most_last_alive_in_wipes"]


def _graph():
    node = SimpleNamespace(label="5v5", sublabel="50%", x=1, y=1, radius=5, fill="#fff",
                           text_fill="#000", short_label="")
    return SimpleNamespace(nodes=[node], edges=[], view_box="0 0 10 10")


def _shoutouts(n):
    return [SimpleNamespace(agent="Jett", display_name=f"p{i}#NA1", headline=f"h{i}", detail=f"d{i}")
            for i in range(n)]


def _ctx(shoutouts=10):
    t0 = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)
    match = SimpleNamespace(id=1, external_id="abc", map_name="Haven", team1_rounds_won=13,
                            team2_rounds_won=5, played_at=t0)
    ts = SimpleNamespace(total_impact=5.0)
    summary = SimpleNamespace(team_summaries_by_team={"team-1": ts, "team-2": ts})
    session = SimpleNamespace(index=1, started_at=t0, ended_at=t0, matches=[match], wins=1, losses=0,
                              roster_ordered=["Aa#NA1", "Bb#NA1"], core_display_names=["Aa#NA1"],
                              team_by_match={1: "team-1"}, match_summaries={1: summary})
    entry = SimpleNamespace(display_name="Aa#NA1", agents=["Jett"], kills=1, deaths=1, assists=1, trades=1,
                            average_impact=1.0, average_round_win_impact=1.0, average_death_impact=-1.0,
                            rounds_played=5, rounds_won=3, rounds_lost=2)
    fun = SimpleNamespace(**{f: None for f in FUN_FIELDS})
    fun.longest_kill_streak = SimpleNamespace(value=4, display_name="Aa#NA1", runner_up=None)
    kda = SimpleNamespace(display_name="Aa#NA1", agents=["Jett"], total_label="1/1/1",
                          by_match={1: SimpleNamespace(label="1/1/1")})
    matrix = SimpleNamespace(total_rounds=2, tiers=[], tier_labels={}, cells={})
    stats = SimpleNamespace(
        leaderboard=[entry], shoutouts=_shoutouts(shoutouts), round_win_diagram=_graph(),
        round_win_diagrams_by_match={1: _graph()}, econ_tier_matrix=matrix, econ_pistol_stats=None,
        fun_stats=fun, kda_rows=[kda])
    return {"request": None, "site_name": "X", "session": session, "stats": stats,
            "matches_by_id": {1: match}, "friends_enabled": False, "other_session_index": None,
            "current_player": None, "econ_loadout_scatter": None}


def _render(demo_mode=False, **kw):
    env = templates.env.overlay(undefined=jinja2.StrictUndefined)
    ctx = _ctx(**kw)
    ctx["demo_mode"] = demo_mode
    return _parse(env.get_template("sessions/detail.html").render(**ctx))


def _wide(root):
    wides = [n for n in root.walk() if "page-wide" in n.classes]
    assert len(wides) == 1
    return wides[0]


def _rows(wide):
    return [c for c in wide.children if "page-row" in c.classes]


@pytest.fixture(params=[False, True], ids=["site", "demo"])
def root(request):
    return _render(demo_mode=request.param)


def test_card_order_and_rows(root):
    wide = _wide(root)
    row1, row2, row3 = _rows(wide)
    assert "page-row--60-40" in row1.classes
    assert "page-row--halves" in row2.classes and "page-row--halves" in row3.classes
    stack, shout = row1.children
    assert "page-stack" in stack.classes
    assert [c.h2() for c in stack.children] == ["Roster", "Impact Leaderboard"]
    assert shout.h2() == "Shoutouts"
    assert [c.h2() for c in row2.children] == ["Round win rate by game state", "Fun Stats"]
    assert [c.h2() for c in row3.children] == ["Economy win rate by buy tier", "KDA by match"]
    cards = [c for c in wide.children if "card" in c.classes]
    assert [c.h2() for c in cards] == ["Matches"]
    assert wide.children.index(row3) < wide.children.index(cards[0])


def test_shoutouts_are_balanced_in_threes_in_the_side_column(root):
    wide = _wide(root)
    cont = [n for n in wide.walk() if "shoutout-rows" in n.classes]
    assert len(cont) == 1 and "shoutout-rows--side" in cont[0].classes
    assert [len([c for c in r.children if "shoutout-card" in c.classes]) for r in cont[0].children] == [3, 3, 2, 2]


def test_no_script_inside_a_row_or_stack(root):
    scripts = [n for n in root.walk() if n.tag == "script"]
    assert scripts
    for n in root.walk():
        if "page-row" in n.classes or "page-stack" in n.classes:
            assert all(c.tag != "script" for c in n.walk())
            assert all("card" in c.classes or "page-stack" in c.classes for c in n.children)


def test_every_table_in_a_row_is_scrollable(root):
    wide = _wide(root)
    for row in _rows(wide):
        for t in (n for n in row.walk() if n.tag == "table"):
            p = t.parent
            while p is not row and "table-scroll" not in p.classes and "chart-scroll" not in p.classes:
                p = p.parent
            assert p is not row
    kda = [n for n in wide.walk() if n.tag == "table" and n.parent is not None and
           any(x.h2() == "KDA by match" for x in [_ancestor_card(n)])][0]
    assert "table-scroll" in kda.parent.classes
    lb = [n for n in wide.walk() if n.tag == "table" and _ancestor_card(n).h2() == "Impact Leaderboard"][0]
    assert "table-scroll" in lb.parent.classes
    m = [n for n in wide.walk() if n.tag == "table" and _ancestor_card(n).h2() == "Matches"][0]
    assert "table-scroll" in m.parent.classes


def _ancestor_card(n):
    while "card" not in n.classes:
        n = n.parent
    return n


def test_tab_script_targets_exist(root):
    ids = [n.attrs["id"] for n in root.walk() if "id" in n.attrs]
    assert ids.count("session-win-graph-meta") == 1
    assert ids.count("session-win-graph-combined") == 1


def test_no_shoutouts_leaves_the_stack_alone_in_row_one():
    wide = _wide(_render(shoutouts=0))
    row1 = _rows(wide)[0]
    assert len(row1.children) == 1 and "page-stack" in row1.children[0].classes
    assert not [n for n in wide.walk() if "shoutout-rows" in n.classes]
