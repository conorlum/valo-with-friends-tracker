"""Which rounds need timing gaps, and their fingerprints (docs/superpowers/specs/2026-10-02-timing-gaps-design.md,
section 7, "Freshness" and "Writer"). A round's gaps are computed with its control; a round whose control is
fresh and ok but whose gap run is missing or stale (new gap rules, an edited choke asset or hearing table) is
computed on its own, from the tick cache when it is there, else through the engine without rewriting control.
`round_gaps` is the gaps.json endpoint's read of one round (section 8).

Standard library and the DB only: no `app.control` import (tests/replays/test_control_isolation.py), so the web
app's pattern page can use it. The hearing table is read as a file, never imported."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from sqlalchemy.orm import load_only

from app.models.replay import ReplayGap, ReplayRoundControl, ReplayRoundGapRun
from app.replays import choke_assets
from app.services import replay_control

GAPS_REVISION = 2     # keep equal to app.gaps.detect.GAPS_REVISION (tests/replays/test_gaps_task.py pins it)
HEARING_FILE = Path(__file__).resolve().parents[1] / "control" / "hearing.json"


def _hex16(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def hearing_hash(path: Path | None = None) -> str:
    """16 hex of the hearing table's numeric view (R6, narrowed by the final review's I2): the same fields, as
    floats, that app/control/engine.py `HEARING` reads and pins (`footstep_range_m`, `default_gun_m`, `guns`),
    as sorted JSON. Editing `sources`, the `PROVISIONAL` marker or the file's layout changes nothing the engine
    uses, so it changes no fingerprint. "none" when there is no table."""
    try:
        body = json.loads(Path(path or HEARING_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "none"
    view = {"footstep_range_m": float(body["footstep_range_m"]), "default_gun_m": float(body["default_gun_m"]),
            "guns": {str(k): float(v) for k, v in body["guns"].items()}}
    return _hex16(json.dumps(view, sort_keys=True))


def engine_key(control_fingerprint: str, map_name: str) -> str:
    """The tick cache's key (R5): everything the engine's unknown depends on, so an edit to any of them misses
    the cache. GAPS_REVISION is deliberately left out: re-running the detector alone is the cache's point."""
    return _hex16(f"{control_fingerprint}|{choke_assets.asset_hash(map_name)}|{hearing_hash()}")


def gap_fingerprint(control_fingerprint: str, map_name: str) -> str:
    """A round's gap run is current while this matches: the control fingerprint, GAPS_REVISION, the map's choke
    asset hash and the hearing table's hash (section 7, "Freshness")."""
    return _hex16(f"{control_fingerprint}|{GAPS_REVISION}|{choke_assets.asset_hash(map_name)}|{hearing_hash()}")


def round_gaps(db, replay, n: int) -> tuple[str, list]:
    """The gaps.json endpoint's answer for round n: (status, rows). `not_computed` when the round has no gap run,
    `failed` when its run failed (no rows either way); else its `ReplayGap` rows in seq order, `stale` when the
    run's fingerprint is not the one the current control fingerprint gives (round_control's freshness test,
    review amendment 6), and `ok` otherwise. The caller has already checked the map and the blob."""
    run = (db.query(ReplayRoundGapRun)
           .options(load_only(ReplayRoundGapRun.status, ReplayRoundGapRun.fingerprint))
           .filter(ReplayRoundGapRun.replay_id == replay.id, ReplayRoundGapRun.round_number == n)
           .one_or_none())
    if run is None:
        return "not_computed", []
    if run.status != "ok":
        return "failed", []
    rows = (db.query(ReplayGap).filter(ReplayGap.replay_id == replay.id, ReplayGap.round_number == n)
            .order_by(ReplayGap.seq).all())
    control = replay_control.round_fingerprint(replay, replay_control.side_groups(db, replay), n)
    current = None if control is None else gap_fingerprint(control, replay.map_name)
    return ("ok" if run.fingerprint == current else "stale"), rows


def plan_gaps(db, planned_control: list, every: list, retry_failed: bool = False) -> list:
    """Of `every` (replay_control.plan(force=True) over the same scope: each round with its current control
    fingerprint and link), the rounds that are not in `planned_control`, whose control row is ok with the current
    fingerprint (R17), and whose gap run is missing or stale; reason `gaps`. A gap run that failed with the
    current fingerprint is skipped (it would fail again) unless `retry_failed`."""
    busy = {(p.replay_id, p.round_number) for p in planned_control}
    control = {(r.replay_id, r.round_number): r for r in db.query(ReplayRoundControl).options(load_only(
        ReplayRoundControl.replay_id, ReplayRoundControl.round_number, ReplayRoundControl.status,
        ReplayRoundControl.fingerprint))}
    runs = {(r.replay_id, r.round_number): r for r in db.query(ReplayRoundGapRun).options(load_only(
        ReplayRoundGapRun.replay_id, ReplayRoundGapRun.round_number, ReplayRoundGapRun.status,
        ReplayRoundGapRun.fingerprint))}
    wanted: dict[tuple, str] = {}
    out = []
    for p in every:
        key = (p.replay_id, p.round_number)
        if not p.computable or key in busy:
            continue
        row = control.get(key)
        if row is None or row.status != "ok" or row.fingerprint != p.fingerprint:
            continue
        if (p.fingerprint, p.map_name) not in wanted:
            wanted[p.fingerprint, p.map_name] = gap_fingerprint(p.fingerprint, p.map_name)
        run = runs.get(key)
        if run is not None and run.fingerprint == wanted[p.fingerprint, p.map_name]:
            if run.status == "ok" or not retry_failed:
                continue
        out.append(replace(p, reason="gaps"))
    return out
