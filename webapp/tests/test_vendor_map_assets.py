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


def _sha12(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()[:12]


def test_a_refresh_keeps_a_hand_patched_minimap_while_upstream_is_unchanged(tmp_path, monkeypatch):
    upstream, patch = b"\x89PNG old upstream", b"\x89PNG hand patch"
    monkeypatch.setattr(vendor_map_assets, "PATCHED_MINIMAPS", {"Corrode": _sha12(upstream)})
    path = tmp_path / "Corrode.png"
    path.write_bytes(patch)
    assert vendor_map_assets.write_minimap(path, "Corrode", upstream) == "kept"
    assert path.read_bytes() == patch


def test_a_new_upstream_minimap_supersedes_the_hand_patch(tmp_path, monkeypatch):
    monkeypatch.setattr(vendor_map_assets, "PATCHED_MINIMAPS", {"Corrode": _sha12(b"\x89PNG old upstream")})
    path = tmp_path / "Corrode.png"
    path.write_bytes(b"\x89PNG hand patch")
    assert vendor_map_assets.write_minimap(path, "Corrode", b"\x89PNG redrawn upstream") == "superseded"
    assert path.read_bytes() == b"\x89PNG redrawn upstream"


def test_an_unpatched_minimap_is_always_written(tmp_path):
    path = tmp_path / "Ascent.png"
    path.write_bytes(b"\x89PNG stale")
    assert vendor_map_assets.write_minimap(path, "Ascent", b"\x89PNG fresh") == "written"
    assert path.read_bytes() == b"\x89PNG fresh"


def test_every_patched_minimap_differs_from_the_upstream_it_replaces_and_matches_its_control_tags():
    tags = json.loads((STATIC / "data" / "control" / "tags.json").read_text(encoding="utf-8"))["maps"]
    for name, upstream_sha in vendor_map_assets.PATCHED_MINIMAPS.items():
        committed = _sha12((STATIC / "img" / "maps" / f"{name}.png").read_bytes())
        assert committed != upstream_sha, f"{name}: the committed image is the upstream one; drop it from the list"
        assert tags[name]["image_sha"] == committed, name
