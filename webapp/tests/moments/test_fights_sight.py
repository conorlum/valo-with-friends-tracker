"""Sight tags (W7): died_unseen, killed_unseen, through_smoke, through the real engine on toy rounds."""

from app.moments import fights
from tests.moments.helpers import damage, door_hall, kill, open_hall, round_of, toy_ability

OPEN = open_hall()
T_KILL = 10.0


def fights_of(players, util, geo=OPEN, sight=True, kills=None):
    rnd, data = round_of(players, kills=kills or [kill(T_KILL, 0, 5)], util=util, geo=geo)
    out = fights.round_moments(rnd, data, sight=sight)
    return {f["slot"]: f for f in out["fights"]}


GUN = [damage(9.0, 0, 5), damage(9.5, 0, 5)]
# slot 0 (A) at x 120 faces east (yaw 0); slot 5 (B) at x 400 faces east too, i.e. away from slot 0
FACING_AWAY = {0: ("A", [(0.0, 120, 200, 0)]), 5: ("B", [(0.0, 400, 200, 0)])}
FACING = {0: ("A", [(0.0, 120, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])}


def test_killer_never_seen_is_unseen_for_victim_and_killer_but_killer_saw():
    f = fights_of(FACING_AWAY, GUN)
    kd = f[0]["kill_detail"][0]
    assert kd["measurable"] and kd["killer_saw"] and not kd["victim_saw"] and not kd["through_smoke"]
    assert (kd["killer"], kd["victim"], kd["t"]) == (0, 5, 10.0)
    assert "killed_unseen" in f[0]["tags"] and "died_unseen" in f[5]["tags"]
    assert f[5]["death_detail"] is kd or f[5]["death_detail"] == kd
    assert f[0]["death_detail"] is None and f[5]["kill_detail"] == []
    assert "through_smoke" not in f[0]["tags"]


def test_both_saw_is_measurable_and_untagged():
    f = fights_of(FACING, GUN)
    kd = f[0]["kill_detail"][0]
    assert kd["measurable"] and kd["killer_saw"] and kd["victim_saw"]
    assert not {"killed_unseen", "died_unseen", "through_smoke"} & (set(f[0]["tags"]) | set(f[5]["tags"]))


def test_through_smoke_counts_as_unseen_and_tags_both():
    smoke = toy_ability("Wraith", "4_Smoke", 260, 200, 5, t=1.0, t1=20.0)
    f = fights_of(FACING, [smoke, *GUN])
    kd = f[0]["kill_detail"][0]
    assert kd["through_smoke"] and kd["measurable"] and not kd["killer_saw"] and not kd["victim_saw"]
    for s in (0, 5):
        assert "through_smoke" in f[s]["tags"]
    assert "killed_unseen" in f[0]["tags"] and "died_unseen" in f[5]["tags"]


def walled_players():
    return {0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 260, 200, 180)])}


def test_walls_both_ways_not_measurable():
    f = fights_of(walled_players(), GUN, geo=door_hall())
    kd = f[0]["kill_detail"][0]
    assert not kd["measurable"] and not kd["wall"] and not kd["through_smoke"]
    assert not {"killed_unseen", "died_unseen", "through_smoke"} & (set(f[0]["tags"]) | set(f[5]["tags"]))


def test_wall_bang_is_measurable_and_tagged():
    f = fights_of(walled_players(), [damage(9.0, 0, 5, wall=True), damage(9.5, 0, 5)], geo=door_hall())
    kd = f[0]["kill_detail"][0]
    assert kd["wall"] and kd["measurable"] and not kd["through_smoke"]
    assert "killed_unseen" in f[0]["tags"] and "died_unseen" in f[5]["tags"]


def test_no_gun_damage_is_not_measurable():
    f = fights_of(FACING_AWAY, [damage(9.0, 0, 5, src="ability")])
    kd = f[0]["kill_detail"][0]
    assert not kd["measurable"] and "killed_unseen" not in f[0]["tags"] and "died_unseen" not in f[5]["tags"]


def test_sight_off_nothing_is_measured():
    f = fights_of(FACING_AWAY, GUN, sight=False)
    kd = f[0]["kill_detail"][0]
    assert not kd["measurable"] and not kd["killer_saw"] and not kd["victim_saw"]
    assert not {"killed_unseen", "died_unseen", "through_smoke"} & (set(f[0]["tags"]) | set(f[5]["tags"]))
    assert f[5]["death_detail"] is not None
