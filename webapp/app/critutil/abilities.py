"""The table of every Valorant ability (abilities.json beside this file) and the parser for the Fandom wiki's
duration rows. Standard library only: nothing from the app (same rule as constants.py), because the replay
worker's extractor and the site both read it."""

from __future__ import annotations

import functools
import hashlib
import json
import re
from pathlib import Path

PATH = Path(__file__).with_name("abilities.json")
EXTRACTOR_FIELDS = ("match", "pieces", "kinds", "type")


@functools.lru_cache(maxsize=None)
def _load(path: str) -> tuple:
    with open(path, encoding="utf-8") as f:
        return tuple(json.load(f)["abilities"])


def load(path=None) -> list[dict]:
    """The entries of abilities.json (or of `path`). Cached per path; callers must not mutate the entries."""
    return list(_load(str(path if path is not None else PATH)))


def lookup(code, agent=None, entries=None):
    for e in (load() if entries is None else entries):
        if agent is not None and e.get("agent") != agent:
            continue
        if re.search(e["match"], code):
            return e
    return None


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def table_hash(entries=None) -> str:
    return _digest(list(load() if entries is None else entries))


def extractor_hash(entries=None) -> str:
    es = load() if entries is None else entries
    return _digest([{k: e.get(k) for k in EXTRACTOR_FIELDS} for e in es])


_ATTR = re.compile(r'^\s*\w+=(?:"[^"]*"|[^\s|"]+)\s*\|(?!\|)')
_FILE = re.compile(r"\[\[File:[^\]]*\]\]")
_NUM = r"(\d+(?:\.\d+)?)"
_RANGE = re.compile(_NUM + r"\s*[-–—]\s*" + _NUM)


def _clean_label(cell: str) -> str:
    cell = re.sub(r"<!--.*?-->", "", cell, flags=re.S)
    cell = _ATTR.sub("", cell)
    return _FILE.sub("", cell).strip()


def _clean_value(cell: str) -> str:
    cell = _ATTR.sub("", cell)
    cell = re.split(r"<br\s*/?>", cell, maxsplit=1)[0]
    cell = re.sub(r"<[^>]*>", "", cell)
    cell = re.sub(r"\{\{[^}]*\}\}", "", cell)
    return cell.strip()


def _number(value: str):
    m = _RANGE.search(value)
    if m and not re.search(r"\d", value[: m.start()]):
        return float(m.group(2))
    m = re.search(_NUM, value)
    return float(m.group(1)) if m else None


def parse_duration(wikitext: str, field: str | None = None):
    """(seconds or None, rows) from the page's ==Stats== wikitable. rows: every row whose label has "duration".
    With `field`, the row labelled so (case-insensitive) is the one used; no such row gives (None, rows)."""
    i = wikitext.find("==Stats==")
    text = wikitext[i:] if i >= 0 else wikitext
    end = text.find("\n|}")
    if end >= 0:
        text = text[:end]
    rows = []
    for chunk in re.split(r"^\|-[^\n]*$", text, flags=re.M)[1:]:
        cells = [ln[1:] for ln in chunk.split("\n") if ln.startswith("|") and not ln.startswith("|}")]
        if len(cells) < 2:
            continue
        label = _clean_label(cells[0])
        if "duration" in label.lower():
            rows.append({"label": label, "value": _clean_value(cells[1])})
    if field is not None:
        chosen = [r for r in rows if r["label"].lower() == field.strip().lower()]
    else:
        chosen = [r for r in rows if r["label"].lower() == "duration"] + rows
    for r in chosen:
        n = _number(r["value"])
        if n is not None:
            return n, rows
        break
    return None, rows
