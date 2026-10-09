"""The map-features half of the tagger (scripts/control_tagger_core.js `TaggerCore.Features`; plan
docs/superpowers/plans/2026-10-04-map-interaction-tagger.md): its pure model agrees with the Python
contract modules on shared fixtures. Unlike test_control_tagger.py these tests fail, not skip, without Node:
a parity check that silently skips proves nothing."""

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.replays import map_feature_schema as ms
from app.replays import map_feature_state as fs

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
CORE = WEBAPP / "scripts" / "control_tagger_core.js"
FIXTURES = HERE.parent / "fixtures" / "control" / "map_features"
NODE = shutil.which("node") or (r"C:\Program Files\nodejs\node.exe" if Path(r"C:\Program Files\nodejs\node.exe").is_file() else None)
CASES = json.loads((FIXTURES / "reducer_cases.json").read_text(encoding="utf-8"))

PRELUDE = """
  const T = require(process.argv[1]);
  const F = T.Features;
  let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
    const p = JSON.parse(input);
    process.stdout.write(JSON.stringify(run(p)));
  });
"""


def run_node(body: str, payload):
    assert NODE, "node is required for the map-features parity tests"
    completed = subprocess.run([NODE, "-e", PRELUDE + body, str(CORE)], input=json.dumps(payload), capture_output=True,
                               text=True, encoding="utf-8", timeout=120, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize('preset', ['vertical_rope', 'zipline'])
def test_review_route_creation_and_unplaced_access_remain_editable(preset):
    got = run_page('''function run(p) {
      const page = openPage(p.data, {map:"Ascent"}), A = page.api, F = page.F;
      A.createPreset(p.preset);
      const route = A.fe.Ascent.mf.routes[A.fe.Ascent.mf.routes.length - 1];
      A.commit(F.setField(A.fe.Ascent.mf, route.id, ["access"], {sites:[{id:"boarding",uv:null}]}), true);
      return {dirty:A.fe.Ascent.dirty, sites:A.fe.Ascent.mf.routes.slice(-1)[0].access.sites,
              issues:page.el("featIssues").innerHTML};
    }''', {'data': page_data(future=False), 'preset': preset})
    assert got['dirty'] and got['sites'] == [{'id': 'boarding', 'uv': None}]
    assert 'off_map' in got['issues'] or 'invalid_geometry' in got['issues']


@pytest.mark.parametrize('shape', [
    {'type': 'point', 'uv': None}, {'type': 'point', 'uv': [10001, 0]},
    {'type': 'polygon', 'uv': [[0, 0], [1, 1], [-1, 2]]},
    {'type': 'paint', 'cells': '!'}, {'type': 'polyline', 'uv': [[0, 0], [1, 1]], 'width': -1},
])
def test_review_preview_invalid_geometry_matches_compiler(shape):
    from map_feature_artifact_toys import source_case, geometry_case
    from app.control.features import _place_shape
    mf = source_case()['map_features']
    mf['features'][0]['states'][0]['footprint'] = shape
    got = run_node('''function run(p) {return F.previewPlacement(p.mf,
      {walk:Array(128*128).fill(1),flat:true})["feature-1"].reasons;}''', {'mf': mf})
    assert _place_shape(geometry_case(flat=True), shape, 'test').reasons[0]['code'] in {r['code'] for r in got}


@pytest.mark.parametrize('pixels', [0, 16, 32, 33, 64])
@pytest.mark.parametrize('restore', [False, True])
def test_review_preview_walkability_reduces_pixels_after_private_restoration(pixels, restore):
    import numpy as np
    from map_feature_artifact_toys import source_case
    from app.control.geometry import geometry_from_masks, pack_paint
    from app.control.features import bundle_status
    mf = source_case()['map_features']
    # One authored paint cell adds 16 pixels inside an otherwise partly occupied engine cell.
    walk = np.zeros((1024, 1024), bool)
    block = walk[320:328, 320:328]
    for i in range(pixels):
        block[i // 8, i % 8] = True
    if restore:
        paint = np.zeros((256, 256), bool)
        paint[81, 81] = True
        mf['features'][0]['base_edits'] = {'potential_ground': {'type': 'paint', 'cells': pack_paint(paint)}}
    geo = geometry_from_masks('Summit', ~walk, walk, 7e-5, [])
    expected = bundle_status(geo, mf, consumers={'test'})['bundle-1'].publishable
    got = run_node('''function run(p) {const px = Uint8Array.from(p.walk);
      const counts = new Uint8Array(128*128);
      px.forEach((v,i) => {if(v) counts[Math.floor(Math.floor(i/1024)/8)*128+Math.floor((i%1024)/8)]++;});
      const walk = F.walkableCells ? F.walkableCells(px) : Uint8Array.from(counts,c => c>32 ? 1 : 0);
      return F.previewPlacement(p.mf,{walk:walk,walk_px:px,flat:true})["feature-1"].ok;}''',
      {'mf': mf, 'walk': walk.astype(int).ravel().tolist()})
    assert got == expected


REDUCER = """
  function run(p) {
    return p.cases.map(c => {
      try { return {trace: F.run(p.features[c.feature], c.events, c.until)}; }
      catch (e) { return {error: e.name}; }
    });
  }
"""


def test_reducer_traces_match_python_exactly():
    extra = [{"name": "until", "feature": "compound_door", "events": [{"t": 1, "kind": "switch"}], "until": 2},
             {"name": "bad event", "feature": "compound_door", "events": [{"t": 1, "kind": "teleport"}]}]
    cases = CASES["cases"] + extra
    got = run_node(REDUCER, {"features": CASES["features"], "cases": cases})
    for case, js in zip(cases, got):
        feature = CASES["features"][case["feature"]]
        try:
            expected = {"trace": fs.run(feature, case["events"], case.get("until"))}
        except fs.FeatureStateError:
            expected = {"error": "FeatureStateError"}
        assert js == expected, case["name"]


# ---- W12: model operations, validation, digests, the canonical catalogue

CATALOGUE = json.loads((FIXTURES / "catalogue_full.json").read_text(encoding="utf-8"))
MF = CATALOGUE["maps"]["Ascent"]["map_features"]


def pairs(items):
    return sorted({(i["where"], i["code"]) for i in items})


DIGESTS = """
  function run(p) {
    return {sha: F.sha256Hex("abc"), digests: p.values.map(v => F.digest(v)), runtime: F.runtimeDigest(p.mf),
            canon: F.canonicalJson(p.values[1])};
  }
"""


def test_digests_match_python_byte_for_byte():
    values = [MF, {"b": [1, 2.5, 2.0, -0.0], "a": "Café ☕ \U0001F642 \x7f   \"q\" \\ \n", "c": None, "d": True},
              {"z": {"y": [{"x": 1e3}]}}]
    got = run_node(DIGESTS, {"values": values, "mf": MF})
    assert got["sha"] == hashlib.sha256(b"abc").hexdigest()
    assert got["digests"] == [ms.digest(v) for v in values]
    assert got["runtime"] == ms.runtime_digest(MF)


def test_tag_canonical_v2_matches_literal_python_and_browser_vectors():
    cases = json.loads((FIXTURES / 'canonical_v2.json').read_text(encoding='utf-8'))
    got = run_node('''function run(p) { return p.map(c => {
      try { return {ascii: F.tagCanonicalJson(c.input)}; }
      catch(e) { return {error: true}; }
    }); }''', cases)
    for case, actual in zip(cases, got):
        if 'error' in case:
            assert actual == {'error': True}
        else:
            assert actual == {'ascii': case['ascii']}


def test_deprecated_floor_selectors_survive_export_but_not_runtime_validation():
    from map_feature_artifact_toys import source_case
    mf = source_case()['map_features']
    mf['features'][0]['floors'] = ['floor-99']
    mf['floors'] = [{'id': 'not-a-floor', 'z_band': ['broken']}]
    got = run_node('''function run(p) { return {source: p, runtime: F.runtimeProjection(p),
      report: F.validate(p, [], [])}; }''', mf)
    assert got['source'] == mf
    assert got['report']['errors'] == []
    assert got['runtime'] == ms.runtime_projection(mf)
    assert 'floors' not in got['runtime']


def mutations():
    """(name, map_features) cases whose validation reports differ, for the JS/Python parity check."""
    out = [("full", MF), ("atlantis", CATALOGUE["maps"]["Atlantis"]["map_features"])]

    def case(name, fn):
        mf = copy.deepcopy(MF)
        fn(mf)
        out.append((name, mf))

    case("dup id", lambda m: m["triggers"].append(copy.deepcopy(m["triggers"][0])))
    case("dangling", lambda m: m["triggers"][0]["targets"].append({"feature": "feature-99", "event": "switch"}))
    case("bad state", lambda m: m["features"][0].update(initial_state="ajar"))
    case("bad guard", lambda m: m["features"][0]["transitions"][0].update(guard={"eval": "x"}))
    case("reducer event", lambda m: m["features"][0]["transitions"][0].update(event="motion_complete"))
    case("bad policy", lambda m: m["features"][0]["transitions"][0].update(mid_motion="sometimes"))
    case("off map", lambda m: m["features"][0]["states"][0]["footprint"]["uv"].__setitem__(0, [4000, 12000]))
    case("short paint", lambda m: m["features"][0]["states"][0]["footprint"].update(type="paint", cells="AAAA"))
    case("bounds order", lambda m: m["features"][0]["states"][0]["sight"][0]["bounds"]["bottom"].update(value=5))
    case("no floor", lambda m: m["features"][0]["states"][0]["sight"][0]["bounds"].pop("floor"))
    case("bad unit", lambda m: m["features"][0]["transitions"][0]["motion"]["duration"].update(unit="ms"))
    case("one endpoint", lambda m: m["routes"][0]["endpoints"].pop())
    case("bad direction", lambda m: m["routes"][0]["directions"][0].update(to="c"))
    case("no entry", lambda m: m["routes"][0]["directions"][0].pop("entry"))
    case("z band", lambda m: m["floors"][0].update(z_band=[3, 1]))
    case("no range", lambda m: m["triggers"][2].update(geometry={"type": "point", "uv": [7000, 7000]}))
    case("bad id", lambda m: m.update(features=[{"id": "trigger-1"}]))
    case("version", lambda m: m.update(version=2))
    case("warnings", lambda m: (m["triggers"][0].update(targets=[]), m["routes"][0]["endpoints"][1].update(floor="floor-1"),
                                m["features"][1]["rotation"].update(pivot=None), m["triggers"][2].update(geometry=None, range={"status": "unresolved"}),
                                m["features"][2].update(initial_state=None)))
    case("access sites", lambda m: m["routes"][1].update(access={"sites": [{"id": "m", "uv": [2000, 7900]}]}))
    case("unresolved guard", lambda m: m["features"][0]["transitions"][2].update(guard={"all": [{"unresolved": "?"}]}))
    case("route states", lambda m: m["routes"][0].update(states=["nope"]))
    return out


VALIDATE = """
  function run(p) { return p.cases.map(c => F.validate(c.mf, c.seed, c.specials)); }
"""


def test_validation_matches_python_on_every_case():
    specials = [{"a": [1000, 8000], "b": [3000, 8000], "one_way": False}]
    cases = [{"name": n, "mf": mf, "seed": ms.checklist_seed("Ascent"), "specials": specials} for n, mf in mutations()]
    summit = ms.empty()
    for _ in range(2):
        fid, summit = ms.allocate(summit, "feature")
        summit["features"].append({"id": fid, "category": "drop_doors", **ms.preset("drop_door")})
    cases.append({"name": "summit counts", "mf": summit, "seed": ms.checklist_seed("Summit"), "specials": []})
    got = run_node(VALIDATE, {"cases": cases})
    for case, js in zip(cases, got):
        rep = ms.validate(case["mf"], "Ascent" if case["name"] != "summit counts" else "Summit", case["specials"])
        assert pairs(js["errors"]) == pairs(rep.errors), case["name"]
        assert pairs(js["warnings"]) == pairs(rep.warnings), case["name"]
    assert any(js["errors"] for js in got) and any(js["warnings"] for js in got)


MODEL = """
  function run(p) {
    const out = {};
    let mf = F.emptyMf();
    const ids = {};
    for (const name of p.presetNames) {
      const r = F.create(mf, p.presets[name], "My " + name, {category: name}, p.routes[name] || null);
      mf = r.mf; ids[name] = r;
    }
    out.created = mf;
    let t = F.addObject(mf, "trigger", {type: "switch", geometry: {type: "point", uv: [10, 10]}, floor: {status: "unresolved"}});
    mf = F.link(t.mf, t.id, ids.switch_door.id, "switch");
    let s = F.addObject(mf, "trigger", {type: "shoot", geometry: {type: "point", uv: [20, 20]}});
    mf = F.link(F.link(s.mf, s.id, ids.switch_door.id, "shoot"), s.id, ids.breakable.id, "destroy");
    mf = F.setField(mf, ids.breakable.id, ["parent"], ids.switch_door.id);
    out.linked = mf;
    out.refs = F.referrers(mf, ids.switch_door.id);
    const refused = F.remove(mf, ids.switch_door.id);
    out.refused = {refused: refused.refused, dangling: refused.dangling, same: JSON.stringify(refused.mf) === JSON.stringify(mf)};
    out.cascade = F.remove(mf, ids.switch_door.id, {cascade: true});
    out.cascadeRope = F.remove(mf, ids.vertical_rope.id, {cascade: true});
    out.relinked = F.remove(mf, ids.switch_door.id, {relink: ids.drop_door.id});
    let h = F.history(mf);
    h = F.historyPush(h, out.cascade.mf);
    h = F.historyPush(h, F.rename(h.present, ids.drop_door.id, "Mid drop-door"));
    const undone = F.undo(F.undo(h));
    out.undoRestores = JSON.stringify(undone.present) === JSON.stringify(mf);
    out.redoAgain = JSON.stringify(F.redo(undone).present) === JSON.stringify(out.cascade.mf);
    out.dupPlain = F.duplicate(mf, ids.switch_door.id);
    out.dupExternal = F.duplicate(mf, ids.switch_door.id, {keepExternal: true});
    out.dupRope = F.duplicate(mf, ids.vertical_rope.id);
    out.ids = Object.fromEntries(Object.entries(ids).map(([k, v]) => [k, v.id]));
    out.renamedDigest = F.runtimeDigest(F.rename(mf, ids.switch_door.id, "Other")) === F.runtimeDigest(mf);
    return out;
  }
"""


def placed(mf):
    """Structural errors other than "endpoint not placed yet" (a new route's endpoints are unplaced until the
    user clicks them; a draft may be incomplete, an export may not)."""
    return [e for e in ms.validate(mf).errors if e["code"] != "missing_endpoint"]


def test_model_operations_keep_identity_and_references():
    presets = {n: ms.preset(n) for n in ms.PRESETS}
    routes = {n: ms.route_template(k) for n, k in ms.ROUTE_PRESET.items()}
    got = run_node(MODEL, {"presetNames": list(ms.PRESETS), "presets": presets, "routes": routes})
    ids = got["ids"]
    created = got["created"]
    assert [f["id"] for f in created["features"]] == [ids[n] for n in ms.PRESETS]
    assert {r["owner"] for r in created["routes"]} == {ids["zipline"], ids["vertical_rope"], ids["teleporter"]}
    assert len({o["id"] for k in ("features", "routes") for o in created[k]}) == len(created["features"]) + len(created["routes"])
    for key in ("created", "linked"):
        assert placed(got[key]) == [], key
    door = ids["switch_door"]
    assert sorted(map(tuple, got["refs"])) == sorted([("trigger-13", "targets"), ("trigger-14", "targets"), (ids["breakable"], "parent")])
    assert got["refused"] == {"refused": True, "dangling": got["refs"], "same": True}
    cascade = got["cascade"]["mf"]
    assert placed(cascade) == [] and not ms.referrers(cascade, door)
    assert [t["targets"] for t in cascade["triggers"]] == [[], [{"feature": ids["breakable"], "event": "destroy"}]]
    rope = got["cascadeRope"]
    assert rope["removed"][0] == ids["vertical_rope"] and len(rope["removed"]) == 2, "its route goes with it"
    assert placed(rope["mf"]) == []
    relinked = got["relinked"]["mf"]
    assert placed(relinked) == [] and ms.referrers(relinked, ids["drop_door"]) and not ms.referrers(relinked, door)
    assert got["undoRestores"] and got["redoAgain"]
    plain, external = got["dupPlain"], got["dupExternal"]
    for dup in (plain, external, got["dupRope"]):
        assert placed(dup["mf"]) == []
    child = next(f for f in plain["mf"]["features"] if f["id"] == plain["ids"][ids["breakable"]])
    assert child["parent"] == plain["id"], "an owned child is copied with its parent link remapped"
    assert not ms.referrers(plain["mf"], plain["id"]) == [] and all(src == child["id"] for src, _ in ms.referrers(plain["mf"], plain["id"]))
    assert {src for src, f in ms.referrers(external["mf"], external["id"]) if f == "targets"} == {"trigger-13", "trigger-14"}
    dup_route = next(r for r in got["dupRope"]["mf"]["routes"] if r["owner"] == got["dupRope"]["id"])
    assert dup_route["id"] not in {r["id"] for r in got["linked"]["routes"]}
    assert got["renamedDigest"]


CATALOGUE_JS = """
  function run(p) {
    const T2 = T;
    const out = {};
    const bad = JSON.parse(JSON.stringify(p.catalogue));
    bad.maps.Ascent.map_features.triggers[0].targets.push({feature: "feature-99", event: "switch"});
    out.invalid = F.importCatalogue(p.current, bad, p.seeds);
    const newer = JSON.parse(JSON.stringify(p.catalogue));
    newer.maps.Ascent.map_features.version = 2;
    out.incompatible = F.importCatalogue(p.current, JSON.stringify(newer), p.seeds);
    out.garbage = F.importCatalogue(p.current, "{not json", p.seeds);
    const ok = F.importCatalogue(p.current, JSON.stringify(p.catalogue), p.seeds);
    out.okFlag = ok.ok; out.affected = ok.affected;
    let mf = ok.catalogue.maps.Ascent.map_features;
    mf = F.rename(mf, "feature-1", "B door");
    mf = F.setField(mf, "feature-1", ["states", 0, "blocks_sight"], false);
    // autosave -> reload: through JSON text, as local storage would
    const reloaded = JSON.parse(JSON.stringify({canonical: ok.catalogue, features: {Ascent: {dirty: true, mf: mf}}}));
    out.exported = F.exportCatalogue(reloaded.canonical, p.maps, {}, reloaded.features);
    out.untouched = F.exportCatalogue(ok.catalogue, p.maps, {}, {});
    out.plainTags = T2.exportTags(ok.catalogue, p.maps, {});
    return out;
  }
"""


def test_catalogue_import_edit_reload_export_keeps_everything():
    current = {"version": 1, "maps": {"Ascent": {"specials": [], "cover_reviewed": False}}}
    seeds = {n: ms.checklist_seed(n) for n in ms.CHECKLIST_SEED}
    maps = {"Ascent": {"image_sha": "abc123def456"}}
    got = run_node(CATALOGUE_JS, {"catalogue": CATALOGUE, "current": current, "seeds": seeds, "maps": maps})
    inv = got["invalid"]
    assert not inv["ok"] and inv["catalogue"] == current
    assert [(e["map"], e["where"], e["code"]) for e in inv["errors"]] == [("Ascent", "trigger-7", "dangling_reference")]
    inc = got["incompatible"]
    assert not inc["ok"] and inc["catalogue"] == current and inc["errors"][0]["code"] == "incompatible_version"
    assert not got["garbage"]["ok"] and got["garbage"]["errors"][0]["code"] == "bad_json"
    assert got["okFlag"] and {"map": "Ascent", "change": "features"} in got["affected"] and {"map": "Atlantis", "change": "added"} in got["affected"]
    out = got["exported"]
    assert out["x_catalogue_unknown"] == {"kept": True} and out["maps"]["Atlantis"] == CATALOGUE["maps"]["Atlantis"]
    asc = out["maps"]["Ascent"]
    assert asc["x_map_unknown"] == [1, 2, 3] and asc["specials"] == [] and "image_sha" not in asc, \
        "a features-only edit leaves the entry's legacy keys alone"
    mf = asc["map_features"]
    assert mf["features"][0]["name"] == "B door" and mf["features"][0]["states"][0]["blocks_sight"] is False
    assert mf["features"][0]["x_feature_unknown"] == {"deep": {"deeper": True}}
    assert mf["features"][0]["states"][0]["x_state_unknown"] == 7 and mf["x_features_unknown"] == "kept"
    assert mf["image_sha"] == "abc123def456" and mf["runtime_digest"] == ms.runtime_digest(mf)
    assert ms.validate(mf, "Ascent").errors == []
    assert got["untouched"] == CATALOGUE == got["plainTags"]


EXPORT_REAL = """
  function run(p) {
    const maps = {Ascent: {candidates: p.map.candidates, params: p.map.params, image_sha: p.map.image_sha}};
    const edits = {Ascent: T.editsFrom(p.tags.maps.Ascent)};
    edits.Ascent.touched = true;
    return {a: T.exportTags(p.tags, maps, edits), b: F.exportCatalogue(p.tags, maps, edits, {Ascent: {dirty: false, mf: {}}})};
  }
"""


def test_with_no_feature_edits_the_export_is_exactly_export_tags():
    sys.path.insert(0, str(WEBAPP / "scripts"))
    import control_tagger

    from app.control import geometry as cg

    tags = json.loads((cg.ASSET_DIR / "tags.json").read_text(encoding="utf-8"))
    got = run_node(EXPORT_REAL, {"tags": tags, "map": control_tagger.map_data("Ascent", tags["maps"]["Ascent"], [])})
    assert got["a"] == got["b"] == tags


# ---- W14: the page


@pytest.mark.parametrize('cells,walk,counts,unresolved,flat,codes', [
    ([2, 1, 2], [1, 1, 1], [0, 1, 1], [0, 0, 0], False, []),
    ([1], [1, 1], [1, 2], [0, 0], False, ['multi_floor']),
    ([1], [1, 1], [1, 0], [0, 0], False, ['missing_floor']),
    ([1], [1, 1], [1, 0], [0, 1], False, ['unresolved_height']),
    ([1], [1, 0], [1, 0], [0, 0], False, ['off_ground']),
    ([], [], [], [], False, ['empty_geometry']),
    ([1], [1, 1], [], [], True, []),
])
def test_browser_placement_uses_actual_floor_counts(cells, walk, counts, unresolved, flat, codes):
    result = run_node('function run(p) {return F.resolvePlacement(p.cells, p.context);}',
                      {'cells': cells, 'context': {'walk': walk, 'floor_counts': counts, 'unresolved': unresolved,
                                                  'flat': flat, 'path': 'fixture'}})
    assert result['ok'] == (not codes)
    assert [r['code'] for r in result['reasons']] == codes
    assert result['cells'] == sorted(set(cells))
    for r in result['reasons']:
        assert r['path'] == 'fixture' and r['cell_count'] == len(r['cells'])


def test_current_authoring_has_no_floor_picker_and_preserves_source_selectors():
    ui = (WEBAPP / 'scripts' / 'control_tagger_features.js').read_text(encoding='utf-8')
    guidance = (WEBAPP / 'scripts' / 'control_tagger.template.html').read_text(encoding='utf-8')
    for phrase in ('Add floor label', 'Give each landing its floor', 'floorOptions(', 'floorpick', 'ground_binding'):
        assert phrase not in ui + guidance
    source = copy.deepcopy(MF)
    source['features'][0]['floors'] = ['floor-old']
    source['x_unknown'] = {'kept': True}
    result = run_node('function run(p) {return {runtime: F.runtimeDigest(p), source: p};}', source)
    assert result['runtime'] == ms.runtime_digest(source) and result['source'] == source


def test_preview_keeps_required_sibling_and_trigger_placement_atomic():
    from map_feature_artifact_toys import source_case
    source = source_case()['map_features']
    source['bundles'][0]['members'] = ['feature-1', 'feature-2']
    source['bundles'] = source['bundles'][:1]
    walk = [1] * (128 * 128)
    counts = [1] * len(walk)
    counts[40 * 128 + 43] = 2
    got = run_node('function run(p) {return F.previewPlacement(p.mf, p.context);}',
                   {'mf': source, 'context': {'walk': walk, 'floor_counts': counts,
                                             'unresolved': [0] * len(walk), 'flat': False}})
    assert not got['feature-1']['ok'] and not got['feature-2']['ok']
    assert got['feature-1']['reasons'] == got['feature-2']['reasons']

def test_the_page_carries_the_features_panel_and_the_python_contract(tmp_path):
    sys.path.insert(0, str(WEBAPP / "scripts"))
    import control_tagger

    out = tmp_path / "tagger.html"
    assert control_tagger.main(["--out", str(out), "--map", "Summit", "--map", "Ascent"]) == 0
    page = out.read_text(encoding="utf-8")
    assert "/*FEATURES_UI*/" not in page and "/*CORE*/" not in page and "window.taggerFeatures = {" in page
    data = json.loads(page.split("var DATA = ", 1)[1].split(";\n", 1)[0])
    feats = data["features"]
    assert feats["schema_version"] == ms.SCHEMA_VERSION
    assert feats["presets"] == {p: ms.preset(p) for p in ms.PRESETS}, "presets come from the schema module, not a copy"
    assert feats["seeds"]["Summit"] == ms.checklist_seed("Summit")
    assert all(v == {'flat': True, 'height_sha': None} for v in feats['floors'].values())
    for needle in ('data-mode="features"', 'data-preset="trigger"', ">Switch/trigger<", ">Breakable<", ">Zipline<",
                   ">Vertical rope<", 'data-ftool="polygon"', 'data-ftool="link"', 'data-ftool="brush"', 'id="featUndo"',
                   'id="featRedo"', 'id="zoomIn"', 'id="featSaved"', 'id="featImport"', 'id="featExport"',
                   'id="featDownload"', 'id="featChecklist"', 'id="featIssues"', "Export reviewed annotations",
                   'data-layer="grid"', 'data-layer="sight"', "probes are not available", 'id="featHelp"'):
        assert needle in page, needle
    scripts = page.split("<script>")
    assert len(scripts) == 4, "core, the page, the features panel"
    for body in scripts[1:]:
        code = body.split("</script>")[0]
        done = subprocess.run([NODE, "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"], input=code,
                              capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
        assert done.returncode == 0, done.stderr


def test_floor_data_is_embedded_only_for_maps_with_a_height_asset(tmp_path):
    import numpy as np

    sys.path.insert(0, str(WEBAPP / "scripts"))
    import control_tagger

    from app.control import heights as hc

    assert control_tagger.floor_data('Ascent')['flat'] is True
    floors = -np.ones((128, 128, hc.MAX_FLOORS), np.int16)
    floors[10, 20, :2] = [0, 40]
    asset = hc.HeightAsset(floors, np.zeros_like(floors), np.zeros((128, 128), bool), np.zeros((128, 128), bool),
                           np.zeros((0, 4), np.int32), {"origin_z": 120})
    hc.save_asset(tmp_path / "Toy.height.npz", asset)
    (tmp_path / "index.json").write_text(json.dumps({"maps": {"Toy": {"height_sha": asset.digest}}}), encoding="utf-8")
    got = control_tagger.floor_data("Toy", tmp_path)
    assert got["height_sha"] == asset.digest and got["origin_z"] == 120
    import base64
    arr = np.frombuffer(base64.b64decode(got["floors"]), "<i2").reshape(128, 128, hc.MAX_FLOORS)
    assert arr[10, 20].tolist()[:2] == [0, 40] and (arr[0, 0] == -1).all()


# ---- W13: drafts

DRAFTS = """
  function Fake(opts) { this.m = new Map(); this.opts = opts || {}; this.writes = 0; }
  Fake.prototype.getItem = function (k) {
    if (this.opts.readDenied) { const e = new Error("denied"); e.name = "SecurityError"; throw e; }
    return this.m.has(k) ? this.m.get(k) : null;
  };
  Fake.prototype.setItem = function (k, v) {
    this.writes++;
    if (this.opts.failOn && k.indexOf(this.opts.failOn) >= 0 && this.writes > (this.opts.after || 0)) {
      const e = new Error("quota"); e.name = "QuotaExceededError"; throw e;
    }
    let size = 0; this.m.forEach((x, key) => { if (key !== k) size += x.length; });
    if (this.opts.quota && size + v.length > this.opts.quota) { const e = new Error("quota"); e.name = "QuotaExceededError"; throw e; }
    this.m.set(k, String(v));
  };
  Fake.prototype.removeItem = function (k) { this.m.delete(k); };
  function snapKeys(store) { return [...store.m.keys()].filter(k => k.indexOf(":snap:") >= 0).map(k => k.split(":").pop()).sort(); }

  function run(p) {
    const out = {};
    const body = n => ({canonical: p.catalogue, features: {Ascent: {dirty: true, mf: {...p.mf, next_id: 100 + n}}}});
    // round trip, three saves, the oldest pruned
    let s = new Fake(), d = new F.Drafts(s, p.source);
    out.empty = d.load();
    out.saves = [1, 2, 3].map(n => d.save(body(n)));
    out.loaded = d.load();
    out.kept = snapKeys(s);
    out.sameBody = JSON.stringify(out.loaded.body) === JSON.stringify(body(3));
    // corrupt active snapshot: the previous one is used
    const key = [...s.m.keys()].find(k => k.endsWith(":snap:3"));
    s.m.set(key, s.m.get(key).replace("103", "999"));
    out.recovered = d.load();
    // an interrupted save (the pointer write fails) leaves the last good snapshot active and no orphan
    s = new Fake({failOn: ":active", after: 7}); d = new F.Drafts(s, p.source);   // saves 1 and 2 take 3 writes each
    d.save(body(1)); d.save(body(2));
    out.interrupted = d.save(body(3));
    out.afterInterrupt = d.load().revision;
    out.interruptKeys = snapKeys(s);
    // quota: nothing saved, and it says so
    s = new Fake({quota: 50}); d = new F.Drafts(s, p.source);
    out.quota = d.save(body(1));
    out.quotaLoad = d.load().status;
    // reads denied (private mode): no throw
    out.denied = new F.Drafts(new Fake({readDenied: true}), p.source).load().status;
    // another source (an image changed): its draft is listed with what differs, never loaded
    s = new Fake(); d = new F.Drafts(s, p.source); d.save(body(1));
    const moved = JSON.parse(JSON.stringify(p.source)); moved.image_digests.Ascent = "changed00000";
    const d2 = new F.Drafts(s, moved);
    out.otherLoad = d2.load().status;
    out.others = d2.others();
    out.readOther = JSON.stringify(d2.readOther(out.others[0].key).body) === JSON.stringify(body(1));
    // a stored snapshot of a schema this page can't read: reported, store untouched
    s = new Fake(); d = new F.Drafts(s, p.source);
    const newer = body(1); newer.canonical = JSON.parse(JSON.stringify(p.catalogue)); newer.canonical.maps.Ascent.map_features.version = 2;
    d.save(newer);
    const before = JSON.stringify([...s.m.entries()]);
    out.incompatible = d.load();
    out.untouched = JSON.stringify([...s.m.entries()]) === before;
    return out;
  }
"""


def test_drafts_survive_corruption_interruption_quota_and_source_changes():
    source = {"catalogue_digest": "c1", "image_digests": {"Ascent": "img1", "Bind": "img2"},
              "floor_digests": {"Ascent": None}, "schema_version": 1}
    got = run_node(DRAFTS, {"catalogue": CATALOGUE, "mf": MF, "source": source})
    assert got["empty"]["status"] == "none"
    assert [s["revision"] for s in got["saves"]] == [1, 2, 3] and all(s["ok"] for s in got["saves"])
    assert got["loaded"]["status"] == "ok" and got["loaded"]["revision"] == 3 and got["sameBody"]
    assert got["kept"] == ["2", "3"], "the active snapshot and the previous one"
    assert got["recovered"]["status"] == "recovered" and got["recovered"]["revision"] == 2
    assert got["interrupted"]["ok"] is False and got["interrupted"]["stage"] == "pointer"
    assert got["afterInterrupt"] == 2 and got["interruptKeys"] == ["1", "2"]
    assert got["quota"]["ok"] is False and got["quota"]["error"].startswith("QuotaExceededError") and got["quotaLoad"] == "none"
    assert got["denied"] == "corrupt"
    assert got["otherLoad"] == "none" and len(got["others"]) == 1 and got["others"][0]["differs"] == ["image of Ascent"]
    assert got["readOther"]
    assert got["incompatible"]["status"] == "incompatible" and got["incompatible"]["body"] is None and got["untouched"]


# ---- W15: rasterising and composition parity, floors and routes through Python's compiler

RASTER = """
  function packBits(a) {
    const out = new Uint8Array(Math.ceil(a.length / 8));
    for (let i = 0; i < a.length; i++) if (a[i]) out[i >> 3] |= 128 >> (i & 7);
    return Buffer.from(out).toString("base64");
  }
  function run(p) {
    const out = {rasters: p.geoms.map(g => packBits(F.raster(g)))};
    if (p.map) {
      const sight = T.rleDecode(p.map.sight), walk = T.rleDecode(p.map.walk);
      out.composed = p.states.map(st => { const m = F.composeFeatures(sight, walk, p.mf, st); return [packBits(m.sight), packBits(m.walk)]; });
    }
    return out;
  }
"""


def random_geoms(n=40, seed=7):
    import random

    rnd = random.Random(seed)

    def pt():
        return [round(rnd.uniform(0, 10000), rnd.choice([0, 2, 5])), round(rnd.uniform(0, 10000), rnd.choice([0, 2, 5]))]

    out = [{"type": "point", "uv": [0, 0]}, {"type": "point", "uv": [10000, 10000]}, {"type": "point", "uv": [39.0625, 78.125]},
           {"type": "polyline", "uv": [[100, 100], [100, 100]]}, {"type": "polygon", "uv": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]}]
    for i in range(n):
        kind = ("point", "polyline", "polygon")[i % 3]
        if kind == "point":
            out.append({"type": "point", "uv": pt()})
        elif kind == "polyline":
            g = {"type": "polyline", "uv": [pt() for _ in range(rnd.randint(2, 5))]}
            if i % 2:
                g["width"] = round(rnd.uniform(1, 400), 3)
            out.append(g)
        else:
            c = pt()
            out.append({"type": "polygon", "uv": [[min(max(c[0] + rnd.uniform(-900, 900), 0), 10000), min(max(c[1] + rnd.uniform(-900, 900), 0), 10000)]
                                                  for _ in range(rnd.randint(3, 8))]})
    return out


def test_js_rasters_match_python_bit_for_bit():
    import base64

    import numpy as np

    from app.control import features as cf

    geoms = random_geoms()
    paint = np.zeros((256, 256), bool)
    paint[5:9, 100:140] = True
    from app.control import geometry as cg
    geoms.append({"type": "paint", "cells": cg.pack_paint(paint)})
    got = run_node(RASTER, {"geoms": geoms})["rasters"]
    for g, js in zip(geoms, got):
        assert js == base64.b64encode(np.packbits(cf.raster(g))).decode(), g


def test_js_composition_matches_python_for_every_state_and_after_reset():
    import base64

    import numpy as np
    from PIL import Image

    sys.path.insert(0, str(WEBAPP / "scripts"))
    import control_tagger

    from app.control import features as cf
    from app.control import geometry as cg

    tags = json.loads((cg.ASSET_DIR / "tags.json").read_text(encoding="utf-8"))
    m = cg.masks(np.array(Image.open(cg.MINIMAP_DIR / "Ascent.png").convert("RGBA")), tags["maps"]["Ascent"])
    mf = copy.deepcopy(MF)
    mf["features"][0]["states"][0]["footprint"] = {"type": "polygon", "uv": [[4000, 4000], [4400, 4000], [4400, 4120], [4000, 4120]]}
    mf["features"][1]["states"][0]["footprint"] = {"type": "polygon", "uv": [[4200, 3900], [4300, 3900], [4300, 4300], [4200, 4300]]}
    mf["features"][1]["states"][0]["sight"] = [{"geometry": {"type": "polyline", "uv": [[4200, 3800], [4600, 3800]]}, "bounds": {"ref": "all_height"}}]
    states = [{}, {"feature-1": "closed"}, {"feature-1": "broken"}, {"feature-1": "closed", "feature-2": "rest_b"},
              {"feature-1": "open", "feature-2": "rotating"}]
    payload = {"geoms": [], "mf": mf, "states": states,
               "map": {"sight": control_tagger.rle(m.sight.astype(np.uint8)), "walk": control_tagger.rle(m.walk.astype(np.uint8))}}
    got = run_node(RASTER, payload)["composed"]
    pack = lambda a: base64.b64encode(np.packbits(a)).decode()    # noqa: E731
    for st, (js_s, js_w) in zip(states, got):
        s, w = cf.compose_masks(m.sight, m.walk, mf, st)
        assert (js_s, js_w) == (pack(s), pack(w)), st
    closed, broken = got[1], got[2]
    assert closed != broken, "breaking the door changes the preview"
    assert got[0] == run_node(RASTER, {**payload, "states": [{}]})["composed"][0], "reset = the round-start states"


ROPE = """
  function run(p) {
    let mf = F.emptyMf();
    let a = F.addObject(mf, "floor", {label: "ground", z_band: [-0.5, 1.0], height_sha: p.sha}); mf = a.mf; const lower = a.id;
    a = F.addObject(mf, "floor", {label: "bridge", z_band: [3.0, 5.0], height_sha: p.sha}); mf = a.mf; const upper = a.id;
    a = F.addObject(mf, "floor", {label: "roof?", z_band: null, height_sha: null}); mf = a.mf; const manual = a.id;
    const r = F.create(mf, p.preset, "Rope", {}, p.route); mf = r.mf;
    const ri = mf.routes.findIndex(x => x.id === r.route);
    mf = F.setField(mf, r.route, ["endpoints", 0, "uv"], p.uv);
    mf = F.setField(mf, r.route, ["endpoints", 1, "uv"], p.uv);
    mf = F.setField(mf, r.route, ["endpoints", 0, "floor"], lower);
    mf = F.setField(mf, r.route, ["endpoints", 1, "floor"], upper);
    mf = F.setField(mf, r.route, ["directions", 0, "entry"], {status: "known", value: 0.5, unit: "s"});
    mf = F.setField(mf, r.route, ["directions", 0, "transit"], {status: "known", value: 2, unit: "s"});
    // a zipline the minimap doesn't show, one landing on a manual (unresolved) floor
    const z = F.create(mf, p.zpreset, "Zipline", {}, p.zroute); mf = z.mf;
    mf = F.setField(mf, z.route, ["endpoints", 0, "uv"], p.za);
    mf = F.setField(mf, z.route, ["endpoints", 1, "uv"], p.zb);
    mf = F.setField(mf, z.route, ["endpoints", 0, "floor"], lower);
    mf = F.setField(mf, z.route, ["endpoints", 1, "floor"], manual);
    const cat = {version: 1, maps: {Bridge: {map_features: mf}}};
    const exported = F.exportCatalogue(cat, {}, {}, {Bridge: {dirty: true, mf: mf}});
    const back = F.importCatalogue({version: 1, maps: {}}, JSON.stringify(exported), {});
    return {mf: mf, exported: exported, reimported: back.ok ? back.catalogue : back, ids: {lower, upper, manual, rope: r.route, zip: z.route}};
  }
"""


def test_a_same_position_rope_and_an_unresolved_landing_round_trip_and_compile_without_shortcuts():
    from app.control import features as cf
    from tests.replays.control_toys import HALL, toy_heights

    geo = toy_heights("Bridge", [HALL], ground=[((96, 96, 240, 296), 4.0)], upper=[((240, 96, 288, 296), 4.0)])
    uv = [264 * 10000 / 1024, 196 * 10000 / 1024]
    got = run_node(ROPE, {"sha": geo.height_sha, "uv": uv, "preset": ms.preset("vertical_rope"), "route": ms.route_template("rope"),
                          "zpreset": ms.preset("zipline"), "zroute": ms.route_template("zipline"),
                          "za": [400 * 10000 / 1024, 196 * 10000 / 1024], "zb": [600 * 10000 / 1024, 600 * 10000 / 1024]})
    ids = got["ids"]
    exported = got["exported"]["maps"]["Bridge"]["map_features"]
    assert got["reimported"]["maps"]["Bridge"]["map_features"] == exported
    rope = next(r for r in exported["routes"] if r["id"] == ids["rope"])
    assert rope["endpoints"][0]["uv"] == rope["endpoints"][1]["uv"], "two landings at one spot"
    assert [e["floor"] for e in rope["endpoints"]] == [ids["lower"], ids["upper"]]
    zip_ = next(r for r in exported["routes"] if r["id"] == ids["zip"])
    assert zip_["endpoints"][1]["floor"] == ids["manual"], "an unresolved floor stays unresolved after export and import"
    rep = ms.validate(exported)
    assert rep.errors == [] and any(w["code"] == "zero_length" for w in rep.warnings)
    arcs, pending = cf.compile_routes(geo, exported)
    rope_arcs = [a for a in arcs if a.route == ids["rope"]]
    assert rope_arcs == [] and pending, 'multi-floor endpoints stay pending despite authored selectors'
    assert not [a for a in arcs if a.route == ids["zip"]], "a landing on an unresolved floor never snaps to another floor"
    assert any(p.startswith(ids["zip"] + ".b") for p in pending)
    assert [d["code"] for d in cf.diagnose(geo, exported) if d["where"].startswith(ids["zip"])] == ["off_ground"], \
        "the off-minimap landing is flagged, not moved"


# ---- W16: rotation poses, previews and the map summary

PREVIEW = """
  function run(p) {
    const poses = p.cases.map(c => F.rotationPose(c.f, c.frac));
    const door = p.mf.features[0];
    // a sandbox sequence through the shared reducer: close, break mid-motion, a later press, then a new round
    const seq = [{t: 0, kind: "switch"}, {t: 1, kind: "destroy"}, {t: 3, kind: "switch"}];
    const before = F.run(door, seq, 5), after = F.run(door, [], 0);
    return {poses, broken: before[before.length - 1].state, reset: after.length ? after[after.length - 1].state : door.initial_state,
            summary: F.mapSummary(p.mf, p.seed), emptySummary: F.mapSummary(F.emptyMf(), [])};
  }
"""


def test_rotation_poses_match_python_and_the_sandbox_and_summary_behave():
    from app.control import features as cf

    rot = MF["features"][1]
    cases = [{"f": rot, "frac": f} for f in (0, 0.25, 0.5, 1)]
    cases.append({"f": {**rot, "rotation": {**rot["rotation"], "direction": {"status": "unresolved"}}}, "frac": 0.5})
    cases.append({"f": {**rot, "rotation": {**rot["rotation"], "phases": [{"at": 0.4, "panel": {"type": "polyline", "uv": [[1, 2], [3, 4]]}}]}}, "frac": 0.9})
    got = run_node(PREVIEW, {"cases": cases, "mf": MF, "seed": ms.checklist_seed("Ascent")})
    for case, js in zip(cases, got["poses"]):
        py = cf.rotation_pose(case["f"], case["frac"])
        if py is None:
            assert js is None
        else:
            flat = lambda pts: [c for p in pts for c in p]       # noqa: E731
            assert js["type"] == py["type"] and flat(js["uv"]) == pytest.approx(flat(py["uv"]), abs=1e-9)
    assert got["broken"] == "broken" and got["reset"] == "open", "broken until the round resets"
    summary = got["summary"]
    assert summary["annotation"]["features"] == 6 and summary["annotation"]["checklist"]["in_progress"] == 1
    assert summary["geometry"]["errors"] == 0 and summary["geometry"]["unresolved"] > 0 and not summary["geometry"]["ready"]
    assert summary["replay"]["decoder"] is False
    assert got["emptySummary"]["geometry"]["ready"] is True


# ---- the panel's wiring: scripts/control_tagger_features.js run in node through tests/replays/tagger_page.js,
# driven by its own buttons and entry points (the pure-model tests above can't see these)

HARNESS = HERE / "tagger_page.js"
PAGE_PRELUDE = """
  const {openPage, memoryStorage} = require(process.argv[1]);
  let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
    const p = JSON.parse(input);
    process.stdout.write(JSON.stringify(run(p)));
  });
"""
# Bind's loaded annotations are of a schema version this page can't read
FUTURE = {"version": 2, "next_id": 99, "features": [{"id": "feature-98", "future_data": "precious"}]}


def run_page(body: str, payload):
    assert NODE, "node is required for the tagger page tests"
    completed = subprocess.run([NODE, "-e", PAGE_PRELUDE + body, str(HARNESS)], input=json.dumps(payload),
                               capture_output=True, text=True, encoding="utf-8", timeout=300, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def page_data(future=True, **extra_tags):
    """The page's embedded data (control_tagger.render's DATA) for Ascent and Bind, from the committed tags with
    the fixture's annotations on Ascent and (`future`) annotations of a newer schema on Bind."""
    sys.path.insert(0, str(WEBAPP / "scripts"))
    import control_tagger

    from app.control import geometry as cg

    tags = json.loads((cg.ASSET_DIR / "tags.json").read_text(encoding="utf-8"))
    tags["maps"]["Ascent"]["map_features"] = copy.deepcopy(MF)
    if future:
        tags["maps"]["Bind"]["map_features"] = copy.deepcopy(FUTURE)
    tags.update(extra_tags)
    maps = control_tagger.build(tags, {}, ["Ascent", "Bind"])
    return {"tags": tags, "maps": maps, "features": control_tagger.features_data(sorted(maps))}


IMPORT_PAGE = """
  function run(p) {
    const out = {}, id = "feature-1";
    const page = openPage(p.data, {map: "Ascent"}), A = page.api, F = page.F, TG = page.TG;
    out.original = F.find(A.fe.Ascent.mf, id).name;
    A.commit(F.setField(A.fe.Ascent.mf, id, ["name"], "my draft"), true);
    TG.edits.Ascent.cover_reviewed = !TG.edits.Ascent.cover_reviewed;        // a legacy edit too
    TG.edits.Ascent.touched = true;
    out.cover = TG.edits.Ascent.cover_reviewed;
    A.importText(JSON.stringify(p.data.tags), "unchanged.json");             // the page's own catalogue, unchanged
    out.preview = page.el("featImportBody").innerHTML;
    out.disabled = page.el("featImportReplace").disabled;
    page.click("featImportCancel");
    out.mine = {name: F.find(A.fe.Ascent.mf, id).name, dirty: A.fe.Ascent.dirty, cover: TG.edits.Ascent.cover_reviewed};
    page.click("featExport");
    const exported = JSON.parse(page.downloads[page.downloads.length - 1].text);
    out.exported = {name: F.find(exported.maps.Ascent.map_features, id).name, cover: exported.maps.Ascent.cover_reviewed,
                    bind: exported.maps.Bind.map_features === undefined};
    A.undo();
    out.undone = F.find(A.fe.Ascent.mf, id).name;
    // replace, on another page: the edit is replaced, as the preview said
    const q = openPage(p.data, {map: "Ascent"});
    q.api.commit(q.F.setField(q.api.fe.Ascent.mf, id, ["name"], "my draft"), true);
    q.api.importText(JSON.stringify(p.data.tags), "unchanged.json");
    q.click("featImportReplace");
    out.replaced = {name: q.F.find(q.api.fe.Ascent.mf, id).name, dirty: q.api.fe.Ascent.dirty};
    // and with nothing edited, the same file changes nothing
    const r = openPage(p.data, {map: "Ascent"});
    r.api.importText(JSON.stringify(p.data.tags), "same.json");
    out.cleanPreview = r.el("featImportBody").innerHTML;
    return out;
  }
"""


def test_an_import_is_previewed_against_the_working_edits_and_only_replace_loses_them():
    got = run_page(IMPORT_PAGE, {"data": page_data(future=False)})
    assert "Ascent: features (replaces your unsaved edits to it)" in got["preview"]
    assert "Ascent: tags" in got["preview"], "the legacy edit is part of the working catalogue"
    assert got["disabled"] is False
    assert got["mine"] == {"name": "my draft", "dirty": True, "cover": got["cover"]}, "cancelling keeps the edits"
    assert got["exported"]["name"] == "my draft" and got["exported"]["cover"] == got["cover"]
    assert got["exported"]["bind"], "an untouched map stays without annotations"
    assert got["undone"] == got["original"], "the kept edit keeps its undo history"
    assert got["replaced"] == {"name": got["original"], "dirty": False}
    assert "It matches your current work." in got["cleanPreview"]


IDS_PAGE = """
  function run(p) {
    const out = {allocated: []};
    let page = openPage(p.data, {map: "Ascent"});
    const ids = pg => pg.api.fe.Ascent.mf.features.map(f => f.id);
    function create(pg) {
      const before = ids(pg);
      pg.api.createPreset("breakable");
      const made = ids(pg).filter(x => before.indexOf(x) < 0);
      out.allocated.push(made[0]);
      return made[0];
    }
    const start = ids(page);
    create(page);
    page.api.undo();
    out.undoneContent = JSON.stringify(ids(page)) === JSON.stringify(start);
    create(page);                                   // after undo
    page.api.undo(); page.api.redo(); page.api.undo();
    create(page);                                   // a new branch after redo and undo
    page = page.reopen();                           // the saved draft, reloaded
    create(page);
    page.api.importText(JSON.stringify(p.data.tags), "lower.json");    // next_id 20: below what was handed out
    out.importAllowed = !page.el("featImportReplace").disabled;
    page.click("featImportReplace");
    out.importedClean = !page.api.fe.Ascent.dirty;
    create(page);
    // recovery: the newest snapshot is damaged after one more feature; the previous one is restored
    create(page);
    const st = page.storage, key = page.api.drafts.key;
    const pointer = JSON.parse(st.getItem("control-tagger-features-v1:" + key + ":active"));
    const snapKey = "control-tagger-features-v1:" + key + ":snap:" + pointer.revision;
    st.m.set(snapKey, st.m.get(snapKey).replace("Breakable", "Broken!!!"));
    page = page.reopen();
    out.recoveredNote = page.el("featDrafts").innerHTML;
    create(page);
    return out;
  }
"""


def test_ids_are_never_reused_through_undo_branches_saves_imports_and_recovery():
    got = run_page(IDS_PAGE, {"data": page_data(future=False)})
    allocated = got["allocated"]
    assert all(allocated) and len(allocated) == 7
    assert len(set(allocated)) == len(allocated), allocated
    assert got["undoneContent"], "undo still removes the created feature"
    assert got["importAllowed"] and got["importedClean"], "the replace really ran"
    numbers = [int(i.split("-")[1]) for i in allocated]
    assert numbers == sorted(numbers) and numbers[0] >= MF["next_id"]
    assert "previous good draft" in got["recoveredNote"]


INCOMPATIBLE_PAGE = """
  function run(p) {
    const out = {};
    const page = openPage(p.data, {map: "Bind"}), A = page.api, TG = page.TG;
    out.flag = A.fe.Bind.incompatible;
    A.createPreset("breakable");
    A.commit(page.F.addObject(A.fe.Bind.mf, "feature", {name: "forced"}).mf, true);
    A.undo();
    out.bind = {dirty: A.fe.Bind.dirty, features: A.fe.Bind.mf.features.length, message: A.ui.message};
    TG.edits.Bind.cover_reviewed = !TG.edits.Bind.cover_reviewed;          // a legacy edit of the same map
    TG.edits.Bind.touched = true;
    out.cover = TG.edits.Bind.cover_reviewed;
    TG.selectMap("Ascent");
    A.createPreset("breakable");                                              // an edit elsewhere: a draft is saved
    page.click("featExport");
    const exported = JSON.parse(page.downloads[page.downloads.length - 1].text);
    out.exported = {bind: exported.maps.Bind.map_features, cover: exported.maps.Bind.cover_reviewed};
    const again = page.reopen();
    out.reopen = {note: again.el("featDrafts").innerHTML, ascentDirty: again.api.fe.Ascent.dirty,
                  bindDirty: again.api.fe.Bind.dirty, bindReadOnly: again.api.fe.Bind.incompatible};
    again.click("featDownload");
    const file = again.downloads[again.downloads.length - 1];
    const other = openPage(p.data, {map: "Ascent"});
    other.api.importText(file.text, file.name);
    out.restorable = !other.el("featImportReplace").disabled;
    other.click("featImportReplace");
    other.click("featExport");
    out.restoredBind = JSON.parse(other.downloads[other.downloads.length - 1].text).maps.Bind.map_features;
    // the model's own guard: a forced dirty model never replaces an unreadable entry
    out.forced = page.F.exportCatalogue(p.data.tags, {}, {}, {Bind: {dirty: true, mf: page.F.emptyMf()}}).maps.Bind.map_features;
    return out;
  }
"""


def test_annotations_of_another_schema_are_never_replaced():
    got = run_page(INCOMPATIBLE_PAGE, {"data": page_data()})
    assert got["flag"] is True
    assert got["bind"]["dirty"] is False and got["bind"]["features"] == 0 and "read-only" in got["bind"]["message"]
    assert got["exported"] == {"bind": FUTURE, "cover": got["cover"]}, "legacy edits export; the annotations as loaded"
    assert got["reopen"]["ascentDirty"] and not got["reopen"]["bindDirty"] and got["reopen"]["bindReadOnly"]
    assert "Restored your draft" in got["reopen"]["note"], "a catalogue holding an unreadable entry still restores drafts"
    assert got["restorable"] and got["restoredBind"] == FUTURE
    assert got["forced"] == FUTURE


DRAFT_PAGE = """
  function run(p) {
    const out = {};
    const page = openPage(p.data, {map: "Ascent", storage: memoryStorage({failWrites: true})}), A = page.api, F = page.F;
    A.commit(F.setField(A.fe.Ascent.mf, "feature-1", ["name"], "unsaved rename"), true);
    // incomplete: a trigger aimed at a feature that doesn't exist yet (a structural error)
    A.commit(F.addObject(A.fe.Ascent.mf, "trigger", {name: "dangling", type: "switch", geometry: null,
                                                     targets: [{feature: "feature-999", event: "switch"}]}).mf, true);
    out.saved = page.el("featSaved").textContent;
    page.click("featExport");
    out.exportRefused = page.downloads.length === 0;
    page.click("featDownload");
    const file = page.downloads[page.downloads.length - 1];
    out.fileName = file.name;
    const want = page.plain(A.fe.Ascent.mf);
    // reopened with working storage: nothing was stored, so it starts clean; the file restores it
    const fresh = openPage(p.data, {map: "Ascent"});
    out.freshDirty = fresh.api.fe.Ascent.dirty;
    fresh.api.importText(file.text, file.name);
    out.dialog = fresh.el("featImportBody").innerHTML;
    out.label = fresh.el("featImportReplace").textContent;
    out.disabled = fresh.el("featImportReplace").disabled;
    fresh.click("featImportReplace");
    out.restoredSame = JSON.stringify(fresh.plain(fresh.api.fe.Ascent.mf)) === JSON.stringify(want);
    out.restoredDirty = fresh.api.fe.Ascent.dirty;
    out.savedAfter = fresh.el("featSaved").textContent;
    const reloaded = fresh.reopen();
    out.reloadedSame = JSON.stringify(reloaded.plain(reloaded.api.fe.Ascent.mf)) === JSON.stringify(want);
    // transactional: a damaged file, a draft of another schema and a garbled one restore nothing
    const parsed = JSON.parse(file.text);
    const tampered = JSON.parse(file.text); tampered.features.Ascent.mf.features[0].name = "tampered";
    const newer = JSON.parse(file.text); newer.features.Ascent.mf.version = 2;
    newer.checksum = F.digest({canonical: newer.canonical, features: newer.features, high: newer.high});
    const shapeless = JSON.parse(file.text); shapeless.features.Ascent = {mf: "nope"};
    out.refused = [tampered, newer, shapeless].map(bad => {
      const t = openPage(p.data, {map: "Ascent"});
      t.api.importText(JSON.stringify(bad), "bad.json");
      return {disabled: t.el("featImportReplace").disabled, body: t.el("featImportBody").innerHTML, dirty: t.api.fe.Ascent.dirty};
    });
    // a file downloaded before drafts carried their provenance: {canonical, features}
    const bare = openPage(p.data, {map: "Ascent"});
    bare.api.importText(JSON.stringify({canonical: parsed.canonical, features: parsed.features}), "old.json");
    out.bareDialog = bare.el("featImportBody").innerHTML;
    bare.click("featImportReplace");
    out.bareSame = JSON.stringify(bare.plain(bare.api.fe.Ascent.mf.features)) === JSON.stringify(want.features);
    // a draft saved for another catalogue says so before it is restored
    const moved = openPage(p.moved, {map: "Ascent"});
    moved.api.importText(file.text, file.name);
    out.movedDialog = moved.el("featImportBody").innerHTML;
    // an older draft listed for another source goes through the same restore, incomplete or not
    const shared = memoryStorage();
    const first = openPage(p.data, {map: "Ascent", storage: shared});
    first.api.commit(first.F.addObject(first.api.fe.Ascent.mf, "trigger", {name: "dangling", type: "switch", geometry: null,
                                                                          targets: [{feature: "feature-999", event: "switch"}]}).mf, true);
    const later = openPage(p.moved, {map: "Ascent", storage: shared});
    const key = later.api.drafts.others()[0].key;
    const button = {getAttribute: k => k === "data-other" ? key : "review"};
    later.el("featDrafts").dispatch("click", {target: {closest: () => button}});
    out.olderLabel = later.el("featImportReplace").textContent;
    out.olderDisabled = later.el("featImportReplace").disabled;
    later.click("featImportReplace");
    out.olderRestored = later.api.fe.Ascent.dirty && later.api.fe.Ascent.mf.triggers.some(t => t.name === "dangling");
    return out;
  }
"""


def test_a_downloaded_draft_restores_after_a_storage_failure():
    got = run_page(DRAFT_PAGE, {"data": page_data(), "moved": page_data(x_moved=True)})
    assert got["saved"].startswith("NOT saved") and got["exportRefused"], "storage failed and the draft is incomplete"
    assert got["fileName"] == "tagger-draft.json" and got["freshDirty"] is False
    assert got["label"] == "Restore draft" and got["disabled"] is False
    assert "Ascent: features" in got["dialog"] and "structural errors" in got["dialog"]
    assert got["restoredSame"] and got["restoredDirty"] and got["savedAfter"] == "saved"
    assert got["reloadedSame"], "the restored draft is the page's draft now"
    for bad, code in zip(got["refused"], ("checksum", "version", "{map: {mf}}")):
        assert bad["disabled"] and not bad["dirty"] and "Not restored" in bad["body"] and code in bad["body"], bad
    assert "no provenance recorded" in got["bareDialog"] and got["bareSame"]
    assert "Saved for a different page: catalogue" in got["movedDialog"]
    assert got["olderLabel"] == "Restore draft" and not got["olderDisabled"] and got["olderRestored"]


def test_preview_uses_actual_asset_counts_and_never_creates_floor_labels():
    source = (WEBAPP / "scripts" / "control_tagger_features.js").read_text(encoding="utf-8")
    assert 'floor_counts:fd.floor_counts' in source and 'function pickFloor' not in source
    core = (WEBAPP / "scripts" / "control_tagger_core.js").read_text(encoding="utf-8")
    assert '"unframed_floor"' in core


def test_drawn_sight_edge_exports_and_reimports_as_a_valid_polyline():
    got = run_page("""
      function run(p) {
        const page = openPage(p.data, {map: "Ascent"}), A = page.api;
        A.ui.sel = "feature-1";
        A.ui.editState[A.ui.sel] = "closed";
        A.setTool("line");
        A.ui.draft = {kind: "line", points: [[320, 320], [352, 320]]};
        A.finishDraft();
        page.click("featExport");
        const file = page.downloads[page.downloads.length - 1];
        if (!file) return {exported: false};
        const tags = JSON.parse(file.text);
        const mf = tags.maps.Ascent.map_features;
        const closed = mf.features.find(f => f.id === "feature-1").states.find(s => s.name === "closed");
        const fresh = openPage(p.data, {map: "Ascent"});
        fresh.api.importText(file.text, file.name);
        const accepted = !fresh.el("featImportReplace").disabled;
        fresh.click("featImportReplace");
        return {exported: true, accepted: accepted, mf: mf,
                edge: closed.sight[closed.sight.length - 1],
                restored: fresh.plain(fresh.api.fe.Ascent.mf)};
      }
    """, {"data": page_data(future=False)})
    assert got["exported"], "drawing a sight edge must not prevent reviewed export"
    assert got["edge"] == {"geometry": {"type": "polyline", "uv": [[3125, 3125], [3437.5, 3125]]},
                           "bounds": {"ref": "unresolved"}}
    assert ms.validate(got["mf"], "Ascent").errors == []
    assert got["accepted"] and got["restored"] == got["mf"]


@pytest.mark.parametrize("source", ["autosave", "download", "embedded"])
def test_existing_line_drafts_repair_without_losing_edits(source):
    got = run_page("""
      function run(p) {
        const page = openPage(p.data, {map: "Ascent"}), A = page.api;
        const old = page.plain(A.fe.Ascent.mf);
        const f = old.features.find(f => f.id === "feature-1");
        f.name = "My edited door";
        f.states.find(s => s.name === "closed").sight = [{
          geometry: {type: "line", uv: [[3125, 3125], [3437.5, 3125]], custom: "keep"},
          bounds: {ref: "unresolved"}, notes: "keep bounds unresolved"
        }];
        A.commit(old, true);
        page.click("featDownload");
        const file = page.downloads[page.downloads.length - 1];
        let fresh;
        if (p.source === "autosave") fresh = page.reopen();
        else if (p.source === "download") {
          fresh = openPage(p.data, {map: "Ascent"});
          fresh.api.importText(file.text, file.name);
          fresh.click("featImportReplace");
        } else {
          const data = JSON.parse(JSON.stringify(p.data));
          data.tags.maps.Ascent.map_features = old;
          fresh = openPage(data, {map: "Ascent"});
        }
        const expected = JSON.parse(JSON.stringify(old));
        expected.features.find(f => f.id === "feature-1").states.find(s => s.name === "closed").sight[0].geometry.type = "polyline";
        fresh.click("featExport");
        return {expected: expected, actual: fresh.plain(fresh.api.fe.Ascent.mf),
                persisted: fresh.plain(fresh.reopen().api.fe.Ascent.mf),
                exported: fresh.downloads.length > 0, saved: fresh.el("featSaved").textContent};
      }
    """, {"data": page_data(future=False), "source": source})
    assert got["actual"] == got["expected"], "repair only the tool's old geometry type"
    assert got["persisted"] == got["expected"] and got["saved"] == "saved"
    assert got["exported"] and ms.validate(got["actual"], "Ascent").errors == []


@pytest.mark.parametrize("uv", [[[3125, 3125]], [[3125, 3125], [10001, 3125]]])
def test_legacy_line_repair_leaves_malformed_geometry_pending(uv):
    got = run_page("""
      function run(p) {
        const page = openPage(p.data, {map: "Ascent"});
        const old = page.plain(page.api.fe.Ascent.mf);
        old.features.find(f => f.id === "feature-1").states.find(s => s.name === "closed").sight = [
          {geometry: {type: "line", uv: p.uv}, bounds: {ref: "unresolved"}}
        ];
        page.api.commit(old, true);
        const fresh = page.reopen();
        fresh.click("featExport");
        return {expected: old, actual: fresh.plain(fresh.api.fe.Ascent.mf), exported: fresh.downloads.length > 0};
      }
    """, {"data": page_data(future=False), "uv": uv})
    assert got["actual"] == got["expected"] and not got["exported"]


def test_the_tagger_reads_a_maps_heights_from_an_export_when_given_one(tmp_path):
    import numpy as np

    sys.path.insert(0, str(WEBAPP / "scripts"))
    import control_tagger

    from app.control import heights as hc

    floors = np.full((128, 128, hc.MAX_FLOORS), -1, np.int16)
    floors[40, 40, 0] = 7
    asset = hc.HeightAsset(floors, np.zeros_like(floors), floors[..., 0] >= 0, np.zeros((128, 128), bool),
                           np.zeros((0, 5), np.int32), {"origin_z": -130})
    hc.save_asset(tmp_path / "Toy.height.npz", asset)
    (tmp_path / "index.json").write_text(json.dumps({"maps": {"Toy": {}}}), encoding="utf-8")
    assert control_tagger.floor_data('Toy', tmp_path)['flat'] is True, 'no committed heights'
    got = control_tagger.floor_data("Toy", tmp_path, heights_dir=tmp_path)
    assert got["height_sha"] == asset.digest and got["origin_z"] == -130
    assert control_tagger.floor_data('Other', tmp_path, heights_dir=tmp_path)['flat'] is True
