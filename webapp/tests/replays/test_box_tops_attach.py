"""Learnt box tops read per cover piece (W7): `Geometry.box_tops` (piece -> its top heights, m) and
`Geometry.box_reach` (per pixel, up to 3 topped pieces within TOP_REACH_PX). Lazy, from the current heights and
cover, cached by their shas; nothing is computed on a map without both."""

import copy
import dataclasses
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from app.control import box_tops as bt  # noqa: E402
from app.control import geometry as cg  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.control.stance import TOP_REACH_PX  # noqa: E402

A, B, D = (100, 100, 120, 120), (151, 100, 171, 120), (600, 600, 620, 620)      # x0, y0, x1, y1; labels 1, 2, 3


def cover_mask():
    m = np.zeros((cg.PX, cg.PX), bool)
    for x0, y0, x1, y1 in (A, B, D):
        m[y0:y1, x0:x1] = True
    return m


def flat_geo():
    floor = np.zeros((cg.PX, cg.PX), bool)
    floor[50:700, 50:700] = True
    return cg.geometry_from_masks("Tops", ~floor, floor, 7e-5)


def flat_asset(geo, tops=None):
    floors = np.full((cg.GRID, cg.GRID, hc.MAX_FLOORS), -1, np.int16)
    floors[geo.walk, 0] = 0
    return hc.HeightAsset(floors, np.zeros_like(floors), geo.walk.copy(), np.zeros_like(geo.walk),
                          np.zeros((0, 5), np.int32), {"version": 3, "units": "dm", "origin_z": 0},
                          box_tops=None if tops is None else np.array(tops, np.int32).reshape(-1, 3))


ROWS = [(110, 110, 12), (110, 111, 25), (112, 112, 12), (160, 110, 8), (300, 300, 10), (610, 610, 5)]
TOPPED = [(110, 110, 12), (110, 111, 25), (112, 112, 12), (160, 110, 8), (300, 300, 10)]    # D has no row


def make(rows=TOPPED, cover=True, heights=True, cover_first=False, sha="c1"):
    geo = flat_geo()
    asset = flat_asset(geo, rows)
    if cover_first and cover:
        cg.attach_cover(geo, cover_mask(), sha)
    if heights:
        cg.attach_heights(geo, asset)
    if cover and not cover_first:
        cg.attach_cover(geo, cover_mask(), sha)
    return geo


def test_tops_are_attributed_per_piece_sorted_and_in_node_z_units():
    geo = make()
    assert dict(geo.box_tops) == {1: (1.2, 2.5), 2: (0.8,)}       # the row on label 0 is skipped, D has none
    assert geo.node_z[: cg.GRID * cg.GRID][geo.walk.ravel()][0] == 0.0


def test_nothing_without_heights_tops_or_cover(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("computed")

    monkeypatch.setattr(ndimage, "distance_transform_edt", boom)
    for geo in (make(heights=False), make(rows=[]), make(rows=None), make(cover=False)):
        assert dict(geo.box_tops) == {} and geo.box_reach is None
    assert make(heights=False).heights is None


def test_reach_per_pixel():
    r = make().box_reach
    assert r.shape == (cg.PX, cg.PX, 3) and r.dtype == np.int16
    assert tuple(r[110, 110]) == (1, 0, 0)                  # inside A (B is 41 px away)
    assert tuple(r[110, 80]) == (1, 0, 0)                   # 20 px left of A's first column (100)
    assert tuple(r[110, 60]) == (0, 0, 0)                   # 40 px out
    assert tuple(r[110, 140]) == (2, 1, 0)                  # B is 11 px, A 21: nearest first
    assert tuple(r[110, 135]) == (1, 2, 0)                  # 16 and 16: the tie goes to the lower id
    assert tuple(r[610, 610]) == (0, 0, 0)                  # D has no top: never listed
    assert tuple(r[610, 605]) == (0, 0, 0)
    assert tuple(r[110, 160]) == (2, 0, 0)


def test_reach_is_euclidean_within_the_constant():
    r = make().box_reach
    assert r[110, 100 - TOP_REACH_PX][0] == 1 and r[110, 100 - TOP_REACH_PX - 1][0] == 0
    assert r[100 - 21, 100 - 28][0] == 1 and r[100 - 21, 100 - 30][0] == 0        # 35 px and 37 px diagonally


def test_attach_order_copy_and_replace_agree():
    a, b = make(), make(cover_first=True)
    assert dict(a.box_tops) == dict(b.box_tops) and np.array_equal(a.box_reach, b.box_reach)
    c = copy.copy(a)
    d = dataclasses.replace(a, name="Other")
    e = dataclasses.replace(a, heights=dataclasses.replace(a.heights))
    for g in (c, d, e):
        assert dict(g.box_tops) == dict(a.box_tops) and np.array_equal(g.box_reach, a.box_reach)


def test_a_replaced_cover_is_read_at_once():
    geo = make()
    first = dict(geo.box_tops)
    geo.cover_px = np.zeros_like(geo.cover_px)
    geo.cover_sha = "none"
    assert first and dict(geo.box_tops) == {} and geo.box_reach is None


def test_cache_by_shas_and_read_only():
    a = make()
    r = a.box_reach
    assert not r.flags.writeable and a.box_reach is r
    with pytest.raises(ValueError):
        r[0, 0, 0] = 1
    with pytest.raises((TypeError, AttributeError)):
        a.box_tops[9] = (1.0,)
    other = make(rows=[(110, 110, 30)])
    assert dict(other.box_tops) == {1: (3.0,)} and dict(a.box_tops) == {1: (1.2, 2.5), 2: (0.8,)}
    assert other.box_reach is not r and tuple(other.box_reach[110, 140]) == (1, 0, 0)
    assert make().box_reach is r                         # same shas: the cached array


def test_box_tops_do_not_touch_the_node_graph():
    with_tops, without = make(), make(rows=None)
    assert with_tops.height_sha != without.height_sha
    assert with_tops.n == without.n
    for name in ("node_cell", "node_z", "node_of", "walk_n", "unresolved", "centres"):
        assert np.array_equal(getattr(with_tops, name), getattr(without, name), equal_nan=True), name
    assert [f.name for f in dataclasses.fields(cg.Geometry) if "box_" in f.name] == []
