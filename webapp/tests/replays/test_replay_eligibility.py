"""P-f: link eligibility is decided at condense, carried in link_inputs, and enforced by the
linker and `--dry-run` (before any DB read), not only shown in the preview."""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import ingest_replay  # noqa: E402
from replay_synthetic import MATCH_UUID, SyntheticMatch  # noqa: E402

from app.replays import condense as cd  # noqa: E402
from app.replays import link as lk  # noqa: E402
from app.replays.contract import GAME_STATE_PATH, load_pin  # noqa: E402

FALLBACK = {"code": "raw_payload_fallback", "export_group_path": GAME_STATE_PATH, "field_name": "RoundResults"}


def partial(channel: int) -> dict:
    return {"code": "partial_sequence_error", "channel_index": channel, "export_group_path": None}


def condense(tmp_path, match, events=None, movement=None):
    directory = match.write(tmp_path / "export", events, movement)
    vrf = match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf") if match.shape == "swiftplay" else None
    return cd.condense_export_dir(directory, source_sha256=match.source_sha256, vrf_path=vrf)


def eligibility(replay) -> dict:
    return replay.link_inputs["eligibility"]


def link_view(replay) -> lk.ReplayLinkView:
    return lk.ReplayLinkView(replay.match_uuid, replay.round_count, replay.report["dropped_final_round"],
                             replay.players, replay.link_inputs)


def test_a_clean_replay_is_eligible(tmp_path):
    record = eligibility(condense(tmp_path, SyntheticMatch()))
    assert record["eligible"] is True and record["reasons"] == []
    assert record["coverage_ok"] and record["min_coverage"] >= cd.MIN_ALIVE_COVERAGE
    assert record["kills_outside_rounds"] == 0 and record["phase_cycle_ok"] and record["lifecycle_ok"]


def test_a_manifest_only_round_results_fallback_makes_it_ineligible(tmp_path):
    # The fold saw nothing wrong; the manifest diagnostic alone blocks the link.
    replay = condense(tmp_path, SyntheticMatch(manifest_extra={"diagnostics": [FALLBACK]}))
    assert replay.link_inputs["round_results_fallback"] is False
    record = eligibility(replay)
    assert record["eligible"] is False and record["link_blocking"]
    result = lk.link(link_view(replay), [], {})
    assert result.status == "unlinked", "no DB match: the linker never gets to eligibility"


def test_the_linker_refuses_an_ineligible_replay_even_with_its_match(tmp_path):
    replay = condense(tmp_path, SyntheticMatch(manifest_extra={"diagnostics": [FALLBACK]}))
    match = lk.DbMatch(1, MATCH_UUID, 2, 1, [], [], [])
    result = lk.link(link_view(replay), [match], {})
    assert (result.status, result.report["check"]) == ("refused", "eligibility")


def _thinned(match: SyntheticMatch, slot: int, keep_until_s: float, resume_s: float | None = None):
    start = match.round_start(1)
    out = []
    for row in match.movement():
        mine = row["shooter_character_net_guid"] == match.pawn(1, slot) and start <= row["time_ms"] <= start + 50_000
        seconds = (row["time_ms"] - start) / 1000
        if mine and seconds > keep_until_s and (resume_s is None or seconds < resume_s):
            continue
        out.append(row)
    return out


def test_coverage_cut_to_52_percent_is_ineligible(tmp_path):
    match = SyntheticMatch()
    record = eligibility(condense(tmp_path, match, movement=_thinned(match, 0, 26.0)))
    assert record["eligible"] is False and record["coverage_ok"] is False
    assert record["min_coverage"] == pytest.approx(0.52, abs=0.01)
    assert any(reason.startswith("coverage below") for reason in record["reasons"])


def test_a_15_second_gap_is_ineligible(tmp_path):
    match = SyntheticMatch()
    record = eligibility(condense(tmp_path, match, movement=_thinned(match, 0, 10.0, 25.0)))
    assert record["eligible"] is False and record["max_gap_s"] == pytest.approx(15.0, abs=0.1)
    assert any("track gap" in reason for reason in record["reasons"])


def test_partial_errors_on_the_phase_channel_pass_when_the_cross_check_ran(tmp_path):
    match = SyntheticMatch(shape="swiftplay", manifest_extra={"diagnostics": [partial(1)] * 3})
    record = eligibility(condense(tmp_path, match))
    assert record["eligible"] is True
    assert record["partial_errors"] == {"1": {"count": 3, "class": "phase_validated"}}


def test_partial_errors_on_the_phase_channel_block_without_the_cross_check(tmp_path):
    match = SyntheticMatch(shape="swiftplay", manifest_extra={"diagnostics": [partial(1)]})
    events = [row for row in match.events() if row.get("function_name") != "ClientGamePhaseEnded"]
    record = eligibility(condense(tmp_path, match, events=events))
    assert record["eligible"] is False and record["phase_ended_checked"] is False
    assert record["partial_errors"]["1"]["class"] == "phase_unvalidated"


def test_partial_error_classes_follow_the_evidence_on_each_channel(tmp_path):
    # Channel 10 carries slot 0's pawn (lifecycle) and its kills; channel 99 carries nothing we read.
    match = SyntheticMatch(shape="swiftplay", manifest_extra={"diagnostics": [partial(10), partial(99)]})
    record = eligibility(condense(tmp_path, match))
    assert record["eligible"] is True
    assert record["partial_errors"] == {"10": {"count": 1, "class": "lifecycle_validated"},
                                        "99": {"count": 1, "class": "ignored"}}


def test_movement_only_channels_are_left_to_the_coverage_limits(tmp_path):
    match = SyntheticMatch(shape="swiftplay", manifest_extra={"diagnostics": [partial(77)]})
    movement = match.movement()
    for row in movement:
        row["channel"] = 77
    record = eligibility(condense(tmp_path, match, movement=movement))
    assert record["partial_errors"]["77"]["class"] == "movement_coverage" and record["eligible"] is True


# ------------------------------------------------------------ --dry-run refuses before any DB read


@pytest.mark.parametrize("case", ["fallback", "coverage", "gap"])
def test_dry_run_refuses_an_ineligible_replay_without_loading_a_row(tmp_path, capsys, case):
    match = SyntheticMatch(manifest_extra={"diagnostics": [FALLBACK]} if case == "fallback" else {})
    movement = {"fallback": None, "coverage": _thinned(match, 0, 26.0), "gap": _thinned(match, 0, 10.0, 25.0)}[case]
    directory = match.write(tmp_path / "export", movement=movement)
    vrf = tmp_path / "archive" / f"{MATCH_UUID}.vrf"
    vrf.parent.mkdir()
    vrf.write_bytes(match.vrf_bytes)
    parser_dir = tmp_path / "parser"
    (parser_dir / "bin").mkdir(parents=True)
    pin = load_pin()
    (parser_dir / "bin" / "BUILD.json").write_text(json.dumps({"commit": pin.commit, "patch_hash": pin.patch_hash}))
    code = ingest_replay.main(["--export-dir", str(directory), "--dry-run", "--vrf", str(vrf),
                               "--parser-dir", str(parser_dir)], loader_factory=lambda: pytest.fail("loaded"))
    printed = capsys.readouterr().out
    assert code == 1
    body = json.loads(printed[printed.index("{"):])
    assert body["status"] == "refused" and body["report"]["check"] == "eligibility"
