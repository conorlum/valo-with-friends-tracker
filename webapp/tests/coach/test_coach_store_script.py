import pytest

from app.models import Player
from scripts import coach_store_report as script


def test_a_player_is_resolved_by_display_name_within_the_match(db, world):
    replay, ids = world
    name = db.get(Player, ids[2]).display_name
    assert script.resolve(db, str(replay.match_uuid), name.upper()) == (replay.match_id, ids[2])


def test_a_player_not_in_the_match_is_refused_with_the_names(db, world):
    replay, ids = world
    with pytest.raises(ValueError, match=db.get(Player, ids[0]).display_name):
        script.resolve(db, str(replay.match_uuid), "someone else")


def test_a_players_team_is_their_side_of_the_match_with_them_first(db, world):
    replay, ids = world
    name = lambda i: db.get(Player, ids[i]).display_name
    team = script.team_names(db, str(replay.match_uuid), name(2))
    assert team[0] == name(2) and sorted(team) == sorted(name(i) for i in range(5))
    assert sorted(script.team_names(db, str(replay.match_uuid), name(7))) == sorted(name(i) for i in range(5, 10))
    with pytest.raises(ValueError, match="not a player of this match"):
        script.team_names(db, str(replay.match_uuid), "someone else")


def test_an_unknown_or_unlinked_match_is_refused(db, world):
    with pytest.raises(ValueError, match="no linked replay"):
        script.resolve(db, "00000000-0000-4000-8000-000000000000", "x")
