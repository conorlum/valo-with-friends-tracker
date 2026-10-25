"""Builds app/critutil/abilities.json from the seed (abilities_seed.json) and the Fandom wiki's ability pages. Two steps:

    $PY scripts/fetch_util_durations.py --fetch <dir>      # downloads each page's wikitext, one at a time
    $PY scripts/fetch_util_durations.py --from <dir>       # parses the saved pages and writes abilities.json

No database. Durations come from the wiki's Stats table; an entry with none there takes the owner's `owner_s`
from the seed (worked out from the page's windup and tick rows), else gets duration_s null and provisional true."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.critutil import abilities  # noqa: E402

SEED = WEBAPP_ROOT / "app" / "critutil" / "abilities_seed.json"
OUT = WEBAPP_ROOT / "app" / "critutil" / "abilities.json"
URL = "https://valorant.fandom.com/api.php?action=parse&page={page}&prop=wikitext&format=json"
USER_AGENT = "valo-with-friends-tracker ability table fetch (one page a second)"


def page_of(entry: dict) -> str:
    return (entry.get("wiki") or {}).get("page") or entry["ability"]


def file_name(page: str) -> str:
    return page.replace("/", "_") + ".json"


def fetch(seed_path: Path, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pages = list(dict.fromkeys(page_of(e) for e in json.loads(seed_path.read_text(encoding="utf-8"))["abilities"]))
    for n, page in enumerate(pages):
        if n:
            time.sleep(1)
        req = urllib.request.Request(URL.format(page=urllib.parse.quote(page)), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            (directory / file_name(page)).write_bytes(r.read())
        print("fetched", page)


def merge(seed_path: Path, directory: Path, out: Path, today: str | None = None) -> None:
    today = today or time.strftime("%Y-%m-%d")
    entries = json.loads(seed_path.read_text(encoding="utf-8"))["abilities"]
    mismatched, missing = [], []
    for e in entries:
        page = page_of(e)
        f = directory / file_name(page)
        secs, rows = None, []
        wiki = dict(e.get("wiki") or {})
        field = wiki.get("field")
        if field in ("-", ""):
            field = None
        if f.exists():
            saved = json.loads(f.read_text(encoding="utf-8"))
            if "parse" in saved:
                secs, rows = abilities.parse_duration(saved["parse"]["wikitext"]["*"], field)
        wiki["page"] = page
        wiki["field"] = field
        e["duration_s"] = secs
        if secs is None and e.get("owner_s") is not None:
            # the owner's figure, worked out from the page's windup and tick rows (seed `owner_s`, `owner_note`)
            e["duration_s"] = float(e["owner_s"])
            e.pop("provisional", None)
            wiki.pop("value", None)
            wiki["rows"] = rows
        elif secs is None:
            e["provisional"] = True
            wiki.pop("value", None)
            wiki["rows"] = rows
            missing.append(e)
        else:
            e.pop("provisional", None)
            chosen = next(r for r in rows if _is_chosen(r, field, rows))
            wiki.update(field=chosen["label"], value=chosen["value"], fetched=today, rows=rows)
            r = e.get("replay_s")
            if r is not None and abs(secs - r) > 1:
                mismatched.append(e)
        e["wiki"] = wiki
    out.write_text(json.dumps({"abilities": entries}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(entries)} entries)")
    print("wiki duration differs from replay_s by more than 1 s:")
    for e in mismatched:
        print(f"  {e['agent']} {e['ability']}: wiki {e['duration_s']} vs replay {e['replay_s']}")
    print("no wiki duration (provisional):")
    for e in missing:
        print(f"  {e['agent']} {e['ability']}")


def _is_chosen(row: dict, field: str | None, rows: list[dict]) -> bool:
    if field is not None:
        return row["label"].lower() == field.strip().lower()
    exact = [r for r in rows if r["label"].lower() == "duration"]
    return row is (exact or rows)[0]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", type=Path, metavar="DIR")
    ap.add_argument("--from", dest="from_dir", type=Path, metavar="DIR")
    ap.add_argument("--seed", type=Path, default=SEED)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    if a.fetch:
        fetch(a.seed, a.fetch)
    if a.from_dir:
        merge(a.seed, a.from_dir, a.out)
    if not (a.fetch or a.from_dir):
        ap.error("give --fetch DIR or --from DIR")


if __name__ == "__main__":
    main()
