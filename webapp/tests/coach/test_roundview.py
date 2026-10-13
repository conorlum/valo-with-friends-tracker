import pytest

from app.coach.roundview import RoundView
from tests.coach import toys


def view(blob):
    return RoundView(blob, toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS)


def test_sides_teammates_and_enemies():
    v = view(toys.round_blob(toys.ten()))
    assert v.side(0) == "A" and v.side(7) == "B"
    assert v.teammates(0) == [1, 2, 3, 4] and v.enemies(0) == [5, 6, 7, 8, 9]


def test_position_of_a_living_player_carries_its_area():
    v = view(toys.round_blob(toys.ten()))
    assert v.position(0, 10.0) == {"alive": True, "u": 2000, "v": 2000, "yaw": 0,
                                   "area": {"name": "A Site", "super": "A"}}


def test_position_follows_a_moving_track():
    pos = toys.ten()
    blob = toys.round_blob(pos, tracks={0: toys.walk(2000, 2000, 8000, 8000, 0.0, 60.0)})
    assert view(blob).position(0, 30.0)["area"]["name"] == "Mid Courtyard"


def test_a_dead_player_reads_as_dead_with_where_they_died():
    v = view(toys.round_blob(toys.ten(), kills=[{"t": 20.0, "killer": 5, "victim": 0}]))
    assert v.alive(0, 19.9) and not v.alive(0, 20.0)
    assert v.position(0, 30.0) == {"alive": False, "died_at": 20.0, "u": 2000, "v": 2000,
                                   "area": {"name": "A Site", "super": "A"}}
    assert v.living("A", 30.0) == [1, 2, 3, 4]


def test_distance_is_in_metres_and_needs_both_alive():
    pos = {**toys.ten(), 0: (2000, 2000), 1: (2700, 2000)}
    v = view(toys.round_blob(pos, kills=[{"t": 20.0, "killer": 5, "victim": 1}]))
    assert v.distance_m(0, 1, 10.0) == pytest.approx(10.0)
    assert v.distance_m(0, 1, 25.0) is None


def test_a_map_without_callouts_gives_no_area():
    v = RoundView(toys.round_blob(toys.ten()), toys.NAMES, toys.UV_PER_UNIT, "Nowhere", toys.CALLOUTS)
    assert v.position(0, 10.0)["area"] is None


def test_a_survivor_is_alive_to_the_end_of_the_round():
    blob = toys.round_blob(toys.ten())
    assert blob["alive"]["1"] == [[0.0, None, "round_end"]]       # the stored shape: no end time
    v = view(blob)
    assert v.alive(1, 59.9) and v.position(1, 60.0)["alive"] and len(v.living("A", 60.0)) == 5


def test_a_death_only_the_site_has_ends_the_life():
    v = RoundView(toys.round_blob(toys.ten()), toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS,
                  db_deaths=[[2, 30.0]])
    assert v.alive(2, 29.9) and not v.alive(2, 30.0)
    assert v.position(2, 40.0)["died_at"] == 30.0 and v.living("A", 40.0) == [0, 1, 3, 4]


def test_sides_come_from_the_link_when_the_blob_has_none():
    blob = toys.round_blob(toys.ten())
    for p in blob["players"]:
        p["side"] = None
    groups = {s: "A" if s < 5 else "B" for s in range(10)}
    v = RoundView(blob, toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS, groups=groups)
    assert v.enemies(0) == [5, 6, 7, 8, 9]
    with pytest.raises(ValueError, match="no side"):
        RoundView(blob, toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS)


def test_utility_and_damage_rows_carry_names():
    util = [{"k": "flash", "t": 9.0, "by": 0, "u": 0, "v": 0, "hits": [[5, 9.5, 1.5]],
             "activation": {"state": "completed", "targets_complete": True}},
            {"k": "ability", "t": 3.0, "by": 1, "code": "Wraith"},
            {"k": "damage", "t": 13.0, "t1": 13.2, "by": 5, "target": 0, "src": "gun", "wall": False, "n": 3},
            {"k": "shot", "t": 13.0, "by": 5}]
    v = view(toys.round_blob(toys.ten(), util=util))
    assert v.utility(by=0) == [{"t": 9.0, "kind": "flash", "by": "P0", "hits": [{"name": "P5", "seconds": 1.5}],
                                "hits_complete": True}]
    assert [row["kind"] for row in v.utility()] == ["flash", "ability"] and v.utility()[1]["ability"] == "Wraith"
    assert v.damage() == [{"t": 13.0, "kind": "damage", "by": "P5", "target": "P0", "source": "gun", "hits": 3}]


def test_r_f9_a_round_role_beats_the_fixed_page_group():
    """R-F9: with no side in the blob, the group comes from the round's role as the control engine derives it
    (attack -> A, defense -> B), not from the page's fixed team group."""
    blob = toys.round_blob(toys.ten())
    for p in blob["players"]:
        p["side"] = None
    groups = {s: "A" if s < 5 else "B" for s in range(10)}
    roles = {0: "defense"}                       # slot 0 defends this round, though its page group is "A"
    v = RoundView(blob, toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS, groups=groups, roles=roles)
    assert v.side(0) == "B"
    assert v.side(1) == "A"                      # no role for slot 1: the page group is the last fallback


def test_r_f10_a_living_player_with_no_sample_yet_is_unknown_not_dead():
    """R-F10: this used to read as dead (alive but nothing to place). A living player we cannot place is
    alive with an unknown position."""
    tracks = {0: toys.stand(2000, 2000, 10.0, 60.0)}          # first sample at t=10
    v = view(toys.round_blob(toys.ten(), tracks=tracks))
    assert v.position(0, 5.0) == {"alive": True, "u": None, "v": None, "area": None, "position": "unknown"}
    assert v.distance_m(0, 5, 5.0) is None


def test_r_f10_a_gap_mid_life_is_unknown_inside_the_gap():
    tracks = {0: toys.stand(2000, 2000, 0.0, 10.0) + toys.stand(2000, 2000, 20.0, 60.0)}
    v = view(toys.round_blob(toys.ten(), tracks=tracks))
    assert v.position(0, 9.5)["area"]["name"] == "A Site"
    assert v.position(0, 15.0) == {"alive": True, "u": None, "v": None, "area": None, "position": "unknown"}
    assert v.position(0, 20.5)["u"] == 2000
