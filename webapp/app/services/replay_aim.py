"""Reads the stored aim events (migration 0039): the only place the site reads them. Only current rows are
returned, by Critical Moments' rule (replay_util.current_rows): the gap run's revision, and the round's
fingerprint against the one its control gives now (`replay_gaps.Freshness`), decided once per round."""

from __future__ import annotations

from app.services import replay_gaps


def current_events(db, replay_ids=None, slots_by_replay=None) -> list:
    """Every `ReplayAimEvent` whose gaps_revision is current and whose round is current, ordered by replay,
    round and idx. With `replay_ids` only those replays are read (an empty list reads none). With
    `slots_by_replay` ({replay id: slots}) only those slots' rows of those replays are read, filtered in SQL;
    it takes the place of `replay_ids`."""
    from sqlalchemy import and_, or_

    from app.models.replay import Replay, ReplayAimEvent

    q = db.query(ReplayAimEvent).filter(ReplayAimEvent.gaps_revision == replay_gaps.GAPS_REVISION)
    if slots_by_replay is not None:
        by_slots: dict = {}
        for rid, slots in slots_by_replay.items():
            if slots:
                by_slots.setdefault(frozenset(slots), []).append(rid)
        if not by_slots:
            return []
        q = q.filter(or_(*(and_(ReplayAimEvent.replay_id.in_(sorted(rids)),
                                ReplayAimEvent.slot.in_(sorted(slots)))
                           for slots, rids in by_slots.items())))
    elif replay_ids is not None:
        q = q.filter(ReplayAimEvent.replay_id.in_(list(replay_ids)))
    rows = q.order_by(ReplayAimEvent.replay_id, ReplayAimEvent.round_number, ReplayAimEvent.idx).all()
    ids = {r.replay_id for r in rows}
    replays = {r.id: r for r in db.query(Replay).filter(Replay.id.in_(list(ids)))} if ids else {}
    fresh = replay_gaps.Freshness(db)
    fresh.preload(replays)
    verdicts: dict = {}
    out = []
    for r in rows:
        if r.replay_id not in replays:
            continue
        key = (r.replay_id, r.round_number, r.gaps_revision, r.fingerprint)
        if key not in verdicts:
            verdicts[key] = fresh.current(replays[r.replay_id], r.round_number, r.gaps_revision, r.fingerprint)
        if verdicts[key]:
            out.append(r)
    return out
