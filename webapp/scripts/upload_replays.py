"""Uploads the newest replays that aren't on the friends site yet, through its own upload page.

    .\\.venv313\\Scripts\\python.exe scripts\\upload_replays.py --count 6
    .\\.venv313\\Scripts\\python.exe scripts\\upload_replays.py --count 6 --dry-run

Reads `<match uuid>.vrf` files from Valorant's Demos folder (`--source`, default
`%LOCALAPPDATA%\\VALORANT\\Saved\\Demos`), newest first, and skips any whose replay page
(`/replays/<uuid>`) already exists on the site. Each of the first `--count` left is uploaded
the way the browser does it: the invite code, the file, then the job's status polled until it
is stored or failed. One at a time, because the site allows one unfinished upload per session.
Polling is what stores a finished parse, so this waits for each job instead of leaving it.

The invite code comes from `$REPLAY_UPLOAD_CODE`, else a `REPLAY_UPLOAD_CODE=` line in
`webapp/.env.remote` (gitignored; this repository is public), else a prompt.

Exits 0 when every chosen upload is stored, 1 when any failed, 2 on a setup error.
"""

from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

SITE = "https://valowithfriendstracker.onrender.com"
REPLAY_NAME = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.vrf$", re.IGNORECASE)
# Not for the site: the Swiftplay proof-of-concept file (docs/replay-viewer-handoff.md).
SKIP = {"d45b2844-d7dd-4efd-bbf7-551854710350"}
POLL_SECONDS = 5
JOB_TIMEOUT_S = 25 * 60  # the site fails a job itself after 20 minutes
CHUNK = 1 << 20
WEBAPP = Path(__file__).resolve().parent.parent


def default_source() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "VALORANT" / "Saved" / "Demos"


def upload_code() -> str:
    code = os.environ.get("REPLAY_UPLOAD_CODE")
    env_remote = WEBAPP / ".env.remote"
    if not code and env_remote.exists():
        for line in env_remote.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*REPLAY_UPLOAD_CODE\s*=\s*(.*?)\s*$", line)
            if match:
                code = match.group(1)
    return code or getpass.getpass("Replay invite code: ")


def on_site(site: str, match_uuid: str) -> bool:
    try:
        with urllib.request.urlopen(f"{site}/replays/{match_uuid}", timeout=60):  # GET: the route has no HEAD
            return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise


class Multipart:
    """A multipart body that streams the file from disk and prints its progress."""

    def __init__(self, path: Path):
        self.path = path
        self.boundary = uuid.uuid4().hex
        self.head = (f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                     f"filename=\"{path.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        self.tail = f"\r\n--{self.boundary}--\r\n".encode()
        self.size = path.stat().st_size
        self.length = len(self.head) + self.size + len(self.tail)

    def __iter__(self):
        yield self.head
        sent, started, last = 0, time.monotonic(), 0.0
        with self.path.open("rb") as handle:
            while chunk := handle.read(CHUNK):
                sent += len(chunk)
                now = time.monotonic()
                if now - last > 0.5 or sent == self.size:
                    last = now
                    print(f"\r  uploading {sent / 1e6:5.1f} of {self.size / 1e6:.1f} MB "
                          f"({100 * sent // self.size}%)", end="", flush=True)
                yield chunk
        print(f" in {time.monotonic() - started:.0f} s", flush=True)
        yield self.tail


def page_error(html: str) -> str:
    match = re.search(r'role="alert"[^>]*>(.*?)<', html, re.S)
    return match.group(1).strip() if match else ""


def upload_one(opener, site: str, path: Path) -> tuple[bool, str]:
    body = Multipart(path)
    request = urllib.request.Request(f"{site}/replays/upload", data=iter(body), method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={body.boundary}", "Content-Length": str(body.length)})
    try:
        with opener.open(request, timeout=600) as response:
            job_url = response.geturl()
    except urllib.error.HTTPError as error:
        return False, page_error(error.read().decode("utf-8", "replace")) or f"the site answered {error.code}"
    if "/replays/uploads/" not in job_url:
        return False, f"unexpected page after upload: {job_url}"
    print(f"  parsing ({job_url})", flush=True)
    started = time.monotonic()
    while time.monotonic() - started < JOB_TIMEOUT_S:
        time.sleep(POLL_SECONDS)
        try:
            with opener.open(f"{job_url}/status", timeout=60) as response:
                status = json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError):
            continue  # a dropped poll is retried; the job keeps running on the worker
        elapsed = time.monotonic() - started
        print(f"\r  parsing... {int(elapsed // 60)}:{int(elapsed % 60):02d}", end="", flush=True)
        if status.get("status") == "stored":
            print()
            linked = "linked to its match" if status.get("linked") else "not linked to a match yet"
            note = f" ({status['error']})" if status.get("error") else ""
            return True, f"stored, {linked}{note}: {site}{status.get('replay_url', '')}"
        if status.get("status") == "failed":
            print()
            return False, f"failed: {status.get('error') or 'unknown error'}"
    print()
    return False, "gave up waiting after 25 minutes"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=5, help="how many new replays to upload (newest first)")
    parser.add_argument("--source", type=Path, default=default_source(), help="Valorant's Demos folder")
    parser.add_argument("--site", default=SITE)
    parser.add_argument("--dry-run", action="store_true", help="list what would be uploaded, upload nothing")
    args = parser.parse_args(argv)
    site = args.site.rstrip("/")

    if not args.source.is_dir():
        print(f"No replay folder at {args.source}", file=sys.stderr)
        return 2
    files = sorted((p for p in args.source.iterdir() if REPLAY_NAME.match(p.name)),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    todo = []
    for path in files:
        match_uuid = REPLAY_NAME.match(path.name).group(1).lower()
        if match_uuid in SKIP:
            continue
        if on_site(site, match_uuid):
            print(f"on the site already: {path.name}")
            continue
        todo.append(path)
        if len(todo) == args.count:
            break
    if not todo:
        print("Nothing to upload: every replay in the folder is on the site.")
        return 0
    print(f"To upload ({len(todo)}): " + ", ".join(p.name for p in todo))
    if args.dry_run:
        return 0

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    data = urllib.parse.urlencode({"code": upload_code()}).encode()
    try:
        opener.open(urllib.request.Request(f"{site}/replays/upload/code", data=data, method="POST"), timeout=60)
    except urllib.error.HTTPError as error:
        reason = page_error(error.read().decode("utf-8", "replace")) or f"the site answered {error.code}"
        print(f"Invite code refused: {reason}", file=sys.stderr)
        return 2

    failed = 0
    for number, path in enumerate(todo, 1):
        print(f"[{number}/{len(todo)}] {path.name} ({path.stat().st_size / 1e6:.0f} MB)", flush=True)
        ok, message = upload_one(opener, site, path)
        print(f"  {message}", flush=True)
        if not ok:
            failed += 1
            if "an hour" in message:  # the site's hourly limit: the rest would be refused too
                print("Stopping: the hourly upload limit is reached. Run it again later.")
                break
    print(f"Done: {len(todo) - failed} stored, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
