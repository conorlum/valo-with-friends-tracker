"""The match page's wide layout (S3): row 1 Scoreboard | Shoutouts, row 2 a stack of Highlights and
Economy | game-state graph, Rounds below. Rendered with StrictUndefined so a sparse context can't pass."""
from html.parser import HTMLParser
from types import SimpleNamespace

import jinja2
import pytest

from app.templates import templates
from tests.test_match_page_result import _player, _summary

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


def _matrix():
    return SimpleNamespace(total_rounds=2, tiers=[], tier_labels={}, cells={})


def _graph():
    node = SimpleNamespace(label="5v5", sublabel="50%", x=1, y=1, radius=5, fill="#fff",
                           text_fill="#000", short_label="")
    return SimpleNamespace(nodes=[node], edges=[], view_box="0 0 10 10")


def _shoutouts(n):
    return [SimpleNamespace(agent="Jett", display_name=f"p{i}#NA1", headline=f"h{i}", detail=f"d{i}")
            for i in range(n)]


def _ctx(shoutouts=10, graph=True, demo_mode=False):
    players = [_player(1, "team-1"), _player(2, "team-2")]
    for p in players:
        for f in ("kills", "deaths", "assists", "traded_teammate"):
            setattr(p, f, 1)
    summary = _summary(players)
    summary.winner_team = "team-1"
    match = SimpleNamespace(external_id="abc", map_name="Haven", team1_rounds_won=13, team2_rounds_won=5,
                            played_at=None)
    tp = lambda: {"top": 0, "height": 4, "tier": "full", "value": 3000, "zero_top": 0}
    rounds = [{"round_number": 1, "outcome": "x", "winner": "team-1", "end_type": "elimination",
               "team_impact": {}}]
    return {
        "request": None, "match": match, "summary": summary, "shoutouts": _shoutouts(shoutouts),
        "viewer_player_id": None, "viewer_team": None, "viewer_team_label": None,
        "scoreboard_teams": [{"team": "team-1", "name": "Team 1", "rounds_won": 13, "won": True,
                              "viewer_label": None, "players": players[:1]},
                             {"team": "team-2", "name": "Team 2", "rounds_won": 5, "won": False,
                              "viewer_label": None, "players": players[1:]}],
        "timeline_rounds": rounds,
        "round_bars": {"height": 10, "zero_top": 5, "rounds": {1: {"team-1": tp()}}},
        "loadout_bars": {"height": 10, "rounds": {1: {"team-1": tp()}}},
        "initial_round_detail": None,
        "chart_data": {"labels": [1], "series": [], "viewer_player_id": None},
        "highlights_chart_data": {"labels": [], "default_player_id": "10", "players": {}},
        "team1_win_graph": _graph() if graph else SimpleNamespace(nodes=[], edges=[], view_box=""),
        "team2_win_graph": _graph() if graph else SimpleNamespace(nodes=[], edges=[], view_box=""),
        "econ_by_round": {},
        "team1_econ_tier_matrix": _matrix(), "team2_econ_tier_matrix": _matrix(),
        "team1_econ_pistol_stats": None, "team2_econ_pistol_stats": None,
        "econ_loadout_scatter": None,
        "replay_url": None, "coach_url": None,
        "current_player": None, "demo_mode": demo_mode,
    }


def _render(**kw):
    env = templates.env.overlay(undefined=jinja2.StrictUndefined)
    return _parse(env.get_template("matches/detail.html").render(**_ctx(**kw)))


def _wide(root):
    wides = [n for n in root.walk() if "page-wide" in n.classes]
    assert len(wides) == 1
    return wides[0]


def _cards(node):
    return [c for c in node.children if "card" in c.classes]


def _rows(wide):
    return [c for c in wide.children if "page-row" in c.classes]


@pytest.fixture(params=[False, True], ids=["site", "demo"])
def root(request):
    return _render(demo_mode=request.param)


def test_card_order_and_rows(root):
    wide = _wide(root)
    row1, row2 = _rows(wide)
    assert "page-row--60-40" in row1.classes and "page-row--halves" in row2.classes
    assert [c.h2() for c in row1.children] == ["Scoreboard", "Shoutouts"]
    stack, state = row2.children
    assert "page-stack" in stack.classes
    assert [c.h2() for c in stack.children] == [
        "Match Data Highlights", "Economy win rate by buy tier"]
    assert state.h2() == "Round win rate by game state"
    after = [c.h2() for c in wide.children if "card" in c.classes]
    assert after == ["Rounds", "Cumulative Impact"]
    assert wide.children.index(row2) < wide.children.index(_cards(wide)[0])


def test_banner_is_full_width_outside_rows(root):
    banner = [n for n in root.walk() if "result-banner" in n.classes]
    assert len(banner) == 1
    p = banner[0].parent
    while p is not None:
        assert "page-row" not in p.classes
        p = p.parent


def test_shoutouts_are_balanced_in_threes_in_the_side_column(root):
    wide = _wide(root)
    cont = [n for n in wide.walk() if "shoutout-rows" in n.classes]
    assert len(cont) == 1 and "shoutout-rows--side" in cont[0].classes
    assert [len([c for c in r.children if "shoutout-card" in c.classes]) for r in cont[0].children] == [3, 3, 2, 2]


def test_no_script_inside_a_row_or_stack(root):
    for n in root.walk():
        if "page-row" in n.classes or "page-stack" in n.classes:
            assert all(c.tag != "script" for c in n.walk())
            assert all("card" in c.classes or "page-stack" in c.classes for c in n.children)


def test_every_getelementbyid_target_exists_once(root):
    ids = [n.attrs["id"] for n in root.walk() if "id" in n.attrs]
    for needed in ("scoreboard-combined", "scoreboard-split", "econ-matrix-1", "team-win-graph-1",
                   "round-detail", "impact-chart", "impact-highlights-chart"):
        assert ids.count(needed) == 1


def test_no_shoutouts_leaves_scoreboard_alone_in_row_one():
    wide = _wide(_render(shoutouts=0))
    row1 = _rows(wide)[0]
    assert [c.h2() for c in row1.children] == ["Scoreboard"]
    assert not [n for n in wide.walk() if "shoutout-rows" in n.classes]


def test_no_game_state_graph_leaves_the_stack_alone_in_row_two():
    wide = _wide(_render(graph=False))
    row2 = _rows(wide)[1]
    assert len(row2.children) == 1 and "page-stack" in row2.children[0].classes
