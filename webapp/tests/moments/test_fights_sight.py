"""Sight tags (W7): died_unseen, killed_unseen, through_smoke, through the real engine on toy rounds."""

from app.control import engine as ce
from app.moments import fights
from tests.moments.helpers import blob, damage, door_hall, flash, kill, open_hall, round_of, toy_ability

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


def test_neither_saw_on_open_ground_without_smoke_is_not_through_smoke():
    # B3: the killer faces north, the victim faces away; the bare line is clear and nothing blocks it
    side_on = {0: ("A", [(0.0, 120, 200, 90)]), 5: ("B", [(0.0, 400, 200, 0)])}
    f = fights_of(side_on, GUN)
    kd = f[0]["kill_detail"][0]
    assert not kd["killer_saw"] and not kd["victim_saw"]
    assert not kd["through_smoke"] and kd["measurable"]          # C3: a clear line is still measurable
    assert "through_smoke" not in f[0]["tags"] and "through_smoke" not in f[5]["tags"]
    assert "killed_unseen" in f[0]["tags"] and "died_unseen" in f[5]["tags"]


def test_both_flashed_on_a_clear_line_keeps_the_unseen_tags():
    # C3: facing each other in the open but both flashed through the whole sight window (6-10 s)
    util = [flash(5.5, 5, [[0, 5.9, 5.0]]), flash(5.5, 0, [[5, 5.9, 5.0]]), *GUN]
    f = fights_of(FACING, util)
    kd = f[0]["kill_detail"][0]
    assert not kd["killer_saw"] and not kd["victim_saw"] and kd["measurable"] and not kd["through_smoke"]
    assert "killed_unseen" in f[0]["tags"] and "died_unseen" in f[5]["tags"]


def test_the_spot_never_reaches_back_into_the_victim_s_earlier_life():
    # C2: slot 5 dies at 3 s (to slot 1), is revived at 9 s and killed by slot 0 at 10 s. Slot 0 has looked at
    # that place the whole round; the spot is in 5's second life, not on the corpse.
    players = {0: ("A", [(0.0, 120, 200, 0)]), 1: ("A", [(0.0, 120, 260, 0)]), 5: ("B", [(0.0, 400, 200, 0)])}
    data = blob(players, util=[damage(9.5, 0, 5), damage(10.0, 0, 5)], deaths={5: 10.0})
    data["alive"]["5"] = [[0.0, 3.0, "kill"], [9.0, 10.0, "kill"]]
    data["kills"] = [kill(3.0, 1, 5), kill(10.0, 0, 5, i=1)]
    rnd = ce.RoundInputs(data, OPEN, ce.ControlLink())
    out = fights.round_moments(rnd, data)
    kd = [f for f in out["fights"] if f["slot"] == 0 and f["kills"]][0]["kill_detail"][0]
    assert kd["spot"] is not None and kd["spot"] >= 9.0


def test_a_dead_opponent_is_never_first_seen():
    # B4: slot 0 kills 5 at 10 s without seeing them, keeps fighting 6 to 11 s, and turns east at 10.25 s,
    # toward where 5's track still is. 5 is dead by then, so slot 0 never "saw" 5.
    players = {0: ("A", [(0.0, 120, 200, 180), (10.2, 120, 200, 180), (10.25, 120, 200, 0)]),
               5: ("B", [(0.0, 400, 200, 0)]), 6: ("B", [(0.0, 400, 240, 0)])}
    util = [damage(9.0, 0, 5), damage(9.5, 0, 5), damage(10.5, 0, 6), damage(11.0, 0, 6)]
    f = fights_of(players, util)
    duel = [d for d in f[0]["duels"] if d["opponent"] == 5][0]
    assert duel["you_saw_t"] is None
    seen6 = [d for d in f[0]["duels"] if d["opponent"] == 6][0]
    assert seen6["you_saw_t"] is not None    # the living opponent in the same view is still seen


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


def victim_turns_away_at(t):
    """The victim faces the killer until t, then faces away for good."""
    return {0: ("A", [(0.0, 120, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180), (t, 400, 200, 180),
                                                      (t + 0.05, 400, 200, 0)])}


def test_sight_counts_from_look_s_before_the_first_damage():
    # first gun damage at 9.0, so the window opens at 9.0 - SIGHT_LOOK_S = 6.0
    assert fights_of(victim_turns_away_at(7.0), GUN)[0]["kill_detail"][0]["victim_saw"]
    assert not fights_of(victim_turns_away_at(5.5), GUN)[0]["kill_detail"][0]["victim_saw"]


def test_sight_off_nothing_is_measured():
    f = fights_of(FACING_AWAY, GUN, sight=False)
    kd = f[0]["kill_detail"][0]
    assert not kd["measurable"] and not kd["killer_saw"] and not kd["victim_saw"]
    assert not {"killed_unseen", "died_unseen", "through_smoke"} & (set(f[0]["tags"]) | set(f[5]["tags"]))
    assert f[5]["death_detail"] is not None
