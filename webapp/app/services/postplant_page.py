"""Page data for /plants/<map>/postplant: current post-plant rows, one layout, cached per figure.

Only rows whose gap fingerprint and `gaps_revision` are current are read (R8), by the rule
`replay_util.current_rows` uses (`Freshness`). A replay counts on `layouts.layout_for`'s asset key; a
pending or unknown layout is left out (D17). The win model is the stored Critical Util "win" model and
is never built here (D14).

The result is one row per (asset key, population, site, side, spot) in `postplant_stats_cache`, valid
while `fingerprint` is unchanged: the layout's current rows, the links and clock offsets the figures
read, the win model's key, the spots file, the callouts, the painted-areas file and CALCULATION_VERSION
(D18, R15). No ingest path needs a hook. Nothing here names a player.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import zlib

from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.coach import areas
from app.models import MatchPlayer
from app.models.replay import Replay, ReplayPlayer, UtilModel
from app.models.replay_postplant import PostplantStatsCache, ReplayRoundPostplant
from app.replays import layouts
from app.services import circle, plant_page, plant_spots, plant_stats, postplant_stats, replay_gaps, util_model
from app.services.round_plants import site_for_uv

logger = logging.getLogger(__name__)

SIDES = ("attack", "defense")


def _current(db, asset_key, *, with_data):
    """[(ReplayRoundPostplant row, Replay)] of every ok row current on this layout, in a fixed order."""
    query = select(ReplayRoundPostplant).where(
        ReplayRoundPostplant.status == "ok", ReplayRoundPostplant.gaps_revision == replay_gaps.GAPS_REVISION)
    if not with_data:
        query = query.options(load_only(
            ReplayRoundPostplant.replay_id, ReplayRoundPostplant.round_number,
            ReplayRoundPostplant.fingerprint, ReplayRoundPostplant.computed_at))
    rows = db.scalars(query.order_by(ReplayRoundPostplant.replay_id, ReplayRoundPostplant.round_number)).all()
    ids = {row.replay_id for row in rows}
    if not ids:
        return []
    map_name = layouts.map_of(asset_key)
    replays = {r.id: r for r in db.scalars(select(Replay).where(Replay.id.in_(ids), Replay.map_name == map_name))}
    chosen = {}
    for replay in replays.values():
        key = (replay.map_name, replay.game_branch)
        if key not in chosen:
            selection = layouts.layout_for(replay.map_name, replay.game_branch)
            chosen[key] = selection.layout.asset_key if selection.state == "ok" else None
    fresh = replay_gaps.Freshness(db)
    fresh.preload(replays)
    return [(row, replays[row.replay_id]) for row in rows
            if row.replay_id in replays and chosen[replays[row.replay_id].map_name, replays[row.replay_id].game_branch] == asset_key
            and fresh.current(replays[row.replay_id], row.round_number, replay_gaps.GAPS_REVISION, row.fingerprint)]


def _slot_players(db, replay_ids):
    """{replay id: {slot: player id or None}} and, for the fingerprint, each slot's match player id."""
    players, links = {rid: {} for rid in replay_ids}, {rid: [] for rid in replay_ids}
    if not replay_ids:
        return players, links
    query = (select(ReplayPlayer.replay_id, ReplayPlayer.slot, ReplayPlayer.match_player_id, MatchPlayer.player_id)
             .outerjoin(MatchPlayer, MatchPlayer.id == ReplayPlayer.match_player_id)
             .where(ReplayPlayer.replay_id.in_(replay_ids)).order_by(ReplayPlayer.replay_id, ReplayPlayer.slot))
    for rid, slot, match_player_id, player_id in db.execute(query):
        players[rid][slot] = player_id
        links[rid].append((slot, match_player_id, player_id))
    return players, links


def read_rounds(db, asset_key) -> list[postplant_stats.RoundInput]:
    """Current, side-known rounds with a plant on a known site, on this layout."""
    current = _current(db, asset_key, with_data=True)
    players, _ = _slot_players(db, sorted({replay.id for _, replay in current}))
    flat = plant_page._flat_spots(asset_key)
    picked = []
    for row, replay in current:
        data = row.data or {}
        plant = data.get("plant")
        if not plant or not data.get("sides_known") or plant.get("u") is None or plant.get("v") is None:
            continue
        site = site_for_uv(replay.map_name, plant["u"], plant["v"])
        if site is None:
            continue
        picked.append((row, replay, data, {"site": site, "u": plant["u"], "v": plant["v"]}))
    spots = plant_stats.assign_spots([p[3] for p in picked], flat)
    return [postplant_stats.RoundInput(
        match_id=replay.match_id if replay.match_id is not None else f"replay:{replay.id}",
        replay_uuid=replay.match_uuid, round_number=row.round_number, map_name=replay.map_name,
        asset_key=asset_key, site=where["site"], spot=spot, data=data, clock_offset=replay.clock_offset,
        slot_players=players[replay.id])
        for (row, replay, data, where), spot in zip(picked, spots)]


def _sha(text) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(db, asset_key, population) -> str:
    """What a cached figure was made from. `population` is None (Everyone) or the circle's player ids."""
    current = _current(db, asset_key, with_data=False)
    ids = sorted({replay.id for _, replay in current})
    _, links = _slot_players(db, ids)
    parts = [f"{row.replay_id}|{row.round_number}|{row.computed_at}" for row, _ in current]
    seen = set()
    for _, replay in current:
        if replay.id not in seen:
            seen.add(replay.id)
            parts.append(f"R{replay.id}|{replay.match_id}|{replay.clock_offset!r}|{replay.link_status}|"
                         f"{replay.linked_at}|{links[replay.id]}")
    model = util_model.load(db)
    painted = areas.load_painted(asset_key)
    parts += [
        f"model:{model['key'] if model else 'none'}",
        f"spots:{plant_spots.digest()}",
        "callouts:" + json.dumps(sorted(areas.callouts(asset_key), key=lambda c: json.dumps(c, sort_keys=True)),
                                 sort_keys=True),
        "paint:" + (hashlib.sha256(painted.raw).hexdigest() if painted is not None else "none"),
        f"calc:{postplant_stats.CALCULATION_VERSION}",
        "population:" + ("everyone" if population is None else ",".join(map(str, sorted(population)))),
    ]
    return _sha("\n".join(parts))



def _painted_points(asset_key) -> dict:
    painted = areas.load_painted(asset_key)
    if painted is None:
        return {}
    sums = {}
    for index, name_id in enumerate(painted.cells):
        if name_id:
            total = sums.setdefault(name_id, [0.0, 0.0, 0])
            total[0] += index % areas.GRID
            total[1] += index // areas.GRID
            total[2] += 1
    return {painted.names[i]["name"]: [(x / n + 0.5) / areas.GRID * areas.UV_SCALE,
                                       (y / n + 0.5) / areas.GRID * areas.UV_SCALE]
            for i, (x, y, n) in sums.items() if i in painted.names}


def _points(asset_key) -> dict:
    """{area name: [u, v]}: its callout's point, else its painted area's centre of mass."""
    points = _painted_points(asset_key)
    points.update({c["name"]: [c["u"], c["v"]] for c in areas.callouts(asset_key)})
    return points


def _population_label(ids) -> str:
    return "everyone" if ids is None else plant_page._population(ids)


def _compute(db, asset_key, ids, site, side, spot, model):
    rounds = read_rounds(db, asset_key)
    sites = sorted({r.site for r in rounds})
    picked = [r for r in rounds if (site is None or r.site == site) and (spot is None or r.spot == spot)]
    include = (lambda r, slot: True) if ids is None else (lambda r, slot: r.slot_players.get(slot) in ids)
    table = postplant_stats.area_table(picked, model, side=side, include_slot=include)
    points = _points(asset_key)
    for row in table["areas"]:
        row["point"] = points.get(row["name"]) if row["marker"] else None
    data = {"asset_key": asset_key, "population": "everyone" if ids is None else "friends", "site": site,
            "side": side, "spot": spot, "has_model": model is not None, "sites": sites,
            "spots": [{"id": s["id"], "name": s["name"], "site": s["site"]} for s in plant_page._flat_spots(asset_key)],
            **table}
    return json.loads(json.dumps(data))                 # what a cache hit returns


def _read_cache(db, key, current):
    try:
        row = db.get(PostplantStatsCache, key)
        if row is None or row.fingerprint != current:
            return None
        return json.loads(zlib.decompress(row.data))
    except Exception:
        logger.warning("post-plant stats cache row unreadable; recomputing", exc_info=True)
        return None


def _store_cache(db, key, current, data):
    try:
        db.merge(PostplantStatsCache(key=key, fingerprint=current,
                                     data=zlib.compress(json.dumps(data).encode("utf-8")),
                                     computed_at=datetime.now(timezone.utc)))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("post-plant stats cache row not stored", exc_info=True)


def page_data(db, asset_key, viewer_id, *, site, side, spot) -> dict:
    """One layout's per-area figures. Everyone when `viewer_id` is None, else that viewer's circle.
    `side` is the engine's: "attack" (Hold) or "defense" (Retake). `site` and `spot` are None for all."""
    ids = circle.circle_ids(db, viewer_id) if viewer_id is not None else None
    key = f"{asset_key}|{_population_label(ids)}|{site or 'all'}|{side}|{spot or 'all'}"
    current = fingerprint(db, asset_key, ids)
    data = _read_cache(db, key, current)
    if data is None:
        data = _compute(db, asset_key, ids, site, side, spot, util_model.load(db))
        _store_cache(db, key, current, data)
    return data
