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
from app.services import map_rotation, plant_page, plant_spots
from app.services import replays as replay_service
from app.services.auth import get_current_player
from app.templates import templates

router = APIRouter(prefix="/plants", tags=["plants"])

LOGIN_NOTE = "Friends shows the rounds you and the players on your Friends page attacked in, so it needs a login. Showing Everyone."
THIN_NOTE = "A spot shows a win rate only from {n} plants; below that it shows its count. Every figure shows its n."
STATIC = Path(__file__).resolve().parents[1] / "static"
SCALE_POINTS = 15.0  # adjusted points at which the colour is at full strength


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


def _spot_view(spot: dict, min_n: int) -> dict:
    adjusted = spot["adjusted_pct"]
    if adjusted is None:
        style = None
    else:
        strength = min(abs(adjusted) / SCALE_POINTS, 1.0)
        colour = "--good" if adjusted >= 0 else "--critical"
        style = f"color-mix(in srgb, var({colour}) {20 + 60 * strength:.0f}%, transparent)"
    interval = spot["adjusted_ci_display"]
    return {
        **spot,
        "left": spot["u"] / 100, "top": spot["v"] / 100, "size": 2 * spot["radius"] / 100,
        "grey": adjusted is None, "fill": style, "enough": spot["n"] >= min_n,
        "interval": f"{interval[0]} to {interval[1]}" if interval else None,
        "raw": None if spot["raw_pct"] is None else f"{spot['raw_pct']:.1f}%",
        "shrunk": None if spot["shrunk_pct"] is None else f"{spot['shrunk_pct']:.1f}%",
        "defused": None if spot["defused_pct"] is None else f"{spot['defused_pct']:.1f}%",
        "median": None if spot["median_plant_time"] is None else f"{spot['median_plant_time']:.1f}",
    }


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
        sites.append({**block, "name": name, "spots": [_spot_view(s, data["min_n"]) for s in block["spots"]]})
    turns = map_rotation.quarter_turns(map_name)
    base = f"/plants/{map_name}"

    def link(**params):
        merged = {"layout": asset_key if query.get("layout") == asset_key else None, "site": site, "view": "friends" if friends else None, **params}
        pairs = [f"{k}={v}" for k, v in merged.items() if v]
        return base + ("?" + "&".join(pairs) if pairs else "")

    return templates.TemplateResponse(request, "plants/map.html", {
        "map_name": map_name, "asset_key": asset_key, "image": _map_image(asset_key, map_name), "sites": sites, "site": site, "friends": friends,
        "login_note": login_note, "turns": turns, "degrees": turns * 90, "min_n": data["min_n"],
        "all_sites": sorted(data["sites"]), "thin_note": THIN_NOTE.format(n=data["min_n"]),
        "view_links": {"everyone": link(view=None), "friends": link(view="friends")},
        "site_links": {name: link(site=name) for name in sorted(data["sites"])} | {"": link(site=None)},
    })
