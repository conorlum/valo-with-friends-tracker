"""TASK 0 GATE (see the spec's 'The tuning surface already exists').

Stage A fits FACTOR_WEIGHTS by regressing on four stored columns. That is
only valid if `impact` really is the linear combination of them that
impact.py's arithmetic implies. This asserts the identity over EVERY row,
via a SQL aggregate rather than a sample.

Only over rows the LEGACY formula produced. The identity describes
`damage + (econ + time + swing)/3`, which is the live legacy combination step;
rc3 replaced it with damage / leverage / econ / assists plus trade credit, and
deliberately does NOT store two of those four terms (`leverage_component` and
`assists_component` are diagnostics the table never carries -- see the exporter's
"columns the table never stores"). So no identity over stored columns can
reconstruct an rc3 row's `impact`: the terms are absent, not imprecise, and
widening TOLERANCE cannot help. Measured 2026-09-18 on the rehearsal restore:
0 breaches of 659,290 legacy rows, and 618,393 of 659,500 rc3 rows.

Run against an rc3 table unfiltered, this test would report a deliberate,
documented formula change as a broken identity -- the same mistake
test_builder_matches_stored_values made across the v1/v2 bump before 5ec6a93,
and for the same reason: matching or mismatching version labels are about which
FORMULA produced a row, not about whether that row is correct.

Skips when no database is reachable -- it is a data gate, not a unit test.
Start Postgres with: docker compose -p valomaths-private up -d
"""

import pytest
from sqlalchemy import text

from app.scoring.impact import FACTOR_WEIGHTS, IMPACT_CALCULATION_VERSION
from app.scoring.impact_runtime import active_manifest
from tests._postgres import postgres_session_or_skip

# impact.py round()s kill_impact, death_impact and each component
# independently, so exact equality is not expected.
TOLERANCE = 2


def _session():
    # A disposable test database only (tests/_postgres.py).
    return postgres_session_or_skip()


def _legacy_only_filter(db):
    """SQL predicate selecting rows the legacy combination step produced.

    Two things vary and neither can be assumed:

    - `scoring_version` only exists from migration 0008. A database still at
      0007 predates rc3 entirely, so every row in it is legacy by construction
      and needs no filter.
    - the boundary is not a constant hardcoded here. With a manifest active it
      is that manifest's activation version. With NO manifest active the
      running code is itself the legacy formula, so anything stored ABOVE the
      version it writes came from a newer one -- which is exactly the stale
      checkout against a swapped table, and must skip rather than report the
      newer rows as a broken identity.
    """
    has_column = db.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'impact_scores' AND column_name = 'scoring_version'"
    )).scalar()
    if not has_column:
        return "true", None
    manifest = active_manifest()
    first_rc3 = (manifest["activation_impact_calculation_version"] if manifest is not None
                 else IMPACT_CALCULATION_VERSION + 1)
    return "scoring_version < :first_rc3", first_rc3


def test_impact_reconstructs_from_stored_components():
    db = _session()
    try:
        total = sum(FACTOR_WEIGHTS.values())
        predicate, first_rc3 = _legacy_only_filter(db)
        params = {
            "we": FACTOR_WEIGHTS["econ"],
            "wt": FACTOR_WEIGHTS["time"],
            "ws": FACTOR_WEIGHTS["swing"],
            "total": total,
            "tol": TOLERANCE,
        }
        if first_rc3 is not None:
            params["first_rc3"] = first_rc3
        row = db.execute(
            text(
                f"""
                select count(*) as rows,
                       max(abs(err)) as max_err,
                       sum(case when abs(err) > :tol then 1 else 0 end) as breaches
                from (
                  select impact - (
                      damage
                      + (:we * econ_impact + :wt * time_impact + :ws * swing_impact) / :total
                  ) as err
                  from impact_scores
                  where {predicate}
                ) t
                """
            ),
            params,
        ).one()
        versions_present = None
        if first_rc3 is not None:
            versions_present = sorted(
                v for (v,) in db.execute(text("select distinct scoring_version from impact_scores"))
            )
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"impact_scores unreadable: {exc}")
    finally:
        db.close()

    if row.rows == 0:
        if versions_present:
            pytest.skip(
                f"no rows below scoring_version {first_rc3}: the table holds "
                f"{versions_present}, which this identity does not describe. It is the "
                f"legacy combination step; rc3 does not store leverage_component or "
                f"assists_component, so its impact cannot be reconstructed from stored "
                f"columns at all."
            )
        pytest.skip("impact_scores is empty")

    assert row.breaches == 0, (
        f"{row.breaches} of {row.rows} rows break the linear identity Stage A "
        f"depends on (max error {row.max_err}). Do NOT proceed to fitting and "
        f"do NOT widen TOLERANCE -- re-read impact.py's kill_impact/"
        f"death_impact combination step instead."
    )
