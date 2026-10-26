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


def run(util=(), kills=(), extra=None, player_state=None, t_end=30.0, agents=None):
    rnd, data = round_of(players(extra), util=list(util), kills=list(kills), geo=GEO, t_end=t_end)
    for p in data["players"]:
        if p["slot"] in (agents or {}):
            p["agent"] = agents[p["slot"]]
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


def test_a_sentinels_trip_earns_no_presence_after_its_owner_dies():
    # slot 5 stands on the wire all round; the Cypher (slot 1) dies at 6 s, and the wire is off from then
    wire = toy_ability("Gumshoe", "4_TripWire", 400, 160, 1, t=2.0, t1=None, kind="GameObject")
    out = run([wire], kills=[kill(6.0, 6, 1)])
    (c,) = cast_by(out, 1)
    assert c["presence"]["armed_s"] == pytest.approx(4.0, abs=0.01)
    p = c["presence"]
    assert 5 in p["entered"] and 0 < p["inside_s"] <= 4.0 * len(p["entered"]) + 1e-9


def test_util_up_before_the_plant_can_stop_a_defuse_while_it_still_burns():
    molly = toy_ability("Phoenix", "MolotovFire", *SPIKE, 0, t=18.0, t1=34.0)
    out = run([planted([[30.0, 33.0, 6, False]]), molly], t_end=60.0)
    (c,) = cast_by(out, 0, "damaging")
    assert c["spike"]["stopped"] == 1 and c["spike"]["attempts"][0]["evidence"] == "zone"


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
    use = {"t": None, "ability_id": "Jett/Blade Storm", "type": "ult", "signals": {"ult_end": None, "death": None}}
    assert ults["0"] == {"cap_seen": None, "end": 7, "t": 30.0, "used": False,
                         "use": use}, "held all round, no drop to prove 7 (the toy's slot 0 is a Jett)"
    assert ults["1"]["cap_seen"] == 6 and ults["1"]["used"] is True


def ult_state(slot=0, drop=None):
    pts = [{"t": -30.0, "n": 8}] + ([{"t": drop, "n": 0}] if drop is not None else [])
    return {"version": 1, "ult": {str(slot): pts}}


def test_chamber_use_is_the_first_tour_de_force_shot_and_ends_at_the_drop_after_it():
    util = [shot(5.0, 0, gun="Vandal"), shot(12.0, 0, gun="Tour de Force"), shot(14.0, 0, gun="Tour de Force")]
    ps = ult_state(0, drop=12.1)
    ps["ult"]["0"].insert(1, {"t": 3.0, "n": 0})      # a drop before the first use is not its end
    out = run(util, kills=[kill(20.0, 5, 0)], player_state=ps, agents={0: "Chamber"})
    assert out["ults"]["0"]["use"] == {"t": 12.0, "ability_id": "Chamber/Tour De Force", "type": "ult",
                                       "signals": {"ult_end": 12.1, "death": 20.0}}


def test_a_timed_ult_use_is_one_ult_cast_with_its_window_counted_once_in_the_baseline():
    from app.services import util_values

    util = [shot(12.0, 0, gun="Tour de Force"), shot(14.0, 0, gun="Tour de Force")]
    ps = ult_state(0, drop=12.1)
    out = run(util, kills=[kill(20.0, 5, 0)], player_state=ps, agents={0: "Chamber"})
    (c,) = [c for c in out["casts"] if c.get("from_use")]
    assert (c["by"], c["t"], c["t_end"], c["type"], c["kinds"]) == (0, 12.0, 12.1, "ult", ["ult"])
    assert c["ability_id"] == "Chamber/Tour De Force" and c["ult_points"] == 8
    data = {**out, "on_known": True}
    ult_segs = [segs for side, typ, segs in util_values.up_segments(data) if typ == "ult"]
    assert len(ult_segs) == 1, "the cast, not the use as well"
    assert util_values.value_round(data, None)["casts"][c["i"]]["found_nobody"] is False


def test_a_tour_de_force_kill_is_the_ults_whole_kill_and_an_operator_kill_is_not(monkeypatch):
    from app.services import util_values

    util = [shot(12.0, 0, gun="Tour de Force"), shot(13.0, 0, gun="Tour de Force"), shot(16.0, 0, gun="Operator")]
    out = run(util, kills=[kill(13.0, 0, 5), kill(16.0, 0, 6)], player_state=ult_state(0, drop=25.0),
              agents={0: "Chamber"})
    (c,) = [c for c in out["casts"] if c.get("from_use")]
    assert c["kills"] == [{"t": 13.0, "killer": 0, "victim": 5, "ours": True, "ev": "ult_weapon"}]
    monkeypatch.setattr(util_values, "swings", lambda rd, model: {
        util_values._kill_key(k["t"], k["killer"], k["victim"]): 0.2 for k in rd["kills"]})
    led = util_values.ledger(out, None)[c["i"]]
    assert led["value"] == pytest.approx(0.2), "the whole swing: the ult was the gun"


def test_jett_use_is_the_first_knife_hit_from_the_equippable():
    miss = {**damage(6.0, 0, 5, src="ability"), "name": "X_Dagger_Production", "from": "ability"}
    hit = {**damage(9.0, 0, 5, src="ability"), "name": "X_Dagger_Production", "from": "equippable"}
    out = run([miss, hit], player_state=ult_state(0), agents={0: "Jett"})
    use = out["ults"]["0"]["use"]
    assert use["t"] == 9.0 and use["ability_id"] == "Jett/Blade Storm"
    assert use["signals"] == {"ult_end": None, "death": None}


def test_neon_use_is_the_first_shot_with_no_gun():
    util = [shot(7.0, 0, gun="Vandal"), shot(11.0, 0, gun=None)]
    out = run(util, player_state=ult_state(0), agents={0: "Neon"})
    assert out["ults"]["0"]["use"]["t"] == 11.0


def frenzy_heal(t, by=0):
    return {"k": "heal", "t": t, "t1": t + 2.5, "by": by, "target": by, "pool": "hp", "amount": 50.0,
            "code": "Vampire", "name": "X_Frenzy", "from": "causer"}


def test_reyna_use_is_her_empress_heal_and_ends_at_the_drop():
    ps = ult_state(0, drop=42.7)
    out = run([frenzy_heal(27.5), frenzy_heal(35.0)], kills=[kill(42.7, 5, 0)], player_state=ps,
              agents={0: "Reyna"}, t_end=60.0)
    assert out["ults"]["0"]["use"] == {"t": 27.5, "ability_id": "Reyna/Empress", "type": "ult",
                                       "signals": {"ult_end": 42.7, "death": 42.7}}


def test_reyna_at_full_health_has_no_use_sign_and_other_agents_have_no_use():
    out = run([shot(7.0, 0, gun=None)], player_state=ult_state(0), agents={0: "Reyna", 1: "Sova"})
    assert out["ults"]["0"]["use"] == {"t": None, "ability_id": "Reyna/Empress", "type": "ult",
                                       "signals": {"ult_end": None, "death": None}}
    assert out["ults"]["1"]["use"] is None


def test_a_use_after_the_slot_died_is_not_a_use():
    util = [shot(12.0, 0, gun="Tour de Force")]
    out = run(util, kills=[kill(10.0, 5, 0)], player_state=ult_state(0), agents={0: "Chamber"})
    assert out["ults"]["0"]["use"]["t"] is None


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


def test_an_astra_star_is_its_own_entry_not_an_ult_or_an_ult_use():
    ps = {"version": 1, "ult": {"1": [{"t": -30.0, "n": 7}]}}
    out = run([toy_ability("Rift", "X_Markers", 250, 150, 1, t=5.0, t1=None, kind="GameObject")], player_state=ps)
    (c,) = cast_by(out, 1)
    assert c["ability_id"] == "Astra/Astral Form star"
    assert "ult" not in c["kinds"] and c["type"] == "other"
    assert out["ults"]["1"]["used"] is False


def test_movement_cast_rows_are_dashes_one_cast_per_use():
    # the condenser's shape (app/replays/extras.py, revision 14): no place, no lifetime
    util = [{"k": "cast", "t": 4.0, "by": 1, "code": "Wushu", "name": "E_Dash"},
            {"k": "cast", "t": 9.0, "by": 0, "code": "Terra", "name": "Q_DoubleDash"},
            {"k": "cast", "t": 15.0, "by": 1, "code": "Wushu", "name": "E_Dash"}]
    out = run(util)
    got = sorted((c["t"], c["by"], c["ability_id"], c["type"], tuple(c["kinds"])) for c in out["casts"])
    assert got == [(4.0, 1, "Jett/Tailwind", "dash", ("dash",)), (9.0, 0, "Waylay/Lightspeed", "dash", ("dash",)),
                   (15.0, 1, "Jett/Tailwind", "dash", ("dash",))]


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


# --- pieces join their casts by the ability table; flash and nearsight casts stay short (W11)


def _paint_shells(t, by=1):
    util = [toy_ability("Clay", "4_Projectile_Primary", 200, 200, by, t=t, t1=t + 1.0, kind="Projectile"),
            toy_ability("Clay", "4_Projectile_SecondarySpawner", 300, 200, by, t=t + 1.0, t1=t + 1.2, kind="Projectile")]
    util += [toy_ability("Clay", "4_Projectile_Secondary", 300 + 10 * i, 200, by, t=t + 1.1 + 0.1 * i, t1=t + 2.0,
                         kind="Projectile") for i in range(4)]
    return util


def test_paint_shells_primary_spawner_and_secondaries_are_one_cast_and_a_second_use_is_another():
    assert len(cast_by(run(_paint_shells(5.0)), 1)) == 1
    assert len(cast_by(run(_paint_shells(5.0) + _paint_shells(15.0)), 1)) == 2


def test_a_piece_of_another_slot_does_not_join():
    util = [toy_ability("Clay", "4_Projectile_Primary", 200, 200, 1, t=5.0, t1=6.0, kind="Projectile"),
            toy_ability("Clay", "4_Projectile_SecondarySpawner", 300, 200, 0, t=6.0, t1=6.2, kind="Projectile")]
    out = run(util)
    assert len(cast_by(out, 1)) == 1 and len(cast_by(out, 0)) == 1


def test_hot_hands_projectile_and_molotov_fire_are_one_molly_cast():
    util = [toy_ability("Phoenix", "4_Molotov_Production", 200, 200, 1, t=5.0, t1=6.0, kind="Projectile"),
            toy_ability("Phoenix", "MolotovFire", 300, 200, 1, t=6.0, t1=11.0, kind="Zone")]
    (c,) = cast_by(run(util), 1)
    assert c["type"] == "molly"


def test_dizzy_chain_is_one_cast():
    util = [toy_ability("Aggrobot", "E_DiscTurret_PowerWave", 200, 200, 1, t=5.0, t1=8.0, kind="GameObject"),
            toy_ability("Aggrobot", "Zamboni_Rocket", 250, 200, 1, t=6.0, t1=7.0, kind="Projectile"),
            toy_ability("Aggrobot", "E_OrbSpawner", 300, 200, 1, t=8.0, t1=9.0, kind="GameObject"),
            toy_ability("Aggrobot", "Reclaim_Orb_Turret", 300, 210, 1, t=9.0, t1=12.0, kind="GameObject")]
    (c,) = cast_by(run(util), 1)
    assert c["t"] == 5.0 and c["t_end"] == 12.0


def test_fast_lane_beside_a_ground_strike_is_its_own_cast():
    util = [toy_ability("Sprinter", "4_GroundStrike", 200, 200, 1, t=5.0, t1=6.0, kind="Projectile"),
            toy_ability("Sprinter", "Q_ElectricSphere", 300, 200, 1, t=6.0, t1=9.0, kind="Zone"),
            toy_ability("Sprinter", "4_Tunnel", 300, 220, 1, t=6.2, t1=9.0, kind="Zone")]
    out = run(util)
    assert len(cast_by(out, 1)) == 2


def test_flashpoint_flash_and_its_through_walls_twin_are_one_cast():
    util = [flash(8.0, 1, [[5, 8.4, 2.0]], name="breach_flashpoint"),
            toy_ability("Breach", "Q_ThroughWalls_Flash", 300, 200, 1, t=8.1, t1=8.5, kind="GameObject")]
    out = run(util)
    assert len(cast_by(out, 1)) == 1


def test_arc_rose_with_no_flash_row_stays_its_own_cast():
    util = [toy_ability("Nox", "StealthingTrap_Flash_2", 300, 200, 1, t=8.0, t1=20.0, kind="GameObject")]
    assert len(cast_by(run(util), 1)) == 1


def test_a_flash_keeps_a_short_span_beside_a_long_lived_row():
    f = flash(8.0, 1, [[5, 8.4, 2.0]], name="phoenix_curveball_left")
    f["pop"] = {"t": 8.8}
    util = [f, toy_ability("Phoenix", "E_FlareCurve_Synced", 200, 200, 1, t=8.0, t1=8.5, kind="Projectile"),
            toy_ability("Zzz", "E_Long", 300, 200, 1, t=8.2, t1=33.0, kind="Zone")]
    casts = cast_by(run(util, t_end=40.0), 1)
    flashed = [c for c in casts if "blind" in c["kinds"]]
    assert len(flashed) == 1 and flashed[0]["t_end"] <= 9.8 and len(casts) == 2


def test_a_nearsight_keeps_its_own_rows_end():
    n = flash(13.0, 1, [[5, 13.5, None]], k="nearsight", name="reyna_leer")
    n["t1"] = 15.0
    util = [n, toy_ability("Vampire", "4_NearsightAoE", 200, 200, 1, t=13.0, t1=13.5, kind="Projectile"),
            toy_ability("Zzz", "E_Long", 300, 200, 1, t=13.2, t1=38.0, kind="Zone")]
    casts = cast_by(run(util, t_end=40.0), 1)
    near = [c for c in casts if "status" in c["kinds"]]
    assert len(near) == 1 and near[0]["t_end"] <= 16.0 and len(casts) == 2


def test_a_real_shaped_flash_row_finds_its_entry_and_keeps_its_lingering_projectile():
    # real flash rows carry no code and their name as `ability`; the Curveball's projectile lives on ~1.5 s past the pop
    f = flash(88.5, 1, [[5, 89.1, 2.0]])
    del f["name"]
    f.update(ability="phoenix_curveball_right", pop={"t": 89.1})
    util = [f, toy_ability("Phoenix", "E_FlareCurve_Synced_Right", 200, 200, 1, t=88.5, t1=90.6, kind="Projectile")]
    (c,) = cast_by(run(util, t_end=100.0), 1)
    assert c["ability_id"] == "Phoenix/Curveball" and c["type"] == "flash" and c["t_end"] == 90.1


def test_spycam_dart_and_its_stuck_piece_are_one_cast_apart_from_the_camera():
    util = [toy_ability("Gumshoe", "E_CameraTrackingDart", 200, 200, 1, t=5.0, t1=6.0, kind="Projectile"),
            toy_ability("Gumshoe", "RemovableObject_GumshoeTrackingDart", 300, 200, 1, t=6.0, t1=14.0, kind="GameObject"),
            toy_ability("Gumshoe", "E_PossessableCamera", 150, 200, 1, t=5.5, t1=None, kind="GameObject")]
    assert len(cast_by(run(util), 1)) == 2


# --- window signals (W12)


def sig(out, by=1):
    return cast_by(out, by)[0]["signals"]


def test_signals_base_keys_are_always_present_and_empty_when_absent():
    s = sig(run([toy_ability("Zz", "E_Thing", 200, 200, 1, t=8.0, t1=12.0, kind="GameObject")]))
    assert set(s) == {"landed", "seen_end", "pop", "hits", "effect_ends", "on", "off", "armed", "ult_drop"}
    assert s["pop"] is None and s["hits"] == [] and s["effect_ends"] == [] and s["on"] == [] and s["off"] == []
    assert s["armed"] is None and s["ult_drop"] is None and s["landed"] == 8.0


def test_signals_seen_end_is_the_max_of_the_pieces_ends_and_ignores_projectiles():
    util = [toy_ability("Clay", "X_Rocket_a", 200, 200, 1, t=8.0, t1=9.0, kind="GameObject"),
            toy_ability("Clay", "X_Rocket_b", 210, 200, 1, t=8.0, t1=99.0, kind="GameObject", gone=11.5),
            toy_ability("Clay", "X_Rocket_p", 220, 200, 1, t=8.0, t1=20.0, kind="Projectile")]
    assert sig(run(util, t_end=40.0))["seen_end"] == 11.5


def test_signals_a_row_closed_by_the_round_ending_is_not_seen():
    util = [toy_ability("Gumshoe", "4_TripWire", 200, 200, 1, t=0.0, t1=30.0, kind="GameObject")]
    assert sig(run(util, t_end=30.0))["seen_end"] is None
    util = [toy_ability("Zz", "E_Thing", 200, 200, 1, t=8.0, t1=None, kind="GameObject")]
    assert sig(run(util, t_end=30.0))["seen_end"] is None


def test_signals_pop_and_hits_on_a_real_shaped_flash():
    f = flash(88.5, 1, [[5, 89.1, 2.0], [6, 89.3, None], [0, 89.2, 1.0]])
    del f["name"]
    f.update(ability="phoenix_curveball_right", pop={"t": 89.126})
    s = sig(run([f], t_end=100.0))
    assert s["pop"] == 89.13
    assert s["hits"] == [[89.1, 91.1, True], [89.3, 90.8, False]]
    assert s["effect_ends"] == [[91.1, True], [90.8, False]]


def test_signals_effect_ends_skip_damage_and_reveal_and_keep_an_unknown_nearsight_end():
    n = flash(13.0, 1, [[5, 13.5, None]], k="nearsight", name="reyna_leer")
    n["t1"] = 15.0
    rev = {"k": "reveal", "t": 13.2, "t1": 16.0, "by": 1, "target": 6, "code": "Vampire", "name": "reyna_leer"}
    s = sig(run([n, rev], t_end=40.0))
    assert s["effect_ends"] == [[15.0, False]]
    assert s["hits"] == [[13.5, 15.0, False]]


def test_signals_on_and_off_are_sorted_concatenations_of_the_rows():
    util = [toy_ability("Zz", "E_A", 200, 200, 1, t=8.0, t1=30.0, kind="GameObject", on=[[20.0, 22.0]],
                        off=[[25.0, None]]),
            toy_ability("Zz", "E_B", 210, 200, 1, t=8.0, t1=30.0, kind="GameObject", on=[[10.0, 12.0]],
                        off=[[15.0, 16.0]])]
    s = sig(run(util, t_end=40.0))
    assert s["on"] == [[10.0, 12.0], [20.0, 22.0]]
    assert s["off"] == [[15.0, 16.0], [25.0, None]]


def test_signals_armed_is_the_landing_time_for_a_placeable_only():
    trip = [toy_ability("Gumshoe", "4_TripWire", 200, 200, 1, t=0.0, t1=20.0, kind="GameObject")]
    assert sig(run(trip, t_end=30.0))["armed"] == 0.0
    other = [toy_ability("Zz", "E_Thing", 200, 200, 1, t=8.0, t1=12.0, kind="GameObject")]
    assert sig(run(other))["armed"] is None


def test_signals_go_off_is_when_a_nanoswarm_was_set_off():
    grenade = toy_ability("Killjoy", "4_RemoteBees_MultiDetonate", 200, 200, 1, t=1.0, t1=20.0, kind="Projectile")
    swarm = toy_ability("Killjoy", "4_BeeSwarm_Damage", 200, 200, 1, t=20.0, t1=24.0, kind="GameObject")
    s = sig(run([grenade, swarm], t_end=40.0, agents={1: "Killjoy"}))
    assert s["go_off"] == 20.0
    hidden = toy_ability("Killjoy", "4_RemoteBees_MultiDetonate", 200, 200, 1, t=1.0, t1=40.0, kind="Projectile")
    assert sig(run([hidden], t_end=40.0, agents={1: "Killjoy"}))["go_off"] is None


def test_signals_go_off_is_razorvines_patch_not_its_hidden_object():
    hidden = toy_ability("Nox", "BarbedWire", 200, 200, 1, t=0.0, t1=34.6, kind="GameObject")
    patch = toy_ability("Nox", "BarbedWire", 200, 200, 1, t=34.6, t1=41.1, kind="Patch")
    out = run([hidden, patch], t_end=60.0, agents={1: "Vyse"})
    assert len(cast_by(out, 1)) == 1                        # the patch is the same use going off, not a second one
    assert sig(out)["go_off"] == 34.6
    never = toy_ability("Nox", "BarbedWire", 200, 200, 1, t=0.0, t1=59.0, kind="GameObject")
    assert sig(run([never], t_end=60.0, agents={1: "Vyse"}))["go_off"] is None


def test_signals_possessed_are_the_cameras_looked_through_spans():
    cam = toy_ability("Gumshoe", "E_PossessableCamera", 200, 200, 1, t=5.4, t1=76.2, kind="Pawn",
                      possessed=[[28.8, 29.4], [6.3, 8.6]])
    assert sig(run([cam], t_end=80.0, agents={1: "Cypher"}))["possessed"] == [[6.3, 8.6], [28.8, 29.4]]
    trip = toy_ability("Gumshoe", "4_TripWire", 200, 200, 1, t=0.0, t1=20.0, kind="GameObject")
    assert "possessed" not in sig(run([trip], t_end=30.0))


def test_signals_owner_death_is_when_a_placeables_owner_died():
    trip = toy_ability("Gumshoe", "4_TripWire", 200, 200, 1, t=0.0, t1=29.0, kind="GameObject")
    kills = [kill(8.0, 1, 5), kill(12.0, 6, 1)]             # an enemy's death first: only the owner's counts
    assert sig(run([trip], kills=kills, t_end=30.0, agents={1: "Cypher"}))["owner_death"] == 12.0
    assert sig(run([trip], t_end=30.0, agents={1: "Cypher"}))["owner_death"] is None
    smoke = toy_ability("Zz", "E_Thing", 200, 200, 1, t=8.0, t1=12.0, kind="GameObject")
    assert "owner_death" not in sig(run([smoke], kills=[kill(10.0, 5, 1)]))


def buff(t, by, target, t1):
    return {"k": "buff", "t": t, "by": by, "t1": t1, "target": target, "code": "Iris", "name": "Q_Harmonize"}


def test_harmonize_on_a_teammate_is_one_stim_cast_to_the_last_buff_end():
    out = run([buff(10.0, 1, 1, 21.0), buff(10.0, 1, 0, 17.6)], t_end=40.0, agents={1: "Miks"})
    cs = cast_by(out, 1)
    assert len(cs) == 1 and cs[0]["ability_id"] == "Miks/Harmonize" and cs[0]["type"] == "stim"
    assert cs[0]["signals"]["seen_end"] == 21.0          # Miks' own buff, refreshed by a kill
    two = run([buff(5.0, 1, 1, 13.0), buff(25.0, 1, 1, 33.0)], t_end=40.0, agents={1: "Miks"})
    assert len(cast_by(two, 1)) == 2


OMEN = ("Wraith", "X_GlobalTeleport_Intention")


def omen_run(ps, t=20.0, **extra):
    return run([toy_ability(*OMEN, 200, 200, 1, t=t, t1=t + 4.0, kind="GameObject", **extra)], player_state=ps,
               t_end=60.0)


def test_signals_ult_drop_is_taken_inside_twelve_seconds_and_not_before():
    got = sig(omen_run({"ult": {"1": [{"t": 0.0, "n": 7}, {"t": 19.0, "n": 0}]}}))
    assert got["ult_drop"] == 19.0
    old = sig(omen_run({"ult": {"1": [{"t": 0.0, "n": 7}, {"t": 7.0, "n": 0}]}}))
    assert old["ult_drop"] is None
    assert "ult_drop" in sig(run([toy_ability("Zz", "E_Thing", 200, 200, 1, t=8.0, t1=12.0)]))


def test_signals_omen_arrival_and_outcome():
    s = sig(omen_run(None, outcome="landed"))
    assert s["arrival"] == 24.0 and s["arrival_outcome"] == "landed"
    util = [toy_ability(*OMEN, 200, 200, 1, t=20.0, t1=None, kind="GameObject")]
    s = sig(run(util, t_end=60.0))
    assert s["arrival"] is None and s["arrival_outcome"] is None
    assert "moved" not in s and "duel_end" not in s


def test_signals_thrash_moved_from_possessed_else_the_rows_t():
    mk = lambda **x: [toy_ability("Aggrobot", "RollyPolly", 200, 200, 1, t=10.0, t1=40.0, kind="GameObject", **x)]
    assert sig(run(mk(possessed=[[12.5, 20.0]]), t_end=60.0))["moved"] == 12.5
    assert sig(run(mk(possessed=[]), t_end=60.0))["moved"] == 10.0
    assert "arrival" not in sig(run(mk(), t_end=60.0))


DUEL = [toy_ability("Sequoia", "X_LineCapture", 200, 200, 1, t=10.0, t1=30.0, kind="GameObject")]


def test_signals_duel_won_lost_and_none():
    won = sig(run(DUEL, kills=[kill(12.0, 1, 5)], t_end=60.0))
    assert won["duel_end"] == 12.0 and won["duel_won"] is True
    lost = sig(run(DUEL, kills=[kill(12.0, 5, 1)], t_end=60.0))
    assert lost["duel_end"] == 12.0 and lost["duel_won"] is False
    none = sig(run(DUEL, kills=[kill(12.0, 0, 5), kill(23.0, 1, 6)], t_end=60.0))
    assert none["duel_end"] is None and none["duel_won"] is None


def test_signals_capture_end_is_the_latest_cocoon_end():
    util = [toy_ability("Cable", "X_FishingHook", 200, 200, 1, t=10.0, t1=30.0, kind="GameObject"),
            status(12.0, 18.0, 1, 5, name="X_FishingHook_Cage", status="cocooned"),
            status(12.5, 20.5, 1, 6, name="X_FishingHook_Cage", status="cocooned")]
    for u in util[1:]:
        u["code"] = "Cable"
    s = sig(run(util, t_end=60.0))
    assert s["capture_end"] == 20.5
    none = sig(run(util[:1], t_end=60.0))
    assert none["capture_end"] is None


def test_signals_until_end_and_until_death_rules(monkeypatch):
    orig = extract._entry_for
    entry = {"match": "^(Zz_X_Blade)$", "agent": "Jett", "ability": "Blade Storm", "type": "ult", "kinds": ["ult"],
             "rule": "ult:until_end", "grace_s": 0, "pieces": [], "duration_s": None}
    monkeypatch.setattr(extract, "_entry_for", lambda k: entry if k == "Zz_X_Blade" else orig(k))
    util = [toy_ability("Zz", "X_Blade", 200, 200, 1, t=10.0, t1=None, kind="GameObject")]
    ps = {"ult": {"1": [{"t": 0.0, "n": 8}, {"t": 9.5, "n": 0}, {"t": 18.0, "n": 0}, {"t": 30.0, "n": 1}]}}
    s = sig(run(util, kills=[kill(25.0, 5, 1)], player_state=ps, t_end=60.0))
    assert s["ult_end"] == 18.0
    assert s["death"] == 25.0 and s["ult_drop"] == 9.5
    ps2 = {"ult": {"1": [{"t": 0.0, "n": 8}, {"t": 10.0, "n": 0}, {"t": 21.0, "n": 0}]}}
    s = sig(run(util, player_state=ps2, t_end=60.0))
    assert s["ult_end"] == 21.0 and s["death"] is None


# --- heal tags: death, kept_alive, hits_after


def test_heal_kept_alive_when_more_was_lost_than_hp_before():
    ps = {"version": 1, "vitals": {"0": vitals((5.0, 0, 20), (6.0, 0, 80), (7.0, 0, 40), (8.0, 0, 10))}}
    (h,) = run([heal(6.0, 60)], player_state=ps)["heals"]
    assert h["kept_alive"] is True and h["death"] is None


def test_heal_not_kept_alive_when_loss_is_at_or_under_hp_before():
    ps = {"version": 1, "vitals": {"0": vitals((5.0, 0, 20), (6.0, 0, 80), (8.0, 0, 60))}}
    (h,) = run([heal(6.0, 60)], player_state=ps)["heals"]
    assert h["kept_alive"] is False and h["hits_after"] is None
    ps = {"version": 1, "vitals": {"0": vitals((5.0, 0, 20), (6.0, 0, 80), (8.0, 0, 70))}}
    (h,) = run([heal(6.0, 60)], player_state=ps)["heals"]
    assert h["kept_alive"] is False
    (h,) = run([heal(6.0, 60)], player_state={"version": 1})["heals"]
    assert h["kept_alive"] is None and h["hits_after"] is None


def test_heal_death_is_the_first_kill_of_the_target_at_or_after_the_heal_end():
    ps = {"version": 1, "vitals": {"0": vitals((5.0, 0, 20), (6.0, 0, 80), (7.0, 0, 40))}}
    out = run([heal(6.0, 60)], kills=[kill(5.5, 5, 6), kill(12.0, 5, 0)], player_state=ps)
    assert out["heals"][0]["death"] == 12.0


def test_heal_hits_after_counts_enemy_gun_runs_from_the_crossing():
    ps = {"version": 1, "vitals": {"0": vitals((5.0, 0, 20), (6.0, 0, 80), (7.0, 0, 50), (7.5, 0, 30),
                                                 (9.0, 0, 5))}}
    util = [heal(6.0, 60), damage(6.9, 5, 0, t1=7.2), damage(7.0, 5, 0), damage(7.5, 5, 0, n=2),
            damage(7.6, 1, 0), damage(7.7, 5, 0, src="ability"), damage(7.8, 0, 5), damage(20.0, 5, 0)]
    (h,) = run(util, kills=[kill(10.0, 5, 0)], player_state=ps)["heals"]
    assert h["kept_alive"] is True and h["death"] == 10.0
    assert h["hits_after"] == 3, "the 6.9 run began before the crossing at 7.0; ability, mate, own and late rows don't count"


# --- Omen spike tags


def _spike(*entries):
    return {"version": 1, "spike": [dict(zip(("t", "s", "slot"), e)) if len(e) == 3 else {"t": e[0], "s": e[1]}
                                    for e in entries]}


def _omen_spike(ps):
    return sig(omen_run(ps))


def test_omen_picked_up_the_spike_within_fifteen_seconds():
    ps = _spike((0.0, "carried", 5), (22.0, "dropped"), (30.0, "carried", 1), (40.0, "planted"))
    s = _omen_spike(ps)
    assert s["spike_pickup"] == 30.0 and s["got_spike"] is True and s["got_spike_planted"] is True
    ps = _spike((0.0, "carried", 5), (22.0, "dropped"), (30.0, "carried", 1))
    assert _omen_spike(ps)["got_spike_planted"] is False


def test_omen_pickup_too_late_or_by_a_teammate_is_not_got_spike():
    s = _omen_spike(_spike((0.0, "carried", 5), (22.0, "dropped"), (39.5, "carried", 1)))
    assert s["spike_pickup"] is None and s["got_spike"] is False and s["got_spike_planted"] is None
    s = _omen_spike(_spike((0.0, "carried", 5), (22.0, "dropped"), (30.0, "carried", 0)))
    assert s["spike_pickup"] == 30.0 and s["got_spike"] is False
    assert _omen_spike({"version": 1})["got_spike"] is None


def test_omen_pickup_without_a_slot_goes_to_the_nearest_alive_player():
    u, v = (140 * 10000 / extract.engine.PX, 200 * 10000 / extract.engine.PX)
    drop = {"t": 22.0, "s": "dropped", "u": u, "v": v}
    ps = {"version": 1, "spike": [{"t": 0.0, "s": "carried", "slot": 5}, drop, {"t": 30.0, "s": "carried"}]}
    s = _omen_spike(ps)
    assert s["got_spike"] is True and s["spike_pickup"] == 30.0
    u2, v2 = (120 * 10000 / extract.engine.PX, 200 * 10000 / extract.engine.PX)
    ps["spike"][1] = {"t": 22.0, "s": "dropped", "u": u2, "v": v2}
    assert _omen_spike(ps)["got_spike"] is False
    ps["spike"][1] = {"t": 22.0, "s": "dropped"}
    s = _omen_spike(ps)
    assert s["got_spike"] is None and s["spike_pickup"] == 30.0
