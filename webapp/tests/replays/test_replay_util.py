"""W-e: flash and nearsight utility events, casters and targets resolved through the pawns."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import MATCH_UUID, SyntheticMatch  # noqa: E402

from app.replays import condense as cd  # noqa: E402
from app.replays import format as fmt  # noqa: E402
from app.replays.contract import ContractError  # noqa: E402


def cast(match, t, caster_slot=None, *, kind="flash", actor=6168, caster_state=None, x=-1800.0, y=500.0):
    key = "flash" if kind == "flash" else "nearsight"
    return {"type": f"valorant_{key}_cast", "time_ms": t, f"{key}_actor_net_guid": actor,
            f"{key}_kind": "kayo_flash_drive_underhand" if kind == "flash" else "reyna_leer",
            "caster_character_net_guid": match.pawn(1, caster_slot) if caster_slot is not None else 777777,
            "caster_player_state_net_guid": caster_state, "caster_subject": None,
            "location": {"x": x, "y": y, "z": 100.0}}


def hit(match, t, target_slot=None, *, kind="flash", actor=6168, target_state=None):
    key = "flash" if kind == "flash" else "nearsight"
    return {"type": f"valorant_{key}_player_hit", "time_ms": t, f"{key}_actor_net_guid": actor,
            "target_character_net_guid": match.pawn(1, target_slot) if target_slot is not None else 888888,
            "target_player_state_net_guid": target_state, "target_subject": None}


def run(tmp_path, match):
    directory = match.write(tmp_path / "export", sorted(match.events(), key=lambda r: r["time_ms"]))
    return cd.condense_export_dir(directory, source_sha256=match.source_sha256,
                                  vrf_path=match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf"))


def test_a_flash_with_two_hits(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    match.extra_events += [cast(match, start + 5000, 2), hit(match, start + 5900, 6), hit(match, start + 6000, 7),
                           hit(match, start + 6000, 7)]
    out = run(tmp_path, match)
    [entry] = out.rounds[1]["util"]
    assert (entry["k"], entry["t"], entry["by"], entry["targets"]) == ("flash", 5.0, 2, [6, 7])
    assert entry["ability"] == "kayo_flash_drive_underhand" and 0 <= entry["u"] <= 10000 and 0 <= entry["v"] <= 10000
    assert out.rounds[2]["util"] == []
    assert out.report["util"]["flash"] == 1 and out.report["util"]["hits"] == 3


def test_a_nearsight_and_the_round_it_belongs_to(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(2)
    match.extra_events += [cast(match, start + 12_000, 8, kind="nearsight", actor=8900),
                           hit(match, start + 13_000, 1, kind="nearsight", actor=8900)]
    out = run(tmp_path, match)
    assert [(u["k"], u["by"], u["targets"]) for u in out.rounds[2]["util"]] == [("nearsight", 8, [1])]
    assert out.rounds[1]["util"] == [] and out.report["util"]["nearsight"] == 1


def test_a_caster_known_only_by_player_state_resolves(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    match.extra_events.append(cast(match, start + 5000, None, caster_state=203))  # player state of slot 3
    assert [u["by"] for u in run(tmp_path, match).rounds[1]["util"]] == [3]


def test_unresolved_casters_are_dropped_and_unresolved_targets_left_out(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    match.extra_events += [cast(match, start + 5000, None, actor=1), hit(match, start + 5500, 5, actor=1),
                           cast(match, start + 7000, 1, actor=2), hit(match, start + 7500, None, actor=2),
                           hit(match, start + 7600, 6, actor=99)]
    out = run(tmp_path, match)
    assert [(u["by"], u["targets"]) for u in out.rounds[1]["util"]] == [(1, [])]
    assert out.report["util"] == {"flash": 1, "hits": 2, "orphan_hits": 1, "outside_rounds": 0,
                                  "unresolved_casters": 1, "unresolved_targets": 1,
                                  # 2026-10-05: no explosion row, so nothing proves the kept flash went off
                                  "flash_activation_unknown": 1, "flash_targets_complete": 0}


def test_a_cast_by_a_player_who_left_refuses(tmp_path):
    match = SyntheticMatch(shape="swiftplay", kills={1: [(10.0, 0, 5)], 2: [(8.0, 5, 0)], 3: [(20.0, 4, 8)]},
                           leaver=(9, SyntheticMatch().round_start(3) + 3000))
    match.extra_events.append(cast(match, match.round_start(3) + 10_000, 9))
    with pytest.raises(ContractError) as refused:
        run(tmp_path, match)
    assert refused.value.reason == "lifecycle" and "flash by slot 9" in refused.value.detail


def test_util_reaches_readers_through_known_kinds_and_holds_no_identity(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    match.extra_events += [cast(match, start + 5000, 2), hit(match, start + 5900, 6)]
    out = run(tmp_path, match)
    blob = fmt.decode_blob(out.encoded_rounds()[1])
    assert [u["k"] for u in fmt.known_util(blob, frozenset({"flash", "nearsight"}))] == ["flash"]
    assert fmt.known_util(blob) == [], "a reader that knows no kinds ignores them"
    text = json.dumps(blob)
    assert "subject" not in text.lower() and "00000000-0000-4000-8000-0000000000" not in text
    assert blob["v"] == 1 and out.recipe.split(".")[1] == f"c{fmt.CONDENSE_REVISION}" == "c15"


def test_hits_carry_their_own_time_and_duration(tmp_path):
    # Map control's input (revision 10): the blind starts at the hit, not the cast, and lasts the
    # victim's own recorded time; a nearsight lasting until removed has no duration.
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    flash_hit = {**hit(match, start + 5900, 6), "initial_duration_seconds": 1.4039224}
    weak_hit = {**hit(match, start + 5900, 7), "initial_duration_seconds": 0.045}
    leer = {**hit(match, start + 9000, 8, kind="nearsight", actor=8900), "configured_duration_seconds": None,
            "duration_until_removed": True}
    paranoia = {**hit(match, start + 9500, 9, kind="nearsight", actor=8900), "configured_duration_seconds": 2}
    match.extra_events += [cast(match, start + 5000, 2), flash_hit, weak_hit,
                           cast(match, start + 8000, 3, kind="nearsight", actor=8900), leer, paranoia]
    flash, nearsight = run(tmp_path, match).rounds[1]["util"]
    assert flash["hits"] == [[6, 5.9, 1.404], [7, 5.9, 0.045]] and flash["targets"] == [6, 7]
    assert nearsight["hits"] == [[8, 9.0, None], [9, 9.5, 2.0]]


def test_the_streaming_loader_keeps_the_util_rows(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    match.extra_events += [cast(match, start + 5000, 2), hit(match, start + 5900, 6)]
    directory = match.write(tmp_path / "export", sorted(match.events(), key=lambda r: r["time_ms"]))
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf")
    streamed = cd.condense_export_dir(directory, source_sha256=match.source_sha256, vrf_path=vrf)
    in_memory = cd.condense_export_dir(directory, source_sha256=match.source_sha256, vrf_path=vrf, streaming=False)
    assert streamed.encoded_rounds() == in_memory.encoded_rounds() and streamed.rounds[1]["util"]


# ---------------------------------------------------------------- explosions, paths and activation (2026-10-05)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "replays" / "utility"


def sample(t, source, x, y, z, actor=6168):
    return {"type": "valorant_flash_path_updated", "time_ms": t, "flash_actor_net_guid": actor,
            "flash_kind": "phoenix_curveball_right", "source": source, "location": {"x": x, "y": y, "z": z}}


def exploded(t, evidence, x, y, z, actor=6168):
    return {"type": "valorant_flash_exploded", "time_ms": t, "flash_actor_net_guid": actor,
            "flash_kind": "phoenix_curveball_right", "location": {"x": x, "y": y, "z": z}, "evidence": evidence}


def flash_of(tmp_path, match, events, round_number=1):
    match.extra_events += events
    return [u for u in run(tmp_path, match).rounds[round_number]["util"] if u["k"] == "flash"]


def test_a_real_curveballs_rows_keep_their_shape():
    """The synthetic rows below are shaped like the real ones: the fixture's own sources, evidence and the
    factor of 100 between its two samples of one instant."""
    rows = [json.loads(line) for line in (FIXTURES / "phoenix_curveball.ndjson").read_text(encoding="utf-8").splitlines()]
    spawn, replicated = [r for r in rows if r["type"] == "valorant_flash_path_updated"][:2]
    assert (spawn["source"], replicated["source"]) == ("spawn_transform", "replicated_movement")
    assert spawn["location"]["x"] == pytest.approx(replicated["location"]["x"] * 100, abs=1)
    pop = next(r for r in rows if r["type"] == "valorant_flash_exploded")
    assert pop["evidence"] == "stop_projectile_rpc" and pop["location"] == replicated["location"]
    assert pop["time_ms"] - replicated["time_ms"] > cd.POP_FRESH_MS       # its place is its cast, 0.6 s stale


def test_a_stale_copied_sample_gives_the_explosion_a_time_and_no_place(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    [flash] = flash_of(tmp_path, match, [
        cast(match, start + 5000, 2, x=-1800.0, y=500.0),
        sample(start + 5000, "spawn_transform", -1800.0, 500.0, 265.2),
        sample(start + 5000, "replicated_movement", -18.0, 5.0, 2.65),
        exploded(start + 5600, "stop_projectile_rpc", -18.0, 5.0, 2.65)])
    assert flash["pop"] == {"t": 5.6, "place": "stale", "age": 0.6}             # never the cast point as the pop
    assert flash["activation"] == {"state": "completed", "evidence": "stop_projectile_rpc",
                                   "targets_complete": True, "diagnostics": []}
    assert (flash["id"], flash["z"], flash["targets"], flash["hits"]) == (6168, 10, [], [])
    # both samples of the cast instant are the same place once each is read in its own unit
    assert flash["path"][0][1:3] == flash["path"][1][1:3] == [flash["u"], flash["v"]]
    assert flash["path"][0][3] == 27 and flash["path"][1][3] == 26              # 265.2 and 2.65 * 100, in dm


def test_a_fresh_replicated_sample_places_the_explosion_in_metres_times_100(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    [flash] = flash_of(tmp_path, match, [
        cast(match, start + 5000, 2, x=-1800.0, y=500.0),
        sample(start + 5000, "spawn_transform", -1800.0, 500.0, 100.0),
        sample(start + 5400, "replicated_movement", -10.0, 12.0, 3.0),
        sample(start + 5900, "replicated_movement", -4.0, 20.0, 3.5),
        exploded(start + 6000, "stop_projectile_rpc", -4.0, 20.0, 3.5)])
    game_map = cd.load_maps()[run_map(tmp_path)]
    assert flash["pop"] == {"t": 6.0, "u": game_map.to_uv(-400.0, 2000.0)[0], "v": game_map.to_uv(-400.0, 2000.0)[1],
                            "z": 35, "src": "replicated_movement", "age": 0.1}
    assert [p[0] for p in flash["path"]] == [5.0, 5.4, 5.9]
    assert flash["path"][-1][1:] == [*game_map.to_uv(-400.0, 2000.0), 35]


def run_map(tmp_path) -> str:
    match = SyntheticMatch(shape="swiftplay")
    return run(tmp_path / "map", match).map_name


def test_a_flash_source_explosion_is_in_world_units_and_short_hits_are_kept(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    hits = [{**hit(match, start + 6300, 6), "initial_duration_seconds": 1.9958553, "correlation": "causing_flash_source"},
            {**hit(match, start + 6300, 7), "initial_duration_seconds": 0.0477972, "correlation": "causing_flash_source"},
            {**hit(match, start + 6300, 8), "initial_duration_seconds": 0.0, "correlation": "causing_flash_source"}]
    [flash] = flash_of(tmp_path, match, [cast(match, start + 5000, 2),
                                         exploded(start + 6000, "skye_flash_source", -900.0, 700.0, 1097.1), *hits])
    game_map = cd.load_maps()[run_map(tmp_path)]
    assert flash["pop"] == {"t": 6.0, "u": game_map.to_uv(-900.0, 700.0)[0], "v": game_map.to_uv(-900.0, 700.0)[1],
                            "z": 110, "src": "flash_source"}
    assert flash["hits"] == [[6, 6.3, 1.996], [7, 6.3, 0.048], [8, 6.3, 0.0]]   # a zero-length blind is still a hit
    assert flash["activation"]["state"] == "completed" and flash["activation"]["targets_complete"] is True


@pytest.mark.parametrize("extra, diagnostics", [
    ([], ["no_explosion_row"]),
    ([exploded(0, "actor_destroyed_fallback", -18.0, 5.0, 2.65)], ["explosion_evidence_actor_destroyed_fallback"]),
])
def test_without_proof_it_went_off_a_flash_is_unknown_and_never_a_proven_empty(tmp_path, extra, diagnostics):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    events = [cast(match, start + 5000, 2)] + [{**e, "time_ms": start + 5600} for e in extra]
    [flash] = flash_of(tmp_path, match, events)
    assert flash["activation"]["state"] == "unknown"
    assert flash["activation"]["targets_complete"] is False
    assert flash["activation"]["diagnostics"] == diagnostics
    assert flash["targets"] == []          # the same empty list a proven empty has: only `activation` tells them apart


def test_a_hit_that_names_no_cast_near_the_explosion_makes_the_targets_incomplete(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    loose = {**hit(match, start + 6400, 6, actor=0), "flash_actor_net_guid": None, "correlation": "unresolved"}
    late = {**hit(match, start + 12_500, 6, actor=0), "flash_actor_net_guid": None, "correlation": "unresolved"}
    first, second = flash_of(tmp_path, match, [
        cast(match, start + 5000, 2), exploded(start + 6000, "skye_flash_source", 0.0, 0.0, 0.0), loose,
        cast(match, start + 10_000, 2, actor=7000), exploded(start + 11_000, "skye_flash_source", 0.0, 0.0, 0.0, actor=7000),
        late])
    assert first["activation"]["targets_complete"] is False
    assert first["activation"]["diagnostics"] == ["unattributed_hit_in_window"] and first["targets"] == []
    assert second["activation"]["targets_complete"] is True, "1.5 s after the pop is outside the window"


def test_an_unresolved_target_makes_the_targets_incomplete(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    [flash] = flash_of(tmp_path, match, [cast(match, start + 5000, 2),
                                         exploded(start + 6000, "skye_flash_source", 0.0, 0.0, 0.0),
                                         hit(match, start + 6200, None), hit(match, start + 6200, 7)])
    assert flash["targets"] == [7]         # a positive hit stands whatever else is missing
    assert flash["activation"]["targets_complete"] is False
    assert flash["activation"]["diagnostics"] == ["unresolved_target"]


def test_a_sample_from_an_unknown_source_is_left_out_and_counted(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    match.extra_events += [cast(match, start + 5000, 2), sample(start + 5000, "spawn_transform", -1800.0, 500.0, 100.0),
                           sample(start + 5300, "predicted", -9.0, 9.0, 1.0),
                           exploded(start + 5600, "stop_projectile_rpc", -9.0, 9.0, 1.0)]
    out = run(tmp_path, match)
    [flash] = out.rounds[1]["util"]
    assert "path" not in flash, "one known sample is a point, not a flight"
    assert flash["pop"] == {"t": 5.6, "place": "unverified"}        # no sample of a known unit matches it
    assert out.report["util"]["path_samples_without_a_known_source"] == 1
    assert out.report["util"]["flash_pops_unverified"] == 1


def test_an_actor_guid_used_again_in_a_later_round_keeps_each_casts_own_rows(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    one, two = match.round_start(1), match.round_start(2)
    match.extra_events += [
        cast(match, one + 5000, 2), exploded(one + 5600, "skye_flash_source", 100.0, 100.0, 0.0), hit(match, one + 5900, 6),
        cast(match, two + 3000, 3), exploded(two + 3500, "skye_flash_source", 900.0, 900.0, 0.0),
        hit(match, two + 3700, 7), hit(match, two + 3700, 8)]
    out = run(tmp_path, match)
    [first], [second] = out.rounds[1]["util"], out.rounds[2]["util"]
    assert (first["by"], first["targets"], first["pop"]["t"]) == (2, [6], 5.6)
    assert (second["by"], second["targets"], second["pop"]["t"]) == (3, [7, 8], 3.5)
    assert first["id"] == second["id"] == 6168 and first["pop"]["u"] != second["pop"]["u"]


def test_explosions_and_paths_reach_the_streaming_reader_too(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    match.extra_events += [cast(match, start + 5000, 2), sample(start + 5000, "spawn_transform", -1800.0, 500.0, 100.0),
                           sample(start + 5500, "replicated_movement", -10.0, 12.0, 3.0),
                           exploded(start + 5600, "stop_projectile_rpc", -10.0, 12.0, 3.0), hit(match, start + 5900, 6)]
    directory = match.write(tmp_path / "export", sorted(match.events(), key=lambda r: r["time_ms"]))
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf")
    streamed = cd.condense_export_dir(directory, source_sha256=match.source_sha256, vrf_path=vrf)
    in_memory = cd.condense_export_dir(directory, source_sha256=match.source_sha256, vrf_path=vrf, streaming=False)
    [flash] = streamed.rounds[1]["util"]
    assert flash["pop"]["src"] == "replicated_movement" and len(flash["path"]) == 2      # populated, not two empties
    assert streamed.encoded_rounds() == in_memory.encoded_rounds()
    from app.replays import contract

    assert {"valorant_flash_exploded", "valorant_flash_path_updated"} <= contract.STREAM_EVENT_TYPES
    assert all(f'"{name}"' in contract._NEEDLES for name in ("valorant_flash_exploded", "valorant_flash_path_updated"))


def test_a_nearsight_row_is_exactly_as_before(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    match.extra_events += [cast(match, start + 8000, 3, kind="nearsight", actor=8900),
                           hit(match, start + 9000, 8, kind="nearsight", actor=8900)]
    [row] = run(tmp_path, match).rounds[1]["util"]
    assert sorted(row) == ["ability", "by", "hits", "k", "t", "targets", "u", "v"]
