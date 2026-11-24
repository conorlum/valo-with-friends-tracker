/* Offline circle editing in unrotated 0..10000 minimap units. No DOM dependencies. */
(function (root) {
  'use strict';
  const copy = value => JSON.parse(JSON.stringify(value));
  const number = (value, min, max) => typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;
  function validate(spot, named, location) {
    if (typeof spot.id !== 'string' || !spot.id.trim()) throw new Error(location + ': missing id');
    if (typeof spot.name !== 'string' || (named && !spot.name.trim())) throw new Error(location + ': every spot needs a name');
    if (!number(spot.u, 0, 10000) || !number(spot.v, 0, 10000)) throw new Error(location + ': centre must be within 0..10000');
    if (isRect(spot)) {
      if (!number(spot.w, Number.MIN_VALUE, 10000) || !number(spot.h, Number.MIN_VALUE, 10000))
        throw new Error(location + ': width and height must be within (0, 10000]');
      if (!number(spot.angle, -180, 180)) throw new Error(location + ': angle must be within -180..180');
    } else if (!number(spot.r, Number.MIN_VALUE, 10000)) throw new Error(location + ': radius must be within (0, 10000]');
  }
  const MIN_SIDE = 20;
  const isRect = spot => spot.w !== undefined;
  const limit = (value, low, high) => Math.max(low, Math.min(high, value));
  const wrapAngle = degrees => { const a = ((degrees + 180) % 360 + 360) % 360 - 180; return a === -180 ? 180 : a; };
  function toLocal(spot, u, v) {
    const turn = spot.angle * Math.PI / 180, c = Math.cos(turn), s = Math.sin(turn), du = u - spot.u, dv = v - spot.v;
    return {x: du * c + dv * s, y: -du * s + dv * c};
  }
  function toWorld(spot, x, y) {
    const turn = spot.angle * Math.PI / 180, c = Math.cos(turn), s = Math.sin(turn);
    return {u: spot.u + x * c - y * s, v: spot.v + x * s + y * c};
  }
  /* Whether a circle or rotated rectangle holds the point, with an optional margin for clicks. */
  function contains(spot, u, v, pad = 0) {
    if (!isRect(spot)) return Math.hypot(spot.u - u, spot.v - v) <= spot.r + pad;
    const p = toLocal(spot, u, v);
    return Math.abs(p.x) <= spot.w / 2 + pad && Math.abs(p.y) <= spot.h / 2 + pad;
  }
  const plain = spot => ({id: spot.id, name: spot.name, u: spot.u, v: spot.v});
  const toRect = spot => isRect(spot) ? {...spot}
    : {...plain(spot), w: Math.min(10000, spot.r * 2), h: Math.min(10000, spot.r * 2), angle: 0};
  const toCircle = spot => isRect(spot) ? {...plain(spot), r: Math.min(10000, Math.max(spot.w, spot.h) / 2)} : {...spot};
  /* The eight resize handles and the rotate handle, `reach` beyond the top edge. */
  function rectHandles(spot, reach) {
    const handles = [];
    for (const sy of [-1, 0, 1]) for (const sx of [-1, 0, 1]) if (sx || sy)
      handles.push({kind: 'resize', sx, sy, ...toWorld(spot, sx * spot.w / 2, sy * spot.h / 2)});
    handles.push({kind: 'rotate', sx: 0, sy: -1, ...toWorld(spot, 0, -spot.h / 2 - reach)});
    return handles;
  }
  /* Drag one handle: the opposite edge or corner stays where it is. */
  function resizeRect(spot, sx, sy, u, v) {
    const p = toLocal(spot, u, v);
    let w = spot.w, h = spot.h, x = 0, y = 0;
    if (sx) { const fixed = -sx * spot.w / 2; w = limit((p.x - fixed) * sx, MIN_SIDE, 10000); x = fixed + sx * w / 2; }
    if (sy) { const fixed = -sy * spot.h / 2; h = limit((p.y - fixed) * sy, MIN_SIDE, 10000); y = fixed + sy * h / 2; }
    const centre = toWorld(spot, x, y);
    return {...spot, u: limit(centre.u, 0, 10000), v: limit(centre.v, 0, 10000), w, h};
  }
  const rotateRect = (spot, u, v) =>
    ({...spot, angle: wrapAngle(Math.atan2(v - spot.v, u - spot.u) * 180 / Math.PI + 90)});
  /* A rectangle along the plants' main direction, covering the middle 90% each way. */
  function fitRect(points) {
    if (points.length < 3) return null;
    const mu = points.reduce((t, p) => t + p[0], 0) / points.length, mv = points.reduce((t, p) => t + p[1], 0) / points.length;
    let uu = 0, vv = 0, uv = 0;
    for (const [u, v] of points) { uu += (u - mu) ** 2; vv += (v - mv) ** 2; uv += (u - mu) * (v - mv); }
    const angle = wrapAngle(Math.atan2(2 * uv, uu - vv) / 2 * 180 / Math.PI);
    const frame = {u: mu, v: mv, angle}, local = points.map(([u, v]) => toLocal(frame, u, v));
    const span = axis => {
      const values = local.map(p => p[axis]).sort((a, b) => a - b), last = values.length - 1;
      return [values[Math.floor(last * .05)], values[Math.ceil(last * .95)]];
    };
    const [x0, x1] = span('x'), [y0, y1] = span('y');
    const centre = toWorld(frame, (x0 + x1) / 2, (y0 + y1) / 2);
    return {u: limit(centre.u, 0, 10000), v: limit(centre.v, 0, 10000), angle,
            w: limit(x1 - x0, MIN_SIDE, 10000), h: limit(y1 - y0, MIN_SIDE, 10000)};
  }
  function pointerUV(x, y, rect, turns) {
    let u = (x - rect.left) / rect.width * 10000, v = (y - rect.top) / rect.height * 10000;
    for (let i = 0; i < ((turns % 4) + 4) % 4; i++) [u, v] = [v, 10000 - u];
    return {u: Math.max(0, Math.min(10000, u)), v: Math.max(0, Math.min(10000, v))};
  }
  /* How far to zoom and shift the displayed map (percent of its size) to frame a site's plants. */
  function siteView(points, turns) {
    if (!points.length) return {zoom: 1, x: 0, y: 0};
    const range = axis => {
      const values = points.map(point => point[axis]).sort((a, b) => a - b), last = values.length - 1;
      return [values[Math.floor(last * .01)], values[Math.ceil(last * .99)]];
    };
    const [u0, u1] = range(0), [v0, v1] = range(1);
    const zoom = 10000 / Math.min(10000, Math.max(2400, Math.max(u1 - u0, v1 - v0) * 1.6));
    let du = (u0 + u1) / 2 - 5000, dv = (v0 + v1) / 2 - 5000;
    for (let i = 0; i < ((turns % 4) + 4) % 4; i++) [du, dv] = [-dv, du];
    const limit = (zoom - 1) * 50, clamp = value => Math.max(-limit, Math.min(limit, value));
    return {zoom, x: clamp(-du * zoom / 100), y: clamp(-dv * zoom / 100)};
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
      for (const field of isRect(old) ? ['name', 'u', 'v', 'w', 'h', 'angle'] : ['name', 'u', 'v', 'r'])
        if (field in changes) spot[field] = changes[field];
      validate(spot, false, key + '/' + site);
      if (JSON.stringify(old) === JSON.stringify(spot)) return false;
      this.replace(key, site, spots.map(row => row.id === id ? spot : row));
      return true;
    }
    /* Swap circle and rectangle, same centre and size, as one undoable edit. */
    toggleShape(key, site, id) {
      const spots = this.spots(key, site), old = spots.find(spot => spot.id === id);
      if (!old) return false;
      const spot = isRect(old) ? toCircle(old) : toRect(old);
      validate(spot, false, key + '/' + site);
      this.replace(key, site, spots.map(row => row.id === id ? spot : row));
      return true;
    }
    /* Make the spot a rectangle fitted to the given plants. */
    fit(key, site, id, points) {
      const spots = this.spots(key, site), old = spots.find(spot => spot.id === id), box = fitRect(points);
      if (!old || !box) return false;
      const spot = {id: old.id, name: old.name, ...box};
      validate(spot, false, key + '/' + site);
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
      const spots = this.spots(key, site);
      const around = s => s && (isRect(s) ? {...plain(s), r: Math.hypot(s.w, s.h) / 2} : s);   /* a merge is a circle */
      const a = around(spots.find(s => s.id === first)), b = around(spots.find(s => s.id === second));
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
  const api = {create: layouts => new Model(layouts), pointerUV, siteView, contains, rectHandles, resizeRect,
               rotateRect, fitRect, toRect, toCircle};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PlantNamer = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
