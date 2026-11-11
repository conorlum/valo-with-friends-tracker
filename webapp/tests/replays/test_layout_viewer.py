"""The replay and coach viewer use layout assets, with map-level scale unchanged."""

import json
import re
import shutil
import subprocess

import pytest

from test_control_store import db, factory, linked
from test_layout_selection import layout_assets, layout_chokes, write_json
from test_gap_patterns import make_replay, put_run, gap, control_fp
from test_gap_patterns_web import request as gaps_request, status_of
from test_replay_routes import request
from test_replay_store import condensed

from app.models import RoundPlayerStat
from app.replays import choke_assets, condense, format as fmt
from app.routers import replays as routes
from app.services import replay_control as rc, replays as service
from app.templates import templates
from app.replays import layouts
from app.routers import gap_patterns as gap_routes
from app.services import gap_patterns as patterns, replay_gaps


@pytest.fixture
def toy(db, linked, layout_assets, monkeypatch):
    RoundPlayerStat.__table__.create(db.get_bind(), checkfirst=True)
    linked.map_name = "Toy"
    linked.game_branch = "release-13.10"
    # Exercise the real maps.json scale loader with only the map-name entry.
    original = condense.load_maps
    map_info = original()["Ascent"]
    monkeypatch.setattr(condense, "load_maps", lambda: {"Toy": map_info})
    service._uv_per_unit.cache_clear()
    monkeypatch.setattr(templates, "context_processors", [])
    yield linked
    service._uv_per_unit.cache_clear()


def page(db, replay):
    return routes.replay_page(request(), str(replay.match_uuid), db).body.decode()


def image_urls(html):
    """Evaluate the actual rendered viewer options, including their version query."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    match = json.loads(re.search(r'<script id="replay-data"[^>]*>(.*?)</script>', html, re.S)[1])["match"]
    expressions = [re.search(rf"^\s*{name}: (.*),$", html, re.M)[1]
                   for name in ("mapImage", "bulletMask")]
    script = "const match = JSON.parse(process.argv[1]); process.stdout.write(JSON.stringify([" + ",".join(expressions) + "]));"
    result = subprocess.run([node, "-e", script, json.dumps(match)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("branch,key", [("release-13.10", "Toy_13_10"), ("release-13.08", "Toy")])
def test_page_loads_the_layout_minimap_and_bullet_mask(db, toy, branch, key):
    toy.game_branch = branch
    html = page(db, toy)
    assert image_urls(html) == [f"/static/img/maps/{key}.png",
                                f"/static/data/control/{key}.bullet.png?v={templates.env.globals['static_version']()}"]
    assert "The map changed in" not in html
    assert "map has several layouts" not in html


@pytest.mark.parametrize("branch,state,patch,note", [
    ("release-13.10", "layout_pending", "13.10",
     "The map changed in 13.10; this replay's map control is out of date until it is redrawn"),
    ("unparsed", "layout_unknown", None,
     "This replay's patch is unknown and the map has several layouts; map control is not shown"),
])
def test_unavailable_layout_explains_why_control_is_hidden(db, toy, layout_assets, branch, state, patch, note):
    folder, index, _ = layout_assets
    del index["maps"]["Toy_13_10"]
    write_json(folder / "index.json", index)
    toy.game_branch = branch
    context = service.page_context(db, toy)["match"]
    html = page(db, toy)
    assert note in html
    assert context["asset"] == "Toy"
    assert context["layout"] == {"id": "Toy@13.10" if patch else None, "state": state, "patch": patch}
    assert context["control"] is None and context["gaps"] is False
    assert 'data-replay-layer="control"' not in html
    assert 'data-replay-layer="gaps"' not in html
    assert image_urls(html) == ["/static/img/maps/Toy.png",
                                f"/static/data/control/Toy.bullet.png?v={templates.env.globals['static_version']()}"]
    response = routes.replay_round_gaps(request(), str(toy.match_uuid), 1, db)
    assert response.status_code == 404 and json.loads(response.body) == {"status": state}


def test_match_context_keeps_map_scale_and_reads_layout_control(db, toy, layout_assets):
    folder, _, _ = layout_assets
    write_json(folder / "tags.json", {"maps": {
        "Toy": {"map_features": {"features": [{"id": "door", "name": "Base door"}]}},
        "Toy_13_10": {"map_features": {"features": [{"id": "door", "name": "Later door"}]}},
    }})
    context = service.page_context(db, toy)["match"]
    assert context["map"] == "Toy"
    assert context["asset"] == "Toy_13_10"
    assert context["layout"] == {"id": "Toy@13.10", "state": "ok", "patch": "13.10"}
    assert context["uv_per_unit"] == round(abs(condense.load_maps()["Toy"].x_mult) * fmt.UV_SCALE, 5)
    assert context["control"]["feature_names"] == {"door": "Later door"}
    viewer = routes.viewer_context(request(), db, toy)
    assert viewer["replay"]["map_name"] == "Toy"
    assert viewer["replay"]["asset"] == "Toy_13_10"


def test_base_layout_keeps_the_existing_match_values(db, toy):
    toy.game_branch = "release-13.08"
    match = service.page_context(db, toy)["match"]
    assert match["asset"] == match["map"] == "Toy"
    assert match["layout"] == {"id": "Toy@base", "state": "ok", "patch": None}
    assert match["control"] == rc.map_layer("Toy")
    assert match["gaps"] is True


def test_gaps_endpoint_reads_chokes_for_the_replays_layout(db, toy, layout_assets, monkeypatch):
    folder, _, _ = layout_assets
    for key, cells in (("Toy", [1, 2]), ("Toy_13_10", [3, 4])):
        choke_assets.save(key, [choke_assets.Choke(1, "1", cells)], 2, asset_dir=folder)
    original = routes.gaps_view.view_rows
    monkeypatch.setattr(routes.gaps_view, "view_rows", lambda rows, key: original(rows, key, asset_dir=folder))
    monkeypatch.setattr(routes.gaps_service, "round_gaps", lambda *args: ("not_computed", []))
    response = routes.replay_round_gaps(request(), str(toy.match_uuid), 1, db)
    assert response.status_code == 200
    chokes = json.loads(response.body)["chokes"]
    assert chokes == routes.gaps_view.choke_points("Toy_13_10", folder)
    assert chokes != routes.gaps_view.choke_points("Toy", folder)


@pytest.fixture
def pattern_layouts(db, layout_assets, layout_chokes, monkeypatch):
    folder, _, registry = layout_assets
    monkeypatch.setattr(layouts, "load_registry", lambda: registry)
    monkeypatch.setattr(gap_routes, "get_current_player", lambda *args: None)
    monkeypatch.setattr(templates, "context_processors", [])
    original = patterns.choke_points
    monkeypatch.setattr(patterns, "choke_points", lambda key: original(key, folder))
    replays = {}
    for branch, key in (("release-13.08", "Toy"), ("release-13.10", "Toy_13_10")):
        replay = make_replay(db, rounds=1, map_name="Toy")
        replay.game_branch = branch
        put_run(db, replay, 1, fingerprint=replay_gaps.gap_fingerprint(control_fp(replay, 1), key))
        # The detector writes geo.name (the layout key) into ReplayGap.map.
        gap(db, replay, 1, 0, map=key)
        # Even a row for the counted round must belong to the selected layout.
        gap(db, replay, 1, 1, map="Toy_13_10" if key == "Toy" else "Toy")
        replays[key] = replay
    return replays


@pytest.mark.parametrize("params,key", [({}, "Toy_13_10"), ({"layout": "Toy"}, "Toy")])
def test_patterns_count_and_draw_only_the_selected_layout(db, pattern_layouts, params, key):
    response = gap_routes.gaps_map(gaps_request(params), "Toy", db)
    data = response.context["data"]
    assert data["map"] == "Toy" and data["layout"] == key
    assert data["layouts"] == [{"key": "Toy", "id": "Toy@base"},
                               {"key": "Toy_13_10", "id": "Toy@13.10"}]
    assert data["rounds"] == {"counted": 1, "replays": 1, "old_blob": 0,
                              "left_out": {"stale": 0, "failed": 0, "not_computed": 0}}
    assert data["rows"]["total"] == 1
    assert data["patterns"][0]["rounds"][0]["match_uuid"] == pattern_layouts[key].match_uuid
    assert data["chokes"] == patterns.choke_points(key)
    drawing = response.context["drawing"]
    assert drawing["map"] == "Toy" and drawing["asset"] == key
    assert drawing["params"] == ({"layout": "Toy"} if key == "Toy" else {})
    html = response.body.decode()
    assert "data-gaps-layouts" in html and "Toy@base" in html and "Toy@13.10" in html
    assert ('name="layout" value="Toy"' in html) == (key == "Toy")
    assert 'href="/gaps/Toy"' in html
    assert 'href="/gaps/Toy?layout=Toy"' in html


@pytest.mark.parametrize("key", ["Ascent", "Nowhere", "Toy_99_99"])
def test_patterns_reject_another_maps_or_unknown_layout(db, pattern_layouts, key):
    assert status_of(lambda: gap_routes.gaps_map(gaps_request({"layout": key}), "Toy", db)) == 404
    assert status_of(lambda: gap_routes.gaps_routes(gaps_request({"layout": key, "seq": "1-2"}), "Toy", db)) == 404


def test_patterns_default_to_the_newest_layout_with_assets(db, pattern_layouts, layout_assets):
    folder, index, _ = layout_assets
    del index["maps"]["Toy_13_10"]
    write_json(folder / "index.json", index)
    response = gap_routes.gaps_map(gaps_request(), "Toy", db)
    assert response.context["data"]["layout"] == "Toy"
    assert response.context["data"]["rounds"]["counted"] == 1
    assert response.context["data"]["rounds"]["left_out"] == {"stale": 0, "failed": 0, "not_computed": 0}
    assert "data-gaps-layouts" not in response.body.decode()
    assert status_of(lambda: gap_routes.gaps_map(gaps_request({"layout": "Toy_13_10"}), "Toy", db)) == 404
    assert status_of(lambda: gap_routes.gaps_routes(
        gaps_request({"layout": "Toy_13_10", "seq": "1-2"}), "Toy", db)) == 404


@pytest.mark.parametrize("params,key", [({}, "Toy_13_10"), ({"layout": "Toy"}, "Toy")])
def test_pattern_routes_honour_layout(db, pattern_layouts, params, key):
    response = gap_routes.gaps_routes(gaps_request({"seq": "1-2", **params}), "Toy", db)
    data = json.loads(response.body)
    assert data["map"] == "Toy"
    assert len(data["rows"]) == 1
    assert data["rows"][0]["match_uuid"] == pattern_layouts[key].match_uuid


def test_patterns_index_counts_the_default_layout_once(db, pattern_layouts):
    response = gap_routes.gaps_index(gaps_request(), db)
    assert response.context["maps"] == [{"name": "Toy", "rounds": 1, "replays": 1}]


def test_patterns_exclude_replays_whose_layout_is_unknown(db, pattern_layouts):
    replay = make_replay(db, rounds=2, map_name="Toy")
    assert replay.game_branch == "b"
    for key in ("Toy", "Toy_13_10"):
        data = patterns.page_data(db, key, patterns.Filters(), None)
        assert data["rounds"]["counted"] == 1
        assert data["rounds"]["left_out"] == {"stale": 0, "failed": 0, "not_computed": 0}


def test_single_layout_patterns_keep_the_previous_page_data(db, monkeypatch):
    monkeypatch.setattr(templates, "context_processors", [])
    monkeypatch.setattr(gap_routes, "get_current_player", lambda *args: None)
    replay = make_replay(db, rounds=1)
    put_run(db, replay, 1)
    gap(db, replay, 1, 0, flicker=True)
    data = patterns.page_data(db, "Ascent", patterns.Filters(), None)
    assert data.pop("layout") == "Ascent"
    assert data.pop("layouts") == [{"key": "Ascent", "id": "Ascent@base"}]
    # Recorded from the existing gap-pattern fixture before the layout change.
    assert data == {
        "map": "Ascent",
        "filters": {"kind": "both", "side": "both", "cause": "any", "use": "any", "t_max": None,
                    "flickers": False, "pop": "everyone", "dir": "left"},
        "params": {}, "population": "everyone", "direction": None, "friends_available": False,
        "rounds": {"counted": 1, "left_out": {"stale": 0, "failed": 0, "not_computed": 0},
                   "old_blob": 0, "replays": 1},
        "rows": {"total": 0, "predicted": 0, "backshot": 0, "unknown_seq": 0},
        "patterns": [], "shapes": {}, "shape_threshold_m": 4.0, "shape_cap": 2000,
        "chokes": patterns.choke_points("Ascent"),
    }
    response = gap_routes.gaps_map(gaps_request(), "Ascent", db)
    assert "data-gaps-layouts" not in response.body.decode()
    assert response.context["drawing"]["params"] == {}


@pytest.mark.parametrize("branch,suffix", [("release-13.08", "?layout=Toy"), ("release-13.10", "")])
def test_replay_patterns_link_keeps_its_layout(db, toy, layout_assets, monkeypatch, branch, suffix):
    monkeypatch.setattr(layouts, "load_registry", lambda: layout_assets[2])
    toy.game_branch = branch
    assert f'href="/gaps/Toy{suffix}" data-replay-gaps-patterns' in page(db, toy)


def test_pattern_minimap_uses_the_drawings_asset():
    from pathlib import Path

    source = (Path(__file__).parents[2] / "app/static/js/gap_patterns.js").read_text(encoding="utf-8")
    expression = re.search(r"image.src = (.*);", source)[1]
    node = shutil.which("node")
    assert node is not None
    script = 'const drawing = {map: "Toy", asset: "Toy_13_10"}; process.stdout.write(' + expression + ');'
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "/static/img/maps/Toy_13_10.png"
