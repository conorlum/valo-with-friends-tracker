/* Offline owner worksheet. It never sends data, changes tags, or infers feature states. */
(function () {
  "use strict";
  function numberOrNull(value) {
    if (String(value).trim() === "") return null;
    var n = Number(value);
    if (!Number.isFinite(n) || n < 0) throw new Error("Times and uncertainty must be finite nonnegative numbers.");
    return n;
  }
  function parseLines(value) {
    if (String(value).trim() === "") return [];
    return String(value).split(",").map(function (s) {
      if (!/^\s*[1-9]\d*\s*$/.test(s)) throw new Error("Evidence lines must be positive whole numbers separated by commas.");
      var n = Number(s); if (!Number.isSafeInteger(n)) throw new Error("Evidence line is too large."); return n;
    });
  }
  function nextSceneId(observations) {
    var n = 1, used = new Set(observations.map(function (o) { return o.observation_id; }));
    while (used.has("scene-" + n)) n++;
    return "scene-" + n;
  }
  function validateWorksheet(value, report) {
    if (!value || value.format !== "map-feature-observations" || value.version !== 1 || value.audit_sha256 !== report.identity.audit_sha256 ||
        value.clock !== "export_time_ms" || !Array.isArray(value.observations)) throw new Error("This worksheet has a different audit, clock or format. Keep it for its original report.");
    var ids = new Set();
    value.observations.forEach(function (o) {
      var feature = o && report.features.find(function (f) {return f.id === o.feature_id;});
      if (!feature || typeof o.observation_id !== "string" || !o.observation_id || o.observation_id.length > 64 || ids.has(o.observation_id)) throw new Error("Unknown feature or duplicate/missing scene ID.");
      ids.add(o.observation_id);
      if (typeof o.state !== "string" || (o.state && feature.states.indexOf(o.state) < 0) || typeof o.action !== "string" || (o.action && feature.events.indexOf(o.action) < 0)) throw new Error("Unknown action or state; worksheet kept unchanged.");
      [o.time_ms,o.uncertainty_ms].forEach(function (v) {if (v !== null && (typeof v !== "number" || !Number.isFinite(v) || v < 0)) throw new Error("Invalid time or uncertainty.");});
      if (report.parser && typeof report.parser.duration_ms === "number" && o.time_ms > report.parser.duration_ms) throw new Error("Scene time is outside the replay duration.");
      if (["unverified","uncertain","verified"].indexOf(o.review) < 0 || typeof o.notes !== "string") throw new Error("Invalid review or notes.");
      if (o.candidate_key !== null && !report.candidates.some(function (c) {return c.key === o.candidate_key;})) throw new Error("Unknown candidate.");
      if (!Array.isArray(o.evidence_lines) || o.evidence_lines.some(function (n) {return !Number.isSafeInteger(n) || n <= 0 || n > report.scan.rows;})) throw new Error("Evidence line is outside the scanned export.");
      if (o.review === "verified" && (o.time_ms === null || o.uncertainty_ms === null || !o.state || !o.evidence_lines.length)) throw new Error("Verified scenes need time, uncertainty, state and source evidence.");
    });
    return value;
  }
  if (typeof module !== "undefined") module.exports = {numberOrNull:numberOrNull, parseLines:parseLines, nextSceneId:nextSceneId, validateWorksheet:validateWorksheet};
  if (typeof document === "undefined") return;
  var data = JSON.parse(document.getElementById("audit-data").textContent), report = data.report, sheet = data.worksheet;
  var box = document.getElementById("observations"), actions = document.getElementById("observation-actions"), message = document.getElementById("review-message");
  var fields = [];
  function node(tag, text) { var el = document.createElement(tag); if (text !== undefined) el.textContent = text; return el; }
  function select(options, value) {
    var el = node("select"); options.forEach(function (option) {var o = node("option", option[1]); o.value = option[0]; el.appendChild(o);});
    el.value = value || ""; return el;
  }
  function field(parent, label, input) {var el = node("label", label + " "); el.appendChild(input); parent.appendChild(el); return input;}
  function input(value, type) {var el = node("input"); el.type = type || "text"; el.value = value === null || value === undefined ? "" : String(value); return el;}
  function collect() {
    sheet.observations = fields.map(function (f) {return Object.assign({}, f.original, {
      action:f.action.value, state:f.state.value, time_ms:numberOrNull(f.time.value), uncertainty_ms:numberOrNull(f.uncertainty.value),
      review:f.review.value, candidate_key:f.candidate.value || null, evidence_lines:parseLines(f.lines.value), notes:f.notes.value
    });});
    return sheet;
  }
  function render() {
    box.replaceChildren(); fields = [];
    sheet.observations.forEach(function (observation) {
      var feature = report.features.find(function (f) { return f.id === observation.feature_id; });
      if (!feature) throw new Error("Worksheet refers to an unknown feature.");
      var fs = node("fieldset"), legend = node("legend", observation.observation_id + " · " + feature.name + " (" + feature.id + ")"); fs.appendChild(legend);
      var f = {original:observation};
      f.action = field(fs,"Observed action",select([["","unresolved"]].concat(feature.events.map(function (v) {return [v,v];})),observation.action));
      f.state = field(fs,"State after action",select([["","unresolved"]].concat(feature.states.map(function (v) {return [v,v];})),observation.state));
      f.time = field(fs,"Absolute export time (ms)",input(observation.time_ms,"number"));
      f.uncertainty = field(fs,"Timing uncertainty (± ms)",input(observation.uncertainty_ms,"number"));
      f.review = field(fs,"Owner review",select([["unverified","unverified"],["uncertain","uncertain"],["verified","verified by owner"]],observation.review));
      f.candidate = field(fs,"Candidate to inspect",select([["","unbound / unresolved"]].concat(report.candidates.map(function (c) {return [c.key,c.key + " · " + (c.hints[0] || "candidate")];})),observation.candidate_key));
      f.lines = field(fs,"Source evidence line numbers (comma separated)",input(observation.evidence_lines.join(",")));
      f.notes = node("textarea"); f.notes.value = observation.notes || ""; field(fs,"What you saw, before/after state, camera/replay clock evidence",f.notes);
      fields.push(f); box.appendChild(fs);
    });
    if (!fields.length) box.appendChild(node("p","No annotated features for this map were supplied. Add a catalogue and regenerate an audit before recording feature observations."));
  }
  function button(text, run) {var b = node("button",text); b.type = "button"; b.addEventListener("click",function () {try {run(); message.textContent = "";} catch (error) {message.textContent = error.message;}}); actions.appendChild(b);}
  var addFeature = select(report.features.map(function (f) {return [f.id,f.name];}), report.features.length ? report.features[0].id : "");
  if (report.features.length) {
    actions.appendChild(addFeature);
    button("Add another scene",function () {collect(); sheet.observations.push({observation_id:nextSceneId(sheet.observations),feature_id:addFeature.value,
      action:"",state:"",time_ms:null,uncertainty_ms:null,review:"unverified",candidate_key:null,evidence_lines:[],notes:""}); render();});
  }
  button("Download observations.json",function () {
    var text = JSON.stringify(validateWorksheet(collect(),report),null,2), url = URL.createObjectURL(new Blob([text],{type:"application/json"}));
    var a = node("a"); a.href = url; a.download = "observations.json"; a.click(); setTimeout(function () {URL.revokeObjectURL(url);},1000);
  });
  var restore = input("","file"); restore.accept = ".json,application/json";
  restore.addEventListener("change",async function () {
    try {
      var incoming = JSON.parse(await restore.files[0].text());
      validateWorksheet(incoming,report);
      sheet = incoming; render(); message.textContent = "Restored worksheet. Run the CLI review before treating scenes as verified.";
    } catch (error) {message.textContent = error.message;}
  });
  field(actions,"Restore downloaded worksheet",restore);
  render();
})();
