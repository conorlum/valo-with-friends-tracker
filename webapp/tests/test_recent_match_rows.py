from types import SimpleNamespace

from app.services.recent_match_rows import _quantile, _round_first_kill_and_clutch


def _round(*kills):
    """kills: (time, killer_mp_id, victim_mp_id) tuples, any order."""
    events = [
        SimpleNamespace(id=i, event_time_seconds=t, killer_match_player_id=k, death_match_player_id=v)
        for i, (t, k, v) in enumerate(kills)
    ]
    return SimpleNamespace(kill_events=events)


OWN = {1, 2, 3, 4, 5}
OPP = {6, 7, 8, 9, 10}


def test_first_blood_and_first_death_use_the_earliest_kill():
    assert _round_first_kill_and_clutch(_round((20, 6, 2), (5, 1, 7)), 1, OWN, OPP)[:2] == (True, False)
    assert _round_first_kill_and_clutch(_round((5, 6, 1)), 1, OWN, OPP)[:2] == (False, True)


def test_clutch_vs_counts_enemies_alive_when_the_player_is_left_alone():
    # Teammates 2-5 die after player 1 killed one enemy -> a 1v4 for player 1.
    r = _round((1, 7, 2), (2, 8, 3), (3, 1, 6), (4, 9, 4), (5, 9, 5), (6, 1, 7), (7, 1, 8), (8, 1, 9))
    assert _round_first_kill_and_clutch(r, 1, OWN, OPP)[2] == 4


def test_no_clutch_when_the_player_dies_or_teammates_survive():
    assert _round_first_kill_and_clutch(_round((1, 6, 1)), 1, OWN, OPP)[2] is None
    assert _round_first_kill_and_clutch(_round((1, 1, 6)), 1, OWN, OPP)[2] is None


def test_quantile_picks_the_top_fifth_boundary():
    assert _quantile(list(range(10)), 0.8) == 8
    assert _quantile([5.0], 0.8) == 5.0
