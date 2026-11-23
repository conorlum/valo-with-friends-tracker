"""Committed spot validation and deterministic, database-free proposals."""
import hashlib
import json

import numpy as np
import pytest

from app.services import plant_spots


def _spot(**changes):
    return {"id": "spot-1", "name": "Default", "u": 4000, "v": 5000, "r": 100, **changes}


def _write(tmp_path, layouts, version=1):
    path = tmp_path / "plant_spots.json"
    path.write_text(json.dumps({"version": version, "layouts": layouts}), encoding="utf-8")
    return path


def _blobs(sizes):
    rng = np.random.default_rng(42)
    centres = [(1000 + i % 7 * 1000, 2000 + i // 7 * 3000) for i in range(len(sizes))]
    points = np.concatenate([rng.normal(centre, 3, size=(size, 2))
                             for centre, size in zip(centres, sizes)])
    return points, centres


def test_three_blobs_give_three_spots_in_uv_units():
    points, centres = _blobs([30, 40, 50])
    spots = plant_spots.propose(points.tolist(), 0.02)
    assert len(spots) == 3
    assert len({spot["id"] for spot in spots}) == 3
    for spot, centre in zip(spots, reversed(centres)):
        assert set(spot) == {"id", "name", "u", "v", "r"}
        assert spot["name"] == ""  # The owner must name proposals before export.
        assert (spot["u"], spot["v"]) == pytest.approx(centre, abs=2)
        assert spot["r"] == pytest.approx(75)
    json.dumps(spots)  # Ready to embed in the local naming page.


def test_fourteen_blobs_keep_only_the_ten_largest():
    points, centres = _blobs(range(40, 54))
    spots = plant_spots.propose(points, 0.02)
    assert len(spots) == 10
    for spot, centre in zip(spots, reversed(centres[4:])):
        assert (spot["u"], spot["v"]) == pytest.approx(centre, abs=2)


def test_uniform_noise_gives_no_spots():
    points = np.array([(u, v) for u in range(500, 10000, 500)
                       for v in range(500, 10000, 500)])
    assert plant_spots.propose(points, 0.02) == []


@pytest.mark.parametrize("points", [[], np.empty((0, 2))])
def test_empty_gives_no_spots(points):
    assert plant_spots.propose(points, 0.02) == []


@pytest.mark.parametrize("extent_m, expected_radius_m", [(0, 1.5), (4, 3.6), (15, 6)])
def test_radius_uses_member_percentile_and_metre_clamps(extent_m, expected_radius_m):
    # A connected chain checks DBSCAN expansion beyond one epsilon neighbourhood.
    scale = 0.02
    offsets = np.linspace(-extent_m, extent_m, 121) / scale
    points = np.column_stack((5000 + offsets, np.full(121, 5000)))
    spots = plant_spots.propose(points, scale)
    assert len(spots) == 1
    assert spots[0]["u"] == pytest.approx(5000)
    assert spots[0]["r"] * scale == pytest.approx(expected_radius_m)


def test_density_minimum_includes_self_and_rounds_three_percent_up():
    assert len(plant_spots.propose([[5000, 5000]] * 5, 0.02)) == 1
    assert plant_spots.propose([[5000, 5000]] * 4, 0.02) == []
    # 167 plants require ceil(5.01) = 6 neighbours: a five-point blob is noise.
    noise = [(u, v) for u in range(200, 9000, 500) for v in range(200, 9000, 500)][:162]
    points = [[9900, 9900]] * 5 + noise
    assert plant_spots.propose(points, 0.02) == []
    assert len(plant_spots.propose([[9900, 9900]] + points, 0.02)) == 1


def test_previously_visited_noise_becomes_a_border_member():
    # At 0.02 m/UV epsilon is 75 UV. The first point is not itself core.
    points = [[4900, 5000], [4970, 5000]] + [[5000, 5000]] * 4
    spots = plant_spots.propose(points, 0.02)
    assert len(spots) == 1
    assert spots[0]["u"] == pytest.approx(np.mean(np.array(points)[:, 0]))


def test_proposals_are_repeatable_including_equal_size_ties():
    points, _ = _blobs([20, 20, 20])
    first = plant_spots.propose(points, 0.02)
    assert plant_spots.propose(points, 0.02) == first
    shuffled = plant_spots.propose(points[::-1], 0.02)
    assert [s["id"] for s in shuffled] == [s["id"] for s in first]
    for actual, expected in zip(shuffled, first):
        for field in ("u", "v", "r"):
            assert actual[field] == pytest.approx(expected[field])


def test_ascent_metres_per_uv_matches_hand_computation():
    # maps.json xMultiplier = 0.00007; 0.00007 * 10000 * 100 = 70.
    assert plant_spots.m_per_uv("Ascent") == pytest.approx(1 / 70)


def test_load_preserves_layout_site_and_spot_shape(tmp_path):
    layouts = {"Ascent": {"A": [_spot()], "B": [_spot()]},
               "Ascent_14_0": {"A": [_spot(u=0, v=10000, r=10000)]}}
    assert plant_spots.load(_write(tmp_path, layouts)) == layouts


def test_load_accepts_ten_spots(tmp_path):
    spots = [_spot(id=f"spot-{i}") for i in range(10)]
    assert plant_spots.load(_write(tmp_path, {"Ascent": {"A": spots}}))["Ascent"]["A"] == spots


def test_load_rejects_eleven_spots(tmp_path):
    spots = [_spot(id=f"spot-{i}") for i in range(11)]
    with pytest.raises(ValueError, match="10"):
        plant_spots.load(_write(tmp_path, {"Ascent": {"A": spots}}))


def test_load_rejects_duplicate_ids_on_one_site(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        plant_spots.load(_write(tmp_path, {"Ascent": {"A": [_spot(), _spot()]}}))


@pytest.mark.parametrize("changes", [
    {"name": ""}, {"name": "   "}, {"name": None}, {"id": ""},
    {"u": -1}, {"u": 10001}, {"v": -1}, {"v": 10001},
    {"u": float("nan")}, {"v": float("inf")}, {"u": True}, {"u": "5000"},
    {"r": 0}, {"r": -1}, {"r": 10001}, {"r": float("nan")}, {"r": float("inf")},
])
def test_load_rejects_invalid_spot_fields(tmp_path, changes):
    with pytest.raises(ValueError):
        plant_spots.load(_write(tmp_path, {"Ascent": {"A": [_spot(**changes)]}}))


@pytest.mark.parametrize("layouts", [[], {"Ascent": []}, {"Ascent": {"A": {}}},
                                    {"Ascent": {"A": [{"id": "spot-1"}]}}])
def test_load_rejects_malformed_structure(tmp_path, layouts):
    with pytest.raises(ValueError):
        plant_spots.load(_write(tmp_path, layouts))


def test_load_rejects_unknown_version(tmp_path):
    with pytest.raises(ValueError, match="version"):
        plant_spots.load(_write(tmp_path, {}, version=2))


def test_committed_file_loads():
    assert plant_spots.load() == {}


def test_digest_tracks_default_file_bytes_without_a_stale_cache(tmp_path, monkeypatch):
    path = _write(tmp_path, {})
    monkeypatch.setattr(plant_spots, "_PATH", path)
    original = plant_spots.digest()
    assert original == hashlib.sha256(path.read_bytes()).hexdigest()
    assert plant_spots.digest() == original
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert plant_spots.digest() != original


@pytest.mark.parametrize("points, scale", [([[1, 2]], 0), ([[1, 2]], -1),
                                           ([[1, 2]], float("inf")), ([[1, 2]], float("nan")),
                                           ([1, 2], 0.02), ([[1, float("nan")]], 0.02)])
def test_propose_rejects_invalid_inputs(points, scale):
    with pytest.raises(ValueError):
        plant_spots.propose(points, scale)
