"""The condenser on synthetic exports: rounds, players, sides, tracks, alive intervals, kills."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import AGENT_NAMES, DEFAULT_KILLS, MATCH_UUID, SyntheticMatch, subject  # noqa: E402

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
    assert out.recipe.startswith(f"2b66c65a7b11.c{fmt.CONDENSE_REVISION}.f1.a")
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
    assert set(blob) == {"v", "round", "map", "hz", "t_start", "t_decided", "t_end", "players", "tracks", "alive",
                         "kills", "plant", "defuse", "util", "movement_casts"}
    # t_decided is the RoundEnding; playback runs on to the next phase (the next buy, 10 s later).
    assert blob["t_start"] == 0.0 and blob["t_decided"] == 40.0 and blob["t_end"] == 50.0
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
    assert refused.value.reason == "phase_cycle"


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


def test_a_player_state_without_a_subject_is_still_a_player(tmp_path):
    match = SyntheticMatch()
    events = [e for e in match.events() if not (e.get("actor_net_guid") == 109 and "Subject" in e.get("payload", {}))]
    out = run(tmp_path, match, events=events)
    assert [p["subject"] for p in out.players] == [subject(i) for i in range(9)] + [None]
    assert out.report["players"]["subjects_decoded"] == 9


def test_nine_players_refuse(tmp_path):
    match = SyntheticMatch()
    pawns_of_9 = {match.pawn(n, 9) for n in range(1, match.rounds + 1)}
    events = [e for e in match.events() if not (e["type"] == "actor_spawned" and e["actor_net_guid"] in pawns_of_9)]
    with pytest.raises(ContractError) as refused:
        run(tmp_path, match, events=events)
    assert refused.value.reason == "players"


def test_two_players_sharing_a_subject_refuse(tmp_path):
    match = SyntheticMatch()
    events = match.events()
    for e in events:
        if e.get("actor_net_guid") == 109 and "Subject" in e.get("payload", {}):
            e["payload"]["Subject"] = subject(0)
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


def lethal_damage(match, n, t, victim_pawn, killer_pawn, function="MulticastNotifyDamage_Point"):
    """A lethal damage notify, as the Abyss export sends for some kills with no kill RPC."""
    return {"type": "rpc_received", "time_ms": match.round_start(n) + int(t * 1000), "packet_id": 1,
            "actor_net_guid": victim_pawn, "object_net_guid": victim_pawn + 5000, "channel": 72,
            "function_name": function,
            "payload": {"AliveAfterDamage": False, "Character": victim_pawn, "DamageKilledTarget": True,
                        "EventInstigatorPawn": killer_pawn, "DamageDealt": 78}}


def test_a_kill_with_only_a_lethal_damage_notify_is_a_kill(tmp_path):
    match = SyntheticMatch()
    events = match.events() + [lethal_damage(match, 3, 25.0, match.pawn(3, 0), match.pawn(3, 5))]
    out = run(tmp_path, match, events=events)
    assert [25.0, 5, 0] in out.link_inputs["kills"]["3"]
    assert out.rounds[3]["alive"]["0"] == [[0.0, 25.0, "kill"]]
    assert out.report["kill_sources"] == {"from_damage": 1, "damage_without_killer": 0}


def test_a_lethal_damage_notify_beside_its_kill_rpc_is_not_a_second_kill(tmp_path):
    match = SyntheticMatch()
    # Round 1's first kill (10.0 s, slot 0 kills slot 5) also has its damage notify, 3 ms later.
    events = match.events() + [lethal_damage(match, 1, 10.003, match.pawn(1, 5), match.pawn(1, 0))]
    out = run(tmp_path, match, events=events)
    assert [k["victim"] for k in out.rounds[1]["kills"]].count(5) == 1
    assert out.report["kill_sources"]["from_damage"] == 0


def test_lethal_damage_on_an_object_or_with_no_killer_adds_no_kill(tmp_path):
    match = SyntheticMatch()
    events = match.events() + [
        lethal_damage(match, 3, 25.0, 99999, match.pawn(3, 5), "MulticastNotifyDamage_Base"),  # a camera
        lethal_damage(match, 3, 26.0, match.pawn(3, 0), 99998),                                # no killer
    ]
    out = run(tmp_path, match, events=events)
    assert out.rounds[3]["alive"]["0"] == [[0.0, None, "round_end"]]
    assert out.report["kill_sources"] == {"from_damage": 0, "damage_without_killer": 1}


def test_the_streaming_loader_keeps_every_damage_notify(tmp_path):
    from app.replays import contract

    match = SyntheticMatch()
    lethal = lethal_damage(match, 3, 25.0, match.pawn(3, 0), match.pawn(3, 5))
    harmless = {**lethal, "time_ms": lethal["time_ms"] - 3000,
                "payload": {**lethal["payload"], "DamageKilledTarget": False, "AliveAfterDamage": True}}
    assert contract.keep_event(lethal) and contract.keep_event(harmless)
    directory = match.write(tmp_path / "export", match.events() + [lethal, harmless], None)
    streamed = cd.condense_export_dir(directory, source_sha256=match.source_sha256, streaming=True)
    in_memory = cd.condense_export_dir(directory, source_sha256=match.source_sha256, streaming=False)
    assert streamed.encoded_rounds() == in_memory.encoded_rounds()
    assert streamed.report["kill_sources"]["from_damage"] == 1
    runs = [u for u in streamed.rounds[3]["util"] if u["k"] == "damage"]
    assert [(u["t"], u["by"], u["target"], u["n"]) for u in runs] == [(22.0, 5, 0, 1), (25.0, 5, 0, 1)]


def damage(match, n, t, victim_pawn, attacker_pawn, *, point=True, wall=False, state=None):
    row = lethal_damage(match, n, t, victim_pawn, attacker_pawn,
                        "MulticastNotifyDamage_Point" if point else "MulticastNotifyDamage_Base")
    row["payload"] = {**row["payload"], "DamageKilledTarget": False, "AliveAfterDamage": True,
                      "DamagerPlayerState": state}
    if point:
        row["payload"]["IsWallPenetration"] = wall
    return row


def test_damage_hits_become_runs_by_attacker_target_kind_and_wallbang(tmp_path):
    match = SyntheticMatch()
    p = lambda slot: match.pawn(2, slot)  # noqa: E731
    events = match.events() + [
        damage(match, 2, 3.0, p(1), p(6)), damage(match, 2, 3.2, p(1), p(6)), damage(match, 2, 3.6, p(1), p(6)),
        damage(match, 2, 4.5, p(1), p(6)),                               # 0.9 s later: a new run
        damage(match, 2, 3.1, p(1), p(6), wall=True),                    # a wallbang is its own run
        damage(match, 2, 5.0, p(2), p(7), point=False), damage(match, 2, 5.25, p(2), p(7), point=False),
        damage(match, 2, 6.0, p(3), 99999, state=None),                  # no attacker: dropped
        damage(match, 2, 6.5, p(3), p(3), point=False),                  # their own molly: dropped
        damage(match, 2, 7.0, 99999, p(8)),                              # an object: not a player
    ]
    out = run(tmp_path, match, events=events)
    runs = [(u["t"], u["t1"], u["by"], u["target"], u["src"], u["wall"], u["n"])
            for u in out.rounds[2]["util"] if u["k"] == "damage"]
    assert runs == [(3.0, 3.6, 6, 1, "gun", False, 3), (3.1, 3.1, 6, 1, "gun", True, 1),
                    (4.5, 4.5, 6, 1, "gun", False, 1), (5.0, 5.25, 7, 2, "ability", False, 2)]
    assert out.report["damage"]["damage_unresolved"] == 1 and out.report["damage"]["damage_self"] == 1
    assert out.report["damage"]["wallbang_runs"] == 1


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
    assert len(points) == 50 * 16 + 1  # to t_end: the next buy phase, 10 s after RoundEnding
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
    assert len(samples_of(blob, 0)) == 50 * 16 + 1, "grid points inside a short gap are filled, not dropped"


def test_a_teleport_splits_a_segment(tmp_path):
    match = SyntheticMatch()
    movement = match.movement()
    for row in movement:
        if row["shooter_character_net_guid"] == match.pawn(1, 0) and row["time_ms"] >= match.round_start(1) + 5000:
            row["position"]["x"] += cd.TELEPORT_UNITS + 100
    assert len(run(tmp_path, match, movement=movement).rounds[1]["tracks"]["0"]) == 2


# Heights (revision 11; docs/superpowers/specs/2026-10-01-control-heights-design.md, part 2)


def heights_of(blob, slot):
    return [[z for *_, z in fmt.decode_segment_z(seg, blob["hz"])] for seg in blob["tracks"].get(str(slot), [])]


def test_every_segment_keeps_its_height_in_decimetres(tmp_path):
    match = SyntheticMatch()
    movement = match.movement()
    start = match.round_start(1)
    for row in movement:
        if row["shooter_character_net_guid"] == match.pawn(1, 0) and row["time_ms"] >= start + 5000:
            row["position"]["z"] = 304.0     # up a 2 m step (well under TELEPORT_UNITS)
    blob = run(tmp_path, match, movement=movement).rounds[1]
    [zs] = heights_of(blob, 0)
    assert zs[0] == 10 and zs[-1] == 30 and set(zs) == {10, 30}, "world z / 10, rounded, no map offset"
    assert all("z" in seg for segs in blob["tracks"].values() for seg in segs)
    assert len(samples_of(blob, 0)[0]) == 4, "decode_segment still returns four values"


def without_heights(blob):
    return {slot: [{k: v for k, v in seg.items() if k != "z"} for seg in segs] for slot, segs in blob["tracks"].items()}


def test_a_sample_without_a_height_leaves_its_run_without_heights_and_moves_nothing(tmp_path):
    match = SyntheticMatch()
    start = match.round_start(1)
    # off-grid times (16 Hz is 62.5 ms), so several samples land on one grid point and others are filled
    movement = [dict(r, time_ms=r["time_ms"] + 17, position=dict(r["position"])) for r in match.movement()]
    movement = [r for r in movement if not (r["shooter_character_net_guid"] == match.pawn(1, 0)
                                            and start + 6000 < r["time_ms"] < start + 6400)]
    reference = run(tmp_path / "all", match, movement=[dict(r, position=dict(r["position"])) for r in movement])
    for row in movement:
        if row["shooter_character_net_guid"] == match.pawn(1, 0) and start + 5000 <= row["time_ms"] < start + 7000:
            del row["position"]["z"]
    blob = run(tmp_path / "mixed", match, movement=movement).rounds[1]
    assert without_heights(blob) == without_heights(reference.rounds[1]), "times and positions as with every height"
    assert not any("z" in seg for seg in blob["tracks"]["0"]), "missing, never zero and never invented"
    assert all("z" in seg for seg in blob["tracks"]["1"]), "the other players are untouched"


def test_heights_never_change_the_stored_times_and_positions(tmp_path):
    match = SyntheticMatch()
    start = match.round_start(1)
    movement = match.movement()
    mine = [r for r in movement if r["shooter_character_net_guid"] == match.pawn(1, 0)]
    # the reviewer's two cases: a height that comes and goes within one grid point, and across a filled gap
    extra = [dict(mine[0], time_ms=start + 3000 + ms, position=dict(mine[0]["position"], x=mine[0]["position"]["x"] + ms))
             for ms in (10, 20)]
    del extra[0]["position"]["z"]
    gap = [r for r in movement + extra if not (r["shooter_character_net_guid"] == match.pawn(1, 0)
                                               and start + 8000 < r["time_ms"] < start + 8250)]
    for row in gap:
        if row["shooter_character_net_guid"] == match.pawn(1, 0) and row["time_ms"] >= start + 8250:
            row["position"].pop("z", None)
    flat = [dict(r, position={k: v for k, v in r["position"].items() if k != "z"}) for r in gap]
    with_z = run(tmp_path / "z", match, movement=gap).rounds[1]
    assert without_heights(with_z) == run(tmp_path / "flat", match, movement=flat).rounds[1]["tracks"]


def test_a_match_without_any_height_stores_none(tmp_path):
    match = SyntheticMatch()
    movement = match.movement()
    for row in movement:
        row["position"].pop("z")
    out = run(tmp_path, match, movement=movement)
    assert not any("z" in seg for blob in out.rounds.values() for segs in blob["tracks"].values() for seg in segs)
    assert len(out.rounds[1]["tracks"]["0"]) == 1, "missing heights alone split nothing"


def test_a_filled_grid_point_takes_the_height_between_its_neighbours(tmp_path):
    match = SyntheticMatch()
    start = match.round_start(1)
    movement = [r for r in match.movement() if not (r["shooter_character_net_guid"] == match.pawn(1, 0)
                                                    and start + 5000 < r["time_ms"] < start + 5500)]
    for row in movement:
        if row["shooter_character_net_guid"] == match.pawn(1, 0) and row["time_ms"] >= start + 5500:
            row["position"]["z"] = 500.0
    blob = run(tmp_path, match, movement=movement).rounds[1]
    [zs] = heights_of(blob, 0)
    assert len(zs) == 50 * 16 + 1
    assert zs[80] == 10 and zs[88] == 50 and zs[84] == 30, "linear between the samples either side of the gap"


def test_a_teleport_still_splits_when_heights_are_missing(tmp_path):
    match = SyntheticMatch()
    movement = match.movement()
    for row in movement:
        row["position"].pop("z")
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


# ------------------------------------------------------------ the first real export's shape (Swiftplay)


def run_swiftplay(tmp_path, match=None, with_vrf=True, **kwargs):
    match = match or SyntheticMatch(shape="swiftplay")
    directory = match.write(tmp_path / "export")
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf") if with_vrf else None
    return cd.condense_export_dir(directory, source_sha256=match.source_sha256, vrf_path=vrf, **kwargs)


def test_swiftplay_shape_condenses(tmp_path):
    out = run_swiftplay(tmp_path)
    assert out.match_uuid == MATCH_UUID and out.report["match_uuid_source"] == "vrf_header"
    assert out.map_name == "Ascent" and out.report["map"]["source"] == "vrf"
    assert out.round_count == 3 and out.hz == 16
    assert [p["agent"] for p in out.players] == AGENT_NAMES, "Clove's post-death pawn is not a player"
    assert [p["subject"] for p in out.players] == [None] * 10
    assert [p["side_group"] for p in out.players] == ["A"] * 5 + ["B"] * 5
    assert out.link_inputs["round_results"] == {}
    for blob in out.rounds.values():
        assert sorted(blob["tracks"], key=int) == [str(s) for s in range(10)]


def test_swiftplay_shape_matches_the_legacy_shape_blob_for_blob(tmp_path):
    legacy = run(tmp_path / "legacy")
    swift = run_swiftplay(tmp_path / "swift")
    assert swift.rounds == legacy.rounds


def test_swiftplay_shape_without_the_vrf_needs_a_map(tmp_path):
    with pytest.raises(ContractError) as refused:
        run_swiftplay(tmp_path / "a", with_vrf=False)
    assert refused.value.reason == "map"
    out = run_swiftplay(tmp_path / "b", with_vrf=False, map_override="Ascent")
    assert out.report["map"]["source"] == "override"


def test_a_vrf_not_named_by_its_header_uuid_refuses_for_local_ingest(tmp_path):
    match = SyntheticMatch(shape="swiftplay", manifest_extra={"source_file": "my-replay.vrf"})
    with pytest.raises(ContractError) as refused:
        run_swiftplay(tmp_path, match)
    assert refused.value.reason == "match_id" and "file name" in refused.value.detail


def test_an_upload_takes_the_header_uuid_whatever_the_file_is_called(tmp_path):
    match = SyntheticMatch(shape="swiftplay", manifest_extra={"source_file": "my-replay.vrf"})
    directory = match.write(tmp_path / "export")
    vrf = match.write_vrf(tmp_path / "upload.vrf")
    out = cd.condense_export_dir(directory, source_sha256=match.source_sha256, vrf_path=vrf, check_file_name=False)
    assert out.match_uuid == MATCH_UUID and out.report["match_uuid_source"] == "vrf_header"


def test_a_game_state_match_id_that_disagrees_with_the_header_refuses(tmp_path):
    match = SyntheticMatch()  # the legacy shape decodes a MatchID
    events = match.events()
    for row in events:
        if (row.get("payload") or {}).get("MatchID"):
            row["payload"]["MatchID"] = "00000000-0000-4000-8000-00000000beef"
    directory = match.write(tmp_path / "export", events)
    with pytest.raises(ContractError) as refused:
        cd.condense_export_dir(directory, source_sha256=match.source_sha256,
                               vrf_path=match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf"))
    assert refused.value.reason == "match_id" and "disagree" in refused.value.detail


def test_a_vrf_without_a_valid_header_refuses(tmp_path):
    match = SyntheticMatch(shape="swiftplay", vrf_bytes=b"/Game/Maps/Ascent/Ascent not a replay")
    with pytest.raises(ContractError) as refused:
        run_swiftplay(tmp_path, match)
    assert refused.value.reason == "vrf_header"


QUIET_ROUND_3 = {**{k: list(v) for k, v in DEFAULT_KILLS.items() if k != 3}, 3: [(20.0, 4, 8)]}


@pytest.mark.parametrize("leave_s, alive", [(-5.0, []), (3.0, [[0.0, 3.0, "left"]])])
def test_a_player_who_leaves(tmp_path, leave_s, alive):
    probe = SyntheticMatch(shape="swiftplay")
    match = SyntheticMatch(shape="swiftplay", kills={k: list(v) for k, v in QUIET_ROUND_3.items()},
                           leaver=(9, probe.round_start(3) + int(leave_s * 1000)))
    out = run_swiftplay(tmp_path, match)
    blob = out.rounds[3]
    assert blob["alive"]["9"] == alive
    assert ("9" in blob["tracks"]) == bool(alive)
    assert out.report["players"]["left"] == {"9": round((probe.round_start(3) + leave_s * 1000) / 1000, 3)}
    assert out.report["spawn_checks"]["3"]["short_handed"] == (not alive)
    assert out.rounds[2]["alive"]["9"][0][2] in ("kill", "round_end"), "earlier rounds are untouched"


def test_a_self_kill_ends_the_life_but_is_no_side_evidence(tmp_path):
    kills = {k: list(v) for k, v in DEFAULT_KILLS.items()}
    kills[1].append((35.0, 3, 3))
    out = run_swiftplay(tmp_path, SyntheticMatch(shape="swiftplay", kills=kills))
    assert out.rounds[1]["alive"]["3"] == [[0.0, 35.0, "kill"]]
    assert [p["side_group"] for p in out.players] == ["A"] * 5 + ["B"] * 5


def test_loose_spawn_clusters_are_not_side_evidence(tmp_path):
    # One side roams during the buy phase (as in the first real export): its round-start
    # positions spread wider than the cluster radius, and a 2-means split would cross sides.
    # Two more kills connect all ten through kills alone, as the real match's did.
    kills = {k: list(v) for k, v in DEFAULT_KILLS.items()}
    kills[3] += [(30.0, 0, 6), (31.0, 1, 7)]  # victims still alive in round 3 (P-c: no second deaths)
    match = SyntheticMatch(shape="swiftplay", kills=kills)
    movement = match.movement()
    for row in movement:
        slot = row["shooter_character_net_guid"] - 1000
        if slot >= 5 and slot % 2:
            row["position"]["x"] -= 7000.0
    directory = match.write(tmp_path / "export", movement=movement)
    out = cd.condense_export_dir(directory, source_sha256=match.source_sha256,
                                 vrf_path=match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf"))
    assert [p["side_group"] for p in out.players] == ["A"] * 5 + ["B"] * 5
    assert not any(c["ok"] for c in out.report["spawn_checks"].values())


def test_match_start_spawn_points_carry_the_sides_when_kills_alone_do_not(tmp_path):
    # One round of kills leaves the players in several groups; the pawns' tight spawn lines join them.
    kills = {1: [(10.0, 0, 5), (12.0, 1, 6), (15.0, 2, 7)]}
    match = SyntheticMatch(shape="swiftplay", rounds=1, kills=kills)
    movement = match.movement()
    for row in movement:  # and the round-start positions are loose, as in the real export
        if (row["shooter_character_net_guid"] - 1000) % 2:
            row["position"]["x"] -= 7000.0
    directory = match.write(tmp_path / "export", movement=movement)
    out = cd.condense_export_dir(directory, source_sha256=match.source_sha256,
                                 vrf_path=match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf"))
    assert out.report["match_spawn_check"]["ok"]
    assert [p["side_group"] for p in out.players] == ["A"] * 5 + ["B"] * 5


# ------------------------------------------------------------ the committed real fixture


REAL_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "replay" / "swiftplay"


@pytest.mark.skipif(not REAL_FIXTURE.exists(), reason="no Swiftplay fixture")
def test_the_real_swiftplay_fixture_condenses():
    # Round 1 of a real Swiftplay match on Abyss (13.06), movement thinned to every 8th row.
    out = cd.condense_export_dir(REAL_FIXTURE, source_sha256=None)
    assert out.map_name == "Abyss" and out.round_count == 1 and out.hz == 16
    assert [p["agent"] for p in out.players] == ["Viper", "Jett", "KAY/O", "Chamber", "Clove",
                                                 "Fade", "Reyna", "Veto", "Jett", "Clove"]
    assert [p["subject"] for p in out.players] == [None] * 10
    assert [p["side_group"] for p in out.players] == ["A", "B", "B", "B", "A", "B", "A", "A", "A", "B"]
    assert out.report["match_spawn_check"]["ok"]
    assert out.report["kills"] == 8 and out.report["kills_outside_rounds"] == 0
    assert sorted(out.rounds[1]["tracks"], key=int) == [str(s) for s in range(10)]
    assert all(c["min"] >= cd.MIN_ALIVE_COVERAGE for c in out.report["coverage"].values())


def test_unresolvable_sides_are_null_and_the_replay_still_condenses(tmp_path):
    # A kill inside one spawn group contradicts the clusters: no consistent 5/5 partition.
    # Pass 5 refused the whole condense; pass 6 stores and plays it with neutral colours.
    kills = {k: list(v) for k, v in DEFAULT_KILLS.items()}
    kills[1].append((30.0, 0, 1))
    out = run(tmp_path, SyntheticMatch(kills=kills))
    assert [p["side_group"] for p in out.players] == [None] * 10
    assert [p["side"] for p in out.rounds[1]["players"]] == [None] * 10
    assert out.report["sides"]["resolved"] is False and "inconsistent" in out.report["sides"]["reason"]


@pytest.mark.skipif(not REAL_FIXTURE.exists(), reason="no Swiftplay fixture")
def test_the_real_swiftplay_fixture_passes_the_pass_6_checks():
    # Regenerated by the pass-6 fixture maker: it keeps the ClientGamePhaseEnded rows, close
    # reasons and channels, and round 1's post-decision period (RoundEnding to the next phase).
    out = cd.condense_export_dir(REAL_FIXTURE, source_sha256=None)
    blob = out.rounds[1]
    assert (blob["t_decided"], blob["t_end"]) == (53.049, 60.041)
    assert out.report["phase_ended_checked"] is True and out.report["kills_outside_rounds"] == 0
    assert out.report["lifecycle"]["contradictions"] == 0 and out.report["sides"]["resolved"] is True
    assert out.link_inputs["eligibility"]["eligible"] is True


COMPETITIVE_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "replay" / "competitive"


@pytest.mark.skipif(not COMPETITIVE_FIXTURE.exists(), reason="no competitive fixture")
def test_the_real_competitive_fixture_condenses_with_decoded_game_state():
    # Rounds 1-2 of a real competitive match on Ascent (13.06), movement thinned to every 20th
    # row, identities synthesised (scripts/make_replay_fixture.py). Competitive decodes what
    # Swiftplay didn't: Subjects, the MatchID and RoundResults (Stage 1b, finding 21).
    out = cd.condense_export_dir(COMPETITIVE_FIXTURE, source_sha256=None)
    assert (out.map_name, out.round_count, out.hz) == ("Ascent", 2, 6)
    assert out.match_uuid == "00000000-0000-4000-8000-00000000f1c5"
    assert out.report["match_uuid_source"] == "game_state"
    assert [p["agent"] for p in out.players] == ["Waylay", "Cypher", "Phoenix", "Omen", "Cypher",
                                                 "Sova", "Iso", "Clove", "Skye", "Omen"]
    assert all(p["subject"].startswith("00000000-0000-4000-8000-") for p in out.players)
    assert len({p["subject"] for p in out.players}) == 10
    assert out.link_inputs["round_results"]["1"]["WinningTeam"] == "Blue"
    assert out.report["kills"] == 15 and out.report["kills_outside_rounds"] == 0
    assert out.report["match_spawn_check"]["ok"] and out.report["sides"]["resolved"]
    assert out.link_inputs["eligibility"]["eligible"] is True
    assert [len(out.rounds[n]["kills"]) for n in (1, 2)] == [9, 6]


# ------------------------------------------------------------ player state (P03, CONDENSE 15)


def test_health_and_the_proven_spike_land_in_the_rounds_player_state(tmp_path):
    from replay_synthetic import player_state_events

    match = SyntheticMatch(shape="swiftplay")
    match.extra_events += player_state_events(match)
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf")
    out = run(tmp_path, match, vrf_path=vrf)
    state = fmt.decode_blob(out.encoded_rounds()[1])["player_state"]
    assert state["vitals"] == {"5": [{"t": 5.0, "life": 0, "hp": 55.0, "sh": 0.0, "mhp": 100.0, "msh": 25.0},
                                     {"t": 10.0, "life": 0, "hp": 0.0, "sh": 0.0, "mhp": 100.0, "msh": 25.0}]}
    assert state["damage_taken"] == {"1": [3.25], "2": [4.5], "5": [5.0, 10.0]}
    [bomb] = [u for u in out.rounds[1]["util"] if u.get("kind") == "Bomb"]
    assert state["spike"] == [{"t": -25.0, "s": "unknown"}, {"t": -20.0, "s": "carried"}, {"t": 15.0, "s": "dropped"},
                              {"t": 18.0, "s": "carried", "slot": 6},
                              {"t": 30.0, "s": "planted", "u": bomb["u"], "v": bomb["v"]}, {"t": 39.0, "s": "defused"}]
    assert out.report["player_state"]["vitals: HP section unresolved (owner never died)"] == 2
    assert out.report["player_state"]["spike: planter attributed"] == 1
    assert fmt.decode_blob(out.encoded_rounds()[2])["player_state"] == {"version": 1, "vitals": {},
                                                                         "damage_taken": {}, "spike": []}


def test_without_extras_the_spike_has_no_carrier(tmp_path):
    from replay_synthetic import player_state_events

    match = SyntheticMatch(shape="swiftplay")
    match.extra_events += player_state_events(match)
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf")
    state = run(tmp_path, match, vrf_path=vrf, with_extras=False).rounds[1]["player_state"]
    assert all("slot" not in e and "u" not in e for e in state["spike"])


def test_an_export_with_no_player_state_sources_keeps_the_old_blob_shape(tmp_path):
    blob = run(tmp_path, SyntheticMatch(shape="swiftplay"), vrf_path=SyntheticMatch(shape="swiftplay").write_vrf(
        tmp_path / f"{MATCH_UUID}.vrf")).rounds[1]
    assert "player_state" not in blob
