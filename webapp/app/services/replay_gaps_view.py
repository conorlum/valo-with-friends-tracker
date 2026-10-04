"""Gap rows as the viewer draws them (docs/superpowers/plans/2026-10-04-timing-gaps-viewer.md, section 2).

Standard library plus app.replays.choke_assets only: the web app and the standalone page use this, so it
imports neither the engine (app.control) nor the detector (app.gaps), nor numpy
(tests/replays/test_control_isolation.py).

`view_rows(rows, map_name)` takes stored gap rows (app/gaps/rows.py `to_rows`'s shape, which is also the
`ReplayGap` columns) and returns `{"chokes": {...}, "rows": [...]}`: each row copied, plus pixel positions for
the spot, the victim and a released cell, the deepest level the gap was used to, and how many gaps were folded
into it. Pixels are on the 1024-px map, as the engine's cell centres and the stored route's points are.

Choke ids are JSON object keys, so they are strings ("7"), here and in the served body alike."""

from __future__ import annotations

import copy
from pathlib import Path

from app.replays import choke_assets

# Equal to app.control.geometry's GRID and PX // GRID (asserted in tests/replays/test_gaps_view.py); not
# imported, to keep the engine out of the web app.
GRID = 128
CELL_PX = 8

# Every stored gap column except the round's key (replay_id, round_number): to_rows's keys.
COLUMNS = (
    "seq", "kind", "map", "victim_slot", "victim_side", "t_open", "t_last_exposed", "t_close",
    "spot_cell", "victim_cell", "distance_m", "angle_deg", "qualified_s", "flicker", "cause", "cause_detail",
    "choke_seq", "route", "candidate_slots", "candidate_distances", "checked_at", "stood_at", "stood_by",
    "shot_at", "shot_by", "killed_at", "killed_by", "victim_won_at", "context", "linked_seq",
)


def cell_xy(cell: int) -> tuple[int, int]:
    """A flat cell on the GRID x GRID grid -> its pixel centre on the 1024-px map."""
    c = int(cell)
    return (c % GRID) * CELL_PX + CELL_PX // 2, (c // GRID) * CELL_PX + CELL_PX // 2


def choke_points(map_name: str, asset_dir: Path = choke_assets.ASSET_DIR) -> dict[str, dict]:
    """{str(id): {"name", "x", "y"}} for each choke that is not deleted: its name and the mean of its cells'
    centres, rounded to 0.1 px. {} when the map has no asset."""
    chokes = choke_assets.load(map_name, asset_dir)
    out: dict[str, dict] = {}
    for c in chokes or []:
        if c.deleted or not c.cells:
            continue
        pts = [cell_xy(cell) for cell in c.cells]
        out[str(c.id)] = {"name": c.name,
                          "x": round(sum(p[0] for p in pts) / len(pts), 1),
                          "y": round(sum(p[1] for p in pts) / len(pts), 1)}
    return out


def _used(row: dict) -> str | None:
    if row.get("killed_at") is not None:
        return "killed"
    if row.get("shot_at") is not None:
        return "shot"
    if row.get("stood_at") is not None:
        return "stood"
    return None


def _released_xy(row: dict) -> list[int] | None:
    detail = row.get("cause_detail") or {}
    if row.get("cause") != "route_released" or detail.get("cell") is None:
        return None
    return list(cell_xy(detail["cell"]))


def view_row(row: dict) -> dict:
    out = copy.deepcopy(dict(row))
    out["spot_xy"] = list(cell_xy(row["spot_cell"]))
    out["victim_xy"] = list(cell_xy(row["victim_cell"]))
    out["released_xy"] = _released_xy(row)
    out["used"] = _used(row)
    out["merged"] = len((row.get("context") or {}).get("merged") or [])
    return out


def view_rows(rows, map_name: str, asset_dir: Path = choke_assets.ASSET_DIR) -> dict:
    """{"chokes": choke_points(map_name), "rows": each row as view_row, sorted by (t_open, seq)}."""
    ordered = sorted(rows, key=lambda r: (r["t_open"], r["seq"]))
    return {"chokes": choke_points(map_name, asset_dir), "rows": [view_row(r) for r in ordered]}


def row_from_model(gap) -> dict:
    """A `ReplayGap` as the stored-row dict view_rows takes (COLUMNS; not the round's key)."""
    return {name: getattr(gap, name) for name in COLUMNS}
