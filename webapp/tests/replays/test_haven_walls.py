"""Owner-reviewed Haven walls, using the committed geometry and real LOS engine."""

import hashlib
import json

import numpy as np
import pytest
from PIL import Image

from app.control import geometry as cg
from app.control import height_build


OWNER_LINES = [
    ("5", (720, 612), (240, 660)),
    ("2", (470, 640), (380, 660)),
    ("3", (700, 465), (640, 465)),
    ("4", (700, 630), (760, 640)),
    ("7", (360, 510), (470, 510)),
    ("1 upper leaf", (600, 635), (500, 635)),
    ("1 lower leaf", (600, 655), (500, 655)),
]
TAG_IDS = {
    "cover": {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 15, 17, 18, 19,
              22, 23, 25, 28, 29, 30, 32, 33, 34, 35, 36, 37, 38},
    "seeover": {20, 21, 24, 26, 27, 31},
    "wall": set(range(41, 50)),
}
UNCHANGED = {
    "Sunset": ("e608a189a789", "ba2802b67c05"),
    "Fracture": ("c4d5aa47ca61", "61afa6aa6628"),
    "Summit": ("a8686d5a1bbd", "3f2a7cec58c7"),
}


@pytest.fixture(scope="module")
def haven():
    return cg.load_base_geometry("Haven")


@pytest.mark.parametrize("label,viewer,target", OWNER_LINES)
def test_owner_wall_lines_are_blocked(haven, label, viewer, target):
    assert not cg.los(haven, (*viewer, None), (*target, None)), label


def test_mid_door_channel_stays_clear(haven):
    assert cg.los(haven, (600, 645, None), (500, 645, None))


def test_reviewed_tags_and_inward_snapped_paint():
    maps = cg.load_tags()["maps"]
    entry = maps["Haven"]
    assert entry["cover_reviewed"] is True
    assert len(entry["tags"]) == 44
    assert {t["tag"] for t in entry["tags"]} == set(TAG_IDS)
    image = np.array(Image.open(cg.MINIMAP_DIR / "Haven.png").convert("RGBA"))
    _, candidates = cg.candidates(image, entry.get("params") or {})
    candidates = {c["id"]: c for c in candidates}
    for kind, ids in TAG_IDS.items():
        assert {t["id"] for t in entry["tags"] if t["tag"] == kind} == ids
    for tag in entry["tags"]:
        assert {k: tag[k] for k in ("id", "kind", "bbox", "px")} == candidates[tag["id"]]
    expected = np.zeros((cg.PAINT_GRID, cg.PAINT_GRID), bool)
    for x0, y0, x1, y1 in [(650, 495, 669, 539), (666, 595, 680, 610), (443, 629, 461, 664)]:
        expected[(y0 + 3) // 4:(y1 + 1) // 4, (x0 + 3) // 4:(x1 + 1) // 4] = True
    assert expected.sum() == 85
    assert np.array_equal(cg.unpack_paint(entry["cover_paint"])[::4, ::4], expected)
    assert hashlib.sha256(entry["cover_paint"].encode()).hexdigest().startswith("6690170d3850500f")
    assert all("thin_wall_exempt" not in e for e in maps.values())


@pytest.mark.parametrize("name,blocked", [
    ("Abyss", 0), ("Ascent", 2), ("Bind", 0), ("Corrode", 1), ("Haven", 0),
    ("Lotus", 2), ("Split", 1), ("Summit", 1), ("Sunset", 1),
])
def test_previously_passing_kill_line_gates(name, blocked):
    row = json.loads((cg.ASSET_DIR / "index.json").read_text(encoding="utf-8"))["maps"][name]
    # D10: Haven's gate excludes the wall tags; pin its rebuilt count separately from information-only walls.
    assert row["kill_lines"]["passes"]
    assert row["kill_lines"]["blocked"] == blocked
    if name == "Haven":
        assert blocked <= 2


@pytest.mark.parametrize("target", [(521, 604), (512, 684)])
def test_mid_door_joins_close_leaf_to_frame_slivers(haven, monkeypatch, target):
    # (518, 604) is already closed by thin padding without joins; (521, 604) isolates the upper join.
    viewer = (610, 645, None)
    assert not cg.los(haven, viewer, (*target, None))
    monkeypatch.setattr(cg, "WALL_JOIN_PX", 0)
    image = np.array(Image.open(cg.MINIMAP_DIR / "Haven.png").convert("RGBA"))
    masks = cg.masks(image, cg.load_tags()["maps"]["Haven"])
    scale = json.loads(cg.MAPS_JSON.read_text(encoding="utf-8"))["Haven"]["xMultiplier"]
    unjoined = cg.geometry_from_masks("Haven", masks.sight, masks.walk, scale)
    assert cg.los(unjoined, viewer, (*target, None)), "negative control: joins alone close this sliver"


@pytest.mark.parametrize("name,hashes", UNCHANGED.items())
def test_untouched_maps_keep_their_mask_bytes(name, hashes):
    for kind, expected in zip(("sight", "walk"), hashes):
        mask = cg.read_mask_png(cg.ASSET_DIR / f"{name}.{kind}.png")
        assert hashlib.sha256(np.packbits(mask).tobytes()).hexdigest()[:12] == expected


def test_owner_lines_are_in_the_permanent_height_gate(haven):
    path = cg.ASSET_DIR / "must_block.json"
    assert b"\r" not in path.read_bytes()
    stored = json.loads(path.read_text(encoding="utf-8"))["lines"]
    rows = []
    for label, viewer, target in OWNER_LINES:
        row = {"map": "Haven", "viewer": [*viewer, None], "target": [*target, None],
               "source": f"card #1, replay ee6e0ba7: owner line #{label}"}
        assert row in stored
        rows.append(row)
    result = height_build.must_block_check(rows, haven, "Haven")
    assert (result["lines"], result["checked"], result["blocked"], result["unchecked"]) == (7, 7, 7, 0)
    assert result["passes"]
    assert all(r["basis"] == "permanent_2d_wall" for r in result["results"])


# Pre-W1 committed masks: disabling padding and Haven's review must recover every old byte identity.
OLD_MASK_HASHES = {
    "Abyss": ("8342f6714685", "42d1d3f29a3c", None),
    "Ascent": ("09f5f6a85c9b", "c7b8d59538e6", "03d6b7c14536"),
    "Bind": ("8e9f1fc13fec", "59c0154fc89d", None),
    "Breeze": ("a112074736d2", "439038d9b82b", None),
    "Corrode": ("b25b161d4a72", "683601dd7bf6", None),
    "Fracture": ("c4d5aa47ca61", "61afa6aa6628", None),
    "Haven": ("4b33f5f8746f", "456ddf172cf3", None),
    "Icebox": ("748966afbe52", "87ebfd069462", None),
    "Lotus": ("39a88a11dd2f", "1bbf00db2ac5", None),
    "Pearl": ("68e221537cb1", "21d5594157a5", None),
    "Split": ("f6b62d344ccd", "87c9ddfd6702", None),
    "Summit": ("a8686d5a1bbd", "3f2a7cec58c7", "4ca7d497b029"),
    "Sunset": ("e608a189a789", "ba2802b67c05", "ca4caf841b49"),
}


@pytest.mark.parametrize("name,expected", OLD_MASK_HASHES.items())
def test_disabling_new_rules_recovers_original_mask_identities(monkeypatch, name, expected):
    monkeypatch.setattr(cg, "thin_padding", lambda wall, exempt: np.zeros_like(wall))
    entry = dict(cg.load_tags()["maps"].get(name, {}))
    if name == "Haven":
        entry.pop("tags")
        entry.pop("cover_paint")
        entry["cover_reviewed"] = False
    image = np.array(Image.open(cg.MINIMAP_DIR / f"{name}.png").convert("RGBA"))
    masks = cg.masks(image, entry)
    cover = cg.see_over_cover(image, entry)
    actual = tuple(hashlib.sha256(np.packbits(m).tobytes()).hexdigest()[:12]
                   for m in (masks.sight, masks.walk, cover))
    assert actual[:2] == expected[:2]
    assert actual[2] == expected[2] if expected[2] else not cover.any()
    if name == "Ascent":
        labels = cg.attach_cover(cg.load_base_geometry(name), cover, actual[2]).cover_px
        assert labels.max() == 18
        assert labels[538, 437] == labels[535, 441] == labels[537, 450] != 0
