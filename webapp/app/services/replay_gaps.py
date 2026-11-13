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
from app.replays import choke_assets, layouts
from app.replays import control_format as cf
from app.services import control_heights, replay_control

GAPS_REVISION = 5     # keep equal to app.gaps.detect.GAPS_REVISION (tests/replays/test_gaps_task.py pins it); 5: Critical Util rides the gap run; 4: critical moments ride the gap run
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
    if path is None:
        # 2026-10-05 (W22): the ability figures the engine now consumes too (app/control/utility.json), through
        # the same numeric view control's fingerprint uses, so one changed range makes control and gaps stale.
        view["figures"] = cf.figures_hash()
    return _hex16(json.dumps(view, sort_keys=True))


def engine_key(control_fingerprint: str, asset_key: str) -> str:
    """The tick cache's key (R5): everything the engine's unknown depends on, so an edit to any of them misses
    the cache. GAPS_REVISION is deliberately left out: re-running the detector alone is the cache's point."""
    return _hex16(f"{control_fingerprint}|{choke_assets.asset_hash(asset_key)}|{hearing_hash()}")


_UNSET = object()


def gap_fingerprint(control_fingerprint: str, asset_key: str, *, chokes=_UNSET, hearing=_UNSET) -> str:
    """A round's gap run is current while this matches: the control fingerprint, GAPS_REVISION, the layout's choke
    asset hash and the hearing table's hash (section 7, "Freshness"). A caller fingerprinting many rounds passes
    `chokes` (asset_hash(asset_key), which may be None) and `hearing` (hearing_hash()) once (the pattern page)."""
    if chokes is _UNSET:
        chokes = choke_assets.asset_hash(asset_key)
    if hearing is _UNSET:
        hearing = hearing_hash()
    text = f"{control_fingerprint}|{GAPS_REVISION}|{chokes}|{hearing}"
    if layouts.map_of(asset_key) != asset_key:
        # R2-3 (2026-11-12): a later layout's gap rows are stored under its own key, so its run must differ from
        # an earlier layout's even when the control inputs and the chokes are the same (a bullet-only redraw).
        # Base keys add nothing: their fingerprints stay byte-identical.
        text += f"|{asset_key}"
    return _hex16(text)


class Freshness:
    """Which stored util rounds are current, by Critical Moments' rule (replay_moments.load_moments_payload): the
    gap run's revision, and its fingerprint against the round's now (the link and its deaths, so a relink or a
    clock correction; the geometry and heights; the chokes; the hearing table). Built once per request: each map's
    geometry and each replay's side groups are read once."""

    def __init__(self, db):
        from app.services import replay_gaps

        self.db, self.hearing = db, replay_gaps.hearing_hash()
        self.maps: dict[str, tuple] = {}
        self.groups: dict[int, dict] = {}

    def preload(self, replay_ids) -> None:
        """Every one of these replays' side groups in one query (replay_control.side_groups, one replay each)."""
        from app.models.replay import ReplayPlayer

        wanted = [rid for rid in set(replay_ids) if rid not in self.groups]
        if not wanted:
            return
        for rid in wanted:
            self.groups[rid] = {}
        for rid, slot, group in self.db.query(ReplayPlayer.replay_id, ReplayPlayer.slot, ReplayPlayer.side_group
                                              ).filter(ReplayPlayer.replay_id.in_(wanted)):
            if group:
                self.groups[rid][slot] = group

    def expected(self, replay, n: int) -> str | None:
        from app.replays import choke_assets
        from app.services import replay_control, replay_gaps

        selection = replay_control.layout_of(replay)
        if selection.state != "ok":
            return None
        key = selection.layout.asset_key
        if key not in self.maps:
            self.maps[key] = (replay_control.resolve_current_geometry(self.db, key),
                              choke_assets.asset_hash(key))
        context, chokes = self.maps[key]
        if replay.id not in self.groups:
            self.groups[replay.id] = replay_control.side_groups(self.db, replay)
        control = replay_control.round_fingerprint(replay, self.groups[replay.id], n, context=context)
        if control is None:
            return None
        return replay_gaps.gap_fingerprint(control, key, chokes=chokes, hearing=self.hearing)

    def current(self, replay, n: int, revision: int, fingerprint: str) -> bool:
        from app.services import replay_gaps

        return revision == replay_gaps.GAPS_REVISION and fingerprint == self.expected(replay, n)


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
    current = Freshness(db).expected(replay, n)
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
        asset_key = p.asset_key or p.map_name
        if (p.fingerprint, asset_key) not in wanted:
            wanted[p.fingerprint, asset_key] = gap_fingerprint(p.fingerprint, asset_key)
        run = runs.get(key)
        if run is not None and run.fingerprint == wanted[p.fingerprint, asset_key]:
            if run.status == "ok" or not retry_failed:
                continue
        out.append(replace(p, reason="gaps"))
    return out
