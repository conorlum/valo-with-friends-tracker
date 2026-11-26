/* Lazy, client-only MP4 export. The live viewer is never used to paint a clip. */
(function (global) {
  "use strict";
  var FPS = 30, BAR = 64, LIMIT = 10000000, ABORT = {};
  function evenSize(w) { return Math.ceil(w / 2) * 2; }
  function defaultWindow(t, end) { return [Math.max(0, t - 3), Math.min(end, t + 7)]; }
  function intersect(segments, a, b) {
    return (segments || []).map(function (s) { return [Math.max(a, s[0]), Math.min(b, s[1])]; })
      .filter(function (s) { return s[1] > s[0]; });
  }
  function frameTimes(req) {
    var segments = intersect(req.segments, req.t_in, req.t_out);
    if (!req.cut || segments.length < 2) segments = [[req.t_in, req.t_out]];
    var out = [], step = req.speed / FPS;
    segments.forEach(function (s) {
      for (var i = 0; s[0] + i * step < s[1] - 1e-9; i++) out.push(s[0] + i * step);
    });
    return out;
  }
  // Fixed 0.07 bits per pixel per encoded frame; an estimate, never a size cap.
  function estimateBytes(req) { return evenSize(req.width) * (evenSize(req.width) + BAR) * frameTimes(req).length * 0.07 / 8; }
  async function pickCodec(Encoder, w, h) {
    if (!Encoder || !Encoder.isConfigSupported) return null;
    for (var codec of ["avc1.640028", "avc1.4d0028", "avc1.42001f"]) {
      try { if ((await Encoder.isConfigSupported(config(codec, w, h))).supported) return codec; }
      catch (_) { /* Try the next profile on this device. */ }
    }
    return null;
  }
  function config(codec, w, h) {
    return {codec: codec, width: w, height: h, framerate: FPS,
      bitrate: Math.round(w * h * FPS * 0.07), avc: {format: "avc"}};
  }
  function captionText(item, round, score) {
    return "Round " + round + (score ? " · " + score[0] + "–" + score[1] : "") +
      " · " + (typeof item === "string" ? item : item.summary || "");
  }
  function fileName(req) {
    var t = (Math.round(req.t_in * 10) / 10).toFixed(1).replace(/\.0$/, "");
    return "round" + req.round + "-" + t + "s.mp4";
  }
  function clock(t) { var n = Math.floor(Math.max(0, t)); return Math.floor(n / 60) + ":" + String(n % 60).padStart(2, "0"); }
  function stopped(signal) { if (signal && signal.aborted) throw ABORT; }
  // Race each preparation/flush wait too: cancellation never waits for a hung resource.
  function waitFor(value, signal) {
    var pending = Promise.resolve(value);
    if (!signal) return pending;
    return new Promise(function (resolve, reject) {
      function abort() { cleanup(); reject(ABORT); }
      function cleanup() { signal.removeEventListener("abort", abort); }
      pending.then(function (v) { cleanup(); if (signal.aborted) reject(ABORT); else resolve(v); },
        function (e) { cleanup(); reject(signal.aborted ? ABORT : e); });
      if (signal.aborted) abort(); else signal.addEventListener("abort", abort, {once: true});
    });
  }
  function dataOf(viewer) { return viewer.options.replayData || {}; }
  function labelCaption(value, names) {
    if (!value || typeof value === "string") return value || "";
    var slot = value.slot != null ? value.slot : value.player;
    var label = names[String(slot)];
    return (label ? label + " · " : "") + (value.summary || "");
  }
  async function prepareClip(viewer, req, signal) {
    stopped(signal);
    var options = Object.assign({}, viewer.options, {themeRoot: viewer.root});
    var layers = Object.assign({}, viewer.layers);
    var extra = {layers: layers, controlBorders: viewer.controlBorders,
      unknownView: viewer.unknownView, controlView: viewer.controlView};
    var round = viewer.current && viewer.current.blob.round === req.round ? viewer.current : null;
    if (!round) round = global.Replay.prepareRound(await waitFor(options.loadRound(req.round), signal));
    stopped(signal);
    var control = null;
    if (req.control && options.loadControl && global.ReplayControl) {
      // A clip owns its decoder and abort controller; live navigation cannot evict its round.
      var cache = new global.ReplayControl.ControlCache(options.loadControl, 1);
      try { control = await waitFor(cache.get(req.round), signal); }
      finally { cache.keep([]); }
    }
    stopped(signal);
    control = control && control.status === "ok" ? control : null;
    if (req.control && !control) layers.control = false;
    extra.control = control;
    var clip = global.Replay.clipViewer(options, round, extra);
    clip.controlUnavailable = !!req.control && !control;
    await waitFor(clip.ready, signal);
    stopped(signal);
    return clip;
  }
  async function exportClip(viewer, req, callbacks) {
    callbacks = callbacks || {};
    var signal = callbacks.signal, encoder = null, error = null, rejectError;
    var failure = new Promise(function (_, reject) { rejectError = reject; });
    failure.catch(function () {});
    var bytes = 0, submitted = 0, received = 0;
    req = Object.assign({}, req, {segments: (req.segments || []).map(function (s) { return s.slice(); })});
    var names = Object.assign({}, dataOf(viewer).clip_names);
    var score = (dataOf(viewer).round_scores || {})[String(req.round)];
    var caption = captionText(labelCaption(req.caption, names), req.round, score && score.slice());
    var times = frameTimes(req), w = evenSize(req.width), h = w + BAR;
    function progress() {
      if (callbacks.onProgress) callbacks.onProgress({frames: submitted, total: times.length, bytes: bytes});
    }
    async function waiting(promise) {
      await waitFor(Promise.race([promise, failure]), signal);
      stopped(signal); if (error) throw error;
    }
    try {
      stopped(signal);
      var clip = await prepareClip(viewer, req, signal);
      stopped(signal);
      if (clip.controlUnavailable && callbacks.onWarning) {
        callbacks.onWarning("Map control isn't ready for this round; exported without it.");
      }
      var codec = await waitFor(pickCodec(global.VideoEncoder, w, h), signal);
      stopped(signal);
      if (!codec) throw new Error("H.264 MP4 export is not supported by this browser.");
      var target = new global.Mp4Muxer.ArrayBufferTarget();
      var muxer = new global.Mp4Muxer.Muxer({target: target,
        video: {codec: "avc", width: w, height: h, frameRate: FPS}, fastStart: "in-memory"});
      encoder = new global.VideoEncoder({
        output: function (chunk, meta) {
          if ((signal && signal.aborted) || error) return;
          try {
            muxer.addVideoChunk(chunk, meta); bytes += chunk.byteLength; received++;
            if (received % 10 === 0) progress();
          } catch (e) { error = e; rejectError(e); }
        },
        error: function (e) { error = e; rejectError(e); }
      });
      encoder.configure(config(codec, w, h));
      var off = global.document.createElement("canvas"); off.width = off.height = 1024;
      var out = global.document.createElement("canvas"); out.width = w; out.height = h;
      var offCtx = off.getContext("2d"), ctx = out.getContext("2d");
      var opts = {control: req.control && !clip.controlUnavailable, names: req.names, health: req.health,
        nameOf: function (slot) { return names[String(slot)] || null; }};
      for (var i = 0; i < times.length; i++) {
        stopped(signal); if (error) throw error;
        while (encoder.encodeQueueSize > 8) await waiting(new Promise(function (r) { setTimeout(r, 0); }));
        clip.drawAt(times[i], offCtx, opts);
        ctx.drawImage(off, 0, 0, w, w);
        ctx.fillStyle = "#15151d"; ctx.fillRect(0, w, w, BAR);
        ctx.fillStyle = "#ffffff"; ctx.font = "16px sans-serif"; ctx.textBaseline = "middle";
        ctx.fillText(caption, 12, w + 20, w - 24);
        ctx.fillText(clock(times[i]), 12, w + 46);
        var frame = new global.VideoFrame(out, {timestamp: Math.round(i * 1e6 / FPS), duration: Math.round(1e6 / FPS)});
        try { encoder.encode(frame, {keyFrame: i % 60 === 0}); } finally { frame.close(); }
        submitted++;
        if (submitted % 10 === 0) progress();
        await waiting(new Promise(function (r) { setTimeout(r, 0); }));
      }
      await waiting(encoder.flush());
      stopped(signal); muxer.finalize(); stopped(signal);
      progress();
      return new Blob([target.buffer], {type: "video/mp4"});
    } catch (e) {
      if (e === ABORT || (signal && signal.aborted)) return null;
      throw e;
    } finally {
      if (encoder && encoder.state !== "closed") encoder.close();
    }
  }
  function megabytes(bytes) { return (bytes / 1000000).toFixed(2) + " MB"; }
  async function openPanel(viewer, prefill) {
    var panel = viewer.root.querySelector("[data-clip-panel]");
    if (!panel) return null;
    var generation = panel.clipGeneration = (panel.clipGeneration || 0) + 1;
    function current() { return generation === panel.clipGeneration; }
    if (panel.clipController) panel.clipController.cancel();
    prefill = prefill || {};
    if (Array.isArray(prefill)) prefill = {t_in: prefill[0], t_out: prefill[1]};
    var n = prefill.round || viewer.number, round = viewer.current;
    if (!round || round.blob.round !== n) round = {blob: await viewer.options.loadRound(n)};
    if (!current()) return null;
    var end = round.blob.t_end, window = defaultWindow(viewer.t, end);
    var q = function (key) { return panel.querySelector("#clip-" + key); };
    function message(text) { if (current()) q("message").textContent = text; }
    var fields = ["in", "out", "speed", "width", "cut", "control", "names", "health", "caption"];
    var segments = (prefill.segments || []).map(function (s) { return s.slice(); });
    var active = null, revision = 0, supported = false;
    panel.hidden = false;
    q("in").value = prefill.t_in != null ? prefill.t_in : prefill.t_start != null ? prefill.t_start : window[0];
    q("out").value = prefill.t_out != null ? prefill.t_out : prefill.t_end != null ? prefill.t_end : window[1];
    q("speed").value = prefill.speed === 2 ? "2" : "1";
    q("width").value = String(prefill.width || 720);
    q("cut").checked = !!prefill.cut;
    ["control", "names", "health"].forEach(function (key) {
      q(key).checked = prefill[key] != null ? prefill[key] : key === "health" || !!viewer.layers[key];
    });
    q("control").disabled = !viewer.controlCache;
    q("caption").value = labelCaption(prefill.caption || (prefill.summary ? prefill : ""), dataOf(viewer).clip_names || {});
    q("size").textContent = "0 MB"; q("progress").value = 0; q("cancel").disabled = true;
    function request() {
      var a = Math.min(end, Math.max(0, Number(q("in").value) || 0));
      var b = Math.min(end, Math.max(a, Number(q("out").value) || 0));
      var parts = intersect(segments, a, b);
      return {round: n, t_in: a, t_out: b, speed: Number(q("speed").value) === 2 ? 2 : 1,
        width: [512, 720, 1024].includes(Number(q("width").value)) ? Number(q("width").value) : 720,
        cut: q("cut").checked && parts.length >= 2, segments: parts, caption: q("caption").value,
        control: !!viewer.controlCache && q("control").checked, names: q("names").checked, health: q("health").checked};
    }
    async function refresh() {
      if (!current()) return;
      var rev = ++revision, req = request(), size = estimateBytes(req);
      q("cut").disabled = !!active || req.segments.length < 2;
      q("estimate").textContent = "Estimated size: " + megabytes(size);
      q("note").hidden = size <= LIMIT;
      // The button stays as it was while the codec is asked: typing in a field and then pressing Export fires the
      // field's change (a refresh) on mousedown, and a button disabled there would lose that click.
      var codec = await pickCodec(global.VideoEncoder, req.width, req.width + BAR);
      if (!current() || rev !== revision || active) return;
      supported = !!codec;
      message(supported ? "" : "H.264 MP4 export is not supported by this browser.");
      q("export").disabled = !!active || !supported || req.t_out <= req.t_in;
    }
    function cancel() { if (active) active.abort(); }
    function busy(value) {
      if (!current()) return;
      fields.forEach(function (key) { q(key).disabled = value; });
      q("in-now").disabled = q("out-now").disabled = value;
      q("control").disabled = value || !viewer.controlCache;
      q("cut").disabled = value || request().segments.length < 2;
      q("export").disabled = value || !supported;
      q("cancel").disabled = !value;
    }
    // Reopening replaces handlers instead of accumulating listeners or retaining an old round.
    fields.forEach(function (key) { q(key).onchange = refresh; });
    q("in-now").onclick = function () { if (!current()) return; q("in").value = viewer.t; return refresh(); };
    q("out-now").onclick = function () { if (!current()) return; q("out").value = viewer.t; return refresh(); };
    q("cancel").onclick = cancel;
    q("close").onclick = function () { if (!current()) return; cancel(); panel.hidden = true; };
    q("link").onclick = async function () {
      if (!current()) return;
      var req = request(), data = dataOf(viewer);
      try {
        var link = global.Replay.momentLink(data.match.uuid, req.round, req.t_in, req.t_out);
        await global.navigator.clipboard.writeText(new URL(link, global.location.href).href);
        message("Moment link copied.");
      } catch (e) { message("Could not copy link: " + e.message); }
    };
    q("export").onclick = async function () {
      if (!current() || active || !supported) return;
      var controller = active = new AbortController(), req = request();
      var warning = "";
      function status(text) { message(text + (warning ? " " + warning : "")); }
      busy(true); q("progress").value = 0; q("size").textContent = "0 MB";
      status("Preparing clip…");
      try {
        var blob = await exportClip(viewer, req, {signal: controller.signal,
          onWarning: function (text) { warning = text; status("Preparing clip…"); }, onProgress: function (p) {
          if (!current()) return;
          q("progress").max = p.total; q("progress").value = p.frames;
          q("size").textContent = megabytes(p.bytes);
          q("note").hidden = p.bytes <= LIMIT && estimateBytes(req) <= LIMIT;
          status("Encoding…");
        }});
        if (!current()) return;
        if (blob && !controller.signal.aborted) {
          var url = global.URL.createObjectURL(blob), a = global.document.createElement("a");
          a.href = url; a.download = fileName(req);
          try { a.click(); } finally { setTimeout(function () { global.URL.revokeObjectURL(url); }, 1000); }
          q("size").textContent = megabytes(blob.size);
          q("note").hidden = blob.size <= LIMIT;
          status("Clip downloaded.");
        } else status("Cancelled.");
      } catch (e) { status(e.message); }
      finally { active = null; busy(false); }
    };
    var result = {request: request, refresh: refresh, cancel: cancel};
    panel.clipController = result;
    busy(false);
    await refresh();
    return result;
  }
  var api = {frameTimes: frameTimes, defaultWindow: defaultWindow, estimateBytes: estimateBytes,
    pickCodec: pickCodec, captionText: captionText, fileName: fileName, evenSize: evenSize,
    prepareClip: prepareClip, exportClip: exportClip, openPanel: openPanel};
  global.ClipExport = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
