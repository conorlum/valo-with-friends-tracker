/*
 * The match page's Play of the Game card (templates/replays/_potg.html). "Play here" loads the viewer code only on
 * the first press, then loops the play (t_start..t_end at 1x) on a small canvas in the card. Each frame is
 * drawn at 1024x1024 into an offscreen canvas by the clip viewer (replay.js `clipViewer`, `drawAt`) and scaled onto
 * the card's 512x512 canvas. Stop, or the page being hidden, ends the loop, also while the code is still
 * loading. No build step; `loopTime` and `player` are pure of the DOM and tested in node
 * (tests/test_potg_card.py); the rest of the file is the page glue.
 */
(function (global) {
  "use strict";

  var SIZE = 1024, SHOWN = 512;

  // The play's clock: t0 + elapsed seconds, wrapping at the length of the play.
  function loopTime(t0, t1, elapsed) {
    var length = t1 - t0;
    if (!(length > 0)) return t0;
    return t0 + (elapsed % length);
  }

  // The loop, with everything outside it injected: raf/caf, now() in ms, document (hidden, addEventListener),
  // ctx (the card's canvas context), off (the 1024 offscreen canvas), item {t_start, t_end}, nameOf(slot),
  // load() -> Promise of a viewer with drawAt, onState("loading" | "playing" | "stopped"), onError(err).
  function player(deps) {
    var token = 0, frameId = null, doc = deps.document;
    function state(name) { if (deps.onState) deps.onState(name); }

    function stop() {
      token += 1;
      if (frameId !== null) { deps.caf(frameId); frameId = null; }
      state("stopped");
    }

    function fail(err) {
      stop();
      if (deps.onError) deps.onError(err);
    }

    function loop(viewer, mine) {
      var offCtx = deps.off.getContext("2d"), started = deps.now(), t0 = deps.item.t_start, t1 = deps.item.t_end;
      function frame() {
        frameId = null;
        if (mine !== token || doc.hidden) return;
        try {
          var t = loopTime(t0, t1, (deps.now() - started) / 1000);
          viewer.drawAt(t, offCtx, { nameOf: deps.nameOf });
          deps.ctx.drawImage(deps.off, 0, 0, SHOWN, SHOWN);
        } catch (err) { fail(err); return; }
        frameId = deps.raf(frame);
      }
      state("playing");
      frame();
    }

    function play() {
      stop();
      var mine = token;
      if (doc.hidden) return Promise.resolve();
      state("loading");
      return Promise.resolve().then(deps.load).then(function (viewer) {
        if (mine !== token || doc.hidden) return;
        loop(viewer, mine);
      }, function (err) { if (mine === token) fail(err); });
    }

    if (doc && doc.addEventListener) doc.addEventListener("visibilitychange", function () { if (doc.hidden) stop(); });
    return { play: play, stop: stop };
  }

  var api = { loopTime: loopTime, player: player };
  global.Potg = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;

  // ---- The page glue: one card, found by its play button, set up on the first press. ----

  if (typeof document === "undefined" || !document.addEventListener) return;

  var scriptLoads = {}, players = new WeakMap();

  function json(url) {
    return fetch(url).then(function (r) { if (!r.ok) throw new Error(url + " " + r.status); return r.json(); });
  }

  function loadScript(src) {
    if (!scriptLoads[src]) scriptLoads[src] = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = function () { reject(new Error("Couldn't load " + src)); };
      document.head.appendChild(s);
    });
    return scriptLoads[src];
  }

  // The viewer for the card's round, built once per card.
  function viewerLoader(card, data) {
    var version = card.getAttribute("data-version") || "", q = version ? "?v=" + version : "", built = null;
    return function () {
      if (built) return built;
      var tables = ["abilities", "weapons", "arena", "held_abilities"].map(function (name) {
        return json("/static/data/" + name + ".json" + q).catch(function () { return {}; });
      });
      built = Promise.all([Promise.all(tables), loadScript("/static/js/replay.js" + q),
                           json("/replays/" + data.uuid + "/" + data.round + ".json")]).then(function (loaded) {
        var Replay = global.Replay, tableSet = loaded[0];
        var round = Replay.prepareRound(Replay.withSiteData(data.site, loaded[2]));
        var viewer = Replay.clipViewer({
          themeRoot: card,
          rotation: data.rotation || 0,
          mapImage: "/static/img/maps/" + data.asset + ".png",
          sightMask: "/static/data/control/" + data.asset + ".sight.png" + q,
          agentIcon: function (agent) { return "/static/img/agents/" + agent.toLowerCase().replace(/[^a-z]/g, "") + ".png"; },
          abilityIcons: tableSet[0], gunIcons: tableSet[1], arenaRoom: tableSet[2], heldAbilities: tableSet[3],
          linked: { players: data.players, uvPerUnit: data.uv_per_unit }
        }, round);
        return viewer.ready;
      });
      built.catch(function () { built = null; });
      return built;
    };
  }

  function setup(card) {
    var data = JSON.parse(card.querySelector("script[data-potg]").textContent);
    var button = card.querySelector("[data-potg-play]"), canvas = card.querySelector("[data-potg-canvas]");
    var note = card.querySelector("[data-potg-note]");
    var off = document.createElement("canvas");
    off.width = off.height = SIZE;
    var clipNames = data.clip_names || {};
    return player({
      raf: function (fn) { return global.requestAnimationFrame(fn); },
      caf: function (id) { global.cancelAnimationFrame(id); },
      now: function () { return global.performance.now(); },
      document: document, ctx: canvas.getContext("2d"), off: off, item: data.item,
      nameOf: function (slot) { return clipNames[String(slot)] || ""; },
      load: viewerLoader(card, data),
      onState: function (name) {
        button.textContent = name === "stopped" ? "▶ Play here" : "Stop";
        if (name !== "stopped") { canvas.hidden = false; if (note) note.textContent = ""; }
        card.setAttribute("data-potg-state", name);
      },
      onError: function (err) {
        if (global.console) console.error(err);
        canvas.hidden = true;
        if (note) note.textContent = "Couldn't play this clip";
      }
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target && event.target.closest ? event.target.closest("[data-potg-play]") : null;
    if (!button) return;
    var card = button.closest(".potg-card");
    if (!card) return;
    var p = players.get(card);
    if (!p) { p = setup(card); players.set(card, p); }
    if (card.getAttribute("data-potg-state") === "stopped" || !card.getAttribute("data-potg-state")) p.play();
    else p.stop();
  });
})(typeof window !== "undefined" ? window : globalThis);
