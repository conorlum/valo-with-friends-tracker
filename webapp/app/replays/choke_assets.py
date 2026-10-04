"""Chokes as a per-map asset (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 2):
`<Map>.chokes.json` beside the control masks. Standard library only, so the web app can read chokes and
hash them without the engine. Detection is app/control/chokes.py.

File shape: {"version": 1, "map", "next_id", "chokes"}. `next_id` is an asset-level high-water mark: ids are
never reused, even after every choke was dropped.

merge: a hand choke (`source: "hand"`) and a deleted one (a tombstone) survive re-detection; a detected choke
that overlaps either is dropped; an auto choke whose cells detection finds again keeps its id and name; an
auto choke detection no longer finds is dropped; new ids come from the high-water mark."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app.replays import format as fmt

ASSET_DIR = fmt.STATIC_DIR / "data" / "control"


@dataclass
class Choke:
    id: int
    name: str
    cells: list[int]
    source: str = "auto"     # "auto" | "hand"
    deleted: bool = False


def merge(existing: list[Choke], detected: list[list[int]], next_id: int) -> tuple[list[Choke], int]:
    next_id = max(next_id, max([c.id for c in existing] + [0]) + 1)
    kept = [c for c in existing if c.source == "hand" or c.deleted]
    covered = set().union(*(set(c.cells) for c in kept)) if kept else set()
    auto_by_cells = {tuple(c.cells): c for c in existing if c.source != "hand" and not c.deleted}
    out = list(kept)
    for cells in detected:
        cells = list(cells)
        if covered & set(cells):
            continue
        old = auto_by_cells.get(tuple(cells))
        if old is not None:
            out.append(old)
            continue
        out.append(Choke(next_id, str(next_id), cells))
        next_id += 1
    return sorted(out, key=lambda c: c.id), next_id


def _path(name: str, asset_dir: Path) -> Path:
    return Path(asset_dir) / f"{name}.chokes.json"


def save(name: str, chokes: list[Choke], next_id: int, asset_dir: Path = ASSET_DIR) -> Path:
    path = _path(name, asset_dir)
    next_id = max(next_id, max([c.id for c in chokes] + [0]) + 1)
    body = {"version": 1, "map": name, "next_id": next_id,
            "chokes": [asdict(c) for c in sorted(chokes, key=lambda c: c.id)]}
    path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    return path


def _read(name: str, asset_dir: Path) -> dict | None:
    path = _path(name, asset_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load(name: str, asset_dir: Path = ASSET_DIR) -> list[Choke] | None:
    body = _read(name, asset_dir)
    if body is None:
        return None
    return [Choke(**c) for c in body["chokes"]]


def load_next_id(name: str, asset_dir: Path = ASSET_DIR) -> int:
    """The id high-water mark (1 when there is no asset)."""
    body = _read(name, asset_dir)
    if body is None:
        return 1
    ids = [c["id"] for c in body["chokes"]]
    return max(int(body.get("next_id", 1)), max(ids + [0]) + 1)


# The hash ignores names, so renaming a choke keeps every gap row fresh.
def asset_hash(name: str, asset_dir: Path = ASSET_DIR) -> str | None:
    """16 hex of what detection uses: each choke's id, cells and tombstone, sorted by id, as sorted JSON. A
    rename, a `source` change or the file's layout changes no fingerprint. None when there is no asset."""
    body = _read(name, asset_dir)
    if body is None:
        return None
    view = sorted(({"id": int(c["id"]), "cells": sorted(int(x) for x in c["cells"]), "deleted": bool(c.get("deleted", False))}
                   for c in body["chokes"]), key=lambda c: c["id"])
    return hashlib.sha256(json.dumps(view, sort_keys=True).encode("utf-8")).hexdigest()[:16]
