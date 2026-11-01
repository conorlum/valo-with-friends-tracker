"""Box-top teaching uses recorded crouch keys, while older blobs retain their learnt tops."""

from dataclasses import asdict, replace
import json

import numpy as np
import pytest

from app.control import box_tops as bt
from tests.replays.test_box_tops import GROUND, blob, const, cover, seg, stand


@pytest.fixture
def cover_px():
    return cover()


def found(b, cover_px):
    return bt.top_stands(b, cover_px, m_per_px=1.0)


def netted(t=5.0, t1=6.0, target=3):
    # The frozen status-row shape; slot numbers and times are synthetic.
    return {"by": 6, "code": "Cashew", "from": "object", "k": "status",
            "name": "Q_ShellShockGrenade", "status": "netted", "t": t, "t1": t1, "target": target}


def test_a_key_held_through_a_run_teaches_the_standing_top(cover_px):
    b = blob(seg(5.0, *const(120, 120, 38, 1)))
    b["crouch"] = {"3": [[4.0, 6.0]]}
    st = found(b, cover_px)
    assert len(st) == 1
    assert (st[0].z_dm, st[0].crouch) == (40, "key")
    assert bt.judge(st, GROUND, lambda px: ())[0].tolist() == [[120, 120, 40]]


def test_straddling_a_key_edge_corrects_only_the_crouched_samples(cover_px):
    # Six raw samples, all one steady run: 40, 40, 40, 42, 42, 42 dm.
    # The first three are crouched, so the corrected median is 42 (raw median 41).
    b = {"hz": 10, "tracks": {"3": [seg(5.0, [120] * 6, [120] * 6, [40] * 3 + [42] * 3)]},
         "crouch": {"3": [[4.0, 5.0]]}}
    st = found(b, cover_px)
    assert len(st) == 1 and (st[0].t0, st[0].t1) == (5.0, 5.5)
    assert (st[0].z_dm, st[0].crouch) == (42, "key")


def test_body_lags_both_key_edges_and_includes_the_interval_end(cover_px):
    # Key [0, .29] -> body [.21, .5]. Of .1, .2, .3, .4, .5, .6,
    # exactly .3, .4, .5 are crouched: the median moves by one dm.
    b = {"hz": 10, "tracks": {"3": [seg(0.1, [120] * 6, [120] * 6, [40] * 6)]},
         "crouch": {"3": [[0.0, 0.29]]}}
    st = found(b, cover_px)
    assert len(st) == 1 and (st[0].z_dm, st[0].crouch) == (41, "key")


@pytest.mark.parametrize("key_end, classification", [(3.79, "up"), (0.0, "none")])
def test_nearby_key_activity_protects_an_up_stand_from_folding(cover_px, key_end, classification):
    b = blob(seg(5.0, *const(108, 120, 35, 1)))
    b["crouch"] = {"3": [[key_end - 1, key_end]]}
    low, = found(b, cover_px)
    assert (low.z_dm, low.crouch) == (35, classification)
    rows = bt.judge([low, stand(), stand()], GROUND, lambda px: ())[0]
    assert set(rows[:, 2]) == ({35, 40} if classification == "up" else {40})


@pytest.mark.parametrize("classification", ["key", "up"])
def test_known_stances_keep_their_z_and_still_support_other_folds(classification):
    low = stand(35, px=(108, 120), crouch=classification)
    high = stand(40, crouch=classification)
    assert [s.z_dm for s in bt._fold_crouches([low, high, high])] == [35, 40, 40]
    unknown = replace(low, crouch="none")
    assert [s.z_dm for s in bt._fold_crouches([unknown, high, high])] == [40, 40, 40]


@pytest.mark.parametrize("with_netted", [False, True])
def test_older_blobs_keep_stands_and_judge_rows_byte_for_byte(cover_px, with_netted):
    b = blob(seg(5.0, *const(108, 120, 35, 1)),
             seg(10.0, *const(120, 120, 40, 1)), seg(15.0, *const(120, 120, 40, 1)))
    if with_netted:
        b["util"] = [netted()]
    st = found(b, cover_px)
    expected = [bt.TopStand(0, 3, i, t, t + 0.992, 1, z, px, (1,), (0.0,))
                for i, t, z, px in ((0, 5., 35, (108, 120)), (1, 10., 40, (120, 120)),
                                    (2, 15., 40, (120, 120)))]
    assert all(s.crouch == "none" for s in st)
    encode = lambda stands: json.dumps([asdict(s) for s in stands], sort_keys=True).encode()
    assert encode(st) == encode(expected)
    rows, assigned, conflicts, counts = bt.judge(st, GROUND, lambda px: ())
    assert rows.tobytes() == np.array([[108, 120, 40], [120, 120, 40]], np.int32).tobytes()
    assert assigned == conflicts == []
    assert counts == dict(teach=3, assign=0, conflict=0, ground=0, platform=0, blackout=0)


def test_netted_without_key_activity_is_crouched_without_lag(cover_px):
    b = blob(seg(5.0, *const(120, 120, 38, 1)))
    b.update(crouch={}, util=[netted()])
    s, = found(b, cover_px)
    assert (s.z_dm, s.crouch) == (40, "key")


@pytest.mark.parametrize("change", [{"target": 2}, {"status": "concussed"}, {"k": "ability"}])
def test_other_status_rows_do_not_crouch_the_player(cover_px, change):
    b = blob(seg(5.0, *const(120, 120, 38, 1)))
    b.update(crouch={}, util=[dict(netted(), **change)])
    s, = found(b, cover_px)
    assert (s.z_dm, s.crouch) == (38, "none")


def test_nearby_netted_interval_is_evidence_of_an_up_stand(cover_px):
    b = blob(seg(5.0, *const(120, 120, 38, 1)))
    b.update(crouch={}, util=[netted(3.0, 4.0)])
    s, = found(b, cover_px)
    assert (s.z_dm, s.crouch) == (38, "up")
