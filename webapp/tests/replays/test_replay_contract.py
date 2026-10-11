"""The exporter contract: manifest checks, diagnostic classes, row order and folding."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_synthetic import BUILD, PIN_COMMIT, SyntheticMatch  # noqa: E402

from app.replays import contract  # noqa: E402
from app.replays.contract import ContractError  # noqa: E402

PIN = contract.load_pin()


def _manifest(**extra):
    return SyntheticMatch(manifest_extra=extra).manifest()


def test_the_committed_pin_matches_the_parser_commit():
    assert PIN.commit == PIN_COMMIT
    assert PIN.schema_version == 8
    assert BUILD in PIN.supported_builds


def test_a_good_manifest_passes():
    match = SyntheticMatch()
    contract.check_manifest(match.manifest(), PIN, match.source_sha256)


@pytest.mark.parametrize("extra, reason", [
    ({"schema_version": 7}, "schema_version"),
    ({"source_sha256": "0" * 64}, "source_sha256"),
    ({"replay_build": "++Ares-Core+release-13.07"}, "replay_build"),
    ({"parser_version": "1.0.0+deadbeefdeadbeef"}, "parser_version"),
])
def test_each_manifest_mismatch_refuses(extra, reason):
    with pytest.raises(ContractError) as refused:
        contract.check_manifest(_manifest(**extra), PIN, SyntheticMatch().source_sha256)
    assert refused.value.reason == reason


def test_the_source_check_can_be_skipped_only_explicitly():
    contract.check_manifest(_manifest(source_sha256="0" * 64), PIN, None)


def test_build_json_must_match_the_pin():
    good = {"commit": PIN.commit, "patch_hash": PIN.patch_hash}
    contract.check_manifest(_manifest(), PIN, None, good)
    for bad in ({**good, "commit": "0" * 40}, {**good, "patch_hash": "0" * 64}):
        with pytest.raises(ContractError) as refused:
            contract.check_manifest(_manifest(), PIN, None, bad)
        assert refused.value.reason == "parser_build"


def test_patch_hash_matches_the_powershell_recipe():
    # build_replay_parser.ps1 hashes "<file>\n<find>\n<replace>\n" per patch, UTF-8.
    import hashlib
    p = PIN.patches[0]
    text = f"{p['file']}\n{p['find']}\n{p['replace']}\n"
    text += ''.join(f"{s['file']}\n{s['sha256']}\n" for s in PIN.source_patches)
    assert PIN.patch_hash == hashlib.sha256(text.encode()).hexdigest()


def test_source_patch_must_ship_with_the_pin_and_match_its_digest(tmp_path):
    import shutil
    pin_path = tmp_path / 'replay_parser.json'
    shutil.copyfile(contract.PIN_FILE, pin_path)
    for patch in PIN.source_patches:
        destination = tmp_path / patch['file']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(contract.PIN_FILE.parent / patch['file'], destination)
    assert contract.load_pin(pin_path).patch_hash == PIN.patch_hash
    destination.write_bytes(destination.read_bytes() + b'changed\n')
    with pytest.raises(ContractError, match='source patch digest'):
        contract.load_pin(pin_path)


def test_diagnostic_classes():
    report = contract.classify_diagnostics(_manifest(diagnostics=[
        {"code": "raw_payload_fallback", "export_group_path": contract.GAME_STATE_PATH, "field_name": "RoundResults"},
        {"code": "raw_payload_fallback", "export_group_path": contract.GAME_STATE_PATH, "field_name": "TeamEconomy"},
        {"code": "partial_sequence_error"},
        {"code": "raw_payload_fallback", "export_group_path": contract.PLAYER_STATE_PATH, "field_name": "UniqueId"},
        {"code": "something_new"},
    ]))
    assert report.link_blocking == [f"raw_payload_fallback on {contract.GAME_STATE_PATH}.RoundResults"]
    assert report.ignored == {"raw_payload_fallback": 1, "partial_sequence_error": 1}
    assert len(report.blocking) == 2
    assert any("BombPlayerState" in item for item in report.blocking)
    assert any("unclassified diagnostic something_new" in item for item in report.blocking)


@pytest.mark.parametrize("extra", [
    {"parse_status": "failed"},
    {"stats": {"malformed_packet_count": contract.MAX_MALFORMED_PACKETS + 1}},
    {"suppressed_diagnostic_count": 3},
])
def test_blocking_manifest_states(extra):
    assert contract.classify_diagnostics(_manifest(**extra)).blocking


def test_completed_with_warnings_is_not_blocking_by_itself():
    assert contract.classify_diagnostics(_manifest(parse_status="completed_with_warnings")).blocking == []


def test_partial_bunch_errors_are_coverage_not_blocking():
    # The first real export: 76 partial_sequence_errors spread over the recorder's and object channels.
    report = contract.classify_diagnostics(_manifest(
        parse_status="completed_with_warnings", stats={"partial_error_count": 3},
        diagnostics=[{"code": "partial_sequence_error", "channel_index": 1},
                     {"code": "partial_sequence_error", "channel_index": 1},
                     {"code": "incomplete_partial_bunch", "channel_index": 74}]))
    assert report.blocking == []
    assert report.channels == {1: 2, 74: 1}
    assert report.coverage == ["3 partial-bunch errors on 2 channels (see the coverage checks)"]


def test_map_codes_come_from_the_vrf_bytes(tmp_path):
    vrf = tmp_path / "x.vrf"
    vrf.write_bytes(b"\x00junk/Game/Maps/Infinity/Infinity\x00/Game/Maps/Infinity_Barriers\x00"
                    b"/Game/Maps/Infinity/Infinity.Infinity\x00")
    assert contract.map_codes_in_file(vrf) == {"Infinity": 2}


def test_rows_sort_by_time_and_keep_file_order_on_ties(tmp_path):
    path = tmp_path / "events.ndjson"
    rows = [{"time_ms": 20, "n": "a"}, {"time_ms": 10, "n": "b"}, {"time_ms": 20, "n": "c"}, {"time_ms": 10, "n": "d"}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n")
    assert [r.data["n"] for r in contract.read_ndjson(path)] == ["b", "d", "a", "c"]


def test_partial_updates_fold_per_actor():
    rows = [
        contract.Row(0, 10, {"actor_net_guid": 1, "payload": {"Subject": "s1", "SpawnedCharacter": 7}}),
        contract.Row(1, 20, {"actor_net_guid": 2, "payload": {"Subject": "s2"}}),
        contract.Row(2, 30, {"actor_net_guid": 1, "payload": {"SpawnedCharacter": 8}}),
        contract.Row(3, 40, {"actor_net_guid": 1, "payload": None}),
    ]
    folded = contract.fold_export_groups(iter(rows))
    assert folded[1].values == {"Subject": "s1", "SpawnedCharacter": 8}
    assert folded[1].history["SpawnedCharacter"] == [(10, 7), (30, 8)]
    assert folded[2].values == {"Subject": "s2"}


def test_an_incomplete_export_refuses(tmp_path):
    (tmp_path / "manifest.json").write_text("{}")
    with pytest.raises(ContractError) as refused:
        contract.load_export(tmp_path)
    assert refused.value.reason == "incomplete_export"


def test_current_recipe_is_the_string_the_condenser_stamps(tmp_path):
    from app.replays import format as fmt
    from app.replays.condense import condense_export_dir

    match = SyntheticMatch(shape="swiftplay")
    replay = condense_export_dir(match.write(tmp_path / "export"), source_sha256=match.source_sha256,
                                 vrf_path=match.write_vrf(tmp_path / "match.vrf"), check_file_name=False)
    assert contract.current_recipe() == replay.recipe
    assert contract.current_recipe(PIN, fmt.STATIC_DIR) == fmt.recipe(PIN.commit, fmt.assets_revision())
    other = contract.ParserPin("f" * 40, PIN.schema_version, PIN.supported_builds, PIN.patches)
    assert contract.current_recipe(other) != contract.current_recipe(), "another parser commit, another recipe"
