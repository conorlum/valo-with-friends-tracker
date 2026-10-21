"""Health and spike state from the export into each round's `player_state` (app/replays/player_state_extract.py;
the replay player-state plan's P03 and its amendments; decisions D4 and D5).

The real-shaped records are anonymized: the LifeChangeEvents blobs below are copied from replay 7a278f4b's export
(W3 EVIDENCE B; they hold only net guids and floats), every other row is synthetic."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import bomb_state, damage_notify, heal_notify, life_change_events  # noqa: E402

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
    pse.attribute_death_drops(state, [{"t": 1.0, "victim": 5, "u": 1, "v": 2}], {})
    assert state == {"version": 1, "vitals": {}}


# D5 (answered 2026-10-09): the spike drops where its carrier died. 7a278f4b R5: slot 5 dies 77 ms before the drop.
DEATH = {"i": 3, "t": 22.802, "killer": 1, "victim": 5, "u": 4210, "v": 6120}


def test_a_drop_right_after_one_death_lies_at_the_death_and_that_player_carried_it():
    state = _spike({"t": -30.0, "s": "carried"}, {"t": 22.879, "s": "dropped"}, {"t": 49.8, "s": "carried"})
    counts = {}
    pse.attribute_death_drops(state, [{"i": 0, "t": 10.0, "killer": 5, "victim": 2, "u": 1, "v": 1}, DEATH], counts)
    assert state["spike"] == [{"t": -30.0, "s": "carried", "slot": 5},
                              {"t": 22.879, "s": "dropped", "u": 4210, "v": 6120}, {"t": 49.8, "s": "carried"}]
    assert counts == {"spike: dropped at the carrier's death": 1}
    assert ps.validate(state)["spike"] == state["spike"]


def test_a_drop_with_no_death_close_before_it_is_voluntary_and_has_no_place():
    state = _spike({"t": -30.0, "s": "carried"}, {"t": 22.879, "s": "dropped"})
    counts = {}
    # a death 0.6 s before the drop, and one just after it: neither caused it
    pse.attribute_death_drops(state, [dict(DEATH, t=22.279), dict(DEATH, t=22.9, victim=6)], counts)
    assert state["spike"] == [{"t": -30.0, "s": "carried"}, {"t": 22.879, "s": "dropped"}]
    assert counts == {"spike: dropped with no death (place unknown)": 1}


def test_two_deaths_before_one_drop_are_a_trade_and_nothing_is_attributed():
    state = _spike({"t": -30.0, "s": "carried"}, {"t": 22.879, "s": "dropped"})
    counts = {}
    pse.attribute_death_drops(state, [DEATH, dict(DEATH, t=22.85, victim=1, killer=7)], counts)
    assert state["spike"] == [{"t": -30.0, "s": "carried"}, {"t": 22.879, "s": "dropped"}]
    assert counts == {"spike: dropped after two deaths (not attributed)": 1}


def test_a_death_with_no_position_still_names_the_carrier_but_places_nothing():
    state = _spike({"t": -30.0, "s": "carried"}, {"t": 22.879, "s": "dropped"})
    counts = {}
    pse.attribute_death_drops(state, [dict(DEATH, u=None, v=None)], counts)
    assert state["spike"] == [{"t": -30.0, "s": "carried", "slot": 5}, {"t": 22.879, "s": "dropped"}]
    assert counts == {"spike: dropped at a death with no position": 1}


def test_a_death_of_someone_other_than_a_proven_carrier_places_nothing():
    state = _spike({"t": 10.0, "s": "carried", "slot": 1}, {"t": 22.879, "s": "dropped"})
    counts = {}
    pse.attribute_death_drops(state, [DEATH], counts)
    assert state["spike"] == [{"t": 10.0, "s": "carried", "slot": 1}, {"t": 22.879, "s": "dropped"}]
    assert counts == {"spike: dropped at a death of someone other than the carrier (not attributed)": 1}


# ---------------------------------------------------------------- ult points and ult orbs (2026-10-18)

def ult_points(t_ms, slot, points):
    """A player state's replicated ult points (player states are 200 + slot in `table()`)."""
    return {"type": "export_group_received", "time_ms": t_ms, "actor_net_guid": 200 + slot,
            "export_group_path": "/Game/GameModes/Bomb/BombPlayerState.BombPlayerState_C",
            "payload": {"NumUltimatePoints": points, "TotalAcquiredUltimatePoints": points}}


def orb(t_ms, guid, x, y):
    return {"type": "actor_spawned", "time_ms": t_ms, "actor_net_guid": guid, "archetype_path": "Default__UltPointOrb_C",
            "location": {"x": x, "y": y, "z": 0.0}}


def closed(t_ms, guid):
    return {"type": "actor_closed", "time_ms": t_ms, "actor_net_guid": guid}


WINDOWS_2 = [(START, 190_000, END), (260_000, 350_000, 360_000)]


def test_ult_points_are_the_seed_before_the_round_then_each_change_and_the_first_round_starts_at_zero():
    rows = [ult_points(120_000, 3, 1), ult_points(120_500, 3, 1), ult_points(150_000, 3, 2),
            ult_points(300_000, 3, 0), ult_points(130_000, 4, 9), ult_points(140_000, 200, 1)]
    first, inputs = extract(rows, windows=WINDOWS_2, n=1)
    second, _ = extract(rows, windows=WINDOWS_2, n=2)
    # round 1: everyone starts at 0 (the game sends a change, not a value); a repeat adds nothing
    assert first["ult"]["3"] == [{"t": -200.0, "n": 0}, {"t": 20.0, "n": 1}, {"t": 50.0, "n": 2}]
    # round 2: the value before it (its look-back starts at round 1's end), then the cast
    assert second["ult"]["3"] == [{"t": -60.0, "n": 2}, {"t": 40.0, "n": 0}]
    # 9 is a full Killjoy; a player state no slot owns is counted, not guessed
    assert first["ult"]["4"] == [{"t": -200.0, "n": 0}, {"t": 30.0, "n": 9}]
    assert inputs.diagnostics["ult: unreadable or unowned NumUltimatePoints"] == 1
    assert ps.validate(first)["ult"]["3"] == first["ult"]["3"]


def pickup(t_ms, guid, slot):
    """The game state's capture notice: the orb and its capturer's pawn (pawns are 1000 + slot in `table()`)."""
    return {"type": "rpc_received", "time_ms": t_ms, "actor_net_guid": 264, "function_name": "OrbPickedUpRPC",
            "class_path": "/Game/GameModes/Bomb/BombGameState.BombGameState_C",
            "payload": {"CollectableOrb": guid, "OrbGatherer": 1000 + slot}}


def to_uv(x, y):
    return int(x // 10), int(y // 10)


def test_an_orb_is_taken_by_the_player_the_game_names_and_otherwise_goes_with_the_plant_or_the_round():
    rows = [orb(101_000, 1, 0.0, 0.0), orb(101_000, 2, 5000.0, 0.0), orb(101_000, 3, 0.0, 5000.0),
            orb(101_000, 4, 9000.0, 9000.0), orb(101_000, 5, 100.0, 0.0),
            closed(130_000, 1), pickup(130_000, 1, 6), pickup(130_000, 1, 6),   # sent twice: one capture
            closed(150_000, 2), bomb_state(150_100, 4),                          # the plant: gone with it
            closed(170_000, 3),                                                  # no capture: round
            closed(150_000, 5), pickup(150_000, 5, 2)]       # captured at the plant: still a capture
    inputs = pse.read_player_state(export(rows), table())
    state = pse.round_player_state(inputs, START, END, None, lives_whole_round, to_uv=to_uv)
    assert state["orbs"] == [
        {"u": 0, "v": 0, "t0": 1.0, "t1": 30.0, "end": "taken", "slot": 6},
        {"u": 500, "v": 0, "t0": 1.0, "t1": 50.0, "end": "plant"},
        {"u": 0, "v": 500, "t0": 1.0, "t1": 70.0, "end": "round"},
        {"u": 900, "v": 900, "t0": 1.0, "t1": None, "end": "round"},   # never closed in the window
        {"u": 10, "v": 0, "t0": 1.0, "t1": 50.0, "end": "taken", "slot": 2},
    ]
    assert ps.validate(state)["orbs"] == state["orbs"]
    # without a map there are no orbs (they have no place to be drawn)
    assert "orbs" not in pse.round_player_state(inputs, START, END, None, lives_whole_round)


def test_a_buy_phase_capture_and_one_at_full_ult_are_named_like_any_other():
    # a short-handed team's spawn orb, captured at -10 s by a player whose ult points don't move (full)
    rows = [orb(85_000, 1, 0.0, 0.0), closed(90_000, 1), pickup(90_000, 1, 3), ult_points(80_000, 3, 7)]
    state = pse.round_player_state(pse.read_player_state(export(rows), table()), START, END, None, lives_whole_round,
                                   to_uv=to_uv)
    assert state["orbs"] == [{"u": 0, "v": 0, "t0": -15.0, "t1": -10.0, "end": "taken", "slot": 3}]


def test_a_reused_orb_guid_is_a_new_orb_with_its_own_capturer():
    rows = [orb(101_000, 1, 0.0, 0.0), closed(130_000, 1), pickup(130_000, 1, 3), pickup(130_000, 1, 3),
            orb(261_000, 1, 500.0, 0.0), closed(300_000, 1), pickup(300_000, 1, 4)]
    inputs = pse.read_player_state(export(rows), table())
    first = pse.round_player_state(inputs, *WINDOWS_2[0][::2], None, lives_whole_round, to_uv=to_uv)
    second = pse.round_player_state(inputs, WINDOWS_2[1][0], WINDOWS_2[1][2], WINDOWS_2[0][2], lives_whole_round,
                                    to_uv=to_uv)
    assert first["orbs"] == [{"u": 0, "v": 0, "t0": 1.0, "t1": 30.0, "end": "taken", "slot": 3}]
    assert second["orbs"] == [{"u": 50, "v": 0, "t0": 1.0, "t1": 40.0, "end": "taken", "slot": 4}]


def test_a_capturer_no_slot_owns_is_counted_and_the_orb_still_taken():
    rows = [orb(101_000, 1, 0.0, 0.0), closed(130_000, 1), dict(pickup(130_000, 1, 0), payload={
        "CollectableOrb": 1, "OrbGatherer": 77})]
    inputs = pse.read_player_state(export(rows), table())
    state = pse.round_player_state(inputs, START, END, None, lives_whole_round, to_uv=to_uv)
    assert state["orbs"] == [{"u": 0, "v": 0, "t0": 1.0, "t1": 30.0, "end": "taken"}]
    assert inputs.diagnostics["orbs: capturer's pawn unknown"] == 1


def test_an_orb_that_never_closes_stays_in_the_round_it_appeared_in():
    rows = [orb(101_000, 1, 0.0, 0.0), orb(261_000, 2, 0.0, 0.0), closed(300_000, 2)]   # orb 1 has no close at all
    inputs = pse.read_player_state(export(rows), table())
    first = pse.round_player_state(inputs, *WINDOWS_2[0][::2], None, lives_whole_round, to_uv=to_uv)
    second = pse.round_player_state(inputs, WINDOWS_2[1][0], WINDOWS_2[1][2], WINDOWS_2[0][2], lives_whole_round,
                                    to_uv=to_uv)
    assert [o["t0"] for o in first["orbs"]] == [1.0]
    assert [o["t0"] for o in second["orbs"]] == [1.0], "round 1's unclosed orb isn't carried into round 2"


def test_a_capture_let_go_goes_on_the_orb_at_its_place_that_was_open():
    state = {"version": 1, "orbs": [{"u": 5, "v": 5, "t0": 1.0, "t1": 20.0, "end": "taken", "slot": 1},
                                    {"u": 9, "v": 9, "t0": 1.0, "t1": None, "end": "round"}]}
    counts = {}
    pse.attach_orb_tries(state, [{"t": 12.0, "t1": 12.6, "slot": 4, "u": 5, "v": 5},
                                 {"t": 30.0, "t1": None, "slot": 2, "u": 9, "v": 9},
                                 {"t": 25.0, "t1": 26.0, "slot": 2, "u": 5, "v": 5}], counts)   # orb 1 gone by then
    assert state["orbs"][0]["tries"] == [[12.0, 12.6, 4]]
    assert state["orbs"][1]["tries"] == [[30.0, None, 2]]
    assert counts == {"orbs: capture let go": 2, "orbs: capture let go on no orb of the round": 1}
    assert ps.validate(state)["orbs"] == state["orbs"]


def test_the_validator_drops_bad_ult_points_and_orbs():
    checked = ps.validate({"version": 1, "ult": {"0": [{"t": 1.0, "n": 3}, {"t": 2.0, "n": 99}, {"t": 3.0, "n": True}],
                                                 "x": [{"t": 1.0, "n": 1}]},
                           "orbs": [{"u": 1, "v": 2, "t0": 1.0, "t1": None, "end": "round",
                                     "tries": [[3.0, 3.5, 4], [3.0, 2.0, 4], [3.0, None, 12], [3.0, None], "x"]},
                                    {"u": 1, "v": 2, "t0": 5.0, "t1": 4.0, "end": "taken"},
                                    {"u": 1, "v": 2, "t0": 1.0, "t1": 2.0, "end": "eaten"},
                                    {"u": 1, "v": 2, "t0": 1.0, "t1": 2.0, "end": "taken", "slot": 12}]})
    assert checked["ult"] == {"0": [{"t": 1.0, "n": 3}]}
    assert checked["orbs"] == [{"u": 1, "v": 2, "t0": 1.0, "t1": None, "end": "round", "tries": [[3.0, 3.5, 4]]}]


# ---------------------------------------------------------------- heals (revision 21; corrects D4)

SAGE_ORB = 702             # Sage's heal-orb equippable (the heal's `Causer`)


def armored_hit():
    """Slot 0 at 27.363 s: HP 91.334, Light shield 7.666 (as in the first extraction test)."""
    return [armor(80_000, ARMOR0), *lethal_hits(),
            damage_notify(127_363, 1000, 1005, 26.0, [(ZERO0, 0.0, 0.0, 1), (ARMOR0 + 2, 7.666, -17.334, 1),
                                                      (HP0, 91.334, -8.666, 1)], index=53)]


def test_a_heal_on_hp_raises_it_and_keeps_the_lifes_last_shield():
    rows = [*armored_hit(), heal_notify(130_000, 1000, 1003, SAGE_ORB, [(HP0, 3.0, 94.334)]),
            heal_notify(130_150, 1000, 1003, SAGE_ORB, [(HP0, 3.0, 97.334)])]
    got, inputs = extract(rows)
    assert got["vitals"]["0"] == [
        {"t": 27.363, "life": 0, "hp": 91.334, "sh": 7.666, "mhp": 100.0, "msh": 25.0},
        {"t": 30.0, "life": 0, "hp": 94.334, "sh": 7.666, "mhp": 100.0, "msh": 25.0},
        {"t": 30.15, "life": 0, "hp": 97.334, "sh": 7.666, "mhp": 100.0, "msh": 25.0}]
    assert got["damage_taken"] == {"0": [27.363]}, "a heal is not damage"
    assert ps.validate(got) == got
    assert inputs.diagnostics["vitals: snapshot after a heal"] == 2
    assert [(h.slot, h.by, h.causer, inputs.heal_pool(h)) for h in inputs.heals] == [(0, 3, SAGE_ORB, "hp")] * 2


def test_a_hit_after_a_heal_is_its_own_snapshot():
    rows = [*armored_hit(), heal_notify(130_000, 1000, 1003, SAGE_ORB, [(HP0, 3.0, 94.334)]),
            damage_notify(131_000, 1000, 1005, 10.0, [(ARMOR0 + 2, 0.0, -7.666, 1), (HP0, 92.0, -2.334, 1)], index=54)]
    got, _ = extract(rows)
    assert [(e["t"], e["hp"], e["sh"]) for e in got["vitals"]["0"]] == [(27.363, 91.334, 7.666), (30.0, 94.334, 7.666),
                                                                         (31.0, 92.0, 0.0)]


@pytest.mark.parametrize("unread", ["undecodable", "no HP section"])
def test_a_hit_that_left_no_snapshot_makes_a_later_heals_shield_unknown(unread):
    # the hit at 29 s took shield too, so carrying 27.363 s's 7.666 into the heal would be a guess
    hit = damage_notify(129_000, 1000, 1005, 5.0, [(ARMOR0 + 2, 2.666, -5.0, 1)], index=54)
    if unread == "undecodable":
        hit["payload"]["LifeChangeEvents"] = {"BitCount": 499, "Data": "!!"}
    got, _ = extract([*armored_hit(), hit, heal_notify(130_000, 1000, 1003, SAGE_ORB, [(HP0, 3.0, 94.334)])])
    assert got["vitals"]["0"][-1] == {"t": 30.0, "life": 0, "hp": 94.334, "sh": None, "mhp": 100.0, "msh": None}


def test_a_heal_with_no_earlier_snapshot_in_its_life_has_an_unproven_shield():
    rows = [*lethal_hits(), heal_notify(130_000, 1001, 1003, SAGE_ORB, [(HP1, 3.0, 63.0)])]
    got, inputs = extract(rows)
    assert got["vitals"]["1"] == [{"t": 30.0, "life": 0, "hp": 63.0, "sh": None, "mhp": 100.0, "msh": None}]
    assert ps.health_pct(got["vitals"]["1"][0]) is None, "the bar is unavailable, never a guessed shield"
    assert inputs.diagnostics["vitals: heal with no earlier snapshot in its life (shield unknown)"] == 1


def test_an_overheal_section_makes_a_heal_but_no_snapshot():
    # Reyna's Devour past 100 and Clove's Pick-Me-Up heal a section that is neither HP nor armor
    rows = [*armored_hit(), heal_notify(130_000, 1000, 1000, 960, [(9_999, 0.13, 0.13)])]
    got, inputs = extract(rows)
    assert [e["t"] for e in got["vitals"]["0"]] == [27.363]
    assert inputs.heal_pool(inputs.heals[0]) == "other"
    assert inputs.diagnostics["vitals: heal of a section that is other (no snapshot)"] == 1


def test_a_heal_on_an_unproven_hp_section_is_unknown_and_makes_no_snapshot():
    rows = [heal_notify(130_000, 1002, 1003, SAGE_ORB, [(270, 3.0, 63.0)])]       # slot 2 never dies
    got, inputs = extract([*rows, damage_notify(129_000, 1002, 1005, 40.0, [(270, 60.0, -40.0, 1)], index=1)])
    assert got["vitals"] == {}
    assert inputs.heal_pool(inputs.heals[0]) == "unknown"


def test_a_heal_whose_changes_dont_sum_to_its_amount_or_isnt_on_a_player_is_dropped():
    bad = heal_notify(130_000, 1000, 1003, SAGE_ORB, [(HP0, 3.0, 94.334)])
    bad["payload"]["Amount"] = 5.0
    wall = heal_notify(130_000, 7_777, None, None, [(7_800, 500.0, 500.0)])     # a wall segment healing in
    _, inputs = extract([*armored_hit(), bad, wall])
    assert inputs.heals == []
    assert inputs.diagnostics["heals: changes don't sum to Amount (dropped)"] == 1
    assert inputs.diagnostics["heals: not on a player's pawn"] == 1


def test_a_heal_nobody_instigated_is_kept_with_no_healer():
    _, inputs = extract([*lethal_hits(), heal_notify(130_000, 1001, None, None, [(HP1, 87.6, 87.6)])])   # KAY/O's ult
    assert [(h.slot, h.by, h.causer) for h in inputs.heals] == [(1, None, 0)]


# ---------------------------------------------------------------- ability charges (revision 21)

def inventory(t_ms, pawn, **slots):
    """A player's AresInventory naming its slot objects (`GrenadeAbilitySlot`, ...)."""
    return {"type": "export_group_received", "time_ms": t_ms, "actor_net_guid": pawn, "object_net_guid": pawn + 6,
            "outer_net_guid": pawn, "export_group_path": "/Script/ShooterGame.AresInventory",
            "payload": {"Character": pawn, **slots}}


def item_slot(t_ms, pawn, obj, contents):
    return {"type": "export_group_received", "time_ms": t_ms, "actor_net_guid": pawn, "object_net_guid": obj,
            "outer_net_guid": pawn + 6, "export_group_path": "/Script/ShooterGame.ItemSlot",
            "payload": {"Contents": contents}}


def charge(t_ms, component, equippable, n=None, signature=False, **more):
    path = "SignatureAbilityResourceComponent" if signature else "EquipmentChargeComponent"
    payload = {**({} if n is None else {"AuthResourceAmount": n}), **more}
    return {"type": "export_group_received", "time_ms": t_ms, "actor_net_guid": equippable, "object_net_guid": component,
            "outer_net_guid": equippable, "export_group_path": f"/Script/ShooterGame.{path}", "payload": payload}


def sage_kit(pawn=1003):
    """Slot 3's inventory: grenade slot 30 holds equippable 31 (charge component 32), Ability2 slot 40 holds the heal
    orb 41 (component 42, a signature's)."""
    return [inventory(70, pawn, GrenadeAbilitySlot=30, Ability1Slot=35, Ability2Slot=40, UltimateSlot=45),
            item_slot(70, pawn, 30, 31), item_slot(70, pawn, 40, 41)]


def test_charges_follow_the_proven_owner_and_slot():
    rows = [*sage_kit(), charge(70, 32, 31, TotalChargesAllowedToPurchaseThisRound=1), charge(70, 42, 41, 1, True),
            charge(85_000, 32, 31, 1, ChargesBoughtThisRound=1),
            charge(120_000, 42, 41, 0), charge(120_000, 42, 41, 0), charge(150_000, 32, 31, 0),
            charge(265_000, 42, 41, 1)]
    inputs = pse.read_player_state(export(rows), table())
    first = pse.round_player_state(inputs, *WINDOWS_2[0][::2], None, lives_whole_round)
    second = pse.round_player_state(inputs, WINDOWS_2[1][0], WINDOWS_2[1][2], WINDOWS_2[0][2], lives_whole_round)
    assert first["charges"] == {"3": {"C": [{"t": -15.0, "n": 1}, {"t": 50.0, "n": 0}],
                                      "E": [{"t": -99.93, "n": 1}, {"t": 20.0, "n": 0}]}}
    # round 2 starts from what each slot held at round 1's end, then each change
    assert second["charges"] == {"3": {"C": [{"t": -60.0, "n": 0}], "E": [{"t": -60.0, "n": 0}, {"t": 5.0, "n": 1}]}}
    assert ps.validate(first) == first
    assert inputs.diagnostics["charges: component proven"] == 2


def test_a_component_whose_owner_isnt_proven_is_left_out():
    rows = [*sage_kit(),
            item_slot(70, 1004, 50, 31), inventory(70, 1004, GrenadeAbilitySlot=50),    # 31 is in two players' slots
            item_slot(70, 1003, 46, 61),                                                # 46 is no ability slot
            item_slot(70, 1003, 40, 71), inventory(70, 1005, Ability1Slot=40),          # 40 named by two inventories
            inventory(70, 1100, GrenadeAbilitySlot=80), item_slot(70, 1100, 80, 81),    # 1100 is no player's pawn
            charge(90_000, 32, 31, 1), charge(90_000, 62, 61, 1), charge(90_000, 72, 71, 1), charge(90_000, 82, 81, 1),
            charge(90_000, 92, 91, 1),                                                  # 91 is in no slot at all
            charge(90_000, 42, 41, 1)]
    inputs = pse.read_player_state(export(rows), table())
    assert inputs.charges == {}
    assert inputs.diagnostics["charges: component not proven (left out)"] == 6
    state = pse.round_player_state(inputs, START, END, None, lives_whole_round)
    assert state is None, "no source at all: no player_state"


def test_a_slot_whose_components_overlap_in_time_is_left_out():
    # halftime gives each ability a new equippable; one that still changes after the next one began isn't proven current
    rows = [*sage_kit(), item_slot(70, 1003, 40, 141), item_slot(70, 1003, 30, 131),
            charge(90_000, 42, 41, 1), charge(95_000, 142, 141, 2), charge(99_000, 42, 41, 0),
            charge(90_000, 32, 31, 1), charge(99_000, 32, 31, 0), charge(150_000, 132, 131, 2)]
    inputs = pse.read_player_state(export(rows), table())
    assert inputs.diagnostics["charges: a slot's components overlap (slot left out)"] == 1
    # E's two components (41's, 141's) both change between 95 and 99 s: left out. C's follow one another: kept.
    assert set(inputs.charges) == {(3, "C")}
    assert inputs.charges[(3, "C")] == [(90_000, 1), (99_000, 0), (150_000, 2)]


def test_the_validator_drops_bad_charges():
    checked = ps.validate({"version": 1, "charges": {"0": {"C": [{"t": 1.0, "n": 2}, {"t": 2.0, "n": 10}],
                                                           "V": [{"t": 1.0, "n": 1}], "Q": "x"},
                                                     "x": {"C": []}, "1": []}})
    assert checked["charges"] == {"0": {"C": [{"t": 1.0, "n": 2}]}}
    assert ps.validate({"version": 1, "charges": []}) is None
