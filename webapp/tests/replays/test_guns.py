"""Portable gun identities, firearm tiers and the owner's range bands."""

import json
from pathlib import Path
import runpy
import shutil

import pytest

from app.replays import guns
from app.replays.extras import EQUIPPABLE_ARCHETYPE


@pytest.mark.parametrize("code,name,category", [
    ("AssaultRifle_ACR", "Phantom", "rifle"),
    ("AssaultRifle_AK", "Vandal", "rifle"),
    ("SubMachineGun_MP5", "Spectre", "smg"),
    ("BasePistol", "Classic", "sidearm"),
    ("Vector", "Stinger", "smg"),
    ("HeavyMachineGun", "Odin", "machine_gun"),
    ("LugerPistol", "Ghost", "sidearm"),
    ("RevolverPistol", "Sheriff", "sidearm"),
    ("AssaultRifle_Burst", "Bulldog", "rifle"),
    ("CompactPistol", "Bandit", "sidearm"),
    ("DMR", "Guardian", "rifle"),
    ("AutomaticPistol", "Frenzy", "sidearm"),
    ("AutomaticShotgun", "Judge", "shotgun"),
    ("LightMachineGun", "Ares", "machine_gun"),
    ("BattleRifle", "Warden", "rifle"),
    ("LeverSniperRifle", "Marshal", "sniper_rifle"),
    ("PumpShotgun", "Bucky", "shotgun"),
    ("Gun_Sprinter_X_HeavyLightningGun_Production", "Overdrive", "ability"),
    ("SawedOffShotgun", "Shorty", "shotgun"),
])
def test_measured_archetypes(code, name, category):
    assert guns.lookup(f"Default__{code}_C") == (name, category)


def test_lookup_fallback_and_table_precedence():
    learned = {
        "Default__New_C": ("Outlaw", "sniper_rifle"),
        "Default__AssaultRifle_AK_C": ("Classic", "sidearm"),
    }
    assert guns.lookup("Default__New_C", learned) == ("Outlaw", "sniper_rifle")
    assert guns.lookup("Default__AssaultRifle_AK_C", learned) == ("Vandal", "rifle")
    assert guns.lookup("Default__New_C") == (None, None)
    assert guns.lookup("Default__Other_C", learned) == (None, None)
    assert guns.lookup(None) == (None, None)


def test_firearm_tiers_exclude_abilities_and_melee():
    assert guns.tier("sidearm") == 0
    assert guns.tier("smg") == guns.tier("shotgun") == 1
    assert guns.tier("rifle") == guns.tier("machine_gun") == guns.tier("sniper_rifle") == 2
    assert guns.tier("sidearm") < guns.tier("smg") < guns.tier("rifle")
    for category in ("ability", "melee", "unknown", None):
        assert guns.tier(category) is None


@pytest.mark.parametrize("name,limit", [
    ("Bucky", 7), ("Judge", 7), ("Shorty", 7), ("Stinger", 10),
    ("Frenzy", 10), ("Classic", 10), ("Spectre", 15),
])
def test_close_range_guns(name, limit):
    assert guns.is_close_range(name)
    assert guns.effective_m(name) == limit
    assert guns.range_verdict(name, limit) == "in"
    assert guns.range_verdict(name, limit + 0.1) == "beyond"


@pytest.mark.parametrize("name", ["Vandal", "Ghost", None])
def test_guns_without_range_limits(name):
    assert not guns.is_close_range(name)
    assert guns.effective_m(name) is None


@pytest.mark.parametrize("name,metres,expected", [
    ("Bucky", 6.9, "in"), ("Bucky", 7.1, "beyond"), ("Spectre", 15, "in"),
    ("Vandal", 60, "in"), (None, 5, None), ("Stinger", None, None),
])
def test_range_verdict(name, metres, expected):
    assert guns.range_verdict(name, metres) == expected


def test_short_range_threshold_and_falloff_note():
    assert guns.SHORT_RANGE_M == 10
    ranges = json.loads((Path(guns.__file__).parent / "gun_ranges.json").read_text(encoding="utf-8"))
    assert ranges["falloff_note"] == "shotguns fall off from 5 m (owner, 2026-11-14)"


@pytest.mark.parametrize("archetype", [
    "Default__Ability_Melee_Knife_C", "Default__Ability_Melee_Test_Skin_C",
    "Default__Ability_Melee__C", "Default__Ability_Melee_C", "Default__Ability_NotMelee_Knife_C",
    "Default__Equippable_Unarmed_C", "Default__AssaultRifle_AK_C", "Default__New_C", None,
])
def test_melee_matches_equippable_code_without_importing_extras(archetype):
    match = EQUIPPABLE_ARCHETYPE.match(archetype) if archetype else None
    expected = archetype == "Default__Equippable_Unarmed_C" or bool(match and match[1] == "Melee")
    assert guns.is_melee(archetype) is expected


def test_every_archetype_has_a_valid_category():
    table = json.loads((Path(guns.__file__).parent / "guns.json").read_text(encoding="utf-8"))
    assert table["archetypes"]
    for archetype, row in table["archetypes"].items():
        assert row["category"] in {*table["tiers"], "ability"}, archetype
    assert "ability" not in table["tiers"] and "melee" not in table["tiers"]


def test_tables_ship_with_worker_and_load_beside_module(tmp_path, monkeypatch):
    module_path = Path(guns.__file__)
    root = Path(__file__).resolve().parents[3]
    dockerfile = (root / "replay_worker" / "Dockerfile").read_text(encoding="utf-8")
    assert any(line.split()[:2] == ["COPY", "webapp/app/replays"] for line in dockerfile.splitlines())
    source = module_path.read_text(encoding="utf-8")
    assert "Path(__file__).parent" in source
    assert "app/data" not in source and "extras" not in source
    for filename in ("guns.py", "guns.json", "gun_ranges.json"):
        shutil.copyfile(module_path.parent / filename, tmp_path / filename)
    monkeypatch.chdir(tmp_path.parent)
    portable = runpy.run_path(str(tmp_path / "guns.py"))
    assert portable["lookup"]("Default__AssaultRifle_AK_C") == ("Vandal", "rifle")
    assert portable["range_verdict"]("Bucky", 7.1) == "beyond"
