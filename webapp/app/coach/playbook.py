"""The coach's playbook (webapp/coach/playbook.md): the owner's principles, the themes a point may carry, and
the rules the model writes under. Its `version` is stamped on every report; bump it by hand on any edit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "coach" / "playbook.md"


class PlaybookError(ValueError):
    pass


@dataclass(frozen=True)
class Playbook:
    version: str
    themes: tuple[str, ...]
    text: str


def load(path: Path | None = None) -> Playbook:
    raw = Path(path or PATH).read_text(encoding="utf-8")
    parts = raw.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise PlaybookError("the playbook needs a front block between two --- lines")
    fields = {}
    for line in parts[1].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    if not fields.get("version"):
        raise PlaybookError("the playbook's front block needs a version")
    themes = tuple(t.strip() for t in fields.get("themes", "").split(",") if t.strip())
    if not themes:
        raise PlaybookError("the playbook's front block needs themes")
    text = parts[2].strip()
    if not text:
        raise PlaybookError("the playbook is empty")
    return Playbook(fields["version"], themes, text)
