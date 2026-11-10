"""Map layout history and replay selection, without worker dependencies."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Literal


ASSET_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9]*(_\d+_\d+)?$")
_PATCH = re.compile(r"release-(\d+)\.(\d+)$")
_SUFFIX = re.compile(r"_\d+_\d+$")
_DATA = Path(__file__).resolve().parents[1] / "static" / "data"


def parse_patch(branch) -> tuple[int, int] | None:
    """Read the numeric patch at the end of a stored game branch."""
    match = _PATCH.search(branch) if isinstance(branch, str) else None
    return (int(match[1]), int(match[2])) if match else None


def map_of(asset_key: str) -> str:
    return _SUFFIX.sub("", asset_key)


def control_layouts(index_path) -> list[str]:
    """The control assets shipped with this worker or Workflow image."""
    return sorted(json.loads(Path(index_path).read_text(encoding="utf-8"))["maps"])


def key_for(map: str, patch: tuple[int, int] | None) -> str:
    return map if patch is None else f"{map}_{patch[0]}_{patch[1]}"


@dataclass(frozen=True)
class Layout:
    display_id: str
    asset_key: str
    start: tuple[int, int] | None
    drawn_from: tuple[int, int] | None
    region_mask: str | None


@dataclass(frozen=True)
class Selection:
    state: Literal["ok", "layout_pending", "layout_unknown"]
    layout: Layout | None


def load_registry(path=None) -> dict:
    """Load the registry relative to this module, or from an explicit path."""
    source = _DATA / "layouts.json" if path is None else Path(path)
    return json.loads(source.read_text(encoding="utf-8"))


def _registry_patch(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    major, minor = value.split(".")
    return int(major), int(minor)


def layouts_of(map: str, registry=None) -> list[Layout]:
    registry = load_registry() if registry is None else registry
    rows = registry["maps"].get(map)
    if rows is None:
        return [Layout(f"{map}@base", map, None, None, None)]
    layouts = [Layout(
        display_id=row["id"],
        asset_key=row["key"],
        start=_registry_patch(row["start"]),
        drawn_from=_registry_patch(row["drawn_from"]),
        region_mask=row["regions"],
    ) for row in rows]
    return sorted(layouts, key=lambda layout: (layout.start is not None, layout.start))


def layout_for(map_name: str, game_branch, *, registry=None, index=None) -> Selection:
    """Choose by patch, then report whether that layout's assets are available."""
    layouts = layouts_of(map_name, registry)
    if len(layouts) == 1:
        return Selection("ok", layouts[0])
    patch = parse_patch(game_branch)
    if patch is None:
        return Selection("layout_unknown", None)
    layout = next(layout for layout in reversed(layouts)
                  if layout.start is None or layout.start <= patch)
    if index is None:
        index = json.loads((_DATA / "control" / "index.json").read_text(encoding="utf-8"))
    state = "ok" if layout.asset_key in index["maps"] else "layout_pending"
    return Selection(state, layout)
