"""Reads the stored aim events (migration 0039): the only place the site reads them. Only current rows are
returned, by Critical Moments' rule (replay_util.current_rows): the gap run's revision, and the round's
fingerprint against the one its control gives now (`replay_gaps.Freshness`)."""

from __future__ import annotations

from app.services import replay_gaps


def current_events(db, replay_ids=None) -> list:
    """Every `ReplayAimEvent` whose gaps_revision is current and whose round is current, ordered by replay,
    round and idx. With `replay_ids` only those replays are read (an empty list reads none)."""
    from app.models.replay import Replay, ReplayAimEvent

    q = db.query(ReplayAimEvent).filter(ReplayAimEvent.gaps_revision == replay_gaps.GAPS_REVISION)
    if replay_ids is not None:
        q = q.filter(ReplayAimEvent.replay_id.in_(list(replay_ids)))
    rows = q.order_by(ReplayAimEvent.replay_id, ReplayAimEvent.round_number, ReplayAimEvent.idx).all()
    ids = {r.replay_id for r in rows}
    replays = {r.id: r for r in db.query(Replay).filter(Replay.id.in_(list(ids)))} if ids else {}
    fresh = replay_gaps.Freshness(db)
    fresh.preload(replays)
    return [r for r in rows if r.replay_id in replays
            and fresh.current(replays[r.replay_id], r.round_number, r.gaps_revision, r.fingerprint)]
