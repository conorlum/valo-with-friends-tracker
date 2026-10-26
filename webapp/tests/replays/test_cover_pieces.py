"""Cover pieces survive the feature runtime's fresh geometry (stage 0d)."""
import numpy as np

from app.control import geometry as cg
from app.control.feature_runtime import prepare_geometry
from test_ascent_feature_integration import compiled_case


def test_the_fresh_archived_domain_keeps_the_cover_pieces():
    geo, *_ = compiled_case()                  # its archive's sight differs, so prepare builds a fresh map
    mask = np.zeros((cg.PX, cg.PX), bool)
    mask[100:110, 100:110] = True
    mask[110:120, 110:120] = True              # touches the first only at a corner: its own piece
    cg.attach_cover(geo, mask, "abc")
    prepared = prepare_geometry(geo)
    assert prepared is not geo
    assert prepared.cover_sha == "abc"
    assert prepared.cover_px is not None and prepared.cover_px.max() == 2
    assert np.array_equal(prepared.cover_px, geo.cover_px)
    assert prepared.see_over_mask(1).sum() == 100


def test_the_fresh_archived_domain_of_a_map_without_cover_has_none():
    geo, *_ = compiled_case()
    prepared = prepare_geometry(geo)
    assert prepared is not geo and prepared.cover_px is None and prepared.cover_sha is None
