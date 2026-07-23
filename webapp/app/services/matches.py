from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import ImpactScore, KillEvent, Match, MatchPlayer, Player, Round


def list_matches(db: Session) -> list[Match]:
    return db.query(Match).order_by(Match.played_at.desc().nullslast(), Match.id.desc()).all()


def list_matches_for_player(db: Session, player_id: int) -> list[Match]:
    return (
        db.query(Match)
        .join(MatchPlayer, MatchPlayer.match_id == Match.id)
        .filter(MatchPlayer.player_id == player_id)
        .order_by(Match.played_at.desc().nullslast(), Match.id.desc())
        .all()
    )


def get_match_or_404(db: Session, external_id: str) -> Match:
    match = db.query(Match).filter_by(external_id=external_id).one_or_none()
    if match is None:
        raise HTTPException(status_code=404, detail=f"No match '{external_id}'")
    return match


@dataclass
class PlayerSummary:
    match_player_id: int
    display_name: str
    agent: str
    team: str
    average_impact: float
    average_kill_impact: float
    average_death_impact: float
    impact_by_round: dict[int, float]
    kill_impact_by_round: dict[int, float]
    death_impact_by_round: dict[int, float]
    econ_kill: float = 0.0
    econ_death: float = 0.0
    clutch_kill: float = 0.0
    clutch_death: float = 0.0
    post_plant_kill: float = 0.0
    post_plant_death: float = 0.0
    traded_teammate: int = 0
    traded_by_teammate: int = 0
    traded_teammate_ids: dict[int, int] = field(default_factory=dict)
    traded_by_teammate_ids: dict[int, int] = field(default_factory=dict)
    traded_teammate_names: dict[str, int] = field(default_factory=dict)
    traded_by_teammate_names: dict[str, int] = field(default_factory=dict)


@dataclass
class TeamSummary:
    team: str
    total_impact: float
    average_impact_per_round: float
    total_kill_impact: float
    total_death_impact: float
    impact_by_round: dict[int, float]


@dataclass
class MatchSummary:
    players: list[PlayerSummary]
    round_numbers: list[int]
    round_outcomes: dict[int, str | None]
    team_summaries: list[TeamSummary]


def get_match_summary(db: Session, match: Match) -> MatchSummary:
    rows = (
        db.query(
            MatchPlayer.id,
            Player.display_name,
            MatchPlayer.agent,
            MatchPlayer.team,
            Round.round_number,
            ImpactScore.impact,
            ImpactScore.kill_impact,
            ImpactScore.death_impact,
            ImpactScore.breakdown,
        )
        .join(Player, Player.id == MatchPlayer.player_id)
        .join(ImpactScore, ImpactScore.match_player_id == MatchPlayer.id)
        .join(Round, Round.id == ImpactScore.round_id)
        .filter(MatchPlayer.match_id == match.id)
        .order_by(Round.round_number)
        .all()
    )

    by_player: dict[int, PlayerSummary] = {}
    round_numbers: set[int] = set()

    for match_player_id, display_name, agent, team, round_number, impact, kill_impact, death_impact, breakdown in rows:
        round_numbers.add(round_number)
        summary = by_player.get(match_player_id)
        if summary is None:
            summary = PlayerSummary(
                match_player_id=match_player_id,
                display_name=display_name,
                agent=agent,
                team=team.value if hasattr(team, "value") else team,
                average_impact=0.0,
                average_kill_impact=0.0,
                average_death_impact=0.0,
                impact_by_round={},
                kill_impact_by_round={},
                death_impact_by_round={},
            )
            by_player[match_player_id] = summary
        summary.impact_by_round[round_number] = impact
        summary.kill_impact_by_round[round_number] = kill_impact
        summary.death_impact_by_round[round_number] = death_impact

        breakdown = breakdown or {}
        summary.econ_kill += breakdown.get("econ_kill", 0)
        summary.econ_death += breakdown.get("econ_death", 0)
        summary.clutch_kill += breakdown.get("clutch_kill", 0)
        summary.clutch_death += breakdown.get("clutch_death", 0)
        summary.post_plant_kill += breakdown.get("post_plant_kill", 0)
        summary.post_plant_death += breakdown.get("post_plant_death", 0)
        summary.traded_teammate += breakdown.get("traded_teammate", 0)
        summary.traded_by_teammate += breakdown.get("traded_by_teammate", 0)
        for teammate_id, count in breakdown.get("traded_teammate_targets", {}).items():
            summary.traded_teammate_ids[int(teammate_id)] = summary.traded_teammate_ids.get(int(teammate_id), 0) + count
        for teammate_id, count in breakdown.get("traded_by_teammate_sources", {}).items():
            summary.traded_by_teammate_ids[int(teammate_id)] = summary.traded_by_teammate_ids.get(int(teammate_id), 0) + count

    for summary in by_player.values():
        summary.traded_teammate_names = {
            by_player[mp_id].display_name: count
            for mp_id, count in summary.traded_teammate_ids.items()
            if mp_id in by_player
        }
        summary.traded_by_teammate_names = {
            by_player[mp_id].display_name: count
            for mp_id, count in summary.traded_by_teammate_ids.items()
            if mp_id in by_player
        }

    for summary in by_player.values():
        values = summary.impact_by_round.values()
        summary.average_impact = sum(values) / len(values) if values else 0.0
        kill_values = summary.kill_impact_by_round.values()
        summary.average_kill_impact = sum(kill_values) / len(kill_values) if kill_values else 0.0
        death_values = summary.death_impact_by_round.values()
        summary.average_death_impact = sum(death_values) / len(death_values) if death_values else 0.0

    players = sorted(by_player.values(), key=lambda p: p.average_impact, reverse=True)
    round_outcomes = {
        r.round_number: r.outcome
        for r in db.query(Round).filter_by(match_id=match.id).all()
    }
    sorted_rounds = sorted(round_numbers)

    team_impact_by_round: dict[str, dict[int, float]] = {}
    team_kill_totals: dict[str, float] = {}
    team_death_totals: dict[str, float] = {}
    for p in by_player.values():
        bucket = team_impact_by_round.setdefault(p.team, {})
        for rn, impact in p.impact_by_round.items():
            bucket[rn] = bucket.get(rn, 0.0) + impact
        team_kill_totals[p.team] = team_kill_totals.get(p.team, 0.0) + sum(p.kill_impact_by_round.values())
        team_death_totals[p.team] = team_death_totals.get(p.team, 0.0) + sum(p.death_impact_by_round.values())

    team_summaries = [
        TeamSummary(
            team=team,
            total_impact=sum(team_impact_by_round[team].values()),
            average_impact_per_round=(
                sum(team_impact_by_round[team].values()) / len(sorted_rounds) if sorted_rounds else 0.0
            ),
            total_kill_impact=team_kill_totals[team],
            total_death_impact=team_death_totals[team],
            impact_by_round=team_impact_by_round[team],
        )
        for team in sorted(team_impact_by_round)
    ]

    return MatchSummary(
        players=players,
        round_numbers=sorted_rounds,
        round_outcomes=round_outcomes,
        team_summaries=team_summaries,
    )


@dataclass
class RoundLogEntry:
    event_time_seconds: float
    kind: str  # "kill", "plant", or "defuse"
    killer_display_name: str | None = None
    killer_agent: str | None = None
    death_display_name: str | None = None
    death_agent: str | None = None
    weapon: str | None = None


@dataclass
class RoundPlayerImpact:
    display_name: str
    agent: str
    team: str
    kill_impact: float
    death_impact: float
    impact: float
    breakdown: dict | None


@dataclass
class RoundTeamTotal:
    team: str
    kill_impact: float
    death_impact: float
    impact: float


@dataclass
class RoundDetail:
    round_number: int
    outcome: str | None
    planted: bool
    plant_time: float | None
    exploded: bool
    defused: bool
    defuse_time: float | None
    events: list[RoundLogEntry]
    player_impacts: list[RoundPlayerImpact]
    team_totals: list[RoundTeamTotal]


def get_round_detail(db: Session, match: Match, round_number: int) -> RoundDetail:
    round_row = db.query(Round).filter_by(match_id=match.id, round_number=round_number).one_or_none()
    if round_row is None:
        raise HTTPException(status_code=404, detail=f"No round {round_number} for match '{match.external_id}'")

    match_players = {
        mp.id: mp for mp in db.query(MatchPlayer).filter_by(match_id=match.id).all()
    }
    players_by_id = {p.id: p for p in db.query(Player).all()}

    def _display_name(match_player_id: int | None) -> str | None:
        if match_player_id is None:
            return None
        mp = match_players.get(match_player_id)
        return players_by_id[mp.player_id].display_name if mp else None

    def _agent(match_player_id: int | None) -> str | None:
        if match_player_id is None:
            return None
        mp = match_players.get(match_player_id)
        return mp.agent if mp else None

    kill_events = (
        db.query(KillEvent)
        .filter_by(round_id=round_row.id)
        .order_by(KillEvent.event_time_seconds)
        .all()
    )
    events: list[RoundLogEntry] = [
        RoundLogEntry(
            event_time_seconds=k.event_time_seconds,
            kind="kill",
            killer_display_name=_display_name(k.killer_match_player_id),
            killer_agent=_agent(k.killer_match_player_id),
            death_display_name=_display_name(k.death_match_player_id),
            death_agent=_agent(k.death_match_player_id),
            weapon=k.weapon,
        )
        for k in kill_events
    ]
    if round_row.planted and round_row.plant_time is not None:
        events.append(RoundLogEntry(event_time_seconds=round_row.plant_time, kind="plant"))
    if round_row.defused and round_row.defuse_time is not None:
        events.append(RoundLogEntry(event_time_seconds=round_row.defuse_time, kind="defuse"))
    events.sort(key=lambda e: e.event_time_seconds)

    impact_scores = db.query(ImpactScore).filter_by(round_id=round_row.id).all()
    player_impacts = []
    for score in impact_scores:
        mp = match_players[score.match_player_id]
        player_impacts.append(
            RoundPlayerImpact(
                display_name=players_by_id[mp.player_id].display_name,
                agent=mp.agent,
                team=mp.team.value if hasattr(mp.team, "value") else mp.team,
                kill_impact=score.kill_impact,
                death_impact=score.death_impact,
                impact=score.impact,
                breakdown=score.breakdown,
            )
        )
    player_impacts.sort(key=lambda p: p.impact, reverse=True)

    totals: dict[str, RoundTeamTotal] = {}
    for p in player_impacts:
        t = totals.setdefault(
            p.team, RoundTeamTotal(team=p.team, kill_impact=0.0, death_impact=0.0, impact=0.0)
        )
        t.kill_impact += p.kill_impact
        t.death_impact += p.death_impact
        t.impact += p.impact
    team_totals = [totals[t] for t in sorted(totals)]

    return RoundDetail(
        round_number=round_row.round_number,
        outcome=round_row.outcome,
        planted=round_row.planted,
        plant_time=round_row.plant_time,
        exploded=round_row.exploded,
        defused=round_row.defused,
        defuse_time=round_row.defuse_time,
        events=events,
        player_impacts=player_impacts,
        team_totals=team_totals,
    )
