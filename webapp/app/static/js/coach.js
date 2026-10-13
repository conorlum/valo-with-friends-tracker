// The coaching page: the strip above the replay (app/templates/replays/_coach.html) and the report's points, two
// tabs beside the map (replays/_coach_points.html). An instance moves the replay to its round and second.
// The viewer is the replay page's own (window.viewer, ready when window.viewerReady resolves).
(function () {
  "use strict";
  var root = document.querySelector("[data-coach]");
  if (!root) return;
  var side = document.querySelector("[data-replay]");

  // Sending feedback reloads the page: come back to the tab the reader was on, not the first one.
  var tabKey = "coach-tab:" + root.getAttribute("data-coach-uuid");
  function showTab(name) {          // what the viewer's own showTab does, usable before the viewer exists
    if (!side || !side.querySelector('[data-replay-panel="' + name + '"]')) return;
    Array.prototype.forEach.call(side.querySelectorAll("[data-replay-tab]"), function (tab) {
      var on = tab.getAttribute("data-replay-tab") === name;
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.classList.toggle("is-active", on);
    });
    Array.prototype.forEach.call(side.querySelectorAll("[data-replay-panel]"), function (panel) {
      panel.hidden = panel.getAttribute("data-replay-panel") !== name;
    });
  }
  try {
    var saved = window.sessionStorage.getItem(tabKey);
    if (saved === "coach-good" || saved === "coach-improve") showTab(saved);
    if (side) side.addEventListener("click", function (event) {
      var tab = event.target.closest("[data-replay-tab]");
      if (tab) window.sessionStorage.setItem(tabKey, tab.getAttribute("data-replay-tab"));
    });
  } catch (e) { /* no session storage: the page opens on its first tab */ }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-coach-round]");
    if (!button) return;
    var round = parseInt(button.getAttribute("data-coach-round"), 10);
    var t = parseFloat(button.getAttribute("data-coach-t"));
    if (!isFinite(round) || !window.viewerReady) return;
    window.viewerReady.then(function () {
      return isFinite(t) ? window.viewer.showRound(round, t) : window.viewer.showRound(round);
    }).then(function () {
      var stage = document.querySelector("[data-replay]");
      if (stage && stage.scrollIntoView) stage.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }).catch(function () { /* a round that fails to load shows the viewer's own error */ });
  });

  // "Prompt out" (this report, or the whole match): copy the feedback prompt; a browser without clipboard access
  // follows the link instead.
  if (navigator.clipboard && window.fetch) {
    Array.prototype.forEach.call(root.querySelectorAll("[data-coach-prompt]"), function (promptLink) {
      promptLink.addEventListener("click", function (event) {
        event.preventDefault();
        fetch(promptLink.href).then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.text(); })
          .then(function (text) { return navigator.clipboard.writeText(text); })
          .then(function () { promptLink.textContent = "Prompt copied"; })
          .catch(function () { window.open(promptLink.href, "_blank", "noopener"); });
      });
    });
  }
})();
