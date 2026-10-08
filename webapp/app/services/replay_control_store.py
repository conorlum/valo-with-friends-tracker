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
from app.replays.map_feature_artifacts import FeatureArtifactError
from app.services import control_heights, replay_control

STORED = "stored"
ALREADY = "skipped: already stored"


def store_round(session_factory, replay_id: int, round_number: int, fingerprint: str | None, result: dict, *,
                require_current: bool = False, planned_inputs=None) -> str:
    """"stored", or "skipped: <why>"."""
    session = session_factory()
    try:
        if result.get('error_kind') in ('infra', 'compat'):
            return 'skipped: retryable infrastructure or compatibility failure'
        try:
            provenance = replay_control.verify_result_inputs(planned_inputs, result)
        except replay_control.InvalidControlInputs:
            return 'skipped: invalid actual inputs'
        if require_current:
            identity = session.query(Replay.map_name, Replay.match_uuid).filter(Replay.id == replay_id).first()
            if identity is None:
                return "skipped: the replay is gone"
            replay_db.advisory_lock(session, control_heights.lock_name(identity.map_name))
            replay_db.advisory_lock(session, str(identity.match_uuid))
            session.expire_all()
            replay = session.get(Replay, replay_id)
            if replay is None or (replay.map_name, replay.match_uuid) != tuple(identity):
                return 'skipped: the replay changed while waiting for locks'
            context = replay_control.resolve_current_geometry(session, replay.map_name)
            current = replay_control.round_fingerprint(replay, replay_control.side_groups(session, replay),
                                                       round_number, context=context)
            if current is None or current != fingerprint or (planned_inputs is not None and
                    replay_control.geometry_inputs(replay.map_name, context=context) != planned_inputs.geometry):
                return "skipped: its inputs changed while computing"
            if context.pinned.artifact_digest and planned_inputs is None:
                return 'skipped: feature result has no pinned inputs'
            if provenance is not None:
                from app.services.control_feature_artifacts import load_artifact
                load_artifact(session, provenance['inputs']['geometry']['features'])
            row = session.get(ReplayRoundControl, (replay_id, round_number))
            if row is not None and row.fingerprint == current and row.status == "ok" \
                    and row.data_version == cf.DATA_VERSION:
                return ALREADY
            # Tags are filesystem inputs. Reidentify only if their raw snapshot changed while locked.
            import hashlib
            try:
                raw = replay_control._current_snapshot()[3]
                if hashlib.sha256(raw).hexdigest() != context.pinned.source_sha256:
                    final = replay_control.resolve_current_geometry(session, replay.map_name)
                    if final.state != 'ready' or final.pinned.geometry != context.pinned.geometry:
                        return 'skipped: its inputs changed while computing'
            except (OSError, ValueError):
                return 'skipped: current feature source is unavailable'
        ok = result["status"] == "ok"
        session.merge(ReplayRoundControl(
            replay_id=replay_id, round_number=round_number, status=result["status"], fingerprint=fingerprint,
            data_version=cf.DATA_VERSION if ok else None, control_revision=cf.CONTROL_REVISION,
            data=result.get("data"), summary=result.get("summary"),
            error=result.get("error"), computed_at=datetime.now(timezone.utc)))
        session.commit()
        return STORED
    except (IntegrityError, replay_control.InvalidControlInputs, FeatureArtifactError):
        session.rollback()
        return "skipped: the replay changed while computing (re-ingested?)"
    finally:
        session.rollback()
        session.close()
