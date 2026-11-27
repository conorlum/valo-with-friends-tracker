"""Page data for /plants: plant rows read at request time, statistics per layout and population.

Rows are read through one helper that decides each plant's layout when it is read (R9): on a
map with several layouts a tracker row counts only when its match's replay `game_branch`
selects the layout, so a layout added later also drops rows stored earlier. Expected win
chances are fitted once over every layout's usable rows (`plant_stats.expected_oof`) and the
subset's values are passed on; they are never refitted on a map or on the Friends subset.

The result is cached one row per (asset key, population) in `plant_stats_cache`, valid while
`fingerprint(db)` (all plant rows, the spots file, the moved-sites file, CALCULATION_VERSION) is
unchanged, so no
ingest path needs a hook. A stale, missing or corrupt row degrades to a recompute. No player
name is ever read or returned.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from statistics import median
import zlib

from sqlalchemy import and_, false, func, not_, or_, select

from app.models import Match, MatchPlayer, RoundPlant
from app.models.replay import Replay
from app.models.round_plant import PlantStatsCache
from app.replays import layouts
from app.scoring.plant_window import attacking_team
from app.services import circle, plant_map_view, plant_site_changes, plant_spots, plant_stats
from app.services.round_plants import asset_key_for

logger = logging.getLogger(__name__)

CALCULATION_VERSION = 3   # 3: usable plants only; no examples; site crop and spot anchors
MIN_N = 50                # plants a spot needs before it shows a win rate (the owner's gate, 2026-11-25)


def _registry_digest() -> str:
    """SHA-256 of the layout registry's bytes: a new layout changes which rows count (R9)."""
    return hashlib.sha256((layouts._DATA / "layouts.json").read_bytes()).hexdigest()


def fingerprint(db) -> str:
    count, newest = db.execute(select(func.count(), func.max(RoundPlant.updated_at))
                               .select_from(RoundPlant)).one()
    # Which replay is linked to which match, and its branch and clock, decide each row's
    # layout and example link: a relink can leave the count unchanged.
    links = hashlib.sha256()
    for match_id, branch, offset in db.execute(
            select(Replay.match_id, Replay.game_branch, Replay.clock_offset)
            .where(Replay.link_status == "linked", Replay.match_id.is_not(None))
            .order_by(Replay.match_id, Replay.game_branch, Replay.clock_offset)):
        links.update(f"{match_id!r}|{branch!r}|{offset!r}\n".encode("utf-8"))
    text = (f"{count}|{newest}|{links.hexdigest()}|{_registry_digest()}|"
            f"{plant_spots.digest()}|{plant_site_changes.digest()}|{CALCULATION_VERSION}")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _joined(query):
    """Usable plants only (D3): those whose round has attackers_alive, defenders_alive and attackers_won."""
    return (query.select_from(RoundPlant)
            .join(Match, Match.id == RoundPlant.match_id)
            .outerjoin(Replay, (Replay.match_id == RoundPlant.match_id) & (Replay.link_status == "linked"))
            .where(RoundPlant.attackers_alive.is_not(None), RoundPlant.defenders_alive.is_not(None),
                   RoundPlant.attackers_won.is_not(None)))


def read_rows(db) -> list[dict]:
    """Every usable stored plant whose layout is known now, with `asset_key` decided from the replay."""
    query = _joined(select(
        RoundPlant.match_id, RoundPlant.round_number, RoundPlant.site, RoundPlant.u, RoundPlant.v,
        RoundPlant.plant_time, RoundPlant.attackers_alive, RoundPlant.defenders_alive,
        RoundPlant.attackers_won, RoundPlant.defused, Match.map_name, Replay.match_uuid,
        Replay.game_branch, Replay.clock_offset, Match.played_at,
    )).order_by(RoundPlant.match_id, RoundPlant.round_number)
    chosen = {}
    rows = []
    for (match_id, round_number, site, u, v, plant_time, attackers_alive, defenders_alive, attackers_won,
         defused, map_name, uuid, branch, offset, played_at) in db.execute(query):
        if not plant_site_changes.counts(map_name, site, played_at):
            continue
        key = (map_name, branch)
        if key not in chosen:
            chosen[key] = asset_key_for(map_name, branch)
        if chosen[key] is None:
            continue
        rows.append(dict(
            match_id=match_id, round_number=round_number, asset_key=chosen[key],
            site=site, u=u, v=v, plant_time=plant_time,
            attackers_alive=attackers_alive, defenders_alive=defenders_alive,
            attackers_won=attackers_won, defused=bool(defused),
            uuid=uuid, clock_offset=offset))
    return rows


def _before_move():
    """SQL true for a plant on a moved site from a match played before the move."""
    moved = [and_(Match.map_name == map_name, RoundPlant.site == site, Match.played_at < start)
             for (map_name, site), start in plant_site_changes.cutoffs().items()]
    # A NULL map or played_at would make the test NULL, which `not_` would drop: read it as kept.
    return func.coalesce(or_(*moved), false()) if moved else false()


def counts_by_asset(db) -> Counter:
    """Usable plants per layout from one grouped query; the same answer as counting `read_rows`."""
    query = _joined(select(Match.map_name, Replay.game_branch, func.count())
                    .where(not_(_before_move()))).group_by(Match.map_name, Replay.game_branch)
    counts = Counter()
    for map_name, branch, n in db.execute(query):
        key = asset_key_for(map_name, branch)
        if key is not None:
            counts[key] += n
    return counts


def _population(ids) -> str:
    if ids is None:
        return "all"
    return "c" + hashlib.sha256(",".join(map(str, sorted(ids))).encode("utf-8")).hexdigest()[:16]


def _flat_spots(asset_key):
    sites = plant_spots.load().get(asset_key, {})
    return [{"id": spot["id"], "name": spot["name"], "site": site, "u": spot["u"], "v": spot["v"],
             **({"w": spot["w"], "h": spot["h"], "angle": spot["angle"]} if "w" in spot
                else {"radius": spot["r"]})}
            for site, spots in sites.items() for spot in spots]


def _box(spot):
    """(x0, y0, x1, y1) of a spot's shape, a rotated rectangle's by its corners."""
    if "radius" in spot:
        r = spot["radius"]
        return spot["u"] - r, spot["v"] - r, spot["u"] + r, spot["v"] + r
    turn = math.radians(spot["angle"])
    half_u = (abs(math.cos(turn)) * spot["w"] + abs(math.sin(turn)) * spot["h"]) / 2
    half_v = (abs(math.sin(turn)) * spot["w"] + abs(math.cos(turn)) * spot["h"]) / 2
    return spot["u"] - half_u, spot["v"] - half_v, spot["u"] + half_u, spot["v"] + half_v


def _crops_and_anchors(rows, spots):
    """Per site the zoom crop [cx, cy, side] (or None), and per (site, spot id) the anchor [u, v]:
    the median position of the plants in the spot (all players), else the shape's centre."""
    assigned = plant_stats.assign_spots(rows, spots)
    inside = defaultdict(list)
    for row, spot_id in zip(rows, assigned):
        if spot_id != "outside":
            inside[row["site"], spot_id].append(row)
    anchors = {}
    for spot in spots:
        found = inside.get((spot["site"], spot["id"]))
        anchors[spot["site"], spot["id"]] = (
            [median(r["u"] for r in found), median(r["v"] for r in found)] if found
            else [spot["u"], spot["v"]])
    crops = {}
    for site in {spot["site"] for spot in spots} | {row["site"] for row in rows}:
        points = [(row["u"], row["v"]) for row in rows if row["site"] == site]
        boxes = [_box(spot) for spot in spots if spot["site"] == site]
        crop = plant_map_view.site_crop(points, boxes)
        crops[site] = None if crop is None else list(crop)
    return crops, anchors


def _compute(db, asset_key, ids):
    rows = read_rows(db)
    expected = plant_stats.expected_oof(rows)          # fitted once, on every layout
    picked = [(row, value) for row, value in zip(rows, expected) if row["asset_key"] == asset_key]
    spots = _flat_spots(asset_key)
    crops, anchors = _crops_and_anchors([row for row, _ in picked], spots)   # all players, before Friends
    if ids is not None:
        teams = defaultdict(set)
        for match_id, team in db.execute(select(MatchPlayer.match_id, MatchPlayer.team)
                                         .where(MatchPlayer.player_id.in_(ids))):
            teams[match_id].add(team)
        picked = [(row, value) for row, value in picked
                  if attacking_team(row["round_number"]) in teams[row["match_id"]]]
    subset = [row for row, _ in picked]
    sites = plant_stats.spot_rows(subset, spots, min_n=MIN_N, expectations=[value for _, value in picked])
    for name, block in sites.items():
        block["crop"] = crops.get(name)
        for spot in block["spots"]:
            spot["anchor"] = anchors[name, spot["id"]]
    data = {"asset_key": asset_key, "population": "friends" if ids is not None else "all",
            "min_n": MIN_N, "sites": sites}
    return json.loads(json.dumps(data))                # what a cache hit returns, tuples included


def _read_cache(db, asset_key, population, current):
    try:
        row = db.get(PlantStatsCache, (asset_key, population))
        if row is None or row.fingerprint != current:
            return None
        return json.loads(zlib.decompress(row.data))
    except Exception:
        logger.warning("plant stats cache row unreadable; recomputing", exc_info=True)
        return None


def _store_cache(db, asset_key, population, current, data):
    try:
        db.merge(PlantStatsCache(asset_key=asset_key, population=population, fingerprint=current,
                                 data=zlib.compress(json.dumps(data).encode("utf-8")),
                                 computed_at=datetime.now(timezone.utc)))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("plant stats cache row not stored", exc_info=True)


def page_data(db, asset_key, viewer_id):
    """One layout's statistics: Everyone when `viewer_id` is None, else that viewer's circle."""
    ids = circle.circle_ids(db, viewer_id) if viewer_id is not None else None
    population = _population(ids)
    current = fingerprint(db)
    data = _read_cache(db, asset_key, population, current)
    if data is None:
        data = _compute(db, asset_key, ids)
        _store_cache(db, asset_key, population, current, data)
    return data
