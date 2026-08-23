import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
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
from app.services.player_graphs import STATE_DIAGRAM_CALCULATION_VERSION
from app.services.player_profile_types import GroupedStat, MatchBreakdown, PlayerProfile
from app.services.player_view_cache import (
    PLAYER_VIEW_CACHE_SCHEMA_VERSION,
    _decode,
    _encode,
    _validate_blob,
    cache_version,
)
from app.services.player_views import PlayerViews
from app.services.players import get_player_profile
from app.scoring.impact import IMPACT_CALCULATION_VERSION

WEBAPP_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Sample data builders
# ---------------------------------------------------------------------------

@dataclass
class _FakeMatch:
    """Duck-typed stand-in for app.models.Match -- _encode_match_summary only
    ever reads these six attributes, so a real ORM instance (with its
    MatchSource enum, DB defaults, etc.) isn't needed here."""

    id: int
    external_id: str
    map_name: str | None
    played_at: datetime | None
    team1_rounds_won: int
    team2_rounds_won: int


def _sample_match_breakdown(
    match_id: int = 1, agent: str = "Jett", team: str = "team-1", win: bool | None = True,
) -> MatchBreakdown:
    match = _FakeMatch(
        id=match_id, external_id=f"ext-{match_id}", map_name="Haven",
        played_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        team1_rounds_won=13, team2_rounds_won=7,
    )
    return MatchBreakdown(
        match=match, agent=agent, team=team, average_impact=12.5, average_kill_impact=20.0,
        average_death_impact=-7.5, win=win, kills=18, deaths=14, assists=4,
    )


def _sample_profile(player_id: int = 1) -> PlayerProfile:
    matches = [_sample_match_breakdown(1), _sample_match_breakdown(2, win=False)]
    return PlayerProfile(
        player=Player(id=player_id, display_name="Foo#123"),
        overall_average_impact=10.0,
        overall_average_round_win_impact=5.0,
        overall_average_death_impact=-6.0,
        matches=matches,
        agent_counts={"Jett": 2},
        agent_stats=[GroupedStat("Jett", 2, 1, 1, 0.5, 10.0, 20.0, -7.5)],
        map_stats=[GroupedStat("Haven", 2, 1, 1, 0.5, 10.0, 20.0, -7.5)],
        avg_econ_kill=1.0, avg_econ_death=0.5, avg_clutch_kill=0.2, avg_clutch_death=0.1,
        avg_post_plant_kill=0.3, avg_post_plant_death=0.15,
        avg_traded_teammate=0.4, avg_traded_by_teammate=0.6,
        top_traded_teammate=[("Bar#456", 3)], top_traded_by_teammate=[("Baz#789", 2)],
    )


def _sample_econ_aggregates() -> dict:
    return {
        "tier_pairs": {("FULL_BUY", "FULL_BUY"): {"win": 5, "total": 10, "ratio_sum": 4.5, "ratio_count": 8}},
        "pistol": {"win": 1, "total": 2, "ratio_sum": 1.0, "ratio_count": 2},
        "loadout_buckets": {10: {"win": 3, "total": 6}},
    }


def _sample_views(player_id: int = 1) -> PlayerViews:
    fight_ev = build_fight_ev_views_from_blocks([], player_id, draws=10)
    win_stats = {"5v5": {"win": 12, "total": 30}, "5v4": {"win": 3, "total": 4}}
    kill_order_weights = {("5v5", "5v4"): 7, ("5v5", "4v5"): -9}
    return PlayerViews(
        win_stats, kill_order_weights, fight_ev,
        _sample_profile(player_id), _sample_econ_aggregates(),
    )


# ---------------------------------------------------------------------------
# Blob round-trip + validation
# ---------------------------------------------------------------------------

def test_blob_round_trip_reproduces_exact_dicts_including_tuple_keys():
    views = _sample_views()
    blob = _encode(views)
    assert _validate_blob(blob)

    decoded = _decode(blob, player=Player(id=1, display_name="Foo#123"))
    # Rebuilt via build_state_diagrams_from_aggregates -- assert the underlying
    # aggregates survive by re-deriving the kill_order edge weights from the
    # rendered graph and comparing to the original tuple-keyed dict.
    rendered_weights = {(e.source, e.target): e.weight for e in decoded.kill_order_graph.edges if e.weight != 0}
    assert rendered_weights == views.kill_order_weights
    assert decoded.fight_ev_data == blob["fight_ev"]


def test_blob_round_trip_reproduces_econ_aggregates():
    views = _sample_views()
    blob = _encode(views)
    decoded = _decode(blob, player=Player(id=1, display_name="Foo#123"))

    cell = decoded.econ_tier_matrix.cells[("FULL_BUY", "FULL_BUY")]
    assert cell.wins == 5
    assert cell.total == 10

    assert decoded.econ_pistol_stats.wins == 1
    assert decoded.econ_pistol_stats.total == 2

    bucket_point_titles = [p.title for p in decoded.econ_loadout_scatter.points]
    assert any("3/6 rounds won" in t for t in bucket_point_titles)


def test_blob_round_trip_reproduces_profile():
    views = _sample_views()
    blob = _encode(views)
    live_player = Player(id=1, display_name="Foo#123")
    decoded = _decode(blob, player=live_player)

    profile = decoded.profile
    assert profile.player is live_player  # doc 2d: live route-level Player attached
    assert len(profile.matches) == 2
    assert profile.matches[0].match.external_id == "ext-1"
    assert profile.matches[0].match.map_name == "Haven"
    assert profile.matches[0].match.played_at == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert profile.matches[0].win is True
    assert profile.matches[1].win is False
    assert profile.overall_average_impact == 10.0
    assert profile.top_traded_teammate == [("Bar#456", 3)]
    assert isinstance(profile.top_traded_teammate[0], tuple)
    from collections import Counter
    assert profile.agent_counts == Counter({"Jett": 2})
    assert profile.agent_counts.most_common(1) == [("Jett", 2)]
    # agent_stats/map_stats are REBUILT at decode, not stored verbatim --
    # assert they were actually recomputed from the reconstructed matches.
    assert profile.agent_stats[0].key == "Jett"
    assert profile.agent_stats[0].matches_played == 2
    assert profile.map_stats[0].key == "Haven"


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
# econ_aggregates validation (Step 2a)
# ---------------------------------------------------------------------------

def test_validation_rejects_missing_econ_aggregates_key():
    blob = _encode(_sample_views())
    del blob["econ_aggregates"]["pistol"]
    assert _validate_blob(blob) is False


def test_validation_rejects_tier_pair_with_wrong_length():
    blob = _encode(_sample_views())
    blob["econ_aggregates"]["tier_pairs"][0] = blob["econ_aggregates"]["tier_pairs"][0][:5]
    assert _validate_blob(blob) is False


def test_validation_rejects_econ_win_greater_than_total():
    blob = _encode(_sample_views())
    blob["econ_aggregates"]["pistol"]["win"] = 999
    assert _validate_blob(blob) is False


def test_validation_rejects_loadout_bucket_index_out_of_range():
    blob = _encode(_sample_views())
    blob["econ_aggregates"]["loadout_buckets"][0] = [99, 1, 2]
    assert _validate_blob(blob) is False


def test_validation_rejects_negative_econ_counts():
    blob = _encode(_sample_views())
    blob["econ_aggregates"]["pistol"]["total"] = -1
    assert _validate_blob(blob) is False


# ---------------------------------------------------------------------------
# profile validation (Step 2a/2d), especially played_at (Step 2d)
# ---------------------------------------------------------------------------

def test_validation_rejects_missing_profile_key():
    blob = _encode(_sample_views())
    del blob["profile"]["overall_average_impact"]
    assert _validate_blob(blob) is False


def test_validation_rejects_match_summary_with_extra_key():
    blob = _encode(_sample_views())
    blob["profile"]["matches"][0]["extra"] = 1
    assert _validate_blob(blob) is False


def test_validation_rejects_match_summary_with_missing_key():
    blob = _encode(_sample_views())
    del blob["profile"]["matches"][0]["map_name"]
    assert _validate_blob(blob) is False


def test_validation_accepts_null_map_name_and_played_at():
    blob = _encode(_sample_views())
    blob["profile"]["matches"][0]["map_name"] = None
    blob["profile"]["matches"][0]["played_at"] = None
    assert _validate_blob(blob) is True


def test_validation_rejects_naive_played_at():
    # Load-bearing: match_label() calls played.astimezone(DISPLAY_TZ) --
    # decoding a naive datetime would silently render the wrong local time
    # instead of raising, so a blob whose played_at would decode naive must
    # be treated as corrupt (Step 2d).
    blob = _encode(_sample_views())
    blob["profile"]["matches"][0]["played_at"] = "2026-01-01T12:00:00"  # no offset
    assert _validate_blob(blob) is False


def test_validation_rejects_unparseable_played_at():
    blob = _encode(_sample_views())
    blob["profile"]["matches"][0]["played_at"] = "not-a-date"
    assert _validate_blob(blob) is False


def test_validation_rejects_win_not_a_bool():
    blob = _encode(_sample_views())
    blob["profile"]["matches"][0]["win"] = "yes"
    assert _validate_blob(blob) is False


def test_validation_rejects_malformed_top_traded_pair():
    blob = _encode(_sample_views())
    blob["profile"]["top_traded_teammate"] = [["OnlyName"]]
    assert _validate_blob(blob) is False


def test_validation_rejects_malformed_agent_counts_pair():
    blob = _encode(_sample_views())
    blob["profile"]["agent_counts"] = [["Jett", "not-a-count"]]
    assert _validate_blob(blob) is False


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

def test_cache_version_is_composite_of_schema_and_calculation_versions():
    assert cache_version() == (
        PLAYER_VIEW_CACHE_SCHEMA_VERSION * 1_000_000_000
        + STATE_DIAGRAM_CALCULATION_VERSION * 1_000_000
        + CALCULATION_VERSION * 1000
        + IMPACT_CALCULATION_VERSION
    )


def test_cache_version_moves_when_fight_ev_calculation_version_moves(monkeypatch):
    import app.services.player_view_cache as pvc

    before = pvc.cache_version()
    monkeypatch.setattr(pvc, "CALCULATION_VERSION", pvc.CALCULATION_VERSION + 1)
    after = pvc.cache_version()
    assert after != before
    assert after == before + 1000


def test_cache_version_moves_when_impact_calculation_version_moves(monkeypatch):
    import app.services.player_view_cache as pvc

    before = pvc.cache_version()
    monkeypatch.setattr(pvc, "IMPACT_CALCULATION_VERSION", pvc.IMPACT_CALCULATION_VERSION + 1)
    after = pvc.cache_version()
    assert after != before
    assert after == before + 1


def test_cache_version_moves_when_state_diagram_calculation_version_moves(monkeypatch):
    import app.services.player_view_cache as pvc

    before = pvc.cache_version()
    monkeypatch.setattr(pvc, "STATE_DIAGRAM_CALCULATION_VERSION", pvc.STATE_DIAGRAM_CALCULATION_VERSION + 1)
    after = pvc.cache_version()
    assert after != before
    assert after == before + 1_000_000


# ---------------------------------------------------------------------------
# Ordering contract (C23): load_player_match_data, get_player_profile, and
# player_econ_samples must all select the same "recent" 30 matches.
#
# Since Step 2, the LIVE player-page route no longer calls
# get_player_profile/player_econ_samples at all (profile/econ come from
# load_player_match_data's own shared hydration, see
# app.services.player_profile_types.build_player_profile_from_match_data and
# app.services.economy_graphs.econ_samples_from_data) -- so this contract is
# enforced BY CONSTRUCTION on that path, not by convention. It still matters
# for get_player_profile/player_econ_samples themselves, which remain
# independently-correct standalone functions -- this test guards that they
# don't silently drift from load_player_match_data's ordering if either is
# ever called directly again.
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
