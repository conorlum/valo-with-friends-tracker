"""P-a: phase-cycle validation, the ClientGamePhaseEnded cross-check, and playback windows."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import BUY_MS, MATCH_UUID, ROUND_MS, SyntheticMatch  # noqa: E402

from app.replays import condense as cd  # noqa: E402
from app.replays import format as fmt  # noqa: E402
from app.replays.contract import GAME_STATE_PATH, ContractError  # noqa: E402

PHASE_RPCS = ("ClientGamePhaseBegin", "ClientGamePhaseEnded")


def swift_phases(match: SyntheticMatch) -> list[tuple[int, int]]:
    """The synthetic Swiftplay match's phase sequence as (ms, phase)."""
    return [(row["time_ms"], row["payload"]["NewPhase"]) for row in match.events()
            if row.get("function_name") == "ClientGamePhaseBegin"]


def with_phases(match: SyntheticMatch, phases: list[tuple[int, int]], ended: bool = True) -> list[dict]:
    """The match's events with its phase RPCs replaced by `phases` (Ended rows paired like the real export)."""
    rows = [row for row in match.events() if row.get("function_name") not in PHASE_RPCS]
    running = 1
    for t, phase in phases:
        if ended:
            rows.append({"type": "rpc_received", "time_ms": t, "actor_net_guid": 2, "channel": 1,
                         "function_name": "ClientGamePhaseEnded", "payload": {"OldPhase": running}})
        rows.append({"type": "rpc_received", "time_ms": t, "actor_net_guid": 2, "channel": 1,
                     "function_name": "ClientGamePhaseBegin", "payload": {"NewPhase": phase}})
        running = phase
    return sorted(rows, key=lambda row: row["time_ms"])


def run(tmp_path, match=None, events=None, movement=None):
    match = match or SyntheticMatch(shape="swiftplay")
    directory = match.write(tmp_path / "export", events, movement)
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf")
    return cd.condense_export_dir(directory, source_sha256=match.source_sha256, vrf_path=vrf)


def refused(tmp_path, match, events) -> ContractError:
    with pytest.raises(ContractError) as caught:
        run(tmp_path, match, events)
    return caught.value


def drop(phases, t, phase):
    assert (t, phase) in phases
    return [p for p in phases if p != (t, phase)]


# ------------------------------------------------------------ the cycle


def test_the_synthetic_cycle_is_valid_and_cross_checked(tmp_path):
    out = run(tmp_path)
    assert out.round_count == 3
    assert out.report["phase_ended_checked"] is True
    assert out.report["kills_outside_rounds"] == 0


def test_a_legacy_export_numbers_rounds_from_a_0_based_round_number(tmp_path):
    match = SyntheticMatch()
    out = cd.condense_export_dir(match.write(tmp_path / "export"), source_sha256=match.source_sha256)
    assert out.report["round_number_at_start"] == [0, 1, 2] and cd.ROUND_NUMBER_BASE == 0
    assert out.report["phase_ended_checked"] is False, "no ClientGamePhaseEnded in the legacy shape"


def test_a_decoded_round_number_that_disagrees_with_the_window_order_refuses(tmp_path):
    match = SyntheticMatch()
    events = match.events()
    for row in events:
        payload = row.get("payload") or {}
        if row.get("export_group_path") == GAME_STATE_PATH and payload.get("Phase") == 4 \
                and row["time_ms"] == match.round_start(2):
            payload["RoundNumber"] = 5
    with pytest.raises(ContractError) as caught:
        cd.condense_export_dir(match.write(tmp_path / "export", events), source_sha256=match.source_sha256)
    assert caught.value.reason == "round_number"


def test_a_missing_final_round_ending_drops_that_round_and_excludes_its_kills(tmp_path):
    out = run(tmp_path, SyntheticMatch(shape="swiftplay", drop_final_round_end=True))
    assert out.round_count == 2 and out.report["dropped_final_round"] is True
    assert out.report["kills_excluded"] == {"dropped_final_round": 4}
    assert out.report["kills_outside_rounds"] == 0


def test_a_final_in_round_ended_by_a_match_end_phase_is_dropped(tmp_path):
    # A surrender's shape (finding 26 decides it): the last 4 meets another phase, not a 5.
    match = SyntheticMatch(shape="swiftplay")
    phases = swift_phases(match)
    end3 = match.round_start(3) + ROUND_MS
    phases = drop(phases, end3, 5) + [(end3, 9)]
    out = run(tmp_path, match, with_phases(match, sorted(phases)))
    assert out.round_count == 2 and out.report["dropped_final_round"] is True


def test_a_5_after_a_dropped_final_round_is_an_orphan(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    end3 = match.round_start(3) + ROUND_MS
    phases = drop(swift_phases(match), end3, 5) + [(end3 - 1000, 3), (end3, 5)]
    assert refused(tmp_path, match, with_phases(match, sorted(phases), ended=False)).reason == "phase_cycle"


def test_4_then_3_then_5_refuses(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start1 = match.round_start(1)
    phases = sorted(swift_phases(match) + [(start1 + 20_000, 3)])
    assert refused(tmp_path, match, with_phases(match, phases, ended=False)).reason == "phase_cycle"


def test_a_lost_round_ending_mid_match_refuses(tmp_path):
    # Round 1's 5 is lost: its 4 meets round 2's buy phase while a later 4 exists.
    match = SyntheticMatch(shape="swiftplay")
    phases = drop(swift_phases(match), match.round_start(1) + ROUND_MS, 5)
    assert refused(tmp_path, match, with_phases(match, phases, ended=False)).reason == "phase_cycle"


def test_a_lost_in_round_refuses_and_does_not_renumber(tmp_path):
    # Round 2's 4 is lost: its 5 is an orphan. Pass 5 would have merged or dropped the round.
    match = SyntheticMatch(shape="swiftplay")
    phases = drop(swift_phases(match), match.round_start(2), 4)
    error = refused(tmp_path, match, with_phases(match, phases, ended=False))
    assert error.reason == "phase_cycle" and "no open InRound" in error.detail


def test_an_orphan_round_ending_refuses(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    phases = sorted(swift_phases(match) + [(match.round_start(2) - BUY_MS + 500, 5)])  # inside a buy phase
    assert refused(tmp_path, match, with_phases(match, phases, ended=False)).reason == "phase_cycle"


def test_halftime_and_between_round_phases_keep_the_numbering(tmp_path):
    match = SyntheticMatch(shape="swiftplay", rounds=4)
    end2 = match.round_start(2) + ROUND_MS
    phases = sorted(swift_phases(match) + [(end2 + 7000, 6), (end2 + 7000 + 1, 2)])
    out = run(tmp_path, match, with_phases(match, phases))
    assert out.round_count == 4 and [out.rounds[n]["round"] for n in (1, 2, 3, 4)] == [1, 2, 3, 4]
    # Round 2's playback ends at the first phase after its 5: the side swap.
    assert out.rounds[2]["t_end"] == 47.0


def test_overtime_rounds_are_numbered_in_order(tmp_path):
    out = run(tmp_path, SyntheticMatch(shape="swiftplay", rounds=6))
    assert sorted(out.rounds) == [1, 2, 3, 4, 5, 6]


# ------------------------------------------------------------ ClientGamePhaseEnded


def test_an_ended_row_naming_the_wrong_phase_refuses(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    events = match.events()
    ended = [row for row in events if row.get("function_name") == "ClientGamePhaseEnded"]
    ended[3]["payload"]["OldPhase"] = 6
    assert refused(tmp_path, match, events).reason == "phase_ended"


def test_the_first_ended_row_closes_a_phase_from_before_the_recording(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    events = match.events()
    first = next(row for row in events if row.get("function_name") == "ClientGamePhaseEnded")
    first["payload"]["OldPhase"] = 0
    assert run(tmp_path, match, events).report["phase_ended_checked"] is True


def test_a_missing_ended_row_mid_match_refuses(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    events = match.events()
    ended = [i for i, row in enumerate(events) if row.get("function_name") == "ClientGamePhaseEnded"]
    del events[ended[4]]
    assert refused(tmp_path, match, events).reason == "phase_ended"


def test_the_last_begin_may_lack_its_ended_row(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    events = match.events()
    last = max(i for i, row in enumerate(events) if row.get("function_name") == "ClientGamePhaseEnded")
    del events[last]
    assert run(tmp_path, match, events).report["phase_ended_checked"] is True


def test_an_ended_row_away_from_its_begin_refuses(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    events = match.events()
    ended = [row for row in events if row.get("function_name") == "ClientGamePhaseEnded"]
    ended[2]["time_ms"] += 5
    assert refused(tmp_path, match, sorted(events, key=lambda row: row["time_ms"])).reason == "phase_ended"


def test_without_ended_rows_the_cross_check_is_reported_as_not_run(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    out = run(tmp_path, match, with_phases(match, swift_phases(match), ended=False))
    assert out.report["phase_ended_checked"] is False and out.round_count == 3


# ------------------------------------------------------------ kills and windows


def test_a_kill_outside_every_window_refuses(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    buy2 = match.round_start(2) - BUY_MS + 5000
    match.extra_events.append({"type": "rpc_received", "time_ms": buy2, "actor_net_guid": match.pawn(1, 0),
                               "channel": 10, "function_name": "MulticastNotifyKilledEnemy",
                               "payload": {"KillerCharacter": match.pawn(1, 0), "KilledCharacter": match.pawn(1, 5)}})
    assert refused(tmp_path, match, sorted(match.events(), key=lambda r: r["time_ms"])).reason == "kills_outside_rounds"


def test_a_kill_after_the_round_was_decided_is_kept(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    match.kills[1] = [k for k in match.kills[1] if k[2] != 9]
    match.kills[1].append((44.0, 4, 9))  # after RoundEnding (40 s), before the next buy (50 s)
    blob = run(tmp_path, match).rounds[1]
    assert blob["t_decided"] == 40.0 and blob["t_end"] == 50.0
    late = [k for k in blob["kills"] if k["t"] > blob["t_decided"]]
    assert [(k["t"], k["killer"], k["victim"]) for k in late] == [(44.0, 4, 9)]
    assert blob["alive"]["9"] == [[0.0, 44.0, "kill"]]


def test_the_last_round_plays_to_the_end_of_the_recording(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    end3 = match.round_start(3) + ROUND_MS
    match.extra_events.append({"type": "actor_spawned", "time_ms": end3 + 6000, "actor_net_guid": 7777,
                               "channel": 99, "archetype_path": "Default__Something_Else_C"})
    blob = run(tmp_path, match).rounds[3]
    assert blob["t_decided"] == 40.0 and blob["t_end"] == 46.0


def test_t_decided_round_trips_through_the_blob_encoder(tmp_path):
    out = run(tmp_path)
    for n, data in out.encoded_rounds().items():
        assert fmt.decode_blob(data)["t_decided"] == out.rounds[n]["t_decided"] == 40.0
