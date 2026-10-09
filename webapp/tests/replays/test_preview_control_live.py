"""scripts/preview_control_live.py's local `--blobs <dir>` mode (plan 2026-10-06-replay-player-state, P09 amendment
"Previews and refresh"): the round blobs come from `<dir>/<n>.json.gz`, the link data still from the public page.
No network here: the fetch functions, the worker pool, the geometry and the page render are stand-ins."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import preview_control_live as live  # noqa: E402

UUID = "00000000-0000-0000-0000-000000000001"
CTX = {"match": {"map": "Ascent", "side_to_team": {"A": "red", "B": "blue"}, "clock_offset": 0.0},
       "players": {"0": {"side": "A"}, "5": {"side": "B"}},
       "rounds": {"1": {"db_deaths": []}, "2": {"db_deaths": []}}}


class _Pool:
    """multiprocessing.Pool's stand-in: records the tasks, computes nothing."""
    seen: list[dict] = []

    def __init__(self, *_args):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def imap_unordered(self, _fn, tasks):
        for task in tasks:
            _Pool.seen.append(task)
            yield {"key": task["key"], "status": "error", "seconds": 0.0, "error": "stand-in"}


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """Every outside effect replaced; returns the URLs fetch was asked for."""
    from app.control import geometry

    fetched: list[str] = []

    def fake_fetch(url):
        fetched.append(url)
        return b"PUBLIC-" + url.encode()

    _Pool.seen = []
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    monkeypatch.setattr(live, "fetch", fake_fetch)
    monkeypatch.setattr(live, "page_data", lambda uuid: {**CTX, "match": dict(CTX["match"])})
    monkeypatch.setattr(live.multiprocessing, "Pool", _Pool)
    monkeypatch.setattr(geometry, "load_geometry", lambda *a, **k: None)
    monkeypatch.setattr(geometry, "visibility", lambda geo: None)
    monkeypatch.setattr(live.subprocess, "check_call", lambda *a, **k: 0)
    return fetched


def _local(tmp_path: Path, rounds) -> Path:
    folder = tmp_path / "blobs"
    folder.mkdir()
    for n in rounds:
        (folder / f"{n}.json.gz").write_bytes(f"LOCAL-{n}".encode())
    return folder


def _out(tmp_path: Path) -> Path:
    return tmp_path / "temp" / "valo-replay" / f"{UUID}-t"


def test_blobs_mode_reads_local_rounds_and_never_fetches_a_blob(offline, tmp_path):
    folder = _local(tmp_path, (1, 2))
    assert live.main([UUID, "1", "2", "--tag", "t", "--blobs", str(folder)]) == 0
    assert offline == [], "the link data comes from page_data; no round blob is fetched"
    assert {t["key"]: t["blob"] for t in _Pool.seen} == {1: b"LOCAL-1", 2: b"LOCAL-2"}
    assert (_out(tmp_path) / "1.json.gz").read_bytes() == b"LOCAL-1"
    assert (_out(tmp_path) / "2.json.gz").read_bytes() == b"LOCAL-2"


def test_blobs_mode_refuses_a_missing_local_round(offline, tmp_path):
    folder = _local(tmp_path, (1,))
    with pytest.raises(SystemExit) as refused:
        live.main([UUID, "1", "2", "--tag", "t", "--blobs", str(folder)])
    assert "2.json.gz" in str(refused.value)
    assert offline == [] and _Pool.seen == [], "no silent fallback to the public blob"


def test_without_the_flag_the_public_blobs_are_fetched(offline, tmp_path):
    assert live.main([UUID, "1", "--tag", "t"]) == 0
    assert offline == [f"{live.SITE}/replays/{UUID}/1.json"]
    assert _Pool.seen[0]["blob"] == f"PUBLIC-{live.SITE}/replays/{UUID}/1.json".encode()
    assert (_out(tmp_path) / "1.json.gz").read_bytes() == _Pool.seen[0]["blob"]
