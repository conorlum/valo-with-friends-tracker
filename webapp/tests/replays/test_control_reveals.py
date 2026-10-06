"""Reveals and what their sources see (W11; the 2026-10-05 review items 4, 9, 13): a revealed enemy is located
where they stand (each Neural Theft ping, a recon pulse, a Haunt, a dart, a trip going off), and a Haunt or recon
pulse clears what it sees, through the map's walls and smokes, within its range."""

import numpy as np
import pytest

from app.control import engine as ce
from app.control import utility as ut
from tests.replays.control_toys import blob, door_hall, open_hall, toy_ability, uv


def regions_of(b, geo, ticks=None):
    out = {}

    class Watch:
        def on_tick(self, tick, unknown):
            out[round(tick.t, 6)] = {s: np.isfinite(r) for s, r in unknown.reached["A"].items()}

    rc = ce.compute_round(b, geo, ticks=ticks, knowledge=False, observer=Watch())
    return rc, out


def reveal(t, target, by=0, name="X_InterrogateHat", code="Gumshoe", t1=None):
    return {"k": "reveal", "t": t, "t1": t + 2.0 if t1 is None else t1, "by": by, "target": target, "code": code,
            "name": name}


def walker():
    """B5 walks east to west along the hall's south side, out of A's view (A faces the west wall)."""
    return {0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 400, 280, 180), (10.0, 200, 280, 180)])}


def test_each_neural_theft_ping_locates_where_the_enemy_is_then():
    geo = open_hall()
    b = blob(walker(), t_end=12.0, util=[reveal(3.0, 5), reveal(7.0, 5)])
    rc, regions = regions_of(b, geo)
    rnd = ce.RoundInputs(b, geo)
    first = [i for i in rnd.infos if i.reason == "neural_theft"]
    assert [i.t for i in first] == [3.0, 5.0, 7.0, 9.0]          # each ping, and the end of each two-second show
    for t in (3.0, 7.0):
        p = rnd.pos(5, t)
        node = geo.node_at(geo.cell_of_px(p[0], p[1]), None)
        assert regions[t][5][node] and regions[t][5].sum() <= 4, t
    assert regions[6.5][5].sum() > 20, "between pings the region spreads again: no lasting omniscience"


def test_a_teammate_reveal_or_a_dead_target_locates_nothing():
    geo = open_hall()
    b = blob({**walker(), 1: ("A", [(0.0, 120, 120, 180)])}, t_end=6.0, util=[reveal(3.0, 1)])
    assert [i for i in ce.RoundInputs(b, geo).infos if i.reason == "neural_theft"] == []
    b = blob(walker(), t_end=6.0, util=[reveal(3.0, 5)], deaths={5: 2.0})
    assert ce.RoundInputs(b, geo).infos == []


def test_a_reveal_on_an_ulting_veto_locates_nothing_but_a_trip_still_does():
    geo = open_hall()
    b = blob(walker(), t_end=10.0, util=[reveal(3.0, 5), reveal(4.0, 5, name="4_TripWire", t1=5.0),
                                         {"k": "ability", "t": 2.0, "t1": 6.0, "by": 5, "kind": "GameObject",
                                          "code": "Pine", "name": "X_Evolution", "u": 0, "v": 0}])
    next(p for p in b["players"] if p["slot"] == 5)["agent"] = "Veto"
    rnd = ce.RoundInputs(b, geo)
    assert rnd.evolution == {5: [(2.0, 6.0)]}
    assert [(i.t, i.reason) for i in rnd.infos] == [(4.0, "trip"), (5.0, "trip")]


def haunt(x, y, t=2.0, by=0, z=None):
    extra = {} if z is None else {"z": z}
    return toy_ability("BountyHunter", "E_LoSReveal_Source_Reactivate", x, y, by, t=t, t1=t + 1.5, kind="GameObject",
                       **extra)


def test_a_haunt_clears_what_it_sees_and_not_behind_a_wall():
    geo = door_hall()                      # a wall x 200-216 from the north edge to the door row (y 288)
    b = blob({0: ("A", [(0.0, 104, 120, 180)]), 5: ("B", [(0.0, 400, 120, 0)])}, t_end=30.0,
             util=[haunt(300, 150, t=25.0)])
    rc, regions = regions_of(b, geo)
    [ex] = [i for i in ce.RoundInputs(b, geo).infos if i.reason == "haunt"]
    assert ex.kind == "exclude" and ex.slot == 5 and ex.detail["needs"] == "reveal"
    x = geo.centres[:, 0]
    seen = ex.mask
    assert seen[geo.node_at(geo.cell_of_px(300, 150), None)] and not (seen & (x < 200)).any()
    before, after = regions[24.5][5], regions[25.0][5]
    assert (before & seen).sum() > 50
    p = ce.RoundInputs(b, geo).pos(5, 25.0)
    own = geo.node_at(geo.cell_of_px(p[0], p[1]), None)
    assert not (after & seen & (np.arange(geo.n) != own)).any(), "nothing it saw is left, but where B5 stands"
    west = before & (x < 200)
    assert (after & west).sum() == west.sum(), "behind the wall is untouched"


def test_a_haunt_sees_only_within_its_range():
    geo = open_hall()
    b = blob({0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 400, 280, 0)])}, t_end=20.0,
             util=[haunt(410, 120, t=15.0)])
    [ex] = [i for i in ce.RoundInputs(b, geo).infos if i.reason == "haunt"]
    d = np.hypot(geo.centres[:, 0] - 410, geo.centres[:, 1] - 120) * geo.m_per_px
    assert ex.mask.any() and d[ex.mask].max() <= ut.FIGURES["reveal_range_m"]["haunt"] + 1e-6
    assert ((d < ut.FIGURES["reveal_range_m"]["haunt"] - 2) & geo.walk_n & ~ex.mask).sum() == 0


def test_a_haunt_that_revealed_someone_leaves_them_to_their_locate():
    geo = open_hall()
    b = blob({0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 400, 280, 0)]), 6: ("B", [(0.0, 390, 120, 0)])},
             t_end=20.0, util=[haunt(380, 200, t=15.0), reveal(15.2, 6, name="E_LoSReveal_Source_Reactivate",
                                                              code="BountyHunter", t1=16.0)])
    infos = [(i.slot, i.kind, i.reason) for i in ce.RoundInputs(b, geo).infos]
    assert (5, "exclude", "haunt") in infos and (6, "exclude", "haunt") not in infos
    assert (6, "locate", "haunt") in infos


def test_a_smoke_up_at_the_pulse_blocks_its_sight():
    geo = open_hall()
    smoke = toy_ability("Wraith", "4_Smoke", 330, 200, 0, t=0.0, t1=30.0)
    b = blob({0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 400, 280, 0)])}, t_end=20.0,
             util=[smoke, haunt(400, 200, t=15.0)])
    [ex] = [i for i in ce.RoundInputs(b, geo).infos if i.reason == "haunt"]
    b2 = blob({0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 400, 280, 0)])}, t_end=20.0,
              util=[haunt(400, 200, t=15.0)])
    [clear] = [i for i in ce.RoundInputs(b2, geo).infos if i.reason == "haunt"]
    behind = geo.node_at(geo.cell_of_px(220, 200), None)
    assert clear.mask[behind] and not ex.mask[behind]


def test_the_figures_are_numbers_only_and_the_sources_are_not_consumed():
    assert set(ut.FIGURES) == {"reveal_range_m", "skye_flash_range_m", "leer_cast_range_m", "hearing_m"}
    assert all(isinstance(v, float) for value in ut.FIGURES.values()
               for v in (value.values() if isinstance(value, dict) else [value]))
