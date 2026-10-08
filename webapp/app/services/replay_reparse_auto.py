"""The automatic re-parse queue: the site's half (docs/superpowers/plans/2026-10-07-auto-reparse-queue-impl.md,
tasks 4-7).

A stored replay is stale when its `recipe` is not the one this deploy stamps (inequality: recipes are never
ordered). A stale *uploaded* replay whose recording the worker has archived is parsed again from that file,
one at a time, behind every upload. A stale local ingest is counted and left to `scripts/reingest_replays.py`,
even when the same file happens to be archived.

- `off_reason`: this site's own settings. The worker's half is `replay_upload.worker_off_reason`; nothing is
  started unless both say None. `REPLAY_REPARSE_AUTO` is off by default.
- `classify`: every stored replay with its state, from the database and an archive index the caller already
  read. It makes no worker request and writes nothing.
- `unfinished_attempts`: every automatic attempt not yet settled, under any recipe. One is enough to stop a
  new one site-wide: an attempt whose acceptance is unknown is still unfinished.

An attempt is a `replay_uploads` row (`replay_upload.AUTO_PREFIX`, `auto_context`). Only an attempt the worker
accepted counts as a try: a refusal before acceptance used no parse. A new recipe is a new tag, so finished
tries start again from zero, but an unfinished attempt of an older recipe still blocks.

`now` is float epoch seconds everywhere here; a naive `finished_at` (sqlite) is read as UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import settings
from app.models.match import Match
from app.models.replay import Replay, ReplayDeletion, ReplayUpload
from app.services import replay_upload as uploads

MAX_TRIES = 2       # accepted attempts per match, file and target recipe
BACKOFF_S = 3600    # between those two
SETTLE_S = 180      # after an accepted attempt settles, before the next is reserved
INDEX_S = 120       # the archive index is read at most this often
TAG_MAX = 64        # replay_uploads.session_key holds 64 characters

STATES = ("deleted", "local", "current", "resolving", "in_flight", "gave_up", "unsupported_build", "unknown",
          "no_archive", "other_recording", "backoff", "eligible")
# Uploaded replays that are waiting for a re-parse: computing their map control now would be thrown away.
HELD = ("eligible", "backoff", "resolving", "in_flight")
# The worker's contract refusal (replay_worker/server.py): the file can't be condensed under this recipe,
# however often it is tried.
FINAL_REFUSAL = "not condensable"


@dataclass(frozen=True)
class Entry:
    replay_id: int
    match_uuid: str
    map_name: str
    state: str
    rank: int               # the order attempts are made in, 0 first (the control queue's: newest match first)
    attempts: int           # accepted attempts that ended, for this file under the target recipe
    last_error: str | None  # of the latest of them


def off_reason(recipe: str | None = None) -> str | None:
    """Why this site starts no automatic re-parse, from its own settings; None when they allow it."""
    if settings.demo_mode:
        return "demo mode"
    if not settings.replay_reparse_auto:
        return "REPLAY_REPARSE_AUTO is off"
    if not settings.replay_control_remote:
        return "map control on the worker (REPLAY_CONTROL_REMOTE) is off"
    if not settings.replay_worker_url:
        return "no replay worker URL is set"
    if len(uploads.auto_tag(recipe if recipe is not None else uploads.site_recipe())) > TAG_MAX:
        return "this recipe is too long for an attempt's tag"
    return None


def index_of(archive: dict | None) -> dict[str, str] | None:
    """`GET /archive` as {match uuid: sha256 of the archived recording}. None in, None out: an index that
    couldn't be read is unknown, never an empty archive."""
    if not isinstance(archive, dict) or not isinstance(archive.get("files"), list):
        return None
    return {str(entry["match_uuid"]).lower(): entry.get("sha256") for entry in archive["files"]
            if isinstance(entry, dict) and entry.get("match_uuid")}


def epoch(when: datetime | None) -> float | None:
    if when is None:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).timestamp()


def unfinished_attempts(session) -> list[ReplayUpload]:
    """Every automatic attempt not yet settled, oldest first, whatever recipe it aims for and whether or not
    its context can be read."""
    return (session.query(ReplayUpload)
            .filter(ReplayUpload.status.in_(uploads.UNFINISHED),
                    ReplayUpload.session_key.startswith(uploads.AUTO_PREFIX, autoescape=True))
            .order_by(ReplayUpload.created_at, ReplayUpload.id).all())


def accepted_attempts(session, recipe: str) -> dict[tuple[str, str], list[ReplayUpload]]:
    """The settled attempts the worker accepted for `recipe`, oldest first, by (match uuid, file sha256).
    Keyed from the row's validated context: `replay_id` is null after a replacement, and a sha alone could be
    another match's."""
    found: dict[tuple[str, str], list[ReplayUpload]] = {}
    rows = (session.query(ReplayUpload)
            .filter(ReplayUpload.session_key == uploads.auto_tag(recipe),
                    ReplayUpload.status.notin_(uploads.UNFINISHED))
            .order_by(ReplayUpload.finished_at, ReplayUpload.created_at, ReplayUpload.id))
    for upload in rows:
        context = uploads.auto_context(upload)
        if context is None or context["phase"] != "accepted" or context["target_recipe"] != recipe:
            continue
        found.setdefault((context["match_uuid"], context["source_sha256"]), []).append(upload)
    return found


def classify(session, recipe: str, index: dict[str, str] | None, builds: frozenset[str], now: float) -> list[Entry]:
    """Every stored replay, in the order attempts are made. `index` is `index_of` the worker's archive (None:
    not known), `builds` the pin's supported builds."""
    deleted = {str(uuid).lower() for (uuid,) in session.query(ReplayDeletion.match_uuid)}
    waiting: dict[str, str] = {}
    for upload in unfinished_attempts(session):   # across every recipe: an old target's attempt still blocks
        context = uploads.auto_context(upload)
        if context is not None and waiting.get(context["match_uuid"]) != "in_flight":
            waiting[context["match_uuid"]] = "in_flight" if context["phase"] == "accepted" else "resolving"
    tried = accepted_attempts(session, recipe)
    rows = (session.query(Replay, Match.played_at).outerjoin(Match, Match.id == Replay.match_id).all())

    def order(row):  # the control queue's D2 order (replay_control_remote._rank)
        replay, played = row
        if replay.link_status == "linked" and played is not None:
            return (0, -epoch(played), -replay.id)
        return (1, -(epoch(replay.created_at) or 0.0), -replay.id)

    entries = []
    for rank, (replay, _) in enumerate(sorted(rows, key=order)):
        uuid = replay.match_uuid.lower()
        attempts = tried.get((uuid, replay.source_sha256), [])
        last = attempts[-1] if attempts else None
        if uuid in deleted:
            state = "deleted"
        elif replay.source != "upload":
            state = "local"
        elif replay.recipe == recipe:
            state = "current"
        elif uuid in waiting:
            state = waiting[uuid]
        elif replay.game_branch not in builds:
            state = "unsupported_build"
        elif index is None:
            state = "unknown"
        elif uuid not in index:
            state = "no_archive"
        elif index[uuid] != replay.source_sha256:
            state = "other_recording"
        elif len(attempts) >= MAX_TRIES or any((a.error or "").startswith(FINAL_REFUSAL) for a in attempts):
            state = "gave_up"
        elif last is not None and now - (epoch(last.finished_at) or now) < BACKOFF_S:
            state = "backoff"
        else:
            state = "eligible"
        entries.append(Entry(replay.id, uuid, replay.map_name, state, rank, len(attempts),
                             last.error if last is not None else None))
    return entries


def held(entries: list[Entry]) -> frozenset[int]:
    """The replays whose map control and timing gaps wait for their re-parse. Never a local one: `classify`
    gives those `local` whatever else is true of them."""
    return frozenset(entry.replay_id for entry in entries if entry.state in HELD)
