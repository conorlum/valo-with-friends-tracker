"""Gates holding the leverage extractor to what app/scoring/impact.py
actually computes. Requires a live Postgres:

    docker compose -p valomaths-private up -d

and skips cleanly when it is unreachable, matching the parent project's
convention for its own DB-backed tests."""

import networkx as nx
import numpy as np
import pytest

import app.scoring.impact as impact_module
from app.models import ImpactScore, MatchPlayer
from app.models.match import Team
from app.scoring.impact import build_impact_rows_for_match
from app.services.kill_order_leverage import (
    COMPONENTS,
    PARAMS,
    build_match_leverage,
    eligible_match_ids,
    shipped_graph,
)

SAMPLE_MATCHES = 12


@pytest.fixture(scope="module")
def db():
    try:
        from app.db import SessionLocal

        session = SessionLocal()
        session.execute(__import__("sqlalchemy").text("select 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"live Postgres unavailable: {exc}")
    yield session
    session.close()


@pytest.fixture(scope="module")
def sample_match_ids(db):
    ids = eligible_match_ids(db)[:SAMPLE_MATCHES]
    if not ids:
        pytest.skip("no eligible matches in the database")
    return ids


def _stored_impact_diff(db, match_id):
    """Round-level Impact differential straight from the scorer, ex-ante."""
    teams = {
        mp.id: mp.team for mp in db.query(MatchPlayer).filter_by(match_id=match_id).all()
    }
    out: dict[int, float] = {}
    for row in build_impact_rows_for_match(db, match_id, use_realized_swing=False):
        sign = 1.0 if teams[row.match_player_id] == Team.TEAM_1 else -1.0
        out[row.round_id] = out.get(row.round_id, 0.0) + sign * row.impact
    return out


def _reconstruct(team_row, graph):
    """damage_diff + (1/3) * SUM_{k,c} b_k * (kill + death), which is the
    shipped formula with FACTOR_WEIGHTS all 1.0."""
    weighted = graph[:, None] * (team_row.kill + team_row.death)
    return team_row.damage_diff + weighted.sum() / 3.0


def test_reconstruction_matches_the_scorer_within_the_rounding_bound(db, sample_match_ids):
    graph = shipped_graph()
    worst = 0.0
    checked = 0
    for match_id in sample_match_ids:
        leverage = build_match_leverage(db, match_id)
        stored = _stored_impact_diff(db, match_id)
        players_per_round: dict[int, int] = {}
        for row in leverage.player_rows:
            players_per_round[row.round_id] = players_per_round.get(row.round_id, 0) + 1
        for team_row in leverage.team_rows:
            if team_row.round_id not in stored:
                continue
            gap = abs(_reconstruct(team_row, graph) - stored[team_row.round_id])
            bound = 1.0 * players_per_round[team_row.round_id]
            assert gap <= bound, (
                f"round {team_row.round_id}: gap {gap:.3f} exceeds rounding bound {bound}"
            )
            worst = max(worst, gap)
            checked += 1
    assert checked > 0
    print(f"\nreconstruction: {checked} rounds, worst gap {worst:.3f}")


def test_impact_is_linear_in_the_edge_weights(db, sample_match_ids):
    """The premise the whole design rests on. Doubling every EDGE weight
    must double the three scored components and leave damage untouched --
    for rows whose kills all crossed a graph edge.

    Rows touching the FALLBACK transition are excluded: _kill_order_bonus's
    except-KeyError branch returns a hard-coded 100 for an untracked
    transition, which by design never scales with the graph (the fallback
    is pinned, not an edge weight -- see the spec's fallback-treatment
    table). A first draft of this test did not exclude them and failed on
    ~0.5% of rows with ratios up to 2.82, which is exactly the fallback's
    constant-100 contribution diluting an otherwise-exact 2x scaling in the
    same rounded integer -- not a defect in impact.py or the extractor.
    Excluding by KEY (not by re-deriving from the doubled run) matters:
    which transitions are untracked doesn't depend on the graph's weights,
    only its topology, which is identical at every scale here.
    """
    base = {(u, v): d["weight"] for u, v, d in impact_module._KILL_ORDER_GRAPH.edges(data=True)}

    def rows_with(scale):
        graph = nx.DiGraph()
        graph.add_weighted_edges_from([(u, v, w * scale) for (u, v), w in base.items()])
        original = impact_module._KILL_ORDER_GRAPH
        impact_module._KILL_ORDER_GRAPH = graph
        try:
            out = {}
            for match_id in sample_match_ids:
                for row in build_impact_rows_for_match(db, match_id, use_realized_swing=False):
                    out[(row.round_id, row.match_player_id)] = row
            return out
        finally:
            impact_module._KILL_ORDER_GRAPH = original

    single = rows_with(1.0)
    double = rows_with(2.0)

    fallback_index = PARAMS.index("fallback")
    fallback_keys = set()
    for match_id in sample_match_ids:
        leverage = build_match_leverage(db, match_id)
        for row in leverage.player_rows:
            touched = (
                np.abs(row.kill[fallback_index]).sum() + np.abs(row.death[fallback_index]).sum()
            )
            if touched > 0:
                fallback_keys.add((row.round_id, row.match_player_id))
    assert fallback_keys, "expected at least one fallback-touched row in the sample"

    keys = [k for k in single if k not in fallback_keys]
    assert keys

    for field in ("econ_impact", "time_impact", "swing_impact"):
        one = np.array([getattr(single[k], field) for k in keys], dtype=float)
        two = np.array([getattr(double[k], field) for k in keys], dtype=float)
        big = np.abs(one) >= 50  # small integers are dominated by their own rounding
        assert big.sum() > 0
        ratio = two[big] / one[big]
        assert np.allclose(ratio, 2.0, atol=0.05), f"{field} ratio {ratio.min()}..{ratio.max()}"

    damage_one = np.array([single[k].damage for k in single], dtype=float)
    damage_two = np.array([double[k].damage for k in single], dtype=float)
    assert np.array_equal(damage_one, damage_two), "damage must not depend on the graph"


def test_the_extractor_writes_nothing(db, sample_match_ids):
    """Same regression guard as the parent project's
    test_impact_exante_swing.py third assertion: the read-only path must
    stay read-only."""
    before = db.query(ImpactScore).count()
    for match_id in sample_match_ids:
        build_match_leverage(db, match_id)
    assert not db.new
    assert not db.dirty
    assert not db.deleted
    db.rollback()
    assert db.query(ImpactScore).count() == before


def test_shipped_graph_round_trips_and_is_side_symmetric(db):
    """shipped_graph() raises on asymmetry, so reaching this point already
    proves it; the assertions pin the values a reader can check by eye
    against impact.py:45-99."""
    graph = shipped_graph()
    assert graph.shape == (len(PARAMS),)
    assert np.all(graph > 0)
    expected = {"5v5": 150.0, "4v4": 170.0, "3v3": 180.0, "2v2": 200.0, "1v1": 250.0,
                "1v2": 190.0, "2v1": 130.0, "5v1": 40.0, "1v5": 60.0}
    for name, value in expected.items():
        assert graph[PARAMS.index(name)] == value


def test_fallback_crossings_are_rare_and_flagged(db, sample_match_ids):
    """Measured at 0.30% of kill events across the full DB. If this ever
    climbs sharply, the resurrection heuristic has drifted and the
    fallback-sensitivity run in the report is no longer a footnote."""
    fallback_index = PARAMS.index("fallback")
    fallback = 0.0
    total = 0.0
    for match_id in sample_match_ids:
        leverage = build_match_leverage(db, match_id)
        for row in leverage.player_rows:
            fallback += np.abs(row.kill[fallback_index]).sum() + np.abs(row.death[fallback_index]).sum()
            total += np.abs(row.kill).sum() + np.abs(row.death).sum()
    assert total > 0
    assert fallback / total < 0.05, f"fallback share {fallback / total:.3%} is not a footnote"
