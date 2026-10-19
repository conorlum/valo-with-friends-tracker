import pytest

from app.coach import snapshot
from app.services import enemy_names
from app.services.replay_moments import home_team, tab_players
from tests.coach.test_snapshot import built

SWAPS = enemy_names.replacements([("travis kelce#NA1", "Neon"), ("M0mmy#0001", "Jett"), ("1234#9", "Sova")])


def test_names_are_swapped_full_short_and_any_case():
    text = "In round 1 you killed travis kelce at 7.97s and in round 4 you killed m0mmy#0001 at 5.39s."
    assert enemy_names.scrub(text, SWAPS) == ("In round 1 you killed the enemy Neon at 7.97s and in round 4 you "
                                              "killed the enemy Jett at 5.39s.")


def test_a_sentence_that_starts_with_a_name_starts_with_the():
    assert enemy_names.scrub("Travis kelce traded. M0mmy's peek", SWAPS) == "The enemy Neon traded. The enemy Jett's peek"


def test_a_name_inside_a_word_or_a_digit_only_short_name_is_left_alone():
    assert enemy_names.scrub("M0mmyx took 1234 damage; 1234#9 died", SWAPS) == "M0mmyx took 1234 damage; the enemy Sova died"
    assert enemy_names.scrub(None, SWAPS) is None and enemy_names.scrub("x", {}) == "x"


def test_a_run_snapshot_names_only_the_subjects_team(tmp_path):
    data = snapshot.hide_enemy_names(built(), 102)          # slot 2, team1
    snapshot.save(data, tmp_path)
    snap = snapshot.load(tmp_path)
    assert snap.names[0] == "P0#T0" and snap.names[4] == "P4#T4"
    assert snap.names[5] == "the enemy Jett" and "P5#T5" not in (tmp_path / snapshot.FILE).read_text(encoding="utf-8")
    assert built()["context"]["players"]["5"]["name"] == "P5#T5"        # the original is not changed


def test_a_run_for_a_player_not_in_the_replay_is_refused():
    with pytest.raises(snapshot.SnapshotError):
        snapshot.hide_enemy_names(built(), 999)


PAGE = {str(s): {"slot": s, "name": f"P{s}", "agent": f"A{s}", "team": "team1" if s < 5 else "team2",
                 "player_id": 100 + s} for s in range(10)}


def test_the_moments_tab_names_the_friends_team_and_the_others_by_agent():
    players = tab_players(PAGE, {105, 106, 101})
    assert home_team(PAGE, {105, 106, 101}) == "team2"
    assert players["5"] == {"name": "P5", "agent": "A5", "team": "team2"}
    assert players["1"]["name"] == "the enemy A1"


def test_with_no_home_team_no_name_is_shown():
    assert home_team(PAGE, {101, 105}) is None
    assert all(p["name"].startswith("the enemy") for p in tab_players(PAGE, set()).values())
