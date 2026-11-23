// Hover details for the plant-spot page: shows a spot's text under the map. Nothing else.
(function () {
  var detail = document.getElementById("plant-detail");
  if (!detail) return;
  document.querySelectorAll(".plant-spot").forEach(function (spot) {
    spot.addEventListener("mouseenter", function () { detail.textContent = spot.dataset.detail || ""; });
    spot.addEventListener("mouseleave", function () { detail.textContent = ""; });
  });
})();
