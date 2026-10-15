"""Map control's caches on the worker's disk, for Workflow runs (spec 2026-10-14, D6). Standard library only.

A control run starts on a blank machine. It gets the map's newest visibility caches (`<Map>.<16 hex>.npz`,
app/control/geometry.py) and feature-row caches (`feature-rows/<Map>.<64 hex>.npz`,
app/control/feature_visibility.py), plus the round's height asset and feature artifact when the worker has them,
under the same relative names, and posts back any cache it created or changed. KEPT newest per map and folder
stay; the archive keeps RESERVE free for them, and a file that would take the folder past it is refused. Cache
files are named by their content's identity, so a late one is still correct (spec D6)."""

from __future__ import annotations

import os
import re
import shutil
import threading
from pathlib import Path

KEPT = 3
RESERVE = 2 * 1024 ** 3
_NAME = re.compile(r"^(?P<folder>feature-rows/)?(?P<map>[A-Za-z]+)\.(?P<key>[0-9a-f]{16}|[0-9a-f]{64})\.npz$")
_LOCK = threading.Lock()          # every install and prune; reads (inputs) need none: files are replaced atomically


def _tree_bytes(cache_dir: Path) -> int:
    total = 0
    for path in Path(cache_dir).rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass                  # pruned or replaced meanwhile
    return total


def _newest(folder: Path, map_name: str) -> list[Path]:
    found = []
    for path in folder.glob(f"{map_name}.*.npz"):
        if path.name.endswith(".tmp.npz") or not _NAME.fullmatch(path.name):
            continue
        try:
            found.append((path.stat().st_mtime, path))
        except OSError:
            pass                      # pruned meanwhile
    return [path for _, path in sorted(found, key=lambda item: item[0], reverse=True)]


def inputs(cache_dir: Path, task: dict) -> dict[str, Path]:
    cache_dir = Path(cache_dir)
    map_name = str(task["map"])
    out: dict[str, Path] = {}
    for prefix, folder in (("", cache_dir), ("feature-rows/", cache_dir / "feature-rows")):
        for path in _newest(folder, map_name)[:KEPT]:
            out[prefix + path.name] = path
    if task.get("height"):
        path = cache_dir / "heights" / f"{map_name}.{task['height']}.height.npz"
        if path.is_file():
            out[f"heights/{path.name}"] = path
    if task.get("features"):
        path = cache_dir / "features" / f"{task['features']}.json"
        if path.is_file():
            out[f"features/{path.name}"] = path
    return out


def valid(name: str, map_name: str) -> bool:
    match = _NAME.fullmatch(name)
    return bool(match) and match["map"] == map_name


def store(cache_dir: Path, name: str, src: Path, reserve: int = RESERVE) -> bool:
    """Moves a posted cache file into place (a rename: `src` is in `<cache>/.incoming`) and prunes its map's folder
    to KEPT. False, with `src` deleted, when the cache folder would grow past `reserve`."""
    target = Path(cache_dir) / name
    map_name = _NAME.fullmatch(name)["map"]
    try:
        with _LOCK:
            replaced = target.stat().st_size if target.is_file() else 0
            # `src` already sits under cache_dir/.incoming, so the tree's total counts it once.
            if _tree_bytes(cache_dir) - replaced > reserve:
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, target)
            for old in _newest(target.parent, map_name)[KEPT:]:
                old.unlink(missing_ok=True)
            return True
    finally:
        Path(src).unlink(missing_ok=True)


def visibility_name(name: str) -> bool:
    """A root-folder visibility cache name, not a feature-row one."""
    match = _NAME.fullmatch(name)
    return bool(match) and not match["folder"]


def has_visibility(cache_dir: Path, map_name: str) -> bool:
    """The map's visibility cache is here: a round of it won't have to build one (what "warm" means, D6)."""
    return bool(_newest(Path(cache_dir), map_name))


def clear_incoming(cache_dir: Path) -> None:
    """Part files a restart left behind."""
    shutil.rmtree(Path(cache_dir) / ".incoming", ignore_errors=True)
    (Path(cache_dir) / ".incoming").mkdir(parents=True, exist_ok=True)
