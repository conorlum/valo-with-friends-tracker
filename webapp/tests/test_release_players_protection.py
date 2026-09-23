"""Release write protection for `players` (plan 2026-09-21-impact-v4, sections
2.4 and 2.5, review finding R1).

Impact v4's remove_post_decided_assists maps assistants to players by
Player.display_name, so `players` is now scoring input. Before this, the swap
excluded it from its source digests and locks and the write gate did not
guard it, so a rename between export and swap passed every check. Each of the
three protections is pinned here, and so is the fingerprint contract the
export and the swap now exchange.
"""
import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.scoring.impact_manifest import SOURCE_FINGERPRINT_VERSION
from scripts import export_impact_artifact as export
from scripts import swap_impact_scores as swap_tool

GATE_SQL = Path(__file__).resolve().parents[1] / "scripts" / "sql" / "release_write_gate.sql"


# -- offline ----------------------------------------------------------------------------

def test_players_is_a_swap_source_table():
    # SOURCE_TABLES feeds the digests (build, verify, swap, rollback) and the
    # swap's and rollback's SHARE locks.
    assert "players" in swap_tool.SOURCE_TABLES


def test_the_write_gate_guards_players():
    sql = GATE_SQL.read_text(encoding="utf-8")
    (guarded,) = re.findall(r"FOREACH guarded IN ARRAY ARRAY\[(.*?)\]", sql, re.S)
    assert "'players'" in guarded


def test_the_export_contract_moved_with_the_fingerprint_contract():
    assert export.ARTIFACT_CONTRACT_VERSION == 2


@pytest.mark.parametrize("version", [None, 1, SOURCE_FINGERPRINT_VERSION + 1])
def test_verify_refuses_an_export_under_another_fingerprint_contract(version):
    inputs = {"match_source_fingerprints": {"1": "x"}, "cohort_fingerprint": "y"}
    if version is not None:
        inputs["fingerprint_version"] = version
    problems = []
    # No database: the contract is checked before a single row is read.
    swap_tool._check_inputs(None, {"inputs": inputs}, {}, problems)
    assert len(problems) == 1 and "fingerprint contract" in problems[0]


# -- against the scratch database ------------------------------------------------------

from tests.test_swap_impact_scores import (  # noqa: E402  (fixture and helpers)
    NAMES,
    _built_and_verified,
    _impacts,
    db,
)
from tests._postgres import postgres_session_or_skip  # noqa: E402


def _rename_a_scored_player(session, key):
    session.execute(text(
        "UPDATE players SET display_name = display_name || '-renamed' "
        "WHERE id = (SELECT player_id FROM match_players WHERE id = :m)"), {"m": key[1]})


def test_a_player_renamed_after_verification_stops_the_swap(db, tmp_path):
    keys, _ = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    _rename_a_scored_player(db, keys[0])
    db.commit()
    with pytest.raises(swap_tool.Refused, match="players changed after verification"):
        swap_tool.swap(db, NAMES)
    db.rollback()
    assert _impacts(db) == [10]


def test_a_player_renamed_during_the_hold_stops_the_rollback(db, tmp_path):
    keys, _ = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    swap_tool.swap(db, NAMES)
    db.commit()
    _rename_a_scored_player(db, keys[0])
    db.commit()
    with pytest.raises(swap_tool.Refused, match="players"):
        swap_tool.rollback(db, NAMES)
    db.rollback()


def test_the_swap_locks_players_against_writers(db, tmp_path):
    keys, _ = _built_and_verified(db, tmp_path, v1=10, rc3=77)
    writer = postgres_session_or_skip(writes=True)
    try:
        swap_tool.install_write_identity(writer, "rc3-runbook")
        writer.execute(text("UPDATE players SET display_name = display_name "
                            "WHERE id = (SELECT player_id FROM match_players WHERE id = :m)"),
                       {"m": keys[0][1]})               # uncommitted: holds ROW EXCLUSIVE
        with pytest.raises(OperationalError) as caught:
            swap_tool.swap(db, NAMES)
        assert getattr(caught.value.orig, "pgcode", None) == swap_tool.LOCK_NOT_AVAILABLE
        db.rollback()
    finally:
        writer.rollback()
        writer.close()
    assert _impacts(db) == [10]


def test_a_write_to_players_with_no_identity_is_refused(db):
    from sqlalchemy.exc import InternalError, ProgrammingError
    other = postgres_session_or_skip(writes=True)
    try:
        with pytest.raises((InternalError, ProgrammingError)) as caught:
            other.execute(text("INSERT INTO players (display_name) VALUES ('ungated#0000')"))
        assert "release write gate refused" in str(caught.value)
    finally:
        other.rollback()
        other.close()


def test_the_export_records_its_fingerprint_contract(db):
    inputs = export._inputs(db, [], fingerprints=True)
    assert inputs["fingerprint_version"] == SOURCE_FINGERPRINT_VERSION
    assert export._inputs(db, [], fingerprints=False)["fingerprint_version"] is None
