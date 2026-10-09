"""W-b: the streaming condenser gives byte-identical output to the in-memory one."""

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import DEFAULT_KILLS, MATCH_UUID, SyntheticMatch  # noqa: E402

from app.replays import condense as cd  # noqa: E402
from app.replays import contract  # noqa: E402

REAL_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "replay" / "swiftplay"


def both(directory: Path, **kwargs):
    streamed = cd.condense_export_dir(directory, streaming=True, **kwargs)
    in_memory = cd.condense_export_dir(directory, streaming=False, **kwargs)
    return streamed, in_memory


def assert_parity(streamed, in_memory):
    assert streamed.encoded_rounds() == in_memory.encoded_rounds(), "blobs must be byte-identical"
    assert streamed.players == in_memory.players
    assert json.dumps(streamed.link_inputs, sort_keys=True) == json.dumps(in_memory.link_inputs, sort_keys=True)
    assert json.dumps(streamed.report, sort_keys=True) == json.dumps(in_memory.report, sort_keys=True)
    assert (streamed.match_uuid, streamed.recipe, streamed.hz) == (in_memory.match_uuid, in_memory.recipe,
                                                                   in_memory.hz)


def noise(match: SyntheticMatch, n: int = 200) -> list[dict]:
    """Rows the condenser never reads (shots, utility paths, other RPCs), like most of a real export."""
    rng = random.Random(7)
    rows = []
    for i in range(n):
        t = rng.randrange(60_000, match.round_start(match.rounds) + 40_000)
        rows.append(rng.choice([
            {"type": "valorant_shot_received", "time_ms": t, "actor_net_guid": 1000, "shot": {"x": i}},
            {"type": "rpc_received", "time_ms": t, "actor_net_guid": 2, "channel": 1,
             "function_name": "ReplaysClientReceiveRemoteCharacterUpdatesSingleArrayNoAutonomous",
             "payload": {"blob": "x" * 40}},
            {"type": "export_group_received", "time_ms": t, "actor_net_guid": 77, "export_group_path": "/Game/X.X_C",
             "payload": {"Health": 100}},
        ]))
    return rows


@pytest.mark.parametrize("shape", ["legacy", "swiftplay"])
def test_parity_on_the_synthetic_shapes(tmp_path, shape):
    match = SyntheticMatch(shape=shape)
    match.extra_events += noise(match)
    events = sorted(match.events(), key=lambda row: row["time_ms"])
    directory = match.write(tmp_path / "export", events)
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf")
    assert_parity(*both(directory, source_sha256=match.source_sha256, vrf_path=vrf))


def test_parity_when_the_files_are_not_in_time_order(tmp_path):
    # The contract sorts by time with ties in file order; the streaming loader sorts its subset
    # and each slot's samples the same way.
    match = SyntheticMatch(shape="swiftplay", leaver=(9, SyntheticMatch().round_start(3) + 3000),
                           kills={**{k: list(v) for k, v in DEFAULT_KILLS.items() if k != 3}, 3: [(20.0, 4, 8)]})
    match.extra_events += noise(match)
    events = match.events()
    random.Random(1).shuffle(events)
    movement = match.movement()
    random.Random(2).shuffle(movement)
    directory = match.write(tmp_path / "export", events, movement)
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf")
    assert_parity(*both(directory, source_sha256=match.source_sha256, vrf_path=vrf))


def test_parity_with_equal_times_in_file_order(tmp_path):
    # Two movement rows of one pawn at the same time_ms: the later row in the file wins its grid point.
    match = SyntheticMatch(shape="swiftplay")
    movement = match.movement()
    twin = dict(movement[500], position={"x": 9999.0, "y": 0.0, "z": 0.0})
    movement.insert(501, twin)
    directory = match.write(tmp_path / "export", movement=movement)
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf")
    assert_parity(*both(directory, source_sha256=match.source_sha256, vrf_path=vrf))


def test_the_streaming_loader_keeps_only_what_the_condenser_reads(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    match.extra_events += noise(match, 500)
    directory = match.write(tmp_path / "export", sorted(match.events(), key=lambda row: row["time_ms"]))
    streamed = contract.load_export_streaming(directory)
    full = contract.load_export(directory)
    kept = [row for row in full.events if contract.keep_event(row.data)]
    assert [row.data for row in streamed.events] == [row.data for row in kept]
    assert len(streamed.events) < len(full.events) - 400
    assert streamed.end_ms == full.end_ms
    assert [row.data for row in streamed.movement] == [row.data for row in sorted(full.movement, key=lambda r: r.index)]


def test_a_skipped_row_can_still_end_the_recording(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    last = match.round_start(3) + 55_000
    match.extra_events.append({"type": "valorant_shot_received", "time_ms": last, "actor_net_guid": 1})
    directory = match.write(tmp_path / "export", sorted(match.events(), key=lambda row: row["time_ms"]))
    assert contract.load_export_streaming(directory).end_ms == last == contract.load_export(directory).end_ms


def test_parity_with_health_and_spike_records(tmp_path):
    # P03: the damage notifies' LifeChangeEvents, armor items, BombState and the planted spike, shuffled among
    # noise; one damage notify lacks `DamageKilledTarget`, so only its function name can let the line through.
    from replay_synthetic import player_state_events

    match = SyntheticMatch(shape="swiftplay")
    match.extra_events += player_state_events(match) + noise(match)
    events = match.events()
    random.Random(3).shuffle(events)
    directory = match.write(tmp_path / "export", events)
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf")
    streamed, in_memory = both(directory, source_sha256=match.source_sha256, vrf_path=vrf)
    assert_parity(streamed, in_memory)
    assert streamed.rounds[1]["player_state"]["damage_taken"]["2"] == [4.5]
    assert [e.get("slot") for e in streamed.rounds[1]["player_state"]["spike"]] == [None, 7, None, 6, None, None]


@pytest.mark.skipif(not REAL_FIXTURE.exists(), reason="no Swiftplay fixture")
def test_parity_on_the_committed_real_fixture():
    assert_parity(*both(REAL_FIXTURE, source_sha256=None))
