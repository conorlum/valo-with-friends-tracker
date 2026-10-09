"""Stores one round's timing gaps (migration 0016; docs/superpowers/specs/2026-10-02-timing-gaps-design.md,
section 7): the run row and all gap rows of a round are replaced in one transaction. Used by
scripts/compute_control.py (local) and by the web app's dispatcher for the replay worker
(app/services/replay_control_remote.py).

Every write takes the replay's advisory lock, so a local and a remote write of one round take turns. With
`expected_control_fingerprint` (the dispatcher, whose results arrive minutes later) the same transaction
first reads the replay again and stores nothing unless the round's control is still the one the gaps were
computed with, the run carries this deploy's gap keys, and no run with those keys is there already
(docs/superpowers/plans/2026-10-07-worker-gaps-and-kill-one-impl.md, Task 3). A relink keeps the replay's
and the round's keys, so the foreign key alone would not catch it.

Standard library and the DB only: the engine is never imported here."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.models.replay import Replay, ReplayGap, ReplayRoundControl, ReplayRoundGapRun
from app.replays import choke_assets
from app.replays import control_format as cf
from app.replays import db as replay_db
from app.services import control_heights, replay_control, replay_gaps

STORED = "stored"
ALREADY = "skipped: already stored"


def _after_lock(session) -> None:
    """Tests only: runs once the replay's lock is held, before anything is read under it."""


def _stale(session, replay_id: int, round_number: int, run: dict, expected: str) -> str | None:
    """Why a worker's gap run must not be stored now, or None. Called under the replay's lock, and reads the
    replay and its rows afresh: whatever was loaded before waiting for the lock may have moved."""
    replay = session.get(Replay, replay_id)
    if replay is None:
        return "skipped: the replay is gone"
    context = replay_control.resolve_current_geometry(session, replay.map_name)
    current = replay_control.round_fingerprint(replay, replay_control.side_groups(session, replay), round_number,
                                               context=context)
    if current is None or current != expected:
        return "skipped: its control inputs changed while computing"
    control = session.get(ReplayRoundControl, (replay_id, round_number))
    if control is None or control.status != "ok" or control.fingerprint != current \
            or control.data_version != cf.DATA_VERSION:
        return "skipped: the round has no current control"
    wanted = replay_gaps.gap_fingerprint(current, replay.map_name)
    if run.get("fingerprint") != wanted or run.get("gaps_revision") != replay_gaps.GAPS_REVISION \
            or run.get("chokes_hash") != choke_assets.asset_hash(replay.map_name):
        return "skipped: computed under other gap rules or assets"
    existing = session.get(ReplayRoundGapRun, (replay_id, round_number))
    if existing is not None and existing.fingerprint == wanted:
        return ALREADY                # another writer got there first; a current failure stays put too
    if not replay_control.current_source_matches(session, context):
        return 'skipped: its control source changed while computing'
    return None


def store_gaps(session_factory, replay_id: int, round_number: int, run: dict, rows: list[dict], *,
               expected_control_fingerprint: str | None = None) -> str:
    """"stored", or "skipped: <why>". Without `expected_control_fingerprint` (the local command, which has
    just planned the round) the round's gaps are replaced as they are given."""
    session = session_factory()
    try:
        # The UUID alone first, so no row is held in the session from before the lock.
        identity = session.query(Replay.map_name, Replay.match_uuid).filter(Replay.id == replay_id).first()
        match_uuid = identity.match_uuid if identity else None
        if match_uuid is not None:
            replay_db.advisory_lock(session, control_heights.lock_name(identity.map_name))
            replay_db.advisory_lock(session, str(match_uuid))
            _after_lock(session)
            session.expire_all()
            fresh = session.query(Replay.map_name, Replay.match_uuid).filter(Replay.id == replay_id).first()
            if fresh != identity:
                return 'skipped: the replay changed while waiting for locks'
        if expected_control_fingerprint is not None:
            if match_uuid is None:
                return "skipped: the replay is gone"
            why = _stale(session, replay_id, round_number, run, expected_control_fingerprint)
            if why is not None:
                return why
        session.query(ReplayGap).filter(ReplayGap.replay_id == replay_id,
                                        ReplayGap.round_number == round_number).delete()
        ok = run["status"] == "ok"
        session.merge(ReplayRoundGapRun(
            replay_id=replay_id, round_number=round_number, status=run["status"], fingerprint=run["fingerprint"],
            gaps_revision=run["gaps_revision"], chokes_hash=run.get("chokes_hash"),
            gap_count=len(rows) if ok else 0, notes=run.get("notes") or None, error=run.get("error"),
            computed_at=datetime.now(timezone.utc)))
        if ok:
            session.add_all(ReplayGap(replay_id=replay_id, round_number=round_number, **row) for row in rows)
        session.commit()
        return STORED
    except IntegrityError:
        session.rollback()
        return "skipped: the replay changed while computing (re-ingested?)"
    finally:
        session.rollback()
        session.close()
