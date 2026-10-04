"""The standalone preview page with the timing-gaps layer (plan 2026-10-04-timing-gaps-viewer, S4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(WEBAPP / "scripts"))

import render_replay_standalone as standalone  # noqa: E402

from app.replays import format as fmt  # noqa: E402

GAPS_BODY = {
    "status": "ok", "stale": False,
    "chokes": {"7": {"name": "choke-7-marker", "x": 300, "y": 400}},
    "rows": [{"seq": 0, "kind": "predicted", "victim_slot": 0, "t_open": 2.5, "t_close": 4.0, "flicker": False,
              "spot_cell": 10, "victim_cell": 11, "spot_xy": [80, 0], "victim_xy": [88, 0], "released_xy": None,
              "used": None, "merged": 0, "choke_seq": ["7"], "route": [], "candidate_slots": [5],
              "cause": "turned", "cause_detail": {"marker": "gap-row-marker"}}],
}


def _folder(tmp_path: Path, linked: bool = True, gaps: bool = True) -> Path:
    blob = {"v": 1, "round": 1, "map": "Ascent", "hz": 16, "t_start": 0.0, "t_end": 10.0, "players": [],
            "tracks": {}, "alive": {}, "kills": []}
    (tmp_path / "1.json.gz").write_bytes(fmt.encode_blob(blob))
    context = {"match": {"linked": linked, "control": None, "uv_per_unit": 0.75, "rounds": [1], "map": "Ascent"},
               "players": {}, "rounds": {"1": {"db": None, "stats": {}, "kills": {}}}}
    (tmp_path / "context.json").write_text(json.dumps(context), encoding="utf-8")
    if gaps:
        (tmp_path / "1.gaps.json").write_text(json.dumps(GAPS_BODY), encoding="utf-8")
    return tmp_path


def _page(folder: Path) -> str:
    return standalone.render(standalone.load_blobs(folder), standalone.load_site(folder))


def test_load_site_reads_the_gaps_bodies_by_round(tmp_path):
    site = standalone.load_site(_folder(tmp_path))
    assert site["gaps"] == {"1": GAPS_BODY}


def test_a_linked_folder_with_gaps_renders_the_layer_tab_script_and_payload(tmp_path):
    page = _page(_folder(tmp_path))
    assert 'data-replay-layer="gaps"' in page and 'data-replay-tab="gaps"' in page
    assert "data-replay-gaps-list" in page and "data-replay-gaps-legend" in page
    # replay_gaps.js is inlined, and before replay.js
    assert "global.ReplayGaps = api" in page
    assert page.index("global.ReplayGaps = api") < page.index("ReplayViewer.prototype.showRound")
    # the round's body is in the payload, and the bootstrap offers it to the viewer
    data = json.loads(page.split("window.REPLAY_DATA = ", 1)[1].split(";\n", 1)[0])
    assert data["gaps"] == {"1": GAPS_BODY}
    assert "options.gaps = true" in page and "options.loadGaps" in page and '"not_computed"' in page


def test_the_page_reads_a_start_round_and_time_from_the_hash(tmp_path):
    page = _page(_folder(tmp_path))
    assert "window.location.hash" in page and "window.viewer.seek(start.t)" in page


def test_without_gaps_json_the_page_has_no_gaps_layer(tmp_path):
    page = _page(_folder(tmp_path, gaps=False))
    assert 'data-replay-tab="gaps"' not in page and 'data-replay-layer="gaps"' not in page
    assert "global.ReplayGaps = api" not in page
    data = json.loads(page.split("window.REPLAY_DATA = ", 1)[1].split(";\n", 1)[0])
    assert "gaps" not in data


def test_an_unlinked_page_never_offers_gaps(tmp_path):
    folder = _folder(tmp_path, linked=False)
    page = _page(folder)
    assert 'data-replay-tab="gaps"' not in page and "global.ReplayGaps = api" not in page
    assert "gap-row-marker" not in page
    # blobs alone (no site data) neither
    assert 'data-replay-tab="gaps"' not in standalone.render(standalone.load_blobs(folder))
