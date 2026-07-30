from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, selectinload

from app.models import ImpactScore, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.scoring.credit_events import RoundStat, compute_round_credit_events
from app.services.friends import list_friend_ids
from app.services.shoutouts import PlayerShoutout, assign_shoutouts

# Minimum shared rounds before a friend is eligible for a shoutout category --
# keeps a 2-round 100% win streak from outranking a friend with a real sample
# size. No such threshold applies to appearing in the main ranked table.
SQUAD_ROUND_THRESHOLD = 20

SQUAD_SHOUTOUT_CATEGORIES: list[tuple[str, str, str]] = [
    ("win_rate_together", "Best Duo", "won {v}% of rounds together"),
    ("avg_round_win_impact_together", "Dynamic Duo", "{v} avg round-win impact together"),
    ("clutches_together", "Clutch Partners", "{v} clutch round{s} won together"),
    ("traded_together", "Trade Partner", "traded for each other {v} time{s}"),
    ("rounds_together", "Ride or Die", "{v} round{s} played together"),
    ("kill_differential_together", "Lethal Combo", "outkilled opponents by {v} combined, together"),
    ("sugar_daddy_credits_together", "Sugar Daddy", "gave away {v} credits worth of guns together"),
    ("scavenger_credits_together", "Scavenger", "salvaged {v} credits worth of gear together"),
]


def _winner_side(outcome: str | None) -> str | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return "team-1"
    if outcome.startswith("Team B"):
        return "team-2"
    return None


@dataclass
class SharedRound:
    """One round the viewer and one friend both played as teammates.

    `viewer_round_win_impact`/`friend_round_win_impact` are each player's own
    win-gated round impact for that round (kill_impact only if their shared
    team won it, minus death_impact regardless -- see
    app.services.matches.average_round_win_impact for the single-player
    version this mirrors). `clutch` is True if either the viewer or this
    friend resolved a clutch (1-or-2-alive win) this round while teammates.
    `traded` is the combined count of the viewer trading for the friend plus
    the friend trading for the viewer this round. `sugar_daddy_credits`/
    `scavenger_credits` are the viewer's + this friend's own combined
    Sugar Daddy/Scavenger credit values for this round -- see
    app.scoring.credit_events for the per-round math.
    """

    match_id: int
    won: bool
    viewer_round_win_impact: float
    friend_round_win_impact: float
    clutch: bool
    traded: int
    viewer_kills: int
    viewer_deaths: int
    friend_kills: int
    friend_deaths: int
    sugar_daddy_credits: int
    scavenger_credits: int


@dataclass
class PairStats:
    friend_player_id: int
    display_name: str
    most_played_agent_together: str
    matches_together: int
    rounds_together: int
    win_rate_together: float
    avg_round_win_impact_together: float
    clutches_together: int
    traded_together: int
    kill_differential_together: int
    sugar_daddy_credits_together: int
    scavenger_credits_together: int


@dataclass
class SquadOverview:
    squad_size: int
    total_matches_together: int
    total_rounds_together: int
    pairs: list[PairStats] = field(default_factory=list)
    shoutouts: list[PlayerShoutout] = field(default_factory=list)


def _aggregate_pair(
    friend_player_id: int,
    display_name: str,
    agent_counts: Counter,
    shared_rounds: list[SharedRound],
) -> PairStats:
    matches_together = len({r.match_id for r in shared_rounds})
    rounds_together = len(shared_rounds)
    wins = sum(1 for r in shared_rounds if r.won)
    win_rate = wins / rounds_together if rounds_together else 0.0
    round_win_impact_sum = sum(r.viewer_round_win_impact + r.friend_round_win_impact for r in shared_rounds)
    avg_round_win_impact = round_win_impact_sum / rounds_together if rounds_together else 0.0
    clutches = sum(1 for r in shared_rounds if r.clutch)
    traded = sum(r.traded for r in shared_rounds)
    kills = sum(r.viewer_kills + r.friend_kills for r in shared_rounds)
    deaths = sum(r.viewer_deaths + r.friend_deaths for r in shared_rounds)
    top_agent = agent_counts.most_common(1)[0][0] if agent_counts else ""
    sugar_daddy_credits = sum(r.sugar_daddy_credits for r in shared_rounds)
    scavenger_credits = sum(r.scavenger_credits for r in shared_rounds)

    return PairStats(
        friend_player_id=friend_player_id,
        display_name=display_name,
        most_played_agent_together=top_agent,
        matches_together=matches_together,
        rounds_together=rounds_together,
        win_rate_together=win_rate,
        avg_round_win_impact_together=avg_round_win_impact,
        clutches_together=clutches,
        traded_together=traded,
        kill_differential_together=kills - deaths,
        sugar_daddy_credits_together=sugar_daddy_credits,
        scavenger_credits_together=scavenger_credits,
    )


def build_squad_overview(
    pair_shared_rounds: dict[int, list[SharedRound]],
    friend_names: dict[int, str],
    friend_agent_counts: dict[int, Counter],
    viewer_match_ids: set[int],
) -> SquadOverview:
    """Pure aggregation: given every friend's shared rounds with the viewer
    (already fetched from the DB), builds the ranked pair list and shoutouts.
    `viewer_match_ids` is the viewer's own match window (recent-30 or career)
    -- used only for `total_matches_together`, since a friend's own match
    count outside shared matches is irrelevant to this page.
    """
    pairs = [
        _aggregate_pair(fid, friend_names.get(fid, "?"), friend_agent_counts.get(fid, Counter()), rounds)
        for fid, rounds in pair_shared_rounds.items()
    ]
    pairs.sort(key=lambda p: p.matches_together, reverse=True)

    total_rounds_together = sum(p.rounds_together for p in pairs)

    eligible = [p for p in pairs if p.rounds_together >= SQUAD_ROUND_THRESHOLD]
    shoutouts: list[PlayerShoutout] = []
    if eligible:
        roster = [(p.friend_player_id, p.display_name, p.most_played_agent_together) for p in eligible]
        raw_dicts: dict[str, dict[int, int]] = {
            "win_rate_together": {p.friend_player_id: round(p.win_rate_together * 100) for p in eligible},
            "avg_round_win_impact_together": {
                p.friend_player_id: round(p.avg_round_win_impact_together) for p in eligible
            },
            "clutches_together": {p.friend_player_id: p.clutches_together for p in eligible},
            "traded_together": {p.friend_player_id: p.traded_together for p in eligible},
            "rounds_together": {p.friend_player_id: p.rounds_together for p in eligible},
            "kill_differential_together": {p.friend_player_id: p.kill_differential_together for p in eligible},
            "sugar_daddy_credits_together": {p.friend_player_id: p.sugar_daddy_credits_together for p in eligible},
            "scavenger_credits_together": {p.friend_player_id: p.scavenger_credits_together for p in eligible},
        }
        shoutouts = assign_shoutouts(roster, raw_dicts, {}, categories=SQUAD_SHOUTOUT_CATEGORIES)

    return SquadOverview(
        squad_size=len(pairs),
        total_matches_together=len(viewer_match_ids),
        total_rounds_together=total_rounds_together,
        pairs=pairs,
        shoutouts=shoutouts,
    )


def _clutch_resolvers_by_round(
    db: Session, match_id: int, own_mp_ids: set[int], own_team: str
) -> dict[int, set[int]]:
    """round_number -> set of own_mp_ids that were part of a resolved clutch
    (round won while at 1-or-2-alive against an equal-or-larger enemy side)
    in that round, for the single match `match_id`. Mirrors
    app.services.session_stats._build_replay_stats' clutch detection, scoped
    to one match and the viewer's own side (viewer + relevant friends).
    """
    all_match_players = db.query(MatchPlayer).filter_by(match_id=match_id).all()
    opp_mp_ids = {mp.id for mp in all_match_players if mp.id not in own_mp_ids}

    resolvers: dict[int, set[int]] = {}
    rounds = (
        db.query(Round)
        .filter_by(match_id=match_id)
        .options(selectinload(Round.kill_events))
        .order_by(Round.round_number)
        .all()
    )
    for round_row in rounds:
        alive_own = set(own_mp_ids)
        alive_opp = set(opp_mp_ids)
        clutch_state: tuple[int, int, frozenset[int]] | None = None

        events = sorted(round_row.kill_events, key=lambda e: (e.event_time_seconds, e.id))
        for event in events:
            death_id = event.death_match_player_id
            alive_own.discard(death_id)
            alive_opp.discard(death_id)
            own_count, opp_count = len(alive_own), len(alive_opp)
            if own_count in (1, 2) and opp_count >= own_count:
                if clutch_state is None or own_count < clutch_state[0]:
                    clutch_state = (own_count, opp_count, frozenset(alive_own))
            if own_count == 0 or opp_count == 0:
                break

        if clutch_state is not None and _winner_side(round_row.outcome) == own_team:
            _, _, alive_snapshot = clutch_state
            resolvers[round_row.round_number] = set(alive_snapshot)

    return resolvers


def get_squad_overview(db: Session, viewer_player_id: int, match_limit: int | None) -> SquadOverview:
    friend_ids = list_friend_ids(db, viewer_player_id)
    friend_names = (
        {p.id: p.display_name for p in db.query(Player).filter(Player.id.in_(friend_ids)).all()}
        if friend_ids
        else {}
    )

    viewer_query = (
        db.query(MatchPlayer)
        .filter_by(player_id=viewer_player_id)
        .join(Match, Match.id == MatchPlayer.match_id)
    )
    if match_limit is not None:
        viewer_mps = list(
            reversed(
                viewer_query.order_by(Match.played_at.desc().nullsfirst(), Match.id.desc())
                .limit(match_limit)
                .all()
            )
        )
    else:
        viewer_mps = viewer_query.order_by(Match.played_at.nullslast(), Match.id).all()

    viewer_match_ids = {mp.match_id for mp in viewer_mps}
    viewer_mp_by_match: dict[int, MatchPlayer] = {mp.match_id: mp for mp in viewer_mps}

    if not friend_ids or not viewer_match_ids:
        return build_squad_overview({}, {}, {}, viewer_match_ids)

    # Every friend teammate (same match, same team as the viewer) across the viewer's window.
    all_teammates = (
        db.query(MatchPlayer)
        .filter(
            MatchPlayer.match_id.in_(viewer_match_ids),
            MatchPlayer.player_id.in_(friend_ids),
        )
        .all()
    )
    friend_mps_by_match: dict[int, list[MatchPlayer]] = {}
    for mp in all_teammates:
        viewer_mp = viewer_mp_by_match.get(mp.match_id)
        if viewer_mp is None or mp.team != viewer_mp.team:
            continue
        friend_mps_by_match.setdefault(mp.match_id, []).append(mp)

    relevant_match_ids = list(friend_mps_by_match.keys())
    if not relevant_match_ids:
        return build_squad_overview({}, {}, {}, viewer_match_ids)

    pair_shared_rounds: dict[int, list[SharedRound]] = {}
    friend_agent_counts: dict[int, Counter] = {}

    for match_id in relevant_match_ids:
        viewer_mp = viewer_mp_by_match[match_id]
        viewer_team = viewer_mp.team.value if hasattr(viewer_mp.team, "value") else viewer_mp.team
        friend_mps = friend_mps_by_match[match_id]
        friend_mp_ids = {mp.id for mp in friend_mps}
        own_mp_ids = {viewer_mp.id} | friend_mp_ids

        clutch_resolvers = _clutch_resolvers_by_round(db, match_id, own_mp_ids, viewer_team)

        rounds_by_number = {r.round_number: r for r in db.query(Round).filter_by(match_id=match_id).all()}

        impact_rows = (
            db.query(ImpactScore, Round.round_number)
            .join(Round, Round.id == ImpactScore.round_id)
            .filter(
                Round.match_id == match_id,
                ImpactScore.match_player_id.in_(own_mp_ids),
            )
            .all()
        )
        impact_by_mp_and_round: dict[tuple[int, int], ImpactScore] = {
            (score.match_player_id, round_number): score for score, round_number in impact_rows
        }

        kda_rows = (
            db.query(RoundPlayerStat, Round.round_number)
            .join(Round, Round.id == RoundPlayerStat.round_id)
            .filter(
                Round.match_id == match_id,
                RoundPlayerStat.match_player_id.in_(own_mp_ids),
            )
            .all()
        )
        kda_by_mp_and_round: dict[tuple[int, int], RoundPlayerStat] = {
            (stat.match_player_id, round_number): stat for stat, round_number in kda_rows
        }

        agent_by_mp = {viewer_mp.id: viewer_mp.agent, **{mp.id: mp.agent for mp in friend_mps}}
        team_by_mp = {mp_id: viewer_team for mp_id in own_mp_ids}
        round_outcomes = {rn: r.outcome for rn, r in rounds_by_number.items()}
        planted_by_round = {rn: r.planted for rn, r in rounds_by_number.items()}
        stats_by_round: dict[int, dict[int, RoundStat]] = {}
        for (mp_id, round_number), stat in kda_by_mp_and_round.items():
            stats_by_round.setdefault(round_number, {})[mp_id] = RoundStat(
                kills=stat.kills, deaths=stat.deaths, loadout=stat.loadout, remaining=stat.remaining
            )
        credit_events = compute_round_credit_events(
            round_outcomes, planted_by_round, stats_by_round, agent_by_mp, team_by_mp
        )

        for friend_mp in friend_mps:
            for round_number, round_row in rounds_by_number.items():
                viewer_score = impact_by_mp_and_round.get((viewer_mp.id, round_number))
                friend_score = impact_by_mp_and_round.get((friend_mp.id, round_number))
                if viewer_score is None or friend_score is None:
                    continue  # one of them sat out this round (rare, but possible)

                won = _winner_side(round_row.outcome) == viewer_team
                viewer_rwi = (viewer_score.kill_impact if won else 0.0) - viewer_score.death_impact
                friend_rwi = (friend_score.kill_impact if won else 0.0) - friend_score.death_impact

                resolvers = clutch_resolvers.get(round_number, set())
                clutch = viewer_mp.id in resolvers or friend_mp.id in resolvers

                viewer_breakdown = viewer_score.breakdown or {}
                friend_breakdown = friend_score.breakdown or {}
                traded = viewer_breakdown.get("traded_teammate_targets", {}).get(str(friend_mp.id), 0)
                traded += friend_breakdown.get("traded_teammate_targets", {}).get(str(viewer_mp.id), 0)

                viewer_kda = kda_by_mp_and_round.get((viewer_mp.id, round_number))
                friend_kda = kda_by_mp_and_round.get((friend_mp.id, round_number))

                round_events = credit_events.get(round_number, {})
                viewer_sugar_daddy, viewer_scavenger = round_events.get(viewer_mp.id, (0, 0))
                friend_sugar_daddy, friend_scavenger = round_events.get(friend_mp.id, (0, 0))

                pair_shared_rounds.setdefault(friend_mp.player_id, []).append(
                    SharedRound(
                        match_id=match_id,
                        won=won,
                        viewer_round_win_impact=viewer_rwi,
                        friend_round_win_impact=friend_rwi,
                        clutch=clutch,
                        traded=traded,
                        viewer_kills=viewer_kda.kills if viewer_kda else 0,
                        viewer_deaths=viewer_kda.deaths if viewer_kda else 0,
                        friend_kills=friend_kda.kills if friend_kda else 0,
                        friend_deaths=friend_kda.deaths if friend_kda else 0,
                        sugar_daddy_credits=viewer_sugar_daddy + friend_sugar_daddy,
                        scavenger_credits=viewer_scavenger + friend_scavenger,
                    )
                )
            friend_agent_counts.setdefault(friend_mp.player_id, Counter())[friend_mp.agent] += 1

    return build_squad_overview(pair_shared_rounds, friend_names, friend_agent_counts, viewer_match_ids)
