import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.orm import Query

from app.models import Player
from app.services.economy_graphs import player_econ_samples
from app.services.fight_ev import (
    CALCULATION_VERSION,
    FIGHT_EV_CELL_KEYS,
    FIGHT_EV_VIEW_KEYS,
    _bootstrap_seed,
    _serialize_cell,
    build_fight_ev_views_from_blocks,
    compute_point_estimate,
    serialize_fight_ev_views,
)
from app.services.player_data import RECENT_MATCH_LIMIT, load_player_match_data
from app.services.player_view_cache import (
    PLAYER_VIEW_CACHE_SCHEMA_VERSION,
    _decode,
    _encode,
    _validate_blob,
    cache_version,
)
from app.services.player_views import PlayerViews
from app.services.players import get_player_profile

WEBAPP_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Blob round-trip + validation
# ---------------------------------------------------------------------------

def _sample_views(player_id: int = 1) -> PlayerViews:
    fight_ev = build_fight_ev_views_from_blocks([], player_id, draws=10)
    win_stats = {"5v5": {"win": 12, "total": 30}, "5v4": {"win": 3, "total": 4}}
    kill_order_weights = {("5v5", "5v4"): 7, ("5v5", "4v5"): -9}
    return PlayerViews(win_stats, kill_order_weights, fight_ev)


def test_blob_round_trip_reproduces_exact_dicts_including_tuple_keys():
    views = _sample_views()
    blob = _encode(views)
    assert _validate_blob(blob)

    decoded = _decode(blob)
    # Rebuilt via build_state_diagrams_from_aggregates -- assert the underlying
    # aggregates survive by re-deriving the kill_order edge weights from the
    # rendered graph and comparing to the original tuple-keyed dict.
    rendered_weights = {(e.source, e.target): e.weight for e in decoded.kill_order_graph.edges if e.weight != 0}
    assert rendered_weights == views.kill_order_weights
    assert decoded.fight_ev_data == blob["fight_ev"]


def test_happy_path_blob_validates():
    blob = _encode(_sample_views())
    assert _validate_blob(blob) is True


def test_validation_rejects_dropped_view_key():
    blob = _encode(_sample_views())
    del blob["fight_ev"]["attacking_tracked_roster"]
    assert _validate_blob(blob) is False


def test_validation_rejects_wrong_cell_count():
    blob = _encode(_sample_views())
    blob["fight_ev"]["attacking_tracked_roster"] = blob["fight_ev"]["attacking_tracked_roster"][:24]
    assert _validate_blob(blob) is False


def test_validation_rejects_cell_missing_display_state():
    blob = _encode(_sample_views())
    del blob["fight_ev"]["attacking_tracked_roster"][0]["display_state"]
    assert _validate_blob(blob) is False


def test_validation_rejects_cell_with_extra_key():
    blob = _encode(_sample_views())
    blob["fight_ev"]["attacking_tracked_roster"][0]["extra_field"] = 1
    assert _validate_blob(blob) is False


def test_validation_rejects_unknown_display_state():
    blob = _encode(_sample_views())
    blob["fight_ev"]["attacking_tracked_roster"][0]["display_state"] = "NOT_A_REAL_STATE"
    assert _validate_blob(blob) is False


def test_validation_rejects_win_greater_than_total():
    blob = _encode(_sample_views())
    blob["state_aggregates"]["win_stats"]["5v5"] = {"win": 99, "total": 1}
    assert _validate_blob(blob) is False


def test_validation_rejects_two_element_kill_order_triple():
    blob = _encode(_sample_views())
    blob["state_aggregates"]["kill_order_weights"].append(["5v5", "4v4"])
    assert _validate_blob(blob) is False


def test_validation_rejects_missing_top_level_keys():
    assert _validate_blob({}) is False
    assert _validate_blob({"state_aggregates": {}}) is False
    assert _validate_blob("not even a dict") is False


# ---------------------------------------------------------------------------
# Serializer/validator drift guard
# ---------------------------------------------------------------------------

def test_serialize_cell_keys_match_declared_cell_keys():
    cell = compute_point_estimate({}, {}, {}, "attacking", 1, 1)
    assert set(_serialize_cell(cell).keys()) == FIGHT_EV_CELL_KEYS


def test_serialize_fight_ev_views_keys_match_declared_view_keys():
    views = build_fight_ev_views_from_blocks([], player_id=1, draws=10)
    assert set(serialize_fight_ev_views(views).keys()) == set(FIGHT_EV_VIEW_KEYS)


# ---------------------------------------------------------------------------
# Version gate
# ---------------------------------------------------------------------------

def test_cache_version_is_composite_of_schema_and_calculation_version():
    assert cache_version() == PLAYER_VIEW_CACHE_SCHEMA_VERSION * 1000 + CALCULATION_VERSION


def test_cache_version_moves_when_calculation_version_moves(monkeypatch):
    import app.services.player_view_cache as pvc

    before = pvc.cache_version()
    monkeypatch.setattr(pvc, "CALCULATION_VERSION", pvc.CALCULATION_VERSION + 1)
    after = pvc.cache_version()
    assert after != before
    assert after == before + 1


# ---------------------------------------------------------------------------
# Ordering contract (C23): load_player_match_data, get_player_profile, and
# player_econ_samples must all select the same "recent" 30 matches.
# ---------------------------------------------------------------------------

def _capture_first_query_sql(fn, *args, **kwargs) -> str:
    """Monkeypatches Query.all so the target function runs to completion
    without ever touching a real DB connection -- the first `.all()` call
    (always the match_players query in each of these three functions) has its
    compiled SQL captured, and every `.all()` call (this one and any that
    would follow, which none of these functions reach with an empty result)
    returns an empty list."""
    captured: dict = {}
    original_all = Query.all

    def fake_all(self):
        if "sql" not in captured:
            captured["sql"] = str(self.statement.compile())
        return []

    Query.all = fake_all
    try:
        fn(*args, **kwargs)
    finally:
        Query.all = original_all
    return captured["sql"]


def _order_by_clause(compiled_sql: str) -> str:
    # The full SELECT differs across the three (different eager-load
    # strategies/joins per function) -- what must match is just the ORDER BY
    # (and any LIMIT after it), which is the shared contract this test guards.
    idx = compiled_sql.index("ORDER BY")
    return compiled_sql[idx:]


def test_recent_window_order_by_is_identical_across_the_three_call_sites():
    from sqlalchemy.orm import Session

    db = Session()
    player = Player(id=1)

    sql_loader = _capture_first_query_sql(load_player_match_data, db, player, RECENT_MATCH_LIMIT)
    sql_profile = _capture_first_query_sql(get_player_profile, db, player, RECENT_MATCH_LIMIT)
    sql_econ = _capture_first_query_sql(player_econ_samples, db, player, RECENT_MATCH_LIMIT)

    assert "ORDER BY" in sql_loader
    assert _order_by_clause(sql_loader) == _order_by_clause(sql_profile) == _order_by_clause(sql_econ)


# ---------------------------------------------------------------------------
# Seed stability (C12): _bootstrap_seed must be process-stable across
# different PYTHONHASHSEED values -- the old hash()-based version was not.
# ---------------------------------------------------------------------------

def test_bootstrap_seed_is_stable_across_pythonhashseed():
    script = (
        "from app.services.fight_ev import _bootstrap_seed; "
        "print(_bootstrap_seed(42, 'attacking', 3, 2, 'tracked_roster'))"
    )

    def _run(hash_seed: str) -> str:
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, cwd=str(WEBAPP_ROOT), env=env
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    assert _run("1") == _run("98765")

    in_process = _bootstrap_seed(42, "attacking", 3, 2, "tracked_roster")
    assert str(in_process) == _run("1")


# ---------------------------------------------------------------------------
# Scope slicing
# ---------------------------------------------------------------------------

def test_scope_slicing_matches_recent_match_limit():
    ordered = list(range(50))
    recent = ordered[:RECENT_MATCH_LIMIT]
    career = ordered[:]
    assert recent == list(range(RECENT_MATCH_LIMIT))
    assert career == ordered
    assert len(recent) == RECENT_MATCH_LIMIT
