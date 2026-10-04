"""The timing-gaps pattern page (docs/superpowers/plans/2026-10-04-timing-gaps-pattern-page.md, P2;
docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 9).

- `GET /gaps`: the maps with the control layer, each with its counted round count.
- `GET /gaps/{map_name}`: one map's patterns, filtered by the query string (app/services/gap_patterns.py `Filters`).
- `GET /gaps/{map_name}/routes.json?seq=1-2`: one pattern's routes and round links (`seq=` is the empty sequence).

Every route 404s in demo mode, like every replay route, and for a map without the control layer. Reads stored
rows only: no app.control or app.gaps (tests/replays/test_control_isolation.py)."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.replay import Replay
from app.services import gap_patterns
from app.services import replay_control as control_service
from app.services import replays as replay_service
from app.services.auth import get_current_player
from app.templates import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gaps", tags=["gaps"])

FRIENDS_LOGIN_NOTE = "Friends shows gaps involving you and the players on your Friends page, so it needs a login."
THIN_DATA_NOTE = ("There are not many rounds here yet: these are counts to look at, not conclusions. "
                  "Every figure shows how many gaps it is out of.")

# The four causes of a predicted gap (app/gaps/detect.py), in the viewer's plain words (replay_gaps.js).
CAUSE_WORDS = {
    "route_released": "a teammate stopped watching the route",
    "victim_turned": "the player turned away",
    "victim_moved": "the player moved into the open",
    "open_timing": "enough time passed (no one ever watched the route)",
}
SIDE_WORDS = {"attack": "attack", "defense": "defense", "none": "side unknown"}


def _enabled_or_404() -> None:
    if not replay_service.replays_enabled():
        raise HTTPException(status_code=404)


def _map_or_404(map_name: str) -> str:
    _enabled_or_404()
    if control_service.map_layer(map_name) is None:
        raise HTTPException(status_code=404)
    return map_name


def control_maps() -> list[str]:
    """Every map with the control layer, by name."""
    index = control_service._assets()[0]
    return sorted(name for name in index if control_service.map_layer(name) is not None)


def parse_seq(value: str | None) -> list[int] | None:
    """`1-2` -> [1, 2]; `` -> [] (the empty sequence); anything else -> None."""
    if value is None:
        return None
    if value == "":
        return []
    parts = value.split("-")
    if not all(p.isdigit() for p in parts):
        return None
    return [int(p) for p in parts]


def route_label(choke_seq: list[int], chokes: dict) -> str:
    """Choke names joined with " → "; a choke not in the asset any more is "choke <id>"."""
    if not choke_seq:
        return "no choke"
    return " → ".join((chokes.get(str(c)) or {}).get("name") or f"choke {c}" for c in choke_seq)


@router.get("")
def gaps_index(request: Request, db: Session = Depends(get_db)):
    """The maps with the control layer; the counted rounds only for maps that have replays (one eligibility
    query set per such map)."""
    _enabled_or_404()
    with_replays = {name for (name,) in db.query(Replay.map_name).distinct()}
    maps = []
    for name in control_maps():
        counted = replays = 0
        if name in with_replays:
            eligible = gap_patterns.eligible_rounds(db, name)
            counted, replays = len(eligible.rounds), len({rid for rid, _ in eligible.rounds})
        maps.append({"name": name, "rounds": counted, "replays": replays})
    return templates.TemplateResponse(request, "gaps/index.html", {"maps": maps, "thin_data_note": THIN_DATA_NOTE})


def page_context(data: dict, logged_in: bool) -> dict:
    """The template's context: the service's page data plus display labels."""
    chokes = data["chokes"]
    patterns = [dict(p, label=route_label(p["choke_seq"], chokes)) for p in data["patterns"]]
    shapes = []
    for side in ("attack", "defense", "none"):
        block = (data["shapes"] or {}).get(side)
        if block is None:
            continue
        shapes.append({"side": side, "side_words": SIDE_WORDS[side], **block,
                       "groups": [dict(g, label=f"no choke, shape group {g['index']}") for g in block["groups"]]})
    return {"data": data, "map_name": data["map"], "filters": data["filters"], "logged_in": logged_in,
            "patterns": [p for p in patterns if p["choke_seq"]],
            # Without a map scale there are no shape groups: the empty sequence stays one pattern.
            "no_choke": next((p for p in patterns if not p["choke_seq"]), None) if data["shapes"] is None else None,
            "shapes": shapes, "cause_words": CAUSE_WORDS, "friends_login_note": FRIENDS_LOGIN_NOTE,
            "thin_data_note": THIN_DATA_NOTE,
            "drawing": {"map": data["map"], "chokes": chokes, "params": data["params"],
                        "shapes": {f"{s['side']}-{g['index']}": g["rounds"] for s in shapes for g in s["groups"]}}}


@router.get("/{map_name}")
def gaps_map(request: Request, map_name: str, db: Session = Depends(get_db)):
    _map_or_404(map_name)
    viewer = get_current_player(request, db)
    filters = gap_patterns.Filters.parse(request.query_params)
    started = time.perf_counter()
    data = gap_patterns.page_data(db, map_name, filters, viewer.id if viewer is not None else None)
    logger.info("gaps page %s: page_data took %.0f ms (%d counted rounds, %d rows, %d patterns, population %s)",
                map_name, (time.perf_counter() - started) * 1000, data["rounds"]["counted"], data["rows"]["total"],
                len(data["patterns"]), data["population"])
    return templates.TemplateResponse(request, "gaps/map.html", page_context(data, viewer is not None))


@router.get("/{map_name}/routes.json")
def gaps_routes(request: Request, map_name: str, db: Session = Depends(get_db)):
    """One pattern's routes under the page's filters and population: [{match_uuid, round, seq, t_open, kind,
    route}], in the page's fixed order."""
    _map_or_404(map_name)
    seq = parse_seq(request.query_params.get("seq"))
    if seq is None:
        return JSONResponse({"status": "bad_seq"}, status_code=400)
    viewer = get_current_player(request, db)
    filters = gap_patterns.Filters.parse(request.query_params)
    rows = gap_patterns.pattern_routes(db, map_name, seq, filters, viewer.id if viewer is not None else None)
    return JSONResponse({"map": map_name, "choke_seq": seq, "rows": rows}, headers={"Cache-Control": "no-store"})
