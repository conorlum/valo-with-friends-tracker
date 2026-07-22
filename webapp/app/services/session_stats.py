from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import ImpactScore, KillEvent, MatchPlayer, Player, Round, RoundPlayerStat
from app.services.player_graphs import StateDiagram, build_session_round_win_diagram
from app.services.sessions import SessionSummary

MULTI_KILL_THRESHOLD = 3


@dataclass
class LeaderboardEntry:
    player_id: int
    display_name: str
    average_impact: float
    rounds_played: int


@dataclass
class MatchKda:
    kills: int
    deaths: int
    assists: int

    @property
    def label(self) -> str:
        return f"{self.kills}/{self.deaths}/{self.assists}"


@dataclass
class KdaRow:
    player_id: int
    display_name: str
    by_match: dict[int, MatchKda]
    total_kills: int = 0
    total_deaths: int = 0
    total_assists: int = 0

    @property
    def total_label(self) -> str:
        return f"{self.total_kills}/{self.total_deaths}/{self.total_assists}"


@dataclass
class FunStatEntry:
    display_name: str
    value: int
    match_id: int | None = None
    round_number: int | None = None
    opponent_display_name: str | None = None


@dataclass
class SessionFunStats:
    biggest_multi_kill: FunStatEntry | None = None
    longest_kill_streak: FunStatEntry | None = None
    most_multi_kills: FunStatEntry | None = None
    most_kills_on_enemy_top_frag: FunStatEntry | None = None
    most_deaths_to_enemy_bottom_frag: FunStatEntry | None = None


@dataclass
class SessionStats:
    leaderboard: list[LeaderboardEntry]
    kda_rows: list[KdaRow]
    round_win_diagram: StateDiagram
    fun_stats: SessionFunStats


def get_session_stats(db: Session, session: SessionSummary) -> SessionStats:
    match_ids = [m.id for m in session.matches]
    roster_player_ids = session.roster_player_ids

    round_win_diagram = build_session_round_win_diagram(session.matches, session.team_by_match)

    if not match_ids or not roster_player_ids:
        return SessionStats(
            leaderboard=[],
            kda_rows=[],
            round_win_diagram=round_win_diagram,
            fun_stats=SessionFunStats(),
        )

    players_by_id = {p.id: p.display_name for p in db.query(Player).all()}

    our_match_players = (
        db.query(MatchPlayer)
        .filter(MatchPlayer.match_id.in_(match_ids), MatchPlayer.player_id.in_(roster_player_ids))
        .all()
    )
    # match_player_id -> player_id, restricted to this session's roster.
    our_mp_to_player: dict[int, int] = {mp.id: mp.player_id for mp in our_match_players}

    leaderboard = _build_leaderboard(db, our_mp_to_player, players_by_id)
    kda_rows = _build_kda_rows(db, match_ids, our_mp_to_player, players_by_id)
    fun_stats = _build_fun_stats(db, session, our_mp_to_player, players_by_id)

    return SessionStats(
        leaderboard=leaderboard,
        kda_rows=kda_rows,
        round_win_diagram=round_win_diagram,
        fun_stats=fun_stats,
    )


def _build_leaderboard(
    db: Session, our_mp_to_player: dict[int, int], players_by_id: dict[int, str]
) -> list[LeaderboardEntry]:
    rows = (
        db.query(ImpactScore.match_player_id, ImpactScore.impact)
        .filter(ImpactScore.match_player_id.in_(our_mp_to_player.keys()))
        .all()
    )
    totals: dict[int, list[float]] = {}
    for match_player_id, impact in rows:
        player_id = our_mp_to_player[match_player_id]
        totals.setdefault(player_id, []).append(impact)

    entries = [
        LeaderboardEntry(
            player_id=player_id,
            display_name=players_by_id.get(player_id, "?"),
            average_impact=sum(values) / len(values),
            rounds_played=len(values),
        )
        for player_id, values in totals.items()
    ]
    entries.sort(key=lambda e: e.average_impact, reverse=True)
    return entries


def _build_kda_rows(
    db: Session,
    match_ids: list[int],
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
) -> list[KdaRow]:
    rows = (
        db.query(
            RoundPlayerStat.match_player_id,
            Round.match_id,
            RoundPlayerStat.kills,
            RoundPlayerStat.deaths,
            RoundPlayerStat.assists,
        )
        .join(Round, Round.id == RoundPlayerStat.round_id)
        .filter(
            Round.match_id.in_(match_ids),
            RoundPlayerStat.match_player_id.in_(our_mp_to_player.keys()),
        )
        .all()
    )

    rows_by_player: dict[int, KdaRow] = {}
    for match_player_id, match_id, kills, deaths, assists in rows:
        player_id = our_mp_to_player[match_player_id]
        row = rows_by_player.get(player_id)
        if row is None:
            row = KdaRow(player_id=player_id, display_name=players_by_id.get(player_id, "?"), by_match={})
            rows_by_player[player_id] = row
        match_kda = row.by_match.get(match_id)
        if match_kda is None:
            match_kda = MatchKda(kills=0, deaths=0, assists=0)
            row.by_match[match_id] = match_kda
        match_kda.kills += kills
        match_kda.deaths += deaths
        match_kda.assists += assists
        row.total_kills += kills
        row.total_deaths += deaths
        row.total_assists += assists

    kda_rows = list(rows_by_player.values())
    kda_rows.sort(key=lambda r: r.total_kills, reverse=True)
    return kda_rows


def _build_fun_stats(
    db: Session,
    session: SessionSummary,
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
) -> SessionFunStats:
    match_ids = [m.id for m in session.matches]

    # --- Per-round kill counts for our players (multi-kill stats) ---
    round_stat_rows = (
        db.query(
            RoundPlayerStat.match_player_id,
            Round.match_id,
            Round.round_number,
            RoundPlayerStat.kills,
        )
        .join(Round, Round.id == RoundPlayerStat.round_id)
        .filter(
            Round.match_id.in_(match_ids),
            RoundPlayerStat.match_player_id.in_(our_mp_to_player.keys()),
        )
        .all()
    )

    biggest_multi_kill: FunStatEntry | None = None
    multi_kill_counts: dict[int, int] = {}
    for match_player_id, match_id, round_number, kills in round_stat_rows:
        player_id = our_mp_to_player[match_player_id]
        if kills >= MULTI_KILL_THRESHOLD:
            multi_kill_counts[player_id] = multi_kill_counts.get(player_id, 0) + 1
        if biggest_multi_kill is None or kills > biggest_multi_kill.value:
            biggest_multi_kill = FunStatEntry(
                display_name=players_by_id.get(player_id, "?"),
                value=kills,
                match_id=match_id,
                round_number=round_number,
            )

    most_multi_kills = None
    if multi_kill_counts:
        best_player_id = max(multi_kill_counts, key=lambda pid: multi_kill_counts[pid])
        most_multi_kills = FunStatEntry(
            display_name=players_by_id.get(best_player_id, "?"),
            value=multi_kill_counts[best_player_id],
        )

    # --- Per-match opponent top/bottom frag, and streaks across the session ---
    kills_on_top_frag: dict[int, int] = {}
    deaths_to_bottom_frag: dict[int, int] = {}
    top_frag_opponent_name: dict[int, str] = {}
    bottom_frag_opponent_name: dict[int, str] = {}

    current_streak: dict[int, int] = {}
    max_streak: dict[int, int] = {}

    for match in session.matches:
        our_side = session.team_by_match.get(match.id)

        match_players = db.query(MatchPlayer).filter_by(match_id=match.id).all()
        team_by_mp: dict[int, str] = {
            mp.id: (mp.team.value if hasattr(mp.team, "value") else mp.team) for mp in match_players
        }

        opp_top_frag_mp_id: int | None = None
        opp_bottom_frag_mp_id: int | None = None
        if our_side is not None:
            opp_side = "team-2" if our_side == "team-1" else "team-1"
            opp_mp_ids = [mp.id for mp in match_players if team_by_mp[mp.id] == opp_side]
            if opp_mp_ids:
                opp_totals: dict[int, int] = {}
                for mp_id, kills in (
                    db.query(RoundPlayerStat.match_player_id, RoundPlayerStat.kills)
                    .join(Round, Round.id == RoundPlayerStat.round_id)
                    .filter(Round.match_id == match.id, RoundPlayerStat.match_player_id.in_(opp_mp_ids))
                    .all()
                ):
                    opp_totals[mp_id] = opp_totals.get(mp_id, 0) + kills
                if opp_totals:
                    opp_top_frag_mp_id = max(opp_totals, key=lambda mp_id: (opp_totals[mp_id], -mp_id))
                    opp_bottom_frag_mp_id = min(opp_totals, key=lambda mp_id: (opp_totals[mp_id], mp_id))

        mp_to_player_name = {
            mp.id: players_by_id.get(mp.player_id, "?") for mp in match_players
        }

        rounds = db.query(Round).filter_by(match_id=match.id).order_by(Round.round_number).all()
        for round_row in rounds:
            events = (
                db.query(KillEvent)
                .filter_by(round_id=round_row.id)
                .order_by(KillEvent.event_time_seconds)
                .all()
            )
            for event in events:
                killer_id = event.killer_match_player_id
                death_id = event.death_match_player_id

                if killer_id is not None and killer_id in our_mp_to_player:
                    player_id = our_mp_to_player[killer_id]
                    current_streak[player_id] = current_streak.get(player_id, 0) + 1
                    if current_streak[player_id] > max_streak.get(player_id, 0):
                        max_streak[player_id] = current_streak[player_id]

                    if opp_top_frag_mp_id is not None and death_id == opp_top_frag_mp_id:
                        kills_on_top_frag[player_id] = kills_on_top_frag.get(player_id, 0) + 1
                        top_frag_opponent_name[player_id] = mp_to_player_name.get(opp_top_frag_mp_id, "?")

                if death_id is not None and death_id in our_mp_to_player:
                    player_id = our_mp_to_player[death_id]
                    current_streak[player_id] = 0

                    if opp_bottom_frag_mp_id is not None and killer_id == opp_bottom_frag_mp_id:
                        deaths_to_bottom_frag[player_id] = deaths_to_bottom_frag.get(player_id, 0) + 1
                        bottom_frag_opponent_name[player_id] = mp_to_player_name.get(
                            opp_bottom_frag_mp_id, "?"
                        )

    longest_kill_streak = None
    if max_streak:
        best_player_id = max(max_streak, key=lambda pid: max_streak[pid])
        longest_kill_streak = FunStatEntry(
            display_name=players_by_id.get(best_player_id, "?"), value=max_streak[best_player_id]
        )

    most_kills_on_enemy_top_frag = None
    if kills_on_top_frag:
        best_player_id = max(kills_on_top_frag, key=lambda pid: kills_on_top_frag[pid])
        most_kills_on_enemy_top_frag = FunStatEntry(
            display_name=players_by_id.get(best_player_id, "?"),
            value=kills_on_top_frag[best_player_id],
            opponent_display_name=top_frag_opponent_name.get(best_player_id),
        )

    most_deaths_to_enemy_bottom_frag = None
    if deaths_to_bottom_frag:
        best_player_id = max(deaths_to_bottom_frag, key=lambda pid: deaths_to_bottom_frag[pid])
        most_deaths_to_enemy_bottom_frag = FunStatEntry(
            display_name=players_by_id.get(best_player_id, "?"),
            value=deaths_to_bottom_frag[best_player_id],
            opponent_display_name=bottom_frag_opponent_name.get(best_player_id),
        )

    return SessionFunStats(
        biggest_multi_kill=biggest_multi_kill,
        longest_kill_streak=longest_kill_streak,
        most_multi_kills=most_multi_kills,
        most_kills_on_enemy_top_frag=most_kills_on_enemy_top_frag,
        most_deaths_to_enemy_bottom_frag=most_deaths_to_enemy_bottom_frag,
    )
