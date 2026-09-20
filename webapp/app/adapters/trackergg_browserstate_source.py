"""
Adapter that maps tracker.gg's own internal match-detail API response into the
webapp's schema. We never call that API ourselves -- a Playwright page attached
to the user's real, already-authenticated Chrome (via CDP) navigates to a
match's tracker.gg URL like a normal browser tab would, and we simply observe
the JSON response the page's own client-side code requests while rendering.

Companion piece to app/adapters/demo_match_source.py, same target schema.
"""

import json
import math
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from playwright.sync_api import Page
from sqlalchemy.orm import Session

from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerSpend, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.impact import compute_impact_for_match, find_unscored_match_ids
from app.scoring.ingest_preflight import verify_ingest_preflight
from app.services.player_view_cache import find_cached_player_ids_for_match, invalidate_player_cache
from app.services.site_stats_cache import invalidate_site_stats_cache

MATCH_URL_TMPL = "https://tracker.gg/valorant/match/{match_id}"
MATCH_API_MARKER_TMPL = "api.tracker.gg/api/v2/valorant/standard/matches/{match_id}"
HISTORY_URL_TMPL = "https://tracker.gg/valorant/profile/riot/{riot_id}/matches"

# The "All Acts" view of the competitive history, i.e. what the act selector
# produces when "All Acts" is chosen: no `season=` param at all. The default
# /matches URL is implicitly scoped to the CURRENT act, which is why players
# who simply haven't queued this act read as zero (see DiscoveryStatus
# .NO_HISTORY) and why everyone else's history appeared to stop at ~20.
ALL_ACTS_HISTORY_URL_TMPL = (
    "https://tracker.gg/valorant/profile/riot/{riot_id}/matches?platform=pc&playlist=competitive"
)
# Substring identifying the history (not match-detail) responses that the
# "Load More" button fires: .../standard/matches/riot/<riot_id>?...&next=N
HISTORY_API_MARKER = "api.tracker.gg/api/v2/valorant/standard/matches/riot/"
LOAD_MORE_PATTERN = re.compile(r"load\s*more", re.IGNORECASE)
# tracker.gg serves history in fixed pages of 20; used only to bound how many
# clicks could possibly be needed, never to assume a page really held 20.
MATCHES_PER_HISTORY_PAGE = 20
# Guard against an unbounded click loop if tracker.gg ever keeps handing back
# a non-null `next` forever. Hitting this is INCOMPLETE, never success.
MAX_HISTORY_PAGES = 50

MIN_MATCH_DELAY_SECONDS = 5
MAX_MATCH_DELAY_SECONDS = 12


class ProfilePrivateError(RuntimeError):
    """The player has their tracker.gg match history set to private -- no
    match data is exposed for them at all, regardless of how we ask."""


def _fetch_history_state(page: Page, riot_id: str, season_id: str | None = None) -> dict:
    """Loads a player's match-history page (optionally scoped to one
    Episode/Act via `season_id`) and returns its `window.__INITIAL_STATE__`.
    Raises ProfilePrivateError if the profile has opted out of public stats."""
    url = HISTORY_URL_TMPL.format(riot_id=riot_id.replace("#", "%23"))
    if season_id is not None:
        url = f"{url}?platform=pc&playlist=competitive&season={season_id}"
    page.goto(url, wait_until="load", timeout=60_000)
    page.wait_for_timeout(5000)

    state = page.evaluate("() => window.__INITIAL_STATE__ ?? null")
    if state is None:
        raise RuntimeError(f"window.__INITIAL_STATE__ missing on history page for {riot_id!r}")

    _raise_if_private(state, riot_id)
    return state


def _raise_if_private(state: dict, riot_id: str) -> None:
    """Raises ProfilePrivateError if the loaded profile opted out of public
    stats. A private profile is a named, distinguishable outcome -- never a
    zero that reads the same as a player with no recent play."""
    profiles = state.get("stats", {}).get("standardProfiles") or []
    for profile in profiles:
        for error in profile.get("errors") or []:
            if error.get("code") == "CollectorResultStatus::Private":
                raise ProfilePrivateError(f"{riot_id!r}'s tracker.gg match history is private")


def _competitive_matches_from_state(state: dict) -> list[dict]:
    profile_matches = state.get("stats", {}).get("standardProfileMatches") or []
    if not profile_matches:
        return []
    matches = profile_matches[0].get("matches") or []
    return [m for m in matches if m.get("metadata", {}).get("modeName") == "Competitive"]


def discover_recent_match_ids(page: Page, riot_id: str, count: int) -> list[str]:
    """Navigates to a player's public match-history page and returns the
    tracker.gg match IDs (UUIDs) for their `count` most recent Competitive
    matches, most recent first -- other modes (Deathmatch, Swiftplay, etc.)
    are filtered out since the Impact scoring is meant to reflect competitive
    play only. No login required -- these pages are public, unless the
    player has opted their profile to private (see ProfilePrivateError)."""
    state = _fetch_history_state(page, riot_id)
    competitive = _competitive_matches_from_state(state)
    return [m["attributes"]["id"] for m in competitive[:count]]


class IngestLedger:
    """Append-only record of the matches one run actually added, so the whole
    run can be backed out if it turns out to be wrong.

    Written as JSONL and flushed per match, deliberately: a run killed or
    crashed halfway still leaves a complete, truthful record of everything
    that made it into the database. A ledger assembled in memory and dumped at
    the end would be empty in exactly the case it is most needed.

    A line is appended the moment `load_match`'s commit makes a match
    permanent -- before impact scoring -- because that commit is the point
    from which dedup will skip the match forever. Anything recorded here is in
    the database; scoring state is recoverable from the database itself.
    """

    def __init__(self, path, run_label: str = ""):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.count = 0
        self._write({
            "event": "run_start",
            "at": datetime.now().astimezone().isoformat(),
            "label": run_label,
        })

    def _write(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
            fh.flush()

    def record_match(self, match: Match, riot_id: str) -> None:
        self.count += 1
        self._write({
            "event": "ingested",
            "at": datetime.now().astimezone().isoformat(),
            "discovered_via": riot_id,
            "match_id": match.id,
            "external_id": match.external_id,
            "map_name": match.map_name,
            "played_at": match.played_at.isoformat() if match.played_at else None,
        })

    def record_end(self, note: str = "") -> None:
        self._write({
            "event": "run_end",
            "at": datetime.now().astimezone().isoformat(),
            "ingested": self.count,
            "note": note,
        })


class DiscoveryStatus(str, Enum):
    """Why a discovery run stopped. Only COMPLETE and EXHAUSTED are outcomes
    where the number returned is the number that exists; everything else means
    the count is a floor, not an answer, and must not print as plain success."""

    COMPLETE = "COMPLETE"        # reached the requested count
    EXHAUSTED = "EXHAUSTED"      # fewer than requested, but tracker.gg says that's all there is
    NO_HISTORY = "NO_HISTORY"    # zero competitive matches exposed under ANY act
    PRIVATE = "PRIVATE"          # profile opted out of public stats
    INCOMPLETE = "INCOMPLETE"    # stopped for any other reason -- the count is a floor


@dataclass(frozen=True)
class DiscoveryResult:
    """What a discovery run reached, against what it was asked for.

    The whole point of this type: `len(match_ids)` alone cannot distinguish
    "they have 20 matches" from "we asked for 100 and the cap gave us 20".
    Callers print `summary()`, never a bare count.
    """

    riot_id: str
    requested: int
    match_ids: list[str] = field(default_factory=list)
    status: DiscoveryStatus = DiscoveryStatus.INCOMPLETE
    reason: str = ""
    pages_fetched: int = 0

    @property
    def reached(self) -> int:
        return len(self.match_ids)

    @property
    def is_conclusive(self) -> bool:
        """True when `reached` is the real number tracker.gg exposes, rather
        than a floor we stopped at. NO_HISTORY and PRIVATE count as conclusive
        -- zero really is everything available for those players -- but they
        still carry their own status, so neither can be mistaken for a run
        that simply found nothing new."""
        return self.status in (
            DiscoveryStatus.COMPLETE,
            DiscoveryStatus.EXHAUSTED,
            DiscoveryStatus.NO_HISTORY,
            DiscoveryStatus.PRIVATE,
        )

    def summary(self) -> str:
        return (
            f"{self.riot_id}: {self.reached}/{self.requested} reached "
            f"[{self.status.value}] ({self.reason}; {self.pages_fetched} page(s))"
        )


def _competitive_from_payload(data: dict) -> list[dict]:
    """Competitive matches out of one history API `data` object. tracker.gg
    already filters by `type=competitive`, but the mode is re-checked here so
    a change in that query param can never silently admit Deathmatch rows."""
    matches = data.get("matches") or []
    return [m for m in matches if (m.get("metadata") or {}).get("modeName") == "Competitive"]


def _next_cursor(data: dict) -> int | None:
    """tracker.gg's page cursor: `metadata.next`, a plain 1-based page index.
    None/absent means this was the last page -- the authoritative
    end-of-history signal, matching the DOM dropping the Load More button."""
    return (data.get("metadata") or {}).get("next")


def discover_match_ids_paginated(
    page: Page,
    riot_id: str,
    count: int,
    max_pages: int = MAX_HISTORY_PAGES,
) -> DiscoveryResult:
    """Reaches up to `count` Competitive match IDs by driving tracker.gg's own
    "Load More" control across the All-Acts history view, most recent first.

    Unlike `discover_recent_match_ids` (one server-rendered batch, current act
    only, hard-capped at the ~20 tracker.gg puts in `__INITIAL_STATE__`), this
    accumulates from the XHR each click fires.

    That distinction is load-bearing, and was measured rather than assumed:
    clicking Load More REPLACES `stats.standardProfileMatches[0].matches` in
    `window.__INITIAL_STATE__` with the newly fetched page instead of appending
    to it. So `__INITIAL_STATE__` stays exactly 20 entries long forever while
    its contents change on every click -- a loop that re-read it and checked
    "did this change?" would pass that check every single time, report success,
    and silently discard every page but the last. Page 0 is read from
    `__INITIAL_STATE__` (correct, and it is what the server rendered); every
    page after it comes from the captured response body, and `__INITIAL_STATE__`
    is never consulted again.

    Returns a DiscoveryResult carrying reached-vs-requested and a named stop
    reason on every path -- a run that could not reach `count` is never
    reported as plain success. Paces itself between clicks exactly like
    `_dedup_and_ingest` does between match fetches: a history page load is a
    request like any other."""
    if count <= 0:
        return DiscoveryResult(
            riot_id=riot_id,
            requested=count,
            status=DiscoveryStatus.COMPLETE,
            reason="nothing requested",
        )

    captured: list[dict] = []

    def on_response(response):
        if HISTORY_API_MARKER in response.url:
            try:
                body = response.json()
            except Exception:
                return
            data = (body or {}).get("data")
            if isinstance(data, dict):
                captured.append(data)

    page.on("response", on_response)
    try:
        url = ALL_ACTS_HISTORY_URL_TMPL.format(riot_id=riot_id.replace("#", "%23"))
        page.goto(url, wait_until="load", timeout=60_000)
        page.wait_for_timeout(5000)

        state = page.evaluate("() => window.__INITIAL_STATE__ ?? null")
        if state is None:
            return DiscoveryResult(
                riot_id=riot_id,
                requested=count,
                status=DiscoveryStatus.INCOMPLETE,
                reason="window.__INITIAL_STATE__ missing on history page",
            )
        _raise_if_private(state, riot_id)

        containers = state.get("stats", {}).get("standardProfileMatches") or []
        if not containers:
            return DiscoveryResult(
                riot_id=riot_id,
                requested=count,
                status=DiscoveryStatus.NO_HISTORY,
                reason="no match container on the All-Acts history page",
                pages_fetched=1,
            )

        first_page = containers[0]
        match_ids: list[str] = []
        seen: set[str] = set()

        def absorb(data: dict) -> int:
            """Adds this page's unseen Competitive IDs; returns how many were new."""
            fresh = 0
            for m in _competitive_from_payload(data):
                match_id = (m.get("attributes") or {}).get("id")
                if match_id and match_id not in seen:
                    seen.add(match_id)
                    match_ids.append(match_id)
                    fresh += 1
            return fresh

        absorb(first_page)
        cursor = _next_cursor(first_page)
        pages = 1

        if not match_ids and cursor is None:
            return DiscoveryResult(
                riot_id=riot_id,
                requested=count,
                status=DiscoveryStatus.NO_HISTORY,
                reason="zero Competitive matches under any act",
                pages_fetched=pages,
            )

        def finish(status: DiscoveryStatus, reason: str) -> DiscoveryResult:
            return DiscoveryResult(
                riot_id=riot_id,
                requested=count,
                match_ids=match_ids[:count],
                status=status,
                reason=reason,
                pages_fetched=pages,
            )

        load_more = page.get_by_role("button", name=LOAD_MORE_PATTERN)
        while len(match_ids) < count:
            if cursor is None:
                return finish(DiscoveryStatus.EXHAUSTED, "metadata.next is null -- end of history")
            if pages >= max_pages:
                return finish(
                    DiscoveryStatus.INCOMPLETE,
                    f"hit the {max_pages}-page safety cap with more history left",
                )
            try:
                clickable = (
                    load_more.count() > 0
                    and load_more.first.is_visible()
                    and load_more.first.is_enabled()
                )
            except Exception as e:
                return finish(DiscoveryStatus.INCOMPLETE, f"Load More control unreadable: {e}")
            if not clickable:
                # The DOM agrees with metadata.next in every case observed, so
                # a missing button with a non-null cursor is a real anomaly --
                # not exhaustion, and not something to report as success.
                return finish(
                    DiscoveryStatus.INCOMPLETE,
                    f"no usable Load More control although metadata.next={cursor}",
                )

            before = len(captured)
            try:
                load_more.first.scroll_into_view_if_needed()
                load_more.first.click()
            except Exception as e:
                return finish(DiscoveryStatus.INCOMPLETE, f"Load More click failed: {e}")
            page.wait_for_timeout(8000)

            new_payloads = captured[before:]
            if not new_payloads:
                return finish(
                    DiscoveryStatus.INCOMPLETE,
                    f"Load More (next={cursor}) fired no history request",
                )

            fresh_total = sum(absorb(d) for d in new_payloads)
            pages += 1
            advanced = [_next_cursor(d) for d in new_payloads]
            new_cursor = advanced[-1]

            if fresh_total == 0:
                # Every ID came back already-seen: the cursor did not really
                # advance. Stopping here is mandatory -- looping would spin
                # forever collecting nothing while looking busy.
                return finish(
                    DiscoveryStatus.INCOMPLETE,
                    f"page {pages} returned no new matches (cursor {cursor} did not advance)",
                )
            if new_cursor is not None and new_cursor == cursor:
                return finish(
                    DiscoveryStatus.INCOMPLETE,
                    f"metadata.next repeated at {cursor} -- cursor stuck",
                )
            cursor = new_cursor

            if len(match_ids) < count and cursor is not None:
                delay = random.uniform(MIN_MATCH_DELAY_SECONDS, MAX_MATCH_DELAY_SECONDS)
                time.sleep(delay)

        return finish(DiscoveryStatus.COMPLETE, "reached the requested count")
    except ProfilePrivateError as e:
        return DiscoveryResult(
            riot_id=riot_id,
            requested=count,
            status=DiscoveryStatus.PRIVATE,
            reason=str(e),
        )
    finally:
        page.remove_listener("response", on_response)


def discover_all_season_ids(page: Page, riot_id: str) -> list[dict]:
    """Returns every Episode/Act tracker.gg knows about, most recent first.
    This reference list (`valorantDb.typeLists.seasons`) is static game
    metadata embedded in every match-history page load -- not per-player --
    but still requires a page load to populate `window.__INITIAL_STATE__`
    (it's empty on a fresh, never-navigated Playwright page), so this takes
    a `riot_id` just to have somewhere to load. Each entry is
    {"id": <act UUID, used as the `season=` query param>, "label": "E1:A1",
    "start_time": <ISO8601>}."""
    _fetch_history_state(page, riot_id)
    seasons = page.evaluate(
        "() => window.__INITIAL_STATE__?.valorantDb?.typeLists?.seasons ?? []"
    )
    acts = [
        {
            "id": act["id"],
            "label": f"{episode['shortName']}:{act['shortName']}",
            "start_time": act["startTime"],
        }
        for episode in seasons
        for act in episode.get("seasons", [])
    ]
    acts.sort(key=lambda a: a["start_time"], reverse=True)
    return acts


def discover_all_competitive_match_ids(
    page: Page,
    riot_id: str,
    max_matches: int | None = None,
    max_acts: int | None = None,
    stop_after_empty_acts: int = 3,
) -> list[str]:
    """Like `discover_recent_match_ids`, but pages back through every
    Episode/Act tracker.gg has on file (most recent first) instead of just
    the current one, to recover a player's full Competitive history rather
    than the ~20 most recent matches. Stops early once `max_matches` is hit,
    once `max_acts` acts have been checked, or after `stop_after_empty_acts`
    consecutive acts with zero Competitive matches (the player likely didn't
    exist/queue competitive that far back) -- whichever comes first.

    Paces itself the same as `ingest_recent_matches` between act requests,
    since each act is a full page navigation just like fetching a match."""
    all_ids: list[str] = []
    seen_ids: set[str] = set()
    empty_streak = 0

    acts = discover_all_season_ids(page, riot_id)
    if max_acts is not None:
        acts = acts[:max_acts]

    for i, act in enumerate(acts):
        state = _fetch_history_state(page, riot_id, season_id=act["id"])
        competitive = _competitive_matches_from_state(state)
        new_ids = [
            m["attributes"]["id"] for m in competitive if m["attributes"]["id"] not in seen_ids
        ]
        print(f"  [{act['label']}] {len(new_ids)} new Competitive match(es)")

        if new_ids:
            empty_streak = 0
            seen_ids.update(new_ids)
            all_ids.extend(new_ids)
            if max_matches is not None and len(all_ids) >= max_matches:
                return all_ids[:max_matches]
        else:
            empty_streak += 1
            if empty_streak >= stop_after_empty_acts:
                break

        if i < len(acts) - 1:
            delay = random.uniform(MIN_MATCH_DELAY_SECONDS, MAX_MATCH_DELAY_SECONDS)
            time.sleep(delay)

    return all_ids


def fetch_match_json(page: Page, match_id: str) -> dict:
    """Navigates to a match's tracker.gg page and returns the `data` object
    from tracker.gg's internal match-detail API response fired by that page
    load (round-by-round kills, damage, loadout, and outcome segments)."""
    marker = MATCH_API_MARKER_TMPL.format(match_id=match_id)
    captured: dict = {}

    def on_response(response):
        if marker in response.url and "body" not in captured:
            captured["body"] = response.json()

    page.on("response", on_response)
    try:
        page.goto(MATCH_URL_TMPL.format(match_id=match_id), wait_until="load", timeout=60_000)
        page.wait_for_timeout(8000)
    finally:
        page.remove_listener("response", on_response)

    if "body" not in captured:
        raise RuntimeError(f"Did not observe tracker.gg's match API response for {match_id}")
    return captured["body"]["data"]


def _get_or_create_player(db: Session, display_name: str) -> Player:
    player = db.query(Player).filter_by(display_name=display_name).one_or_none()
    if player is None:
        player = Player(display_name=display_name)
        db.add(player)
        db.flush()
    return player


def _team_for(team_id: str) -> Team:
    return Team.TEAM_1 if team_id == "Red" else Team.TEAM_2


# tracker.gg's roundResult value for the placeholder rounds it appends to a
# surrendered match. Filtered out at ingest -- see the round loop in load_match.
SURRENDERED_ROUND_RESULT = "Surrendered"


def _outcome_string(winning_team_id: str, round_result: str) -> str:
    # app/scoring/impact.py's _did_team_win parses "Team A"/"Team B" out of
    # this string (a convention inherited from the original HTML scraper) --
    # keep producing that shape rather than touching the shared scoring code.
    letter = "A" if winning_team_id == "Red" else "B"
    return f"Team {letter} {round_result} Win"


# Kill distance. VERIFIED 2026-09-08 against 8 captured matches / 1,279 kills
# (scripts/capture_trackergg_state.py, plus fetch_match_json directly).
#
# There is NO distance field. tracker.gg returns COORDINATES, and the distance
# has to be computed:
#   metadata.opponentLocation   -> {x, y} for the VICTIM at the kill
#   metadata.playerLocations    -> [{platformUserIdentifier, location{x,y},
#                                    viewRadians}] including the KILLER
# Both were present on 98.69% of kills; the rest yield no distance.
#
# UNITS ARE UNREAL UNITS (~centimetres), NOT metres -- raw / 100 = metres.
# The weapon breakdown is what settles it, and it is unambiguous:
#   Operator/Outlaw (snipers)  median 2798-2801 uu -> 28.0 m
#   Guardian                   median      2081 uu -> 20.8 m
#   Vandal/Phantom (rifles)    median 1604-1627 uu -> 16.0-16.3 m
#   Classic/Spectre/Bulldog    median 1118-1189 uu -> 11.2-11.9 m
#   max over all maps               5335 uu -> 53.4 m
# Read as metres the raw numbers would put a median Vandal kill at 1.6 km; read
# as centimetres every weapon lands where its effective range says it should,
# and the 53 m maximum matches a long Valorant sightline.
#
# Known limitation: the coordinates are planar (x, y only, no elevation), so on
# vertical maps two players stacked above each other measure as adjacent.
UNREAL_UNITS_PER_METRE = 100.0


def _pickup_distance_meta(meta: dict, killer_identifier: str | None) -> dict:
    """Distance between killer and victim at the kill, in metres.

    Returns {} when either location is absent, which is what the ~1.3% of
    kills without both endpoints must produce -- pickup_bonus then sees no
    distance and abstains.
    """
    victim_location = meta.get("opponentLocation")
    if not (isinstance(victim_location, dict) and killer_identifier):
        return {}
    killer_location = next(
        (
            entry.get("location")
            for entry in (meta.get("playerLocations") or [])
            if entry.get("platformUserIdentifier") == killer_identifier
        ),
        None,
    )
    if not isinstance(killer_location, dict):
        return {}
    try:
        dx = float(killer_location["x"]) - float(victim_location["x"])
        dy = float(killer_location["y"]) - float(victim_location["y"])
    except (KeyError, TypeError, ValueError):
        return {}
    raw = math.hypot(dx, dy)
    return {
        "kill_distance_raw": raw,
        "kill_distance_m": raw / UNREAL_UNITS_PER_METRE,
    }


def load_match(db: Session, match_json: dict) -> Match:
    external_id = match_json["attributes"]["id"]
    existing = db.query(Match).filter_by(external_id=external_id).one_or_none()
    if existing is not None:
        return existing

    metadata = match_json["metadata"]
    segments = match_json["segments"]

    team_summaries = {s["attributes"]["teamId"]: s for s in segments if s["type"] == "team-summary"}
    player_summaries = [s for s in segments if s["type"] == "player-summary"]
    round_summaries = sorted(
        (s for s in segments if s["type"] == "round-summary"),
        key=lambda s: s["attributes"]["round"],
    )
    player_rounds = [s for s in segments if s["type"] == "player-round"]
    player_round_kills = [s for s in segments if s["type"] == "player-round-kills"]

    team1_rounds_won = team_summaries.get("Red", {}).get("stats", {}).get("roundsWon", {}).get("value", 0)
    team2_rounds_won = team_summaries.get("Blue", {}).get("stats", {}).get("roundsWon", {}).get("value", 0)

    played_at = None
    if metadata.get("dateStarted"):
        played_at = datetime.fromisoformat(metadata["dateStarted"])

    match = Match(
        external_id=external_id,
        source=MatchSource.SCRAPED,
        map_name=metadata.get("mapName"),
        played_at=played_at,
        team1_rounds_won=team1_rounds_won,
        team2_rounds_won=team2_rounds_won,
    )
    db.add(match)
    db.flush()

    match_players: dict[str, MatchPlayer] = {}
    for ps in player_summaries:
        identifier = ps["attributes"]["platformUserIdentifier"]
        player = _get_or_create_player(db, identifier)
        match_player = MatchPlayer(
            match_id=match.id,
            player_id=player.id,
            agent=ps["metadata"].get("agentName", "Unknown"),
            team=_team_for(ps["metadata"]["teamId"]),
        )
        db.add(match_player)
        match_players[identifier] = match_player
    db.flush()

    rounds_by_number: dict[int, Round] = {}
    for rs in round_summaries:
        # tracker.gg pads a surrendered match out to its notional length with
        # placeholder rounds: no kill events, and every player's stats row is
        # all zeros. Nobody played them, so ingesting them silently inflates
        # every "per round played" denominator (Most Active %, the Scavenger
        # credits-per-round threshold) and dilutes average Impact with zeros.
        # Skip them here -- the player-stat and kill-event loops below already
        # skip anything whose round is missing from rounds_by_number.
        if rs["stats"]["roundResult"]["value"] == SURRENDERED_ROUND_RESULT:
            continue

        round_number = rs["attributes"]["round"]
        plant = rs["metadata"].get("plant")
        exploded = rs["metadata"].get("exploded")
        defuse = rs["metadata"].get("defuse")

        db_round = Round(
            match_id=match.id,
            round_number=round_number,
            outcome=_outcome_string(rs["stats"]["winningTeam"]["value"], rs["stats"]["roundResult"]["value"]),
            planted=plant is not None,
            plant_time=(plant["roundTime"] / 1000.0) if plant else None,
            exploded=exploded is not None,
            defused=defuse is not None,
            defuse_time=(defuse["roundTime"] / 1000.0) if defuse else None,
        )
        db.add(db_round)
        db.flush()
        rounds_by_number[round_number] = db_round

    # tracker.gg's spentCredits goes to its own table once the stat rows have ids.
    # Absent means unknown: no row is written, never a 0 (spec 2026-09-12 section 10).
    pending_spend: list[tuple[RoundPlayerStat, int]] = []
    for pr in player_rounds:
        match_player = match_players.get(pr["attributes"]["platformUserIdentifier"])
        round_row = rounds_by_number.get(pr["attributes"]["round"])
        if match_player is None or round_row is None:
            continue
        stats = pr["stats"]
        stat_row = RoundPlayerStat(
            round_id=round_row.id,
            match_player_id=match_player.id,
            score=stats["score"]["value"],
            kills=stats["kills"]["value"],
            deaths=stats["deaths"]["value"],
            assists=stats["assists"]["value"],
            loadout=stats["loadoutValue"]["value"],
            remaining=stats["remainingCredits"]["value"],
        )
        db.add(stat_row)
        spent = (stats.get("spentCredits") or {}).get("value")
        if (isinstance(spent, (int, float)) and not isinstance(spent, bool) and math.isfinite(spent)
                and spent >= 0):
            pending_spend.append((stat_row, int(spent)))

    if pending_spend:
        db.flush()
        for stat_row, spent in pending_spend:
            db.add(RoundPlayerSpend(round_player_stat_id=stat_row.id, spent=spent))

    for prk in player_round_kills:
        round_row = rounds_by_number.get(prk["attributes"]["round"])
        if round_row is None:
            continue
        killer = match_players.get(prk["attributes"]["platformUserIdentifier"])
        victim = match_players.get(prk["attributes"]["opponentPlatformUserIdentifier"])
        meta = prk["metadata"]
        db.add(
            KillEvent(
                round_id=round_row.id,
                killer_match_player_id=killer.id if killer else None,
                death_match_player_id=victim.id if victim else None,
                weapon=meta.get("weaponName") or "Unknown",
                event_time_seconds=meta["roundTime"] / 1000.0,
                source_meta={
                    "assistants": [
                        a["platformUserIdentifier"] for a in (meta.get("assistants") or [])
                    ],
                    **_pickup_distance_meta(
                        meta, prk["attributes"].get("platformUserIdentifier")
                    ),
                },
            )
        )

    db.commit()
    return match


def _dedup_and_ingest(
    db: Session,
    page: Page,
    match_ids: list[str],
    ledger: "IngestLedger | None" = None,
    riot_id: str = "",
) -> set[int]:
    """Shared tail end of both ingestion entry points below: skip anything
    already in the DB (dedup by tracker.gg's own match ID, so the same match
    is never double-ingested even when reached via a different player's
    history), then ingest+score the rest with a human pace between requests.

    Returns the union of player IDs whose player_view_cache rows were
    invalidated -- callers batch a deferred pre-warm over this set rather than
    recomputing per match (a player appearing in N ingested matches would
    otherwise be recomputed N times).

    Raises whatever the ingest loop raised, preserving the behaviour its
    pre-existing callers (snowball_1hour.py, the map-diversity crawl) already
    handle. Callers that need the partial progress behind a failure use
    `_ingest_discovered` directly."""
    outcome = _ingest_discovered(db, page, match_ids, ledger, riot_id)
    if outcome.error is not None:
        raise outcome.error
    return outcome.dirty


@dataclass
class _IngestProgress:
    """What an ingest loop actually managed to do, including when it died
    partway. `dirty` and `ingested` describe work that is already committed
    and is valid regardless of `error`."""

    dirty: set[int] = field(default_factory=set)
    ingested: int = 0
    already_present: int = 0
    attempted: int = 0
    error: BaseException | None = None


def _ingest_discovered(
    db: Session,
    page: Page,
    match_ids: list[str],
    ledger: "IngestLedger | None" = None,
    riot_id: str = "",
) -> _IngestProgress:
    """The ingest loop, reporting partial progress instead of losing it.

    A failure partway through leaves every match committed before it fully
    ingested and scored; that work is real and must not be reported as
    nothing. The 2026-09-19 roster run made the cost concrete: one dropped
    connection 27 minutes into a player's ingest printed `0/200` for a player
    whose discovery had reached 200 and whose ingest had already added 28."""
    # Before the first load_match commit, not after it: a checkout that cannot
    # score must not leave a committed, unscored match behind (match 3133).
    verify_ingest_preflight(db)

    progress = _IngestProgress()

    new_ids = []
    for match_id in match_ids:
        if db.query(Match).filter_by(external_id=match_id).one_or_none() is not None:
            print(f"  {match_id}: already ingested, skipping")
            progress.already_present += 1
        else:
            new_ids.append(match_id)
    progress.attempted = len(new_ids)

    dirty: set[int] = progress.dirty
    for i, match_id in enumerate(new_ids):
        print(f"[{i + 1}/{len(new_ids)}] capturing {match_id}")
        try:
            match_json = fetch_match_json(page, match_id)
            match = load_match(db, match_json)        # commits internally
        except Exception as e:
            # Stop here, but keep everything already committed. The caller
            # decides whether a partial ingest is acceptable; it is never
            # silently rewritten to zero.
            print(f"  FAILED on {match_id}: {type(e).__name__}: {e}")
            progress.error = e
            return progress

        # Recorded here, immediately after the commit that makes this match
        # permanent and dedup-skipped forever -- not at the end of the run.
        # Whatever is in the ledger is in the database, even if the run dies
        # on the very next line.
        if ledger is not None:
            ledger.record_match(match, riot_id)

        # Invalidate BETWEEN load_match's commit and impact scoring, not after
        # both: load_match's commit makes the match row permanent and
        # dedup-skipped on every future run, so if compute_impact_for_match
        # then raises, invalidating first still leaves the cache empty
        # (recomputes live on next visit) rather than permanently stale.
        cached_ids = find_cached_player_ids_for_match(db, match.id)
        if cached_ids:
            invalidate_player_cache(db, cached_ids)    # DELETE, no commit
            dirty |= cached_ids
        # Unlike the per-player cache above, the "All Players" site-stats cache
        # covers EVERY match in the DB, so it's invalidated on every new match
        # regardless of whether cached_ids is empty.
        invalidate_site_stats_cache(db)                # DELETE, no commit
        db.commit()                                    # invalidation visible

        try:
            compute_impact_for_match(db, match.id)     # commits internally
        except Exception as e:
            # The match itself is committed and will be dedup-skipped forever,
            # so it counts as ingested; backfill_unscored_matches picks the
            # scoring up on the next run (this is the match-3133 shape).
            progress.ingested += 1
            print(f"  SCORING FAILED for {match_id}: {type(e).__name__}: {e}")
            progress.error = e
            return progress
        progress.ingested += 1
        print(f"  ingested {match.map_name} ({match_id})")

        if i < len(new_ids) - 1:
            delay = random.uniform(MIN_MATCH_DELAY_SECONDS, MAX_MATCH_DELAY_SECONDS)
            print(f"  waiting {delay:.1f}s before next match...")
            time.sleep(delay)

    return progress


def backfill_unscored_matches(db: Session) -> set[int]:
    """Finds any match that fully committed (has MatchPlayer rows) but never
    got ImpactScore rows -- the signature of an ingest run that got killed or
    crashed between load_match's commit and compute_impact_for_match's own
    commit for one match (see the comment in _dedup_and_ingest) -- and scores
    it now. compute_impact_for_match is idempotent, so this is safe to call
    every run regardless of whether anything is actually stranded.

    Callers should run this before their own ingestion so a stranded match
    from a previous interrupted run gets caught before it causes a session-
    or match-page 500 (this was a real production incident: a match found
    this way is otherwise invisible until a user hits it). Returns the union
    of player IDs whose cache rows were invalidated, same contract as
    _dedup_and_ingest, so callers can fold it into the same pre-warm batch.
    """
    # This function's first act used to be committing cache deletions, which a
    # checkout that could not then score left deleted. Refuse first.
    verify_ingest_preflight(db)

    unscored_match_ids = find_unscored_match_ids(db)
    dirty: set[int] = set()
    for match_id in unscored_match_ids:
        match = db.query(Match).filter_by(id=match_id).one()
        print(f"found stranded unscored match {match.external_id} ({match.map_name}), backfilling...")

        cached_ids = find_cached_player_ids_for_match(db, match_id)
        if cached_ids:
            invalidate_player_cache(db, cached_ids)
            dirty |= cached_ids
        invalidate_site_stats_cache(db)
        db.commit()

        compute_impact_for_match(db, match_id)
        print(f"  backfilled {match.external_id}")

    return dirty


def ingest_recent_matches(db: Session, page: Page, riot_id: str, count: int) -> set[int]:
    """Discovers a player's `count` most recent matches (current act only)
    and ingests+scores the ones not already in the DB. Returns the player IDs
    whose cache rows were invalidated (see _dedup_and_ingest)."""
    try:
        match_ids = discover_recent_match_ids(page, riot_id, count)
    except ProfilePrivateError as e:
        print(f"skipping {riot_id}: {e}")
        return set()
    print(f"discovered {len(match_ids)} recent match(es) for {riot_id}")
    return _dedup_and_ingest(db, page, match_ids)


@dataclass(frozen=True)
class IngestOutcome:
    """Discovery and ingestion reported separately, because they fail
    separately.

    Collapsing the two is what produced the 2026-09-19 misreport: a player
    whose discovery reached 200/200 and whose ingest added 28 matches before a
    dropped connection was printed as `0/200`, because the failure replaced
    the discovery result instead of sitting beside it. Discovery answers "how
    much of their history did we find"; ingestion answers "how much of it did
    we get into the database". Both are always reported."""

    discovery: DiscoveryResult
    ingested: int = 0
    already_present: int = 0
    attempted: int = 0
    error: str = ""

    @property
    def riot_id(self) -> str:
        return self.discovery.riot_id

    @property
    def ok(self) -> bool:
        """Discovery was conclusive AND ingestion ran to completion."""
        return not self.error and self.discovery.is_conclusive

    def summary(self) -> str:
        head = (
            f"{self.riot_id}: discovered {self.discovery.reached}/"
            f"{self.discovery.requested} [{self.discovery.status.value}]"
        )
        if self.attempted or self.ingested:
            head += f" | ingested {self.ingested}/{self.attempted} new"
        if self.already_present:
            head += f" ({self.already_present} already held)"
        if self.error:
            head += f" | INGEST FAILED: {self.error}"
        return head


def ingest_paginated_history(
    db: Session,
    page: Page,
    riot_id: str,
    count: int,
    ledger: "IngestLedger | None" = None,
) -> tuple[set[int], IngestOutcome]:
    """Like `ingest_recent_matches`, but discovers via the paginated All-Acts
    path so `count` above ~20 actually reaches that many, and hands a full
    IngestOutcome back to the caller alongside the invalidated player IDs.

    Returning the outcome rather than just a count is the point: the caller has
    to be able to say "200 requested, 200 discovered, 28 of 172 ingested before
    the connection dropped" instead of any single number that looks like
    success -- or, worse, like nothing happened at all.

    Neither discovery nor ingest failures are raised here: a batch over a
    roster keeps going past one bad profile or one dropped connection, and
    what did succeed is preserved and reported."""
    result = discover_match_ids_paginated(page, riot_id, count)
    print(f"  discovery: {result.summary()}")
    if not result.match_ids:
        return set(), IngestOutcome(discovery=result)

    progress = _ingest_discovered(db, page, result.match_ids, ledger, riot_id)
    outcome = IngestOutcome(
        discovery=result,
        ingested=progress.ingested,
        already_present=progress.already_present,
        attempted=progress.attempted,
        error=(
            f"{type(progress.error).__name__}: {progress.error}"
            if progress.error is not None
            else ""
        ),
    )
    print(f"  {outcome.summary()}")
    return progress.dirty, outcome


def ingest_full_history(
    db: Session,
    page: Page,
    riot_id: str,
    max_matches: int | None = None,
    max_acts: int | None = None,
) -> set[int]:
    """Like `ingest_recent_matches`, but pages back through every Episode/Act
    (via `discover_all_competitive_match_ids`) to pull as much of a player's
    Competitive history as tracker.gg exposes, rather than just the current
    act. Used by the map-diversity data crawl (Phase B) -- deep per-player
    history is what lets us reconstruct each player's map-history state
    before any given match. Returns the invalidated player IDs, same as
    ingest_recent_matches (scripts/crawl_map_diversity_data.py intentionally
    ignores this return value -- pre-warming mid-crawl would be wasted work;
    run scripts/recompute_player_views.py after a crawl instead)."""
    try:
        match_ids = discover_all_competitive_match_ids(
            page, riot_id, max_matches=max_matches, max_acts=max_acts
        )
    except ProfilePrivateError as e:
        print(f"skipping {riot_id}: {e}")
        return set()
    print(f"discovered {len(match_ids)} total Competitive match(es) for {riot_id}")
    return _dedup_and_ingest(db, page, match_ids)
