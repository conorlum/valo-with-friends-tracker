"""Match page result banner / scoreboard helpers: who won, which team the
viewer should read as theirs, and the round strip's per-round winner."""
from types import SimpleNamespace

import app.services.matches as matches
from app.services.matches import (
    MatchSummary,
    PlayerSummary,
    get_viewer_team,
    match_round_timeline,
    match_winner_team,
    round_end_type,
    round_impact_bars,
    round_loadout_bars,
    cumulative_impact_series,
)


def _player(player_id: int, team: str) -> PlayerSummary:
    return PlayerSummary(
        match_player_id=player_id * 10,
        display_name=f"p{player_id}",
        agent="Jett",
        team=team,
        average_impact=0.0,
        average_kill_impact=0.0,
        average_death_impact=0.0,
        average_round_win_impact=0.0,
        impact_by_round={},
        kill_impact_by_round={},
        death_impact_by_round={},
        player_id=player_id,
    )


def _summary(players, round_outcomes=None) -> MatchSummary:
    return MatchSummary(players=players, round_numbers=[], round_outcomes=round_outcomes or {}, team_summaries=[])


def test_match_winner_team():
    assert match_winner_team(SimpleNamespace(team1_rounds_won=13, team2_rounds_won=9)) == "team-1"
    assert match_winner_team(SimpleNamespace(team1_rounds_won=7, team2_rounds_won=13)) == "team-2"
    assert match_winner_team(SimpleNamespace(team1_rounds_won=16, team2_rounds_won=16)) is None


def test_viewer_team_logged_out_is_neutral():
    assert get_viewer_team(None, _summary([_player(1, "team-1")]), None) == (None, None)


def test_viewer_team_is_own_team_when_viewer_played(monkeypatch):
    monkeypatch.setattr(matches, "list_friend_ids", lambda db, pid: pytest_fail())
    summary = _summary([_player(1, "team-1"), _player(2, "team-2")])
    assert get_viewer_team(None, summary, 2) == ("team-2", "You")


def test_viewer_team_falls_back_to_the_team_with_more_friends(monkeypatch):
    monkeypatch.setattr(matches, "list_friend_ids", lambda db, pid: {1, 3, 4})
    summary = _summary([_player(1, "team-1"), _player(3, "team-2"), _player(4, "team-2")])
    assert get_viewer_team(None, summary, 99) == ("team-2", "Your friends")


def test_viewer_team_is_neutral_when_friends_split_evenly_or_absent(monkeypatch):
    summary = _summary([_player(1, "team-1"), _player(3, "team-2")])
    monkeypatch.setattr(matches, "list_friend_ids", lambda db, pid: {1, 3})
    assert get_viewer_team(None, summary, 99) == (None, None)
    monkeypatch.setattr(matches, "list_friend_ids", lambda db, pid: set())
    assert get_viewer_team(None, summary, 99) == (None, None)


def test_round_timeline_is_in_play_order_with_winners_end_types_and_team_impact():
    summary = _summary([], {2: "Team B Elimination Win", 1: "Team A Defuse Win", 3: None})
    summary.team_summaries_by_team = {"team-1": SimpleNamespace(impact_by_round={1: 120.4, 2: -80.0})}
    timeline = match_round_timeline(summary)
    assert [(r["round_number"], r["winner"], r["end_type"]) for r in timeline] == [
        (1, "team-1", "defuse"),
        (2, "team-2", "elimination"),
        (3, None, None),
    ]
    assert timeline[0]["team_impact"] == {"team-1": 120.4, "team-2": None}
    assert timeline[2]["team_impact"] == {"team-1": None, "team-2": None}


def test_round_end_type():
    assert [
        round_end_type(o)
        for o in ["Team A Elimination Win", "Team B Defuse Win", "Team A Detonate Win", "Team B Time Win",
                  "Team A Surrendered Win", None, "something new"]
    ] == ["elimination", "defuse", "detonate", "time", "surrender", None, None]


def test_round_impact_bars_share_one_scale_around_a_zero_line():
    timeline = [
        {"round_number": 1, "team_impact": {"team-1": 300.0, "team-2": -100.0}},
        {"round_number": 2, "team_impact": {"team-1": 150.0, "team-2": None}},
    ]
    bars = round_impact_bars(timeline, height_px=80)
    # 300 up + 100 down spans the 80px: zero line 60px down, 0.2px per unit.
    assert bars["zero_top"] == 60
    assert bars["rounds"][1]["team-1"] == {"top": 0, "height": 60, "value": 300.0}
    assert bars["rounds"][1]["team-2"] == {"top": 60, "height": 20, "value": -100.0}
    assert bars["rounds"][2] == {"team-1": {"top": 30, "height": 30, "value": 150.0}}


def test_round_impact_bars_with_no_impact_rows():
    bars = round_impact_bars([{"round_number": 1, "team_impact": {"team-1": None, "team-2": None}}], height_px=64)
    assert bars["rounds"] == {1: {}}


def test_round_loadout_bars_scale_from_zero_to_the_match_max():
    econ = {
        1: SimpleNamespace(team1_loadout=800, team2_loadout=400, team1_tier_label="Pistol", team2_tier_label="Pistol"),
        2: SimpleNamespace(team1_loadout=4000, team2_loadout=0, team1_tier_label="Full Buy", team2_tier_label="Eco"),
    }
    bars = round_loadout_bars(econ, height_px=40)
    assert bars["rounds"][2]["team-1"] == {"top": 0, "height": 40, "value": 4000, "tier": "Full Buy"}
    assert bars["rounds"][2]["team-2"]["height"] == 0
    assert bars["rounds"][1]["team-1"]["height"] == 8
    assert bars["rounds"][1]["team-2"]["top"] == 36


def test_cumulative_impact_carries_missing_rounds_forward():
    p = _player(1, "team-1")
    p.impact_by_round = {1: 100.0, 3: -40.0}
    summary = _summary([p])
    summary.round_numbers = [1, 2, 3]
    [series] = cumulative_impact_series(summary)
    assert series["data"] == [100.0, 100.0, 60.0]
    assert (series["label"], series["team"], series["player_id"]) == ("p1", "team-1", 1)


def pytest_fail():
    raise AssertionError("friend lookup should be skipped when the viewer played in the match")


def test_strip_tag_drops_every_tag_in_a_joined_list():
    from app.templates import strip_tag

    assert strip_tag("NPrightdolphin#NA1") == "NPrightdolphin"
    assert strip_tag("Beef Shortrib#Galbi, ayoko na#NA1") == "Beef Shortrib, ayoko na"
    assert strip_tag("noTag") == "noTag"
    assert strip_tag(None) is None


def test_death_impact_filter_shows_the_signed_cost():
    from app.templates import death_impact

    assert [death_impact(136.4), death_impact(0.2), death_impact(None)] == ["-136", "0", "0"]
