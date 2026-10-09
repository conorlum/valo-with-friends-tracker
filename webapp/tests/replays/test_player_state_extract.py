"""Health and spike state from the export into each round's `player_state` (app/replays/player_state_extract.py;
the replay player-state plan's P03 and its amendments; decisions D4 and D5).

The real-shaped records are anonymized: the LifeChangeEvents blobs below are copied from replay 7a278f4b's export
(W3 EVIDENCE B; they hold only net guids and floats), every other row is synthetic."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import bomb_state, damage_notify, life_change_events  # noqa: E402

from app.replays import player_state as ps  # noqa: E402
from app.replays import player_state_extract as pse  # noqa: E402
from app.replays.condense import PlayerTable  # noqa: E402
from app.replays.contract import Export, Row  # noqa: E402

# Real blobs (7a278f4b): a gun hit through Light armor, the same pawn's lethal hit, Phoenix's ult "lethal" hit
# that returns him to 100, and a fall that leaves full Regen shields untouched.
REAL_ARMORED = {"BitCount": 499, "Data": "BgIWICkKGEAAAAAAGkAAAACAHAIBCCxAslwwgHic6oE0gOJYFYM5BAIYWICUKGAAcazaCmkAeZwq"
                                         "BHMIBAAA"}
REAL_LETHAL = {"BitCount": 499, "Data": "BgIWICkKGEAAAAAAGkAAAACAHAIBCCxAslwwgAAAAAA0gAAAAAA5BAIYWICUKGAAAQAAAGgAAQBA"
                                       "BHMIAAAA"}
REAL_PHOENIX_ULT = {"BitCount": 499, "Data": "BgIWIBVaGEAAAAAAGkAAAACAHAIBCCxAKiQwgAAAAAA0gAAAAAA5BAIYWIBESGAAAQAgC2kAA"
                                            "QAABHMIBAAA"}
REAL_FALL = {"BitCount": 507, "Data": "BgIWME0PAhhAAADIQRpAAAAAgBwCAQgsQOIsMIAAAAAANIAAAAAAOQQCGFiAtFlgAHE6pwppAHksxgV"
                                     "zCAQAAA=="}


def _round(sections):
    return [(s["g"], round(s["res"], 3), round(s["d"], 3), s["alive"]) for s in sections]


def test_the_decoder_reads_the_real_blobs():
    assert _round(pse.decode_life_changes(REAL_ARMORED)) == [(660, 0.0, 0.0, 1), (2988, 7.666, -17.334, 1),
                                                             (658, 91.334, -8.666, 1)]
    assert _round(pse.decode_life_changes(REAL_LETHAL)) == [(660, 0.0, 0.0, 1), (2988, 0.0, 0.0, 1),
                                                            (658, 0.0, -9.0, 0)]
    assert _round(pse.decode_life_changes(REAL_PHOENIX_ULT)) == [(5770, 0.0, 0.0, 1), (1162, 0.0, 0.0, 1),
                                                                 (1160, 100.0, -8.0, 1)]
    fall = _round(pse.decode_life_changes(REAL_FALL))
    assert fall == [(17318, 25.0, 0.0, 1), (1464, 0.0, 0.0, 1), (1462, 84.904, -15.096, 1)]


def test_the_test_encoder_writes_what_the_decoder_reads():
    blob = life_change_events([(658, 91.334, -8.666, 1), (300, 0.0, 0.0, None), (70000, 25.0, -0.5, 0)])
    got = pse.decode_life_changes(blob)
    assert [(s["g"], s["alive"]) for s in got] == [(658, 1), (300, None), (70000, 0)]
    assert [round(s["res"], 3) for s in got] == [91.334, 0.0, 25.0]


@pytest.mark.parametrize("bad", [None, {}, {"BitCount": 499}, {"BitCount": 499, "Data": "!!"},
                                 {"BitCount": 4000, "Data": REAL_ARMORED["Data"]}, {"BitCount": "x", "Data": "AA=="},
                                 {"BitCount": 16, "Data": "/////w=="}])
def test_an_undecodable_blob_is_none_never_a_guess(bad):
    assert pse.decode_life_changes(bad) is None


def test_max_hp_100_is_the_measured_pre_hit_value():
    """MAX_HP is not a game constant typed in: W3 EVIDENCE B measured the HP section's pre-hit value (result -
    delta) at every slot's first hit of a round as 100 on 202 of 202, with no MaxLife field in the export. These
    two real hits, from full health, show the same pre-hit value."""
    for blob in (REAL_ARMORED, REAL_FALL):
        hp = pse.decode_life_changes(blob)[-1]
        assert round(hp["res"] - hp["d"], 3) == pse.MAX_HP == 100.0


def test_armor_capacities_are_the_measured_ones():
    assert pse.ARMOR_CAPACITY == {"Default__LightArmorItem_C": 25.0, "Default__HeavyArmorItem_C": 50.0,
                                  "Default__PlasmaArmorItem_C": 25.0}


# ---------------------------------------------------------------- extraction over an export

START, END = 100_000, 200_000          # one round: InRound at 100 s, playback to 200 s
WINDOWS = [(START, 190_000, END)]


def table() -> PlayerTable:
    return PlayerTable(subjects=[None] * 10, agents=["Sage"] * 10, pawn_slot={1000 + s: s for s in range(10)},
                       other_slot={200 + s: s for s in range(10)}, pawn_changes={}, gone_ms={})


def export(rows) -> Export:
    ordered = sorted(enumerate(rows), key=lambda item: item[1]["time_ms"])
    return Export({}, [Row(i, r["time_ms"], r) for i, r in ordered], [], max(r["time_ms"] for r in rows))


def armor(t_ms, guid, kind="Light"):
    return {"type": "actor_spawned", "time_ms": t_ms, "actor_net_guid": guid,
            "archetype_path": f"Default__{kind}ArmorItem_C", "location": {"x": 0.0, "y": 0.0, "z": 0.0}}


HP0, ZERO0 = 70, 72        # slot 0's HP section and its always-0 sibling (guids inside the blob)
HP1 = 170                  # slot 1's
ARMOR0 = 500               # slot 0's armor item; its section is guid + 2


def lethal_hits():
    """A lethal hit on each of slots 0 and 1 elsewhere in the match: what names their HP sections."""
    return [damage_notify(300_000, 1000, 1005, 9.0, [(ZERO0, 0.0, 0.0, 1), (HP0, 0.0, -9.0, 0)], index=90,
                          lethal=True),
            damage_notify(300_500, 1001, 1005, 30.0, [(HP1 + 2, 0.0, 0.0, 1), (HP1, 0.0, -30.0, 0)], index=91,
                          lethal=True)]


def lives_whole_round(slot, t_ms):
    return 0 if START <= t_ms <= END else None


def extract(rows, life_of=lives_whole_round, windows=WINDOWS, n=1):
    inputs = pse.read_player_state(export(rows), table())
    start, _, end = windows[n - 1]
    prev_end = windows[n - 2][2] if n > 1 else None
    return pse.round_player_state(inputs, start, end, prev_end, life_of), inputs


def test_a_hit_is_a_full_snapshot_after_it_with_the_armors_measured_max():
    rows = [armor(80_000, ARMOR0), *lethal_hits(),
            damage_notify(127_363, 1000, 1005, 26.0, [(ZERO0, 0.0, 0.0, 1), (ARMOR0 + 2, 7.666, -17.334, 1),
                                                      (HP0, 91.334, -8.666, 1)], index=53)]
    got, inputs = extract(rows)
    assert got["vitals"] == {"0": [{"t": 27.363, "life": 0, "hp": 91.334, "sh": 7.666, "mhp": 100.0, "msh": 25.0}]}
    assert got["damage_taken"] == {"0": [27.363]}
    assert ps.validate(got) == got
    assert ps.health_pct(got["vitals"]["0"][0]) == pytest.approx(100 * 99.0 / 125)


def test_no_armor_section_and_none_this_life_is_no_shield_proven_zero():
    rows = [*lethal_hits(), damage_notify(110_000, 1001, 1005, 30.0, [(HP1 + 2, 0.0, 0.0, 1), (HP1, 70.0, -30.0, 1)],
                                          index=5)]
    got, _ = extract(rows)
    assert got["vitals"]["1"] == [{"t": 10.0, "life": 0, "hp": 70.0, "sh": 0.0, "mhp": 100.0, "msh": 0.0}]


def test_a_broken_armor_keeps_its_max_for_the_life():
    rows = [armor(80_000, ARMOR0, "Heavy"), *lethal_hits(),
            damage_notify(110_000, 1000, 1005, 75.0, [(ARMOR0 + 2, 0.0, -50.0, 1), (HP0, 75.0, -25.0, 1)], index=1),
            damage_notify(111_000, 1000, 1005, 20.0, [(HP0, 55.0, -20.0, 1)], index=2)]
    got, _ = extract(rows)
    assert [(e["sh"], e["msh"]) for e in got["vitals"]["0"]] == [(0.0, 50.0), (0.0, 50.0)]


def test_a_missing_armor_section_while_shield_was_up_is_unproven_not_zero():
    rows = [armor(80_000, ARMOR0), *lethal_hits(),
            damage_notify(110_000, 1000, 1005, 10.0, [(ARMOR0 + 2, 18.0, -7.0, 1), (HP0, 97.0, -3.0, 1)], index=1),
            damage_notify(111_000, 1000, 1005, 20.0, [(HP0, 77.0, -20.0, 1)], index=2)]
    got, inputs = extract(rows)
    assert [(e["hp"], e["sh"], e["msh"]) for e in got["vitals"]["0"]] == [(97.0, 18.0, 25.0), (77.0, None, None)]
    assert inputs.diagnostics["shield: armor section missing while shield > 0"] == 1


def test_an_unknown_section_taking_damage_leaves_the_shield_unproven():
    rows = [*lethal_hits(),
            damage_notify(110_000, 1000, 1005, 10.0, [(9000, 40.0, -5.0, 1), (HP0, 95.0, -5.0, 1)], index=1)]
    got, inputs = extract(rows)
    assert [(e["hp"], e["sh"], e["msh"]) for e in got["vitals"]["0"]] == [(95.0, None, None)]
    assert inputs.diagnostics["shield: an unknown section took damage"] == 1


def test_shield_above_its_armors_capacity_is_an_invalid_max():
    rows = [armor(80_000, ARMOR0), *lethal_hits(),
            damage_notify(110_000, 1000, 1005, 10.0, [(ARMOR0 + 2, 40.0, -5.0, 1), (HP0, 95.0, -5.0, 1)], index=1)]
    got, inputs = extract(rows)
    assert (got["vitals"]["0"][0]["sh"], got["vitals"]["0"][0]["msh"]) == (None, None)
    assert inputs.diagnostics["shield: above its armor's capacity"] == 1


def test_the_hp_section_is_the_one_every_lethal_hit_zeroes_even_with_extra_sections():
    # Clove: two extra sections that sometimes also report dead on a kill; only the HP section is in all of them.
    rows = [damage_notify(300_000, 1003, 1005, 9.0, [(982, 0.0, 0.0, 1), (958, 0.0, -9.0, 0)], index=1, lethal=True),
            damage_notify(301_000, 1003, 1005, 9.0, [(982, 0.0, 0.0, 0), (960, 0.0, 0.0, 0), (958, 0.0, -9.0, 0)],
                          index=2, lethal=True),
            damage_notify(110_000, 1003, 1005, 9.0, [(982, 0.0, 0.0, 1), (958, 91.0, -9.0, 1)], index=3)]
    got, _ = extract(rows)
    assert got["vitals"]["3"][0]["hp"] == 91.0


def test_a_pawn_that_never_died_has_no_proven_hp_section_and_no_vitals():
    rows = [damage_notify(110_000, 1004, 1005, 9.0, [(80, 0.0, 0.0, 1), (78, 91.0, -9.0, 1)], index=3)]
    got, inputs = extract(rows)
    assert "4" not in got["vitals"] and got["damage_taken"] == {"4": [10.0]}
    assert inputs.diagnostics["vitals: HP section unresolved (owner never died)"] == 1


def test_conflicting_lethal_hits_leave_the_hp_section_unresolved():
    rows = [damage_notify(300_000, 1004, 1005, 9.0, [(80, 0.0, -9.0, 0)], index=1, lethal=True),
            damage_notify(301_000, 1004, 1005, 9.0, [(90, 0.0, -9.0, 0)], index=2, lethal=True),
            damage_notify(110_000, 1004, 1005, 9.0, [(80, 91.0, -9.0, 1)], index=3)]
    got, inputs = extract(rows)
    assert "4" not in got["vitals"]
    assert inputs.diagnostics["vitals: HP section unresolved (lethal hits disagree)"] == 1


def test_sections_that_dont_sum_to_the_damage_are_a_decoder_gap_not_a_snapshot():
    rows = [*lethal_hits(), damage_notify(110_000, 1001, 1005, 50.0, [(HP1, 70.0, -30.0, 1)], index=5)]
    got, inputs = extract(rows)
    assert "1" not in got["vitals"] and got["damage_taken"] == {"1": [10.0]}
    assert inputs.diagnostics["vitals: sections don't sum to DamageTaken"] == 1


def test_one_hit_sent_twice_counts_once_and_pellets_at_one_ms_stay_separate():
    one = damage_notify(110_000, 1001, 1005, 30.0, [(HP1, 70.0, -30.0, 1)], index=5)
    pellet = damage_notify(110_000, 1001, 1005, 10.0, [(HP1, 60.0, -10.0, 1)], index=6)
    got, inputs = extract([*lethal_hits(), one, dict(one), pellet])
    assert [e["hp"] for e in got["vitals"]["1"]] == [70.0, 60.0]
    assert got["damage_taken"] == {"1": [10.0]}
    assert inputs.diagnostics["damage: repeated hit (same victim, respawn, index)"] == 1


def test_damage_taken_keeps_self_and_fall_damage_and_drops_zero():
    fall = damage_notify(110_000, 1001, 1001, 15.1, [(HP1, 84.9, -15.1, 1)], index=5, point=False)
    zero = damage_notify(120_000, 1001, 1005, 0.0, [(HP1, 84.9, 0.0, 1)], index=6, point=False)
    got, inputs = extract([*lethal_hits(), fall, zero])
    assert got["damage_taken"] == {"1": [10.0]}
    assert [e["t"] for e in got["vitals"]["1"]] == [10.0], "a zero-damage notify is no hit"
    assert inputs.diagnostics["damage: zero damage (dropped)"] == 1


def test_lives_come_from_the_rounds_alive_intervals_and_a_hit_outside_one_is_dropped():
    def life_of(slot, t_ms):
        return 0 if t_ms <= 130_000 else (1 if t_ms >= 150_000 else None)

    rows = [*lethal_hits(),
            damage_notify(110_000, 1001, 1005, 30.0, [(HP1, 70.0, -30.0, 1)], index=5),
            damage_notify(140_000, 1001, 1005, 30.0, [(HP1, 40.0, -30.0, 1)], index=6),
            damage_notify(160_000, 1001, 1005, 30.0, [(HP1, 70.0, -30.0, 1)], index=7, respawn=1)]
    got, inputs = extract(rows, life_of=life_of)
    assert [(e["t"], e["life"]) for e in got["vitals"]["1"]] == [(10.0, 0), (60.0, 1)]
    assert got["damage_taken"]["1"] == [10.0, 40.0, 60.0]
    assert inputs.diagnostics["vitals: hit outside a life"] == 1


def test_phoenixs_ult_return_is_kept_as_its_own_snapshot_and_counted():
    rows = [*lethal_hits(), damage_notify(110_000, 1001, 1005, 8.0, [(HP1, 100.0, -8.0, 1)], index=5)]
    got, inputs = extract(rows)
    assert got["vitals"]["1"][0]["hp"] == 100.0
    assert inputs.diagnostics["vitals: HP above max before the hit (a respawn)"] == 1


def test_no_heal_is_invented_between_hits():
    rows = [*lethal_hits(),
            damage_notify(110_000, 1001, 1005, 90.0, [(HP1, 10.0, -90.0, 1)], index=5),
            damage_notify(150_000, 1001, 1005, 2.5, [(HP1, 97.5, -2.5, 1)], index=6)]
    got, _ = extract(rows)
    assert [(e["t"], e["hp"]) for e in got["vitals"]["1"]] == [(10.0, 10.0), (50.0, 97.5)]
    assert ps.vitals_at(got, 1, 49.9)["hp"] == 10.0, "the heal shows only at the next hit (D4)"


def test_hits_of_other_rounds_and_objects_stay_out():
    rows = [*lethal_hits(), damage_notify(110_000, 7777, 1005, 30.0, [(HP1, 70.0, -30.0, 1)], index=5)]
    got, _ = extract(rows)
    assert got["vitals"] == {} and got["damage_taken"] == {}


def test_an_export_without_life_change_events_has_no_vitals_part():
    row = damage_notify(110_000, 1001, 1005, 30.0, [(HP1, 70.0, -30.0, 1)], index=5)
    del row["payload"]["LifeChangeEvents"]
    got, _ = extract([row])
    assert "vitals" not in got and got["damage_taken"] == {"1": [10.0]}


def test_an_export_with_none_of_the_sources_has_no_player_state():
    got, _ = extract([armor(110_000, ARMOR0)])
    assert got is None


# ---------------------------------------------------------------- spike (D5 option A)

TWO = [(100_000, 190_000, 200_000), (250_000, 340_000, 350_000)]


def test_spike_states_come_from_bomb_state_with_exact_times_in_their_round():
    rows = [bomb_state(55_000, 1), bomb_state(69_133, 3), bomb_state(69_133, 3), bomb_state(136_645, 2),
            bomb_state(139_435, 3), bomb_state(180_930, 4), bomb_state(195_000, 5), bomb_state(201_000, 0),
            bomb_state(205_000, 1), bomb_state(231_479, 3)]
    got, _ = extract(rows, windows=TWO, n=1)
    assert got["spike"] == [{"t": -45.0, "s": "unknown"}, {"t": -30.867, "s": "carried"},
                            {"t": 36.645, "s": "dropped"}, {"t": 39.435, "s": "carried"},
                            {"t": 80.93, "s": "planted"}, {"t": 95.0, "s": "detonated"}]
    second, _ = extract(rows, windows=TWO, n=2)
    assert second["spike"] == [{"t": -49.0, "s": "unknown"}, {"t": -18.521, "s": "carried"}], \
        "round over (0) then spawned (1): both unknown, one entry"
    assert "vitals" not in got and "damage_taken" not in got


def test_an_unknown_bomb_state_value_is_unknown_and_counted():
    got, inputs = extract([bomb_state(110_000, 3), bomb_state(120_000, 9)])
    assert got["spike"] == [{"t": 10.0, "s": "carried"}, {"t": 20.0, "s": "unknown"}]
    assert inputs.diagnostics["spike: unknown BombState value"] == 1


def _spike(*entries):
    return {"version": 1, "spike": [dict(e) for e in entries]}


BOMB = {"k": "ability", "t": 80.918, "by": 1, "kind": "Bomb", "code": "", "name": "Spike", "owner_by": "planted",
        "u": 5480, "v": 1136}


def test_the_planter_carried_from_the_last_pickup_and_the_plant_takes_its_place():
    state = _spike({"t": -30.867, "s": "carried"}, {"t": 36.645, "s": "dropped"}, {"t": 39.435, "s": "carried"},
                   {"t": 80.93, "s": "planted"}, {"t": 95.0, "s": "detonated"})
    counts = {}
    pse.attribute_planter(state, [BOMB], counts)
    assert state["spike"] == [{"t": -30.867, "s": "carried"}, {"t": 36.645, "s": "dropped"},
                              {"t": 39.435, "s": "carried", "slot": 1}, {"t": 80.93, "s": "planted", "u": 5480,
                                                                          "v": 1136},
                              {"t": 95.0, "s": "detonated"}]
    assert counts == {"spike: planter attributed": 1}
    assert ps.validate(state)["spike"] == state["spike"]


def test_an_instigator_named_planter_counts_too():
    state = _spike({"t": 39.435, "s": "carried"}, {"t": 80.93, "s": "planted"})
    pse.attribute_planter(state, [dict(BOMB, owner_by="instigator", by=4)], {})
    assert state["spike"][0] == {"t": 39.435, "s": "carried", "slot": 4}


@pytest.mark.parametrize("bomb", [dict(BOMB, owner_by="agent"), dict(BOMB, owner_by="nearest"), dict(BOMB, by=None)])
def test_an_unproven_planter_leaves_the_carrier_absent_but_the_planted_place_stands(bomb):
    state = _spike({"t": 39.435, "s": "carried"}, {"t": 80.93, "s": "planted"})
    counts = {}
    pse.attribute_planter(state, [bomb], counts)
    assert state["spike"] == [{"t": 39.435, "s": "carried"}, {"t": 80.93, "s": "planted", "u": 5480, "v": 1136}]
    assert counts == {"spike: planter not proven": 1}


def test_no_bomb_row_at_the_plant_leaves_carrier_and_place_absent():
    state = _spike({"t": 39.435, "s": "carried"}, {"t": 80.93, "s": "planted"})
    counts = {}
    pse.attribute_planter(state, [dict(BOMB, t=78.0), {"k": "shot", "t": 80.9, "by": 1}], counts)
    assert state["spike"] == [{"t": 39.435, "s": "carried"}, {"t": 80.93, "s": "planted"}]
    assert counts == {"spike: no Bomb row at the plant": 1}


def test_a_drop_or_unknown_right_before_the_plant_gets_no_carrier():
    state = _spike({"t": 39.435, "s": "carried"}, {"t": 70.0, "s": "dropped"}, {"t": 80.93, "s": "planted"})
    counts = {}
    pse.attribute_planter(state, [BOMB], counts)
    assert all("slot" not in e for e in state["spike"]) and state["spike"][1] == {"t": 70.0, "s": "dropped"}
    assert state["spike"][2] == {"t": 80.93, "s": "planted", "u": 5480, "v": 1136}
    assert counts == {"spike: no pickup right before the plant": 1}


def test_two_plants_in_one_round_contradict_and_nothing_is_attributed():
    state = _spike({"t": 39.435, "s": "carried"}, {"t": 80.93, "s": "planted"}, {"t": 85.0, "s": "planted"})
    counts = {}
    pse.attribute_planter(state, [BOMB], counts)
    assert all("slot" not in e and "u" not in e for e in state["spike"])
    assert counts == {"spike: conflicting plants (not attributed)": 1}


def test_a_round_without_a_spike_part_is_left_alone():
    state = {"version": 1, "vitals": {}}
    pse.attribute_planter(state, [BOMB], {})
    assert state == {"version": 1, "vitals": {}}
