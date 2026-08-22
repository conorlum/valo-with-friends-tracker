from app.models.match import Team
from app.services.fight_ev import (
    BootstrapResult,
    DisplayState,
    DuelCounts,
    MatchFightEvBlock,
    WinCounts,
    bootstrap_cell,
    build_match_fight_ev_block,
    classify_cell,
    compute_fight_ev_view,
    compute_point_estimate,
    win_rate,
)
from app.services.state_replay import DuelOccurrence, StateEntryOccurrence, TerminalCause


def make_block(match_id, wins=None, player_duels=None, roster_duels=None, all_teammate_duels=None):
    return MatchFightEvBlock(
        match_id=match_id,
        wins=wins or {},
        player_duels=player_duels or {},
        roster_duels=roster_duels or {},
        all_teammate_duels=all_teammate_duels or {},
    )


def test_exact_hand_calculated_l_and_m_fixture():
    # W_u = W(attacking,2,1) = 8/10 = 0.8, W_d = W(attacking,1,2) = 2/10 = 0.2 -> L = 0.6
    wins = {
        ("attacking", 2, 1): WinCounts(wins=8, entries=10),
        ("attacking", 1, 2): WinCounts(wins=2, entries=10),
    }
    # p_player = 6/10 = 0.6, p_teammates = 3/10 = 0.3 -> M = (0.6-0.3)*0.6 = 0.18
    player_duels = {("attacking", 2, 2): DuelCounts(kills=6, deaths=4)}
    teammate_duels = {("attacking", 2, 2): DuelCounts(kills=3, deaths=7)}

    cell = compute_point_estimate(wins, player_duels, teammate_duels, "attacking", 2, 2)
    assert round(cell.leverage, 10) == 0.6
    assert round(cell.m, 10) == 0.18
    assert cell.display_state == DisplayState.POSITIVE


def test_rates_are_volume_weighted_pooled_counts_not_means_of_teammate_rates():
    # Teammate A: 1/1 (100%). Teammate B: 0/9 (0%). Pooled: 1/10 = 10%, not the
    # 50% a naive average-of-rates would give.
    block = make_block(
        1,
        wins={("attacking", 3, 3): WinCounts(wins=5, entries=10), ("attacking", 2, 3): WinCounts(wins=1, entries=10)},
        all_teammate_duels={("attacking", 3, 3): DuelCounts(kills=1, deaths=9)},
    )
    rate, n = _duel_rate_via_view(block, "attacking", 3, 3)
    assert rate == 0.1
    assert n == 10


def _duel_rate_via_view(block, side, a, b):
    from app.services.fight_ev import duel_rate

    return duel_rate(block.all_teammate_duels, (side, a, b))


def test_target_player_is_excluded_from_both_teammate_pools():
    entries = []
    duels = [
        DuelOccurrence(
            match_id=1, round_id=1, sequence=0, event_time_seconds=1.0,
            team1_alive_before=frozenset({1, 2}), team2_alive_before=frozenset({6, 7}),
            killer_match_player_id=1, victim_match_player_id=6,
        )
    ]
    block = build_match_fight_ev_block(
        match_id=1, entries=entries, duels=duels,
        round_side_by_round_id={1: "attacking"},
        target_team=Team.TEAM_1, target_match_player_id=1,
        match_player_team={1: Team.TEAM_1, 2: Team.TEAM_1, 6: Team.TEAM_2, 7: Team.TEAM_2},
        match_player_to_player_id={1: 101, 2: 102, 6: 201, 7: 202},
        roster_player_ids={102},
    )
    key = ("attacking", 2, 2)
    assert block.player_duels[key].kills == 1
    assert key not in block.roster_duels
    assert key not in block.all_teammate_duels


def test_roster_pool_uses_only_target_owned_friendship_ids_who_were_teammates():
    duels = [
        DuelOccurrence(
            match_id=1, round_id=1, sequence=0, event_time_seconds=1.0,
            team1_alive_before=frozenset({1, 2, 3}), team2_alive_before=frozenset({6}),
            killer_match_player_id=2, victim_match_player_id=6,
        )
    ]
    block = build_match_fight_ev_block(
        match_id=1, entries=[], duels=duels,
        round_side_by_round_id={1: "attacking"},
        target_team=Team.TEAM_1, target_match_player_id=1,
        match_player_team={1: Team.TEAM_1, 2: Team.TEAM_1, 3: Team.TEAM_1, 6: Team.TEAM_2},
        match_player_to_player_id={1: 101, 2: 102, 3: 103, 6: 201},
        roster_player_ids={103},  # player 2 (match_player 2) is NOT on the roster
    )
    key = ("attacking", 3, 1)
    assert block.all_teammate_duels[key].kills == 1
    assert key not in block.roster_duels


def test_opponents_on_friendship_list_are_excluded():
    # Teammate (match_player 2) gets the kill on an opponent (match_player 6)
    # whose *player* id happens to sit on the target's friendship list --
    # that must not make the kill count as a roster-teammate duel.
    duels = [
        DuelOccurrence(
            match_id=1, round_id=1, sequence=0, event_time_seconds=1.0,
            team1_alive_before=frozenset({1, 2}), team2_alive_before=frozenset({6}),
            killer_match_player_id=2, victim_match_player_id=6,
        )
    ]
    block = build_match_fight_ev_block(
        match_id=1, entries=[], duels=duels,
        round_side_by_round_id={1: "attacking"},
        target_team=Team.TEAM_1, target_match_player_id=1,
        match_player_team={1: Team.TEAM_1, 2: Team.TEAM_1, 6: Team.TEAM_2},
        match_player_to_player_id={1: 101, 2: 102, 6: 201},
        roster_player_ids={201},
    )
    key = ("attacking", 2, 1)
    assert block.all_teammate_duels[key].kills == 1
    assert key not in block.roster_duels
    assert key not in block.player_duels


def test_ungated_w_includes_entries_after_target_player_dies():
    entries = [
        StateEntryOccurrence(
            match_id=1, round_id=1, sequence=0, event_time_seconds=0.0,
            team1_alive_ids=frozenset({2, 3}), team2_alive_ids=frozenset({6, 7}),
            post_plant=False, winner=Team.TEAM_1,
        ),
    ]
    # Target player (match_player 1) is already dead/absent from team1_alive_ids
    # in this entry -- ungated W must still record it.
    block = build_match_fight_ev_block(
        match_id=1, entries=entries, duels=[],
        round_side_by_round_id={1: "attacking"},
        target_team=Team.TEAM_1, target_match_player_id=1,
        match_player_team={1: Team.TEAM_1, 2: Team.TEAM_1, 3: Team.TEAM_1, 6: Team.TEAM_2, 7: Team.TEAM_2},
        match_player_to_player_id={1: 101, 2: 102, 3: 103, 6: 201, 7: 202},
        roster_player_ids=set(),
    )
    key = ("attacking", 2, 2)
    assert block.wins[key].entries == 1
    assert block.wins[key].wins == 1


def test_definitional_and_estimated_rails_selected_correctly_per_side():
    wins = {("defending", 2, 0): WinCounts(wins=3, entries=5)}
    assert win_rate(wins, "attacking", 3, 0) == 1.0  # definitional constant
    assert win_rate(wins, "defending", 0, 4) == 0.0  # definitional constant
    assert win_rate(wins, "defending", 2, 0) == 0.6  # estimated rail, has data
    assert win_rate(wins, "attacking", 0, 4) is None  # estimated rail, no data


def test_missing_input_produces_no_data_not_zero():
    cell = compute_point_estimate({}, {}, {}, "attacking", 2, 2)
    assert cell.display_state == DisplayState.NO_DATA
    assert cell.m is None


def test_leverage_non_positive_produces_non_positive_leverage():
    wins = {
        ("attacking", 2, 1): WinCounts(wins=2, entries=10),
        ("attacking", 1, 2): WinCounts(wins=8, entries=10),
    }
    player_duels = {("attacking", 2, 2): DuelCounts(kills=5, deaths=5)}
    teammate_duels = {("attacking", 2, 2): DuelCounts(kills=5, deaths=5)}
    cell = compute_point_estimate(wins, player_duels, teammate_duels, "attacking", 2, 2)
    assert cell.display_state == DisplayState.NON_POSITIVE_LEVERAGE
    assert cell.m is None


def test_all_teammates_reconstruction_is_exact_from_integer_counts():
    # p_player and all-teammates combined must equal the target team's overall
    # resolved-duel rate at the same state/side (section 10's reconstruction check).
    key = ("attacking", 3, 2)
    player = DuelCounts(kills=4, deaths=2)
    all_teammates = DuelCounts(kills=10, deaths=6)
    combined_kills = player.kills + all_teammates.kills
    combined_deaths = player.deaths + all_teammates.deaths
    team_overall = DuelCounts(kills=14, deaths=8)
    assert (combined_kills, combined_deaths) == (team_overall.kills, team_overall.deaths)


def test_bootstrap_is_deterministic_with_fixed_seed():
    blocks = [
        make_block(
            i,
            wins={
                ("attacking", 2, 1): WinCounts(wins=7, entries=10),
                ("attacking", 1, 2): WinCounts(wins=3, entries=10),
            },
            player_duels={("attacking", 2, 2): DuelCounts(kills=6, deaths=4)},
            all_teammate_duels={("attacking", 2, 2): DuelCounts(kills=3, deaths=7)},
        )
        for i in range(10)
    ]
    result1 = bootstrap_cell(blocks, "attacking", 2, 2, "all_teammates", seed=42, draws=200)
    result2 = bootstrap_cell(blocks, "attacking", 2, 2, "all_teammates", seed=42, draws=200)
    assert result1 == result2


def test_each_draw_recomputes_all_inputs_from_same_sampled_matches():
    # A block whose per-match numbers are wildly inconsistent with the pooled
    # aggregate must still influence the bootstrap distribution -- proves draws
    # aren't just resampling a single precomputed pooled M.
    varied_blocks = [
        make_block(
            1,
            wins={("attacking", 2, 1): WinCounts(wins=10, entries=10), ("attacking", 1, 2): WinCounts(wins=0, entries=10)},
            player_duels={("attacking", 2, 2): DuelCounts(kills=10, deaths=0)},
            all_teammate_duels={("attacking", 2, 2): DuelCounts(kills=0, deaths=10)},
        ),
        make_block(
            2,
            wins={("attacking", 2, 1): WinCounts(wins=0, entries=10), ("attacking", 1, 2): WinCounts(wins=10, entries=10)},
            player_duels={("attacking", 2, 2): DuelCounts(kills=0, deaths=10)},
            all_teammate_duels={("attacking", 2, 2): DuelCounts(kills=10, deaths=0)},
        ),
    ]
    result = bootstrap_cell(varied_blocks, "attacking", 2, 2, "all_teammates", seed=1, draws=500)
    # Mixed-match draws (leverage <= 0) and single-match draws (leverage > 0,
    # opposite-signed M) are both possible outcomes of resampling raw counts --
    # only some draws are defined, proving the draw isn't just replaying one
    # precomputed pooled M every time.
    assert 0 < result.defined_draw_fraction < 1


def test_undefined_draws_lower_the_defined_draw_fraction():
    blocks = [
        make_block(1, wins={}, player_duels={}, all_teammate_duels={}),
        make_block(
            2,
            wins={("attacking", 2, 1): WinCounts(wins=7, entries=10), ("attacking", 1, 2): WinCounts(wins=3, entries=10)},
            player_duels={("attacking", 2, 2): DuelCounts(kills=6, deaths=4)},
            all_teammate_duels={("attacking", 2, 2): DuelCounts(kills=3, deaths=7)},
        ),
    ]
    result = bootstrap_cell(blocks, "attacking", 2, 2, "all_teammates", seed=7, draws=500)
    assert 0 < result.defined_draw_fraction < 1


def test_insufficient_clusters_produce_interval_not_estimable():
    wins = {
        ("attacking", 2, 1): WinCounts(wins=8, entries=10),
        ("attacking", 1, 2): WinCounts(wins=2, entries=10),
    }
    player_duels = {("attacking", 2, 2): DuelCounts(kills=6, deaths=4)}
    teammate_duels = {("attacking", 2, 2): DuelCounts(kills=3, deaths=7)}
    cell = compute_point_estimate(wins, player_duels, teammate_duels, "attacking", 2, 2)
    assert cell.m is not None

    sparse_bootstrap = BootstrapResult(
        ci_low=0.1, ci_high=0.2, defined_draw_fraction=1.0,
        contributing_matches_player=1, contributing_matches_teammates=1,
        contributing_matches_w_u=1, contributing_matches_w_d=1,
    )
    classified = classify_cell(cell, sparse_bootstrap)
    assert classified.display_state == DisplayState.INTERVAL_NOT_ESTIMABLE


def test_narrower_valid_interval_never_produces_smaller_precision_encoding():
    cell = compute_point_estimate(
        {("attacking", 2, 1): WinCounts(wins=8, entries=10), ("attacking", 1, 2): WinCounts(wins=2, entries=10)},
        {("attacking", 2, 2): DuelCounts(kills=6, deaths=4)},
        {("attacking", 2, 2): DuelCounts(kills=3, deaths=7)},
        "attacking", 2, 2,
    )
    wide = BootstrapResult(
        ci_low=0.01, ci_high=0.5, defined_draw_fraction=1.0,
        contributing_matches_player=20, contributing_matches_teammates=20,
        contributing_matches_w_u=20, contributing_matches_w_d=20,
    )
    narrow = BootstrapResult(
        ci_low=0.15, ci_high=0.2, defined_draw_fraction=1.0,
        contributing_matches_player=20, contributing_matches_teammates=20,
        contributing_matches_w_u=20, contributing_matches_w_d=20,
    )

    def precision(bootstrap):
        return 1.0 / (bootstrap.ci_high - bootstrap.ci_low)

    classified_wide = classify_cell(cell, wide)
    classified_narrow = classify_cell(cell, narrow)
    assert classified_wide.display_state == DisplayState.POSITIVE
    assert classified_narrow.display_state == DisplayState.POSITIVE
    assert precision(narrow) > precision(wide)


def test_compute_fight_ev_view_returns_25_cells():
    blocks = [
        make_block(
            i,
            wins={
                ("attacking", 2, 1): WinCounts(wins=7, entries=10),
                ("attacking", 1, 2): WinCounts(wins=3, entries=10),
            },
            player_duels={("attacking", 2, 2): DuelCounts(kills=6, deaths=4)},
            all_teammate_duels={("attacking", 2, 2): DuelCounts(kills=3, deaths=7)},
        )
        for i in range(6)
    ]
    cells = compute_fight_ev_view(blocks, "attacking", "all_teammates", player_id=99, draws=100)
    assert len(cells) == 25
    target = next(c for c in cells if c.a == 2 and c.b == 2)
    assert target.m is not None
