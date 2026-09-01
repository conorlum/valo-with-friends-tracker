import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Match, MatchPlayer, Player
from app.services.matches import MatchSummary, get_match_summary, list_matches, list_matches_for_players

DEFAULT_OVERLAP_THRESHOLD = 3
DEFAULT_MAX_GAP = timedelta(hours=6)

logger = logging.getLogger(__name__)


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
    anchor_player_ids: set[int] | None = None,
) -> list[RosterSession]:
    """Group matches into consecutive-roster "sessions".

    Pure and DB-free by design: takes plain Match-like objects (only .id,
    .played_at, .team1_rounds_won, .team2_rounds_won are read) and a
    pre-resolved match_id -> roster mapping, so it's testable against
    synthetic data without a real database.

    `anchor_player_ids` is the set of players the caller is building sessions
    for (the viewer, plus their friends when friends are in scope). It's only
    consulted to decide which side of a match is "ours" when the run's own
    rosters can't say -- see _build_roster_session.
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

    return [
        _build_roster_session(run, match_players_by_match, anchor_player_ids) for run in runs
    ]


def _side_holding(
    team1_ids: set[int], team2_ids: set[int], reference_ids: set[int] | None
) -> str | None:
    """Which side `reference_ids` mostly sits on, or None when it can't tell."""
    if not reference_ids:
        return None
    overlap1 = len(team1_ids & reference_ids)
    overlap2 = len(team2_ids & reference_ids)
    if overlap1 == overlap2:
        return None
    return "team-1" if overlap1 > overlap2 else "team-2"


def _build_roster_session(
    run: list[Match],
    match_players_by_match: dict[int, list[SessionMatchPlayer]],
    anchor_player_ids: set[int] | None = None,
) -> RosterSession:
    per_match_ids = [{mp.player_id for mp in match_players_by_match.get(m.id, [])} for m in run]

    is_multi_match = len(run) > 1

    # Used only to tell which side is "our" team per match: since consecutive
    # matches in a run are grouped by overlapping *full* (both-team) rosters,
    # intersecting those full rosters across the run still isolates mostly our
    # persistent players (opponents differ match to match). A single-match run
    # has nothing to intersect against, so its "core" would just be the whole
    # 10-player lobby -- useless here, and actively misleading when the two
    # sides aren't the same size, so it's skipped entirely below.
    provisional_core_ids = set(per_match_ids[0]) if per_match_ids else set()
    for ids in per_match_ids[1:]:
        provisional_core_ids &= ids
    core_reference_ids = provisional_core_ids if is_multi_match else None

    wins = 0
    losses = 0
    ambiguous_match_ids: list[int] = []
    per_match_our_ids: list[set[int]] = []
    team_by_match: dict[int, str] = {}

    for m, all_ids in zip(run, per_match_ids):
        team1_ids = {
            mp.player_id for mp in match_players_by_match.get(m.id, []) if mp.team == "team-1"
        }
        team2_ids = {
            mp.player_id for mp in match_players_by_match.get(m.id, []) if mp.team == "team-2"
        }
        # The run's own cross-match core is the stronger signal when there is
        # one, so it goes first; the anchor (the players this session was built
        # for) resolves what it can't -- every single-match session, plus the
        # occasional multi-match one where the core splits evenly across sides.
        our_team = _side_holding(team1_ids, team2_ids, core_reference_ids) or _side_holding(
            team1_ids, team2_ids, anchor_player_ids
        )
        if our_team is None:
            # Can't tell which side is ours for this match; fall back to
            # including everyone so we don't silently drop real teammates.
            ambiguous_match_ids.append(m.id)
            per_match_our_ids.append(all_ids)
            continue
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


def list_sessions(db: Session, player_ids: list[int] | None = None) -> list[SessionSummary]:
    """Scoped to matches at least one of `player_ids` was in, when given --
    without it, this would group every match in the whole DB (including
    every crawled snowball opponent who has nothing to do with this
    player) into sessions, which is both wrong and, at thousands of
    matches, slow."""
    t0 = time.perf_counter()
    all_matches = list_matches_for_players(db, player_ids) if player_ids else list_matches(db)
    matches = [m for m in all_matches if m.played_at is not None]
    match_ids = [m.id for m in matches]
    t1 = time.perf_counter()

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
    t2 = time.perf_counter()

    roster_sessions = group_matches_into_sessions(
        matches, match_players_by_match, anchor_player_ids=set(player_ids) if player_ids else None
    )
    t3 = time.perf_counter()

    sessions = []
    for i, rs in enumerate(roster_sessions):
        # match_summaries is deliberately left empty here -- it's only used by the
        # single-session detail page (sessions/detail.html), which fills it in for
        # just that session's matches in get_session_or_404 below. Computing it here
        # for every session's matches would mean list_sessions() -- called by both
        # the session list page (which never reads match_summaries at all) and every
        # single-session detail lookup -- pays the cost of summarizing every match
        # the viewer has ever played, every time.
        others = rs.roster_display_names - rs.core_display_names
        roster_ordered = sorted(rs.core_display_names) + sorted(others)
        sessions.append(
            SessionSummary(
                index=i,
                matches=rs.matches,
                match_summaries={},
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
    t4 = time.perf_counter()
    logger.info(
        "list_sessions player_ids=%d matches=%d match_player_rows=%d sessions=%d "
        "query_matches=%.3fs query_match_players=%.3fs group=%.3fs build=%.3fs total=%.3fs",
        len(player_ids) if player_ids else 0,
        len(matches),
        sum(len(v) for v in match_players_by_match.values()),
        len(sessions),
        t1 - t0, t2 - t1, t3 - t2, t4 - t3, t4 - t0,
    )
    return sessions


def find_session_index_for_matches(
    sessions: list[SessionSummary], match_ids: list[int]
) -> int | None:
    """Finds the index of the session in `sessions` sharing the most matches
    with `match_ids`, or None if none of them overlap at all.

    Used to translate a session viewed under one friends-scope into the
    equivalent session under a different scope -- a session's index is only
    valid within the player_ids list it was built from (list_sessions can
    group matches differently, and assign different positions, depending on
    which players are in scope), so a session_index from one scope can't be
    reused directly as a link into another.

    Matching on overlap rather than on one chosen match matters because the
    two scopes hold different match sets: narrowing to "just mine" drops every
    match the viewer sat out, so keying off the session's *first* match sent
    the viewer back to the session list whenever that first match happened to
    be one they skipped.
    """
    wanted = set(match_ids)
    best_index: int | None = None
    best_overlap = 0
    for s in sessions:
        overlap = sum(1 for m in s.matches if m.id in wanted)
        if overlap > best_overlap:
            best_index = s.index
            best_overlap = overlap
    return best_index


def get_session_or_404(db: Session, session_index: int, player_ids: list[int] | None = None) -> SessionSummary:
    sessions = list_sessions(db, player_ids)
    if not (0 <= session_index < len(sessions)):
        raise HTTPException(status_code=404, detail=f"No session {session_index}")
    session = sessions[session_index]
    t0 = time.perf_counter()
    session.match_summaries = {m.id: get_match_summary(db, m) for m in session.matches}
    logger.info(
        "get_session_or_404 session_index=%d matches=%d match_summaries=%.3fs",
        session_index, len(session.matches), time.perf_counter() - t0,
    )
    return session
