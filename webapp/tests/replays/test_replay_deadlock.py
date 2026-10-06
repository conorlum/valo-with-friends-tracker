"""Deadlock's utility in the stored round (the 2026-10-05 review, items 24-25): her Barrier Mesh, Sonic
Sensors and Annihilation are spawned under names that aren't `<Kind>_<AgentCode>_<Name>`, so they were skipped
as "not an agent's". Read here from cuts of the real Summit export (tests/fixtures/replays/utility)."""

import base64
import json
from pathlib import Path

import pytest

from app.replays.condense import MapInfo, PlayerTable
from app.replays.extras import ARCHETYPE_ALIASES, build_extras, packed_vector

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "replays" / "utility"
GAME_MAP = MapInfo("Testmap", "Test", 1e-5, 1e-5, 0.5, 0.5)       # u = y * 1e-5 + 0.5: 10,000 units = 1,000 uv
AGENTS = {"Cable": "Deadlock", "Vampire": "Reyna", "BountyHunter": "Fade"}


def players() -> PlayerTable:
    # Slot 2 is the only Deadlock (pawn 830 in the Summit export).
    agents = ["Cypher", "Omen", "Deadlock", "Reyna", "Clove", "Sova", "Phoenix", "Fade", "Jett", "Reyna"]
    pawns = {830: 2, 1532: 3, 1332: 7}
    return PlayerTable(subjects=[None] * 10, agents=agents, pawn_slot=pawns, other_slot={}, pawn_changes={}, gone_ms={})


def extras_of(name: str, start_ms: int, end_ms: int, rows=None, tmp_path=None):
    path = FIXTURES / f"{name}.ndjson"
    if rows is not None:
        path = tmp_path / "events.ndjson"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return build_extras(path, players(), [(start_ms, end_ms, end_ms)], GAME_MAP, AGENTS, lambda t_ms: {})


def fixture_rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / f"{name}.ndjson").read_text(encoding="utf-8").splitlines() if line]


def test_a_packed_vector_decodes_to_the_roots_own_spawn_point():
    rows = fixture_rows("deadlock_wall_full")
    root = next(r for r in rows if r["type"] == "actor_spawned" and r["archetype_path"].endswith("CableJamRoot_C"))
    hit = next(r for r in rows if r["type"] == "rpc_received" and r["actor_net_guid"] == root["actor_net_guid"]
               and "DamageOrigin" in r["payload"])
    origin = hit["payload"]["DamageOrigin"]
    assert origin["BitCount"] == 70
    x, y, z = packed_vector(origin["BitCount"], base64.b64decode(origin["Data"]))
    assert (x, y, z) == pytest.approx((root["location"]["x"], root["location"]["y"], root["location"]["z"]), abs=0.05)


@pytest.mark.parametrize("bits, data", [(8, b"\x00"), (70, b"\x00" * 4), (71, b"\xff" * 9), (0, b"")])
def test_a_property_that_is_not_a_packed_vector_decodes_to_nothing(bits, data):
    assert packed_vector(bits, data) is None


def test_a_whole_mesh_is_one_row_with_its_four_arms_and_when_it_was_up():
    extras = extras_of("deadlock_wall_full", 240_000, 290_000)
    rows = extras.rounds[1]["abilities"]
    assert sorted(r["name"] for r in rows) == ["E_CableJam", "E_CableJam_Root"]       # the throw and the wall
    wall = next(r for r in rows if r["name"] == "E_CableJam_Root")
    assert (wall["code"], wall["agent"], wall["kind"], wall["slot"], wall["owner_by"]) == \
        ("Cable", "Deadlock", "GameObject", 2, "agent")
    assert (wall["t0"], wall["t1"]) == (8.408, 38.413)                               # 30.0 s
    assert wall["on"] == [[11.414, 38.413]]                                          # solid 3.0 s after it lands
    assert wall["thrown"]["t0"] == 7.598
    # the nodes' real places, from their damage rows (world x, y -> v, u): none is at the root
    assert [arm[:2] for arm in wall["arms"]] == [GAME_MAP.to_uv(x, y) and list(GAME_MAP.to_uv(x, y)) for x, y in
                                                 [(7151.8, 1276.4), (6094.69, 1633.5), (6300.4, 425.0), (7008.29, 719.9)]]
    assert all(arm[2] == 10 for arm in wall["arms"])                                 # z 100 units, in decimetres
    assert all(arm[3] is None for arm in wall["arms"])                               # every arm lasted the wall
    assert "arms_missing" not in wall
    assert extras.report["mesh_walls"] == 1 and extras.report["mesh_nodes"] == 4


def test_a_mesh_shot_down_ends_early():
    extras = extras_of("deadlock_wall_broken", 840_000, 870_000)
    wall = next(r for r in extras.rounds[1]["abilities"] if r["name"] == "E_CableJam_Root")
    assert wall["t1"] - wall["t0"] == pytest.approx(4.338, abs=0.001)
    assert wall["on"][0][0] - wall["t0"] == pytest.approx(3.0, abs=0.01)
    assert len(wall["arms"]) == 4


def test_an_arm_ends_when_its_node_goes_before_the_root(tmp_path):
    rows = fixture_rows("deadlock_wall_full")
    spawn = next(r for r in rows if r["type"] == "actor_spawned" and "CableDeployer" in r["archetype_path"])
    node = spawn["actor_net_guid"]
    for r in rows:
        if r["type"] == "actor_closed" and r["actor_net_guid"] == node:
            r["time_ms"] = spawn["time_ms"] + 12_000
    rows.sort(key=lambda r: r["time_ms"])       # its damage rows stay: they are where its place comes from
    wall = next(r for r in extras_of("x", 240_000, 290_000, rows, tmp_path).rounds[1]["abilities"]
                if r["name"] == "E_CableJam_Root")
    gone = [arm[3] for arm in wall["arms"]]
    assert sorted(g is None for g in gone) == [False, True, True, True]
    assert next(g for g in gone if g is not None) == pytest.approx(wall["t0"] + 12.0, abs=0.001)


def test_a_node_nothing_damaged_has_no_place_and_is_counted_not_guessed(tmp_path):
    rows = fixture_rows("deadlock_wall_full")
    node = next(r["actor_net_guid"] for r in rows if r["type"] == "actor_spawned" and "CableDeployer" in r["archetype_path"])
    rows = [r for r in rows if not (r["type"] == "rpc_received" and r["actor_net_guid"] == node
                                    and r["function_name"].startswith("MulticastNotifyDamage"))]
    extras = extras_of("x", 240_000, 290_000, rows, tmp_path)
    wall = next(r for r in extras.rounds[1]["abilities"] if r["name"] == "E_CableJam_Root")
    assert len(wall["arms"]) == 3 and wall["arms_missing"] == 1
    assert extras.report["mesh_arms_without_a_place"] == 1


def test_sensors_their_trigger_and_the_ult_are_rows_and_the_warnings_are_not():
    extras = extras_of("deadlock_sensor_hook", 840_000, 870_000)
    names = sorted(r["name"] for r in extras.rounds[1]["abilities"])
    assert names == ["Q_SoundSensor", "Q_SoundSensor_Fissure", "Q_SoundSensor_Fissure", "X_FishingHook",
                     "X_FishingHook_Cage"]
    by = {r["name"]: r for r in extras.rounds[1]["abilities"]}
    assert all(r["slot"] == 2 and r["agent"] == "Deadlock" for r in extras.rounds[1]["abilities"])
    assert by["Q_SoundSensor"]["t1"] - by["Q_SoundSensor"]["t0"] == pytest.approx(14.486, abs=0.001)
    assert by["X_FishingHook"]["t1"] - by["X_FishingHook"]["t0"] == pytest.approx(3.003, abs=0.001)
    assert "z" in by["Q_SoundSensor"] and "z" in by["X_FishingHook"]
    # the hook's two path warnings and its end marker: counted, never drawn (its spline and the cocoon's
    # `MotherNode` have no code part at all and were never read)
    assert extras.report["skipped_not_agent"] == {"FishingHook": 3}


def test_only_the_exact_archetypes_are_taken_in(tmp_path):
    lookalikes = ["Default__GameObject_CableJamRootDecor_C", "Default__GameObject_StealthingTrap_SoundSensor_Old_C",
                  "Default__Actor_FishingHookProp_C", "Default__GameObject_SoundSensor_Ambient_C"]
    rows = [{"type": "actor_spawned", "time_ms": 250_000 + i, "actor_net_guid": 50 + i, "archetype_path": name,
             "location": {"x": 0, "y": 0, "z": 0}, "rotation": None} for i, name in enumerate(lookalikes)]
    extras = extras_of("x", 240_000, 290_000, rows, tmp_path)
    assert extras.rounds == {}
    deadlocks = [alias for name, alias in ARCHETYPE_ALIASES.items() if "Wraith" not in name]
    assert len(deadlocks) == 7
    assert all(alias.startswith(("Default__GameObject_Cable_", "Default__Projectile_Cable_")) for alias in deadlocks)
