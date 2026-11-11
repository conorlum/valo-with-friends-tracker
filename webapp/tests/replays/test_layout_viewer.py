"""The replay and coach viewer use layout assets, with map-level scale unchanged."""

import json
import re
import shutil
import subprocess

import pytest

from test_control_store import db, factory, linked
from test_layout_selection import layout_assets, write_json
from test_replay_routes import request
from test_replay_store import condensed

from app.models import RoundPlayerStat
from app.replays import choke_assets, condense, format as fmt
from app.routers import replays as routes
from app.services import replay_control as rc, replays as service
from app.templates import templates


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
