// The coaching page (app/templates/replays/_coach.html): an instance moves the replay to its round and second.
// The viewer is the replay page's own (window.viewer, ready when window.viewerReady resolves).
(function () {
  "use strict";
  var root = document.querySelector("[data-coach]");
  if (!root) return;
  root.addEventListener("click", function (event) {
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
