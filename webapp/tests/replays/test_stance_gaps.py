"""The gap detector and the tick cache carry the stance's eye and see-over piece (plan 4e, gaps part)."""

import gzip
import pickle
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

from control_toys import BOX_WITNESS, box_hall, box_hall_tops, open_hall, on_box_track  # noqa: E402
from test_stance_engine import T, inputs, still  # noqa: E402
from test_stance import CENTRE  # noqa: E402


def test_over_cache_preserves_the_real_z_own_node(tmp_path):
    geo = box_hall_tops()
    live, cached, raw, stances, _ = run(geo, {0: on_box_track(252, 196, rise_m=3.0)}, tmp_path)
    st = stances[0]
    assert st.kind == "over" and st.own != live[0].node
    assert live[0].own == cached[0].own == raw[0][-1] == st.own
    assert tuple(raw[0][9:12]) == (live[0].eye_x, live[0].eye_y, st.see_over)


def test_over_exposure_cast_and_sight_memo_keep_own_nodes_apart(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from app.control import engine, observe
    from app.gaps import detect

    geo = box_hall_tops()
    rnd, _ = inputs(geo, {0: on_box_track(252, 196, rise_m=3.0)})
    tk = engine.Tick(rnd, T)
    rec = observe.record(tk, SimpleNamespace(entry={}, events={}))
    pv = rec.players[0]
    assert pv.own is not None and pv.own != pv.node
    other = replace(pv, own=pv.node)
    seen = []

    def cast(*args, **kwargs):
        seen.append(kwargs["own"])
        out = np.zeros(geo.n, bool)
        out[kwargs["own"]] = True
        return out

    monkeypatch.setattr(detect, "cast", cast)
    det = detect.GapDetector(geo, rnd)
    det.prev_t = T - .5
    rec.smokes = [(280.0, 196.0, 8.0, True)]
    for p in (pv, other, pv):
        det._tick_cache.clear()
        det._smoked(p, rec)
    assert seen == [pv.own, pv.own, other.own, other.own]
    assert det.timing["casts"] == 4
    # A judged unknown node triggers the victim's exposure cast through the same memo.
    ent = np.full(geo.n, -1, np.int64)
    ent[BOX_WITNESS["E"]] = 0
    for p in (pv, other):
        det._victim(p, rec, SimpleNamespace(), {5: ent})
        sight = det._victim_prev[p.slot]["sight"]
        assert sight[p.own] and not sight[p.node if p.own != p.node else pv.own]
        assert det._victim_prev[p.slot]["own"] == p.own
    assert det.timing["casts"] == 4
    # No unknown last tick: the moved-cause fallback must reconstruct sight with that tick's own.
    det._victim(pv, rec, SimpleNamespace(), {})
    prev = dict(det._victim_prev[pv.slot], node=pv.node + 1)
    det._sight_cache.clear()
    seen.clear()
    log = SimpleNamespace(trace=lambda _: np.array([0]), node=np.array([BOX_WITNESS["E"]]), t=np.array([T]))
    det._cause(pv, "A", 0, 5, log, 0, T, T, [(5, np.array([BOX_WITNESS["E"]]))], prev)
    assert seen == [pv.own]


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
    geo = box_hall_tops()
    live, cached, raw, stances, _ = on_box_round(geo, tmp_path)
    st = stances[0]
    assert st is not None and st.kind == "on_box" and st.see_over
    for pv in (live[0], cached[0]):
        assert pv.see_over == tuple(st.see_over)
        assert pv.eye_x == float(st.eye_px[0]) and pv.eye_y == float(st.eye_px[1])
    assert len(raw[0]) == 13 and tuple(raw[0][9:12]) == (live[0].eye_x, live[0].eye_y, live[0].see_over)
    assert raw[0][-1] is None


def test_sight_from_the_eye_with_the_piece_sees_what_without_it_does_not(tmp_path):
    from app.control import engine as ce
    from app.gaps import detect as gd

    geo = box_hall_tops()
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
    assert seen == [(), (1,)] and det.timing["casts"] == 2


def test_no_stance_round_appends_own_after_nine_fields_and_keeps_default_views(tmp_path):
    geo = box_hall()
    players = {0: ("A", [(0.0, 60.0, 60.0, 0, 0.0), (10.0, 60.0, 60.0, 0, 0.0)]), 5: still("B", 330.0, 100.0, 180)}
    live, cached, raw, stances, _ = run(geo, players, tmp_path)
    assert stances[0] is None
    assert all(len(t) == 10 and t[-1] is None for t in raw.values())
    for pv in list(live.values()) + list(cached.values()):
        assert (pv.eye_x, pv.eye_y, pv.see_over, pv.own) == (None, None, (), None)


def test_round_on_a_map_without_cover_appends_own_after_nine_fields(tmp_path):
    geo = open_hall()
    assert getattr(geo, "cover_px", None) is None
    players = {0: ("A", [(0.0, 60.0, 60.0, 0, 0.0), (10.0, 60.0, 60.0, 0, 0.0)]), 5: still("B", 330.0, 100.0, 180)}
    live, cached, raw, stances, _ = run(geo, players, tmp_path)
    assert all(len(t) == 10 and t[-1] is None for t in raw.values())
    assert all(pv.eye_x is None and pv.see_over == () for pv in cached.values())


def test_an_old_cache_file_with_an_int_see_over_reads_back_as_a_tuple(tmp_path):
    """Files written before the vantage (R6) store the piece as an int, 0 for none: they read as (n,) and ()."""
    geo = box_hall_tops()
    on_box_round(geo, tmp_path)
    from app.gaps import cache

    path = cache.cache_path(1, 1, "oldformat", tmp_path)
    with gzip.open(cache.cache_path(1, 1, "stance", tmp_path), "rb") as f:
        head, body = pickle.load(f), pickle.load(f)
    for tk in body["ticks"]:
        tk[1].update({s: v[:11] + (3,) if len(v) == 13 else v[:9] for s, v in tk[1].items()})
    first = body["ticks"][0][1]
    first[99] = tuple(next(iter(first.values()))[:9]) + (1.0, 2.0, 0)
    with gzip.open(path, "wb") as f:
        pickle.dump(head, f)
        pickle.dump(body, f)
    views = [pv for rec, _ in cache.replay(path) for pv in rec.players.values() if pv.eye_x is not None]
    assert views and all(isinstance(pv.see_over, tuple) for pv in views)
    assert all(pv.own is None for rec, _ in cache.replay(path) for pv in rec.players.values())
    assert any(pv.see_over == (3,) for pv in views) and any(pv.see_over == () for pv in views)
