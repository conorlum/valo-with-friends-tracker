"""Build app/data/map_pool_seed.json (the reviewed map-pool history).

Offline part (S3a): parse the wiki's "Map rotations > History" wikitext, apply the
committed corrections (scripts/map_pool_corrections.json), and diff consecutive dated
pools into app.services.map_pool.Change objects.  Fetching, dating the columns, parsing
the later patch notes and writing the seed are S3b (see main()).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.services.map_pool import Change  # noqa: E402

CORRECTIONS_PATH = Path(__file__).resolve().with_name("map_pool_corrections.json")

# Release patch -> release date, for the patches before 5.0 (the wiki's first column).
PRE_5_RELEASE_DATES = {
    "1.0": datetime(2020, 6, 2, tzinfo=timezone.utc),
    "1.10": datetime(2020, 10, 13, tzinfo=timezone.utc),
    "2.08": datetime(2021, 4, 27, tzinfo=timezone.utc),
    "3.05": datetime(2021, 9, 8, tzinfo=timezone.utc),
}

_BG = re.compile(r"background:\s*(#9eff9e|#ffc7c7|gray)", re.I)
_COLSPAN = re.compile(r"colspan\s*=\s*\"?(\d+)", re.I)
_PATCH = re.compile(r"\{\{patch\|([^}|]+)\}\}")
_NAME = re.compile(r"\[\[([^\]|]+)")
_STATE = {"#9eff9e": "in", "#ffc7c7": "out", "gray": "gray"}


def _split_rows(wikitext: str) -> list[list[str]]:
    rows: list[list[str]] = [[]]
    for line in wikitext.splitlines():
        if line.startswith("|-class=sortbottom") or line.startswith("|}"):
            break
        if line.startswith("|-"):
            rows.append([])
        elif line.startswith("|") or line.startswith("!"):
            rows[-1].append(line)
    return rows


def _header_patch(line: str) -> str | None:
    m = _PATCH.search(line)
    if m:
        return m.group(1).strip()
    text = line[1:].split("|")[-1].strip()
    return text if re.fullmatch(r"\d+\.\d+", text) else None


def parse_wiki_history(wikitext: str):
    """Return (columns, pools, releases).

    columns: starting-patch labels in order, only those with at least one filled cell.
    pools: column -> set of maps in the pool (cell background #9eff9e).
    releases: map -> release patch.
    """
    rows = _split_rows(wikitext)
    header = [p for p in (_header_patch(l) for l in rows[1]) if p]
    all_in: dict[str, set[str]] = {c: set() for c in header}
    filled = {c: False for c in header}
    releases: dict[str, str] = {}
    for row in rows[2:]:
        if len(row) < 2:
            continue
        name_m = _NAME.search(row[0])
        if not name_m:
            continue
        name = name_m.group(1).strip()
        rel = _PATCH.search(row[1])
        if rel:
            releases[name] = rel.group(1).strip()
        col = 0
        for line in row[2:]:
            bg = _BG.search(line)
            span = _COLSPAN.search(line)
            n = int(span.group(1)) if span else 1
            state = _STATE[bg.group(1).lower()] if bg else None
            for _ in range(n):
                if col >= len(header):
                    break
                c = header[col]
                if state in ("in", "out"):
                    filled[c] = True
                if state == "in":
                    all_in[c].add(name)
                col += 1
    columns = [c for c in header if filled[c]]
    return columns, {c: all_in[c] for c in columns}, releases


def apply_corrections(columns, pools, corrections):
    """Apply corrections in order; return new (columns, pools), inputs untouched.

    A correction with "after_patch" inserts a new column `patch` right after that column,
    starting from a copy of that column's pool; otherwise it edits column `patch` in place.
    "add" / "remove" are lists of map names.
    """
    columns = list(columns)
    pools = {c: set(p) for c, p in pools.items()}
    for corr in corrections:
        patch = corr["patch"]
        after = corr.get("after_patch")
        if after is not None:
            if after not in pools:
                raise KeyError(after)
            columns.insert(columns.index(after) + 1, patch)
            pools[patch] = set(pools[after])
        elif patch not in pools:
            raise KeyError(patch)
        pools[patch] |= set(corr.get("add", ()))
        pools[patch] -= set(corr.get("remove", ()))
    return columns, pools


def release_changes(releases: dict[str, str]) -> list[Change]:
    """INs at the release date for maps released before 5.0."""
    return [
        Change(m, True, PRE_5_RELEASE_DATES[p], p, "seed")
        for m, p in sorted(releases.items())
        if p in PRE_5_RELEASE_DATES
    ]


def pre_5_0_pool(releases: dict[str, str]) -> set[str]:
    return {m for m, p in releases.items() if p in PRE_5_RELEASE_DATES}


def pools_to_changes(dated_pools, prior=frozenset(), source: str = "seed") -> list[Change]:
    """Diff consecutive (patch, effective_at, pool) entries into Changes.

    `prior` is the pool before the first entry (the pre-5.0 maps).
    """
    changes: list[Change] = []
    before = set(prior)
    for patch, at, pool in dated_pools:
        pool = set(pool)
        for m in sorted(pool - before):
            changes.append(Change(m, True, at, patch, source))
        for m in sorted(before - pool):
            changes.append(Change(m, False, at, patch, source))
        before = pool
    return changes


# ---------------------------------------------------------------- S3b: fetching and dating

USER_AGENT = "valo-with-friends-tracker map-pool seed (+https://valowithfriendstracker.onrender.com)"
TIMEOUT_S = 20
PAUSE_S = 1.0
WIKI_URL = "https://valorant.fandom.com/api.php?action=parse&page=Maps&prop=wikitext&section=8&format=json"
LISTING_URL = "https://playvalorant.com/en-us/news/game-updates/"
PATCH_URL_BASE = "https://playvalorant.com/en-us/news/game-updates/valorant-patch-notes-"
DEFAULT_OUT = WEBAPP_ROOT / "app" / "data" / "map_pool_seed.json"
# 5.0 went live on the wiki's day, not on its patch-notes page date (Pearl rule).
LIVE_DATE_OVERRIDES = {"5.0": datetime(2022, 6, 22, tzinfo=timezone.utc)}
# Patch label -> URL, where the pattern valorant-patch-notes-<maj>-<min>/ does not hold.
URL_EXCEPTIONS = {
    "8.11": PATCH_URL_BASE + "811/",
    "9.01": PATCH_URL_BASE + "901/",
    "9.02": PATCH_URL_BASE + "902/",
    "9.06": PATCH_URL_BASE + "906/",
    "10.07": "https://playvalorant.com/en-us/news/valorant-patch-notes-10-07/",
}
_TITLE_PATCH = re.compile(r"patch notes\s+(\d+\.\d+)", re.I)


class Fetcher:
    """fetch(url) -> text.  With a cache dir, a saved page makes no request."""

    def __init__(self, cache: Path | None = None):
        self.cache = cache
        self.requests = 0
        self._last = 0.0

    def __call__(self, url: str) -> str:
        path = None
        if self.cache is not None:
            path = Path(self.cache) / hashlib.sha1(url.encode()).hexdigest()[:12]
            if path.exists():
                return path.read_text(encoding="utf-8", errors="replace")
        wait = PAUSE_S - (time.monotonic() - self._last)
        if self.requests and wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            body = r.read().decode("utf-8", "replace")
        self._last = time.monotonic()
        self.requests += 1
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return body


def patch_key(label: str) -> tuple[int, int]:
    major, minor = label.split(".")
    return int(major), int(minor)


def patch_url(label: str, listing_urls: dict[str, str]) -> str:
    """URL of a patch's notes: the listing's, else the exception table, else the pattern."""
    if label in listing_urls:
        return listing_urls[label]
    if label in URL_EXCEPTIONS:
        return URL_EXCEPTIONS[label]
    major, minor = label.split(".")
    return f"{PATCH_URL_BASE}{major}-{minor}/"


def listing_patch_urls(items) -> dict[str, str]:
    """Patch label -> URL from the game-updates listing items (titles 'VALORANT Patch Notes 13.08')."""
    out: dict[str, str] = {}
    for it in items:
        m = _TITLE_PATCH.search(it.title or "")
        if m:
            out.setdefault(m.group(1), it.url)
    return out


def wikitext_from_api(body: str) -> str:
    return json.loads(body)["parse"]["wikitext"]["*"]


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_seed(fetch, built_at: datetime | None = None, corrections=None) -> dict:
    """Run the whole build through `fetch(url) -> text`; return the seed dict."""
    from app.services.patch_notes import parse_listing, parse_patch_notes

    if corrections is None:
        corrections = json.loads(CORRECTIONS_PATH.read_text(encoding="utf-8"))
    columns, pools, releases = parse_wiki_history(wikitext_from_api(fetch(WIKI_URL)))
    wiki_last = columns[-1]
    columns, pools = apply_corrections(columns, pools, corrections)
    listing_urls = listing_patch_urls(parse_listing(fetch(LISTING_URL)))
    corr_dates = {
        c["patch"]: datetime.fromisoformat(c["published_at"].replace("Z", "+00:00"))
        for c in corrections
        if c.get("published_at")
    }
    patch_rows: list[dict] = []
    urls: dict[str, str] = {}

    def read(label: str):
        url = patch_url(label, listing_urls)
        notes = parse_patch_notes(fetch(url))
        urls[label] = url
        when = notes.published_at or corr_dates.get(label)
        if when is None:
            raise ValueError(f"no publish date for patch {label} ({url})")
        patch_rows.append({"url": url, "patch": label, "published_at": _iso(when)})
        return notes, when

    dated = []
    for label in columns:
        notes, when = read(label)
        when = corr_dates.get(label, LIVE_DATE_OVERRIDES.get(label, when))
        dated.append((label, when, set(pools[label])))
    # Patches after the wiki's last filled column: parse the notes and apply in order.
    later = sorted((p for p in listing_urls if patch_key(p) > patch_key(wiki_last)), key=patch_key)
    pool = set(dated[-1][2])
    for label in later:
        notes, when = read(label)
        if notes.is_miss:
            print(f"warning: patch {label} mentions the pool but nothing parsed", file=sys.stderr)
        for ch in notes.changes:
            (pool.add if ch.in_pool else pool.discard)(ch.map_name)
        dated.append((label, when, set(pool)))
    changes = release_changes(releases)
    changes += pools_to_changes(dated, prior=pre_5_0_pool(releases))
    rows = []
    for c in changes:
        rows.append({
            "map": c.map_name,
            "in_pool": c.in_pool,
            "effective_at": _iso(c.effective_at),
            "patch": c.patch,
            "source_url": urls.get(c.patch, "") if c.patch in urls else WIKI_URL,
        })
    rows.sort(key=lambda r: (r["effective_at"], r["map"], r["in_pool"]))
    patch_rows.sort(key=lambda r: patch_key(r["patch"]))
    return {
        "built_at": _iso(built_at or datetime.now(timezone.utc)),
        "sources": [WIKI_URL, LISTING_URL],
        "changes": rows,
        "patch_notes": patch_rows,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, help="directory of saved pages (sha1(url)[:12] names)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="seed JSON to write")
    args = parser.parse_args(argv)
    fetcher = Fetcher(args.cache)
    seed = build_seed(fetcher)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(seed, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(seed['changes'])} changes, {len(seed['patch_notes'])} patch pages, "
          f"{fetcher.requests} network requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
