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


def test_a_round_that_stores_no_events_has_no_signals_answer():
    assert knowledge.signals(None, "A", 20.0, toys.NAMES) is None
