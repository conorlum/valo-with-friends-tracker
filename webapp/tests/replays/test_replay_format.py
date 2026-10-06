"""JSON format v1: delta encoding, segments, blobs, recipe, size budget."""

import gzip
import json

import pytest

from app.replays import format as fmt


def test_delta_round_trip():
    values = [4121, 4130, 4127, 4127, 5000]
    encoded = fmt.delta_encode(values)
    assert encoded == [4121, 9, -3, 0, 873]
    assert fmt.delta_decode(encoded) == values
    assert fmt.delta_encode([]) == [] and fmt.delta_decode([]) == []


def test_yaw_deltas_take_the_shortest_arc():
    yaws = [358, 2, 350, 10, 180]
    encoded = fmt.encode_yaw_deltas(yaws)
    assert encoded == [358, 4, -12, 20, 170]
    assert fmt.decode_yaw_deltas(encoded) == yaws


def test_segment_round_trip_on_the_native_grid():
    segment = fmt.encode_segment(1.25, [10, 12, 15], [20, 20, 19], [90, 91, 359])
    assert fmt.decode_segment(segment, hz=4) == [(1.25, 10, 20, 90), (1.5, 12, 20, 91), (1.75, 15, 19, 359)]


def test_segment_arrays_must_match():
    with pytest.raises(fmt.FormatError):
        fmt.encode_segment(0.0, [1, 2], [1], [1, 2])
    with pytest.raises(fmt.FormatError):
        fmt.encode_segment(0.0, [], [], [])


def test_a_segment_with_heights_round_trips_and_old_readers_see_four_values():
    segment = fmt.encode_segment(1.25, [10, 12, 15], [20, 20, 19], [90, 91, 359], z=[30, 30, 27])
    assert segment["z"] == [30, 0, -3]
    assert fmt.decode_segment_z(segment, hz=4) == [(1.25, 10, 20, 90, 30), (1.5, 12, 20, 91, 30),
                                                   (1.75, 15, 19, 359, 27)]
    assert fmt.decode_segment(segment, hz=4) == [(1.25, 10, 20, 90), (1.5, 12, 20, 91), (1.75, 15, 19, 359)]


def test_a_segment_without_heights_decodes_as_before_with_no_z():
    segment = fmt.encode_segment(1.25, [10, 12], [20, 20], [90, 91])
    assert "z" not in segment, "a missing height is a missing key, never 0"
    assert fmt.decode_segment_z(segment, hz=4) == [(1.25, 10, 20, 90, None), (1.5, 12, 20, 91, None)]
    assert fmt.decode_segment(segment, hz=4) == [(1.25, 10, 20, 90), (1.5, 12, 20, 91)]


def test_a_segments_heights_must_cover_every_sample():
    with pytest.raises(fmt.FormatError):
        fmt.encode_segment(0.0, [1, 2], [1, 2], [1, 2], z=[5])
    with pytest.raises(fmt.FormatError):
        fmt.decode_segment_z({"t0": 0.0, "u": [1, 1], "v": [1, 1], "yaw": [0, 0], "z": [5]}, hz=4)


def test_the_recipe_is_revision_12():
    assert fmt.CONDENSE_REVISION == 13 and fmt.FORMAT_VERSION == 1
    assert ".c13.f1." in fmt.recipe("2b66c65a7b116154e18e", "abcdef0123456789")


def _blob(**extra):
    blob = {"v": 1, "round": 1, "map": "Ascent", "hz": 16, "t_start": 0.0, "t_end": 1.0, "players": [],
            "tracks": {}, "alive": {}, "kills": [], "plant": None, "defuse": None, "util": []}
    blob.update(extra)
    return blob


def test_blob_round_trip_is_deterministic():
    blob = _blob(tracks={"0": [fmt.encode_segment(0.0, [1], [2], [3])]})
    first, second = fmt.encode_blob(blob), fmt.encode_blob(json.loads(json.dumps(blob)))
    assert first == second, "the same blob must always give the same bytes (Stage 3 byte-identity)"
    assert fmt.decode_blob(first) == blob


@pytest.mark.parametrize("version", sorted(fmt.SUPPORTED_VERSIONS))
def test_every_supported_version_decodes(version):
    assert fmt.decode_blob(fmt.encode_blob(_blob(v=version)))["v"] == version


def test_an_unsupported_version_is_refused_both_ways():
    with pytest.raises(fmt.FormatError):
        fmt.encode_blob(_blob(v=99))
    data = gzip.compress(json.dumps(_blob(v=99)).encode())
    with pytest.raises(fmt.FormatError):
        fmt.decode_blob(data)


def test_unknown_util_kinds_are_ignored():
    blob = _blob(util=[{"k": "flash", "t": 1.0, "by": 0}, {"k": "future_kind", "t": 2.0, "by": 1, "x": 5}])
    blob = fmt.decode_blob(fmt.encode_blob(blob))
    assert fmt.known_util(blob) == []
    assert fmt.known_util(blob, frozenset({"flash"})) == [{"k": "flash", "t": 1.0, "by": 0}]


def test_recipe_shape():
    assert fmt.recipe("2b66c65a7b116154e18e", "abcdef0123456789") == \
        f"2b66c65a7b11.c{fmt.CONDENSE_REVISION}.f{fmt.FORMAT_VERSION}.aabcdef01"


def test_assets_revision_ignores_line_endings(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "maps.json").write_bytes(b"{\n}\n")
    (tmp_path / "data" / "agents.json").write_bytes(b"{}\n")
    first = fmt.assets_revision(tmp_path)
    (tmp_path / "data" / "maps.json").write_bytes(b"{\r\n}\r\n")
    assert fmt.assets_revision(tmp_path) == first
    (tmp_path / "data" / "agents.json").write_bytes(b'{"Wushu": "Jett"}\n')
    assert fmt.assets_revision(tmp_path) != first


def test_size_report_budget():
    small = {n: b"x" * 1000 for n in range(1, 25)}
    report = fmt.size_report(small)
    assert report == {"rounds": 24, "total_bytes": 24000, "p95_bytes": 1000, "max_bytes": 1000, "fits_budget": True}
    one_big = {**small, 25: b"x" * (fmt.ROUND_BUDGET_P95_BYTES + 1)}
    assert fmt.size_report(one_big)["fits_budget"] is True, "one outlier round stays under p95"
    many_big = {n: b"x" * (fmt.ROUND_BUDGET_P95_BYTES + 1) for n in range(1, 25)}
    assert fmt.size_report(many_big)["fits_budget"] is False
    assert fmt.size_report({})["fits_budget"] is True
