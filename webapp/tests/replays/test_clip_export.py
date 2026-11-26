"""Clip export contracts with synthetic rows, WebCodecs fakes and the real MP4 muxer."""
import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

WEBAPP = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")

HARNESS = r"""
const assert = require('assert/strict'), fs = require('fs'), vm = require('vm');
const C = require(process.argv[1] + '/app/static/js/clip_export.js');
vm.runInThisContext(fs.readFileSync(process.argv[1] + '/app/static/vendor/mp4-muxer-5.2.2.js', 'utf8'));
global.Mp4Muxer = Mp4Muxer;
const texts = [], draws = [], encoders = [], frames = [], loads = [], clips = [], configs = [];
let ready = Promise.resolve(), load = n => Promise.resolve({round:n,t_end:100}), onDraw = () => {}, flushWait = null;
class Chunk {
  constructor(frame, key) { Object.assign(this, {timestamp:frame.timestamp, duration:frame.duration,
    type:key ? 'key' : 'delta', byteLength:100}); }
  copyTo(dest) { dest.fill(0); }
}
global.EncodedVideoChunk = Chunk;
const originalChunk = Mp4Muxer.Muxer.prototype.addVideoChunk;
let chunks = 0;
Mp4Muxer.Muxer.prototype.addVideoChunk = function(chunk, meta) {
  assert(Number.isFinite(chunk.duration)); assert.equal(meta.marker, 'metadata'); chunks++;
  return originalChunk.call(this, chunk, meta);
};
class Encoder {
  static async isConfigSupported(config) { configs.push(config); return {supported:true, config}; }
  constructor(callbacks) { this.callbacks = callbacks; this.encodeQueueSize = 0; this.pending = []; encoders.push(this); }
  configure(config) { this.config = config; }
  encode(frame, opts) {
    this.encodeQueueSize++;
    this.pending.push(new Promise(resolve => setTimeout(() => {
      this.encodeQueueSize--;
      if (!this.closed) this.callbacks.output(new Chunk(frame, opts.keyFrame), {marker:'metadata',
        decoderConfig:{codec:this.config.codec, description:new Uint8Array([1,100,0,40,255,225,0,0,1,0,0])}});
      resolve();
    }, 3)));
  }
  async flush() { await Promise.all(this.pending); if (flushWait) await flushWait; }
  close() { this.closed = true; }
}
global.VideoEncoder = Encoder;
global.VideoFrame = class {
  constructor(canvas, opts) { Object.assign(this, opts); frames.push(this); }
  close() { this.closed = true; }
};
function element() {
  return {value:'', checked:false, hidden:false, disabled:false, textContent:'', handlers:{},
    addEventListener(k, fn) { this.handlers[k] = fn; }, click() { return this.onclick?.() ?? this.handlers.click?.(); },
    getContext() { return {drawImage(){}, fillRect(){}, fillText(t){texts.push(t);},
      measureText(t){return {width:t.length * 8};}}; } };
}
let downloads = 0;
global.document = {createElement(tag) { const el = element();
  if (tag === 'a') el.click = () => { downloads++; }; return el; }};
global.URL.createObjectURL = () => 'blob:fixture'; global.URL.revokeObjectURL = () => {};
global.Replay = {
  prepareRound(blob) { return {blob, extras:{}, tracks:{}, state:{}}; },
  clipViewer(options, round, extra) {
    assert.equal(options.themeRoot, viewer.root);
    const clip = {ready, round, extra, drawAt(t, ctx, opts) {
      draws.push(t); assert.equal(opts.nameOf(6), 'the enemy Sova'); onDraw(t, round); }};
    clips.push(clip); return clip;
  },
  momentLink(uuid,n,a,b) {return `/replays/${uuid}?round=${n}&t=${a.toFixed(2)}&until=${b.toFixed(2)}`;}
};
const nodes = {};
for (const key of ['in','out','in-now','out-now','speed','width','cut','control','names','health',
  'caption','estimate','size','note','message','export','cancel','progress','link','close']) nodes['#clip-'+key] = element();
const panel = {hidden:true, querySelector: s => nodes[s]};
const viewer = {root:{querySelector:s => s === '[data-clip-panel]' ? panel : null}, t:10, number:4,
  current:{blob:{round:4,t_end:100}}, layers:{names:true,control:false},
  options:{loadRound(n){loads.push(n); return load(n);},
    replayData:{match:{uuid:'fixture'}, clip_names:{1:'Main',6:'the enemy Sova'}, round_scores:{4:[3,1]}}}};
const req = {round:4,t_in:2,t_out:4,speed:1,width:512,cut:false,segments:[],caption:'1v3 clutch',
  control:false,names:true,health:true};
const tick = () => new Promise(r => setTimeout(r, 0));
function deferred() { let resolve, reject; const promise = new Promise((a,b)=>{resolve=a;reject=b;}); return {promise,resolve,reject}; }
(async () => { BODY })().catch(e => {console.error(e.stack); process.exitCode=1;});
"""


def node(body):
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run([NODE, "-e", HARNESS.replace("BODY", body), str(WEBAPP)],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr


def test_pure_contracts():
    node("""
const times = C.frameTimes(req); assert.equal(times.length,60); assert.equal(times[0],2);
assert.equal(times[1],2+1/30); assert.deepEqual(times,C.frameTimes(req));
assert.equal(C.frameTimes({...req,speed:2}).length,30);
const cut = C.frameTimes({...req,cut:true,t_out:6,segments:[[2,3],[5,6]]});
assert.equal(cut.length,60); assert.equal(cut[30],5);
assert.deepEqual(C.defaultWindow(1,100),[0,8]); assert.deepEqual(C.defaultWindow(98,100),[95,100]);
assert.equal(C.evenSize(721),722);
assert.equal(C.captionText('1v3 clutch',14,[7,6]),'Round 14 · 7–6 · 1v3 clutch');
assert.equal(C.fileName({...req,round:14,t_in:27.5}),'round14-27.5s.mp4');
assert.equal(C.fileName({...req,round:14,t_in:27.4833333333}),'round14-27.5s.mp4');
assert.equal(C.fileName({...req,round:14,t_in:30}),'round14-30s.mp4');
assert.equal(C.fileName({...req,round:14,t_in:29.97}),'round14-30s.mp4');
assert.equal(C.estimateBytes({...req,t_out:6}),2*C.estimateBytes(req));
const ladder = {isConfigSupported:async c => ({supported:c.codec==='avc1.42001f'})};
assert.equal(await C.pickCodec(ladder,512,576),'avc1.42001f');
assert.equal(await C.pickCodec({isConfigSupported:async()=>({supported:false})},512,576),null);
assert.equal(await C.pickCodec(null,512,576),null);
""")


def test_runner_real_muxer_fixed_times_metadata_progress_and_private_names():
    node("""
const before = {layers:{...viewer.layers},t:viewer.t,current:viewer.current}, progress=[];
const blob = await C.exportClip(viewer,req,{onProgress:p=>progress.push({...p})});
assert.equal(blob.type,'video/mp4'); assert(blob.size>0);
assert.deepEqual(draws,C.frameTimes(req)); assert.equal(chunks,60);
assert(progress.length>=6); assert.equal(progress.at(-1).bytes,6000);
assert(progress.every((p,i)=>i===0 || p.bytes>=progress[i-1].bytes));
for (let i=0;i<frames.length;i++) { assert.equal(frames[i].timestamp,Math.round(i*1e6/30));
  assert(Number.isFinite(frames[i].duration)); assert(frames[i].closed); }
assert(texts.includes('Round 4 · 3–1 · 1v3 clutch'));
assert.deepEqual(viewer.layers,before.layers); assert.equal(viewer.t,before.t); assert.equal(viewer.current,before.current);
assert(encoders[0].closed);
""")


def test_round_is_frozen_and_ready_failure_prevents_frames():
    node("""
onDraw = (t,round) => {assert.equal(round.blob.round,2); viewer.number=3; viewer.current={blob:{round:3}};};
await C.exportClip(viewer,{...req,round:2,t_out:2.2},{}); assert.deepEqual(loads,[2]);
draws.length=0; ready=Promise.reject(Error('Image failed: fixture.png'));
await assert.rejects(C.exportClip(viewer,req,{}),/Image failed: fixture.png/); assert.equal(draws.length,0);
""")


@pytest.mark.parametrize("phase", ["load", "control", "ready", "codec", "flush", "frame"])
def test_cancel_at_every_await_and_next_export_succeeds(phase):
    node("""
const phase = 'PHASE', gate=deferred(), controller=new AbortController();
let requested={...req,round:2,t_out:2.4};
if(phase==='load') load=()=>gate.promise;
if(phase==='control') {requested.control=true; viewer.controlCache={get:()=>gate.promise};}
if(phase==='ready') ready=gate.promise;
if(phase==='codec') Encoder.isConfigSupported=()=>gate.promise;
if(phase==='flush') flushWait=gate.promise;
if(phase==='frame') onDraw=()=>{if(draws.length===5)setTimeout(()=>controller.abort(),0);};
const pending=C.exportClip(viewer,requested,{signal:controller.signal});
if(phase==='flush') {while(!encoders.length || draws.length<C.frameTimes(requested).length)await tick(); await tick();}
else if(phase!=='frame') await tick();
if(phase!=='frame')controller.abort();
assert.equal(await pending,null); assert.equal(downloads,0);
if(['load','control','ready','codec'].includes(phase))assert.equal(encoders.length,0);
else assert(encoders[0].closed);
if(phase==='frame')assert(draws.length<=6);
gate.resolve({round:2,t_end:100}); await tick();
ready=Promise.resolve(); load=n=>Promise.resolve({round:n,t_end:100}); flushWait=null; onDraw=()=>{};
Encoder.isConfigSupported=async()=>({supported:true});
const blob=await C.exportClip(viewer,req,{}); assert(blob.size>0); assert.equal(downloads,0);
""".replace("PHASE", phase))


def test_cut_clock_and_agent_caption():
    node("""
viewer.options.replayData.clip_names[1]='Jett';
await C.exportClip(viewer,{...req,t_in:8,t_out:21,cut:true,segments:[[8,11],[18,21]],
  caption:{summary:'1v3 clutch',slot:1}},{});
assert(texts.includes('0:10')); assert(texts.includes('0:20'));
assert(texts.some(t=>t.includes('Jett') && t.includes('1v3 clutch')));
""")


def test_panel_segments_estimates_support_and_download_lifecycle():
    node("""
const p=await C.openPanel(viewer,{t_in:8,t_out:21,segments:[[8,12.5],[18,21]],cut:true});
nodes['#clip-in'].value='10'; nodes['#clip-out'].value='19'; await p.refresh();
assert.deepEqual(p.request().segments,[[10,12.5],[18,19]]);
nodes['#clip-out'].value='12'; await p.refresh();
assert(nodes['#clip-cut'].disabled); assert.equal(p.request().cut,false);
assert.deepEqual(C.frameTimes(p.request()),C.frameTimes({...req,t_in:10,t_out:12,speed:1}));
nodes['#clip-cut'].checked=false; nodes['#clip-width'].value='1024'; nodes['#clip-in'].value='0'; nodes['#clip-out'].value='100'; await p.refresh();
assert(!nodes['#clip-note'].hidden); assert.equal(p.request().width,1024); assert.equal(p.request().speed,1);
Encoder.isConfigSupported=async()=>({supported:false}); await p.refresh();
assert(nodes['#clip-export'].disabled); assert(/H.264|supported/.test(nodes['#clip-message'].textContent));
Encoder.isConfigSupported=async()=>({supported:true}); nodes['#clip-out'].value='0.2'; await p.refresh();
flushWait=deferred(); const gate=flushWait; flushWait=gate.promise;
const pending=nodes['#clip-export'].click(); while(!encoders.length || draws.length<6)await tick();
nodes['#clip-cancel'].click(); await pending; gate.resolve(); flushWait=null; await tick(); assert.equal(downloads,0);
await nodes['#clip-export'].click(); assert.equal(downloads,1); assert(!nodes['#clip-export'].disabled);
""")


def test_template_is_opt_in_and_scripts_are_lazy():
    env = Environment(loader=FileSystemLoader(WEBAPP / "app/templates"))
    context = dict(replay={"map_name": "Ascent", "round_numbers": [1]}, linked=False,
                   control=None, gaps=False, demo_mode=False)
    plain = env.get_template("replays/_player.html").render(**context)
    enabled = env.get_template("replays/_player.html").render(**context, clip_export=True)
    assert "data-replay-export" not in plain and "data-clip-panel" not in plain
    assert "data-replay-export" in enabled and "data-clip-panel" in enabled
    env.globals["static_version"] = lambda: "test"
    env.filters["strip_tag"] = lambda value: value
    page = env.get_template("replays/replay.html").render(
        **context, replay_data={"match": {}, "rounds": {}, "players": {}}, start_round=1,
        start_t=None, until_t=None, viewer_slot=None, open_export=False)
    assert "clip_export.js" not in page and "mp4-muxer" not in page
    replay_js = (WEBAPP / "app/static/js/replay.js").read_text(encoding="utf-8")
    assert "/static/vendor/mp4-muxer-5.2.2.js" in replay_js
    assert "/static/js/clip_export.js" in replay_js
    assert "viewerOpenExport" in page
