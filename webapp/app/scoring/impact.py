from collections import defaultdict

import networkx as nx
from sqlalchemy.orm import Session

from app.models import ImpactScore, KillEvent, MatchPlayer, Round, RoundPlayerStat
from app.models.match import Team

_KILL_ORDER_GRAPH = nx.DiGraph()
_KILL_ORDER_GRAPH.add_weighted_edges_from(
    [
        ("5v5", "4v5", 150),
        ("5v5", "5v4", 150),
        ("4v5", "3v5", 130),
        ("4v5", "4v4", 140),
        ("5v4", "4v4", 140),
        ("5v4", "5v3", 130),
        ("3v5", "2v5", 90),
        ("3v5", "3v4", 120),
        ("4v4", "3v4", 170),
        ("4v4", "4v3", 170),
        ("5v3", "4v3", 120),
        ("5v3", "5v2", 90),
        ("2v5", "1v5", 50),
        ("2v5", "2v4", 70),
        ("3v4", "2v4", 130),
        ("3v4", "3v3", 160),
        ("4v3", "3v3", 160),
        ("4v3", "4v2", 130),
        ("5v2", "4v2", 70),
        ("5v2", "5v1", 50),
        ("1v5", "0v5", 40),
        ("1v5", "1v4", 60),
        ("2v4", "1v4", 80),
        ("2v4", "2v3", 130),
        ("3v3", "2v3", 180),
        ("3v3", "3v2", 180),
        ("4v2", "3v2", 130),
        ("4v2", "4v1", 80),
        ("5v1", "4v1", 60),
        ("5v1", "5v0", 40),
        ("1v4", "0v4", 50),
        ("1v4", "1v3", 70),
        ("2v3", "1v3", 140),
        ("2v3", "2v2", 170),
        ("3v2", "2v2", 170),
        ("3v2", "3v1", 140),
        ("4v1", "3v1", 70),
        ("4v1", "4v0", 50),
        ("1v3", "0v3", 70),
        ("1v3", "1v2", 120),
        ("2v2", "1v2", 200),
        ("2v2", "2v1", 200),
        ("3v1", "2v1", 120),
        ("3v1", "3v0", 70),
        ("1v2", "0v2", 130),
        ("1v2", "1v1", 190),
        ("2v1", "1v1", 190),
        ("2v1", "2v0", 130),
        ("1v1", "0v1", 250),
        ("1v1", "1v0", 250),
    ]
)


FORCE_THRESHOLD = 3250
FULL_BUY_THRESHOLD = 4200
WIN_BONUS = 3000
SURVIVE_LOSS_BONUS = 1000  # documented for completeness; see _econ_swing_risk_factor
KILL_REWARD = 200
PLANT_BONUS = 300

# Relative importance of each factor when combining them into kill_impact/death_impact.
# Equal weights would reproduce a plain average; these are a starting proposal, not a
# final tuning -- adjust freely.
FACTOR_WEIGHTS = {
    "econ": 1.0,
    "time": 1.0,
    "swing": 1.0,
}
_FACTOR_WEIGHT_TOTAL = sum(FACTOR_WEIGHTS.values())


_ECON_TIER_CODES = {"SAVE": 8, "ECO": 6, "FORCE": 5, "FULL_BUY": 4}


def econ_tier_name(loadout: int) -> str:
    if loadout < 1000:
        return "SAVE"
    if loadout < FORCE_THRESHOLD:
        return "ECO"
    if loadout < FULL_BUY_THRESHOLD:
        return "FORCE"
    return "FULL_BUY"


def _categorize_econ(loadout: int) -> int:
    return _ECON_TIER_CODES[econ_tier_name(loadout)]


def _kill_order_bonus(team1_kill_index: int, team2_kill_index: int, kill_team: Team, self_kill: bool) -> float:
    before_node = f"{team1_kill_index}v{team2_kill_index}"
    if self_kill:
        if kill_team == Team.TEAM_1:
            team2_kill_index -= 1
        else:
            team1_kill_index -= 1
    else:
        if kill_team == Team.TEAM_1:
            team1_kill_index -= 1
        else:
            team2_kill_index -= 1
    after_node = f"{team1_kill_index}v{team2_kill_index}"

    try:
        return _KILL_ORDER_GRAPH[before_node][after_node]["weight"]
    except KeyError:
        return 100


def _time_factor(round_row: Round, kill_time: float, for_death: bool = False) -> float:
    # Mirrors the original's chronological state machine (planted/plantedTime/
    # exploded/defused flags updated as the event log is walked), reconstructed
    # from the round's final planted/plant_time/exploded/defuse_time. The
    # post-plant window and ramp only apply once a plant has actually happened
    # in this round, and only to kills at or after the plant.
    plant_time = round_row.plant_time if round_row.planted else None

    exploded_effective = round_row.exploded and plant_time is not None and kill_time >= plant_time + 45
    defused_effective = (
        round_row.defused and round_row.defuse_time is not None and kill_time >= round_row.defuse_time
    )
    if exploded_effective or defused_effective:
        return 0.5

    if plant_time is not None and kill_time >= plant_time:
        if plant_time + 38 <= kill_time <= plant_time + 45:
            # A kill in this window is denying/clutching a near-explosion round, so
            # it's highly valuable. A death in this window isn't the mirror-image
            # punishment -- the round is basically already decided in the killer's
            # favor by then -- so it gets the same discount as a death after the
            # round has already been decided (exploded/defused).
            return 0.5 if for_death else 1.75
        return 1 + (kill_time - plant_time) / 53

    return 1


def _traded_factor(round_kills: list[dict], checking_kill: dict, self_kill: bool) -> float:
    if self_kill:
        return 1

    time_to_trade = 10
    killer_id = checking_kill["killer_match_player_id"]
    death_time = checking_kill["event_time_seconds"]

    for kill in round_kills:
        if kill["death_match_player_id"] == killer_id:
            trade_time = kill["event_time_seconds"] - death_time
            if 0 <= trade_time <= time_to_trade:
                return trade_time / time_to_trade

    return 1


def _clutch_bucket(own_alive: int, opp_alive: int) -> bool:
    # Priority order so a kill is only ever classified into one bucket:
    # lone survivor (1vX), an even state resolving to a man advantage (XvX),
    # or fighting back from a 2-vs-3-or-more disadvantage (2vX, X > 2).
    if own_alive == 1:
        return True
    if own_alive == opp_alive:
        return True
    if own_alive == 2 and opp_alive > 2:
        return True
    return False


def _check_for_resurrection(kill_index: int, round_kills: list[dict]) -> bool:
    match_player_id = round_kills[kill_index]["death_match_player_id"]
    for later_kill in round_kills[kill_index + 1 :]:
        if later_kill["death_match_player_id"] == match_player_id or later_kill["killer_match_player_id"] == match_player_id:
            return True
    return False


def _did_team_win(outcome: str, team: Team) -> bool:
    team_letter = outcome.split("Team ")[1][0]
    return (team_letter == "A" and team == Team.TEAM_1) or (team_letter == "B" and team == Team.TEAM_2)


def _rounds_since_last_win(round_outcomes: dict[int, str], round_number: int, team: Team) -> int:
    loss_streak = 0
    r = round_number - 1
    while r > 0:
        if _did_team_win(round_outcomes[r], team):
            return loss_streak
        loss_streak += 1
        r -= 1
    return loss_streak


def _min_next_round_econ_bonus(round_outcomes: dict[int, str], round_number: int, team: Team) -> int:
    loss_streak = _rounds_since_last_win(round_outcomes, round_number, team)
    if loss_streak == 0:
        return 1900
    if loss_streak == 1:
        return 2400
    return 2900


def _attacking_team(round_number: int) -> Team | None:
    # Confirmed against the demo matches: team-1 attacks rounds 1-12, team-2
    # attacks rounds 13-24. No attacking-side data is stored in the schema, so
    # this is a documented convention, not derived from a stored fact.
    if round_number <= 12:
        return Team.TEAM_1
    if round_number <= 24:
        return Team.TEAM_2
    return None  # OT: already treated as economy-neutral below


def _econ_swing_risk_factor(
    round_outcomes: dict[int, str],
    round_player_stats: dict[int, dict[int, dict]],
    match_players: dict[int, MatchPlayer],
    round_number: int,
    team: Team,
    round_row: Round,
) -> float:
    if round_number in (1, 13):
        return 1.5
    if round_number in (12, 24):
        return 1
    if round_number > 24:
        return 1

    loadout_threshold = FULL_BUY_THRESHOLD
    vandal_cost = 2900
    econ_bonus = _min_next_round_econ_bonus(round_outcomes, round_number, team)
    plant_bonus = PLANT_BONUS if round_row.planted and _attacking_team(round_number) == team else 0

    cant_buy_next = 0
    can_buy_next = 0
    can_buy_if_win = 0
    can_buy_double = 0
    can_buy_if_win_double = 0
    need_to_buy_next = 0
    bought_in = 0

    for match_player_id, stat in round_player_stats[round_number].items():
        if match_players[match_player_id].team != team:
            continue

        # Kill reward and plant bonus are paid out between rounds, so they're
        # already-known money this player will have next round on top of
        # whatever's left in "remaining" -- unlike the win/loss bonus, which
        # depends on next round's still-undecided outcome.
        known_next_round_extra = stat["kills"] * KILL_REWARD + plant_bonus
        remaining = stat["remaining"] + known_next_round_extra
        need_to_buy_next += 1 if stat["deaths"] > 0 else 0
        current_loadout = stat["loadout"]

        if remaining + econ_bonus < loadout_threshold:
            cant_buy_next += 1
        if remaining + econ_bonus >= loadout_threshold:
            can_buy_next += 1
        if remaining + WIN_BONUS >= loadout_threshold:
            can_buy_if_win += 1
        if remaining + econ_bonus >= loadout_threshold + vandal_cost:
            can_buy_next -= 1
            can_buy_double += 1
        if remaining + WIN_BONUS >= loadout_threshold + vandal_cost:
            can_buy_if_win -= 1
            can_buy_if_win_double += 1
        if current_loadout >= loadout_threshold:
            bought_in += 1

    swing_factor = round((bought_in + cant_buy_next - can_buy_double + 3) * 0.01, 2)

    if can_buy_next + 2 * can_buy_double >= 5:
        low_risk = 0.7 - round((can_buy_next + 2 * can_buy_double) * 0.05, 2)
        return round(low_risk + bought_in * swing_factor, 1)

    if round_number in (2, 14):
        swing_factor = 0.15
        return round(1 + (cant_buy_next * swing_factor) + ((need_to_buy_next - can_buy_next) * 0.1), 2)

    if can_buy_next + can_buy_double < 5:
        return round(
            1
            + (0.67 * (bought_in * swing_factor) + (cant_buy_next * swing_factor))
            + ((need_to_buy_next - (can_buy_if_win + 2 * can_buy_if_win_double)) * swing_factor),
            2,
        )

    return 1


def _realized_econ_swing_factor(
    round_player_stats: dict[int, dict[int, dict]],
    match_players: dict[int, MatchPlayer],
    round_number: int,
    team: Team,
) -> float:
    # Valorant resets economy at halftime, so there's no real next-round link to
    # check out of the last round of a half, or past the modeled OT boundary.
    if round_number in (12, 24) or round_number > 24:
        return 1.0

    next_round_stats = round_player_stats.get(round_number + 1)
    if not next_round_stats:
        return 1.0

    denied_count = 0
    for match_player_id, stat in next_round_stats.items():
        if match_players[match_player_id].team != team:
            continue
        if stat["loadout"] < FULL_BUY_THRESHOLD:
            denied_count += 1

    # Symmetric to _econ_swing_risk_factor's ~0.5-1.5 range: 0 denied -> 0.5 (team
    # recovered fully), 5 denied -> 1.5 (fully denied), 2.5 -> neutral midpoint.
    return round(0.5 + 0.2 * denied_count, 2)


def _combine_swing_factors(swing: float, realized: float) -> float:
    # Only trust a combined signal when the pre-round prediction and the actual
    # next-round outcome agree on direction (both fragile or both safe) -- if they
    # disagree, neither claim survives, so treat the round as neutral. Realized is
    # ground truth, so when they do agree, take whichever magnitude is larger.
    swing_above = swing > 1
    realized_above = realized > 1
    if swing == 1 or realized == 1 or swing_above != realized_above:
        return 1.0
    return max(swing, realized, key=lambda x: abs(x - 1))


def compute_impact_for_match(db: Session, match_id: int) -> None:
    rounds = db.query(Round).filter_by(match_id=match_id).order_by(Round.round_number).all()
    rounds_by_number: dict[int, Round] = {r.round_number: r for r in rounds}
    round_number_by_round_id: dict[int, int] = {r.id: r.round_number for r in rounds}
    round_outcomes: dict[int, str] = {r.round_number: r.outcome for r in rounds}

    match_players: dict[int, MatchPlayer] = {
        mp.id: mp for mp in db.query(MatchPlayer).filter_by(match_id=match_id).all()
    }

    round_player_stats: dict[int, dict[int, dict]] = defaultdict(dict)
    for stat in db.query(RoundPlayerStat).join(Round).filter(Round.match_id == match_id).all():
        round_number = round_number_by_round_id[stat.round_id]
        round_player_stats[round_number][stat.match_player_id] = {
            "score": stat.score,
            "kills": stat.kills,
            "deaths": stat.deaths,
            "assists": stat.assists,
            "loadout": stat.loadout,
            "remaining": stat.remaining,
        }

    round_kills: dict[int, list[dict]] = defaultdict(list)
    for kill in (
        db.query(KillEvent)
        .join(Round)
        .filter(Round.match_id == match_id)
        .order_by(KillEvent.event_time_seconds, KillEvent.id)
        .all()
    ):
        round_number = round_number_by_round_id[kill.round_id]
        round_kills[round_number].append(
            {
                "killer_match_player_id": kill.killer_match_player_id,
                "death_match_player_id": kill.death_match_player_id,
                "event_time_seconds": kill.event_time_seconds,
            }
        )

    # Trade detection: kill D1 (A kills B, an enemy kill), followed within 10s by
    # kill D2 where B's teammate C kills A. From C's perspective, C traded for B;
    # from B's perspective, B was traded by C.
    time_to_trade = 10
    trade_kill_counts: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    trade_death_counts: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    trade_kill_targets: dict[int, dict[int, dict[int, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    trade_death_sources: dict[int, dict[int, dict[int, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    for round_number, kills in round_kills.items():
        for d1 in kills:
            a_id, b_id = d1["killer_match_player_id"], d1["death_match_player_id"]
            if a_id is None or b_id is None or a_id == b_id:
                continue
            b_team = match_players[b_id].team
            if match_players[a_id].team == b_team:
                continue

            for d2 in kills:
                c_id, victim2_id = d2["killer_match_player_id"], d2["death_match_player_id"]
                if victim2_id != a_id or c_id is None or c_id == victim2_id:
                    continue
                trade_time = d2["event_time_seconds"] - d1["event_time_seconds"]
                if 0 <= trade_time <= time_to_trade and match_players[c_id].team == b_team:
                    trade_kill_counts[round_number][c_id] += 1
                    trade_death_counts[round_number][b_id] += 1
                    trade_kill_targets[round_number][c_id][b_id] += 1
                    trade_death_sources[round_number][b_id][c_id] += 1
                    break

    # Econ-differential factor per kill.
    for round_number, kills in round_kills.items():
        for kill in kills:
            killer_id = kill["killer_match_player_id"]
            death_id = kill["death_match_player_id"]
            self_kill = killer_id == death_id
            killer_econ = round_player_stats[round_number][killer_id]["loadout"]
            death_econ = round_player_stats[round_number][death_id]["loadout"]
            if self_kill:
                kill["econ_differential_factor"] = _categorize_econ(death_econ)
                kill["econ_mismatch"] = False
            else:
                killer_tier = _categorize_econ(killer_econ)
                death_tier = _categorize_econ(death_econ)
                kill["econ_differential_factor"] = killer_tier / death_tier
                kill["econ_mismatch"] = killer_tier != death_tier

    # Kill-order bonuses, decorated per kill.
    for round_number, kills in round_kills.items():
        round_row = rounds_by_number[round_number]
        # Despite the names, team1_kill_index tracks TEAM_2's alive count and
        # team2_kill_index tracks TEAM_1's -- each decrements when the *other*
        # team lands a kill against it. Fixed for the whole round regardless of
        # which team is killing on a given kill (see the decrement below).
        team1_kill_index = 5
        team2_kill_index = 5

        team1_swing = _econ_swing_risk_factor(
            round_outcomes, round_player_stats, match_players, round_number, Team.TEAM_1, round_row
        )
        team2_swing = _econ_swing_risk_factor(
            round_outcomes, round_player_stats, match_players, round_number, Team.TEAM_2, round_row
        )
        team1_realized_swing = _realized_econ_swing_factor(round_player_stats, match_players, round_number, Team.TEAM_1)
        team2_realized_swing = _realized_econ_swing_factor(round_player_stats, match_players, round_number, Team.TEAM_2)
        team1_combined_swing = _combine_swing_factors(team1_swing, team1_realized_swing)
        team2_combined_swing = _combine_swing_factors(team2_swing, team2_realized_swing)

        for kill_index, kill in enumerate(kills):
            killer_id = kill["killer_match_player_id"]
            death_id = kill["death_match_player_id"]
            self_kill = killer_id == death_id
            killer_team = match_players[killer_id].team

            # Valorant's own combat-score kill-order bonus: 150 for a kill against a
            # still-full 5-player enemy team, decrementing 20 per further kill landed
            # against that same team this round, regardless of the killer's own losses.
            # This has nothing to do with our kill_order_bonus graph below -- it has to
            # be backed out of the raw ACS number so damages reflects pure damage+assists.
            victim_team_alive = team1_kill_index if killer_team == Team.TEAM_1 else team2_kill_index
            kill["acs_bonus"] = 0 if self_kill else max(0, 150 - 20 * (5 - victim_team_alive))

            combined_swing_factor = team1_combined_swing if killer_team == Team.TEAM_2 else team2_combined_swing
            kill_order_bonus = _kill_order_bonus(team1_kill_index, team2_kill_index, killer_team, self_kill)

            kill["kill_order_bonus"] = kill_order_bonus if not self_kill else 0
            kill["kill_order_bonus_x_econ"] = (
                kill_order_bonus * kill["econ_differential_factor"] if not self_kill else 0
            )
            kill["kill_order_bonus_x_time"] = (
                kill_order_bonus * _time_factor(round_row, kill["event_time_seconds"]) if not self_kill else 0
            )
            kill["kill_order_bonus_x_swing"] = kill_order_bonus * combined_swing_factor if not self_kill else 0

            death_order_bonus = kill_order_bonus * _traded_factor(kills, kill, self_kill)
            kill["death_order_bonus"] = death_order_bonus

            if self_kill:
                if kill["econ_differential_factor"] == 4:
                    death_econ_factor = 0.9
                elif kill["econ_differential_factor"] == 5:
                    death_econ_factor = 0.85
                elif kill["econ_differential_factor"] == 6:
                    death_econ_factor = 0.75
                else:
                    death_econ_factor = 0.15
            else:
                death_econ_factor = kill["econ_differential_factor"]

            kill["death_order_bonus_x_econ"] = death_order_bonus * death_econ_factor
            kill["death_order_bonus_x_time"] = death_order_bonus * _time_factor(
                round_row, kill["event_time_seconds"], for_death=True
            )
            kill["death_order_bonus_x_swing"] = death_order_bonus * combined_swing_factor

            killer_own_alive = team2_kill_index if killer_team == Team.TEAM_1 else team1_kill_index
            killer_opp_alive = team1_kill_index if killer_team == Team.TEAM_1 else team2_kill_index
            kill["killer_clutch"] = not self_kill and _clutch_bucket(killer_own_alive, killer_opp_alive)
            kill["victim_clutch"] = not self_kill and _clutch_bucket(killer_opp_alive, killer_own_alive)

            plant_time = round_row.plant_time if round_row.planted else None
            kill["is_post_plant"] = plant_time is not None and kill["event_time_seconds"] >= plant_time

            resurrection = _check_for_resurrection(kill_index, kills)
            if not resurrection:
                if self_kill:
                    if killer_team == Team.TEAM_1:
                        team2_kill_index -= 1
                    else:
                        team1_kill_index -= 1
                else:
                    if killer_team == Team.TEAM_1:
                        team1_kill_index -= 1
                    else:
                        team2_kill_index -= 1

    # Aggregate per (round, match_player) and write impact_scores.
    for round_number, mp_stats in round_player_stats.items():
        round_row = rounds_by_number[round_number]
        kills = round_kills.get(round_number, [])

        for match_player_id, stat in mp_stats.items():
            acs = stat["score"]
            kill_order_bonus_x_econ_sum = 0.0
            kill_order_bonus_x_time_sum = 0.0
            kill_order_bonus_x_swing_sum = 0.0
            kills_in_round = 0
            clutch_kill_sum = 0.0
            post_plant_kill_sum = 0.0
            econ_mismatch_kill_sum = 0.0

            for kill in kills:
                if kill["killer_match_player_id"] == match_player_id:
                    acs -= kill["acs_bonus"]
                    kills_in_round += 1
                    kill_order_bonus_x_econ_sum += kill["kill_order_bonus_x_econ"]
                    kill_order_bonus_x_time_sum += kill["kill_order_bonus_x_time"]
                    kill_order_bonus_x_swing_sum += kill["kill_order_bonus_x_swing"]
                    if kill["killer_clutch"]:
                        clutch_kill_sum += kill["kill_order_bonus"]
                    if kill["is_post_plant"]:
                        post_plant_kill_sum += kill["kill_order_bonus_x_time"]
                    if kill["econ_mismatch"]:
                        econ_mismatch_kill_sum += kill["kill_order_bonus_x_econ"]

            adjust_acs_for_multikill = -50 * kills_in_round if kills_in_round > 1 else 0
            damage_and_assists = acs - adjust_acs_for_multikill

            death_order_bonus_x_econ_sum = 0.0
            death_order_bonus_x_time_sum = 0.0
            death_order_bonus_x_swing_sum = 0.0
            clutch_death_sum = 0.0
            post_plant_death_sum = 0.0
            econ_mismatch_death_sum = 0.0
            for kill in kills:
                if kill["death_match_player_id"] == match_player_id:
                    death_order_bonus_x_econ_sum += kill["death_order_bonus_x_econ"]
                    death_order_bonus_x_time_sum += kill["death_order_bonus_x_time"]
                    death_order_bonus_x_swing_sum += kill["death_order_bonus_x_swing"]
                    if kill["victim_clutch"]:
                        clutch_death_sum += kill["death_order_bonus"]
                    if kill["is_post_plant"]:
                        post_plant_death_sum += kill["death_order_bonus_x_time"]
                    if kill["econ_mismatch"]:
                        econ_mismatch_death_sum += kill["death_order_bonus_x_econ"]

            damages = round(damage_and_assists * 1.25)
            kill_impact = round(
                damages
                + (
                    FACTOR_WEIGHTS["econ"] * kill_order_bonus_x_econ_sum
                    + FACTOR_WEIGHTS["time"] * kill_order_bonus_x_time_sum
                    + FACTOR_WEIGHTS["swing"] * kill_order_bonus_x_swing_sum
                )
                / _FACTOR_WEIGHT_TOTAL
            )
            death_impact = round(
                (
                    FACTOR_WEIGHTS["econ"] * death_order_bonus_x_econ_sum
                    + FACTOR_WEIGHTS["time"] * death_order_bonus_x_time_sum
                    + FACTOR_WEIGHTS["swing"] * death_order_bonus_x_swing_sum
                )
                / _FACTOR_WEIGHT_TOTAL
            )
            impact = kill_impact - death_impact

            breakdown = {
                "damage": damages,
                "econ_impact": round(kill_order_bonus_x_econ_sum - death_order_bonus_x_econ_sum),
                "time_impact": round(kill_order_bonus_x_time_sum - death_order_bonus_x_time_sum),
                "swing_impact": round(kill_order_bonus_x_swing_sum - death_order_bonus_x_swing_sum),
                "econ_kill": round(econ_mismatch_kill_sum),
                "econ_death": round(econ_mismatch_death_sum),
                "clutch_kill": round(clutch_kill_sum),
                "clutch_death": round(clutch_death_sum),
                "post_plant_kill": round(post_plant_kill_sum),
                "post_plant_death": round(post_plant_death_sum),
                "traded_teammate": trade_kill_counts[round_number].get(match_player_id, 0),
                "traded_by_teammate": trade_death_counts[round_number].get(match_player_id, 0),
                "traded_teammate_targets": {
                    str(k): v for k, v in trade_kill_targets[round_number].get(match_player_id, {}).items()
                },
                "traded_by_teammate_sources": {
                    str(k): v for k, v in trade_death_sources[round_number].get(match_player_id, {}).items()
                },
            }

            impact_score = (
                db.query(ImpactScore)
                .filter_by(round_id=round_row.id, match_player_id=match_player_id)
                .one_or_none()
            )
            if impact_score is None:
                impact_score = ImpactScore(round_id=round_row.id, match_player_id=match_player_id)
                db.add(impact_score)

            impact_score.kill_impact = kill_impact
            impact_score.death_impact = death_impact
            impact_score.impact = impact
            impact_score.breakdown = breakdown

    db.commit()
