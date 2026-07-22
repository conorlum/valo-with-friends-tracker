import re
import sys
from datetime import datetime
from itertools import product
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import ImpactScore, KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import matchDataPipeline  # noqa: E402

_MAP_NAME_RE = re.compile(r"^[A-Za-z]+")


def _map_name(filename: str) -> str | None:
    match = _MAP_NAME_RE.match(filename)
    return match.group(0) if match else None


# Some filenames' digit strings parse to more than one date/day-of-month that
# both land in valid ranges (e.g. "Jan 16" vs "Nov 6" both fit the same
# digits). The original page for these is gone, so there's no way to
# recover the real date -- the user gave a best-guess (month, day) to break
# the tie for each one listed here.
_AMBIGUOUS_DATE_OVERRIDES: dict[str, tuple[int, int]] = {
    "Sunset11625924": (11, 6),  # user's best guess; page no longer available to confirm
}


def _parse_played_at(filename: str) -> datetime | None:
    """Parses the `<Map><MMDDYYHHMM>` filename convention into a datetime.

    Month/day/hour aren't consistently zero-padded when these filenames were
    typed by hand (e.g. "9" for a 9 o'clock hour, not "09"), so the digit
    string can be 8-10 characters wide with no fixed field boundaries. Year
    (2 digits) and minutes (2 digits, always padded) are fixed-width; month,
    day, and hour each vary 1-2 digits. We brute-force the possible widths and
    keep the decomposition where every field lands in a valid range -- this
    is unique in practice since invalid splits tend to produce an out-of-range
    hour or month.

    This scraped-data quirk is specific to how these filenames were typed by
    hand -- it has no bearing on a future RiotAPISource, which will carry a
    real match timestamp from the API.

    Per the user: this dataset's matches are always played in the evening, so
    the parsed hour (1-12) is treated as PM. Do not carry that assumption
    into any other data source.
    """
    map_name = _map_name(filename)
    if map_name is None:
        return None
    digits = filename[len(map_name) :]
    if len(digits) < 6:
        return None

    minute = digits[-2:]
    head = digits[:-2]

    candidates = []
    for mm_width, dd_width, hh_width in product((1, 2), repeat=3):
        if mm_width + dd_width + 2 + hh_width != len(head):
            continue
        month_str = head[:mm_width]
        day_str = head[mm_width : mm_width + dd_width]
        year_str = head[mm_width + dd_width : mm_width + dd_width + 2]
        hour_str = head[mm_width + dd_width + 2 :]

        month, day, year, hour = int(month_str), int(day_str), int(year_str), int(hour_str)
        if not (1 <= month <= 12 and 1 <= day <= 31 and 1 <= hour <= 12):
            continue
        candidates.append((month, day, year, hour, int(minute)))

    if len(candidates) != 1:
        override = _AMBIGUOUS_DATE_OVERRIDES.get(filename)
        if override is not None:
            candidates = [c for c in candidates if (c[0], c[1]) == override]
        if len(candidates) != 1:
            return None

    month, day, year, hour, minute_value = candidates[0]
    hour_24 = hour if hour == 12 else hour + 12  # assume PM -- see docstring
    try:
        return datetime(2000 + year, month, day, hour_24, minute_value)
    except ValueError:
        return None


def _team_from_outcome_letter(letter: str) -> Team:
    return Team.TEAM_1 if letter == "A" else Team.TEAM_2


def _get_or_create_player(db: Session, display_name: str) -> Player:
    player = db.query(Player).filter_by(display_name=display_name).one_or_none()
    if player is None:
        player = Player(display_name=display_name)
        db.add(player)
        db.flush()
    return player


def load_match(db: Session, filename: str) -> Match:
    existing = db.query(Match).filter_by(external_id=filename).one_or_none()
    if existing is not None:
        # RoundPlayerStat/KillEvent/ImpactScore reference match_players by raw FK
        # (no ORM relationship), so cascading through Match's relationships alone
        # picks an unsafe delete order. Delete children explicitly, FK-safe order.
        round_ids = [r.id for r in db.query(Round.id).filter_by(match_id=existing.id).all()]
        if round_ids:
            db.query(ImpactScore).filter(ImpactScore.round_id.in_(round_ids)).delete(synchronize_session=False)
            db.query(KillEvent).filter(KillEvent.round_id.in_(round_ids)).delete(synchronize_session=False)
            db.query(RoundPlayerStat).filter(RoundPlayerStat.round_id.in_(round_ids)).delete(synchronize_session=False)
        db.query(Round).filter_by(match_id=existing.id).delete(synchronize_session=False)
        db.query(MatchPlayer).filter_by(match_id=existing.id).delete(synchronize_session=False)
        db.delete(existing)
        db.flush()

    round_outcomes = matchDataPipeline.parseRoundOutcome(filename)
    round_kill_logs = matchDataPipeline.parseRoundKillList(filename)
    players_round_info = matchDataPipeline.parsePlayerRoundInfo(filename)
    agent_team_to_username = matchDataPipeline.reverseAgentTeamToPlayerUsername(players_round_info)

    team1_wins = sum(1 for outcome in round_outcomes.values() if outcome.startswith("Team A"))
    team2_wins = sum(1 for outcome in round_outcomes.values() if outcome.startswith("Team B"))

    match = Match(
        external_id=filename,
        source=MatchSource.SCRAPED,
        map_name=_map_name(filename),
        played_at=_parse_played_at(filename),
        team1_rounds_won=team1_wins,
        team2_rounds_won=team2_wins,
    )
    db.add(match)
    db.flush()

    match_players: dict[str, MatchPlayer] = {}
    for username, info in players_round_info.items():
        player = _get_or_create_player(db, username)
        match_player = MatchPlayer(
            match_id=match.id,
            player_id=player.id,
            agent=info["Agent"],
            team=Team(info["Team"]),
        )
        db.add(match_player)
        match_players[username] = match_player
    db.flush()

    round_count = len(next(iter(players_round_info.values()))["RoundInfo"])

    for i in range(round_count):
        round_number = i + 1
        round_index_str = str(round_number)
        events = round_kill_logs[round_index_str]

        planted = False
        plant_time = None
        exploded = False
        defused = False
        defuse_time = None
        for event in events:
            if event["Event"] == "Planted":
                planted = True
                plant_time = event["eventTime"]
            elif event["Event"] == "Exploded":
                exploded = True
            elif event["Event"] == "Defused":
                defused = True
                defuse_time = event["eventTime"]

        db_round = Round(
            match_id=match.id,
            round_number=round_number,
            outcome=round_outcomes.get(round_index_str),
            planted=planted,
            plant_time=plant_time,
            exploded=exploded,
            defused=defused,
            defuse_time=defuse_time,
        )
        db.add(db_round)
        db.flush()

        for username, match_player in match_players.items():
            round_info = players_round_info[username]["RoundInfo"][i]
            db.add(
                RoundPlayerStat(
                    round_id=db_round.id,
                    match_player_id=match_player.id,
                    score=round_info["Score"],
                    kills=round_info["Kills"],
                    deaths=round_info["Deaths"],
                    assists=round_info["Assists"],
                    loadout=round_info["Loadout"],
                    remaining=round_info["Remaining"],
                )
            )

        for event in events:
            if event["Event"] != "Kill":
                continue

            killer_username = agent_team_to_username.get(event["killerTeam"] + event["killerCharacter"])
            death_username = agent_team_to_username.get(event["deathTeam"] + event["deathCharacter"])

            db.add(
                KillEvent(
                    round_id=db_round.id,
                    killer_match_player_id=match_players[killer_username].id if killer_username else None,
                    death_match_player_id=match_players[death_username].id if death_username else None,
                    weapon=event["killWeapon"],
                    event_time_seconds=event["eventTime"],
                )
            )

    db.commit()
    return match
