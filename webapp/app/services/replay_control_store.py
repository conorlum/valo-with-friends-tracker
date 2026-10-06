"""Stores one round's computed control (`replay_round_control`; docs/replay-map-control-plan.md,
"Storage"). Used by scripts/compute_control.py (local) and by the web app's dispatcher for the replay
worker (app/services/replay_control_remote.py; docs/map-control-worker-plan.md, S6).

`store_round` merges the row with the fingerprint the round was planned with. With `require_current`
(the dispatcher, whose results can arrive minutes later), under the replay's advisory lock it first
recomputes the round's fingerprint and stores nothing if it moved (a new link, a re-ingest) or if a row
with the current fingerprint is already there (the local command got there first).

Every row records `control_format.CONTROL_REVISION` (migration 0017); both writers only store results
computed under this deploy's revision.

Standard library and the DB only: the engine is never imported here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.models.replay import Replay, ReplayRoundControl
from app.replays import control_format as cf
from app.replays import db as replay_db
from app.services import replay_control

STORED = "stored"


def store_round(session_factory, replay_id: int, round_number: int, fingerprint: str | None, result: dict, *,
                require_current: bool = False) -> str:
    """"stored", or "skipped: <why>"."""
    session = session_factory()
    try:
        if require_current:
            replay = session.get(Replay, replay_id)
            if replay is None:
                return "skipped: the replay is gone"
            replay_db.advisory_lock(session, str(replay.match_uuid))
            current = replay_control.round_fingerprint(replay, replay_control.side_groups(session, replay),
                                                       round_number)
            if current != fingerprint:
                return "skipped: its inputs changed while computing"
            row = session.get(ReplayRoundControl, (replay_id, round_number))
            if row is not None and row.fingerprint == current and row.status == "ok" \
                    and row.data_version == cf.DATA_VERSION:
                return "skipped: already stored"
        ok = result["status"] == "ok"
        session.merge(ReplayRoundControl(
            replay_id=replay_id, round_number=round_number, status=result["status"], fingerprint=fingerprint,
            data_version=cf.DATA_VERSION if ok else None, control_revision=cf.CONTROL_REVISION,
            data=result.get("data"), summary=result.get("summary"),
            error=result.get("error"), computed_at=datetime.now(timezone.utc)))
        session.commit()
        return STORED
    except IntegrityError:
        session.rollback()
        return "skipped: the replay changed while computing (re-ingested?)"
    finally:
        session.rollback()
        session.close()
