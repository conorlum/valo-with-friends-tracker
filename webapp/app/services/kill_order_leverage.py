"""Per-kill decomposition of the kill-order graph's contribution to Impact.

Impact is exactly linear in the 26 kill-order parameters before rounding
(verified in the spec: doubling every edge weight doubles econ/time/swing to
a median ratio of exactly 2.000, and leaves damage at 1.000). So a round's
Impact differential can be written

    ImpactDiff(r) = damage_diff(r) + SUM_k b_k * x_r[k]

and every candidate graph is scored on the SAME design matrix, differing
only in b. This module produces x_r.

The state walk here mirrors app/scoring/impact.py:503-580 exactly, INCLUDING
its resurrection rule, which differs from app/services/state_replay.py's
(that engine excludes ambiguous-lifecycle rounds; impact.py keeps them and
declines to decrement). The divergence is deliberate: this stage refits the
parameters of the shipped scorer, so extracting through a different engine
would refit a graph for a metric that is not the one that ships. The
reconstruction gate holds the two in sync.
"""

from dataclasses import dataclass, replace

import numpy as np

from app.models.match import Team
from app.scoring.impact import (
    _KILL_ORDER_GRAPH,
    _categorize_econ,
    _check_for_resurrection,
    _econ_swing_risk_factor,
    _time_factor,
    _traded_factor,
)

# own-major so PARAMS[:5] is the own=1 row. The 26th is the fallback
# _kill_order_bonus returns for transitions the graph does not contain --
# 543 of 178,242 kill events, in 497 of 23,955 rounds, measured.
PARAMS: list[str] = [f"{own}v{opp}" for own in range(1, 6) for opp in range(1, 6)] + ["fallback"]
PARAM_INDEX: dict[str, int] = {name: i for i, name in enumerate(PARAMS)}
COMPONENTS: tuple[str, str, str] = ("econ", "time", "swing")
FALLBACK_INDEX = PARAM_INDEX["fallback"]
FALLBACK_WEIGHT = 100.0

# Self-kill death-side econ factors, from impact.py:547-555. Keyed by the
# victim's econ tier code.
_SELF_KILL_DEATH_ECON = {4: 0.9, 5: 0.85, 6: 0.75}
_SELF_KILL_DEATH_ECON_DEFAULT = 0.15


def shipped_graph() -> np.ndarray:
    """The live _KILL_ORDER_GRAPH as a 26-vector in PARAMS order.

    The DiGraph's 50 edges are exactly 25 killer-perspective parameters
    duplicated by side. That symmetry is a structural invariant of the
    metric -- without it Impact would depend on which team you happen to be
    -- so it is ASSERTED here rather than assumed.
    """
    graph = np.full(len(PARAMS), np.nan)
    graph[FALLBACK_INDEX] = FALLBACK_WEIGHT
    for source, target, data in _KILL_ORDER_GRAPH.edges(data=True):
        before_a, before_b = (int(v) for v in source.split("v"))
        after_a, _ = (int(v) for v in target.split("v"))
        # A decrement of the first index is a TEAM_1 kill, whose killer has
        # `before_b` alive against `before_a`; otherwise it is TEAM_2's.
        own, opp = (before_b, before_a) if after_a == before_a - 1 else (before_a, before_b)
        index = PARAM_INDEX[f"{own}v{opp}"]
        weight = float(data["weight"])
        if not np.isnan(graph[index]) and graph[index] != weight:
            raise ValueError(
                f"kill-order graph is not side-symmetric at {own}v{opp}: "
                f"{graph[index]} != {weight}"
            )
        graph[index] = weight
    if np.isnan(graph).any():
        missing = [PARAMS[i] for i in np.flatnonzero(np.isnan(graph))]
        raise ValueError(f"kill-order graph has no weight for {missing}")
    return graph


@dataclass(frozen=True)
class KillTerm:
    """One kill's contribution, with the graph weight factored OUT.

    `kill`, `death` and `death_untraded` are the three component factors
    (econ, time, swing). `death` is what impact.py actually scores -- the
    traded factor already applied. `death_untraded` is the same before that
    discount.

    Both are stored rather than one being derived, for two reasons. The
    player-level read reports the trade discount as a subtraction
    (`death_untraded - death`), and _traded_factor can legitimately return
    0.0 for a kill traded back instantly, which would make division by
    `traded` undefined.
    """

    round_number: int
    round_id: int
    param_index: int
    tracked: bool
    sign: float
    killer_match_player_id: int
    victim_match_player_id: int
    kill: tuple[float, float, float]
    death: tuple[float, float, float]
    death_untraded: tuple[float, float, float]
    traded: float
    # Alive counts AFTER this kill, straight from the walk. Reconstructing
    # them later by counting victims is wrong: impact.py deliberately does
    # not decrement on events _check_for_resurrection flags, so a
    # re-referenced player would be subtracted twice and the terminal state
    # could go negative.
    alive_team1_after: int = 5
    alive_team2_after: int = 5


def kill_terms_for_match(
    rounds_by_number,
    round_outcomes,
    round_player_stats,
    match_players,
    round_kills,
) -> dict[int, list[KillTerm]]:
    """Decompose every kill of a match. Inputs are the same structures
    build_impact_rows_for_match builds internally (impact.py:404-437).

    EX-ANTE ONLY: the swing factor comes from _econ_swing_risk_factor and
    the realized term is never consulted, because it reads round N+1's
    loadouts and any forward-looking model trained on it would leak.
    """
    out: dict[int, list[KillTerm]] = {}

    for round_number, kills in round_kills.items():
        round_row = rounds_by_number[round_number]
        stats = round_player_stats[round_number]

        swing_by_team = {
            Team.TEAM_1: _econ_swing_risk_factor(
                round_outcomes, round_player_stats, match_players,
                round_number, Team.TEAM_1, round_row,
            ),
            Team.TEAM_2: _econ_swing_risk_factor(
                round_outcomes, round_player_stats, match_players,
                round_number, Team.TEAM_2, round_row,
            ),
        }

        # Mirrors impact.py's confusing but load-bearing naming: team1_index
        # tracks TEAM_2's alive count and vice versa, because each decrements
        # when the OTHER team lands a kill.
        team1_index = 5
        team2_index = 5
        terms: list[KillTerm] = []

        for position, event in enumerate(kills):
            killer_id = event["killer_match_player_id"]
            victim_id = event["death_match_player_id"]
            self_kill = killer_id == victim_id
            killer_team = match_players[killer_id].team

            # _kill_order_bonus's before/after edge lookup depends only on
            # WHICH raw index decrements, not on killer identity: a TEAM_2
            # self-kill decrements team1_index (which tracks TEAM_2's own
            # count) via the exact same edge a TEAM_1 kill on a TEAM_2
            # opponent would use. shipped_graph() re-indexes every edge by
            # "which index decrements", so own/opp here must follow the
            # SAME rule -- keying off killer_team alone (own = killer's
            # team) is only correct for non-self kills, and a self-kill
            # then gets attributed to the wrong lattice cell, since the
            # decremented index (its own team) is on the OTHER side of the
            # own/opp pair from what killer_team alone would suggest.
            # Verified against a live gate: a self-kill misattributed this
            # way produces a ~22-point-per-round reconstruction gap against
            # the shipped scorer.
            before = f"{team1_index}v{team2_index}"
            after_a_decrements = (killer_team == Team.TEAM_1) != self_kill
            after_a, after_b = team1_index, team2_index
            if after_a_decrements:
                after_a -= 1
            else:
                after_b -= 1
            tracked = _KILL_ORDER_GRAPH.has_edge(before, f"{after_a}v{after_b}")

            if after_a_decrements:
                own, opp = team2_index, team1_index
            else:
                own, opp = team1_index, team2_index
            param_index = PARAM_INDEX[f"{own}v{opp}"] if tracked else FALLBACK_INDEX

            # Both halves carry the killer's sign: the victim's death_impact
            # is subtracted from the OTHER team, so it raises the same
            # differential the kill does. A self-kill reverses it.
            sign = 1.0 if killer_team == Team.TEAM_1 else -1.0
            if self_kill:
                sign = -sign

            killer_tier = _categorize_econ(stats[killer_id]["loadout"])
            victim_tier = _categorize_econ(stats[victim_id]["loadout"])
            swing = swing_by_team[Team.TEAM_2 if killer_team == Team.TEAM_1 else Team.TEAM_1]
            traded = _traded_factor(kills, event, self_kill)

            if self_kill:
                kill_half = (0.0, 0.0, 0.0)
                death_econ = _SELF_KILL_DEATH_ECON.get(victim_tier, _SELF_KILL_DEATH_ECON_DEFAULT)
            else:
                kill_half = (
                    killer_tier / victim_tier,
                    _time_factor(round_row, event["event_time_seconds"]),
                    swing,
                )
                death_econ = killer_tier / victim_tier

            death_untraded = (
                death_econ,
                _time_factor(round_row, event["event_time_seconds"], for_death=True),
                swing,
            )
            death_half = tuple(traded * value for value in death_untraded)

            terms.append(
                KillTerm(
                    round_number=round_number,
                    round_id=round_row.id,
                    param_index=param_index,
                    tracked=tracked,
                    sign=sign,
                    killer_match_player_id=killer_id,
                    victim_match_player_id=victim_id,
                    kill=kill_half,
                    death=death_half,
                    death_untraded=death_untraded,
                    traded=traded,
                )
            )

            if not _check_for_resurrection(position, kills):
                if (killer_team == Team.TEAM_1) != self_kill:
                    team1_index -= 1
                else:
                    team2_index -= 1
            # team1_index tracks TEAM_2's alive count and vice versa.
            terms[-1] = replace(
                terms[-1], alive_team1_after=team2_index, alive_team2_after=team1_index
            )

        out[round_number] = terms

    return out


@dataclass(frozen=True)
class TeamLeverageRow:
    """One round, in contributes-to-Impact_diff form (team A minus team B).

    A fit consumes `damage_diff` plus these two blocks; under the shipped
    weights the round's Impact differential is

        damage_diff + (1/3) * SUM_{k,c} b_k * (kill[k][c] + death[k][c])

    Both blocks are ADDED because a kill raises the differential twice: the
    killer's kill_impact rises, and the victim's death_impact is subtracted
    from the other team.
    """

    match_id: int
    round_id: int
    round_number: int
    damage_diff: float
    kill: np.ndarray            # (26, 3)
    death: np.ndarray           # (26, 3), traded discount applied
    death_untraded: np.ndarray  # (26, 3), before the discount
    # Rung 4 of the control ladder. Two numbers, deliberately: a richer
    # terminal encoding could reconstruct the round and make the rung 4 -> 5
    # headline null for reasons unrelated to the price list.
    terminal_alive_diff: float = 0.0
    total_kills: int = 0


@dataclass(frozen=True)
class PlayerLeverageRow:
    """One (round, match_player), unsigned. Consumed by Stage 0's per-player
    block and by the kill/death-and-trades read, neither of which can be
    served from a team differential."""

    match_id: int
    round_id: int
    round_number: int
    match_player_id: int
    # The CANONICAL player, stable across matches. match_player_id is a
    # per-match surrogate, so grouping by it makes "within-player" analysis
    # impossible -- terciles would compare strong players against weak ones
    # instead of a player against their own baseline. 94.7% of players in
    # this DB have exactly one match, which is what makes that distinction
    # decisive rather than academic.
    player_id: int
    team_is_a: bool
    damage: float
    kill: np.ndarray
    death: np.ndarray
    death_untraded: np.ndarray


@dataclass(frozen=True)
class MatchLeverage:
    match_id: int
    team_rows: list[TeamLeverageRow]
    player_rows: list[PlayerLeverageRow]


def _blocks() -> np.ndarray:
    return np.zeros((len(PARAMS), len(COMPONENTS)))


def assemble_round(match_id, round_row, terms, match_players, damage_by_match_player):
    """Both products for one round. Pure: no DB access, so it is fixture
    testable."""
    player_kill = {mp_id: _blocks() for mp_id in match_players}
    player_death = {mp_id: _blocks() for mp_id in match_players}
    player_death_raw = {mp_id: _blocks() for mp_id in match_players}

    for term in terms:
        killer, victim = term.killer_match_player_id, term.victim_match_player_id
        player_kill[killer][term.param_index] += np.asarray(term.kill, dtype=float)
        player_death[victim][term.param_index] += np.asarray(term.death, dtype=float)
        player_death_raw[victim][term.param_index] += np.asarray(
            term.death_untraded, dtype=float
        )

    player_rows: list[PlayerLeverageRow] = []
    for mp_id, match_player in match_players.items():
        player_rows.append(
            PlayerLeverageRow(
                match_id=match_id,
                round_id=round_row.id,
                round_number=round_row.round_number,
                match_player_id=mp_id,
                player_id=match_player.player_id,
                team_is_a=match_player.team == Team.TEAM_1,
                damage=float(damage_by_match_player.get(mp_id, 0.0)),
                kill=player_kill[mp_id],
                death=player_death[mp_id],
                death_untraded=player_death_raw[mp_id],
            )
        )

    # SUM_A - SUM_B for the kill block; SUM_B - SUM_A for the death block,
    # because a death is SUBTRACTED from the player who suffered it.
    def combine(field, flip):
        total = _blocks()
        for row in player_rows:
            on_a = row.team_is_a
            sign = (1.0 if on_a else -1.0) * (-1.0 if flip else 1.0)
            total += sign * getattr(row, field)
        return total

    # Read off the walk, never recount victims: impact.py declines to
    # decrement on events _check_for_resurrection flags, so counting
    # distinct victims double-subtracts a re-referenced player and can
    # drive the terminal state negative. A round with no kills is 5v5.
    alive_a = terms[-1].alive_team1_after if terms else 5
    alive_b = terms[-1].alive_team2_after if terms else 5

    team_row = TeamLeverageRow(
        match_id=match_id,
        round_id=round_row.id,
        round_number=round_row.round_number,
        damage_diff=float(
            sum(r.damage for r in player_rows if r.team_is_a)
            - sum(r.damage for r in player_rows if not r.team_is_a)
        ),
        kill=combine("kill", flip=False),
        death=combine("death", flip=True),
        death_untraded=combine("death_untraded", flip=True),
        terminal_alive_diff=float(alive_a - alive_b),
        total_kills=len(terms),
    )
    return team_row, player_rows


from collections import defaultdict

from app.models import KillEvent, Match, MatchPlayer, Round
from app.models.round import RoundPlayerStat
from app.scoring.impact import build_impact_rows_for_match
from app.services.surrender_rounds import NOT_A_SURRENDER_ROUND


def eligible_match_ids(db) -> list[int]:
    return [
        match_id
        for (match_id,) in db.query(Match.id)
        .join(Round, Round.match_id == Match.id)
        .filter(NOT_A_SURRENDER_ROUND)
        .distinct()
        .order_by(Match.id)
        .all()
    ]


def build_match_leverage(db, match_id: int) -> MatchLeverage:
    """Replay one match. Mirrors build_impact_rows_for_match's own loads
    (impact.py:404-437) so the two see identical inputs.

    EX-ANTE: damage comes from the scorer with use_realized_swing=False.
    Damage is graph-independent, but the flag is passed explicitly so this
    never becomes the one path that quietly reads round N+1.
    """
    rounds = (
        db.query(Round)
        .filter(Round.match_id == match_id)
        .filter(NOT_A_SURRENDER_ROUND)
        .order_by(Round.round_number)
        .all()
    )
    rounds_by_number = {r.round_number: r for r in rounds}
    number_by_round_id = {r.id: r.round_number for r in rounds}
    round_outcomes = {r.round_number: r.outcome for r in rounds}

    match_players = {
        mp.id: mp for mp in db.query(MatchPlayer).filter_by(match_id=match_id).all()
    }

    round_player_stats: dict[int, dict[int, dict]] = defaultdict(dict)
    for stat in db.query(RoundPlayerStat).join(Round).filter(Round.match_id == match_id).all():
        number = number_by_round_id.get(stat.round_id)
        if number is None:
            continue  # a surrender placeholder round, already filtered above
        round_player_stats[number][stat.match_player_id] = {
            "score": stat.score, "kills": stat.kills, "deaths": stat.deaths,
            "assists": stat.assists, "loadout": stat.loadout, "remaining": stat.remaining,
        }

    round_kills: dict[int, list[dict]] = defaultdict(list)
    for event in (
        db.query(KillEvent)
        .join(Round)
        .filter(Round.match_id == match_id)
        .order_by(KillEvent.event_time_seconds, KillEvent.id)
        .all()
    ):
        number = number_by_round_id.get(event.round_id)
        if number is None:
            continue
        round_kills[number].append({
            "killer_match_player_id": event.killer_match_player_id,
            "death_match_player_id": event.death_match_player_id,
            "event_time_seconds": event.event_time_seconds,
        })

    damage_by_round: dict[int, dict[int, float]] = defaultdict(dict)
    for calculated in build_impact_rows_for_match(db, match_id, use_realized_swing=False):
        number = number_by_round_id.get(calculated.round_id)
        if number is None:
            continue
        damage_by_round[number][calculated.match_player_id] = float(calculated.damage)

    terms_by_round = kill_terms_for_match(
        rounds_by_number, round_outcomes, round_player_stats, match_players, round_kills
    )

    team_rows: list[TeamLeverageRow] = []
    player_rows: list[PlayerLeverageRow] = []
    for number, round_row in rounds_by_number.items():
        if number not in round_player_stats:
            continue  # no stats rows: nothing to attribute
        team_row, rows = assemble_round(
            match_id=match_id,
            round_row=round_row,
            terms=terms_by_round.get(number, []),
            match_players=match_players,
            damage_by_match_player=damage_by_round.get(number, {}),
        )
        team_rows.append(team_row)
        player_rows.extend(rows)

    return MatchLeverage(match_id=match_id, team_rows=team_rows, player_rows=player_rows)


def load_all_leverage(db, report: dict | None = None):
    """Every eligible match. Costs a full replay -- minutes, comparable to
    the parent project's load_all_observations.

    A match that raises is EXCLUDED and counted, never silently turned into
    zero-leverage rows; the CLI prints the count.
    """
    team_rows: list[TeamLeverageRow] = []
    player_rows: list[PlayerLeverageRow] = []
    excluded: list[int] = []
    match_ids = eligible_match_ids(db)
    for match_id in match_ids:
        try:
            leverage = build_match_leverage(db, match_id)
        except (KeyError, ValueError):
            excluded.append(match_id)
            continue
        team_rows.extend(leverage.team_rows)
        player_rows.extend(leverage.player_rows)
    if report is not None:
        report["eligible_matches"] = len(match_ids)
        report["excluded_matches"] = len(excluded)
        report["excluded_match_ids"] = excluded[:20]
    return team_rows, player_rows


from app.scoring.impact import _did_team_win


@dataclass(frozen=True)
class StateVisitRow:
    """One team's view of one man-advantage state the round passed through.

    Every state entry produces TWO rows, one per team, mirrored: at (3, 2)
    for team A the same instant is (2, 3) for team B. That is what makes
    P(win | own, own) come out at exactly 0.5 by construction, which is a
    useful sanity check on the whole table.
    """

    match_id: int
    round_id: int
    own: int
    opp: int
    won: bool


def state_visits_for_match(db, match_id: int) -> list[StateVisitRow]:
    """Replay the alive-count walk again, recording state entries rather
    than kill terms. Uses impact.py's resurrection rule, like everything
    else here."""
    rounds = (
        db.query(Round).filter(Round.match_id == match_id)
        .filter(NOT_A_SURRENDER_ROUND).order_by(Round.round_number).all()
    )
    outcome_by_round_id = {r.id: r.outcome for r in rounds}
    teams = {mp.id: mp.team for mp in db.query(MatchPlayer).filter_by(match_id=match_id).all()}

    kills_by_round: dict[int, list] = defaultdict(list)
    for event in (
        db.query(KillEvent).join(Round).filter(Round.match_id == match_id)
        .order_by(KillEvent.event_time_seconds, KillEvent.id).all()
    ):
        if event.round_id in outcome_by_round_id:
            kills_by_round[event.round_id].append(event)

    out: list[StateVisitRow] = []
    # EVERY eligible round, not just those with kills. A round that ends by
    # defuse or time expiry with no kills still starts at 5v5, and dropping
    # it biases P(win | 5v5) toward rounds that contained a kill.
    for round_id in outcome_by_round_id:
        events = kills_by_round.get(round_id, [])
        outcome = outcome_by_round_id[round_id]
        if not outcome or "Team " not in outcome:
            continue
        try:
            team_1_won = _did_team_win(outcome, Team.TEAM_1)
        except (IndexError, ValueError):
            continue

        alive_1 = alive_2 = 5

        def record():
            out.append(StateVisitRow(match_id, round_id, alive_1, alive_2, team_1_won))
            out.append(StateVisitRow(match_id, round_id, alive_2, alive_1, not team_1_won))

        record()
        for position, event in enumerate(events):
            plain = [
                {"killer_match_player_id": e.killer_match_player_id,
                 "death_match_player_id": e.death_match_player_id,
                 "event_time_seconds": e.event_time_seconds}
                for e in events
            ]
            if _check_for_resurrection(position, plain):
                continue
            victim = event.death_match_player_id
            if victim is None:
                continue
            if teams[victim] == Team.TEAM_1:
                alive_1 -= 1
            else:
                alive_2 -= 1
            if alive_1 < 0 or alive_2 < 0:
                break
            record()
    return out
