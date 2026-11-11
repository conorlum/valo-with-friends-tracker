"""Count alive replay positions on non-walk cells, in bounded idle dispatcher passes."""

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import inf

from sqlalchemy import and_, func, or_

from app.coach import areas
from app.config import settings
from app.models.replay import Replay, ReplayRound, ReplayRoundControl, ReplayRoundWallShare
from app.models.site_events import SiteEvent
from app.replays import control_format as cf
from app.replays import format as fmt
from app.replays import layouts
from app.services import replay_control, replay_reparse_auto, site_health

log = logging.getLogger(__name__)

STEP_ROWS = 20  # PROVISIONAL(D12): until W14 times one replay
MIN_REPLAYS = 4  # PROVISIONAL(D12)
THRESHOLD_POINTS = 0.5  # PROVISIONAL(D12)


def _cell(u, v) -> int:
    """Inverse of the control grid's cell-centre UV coordinates (column u, row v)."""
    return (min(cf.GRID - 1, int(v / fmt.UV_SCALE * cf.GRID)) * cf.GRID
            + min(cf.GRID - 1, int(u / fmt.UV_SCALE * cf.GRID)))


def _samples(control_data: bytes, round_blob: dict):
    """Yield (u, v, inside) for counted 4 Hz samples alive on the replay's own clock."""
    header, _ = cf.unpack_data(control_data)
    walk = set(cf.walk_bitmap(header))
    for slot, segments in round_blob.get("tracks", {}).items():
        lives = [(t0, inf if t1 is None else t1)
                 for t0, t1, _ in round_blob.get("alive", {}).get(slot, [])]
        for segment in segments:
            for t, u, v, _ in fmt.decode_segment(segment, round_blob["hz"]):
                if abs(round(t * 4) - t * 4) > 1e-6:
                    continue
                if any(t0 <= t < t1 for t0, t1 in lives):
                    yield u, v, _cell(u, v) not in walk


def round_share(control_data: bytes, round_blob: dict) -> tuple[int, int]:
    """(Inside walls, total) for 4 Hz samples alive on the replay's own clock."""
    inside = total = 0
    for _, _, in_wall in _samples(control_data, round_blob):
        total += 1
        inside += in_wall
    return inside, total


@dataclass
class _Pool:
    inside: int = 0
    total: int = 0
    replays: set[int] = field(default_factory=set)


def _current_shares(db):
    return (db.query(ReplayRoundWallShare)
            .join(ReplayRoundControl,
                  and_(ReplayRoundWallShare.replay_id == ReplayRoundControl.replay_id,
                       ReplayRoundWallShare.round_number == ReplayRoundControl.round_number,
                       ReplayRoundWallShare.control_fingerprint == ReplayRoundControl.fingerprint))
            .filter(ReplayRoundControl.status == "ok"))


def _callouts(db, asset_key, replay_ids):
    """Decode at most 200 current rounds, only after their patch trips the alarm."""
    rows = (_current_shares(db)
            .join(ReplayRound, and_(ReplayRound.replay_id == ReplayRoundWallShare.replay_id,
                                   ReplayRound.round_number == ReplayRoundWallShare.round_number))
            .with_entities(ReplayRoundControl.data, ReplayRound.data)
            .filter(ReplayRoundWallShare.replay_id.in_(replay_ids))
            .order_by(ReplayRoundWallShare.replay_id, ReplayRoundWallShare.round_number)
            .limit(200).all())
    counts = Counter()
    for control_data, data in rows:
        if data is None:
            continue
        try:
            for u, v, inside in _samples(control_data, fmt.decode_blob(data)):
                if inside:
                    area = areas.area_at(asset_key, u, v)
                    counts[area["name"] if area is not None else "unnamed"] += 1
        except Exception:  # noqa: BLE001 - an unreadable round cannot hide a measured rise
            log.warning("wall callouts for %s unreadable", asset_key, exc_info=True)
    return [{"name": name, "count": count}
            for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3]]


def evaluate(db) -> None:
    """Raise later-patch rises over each layout's pooled baseline; resolve every other open key."""
    if settings.demo_mode:
        return
    rows = (_current_shares(db).join(Replay, Replay.id == ReplayRoundWallShare.replay_id)
            .with_entities(Replay.id, Replay.map_name, Replay.game_branch,
                           func.sum(ReplayRoundWallShare.inside), func.sum(ReplayRoundWallShare.total))
            .group_by(Replay.id, Replay.map_name, Replay.game_branch))
    groups = defaultdict(lambda: defaultdict(_Pool))
    for replay in rows:
        patch = layouts.parse_patch(replay.game_branch)
        if patch is None:
            continue
        selection = replay_control.layout_of(replay)
        if selection.state != "ok" or selection.layout is None or selection.layout.drawn_from is None:
            continue
        layout = selection.layout
        pool = groups[(replay.map_name, layout.asset_key, layout.drawn_from)][patch]
        pool.inside += replay[3]
        pool.total += replay[4]
        pool.replays.add(replay.id)
    raised = set()
    for (map_name, asset_key, drawn_from), patches in sorted(groups.items()):
        baseline_inside = sum(pool.inside for patch, pool in patches.items() if patch <= drawn_from)
        baseline_total = sum(pool.total for patch, pool in patches.items() if patch <= drawn_from)
        if not baseline_total:
            continue
        for patch, pool in sorted(patches.items()):
            if patch <= drawn_from or len(pool.replays) < MIN_REPLAYS or not pool.total:
                continue
            # Integer cross-products preserve the strict boundary before division into percentage points.
            rise = 100 * (pool.inside * baseline_total - baseline_inside * pool.total) / (pool.total * baseline_total)
            if rise <= THRESHOLD_POINTS:
                continue
            patch_name = f"{patch[0]}.{patch[1]:02d}"
            key = f"{map_name}:{patch_name}"
            context = {"map": map_name, "patch": patch_name, "layout": asset_key, "replays": len(pool.replays),
                       "share": round(100 * pool.inside / pool.total, 3),
                       "baseline": round(100 * baseline_inside / baseline_total, 3),
                       "callouts": _callouts(db, asset_key, pool.replays)}
            site_health.record("map_layout_walls", key,
                               f"{map_name} patch {patch_name}: positions inside walls rose {rise:.3f} points "
                               f"over {asset_key}'s baseline across {len(pool.replays)} replays", context)
            raised.add(key)
    for (key,) in db.query(SiteEvent.key).filter(SiteEvent.kind == "map_layout_walls"):
        if key not in raised:
            site_health.resolve("map_layout_walls", key)


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
