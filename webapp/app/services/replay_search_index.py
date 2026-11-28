"""Storage for the replay search index (R8, R9, D9, D10): the loader that gathers one replay's DB-free `inputs`
for `app.search.index.build_rows`, the cheap source key that says when a replay's rows are out of date, and the
per-replay rebuild (delete, insert, state row, one transaction).

Rows come from the stored ok moments/util/gap rows as they are, without the control freshness check (D9); a
re-gap stores new rows, which changes the key. Man-advantage states come from `replay_view.round_annotations`
(D10). Imports no scoring code beyond `app.scoring.plant_window.attacking_team`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import delete, func, insert, select

from app.models import KillEvent, Match, MatchPlayer, Round, RoundPlant, RoundPlayerStat
from app.models.replay import (Replay, ReplayGap, ReplayPlayer, ReplayRoundMoments, ReplayRoundUtil)
from app.models.replay_search import ReplayMomentIndex, ReplayMomentIndexState
from app.scoring.plant_window import attacking_team
from app.search import index
from app.search.match import ROW_KEYS
from app.services import replay_view
from app.services.state_replay import _winner_team

CHUNK = 400  # ids per IN list
PENDING_CHUNK = 25  # replays whose keys `pending` computes at a time


def _chunks(items, size=CHUNK):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _linked(db, replay_ids=None):
    """[(replay id, match id)] of linked replays, newest id first, optionally restricted to `replay_ids`."""
    query = select(Replay.id, Replay.match_id).where(Replay.link_status == "linked", Replay.match_id.is_not(None))
    if replay_ids is not None:
        query = query.where(Replay.id.in_(list(replay_ids)))
    return [(rid, mid) for rid, mid in db.execute(query.order_by(Replay.id.desc())).all()]


def _iso(moment):
    """The ISO string in UTC with an explicit offset (a naive value is read as UTC), or None."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _team_value(team):
    return getattr(team, "value", team)


def _on_replay_clock(value, offset):
    return None if value is None else round(value - offset, 3)


def _round3(value):
    """`ReplayGap.t_open` is a REAL (float32), so 41.47 comes back as 41.470001220703125."""
    return None if value is None else round(float(value), 3)


def load_inputs(db, replay_id: int) -> dict | None:
    """The DB-free `inputs` of `index.build_rows`; None when the replay is not linked to a match."""
    replay = db.get(Replay, replay_id)
    if replay is None or replay.link_status != "linked" or replay.match_id is None:
        return None
    match = db.get(Match, replay.match_id)
    if match is None:
        return None
    offset = float(replay.clock_offset or 0.0)

    mp_rows = {mp.id: mp for mp in db.query(MatchPlayer).filter(MatchPlayer.match_id == match.id)}
    players, slot_of = {}, {}
    for rp in db.query(ReplayPlayer).filter(ReplayPlayer.replay_id == replay_id).order_by(ReplayPlayer.slot):
        mp = mp_rows.get(rp.match_player_id)
        players[rp.slot] = {"player_id": mp.player_id if mp else None, "agent": rp.agent,
                            "team": _team_value(mp.team) if mp else None}
        if mp is not None:
            slot_of[mp.id] = rp.slot

    rounds = db.query(Round).filter(Round.match_id == match.id).order_by(Round.round_number).all()
    by_id = {r.id: r for r in rounds}
    sites = {p.round_id: p.site for p in db.query(RoundPlant).filter(RoundPlant.match_id == match.id)}
    loadouts: dict[int, dict] = {r.id: {"team-1": [], "team-2": []} for r in rounds}
    for round_id, mp_id, loadout in db.query(RoundPlayerStat.round_id, RoundPlayerStat.match_player_id,
                                             RoundPlayerStat.loadout).filter(RoundPlayerStat.round_id.in_(list(by_id))):
        mp = mp_rows.get(mp_id)
        if mp is not None and _team_value(mp.team) in loadouts[round_id]:
            loadouts[round_id][_team_value(mp.team)].append(loadout)
    kills: dict[int, list] = {r.id: [] for r in rounds}
    for k in (db.query(KillEvent).filter(KillEvent.round_id.in_(list(by_id)))
              .order_by(KillEvent.event_time_seconds, KillEvent.id)):
        victim = slot_of.get(k.death_match_player_id)
        if victim is None:
            continue
        kills[k.round_id].append({"t": round(k.event_time_seconds - offset, 3),
                                  "killer_slot": slot_of.get(k.killer_match_player_id), "victim_slot": victim,
                                  "weapon": k.weapon})
    annotations = replay_view.round_annotations(db, match.id, offset)

    fights, casts = {}, {}
    for row in db.query(ReplayRoundMoments).filter(ReplayRoundMoments.replay_id == replay_id,
                                                   ReplayRoundMoments.status == "ok"):
        data = (row.fights or {}).get("fights")
        fights[row.round_number] = data if isinstance(data, list) else None
    for row in db.query(ReplayRoundUtil).filter(ReplayRoundUtil.replay_id == replay_id,
                                                ReplayRoundUtil.status == "ok"):
        data = (row.data or {}).get("casts")
        casts[row.round_number] = data if isinstance(data, list) else None
    backshots: dict[int, list] = {}
    for g in (db.query(ReplayGap).filter(ReplayGap.replay_id == replay_id, ReplayGap.kind == "backshot")
              .order_by(ReplayGap.round_number, ReplayGap.seq)):
        backshots.setdefault(g.round_number, []).append({"t": _round3(g.t_open), "victim_slot": g.victim_slot,
                                                         "cause": g.cause})

    out_rounds = {}
    for r in rounds:
        n = r.round_number
        annotation = annotations.get(n) or {}
        winner = _winner_team(r.outcome)
        side = attacking_team(n)
        out_rounds[n] = {
            "outcome": r.outcome,
            "attack_team": _team_value(side) if side else None,
            "won_team": _team_value(winner) if winner else None,
            "site": sites.get(r.id),
            "plant_t": _on_replay_clock(r.plant_time, offset),
            "defuse_t": _on_replay_clock(r.defuse_time, offset),
            "loadouts": loadouts[r.id],
            "states": None if annotation.get("excluded") or not annotation.get("states") else [
                {"t": s["t"], "alive": s["alive"]} for s in annotation["states"]],
            "kills": kills[r.id],
            "fights": fights.get(n),
            "casts": casts.get(n),
            "backshots": backshots.get(n, []),
        }
    return {"replay": {"id": replay.id, "uuid": str(replay.match_uuid), "map": replay.map_name,
                       "played_at": _iso(match.played_at), "match_id": match.id},
            "players": players, "rounds": out_rounds}


def key_parts(db, replay_ids) -> dict[int, dict]:
    """{replay id: the values its source key hashes}, in a few grouped queries and without loading any JSON.
    Replays that are not linked are left out."""
    linked = dict(_linked(db, replay_ids))
    parts: dict[int, dict] = {}
    for ids in _chunks(linked):
        reps = {r.id: r for r in db.query(Replay).filter(Replay.id.in_(ids))}
        matches = {m.id: m for m in db.query(Match).filter(Match.id.in_({linked[i] for i in ids}))}
        for rid in ids:
            replay, match = reps[rid], matches.get(linked[rid])
            parts[rid] = {
                "match_id": linked[rid], "uuid": str(replay.match_uuid), "map": replay.map_name,
                "played_at": _iso(match.played_at) if match else None,
                "clock_offset": float(replay.clock_offset or 0.0),
                "moments": {}, "util": {}, "backshots": [], "slots": {},
            }
        for model, name in ((ReplayRoundMoments, "moments"), (ReplayRoundUtil, "util")):
            for row in db.query(model.replay_id, model.round_number, model.status, model.fingerprint,
                                model.gaps_revision).filter(model.replay_id.in_(ids)).order_by(
                                    model.replay_id, model.round_number):
                parts[row.replay_id][name][str(row.round_number)] = [row.status, row.fingerprint,
                                                                     row.gaps_revision]
        for g in (db.query(ReplayGap.replay_id, ReplayGap.round_number, ReplayGap.seq, ReplayGap.t_open,
                           ReplayGap.victim_slot, ReplayGap.cause)
                  .filter(ReplayGap.replay_id.in_(ids), ReplayGap.kind == "backshot")
                  .order_by(ReplayGap.replay_id, ReplayGap.round_number, ReplayGap.seq)):
            parts[g.replay_id]["backshots"].append([g.round_number, g.seq, g.t_open, g.victim_slot, g.cause])
        for rp in db.query(ReplayPlayer.replay_id, ReplayPlayer.slot, ReplayPlayer.match_player_id,
                           ReplayPlayer.agent).filter(ReplayPlayer.replay_id.in_(ids)):
            parts[rp.replay_id]["slots"][str(rp.slot)] = [rp.match_player_id, rp.agent]

        by_match: dict[int, list[int]] = {}
        for rid in ids:
            by_match.setdefault(linked[rid], []).append(rid)
        match_ids = list(by_match)
        players: dict[int, list] = {m: [] for m in match_ids}
        for mp in (db.query(MatchPlayer.id, MatchPlayer.match_id, MatchPlayer.player_id, MatchPlayer.team)
                   .filter(MatchPlayer.match_id.in_(match_ids)).order_by(MatchPlayer.id)):
            players[mp.match_id].append([mp.id, mp.player_id, _team_value(mp.team)])
        round_info: dict[int, dict] = {m: {} for m in match_ids}
        round_match: dict[int, tuple[int, int]] = {}
        for r in db.query(Round.id, Round.match_id, Round.round_number, Round.outcome, Round.planted,
                          Round.exploded, Round.defused, Round.plant_time, Round.defuse_time).filter(
                              Round.match_id.in_(match_ids)):
            round_info[r.match_id][str(r.round_number)] = [r.outcome, r.planted, r.exploded, r.defused,
                                                           r.plant_time, r.defuse_time]
            round_match[r.id] = (r.match_id, r.round_number)
        sites: dict[int, dict] = {m: {} for m in match_ids}
        for p in db.query(RoundPlant.match_id, RoundPlant.round_number, RoundPlant.site).filter(
                RoundPlant.match_id.in_(match_ids)):
            sites[p.match_id][str(p.round_number)] = p.site
        kill_ids: dict[int, list] = {m: [] for m in match_ids}
        for k in (db.query(KillEvent.id, KillEvent.round_id, KillEvent.event_time_seconds,
                           KillEvent.killer_match_player_id, KillEvent.death_match_player_id, KillEvent.weapon)
                  .join(Round, Round.id == KillEvent.round_id).filter(Round.match_id.in_(match_ids))
                  .order_by(KillEvent.id)):
            kill_ids[round_match[k.round_id][0]].append([k.id, k.round_id, k.event_time_seconds,
                                                         k.killer_match_player_id, k.death_match_player_id,
                                                         k.weapon])
        digests: dict[int, list] = {m: [] for m in match_ids}
        for s in (db.query(RoundPlayerStat.round_id, RoundPlayerStat.match_player_id, RoundPlayerStat.loadout)
                  .join(Round, Round.id == RoundPlayerStat.round_id).filter(Round.match_id.in_(match_ids))):
            m, n = round_match[s.round_id]
            digests[m].append((n, s.match_player_id, s.loadout))
        for m, rids in by_match.items():
            digest = hashlib.sha256(json.dumps(sorted(digests[m])).encode("utf-8")).hexdigest()
            for rid in rids:
                parts[rid].update({"rounds": round_info[m], "sites": sites[m], "kills": kill_ids[m],
                                   "loadouts": digest, "players": players[m]})
    return parts


def source_keys(db, replay_ids=None) -> dict[int, str]:
    """{replay id: source key} over the linked replays (all of them, or just `replay_ids`)."""
    ids = [rid for rid, _ in _linked(db, replay_ids)]
    return {rid: index.source_key(p) for rid, p in key_parts(db, ids).items()}


def build_for_replay(db, replay_id: int) -> list[dict]:
    """The replay's index rows, in memory; [] when it is not linked."""
    inputs = load_inputs(db, replay_id)
    return [] if inputs is None else index.build_rows(inputs)


def _now():
    return datetime.now(timezone.utc)


def rebuild(session_factory, replay_id: int) -> str:
    """Rebuild one replay's rows in one transaction. Never raises. A failure rolls back and writes only `error`
    and `built_at` on the state row, so the old key and revision stay and the replay remains pending."""
    try:
        with session_factory() as db:
            key = None
            try:
                parts = key_parts(db, [replay_id]).get(replay_id)
                if parts is None:
                    return "skipped: not linked"
                key = index.source_key(parts)
                revision = index.INDEX_REVISION
                inputs = load_inputs(db, replay_id)
                rows = [] if inputs is None else index.build_rows(inputs)
                db.execute(delete(ReplayMomentIndex).where(ReplayMomentIndex.replay_id == replay_id))
                for chunk in _chunks(rows, 1000):
                    db.execute(insert(ReplayMomentIndex), [{k: r[k] for k in ROW_KEYS} for r in chunk])
                state = db.get(ReplayMomentIndexState, replay_id)
                if state is None:
                    state = ReplayMomentIndexState(replay_id=replay_id)
                    db.add(state)
                state.key, state.revision, state.row_count = key, revision, len(rows)
                state.error, state.built_at = None, _now()
                db.commit()
                return f"rebuilt: {len(rows)} rows"
            except Exception as exc:  # noqa: BLE001 - recorded, never raised
                db.rollback()
                message = f"{type(exc).__name__}: {exc}"
                try:
                    state = db.get(ReplayMomentIndexState, replay_id)
                    if state is None:
                        state = ReplayMomentIndexState(replay_id=replay_id, key="", revision=0, row_count=0)
                        db.add(state)
                    elif key is not None and state.key == key and state.revision == index.INDEX_REVISION:
                        # another build of this replay already stored this key: its row is current, leave it
                        # (not a failure: the sweep resolves any Site health row for it)
                        return "skipped: built elsewhere"
                    state.error, state.built_at = message, _now()
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
                return f"failed: {message}"
    except Exception as exc:  # noqa: BLE001 - e.g. no session
        return f"failed: {type(exc).__name__}: {exc}"


def _states(db):
    return {s.replay_id: s for s in db.query(ReplayMomentIndexState)}


def pending(db, limit: int, skip=()) -> list[int]:
    """Linked replays whose state row is missing, has another key or another revision, newest id first."""
    skipped = set(skip or ())
    states = _states(db)
    todo = [rid for rid, _ in _linked(db) if rid not in skipped]
    out: list[int] = []
    for ids in _chunks(todo, PENDING_CHUNK):
        need_key = []
        for rid in ids:
            state = states.get(rid)
            if state is None or state.revision != index.INDEX_REVISION:
                out.append(rid)
            else:
                need_key.append(rid)
        keys = {rid: index.source_key(p) for rid, p in key_parts(db, need_key).items()} if need_key else {}
        out.extend(rid for rid in need_key if states[rid].key != keys.get(rid))
        if len(out) >= limit:
            break
    return sorted(out, reverse=True)[:limit]


def drop_unlinked(db) -> list[int]:
    """Delete the index and state rows of replays no longer linked; the dropped replay ids, sorted (`len` is the
    number dropped). The caller commits."""
    linked = {rid for rid, _ in _linked(db)}
    held = set(db.execute(select(ReplayMomentIndex.replay_id).distinct()).scalars())
    held |= set(db.execute(select(ReplayMomentIndexState.replay_id)).scalars())
    gone = held - linked
    for ids in _chunks(gone):
        db.execute(delete(ReplayMomentIndex).where(ReplayMomentIndex.replay_id.in_(ids)))
        db.execute(delete(ReplayMomentIndexState).where(ReplayMomentIndexState.replay_id.in_(ids)))
    return sorted(gone)


def rows_for_replay(db, replay_id: int) -> tuple[bool, dict[int, list[dict]]]:
    """(is the stored index current for this replay, {round: rows in seq order}); (False, {}) with no state row."""
    state = db.get(ReplayMomentIndexState, replay_id)
    if state is None:
        return False, {}
    keys = source_keys(db, [replay_id])
    current = (state.error is None and state.revision == index.INDEX_REVISION
               and keys.get(replay_id) == state.key)
    by_round: dict[int, list[dict]] = {}
    for row in db.execute(select(ReplayMomentIndex).where(ReplayMomentIndex.replay_id == replay_id)
                          .order_by(ReplayMomentIndex.round, ReplayMomentIndex.seq)).scalars():
        by_round.setdefault(row.round, []).append({k: getattr(row, k) for k in ROW_KEYS})
    return current, by_round


def status(db) -> dict:
    """Counts over the linked replays, mutually exclusive in this order: missing (no state row), failed (the state
    row holds an error), stale (another key or revision), indexed."""
    linked = [rid for rid, _ in _linked(db)]
    states = _states(db)
    counts = {"linked": len(linked), "indexed": 0, "missing": 0, "stale": 0, "failed": 0}
    check = []
    for rid in linked:
        state = states.get(rid)
        if state is None:
            counts["missing"] += 1
        elif state.error is not None:
            counts["failed"] += 1
        elif state.revision != index.INDEX_REVISION:
            counts["stale"] += 1
        else:
            check.append(rid)
    for ids in _chunks(check, PENDING_CHUNK):
        for rid, p in key_parts(db, ids).items():
            counts["indexed" if index.source_key(p) == states[rid].key else "stale"] += 1
    return counts
