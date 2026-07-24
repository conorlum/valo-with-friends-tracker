from dataclasses import dataclass

from sqlalchemy.orm import Session, aliased

from app.models import ImpactScore, KillEvent, MatchPlayer, Player, Round, RoundPlayerStat
from app.scoring.impact import FORCE_THRESHOLD, econ_tier_name
from app.services.friends import list_friend_ids
from app.services.player_graphs import StateDiagram, build_session_round_win_diagram
from app.services.shoutouts import PlayerShoutout, assign_shoutouts
from app.services.sessions import SessionSummary

MULTI_KILL_THRESHOLD = 3
# Operator (4700) + at minimum light shields (400): a round where the player
# clearly bought (and presumably played) an Operator.
OP_LOADOUT_THRESHOLD = 5100
# event_time_seconds is round-relative (0 = round start); an "entry" kill lands
# in this opening window, a "late" kill lands at/after this mark.
ENTRY_KILL_WINDOW_SECONDS = 20
LATE_KILL_MARK_SECONDS = 60


@dataclass
class LeaderboardEntry:
    player_id: int
    display_name: str
    average_impact: float
    rounds_played: int
    rounds_won: int = 0
    rounds_lost: int = 0


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
    runner_up: "FunStatEntry | None" = None


@dataclass
class SessionFunStats:
    biggest_multi_kill: FunStatEntry | None = None
    longest_kill_streak: FunStatEntry | None = None
    most_multi_kills: FunStatEntry | None = None
    most_kills_on_enemy_top_frag: FunStatEntry | None = None
    most_deaths_to_enemy_bottom_frag: FunStatEntry | None = None
    most_clutches: FunStatEntry | None = None
    round_mvp: FunStatEntry | None = None
    eco_frags: FunStatEntry | None = None
    op_crutch: FunStatEntry | None = None
    most_trades_made: FunStatEntry | None = None
    most_traded: FunStatEntry | None = None
    most_econ_upset_deaths: FunStatEntry | None = None
    most_round_changer: FunStatEntry | None = None
    most_xvx_kills: FunStatEntry | None = None
    most_spike_deaths: FunStatEntry | None = None
    post_plant_menace: FunStatEntry | None = None
    most_ghost_rounds: FunStatEntry | None = None
    most_entry_kills: FunStatEntry | None = None
    most_late_round_kills: FunStatEntry | None = None
    most_first_deaths_in_losses: FunStatEntry | None = None
    most_last_alive_in_wipes: FunStatEntry | None = None


@dataclass
class SessionStats:
    leaderboard: list[LeaderboardEntry]
    kda_rows: list[KdaRow]
    round_win_diagram: StateDiagram
    fun_stats: SessionFunStats
    shoutouts: list[PlayerShoutout]


def get_session_stats(
    db: Session, session: SessionSummary, viewer_player_id: int | None = None
) -> SessionStats:
    match_ids = [m.id for m in session.matches]
    roster_player_ids = session.roster_player_ids

    round_win_diagram = build_session_round_win_diagram(session.matches, session.team_by_match)

    if not match_ids or not roster_player_ids:
        return SessionStats(
            leaderboard=[],
            kda_rows=[],
            round_win_diagram=round_win_diagram,
            fun_stats=SessionFunStats(),
            shoutouts=[],
        )

    players_by_id = {p.id: p.display_name for p in db.query(Player).all()}

    our_match_players = (
        db.query(MatchPlayer)
        .filter(MatchPlayer.match_id.in_(match_ids), MatchPlayer.player_id.in_(roster_player_ids))
        .all()
    )
    # match_player_id -> player_id, restricted to this session's roster.
    our_mp_to_player: dict[int, int] = {mp.id: mp.player_id for mp in our_match_players}

    leaderboard = _build_leaderboard(db, our_mp_to_player, players_by_id, session.team_by_match)
    kda_rows = _build_kda_rows(db, match_ids, our_mp_to_player, players_by_id)
    biggest_multi_kill, raw_counts = _compute_raw_session_counts(db, session, our_mp_to_player, players_by_id)
    fun_stats = _build_fun_stats(db, session, our_mp_to_player, players_by_id, biggest_multi_kill, raw_counts)
    shoutouts = _build_shoutouts(raw_counts, leaderboard, players_by_id)
    if viewer_player_id is not None:
        friend_ids = list_friend_ids(db, viewer_player_id) | {viewer_player_id}
        shoutouts = [s for s in shoutouts if s.player_id in friend_ids]

    return SessionStats(
        leaderboard=leaderboard,
        kda_rows=kda_rows,
        round_win_diagram=round_win_diagram,
        fun_stats=fun_stats,
        shoutouts=shoutouts,
    )


def _winner_side(outcome: str | None) -> str | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return "team-1"
    if outcome.startswith("Team B"):
        return "team-2"
    return None


def _build_leaderboard(
    db: Session,
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
    team_by_match: dict[int, str],
) -> list[LeaderboardEntry]:
    rows = (
        db.query(ImpactScore.match_player_id, ImpactScore.impact, Round.match_id, Round.outcome)
        .join(Round, Round.id == ImpactScore.round_id)
        .filter(ImpactScore.match_player_id.in_(our_mp_to_player.keys()))
        .all()
    )
    impacts: dict[int, list[float]] = {}
    wins: dict[int, int] = {}
    losses: dict[int, int] = {}
    for match_player_id, impact, match_id, outcome in rows:
        player_id = our_mp_to_player[match_player_id]
        impacts.setdefault(player_id, []).append(impact)

        our_side = team_by_match.get(match_id)
        winner = _winner_side(outcome)
        if our_side is None or winner is None:
            continue
        if winner == our_side:
            wins[player_id] = wins.get(player_id, 0) + 1
        else:
            losses[player_id] = losses.get(player_id, 0) + 1

    entries = [
        LeaderboardEntry(
            player_id=player_id,
            display_name=players_by_id.get(player_id, "?"),
            average_impact=sum(values) / len(values),
            rounds_played=len(values),
            rounds_won=wins.get(player_id, 0),
            rounds_lost=losses.get(player_id, 0),
        )
        for player_id, values in impacts.items()
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


def _top_entry(counts: dict[int, int], players_by_id: dict[int, str]) -> FunStatEntry | None:
    """Top entry for a per-player count, with the runner-up (2nd place)
    attached so the template can show both.
    """
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], players_by_id.get(kv[0], "?")))
    best_player_id, best_value = ranked[0]
    entry = FunStatEntry(display_name=players_by_id.get(best_player_id, "?"), value=best_value)
    if len(ranked) > 1:
        runner_up_player_id, runner_up_value = ranked[1]
        entry.runner_up = FunStatEntry(
            display_name=players_by_id.get(runner_up_player_id, "?"), value=runner_up_value
        )
    return entry


@dataclass
class _RawSessionCounts:
    """Raw per-player counts behind the Fun Stats tiles, kept around (rather
    than immediately collapsed to a single winner) so the Shoutouts builder
    can also draw from the exact same numbers.
    """

    multi_kill_counts: dict[int, int]
    eco_kill_counts: dict[int, int]
    op_kill_counts: dict[int, int]
    max_streak: dict[int, int]
    kills_on_top_frag: dict[int, int]
    deaths_to_bottom_frag: dict[int, int]
    clutch_counts: dict[int, int]
    xvx_kill_counts: dict[int, int]
    round_changer_kill_counts: dict[int, int]
    first_death_in_loss_counts: dict[int, int]
    last_alive_in_wipe_counts: dict[int, int]
    mvp_counts: dict[int, int]
    best_round_impact: dict[int, tuple[float, int, int]]
    traded_teammate_totals: dict[int, int]
    traded_by_teammate_totals: dict[int, int]
    post_plant_kill_counts: dict[int, int]
    entry_kill_counts: dict[int, int]
    late_kill_counts: dict[int, int]


def _compute_raw_session_counts(
    db: Session,
    session: SessionSummary,
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
) -> tuple[FunStatEntry | None, _RawSessionCounts]:
    """Runs every per-player replay/aggregation query for the session once.
    Returns the biggest-multi-kill entry separately (it needs tie-handling
    that doesn't fit the raw-counts shape) alongside the raw counts bundle.
    """
    match_ids = [m.id for m in session.matches]

    biggest_multi_kill, multi_kill_counts, eco_kill_counts, op_kill_counts = _build_round_kill_stats(
        db, match_ids, our_mp_to_player, players_by_id
    )
    (
        max_streak,
        kills_on_top_frag,
        deaths_to_bottom_frag,
        clutch_counts,
        xvx_kill_counts,
        round_changer_kill_counts,
        first_death_in_loss_counts,
        last_alive_in_wipe_counts,
    ) = _build_replay_stats(db, session, our_mp_to_player, players_by_id)
    mvp_counts, best_round_impact = _build_round_mvp(db, match_ids, our_mp_to_player, players_by_id)
    traded_teammate_totals, traded_by_teammate_totals = _build_trade_stats(db, our_mp_to_player, players_by_id)
    post_plant_kill_counts = _build_post_plant_menace_stats(db, match_ids, our_mp_to_player, players_by_id)
    entry_kill_counts = _build_entry_kill_stats(db, match_ids, our_mp_to_player, players_by_id)
    late_kill_counts = _build_late_kill_stats(db, match_ids, our_mp_to_player, players_by_id)

    return biggest_multi_kill, _RawSessionCounts(
        multi_kill_counts=multi_kill_counts,
        eco_kill_counts=eco_kill_counts,
        op_kill_counts=op_kill_counts,
        max_streak=max_streak,
        kills_on_top_frag=kills_on_top_frag,
        deaths_to_bottom_frag=deaths_to_bottom_frag,
        clutch_counts=clutch_counts,
        xvx_kill_counts=xvx_kill_counts,
        round_changer_kill_counts=round_changer_kill_counts,
        first_death_in_loss_counts=first_death_in_loss_counts,
        last_alive_in_wipe_counts=last_alive_in_wipe_counts,
        mvp_counts=mvp_counts,
        best_round_impact=best_round_impact,
        traded_teammate_totals=traded_teammate_totals,
        traded_by_teammate_totals=traded_by_teammate_totals,
        post_plant_kill_counts=post_plant_kill_counts,
        entry_kill_counts=entry_kill_counts,
        late_kill_counts=late_kill_counts,
    )


def _build_fun_stats(
    db: Session,
    session: SessionSummary,
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
    biggest_multi_kill: FunStatEntry | None,
    raw: _RawSessionCounts,
) -> SessionFunStats:
    match_ids = [m.id for m in session.matches]

    most_econ_upset_deaths = _build_econ_upset_stats(db, match_ids, our_mp_to_player, players_by_id)
    most_spike_deaths = _build_spike_death_stats(db, match_ids, our_mp_to_player, players_by_id)
    most_ghost_rounds = _build_ghost_stats(db, match_ids, our_mp_to_player, players_by_id)

    return SessionFunStats(
        biggest_multi_kill=biggest_multi_kill,
        longest_kill_streak=_top_entry(raw.max_streak, players_by_id),
        most_multi_kills=_top_entry(raw.multi_kill_counts, players_by_id),
        most_kills_on_enemy_top_frag=_top_entry(raw.kills_on_top_frag, players_by_id),
        most_deaths_to_enemy_bottom_frag=_top_entry(raw.deaths_to_bottom_frag, players_by_id),
        most_clutches=_top_entry(raw.clutch_counts, players_by_id),
        round_mvp=_top_entry(raw.mvp_counts, players_by_id),
        eco_frags=_top_entry(raw.eco_kill_counts, players_by_id),
        op_crutch=_top_entry(raw.op_kill_counts, players_by_id),
        most_trades_made=_top_entry(raw.traded_teammate_totals, players_by_id),
        most_traded=_top_entry(raw.traded_by_teammate_totals, players_by_id),
        most_econ_upset_deaths=most_econ_upset_deaths,
        most_round_changer=_top_entry(raw.round_changer_kill_counts, players_by_id),
        most_xvx_kills=_top_entry(raw.xvx_kill_counts, players_by_id),
        most_spike_deaths=most_spike_deaths,
        post_plant_menace=_top_entry(raw.post_plant_kill_counts, players_by_id),
        most_ghost_rounds=most_ghost_rounds,
        most_entry_kills=_top_entry(raw.entry_kill_counts, players_by_id),
        most_late_round_kills=_top_entry(raw.late_kill_counts, players_by_id),
        most_first_deaths_in_losses=_top_entry(raw.first_death_in_loss_counts, players_by_id),
        most_last_alive_in_wipes=_top_entry(raw.last_alive_in_wipe_counts, players_by_id),
    )


def _build_round_kill_stats(
    db: Session,
    match_ids: list[int],
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
) -> tuple[FunStatEntry | None, dict[int, int], dict[int, int], dict[int, int]]:
    """Per-round kill counts (+ that round's loadout) for our players.

    Drives: biggest single-round multi-kill (returned directly, since it needs
    the tie-handling in `_build_biggest_multi_kill_entry`), plus raw per-player
    counts of 3+ kill rounds, kills landed while on an eco/save buy, and kills
    landed while carrying an Operator-tier loadout (returned raw so the
    Shoutouts builder can also draw from them).
    """
    rows = (
        db.query(
            RoundPlayerStat.match_player_id,
            Round.match_id,
            Round.round_number,
            RoundPlayerStat.kills,
            RoundPlayerStat.loadout,
        )
        .join(Round, Round.id == RoundPlayerStat.round_id)
        .filter(
            Round.match_id.in_(match_ids),
            RoundPlayerStat.match_player_id.in_(our_mp_to_player.keys()),
        )
        .all()
    )

    best_kill_count: int | None = None
    best_kill_rows: list[tuple[int, int, int]] = []  # (player_id, match_id, round_number)
    multi_kill_counts: dict[int, int] = {}
    eco_kill_counts: dict[int, int] = {}
    op_kill_counts: dict[int, int] = {}

    for match_player_id, match_id, round_number, kills, loadout in rows:
        player_id = our_mp_to_player[match_player_id]

        if kills >= MULTI_KILL_THRESHOLD:
            multi_kill_counts[player_id] = multi_kill_counts.get(player_id, 0) + 1
        if best_kill_count is None or kills > best_kill_count:
            best_kill_count = kills
            best_kill_rows = [(player_id, match_id, round_number)]
        elif kills == best_kill_count:
            best_kill_rows.append((player_id, match_id, round_number))

        if loadout < FORCE_THRESHOLD:
            eco_kill_counts[player_id] = eco_kill_counts.get(player_id, 0) + kills
        if loadout >= OP_LOADOUT_THRESHOLD:
            op_kill_counts[player_id] = op_kill_counts.get(player_id, 0) + kills

    biggest_multi_kill = _build_biggest_multi_kill_entry(best_kill_count, best_kill_rows, players_by_id)

    return (biggest_multi_kill, multi_kill_counts, eco_kill_counts, op_kill_counts)


def _build_biggest_multi_kill_entry(
    best_kill_count: int | None,
    best_kill_rows: list[tuple[int, int, int]],
    players_by_id: dict[int, str],
) -> FunStatEntry | None:
    """If a single player holds the session's top single-round kill count
    outright, name that round/map. If 2+ players tied for it, name them all
    instead -- a specific round/map wouldn't be meaningful for a tie.
    """
    if best_kill_count is None or not best_kill_rows:
        return None

    distinct_player_ids = list(dict.fromkeys(pid for pid, _, _ in best_kill_rows))
    if len(distinct_player_ids) == 1:
        player_id, match_id, round_number = best_kill_rows[0]
        return FunStatEntry(
            display_name=players_by_id.get(player_id, "?"),
            value=best_kill_count,
            match_id=match_id,
            round_number=round_number,
        )

    names = [players_by_id.get(pid, "?").split("#", 1)[0] for pid in sorted(distinct_player_ids, key=lambda pid: players_by_id.get(pid, "?"))]
    return FunStatEntry(display_name=", ".join(names), value=best_kill_count)


def _build_replay_stats(
    db: Session,
    session: SessionSummary,
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
) -> tuple[
    dict[int, int],
    dict[int, int],
    dict[int, int],
    dict[int, int],
    dict[int, int],
    dict[int, int],
    dict[int, int],
    dict[int, int],
]:
    """Replays every round's kill feed in order to derive stats that depend on
    who was alive when: no-death kill streaks, kills on/deaths to each
    match's enemy top/bottom fragger, clutches (won a round while down to 1 or
    2 alive against an equal-or-larger enemy side), kills landed in an
    even-numbers (XvX) fight, kills landed while outnumbered (a "round
    changer" -- turning the tide from a numbers disadvantage), who died first
    (of ours) in rounds we lost, and who was the last of ours standing (before
    also dying) in rounds where our whole 5-stack got wiped.
    """
    kills_on_top_frag: dict[int, int] = {}
    deaths_to_bottom_frag: dict[int, int] = {}
    clutch_counts: dict[int, int] = {}
    xvx_kill_counts: dict[int, int] = {}
    round_changer_kill_counts: dict[int, int] = {}
    first_death_in_loss_counts: dict[int, int] = {}
    last_alive_in_wipe_counts: dict[int, int] = {}

    current_streak: dict[int, int] = {}
    max_streak: dict[int, int] = {}

    for match in session.matches:
        our_side = session.team_by_match.get(match.id)

        match_players = db.query(MatchPlayer).filter_by(match_id=match.id).all()
        team_by_mp: dict[int, str] = {
            mp.id: (mp.team.value if hasattr(mp.team, "value") else mp.team) for mp in match_players
        }
        own_mp_ids = {mp.id for mp in match_players if mp.id in our_mp_to_player}
        opp_mp_ids = {mp.id for mp in match_players if mp.id not in our_mp_to_player}

        opp_top_frag_mp_id: int | None = None
        opp_bottom_frag_mp_id: int | None = None
        if our_side is not None and opp_mp_ids:
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

        rounds = db.query(Round).filter_by(match_id=match.id).order_by(Round.round_number).all()
        for round_row in rounds:
            round_won_by_us = our_side is not None and _winner_side(round_row.outcome) == our_side
            alive_own = set(own_mp_ids)
            alive_opp = set(opp_mp_ids)
            # Most extreme (own_count, opp_count, alive-own-snapshot) reached while
            # outnumbered-or-even at 1 or 2 alive -- the clutch situation, if any,
            # this round resolved from.
            clutch_state: tuple[int, int, frozenset[int]] | None = None
            first_own_death_id: int | None = None

            events = (
                db.query(KillEvent)
                .filter_by(round_id=round_row.id)
                .order_by(KillEvent.event_time_seconds)
                .all()
            )
            for event in events:
                killer_id = event.killer_match_player_id
                death_id = event.death_match_player_id
                pre_own_count, pre_opp_count = len(alive_own), len(alive_opp)

                if (
                    killer_id is not None
                    and death_id is not None
                    and killer_id != death_id
                    and killer_id in our_mp_to_player
                    and pre_own_count == pre_opp_count
                    and pre_own_count > 0
                ):
                    xvx_player_id = our_mp_to_player[killer_id]
                    xvx_kill_counts[xvx_player_id] = xvx_kill_counts.get(xvx_player_id, 0) + 1

                if (
                    killer_id is not None
                    and death_id is not None
                    and killer_id != death_id
                    and killer_id in our_mp_to_player
                    and pre_own_count < pre_opp_count
                ):
                    changer_player_id = our_mp_to_player[killer_id]
                    round_changer_kill_counts[changer_player_id] = (
                        round_changer_kill_counts.get(changer_player_id, 0) + 1
                    )

                if killer_id is not None and killer_id in our_mp_to_player:
                    player_id = our_mp_to_player[killer_id]
                    current_streak[player_id] = current_streak.get(player_id, 0) + 1
                    if current_streak[player_id] > max_streak.get(player_id, 0):
                        max_streak[player_id] = current_streak[player_id]

                    if opp_top_frag_mp_id is not None and death_id == opp_top_frag_mp_id:
                        kills_on_top_frag[player_id] = kills_on_top_frag.get(player_id, 0) + 1

                if death_id is not None and death_id in our_mp_to_player:
                    player_id = our_mp_to_player[death_id]
                    current_streak[player_id] = 0

                    if opp_bottom_frag_mp_id is not None and killer_id == opp_bottom_frag_mp_id:
                        deaths_to_bottom_frag[player_id] = deaths_to_bottom_frag.get(player_id, 0) + 1

                    if first_own_death_id is None:
                        first_own_death_id = death_id

                alive_own.discard(death_id)
                alive_opp.discard(death_id)

                own_count, opp_count = len(alive_own), len(alive_opp)
                if own_count in (1, 2) and opp_count >= own_count:
                    if clutch_state is None or own_count < clutch_state[0]:
                        clutch_state = (own_count, opp_count, frozenset(alive_own))

                if (
                    own_count == 0
                    and len(own_mp_ids) == 5
                    and death_id is not None
                    and death_id in our_mp_to_player
                ):
                    last_player_id = our_mp_to_player[death_id]
                    last_alive_in_wipe_counts[last_player_id] = (
                        last_alive_in_wipe_counts.get(last_player_id, 0) + 1
                    )

                if own_count == 0 or opp_count == 0:
                    break

            if round_won_by_us and clutch_state is not None:
                _, _, alive_snapshot = clutch_state
                for mp_id in alive_snapshot:
                    player_id = our_mp_to_player[mp_id]
                    clutch_counts[player_id] = clutch_counts.get(player_id, 0) + 1

            if our_side is not None and not round_won_by_us and first_own_death_id is not None:
                first_death_player_id = our_mp_to_player[first_own_death_id]
                first_death_in_loss_counts[first_death_player_id] = (
                    first_death_in_loss_counts.get(first_death_player_id, 0) + 1
                )

    return (
        max_streak,
        kills_on_top_frag,
        deaths_to_bottom_frag,
        clutch_counts,
        xvx_kill_counts,
        round_changer_kill_counts,
        first_death_in_loss_counts,
        last_alive_in_wipe_counts,
    )


def _build_round_mvp(
    db: Session,
    match_ids: list[int],
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
) -> tuple[dict[int, int], dict[int, tuple[float, int, int]]]:
    """Counts, for every round in the session (both teams), how often one of
    our players had the single highest Impact score of anyone in that round
    -- and separately, for our own players only, each one's single best
    Impact round of the session (value, match_id, round_number), used as a
    Shoutouts fallback ("Highlight Reel").
    """
    rows = (
        db.query(
            ImpactScore.round_id,
            ImpactScore.match_player_id,
            ImpactScore.impact,
            Round.match_id,
            Round.round_number,
        )
        .join(Round, Round.id == ImpactScore.round_id)
        .filter(Round.match_id.in_(match_ids))
        .all()
    )
    best_by_round: dict[int, tuple[int, float]] = {}
    best_round_impact: dict[int, tuple[float, int, int]] = {}
    for round_id, match_player_id, impact, match_id, round_number in rows:
        current = best_by_round.get(round_id)
        if current is None or impact > current[1]:
            best_by_round[round_id] = (match_player_id, impact)

        player_id = our_mp_to_player.get(match_player_id)
        if player_id is not None:
            current_best = best_round_impact.get(player_id)
            if current_best is None or impact > current_best[0]:
                best_round_impact[player_id] = (impact, match_id, round_number)

    mvp_counts: dict[int, int] = {}
    for match_player_id, _impact in best_by_round.values():
        player_id = our_mp_to_player.get(match_player_id)
        if player_id is not None:
            mvp_counts[player_id] = mvp_counts.get(player_id, 0) + 1

    return mvp_counts, best_round_impact


def _build_trade_stats(
    db: Session,
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
) -> tuple[dict[int, int], dict[int, int]]:
    """Sums the impact scorer's traded_teammate/traded_by_teammate breakdown
    counts across the session: who avenged a teammate's death most (traded
    the most), and who got avenged the most (got traded the most).
    """
    rows = (
        db.query(ImpactScore.match_player_id, ImpactScore.breakdown)
        .filter(ImpactScore.match_player_id.in_(our_mp_to_player.keys()))
        .all()
    )
    traded_teammate_totals: dict[int, int] = {}
    traded_by_teammate_totals: dict[int, int] = {}
    for match_player_id, breakdown in rows:
        player_id = our_mp_to_player[match_player_id]
        breakdown = breakdown or {}
        traded_teammate_totals[player_id] = traded_teammate_totals.get(player_id, 0) + breakdown.get(
            "traded_teammate", 0
        )
        traded_by_teammate_totals[player_id] = traded_by_teammate_totals.get(
            player_id, 0
        ) + breakdown.get("traded_by_teammate", 0)

    return (traded_teammate_totals, traded_by_teammate_totals)


# (killer's econ tier, victim's econ tier) pairs counted as an "upset" death: a
# cheaper-buying enemy killed one of our players on a pricier buy.
_ECON_UPSET_TIER_PAIRS = {
    ("ECO", "FORCE"),
    ("ECO", "FULL_BUY"),
    ("SAVE", "FORCE"),
    ("SAVE", "FULL_BUY"),
}


def _build_econ_upset_stats(
    db: Session,
    match_ids: list[int],
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
) -> FunStatEntry | None:
    """Counts deaths of our players to a cheaper-buying enemy: killer on
    eco/save, victim (one of ours) on force/full buy -- the "upset" death.
    """
    KillerStat = aliased(RoundPlayerStat)
    VictimStat = aliased(RoundPlayerStat)

    rows = (
        db.query(
            KillEvent.death_match_player_id,
            KillerStat.loadout,
            VictimStat.loadout,
        )
        .join(Round, Round.id == KillEvent.round_id)
        .join(
            VictimStat,
            (VictimStat.round_id == KillEvent.round_id)
            & (VictimStat.match_player_id == KillEvent.death_match_player_id),
        )
        .join(
            KillerStat,
            (KillerStat.round_id == KillEvent.round_id)
            & (KillerStat.match_player_id == KillEvent.killer_match_player_id),
        )
        .filter(
            Round.match_id.in_(match_ids),
            KillEvent.death_match_player_id.in_(our_mp_to_player.keys()),
            KillEvent.killer_match_player_id.isnot(None),
            KillEvent.killer_match_player_id != KillEvent.death_match_player_id,
        )
        .all()
    )

    upset_death_counts: dict[int, int] = {}
    for death_mp_id, killer_loadout, victim_loadout in rows:
        tier_pair = (econ_tier_name(killer_loadout), econ_tier_name(victim_loadout))
        if tier_pair not in _ECON_UPSET_TIER_PAIRS:
            continue
        player_id = our_mp_to_player[death_mp_id]
        upset_death_counts[player_id] = upset_death_counts.get(player_id, 0) + 1

    return _top_entry(upset_death_counts, players_by_id)


# Spike detonation deaths aren't always labeled with a distinct "Spike" weapon
# marker -- sometimes a detonation death is classified the same as other
# no-weapon-icon deaths (e.g. fall damage), where "Environmental" is the
# closest available proxy and in practice is overwhelmingly spike deaths.
# Other times it's labeled distinctly, e.g. as "Bomb".
SPIKE_DEATH_WEAPONS = {"Environmental", "Bomb"}


def _build_spike_death_stats(
    db: Session,
    match_ids: list[int],
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
) -> FunStatEntry | None:
    rows = (
        db.query(KillEvent.death_match_player_id)
        .join(Round, Round.id == KillEvent.round_id)
        .filter(
            Round.match_id.in_(match_ids),
            KillEvent.weapon.in_(SPIKE_DEATH_WEAPONS),
            KillEvent.death_match_player_id.in_(our_mp_to_player.keys()),
        )
        .all()
    )
    spike_death_counts: dict[int, int] = {}
    for (death_mp_id,) in rows:
        player_id = our_mp_to_player[death_mp_id]
        spike_death_counts[player_id] = spike_death_counts.get(player_id, 0) + 1

    return _top_entry(spike_death_counts, players_by_id)


def _build_post_plant_menace_stats(
    db: Session,
    match_ids: list[int],
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
) -> dict[int, int]:
    rows = (
        db.query(KillEvent.killer_match_player_id)
        .join(Round, Round.id == KillEvent.round_id)
        .filter(
            Round.match_id.in_(match_ids),
            Round.planted.is_(True),
            Round.plant_time.isnot(None),
            KillEvent.event_time_seconds >= Round.plant_time,
            KillEvent.killer_match_player_id.in_(our_mp_to_player.keys()),
            KillEvent.killer_match_player_id != KillEvent.death_match_player_id,
        )
        .all()
    )
    post_plant_kill_counts: dict[int, int] = {}
    for (killer_mp_id,) in rows:
        player_id = our_mp_to_player[killer_mp_id]
        post_plant_kill_counts[player_id] = post_plant_kill_counts.get(player_id, 0) + 1

    return post_plant_kill_counts


def _build_entry_kill_stats(
    db: Session,
    match_ids: list[int],
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
) -> dict[int, int]:
    """Kills landed within the round's opening ENTRY_KILL_WINDOW_SECONDS."""
    rows = (
        db.query(KillEvent.killer_match_player_id)
        .join(Round, Round.id == KillEvent.round_id)
        .filter(
            Round.match_id.in_(match_ids),
            KillEvent.event_time_seconds <= ENTRY_KILL_WINDOW_SECONDS,
            KillEvent.killer_match_player_id.in_(our_mp_to_player.keys()),
            KillEvent.killer_match_player_id != KillEvent.death_match_player_id,
        )
        .all()
    )
    entry_kill_counts: dict[int, int] = {}
    for (killer_mp_id,) in rows:
        player_id = our_mp_to_player[killer_mp_id]
        entry_kill_counts[player_id] = entry_kill_counts.get(player_id, 0) + 1

    return entry_kill_counts


def _build_late_kill_stats(
    db: Session,
    match_ids: list[int],
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
) -> dict[int, int]:
    """Kills landed at/after the round's LATE_KILL_MARK_SECONDS mark."""
    rows = (
        db.query(KillEvent.killer_match_player_id)
        .join(Round, Round.id == KillEvent.round_id)
        .filter(
            Round.match_id.in_(match_ids),
            KillEvent.event_time_seconds >= LATE_KILL_MARK_SECONDS,
            KillEvent.killer_match_player_id.in_(our_mp_to_player.keys()),
            KillEvent.killer_match_player_id != KillEvent.death_match_player_id,
        )
        .all()
    )
    late_kill_counts: dict[int, int] = {}
    for (killer_mp_id,) in rows:
        player_id = our_mp_to_player[killer_mp_id]
        late_kill_counts[player_id] = late_kill_counts.get(player_id, 0) + 1

    return late_kill_counts


def _build_ghost_stats(
    db: Session,
    match_ids: list[int],
    our_mp_to_player: dict[int, int],
    players_by_id: dict[int, str],
) -> FunStatEntry | None:
    """Counts rounds where a player survived to the end but contributed
    nothing: no kills, no assists, no death.
    """
    rows = (
        db.query(RoundPlayerStat.match_player_id)
        .join(Round, Round.id == RoundPlayerStat.round_id)
        .filter(
            Round.match_id.in_(match_ids),
            RoundPlayerStat.match_player_id.in_(our_mp_to_player.keys()),
            RoundPlayerStat.kills == 0,
            RoundPlayerStat.assists == 0,
            RoundPlayerStat.deaths == 0,
        )
        .all()
    )
    ghost_round_counts: dict[int, int] = {}
    for (match_player_id,) in rows:
        player_id = our_mp_to_player[match_player_id]
        ghost_round_counts[player_id] = ghost_round_counts.get(player_id, 0) + 1

    return _top_entry(ghost_round_counts, players_by_id)


def _build_shoutouts(
    raw: _RawSessionCounts,
    leaderboard: list[LeaderboardEntry],
    players_by_id: dict[int, str],
) -> list[PlayerShoutout]:
    """Gives every player on the session roster exactly one flattering,
    individual callout -- unlike Fun Stats, which only ever names a single
    session-wide winner per category. See `app.services.shoutouts` for the
    assignment algorithm; this just adapts the session's raw counts and
    leaderboard into the shape it expects.
    """
    raw_dicts: dict[str, dict[int, int]] = {
        "entry_kill_counts": raw.entry_kill_counts,
        "clutch_counts": raw.clutch_counts,
        "post_plant_kill_counts": raw.post_plant_kill_counts,
        "multi_kill_counts": raw.multi_kill_counts,
        "max_streak": raw.max_streak,
        "round_changer_kill_counts": raw.round_changer_kill_counts,
        "xvx_kill_counts": raw.xvx_kill_counts,
        "kills_on_top_frag": raw.kills_on_top_frag,
        "late_kill_counts": raw.late_kill_counts,
        "op_kill_counts": raw.op_kill_counts,
        "eco_kill_counts": raw.eco_kill_counts,
        "traded_teammate_totals": raw.traded_teammate_totals,
        "traded_by_teammate_totals": raw.traded_by_teammate_totals,
        "mvp_counts": raw.mvp_counts,
    }
    best_single_round_impact = {pid: impact for pid, (impact, _match_id, _round_number) in raw.best_round_impact.items()}

    anchor = None
    if leaderboard:
        leader = leaderboard[0]
        anchor = (
            leader.player_id,
            "Anchor of the Session",
            f"led the roster with {round(leader.average_impact)} avg Impact per round",
        )

    roster = [(entry.player_id, players_by_id.get(entry.player_id, "?")) for entry in leaderboard]
    return assign_shoutouts(roster, raw_dicts, best_single_round_impact, anchor)
