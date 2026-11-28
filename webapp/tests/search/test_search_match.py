"""The pure replay-search matcher on hand-made rows (R1, R4, R5, R6)."""

from app.search import match
from app.search.filters import parse
from app.search.match import ROW_KEYS, Context, matches, our_team, select, summary


def row(**over):
    base = {
        "replay_id": 1, "uuid": "u1", "map": "Ascent", "played_at": "2026-05-01T12:00:00", "match_id": 10,
        "round": 13, "seq": 0,
        "attack_team": "team-2", "won_team": "team-2", "buy1": "full", "buy2": "eco", "score1": 5, "score2": 7,
        "planted": True, "site": "A", "plant_t": 10.0,
        "kind": "state", "t": 30.0, "t_end": None, "team": None, "slot": None, "player_id": None, "agent": None,
        "other_slot": None, "other_player_id": None, "other_agent": None, "other_team": None,
        "ability": None, "ability_key": None, "ult": False, "first": False,
        "alive1": 4, "alive2": 3, "alive_slots": [0, 1, 2, 3, 5, 6, 7], "post_plant": True, "tags": [],
        "weapon": None,
    }
    assert set(base) == set(ROW_KEYS)
    base.update(over)
    return base


def ctx(**kw):
    return Context(ours=kw.get("ours", {}), player_slots=kw.get("player_slots", {}), allowed=kw.get("allowed"))


def test_row_keys_and_constants():
    assert len(ROW_KEYS) == len(set(ROW_KEYS)) == 37
    assert set(match.KINDS) == {"state", "kill", "death", "plant", "defuse", "cast", "fight", "backshot"}
    assert set(match.BUYS) == {"pistol", "save", "eco", "force", "full"}


def test_state_row_attack_side_perspective():
    # team-2 attacks, so "side=attack" means ours = team-2: alive2=3 v alive1=4
    r = row()
    c = ctx()
    assert matches(parse(["kind=state", "state=3v4", "side=attack"]), c, r)
    assert not matches(parse(["kind=state", "state=4v3", "side=attack"]), c, r)


def test_state_row_defense_side_perspective():
    r = row()
    c = ctx()
    assert matches(parse(["state=4v3", "side=defense"]), c, r)
    assert not matches(parse(["state=3v4", "side=defense"]), c, r)


def test_our_team():
    r = row()
    assert our_team(parse(["side=attack"]), ctx(), r) == "team-2"
    assert our_team(parse(["side=defense"]), ctx(), r) == "team-1"
    assert our_team(parse(["as=A#1"]), ctx(ours={1: "team-1"}), r) == "team-1"
    assert our_team(parse(["as=A#1"]), ctx(), r) is None
    assert our_team(parse([]), ctx(), r) is None


def test_left():
    f = parse(["left=20"])
    assert matches(f, ctx(), row(plant_t=10.0, t=30.0))
    assert not matches(f, ctx(), row(plant_t=10.0, t=40.0))
    assert not matches(f, ctx(), row(plant_t=None, t=40.0, post_plant=False))
    assert not matches(f, ctx(), row(plant_t=10.0, t=30.0, post_plant=False))


def test_death_first():
    r = row(kind="death", first=True)
    assert matches(parse(["kind=death", "first=1"]), ctx(), r)
    assert not matches(parse(["kind=death", "first=0"]), ctx(), r)
    assert not matches(parse(["kind=kill", "first=1"]), ctx(), r)


def test_player_on_state_row_means_alive():
    c = ctx(player_slots={1: 2})
    f = parse(["player=A#1"])
    assert matches(f, c, row(alive_slots=[0, 2, 5]))
    assert not matches(f, c, row(alive_slots=[0, 5]))


def test_player_on_event_is_the_actor_slot():
    c = ctx(player_slots={1: 2})
    f = parse(["player=A#1"])
    assert matches(f, c, row(kind="kill", slot=2, alive_slots=[]))
    assert not matches(f, c, row(kind="kill", slot=3, alive_slots=[]))


def test_player_replay_missing_never_matches():
    assert not matches(parse(["player=A#1"]), ctx(player_slots={2: 1}), row(alive_slots=[1]))


def test_as_replay_missing_never_matches():
    f = parse(["as=A#1"])
    assert not matches(f, ctx(ours={}), row())
    assert matches(f, ctx(ours={1: "team-1"}), row())


def test_tags_all_required():
    f = parse(["tag=alone", "tag=moving"])
    assert matches(f, ctx(), row(tags=["moving", "alone", "x"]))
    assert not matches(f, ctx(), row(tags=["alone"]))


def test_ability_name_and_key():
    cast = row(kind="cast", ability="Sova/Hunter's Fury", ability_key="4")
    assert matches(parse(["ability=hunter"]), ctx(), cast)
    assert not matches(parse(["ability=sova"]), ctx(), cast)  # only the ability's name part
    assert matches(parse(["ability=4"]), ctx(), cast)
    assert not matches(parse(["ability=x"]), ctx(), cast)
    assert matches(parse(["ability=X"]), ctx(), row(kind="cast", ability="A/B", ability_key="X"))
    assert matches(parse(["ability=x"]), ctx(), row(kind="cast", ability="A/B", ability_key="X"))
    assert not matches(parse(["ability=hunter"]), ctx(), row(kind="cast", ability=None))


def test_team_ours_and_theirs():
    r = row(kind="kill", team="team-1")
    assert matches(parse(["team=ours", "side=defense"]), ctx(), r)
    assert not matches(parse(["team=theirs", "side=defense"]), ctx(), r)
    assert matches(parse(["team=theirs", "side=attack"]), ctx(), r)
    assert not matches(parse(["team=ours", "side=attack"]), ctx(), row(kind="state", team=None))


def test_agent_case_insensitive():
    r = row(kind="kill", agent="Sova")
    assert matches(parse(["agent=sova"]), ctx(), r)
    assert not matches(parse(["agent=jett"]), ctx(), r)
    assert not matches(parse(["agent=sova"]), ctx(), row())  # state row: no actor


def test_won_buy_score_round_planted_site():
    r = row()  # team-2 attacks and won; buy1 full buy2 eco; score1 5 score2 7
    assert matches(parse(["won=1", "side=attack"]), ctx(), r)
    assert not matches(parse(["won=1", "side=defense"]), ctx(), r)
    assert matches(parse(["won=0", "side=defense"]), ctx(), r)
    assert matches(parse(["our_buy=eco", "their_buy=full", "side=attack"]), ctx(), r)
    assert matches(parse(["our_score=7", "their_score=4..5", "side=attack"]), ctx(), r)
    assert not matches(parse(["our_score=0..6", "side=attack"]), ctx(), r)
    assert matches(parse(["round=12..14"]), ctx(), r)
    assert not matches(parse(["round=1..12"]), ctx(), r)
    assert matches(parse(["planted=1", "site=a"]), ctx(), r)
    assert not matches(parse(["planted=0"]), ctx(), r)
    assert not matches(parse(["site=B"]), ctx(), r)


def test_undecided_round_matches_neither_won_value():
    r = row(won_team=None)
    assert not matches(parse(["won=1", "side=attack"]), ctx(), r)
    assert not matches(parse(["won=0", "side=attack"]), ctx(), r)


def test_state_none_never_matches():
    r = row(alive1=None, alive2=None)
    assert not matches(parse(["state=0v0", "side=attack"]), ctx(), r)


def test_event_state_uses_row_counts():
    r = row(kind="kill", alive1=5, alive2=2, post_plant=False)
    assert matches(parse(["kind=kill", "state=5v2", "side=defense"]), ctx(), r)
    assert matches(parse(["post_plant=0"]), ctx(), r)
    assert not matches(parse(["post_plant=1"]), ctx(), r)


def test_after_before_inclusive():
    r = row(t=30.0)
    assert matches(parse(["after=30", "before=30"]), ctx(), r)
    assert not matches(parse(["after=30.5"]), ctx(), r)
    assert not matches(parse(["before=29.9"]), ctx(), r)


def test_map_dates_allowed_ult():
    r = row(played_at="2026-05-01T23:59:59")
    assert matches(parse(["map=ASCENT"]), ctx(), r)
    assert not matches(parse(["map=haven"]), ctx(), r)
    assert matches(parse(["since=2026-05-01", "until=2026-05-01"]), ctx(), r)
    assert not matches(parse(["since=2026-05-02"]), ctx(), r)
    assert not matches(parse(["until=2026-04-30"]), ctx(), r)
    assert not matches(parse(["since=2026-01-01"]), ctx(), row(played_at=None))
    assert matches(parse(["with=B#2"]), ctx(allowed=frozenset({1})), r)
    assert not matches(parse(["with=B#2"]), ctx(allowed=frozenset({2})), r)
    assert matches(parse([]), ctx(allowed=None), r)
    u = row(kind="cast", ult=True)
    assert matches(parse(["ult=1"]), ctx(), u)
    assert not matches(parse(["ult=0"]), ctx(), u)


def test_select_order():
    rows = [
        row(replay_id=1, played_at="2026-05-01T10:00:00", round=2, t=5.0, seq=0),
        row(replay_id=2, played_at=None, round=1, t=1.0, seq=0),
        row(replay_id=3, played_at="2026-06-01T10:00:00", round=3, t=9.0, seq=1),
        row(replay_id=3, played_at="2026-06-01T10:00:00", round=3, t=9.0, seq=0),
        row(replay_id=3, played_at="2026-06-01T10:00:00", round=1, t=50.0, seq=0),
        row(replay_id=4, played_at="2026-06-01T10:00:00", round=1, t=1.0, seq=0),
    ]
    out = select(parse([]), ctx(), rows)
    got = [(r["replay_id"], r["round"], r["t"], r["seq"]) for r in out]
    assert got == [(4, 1, 1.0, 0), (3, 1, 50.0, 0), (3, 3, 9.0, 0), (3, 3, 9.0, 1), (1, 2, 5.0, 0), (2, 1, 1.0, 0)]


def test_select_filters():
    rows = [row(kind="kill"), row(kind="death")]
    assert [r["kind"] for r in select(parse(["kind=kill"]), ctx(), rows)] == ["kill"]


def names(mapping):
    return lambda slot: mapping.get(slot)


def test_summary_kill_named_and_enemy():
    r = row(kind="kill", slot=1, agent="Jett", other_slot=6, other_agent="Sova", weapon="Vandal",
            alive1=3, alive2=4, post_plant=True, plant_t=10.0, t=24.0, team="team-1", other_team="team-2")
    s = summary(r, names({1: "Alpha"}), "team-1")
    assert s == "Alpha killed the enemy Sova (Vandal), 3v4 post-plant, 31 s on the spike"


def test_summary_perspective_flips_counts():
    r = row(kind="kill", slot=1, agent="Jett", other_slot=6, other_agent="Sova", weapon="Vandal",
            alive1=3, alive2=4, post_plant=False, plant_t=None, t=24.0)
    assert "4v3" in summary(r, names({}), "team-2")
    assert "3v4" in summary(r, names({}), "team-1")
    assert "post-plant" not in summary(r, names({}), "team-1")


def test_summary_no_perspective_all_by_agent():
    r = row(kind="kill", slot=1, agent="Jett", other_slot=6, other_agent="Sova", weapon="Vandal",
            post_plant=False, plant_t=None)
    s = summary(r, names({}), None)
    assert s.startswith("Jett killed Sova (Vandal)")
    assert "the enemy" not in s


def test_summary_death_and_cast():
    d = row(kind="death", slot=6, agent="Sova", other_slot=1, other_agent="Jett", weapon="Vandal",
            post_plant=False, plant_t=None)
    assert summary(d, names({1: "Alpha"}), "team-1").startswith("the enemy Sova was killed by Alpha (Vandal)")
    c = row(kind="cast", slot=1, agent="Sova", ability="Sova/Hunter's Fury", post_plant=False, plant_t=None)
    assert summary(c, names({1: "Alpha"}), "team-1").startswith("Alpha cast Hunter's Fury")


def test_summary_state_has_no_actor():
    s = summary(row(post_plant=False, plant_t=None), names({}), "team-1")
    assert "4v3" in s


def test_row_keys_has_ability_not_ability_id():
    assert "ability" in ROW_KEYS and "ability_id" not in ROW_KEYS


def test_summary_plant_and_defuse_without_actor():
    p = row(kind="plant", site="A", slot=None, agent=None, alive1=None, alive2=None, post_plant=False, plant_t=None)
    assert summary(p, names({}), "team-2") == "Spike planted on A"
    assert summary(p, names({}), None) == "Spike planted on A"
    assert summary(row(kind="plant", site=None, post_plant=False, plant_t=None, alive1=None, alive2=None), names({}), None) == "Spike planted"
    d = row(kind="defuse", slot=None, agent=None, alive1=None, alive2=None, post_plant=False, plant_t=None)
    assert summary(d, names({}), "team-1") == "Spike defused"
    named = row(kind="plant", site="B", slot=1, agent="Jett", alive1=None, alive2=None, post_plant=False, plant_t=None)
    assert summary(named, names({1: "Alpha"}), "team-2") == "Alpha planted the spike on B"
