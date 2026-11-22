"""Owner map review rules pinned from f241c31c and the approved W1 input.

All expectations are literals: the private review input is never needed by tests.
"""
import copy
import hashlib
import json

import numpy as np
import pytest
from PIL import Image

from app.control import geometry as cg, rope_ground

TAGS = cg.load_tags()["maps"]
INDEX = json.loads((cg.ASSET_DIR / "index.json").read_text(encoding="utf-8"))["maps"]
MUTABLE = ("tags", "cover_paint", "bullet_paint", "cover_reviewed")

EXPECTED = {'Abyss': {'cover': [1, 4, 5, 6, 9, 10, 15, 17, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28],
           'seeacross': [53, 59],
           'seeover': [2, 3, 11, 12, 13, 14, 16, 18],
           'wall': [33, 37, 39, 40, 43, 44, 48]},
 'Ascent': {'cover': [2, 4, 5, 6, 7, 8, 9, 11, 13, 14, 15, 16, 22, 23, 25, 26, 28, 29, 30, 32, 33, 34],
            'seeover': [3, 10, 12, 19, 20, 21, 24, 27],
            'wall': [36, 38, 41, 43, 45, 46, 48, 49]},
 'Bind': {'cover': [1, 2, 3, 6, 10, 11, 12, 15, 16, 18, 19, 20, 24, 25, 26],
          'seeover': [8, 9, 13, 17, 21, 22, 23],
          'wall': [31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 43, 44, 45, 46, 47]},
 'Breeze': {'cover': [1, 2, 3, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 19, 20],
            'seeover': [4, 5, 6, 18],
            'wall': [21, 22, 23, 24, 26, 27, 28, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41]},
 'Corrode': {'cover': [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 18, 20, 22, 26, 27, 28, 29, 30],
             'seeover': [10, 15, 16, 17, 19, 21, 23]},
 'Fracture': {'cover': [1, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 19, 21, 23, 24, 25, 26, 27],
              'seeover': [2, 3, 4, 17, 18],
              'wall': [28, 30, 32, 33, 36, 37, 41, 42, 44, 45, 46, 49, 50, 51, 53, 54]},
 'Lotus': {'cover': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 19, 20],
           'seeover': [11, 18],
           'wall': [28, 29, 30]},
 'Pearl': {'cover': [1, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 18, 20, 21, 22],
           'seeover': [2, 9, 16, 17, 19, 23, 24, 25, 26],
           'wall': [27, 28, 29, 30, 31, 38, 39, 40, 42, 45, 47, 48]},
 'Split': {'cover': [1, 2, 3, 6, 7, 8, 9, 10, 12, 14, 16, 17, 20, 21, 23, 24, 25, 26, 27, 29, 30, 31, 32],
           'seeover': [4, 5, 11, 13, 15, 19, 22, 28],
           'wall': [34, 38, 40, 41, 43, 44, 46, 48]},
 'Summit': {'cover': [5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 26, 28, 29, 30],
            'seeover': [1, 2, 3, 4, 25, 27, 31, 32, 33],
            'wall': [34, 35, 38, 39, 48, 49, 50, 51, 52]},
 'Sunset': {'cover': [1, 2, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18, 19, 20, 21, 22, 23, 25, 26],
            'seeover': [3, 5, 27, 28],
            'wall': [29, 34, 36]}}

PAINT_SHA16 = {'Abyss': {'cover_paint': '2811cd321f59d009', 'bullet_paint': 'edb1498006a3b946'},
 'Ascent': {'cover_paint': 'adf552c6d98492de', 'bullet_paint': 'ad46c5a0b70370c4'},
 'Bind': {'cover_paint': '64fb0e542a2d3570', 'bullet_paint': 'a2296aa7f3c558a5'},
 'Breeze': {'cover_paint': '49e751055750f4d3', 'bullet_paint': '8d599815c19d4d29'},
 'Corrode': {'cover_paint': '82d02934d94b3198', 'bullet_paint': '9294c90bbccff715'},
 'Fracture': {'cover_paint': None, 'bullet_paint': None},
 'Lotus': {'cover_paint': '2c5468ddaa6447ac', 'bullet_paint': '975245d250d85be3'},
 'Pearl': {'cover_paint': None, 'bullet_paint': None},
 'Split': {'cover_paint': '8fc63fcb97e25e82', 'bullet_paint': 'ffcc99f7820ce99b'},
 'Summit': {'cover_paint': '2fac327fa82c64bd', 'bullet_paint': 'c1f97ba3da216788'},
 'Sunset': {'cover_paint': '089ed9aff025856e', 'bullet_paint': 'd76816512a620d31'}}

MIXED_UNTAGGED = {'Abyss': [8],
 'Ascent': [17, 18, 31],
 'Bind': [4, 5, 7, 14],
 'Breeze': [13, 25],
 'Corrode': [24, 25, 35],
 'Fracture': [],
 'Lotus': [21],
 'Pearl': [],
 'Split': [18],
 'Summit': [6],
 'Sunset': [15, 24]}

MIXED_CELLS = {'Abyss': (8, {'cover_paint': [15567, 15568, 15569], 'bullet_paint': [15570, 15571, 15572]}),
 'Ascent': (31, {'cover_paint': [48479, 48480, 48481], 'bullet_paint': [48483, 48484, 48485]}),
 'Bind': (7, {'cover_paint': [22689, 22690, 22691], 'bullet_paint': [23969, 23970, 23971]}),
 'Breeze': (13, {'cover_paint': [28609, 28610, 28611], 'bullet_paint': [30159, 30160, 30161]}),
 'Corrode': (24, {'cover_paint': [47199, 47200, 47201], 'bullet_paint': [48225, 48226, 48227]}),
 'Lotus': (21, {'cover_paint': [50273, 50274, 50275], 'bullet_paint': [49249, 49250, 49505]}),
 'Split': (18, {'cover_paint': [46692, 46693, 46694], 'bullet_paint': [46696, 46697, 46698]}),
 'Summit': (6, {'cover_paint': [12825, 12826, 12827], 'bullet_paint': [15390, 15391, 15392]}),
 'Sunset': (15, {'cover_paint': [28716, 28717, 28718], 'bullet_paint': [28464, 28465, 28466]})}

BASE_PRESERVED = {'Abyss': 'aa3c64d94caa5c00731fa92b539cdf56bdf4af8ddc701e9a82012879a7b42342',
 'Ascent': '5a96ca4e5893cf8639deaa1b933d1acb84e8739260ec986457315a1f053cdebe',
 'Bind': '6e36e09ee59f1bf041574211a299522859a72e5524702a5371698c9f2e8e9a17',
 'Breeze': 'f2c6d978b419489010fc2eeaafe288efd3340b4ffb15c5eaaccc6e6327bb5dc4',
 'Corrode': '3de70088190639fde83ddcfec1137fe08895c3c463ea09c2db3d6eafee8a77b4',
 'Fracture': '4259bf1c4fdeee61cb26e718742c76ecb20b158d7f6af975e8581b66fc401ee8',
 'Lotus': '415ba022b42ff5b52b045f8e4410520822f112bbf1a3133a40dd84053b4d27d7',
 'Pearl': '2293f0fac8991b342a0345ca3880b5368d5711435212494c59c4b92e9060364d',
 'Split': 'c3f601d77ec5a6d17e25a96835dd3b90361be2332684b9bdd10639613131e5d0',
 'Summit': 'eabb0dccc2716129a09f0db02ff401266a29683c6b5e8bd100b646060f41c914',
 'Sunset': '3429e03403d389d6675c359b457ce2463129dc11567081f11e63f5f8358867c2'}

BASE_UNTOUCHED = {'Haven': '30d480ef68d9d5d1944209ce552ab1e7e7d3c8d31af2a34d4ee16ed3be6e43fe',
 'Icebox': '800542bdf69d78df8594b6663e808f9b95201b98a2393d23be7c97172bfac415'}

ICEBOX_MASK_SHA256 = {'sight': 'b2489f53aac7c04686bc96f968b5423207f2a66e8ae107dfcd38fd22d8688d86',
 'walk': '05871c7134598e03d34c4b5210218837836e90a2e727804a3beed55093ebbb25',
 'bullet': 'b2489f53aac7c04686bc96f968b5423207f2a66e8ae107dfcd38fd22d8688d86',
 'barrier': '83c3ca5abf2b2eeb385b711354cb1b0fdff124a00d1240887db4983e97ae5819'}

BASE_PASSING = ['Abyss', 'Ascent', 'Bind', 'Corrode', 'Haven', 'Lotus', 'Split', 'Summit', 'Sunset']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def rgba(name):
    return np.array(Image.open(cg.MINIMAP_DIR / f"{name}.png").convert("RGBA"))


@pytest.mark.parametrize("name", EXPECTED)
def test_owner_tags_and_paints(name):
    entry = TAGS[name]
    assert entry["cover_reviewed"] is True
    assert [t["id"] for t in entry["tags"]] == sorted(t["id"] for t in entry["tags"])
    assert {kind: [t["id"] for t in entry["tags"] if t["tag"] == kind]
            for kind in {t["tag"] for t in entry["tags"]}} == EXPECTED[name]
    _, found = cg.candidates(rgba(name), entry.get("params") or {})
    by_id = {c["id"]: c for c in found}
    for tag in entry["tags"]:
        assert tag["id"] not in MIXED_UNTAGGED[name]
        assert {k: tag[k] for k in ("id", "kind", "bbox", "px")} == by_id[tag["id"]]
        if tag["tag"] == "wall":
            assert tag["kind"] == "line"
        elif tag["tag"] in ("cover", "seeover"):
            assert tag["kind"] == "closed"
    for key, expected in PAINT_SHA16[name].items():
        if expected is None:
            assert key not in entry
        else:
            assert hashlib.sha256(entry[key].encode("utf-8")).hexdigest()[:16] == expected


@pytest.mark.parametrize("name", MIXED_CELLS)
def test_mixed_shape_paint_composes_into_geometry(name):
    entry, image = TAGS[name], rgba(name)
    cid, cells = MIXED_CELLS[name]
    labels, _ = cg.candidates(image, entry.get("params") or {})
    cover = cg.unpack_paint(entry["cover_paint"])
    seeover = cg.unpack_paint(entry["bullet_paint"])
    assert not (cover & seeover).any()
    for key, paint in (("cover_paint", cover), ("bullet_paint", seeover)):
        grid = paint[::4, ::4].ravel()
        assert grid[cells[key]].all()
        assert (paint & (labels == cid)).any()
    assert cg.masks(image, entry).cover[cover].all()
    assert cg.bullet_mask(image, entry)[seeover].all()


@pytest.mark.parametrize("name", EXPECTED)
def test_unreviewed_fields_are_preserved(name):
    entry = copy.deepcopy(TAGS[name])
    if name == "Abyss":
        # R1.1 permits only pruning/re-stamping these domains; retain the original pin.
        for route in entry["map_features"]["routes"]:
            if route["id"] in ROPE_BASE_SHA256:
                route["walking_domain"] = original_rope_domain(route)
    assert digest({k: v for k, v in entry.items() if k not in MUTABLE}) == BASE_PRESERVED[name]


@pytest.mark.parametrize("name", BASE_UNTOUCHED)
def test_haven_and_icebox_entries_are_preserved(name):
    assert digest(TAGS[name]) == BASE_UNTOUCHED[name]


@pytest.mark.parametrize("kind", ICEBOX_MASK_SHA256)
def test_icebox_masks_are_preserved(kind):
    assert hashlib.sha256((cg.ASSET_DIR / f"Icebox.{kind}.png").read_bytes()).hexdigest() == ICEBOX_MASK_SHA256[kind]


def test_icebox_has_no_cover():
    assert not (cg.ASSET_DIR / "Icebox.cover.png").exists()
    assert "cover_sha" not in INDEX["Icebox"]
    assert INDEX["Icebox"]["cover_reviewed"] is False


def test_base_passing_maps_still_pass_with_wall_diagnostics():
    passing = [n for n, row in INDEX.items() if row.get("kill_lines") and row["kill_lines"]["passes"]]
    assert passing == BASE_PASSING
    for row in INDEX.values():
        if row.get("kill_lines"):
            assert row["kill_lines"]["passes"]
            assert row["kill_lines_with_walls"] is not None
    assert EXPECTED["Abyss"]["seeacross"] == [53, 59]
# R1.1: origin/main domains before requalification; literal rows/edges removed by
# the owner's >50% permanent-walk rule. Restoring them must reproduce the base pin.
ROPE_COUNTS = {'route-10': (195, 1282, 195, 1282),
 'route-12': (127, 700, 127, 700),
 'route-4': (368, 2560, 350, 2474),
 'route-8': (276, 1688, 276, 1688)}
ROPE_BASE_SHA256 = {'route-10': '4c06eebe11aabe854f57b6c1b8277e8e777ea5abb71cf4e23784cb3730816b69',
 'route-12': 'e606c4adee143d34588b42e45a4d2652d878c41d9b702c83293d318989a10805',
 'route-4': '19459d0cca582f4904d41bf4e17afd214b3aa71632c87b573406f0884bc0102c',
 'route-8': '4d959deeffc851854245ecada0768d26d7b9c64ce8f409907ce77369b0a5a9c0'}
ROPE_REMOVED = {'route-4': {'coarse': [[1845, [130], 1], [1846, [130], 1], [1973, [130], 1], [1974, [130], 1],
                        [1975, [130], 1], [2101, [130], 1], [2102, [130], 1], [2106, [101], 1],
                        [2229, [130], 1], [2234, [101], 1], [2357, [130], 1], [2358, [130], 1],
                        [2485, [130], 1], [2486, [130], 1], [2487, [130], 1], [2613, [90], 1],
                        [2614, [90], 1], [2616, [90], 1]],
             'edges': [[1845, 0, 1846, 0, 0], [1845, 0, 1973, 0, 0], [1845, 0, 1974, 0, 0],
                       [1846, 0, 1845, 0, 0], [1846, 0, 1973, 0, 0], [1846, 0, 1974, 0, 0],
                       [1846, 0, 1975, 0, 0], [1973, 0, 1845, 0, 0], [1973, 0, 1846, 0, 0],
                       [1973, 0, 1974, 0, 0], [1973, 0, 2101, 0, 0], [1973, 0, 2102, 0, 0],
                       [1974, 0, 1845, 0, 0], [1974, 0, 1846, 0, 0], [1974, 0, 1973, 0, 0],
                       [1974, 0, 1975, 0, 0], [1974, 0, 2101, 0, 0], [1974, 0, 2102, 0, 0],
                       [1975, 0, 1846, 0, 0], [1975, 0, 1974, 0, 0], [1975, 0, 2102, 0, 0],
                       [2101, 0, 1973, 0, 0], [2101, 0, 1974, 0, 0], [2101, 0, 2102, 0, 0],
                       [2101, 0, 2229, 0, 0], [2102, 0, 1973, 0, 0], [2102, 0, 1974, 0, 0],
                       [2102, 0, 1975, 0, 0], [2102, 0, 2101, 0, 0], [2102, 0, 2229, 0, 0],
                       [2106, 0, 2234, 0, 0], [2229, 0, 2101, 0, 0], [2229, 0, 2102, 0, 0],
                       [2229, 0, 2357, 0, 0], [2229, 0, 2358, 0, 0], [2234, 0, 2106, 0, 0],
                       [2234, 0, 2235, 0, 1], [2234, 0, 2362, 0, 1], [2235, 0, 2234, 0, 0],
                       [2357, 0, 2229, 0, 0], [2357, 0, 2358, 0, 0], [2357, 0, 2485, 0, 0],
                       [2357, 0, 2486, 0, 0], [2358, 0, 2229, 0, 0], [2358, 0, 2357, 0, 0],
                       [2358, 0, 2485, 0, 0], [2358, 0, 2486, 0, 0], [2358, 0, 2487, 0, 0],
                       [2362, 0, 2234, 0, 0], [2485, 0, 2357, 0, 0], [2485, 0, 2358, 0, 0],
                       [2485, 0, 2486, 0, 0], [2486, 0, 2357, 0, 0], [2486, 0, 2358, 0, 0],
                       [2486, 0, 2485, 0, 0], [2486, 0, 2487, 0, 0], [2486, 0, 2615, 1, 0],
                       [2487, 0, 2358, 0, 0], [2487, 0, 2486, 0, 0], [2487, 0, 2615, 1, 0],
                       [2613, 0, 2614, 0, 0], [2613, 0, 2741, 0, 0], [2613, 0, 2742, 0, 0],
                       [2614, 0, 2613, 0, 0], [2614, 0, 2615, 0, 0], [2614, 0, 2741, 0, 0],
                       [2614, 0, 2742, 0, 0], [2614, 0, 2743, 0, 0], [2615, 0, 2614, 0, 0],
                       [2615, 0, 2616, 0, 0], [2615, 1, 2486, 0, 0], [2615, 1, 2487, 0, 0],
                       [2616, 0, 2615, 0, 0], [2616, 0, 2617, 0, 0], [2616, 0, 2743, 0, 0],
                       [2616, 0, 2744, 0, 0], [2616, 0, 2745, 0, 0], [2617, 0, 2616, 0, 0],
                       [2741, 0, 2613, 0, 0], [2741, 0, 2614, 0, 0], [2742, 0, 2613, 0, 0],
                       [2742, 0, 2614, 0, 0], [2743, 0, 2614, 0, 0], [2743, 0, 2616, 0, 0],
                       [2744, 0, 2616, 0, 0], [2745, 0, 2616, 0, 0]],
             'fine': []}}


def original_rope_domain(route):
    domain = copy.deepcopy(route["walking_domain"])
    removed = ROPE_REMOVED.get(route["id"], {})
    for key in ("coarse", "fine", "edges"):
        rows = {json.dumps(row): row for row in domain[key] + removed.get(key, [])}
        domain[key] = sorted(rows.values())
    domain["walk_sha"] = "42d1d3f29a3c"
    return domain


ENABLED_ROPE_DOMAINS = [
    (name, route["id"])
    for name, entry in TAGS.items()
    for route, _ in rope_ground.domains_for(
        entry.get("map_features", {}))
]


@pytest.mark.parametrize("name,route_id", ENABLED_ROPE_DOMAINS)
def test_committed_enabled_rope_domains_validate_permanent_walk(name, route_id):
    from app.control import rope_ground

    geo = cg.load_geometry(name, load_features=False, height_mode="flat")
    route = next(r for r in TAGS[name]["map_features"]["routes"] if r["id"] == route_id)
    assert rope_ground.validate_domain(geo, route["walking_domain"])


@pytest.mark.parametrize("route_id", ROPE_COUNTS)
def test_requalified_rope_domains_are_base_subsets(route_id):
    assert set(ENABLED_ROPE_DOMAINS) == {("Abyss", rid) for rid in ROPE_COUNTS}
    route = next(r for r in TAGS["Abyss"]["map_features"]["routes"] if r["id"] == route_id)
    domain = route["walking_domain"]
    original = original_rope_domain(route)
    assert digest(original) == ROPE_BASE_SHA256[route_id]
    before_rows, before_edges, after_rows, after_edges = ROPE_COUNTS[route_id]
    assert len(original["coarse"]) + len(original["fine"]) == before_rows
    assert len(original["edges"]) == before_edges
    assert len(domain["coarse"]) + len(domain["fine"]) == after_rows
    assert len(domain["edges"]) == after_edges
    for key in ("coarse", "fine", "edges"):
        assert {json.dumps(row) for row in domain[key]} <= {json.dumps(row) for row in original[key]}
        assert not {json.dumps(row) for row in domain[key]} & {
            json.dumps(row) for row in ROPE_REMOVED.get(route_id, {}).get(key, [])}
    assert domain["source_height_sha"] == original["source_height_sha"] == "f89fd35a74da"
    geo = cg.load_geometry("Abyss", load_features=False, height_mode="flat")
    assert domain["walk_sha"] == hashlib.sha256(np.packbits(geo.walk_px).tobytes()).hexdigest()[:12]


def test_committed_abyss_flat_features_compile_and_prepare(monkeypatch):
    import base64
    from app.control import features as cf, rope_ground, route_runtime
    from app.control.feature_runtime import prepare_geometry
    from app.replays import map_feature_artifacts as fa, map_feature_inputs as fi, map_feature_sources
    from app.services import control_feature_artifacts as archive

    raw = (cg.ASSET_DIR / "tags.json").read_bytes()
    geo = cg.load_geometry("Abyss", load_features=False, height_mode="flat")
    scale = json.loads(cg.MAPS_JSON.read_text(encoding="utf-8"))["Abyss"]["xMultiplier"]
    base = map_feature_sources.base_snapshot("Abyss", cg.ASSET_DIR, TAGS["Abyss"], scale)
    inputs = fi.identify_features("Abyss", "flat", fi.capture_source_snapshot("Abyss", raw), base)
    direct = cf.compile_artifact(geo, inputs, "0" * 40)
    quiet_bundles = {b["id"] for b in TAGS["Abyss"]["map_features"]["bundles"]
                     if b.get("enabled") and b.get("runtime_consumer") == "quiet_rope_v1"}
    assert inputs.key.height_digest == "flat" and geo.heights is None
    assert quiet_bundles <= set(direct.manifest["active_bundles"])
    assert not quiet_bundles.intersection(direct.manifest["pending"])

    # Exercise the service's preparation contract without a DB or child process.
    stored = []
    monkeypatch.setattr(archive, "find_artifact", lambda db, key: None)
    def compile_here(mode, payload):
        request = fi.read_json(payload)
        assert mode == request["mode"] == "compile" and request["height"] is None
        child_inputs = fi.FeatureInput(fi.FeatureKey(**request["inputs"]["key"]),
            base64.b64decode(request["inputs"]["canonical_inputs"]), b"", "")
        child_geo = cf.geometry_from_feature_inputs(child_inputs, None)
        return fa.encode_artifact(cf.compile_artifact(child_geo, child_inputs, "0" * 40))
    def store(db, artifact, *, height_bytes):
        assert height_bytes is None
        stored.append(artifact)
        return artifact
    monkeypatch.setattr(archive, "run_feature_child", compile_here)
    monkeypatch.setattr(archive, "store_artifact", store)
    prepared = archive.prepare_artifact(None, inputs, None)
    assert stored == [prepared] and prepared.digest == direct.digest
    geo.features, geo.features_sha = prepared, prepared.digest
    walking = prepare_geometry(geo)
    assert walking.movement_only and walking.height_sha is None
    assert geo.heights is None
    owners = {r["owner"] for r, _ in rope_ground.domains_for(TAGS["Abyss"]["map_features"])}
    arcs, reasons = route_runtime.readiness(walking, TAGS["Abyss"]["map_features"], owners)
    assert not reasons
    assert {a.route for a in arcs} == set(ROPE_COUNTS)
