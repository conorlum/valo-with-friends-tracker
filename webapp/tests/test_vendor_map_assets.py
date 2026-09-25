"""The vendored map/agent tables: their builder, and the committed files' spelling."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import vendor_map_assets  # noqa: E402
from app.templates import agent_icon_slug  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def _map(name, url, mult=7e-05, icon="https://x/icon.png"):
    return {"displayName": name, "mapUrl": url, "uuid": "u-" + name, "displayIcon": icon,
            "xMultiplier": mult, "yMultiplier": -mult, "xScalarToAdd": 0.5, "yScalarToAdd": 0.5}


def test_map_code_is_the_folder_after_game_maps():
    assert vendor_map_assets.map_code("/Game/Maps/Ascent/Ascent") == "Ascent"
    assert vendor_map_assets.map_code("/Game/Maps/HURM/HURM_Alley/HURM_Alley") == "HURM"
    with pytest.raises(ValueError):
        vendor_map_assets.map_code("/Game/Other/Ascent")


def test_only_maps_with_a_transform_and_icon_are_kept():
    table = vendor_map_assets.build_maps_table([
        _map("Ascent", "/Game/Maps/Ascent/Ascent"),
        _map("The Range", "/Game/Maps/Poveglia/Range", mult=0),
        _map("District", "/Game/Maps/HURM/HURM_Alley/HURM_Alley", mult=0),
        _map("Basic", "/Game/Maps/NPEV2/NPEV2", icon=None),
    ])
    assert list(table) == ["Ascent"]
    assert table["Ascent"]["code"] == "Ascent"
    assert table["Ascent"]["image"] == "img/maps/Ascent.png"
    assert table["Ascent"]["yMultiplier"] == -7e-05


def test_agents_table_maps_code_names_to_display_names():
    table = vendor_map_assets.build_agents_table([
        {"developerName": "Wushu", "displayName": "Jett", "isPlayableCharacter": True},
        {"developerName": "Grenadier", "displayName": "KAY/O", "isPlayableCharacter": True},
        {"developerName": "Npc", "displayName": "Bot", "isPlayableCharacter": False},
    ])
    assert table == {"Grenadier": "KAY/O", "Wushu": "Jett"}


def test_committed_agents_use_the_db_spelling():
    agents = json.loads((STATIC / "data" / "agents.json").read_text(encoding="utf-8"))
    assert agents["Grenadier"] == "KAY/O"
    assert agents["Wushu"] == "Jett"
    missing = [name for name in agents.values() if agent_icon_slug(name) is None]
    assert missing == []


def test_committed_maps_have_an_image_and_a_transform():
    maps = json.loads((STATIC / "data" / "maps.json").read_text(encoding="utf-8"))
    assert {"Ascent", "Bind", "Haven", "Split", "Lotus"} <= set(maps)
    for name, entry in maps.items():
        assert (STATIC / entry["image"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", name
        assert entry["map_url"].startswith(f"/Game/Maps/{entry['code']}/")
        assert all(entry[k] for k in vendor_map_assets.TRANSFORM_KEYS)
