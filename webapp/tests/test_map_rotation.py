"""Spawn-based display rotation is shared by every server-rendered map surface."""

import json
import sys
from pathlib import Path

import pytest

WEBAPP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP / "tests" / "replays"))
sys.path.insert(0, str(WEBAPP / "scripts"))

from test_layout_viewer import (  # noqa: E402,F401 (shared SQLite fixtures)
    condensed, db, factory, gaps_request, layout_assets, layout_chokes, linked,
    pattern_layouts, toy,
)
import control_tagger  # noqa: E402
import render_replay_standalone as standalone  # noqa: E402
from app.routers import gap_patterns as gap_routes  # noqa: E402
from app.services import map_rotation, replays  # noqa: E402


ROTATIONS = {
    "Abyss": 1, "Ascent": 1, "Bind": 0, "Breeze": 0, "Corrode": 1,
    "Fracture": 0, "Haven": 1, "Icebox": 3, "Lotus": 0, "Pearl": 0,
    "Split": 1, "Summit": 0, "Sunset": 0,
}


def spawns(u, v):
    # Same list-of-callouts shape as callouts.json; image v increases downwards.
    return [{"name": "Attacker Side Spawn", "super": "Attacker Side", "u": u, "v": v},
            {"name": "Defender Side Spawn", "super": "Defender Side", "u": 5000, "v": 5000}]


@pytest.mark.parametrize("name,expected", ROTATIONS.items())
def test_real_maps(name, expected):
    assert map_rotation.quarter_turns(name) == expected


@pytest.mark.parametrize("name,expected", [("Haven_13_10", 1), ("Nowhere", 0), (None, 0)])
def test_layout_suffix_and_unknown_maps(name, expected):
    assert map_rotation.quarter_turns(name) == expected


@pytest.mark.parametrize("rows", [[], spawns(7000, 5000)[:1], spawns(7000, 5000)[1:]])
def test_missing_spawn(rows):
    assert map_rotation.quarter_turns("Toy", table={"Toy": rows}) == 0


@pytest.mark.parametrize("u,v,expected", [
    (7000, 5000, 1), (3000, 5000, 3), (5000, 3000, 2), (5000, 7000, 0),
])
def test_cardinal_directions(u, v, expected):
    assert map_rotation.quarter_turns("Toy", table={"Toy": spawns(u, v)}) == expected


def test_empty_table_overrides_real_callouts():
    assert map_rotation.quarter_turns("Haven", table={}) == 0


@pytest.mark.parametrize("has_spawns,expected", [(False, 0), (True, 1)])
def test_replay_context_and_viewer_options(db, toy, monkeypatch, has_spawns, expected):
    if has_spawns:
        monkeypatch.setattr(map_rotation, "_callouts", lambda: {"Toy": spawns(7000, 5000)})
    assert replays.page_context(db, toy)["match"]["rotation"] == expected
    from test_layout_viewer import page

    assert "rotation: match.rotation || 0," in page(db, toy)


@pytest.mark.parametrize("key", ["Toy", "Toy_13_10"])
def test_gap_drawing_rotation_uses_map_name(db, pattern_layouts, monkeypatch, key):
    monkeypatch.setattr(map_rotation, "_callouts", lambda: {"Toy": spawns(3000, 5000)})
    response = gap_routes.gaps_map(gaps_request({"layout": key}), "Toy", db)
    drawing = response.context["drawing"]
    assert drawing["map"] == "Toy" and drawing["asset"] == key
    assert drawing["rotation"] == 3


def test_tagger_build_and_embedded_data_have_rotation():
    tags = json.loads((control_tagger.cg.ASSET_DIR / "tags.json").read_text(encoding="utf-8"))
    names = ["Abyss", "Breeze"]
    maps = control_tagger.build(tags, {}, names)
    assert {name: data["rotation"] for name, data in maps.items()} == {"Abyss": 1, "Breeze": 0}
    page = control_tagger.render(tags, maps)
    data = json.loads(page.split("var DATA = ", 1)[1].split(";\n", 1)[0])
    assert {name: data["rotation"] for name, data in data["maps"].items()} == {"Abyss": 1, "Breeze": 0}


def test_standalone_payload_and_viewer_options(condensed):
    page = standalone.render(condensed.rounds)
    data = json.loads(page.split("window.REPLAY_DATA = ", 1)[1].split(";\n", 1)[0])
    assert data["rotation"] == ROTATIONS[condensed.rounds[1]["map"]]
    assert "rotation: data.rotation || 0," in page
