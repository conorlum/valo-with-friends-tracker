"""The standalone preview page with the Critical Moments tab (plan 2026-10-18-critical-moments-impl, W16)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(WEBAPP / "scripts"))

import render_replay_standalone as standalone  # noqa: E402

from app.replays import format as fmt  # noqa: E402

MOMENTS_BODY = {"status": "ok", "stale": False, "players": {"0": {"name": "slot-0-marker", "team": "Red"}},
                "attack_round1": [0], "rounds": {}, "summary": {}}


def _folder(tmp_path: Path, control: bool = True, moments: bool = True) -> Path:
    blob = {"v": 1, "round": 1, "map": "Ascent", "hz": 16, "t_start": 0.0, "t_end": 10.0, "players": [],
            "tracks": {}, "alive": {}, "kills": []}
    (tmp_path / "1.json.gz").write_bytes(fmt.encode_blob(blob))
    context = {"match": {"linked": True, "control": {"revision": 1} if control else None, "uv_per_unit": 0.75,
                         "rounds": [1], "map": "Ascent"},
               "players": {}, "rounds": {"1": {"db": None, "stats": {}, "kills": {}}}}
    (tmp_path / "context.json").write_text(json.dumps(context), encoding="utf-8")
    if moments:
        (tmp_path / "moments.json").write_text(json.dumps(MOMENTS_BODY), encoding="utf-8")
    return tmp_path


def _page(folder: Path) -> str:
    return standalone.render(standalone.load_blobs(folder), standalone.load_site(folder))


def _data(page: str) -> dict:
    return json.loads(page.split("window.REPLAY_DATA = ", 1)[1].split(";\n", 1)[0])


def test_load_site_reads_the_moments_body(tmp_path):
    assert standalone.load_site(_folder(tmp_path))["moments"] == MOMENTS_BODY


def test_a_control_folder_with_moments_renders_the_tab_script_and_payload(tmp_path):
    page = _page(_folder(tmp_path))
    assert 'data-replay-tab="moments"' in page and "data-replay-moments" in page
    assert "global.ReplayMoments = api" in page
    assert _data(page)["moments"] == MOMENTS_BODY
    assert "options.loadMoments" in page and "options.momentsViewerSlot" in page


def test_the_hash_can_open_the_moments_tab_for_a_slot(tmp_path):
    page = _page(_folder(tmp_path))
    # "#tab=moments&slot=N": the slot is numeric (start.slot), the tab name is not
    assert "start.slot" in page and 'openTab === "moments"' in page


def test_no_moments_file_leaves_the_script_and_payload_out(tmp_path):
    page = _page(_folder(tmp_path, moments=False))
    # (the bootstrap's loadMoments is behind `data.moments`, so with neither it offers no tab data)
    assert "global.ReplayMoments = api" not in page and "moments" not in _data(page)


def test_moments_need_map_control(tmp_path):
    page = _page(_folder(tmp_path, control=False))
    assert "global.ReplayMoments = api" not in page and "moments" not in _data(page)
