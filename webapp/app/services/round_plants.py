"""Plant extraction, round outcomes and storage; callers own the transaction."""
from collections import defaultdict
from contextlib import nullcontext
from datetime import datetime, timezone
from functools import lru_cache
import json
import math
from pathlib import Path
import zlib

from sqlalchemy import select

from app.adapters.tracker_verification import TrackerMatchUnavailable, TrackerVerificationTimeout
from app.models import KillEvent, Match, MatchPlayer, Round, RoundPlant
from app.models.match import Team
from app.models.replay import Replay, ReplayRound
from app.replays import layouts
from app.replays.condense import load_maps
from app.replays.format import decode_blob
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
                and row.planted and values["plant_time"] is not None):
            a_ids, d_ids = entry.team1_alive_ids, entry.team2_alive_ids
            if attack == Team.TEAM_2:
                a_ids, d_ids = d_ids, a_ids
            values.update(attackers_alive=len(a_ids), defenders_alive=len(d_ids),
                          attackers_won=entry.winner == attack)
        result[row.round_number] = values
    return result


def upsert_plants(db, match, plants, source, *, dry_run=False):
    """Store normalized position dictionaries keyed by ``round_number``; do not commit.

    Positions supply integer u/v, site, and optionally asset_key/game_branch and
    DB-clock plant_time. Replay callers can instead supply Bomb t and clock_offset.
    A stored Round.plant_time always wins. Unknown rounds and invalid positions
    are counted as skipped, as are tracker updates protected by replay precedence.
    Storage errors propagate so the caller can roll back its post-ingest transaction.
    Dry runs count changes without issuing writes or changing ORM objects.
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
            if not dry_run:
                db.add(stored)
            existing[rnd.id] = stored
            summary["inserted"] += 1
        elif any(getattr(stored, key) != value for key, value in values.items()):
            if not dry_run:
                for key, value in values.items():
                    setattr(stored, key, value)
                stored.updated_at = datetime.now(timezone.utc)
            summary["updated"] += 1
    if not dry_run:
        db.flush()
    return summary


UNAVAILABLE_PATH = Path(__file__).resolve().parents[2] / "ingest_ledgers" / "round_plants_unavailable.json"


def load_unavailable():
    """Local external match IDs only; fail visibly rather than overwrite a bad ledger."""
    if not UNAVAILABLE_PATH.exists():
        return set()
    rows = json.loads(UNAVAILABLE_PATH.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or any(not isinstance(row, str) for row in rows):
        raise ValueError("round plants unavailable ledger must be a list of external match IDs")
    return set(rows)


def _save_unavailable(rows):
    UNAVAILABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = UNAVAILABLE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(sorted(rows)) + "\n", encoding="utf-8")
    temporary.replace(UNAVAILABLE_PATH)


def pending_matches(db, limit=None, skip=()):
    """Newest matches with any planted DB round still missing its position."""
    missing = (select(Round.id).where(
        Round.match_id == Match.id, Round.planted.is_(True),
        ~select(RoundPlant.round_id).where(RoundPlant.round_id == Round.id).exists(),
    ).exists())
    query = select(Match).where(missing)
    if skip:
        query = query.where(Match.external_id.not_in(skip))
    query = query.order_by(Match.played_at.desc().nullslast(), Match.created_at.desc(), Match.id.desc())
    if limit is not None:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        query = query.limit(limit)
    return db.scalars(query).all()


def sync_replays(db, dry_run=False):
    """Sync linked replay Bombs, isolating bad rounds and committing each replay.

    Dry runs execute the same validation without issuing any writes.
    The standard blob reader handles gzip; old zlib rows have a narrow fallback.
    """
    result = dict(replays=0, inserted=0, updated=0, skipped=0)
    replays = db.scalars(select(Replay).where(
        Replay.link_status == "linked", Replay.match_id.is_not(None)).order_by(Replay.id)).all()
    for replay in replays:
        match = db.get(Match, replay.match_id)
        positions = []
        for row in db.scalars(select(ReplayRound).where(
                ReplayRound.replay_id == replay.id).order_by(ReplayRound.round_number)).all():
            try:
                try:
                    blob = decode_blob(row.data)
                except OSError:
                    blob = json.loads(zlib.decompress(row.data))
                bomb = next((event for event in blob.get("util") or ()
                             if event.get("k") == "ability" and event.get("kind") == "Bomb"), None)
                if bomb is None:
                    continue
                positions.append(dict(
                    round_number=row.round_number, u=bomb.get("u"), v=bomb.get("v"),
                    t=bomb.get("t"), clock_offset=replay.clock_offset,
                    game_branch=replay.game_branch,
                ))
            except Exception:
                # Decode failures are counted before the batch; valid rounds still sync.
                result["skipped"] += 1
        if positions:
            try:
                with nullcontext() if dry_run else db.begin_nested():
                    counts = upsert_plants(db, match, positions, source="replay", dry_run=dry_run)
            except Exception:
                # Roll back the entire batch before isolating a refused plant.
                counts = dict(inserted=0, updated=0, skipped=0)
                for position in positions:
                    try:
                        with nullcontext() if dry_run else db.begin_nested():
                            single = upsert_plants(db, match, [position], source="replay", dry_run=dry_run)
                    except Exception:
                        counts["skipped"] += 1
                    else:
                        for key in single:
                            counts[key] += single[key]
            for key in counts:
                result[key] += counts[key]
        if dry_run:
            db.rollback()
        else:
            db.commit()
        result["replays"] += 1
    return result


def _tracker_plants(match, payload):
    positions = []
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        return positions
    for summary in payload["segments"]:
        if not isinstance(summary, dict) or summary.get("type") != "round-summary":
            continue
        attributes = summary.get("attributes")
        if not isinstance(attributes, dict) or type(attributes.get("round")) is not int:
            continue
        plant = plant_from_summary(summary)
        if plant is None:
            continue
        try:
            u, v = to_uv(match.map_name, plant["x"], plant["y"])
        except KeyError:
            continue
        positions.append(dict(round_number=attributes["round"],
                              u=u, v=v, site=plant["site"]))
    return positions


def backfill(db, page, *, limit, fetch, sleep, uniform, retry_unavailable=False):
    """Commit positions alone per match; the DB and local ledger are the resume point."""
    unavailable = load_unavailable()
    todo = pending_matches(db, limit, skip=() if retry_unavailable else unavailable)
    result = dict(done=0, failed=0, unavailable=0, inserted=0, updated=0, skipped=0, stopped=None)
    consecutive_failures = 0
    for index, match in enumerate(todo):
        if index:
            sleep(uniform(5, 12))
        external_id = match.external_id
        try:
            payload = fetch(page, external_id)
            if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
                raise TrackerMatchUnavailable("match payload has no usable segments")
            counts = upsert_plants(db, match, _tracker_plants(match, payload), source="tracker")
            db.commit()
        except TrackerVerificationTimeout:
            db.rollback()
            result["stopped"] = "verification"
            break
        except TrackerMatchUnavailable:
            db.rollback()
            unavailable.add(external_id)
            result["unavailable"] += 1
            consecutive_failures = 0
        except Exception:
            db.rollback()
            result["failed"] += 1
            consecutive_failures += 1
        else:
            consecutive_failures = 0
            result["done"] += 1
            for key in counts:
                result[key] += counts[key]
            unavailable.discard(external_id)
            # A partial response also needs a ledger entry so it cannot starve later matches.
            if db.scalar(select(Round.id).where(
                    Round.match_id == match.id, Round.planted.is_(True),
                    ~select(RoundPlant.round_id).where(RoundPlant.round_id == Round.id).exists(),
            ).limit(1)) is not None:
                unavailable.add(external_id)
                result["unavailable"] += 1
        _save_unavailable(unavailable)
        if consecutive_failures >= 5:
            result["stopped"] = "failures"
            break
    result["left"] = len(pending_matches(db, skip=unavailable))
    return result
