from dataclasses import dataclass

from app.scoring.agent_economy import ARMOR_COST, free_ability_credits, max_utility_cost
from app.scoring.impact import KILL_REWARD, PLANT_BONUS, WIN_BONUS


@dataclass
class RoundStat:
    kills: int
    deaths: int
    loadout: int
    remaining: int


def _did_team_win(outcome: str | None, team: str) -> bool:
    if not outcome:
        return False
    if outcome.startswith("Team A"):
        return team == "team-1"
    if outcome.startswith("Team B"):
        return team == "team-2"
    return False


def _attacking_team(round_number: int) -> str | None:
    # Same documented convention as app.scoring.impact._attacking_team: no
    # attacking-side data is stored in the schema, so this is a convention,
    # not a derived fact.
    if 1 <= round_number <= 12:
        return "team-1"
    if 13 <= round_number <= 24:
        return "team-2"
    return None


def _round_bonus(round_outcomes: dict[int, str], round_number: int, team: str) -> int:
    """The actual econ bonus a team earns going into `round_number`, based on
    the real outcome of round_number - 1 (unlike
    app.scoring.impact._min_next_round_econ_bonus, which is a deliberately
    pessimistic forward-looking estimate -- this is retrospective, since by
    the time we're computing credit events every round's outcome is known).
    """
    prev = round_number - 1
    if _did_team_win(round_outcomes.get(prev), team):
        return WIN_BONUS
    loss_streak = 1
    r = prev - 1
    while r > 0 and not _did_team_win(round_outcomes.get(r), team):
        loss_streak += 1
        r -= 1
    if loss_streak == 1:
        return 1900
    if loss_streak == 2:
        return 2400
    return 2900


def compute_round_credit_events(
    round_outcomes: dict[int, str],
    planted_by_round: dict[int, bool],
    stats_by_round: dict[int, dict[int, RoundStat]],
    agent_by_mp: dict[int, str],
    team_by_mp: dict[int, str],
) -> dict[int, dict[int, tuple[int, int]]]:
    """round_number -> match_player_id -> (sugar_daddy_credits, scavenger_credits)
    earned/spent that single round. Rounds 1 and 13 are skipped (pistol
    economy reset, no prior round to compare against).

    Sugar Daddy: a player who survived the previous round already has a free
    weapon, so any credit spend beyond armor + their agent's max utility cost
    this round can only be a weapon bought for someone else.

    Scavenger: any round where a player's cash + gear value (stripped of the
    phantom credit value tracker.gg's loadout figure assigns to a free
    signature-ability charge) exceeds what their tracked income (kills,
    plant, round win/loss bonus, plus any gear legitimately carried over from
    surviving the previous round) can explain -- the surplus is gear picked
    up for free. This fires whether or not they died last round, e.g.
    upgrading a carried-over Spectre into a found Vandal counts too.
    """
    events: dict[int, dict[int, tuple[int, int]]] = {}

    for round_number in sorted(stats_by_round):
        if round_number in (1, 13):
            continue
        prev_number = round_number - 1
        prev_stats = stats_by_round.get(prev_number)
        if not prev_stats:
            continue
        current_stats = stats_by_round[round_number]

        round_events: dict[int, tuple[int, int]] = {}
        for match_player_id, stat in current_stats.items():
            prev_stat = prev_stats.get(match_player_id)
            if prev_stat is None:
                continue
            team = team_by_mp.get(match_player_id)
            if team is None:
                continue
            agent = agent_by_mp.get(match_player_id)

            plant_bonus = (
                PLANT_BONUS
                if planted_by_round.get(prev_number) and _attacking_team(prev_number) == team
                else 0
            )
            round_bonus = _round_bonus(round_outcomes, round_number, team)
            cash_available = prev_stat.remaining + prev_stat.kills * KILL_REWARD + plant_bonus + round_bonus

            sugar_daddy_credits = 0
            if prev_stat.deaths == 0:
                spend = max(0, cash_available - stat.remaining)
                ceiling = ARMOR_COST + max_utility_cost(agent)
                if spend > ceiling:
                    sugar_daddy_credits = spend - ceiling

            free_credits = free_ability_credits(agent)
            adjusted_loadout = max(0, stat.loadout - free_credits)
            carried_over = max(0, prev_stat.loadout - free_credits) if prev_stat.deaths == 0 else 0
            wealth_after = stat.remaining + adjusted_loadout
            wealth_before = cash_available + carried_over
            scavenger_credits = max(0, wealth_after - wealth_before)

            round_events[match_player_id] = (sugar_daddy_credits, scavenger_credits)

        events[round_number] = round_events

    return events
