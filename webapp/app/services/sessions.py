from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Match, MatchPlayer, Player
from app.services.matches import MatchSummary, get_match_summary, list_matches

DEFAULT_OVERLAP_THRESHOLD = 3
DEFAULT_MAX_GAP = timedelta(hours=6)


@dataclass
class SessionMatchPlayer:
    player_id: int
    team: str
    display_name: str


@dataclass
class RosterSession:
    matches: list[Match]
    roster_player_ids: set[int]
    roster_display_names: set[str]
    core_player_ids: set[int]
    core_display_names: set[str]
    started_at: datetime
    ended_at: datetime
    wins: int
    losses: int
    ambiguous_match_ids: list[int] = field(default_factory=list)
    is_multi_match: bool = False
    team_by_match: dict[int, str] = field(default_factory=dict)


def group_matches_into_sessions(
    matches: list[Match],
    match_players_by_match: dict[int, list[SessionMatchPlayer]],
    overlap_threshold: int = DEFAULT_OVERLAP_THRESHOLD,
    max_gap: timedelta = DEFAULT_MAX_GAP,
) -> list[RosterSession]:
    """Group matches into consecutive-roster "sessions".

    Pure and DB-free by design: takes plain Match-like objects (only .id,
    .played_at, .team1_rounds_won, .team2_rounds_won are read) and a
    pre-resolved match_id -> roster mapping, so it's testable against
    synthetic data without a real database.
    """
    timed_matches = sorted(
        (m for m in matches if m.played_at is not None),
        key=lambda m: (m.played_at, m.id),
    )

    runs: list[list[Match]] = []
    for m in timed_matches:
        if runs:
            prev = runs[-1][-1]
            prev_ids = {mp.player_id for mp in match_players_by_match.get(prev.id, [])}
            cur_ids = {mp.player_id for mp in match_players_by_match.get(m.id, [])}
            gap = m.played_at - prev.played_at
            if gap <= max_gap and len(prev_ids & cur_ids) >= overlap_threshold:
                runs[-1].append(m)
                continue
        runs.append([m])

    return [_build_roster_session(run, match_players_by_match) for run in runs]


def _build_roster_session(
    run: list[Match], match_players_by_match: dict[int, list[SessionMatchPlayer]]
) -> RosterSession:
    per_match_ids = [{mp.player_id for mp in match_players_by_match.get(m.id, [])} for m in run]

    # Used only to tell which side is "our" team per match: since consecutive
    # matches in a run are grouped by overlapping *full* (both-team) rosters,
    # intersecting those full rosters across the run still isolates mostly our
    # persistent players (opponents differ match to match).
    provisional_core_ids = set(per_match_ids[0]) if per_match_ids else set()
    for ids in per_match_ids[1:]:
        provisional_core_ids &= ids

    is_multi_match = len(run) > 1
    wins = 0
    losses = 0
    ambiguous_match_ids: list[int] = []
    per_match_our_ids: list[set[int]] = []
    team_by_match: dict[int, str] = {}

    if is_multi_match:
        for m, all_ids in zip(run, per_match_ids):
            team1_ids = {
                mp.player_id for mp in match_players_by_match.get(m.id, []) if mp.team == "team-1"
            }
            team2_ids = {
                mp.player_id for mp in match_players_by_match.get(m.id, []) if mp.team == "team-2"
            }
            overlap1 = len(team1_ids & provisional_core_ids)
            overlap2 = len(team2_ids & provisional_core_ids)
            if overlap1 == overlap2:
                # Can't tell which side is ours for this match; fall back to
                # including everyone so we don't silently drop real teammates.
                ambiguous_match_ids.append(m.id)
                per_match_our_ids.append(all_ids)
                continue
            our_team = "team-1" if overlap1 > overlap2 else "team-2"
            team_by_match[m.id] = our_team
            per_match_our_ids.append(team1_ids if our_team == "team-1" else team2_ids)
            our_won = (
                m.team1_rounds_won > m.team2_rounds_won
                if our_team == "team-1"
                else m.team2_rounds_won > m.team1_rounds_won
            )
            if our_won:
                wins += 1
            else:
                losses += 1
    else:
        # A single match has no other match in the session to compare
        # rosters against, so which side is "ours" can't be determined.
        per_match_our_ids = list(per_match_ids)

    roster_player_ids: set[int] = set()
    for ids in per_match_our_ids:
        roster_player_ids |= ids

    core_player_ids = set(per_match_our_ids[0]) if per_match_our_ids else set()
    for ids in per_match_our_ids[1:]:
        core_player_ids &= ids

    display_name_by_id: dict[int, str] = {}
    for m in run:
        for mp in match_players_by_match.get(m.id, []):
            display_name_by_id[mp.player_id] = mp.display_name

    roster_display_names = {
        display_name_by_id[pid] for pid in roster_player_ids if pid in display_name_by_id
    }
    core_display_names = {
        display_name_by_id[pid] for pid in core_player_ids if pid in display_name_by_id
    }

    return RosterSession(
        matches=run,
        roster_player_ids=roster_player_ids,
        roster_display_names=roster_display_names,
        core_player_ids=core_player_ids,
        core_display_names=core_display_names,
        started_at=run[0].played_at,
        ended_at=run[-1].played_at,
        wins=wins,
        losses=losses,
        ambiguous_match_ids=ambiguous_match_ids,
        is_multi_match=is_multi_match,
        team_by_match=team_by_match,
    )


@dataclass
class SessionSummary:
    index: int
    matches: list[Match]
    match_summaries: dict[int, MatchSummary]
    roster_player_ids: set[int]
    roster_display_names: set[str]
    core_player_ids: set[int]
    core_display_names: set[str]
    started_at: datetime
    ended_at: datetime
    wins: int
    losses: int
    ambiguous_match_ids: list[int]
    is_multi_match: bool
    team_by_match: dict[int, str] = field(default_factory=dict)
    roster_ordered: list[str] = field(default_factory=list)


def list_sessions(db: Session) -> list[SessionSummary]:
    matches = [m for m in list_matches(db) if m.played_at is not None]
    match_ids = [m.id for m in matches]

    match_players_by_match: dict[int, list[SessionMatchPlayer]] = {}
    if match_ids:
        rows = (
            db.query(MatchPlayer.match_id, MatchPlayer.player_id, MatchPlayer.team, Player.display_name)
            .join(Player, Player.id == MatchPlayer.player_id)
            .filter(MatchPlayer.match_id.in_(match_ids))
            .all()
        )
        for match_id, player_id, team, display_name in rows:
            match_players_by_match.setdefault(match_id, []).append(
                SessionMatchPlayer(
                    player_id=player_id,
                    team=team.value if hasattr(team, "value") else team,
                    display_name=display_name,
                )
            )

    roster_sessions = group_matches_into_sessions(matches, match_players_by_match)

    sessions = []
    for i, rs in enumerate(roster_sessions):
        match_summaries = {m.id: get_match_summary(db, m) for m in rs.matches}
        others = rs.roster_display_names - rs.core_display_names
        roster_ordered = sorted(rs.core_display_names) + sorted(others)
        sessions.append(
            SessionSummary(
                index=i,
                matches=rs.matches,
                match_summaries=match_summaries,
                roster_player_ids=rs.roster_player_ids,
                roster_display_names=rs.roster_display_names,
                core_player_ids=rs.core_player_ids,
                core_display_names=rs.core_display_names,
                started_at=rs.started_at,
                ended_at=rs.ended_at,
                wins=rs.wins,
                losses=rs.losses,
                ambiguous_match_ids=rs.ambiguous_match_ids,
                is_multi_match=rs.is_multi_match,
                team_by_match=rs.team_by_match,
                roster_ordered=roster_ordered,
            )
        )
    return sessions


def get_session_or_404(db: Session, session_index: int) -> SessionSummary:
    sessions = list_sessions(db)
    if not (0 <= session_index < len(sessions)):
        raise HTTPException(status_code=404, detail=f"No session {session_index}")
    return sessions[session_index]
