"""Learnt box tops in the height asset (W5b): an optional `box_tops` array, hashed only when it has rows, learnt by
the build from a map's cover labels, passed in by the height job, checked by the verifier, and an input of the
manifest."""

import hashlib
import io
import json
import math
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
# the manifest digest holds HEIGHT_RULES_REVISION: re-recorded at 8 (was "31495d88d6e6e4f5" at 7,
# "be548d2a5bc53e15" at 6, "3dcbe2c75de289ad" at 5);
# the build digest is unchanged
NO_COVER_MANIFEST_DIGEST = "1f82b0eb900c99e7"

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


def test_a_no_cover_build_and_manifest_keep_the_digests_they_had(monkeypatch):
    sample = hb.build(covered_rounds({9: level_walk(320, 328, 6.0, side="B")}), GEO)
    assert sample.asset.box_tops is None and sample.asset.digest == NO_COVER_BUILD_DIGEST
    assert hi.digest(hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)) == NO_COVER_MANIFEST_DIGEST
    monkeypatch.setattr("app.replays.control_format.HEIGHT_RULES_REVISION", 7)
    assert hi.digest(hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)) == "31495d88d6e6e4f5"


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
    assert as_json(plain.report) == as_json({k: v for k, v in learnt.report.items() if not k.startswith('box_')})
    assert learnt.report['box_conflicts'] == 0
    assert learnt.report['box_counts']['teach'] == 6
    assert 'box_conflicts' not in learnt.asset.meta and 'box_conflicts' not in plain.report
    assert plain.asset.meta == learnt.asset.meta
    assert learnt.asset.digest != plain.asset.digest


def test_cover_build_passes_world_sides_snap_and_near_floors(monkeypatch):
    from app.control import box_tops as bt

    labels = cover()
    probe = (325, 160)  # six pixels beyond the piece's last pixel (319, 160)
    neighbour = (329, 160)  # its connected leaf starts at (328, 160), 3 px from the probe
    seen = {}
    judge, floors_near = bt.judge, bt.floors_near

    def sparse_leaf(geo, asset):
        assert geo.cover_px is labels
        seen['asset'] = asset
        # Isolate the wiring: this leaf has no floor at the probe, only at its neighbour.
        connected = {(neighbour[0] // 4, neighbour[1] // 4): (z_dm(0.0),)}
        leaf = lambda px: connected.get((px[0] // 4, px[1] // 4), ())
        seen['plain_leaf'] = leaf
        return leaf

    def near(leaf, px, radius):
        seen['radius'] = radius
        return floors_near(leaf, px, radius)

    def record(stands, ground, leaf_floor, beside=None, **kwargs):
        seen.update(kwargs)
        seen['near_floors'] = leaf_floor(probe)
        return judge(stands, ground, leaf_floor, beside, **kwargs)

    monkeypatch.setattr(hb, 'box_leaf_floor', sparse_leaf)
    monkeypatch.setattr(bt, 'floors_near', near)
    monkeypatch.setattr(bt, 'judge', record)
    learnt = hb.build(covered_rounds({9: box_player()}), GEO, cover_px=labels)
    asset = seen['asset']
    floors = bt.piece_floors(labels, asset.floors, fine_cells=asset.fine_cells,
                             fine_floors=asset.fine_floors, walk_px=GEO.walk_px)
    assert asset.origin_z != 0
    assert seen['sides'] == {p: tuple(f + asset.origin_z for f in s.values())
                             for p, s in floors.sides.items()}
    assert set(seen['sides'][1]) == {z_dm(0.0)}
    assert callable(seen['snap'])
    x, y = seen['snap'](1, probe)
    assert labels[y, x] == 1
    assert seen['plain_leaf'](probe) == ()
    assert seen['plain_leaf'](neighbour) == (z_dm(0.0),)
    assert seen['plain_leaf']((328, 160)) == (z_dm(0.0),)
    assert seen['near_floors'] == (z_dm(0.0),)
    assert seen['radius'] == math.ceil(1.0 / GEO.m_per_px)
    assert learnt.asset.box_tops[:, 2].tolist() == [z_dm(1.2) - asset.origin_z]


def test_a_top_is_learnt_without_a_climb():
    """A player who starts the round on the box (no rise before it) still shows its top: it is a clear step."""
    start_on = ("B", [(0.0, 308, 160, 0, 1.2), (30.0, 308, 160, 0, 1.2)])
    tops = hb.build(covered_rounds({9: start_on}), GEO, cover_px=cover()).asset.box_tops
    assert tops is not None and tops[:, 2].tolist() == [z_dm(1.2) - z_dm(0.0)]


def test_touching_stands_build_tops_on_the_piece_and_pass_verification(tmp_path, checks):
    touching = ('B', [(0., 299, 160, 0, 1.2), (30., 299, 160, 0, 1.2)])
    assets = cover_assets(tmp_path)
    blobs = write_blobs(tmp_path / 'blobs', covered_rounds({9: touching}))
    out = height_job.run('Ascent', height_job.BlobDir(blobs, 'Ascent'), asset_dir=assets, must_block=checks)
    tops = _raw_parts(out['asset']).get('box_tops')
    assert round(GEO.m_per_px, 2) == 0.14
    assert tops is not None and len(tops) > 0
    assert (cover()[tops[:, 1], tops[:, 0]] == 1).all()
    assert tops[:, 0].tolist() == [300]
    assert tops[:, 2].tolist() == [z_dm(1.2) - z_dm(0.0)]
    seen = height_verify.describe(out['asset'], 'Ascent', asset_dir=assets, must_block=checks)
    assert 'error' not in seen and seen['box_tops'] == len(tops) and seen['digest'] == out['digest']


def test_a_stand_at_floor_height_inside_the_outline_is_no_top():
    at_floor = ("B", [(0.0, 280, 160, 0, 0.0), (1.0, 308, 160, 0, 0.0), (30.0, 308, 160, 0, 0.0)])
    plain = hb.build(covered_rounds({9: at_floor}), GEO)
    covered = hb.build(covered_rounds({9: at_floor}), GEO, cover_px=cover())
    assert covered.asset.box_tops is None and covered.asset.digest == plain.asset.digest


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


def test_tops_are_judged_after_real_refinement_with_a_nonzero_origin_shift(monkeypatch):
    from app.control import box_tops as bt, height_refinement as hr
    parent = GEO.cell_of_px(292, 160)
    previous = hr.history(GEO.name, [parent])
    original = hr.analyze

    def lower_child(*args):
        evidence = original(*args)
        for child in evidence['children']:
            child['floors_world_dm'] = [(z_dm(-1), 0)]
            child['kind'] = hc.KIND_STANDS
        return evidence

    monkeypatch.setattr(hr, 'analyze', lower_child)
    judge = bt.judge
    seen = []

    def after_refine(stands, ground, leaf_floor, beside=None, **kwargs):
        # The connected child is in the new world frame and is visible to the judge.
        assert z_dm(-1) in leaf_floor((292, 160))
        seen.append(True)
        return judge(stands, ground, leaf_floor, beside, **kwargs)

    monkeypatch.setattr(bt, 'judge', after_refine)
    sample = hb.build(rounds(), GEO, cover_px=cover(), refinement_history=previous)
    assert sample.asset.origin_z == z_dm(-1) and seen == [True]
    for x, y, z in sample.asset.box_tops:
        assert cover()[y, x] == 1 and z + sample.asset.origin_z == z_dm(1.2)


def test_conflicts_are_world_records_in_meta_and_report_links_and_round_trip(tmp_path):
    outside = ('B', [(0., 297, 160, 0, 3.), (30., 297, 160, 0, 3.)])
    # Keep the full ground corpus, with one outside stand below either vote threshold.
    source = covered_rounds()
    source[0] = covered_rounds({9: outside})[0]
    sample = hb.build(source, GEO, cover_px=cover())
    conflicts = sample.asset.meta['box_conflicts']
    assert sample.report['box_conflicts'] == sample.report['box_counts']['conflict'] == len(conflicts) == 1
    assert sample.asset.box_tops is None
    first = conflicts[0]
    # Encoded toy coordinates truncate to x=296, y=159: four pixels (0.56 m) off the piece.
    assert first == dict(match='match-0', round=1, slot=9, t0=0., t1=30., z=z_dm(3.) / 10,
                         candidates=[[1, [], round(4 * GEO.m_per_px, 2)]], px=[296, 159])
    lines = hb.report_lines('Toy', sample.report)
    assert any('1 box conflicts' in line for line in lines)
    assert f"  box conflict: piece(s) 1 ({4 * GEO.m_per_px:.2f} m) z {first['z']} m 30.0 s https://valowithfriendstracker.onrender.com/replays/match-0?round=1&t=0.0" in lines
    hc.save_asset(tmp_path / 'conflicts.npz', sample.asset)
    back = hc.load_asset(tmp_path / 'conflicts.npz')
    assert back.meta['box_conflicts'] == conflicts and back.digest == sample.asset.digest


def test_report_reads_legacy_and_distance_candidates_together():
    report = dict(hb.build(covered_rounds(), GEO).report)
    record = dict(match='toy-match', round=1, slot=9, t0=0., t1=1., z=3., px=[299, 159])
    report.update(box_conflicts=2, box_conflict_records=[
        dict(record, candidates=[[5, [4.0]], [6, []]]),
        dict(record, candidates=[[15, [4.0], 0.58], [6, [], 0.7]])])
    lines = hb.report_lines('Toy', report)
    assert any('piece(s) 5, 6 z' in line for line in lines)
    assert any('piece(s) 15 (0.58 m), 6 (0.70 m) z' in line for line in lines)


def test_box_stands_get_the_same_blackouts_and_platform_filter(monkeypatch):
    from tests.replays.control_toys import toy_ability
    monkeypatch.setattr(hb.hm, 'blackouts', lambda blob, geo: {9: [(0., 30.)]})
    start_on = ('B', [(0., 308, 160, 0, 1.2), (30., 308, 160, 0, 1.2)])
    blacked = hb.build(covered_rounds({9: start_on}), GEO, cover_px=cover())
    assert blacked.asset.box_tops is None
    assert blacked.report['box_counts']['blackout'] == 6
    assert blacked.report['box_counts']['teach'] == 0
    monkeypatch.setattr(hb.hm, 'blackouts', lambda blob, geo: {})
    inputs = rounds()
    for _, _, blob in inputs:
        blob['util'] = [toy_ability('Thorne', 'E_Wall_Fortifying', 308, 160, 1, t=0., t1=30., kind='GameObject')]
    platform = hb.build(inputs, GEO, cover_px=cover())
    assert platform.asset.box_tops is None
    assert platform.report['box_counts']['platform'] >= 6
    assert platform.report['box_counts']['teach'] == 0


def test_connected_leaf_floor_requires_an_edge_endpoint_at_the_matching_level():
    asset = hb.build(covered_rounds(), GEO).asset
    cell = GEO.cell_of_px(299, 160)
    other = cell - 1
    asset.floors.reshape(-1, hc.MAX_FLOORS)[cell] = [0, 20, -1]
    asset.edges = np.array([[cell, 0, other, 0, hc.EDGE_STEP]], np.int32)
    leaf = hb.box_leaf_floor(GEO, asset)
    assert leaf((299, 160)) == (asset.origin_z,)
    asset.edges = np.array([[cell, 1, other, 0, hc.EDGE_STEP]], np.int32)
    assert hb.box_leaf_floor(GEO, asset)((299, 160)) == (asset.origin_z + 20,)


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
