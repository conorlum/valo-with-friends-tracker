"""Resolve named map areas from reviewed paint, or from approximate callouts (app/static/data/callouts.json,
written by scripts/fetch_callouts.py) before a layout is painted. Areas are keyed by a layout's asset key;
a layout with neither has no areas, and callers get None."""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.replays.control_format import GRID
from app.replays.format import UV_SCALE

CALLOUTS_FILE = Path(__file__).resolve().parents[1] / "static" / "data" / "callouts.json"
AREAS_DIR = CALLOUTS_FILE.parent / "control"
SITES = frozenset({"A", "B", "C"})
_SUFFIX = ".areas.json"
_NAME_FIELDS = ("id", "name", "parent", "super", "super_source")
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Painted:
    """Decoded version 1 data. Cached instances and their fields are shared by readers."""

    names: dict[int, dict]
    cells: tuple[int, ...]
    cell_runs: tuple[tuple[int, int], ...]
    bands: dict[int, tuple[tuple[int, int, int], ...]]
    paths: dict[int, tuple[str, ...]]
    raw: bytes


@lru_cache(maxsize=1)
def _table() -> dict:
    try:
        return json.loads(CALLOUTS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def callouts(asset_key: str | None, table: dict | None = None) -> list[dict]:
    return (table if table is not None else _table()).get(asset_key, [])


def _nearest_label(asset_key: str | None, u: float, v: float, table: dict | None = None) -> dict | None:
    best = None
    for c in callouts(asset_key, table):
        d = (c["u"] - u) ** 2 + (c["v"] - v) ** 2
        if best is None or d < best[0]:
            best = (d, c)
    if best is None:
        return None
    name = best[1]["name"]
    return {"name": name, "super": best[1]["super"], "path": [name], "approximate": True}


def cell_at(u: float, v: float, *, grid: int = GRID) -> int:
    x = min(grid - 1, max(0, int(u / UV_SCALE * grid)))
    y = min(grid - 1, max(0, int(v / UV_SCALE * grid)))
    return y * grid + x


def _ancestors(names: dict[int, dict], name_id: int) -> list[int]:
    lineage = []
    parent = name_id
    while parent is not None:
        if parent in lineage:
            raise ValueError(f"name {name_id} has a parent cycle")
        if parent not in names:
            raise ValueError(f"name {name_id} has unknown parent id {parent}")
        lineage.append(parent)
        parent = names[parent].get("parent")
    if len(lineage) > 3:
        raise ValueError(f"name {name_id} is more than three levels deep")
    return lineage[::-1]


def _nonempty_string(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _validate_names(rows, errors: list[str]) -> dict[int, dict]:
    names = {}
    labels = set()
    if not isinstance(rows, list):
        errors.append("names must be an array")
        return names
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] < 1:
            errors.append("each name needs a positive integer id")
            continue
        name_id = row["id"]
        if name_id in names:
            errors.append(f"duplicate name id {name_id}")
            continue
        label = row.get("name")
        if not _nonempty_string(label):
            errors.append(f"name {name_id} needs a name")
        elif label in labels:
            errors.append(f"duplicate area name {label!r}")
        else:
            labels.add(label)
        if "parent" not in row or (row["parent"] is not None and type(row["parent"]) is not int):
            errors.append(f"name {name_id} needs an integer parent id or null")
            continue
        if not _nonempty_string(row.get("super")):
            errors.append(f"name {name_id} needs a super")
        if row.get("super_source") not in ("auto", "set"):
            errors.append(f"name {name_id} needs a super_source of auto or set")
        names[name_id] = row
    for name_id, row in names.items():
        try:
            root, *_ = _ancestors(names, name_id)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if root != name_id and row.get("super") != names[root].get("super"):
            errors.append(f"name {name_id} must have the same super as its root")
    return names


def _expand_cells(runs) -> tuple[int, ...]:
    return tuple(name_id for name_id, length in runs for _ in range(length))


def _validate_cells(runs, ids: set[int], errors: list[str]) -> tuple[int, ...]:
    if not isinstance(runs, list):
        errors.append("cells must be an array of runs")
        return ()
    start_errors = len(errors)
    count = 0
    for run in runs:
        if (not isinstance(run, list) or len(run) != 2 or type(run[0]) is not int
                or type(run[1]) is not int or run[1] <= 0):
            errors.append("cell runs must be [name_id, positive length]")
            continue
        if run[0] not in ids:
            errors.append(f"unknown name id {run[0]} in cells")
        count += run[1]
    if count != GRID * GRID:
        errors.append(f"cells must cover {GRID * GRID} grid cells")
    return _expand_cells(runs) if len(errors) == start_errors else ()


def _validate_bands(bands, ids: set[int], cells: tuple[int, ...], errors: list[str]) -> None:
    if not isinstance(bands, dict):
        errors.append("bands must be an object")
        return
    for cell_key, bands_at_cell in bands.items():
        if (not isinstance(cell_key, str) or not cell_key.isascii() or not cell_key.isdecimal()
                or len(cell_key) > len(str(GRID * GRID - 1))):
            errors.append(f"invalid band cell {cell_key!r}")
            continue
        cell = int(cell_key)
        if cell_key != str(cell) or cell >= GRID * GRID:
            errors.append(f"invalid band cell {cell_key!r}")
            continue
        if not isinstance(bands_at_cell, list) or not bands_at_cell:
            errors.append(f"cell {cell} needs at least one band")
            continue
        if any(not isinstance(band, list) or len(band) != 3 or any(type(n) is not int for n in band)
               or band[0] > band[1] for band in bands_at_cell):
            errors.append(f"cell {cell} bands must be [z_lo, z_hi, name_id] with ordered integer bounds")
            continue
        if any(band[2] not in ids for band in bands_at_cell):
            errors.append(f"unknown name id in bands for cell {cell}")
        if any(a[2] != b[2] and max(a[0], b[0]) <= min(a[1], b[1])
               for i, a in enumerate(bands_at_cell) for b in bands_at_cell[i + 1:]):
            errors.append(f"differently named bands overlap for cell {cell}")
        if cells and min(bands_at_cell)[2] != cells[cell]:
            errors.append(f"cell {cell} must match the lowest band's name")


def validate(obj) -> list[str]:
    if not isinstance(obj, dict):
        return ["areas file must be an object"]
    errors = []
    if type(obj.get("version")) is not int or obj["version"] != 1:
        errors.append("version must be 1")
    if not _nonempty_string(obj.get("map")):
        errors.append("map must be a name")
    if type(obj.get("reviewed")) is not bool:
        errors.append("reviewed must be a boolean")
    ids = {0, *_validate_names(obj.get("names"), errors)}
    cells = _validate_cells(obj.get("cells"), ids, errors)
    _validate_bands(obj.get("bands"), ids, cells, errors)
    return errors


def dumps(painted_dict: dict) -> str:
    """Write deterministic JSON. The tagger uses the same field order and indentation."""
    obj = {key: painted_dict[key] for key in ("version", "map", "reviewed")}
    obj["names"] = [
        {key: row[key] for key in _NAME_FIELDS}
        for row in sorted(painted_dict["names"], key=lambda row: row["id"])
    ]
    obj["cells"] = painted_dict["cells"]
    obj["bands"] = {
        cell: sorted(painted_dict["bands"][cell])
        for cell in sorted(painted_dict["bands"], key=int)
    }
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


@lru_cache(maxsize=64)
def _read_painted(path: Path, raw: bytes) -> Painted | None:
    # bytes are hashed in the cache key: even a same-size/same-mtime edit replaces the decode.
    try:
        obj = json.loads(raw.decode("utf-8"))
        if isinstance(obj, dict) and obj.get("reviewed") is False:
            return None
        errors = validate(obj)
        if errors:
            raise ValueError(errors[0])
        if obj["map"] != path.name.removesuffix(_SUFFIX):
            raise ValueError("map does not match the filename")
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError) as exc:
        _LOG.warning("Ignoring painted areas file %s: %s", path, exc)
        return None
    names = {row["id"]: row for row in sorted(obj["names"], key=lambda row: row["id"])}
    paths = {name_id: tuple(names[i]["name"] for i in _ancestors(names, name_id)) for name_id in names}
    bands = {int(cell): tuple(map(tuple, sorted(rows))) for cell, rows in obj["bands"].items()}
    return Painted(names, _expand_cells(obj["cells"]), tuple(map(tuple, obj["cells"])), bands, paths, raw)


def from_raw(map_name: str, raw: bytes) -> Painted | None:
    """Decode one painted areas file's bytes, as if read from `<map_name>.areas.json` (the build context's copy)."""
    if not map_name or any(char in map_name for char in "/\\:"):
        return None
    return _read_painted(AREAS_DIR / (map_name + _SUFFIX), raw)


def _areas_dir(areas_dir: Path | None) -> Path:
    return AREAS_DIR if areas_dir is None else Path(areas_dir)


def load_painted(map_name: str, areas_dir: Path | None = None) -> Painted | None:
    if not map_name or any(char in map_name for char in "/\\:"):
        return None
    path = _areas_dir(areas_dir) / (map_name + _SUFFIX)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        _LOG.warning("Cannot access painted areas file %s: %s", path, exc)
        return None
    return _read_painted(path, raw)


def area_at(
    map_name: str, u: float, v: float, z: float | None = None, table: dict | None = None,
    areas_dir: Path | None = None,
    *, painted_table: dict[str, Painted] | None = None,
) -> dict | None:
    painted = load_painted(map_name, areas_dir) if painted_table is None else painted_table.get(map_name)
    if painted is None:
        return _nearest_label(map_name, u, v, table)
    cell = cell_at(u, v)
    name_id = painted.cells[cell]
    bands = painted.bands.get(cell)
    if bands:
        matched = next((band_id for lo, hi, band_id in bands if z is not None and lo <= z <= hi), None)
        if matched is not None:
            name_id = matched
        elif any(band_id != name_id for _, _, band_id in bands):
            return None
    if name_id == 0:
        return None
    row = painted.names[name_id]
    return {"name": row["name"], "super": row["super"], "path": list(painted.paths[name_id]), "approximate": False}


def tree(map_name: str, areas_dir: Path | None = None) -> list[dict] | None:
    painted = load_painted(map_name, areas_dir)
    if painted is None:
        return None
    return [
        {"name": row["name"], "parent": painted.names[row["parent"]]["name"] if row["parent"] else None,
         "super": row["super"]}
        for row in painted.names.values()
    ]


def all_names(map_name: str, areas_dir: Path | None = None, table: dict | None = None) -> list[str]:
    painted = load_painted(map_name, areas_dir)
    rows = painted.names.values() if painted is not None else callouts(map_name, table)
    return [row["name"] for row in rows]


def is_painted(map_name: str, areas_dir: Path | None = None) -> bool:
    return load_painted(map_name, areas_dir) is not None


@lru_cache(maxsize=1)
def _git_root() -> Path | None:
    webapp = Path(__file__).resolve().parents[2]
    return next((path for path in (webapp, *webapp.parents) if (path / ".git").exists()), None)


def _git_bytes(root: Path, *args: str, input: bytes | None = None) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], input=input,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=False)
    if result.returncode:
        raise OSError(f"git {args[0]} failed")
    return result.stdout


@lru_cache(maxsize=8)
def _published_table(root: Path, directory: Path, sha: str) -> dict[str, Painted]:
    """Read a fetched commit's paint in one batch, without consulting working files."""
    relative = directory.relative_to(root)
    listing = _git_bytes(root, "ls-tree", "-r", "-z", sha, "--", relative.as_posix())
    entries = []
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        path = Path(name.decode("utf-8"))
        mode, kind, oid = metadata.split()
        if kind == b"blob" and path.parent == relative and path.name.endswith(_SUFFIX):
            entries.append((path, oid))
    if not entries:
        return {}
    blobs = _git_bytes(root, "cat-file", "--batch", input=b"".join(oid + b"\n" for _, oid in entries))
    table, offset = {}, 0
    for path, _ in entries:
        end = blobs.index(b"\n", offset)
        oid, kind, size = blobs[offset:end].split()
        if kind != b"blob":
            raise ValueError("expected a painted areas blob")
        start, size = end + 1, int(size)
        raw = blobs[start:start + size]
        offset = start + size + 1
        if len(raw) != size or blobs[offset - 1:offset] != b"\n":
            raise ValueError("incomplete painted areas blob")
        painted = _read_painted(root / path, raw)
        if painted is not None:
            table[path.name.removesuffix(_SUFFIX)] = painted
    return table


@lru_cache(maxsize=None)
def _warn_git_unavailable(root: Path) -> None:
    _LOG.warning("Cannot read origin/main painted areas with git in %s; using reviewed working files", root)


def load_table(areas_dir: Path | None = None, *, published_only: bool = False) -> dict[str, Painted]:
    """Capture reviewed paint; Critical Util reads origin/main in a git checkout."""
    table = {}
    root = _git_root() if published_only else None
    directory = _areas_dir(areas_dir)
    if root is not None:
        try:
            sha = _git_bytes(root, "rev-parse", "--verify", "origin/main").decode("ascii").strip()
            if not sha:
                raise ValueError("missing origin/main commit")
            return _published_table(root.resolve(), directory.resolve(), sha)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            _warn_git_unavailable(root)
    try:
        paths = sorted(directory.glob(f"*{_SUFFIX}"))
    except OSError as exc:
        _LOG.warning("Cannot list painted areas files: %s", exc)
        return table
    for path in paths:
        map_name = path.name.removesuffix(_SUFFIX)
        painted = load_painted(map_name, areas_dir)
        if painted is not None:
            table[map_name] = painted
    return table


def table_hash(areas_dir: Path | None = None, *, painted_table: dict[str, Painted] | None = None) -> str | None:
    table = load_table(areas_dir) if painted_table is None else painted_table
    return _table_digest(tuple(table[map_name].raw for map_name in sorted(table)))


@lru_cache(maxsize=8)
def _table_digest(raws: tuple[bytes, ...]) -> str | None:
    digest = hashlib.sha256()
    for raw in raws:
        digest.update(raw.replace(b"\r\n", b"\n"))
    return digest.hexdigest() if raws else None
