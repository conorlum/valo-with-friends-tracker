"""The pure linker on synthetic data: every linking case in the plan's test list."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import AGENT_CODES, AGENT_NAMES, MATCH_UUID, SyntheticMatch, subject, team_of  # noqa: E402

from app.replays import link as lk  # noqa: E402
from app.replays.condense import condense_export_dir  # noqa: E402

# A complete competitive match (P-e): 15 rounds, 13-2 to team-1 (Red). Rounds 5-15 repeat the
# first four rounds' kill scripts; the provisional minimum is 20 anchors.
BASE_KILLS = {
    1: [(10.0, 0, 5), (12.0, 1, 6), (15.0, 2, 7), (20.0, 3, 8), (25.0, 4, 9)],
    2: [(8.0, 5, 0), (9.0, 6, 1), (18.0, 0, 5), (22.0, 7, 2), (30.0, 8, 3), (31.0, 9, 4)],
    3: [(5.0, 2, 9), (6.0, 9, 2), (6.05, 8, 3), (20.0, 4, 8), (21.0, 0, 7), (22.0, 1, 6), (23.0, 3, 5)],
    4: [(4.0, 5, 0), (7.0, 6, 1), (9.0, 7, 2), (11.0, 8, 3), (13.0, 9, 4), (14.0, 4, 9)],
}
ROUNDS = 15
LINK_KILLS = {n: list(BASE_KILLS[(n - 1) % 4 + 1]) for n in range(1, ROUNDS + 1)}
WINNERS = {n: "Blue" if n in (2, 4) else "Red" for n in range(1, ROUNDS + 1)}
OFFSET = 0.4  # DB clock = replay clock + 0.4 s


@pytest.fixture(scope="module")
def condensed(tmp_path_factory):
    match = SyntheticMatch(rounds=ROUNDS, kills=copy.deepcopy(LINK_KILLS), winners=dict(WINNERS))
    directory = match.write(tmp_path_factory.mktemp("link") / "export")
    return condense_export_dir(directory, source_sha256=match.source_sha256)


def view(condensed, **changes) -> lk.ReplayLinkView:
    replay = lk.ReplayLinkView(condensed.match_uuid, condensed.round_count,
                               condensed.report["dropped_final_round"], copy.deepcopy(condensed.players),
                               copy.deepcopy(condensed.link_inputs))
    for key, value in changes.items():
        setattr(replay, key, value)
    return replay


def mp(slot):
    return 500 + slot


def outcome(n: int, winners=None) -> str:
    return f"Team {'A' if (winners or WINNERS)[n] == 'Red' else 'B'} Elimination Win"


def db_match(kills=None, agents=None, extra_kills=(), rounds=None, score=None, subjects=None) -> lk.DbMatch:
    agents = agents or AGENT_NAMES
    players = [lk.DbPlayer(mp(s), 900 + s, agents[s], "team-1" if team_of(s) == 0 else "team-2",
                           (subjects or {}).get(s)) for s in range(10)]
    kill_rows, next_id = [], 1
    for n, script in (kills or LINK_KILLS).items():
        for t, killer, victim in script:
            kill_rows.append(lk.DbKill(next_id, n, round(t + OFFSET, 3), mp(killer), mp(victim)))
            next_id += 1
    kill_rows.extend(extra_kills)
    rounds = rounds or [lk.DbRound(n, outcome(n)) for n in sorted(WINNERS)]
    team1 = sum(1 for r in rounds if lk.outcome_winner(r.outcome) == "team-1" and not lk.is_surrender_round(r.outcome))
    team2 = sum(1 for r in rounds if lk.outcome_winner(r.outcome) == "team-2" and not lk.is_surrender_round(r.outcome))
    t1, t2 = score or (team1, team2)
    return lk.DbMatch(42, MATCH_UUID.upper(), t1, t2, rounds, players, kill_rows)


def test_happy_path(condensed):
    result = lk.link(view(condensed), [db_match()], {})
    assert result.status == "linked", result.report
    assert result.match_id == 42
    assert result.clock_offset == pytest.approx(OFFSET)
    assert result.slot_to_match_player == {s: mp(s) for s in range(10)}
    assert result.side_to_team == {"A": "team-1", "B": "team-2"}
    assert result.kill_map["1"] == [1, 2, 3, 4, 5]
    assert result.db_deaths == {}
    assert result.report["surrender"] is False and result.report["anchors"] == sum(map(len, LINK_KILLS.values()))
    assert sorted(result.backfills) == [(900 + s, subject(s)) for s in range(10)]
    assert "subject" not in str(result.report).lower() and "00000000-0000-4000-8000-0000000000" not in str(result.report)


def test_no_db_match_stays_unlinked_and_links_later_after_a_crawl(condensed):
    replay = view(condensed)
    assert lk.link(replay, [], {}).status == "unlinked"
    assert lk.link(replay, [db_match()], {}).status == "linked"


def test_two_match_rows_refuse(condensed):
    assert lk.link(view(condensed), [db_match(), db_match()], {}).status == "refused"


def test_a_different_match_uuid_never_links(condensed):
    other = db_match()
    other.external_id = "00000000-0000-4000-8000-000000000fff"
    assert lk.link(view(condensed), [other], {}).status == "unlinked"


# ------------------------------------------------------------ rounds


def test_a_shifted_round_set_refuses(condensed):
    rounds = [lk.DbRound(n + 1, r.outcome) for n, r in enumerate(db_match().rounds, 0)]
    result = lk.link(view(condensed), [db_match(rounds=[lk.DbRound(r.number + 1, r.outcome) for r in rounds])], {})
    assert (result.status, result.report["check"]) == ("refused", "rounds")


def test_a_winner_mismatch_refuses(condensed):
    # Rounds 2 and 3 swap winners in the DB: still 13-2, so only the per-round check catches it.
    rounds = db_match().rounds
    rounds[1] = lk.DbRound(2, "Team A Elimination Win")
    rounds[2] = lk.DbRound(3, "Team B Elimination Win")
    result = lk.link(view(condensed), [db_match(rounds=rounds)], {})
    assert result.report["check"] == "round_results"


def test_a_score_mismatch_refuses(condensed):
    assert lk.link(view(condensed), [db_match(score=(13, 3))], {}).report["check"] == "score"


def test_a_round_results_fallback_refuses_the_link(condensed):
    replay = view(condensed)
    replay.link_inputs["round_results_fallback"] = True
    assert lk.link(replay, [db_match()], {}).report["check"] == "round_results"


def test_a_missing_decoded_winner_refuses(condensed):
    replay = view(condensed)
    del replay.link_inputs["round_results"]["3"]
    assert lk.link(replay, [db_match()], {}).report["check"] == "round_results"


def test_a_dropped_final_round_needs_a_detected_surrender(condensed):
    # A complete 13-2 match has no room for a dropped final round.
    result = lk.link(view(condensed, dropped_final_round=True), [db_match()], {})
    assert (result.status, result.report["check"]) == ("refused", "rounds")


def _surrender_rows(played: int, winners: dict) -> list:
    return [lk.DbRound(n, outcome(n, winners)) for n in range(1, played + 1)]


def _truncated(replay, played: int, dropped: bool) -> lk.ReplayLinkView:
    out = lk.ReplayLinkView(replay.match_uuid, played, dropped, copy.deepcopy(replay.players),
                            copy.deepcopy(replay.link_inputs))
    out.link_inputs["kills"] = {k: v for k, v in out.link_inputs["kills"].items() if int(k) <= played}
    return out


def test_an_adapter_shaped_surrender_links(swiftplay):
    # 13 rounds played, rows 10-3 (no legal end); tracker.gg pads roundsWon to 13-3 and the
    # adapter drops its padding rows. With or without a dropped final replay round, it links.
    played = 13
    winners = {n: ("Blue" if n in (2, 4, 13) else "Red") for n in range(1, played + 1)}
    kills = {n: s for n, s in LINK_KILLS.items() if n <= played}
    for dropped in (False, True):
        result = lk.link(_truncated(swiftplay, played, dropped),
                         [db_match(kills=kills, rounds=_surrender_rows(played, winners), score=(13, 3))], {})
        assert result.status == "linked", result.report
        assert result.report["surrender"] is True


def test_a_padded_score_the_rows_contradict_refuses(swiftplay):
    # The pass-5 review probe: a truncated replay against as many rows of a 13-11 match.
    played = 4
    replay = _truncated(swiftplay, played, False)
    kills = {n: s for n, s in LINK_KILLS.items() if n <= played}
    rows = _surrender_rows(played, WINNERS)
    result = lk.link(replay, [db_match(kills=kills, rounds=rows, score=(13, 11))], {})
    assert (result.status, result.report["check"]) == ("refused", "completeness")
    # A tied award is never a surrender either.
    result = lk.link(replay, [db_match(kills=kills, rounds=rows, score=(2, 2))], {})
    assert (result.status, result.report["check"]) == ("refused", "completeness")


def test_an_incomplete_db_round_set_with_an_unchanged_score_refuses(condensed):
    rows = db_match().rounds[:-3]
    result = lk.link(view(condensed), [db_match(rounds=rows, score=(13, 2))], {})
    assert (result.status, result.report["check"]) == ("refused", "rounds")


def test_overtime_is_a_legal_end():
    assert lk.is_legal_end("competitive", 14, 12) and lk.is_legal_end("competitive", 16, 14)
    assert not lk.is_legal_end("competitive", 14, 11) and not lk.is_legal_end("competitive", 15, 12)
    assert lk.is_legal_end("competitive", 13, 11) and not lk.is_legal_end("competitive", 13, 12)


def test_a_short_mode_has_its_own_legal_end():
    assert lk.is_legal_end("swiftplay", 5, 4) and not lk.is_legal_end("swiftplay", 6, 4)
    with pytest.raises(lk.Refused):
        lk.is_legal_end("deathmatch", 40, 30)


# ------------------------------------------------------------ teams and players


def test_an_agent_change_refuses(condensed):
    agents = list(AGENT_NAMES)
    agents[2] = "Viper"
    result = lk.link(view(condensed), [db_match(agents=agents)], {})
    assert result.report["check"] == "assignment" and result.report["candidates"] == 0


def test_a_duplicate_agent_across_teams_still_links(condensed):
    replay = view(condensed)
    agents = list(AGENT_NAMES)
    agents[7] = agents[0]  # Jett on both teams
    for p in replay.players:
        p["agent"] = agents[p["slot"]]
    assert lk.link(replay, [db_match(agents=agents)], {}).status == "linked"


def test_a_fully_mirrored_composition_is_told_apart_by_the_kills(condensed):
    replay = view(condensed)
    mirrored = AGENT_NAMES[:5] * 2
    for p in replay.players:
        p["agent"] = mirrored[p["slot"]]
    match = db_match(agents=mirrored)
    # Cross-team kills alone leave several assignments (teams come from the DB, pass 6)...
    assert len(lk.assignment_candidates(replay, match)) > 1
    # ...and the ordered kill sequences leave exactly one.
    result = lk.link(replay, [match], {})
    assert result.status == "linked" and result.slot_to_match_player == {s: mp(s) for s in range(10)}


def _mutual_kill_match(tmp_path_factory):
    """Mirrored agents and a really symmetric trace, built as input: in every round, slot i and
    slot i + 5 (the same agent, opposite teams) kill each other at the same instant."""
    kills = {n: [k for i in range(5) for k in ((10.0 + 3 * i, i, i + 5), (10.0 + 3 * i, i + 5, i))]
             for n in range(1, ROUNDS + 1)}
    match = SyntheticMatch(rounds=ROUNDS, kills=kills, winners=dict(WINNERS),
                           agent_codes=[*AGENT_CODES[:5], *AGENT_CODES[:5]])
    directory = match.write(tmp_path_factory.mktemp("mutual") / "export")
    return condense_export_dir(directory, source_sha256=match.source_sha256), kills


def test_a_really_symmetric_kill_trace_refuses(tmp_path_factory):
    replay, kills = _mutual_kill_match(tmp_path_factory)
    match = db_match(kills=kills, agents=AGENT_NAMES[:5] * 2)
    result = lk.link(view(replay), [match], {})
    assert (result.status, result.report["check"]) == ("refused", "assignment")
    assert result.report["candidates"] > 1 and result.report["unpinned"]


def test_two_kill_free_same_agent_opposing_slots_with_crossed_spawn_evidence_refuse(condensed):
    # The pass-5 review probe: slots 2 and 7 play the same agent on opposite teams, are in no
    # kill, and their spawn evidence is crossed. Pass 5 linked the wrong assignment.
    replay = view(condensed)
    agents = list(AGENT_NAMES)
    agents[7] = agents[2]
    for p in replay.players:
        p["agent"] = agents[p["slot"]]
    kills = {n: [k for k in script if 2 not in k[1:] and 7 not in k[1:]] for n, script in LINK_KILLS.items()}
    replay.link_inputs["kills"] = {n: [k for k in ks if 2 not in k[1:] and 7 not in k[1:]]
                                   for n, ks in replay.link_inputs["kills"].items()}
    spawns = replay.link_inputs["spawn_points"]
    spawns["2"], spawns["7"] = spawns["7"], spawns["2"]
    for positions in replay.link_inputs["start_positions"].values():
        positions["2"], positions["7"] = positions["7"], positions["2"]
    result = lk.link(replay, [db_match(kills=kills, agents=agents)], {})
    assert (result.status, result.report["check"], result.report["candidates"]) == ("refused", "assignment", 2)
    assert result.report["unpinned"] == [{"slot": 2, "agent": agents[2]}, {"slot": 7, "agent": agents[2]}]


def test_side_groups_that_disagree_with_the_teams_refuse(condensed):
    replay = view(condensed)
    replay.players[5]["side_group"] = "A"
    result = lk.link(replay, [db_match()], {})
    assert (result.report["check"], result.report["reason"]) == \
        ("proximity", "the condenser's side groups disagree with the teams")


def test_an_unresolved_partition_still_links_on_kills(condensed):
    replay = view(condensed)
    for p in replay.players:
        p["side_group"] = None
    result = lk.link(replay, [db_match()], {})
    assert result.status == "linked", result.report
    assert result.side_to_team == {} and result.slot_to_match_player == {s: mp(s) for s in range(10)}


def test_a_same_team_replay_kill_leaves_no_candidate(condensed):
    replay = view(condensed)
    replay.link_inputs["kills"]["1"].append([30.0, 0, 1])
    result = lk.link(replay, [db_match()], {})
    assert (result.report["check"], result.report["candidates"]) == ("assignment", 0)


def test_misleading_tight_clusters_refuse_and_never_exclude_a_candidate(condensed):
    replay = view(condensed)
    positions = replay.link_inputs["start_positions"]["2"]
    positions["0"], positions["5"] = positions["5"], positions["0"]
    assert {s: mp(s) for s in range(10)} in lk.assignment_candidates(replay, db_match())
    result = lk.link(replay, [db_match()], {})
    assert (result.report["check"], result.report["evidence"], result.report["round"]) == \
        ("proximity", "round_start_cluster", 2)


def test_a_slots_spawn_point_moved_to_the_other_team_refuses(condensed):
    replay = view(condensed)
    spawns = replay.link_inputs["spawn_points"]
    spawns["0"] = list(spawns["5"])
    result = lk.link(replay, [db_match()], {})
    assert (result.report["check"], result.report["evidence"]) == ("proximity", "match_start_spawns")


def test_spawn_points_are_in_world_units(condensed):
    spawns = condensed.link_inputs["spawn_points"]
    assert sorted(spawns, key=int) == [str(s) for s in range(10)]
    # Team 0 spawns around world (-2000, 500) in the synthetic match: world units, not 0..10000 u/v.
    assert all(abs(spawns[str(s)][0] + 2000) <= 300.5 for s in range(5))


def test_same_team_duplicate_agents_still_link(condensed):
    replay = view(condensed)
    agents = list(AGENT_NAMES)
    agents[1] = agents[0]  # two Jetts on team 1, as a non-competitive mode allows
    for p in replay.players:
        p["agent"] = agents[p["slot"]]
    result = lk.link(replay, [db_match(agents=agents)], {})
    assert result.status == "linked", result.report
    assert result.slot_to_match_player == {s: mp(s) for s in range(10)}


def test_a_riot_subject_anchor_must_agree(condensed):
    good = db_match(subjects={3: subject(3)})
    assert lk.link(view(condensed), [good], {subject(3): 903}).status == "linked"


# ------------------------------------------------------------ kills


@pytest.mark.parametrize("extra", [
    lk.DbKill(900, 2, 35.0, None, mp(2)),        # no killer (spike/fall)
    lk.DbKill(901, 2, 35.0, mp(2), mp(2)),       # self-kill
    lk.DbKill(902, 1, 35.0, mp(0), mp(1)),       # teamkill
])
def test_each_excluded_db_kill_class_becomes_a_db_death(condensed, extra):
    result = lk.link(view(condensed), [db_match(extra_kills=[extra])], {})
    assert result.status == "linked", result.report
    [death] = result.db_deaths[str(extra.round_number)]
    assert death == {"slot": extra.victim - 500, "t_db": 35.0}


def test_an_unexpected_unmatched_db_death_refuses(condensed):
    result = lk.link(view(condensed), [db_match(extra_kills=[lk.DbKill(903, 1, 30.0, mp(0), mp(6))])], {})
    assert result.report["check"] == "assignment"


def test_two_swapped_kills_refuse(condensed):
    kills = copy.deepcopy(LINK_KILLS)
    kills[2][0], kills[2][1] = (8.0, 6, 1), (9.0, 5, 0)
    assert lk.link(view(condensed), [db_match(kills=kills)], {}).report["check"] == "assignment"


def test_kills_out_of_order_within_the_residual_window_pair(condensed):
    kills = copy.deepcopy(LINK_KILLS)
    # Round 3's 6.0 / 6.05 kills arrive in the other order in the DB.
    kills[3][1], kills[3][2] = (6.05, 9, 2), (6.0, 8, 3)
    assert lk.link(view(condensed), [db_match(kills=kills)], {}).status == "linked"


def test_real_jitter_across_pass_5s_tie_window_pairs(condensed):
    # 0.05 s apart in the replay, 0.11 s apart and reversed in the DB: pass 5's separate 0.1 s
    # tie groups refused this; pairing inside the residual window accepts it.
    kills = copy.deepcopy(LINK_KILLS)
    kills[3][1], kills[3][2] = (6.11, 9, 2), (6.0, 8, 3)
    result = lk.link(view(condensed), [db_match(kills=kills)], {})
    assert result.status == "linked", result.report


def test_kills_out_of_order_beyond_the_residual_window_refuse(condensed):
    kills = copy.deepcopy(LINK_KILLS)
    # Round 1's 10 s and 12 s kills reversed in the DB, 2 s apart: more than MAX_RESIDUAL_S.
    kills[1][0], kills[1][1] = (12.0, 0, 5), (10.0, 1, 6)
    result = lk.link(view(condensed), [db_match(kills=kills)], {})
    assert (result.status, result.report["check"]) == ("refused", "assignment")


def test_match_kills_pairs_within_the_window_directly():
    mapping = {s: s for s in range(10)}
    ours = [[1.0, 0, 5], [1.5, 1, 6]]
    theirs = [lk.DbKill(1, 1, 1.4, 1, 6), lk.DbKill(2, 1, 1.45, 0, 5)]
    assert [d.id for _, d in lk.match_kills(ours, theirs, mapping)] == [2, 1]
    theirs = [lk.DbKill(1, 1, 0.5, 1, 6), lk.DbKill(2, 1, 1.45, 0, 5)]
    assert lk.match_kills(ours, theirs, mapping) is None, "0.95 s apart in the DB: beyond the window"
    assert lk.match_kills(ours, theirs[:1], mapping) is None, "counts differ"


# ------------------------------------------------------------ clock


def _shifted(round_shift=None, kill_shift=None):
    kills = copy.deepcopy(LINK_KILLS)
    for n, script in kills.items():
        for i, (t, a, b) in enumerate(script):
            if round_shift is not None:
                t += round_shift
            if kill_shift is not None and (n, i) == kill_shift[0]:
                t += kill_shift[1]
            script[i] = (t, a, b)
    return kills


def test_a_3_second_clock_shift_refuses(condensed):
    result = lk.link(view(condensed), [db_match(kills=_shifted(round_shift=3.0))], {})
    assert (result.report["check"], result.report["reason"]) == ("clock", "clock offset over the limit")


def test_one_large_residual_refuses(condensed):
    result = lk.link(view(condensed), [db_match(kills=_shifted(kill_shift=((4, 5), 1.0)))], {})
    assert result.report["check"] == "clock"
    assert result.report["reason"] == "a residual is over the limit"


def test_too_few_anchors_refuse(condensed, monkeypatch):
    total = sum(map(len, LINK_KILLS.values()))
    monkeypatch.setattr(lk, "MIN_ANCHORS", total + 1)
    result = lk.link(view(condensed), [db_match()], {})
    assert (result.report["check"], result.report["anchors"]) == ("clock", total)


def test_a_round_with_no_anchor_refuses(condensed, monkeypatch):
    monkeypatch.setattr(lk, "MIN_ANCHORS", 1)
    replay = view(condensed)
    replay.link_inputs["kills"]["4"] = []
    kills = copy.deepcopy(LINK_KILLS)
    kills[4] = []
    result = lk.link(replay, [db_match(kills=kills)], {})
    assert (result.report["check"], result.report["rounds"]) == ("clock", [4])


# ------------------------------------------------------------ eligibility (P-f)


def test_an_ineligible_replay_refuses_before_anything_else(condensed):
    replay = view(condensed)
    replay.link_inputs["eligibility"]["eligible"] = False
    replay.link_inputs["eligibility"]["reasons"] = ["coverage below 90% for slots ['3']"]
    result = lk.link(replay, [db_match()], {})
    assert (result.status, result.report["check"]) == ("refused", "eligibility")
    assert result.report["reasons"] == ["coverage below 90% for slots ['3']"]


def test_a_replay_with_no_eligibility_record_refuses(condensed):
    replay = view(condensed)
    del replay.link_inputs["eligibility"]
    assert lk.link(replay, [db_match()], {}).report["check"] == "eligibility"


# ------------------------------------------------------------ backfill


def test_a_player_with_a_different_subject_refuses_with_no_backfills(condensed):
    result = lk.link(view(condensed), [db_match(subjects={4: "00000000-0000-4000-8000-00000000ffff"})], {})
    assert result.status == "refused" and result.backfills == []


def test_a_subject_owned_by_another_player_refuses_with_no_backfills(condensed):
    result = lk.link(view(condensed), [db_match()], {subject(4): 12345})
    assert (result.status, result.report["check"]) == ("refused", "backfill")
    assert result.backfills == []


def test_a_subject_already_on_the_same_player_is_not_backfilled_again(condensed):
    result = lk.link(view(condensed), [db_match(subjects={4: subject(4)})], {subject(4): 904})
    assert result.status == "linked"
    assert (904, subject(4)) not in result.backfills and len(result.backfills) == 9


# ------------------------------------------------------------ the first real export's shape (Swiftplay)


@pytest.fixture(scope="module")
def swiftplay(tmp_path_factory):
    """No Subjects and no decoded winners: identity and rounds rest on agents, sides and kills."""
    match = SyntheticMatch(rounds=ROUNDS, kills=copy.deepcopy(LINK_KILLS), shape="swiftplay")
    root = tmp_path_factory.mktemp("swiftplay")
    directory = match.write(root / "export")
    return condense_export_dir(directory, source_sha256=match.source_sha256,
                               vrf_path=match.write_vrf(root / f"{MATCH_UUID}.vrf"))


def test_a_replay_without_subjects_or_winners_links_on_kills(swiftplay):
    assert all(p["subject"] is None for p in swiftplay.players)
    assert swiftplay.link_inputs["round_results"] == {}
    result = lk.link(view(swiftplay), [db_match()], {})
    assert result.status == "linked", result.report
    assert result.slot_to_match_player == {s: mp(s) for s in range(10)}
    assert result.backfills == []
    assert result.report["winners_checked"] is False


def test_without_subjects_an_anchored_db_player_does_not_block_the_link(swiftplay):
    result = lk.link(view(swiftplay), [db_match(subjects={4: subject(4)})], {subject(4): 904})
    assert result.status == "linked", result.report


def test_without_winners_a_changed_kill_still_refuses(swiftplay):
    kills = copy.deepcopy(LINK_KILLS)
    kills[2][0] = (8.0, 6, 0)
    assert lk.link(view(swiftplay), [db_match(kills=kills)], {}).report["check"] == "assignment"


def test_a_replay_self_kill_pairs_with_nothing(swiftplay):
    # Clove's ult running out: the replay reports her killing herself; the DB excludes self-kills.
    replay = view(swiftplay)
    replay.link_inputs["kills"]["2"].append([35.0, 3, 3])
    result = lk.link(replay, [db_match(extra_kills=[lk.DbKill(99, 2, 35.0 + OFFSET, mp(3), mp(3))])], {})
    assert result.status == "linked", result.report
    assert result.kill_map["2"][-1] is None


def test_loose_round_start_positions_do_not_refuse_a_correct_link(tmp_path):
    # As in the first real export: one side roams through the buy phase, so its round-start
    # positions spread far wider than the cluster radius. Those rounds are no side evidence.
    match = SyntheticMatch(rounds=ROUNDS, kills=copy.deepcopy(LINK_KILLS), shape="swiftplay")
    movement = match.movement()
    for row in movement:
        slot = row["shooter_character_net_guid"] - 1000
        if slot >= 5 and slot % 2:
            row["position"]["x"] -= 7000.0
    directory = match.write(tmp_path / "export", movement=movement)
    replay = condense_export_dir(directory, source_sha256=match.source_sha256,
                                 vrf_path=match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf"))
    assert replay.link_inputs["start_positions"] == {}
    result = lk.link(view(replay), [db_match()], {})
    assert result.status == "linked", result.report
