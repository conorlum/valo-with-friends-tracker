"""Count alive replay positions on non-walk cells, in bounded idle dispatcher passes."""

import logging
from datetime import datetime, timezone
from math import inf

from sqlalchemy import and_, or_

from app.config import settings
from app.models.replay import Replay, ReplayRound, ReplayRoundControl, ReplayRoundWallShare
from app.replays import control_format as cf
from app.replays import format as fmt
from app.services import replay_control, replay_reparse_auto

log = logging.getLogger(__name__)

STEP_ROWS = 20  # PROVISIONAL(D12): until W14 times one replay


def _cell(u, v) -> int:
    """Inverse of the control grid's cell-centre UV coordinates (column u, row v)."""
    return (min(cf.GRID - 1, int(v / fmt.UV_SCALE * cf.GRID)) * cf.GRID
            + min(cf.GRID - 1, int(u / fmt.UV_SCALE * cf.GRID)))


def round_share(control_data: bytes, round_blob: dict) -> tuple[int, int]:
    """(Inside walls, total) for 4 Hz samples alive on the replay's own clock."""
    header, _ = cf.unpack_data(control_data)
    walk = set(cf.walk_bitmap(header))
    inside = total = 0
    for slot, segments in round_blob.get("tracks", {}).items():
        lives = [(t0, inf if t1 is None else t1)
                 for t0, t1, _ in round_blob.get("alive", {}).get(slot, [])]
        for segment in segments:
            for t, u, v, _ in fmt.decode_segment(segment, round_blob["hz"]):
                if abs(round(t * 4) - t * 4) > 1e-6:
                    continue
                if any(t0 <= t < t1 for t0, t1 in lives):
                    total += 1
                    inside += _cell(u, v) not in walk
    return inside, total


def step(session_factory, now, *, control_in_flight: int) -> dict:
    """Store at most STEP_ROWS missing/stale shares, committing once in our own session."""
    if settings.demo_mode:
        return {"skipped": "demo mode"}
    if control_in_flight > 0:
        return {"skipped": "map control tasks are in flight"}
    session = session_factory()
    try:
        if replay_reparse_auto.ordinary_upload_unfinished(session):
            return {"skipped": "an upload is unfinished"}
        rows = (session.query(ReplayRoundControl, Replay, ReplayRound.data)
                .join(Replay, Replay.id == ReplayRoundControl.replay_id)
                .outerjoin(ReplayRound, and_(ReplayRound.replay_id == ReplayRoundControl.replay_id,
                                             ReplayRound.round_number == ReplayRoundControl.round_number))
                .outerjoin(ReplayRoundWallShare,
                           and_(ReplayRoundWallShare.replay_id == ReplayRoundControl.replay_id,
                                ReplayRoundWallShare.round_number == ReplayRoundControl.round_number))
                .filter(ReplayRoundControl.status == "ok",
                        or_(ReplayRoundWallShare.replay_id.is_(None),
                            ReplayRoundWallShare.control_fingerprint != ReplayRoundControl.fingerprint))
                .order_by(ReplayRoundControl.replay_id, ReplayRoundControl.round_number)
                .limit(STEP_ROWS).all())
        computed_at = datetime.fromtimestamp(now, timezone.utc)
        for control, replay, data in rows:
            try:
                inside, total = round_share(control.data, fmt.decode_blob(data)) if data is not None else (0, 0)
            except Exception:  # noqa: BLE001 - one unreadable round must not hold back every row after it
                log.warning("wall share of replay %s round %s unreadable; stored as 0/0",
                            control.replay_id, control.round_number, exc_info=True)
                inside, total = 0, 0
            layout = replay_control.layout_of(replay).layout
            session.merge(ReplayRoundWallShare(
                replay_id=control.replay_id, round_number=control.round_number,
                # layout_unknown has no layout; its control is stale, so no reader counts the row (W15 joins to
                # current rows only): the bare map name keeps it from being picked again.
                asset_key=layout.asset_key if layout is not None else replay.map_name,
                control_fingerprint=control.fingerprint, inside=inside, total=total, computed_at=computed_at))
        session.commit()
        return {"computed": len(rows)}
    finally:
        session.close()
