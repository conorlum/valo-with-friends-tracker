"""The optional per-round `player_state` subsection, version 1 (app/replays/player_state.py; the replay
player-state plan P02): validation to a normalized copy or unavailable, and the pure query helpers.
Synthetic, anonymized fixtures; event times sit between 1/16 s samples."""

import copy
import math

import pytest

from app.replays import player_state as ps


def _state(**extra):
    state = {
        "version": 1,
        "vitals": {
            "5": [{"t": 18.626, "life": 0, "hp": 74.0, "sh": 0.0, "mhp": 100.0, "msh": 0.0}],
            "7": [{"t": -1.0, "life": 0, "hp": 100.0, "sh": 50.0, "mhp": 100.0, "msh": 50.0, "q": "start"},
                  {"t": 18.634, "life": 0, "hp": 91.334, "sh": 7.666, "mhp": 100.0, "msh": 50.0},
                  {"t": 40.0, "life": 0, "hp": 0.0, "sh": 0.0, "mhp": 100.0, "msh": 50.0},
                  {"t": 61.3, "life": 1, "hp": 60.0, "sh": 0.0, "mhp": 100.0, "msh": 0.0}],
        },
        "damage_taken": {"5": [18.626], "7": [40.0, 18.634, 61.3]},
        "spike": [{"t": -30.867, "s": "carried"}, {"t": 36.645, "s": "dropped"},
                  {"t": 39.435, "s": "carried", "slot": 1}, {"t": 76.932, "s": "planting", "slot": 1},
                  {"t": 80.918, "s": "planted", "slot": 1, "u": 5480, "v": 1136}],
    }
    state.update(extra)
    return state


# ---------------------------------------------------------------- validate


@pytest.mark.parametrize("part,key", [("overheal", "oh"), ("decay", "dec")])
def test_health_parts_normalize_stably_without_mutating(part, key):
    def point(t, value):
        return {"t": t, "life": 0, key: value, **({"unk": False} if part == "decay" else {})}

    points = [point(2, 30), point(0, 0), point(2, 10), point(3, 0)]
    state = _state(**{part: {"0": points}})
    original = copy.deepcopy(state)
    got = ps.validate(state)
    assert got[part] == {"0": [points[1], points[0], points[2], points[3]]}
    assert ps.validate(got) == got
    assert state == original


@pytest.mark.parametrize("part,point", [
    ("overheal", {"t": 1, "life": 0, "oh": -1}),
    ("overheal", {"t": 1, "life": 0, "oh": True}),
    ("overheal", {"t": 1, "life": 0, "oh": ps.VALUE_MAX + 1}),
    ("overheal", {"t": 1, "oh": 20}),
    ("decay", {"t": 1, "life": 0, "dec": "20", "unk": False}),
    ("decay", {"t": 1, "life": 0, "dec": -1, "unk": False}),
    ("decay", {"t": 1, "life": 0, "dec": math.nan, "unk": False}),
    ("decay", {"t": 1, "life": 0, "dec": ps.VALUE_MAX + 1, "unk": False}),
    ("decay", {"t": 1, "life": 0, "dec": 20, "unk": 1}),
    ("decay", {"t": 1, "life": 0, "dec": 20}),
    ("decay", {"t": 1, "dec": 20, "unk": False}),
])
def test_health_parts_drop_and_count_each_bad_point(part, point):
    good = {"t": 2, "life": 0, **({"oh": 0} if part == "overheal" else {"dec": 0, "unk": False})}
    diagnostics = {}
    got = ps.validate(_state(**{part: {"0": [point, point, good]}}), diagnostics)
    assert got[part] == {"0": [good]}
    assert diagnostics == {f"{part}: bad event": 2}


@pytest.mark.parametrize("part", ["overheal", "decay"])
def test_health_parts_limits_shapes_and_missing(part):
    good = {"t": 1, "life": 0, **({"oh": 0} if part == "overheal" else {"dec": 0, "unk": False})}
    diagnostics = {}
    got = ps.validate(_state(**{part: {"0": [good] * 513, "1": [good] * 512,
                                        "2": "bad", "10": [good]}}), diagnostics)
    assert got[part] == {"1": [good] * 512}
    assert diagnostics[f"{part}: too many events (slot unavailable)"] == 1
    assert diagnostics[f"{part}: bad slot"] == 2
    diagnostics = {}
    assert ps.validate(_state(**{part: []}), diagnostics) == ps.validate(_state())
    assert diagnostics == {f"{part}: wrong shape (unavailable)": 1}
    query = ps.overheal_at if part == "overheal" else ps.decay_at
    assert query(ps.validate(_state()), 0, 1) is None
    assert query(None, 0, 1) is None


def test_overheal_at_points_and_across_lives():
    points = [{"t": 1, "life": 0, "oh": 100}, {"t": 2, "life": 0, "oh": 40},
              {"t": 2, "life": 0, "oh": 20}, {"t": 5, "life": 1, "oh": 10}]
    state = ps.validate(_state(overheal={"0": points}))
    assert ps.overheal_at(state, 0, 0) is None
    assert ps.overheal_at(state, 0, 1) == points[0]
    assert ps.overheal_at(state, 0, 1.5) == points[0]
    assert ps.overheal_at(state, 0, 2) == points[2]
    assert ps.overheal_at(state, 0, 10) == points[3]
    assert ps.overheal_at(state, 0, 4, life=1) is None
    assert ps.overheal_at(state, 0, 10, life=0) == points[2]
    assert ps.overheal_at(state, 1, 10) is None


def test_decay_at_interpolates_jumps_unknown_and_closure():
    points = [{"t": 0, "life": 0, "dec": 0, "unk": False},
              {"t": 0.75, "life": 0, "dec": 90, "unk": False},
              {"t": 2, "life": 0, "dec": 30, "unk": True},
              {"t": 2, "life": 0, "dec": 10, "unk": True},
              {"t": 3, "life": 0, "dec": 0, "unk": False},
              {"t": 5, "life": 1, "dec": 20, "unk": False},
              {"t": 6, "life": 1, "dec": 0, "unk": False}]
    state = ps.validate(_state(decay={"0": points}))
    for t, expected in [(-1, None), (0, {"dec": 0, "unk": False}),
                        (0.375, {"dec": 45, "unk": False}),
                        (1.375, {"dec": 60, "unk": False}),
                        (2, {"dec": 10, "unk": True}), (2.5, {"dec": 5, "unk": True}),
                        (3, {"dec": 0, "unk": False}), (4, None),
                        (5.5, {"dec": 10, "unk": False}), (7, None)]:
        assert ps.decay_at(state, 0, t) == expected
    assert ps.decay_at(state, 0, 4, life=1) is None
    assert ps.decay_at(state, 0, 5, life=0) is None
    assert ps.decay_at(state, 1, 2) is None
    # No interpolation into a different life, even when the previous life lacks a closing knot.
    state["decay"]["0"] = [points[3], points[5]]
    assert ps.decay_at(state, 0, 4) == {"dec": 10, "unk": True}


def test_a_valid_subsection_comes_back_normalized_and_stable():
    got = ps.validate(_state())
    assert got is not None and got["version"] == 1
    assert got["damage_taken"]["7"] == [18.634, 40.0, 61.3], "sorted"
    assert ps.validate(copy.deepcopy(got)) == got, "normalizing twice changes nothing"
    assert _state()["damage_taken"]["7"] == [40.0, 18.634, 61.3], "the input is not mutated"


@pytest.mark.parametrize("bad", [None, [], "x", 5, {"vitals": {}}, {"version": 2}, {"version": "1"},
                                 {"version": True}, {"version": 1, "vitals": []},
                                 {"version": 1, "damage_taken": [1.0]}, {"version": 1, "spike": {}}])
def test_an_unsupported_version_or_malformed_structure_is_unavailable(bad):
    assert ps.validate(bad) is None


def test_missing_parts_are_unavailable_not_zero():
    got = ps.validate({"version": 1})
    assert got == {"version": 1}
    assert ps.vitals_at(got, 3, 10.0) is None
    assert ps.damaged_by(got, 3, 10.0) is False
    assert ps.spike_at(got, 10.0) is None


GOOD = {"t": 20.0625, "life": 0, "hp": 50.0, "sh": 25.0, "mhp": 100.0, "msh": 50.0}


def _but(**change):
    event = {**GOOD, **change}
    return {k: v for k, v in event.items() if v is not _MISSING}


_MISSING = object()


def test_the_good_vitals_event_is_kept():
    state = _state()
    state["vitals"]["5"].append(dict(GOOD))
    assert ps.validate(state)["vitals"]["5"][-1] == GOOD


@pytest.mark.parametrize("event", [
    _but(t=math.nan), _but(t=math.inf), _but(t=99999.0), _but(t=_MISSING),
    _but(hp=-1.0), _but(hp=math.nan), _but(hp=5000.0), _but(hp=True), _but(hp="50"),
    _but(mhp=0.0), _but(msh=-25.0), _but(sh=math.inf),
    _but(life=-1), _but(life=0.5), _but(life=True), _but(life=_MISSING),
    _but(q="guess"),
    "not an event",
])
def test_a_bad_vitals_event_is_dropped_and_the_rest_kept(event):
    state = _state()
    state["vitals"]["5"].append(event)
    diagnostics = {}
    got = ps.validate(state, diagnostics)
    assert got["vitals"]["5"] == _state()["vitals"]["5"]
    assert got["vitals"]["7"] == ps.validate(_state())["vitals"]["7"]
    assert sum(diagnostics.values()) >= 1


@pytest.mark.parametrize("key", ["hp", "sh", "mhp", "msh"])
def test_every_vitals_value_is_a_required_key(key):
    state = _state()
    state["vitals"]["5"].append(_but(**{key: _MISSING}))
    assert ps.validate(state)["vitals"]["5"] == _state()["vitals"]["5"]


@pytest.mark.parametrize("key", ["hp", "mhp"])
def test_hp_and_its_max_are_never_null(key):
    state = _state()
    state["vitals"]["5"].append(_but(**{key: None}))
    assert ps.validate(state)["vitals"]["5"] == _state()["vitals"]["5"]


def test_an_unproven_shield_is_null_together_and_the_bar_is_unavailable():
    state = _state()
    state["vitals"]["5"] += [_but(sh=None, msh=None), _but(t=11.0625, sh=None), _but(t=12.0625, msh=None)]
    got = ps.validate(state)["vitals"]["5"]
    assert got[-1] == {**GOOD, "sh": None, "msh": None}, "HP is still carried; one shield value alone is dropped"
    assert len(got) == 2
    assert ps.health_pct(got[-1]) is None
    assert ps.vitals_at({"vitals": {"5": got}}, 5, 20.0625)["hp"] == 50.0


@pytest.mark.parametrize("slot", ["10", "-1", "x", "1.5"])
def test_an_out_of_range_slot_is_dropped(slot):
    state = _state()
    state["vitals"][slot] = [dict(GOOD)]
    state["damage_taken"][slot] = [1.0]
    got = ps.validate(state)
    assert slot not in got["vitals"] and slot not in got["damage_taken"]
    assert set(got["vitals"]) == {"5", "7"}


def test_damage_times_drop_bad_values_and_duplicates():
    got = ps.validate(_state(damage_taken={"2": [18.626, math.nan, -500.0, "1", True, 18.626, 3.0625]}))
    assert got["damage_taken"] == {"2": [3.0625, 18.626]}


@pytest.mark.parametrize("entry", [
    {"t": 50.0, "s": "juggled"},
    {"t": 50.0, "s": "carried", "slot": 10},
    {"t": 50.0, "s": "dropped", "u": 10001, "v": 5},
    {"t": 50.0, "s": "dropped", "u": 5},
    {"t": 50.0, "s": "dropped", "u": 5.5, "v": 5},
    {"t": math.nan, "s": "dropped"},
    {"s": "dropped"},
])
def test_a_bad_spike_entry_is_dropped(entry):
    state = _state()
    state["spike"].insert(2, entry)
    assert ps.validate(state)["spike"] == ps.validate(_state())["spike"]


def test_absurd_counts_make_that_part_unavailable():
    hits = [{**GOOD, "t": 1.0 + i / 1000} for i in range(ps.MAX_EVENTS + 1)]
    got = ps.validate(_state(vitals={"3": hits, "5": _state()["vitals"]["5"]},
                             damage_taken={"3": [1.0 + i / 1000 for i in range(ps.MAX_EVENTS + 1)]},
                             spike=[{"t": float(i), "s": "carried"} for i in range(ps.MAX_SPIKE + 1)]))
    assert "3" not in got["vitals"] and "5" in got["vitals"]
    assert "3" not in got["damage_taken"]
    assert "spike" not in got


def test_unknown_keys_are_dropped_from_the_copy():
    state = _state(future={"x": 1})
    state["vitals"]["5"][0]["extra"] = 9
    state["spike"][0]["evidence"] = "anything"
    got = ps.validate(state)
    assert "future" not in got and "extra" not in got["vitals"]["5"][0] and "evidence" not in got["spike"][0]


# ---------------------------------------------------------------- vitals and health percent


def test_vitals_at_never_selects_a_future_event():
    got = ps.validate(_state())
    assert ps.vitals_at(got, 5, 18.625) is None, "the hit at 18.626 is not visible at 18.625"
    assert ps.vitals_at(got, 5, 18.626)["hp"] == 74.0, "right-continuous: it applies at its own time"
    assert ps.vitals_at(got, 7, 18.63)["q"] == "start"
    assert ps.vitals_at(got, 7, 30.0)["hp"] == 91.334


def test_vitals_at_does_not_carry_an_earlier_life():
    got = ps.validate(_state())
    assert ps.vitals_at(got, 7, 50.0, life=0)["hp"] == 0.0
    assert ps.vitals_at(got, 7, 50.0, life=1) is None, "revived at 50 with no new record yet: unavailable"
    assert ps.vitals_at(got, 7, 61.3, life=1)["hp"] == 60.0
    assert ps.vitals_at(got, 7, 61.3, life=0)["hp"] == 0.0


def test_equal_time_vitals_take_the_later_source_order():
    got = ps.validate(_state(vitals={"4": [{"t": 18.626, "life": 0, "hp": 90.0, "sh": 0.0, "mhp": 100.0, "msh": 0.0},
                                           {"t": 18.626, "life": 0, "hp": 80.0, "sh": 0.0, "mhp": 100.0, "msh": 0.0}]}))
    assert ps.vitals_at(got, 4, 18.626)["hp"] == 80.0


@pytest.mark.parametrize("event, expected", [
    ({"hp": 91.334, "sh": 7.666, "mhp": 100.0, "msh": 50.0}, 66.0),
    ({"hp": 74.0, "sh": 0.0, "mhp": 100.0, "msh": 0.0}, 74.0),
    ({"hp": 100.0, "sh": 25.0, "mhp": 100.0, "msh": 25.0}, 100.0),
    ({"hp": 150.0, "sh": 0.0, "mhp": 100.0, "msh": 0.0}, 100.0),
    ({"hp": 0.0, "sh": 0.0, "mhp": 100.0, "msh": 50.0}, 0.0),
    ({"hp": 50.0, "sh": 0.0, "mhp": 100.0}, None),
    ({"hp": 50.0, "mhp": 100.0, "msh": 0.0}, None),
    ({"sh": 0.0, "mhp": 100.0, "msh": 0.0}, None),
    ({"hp": 0.0, "sh": 0.0, "mhp": 0.0, "msh": 0.0}, None),
    (None, None),
])
def test_health_percent_uses_the_measured_maxima(event, expected):
    got = ps.health_pct(event)
    assert got == (pytest.approx(expected) if expected is not None else None)


def test_health_percent_is_never_over_a_fixed_150():
    assert ps.health_pct({"hp": 100.0, "sh": 0.0, "mhp": 100.0, "msh": 0.0}) == 100.0


# ---------------------------------------------------------------- damage and spike


def test_damaged_by_is_from_the_first_confirmed_hit_on():
    got = ps.validate(_state())
    assert ps.damaged_by(got, 5, 18.625) is False
    assert ps.damaged_by(got, 5, 18.626) is True
    assert ps.damaged_by(got, 5, 90.0) is True
    assert ps.damaged_by(got, 0, 90.0) is False


def test_spike_at_follows_the_transitions():
    got = ps.validate(_state())
    assert ps.spike_at(got, -31.0) is None
    assert ps.spike_at(got, 0.0) == {"t": -30.867, "s": "carried"}, "carried with no proven carrier"
    assert ps.spike_at(got, 36.645)["s"] == "dropped"
    assert ps.spike_at(got, 39.435) == {"t": 39.435, "s": "carried", "slot": 1}
    assert ps.spike_at(got, 80.917)["s"] == "planting"
    assert ps.spike_at(got, 80.918) == {"t": 80.918, "s": "planted", "slot": 1, "u": 5480, "v": 1136}


@pytest.mark.parametrize("group, winner", [
    ([{"s": "planted", "slot": 1}, {"s": "planting", "slot": 1}], "planted"),
    ([{"s": "planted", "slot": 1}, {"s": "carried", "slot": 1}], "planted"),
    ([{"s": "carried", "slot": 1}, {"s": "planted", "slot": 1}], "planted"),
    ([{"s": "dropped"}, {"s": "carried", "slot": 2}], "carried"),
    ([{"s": "carried", "slot": 2}, {"s": "dropped"}], "dropped"),
    ([{"s": "planted"}, {"s": "defused"}], "defused"),
])
def test_equal_time_spike_precedence(group, winner):
    state = _state(spike=[{"t": 10.0, "s": "carried", "slot": 1}, *({"t": 45.5625, **g} for g in group)])
    got = ps.validate(state)
    assert ps.spike_at(got, 45.5625)["s"] == winner
    assert ps.validate(got) == got


@pytest.mark.parametrize("group", [
    [{"s": "carried", "slot": 2}, {"s": "carried", "slot": 3}],
    [{"s": "carried", "slot": 2}, {"s": "dropped"}, {"s": "carried", "slot": 3}],
    [{"s": "planted", "slot": 2}, {"s": "planted", "slot": 3}],
])
def test_contradictory_records_at_one_time_become_unknown(group):
    state = _state(spike=[{"t": 10.0, "s": "carried", "slot": 1}, *({"t": 45.5625, **g} for g in group),
                          {"t": 50.0625, "s": "dropped"}])
    diagnostics = {}
    got = ps.validate(state, diagnostics)
    assert ps.spike_at(got, 45.5625) == {"t": 45.5625, "s": "unknown"}, "never a guessed carrier"
    assert ps.spike_at(got, 45.5) == {"t": 10.0, "s": "carried", "slot": 1}
    assert ps.spike_at(got, 50.0625)["s"] == "dropped", "a later record resolves it"
    assert diagnostics == {"spike: contradictory records at one time (unknown)": 1}
    assert ps.validate(got) == got


def test_the_same_carrier_twice_is_not_a_contradiction():
    got = ps.validate(_state(spike=[{"t": 45.5625, "s": "carried", "slot": 2}, {"t": 45.5625, "s": "carried"},
                                    {"t": 45.5625, "s": "carried", "slot": 2}]))
    assert ps.spike_at(got, 45.5625) == {"t": 45.5625, "s": "carried", "slot": 2}
