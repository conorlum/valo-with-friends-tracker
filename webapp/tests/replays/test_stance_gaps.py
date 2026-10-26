"""The gap detector and the tick cache carry the stance's eye and see-over piece (plan 4e, gaps part)."""

import gzip
import pickle
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

from control_toys import BOX_WITNESS, box_hall, open_hall, on_box_track  # noqa: E402
from test_stance_engine import T, inputs, still  # noqa: E402
from test_stance import CENTRE  # noqa: E402


def run(geo, players, tmp_path, name="stance"):
    """(live PlayerViews at T, cached PlayerViews at T, the raw cached player tuples at T, engine Tick holders)."""
    from app.control import engine as ce
    from app.gaps import cache

    rnd, blob = inputs(geo, players)
    records = {}
    writer = cache.Writer(cache.cache_path(1, 1, name, tmp_path))

    def observe(rec, unk):
        if abs(rec.t - T) < 1e-6:
            records["live"] = rec
            records["stance"] = {s: h.stance for s, h in ce.Tick(rnd, T).holders.items()}
        writer(rec, unk)

    rc = ce.compute_round(blob, geo, None, observer=observe, knowledge=False)
    writer.close(rc.missing_inputs)
    cached = next(rec for rec, _ in cache.replay(writer.path) if abs(rec.t - T) < 1e-6)
    with gzip.open(writer.path, "rb") as f:
        pickle.load(f)
        body = pickle.load(f)
    raw = next(tk[1] for tk in body["ticks"] if abs(tk[0] - T) < 1e-6)
    return records["live"].players, cached.players, raw, records["stance"], blob


def on_box_round(geo, tmp_path):
    players = {0: on_box_track(*CENTRE, yaw=0), 5: still("B", 330.0, 100.0, 180)}
    return run(geo, players, tmp_path)


def test_player_on_the_box_carries_the_stance_eye_and_piece(tmp_path):
    geo = box_hall()
    live, cached, raw, stances, _ = on_box_round(geo, tmp_path)
    st = stances[0]
    assert st is not None and st.kind == "on_box" and st.see_over
    for pv in (live[0], cached[0]):
        assert pv.see_over == int(st.see_over)
        assert pv.eye_x == float(st.eye_px[0]) and pv.eye_y == float(st.eye_px[1])
    assert len(raw[0]) == 12 and tuple(raw[0][9:]) == (live[0].eye_x, live[0].eye_y, live[0].see_over)


def test_sight_from_the_eye_with_the_piece_sees_what_without_it_does_not(tmp_path):
    from app.control import engine as ce
    from app.gaps import detect as gd

    geo = box_hall()
    live, _, _, _, blob = on_box_round(geo, tmp_path)
    pv = live[0]

    def fresh():
        return gd.GapDetector(geo, ce.RoundInputs(blob, geo, None, None))

    def sight(det, over):
        return det._sight(pv.eye_x, pv.eye_y, pv.eye_z, pv.node, [], see_over=over)

    det = fresh()
    over, flat = sight(det, pv.see_over), sight(det, 0)
    assert (over & ~flat).any(), "the see-over piece must open cells the flat cast does not see"
    assert not (flat & ~over).any()
    assert over[BOX_WITNESS["E"]]
    again = fresh()                                    # the other order: the memo hands neither the other's answer
    flat2, over2 = sight(again, 0), sight(again, pv.see_over)
    assert (flat2 == flat).all() and (over2 == over).all()


def test_sight_memo_keys_on_see_over(monkeypatch):
    from app.control import engine
    from app.gaps import detect
    from tests.replays.control_toys import blob

    geo = open_hall()
    det = detect.GapDetector(geo, engine.RoundInputs(blob({}, t_end=10), geo))
    seen = []

    def cast(*args, **kwargs):
        seen.append(kwargs.get("see_over"))
        return np.ones(geo.n, bool)

    monkeypatch.setattr(detect, "cast", cast)
    det._sight(124.0, 124.0, None, 0, [], see_over=0)
    det._sight(124.0, 124.0, None, 0, [], see_over=1)
    det._sight(124.0, 124.0, None, 0, [], see_over=1)
    assert seen == [0, 1] and det.timing["casts"] == 2


def test_no_stance_round_keeps_nine_field_tuples_and_default_views(tmp_path):
    geo = box_hall()
    players = {0: ("A", [(0.0, 60.0, 60.0, 0, 0.0), (10.0, 60.0, 60.0, 0, 0.0)]), 5: still("B", 330.0, 100.0, 180)}
    live, cached, raw, stances, _ = run(geo, players, tmp_path)
    assert stances[0] is None
    assert all(len(t) == 9 for t in raw.values())
    for pv in list(live.values()) + list(cached.values()):
        assert (pv.eye_x, pv.eye_y, pv.see_over) == (None, None, 0)


def test_round_on_a_map_without_cover_has_nine_field_tuples(tmp_path):
    geo = open_hall()
    assert getattr(geo, "cover_px", None) is None
    players = {0: ("A", [(0.0, 60.0, 60.0, 0, 0.0), (10.0, 60.0, 60.0, 0, 0.0)]), 5: still("B", 330.0, 100.0, 180)}
    live, cached, raw, stances, _ = run(geo, players, tmp_path)
    assert all(len(t) == 9 for t in raw.values())
    assert all(pv.eye_x is None and pv.see_over == 0 for pv in cached.values())
