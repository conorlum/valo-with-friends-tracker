from app.scoring.credit_events import RoundStat, compute_round_credit_events

TEAM = "team-1"
MP = 1


def _outcomes(*results):
    """results: sequence of 'win'/'loss' for TEAM, 1-indexed by round number."""
    outcomes = {}
    for i, result in enumerate(results, start=1):
        outcomes[i] = "Team A Wins" if result == "win" else "Team B Wins"
    return outcomes


def test_sugar_daddy_flags_survivor_overspend_beyond_armor_and_utility():
    # Round 2: survived (deaths=0), team won round 1 -> round_bonus 3000.
    # cash_available = 0 (prev remaining) + 0 (no kills) + 0 (no plant) + 3000 = 3000.
    # spend = 3000 - remaining(200) = 2800. Ceiling for jett (armor 1000 + util 550) = 1550.
    # Excess = 2800 - 1550 = 1250.
    stats_by_round = {
        1: {MP: RoundStat(kills=0, deaths=0, loadout=800, remaining=0)},
        2: {MP: RoundStat(kills=0, deaths=0, loadout=800, remaining=200)},
    }
    events = compute_round_credit_events(
        round_outcomes=_outcomes("win", "win"),
        planted_by_round={1: False, 2: False},
        stats_by_round=stats_by_round,
        agent_by_mp={MP: "jett"},
        team_by_mp={MP: TEAM},
    )
    sugar_daddy, scavenger = events[2][MP]
    assert sugar_daddy == 1250


def test_sugar_daddy_not_flagged_for_normal_armor_and_utility_topup():
    # Same setup but remaining stays high (1500) -> spend = 3000-1500=1500, under
    # the 1550 ceiling for jett -- normal top-up, not a gift.
    stats_by_round = {
        1: {MP: RoundStat(kills=0, deaths=0, loadout=800, remaining=0)},
        2: {MP: RoundStat(kills=0, deaths=0, loadout=800, remaining=1500)},
    }
    events = compute_round_credit_events(
        round_outcomes=_outcomes("win", "win"),
        planted_by_round={1: False, 2: False},
        stats_by_round=stats_by_round,
        agent_by_mp={MP: "jett"},
        team_by_mp={MP: TEAM},
    )
    sugar_daddy, _ = events[2][MP]
    assert sugar_daddy == 0


def test_sugar_daddy_not_flagged_if_player_died_previous_round():
    stats_by_round = {
        1: {MP: RoundStat(kills=0, deaths=1, loadout=800, remaining=0)},
        2: {MP: RoundStat(kills=0, deaths=0, loadout=4000, remaining=0)},
    }
    events = compute_round_credit_events(
        round_outcomes=_outcomes("win", "win"),
        planted_by_round={1: False, 2: False},
        stats_by_round=stats_by_round,
        agent_by_mp={MP: "jett"},
        team_by_mp={MP: TEAM},
    )
    sugar_daddy, _ = events[2][MP]
    assert sugar_daddy == 0


def test_scavenger_flags_free_pickup_after_death():
    # Round 2: died round 1 (no carryover). cash_available = 0 + 0 + 0 + round_bonus(loss since
    # team lost round 1) = 1900. wealth_after = remaining(0) + loadout(2900, a Vandal) = 2900.
    # surplus = 2900 - 1900 = 1000.
    stats_by_round = {
        1: {MP: RoundStat(kills=0, deaths=1, loadout=800, remaining=0)},
        2: {MP: RoundStat(kills=0, deaths=0, loadout=2900, remaining=0)},
    }
    events = compute_round_credit_events(
        round_outcomes=_outcomes("loss", "win"),
        planted_by_round={1: False, 2: False},
        stats_by_round=stats_by_round,
        agent_by_mp={MP: "viper"},
        team_by_mp={MP: TEAM},
    )
    _, scavenger = events[2][MP]
    assert scavenger == 1000


def test_scavenger_flags_weapon_upgrade_while_surviving_no_death_required():
    # Round 2: survived round 1 with a Spectre (loadout 1600), team lost round 1 so
    # round_bonus = 1900. cash_available = 0 + 0 + 0 + 1900 = 1900.
    # carried_over = 1600 (their own Spectre, kept for free).
    # wealth_before = 1900 + 1600 = 3500. They spend nothing (remaining stays 1900)
    # but now hold a Vandal (loadout 2900): wealth_after = 1900 + 2900 = 4800.
    # surplus = 4800 - 3500 = 1300 -- matches the example the user gave.
    stats_by_round = {
        1: {MP: RoundStat(kills=0, deaths=0, loadout=1600, remaining=0)},
        2: {MP: RoundStat(kills=0, deaths=0, loadout=2900, remaining=1900)},
    }
    events = compute_round_credit_events(
        round_outcomes=_outcomes("loss", "win"),
        planted_by_round={1: False, 2: False},
        stats_by_round=stats_by_round,
        agent_by_mp={MP: "viper"},
        team_by_mp={MP: TEAM},
    )
    _, scavenger = events[2][MP]
    assert scavenger == 1300


def test_scavenger_not_flagged_for_normal_paid_upgrade():
    # Same as above but they actually spent the money (remaining drops to 0
    # instead of staying at 1900) -- no free pickup, no surplus.
    stats_by_round = {
        1: {MP: RoundStat(kills=0, deaths=0, loadout=1600, remaining=0)},
        2: {MP: RoundStat(kills=0, deaths=0, loadout=2900, remaining=600)},
    }
    events = compute_round_credit_events(
        round_outcomes=_outcomes("loss", "win"),
        planted_by_round={1: False, 2: False},
        stats_by_round=stats_by_round,
        agent_by_mp={MP: "viper"},
        team_by_mp={MP: TEAM},
    )
    _, scavenger = events[2][MP]
    assert scavenger == 0


def test_free_ability_credits_dont_falsely_trigger_scavenger():
    # Omen has 150 phantom credits baked into loadout. A player who died round 1,
    # then bought a normal eco loadout (real value 800) this round should show
    # raw loadout 950 (800 + 150 phantom) but adjusted_loadout of 800 -- no surplus
    # if their remaining matches a real spend.
    stats_by_round = {
        1: {MP: RoundStat(kills=0, deaths=1, loadout=800, remaining=0)},
        2: {MP: RoundStat(kills=0, deaths=0, loadout=950, remaining=1100)},
    }
    # team lost round 1 -> round_bonus 1900; cash_available = 1900.
    # spend = 1900 - 1100 = 800, matching the real (non-phantom) loadout value exactly.
    events = compute_round_credit_events(
        round_outcomes=_outcomes("loss", "win"),
        planted_by_round={1: False, 2: False},
        stats_by_round=stats_by_round,
        agent_by_mp={MP: "omen"},
        team_by_mp={MP: TEAM},
    )
    _, scavenger = events[2][MP]
    assert scavenger == 0


def test_rounds_1_and_13_are_skipped_no_prior_round_to_compare():
    stats_by_round = {1: {MP: RoundStat(kills=0, deaths=0, loadout=800, remaining=0)}}
    events = compute_round_credit_events(
        round_outcomes={},
        planted_by_round={1: False},
        stats_by_round=stats_by_round,
        agent_by_mp={MP: "jett"},
        team_by_mp={MP: TEAM},
    )
    assert events == {}


def test_kill_reward_and_plant_bonus_count_toward_cash_available():
    # Round 2: survived round 1 with 2 kills (400 credits) and their (attacking)
    # team planted in round 1 (+300). Team lost round 1 -> round_bonus 1900.
    # cash_available = 0 + 400 + 300 + 1900 = 2600. remaining this round = 400
    # -> spend = 2200. Ceiling for viper (armor 1000 + util 500) = 1500.
    # Excess = 2200 - 1500 = 700.
    stats_by_round = {
        1: {MP: RoundStat(kills=2, deaths=0, loadout=2900, remaining=0)},
        2: {MP: RoundStat(kills=0, deaths=0, loadout=2900, remaining=400)},
    }
    events = compute_round_credit_events(
        round_outcomes=_outcomes("loss", "win"),
        planted_by_round={1: True, 2: False},
        stats_by_round=stats_by_round,
        agent_by_mp={MP: "viper"},
        team_by_mp={MP: TEAM},
    )
    sugar_daddy, _ = events[2][MP]
    assert sugar_daddy == 700
