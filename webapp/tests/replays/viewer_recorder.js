"use strict";

// Deterministic canvas call recorder. Numbers retain their full JS precision; images have stable ids.
function mockCtx(log) {
  const defaults = {fillStyle: "#000000", strokeStyle: "#000000", globalAlpha: 1,
    font: "10px sans-serif", lineWidth: 1, textAlign: "start", textBaseline: "alphabetic",
    lineDashOffset: 0, globalCompositeOperation: "source-over", lineCap: "butt", lineJoin: "miter",
    miterLimit: 10, imageSmoothingEnabled: true, shadowBlur: 0, shadowColor: "rgba(0, 0, 0, 0)",
    shadowOffsetX: 0, shadowOffsetY: 0, filter: "none"};
  const c = Object.assign({}, defaults), stack = [];
  let tf = [1, 0, 0, 1, 0, 0], dash = [], gradientId = 0;
  const serial = value => value && typeof value === "object"
    ? (value.recordingId ? {image: value.recordingId} : JSON.parse(JSON.stringify(value))) : value;
  const state = () => Object.fromEntries(Object.keys(defaults).map(k => [k, serial(c[k])]));
  const rec = (op, args) => log.push({op, args: Array.from(args, serial), tf: tf.slice(),
    style: state(), dash: dash.slice()});
  for (const op of ["beginPath", "closePath", "moveTo", "lineTo", "arc", "rect", "fill", "stroke",
                    "fillRect", "strokeRect", "clearRect", "fillText", "strokeText", "drawImage", "clip"]) {
    c[op] = function () { rec(op, arguments); };
  }
  c.setTransform = function () { tf = Array.from(arguments); rec("setTransform", arguments); };
  c.save = function () { rec("save", []); stack.push({style: Object.assign({}, c), tf: tf.slice(), dash: dash.slice()}); };
  c.restore = function () {
    const saved = stack.pop();
    if (saved) { Object.assign(c, saved.style); tf = saved.tf; dash = saved.dash; }
    rec("restore", []);
  };
  c.setLineDash = function (value) { dash = value.slice(); rec("setLineDash", [value]); };
  c.getLineDash = function () { rec("getLineDash", []); return dash.slice(); };
  c.measureText = function (text) {
    rec("measureText", [text]);
    const m = /(\d+(?:\.\d+)?)px/.exec(c.font);
    return {width: String(text).length * (m ? Number(m[1]) : 10) * 0.6};
  };
  for (const op of ["createRadialGradient", "createLinearGradient"]) {
    c[op] = function () {
      rec(op, arguments);
      const gradient = {gradient: ++gradientId, stops: []};
      gradient.addColorStop = function (offset, color) {
        rec("addColorStop", [gradient.gradient, offset, color]); gradient.stops.push([offset, color]);
      };
      return gradient;
    };
  }
  return c;
}

function tip() {
  return {hidden: true, textContent: "", style: {}, offsetWidth: 10, offsetHeight: 10,
    parentNode: {getBoundingClientRect: () => ({width: 512, height: 512})}};
}

function still(u, v, yaw = 0) {
  return [{t0: 0, u: [u, 0], v: [v, 0], yaw: [yaw, 0]}];
}

function simpleBlob() {
  return {v: 1, round: 1, hz: 0.01, t_end: 100, kills: [], util: [],
    players: [{slot: 1, side: "A", agent: "Omen"}, {slot: 6, side: "B", agent: "Sova"}],
    tracks: {"1": still(2400, 3600, 25), "6": still(8000, 7200, 210)},
    alive: {"1": [[0, null, "spawn", []]], "6": [[0, null, "spawn", []]]}};
}

function environment(replayPath) {
  const path = require("path");
  global.getComputedStyle = () => ({getPropertyValue: () => ""});
  // The golden's replay.js lives in a temp directory; load companion modules from the working tree.
  const staticDir = path.resolve(__dirname, "../../app/static/js");
  global.ReplayGaps = require(path.join(staticDir, "replay_gaps.js"));
  global.ReplayControl = require(path.join(staticDir, "replay_control.js"));
  const R = require(replayPath);
  return R;
}

function makeViewer(R, blob = simpleBlob(), rotation, view = {k: 1, ox: 0, oy: 0}) {
  const log = [], v = Object.create(R.ReplayViewer.prototype), extras = R.extrasFromUtil(blob.util);
  const options = {};
  if (rotation !== undefined) options.rotation = rotation;
  Object.assign(v, {options, turns: ((options.rotation | 0) % 4 + 4) % 4, icons: {}, linked: null,
    t: 10.1, number: blob.round, hover: null, highlight: null, view,
    layers: {names: true, abilities: true, projectiles: true, tracers: true, cones: true,
      control: false, gaps: false, features: true, areas: false, aim: false},
    canvas: {width: 1024, height: 1024, setAttribute() {},
      getBoundingClientRect: () => ({left: 12, top: 20, width: 512, height: 512})},
    map: {recordingId: "minimap", complete: true, naturalWidth: 256},
    ui: {state: {}, hud: {hidden: true, innerHTML: ""}, tip: tip()},
    control: null, controlCache: null,
    current: {blob, extras, tracks: R.decodeRound(blob), state: R.indexRoundState(blob, extras.abilities)}});
  v.ctx = mockCtx(log);
  return {v, log};
}

// Four wholly synthetic rounds covering the zero-turn draw paths, including both icon branches.
function scenes() {
  return [0, 1, 2, 3].map(i => {
    const blob = simpleBlob();
    blob.round = i + 1;
    blob.tracks["1"] = still(2400 + i * 300, 3600 + i * 200, 25 + i * 40);
    blob.util = [
      {k: "ability", kind: "GameObject", code: "Wraith", name: "4_Smoke", by: 1,
        t: 5, t1: 20, u: 5000, v: 4000, full: 15},
      {k: "ability", kind: "GameObject", code: "Gumshoe", name: "E_PossessableCamera", by: 6,
        t: 5, t1: 30, u: 7200, v: 6500},
      {k: "status", by: 6, target: 1, status: "concussed", t: 9, t1: 12, code: "Iris", name: "Concuss"},
      {k: "shot", by: 1, t: 10, u: 2400, v: 3600, u1: 4200, v1: 3600}
    ];
    blob.kills = [{i: 0, t: 10, killer: 1, victim: 6, u: 8000, v: 7200, weapon: "Vandal", gain: 0.4, loss: 0.3}];
    blob.player_state = {version: 1, vitals: {"1": [{t: 5, life: 0, hp: 70, sh: 20, mhp: 100, msh: 50}]},
      damage_taken: {"1": [5]}, spike: [{t: 0, s: "carried", slot: 1}]};
    // Later scenes exercise dropped/planted markers as well as the carrier badge.
    if (i === 2) blob.player_state.spike.push({t: 8, s: "dropped", u: 3500, v: 5000});
    if (i === 3) blob.util.push({k: "ability", kind: "Bomb", t: 8, t1: null, by: 1,
      u: 6000, v: 6000, defuses: []});
    blob.map_features = {v: 1, status: "decoded", features: [
      {key: "door", name: "Test door", uv: [6500, 2500], initial: "closed", events: []}
    ]};
    return {blob, view: i % 2 ? {k: 1.6, ox: 80, oy: 120} : {k: 1, ox: 0, oy: 0}};
  });
}

function recordRounds(replayPath, rotation, highlight = null) {
  const R = environment(replayPath);
  return scenes().map(({blob, view}) => {
    const {v, log} = makeViewer(R, blob, rotation, view);
    v.options.agentIcon = () => null;
    v.icons.Omen = {recordingId: "agent-omen", complete: true, naturalWidth: 64};
    v.options.abilityIcon = () => null;
    v.layers.areas = true;
    v.areas = {approximate: true, bands: {}};
    v.areasPaint = {key: "true/", canvas: {recordingId: "areas"},
      labels: [{name: "Test area", u: 3000, v: 6000}]};
    v.gaps = true; v.layers.gaps = true; v.gapsHover = null;
    v.gapsReady = {[blob.round]: {chokes: {"3": {name: "Test choke", x: 500, y: 300}}, rows: [
      {seq: 0, kind: "predicted", victim_slot: 1, t_open: 9, t_close: 15, flicker: false,
        cause: "open_timing", cause_detail: {}, choke_seq: [3],
        route: [[[8, 400, 200], [10, 500, 300]]], candidate_slots: [6], spot_xy: [500, 300],
        victim_xy: [520, 320], released_xy: null, used: null, merged: 0, context: {t_round: 9},
        stood_by: null, shot_by: null, killed_by: null, killed_at: null}
    ]}};
    // Warm offscreen layers: exercise real drawControl without rasterizing an unrelated control fixture.
    v.control = {}; v.layers.control = true; v.unknownView = "both";
    v.highlight = highlight;
    v.controlPaint = [blob.round, 0, true, highlight, null, "both", false].join(":");
    v.controlCache = {ready: () => ({parsed: {times: [0], unknown_a: [], unknown_b: []}})};
    v.controlCanvas = name => ({canvas: {recordingId: "control-" + name}});
    v.draw();
    return {round: blob.round, calls: log, layout: v.layout, hits: v.hits};
  });
}

module.exports = {mockCtx, tip, simpleBlob, environment, makeViewer, recordRounds, scenes};
