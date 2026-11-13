/* Areas mode for the local control tagger. Paint and pins are kept in memory until export. */
(function () {
  "use strict";
  var TG = window.tagger, Core = window.TaggerCore.Areas, PX = window.TaggerCore.PX;
  var $ = function (id) { return document.getElementById(id); };
  var canvas = $("areasCanvas"), preview = $("areasReferencePreview"), stage = $("stage");
  var colours = ["#71b3f0", "#f5b962", "#84dcb1", "#ec9fa8", "#bba4f7", "#7ed3dc",
                 "#e1d27d", "#f3a77d", "#92a3fa", "#c4db84", "#e998d4", "#8ad9c8"];
  var maps = new Map();
  var ui = {tool: "brush", stroke: null, hover: null, pins: [], alignment: null, drawing: false};
  var storagePrefix = "control-tagger-area-reference-v1:";

  function active() { return TG.state.mode === "areas"; }
  function colour(id) { return colours[(id - 1) % colours.length]; }
  function cellCount(count) { return count + (count === 1 ? " cell" : " cells"); }
  function current() {
    var name = TG.state.map;
    if (!maps.has(name)) {
      var data = TG.DATA.maps[name].areas, model = Core.fromData(data);
      var state = {model: model, selected: model.names.length ? model.names[0].id : null, floor: 0,
                   saved: data.existing, pins: [], reference: null, referenceNote: "", pendingImage: null};
      maps.set(name, state);
      restoreReference(state);
    }
    return maps.get(name);
  }

  function rowById(state, id) {
    return state.model.names.find(function (row) { return row.id === id; });
  }

  function addOption(select, value, text) {
    var option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    select.appendChild(option);
  }

  function renderNames(revealSelection) {
    var state = current(), names = state.model.names, list = $("areasTree"), children = new Map();
    var selected = rowById(state, state.selected);
    list.replaceChildren();
    names.forEach(function (row) {
      if (!children.has(row.parent)) children.set(row.parent, []);
      children.get(row.parent).push(row);
    });
    function addRows(parent, depth) {
      (children.get(parent) || []).forEach(function (row) {
        var button = document.createElement("button"), swatch = document.createElement("span");
        var label = document.createElement("span"), badge = document.createElement("span");
        button.type = "button";
        button.setAttribute("aria-pressed", String(row.id === state.selected));
        button.style.paddingInlineStart = 8 + 14 * depth + "px";
        swatch.className = "sw";
        swatch.style.backgroundColor = colour(row.id);
        label.className = "name";
        label.textContent = row.name;
        badge.className = "area-badge " + (row.super_source === "auto" ? "saved-dirty" : "saved-ok");
        badge.textContent = row.super + " · " + row.super_source;
        button.append(swatch, label, badge);
        button.addEventListener("click", function () { selectName(row.id); });
        list.appendChild(button);
        addRows(row.id, depth + 1);
      });
    }
    addRows(null, 0);
    if (revealSelection) {
      var button = list.querySelector('[aria-pressed="true"]');
      if (button) button.scrollIntoView({block: "nearest", inline: "nearest"});
    }
    $("areasName").value = selected ? selected.name : "";
    $("areasParent").replaceChildren();
    addOption($("areasParent"), "", "No parent");
    $("areasMergeTarget").replaceChildren();
    addOption($("areasMergeTarget"), "", "Merge into…");
    names.forEach(function (row) {
      if (row === selected) return;
      addOption($("areasParent"), row.id, row.name);
      addOption($("areasMergeTarget"), row.id, row.name);
    });
    $("areasParent").value = selected && selected.parent !== null ? selected.parent : "";
    $("areasRegion").replaceChildren();
    var regions = Core.REGIONS.slice();
    if (selected && regions.indexOf(selected.super) === -1) regions.push(selected.super);
    regions.forEach(function (region) { addOption($("areasRegion"), region, region); });
    $("areasRegion").value = selected ? selected.super : "";
    var root = selected && selected.parent === null;
    $("areasRegion").disabled = !root;
    $("areasConfirmRegion").disabled = !root || selected.super_source === "set";
    $("areasRegionStatus").textContent = selected ? (root ? "Region: " + selected.super_source : "Region inherited") : "";
    ["areasName", "areasRename", "areasParent", "areasMergeTarget", "areasAddChild"].forEach(function (id) {
      $(id).disabled = !selected;
    });
    $("areasMerge").disabled = true;
    $("areasAuto").textContent = state.model.stillAuto() + " names still auto";
  }

  function selectName(id) {
    current().selected = id;
    renderNames(true);
    drawSoon();
  }

  function renderFloor() {
    var state = current(), control = $("areasFloor"), model = state.model;
    var count = Math.max(0, ...Object.values(model.floors).map(function (bands) { return bands.length; }),
                         ...Object.values(model.bands).map(function (bands) { return bands.length; }));
    $("areasFloorRow").hidden = count < 2;
    control.replaceChildren();
    for (var i = 0; i < count; i++) addOption(control, i, i === 0 ? "Ground" : "Floor " + (i + 1));
    addOption(control, "all", "All floors");
    if (state.floor !== null && state.floor >= count) state.floor = 0;
    control.value = state.floor === null ? "all" : state.floor;
    renderFloorHint();
  }

  function renderFloorHint() {
    var state = current();
    var bands = ui.hover ? state.model.floorBands(cellAt(ui.hover, state.model.grid)) : [];
    var range = state.floor === null ? null : bands[state.floor];
    $("areasFloorHint").textContent = range ? range[0] + " to " + range[1] + " dm" : "";
  }

  function renderPins() {
    var state = current();
    state.pins = state.model.pinStatus(ui.pins.filter(function (pin) { return pin.map === state.model.map; }));
    var resolved = state.pins.filter(function (pin) { return pin.ok; }).length;
    $("areasPinStatus").textContent = state.pins.length ? resolved + " of " + state.pins.length + " pins resolved"
      : ui.pins.length ? "No pins for this map" : "No pins loaded";
    $("areasExportFixture").disabled = resolved === 0;
  }

  function message(text) { $("areasMessage").textContent = text; }

  function changed(text, revealSelection) {
    current().model.autoSupers();
    renderNames(revealSelection);
    renderFloor();
    renderPins();
    message(text);
    drawSoon();
  }

  function setTool(tool) {
    ui.tool = tool;
    document.querySelectorAll("[data-area-tool]").forEach(function (button) {
      button.classList.toggle("active", button.dataset.areaTool === tool);
      button.setAttribute("aria-pressed", String(button.dataset.areaTool === tool));
    });
    drawSoon();
  }

  function toCanvas(e, element) {
    var rect = element.getBoundingClientRect();
    return [(e.clientX - rect.left) / rect.width * element.width, (e.clientY - rect.top) / rect.height * element.height];
  }

  function cellAt(point, grid) {
    function axis(p) { return Math.max(0, Math.min(grid - 1, Math.floor(p / PX * grid))); }
    return axis(point[1]) * grid + axis(point[0]);
  }

  function brushRadius() { return (+$("areasBrushSize").value - 1) / 2; }

  function dab(point, state) {
    var grid = state.model.grid, center = cellAt(point, grid), x = center % grid, y = Math.floor(center / grid);
    var radius = brushRadius(), cells = [];
    for (var dy = -radius; dy <= radius; dy++) {
      for (var dx = -radius; dx <= radius; dx++) {
        if (x + dx >= 0 && x + dx < grid && y + dy >= 0 && y + dy < grid && dx * dx + dy * dy <= radius * radius) {
          cells.push((y + dy) * grid + x + dx);
        }
      }
    }
    return ui.tool === "erase" ? state.model.erase(cells, state.floor) : state.model.paint(cells, state.selected, state.floor);
  }

  function mark(ctx, x, y, colourValue, label, radius) {
    ctx.save();
    ctx.fillStyle = colourValue;
    ctx.strokeStyle = "#111827";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#111827";
    ctx.font = "bold " + (radius + 3) + "px system-ui";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, x, y);
    ctx.restore();
  }

  function draw() {
    ui.drawing = false;
    if (!active()) return;
    var state = current(), model = state.model, ctx = canvas.getContext("2d"), size = PX / model.grid;
    ctx.clearRect(0, 0, PX, PX);
    if (ui.alignment) {
      ui.alignment.pairs.forEach(function (pair, index) { mark(ctx, pair[1][0], pair[1][1], "#ffd47a", index + 1, 10); });
      return;
    }
    var ref = state.reference;
    if (ref) {
      var m = ref.matrix;
      ctx.save();
      ctx.globalAlpha = +$("areasOpacity").value / 100;
      ctx.setTransform(m[0][0], m[1][0], m[0][1], m[1][1], m[0][2], m[1][2]);
      ctx.drawImage(ref.image, 0, 0);
      ctx.restore();
    }
    var positions = new Map();
    ctx.save();
    ctx.globalAlpha = ref ? .35 : .54;
    for (var y = 0; y < model.grid; y++) {
      for (var x = 0; x < model.grid;) {
        var id = model.nameAt(y * model.grid + x, state.floor), end = x + 1;
        while (end < model.grid && model.nameAt(y * model.grid + end, state.floor) === id) end++;
        if (id) {
          ctx.fillStyle = colour(id);
          ctx.fillRect(x * size, y * size, (end - x) * size, size);
          var place = positions.get(id) || [0, 0, 0], count = end - x;
          place[0] += (x + end) * count / 2;
          place[1] += (y + .5) * count;
          place[2] += count;
          positions.set(id, place);
        }
        x = end;
      }
    }
    ctx.restore();
    ctx.font = "600 14px system-ui";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.strokeStyle = "#101820";
    ctx.lineJoin = "round";
    ctx.lineWidth = 3;
    model.names.forEach(function (row) {
      var place = positions.get(row.id);
      if (!place) return;
      var x = place[0] / place[2] * size, y = place[1] / place[2] * size;
      ctx.fillStyle = row.id === state.selected ? "#ffffff" : "#e5edf7";
      ctx.strokeText(row.name, x, y);
      ctx.fillText(row.name, x, y);
    });
    state.pins.forEach(function (pin) {
      mark(ctx, pin.u * PX / 10000, pin.v * PX / 10000, pin.ok ? "#63e1a2" : "#ff828a", pin.ok ? "✓" : "×", 10);
    });
    if (ui.hover && (ui.tool === "brush" || ui.tool === "erase")) {
      ctx.strokeStyle = "rgba(255,255,255,.9)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(ui.hover[0], ui.hover[1], (brushRadius() + .5) * size, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  function drawSoon() {
    if (!ui.drawing && active()) {
      ui.drawing = true;
      window.requestAnimationFrame(draw);
    }
  }

  canvas.addEventListener("pointerdown", function (e) {
    if (!active() || e.button !== 0 || ui.stroke) return;
    var state = current(), model = state.model, point = toCanvas(e, canvas), cell = cellAt(point, model.grid);
    if (ui.alignment) {
      matchMapPoint(point);
      return;
    }
    if (ui.tool === "pick") {
      var name = model.nameAt(cell, state.floor);
      if (name) selectName(name);
      else message("This cell has no area on the selected floor.");
      return;
    }
    if (state.selected === null && ui.tool !== "erase") {
      message("Add a name before painting.");
      return;
    }
    if (ui.tool === "fill") {
      var count = model.fill(cell, state.selected, state.floor);
      changed(count ? cellCount(count) + " filled." : "No cells changed.");
      return;
    }
    ui.stroke = {pointer: e.pointerId, last: point, count: dab(point, state)};
    canvas.setPointerCapture(e.pointerId);
    drawSoon();
  });

  canvas.addEventListener("pointermove", function (e) {
    if (!active()) return;
    var state = current(), p = toCanvas(e, canvas);
    ui.hover = p;
    renderFloorHint();
    if (ui.stroke && ui.stroke.pointer === e.pointerId) {
      var from = ui.stroke.last, dx = p[0] - from[0], dy = p[1] - from[1];
      var steps = Math.max(1, Math.ceil(Math.hypot(dx, dy) / (PX / state.model.grid / 2)));
      for (var i = 1; i <= steps; i++) ui.stroke.count += dab([from[0] + dx * i / steps, from[1] + dy * i / steps], state);
      ui.stroke.last = p;
    }
    drawSoon();
  });

  function finishStroke(e) {
    if (!ui.stroke || ui.stroke.pointer !== e.pointerId) return;
    var count = ui.stroke.count;
    ui.stroke = null;
    changed(count ? cellCount(count) + (ui.tool === "erase" ? " erased." : " painted.") : "No cells changed.");
  }
  canvas.addEventListener("pointerup", finishStroke);
  canvas.addEventListener("pointercancel", finishStroke);
  canvas.addEventListener("pointerleave", function () { ui.hover = null; renderFloorHint(); drawSoon(); });

  function validMatrix(m) {
    return Array.isArray(m) && m.length === 2 && m.every(function (r) {
      return Array.isArray(r) && r.length === 3 && r.every(Number.isFinite);
    });
  }

  function imageFit(image, width, height) {
    var scale = Math.min(width / image.naturalWidth, height / image.naturalHeight);
    return {scale: scale, left: (width - image.naturalWidth * scale) / 2, top: (height - image.naturalHeight * scale) / 2};
  }

  function saveReference(state) {
    var ref = state.reference;
    try {
      window.localStorage.setItem(storagePrefix + state.model.map, JSON.stringify({url: ref.url, matrix: ref.matrix}));
    } catch (e) {
      state.referenceNote += " Browser storage is unavailable for this image.";
    }
  }

  function loadReference(state, url, matrix, save) {
    var image = new Image();
    state.pendingImage = image;
    image.onload = function () {
      if (state.pendingImage !== image) return;
      state.pendingImage = null;
      var fit = imageFit(image, PX, PX);
      state.reference = {image: image, url: url, matrix: validMatrix(matrix) ? matrix
        : [[fit.scale, 0, fit.left], [0, fit.scale, fit.top]]};
      state.referenceNote = "Reference image loaded.";
      if (save) saveReference(state);
      if (active() && state === current()) { renderReference(); drawSoon(); }
    };
    image.onerror = function () {
      if (state.pendingImage !== image) return;
      state.pendingImage = null;
      state.referenceNote = "Could not open this reference image.";
      if (active() && state === current()) renderReference();
    };
    image.src = url;
  }

  function restoreReference(state) {
    try {
      var saved = JSON.parse(window.localStorage.getItem(storagePrefix + state.model.map));
      if (saved && typeof saved.url === "string" && saved.url.startsWith("data:image/")) {
        loadReference(state, saved.url, saved.matrix, false);
      }
    } catch (e) { state.referenceNote = "The saved reference could not be restored."; }
  }

  function renderReference() {
    var state = current(), ref = state.reference, align = ui.alignment;
    preview.hidden = !ref;
    preview.classList.toggle("aligning", !!align);
    $("areasAlign").disabled = !ref;
    $("areasAlign").textContent = align ? "Cancel alignment" : "Align image";
    $("areasAlignPairs").disabled = !!align;
    $("areasReferenceClear").disabled = !ref;
    $("areasReferenceStatus").textContent = align ? "Pair " + (align.pairs.length + 1) + " of " + align.count
      + (align.pending ? ": click the matching map point." : ": click a point on the reference image.") : state.referenceNote;
    if (!ref) return;
    var ctx = preview.getContext("2d"), fit = imageFit(ref.image, preview.width, preview.height);
    ctx.clearRect(0, 0, preview.width, preview.height);
    ctx.drawImage(ref.image, fit.left, fit.top, ref.image.naturalWidth * fit.scale, ref.image.naturalHeight * fit.scale);
    if (!align) return;
    function show(point, index) {
      mark(ctx, fit.left + point[0] * fit.scale, fit.top + point[1] * fit.scale, "#ffd47a", index + 1, 8);
    }
    align.pairs.forEach(function (pair, index) { show(pair[0], index); });
    if (align.pending) show(align.pending, align.pairs.length);
  }

  function cancelAlignment() {
    if (!ui.alignment) return false;
    ui.alignment = null;
    current().referenceNote = "Alignment cancelled.";
    renderReference();
    drawSoon();
    return true;
  }

  function matchMapPoint(point) {
    var align = ui.alignment;
    if (!align.pending) return;
    align.pairs.push([align.pending, point]);
    align.pending = null;
    if (align.pairs.length === align.count) {
      var state = current(), matrix = Core.solveAlign(align.pairs);
      ui.alignment = null;
      if (matrix) {
        state.reference.matrix = matrix;
        state.referenceNote = "Aligned with " + align.count + " point pairs.";
        saveReference(state);
      } else {
        state.referenceNote = "Alignment needs distinct points. For three pairs, choose points that form a triangle.";
      }
    }
    renderReference();
    drawSoon();
  }

  preview.addEventListener("click", function (e) {
    var align = ui.alignment, ref = current().reference;
    if (!align || !ref) return;
    var p = toCanvas(e, preview), fit = imageFit(ref.image, preview.width, preview.height);
    var x = (p[0] - fit.left) / fit.scale, y = (p[1] - fit.top) / fit.scale;
    if (x < 0 || y < 0 || x >= ref.image.naturalWidth || y >= ref.image.naturalHeight) return;
    align.pending = [x, y];
    renderReference();
  });

  function download(filename, contents) {
    var link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([contents], {type: "application/json"}));
    link.download = filename;
    link.click();
    window.setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
  }

  function addName() {
    var state = current(), name = $("areasNewName"), parent = $("areasAddChild").checked ? state.selected : null;
    var id = state.model.addName(name.value, parent);
    if (id === null) { message("Choose a unique name and a parent within three levels."); return; }
    name.value = "";
    state.selected = id;
    changed("Area added.", true);
  }

  function rename() {
    var state = current();
    if (state.model.rename(state.selected, $("areasName").value)) changed("Area renamed.", true);
    else message("Enter a unique area name.");
  }

  document.querySelectorAll("[data-area-tool]").forEach(function (button) {
    button.addEventListener("click", function () { setTool(button.dataset.areaTool); });
  });
  $("areasBrushSize").addEventListener("input", function () {
    $("areasBrushSizeText").textContent = cellCount(+this.value);
    drawSoon();
  });
  $("areasFloor").addEventListener("change", function () {
    current().floor = this.value === "all" ? null : +this.value;
    renderFloorHint();
    drawSoon();
  });
  $("areasAdd").addEventListener("click", addName);
  $("areasNewName").addEventListener("keydown", function (e) { if (e.key === "Enter") addName(); });
  $("areasRename").addEventListener("click", rename);
  $("areasName").addEventListener("keydown", function (e) { if (e.key === "Enter") rename(); });
  $("areasParent").addEventListener("change", function () {
    var state = current(), parent = this.value ? +this.value : null;
    if (state.model.setParent(state.selected, parent)) changed("Parent updated.", true);
    else { renderNames(); message("That parent would create a cycle or a fourth level."); }
  });
  $("areasRegion").addEventListener("change", function () {
    var state = current();
    if (state.model.setSuper(state.selected, this.value)) changed("Region set.");
  });
  $("areasConfirmRegion").addEventListener("click", function () {
    var state = current();
    if (state.model.confirmSuper(state.selected)) changed("Region confirmed.");
  });
  $("areasMergeTarget").addEventListener("change", function () { $("areasMerge").disabled = !this.value; });
  $("areasMerge").addEventListener("click", function () {
    var state = current(), target = +$("areasMergeTarget").value;
    if (!state.model.merge(state.selected, target)) { message("That merge would create a fourth level."); return; }
    state.selected = target;
    changed("Areas merged.", true);
  });

  $("areasPinsFile").addEventListener("change", async function () {
    var file = this.files[0];
    if (!file) return;
    try {
      var pins = JSON.parse(await file.text());
      if (!Array.isArray(pins) || pins.some(function (pin) {
        return !pin || typeof pin.map !== "string" || typeof pin.area !== "string" || !pin.area.trim()
          || ![pin.u, pin.v].every(function (v) { return Number.isFinite(v) && v >= 0 && v <= 10000; })
          || (pin.z != null && !Number.isFinite(pin.z));
      })) throw new Error("invalid pins");
      ui.pins = pins;
      renderPins();
      drawSoon();
    } catch (e) { message("Could not load pins. Choose a JSON array exported by area_pins.py."); }
    this.value = "";
  });

  $("areasReferenceFile").addEventListener("change", function () {
    var file = this.files[0];
    if (!file) return;
    cancelAlignment();
    var state = current(), reader = new FileReader();
    state.pendingImage = reader;
    reader.onload = function () {
      if (state.pendingImage === reader) loadReference(state, reader.result, null, true);
    };
    reader.onerror = function () {
      if (state.pendingImage !== reader) return;
      state.pendingImage = null;
      state.referenceNote = "Could not read this reference image.";
      if (active() && state === current()) renderReference();
    };
    reader.readAsDataURL(file);
    this.value = "";
  });
  $("areasAlign").addEventListener("click", function () {
    if (cancelAlignment()) return;
    ui.alignment = {count: +$("areasAlignPairs").value, pairs: [], pending: null};
    renderReference();
    drawSoon();
  });
  $("areasReferenceClear").addEventListener("click", function () {
    var state = current();
    ui.alignment = null;
    state.reference = state.pendingImage = null;
    state.referenceNote = "";
    try { window.localStorage.removeItem(storagePrefix + state.model.map); }
    catch (e) { state.referenceNote = "The browser could not clear its saved copy."; }
    renderReference();
    drawSoon();
  });
  $("areasOpacity").addEventListener("input", function () {
    $("areasOpacityText").textContent = this.value + "%";
    drawSoon();
  });
  $("areasExport").addEventListener("click", function () {
    var model = current().model;
    download(model.map + ".areas.json", model.exportText());
    renderNames();
  });
  $("areasExportFixture").addEventListener("click", function () {
    download("area_cases.json", JSON.stringify(current().model.fixtureFromPins(ui.pins), null, 1) + "\n");
  });

  function onMap() {
    ui.alignment = ui.stroke = ui.hover = null;
    if (!active()) return;
    var state = current();
    $("areasSource").textContent = state.saved ? "Saved areas" : "Walking-distance draft";
    renderNames();
    renderFloor();
    renderPins();
    renderReference();
    message("");
    drawSoon();
  }

  function onMode(mode) {
    var on = mode === "areas";
    $("areasCard").hidden = !on;
    stage.classList.toggle("areas", on);
    document.body.classList.toggle("areas-mode", on);
    if (on) setTool(ui.tool);
    onMap();
  }

  window.taggerAreas = {onMode: onMode, onMap: onMap, cancelAlignment: cancelAlignment};
})();
