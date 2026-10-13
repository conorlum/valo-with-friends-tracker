"""The coach's playbook (webapp/coach/playbook.md): the owner's principles, the themes a point may carry, and
the rules the model writes under. Its `version` is stamped on every report; bump it by hand on any edit.
`load` refuses a file at version 0 or with no principles: the coach judges by the owner's own."""

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
    if not _principles(text) or not fields["version"].strip("0"):          # "0", "00": no version yet
        # The owner's decision: the coach judges by the owner's own principles, so it does not run without them.
        raise PlaybookError("the playbook has no principles yet: the owner writes theirs under '# Principles' in "
                            f"{PATH.name} and sets version to 1 or higher")
    return Playbook(fields["version"], themes, text)


def _principles(text: str) -> list[str]:
    """The lines under the '# Principles' heading, up to the next top-level heading."""
    lines, inside = [], False
    for line in text.splitlines():
        if line.startswith("# "):
            inside = line.strip().lower() == "# principles"
        elif inside and line.strip():
            lines.append(line.strip())
    return lines
