from collections import Counter
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import ImpactScore, Match, MatchPlayer, Player, Round


@dataclass
class PlayerListEntry:
    display_name: str
    matches_played: int


def list_players(db: Session) -> list[PlayerListEntry]:
    rows = (
        db.query(Player.display_name, func.count(MatchPlayer.id))
        .join(MatchPlayer, MatchPlayer.player_id == Player.id)
        .group_by(Player.display_name)
        .order_by(func.count(MatchPlayer.id).desc())
        .all()
    )
    return [PlayerListEntry(display_name=name, matches_played=cnt) for name, cnt in rows]


def list_player_display_names(db: Session) -> list[str]:
    """Cheap alternative to list_players() for UI pickers that only need names
    (the login page, the friends 'add' dropdown) -- skips the MatchPlayer/
    ImpactScore join those don't render, just to build a matches/impact stat
    those pages never use.
    """
    names = [name for (name,) in db.query(Player.display_name).all()]
    names.sort(key=str.lower)
    return names


def get_player_or_404(db: Session, display_name: str) -> Player:
    player = db.query(Player).filter_by(display_name=display_name).one_or_none()
    if player is None:
        raise HTTPException(status_code=404, detail=f"No player '{display_name}'")
    return player


def find_player_by_search_query(db: Session, query: str) -> Player | None:
    """Looks up a player from a "Name#Tag" search box.

    The scraped demo data has no real Riot ID tag, so any trailing "#..."
    is stripped before matching -- typing it out of habit still works.
    """
    name = query.split("#", 1)[0].strip()
    if not name:
        return None
    return db.query(Player).filter(Player.display_name.ilike(name)).one_or_none()


@dataclass
class MatchBreakdown:
    match: Match
    agent: str
    team: str
    average_impact: float
    average_kill_impact: float
    average_death_impact: float
    win: bool | None


@dataclass
class GroupedStat:
    """Match-level win/loss + impact breakdown grouped by some key (an agent
    name or a map name)."""

    key: str
    matches_played: int
    wins: int
    losses: int
    win_rate: float | None
    average_impact: float
    average_kill_impact: float
    average_death_impact: float


@dataclass
class PlayerProfile:
    player: Player
    overall_average_impact: float
    matches: list[MatchBreakdown]
    agent_counts: Counter = field(default_factory=Counter)
    agent_stats: list[GroupedStat] = field(default_factory=list)
    map_stats: list[GroupedStat] = field(default_factory=list)
    avg_econ_kill: float = 0.0
    avg_econ_death: float = 0.0
    avg_clutch_kill: float = 0.0
    avg_clutch_death: float = 0.0
    avg_post_plant_kill: float = 0.0
    avg_post_plant_death: float = 0.0
    avg_traded_teammate: float = 0.0
    avg_traded_by_teammate: float = 0.0
    top_traded_teammate: list[tuple[str, int]] = field(default_factory=list)
    top_traded_by_teammate: list[tuple[str, int]] = field(default_factory=list)


def _match_win(match: Match, team: str) -> bool | None:
    """None for a tie (or missing round data) -- excluded from win-rate math
    rather than counted as a loss."""
    if match.team1_rounds_won == match.team2_rounds_won:
        return None
    team1_won = match.team1_rounds_won > match.team2_rounds_won
    return team1_won if team == "team-1" else not team1_won


def _grouped_stats(matches: list[MatchBreakdown], key_fn) -> list[GroupedStat]:
    groups: dict[str, dict] = {}
    for m in matches:
        key = key_fn(m)
        if key is None:
            continue
        g = groups.setdefault(
            key, {"count": 0, "wins": 0, "losses": 0, "impact": 0.0, "kill": 0.0, "death": 0.0}
        )
        g["count"] += 1
        if m.win is True:
            g["wins"] += 1
        elif m.win is False:
            g["losses"] += 1
        g["impact"] += m.average_impact
        g["kill"] += m.average_kill_impact
        g["death"] += m.average_death_impact

    stats = []
    for key, g in groups.items():
        decided = g["wins"] + g["losses"]
        stats.append(
            GroupedStat(
                key=key,
                matches_played=g["count"],
                wins=g["wins"],
                losses=g["losses"],
                win_rate=(g["wins"] / decided) if decided else None,
                average_impact=g["impact"] / g["count"],
                average_kill_impact=g["kill"] / g["count"],
                average_death_impact=g["death"] / g["count"],
            )
        )
    stats.sort(key=lambda s: s.matches_played, reverse=True)
    return stats


def get_player_profile(db: Session, player: Player) -> PlayerProfile:
    match_players = (
        db.query(MatchPlayer)
        .filter_by(player_id=player.id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .options(joinedload(MatchPlayer.match))
        .order_by(Match.played_at.nullslast(), Match.id)
        .all()
    )

    match_player_ids = [mp.id for mp in match_players]
    match_ids = [mp.match_id for mp in match_players]

    scores_by_match_player: dict[int, list[ImpactScore]] = {}
    if match_player_ids:
        for score in db.query(ImpactScore).filter(ImpactScore.match_player_id.in_(match_player_ids)).all():
            scores_by_match_player.setdefault(score.match_player_id, []).append(score)

    teammates_by_match: dict[int, list[MatchPlayer]] = {}
    if match_ids:
        all_teammates = (
            db.query(MatchPlayer)
            .filter(MatchPlayer.match_id.in_(match_ids))
            .options(joinedload(MatchPlayer.player))
            .all()
        )
        for mp in all_teammates:
            teammates_by_match.setdefault(mp.match_id, []).append(mp)

    matches: list[MatchBreakdown] = []
    all_impacts: list[float] = []
    agent_counts: Counter = Counter()

    total_econ_kill = 0.0
    total_econ_death = 0.0
    total_clutch_kill = 0.0
    total_clutch_death = 0.0
    total_post_plant_kill = 0.0
    total_post_plant_death = 0.0
    total_traded_teammate = 0
    total_traded_by_teammate = 0
    traded_teammate_totals: dict[str, int] = {}
    traded_by_teammate_totals: dict[str, int] = {}

    for match_player in match_players:
        scores = scores_by_match_player.get(match_player.id, [])
        if not scores:
            continue

        impacts = [score.impact for score in scores]
        kill_impacts = [score.kill_impact for score in scores]
        death_impacts = [score.death_impact for score in scores]

        match = match_player.match
        team = match_player.team.value if hasattr(match_player.team, "value") else match_player.team
        matches.append(
            MatchBreakdown(
                match=match,
                agent=match_player.agent,
                team=team,
                average_impact=sum(impacts) / len(impacts),
                average_kill_impact=sum(kill_impacts) / len(kill_impacts),
                average_death_impact=sum(death_impacts) / len(death_impacts),
                win=_match_win(match, team),
            )
        )
        all_impacts.extend(impacts)
        agent_counts[match_player.agent] += 1

        teammate_names = {
            mp.id: mp.player.display_name
            for mp in teammates_by_match.get(match_player.match_id, [])
        }

        for score in scores:
            breakdown = score.breakdown or {}
            total_econ_kill += breakdown.get("econ_kill", 0)
            total_econ_death += breakdown.get("econ_death", 0)
            total_clutch_kill += breakdown.get("clutch_kill", 0)
            total_clutch_death += breakdown.get("clutch_death", 0)
            total_post_plant_kill += breakdown.get("post_plant_kill", 0)
            total_post_plant_death += breakdown.get("post_plant_death", 0)
            total_traded_teammate += breakdown.get("traded_teammate", 0)
            total_traded_by_teammate += breakdown.get("traded_by_teammate", 0)
            for teammate_id, count in breakdown.get("traded_teammate_targets", {}).items():
                name = teammate_names.get(int(teammate_id))
                if name:
                    traded_teammate_totals[name] = traded_teammate_totals.get(name, 0) + count
            for teammate_id, count in breakdown.get("traded_by_teammate_sources", {}).items():
                name = teammate_names.get(int(teammate_id))
                if name:
                    traded_by_teammate_totals[name] = traded_by_teammate_totals.get(name, 0) + count

    overall_average = sum(all_impacts) / len(all_impacts) if all_impacts else 0.0
    matches_played = len(matches)

    def _avg(total: float) -> float:
        return total / matches_played if matches_played else 0.0

    def _top4(totals: dict[str, int]) -> list[tuple[str, int]]:
        return sorted(totals.items(), key=lambda item: item[1], reverse=True)[:4]

    return PlayerProfile(
        player=player,
        overall_average_impact=overall_average,
        matches=matches,
        agent_counts=agent_counts,
        agent_stats=_grouped_stats(matches, lambda m: m.agent),
        map_stats=_grouped_stats(matches, lambda m: m.match.map_name),
        avg_econ_kill=_avg(total_econ_kill),
        avg_econ_death=_avg(total_econ_death),
        avg_clutch_kill=_avg(total_clutch_kill),
        avg_clutch_death=_avg(total_clutch_death),
        avg_post_plant_kill=_avg(total_post_plant_kill),
        avg_post_plant_death=_avg(total_post_plant_death),
        avg_traded_teammate=_avg(total_traded_teammate),
        avg_traded_by_teammate=_avg(total_traded_by_teammate),
        top_traded_teammate=_top4(traded_teammate_totals),
        top_traded_by_teammate=_top4(traded_by_teammate_totals),
    )
