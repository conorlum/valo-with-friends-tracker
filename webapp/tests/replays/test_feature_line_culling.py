"""Independent unculled pixel oracle for the moving-door line rejection."""
from dataclasses import replace

import numpy as np
import pytest

from app.control import features as cf


@pytest.mark.parametrize('border', [False, True])
@pytest.mark.parametrize('all_height', [False, True])
@pytest.mark.parametrize('target_count', [0, 4, 5, 24, 600])
def test_segment_culling_matches_unculled_pixel_sampling(border, all_height, target_count):
    rng = np.random.default_rng(123)
    mask = np.zeros((1024, 1024), bool)
    mask[450:457, (0 if border else 490):(9 if border else 499)] = True
    mask[456:462, (5 if border else 494):(13 if border else 502)] = True
    occ = cf._bounded_occluder('panel', mask, 1, 3, all_height=all_height)
    targets = np.column_stack((rng.uniform(-100, 1124, (target_count, 2)), rng.uniform(-3, 6, target_count)))
    targets[::19, 2] = np.nan
    # Include vertical, axis-aligned, corner and near-boundary rays. The last
    # target fixes a long common sample interval even when it is culled.
    for a in [(0, 0, 2), (100, 300, None), (496, 453, 0), (-20, 455, 2), (490-1e-10, 450, 2)]:
        b = np.vstack((targets, [[a[0], a[1], 5], [a[0], 453, 2], [496, a[1], 2], [1500, 1500, 0]]))
        assert np.array_equal(cf.blocked_lines([occ], a, b),
                              cf.blocked_lines([replace(occ, bounds_px=None)], a, b))


def test_tight_rejection_skips_lines_whose_axis_bounds_overlap_the_door():
    # Every broad axis box overlaps this panel; only one segment enters it.
    targets = np.array([[100, 20, 0], [20, 100, 0], [100, 100, 0]])
    assert cf._lines_meet_bounds((0, 0), targets, (40, 40, 60, 60)).tolist() == [False, False, True]


def test_multiple_masks_and_unknown_bounds_keep_the_oracle_answer():
    rng = np.random.default_rng(99)
    masks = []
    for x, y in [(300, 300), (600, 200), (700, 700)]:
        mask = np.zeros((1024, 1024), bool); mask[y:y+3, x:x+8] = True
        masks.append(cf._bounded_occluder(str(x), mask, all_height=True))
    masks[1] = replace(masks[1], bounds_px=None)
    targets = np.column_stack((rng.uniform(0, 1024, (1000, 2)), np.zeros(1000)))
    assert np.array_equal(cf.blocked_lines(masks, (50, 50, 2), targets),
                          cf.blocked_lines([replace(o, bounds_px=None) for o in masks], (50, 50, 2), targets))
