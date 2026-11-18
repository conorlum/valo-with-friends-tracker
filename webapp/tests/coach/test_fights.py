"""Fight facts over the coach's existing toy round builder."""

from types import SimpleNamespace

import pytest

from app.coach import fights, knowledge
from app.coach.roundview import RoundView
from tests.coach import toys


CATEGORIES = {"Vandal": "rifle", "Phantom": "rifle", "Classic": "sidearm", "Sheriff": "sidearm",
              "Stinger": "smg", "Spectre": "smg", "Bucky": "shotgun", "Judge": "shotgun",
              "Knife": "melee", "Overdrive": "ability"}


def hold(slot, gun, t=0.0, guid=None, gen=0, **extra):
    return {"k": "hold", "t": t, "by": slot, "gun": gun, "cat": CATEGORIES.get(gun, "other"),
            "id": guid or f"gun-{slot}", "gen": gen, **extra}


def shot(slot, gun, t, gid=None, gen=0):
    return {"k": "shot", "t": t, "by": slot, "gun": gun, "gid": gid or f"gun-{slot}", "gen": gen}


def snapshot(*, my="Vandal", enemy="Classic", metres=6, mate_m=30, own=3, opponents=3,
             util=None, kills=None, knew=None, stats=None, names=None, t_decided=None):
    positions = {s: (2000, 2000 + round(mate_m * 70)) for s in range(own)}
    positions[0] = (2000, 2000)
    positions.update({s: (2000 + round(metres * 70), 2000) for s in range(5, 5 + opponents)})
    if util is None:
        util = [hold(0, my, seed=True), hold(5, enemy, seed=True)]
    if kills is None:
        kills = [{"t": 10.0, "killer": 5, "victim": 0}]
    blob = toys.round_blob(positions, kills=kills, util=util, t_decided=t_decided)
    names = names or toys.NAMES
    view = RoundView(blob, names, toys.UV_PER_UNIT, "Toy", callout_table=toys.CALLOUTS)
    site = {"stats": stats or {}, "kills": {"0": {"weapon": "Classic"}}}
    return SimpleNamespace(view=lambda n: view, names=names, control=lambda n: {"knew": knew or {}},
                           site=lambda n: site, context={"rounds": {"1": site}})


def first(snap):
    return fights.for_round(snap, 1, 0)[0]


@pytest.mark.parametrize("item,expected", [
    ({"gun": None, "cat": "ability", "code": "Gumshoe", "key": "E", "name": "Camera"}, "Spycam"),
    ({"gun": "Knife", "cat": "melee"}, "Knife"),
    ({"gun": "Knife", "cat": "melee", "item": "unarmed"}, "unarmed"),
    ({"gun": "Vandal", "cat": "rifle"}, None),
    ({"gun": None, "cat": "defuser"}, None),
    ({"gun": None, "cat": "spike"}, "Spike"),
    ({"gun": None, "cat": "flex"}, "flex item"),
    ({"gun": None, "cat": "other"}, None),
    ({"gun": None, "cat": "ability", "code": None, "key": None, "name": "Overdrive"}, "Overdrive"),
    # Not in the held table: the unnamed label, which Site health reports (held_ability_unnamed).
    ({"gun": None, "cat": "ability", "key": None, "name": "Unknown_Ability"}, "an ability"),
    (None, None),
])
def test_out_at_death_uses_the_last_hold_not_the_best_gun(item, expected):
    rows = [] if item is None else [hold(0, "Vandal"), {"k": "hold", "by": 0, "t": 9, **item}]
    snap = snapshot(util=rows)
    snap.view(1).blob["players"][0]["agent"] = "Cypher"
    assert first(snap)["death"]["out_at_death"] == expected


@pytest.mark.parametrize("agent,item,expected", [
    ("Sova", {"gun": None, "cat": "ability", "code": "Hunter", "key": "Q", "name": "RevealBolt_Signature"}, "Recon Bolt"),
    ("Omen", {"gun": None, "cat": "ability", "code": "Wraith", "key": "Q", "name": "NearsightMissile"}, "Paranoia"),
    ("Cypher", {"gun": "Knife", "cat": "melee"}, "Knife"),
    ("Cypher", {"gun": "Knife", "cat": "melee", "item": "unarmed"}, "unarmed"),
    ("Cypher", {"gun": None, "cat": "spike"}, "Spike"),
    ("Cypher", {"gun": None, "cat": "flex"}, "flex item"),
    ("Cypher", {"gun": "Vandal", "cat": "rifle"}, None),
    ("Cypher", {"gun": None, "cat": "defuser"}, None),
])
@pytest.mark.parametrize("close_t", [10.0, 10.001])
def test_death_teardown_does_not_erase_the_last_living_item(agent, item, expected, close_t):
    # Same close shape as test_replay_extras.test_hold_python_round_trip_preserves_all_fields.
    close = {"k": "hold", "t": close_t, "by": 0, "gun": None, "cat": "other"}
    snap = snapshot(util=[hold(0, "Vandal"), {"k": "hold", "t": 9, "by": 0, **item}, close])
    snap.view(1).blob["players"][0]["agent"] = agent
    death = first(snap)["death"]
    assert death["out_at_death"] == expected
    assert death["dropped_gun"] == "Vandal"


@pytest.mark.parametrize("switch,expected", [
    ({"t": 9.5, "cat": "other"}, None),  # An earlier unknown switch is real.
    ({"t": 10, "cat": "other", "id": 901}, None),  # A real unknown actor.
    ({"t": 10, "cat": "spike"}, "Spike"),
    ({"t": 10, "cat": "rifle", "gun": "Vandal"}, None),
    ({"t": 10, "cat": "defuser"}, None),
    ({"t": 10.001, "cat": "spike"}, "Knife"),  # Future real switches do not count.
])
def test_death_teardown_preserves_genuine_switches(switch, expected):
    snap = snapshot(util=[hold(0, "Knife", 8), {"k": "hold", "by": 0, "gun": None, **switch},
                          {"k": "hold", "t": 10, "by": 0, "gun": None, "cat": "other"}])
    assert first(snap)["death"]["out_at_death"] == expected


def test_death_teardown_does_not_reuse_a_previous_lifes_item():
    snap = snapshot(util=[hold(0, "Knife", 1),
                          {"k": "hold", "t": 10, "by": 0, "gun": None, "cat": "other"}])
    snap.view(1).blob["alive"]["0"] = [[0, 5, "kill"], [7, 10, "kill"]]
    assert first(snap)["death"]["out_at_death"] is None


@pytest.mark.parametrize("span,owner,expected", [
    ([8, None], 0, "Spycam"), ([8, 11], 0, "Spycam"), ([8, 9], 0, None),
    ([8, 10], 0, "Spycam"), ([10, 10], 0, "Spycam"), ([8, 9.999], 0, None),
    ([11, None], 0, None), ([8, None], 1, None),
])
def test_out_at_death_counts_only_current_own_possession(span, owner, expected):
    camera = {"k": "ability", "t": 2, "t1": None, "by": owner, "kind": "Pawn",
              "code": "Gumshoe", "name": "E_PossessableCamera", "possessed": [span]}
    snap = snapshot(util=[hold(0, "Vandal"), camera])
    snap.view(1).blob["players"][0]["agent"] = "Cypher"
    assert first(snap)["death"]["out_at_death"] == expected


@pytest.mark.parametrize("end", [None, 10])
def test_out_at_death_keeps_possession_when_the_camera_closes_at_death(end):
    camera = {"k": "ability", "t": 2, "t1": 10, "by": 0, "kind": "Pawn",
              "code": "Gumshoe", "name": "E_PossessableCamera", "possessed": [[8, end]]}
    snap = snapshot(util=[hold(0, "Vandal"), camera])
    snap.context["players"] = {"0": {"agent": "Cypher"}}
    assert first(snap)["death"]["out_at_death"] == "Spycam"


def test_out_at_death_does_not_reuse_a_previous_lifes_hold_or_a_future_hold():
    snap = snapshot(util=[hold(0, "Knife", 1), hold(0, "Knife", 11)])
    snap.view(1).blob["alive"]["0"] = [[0, 5, "kill"], [7, 10, "kill"]]
    assert first(snap)["death"]["out_at_death"] is None


@pytest.mark.parametrize("code,key,name,expected", [
    ("Gumshoe", "C", "TripWire", "Trapwire"), ("Gumshoe", "Q", "CageTrap", "Cyber Cage"),
    ("Gumshoe", "E", "Camera", "Spycam"), ("Gumshoe", "X", "InterrogateV2", "Neural Theft"),
    # The replay's slot letter is not the key: Omen's smoke is stored as C, and is Dark Cover (E).
    ("Wraith", "C", "Smoke", "Dark Cover"),
])
def test_out_at_death_names_the_held_ability_from_the_held_table(code, key, name, expected):
    snap = snapshot(util=[{"k": "hold", "t": 9, "by": 0, "gun": None, "cat": "ability",
                           "code": code, "key": key, "name": name}])
    snap.context["players"] = {"0": {"agent": "Cypher"}}
    assert first(snap)["death"]["out_at_death"] == expected


def test_lone_even_death_hands_over_better_gun_and_has_provenance():
    f = first(snapshot())
    assert (f["subject"], f["side"], f["enemy"], f["won"]) == (0, "A", "P5", False)
    assert (f["t"], f["t0"], f["g"], f["metres"]) == (10, 10, 9.95, 6)
    assert f["alive"] == {"own": 3, "enemy": 3}
    assert f["lone"] is True and f["one_v_one"] is False and f["short_range"] is True
    assert (f["my_gun"], f["enemy_gun"], f["my_gun_source"], f["enemy_gun_source"]) == (
        "Vandal", "Classic", "held", "held")
    assert f["handover"] == {"my_best_tier": 2, "killer_tier": 0, "better": True}
    assert f["death"] == {"dropped_gun": "Vandal", "dropped_id": "gun-0", "dropped_gen": 0,
                          "dropped_tier": 2, "killer_gun_at_kill": "Classic", "killer_tier": 0, "better": True,
                          "out_at_death": None}
    assert f["flags"] == ["gun_handover"]
    assert f["kill_weapon"] == "Classic"


@pytest.mark.parametrize("kwargs,lone", [({"own": 1, "opponents": 1}, None),
                                         ({"own": 2, "opponents": 3}, True), ({"mate_m": 12}, False)])
def test_handover_requires_lone_even_numbers_outside_one_v_one(kwargs, lone):
    f = first(snapshot(**kwargs))
    assert f["lone"] is lone
    assert f["flags"] == []


def test_pickup_and_kills_follow_actor_generation_and_last_shot():
    rows = [hold(0, "Vandal", gen=4), hold(5, "Classic"),
            hold(5, "Vandal", 11, guid="gun-0", gen=4, **{"from": 0}),
            shot(5, "Vandal", 11.9, "gun-0", 4), shot(5, "Vandal", 13.9, "gun-0", 8),
            shot(5, "Classic", 15.9)]
    kills = [{"t": 10, "killer": 5, "victim": 0}, {"t": 12, "killer": 5, "victim": 1},
             {"t": 14, "killer": 5, "victim": 2}, {"t": 16, "killer": 5, "victim": 3}]
    f = first(snapshot(util=rows, kills=kills, own=4))
    assert f["picked_up"] == {"by": "P5", "t": 11}
    assert f["kills_with"] == 1


def pickup_rows(*extra):
    return [hold(0, "Vandal", gen=4), hold(5, "Classic"),
            hold(5, "Vandal", 11, guid="gun-0", gen=4, **{"from": 0}), *extra]


def with_kill_weapons(snap, weapons):
    for i, weapon in weapons.items():
        snap.context["rounds"]["1"]["kills"][str(i)] = {"weapon": weapon}
    return snap


def test_kills_with_ignores_ability_kill_after_the_gun_was_fired_once():
    rows = pickup_rows(shot(5, "Vandal", 11.1, "gun-0", 4))
    kills = [{"t": 10, "killer": 5, "victim": 0}, {"t": 20, "killer": 5, "victim": 1}]
    snap = with_kill_weapons(snapshot(util=rows, kills=kills), {1: "Paint Shells"})
    assert first(snap)["kills_with"] == 0
    snap = with_kill_weapons(snapshot(util=rows, kills=kills), {1: "vandal"})
    assert first(snap)["kills_with"] == 1
    assert first(snapshot(util=rows, kills=kills))["kills_with"] == 1    # weapon unknown: shot decides


def test_kills_with_needs_the_picker_still_holding_the_actor():
    other = [hold(5, "Classic", 12.5, guid="gun-5", gen=0), None]
    for switch in (other[0], {"k": "hold", "t": 12.5, "by": 5, "gun": None, "cat": "other"},
                   hold(5, "Overdrive", 12.5, guid="x", gen=9)):
        rows = pickup_rows(shot(5, "Vandal", 11.9, "gun-0", 4), switch)
        kills = [{"t": 10, "killer": 5, "victim": 0}, {"t": 12, "killer": 5, "victim": 1},
                 {"t": 13, "killer": 5, "victim": 2}]
        assert first(snapshot(util=rows, kills=kills))["kills_with"] == 1, switch


def test_kills_with_stops_at_the_pickers_death():
    rows = pickup_rows(shot(5, "Vandal", 11.9, "gun-0", 4), shot(5, "Vandal", 13.9, "gun-0", 4))
    kills = [{"t": 10, "killer": 5, "victim": 0}, {"t": 12, "killer": 5, "victim": 1},
             {"t": 13, "killer": 2, "victim": 5}, {"t": 14, "killer": 5, "victim": 3}]
    assert first(snapshot(util=rows, kills=kills, own=4))["kills_with"] == 1


def test_kills_with_ignores_the_pickers_friendly_kills():
    rows = pickup_rows(shot(5, "Vandal", 11.9, "gun-0", 4))
    kills = [{"t": 10, "killer": 5, "victim": 0}, {"t": 12, "killer": 5, "victim": 6}]
    assert first(snapshot(util=rows, kills=kills))["kills_with"] == 0


@pytest.mark.parametrize("killer,victim,subject", [(1, 0, 0), (1, 0, 1), (0, 1, 0), (0, 1, 1)])
def test_team_kills_are_not_fights_for_either_side(killer, victim, subject):
    rows = [hold(0, "Vandal"), hold(1, "Classic"), hold(5, "Classic")]
    snap = snapshot(util=rows, kills=[{"t": 10, "killer": killer, "victim": victim}])
    assert fights.for_round(snap, 1, subject) == []


def test_enemy_team_kill_is_not_a_fight_for_the_enemy_either():
    snap = snapshot(kills=[{"t": 10, "killer": 6, "victim": 5}])
    assert fights.for_round(snap, 1, 5) == [] and fights.for_round(snap, 1, 6) == []


def test_gun_given_to_a_teammate_before_death_is_not_dropped():
    rows = [hold(0, "Vandal", gen=4), hold(1, "Vandal", 5, guid="gun-0", gen=4, **{"from": 0}),
            hold(0, "Classic", 6, guid="c0", gen=1), hold(5, "Classic")]
    f = first(snapshot(util=rows))
    assert (f["death"]["dropped_gun"], f["death"]["dropped_id"], f["death"]["better"]) == ("Classic", "c0", False)
    assert f["handover"]["better"] is False and f["flags"] == []


def test_gun_with_no_candidate_left_has_no_drop_and_no_handover():
    rows = [hold(0, "Vandal", gen=4), hold(1, "Vandal", 5, guid="gun-0", gen=4, **{"from": 0}),
            {"k": "hold", "t": 6, "by": 0, "gun": "Knife", "cat": "melee", "id": "k", "gen": 7}, hold(5, "Classic")]
    f = first(snapshot(util=rows))
    assert f["death"]["dropped_gun"] is None and f["death"]["dropped_id"] is None
    assert f["death"]["better"] is False and f["flags"] == []


def test_gun_taken_back_before_death_is_a_candidate_again():
    rows = [hold(0, "Vandal", gen=4), hold(1, "Vandal", 5, guid="gun-0", gen=4, **{"from": 0}),
            hold(0, "Vandal", 7, guid="gun-0", gen=4, **{"from": 1}), hold(0, "Classic", 8, guid="c0", gen=1),
            hold(5, "Classic")]
    f = first(snapshot(util=rows))
    assert (f["death"]["dropped_gun"], f["death"]["better"]) == ("Vandal", True)


def test_a_gun_taken_only_at_or_before_the_death_instant_counts_and_after_does_not():
    base = [hold(0, "Vandal", gen=4), hold(0, "Classic", 6, guid="c0", gen=1), hold(5, "Classic")]
    at = first(snapshot(util=[*base, hold(5, "Vandal", 10, guid="gun-0", gen=4, **{"from": 0})]))
    assert at["death"]["dropped_gun"] == "Classic"
    after = first(snapshot(util=[*base, hold(5, "Vandal", 10.5, guid="gun-0", gen=4, **{"from": 0})]))
    assert after["death"]["dropped_gun"] == "Vandal" and after["picked_up"] == {"by": "P5", "t": 10.5}


@pytest.mark.parametrize("picker,gen,expected", [(6, 0, {"by": "P6", "t": 11}), (5, 8, None), (1, 0, None)])
def test_pickup_requires_enemy_previous_holder_and_same_generation(picker, gen, expected):
    rows = [hold(0, "Vandal"), hold(5, "Classic"),
            hold(picker, "Vandal", 11, guid="gun-0", gen=gen, **{"from": 0})]
    f = first(snapshot(util=rows))
    assert f["picked_up"] == expected


def test_stinger_at_eighteen_metres_is_outranged():
    f = first(snapshot(my="Stinger", enemy="Vandal", metres=18))
    assert (f["my_range"], f["enemy_range"], f["off_range"]) == ("beyond", "in", "outranged")
    assert f["flags"] == ["off_range_fight"]


@pytest.mark.parametrize("when,flag", [(7, True), (9, False)])
def test_heard_close_range_gun_gives_time_to_reposition(when, flag):
    rows = [hold(0, "Vandal"), hold(5, "Bucky"), shot(5, "Bucky", when)]
    f = first(snapshot(enemy="Bucky", metres=5, util=rows))
    assert f["known"] == {"how": "heard", "t": when, "before_s": 10 - when}
    assert ("off_range_fight" in f["flags"]) is flag
    assert f["off_range"] == ("into_their_range" if flag else None)


@pytest.mark.parametrize("heard,expected", [(7, "seen"), (6, "heard"), (None, "seen")])
def test_earliest_knowledge_wins_with_seen_tie_break(heard, expected):
    rows = [hold(0, "Vandal"), hold(5, "Judge", 5)]
    if heard is not None:
        rows.append(shot(5, "Judge", heard))
    f = first(snapshot(util=rows, knew={"A": {"5": [[7, 9, 2420, 2000]]}}))
    assert f["known"]["how"] == expected
    assert f["known"]["t"] == (6 if heard == 6 else 7)


def test_seen_run_intersects_hold_and_current_life_before_fight_start():
    rows = [hold(0, "Vandal"), hold(5, "Classic"), hold(5, "Judge", 7)]
    f = first(snapshot(util=rows, knew={"A": {"5": [[4, 8, 2420, 2000]]}}))
    assert f["known"] == {"how": "seen", "t": 7, "before_s": 3}
    f = first(snapshot(util=rows, knew={"A": {"5": [[4, 6, 2420, 2000], [10, 11, 2420, 2000]]}}))
    assert f["known"] is None


@pytest.mark.parametrize("loadout,expected", [(900, {"how": "econ_guess", "gun_class": "sidearm"}),
                                              (1500, {"how": "econ_guess", "gun_class": "sidearm"}),
                                              (3000, None)])
def test_economy_guess_uses_enemy_team_mean(loadout, expected):
    stats = {"0": {"loadout": 9000}, "5": {"loadout": loadout - 100}, "6": {"loadout": loadout + 100}}
    assert first(snapshot(stats=stats))["known"] == expected


def test_old_blobs_use_shot_fallback_and_never_claim_seen():
    rows = [shot(0, "Vandal", 8), shot(0, "Classic", 11), shot(5, "Bucky", 7)]
    f = first(snapshot(util=rows, knew={"A": {"5": [[6, 9, 2420, 2000]]}}))
    assert (f["my_gun"], f["enemy_gun"], f["my_gun_source"], f["enemy_gun_source"]) == (
        "Vandal", "Bucky", "shot", "shot")
    assert f["known"]["how"] == "heard"
    f = first(snapshot(util=[shot(0, "Stinger", 11), shot(5, "Vandal", 12)]))
    assert f["my_gun"] == "Stinger" and f["my_gun_source"] == "shot"
    assert f["known"] is None
    f = first(snapshot(util=[]))
    assert f["my_gun"] is None and f["my_gun_source"] is None


@pytest.mark.parametrize("killer,victim,decided", [(0, 0, 60), (5, 0, 9), (None, 0, 60)])
def test_self_kills_environment_and_closed_round_kills_are_excluded(killer, victim, decided):
    snap = snapshot(kills=[{"t": 10, "killer": killer, "victim": victim}], t_decided=decided)
    assert fights.for_round(snap, 1, 0) == []


def test_one_shot_geometry_allows_both_flags():
    f = first(snapshot(my="Vandal", enemy="Bucky", metres=5,
                       util=[hold(0, "Vandal"), hold(5, "Bucky"), shot(5, "Bucky", 7)]))
    assert f["g"] == 9.95 and f["metres"] == 5 and f["lone"] is True
    assert f["flags"] == ["gun_handover", "off_range_fight"]


@pytest.mark.parametrize("last", ["Spectre", "Knife", None, "Overdrive"])
def test_dropped_best_firearm_survives_switches_and_closing_rows(last):
    rows = [hold(0, "Vandal", seed=True, gen=4), hold(0, last, 6, guid="later", gen=5),
            hold(5, "Classic"), hold(5, "Vandal", 11, guid="gun-0", gen=4, **{"from": 0})]
    if last is None:
        rows[1] = {"k": "hold", "t": 6, "by": 0, "gun": None, "cat": "other"}
    f = first(snapshot(util=rows))
    assert f["my_gun"] == last
    assert f["my_gun_source"] == ("held" if last else None)
    assert (f["death"]["dropped_gun"], f["death"]["dropped_gen"], f["death"]["better"]) == ("Vandal", 4, True)
    assert f["picked_up"] == {"by": "P5", "t": 11}


def test_tied_best_tier_uses_latest_generation():
    rows = [hold(0, "Vandal"), hold(0, "Phantom", 5, guid="new-rifle", gen=2), hold(5, "Classic")]
    f = first(snapshot(util=rows))
    assert (f["death"]["dropped_gun"], f["death"]["dropped_id"], f["death"]["dropped_gen"]) == (
        "Phantom", "new-rifle", 2)


def test_tied_tier_and_time_use_last_stored_hold():
    rows = [hold(0, "Vandal", 5), hold(0, "Phantom", 5, guid="last-rifle", gen=2), hold(5, "Classic")]
    assert first(snapshot(util=rows))["death"]["dropped_id"] == "last-rifle"


def test_killer_gun_at_kill_is_separate_from_start_gun():
    rows = [hold(0, "Vandal"), hold(5, "Classic"), hold(5, "Sheriff", 9),
            {"k": "damage", "t": 8, "t1": 8, "by": 0, "target": 5, "src": "gun", "wall": False, "n": 1}]
    f = first(snapshot(util=rows))
    assert f["enemy_gun"] == "Classic" and f["death"]["killer_gun_at_kill"] == "Sheriff"
    assert f["g"] == 8


@pytest.mark.parametrize("my,enemy", [("Knife", "Bucky"), ("Vandal", "Knife"), ("Overdrive", "Bucky"),
                                      ("Vandal", None)])
def test_non_firearms_never_get_ranges_or_comparison_flags(my, enemy):
    rows = [hold(0, my), hold(5, enemy), shot(5, enemy, 7)]
    f = first(snapshot(my=my, enemy=enemy, util=rows, metres=5))
    assert f["my_range"] == ("in" if my == "Vandal" else None)
    assert f["enemy_range"] == ("in" if enemy == "Bucky" else None)
    assert f["flags"] == []


def test_hearing_uses_only_matching_gun_before_start_and_each_shots_own_range(monkeypatch):
    # Give the Bucky a shorter hearing radius to exercise per-gun audibility.
    monkeypatch.setitem(knowledge._HEARING["guns"], "Bucky", 4)
    rows = [hold(0, "Vandal"), hold(5, "Bucky"), shot(5, "Bucky", 7),
            shot(5, "Vandal", 7.2), shot(5, "Bucky", 10.1)]
    assert first(snapshot(util=rows, metres=6))["known"] is None
    rows.append(shot(5, "Bucky", 10))
    assert first(snapshot(util=rows, metres=3))["known"]["t"] == 7


def test_shot_at_fight_start_gives_no_advance_knowledge():
    rows = [hold(0, "Vandal"), hold(5, "Bucky"), shot(5, "Bucky", 10)]
    assert first(snapshot(util=rows))["known"] is None


@pytest.mark.parametrize("start,end,expected", [(3, 8, 5), (7, 9, 7), (2, 4.9, 10)])
@pytest.mark.parametrize("reverse", [False, True])
def test_merged_damage_run_start_is_clamped(start, end, expected, reverse):
    damage = {"k": "damage", "t": start, "t1": end, "by": 5 if reverse else 0,
              "target": 0 if reverse else 5, "src": "gun", "wall": False, "n": 3}
    f = first(snapshot(util=[hold(0, "Vandal"), hold(5, "Classic"), damage]))
    assert f["t0"] == expected
    assert f["g"] == (9.95 if expected == 10 else expected)


@pytest.mark.parametrize("metres,verdict,short", [(7.04, "beyond", True), (6.96, "in", True),
                                               (9.96, "beyond", True), (10.04, "beyond", False)])
def test_range_thresholds_use_unrounded_distance(metres, verdict, short):
    f = first(snapshot(my="Bucky", enemy="Vandal", metres=metres))
    assert f["metres"] == round(metres, 1)
    assert f["my_range"] == verdict and f["short_range"] is short


@pytest.mark.parametrize("next_hold", [False, True])
def test_revive_starts_a_new_life_without_a_held_gun(next_hold):
    rows = [hold(0, "Vandal"), hold(5, "Classic"), shot(0, "Vandal", 8)]
    if next_hold:
        rows.append(hold(0, "Spectre", 9, guid="revived-gun", gen=3))
    snap = snapshot(util=rows)
    view = snap.view(1)
    view.blob["alive"]["0"] = [[0, 5, "kill"], [7, 10, "kill"]]
    f = first(snap)
    assert f["my_gun"] == ("Spectre" if next_hold else None)
    assert f["my_gun_source"] == ("held" if next_hold else None)
    assert f["death"]["dropped_gun"] == ("Spectre" if next_hold else None)
    assert f["death"]["better"] is next_hold


def test_authoritative_holds_prevent_shot_fallback_for_missing_slot_hold():
    f = first(snapshot(util=[hold(5, "Classic"), shot(0, "Vandal", 7)]))
    assert f["my_gun"] is None and f["my_gun_source"] is None
    assert f["death"]["dropped_gun"] is None and f["flags"] == []


@pytest.mark.parametrize("context", [{}, {"rounds": {}}, {"rounds": {"1": {}}}])
def test_missing_context_round_or_kill_has_null_kill_weapon(context):
    from app.coach.snapfile import Snapshot

    snap = snapshot()
    snap.context = context
    snap.site = lambda n: Snapshot.site(snap, n)
    assert first(snap)["kill_weapon"] is None


def test_unplaceable_geometry_has_no_range_or_lone_claim():
    snap = snapshot()
    snap.view(1).blob["tracks"].clear()
    f = first(snap)
    assert f["metres"] is None and f["lone"] is None and f["short_range"] is None
    assert f["my_range"] is None and f["enemy_range"] is None and f["flags"] == []


def test_counts_are_just_before_damage_start_while_geometry_is_at_start():
    rows = [hold(0, "Vandal"), hold(5, "Classic"),
            {"k": "damage", "t": 8, "t1": 9, "by": 5, "target": 0, "src": "gun", "wall": False, "n": 3}]
    kills = [{"t": 8, "killer": 6, "victim": 1}, {"t": 10, "killer": 5, "victim": 0}]
    f = first(snapshot(util=rows, kills=kills))
    assert f["g"] == 8 and f["alive"] == {"own": 3, "enemy": 3}


@pytest.mark.parametrize("gun", ["Operator", "Bandit", "Warden"])
def test_legacy_firearms_have_range_verdicts(gun):
    rows = [shot(0, gun, 8), shot(5, "Vandal", 9)]
    assert first(snapshot(util=rows))["my_range"] == "in"


def test_win_uses_snapshot_names_and_missing_context_weapon_is_null():
    snap = snapshot(kills=[{"t": 10, "killer": 0, "victim": 5}], names={**toys.NAMES, 5: "the enemy Jett"})
    del snap.context
    f = first(snap)
    assert f["won"] is True and f["enemy"] == "the enemy Jett"
    assert f["subject"] == 0 and f["side"] == "A"
    assert f["kill_weapon"] is None and f["death"] is None and f["handover"] is None
    assert f["picked_up"] is None and f["kills_with"] == 0
    other = fights.for_round(snap, 1, 5)[0]
    assert other["subject"] == 5 and other["side"] == "B"
