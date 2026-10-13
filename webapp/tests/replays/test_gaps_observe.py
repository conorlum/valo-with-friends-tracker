"""The observer hook and the tick cache (timing-gaps spec, section 3)."""

import numpy as np
import pytest

from app.control import engine as ce
from app.control import heights as hc
from app.gaps import cache
from tests.replays.control_toys import blob, door_hall, height_blob, standing, toy_heights


@pytest.mark.parametrize('rejected', ['bounds', 'digest', 'missing_digest', 'missing_bounds'])
def test_rejected_tick_cache_panel_keeps_unculled_visibility(tmp_path, rejected):
    from dataclasses import replace
    from types import SimpleNamespace
    from app.control import features
    from app.control.observe import TickRecord
    from app.control.routes import RouteLog
    from app.gaps import detect
    from tests.replays.control_toys import open_hall

    geo = open_hall()
    mask = np.zeros((1024, 1024), bool)
    mask[96:296, 200:204] = True
    original = features._bounded_occluder('door', mask, bottom=1, top=3, all_height=True)
    changes = {
        'bounds': {'bounds_px': (1, 1, 4, 4)},
        'digest': {'mask_digest': b'wrong', 'bounds_px': (1, 1, 4, 4)},
        'missing_digest': {'mask_digest': None, 'bounds_px': (1, 1, 4, 4)},
        'missing_bounds': {'bounds_px': None},
    }
    candidate = replace(original, source_area='rejected-source-proof', **changes[rejected])
    unknown = SimpleNamespace(geo=geo, log={'A': RouteLog(), 'B': RouteLog()})
    writer = cache.Writer(tmp_path/'rejected-panel.pkl.gz')
    for t in (0., 1.):
        writer(TickRecord(t, {}, {'A': {}, 'B': {}}, {'A': [], 'B': []}, [candidate]), unknown)
    writer.close()

    # Independent fallback: retain the physical raster/band without ANY culling metadata.
    reference = features.BoundedOccluder('door', mask.copy(), bottom=1, top=3, all_height=True)
    source, target = geo.node_at_px(124, 196), geo.node_at_px(340, 196)
    rnd = ce.RoundInputs(blob({}, t_end=10), geo)
    baseline = detect.GapDetector(geo, rnd)
    expected = baseline._sight(124, 196, 2., source, [reference])
    assert baseline._sight(124, 196, 2., source, [])[target]
    assert not expected[target]
    restored = detect.GapDetector(geo, rnd)
    for rec, _ in cache.replay(writer.path):
        panel, = rec.smokes
        assert np.array_equal(panel.mask, mask)
        assert (panel.owner, panel.bottom, panel.top, panel.all_height) == ('door', 1, 3, True)
        assert panel.bounds_px is None and panel.mask_digest is None and panel.source_area is None
        actual = restored._sight(124, 196, 2., source, rec.smokes)
        assert np.array_equal(actual, expected)
        assert not restored._sight_cache
    assert restored.timing['casts'] == 2


def test_tick_cache_restores_only_verified_panel_immutability(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace
    from app.control import features
    from app.control.observe import TickRecord
    from app.control.routes import RouteLog
    mask = np.zeros((1024, 1024), bool); mask[96:296, 200:204] = True
    trusted = features._bounded_occluder('compiled', mask, all_height=True)
    direct = features.BoundedOccluder('direct', mask.copy())
    altered = mask.copy(); altered[100, 200] = False
    wrong_digest = replace(trusted, owner='altered', mask=altered)
    # Shared storage must not make a bad enclosing bound eligible for reuse.
    wrong_bounds = replace(trusted, owner='bounds', bounds_px=(1, 1, 4, 4))
    geo = door_hall()
    unknown = SimpleNamespace(geo=geo, log={'A': RouteLog(), 'B': RouteLog()})
    writer = cache.Writer(tmp_path/'panels.pkl.gz')
    for t in (0., 1.):
        writer(TickRecord(t, {}, {'A': {}, 'B': {}}, {'A': [], 'B': []},
                          [trusted, direct, wrong_digest, wrong_bounds]), unknown)
    writer.close()
    records = list(cache.replay(writer.path))
    for record, _ in records:
        good, mutable, bad_hash, bad_bounds = record.smokes
        assert not good.mask.flags.writeable and good.mask_digest == trusted.mask_digest
        assert good.bounds_px == trusted.bounds_px
        assert np.array_equal(good.mask, trusted.mask)
        assert mutable.mask.flags.writeable and mutable.mask_digest is None
        assert bad_hash.mask.flags.writeable and bad_hash.mask_digest is None
        assert bad_bounds.mask_digest is None and bad_bounds.bounds_px is None
        assert mutable.bounds_px is None and bad_hash.bounds_px is None
        assert all(p.source_area is None for p in (mutable, bad_hash, bad_bounds))
    assert records[0][0].smokes[0] is records[1][0].smokes[0]


def _round():
    return blob({0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180), (6.0, 300, 270, 180)])}, t_end=6.0)


def test_control_is_byte_identical_with_an_observer():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import control_reference as ref
    geo, data = door_hall(), _round()
    plain = ref.digest_round(ce.compute_round(data, geo), data)
    watched = ref.digest_round(ce.compute_round(data, geo, observer=lambda rec, unk: None), data)
    assert plain == watched


def test_the_observer_sees_every_tick_with_players_and_unknown():
    geo, data = door_hall(), _round()
    seen = []
    rc = ce.compute_round(data, geo, observer=lambda rec, unk: seen.append(
        (rec.t, sorted(rec.players), {s: {e: int((a >= 0).sum()) for e, a in d.items()} for s, d in rec.unknown.items()})))
    assert [t for t, _, _ in seen] == rc.ticks.tolist()
    assert seen[0][1] == [0, 5]
    assert seen[-1][2]["A"][5] > 0


def test_the_view_is_the_players_own_sight_before_memory():
    """R12: `view` is active | passive (presence included) as the tick built it, before Memory adds
    remembered ground to `passive`; `live` is view plus utility and the player's own node."""
    geo, data = door_hall(), _round()
    checked = []

    def look(rec, unk):
        for p in rec.players.values():
            assert p.view is not None and p.view.dtype == bool and len(p.view) == geo.n
            assert not (p.view & ~p.live).any()
            assert np.array_equal(p.live, p.view | p.utility | (np.arange(geo.n) == p.node))
            assert p.eye_z is None                    # a flat map
        checked.append(rec.t)

    ce.compute_round(data, geo, observer=look)
    assert checked


def test_eye_z_is_the_engines_eye_height_on_a_map_with_heights():
    """R13: `eye_z` is Tick._eye: the player's own z plus EYE_M."""
    geo = toy_heights("ObserveEye", [(96, 96, 416, 296)], upper=[((200, 150, 260, 200), 3.0)])
    data = height_blob({0: standing(230, 175, 3.0), 5: standing(380, 250, 0.0, "B")}, t_end=3.0)
    eyes = []
    ce.compute_round(data, geo, observer=lambda rec, unk: eyes.append({s: p.eye_z for s, p in rec.players.items()}))
    assert eyes
    for e in eyes:
        assert abs(e[0] - (3.0 + hc.EYE_M)) < 0.05
        assert abs(e[5] - hc.EYE_M) < 0.05


def test_cache_keys_differ_by_round(tmp_path):
    a = cache.cache_path(7, 1, "abcd", tmp_path)
    b = cache.cache_path(7, 2, "abcd", tmp_path)
    c = cache.cache_path(8, 1, "abcd", tmp_path)
    assert len({a, b, c}) == 3


def test_cache_keys_differ_by_engine_key(tmp_path):
    """R5: a different engine key (a choke or hearing edit) never names the old file."""
    assert cache.cache_path(7, 1, "abcd", tmp_path) != cache.cache_path(7, 1, "abce", tmp_path)


def test_the_cache_replays_what_the_observer_saw(tmp_path):
    geo, data = door_hall(), _round()
    live = []
    path = cache.cache_path(1, 1, "f", tmp_path)
    writer = cache.Writer(path)

    def both(rec, unk):
        live.append((rec.t, {s: {e: a.copy() for e, a in d.items()} for s, d in rec.unknown.items()}))
        writer(rec, unk)

    ce.compute_round(data, geo, observer=both)
    writer.close()
    replayed = list(cache.replay(path))
    assert [rec.t for rec, _ in replayed] == [t for t, _ in live]
    for (rec, logs), (_, want) in zip(replayed, live):
        for side, enemies in want.items():
            for e, arr in enemies.items():
                assert np.array_equal(rec.unknown[side][e], arr)
    assert len(replayed[-1][1]["A"]) > 0


def test_the_cache_keeps_players_events_and_smokes(tmp_path):
    geo = toy_heights("ObserveCache", [(96, 96, 416, 296)], upper=[((200, 150, 260, 200), 3.0)])
    data = height_blob({0: standing(230, 175, 3.0), 5: ("B", [(0.0, 380, 250, 180, 0.0), (3.0, 300, 270, 180, 0.0)])},
                       t_end=3.0)
    live = []
    path = cache.cache_path(1, 2, "k", tmp_path)
    writer = cache.Writer(path)

    def both(rec, unk):
        live.append((rec.t, {s: (p.team, p.node, p.x, p.y, p.yaw, p.live.copy(), p.utility.copy(), p.view.copy(),
                                 p.eye_z) for s, p in rec.players.items()},
                     {k: list(v) for k, v in rec.events.items()}, list(rec.smokes)))
        writer(rec, unk)

    ce.compute_round(data, geo, observer=both)
    writer.close()
    replayed = [rec for rec, _ in cache.replay(path)]
    assert len(replayed) == len(live)
    for rec, (t, players, events, smokes) in zip(replayed, live):
        assert rec.t == t and rec.events == events and rec.smokes == smokes
        assert sorted(rec.players) == sorted(players)
        for s, (team, node, x, y, yaw, lv, ut, view, eye) in players.items():
            p = rec.players[s]
            assert (p.team, p.node, p.x, p.y, p.yaw, p.eye_z) == (team, node, x, y, yaw, eye)
            assert np.array_equal(p.live, lv) and np.array_equal(p.utility, ut) and np.array_equal(p.view, view)
    assert any(p.eye_z is not None for rec in replayed for p in rec.players.values())


def test_the_cache_stores_the_missing_inputs(tmp_path):
    """R20: the compute-time missing-input counts travel with the file."""
    geo, data = door_hall(), _round()
    path = cache.cache_path(1, 3, "m", tmp_path)
    writer = cache.Writer(path)
    rc = ce.compute_round(data, geo, observer=writer)
    writer.close(rc.missing_inputs | {"locating event without a position": 2})
    assert cache.read_missing(path) == rc.missing_inputs | {"locating event without a position": 2}
    empty = cache.cache_path(1, 4, "m", tmp_path)
    w2 = cache.Writer(empty)
    ce.compute_round(data, geo, observer=w2)
    w2.close()
    assert cache.read_missing(empty) == {}
    assert not list(tmp_path.glob("*.tmp"))


def test_a_cache_of_another_format_is_refused(tmp_path):
    import gzip
    import pickle

    import pytest

    path = tmp_path / "old.ticks.pkl.gz"
    with gzip.open(path, "wb") as f:
        pickle.dump({"format": 0}, f)
    with pytest.raises(ValueError):
        list(cache.replay(path))
    with pytest.raises(ValueError):
        cache.read_missing(path)
