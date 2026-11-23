"""Plant extraction, round outcomes and storage; callers own the transaction."""
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
import json
import math
from pathlib import Path

from sqlalchemy import select

from app.models import KillEvent, MatchPlayer, Round, RoundPlant
from app.models.match import Team
from app.replays import layouts
from app.replays.condense import load_maps
from app.scoring.plant_window import attacking_team, is_phantom_plant
from app.services import state_replay as sr


@lru_cache(maxsize=1)
def _maps():
    return load_maps()


def to_uv(map_name, x, y):
    """Use the replay's affine transform, integer rounding and 0..10000 clamp."""
    return _maps()[map_name].to_uv(x, y)


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def plant_from_summary(round_summary):
    """Read a tracker round-summary plant; malformed JSON shapes have no plant."""
    if not isinstance(round_summary, dict):
        return None
    metadata = round_summary.get("metadata")
    if not isinstance(metadata, dict):
        return None
    plant = metadata.get("plant")
    if not isinstance(plant, dict) or plant.get("site") not in ("A", "B", "C"):
        return None
    location = plant.get("location")
    if not isinstance(location, dict):
        return None
    x, y = location.get("x"), location.get("y")
    if not (_finite_number(x) and _finite_number(y)):
        return None
    return {"site": plant["site"], "x": x, "y": y}


def asset_key_for(map_name, game_branch):
    """Reselect on every read: a later layout makes branchless tracker rows unknown.

    A pending layout still has a known asset key; its control-layer availability
    does not change which layout a plant belongs to.
    """
    if not map_name:
        return None
    choices = layouts.layouts_of(map_name)
    if len(choices) == 1:
        return choices[0].asset_key
    selection = layouts.layout_for(map_name, game_branch)
    return selection.layout.asset_key if selection.layout is not None else None


@lru_cache(maxsize=1)
def _callouts():
    path = Path(__file__).resolve().parents[1] / "static" / "data" / "callouts.json"
    return json.loads(path.read_text(encoding="utf-8"))


def site_for_uv(map_name, u, v):
    """Return the nearest named bomb site, ignoring lobbies and other callouts."""
    sites = [row for row in _callouts().get(map_name, ()) if row["name"] in ("A Site", "B Site", "C Site")]
    if not sites:
        return None
    closest = min(sites, key=lambda row: (row["u"] - u) ** 2 + (row["v"] - v) ** 2)
    return closest["name"][0]


def outcomes_for_match(db, match_id, plant_times: dict[int, float] | None = None):
    """Replay this match with DB-clock fallback times, without changing Round rows.

    Stored times take precedence. A planted round without a usable time, an
    excluded round or a phantom plant retains NULL alive counts and outcome.
    """
    players = db.scalars(select(MatchPlayer).where(MatchPlayer.match_id == match_id)).all()
    rounds = db.scalars(select(Round).where(Round.match_id == match_id).order_by(Round.round_number)).all()
    times = {}
    for row in rounds:
        time = row.plant_time
        if time is None:
            time = (plant_times or {}).get(row.round_number)
        times[row.round_number] = time if _finite_number(time) else None
    kills = defaultdict(list)
    for kill in db.scalars(select(KillEvent).join(Round, KillEvent.round_id == Round.id)
                           .where(Round.match_id == match_id)):
        kills[kill.round_id].append(sr.KillEventInput(
            kill.id, kill.killer_match_player_id, kill.death_match_player_id, kill.event_time_seconds))
    inputs = tuple(sr.RoundInput(
        row.id, row.round_number, row.outcome, row.planted, times[row.round_number],
        row.exploded, row.defused, row.defuse_time, tuple(kills[row.id]),
    ) for row in rounds)
    entries, _, _ = sr.replay_match(sr.MatchInput(
        match_id,
        frozenset(player.id for player in players if player.team == Team.TEAM_1),
        frozenset(player.id for player in players if player.team == Team.TEAM_2), inputs,
    ))
    before_plant = {entry.round_id: entry for entry in entries if not entry.post_plant}
    result = {}
    for row in rounds:
        values = dict(attackers_alive=None, defenders_alive=None, attackers_won=None,
                      plant_time=times[row.round_number], defused=bool(row.defused))
        entry = before_plant.get(row.id)
        attack = attacking_team(row.round_number)
        if (entry is not None and attack is not None and not is_phantom_plant(row)
                and (not row.planted or values["plant_time"] is not None)):
            a_ids, d_ids = entry.team1_alive_ids, entry.team2_alive_ids
            if attack == Team.TEAM_2:
                a_ids, d_ids = d_ids, a_ids
            values.update(attackers_alive=len(a_ids), defenders_alive=len(d_ids),
                          attackers_won=entry.winner == attack)
        result[row.round_number] = values
    return result


def upsert_plants(db, match, plants, source):
    """Store normalized position dictionaries keyed by ``round_number``; do not commit.

    Positions supply integer u/v, site, and optionally asset_key/game_branch and
    DB-clock plant_time. Replay callers can instead supply Bomb t and clock_offset.
    A stored Round.plant_time always wins. Unknown rounds and invalid positions
    are counted as skipped, as are tracker updates protected by replay precedence.
    Storage errors propagate so the caller can roll back its post-ingest transaction.
    """
    if source not in ("tracker", "replay"):
        raise ValueError("plant source must be tracker or replay")
    rounds = {row.round_number: row for row in db.scalars(
        select(Round).where(Round.match_id == match.id))}
    existing = {row.round_id: row for row in db.scalars(
        select(RoundPlant).where(RoundPlant.match_id == match.id))}
    plants = list(plants)
    plant_times = {}
    for plant in plants:
        if not isinstance(plant, dict) or type(plant.get("round_number")) is not int:
            continue
        fallback = plant.get("plant_time")
        if source == "replay" and fallback is None and _finite_number(plant.get("t")):
            offset = plant.get("clock_offset") or 0
            if _finite_number(offset):
                fallback = plant["t"] + offset
        if _finite_number(fallback):
            plant_times[plant["round_number"]] = fallback
    outcomes = outcomes_for_match(db, match.id, plant_times=plant_times)
    summary = {"inserted": 0, "updated": 0, "skipped": 0}
    for plant in plants:
        if (not isinstance(plant, dict) or type(plant.get("round_number")) is not int
                or type(plant.get("u")) is not int or type(plant.get("v")) is not int):
            summary["skipped"] += 1
            continue
        rnd = rounds.get(plant["round_number"])
        if rnd is None:
            summary["skipped"] += 1
            continue
        stored = existing.get(rnd.id)
        if stored is not None and stored.source == "replay" and source == "tracker":
            summary["skipped"] += 1
            continue
        # Keep the known site on subsequent replay refreshes too, so a tracker
        # label retained during promotion cannot be lost on the next refresh.
        site = (stored.site if stored is not None and source == "replay"
                else plant.get("site"))
        if site not in ("A", "B", "C"):
            site = site_for_uv(match.map_name, plant["u"], plant["v"])
        if site is None:
            summary["skipped"] += 1
            continue
        values = dict(outcomes[rnd.round_number])
        values.update(match_id=match.id, round_number=rnd.round_number, source=source,
                      site=site, u=plant["u"], v=plant["v"],
                      asset_key=plant.get("asset_key", asset_key_for(match.map_name, plant.get("game_branch"))))
        if stored is None:
            stored = RoundPlant(round_id=rnd.id, **values)
            db.add(stored)
            existing[rnd.id] = stored
            summary["inserted"] += 1
        elif any(getattr(stored, key) != value for key, value in values.items()):
            for key, value in values.items():
                setattr(stored, key, value)
            stored.updated_at = datetime.now(timezone.utc)
            summary["updated"] += 1
    db.flush()
    return summary
