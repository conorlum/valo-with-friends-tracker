"""Gap view rows (timing-gaps viewer plan, section 2): pixel positions, choke points, used levels, merged
counts. No engine, no database."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from app.replays import choke_assets  # noqa: E402
from app.replays.choke_assets import Choke  # noqa: E402
from app.services import replay_gaps_view as view  # noqa: E402


def _row(**over):
    row = {
        "seq": 0, "kind": "predicted", "map": "Testmap", "victim_slot": 3, "victim_side": "attack",
        "t_open": 10.0, "t_last_exposed": 12.0, "t_close": 13.0, "spot_cell": 130, "victim_cell": 0,
        "distance_m": 9.5, "angle_deg": 160.0, "qualified_s": 2.0, "flicker": False,
        "cause": "open_timing", "cause_detail": {}, "choke_seq": [1, 2], "route": [[4, 4], [12, 12]],
        "candidate_slots": [7], "candidate_distances": {"7": 20.0}, "checked_at": None,
        "stood_at": None, "stood_by": None, "shot_at": None, "shot_by": None,
        "killed_at": None, "killed_by": None, "victim_won_at": None, "context": {}, "linked_seq": None,
    }
    row.update(over)
    return row


# --- cell_xy ---------------------------------------------------------------------------------------

def test_cell_xy_corners():
    assert view.cell_xy(0) == (4, 4)
    assert view.cell_xy(127) == (1020, 4)
    assert view.cell_xy(128 * 127) == (4, 1020)
    assert view.cell_xy(128 * 128 - 1) == (1020, 1020)
    assert view.cell_xy(130) == (20, 12)


def test_grid_constants_match_the_engine():
    from app.control import geometry  # the test may load the engine; the module may not
    assert view.GRID == geometry.GRID
    assert view.CELL_PX == geometry.PX // geometry.GRID


# --- choke_points ----------------------------------------------------------------------------------

def test_choke_points_mean_centre_and_tombstones_left_out(tmp_path):
    choke_assets.save("Testmap", [
        Choke(1, "Mid door", [0, 1]),                        # centres (4,4), (12,4)
        Choke(2, "2", [0, 128, 129], source="hand"),         # (4,4), (4,12), (12,12)
        Choke(3, "gone", [500], deleted=True),
    ], next_id=4, asset_dir=tmp_path)
    points = view.choke_points("Testmap", tmp_path)
    assert points == {"1": {"name": "Mid door", "x": 8.0, "y": 4.0},
                      "2": {"name": "2", "x": 6.7, "y": 9.3}}


def test_choke_points_empty_without_an_asset(tmp_path):
    assert view.choke_points("Nomap", tmp_path) == {}


# --- view_rows -------------------------------------------------------------------------------------

def test_view_rows_predicted_route_released(tmp_path):
    choke_assets.save("Testmap", [Choke(1, "A main", [0])], next_id=2, asset_dir=tmp_path)
    row = _row(cause="route_released",
               cause_detail={"cell": 129, "t": 9.0, "player": 2, "by": "view", "reason": "left"})
    out = view.view_rows([row], "Testmap", tmp_path)
    assert out["chokes"] == {"1": {"name": "A main", "x": 4.0, "y": 4.0}}
    (r,) = out["rows"]
    assert r["spot_xy"] == [20, 12]
    assert r["victim_xy"] == [4, 4]
    assert r["released_xy"] == [12, 12]
    assert r["used"] is None
    assert r["merged"] == 0
    for key, value in row.items():
        assert r[key] == value
    assert "spot_xy" not in row                      # the input is copied, not changed
    assert json.loads(json.dumps(out)) == out        # JSON-safe, keys included


def test_view_rows_backshot_has_no_released_cell(tmp_path):
    row = _row(kind="backshot", qualified_s=None, cause=None, cause_detail=None, choke_seq=None,
               t_close=None, shot_at=10.2, shot_by=7, spot_cell=200, victim_cell=300, linked_seq=0)
    (r,) = view.view_rows([row], "Nomap", tmp_path)["rows"]
    assert r["released_xy"] is None
    assert r["used"] == "shot"
    assert r["spot_xy"] == list(view.cell_xy(200))
    assert r["victim_xy"] == list(view.cell_xy(300))
    assert r["merged"] == 0


def test_route_released_without_a_cell_and_other_causes_have_none(tmp_path):
    rows = [_row(seq=0, cause="route_released", cause_detail={}),
            _row(seq=1, cause="victim_turned", cause_detail={"cell": 5})]
    assert [r["released_xy"] for r in view.view_rows(rows, "Nomap", tmp_path)["rows"]] == [None, None]


def test_used_is_the_deepest_level(tmp_path):
    rows = [_row(seq=0),
            _row(seq=1, stood_at=11.0),
            _row(seq=2, stood_at=11.0, shot_at=11.5),
            _row(seq=3, stood_at=11.0, shot_at=11.5, killed_at=11.6),
            _row(seq=4, killed_at=11.6)]
    used = [r["used"] for r in view.view_rows(rows, "Nomap", tmp_path)["rows"]]
    assert used == [None, "stood", "shot", "killed", "killed"]


def test_merged_count(tmp_path):
    rows = [_row(seq=0, context={"merged": [{"choke_seq": [1], "t_open": 9.0},
                                            {"choke_seq": [1, 2], "t_open": 9.5}], "t_round": 9.0}),
            _row(seq=1, context=None),
            _row(seq=2, context={"merged": []})]
    assert [r["merged"] for r in view.view_rows(rows, "Nomap", tmp_path)["rows"]] == [2, 0, 0]


def test_rows_sorted_by_t_open_then_seq(tmp_path):
    rows = [_row(seq=2, t_open=5.0), _row(seq=0, t_open=7.0), _row(seq=1, t_open=5.0)]
    assert [r["seq"] for r in view.view_rows(rows, "Nomap", tmp_path)["rows"]] == [1, 2, 0]


def test_view_rows_empty_map_asset(tmp_path):
    assert view.view_rows([], "Nomap", tmp_path) == {"chokes": {}, "rows": []}


# --- row_from_model --------------------------------------------------------------------------------

def test_row_from_model_covers_every_gap_column_but_the_round_key():
    from app.models.replay import ReplayGap
    names = {c.name for c in ReplayGap.__table__.columns} - {"replay_id", "round_number"}
    assert set(view.COLUMNS) == names
    assert set(_row()) == names
    gap = SimpleNamespace(replay_id=1, round_number=2, **_row(seq=4))
    assert view.row_from_model(gap) == _row(seq=4)
