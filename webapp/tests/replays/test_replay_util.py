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
                                  "unresolved_casters": 1, "unresolved_targets": 1}


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
    assert blob["v"] == 1 and out.recipe.split(".")[1] == f"c{fmt.CONDENSE_REVISION}" == "c4"


def test_the_streaming_loader_keeps_the_util_rows(tmp_path):
    match = SyntheticMatch(shape="swiftplay")
    start = match.round_start(1)
    match.extra_events += [cast(match, start + 5000, 2), hit(match, start + 5900, 6)]
    directory = match.write(tmp_path / "export", sorted(match.events(), key=lambda r: r["time_ms"]))
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf")
    streamed = cd.condense_export_dir(directory, source_sha256=match.source_sha256, vrf_path=vrf)
    in_memory = cd.condense_export_dir(directory, source_sha256=match.source_sha256, vrf_path=vrf, streaming=False)
    assert streamed.encoded_rounds() == in_memory.encoded_rounds() and streamed.rounds[1]["util"]
