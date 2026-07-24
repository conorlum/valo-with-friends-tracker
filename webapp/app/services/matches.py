from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import ImpactScore, KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.scoring.impact import FORCE_THRESHOLD
from app.services.friends import list_friend_ids
from app.services.shoutouts import PlayerShoutout, assign_shoutouts

# Kept in sync with the same-named constants in app.services.session_stats
# (not imported from there -- sessions.py already imports from this module,
# so importing back would be circular).
_MULTI_KILL_THRESHOLD = 3
_OP_LOADOUT_THRESHOLD = 5100
_ENTRY_KILL_WINDOW_SECONDS = 20
_LATE_KILL_MARK_SECONDS = 60


def _winner_side(outcome: str | None) -> str | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return "team-1"
    if outcome.startswith("Team B"):
        return "team-2"
    return None


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


def get_match_shoutouts(
    db: Session, match: Match, summary: MatchSummary, viewer_player_id: int | None = None
) -> list[PlayerShoutout]:
    """Gives every player in this match (both teams) one flattering,
    individual callout, via the same category catalog and assignment
    algorithm as the session-level Shoutouts feature
    (`app.services.session_stats`) -- but scoped to just this match, and
    computed symmetrically across both teams since there's no "our side"
    on a single match page. Uses `match_player_id` as the identity key
    throughout (unlike the session version, which aggregates by `Player.id`
    across matches) since within one match the two coincide 1:1.
    """
    match_players = db.query(MatchPlayer).filter_by(match_id=match.id).all()
    team_of_mp: dict[int, str] = {
        mp.id: (mp.team.value if hasattr(mp.team, "value") else mp.team) for mp in match_players
    }
    mp_ids_by_team: dict[str, set[int]] = {}
    for mp_id, team in team_of_mp.items():
        mp_ids_by_team.setdefault(team, set()).add(mp_id)

    multi_kill_counts: dict[int, int] = {}
    eco_kill_counts: dict[int, int] = {}
    op_kill_counts: dict[int, int] = {}
    kill_totals: dict[int, int] = {}
    for match_player_id, kills, loadout in (
        db.query(RoundPlayerStat.match_player_id, RoundPlayerStat.kills, RoundPlayerStat.loadout)
        .join(Round, Round.id == RoundPlayerStat.round_id)
        .filter(Round.match_id == match.id)
        .all()
    ):
        kill_totals[match_player_id] = kill_totals.get(match_player_id, 0) + kills
        if kills >= _MULTI_KILL_THRESHOLD:
            multi_kill_counts[match_player_id] = multi_kill_counts.get(match_player_id, 0) + 1
        if loadout < FORCE_THRESHOLD:
            eco_kill_counts[match_player_id] = eco_kill_counts.get(match_player_id, 0) + kills
        if loadout >= _OP_LOADOUT_THRESHOLD:
            op_kill_counts[match_player_id] = op_kill_counts.get(match_player_id, 0) + kills

    # Each team's own top fragger, for "kills on the enemy's top fragger" --
    # computed per-team (not "ours vs theirs") since a single match has no
    # notion of one side being "ours".
    top_frag_mp_by_team: dict[str, int] = {
        team: max(mp_ids, key=lambda mp_id: (kill_totals.get(mp_id, 0), -mp_id))
        for team, mp_ids in mp_ids_by_team.items()
        if mp_ids
    }

    max_streak: dict[int, int] = {}
    current_streak: dict[int, int] = {}
    kills_on_top_frag: dict[int, int] = {}
    clutch_counts: dict[int, int] = {}
    xvx_kill_counts: dict[int, int] = {}
    round_changer_kill_counts: dict[int, int] = {}

    rounds = db.query(Round).filter_by(match_id=match.id).order_by(Round.round_number).all()
    for round_row in rounds:
        winner_side = _winner_side(round_row.outcome)
        alive_by_team: dict[str, set[int]] = {team: set(ids) for team, ids in mp_ids_by_team.items()}
        # Most extreme (own_count, opp_count, alive-snapshot) reached while
        # outnumbered-or-even at 1 or 2 alive, tracked per team -- the clutch
        # situation, if any, that team resolved from this round.
        clutch_state_by_team: dict[str, tuple[int, int, frozenset[int]] | None] = {
            team: None for team in mp_ids_by_team
        }

        events = (
            db.query(KillEvent)
            .filter_by(round_id=round_row.id)
            .order_by(KillEvent.event_time_seconds)
            .all()
        )
        for event in events:
            killer_id = event.killer_match_player_id
            death_id = event.death_match_player_id
            killer_team = team_of_mp.get(killer_id) if killer_id is not None else None
            death_team = team_of_mp.get(death_id) if death_id is not None else None

            if (
                killer_id is not None
                and death_id is not None
                and killer_id != death_id
                and killer_team is not None
                and death_team is not None
                and killer_team != death_team
            ):
                pre_killer_count = len(alive_by_team[killer_team])
                pre_other_count = len(alive_by_team[death_team])

                if pre_killer_count == pre_other_count and pre_killer_count > 0:
                    xvx_kill_counts[killer_id] = xvx_kill_counts.get(killer_id, 0) + 1
                if pre_killer_count < pre_other_count:
                    round_changer_kill_counts[killer_id] = round_changer_kill_counts.get(killer_id, 0) + 1

                top_frag_mp = top_frag_mp_by_team.get(death_team)
                if top_frag_mp is not None and death_id == top_frag_mp:
                    kills_on_top_frag[killer_id] = kills_on_top_frag.get(killer_id, 0) + 1

            if killer_id is not None and killer_id in team_of_mp:
                current_streak[killer_id] = current_streak.get(killer_id, 0) + 1
                if current_streak[killer_id] > max_streak.get(killer_id, 0):
                    max_streak[killer_id] = current_streak[killer_id]

            if death_id is not None and death_id in team_of_mp:
                current_streak[death_id] = 0
                if death_team is not None:
                    alive_by_team[death_team].discard(death_id)

            for team, alive_set in alive_by_team.items():
                other_teams = [t for t in alive_by_team if t != team]
                if not other_teams:
                    continue
                opp_count = len(alive_by_team[other_teams[0]])
                own_count = len(alive_set)
                if own_count in (1, 2) and opp_count >= own_count:
                    state = clutch_state_by_team[team]
                    if state is None or own_count < state[0]:
                        clutch_state_by_team[team] = (own_count, opp_count, frozenset(alive_set))

            if any(len(alive_set) == 0 for alive_set in alive_by_team.values()):
                break

        if winner_side is not None:
            state = clutch_state_by_team.get(winner_side)
            if state is not None:
                _, _, alive_snapshot = state
                for mp_id in alive_snapshot:
                    clutch_counts[mp_id] = clutch_counts.get(mp_id, 0) + 1

    mvp_counts: dict[int, int] = {}
    best_round_impact: dict[int, float] = {}
    best_by_round: dict[int, tuple[int, float]] = {}
    for round_id, match_player_id, impact in (
        db.query(ImpactScore.round_id, ImpactScore.match_player_id, ImpactScore.impact)
        .join(Round, Round.id == ImpactScore.round_id)
        .filter(Round.match_id == match.id)
        .all()
    ):
        current = best_by_round.get(round_id)
        if current is None or impact > current[1]:
            best_by_round[round_id] = (match_player_id, impact)
        current_best = best_round_impact.get(match_player_id)
        if current_best is None or impact > current_best:
            best_round_impact[match_player_id] = impact
    for match_player_id, _impact in best_by_round.values():
        mvp_counts[match_player_id] = mvp_counts.get(match_player_id, 0) + 1

    traded_teammate_totals: dict[int, int] = {}
    traded_by_teammate_totals: dict[int, int] = {}
    for match_player_id, breakdown in (
        db.query(ImpactScore.match_player_id, ImpactScore.breakdown)
        .join(Round, Round.id == ImpactScore.round_id)
        .filter(Round.match_id == match.id)
        .all()
    ):
        breakdown = breakdown or {}
        traded_teammate_totals[match_player_id] = traded_teammate_totals.get(
            match_player_id, 0
        ) + breakdown.get("traded_teammate", 0)
        traded_by_teammate_totals[match_player_id] = traded_by_teammate_totals.get(
            match_player_id, 0
        ) + breakdown.get("traded_by_teammate", 0)

    post_plant_kill_counts: dict[int, int] = {}
    for (killer_mp_id,) in (
        db.query(KillEvent.killer_match_player_id)
        .join(Round, Round.id == KillEvent.round_id)
        .filter(
            Round.match_id == match.id,
            Round.planted.is_(True),
            Round.plant_time.isnot(None),
            KillEvent.event_time_seconds >= Round.plant_time,
            KillEvent.killer_match_player_id.isnot(None),
            KillEvent.killer_match_player_id != KillEvent.death_match_player_id,
        )
        .all()
    ):
        post_plant_kill_counts[killer_mp_id] = post_plant_kill_counts.get(killer_mp_id, 0) + 1

    entry_kill_counts: dict[int, int] = {}
    for (killer_mp_id,) in (
        db.query(KillEvent.killer_match_player_id)
        .join(Round, Round.id == KillEvent.round_id)
        .filter(
            Round.match_id == match.id,
            KillEvent.event_time_seconds <= _ENTRY_KILL_WINDOW_SECONDS,
            KillEvent.killer_match_player_id.isnot(None),
            KillEvent.killer_match_player_id != KillEvent.death_match_player_id,
        )
        .all()
    ):
        entry_kill_counts[killer_mp_id] = entry_kill_counts.get(killer_mp_id, 0) + 1

    late_kill_counts: dict[int, int] = {}
    for (killer_mp_id,) in (
        db.query(KillEvent.killer_match_player_id)
        .join(Round, Round.id == KillEvent.round_id)
        .filter(
            Round.match_id == match.id,
            KillEvent.event_time_seconds >= _LATE_KILL_MARK_SECONDS,
            KillEvent.killer_match_player_id.isnot(None),
            KillEvent.killer_match_player_id != KillEvent.death_match_player_id,
        )
        .all()
    ):
        late_kill_counts[killer_mp_id] = late_kill_counts.get(killer_mp_id, 0) + 1

    raw_dicts: dict[str, dict[int, int]] = {
        "entry_kill_counts": entry_kill_counts,
        "clutch_counts": clutch_counts,
        "post_plant_kill_counts": post_plant_kill_counts,
        "multi_kill_counts": multi_kill_counts,
        "max_streak": max_streak,
        "round_changer_kill_counts": round_changer_kill_counts,
        "xvx_kill_counts": xvx_kill_counts,
        "kills_on_top_frag": kills_on_top_frag,
        "late_kill_counts": late_kill_counts,
        "op_kill_counts": op_kill_counts,
        "eco_kill_counts": eco_kill_counts,
        "traded_teammate_totals": traded_teammate_totals,
        "traded_by_teammate_totals": traded_by_teammate_totals,
        "mvp_counts": mvp_counts,
    }

    anchor = None
    if summary.players:
        leader = summary.players[0]
        anchor = (
            leader.match_player_id,
            "Anchor of the Match",
            f"led the match with {round(leader.average_impact)} avg Impact per round",
        )

    roster = [(p.match_player_id, p.display_name) for p in summary.players]
    shoutouts = assign_shoutouts(roster, raw_dicts, best_round_impact, anchor)

    if viewer_player_id is not None:
        player_id_by_mp: dict[int, int] = {mp.id: mp.player_id for mp in match_players}
        friend_ids = list_friend_ids(db, viewer_player_id) | {viewer_player_id}
        shoutouts = [
            s for s in shoutouts if player_id_by_mp.get(s.player_id) in friend_ids
        ]

    return shoutouts


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
