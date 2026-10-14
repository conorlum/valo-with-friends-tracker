"""Build app/data/map_pool_seed.json (the reviewed map-pool history).

Offline part (S3a): parse the wiki's "Map rotations > History" wikitext, apply the
committed corrections (scripts/map_pool_corrections.json), and diff consecutive dated
pools into app.services.map_pool.Change objects.  Fetching, dating the columns, parsing
the later patch notes and writing the seed are S3b (see main()).
"""

from __future__ import annotations

import argparse
import re
import sys
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, help="directory of saved pages (sha1(url)[:12] names)")
    parser.add_argument("--out", type=Path, help="seed JSON to write")
    parser.parse_args(argv)
    # --- S3b stub: fetch the wiki section and patch pages, date the columns from the
    # patch pages' datePublished, parse the patch notes after the wiki's last column,
    # build the Changes with release_changes/pools_to_changes, write the seed JSON.
    raise NotImplementedError("S3b")


if __name__ == "__main__":
    raise SystemExit(main())
