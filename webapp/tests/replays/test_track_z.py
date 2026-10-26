"""RoundInputs.track_z: each sample's raw position-z in metres, on every map (flat or heights), NaN where a
segment has no z. `heights` and `height()` stay what they were: heights maps only, above the map's origin."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from control_toys import height_blob, open_hall, standing, toy_heights, z_dm  # noqa: E402

from app.control import engine as ce  # noqa: E402

PLAYERS = {0: ("A", [(0.0, 150, 204, 0, 1.0), (4.0, 200, 204, 0, 3.0)]),
           5: standing(300, 204, 0.5, "B")}


def blob_with_a_zless_segment():
    blob = height_blob(PLAYERS, t_end=5.0)
    extra = height_blob({6: ("B", [(0.0, 300, 120, 0, 2.0), (4.0, 320, 120, 0, 2.0)])}, t_end=5.0)
    seg = dict(extra["tracks"]["6"][0])
    seg_no_z = {k: v for k, v in seg.items() if k != "z"}
    blob["tracks"]["6"] = [seg, {**seg_no_z, "t0": seg["t0"] + len(seg["u"]) / blob["hz"]}]
    blob["players"].append({**blob["players"][0], "slot": 6, "side": "B"})
    return blob


def test_track_z_is_the_raw_z_in_metres_on_a_flat_map():
    blob = height_blob(PLAYERS, t_end=5.0)
    rnd = ce.RoundInputs(blob, open_hall())
    z = rnd.track_z[5]
    assert np.allclose(z, z_dm(0.5) / 10.0) and len(z) == len(rnd.tracks[5][0])
    assert rnd.track_z[0][0] == pytest.approx(z_dm(1.0) / 10.0)
    assert rnd.height(0, 0.0) is None and rnd.height(5, 1.0) is None
    assert rnd.heights == {} and not [k for k in rnd.missing if "height" in k]


def test_a_segment_without_z_is_nan_for_its_samples_only():
    blob = blob_with_a_zless_segment()
    rnd = ce.RoundInputs(blob, open_hall())
    n = len(blob["tracks"]["6"][0]["u"])
    z = rnd.track_z[6]
    assert len(z) == n + len(blob["tracks"]["6"][1]["u"])
    assert not np.isnan(z[:n]).any() and np.isnan(z[n:]).all()
    assert not np.isnan(rnd.track_z[0]).any()


@pytest.mark.slow
def test_flat_and_heights_maps_give_the_same_track_z_and_heights_keep_their_origin():
    blob = blob_with_a_zless_segment()
    geo = toy_heights("TrackZ", [(96, 96, 416, 296)])
    flat, tall = ce.RoundInputs(blob, open_hall()), ce.RoundInputs(blob, geo)
    assert flat.track_z.keys() == tall.track_z.keys()
    for s in flat.track_z:
        assert np.array_equal(flat.track_z[s], tall.track_z[s], equal_nan=True)
    origin = geo.heights.origin_z                    # world decimetres
    for s, h in tall.heights.items():
        assert np.allclose(h, tall.track_z[s] - origin / 10.0, equal_nan=True)
    assert flat.heights == {}
