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
# Card #6 (box-door-review) reviewed the other 11 maps; their entries are pinned
# in test_owner_map_reviews.py. Icebox alone remains unreviewed.
UNCHANGED = {
    "Icebox": ('6fa95e68b769', '87ebfd069462'),
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


def wall_geometry(name):
    """The map's geometry built from the wall mask alone (padded walls, no cover): what 'permanent' means."""
    image = np.array(Image.open(cg.MINIMAP_DIR / f"{name}.png").convert("RGBA"))
    m = cg.masks(image, cg.load_tags()["maps"].get(name, {}))
    scale = json.loads(cg.MAPS_JSON.read_text(encoding="utf-8"))[name]["xMultiplier"]
    return cg.geometry_from_masks(name, m.wall, m.walk, scale)


def test_owner_lines_are_in_the_permanent_height_gate(haven):
    path = cg.ASSET_DIR / "must_block.json"
    assert b"\r" not in path.read_bytes()
    stored = json.loads(path.read_text(encoding="utf-8"))["lines"]
    rows = []
    for label, viewer, target in OWNER_LINES:
        row = {"map": "Haven", "viewer": [*viewer, None], "target": [*target, None],
               "source": f"card #1, replay ee6e0ba7: owner line #{label}"}
        if label == "7":
            # Blocked only by cover (B-site centre box): heights can see over it, so the permanent gate must not list it.
            assert row not in stored
            continue
        assert row in stored
        rows.append(row)
    assert [r for r in stored if r["map"] == "Haven"] == rows
    result = height_build.must_block_check(rows, haven, "Haven")
    assert (result["lines"], result["checked"], result["blocked"], result["unchecked"]) == (6, 6, 6, 0)
    assert result["passes"]
    assert all(r["basis"] == "permanent_2d_wall" for r in result["results"])


def _z_null_lines():
    stored = json.loads((cg.ASSET_DIR / "must_block.json").read_text(encoding="utf-8"))["lines"]
    out = []
    for i, line in enumerate(stored):
        if line["viewer"][2] is None and line["target"][2] is None:
            marks = []
            if line["map"] == "Ascent" and line["viewer"] == [424, 282, None]:
                # Found by this test (round 2): the wall mask alone does not block it; only cover does. Kept in the
                # gate per the owner's ruling (report, don't remove); a later review decides.
                marks = [pytest.mark.xfail(reason="Ascent case 1 line is blocked by cover, not by the wall mask alone",
                                           strict=True)]
            out.append(pytest.param(line, id=f"{line['map']}-{i}", marks=marks))
    return out


@pytest.mark.parametrize("line", _z_null_lines())
def test_every_z_null_must_block_line_is_blocked_by_the_wall_mask_alone(line):
    # The gate's proof is "a permanent 2D wall": cover (which heights can see over) must not be what blocks it.
    geo = wall_geometry(line["map"])
    assert not cg.los(geo, tuple(line["viewer"]), tuple(line["target"])), line["source"]


# Known limitation (2026-11-19, branch review round 2): cast and los never count samples at steps 0 and 2, so a
# viewer standing within ~2 px of a padded wall face gets at most one counted hit and the ray passes. Pinned as it
# is, not as it should be: a later engine change (with a CONTROL_REVISION bump) that fixes it must trip this test.
# Owner line #3's wall, toward (640, 465).
LIMITATION_LOS_TRUE_EAST = [670, 671, 672]       # viewer x, looking west to (640, 465): sees through
LIMITATION_LOS_FALSE_EAST = [673, 674, 675, 680]
LIMITATION_LOS_TRUE_WEST = [665, 666, 667]       # viewer x, looking east to (700, 465): sees through
LIMITATION_LOS_FALSE_WEST = [664, 663, 660]


def test_viewer_hugging_a_padded_wall_still_sees_through_it_known_limitation(haven):
    for x in LIMITATION_LOS_TRUE_EAST:
        assert cg.los(haven, (x, 465, None), (640, 465, None)), x
    for x in LIMITATION_LOS_FALSE_EAST:
        assert not cg.los(haven, (x, 465, None), (640, 465, None)), x
    for x in LIMITATION_LOS_TRUE_WEST:
        assert cg.los(haven, (x, 465, None), (700, 465, None)), x
    for x in LIMITATION_LOS_FALSE_WEST:
        assert not cg.los(haven, (x, 465, None), (700, 465, None)), x


# Disabling card #1 padding and Haven review recovers these pre-card-1 identities.
# Card #6 replaced the other 11 maps' tags and paint; their pre-card-1 identities
# are pinned at commit f241c31c, rather than here.
OLD_MASK_HASHES = {
    "Haven": ("4b33f5f8746f", "456ddf172cf3", None),
    "Icebox": ("748966afbe52", "87ebfd069462", None),
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
