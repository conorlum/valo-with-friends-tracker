// Plant-spot page: sortable table and the narrow map view. No library.

// rows = [{value, index}] -> the indices in sorted order. A pure sort: numbers by value, text by
// localeCompare, rows with no value last in both directions, ties keep their original order.
function sortRows(rows, type, direction) {
  var sign = direction === "desc" ? -1 : 1;
  var keyed = rows.map(function (row) {
    var v = row.value;
    var empty = v === null || v === undefined || String(v).trim() === "";
    var key = null;
    if (!empty) {
      key = type === "number" ? Number(String(v).trim().replace(/^\+/, "")) : String(v);
      if (type === "number" && isNaN(key)) empty = true;
    }
    return { index: row.index, empty: empty, key: key };
  });
  keyed.sort(function (a, b) {
    if (a.empty !== b.empty) return a.empty ? 1 : -1;
    if (!a.empty) {
      var c = type === "number" ? a.key - b.key : a.key.localeCompare(b.key);
      if (c !== 0) return sign * c;
    }
    return a.index - b.index;
  });
  return keyed.map(function (k) { return k.index; });
}

if (typeof module !== "undefined") module.exports = { sortRows: sortRows };

if (typeof document !== "undefined") {
  document.querySelectorAll("[data-sort-table]").forEach(function (table) {
    var body = table.tBodies[0];
    var heads = Array.prototype.slice.call(table.querySelectorAll("th[data-sort-key]"));
    heads.forEach(function (th, col) {
      th.addEventListener("click", function () {
        var type = th.dataset.sortType || "text";
        var current = th.getAttribute("aria-sort");
        var direction;
        if (current === "ascending") direction = "desc";
        else if (current === "descending") direction = "asc";
        else direction = type === "number" ? "desc" : "asc";
        var trs = Array.prototype.slice.call(body.rows);
        var order = sortRows(trs.map(function (tr, i) {
          var cell = tr.cells[col];
          return { value: cell ? cell.dataset.sortValue : "", index: i };
        }), type, direction);
        order.forEach(function (i) { body.appendChild(trs[i]); });
        heads.forEach(function (h) {
          if (h === th) h.setAttribute("aria-sort", direction === "desc" ? "descending" : "ascending");
          else h.removeAttribute("aria-sort");
        });
      });
    });
  });

  var svg = document.querySelector("svg.plant-map[data-viewbox-narrow]");
  if (svg && window.matchMedia) {
    var wide = svg.getAttribute("viewBox");
    var narrow = svg.dataset.viewboxNarrow;
    var mq = window.matchMedia("(max-width: 640px)");
    var apply = function () { svg.setAttribute("viewBox", mq.matches ? narrow : wide); };
    apply();
    if (mq.addEventListener) mq.addEventListener("change", apply);
    else if (mq.addListener) mq.addListener(apply);
  }
}
