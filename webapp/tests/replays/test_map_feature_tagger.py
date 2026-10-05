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
    const merged = F.mergeCatalogue(p.current, ok.catalogue, "current");
    out.mergeConflicts = merged.conflicts; out.mergedKeepsCurrent = JSON.stringify(merged.catalogue.maps.Ascent) === JSON.stringify(p.current.maps.Ascent);
    out.mergedAddsAtlantis = !!merged.catalogue.maps.Atlantis;
    out.mergedIncoming = F.mergeCatalogue(p.current, ok.catalogue, "incoming").catalogue;
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
    assert got["mergeConflicts"] == ["Ascent"] and got["mergedKeepsCurrent"] and got["mergedAddsAtlantis"]
    assert got["mergedIncoming"]["maps"]["Ascent"] == CATALOGUE["maps"]["Ascent"]


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
