"""Moving shots, dash exclusion and gun class (W6a), through the real engine on toy rounds."""

from app.moments import fights
from tests.moments.helpers import cast, damage, round_of, shot
from tests.replays.control_toys import open_hall

GEO = open_hall()


def px(m):
    return m / GEO.m_per_px


def players(mps, still_from=9.0, still_to=13.0):
    """Slot 0 walks east at `mps` between still_from and still_to, standing otherwise. B: slot 5 far east."""
    x1 = 120 + px(mps * (still_to - still_from))
    return {0: ("A", [(0.0, 120, 200, 0), (still_from, 120, 200, 0), (still_to, x1, 200, 0)]),
            5: ("B", [(0.0, 400, 160, 180)])}


def run(mps, shots, extra_util=(), casts_seen=True, **kw):
    util = [damage(10, 0, 5), damage(11, 0, 5), *shots, *extra_util]   # the fight spans 10 to 11 at least
    rnd, data = round_of(players(mps, **kw), util=util)
    if casts_seen:
        data["movement_casts"] = 1
    return fights.round_moments(rnd, data)["fights"][0]


def test_exactly_threshold_is_not_above_and_just_over_is(monkeypatch):
    rnd, data = round_of(players(3.0), util=[damage(10, 0, 5), damage(11, 0, 5), shot(10.0, 0), shot(10.5, 0)])
    v = fights._speed(rnd, 0, 10.0)
    assert abs(v - 3.0) < 0.05
    monkeypatch.setattr(fights, "MOVING_MPS", v)
    assert fights.round_moments(rnd, data)["fights"][0]["moving"]["above"] == 0
    monkeypatch.setattr(fights, "MOVING_MPS", v - 1e-9)
    assert fights.round_moments(rnd, data)["fights"][0]["moving"]["above"] == 2


def test_speeds_around_threshold():
    assert run(2.4, [shot(10.0, 0)])["moving"]["above"] == 0
    assert run(2.6, [shot(10.0, 0)])["moving"]["above"] == 1


def test_dash_exclusion_window():
    f = run(4.0, [shot(10.7, 0)], [cast(10.0, 0)])
    assert f["moving"]["measured"] == 0 and f["moving"]["first_mps"] is None
    f = run(4.0, [shot(10.8, 0)], [cast(10.0, 0)])
    assert f["moving"]["measured"] == 1


def test_other_slots_cast_does_not_exclude():
    assert run(4.0, [shot(10.2, 0)], [cast(10.0, 5)])["moving"]["measured"] == 1


def test_no_movement_casts_key_ignores_cast_rows():
    f = run(4.0, [shot(10.2, 0)], [cast(10.0, 0)], casts_seen=False)
    assert f["moving"]["measured"] == 1


def test_shotgun_fast_shots_not_tagged_but_shown():
    f = run(4.0, [shot(10.0, 0, "Bucky"), shot(10.3, 0, "Bucky")])
    assert f["moving"]["above"] == 2 and f["moving"]["gun_class"] == "shotgun"
    assert f["moving"]["tagged"] is False and "moving" not in f["tags"]
    assert f["moving"]["first_mps"] is not None


def test_one_of_three_not_tagged_two_of_three_tagged():
    # standing until 10.9, then walking at 4 m/s: shots at 10.0 and 10.3 stand, 11.5 moves
    late = [damage(12, 0, 5)]
    one = run(4.0, [shot(10.0, 0), shot(10.3, 0), shot(11.5, 0)], late, still_from=11.0, still_to=13.0)
    assert one["moving"]["measured"] == 3 and one["moving"]["above"] == 1
    assert one["moving"]["tagged"] is False and "moving" not in one["tags"]
    two = run(4.0, [shot(10.0, 0), shot(11.5, 0), shot(11.7, 0)], late, still_from=11.0, still_to=13.0)
    assert two["moving"]["above"] == 2 and two["moving"]["tagged"] is True
    assert "moving" in two["tags"] and two["moving"]["gun_class"] == "rifle"
    assert two["moving"]["first_mps"] == 0.0


def test_shot_before_lead_window_not_counted():
    f = run(4.0, [shot(8.9, 0), shot(10.0, 0)])   # t0 is 10: 8.9 is 1.1 s early
    assert f["moving"]["measured"] == 1
    assert run(4.0, [shot(9.0, 0), shot(10.0, 0)])["moving"]["measured"] == 2


def test_other_players_shots_ignored():
    f = run(4.0, [shot(10.0, 5), shot(10.0, 0)])
    assert f["moving"]["measured"] == 1


def test_sample_span_over_limit_gives_no_speed(monkeypatch):
    rnd, data = round_of(players(4.0), util=[damage(10, 0, 5), shot(10.0, 0)])
    assert fights._speed(rnd, 0, 10.0) is not None
    monkeypatch.setattr(fights, "SPEED_MAX_SPAN_S", 0.1)    # the 16 Hz neighbours span 0.125 s
    f = fights.round_moments(rnd, data)["fights"][0]
    assert f["moving"]["measured"] == 0 and f["moving"]["gun_class"] == "rifle"


def test_no_shots_gives_no_gun_class():
    f = run(4.0, [])
    assert f["moving"] == {"measured": 0, "above": 0, "first_mps": None, "tagged": False, "gun_class": None}


def test_gun_class_majority_and_tie_goes_to_earliest():
    f = run(0.0, [shot(10.0, 0, "Classic"), shot(10.1, 0, "Vandal"), shot(10.2, 0, "Vandal")])
    assert f["moving"]["gun_class"] == "rifle"
    f = run(0.0, [shot(10.0, 0, "Classic"), shot(10.1, 0, "Vandal")])
    assert f["moving"]["gun_class"] == "pistol"
    assert run(0.0, [shot(10.0, 0, "Mystery")])["moving"]["gun_class"] == "other"
