"""Spot, time to kill, clean and its reason, angle bands, and the duels' saw_first tags (W8), on toy rounds."""

import pytest

from app.moments import fights
from tests.moments.helpers import damage, kill, open_hall, round_of, shot

OPEN = open_hall()
T_KILL = 10.0
GUN = [damage(9.0, 0, 5), damage(9.5, 0, 5)]
VICTIM = (5, ("B", [(0.0, 400, 200, 180)]))


def fights_of(players, util, sight=True):
    rnd, data = round_of(players, kills=[kill(T_KILL, 0, 5)], util=util, geo=OPEN)
    out = fights.round_moments(rnd, data, sight=sight)
    return {f["slot"]: f for f in out["fights"]}


def killer_from(spot, yaw=0):
    """The killer looks away until just before `spot` (a sample time), then has the victim at `yaw`."""
    return {0: ("A", [(0.0, 120, 200, 90), (spot - 0.01, 120, 200, 90), (spot, 120, 200, yaw)]), VICTIM[0]: VICTIM[1]}


def kd_of(players, util, **kw):
    return fights_of(players, util, **kw)[0]["kill_detail"][0]


def test_first_shot_099_after_spot_is_clean():
    kd = kd_of(killer_from(8.0), [*GUN, shot(8.99, 0), shot(9.3, 0), shot(9.6, 0)])
    assert kd["spot"] == 8.0 and kd["first_shot"] == 8.99
    assert kd["ttk_shot"] == 1.01 and kd["ttk_spot"] == 2.0
    assert kd["clean"] and kd["reason"] is None


def test_first_shot_101_after_spot_is_slow_start():
    kd = kd_of(killer_from(8.0), [*GUN, shot(9.01, 0), shot(9.3, 0), shot(9.6, 0)])
    assert kd["spot"] == 8.0 and kd["first_shot"] == 9.01
    assert not kd["clean"] and kd["reason"] == "slow_start"


def test_fired_before_the_spot():
    kd = kd_of(killer_from(8.0), [*GUN, shot(7.8, 0), shot(8.3, 0), shot(8.8, 0)])
    assert kd["first_shot"] == 7.8 and kd["spot"] == 8.0
    assert not kd["clean"] and kd["reason"] == "fired_before_spot"


def test_shot_within_tolerance_before_spot_is_not_early():
    kd = kd_of(killer_from(8.0), [*GUN, shot(7.96, 0), shot(8.4, 0)])
    assert kd["reason"] is None and kd["clean"]


def test_shot_run_breaks_at_a_gap_over_run_gap():
    kd = kd_of(killer_from(8.0), [*GUN, shot(6.0, 0), shot(8.5, 0), shot(9.1, 0), shot(9.7, 0)])
    assert kd["first_shot"] == 8.5          # 8.5 -> 9.1 -> 9.7 are 0.6 apart; 6.0 is far
    kd = kd_of(killer_from(8.0), [*GUN, shot(8.5, 0), shot(9.11, 0), shot(9.7, 0)])
    assert kd["first_shot"] == 9.11         # 0.61 breaks


def test_shot_after_the_kill_counts_a_little():
    # the walk starts from the last shot at or before kill + SHOT_AFTER_KILL_S; 9.3 -> 10.04 is a 0.74 s gap
    kd = kd_of(killer_from(8.0), [*GUN, shot(9.3, 0), shot(10.04, 0)])
    assert kd["first_shot"] == 10.04
    kd = kd_of(killer_from(8.0), [*GUN, shot(9.3, 0), shot(10.06, 0)])
    assert kd["first_shot"] == 9.3


def test_no_shots():
    kd = kd_of(killer_from(8.0), GUN)
    assert kd["first_shot"] is None and kd["ttk_shot"] is None and not kd["clean"] and kd["reason"] == "no_shots"
    assert kd["spot"] == 8.0 and kd["ttk_spot"] == 2.0


def test_never_saw_keeps_the_first_shot():
    players = {0: ("A", [(0.0, 120, 200, 90)]), VICTIM[0]: ("B", [(0.0, 400, 200, 0)])}
    kd = kd_of(players, [*GUN, shot(9.0, 0)])
    assert kd["spot"] is None and kd["ttk_spot"] is None and kd["angle"] is None and kd["band"] is None
    assert kd["first_shot"] == 9.0 and kd["ttk_shot"] == 1.0
    assert not kd["clean"] and kd["reason"] == "never_saw"


def test_moving_shots_are_not_clean():
    walker = [(8.0 + i * 0.05, 120 + i * 4, 200, 0) for i in range(40)]
    players = {0: ("A", [(0.0, 120, 200, 90), (7.99, 120, 200, 90), *walker]), VICTIM[0]: VICTIM[1]}
    kd = kd_of(players, [*GUN, shot(8.5, 0), shot(8.8, 0), shot(9.1, 0)])
    assert kd["spot"] == 8.0 and not kd["clean"] and kd["reason"] == "moving"
    one = kd_of(players, [*GUN, shot(8.5, 0)])
    assert one["clean"]


def sight_gap_players(n_unseen):
    """Seen from 7.0 and from 8.5; `n_unseen` samples (1/16 s each) unseen just before 8.5."""
    le = 8.5 - (n_unseen + 1) / 16
    track = [(0.0, 120, 200, 90), (6.99, 120, 200, 90), (7.0, 120, 200, 0), (le + 0.005, 120, 200, 0),
             (le + 0.01, 120, 200, 90), (8.49, 120, 200, 90), (8.5, 120, 200, 0)]
    return {0: ("A", track), VICTIM[0]: VICTIM[1]}


def test_sight_gap_of_a_quarter_second_keeps_the_run():
    kd = kd_of(sight_gap_players(4), [*GUN, shot(9.0, 0)])
    assert kd["spot"] == 7.0


def test_sight_gap_over_a_quarter_second_breaks_the_run():
    kd = kd_of(sight_gap_players(5), [*GUN, shot(9.0, 0)])
    assert kd["spot"] == 8.5


@pytest.mark.parametrize("yaw,band,angle", [(5, "on", 5), (6, "near", 6), (20, "near", 20), (21, "off", 21),
                                            (-5, "on", 5), (-40, "off", 40)])
def test_angle_bands(yaw, band, angle):
    kd = kd_of(killer_from(8.0, yaw=yaw), [*GUN, shot(9.0, 0)])
    assert kd["band"] == band and kd["angle"] == angle


def test_angle_is_an_int():
    kd = kd_of(killer_from(8.0, yaw=5.4), [*GUN, shot(9.0, 0)])
    assert isinstance(kd["angle"], int) and kd["angle"] == 5


def test_sight_off_fills_nothing():
    f = fights_of(killer_from(8.0), [*GUN, shot(9.0, 0)], sight=False)
    kd = f[0]["kill_detail"][0]
    assert kd["spot"] is None and kd["first_shot"] is None and not kd["clean"] and kd["reason"] is None
    assert f[0]["duels"] == [] and not {"saw_first", "seen_first", "caught_off_angle"} & set(f[0]["tags"])


def test_no_gun_damage_fills_nothing():
    kd = kd_of(killer_from(8.0), [damage(9.0, 0, 5, src="ability"), shot(9.0, 0)])
    assert kd["spot"] is None and kd["first_shot"] is None and not kd["clean"] and kd["reason"] is None


def test_caught_off_angle():
    # the victim has watched the killer since the window opened (6.0); the killer sees at 8.0 from 40 degrees off
    f = fights_of(killer_from(8.0, yaw=40), GUN)
    duel = f[0]["duels"][0]
    assert duel["opponent"] == 5
    assert duel["they_saw_t"] == 6.0 and duel["you_saw_t"] == 8.0 and duel["you_angle"] == 40
    assert duel["they_angle"] == 0
    assert "seen_first" in f[0]["tags"] and "caught_off_angle" in f[0]["tags"] and "saw_first" not in f[0]["tags"]
    assert "saw_first" in f[5]["tags"] and "caught_off_angle" not in f[5]["tags"]
    assert f[5]["duels"][0]["you_saw_t"] == 6.0 and f[5]["duels"][0]["they_saw_t"] == 8.0


def test_seen_first_on_angle_is_not_caught():
    f = fights_of(killer_from(8.0, yaw=10), GUN)
    assert "seen_first" in f[0]["tags"] and "caught_off_angle" not in f[0]["tags"]


def test_tie_gives_neither_tag():
    both = {0: ("A", [(0.0, 120, 200, 0)]), VICTIM[0]: VICTIM[1]}
    f = fights_of(both, GUN)
    d = f[0]["duels"][0]
    assert d["you_saw_t"] == d["they_saw_t"] == 6.0
    assert not {"saw_first", "seen_first", "caught_off_angle"} & set(f[0]["tags"])


def test_neither_saw_has_no_times_and_no_tags():
    away = {0: ("A", [(0.0, 120, 200, 90)]), VICTIM[0]: ("B", [(0.0, 400, 200, 0)])}
    f = fights_of(away, GUN)
    d = f[0]["duels"][0]
    assert d["you_saw_t"] is None and d["they_saw_t"] is None and d["you_angle"] is None and d["they_angle"] is None
    assert not {"saw_first", "seen_first", "caught_off_angle"} & set(f[0]["tags"])


def test_player_never_saw_is_seen_first_without_caught_off_angle():
    players = {0: ("A", [(0.0, 120, 200, 90)]), VICTIM[0]: VICTIM[1]}
    f = fights_of(players, GUN)
    d = f[0]["duels"][0]
    assert d["you_saw_t"] is None and d["they_saw_t"] == 6.0
    assert "seen_first" in f[0]["tags"] and "caught_off_angle" not in f[0]["tags"]
