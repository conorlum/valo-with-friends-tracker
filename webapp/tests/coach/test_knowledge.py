import pytest

from app.coach import knowledge
from app.coach.roundview import RoundView
from tests.coach import toys


def view(blob):
    return RoundView(blob, toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS)


KNEW = {"A": {"5": [[5.0, 8.0, 8000, 8000], [20.0, 40.0, 5000, 5000]], "6": [[30.0, 31.0, 8000, 8000]]},
        "B": {}}


def test_nothing_is_known_before_the_first_sighting():
    assert knowledge.known_enemies(KNEW, "A", 4.0, view(toys.round_blob(toys.ten()))) == []


def test_a_past_sighting_keeps_its_last_place_and_time():
    (e,) = knowledge.known_enemies(KNEW, "A", 15.0, view(toys.round_blob(toys.ten())))
    assert (e["slot"], e["name"], e["first_seen"], e["last_seen"], e["in_view"]) == (5, "P5", 5.0, 8.0, False)
    assert e["area"]["name"] == "B Site" and e["alive"] is True


def test_an_enemy_in_view_is_placed_where_they_are_now():
    v = view(toys.round_blob(toys.ten()))          # slot 5 really stands on B Site the whole round
    (e,) = knowledge.known_enemies(KNEW, "A", 25.0, v)
    assert e["in_view"] is True and e["last_seen"] == 25.0 and e["area"]["name"] == "B Site"


def test_a_dead_enemy_is_marked_dead():
    v = view(toys.round_blob(toys.ten(), kills=[{"t": 10.0, "killer": 0, "victim": 5}]))
    (e,) = knowledge.known_enemies(KNEW, "A", 15.0, v)
    assert e["alive"] is False


def test_the_other_side_knows_only_its_own_sightings():
    assert knowledge.known_enemies(KNEW, "B", 50.0, view(toys.round_blob(toys.ten()))) == []


EVENTS = {"A": [[3.0, 6, "footsteps"], [12.5, 7, "gunfire"], [40.0, 6, "seen"]], "B": []}


def test_signals_are_the_sides_locating_events_so_far():
    assert knowledge.signals(EVENTS, "A", 20.0, toys.NAMES) == [{"t": 3.0, "name": "P6", "reason": "footsteps"},
                                                                {"t": 12.5, "name": "P7", "reason": "gunfire"}]
    assert knowledge.signals(EVENTS, "B", 20.0, toys.NAMES) == []


def test_a_signal_places_the_enemy_only_roughly_from_the_nearest_teammate():
    # slot 6 stands at (8000, 8000); teammate 1 is 3000 uv straight above (42.9 m), the rest 121 m away
    v = view(toys.round_blob({**toys.ten(), 1: (8000, 5000)}))
    first = knowledge.signals(EVENTS, "A", 20.0, toys.NAMES, v)[0]
    assert first["rough_place"] == {"from": "P1", "direction": "down", "metres_min": 40.0, "metres_max": 50.0}
    assert not {"u", "v", "area"} & set(first) and not {"u", "v", "area"} & set(first["rough_place"])


@pytest.mark.parametrize("mate, direction", [((5000, 8000), "right"), ((9500, 8000), "left"), ((8000, 9500), "up"),
                                             ((7000, 6500), "down"), ((6500, 7000), "right")])
def test_the_direction_is_one_of_four_minimap_quarters(mate, direction):
    v = view(toys.round_blob({**toys.ten(), 1: mate}))
    assert knowledge.signals(EVENTS, "A", 5.0, toys.NAMES, v)[0]["rough_place"]["direction"] == direction


def test_the_distance_band_is_fixed_so_it_does_not_give_the_distance_away():
    for u in (7300, 7000, 6610):                    # 10.0 m, 14.3 m and 19.9 m to the left of the enemy
        v = view(toys.round_blob({**toys.ten(), 1: (u, 8000)}))
        rough = knowledge.signals(EVENTS, "A", 5.0, toys.NAMES, v)[0]["rough_place"]
        assert (rough["metres_min"], rough["metres_max"]) == (10.0, 20.0)


def test_a_signal_with_no_living_teammate_to_place_it_from_has_no_rough_place():
    kills = [{"t": 1.0, "killer": 5, "victim": s} for s in range(5)]
    v = view(toys.round_blob(toys.ten(), kills=kills))
    assert knowledge.signals(EVENTS, "A", 5.0, toys.NAMES, v) == [{"t": 3.0, "name": "P6", "reason": "footsteps"}]


def test_a_round_that_stores_no_events_has_no_signals_answer():
    assert knowledge.signals(None, "A", 20.0, toys.NAMES) is None
