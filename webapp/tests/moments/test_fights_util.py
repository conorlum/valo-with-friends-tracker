"""Utility on / near / smoke near, utility on you, mollied (W6b), through the real engine on toy rounds."""

from app.moments import fights
from tests.moments.helpers import damage, flash, reveal, round_of, status, toy_ability
from tests.replays.control_toys import open_hall

GEO = open_hall()
T0 = 10.0


def px(m):
    return m / GEO.m_per_px


def players(extra=None, mover=None):
    p = {0: ("A", mover or [(0.0, 120, 200, 0)]), 1: ("A", [(0.0, 140, 200, 0)]),
         5: ("B", [(0.0, 400, 160, 180)]), 6: ("B", [(0.0, 400, 240, 180)])}
    p.update(extra or {})
    return p


def fight0(util=(), **kw):
    rnd, data = round_of(players(**kw), util=[damage(T0, 0, 5), damage(T0 + 1, 0, 5), *util], geo=GEO)
    out = fights.round_moments(rnd, data)
    return [f for f in out["fights"] if f["slot"] == 0][0]


def placed(name, dx_m, t=5.0, t1=20.0, by=1, kind="Zone"):
    return toy_ability("Code", name, 400 - px(dx_m), 160, by, t=t, t1=t1, kind=kind)


def test_flash_hit_before_window_not_counted_and_inside_counted_with_null_dur():
    f = fight0([flash(6.0, 1, [[5, T0 - 3.1, 1.0]]), flash(7.0, 1, [[5, T0 - 2.9, None]])])
    assert [(u["by"], u["kind"], u["on"], u["t"], u["dur"]) for u in f["util_on"]] == [(1, "flash", 5, 7.1, None)]
    assert "util_on_them" in f["tags"]


def test_status_with_null_by_counts_for_no_side():
    f = fight0([status(9.0, 12.0, None, 5), status(9.0, 12.0, None, 0)])
    assert f["util_on"] == [] and f["util_on_you"] == []
    assert "util_on_them" not in f["tags"] and "util_on_you" not in f["tags"]


def test_teammate_reveal_on_opponent():
    f = fight0([reveal(8.0, 11.0, 1, 5)])
    assert len(f["util_on"]) == 1 and f["util_on"][0]["kind"] == "reveal" and f["util_on"][0]["on"] == 5
    assert "util_on_them" in f["tags"]


def test_reveal_that_ended_before_the_window_is_not_counted():
    assert fight0([reveal(2.0, 6.0, 1, 5)])["util_on"] == []


def test_enemy_flash_on_player():
    f = fight0([flash(9.0, 5, [[0, 9.5, 1.2]])])
    assert f["util_on_you"] == [{"by": 5, "kind": "flash", "util_kind": "blind", "name": "Flash", "t": 9.5,
                                 "dur": 1.2}]
    assert "util_on_you" in f["tags"] and f["util_on"] == []


def test_nearsight_and_ability_damage_are_utility_on():
    f = fight0([flash(9.0, 1, [[5, 9.2, 2.0]], k="nearsight", name="Nearsight"),
                {**damage(9.5, 1, 5, src="ability"), "name": "Shock"}])
    assert sorted((u["kind"], u["util_kind"]) for u in f["util_on"]) == [("damage", "damaging"), ("nearsight", "status")]


def test_reveal_and_status_on_them_get_their_kinds():
    f = fight0([reveal(8.0, 11.0, 1, 5), status(9.0, 12.0, 1, 5)])
    assert sorted(u["util_kind"] for u in f["util_on"]) == ["info", "status"]


def test_smoke_near_enemy_is_smoke_near_not_util_near():
    f = fight0([placed("4_Smoke", 5.0)])
    assert len(f["smoke_near"]) == 1 and f["util_near"] == []
    assert f["smoke_near"][0]["by"] == 1 and f["smoke_near"][0]["on"] == 5
    assert "smoke_near" in f["tags"] and "util_near_them" not in f["tags"]


def test_deny_ability_at_threshold():
    inside = fight0([placed("E_Turret", 7.9)])
    assert len(inside["util_near"]) == 1 and "util_near_them" in inside["tags"]
    assert abs(inside["util_near"][0]["m"] - 7.9) < 0.11
    assert fight0([placed("E_Turret", 8.1)])["util_near"] == []


def test_near_them_carries_its_kind_and_counts_info_status_blind():
    f = fight0([placed("E_PossessableCamera", 3.0), placed("4_SlowField_Production", 4.0),
                placed("E_HawkFlash_FlashSource", 5.0)])
    assert sorted(u["util_kind"] for u in f["util_near"]) == ["blind", "info", "status"]


def test_heal_teleport_and_walls_are_never_near_them():
    f = fight0([placed("4_Heal_AOE", 3.0), placed("4_UsableTeleport", 3.0), placed("E_Wall_Fortifying", 3.0)])
    assert f["util_near"] == [] and f["smoke_near"] == [] and "util_near_them" not in f["tags"]


def test_ability_placed_after_t0_or_unlisted_is_not_near():
    assert fight0([placed("E_Turret", 3.0, t=T0 + 0.5)])["util_near"] == []
    f = fight0([placed("SomethingElse", 3.0)])
    assert f["util_near"] == [] and f["smoke_near"] == []


def test_ability_of_the_enemy_is_not_near_them():
    assert fight0([placed("E_Turret", 3.0, by=6)])["util_near"] == []


def molly(by, x, y=200, t=9.0, t1=20.0):
    return toy_ability("Phoenix", "MolotovFire", x, y, by, t=t, t1=t1, kind="Patch", owner_by="agent")


LEAVES = [(0.0, 120, 200, 0), (9.2, 120, 200, 0), (11.0, 300, 200, 0)]


def test_player_inside_a_molly_at_its_start_who_leaves_is_mollied():
    f = fight0([molly(5, 120)], mover=LEAVES)
    assert len(f["mollied"]) == 1
    m = f["mollied"][0]
    assert m["by"] == 5 and m["name"] == "molly" and m["t_in"] == 9.0 and m["t_out"] > 9.2
    assert "mollied" in f["tags"]


def test_molly_beside_the_player_does_not_count():
    f = fight0([molly(5, 120 + px(10))], mover=LEAVES)
    assert f["mollied"] == [] and "mollied" not in f["tags"]


def test_own_teams_molly_does_not_count():
    assert fight0([molly(1, 120)], mover=LEAVES)["mollied"] == []


def test_leaving_a_molly_long_before_the_fight_is_not_this_fight_s():
    # C4: Hot Hands burns 4 s (utility.json), so 4.5-8.5 s, overlapping the fight's utility window (7-11 s); the
    # player leaves at about 5 s, before that window
    early = [(0.0, 120, 200, 0), (4.7, 120, 200, 0), (6.0, 300, 200, 0)]
    f = fight0([molly(5, 120, t=4.5, t1=8.5)], mover=early)
    assert f["mollied"] == [] and "mollied" not in f["tags"]
    late = [(0.0, 120, 200, 0), (7.4, 120, 200, 0), (8.4, 300, 200, 0)]      # leaves inside the window: counts
    g = fight0([molly(5, 120, t=4.5, t1=8.5)], mover=late)
    assert len(g["mollied"]) == 1 and g["mollied"][0]["t_out"] >= 7.0


def test_player_who_stays_inside_is_not_mollied():
    assert fight0([molly(5, 120)])["mollied"] == []
