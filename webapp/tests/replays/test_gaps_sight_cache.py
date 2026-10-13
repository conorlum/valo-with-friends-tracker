"""Exact release/exposure casts reuse bounded results across read-only ticks."""
from dataclasses import replace

import numpy as np
import pytest

from app.control import engine, features
from app.control.observe import TickRecord
from app.control.routes import RouteLog
from app.gaps import detect
from tests.replays.control_toys import blob, open_hall


def detector():
    geo = open_hall()
    rnd = engine.RoundInputs(blob({}, t_end=10), geo)
    return detect.GapDetector(geo, rnd)


def test_identical_sight_requests_reuse_one_cast_and_keep_fallback_notes(monkeypatch):
    det = detector()
    calls = []
    def cast(*args, **kwargs):
        calls.append(kwargs)
        kwargs['record']['unresolved_rays'] = 4
        return np.ones(det.geo.n, bool)
    monkeypatch.setattr(detect, 'cast', cast)
    first = det._sight(124, 124, None, 0, [(180, 180, 35, False)])
    again = det._sight(124., 124., None, 0, [(180, 180, 35, False)])
    assert first is again and len(calls) == 1
    assert det.timing['casts'] == 1
    assert det.notes == {'exposure rays through unresolved terrain (2D sight there)': 1}
    assert not first.flags.writeable


@pytest.mark.parametrize('changed', ['x', 'y', 'eye', 'node', 'smoke', 'panel'])
def test_changed_sight_inputs_are_not_reused(changed, monkeypatch):
    det = detector()
    calls = []
    def cast(*args, **kwargs):
        calls.append(kwargs)
        return np.ones(det.geo.n, bool)
    monkeypatch.setattr(detect, 'cast', cast)
    mask = np.zeros((1024, 1024), bool)
    mask[96:296, 200:204] = True
    panel = features._bounded_occluder('door', mask, all_height=True)
    args = [124., 124., 1.7, 0, [panel]]
    det._sight(*args)
    if changed == 'x': args[0] += .1
    elif changed == 'y': args[1] += .1
    elif changed == 'eye': args[2] += .1
    elif changed == 'node': args[3] = 1
    elif changed == 'smoke': args[4] = [panel, (180, 180, 35, False)]
    else: args[4] = [replace(panel, bottom=1, top=3, all_height=False)]
    det._sight(*args)
    assert len(calls) == 2


def test_new_tick_reuses_exact_sight_but_mutable_rasters_do_not(monkeypatch):
    det = detector()
    calls = []
    def cast(*args, **kwargs):
        calls.append(kwargs)
        return np.ones(det.geo.n, bool)
    monkeypatch.setattr(detect, 'cast', cast)
    det._sight(124, 124, None, 0, [])
    logs = {'A': RouteLog(), 'B': RouteLog()}
    det.step(TickRecord(0, {}, {'A': {}, 'B': {}}, {'A': [], 'B': []}, []), logs)
    det._sight(124, 124, None, 0, [])
    assert len(calls) == 1
    mutable = features.BoundedOccluder('direct', np.zeros((1024, 1024), bool))
    det._sight(124, 124, None, 0, [mutable])
    mutable.mask[:] = True
    det._sight(124, 124, None, 0, [mutable])
    assert len(calls) == 3


def test_wall_geometry_changes_and_equivalent_compiler_panels(monkeypatch):
    det = detector()
    calls = []
    def cast(*args, **kwargs):
        calls.append(kwargs)
        return np.ones(det.geo.n, bool)
    monkeypatch.setattr(detect, 'cast', cast)
    wall = engine.Wall.from_points([(140, 200), (300, 200)])
    first = det._sight(124, 124, 1.7, 0, [wall])
    assert det._sight(124, 124, 1.7, 0, [engine.Wall(wall.segs.copy())]) is first
    wall.segs[0, 0] += 20
    det._sight(124, 124, 1.7, 0, [wall])
    assert len(calls) == 2
    mask = np.zeros((1024, 1024), bool); mask[96:296, 200:204] = True
    a = features._bounded_occluder('a', mask, all_height=True)
    b = features._bounded_occluder('b', mask.copy(), all_height=True)
    first = det._sight(124, 124, 1.7, 0, [a])
    assert det._sight(124, 124, 1.7, 0, [b]) is first
    assert len(calls) == 3


def test_sight_lru_is_bounded_and_geometry_replacement_invalidates_it(monkeypatch):
    import copy
    det = detector()
    calls = []
    def cast(*args, **kwargs):
        calls.append(kwargs)
        return np.ones(det.geo.n, bool)
    monkeypatch.setattr(detect, 'cast', cast)
    monkeypatch.setattr(detect, 'MAX_SIGHT_CACHE_BYTES', 2 * (det.geo.n + 1024))
    for x in (124, 125, 126):
        det._sight(x, 124, None, 0, [])
    assert len(det._sight_cache) == 2 and det._sight_bytes <= detect.MAX_SIGHT_CACHE_BYTES
    det._sight(124, 124, None, 0, [])  # evicted
    assert len(calls) == 4
    det.geo = copy.copy(det.geo)
    det.geo.sight = det.geo.sight.copy()
    det._sight(124, 124, None, 0, [])
    assert len(calls) == 5 and len(det._sight_cache) == 1
