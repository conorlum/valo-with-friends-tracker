from app.models.round import Round
from app.models.match import Team
from app.scoring import plant_window
from app.scoring.plant_window import (
    PRE_WINDOW,
    POST_WINDOW,
    attacking_team,
    effective_plant_time,
    in_plant_window,
    is_phantom_plant,
    seconds_to_plant,
    window_bucket,
)


def _round(**kwargs) -> Round:
    defaults = dict(round_number=5, outcome=None, planted=False, plant_time=None, exploded=False, defused=False, defuse_time=None)
    defaults.update(kwargs)
    return Round(**defaults)


# -- is_phantom_plant --------------------------------------------------------

def test_phantom_plant_is_planted_round_ending_in_time_win():
    r = _round(planted=True, plant_time=101.0, outcome="Team A Time Win")
    assert is_phantom_plant(r) is True


def test_elimination_win_with_noisy_late_plant_time_is_not_phantom():
    r = _round(planted=True, plant_time=101.0, outcome="Team B Elimination Win")
    assert is_phantom_plant(r) is False


def test_unplanted_round_is_not_phantom():
    r = _round(planted=False, plant_time=None, outcome="Team A Elimination Win")
    assert is_phantom_plant(r) is False


def test_detonate_win_is_not_phantom():
    r = _round(planted=True, plant_time=40.0, outcome="Team A Detonate Win")
    assert is_phantom_plant(r) is False


# -- effective_plant_time -----------------------------------------------------

def test_effective_plant_time_none_for_unplanted():
    r = _round(planted=False, plant_time=None, outcome="Team A Elimination Win")
    assert effective_plant_time(r) is None


def test_effective_plant_time_none_for_phantom():
    r = _round(planted=True, plant_time=101.0, outcome="Team B Time Win")
    assert effective_plant_time(r) is None


def test_effective_plant_time_is_plant_time_for_real_plant():
    r = _round(planted=True, plant_time=40.0, outcome="Team A Detonate Win")
    assert effective_plant_time(r) == 40.0


# -- attacking_team -----------------------------------------------------------

def test_attacking_team_regulation_first_half():
    assert attacking_team(1) == Team.TEAM_1
    assert attacking_team(12) == Team.TEAM_1


def test_attacking_team_regulation_second_half():
    assert attacking_team(13) == Team.TEAM_2
    assert attacking_team(24) == Team.TEAM_2


def test_attacking_team_overtime_resets_to_round_one_side_then_alternates():
    assert attacking_team(25) == Team.TEAM_1
    assert attacking_team(26) == Team.TEAM_2
    assert attacking_team(27) == Team.TEAM_1
    assert attacking_team(30) == Team.TEAM_2


def test_attacking_team_none_below_round_one():
    assert attacking_team(0) is None
    assert attacking_team(-1) is None


def _outcome_winner(outcome: str) -> Team:
    return Team.TEAM_1 if outcome.startswith("Team A") else Team.TEAM_2


def _outcome_derived_attacker(outcome: str) -> Team:
    # Independent derivation, per the spec: a Time Win or a Defuse Win means
    # the winner defended (so the OTHER team attacked); a Detonate Win means
    # the winner attacked. Deliberately not implemented in plant_window.py
    # itself -- it exists only to cross-check attacking_team's convention.
    winner = _outcome_winner(outcome)
    other = Team.TEAM_2 if winner == Team.TEAM_1 else Team.TEAM_1
    if "Detonate" in outcome:
        return winner
    if "Time" in outcome or "Defuse" in outcome:
        return other
    raise ValueError(f"not outcome-determinable: {outcome!r}")


def test_attacking_team_matches_outcome_derived_convention_over_a_sample():
    # Table-driven check against a sample of round/outcome pairs, spanning
    # regulation and overtime and every outcome-determinable result type,
    # cross-checked with an independent outcome-derivation instead of a
    # second copy of attacking_team's own arithmetic.
    cases = [
        (1, "Team A Detonate Win"),
        (5, "Team B Defuse Win"),
        (12, "Team B Time Win"),
        (13, "Team B Detonate Win"),
        (20, "Team A Defuse Win"),
        (24, "Team A Time Win"),
        (25, "Team A Detonate Win"),
        (26, "Team B Detonate Win"),
        (27, "Team B Defuse Win"),
        (30, "Team A Time Win"),
    ]
    for round_number, outcome in cases:
        assert attacking_team(round_number) == _outcome_derived_attacker(outcome), (round_number, outcome)


# -- seconds_to_plant ---------------------------------------------------------

def test_seconds_to_plant_none_for_unplanted():
    r = _round(planted=False)
    assert seconds_to_plant(r, 20.0) is None


def test_seconds_to_plant_none_for_phantom():
    r = _round(planted=True, plant_time=105.0, outcome="Team A Time Win")
    assert seconds_to_plant(r, 20.0) is None


def test_seconds_to_plant_positive_before_plant():
    r = _round(planted=True, plant_time=40.0, outcome="Team A Detonate Win")
    assert seconds_to_plant(r, 30.0) == 10.0


def test_seconds_to_plant_negative_after_plant():
    r = _round(planted=True, plant_time=40.0, outcome="Team A Detonate Win")
    assert seconds_to_plant(r, 50.0) == -10.0


def test_seconds_to_plant_zero_at_plant_instant():
    r = _round(planted=True, plant_time=40.0, outcome="Team A Detonate Win")
    assert seconds_to_plant(r, 40.0) == 0.0


# -- in_plant_window ------------------------------------------------------------

def test_in_plant_window_true_at_pre_window_boundary():
    r = _round(planted=True, plant_time=40.0, outcome="Team A Detonate Win")
    assert in_plant_window(r, 40.0 - PRE_WINDOW) is True


def test_in_plant_window_false_just_beyond_pre_window():
    r = _round(planted=True, plant_time=40.0, outcome="Team A Detonate Win")
    assert in_plant_window(r, 40.0 - PRE_WINDOW - 0.01) is False


def test_in_plant_window_true_at_post_window_boundary():
    r = _round(planted=True, plant_time=40.0, outcome="Team A Detonate Win")
    assert in_plant_window(r, 40.0 + POST_WINDOW) is True


def test_in_plant_window_false_just_beyond_post_window():
    r = _round(planted=True, plant_time=40.0, outcome="Team A Detonate Win")
    assert in_plant_window(r, 40.0 + POST_WINDOW + 0.01) is False


def test_in_plant_window_false_for_unplanted_round():
    r = _round(planted=False)
    assert in_plant_window(r, 20.0) is False


# -- window_bucket --------------------------------------------------------------

def test_window_bucket_labels_pre_plant_buckets():
    r = _round(planted=True, plant_time=40.0, outcome="Team A Detonate Win")
    assert window_bucket(r, 40.0 - 25.0) == "-30..-20"
    assert window_bucket(r, 40.0 - 15.0) == "-20..-10"
    assert window_bucket(r, 40.0 - 7.0) == "-10..-5"
    assert window_bucket(r, 40.0 - 2.0) == "-5..0"


def test_window_bucket_labels_post_plant_buckets():
    r = _round(planted=True, plant_time=40.0, outcome="Team A Detonate Win")
    assert window_bucket(r, 40.0 + 2.0) == "0..+5"
    assert window_bucket(r, 40.0 + 10.0) == "+5..+15"


def test_window_bucket_none_outside_every_bucket():
    r = _round(planted=True, plant_time=40.0, outcome="Team A Detonate Win")
    assert window_bucket(r, 40.0 - 100.0) is None
    assert window_bucket(r, 40.0 + 100.0) is None


def test_window_bucket_none_for_unplanted_round():
    r = _round(planted=False)
    assert window_bucket(r, 20.0) is None


def test_window_bucket_none_for_phantom_plant():
    r = _round(planted=True, plant_time=105.0, outcome="Team B Time Win")
    assert window_bucket(r, 90.0) is None
