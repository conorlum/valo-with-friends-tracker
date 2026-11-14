"""Height brushes use strict, disjoint fine-cell masks, independent of tags."""

import base64
import hashlib
import json

import numpy as np
import pytest

from app.control import geometry as cg
from app.replays import height_inputs as hi


def write_paint(folder, document):
    (folder / "height_paint.json").write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture
def cells():
    mask = np.zeros((256, 256), dtype=bool)
    mask[0, 0] = mask[2, 3] = mask[17, 128] = mask[255, 255] = True
    return mask


def test_no_file_means_no_paint(tmp_path):
    assert cg.load_height_paint("Toy", tmp_path) == (None, None)


@pytest.mark.parametrize("maps", [{}, {"Toy": {}}, {"Other": {}}])
def test_no_map_or_brush_means_no_paint(tmp_path, maps):
    write_paint(tmp_path, {"maps": maps})
    assert cg.load_height_paint("Toy", tmp_path) == (None, None)


def test_ground_cells_and_canonical_sha_match_manifest(tmp_path, cells):
    entry = {"ground": cg.pack_paint(cells)}
    write_paint(tmp_path, {"maps": {"Toy": entry}})
    masks, sha = cg.load_height_paint("Toy", tmp_path)
    assert set(masks) == {"ground"}
    assert masks["ground"].dtype == np.bool_
    np.testing.assert_array_equal(masks["ground"], cells)
    expected = hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
    assert sha == expected and len(sha) == 12
    (tmp_path / "index.json").write_text(json.dumps({"maps": {
        "Toy": {"sight_sha": "s", "walk_sha": "w"}}}), encoding="utf-8")
    (tmp_path / "maps.json").write_text('{"Toy": {"xMultiplier": 0.00007}}', encoding="utf-8")
    assert hi.geometry_identity("Toy", tmp_path, tmp_path / "maps.json")["height_paint"] == sha


def test_disjoint_brushes_are_allowed(tmp_path, cells):
    other = np.zeros_like(cells)
    other[0, 1] = True
    write_paint(tmp_path, {"maps": {"Toy": {
        "open": cg.pack_paint(cells), "ground": cg.pack_paint(other), "top": cg.pack_paint(~(cells | other))}}})
    masks, sha = cg.load_height_paint("Toy", tmp_path)
    assert sha
    np.testing.assert_array_equal(masks["open"], cells)
    np.testing.assert_array_equal(masks["ground"], other)
    np.testing.assert_array_equal(masks["top"], ~(cells | other))


def test_overlap_is_rejected(tmp_path, cells):
    entry = {brush: cg.pack_paint(cells) for brush in ("open", "ground")}
    write_paint(tmp_path, {"maps": {"Toy": entry}})
    with pytest.raises(cg.GeometryError, match="overlap"):
        cg.load_height_paint("Toy", tmp_path)


@pytest.mark.parametrize("payload", [
    "", "bad base64!", "!" + base64.b64encode(bytes(8192)).decode() + "!",
    base64.b64encode(bytes(8191)).decode(), base64.b64encode(bytes(8193)).decode(), "é",
])
def test_bad_payload_is_rejected(tmp_path, payload):
    write_paint(tmp_path, {"maps": {"Toy": {"ground": payload}}})
    with pytest.raises(cg.GeometryError):
        cg.load_height_paint("Toy", tmp_path)


@pytest.mark.parametrize("text", [
    "not json", "null", "[]", "{}", '{"maps": []}', '{"maps": {"Toy": []}}',
    '{"maps": {"Toy": {"ground": null}}}', '{"maps": {"Toy": {"unknown": ""}}}',
    '{"maps": {}, "other": {}}',
])
def test_bad_document_is_rejected(tmp_path, text):
    (tmp_path / "height_paint.json").write_text(text, encoding="utf-8")
    with pytest.raises(cg.GeometryError):
        cg.load_height_paint("Toy", tmp_path)


def test_unreadable_file_is_rejected(tmp_path):
    (tmp_path / "height_paint.json").mkdir()
    with pytest.raises(cg.GeometryError):
        cg.load_height_paint("Toy", tmp_path)


def test_all_zero_payload_means_no_paint(tmp_path):
    write_paint(tmp_path, {"maps": {"Toy": {
        brush: cg.pack_paint(np.zeros((256, 256), dtype=bool)) for brush in ("open", "ground", "top")}}})
    assert cg.load_height_paint("Toy", tmp_path) == (None, None)


def test_geometry_has_unattached_height_paint_by_default():
    assert cg.Geometry.__dataclass_fields__["height_paint"].default is None
    assert cg.Geometry.__dataclass_fields__["height_paint_sha"].default is None
