"""The plant-spot page: where attackers plant on each map and how those rounds go.

- `GET /plants`: the maps with named spots, with their plant counts.
- `GET /plants/{map_name}?site=&view=&layout=`: one map's named spots, Everyone or Friends.

Both 404 in demo mode and for an unknown map. Friends needs a login; logged out falls back to Everyone
with a note. Nothing here names a player."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.replays import layouts
from app.services import map_rotation, plant_map_view, plant_page, plant_spots, postplant_page, postplant_stats
from app.services import replays as replay_service
from app.services.auth import get_current_player
from app.templates import templates

router = APIRouter(prefix="/plants", tags=["plants"])

LOGIN_NOTE = "Friends shows the rounds you and the players on your Friends page attacked in, so it needs a login. Showing Everyone."
THIN_NOTE = ("Counts include only plants whose round has a usable result. "
             "A spot shows a win rate from {n} plants; below that it shows its count.")
STATIC = Path(__file__).resolve().parents[1] / "static"


def _enabled_or_404() -> None:
    if not replay_service.replays_enabled():
        raise HTTPException(status_code=404)


def _choose_layout(map_name: str, requested: str | None) -> str:
    registry = layouts.load_registry()["maps"]
    if map_name not in registry:
        raise HTTPException(status_code=404)
    keys = [layout.asset_key for layout in layouts.layouts_of(map_name)]
    if requested in keys:
        return requested
    named = plant_spots.load()
    with_spots = [key for key in keys if any(named.get(key, {}).values())]
    return (with_spots or keys)[-1]


def _map_image(asset_key: str, map_name: str) -> str:
    """The layout's own minimap, else the base map's (as scripts/plant_namer.py does)."""
    if not (STATIC / "img" / "maps" / f"{asset_key}.png").is_file():
        asset_key = map_name
    return f"/static/img/maps/{quote(asset_key)}.png"


def _n(value):
    """A number as the SVG writes it: an integer when it is one, else two decimals at most."""
    value = round(float(value), 2)
    return int(value) if value == int(value) else value


def _spot_view(spot: dict, min_n: int, index: int = 0) -> dict:
    adjusted = spot["adjusted_pct"]
    interval = spot["adjusted_ci_display"]
    if "w" in spot:
        shape = {"kind": "rect", "x": _n(spot["u"] - spot["w"] / 2), "y": _n(spot["v"] - spot["h"] / 2),
                 "w": _n(spot["w"]), "h": _n(spot["h"]), "angle": _n(spot["angle"]),
                 "u": _n(spot["u"]), "v": _n(spot["v"])}
    else:
        shape = {"kind": "circle", "cx": _n(spot["u"]), "cy": _n(spot["v"]), "r": _n(spot["radius"])}

    def number(value):
        return "" if value is None else value

    return {
        **spot,
        "colour": plant_map_view.colour(index), "shape": shape, "muted": adjusted is None,
        "sort": {"name": spot["name"], "n": spot["usable_n"], "adjusted": number(adjusted),
                 "ci_low": number(spot["adjusted_ci"][0] if spot["adjusted_ci"] else None),
                 "raw": number(spot["raw_pct"]), "shrunk": number(spot["shrunk_pct"]),
                 "median": number(spot["median_plant_time"])},
        "interval": f"{interval[0]} to {interval[1]}" if interval else None,
        "raw": None if spot["raw_pct"] is None else f"{spot['raw_pct']:.1f}%",
        "shrunk": None if spot["shrunk_pct"] is None else f"{spot['shrunk_pct']:.1f}%",
        "median": None if spot["median_plant_time"] is None else f"{spot['median_plant_time']:.1f}",
    }


def _view_box(values) -> str:
    return " ".join(str(_n(value)) for value in values)


def _site_view(data, site, turns):
    """The zoomed map of one site: viewBox, narrow viewBox, crop square and labels; None without a crop."""
    block = data["sites"][site]
    if block.get("crop") is None:
        return None
    cx, cy, side = block["crop"]
    rcx, rcy = plant_map_view.rotate_uv(cx, cy, turns)
    gutter = 0.45 * side
    origin = rcx - side / 2
    anchors = []
    for index, spot in enumerate(block["spots"]):
        ax, ay = plant_map_view.rotate_uv(*spot["anchor"], turns)
        anchors.append((spot["id"], ax, ay))
    placed = plant_map_view.layout_labels(anchors, (origin, rcy - side / 2, side), 0.06 * side)
    by_id = {a[0]: a for a in anchors}
    names = {s["id"]: (s["name"], plant_map_view.colour(i)) for i, s in enumerate(block["spots"])}
    labels = [{"id": ident, "name": names[ident][0], "colour": names[ident][1], "side": where,
               "x": _n(x), "y": _n(y), "ax": _n(by_id[ident][1]), "ay": _n(by_id[ident][2])}
              for ident, where, x, y in placed]
    return {
        "view_box": _view_box((origin - gutter, rcy - side / 2, side + 2 * gutter, side)),
        "narrow_viewbox": _view_box((origin, rcy - side / 2, side, side)),
        "crop": {"x": _n(cx - side / 2), "y": _n(cy - side / 2), "side": _n(side)},
        "labels": labels, "font": _n(0.035 * side), "dot": _n(0.008 * side),
    }


MEASURES = (("net", "Net win"), ("gained", "Win gained"), ("kills", "Kills"), ("survival", "Survival"),
            ("line", "Line of sight"), ("crossfire", "Crossfire"))
SIDES = {"hold": "attack", "retake": "defense"}
NO_MODEL_NOTE = ("No win model is stored yet, so the win columns are unavailable and the map is coloured "
                 "by kills.")
POSTPLANT_NOTE = ("Post-plant rounds only, from replays. Hold is the attackers' side, Retake the defenders'. "
                  "n counts a player's post-plant in an area; colour starts at n={n}, below that the marker is "
                  "grey. Colour compares an area with the site-wide mean; every figure shows its n.")
NEEDS_TWO = "needs 2 matches"


def _pts(value) -> str:
    return postplant_stats.pts(value)


def _ci(interval, fmt):
    return None if interval is None else f"{fmt(interval[0])} to {fmt(interval[1])}"


def _deviation(row, measure, means):
    """How much better (positive) or worse an area is than the site-wide mean, in the measure's own units."""
    if measure == "survival":    # fewer deaths per minute held than the site mean is better (D12)
        if row["deaths_per_min"] is None or means["deaths_per_min"] is None:
            return None
        return means["deaths_per_min"] - row["deaths_per_min"]
    value, mean = {
        "kills": (row["shrunk_kills_per_n"], means["kills"]),
        "gained": (row["shrunk_win_gained_per_n"], means["win_gained"]),
        "net": (row["shrunk_net_win_per_n"], means["net_win"]),
        "line": (row["line_share"], means["line"]),
        "crossfire": (row["crossfire_share"], means["crossfire"]),
    }[measure]
    return None if value is None or mean is None else value - mean


def _means(data):
    rows = data["areas"]
    held = sum(r["held_s"] for r in rows)
    weighted = lambda key: (sum((r[key] or 0) * r["held_s"] for r in rows) / held) if held else None  # noqa: E731
    return {**{k: v for k, v in data["site_mean"].items()},
            "deaths_per_min": sum(r["deaths"] for r in rows) * 60 / held if held else None,
            "line": weighted("line_share"), "crossfire": weighted("crossfire_share")}


def _row_view(row, has_model):
    n = f"n={row['n']}"

    empty = row["n"] == 0          # an area with events but no samples: no denominator

    def win(value, ci):
        if not has_model:
            return f"no win model ({n})", None
        if empty or value is None:
            return f"- ({n})", None
        return f"{_pts(value)} ({n})", _ci(ci, _pts) or NEEDS_TWO

    def two(x):
        return f"{x:.2f}"

    gained, gained_ci = win(row["win_gained_per_n"], row["gained_ci"])
    net, net_ci = win(row["net_win_per_n"], row["net_ci"])
    return {
        "name": row["name"], "n": row["n"],
        "kills": f"{two(row['kills_per_n'])} ({n})" if row["kills_per_n"] is not None else f"- ({n})",
        "kills_ci": None if empty else (_ci(row["kills_ci"], two) or NEEDS_TWO),
        "deaths": f"{two(row['deaths_per_n'])} ({n})" if row["deaths_per_n"] is not None else f"- ({n})",
        "kd": f"{row['kd']:.2f} ({n})" if row["kd"] is not None else f"- ({n})",
        "gained": gained, "gained_ci": gained_ci, "net": net, "net_ci": net_ci,
        "deaths_min": f"{row['deaths_per_min']:.2f} ({n})" if row["deaths_per_min"] is not None else f"- ({n})",
        "line": f"{row['line_share']:.0%} ({n})" if row["line_share"] is not None else f"- ({n})",
        "crossfire": f"{row['crossfire_share']:.0%} ({n})" if row["crossfire_share"] is not None else f"- ({n})",
        "examples": [{"url": e["url"], "swing": _pts(e["swing"])} for e in row["examples"]],
    }


def _marker_views(data, measure):
    """One marker per named area with a point: sized by its share of held seconds, coloured by the measure
    only from MIN_N player-post-plants (D15)."""
    means = _means(data)
    held = sum(r["held_s"] for r in data["areas"]) or 1.0
    marked = [r for r in data["areas"] if r["marker"] and r.get("point")]
    deviations = {r["name"]: _deviation(r, measure, means) for r in marked}
    scale = max((abs(d) for r in marked if r["coloured"] and (d := deviations[r["name"]]) is not None), default=0)
    out = []
    for r in marked:
        dev = deviations[r["name"]]
        grey = not r["coloured"] or dev is None
        if grey:
            fill = None
        else:
            strength = abs(dev) / scale if scale else 0.0
            colour = "--good" if dev >= 0 else "--critical"
            fill = f"color-mix(in srgb, var({colour}) {20 + 60 * strength:.0f}%, transparent)"
        out.append({"name": r["name"], "n": r["n"], "left": r["point"][0] / 100, "top": r["point"][1] / 100,
                    "size": 3 + 17 * (r["held_s"] / held) ** 0.5, "grey": grey, "fill": fill,
                    "detail": f"{r['name']}: n={r['n']}"})
    return out


@router.get("/{map_name}/postplant")
def postplant_map(request: Request, map_name: str, db: Session = Depends(get_db)):
    _enabled_or_404()
    query = request.query_params
    asset_key = _choose_layout(map_name, query.get("layout"))
    viewer = get_current_player(request, db)
    wants_friends = query.get("view") == "friends"
    login_note = LOGIN_NOTE if wants_friends and viewer is None else None
    friends = wants_friends and viewer is not None
    side_name = "retake" if query.get("side") == "retake" else "hold"
    site = query.get("site") if query.get("site") in ("A", "B", "C") else None
    named = [s for s in plant_page._flat_spots(asset_key) if site is not None and s["site"] == site]
    spot = query.get("spot") if query.get("spot") in {s["id"] for s in named} else None
    data = postplant_page.page_data(db, asset_key, viewer.id if friends else None,
                                    site=site, side=SIDES[side_name], spot=spot)
    has_model = data["has_model"]
    measure = query.get("measure") if query.get("measure") in dict(MEASURES) else "net"
    if measure in ("net", "gained") and not has_model:
        measure = "kills"
    turns = map_rotation.quarter_turns(map_name)

    def link(**params):
        merged = {"layout": asset_key if query.get("layout") == asset_key else None,
                  "side": side_name if side_name != "hold" else None, "site": site, "spot": spot,
                  "view": "friends" if friends else None, "measure": query.get("measure"), **params}
        if "site" in params and "spot" not in params:
            merged["spot"] = None           # a spot belongs to its site
        pairs = [f"{k}={v}" for k, v in merged.items() if v]
        return f"/plants/{map_name}/postplant" + ("?" + "&".join(pairs) if pairs else "")

    return templates.TemplateResponse(request, "plants/postplant.html", {
        "map_name": map_name, "asset_key": asset_key, "image": _map_image(asset_key, map_name),
        "turns": turns, "degrees": turns * 90, "friends": friends, "login_note": login_note,
        "side": side_name, "site": site, "spot": spot, "measure": measure, "has_model": has_model,
        "no_model_note": NO_MODEL_NOTE, "note": POSTPLANT_NOTE.format(n=postplant_stats.MIN_N),
        "measures": MEASURES, "sites": data["sites"], "spots": named, "distinct_n": data["distinct_n"],
        "markers": _marker_views(data, measure), "rows": [_row_view(r, has_model) for r in data["areas"]],
        "links": {
            "plants": f"/plants/{map_name}" + (f"?layout={asset_key}" if query.get("layout") == asset_key else ""),
            "views": {"everyone": link(view=None), "friends": link(view="friends")},
            "sides": {"hold": link(side=None), "retake": link(side="retake")},
            "sites": {"": link(site=None), **{s: link(site=s) for s in data["sites"]}},
            "spots": {"": link(spot=None), **{s["id"]: link(spot=s["id"]) for s in named}},
            "measures": {m: link(measure=m) for m, _ in MEASURES},
        },
    })


@router.get("")
def plants_index(request: Request, db: Session = Depends(get_db)):
    _enabled_or_404()
    counts = plant_page.counts_by_asset(db)
    rows = []
    for key, sites in sorted(plant_spots.load().items()):
        named = sum(len(spots) for spots in sites.values())
        if named:
            rows.append({"map": layouts.map_of(key), "asset_key": key, "spots": named,
                         "plants": counts.get(key, 0)})
    return templates.TemplateResponse(request, "plants/index.html", {"maps": rows, "min_n": plant_page.MIN_N})


@router.get("/{map_name}")
def plants_map(request: Request, map_name: str, db: Session = Depends(get_db)):
    _enabled_or_404()
    query = request.query_params
    asset_key = _choose_layout(map_name, query.get("layout"))
    viewer = get_current_player(request, db)
    wants_friends = query.get("view") == "friends"
    login_note = LOGIN_NOTE if wants_friends and viewer is None else None
    friends = wants_friends and viewer is not None
    data = plant_page.page_data(db, asset_key, viewer.id if friends else None)
    site = query.get("site") if query.get("site") in data["sites"] else None
    sites = []
    for name, block in sorted(data["sites"].items()):
        if site is not None and name != site:
            continue
        sites.append({**block, "name": name,
                      "spots": [_spot_view(s, data["min_n"], i) for i, s in enumerate(block["spots"])]})
    turns = map_rotation.quarter_turns(map_name)
    zone = [{"x": _n(x), "y": _n(y), "w": _n(w), "h": _n(h)}
            for x, y, w, h in plant_map_view.zone_rects(asset_key)]
    zoom = _site_view(data, site, turns) if site is not None else None
    view_box = zoom["view_box"] if zoom else "0 0 10000 10000"
    base = f"/plants/{map_name}"

    def link(**params):
        merged = {"layout": asset_key if query.get("layout") == asset_key else None, "site": site, "view": "friends" if friends else None, **params}
        pairs = [f"{k}={v}" for k, v in merged.items() if v]
        return base + ("?" + "&".join(pairs) if pairs else "")

    return templates.TemplateResponse(request, "plants/map.html", {
        "map_name": map_name, "asset_key": asset_key, "image": _map_image(asset_key, map_name), "sites": sites, "site": site, "friends": friends,
        "zone": zone, "zoom": zoom, "view_box": view_box,
        "login_note": login_note, "turns": turns, "degrees": turns * 90, "min_n": data["min_n"],
        "all_sites": sorted(data["sites"]), "thin_note": THIN_NOTE.format(n=data["min_n"]),
        "view_links": {"everyone": link(view=None), "friends": link(view="friends")},
        "site_links": {name: link(site=name) for name in sorted(data["sites"])} | {"": link(site=None)},
    })
