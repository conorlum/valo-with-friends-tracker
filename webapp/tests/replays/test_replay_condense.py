"""The condenser on synthetic exports: rounds, players, sides, tracks, alive intervals, kills."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import AGENT_NAMES, MATCH_UUID, SyntheticMatch, subject  # noqa: E402

from app.replays import condense as cd  # noqa: E402
from app.replays import format as fmt  # noqa: E402
from app.replays.contract import GAME_STATE_PATH, PLAYER_STATE_PATH, ContractError  # noqa: E402

MAPS = cd.load_maps()
ASCENT = MAPS["Ascent"]


def run(tmp_path, match=None, events=None, movement=None, **kwargs):
    match = match or SyntheticMatch()
    directory = match.write(tmp_path / "export", events, movement)
    return cd.condense_export_dir(directory, source_sha256=match.source_sha256, **kwargs)


def samples_of(blob, slot):
    return [s for seg in blob["tracks"].get(str(slot), []) for s in fmt.decode_segment(seg, blob["hz"])]


# ------------------------------------------------------------ the happy path


def test_happy_path(tmp_path):
    out = run(tmp_path)
    assert out.match_uuid == MATCH_UUID
    assert out.map_name == "Ascent" and out.hz == 16 and out.round_count == 3
    assert out.game_branch == "++Ares-Core+release-13.06"
    assert out.recipe.startswith("2b66c65a7b11.c1.f1.a")
    assert [p["agent"] for p in out.players] == AGENT_NAMES
    assert [p["side_group"] for p in out.players] == ["A"] * 5 + ["B"] * 5
    assert [p["subject"] for p in out.players] == [subject(i) for i in range(10)]
    assert out.report["kills"] == 15 and out.report["kills_outside_rounds"] == 0
    assert out.report["diagnostics"]["blocking"] == []
    assert all(check["ok"] for check in out.report["spawn_checks"].values())
    assert out.report["sizes"]["fits_budget"]


def test_blobs_hold_no_identities(tmp_path):
    out = run(tmp_path)
    for data in out.encoded_rounds().values():
        text = json.dumps(fmt.decode_blob(data))
        assert "00000000-0000-4000-8000-0000000000" not in text
        assert MATCH_UUID not in text
    assert "subject" not in json.dumps(out.link_inputs).lower()


def test_round_blob_shape(tmp_path):
    blob = run(tmp_path).rounds[1]
    assert set(blob) == {"v", "round", "map", "hz", "t_start", "t_end", "players", "tracks", "alive", "kills",
                         "plant", "defuse", "util"}
    assert blob["t_start"] == 0.0 and blob["t_end"] == 40.0
    assert blob["players"][0] == {"slot": 0, "agent": "Jett", "side": "A"}
    assert blob["util"] == [] and blob["plant"] is None


def test_played_rounds_are_numbered_from_one_and_round_results_follow_d8(tmp_path):
    out = run(tmp_path)
    assert sorted(out.rounds) == [1, 2, 3]
    assert [out.rounds[n]["round"] for n in (1, 2, 3)] == [1, 2, 3]
    assert out.report["round_number_at_start"] == [0, 1, 2]
    assert {n: r["WinningTeam"] for n, r in out.link_inputs["round_results"].items()} == \
        {"1": "Red", "2": "Blue", "3": "Red"}


def test_kills_in_slot_terms_with_victim_positions(tmp_path):
    out = run(tmp_path)
    first = out.rounds[1]["kills"][0]
    assert (first["i"], first["t"], first["killer"], first["victim"]) == (0, 10.0, 0, 5)
    assert 0 <= first["u"] <= 10000 and 0 <= first["v"] <= 10000
    assert out.link_inputs["kills"]["3"] == [[5.0, 2, 9], [6.0, 9, 2], [6.05, 8, 3], [20.0, 4, 8]]


def test_alive_intervals(tmp_path):
    alive = run(tmp_path).rounds[1]["alive"]
    assert alive["5"] == [[0.0, 10.0, "kill"]]
    assert alive["0"] == [[0.0, None, "round_end"]]


# ------------------------------------------------------------ rounds


def test_a_final_round_without_round_ending_is_dropped_and_reported(tmp_path):
    out = run(tmp_path, SyntheticMatch(drop_final_round_end=True))
    assert out.round_count == 2
    assert out.report["dropped_final_round"] is True


def test_a_mid_match_round_without_round_ending_refuses(tmp_path):
    match = SyntheticMatch()
    events = [e for e in match.events()
              if not (e.get("payload", {}).get("Phase") == 5 and e["time_ms"] == match.round_start(1) + 40_000)]
    with pytest.raises(ContractError) as refused:
        run(tmp_path, match, events=events)
    assert refused.value.reason == "round_without_end"


def test_a_one_round_match(tmp_path):
    out = run(tmp_path, SyntheticMatch(rounds=1))
    assert out.round_count == 1


def test_overtime_rounds_keep_the_same_side_groups(tmp_path):
    out = run(tmp_path, SyntheticMatch(rounds=6))
    assert out.round_count == 6
    assert [p["side_group"] for p in out.players] == ["A"] * 5 + ["B"] * 5
    assert len(out.link_inputs["start_positions"]) == 6


def test_round_results_fallback_is_reported_not_fatal(tmp_path):
    match = SyntheticMatch()
    events = match.events() + [{"type": "export_group_received", "time_ms": 1, "packet_id": 1, "actor_net_guid": 50,
                                "object_net_guid": 50, "channel": 2, "export_group_path": GAME_STATE_PATH,
                                "payload": {"RoundResults": {"raw": "AAAA"}}}]
    out = run(tmp_path, match, events=events)
    assert out.report["round_results_fallback"] is True
    assert out.link_inputs["round_results_fallback"] is True


# ------------------------------------------------------------ players and map


def test_the_map_comes_from_actor_paths_or_an_override(tmp_path):
    match = SyntheticMatch(map_code="Nowhere")
    with pytest.raises(ContractError) as refused:
        run(tmp_path / "a", match)
    assert refused.value.reason == "map"
    assert run(tmp_path / "b", match, map_override="Ascent").map_name == "Ascent"
    with pytest.raises(ContractError):
        run(tmp_path / "c", SyntheticMatch(), map_override="Bind")


def test_nine_subjects_refuse(tmp_path):
    match = SyntheticMatch()
    events = [e for e in match.events() if not (e.get("actor_net_guid") == 109 and "Subject" in e.get("payload", {}))]
    with pytest.raises(ContractError) as refused:
        run(tmp_path, match, events=events)
    assert refused.value.reason == "subjects"


def test_an_unknown_agent_code_refuses(tmp_path):
    codes = list(SyntheticMatch().agent_codes)
    codes[3] = "NotAnAgent"
    with pytest.raises(ContractError) as refused:
        run(tmp_path, SyntheticMatch(agent_codes=codes))
    assert refused.value.reason == "unknown_agent"


def test_an_unresolved_kill_refuses(tmp_path):
    match = SyntheticMatch()
    events = match.events() + [{"type": "rpc_received", "time_ms": match.round_start(1) + 1000, "packet_id": 1,
                                "actor_net_guid": 1, "object_net_guid": 1, "channel": 1,
                                "function_name": "MulticastNotifyKilledEnemy",
                                "payload": {"KillerCharacter": 99999, "KilledCharacter": match.pawn(1, 5)}}]
    with pytest.raises(ContractError) as refused:
        run(tmp_path, match, events=events)
    assert refused.value.reason == "unresolved_kill"


# ------------------------------------------------------------ side groups


def test_side_groups_from_kills_alone():
    kills = [cd.Kill(0, a, b) for a, b in [(0, 5), (5, 1), (1, 6), (6, 2), (2, 7), (7, 3), (3, 8), (8, 4), (4, 9)]]
    assert cd.side_groups(kills, []) == ["A"] * 5 + ["B"] * 5


def test_side_groups_refuse_inconsistent_evidence():
    clusters = [([0, 1, 2, 3, 4], [5, 6, 7, 8, 9])]
    with pytest.raises(ContractError, match="inconsistent"):
        cd.side_groups([cd.Kill(0, 0, 1)], clusters)


def test_side_groups_refuse_disconnected_evidence():
    with pytest.raises(ContractError, match="disconnected"):
        cd.side_groups([cd.Kill(0, 0, 5)], [])


def test_side_groups_refuse_an_uneven_split():
    kills = [cd.Kill(0, 0, s) for s in range(1, 10)]
    with pytest.raises(ContractError, match="1/9|9/1"):
        cd.side_groups(kills, [])


def test_spawn_clusters_with_a_straggler_fail_the_check(tmp_path):
    match = SyntheticMatch()
    movement = match.movement()
    for row in movement:
        if row["shooter_character_net_guid"] == match.pawn(1, 0):
            row["position"]["x"] += 5000.0
    out = run(tmp_path, match, movement=movement)
    assert out.report["spawn_checks"]["1"]["ok"] is False


# ------------------------------------------------------------ tracks


def test_tracks_sit_on_the_native_grid(tmp_path):
    blob = run(tmp_path).rounds[1]
    [segment] = blob["tracks"]["0"]
    assert segment["t0"] == 0.0
    points = samples_of(blob, 0)
    assert len(points) == 40 * 16 + 1
    assert points[16][0] == 1.0


def test_a_gap_over_one_second_splits_a_segment_and_a_short_gap_does_not(tmp_path):
    match = SyntheticMatch()
    start = match.round_start(1)
    long_gap = [r for r in match.movement() if not (r["shooter_character_net_guid"] == match.pawn(1, 0)
                                                     and start + 5000 < r["time_ms"] < start + 7000)]
    blob = run(tmp_path / "long", match, movement=long_gap).rounds[1]
    assert len(blob["tracks"]["0"]) == 2
    short_gap = [r for r in match.movement() if not (r["shooter_character_net_guid"] == match.pawn(1, 0)
                                                      and start + 5000 < r["time_ms"] < start + 5500)]
    blob = run(tmp_path / "short", match, movement=short_gap).rounds[1]
    assert len(blob["tracks"]["0"]) == 1
    assert len(samples_of(blob, 0)) == 40 * 16 + 1, "grid points inside a short gap are filled, not dropped"


def test_a_teleport_splits_a_segment(tmp_path):
    match = SyntheticMatch()
    movement = match.movement()
    for row in movement:
        if row["shooter_character_net_guid"] == match.pawn(1, 0) and row["time_ms"] >= match.round_start(1) + 5000:
            row["position"]["x"] += cd.TELEPORT_UNITS + 100
    assert len(run(tmp_path, match, movement=movement).rounds[1]["tracks"]["0"]) == 2


def test_samples_after_death_are_dropped(tmp_path):
    match = SyntheticMatch()
    movement = match.movement()
    dead = match.death_ms(1, 5)
    movement.append({**movement[0], "time_ms": dead + 3000, "actor_net_guid": match.pawn(1, 5),
                     "shooter_character_net_guid": match.pawn(1, 5), "position": {"x": 0.0, "y": 0.0, "z": 0.0}})
    blob = run(tmp_path, match, movement=movement).rounds[1]
    assert max(t for t, *_ in samples_of(blob, 5)) <= 10.0


def test_a_revive_opens_a_new_interval_and_segment(tmp_path):
    match = SyntheticMatch()
    start = match.round_start(1)
    events = match.events() + [{"type": "rpc_received", "time_ms": start + 12_000, "packet_id": 1,
                                "actor_net_guid": 50, "object_net_guid": 50, "channel": 2,
                                "function_name": "MulticastReceivePlayerResurrectEvent",
                                "payload": {"ResurrectorPlayer": 106, "ResurrectedPlayer": 105}}]
    movement = match.movement()
    x0, y0 = match.spawn_position(1, 5)
    for k in range(16):
        movement.append({**movement[0], "time_ms": start + 12_000 + k * 62, "actor_net_guid": match.pawn(1, 5),
                         "shooter_character_net_guid": match.pawn(1, 5), "position": {"x": x0, "y": y0, "z": 100.0}})
    blob = run(tmp_path, match, events=events, movement=movement).rounds[1]
    assert blob["alive"]["5"] == [[0.0, 10.0, "kill"], [12.0, None, "round_end"]]
    assert len(blob["tracks"]["5"]) == 2


def test_a_pawn_change_splits_the_segment_but_not_the_life(tmp_path):
    match = SyntheticMatch()
    start = match.round_start(1)
    new_pawn = 7777
    events = match.events() + [
        {"type": "actor_spawned", "time_ms": start + 5000, "packet_id": 1, "actor_net_guid": new_pawn, "channel": 99,
         "is_dynamic": True, "actor_path": None, "archetype_path": "/Game/Characters/Wushu/Wushu_PC.Default__Wushu_PC_C"},
        {"type": "export_group_received", "time_ms": start + 5000, "packet_id": 1, "actor_net_guid": 100,
         "object_net_guid": 100, "channel": 3, "export_group_path": PLAYER_STATE_PATH,
         "payload": {"SpawnedCharacter": new_pawn}},
    ]
    movement = match.movement()
    for row in movement:
        if row["shooter_character_net_guid"] == match.pawn(1, 0) and row["time_ms"] > start + 5000:
            row["shooter_character_net_guid"] = new_pawn
    blob = run(tmp_path, match, events=events, movement=movement).rounds[1]
    assert blob["alive"]["0"] == [[0.0, None, "round_end"]]
    assert len(blob["tracks"]["0"]) == 2


def test_error_sentinel_rows_are_dropped(tmp_path):
    match = SyntheticMatch()
    movement = match.movement()
    movement[0] = {**movement[0], "error_sentinel": True}
    out = run(tmp_path, match, movement=movement)
    assert out.report["movement"]["error_sentinel"] == 1


def test_coverage_and_gaps_are_reported(tmp_path):
    match = SyntheticMatch()
    start = match.round_start(2)
    movement = [r for r in match.movement() if not (r["shooter_character_net_guid"] == match.pawn(2, 4)
                                                     and start + 10_000 < r["time_ms"] < start + 25_000)]
    out = run(tmp_path, match, movement=movement)
    assert out.report["coverage"]["4"]["min"] < cd.MIN_ALIVE_COVERAGE
    assert out.report["coverage"]["4"]["max_gap_s"] >= 15.0
    assert out.report["coverage"]["0"]["max_gap_s"] < 1.0


def test_hz_is_the_exported_rate(tmp_path):
    assert run(tmp_path, SyntheticMatch(hz=32)).hz == 32


# ------------------------------------------------------------ geometry


def test_uv_against_two_known_points():
    # Ascent: u = y * 7e-05 + 0.813895, v = x * -7e-05 + 0.573242.
    assert ASCENT.to_uv(0.0, 0.0) == (8139, 5732)
    assert ASCENT.to_uv(-2000.0, 500.0) == (8489, 7132)
    assert ASCENT.to_uv(1e9, 1e9) == (10000, 0), "clamped to the image"


def test_yaw_is_projected_into_map_space():
    # Facing world +y moves along +u (0 degrees); facing world +x moves along -v (270) on Ascent.
    assert ASCENT.yaw_to_map(90.0) == 0
    assert ASCENT.yaw_to_map(0.0) == 270
    assert ASCENT.yaw_to_map(180.0) == 90


# ------------------------------------------------------------ diagnostics


def test_blocking_diagnostics_refuse_unless_allowed(tmp_path):
    match = SyntheticMatch(manifest_extra={"diagnostics": [{"code": "brand_new_code"}]})
    with pytest.raises(ContractError) as refused:
        run(tmp_path / "a", match)
    assert refused.value.reason == "blocking_diagnostics"
    out = run(tmp_path / "b", match, allow_blocking=True)
    assert out.report["diagnostics"]["blocking"]
