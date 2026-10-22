"""Critical Util's per-round evidence (app/critutil/extract.py) on toy rounds through the real engine."""

from app.critutil import extract
from tests.moments.helpers import damage, flash, kill, round_of, shot, toy_ability
from tests.replays.control_toys import open_hall

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
    assert c["harm"] == [{"mate": 0, "how": "blind", "t": 8.2, "dur": 2.0, "died": True}]
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


def test_smoke_between_teams_cuts_sight_lines_and_covered_teammates_lived():
    smoke = toy_ability("Wraith", "4_Smoke", 260, 200, 1, t=5.0, t1=15.0, kind="Zone")
    out = run([smoke])
    (c,) = cast_by(out, 1, "smoke")
    assert c["cover"]["blocked_s"] > 0
    assert set(c["cover"]["covered"]) <= {0, 1}
    assert c["cover"]["covered_lived"] == c["cover"]["covered"]


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
    assert c["harm"] == [{"mate": 0, "how": "concussed", "t": 8.2, "dur": 1.8, "died": True}]
    assert out["avail"]["friendly_status"] is True
