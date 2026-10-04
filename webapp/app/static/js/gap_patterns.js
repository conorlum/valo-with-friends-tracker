/*
 * The timing-gaps pattern page (/gaps/<map>; docs/superpowers/plans/2026-10-04-timing-gaps-pattern-page.md, P2).
 * No build step and no dependencies. Selecting a pattern fetches its routes (routes.json) and draws them on the
 * minimap; a "no choke" shape group draws from the points embedded in the page. Every position is in pixels on
 * the 1024-px map. The pure helpers are tested in node (tests/replays/test_gap_patterns_web.py).
 */
(function (global) {
  "use strict";

  var MAP_PX = 1024;
  var COLORS = { predicted: "rgba(255, 176, 32, 0.55)", backshot: "rgba(230, 60, 220, 0.6)" };

  // Route points ([x, y], or a stored route's [t, x, y]) on the 1024-px map -> canvas px for a square canvas.
  function routePath(points, size) {
    return (points || []).map(function (p) {
      var x = p.length >= 3 ? p[1] : p[0];
      var y = p.length >= 3 ? p[2] : p[1];
      return [x / MAP_PX * size, y / MAP_PX * size];
    });
  }

  // A stored route (pieces of [t, x, y]) as canvas paths, one per unbroken piece.
  function routePieces(route, size) {
    return (route || []).filter(function (piece) { return piece && piece.length; })
      .map(function (piece) { return routePath(piece, size); });
  }

  // The selected pattern's routes.json URL: the page's filters plus `seq` ("" for the empty sequence).
  function routesUrl(mapName, seq, params) {
    var query = ["seq=" + encodeURIComponent(seq)];
    Object.keys(params || {}).sort().forEach(function (key) {
      query.push(encodeURIComponent(key) + "=" + encodeURIComponent(params[key]));
    });
    return "/gaps/" + encodeURIComponent(mapName) + "/routes.json?" + query.join("&");
  }

  // A round's viewer link, starting when the gap opened.
  function roundLink(ref) {
    return "/replays/" + encodeURIComponent(ref.match_uuid) + "?round=" + ref.round + "&t=" + ref.t_open;
  }

  // The chokes a sequence crosses, as {name, x, y} (a choke no longer in the asset is skipped).
  function chokeLabels(seq, chokes) {
    var out = [];
    (seq || []).forEach(function (id) {
      var c = chokes && chokes[String(id)];
      if (c && out.every(function (o) { return o.id !== id; })) out.push({ id: id, name: String(c.name), x: c.x, y: c.y });
    });
    return out;
  }

  function seqOf(key) {
    return key === "" ? [] : String(key).split("-").map(Number);
  }

  // ---------------------------------------------------------------- page wiring (browser only)

  function wire(doc) {
    var dataEl = doc.getElementById("gaps-drawing");
    var canvas = doc.querySelector("[data-gaps-canvas]");
    if (!dataEl || !canvas) return;
    var drawing = JSON.parse(dataEl.textContent);
    var ctx = canvas.getContext("2d");
    var status = doc.querySelector("[data-gaps-status]");
    var list = doc.querySelector("[data-gaps-rounds-list]");
    var heading = doc.querySelector("[data-gaps-selected-label]");
    var image = new Image();
    var current = null;     // {routes: [{kind, pieces: [[x, y]...]...}], labels: [...]}
    var token = 0;

    function draw() {
      var size = canvas.width;
      ctx.clearRect(0, 0, size, size);
      if (image.complete && image.naturalWidth) ctx.drawImage(image, 0, 0, size, size);
      if (!current) return;
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      current.routes.forEach(function (r) {
        ctx.strokeStyle = COLORS[r.kind] || COLORS.predicted;
        r.pieces.forEach(function (pts) {
          if (!pts.length) return;
          ctx.beginPath();
          ctx.moveTo(pts[0][0], pts[0][1]);
          for (var i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
          ctx.stroke();
        });
      });
      ctx.font = "12px sans-serif";
      ctx.textAlign = "center";
      current.labels.forEach(function (c) {
        var xy = routePath([[c.x, c.y]], size)[0];
        ctx.fillStyle = "#ffffff";
        ctx.beginPath();
        ctx.arc(xy[0], xy[1], 3, 0, 2 * Math.PI);
        ctx.fill();
        ctx.lineWidth = 3;
        ctx.strokeStyle = "rgba(0, 0, 0, 0.8)";
        ctx.strokeText(c.name, xy[0], xy[1] - 6);
        ctx.fillText(c.name, xy[0], xy[1] - 6);
      });
    }

    function listRounds(refs) {
      list.textContent = "";
      refs.forEach(function (ref) {
        var li = doc.createElement("li");
        var a = doc.createElement("a");
        a.href = roundLink(ref);
        a.textContent = "Round " + ref.round + ", at " + Number(ref.t_open).toFixed(1) + " s" +
          (ref.kind === "backshot" ? " (shot from behind)" : "");
        li.appendChild(a);
        list.appendChild(li);
      });
    }

    function show(label, routes, refs, labels) {
      current = { routes: routes, labels: labels };
      heading.textContent = label;
      status.textContent = refs.length + " on the map (amber: predicted gaps, magenta: shots from behind).";
      listRounds(refs);
      draw();
    }

    function select(button) {
      var size = canvas.width;
      var label = button.getAttribute("data-label");
      var mine = ++token;
      if (button.hasAttribute("data-shape")) {
        var refs = drawing.shapes[button.getAttribute("data-shape")] || [];
        show(label, refs.map(function (r) { return { kind: r.kind, pieces: [routePath(r.points, size)] }; }), refs, []);
        return;
      }
      var seq = button.getAttribute("data-seq");
      status.textContent = "Loading…";
      fetch(routesUrl(drawing.map, seq, drawing.params), { credentials: "same-origin" })
        .then(function (res) { if (!res.ok) throw new Error(res.status); return res.json(); })
        .then(function (body) {
          if (mine !== token) return;
          show(label, body.rows.map(function (r) { return { kind: r.kind, pieces: routePieces(r.route, size) }; }),
            body.rows, chokeLabels(seqOf(seq), drawing.chokes));
        })
        .catch(function () { if (mine === token) status.textContent = "Could not load this route's rounds."; });
    }

    doc.addEventListener("click", function (e) {
      var button = e.target.closest && e.target.closest("[data-gaps-select]");
      if (button) select(button);
    });
    image.onload = draw;
    image.src = "/static/img/maps/" + encodeURIComponent(drawing.map) + ".png";
    draw();
  }

  var api = {
    MAP_PX: MAP_PX, routePath: routePath, routePieces: routePieces, routesUrl: routesUrl, roundLink: roundLink,
    chokeLabels: chokeLabels, seqOf: seqOf, wire: wire
  };
  global.GapPatterns = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { wire(document); });
    else wire(document);
  }
})(typeof window !== "undefined" ? window : globalThis);
