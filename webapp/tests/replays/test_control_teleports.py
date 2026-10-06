"""Teleports and temporary bodies (W16-W18; the 2026-10-05 review items 12, 16-18, 21): Omen's ult, Yoru's beacon and
drift exit, Phoenix's return marker, Waylay's recall. Hearing ranges are utility.json's provisional figures (D7);
the tests set their own so the toy hall (45 m long) has places out of earshot."""

import numpy as np
import pytest

from app.control import engine as ce
from app.control import utility as ut
from tests.replays.control_toys import blob, open_hall, toy_ability, uv

R = 10.0


@pytest.fixture(autouse=True)
def short_hearing(monkeypatch):
    monkeypatch.setitem(ut.FIGURES, "hearing_m", {k: R for k in ut.FIGURES["hearing_m"]})


def omen_marker(x=380, y=120, t=4.0, t1=7.5, outcome="completed"):
    return toy_ability("Wraith", "X_GlobalTeleport_Intention", x, y, 5, t=t, t1=t1, kind="GameObject",
                       outcome=outcome, evidence="test")


def infos(players, util, deaths=None):
    b = blob(players, t_end=12.0, util=util, deaths=deaths)
    return ce.RoundInputs(b, open_hall()).infos


OMEN = ("B", [(0.0, 300, 280, 0)])


@pytest.mark.parametrize("outcome", ["completed", "cancelled"])
def test_an_unheard_unseen_destination_broadens_and_stays_whatever_the_outcome(outcome):
    listener = ("A", [(0.0, 104, 200, 180)])                        # far west, facing away
    out = infos({0: listener, 5: OMEN}, [omen_marker(outcome=outcome)])
    kinds = [(i.t, i.kind, i.reason) for i in out]
    assert kinds == [(4.0, "pause", "omen_channel"), (4.0, "broaden", "omen_unheard"), (7.5, "resume", "omen_channel")]
    broad = next(i for i in out if i.kind == "broaden")
    geo = open_hall()
    d = np.hypot(geo.centres[:, 0] - 104, geo.centres[:, 1] - 200) * geo.m_per_px
    assert not (broad.mask & (d <= R - 0.5)).any() and (broad.mask & (d > R + 1) & geo.walk_n).sum() > 100


@pytest.mark.parametrize("outcome", ["completed", "cancelled"])
def test_a_heard_destination_adds_nothing(outcome):
    near = ("A", [(0.0, 380 - 50, 120, 180)])                       # 50 px = 7 m from it, facing away
    kinds = [i.kind for i in infos({0: near, 5: OMEN}, [omen_marker(outcome=outcome)])]
    assert kinds == ["pause", "resume"]


def test_a_seen_destination_adds_nothing_and_takes_precedence():
    watcher = ("A", [(0.0, 150, 120, 0)])                          # 32 m away, looking straight at it
    kinds = [i.kind for i in infos({0: watcher, 5: OMEN}, [omen_marker()])]
    assert kinds == ["pause", "resume"]


def test_the_hearing_boundary_counts_as_heard():
    geo = open_hall()
    dx = R / geo.m_per_px - 0.5            # half a pixel inside (stored positions are rounded to the uv grid)
    at_edge = ("A", [(0.0, 380 - dx, 120, 180)])
    assert [i.kind for i in infos({0: at_edge, 5: OMEN}, [omen_marker()])] == ["pause", "resume"]


def test_a_dead_listener_hears_nothing():
    near = ("A", [(0.0, 330, 120, 180)])
    far = ("A", [(0.0, 104, 200, 180)])
    out = infos({0: near, 1: far, 5: OMEN}, [omen_marker()], deaths={0: 3.0})
    assert "broaden" in [i.kind for i in out]


def test_the_channel_freezes_his_region_and_a_cancelled_unheard_addition_is_kept():
    geo = open_hall()
    b = blob({0: ("A", [(0.0, 104, 200, 180)]), 5: OMEN}, t_end=12.0, util=[omen_marker(outcome="cancelled")])
    sizes = {}

    class Watch:
        def on_tick(self, tick, unknown):
            r = unknown.reached["A"].get(5)
            sizes[round(tick.t, 6)] = 0 if r is None else int(np.isfinite(r).sum())

    ce.compute_round(b, geo, knowledge=False, observer=Watch())
    broad = next(i for i in ce.RoundInputs(b, geo).infos if i.kind == "broaden")
    assert sizes[4.0] >= int(broad.mask.sum()) - 30
    assert sizes[8.0] >= int(broad.mask.sum()) - 30, "the cancel takes nothing back"


def test_yoru_a_heard_beacon_adds_a_protected_origin_and_an_unheard_one_nothing():
    b = blob({0: ("A", [(0.0, 330, 120, 180)]), 5: ("B", [(0.0, 104, 280, 0)])}, t_end=12.0)
    rnd = ce.RoundInputs(b, open_hall())
    [info] = ut.yoru_beacon(rnd, 5, 3.0, 380, 120, "beacon-1")
    assert (info.kind, info.reason, info.source, info.side) == ("hypothesis", "yoru_beacon", "beacon-1", "A")
    assert ut.yoru_beacon(rnd, 5, 3.0, 104, 120, "beacon-2") == [], "nobody within earshot of it"


def test_yoru_an_unheard_drift_exit_broadens_and_a_heard_one_does_nothing():
    b = blob({0: ("A", [(0.0, 330, 120, 180)]), 5: ("B", [(0.0, 104, 280, 0)])}, t_end=12.0)
    rnd = ce.RoundInputs(b, open_hall())
    assert ut.yoru_drift_exit(rnd, 5, 3.0, 350, 120, "x") == []
    [info] = ut.yoru_drift_exit(rnd, 5, 3.0, 104, 280, "x")
    assert info.kind == "broaden" and not info.mask[open_hall().node_at(open_hall().cell_of_px(330, 120), None)]


def test_yoru_has_no_reader_until_the_export_has_a_beacon_signal():
    assert ut.yoru_beacon not in ut.READERS and ut.yoru_drift_exit not in ut.READERS


def anchor(recall=True, t=2.0, t1=6.0, at=(380, 120), frm=(300, 270)):
    extra = {}
    if recall:
        u, v = uv(*frm)
        extra["recall"] = {"t": t1 - 1.0, "t1": t1, "u": u, "v": v}
    return toy_ability("Terra", "E_RewindTime_RewindTarget", at[0], at[1], 5, t=t, t1=t1, kind="GameObject", **extra)


def test_a_heard_recall_puts_waylay_at_her_return_point_when_she_arrives():
    out = infos({0: ("A", [(0.0, 330, 270, 180)]), 5: ("B", [(0.0, 300, 270, 0)])}, [anchor()])
    [info] = out
    assert (info.t, info.kind, info.reason, info.slot) == (6.0, "locate", "waylay_recall", 5)
    assert (round(info.x), round(info.y)) == (380, 120)


def test_a_recall_heard_only_where_she_arrives_counts_too():
    out = infos({0: ("A", [(0.0, 360, 120, 180)]), 5: ("B", [(0.0, 300, 270, 0)])}, [anchor()])
    assert [i.reason for i in out] == ["waylay_recall"]


def test_an_unheard_recall_or_none_at_all_changes_nothing():
    assert infos({0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 300, 270, 0)])}, [anchor()]) == []
    assert infos({0: ("A", [(0.0, 330, 270, 180)]), 5: ("B", [(0.0, 300, 270, 0)])}, [anchor(recall=False)]) == []


def test_a_heard_recall_restarts_her_region_and_no_one_elses():
    geo = open_hall()
    b = blob({0: ("A", [(0.0, 330, 270, 180)]), 5: ("B", [(0.0, 300, 270, 0), (5.9, 380, 120, 0)]),
              6: ("B", [(0.0, 400, 280, 0)])}, t_end=12.0, util=[anchor()])
    regions = {}

    class Watch:
        def on_tick(self, tick, unknown):
            regions[round(tick.t, 6)] = {s: np.isfinite(r).sum() for s, r in unknown.reached["A"].items()}

    ce.compute_round(b, geo, knowledge=False, observer=Watch())
    assert regions[6.0][5] <= 3 < regions[5.5][5]
    assert regions[6.0][6] >= regions[5.5][6]


def test_phoenixs_return_marker_tells_nothing_and_holds_no_unknown():
    marker = toy_ability("Phoenix", "X_ResTarget_Production", 380, 120, 5, t=2.0, t1=6.0, kind="GameObject")
    b = blob({0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 300, 270, 0)])}, t_end=12.0, util=[marker])
    rnd = ce.RoundInputs(b, open_hall())
    assert rnd.infos == [] and rnd.watchers == []
    tick = ce.Tick(rnd, 4.0)
    assert set(tick.holders) == {0, 5}
    node = open_hall().node_at(open_hall().cell_of_px(380, 120), None)
    assert tick.holders[5].cell != node, "his body is his track, never the marker"
