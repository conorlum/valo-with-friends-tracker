"""Stores one round's timing gaps (migration 0016; docs/superpowers/specs/2026-10-02-timing-gaps-design.md,
section 7): the run row and all gap rows of a round are replaced in one transaction. Used by
scripts/compute_control.py. The engine is never imported here."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.models.replay import ReplayGap, ReplayRoundGapRun

STORED = "stored"


def store_gaps(session_factory, replay_id: int, round_number: int, run: dict, rows: list[dict]) -> str:
    session = session_factory()
    try:
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
