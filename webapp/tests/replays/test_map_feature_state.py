"""The map-feature reducer (app/replays/map_feature_state.py): guarded transitions, motion and its
mid-motion policies, destruction, round reset, proximity occupancy and equal-time ordering, on the shared
event-sequence fixtures (tests/fixtures/control/map_features/reducer_cases.json) that the JS twin also runs."""

import copy
import json
from pathlib import Path

import pytest

from app.replays import map_feature_state as fs

CASES = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "control" / "map_features" / "reducer_cases.json")
                   .read_text(encoding="utf-8"))


def compact(trace):
    return [[e["t"], e["kind"], e["result"], e["state"]] for e in trace]


@pytest.mark.parametrize("case", CASES["cases"], ids=[c["name"][:60] for c in CASES["cases"]])
def test_reducer_case(case):
    feature = CASES["features"][case["feature"]]
    before = copy.deepcopy(feature), copy.deepcopy(case["events"])
    trace = fs.run(feature, case["events"])
    assert compact(trace) == [[float(t), k, r, s] for t, k, r, s in case["expect"]]
    assert (feature, case["events"]) == before, "the reducer must not change its inputs"


def test_destruction_clears_pending_and_terminal_rejects_everything_but_reset():
    door = CASES["features"]["compound_door"]
    trace = fs.run(door, [{"t": 1, "kind": "switch"}, {"t": 2, "kind": "destroy"}, {"t": 2.5, "kind": "observed", "state": "open"}])
    assert trace[0]["pending"] == [[3.0, "motion_complete", None]]
    assert trace[1]["pending"] == [] and trace[1]["moving"] is False
    assert trace[2]["result"] == "rejected" and trace[2]["reason"] == "terminal"


def test_reset_starts_a_new_epoch_and_clears_occupancy():
    door = CASES["features"]["proximity_door"]
    trace = fs.run(door, [{"t": 1, "kind": "proximity_enter", "occupant": "a"}, {"t": 2, "kind": "reset"}])
    assert trace[-1]["epoch"] == 1 and trace[-1]["occupancy"] == 0 and trace[-1]["state"] == "closed"


def test_stale_reasons_and_notes_are_recorded():
    door = CASES["features"]["unresolved_door"]
    trace = fs.run(door, [{"t": 0, "kind": "shoot"}, {"t": 1, "kind": "switch"}, {"t": 2, "kind": "switch"}])
    assert trace[0]["reason"] == "no_transition"
    assert trace[1]["notes"] == ["unresolved_duration"]
    assert trace[2]["reason"] == "unresolved_policy"


def test_until_stops_before_later_events():
    door = CASES["features"]["compound_door"]
    trace = fs.run(door, [{"t": 1, "kind": "switch"}], until=2)
    assert compact(trace) == [[1.0, "switch", "applied", "opening"]]


def test_unknown_guards_and_events_are_errors():
    with pytest.raises(fs.FeatureStateError):
        fs.guard_ok({"eval": "1"}, fs.initial({"initial_state": "x"}))
    with pytest.raises(fs.FeatureStateError):
        fs.run({"initial_state": "x"}, [{"t": 0, "kind": "teleport"}])


def test_a_follow_up_loop_is_stopped():
    looping = {"initial_state": "a", "states": [{"name": "a"}],
               "transitions": [{"id": "x", "from": ["a"], "event": "scheduled", "name": "tick", "to": "a",
                                "follow_up": {"after": {"status": "known", "value": 1}, "name": "tick"}},
                               {"id": "go", "from": ["a"], "event": "switch", "to": "a",
                                "follow_up": {"after": {"status": "known", "value": 1}, "name": "tick"}}]}
    with pytest.raises(fs.FeatureStateError):
        fs.run(looping, [{"t": 0, "kind": "switch"}])
    assert len(fs.run(looping, [{"t": 0, "kind": "switch"}], until=5)) == 6
