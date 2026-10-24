"""Critical Util's per-round evidence (app/critutil/extract.py) on toy rounds through the real engine."""

import pytest

from app.critutil import extract
from tests.moments.helpers import damage, flash, kill, round_of, shot, status, toy_ability
from tests.replays.control_toys import open_hall, uv

GEO = open_hall()


def px(m):
    return m / GEO.m_per_px


def players(extra=None):
    p = {0: ("A", [(0.0, 120, 200, 0)]), 1: ("A", [(0.0, 140, 200, 0)]),
         5: ("B", [(0.0, 400, 160, 180)]), 6: ("B", [(0.0, 400, 240, 180)])}
    p.update(extra or {})
    return p


def run(util=(), kills=(), extra=None, player_state=None, t_end=30.0):
    rnd, data = round_of(players(extra), util=list(util), kills=list(kills), geo=GEO, t_end=t_end)
    if player_state is not None:
        data["player_state"] = player_state
    return extract.round_util(rnd, data)


def cast_by(out, by, kind=None):
    return [c for c in out["casts"] if c["by"] == by and (kind is None or kind in c["kinds"])]


def test_flash_on_enemy_then_teammate_kill_is_affected_and_followed():
    out = run([flash(8.0, 1, [[5, 8.4, 2.0]]), damage(9.0, 0, 5)], kills=[kill(9.2, 0, 5)])
    (c,) = cast_by(out, 1)
    assert "blind" in c["kinds"]
    assert c["enemies"] == [5]
    assert c["kills"] == [{"t": 9.2, "killer": 0, "victim": 5, "ours": True, "ev": "affected"}]
    assert c["followed"] is True


def test_kill_after_grace_is_not_linked():
    out = run([flash(8.0, 1, [[5, 8.4, 1.0]])], kills=[kill(8.4 + 1.0 + 1.5, 0, 5)])
    assert cast_by(out, 1)[0]["kills"] == []


def test_flash_on_teammate_who_dies_while_blind_is_harm():
    out = run([flash(8.0, 1, [[0, 8.2, 2.0]])], kills=[kill(9.0, 5, 0)])
    (c,) = cast_by(out, 1)
    assert c["harm"] == [{"mate": 0, "how": "blind", "t": 8.2, "dur": 2.0, "died": True, "died_t": 9.0}]
    assert c["enemies"] == []


def test_enemy_we_flashed_who_still_kills_us_is_a_failed_follow():
    out = run([flash(8.0, 1, [[5, 8.2, 2.0]])], kills=[kill(9.0, 5, 0)])
    assert cast_by(out, 1)[0]["kills"][0]["ev"] == "failed_follow"


def test_flash_ability_row_merges_into_the_flash_cast():
    out = run([flash(8.0, 1, [[5, 8.4, 2.0]]),
               toy_ability("Guide", "E_HawkFlash_FlashSource", 300, 200, 1, t=8.3, t1=8.6, kind="GameObject")])
    assert len(cast_by(out, 1)) == 1


def test_parts_of_one_cast_merge_and_separate_uses_do_not():
    out = run([toy_ability("Pandemic", "E_SmokeScreenManager", 250, 150, 1, t=5.0, t1=20.0),
               toy_ability("Pandemic", "E_SmokeScreen_Wall", 250, 260, 1, t=5.2, t1=20.0),
               toy_ability("Wraith", "4_SmokeZone_Placed", 250, 150, 1, t=6.0, t1=20.0),
               toy_ability("Wraith", "4_SmokeZone_Placed", 260, 150, 1, t=12.0, t1=20.0)])
    assert len(cast_by(out, 1)) == 3, "the wall and its manager are one; the same zone landing again is another use"


def test_a_recon_projectile_bolt_and_pings_are_one_cast():
    # the shape of a real Sova recon (2026-10-21 export): projectile, bolt, a ping thrown from it, then a second ping
    proj = toy_ability("Hunter", "Q_RevealBolt", 200, 200, 1, t=13.075, t1=14.991, kind="Projectile")
    bolt = toy_ability("Hunter", "Q_SonarBolt", 300, 200, 1, t=13.998, t1=16.296, kind="GameObject")
    ping = toy_ability("Hunter", "Q_SonarPing", 300, 200, 1, t=14.694, t1=16.562, kind="GameObject",
                       thrown={"t0": 13.075})
    ping2 = toy_ability("Hunter", "Q_SonarPing", 300, 200, 1, t=16.296, t1=18.167, kind="GameObject")
    (c,) = cast_by(run([proj, bolt, ping, ping2]), 1)
    assert c["t"] == 13.07 and c["t_end"] == 18.17 and "info" in c["kinds"]


def test_a_nearsight_row_and_its_projectile_and_source_are_one_cast():
    leer = flash(13.607, 1, [[5, 14.8, None]], k="nearsight", name="reyna_leer")
    proj = toy_ability("Vampire", "4_NearsightAoE", 200, 200, 1, t=13.607, t1=14.133, kind="Projectile")
    src = toy_ability("Vampire", "4_NearsightAOE_Source", 300, 200, 1, t=14.133, t1=16.14, kind="GameObject",
                      thrown={"t0": 13.607})
    assert len(cast_by(run([leer, proj, src]), 1)) == 1


def test_two_smokes_thrown_apart_are_two_casts_and_each_lands_where_its_zone_is():
    util = [toy_ability("Wraith", "4_Smoke", 140, 200, 1, t=10.6, t1=13.2, kind="Projectile"),
            toy_ability("Wraith", "4_Smoke", 260, 200, 1, t=12.7, t1=28.7, kind="Zone"),
            toy_ability("Wraith", "4_Smoke", 140, 200, 1, t=17.6, t1=19.8, kind="Projectile"),
            toy_ability("Wraith", "4_Smoke", 300, 120, 1, t=19.3, t1=35.3, kind="Zone")]
    casts = cast_by(run(util), 1, "smoke")
    assert [c["t"] for c in casts] == [10.6, 17.6]
    from tests.replays.control_toys import uv

    assert (casts[0]["u"], casts[0]["v"]) == uv(260, 200), "the zone's spot, not the thrower's"


def test_two_buy_phase_trips_condensed_to_zero_are_two_casts_each_with_its_own_anchor():
    # Lotus round 3 (2026-10-22 export, read-only): both of a Cypher's buy-phase trips start at 0 s, the condenser's
    # clamp, with the second anchor of each on its wire's `end`. The rows in the order the condenser wrote them,
    # and with the anchors first, to show the pairing is by place, not by order.
    a = {"k": "ability", "t": 0.0, "t1": 28.0, "by": 1, "kind": "GameObject", "code": "Gumshoe",
         "name": "4_TripWire", "id": 7884, "u": 4452, "v": 7950, "end": [4452, 8451]}
    a2 = {"k": "ability", "t": 0.0, "t1": 28.0, "by": 1, "kind": "GameObject", "code": "Gumshoe",
          "name": "4_TripWire_SecondWire", "id": 7912, "u": 4452, "v": 8451}
    b = {"k": "ability", "t": 0.0, "t1": 28.0, "by": 1, "kind": "GameObject", "code": "Gumshoe",
         "name": "4_TripWire", "id": 7948, "u": 7131, "v": 7139, "end": [6209, 7139]}
    b2 = {"k": "ability", "t": 0.0, "t1": 28.0, "by": 1, "kind": "GameObject", "code": "Gumshoe",
          "name": "4_TripWire_SecondWire", "id": 7976, "u": 6209, "v": 7139}
    for util in ([a, a2, b, b2], [a2, b2, b, a]):
        rnd, data = round_of(players(), util=[dict(r) for r in util], geo=GEO)
        casts = extract.build_casts(rnd, data)
        assert sorted(sorted(c.ids) for c in casts) == [[7884, 7912], [7948, 7976]]


def test_one_use_whose_same_pieces_land_at_two_places_mid_round_stays_one_cast():
    # Skye's ult: three seekers of one name at once, mid-round. Only the buy phase's clamp splits by place.
    util = [toy_ability("Guide", "X_Pack", 200 + 30 * i, 200, 1, t=20.0 + 0.1 * i, t1=26.0, kind="Pawn", id=40 + i)
            for i in range(3)]
    assert len(cast_by(run(util), 1)) == 1


def test_smoke_between_teams_cuts_sight_lines_and_covered_teammates_lived():
    smoke = toy_ability("Wraith", "4_Smoke", 260, 200, 1, t=5.0, t1=15.0, kind="Zone")
    out = run([smoke])
    (c,) = cast_by(out, 1, "smoke")
    assert c["cover"]["blocked_s"] > 0
    assert set(c["cover"]["covered"]) <= {0, 1}
    assert c["cover"]["covered_lived"] == c["cover"]["covered"]


def test_two_coincident_smokes_share_the_sight_lines_and_the_save_once():
    one = toy_ability("Wraith", "4_Smoke", 260, 200, 1, t=5.0, t1=15.0, kind="Zone")
    (alone,) = cast_by(run([one]), 1, "smoke")
    two = toy_ability("Wraith", "4_Smoke", 260, 200, 0, t=5.0, t1=15.0, kind="Zone")
    out = run([one, two])
    (a,), (b,) = cast_by(out, 1, "smoke"), cast_by(out, 0, "smoke")
    assert a["cover"]["blocked_s"] + b["cover"]["blocked_s"] == pytest.approx(alone["cover"]["blocked_s"], abs=0.02)
    assert sorted(a["cover"]["covered_lived"] + b["cover"]["covered_lived"]) == alone["cover"]["covered_lived"], \
        "each covered teammate who lived is one save, on one cast"


def test_an_enemy_waiting_at_two_mollies_waits_once():
    mover = [(0.0, 400 + px(9), 160, 180), (20.0, 400 + px(9), 160, 180)]
    one = toy_ability("Phoenix", "MolotovFire", 400, 160, 1, t=8.0, t1=14.0)
    (alone,) = cast_by(run([one], extra={5: ("B", mover)}), 1, "damaging")
    two = toy_ability("Phoenix", "MolotovFire", 400, 160, 0, t=8.0, t1=14.0)
    out = run([one, two], extra={5: ("B", mover)})
    waits = [c["presence"]["waiting_s"] for c in out["casts"] if "damaging" in c["kinds"]]
    assert sum(waits) == pytest.approx(alone["presence"]["waiting_s"], abs=0.02) and alone["presence"]["waiting_s"] > 0


SPIKE = (300, 200)


def planted(defuses):
    u, v = uv(*SPIKE)
    return {"k": "ability", "kind": "Bomb", "t": 20.0, "u": u, "v": v, "by": 0, "defuses": defuses}


def test_a_defenders_smoke_never_stops_its_own_teams_defuse():
    smoke = toy_ability("Wraith", "4_Smoke", *SPIKE, 5, t=25.0, t1=40.0, kind="Zone")
    out = run([planted([[30.0, 33.0, 6, False]]), smoke], t_end=60.0)
    (c,) = cast_by(out, 5, "smoke")
    assert c["spike"]["stopped"] == 0 and c["spike"]["attempts"][0]["evidence"] is None


def test_a_stopped_defuse_goes_once_to_the_cast_that_reached_the_defuser():
    fissure = toy_ability("Breach", "E_SweetSpotFissure", *SPIKE, 1, t=29.0, t1=31.0, kind="GameObject")
    stun = status(30.0, 31.5, 1, 6, name="E_SweetSpotFissure", status="concussed")
    molly = toy_ability("Phoenix", "MolotovFire", *SPIKE, 0, t=28.0, t1=34.0)
    smoke = toy_ability("Wraith", "4_Smoke", *SPIKE, 0, t=25.0, t1=40.0, kind="Zone")
    out = run([planted([[30.0, 33.0, 6, False], [36.0, 40.0, 6, True]]), fissure, stun, molly, smoke], t_end=60.0)
    stopped = {tuple(c["names"]): c["spike"]["stopped"] for c in out["casts"] if c["spike"]}
    assert stopped == {("E_SweetSpotFissure",): 1, ("MolotovFire",): 0, ("4_Smoke",): 0}, \
        "an effect on the defuser before a zone on the spike; a smoke alone stops nothing; a finished one counts for none"


def vitals(*points):
    return [{"t": t, "life": life, "hp": hp, "sh": 0, "mhp": 100, "msh": 0} for t, life, hp in points]


def heal(t1, amount, by=1, target=0):
    return {"k": "heal", "t": t1 - 0.5, "t1": t1, "by": by, "target": target, "pool": "hp", "amount": amount, "n": 5,
            "code": "Thorne", "name": "Q_Heal_Production_New", "from": "equippable"}


def test_a_heal_the_later_heal_covered_alone_saved_nothing():
    # 20 HP, healed to 80, then to 100, then 25 damage: without either heal they'd still have lived
    ps = {"version": 1, "vitals": {"0": vitals((5.0, 0, 20), (6.0, 0, 80), (8.0, 0, 100), (10.0, 0, 75))}}
    out = run([heal(6.0, 60), heal(8.0, 20), damage(10.0, 5, 0)], player_state=ps)
    assert [(h["saved"], h["saved_share"]) for h in out["heals"]] == [(False, 0.0), (False, 0.0)]


def test_heals_each_needed_share_the_save_by_amount():
    # 10 HP, +40 to 50, +40 to 90, then 85 damage to 5: without either one they'd have died
    ps = {"version": 1, "vitals": {"0": vitals((5.0, 0, 10), (6.0, 0, 50), (8.0, 0, 90), (10.0, 0, 5))}}
    out = run([heal(6.0, 40), heal(8.0, 40, by=0), damage(10.0, 5, 0)], player_state=ps)
    assert [h["saved_share"] for h in out["heals"]] == [0.5, 0.5]


def test_damage_in_a_later_life_is_not_what_a_heal_saved_them_from():
    ps = {"version": 1, "vitals": {"0": vitals((5.0, 0, 20), (6.0, 0, 80), (12.0, 1, 100), (13.0, 1, 5))}}
    (h,) = run([heal(6.0, 60)], player_state=ps)["heals"]
    assert h["saved"] is False
    (h,) = run([heal(6.0, 60)], player_state={"version": 1})["heals"]
    assert h["saved"] is None and h["saved_share"] is None, "no vitals: unavailable, never false"


def test_each_slot_s_ult_is_reported_for_the_match_to_judge():
    ps = {"version": 1, "ult": {"0": [{"t": -30.0, "n": 7}], "1": [{"t": -30.0, "n": 6}, {"t": 12.0, "n": 0}]}}
    util = [toy_ability("Sarge", "X_Orbital", 300, 200, 1, t=12.0, t1=20.0)]
    ults = run(util, player_state=ps)["ults"]
    assert ults["0"] == {"cap_seen": None, "end": 7, "t": 30.0, "used": False}, "held all round, no drop to prove 7"
    assert ults["1"]["cap_seen"] == 6 and ults["1"]["used"] is True


def test_molly_damage_by_id_is_zone_damage_on_the_kill():
    molly = toy_ability("Phoenix", "MolotovFire", 400, 160, 1, t=8.0, t1=14.0, id=77)
    hit = damage(8.5, 1, 5, t1=9.0, src="ability")
    hit.update(code="Phoenix", name="MolotovFire", id=77)
    out = run([molly, hit], kills=[kill(10.0, 0, 5)])
    (c,) = cast_by(out, 1, "damaging")
    assert c["kills"][0]["ev"] == "zone_damage"
    assert any(e["how"] == "damage" for e in c["effects"])


def test_enemy_waiting_outside_a_molly_is_waiting_not_held_back():
    mover = [(0.0, 400 + px(9), 160, 180), (20.0, 400 + px(9), 160, 180)]   # stands just outside the ring edge
    molly = toy_ability("Phoenix", "MolotovFire", 400, 160, 1, t=8.0, t1=14.0)
    out = run([molly], extra={5: ("B", mover)})
    p = cast_by(out, 1, "damaging")[0]["presence"]
    assert p["waiting_s"] > 0 and 5 not in p["entered"] and p["held_back"] == []


def test_enemy_who_comes_to_the_edge_and_turns_back_is_held_back():
    mover = [(0.0, 400 + px(30), 160, 180), (9.0, 400 + px(7), 160, 180), (13.0, 400 + px(25), 160, 0)]
    molly = toy_ability("Phoenix", "MolotovFire", 400, 160, 1, t=8.0, t1=14.0)
    out = run([molly], extra={5: ("B", mover)})
    assert cast_by(out, 1, "damaging")[0]["presence"]["held_back"] == [5]


def test_a_flash_has_no_presence_and_a_device_triggers_on_its_first_enemy():
    out = run([flash(8.0, 1, [[5, 8.4, 2.0]])])
    assert cast_by(out, 1)[0]["presence"] is None
    trip = toy_ability("Gumshoe", "4_TripWire", 380, 160, 1, t=2.0, t1=25.0, kind="GameObject")
    from tests.moments.helpers import status

    tether = status(11.0, 13.0, 1, 5, name="4_TripWire", status="tethered")
    (c,) = cast_by(run([trip, tether]), 1, "device")
    assert c["trigger"] == 11.0


def test_heal_that_kept_a_player_alive_through_damage_above_their_health_is_saved():
    ps = {"version": 1, "vitals": {"0": [{"t": 5.0, "life": 0, "hp": 30, "sh": 0, "mhp": 100, "msh": 50},
                                         {"t": 8.5, "life": 0, "hp": 90, "sh": 0, "mhp": 100, "msh": 50},
                                         {"t": 10.0, "life": 0, "hp": 40, "sh": 0, "mhp": 100, "msh": 50},
                                         {"t": 11.0, "life": 0, "hp": 20, "sh": 0, "mhp": 100, "msh": 50}]}}
    heal = {"k": "heal", "t": 6.0, "t1": 8.5, "by": 1, "target": 0, "pool": "hp", "amount": 60.0, "n": 20,
            "code": "Thorne", "name": "Q_Heal_Production_New", "from": "equippable", "hp_after": 90.0}
    out = run([heal, damage(10.0, 5, 0), damage(11.0, 5, 0)], player_state=ps)
    (h,) = out["heals"]
    assert h["hp_before"] == 30 and h["taken_after"] == 70 and h["saved"] is True and h["fought"] is True


def test_death_holding_a_flash_after_engaging_first_is_unused_util():
    ps = {"version": 1, "charges": {"0": {"Q": [{"t": 0.0, "n": 2}]}}}
    util = [shot(8.0, 0), damage(8.1, 0, 5), damage(9.0, 5, 0)]
    rnd, data = round_of(players(), util=util, kills=[kill(9.5, 5, 0)], geo=GEO)
    data["players"][0]["agent"] = "Breach"
    data["player_state"] = ps
    out = extract.round_util(rnd, data)
    assert out["unused"] == [{"slot": 0, "t": 9.5, "kind": "held_at_death", "abilities": ["Flashpoint"], "kinds": ["blind"]}]


def test_ambushed_death_makes_no_unused_claim():
    ps = {"version": 1, "charges": {"0": {"Q": [{"t": 0.0, "n": 2}]}}}
    rnd, data = round_of(players(), util=[damage(9.0, 5, 0)], kills=[kill(9.5, 5, 0)], geo=GEO)
    data["players"][0]["agent"] = "Breach"
    data["player_state"] = ps
    assert extract.round_util(rnd, data)["unused"] == []


def test_output_is_plain_and_versioned():
    import json

    out = run([flash(8.0, 1, [[5, 8.4, 2.0]])], kills=[kill(9.2, 0, 5)])
    assert out["v"] == extract.VERSION
    json.dumps(out)


def test_a_stun_on_a_teammate_flagged_friendly_is_harm():
    from tests.moments.helpers import status

    fault = toy_ability("Breach", "E_SweetSpotFissure", 200, 200, 1, t=8.0, t1=9.0, kind="GameObject")
    stun = {**status(8.2, 10.0, 1, 0, name="E_SweetSpotFissure", status="concussed"), "friendly": True}
    out = run([fault, stun], kills=[kill(9.5, 5, 0)])
    (c,) = cast_by(out, 1)
    assert c["harm"] == [{"mate": 0, "how": "concussed", "t": 8.2, "dur": 1.8, "died": True, "died_t": 9.5}]
    assert out["avail"]["friendly_status"] is True


# --- the ability table: kinds, type, ability_id, and how long a row with no end lasts (W10)


def _one(code, name, **kw):
    out = run([toy_ability(code, name, 250, 150, 1, **kw)])
    (c,) = cast_by(out, 1)
    return out, c


def test_wingman_is_a_status_not_an_ult():
    _, c = _one("Aggrobot", "SeekerNade", t=5.0, t1=7.0, kind="GameObject")
    assert "ult" not in c["kinds"] and c["type"] == "status" and c["ability_id"] == "Gekko/Wingman"


def test_thrash_and_steel_garden_are_ults():
    _, c = _one("Aggrobot", "RollyPolly", t=5.0, t1=9.0, kind="GameObject")
    assert "ult" in c["kinds"] and c["type"] == "ult" and c["ability_id"] == "Gekko/Thrash"
    _, c = _one("Nox", "DisarmPulse", t=5.0, t1=9.0, kind="GameObject")
    assert "ult" in c["kinds"] and c["type"] == "ult" and c["ability_id"] == "Vyse/Steel Garden"


def test_a_row_with_no_end_lasts_the_tables_duration():
    _, c = _one("Sarge", "Q_Molotov_Production", t=5.0, t1=None)
    assert c["t_end"] == 13.0 and c["type"] == "molly" and c["end_unknown"] is False


def test_a_placeable_with_no_end_runs_to_t_decided():
    out, c = _one("Killjoy", "Q_StealthAlarmbot", t=5.0, t1=None)
    assert c["type"] == "placeable" and c["t_end"] == out["t_decided"] and c["end_unknown"] is False


def test_an_unknown_ability_with_no_end_is_end_unknown_and_other():
    out, c = _one("Zzz", "E_Mystery", t=5.0, t1=None)
    assert c["t_end"] == out["t_decided"] and c["end_unknown"] is True
    assert c["type"] == "other" and c["ability_id"] is None


def test_no_40_second_cap():
    assert not hasattr(extract, "LIFE_CAP_S")
    _, c = _one("Zzz", "E_Mystery", t=2.0, t1=None)
    assert c["t_end"] > 30.0 - 1e-9 or c["end_unknown"]
    out = run([toy_ability("Zzz", "E_Mystery", 250, 150, 1, t=2.0, t1=None)], t_end=100.0)
    (c,) = cast_by(out, 1)
    assert out["t_decided"] > 50.0 and c["t_end"] == out["t_decided"] and c["end_unknown"] is True


def test_an_ult_the_table_does_not_know_is_still_type_ult():
    _, c = _one("Zzz", "X_Mystery", t=5.0, t1=9.0)
    assert "ult" in c["kinds"] and c["type"] == "ult" and c["ability_id"] is None


def test_kind_label_is_unchanged_for_one_cast_per_old_kind():
    from app.services.replay_util import kind_label

    cases = [(("Wraith", "4_Smoke"), "smoke"), (("Sarge", "Q_Molotov_Production"), "damaging")]
    for (code, name), label in cases:
        _, c = _one(code, name, t=5.0, t1=9.0)
        assert kind_label(c["kinds"]) == label, (code, name, c["kinds"])
    out = run([flash(8.0, 1, [[5, 8.4, 2.0]])])
    assert kind_label(cast_by(out, 1)[0]["kinds"]) == "blind"
