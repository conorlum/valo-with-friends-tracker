"""The local preview's extras: ability objects (with their owners), the spike and shots."""

import json

import pytest

from app.replays.condense import MapInfo, PlayerTable
from app.replays.extras import TRACER_UNITS, build_extras, normalize_archetype

# u = y * 1e-4 + 0.5, v = x * 1e-4 + 0.5: world (0, 0) is the minimap's centre.
GAME_MAP = MapInfo("Testmap", "Test", 1e-4, 1e-4, 0.5, 0.5)
AGENTS = {"Wraith": "Omen", "Gumshoe": "Cypher", "Hunter": "Sova"}
# Two rounds: InRound at 10 s and 70 s; each playback window ends 50 s later.
WINDOWS = [(10_000, 50_000, 60_000), (70_000, 110_000, 120_000)]


def players() -> PlayerTable:
    # Slots 0 and 5 both play Omen; slot 1 is the only Cypher. Pawns 100+slot, player states 200+slot.
    agents = ["Omen", "Cypher", "Sova", "Jett", "Sage", "Omen", "Reyna", "Killjoy", "Breach", "Viper"]
    return PlayerTable(subjects=[None] * 10, agents=agents, pawn_slot={100 + s: s for s in range(10)},
                       other_slot={200 + s: s for s in range(10)}, pawn_changes={}, gone_ms={})


def spawned(t_ms, guid, archetype, x, y, yaw=0.0):
    return {"type": "actor_spawned", "time_ms": t_ms, "actor_net_guid": guid, "archetype_path": archetype,
            "location": {"x": x, "y": y, "z": 0}, "rotation": {"pitch": 0, "yaw": yaw, "roll": 0}}


def instigated(t_ms, guid, pawn):
    return {"type": "export_group_received", "time_ms": t_ms, "actor_net_guid": guid, "is_actor": True,
            "payload": {"Instigator": pawn, "Owner": 999}}


def closed(t_ms, guid):
    return {"type": "actor_closed", "time_ms": t_ms, "actor_net_guid": guid, "reason": "destroyed"}


def shot(t_ms, state, x, y, dx, dy, gun="Vandal"):
    return {"type": "valorant_shot_received", "time_ms": t_ms,
            "shot": {"firing_player_state": state, "location": {"x": x, "y": y, "z": 0},
                     "attack_vectors": [{"x": dx, "y": dy, "z": 0}], "equippable": {"name": gun}, "num_projectiles": 1}}


def run(tmp_path, rows, positions=None):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return build_extras(path, players(), WINDOWS, GAME_MAP, AGENTS, lambda t_ms: positions or {})


def test_the_instigator_names_the_owner_and_the_close_ends_it(tmp_path):
    extras = run(tmp_path, [spawned(20_000, 7, "Default__Zone_Wraith_4_Smoke_C", 1000, -2000),
                            instigated(20_000, 7, 105), closed(35_000, 7)])
    [smoke] = extras.rounds[1]["abilities"]
    assert (smoke["slot"], smoke["owner_by"], smoke["agent"]) == (5, "instigator", "Omen")
    assert (smoke["t0"], smoke["t1"]) == (10.0, 25.0)
    assert (smoke["u"], smoke["v"]) == (3000, 6000)


def test_a_lone_agent_owns_its_objects_and_equippables_are_skipped(tmp_path):
    extras = run(tmp_path, [spawned(20_000, 8, "Default__GameObject_Gumshoe_4_TripWire_C", 0, 0),
                            spawned(20_000, 9, "Default__Ability_Gumshoe_4_TripWire_C", 0, 0)])
    [wire] = extras.rounds[1]["abilities"]
    assert (wire["slot"], wire["owner_by"], wire["t1"]) == (1, "agent", None)


def test_two_players_of_one_agent_fall_back_to_the_nearest_with_its_evidence(tmp_path):
    positions = {0: (5000.0, 5000.0), 5: (100.0, 0.0)}
    extras = run(tmp_path, [spawned(20_000, 10, "Default__Projectile_Wraith_4_Smoke_C", 0, 0)], positions)
    [throw] = extras.rounds[1]["abilities"]
    assert (throw["slot"], throw["owner_by"], throw["owner_d"], throw["other_d"]) == (5, "nearest", 100, 7071)


def test_the_nearest_guess_is_scored_where_the_instigator_is_known(tmp_path):
    positions = {0: (5000.0, 5000.0), 5: (100.0, 0.0)}
    extras = run(tmp_path, [spawned(20_000, 11, "Default__Zone_Wraith_4_Smoke_C", 0, 0), instigated(20_000, 11, 100)],
                 positions)
    assert extras.rounds[1]["abilities"][0]["slot"] == 0
    assert extras.report["nearest_check_disagree"] == 1


def test_a_buy_phase_setup_belongs_to_the_next_round_from_its_start(tmp_path):
    extras = run(tmp_path, [spawned(65_000, 12, "Default__Pawn_Gumshoe_E_PossessableCamera_C", 0, 0),
                            spawned(64_000, 13, "Default__Projectile_Gumshoe_Q_CageTrap_C", 0, 0), closed(66_000, 13)])
    [camera] = extras.rounds[2]["abilities"]
    assert (camera["t0"], camera["name"]) == (0.0, "E_PossessableCamera")
    assert extras.report["abilities_buy_phase"] == 1 and extras.report["abilities_buy_phase_only"] == 1


def test_a_close_before_a_reused_guid_spawns_is_not_its_close(tmp_path):
    extras = run(tmp_path, [closed(15_000, 14), spawned(20_000, 14, "Default__GameObject_Hunter_Q_SonarPing_C", 0, 0)])
    assert extras.rounds[1]["abilities"][0]["t1"] is None


def test_the_spike_is_kept_with_its_planter(tmp_path):
    extras = run(tmp_path, [spawned(40_000, 15, "Default__TimedBomb_C", 0, 0), instigated(40_000, 15, 106),
                            closed(55_000, 15)])
    [spike] = extras.rounds[1]["abilities"]
    assert (spike["kind"], spike["slot"], spike["t0"], spike["t1"]) == ("Bomb", 6, 30.0, 45.0)


def test_shots_resolve_their_shooter_and_point_a_tracer_along_the_attack_vector(tmp_path):
    extras = run(tmp_path, [shot(12_000, 203, 0, 0, 0.0, 2.0), shot(13_000, None, 0, 0, 1, 0),
                            shot(5_000, 203, 0, 0, 1, 0), shot(14_000, 777, 0, 0, 1, 0)])
    [one] = extras.rounds[1]["shots"]
    assert (one["t"], one["slot"], one["gun"], one["u"], one["v"]) == (2.0, 3, "Vandal", 5000, 5000)
    assert (one["u1"], one["v1"]) == (5000 + round(TRACER_UNITS), 5000)   # +y world is +u
    assert extras.report["shots_without_shooter"] == 1
    assert extras.report["shots_outside_rounds"] == 1 and extras.report["shots_unresolved"] == 1


@pytest.mark.parametrize("archetype", ["Default__BasePistol_C", "Default__Ability_Melee_Base_C",
                                       "Default__Wraith_PC_C", "Default__EquippableGroundPickup_C"])
def test_other_actors_are_ignored(tmp_path, archetype):
    assert run(tmp_path, [spawned(20_000, 16, archetype, 0, 0)]).rounds == {}


def test_a_possessed_pawn_belongs_to_its_possessor(tmp_path):
    table = players()
    table.possession[20] = [(21_000, 22_000, 5)]
    path = tmp_path / "events.ndjson"
    path.write_text(json.dumps(spawned(20_000, 20, "Default__Pawn_Wraith_E_Thing_C", 0, 0)) + "\n", encoding="utf-8")
    [pawn] = build_extras(path, table, WINDOWS, GAME_MAP, AGENTS, lambda t: {}).rounds[1]["abilities"]
    assert (pawn["slot"], pawn["owner_by"]) == (5, "possessed")


def test_owners_pass_from_a_landed_throw_to_its_object_and_on_to_what_spawns_inside_it(tmp_path):
    positions = {0: (5000.0, 5000.0), 5: (10.0, 0.0)}
    extras = run(tmp_path, [
        spawned(15_000, 30, "Default__Projectile_Wraith_Q_Cage_C", 0, 0), closed(16_000, 30),  # thrown by slot 5
        spawned(16_100, 31, "Default__GameObject_Wraith_Q_Cage_C", 3000, 3000),                 # lands far from both
        spawned(30_000, 32, "Default__Zone_Wraith_Q_Cage_C", 3050, 3000),                       # activates inside it
        spawned(30_100, 33, "Default__GameObject_Wraith_Q_Cage_SecondAnchor_C", 9000, -9000),  # an unrelated kind
        spawned(40_000, 34, "Default__GameObject_Wraith_4_Wire_C", 3000, -3000),               # unclear, and...
        spawned(40_100, 35, "Default__GameObject_Wraith_4_Wire_C", 3500, -3000),               # its pair
    ], positions)
    got = {a["name"]: (a["slot"], a["owner_by"]) for a in extras.rounds[1]["abilities"]}
    assert got["Q_Cage"] == (5, "parent")
    assert [(a["kind"], a["owner_by"]) for a in extras.rounds[1]["abilities"][:2]] == [
        ("Projectile", "nearest"), ("GameObject", "landed")]
    assert got["Q_Cage_SecondAnchor"] == (None, None), "far from both Omens with no evidence: no owner"
    assert got["4_Wire"] == (None, None)


def test_a_landed_object_keeps_its_throw_and_a_drone_its_path(tmp_path):
    rows = [spawned(20_000, 40, "Default__Projectile_Hunter_Q_RevealBolt_C", 1000, 0), closed(21_000, 40),
            spawned(21_050, 41, "Default__GameObject_Hunter_Q_SonarBolt_C", 3000, 0),
            spawned(25_000, 42, "Default__Pawn_Hunter_E_Drone_C", 0, 0), closed(27_000, 42)]
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    drone = [(24_000, 0.0, 0.0), (25_000, 0.0, 0.0), (25_050, 10.0, 0.0), (25_100, 20.0, 0.0), (26_000, 900.0, 0.0),
             (28_000, 5000.0, 0.0)]
    extras = build_extras(path, players(), WINDOWS, GAME_MAP, AGENTS, lambda t: {},
                          lambda guid: drone if guid == 42 else [])
    bolt, drone_entry = [a for a in extras.rounds[1]["abilities"] if a["kind"] != "Projectile"]
    assert bolt["thrown"] == {"t0": 10.0, "t1": 11.0, "u": 5000, "v": 6000}
    # One point per 100 ms, inside the drone's own life (25-27 s) only.
    assert [p[0] for p in drone_entry["path"]] == [15.0, 15.1, 16.0]
    assert "thrown" not in drone_entry and extras.report["pawn_paths"] == 1


@pytest.mark.parametrize("code,name,expected", [
    ("E", "Aggrobot_DiscTurret_PowerWave", ("Aggrobot", "E_DiscTurret_PowerWave")),
    ("C", "Grenadier_Flash_Underhand", ("Grenadier", "C_Flash_Underhand")),
    ("Q", "BountyHunter_Tether_SphereExpansion", ("BountyHunter", "Q_Tether_SphereExpansion")),
    ("RemovableObject", "GumshoeTrackingDart", ("Gumshoe", "RemovableObject_GumshoeTrackingDart")),
    ("Wraith", "4_Smoke", ("Wraith", "4_Smoke")),              # the usual shape: unchanged
    ("E", "Nobody_Thing", ("E", "Nobody_Thing")),              # no known code: unchanged
    ("Infinity", "Rain_Instance", ("Infinity", "Rain_Instance")),
])
def test_archetypes_with_the_slot_letter_first_are_read_as_their_agents(code, name, expected):
    codes = {"Aggrobot": "Gekko", "Grenadier": "KAY/O", "BountyHunter": "Fade", "Gumshoe": "Cypher",
             "Wraith": "Omen"}
    assert normalize_archetype(code, name, codes) == expected


def test_a_slot_first_archetype_gets_its_agent_and_owner(tmp_path):
    extras = run(tmp_path, [spawned(20_000, 7, "Default__GameObject_RemovableObject_GumshoeTrackingDart_C", 0, 0),
                            spawned(20_000, 8, "Default__Projectile_E_Hunter_Drone_Thing_C", 0, 0)])
    dart, drone = extras.rounds[1]["abilities"]
    assert (dart["code"], dart["agent"], dart["slot"]) == ("Gumshoe", "Cypher", 1)
    assert (drone["code"], drone["name"], drone["agent"], drone["slot"]) == ("Hunter", "E_Drone_Thing", "Sova", 2)


# ---------------------------------------------------------------- stored util (Stage 2, R2)

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import SyntheticMatch  # noqa: E402

from app.replays import condense as cd  # noqa: E402
from app.replays import format as fmt  # noqa: E402
from app.replays.extras import rounds_extras, util_entries  # noqa: E402

UTIL_KEYS = {"k", "t", "by", "t1", "kind", "code", "name", "agent", "owner_by", "u", "v", "yaw", "thrown", "path",
             "owner_d", "other_d", "u1", "v1", "gun", "n"}


def test_util_entries_round_trip_to_the_viewers_shape():
    extras = {"abilities": [{"t0": 1.5, "t1": 9.0, "kind": "Zone", "code": "Wraith", "name": "4_Smoke",
                             "agent": "Omen", "slot": 6, "owner_by": "agent", "u": 10, "v": 20, "yaw": 90}],
              "shots": [{"t": 3.25, "slot": 2, "u": 1, "v": 2, "gun": "Vandal", "n": 1, "u1": 5, "v1": 6}]}
    util = util_entries(extras)
    assert [u["k"] for u in util] == ["ability", "shot"]
    assert util[0]["t"] == 1.5 and util[0]["by"] == 6 and "t0" not in util[0] and "slot" not in util[0]
    assert util[1]["t"] == 3.25 and util[1]["by"] == 2
    assert rounds_extras(util + [{"k": "flash", "t": 1, "by": 0}]) == extras


def test_condense_stores_ability_objects_in_util_deterministically(tmp_path):
    match = SyntheticMatch()
    start = match.round_start(1)
    ability = spawned(start + 5_000, 9_001, "Default__Zone_Wraith_4_Smoke_C", 0.0, 0.0)
    events = sorted(match.events() + [ability, closed(start + 20_000, 9_001)], key=lambda row: row["time_ms"])
    directory = match.write(tmp_path / "m", events=events)
    first = cd.condense_export_dir(directory, source_sha256=match.source_sha256)
    again = cd.condense_export_dir(directory, source_sha256=match.source_sha256)
    assert first.encoded_rounds() == again.encoded_rounds()
    stored = [u for u in first.rounds[1]["util"] if u["k"] == "ability"]
    assert len(stored) == 1 and stored[0]["t"] == 5.0 and stored[0]["t1"] == 20.0
    assert stored[0]["agent"] == "Omen" and stored[0]["by"] == 6 and stored[0]["owner_by"] == "agent"
    assert first.report["extras"]["abilities"] == 1
    assert first.report["sizes"]["total_bytes"] == sum(len(b) for b in first.encoded_rounds().values())
    for blob in first.rounds.values():
        assert all(set(u) <= UTIL_KEYS | {"ability", "targets"} for u in blob["util"])
        assert fmt.decode_blob(fmt.encode_blob(blob)) == blob
    plain = cd.condense_export_dir(directory, source_sha256=match.source_sha256, with_extras=False)
    assert not [u for u in plain.rounds[1]["util"] if u["k"] == "ability"]


def test_the_streaming_and_in_memory_paths_store_the_same_util(tmp_path):
    match = SyntheticMatch()
    start = match.round_start(2)
    ability = spawned(start + 2_000, 9_002, "Default__GameObject_Gumshoe_E_Trap_C", 100.0, 100.0)
    events = sorted(match.events() + [ability], key=lambda row: row["time_ms"])
    directory = match.write(tmp_path / "m", events=events)
    streamed = cd.condense_export_dir(directory, source_sha256=match.source_sha256)
    loaded = cd.condense_export_dir(directory, source_sha256=match.source_sha256, streaming=False)
    assert streamed.encoded_rounds() == loaded.encoded_rounds()
    assert [u["k"] for u in streamed.rounds[2]["util"]].count("ability") == 1
