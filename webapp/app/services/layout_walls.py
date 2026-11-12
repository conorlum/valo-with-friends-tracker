"""Count alive replay positions on non-walk cells, in bounded idle dispatcher passes."""

import hashlib
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
from app.services import control_heights, replay_control, replay_reparse_auto, site_health

log = logging.getLogger(__name__)

STEP_ROWS = 20  # ~0.55 s a round over the external DB: ~11 s a pass (W14)
MIN_REPLAYS = 4  # D12
MIN_BASELINE_REPLAYS = MIN_REPLAYS  # F8: a baseline from one replay judges nothing
THRESHOLD_POINTS = 0.5  # D12
EVALUATE_EVERY_S = 600  # evaluate on a quiet site too: a deleted replay can drop a pool below its alarm (S10)


def _cell(u, v) -> int:
    """Inverse of the control grid's cell-centre UV coordinates (column u, row v)."""
    return (min(cf.GRID - 1, int(v / fmt.UV_SCALE * cf.GRID)) * cf.GRID
            + min(cf.GRID - 1, int(u / fmt.UV_SCALE * cf.GRID)))


def on_quarter(t: float, hz: int) -> bool:
    """Whether the sample at round time `t` of a `hz` track is the first one at or after a quarter second. Read
    from the sample's grid index, since a segment's `t0` is stored rounded to 3 decimals (1/16 s is 0.062): a
    test on `t` itself dropped every segment that starts on an odd 16 Hz index (F3, 2026-11-12)."""
    k = round(t * hz)
    return (4 * k) // hz != (4 * (k - 1)) // hz


def _samples_with(header: dict, round_blob: dict):
    walk = set(cf.walk_bitmap(header))
    hz = round_blob["hz"]
    for slot, segments in round_blob.get("tracks", {}).items():
        lives = [(t0, inf if t1 is None else t1)
                 for t0, t1, _ in round_blob.get("alive", {}).get(slot, [])]
        for segment in segments:
            for t, u, v, _ in fmt.decode_segment(segment, hz):
                if not on_quarter(t, hz):
                    continue
                if any(t0 <= t < t1 for t0, t1 in lives):
                    yield u, v, _cell(u, v) not in walk


def _samples(control_data: bytes, round_blob: dict):
    """Yield (u, v, inside) for counted 4 Hz samples alive on the replay's own clock."""
    header, _ = cf.unpack_data(control_data)
    yield from _samples_with(header, round_blob)


def _count(samples) -> tuple[int, int]:
    inside = total = 0
    for _, _, in_wall in samples:
        total += 1
        inside += in_wall
    return inside, total


def round_share(control_data: bytes, round_blob: dict) -> tuple[int, int]:
    """(Inside walls, total) for 4 Hz samples alive on the replay's own clock."""
    return _count(_samples(control_data, round_blob))


NOT_CURRENT = ""   # a share measured on control that isn't current for the replay's layout: never counted


class _Keys:
    """Per step: the asset key a control row's share is filed under. The replay's layout key when the row's
    fingerprint is that layout's current one, else NOT_CURRENT (S9, 2026-11-12): a row computed before a redraw
    measured the old walk mask, so it is never pooled under the new layout. Recomputed control gets a new
    fingerprint, and its share is then measured again."""

    def __init__(self, session):
        self.session = session
        self.heights = None
        self.groups, self.contexts = {}, {}

    def __call__(self, replay, control) -> str:
        selection = replay_control.layout_of(replay)
        if selection.state != "ok" or selection.layout is None:
            return NOT_CURRENT
        key = selection.layout.asset_key
        if self.heights is None:
            self.heights = control_heights.active_digests(self.session)
        if replay.id not in self.groups:
            self.groups[replay.id] = replay_control.side_groups(self.session, replay)
        if key not in self.contexts:
            self.contexts[key] = replay_control.resolve_current_geometry(self.session, key)
        current = replay_control.round_fingerprint(replay, self.groups[replay.id], control.round_number,
                                                   self.heights, context=self.contexts[key])
        return key if current is not None and current == control.fingerprint else NOT_CURRENT


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


def _seen_evidence(seen, context):
    """Site health's dedupe: None when this measurement (its replays and counts) is the one already recorded,
    so a standing alarm isn't re-recorded every pass, and one a PR cleared as false stays cleared until the
    evidence changes (F4, 2026-11-12); else the new evidence to keep."""
    evidence = (context or {}).get("evidence")
    if isinstance(seen, dict) and seen.get("evidence") == evidence:
        return None
    return {"evidence": evidence}


_last_raised: set = set()      # the keys the previous pass raised: their records may still be queued (F11)
_callout_cache: dict = {}      # (asset key, evidence) -> top callouts, so a standing alarm decodes once (F9)


def _callouts_for(db, asset_key, replay_ids, evidence):
    key = (asset_key, evidence)
    if key not in _callout_cache:
        if len(_callout_cache) > 64:
            _callout_cache.clear()
        _callout_cache[key] = _callouts(db, asset_key, replay_ids)
    return _callout_cache[key]


def evaluate(db) -> dict:
    """Raise later-patch rises over each layout's pooled baseline; resolve every other open key. Returns the
    layouts that couldn't be judged for want of a baseline (F8), for the dispatcher's counts."""
    global _last_raised
    if settings.demo_mode:
        return {}
    rows = (_current_shares(db).join(Replay, Replay.id == ReplayRoundWallShare.replay_id)
            .with_entities(Replay.id, Replay.map_name, Replay.game_branch, ReplayRoundWallShare.asset_key,
                           func.sum(ReplayRoundWallShare.inside), func.sum(ReplayRoundWallShare.total))
            .group_by(Replay.id, Replay.map_name, Replay.game_branch, ReplayRoundWallShare.asset_key))
    groups = defaultdict(lambda: defaultdict(_Pool))
    for replay in rows:
        patch = layouts.parse_patch(replay.game_branch)
        if patch is None:
            continue
        selection = replay_control.layout_of(replay)
        if selection.state != "ok" or selection.layout is None or selection.layout.drawn_from is None:
            continue
        layout = selection.layout
        if replay[3] != layout.asset_key or not replay[5]:
            continue        # S9: measured on another layout's mask; S13: no position measured at all
        pool = groups[(replay.map_name, layout.asset_key, layout.drawn_from)][patch]
        pool.inside += replay[4]
        pool.total += replay[5]
        pool.replays.add(replay.id)
    raised, unjudged = set(), {}
    for (map_name, asset_key, drawn_from), patches in sorted(groups.items()):
        baseline = [pool for patch, pool in patches.items() if patch <= drawn_from]
        baseline_inside = sum(pool.inside for pool in baseline)
        baseline_total = sum(pool.total for pool in baseline)
        baseline_replays = len(set().union(*(pool.replays for pool in baseline))) if baseline else 0
        if baseline_replays < MIN_BASELINE_REPLAYS:
            unjudged[asset_key] = baseline_replays        # F8: too few to judge a later patch against
            continue
        for patch, pool in sorted(patches.items()):
            if patch <= drawn_from or len(pool.replays) < MIN_REPLAYS or not pool.total:
                continue
            # Integer cross-products preserve the strict boundary before division into percentage points.
            rise = 100 * (pool.inside * baseline_total - baseline_inside * pool.total) / (pool.total * baseline_total)
            if rise <= THRESHOLD_POINTS:
                continue
            patch_name = layouts.patch_text(patch)
            key = f"{map_name}:{patch_name}"
            evidence = hashlib.sha256(f"{sorted(pool.replays)}:{pool.inside}/{pool.total}:"
                                      f"{baseline_inside}/{baseline_total}".encode()).hexdigest()[:16]
            context = {"map": map_name, "patch": patch_name, "layout": asset_key, "replays": len(pool.replays),
                       "share": round(100 * pool.inside / pool.total, 3),
                       "baseline": round(100 * baseline_inside / baseline_total, 3),
                       "callouts": _callouts_for(db, asset_key, pool.replays, evidence), "evidence": evidence}
            site_health.record("map_layout_walls", key,
                               f"{map_name} patch {patch_name}: positions inside walls rose {rise:.3f} points "
                               f"over {asset_key}'s baseline across {len(pool.replays)} replays", context,
                               dedupe=_seen_evidence)
            raised.add(key)
    stored = {key for (key,) in db.query(SiteEvent.key).filter(SiteEvent.kind == "map_layout_walls")}
    for key in sorted((stored | _last_raised) - raised):
        site_health.resolve("map_layout_walls", key)
    _last_raised = raised
    if unjudged:
        log.info("map_layout_walls: no baseline to judge against for %s", unjudged)
    return unjudged


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
        key_of = _Keys(session)
        for control, replay, data in rows:
            try:
                inside, total = round_share(control.data, fmt.decode_blob(data)) if data is not None else (0, 0)
            except Exception:  # noqa: BLE001 - one unreadable round must not hold back every row after it
                log.warning("wall share of replay %s round %s unreadable; stored as 0/0",
                            control.replay_id, control.round_number, exc_info=True)
                inside, total = 0, 0
            session.merge(ReplayRoundWallShare(
                replay_id=control.replay_id, round_number=control.round_number,
                asset_key=key_of(replay, control),
                control_fingerprint=control.fingerprint, inside=inside, total=total, computed_at=computed_at))
        session.commit()
        return {"computed": len(rows)}
    finally:
        session.close()
