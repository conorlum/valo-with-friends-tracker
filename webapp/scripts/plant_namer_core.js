/* Offline circle editing in unrotated 0..10000 minimap units. No DOM dependencies. */
(function (root) {
  'use strict';
  const copy = value => JSON.parse(JSON.stringify(value));
  const number = (value, min, max) => typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;
  function validate(spot, named, location) {
    if (typeof spot.id !== 'string' || !spot.id.trim()) throw new Error(location + ': missing id');
    if (typeof spot.name !== 'string' || (named && !spot.name.trim())) throw new Error(location + ': every spot needs a name');
    if (!number(spot.u, 0, 10000) || !number(spot.v, 0, 10000)) throw new Error(location + ': centre must be within 0..10000');
    if (!number(spot.r, Number.MIN_VALUE, 10000)) throw new Error(location + ': radius must be within (0, 10000]');
  }
  function pointerUV(x, y, rect, turns) {
    let u = (x - rect.left) / rect.width * 10000, v = (y - rect.top) / rect.height * 10000;
    for (let i = 0; i < ((turns % 4) + 4) % 4; i++) [u, v] = [v, 10000 - u];
    return {u: Math.max(0, Math.min(10000, u)), v: Math.max(0, Math.min(10000, v))};
  }
  class Model {
    constructor(layouts) {
      this.layouts = copy(layouts);
      this.history = [];
    }
    spots(key, site) { return this.layouts[key]?.[site] || []; }
    save() { this.history.push(copy(this.layouts)); }
    replace(key, site, spots) {
      this.save();
      if (!this.layouts[key]) this.layouts[key] = {};
      this.layouts[key][site] = spots;
    }
    add(key, site, u, v, r = 150) {
      const spots = this.spots(key, site);
      if (spots.length >= 10) return null;
      let i = 1;
      while (spots.some(spot => spot.id === 'spot-' + i)) i++;
      const spot = {id: 'spot-' + i, name: '', u, v, r};
      validate(spot, false, key + '/' + site);
      this.replace(key, site, [...spots, spot]);
      return spot.id;
    }
    update(key, site, id, changes) {
      const spots = this.spots(key, site), old = spots.find(spot => spot.id === id);
      if (!old) return false;
      const spot = {...old};
      for (const field of ['name', 'u', 'v', 'r']) if (field in changes) spot[field] = changes[field];
      validate(spot, false, key + '/' + site);
      if (JSON.stringify(old) === JSON.stringify(spot)) return false;
      this.replace(key, site, spots.map(row => row.id === id ? spot : row));
      return true;
    }
    discard(key, site, id) {
      const spots = this.spots(key, site);
      if (!spots.some(spot => spot.id === id)) return false;
      this.replace(key, site, spots.filter(spot => spot.id !== id));
      return true;
    }
    merge(key, site, first, second) {
      const spots = this.spots(key, site), a = spots.find(s => s.id === first), b = spots.find(s => s.id === second);
      if (!a || !b || first === second) return false;
      const d = Math.hypot(b.u - a.u, b.v - a.v);
      let circle;
      if (a.r >= d + b.r) circle = {...a};
      else if (b.r >= d + a.r) circle = {...a, u: b.u, v: b.v, r: b.r};
      else {
        const r = (d + a.r + b.r) / 2, fraction = (r - a.r) / d;
        circle = {...a, u: a.u + fraction * (b.u - a.u), v: a.v + fraction * (b.v - a.v), r};
      }
      validate(circle, false, key + '/' + site);
      this.replace(key, site, spots.filter(s => s.id !== second).map(s => s.id === first ? circle : s));
      return true;
    }
    undo() {
      if (!this.history.length) return false;
      this.layouts = this.history.pop();
      return true;
    }
    /* Spots with a non-blank name, the only ones an export keeps. */
    namedCount() { return this.countSpots(true); }
    /* Spots the export leaves out, over the whole file, so a forgotten proposal shows. */
    unnamedCount() { return this.countSpots(false); }
    countSpots(named) {
      let n = 0;
      for (const sites of Object.values(this.layouts)) for (const spots of Object.values(sites))
        for (const spot of spots) if ((typeof spot.name === 'string' && spot.name.trim() !== '') === named) n++;
      return n;
    }
    exportJSON() {
      const layouts = {};
      for (const [key, sites] of Object.entries(this.layouts)) for (const [site, all] of Object.entries(sites)) {
        const location = key + '/' + site, ids = new Set();
        const spots = all.filter(spot => typeof spot.name === 'string' && spot.name.trim() !== '').map(copy);
        if (!spots.length) continue;                       /* a site with nothing named is left out */
        if (spots.length > 10) throw new Error(location + ': at most 10 spots');
        for (const spot of spots) {
          validate(spot, true, location);
          if (ids.has(spot.id)) throw new Error(location + ': duplicate id');
          ids.add(spot.id);
          spot.name = spot.name.trim();
        }
        (layouts[key] = layouts[key] || {})[site] = spots;   /* a layout with no sites is left out */
      }
      if (!Object.keys(layouts).length) throw new Error('No named spots to export: name at least one circle first.');
      return JSON.stringify({version: 1, layouts}, null, 2) + '\n';
    }
  }
  const api = {create: layouts => new Model(layouts), pointerUV};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PlantNamer = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
