"""Stage C0 against the real database. Requires:

    docker compose -p valomaths-private up -d

Skips cleanly when Postgres is unreachable. This is the test that would
catch the spec's own headline numbers having drifted as the crawl grows."""

import numpy as np
import pytest

from app.services.kill_order_leverage import load_all_leverage, state_visits_for_match
from app.services.kill_order_refit import stage_c0_report


@pytest.fixture(scope="module")
def loaded():
    try:
        from app.db import SessionLocal

        session = SessionLocal()
        session.execute(__import__("sqlalchemy").text("select 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"live Postgres unavailable: {exc}")
    report: dict = {}
    team_rows, player_rows = load_all_leverage(session, report=report)
    visits = []
    for match_id in {row.match_id for row in team_rows}:
        visits.extend(state_visits_for_match(session, match_id))
    yield team_rows, player_rows, visits, report
    session.close()


def test_the_shipped_graph_tracks_the_measured_swing_curve(loaded):
    """Spec figure: exposure-weighted R^2 = 0.9704, every residual within
    +-17 on a 40-250 scale. A large drop here means the DB has changed
    enough that the spec's framing needs revisiting."""
    team_rows, _players, visits, _report = loaded
    report = stage_c0_report(team_rows, _players, visits, draws=50)
    fit = report["shipped_vs_swing"]
    assert fit["r_squared"] > 0.90
    assert max(abs(r) for r in fit["residuals"].values()) < 40


def test_swapping_the_graph_barely_moves_the_metric(loaded):
    """Spec figure: r = 0.998 with zero sign flips over 5,259 rounds. If
    this collapses, the headroom argument the whole stage is framed around
    has stopped holding."""
    team_rows, player_rows, visits, _report = loaded
    report = stage_c0_report(team_rows, player_rows, visits, draws=50)
    swap = report["current_vs_swing_plugin"]
    assert swap["round_level"]["pearson"] > 0.95
    assert swap["round_level"]["sign_flip_rate"] < 0.05
    assert swap["player_match_level"]["pearson"] > 0.95


def test_every_state_has_enough_exposure_to_estimate(loaded):
    """Measured: the rarest parameter is crossed 1,446 times. The estimation
    problem here is conditioning, not sparsity, and this pins that."""
    _team, _players, visits, _report = loaded
    report = stage_c0_report(_team, _players, visits, draws=10)
    counts = report["swing_table"]["visits"]
    lattice = {k: v for k, v in counts.items() if k != "fallback"}
    assert min(lattice.values()) > 500
