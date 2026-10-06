"""The raw utility fixtures (tests/fixtures/replays/utility): small cuts of real exports, with subjects
nulled, that the extraction tests read. These checks pin the shapes the extraction depends on, so a
fixture that lost its evidence fails here and not as a vacuous pass further on."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "replays" / "utility"
NAMES = ["deadlock_sensor_hook", "deadlock_wall_broken", "deadlock_wall_full", "kayo_knife_empty", "kayo_knife_hits",
         "kayo_ult", "omen_ult", "phoenix_blaze", "phoenix_curveball", "phoenix_ult", "sage_wall",
         "skye_flash_empty", "skye_flash_hits", "vyse_wall_untriggered", "waylay_recall"]
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / f"{name}.ndjson").read_text(encoding="utf-8").splitlines() if line]


def of_type(name: str, kind: str) -> list[dict]:
    return [r for r in rows(name) if r["type"] == kind]


def test_every_fixture_is_listed_small_and_in_time_order():
    assert sorted(p.stem for p in FIXTURES.glob("*.ndjson")) == NAMES
    for name in NAMES:
        data = rows(name)
        assert data, name
        assert (FIXTURES / f"{name}.ndjson").stat().st_size < 100_000, name
        times = [r["time_ms"] for r in data]
        assert times == sorted(times), name


@pytest.mark.parametrize("name", NAMES)
def test_fixtures_name_no_player(name):
    text = (FIXTURES / f"{name}.ndjson").read_text(encoding="utf-8")
    assert not UUID.search(text)
    for row in rows(name):
        assert all(value is None for key, value in row.items() if key.endswith("_subject"))


def test_a_curveball_has_both_coordinate_sources_and_they_differ_by_100():
    samples = of_type("phoenix_curveball", "valorant_flash_path_updated")
    spawn = next(s for s in samples if s["source"] == "spawn_transform")
    replicated = next(s for s in samples if s["source"] == "replicated_movement" and s["time_ms"] == spawn["time_ms"])
    for axis in "xyz":
        assert spawn["location"][axis] == pytest.approx(replicated["location"][axis] * 100, abs=1.0)
    exploded = of_type("phoenix_curveball", "valorant_flash_exploded")[0]
    assert exploded["evidence"] == "stop_projectile_rpc"
    assert abs(exploded["location"]["x"]) < 200          # still in metres: the unit follows the source


def test_skye_flashes_one_with_short_hits_one_with_none():
    hits = of_type("skye_flash_hits", "valorant_flash_player_hit")
    assert len(hits) == 3 and min(h["initial_duration_seconds"] for h in hits) < 0.1
    assert {h["correlation"] for h in hits} == {"causing_flash_source"}
    exploded = of_type("skye_flash_hits", "valorant_flash_exploded")[0]
    assert exploded["evidence"] == "skye_flash_source" and exploded["location"]["x"] > 1000   # centimetres
    assert of_type("skye_flash_empty", "valorant_flash_cast") and of_type("skye_flash_empty", "valorant_flash_exploded")
    assert not of_type("skye_flash_empty", "valorant_flash_player_hit")


def test_a_deadlock_wall_is_a_root_and_four_nodes_with_damage_rows():
    for name, parts_destroyed_early in (("deadlock_wall_full", False), ("deadlock_wall_broken", True)):
        spawns = of_type(name, "actor_spawned")
        kinds = sorted(s["archetype_path"] for s in spawns)
        assert kinds.count("Default__GameObject_CableJam_CableDeployer_Precomputed_C") == 4, name
        assert kinds.count("Default__GameObject_CableJamRoot_C") == 1, name
        root = next(s for s in spawns if s["archetype_path"] == "Default__GameObject_CableJamRoot_C")
        nodes = [s for s in spawns if "CableDeployer" in s["archetype_path"]]
        # every node spawns at the root: its real place is only in its damage rows
        assert all(n["location"]["x"] == root["location"]["x"] for n in nodes), name
        damaged = {r["actor_net_guid"] for r in rows(name) if r["type"] == "rpc_received"
                   and r["function_name"].startswith("MulticastNotifyDamage")
                   and isinstance(r["payload"].get("DamageOrigin"), dict)}
        assert {n["actor_net_guid"] for n in nodes} <= damaged, name
        closes = {r["actor_net_guid"]: r["time_ms"] for r in of_type(name, "actor_closed")}
        life = closes[root["actor_net_guid"]] - root["time_ms"]
        assert (life < 10_000) is parts_destroyed_early, name


def test_knives_one_that_hit_and_one_that_hit_nobody():
    def pulse_and_hits(name):
        data = rows(name)
        pulse = next(r for r in data if r["type"] == "actor_spawned" and "SuppressionPulse" in r["archetype_path"])
        fired = [r for r in data if r["type"] == "rpc_received" and r["actor_net_guid"] == pulse["actor_net_guid"]
                 and r["function_name"] == "MulticastPlayOneShotEffect"]
        named = [r for r in data if r["type"] == "rpc_received" and r["actor_net_guid"] != pulse["actor_net_guid"]
                 and any(v.get("Value") == pulse["actor_net_guid"] for v in r["payload"].get("FunctionObjectValues") or [])]
        return pulse, fired, named

    pulse, fired, named = pulse_and_hits("kayo_knife_hits")
    assert len(fired) == 1 and fired[0]["time_ms"] - pulse["time_ms"] == 1000
    assert fired[0]["payload"]["FunctionFloatValues"][0]["Value"] == 1500      # the suppress radius, 15 m
    assert len(named) == 5
    pulse, fired, named = pulse_and_hits("kayo_knife_empty")
    assert len(fired) == 1 and named == []


def test_walls_sage_segments_and_an_untriggered_shear():
    assert len(of_type("sage_wall", "valorant_wall_segment_spawned")) == 4
    assert {r["evidence"] for r in of_type("sage_wall", "valorant_wall_segment_destroyed")} == {"disable_collision_rpc"}
    placed = of_type("vyse_wall_untriggered", "valorant_wall_placed")[0]
    assert placed["wall_kind"] == "vyse_shear" and placed["wall_start"] and placed["wall_end"]
    assert of_type("vyse_wall_untriggered", "valorant_wall_destroyed")[0]["evidence"] == "vyse_unactivated_trap_destroyed"
    assert not of_type("vyse_wall_untriggered", "valorant_wall_activated")


def test_blaze_recall_and_ult_markers():
    blaze = [r for r in rows("phoenix_blaze") if r["type"] == "export_group_received"
             and "ReplicatedMovement" in (r.get("payload") or {})]
    assert len(blaze) >= 8
    assert all(abs(r["payload"]["ReplicatedMovement"]["location"]["x"]) < 200 for r in blaze)   # metres
    recall = sorted(s["archetype_path"] for s in of_type("waylay_recall", "actor_spawned"))
    assert recall == ["Default__GameObject_Terra_E_RewindTime_RewindTarget_C",
                      "Default__Projectile_Terra_E_RewindTime_ForObservers_C"]
    assert len({r["time_ms"] for r in of_type("waylay_recall", "actor_closed")}) == 1    # both end on arrival
    assert of_type("omen_ult", "actor_spawned")[0]["archetype_path"] == "Default__Intention_Wraith_X_GlobalTeleport_C"
    assert "ResTarget" in of_type("phoenix_ult", "actor_spawned")[0]["archetype_path"]


def test_the_inspector_reads_a_fixture_and_keeps_a_bounded_number_of_rows(tmp_path):
    from scripts.inspect_replay_utility import inspect

    actors = inspect(FIXTURES / "deadlock_wall_full.ndjson", re.compile("CableJam"), per_actor=3, t_from=None, t_to=None)
    by_kind = {}
    for actor in actors:
        by_kind.setdefault(actor["archetype"], []).append(actor)
    assert len(by_kind["GameObject_CableJamRoot"]) == 1
    assert len(by_kind["GameObject_CableJam_CableDeployer_Precomputed"]) == 4
    assert all(len(a["events"]) <= 3 for a in actors)                    # the cap
    assert any(sum(a["counts"].values()) > 3 for a in actors)            # the rest are counted, not kept
    assert all(a["closed_ms"] is not None for a in actors)
