/*
 * Highlights tab of the replay viewer: the Play of the Game, then each friend's best plays. Reads
 * /replays/<uuid>/highlights.json (app/services/highlights.py `payload`). Summaries never carry a name (the UI puts
 * `item.player` in front) and no opponent name is in the payload. No build step and no dependencies; render() is
 * the only part that touches the DOM, and it is tested in node (tests/replays/test_highlights_tab.py).
 */
(function (global) {
  "use strict";

  var MORE_LIMIT = 2;
  var FALLBACK_NOTES = { kill: "best kill", util: "best util" };
  var MISSING_NOTES = {fight: "Team-fight swings not available yet", util: "Util plays not available yet",
    winchance: "Win-chance swings not available yet"};

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function durationText(item) {
    var seconds = Math.max(0, Math.round(Number(item.t_end) - Number(item.t_start)));
    return (isFinite(seconds) ? seconds : 0) + " s";
  }

  function button(text, attr, id, onClick) {
    var b = el("button", "replay-button replay-highlight-button", text);
    b.type = "button";
    b.setAttribute(attr, String(id));
    b.addEventListener("click", function () { onClick(); });
    return b;
  }

  function itemRow(item, handlers) {
    var li = el("li", "replay-highlight-row");
    li.appendChild(el("span", "replay-highlight-round", "R" + item.round));
    li.appendChild(el("span", "replay-highlight-summary", (item.player ? item.player + " " : "") + (item.summary || "")));
    li.appendChild(el("span", "replay-highlight-duration", durationText(item)));
    li.appendChild(button("Play", "data-highlight-jump", item.id, function () { handlers.jump(item); }));
    li.appendChild(button("Export clip", "data-highlight-export", item.id, function () { handlers.exportClip(item); }));
    return li;
  }

  function render(container, payload, handlers) {
    container.textContent = "";
    handlers = handlers || {};
    var safe = {
      jump: function (item) { if (handlers.jump) handlers.jump(item); },
      exportClip: function (item) { if (handlers.exportClip) handlers.exportClip(item); }
    };
    payload = payload || {};
    if (payload.potg) {
      container.appendChild(el("h3", "replay-highlights-heading", "Play of the Game"));
      var potg = el("ol", "replay-feed");
      potg.appendChild(itemRow(payload.potg, safe));
      container.appendChild(potg);
    }
    var players = payload.players || {};
    Object.keys(players).sort(function (a, b) { return Number(a) - Number(b); }).forEach(function (slot) {
      var p = players[slot];
      container.appendChild(el("h3", "replay-highlights-heading", p.name + (p.agent ? " (" + p.agent + ")" : "")));
      var list = el("ol", "replay-feed");
      if (!p.best) {
        container.appendChild(list);
        container.appendChild(el("p", "replay-side-note", "No highlight this match"));
        return;
      }
      if (p.fallback && FALLBACK_NOTES[p.fallback]) {
        container.appendChild(el("p", "replay-side-note", FALLBACK_NOTES[p.fallback]));
      }
      list.appendChild(itemRow(p.best, safe));
      (p.more || []).slice(0, MORE_LIMIT).forEach(function (item) { list.appendChild(itemRow(item, safe)); });
      container.appendChild(list);
    });
    Object.keys(MISSING_NOTES).forEach(function (kind) {
      if ((payload.missing || []).indexOf(kind) >= 0) container.appendChild(el("p", "replay-side-note", MISSING_NOTES[kind]));
    });
  }

  var api = { render: render, durationText: durationText };
  global.Highlights = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
