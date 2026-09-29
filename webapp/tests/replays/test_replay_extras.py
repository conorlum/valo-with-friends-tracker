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
    return build_extras(path, table or two_cyphers(), WINDOWS, GAME_MAP, AGENTS5, lambda t_ms: positions or {})


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


# ---------------------------------------------------------------- stored util (Stage 2, R2)

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import SyntheticMatch  # noqa: E402

from app.replays import condense as cd  # noqa: E402
from app.replays import format as fmt  # noqa: E402
from app.replays.extras import rounds_extras, util_entries  # noqa: E402

UTIL_KEYS = {"k", "t", "by", "t1", "kind", "code", "name", "agent", "owner_by", "u", "v", "yaw", "thrown", "path",
             "owner_d", "other_d", "u1", "v1", "gun", "n", "end", "defuses", "points", "on", "target"}


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
