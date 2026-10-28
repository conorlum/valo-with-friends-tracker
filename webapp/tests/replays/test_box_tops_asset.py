"""Learnt box tops in the height asset (W5b): an optional `box_tops` array, hashed only when it has rows, learnt by
the build from a map's cover labels, passed in by the height job, checked by the verifier, and an input of the
manifest."""

import hashlib
import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from test_control_heights import GEO, covered_rounds, level_walk, toy_assets, write_blobs  # noqa: E402
from test_control_height_job import _npz, _raw_parts, checks  # noqa: E402,F401
from test_height_inputs import GEOMETRY, REPLAYS  # noqa: E402

from app.control import geometry as cg  # noqa: E402
from app.control import height_build as hb  # noqa: E402
from app.control import height_job, height_verify  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.replays import height_inputs as hi  # noqa: E402
from tests.replays.control_toys import z_dm  # noqa: E402

# recorded on the code before this item, for the toy builds below (no cover given)
NO_COVER_BUILD_DIGEST = "b48acdae2528"
# the manifest digest holds HEIGHT_RULES_REVISION: re-recorded at 6 (was "3dcbe2c75de289ad" at 5); the build digest is unchanged
NO_COVER_MANIFEST_DIGEST = "be548d2a5bc53e15"

PIECE = (slice(150, 170), slice(300, 320))        # rows (y), columns (x) of the one cover piece


def cover():
    c = np.zeros((cg.PX, cg.PX), np.int16)
    c[PIECE] = 1
    return c


def box_player():
    """Walks to the piece's edge at ground level, then up onto it (1.2 m) and stays: one top stand."""
    return "B", [(0.0, 280, 160, 0, 0.0), (1.0, 299, 160, 0, 0.0), (1.25, 308, 160, 0, 1.2), (30.0, 308, 160, 0, 1.2)]


def rounds(with_box=True):
    return covered_rounds({9: box_player()} if with_box else None)


def as_json(report):
    return json.dumps(report, sort_keys=True, default=str)


def test_a_no_cover_build_and_manifest_keep_the_digests_they_had():
    sample = hb.build(covered_rounds({9: level_walk(320, 328, 6.0, side="B")}), GEO)
    assert sample.asset.box_tops is None and sample.asset.digest == NO_COVER_BUILD_DIGEST
    assert hi.digest(hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)) == NO_COVER_MANIFEST_DIGEST


def test_a_cover_build_learns_the_top_and_changes_nothing_else():
    plain = hb.build(rounds(), GEO)
    learnt = hb.build(rounds(), GEO, cover_px=cover())
    tops = learnt.asset.box_tops
    assert plain.asset.box_tops is None
    assert tops.dtype == np.int32 and tops.shape == (1, 3)
    x, y, z = tops[0]
    assert 300 <= x < 320 and 150 <= y < 170
    assert z == z_dm(1.2) - learnt.asset.origin_z
    for name in ("floors", "spread", "supported", "unresolved", "edges", "kind"):
        assert (getattr(plain.asset, name) == getattr(learnt.asset, name)).all(), name
    assert as_json(plain.report) == as_json(learnt.report)
    assert plain.asset.meta == learnt.asset.meta
    assert learnt.asset.digest != plain.asset.digest


def test_cover_with_no_top_stand_is_the_plain_asset():
    plain = hb.build(rounds(False), GEO)
    covered = hb.build(rounds(False), GEO, cover_px=cover())
    assert covered.asset.box_tops is None and covered.asset.digest == plain.asset.digest


def test_box_tops_round_trip_and_move_the_digest(tmp_path):
    learnt = hb.build(rounds(), GEO, cover_px=cover())
    hc.save_asset(tmp_path / "a.npz", learnt.asset)
    back = hc.load_asset(tmp_path / "a.npz")
    assert back.box_tops.dtype == np.int32 and (back.box_tops == learnt.asset.box_tops).all()
    assert back.digest == learnt.asset.digest
    moved = hb.build(rounds(), GEO, cover_px=cover())
    moved.asset.box_tops = moved.asset.box_tops + np.array([0, 0, 1], np.int32)
    assert moved.asset.digest != learnt.asset.digest


@pytest.mark.parametrize("empty", [None, np.zeros((0, 3), np.int32)])
def test_none_and_empty_tops_are_the_same_asset_and_the_same_file(tmp_path, empty):
    plain = hb.build(rounds(False), GEO).asset
    other = hb.build(rounds(False), GEO).asset
    other.box_tops = empty
    assert other.digest == plain.digest
    hc.save_asset(tmp_path / "a.npz", plain)
    hc.save_asset(tmp_path / "b.npz", other)
    with np.load(tmp_path / "a.npz") as a, np.load(tmp_path / "b.npz") as b:
        assert a.files == b.files and "box_tops" not in a.files
        assert all((a[k] == b[k]).all() for k in a.files)
    assert hc.load_asset(tmp_path / "b.npz").box_tops is None


def test_a_refinement_that_moves_the_origin_moves_the_tops_with_the_floors():
    from app.control import adaptive  # noqa: F401 - the shift lives there
    src = Path(adaptive.__file__).read_text(encoding="utf-8")
    assert "asset.box_tops[:, 2] += shift" in src


# ---------------------------------------------------------------- the manifest


def test_the_cover_mask_is_an_input_of_the_manifest():
    base = hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)
    covered = hi.manifest("Sunset", REPLAYS, {**GEOMETRY, "cover": "c" * 12}, "k" * 12)
    redrawn = hi.manifest("Sunset", REPLAYS, {**GEOMETRY, "cover": "d" * 12}, "k" * 12)
    assert "cover" not in base["geometry"] and covered["geometry"]["cover"] == "c" * 12
    assert len({hi.digest(base), hi.digest(covered), hi.digest(redrawn)}) == 3


def test_geometry_identity_names_the_cover_only_when_the_index_has_it(tmp_path):
    maps = tmp_path / "maps.json"
    maps.write_text(json.dumps({"M": {"xMultiplier": 1.0}}), encoding="utf-8")
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"maps": {"M": {"sight_sha": "s", "walk_sha": "w"}}}), encoding="utf-8")
    assert hi.geometry_identity("M", tmp_path, maps) == {"sight": "s", "walk": "w", "scale": 1.0}
    index.write_text(json.dumps({"maps": {"M": {"sight_sha": "s", "walk_sha": "w", "cover_sha": "c"}}}), encoding="utf-8")
    assert hi.geometry_identity("M", tmp_path, maps) == {"sight": "s", "walk": "w", "scale": 1.0, "cover": "c"}


# ---------------------------------------------------------------- the job and the verifier


def cover_assets(tmp_path):
    assets = toy_assets(tmp_path)
    mask = cover() > 0
    cg.write_mask_png(assets / "Ascent.cover.png", mask)
    sha = hashlib.sha256(np.packbits(mask).tobytes()).hexdigest()[:12]
    index = json.loads((assets / "index.json").read_text(encoding="utf-8"))
    index["maps"]["Ascent"]["cover_sha"] = sha
    (assets / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return assets


def test_the_cover_labels_come_from_the_committed_cover_png(tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "covered").mkdir()
    assert cg.load_cover_labels("Ascent", toy_assets(tmp_path / "plain")) is None
    labels = cg.load_cover_labels("Ascent", cover_assets(tmp_path / "covered"))
    assert labels.dtype == np.int16 and labels.shape == (cg.PX, cg.PX) and set(np.unique(labels)) == {0, 1}


def job(tmp_path, checks, with_box=True):
    assets = cover_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", rounds(with_box))
    out = height_job.run("Ascent", height_job.BlobDir(blobs, "Ascent"), asset_dir=assets, must_block=checks)
    return assets, out


def test_the_job_passes_the_maps_cover_labels_to_the_build(tmp_path, checks):
    assets, out = job(tmp_path, checks)
    parts = _raw_parts(out["asset"])
    assert parts["box_tops"].dtype == np.dtype("<i4") and parts["box_tops"].shape == (1, 3)
    seen = height_verify.describe(out["asset"], "Ascent", asset_dir=assets, must_block=checks)
    assert "error" not in seen and seen["digest"] == out["digest"] and seen["box_tops"] == 1


def test_the_job_on_a_map_without_cover_makes_no_tops(tmp_path, checks):
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", rounds())
    out = height_job.run("Ascent", height_job.BlobDir(blobs, "Ascent"), asset_dir=assets, must_block=checks)
    assert "box_tops" not in _raw_parts(out["asset"])
    assert "box_tops" not in height_verify.describe(out["asset"], "Ascent", asset_dir=assets, must_block=checks)


@pytest.mark.parametrize("damage", ["dtype", "shape", "pixel", "negative z", "off cover", "good"])
def test_the_verifier_checks_the_tops(tmp_path, checks, damage):
    assets, out = job(tmp_path, checks)
    parts = _raw_parts(out["asset"])
    tops = parts["box_tops"]
    if damage == "dtype":
        parts["box_tops"] = tops.astype("<i8")
    elif damage == "shape":
        parts["box_tops"] = tops.reshape(-1)
    elif damage == "pixel":
        parts["box_tops"] = np.array([[cg.PX, 160, 5]], "<i4")
    elif damage == "negative z":
        parts["box_tops"] = np.array([[308, 160, -1]], "<i4")
    elif damage == "off cover":
        parts["box_tops"] = np.array([[100, 100, 5]], "<i4")
    seen = height_verify.describe(_npz(parts), "Ascent", asset_dir=assets, must_block=checks)
    if damage == "good":
        assert "error" not in seen and seen["box_tops"] == 1
    else:
        assert "box_tops" in seen["error"], seen
