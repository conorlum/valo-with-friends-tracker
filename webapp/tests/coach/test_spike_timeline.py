"""The coach's timeline: each defuse attempt (tap / half / defused) and the plant's place and planter."""

import pytest

from app.coach import areas, query, snapshot
from tests.coach import toys
from tests.coach.test_snapshot import CONTEXT


@pytest.fixture(autouse=True)
def toy_areas(monkeypatch):
    monkeypatch.setattr(areas, "callouts", lambda map_name, table=None: toys.CALLOUTS["Toy"])


def bomb(t=10.0, t1=None, defuses=(), owner_by="planted", by=1, u=8000, v=8000):
    entry = {"k": "ability", "kind": "Bomb", "name": "Spike", "by": by, "t": t, "t1": t1,
             "defuses": [list(d) for d in defuses]}
    if owner_by is not None:
        entry["owner_by"] = owner_by
    if u is not None:
        entry["u"], entry["v"] = u, v
    return entry


def make(tmp_path, entry=None, db=None, kills=(), t_end=60.0):
    util = [] if entry is None else [entry]
    blob = toys.round_blob(toys.ten(), kills=kills, util=util, t_end=t_end, n=1)
    context = {**CONTEXT, "match": {**CONTEXT["match"], "rounds": [1]},
               "rounds": {"1": {"db": db or {}, "stats": {}, "kills": {}}}}
    snapshot.save(snapshot.assemble(context, {1: blob}, {}), tmp_path)
    return snapshot.load(tmp_path)


def attempts(snap):
    return [e for e in query.timeline(snap, 1)["events"] if e["kind"] == "defuse" and "result" in e]


def test_a_tap_then_an_unfinished_half_ending_at_the_defusers_death(tmp_path):
    kills = [{"t": 31.04, "killer": 5, "victim": 3}]
    snap = make(tmp_path, bomb(defuses=[(20.0, 21.9, 2, False), (25.0, 31.04, 3, False)]), kills=kills)
    first, second = attempts(snap)
    assert (first["result"], first["seconds"], first["by"]) == ("tap", 1.9, "P2#T2")
    assert (second["result"], second["seconds"], second["by"]) == ("half", 6.04, "P3#T3")
    assert "resumed" not in first and "resumed" not in second and "until" not in second


def test_many_taps_then_a_finish_drop_the_site_defuse(tmp_path):
    taps = [(20.0 + i * 0.25, 20.0 + i * 0.25 + 0.07, 2, False) for i in range(34)]
    snap = make(tmp_path, bomb(defuses=taps + [(40.0, 47.0, 2, True)]), db={"plant": 10.0, "defuse": 47.0})
    events = query.timeline(snap, 1)["events"]
    assert [e["result"] for e in events if e["kind"] == "defuse"].count("tap") == 34
    assert [e["result"] for e in events if e["kind"] == "defuse"].count("defused") == 1
    assert all("result" in e for e in events if e["kind"] == "defuse")


def test_an_unfinished_attempt_keeps_the_site_defuse(tmp_path):
    snap = make(tmp_path, bomb(defuses=[(20.0, 21.0, 2, False)]), db={"plant": 10.0, "defuse": 47.0})
    defuses = [e for e in query.timeline(snap, 1)["events"] if e["kind"] == "defuse"]
    assert len(defuses) == 2 and sum("result" in e for e in defuses) == 1


def test_a_single_finished_attempt_says_how_long_the_spike_had_left(tmp_path):
    snap = make(tmp_path, bomb(t=10.0, defuses=[(20.0, 27.0, 2, True)]))
    (only,) = attempts(snap)
    assert (only["result"], only["seconds"], only["t"], only["t1"]) == ("defused", 7.0, 20.0, 27.0)
    assert only["spike_left_s"] == 35.0


def test_an_attempt_after_a_half_is_resumed(tmp_path):
    snap = make(tmp_path, bomb(defuses=[(20.0, 24.0, 2, False), (30.0, 33.5, 2, True)]))
    half, done = attempts(snap)
    assert (half["result"], "resumed" in half) == ("half", False)
    assert (done["result"], done["resumed"]) == ("defused", True)


def test_every_attempt_after_a_half_is_resumed_and_judged_by_its_own_length(tmp_path):
    snap = make(tmp_path, bomb(defuses=[(20.0, 24.0, 2, False), (30.0, 34.0, 3, False), (40.0, 41.0, 2, False)]))
    first, second, third = attempts(snap)
    assert [a["result"] for a in (first, second, third)] == ["half", "half", "tap"]
    assert ("resumed" in first, second.get("resumed"), third.get("resumed")) == (False, True, True)


def test_the_half_boundary_is_judged_on_the_displayed_seconds(tmp_path):
    snap = make(tmp_path, bomb(defuses=[(20.0, 23.499, 2, False), (30.0, 33.494, 2, False)]))
    assert [(a["seconds"], a["result"]) for a in attempts(snap)] == [(3.5, "half"), (3.49, "tap")]


def test_a_null_stop_is_never_defused(tmp_path):
    snap = make(tmp_path, bomb(defuses=[(20.0, None, 2, True)]), t_end=25.0, db={"plant": 10.0, "defuse": 25.0})
    defuses = [e for e in query.timeline(snap, 1)["events"] if e["kind"] == "defuse"]
    assert [e.get("result") for e in defuses] == ["half", None]


def test_the_half_boundary_is_three_and_a_half_seconds(tmp_path):
    snap = make(tmp_path, bomb(defuses=[(10.0, 13.5, 2, False), (20.0, 20.0 + 3.49, 2, False),
                                        (30.0, 32.0, 2, True)]))
    assert [e["result"] for e in attempts(snap)] == ["half", "tap", "defused"]


def test_a_null_stop_ends_at_the_defusers_death(tmp_path):
    kills = [{"t": 22.0, "killer": 5, "victim": 2}]
    snap = make(tmp_path, bomb(defuses=[(20.0, None, 2, False)]), kills=kills)
    (only,) = attempts(snap)
    assert (only["result"], only["seconds"], only["t1"], only["until"]) == ("tap", 2.0, 22.0, "death")


def test_a_null_stop_ends_at_the_round_end(tmp_path):
    snap = make(tmp_path, bomb(defuses=[(20.0, None, 2, False)]), t_end=25.0)
    (only,) = attempts(snap)
    assert (only["result"], only["seconds"], only["until"]) == ("half", 5.0, "round_end")


def test_a_null_stop_is_capped_by_the_bomb_end(tmp_path):
    snap = make(tmp_path, bomb(t1=24.0, defuses=[(20.0, None, 2, False)]))
    (only,) = attempts(snap)
    assert (only["result"], only["seconds"], only["t1"], only["until"]) == ("half", 4.0, 24.0, "round_end")


def test_a_start_in_no_alive_interval_runs_to_the_cap(tmp_path):
    kills = [{"t": 15.0, "killer": 5, "victim": 2}]
    snap = make(tmp_path, bomb(defuses=[(20.0, None, 2, False)]), kills=kills, t_end=30.0)
    (only,) = attempts(snap)
    assert (only["result"], only["seconds"], only["until"]) == ("half", 10.0, "round_end")


def test_the_plant_carries_the_planter_and_the_bombs_own_place(tmp_path):
    snap = make(tmp_path, bomb(t=10.0, by=1))
    (plant,) = [e for e in query.timeline(snap, 1)["events"] if e["kind"] == "plant"]
    assert plant == {"t": 10.0, "kind": "plant", "by": "P1#T1", "area": {"name": "B Site", "super": "B"}}


def test_a_guessed_planter_is_not_named(tmp_path):
    snap = make(tmp_path, bomb(owner_by="nearest"))
    (plant,) = [e for e in query.timeline(snap, 1)["events"] if e["kind"] == "plant"]
    assert plant["by"] is None and plant["area"]["name"] == "B Site"


def test_a_bomb_without_a_place_has_no_area(tmp_path):
    snap = make(tmp_path, bomb(u=None))
    (plant,) = [e for e in query.timeline(snap, 1)["events"] if e["kind"] == "plant"]
    assert plant["area"] is None


def test_without_a_bomb_entry_the_site_plant_time_stands(tmp_path):
    snap = make(tmp_path, None, db={"plant": 12.5})
    (plant,) = [e for e in query.timeline(snap, 1)["events"] if e["kind"] == "plant"]
    assert plant == {"t": 12.5, "kind": "plant", "by": None, "area": None}


def test_the_spike_is_not_an_ability_row_in_the_timeline_but_is_in_utility(tmp_path):
    snap = make(tmp_path, bomb())
    assert not [e for e in query.timeline(snap, 1)["events"] if e["kind"] == "ability"]
    assert [r["ability"] for r in snap.view(1).utility()] == ["Spike"]
    assert snap.view(1).utility(spike=False) == []
    assert snap.view(1).spike()["kind"] == "Bomb"
