"""Fight split, outcome, trades and teammates (W5), through the real engine on toy rounds."""

import math

import pytest

from app.moments import fights
from app.moments.constants import TRADE_RANGE_M
from tests.moments.helpers import damage, kill, round_of, shot
from tests.replays.control_toys import open_hall, uv

GEO = open_hall()


def px(m):
    return m / GEO.m_per_px


def team(mate_m=30.0, extra=()):
    """A: slot 0 at x 120, slot 1 `mate_m` east of it, slot 2 (optional extras). B: slots 5, 6 far east."""
    p = {0: ("A", [(0.0, 120, 200, 0)]), 1: ("A", [(0.0, 120 + px(mate_m), 200, 0)]),
         5: ("B", [(0.0, 400, 160, 180)]), 6: ("B", [(0.0, 400, 240, 180)])}
    p.update(extra)
    return p


def run(players, **kw):
    rnd, data = round_of(players, **kw)
    return fights.round_moments(rnd, data)


def of(out, slot):
    return [f for f in out["fights"] if f["slot"] == slot]


def test_envelope_and_every_field_present():
    out = run(team(), util=[damage(10, 0, 5)])
    assert out["v"] == 1 and out["sight"] is True
    f = of(out, 0)[0]
    for key in ("slot", "i", "t0", "t1", "opponents", "first", "man", "kills", "died", "trade", "moving",
                "mates", "util_on", "util_near", "smoke_near", "util_on_you", "mollied", "duels",
                "kill_detail", "death_detail", "tags"):
        assert key in f
    assert f["kill_detail"] == [] and f["death_detail"] is None and f["i"] == 0


def test_gap_of_2_9_s_is_one_fight_and_3_1_s_is_two():
    one = of(run(team(), util=[damage(10, 0, 5), damage(12.9, 0, 5)]), 0)
    assert len(one) == 1 and one[0]["t0"] == 10 and one[0]["t1"] == pytest.approx(12.9)
    two = of(run(team(), util=[damage(10, 0, 5), damage(13.1, 0, 5)]), 0)
    assert [f["i"] for f in two] == [0, 1] and two[1]["t0"] == pytest.approx(13.1)


def test_a_damage_run_s_end_counts_for_the_gap_and_the_fight_end():
    # B1: a run 10-12.5 s then damage at 14 s is 1.5 s apart, one fight; the run's end is the fight's end
    [f] = of(run(team(), util=[damage(10, 0, 5, t1=12.5), damage(14, 0, 5)]), 0)
    assert f["t0"] == 10 and f["t1"] == pytest.approx(14)
    [g] = of(run(team(), util=[damage(10, 0, 5, t1=12.5)]), 0)
    assert g["t1"] == pytest.approx(12.5)
    [h] = of(run(team(), util=[damage(10, 0, 5, t1=25)], t_decided=20.0), 0)   # clipped to t_decided
    assert h["t1"] == pytest.approx(20)


def test_a_teammate_damage_run_that_began_before_the_fight_counts_as_with():
    # C1: the fight is 10-11 s; teammate 1, 2.8 m away, deals ability damage 9.9-10.2 s
    util = [damage(10, 0, 5), damage(11, 0, 5), damage(9.9, 1, 5, t1=10.2, src="ability")]
    [f] = [f for f in of(run(team(2.8), util=util), 0) if f["t0"] == 10]
    assert f["mates"]["state"] == "with" and f["mates"]["with_slot"] == 1 and "with_teammate" in f["tags"]
    ended = [damage(10, 0, 5), damage(11, 0, 5), damage(9.0, 1, 5, t1=9.5, src="ability")]
    [g] = [f for f in of(run(team(2.8), util=ended), 0) if f["t0"] == 10]
    assert "with_teammate" not in g["tags"]     # a run that ended before the fight still doesn't


def test_events_after_t_decided_are_dropped():
    out = run(team(), util=[damage(10, 0, 5), damage(25, 0, 5)], t_decided=20.0)
    [f] = of(out, 0)
    assert f["t1"] == 10
    assert of(run(team(), util=[damage(25, 0, 5)], t_decided=20.0), 0) == []


def test_a_death_only_the_db_knows_is_not_a_fight():
    out = run(team(), db_deaths=[(0, 10.0)])
    assert out["fights"] == []


def test_same_team_damage_is_not_a_fight():
    assert run(team(), util=[damage(10, 0, 1)])["fights"] == []


def test_both_trade_verdicts_use_unrounded_times():
    # B6: 6.001 s apart is outside the window for the death AND for the avenger, though 16.004 rounds to 16.0
    out = run(team(10), kills=[kill(10.003, 5, 0), kill(16.004, 1, 5)])
    [dead] = of(out, 0)
    assert dead["trade"]["died"] == "not_traded"
    avenger = [f for f in of(out, 1) if f["kills"]][0]
    assert avenger["trade"]["avenged"] == [] and "traded_teammate" not in avenger["tags"]


@pytest.mark.parametrize("second,traded", [(16.0, True), (16.01, False)])
def test_trade_at_the_window_edge(second, traded):
    out = run(team(10), kills=[kill(10, 5, 0), kill(second, 1, 5)])
    [dead] = of(out, 0)
    assert dead["died"] == {"t": 10, "killer": 5}
    if traded:
        assert dead["trade"]["died"] == "traded" and dead["trade"]["by"] == 1
        assert dead["trade"]["after_s"] == pytest.approx(6.0)
        assert "died_traded" in dead["tags"]
    else:
        assert dead["trade"]["died"] == "not_traded" and dead["trade"]["by"] is None
        assert "died_not_traded" in dead["tags"]
    # the avenger's fight: victim 5 had killed teammate 0 within the window before
    avenger = [f for f in of(out, 1) if f["kills"]][0]
    if traded:
        assert avenger["trade"]["avenged"] == [{"victim": 5, "teammate": 0}]
        assert "traded_teammate" in avenger["tags"]
    else:
        assert avenger["trade"]["avenged"] == [] and "traded_teammate" not in avenger["tags"]
    assert avenger["trade"]["died"] is None


def test_a_fight_without_a_death_has_no_trade_verdict():
    [f] = of(run(team(), util=[damage(10, 0, 5)]), 0)
    assert f["died"] is None and f["trade"]["died"] is None and f["trade"]["avenged"] == []


def test_teammate_at_19_9_m_is_neither_alone_nor_with():
    [f] = of(run(team(19.9), kills=[kill(10, 5, 0)]), 0)
    assert f["mates"]["state"] is None and f["mates"]["nearest_slot"] == 1
    assert f["mates"]["nearest_m"] == pytest.approx(19.9, abs=0.1)
    assert not {"alone", "with_teammate", "no_teammate"} & set(f["tags"])


def test_teammate_at_20_1_m_is_alone():
    [f] = of(run(team(20.1), kills=[kill(10, 5, 0)]), 0)
    assert f["mates"]["state"] == "alone" and f["mates"]["nearest_m"] > TRADE_RANGE_M
    assert "alone" in f["tags"]


def test_a_teammate_in_range_who_damaged_an_opponent_is_with():
    out = run(team(10), util=[damage(9.0, 0, 5), damage(9.5, 1, 5), damage(10.0, 0, 5)])
    [f] = of(out, 0)
    assert f["mates"]["state"] == "with" and f["mates"]["with_slot"] == 1
    assert f["mates"]["with_m"] == pytest.approx(10.0, abs=0.1)
    assert "with_teammate" in f["tags"] and "alone" not in f["tags"]


def _aimed(deg):
    """Slot 1 (10 m east of slot 0, at x~120+) shoots from its position, aimed `deg` off the bearing to slot 5."""
    p = team(10)
    sx, sy = p[1][1][0][1], p[1][1][0][2]
    bearing = math.atan2(160 - sy, 400 - sx) + math.radians(deg)
    u, v = uv(sx, sy)
    u1, v1 = uv(sx + 100 * math.cos(bearing), sy + 100 * math.sin(bearing))
    return p, [damage(9.0, 0, 5), shot(9.2, 1, u=u, v=v, u1=u1, v1=v1), damage(10.0, 0, 5)]


def test_a_teammate_who_shot_at_the_opponent_is_with():
    p, util = _aimed(0)
    [f] = of(run(p, util=util), 0)
    assert f["mates"]["state"] == "with" and f["mates"]["with_slot"] == 1


def test_a_teammate_who_shot_25_degrees_away_is_not_with():
    p, util = _aimed(25)
    [f] = of(run(p, util=util), 0)
    assert f["mates"]["state"] is None and f["mates"]["with_slot"] is None


def test_aimed_shots_do_not_count_when_the_rule_is_off(monkeypatch):
    monkeypatch.setattr(fights, "WITH_AIM_DEG", -1)
    p, util = _aimed(0)
    [f] = of(run(p, util=util), 0)
    assert f["mates"]["with_slot"] is None


def test_a_shot_row_without_an_end_point_does_not_count():
    p, util = _aimed(0)
    del util[1]["u1"]
    [f] = of(run(p, util=util), 0)
    assert f["mates"]["with_slot"] is None


def test_a_teammate_out_of_range_who_fired_does_not_make_it_with():
    [f] = of(run(team(25), util=[damage(9.0, 0, 5), damage(9.5, 1, 5), damage(10.0, 0, 5)]), 0)
    assert f["mates"]["state"] == "alone" and f["mates"]["with_slot"] is None


def test_no_teammate_alive():
    out = run(team(), kills=[kill(2, 6, 1), kill(10, 5, 0)])
    [f] = [f for f in of(out, 0)]
    assert f["mates"] == {"state": "none", "nearest_m": None, "nearest_slot": None, "with_slot": None,
                          "with_m": None}
    assert "no_teammate" in f["tags"]
    assert f["man"] == [1, 2]


def test_first_and_man():
    players = team(10, extra={2: ("A", [(0.0, 120, 120, 0)])})
    out = run(players, util=[damage(10, 0, 5), damage(10.5, 6, 0)])
    [f] = of(out, 0)
    assert f["first"] == "player" and f["man"] == [3, 2] and f["opponents"] == [5, 6]
    [g] = of(out, 6)
    assert g["first"] == "player" and g["man"] == [2, 3] and g["opponents"] == [0]
    [h] = of(run(players, util=[damage(9, 6, 0), damage(10, 0, 5)]), 0)
    assert h["first"] == "opponent" and h["t0"] == 9


def test_kills_and_died_come_from_kill_rows():
    out = run(team(10), kills=[kill(10, 0, 5), kill(11, 6, 0)])
    [f] = of(out, 0)
    assert f["kills"] == [{"victim": 5, "t": 10}] and f["died"] == {"t": 11, "killer": 6}
