"""Aim events (crosshair placement): one event per fight and opponent, through the real engine on toy rounds.

Slot 0 (A) stands at x 120 and slot 5 (B) at x 400 on one row, so the bearing from 0 to 5 is 0 degrees and the
error is the player's yaw itself. A smoke between them, up until a time, opens the line then; the fight's first
damage comes later, so the event is the smoke's end and not `in_view_at_start` unless that is under test."""

import pytest

from app.moments import aim, fights
from app.moments.constants import AIM_LADDER_DEG
from app.replays import format as fmt
from tests.moments.helpers import damage, door_hall, flash, kill, open_hall, round_of, shot, status, toy_ability

OPEN = open_hall()
KEYS = {"slot", "i", "opponent", "t", "cls", "err", "err_125", "err_250", "d_m", "fine", "hold", "opener",
        "gun_class", "hp", "enemy_hp", "outcome", "t_end", "on", "shot", "dmg"}
SMOKE_TO_2 = toy_ability("Wraith", "4_Smoke", 260, 200, 5, t=0.0, t1=2.0)
WON = [damage(3.5, 0, 5), kill(4.0, 0, 5)]


def players(yaw=30, extra=None, mine=None):
    out = {0: ("A", mine or [(0.0, 120, 200, yaw)]), 5: ("B", [(0.0, 400, 200, 180)])}
    out.update(extra or {})
    return out


def events_of(pl, util=(), kills=(kill(4.0, 0, 5),), hz=None, t_end=8.0, mutate=None,
              geo=OPEN):
    util = list(util) + [damage(3.5, 0, 5)] if not any(u["k"] == "damage" for u in util) else list(util)
    rnd, data = round_of(pl, kills=list(kills), util=util, t_end=t_end, geo=geo, hz=hz)
    if mutate:
        mutate(data)
    fl = fights.round_moments(rnd, data, sight=False)["fights"]
    return [e for e in aim.round_aim(rnd, data, fl) if e["slot"] == 0], rnd, data, fl


def one(*a, **k):
    out, *_ = events_of(*a, **k)
    assert len(out) == 1
    return out[0]


def test_event_has_the_contract_keys_and_names_its_fight():
    ev, rnd, data, fl = events_of(players(), [SMOKE_TO_2])
    [e] = ev
    assert set(e) == KEYS
    assert (e["slot"], e["i"], e["opponent"]) == (0, 0, 5)
    assert e["t"] == pytest.approx(2.0, abs=0.002)
    assert e["opener"] in ("peeker", "holder", "both", "unclear") and isinstance(e["hold"], bool)
    assert e["d_m"] is not None and len(e["on"]) == len(AIM_LADDER_DEG)


def test_enemy_appearing_30_degrees_to_the_side_is_ok_with_error_30():
    e = one(players(30), [SMOKE_TO_2])
    assert e["err"] == pytest.approx(30, abs=0.5)
    assert e["cls"] == "ok" and e["fine"] is False


def _corner(hz):
    """Slot 5 walks south along the east face of the door hall's wall until the line from slot 0 (west of it, by
    the door) clears past the wall's end: a clearing that falls between 1/16 s steps."""
    pl = {0: ("A", [(0.0, 150, 280, 0)]), 5: ("B", [(0.0, 232, 250, 180), (4.0, 232, 295, 180)])}
    util = [damage(4.5, 0, 5)]
    ev, rnd, data, fl = events_of(pl, util, kills=[kill(5.0, 0, 5)], hz=hz, geo=door_hall())
    [e] = ev
    return e, rnd


def _truth(rnd, t_lo, t_hi):
    """The first clear time on a 1/1024 s scan."""
    from app.moments import peeks
    t = t_lo
    while t < t_hi:
        if peeks.line_clear(rnd, 0, 5, t):
            return t
        t += 1 / 1024
    return None


def test_bisection_finds_the_clearing_to_the_native_step():
    e, rnd = _corner(128)
    truth = _truth(rnd, 1.5, 4.5)
    assert truth is not None and e["cls"] != "in_view_at_start"
    assert truth - 0.001 <= e["t"] <= truth + 1 / 128 + 0.001


def test_at_16_hz_the_event_is_the_first_clear_sample():
    e, rnd = _corner(16)
    truth = _truth(rnd, 1.5, 4.5)
    assert truth - 0.001 <= e["t"] <= truth + 1 / 16 + 0.001
    assert (e["t"] - 1.5) * 16 == pytest.approx(round((e["t"] - 1.5) * 16), abs=0.02)


def test_facing_away_is_behind():
    e = one(players(150), [SMOKE_TO_2])
    assert e["err"] == pytest.approx(150, abs=0.5) and e["cls"] == "behind"


def test_a_turn_of_60_degrees_in_the_quarter_second_before_is_turning():
    e = one(players(mine=[(0.0, 120, 200, 60), (1.9, 120, 200, 0)]), [SMOKE_TO_2])
    assert e["cls"] == "turning" and e["err"] == pytest.approx(0, abs=0.5)


def test_a_turn_of_30_degrees_is_ok():
    e = one(players(mine=[(0.0, 120, 200, 30), (1.9, 120, 200, 0)]), [SMOKE_TO_2])
    assert e["cls"] == "ok"


MATE = {1: ("A", [(0.0, 130, 250, 0)])}


def test_an_enemy_flash_on_the_viewer_at_the_event_is_impaired():
    e = one(players(extra=MATE), [SMOKE_TO_2, flash(1.7, 5, [[0, 1.8, 0.5]])])
    assert e["cls"] == "impaired"


def test_a_flash_that_ended_before_the_event_is_not_impaired():
    e = one(players(extra=MATE), [SMOKE_TO_2, flash(1.0, 5, [[0, 1.1, 0.5]])])
    assert e["cls"] == "ok"


def test_a_teammates_or_own_flash_is_not_impaired():
    e = one(players(extra=MATE), [SMOKE_TO_2, flash(1.7, 1, [[0, 1.8, 0.5]])])
    assert e["cls"] == "ok"
    e = one(players(extra=MATE), [SMOKE_TO_2, flash(1.7, 0, [[0, 1.8, 0.5]])])
    assert e["cls"] == "ok"


def test_an_enemy_status_on_the_viewer_is_impaired():
    e = one(players(extra=MATE), [SMOKE_TO_2, status(1.5, 3.0, 5, 0)])
    assert e["cls"] == "impaired"


def test_a_status_from_a_teammate_is_not_impaired():
    e = one(players(extra=MATE), [SMOKE_TO_2, status(1.5, 3.0, 1, 0)])
    assert e["cls"] == "ok"


def test_a_line_clear_at_the_first_sample_is_in_view_at_start():
    e = one(players(30), [])
    assert e["cls"] == "in_view_at_start" and e["t"] == pytest.approx(0.5, abs=0.001)


def test_threshold_times_follow_the_yaw_track():
    mine = [(0.0, 120, 200, 30), (2.2, 120, 200, 4), (2.4, 120, 200, 1)]
    e = one(players(mine=mine), [SMOKE_TO_2], hz=128, t_end=6.0)
    on = dict(zip(AIM_LADDER_DEG, e["on"]))
    assert on[5] == pytest.approx(0.2, abs=0.02)
    assert on[4] == pytest.approx(0.2, abs=0.02)
    assert on[1] == pytest.approx(0.4, abs=0.02)
    assert on[0.5] is None
    assert on[20] == pytest.approx(0.2, abs=0.02)


def test_a_player_dying_before_reaching_a_threshold_censors_it():
    mine = [(0.0, 120, 200, 30), (2.1, 120, 200, 5)]
    util = [SMOKE_TO_2, damage(2.2, 5, 0)]
    e = one(players(mine=mine, extra=MATE), util, kills=[kill(2.3, 5, 0)])
    on = dict(zip(AIM_LADDER_DEG, e["on"]))
    assert on[2] is None and on[5] == pytest.approx(0.1, abs=0.07)
    assert e["t_end"] == pytest.approx(2.3, abs=0.01)
    assert e["outcome"] == "lost"


def test_the_probe_past_the_window_is_null_not_the_last_sample():
    util = [SMOKE_TO_2, damage(2.1, 5, 0)]
    e = one(players(), util, kills=[kill(2.2, 5, 0)])
    assert e["err_125"] is not None and e["err_250"] is None


def test_shot_and_damage_times_are_after_the_event_and_by_the_player():
    util = [SMOKE_TO_2, shot(1.5, 0), shot(2.5, 0), shot(2.2, 0), shot(2.1, 5), damage(2.6, 0, 5),
            damage(2.5, 0, 5, src="ability"), damage(2.5, 5, 0)]
    e = one(players(extra=MATE), util, kills=[kill(3.0, 0, 5)])
    assert e["shot"] == pytest.approx(0.2, abs=0.01)
    assert e["dmg"] == pytest.approx(0.6, abs=0.01)


def test_no_shot_after_the_event_is_null():
    e = one(players(), [SMOKE_TO_2, shot(1.0, 0)])
    assert e["shot"] is None


def test_outcomes():
    assert one(players(), [SMOKE_TO_2], kills=[kill(4.0, 0, 5)])["outcome"] == "won"
    assert one(players(), [SMOKE_TO_2, damage(3.5, 5, 0)], kills=[kill(4.0, 5, 0)])["outcome"] == "lost"
    assert one(players(), [SMOKE_TO_2, damage(3.5, 0, 5)], kills=[])["outcome"] == "none"


def test_hp_adds_shield_and_comes_from_the_slots_life():
    def put(data):
        data["player_state"] = {"vitals": {
            "0": [{"t": 0.0, "life": 0, "hp": 100, "sh": 25, "mhp": 100, "msh": 50}],
            "5": [{"t": 0.0, "life": 0, "hp": 60, "sh": None, "mhp": 100, "msh": None}]}}
    e = one(players(), [SMOKE_TO_2], mutate=put)
    assert (e["hp"], e["enemy_hp"]) == (125, 60)
    assert one(players(), [SMOKE_TO_2])["hp"] is None


def test_gun_class_is_the_fights():
    ev, rnd, data, fl = events_of(players(), [SMOKE_TO_2, shot(3.0, 0, gun="Vandal")])
    [f] = [f for f in fl if f["slot"] == 0]
    assert ev[0]["gun_class"] == f["moving"]["gun_class"]


def _with_yq(deg, n_off=0):
    def put(data):
        seg = data["tracks"]["0"][0]
        unit = round(deg * fmt.YAW_UNITS / 360)
        seg["yq"] = fmt.encode_yaw_units([unit] * (len(seg["u"]) - n_off))
    return put


def test_a_blob_with_the_fine_channel_uses_it():
    e = one(players(30), [SMOKE_TO_2], mutate=_with_yq(30.25))
    assert e["fine"] is True and e["err"] == pytest.approx(30.25, abs=0.02)
    assert e["err"] != round(e["err"])


def test_a_wrong_length_yq_falls_back_to_whole_degrees():
    e = one(players(30), [SMOKE_TO_2], mutate=_with_yq(30.25, n_off=1))
    assert e["fine"] is False and e["err"] == pytest.approx(30, abs=0.01)


def test_fine_yaw_leaves_out_a_slot_with_a_bad_segment():
    _, rnd, data, _ = events_of(players(30), [SMOKE_TO_2], mutate=_with_yq(30.25, n_off=1))
    assert 0 not in aim.fine_yaw(data)
    _with_yq(30.25)(data)
    times, deg = aim.fine_yaw(data)[0]
    assert len(times) == len(deg) == len(rnd.tracks[0][0])
    assert deg[0] == pytest.approx(30.25, abs=0.02)
