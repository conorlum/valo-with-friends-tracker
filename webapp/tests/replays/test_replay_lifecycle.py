"""P-c: channel lifecycle. Dormant or reopened channels are unobserved, not departed; a
player left only on a final, non-dormant close; contradictions refuse."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import AGENT_CODES, DEFAULT_KILLS, MATCH_UUID, ROUND_MS, SyntheticMatch  # noqa: E402

from app.replays import condense as cd  # noqa: E402
from app.replays import format as fmt  # noqa: E402
from app.replays.contract import ContractError  # noqa: E402


def close(match, slot, t, reason="destroyed"):
    row = {"type": "actor_closed", "time_ms": t, "actor_net_guid": match.pawn(1, slot), "channel": 10 + slot}
    if reason is not None:
        row["reason"] = reason
    return row


def reopen(match, slot, t):
    return {"type": "actor_spawned", "time_ms": t, "actor_net_guid": match.pawn(1, slot), "channel": 10 + slot,
            "is_dynamic": True, "actor_path": None, "archetype_path": f"Default__{AGENT_CODES[slot]}_PC_C"}


def run(tmp_path, match, movement=None):
    directory = match.write(tmp_path / "export", sorted(match.events(), key=lambda r: r["time_ms"]), movement)
    return cd.condense_export_dir(directory, source_sha256=match.source_sha256,
                                  vrf_path=match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf"))


def refused(tmp_path, match) -> ContractError:
    with pytest.raises(ContractError) as caught:
        run(tmp_path, match)
    return caught.value


def without_movement(match, slot, lo, hi):
    return [r for r in match.movement() if not (r["shooter_character_net_guid"] == match.pawn(1, slot)
                                                 and lo < r["time_ms"] < hi)]


def test_a_dormant_close_and_reopen_stays_alive_and_unobserved(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    match.extra_events += [close(match, 0, start + 5000, "dormancy"), reopen(match, 0, start + 9000)]
    out = run(tmp_path, match, without_movement(match, 0, start + 5000, start + 9000))
    assert out.rounds[1]["alive"]["0"] == [[0.0, None, "round_end", ["unobserved"]]]
    assert len(out.rounds[1]["tracks"]["0"]) == 2
    # The 4 s unseen span doesn't count against coverage or the gap limit.
    assert out.report["coverage"]["0"]["max_gap_s"] <= cd.MAX_TRACK_GAP_S
    assert out.report["coverage"]["0"]["min"] >= cd.MIN_ALIVE_COVERAGE
    assert out.report["lifecycle"]["left"] == {} and out.report["lifecycle"]["unobserved_spans"] == 1
    assert out.rounds[2]["alive"]["0"][0][3:] == [], "only the round with the span is flagged"


def test_a_dormant_close_with_no_reopen_is_unobserved_to_the_end_not_left(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(3)
    match.extra_events.append(close(match, 0, start + 5000, "dormancy"))
    out = run(tmp_path, match, without_movement(match, 0, start + 5000, start + 999_999))
    assert out.report["lifecycle"]["left"] == {}
    assert out.rounds[3]["alive"]["0"] == [[0.0, None, "round_end", ["unobserved"]]]


def test_a_destroyed_close_with_no_reopen_is_left(tmp_path):
    kills = {k: list(v) for k, v in DEFAULT_KILLS.items()}
    kills[3] = [(20.0, 4, 8)]
    match = SyntheticMatch(shape="swiftplay", kills=kills, leaver=(9, SyntheticMatch().round_start(3) + 3000))
    out = run(tmp_path, match)
    assert out.report["lifecycle"]["closes_by_reason"] == {"destroyed": 1}
    assert list(out.report["lifecycle"]["left"]) == ["9"]
    assert out.rounds[3]["alive"]["9"] == [[0.0, 3.0, "left"]]


def test_a_close_after_the_final_round_was_decided_is_the_match_ending(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    match.extra_events.append(close(match, 0, match.round_start(3) + ROUND_MS + 2000))
    out = run(tmp_path, match)
    assert out.report["lifecycle"]["left"] == {}
    assert out.rounds[3]["alive"]["0"] == [[0.0, None, "round_end"]]


def test_a_close_with_no_reason_refuses(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    match.extra_events.append(close(match, 0, match.round_start(2) + 1000, reason=None))
    error = refused(tmp_path, match)
    assert error.reason == "lifecycle" and "no reason" in error.detail


def test_a_destroyed_close_then_a_reopen_is_unobserved_not_a_left(tmp_path):
    # "A reopen after a left" can't happen: the reopen makes the span unobserved.
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(2)
    match.extra_events += [close(match, 9, start + 20_000), reopen(match, 9, start + 22_000)]  # 9 survives round 2
    out = run(tmp_path, match, without_movement(match, 9, start + 20_000, start + 22_000))
    assert out.report["lifecycle"]["left"] == {}
    assert out.rounds[2]["alive"]["9"] == [[0.0, None, "round_end", ["unobserved"]]]


def test_a_kill_of_a_player_who_left_refuses(tmp_path):
    # Slot 9 leaves before round 3, where the default script still kills them at 5 s.
    match = SyntheticMatch(shape="swiftplay", leaver=(9, SyntheticMatch().round_start(3) - 5000))
    error = refused(tmp_path, match)
    assert error.reason == "lifecycle" and "after that player left" in error.detail


def test_a_kill_by_a_player_who_left_refuses(tmp_path):
    kills = {k: list(v) for k, v in DEFAULT_KILLS.items()}
    kills[3] = [(20.0, 9, 4)]
    match = SyntheticMatch(shape="swiftplay", kills=kills, leaver=(9, SyntheticMatch().round_start(3) + 3000))
    error = refused(tmp_path, match)
    assert error.reason == "lifecycle" and "a kill by slot 9" in error.detail


def test_a_second_death_in_one_round_with_no_revive_refuses(tmp_path):
    kills = {k: list(v) for k, v in DEFAULT_KILLS.items()}
    kills[1].append((30.0, 1, 5))  # slot 5 already died at 10 s
    error = refused(tmp_path, SyntheticMatch(shape="swiftplay", kills=kills))
    assert error.reason == "lifecycle" and "second death" in error.detail


def test_deaths_in_two_rounds_are_two_lives(tmp_path):
    out = run(tmp_path, SyntheticMatch(shape="swiftplay"))  # slot 5 dies in rounds 1 and 2
    assert out.rounds[1]["alive"]["5"] == [[0.0, 10.0, "kill"]]
    assert out.rounds[2]["alive"]["5"][0][2] == "kill"


def test_the_clove_shape_is_an_uncertain_life(tmp_path):
    # Killed at 10 s, then a self-kill at 25 s with no decoded revive: the revive happened
    # but wasn't seen. The life ends at the first death, flagged.
    kills = {k: list(v) for k, v in DEFAULT_KILLS.items()}
    kills[1].append((25.0, 5, 5))
    out = run(tmp_path, SyntheticMatch(shape="swiftplay", kills=kills))
    assert out.rounds[1]["alive"]["5"] == [[0.0, 10.0, "kill", ["uncertain"]]]
    assert out.report["lifecycle"]["uncertain_lives"] == 1


def test_a_self_kill_as_the_first_death_is_a_plain_kill(tmp_path):
    kills = {k: list(v) for k, v in DEFAULT_KILLS.items()}
    kills[1].append((35.0, 3, 3))
    out = run(tmp_path, SyntheticMatch(shape="swiftplay", kills=kills))
    assert out.rounds[1]["alive"]["3"] == [[0.0, 35.0, "kill"]]
    assert out.report["lifecycle"]["uncertain_lives"] == 0


def test_alive_flags_round_trip_through_the_blob_encoder(tmp_path):
    kills = {k: list(v) for k, v in DEFAULT_KILLS.items()}
    kills[1].append((25.0, 5, 5))
    out = run(tmp_path, SyntheticMatch(shape="swiftplay", kills=kills))
    assert fmt.decode_blob(out.encoded_rounds()[1])["alive"]["5"] == [[0.0, 10.0, "kill", ["uncertain"]]]


# ------------------------------------------------------------ a disconnect and reconnect
#
# The shape of 9b73ca26 (2026-09-28): a player's pawn closes `destroyed`, no reopen, and a new
# pawn for the same player state spawns later. They are away in between, not alive and unseen.

REJOIN_PAWN = 1109


def rejoin(match, slot, t):
    code = AGENT_CODES[slot]
    return [{"type": "actor_spawned", "time_ms": t, "actor_net_guid": REJOIN_PAWN, "channel": 70, "is_dynamic": True,
             "actor_path": None, "archetype_path": f"Default__{code}_PC_C",
             "location": dict(zip("xyz", (*match.spawn_position(1, slot), 100.0)))},
            {"type": "export_group_received", "time_ms": t + 5, "actor_net_guid": REJOIN_PAWN,
             "object_net_guid": REJOIN_PAWN, "channel": 70,
             "export_group_path": f"/Game/Characters/{code}/{code}_PC.{code}_PC_C",
             "payload": {"PlayerState": 200 + slot, "Controller": 300 + slot, "IsPlayerCharacter": True}}]


def reconnect(match, slot, away_ms, back_ms):
    """Events for the drop and the rejoin, and movement with nothing between them and the new
    pawn's GUID after."""
    match.extra_events += [close(match, slot, away_ms), *rejoin(match, slot, back_ms)]
    movement = []
    for row in match.movement():
        if row["shooter_character_net_guid"] == match.pawn(1, slot):
            if away_ms < row["time_ms"] < back_ms:
                continue
            if row["time_ms"] >= back_ms:
                row = {**row, "actor_net_guid": REJOIN_PAWN, "object_net_guid": REJOIN_PAWN,
                       "shooter_character_net_guid": REJOIN_PAWN}
        movement.append(row)
    return movement


def without_slot(kills, slot, rounds):
    return {n: [k for k in v if n not in rounds or slot not in k[1:]] for n, v in kills.items()}


def test_a_player_away_for_a_whole_round_is_absent_from_it(tmp_path):
    # Slot 9 dies in round 1 at 25 s, drops after it, misses round 2 and is back for round 3's buy.
    match = SyntheticMatch(shape="swiftplay", kills=without_slot(DEFAULT_KILLS, 9, {2}))
    away_ms, back_ms = match.round_start(1) + 30_000, match.round_start(3) - 10_000
    out = run(tmp_path, match, reconnect(match, 9, away_ms, back_ms))
    assert out.report["lifecycle"]["away"] == {"9": [[away_ms / 1000, back_ms / 1000]]}
    assert out.report["lifecycle"]["left"] == {}
    assert out.report["players"]["absent_by_round"] == {"2": [9]}
    assert out.rounds[1]["alive"]["9"] == [[0.0, 25.0, "kill"]]
    assert out.rounds[2]["alive"]["9"] == [] and "9" not in out.rounds[2]["tracks"]
    assert out.rounds[3]["alive"]["9"] == [[0.0, 5.0, "kill"]] and out.rounds[3]["tracks"]["9"]
    assert out.link_inputs["eligibility"]["eligible"], out.link_inputs["eligibility"]["reasons"]


def test_a_drop_mid_round_ends_that_life_as_left(tmp_path):
    match = SyntheticMatch(shape="swiftplay", kills=without_slot(DEFAULT_KILLS, 9, {2}))
    away_ms, back_ms = match.round_start(2) + 5_000, match.round_start(3) - 10_000
    out = run(tmp_path, match, reconnect(match, 9, away_ms, back_ms))
    assert out.rounds[2]["alive"]["9"] == [[0.0, 5.0, "left"]]
    assert out.report["players"]["absent_by_round"] == {}
    assert out.link_inputs["eligibility"]["eligible"], out.link_inputs["eligibility"]["reasons"]


def test_a_rejoin_mid_round_opens_a_life_there(tmp_path):
    # Away from round 2's buy phase to 10 s into it; the default script has 9 kill at 31 s.
    match = SyntheticMatch(shape="swiftplay")
    away_ms, back_ms = match.round_start(2) - 20_000, match.round_start(2) + 10_000
    out = run(tmp_path, match, reconnect(match, 9, away_ms, back_ms))
    assert out.rounds[2]["alive"]["9"] == [[10.0, None, "round_end"]]
    assert out.report["players"]["absent_by_round"] == {"2": [9]}
    assert out.link_inputs["eligibility"]["eligible"], out.link_inputs["eligibility"]["reasons"]


def test_a_kill_by_a_disconnected_player_refuses(tmp_path):
    match = SyntheticMatch(shape="swiftplay")  # the default script has 9 kill 4 at 31 s in round 2
    away_ms, back_ms = match.round_start(2) + 5_000, match.round_start(3) - 10_000
    match.extra_events += [close(match, 9, away_ms), *rejoin(match, 9, back_ms)]
    error = refused(tmp_path, match)
    assert error.reason == "lifecycle" and "a kill by slot 9" in error.detail and "disconnected" in error.detail


# ------------------------------------------------------------ alive_intervals directly


def test_alive_intervals_end_a_life_at_a_disconnect():
    assert cd.alive_intervals(0, 100, [], [], [], away=[(40, 200)]) == [[0, 40, "left"]]


def test_alive_intervals_open_no_life_in_a_round_spent_away():
    assert cd.alive_intervals(0, 100, [], [], [], away=[(-50, 200)]) == []


def test_alive_intervals_open_a_life_at_the_rejoin():
    assert cd.alive_intervals(0, 100, [], [], [60], away=[(-50, 60)]) == [[60, None, "round_end"]]


def test_alive_intervals_drop_and_rejoin_in_one_round():
    assert cd.alive_intervals(0, 100, [], [], [70], away=[(30, 70)]) == [[0, 30, "left"], [70, None, "round_end"]]


def test_alive_intervals_a_drop_after_a_death_changes_nothing():
    assert cd.alive_intervals(0, 100, [20], [], [], away=[(40, 200)]) == [[0, 20, "kill"]]


def test_alive_intervals_flag_only_overlapping_spans():
    out = cd.alive_intervals(0, 100, [50], [70], [], None, (), [(80, 90)])
    assert out == [[0, 50, "kill"], [70, None, "round_end", ["unobserved"]]]


def test_alive_intervals_left_during_an_unobserved_span():
    out = cd.alive_intervals(0, 100, [], [], [], 60, (), [(40, 100)])
    assert out == [[0, 60, "left", ["unobserved"]]]
