"""Kill-order-bonus / traded-factor extraction for the Part 3 centring solve
(app.scoring.preplant_centering.solve_kill_side_centering needs both,
aligned index-for-index with extract_preplant_observations's output).

Deliberately NOT in preplant_time_model.py: this module imports
app.scoring.impact (for _kill_order_bonus/_traded_factor, the exact same
functions production scoring uses), and impact.py will import
app.scoring.preplant_scalar -> preplant_k_selection -> preplant_time_model
once Part 3 is wired in (Task 9) -- so preplant_time_model.py must NOT
import anything from impact.py, or that becomes a cycle. Only the
fit-and-report script (which nothing imports) needs this module.
"""

from app.models.match import Team
from app.scoring.impact import (
    _alive_before_each_kill, _kill_order_bonus, _present_players, _round_stats_for_presence,
    _scoreable_kills, _traded_factor,
)
from app.scoring.plant_window import attacking_team, effective_plant_time, is_phantom_plant, seconds_to_plant
from app.scoring.preplant_time_model import PreplantKillObservation, _RoundRow, _winner_team
from sqlalchemy import text


def extract_preplant_observations_with_factors(
    db,
) -> tuple[list[PreplantKillObservation], list[float], list[float]]:
    """Walks the same rounds/kills as extract_preplant_observations, once,
    so the three returned lists are guaranteed aligned by construction
    rather than by two separate walks agreeing on their filtering."""
    rounds = {
        r["id"]: dict(r)
        for r in db.execute(text(
            "SELECT id, match_id, round_number, outcome, planted, plant_time FROM rounds"
        )).mappings()
    }
    match_players = {
        mp["id"]: dict(mp)
        for mp in db.execute(text("SELECT id, match_id, team FROM match_players")).mappings()
    }
    # Built once for _traded_factor's team check, not per kill.
    team_of = {mp_id: Team[mp["team"]] for mp_id, mp in match_players.items()}
    stats_by_round = _round_stats_for_presence(db)

    kills_by_round: dict[int, list[dict]] = {}
    for k in db.execute(text(
        "SELECT round_id, killer_match_player_id, death_match_player_id, event_time_seconds, weapon "
        "FROM kill_events ORDER BY round_id, event_time_seconds, id"
    )).mappings():
        kills_by_round.setdefault(k["round_id"], []).append(dict(k))

    observations: list[PreplantKillObservation] = []
    kill_order_bonuses: list[float] = []
    traded_factors: list[float] = []

    for round_id, kills in kills_by_round.items():
        r = rounds.get(round_id)
        if r is None:
            continue
        if r["outcome"] and "Surrendered" in r["outcome"]:
            continue

        round_row = _RoundRow(r["planted"], r["plant_time"], r["outcome"])
        if is_phantom_plant(round_row):
            continue
        plant_time = effective_plant_time(round_row)
        if plant_time is None:
            continue

        winner = _winner_team(r["outcome"])
        atk = attacking_team(r["round_number"])
        kills = _scoreable_kills(kills, team_of)
        # REPLAY POLICY: the scorer's own alive counts
        # (impact._alive_before_each_kill), so the fitted curve is keyed
        # on exactly the states scoring uses. Hand-rolled replays here
        # drifted from the scorer before -- on self-kills, team kills,
        # environmental deaths and revives -- and `exact_state` and `adv`
        # are the standardization keys, so a wrong state reweights the
        # estimate rather than just mislabelling a row.
        alive_before = _alive_before_each_kill(
            kills, team_of, _present_players(stats_by_round.get(round_id, {}), kills, team_of))

        for index, kill in enumerate(kills):
            alive = alive_before[index]
            killer_id = kill["killer_match_player_id"]
            victim_id = kill["death_match_player_id"]
            if not killer_id or not victim_id or killer_id not in match_players or victim_id not in match_players:
                continue
            killer_team = Team[match_players[killer_id]["team"]]
            victim_team = Team[match_players[victim_id]["team"]]
            self_kill = killer_team == victim_team
            kill_time = kill["event_time_seconds"]
            dt = seconds_to_plant(round_row, kill_time)

            if not self_kill and dt is not None and dt > 0:
                killer_alive = alive[killer_team]
                victim_alive = alive[victim_team]
                observations.append(PreplantKillObservation(
                    match_id=r["match_id"], round_id=round_id, dt=dt,
                    adv=killer_alive - victim_alive,
                    is_attacker=(atk == killer_team),
                    exact_state=f"{killer_alive}v{victim_alive}",
                    round_won_by_killer_team=(winner == killer_team) if winner is not None else None,
                ))
                # _kill_order_bonus's first two positional args are named
                # team1_kill_index/team2_kill_index but actually carry
                # TEAM_2's and TEAM_1's alive counts respectively (see
                # impact.py's own comment at the kill loop) -- reproduced
                # exactly so this matches production scoring's own bonus.
                kill_order_bonuses.append(
                    _kill_order_bonus(alive[Team.TEAM_2], alive[Team.TEAM_1], killer_team, self_kill=False)
                )
                traded_factors.append(_traded_factor(
                    kills, kill, self_kill=False,
                    team_of=team_of,
                ))

    return observations, kill_order_bonuses, traded_factors
