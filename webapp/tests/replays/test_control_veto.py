"""Veto's Evolution (W10, the 2026-10-05 review item 19): enemy blinds, suppression, reveals and statuses don't
touch him while it is on; sight, drone cones and a trip's trigger still do. No local replay has a Veto, so these
rounds are toys whose players are relabelled Veto, with an explicit Evolution row (control_toys labels everyone
Jett, so each test checks its own labels first)."""

import pytest

from app.control import engine as ce
from app.control import utility as ut
from tests.replays.control_toys import blob, open_hall

EVOLUTION = (2.0, 6.0)


def veto_round(veto_slot=5, util=()):
    b = blob({0: ("A", [(0.0, 120, 200, 0)]), 1: ("A", [(0.0, 130, 250, 0)]), 5: ("B", [(0.0, 400, 200, 180)]),
              6: ("B", [(0.0, 390, 150, 180)])}, t_end=10.0, util=list(util))
    for p in b["players"]:
        if p["slot"] == veto_slot:
            p["agent"] = "Veto"
    b["util"].append({"k": "ability", "t": EVOLUTION[0], "t1": EVOLUTION[1], "by": veto_slot, "kind": "GameObject",
                      "code": "Pine", "name": "X_Evolution", "u": 0, "v": 0})
    return b


def inputs(b):
    rnd = ce.RoundInputs(b, open_hall())
    veto = [p["slot"] for p in b["players"] if p["agent"] == "Veto"]
    assert len(veto) == 1 and rnd.evolution == {veto[0]: [EVOLUTION]}, "the fixture's Veto and his Evolution"
    return rnd, veto[0]


@pytest.mark.parametrize("capability, works_during", sorted(ut.CAPABILITIES.items()))
@pytest.mark.parametrize("t, during", [(1.0, False), (2.0, True), (5.99, True), (6.0, False)])
def test_the_capability_matrix_before_during_and_after(capability, works_during, t, during):
    spans = {5: [EVOLUTION]}
    assert ut.affects(capability, spans, 5, t) is (works_during or not during)
    assert ut.affects(capability, spans, 6, t) is True                 # nobody else is immune


def test_only_sight_drone_sight_and_triggers_still_work():
    assert {c for c, ok in ut.CAPABILITIES.items() if ok} == {"sight", "drone_sight", "trigger"}


@pytest.mark.parametrize("veto_slot, other", [(5, 6), (1, 0)])
def test_blinds_miss_him_during_evolution_only(veto_slot, other):
    by = 0 if veto_slot >= 5 else 5
    flash = {"k": "flash", "t": 0.5, "by": by, "ability": "skye_guiding_light", "targets": [veto_slot, other],
             "hits": [[veto_slot, 1.0, 1.0], [veto_slot, 3.0, 1.0], [veto_slot, 7.0, 1.0], [other, 3.0, 1.0]]}
    rnd, veto = inputs(veto_round(veto_slot, [flash]))
    assert veto == veto_slot
    assert rnd.flashed[veto] == [ce._span(1.0, 2.0), ce._span(7.0, 8.0)]
    assert rnd.flashed[other] == [ce._span(3.0, 4.0)]


def test_a_zero_length_blind_on_him_is_dropped_too():
    flash = {"k": "flash", "t": 2.5, "by": 0, "ability": "skye_guiding_light", "targets": [5], "hits": [[5, 3.0, 0.0]]}
    rnd, _ = inputs(veto_round(5, [flash]))
    assert rnd.flashed[5] == []


def test_reveals_and_statuses_miss_him_but_not_his_team():
    util = [{"k": "reveal", "t": 3.0, "t1": 4.0, "by": 0, "target": 5, "code": "Gumshoe", "name": "X_InterrogateHat"},
            {"k": "reveal", "t": 3.0, "t1": 4.0, "by": 0, "target": 6, "code": "Gumshoe", "name": "X_InterrogateHat"},
            {"k": "reveal", "t": 7.0, "t1": 8.0, "by": 0, "target": 5, "code": "Gumshoe", "name": "X_InterrogateHat"},
            {"k": "status", "t": 3.0, "t1": 5.0, "by": 0, "target": 5, "status": "suppressed", "code": "Grenadier",
             "name": "E_SuppressionPulse", "from": "object"},
            {"k": "status", "t": 3.0, "t1": 4.0, "by": 0, "target": 5, "status": "concussed", "code": "Gumshoe",
             "name": "4_TripWire", "from": "object"},
            {"k": "status", "t": 3.0, "t1": 4.0, "by": 0, "target": 6, "status": "concussed", "code": "Gumshoe",
             "name": "4_TripWire", "from": "object"}]
    rnd, _ = inputs(veto_round(5, util))
    assert [(r[3], r[0]) for r in rnd.reveals] == [(6, ce.snap(3.0)), (5, ce.snap(7.0))]
    assert rnd.contest_status[5] == [] and rnd.downgraded[5] == [ce._span(7.0, 8.0)]
    assert rnd.downgraded[6] == [ce._span(3.0, 4.0), ce._span(3.0, 4.0)]


def test_an_inference_that_needs_a_capability_he_is_immune_to_is_dropped():
    b = veto_round(5)
    infos = [ut.Info(3.0, "A", 5, "exclude", "knife_zero", x=400, y=200, radius_m=15, detail={"needs": "suppress"}),
             ut.Info(3.0, "A", 6, "exclude", "knife_zero", x=400, y=200, radius_m=15, detail={"needs": "suppress"}),
             ut.Info(3.5, "A", 5, "locate", "neural_theft", x=400, y=200, detail={"needs": "reveal"}),
             ut.Info(4.0, "A", 5, "locate", "drone_seen", x=400, y=200, detail={"needs": "drone_sight"}),
             ut.Info(7.0, "A", 5, "locate", "neural_theft", x=400, y=200, detail={"needs": "reveal"})]
    rnd = ce.RoundInputs(b, open_hall(), infos=infos)
    assert [(i.t, i.slot, i.reason) for i in rnd.infos] == [(3.0, 6, "knife_zero"), (4.0, 5, "drone_seen"),
                                                           (7.0, 5, "neural_theft")]


def test_a_round_without_a_veto_is_untouched():
    b = blob({0: ("A", [(0.0, 120, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=10.0,
             util=[{"k": "ability", "t": 2.0, "t1": 6.0, "by": 5, "kind": "GameObject", "code": "Pine",
                    "name": "X_Evolution", "u": 0, "v": 0},
                   {"k": "flash", "t": 2.5, "by": 0, "ability": "x", "targets": [5], "hits": [[5, 3.0, 1.0]]}])
    rnd = ce.RoundInputs(b, open_hall())
    assert rnd.evolution == {} and rnd.flashed[5] == [ce._span(3.0, 4.0)]     # a Jett with that row isn't a Veto
