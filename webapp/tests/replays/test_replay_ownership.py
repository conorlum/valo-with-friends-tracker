"""P-b: pawn ownership is validated, not first-claim-wins, and possession is an interval."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import MATCH_UUID, SyntheticMatch, subject  # noqa: E402

from app.replays import condense as cd  # noqa: E402
from app.replays.contract import PLAYER_STATE_PATH, ContractError  # noqa: E402

DRONE = 5555


def run(tmp_path, match, events=None):
    directory = match.write(tmp_path / "export", events)
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf") if match.shape == "swiftplay" else None
    return cd.condense_export_dir(directory, source_sha256=match.source_sha256, vrf_path=vrf)


def group(t, actor, payload, path="/Game/Characters/X/X_PC.X_PC_C"):
    return {"type": "export_group_received", "time_ms": t, "actor_net_guid": actor, "object_net_guid": actor,
            "channel": 3, "export_group_path": path, "payload": payload}


def refused(tmp_path, match) -> ContractError:
    with pytest.raises(ContractError) as caught:
        run(tmp_path, match)
    return caught.value


def test_consistent_repeated_claims_condense(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    match.extra_events.append(group(500, match.pawn(1, 0), {"PlayerState": 200}))  # the same claim again
    out = run(tmp_path, match)
    assert out.report["ownership"]["claims"] == 11 and out.report["ownership"]["pawns"] == 10


def test_a_pawn_whose_player_state_changes_refuses(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    match.extra_events.append(group(90_000, match.pawn(1, 3), {"PlayerState": 299}))
    error = refused(tmp_path, match)
    assert error.reason == "ownership" and "PlayerState" in error.detail


def test_a_pawn_claimed_by_two_player_states_refuses_legacy_shape(tmp_path):
    # Two BombPlayerStates both name slot 0's round-1 pawn as their SpawnedCharacter.
    match = SyntheticMatch()
    match.extra_events.append(group(match.round_start(1) - 29_000, 101, {"SpawnedCharacter": match.pawn(1, 0)},
                                    PLAYER_STATE_PATH))
    error = refused(tmp_path, match)
    assert error.reason == "ownership" and "2 player states" in error.detail


def test_a_pawn_claimed_by_two_player_states_refuses_mixed_sources(tmp_path):
    # The character says player state 200; player state 201 says it spawned that character.
    match = SyntheticMatch(shape="swiftplay")
    match.extra_events.append(group(100, 201, {"SpawnedCharacter": match.pawn(1, 0)}, PLAYER_STATE_PATH))
    error = refused(tmp_path, match)
    assert error.reason == "ownership" and "PlayerState" in error.detail and "SpawnedCharacter" in error.detail


def test_possessing_another_players_character_refuses(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    match.extra_events.append(group(100, 204, {"PossessedCharacter": match.pawn(1, 0)}, PLAYER_STATE_PATH))
    assert refused(tmp_path, match).reason == "ownership"


def test_a_subject_that_changes_refuses(tmp_path):
    match = SyntheticMatch()
    match.extra_events.append(group(90_000, 102, {"Subject": subject(42)}, PLAYER_STATE_PATH))
    error = refused(tmp_path, match)
    assert error.reason == "ownership" and "Subject" in error.detail


def _drone_kill(match: SyntheticMatch, possess_from_s: float, possess_to_s: float) -> None:
    """Slot 0's round-1 kill (10 s) is made by a drone that player state 200 possesses for a while."""
    start = match.round_start(1)
    match.extra_events += [group(start + int(possess_from_s * 1000), 200, {"PossessedCharacter": DRONE},
                                 PLAYER_STATE_PATH),
                           group(start + int(possess_to_s * 1000), 200, {"PossessedCharacter": match.pawn(1, 0)},
                                 PLAYER_STATE_PATH)]
    events = match.events()
    for row in events:
        payload = row.get("payload") or {}
        if row.get("function_name") == "MulticastNotifyKilledEnemy" and row["time_ms"] == start + 10_000:
            payload["KillerCharacter"] = DRONE
    match.extra_events = []
    match._events_override = events


class _Match(SyntheticMatch):
    """A synthetic match whose event rows a test rewrote."""

    def events(self):
        override = getattr(self, "_events_override", None)
        return override if override is not None else super().events()


def test_a_possessed_pawn_resolves_a_kill_inside_its_interval(tmp_path):
    match = _Match(shape="swiftplay")
    _drone_kill(match, 9.0, 11.0)
    out = run(tmp_path, match)
    first = out.rounds[1]["kills"][0]
    assert (first["t"], first["killer"], first["victim"]) == (10.0, 0, 5)
    assert out.report["ownership"]["possession_intervals"] == 1


def test_a_possessed_pawn_does_not_resolve_a_kill_outside_its_interval(tmp_path):
    match = _Match(shape="swiftplay")
    _drone_kill(match, 10.5, 12.0)  # the kill at 10 s comes before the possession starts
    error = refused(tmp_path, match)
    assert error.reason == "unresolved_kill"


def test_a_possession_ends_at_the_next_possession_update(tmp_path):
    match = _Match(shape="swiftplay")
    _drone_kill(match, 8.0, 9.5)  # released before the kill at 10 s
    assert refused(tmp_path, match).reason == "unresolved_kill"
