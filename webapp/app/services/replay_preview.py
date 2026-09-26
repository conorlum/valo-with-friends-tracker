"""Where the local replay preview's bundles live, and which matches have one.

Dev-only, like app/routers/replay_preview.py: everything here is off (None / False) unless
`settings.replay_preview_dir` names a folder, and always off in demo mode. Reads local files
only; no DB access.
"""

import re
from pathlib import Path

from app.config import settings

UUID_SHAPE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def preview_root() -> Path | None:
    if settings.demo_mode or not settings.replay_preview_dir:
        return None
    root = Path(settings.replay_preview_dir)
    return root if root.is_dir() else None


def replay_preview_enabled() -> bool:
    return preview_root() is not None


def replay_url(external_id: str | None) -> str | None:
    """The preview page for this match (keyed on matches.external_id, which is the replay's
    match UUID), or None when the preview is off or no bundle was built for it."""
    root = preview_root()
    if root is None or not external_id:
        return None
    uuid = external_id.lower()
    if not UUID_SHAPE.match(uuid) or not (root / uuid / "bundle.json").is_file():
        return None
    return f"/dev/replays/{uuid}"
