"""Raw CrouchHeld evidence and per-slot spans on each round's clock."""

import json
import sys
from pathlib import Path

import pytest

from app.replays import condense as cd, extras, format as fmt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_synthetic import MATCH_UUID, SyntheticMatch  # noqa: E402


WINDOWS = [(10_000, 50_000, 60_000), (70_000, 110_000, 120_000)]


def row(t_ms=9291, held=True, pawn=1106):
    # The complete export shape, including fields unrelated to crouch.
    return {"type": "export_group_received", "time_ms": t_ms, "packet_id": 3130,
            "actor_net_guid": pawn, "object_net_guid": pawn, "channel": 98, "is_actor": True,
            "is_deleted": False, "export_group_path": "/Game/Characters/Gumshoe/Gumshoe_PC.Gumshoe_PC_C",
            "kind": "actor", "categories": ["agent"],
            "payload": {"CrouchHeld": held, "ReplicatedMovementMode": 3}}


def raw(tmp_path, rows):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return extras.read_raw(path), path


def spans(tmp_path, rows, pawn_slot=None):
    evidence, _ = raw(tmp_path, rows)
    return extras.crouch_spans(evidence.crouch, {1106: 0} if pawn_slot is None else pawn_slot, WINDOWS)


def test_read_raw_keeps_the_real_crouch_row_through_its_prescreen(tmp_path):
    evidence, _ = raw(tmp_path, [row(), row(9400, False)])
    assert evidence.crouch == [(9291, 1106, True), (9400, 1106, False)]


def test_true_then_false_is_one_span_sorted_by_time(tmp_path):
    assert spans(tmp_path, [row(25_000, False), row(20_000)]) == {1: {0: [[10.0, 15.0]]}}


def test_touching_spans_merge_and_repeated_true_does_not_restart(tmp_path):
    assert spans(tmp_path, [row(20_000), row(21_000), row(25_000, False), row(25_000), row(30_000, False)]) == {
        1: {0: [[10.0, 20.0]]}}


def test_an_open_span_closes_at_round_end(tmp_path):
    assert spans(tmp_path, [row(100_000)]) == {2: {0: [[30.0, 50.0]]}}


def test_a_span_crossing_rounds_is_clipped_to_each_window(tmp_path):
    assert spans(tmp_path, [row(55_000), row(75_000, False)]) == {
        1: {0: [[45.0, 50.0]]}, 2: {0: [[0.0, 5.0]]}}


def test_buy_phase_evidence_is_clipped_to_the_round_start(tmp_path):
    assert spans(tmp_path, [row(), row(11_000, False)]) == {1: {0: [[0.0, 1.0]]}}


def test_unknown_post_death_pawn_is_dropped_and_reported(tmp_path):
    unknown = row(20_000, pawn=9999)
    unknown["export_group_path"] = "/Game/Characters/Gumshoe/Gumshoe_PostDeath_PC.Gumshoe_PostDeath_PC_C"
    evidence, path = raw(tmp_path, [unknown, row(30_000, False, 9999), row(20_000), row(25_000, False)])
    assert extras.crouch_spans(evidence.crouch, {1106: 0}, WINDOWS) == {1: {0: [[10.0, 15.0]]}}
    players = cd.PlayerTable(subjects=[None], agents=["Cypher"], pawn_slot={1106: 0},
                             other_slot={}, pawn_changes={}, gone_ms={})
    game_map = cd.MapInfo("Testmap", "Test", 1e-4, 1e-4, 0.5, 0.5)
    result = extras.build_extras(path, players, WINDOWS, game_map, {"Gumshoe": "Cypher"}, lambda t: {})
    assert result.rounds[1]["crouch"] == {0: [[10.0, 15.0]]}
    assert result.report["crouch_unknown_pawn"] == 2
    assert result.report["crouch_spans"] == 1


def test_a_replaced_pawns_open_span_ends_at_the_next_pawns_spawn(tmp_path):
    # Dies holding the key: pawn 1106 never records False, and its replacement can't close it.
    evidence, _ = raw(tmp_path, [row(20_000), row(70_000, False, 2106)])
    changes = {0: [(0, 1106), (65_000, 2106)]}
    assert extras.crouch_spans(evidence.crouch, {1106: 0, 2106: 0}, WINDOWS, changes) == {1: {0: [[10.0, 50.0]]}}


def test_a_replaced_pawns_span_is_cut_where_the_next_pawn_spawns_inside_a_round(tmp_path):
    evidence, _ = raw(tmp_path, [row(20_000)])
    assert extras.crouch_spans(evidence.crouch, {1106: 0, 2106: 0}, WINDOWS,
                               {0: [(0, 1106), (30_000, 2106)]}) == {1: {0: [[10.0, 20.0]]}}


def test_the_last_pawn_keeps_its_open_span_across_rounds(tmp_path):
    evidence, _ = raw(tmp_path, [row(55_000)])
    assert extras.crouch_spans(evidence.crouch, {1106: 0}, WINDOWS, {0: [(0, 1106)]}) == {
        1: {0: [[45.0, 50.0]]}, 2: {0: [[0.0, 50.0]]}}


def test_build_extras_passes_the_players_spawns(tmp_path):
    evidence, path = raw(tmp_path, [row(20_000), row(70_000, False, 2106)])
    players = cd.PlayerTable(subjects=[None], agents=["Cypher"], pawn_slot={1106: 0, 2106: 0},
                             other_slot={}, pawn_changes={0: [(0, 1106), (65_000, 2106)]}, gone_ms={})
    game_map = cd.MapInfo("Testmap", "Test", 1e-4, 1e-4, 0.5, 0.5)
    result = extras.build_extras(path, players, WINDOWS, game_map, {"Gumshoe": "Cypher"}, lambda t: {})
    assert result.rounds[1]["crouch"] == {0: [[10.0, 50.0]]}
    assert "crouch" not in result.rounds.get(2, {})


def test_empty_false_only_and_outside_round_spans_are_empty(tmp_path):
    assert spans(tmp_path, []) == {}
    assert spans(tmp_path, [row(20_000, False), row(61_000), row(69_000, False)]) == {}


def test_pawns_are_independent_and_only_overlapping_spans_merge(tmp_path):
    rows = [row(20_000), row(25_000, pawn=1107), row(30_000, False), row(35_000, False, 1107),
            row(40_000), row(45_000, False)]
    assert spans(tmp_path, rows, {1106: 0, 1107: 1}) == {
        1: {0: [[10.0, 20.0], [30.0, 35.0]], 1: [[15.0, 25.0]]}}
    assert spans(tmp_path, rows, {1106: 0, 1107: 0}) == {1: {0: [[10.0, 25.0], [30.0, 35.0]]}}


@pytest.mark.parametrize("streaming", [True, False])
def test_condenser_attaches_string_slot_keys_and_marks_empty_rounds(tmp_path, streaming):
    match = SyntheticMatch(shape="swiftplay")
    pawn = match.pawn(1, 0)
    events = match.events() + [row(match.round_start(1) + 1000, pawn=pawn),
                               row(match.round_start(1) + 2000, False, pawn)]
    directory = match.write(tmp_path / "export", events)
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf")
    result = cd.condense_export_dir(directory, source_sha256=match.source_sha256, streaming=streaming, vrf_path=vrf)
    assert result.rounds[1]["crouch"] == {"0": [[1.0, 2.0]]}
    assert all(blob["crouch"] == {} for n, blob in result.rounds.items() if n != 1)
    assert fmt.CONDENSE_REVISION == 25


def test_a_legacy_blob_without_crouch_reads_unchanged():
    blob = {"v": 1, "round": 1, "map": "Ascent", "hz": 16, "t_start": 0.0, "t_end": 1.0,
            "players": [], "tracks": {}, "alive": {}, "kills": [], "plant": None, "defuse": None, "util": []}
    assert fmt.decode_blob(fmt.encode_blob(blob)) == blob
    assert "crouch" not in fmt.decode_blob(fmt.encode_blob(blob))
