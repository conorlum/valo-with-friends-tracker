from collections import Counter

from app.services.squad import SharedRound, build_squad_overview


def _round(won=True, viewer_rwi=1.0, friend_rwi=1.0, clutch=False, traded=0,
           vk=1, vd=0, fk=1, fd=0, match_id=1, sugar_daddy=0, scavenger=0):
    return SharedRound(
        match_id=match_id,
        won=won,
        viewer_round_win_impact=viewer_rwi,
        friend_round_win_impact=friend_rwi,
        clutch=clutch,
        traded=traded,
        viewer_kills=vk,
        viewer_deaths=vd,
        friend_kills=fk,
        friend_deaths=fd,
        sugar_daddy_credits=sugar_daddy,
        scavenger_credits=scavenger,
    )


def test_pairs_sorted_by_matches_together_descending():
    pair_shared_rounds = {
        1: [_round(match_id=1), _round(match_id=2)],  # 2 matches
        2: [_round(match_id=1)],  # 1 match
    }
    friend_names = {1: "Alice#NA1", 2: "Bob#NA1"}
    friend_agent_counts = {1: Counter({"Jett": 2}), 2: Counter({"Omen": 1})}

    overview = build_squad_overview(pair_shared_rounds, friend_names, friend_agent_counts, {1, 2})

    assert [p.friend_player_id for p in overview.pairs] == [1, 2]
    assert overview.squad_size == 2
    assert overview.total_matches_together == 2  # viewer's own in-window match count


def test_win_rate_round_win_impact_clutch_and_trade_aggregation():
    rounds = [
        _round(won=True, viewer_rwi=2.0, friend_rwi=1.0, clutch=True, traded=1, vk=2, vd=0, fk=1, fd=1),
        _round(won=False, viewer_rwi=-1.0, friend_rwi=0.0, clutch=False, traded=0, vk=0, vd=1, fk=0, fd=1),
    ]
    pair_shared_rounds = {1: rounds}
    friend_names = {1: "Alice#NA1"}
    friend_agent_counts = {1: Counter({"Jett": 1})}

    overview = build_squad_overview(pair_shared_rounds, friend_names, friend_agent_counts, {1})
    pair = overview.pairs[0]

    assert pair.rounds_together == 2
    assert pair.win_rate_together == 0.5
    # (2.0+1.0) + (-1.0+0.0) = 2.0, / 2 rounds = 1.0
    assert pair.avg_round_win_impact_together == 1.0
    assert pair.clutches_together == 1
    assert pair.traded_together == 1
    # kills: 2+1+0+0=3, deaths: 0+1+1+1=3 -> differential 0
    assert pair.kill_differential_together == 0
    assert pair.most_played_agent_together == "Jett"


def test_sugar_daddy_and_scavenger_credits_sum_across_shared_rounds():
    rounds = [
        _round(match_id=1, sugar_daddy=500, scavenger=0),
        _round(match_id=1, sugar_daddy=0, scavenger=1300),
    ]
    pair_shared_rounds = {1: rounds}
    friend_names = {1: "Alice#NA1"}
    friend_agent_counts = {1: Counter({"Jett": 1})}

    overview = build_squad_overview(pair_shared_rounds, friend_names, friend_agent_counts, {1})
    pair = overview.pairs[0]

    assert pair.sugar_daddy_credits_together == 500
    assert pair.scavenger_credits_together == 1300


def test_friend_below_threshold_appears_in_table_but_not_shoutouts():
    below_threshold_rounds = [_round(match_id=1)] * 19  # SQUAD_ROUND_THRESHOLD is 20
    pair_shared_rounds = {1: below_threshold_rounds}
    friend_names = {1: "Alice#NA1"}
    friend_agent_counts = {1: Counter({"Jett": 1})}

    overview = build_squad_overview(pair_shared_rounds, friend_names, friend_agent_counts, {1})

    assert len(overview.pairs) == 1
    assert overview.pairs[0].rounds_together == 19
    assert overview.shoutouts == []


def test_empty_squad():
    overview = build_squad_overview({}, {}, {}, set())
    assert overview.squad_size == 0
    assert overview.total_matches_together == 0
    assert overview.total_rounds_together == 0
    assert overview.pairs == []
    assert overview.shoutouts == []
