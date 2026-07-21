"""
Spike/utility: capture window.__INITIAL_STATE__ from one or more tracker.gg
match pages using your own already-logged-in Chrome session.

Does NOT crawl or discover matches on its own -- you supply the exact match
URLs you want. It drives your real, authenticated browser tab through that
list with human-paced delays between navigations; it never fetches pages
independently of a real browser session.

Prerequisite: run scripts\\launch_trackergg_chrome.ps1 first (leaves a Chrome
window open with --remote-debugging-port=9222) and be logged into tracker.gg
in that window.

Usage:
    .venv\\Scripts\\python.exe scripts\\capture_trackergg_state.py <match_url> [<match_url> ...]
    .venv\\Scripts\\python.exe scripts\\capture_trackergg_state.py --file urls.txt

Dumps one JSON file per match into webapp/captured_state/<slug>.json for
inspection, so we can confirm the real shape of __INITIAL_STATE__ before
writing the full mapper into app/adapters/trackergg_browserstate_source.py.
"""

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

CDP_URL = "http://localhost:9222"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "captured_state"
MIN_DELAY_SECONDS = 5
MAX_DELAY_SECONDS = 12


def slug_for(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"[^a-zA-Z0-9_-]", "_", tail) or "match"


def capture_one(page, url: str) -> dict:
    page.goto(url, wait_until="networkidle", timeout=60_000)
    state = page.evaluate("() => window.__INITIAL_STATE__ ?? null")
    if state is None:
        raise RuntimeError(
            f"window.__INITIAL_STATE__ was not found on {url} -- "
            "confirm you're logged in and the page fully loaded."
        )
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="*", help="tracker.gg match URLs")
    parser.add_argument("--file", help="text file with one match URL per line")
    args = parser.parse_args()

    urls = list(args.urls)
    if args.file:
        urls.extend(
            line.strip()
            for line in Path(args.file).read_text().splitlines()
            if line.strip()
        )

    if not urls:
        parser.error("provide at least one match URL, or --file with a list")

    OUTPUT_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]
        page = context.new_page()

        for i, url in enumerate(urls):
            print(f"[{i + 1}/{len(urls)}] capturing {url}")
            state = capture_one(page, url)

            out_path = OUTPUT_DIR / f"{slug_for(url)}.json"
            out_path.write_text(json.dumps(state, indent=2))
            print(f"  -> saved {out_path}")

            if i < len(urls) - 1:
                delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
                print(f"  waiting {delay:.1f}s before next match...")
                time.sleep(delay)

        page.close()


if __name__ == "__main__":
    main()
