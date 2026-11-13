"""Identify every source and asset used by the portable Critical Util build.

Standard library only: the replay worker can compare builds without importing the builder.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


# A util input key (a round row's or an observation's): the one pattern the site, the task and the worker's store share.
KEY_RE = r"^[0-9A-Za-z._-]{1,120}$"

_WEBAPP_ROOT =Path(__file__).resolve().parents[2]
SOURCES = tuple(sorted(
    [path.relative_to(_WEBAPP_ROOT).as_posix() for path in (_WEBAPP_ROOT / "app" / "critutil").rglob("*.py")]
    + [
        "app/critutil/abilities.json",
        "app/services/util_values.py",
        "app/services/util_model.py",
        "app/static/js/replay.js",
        "app/static/data/callouts.json",
        "app/coach/__init__.py",
        "app/coach/areas.py",
        # areas.py's grid (control_format.GRID) and minimap scale (format.UV_SCALE) place every cast's area
        "app/replays/__init__.py",
        "app/replays/control_format.py",
        "app/replays/format.py",
        "app/replays/player_state.py",
    ]
))


def digest(webapp_root: str | Path) -> str:
    """SHA-256 of sorted POSIX paths and LF-normalized contents, truncated to 16 hex digits.

    Reading each declared source directly makes a missing asset an error.
    """
    root = Path(webapp_root)
    result = hashlib.sha256()
    for relative in SOURCES:
        result.update(relative.encode("utf-8"))
        result.update((root / relative).read_bytes().replace(b"\r\n", b"\n"))
    return result.hexdigest()[:16]
