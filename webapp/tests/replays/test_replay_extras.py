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


def spawned(t_ms, guid, archetype, x, y, yaw=0.0, z=0):
    """`z=None` leaves the height out of the row, as an export without one would."""
    location = {"x": x, "y": y} if z is None else {"x": x, "y": y, "z": z}
    return {"type": "actor_spawned", "time_ms": t_ms, "actor_net_guid": guid, "archetype_path": archetype,
            "location": location, "rotation": {"pitch": 0, "yaw": yaw, "roll": 0}}


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
def test_spawns_without_a_switch_write_no_rows(tmp_path, archetype):
    assert run(tmp_path, [spawned(20_000, 16, archetype, 0, 0)]).rounds == {}


def test_a_possessed_pawn_belongs_to_its_possessor(tmp_path):
    table = players()
    table.possession[20] = [(21_000, 22_000, 5)]
    path = tmp_path / "events.ndjson"
    path.write_text(json.dumps(spawned(20_000, 20, "Default__Pawn_Wraith_E_Thing_C", 0, 0)) + "\n", encoding="utf-8")
    [pawn] = build_extras(path, table, WINDOWS, GAME_MAP, AGENTS, lambda t: {}).rounds[1]["abilities"]
    assert (pawn["slot"], pawn["owner_by"]) == (5, "possessed")


def test_a_dart_fired_from_a_spycam_is_the_cameras_owners_and_so_is_where_it_lands(tmp_path):
    table = players()
    table.agents[6] = "Cypher"                      # two Cyphers: no lone-agent owner
    table.possession[20] = [(21_000, None, 6)]
    rows = [spawned(20_000, 20, "Default__Pawn_Gumshoe_E_PossessableCamera_C", 0, 0),
            spawned(25_000, 21, "Default__Projectile_Gumshoe_E_CameraTrackingDart_C", 50, 50), closed(25_100, 21),
            spawned(25_100, 22, "Default__GameObject_RemovableObject_GumshoeTrackingDart_C", 900, 900)]
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    got = [(a["name"], a["slot"], a["owner_by"])
           for a in build_extras(path, table, WINDOWS, GAME_MAP, AGENTS, lambda t: {}).rounds[1]["abilities"]]
    assert got == [("E_PossessableCamera", 6, "possessed"), ("E_CameraTrackingDart", 6, "fired"),
                   ("RemovableObject_GumshoeTrackingDart", 6, "landed")]


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
    assert bolt["thrown"] == {"t0": 10.0, "t1": 11.0, "u": 5000, "v": 6000, "z": 0}
    # One point per 100 ms, inside the drone's own life (25-27 s) only.
    assert [p[0] for p in drone_entry["path"]] == [15.0, 15.1, 16.0]
    assert "thrown" not in drone_entry and extras.report["pawn_paths"] == 1


def test_heights_survive_on_the_spawn_the_throw_the_drone_path_and_both_trip_anchors(tmp_path):
    # Revision 11 (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 2): decimetres of
    # world z, as the export gave it.
    rows = [spawned(20_000, 40, "Default__Projectile_Hunter_Q_RevealBolt_C", 1000, 0, z=512.0), closed(21_000, 40),
            spawned(21_050, 41, "Default__GameObject_Hunter_Q_SonarBolt_C", 3000, 0, z=-96.0),
            spawned(25_000, 42, "Default__Pawn_Hunter_E_Drone_C", 0, 0, z=130.0), closed(27_000, 42),
            spawned(1_000, 500, "Default__Ability_Gumshoe_4_TripWire_C", 0, 0),
            effect(2_000, 101, 1, [500]),
            spawned(8_000, 700, "Default__GameObject_Gumshoe_4_TripWire_C", 1000, 0, z=40.0),
            spawned(8_000, 701, "Default__GameObject_Gumshoe_4_TripWire_SecondWire_C", 1000, 400, z=245.0),
            placement(8_000, 500, [701])]
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    drone = [(25_000, 0.0, 0.0, 130.0), (25_100, 20.0, 0.0, 134.0), (25_200, 30.0, 0.0, None), (26_000, 900.0, 0.0, 251.0)]
    extras = build_extras(path, players(), WINDOWS, GAME_MAP, AGENTS, lambda t: {},
                          lambda guid: drone if guid == 42 else [])
    by_name = {a["name"]: a for a in extras.rounds[1]["abilities"]}
    bolt = by_name["Q_SonarBolt"]
    assert bolt["z"] == -10 and bolt["thrown"]["z"] == 51, "the landing point's height, and the throw's own"
    assert by_name["E_Drone"]["z"] == 13
    assert by_name["E_Drone"]["path"] == [[15.0, 5000, 5000, 13], [15.1, 5000, 5020, 13], [15.2, 5000, 5030],
                                          [16.0, 5000, 5900, 25]], "a point without a height keeps three values"
    wire = by_name["4_TripWire"]
    assert (wire["z"], wire["end"], wire["end_z"]) == (4, [5400, 6000], 24)


def test_an_actor_without_a_height_stores_none_never_zero(tmp_path):
    rows = [spawned(20_000, 40, "Default__Projectile_Hunter_Q_RevealBolt_C", 1000, 0, z=None), closed(21_000, 40),
            spawned(21_050, 41, "Default__GameObject_Hunter_Q_SonarBolt_C", 3000, 0, z=None),
            spawned(25_000, 42, "Default__Pawn_Hunter_E_Drone_C", 0, 0, z=None), closed(27_000, 42),
            spawned(1_000, 500, "Default__Ability_Gumshoe_4_TripWire_C", 0, 0),
            effect(2_000, 101, 1, [500]),
            spawned(8_000, 700, "Default__GameObject_Gumshoe_4_TripWire_C", 1000, 0, z=None),
            spawned(8_000, 701, "Default__GameObject_Gumshoe_4_TripWire_SecondWire_C", 1000, 400, z=None),
            placement(8_000, 500, [701])]
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    drone = [(25_000, 0.0, 0.0), (25_100, 20.0, 0.0), (26_000, 900.0, 0.0)]     # the old three-value shape
    extras = build_extras(path, players(), WINDOWS, GAME_MAP, AGENTS, lambda t: {},
                          lambda guid: drone if guid == 42 else [])
    for entry in extras.rounds[1]["abilities"]:
        assert "z" not in entry and "end_z" not in entry and "z" not in entry.get("thrown", {})
        assert all(len(point) == 3 for point in entry.get("path", []))


def test_a_pawn_carries_when_it_was_possessed_and_its_facing_over_time(tmp_path):
    # Map control's inputs (revision 10): a camera in use only while possessed, facing its yaw.
    rows = [spawned(12_000, 42, "Default__Pawn_Gumshoe_E_PossessableCamera_C", 0, 0, yaw=90.0)]
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    table = players()
    table.possession[42] = [(5_000, 9_000, 1), (20_000, 23_000, 1), (40_000, None, 1)]
    # World yaw 90 is +y, which is +u on this map: minimap 0. It turns to world 0 (+v, minimap 90).
    yaws = [(19_000, 90.0), (20_000, 90.0), (20_050, 60.0), (20_200, 89.5), (21_000, 0.0), (21_100, 1.0)]
    extras = build_extras(path, table, WINDOWS, GAME_MAP, AGENTS, lambda t: {}, lambda guid: [],
                          pawn_yaws=lambda guid: yaws if guid == 42 else [])
    [camera] = extras.rounds[1]["abilities"]
    # The buy-phase possession (5-9 s) ended before the round; the last one is open-ended.
    assert camera["possessed"] == [[10.0, 13.0], [30.0, None]]
    # A point when it turned YAW_STEP_DEG or more (20.0 s and 21.1 s didn't), at most one per PATH_STEP_MS.
    assert camera["yaws"] == [[9.0, 0], [10.05, 30], [10.2, 0], [11.0, 90]]
    assert extras.report["pawns_possessed"] == 1


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


# ---------------------------------------------------------------- Stage 5: equippables, reveals, spike, wall

import base64  # noqa: E402

from app.replays.extras import packed_ints  # noqa: E402

AGENTS5 = {**AGENTS, "BountyHunter": "Fade", "Pandemic": "Viper"}


def two_cyphers() -> PlayerTable:
    # Slots 1 and 6 both play Cypher (one per team); slot 2 is Sova, slot 7 Fade, slot 9 Viper.
    table = players()
    table.agents[6] = "Cypher"
    table.agents[7] = "Fade"
    return table


def effect(t_ms, actor, effect_id, context, container=500):
    return {"type": "rpc_received", "time_ms": t_ms, "actor_net_guid": actor, "function_name": "MulticastPlayContinuousEffect",
            "payload": {"EffectId": effect_id, "EffectContainer": container,
                        "FunctionObjectValues": [{"Name": {"TagName": "FXC.EffectContext"}, "Value": v} for v in context]}}


def stop(t_ms, actor, effect_id):
    return {"type": "rpc_received", "time_ms": t_ms, "actor_net_guid": actor, "function_name": "MulticastStopContinuousEffect",
            "payload": {"EffectId": effect_id}}


def packed(values):
    out = bytearray(b"\x02\x02\x06")
    for value in values:
        while True:
            more = value >= 0x80
            out.append(((value & 0x7F) << 1) | int(more))
            value >>= 7
            if not more:
                break
    return base64.b64encode(bytes(out)).decode()


def placement(t_ms, equip, listed):
    return {"type": "export_group_received", "time_ms": t_ms, "actor_net_guid": equip, "is_actor": False,
            "payload": {"Actors": {"BitCount": 64, "Data": packed([16, *listed]), "TypeName": "Actors"}}}


def run5(tmp_path, rows, table=None, positions=None):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return build_extras(path, table or two_cyphers(), WINDOWS, GAME_MAP, AGENTS_FL if table is not None else AGENTS5, lambda t_ms: positions or {})


def test_packed_ints_reads_unreal_packed_values():
    # The Ascent export's first trapwire placement: its list names the second anchor, GUID 3512.
    assert packed_ints(base64.b64decode("AgIGIHE2AAA=")) == [16, 3512, 0, 0]
    assert packed_ints(base64.b64decode(packed([16, 5, 300, 70000]))) == [16, 5, 300, 70000]


def test_trapwires_take_their_equippables_owner_and_pair_exactly(tmp_path):
    # Two Cyphers' trapwire equippables (500, 600); each owner's character plays an effect naming theirs.
    # Both place trips in the buy phase within a second of each other: no nearest guess, no time pairing.
    rows = [spawned(1_000, 500, "Default__Ability_Gumshoe_4_TripWire_C", 0, 0),
            spawned(1_000, 600, "Default__Ability_Gumshoe_4_TripWire_C", 9000, 9000),
            effect(2_000, 101, 1, [500]), effect(2_100, 106, 2, [600]), effect(2_200, 106, 3, [500, 999]),
            effect(2_300, 101, 4, [500]),
            spawned(8_000, 700, "Default__GameObject_Gumshoe_4_TripWire_C", 1000, 0),
            spawned(8_000, 701, "Default__GameObject_Gumshoe_4_TripWire_SecondWire_C", 1000, 400),
            placement(8_000, 500, [701]),
            spawned(8_400, 710, "Default__GameObject_Gumshoe_4_TripWire_C", 1200, 0),
            spawned(8_400, 711, "Default__GameObject_Gumshoe_4_TripWire_SecondWire_C", 1200, -900),
            placement(8_400, 600, [711])]
    extras = run5(tmp_path, rows, positions={1: (0.0, 0.0), 6: (0.0, 50.0)})
    got = {(a["name"], a["u"], a["v"]): a for a in extras.rounds[1]["abilities"]}
    first = got[("4_TripWire", 5000, 6000)]
    second = got[("4_TripWire", 5000, 6200)]
    assert (first["slot"], first["owner_by"], first["end"]) == (1, "equippable", [5400, 6000])
    assert (second["slot"], second["owner_by"], second["end"]) == (6, "equippable", [4100, 6200])
    assert {a["slot"] for a in extras.rounds[1]["abilities"] if a["name"].endswith("SecondWire")} == {1, 6}
    assert extras.report["equippables_effects"] == 2 and extras.report["wires_paired"] == 2


def test_an_unvoted_equippable_takes_its_siblings_owner_and_a_knife_is_nobodys(tmp_path):
    rows = [spawned(1_000, 500, "Default__Ability_Gumshoe_4_TripWire_C", 0, 0),
            spawned(1_000, 501, "Default__Ability_Gumshoe_Q_CageTrap_C", 20, 0),     # given with it
            spawned(1_000, 502, "Default__Ability_Melee_Knife_C", 0, 0),
            effect(2_000, 106, 1, [500]), effect(2_000, 106, 2, [502]),
            spawned(9_000, 800, "Default__Projectile_Gumshoe_Q_CageTrap_C", 500, 500), placement(9_000, 501, [800])]
    extras = run5(tmp_path, rows)
    [cage] = extras.rounds[1]["abilities"]
    assert (cage["slot"], cage["owner_by"]) == (6, "equippable")
    assert extras.report["equippables_sibling"] == 1 and extras.report["equippables_unowned"] == 1


def test_the_instigator_still_wins_and_is_checked_against_the_equippable(tmp_path):
    rows = [spawned(1_000, 500, "Default__Ability_Gumshoe_4_TripWire_C", 0, 0), effect(2_000, 101, 1, [500]),
            spawned(8_000, 700, "Default__GameObject_Gumshoe_4_TripWire_C", 0, 0), instigated(8_000, 700, 106),
            placement(8_000, 500, [])]
    [wire] = run5(tmp_path, rows).rounds[1]["abilities"]
    assert (wire["slot"], wire["owner_by"]) == (6, "instigator")
    rows[-1] = placement(8_000, 500, [700])
    extras = run5(tmp_path, rows)
    assert extras.report["equippable_check_disagree"] == 1


def test_a_reveal_is_an_effect_on_a_revealed_character_naming_the_revealer_after_a_reveal_source(tmp_path):
    ping = "Default__GameObject_Hunter_Q_SonarPing_C"
    rows = [spawned(20_000, 50, ping, 0, 0), effect(20_800, 108, 1, [102], container=77), stop(22_400, 108, 1),
            effect(20_900, 104, 2, [102], container=77),                                # no stop: 2 s
            spawned(40_000, 51, ping, 0, 0), effect(41_000, 108, 3, [102], container=77),
            effect(41_100, 108, 4, [102], container=77), stop(41_200, 108, 4),       # a second pulse: its own
            effect(30_000, 109, 5, [102], container=88),                              # no ping before it
            effect(20_950, 105, 6, [102], container=99)]                              # its container is mostly
    rows += [effect(10_000 + i, 105, 10 + i, [102], container=99) for i in range(5)]  # elsewhere: not a reveal
    extras = run5(tmp_path, sorted(rows, key=lambda r: r["time_ms"]))
    reveals = extras.rounds[1]["reveals"]
    assert [(r["t0"], r["t1"], r["slot"], r["target"]) for r in reveals] == [
        (10.8, 12.4, 2, 8), (10.9, 12.9, 2, 4), (31.0, 33.0, 2, 8), (31.1, 32.1, 2, 8)]
    assert reveals[0]["code"] == "Hunter" and reveals[0]["name"] == "Q_SonarPing"
    assert extras.report["reveal_containers"] == 1


def test_defuse_attempts_come_from_the_spikes_effects(tmp_path):
    rows = [spawned(30_000, 15, "Default__TimedBomb_C", 0, 0), instigated(30_000, 15, 106),
            effect(35_000, 15, 1, [103]), stop(38_800, 15, 1),                        # held past half: halved
            effect(40_000, 15, 2, [104]), stop(43_500, 15, 2),
            {"type": "rpc_received", "time_ms": 43_500, "actor_net_guid": 15, "function_name": "MulticastPlayOneShotEffect",
             "payload": {"EffectContainer": 3}},
            closed(50_000, 15)]
    [spike] = run5(tmp_path, rows).rounds[1]["abilities"]
    assert spike["defuses"] == [[25.0, 28.8, 3, False], [30.0, 33.5, 4, True]]


def test_vipers_wall_keeps_its_line_and_when_it_was_up(tmp_path):
    wall = 60

    def point(t_ms, x, y):
        return {"type": "rpc_received", "time_ms": t_ms, "actor_net_guid": wall, "function_name": "MulticastAddSmokeScreenPoint",
                "payload": {"Translation": {"x": x, "y": y, "z": 0}}}

    def up(t_ms, value):
        return {"type": "export_group_received", "time_ms": t_ms, "actor_net_guid": wall, "is_actor": True,
                "payload": {"WallActivated": value, "CurrentFuelLevel": 0.5}}

    rows = [spawned(5_000, wall, "Default__GameObject_Pandemic_E_SmokeScreenManager_C", 0, 0), instigated(5_000, wall, 109),
            *[point(5_000 + 40 * i, 10.0 * i, 0.0) for i in range(60)],
            up(15_000, True), up(22_000, False), up(30_000, True), closed(58_000, wall)]
    [screen] = run5(tmp_path, rows).rounds[1]["abilities"]
    assert (screen["slot"], screen["t0"]) == (9, 0.0)
    assert len(screen["points"]) == 48 and screen["points"][0] == [5000, 5000] and screen["points"][-1] == [5000, 5590]
    assert screen["on"] == [[5.0, 12.0], [20.0, None]]
    # The first points missing: the line starts at the cast point.
    rows[2:22] = []
    [screen] = run5(tmp_path, rows).rounds[1]["abilities"]
    assert screen["points"][:2] == [[5000, 5000], [5000, 5200]] and len(screen["points"]) == 41


# ---------------------------------------------------------------- owners by chain and cause, reveal pings

AGENTS6 = {**AGENTS5, "Deadeye": "Chamber"}


def table6() -> PlayerTable:
    # Slots 2 and 8 both play Sova, 3 and 9 both Chamber, 1 and 6 both Cypher.
    table = players()
    table.agents[6], table.agents[8], table.agents[3], table.agents[9] = "Cypher", "Sova", "Chamber", "Chamber"
    return table


def run6(tmp_path, rows, positions=None, teams=None):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["time_ms"])), encoding="utf-8")
    return build_extras(path, table6(), WINDOWS, GAME_MAP, AGENTS6, lambda t_ms: positions or {}, teams=teams)


def oneshot(t_ms, actor, context, container):
    return {"type": "rpc_received", "time_ms": t_ms, "actor_net_guid": actor, "function_name": "MulticastPlayOneShotEffect",
            "payload": {"EffectContainer": container,
                        "FunctionObjectValues": [{"Name": {"TagName": "FXC.EffectContext"}, "Value": v} for v in context]}}


def test_two_sovas_droning_at_once_each_own_their_darts_through_the_chain(tmp_path):
    # Sova -> deploy-drone equippable -> drone -> the drone's own equippable -> dart. No possession
    # is decoded and the dart lands on the other Sova, so no guess could get this right.
    rows = []
    for sova, pawn, deploy, drone, kit in ((2, 102, 500, 600, 700), (8, 108, 510, 610, 710)):
        rows += [spawned(1_000, deploy, "Default__Ability_Hunter_E_DeployDrone_C", 0, 0), effect(2_000, pawn, sova, [deploy]),
                 spawned(20_000, drone, f"Default__Pawn_Hunter_E_Drone_C", 100 * sova, 0), placement(20_000, deploy, [drone]),
                 spawned(20_000, kit, "Default__Ability_Hunter_E_Drone_Abilities_C", 100 * sova, 0),
                 placement(20_050, kit, [drone])]
    rows += [spawned(24_000, 800, "Default__GameObject_Hunter_E_Drone_RevealDart_C", 200, 0), placement(24_000, 710, [102])]
    extras = run6(tmp_path, rows, positions={2: (200.0, 0.0), 8: (5000.0, 0.0)})
    got = {a["name"]: (a["slot"], a["owner_by"]) for a in extras.rounds[1]["abilities"] if a["name"] == "E_Drone_RevealDart"}
    assert got == {"E_Drone_RevealDart": (8, "equippable")}
    assert extras.report["equippables_lists"] == 2
    # The hit itself is a reveal: Sova 8's dart tagged Sova 2 at 24 s.
    assert [(r["t0"], r["t1"], r["slot"], r["target"]) for r in extras.rounds[1]["reveals"]] == [(14.0, 15.0, 8, 2)]


def test_dart_and_neural_theft_pings_are_reveals_at_their_own_moments(tmp_path):
    rows = [  # A drone dart's pings name the shooter's player state (208), 1.6 s and 2.8 s after the hit.
        spawned(20_000, 60, "Default__GameObject_Hunter_E_Drone_RevealDart_C", 0, 0),
        oneshot(21_600, 104, [208], 31), oneshot(22_800, 104, [208], 31),
        # Neural Theft's pings name Cypher 6's ult equippable, at +3 s and +7 s.
        spawned(1_000, 900, "Default__Ability_Gumshoe_X_InterrogateV2_C", 0, 0), effect(2_000, 106, 1, [900]),
        spawned(40_000, 61, "Default__GameObject_Gumshoe_X_InterrogateHat_C", 0, 0),
        oneshot(43_000, 101, [900], 32), oneshot(43_000, 105, [900], 32),
        oneshot(47_000, 101, [900], 32), oneshot(47_000, 105, [900], 32)]
    reveals = run6(tmp_path, rows).rounds[1]["reveals"]
    assert [(r["t0"], r["t1"], r["slot"], r["target"], r["name"]) for r in reveals] == [
        (11.6, 12.6, 8, 4, "E_Drone_RevealDart"), (12.8, 13.8, 8, 4, "E_Drone_RevealDart"),
        (33.0, 34.0, 6, 1, "X_InterrogateHat"), (33.0, 34.0, 6, 5, "X_InterrogateHat"),
        (37.0, 38.0, 6, 1, "X_InterrogateHat"), (37.0, 38.0, 6, 5, "X_InterrogateHat")]


def test_a_reveal_counts_only_the_revealers_enemies(tmp_path):
    # Neural Theft's ping reaches Cypher 6's enemies (team A: 1, 5); its "ult active" effect, also
    # naming the ult, plays on everyone, teammate 7 (team B, like 6) included.
    teams = {1: "A", 5: "A", 6: "B", 7: "B"}
    rows = [spawned(1_000, 900, "Default__Ability_Gumshoe_X_InterrogateV2_C", 0, 0), effect(2_000, 106, 1, [900]),
            spawned(40_000, 61, "Default__GameObject_Gumshoe_X_InterrogateHat_C", 0, 0),
            oneshot(43_000, 101, [900], 32), oneshot(43_000, 105, [900], 32), oneshot(43_000, 107, [900], 32),
            oneshot(47_000, 101, [900], 32), oneshot(47_000, 105, [900], 32), oneshot(47_000, 107, [900], 32)]
    assert {r["target"] for r in run6(tmp_path, rows, teams=teams).rounds[1]["reveals"]} == {1, 5}
    assert {r["target"] for r in run6(tmp_path, rows).rounds[1]["reveals"]} == {1, 5, 7}, "no teams: no filter"


def test_a_chamber_slow_is_its_traps_or_its_ult_kills(tmp_path):
    kill = {"type": "rpc_received", "time_ms": 40_000, "actor_net_guid": 101, "function_name": "MulticastNotifyDamage_Point",
            "payload": {"DamageKilledTarget": True, "EventInstigatorPawn": 109, "Character": 101}}
    rows = [spawned(12_000, 70, "Default__GameObject_Deadeye_E_Trap_C", 0, 0), instigated(12_000, 70, 103),
            spawned(12_000, 71, "Default__GameObject_Deadeye_E_Trap_C", 3000, 0), instigated(12_000, 71, 109),
            effect(30_000, 70, 1, [], container=1), {**effect(30_000, 70, 1, [5]), "payload": {"EffectId": 1}},
            spawned(30_900, 75, "Default__Projectile_Deadeye_4_Trap_Dart_C", 150, 0),  # the dart trap 70 shot
            spawned(30_920, 72, "Default__Patch_Deadeye_E_Slow_Large_C", 900, 0),   # 0.92 s after trap 70 fired
            kill, spawned(40_000, 73, "Default__Patch_Deadeye_E_Slow_Large_C", 5000, 5000),  # the ult's kill
            spawned(50_000, 74, "Default__Patch_Deadeye_E_Slow_Large_C", 200, 0),   # neither: no owner, no guess
            spawned(55_000, 76, "Default__Projectile_Deadeye_4_Trap_Dart_C", 9000, 0)]  # no trap fired: no guess
    abilities = run6(tmp_path, rows, positions={3: (200.0, 0.0), 9: (9000.0, 0.0)}).rounds[1]["abilities"]
    got = [(a["slot"], a["owner_by"]) for a in abilities if a["name"] == "E_Slow_Large"]
    assert got == [(3, "triggered"), (9, "ult_kill"), (None, None)]
    darts = [(a["slot"], a["owner_by"]) for a in abilities if a["name"] == "4_Trap_Dart"]
    assert darts == [(3, "triggered"), (None, None)], "not the nearest Chamber (9 stands on the second dart)"


def test_the_planter_is_whoever_started_planting_four_seconds_before(tmp_path):
    rows = [spawned(20_000, 80, "Default__BombEquippable_C", 0, 0),
            effect(36_000, 104, 1, [80]), spawned(40_000, 81, "Default__TimedBomb_C", 0, 0)]
    [spike] = run6(tmp_path, rows).rounds[1]["abilities"]
    assert (spike["slot"], spike["owner_by"]) == (4, "planted")


def test_only_a_throw_gets_the_nearest_guess(tmp_path):
    rows = [spawned(20_000, 90, "Default__GameObject_Hunter_E_Drone_RevealDart_C", 0, 0),
            spawned(20_000, 91, "Default__Projectile_Hunter_Q_RevealBolt_C", 0, 0)]
    got = {a["kind"]: (a["slot"], a["owner_by"]) for a in
           run6(tmp_path, rows, positions={2: (10.0, 0.0), 8: (5000.0, 0.0)}).rounds[1]["abilities"]}
    assert got == {"GameObject": (None, None), "Projectile": (2, "nearest")}


# ---------------------------------------------------------------- Stage 6: pops and statuses

AGENTS7 = {**AGENTS6, "Grenadier": "KAY/O", "Terra": "Waylay", "Sequoia": "Iso", "Iris": "Miks"}


def table7() -> PlayerTable:
    # Slot 0 KAY/O, 4 Waylay, 5 Iso, 7 Miks (team A: 0, 4, 5, 7); slots 1, 2, 3 are team B.
    table = players()
    table.agents[0], table.agents[4], table.agents[5], table.agents[7] = "KAY/O", "Waylay", "Iso", "Miks"
    return table


TEAMS7 = {0: "A", 4: "A", 5: "A", 7: "A", 6: "A", 1: "B", 2: "B", 3: "B", 8: "B", 9: "B"}


def run7(tmp_path, rows, teams=TEAMS7):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["time_ms"])), encoding="utf-8")
    return build_extras(path, table7(), WINDOWS, GAME_MAP, AGENTS7, lambda t_ms: {}, teams=teams)


def test_zero_point_is_read_despite_its_casing_and_suppresses_enemies_for_the_games_8_s(tmp_path):
    pulse = 40
    rows = [spawned(20_000, pulse, "Default__Gameobject_Grenadier_E_SuppressionPulse_C", 0, 0), instigated(20_000, pulse, 100),
            oneshot(21_000, pulse, [], 9),                                   # the pulse goes off at +1 s
            oneshot(21_050, 101, [pulse], 31), oneshot(21_050, 102, [pulse], 31),  # two enemies suppressed
            oneshot(21_050, 104, [pulse], 31),                               # a teammate: friendly (revision 22)
            closed(35_000, pulse)]
    extras = run7(tmp_path, rows)
    [knife] = extras.rounds[1]["abilities"]
    assert (knife["kind"], knife["code"], knife["name"], knife["fx"]) == ("GameObject", "Grenadier", "E_SuppressionPulse", [11.0])
    statuses = extras.rounds[1]["statuses"]
    got = [(st["target"], st["t0"], st["t1"], st["status"], st["slot"], st["from"]) for st in statuses
           if not st.get("friendly")]
    assert got == [(1, 11.05, 19.05, "suppressed", 0, "object"), (2, 11.05, 19.05, "suppressed", 0, "object")]
    assert [(st["target"], st["status"], st["slot"]) for st in statuses if st.get("friendly")] == [(4, "suppressed", 0)]


def test_an_object_named_status_lasts_until_it_stops_and_duplicates_merge(tmp_path):
    boom = 41
    rows = [spawned(30_000, boom, "Default__GameObject_Terra_C_TimeSlowGrenade_Explosion_C", 0, 0),
            instigated(30_000, boom, 104),
            effect(30_000, 101, 1, [boom], container=7), stop(30_600, 101, 1),
            effect(30_100, 101, 2, [boom], container=8), stop(30_900, 101, 2),   # a second container, same hit
            effect(30_000, 105, 3, [boom], container=7), stop(30_600, 105, 3),   # Waylay's teammate: friendly
            closed(40_000, boom)]
    extras = run7(tmp_path, rows)
    statuses = extras.rounds[1]["statuses"]
    assert [(st["target"], st["t0"], st["t1"], st["status"]) for st in statuses if not st.get("friendly")] ==         [(1, 20.0, 20.9, "hindered")]
    assert [(st["target"], st["status"], st["friendly"]) for st in statuses if st.get("friendly")] == [(5, "hindered", True)]
    assert "fx" not in extras.rounds[1]["abilities"][0], "no effect on itself: it pops at its spawn"


def test_a_caster_named_status_is_found_after_its_source_like_isos_fragile(tmp_path):
    rows = []
    for i, t in enumerate((20_000, 40_000)):
        missile = 50 + i
        rows += [spawned(t, missile, "Default__GameObject_Sequoia_Q_FragileMissile_TrajectoryWarning_C", 0, 0),
                 instigated(t, missile, 105), closed(t + 1_550, missile),
                 effect(t + 600, 101 + i, 10 + i, [105], container=70), stop(t + 4_600, 101 + i, 10 + i)]
    rows += [effect(20_000, 102, 40, [50], container=72), stop(20_300, 102, 40),     # the path warning: not a status
             effect(52_000, 103, 30, [105], container=71), stop(52_500, 103, 30),   # Iso's, but no source near
             effect(21_000, 104, 31, [105], container=70), stop(25_000, 104, 31)]   # on Iso's teammate
    extras = run7(tmp_path, rows)
    got = [(st["target"], st["t0"], st["t1"], st["status"], st["slot"], st["from"]) for st in extras.rounds[1]["statuses"]]
    assert got == [(1, 10.6, 14.6, "fragile", 5, "caster"), (2, 30.6, 34.6, "fragile", 5, "caster")]


AGENTS_NDH = {**AGENTS7, "Sprinter": "Neon", "Cable": "Deadlock", "Mage": "Harbor"}


def object_statuses(tmp_path, archetypes, effect_ms=1_000, life_ms=16_000):
    """Waylay's slot (4, team A) owns one object per archetype; each plays an effect on enemy 1 (team B) from
    1 s after its spawn, for `effect_ms`, and closes `life_ms` after it."""
    rows = []
    for i, archetype in enumerate(archetypes):
        guid, t = 60 + i, 20_000 + i * 20_000
        rows += [spawned(t, guid, f"Default__GameObject_{archetype}_C", 0, 0), instigated(t, guid, 104),
                 effect(t + 1_000, 101, 10 + i, [guid], container=80 + i), stop(t + 1_000 + effect_ms, 101, 10 + i),
                 closed(t + life_ms, guid)]
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["time_ms"])), encoding="utf-8")
    extras = build_extras(path, table7(), WINDOWS, GAME_MAP, AGENTS_NDH, lambda t_ms: {}, teams=TEAMS7)
    return [(st["name"], st["t0"], st["t1"], st["status"]) for st in extras.rounds[1].get("statuses", [])]


def test_fast_lane_high_tide_and_storm_surge_effects_are_no_status(tmp_path):
    archetypes = ["Sprinter_4_Tunnel", "Mage_Q_WallManager", "Mage_4_SplashGrenade"]
    assert object_statuses(tmp_path, archetypes) == []


def test_deadlocks_gravnet_nets_and_her_ult_cocoons_for_as_long_as_they_last(tmp_path):
    got = object_statuses(tmp_path, ["Cable_4_RemovableNet", "Cable_X_FishingHook_Cage"], effect_ms=2_000)
    assert got == [("4_RemovableNet", 11.0, 13.0, "netted"), ("X_FishingHook_Cage", 31.0, 33.0, "cocooned")]


def buffed(t_ms, actor, effect_id, context, seconds, container=900):
    row = effect(t_ms, actor, effect_id, context, container)
    row["payload"]["FunctionFloatValues"] = [{"Name": {"TagName": "FXC.Buff.Duration"}, "Value": seconds}]
    return row


def force(t_ms, character, source):
    return {"type": "rpc_received", "time_ms": t_ms, "actor_net_guid": character, "function_name": "NetMulticastApplyForceModule",
            "payload": {"Character": character, "Source": source, "Module": 77}}


def reckoning(tmp_path, touches):
    """Waylay's slot (4, team A) casts a Reckoning (one chunk, guid 70) at 20 s, which closes at 34 s."""
    rows = [spawned(20_000, 70, "Default__GameObject_Mage_X_TidalWave_Chunk_C", 0, 0), instigated(20_000, 70, 104),
            closed(34_000, 70)] + touches
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["time_ms"])), encoding="utf-8")
    extras = build_extras(path, table7(), WINDOWS, GAME_MAP, AGENTS_NDH, lambda t_ms: {}, teams=TEAMS7)
    return [(st["target"], st["t0"], st["t1"], st["status"]) for st in extras.rounds[1].get("statuses", [])]


def test_reckoning_nearsights_for_its_debuffs_length_not_its_touch_effects(tmp_path):
    touches = [effect(21_000, 101, 1, [70], container=800),                 # enemy 1 touched before it formed
               force(21_000, 101, 70),
               effect(22_000, 102, 2, [70], container=800), buffed(22_000, 102, 3, [70], 3),   # enemy 2 debuffed
               force(22_000, 102, 70), stop(34_000, 102, 2), stop(34_000, 102, 3),
               effect(22_500, 105, 4, [70], container=800), force(22_500, 105, 70),            # a teammate touched
               buffed(23_000, 107, 5, [70], 3), force(23_000, 107, 70),                        # one debuffed
               buffed(23_500, 104, 6, [70], 3)]                                                # never Harbor
    assert reckoning(tmp_path, touches) == [(2, 12.0, 15.0, "nearsighted"), (7, 13.0, 16.0, "nearsighted")]


def test_walking_back_into_a_stopped_reckoning_nearsights_again(tmp_path):
    touches = [buffed(22_000, 102, 3, [70], 3), force(22_000, 102, 70), force(22_100, 102, 70),
               force(28_000, 102, 70), force(29_500, 102, 70),               # back in the stopped wave
               force(30_000, 101, 70)]                                       # never debuffed: not a hit
    assert reckoning(tmp_path, touches) == [(2, 12.0, 15.1, "nearsighted"), (2, 18.0, 22.5, "nearsighted")]


def test_a_track_crossing_high_tide_is_slowed_whoever_it_is():
    from app.replays.condense import high_tide_slows
    from app.replays.format import encode_segment

    def track(t0, us, vs):
        return [encode_segment(t0, us, vs, [0] * len(us))]

    wall = [{"k": "ability", "t": 5.0, "t1": 7.0, "by": 2, "code": "Mage", "name": "Q_Wall", "kind": "Projectile",
             "flight": [[5.0, 5000, 1000, 28], [6.0, 5000, 5000, 28], [7.0, 5000, 9000, 28]]},
            {"k": "ability", "t": 5.0, "t1": 22.0, "by": 2, "code": "Mage", "name": "Q_WallManager", "kind": "GameObject"}]
    # 16 Hz; slot 1 crosses u=5000 at 10 s (and back at 30 s, after the wall), slot 3 at 12 s, slot 4 never.
    blob = {"hz": 16, "util": wall, "tracks": {
        "1": track(9.0, [4840 + 10 * i for i in range(33)], [3000] * 33),
        "3": track(11.0, [5160 - 10 * i for i in range(33)], [6000] * 33),
        "4": track(9.0, [4000] * 33, [3000] * 33),
        "5": track(29.0, [5160 - 10 * i for i in range(33)], [3000] * 33)}}
    got = [(e["target"], e["t"], e["t1"], e["status"], e["by"]) for e in high_tide_slows(blob)]
    assert got == [(1, 10.0, 10.6, "slowed", 2), (3, 12.0, 12.6, "slowed", 2)]


def test_a_status_on_a_dead_player_is_dropped_and_one_running_past_death_ends_there():
    from app.replays.condense import statuses_while_alive

    def status(t, t1, target=1):
        return {"k": "status", "t": t, "t1": t1, "target": target, "status": "slowed"}

    alive = {"1": [[0.0, 20.0, "kill"], [30.0, None, "revive"]], "2": [[0.0, None, None]]}
    util = [status(5.0, 8.0), status(18.0, 24.0), status(22.0, 23.0), status(31.0, 33.0), status(40.0, 41.0, 2),
            {"k": "shot", "t": 25.0, "by": 1}]
    got = statuses_while_alive(util, alive)
    assert [(e["k"], e["t"], e.get("t1")) for e in got] == [
        ("status", 5.0, 8.0), ("status", 18.0, 20.0), ("status", 31.0, 33.0), ("status", 40.0, 41.0), ("shot", 25.0, None)]
    assert statuses_while_alive(util, None) == util


def test_m_pulse_keeps_its_pulses(tmp_path):
    wave = 42
    rows = [spawned(20_000, wave, "Default__GameObject_Thumper_Concuss_C", 0, 0), instigated(20_000, wave, 107),
            oneshot(20_000, wave, [], 5), effect(20_010, wave, 1, [], container=6),
            effect(22_000, wave, 2, [], container=6), effect(24_000, wave, 3, [], container=6), closed(25_000, wave)]
    [pulse] = run7(tmp_path, rows).rounds[1]["abilities"]
    assert (pulse["code"], pulse["name"], pulse["fx"]) == ("Iris", "Concuss", [10.0, 12.0, 14.0])


def damage(t_ms, victim, causer, lethal=False):
    return {"type": "rpc_received", "time_ms": t_ms, "actor_net_guid": victim, "function_name": "MulticastNotifyDamage_Point",
            "payload": {"DamageCauser": causer, "DamageDealt": 5, "DamageKilledTarget": lethal}}


def test_a_trapwire_tethers_then_goes_off_revealing_and_concussing_and_stays_until_shot(tmp_path):
    # Cypher 1's trip (anchors 60, 61; team B) catches enemy 4 (team A).
    rows = [spawned(1_000, 500, "Default__Ability_Gumshoe_4_TripWire_C", 0, 0), effect(2_000, 101, 1, [500]),
            spawned(8_000, 60, "Default__GameObject_Gumshoe_4_TripWire_C", 0, 0),
            spawned(8_000, 61, "Default__GameObject_Gumshoe_4_TripWire_SecondWire_C", 0, 400), placement(8_000, 500, [61]),
            effect(22_840, 104, 5, [60], container=4895), effect(22_840, 104, 6, [61], container=4907),  # caught
            oneshot(22_840, 101, [61], 4919),                                   # the owner's alert: not a status
            oneshot(23_540, 104, [60], 5173), oneshot(23_540, 104, [61], 5173), damage(23_540, 104, 61),
            stop(23_540, 104, 5), stop(23_540, 104, 6)]
    extras = run7(tmp_path, rows)
    got = [(st["target"], st["t0"], st["t1"], st["status"], st["slot"], st["name"]) for st in extras.rounds[1]["statuses"]]
    assert got == [(4, 12.84, 13.54, "tethered", 1, "4_TripWire"), (4, 13.54, 14.54, "concussed", 1, "4_TripWire")]
    assert [(r["t0"], r["slot"], r["target"], r["name"]) for r in extras.rounds[1]["reveals"]] == [(13.54, 1, 4, "4_TripWire")]
    [wire] = [a for a in extras.rounds[1]["abilities"] if a["name"] == "4_TripWire"]
    assert "gone" not in wire, "going off doesn't use a trip up"
    rows.append(damage(40_000, 61, 999, lethal=True))                       # then its second anchor is shot
    [wire] = [a for a in run7(tmp_path, rows).rounds[1]["abilities"] if a["name"] == "4_TripWire"]
    assert wire["gone"] == 30.0, "either anchor destroyed takes the wire"


def test_utility_shot_and_destroyed_is_gone(tmp_path):
    rows = [spawned(20_000, 70, "Default__Pawn_Gumshoe_E_PossessableCamera_C", 0, 0), instigated(20_000, 70, 101),
            damage(30_000, 70, 999, lethal=True), closed(55_000, 70),
            spawned(20_000, 71, "Default__Pawn_Gumshoe_E_PossessableCamera_C", 0, 0), instigated(20_000, 71, 101),
            damage(30_000, 71, 999)]                                            # hit but not destroyed
    cams = run7(tmp_path, rows).rounds[1]["abilities"]
    assert [c.get("gone") for c in cams] == [20.0, None]


def test_vipers_pit_keeps_its_ground_and_decays_the_enemies_inside(tmp_path):
    # trap-jobs D13, the shapes of b22430cd round 6: the Pit's GroundVolumeComponent lists its fragments (2 m
    # convex hulls), and an enemy inside gets an effect naming the Pit beside Viper. Viper is slot 9 (team B).
    pit = 300
    fragments = {"type": "export_group_received", "time_ms": 21_000, "actor_net_guid": pit,
                 "export_group_path": "/Script/DynamicVolume.GroundVolumeComponent",
                 "payload": {"FinalCount": 2, "FragmentDelta": {"DeletedIds": [], "Updates": [
                     {"ReplicationID": 1, "ConvexHullPoints": [{"Point": {"x": x, "y": y, "z": 188}}
                                                               for x, y in ((0, 0), (200, 0), (200, 200), (0, 200))]},
                     {"ReplicationID": 2, "ConvexHullPoints": [{"Point": {"x": x, "y": y, "z": 188}}
                                                               for x, y in ((200, 0), (400, 0), (400, 200), (200, 200))]}]}}}
    rows = [spawned(20_000, pit, "Default__Patch_Pandemic_X_Circular_C", 100, 100), instigated(20_000, pit, 109),
            fragments,
            effect(25_000, 100, 7, [pit, 109], container=8263), stop(30_000, 100, 7),      # enemy (team A): decayed
            effect(25_000, 101, 8, [pit, 109], container=8263), stop(30_000, 101, 8),      # a teammate: harmless contact
            closed(40_000, pit)]
    out = build_extras_rows(tmp_path, rows, {**AGENTS7, "Pandemic": "Viper"}).rounds[1]
    [row] = [a for a in out["abilities"] if a["name"] == "X_Circular"]
    assert len(row["area"]) == 2 and all(len(h) == 4 for h in row["area"])
    assert row["area"][1][1] == list(GAME_MAP.to_uv(400, 0))
    decayed = [s for s in out.get("statuses", []) if s["status"] == "decayed"]
    assert [(s["slot"], s["target"], s["t0"], s["t1"], bool(s.get("friendly"))) for s in decayed] == [
        (9, 0, 15.0, 20.0, False)]


def _ground(t_ms, guid, updates=(), deleted=()):
    return {"type": "export_group_received", "time_ms": t_ms, "actor_net_guid": guid,
            "export_group_path": "/Script/DynamicVolume.GroundVolumeComponent",
            "payload": {"FinalCount": len(updates), "FragmentDelta": {"DeletedIds": list(deleted), "Updates": [
                {"ReplicationID": rid, "ConvexHullPoints": [{"Point": {"x": x + dx, "y": y, "z": 188}}
                                                            for x, y in ((0, 0), (200, 0), (200, 200), (0, 200))]}
                for rid, dx in updates]}}}


def test_a_pits_ground_drops_deleted_fragments_and_never_outlives_its_spawn(tmp_path):
    # Branch review B19: a delta that only deletes (no hull points in the line) still removes its fragment.
    # B20: a GUID reused by a later Pit starts with no ground of the earlier one's.
    pit, agents = 300, {**AGENTS7, "Pandemic": "Viper"}
    rows = [spawned(20_000, pit, "Default__Patch_Pandemic_X_Circular_C", 100, 100), instigated(20_000, pit, 109),
            _ground(21_000, pit, updates=[(1, 0), (2, 200)]), _ground(22_000, pit, deleted=[1]),
            closed(30_000, pit)]
    [row] = [a for a in build_extras_rows(tmp_path, rows, agents).rounds[1]["abilities"] if a["name"] == "X_Circular"]
    assert len(row["area"]) == 1 and row["area"][0][0] == list(GAME_MAP.to_uv(200, 0))
    rows += [spawned(40_000, pit, "Default__Patch_Pandemic_X_Circular_C", 900, 900), instigated(40_000, pit, 109),
             _ground(41_000, pit, updates=[(7, 900)]), closed(50_000, pit)]
    [row] = [a for a in build_extras_rows(tmp_path, rows, agents).rounds[1]["abilities"] if a["name"] == "X_Circular"]
    assert len(row["area"]) == 1 and row["area"][0][0] == list(GAME_MAP.to_uv(900, 0))


def build_extras_rows(tmp_path, rows, agents):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["time_ms"])), encoding="utf-8")
    return build_extras(path, table7(), WINDOWS, GAME_MAP, agents, lambda t_ms: {}, teams=TEAMS7)


AGENTS14 = {**AGENTS7, "Aggrobot": "Gekko", "Vampire": "Reyna"}


def run14(tmp_path, rows):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["time_ms"])), encoding="utf-8")
    return build_extras(path, table7(), WINDOWS, GAME_MAP, AGENTS14, lambda t_ms: {}, teams=TEAMS7)


def _leer(guid):
    return spawned(20_000, guid, "Default__GameObject_Vampire_4_NearsightAOE_Source_C", 0, 0)


def test_a_lethal_hit_in_the_close_ms_is_gone_for_every_object(tmp_path):
    # trap-jobs D4 (CONDENSE 23): a shot camera, turret or alarmbot closes in its lethal hit's ms, like Leer.
    rows = [_leer(80), damage(30_000, 80, 999, lethal=True), closed(30_000, 80),    # shot down: same ms
            _leer(81), closed(30_000, 81),                                          # natural close
            spawned(20_000, 82, "Default__Pawn_Gumshoe_E_PossessableCamera_C", 0, 0), instigated(20_000, 82, 101),
            damage(30_000, 82, 999, lethal=True), closed(30_000, 82),               # shot: same ms
            spawned(20_000, 83, "Default__Pawn_Gumshoe_E_PossessableCamera_C", 0, 0), instigated(20_000, 83, 101),
            damage(30_000, 83, 999), closed(30_000, 83),                            # hit, not lethal: a recall
            spawned(20_000, 84, "Default__Pawn_Gumshoe_E_PossessableCamera_C", 0, 0), instigated(20_000, 84, 101),
            closed(30_000, 84), damage(30_050, 84, 999, lethal=True)]               # lethal after the close
    out = {a["id"]: a for a in run14(tmp_path, rows).rounds[1]["abilities"]}
    assert out[80]["gone"] == 20.0
    assert "gone" not in out[81]
    assert out[82]["gone"] == 20.0
    assert "gone" not in out[83]
    assert "gone" not in out[84]


def _dizzy_rows(close_ms=21_000, spawner=True, ray_y=60.0):
    rows = [spawned(20_000, 90, "Default__GameObject_Aggrobot_E_DiscTurret_PowerWave_C", 0, 0), closed(close_ms, 90)]
    if spawner:
        rows.append(spawned(close_ms, 91, "Default__GameObject_Aggrobot_E_OrbSpawner_C", 1000, 0, z=50))
    # A shot from the origin along +x; the spawner at (1000, 0, 50) is `ray_y` off the line's y... via the origin's y.
    row = shot(close_ms, 200, 0, ray_y, 1, 0)
    row["shot"]["location"]["z"] = 50
    rows.append(row)
    return rows


def _dizzy(tmp_path, **kw):
    [d] = [a for a in run14(tmp_path, _dizzy_rows(**kw)).rounds[1]["abilities"] if a["id"] == 90]
    return d


def test_a_dizzy_shot_down_is_gone_when_an_orb_dropped_under_a_shot_ray(tmp_path):
    assert _dizzy(tmp_path)["gone"] == 11.0
    assert "gone" not in _dizzy(tmp_path, ray_y=400.0)
    assert "gone" not in _dizzy(tmp_path, close_ms=21_900)       # closed at its natural 1.9 s
    assert "gone" not in _dizzy(tmp_path, spawner=False)


def test_a_poison_cloud_takes_the_zones_of_its_own_owner_while_alive(tmp_path):
    # The export gives neither emitter nor zone an Instigator: owners come by nearest / the landing.
    em = "Default__Projectile_Pandemic_4_SmokeGrenade_C"
    zone = "Default__Zone_Pandemic_4_SmokeZone_C"
    rows = [spawned(20_000, 95, em, 0, 0), closed(22_000, 95),                       # player 8's, lands at 22 s
            spawned(22_000, 96, zone, 0, 0), closed(25_000, 96),
            spawned(20_000, 100, em, 5000, 5000), closed(23_000, 100),               # player 9's, lands at 23 s
            spawned(23_000, 101, zone, 5000, 5000),
            spawned(40_000, 102, em, 0, 0), closed(41_000, 102)]                     # player 8's, no zone in its life
    table = table7()
    table.agents[8] = table.agents[9] = "Viper"
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["time_ms"])), encoding="utf-8")
    extras = build_extras(path, table, WINDOWS, GAME_MAP, AGENTS14, lambda t_ms: {8: (0.0, 0.0), 9: (5000.0, 5000.0)},
                          teams=TEAMS7)
    out = {a["id"]: a for a in extras.rounds[1]["abilities"] if a["name"] == "4_SmokeGrenade"}
    assert (out[95]["slot"], out[100]["slot"]) == (8, 9)
    assert out[95]["on"] == [[12.0, 15.0]]
    assert out[100]["on"] == [[13.0, None]]
    assert out[102]["on"] == [], "known never on: not the same as a row stored before `on`"


def held(t_ms, pawn, equippable):
    # the real shape (2026-10-25 Sunset export): the character's inventory names what it now holds
    return {"type": "export_group_received", "time_ms": t_ms, "actor_net_guid": pawn, "is_actor": False,
            "export_group_path": "/Script/ShooterGame.AresInventory",
            "payload": {"NetTimestamp": 1, "NewCurrentEquippable": equippable}}


# Held guns (revision 30). All actor and pawn ids here are synthetic.
VANDAL = "Default__AssaultRifle_AK_C"
OVERDRIVE = "Default__Gun_Sprinter_X_HeavyLightningGun_Production_C"
UNKNOWN_GUN = "Default__FutureSniper_C"


def gun_shot(t_ms, guid=900, name="Vandal", category="rifle"):
    row = shot(t_ms, 201, 0, 0, 1, 0, name)
    row["shot"]["equippable"] = {"net_guid": guid, "name": name, "category": category}
    return row


def held_run(tmp_path, rows, *, table=None, alive=None):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return build_extras(path, table or players(), WINDOWS, GAME_MAP, AGENTS, lambda t: {},
                        alive_by_round=alive)


def holds(extras, n=1):
    return [r for r in util_entries(extras.rounds.get(n, {})) if r["k"] == "hold"]


@pytest.mark.parametrize("reference", [{"net_guid": 900}, {"net_guid": 900, "path": "SyntheticGun"}])
def test_held_vandal_real_inventory_shape_and_shot_guid(tmp_path, reference):
    switch = held(15_000, 101, 900)
    switch["payload"]["NewCurrentEquippable"] = reference
    extras = held_run(tmp_path, [spawned(12_000, 900, VANDAL, 0, 0), switch,
                                 held(15_100, 101, 900), gun_shot(16_000)])
    assert holds(extras) == [{"k": "hold", "t": 5.0, "by": 1, "gun": "Vandal", "cat": "rifle",
                              "id": 900, "gen": 0}]
    assert extras.rounds[1]["shots"][0]["gid"] == 900
    assert extras.rounds[1]["shots"][0]["gen"] == 0
    assert extras.report["holds"] == 1


@pytest.mark.parametrize("lead,expected", [(1_500, True), (3_000, False)])
def test_early_gun_switch_keeps_switch_time(tmp_path, lead, expected):
    extras = held_run(tmp_path, [held(15_000, 101, 900), spawned(15_000 + lead, 900, VANDAL, 0, 0)])
    firearm = [r for r in holds(extras) if r.get("id") == 900]
    assert bool(firearm) == expected
    if expected:
        assert firearm[0]["t"] == 5.0


MELEE = "Default__Ability_Melee_Base_C"


def test_early_switch_resolved_late_keeps_source_order_at_the_same_millisecond(tmp_path):
    # Knife (unspawned) then Vandal at one instant; the knife spawns afterwards. Last hold must be Vandal.
    extras = held_run(tmp_path, [spawned(12_000, 900, VANDAL, 0, 0),
                                 held(15_000, 101, 901), held(15_000, 101, 900),
                                 spawned(15_500, 901, MELEE, 0, 0)])
    rows = holds(extras)
    assert [(r["gun"], r["t"]) for r in rows] == [("Knife", 5.0), ("Vandal", 5.0)]
    assert rows[-1]["id"] == 900


def test_early_switch_resolved_late_keeps_order_among_several_simultaneous_switches(tmp_path):
    # Two unspawned actors and a spawned one, all at one instant, resolved at different later times.
    extras = held_run(tmp_path, [spawned(12_000, 900, VANDAL, 0, 0),
                                 held(15_000, 101, 901), held(15_000, 101, 900), held(15_000, 101, 902),
                                 spawned(15_400, 902, MELEE, 0, 0), spawned(15_600, 901, MELEE, 0, 0)])
    assert [r["id"] for r in holds(extras)] == [901, 900, 902]


def test_early_switch_resolved_late_on_another_slot_does_not_reorder_this_one(tmp_path):
    extras = held_run(tmp_path, [spawned(12_000, 900, VANDAL, 0, 0),
                                 held(15_000, 101, 901), held(15_000, 106, 900), held(15_000, 101, 900),
                                 spawned(15_500, 901, MELEE, 0, 0)])
    mine = [r["gun"] for r in holds(extras) if r["by"] == 1]
    assert mine == ["Knife", "Vandal"]


def test_melee_and_other_switches_close_gun_but_keep_ult_equips(tmp_path):
    extras = held_run(tmp_path, [spawned(12_000, 900, VANDAL, 0, 0),
        spawned(12_000, 901, "Default__Ability_Melee_Base_C", 0, 0),
        spawned(12_000, 902, "Default__Ability_Vampire_X_Frenzy_C", 0, 0),
        spawned(12_000, 903, "Default__BombEquippable_C", 0, 0),
        spawned(12_000, 904, OVERDRIVE, 0, 0),
        held(14_000, 101, 900), held(15_000, 101, 901), held(16_000, 101, 902),
        held(17_000, 101, 903), held(18_000, 101, 904)])
    rows = holds(extras)
    assert (rows[1]["gun"], rows[1]["cat"]) == ("Knife", "melee")
    assert rows[2:] == [{"k": "hold", "t": t, "by": 1, "gun": None, "cat": "other"}
                        for t in (6.0, 7.0, 8.0)]
    assert len(extras.rounds[1]["equips"]) == 1


@pytest.mark.parametrize("archetype", [
    "Default__Equippable_Snake_Totem_C",
    "Default__Equippable_Sequoia_X_ArenaTeleport_C",
    "Default__Equippable_Totem_PDA_C",
    "Default__Equippable_Skymage_Totem_C",
    "Default__Equippable_DuosDay26_Duck_Totem_C",
    "Default__Equippable_FutureItem_C",
    "Default__Bomb_Defuser_C",
])
def test_non_gun_equippables_close_holds_without_unnamed_guns(tmp_path, archetype):
    extras = held_run(tmp_path, [spawned(12_000, 900, VANDAL, 0, 0),
        spawned(13_000, 901, archetype, 0, 0), held(14_000, 101, 900), held(15_000, 101, 901)])
    assert holds(extras) == [
        {"k": "hold", "t": 4.0, "by": 1, "gun": "Vandal", "cat": "rifle", "id": 900, "gen": 0},
        {"k": "hold", "t": 5.0, "by": 1, "gun": None, "cat": "other"}]
    assert extras.report["holds_unnamed"] == 0


def test_unarmed_equippable_still_opens_a_melee_hold(tmp_path):
    from app.replays import guns

    archetype = "Default__Equippable_Unarmed_C"
    assert guns.is_melee(archetype)
    extras = held_run(tmp_path, [spawned(12_000, 900, VANDAL, 0, 0),
        spawned(13_000, 901, archetype, 0, 0), held(14_000, 101, 900), held(15_000, 101, 901)])
    assert holds(extras)[1] == {"k": "hold", "t": 5.0, "by": 1, "gun": "Knife", "cat": "melee",
                                "id": 901, "gen": 1}
    assert extras.report["holds_unnamed"] == 0


@pytest.mark.parametrize("fired", [False, True])
def test_unknown_ds_gun_remains_a_candidate_for_shot_learning(tmp_path, fired):
    rows = [spawned(12_000, 900, VANDAL, 0, 0),
            spawned(13_000, 901, "Default__DS_Gun_C", 0, 0),
            held(14_000, 101, 900), held(15_000, 101, 901)]
    if fired:
        rows.append(gun_shot(16_000, guid=901, name="Outlaw", category="sniper_rifle"))
    extras = held_run(tmp_path, rows)
    assert holds(extras)[1] == {"k": "hold", "t": 5.0, "by": 1,
                                "gun": "Outlaw" if fired else None,
                                "cat": "sniper_rifle" if fired else None, "id": 901, "gen": 1}
    assert extras.report["holds_unnamed"] == (0 if fired else 1)


def test_learning_and_pickups_are_per_generation_even_for_late_shot_rows(tmp_path):
    extras = held_run(tmp_path, [spawned(12_000, 900, UNKNOWN_GUN, 0, 0),
        held(13_000, 101, 900), held(20_000, 106, 900), closed(30_000, 900),
        spawned(31_000, 900, UNKNOWN_GUN, 0, 0), held(32_000, 101, 900),
        gun_shot(14_000, name="Outlaw", category="sniper_rifle")])
    first, pickup, reused = holds(extras)
    assert (first["gun"], first["cat"]) == ("Outlaw", "sniper_rifle")
    assert pickup["from"] == 1 and pickup["gen"] == first["gen"] == 0
    assert "from" not in reused and reused["gen"] == 1
    assert reused["gun"] is None and reused["cat"] is None
    assert extras.report["holds_unnamed"] == 1
    assert extras.rounds[1]["shots"][0]["gen"] == 0


def test_ability_gun_shots_have_distinct_generations_and_close_holds(tmp_path):
    extras = held_run(tmp_path, [spawned(12_000, 900, OVERDRIVE, 0, 0), held(13_000, 101, 900),
        gun_shot(14_000, name="Overdrive", category="ability"), closed(15_000, 900),
        spawned(16_000, 900, OVERDRIVE, 0, 0), held(17_000, 101, 900),
        gun_shot(18_000, name="Overdrive", category="ability")])
    assert [(s["gid"], s["gen"]) for s in extras.rounds[1]["shots"]] == [(900, 0), (900, 1)]
    assert all(r["cat"] == "other" and "gen" not in r and "id" not in r for r in holds(extras))


def test_shot_without_equippable_has_no_guid_or_generation(tmp_path):
    row = shot(15_000, 201, 0, 0, 1, 0)
    del row["shot"]["equippable"]
    [entry] = held_run(tmp_path, [row]).rounds[1]["shots"]
    assert "gid" not in entry and "gen" not in entry


def test_buy_phase_switch_and_survivor_seed(tmp_path):
    alive = {1: {"1": [[0.0, None, "round_end"]], "6": [[0.0, None, "round_end"]]},
             2: {"1": [[0.0, None, "round_end"]], "6": [[0.0, None, "round_end"]]}}
    rows = [spawned(12_000, 900, VANDAL, 0, 0), held(15_000, 101, 900),
            spawned(65_000, 901, VANDAL, 0, 0), held(66_000, 106, 901)]
    extras = held_run(tmp_path, rows, alive=alive)
    assert holds(extras, 2) == [
        {"k": "hold", "t": 0.0, "by": 1, "gun": "Vandal", "cat": "rifle", "id": 900, "gen": 0, "seed": True},
        {"k": "hold", "t": 0.0, "by": 6, "gun": "Vandal", "cat": "rifle", "id": 901, "gen": 1}]
    assert extras.report["holds_seeded"] == 1
    assert not any(r.get("seed") for r in holds(held_run(tmp_path, rows), 2))


@pytest.mark.parametrize("revive", [False, True])
def test_dead_or_revived_holder_never_seeds_but_surviving_picker_does(tmp_path, revive):
    table = players()
    if revive:
        table.pawn_slot[301] = 1
        table.pawn_changes[1] = [(0, 101), (40_000, 301)]
    alive = {1: {"1": [[0.0, 20.0, "kill"]] + ([[30.0, None, "round_end"]] if revive else []),
                 "6": [[0.0, None, "round_end"]]},
             2: {"1": [[0.0, None, "round_end"]], "6": [[0.0, None, "round_end"]]}}
    extras = held_run(tmp_path, [spawned(12_000, 900, VANDAL, 0, 0), held(15_000, 101, 900),
                                 held(35_000, 106, 900)], table=table, alive=alive)
    assert holds(extras)[1]["from"] == 1
    assert holds(extras, 2) == [{"k": "hold", "t": 0.0, "by": 6, "gun": "Vandal", "cat": "rifle",
                                 "id": 900, "gen": 0, "seed": True}]


def test_death_then_revive_without_pickup_still_does_not_seed(tmp_path):
    table = players()
    table.pawn_slot[301] = 1
    table.pawn_changes[1] = [(0, 101), (40_000, 301)]
    alive = {1: {"1": [[0.0, 20.0, "kill"], [30.0, None, "round_end"]]},
             2: {"1": [[0.0, None, "round_end"]]}}
    extras = held_run(tmp_path, [spawned(12_000, 900, VANDAL, 0, 0), held(15_000, 101, 900)],
                      table=table, alive=alive)
    assert holds(extras, 2) == []


def test_hold_python_round_trip_preserves_all_fields():
    rows = [{"k": "hold", "t": 0.0, "by": 1, "gun": "Vandal", "cat": "rifle", "id": 900, "gen": 0, "seed": True},
            {"k": "hold", "t": 20.0, "by": 6, "gun": None, "cat": None, "id": 901, "gen": 1, "from": 1},
            {"k": "hold", "t": 21.0, "by": 6, "gun": None, "cat": "other"}]
    assert util_entries(rounds_extras(rows)) == rows


@pytest.mark.parametrize("streaming", [True, False])
def test_attach_extras_passes_survival_for_seeds(tmp_path, streaming):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    events = match.events() + [spawned(start + 1_000, 9001, VANDAL, 0, 0),
                               held(start + 2_000, match.pawn(1, 1), 9001)]
    directory = match.write(tmp_path / "export", events)
    from replay_synthetic import MATCH_UUID

    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf")
    replay = cd.condense_export_dir(directory, source_sha256=match.source_sha256,
                                    streaming=streaming, vrf_path=vrf)
    assert [r for r in replay.rounds[2]["util"] if r["k"] == "hold"] == [
        {"k": "hold", "t": 0.0, "by": 1, "gun": "Vandal", "cat": "rifle",
         "id": 9001, "gen": 0, "seed": True}]
    assert not [r for r in replay.rounds[3]["util"] if r["k"] == "hold"]


def test_seeds_use_ownership_at_round_start_and_a_later_explicit_hold_does_not_hide_seed(tmp_path):
    alive = {1: {"1": [[0.0, None, "round_end"]]},
             2: {"1": [[0.0, None, "round_end"]], "6": [[0.0, None, "round_end"]]}}
    extras = held_run(tmp_path, [spawned(12_000, 900, VANDAL, 0, 0), held(15_000, 101, 900),
                                 held(80_000, 106, 900)], alive=alive)
    assert holds(extras, 2)[0].get("seed") is True
    assert holds(extras, 2)[1]["from"] == 1


def test_a_gun_taken_by_another_slot_cannot_seed_for_its_earlier_living_holder(tmp_path):
    alive = {n: {str(s): [[0.0, None, "round_end"]] for s in (1, 6)} for n in (1, 2)}
    extras = held_run(tmp_path, [spawned(12_000, 900, VANDAL, 0, 0), held(15_000, 101, 900),
                                 held(35_000, 106, 900)], alive=alive)
    assert [r["by"] for r in holds(extras, 2)] == [6]


def test_a_switch_to_the_ult_equippable_is_one_equip_row_and_other_switches_none(tmp_path):
    rows = [spawned(1_000, 50, "Default__Ability_Vampire_X_Frenzy_C", 0, 0),       # Reyna's (slot 6, pawn 106)
            spawned(1_000, 51, "Default__Ability_Vampire_Q_Heal_C", 0, 0),
            held(14_000, 106, 900),                                                 # her gun
            held(15_000, 106, 50), held(15_100, 106, 50),                           # Empress: one switch, sent twice
            held(16_000, 106, 900), held(17_000, 106, 51),                          # back to the gun, then Devour
            held(80_000, 106, 50)]                                                  # round 2
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    extras = build_extras(path, table7(), WINDOWS, GAME_MAP, AGENTS14, lambda t_ms: {}, teams=TEAMS7)
    assert extras.rounds[1]["equips"] == [{"t": 5.0, "slot": 6, "code": "Vampire", "name": "X_Frenzy"}]
    assert extras.rounds[2]["equips"] == [{"t": 10.0, "slot": 6, "code": "Vampire", "name": "X_Frenzy"}]
    util = util_entries(extras.rounds[1])
    assert [e for e in util if e["k"] == "equip"] == [{"k": "equip", "t": 5.0, "by": 6, "code": "Vampire",
                                                       "name": "X_Frenzy"}]
    assert rounds_extras(util)["equips"] == extras.rounds[1]["equips"]


def test_a_switch_sent_before_its_equippable_spawned_keeps_its_time_and_a_reused_guid_is_a_new_switch(tmp_path):
    rows = [held(15_000, 106, 50),                                                  # names guid 50 before it spawns
            spawned(15_200, 50, "Default__Ability_Vampire_X_Frenzy_C", 0, 0),
            held(15_400, 106, 50),                                                  # the repeat: not another switch
            closed(40_000, 50),
            spawned(75_000, 50, "Default__Ability_Vampire_X_Frenzy_C", 0, 0),       # the GUID reused, a new item
            held(80_000, 106, 50)]                                                  # held without a switch between
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    extras = build_extras(path, table7(), WINDOWS, GAME_MAP, AGENTS14, lambda t_ms: {}, teams=TEAMS7)
    assert [e["t"] for e in extras.rounds[1]["equips"]] == [5.0]
    assert [e["t"] for e in extras.rounds[2]["equips"]] == [10.0]


def test_a_pending_switch_survives_a_later_switch_and_a_stale_one_names_no_later_item(tmp_path):
    rows = [held(15_000, 106, 50), held(15_100, 106, 900),                        # Empress, then the gun, both
            spawned(15_300, 50, "Default__Ability_Vampire_X_Frenzy_C", 0, 0),       # before 50's spawn row
            held(30_000, 106, 60),                                                  # names 60 long before it spawns
            spawned(75_000, 60, "Default__Ability_Vampire_X_Frenzy_C", 0, 0),
            held(80_000, 106, 900), held(81_000, 106, 60)]                          # the real switch to it
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    extras = build_extras(path, table7(), WINDOWS, GAME_MAP, AGENTS14, lambda t_ms: {}, teams=TEAMS7)
    assert [e["t"] for e in extras.rounds[1]["equips"]] == [5.0]
    assert [e["t"] for e in extras.rounds[2]["equips"]] == [11.0]


# ---------------------------------------------------------------- stored util (Stage 2, R2)

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import SyntheticMatch  # noqa: E402

from app.replays import condense as cd  # noqa: E402
from app.replays import format as fmt  # noqa: E402
from app.replays.extras import rounds_extras, util_entries  # noqa: E402

UTIL_KEYS = {"k", "t", "by", "t1", "kind", "code", "name", "agent", "owner_by", "u", "v", "yaw", "thrown", "path",
             "owner_d", "other_d", "u1", "v1", "gun", "n", "end", "defuses", "points", "on", "target", "fx",
             "status", "from", "z", "end_z", "off", "full",
             "id",   # revision 21: an ability row's actor guid, which damage and heal causes name
             "src"}  # CONDENSE 30: a levitated status's zone (that ability row's "id")


def test_util_entries_round_trip_to_the_viewers_shape():
    extras = {"abilities": [{"t0": 1.5, "t1": 9.0, "kind": "Zone", "code": "Wraith", "name": "4_Smoke",
                             "agent": "Omen", "slot": 6, "owner_by": "agent", "u": 10, "v": 20, "yaw": 90}],
              "shots": [{"t": 3.25, "slot": 2, "u": 1, "v": 2, "gun": "Vandal", "n": 1, "u1": 5, "v1": 6}]}
    util = util_entries(extras)
    assert [u["k"] for u in util] == ["ability", "shot"]
    assert util[0]["t"] == 1.5 and util[0]["by"] == 6 and "t0" not in util[0] and "slot" not in util[0]
    assert util[1]["t"] == 3.25 and util[1]["by"] == 2
    assert rounds_extras(util + [{"k": "flash", "t": 1, "by": 0}]) == extras
    extras["reveals"] = [{"t0": 4.0, "t1": 6.0, "slot": 2, "target": 8, "code": "Hunter", "name": "Q_SonarPing"}]
    util = util_entries(extras)
    assert util[1] == {"k": "reveal", "t": 4.0, "by": 2, "t1": 6.0, "target": 8, "code": "Hunter", "name": "Q_SonarPing"}
    assert rounds_extras(util) == extras
    extras["statuses"] = [{"t0": 5.0, "t1": 7.0, "slot": 2, "target": 8, "code": "Iris", "name": "Concuss",
                           "status": "concussed", "from": "object"}]
    util = util_entries(extras)
    assert [u["k"] for u in util].count("status") == 1 and rounds_extras(util) == extras


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
    assert stored[0]["id"] == 9_001, "the actor's guid: what a damage or heal cause joins on"
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


# ---------------------------------------------------------------- Killjoy devices switched off (revision 12)

from app.replays.extras import device_off_spans  # noqa: E402

# Shapes from the Sunset and Lotus replays (docs/superpowers/plans/2026-10-04-control-bugs-impl.md). Container
# ids are made up: they vary by replay, so the rule never reads them. A turret spawns at 0 with its spawn
# effect (container 10) and boots at 2 s (container 11); an alarmbot boots at 1.7 s (container 21).
TURRET = [(0, "oneshot", None, 9), (0, "play", 1, 10), (2_000, "play", 2, 11)]
ALARMBOT = [(0, "oneshot", None, 19), (195, "oneshot", None, 20), (1_700, "play", 1, 21)]


def off_at(t, *watching, new=50, container=60):
    """Switched off: the watching effects stop as a new effect starts, in the same millisecond."""
    return [(t, "play", new, container), *[(t, "stop", w, None) for w in watching]]


def back_on(t, off_effect, new_ids=(70, 71), containers=(10, 11)):
    """Back on: the off effect stops and the spawn and boot containers replay."""
    return [(t, "stop", off_effect, None), *[(t, "play", i, c) for i, c in zip(new_ids, containers)]]


def test_a_turret_switched_off_stays_off_until_it_closes():
    fx = TURRET + off_at(23_600, 1, 2)
    assert device_off_spans(fx, 0, 70_000, turret=True) == [[23_600, None]]


def test_a_turret_comes_back_on_and_can_go_off_again():
    fx = TURRET + off_at(29_600, 1, 2) + [(57_700, "play", 40, 61)] + back_on(59_700, 50) \
        + off_at(62_200, 70, 71, new=80)
    assert device_off_spans(fx, 0, 72_000, turret=True) == [[29_600, 59_700], [62_200, None]]


def test_a_turret_attack_is_not_off():
    # an enemy in front of it: an effect 8 ms before the boot stops, its partner stopped 0.75 s later; the
    # spawn effect never stops. Then the boot replays, and later the turret is switched off for real.
    fx = TURRET + [(5_237, "play", 30, 62), (5_245, "stop", 2, None), (5_245, "play", 31, 63),
                   (5_995, "stop", 31, None), (6_456, "stop", 30, None), (7_323, "play", 32, 11)] \
        + off_at(43_900, 1, 32)
    assert device_off_spans(fx, 0, 90_000, turret=True) == [[43_900, None]]


def test_a_turret_destroyed_or_picked_up_soon_after_is_not_off():
    fx = TURRET + off_at(10_000, 1, 2)
    assert device_off_spans(fx, 0, 11_000, turret=True) == []


def test_an_alarmbot_going_off_is_not_switched_off():
    # its boot stops as the trigger effect starts; the trigger stops with a one-shot (the alarm), even 1.6 s on
    fx = ALARMBOT + off_at(80_500, 1, container=64) + [(82_106, "stop", 50, None), (82_106, "oneshot", None, 65)]
    assert device_off_spans(fx, 0, 82_800, turret=False) == []


def test_an_alarmbot_switched_off():
    fx = ALARMBOT + off_at(36_100, 1)
    assert device_off_spans(fx, 0, 113_000, turret=False) == [[36_100, None]]


def test_a_device_without_its_first_effect_never_reads_off():
    assert device_off_spans([(0, "oneshot", None, 9)] + off_at(5_000, 1), 0, 60_000, turret=True) == []


def test_another_devices_events_under_a_reused_guid_are_ignored():
    fx = [(t + 100_000, kind, i, c) for t, kind, i, c in TURRET] + off_at(50_000, 1, 2)
    assert device_off_spans(fx, 100_000, 200_000, turret=True) == []


def kj_play(t_ms, guid, effect_id, container):
    return {"type": "rpc_received", "time_ms": t_ms, "actor_net_guid": guid,
            "function_name": "MulticastPlayContinuousEffect", "payload": {"EffectId": effect_id, "EffectContainer": container}}


def test_the_condenser_stores_a_turrets_off_spans_in_round_seconds(tmp_path):
    turret, bot = 90, 91
    rows = [spawned(20_000, turret, "Default__Pawn_Killjoy_E_Turret_C", 0, 0), instigated(20_000, turret, 107),
            kj_play(20_000, turret, 1, 10), kj_play(22_000, turret, 2, 11),
            kj_play(31_600, turret, 50, 60), stop(31_600, turret, 1), stop(31_600, turret, 2),
            stop(35_000, turret, 50), kj_play(35_000, turret, 3, 10), kj_play(35_000, turret, 4, 11),
            kj_play(41_000, turret, 51, 60), stop(41_000, turret, 3), stop(41_000, turret, 4),
            closed(65_000, turret),                                            # after the playback window
            spawned(20_000, bot, "Default__Pawn_Killjoy_Q_StealthAlarmbot_C", 0, 0), instigated(20_000, bot, 107),
            kj_play(21_700, bot, 1, 21), closed(40_000, bot)]
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["time_ms"])), encoding="utf-8")
    extras = build_extras(path, players(), WINDOWS, GAME_MAP, {**AGENTS, "Killjoy": "Killjoy"}, lambda t_ms: {})
    rows_by_name = {a["name"]: a for a in extras.rounds[1]["abilities"]}
    assert rows_by_name["E_Turret"]["off"] == [[21.6, 25.0], [31.0, None]]
    assert "off" not in rows_by_name["Q_StealthAlarmbot"]
    assert extras.report["kj_off_spans"] == 2


# ---- movement-ability casts (docs/superpowers/plans/2026-10-05-height-slopes.md, Task 1b)

AGENTS_MOVE = {**AGENTS, "Wushu": "Jett", "Terra": "Waylay"}


def move_play(t_ms, pawn, floats, triggered=True, context=(4000,)):
    """The effect a dash plays on its caster's character as the dash starts: its float tags tell which one."""
    payload = {"EffectId": t_ms, "EffectContainer": 900,
               "FunctionObjectValues": [{"Name": {"TagName": "FXC.EffectContext"}, "Value": v} for v in context],
               "FunctionFloatValues": [{"Name": {"TagName": k}, "Value": v} for k, v in floats.items()]}
    if triggered:
        payload["ClientControllerThatTriggered"] = 46
    return {"type": "rpc_received", "time_ms": t_ms, "actor_net_guid": pawn, "function_name": "MulticastPlayContinuousEffect",
            "payload": payload}


def run_moves(tmp_path, rows):
    table = players()
    table.agents[8] = "Waylay"         # slot 3 is Jett (pawn 103), slot 8 Waylay (pawn 108)
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return build_extras(path, table, WINDOWS, GAME_MAP, AGENTS_MOVE, lambda t_ms: {})


JETT_DASH = {"FXC.TimedStateDuration": 0.45}
WAYLAY_DASH = {"FXC.Angle": 4.78, "FXC.TimedStateDuration": 0.4}


def test_a_dash_is_one_cast_with_its_casters_slot_and_its_time(tmp_path):
    extras = run_moves(tmp_path, [move_play(22_000, 103, JETT_DASH),
                                  move_play(30_000, 108, WAYLAY_DASH), move_play(30_000, 108, WAYLAY_DASH),
                                  move_play(30_150, 108, WAYLAY_DASH),     # within 0.2 s: the same cast
                                  move_play(30_550, 108, WAYLAY_DASH)])    # her second dash
    assert extras.rounds[1]["casts"] == [{"t": 12.0, "slot": 3, "code": "Wushu", "name": "E_Dash"},
                                         {"t": 20.0, "slot": 8, "code": "Terra", "name": "Q_DoubleDash"},
                                         {"t": 20.55, "slot": 8, "code": "Terra", "name": "Q_DoubleDash"}]
    assert extras.report["movement_casts"] == 3


def test_other_effects_and_other_agents_give_no_cast(tmp_path):
    extras = run_moves(tmp_path, [
        move_play(22_000, 103, WAYLAY_DASH),                                  # Waylay's tags on Jett
        move_play(23_000, 103, JETT_DASH, triggered=False),                   # not triggered by a player
        move_play(24_000, 103, {"FXC.TimedStateDuration": 0.2}),              # another duration
        move_play(25_000, 103, {**JETT_DASH, "FXC.Intensity": 1.0}),          # another tag beside it
        move_play(26_000, 100, JETT_DASH),                                    # Omen
        move_play(27_000, 999, JETT_DASH)])                                   # nobody's character
    assert "casts" not in extras.rounds.get(1, {})
    assert extras.report.get("movement_casts", 0) == 0


def test_casts_are_util_entries_in_time_order_and_read_back():
    from app.replays.extras import rounds_extras, util_entries

    rnd = {"abilities": [], "shots": [],
           "casts": [{"t": 4.0, "slot": 3, "code": "Wushu", "name": "E_Dash"},
                     {"t": 2.0, "slot": 8, "code": "Terra", "name": "Q_DoubleDash"}]}
    util = util_entries(rnd)
    assert [e for e in util if e["k"] == "cast"] == [
        {"k": "cast", "t": 2.0, "by": 8, "code": "Terra", "name": "Q_DoubleDash"},
        {"k": "cast", "t": 4.0, "by": 3, "code": "Wushu", "name": "E_Dash"}]
    assert sorted(rounds_extras(util)["casts"], key=lambda c: c["t"]) == sorted(rnd["casts"], key=lambda c: c["t"])


def test_every_condensed_round_says_its_casts_were_recorded(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from replay_synthetic import SyntheticMatch

    from app.replays import condense as cd

    match = SyntheticMatch()
    replay = cd.condense_export_dir(match.write(tmp_path / "export"), source_sha256=match.source_sha256)
    assert replay.rounds and all(blob["movement_casts"] == 1 for blob in replay.rounds.values()), \
        "a round nobody cast in is known to have had no cast"
    assert not any(e["k"] == "cast" for blob in replay.rounds.values() for e in blob["util"])


# ---------------------------------------------------------------- Fault Line's charge and Arc Rose's trigger (2026-10-18)

AGENTS_FL = {**AGENTS5, "Breach": "Breach", "Nox": "Vyse"}


def vyse_and_breach() -> PlayerTable:
    table = two_cyphers()
    table.agents[3] = "Vyse"            # slot 8 is already Breach (pawn 108)
    return table


def fissure(t_ms, guid):
    return [spawned(t_ms, guid, "Default__GameObject_Breach_E_SweetSpotFissure_C", 0, 0), instigated(t_ms, guid, 108),
            closed(t_ms + 6_100, guid)]


def test_fault_lines_charge_is_learnt_from_breachs_charge_and_full_charge_effects(tmp_path):
    rows = [
        effect(18_500, 108, 1, [5000], container=700), effect(19_500, 108, 2, [5000], container=701), *fissure(20_000, 41),
        effect(28_000, 108, 3, [5000], container=700), effect(29_000, 108, 4, [5000], container=701), *fissure(30_000, 42),
        effect(39_600, 108, 5, [5000], container=700), *fissure(40_000, 43),            # released 0.4 s into a charge
        # a busy pair of effects a second apart, far from any release: never taken for the charge
        *[e for i in range(5) for e in (effect(45_000 + 2_000 * i, 108, 10 + i, [5000], container=900),
                                         effect(46_000 + 2_000 * i, 108, 20 + i, [5000], container=901))],
    ]
    lines = [a for a in run5(tmp_path, rows, table=vyse_and_breach()).rounds[1]["abilities"] if a["name"] == "E_SweetSpotFissure"]
    assert [a["charge"] for a in lines] == [1.0, 1.0, 0.4]


def test_without_full_charges_to_learn_from_fault_line_has_no_charge(tmp_path):
    rows = [effect(39_600, 108, 5, [5000], container=700), *fissure(40_000, 43)]
    [line] = [a for a in run5(tmp_path, rows, table=vyse_and_breach()).rounds[1]["abilities"]
              if a["name"] == "E_SweetSpotFissure"]
    assert "charge" not in line


# ---------------------------------------------------------------- ult orb captures let go (2026-10-18)

ORB = "Default__UltPointOrb_C"


def orb_pickup(t_ms, orb, pawn):
    return {"type": "rpc_received", "time_ms": t_ms, "actor_net_guid": 264, "function_name": "OrbPickedUpRPC",
            "payload": {"CollectableOrb": orb, "OrbGatherer": pawn}}


def capture(t_ms, orb, pawn, effect_id):
    """A completed capture: the channel's two effects on the capturer, the pickup (sent twice) 2 s later."""
    return [effect(t_ms, pawn, effect_id, [pawn], container=800), effect(t_ms + 15, pawn, effect_id + 1, [7], container=801),
            orb_pickup(t_ms + 2_000, orb, pawn), orb_pickup(t_ms + 2_000, orb, pawn), closed(t_ms + 2_000, orb)]


def test_a_capture_let_go_is_learnt_from_the_completed_ones_and_placed_on_its_orb(tmp_path):
    rows = [spawned(11_000, 50, ORB, 0, 0), spawned(11_000, 51, ORB, 5000, 0), spawned(11_000, 52, ORB, 0, 3000),
            *capture(18_000, 50, 102, 1), *capture(30_000, 51, 103, 3),
            effect(25_000, 102, 7, [102], container=900),                          # unrelated: never a try
            effect(40_000, 104, 9, [104], container=800), stop(40_700, 104, 9),    # slot 4 lets go, on orb 52
            effect(45_000, 105, 9, [105], container=800)]                          # slot 5 nowhere near an orb
    out = run5(tmp_path, rows, positions={4: (0.0, 3050.0), 5: (9000.0, 9000.0)})
    u, v = GAME_MAP.to_uv(0.0, 3000.0)
    assert out.rounds[1]["orb_tries"] == [{"t": 30.0, "t1": 30.7, "slot": 4, "u": u, "v": v}]
    assert out.report["orb tries"] == 1 and out.report["orb tries: no orb within reach"] == 1


def test_a_try_let_go_after_the_round_ends_stops_at_its_end_and_a_reused_guid_is_a_new_orb(tmp_path):
    # orb 50 is taken in round 1, then its guid comes back in round 2 for an orb elsewhere; slot 4 starts a capture
    # on that one a second before round 2 ends and lets go after it
    end = WINDOWS[1][2]
    rows = [spawned(11_000, 50, ORB, 0, 0), *capture(18_000, 50, 102, 1), spawned(11_000, 51, ORB, 5000, 0),
            *capture(30_000, 51, 103, 3), spawned(70_000, 50, ORB, 0, 3000),
            effect(end - 1_000, 104, 9, [104], container=800), stop(end + 5_000, 104, 9)]
    out = run5(tmp_path, rows, positions={4: (0.0, 3050.0)})
    [attempt] = out.rounds[2]["orb_tries"]
    assert (attempt["u"], attempt["v"]) == GAME_MAP.to_uv(0.0, 3000.0)
    assert attempt["t1"] == round((end - WINDOWS[1][0]) / 1000, 3)


def test_with_one_capture_to_learn_from_no_try_is_found(tmp_path):
    rows = [spawned(11_000, 50, ORB, 0, 0), spawned(11_000, 52, ORB, 0, 3000), *capture(18_000, 50, 102, 1),
            effect(40_000, 104, 9, [104], container=800)]
    assert "orb_tries" not in run5(tmp_path, rows, positions={4: (0.0, 3050.0)}).rounds.get(1, {})


def test_an_arc_rose_flashes_half_a_second_after_vyse_triggers_it(tmp_path):
    rose = "Default__GameObject_Nox_StealthingTrap_Flash_2_C"
    rows = [spawned(12_000, 70, rose, 0, 0), instigated(12_000, 70, 103),
            {"type": "rpc_received", "time_ms": 12_200, "actor_net_guid": 70, "function_name": "MulticastPlayOneShotEffect",
             "payload": {"EffectContainer": 4219}},                                    # its deploy, not a trigger
            effect(30_000, 70, 9, [], container=5513), closed(37_000, 70),
            spawned(13_000, 71, rose, 100, 0), instigated(13_000, 71, 103), closed(55_000, 71)]   # never triggered
    roses = run5(tmp_path, rows, table=vyse_and_breach()).rounds[1]["abilities"]
    assert [r.get("fx") for r in roses] == [[20.5], None]


# ---------------------------------------------------------------- Veto's Interceptor (2026-10-18)

INTERCEPTOR = "Default__Pawn_Pine_E_RadEater_C"
BOOM_BOT = "Default__Pawn_Clay_E_Boomba_C"


def veto_and_raze() -> PlayerTable:
    """Slot 2 plays Veto (side A, with slot 0's Omen); slot 7 plays Raze (side B)."""
    table = players()
    table.agents[2], table.agents[7] = "Veto", "Raze"
    return table


def goes_active(t_ms, guid, seconds=9):
    return {"type": "rpc_received", "time_ms": t_ms, "actor_net_guid": guid, "function_name": "MulticastPlayContinuousEffect",
            "payload": {"EffectId": 1, "EffectContainer": 7,
                        "FunctionFloatValues": [{"Name": {"TagName": "FXC.Duration"}, "Value": seconds}]}}


def run_veto(tmp_path, rows):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["time_ms"])), encoding="utf-8")
    teams = {s: "A" if s < 5 else "B" for s in range(10)}
    return build_extras(path, veto_and_raze(), WINDOWS, GAME_MAP, {**AGENTS, "Pine": "Veto", "Clay": "Raze"},
                        lambda t_ms: {}, teams=teams)


def bot(guid, t0, t1, x, by=107):
    return [spawned(t0, guid, BOOM_BOT, x, 0), instigated(t0, guid, by), closed(t1, guid)]


def test_an_interceptor_is_set_off_and_stops_enemy_utility_cut_short_in_its_range(tmp_path):
    rows = [spawned(20_000, 50, INTERCEPTOR, 0, 0), instigated(20_000, 50, 102), goes_active(22_000, 50),
            closed(31_000, 50),
            *bot(60, 21_000, 22_100, 500),            # stopped: 1.1 s of a bot's 5, 5 m off
            *bot(62, 21_000, 23_000, 5000),           # cut short too, but 50 m off
            *bot(65, 18_000, 23_000, 300),            # ran its full 5 s beside it
            *bot(66, 21_000, 22_500, 400, by=100),    # a teammate's (Omen's slot, for the test)
            *bot(67, 32_000, 33_000, 200),            # after the Interceptor had gone
            *bot(61, 75_000, 80_000, 9000)]           # round 2: the longest life a bot has
    out = run_veto(tmp_path, rows)
    [interceptor] = [a for a in out.rounds[1]["abilities"] if a["name"] == "E_RadEater"]
    assert interceptor["active"] == 12.0
    assert interceptor["intercepts"] == [[12.1, *GAME_MAP.to_uv(500, 0)]]
    assert out.report["interceptors_active"] == 1 and out.report["intercepts"] == 1


def test_an_interceptor_counts_one_use_once(tmp_path):
    shell, cluster = "Default__Projectile_Clay_4_Projectile_Primary_C", "Default__GameObject_Clay_4_Cluster_C"

    def actor(guid, kind, t0, t1, x):
        return [spawned(t0, guid, kind, x, 0), instigated(t0, guid, 107), closed(t1, guid)]
    rows = [spawned(20_000, 50, INTERCEPTOR, 0, 0), instigated(20_000, 50, 102), goes_active(22_000, 50),
            closed(31_000, 50),
            *actor(70, shell, 21_000, 22_000, 300),     # it landed: its object spawned 0.1 s after, only a stage
            *actor(71, cluster, 22_100, 22_400, 300),   # stopped
            *actor(73, cluster, 22_000, 23_000, 400), *actor(74, cluster, 22_000, 23_000, 450),   # one use, two parts
            *actor(75, shell, 75_000, 80_000, 9000), *actor(76, cluster, 75_000, 80_000, 9000)]   # round 2: full lives
    [interceptor] = [a for a in run_veto(tmp_path, rows).rounds[1]["abilities"] if a["name"] == "E_RadEater"]
    assert [hit[0] for hit in interceptor["intercepts"]] == [12.4, 13.0]


def test_an_interceptor_never_set_off_has_no_active_time(tmp_path):
    rows = [spawned(20_000, 50, INTERCEPTOR, 0, 0), instigated(20_000, 50, 102), goes_active(21_000, 50, seconds=0.5),
            *bot(60, 21_000, 22_100, 500), *bot(61, 75_000, 80_000, 9000)]
    out = run_veto(tmp_path, rows)
    [interceptor] = [a for a in out.rounds[1]["abilities"] if a["name"] == "E_RadEater"]
    assert "active" not in interceptor and "intercepts" not in interceptor
    assert out.report["interceptors_never_active"] == 1


# ---------------------------------------------------------------- trip length mark, carried pairs, pair history

TRIP_FIRST = "Default__GameObject_Gumshoe_4_TripWire_C"
TRIP_SECOND = "Default__GameObject_Gumshoe_4_TripWire_SecondWire_C"
TRIP_EQUIP = [spawned(1_000, 500, "Default__Ability_Gumshoe_4_TripWire_C", 0, 0), effect(2_000, 101, 1, [500])]


def placement9(t_ms, equip, listed):
    """A schema 9 placement: `Actors` is a list of `{"NetGuid": {...}}` (the shape the real export has)."""
    return {"type": "export_group_received", "time_ms": t_ms, "actor_net_guid": equip, "is_actor": False,
            "payload": {"Actors": [{"NetGuid": {"net_guid": g, "path": None}} for g in listed]}}


def trip_rows(t_ms, first, second, x, y, dx, dy, claim=True, equip=500):
    rows = [spawned(t_ms, first, TRIP_FIRST, x, y, z=100.0), spawned(t_ms, second, TRIP_SECOND, x + dx, y + dy, z=100.0)]
    return rows + ([placement9(t_ms, equip, [second])] if claim else [])


def run_trips(tmp_path, rows):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return build_extras(path, two_cyphers(), WINDOWS, GAME_MAP, AGENTS5, lambda t_ms: {})


def firsts(extras, n):
    return [a for a in extras.rounds.get(n, {"abilities": []})["abilities"] if a["name"] == "4_TripWire"]


def test_a_14_m_pair_has_its_end_and_no_long_mark(tmp_path):
    extras = run_trips(tmp_path, [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400)])
    [wire] = firsts(extras, 1)
    assert wire["end"] == [round((1400 * 1e-4 + 0.5) * 10_000), round((1000 * 1e-4 + 0.5) * 10_000)]
    assert "long" not in wire
    assert extras.report["wires_paired"] == 1 and extras.report.get("wires_too_long", 0) == 0


def test_a_16_5_m_pair_keeps_its_end_and_is_marked_long(tmp_path):
    extras = run_trips(tmp_path, [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1650)])
    [wire] = firsts(extras, 1)
    assert wire["end"] is not None and wire["long"] is True
    assert extras.report["wires_paired"] == 1 and extras.report["wires_too_long"] == 1


def test_exactly_16_m_is_not_long(tmp_path):
    # a 3-4-5 pair: 960 by 1280 world units is exactly 1600 = 16.0 m
    extras = run_trips(tmp_path, [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 960, 1280)])
    [wire] = firsts(extras, 1)
    assert wire["end"] is not None and "long" not in wire
    assert extras.report.get("wires_too_long", 0) == 0


def test_a_carried_pair_whose_round_1_pair_was_long_is_long_too(tmp_path):
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1700), closed(59_000, 700), closed(59_000, 701),
            *trip_rows(65_000, 700, 701, 1000, 0, 0, 1700, claim=False)]
    extras = run_trips(tmp_path, rows)
    [first], [second] = firsts(extras, 1), firsts(extras, 2)
    assert first["long"] is True and second["long"] is True and second["end"] == first["end"]
    assert extras.report["wires_carried"] == 1 and extras.report["wires_too_long"] == 2


def test_the_same_anchors_at_round_start_with_no_claim_take_the_previous_pair(tmp_path):
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400), closed(59_000, 700), closed(59_000, 701),
            *trip_rows(65_000, 700, 701, 1000, 0, 0, 1400, claim=False)]
    extras = run_trips(tmp_path, rows)
    [first], [second] = firsts(extras, 1), firsts(extras, 2)
    assert second["end"] == first["end"] and "long" not in second
    assert second["t0"] == 0.0
    assert extras.report["wires_carried"] == 1 and extras.report["wires_paired"] == 1


def test_two_carried_pairs_with_a_tie_each_keep_their_own_partner(tmp_path):
    one = [*trip_rows(8_000, 700, 701, 1000, 0, 0, 1400), *trip_rows(8_400, 710, 711, 3000, 0, 0, 1000)]
    closes = [closed(59_000, g) for g in (700, 701, 710, 711)]
    # round 2: the second pair's anchors come first, all four on one tick, no claims
    again = [*trip_rows(65_000, 710, 711, 3000, 0, 0, 1000, claim=False),
             *trip_rows(65_000, 700, 701, 1000, 0, 0, 1400, claim=False)]
    extras = run_trips(tmp_path, [*TRIP_EQUIP, *one, *closes, *again])
    by_place = {(a["u"], a["v"]): a["end"] for a in firsts(extras, 2)}
    was = {(a["u"], a["v"]): a["end"] for a in firsts(extras, 1)}
    assert len(by_place) == 2 and by_place == was
    assert extras.report["wires_carried"] == 2


def test_carried_anchors_whose_partner_is_not_known_get_no_end(tmp_path):
    # round 1's first anchor was never paired; round 2 re-spawns it
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400, claim=False), closed(59_000, 700),
            closed(59_000, 701), *trip_rows(65_000, 700, 701, 1000, 0, 0, 1400, claim=False)]
    extras = run_trips(tmp_path, rows)
    assert all("end" not in a for a in firsts(extras, 2)) and extras.report.get("wires_carried", 0) == 0
    # round 1 had a pair, but round 2's anchors are other guids somewhere else
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400), closed(59_000, 700), closed(59_000, 701),
            *trip_rows(65_000, 800, 801, -4000, 500, 0, 1400, claim=False)]
    extras = run_trips(tmp_path, rows)
    assert all("end" not in a for a in firsts(extras, 2)) and extras.report.get("wires_carried", 0) == 0


def test_new_guids_at_the_same_positions_are_paired_by_position(tmp_path):
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400), closed(59_000, 700), closed(59_000, 701),
            *trip_rows(65_000, 800, 801, 1001, 0.8, 0, 1400, claim=False)]
    extras = run_trips(tmp_path, rows)
    [first], [second] = firsts(extras, 1), firsts(extras, 2)
    # the end is round 2's own second anchor (801, 0.8 units on), never round 1's copied over
    assert second["id"] == 800 and second["end"] == list(GAME_MAP.to_uv(1001, 1400.8)) != first["end"]
    assert extras.report["wires_carried"] == 1


def test_a_reused_anchor_guid_leaves_round_1s_pair_in_round_1_with_its_own_facts(tmp_path):
    # round 2's wire is the other Cypher's (slot 6), placed with their own equippable (600)
    rows = [*TRIP_EQUIP, spawned(1_000, 600, "Default__Ability_Gumshoe_4_TripWire_C", 0, 0),
            effect(2_100, 106, 2, [600]),
            *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400), instigated(8_000, 700, 101),
            damage(30_000, 701, 999, lethal=True), closed(59_000, 700), closed(59_000, 701),
            *trip_rows(65_000, 700, 701, -2000, 300, 0, 1200, equip=600), instigated(65_000, 700, 106)]
    extras = run_trips(tmp_path, rows)
    [first], [second] = firsts(extras, 1), firsts(extras, 2)
    assert (first["slot"], first["owner_by"], first["t1"], first["gone"]) == (1, "instigator", 49.0, 20.0)
    assert (second["slot"], second["owner_by"], second["t1"]) == (6, "instigator", None)
    assert "gone" not in second
    assert first["end"] != second["end"]
    # the same with no close rows at all: a later spawn ends the earlier life
    rows = [r for r in rows if r["type"] != "actor_closed"]
    extras = run_trips(tmp_path, rows)
    assert len(firsts(extras, 1)) == 1 and len(firsts(extras, 2)) == 1


def test_a_trip_alive_across_the_round_boundary_is_in_both_rounds_still_paired(tmp_path):
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400)]      # no close, no round 2 spawn rows
    extras = run_trips(tmp_path, rows)
    [first], [second] = firsts(extras, 1), firsts(extras, 2)
    assert second["end"] == first["end"] and second["t0"] == 0.0 and second["t1"] is None
    assert second["slot"] == first["slot"] == 1
    assert {a["name"] for a in extras.rounds[2]["abilities"]} == {"4_TripWire", "4_TripWire_SecondWire"}
    # closed in round 1: it does not reach round 2
    extras = run_trips(tmp_path, [*rows, closed(59_000, 700), closed(59_000, 701)])
    assert not firsts(extras, 2)


# ---------------------------------------------------------------- branch review 2 (R1-R4)

def test_a_reused_first_guid_somewhere_else_is_not_carried_and_a_lone_first_respawn_gets_no_end(tmp_path):
    # R1: round 1's 14 m wire is closed; round 2 reuses its first anchor's guid 40 m away for the other Cypher
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400), closed(59_000, 700), closed(59_000, 701),
            spawned(65_000, 700, TRIP_FIRST, -3000, 0, z=100.0), instigated(65_000, 700, 106),
            spawned(65_000, 720, TRIP_SECOND, -3000, 1000, z=100.0)]
    extras = run_trips(tmp_path, rows)
    [wire] = firsts(extras, 2)
    assert "end" not in wire and "long" not in wire
    assert extras.report.get("wires_carried", 0) == 0 and extras.report.get("wires_too_long", 0) == 0
    # the first anchor alone re-spawns in place: no second anchor corroborates it, so no stored old endpoint
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400), closed(59_000, 700), closed(59_000, 701),
            spawned(65_000, 700, TRIP_FIRST, 1000, 0, z=100.0)]
    [wire] = firsts(run_trips(tmp_path, rows), 2)
    assert "end" not in wire


def test_a_carried_anchor_of_another_owner_never_takes_the_pair(tmp_path):
    # both anchors re-spawn in place, but the first one's Instigator is the other Cypher (slot 6)
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400), closed(59_000, 700), closed(59_000, 701),
            *trip_rows(65_000, 700, 701, 1000, 0, 0, 1400, claim=False), instigated(65_000, 700, 106)]
    extras = run_trips(tmp_path, rows)
    assert all("end" not in a for a in firsts(extras, 2)) and extras.report.get("wires_carried", 0) == 0


def test_two_cyphers_placing_in_the_same_tick_each_keep_their_end(tmp_path):
    # R2: two schema 9 placements 10 ms apart, one per Cypher; each first anchor names its owner by Instigator
    rows = [spawned(1_000, 500, "Default__Ability_Gumshoe_4_TripWire_C", 0, 0),
            spawned(1_000, 600, "Default__Ability_Gumshoe_4_TripWire_C", 9000, 9000),
            effect(2_000, 101, 1, [500]), effect(2_100, 106, 2, [600]),
            *trip_rows(8_000, 700, 701, 1000, 0, 0, 1000, equip=500), instigated(8_000, 700, 101),
            *trip_rows(8_010, 710, 711, 3000, 0, 0, 1200, equip=600), instigated(8_010, 710, 106)]
    extras = run_trips(tmp_path, rows)
    got = {a["id"]: (a["slot"], a.get("end")) for a in firsts(extras, 1)}
    assert got == {700: (1, list(GAME_MAP.to_uv(1000, 1000))), 710: (6, list(GAME_MAP.to_uv(3000, 1200)))}
    assert extras.report["wires_paired"] == 2


def test_a_wire_ends_when_either_anchor_closes_or_its_guid_is_reused(tmp_path):
    # R3: the far anchor closes before round 2; the first stays open. No round 2 wire.
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400), closed(59_000, 701)]
    extras = run_trips(tmp_path, rows)
    [wire] = firsts(extras, 1)
    assert wire["t1"] == 49.0 and not firsts(extras, 2)
    # the first guid reused 5 s into round 2: the carried row ends there, not open-ended
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400),
            spawned(75_000, 700, TRIP_FIRST, -3000, 0, z=100.0)]
    extras = run_trips(tmp_path, rows)
    carried = [a for a in firsts(extras, 2) if a.get("end") == list(GAME_MAP.to_uv(1000, 1400))]
    assert [a["t1"] for a in carried] == [5.0]
    [second] = [a for a in extras.rounds[2]["abilities"] if a["name"] == "4_TripWire_SecondWire"]
    assert second["t1"] is None       # the far anchor's own row keeps its own life (never closed, never reused)


def test_a_round_1_trip_going_off_keeps_its_reveal_after_round_2_reuses_its_guid(tmp_path):
    # R4: the tether-and-concuss case, then a later spawn under the first anchor's guid
    rows = [spawned(1_000, 500, "Default__Ability_Gumshoe_4_TripWire_C", 0, 0), effect(2_000, 101, 1, [500]),
            spawned(8_000, 60, "Default__GameObject_Gumshoe_4_TripWire_C", 0, 0),
            spawned(8_000, 61, "Default__GameObject_Gumshoe_4_TripWire_SecondWire_C", 0, 400), placement(8_000, 500, [61]),
            effect(22_840, 104, 5, [60], container=4895), stop(23_540, 104, 5),
            oneshot(23_540, 104, [60], 5173), closed(59_000, 60), closed(59_000, 61),
            spawned(80_000, 60, "Default__GameObject_Gumshoe_4_TripWire_C", 5000, 5000), instigated(80_000, 60, 102)]
    extras = run7(tmp_path, rows)
    assert [(r["t0"], r["slot"], r["target"]) for r in extras.rounds[1]["reveals"]] == [(13.54, 1, 4)]
    assert {(st["status"], st["slot"]) for st in extras.rounds[1]["statuses"]} == {("tethered", 1), ("concussed", 1)}


# ---------------------------------------------------------------- branch review 3 (R1-R5)

def test_a_first_anchor_never_takes_another_owners_lone_placement(tmp_path):
    # R1: two wires 10 ms apart with explicit Instigators; only Cypher 1's placement context is in the export
    rows = [spawned(1_000, 500, "Default__Ability_Gumshoe_4_TripWire_C", 0, 0),
            spawned(1_000, 600, "Default__Ability_Gumshoe_4_TripWire_C", 9000, 9000),
            effect(2_000, 101, 1, [500]), effect(2_100, 106, 2, [600]),
            *trip_rows(8_000, 700, 701, 1000, 0, 0, 1000, equip=500), instigated(8_000, 700, 101),
            *trip_rows(8_010, 710, 711, -3000, 0, 0, 1200, claim=False), instigated(8_010, 710, 106)]
    extras = run_trips(tmp_path, rows)
    got = {a["id"]: (a["slot"], a.get("end")) for a in firsts(extras, 1)}
    assert got == {700: (1, list(GAME_MAP.to_uv(1000, 1000))), 710: (6, None)}
    assert extras.report["wires_paired"] == 1 and extras.report.get("wires_too_long", 0) == 0


def test_two_ownerless_first_anchors_claiming_one_placement_pair_neither(tmp_path):
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1000),
            spawned(8_010, 710, TRIP_FIRST, -3000, 0, z=100.0)]
    extras = run_trips(tmp_path, rows)
    assert all("end" not in a for a in firsts(extras, 1))
    assert extras.report["wires_placement_shared"] == 2 and extras.report.get("wires_paired", 0) == 0


def test_a_spycam_reusing_the_far_anchors_guid_ends_the_wire(tmp_path):
    # R2: no close row for the far anchor; a camera spawns under its guid in round 2's buy phase
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400),
            spawned(65_000, 701, "Default__Pawn_Gumshoe_E_PossessableCamera_C", 4000, 4000), instigated(65_000, 701, 101)]
    extras = run_trips(tmp_path, rows)
    [wire] = firsts(extras, 1)
    assert wire["t1"] == 50.0 and not firsts(extras, 2)
    assert not [a for a in extras.rounds[2]["abilities"] if a["name"] == "4_TripWire_SecondWire"]


def test_an_ability_reusing_a_closed_trips_guid_keeps_its_status_and_the_trip_keeps_its_trigger(tmp_path):
    # R3: round 1's wire goes off, closes; round 2 reuses the first anchor's guid for a slow field
    rows = [spawned(1_000, 500, "Default__Ability_Gumshoe_4_TripWire_C", 0, 0), effect(2_000, 101, 1, [500]),
            spawned(8_000, 60, "Default__GameObject_Gumshoe_4_TripWire_C", 0, 0),
            spawned(8_000, 61, "Default__GameObject_Gumshoe_4_TripWire_SecondWire_C", 0, 400), placement(8_000, 500, [61]),
            effect(22_840, 104, 5, [60], container=4895), stop(23_540, 104, 5),
            oneshot(23_540, 104, [60], 5173), closed(59_000, 60), closed(59_000, 61),
            spawned(80_000, 60, "Default__GameObject_Thorne_4_SlowField_Production_C", 5000, 5000),
            instigated(80_000, 60, 103), effect(85_000, 104, 9, [60], container=7000), stop(87_000, 104, 9)]
    # without Sage's code the field is an unknown actor and is dropped: the earlier trip must survive that too
    extras = run7(tmp_path, rows)
    assert [(r["t0"], r["slot"], r["target"]) for r in extras.rounds[1]["reveals"]] == [(13.54, 1, 4)]
    assert "statuses" not in extras.rounds.get(2, {})
    extras = build_extras_rows(tmp_path, rows, {**AGENTS7, "Thorne": "Sage"})
    assert [(r["t0"], r["slot"], r["target"]) for r in extras.rounds[1]["reveals"]] == [(13.54, 1, 4)]
    assert [(st["status"], st["slot"], st["target"], st["t0"]) for st in extras.rounds[2]["statuses"]] == [
        ("slowed", 3, 4, 15.0)]


def test_a_carried_wire_with_fresh_guids_and_no_instigators_keeps_round_1s_owner(tmp_path):
    # R4: round 1's owner came from its placement; round 2's anchors re-spawn in place, no Instigator rows
    rows = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400), closed(59_000, 700), closed(59_000, 701),
            *trip_rows(65_000, 800, 801, 1000, 0, 0, 1400, claim=False)]
    extras = run_trips(tmp_path, rows)
    [first] = firsts(extras, 1)
    [wire] = firsts(extras, 2)
    [far] = [a for a in extras.rounds[2]["abilities"] if a["name"] == "4_TripWire_SecondWire"]
    assert first["slot"] == 1 and (wire["slot"], wire["owner_by"]) == (1, "carried")
    assert far["slot"] == 1 and wire["end"] == first["end"]


def test_an_anchor_closed_in_the_buy_phase_never_takes_the_carry_from_the_live_one(tmp_path):
    # R5: the old first anchor re-spawns in place and closes before round 2 starts; the live wire has fresh guids
    base = [*TRIP_EQUIP, *trip_rows(8_000, 700, 701, 1000, 0, 0, 1400), closed(59_000, 700), closed(59_000, 701)]
    live = trip_rows(65_000, 800, 801, 1000, 0, 0, 1400, claim=False)
    extras = run_trips(tmp_path, [*base, spawned(62_000, 700, TRIP_FIRST, 1000, 0, z=100.0), closed(63_000, 700), *live])
    [wire] = firsts(extras, 2)
    assert wire["id"] == 800 and wire["end"] == list(GAME_MAP.to_uv(1000, 1400))
    # the same on the far anchor: the closed one is never attached, so the live wire does not end at 0.0
    extras = run_trips(tmp_path, [*base, spawned(62_000, 701, TRIP_SECOND, 1000, 1400, z=100.0), closed(63_000, 701),
                                  *live])
    [wire] = firsts(extras, 2)
    assert wire["end"] == list(GAME_MAP.to_uv(1000, 1400)) and wire["t1"] is None


# ---------------------------------------------------------------- Hush (W9, CONDENSE 30): rows cut from two real exports

from app.replays.extras import read_raw  # noqa: E402

HUSH_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "replays" / "utility"
AGENTS_HUSH = {"Paprika": "Hush"}
PULL_DASH = "/Game/Characters/Paprika/S0/Ability_4/ForceModule_Paprika_4_PullDash.ForceModule_Paprika_4_PullDash_C"


def hush_rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (HUSH_FIXTURES / f"{name}.ndjson").read_text(encoding="utf-8").splitlines()]


def hush_players(pawns: dict[int, int]) -> PlayerTable:
    """`pawns`: pawn guid -> slot. Slots 0 and 1 play Hush; the rest are other agents."""
    agents = ["Hush", "Hush", "Sova", "Jett", "Sage", "Omen", "Reyna", "Killjoy", "Breach", "Viper"]
    return PlayerTable(subjects=[None] * 10, agents=agents, pawn_slot=dict(pawns),
                       other_slot={200 + s: s for s in range(10)}, pawn_changes={}, gone_ms={})


def run_hush(tmp_path, rows, pawns, teams=None, start=340_000, agents=AGENTS_HUSH):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["time_ms"])), encoding="utf-8")
    windows = [(start, start + 40_000, start + 50_000)]
    return build_extras(path, hush_players(pawns), windows, GAME_MAP, agents, lambda t_ms: {}, teams=teams)


def test_a_flow_is_one_cast_row_for_hush_beside_its_anchor_row(tmp_path):
    # 2026-11-15, 31 Flows in two exports: the move starts with FXC_Paprika_4_FreezeProjectileHUD (float tag
    # FXC.TimedStateDuration 0.35, triggered by the player) in the ms the anchor spawns. FXC_Paprika_4_Cast (0.7, the
    # windup) is the same Flow 0.70 s earlier and is not a cast of its own.
    extras = run_hush(tmp_path, hush_rows("hush_flow"), {708: 0}, start=340_000)
    rnd = extras.rounds[1]
    assert rnd["casts"] == [{"t": 17.477, "slot": 0, "code": "Paprika", "name": "4_PullBlast"}]
    [anchor] = [a for a in rnd["abilities"] if a["name"] == "4_PullBlast"]
    assert (anchor["id"], anchor["slot"], anchor["t0"], anchor["agent"]) == (9076, 0, 17.477, "Hush")
    assert extras.report["movement_casts"] == 1 and "statuses" not in rnd


def test_hush_effects_that_are_not_the_move_give_no_cast(tmp_path):
    rows = [r for r in hush_rows("hush_flow") if r["type"] != "actor_spawned"]
    for row in rows:                                   # the same rows on a player who is not Hush
        row["actor_net_guid"] = 102 if row["actor_net_guid"] == 708 else row["actor_net_guid"]
    extras = run_hush(tmp_path, rows, {102: 2})
    assert "casts" not in extras.rounds.get(1, {})
    # and without the move's own effect there is no cast: the windup alone (0.7) is not one
    kept = [r for r in hush_rows("hush_flow") if "FreezeProjectileHUD" not in json.dumps(r)]
    assert "casts" not in run_hush(tmp_path, kept, {708: 0}).rounds[1]


def test_a_force_row_keeps_its_module_path_and_length_while_forces_still_see_the_bare_guid(tmp_path):
    path = tmp_path / "events.ndjson"
    rows = hush_rows("hush_flow")
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    raw = read_raw(path, frozenset({708}))
    # the StopAerialMovement row names the equippable (1936), which is no actor: neither list keeps it
    assert raw.forces == [(357477, 708, 9076)]
    assert raw.force_modules == [(357477, 708, 9076, PULL_DASH, 0.4)]
    # schema 9 wrote the module as {"net_guid", "path"}; the shared reader still turns every other reference into a
    # bare guid, and a module written as a bare guid (schema 8) has no path to keep
    old = json.loads(json.dumps(rows))
    for row in old:
        if row.get("function_name") == "NetMulticastApplyForceModule":
            row["payload"]["Module"] = row["payload"]["Module"]["net_guid"]
            row["payload"].pop("Duration", None)
    path.write_text("".join(json.dumps(r) + "\n" for r in old), encoding="utf-8")
    raw = read_raw(path, frozenset({708}))
    assert raw.forces == [(357477, 708, 9076)] and raw.force_modules == []
    # a path with no length keeps None
    odd = json.loads(json.dumps(rows))
    for row in odd:
        if row.get("function_name") == "NetMulticastApplyForceModule":
            row["payload"].pop("Duration", None)
    path.write_text("".join(json.dumps(r) + "\n" for r in odd), encoding="utf-8")
    assert read_raw(path, frozenset({708})).force_modules == [(357477, 708, 9076, PULL_DASH, None)]


def pulled_rows(extras):
    return [(s["target"], s["t0"], s["t1"], s["status"], s["slot"], s["name"], s["from"], s.get("friendly", False))
            for s in extras.rounds[1].get("statuses", [])]


def test_a_displacement_pulls_each_victim_for_the_modules_own_length(tmp_path):
    # ee6e0ba7 at 56.073 s: one area (owner pawn 706) pulls two players, Duration 0.5 on each force row
    teams = {0: "A", 1: "B", 2: "B"}
    extras = run_hush(tmp_path, hush_rows("hush_displacement"), {706: 0, 1204: 1, 1422: 2}, teams=teams, start=40_000)
    assert pulled_rows(extras) == [(1, 16.073, 16.573, "pulled", 0, "E_RedirectPullArea", "force", False),
                                   (2, 16.073, 16.573, "pulled", 0, "E_RedirectPullArea", "force", False)]
    assert all("src" not in s for s in extras.rounds[1]["statuses"]), "only a levitated row names its zone"
    [area] = extras.rounds[1]["abilities"]
    assert (area["name"], area["slot"], area["agent"]) == ("E_RedirectPullArea", 0, "Hush")


def test_a_displacement_that_pulls_a_teammate_flags_it_friendly_and_a_second_touch_extends_the_run(tmp_path):
    rows = hush_rows("hush_displacement")
    again = json.loads(json.dumps(next(r for r in rows if r.get("actor_net_guid") == 1204 and r["type"] == "rpc_received")))
    again["time_ms"] += 250
    teams = {0: "A", 1: "A", 2: "B"}
    extras = run_hush(tmp_path, [*rows, again], {706: 0, 1204: 1, 1422: 2}, teams=teams, start=40_000)
    assert pulled_rows(extras) == [(1, 16.073, 16.823, "pulled", 0, "E_RedirectPullArea", "force", True),
                                   (2, 16.073, 16.573, "pulled", 0, "E_RedirectPullArea", "force", False)]


def test_a_pull_row_without_a_length_lasts_the_measured_half_second(tmp_path):
    rows = hush_rows("hush_displacement")
    for row in rows:
        if row["type"] == "rpc_received":
            row["payload"].pop("Duration")
    extras = run_hush(tmp_path, rows, {706: 0, 1204: 1, 1422: 2}, teams={0: "A", 1: "B", 2: "B"}, start=40_000)
    assert [(r[0], r[2]) for r in pulled_rows(extras)] == [(1, 16.573), (2, 16.573)]


def concuss_rows(name="hush_displacement_concuss"):
    return hush_rows(name)


def statuses_of(extras, status):
    return [(s["target"], s["t0"], s["t1"], s["slot"], s["code"], s["name"], s["from"], s.get("friendly", False))
            for s in extras.rounds[1].get("statuses", []) if s["status"] == status]


CONCUSS_PAWNS = {706: 0, 1204: 1, 1422: 2}
CONCUSS_TEAMS = {0: "A", 1: "B", 2: "B"}


def run_concuss(tmp_path, rows, teams=CONCUSS_TEAMS, pawns=CONCUSS_PAWNS, agents=AGENTS_HUSH):
    return run_hush(tmp_path, rows, pawns, teams=teams, start=40_000, agents=agents)


def test_a_pull_that_hits_something_concusses_for_as_long_as_the_effect_plays(tmp_path):
    # ee6e0ba7 at 56.073 s (A1, 2026-11-15): both pulled players are stopped by a wall; each gets FXC_Global_ConcussedWavy
    # naming Hush's pawn, in the ms of its first blocked move (+297 and +414 ms after the force row), and it plays 1.8 s
    # and 1.6 s (its stop). The pulled rows are the same as without the concuss.
    extras = run_concuss(tmp_path, concuss_rows())
    assert statuses_of(extras, "concussed") == [
        (1, 16.370, 18.169, 0, "Paprika", "E_RedirectPullArea", "force", False),
        (2, 16.487, 18.089, 0, "Paprika", "E_RedirectPullArea", "force", False)]
    assert [r for r in pulled_rows(extras) if r[3] == "pulled"] == pulled_rows(
        run_concuss(tmp_path, hush_rows("hush_displacement")))
    [area] = extras.rounds[1]["abilities"]
    assert area["name"] == "E_RedirectPullArea"
    assert all("src" not in s for s in extras.rounds[1]["statuses"])


def test_a_pull_that_hits_nothing_is_only_a_pull(tmp_path):
    # the same two pulls, the second player's concuss and blocked moves removed: pulled twice, concussed once
    rows = [r for r in concuss_rows() if not (r["actor_net_guid"] == 1422 and r["type"] == "rpc_received"
                                               and r["function_name"] != "NetMulticastApplyForceModule")]
    extras = run_concuss(tmp_path, rows)
    assert [s[0] for s in statuses_of(extras, "concussed")] == [1]
    assert [s[0] for s in statuses_of(extras, "pulled")] == [1, 2]
    bare = run_concuss(tmp_path, [r for r in concuss_rows() if "Concussed" not in json.dumps(r)
                                  and r.get("function_name") != "MulticastStopContinuousEffect"
                                  and r.get("function_name") != "MulticastPlayOneShotEffect"])
    assert statuses_of(bare, "concussed") == []
    assert pulled_rows(bare) == pulled_rows(run_concuss(tmp_path, hush_rows("hush_displacement")))


def test_a_concuss_with_no_pull_before_it_or_long_after_it_is_not_displacements(tmp_path):
    rows = concuss_rows()
    no_pull = [r for r in rows if r.get("function_name") != "NetMulticastApplyForceModule"]
    assert statuses_of(run_concuss(tmp_path, no_pull), "concussed") == []
    late = json.loads(json.dumps(rows))
    for row in late:                  # every concuss play and stop 600 ms later: past the pull's 0.5 s
        if row["type"] == "rpc_received" and row["function_name"] != "NetMulticastApplyForceModule":
            row["time_ms"] += 600
    assert statuses_of(run_concuss(tmp_path, late), "concussed") == []
    assert [s[0] for s in statuses_of(run_concuss(tmp_path, late), "pulled")] == [1, 2]


def test_a_concuss_from_another_player_is_not_hushs(tmp_path):
    rows = json.loads(json.dumps(concuss_rows()))
    for row in rows:
        for value in (row.get("payload") or {}).get("FunctionObjectValues") or []:
            if value["Value"]["net_guid"] == 706:
                value["Value"]["net_guid"] = 303                # slot 3, not the area's owner
    extras = run_concuss(tmp_path, rows, pawns={**CONCUSS_PAWNS, 303: 3})
    assert statuses_of(extras, "concussed") == []


def test_a_teammate_a_displacement_pulled_and_concussed_is_flagged_friendly(tmp_path):
    extras = run_concuss(tmp_path, concuss_rows(), teams={0: "A", 1: "A", 2: "B"})
    assert [(s[0], s[7]) for s in statuses_of(extras, "concussed")] == [(1, True), (2, False)]
    assert [(s[0], s[7]) for s in statuses_of(extras, "pulled")] == [(1, True), (2, False)]


def test_the_concuss_container_is_found_by_its_path_never_by_a_bare_guid(tmp_path):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in concuss_rows()), encoding="utf-8")
    raw = read_raw(path, frozenset({1204, 1422}))
    assert raw.concuss_containers == {4771}
    plain = json.loads(json.dumps(concuss_rows()))
    for row in plain:                 # schema 8 wrote the container as the bare guid: no path, nothing to find
        container = (row.get("payload") or {}).get("EffectContainer")
        if isinstance(container, dict):
            row["payload"]["EffectContainer"] = container["net_guid"]
    path.write_text("".join(json.dumps(r) + "\n" for r in plain), encoding="utf-8")
    assert read_raw(path, frozenset({1204, 1422})).concuss_containers == set()
    assert statuses_of(run_concuss(tmp_path, plain), "concussed") == []


def test_another_agents_concuss_keeps_its_own_shape_beside_hushs(tmp_path):
    # Tejo's Shock-Wave shape (a caster-named concuss in the 3 s after its grenade spawned, here in its own container)
    # still comes out as a caster status for the same victims, with Hush's rows added, none of it changed.
    rows = concuss_rows()
    for i, victim in enumerate((1204, 1422)):
        rows += [spawned(57_000, 900 + i, "Default__GameObject_Cashew_Q_ShellShockGrenade_C", 0, 0),
                 instigated(57_000, 900 + i, 303),
                 effect(57_200 + i, victim, 60 + i, [303], container=4999), stop(58_000 + i, victim, 60 + i)]
    extras = run_concuss(tmp_path, rows, pawns={**CONCUSS_PAWNS, 303: 3}, agents={**AGENTS_HUSH, "Cashew": "Tejo"})
    mine =[r for r in statuses_of(extras, "concussed") if r[4] == "Paprika"]
    other = [r for r in statuses_of(extras, "concussed") if r[4] == "Cashew"]
    assert len(mine) == 2
    assert [(r[0], r[1], r[2], r[3], r[6]) for r in other] == [(1, 17.2, 18.0, 3, "caster"), (2, 17.201, 18.001, 3, "caster")]


def levitated(extras):
    return sorted((s["target"], s["slot"], s["src"], s["t0"], s["t1"], s.get("friendly", False))
                  for s in extras.rounds[1].get("statuses", []) if s["status"] == "levitated")


def test_a_suspension_levitates_for_the_modules_length_and_each_row_names_its_own_zone(tmp_path):
    # 1d76e723 at 748 s: two Hushes (slots 0 and 1, opposite sides) ult 3.7 s apart; each zone levitates one player.
    # The force's Source is the zone itself (spawned in the same ms); 4 s is the payload's Duration (wiki: 4 s).
    pawns = {708: 0, 1422: 1, 1218: 2}
    teams = {0: "A", 2: "A", 1: "B"}
    extras = run_hush(tmp_path, hush_rows("hush_suspension"), pawns, teams=teams, start=740_000)
    zones = {a["id"]: a for a in extras.rounds[1]["abilities"] if a["name"] == "X_LevitateZone"}
    assert {g: z["slot"] for g, z in zones.items()} == {17052: 1, 17170: 0}
    assert levitated(extras) == [(1, 0, 17170, 11.894, 15.894, False), (2, 1, 17052, 8.212, 12.212, False)]
    for target, by, src, *_ in levitated(extras):
        assert zones[src]["slot"] == by


def test_two_suspensions_in_one_round_where_only_one_levitates_anyone(tmp_path):
    pawns = {708: 0, 1422: 1, 1218: 2}
    teams = {0: "A", 2: "A", 1: "B"}
    for zone, victim in ((17052, 2), (17170, 1)):
        rows = [r for r in hush_rows("hush_suspension")
                if not (r.get("function_name") == "NetMulticastApplyForceModule"
                        and r["payload"]["Source"]["net_guid"] != zone)]
        extras = run_hush(tmp_path, rows, pawns, teams=teams, start=740_000)
        assert [(t, src) for t, _, src, *_ in levitated(extras)] == [(victim, zone)]
        assert {a["id"] for a in extras.rounds[1]["abilities"] if a["name"] == "X_LevitateZone"} == {17052, 17170}


def test_a_suspension_on_a_teammate_is_flagged_friendly(tmp_path):
    pawns = {708: 0, 1422: 1, 1218: 2}
    extras = run_hush(tmp_path, hush_rows("hush_suspension"), pawns, teams={0: "A", 2: "A", 1: "A"}, start=740_000)
    assert [(t, f) for t, _, _, _, _, f in levitated(extras)] == [(1, True), (2, True)]


def test_a_levitate_row_without_a_length_lasts_the_wikis_four_seconds(tmp_path):
    rows = hush_rows("hush_suspension")
    for row in rows:
        if row.get("function_name") == "NetMulticastApplyForceModule":
            row["payload"].pop("Duration")
    extras = run_hush(tmp_path, rows, {708: 0, 1422: 1, 1218: 2}, teams={0: "A", 2: "A", 1: "B"}, start=740_000)
    assert [round(t1 - t0, 3) for _, _, _, t0, t1, _ in levitated(extras)] == [4.0, 4.0]


def test_the_plumbing_of_suspension_and_the_telekinesis_pickup_are_no_ability_rows(tmp_path):
    rows = [{"type": "actor_spawned", "time_ms": 341_000, "actor_net_guid": 2056, "location": {"x": 0, "y": 0, "z": 0},
             "archetype_path": "Default__GameObject_Paprika_X_ForceProxyManager_C"},
            {"type": "actor_spawned", "time_ms": 341_000, "actor_net_guid": 2057, "location": {"x": 0, "y": 0, "z": 0},
             "archetype_path": "Default__Actor_Paprika_X_ForceModuleProxy_C"},
            {"type": "actor_spawned", "time_ms": 341_000, "actor_net_guid": 2058, "location": {"x": 0, "y": 0, "z": 0},
             "archetype_path": "Default__Ability_Paprika_Passive_Telekinesis_C"}]
    extras = run_hush(tmp_path, rows, {708: 0})
    assert extras.rounds.get(1, {}).get("abilities", []) == []
    assert extras.report["paprika_plumbing_skipped"] == 1


# --- Flashbulb: the condenser's flash rows (hits already extracted; the pop is the new part)


def hush_flash(tmp_path, name, hit_slots=(6, 7)):
    """One Flashbulb cut from a real export, cast by slot 2 at 5 s of round 1, its hits on `hit_slots` in order."""
    from replay_synthetic import MATCH_UUID

    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    rows = hush_rows(name)
    shift = start + 5000 - next(r for r in rows if r["type"] == "valorant_flash_cast")["time_ms"]
    victims = iter(hit_slots)
    for row in rows:
        row["time_ms"] += shift
        if row["type"] == "valorant_flash_cast":
            row["caster_character_net_guid"], row["caster_player_state_net_guid"] = match.pawn(1, 2), None
        elif row["type"] == "valorant_flash_player_hit":
            row["target_character_net_guid"], row["target_player_state_net_guid"] = match.pawn(1, next(victims)), None
    match.extra_events += rows
    out = cd.condense_export_dir(match.write(tmp_path / "export", sorted(match.events(), key=lambda r: r["time_ms"])),
                                 source_sha256=match.source_sha256,
                                 vrf_path=match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf"))
    [flash] = [u for u in out.rounds[1]["util"] if u["k"] == "flash"]
    pop = next(r for r in rows if r["type"] == "valorant_flash_exploded")
    game_map = cd.load_maps()[out.map_name]
    where = pop["location"]
    expected = {"t": round((pop["time_ms"] - start) / 1000.0, 3),
                "u": game_map.to_uv(where["x"] * 100.0, where["y"] * 100.0)[0],
                "v": game_map.to_uv(where["x"] * 100.0, where["y"] * 100.0)[1],
                "z": int(round(where["z"] * 100.0 / 10.0)), "src": "flash_windup"}
    return flash, expected, start


@pytest.mark.parametrize("name, hits", [("hush_flashbulb_overhand", 1), ("hush_flashbulb_underhand", 2)])
def test_a_flashbulb_pops_at_its_windup_row_and_is_proven_complete(tmp_path, name, hits):
    # 22 of 22 Flashbulbs in two exports: `flash_windup_ended` rows, in metres, equal to the flight's last replicated
    # sample, 418-502 ms after it (the bulb comes to rest and pops after its windup: older than POP_FRESH_MS, but the
    # row's own place); any hit is in the pop's ms (+8 ms once)
    flash, expected, _ = hush_flash(tmp_path, name)
    assert flash["pop"] == expected
    assert flash["activation"] == {"state": "completed", "evidence": "flash_windup_ended", "targets_complete": True,
                                   "diagnostics": []}
    assert len(flash["hits"]) == hits == len(flash["targets"])
    assert flash["ability"].startswith("hush_flashbulb_")


def test_a_flashbulb_that_hit_nobody_is_a_proven_empty(tmp_path):
    flash, expected, _ = hush_flash(tmp_path, "hush_flashbulb_empty")
    assert flash["activation"]["state"] == "completed" and flash["activation"]["targets_complete"] is True
    assert flash["hits"] == [] and flash["targets"] == [] and flash["pop"] == expected


# ---------------------------------------------------------------- W3: decay sources and their figures

AGENTS_DECAY = {**AGENTS7, "Pandemic": "Viper", "BountyHunter": "Fade"}


def run_decay(tmp_path, rows):
    # Slot 9 Viper and slot 8 Fade (team B); the team-A pawns 100-107 are their enemies.
    table = table7()
    table.agents[8], table.agents[9] = "Fade", "Viper"
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["time_ms"])), encoding="utf-8")
    return build_extras(path, table, WINDOWS, GAME_MAP, AGENTS_DECAY, lambda t_ms: {}, teams=TEAMS7).rounds[1]


def _statuses(out, name, status):
    return [(s["slot"], s["target"], s["t0"], s["t1"], bool(s.get("friendly")))
            for s in out.get("statuses", []) if s["name"] == name and s["status"] == status]


def test_toxic_screen_decays_the_enemy_it_touches_for_the_span_of_the_touch(tmp_path):
    # 6f12db3e: an enemy touching the wall plays FXC_Pandemic_Ability_E_SmokeScreen_Blind naming the wall's manager, for
    # 0.06-0.7 s, and a one-shot heavy-application effect on them in the same 50-100 ms. Viper is slot 9 (pawn 109).
    wall = 60
    rows = [spawned(20_000, wall, "Default__GameObject_Pandemic_E_SmokeScreenManager_C", 0, 0), instigated(20_000, wall, 109),
            effect(25_000, 100, 7, [wall, 109], container=8263), stop(25_300, 100, 7),      # enemy: decayed
            effect(26_000, 101, 8, [wall, 109], container=8263), stop(26_200, 101, 8),      # teammate: harmless contact
            effect(27_000, 109, 9, [wall, 109], container=8263), stop(27_200, 109, 9),      # Viper herself: nothing
            closed(40_000, wall)]
    out = run_decay(tmp_path, rows)
    assert _statuses(out, "E_SmokeScreenManager", "decayed") == [(9, 0, 15.0, 15.3, False)]


def test_nightfall_decays_the_enemy_its_wave_hits_for_the_debuffs_eight_seconds(tmp_path):
    # 6f12db3e: the wave (GameObject_BountyHunter_X_WaveForm) plays one one-shot FXC_BountyHunter_X_ApplyBuffVictim on each
    # enemy it hits (5 hits, 3 casts). The debuff lasts 8 s (wiki), which the one-shot does not carry.
    wave = 70
    rows = [spawned(20_000, wave, "Default__GameObject_BountyHunter_X_WaveForm_C", 0, 0), instigated(20_000, wave, 108),
            oneshot(22_000, 100, [wave, 108], 9001), oneshot(22_100, 101, [wave, 108], 9001), closed(23_000, wave)]
    out = run_decay(tmp_path, rows)
    assert _statuses(out, "X_WaveForm", "decayed") == [(8, 0, 12.0, 20.0, False)]


@pytest.mark.parametrize("code,name", [("Pandemic", "E_SmokeScreenManager"), ("Pandemic", "X_Circular"),
                                      ("BountyHunter", "X_WaveForm"), ("BountyHunter", "Q_Tether_SphereExpansion"),
                                      ("Smonk", "Q_DecayExplosion"), ("Breach", "E_SweetSpotFissure")])
def test_friendly_contacts_never_decay_but_keep_other_statuses(tmp_path, code, name):
    from app.replays.status_intervals import normalize_conditions

    rows = [spawned(20_000, 80, f"Default__GameObject_{code}_{name}_C", 0, 0), instigated(20_000, 80, 108),
            effect(22_000, 101, 7, [80, 108]), stop(22_300, 101, 7), closed(30_000, 80)]
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    agents = {**AGENTS_DECAY, "Smonk": "Clove", "Breach": "Breach"}
    out = build_extras(path, table7(), WINDOWS, GAME_MAP, agents, lambda t: {}, teams=TEAMS7).rounds[1]
    statuses = out.get("statuses", [])
    assert all(s["status"] != "decayed" for s in statuses)
    expected = "tethered" if name == "Q_Tether_SphereExpansion" else "concussed" if code == "Breach" else None
    assert [(s["status"], s.get("friendly")) for s in statuses] == ([(expected, True)] if expected else [])
    util = [{**s, "k": "status", "t": s["t0"], "by": s["slot"]} for s in statuses]
    assert all(c["c"] != "decayed" for line in normalize_conditions(util).values() for c in line)


@pytest.mark.parametrize("one_shot", [False, True])
@pytest.mark.parametrize("named", [False, True])
def test_secondary_statuses_expand_on_both_attribution_paths(tmp_path, one_shot, named):
    rows = []
    for i, t in enumerate((20_000, 40_000)):
        sphere = 80 + i
        rows += [spawned(t, sphere, "Default__GameObject_Q_BountyHunter_Tether_SphereExpansion_C", 0, 0),
                 instigated(t, sphere, 108)]
        for target in (100, 101):  # an enemy and a teammate
            context = [sphere, 108] if named else [108]
            rows += ([oneshot(t + 600, target, context, 9101)] if one_shot else
                     [effect(t + 600, target, i + target, context, 9101), stop(t + 1600, target, i + target)])
        rows.append(closed(t + 2000, sphere))
    out = run_decay(tmp_path, rows)
    tether = _statuses(out, "Q_Tether_SphereExpansion", "tethered")
    decay = _statuses(out, "Q_Tether_SphereExpansion", "decayed")
    assert len(decay) == 2
    assert decay == [s for s in tether if not s[-1]]
    assert len(tether) == (4 if named else 2)


def test_seize_gives_both_tethered_and_decayed_over_the_tether_span(tmp_path):
    # 6f12db3e: Seize's wave decays and then tethers whoever it holds; the export names the object on the enemy in two
    # continuous effects (TetherChain_Grenade, TetherVictimFeedback), 1.2-4.7 s, in 3 of 3 plays. A statuses table that
    # allowed one status per object made Seize "tethered" only.
    sphere = 80
    rows = [spawned(20_000, sphere, "Default__GameObject_Q_BountyHunter_Tether_SphereExpansion_C", 0, 0),
            instigated(20_000, sphere, 108),
            effect(22_000, 100, 7, [sphere, 108], container=9101), effect(22_000, 100, 8, [sphere, 108], container=9102),
            stop(25_000, 100, 7), stop(25_000, 100, 8), closed(30_000, sphere)]
    out = run_decay(tmp_path, rows)
    assert _statuses(out, "Q_Tether_SphereExpansion", "tethered") == [(8, 0, 12.0, 15.0, False)]
    assert _statuses(out, "Q_Tether_SphereExpansion", "decayed") == [(8, 0, 12.0, 15.0, False)]


def test_a_tether_only_source_stays_tether_only(tmp_path):
    # The trapwire tethers but does not decay: a second status is a per-source entry, not a blanket.
    from app.replays.extras import statuses_of, _Actor
    wire = _Actor(1, 0, "", "Gumshoe", "4_TripWire", 0, 0, None)
    assert statuses_of(wire) == ("tethered",)
    seize = _Actor(2, 0, "", "BountyHunter", "Q_Tether_SphereExpansion", 0, 0, None)
    assert statuses_of(seize) == ("tethered", "decayed")


def _decay_figures():
    import pathlib
    import app.replays
    return json.loads((pathlib.Path(app.replays.__file__).parent / "decay_figures.json").read_text(encoding="utf-8"))


def test_every_decay_figure_names_a_decayed_source_and_carries_its_wiki_page():
    from app.replays.extras import statuses_of, _Actor
    figures = _decay_figures()
    assert "_doc" in figures and isinstance(figures["_doc"], dict)
    sources = {k: v for k, v in figures.items() if k != "_doc"}
    assert "Smonk_Q_DecayExplosion" in sources and "Pandemic_X_Circular" in sources
    for key, entry in sources.items():
        code, name = key.split("_", 1)
        assert "decayed" in statuses_of(_Actor(1, 0, "", code, name, 0, 0, None)), key
        assert entry["wiki"].startswith("https://valorant.fandom.com/wiki/"), key
        assert entry["fetched"].startswith("2026-11-"), key
        assert entry["note"], key
        # every field the entry uses is documented, and every field has a number or an explicit null
        for field in entry:
            if field not in ("wiki", "fetched", "note"):
                assert field in figures["_doc"], (key, field)
                assert entry[field] is None or isinstance(entry[field], (int, float)), (key, field)
        assert entry["onset_hp"] is not None, key


def test_every_decayed_source_has_figures_or_is_listed_as_unfigured():
    # R8: a mark without an amount is the exception, and it is named here so it cannot appear unnoticed.
    from app.replays.extras import STATUS_OBJECTS, decay_source_keys
    figures = {k for k in _decay_figures() if k != "_doc"}
    unfigured = set(decay_source_keys()) - figures
    assert unfigured == set(), unfigured
    assert any(label == "decayed" for _, label in STATUS_OBJECTS)


def test_meddle_and_the_pit_carry_the_measured_figures():
    figures = _decay_figures()
    meddle = figures["Smonk_Q_DecayExplosion"]
    assert (meddle["onset_hp"], meddle["build_s"], meddle["restore_s"], meddle["total_s"]) == (90, 0.75, 1.75, 5)
    pit = figures["Pandemic_X_Circular"]
    assert (pit["onset_hp"], pit["rate_hp_s"], pit["hold_s"], pit["restore_hp_s"]) == (30, 10, 1.5, 25)


def test_nightfall_carries_the_owners_hold_and_restore_figures():
    nightfall = _decay_figures()["BountyHunter_X_WaveForm"]
    assert {k: nightfall[k] for k in ("onset_hp", "build_s", "rate_hp_s", "max_hp", "hold_s",
                                     "restore_s", "restore_hp_s", "total_s")} == {
        "onset_hp": 75, "build_s": 0, "rate_hp_s": 0, "max_hp": 75, "hold_s": 8,
        "restore_s": 12, "restore_hp_s": 6.25, "total_s": 20}
    assert nightfall["wiki"] == "https://valorant.fandom.com/wiki/Nightfall"
    assert nightfall["fetched"] == "2026-11-20"
