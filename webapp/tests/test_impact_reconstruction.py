"""TASK 0 GATE (see the spec's 'The tuning surface already exists').

Stage A fits FACTOR_WEIGHTS by regressing on four stored columns. That is
only valid if `impact` really is the linear combination of them that
impact.py's arithmetic implies. This asserts the identity over EVERY row,
via a SQL aggregate rather than a sample.

Skips when no database is reachable -- it is a data gate, not a unit test.
Start Postgres with: docker compose -p valomaths-private up -d
"""

import pytest
from sqlalchemy import text

from app.scoring.impact import FACTOR_WEIGHTS

# impact.py round()s kill_impact, death_impact and each component
# independently, so exact equality is not expected.
TOLERANCE = 2


def _session():
    try:
        from app.db import SessionLocal

        return SessionLocal()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no database available: {exc}")


def test_impact_reconstructs_from_stored_components():
    db = _session()
    try:
        total = sum(FACTOR_WEIGHTS.values())
        row = db.execute(
            text(
                """
                select count(*) as rows,
                       max(abs(err)) as max_err,
                       sum(case when abs(err) > :tol then 1 else 0 end) as breaches
                from (
                  select impact - (
                      damage
                      + (:we * econ_impact + :wt * time_impact + :ws * swing_impact) / :total
                  ) as err
                  from impact_scores
                ) t
                """
            ),
            {
                "we": FACTOR_WEIGHTS["econ"],
                "wt": FACTOR_WEIGHTS["time"],
                "ws": FACTOR_WEIGHTS["swing"],
                "total": total,
                "tol": TOLERANCE,
            },
        ).one()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"impact_scores unreadable: {exc}")
    finally:
        db.close()

    if row.rows == 0:
        pytest.skip("impact_scores is empty")

    assert row.breaches == 0, (
        f"{row.breaches} of {row.rows} rows break the linear identity Stage A "
        f"depends on (max error {row.max_err}). Do NOT proceed to fitting and "
        f"do NOT widen TOLERANCE -- re-read impact.py's kill_impact/"
        f"death_impact combination step instead."
    )
