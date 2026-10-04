"""The tick cache (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 3): one round's tick
records, kept locally so the gap detector can be re-run without the engine. Unknown entry arrays are stored
as changes from the previous tick; the route logs once, at the end. Local only: never in the database or
the repo. Pickle is safe here because only this module writes these files.

A file is a gzip stream of two pickles: a small header (`format`, the compute-time `missing` input counts,
the tick count), read alone by `read_missing`, then the body (ticks and route logs), read by `replay`.

The file name carries an opaque engine key (R5: Task 10 hashes the control fingerprint, the map's choke
asset and the hearing table into it), so an edit to any of them misses the cache."""

from __future__ import annotations

import gzip
import pickle
from pathlib import Path

import numpy as np

from app.control.observe import PlayerView, TickRecord

FORMAT = 1


def cache_dir() -> Path:
    from app.control import geometry

    return Path(geometry.cache_dir()) / "gaps"


def cache_path(replay_id: int, round_number: int, key: str, directory: Path | None = None) -> Path:
    """`key` is the engine key (R5): opaque here."""
    return Path(directory or cache_dir()) / f"{replay_id}-r{round_number}-{key}.ticks.pkl.gz"


def _nodes(mask) -> np.ndarray | None:
    return None if mask is None else np.flatnonzero(mask).astype(np.int32)


def _mask(idx, n: int) -> np.ndarray | None:
    if idx is None:
        return None
    out = np.zeros(n, bool)
    out[idx] = True
    return out


class Writer:
    """An observer for compute_round that keeps every tick, then writes the file on `close`."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.ticks: list = []
        self.prev: dict = {}
        self.logs = None

    def __call__(self, rec: TickRecord, unknown) -> None:
        diff = {}
        for side, enemies in rec.unknown.items():
            for e, arr in enemies.items():
                old = self.prev.get((side, e))
                if old is None or len(old) != len(arr):
                    idx = np.flatnonzero(arr >= 0)
                    diff[(side, e)] = ("full", idx.astype(np.int32), arr[idx].astype(np.int64))
                else:
                    idx = np.flatnonzero(arr != old)
                    if len(idx):
                        diff[(side, e)] = ("delta", idx.astype(np.int32), arr[idx].astype(np.int64))
                self.prev[(side, e)] = arr.copy()
        gone = [k for k in self.prev if k[1] not in rec.unknown.get(k[0], {})]
        for k in gone:
            del self.prev[k]
        players = {s: (p.team, p.node, p.x, p.y, p.yaw, _nodes(p.live), _nodes(p.utility), _nodes(p.view), p.eye_z)
                   for s, p in rec.players.items()}
        events = {side: list(ev) for side, ev in rec.events.items()}
        self.ticks.append((rec.t, players, diff, gone, events, list(rec.smokes), unknown.geo.n))
        self.logs = unknown.log          # route logs only grow: the last tick's holds every entry any tick used

    def close(self, missing: dict | None = None) -> None:
        """Writes the file (atomically). `missing` is the round's compute-time missing-input counts (R20)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with gzip.open(tmp, "wb") as f:
                pickle.dump({"format": FORMAT, "missing": dict(missing or {}), "ticks": len(self.ticks)}, f,
                            protocol=5)
                pickle.dump({"ticks": self.ticks, "logs": self.logs}, f, protocol=5)
            tmp.replace(self.path)
        finally:
            if tmp.exists():
                tmp.unlink()


def _header(f, path) -> dict:
    head = pickle.load(f)
    if not isinstance(head, dict) or head.get("format") != FORMAT:
        got = head.get("format") if isinstance(head, dict) else None
        raise ValueError(f"tick cache {path}: format {got}, expected {FORMAT}")
    return head


def read_missing(path: Path) -> dict:
    """The compute-time missing-input counts stored with the file (R20), without reading the ticks."""
    with gzip.open(path, "rb") as f:
        return dict(_header(f, path)["missing"])


def replay(path: Path):
    """Yields (TickRecord, logs) in tick order, rebuilding each tick's full entry arrays one at a time.
    An entry array that did not change is the same object in the next record: read, never modify."""
    with gzip.open(path, "rb") as f:
        _header(f, path)
        body = pickle.load(f)
    logs, state = body["logs"], {}
    for t, players, diff, gone, events, smokes, n in body["ticks"]:
        for k in gone:
            state.pop(k, None)
        for k, (how, idx, val) in diff.items():
            arr = np.full(n, -1, np.int64) if how == "full" or k not in state else state[k].copy()
            arr[idx] = val
            state[k] = arr
        unknown: dict = {"A": {}, "B": {}}
        for (side, e), arr in state.items():
            unknown.setdefault(side, {})[e] = arr
        views = {s: PlayerView(s, team, node, x, y, yaw, _mask(live, n), _mask(util, n), _mask(view, n), eye_z)
                 for s, (team, node, x, y, yaw, live, util, view, eye_z) in players.items()}
        yield TickRecord(t, views, unknown, events, smokes), logs


def read(path: Path) -> tuple[list[TickRecord], dict]:
    """Every record at once, and the route logs (small rounds and tests; prefer `replay`)."""
    records, logs = [], {}
    for rec, logs in replay(path):
        records.append(rec)
    return records, logs
