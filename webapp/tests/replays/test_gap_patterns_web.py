"""The timing-gaps pattern page's routes, templates and drawing helpers
(docs/superpowers/plans/2026-10-04-timing-gaps-pattern-page.md, P2). SQLite with the service tests' fixtures
(tests/replays/test_gap_patterns.py); the JS helpers run in node."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from starlette.requests import Request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_gap_patterns import MAP, db, friends_world, gap, make_replay, put_run  # noqa: E402,F401  (fixtures)

from app.config import settings  # noqa: E402
from app.routers import gap_patterns as routes  # noqa: E402
from app.services import gap_patterns as service  # noqa: E402
from app.templates import templates  # noqa: E402

WEBAPP = HERE.parents[1]
PATTERNS_JS = WEBAPP / "app" / "static" / "js" / "gap_patterns.js"
NODE = shutil.which("node")


class Viewer:
    def __init__(self, player_id):
        self.id = player_id


def request(params=None):
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "session": {},
                    "query_string": urlencode(params or {}).encode()})


def status_of(call) -> int:
    with pytest.raises(HTTPException) as raised:
        call()
    return raised.value.status_code


@pytest.fixture
def captured(monkeypatch):
    """The template name and context a route renders, without rendering it."""
    seen = {}

    def fake(req, name, context, **kw):
        seen.update(name=name, context=context)
        return seen

    monkeypatch.setattr(routes.templates, "TemplateResponse", fake)
    return seen


def logged_in_as(monkeypatch, player_id):
    monkeypatch.setattr(routes, "get_current_player", lambda req, db: None if player_id is None else Viewer(player_id))


def render(name, context, **extra):
    return templates.env.get_template(name).render(**context, **extra)


def page_html(db, monkeypatch, captured, viewer=None, **params):
    logged_in_as(monkeypatch, viewer)
    routes.gaps_map(request(params), MAP, db)
    assert captured["name"] == "gaps/map.html"
    return render("gaps/map.html", captured["context"])


# ---------------------------------------------------------------- 404s


def test_every_gaps_route_404s_in_demo_mode(db, monkeypatch):
    replay = make_replay(db, rounds=1)
    put_run(db, replay, 1)
    gap(db, replay, 1, 0)
    monkeypatch.setattr(settings, "demo_mode", True)
    logged_in_as(monkeypatch, None)
    assert status_of(lambda: routes.gaps_index(request(), db)) == 404
    assert status_of(lambda: routes.gaps_map(request(), MAP, db)) == 404
    assert status_of(lambda: routes.gaps_routes(request({"seq": "1-2"}), MAP, db)) == 404


def test_an_unknown_map_or_one_without_the_control_layer_404s(db, monkeypatch):
    logged_in_as(monkeypatch, None)
    for name in ("Nowhere", "ascent", "../Ascent"):
        assert status_of(lambda: routes.gaps_map(request(), name, db)) == 404
        assert status_of(lambda: routes.gaps_routes(request({"seq": ""}), name, db)) == 404
    monkeypatch.setattr(routes.control_service, "map_layer", lambda name: None)
    assert status_of(lambda: routes.gaps_map(request(), MAP, db)) == 404


# ---------------------------------------------------------------- the index


def test_the_index_lists_control_maps_with_their_counted_rounds(db, captured):
    replay = make_replay(db, rounds=3)
    put_run(db, replay, 1)
    put_run(db, replay, 2)
    routes.gaps_index(request(), db)
    maps = {m["name"]: m for m in captured["context"]["maps"]}
    assert MAP in maps and maps[MAP]["rounds"] == 2 and maps[MAP]["replays"] == 1
    assert all(m["rounds"] == 0 for name, m in maps.items() if name != MAP)
    assert set(maps) == set(routes.control_maps())
    html = render("gaps/index.html", captured["context"])
    assert f'href="/gaps/{MAP}"' in html and routes.THIN_DATA_NOTE in html


# ---------------------------------------------------------------- the page


def test_query_string_filters_reach_page_data(db, monkeypatch, captured):
    seen = []
    real = service.page_data

    def spy(db_, map_name, filters, viewer):
        seen.append((map_name, filters, viewer))
        return real(db_, map_name, filters, viewer)

    monkeypatch.setattr(service, "page_data", spy)
    logged_in_as(monkeypatch, 42)
    routes.gaps_map(request({"kind": "predicted", "side": "defense", "cause": "victim_turned", "use": "shot",
                             "t_max": "30", "flickers": "1", "pop": "friends", "dir": "opponents"}), MAP, db)
    assert seen == [(MAP, service.Filters(kind="predicted", side="defense", cause="victim_turned", use="shot",
                                          t_max=30.0, flickers=True, pop="friends", dir="opponents"), 42)]
    assert captured["context"]["logged_in"] is True


def test_the_page_logs_how_long_page_data_took(db, monkeypatch, captured, caplog):
    logged_in_as(monkeypatch, None)
    with caplog.at_level("INFO", logger=routes.logger.name):
        routes.gaps_map(request(), MAP, db)
    assert any("page_data took" in rec.getMessage() and rec.levelname == "INFO" for rec in caplog.records)


def test_logged_out_shows_everyone_only_and_the_login_line(friends_world, monkeypatch, captured):
    html = page_html(friends_world["db"], monkeypatch, captured, None, pop="friends")
    assert 'value="everyone" checked' in html
    assert 'value="friends"' not in html and 'name="dir"' not in html
    assert routes.FRIENDS_LOGIN_NOTE in html and "data-gaps-login" in html
    assert "Everyone: every counted round." in html


def test_logged_in_offers_friends_and_the_direction_switch(friends_world, monkeypatch, captured):
    w = friends_world
    html = page_html(w["db"], monkeypatch, captured, w["viewer"], pop="friends", dir="opponents")
    assert 'value="friends" checked' in html and 'value="opponents" checked' in html
    assert "Gaps my group left open" in html and "Gaps our opponents left open" in html
    assert routes.FRIENDS_LOGIN_NOTE not in html
    assert "Friends: gaps our opponents left open" in html


def test_the_thin_data_line_and_the_left_out_counts(db, monkeypatch, captured):
    replay = make_replay(db, rounds=5)
    put_run(db, replay, 1)
    put_run(db, replay, 2, fingerprint="0" * 16)          # stale
    put_run(db, replay, 3, fingerprint="1" * 16)          # stale
    put_run(db, replay, 4, status="failed")
    # round 5: not worked out yet
    gap(db, replay, 1, 0)
    html = page_html(db, monkeypatch, captured)
    assert routes.THIN_DATA_NOTE in html and "not conclusions" in html
    assert "1 rounds from 1 replays count." in html
    assert "Left out: 2 stale" in html and "1 failed, 1 not worked out yet" in html


def test_the_patterns_table_shows_names_and_every_n(db, monkeypatch, captured):
    replay = make_replay(db, rounds=1)
    put_run(db, replay, 1)
    gap(db, replay, 1, 0, choke_seq=[1, 2], stood_at=12.0, cause="victim_turned")
    gap(db, replay, 1, 1, choke_seq=[1, 2], cause="victim_turned")
    gap(db, replay, 1, 2, kind="backshot", choke_seq=[1, 2], killed_at=15.0, linked_seq=0)
    gap(db, replay, 1, 3, choke_seq=[])                 # no choke: a shape group
    html = page_html(db, monkeypatch, captured)
    chokes = captured["context"]["data"]["chokes"]
    label = routes.route_label([1, 2], chokes)
    assert " → " in label and f">{label}<" in html
    assert 'data-seq="1-2"' in html
    row = html.split(f">{label}<", 1)[1].split("</tr>", 1)[0]
    assert "1 / 2" in row                                 # stood there
    assert "the player turned away (2 / 2)" in row
    assert "1 / 1" in row                                 # the back-shot killed
    assert "no choke, shape group 1" in html and 'data-shape="attack-1"' in html
    drawing = json.loads(html.split('id="gaps-drawing">', 1)[1].split("</script>", 1)[0])
    assert drawing["map"] == MAP and len(drawing["shapes"]["attack-1"][0]["points"]) == service.SHAPE_POINTS


def test_a_filtered_level_says_so_instead_of_a_share(db, monkeypatch, captured):
    replay = make_replay(db, rounds=1)
    put_run(db, replay, 1)
    gap(db, replay, 1, 0, stood_at=12.0)
    html = page_html(db, monkeypatch, captured, use="stood")
    assert "all, by the filter" in html


def test_the_page_renders_through_the_real_template_response(db, monkeypatch):
    logged_in_as(monkeypatch, None)
    response = routes.gaps_map(request(), MAP, db)
    assert response.status_code == 200 and b"gap_patterns.js" in response.body


def test_route_labels():
    chokes = {"1": {"name": "Mid doors", "x": 1, "y": 2}, "2": {"name": "2", "x": 3, "y": 4}}
    assert routes.route_label([1, 2, 9], chokes) == "Mid doors → 2 → choke 9"
    assert routes.route_label([], chokes) == "no choke"
    assert routes.parse_seq("") == [] and routes.parse_seq("3-12") == [3, 12]
    assert routes.parse_seq("a-1") is None and routes.parse_seq("1--2") is None and routes.parse_seq(None) is None


# ---------------------------------------------------------------- routes.json


def test_routes_json_returns_the_selected_patterns_routes(db, monkeypatch):
    logged_in_as(monkeypatch, None)
    replay = make_replay(db, rounds=2)
    put_run(db, replay, 1)
    put_run(db, replay, 2)
    gap(db, replay, 1, 0, choke_seq=[1, 2], t_open=12.25)
    gap(db, replay, 2, 0, choke_seq=[1, 2], victim_side="defense")
    gap(db, replay, 2, 1, choke_seq=[])
    body = json.loads(routes.gaps_routes(request({"seq": "1-2"}), MAP, db).body)
    assert body["map"] == MAP and body["choke_seq"] == [1, 2]
    assert [(r["round"], r["seq"]) for r in body["rows"]] == [(1, 0), (2, 0)]
    first = body["rows"][0]
    assert set(first) == {"match_uuid", "round", "seq", "t_open", "kind", "route"}
    assert first["match_uuid"] == replay.match_uuid and first["t_open"] == 12.25
    assert first["route"] == [[[8.0, 400, 200], [10.0, 500, 300]]]
    # the page's filters apply, and `seq=` is the empty sequence
    side = json.loads(routes.gaps_routes(request({"seq": "1-2", "side": "defense"}), MAP, db).body)
    assert [r["round"] for r in side["rows"]] == [2]
    empty = json.loads(routes.gaps_routes(request({"seq": ""}), MAP, db).body)
    assert [(r["round"], r["seq"]) for r in empty["rows"]] == [(2, 1)]
    assert routes.gaps_routes(request({"seq": "x"}), MAP, db).status_code == 400
    assert routes.gaps_routes(request(), MAP, db).status_code == 400


# ---------------------------------------------------------------- the viewer's link


def render_player(**context):
    return templates.env.get_template("replays/_player.html").render(
        replay={"map_name": "Ascent", "round_numbers": [1, 2]}, **context)


def test_the_viewer_gaps_tab_links_to_the_maps_patterns():
    html = render_player(linked=True, gaps=True)
    assert 'href="/gaps/Ascent"' in html and "Patterns on this map" in html
    assert "Patterns on this map" not in render_player(linked=True)


# ---------------------------------------------------------------- the drawing helpers (node)


def test_node_is_installed():
    assert NODE is not None, "node is needed for the drawing helpers' tests"


def test_the_drawing_helpers_in_node():
    script = """
      const G = require(process.argv[1]);
      process.stdout.write(JSON.stringify({
        path: G.routePath([[0, 0], [512, 1024]], 512),
        stored: G.routePath([[8.0, 256, 768]], 1024),
        pieces: G.routePieces([[[1, 1024, 0]], [], [[2, 0, 512], [3, 512, 512]]], 256),
        url: G.routesUrl("Ascent", "1-2", {side: "attack", flickers: "1"}),
        empty: G.routesUrl("Ascent", "", {}),
        link: G.roundLink({match_uuid: "abc", round: 7, t_open: 12.5}),
        labels: G.chokeLabels([3, 9, 3], {"3": {name: "Mid", x: 1, y: 2}}),
        seq: [G.seqOf(""), G.seqOf("4-10")]
      }));
    """
    completed = subprocess.run([NODE, "-e", script, str(PATTERNS_JS)], capture_output=True, text=True,
                               encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    got = json.loads(completed.stdout)
    assert got["path"] == [[0, 0], [256, 512]]
    assert got["stored"] == [[256, 768]]
    assert got["pieces"] == [[[256, 0]], [[0, 128], [128, 128]]]
    assert got["url"] == "/gaps/Ascent/routes.json?seq=1-2&flickers=1&side=attack"
    assert got["empty"] == "/gaps/Ascent/routes.json?seq="
    assert got["link"] == "/replays/abc?round=7&t=12.5"
    assert got["labels"] == [{"id": 3, "name": "Mid", "x": 1, "y": 2}]
    assert got["seq"] == [[], [4, 10]]
