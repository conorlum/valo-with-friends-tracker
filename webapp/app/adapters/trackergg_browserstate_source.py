"""
Adapter that maps tracker.gg's own internal match-detail API response into the
webapp's schema. We never call that API ourselves -- a Playwright page attached
to the user's real, already-authenticated Chrome (via CDP) navigates to a
match's tracker.gg URL like a normal browser tab would, and we simply observe
the JSON response the page's own client-side code requests while rendering.

Companion piece to app/adapters/demo_match_source.py, same target schema.
"""

import random
import time
from datetime import datetime

from playwright.sync_api import Page
from sqlalchemy.orm import Session

from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.impact import compute_impact_for_match

MATCH_URL_TMPL = "https://tracker.gg/valorant/match/{match_id}"
MATCH_API_MARKER_TMPL = "api.tracker.gg/api/v2/valorant/standard/matches/{match_id}"
HISTORY_URL_TMPL = "https://tracker.gg/valorant/profile/riot/{riot_id}/matches"

MIN_MATCH_DELAY_SECONDS = 5
MAX_MATCH_DELAY_SECONDS = 12


class ProfilePrivateError(RuntimeError):
    """The player has their tracker.gg match history set to private -- no
    match data is exposed for them at all, regardless of how we ask."""


def discover_recent_match_ids(page: Page, riot_id: str, count: int) -> list[str]:
    """Navigates to a player's public match-history page and returns the
    tracker.gg match IDs (UUIDs) for their `count` most recent Competitive
    matches, most recent first -- other modes (Deathmatch, Swiftplay, etc.)
    are filtered out since the Impact scoring is meant to reflect competitive
    play only. No login required -- these pages are public, unless the
    player has opted their profile to private (see ProfilePrivateError)."""
    url = HISTORY_URL_TMPL.format(riot_id=riot_id.replace("#", "%23"))
    page.goto(url, wait_until="load", timeout=60_000)
    page.wait_for_timeout(5000)

    state = page.evaluate("() => window.__INITIAL_STATE__ ?? null")
    if state is None:
        raise RuntimeError(f"window.__INITIAL_STATE__ missing on history page for {riot_id!r}")

    profiles = state.get("stats", {}).get("standardProfiles") or []
    for profile in profiles:
        for error in profile.get("errors") or []:
            if error.get("code") == "CollectorResultStatus::Private":
                raise ProfilePrivateError(f"{riot_id!r}'s tracker.gg match history is private")

    profile_matches = state.get("stats", {}).get("standardProfileMatches") or []
    if not profile_matches:
        return []

    matches = profile_matches[0].get("matches") or []
    competitive = [m for m in matches if m.get("metadata", {}).get("modeName") == "Competitive"]
    return [m["attributes"]["id"] for m in competitive[:count]]


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


def _outcome_string(winning_team_id: str, round_result: str) -> str:
    # app/scoring/impact.py's _did_team_win parses "Team A"/"Team B" out of
    # this string (a convention inherited from the original HTML scraper) --
    # keep producing that shape rather than touching the shared scoring code.
    letter = "A" if winning_team_id == "Red" else "B"
    return f"Team {letter} {round_result} Win"


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

    for pr in player_rounds:
        match_player = match_players.get(pr["attributes"]["platformUserIdentifier"])
        round_row = rounds_by_number.get(pr["attributes"]["round"])
        if match_player is None or round_row is None:
            continue
        stats = pr["stats"]
        db.add(
            RoundPlayerStat(
                round_id=round_row.id,
                match_player_id=match_player.id,
                score=stats["score"]["value"],
                kills=stats["kills"]["value"],
                deaths=stats["deaths"]["value"],
                assists=stats["assists"]["value"],
                loadout=stats["loadoutValue"]["value"],
                remaining=stats["remainingCredits"]["value"],
            )
        )

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
                    ]
                },
            )
        )

    db.commit()
    return match


def ingest_recent_matches(db: Session, page: Page, riot_id: str, count: int) -> None:
    """Discovers a player's `count` most recent matches, skips any already in
    the DB (dedup by tracker.gg's own match ID, so the same match is never
    double-ingested even when reached via a different player's history), and
    ingests+scores the rest with a human pace between requests."""
    try:
        match_ids = discover_recent_match_ids(page, riot_id, count)
    except ProfilePrivateError as e:
        print(f"skipping {riot_id}: {e}")
        return
    print(f"discovered {len(match_ids)} recent match(es) for {riot_id}")

    new_ids = []
    for match_id in match_ids:
        if db.query(Match).filter_by(external_id=match_id).one_or_none() is not None:
            print(f"  {match_id}: already ingested, skipping")
        else:
            new_ids.append(match_id)

    for i, match_id in enumerate(new_ids):
        print(f"[{i + 1}/{len(new_ids)}] capturing {match_id}")
        match_json = fetch_match_json(page, match_id)
        match = load_match(db, match_json)
        compute_impact_for_match(db, match.id)
        print(f"  ingested {match.map_name} ({match_id})")

        if i < len(new_ids) - 1:
            delay = random.uniform(MIN_MATCH_DELAY_SECONDS, MAX_MATCH_DELAY_SECONDS)
            print(f"  waiting {delay:.1f}s before next match...")
            time.sleep(delay)
