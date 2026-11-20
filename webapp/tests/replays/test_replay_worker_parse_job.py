"""replay_worker/parse_job.py: the parse-and-condense step shared by the worker's local path and the parse task."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from replay_worker import parse_job, server  # noqa: E402


def test_server_still_exports_the_moved_names():
    assert server.kill_tree is parse_job.kill_tree
    assert server.parser_detail is parse_job.parser_detail
    assert (server.REASON_TIMEOUT, server.REASON_PARSE) == ("parse timed out", "parse failed")


def test_player_state_diagnostics_survive_worker_result_and_site_storage(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from app.replays.condense import CondensedReplay
    from app.replays.store import _insert
    from app.services.replay_upload import condensed_from_result

    check = {"round": 2, "slot": 0, "t": 10.5, "dtype": "DmgType_BountyHunter_Decay_Nightfall_C",
             "booked": 54.0, "computed": 50.0, "verdict": "too low"}
    diagnostics = {"decay: too low": 1, "decay_checks": [check],
                   "decay_tightest": {check["dtype"]: {k: check[k] for k in ("round", "slot", "t", "booked", "computed")}}}
    replay = CondensedReplay("synthetic-match", "Ascent", "release-11.10", "sha", "recipe", 10, {}, [], {},
                             {"player_state": {**diagnostics, "per_hit": [{"hp": 100}]}})
    out = tmp_path / "export"
    out.mkdir()
    (out / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(parse_job.subprocess, "Popen", lambda *a, **k: Mock(wait=lambda **kw: 0))
    monkeypatch.setattr(parse_job, "condense_export_dir", lambda *a, **k: replay)
    result, _ = parse_job.parse_file(tmp_path / "upload.vrf", tmp_path, "sha", parser_cmd=["parser"],
                                    timeout_s=30, memory_cap_mb=0, parser_build=None)
    assert result["report"]["player_state"] == diagnostics
    restored = condensed_from_result(result)
    assert restored.report["player_state"] == diagnostics
    row = _insert(Mock(), restored, "upload")
    assert row.link_inputs["player_state"] == diagnostics
    assert not out.exists()


def test_a_parser_that_exits_nonzero_is_parse_failed_with_its_last_lines(tmp_path):
    script = tmp_path / "bad_parser.py"
    script.write_text("import sys; print('Reading header'); print('Boom: bad chunk'); sys.exit(3)", encoding="utf-8")
    vrf = tmp_path / "upload.vrf"
    vrf.write_bytes(b"x")
    with pytest.raises(parse_job.ParseFailed) as failed:
        parse_job.parse_file(vrf, tmp_path, "sha", parser_cmd=[sys.executable, str(script), "{vrf}", "{out}"],
                             timeout_s=30, memory_cap_mb=0, parser_build=None)
    assert failed.value.reason == "parse failed" and "Boom: bad chunk" in failed.value.detail
    assert failed.value.seconds is not None                     # today's job records the time of a failed parse


def test_a_parser_that_hangs_is_parse_timed_out(tmp_path):
    script = tmp_path / "slow_parser.py"
    script.write_text("import time; time.sleep(30)", encoding="utf-8")
    vrf = tmp_path / "upload.vrf"
    vrf.write_bytes(b"x")
    with pytest.raises(parse_job.ParseFailed) as failed:
        parse_job.parse_file(vrf, tmp_path, "sha", parser_cmd=[sys.executable, str(script)], timeout_s=1,
                             memory_cap_mb=0, parser_build=None)
    assert failed.value.reason == "parse timed out"


def test_a_failed_parse_leaves_no_export_behind(tmp_path):
    """The export is ~65x the file: one left after a failure fills a Workflow run's disk (2026-10-16)."""
    script = tmp_path / "half_parser.py"
    script.write_text("import sys, pathlib; out = pathlib.Path(sys.argv[1]); out.mkdir(); "
                      "(out / 'chunk.bin').write_bytes(b'x' * 1000); sys.exit(3)", encoding="utf-8")
    vrf = tmp_path / "upload.vrf"
    vrf.write_bytes(b"x")
    with pytest.raises(parse_job.ParseFailed):
        parse_job.parse_file(vrf, tmp_path, "sha", parser_cmd=[sys.executable, str(script), "{out}"],
                             timeout_s=30, memory_cap_mb=0, parser_build=None)
    assert not (tmp_path / "export").exists()
