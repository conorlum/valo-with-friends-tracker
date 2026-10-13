from app.coach import abilities
from app.coach.roundview import RoundView
from tests.coach import toys


def test_an_ability_gets_the_name_the_viewer_draws_it_under():
    assert abilities.display("Gumshoe", "4_TripWire") == "Trapwire"
    assert abilities.display("Hunter", "Q_SonarPing") == "Recon Bolt"
    assert len(abilities._viewer_rows()) > 50              # the viewer's table was found and read


def test_objects_the_viewer_does_not_draw_are_named_here():
    assert abilities.display("Gumshoe", "E_CameraTrackingDart") == "Spycam dart"


def test_an_unknown_ability_has_no_name_and_keeps_its_code_name():
    assert abilities.display("Nobody", "X_Made_Up") is None and abilities.display(None, "x") is None
    util = [{"k": "ability", "t": 5.0, "by": 0, "code": "Nobody", "name": "X_Made_Up"}]
    view = RoundView(toys.round_blob(toys.ten(), util=util), toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS)
    assert view.utility()[0]["ability"] == "X_Made_Up"


def test_the_objects_of_one_cast_are_one_row():
    util = [{"k": "ability", "t": 5.0, "by": 0, "code": "Gumshoe", "name": "4_TripWire"},
            {"k": "ability", "t": 5.0, "by": 0, "code": "Gumshoe", "name": "4_TripWire_SecondWire"},
            {"k": "ability", "t": 30.0, "by": 0, "code": "Gumshoe", "name": "4_TripWire"},
            {"k": "ability", "t": 30.2, "by": 1, "code": "Gumshoe", "name": "4_TripWire"}]
    view = RoundView(toys.round_blob(toys.ten(), util=util), toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS)
    assert [(row["t"], row["by"], row["ability"]) for row in view.utility()] == [
        (5.0, "P0", "Trapwire"), (30.0, "P0", "Trapwire"), (30.2, "P1", "Trapwire")]
