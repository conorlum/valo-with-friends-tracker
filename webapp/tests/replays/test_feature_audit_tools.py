"""Owner tooling: compiler source identity and offline worksheet input contracts."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

WEBAPP = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('diagnose_map_features', WEBAPP / 'scripts' / 'diagnose_map_features.py')
DIAG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIAG)
NODE = shutil.which('node') or r'C:\Program Files\nodejs\node.exe'


def test_diagnostic_command_uses_exact_private_source_and_explicit_flat_context():
    tags = json.loads((WEBAPP / 'app' / 'static' / 'data' / 'control' / 'tags.json').read_text())
    tags['maps']['Ascent']['map_features'] = {'version': 1, 'features': [], 'triggers': [], 'routes': [], 'bundles': [], 'next_id': 1}
    raw = json.dumps(tags).encode()
    report = DIAG.diagnose(raw, 'Ascent')
    assert report['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert report['counts'] == {'total_tagged': 0, 'placeable': 0, 'pending': 0}
    assert report['context']['height_mode'] == 'flat' and report['height'] == 'flat'
    assert report['context']['site_active_height_verified'] is False


def test_diagnostic_command_refuses_changed_map_image():
    tags = {'maps': {'Ascent': {'image_sha': 'changed', 'map_features': {'version': 1, 'features': [], 'triggers': [], 'routes': [], 'bundles': [], 'next_id': 1}}}}
    with pytest.raises(ValueError, match='Map image changed'):
        DIAG.diagnose(json.dumps(tags).encode(), 'Ascent')


def test_diagnostic_command_refuses_structural_errors_before_compilation():
    tags = {'maps': {'Ascent': {'map_features': {'version': 99}}}}
    with pytest.raises(ValueError, match='Structural errors'):
        DIAG.diagnose(json.dumps(tags).encode(), 'Ascent')


def test_exact_height_diagnostics_expose_newly_pending_after_multifloor_rebuild(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from map_feature_artifact_toys import source_case, geometry_case
    from app.control.heights import save_asset
    flat = geometry_case(flat=True)
    monkeypatch.setattr(DIAG.cg, 'masks', lambda *args: SimpleNamespace(sight=flat.sight, walk=flat.walk_px))
    raw = json.dumps({'maps': {'Summit': source_case()}}).encode()
    single, multi = tmp_path / 'single.npz', tmp_path / 'multi.npz'
    save_asset(single, geometry_case().heights); save_asset(multi, geometry_case(multi=True).heights)
    first = DIAG.diagnose(raw, 'Summit', height=single)
    second = DIAG.diagnose(raw, 'Summit', height=multi, previous=first['snapshot'])
    assert first['context']['height_mode'] == 'exact_file'
    assert first['context']['site_active_height_verified'] is False
    assert second['comparison']['newly_pending'] == ['feature-1']
    assert any(r['code'] == 'multi_floor' for r in second['features'][0]['reasons'])
    assert all(f['runtime'] != 'active' for f in second['features'])


@pytest.mark.parametrize('change, message', [
    ({'height': 'wrong'}, 'height'), ({'compiler': 999}, 'versions'),
    ({'editor_entry': {}}, 'identity'), ({'context': {'image_sha': 'wrong'}}, 'image identity')])
def test_tagger_refuses_stale_or_mismatched_compiler_attachment(change, message):
    spec = importlib.util.spec_from_file_location('control_tagger', WEBAPP / 'scripts' / 'control_tagger.py')
    tagger = importlib.util.module_from_spec(spec); spec.loader.exec_module(tagger)
    tags = json.loads((WEBAPP / 'app' / 'static' / 'data' / 'control' / 'tags.json').read_text())
    report = DIAG.diagnose(json.dumps(tags).encode(), 'Ascent')
    maps = tagger.build(tags, {}, ['Ascent'])
    assert 'Compiler report' in tagger.render(tags, maps, diagnostics=report)
    report.update(change)
    with pytest.raises(ValueError, match=message):
        tagger.render(tags, maps, diagnostics=report)


def test_worksheet_ui_rejects_bad_numeric_and_evidence_inputs():
    script = r'''const H = require(process.argv[1]);
      const good = [H.numberOrNull(""),H.numberOrNull("0"),H.numberOrNull("30.5"),H.parseLines("1, 20"),H.nextSceneId([{observation_id:"scene-1"},{observation_id:"scene-3"}])];
      const bad = ["NaN","Infinity","-1"].map(x => {try {H.numberOrNull(x);return false;}catch {return true;}});
      const lines = ["0","1.5","1,","-3"].map(x => {try {H.parseLines(x);return false;}catch {return true;}});
      process.stdout.write(JSON.stringify({good,bad,lines}));'''
    completed = subprocess.run([NODE, '-e', script, str(WEBAPP / 'scripts' / 'feature_audit_review.js')], capture_output=True, text=True, check=True)
    got = json.loads(completed.stdout)
    assert got['good'] == [None, 0, 30.5, [1, 20], 'scene-2']
    assert all(got['bad']) and all(got['lines'])


def test_offline_review_page_download_add_scene_and_transactional_restore():
    script = r'''const fs = require("fs"), vm = require("vm"), {element} = require(process.argv[1]);
      const report = {identity:{audit_sha256:"fixture"},features:[{id:"feature-9",name:"Door",states:["open","closed"],events:["switch"]}],candidates:[],scan:{rows:100}};
      const original = {format:"map-feature-observations",version:1,audit_sha256:"fixture",clock:"export_time_ms",observations:[{
        observation_id:"scene-1",feature_id:"feature-9",state:"",action:"",time_ms:null,uncertainty_ms:null,review:"unverified",candidate_key:null,evidence_lines:[],notes:""}]};
      const elements = {}, doc = {_downloads:[],_blobs:{}, createElement(tag){return element(doc,null,tag);},
        getElementById(id){return elements[id] || (elements[id] = element(doc,id));}};
      doc.getElementById("audit-data").textContent = JSON.stringify({report,worksheet:original});
      const context = {document:doc,console,setTimeout(fn){fn();},Blob:class {constructor(parts){this.text=parts.join("");}},
        URL:{createObjectURL(b){doc._blobs.fixture=b.text;return "fixture";},revokeObjectURL(){}}};
      vm.createContext(context); vm.runInContext(fs.readFileSync(process.argv[2],"utf8"),context);
      function walk(el){return [el,...(el.children||[]).flatMap(walk)];}
      function button(text){return walk(doc.getElementById("observation-actions")).find(e=>e.textContent===text);}
      button("Download observations.json").click(); const first=JSON.parse(doc._downloads.at(-1).text);
      button("Add another scene").click(); button("Download observations.json").click(); const second=JSON.parse(doc._downloads.at(-1).text);
      const restore=walk(doc.getElementById("observation-actions")).find(e=>e.type==="file");
      const bad=structuredClone(first);bad.observations[0].state="wrong";
      restore.files=[{text:async()=>JSON.stringify(bad)}];restore.dispatch("change");
      setImmediate(()=>{const error=doc.getElementById("review-message").textContent;
        button("Download observations.json").click();const after=JSON.parse(doc._downloads.at(-1).text);
        process.stdout.write(JSON.stringify({first,second,error,after}));});'''
    completed = subprocess.run([NODE, '-e', script, str(WEBAPP / 'tests' / 'replays' / 'tagger_page.js'),
                                str(WEBAPP / 'scripts' / 'feature_audit_review.js')], capture_output=True, text=True, check=True)
    got = json.loads(completed.stdout)
    assert got['first']['observations'][0]['review'] == 'unverified'
    assert got['first']['observations'][0]['time_ms'] is None
    assert [o['observation_id'] for o in got['second']['observations']] == ['scene-1', 'scene-2']
    assert 'Unknown action or state' in got['error']
    assert got['after'] == got['second']
