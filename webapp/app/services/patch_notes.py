"""Reads Riot's VALORANT patch-notes pages for Competitive map-pool moves.

Pure functions over HTML text (no network, no DB): the caller fetches a page and hands the markup here.

- `parse_patch_notes(html)` -> `PatchNotes`: the page's publish time (JSON-LD `datePublished`), every
  "X is IN / Y is OUT of the Competitive queue" line, and whether the page talks about the map pool at all.
  A page that mentions the pool but yields no change is a *miss* (`is_miss`): the wording changed and a
  person should look.
- `parse_listing(html)` -> `list[ListingItem]`: the patch-notes entries of the Game Updates listing page,
  read from its `__NEXT_DATA__` JSON.
- `pool_lines(text)`: one block of plain text to changes; exposed for one-line tests.

Standard library only. Map names are title case, as in `matches.map_name`; times are timezone-aware UTC.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

KNOWN_MAPS = frozenset(
    {
        "Abyss",
        "Ascent",
        "Bind",
        "Breeze",
        "Corrode",
        "Fracture",
        "Haven",
        "Icebox",
        "Lotus",
        "Pearl",
        "Split",
        "Summit",
        "Sunset",
    }
)

_BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "li", "p"})
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_NAME_SPLIT = re.compile(r"\s*,\s*|\s+and\s+", re.I)
_POOL_LINE = re.compile(
    r"^(?P<maps>.+?)\s+(?:is|are)\s+(?:back\s+)?(?P<dir>in|out)\b\s*(?:of\s+)?(?:the\s+)?(?P<queues>.*)$",
    re.I,
)
_POOL_HEADING = re.compile(r"^\W*map pool updates\b", re.I)


@dataclass(frozen=True)
class PoolChange:
    """One map's move into or out of the Competitive pool."""

    map_name: str  # title case
    in_pool: bool


@dataclass(frozen=True)
class PatchNotes:
    published_at: datetime | None  # JSON-LD datePublished
    changes: tuple[PoolChange, ...]
    mentions_pool: bool  # a block contains "map pool updates" or "map rotation"

    @property
    def is_miss(self) -> bool:
        return self.mentions_pool and not self.changes


@dataclass(frozen=True)
class ListingItem:
    title: str
    url: str
    published_at: datetime | None


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_name(raw: str) -> str | None:
    name = re.sub(r"^\s*and\s+", "", raw, flags=re.I)
    name = re.sub(r"[^\w\s]", "", name).strip()
    if not name or not re.fullmatch(r"[A-Za-z ]+", name):
        return None
    if not 1 <= len(name.split()) <= 3:
        return None
    return name.title()


def pool_lines(text: str, in_pool_block: bool = False) -> tuple[PoolChange, ...]:
    """Competitive-pool moves in one block of plain text.

    `in_pool_block` says the block sits under a "Map Pool Updates" item or heading; only there is a name
    outside `KNOWN_MAPS` accepted. Elsewhere such a line is dropped whole.
    """
    found: list[PoolChange] = []
    for sentence in _SENTENCE_SPLIT.split(_normalise(text)):
        match = _POOL_LINE.match(sentence.strip())
        if not match or "competitive" not in match.group("queues").lower():
            continue
        names: list[str] = []
        for part in _NAME_SPLIT.split(match.group("maps")):
            if not part.strip():
                continue
            name = _clean_name(part)
            if name is None:
                names = []
                break
            names.append(name)
        if not names:
            continue
        if not in_pool_block and any(n not in KNOWN_MAPS for n in names):
            continue
        in_pool = match.group("dir").lower() == "in"
        for name in names:
            change = PoolChange(name, in_pool)
            if change not in found:
                found.append(change)
    return tuple(found)


class _Frame:
    __slots__ = ("tag", "parts", "pool")

    def __init__(self, tag: str, pool: bool) -> None:
        self.tag = tag
        self.parts: list[str] = []
        self.pool = pool


class _PageParser(HTMLParser):
    """Collects text blocks (h1-h6, li, p) and JSON-LD scripts.

    A nested block ends its parent's text, so "Map Pool Updates<ul><li>ASCENT ...</li></ul>" gives two blocks.
    Each block is (text, in_rich_text_section, under_pool_heading).
    """

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[tuple[str, bool, bool]] = []
        self.ld_json: list[str] = []
        self._stack: list[_Frame] = []
        self._sections: list[bool] = []
        self._heading_pool = False
        self._in_ld = False
        self._ld_parts: list[str] = []
        self._skip_depth = 0

    def _flush(self, frame: _Frame) -> None:
        text = _normalise("".join(frame.parts))
        frame.parts = []
        if not text:
            return
        is_pool_heading = bool(_POOL_HEADING.match(text))
        if is_pool_heading:
            frame.pool = True
            if frame.tag in _HEADING_TAGS:
                self._heading_pool = True
        elif frame.tag in _HEADING_TAGS:
            self._heading_pool = False
        self.blocks.append((text, any(self._sections), frame.pool or self._heading_pool))

    def _close_to(self, tag: str) -> None:
        if not any(f.tag == tag for f in self._stack):
            return
        while self._stack:
            frame = self._stack.pop()
            self._flush(frame)
            if frame.tag == tag:
                break

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "script":
            if (attrs_d.get("type") or "").lower() == "application/ld+json":
                self._in_ld = True
                self._ld_parts = []
            else:
                self._skip_depth += 1
            return
        if tag == "style":
            self._skip_depth += 1
            return
        if tag == "section":
            self._sections.append(attrs_d.get("data-testid") == "ArticleRichTextBlade")
            return
        if tag == "br":
            if self._stack:
                self._stack[-1].parts.append(" ")
            return
        if tag in _BLOCK_TAGS:
            if self._stack:
                top = self._stack[-1]
                if top.tag == "p" and tag == "p":
                    self._close_to("p")
                elif top.tag in _HEADING_TAGS and tag in _HEADING_TAGS:
                    self._close_to(top.tag)
            if self._stack:
                self._flush(self._stack[-1])
            parent_pool = self._stack[-1].pool if self._stack else False
            self._stack.append(_Frame(tag, parent_pool))
        elif tag in ("ul", "ol", "div") and self._stack:
            self._flush(self._stack[-1])

    def handle_endtag(self, tag):
        if tag == "script":
            if self._in_ld:
                self._in_ld = False
                self.ld_json.append("".join(self._ld_parts))
            elif self._skip_depth:
                self._skip_depth -= 1
            return
        if tag == "style":
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if tag == "section":
            if self._sections:
                self._sections.pop()
            return
        if tag in _BLOCK_TAGS:
            self._close_to(tag)

    def handle_data(self, data):
        if self._in_ld:
            self._ld_parts.append(data)
        elif self._skip_depth:
            return
        elif self._stack:
            self._stack[-1].parts.append(data)

    def close(self):
        super().close()
        while self._stack:
            self._flush(self._stack.pop())


def _published_at(ld_scripts: list[str]) -> datetime | None:
    def nodes(obj):
        if isinstance(obj, dict):
            yield obj
            graph = obj.get("@graph")
            if isinstance(graph, list):
                for child in graph:
                    yield from nodes(child)
        elif isinstance(obj, list):
            for child in obj:
                yield from nodes(child)

    for raw in ld_scripts:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in nodes(data):
            when = _parse_time(node.get("datePublished"))
            if when is not None:
                return when
    return None


def parse_patch_notes(html: str) -> PatchNotes:
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    blocks = parser.blocks
    rich = [b for b in blocks if b[1]]
    if rich:
        blocks = rich
    changes: list[PoolChange] = []
    mentions_pool = False
    for text, _in_blade, under_pool in blocks:
        lowered = text.lower()
        if "map pool updates" in lowered or "map rotation" in lowered:
            mentions_pool = True
        for change in pool_lines(text, in_pool_block=under_pool):
            if change not in changes:
                changes.append(change)
    return PatchNotes(_published_at(parser.ld_json), tuple(changes), mentions_pool)


def parse_listing(html: str, base_url: str = "https://playvalorant.com") -> list[ListingItem]:
    match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return []
    blades = (((data.get("props") or {}).get("pageProps") or {}).get("page") or {}).get("blades") or []
    out: list[ListingItem] = []
    for blade in blades:
        items = blade.get("items") if isinstance(blade, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            payload = (item.get("action") or {}).get("payload") or {}
            url = payload.get("url")
            if not isinstance(url, str) or "patch-notes" not in urlparse(url).path:
                continue
            out.append(
                ListingItem(
                    title=_normalise(str(item.get("title") or "")),
                    url=urljoin(base_url, url),
                    published_at=_parse_time(item.get("publishedAt")),
                )
            )
    return out
