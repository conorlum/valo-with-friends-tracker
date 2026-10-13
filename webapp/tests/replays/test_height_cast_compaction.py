"""Compare optimized ray state handling with the frozen pre-change caster."""
import copy

import numpy as np
import pytest

from app.control import geometry as cg, heights as hc
from tests.replays.height_cast_reference import _cast_heights as reference
from tests.replays.test_feature_visibility_cache import bridge
from tests.replays.test_adaptive_ground import mixed


@pytest.mark.parametrize('terrain', ['bridge', 'fine-flat', 'fine-ledge', 'fine-unresolved'])
@pytest.mark.parametrize('blockers', ['none', 'hollow', 'solid', 'wall', 'combined'])
def test_compacted_rays_preserve_nodes_and_diagnostics(terrain, blockers):
    geo = copy.deepcopy(bridge() if terrain == 'bridge' else mixed(
        {'fine-flat': (0, 0, 0, 0), 'fine-ledge': (0, 30, 0, 30),
         'fine-unresolved': (0, 30, None, 0)}[terrain]))
    if terrain == 'bridge':
        points = [(124.25, 124.75), (280, 200), (210, 280)]
    else:
        points = [(196.25, 196.75), (202, 202), (206, 202)]
    for x, y in points:
        node = geo.node_at_px(x, y)
        z = geo.node_z[node]
        eye = float(z + hc.EYE_M) if np.isfinite(z) else hc.EYE_M
        wall = cg.Wall.from_points([(x+12, y-50), (x+12, y+60)])
        cases = {'none': [], 'hollow': [(x+8, y+3, 15, False)],
                 'solid': [(x+8, y+3, 15, True)], 'wall': [wall],
                 'combined': [wall, (x-8, y+3, 10, False)]}
        for angles in (np.arange(0, 360, .5), np.array([0., 90., 180., 270.]), np.array([])):
            before, after = {}, {}
            expected = reference(geo, x, y, angles, cases[blockers], eye, node, before)
            actual = cg._cast_heights(geo, x, y, angles, cases[blockers], eye, node, after)
            assert np.array_equal(actual, expected)
            assert after == before


def test_each_cast_observes_mutable_terrain_and_angle_changes():
    geo = copy.deepcopy(bridge())
    rng = np.random.default_rng(913)
    for _ in range(12):
        x, y = rng.uniform(98, 414), rng.uniform(98, 294)
        node = geo.node_at_px(x, y)
        geo.node_z[np.isfinite(geo.node_z)] += .125
        eye = float(geo.node_z[node] + hc.EYE_M)
        angles = rng.uniform(-720, 720, 95)
        a, b = {}, {}
        assert np.array_equal(cg._cast_heights(geo, x, y, angles, [], eye, None, a),
                              reference(geo, x, y, angles, [], eye, None, b))
        assert a == b
