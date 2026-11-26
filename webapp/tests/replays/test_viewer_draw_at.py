"""Frozen export frames preserve live rendering, UI and control buffers (synthetic inputs only)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEBAPP = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def node(body, payload=None):
    script = """
const assert = require('assert/strict');
const H = require(process.argv[2]), R = H.environment(process.argv[1]);
let input = '';
process.stdin.on('data', d => input += d).on('end', async () => {
  try {
    const p = JSON.parse(input);
    await (async () => { BODY })();
    process.stdout.write('ok');
  } catch (e) { console.error(e.stack); process.exitCode = 1; }
});
""".replace("BODY", body)
    done = subprocess.run(
        [NODE, "-e", script, str(WEBAPP / "app/static/js/replay.js"),
         str(Path(__file__).with_name("viewer_recorder.js"))],
        input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == "ok"


@pytest.mark.parametrize("rotation", ["undefined", "0", "1"])
def test_draw_matches_baseline(rotation):
    baseline = json.loads((WEBAPP / "tests/fixtures/replay/draw_baseline.json").read_text(encoding="utf-8"))
    node("assert.deepEqual(JSON.parse(JSON.stringify(H.recordRounds(process.argv[1], p.rotation === 'undefined' ? undefined : +p.rotation))), p.rows);",
         {"rotation": rotation, "rows": baseline[rotation]})


@pytest.mark.parametrize("rotation", [0, 1])
def test_draw_at_matches_draw_and_preserves_live_state(rotation):
    node("""
for (const {blob, view} of H.scenes()) {
  const {v, log} = H.makeViewer(R, blob, p, view), attrs = {};
  v.canvas.setAttribute = (k, value) => { attrs[k] = value; };
  v.draw();
  const before = {t: v.t, hits: v.hits, layout: v.layout, state: v.ui.state.textContent,
    attrs: {...attrs}, tip: JSON.stringify(v.ui.tip)};
  const out = [];
  assert.equal(v.drawAt(10.1, H.mockCtx(out), {}), undefined);
  assert.deepEqual(out, log);
  assert.equal(v.t, before.t); assert.equal(v.hits, before.hits); assert.equal(v.layout, before.layout);
  assert.equal(v.ui.state.textContent, before.state); assert.deepEqual(attrs, before.attrs);
  assert.equal(JSON.stringify(v.ui.tip), before.tip);
  v.hover = {x: 0, y: 0}; v.showTip = () => { throw Error('tooltip touched'); };
  const earlier = []; v.drawAt(5, H.mockCtx(earlier));
  assert.equal(v.t, 10.1);
  v.hover = null; v.t = 5; log.length = 0; v.ctx = H.mockCtx(log); v.draw();
  assert.deepEqual(earlier, log);
}
""", rotation)


def test_frame_overrides_health_names_and_restores_on_exception():
    node("""
const {v} = H.makeViewer(R, H.scenes()[0].blob);
v.linked = {players: {'1': {name: 'Fixture#TEST', team: 'team-1'}}};
const layers = {...v.layers}, nameOf = v.nameOf;
function frame(opts) { const log = []; v.drawAt(10.1, H.mockCtx(log), opts); return log; }
const normal = frame({});
assert(normal.some(c => c.op === 'fillRect' && c.style.fillStyle === '#3a3a42'));
assert(!frame({health: false}).some(c => c.op === 'fillRect' && c.style.fillStyle === '#3a3a42'));
assert(normal.some(c => c.op === 'fillText' && c.args[0] === 'Fixture'));
assert(!frame({names: false}).some(c => c.op === 'fillText' && c.args[0] === 'Fixture'));
v.linked = null;
assert(frame({nameOf: () => 'the enemy Sova'}).some(c => c.op === 'fillText' && c.args[0] === 'the enemy Sova'));
assert.deepEqual(v.layers, layers); assert.equal(v.nameOf, nameOf);
v.drawAbilities = () => { throw Error('paint failed'); };
assert.throws(() => v.drawAt(5, H.mockCtx([]), {names: false, control: true}), /paint failed/);
assert.equal(v.t, 10.1); assert.deepEqual(v.layers, layers); assert.equal(v.nameOf, nameOf);
assert(!v._offscreen);
""")


CANVAS_ENV = """
global.document = {createElement: tag => {
  assert.equal(tag, 'canvas');
  const ctx = H.mockCtx([]);
  ctx.getImageData = (x, y, w, h) => ({data: new Uint8ClampedArray(w*h*4)});
  ctx.createImageData = (w, h) => ({data: new Uint8ClampedArray(w*h*4)});
  ctx.putImageData = () => {};
  return {width: 1024, height: 1024, getContext: () => ctx};
}};
"""


def test_control_buffers_and_dom_are_isolated():
    node(CANVAS_ENV + """
const {v, log} = H.makeViewer(R);
v.control = {}; v.layers.control = true; v.unknownView = 'off';
v.controlCache = {ready: () => ({parsed: {times: [0, 8], walk: [0]},
  cursor: {states: tick => new Uint8Array([tick + 1])}})};
v.draw();
const images = v.controlImages, paint = v.controlPaint, shares = v.controlShares;
images.states.canvas.recordingId = 'control-states';
const pixels = images.states.data.data.slice();
const bar = v.renderCtlBar;
v.renderCtlBar = () => { throw Error('bar called'); };
v.ui = new Proxy({}, {get() { throw Error('DOM accessed'); }});
v.drawAt(5, H.mockCtx([]));
assert.equal(v.controlImages, images); assert.equal(v.controlPaint, paint); assert.equal(v.controlShares, shares);
assert.deepEqual(images.states.data.data, pixels);
const off = []; v.drawAt(5, H.mockCtx(off), {control: false});
assert(!off.some(c => c.op === 'drawImage' && c.args[0].image === 'control-states'));
v.renderCtlBar = bar; v.ui = {state: {}};
log.length = 0; v.draw();
assert(log.some(c => c.op === 'drawImage' && c.args[0].image === 'control-states'));
assert.equal(v.controlImages, images); assert.equal(v.controlPaint, paint);
""")


@pytest.mark.parametrize("failure", ["map", "agent", "ability", "sight"])
def test_clip_asset_failures_include_url_except_missing_sight(failure):
    node(CANVAS_ENV + """
const urls = [], themeRoot = {};
global.Image = class {
  set src(url) {
    urls.push(url);
    queueMicrotask(() => {
      if (url === p) { this.onerror(); return; }
      this.complete = true; this.naturalWidth = this.naturalHeight = 2; this.onload();
    });
  }
};
const v = R.clipViewer({themeRoot, mapImage: 'map', sightMask: 'sight', agentIcon: () => 'agent',
  abilityIcons: {Omen: {'Dark Cover': 'ability'}}}, R.prepareRound(H.scenes()[0].blob));
if (p === 'sight') { await v.ready; assert.equal(v.outlines, null); }
else await assert.rejects(v.ready, e => e instanceof Error && e.message.includes(p));
assert(urls.includes(p));
""", failure)


@pytest.mark.parametrize("delayed", ["map", "agent", "ability", "sight"])
def test_clip_waits_for_assets_and_uses_theme_root(delayed):
    node(CANVAS_ENV + """
const themeRoot = {}, pending = [], loaded = [];
global.getComputedStyle = el => {
  assert.equal(el, themeRoot); return {getPropertyValue: () => ''};
};
global.Image = class {
  set src(url) {
    loaded.push(url);
    const finish = () => { this.complete = true; this.naturalWidth = this.naturalHeight = 2; this.onload(); };
    if (url === p) pending.push(finish); else queueMicrotask(finish);
  }
};
assert.throws(() => R.clipViewer({mapImage: 'map'}, R.prepareRound(H.simpleBlob())), /themeRoot/);
const control = {status: 'ok', parsed: {times: [], header: {}}};
const blob = H.scenes()[0].blob;
blob.util[0].agent = 'Omen'; blob.util[0].thrown = {t0: 9, t1: 11, u: 2400, v: 3600};
const v = R.clipViewer({themeRoot, mapImage: 'map', sightMask: 'sight', agentIcon: () => 'agent',
  abilityIcons: {Omen: {'Dark Cover': 'ability'}}}, R.prepareRound(blob),
  {control, layers: {names: false}});
let ready = false; v.ready.then(() => { ready = true; });
await new Promise(resolve => setImmediate(resolve));
assert.equal(ready, false); assert(pending.length);
pending.forEach(f => f()); await v.ready;
assert(v.mapDark); assert(v.outlines); assert(v.sight);
assert.equal(v.controlCache.ready(v.number), control); assert.equal(v.controlCache.ready(99), null);
const calls = []; v.drawAt(10.1, H.mockCtx(calls));
assert(calls.some(c => c.op === 'drawImage')); assert.equal(v.layers.names, false);
assert.equal(loaded.filter(url => url === 'ability').length, 1, 'no lazy icon request after ready');
""", delayed)


def test_prepare_round_matches_original_decode_and_fetch():
    node("""
const blob = R.withSiteData({db: {winner: 'A'}, stats: {}, alive_steps: [], annotations: [],
  kills: {'0': {gain: 0.7}}}, H.scenes()[0].blob);
blob.arena = {'1': [{t0: 3, x: [10, 1], y: [20, 2], yaw: [90, 1]}]};
blob.util.push(
  {k: 'ability', kind: 'GameObject', code: 'Mage', name: 'Q_WallManager', by: 1, t: 5, t1: 20},
  {k: 'ability', kind: 'Projectile', code: 'Mage', name: 'Q_Wall', by: 1, t: 5, t1: 6,
   flight: [[5, 100, 200, 0], [6, 200, 300, 0]]});
// The pre-refactor fetchRound decode, kept inline as the reference.
let extras = blob.extras || R.extrasFromUtil(blob.util);
const traces = R.traceWalls(extras.abilities), state = R.indexRoundState(blob, extras.abilities);
extras = Object.assign({}, extras, {abilities: extras.abilities.concat(R.fragmentZones(extras.abilities))});
const expected = {blob, tracks: R.decodeRound(blob), arena: R.decodeArena(blob), extras, traces, state};
assert(Object.keys(traces).length, 'the reference must exercise a traced wall');
assert.deepEqual(R.prepareRound(blob), expected);
const {v} = H.makeViewer(R); v.cache = {}; v.options.loadRound = () => blob;
assert.deepEqual(await v.fetchRound(blob.round), expected);
assert(v.fetchRound.toString().includes('prepareRound'));
blob.extras = extras; assert.deepEqual(R.prepareRound(blob).tracks, expected.tracks);
""")


@pytest.mark.parametrize("status", ["not_ready", "failed", "unavailable", "error", "aborted", None])
def test_clip_cache_hides_non_ok_control_from_painters(status):
    node(CANVAS_ENV + """
global.Image = class {set src(url) {
  this.complete=true; this.naturalWidth=this.naturalHeight=2; queueMicrotask(()=>this.onload());
}};
const v=R.clipViewer({themeRoot:{},mapImage:'map'}, R.prepareRound(H.simpleBlob()),
  {control:p === null ? null : {status:p},layers:{control:true}});
await v.ready;
assert.equal(v.controlCache.ready(v.number),null);
v.controlView='A';
assert.equal(v.knowingGroup(v.controlCache.ready(v.number)),null);
v.drawAt(5,H.mockCtx([]));
""", status)


@pytest.mark.parametrize("label", [None, "", "#", "Fixture"])
@pytest.mark.parametrize("linked", [False, True])
def test_unlabelled_slots_have_no_name_plate_or_text(label, linked):
    node("""
const {v}=H.makeViewer(R);
if(p.linked) v.linked={players:{}};
v.nameOf=()=>p.label;
const log=[], layout=[], opts={health:false,nameOf:v.nameOf};
v.drawBlocks(H.mockCtx(log),20,[],[{slot:1,x:300,y:300,dim:1}],layout,opts);
const visible=!!(p.label && p.label.split('#')[0]);
assert.equal(log.some(c=>c.op==='fillRect'),visible);
assert.equal(log.some(c=>c.op==='fillText'),visible);
assert.equal(layout.length,visible ? 1 : 0);
// Exercise the complete unlinked/linked export draw path as well.
const calls=[]; v.drawAt(5,H.mockCtx(calls),opts);
if(!visible) {
  assert(!calls.some(c=>c.op==='fillRect' && c.style.fillStyle==='rgba(10, 10, 12, 0.78)'));
  assert(!calls.some(c=>c.op==='fillText' && c.args[0]===''));
}
""", {"label": label, "linked": linked})
