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

from app.scoring import impact
from app.scoring.impact import FACTOR_WEIGHTS
from tests._postgres import postgres_session_or_skip

# The first scoring_version whose rows the NEW structure wrote (rc3, version 3).
# Versions 1 and 2 are the legacy combination step this identity describes, and
# every version from 3 on -- rc3, v4 and whatever follows -- is not. A fact
# about the history of the formula, so it is fixed here rather than read from
# the active manifest: with v4 active, "the activation version" is 4, and v3
# rows would have been classified as legacy (plan 2026-09-21-impact-v4, R9).
FIRST_NEW_STRUCTURE_VERSION = 3

# impact.py round()s kill_impact, death_impact and each component
# independently, so exact equality is not expected.
TOLERANCE = 2


def _session():
    # A disposable test database only (tests/_postgres.py).
    return postgres_session_or_skip()


def _legacy_only_filter(db):
    """SQL predicate selecting rows the legacy combination step produced.

    Two things matter here:

    - `scoring_version` only exists from migration 0008. A database still at
      0007 predates rc3 entirely, so every row in it is legacy by construction
      and needs no filter.
    - the boundary is the formula's GENERATION, FIRST_NEW_STRUCTURE_VERSION,
      and deliberately not the active manifest's activation version: that
      moves with every release (4 under v4), while which versions the legacy
      step wrote never does.
    """
    has_column = db.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'impact_scores' AND column_name = 'scoring_version'"
    )).scalar()
    if not has_column:
        return "true", None
    return "scoring_version < :first_rc3", FIRST_NEW_STRUCTURE_VERSION


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


class _HasScoringVersion:
    """Offline stand-in: the only query _legacy_only_filter makes."""

    def execute(self, *_args, **_kwargs):
        class _Result:
            @staticmethod
            def scalar():
                return 1
        return _Result()


@pytest.mark.parametrize("active", [None, 3, 4, 5])
def test_the_legacy_boundary_is_the_formula_generation_not_the_active_version(monkeypatch, active):
    """Plan 2026-09-21-impact-v4 section 2.7 (R9). Versions 1 and 2 are the
    legacy combination step; 3 (rc3) is the first new-structure formula, and so
    is every version after it. Taking the ACTIVE manifest's activation version
    as the boundary would classify v3 rows as legacy once v4 is active."""
    # Patched at the SOURCE, app.scoring.impact_runtime, so this would also
    # catch a regression that re-read the manifest through a function-local
    # import -- which is how the rest of the codebase imports it.
    from app.scoring import impact_runtime
    manifest = None if active is None else {"activation_impact_calculation_version": active}
    monkeypatch.setattr(impact_runtime, "active_manifest", lambda: manifest)
    monkeypatch.setattr(impact, "IMPACT_CALCULATION_VERSION", (active or 3) + 1)
    predicate, boundary = _legacy_only_filter(_HasScoringVersion())
    assert boundary == FIRST_NEW_STRUCTURE_VERSION == 3
    assert predicate == "scoring_version < :first_rc3"
