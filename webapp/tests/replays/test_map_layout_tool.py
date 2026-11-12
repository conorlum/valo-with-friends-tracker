"""The map layout tool's fit, diff and position count (scripts/map_layout_fit.py), on toy masks."""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import map_layout_fit as fit  # noqa: E402

SIZE = 256


def _ours() -> np.ndarray:
    """An asymmetric L-ish shape with a notch, so rotation is identifiable."""
    m = np.zeros((SIZE // 2, SIZE // 2), bool)
    m[30:100, 40:60] = True
    m[80:100, 40:110] = True
    m[30:45, 60:75] = True
    return np.kron(m, np.ones((2, 2), bool))


def _picture_of(ours: np.ndarray, rot_back: int, scale: float) -> np.ndarray:
    """A picture mask that fit() must map onto ours with rotation (4 - rot_back) % 4 and the given scale."""
    ys, xs = np.nonzero(ours)
    crop = ours[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    img = Image.fromarray(crop.astype(np.uint8) * 255).rotate(90 * rot_back, expand=True)
    img = img.resize((round(img.width / scale), round(img.height / scale)), Image.NEAREST)
    pic = np.zeros((img.height + 10, img.width + 10), bool)
    pic[5:5 + img.height, 5:5 + img.width] = np.array(img) > 0
    return pic


def test_fit_recovers_rotation_and_scale():
    ours = _ours()
    pic = _picture_of(ours, rot_back=3, scale=1.3)
    res = fit.fit_mask(pic, ours, size=SIZE, scales=np.arange(1.0, 1.8, 0.01))
    assert res["rot"] == 1
    assert abs(res["scale"] - 1.3) < 0.01
    assert res["iou"] > 0.95
    assert set(res) >= {"rot", "scale", "tx", "ty", "iou"}


def test_diff_lists_added_and_removed_regions():
    ours = _ours()
    new = ours.copy()
    new[5:25, 5:25] = True            # floor in the picture only
    new[170:190, 100:130] = False     # floor in ours only (inside the doubled L)
    regions = fit.diff_regions(ours, new, min_px=20)
    added = [r for r in regions if r["kind"] == "added"]
    removed = [r for r in regions if r["kind"] == "removed"]
    assert len(added) == 1 and len(removed) == 1
    assert added[0]["bbox"] == (5, 5, 24, 24)
    assert 380 <= added[0]["area"] <= 400   # the opening trims the corners
    assert removed[0]["bbox"] == (100, 170, 129, 189)


def test_positions_counted_inside_regions_only():
    ours = _ours()
    new = ours.copy()
    new[5:25, 5:25] = True
    regions = fit.diff_regions(ours, new, min_px=20)
    positions = [(10, 10), (20, 20), (15, 6), (50, 50), (100, 100), (30, 10)]
    counts = fit.count_positions(regions, positions)
    assert counts == [3]
