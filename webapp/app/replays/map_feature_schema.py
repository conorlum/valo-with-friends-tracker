"""The `map_features` annotation schema (docs/superpowers/plans/2026-10-04-map-interaction-tagger.md, "Storage
contract"; frozen in docs/superpowers/specs/2026-10-04-map-features-contract.md): its shape, the presets the
tagger offers, structural validation, the reference graph, and the runtime/editorial split that keeps
cosmetic edits from staling control.

Standard library only (copied into the upload worker with app/replays). Geometry-dependent checks (endpoint
on walkable ground, a footprint leaking past a passage, a stale floor binding) need the masks and heights and
live in app/control/features.py `diagnose`.

One object per map, beside the map's other keys in `tags.json`'s entry:

    {"version": 1, "next_id": n, "features": [...], "triggers": [...], "routes": [...], "floors": [...],
     "bundles": [...], "checklist": {category: {"status", "none", "notes"}},
     "image_sha": (export metadata), "runtime_digest": (export metadata)}

Every object keeps keys it doesn't know. Ids are "<kind>-<n>" (feature, trigger, route, floor, bundle,
state rows use their own names), allocated from `next_id` and never reused. A fact nobody has verified is
{"status": "unresolved", "note"?}; never 0, never a default. A known number is {"status": "known", "value",
"unit"} ("s" or "m").

Geometry is in minimap u/v (0..UV_MAX), independent of the page's zoom: {"type": "point", "uv": [u, v]},
{"type": "polyline", "uv": [[u, v], ...], "width": uv}, {"type": "polygon", "uv": [[u, v], ...]} or
{"type": "paint", "cells": <256 x 256 bits, the tagger's paint format>}.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import math
import re

from app.replays.map_feature_state import EVENTS, GUARD_KEYS, MID_MOTION

SCHEMA_VERSION = 1
UV_MAX = 10000
PAINT_BYTES = 256 * 256 // 8
ID_KINDS = ("feature", "trigger", "route", "floor", "bundle")
ID_RE = re.compile(r"^(feature|trigger|route|floor|bundle)-([1-9][0-9]*)$")
GEOMETRY_TYPES = ("point", "polyline", "polygon", "paint")
TRIGGER_TYPES = ("switch", "shoot", "proximity", "other")
ROUTE_KINDS = ("zipline", "rope", "teleporter", "drop", "custom")
IN_TRANSIT = ("complete", "abort", "unresolved")
BOUND_REFS = ("ground", "world", "all_height", "unresolved")
REVIEW = ("draft", "needs_verification", "user_reviewed")
CHECK_STATUS = ("not_started", "in_progress", "user_reviewed")
# Editorial keys: they never reach a runtime hash (a rename or a note never stales control).
EDITORIAL = ("name", "notes", "review", "ui", "parser_bindings", "category")
TOP_EDITORIAL = ("checklist", "next_id", "image_sha", "runtime_digest", "ui", "notes")


def unresolved(note: str | None = None) -> dict:
    return {"status": "unresolved", **({"note": note} if note else {})}


def known(value: float, unit: str) -> dict:
    return {"status": "known", "value": value, "unit": unit}


def empty() -> dict:
    return {"version": SCHEMA_VERSION, "next_id": 1, "features": [], "triggers": [], "routes": [], "floors": [],
            "bundles": [], "checklist": {}}


# ---------------------------------------------------------------- presets

def _door_states(*names, terminal=()):
    return [{"name": n, "blocks_movement": n not in ("open",), "blocks_sight": n not in ("open",),
             "terminal": n in terminal} for n in names]


def _unknown_motion(state: str) -> dict:
    return {"state": state, "duration": unresolved("motion time not measured")}


def preset(name: str) -> dict:
    """A feature template for one of PRESETS, with every unknown fact explicit. The caller gives it an id
    and a name (`create`)."""
    if name == "drop_door":
        return {"preset": name, "kind": "door", "capabilities": ["closeable"],
                "states": _door_states("open", "dropping", "closed"), "initial_state": "open",
                "transitions": [{"id": "drop", "from": ["open"], "event": "activate", "to": "closed",
                                 "motion": _unknown_motion("dropping"), "mid_motion": "unresolved"}],
                "reset": "round"}
    if name == "switch_door":
        return {"preset": name, "kind": "door", "capabilities": ["closeable"],
                "states": _door_states("closed", "opening", "open", "closing"), "initial_state": None,
                "transitions": [
                    {"id": "open", "from": ["closed"], "event": "switch", "to": "open",
                     "motion": _unknown_motion("opening"), "mid_motion": "unresolved"},
                    {"id": "close", "from": ["open"], "event": "switch", "to": "closed",
                     "motion": _unknown_motion("closing"), "mid_motion": "unresolved"}],
                "reset": "round"}
    if name == "proximity_door":
        return {"preset": name, "kind": "door", "capabilities": ["closeable", "proximity"],
                "states": _door_states("closed", "open"), "initial_state": "closed",
                "transitions": [
                    {"id": "first-in", "from": ["closed"], "event": "proximity_enter", "guard": {"occupants_eq": 1}, "to": "open"},
                    {"id": "hold", "from": ["open"], "event": "proximity_enter", "guard": {"occupants_gt": 0}, "to": "open"},
                    {"id": "last-out", "from": ["open"], "event": "proximity_leave", "guard": {"occupants_eq": 0},
                     "to": "open", "follow_up": {"after": unresolved("close delay not measured"), "name": "close"}},
                    {"id": "close", "from": ["open"], "event": "scheduled", "name": "close",
                     "guard": {"occupants_eq": 0}, "to": "closed"}],
                "reset": "round"}
    if name == "rotating_door":
        return {"preset": name, "kind": "rotating_door", "capabilities": ["rotating"],
                "states": [{"name": "rest_a", "blocks_movement": True, "blocks_sight": True},
                           {"name": "rotating", "blocks_movement": True, "blocks_sight": True},
                           {"name": "rest_b", "blocks_movement": True, "blocks_sight": True}],
                "initial_state": "rest_a",
                "transitions": [
                    {"id": "turn-a", "from": ["rest_a"], "event": "switch", "to": "rest_b",
                     "motion": _unknown_motion("rotating"), "mid_motion": "unresolved"},
                    {"id": "turn-b", "from": ["rest_b"], "event": "switch", "to": "rest_a",
                     "motion": _unknown_motion("rotating"), "mid_motion": "unresolved"}],
                "rotation": {"pivot": None, "panel": None, "direction": unresolved(), "start_deg": unresolved(),
                             "end_deg": unresolved(), "phases": []},
                "reset": "round"}
    if name == "breakable":
        return {"preset": name, "kind": "breakable", "capabilities": ["breakable"],
                "states": [{"name": "intact", "blocks_movement": True, "blocks_sight": True},
                           {"name": "broken", "blocks_movement": False, "blocks_sight": False, "terminal": True}],
                "initial_state": "intact",
                "transitions": [{"id": "break", "from": "*", "event": "destroy", "to": "broken"}],
                "reset": "round"}
    if name in ("zipline", "vertical_rope", "teleporter"):
        return {"preset": name, "kind": "connection", "capabilities": ["traversal"],
                "states": [{"name": "available", "blocks_movement": False, "blocks_sight": False}],
                "initial_state": "available", "transitions": [], "reset": "round"}
    if name == "custom":
        return {"preset": name, "kind": "custom", "capabilities": [], "states": [], "initial_state": None,
                "transitions": [], "reset": "round"}
    raise ValueError(f"unknown preset {name!r}")


PRESETS = ("drop_door", "switch_door", "proximity_door", "rotating_door", "breakable", "zipline", "vertical_rope",
           "teleporter", "custom")
ROUTE_PRESET = {"zipline": "zipline", "vertical_rope": "rope", "teleporter": "teleporter"}


def make_breakable(feature: dict) -> dict:
    """A copy of a door that can also be destroyed: a terminal `broken` state and a destroy row from any
    state (Ascent's and Sunset's switch doors)."""
    out = copy.deepcopy(feature)
    if "breakable" not in out.setdefault("capabilities", []):
        out["capabilities"].append("breakable")
    if not any(s.get("name") == "broken" for s in out.setdefault("states", [])):
        out["states"].append({"name": "broken", "blocks_movement": False, "blocks_sight": False, "terminal": True})
    if not any(r.get("event") == "destroy" for r in out.setdefault("transitions", [])):
        out["transitions"].append({"id": "break", "from": "*", "event": "destroy", "to": "broken"})
    return out


def route_template(kind: str) -> dict:
    """A route's unknown-everything skeleton: two endpoints, both directions, endpoint-only access."""
    def way(a, b):
        return {"from": a, "to": b, "entry": unresolved(), "transit": unresolved(), "length": unresolved()}
    return {"kind": kind, "endpoints": [{"id": "a", "uv": None},
                                        {"id": "b", "uv": None}],
            "path": None, "access": "endpoint_only", "directions": [way("a", "b"), way("b", "a")],
            "states": None, "in_transit": "unresolved"}


# ---------------------------------------------------------------- the checklist seed (plan, "Map checklist")

CHECKLIST_SEED = {
    "Abyss": [("breakable_blocks", "Breakable blocks", "breakable", None), ("ropes", "Vertical ropes", "vertical_rope", None)],
    "Ascent": [("switch_breakable_doors", "Switch-operated breakable doors", "switch_door", None)],
    "Bind": [("teleporters", "Teleporters", "teleporter", None), ("noise", "Noise cue annotation", None, None)],
    "Breeze": [("proximity_doors", "Proximity-operated doors", "proximity_door", None), ("noise", "Noise cues", None, None),
               ("ropes", "Vertical ropes", "vertical_rope", None)],
    "Corrode": [],
    "Fracture": [("proximity_doors", "Proximity-operated door", "proximity_door", 1), ("noise", "Noise cue", None, None),
                 ("ropes", "Vertical ropes", "vertical_rope", None)],
    "Haven": [],
    "Icebox": [("zipline", "Zipline", "zipline", None), ("ropes", "Vertical ropes", "vertical_rope", None)],
    "Lotus": [("rotating_doors", "Rotating doors", "rotating_door", None), ("breakable_door", "Breakable door", "breakable", None),
              ("ropes", "Vertical ropes", "vertical_rope", None)],
    "Pearl": [],
    "Split": [("ropes", "Vertical ropes", "vertical_rope", None)],
    "Summit": [("drop_doors", "Drop-doors", "drop_door", 3), ("ropes", "Vertical ropes", "vertical_rope", None)],
    "Sunset": [("closeable_breakable_door", "Closeable breakable door", "switch_door", None)],
}
NO_FEATURES_NOTE = "User reported no features (user input, not a verified engine assertion)"


def checklist_seed(map_name: str) -> list[dict]:
    """The map's categories as the page lists them: no placements, counts only where the user gave them."""
    return [{"key": key, "label": label, "preset": p, "expected": n} for key, label, p, n in CHECKLIST_SEED.get(map_name, [])]


# ---------------------------------------------------------------- ids

def _all_objects(mf: dict):
    for kind in ("features", "triggers", "routes", "floors", "bundles"):
        for obj in mf.get(kind) or []:
            if isinstance(obj, dict):
                yield kind, obj


def next_number(mf: dict) -> int:
    """The next free id number: past `next_id` and every id in use (so a hand-edited file can't reuse one)."""
    top = 0
    for _, obj in _all_objects(mf):
        m = ID_RE.match(str(obj.get("id")))
        if m:
            top = max(top, int(m.group(2)))
    n = mf.get("next_id")
    return max(n if isinstance(n, int) and not isinstance(n, bool) else 1, top + 1)


def allocate(mf: dict, kind: str) -> tuple[str, dict]:
    """A fresh id of `kind` and a copy of `mf` whose next_id is past it."""
    if kind not in ID_KINDS:
        raise ValueError(kind)
    out = copy.deepcopy(mf)
    n = next_number(out)
    out["next_id"] = n + 1
    return f"{kind}-{n}", out


# ---------------------------------------------------------------- references

def references(mf: dict) -> list[tuple[str, str, str]]:
    """Every typed reference as (from id, field, to id): trigger targets, parent doors, route owners, bundle
    members and a feature's bundle, and every floor binding (features, occluders, triggers, endpoints, access
    sites, base edits)."""
    out = []

    def floor_ref(src, field, value):
        if isinstance(value, str):
            out.append((src, field, value))

    for f in mf.get("features") or []:
        fid = f.get("id")
        if isinstance(f.get("parent"), str):
            out.append((fid, "parent", f["parent"]))
        if isinstance(f.get("bundle"), str):
            out.append((fid, "bundle", f["bundle"]))
        floors = f.get("floors")
        for fl in floors if isinstance(floors, list) else []:
            floor_ref(fid, "floors", fl)
        for s in f.get("states") or []:
            for occ in s.get("sight") or []:
                floor_ref(fid, f"states.{s.get('name')}.sight.floor", (occ.get("bounds") or {}).get("floor"))
        edits = f.get("base_edits") or {}
        floor_ref(fid, "base_edits.ground_binding", edits.get("ground_binding"))
    for t in mf.get("triggers") or []:
        for target in t.get("targets") or []:
            if isinstance(target, dict) and isinstance(target.get("feature"), str):
                out.append((t.get("id"), "targets", target["feature"]))
        floor_ref(t.get("id"), "floor", t.get("floor"))
    for r in mf.get("routes") or []:
        if isinstance(r.get("owner"), str):
            out.append((r.get("id"), "owner", r["owner"]))
        for e in r.get("endpoints") or []:
            floor_ref(r.get("id"), f"endpoints.{e.get('id')}.floor", e.get("floor"))
        access = r.get("access")
        if isinstance(access, dict):
            for site in access.get("sites") or []:
                floor_ref(r.get("id"), f"access.{site.get('id')}.floor", site.get("floor"))
    for b in mf.get("bundles") or []:
        for m in b.get("members") or []:
            out.append((b.get("id"), "members", m))
    return out


def referrers(mf: dict, target: str) -> list[tuple[str, str]]:
    """Who points at `target`: (from id, field)."""
    return [(src, field) for src, field, dst in references(mf) if dst == target]


# ---------------------------------------------------------------- runtime vs editorial

def runtime_projection(mf: dict) -> dict:
    """What control could consume: everything but the editorial keys (top level and per object), with each
    list sorted by id (list order is the page's, not the engine's). Unknown keys are kept: a field this
    version doesn't know might matter, so it counts as runtime."""
    mf = without_floor_selectors(mf)
    def strip(obj):
        return {k: copy.deepcopy(v) for k, v in obj.items() if k not in EDITORIAL}

    out = {k: copy.deepcopy(v) for k, v in mf.items() if k not in TOP_EDITORIAL}
    for kind in ("features", "triggers", "routes", "bundles"):
        if isinstance(mf.get(kind), list):
            items = [strip(o) if isinstance(o, dict) else o for o in mf[kind]]
            out[kind] = sorted(items, key=lambda o: str(o.get("id")) if isinstance(o, dict) else "")
    return _js_numbers(out)


def without_floor_selectors(mf: dict) -> dict:
    """Drop only retired authoring selectors; preserve unknown keys and the original source."""
    out = copy.deepcopy(mf)
    out.pop('floors', None)
    def bounds(obj):
        if isinstance(obj, dict) and obj.get('ref') == 'ground':
            obj.pop('floor', None)
    for f in out.get('features') or []:
        if not isinstance(f, dict):
            continue
        f.pop('floors', None)
        edits = f.get('base_edits')
        if isinstance(edits, dict):
            edits.pop('ground_binding', None)
        for s in f.get('states') or []:
            if isinstance(s, dict):
                bounds(s.get('sight_bounds'))
                for occ in s.get('sight') or []:
                    if isinstance(occ, dict):
                        bounds(occ.get('bounds'))
        for phase in (f.get('rotation') or {}).get('phases') or []:
            if isinstance(phase, dict):
                bounds(phase.get('sight_bounds'))
                bounds(phase.get('bounds'))
                for occ in phase.get('sight') or []:
                    if isinstance(occ, dict):
                        bounds(occ.get('bounds'))
    for t in out.get('triggers') or []:
        if isinstance(t, dict):
            t.pop('floor', None)
    for r in out.get('routes') or []:
        if not isinstance(r, dict):
            continue
        for e in (r.get('endpoints') or []) + ((r.get('access') or {}).get('sites', [])
                 if isinstance(r.get('access'), dict) else []):
            if isinstance(e, dict):
                e.pop('floor', None)
    return out


def tag_canonical_bytes(value: object) -> bytes:
    """Feature-tag v2: ASCII typed JSON, exact binary64 numbers, UTF-16 key order."""
    import struct
    def typed(item, path):
        if item is None:
            return ['null']
        if isinstance(item, bool):
            return ['bool', item]
        if isinstance(item, (int, float)):
            if isinstance(item, int) and abs(item) > 9007199254740991:
                raise ValueError(f'{path}: integer outside JavaScript safe range')
            if not math.isfinite(item):
                raise ValueError(f'{path}: nonfinite number')
            if (isinstance(item, int) or item.is_integer()) and abs(item) > 9007199254740991:
                raise ValueError(f'{path}: whole number outside JavaScript safe range')
            return ['number', struct.pack('>d', 0.0 if item == 0 else float(item)).hex()]
        if isinstance(item, str):
            return ['string', item]
        if isinstance(item, list):
            return ['array', [typed(v, f'{path}[{i}]') for i, v in enumerate(item)]]
        if isinstance(item, dict) and all(isinstance(k, str) for k in item):
            keys = sorted(item, key=lambda k: k.encode('utf-16-be', errors='surrogatepass'))
            return ['object', [[k, typed(item[k], f'{path}.{k}')] for k in keys]]
        raise ValueError(f'{path}: unsupported JSON value')
    return json.dumps(['feature-tags-v2', typed(value, '$')], ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False).encode('ascii')


def _js_numbers(value):
    """Whole floats as ints (2.0 -> 2), as JavaScript writes them, so the page's digest and this one agree."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _js_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_js_numbers(v) for v in value]
    return value


def digest(value) -> str:
    """16 hex of SHA-256 over the canonical JSON (sorted keys, no spaces, ASCII escapes, whole floats as ints).
    scripts/control_tagger_core.js `Features.digest` gives the same string."""
    return hashlib.sha256(json.dumps(_js_numbers(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
                          .encode("utf-8")).hexdigest()[:16]


def runtime_digest(mf: dict) -> str:
    return hashlib.sha256(tag_canonical_bytes(runtime_projection(mf))).hexdigest()[:16]


def editorial_digest(mf: dict) -> str:
    """A hash of everything, editorial included: what the page's draft provenance records."""
    return digest({k: v for k, v in mf.items() if k not in ("runtime_digest",)})


def canonical(mf: dict) -> dict:
    """A deep copy as JSON would carry it: every key kept, unknown ones included."""
    return json.loads(json.dumps(mf, allow_nan=False))


# ---------------------------------------------------------------- validation

class Report:
    def __init__(self):
        self.errors: list[dict] = []
        self.warnings: list[dict] = []

    def error(self, where, code, message):
        self.errors.append({"where": where, "code": code, "message": message})

    def warn(self, where, code, message):
        self.warnings.append({"where": where, "code": code, "message": message})

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"errors": self.errors, "warnings": self.warnings}


def check_version(mf) -> str | None:
    """None when this module can read `mf`, else why not. No migration is guessed."""
    if not isinstance(mf, dict):
        return "map_features is not an object"
    v = mf.get("version")
    if v != SCHEMA_VERSION:
        return f"map_features version {v!r} is not {SCHEMA_VERSION}; refusing to read or rewrite it"
    return None


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _uv(p) -> bool:
    return isinstance(p, (list, tuple)) and len(p) == 2 and all(_finite(c) and 0 <= c <= UV_MAX for c in p)


def _value(rep, where, v, unit, required=True):
    """A value object: known (finite, >= 0, the right unit) or unresolved. Returns True if unresolved."""
    if v is None:
        if required:
            rep.error(where, "missing_value", "missing; use {\"status\": \"unresolved\"} for an unknown value")
        return False
    if not isinstance(v, dict) or v.get("status") not in ("known", "unresolved"):
        rep.error(where, "bad_value", f"not a value object: {v!r}")
        return False
    if v["status"] == "unresolved":
        return True
    if not _finite(v.get("value")) or v["value"] < 0:
        rep.error(where, "bad_value", f"known value must be a finite number >= 0: {v.get('value')!r}")
    if unit and v.get("unit") != unit:
        rep.error(where, "bad_unit", f"unit must be {unit!r}, not {v.get('unit')!r}")
    return False


def _geometry(rep, where, g, allowed=GEOMETRY_TYPES):
    if not isinstance(g, dict) or g.get("type") not in allowed:
        rep.error(where, "bad_geometry", f"geometry must be one of {allowed}: {g!r}"[:200])
        return
    kind = g["type"]
    if kind == "point" and not _uv(g.get("uv")):
        rep.error(where, "bad_coordinates", "a point needs uv [u, v] within 0..10000")
    elif kind in ("polyline", "polygon"):
        pts = g.get("uv")
        need = 2 if kind == "polyline" else 3
        if not isinstance(pts, list) or len(pts) < need or not all(_uv(p) for p in pts):
            rep.error(where, "bad_coordinates", f"a {kind} needs at least {need} uv points within 0..10000")
        if kind == "polyline" and "width" in g and not (_finite(g["width"]) and g["width"] > 0):
            rep.error(where, "bad_dimensions", "a polyline's width must be a positive number")
    elif kind == "paint":
        try:
            raw = base64.b64decode(g.get("cells") or "", validate=True)
        except (binascii.Error, ValueError, TypeError):
            raw = b""
        if len(raw) != PAINT_BYTES:
            rep.error(where, "bad_paint", f"paint must be {PAINT_BYTES} bytes of base64")


def _guard(rep, where, g):
    if g is None:
        return
    if not isinstance(g, dict) or len(g) != 1 or next(iter(g)) not in GUARD_KEYS:
        rep.error(where, "bad_guard", f"unknown guard {g!r}"[:200])
        return
    (key, arg), = g.items()
    if key in ("all", "any"):
        for i, sub in enumerate(arg if isinstance(arg, list) else []):
            _guard(rep, f"{where}.{key}[{i}]", sub)
    elif key == "not":
        _guard(rep, f"{where}.not", arg)
    elif key == "unresolved":
        rep.warn(where, "unresolved_guard", "guard unresolved: the transition can't run until it is described")


def validate(mf, map_name: str | None = None, specials: list | None = None) -> Report:
    """Structural errors and annotation warnings (the plan's "Validation" list, minus what needs geometry)."""
    rep = Report()
    problem = check_version(mf)
    if problem:
        rep.error("map_features", "incompatible_version", problem)
        return rep
    mf = without_floor_selectors(mf)
    ids: dict[str, str] = {}
    for kind, obj in _all_objects(mf):
        oid = obj.get("id")
        if not isinstance(oid, str) or not ID_RE.match(oid) or not oid.startswith(kind[:-1] + "-"):
            rep.error(f"{kind}:{oid}", "bad_id", f"id must be '{kind[:-1]}-<n>'")
        elif oid in ids:
            rep.error(oid, "duplicate_id", f"id used twice ({ids[oid]} and {kind})")
        else:
            ids[oid] = kind
    for kind in ("features", "triggers", "routes", "floors", "bundles"):
        if kind in mf and not isinstance(mf[kind], list):
            rep.error(kind, "bad_shape", f"{kind} must be a list")
    features = {f.get("id"): f for f in mf.get("features") or [] if isinstance(f, dict)}
    floors = {f.get("id"): f for f in mf.get("floors") or [] if isinstance(f, dict)}
    bundles = {b.get("id"): b for b in mf.get("bundles") or [] if isinstance(b, dict)}
    for src, field, dst in references(mf):
        expected = {"parent": features, "owner": features, "targets": features, "members": features,
                    "bundle": bundles}.get(field, floors)
        if dst not in expected:
            rep.error(src, "dangling_reference", f"{field} -> {dst!r}, which doesn't exist")
    for fl in floors.values():
        _floor(rep, fl)
    for f in features.values():
        _feature(rep, f, floors)
    targeted = set()
    for t in mf.get("triggers") or []:
        if isinstance(t, dict):
            targeted |= _trigger(rep, t, features)
    for r in mf.get("routes") or []:
        if isinstance(r, dict):
            _route(rep, r, features, floors, specials or [])
    for b in bundles.values():
        if not b.get("members"):
            rep.warn(b.get("id"), "empty_bundle", "a bundle with no members")
    _counts(rep, mf, map_name)
    return rep


def _floor(rep, fl):
    fid = fl.get("id")
    band = fl.get("z_band")
    if band is None:
        rep.warn(fid, "unresolved_floor", f"floor {fl.get('label')!r} has no height band: a manual label only")
    elif not (isinstance(band, list) and len(band) == 2 and all(_finite(z) for z in band) and band[0] < band[1]):
        rep.error(fid, "bad_dimensions", "z_band must be [low, high] metres with low < high")
    elif not fl.get("height_sha"):
        rep.warn(fid, "unbound_floor", "a height band without the height asset it was read from")
    elif not (type(fl.get("origin_z")) is int):
        rep.warn(fid, "unframed_floor", "a height band without the lowest floor (origin_z) of the asset it was read "
                                        "from: it stops binding when the map's heights are rebuilt")


def _feature(rep, f, floors):
    if "sliding" in f:
        from app.replays.map_feature_motion import geometry_problems, vertical_problems
        for message in geometry_problems(f) + vertical_problems(f):
            rep.warn(f'{f.get("id")}.sliding', "motion_incomplete", message)
    fid = f.get("id")
    if 'replay_key' in f:
        from app.replays.ascent_features import KEYS
        if f['replay_key'] not in KEYS:
            rep.error(fid, 'bad_replay_binding', 'unknown Ascent replay binding')
    states = f.get("states") if isinstance(f.get("states"), list) else []
    names = [s.get("name") for s in states if isinstance(s, dict)]
    if len(set(names)) != len(names) or not all(isinstance(n, str) and n for n in names):
        rep.error(fid, "bad_states", "state names must be unique non-empty strings")
    init = f.get("initial_state")
    if init is None:
        rep.warn(fid, "uncertain_initial_state", "round-start state not given")
    elif init not in names:
        rep.error(fid, "bad_state_reference", f"initial_state {init!r} is not one of its states")
    row_ids = set()
    for i, row in enumerate(f.get("transitions") or []):
        where = f"{fid}.transitions[{i}]"
        if not isinstance(row, dict):
            rep.error(where, "bad_shape", "a transition is an object")
            continue
        if row.get("id") in row_ids or not isinstance(row.get("id"), str):
            rep.error(where, "duplicate_id", "transition ids must be unique strings within the feature")
        row_ids.add(row.get("id"))
        if row.get("event") not in EVENTS or row.get("event") in ("motion_complete", "reset"):
            rep.error(where, "bad_event", f"unknown or reducer-owned event {row.get('event')!r}")
        if row.get("event") == "activate":
            rep.warn(where, "unresolved_trigger", "what starts this transition is not classified yet")
        src = row.get("from")
        if src != "*" and not (isinstance(src, list) and all(s in names for s in src)):
            rep.error(where, "bad_state_reference", f"from {src!r} names a state the feature doesn't have")
        if row.get("to") not in names:
            rep.error(where, "bad_state_reference", f"to {row.get('to')!r} is not one of its states")
        motion = row.get("motion")
        if motion is not None:
            if not isinstance(motion, dict) or motion.get("state") not in names:
                rep.error(where, "bad_state_reference", "motion.state must be one of its states")
            elif _value(rep, f"{where}.motion.duration", motion.get("duration"), "s"):
                rep.warn(where, "unresolved_timing", "motion duration unresolved")
            if row.get("mid_motion", "unresolved") not in MID_MOTION:
                rep.error(where, "bad_policy", f"mid_motion must be one of {MID_MOTION}")
            elif row.get("mid_motion", "unresolved") == "unresolved":
                rep.warn(where, "unresolved_policy", "what a press during the motion does is unresolved")
        follow = row.get("follow_up")
        if follow is not None:
            if not isinstance(follow, dict) or not isinstance(follow.get("name"), str):
                rep.error(where, "bad_follow_up", "follow_up needs a name")
            elif _value(rep, f"{where}.follow_up.after", follow.get("after"), "s"):
                rep.warn(where, "unresolved_timing", "follow-up delay unresolved")
            elif not any(r.get("event") == "scheduled" and r.get("name") == follow.get("name")
                         for r in f.get("transitions") or [] if isinstance(r, dict)):
                rep.error(where, "dangling_reference", f"no row handles the scheduled event {follow.get('name')!r}")
        _guard(rep, f"{where}.guard", row.get("guard"))
    for s in states:
        if not isinstance(s, dict):
            continue
        where = f"{fid}.states.{s.get('name')}"
        for key in ("blocks_movement", "blocks_sight"):
            if key in s and not isinstance(s[key], bool):
                rep.error(where, "bad_shape", f"{key} is true or false")
        if s.get("footprint") is not None:
            _geometry(rep, f"{where}.footprint", s["footprint"])
        for j, occ in enumerate(s.get("sight") or []):
            _occluder(rep, f"{where}.sight[{j}]", occ)
    rot = f.get("rotation")
    if "rotating" in (f.get("capabilities") or []):
        if not isinstance(rot, dict) or rot.get("pivot") is None or rot.get("panel") is None:
            rep.warn(fid, "motion_incomplete", "rotating door without a pivot and panel: motion not fully described")
        elif rot.get("pivot") is not None:
            _geometry(rep, f"{fid}.rotation.pivot", rot["pivot"], ("point",))
    noise = f.get("noise")
    if isinstance(noise, dict) and noise.get("origin") is not None:
        _geometry(rep, f"{fid}.noise.origin", noise["origin"], ("point",))
    edits = f.get("base_edits") or {}
    for key in ("potential_ground", "remove_sight"):
        if edits.get(key) is not None:
            _geometry(rep, f"{fid}.base_edits.{key}", edits[key])
    for k, rc in enumerate(edits.get("reclassify") or []):
        if not isinstance(rc, dict) or rc.get("source") not in ("cover_paint", "cant_walk_paint", "tag", "base") \
                or rc.get("geometry") is None:
            rep.error(f"{fid}.base_edits.reclassify[{k}]", "bad_reclassify",
                      "reclassify names its source (cover_paint, cant_walk_paint, tag, base) and the exact geometry")
        else:
            _geometry(rep, f"{fid}.base_edits.reclassify[{k}]", rc["geometry"])


def _occluder(rep, where, occ):
    if not isinstance(occ, dict):
        rep.error(where, "bad_shape", "an occluder is an object")
        return
    _geometry(rep, f"{where}.geometry", occ.get("geometry"), ("polyline", "polygon"))
    b = occ.get("bounds")
    if not isinstance(b, dict) or b.get("ref") not in BOUND_REFS:
        rep.error(where, "bad_bounds", f"bounds.ref must be one of {BOUND_REFS}")
        return
    if b["ref"] == "unresolved":
        rep.warn(where, "unresolved_height", "sight bounds unresolved: saved, pending, blocks nothing")
        return
    if b["ref"] == "all_height":
        return
    unres = [_value(rep, f"{where}.bounds.{k}", b.get(k), "m") for k in ("bottom", "top")]
    if any(unres):
        rep.warn(where, "unresolved_height", "a sight bound is unresolved")
    elif b["bottom"]["value"] >= b["top"]["value"]:
        rep.error(where, "bad_dimensions", "bottom must be below top")


def _trigger(rep, t, features) -> set:
    tid = t.get("id")
    if t.get("type") not in TRIGGER_TYPES:
        rep.error(tid, "bad_trigger", f"type must be one of {TRIGGER_TYPES}")
    if t.get("geometry") is not None:
        _geometry(rep, f"{tid}.geometry", t["geometry"], ("point", "polygon", "paint"))
    if t.get("type") == "proximity":
        area = t.get("geometry") is not None and t["geometry"].get("type") in ("polygon", "paint")
        if not area:
            rng = t.get("range")
            if rng is None:
                rep.error(tid, "missing_range", "a proximity trigger needs an area or an explicitly unresolved range")
            elif _value(rep, f"{tid}.range", rng, "m"):
                rep.warn(tid, "unresolved_range", "proximity range unresolved")
    targets = t.get("targets") or []
    if not targets:
        rep.warn(tid, "no_target", "trigger linked to nothing")
    out = set()
    for target in targets:
        if not isinstance(target, dict) or target.get("event") not in EVENTS:
            rep.error(tid, "bad_target", f"a target is {{feature, event}} with a known event: {target!r}"[:200])
        else:
            out.add(target.get("feature"))
    return out


def _route(rep, r, features, floors, specials):
    rid = r.get("id")
    if r.get("kind") not in ROUTE_KINDS:
        rep.error(rid, "bad_route", f"kind must be one of {ROUTE_KINDS}")
    ends = r.get("endpoints")
    if not isinstance(ends, list) or len(ends) != 2:
        rep.error(rid, "missing_endpoint", "a route has exactly two endpoints")
        return
    names = set()
    for e in ends:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str):
            rep.error(rid, "missing_endpoint", "an endpoint needs an id")
            continue
        names.add(e["id"])
        if e.get("uv") is None:
            rep.error(f"{rid}.{e['id']}", "missing_endpoint", "endpoint not placed")
        elif not _uv(e["uv"]):
            rep.error(f"{rid}.{e['id']}", "bad_coordinates", "endpoint uv must be within 0..10000")
    access = r.get("access", "endpoint_only")
    if isinstance(access, dict):
        for s in access.get("sites") or []:
            if not isinstance(s, dict) or not _uv(s.get("uv")) or not isinstance(s.get("id"), str):
                rep.error(rid, "bad_coordinates", "an access site needs an id and uv")
            else:
                names.add(s["id"])
        rep.warn(rid, "unsupported_access", "intermediate access is recorded but not yet consumable")
    elif access != "endpoint_only":
        rep.error(rid, "bad_route", "access is 'endpoint_only' or {sites: [...]}")
    if r.get("path") is not None:
        _geometry(rep, f"{rid}.path", r["path"], ("polyline",))
    for i, d in enumerate(r.get("directions") or []):
        where = f"{rid}.directions[{i}]"
        if not isinstance(d, dict) or d.get("from") not in names or d.get("to") not in names or d.get("from") == d.get("to"):
            rep.error(where, "bad_route", "a direction joins two of the route's endpoints or access sites")
            continue
        if any([_value(rep, f"{where}.{k}", d.get(k), u) for k, u in (("entry", "s"), ("transit", "s"))]):
            rep.warn(where, "unresolved_cost", "travel cost unresolved: the arc is saved but not consumable")
        if d.get("length") is not None:
            _value(rep, f"{where}.length", d["length"], "m", required=False)
    if not r.get("directions"):
        rep.error(rid, "bad_route", "a route needs at least one direction")
    if r.get("in_transit", "unresolved") not in IN_TRANSIT:
        rep.error(rid, "bad_route", f"in_transit must be one of {IN_TRANSIT}")
    elif r.get("states") is not None and r.get("in_transit", "unresolved") == "unresolved":
        rep.warn(rid, "unresolved_policy", "conditional route with no policy for travel underway at closure")
    owner = features.get(r.get("owner"))
    if r.get("states") is not None:
        owned = {s.get("name") for s in (owner or {}).get("states") or []}
        if not isinstance(r["states"], list) or not set(r["states"]) <= owned:
            rep.error(rid, "bad_state_reference", "a route's states are states of its owner")
    good = [e for e in ends if isinstance(e, dict) and _uv(e.get("uv"))]
    if len(good) == 2 and list(good[0]["uv"]) == list(good[1]["uv"]):
        rep.warn(rid, "zero_length", "endpoints share a position")
    for sp in specials:
        try:
            pair = {tuple(sp["a"]), tuple(sp["b"])}
        except (KeyError, TypeError):
            continue
        if len(good) == 2 and {tuple(good[0]["uv"]), tuple(good[1]["uv"])} == pair:
            rep.warn(rid, "duplicates_special", "this route repeats a legacy special: it would run twice")


def _counts(rep, mf, map_name):
    if not map_name:
        return
    for cat in checklist_seed(map_name):
        if cat["expected"] is None:
            continue
        n = sum(1 for f in mf.get("features") or [] if isinstance(f, dict) and f.get("category") == cat["key"])
        entry = (mf.get("checklist") or {}).get(cat["key"]) or {}
        if n and n != cat["expected"]:
            rep.warn(cat["key"], "count_mismatch", f"{n} {cat['label']} annotated; the user's catalogue says {cat['expected']}")
        elif not n and entry.get("status") == "user_reviewed" and not entry.get("none"):
            rep.warn(cat["key"], "count_mismatch", f"reviewed with none annotated; the user's catalogue says {cat['expected']}")
