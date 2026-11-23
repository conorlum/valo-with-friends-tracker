"""One opening per fight, using 125 Hz tracks and slot-only toy data."""

import math

import numpy as np
import pytest

from app.control import engine, height_motion, heights
from app.moments import fights, peek_constants as pc, peeks
from tests.moments.helpers import cast, damage, kill, shot
from tests.replays import control_toys as toys


M_PER_PX = .14
KEYS = sorted("slot i opponent t0 t1 t_event t_first_sight t_onset opener type reason jiggles "
              "speed_mps sideways_m exposure_s repeek_gap_s repeek_spot_m distance_m band result gun_class".split())


def standing(x=230, y=200, side="A"):
    return side, [(0.0, x, y, 0)]


def steps(*intervals, near=False):
    hidden, exposed = (238, 245) if near else (200, 280)
    points = [(0.0, 230, hidden, 0)]
    for on, off in intervals:
        points += [(on - .008, 230, hidden, 0), (on, 230, exposed, 0)]
        if off is not None:
            points += [(off - .008, 230, exposed, 0), (off, 230, hidden, 0)]
    return "A", points


def round_of(player=None, enemy=None, *, util=(), kills=(), extra=None, geo=None, t_end=24):
    players = {0: player or steps((12, None)), 5: enemy or standing(330, 280, "B"), **(extra or {})}
    data = toys.blob(players, t_end=t_end, util=util, deaths={k["victim"]: k["t"] for k in kills})
    for slot, (_, points) in players.items():
        data["tracks"][str(slot)] = toys.track(points, 0, t_end, hz=125)
    data["hz"], data["kills"] = 125, list(kills)
    return engine.RoundInputs(data, geo or toys.midwall_hall(), engine.ControlLink(db_deaths=())), data


def row(rnd, blob, *, onset=12, i=0, opponent=5):
    found = next(r for r in peeks.round_openings(rnd, blob) if r["slot"] == 0 and r["i"] == i)
    assert found["opponent"] == opponent
    if onset is None:
        assert found["t_onset"] is None
    else:
        assert found["t_onset"] == pytest.approx(onset, abs=.035)
    assert sorted(found) == KEYS
    return found


def corner_setup(rnd, before=11.75, after=12.05):
    assert peeks.line_clear(rnd, 0, 5, before) is False
    assert peeks.line_clear(rnd, 0, 5, after) is True


def swing(speed=5.4, stop=280):
    return "A", [(0, 230, 200, 0), (10, 230, 200, 0),
                 (10 + (stop - 200) * M_PER_PX / speed, 230, stop, 0)]


def test_held_enemy_enters_line_and_player_wins():
    enemy = "B", [(0, 330, 120, 180), (11.992, 330, 120, 180), (12, 330, 280, 180)]
    rnd, blob = round_of(standing(230, 280), enemy, util=[damage(12.25, 0, 5)], kills=[kill(12.5, 0, 5)])
    corner_setup(rnd)
    r = row(rnd, blob)
    assert (r["type"], r["opener"], r["result"], r["reason"]) == ("held", "holder", "won", None)
    assert r["speed_mps"] == 0
    assert r["t_first_sight"] is not None


@pytest.mark.parametrize("speed,stop,label,reason", [(5.4, 280, "wide_swing", None),
                                                    (5.4, 248 + .5 / M_PER_PX, "other", "fast_short"),
                                                    (3.0, 280, "slow_peek", None)])
def test_continuous_corner_peeks(speed, stop, label, reason):
    event = 10 + 41 * M_PER_PX / speed + .2
    rnd, blob = round_of(swing(speed, stop), util=[damage(event, 0, 5)])
    corner_setup(rnd, 10, event)
    onset = peeks.exposures(rnd, 0, 5, event - 10, event + .1)[0]["on"]
    r = row(rnd, blob, onset=onset)
    assert (r["type"], r["reason"]) == (label, reason)
    if label == "wide_swing":
        assert r["sideways_m"] >= 1.5
        assert r["speed_mps"] >= 4.5


def test_jiggle_is_adjective_on_later_wide_swing():
    player = "A", steps((9, 9.3))[1] + swing()[1][1:]
    event = 11.28125
    rnd, blob = round_of(player, util=[damage(event, 0, 5)])
    corner_setup(rnd, 10, event)
    exps = peeks.exposures(rnd, 0, 5, event - 10, event + .1)
    assert len(exps) == 2 and peeks.is_jiggle_exposure(rnd, 0, 5, exps[0])
    r = row(rnd, blob, onset=exps[1]["on"])
    assert (r["type"], r["jiggles"], r["repeek_gap_s"]) == ("wide_swing", 1, None)


@pytest.mark.parametrize("won", [False, True])
def test_jiggle_kill_on_closing_sample(won):
    player = "A", [(0, 230, 200, 0), (11.992, 230, 200, 0), (12, 230, 280, 0),
                   (12.2, 230, 280, 0), (12.5, 230, 200, 0)]
    killer, victim = (0, 5) if won else (5, 0)
    rnd, blob = round_of(player, util=[damage(12.25, 5, 0)], kills=[kill(12.3125, killer, victim)])
    corner_setup(rnd)
    r = row(rnd, blob)
    assert (r["type"], r["result"]) == ("jiggle", "won" if won else "lost")
    assert peeks.windowed_velocity(rnd, 0, 12.3125)[1] < 0


@pytest.mark.parametrize("offset,is_repeek", [(0, True), (3, False)])
def test_repeek_same_fight_and_spot_limit(offset, is_repeek):
    player = "A", steps((8, 9.5), near=True)[1] + [(9.55, 230, 238, 0), (9.6, 230, 200, 0),
        (10.4, 230 - offset / M_PER_PX, 200, 0), (10.492, 230 - offset / M_PER_PX, 200, 0),
        (10.5, 230 - offset / M_PER_PX, 245, 0)]
    rnd, blob = round_of(player, util=[damage(10.75, 0, 5)])
    corner_setup(rnd, 10.25, 10.55)
    r = row(rnd, blob, onset=10.5)
    assert (r["type"] == "re_peek") is is_repeek
    assert r["jiggles"] == 0
    assert r["repeek_gap_s"] == pytest.approx(1, abs=.04)
    # A gradual close makes the recorded ending spot slightly inside cover.
    assert (r["repeek_spot_m"] <= 2) is is_repeek


def test_repeek_across_four_second_fight_gap():
    rnd, blob = round_of(steps((7, 8.5), (12.5, None), near=True),
                         util=[damage(8.25, 0, 5), damage(12.75, 0, 5)])
    corner_setup(rnd, 12.25, 12.55)
    r = row(rnd, blob, onset=12.5, i=1)
    assert r["type"] == "re_peek"
    assert r["repeek_gap_s"] == pytest.approx(4, abs=.04)


def test_repeek_damage_run_overlaps_window_without_prior_exposure():
    # The damage run ends at the later peek's spot, while the enemy is behind cover.
    player = "A", [(0, 230, 280, 0), (11, 230, 280, 0), (11.25, 230, 200, 0),
                   (11.992, 230, 200, 0), (12, 230, 280, 0)]
    enemy = "B", [(0, 330, 120, 0), (11.992, 330, 120, 0), (12, 330, 280, 0)]
    rnd, blob = round_of(player, enemy, util=[damage(6, 5, 0, t1=11, src="ability"), damage(12.25, 0, 5)])
    corner_setup(rnd)
    r = row(rnd, blob)
    assert r["type"] == "re_peek"
    assert (r["repeek_gap_s"], r["repeek_spot_m"]) == (1, 0)


def test_first_event_stays_in_earlier_main_exposure():
    rnd, blob = round_of(steps((9, 10.5), (12, None)), util=[damage(9.25, 0, 5), damage(12.25, 0, 5)])
    corner_setup(rnd, 8.75, 9.05)
    r = row(rnd, blob, onset=9)
    assert r["type"] != "re_peek"
    assert r["t_event"] == 9.25
    assert r["exposure_s"] == pytest.approx(1.5, abs=.04)


def arc(t, onset, *, velocity=6, offset=0):
    elapsed = t - (onset + offset - velocity / heights.GRAVITY_MPS2)
    return np.maximum(0, velocity * elapsed - heights.GRAVITY_MPS2 / 2 * elapsed ** 2)


def add_height(rnd, blob, slot, profile, onset):
    for seg in blob["tracks"][str(slot)]:
        t = seg["t0"] + np.arange(len(seg["u"])) / blob["hz"]
        z = np.rint(profile(t, onset) * 10).astype(int)
        seg["z"] = np.diff(z, prepend=0).tolist()
    return engine.RoundInputs(blob, rnd.geo, engine.ControlLink(db_deaths=()))


def motion_round(profile=arc, *, slot=0, player=None, enemy=None, util=()):
    event = 11.28125
    rnd, blob = round_of(player or swing(), enemy, util=[*util, damage(event, 0, 5)])
    blob["movement_casts"] = 1
    corner_setup(rnd, 10, event)
    onset = peeks.exposures(rnd, 0, 5, event - 10, event + .1)[0]["on"]
    rnd = add_height(rnd, blob, slot, profile, onset)
    [(s, t, x, y, z)] = [track for track in height_motion.tracks(blob) if track[0] == slot]
    assert s == slot and blob["hz"] == 125 and z is not None
    mask = height_motion.airborne(x, y, z, blob["hz"], rnd.geo.m_per_px)
    return rnd, blob, onset, t, mask


def test_jump_peek_real_arc():
    rnd, blob, onset, t, mask = motion_round(lambda t, onset: arc(t, onset, velocity=math.sqrt(48)))
    assert mask[np.abs(t - onset) <= .25].any()
    assert not height_motion.blackouts(blob, rnd.geo)
    assert np.max(rnd.track_z[0]) == pytest.approx(1.2)
    [(start, end, rise)] = peeks._flights(blob, rnd.geo)[0]
    assert start <= onset <= end
    assert rise == pytest.approx(1.2, abs=.1)
    r = row(rnd, blob, onset=onset)
    assert (r["opener"], r["type"], r["reason"]) == ("peeker", "jump_peek", None)


@pytest.mark.parametrize("ability", [False, True])
def test_falling_round_corner_is_normal_or_ability_peek(ability):
    def fall(t, onset):
        elapsed = np.maximum(0, t - (onset - .2))
        return np.maximum(0, 2 - heights.GRAVITY_MPS2 / 2 * elapsed ** 2)

    rnd, blob, onset, t, mask = motion_round(
        fall, util=[cast(10, 0, code="Wushu", name="E_Dash")] if ability else [])
    assert mask[np.abs(t - onset) <= .25].any()
    z = rnd.track_z[0]
    assert np.ptp(z[mask]) > 1
    assert np.all(np.diff(z) <= 0)
    spans = height_motion.blackouts(blob, rnd.geo).get(0, ())
    assert bool(spans) is ability
    if ability:
        assert onset - 10 > fights.DASH_EXCLUDE_S
        assert any(a <= t[mask][-1] and b >= t[mask][0] for a, b in spans)
    flat, flat_blob, flat_onset, _, flat_mask = motion_round(lambda t, _: np.zeros_like(t))
    assert not flat_mask.any()
    normal = row(flat, flat_blob, onset=flat_onset)
    assert (normal["type"], normal["reason"]) == ("wide_swing", None)
    r = row(rnd, blob, onset=onset)
    expected = ("ability_peek", None) if ability else (normal["type"], normal["reason"])
    assert (r["type"], r["reason"]) == expected
    [(start, end, rise)] = peeks._flights(blob, rnd.geo)[0]
    assert start <= onset <= end and rise == 0


@pytest.mark.parametrize("rise,label", [(.3, "wide_swing"), (.49, "wide_swing"),
                                       (.5, "jump_peek"), (.6, "jump_peek")])
def test_jump_rise_filter_on_flight_data(monkeypatch, rise, label):
    rnd, blob, onset, _, mask = motion_round(lambda t, _: np.zeros_like(t))
    assert not mask.any()
    assert not height_motion.blackouts(blob, rnd.geo)
    assert row(rnd, blob, onset=onset)["type"] == "wide_swing"
    monkeypatch.setattr(peeks, "_flights", lambda blob, geo: {0: [(onset - .1, onset + .1, rise)]})
    r = row(rnd, blob, onset=onset)
    assert (r["opener"], r["type"], r["reason"]) == ("peeker", label, None)


def test_steady_ramp_is_normal_peek():
    rnd, blob, onset, t, mask = motion_round(lambda t, _: np.clip(t - 10, 0, 2) * 1.5)
    z = rnd.track_z[0]
    assert np.ptp(z[np.abs(t - onset) <= .5]) > .8
    assert not mask.any()
    assert not height_motion.blackouts(blob, rnd.geo)
    r = row(rnd, blob, onset=onset)
    assert (r["type"], r["reason"]) == ("wide_swing", None)


def test_only_opponent_jumps():
    rnd, blob, onset, t, mask = motion_round(slot=5)
    assert mask[np.abs(t - onset) <= .25].any()
    assert not height_motion.blackouts(blob, rnd.geo)
    r = row(rnd, blob, onset=onset)
    assert (r["type"], r["reason"]) == ("wide_swing", None)


@pytest.mark.parametrize("flying", [False, True])
def test_movement_cast_is_ability_peek(flying):
    profile = arc if flying else lambda t, _: np.zeros_like(t)
    rnd, blob, onset, t, mask = motion_round(profile, util=[cast(11.1, 0)])
    assert bool(mask[np.abs(t - onset) <= .25].any()) is flying
    assert abs(11.1 - onset) <= fights.DASH_EXCLUDE_S
    r = row(rnd, blob, onset=onset)
    assert (r["type"], r["reason"]) == ("ability_peek", None)


def test_upward_burst_without_cast_is_ability_peek():
    rnd, blob, onset, t, mask = motion_round(lambda t, onset: arc(t, onset, velocity=12))
    assert not any(e["k"] == "cast" for e in blob["util"])
    assert mask[np.abs(t - onset) <= .25].any()
    assert height_motion.blackouts(blob, rnd.geo)[0]
    r = row(rnd, blob, onset=onset)
    assert (r["type"], r["reason"]) == ("ability_peek", None)


def test_ability_span_on_flight_can_start_before_cast_window():
    rnd, blob, onset, t, mask = motion_round(util=[cast(10, 0, code="Wushu", name="E_Dash")])
    assert mask[np.abs(t - onset) <= .25].any()
    assert onset - 10 > fights.DASH_EXCLUDE_S
    assert any(a <= t[mask][-1] and b >= t[mask][0]
               for a, b in height_motion.blackouts(blob, rnd.geo)[0])
    r = row(rnd, blob, onset=onset)
    assert (r["type"], r["reason"]) == ("ability_peek", None)


def test_jump_as_enemy_opens_line_is_caught_moving():
    player = "A", [(0, 230, 280, 0), (10, 230, 280, 0), (14, 110, 280, 0)]
    enemy = "B", [(0, 330, 120, 0), (10.992, 330, 120, 0), (11, 330, 280, 0)]
    rnd, blob, onset, t, mask = motion_round(player=player, enemy=enemy)
    assert mask[np.abs(t - onset) <= .25].any()
    assert not height_motion.blackouts(blob, rnd.geo)
    r = row(rnd, blob, onset=onset)
    assert (r["opener"], r["type"], r["reason"]) == ("holder", "other", "caught_moving")


@pytest.mark.parametrize("offset", [-.62, .62])
def test_jump_outside_onset_window_is_normal_peek(offset):
    rnd, blob, onset, t, mask = motion_round(lambda t, onset: arc(t, onset, offset=offset))
    assert mask.any()
    assert np.min(np.abs(t[mask] - onset)) == pytest.approx(.3, abs=.04)
    assert not mask[np.abs(t - onset) <= .25].any()
    r = row(rnd, blob, onset=onset)
    assert (r["type"], r["reason"]) == ("wide_swing", None)


@pytest.mark.parametrize("ability", [False, True])
def test_flight_with_missing_opener_position(ability, monkeypatch):
    rnd, blob, onset, t, mask = motion_round(util=[cast(11.1, 0)] if ability else [])
    assert mask[np.abs(t - onset) <= .25].any()
    pos = rnd.pos
    monkeypatch.setattr(rnd, "pos", lambda s, t: None if s == 0 and
                        abs(t - (onset - pc.OPENER_DELTA_S)) < 1e-8 else pos(s, t))
    r = row(rnd, blob, onset=onset)
    assert r["opener"] is None
    assert (r["type"], r["reason"]) == (("ability_peek", None) if ability else ("other", "unmeasured"))


def test_flights_and_ability_spans_computed_once_per_round(monkeypatch):
    rnd, blob, onset, _, _ = motion_round(util=[damage(17, 0, 5)])
    rnd = add_height(rnd, blob, 5, arc, onset)
    calls = {"airborne": 0, "blackouts": 0}

    def counted(name, real):
        def wrapper(*args, **kwargs):
            calls[name] += 1
            return real(*args, **kwargs)
        return wrapper

    for name in calls:
        monkeypatch.setattr(height_motion, name, counted(name, getattr(height_motion, name)))
    rows = peeks.round_openings(rnd, blob)
    assert sum(r["slot"] == 0 for r in rows) == 2
    assert calls == {"airborne": 2, "blackouts": 1}


def test_caught_moving_as_enemy_opens_line():
    player = "A", [(0, 230, 280, 0), (10, 230, 280, 0), (14, 110, 280, 0)]
    enemy = "B", [(0, 330, 120, 0), (11.992, 330, 120, 0), (12, 330, 280, 0)]
    rnd, blob = round_of(player, enemy, util=[damage(12.25, 5, 0)])
    corner_setup(rnd)
    r = row(rnd, blob)
    assert (r["opener"], r["type"], r["reason"]) == ("holder", "other", "caught_moving")


def test_no_line_keeps_opponent_and_distance():
    rnd, blob = round_of(standing(), util=[damage(12.25, 0, 5, wall=True)])
    assert peeks.line_clear(rnd, 0, 5, 12.25) is False
    r = row(rnd, blob, onset=None)
    assert (r["type"], r["reason"], r["t_first_sight"]) == ("other", "no_line", None)
    assert r["distance_m"] == round(math.hypot(100, 80) * M_PER_PX, 1)


def test_no_line_keeps_an_earlier_first_sight():
    rnd, blob = round_of(steps((10, 11)), util=[damage(12.25, 0, 5, wall=True)])
    corner_setup(rnd, 9.75, 10.05)
    assert peeks.line_clear(rnd, 0, 5, 12.25) is False
    r = row(rnd, blob, onset=None)
    assert (r["type"], r["reason"]) == ("other", "no_line")
    assert r["t_first_sight"] == pytest.approx(10, abs=.07)


def test_first_gun_event_skips_enemy_ability_damage():
    rnd, blob = round_of(util=[damage(11.25, 5, 0, src="ability"), damage(12.25, 0, 5)])
    corner_setup(rnd)
    group = fights._split(fights._events(rnd, blob, 0))[0]
    assert peeks.first_gun_event(rnd, blob, 0, group) == (12.25, 5)
    r = row(rnd, blob)
    assert r["t_event"] == 12.25


def test_first_gun_event_matches_incoming_source_and_target():
    rnd, blob = round_of(util=[damage(11.25, 5, 0, src="ability"),
                               damage(12.25, 0, 5, src="ability"), damage(12.25, 6, 0)],
                         extra={6: standing(330, 280, "B")})
    assert peeks.line_clear(rnd, 0, 6, 11.75) is False
    assert peeks.line_clear(rnd, 0, 6, 12.05) is True
    r = row(rnd, blob, opponent=6)
    assert r["t_event"] == 12.25


def test_kill_is_a_gun_event_even_without_damage():
    rnd, blob = round_of(kills=[kill(12.25, 5, 0)])
    corner_setup(rnd)
    r = row(rnd, blob)
    assert (r["t_event"], r["result"]) == (12.25, "lost")


def test_no_gun_schema():
    rnd, blob = round_of(util=[damage(12.25, 5, 0, src="ability")])
    assert peeks.first_gun_event(rnd, blob, 0, fights._events(rnd, blob, 0)) == (None, None)
    r = row(rnd, blob, onset=None, opponent=None)
    assert (r["type"], r["reason"]) == ("other", "no_gun")
    assert r["t_event"] is None


@pytest.mark.parametrize("reopen", [False, True])
def test_forward_scan_kill_must_belong_to_main_exposure(reopen):
    # In the uninterrupted case the player is returning but remains visible at the kill.
    player = "A", [(0, 230, 200, 0), (11.992, 230, 200, 0), (12, 230, 280, 0),
                   (12.16, 230, 280, 0), (12.6, 230, 200, 0)]
    if reopen:
        player = steps((12, 12.125), (12.1875, None))
        player[1].extend([(12.2, 230, 280, 0), (12.5, 230, 250, 0)])
    rnd, blob = round_of(player, util=[damage(12.03125, 0, 5)], kills=[kill(12.3125, 0, 5)])
    corner_setup(rnd, 11.75, 12.05)
    r = row(rnd, blob)
    assert (r["type"] == "jiggle") is (not reopen)
    assert r["result"] == "won"


def test_competing_main_exposures_prefers_the_one_holding_event():
    rnd, blob = round_of(steps((11, 12.184), (12.216, None), near=True), util=[damage(12.25, 0, 5)])
    assert peeks.line_clear(rnd, 0, 5, 12.1875) is False
    assert peeks.line_clear(rnd, 0, 5, 12.25) is True
    r = row(rnd, blob, onset=12.21875)
    assert (r["type"], r["reason"], r["exposure_s"]) == ("other", "unclear", None)


def test_event_just_after_line_closes_uses_latest_slack_exposure():
    rnd, blob = round_of(steps((11, 12.1875)), util=[damage(12.25, 0, 5)])
    corner_setup(rnd, 10.75, 11.05)
    assert peeks.line_clear(rnd, 0, 5, 12.25) is False
    r = row(rnd, blob, onset=11)
    assert r["reason"] != "no_line"


def test_repeek_filter_uses_older_near_candidate_over_newer_far_one():
    # Both closes follow long exposures; the newer close is farther into cover.
    far_y = 245 - 3 / M_PER_PX
    player = "A", steps((6, 7), near=True)[1] + [(7.5, 230, 200, 0), (7.992, 230, 200, 0),
        (8, 230, 280, 0), (8.992, 230, 280, 0), (9, 230, far_y, 0),
        (10.992, 230, far_y, 0), (11, 230, 245, 0)]
    rnd, blob = round_of(player, util=[damage(11.25, 0, 5)])
    corner_setup(rnd, 10.75, 11.05)
    # Stored integer uv coordinates quantize the requested three-metre offset.
    assert math.dist(rnd.pos(0, 9)[:2], rnd.pos(0, 11)[:2]) * M_PER_PX == pytest.approx(3, abs=.02)
    r = row(rnd, blob, onset=11)
    assert r["type"] == "re_peek"
    assert r["repeek_gap_s"] == pytest.approx(4, abs=.04)
    assert r["repeek_spot_m"] <= 2


def test_jiggle_velocity_is_measured_at_kill_not_previous_onset_step():
    # Reverse during the last 1/32 s. Trailing velocity changes sign only at the kill.
    player = "A", [(0, 230, 200, 0), (11.992, 230, 200, 0), (12, 230, 280, 0),
        (12.16, 230, 280, 0), (12.288, 230, 286, 0), (12.312, 230, 274, 0)]
    rnd, blob = round_of(player, util=[damage(12.03125, 0, 5)], kills=[kill(12.3125, 0, 5)])
    corner_setup(rnd)
    assert peeks.windowed_velocity(rnd, 0, 12.3125 - pc.ONSET_STEP_S)[1] > 0
    assert peeks.windowed_velocity(rnd, 0, 12.3125)[1] < 0
    r = row(rnd, blob)
    assert r["type"] == "jiggle"


def test_unmeasured_segment_break_at_onset():
    rnd, blob = round_of(util=[damage(12.25, 0, 5)])
    points = steps((12, None))[1]
    blob["tracks"]["0"] = toys.track(points, 0, 11.992, hz=125) + toys.track(points, 12, 24, hz=125)
    rnd = engine.RoundInputs(blob, rnd.geo, engine.ControlLink(db_deaths=()))
    corner_setup(rnd)
    r = row(rnd, blob)
    assert (r["type"], r["reason"], r["speed_mps"]) == ("other", "unmeasured", None)
    assert r["opener"] == "peeker"


@pytest.mark.parametrize("missing", ["held_speed", "opener_position", "sideways_position"])
def test_unmeasured_required_check_keeps_available_measurements(monkeypatch, missing):
    if missing == "held_speed":
        enemy = "B", [(0, 330, 120, 0), (11.992, 330, 120, 0), (12, 330, 280, 0)]
        rnd, blob = round_of(standing(230, 280), enemy, util=[damage(12.25, 0, 5)])
        corner_setup(rnd)
        monkeypatch.setattr(peeks, "max_speed", lambda *args: None)
        onset = 12
    else:
        event = 11.28125
        rnd, blob = round_of(swing(), util=[damage(event, 0, 5)])
        corner_setup(rnd, 10, event)
        onset = peeks.exposures(rnd, 0, 5, event - 10, event + .1)[0]["on"]
        pos = rnd.pos
        absent = onset - pc.OPENER_DELTA_S if missing == "opener_position" else onset + pc.WIDE_LOOK_S
        monkeypatch.setattr(rnd, "pos", lambda s, t: None if s == 0 and abs(t - absent) < 1e-8 else pos(s, t))
    r = row(rnd, blob, onset=onset)
    assert (r["type"], r["reason"]) == ("other", "unmeasured")
    assert r["speed_mps"] is not None
    assert r["opener"] == (None if missing == "opener_position" else "holder" if missing == "held_speed" else "peeker")


@pytest.mark.parametrize("moving,label", [(False, "held"), (True, "other")])
def test_neither_move_opens_line_after_smoke(moving, label):
    player = ("A", [(0, 120, 200, 0), (10, 120, 200, 0), (14, 200, 200, 0)]) if moving else standing(120, 200)
    smoke = toys.toy_ability("Wraith", "4_Smoke", 260, 200, 5, t=0, t1=12)
    rnd, blob = round_of(player, standing(400, 200, "B"), geo=toys.open_hall(),
                         util=[smoke, damage(12.25, 0, 5)])
    corner_setup(rnd)
    r = row(rnd, blob)
    assert r["type"] == label
    assert r["reason"] == ("unclear" if moving else None)


def test_still_peek_small_step_opens_corner():
    player = "A", [(0, 230, 239, 0), (11.75, 230, 239, 0), (12, 230, 240.43, 0)]
    rnd, blob = round_of(player, util=[damage(12.25, 0, 5)])
    corner_setup(rnd, 11.75, 12.05)
    onset = peeks.exposures(rnd, 0, 5, 2.25, 12.35)[0]["on"]
    r = row(rnd, blob, onset=onset)
    assert r["speed_mps"] <= pc.HELD_MAX_MPS
    assert (r["type"], r["reason"]) == ("other", "still_peek")


def test_no_onset_keeps_first_sight_and_opponent():
    rnd, blob = round_of(standing(230, 280), util=[damage(12.25, 0, 5)])
    assert peeks.line_clear(rnd, 0, 5, 2.25) is True
    r = row(rnd, blob, onset=None)
    assert (r["type"], r["reason"]) == ("other", "no_onset")
    assert r["t_first_sight"] is not None


def test_third_party_kill_does_not_decide_duel():
    rnd, blob = round_of(util=[damage(12.25, 0, 5)], kills=[kill(12.5, 1, 5)], extra={1: standing(220, 280)})
    corner_setup(rnd)
    r = row(rnd, blob)
    assert r["result"] == "unresolved"


@pytest.mark.parametrize("distance,band", [(9.9, "close"), (10, "mid"), (24.9, "mid"), (25, "long")])
def test_distance_band_boundaries(distance, band):
    assert peeks.distance_band(distance) == band


def test_gun_class_comes_from_players_shots():
    rnd, blob = round_of(util=[shot(12.25, 0, "Vandal"), shot(12.25, 5, "Sheriff"), damage(12.25, 0, 5)])
    corner_setup(rnd)
    r = row(rnd, blob)
    assert r["gun_class"] == "rifle"


def test_round_openings_order_one_per_slot_and_fight():
    rnd, blob = round_of(steps((7, 8.5), (12, None)), util=[damage(7.25, 0, 5), damage(12.25, 0, 5)])
    rnd.team = dict(reversed(list(rnd.team.items())))
    rows = peeks.round_openings(rnd, blob)
    assert [(r["slot"], r["i"]) for r in rows] == [(0, 0), (0, 1), (5, 0), (5, 1)]
    assert all(sorted(r) == KEYS for r in rows)
